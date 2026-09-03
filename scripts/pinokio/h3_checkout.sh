#!/bin/bash
# Move the vendored minimax-h3-mlx checkout to the branch the app expects,
# and re-sync its venv deps. THE ONE PLACE THE H3 BRANCH PIN LIVES.
#
# Issue #74 (diagnosed by @blackest): installs cloned before the v2 engine sat
# on `codex/h3-engine`, and Update's Q8 build step ran h3_build_q8.sh against
# that tree — where scripts/quantize_stream.py has never existed — so the
# half-memory engine never got built on any pre-existing install. The H3
# installer already moved the branch on a re-install; Update did not. Both
# call this now.
#
# Usage: bash scripts/pinokio/h3_checkout.sh <h3-checkout-dir>
# Exit 0 = the tree is on the pin and its deps are synced. Non-zero = left as
# it was (nothing destructive happens before the fetch succeeds).
set -u
H3_BRANCH="codex/h3-engine-v2"
H3_URL="https://github.com/mrbizarro/minimax-h3-mlx.git"
H3_DIR="${1:-}"
if [ -z "$H3_DIR" ] || [ ! -d "$H3_DIR/.git" ]; then
  echo "h3_checkout.sh: no H3 checkout at '${H3_DIR:-<none>}' - nothing to move."
  exit 1
fi
cd "$H3_DIR" || exit 1
git remote get-url origin >/dev/null 2>&1 || git remote add origin "$H3_URL"
if ! git fetch --force origin "$H3_BRANCH"; then
  echo "WARN: could not fetch $H3_BRANCH from $H3_URL - H3 checkout left as is."
  exit 1
fi
if [ -d minimax_h3_mlx ]; then
  git reset --hard HEAD >/dev/null
else
  echo 'WARN: not the H3 tree - skipping reset'
fi
if ! git checkout --force -B "$H3_BRANCH" FETCH_HEAD; then
  echo "WARN: could not check out $H3_BRANCH - H3 checkout left as is."
  exit 1
fi
echo "H3 engine at $H3_BRANCH ($(git rev-parse --short HEAD))"
if [ ! -f scripts/quantize_stream.py ]; then
  echo "WARN: $H3_BRANCH has no scripts/quantize_stream.py - the Q8 build cannot run from this tree."
  exit 1
fi
# The branch move can change requirements; uv is a no-op when nothing did.
if [ -x .venv/bin/python ]; then
  uv pip install --python .venv/bin/python -r requirements.txt \
    || echo 'WARN: H3 requirements re-sync failed - re-run the H3 engine Install.'
fi
