"""Generate LoRA evaluation samples (text-to-frame) for the trained character.

Loads `BasePipeline` once, fuses a LoRA checkpoint via `_pending_loras`, then
runs each prompt at `num_frames=1` (single-frame T2V — the LoRA was trained on
1-frame samples, so this matches the training distribution exactly).

Outputs to `outputs/eval/<run_tag>/`:
    NN_<prompt-slug>_seedSSS_strSS.png         # generated frame (extracted from mp4)
    NN_<prompt-slug>_seedSSS_strSS.mp4         # the actual pipeline output
    manifest.json                              # prompt/seed/strength/timing per render

Usage:
    ./scripts/run.sh python -m lora_lab.evaluate \
        --lora outputs/char_image_lora/checkpoints/lora_weights_step_01500.safetensors \
        --strength 1.0 \
        --tag step1500_str10 \
        --width 512 --height 512 \
        --frames 1 --num-steps 8

Pass `--lora ""` for a baseline (no LoRA fused). Same prompts + seeds, so
outputs are directly comparable to LoRA runs.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import mlx.core as mx
from ltx_pipelines_mlx import BasePipeline

from lora_lab import resolve_default_model_dir, resolve_default_text_encoder

logger = logging.getLogger(__name__)


# Resolved at import time via the same logic the trainer uses (env override
# LTX_MODELS_DIR → vendored mlx_models/ → HF cache fallback). Hardcoding the
# old dgrauet HF snapshot path here used to break the public Pinokio install,
# whose model dir is the vendored mlx_models/ltx-2.3-mlx-q4 — completely
# unrelated to ~/.cache/huggingface.
DEFAULT_MODEL_DIR = resolve_default_model_dir()

# Prompts use `{trigger}` as a placeholder for the LoRA trigger token. The
# CLI substitutes it via str.format at runtime — pass `--trigger mychar` (or
# whatever token the LoRA was trained on) to fill it in. Default is `mychar`
# so a fresh user can still smoke-test the eval pipeline without editing
# this file.
DEFAULT_PROMPTS = [
    "{trigger}, close-up portrait, facing camera, neutral expression, soft daylight, blurred indoor background",
    "{trigger}, medium shot, three-quarter angle, slight smile, warm indoor lighting, dark t-shirt",
    "{trigger}, half-body, looking up, outdoor sunny afternoon, light shirt, blue sky",
    "{trigger}, close-up portrait, looking away, evening light, profile view",
    "{trigger}, medium shot, big grin, outdoor tropical balcony, daytime, dark t-shirt, palm trees background",
    "{trigger}, close-up selfie, facing camera, deadpan expression, gray t-shirt, indoor home gym",
    "{trigger}, half-body shot, facing camera, hands in pockets, autumn forest, soft overcast light",
    "{trigger}, close-up portrait, three-quarter angle, contemplative expression, cafe interior, golden hour",
    "{trigger}, full-body shot, facing camera, walking, urban street, daytime",
    "{trigger}, close-up portrait, looking down, dramatic backlight, dark background",
    "{trigger}, medium shot, slight smile, library shelves behind, warm indoor light",
    "{trigger}, close-up selfie, big grin, beach background, bright outdoor light",
    "{trigger}, half-body, facing camera, sitting at desk with computer, screen lighting, indoor",
    "{trigger}, three-quarter angle, looking out window, overcast daylight, indoor",
    "{trigger}, close-up portrait, facing camera, serious expression, neutral background, studio lighting",
]


def _slugify(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:max_len]


def _load_lora_pairs(lora_path: Path, strength: float) -> list:
    """Build the list expected by `pipeline._pending_loras`.

    The upstream hook is:
        pipe._pending_loras = [(state_dict_loader_result, strength), ...]
    where each "state_dict_loader_result" is whatever `SafetensorsStateDictLoader`
    yields. The simplest way is to pass a `(path_str, strength)` and let
    `_fuse_pending_loras` handle loading — but the exact shape depends on the
    pipeline's `_fuse_pending_loras`. We use the same pattern Phosphene uses
    in mlx_warm_helper:_attach_loras: pass `(str(local_path), float)` pairs.
    """
    return [(str(lora_path), float(strength))]


def _extract_first_frame_png(mp4_path: Path, png_path: Path) -> None:
    """Pull frame 0 from an mp4 into a png using ffmpeg (already on PATH via run.sh)."""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4_path),
         "-frames:v", "1", "-update", "1", str(png_path)],
        check=True,
    )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--lora", default="", help="path to LoRA .safetensors; empty string = baseline (no LoRA)")
    p.add_argument("--strength", type=float, default=1.0)
    p.add_argument("--tag", required=True, help="subfolder under outputs/eval/")
    p.add_argument("--width", type=int, default=512)
    p.add_argument("--height", type=int, default=512)
    p.add_argument("--frames", type=int, default=1, help="num_frames (1 = single image, 25 = ~1s clip)")
    p.add_argument("--num-steps", type=int, default=8)
    p.add_argument("--seed-start", type=int, default=1000)
    p.add_argument("--prompts-file", default=None, help="optional .txt file with one prompt per line; else use DEFAULT_PROMPTS")
    p.add_argument("--limit", type=int, default=None, help="only run the first N prompts")
    p.add_argument("--trigger", default="mychar",
                   help="trigger token substituted for `{trigger}` in DEFAULT_PROMPTS "
                        "(must match what the LoRA was trained on). Ignored when --prompts-file is given.")
    p.add_argument("--model-dir", default=str(DEFAULT_MODEL_DIR))
    p.add_argument("--low-memory", action="store_true",
                   help="reload transformer between renders (cuts peak RAM, slower); default keeps loaded across renders")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    if args.prompts_file:
        prompts = [line.strip() for line in Path(args.prompts_file).read_text().splitlines() if line.strip()]
    else:
        # Substitute the trigger token into the {trigger}-placeholder defaults.
        # Falls back to a literal `mychar` if the user forgot to pass --trigger.
        trigger = (args.trigger or "mychar").strip() or "mychar"
        prompts = [p.format(trigger=trigger) for p in DEFAULT_PROMPTS]
    if args.limit:
        prompts = prompts[: args.limit]
    print(f"running {len(prompts)} prompts | {args.width}x{args.height} {args.frames}f {args.num_steps}step")

    out_root = Path("outputs/eval") / args.tag
    out_root.mkdir(parents=True, exist_ok=True)

    pipe = BasePipeline(
        model_dir=args.model_dir,
        gemma_model_id=resolve_default_text_encoder(),
        low_memory=args.low_memory,
        low_ram_streaming=False,
    )

    if args.lora:
        lora_path = Path(args.lora).resolve()
        if not lora_path.exists():
            raise FileNotFoundError(f"LoRA file missing: {lora_path}")
        pipe._pending_loras = _load_lora_pairs(lora_path, args.strength)
        print(f"LoRA: {lora_path.name}  strength={args.strength}")
    else:
        print("baseline run — NO LoRA")

    manifest = {
        "tag": args.tag,
        "lora": args.lora or None,
        "strength": args.strength if args.lora else None,
        "width": args.width,
        "height": args.height,
        "frames": args.frames,
        "num_steps": args.num_steps,
        "model_dir": args.model_dir,
        "renders": [],
    }

    for i, prompt in enumerate(prompts):
        seed = args.seed_start + i
        slug = _slugify(prompt, 40)
        name = f"{i:02d}_{slug}_seed{seed}_str{int(args.strength * 10):02d}"
        mp4 = out_root / f"{name}.mp4"
        png = out_root / f"{name}.png"
        print(f"\n[{i + 1}/{len(prompts)}] seed={seed} | {prompt[:70]}")
        t0 = time.time()
        try:
            pipe.generate_and_save(
                prompt=prompt,
                output_path=str(mp4),
                height=args.height,
                width=args.width,
                num_frames=args.frames,
                seed=seed,
                num_steps=args.num_steps,
            )
            wall = time.time() - t0
            if args.frames == 1 and mp4.exists():
                try:
                    _extract_first_frame_png(mp4, png)
                except Exception as exc:
                    logger.warning("png extract failed: %s", exc)
            manifest["renders"].append(
                {"idx": i, "prompt": prompt, "seed": seed, "mp4": mp4.name, "png": png.name if png.exists() else None, "wall_s": round(wall, 1)}
            )
            print(f"  wrote {mp4.name} ({wall:.1f}s)")
        except Exception as exc:
            wall = time.time() - t0
            logger.exception("render %d failed", i)
            manifest["renders"].append({"idx": i, "prompt": prompt, "seed": seed, "error": str(exc), "wall_s": round(wall, 1)})

        (out_root / "manifest.json").write_text(json.dumps(manifest, indent=2))

    print(f"\ndone — manifest at {out_root / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
