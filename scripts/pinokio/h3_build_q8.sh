#!/usr/bin/env bash
# Build the reduced-RAM Q8 DiT locally, on the 48 GB-class Macs that need it.
#
# Lifted out of install_h3.js (595-char dispatch). See
# scripts/pinokio/README.md.
#
#   cwd : minimax-h3-mlx
#   $1  : the app root. `{{cwd}}` is substituted by Pinokio in the MESSAGE, so
#         it cannot appear in this file — it arrives as an argument.
#
# SEMANTICS: unchanged — one shell, no `set -e`, exit code from the last
# command. Idempotent: the pack's own quant_config.json gates the re-run.
#
# WHY IT MATTERS: the Q8 pack halves the render peak (27.3 vs 42.8 GiB
# measured), which is what makes H3 possible on this class of machine at all —
# and the panel's `h3_capable()` returns False on a sub-60 GB Mac until this
# pack EXISTS ON DISK, so a 48 GB Mac with no pack gets no Engine switcher.
# quantize_stream.py never holds more than one tensor (CPU stream,
# deterministic), so the build runs fine on the same 48 GB machine that could
# never load the model whole. ~22 GB on disk, ~5 min.
#
# Since v4.8.0 the Q8 engine is the panel's AUTO DEFAULT on every machine,
# so every machine builds the pack — see the gate comment below.

APP_ROOT="$1"
if [ -z "$APP_ROOT" ]; then
  echo 'h3_build_q8.sh: no app root passed - skipping the Q8 build'
  exit 2
fi

PACK="$APP_ROOT/mlx_models/hailuo-h3/models/h3-dit-q8"
SRC="$APP_ROOT/mlx_models/hailuo-h3/models/deepbeep-pruned-bf16/MiniMax-H3-FL2VA-pruned_bf16.safetensors"
# EVERY machine builds the pack now. Until v4.8.0 this step was skipped on
# 64 GB+ Macs ("bf16 is the quality default there") — and then v4.8.0 made Q8
# the AUTO DEFAULT everywhere without un-gating this build, so on a big Mac
# the new default silently fell back to bf16 for want of a pack that Install
# refused to build. Field report, day one: "i updated but still getting this
# much memory" — python3.11 at 47.76 GB, which is the bf16 engine plus cache.
# The default's weights must be installable wherever the default applies;
# 22 GB of disk and ~5 min is the price of the halving the release notes
# promised. Idempotent via the pack's own quant_config.json, as before.
if [ -f "$PACK/quant_config.json" ]; then
  echo 'Q8 engine already built - skipping'
else
  echo '=== Building the reduced-RAM Q8 engine (~5 min, one time) ==='
  .venv/bin/python scripts/quantize_stream.py --src "$SRC" --out "$PACK"
fi
