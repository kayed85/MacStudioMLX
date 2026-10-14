#!/usr/bin/env python3
"""FILM-07 — a board re-render reaches the timeline.

Before this: re-rolling a shot already on the timeline (Rewrite, Retry, New
still, "Render remaining") overwrote `draft_output`/`final_output` with no
memory of what it replaced, so a clip on the timeline that named the OLD
path was never told anything had changed — Export shipped the old take, and
the new one sat in the "rendered, not placed" list whose only action was
Place (which appends, FILM-08).

Locked here:
  * `_sb_reconcile` keeps the superseded path in `shots[n]["takes"]` every
    time a re-render changes `draft_output` or `final_output`, capped, and
    never records a no-op (same path twice).
  * `_sbe_relinks` offers the shot's CURRENT output against any timeline
    clip whose path is one of those superseded takes — flagged `retake`,
    the same one-clip-at-a-time shape an actual retake already has, and
    never re-offered once adopted.
  * `_sbe_board_clips` carries what a Retake dialog needs to say what will
    render (FILM-53): `mode`, `audio`, `audio_start_time`, `est_min`.

Run:  ./ltx-2-mlx/env/bin/python3.11 -m pytest -q test_editor_film_takes.py
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
import storyboard_editor as sedit                                    # noqa: E402
from test_storyboard_editor_api import FakeHandler, _board, _clip, _edit  # noqa: E402


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

    def save(self, board):
        storyboard.save_storyboard(self.state, board)
        return board

    def load(self, bid):
        return storyboard.load_storyboard(self.state, bid)


def _reconcile(board, index):
    with mock.patch.object(panel, "_sb_job_index", return_value=index):
        return panel._sb_reconcile(board)


class TheTakeHistory(Sandbox):
    def test_a_rerender_records_the_old_path_in_takes(self):
        board = self.save(_board([Path("/o/s1_v1.mp4")]))
        board["shots"][0]["draft_job_id"] = "j2"
        index = {"j2": {"status": "done", "output_path": "/o/s1_v2.mp4"}}
        self.assertTrue(_reconcile(board, index))
        self.assertEqual(board["shots"][0]["draft_output"], "/o/s1_v2.mp4")
        self.assertEqual(board["shots"][0]["takes"], ["/o/s1_v1.mp4"])

    def test_a_second_rerender_appends_and_never_duplicates(self):
        board = self.save(_board([Path("/o/s1_v1.mp4")]))
        board["shots"][0]["takes"] = ["/o/s1_v0.mp4"]
        board["shots"][0]["draft_job_id"] = "j3"
        index = {"j3": {"status": "done", "output_path": "/o/s1_v2.mp4"}}
        _reconcile(board, index)
        self.assertEqual(board["shots"][0]["takes"], ["/o/s1_v0.mp4", "/o/s1_v1.mp4"])
        # the SAME output landing twice (a stale queue re-poll) is a no-op
        board["shots"][0]["draft_job_id"] = "j3"
        changed = _reconcile(board, index)
        self.assertFalse(changed)
        self.assertEqual(board["shots"][0]["takes"], ["/o/s1_v0.mp4", "/o/s1_v1.mp4"])

    def test_takes_history_is_capped(self):
        board = self.save(_board([Path("/o/s1_v0.mp4")]))
        board["shots"][0]["takes"] = [f"/o/s1_v{i}.mp4" for i in range(1, 20)]
        board["shots"][0]["draft_job_id"] = "jN"
        index = {"jN": {"status": "done", "output_path": "/o/s1_vNEW.mp4"}}
        _reconcile(board, index)
        self.assertLessEqual(len(board["shots"][0]["takes"]), 20)
        self.assertEqual(board["shots"][0]["takes"][-1], "/o/s1_v0.mp4")


class TheRelinkOffer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old = self.root / "s1_v1.mp4"
        self.old.write_bytes(b"x")
        self.new = self.root / "s1_v2.mp4"
        self.new.write_bytes(b"y")
        self.board = _board([self.old])
        self.board["shots"][0]["takes"] = [str(self.old)]
        self.board["shots"][0]["draft_output"] = str(self.new)   # the CURRENT output
        c = _clip(str(self.old), 0.0, 4.0, 0.0, id="c1")
        self.edit = sedit.normalise_edit(_edit([c]))

    def test_a_stale_take_on_the_timeline_is_offered_flagged(self):
        rows = panel._sbe_relinks(self.board, self.edit)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["retake"])
        self.assertEqual([rows[0]["id"], rows[0]["path"], rows[0]["to"]],
                         ["c1", str(self.old), str(self.new)])

    def test_a_clip_already_playing_the_current_take_is_not_offered(self):
        c = _clip(str(self.new), 0.0, 4.0, 0.0, id="c1")
        edit = sedit.normalise_edit(_edit([c]))
        self.assertEqual(panel._sbe_relinks(self.board, edit), [])

    def test_a_take_whose_file_is_gone_is_never_offered(self):
        self.new.unlink()
        self.assertEqual(panel._sbe_relinks(self.board, self.edit), [])

    def test_adopting_it_keeps_film_start_and_the_trim(self):
        bdir = self.root / "sb_t"
        bdir.mkdir()
        sedit.save_edit(bdir, self.edit)
        with mock.patch.object(panel, "STATE_DIR", self.root), \
             mock.patch.object(panel, "_sbe_board_dir", return_value=bdir), \
             mock.patch.object(storyboard, "load_storyboard", return_value=self.board), \
             mock.patch.object(storyboard, "save_storyboard"), \
             mock.patch.object(panel, "_sbe_proxy_now"):
            h = FakeHandler()
            h.post("edit/relink", {"id": "sb_t", "only": "c1"})
            self.assertEqual(h.status, 200, h.payload)
            got = sedit.load_edit(bdir)["clips"][0]
            self.assertEqual(got["path"], str(self.new))
            self.assertEqual([got["start"], got["end"], got["film_start"]], [0.0, 4.0, 0.0])
            # adopted — no longer offered
            self.assertEqual(panel._sbe_relinks(self.board, sedit.load_edit(bdir)), [])


class TheBoardClipsCarryRetakeContext(unittest.TestCase):
    def test_mode_audio_and_estimate_ride_along(self):
        p = Path(tempfile.mkdtemp()) / "s1.mp4"
        p.write_bytes(b"x")
        board = _board([p])
        board["shots"][0].update({"mode": "a2v", "audio": "/x/song.wav",
                                  "audio_start_time": 12.5, "duration_s": 4.0})
        rows = panel._sbe_board_clips(board)
        self.assertEqual(rows[0]["mode"], "a2v")
        self.assertEqual(rows[0]["audio"], "/x/song.wav")
        self.assertEqual(rows[0]["audio_start_time"], 12.5)
        self.assertIn("draft", rows[0]["est_min"])
        self.assertIn("final", rows[0]["est_min"])
        self.assertGreaterEqual(rows[0]["est_min"]["draft"], 0)


if __name__ == "__main__":
    unittest.main()
