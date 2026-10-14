#!/usr/bin/env python3
"""FILM-43 (server half): the board list gets dates, a thumbnail, rename
(already possible via the ordinary save route — locked here) and a real
duplicate action; a music-video re-plan updates its own board instead of
minting a duplicate.
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


def _board(bid, shots, **kw):
    b = {"schema": 1, "id": bid, "title": "T", "created_at": 1,
         "policy": storyboard.default_policy(), "cast": [],
         "engine_mode": "ltx", "shots": shots}
    b.update(kw)
    return b


def _shot(n, **kw):
    s = {"n": n, "uid": f"shot_{n:08x}", "mode": "text", "engine": "ltx",
         "prompt": f"shot {n}", "duration_s": 5.0, "seed": n, "refs": [],
         "status": "pending"}
    s.update(kw)
    return s


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

    def post(self, action, form):
        h = FakeHandler()
        panel.Handler._storyboard_post(h, action, "", form)
        return h


class TheSummaryCarriesDatesAndAThumbnail(Sandbox):
    def test_updated_at_created_at_and_thumb_are_exposed(self):
        board = _board("sb_1", [
            _shot(1),
            _shot(2, draft_output="/o/s2.mp4"),
        ], created_at=100, updated_at=200)
        summary = panel._sb_board_summary(board)
        self.assertEqual(summary["created_at"], 100)
        self.assertEqual(summary["updated_at"], 200)
        self.assertEqual(summary["thumb"], "/o/s2.mp4")

    def test_no_clips_means_no_thumbnail(self):
        board = _board("sb_2", [_shot(1)])
        summary = panel._sb_board_summary(board)
        self.assertIsNone(summary["thumb"])


class TheDuplicateAction(Sandbox):
    def test_duplicate_makes_a_full_independent_copy(self):
        board = self.save(_board("sb_orig", [
            _shot(1, draft_output="/o/s1.mp4", status="done", uid="shot_aaaa"),
        ], title="My Film"))
        h = self.post("duplicate", {"id": ["sb_orig"]})
        self.assertEqual(h.status, 200, h.payload)
        new_id = h.payload["id"]
        self.assertNotEqual(new_id, "sb_orig")
        self.assertEqual(h.payload["title"], "My Film (copy)")

        copy = storyboard.load_storyboard(self.state, new_id)
        self.assertEqual(copy["title"], "My Film (copy)")
        # the clip reference travels — nothing is re-rendered by duplicating
        self.assertEqual(copy["shots"][0]["draft_output"], "/o/s1.mp4")
        # but the shot's identity does NOT — a save on one board must never
        # be able to merge onto the other's shots (FILM-10's uid contract)
        self.assertNotEqual(copy["shots"][0]["uid"], "shot_aaaa")

        original = storyboard.load_storyboard(self.state, "sb_orig")
        self.assertEqual(original["title"], "My Film")   # untouched

    def test_duplicate_does_not_inherit_server_only_bookkeeping(self):
        board = self.save(_board("sb_orig2", [_shot(1)], title="X",
                                 render_intent={"pass": "draft", "only": None},
                                 planner={"state": "running"},
                                 auto_film="/o/old_film.mp4"))
        h = self.post("duplicate", {"id": ["sb_orig2"]})
        copy = storyboard.load_storyboard(self.state, h.payload["id"])
        self.assertNotIn("render_intent", copy)
        self.assertNotIn("planner", copy)
        self.assertNotIn("auto_film", copy)

    def test_duplicating_an_untitled_board_is_still_sensible(self):
        board = self.save(_board("sb_orig3", [_shot(1)], title=""))
        h = self.post("duplicate", {"id": ["sb_orig3"]})
        self.assertEqual(h.status, 200)
        self.assertIn("copy", h.payload["title"].lower())

    def test_an_unknown_board_is_a_404_style_error(self):
        h = self.post("duplicate", {"id": ["sb_nope"]})
        self.assertFalse(h.payload.get("ok", True))


if __name__ == "__main__":
    unittest.main()
