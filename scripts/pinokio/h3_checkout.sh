#!/bin/bash
# Move the vendored minimax-h3-mlx checkout to the EXACT engine commit this
# Phosphene release was validated against, and re-sync its venv deps.
# THE ONE PLACE THE H3 ENGINE PIN LIVES.
#
# Issue #74 (diagnosed by @blackest): installs cloned before the v2 engine sat
# on `codex/h3-engine`, and Update's Q8 build step ran h3_build_q8.sh against
# that tree — where scripts/quantize_stream.py has never existed — so the
# half-memory engine never got built on any pre-existing install. The H3
# installer already moved the branch on a re-install; Update did not. Both
# call this now.
#
# 4.19.0 — A COMMIT, NOT A BRANCH TIP. Through 4.18.x this checked out
# whatever `codex/h3-engine-v2` pointed at on the day it ran, so pushing the
# engine branch changed every installed Phosphene's H3 at its next Update or
# "Update Hailuo H3 runner" — with no Phosphene release, no gates and no way
# back. The panel and the runner are one product (the panel passes flags the
# runner must know), so the engine now moves only when a Phosphene release
# moves this pin, the same contract the LTX lane (ltx_checkout.sh, a tag) and
# the music engine (engine_pin.txt, a SHA) already have. H3_PIN_REF is a ref on
# the engine repo that CONTAINS the pin — fetching it is how the commit
# arrives; the commit is what gets checked out, and HEAD is verified after.
# Moving the pin: push the engine commit to a ref there (a new branch or tag;
# never force-move one), then change both lines below in the same commit.
#
# Usage: bash scripts/pinokio/h3_checkout.sh <h3-checkout-dir>
# Exit 0 = the tree is at the pin and its deps are synced. Non-zero = left as
# it was (nothing destructive happens before the pinned commit is on disk).
set -u
H3_PIN_SHA="3f6c75c09ccfa336a8c211b787d6a2350d83ef7a"
H3_PIN_REF="phosphene/v4.19.0"
H3_LOCAL_BRANCH="phosphene-pinned"
H3_URL="https://github.com/mrbizarro/minimax-h3-mlx.git"
H3_DIR="${1:-}"
if [ -z "$H3_DIR" ] || [ ! -d "$H3_DIR/.git" ]; then
  echo "h3_checkout.sh: no H3 checkout at '${H3_DIR:-<none>}' - nothing to move."
  exit 1
fi
cd "$H3_DIR" || exit 1
git remote get-url origin >/dev/null 2>&1 || git remote add origin "$H3_URL"
have_pin() { git cat-file -e "${H3_PIN_SHA}^{commit}" 2>/dev/null; }
# The ref first (one round trip, and it keeps the pin reachable locally); the
# bare commit as a fallback for a remote where the ref was renamed but the
# commit is still reachable from something else.
git fetch --force origin "$H3_PIN_REF" >/dev/null 2>&1 || true
have_pin || git fetch --force origin "$H3_PIN_SHA" >/dev/null 2>&1 || true
if ! have_pin; then
  echo "WARN: could not fetch the H3 engine pin ${H3_PIN_SHA:0:7} ($H3_PIN_REF) from $(git remote get-url origin) - H3 checkout left as is."
  exit 1
fi
if [ -d minimax_h3_mlx ]; then
  git reset --hard HEAD >/dev/null
else
  echo 'WARN: not the H3 tree - skipping reset'
fi
if ! git checkout --force -B "$H3_LOCAL_BRANCH" "$H3_PIN_SHA" >/dev/null 2>&1; then
  echo "WARN: could not check out the H3 engine pin ${H3_PIN_SHA:0:7} - H3 checkout left as is."
  exit 1
fi
if [ "$(git rev-parse HEAD)" != "$H3_PIN_SHA" ]; then
  echo "WARN: H3 checkout is at $(git rev-parse --short HEAD), not the pin ${H3_PIN_SHA:0:7}."
  exit 1
fi
echo "H3 engine at ${H3_PIN_SHA:0:7} (pinned, $H3_PIN_REF)"
if [ ! -f scripts/quantize_stream.py ]; then
  echo "WARN: the H3 pin has no scripts/quantize_stream.py - the Q8 build cannot run from this tree."
  exit 1
fi
# The branch move can change requirements; uv is a no-op when nothing did.
if [ -x .venv/bin/python ]; then
  uv pip install --python .venv/bin/python -r requirements.txt \
    || echo 'WARN: H3 requirements re-sync failed - re-run the H3 engine Install.'
fi
