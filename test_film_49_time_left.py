"""FILM-49 [P2]: "Time left" during a film counted only the current batch.

_sb_render_thread submits a film "ONE BUCKET AT A TIME" (its own docstring),
so /status.eta_sec — the sum of per-job ETAs for whatever is queued RIGHT
NOW — only ever covered that one bucket. sbRenderRunBar's `allMine` check
is true for nearly the whole render (nothing else typically shares the
queue with a film), so the shallow bucket total was what actually
displayed: "12m left" with hours still to go once later buckets (the
report's ~19-min singing shots) were counted.

The fix combines eta_sec (what's genuinely queued) with the film's OWN
per-shot estimate for every shot that hasn't been queued at all yet.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NODE = shutil.which("node")
STORYBOARD_JS = (ROOT / "webapp" / "js" / "storyboard.js").read_text(encoding="utf-8")


@unittest.skipUnless(NODE, "node not on PATH")
class TimeLeftCountsTheWholeFilm(unittest.TestCase):
    def _run(self, js: str) -> dict:
        r = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip())

    def _harness(self, *, queue_ns, current_n, eta_sec, per_shot, shots,
                 current_remaining=None) -> str:
        """queue_ns: shot numbers with a job currently queued (all tagged
        to this film). current_n: the shot rendering right now, or None.
        per_shot: {n: seconds} the film's own per-shot estimate.
        shots: [{n, out}] — out truthy means this shot already has an
        output for the pass being priced."""
        from scripts.extract_panel_js import extract_function
        fns = "\n".join(extract_function(n, STORYBOARD_JS) for n in
                        ("sbFmtWall", "_sbTagOf", "sbRenderRunBar"))
        els = {}

        def _shots_js():
            return json.dumps([{"n": s["n"], "draft_output": s["out"], "status": "pending"}
                              for s in shots])

        def _queue_js():
            return json.dumps([{"params": {"session_tag": f"sb:film1#{n}"}} for n in queue_ns])

        current_js = ("null" if current_n is None else
                     json.dumps({"params": {"session_tag": f"sb:film1#{current_n}"},
                                 "progress": {"remaining_sec": current_remaining}}))
        return f"""
globalThis.SB = {{ id: 'film1' }};
const _els = {{}};
function el(id) {{ return _els[id] || (_els[id] = {{ id, textContent: '', innerHTML: '', hidden: true }}); }}
const document = {{ getElementById: el }};
function sbEl(id) {{ return el(id); }}
function snippet(s, n) {{ return String(s || '').slice(0, n); }}
function escapeHtml(s) {{ return String(s || ''); }}
{fns}
const LAST_STATUS = {{
  current: {current_js},
  queue: {_queue_js()},
  paused: false,
  eta_sec: {eta_sec},
}};
const r = {{
  rendering: true,
  pass: 'draft',
  board: {{ shots: {_shots_js()} }},
  per_shot_est: {json.dumps(per_shot)},
}};
sbRenderRunBar(r);
console.log(JSON.stringify({{ sub: el('sbRunSub').textContent }}));
"""

    def test_not_yet_queued_shots_are_added_to_the_queues_own_total(self):
        """3 shots: #1 done, #2 queued (eta_sec covers it), #3 NOT queued
        yet (the report's later-bucket singing shot, 19 min = 1140s)."""
        out = self._run(self._harness(
            queue_ns=[2], current_n=None, eta_sec=120,
            per_shot={"2": 120, "3": 1140},
            shots=[{"n": 1, "out": "a.mp4"}, {"n": 2, "out": None}, {"n": 3, "out": None}],
        ))
        # 120 (queued) + 1140 (not yet queued) = 1260s = 21 min.
        self.assertIn("21 m", out["sub"])

    def test_old_behaviour_would_have_shown_only_the_queued_bucket(self):
        """Regression pin: eta_sec ALONE (the pre-fix number) must NOT be
        what ends up on screen when a shot is still unqueued."""
        out = self._run(self._harness(
            queue_ns=[2], current_n=None, eta_sec=120,
            per_shot={"2": 120, "3": 1140},
            shots=[{"n": 1, "out": "a.mp4"}, {"n": 2, "out": None}, {"n": 3, "out": None}],
        ))
        self.assertNotIn("2 m", out["sub"])  # sbFmtWall(120) == "about 2 m"

    def test_the_currently_rendering_shot_is_not_double_counted(self):
        """The shot actively rendering counts by its OWN remaining_sec
        (4.17 Codex EST-6: it used to count as nothing at all) — never
        again from per_shot_est."""
        out = self._run(self._harness(
            queue_ns=[], current_n=2, eta_sec=0,
            per_shot={"2": 600, "3": 300}, current_remaining=60,
            shots=[{"n": 1, "out": "a.mp4"}, {"n": 2, "out": None}, {"n": 3, "out": None}],
        ))
        # Shot 2's 60 s still to go + shot 3's 300 s = 6 min; shot 2's
        # 600 s per-shot estimate is not added on top.
        self.assertIn("6 m", out["sub"])

    def test_nothing_left_shows_no_time_suffix(self):
        out = self._run(self._harness(
            queue_ns=[], current_n=None, eta_sec=0, per_shot={},
            shots=[{"n": 1, "out": "a.mp4"}],
        ))
        self.assertNotIn("left", out["sub"])


if __name__ == "__main__":
    unittest.main()
