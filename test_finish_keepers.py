#!/usr/bin/env python3
"""FILM-47: "Finish keepers" honesty and bulk-grading.

  * POST /storyboard/keep-all-ungraded marks every rendered, un-skipped,
    ungraded shot KEEP in ONE save — the bulk equivalent of pressing K on
    each one by hand, without the race risk of N separate grade calls each
    doing their own load-mutate-save.
  * A cut or already-graded shot is left alone.
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


def _shot(n, **kw):
    s = {"n": n, "uid": f"shot_{n:08x}", "mode": "text", "engine": "ltx",
         "prompt": f"shot {n}", "duration_s": 5.0, "seed": n, "refs": [],
         "status": "pending"}
    s.update(kw)
    return s


def _board(bid, shots):
    return {"schema": 1, "id": bid, "title": "T", "created_at": 1,
            "policy": storyboard.default_policy(), "cast": [],
            "engine_mode": "ltx", "shots": shots}


class FakeHandler:
    def __init__(self):
        self.status = None
        self.payload = None

    def _json(self, payload, status: int = 200):
        self.payload, self.status = payload, status


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.state, self.out = root / "state", root / "outputs"
        self.state.mkdir()
        self.out.mkdir()
        for p in (mock.patch.object(panel, "STATE_DIR", self.state),
                  mock.patch.object(panel, "OUTPUT", self.out),
                  mock.patch.object(panel, "push", lambda *a, **k: None)):
            p.start()
            self.addCleanup(p.stop)

    def save(self, board):
        storyboard.save_storyboard(self.state, board)
        return board

    def load(self, bid):
        return storyboard.load_storyboard(self.state, bid)

    def post(self, action, form):
        h = FakeHandler()
        panel.Handler._storyboard_post(h, action, "", form)
        return h


class TheBulkKeepRoute(Sandbox):
    def test_every_rendered_ungraded_shot_is_kept_in_one_save(self):
        self.save(_board("sb_bulk", [
            _shot(1, draft_output="/o/s1.mp4", status="done"),
            _shot(2, draft_output="/o/s2.mp4", status="done"),
            _shot(3, draft_output="/o/s3.mp4", status="skipped", grade="cut"),
        ]))
        h = self.post("keep-all-ungraded", {"id": ["sb_bulk"]})
        self.assertEqual(h.status, 200, h.payload)
        self.assertEqual(h.payload["kept"], 2)
        reloaded = self.load("sb_bulk")
        self.assertEqual(reloaded["shots"][0]["grade"], "keep")
        self.assertEqual(reloaded["shots"][1]["grade"], "keep")
        # the cut shot is untouched
        self.assertEqual(reloaded["shots"][2]["grade"], "cut")

    def test_an_already_graded_shot_is_left_alone(self):
        self.save(_board("sb_bulk2", [
            _shot(1, draft_output="/o/s1.mp4", status="done", grade="reroll"),
        ]))
        h = self.post("keep-all-ungraded", {"id": ["sb_bulk2"]})
        self.assertEqual(h.payload["kept"], 0)
        self.assertEqual(self.load("sb_bulk2")["shots"][0]["grade"], "reroll")

    def test_an_unrendered_shot_is_never_kept(self):
        self.save(_board("sb_bulk3", [_shot(1)]))   # no draft_output at all
        h = self.post("keep-all-ungraded", {"id": ["sb_bulk3"]})
        self.assertEqual(h.payload["kept"], 0)
        self.assertNotIn("grade", self.load("sb_bulk3")["shots"][0])

    def test_nothing_to_keep_still_answers_ok(self):
        self.save(_board("sb_bulk4", []))
        h = self.post("keep-all-ungraded", {"id": ["sb_bulk4"]})
        self.assertEqual(h.status, 200)
        self.assertEqual(h.payload["kept"], 0)


if __name__ == "__main__":
    unittest.main()
