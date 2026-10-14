"""Draw Things Bridge for MacStudioMLX.

Connects the local models and acceleration engine from Draw Things into MacStudioMLX:
- Reuses models in ~/Library/Containers/com.liuliu.draw-things/Data/Documents/Models directly
- Uses gRPC service (via Draw Things app on port 7859 or headless gRPCServerCLI-macOS)
- Progressive preview writer to `live_preview_dir`:
    - preview_latest.png
    - status.json (schema: "h3-live-preview/1")
- Supports Video Generation: MiniMax H3 (T2V & Ref2V) with 3/4-step Turbo
- Supports Image Generation: Qwen Image 2.1 (6-step Turbo), Krea 2 Turbo (4-step), FLUX.2 Klein 4B
- Output assembly with ffmpeg/imageio and MacStudioMLX sidecar compatibility.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

# Draw Things constants
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7859
DRAWTHINGS_MODELS_DIR = Path(
    os.path.expanduser("~/Library/Containers/com.liuliu.draw-things/Data/Documents/Models")
)
CLI_SERVER_PATH = Path("/Users/mk/MacStudioMLX/bin/gRPCServerCLI-macOS")

_SERVER_PROC: Optional[subprocess.Popen] = None
_ACTIVE_MODEL_VERSION: str = "qwen"


def is_server_running(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> bool:
    """Check if Draw Things gRPC service is listening on port."""
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except (OSError, ConnectionRefusedError):
        return False


def ensure_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    timeout_sec: float = 15.0,
    models_dir: Optional[Path] = None,
) -> bool:
    """Ensure Draw Things gRPC server is running. Spawns headless CLI if needed."""
    global _SERVER_PROC
    if is_server_running(host, port):
        return True

    m_dir = models_dir or DRAWTHINGS_MODELS_DIR
    if not m_dir.is_dir():
        return False

    if not CLI_SERVER_PATH.is_file():
        return False

    cmd = [
        str(CLI_SERVER_PATH),
        str(m_dir),
        "--port",
        str(port),
        "--no-tls",
    ]

    try:
        _SERVER_PROC = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        return False

    t0 = time.time()
    while time.time() - t0 < timeout_sec:
        if is_server_running(host, port):
            return True
        time.sleep(0.5)

    return False


def stop_server() -> None:
    """Stop headless server if spawned by us."""
    global _SERVER_PROC
    if _SERVER_PROC is not None:
        try:
            _SERVER_PROC.terminate()
            _SERVER_PROC.wait(timeout=3)
        except Exception:
            try:
                _SERVER_PROC.kill()
            except Exception:
                pass
        _SERVER_PROC = None


# ---- Patch Decoders for Preview -----------------------------------------------

def _install_preview_patch():
    """Install custom patch into drawthings_py so preview decoding works for Qwen and H3."""
    try:
        import drawthings_py.image._preview_decoders as dec
        import drawthings_py.grpc.grpc_service as gs
        from drawthings_py.image.image_buffer import ImageBuffer

        orig_decode = dec.decode_preview
        orig_get_data = dec.get_image_data

        def patched_decode(preview: bytes, version: Optional[str] = None) -> Optional[ImageBuffer]:
            try:
                width, height, channels, floats = orig_get_data(preview)
                eff_ver = (version or _ACTIVE_MODEL_VERSION or "").lower()

                # Handle 64 channels patchified latents (Qwen 2.1)
                if channels == 64:
                    # Unpatchify (H, W, 2, 2, 16) -> (H*2, W*2, 16)
                    try:
                        H, W = height, width
                        unpatch = (
                            floats.reshape(H, W, 2, 2, 16)
                            .transpose(0, 2, 1, 3, 4)
                            .reshape(H * 2, W * 2, 16)
                        )
                        return dec.decode_wan2_1(W * 2, H * 2, unpatch)
                    except Exception:
                        pass

                # Handle 24 channels (MiniMax H3 video + audio latents)
                if channels == 24:
                    try:
                        v = floats.reshape(height, width, 24)[:, :, :16].reshape(-1, 16)
                        return dec.decode_wan2_1(width, height, v)
                    except Exception:
                        pass

                if eff_ver.startswith("qwen") or eff_ver.startswith("wan"):
                    return dec.decode_wan2_1(width, height, floats)

                return orig_decode(preview, version=eff_ver)
            except Exception:
                try:
                    return orig_decode(preview, version=None)
                except Exception:
                    return None

        dec.decode_preview = patched_decode
        gs.decode_preview = patched_decode
    except Exception:
        pass


_install_preview_patch()


# ---- Generation Bridge Functions ----------------------------------------------

async def generate_dt_image(
    prompt: str,
    negative_prompt: str = "",
    model: str = "qwen_image_2.1_i8x.ckpt",
    autoencoder: Optional[str] = "qwen_image_2.1_vae_f16.ckpt",
    text_encoder: Optional[str] = "qwen_3_vl_8b_instruct_i8x.ckpt",
    lora_file: Optional[str] = "qwen_image_2.16step_lora_f16.ckpt",
    lora_weight: float = 1.0,
    steps: int = 6,
    guidance_scale: float = 1.0,
    width: int = 512,
    height: int = 512,
    seed: int = -1,
    init_image: Optional[str] = None,
    strength: float = 0.85,
    live_preview_dir: Optional[Path] = None,
    on_log: Optional[Callable[[str], None]] = None,
) -> List[Any]:
    """Generate image via Draw Things gRPC service with progressive preview."""
    global _ACTIVE_MODEL_VERSION
    _ACTIVE_MODEL_VERSION = "qwen" if "qwen" in model.lower() else "flux"

    if not ensure_server():
        raise RuntimeError("Draw Things gRPC server is not running and could not be started on 127.0.0.1:7859")

    from drawthings_py import DrawThings, Configs, RequestBuilder

    # Auto prefix trigger
    prompt_text = prompt.strip()
    if lora_file and "qwen" in lora_file.lower() and not prompt_text.startswith("qwenturbo"):
        prompt_text = f"qwenturbo {prompt_text}"
    elif lora_file and "krea" in lora_file.lower() and not prompt_text.startswith("krea2rurbo"):
        prompt_text = f"krea2rurbo {prompt_text}"

    config = Configs.create()
    config["model"] = model
    if autoencoder:
        config["autoencoder"] = autoencoder
    if text_encoder:
        config["textEncoder"] = text_encoder

    config["steps"] = steps
    config["guidanceScale"] = guidance_scale
    config["width"] = width
    config["height"] = height
    config["numFrames"] = 1
    if seed >= 0:
        config["seed"] = seed

    if lora_file and lora_file != "none":
        config["loras"] = [{
            "file": lora_file,
            "weight": lora_weight,
            "mode": "all",
        }]

    builder = RequestBuilder(config, prompt_text, negative_prompt)

    if init_image and os.path.isfile(init_image):
        builder.init_image(init_image)
        config["strength"] = strength

    t_start = time.time()
    total_steps = steps

    def progress_callback(signpost: Any, preview_buf: Any):
        step = None
        if hasattr(signpost, "sampling") and hasattr(signpost.sampling, "step"):
            step = signpost.sampling.step

        if on_log and step is not None:
            on_log(f"Sampling step {step}/{total_steps}")

        if live_preview_dir:
            live_preview_dir.mkdir(parents=True, exist_ok=True)
            cur_step = step if step is not None else 1
            elapsed = time.time() - t_start
            sec_per_step = elapsed / max(1, cur_step)
            eta = max(0.0, (total_steps - cur_step) * sec_per_step)

            if preview_buf is not None:
                try:
                    preview_buf.to_file(str(live_preview_dir / "preview_latest.png"))
                except Exception:
                    pass

            status_payload = {
                "schema": "h3-live-preview/1",
                "forward": cur_step,
                "total_forwards": total_steps,
                "status": "running",
                "eta_seconds": round(eta, 1),
            }
            try:
                (live_preview_dir / "status.json").write_text(json.dumps(status_payload))
            except Exception:
                pass

    builder.on_progress(progress_callback)

    async with DrawThings.grpc(host=DEFAULT_HOST, port=DEFAULT_PORT) as service:
        service._disable_messages = True
        results = await service.generate(builder)
        return results


async def generate_dt_video(
    prompt: str,
    negative_prompt: str = "",
    model: str = "minimax_h3_fl2va_q8p.ckpt",
    autoencoder: Optional[str] = "minimax_h3_vae_f16.ckpt",
    text_encoder: Optional[str] = "qwen_3_vl_32b_50_i8x.ckpt",
    lora_file: Optional[str] = "minimax_h3_3step_lora_f16.ckpt",
    lora_weight: float = 1.0,
    steps: int = 4,
    sampler: int = 16,
    shift: float = 12.0,
    guidance_scale: float = 1.0,
    width: int = 512,
    height: int = 512,
    num_frames: int = 17,
    fps: int = 12,
    seed: int = -1,
    init_image: Optional[str] = None,
    last_frame_image: Optional[str] = None,
    strength: float = 0.85,
    output_mp4: Optional[Path] = None,
    live_preview_dir: Optional[Path] = None,
    on_log: Optional[Callable[[str], None]] = None,
) -> Tuple[Optional[Path], List[Path]]:
    """Generate video via Draw Things gRPC service with live progressive preview and MP4 assembly."""
    global _ACTIVE_MODEL_VERSION
    _ACTIVE_MODEL_VERSION = "minimax_h3"

    if not ensure_server():
        raise RuntimeError("Draw Things gRPC server is not running and could not be started on 127.0.0.1:7859")

    from drawthings_py import DrawThings, Configs, RequestBuilder

    # Auto prefix trigger
    prompt_text = prompt.strip()
    if lora_file and "3step" in lora_file.lower():
        if not prompt_text.startswith("h3_3steps"):
            prompt_text = f"h3_3steps {prompt_text}"
    elif lora_file and "turbo" in lora_file.lower():
        if not prompt_text.startswith("h3tturbo"):
            prompt_text = f"h3tturbo {prompt_text}"

    config = Configs.create()
    config["model"] = model
    if autoencoder:
        config["autoencoder"] = autoencoder
    if text_encoder:
        config["textEncoder"] = text_encoder

    config["steps"] = steps
    config["sampler"] = sampler
    config["shift"] = shift
    config["guidanceScale"] = guidance_scale
    config["width"] = width
    config["height"] = height
    config["numFrames"] = num_frames
    if seed >= 0:
        config["seed"] = seed

    if lora_file and lora_file != "none":
        config["loras"] = [{
            "file": lora_file,
            "weight": lora_weight,
            "mode": "all",
        }]

    builder = RequestBuilder(config, prompt_text, negative_prompt)

    if init_image and os.path.isfile(init_image):
        builder.init_image(init_image)
        config["strength"] = strength

    if last_frame_image and os.path.isfile(last_frame_image):
        builder.add_moodboard_image(last_frame_image)

    t_start = time.time()
    total_steps = steps

    def progress_callback(signpost: Any, preview_buf: Any):
        step = None
        if hasattr(signpost, "sampling") and hasattr(signpost.sampling, "step"):
            step = signpost.sampling.step

        if on_log and step is not None:
            on_log(f"H3 Denoise step {step}/{total_steps}")

        if live_preview_dir:
            live_preview_dir.mkdir(parents=True, exist_ok=True)
            cur_step = step if step is not None else 1
            elapsed = time.time() - t_start
            sec_per_step = elapsed / max(1, cur_step)
            eta = max(0.0, (total_steps - cur_step) * sec_per_step)

            if preview_buf is not None:
                try:
                    preview_buf.to_file(str(live_preview_dir / "preview_latest.png"))
                except Exception:
                    pass

            status_payload = {
                "schema": "h3-live-preview/1",
                "forward": cur_step,
                "total_forwards": total_steps,
                "window": 1,
                "total_windows": 1,
                "status": "running",
                "eta_seconds": round(eta, 1),
            }
            try:
                (live_preview_dir / "status.json").write_text(json.dumps(status_payload))
            except Exception:
                pass

    builder.on_progress(progress_callback)

    if on_log:
        on_log(f"Submitting H3 job ({num_frames} frames, {width}x{height}, {steps} steps) to Draw Things...")

    async with DrawThings.grpc(host=DEFAULT_HOST, port=DEFAULT_PORT) as service:
        service._disable_messages = True
        results = await service.generate(builder)

    if not results:
        raise RuntimeError("No frames generated by Draw Things.")

    saved_frames: List[Path] = []
    tmp_dir = (output_mp4.parent if output_mp4 else Path("/tmp")) / f"dt_frames_{int(time.time()*1000)}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    for idx, item in enumerate(results):
        frame_path = tmp_dir / f"frame_{idx:04d}.png"
        item.to_file(str(frame_path))
        saved_frames.append(frame_path)

    # Stitch into MP4 if output_mp4 specified
    final_video: Optional[Path] = None
    if output_mp4 and len(saved_frames) > 0:
        import imageio.v2 as iio
        output_mp4.parent.mkdir(parents=True, exist_ok=True)
        writer = iio.get_writer(str(output_mp4), fps=fps, codec="libx264")
        for f in saved_frames:
            writer.append_data(iio.imread(str(f)))
        writer.close()
        final_video = output_mp4

    return final_video, saved_frames
