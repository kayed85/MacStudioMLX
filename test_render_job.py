#!/usr/bin/env python3
"""FILM-28: the Editor's Render, as a job — progress and Cancel.

Before this, `/storyboard/edit/render` ran the whole ffmpeg pass on the
HTTP handler thread: the request did not answer until the encode finished,
with no way to show progress and no way to change your mind partway
through. `edit/render/start` is a deliberate SECOND door beside the
original `edit/render` — which stays exactly as it always was, still
synchronous, for any caller that has not moved (a curl, a script, an
agent) — that answers immediately with a job id; `edit/render/status`
(GET) polls it; `edit/render/cancel` asks it to stop.

Runs against a scratch OUTPUT/STATE_DIR with ffmpeg mocked (real threading,
real file writes, real route dispatch — only the encoder itself is a
stand-in), the same sandbox test_storyboard_film_integrity.py already uses.
"""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as panel                                        # noqa: E402
import storyboard                                                    # noqa: E402
import storyboard_editor as sedit                                    # noqa: E402


def _board(bid, title="Night Drive"):
    return {"schema": 1, "id": bid, "title": title, "created_at": 1_700_000_000,
            "policy": storyboard.default_policy(), "cast": [],
            "engine_mode": "ltx", "shots": []}


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
        # Every job registry starts empty and clean per test — the module
        # level dict would otherwise carry jobs between tests in the same
        # process.
        panel._SB_FILM_JOBS.clear()
        self.clip = root / "a.mp4"
        self.clip.write_bytes(b"clip")
        board = _board("sb_t")
        storyboard.save_storyboard(self.state, board)
        self.bdir = storyboard.board_dir(self.state, "sb_t")
        edit = {"version": sedit.EDIT_VERSION, "board_id": "sb_t", "revision": 0,
                "source": "human", "audio": None, "beats": None, "settings": {},
                "clips": [sedit.new_clip(str(self.clip), 0.0, 2.0, 0.0,
                                         source="human", duration=2.0)]}
        sedit.save_edit(self.bdir, edit)
        self._probe = mock.patch.object(
            panel, "_sb_probe_clip",
            return_value={"has_audio": False, "duration": 2.0, "w": 640,
                         "h": 360, "sample_rate": 0})
        self._probe.start()
        self.addCleanup(self._probe.stop)

    def _post(self, action, form):
        from test_storyboard_editor_api import FakeHandler
        return FakeHandler().post(action, form)

    def _get(self, url):
        from test_storyboard_editor_api import FakeHandler
        return FakeHandler().get(url)

    def _wait_until(self, cond, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if cond():
                return True
            time.sleep(0.02)
        return False


class ASlowRender(Sandbox):
    """ffmpeg takes a moment — long enough to observe "running" for real."""

    def setUp(self):
        super().setUp()
        def fake_ffmpeg(cmd, label):
            time.sleep(0.25)
            Path(cmd[-1]).write_bytes(b"film")
        self._ffmpeg = mock.patch.object(panel, "run_ffmpeg_tracked",
                                         side_effect=fake_ffmpeg)
        self._ffmpeg.start()
        self.addCleanup(self._ffmpeg.stop)

    def test_start_answers_immediately_with_a_job_id(self):
        t0 = time.time()
        h = self._post("edit/render/start", {"id": "sb_t"})
        elapsed = time.time() - t0
        self.assertTrue(h.payload.get("ok"), h.payload)
        self.assertIn("job", h.payload)
        # The whole point: the HTTP response does not wait for the encode
        # (0.25s in this fixture) to finish.
        self.assertLess(elapsed, 0.2)

    def test_status_reports_running_then_done(self):
        h = self._post("edit/render/start", {"id": "sb_t"})
        job = h.payload["job"]
        s1 = self._get(f"/storyboard/edit/render/status?job={job}")
        self.assertEqual(s1.payload["state"], "running")
        self.assertNotIn("result", s1.payload)
        self.assertTrue(self._wait_until(
            lambda: self._get(f"/storyboard/edit/render/status?job={job}")
                        .payload["state"] != "running"))
        s2 = self._get(f"/storyboard/edit/render/status?job={job}")
        self.assertEqual(s2.payload["state"], "done")
        self.assertTrue(s2.payload["result"]["ok"])
        self.assertIn("path", s2.payload["result"])
        self.assertGreaterEqual(s2.payload["elapsed_sec"], 0)

    def test_an_unknown_job_is_404_on_status_and_cancel(self):
        s = self._get("/storyboard/edit/render/status?job=nope")
        self.assertEqual(s.status, 404)
        c = self._post("edit/render/cancel", {"job": "nope"})
        self.assertEqual(c.status, 404)

    def test_cancel_while_running_discards_the_file_and_the_result(self):
        h = self._post("edit/render/start", {"id": "sb_t"})
        job = h.payload["job"]
        c = self._post("edit/render/cancel", {"job": job})
        self.assertTrue(c.payload["ok"])
        self.assertFalse(c.payload["already_done"])
        self.assertTrue(self._wait_until(
            lambda: self._get(f"/storyboard/edit/render/status?job={job}")
                        .payload["state"] != "running"))
        s = self._get(f"/storyboard/edit/render/status?job={job}")
        self.assertEqual(s.payload["state"], "canceled")
        # The file the encoder wrote must not be left behind for a render
        # nobody asked to keep.
        night_dir = panel._sb_film_dir(_board("sb_t"))
        leftover = [p for p in night_dir.glob("*.mp4")] if night_dir.is_dir() else []
        self.assertEqual(leftover, [])

    def test_cancel_after_it_already_finished_is_too_late_and_harmless(self):
        h = self._post("edit/render/start", {"id": "sb_t"})
        job = h.payload["job"]
        self.assertTrue(self._wait_until(
            lambda: self._get(f"/storyboard/edit/render/status?job={job}")
                        .payload["state"] != "running"))
        c = self._post("edit/render/cancel", {"job": job})
        self.assertTrue(c.payload["ok"])
        self.assertTrue(c.payload["already_done"])
        s = self._get(f"/storyboard/edit/render/status?job={job}")
        self.assertEqual(s.payload["state"], "done")   # the real result stands
        self.assertTrue(s.payload["result"]["ok"])


class AFastRender(Sandbox):
    """The plain synchronous door is untouched — same behaviour as always."""

    def setUp(self):
        super().setUp()
        def fake_ffmpeg(cmd, label):
            Path(cmd[-1]).write_bytes(b"film")
        self._ffmpeg = mock.patch.object(panel, "run_ffmpeg_tracked",
                                         side_effect=fake_ffmpeg)
        self._ffmpeg.start()
        self.addCleanup(self._ffmpeg.stop)

    def test_the_original_route_still_answers_with_the_finished_film(self):
        h = self._post("edit/render", {"id": "sb_t"})
        self.assertTrue(h.payload.get("ok"), h.payload)
        self.assertIn("path", h.payload)
        self.assertNotIn("job", h.payload)

    def test_a_failed_encode_is_filed_as_an_error_job(self):
        def dead(cmd, label):
            raise RuntimeError("disk is full")
        with mock.patch.object(panel, "run_ffmpeg_tracked", side_effect=dead):
            h = self._post("edit/render/start", {"id": "sb_t"})
            job = h.payload["job"]
            self.assertTrue(self._wait_until(
                lambda: self._get(f"/storyboard/edit/render/status?job={job}")
                            .payload["state"] != "running"))
            s = self._get(f"/storyboard/edit/render/status?job={job}")
            self.assertEqual(s.payload["state"], "error")
            self.assertIn("disk is full", s.payload["error"])
            self.assertNotIn("result", s.payload)


class TheJobFunctionDirectly(unittest.TestCase):
    """_sb_film_job_run in isolation — no thread, no HTTP, just the filing
    logic three ways: success, failure, cancel-while-running."""

    def test_success_files_a_done_job(self):
        panel._SB_FILM_JOBS.clear()
        panel._SB_FILM_JOBS["j1"] = {"board_id": "b", "state": "running",
                                     "started_at": time.time(), "cancel": False,
                                     "result": None, "error": None}
        with mock.patch.object(panel, "_sbe_render_edit",
                               return_value={"ok": True, "path": "/x/f.mp4"}):
            panel._sb_film_job_run("j1", {}, {}, music=None, music_mode=None,
                                   out_name="f.mp4", deliver={})
        job = panel._SB_FILM_JOBS["j1"]
        self.assertEqual(job["state"], "done")
        self.assertEqual(job["result"]["path"], "/x/f.mp4")
        self.assertIsNone(job["error"])

    def test_failure_files_an_error_job(self):
        panel._SB_FILM_JOBS.clear()
        panel._SB_FILM_JOBS["j1"] = {"board_id": "b", "state": "running",
                                     "started_at": time.time(), "cancel": False,
                                     "result": None, "error": None}
        with mock.patch.object(panel, "_sbe_render_edit",
                               return_value={"ok": False, "error": "boom"}):
            panel._sb_film_job_run("j1", {}, {}, music=None, music_mode=None,
                                   out_name="f.mp4", deliver={})
        job = panel._SB_FILM_JOBS["j1"]
        self.assertEqual(job["state"], "error")
        self.assertEqual(job["error"], "boom")

    def test_an_exception_from_the_assembler_is_filed_not_raised(self):
        panel._SB_FILM_JOBS.clear()
        panel._SB_FILM_JOBS["j1"] = {"board_id": "b", "state": "running",
                                     "started_at": time.time(), "cancel": False,
                                     "result": None, "error": None}
        with mock.patch.object(panel, "_sbe_render_edit",
                               side_effect=RuntimeError("kaboom")):
            panel._sb_film_job_run("j1", {}, {}, music=None, music_mode=None,
                                   out_name="f.mp4", deliver={})
        job = panel._SB_FILM_JOBS["j1"]
        self.assertEqual(job["state"], "error")
        self.assertIn("kaboom", job["error"])

    def test_cancel_deletes_a_successfully_written_file(self):
        with tempfile.TemporaryDirectory() as d:
            out_path = Path(d) / "f.mp4"
            out_path.write_bytes(b"film")
            panel._SB_FILM_JOBS.clear()
            panel._SB_FILM_JOBS["j1"] = {"board_id": "b", "state": "running",
                                         "started_at": time.time(), "cancel": True,
                                         "result": None, "error": None}
            with mock.patch.object(panel, "_sbe_render_edit",
                                   return_value={"ok": True, "path": str(out_path)}):
                panel._sb_film_job_run("j1", {}, {}, music=None, music_mode=None,
                                       out_name="f.mp4", deliver={})
            job = panel._SB_FILM_JOBS["j1"]
            self.assertEqual(job["state"], "canceled")
            self.assertFalse(out_path.exists())

    def test_a_job_that_has_since_vanished_is_a_no_op(self):
        # The registry can be swept between a job starting and finishing in
        # a very long-idle process — filing into nothing must not raise.
        panel._SB_FILM_JOBS.clear()
        with mock.patch.object(panel, "_sbe_render_edit",
                               return_value={"ok": True, "path": "/x/f.mp4"}):
            panel._sb_film_job_run("ghost", {}, {}, music=None, music_mode=None,
                                   out_name="f.mp4", deliver={})   # must not raise
        self.assertEqual(panel._SB_FILM_JOBS, {})


if __name__ == "__main__":
    unittest.main()
