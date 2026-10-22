#!/usr/bin/env bash
# Hailuo H3 install: the MiniMax H3 licence must be accepted first (4.19.8).
#
#   bash scripts/pinokio/h3_licence_gate.sh        (from the app root)
#
# WHY. The MiniMax H3 Community License binds whoever downloads or runs H3, it
# covers only its Applicable Territory (worldwide except the EU, the UK, South
# Korea and the US), and it asks a product to bind and notify each user before
# giving access (s. V.2). The panel shows the licence screen and stores the
# acceptance (model_licences.py writes state/licence-h3-<version>.accepted);
# this step refuses to start the ~75 GB download until that file exists.
#
# An install that already HAS the H3 weights (a Repair, or an install made
# before 4.19.8) is not stopped here: nothing new is downloaded for a
# licence it already holds, and the panel asks for the acceptance before the
# next H3 render instead.
#
# The version below MUST equal model_licences.LICENCES["h3"]["version"]
# (test_model_licences.py holds them together). Pinokio stops an install only
# on "error:" / "errno " in the output, so the refusal is ONE "FATAL error:" line.
. scripts/pinokio/h3_roots.sh >/dev/null
H3_LICENCE_VERSION="2026-08-02"
STATE="${LTX_STATE_DIR:-$APP_ROOT/state}"
case "$STATE" in /*) ;; *) STATE="$APP_ROOT/$STATE" ;; esac
if [ -f "$STATE/licence-h3-$H3_LICENCE_VERSION.accepted" ]; then
  echo "MiniMax H3 licence: accepted in Phosphene."
  exit 0
fi
# "Already installed" means a real weight file is on disk (the bf16 DiT, or a
# built Q8 shard) - an empty folder is not an install (Codex review, 4.19.8).
h3_has_weights() {
  [ -s "$H3_MODELS_ROOT/$H3_DIT_REL" ] || [ -s "$H3_LAYOUT/$H3_DIT_REL" ] && return 0
  local d
  for d in "$H3_LAYOUT/h3-dit-q8" "$H3_MODELS_ROOT/h3-dit-q8"; do
    [ -n "$(find "$d" -maxdepth 1 -name '*.safetensors' -size +1M 2>/dev/null | head -1)" ] && return 0
  done
  return 1
}
if h3_has_weights; then
  echo "MiniMax H3 licence: not accepted in Phosphene yet. H3 is already on this Mac, so this continues; Phosphene asks you to accept the licence before your next H3 render."
  exit 0
fi
echo "FATAL error: Open Phosphene, click Hailuo H3 in the engine switcher, read the MiniMax H3 licence and tick I accept, then run Install Hailuo H3 again."
exit 1
