#!/usr/bin/env python3
"""FILM-22: a rendered ("done") shot can be edited and re-rendered without
going through the LLM Rewrite — which refuses outright while the queue is
non-empty and burns a planner call for what might be "just try again."

`_sb_new_take` is the escape hatch: archive the current clip(s) into
`takes[]` (never delete them), put the shot back to pending with a fresh
seed, and clear its grade. No planner, no LLM, and it works even while
other shots are queued (the route imposes no queue-empty check, unlike
replan-shots).
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as panel                                        # noqa: E402
import storyboard                                                    # noqa: E402


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


class TheNewTakeFunction(unittest.TestCase):
    def test_the_clip_is_archived_not_deleted(self):
        board = {"shots": [{"n": 1, "status": "done", "seed": 42,
                            "draft_output": "/o/s1_draft.mp4",
                            "final_output": "/o/s1_final.mp4",
                            "grade": "keep"}]}
        shot = panel._sb_new_take(board, 1)
        self.assertIs(shot, board["shots"][0])
        takes = shot["takes"]
        self.assertEqual(len(takes), 2)
        by_pass = {t["pass"]: t for t in takes}
        self.assertEqual(by_pass["draft"]["path"], "/o/s1_draft.mp4")
        self.assertEqual(by_pass["draft"]["seed"], 42)
        self.assertEqual(by_pass["final"]["path"], "/o/s1_final.mp4")
        self.assertIn("at", by_pass["draft"])

    def test_the_shot_is_unlocked_and_reseeded(self):
        board = {"shots": [{"n": 1, "status": "done", "seed": 42,
                            "draft_output": "/o/s1.mp4", "grade": "keep",
                            "error": "an old error"}]}
        shot = panel._sb_new_take(board, 1)
        self.assertEqual(shot["status"], "pending")
        self.assertIsNone(shot["grade"])
        self.assertNotIn("error", shot)
        self.assertNotIn("draft_output", shot)
        self.assertNotEqual(shot["seed"], 42)
        self.assertIsInstance(shot["seed"], int)

    def test_a_repeat_press_never_reuses_the_same_seed(self):
        board = {"shots": [{"n": 1, "status": "done", "seed": 42,
                            "draft_output": "/o/s1.mp4"}]}
        panel._sb_new_take(board, 1)
        seed1 = board["shots"][0]["seed"]
        board["shots"][0]["status"] = "done"
        board["shots"][0]["draft_output"] = "/o/s1_take2.mp4"
        panel._sb_new_take(board, 1)
        seed2 = board["shots"][0]["seed"]
        self.assertNotEqual(seed1, seed2)
        self.assertEqual(len(board["shots"][0]["takes"]), 2)

    def test_prompt_mode_and_music_video_fields_are_untouched(self):
        # Unlike a Rewrite, a New take never touches the shot's creative or
        # structural fields — only its render bookkeeping and seed.
        board = {"shots": [{"n": 1, "status": "done", "seed": 42,
                            "draft_output": "/o/s1.mp4", "prompt": "she sings",
                            "mode": "a2v", "music_video": {"kind": "singing"},
                            "audio": "/x/song.wav"}]}
        shot = panel._sb_new_take(board, 1)
        self.assertEqual(shot["prompt"], "she sings")
        self.assertEqual(shot["mode"], "a2v")
        self.assertEqual(shot["music_video"], {"kind": "singing"})
        self.assertEqual(shot["audio"], "/x/song.wav")

    def test_no_such_shot_returns_none(self):
        board = {"shots": [{"n": 1, "status": "done"}]}
        self.assertIsNone(panel._sb_new_take(board, 9))

    def test_a_shot_with_no_clip_at_all_still_unlocks_cleanly(self):
        board = {"shots": [{"n": 1, "status": "pending", "seed": 5}]}
        shot = panel._sb_new_take(board, 1)
        self.assertEqual(shot["takes"], [])
        self.assertEqual(shot["status"], "pending")


class TheNewTakeRoute(unittest.TestCase):
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

    def post(self, board_id, n):
        h = FakeHandler()
        panel.Handler._storyboard_post(h, "new-take", "",
                                       {"id": [board_id], "n": [str(n)]})
        return h

    def test_works_even_while_the_queue_is_busy(self):
        # No claim, no busy check — unlike replan-shots/render, this route
        # never refuses because something else is queued.
        board = self.save(_board("sb_nt", [
            {"n": 1, "status": "done", "seed": 1, "draft_output": "/o/s1.mp4"}]))
        with mock.patch.object(panel, "STATE", {"running": {"id": "x"}, "queue": [{}]}):
            h = self.post("sb_nt", 1)
        self.assertEqual(h.status, 200, h.payload)
        reloaded = storyboard.load_storyboard(self.state, "sb_nt")
        self.assertEqual(reloaded["shots"][0]["status"], "pending")
        self.assertEqual(len(reloaded["shots"][0]["takes"]), 1)

    def test_an_unknown_shot_is_a_404(self):
        board = self.save(_board("sb_nt2", [{"n": 1, "status": "done"}]))
        h = self.post("sb_nt2", 9)
        self.assertEqual(h.status, 404)

    def test_the_reply_carries_the_full_board_payload(self):
        board = self.save(_board("sb_nt3", [
            {"n": 1, "status": "done", "seed": 1, "draft_output": "/o/s1.mp4"}]))
        h = self.post("sb_nt3", 1)
        self.assertTrue(h.payload.get("ok"))
        self.assertEqual(h.payload["board"]["id"], "sb_nt3")
        self.assertEqual(h.payload["board"]["shots"][0]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
