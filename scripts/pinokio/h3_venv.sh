#!/usr/bin/env bash
# The H3 engine's own Python 3.11 venv — self-healing. cwd: the H3 checkout.
# Lifted out of install_h3.js (a 372-char dispatch, above the ~350 Pinokio 8 is
# proven to survive) when that step had to learn the relocated checkout; the
# reasoning for probing the interpreter instead of a path lives above the step
# in install_h3.js.
#
# A venv that cannot run ends in one "FATAL error:" line: Pinokio 8.2 ignores a
# shell.run step's exit code and stops a script only when the step's OUTPUT
# matches /error:/i or /errno /i (pinokiod kernel/shells.js default `on`
# handlers). Before, `uv venv` failing printed 'venv python NOT executable' and
# install_h3.js went on to the 75 GB download with nothing to install into.
echo '=== H3 venv check ==='
if .venv/bin/python -c 'import sys' >/dev/null 2>&1; then
  echo 'H3 venv healthy - reusing it'
else
  echo 'H3 venv missing or broken (dangling interpreter) - rebuilding, no weights are re-downloaded'
  which uv && uv --version || echo 'uv NOT FOUND'
  rm -rf .venv
  uv venv --python 3.11 --seed .venv
  .venv/bin/python --version || echo 'venv python NOT executable'
  if ! .venv/bin/python -c 'import sys' >/dev/null 2>&1; then
    echo 'FATAL error: the Python 3.11 environment for Hailuo H3 could not be created. Check your internet connection and free disk space (it downloads Python the first time), then click Install again - the weights are not touched.'
    exit 1
  fi
fi
