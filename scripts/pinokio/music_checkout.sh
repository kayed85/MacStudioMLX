#!/usr/bin/env bash
# One checkout implementation for Install and Update. The SHA has one home.
#
# Every failure prints one "FATAL error:" line: Pinokio 8.2 ignores a shell.run
# step's exit code and stops a script only when the step's OUTPUT matches
# /error:/i or /errno /i (pinokiod kernel/shells.js default `on` handlers).
# Update keeps its music step optional: post_update.sh runs this file through
# `defang`, which turns the FATAL line into a WARN.
set -euo pipefail
fatal() { echo "FATAL error: $*"; exit 1; }
MUSIC_PIN_FILE="$(cd "$(dirname "$0")/../music" && pwd)/engine_pin.txt"
[ -s "$MUSIC_PIN_FILE" ] || fatal "the YuE2 engine version file is missing ($MUSIC_PIN_FILE) - the Phosphene download is incomplete; run Update, then try again."
MUSIC_PIN=$(cat "$MUSIC_PIN_FILE")
MUSIC_CHECKOUT="${1:-}"
[ -n "$MUSIC_CHECKOUT" ] || fatal "no music engine folder was given to the checkout step - report this line."
[ -d "$MUSIC_CHECKOUT/.git" ] || fatal "the YuE2 music engine is not on this Mac ($MUSIC_CHECKOUT is missing) - its download did not finish. Click Install again with a working internet connection."
cd "$MUSIC_CHECKOUT"
# Fetch succeeds before a forced checkout; app-managed source, external weights.
git fetch --force origin "$MUSIC_PIN" || fatal "could not download the YuE2 engine version Phosphene needs (${MUSIC_PIN:0:7}). Check your internet connection, then click Install again."
git checkout --force --detach FETCH_HEAD || fatal "could not switch the YuE2 engine to the version Phosphene needs (${MUSIC_PIN:0:7}) - click Install again; if it repeats, report the lines above."
[ "$(git rev-parse HEAD)" = "$MUSIC_PIN" ] || fatal "the YuE2 engine is on the wrong version ($(git rev-parse --short HEAD), not ${MUSIC_PIN:0:7}) - click Install again; if it repeats, report this line."
echo "YuE2 engine pinned to $MUSIC_PIN"
