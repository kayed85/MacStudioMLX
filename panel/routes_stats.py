"""The repo-stats dashboard's routes — the first family out of the chain.

Handlers here are the bodies that lived in mlx_ltx_panel.py's
do_GET/do_POST chains, verbatim except for two mechanical renames the
move forces: `self` → `h` (the Handler instance arrives as a
parameter), and panel-module globals → `P.<name>` (a function's global
namespace is the module it is DEFINED in, so the panel's names must be
reached through the module object). `P` is assigned by mlx_ltx_panel at
import-wiring time — see panel/__init__.py for why it is not an import.
"""
from __future__ import annotations

import threading
from urllib.parse import parse_qs

from panel.routes import get, post

P = None  # the running mlx_ltx_panel module; assigned at wiring time


# Repo-stats dashboard. HTML is a static file in panel_assets/;
# JSONL data lives in state/ and gets appended by stats_fetch_loop.
# Loopback-only via do_GET's early _is_local_request guard.
@get("/stats")
@get("/stats/")
def stats_page(h, parsed) -> None:
    try:
        html = P.STATS_HTML_FILE.read_bytes()
    except FileNotFoundError:
        h.send_error(404, "stats.html missing — repo install broken")
        return
    h.send_response(200)
    h.send_header("Content-Type", "text/html; charset=utf-8")
    h.send_header("Cache-Control", "no-cache")
    h.send_header("Content-Length", str(len(html)))
    h.end_headers()
    try: h.wfile.write(html)
    except (BrokenPipeError, ConnectionResetError): pass


@get("/stats/data")
def stats_data(h, parsed) -> None:
    # The whole archive, always (see P._stats_data_for_range for why it no
    # longer trims by `range`); the page applies the range by date. `range`
    # is still read so an old cached page's URL keeps working.
    qs = parse_qs(parsed.query)
    range_key = (qs.get("range", [P.STATS_RANGE_DEFAULT])[0] or
                 P.STATS_RANGE_DEFAULT)
    try:
        body = P._stats_data_for_range(range_key)
    except FileNotFoundError:
        # No snapshot yet — return an empty but valid JSONL so the
        # dashboard's "no data yet" path renders cleanly.
        body = b""
    h.send_response(200)
    h.send_header("Content-Type", "application/jsonl; charset=utf-8")
    h.send_header("Cache-Control", "no-cache")
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    try: h.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError): pass


# Usage section of the same dashboard. Local aggregates from
# state/usage-log.jsonl always; fleet aggregates from PostHog when a
# personal API key is configured (6 h cached). `?force=1` busts the
# cache for the dashboard's refresh button. Never raises out — a
# broken usage view must not 500 the maintainer's stats page.
@get("/stats/usage")
def stats_usage(h, parsed) -> None:
    try:
        qs = parse_qs(parsed.query)
        range_key = (qs.get("range", [P.STATS_RANGE_DEFAULT])[0] or
                     P.STATS_RANGE_DEFAULT)
        report = P._usage_report(force=qs.get("force", ["0"])[0] == "1",
                                  range_key=range_key)
    except Exception as exc:
        report = {"ok": False, "source": "local",
                  "error": str(exc)[:200]}
    h._json(report)


# Manual refresh trigger for the stats dashboard. The fetcher
# also runs daily in stats_fetch_loop; this is the "I want to
# see today's number RIGHT NOW" button. Returns 202 + spawns
# the fetch in a background thread so the HTTP response doesn't
# block on a possibly-slow API roundtrip.
@post("/stats/refresh")
def stats_refresh(h, path, qs, ctype) -> None:
    if P.STATS_READONLY:
        # Read-only stats mode serves ANOTHER install's archive; writing it
        # is the daily launchd job's business, never this panel's.
        h._json({"ok": False, "readonly": True,
                 "hint": "read-only dashboard: the archive is refreshed by "
                         "the daily stats job, not by this panel"}, 409)
        return
    if not P._stats_fetch_enabled():
        # Maintainer-only (issue #106): without PHOSPHENE_STATS_FETCH=1 this
        # panel never reads a GitHub token or runs `gh`, not even on a click.
        h._json({"ok": False, "disabled": True,
                 "hint": "this panel does not fetch repo stats (maintainer "
                         "opt-in: PHOSPHENE_STATS_FETCH=1); the archive is "
                         "refreshed by the daily stats job"}, 409)
        return
    threading.Thread(
        target=P._run_stats_fetch_once, daemon=True,
        name="phos-stats-manual-refresh",
    ).start()
    h._json({"ok": True, "queued": True,
             "hint": "fetch running in background; reload "
                     "/stats in ~15 s for fresh numbers"}, 202)
