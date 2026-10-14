#!/usr/bin/env python3
"""FILM-01: "Rewrite N shots" must rewrite every named shot, not just the first.

Before the fix, `replan-shots` joined every shot's note into ONE feedback
string ("shot 3: ...\\nshot 7: ...") and made a single shot-mode planner
call. `storyboard_planner._parse_feedback`'s "shot N:" pattern only ever
matches the FIRST header and (re.DOTALL) folds every later note into that
one shot's note — so only the first named shot was ever actually rewritten;
every other named shot kept its old prompt AND its old pinned seed, so it
re-rendered as an identical clip.

The fix runs one shot-mode planner call per named shot, in one session,
threading the running spec through so each call sees the previous one's
result. This test stands in for the real planner with a fake that mimics
its documented per-shot contract (replace exactly the named shot, carry
every other shot across by reference, assign that shot a fresh seed) and
proves both named shots come back genuinely different while the untouched
shot is carried over byte-identical.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as panel                                        # noqa: E402
import storyboard                                                    # noqa: E402
import storyboard_planner                                            # noqa: E402


def _shot(n, prompt, seed):
    return {"n": n, "uid": f"shot_{n:08x}", "mode": "text", "engine": "ltx",
            "prompt": prompt, "duration_s": 5.0, "seed": seed, "refs": [],
            "status": "done", "draft_output": f"/out/S{n}.mp4"}


def _fake_plan_film(concept, *, n_shots, style, characters, must_include,
                    feedback, previous, engine, board_id, locations,
                    known_character_ids, max_dim, session):
    """Stands in for the real planner's documented shot-mode contract."""
    assert isinstance(feedback, dict) and "shot" in feedback, feedback
    target_n, note = feedback["shot"], feedback["note"]
    shots = [dict(s) for s in (previous.get("shots") or [])]
    for s in shots:
        if s["n"] == target_n:
            s["prompt"] = f"rewritten for shot {target_n}: {note}"
            s["seed"] = 90000 + target_n          # a fresh seed, every time
            s["status"] = "pending"
    spec = dict(previous)
    spec["shots"] = shots
    spec["_planner"] = {"model": "fake", "attempts": 1, "elapsed_s": 0.1,
                        "warnings": []}
    return spec


class RewriteMultipleShots(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.state, self.out = root / "state", root / "outputs"
        self.state.mkdir()
        self.out.mkdir()
        for p in (mock.patch.object(panel, "STATE_DIR", self.state),
                  mock.patch.object(panel, "OUTPUT", self.out),
                  mock.patch.object(panel, "push", lambda *a, **k: None),
                  mock.patch.object(panel, "_sb_h3_available", lambda: False),
                  mock.patch.object(panel, "_sb_known_character_ids", lambda: []),
                  mock.patch.object(panel, "_sb_max_dim", lambda: 1280)):
            p.start()
            self.addCleanup(p.stop)
        self.board = {"schema": 1, "id": "sb_rw", "title": "T", "created_at": 1,
                      "policy": storyboard.default_policy(), "cast": [],
                      "engine_mode": "ltx", "concept": "a test film",
                      "planner": {"state": "idle"},
                      "shots": [_shot(1, "shot one, untouched", 1001),
                                _shot(2, "shot two, old prompt", 1002),
                                _shot(3, "shot three, old prompt", 1003)]}
        storyboard.save_storyboard(self.state, self.board)
        panel._SB_PLANNERS.clear()
        panel._SB_PLANNERS["sb_rw"] = {"cancelled": False}
        self.addCleanup(panel._SB_PLANNERS.clear)

    def test_both_named_shots_get_distinct_new_prompts_and_seeds(self):
        previous = {k: self.board.get(k) for k in
                    ("schema", "id", "title", "cast", "policy", "shots")}
        brief = {"concept": self.board["concept"], "n_shots": 3, "style": "",
                 "characters": [], "must": [], "locations": [],
                 "shot_notes": {2: "make it bluer", 3: "faster camera"},
                 "engine_mode": "ltx", "reroll_ns": [2, 3]}
        with mock.patch.object(storyboard_planner, "plan_film",
                               side_effect=_fake_plan_film) as pf:
            panel._sb_plan_thread("sb_rw", brief, previous)
        self.assertEqual(pf.call_count, 2, "one planner call per named shot")

        reloaded = storyboard.load_storyboard(self.state, "sb_rw")
        by_n = {s["n"]: s for s in reloaded["shots"]}

        # Shot 1 was never named — carried across untouched.
        self.assertEqual(by_n[1]["prompt"], "shot one, untouched")
        self.assertEqual(by_n[1]["seed"], 1001)

        # Shot 2 AND shot 3 both got their OWN rewrite — the bug this fixes
        # is shot 3 silently keeping its old prompt/seed while shot 2's note
        # swallowed shot 3's text too.
        self.assertIn("make it bluer", by_n[2]["prompt"])
        self.assertIn("faster camera", by_n[3]["prompt"])
        self.assertNotIn("faster camera", by_n[2]["prompt"])
        self.assertNotIn("make it bluer", by_n[3]["prompt"])

        # Every rewritten shot got a genuinely NEW seed (not the pinned one),
        # and the two rewrites are not the same seed as each other.
        self.assertNotEqual(by_n[2]["seed"], 1002)
        self.assertNotEqual(by_n[3]["seed"], 1003)
        self.assertNotEqual(by_n[2]["seed"], by_n[3]["seed"])

        # Both go back to pending, unrendered, so the next render actually
        # spends a job on them.
        self.assertEqual(by_n[2]["status"], "pending")
        self.assertEqual(by_n[3]["status"], "pending")
        self.assertIsNone(by_n[2].get("draft_output"))
        self.assertIsNone(by_n[3].get("draft_output"))
        self.assertEqual(by_n[2]["stale_output"], "/out/S2.mp4")
        self.assertEqual(by_n[3]["stale_output"], "/out/S3.mp4")


if __name__ == "__main__":
    unittest.main()
