"""VC-08: the out-of-memory failure card offers "Retry smaller", not only a
retry of the identical job that will fail the same way.

WHAT THIS GUARDS. The failed Now-card's only action used to be Retry, which
re-submits the exact params that just OOM'd — same quality, same length,
guaranteed to fail again after another 10+ minutes on a memory-pressured Mac.

Server: /queue/retry now accepts an optional `overrides` form field (a JSON
object, allowlisted to {quality, frames} — never an arbitrary merge) applied
onto the source job's params before it's re-queued, still going through the
same _validate_character_quality gate as every other enqueue path.

Client: queue.js's smallerRetryOverrides(params) walks the quality ladder
down one rung and caps frames at 121 (5s) when the source job was longer;
the failed card shows "Retry smaller" + "Retry as is" only when the failure
looks memory-shaped (SIGKILL/jetsam) AND a smaller version actually exists,
otherwise just the original single "Retry".
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlencode

ROOT = Path(__file__).resolve().parent
STATE = Path(tempfile.mkdtemp(prefix="phos-retry-smaller-"))
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8318")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402
from panel.routes import POST_ROUTES  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from extract_panel_js import extract_function  # noqa: E402

NODE = shutil.which("node")


class _H:
    def __init__(s, body):
        s.body, s.out, s.code = body, None, None

    def _read_form_body(s):
        return s.body, parse_qs(s.body)

    def _json(s, obj, code=200):
        s.out, s.code = obj, code


def _post_retry(**form):
    h = _H(urlencode(form))
    POST_ROUTES["/queue/retry"](h, "/queue/retry", {}, "")
    return h.code, h.out


class ServerOverridesAreAllowlisted(unittest.TestCase):
    def setUp(self):
        with P.QUEUE_COND:
            self._before_hist = list(P.STATE["history"])
            self._before_queue = list(P.STATE["queue"])
        P.STATE["history"].insert(0, {
            "id": "srcjob1", "status": "failed",
            "params": {"mode": "t2v", "prompt": "a lighthouse", "quality": "standard",
                       "frames": 241, "width": 1280, "height": 704,
                       "engine": "ltx", "no_voice": "off"},
        })
        self.addCleanup(self._restore)

    def _restore(self):
        with P.QUEUE_COND:
            P.STATE["history"] = self._before_hist
            P.STATE["queue"] = self._before_queue

    def _queued_ids(self):
        with P.QUEUE_COND:
            before = {j["id"] for j in self._before_queue}
            return [j for j in P.STATE["queue"] if j["id"] not in before]

    def test_no_overrides_copies_verbatim(self):
        code, out = _post_retry(id="srcjob1")
        self.assertEqual(code, 200)
        self.assertTrue(out["ok"])
        q = self._queued_ids()
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0]["params"]["quality"], "standard")
        self.assertEqual(q[0]["params"]["frames"], 241)

    def test_quality_and_frames_override_applies(self):
        overrides = json.dumps({"quality": "balanced", "frames": 121})
        code, out = _post_retry(id="srcjob1", overrides=overrides)
        self.assertEqual(code, 200)
        self.assertTrue(out["ok"])
        q = self._queued_ids()
        self.assertEqual(q[0]["params"]["quality"], "balanced")
        self.assertEqual(q[0]["params"]["frames"], 121)
        # everything else still came from the source job, untouched
        self.assertEqual(q[0]["params"]["prompt"], "a lighthouse")
        self.assertEqual(q[0]["params"]["width"], 1280)

    def test_unknown_override_key_is_refused(self):
        overrides = json.dumps({"width": 99999})
        code, out = _post_retry(id="srcjob1", overrides=overrides)
        self.assertEqual(code, 400)
        self.assertIn("width", out["error"])
        self.assertEqual(self._queued_ids(), [])

    def test_unknown_quality_value_is_refused(self):
        overrides = json.dumps({"quality": "ultra"})
        code, out = _post_retry(id="srcjob1", overrides=overrides)
        self.assertEqual(code, 400)
        self.assertEqual(self._queued_ids(), [])

    def test_malformed_json_is_refused(self):
        code, out = _post_retry(id="srcjob1", overrides="{not json")
        self.assertEqual(code, 400)
        self.assertEqual(self._queued_ids(), [])

    def test_overrides_still_run_the_character_quality_gate(self):
        P.STATE["history"].insert(0, {
            "id": "srcjob2", "status": "failed",
            "params": {"mode": "t2v", "prompt": "a hero", "quality": "high",
                       "frames": 121, "character_id": "somechar",
                       "engine": "ltx", "no_voice": "off"},
        })
        # Stepping this down to balanced would reproduce the exact
        # character+non-high combination /queue/add already refuses -
        # the override path must hit the same gate, not bypass it.
        overrides = json.dumps({"quality": "balanced"})
        code, out = _post_retry(id="srcjob2", overrides=overrides)
        self.assertEqual(code, 400)
        self.assertEqual(self._queued_ids(), [])


class FailedCardOffersBothButtonsOnlyForMemoryFailures(unittest.TestCase):
    """4.17 merge: VC-08 (this suite) and SYS-07 both built "Retry smaller".
    One implementation ships: friendlyJobError flags `smaller` for the OOM
    and GPU-watchdog classes on LTX only (H3-22 — an H3 job has no LTX
    quality/canvas to step), the card shows "Retry smaller" + "Retry as is",
    and the click goes to /queue/retry with smaller=1, where the server
    shrinks the canvas too. VC-08's client ladder only stepped `quality`,
    which leaves the stored width/height — the canvas that actually decides
    memory — unchanged; it was removed. The server's allowlisted `overrides`
    field (ServerOverridesAreAllowlisted above) stays for API callers."""

    def _flags(self, *cases) -> list:
        if NODE is None:
            raise unittest.SkipTest("node not on PATH")
        src = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
        # friendlyJobError's generic fallback calls H3-22's _redactLocalPaths.
        fn = (extract_function("_redactLocalPaths", src) + "\n"
              + extract_function("friendlyJobError", src))
        calls = ",\n  ".join(
            f"(friendlyJobError({json.dumps(raw)}, {json.dumps(eng)}) || {{}}).smaller === true"
            for raw, eng in cases)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(fn + f"\nconsole.log(JSON.stringify([\n  {calls}\n]));")
            path = fh.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            return json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            Path(path).unlink(missing_ok=True)

    def test_smaller_is_offered_for_ltx_memory_and_watchdog_failures_only(self):
        got = self._flags(
            ("Helper killed by the OS: SIGKILL", "ltx"),
            ("helper exited from SIGABRT (GPU watchdog timeout)", "ltx"),
            ("Helper killed by the OS: SIGKILL", "h3"),
            ("shape mismatch (256, 256) vs (272, 272)", "ltx"),
        )
        self.assertEqual(got, [True, True, False, False])

    def test_card_offers_retry_smaller_and_retry_as_is(self):
        src = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
        self.assertIn('data-action="retry-smaller"', src)
        self.assertIn("<span>Retry as is</span>", src)
        self.assertNotIn("smallerRetryOverrides", src)

    def test_delegated_handler_sends_the_server_side_smaller_retry(self):
        main = (ROOT / "webapp" / "js" / "main.js").read_text(encoding="utf-8")
        self.assertEqual(main.count("btn.dataset.action === 'retry-smaller'"), 1)
        self.assertIn("retryJob(id, { smaller: true })", main)

    def test_oom_copy_drops_jargon(self):
        src = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
        # The sigkill branch of friendlyJobError - pull just that literal.
        self.assertIn("Your Mac ran out of memory.", src)
        self.assertNotIn("Helper killed by the OS", src)
        self.assertNotIn("(jetsam)", src)


if __name__ == "__main__":
    unittest.main()
