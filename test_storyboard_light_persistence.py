#!/usr/bin/env python3
"""FILM-14 (item 4, server half): the board's shared "Light" line can be set
and changed on an already-planned board through the ordinary save route —
no re-plan needed — and, like `locations`, a re-plan that never mentions
`light` at all must not erase it.
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


def _shot(n, **kw):
    s = {"n": n, "uid": f"shot_{n:08x}", "mode": "text", "engine": "ltx",
         "prompt": f"shot {n}", "duration_s": 5.0, "seed": n, "refs": [],
         "status": "pending"}
    s.update(kw)
    return s


def _board(bid, shots, **kw):
    b = {"schema": storyboard.SCHEMA_VERSION, "id": bid, "title": "T",
         "created_at": 1, "policy": storyboard.default_policy(), "cast": [],
         "engine_mode": "ltx", "concept": "a film", "shots": shots}
    b.update(kw)
    return b


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
                  mock.patch.object(panel, "push", lambda *a, **k: None),
                  # The "plan" action patches board["light"] synchronously,
                  # then hands off to a background thread that actually
                  # talks to the planner model — irrelevant here and, with
                  # no real model on disk, liable to leave the one global
                  # planner slot claimed if it is allowed to run for real.
                  mock.patch.object(panel, "_sb_plan_thread", lambda *a, **k: None)):
            p.start()
            self.addCleanup(p.stop)
        panel._SB_PLANNERS.clear()
        self.addCleanup(panel._SB_PLANNERS.clear)

    def save(self, board):
        storyboard.save_storyboard(self.state, board)
        return board

    def load(self, bid):
        return storyboard.load_storyboard(self.state, bid)

    def post_save(self, bid, board_payload):
        h = FakeHandler()
        body = json.dumps({"id": bid, "board": board_payload})
        panel.Handler._storyboard_post(h, "save", body, {})
        return h

    def post(self, action, form):
        h = FakeHandler()
        panel.Handler._storyboard_post(h, action, "", form)
        return h


class LightReachesAnUnrenderedShotWithNoReplan(Sandbox):
    def test_saving_a_light_note_on_an_existing_board_needs_no_replan(self):
        self.save(_board("sb_light1", [_shot(1)]))
        h = self.post_save("sb_light1", {"id": "sb_light1",
                                         "light": "cold blue moonlight"})
        self.assertEqual(h.status, 200, h.payload)
        self.assertEqual(self.load("sb_light1")["light"], "cold blue moonlight")

    def test_it_reaches_the_shots_prompt_at_render_time(self):
        self.save(_board("sb_light2", [_shot(1, prompt="he waits by the door")],
                         light="a single hard sidelight, long shadow"))
        board = self.load("sb_light2")
        job = storyboard.shot_to_job(
            board["shots"][0], board["policy"]["draft"],
            locations=storyboard.board_locations(board),
            wardrobe=storyboard.board_wardrobe(board),
            light=storyboard.board_light(board))
        self.assertIn("a single hard sidelight, long shadow", job["prompt"])

    def test_a_replan_that_never_mentions_light_keeps_it(self):
        # Same shape as locations/wardrobe: sbReplan()'s form never sends
        # `light`, so an absent field must not erase the board's value.
        self.save(_board("sb_light3", [_shot(1)], light="firelight, warm"))
        h = self.post("plan", {"id": ["sb_light3"], "concept": ["a film"],
                               "shots": ["1"]})
        self.assertTrue(h.payload.get("ok"), h.payload)
        self.assertEqual(self.load("sb_light3").get("light"), "firelight, warm")

    def test_a_plan_form_that_does_send_light_replaces_it(self):
        self.save(_board("sb_light4", [_shot(1)], light="firelight, warm"))
        h = self.post("plan", {"id": ["sb_light4"], "concept": ["a film"],
                               "shots": ["1"], "light": ["cold fluorescent"]})
        self.assertTrue(h.payload.get("ok"), h.payload)
        self.assertEqual(self.load("sb_light4").get("light"), "cold fluorescent")

    def test_a_board_with_no_light_note_still_saves_and_renders_unchanged(self):
        self.save(_board("sb_light5", [_shot(1, prompt="he waits")]))
        h = self.post_save("sb_light5", {"id": "sb_light5", "title": "Renamed"})
        self.assertEqual(h.status, 200, h.payload)
        board = self.load("sb_light5")
        self.assertEqual(board.get("light", ""), "")
        job = storyboard.shot_to_job(
            board["shots"][0], board["policy"]["draft"],
            light=storyboard.board_light(board))
        self.assertEqual(job["prompt"], "he waits")


if __name__ == "__main__":
    unittest.main()
