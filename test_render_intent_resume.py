#!/usr/bin/env python3
"""FILM-17: a panel restart during an overnight board render must not
silently stop it.

`_sb_render_thread` queues one bucket of shots at a time and lives only in
`_SB_RENDERS`, this process's own memory. A restart mid-render used to leave
a board reading "Drafts · 18 of 52" with no thread, no error, and no film —
riding the ordinary queue only covers jobs already queued, nothing was left
to queue the next bucket.

Locked here: every render start writes `board["render_intent"]` before the
thread does anything real; the thread's own `finally` clears it on every
ordinary exit (done, stopped, or errored); and `_sb_boot_reconcile()` — the
one path that runs AFTER a hard kill, the only way `render_intent` can still
be set — either resumes the render (unfinished) or cleans up and runs the
auto-film step it never got to (finished, just not noticed).
"""
from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as panel                                        # noqa: E402
import storyboard                                                    # noqa: E402


def _shot(n, **kw):
    s = {"n": n, "mode": "text", "engine": "ltx", "prompt": f"shot {n} happens",
         "duration_s": 5.0, "seed": 100 + n, "refs": [], "status": "pending",
         "uid": f"shot_{n:08x}"}
    s.update(kw)
    return s


def _board(shots, bid="sb_20231115_d15bad", **kw):
    b = {"schema": 1, "id": bid, "title": "Overnight film", "created_at": 1_700_050_000,
         "policy": storyboard.default_policy(), "cast": [], "engine_mode": "ltx",
         "shots": shots}
    b.update(kw)
    return b


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state"
        self.state.mkdir()
        self.queue_state = {"queue": [], "current": None, "history": []}
        for p in (mock.patch.object(panel, "STATE_DIR", self.state),
                  mock.patch.object(panel, "STATE", self.queue_state),
                  mock.patch.object(panel, "persist_queue", lambda: None),
                  mock.patch.object(panel, "push", lambda *a, **k: None)):
            p.start()
            self.addCleanup(p.stop)
        panel._SB_RENDERS.clear()
        panel._SB_PLANNERS.clear()
        self.addCleanup(panel._SB_RENDERS.clear)
        self.addCleanup(panel._SB_PLANNERS.clear)

    def save(self, board):
        storyboard.save_storyboard(self.state, board)
        return board

    def load(self, bid):
        return storyboard.load_storyboard(self.state, bid)


class RenderIntentBookkeeping(unittest.TestCase):
    def test_set_and_clear(self):
        board = {}
        panel._sb_set_render_intent(board, "draft", [1, 2], auto=True)
        self.assertEqual(board["render_intent"],
                         {"pass": "draft", "only": [1, 2], "auto": True})
        panel._sb_clear_render_intent(board)
        self.assertNotIn("render_intent", board)

    def test_only_none_stays_none(self):
        board = {}
        panel._sb_set_render_intent(board, "final", None)
        self.assertIsNone(board["render_intent"]["only"])
        self.assertFalse(board["render_intent"]["auto"])

    def test_intent_done_checks_the_right_pass_and_scope(self):
        board = _board([_shot(1, draft_output="/o/1.mp4"),
                        _shot(2, draft_output="/o/2.mp4"),
                        _shot(3)])
        self.assertFalse(panel._sb_render_intent_done(
            board, {"pass": "draft", "only": None}))
        self.assertTrue(panel._sb_render_intent_done(
            board, {"pass": "draft", "only": [1, 2]}))
        self.assertFalse(panel._sb_render_intent_done(
            board, {"pass": "final", "only": [1]}))
        # a skipped (cut) shot does not block "done"
        board2 = _board([_shot(1, draft_output="/o/1.mp4"),
                         _shot(2, status="skipped")])
        self.assertTrue(panel._sb_render_intent_done(
            board2, {"pass": "draft", "only": None}))


class TheThreadClearsItsOwnIntent(Sandbox):
    def test_render_intent_is_gone_when_the_thread_finishes(self):
        board = _board([_shot(1)])
        panel._sb_set_render_intent(board, "draft", None)
        self.save(board)
        bid = board["id"]
        panel._SB_RENDERS[bid] = {"stop": False, "queued": 0}
        th = threading.Thread(target=panel._sb_render_thread,
                              args=(bid, "draft", None), daemon=True)
        with mock.patch.object(panel, "_sb_enqueue", return_value="j1"), \
             mock.patch.object(panel, "_sb_job_index",
                               return_value={"j1": {"status": "done",
                                                    "output_path": "/o/1.mp4"}}), \
             mock.patch.object(panel, "_sb_h3_available", return_value=False), \
             mock.patch.object(panel, "h3_supports_chain_prompts", return_value=False), \
             mock.patch.object(panel, "h3_supports_first_frame", return_value=False), \
             mock.patch.object(panel, "_sb_lipsync_gate", lambda *a, **k: None), \
             mock.patch.object(panel, "_sb_sweep_stage_a", lambda *a, **k: None), \
             mock.patch.object(storyboard, "shot_to_job",
                               lambda shot, *a, **k: {"mode": "t2v",
                                                      "prompt": shot["prompt"]}):
            th.start()
            th.join(timeout=10)
        self.assertFalse(th.is_alive())
        self.assertNotIn("render_intent", self.load(bid))

    def test_render_intent_is_gone_after_a_stop(self):
        board = _board([_shot(1), _shot(2)])
        panel._sb_set_render_intent(board, "draft", None)
        self.save(board)
        bid = board["id"]
        panel._SB_RENDERS[bid] = {"stop": True, "queued": 0}   # stopped before it starts
        panel._sb_render_thread(bid, "draft", None)
        self.assertNotIn("render_intent", self.load(bid))


class BootResumesAnInterruptedRender(Sandbox):
    def test_an_unfinished_render_intent_is_resumed(self):
        board = _board([_shot(1, draft_output="/o/1.mp4", status="done"),
                        _shot(2)])                       # shot 2 never queued
        panel._sb_set_render_intent(board, "draft", None, auto=False)
        self.save(board)
        with mock.patch.object(panel.threading, "Thread") as th_cls, \
             mock.patch.object(panel, "_sb_auto_film") as auto_film:
            fake_thread = mock.Mock()
            th_cls.return_value = fake_thread
            panel._sb_boot_reconcile()
        th_cls.assert_called_once()
        _, kw = th_cls.call_args
        self.assertIs(kw["target"], panel._sb_render_thread)
        self.assertEqual(kw["args"], (board["id"], "draft", None))
        fake_thread.start.assert_called_once()
        self.assertIn(board["id"], panel._SB_RENDERS)
        self.assertTrue(panel._SB_RENDERS[board["id"]].get("resumed"))
        auto_film.assert_not_called()
        # the intent stays on disk until the resumed thread's own finally
        # clears it — it is NOT the boot pass's job to clear an unfinished one
        self.assertIn("render_intent", self.load(board["id"]))

    def test_an_already_finished_render_intent_is_cleaned_up_not_resumed(self):
        board = _board([_shot(1, draft_output="/o/1.mp4", status="done"),
                        _shot(2, draft_output="/o/2.mp4", status="done")])
        panel._sb_set_render_intent(board, "draft", None, auto=True)
        self.save(board)
        with mock.patch.object(panel.threading, "Thread") as th_cls, \
             mock.patch.object(panel, "_sb_auto_film") as auto_film:
            panel._sb_boot_reconcile()
        th_cls.assert_not_called()
        auto_film.assert_called_once()
        self.assertNotIn("render_intent", self.load(board["id"]))

    def test_only_is_respected_when_resuming(self):
        board = _board([_shot(1, draft_output="/o/1.mp4", status="done"),
                        _shot(2), _shot(3)])
        panel._sb_set_render_intent(board, "final", [2, 3])
        self.save(board)
        with mock.patch.object(panel.threading, "Thread") as th_cls:
            fake_thread = mock.Mock()
            th_cls.return_value = fake_thread
            panel._sb_boot_reconcile()
        _, kw = th_cls.call_args
        self.assertEqual(kw["args"], (board["id"], "final", [2, 3]))

    def test_a_board_already_claimed_by_a_live_render_or_planner_is_left_alone(self):
        board = _board([_shot(1)])
        panel._sb_set_render_intent(board, "draft", None)
        self.save(board)
        panel._SB_RENDERS[board["id"]] = {"stop": False, "queued": 0}
        with mock.patch.object(panel.threading, "Thread") as th_cls:
            panel._sb_boot_reconcile()
        th_cls.assert_not_called()

    def test_a_board_with_no_render_intent_is_untouched(self):
        board = self.save(_board([_shot(1)]))
        with mock.patch.object(panel.threading, "Thread") as th_cls:
            panel._sb_boot_reconcile()
        th_cls.assert_not_called()
        self.assertEqual(self.load(board["id"]), board)

    def test_a_stuck_planner_state_is_still_healed_as_before(self):
        board = _board([_shot(1)], planner={"state": "running", "stage": "write"})
        self.save(board)
        with mock.patch.object(panel.threading, "Thread") as th_cls:
            panel._sb_boot_reconcile()
        th_cls.assert_not_called()
        reloaded = self.load(board["id"])
        self.assertEqual(reloaded["planner"]["state"], "failed")
        self.assertEqual(reloaded["planner"]["error_kind"], "restarted")


if __name__ == "__main__":
    unittest.main()
