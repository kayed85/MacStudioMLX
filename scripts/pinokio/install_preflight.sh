#!/usr/bin/env bash
# Install disk preflight + the honest size/time line (4.20).
#
#   cwd : the app root
#
# Runs BEFORE the engine clone, the venv and every download, so a Mac without
# room is told in second one, in plain words (needed GB vs free GB), instead of
# dying forty minutes into a 27 GB download - or, worse, before Python ever
# ran, which is why the fleet has exactly one disk_full event. The numbers come
# from install_plan.py, which reads required_files.json -> install; the same
# module writes the background-download marker at the end of install.js.
#
# SEMANTICS: one deliberate stop - the free space is KNOWN to be short for the
# part before Start. Everything else is FAIL-OPEN: a preflight that cannot run
# must never be what blocks a fine install.
#
# HOW IT STOPS. Pinokio 8.2 ignores a shell.run step's exit code; the script
# stops only when the step's OUTPUT matches /error:/i or /errno /i (pinokiod
# kernel/shells.js, the default `on` handlers - ltx_engine_env.sh's "FATAL
# error:" relies on the same rule). install_plan.py prints a "FATAL error:"
# line on a shortfall, and that line is what stops the install; the `exit 1`
# is for every other caller. Nothing else here may print "error:".
#
# The venv does not exist yet, so this runs on Pinokio's conda Python (the
# "ai" bundle puts it first on PATH). /usr/bin/python3 is skipped unless the
# Command Line Tools are present: without them it is a stub that opens an
# "install developer tools" dialog instead of running anything.

PY=""
for c in python3 python; do
  p=$(command -v "$c" 2>/dev/null) || continue
  if [ "$p" = "/usr/bin/python3" ] && ! xcode-select -p >/dev/null 2>&1; then
    continue
  fi
  PY="$p"
  break
done

if [ -z "$PY" ]; then
  echo 'Install preflight skipped - no Python yet. Free space on this drive:'
  df -h . 2>/dev/null | tail -1
  exit 0
fi

OUT="$("$PY" install_plan.py preflight 2>&1)"
rc=$?
printf '%s\n' "$OUT"
if [ "$rc" = "1" ]; then
  # install_plan.py prints the FATAL line itself; if it ever exits 1 without one,
  # say so here - the exit code alone stops nothing under Pinokio 8.2.
  case "$OUT" in
    *"FATAL error:"*) ;;
    *) echo 'FATAL error: the install preflight refused to continue - the lines above say why. Fix that, then click Install again.' ;;
  esac
  exit 1
fi
# Any other failure (a crash, an old Python) is the preflight's problem, not
# the user's: say so and carry on.
if [ "$rc" != "0" ]; then
  echo "Install preflight could not run (exit $rc) - continuing."
fi
exit 0
