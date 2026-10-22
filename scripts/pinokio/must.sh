#!/usr/bin/env bash
# Run one REQUIRED command; if it fails, say so in the words Pinokio acts on.
#
#   bash scripts/pinokio/must.sh '<what failed + what to do>' <command> [args...]
#
# WHY. Pinokio 8.2 ignores a shell.run step's exit code. It stops a script only
# when the step's OUTPUT matches /error:/i or /errno /i (pinokiod
# kernel/shells.js, the default `on` handlers). A required command whose own
# failure text has neither word (patch_ltx_codec.py's banner, music_fetch.py's
# "YuE2 pack: ..." line, a missing interpreter's "No such file") therefore let
# the install march on to "ready" with the engine broken. This wrapper runs the
# command unchanged and, when it exits non-zero, prints ONE line
#
#   FATAL error: <reason> (exit N)
#
# and exits with the command's own code.
#
# Keep the reason plain and free of "error:" / "errno " (it also sits on the
# dispatch line Pinokio echoes), one sentence of cause plus the next click, and
# short enough that the whole dispatch stays under ~350 characters.
reason="${1:-}"
if [ -z "$reason" ] || [ "$#" -lt 2 ]; then
  echo "FATAL error: must.sh was called without a reason and a command - report this line."
  exit 2
fi
shift
"$@"
rc=$?
if [ "$rc" -ne 0 ]; then
  echo "FATAL error: $reason (exit $rc)"
  exit "$rc"
fi
