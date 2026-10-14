#!/usr/bin/env python3
"""FILM-46: anchor stills get an approve step.

Before this, the render thread went straight from still to (expensive)
video with no way to look at the still first — the composition was only
ever seen after the minutes were already spent, and the estimate never
counted the stills at all.

Locked here: the render thread holds a shot back once its still lands until
it is approved; the /storyboard/approve-still and /approve-all-stills
routes; a freshly-landed still always needs its own approval even if an
earlier one on the same shot was approved; and the estimate counts still
generation.
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
         "prompt": f"a shot {n} happens", "duration_s": 5.0, "seed": 100 + n,
         "refs": [], "status": "pending"}
    s.update(kw)
    return s


def _board(bid, shots, **kw):
    b = {"schema": 1, "id": bid, "title": "T", "created_at": 1,
         "policy": storyboard.default_policy(), "cast": [],
         "engine_mode": "ltx", "anchor_stills": True, "stills_first": True,
         "shots": shots}
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
        self.queue_state = {"queue": [], "current": None, "history": []}
        for p in (mock.patch.object(panel, "STATE_DIR", self.state),
                  mock.patch.object(panel, "OUTPUT", self.out),
                  mock.patch.object(panel, "STATE", self.queue_state),
                  mock.patch.object(panel, "persist_queue", lambda: None),
                  mock.patch.object(panel, "push", lambda *a, **k: None)):
            p.start()
            self.addCleanup(p.stop)
        panel._SB_RENDERS.clear()
        self.addCleanup(panel._SB_RENDERS.clear)

    def save(self, board):
        storyboard.save_storyboard(self.state, board)
        return board

    def load(self, bid):
        return storyboard.load_storyboard(self.state, bid)

    def post(self, action, form):
        h = FakeHandler()
        panel.Handler._storyboard_post(h, action, "", form)
        return h


class TheEstimateCountsStills(unittest.TestCase):
    def test_a_shot_with_no_still_yet_is_charged_the_still_cost(self):
        board = {"shots": [_shot(1)], "anchor_stills": True,
                "policy": storyboard.default_policy()}
        est = storyboard.estimate(board, pass_name="draft")
        self.assertEqual(est["still_secs"], round(storyboard._STILL_RENDER_SECS))

    def test_a_shot_that_already_has_its_still_is_not_charged_again(self):
        board = {"shots": [_shot(1, still="/o/s1_still.png")], "anchor_stills": True,
                "policy": storyboard.default_policy()}
        est = storyboard.estimate(board, pass_name="draft")
        self.assertEqual(est["still_secs"], 0)

    def test_anchor_stills_off_charges_nothing(self):
        board = {"shots": [_shot(1)], "anchor_stills": False,
                "policy": storyboard.default_policy()}
        est = storyboard.estimate(board, pass_name="draft")
        self.assertEqual(est["still_secs"], 0)

    def test_an_h3_shot_is_never_charged_a_still_it_cannot_use(self):
        board = {"shots": [_shot(1, engine="h3")], "anchor_stills": True,
                "policy": storyboard.default_policy()}
        est = storyboard.estimate(board, pass_name="draft")
        self.assertEqual(est["still_secs"], 0)

    def test_the_still_cost_is_folded_into_the_render_total(self):
        board = {"shots": [_shot(1)], "anchor_stills": True,
                "policy": storyboard.default_policy()}
        est = storyboard.estimate(board, pass_name="draft")
        self.assertGreaterEqual(est["render_secs"], est["still_secs"])


class ANewStillAlwaysNeedsItsOwnApproval(Sandbox):
    def test_reconcile_resets_approval_on_a_fresh_still(self):
        board = _board("sb_re1", [_shot(1, still_job_id="j1", still_approved=True)])
        with mock.patch.object(panel, "_sb_job_index",
                               return_value={"j1": {"status": "done",
                                                    "output_path": "/o/new_still.png"}}):
            panel._sb_reconcile(board)
        shot = board["shots"][0]
        self.assertEqual(shot["still"], "/o/new_still.png")
        self.assertFalse(shot["still_approved"])


class TheRenderThreadHoldsForApproval(Sandbox):
    def _run(self, board, *, still_status="done", still_dims_ok=True):
        bid = board["id"]
        self.save(board)
        panel._SB_RENDERS[bid] = {"stop": False, "queued": 0}
        queued_forms = []

        def enqueue(form):
            queued_forms.append(form)
            return f"video-{len(queued_forms)}"

        def job_index():
            return {}

        with mock.patch.object(panel, "_sb_enqueue", side_effect=enqueue), \
             mock.patch.object(panel, "_sb_job_index", side_effect=job_index), \
             mock.patch.object(panel, "_sb_h3_available", return_value=False), \
             mock.patch.object(panel, "h3_supports_chain_prompts", return_value=False), \
             mock.patch.object(panel, "h3_supports_first_frame", return_value=False), \
             mock.patch.object(panel, "_sb_lipsync_gate", lambda *a, **k: None), \
             mock.patch.object(panel, "_sb_sweep_stage_a", lambda *a, **k: None), \
             mock.patch.object(storyboard, "shot_to_job",
                               lambda shot, *a, **k: {"mode": "t2v", "prompt": shot["prompt"]}):
            panel._sb_render_thread(bid, "draft", None)
        return queued_forms

    def test_a_shot_awaiting_approval_never_gets_a_video_job_queued(self):
        board = _board("sb_hold", [
            _shot(1, still="/o/s1_still.png", still_approved=False),
        ])
        forms = self._run(board)
        self.assertEqual(forms, [])
        after = self.load("sb_hold")
        self.assertEqual(after["shots"][0]["status"], "pending")

    def test_an_approved_shot_proceeds_to_video(self):
        board = _board("sb_go", [
            _shot(1, still="/o/s1_still.png", still_approved=True),
        ])
        forms = self._run(board)
        self.assertEqual(len(forms), 1)

    def test_only_the_unapproved_shots_are_held_others_still_render(self):
        board = _board("sb_mixed", [
            _shot(1, still="/o/s1.png", still_approved=True),
            _shot(2, still="/o/s2.png", still_approved=False),
        ])
        forms = self._run(board)
        self.assertEqual(len(forms), 1)
        self.assertIn("shot 1", forms[0]["prompt"])

    def test_stills_first_off_renders_straight_through_as_before(self):
        board = _board("sb_plain", [
            _shot(1, still="/o/s1.png", still_approved=False),
        ], stills_first=False)
        forms = self._run(board)
        self.assertEqual(len(forms), 1)


class TheApproveRoutes(Sandbox):
    def test_approve_still_marks_one_shot(self):
        board = self.save(_board("sb_ap1", [
            _shot(1, still="/o/s1.png"), _shot(2, still="/o/s2.png")]))
        h = self.post("approve-still", {"id": ["sb_ap1"], "n": ["1"]})
        self.assertEqual(h.status, 200, h.payload)
        reloaded = self.load("sb_ap1")
        self.assertTrue(reloaded["shots"][0]["still_approved"])
        self.assertNotIn("still_approved", reloaded["shots"][1])

    def test_approve_still_refuses_a_shot_with_no_still_yet(self):
        board = self.save(_board("sb_ap2", [_shot(1)]))
        h = self.post("approve-still", {"id": ["sb_ap2"], "n": ["1"]})
        self.assertEqual(h.status, 400)

    def test_approve_still_404s_for_an_unknown_shot(self):
        board = self.save(_board("sb_ap3", [_shot(1, still="/o/s1.png")]))
        h = self.post("approve-still", {"id": ["sb_ap3"], "n": ["9"]})
        self.assertEqual(h.status, 404)

    def test_approve_all_stills_marks_every_pending_one(self):
        board = self.save(_board("sb_ap4", [
            _shot(1, still="/o/s1.png"),
            _shot(2, still="/o/s2.png", still_approved=True),
            _shot(3),                                     # no still at all
        ]))
        h = self.post("approve-all-stills", {"id": ["sb_ap4"]})
        self.assertEqual(h.status, 200, h.payload)
        self.assertEqual(h.payload["approved"], 1)
        reloaded = self.load("sb_ap4")
        self.assertTrue(reloaded["shots"][0]["still_approved"])
        self.assertTrue(reloaded["shots"][1]["still_approved"])
        self.assertNotIn("still_approved", reloaded["shots"][2])


class NewStillClearsApproval(unittest.TestCase):
    def test_clearing_a_machine_still_drops_its_approval_flag_too(self):
        board = {"shots": [_shot(1, still="/o/s1.png", still_approved=True,
                                 seed=42)]}
        shot = panel._sb_clear_still(board, 1)
        self.assertNotIn("still_approved", shot)
        self.assertNotIn("still", shot)


if __name__ == "__main__":
    unittest.main()
