#!/usr/bin/env bash
# Music engine (YuE2) install: its licence must be accepted first (4.19.8).
#
#   bash scripts/pinokio/music_licence_gate.sh        (from the app root)
#
# YuE2's weights are CC BY-NC 4.0 plus an individual-creator permission
# (LICENSES/YuE2-MODEL_LICENSE.txt). The panel shows that licence and stores
# the acceptance (model_licences.py writes state/licence-yue2-<version>.accepted);
# the in-panel installer asks first (HTTP 451 -> the licence screen), and this
# step does the same for the Pinokio sidebar's "Install the music engine".
# An install that already has the YuE2 weights continues (a Repair); the panel
# asks before the next song instead. The version MUST equal
# model_licences.LICENCES["yue2"]["version"] (test_model_licences.py).
APP_ROOT="$(pwd)"
YUE2_LICENCE_VERSION="2026-09-16"
STATE="${LTX_STATE_DIR:-$APP_ROOT/state}"
case "$STATE" in /*) ;; *) STATE="$APP_ROOT/$STATE" ;; esac
MODELS="${LTX_MUSIC_MODELS:-$APP_ROOT/mlx_models/yue2}"
case "$MODELS" in /*) ;; *) MODELS="$APP_ROOT/$MODELS" ;; esac
if [ -f "$STATE/licence-yue2-$YUE2_LICENCE_VERSION.accepted" ]; then
  echo "YuE2 licence: accepted in Phosphene."
  exit 0
fi
if [ -n "$(find "$MODELS" -name '*.safetensors' -size +1M 2>/dev/null | head -1)" ]; then
  echo "YuE2 licence: not accepted in Phosphene yet. The music engine is already on this Mac, so this continues; Phosphene asks you to accept the licence before your next song."
  exit 0
fi
echo "FATAL error: Open Phosphene, go to Audio - Compose, click Install music engine and tick I accept on the YuE2 licence (non-commercial use only). That installs it; or run this again after accepting."
exit 1
