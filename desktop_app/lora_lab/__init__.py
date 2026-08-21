"""lora_lab — Phosphene's character + voice LoRA training pipeline.

This package is vendored into Phosphene at
``phosphene-dev.git/lora_lab/`` and runs in the same MLX venv as the
panel helper (``ltx-2-mlx/env/``).

Public install discipline lives in this ``__init__.py`` so every
submodule (preprocess_images, preprocess_audio, train_character,
train_audio) can call ``resolve_default_model_dir()`` and get the right
LTX-2.3 Q4 base regardless of how the user installed Phosphene.
"""

from __future__ import annotations

import os
from pathlib import Path


def resolve_default_model_dir() -> str:
    """Locate the LTX-2.3 Q4 model directory on this machine.

    Resolution order — first match wins, no directory existence required
    at import time so this is safe to call before any model download:

      1. ``LTX_MODELS_DIR`` env var (the Phosphene panel sets this when
         it spawns ``lora_lab.train_character`` so the trainer picks up
         the same model root the panel just preflighted). Returns
         ``$LTX_MODELS_DIR/ltx-2.3-mlx-q4``.

      2. Script-relative walk — look for ``mlx_models/ltx-2.3-mlx-q4``
         in any ancestor of this file up to the nearest ``.git``. This
         is what makes the vendored install (where this file lives at
         ``phosphene-dev.git/lora_lab/__init__.py`` and the model dir
         is at ``phosphene-dev.git/mlx_models/ltx-2.3-mlx-q4``)
         resolve cleanly.

      3. ``~/.cache/huggingface/hub/models--dgrauet--ltx-2.3-mlx-q4/
         snapshots/<latest>`` — fallback for the authoring tree at
         ``~/AI/projects/lora-lab/`` and any historical installs that
         downloaded via the HF hub directly.

      4. Worst case (nothing exists yet, no env), return the expected
         vendored path so the eventual error message names a place the
         user can actually inspect.

    Designed to never raise on import. The real failure comes later
    when the trainer tries to ``mx.load`` from a non-existent path and
    gets a clear filesystem error.
    """
    # 1. Explicit env override (set by the Phosphene panel).
    env_dir = os.environ.get("LTX_MODELS_DIR")
    if env_dir:
        candidate = Path(env_dir).expanduser() / "ltx-2.3-mlx-q4"
        if candidate.is_dir():
            return str(candidate)
        # Even if it doesn't exist yet, prefer the env path — it's what
        # the panel expects and the user controls it.
        env_candidate = str(candidate)
    else:
        env_candidate = None

    # 2. Script-relative walk for the vendored install.
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "mlx_models" / "ltx-2.3-mlx-q4"
        if candidate.is_dir():
            return str(candidate)
        # Stop at the nearest repo root to avoid escaping into /Users/.
        if (parent / ".git").exists():
            break

    # 3. HF cache fallback (authoring tree / pre-Pinokio installs).
    legacy_root = (
        Path.home() / ".cache" / "huggingface" / "hub"
        / "models--dgrauet--ltx-2.3-mlx-q4" / "snapshots"
    )
    if legacy_root.is_dir():
        try:
            snapshots = [p for p in legacy_root.iterdir() if p.is_dir()]
            if snapshots:
                snapshots.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                return str(snapshots[0])
        except OSError:
            pass

    # 4. Final fallback — the env path (even if it doesn't exist) or
    # the vendored guess. Don't raise; let the trainer surface the
    # real error when it tries to load.
    if env_candidate:
        return env_candidate
    return str(here.parents[1] / "mlx_models" / "ltx-2.3-mlx-q4")


def resolve_default_text_encoder() -> str:
    """Locate Gemma 3 12B (LTX's text encoder) the same way.

    Same resolution rules as ``resolve_default_model_dir`` but for
    Gemma. Falls back to the HF repo id (``mlx-community/gemma-3-12b
    -it-4bit``) which mlx-vlm / ltx-trainer-mlx can fetch on first run.
    """
    env_dir = os.environ.get("LTX_MODELS_DIR")
    if env_dir:
        candidate = Path(env_dir).expanduser() / "gemma-3-12b-it-4bit"
        if candidate.is_dir():
            return str(candidate)
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "mlx_models" / "gemma-3-12b-it-4bit"
        if candidate.is_dir():
            return str(candidate)
        if (parent / ".git").exists():
            break
    return "mlx-community/gemma-3-12b-it-4bit"
