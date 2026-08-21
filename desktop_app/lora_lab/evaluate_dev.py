"""Generate LoRA evaluation samples via the DEV one-stage pipeline (CFG + STG).

Matches the inference path the iter-4 LoRA was trained against (dev
transformer + full-range flow matching), not the 8-step distilled path. Per
LTX-2 Issue #175, a LoRA trained against dev MUST be inferred against dev for
the objective schedule to match — otherwise the trained weight deltas point
at the wrong target and identity won't realize.

Args mirror evaluate.py + adds CFG / STG / num_steps knobs for the dev path.

Usage::

    ./scripts/run.sh python -m lora_lab.evaluate_dev \\
        --lora outputs/char_image_lora_r32_dev/checkpoints/lora_weights_step_03000.safetensors \\
        --strength 1.0 --tag dev_r32_step3000_str10_n9 \\
        --width 576 --height 576 --frames 9 --num-steps 30 --cfg-scale 3.0 \\
        --limit 5
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
import time
from pathlib import Path

import mlx.core as mx
import mlx.utils
from ltx_pipelines_mlx import TI2VidOneStagePipeline
from ltx_core_mlx.loader.fuse_loras import apply_loras
from ltx_core_mlx.loader.primitives import LoraStateDictWithStrength, StateDict
from ltx_core_mlx.loader.sd_ops import LTXV_LORA_COMFY_RENAMING_MAP
from ltx_core_mlx.loader.sft_loader import SafetensorsStateDictLoader
from ltx_core_mlx.utils.memory import aggressive_cleanup

from lora_lab import resolve_default_model_dir, resolve_default_text_encoder

logger = logging.getLogger(__name__)


# Same resolver story as evaluate.py — env (LTX_MODELS_DIR) → vendored
# mlx_models/ → HF cache fallback. The previous hardcoded dgrauet snapshot
# path silently broke fresh Pinokio installs whose model dir lives at
# <panel-root>/mlx_models/ltx-2.3-mlx-q4 (not in ~/.cache/huggingface).
DEFAULT_MODEL_DIR = resolve_default_model_dir()

# `{trigger}` is substituted in main() so this file is reusable for any
# character LoRA. Pass `--trigger mychar` (or whatever token the LoRA was
# trained on); default is `mychar`.
DEFAULT_PROMPTS = [
    "{trigger} man, close-up portrait, facing camera, neutral expression, soft daylight",
    "{trigger} man, medium shot, three-quarter angle, slight smile, warm indoor lighting",
    "{trigger} man, half body shot, looking up, outdoor sunny afternoon",
    "{trigger} man, close-up portrait, looking away, profile view, evening light",
    "{trigger} man, medium shot, big grin, outdoor tropical balcony, daytime",
]


def _slugify(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:max_len]


def _load_lora_pairs(lora_path: Path, strength: float) -> list:
    return [(str(lora_path), float(strength))]


def _fuse_custom_lora_into_pipe_dit(pipe, lora_path: Path, strength: float) -> None:
    """Fuse a custom LoRA into the dev pipeline's already-loaded transformer.

    Workaround for an MLX-port bug: `TwoStagePipeline.load()` (and its subclass
    `TI2VidOneStagePipeline.load()`) override `BasePipeline.load()` but never
    call `_fuse_pending_loras`. So setting `pipe._pending_loras = [...]` on a
    dev pipeline is silently ignored — the dit loads with no LoRA fused.

    This helper replicates the in-place fusion pattern that
    `TwoStagePipeline._fuse_distilled_lora` uses for the distillation LoRA,
    but for an arbitrary user-provided LoRA file.
    """
    assert pipe.dit is not None, "pipe.dit must be loaded before fusing LoRA"

    flat_params = mlx.utils.tree_flatten(pipe.dit.parameters())
    flat_model = {k: v for k, v in flat_params if isinstance(v, mx.array)}
    model_sd = StateDict(sd=flat_model, size=0, dtype=set())

    loader = SafetensorsStateDictLoader()
    lora_sd = loader.load(str(lora_path), sd_ops=LTXV_LORA_COMFY_RENAMING_MAP)
    lora_with_strength = LoraStateDictWithStrength(state_dict=lora_sd, strength=float(strength))

    fused = apply_loras(model_sd, [lora_with_strength])
    pipe.dit.load_weights(list(fused.sd.items()))
    aggressive_cleanup()
    print(f"  manually fused LoRA into dit ({Path(lora_path).name} @ strength={strength})")


def _extract_first_frame_png(mp4_path: Path, png_path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4_path),
         "-frames:v", "1", "-update", "1", str(png_path)],
        check=True,
    )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--lora", default="")
    p.add_argument("--strength", type=float, default=1.0)
    p.add_argument("--tag", required=True)
    p.add_argument("--width", type=int, default=576)
    p.add_argument("--height", type=int, default=576)
    p.add_argument("--frames", type=int, default=9)
    p.add_argument("--num-steps", type=int, default=30)
    p.add_argument("--cfg-scale", type=float, default=3.0)
    p.add_argument("--stg-scale", type=float, default=0.0)
    p.add_argument("--seed-start", type=int, default=1000)
    p.add_argument("--prompts-file", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--trigger", default="mychar",
                   help="trigger token substituted into `{trigger}` placeholders in "
                        "DEFAULT_PROMPTS. Ignored when --prompts-file is given.")
    p.add_argument("--model-dir", default=str(DEFAULT_MODEL_DIR))
    p.add_argument("--low-memory", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    if args.prompts_file:
        prompts = [line.strip() for line in Path(args.prompts_file).read_text().splitlines() if line.strip()]
    else:
        trigger = (args.trigger or "mychar").strip() or "mychar"
        prompts = [p.format(trigger=trigger) for p in DEFAULT_PROMPTS]
    if args.limit:
        prompts = prompts[: args.limit]
    print(f"running {len(prompts)} prompts via DEV pipeline | {args.width}x{args.height} {args.frames}f "
          f"{args.num_steps}step CFG={args.cfg_scale} STG={args.stg_scale}")

    out_root = Path("outputs/eval") / args.tag
    out_root.mkdir(parents=True, exist_ok=True)

    pipe = TI2VidOneStagePipeline(
        model_dir=args.model_dir,
        gemma_model_id=resolve_default_text_encoder(),
        low_memory=args.low_memory,
        low_ram_streaming=False,
    )

    lora_path: Path | None = None
    if args.lora:
        lora_path = Path(args.lora).resolve()
        if not lora_path.exists():
            raise FileNotFoundError(f"LoRA file missing: {lora_path}")
        print(f"LoRA: {lora_path.name}  strength={args.strength}")
        print("note: dev pipeline doesn't fuse _pending_loras; using manual post-load fusion")
    else:
        print("baseline run — NO LoRA")

    manifest = {
        "tag": args.tag,
        "pipeline": "TI2VidOneStagePipeline (dev transformer + CFG)",
        "lora": args.lora or None,
        "strength": args.strength if args.lora else None,
        "width": args.width,
        "height": args.height,
        "frames": args.frames,
        "num_steps": args.num_steps,
        "cfg_scale": args.cfg_scale,
        "stg_scale": args.stg_scale,
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
            if lora_path is not None:
                # Force a fresh dit load so we can manually fuse the LoRA before generate.
                pipe.load()
                _fuse_custom_lora_into_pipe_dit(pipe, lora_path, args.strength)
            pipe.generate_and_save(
                prompt=prompt,
                output_path=str(mp4),
                height=args.height,
                width=args.width,
                num_frames=args.frames,
                seed=seed,
                num_steps=args.num_steps,
                cfg_scale=args.cfg_scale,
                stg_scale=args.stg_scale,
            )
            wall = time.time() - t0
            if mp4.exists():
                try:
                    _extract_first_frame_png(mp4, png)
                except Exception as exc:
                    logger.warning("png extract failed: %s", exc)
            manifest["renders"].append({
                "idx": i, "prompt": prompt, "seed": seed,
                "mp4": mp4.name, "png": png.name if png.exists() else None,
                "wall_s": round(wall, 1),
            })
            print(f"  wrote {mp4.name} ({wall:.1f}s)")
        except Exception as exc:
            wall = time.time() - t0
            logger.exception("render %d failed", i)
            manifest["renders"].append({
                "idx": i, "prompt": prompt, "seed": seed,
                "error": str(exc), "wall_s": round(wall, 1),
            })

        (out_root / "manifest.json").write_text(json.dumps(manifest, indent=2))

    print(f"\ndone — manifest at {out_root / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
