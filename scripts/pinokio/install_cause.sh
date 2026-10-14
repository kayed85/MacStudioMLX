#!/usr/bin/env bash
# WHY AN INSTALL STEP FAILED, IN ONE CLOSED WORD (4.17.5).
#
# Sourced by ltx_engine_env.sh (the engine's Python packages) and
# a2v_stems_deps.sh (the voice separator). Both used to fail with a class and
# nothing else: on 4.17.4 four fresh installs failed at engine_env and two
# separator installs at weights_failed, and nothing said whether the network,
# the disk or a package resolver was to blame. This reads the step's own
# output and answers with one word from a closed list - the word, never the
# output, is what a record and an analytics event carry (no paths, no hosts).
#
#   install_cause <log file>  ->  prints one of:
#     disk            the disk filled up
#     timeout         a download or request timed out
#     network         could not reach / talk to a server (DNS, TLS, reset)
#     python_missing  the venv's Python does not run
#     uv_error        the package tool itself failed (resolver, build, no uv)
#     other           none of the above
#
# Order matters: a uv timeout line also says "error sending request", and a
# full disk can surface as a failed build, so the specific causes are tested
# before the general ones. Matching is case-insensitive.
#
# docs/ANALYTICS.md (install_step.cause, separator_install.cause) lists the
# same words; test_install_cause.py runs this function on real failure lines.

install_cause() {
  local log="$1"
  [ -f "$log" ] || { echo other; return 0; }
  if grep -qiE 'no space left on device|os error 28|disk quota exceeded|ENOSPC|not enough (free )?(disk )?space' "$log"; then
    echo disk
  elif grep -qiE 'timed out|read timeout|connect timeout|timeout error|timeouterror|deadline exceeded|took longer than' "$log"; then
    echo timeout
  elif grep -qiE 'error sending request|dns error|failed to lookup address|could not resolve|nodename nor servname|name or service not known' "$log" \
    || grep -qiE 'connection (refused|reset|closed|aborted)|network is unreachable|no route to host|temporary failure in name resolution|remote ?disconnected' "$log" \
    || grep -qiE 'tls handshake|ssl error|sslerror|certificate verify failed|invalid peer certificate|proxy error|proxyerror|urlopen error' "$log" \
    || grep -qiE 'failed to fetch|failed to download|http status (5[0-9][0-9]|429)|status code: (5[0-9][0-9]|429)' "$log"; then
    echo network
  elif grep -qiE 'no working python|python[0-9.]* not executable|venv python not executable|interpreter .* does not exist|no interpreter found|no python at' "$log"; then
    echo python_missing
  elif grep -qiE 'no solution found|because .* depends on|failed to build|failed to prepare distributions|metadata-generation-failed' "$log" \
    || grep -qiE 'uv: command not found|command not found: uv|uv not found|resolution failed|could not find a version|requires-python|unsatisfiable' "$log"; then
    echo uv_error
  else
    echo other
  fi
}
