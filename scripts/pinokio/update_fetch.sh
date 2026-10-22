#!/usr/bin/env bash
# RESOLVE THE UPSTREAM AND FETCH IT - the pre-pull step that establishes the
# ref update_obstruction_guard.sh and update_converge.sh depend on.
#
# Lifted out of update.js, where it was a four-line `"\n"-joined` STRING that
# carried two `echo 'FATAL error: ...'` branches. See scripts/pinokio/README.md.
#
# WHY IT MOVED (the #114 class, @Morac2, 2026-10-20). Pinokio 8.2 stops a
# shell.run step when its OUTPUT matches /error:/i or /errno /i (pinokiod
# kernel/shells.js default `on` handlers) and ignores the exit code. The shell
# ECHOES the dispatched command before running it, so a literal "error:" inside
# the command text trips that matcher ON THE SUCCESS PATH, before the step even
# runs - the shell detaches and Update dies on
#   [ -n "$U" ] || { echo 'FATAL error: no upstream configured'; exit 1; }
# with input.event ["error:"], exactly as reported in #114. The failure text we
# WANT Pinokio to act on must therefore be printed at RUNTIME from inside a
# script, never sit in the echoed dispatch. The dispatch is now the clean one
# line `bash scripts/pinokio/update_fetch.sh`; these FATAL lines fire only when
# the step genuinely fails, so Pinokio still stops on a real fault.
#
#   cwd : the app root, i.e. the panel repo
#
# SEMANTICS: unchanged, byte for byte in behaviour. One shell, NO `set -e`
# (the `|| { ...; exit 1; }` branches are the whole control flow). NOTHING here
# moves the tree - the guard and converge steps that follow do, and only after
# this fetch has provably succeeded, so reset only ever runs against a ref this
# fetch just refreshed.
U=$(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null)
[ -n "$U" ] || { echo 'FATAL error: no upstream configured'; exit 1; }
echo "updating $(git rev-parse --abbrev-ref HEAD) from $U"
git fetch "${U%%/*}" || { echo 'FATAL error: fetch failed (offline?) - nothing changed, re-run Update'; exit 1; }
