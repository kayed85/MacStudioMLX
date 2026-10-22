#!/usr/bin/env bash
# Python 3.12 / MLX 0.32.2 must never enter LTX's Python 3.11 / MLX 0.31.1 venv.
#
# A failure prints one "FATAL error:" line: Pinokio 8.2 ignores a shell.run
# step's exit code and stops a script only when the step's OUTPUT matches
# /error:/i or /errno /i (pinokiod kernel/shells.js default `on` handlers).
set -euo pipefail
MUSIC_CHECKOUT="${1:-}"
if [ -z "$MUSIC_CHECKOUT" ]; then
  echo "FATAL error: no music engine folder was given to the venv step - report this line."
  exit 1
fi
# A pyvenv.cfg can survive a dangling managed-Python symlink. Probe execution.
if ! "$MUSIC_CHECKOUT/.venv/bin/python" -c 'import sys; assert sys.version_info[:2] == (3, 12)' >/dev/null 2>&1; then
  uv venv --clear --python 3.12 "$MUSIC_CHECKOUT/.venv" || {
    echo "FATAL error: could not create the Python 3.12 environment for the YuE2 music engine. Check your internet connection and free disk space (it downloads Python the first time), then click Install again."
    exit 1
  }
fi
