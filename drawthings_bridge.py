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
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

_FALLBACK_SITE = Path("/Users/mk/.gemini/antigravity/scratch/marketing_design_skill/venv/lib/python3.14/site-packages")
if _FALLBACK_SITE.is_dir() and str(_FALLBACK_SITE) not in sys.path:
    sys.path.append(str(_FALLBACK_SITE))

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

DRAWTHINGS_LORA_TRIGGERS: Dict[str, str] = {
    "minimax_h3_3step_lora_f16.ckpt": "h3_3steps",
    "minimax_h3_turbo_v4_lora_f16.ckpt": "h3tturbo",
    "h3_photoreal_lora_f16.ckpt": "h3image",
    "relim_v2_lora_500__1__lora_f16.ckpt": "h3relim",
    "qwen_image_2.16step_lora_f16.ckpt": "qwenturbo",
    "krea2_turbo_4step_lora_f16.ckpt": "krea2rurbo",
    "krea2turbo_radiance_lora_f16.ckpt": "kreaditals",
    "ideokrea_v1_lora_f16.ckpt": "ideokrea",
}


def align_dim_64(val: Any, default: int = 512) -> int:
    """Ensure dimension is a positive integer multiple of 64, minimum 256."""
    try:
        n = int(val or default)
        if n <= 0:
            n = default
        return max(256, int(round(n / 64.0)) * 64)
    except (TypeError, ValueError):
        return default


def list_drawthings_loras() -> List[Dict[str, Any]]:
    """Enumerate all LoRAs available in Draw Things models directory."""
    out: List[Dict[str, Any]] = []
    if not DRAWTHINGS_MODELS_DIR.is_dir():
        return out

    descriptions = {
        "minimax_h3_3step_lora_f16.ckpt": "MiniMax H3 3-step Turbo (lightning fast 3-step video)",
        "minimax_h3_turbo_v4_lora_f16.ckpt": "MiniMax H3 Turbo v4 (high quality 4-step video)",
        "h3_photoreal_lora_f16.ckpt": "MiniMax H3 Photorealism (natural skin tones & cinematic lighting)",
        "relim_v2_lora_500__1__lora_f16.ckpt": "MiniMax H3 Relim v2 (moody atmospheric lighting)",
        "qwen_image_2.16step_lora_f16.ckpt": "Qwen 2.1 Turbo 6-step (high-speed photorealism)",
        "krea2_turbo_4step_lora_f16.ckpt": "Krea 2 Turbo 4-step (ultra-fast diffusion)",
        "krea2turbo_radiance_lora_f16.ckpt": "Krea 2 Radiance (vivid lighting & high dynamic range)",
        "ideokrea_v1_lora_f16.ckpt": "IdeoKrea v1 (graphic design & typography on Krea)",
    }

    for f in sorted(DRAWTHINGS_MODELS_DIR.glob("*lora*.ckpt")):
        name = f.stem
        trigger = DRAWTHINGS_LORA_TRIGGERS.get(f.name, "")
        is_h3 = any(k in f.name.lower() for k in ("h3", "minimax", "relim"))
        lane = "h3" if is_h3 else "image"
        out.append({
            "id": f"dt:{f.name}",
            "name": name.replace("_lora_f16", "").replace("_", " ").title(),
            "description": descriptions.get(f.name, f"Draw Things LoRA (trigger: {trigger})"),
            "path": str(f),
            "filename": f.name,
            "lane": lane,
            "trigger": trigger,
            "compatible_modes": ["video:h3"] if is_h3 else ["image"],
            "size_mb": round(f.stat().st_size / (1024 * 1024), 1),
            "drawthings": True,
        })
    return out


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
    autoencoder: Optional[str] = None,
    text_encoder: Optional[str] = None,
    lora_file: Optional[str] = None,
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
    trigger = DRAWTHINGS_LORA_TRIGGERS.get(lora_file or "", "")
    if trigger and not prompt_text.startswith(trigger):
        prompt_text = f"{trigger} {prompt_text}"

    safe_w = align_dim_64(width, 512)
    safe_h = align_dim_64(height, 512)

    config = Configs.create()
    config["model"] = model
    if autoencoder:
        config["autoencoder"] = autoencoder
    if text_encoder:
        config["textEncoder"] = text_encoder

    config["steps"] = steps
    config["guidanceScale"] = guidance_scale
    config["width"] = safe_w
    config["height"] = safe_h
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
    autoencoder: Optional[str] = None,
    text_encoder: Optional[str] = None,
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
    trigger = DRAWTHINGS_LORA_TRIGGERS.get(lora_file or "", "")
    if trigger and not prompt_text.startswith(trigger):
        prompt_text = f"{trigger} {prompt_text}"

    safe_w = align_dim_64(width, 512)
    safe_h = align_dim_64(height, 512)

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
    config["width"] = safe_w
    config["height"] = safe_h
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

    # Save audio if present
    wav_path = None
    if hasattr(results, "audio") and results.audio is not None and output_mp4:
        try:
            wav_path = output_mp4.with_suffix(".wav")
            results.audio.to_file(str(wav_path))
            if on_log:
                on_log(f"Synchronized audio saved to {wav_path.name}")
        except Exception as e:
            if on_log:
                on_log(f"Audio save note: {e}")
            wav_path = None

    # Stitch into MP4 if output_mp4 specified
    final_video: Optional[Path] = None
    if output_mp4 and len(saved_frames) > 0:
        import imageio.v2 as iio
        output_mp4.parent.mkdir(parents=True, exist_ok=True)
        raw_video = output_mp4 if not wav_path else (output_mp4.parent / f"raw_{output_mp4.name}")
        writer = iio.get_writer(str(raw_video), fps=fps, codec="libx264")
        for f in saved_frames:
            writer.append_data(iio.imread(str(f)))
        writer.close()

        if wav_path and wav_path.is_file():
            try:
                mux_cmd = [
                    "ffmpeg", "-y",
                    "-i", str(raw_video),
                    "-i", str(wav_path),
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-shortest",
                    str(output_mp4),
                ]
                subprocess.run(mux_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                if raw_video != output_mp4 and raw_video.is_file():
                    raw_video.unlink()
            except Exception:
                if raw_video != output_mp4 and raw_video.is_file():
                    raw_video.replace(output_mp4)

        final_video = output_mp4

    return final_video, saved_frames
