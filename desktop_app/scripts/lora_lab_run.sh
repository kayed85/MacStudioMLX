#!/usr/bin/env bash
# Env shim for the panel-vendored lora_lab module. Sets the right
# PYTHONPATH + ffmpeg PATH + LTX-2.3 GPU watchdog overrides, then execs
# whatever command was passed.
#
# Was previously sourced from a separate lora-lab repo on Mr Bizarro's dev
# machine; vendored into the panel tree 2026-05-17 so an installer-only
# user gets training out of the box without cloning a second repo.
#
# Example:
#   ./scripts/lora_lab_run.sh python -m lora_lab.train_character \
#       --spec <spec.json> --job-id <id>

set -euo pipefail

# PANEL_ROOT = the phosphene-dev.git checkout this script lives in.
PANEL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LTX_ROOT="$PANEL_ROOT/ltx-2-mlx"
LTX_PY="$LTX_ROOT/env/bin/python"

if [[ ! -x "$LTX_PY" ]]; then
    echo "fatal: ltx-2-mlx env python not found at $LTX_PY" >&2
    exit 1
fi

# Resolve ffmpeg the same way the panel does — in order:
#   1. $PHOSPHENE_FFMPEG_DIR (explicit override, points at the directory
#      containing the ffmpeg binary)
#   2. $LTX_FFMPEG (single-binary path, exported by the panel after
#      its own resolver picks it)
#   3. Standard Pinokio install locations (the typical Mac install)
#   4. System PATH
# Previously hard-coded to /Users/.../pinokio/bin/ffmpeg-env/bin which
# only worked on the maintainer's machine — a public install would die
# on this check before any training ever ran.
FFMPEG_DIR=""
if [[ -n "${PHOSPHENE_FFMPEG_DIR:-}" && -x "${PHOSPHENE_FFMPEG_DIR}/ffmpeg" ]]; then
    FFMPEG_DIR="${PHOSPHENE_FFMPEG_DIR}"
elif [[ -n "${LTX_FFMPEG:-}" && -x "${LTX_FFMPEG}" ]]; then
    FFMPEG_DIR="$(dirname "${LTX_FFMPEG}")"
else
    for cand in \
        "${HOME}/pinokio/bin/ffmpeg-env/bin" \
        "${PANEL_ROOT}/../../bin/ffmpeg-env/bin" \
        "/Applications/Pinokio.app/Contents/Resources/bin/ffmpeg-env/bin"; do
        if [[ -x "${cand}/ffmpeg" ]]; then
            FFMPEG_DIR="${cand}"
            break
        fi
    done
    if [[ -z "${FFMPEG_DIR}" ]]; then
        sys_ffmpeg="$(command -v ffmpeg 2>/dev/null || true)"
        if [[ -n "${sys_ffmpeg}" ]]; then
            FFMPEG_DIR="$(dirname "${sys_ffmpeg}")"
        fi
    fi
fi
if [[ -z "${FFMPEG_DIR}" || ! -x "${FFMPEG_DIR}/ffmpeg" ]]; then
    echo "fatal: ffmpeg not found." >&2
    echo "  Tried: PHOSPHENE_FFMPEG_DIR, LTX_FFMPEG, ~/pinokio/bin/ffmpeg-env/bin," >&2
    echo "  Pinokio.app bundle, system PATH." >&2
    echo "  Install Pinokio (which bundles ffmpeg) or set" >&2
    echo "  PHOSPHENE_FFMPEG_DIR=/dir/containing/ffmpeg before running this." >&2
    exit 1
fi

export PATH="$FFMPEG_DIR:$PATH"
# Vendored lora_lab sits at $PANEL_ROOT/lora_lab/, importable as a
# top-level package when $PANEL_ROOT is on PYTHONPATH. ltx-trainer-mlx
# stays where it is inside the ltx-2-mlx package tree.
export PYTHONPATH="$PANEL_ROOT:$LTX_ROOT/packages/ltx-trainer/src:${PYTHONPATH:-}"

# Disable the lazy-graph eval splitters added in ltx-2-mlx 0897e7d.
# That commit forces mx.eval() every 8 DiT blocks (and every Gemma
# layer) to dodge the macOS GPU watchdog on M2 Max 64 GB. For inference
# on those machines it's a real fix.
#
# But for training on this M4 Max it's catastrophic: every forced
# materialization allocates+evicts Metal buffers mid-graph, the working
# set never settles, RSS stays tiny while the whole 11 GB transformer
# thrashes through mmap. Per-step time goes from 3 s to >10 min.
#
# The commit's own message documents the escape hatch:
# LTX2_GEMMA_EVAL_EVERY=0 and LTX2_DIT_EVAL_EVERY=0 recover full
# lazy-graph pipelining on Mac Studio / M-series Ultra. Default both to
# 0 for any lora_lab command; caller can still override (e.g. for a
# smaller-RAM render).
export LTX2_GEMMA_EVAL_EVERY="${LTX2_GEMMA_EVAL_EVERY:-0}"
export LTX2_DIT_EVAL_EVERY="${LTX2_DIT_EVAL_EVERY:-0}"

# If the first arg is "python" / "python3", swap it for the ltx env's
# python. Lets callers write `./scripts/lora_lab_run.sh python -m ...`
# without knowing the full env path.
if [[ "${1:-}" == "python" || "${1:-}" == "python3" ]]; then
    shift
    exec "$LTX_PY" "$@"
fi
exec "$@"
