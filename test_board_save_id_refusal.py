#!/usr/bin/env python3
"""FILM-19 (server half): a save must never write one board's shots into
another board's file.

`sbFlushSave` posts `{id: SB.id, board: SB.payload.board}` — two reads of
client state that are supposed to name the same board but are not read
atomically. A switch-boards race (the 2 s poll landing after a row click, a
stale `sbLoad` reply — see FILM-02/test_storyboard_tab_race.py for the
client-side half of the same race class) can leave `SB.payload` holding
board A's shots while `SB.id` has already moved to board B. Before this fix
the save route trusted `id` alone and merged `incoming["shots"]` onto
whatever board `id` named — happily overwriting board B with board A's plan.

The route now refuses (400) whenever the posted board's own `id` disagrees
with the id the request is addressed to.
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

    def post_save(self, url_id, board_payload):
        h = FakeHandler()
        body = json.dumps({"id": url_id, "board": board_payload})
        panel.Handler._storyboard_post(h, "save", body, {})
        return h


class AMismatchedBoardIdIsRefused(Sandbox):
    def test_the_urls_board_and_the_bodys_board_must_agree(self):
        a = self.save(_board("sb_aaaa", [{"n": 1, "prompt": "board A shot"}]))
        b = self.save(_board("sb_bbbb", [{"n": 1, "prompt": "board B shot"}]))
        # SB.id has moved to board B, but SB.payload.board is stale board A.
        h = self.post_save("sb_bbbb", a)
        self.assertEqual(h.status, 400)
        self.assertIn("sb_aaaa", h.payload["error"])
        self.assertIn("sb_bbbb", h.payload["error"])
        # Neither board was touched.
        self.assertEqual(storyboard.load_storyboard(self.state, "sb_aaaa")["shots"],
                         a["shots"])
        self.assertEqual(storyboard.load_storyboard(self.state, "sb_bbbb")["shots"],
                         b["shots"])

    def test_a_matching_id_saves_normally(self):
        board = self.save(_board("sb_cccc", [{"n": 1, "prompt": "original"}]))
        edited = dict(board, shots=[{"n": 1, "prompt": "edited"}])
        h = self.post_save("sb_cccc", edited)
        self.assertEqual(h.status, 200, h.payload)
        self.assertEqual(
            storyboard.load_storyboard(self.state, "sb_cccc")["shots"][0]["prompt"],
            "edited")

    def test_a_board_with_no_id_yet_is_not_a_mismatch(self):
        # A brand-new board's very first save may post a board sub-object
        # that has never round-tripped through a GET and so carries no id
        # of its own yet — that must not be refused as a mismatch.
        board = self.save(_board("sb_dddd", []))
        fresh_shots = {"shots": [{"n": 1, "prompt": "new shot"}]}
        h = self.post_save("sb_dddd", fresh_shots)
        self.assertEqual(h.status, 200, h.payload)

    def test_an_empty_url_id_with_a_real_board_id_is_still_refused(self):
        board = self.save(_board("sb_eeee", [{"n": 1, "prompt": "x"}]))
        h = self.post_save("", board)
        self.assertEqual(h.status, 400)


if __name__ == "__main__":
    unittest.main()
