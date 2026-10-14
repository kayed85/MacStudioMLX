#!/usr/bin/env python3
"""4.17 integration leftovers — things only possible once the editor-a,
editor-b, board and music packages were combined (coordinator follow-up).

1. The queued film render (FILM-28's job) honours the sequence aspect
   (FILM-58, edit.settings.aspect).
2. Cancel on that render really stops ffmpeg: every ffmpeg the job starts is
   its own process group recorded ON THE JOB, never in STATE["mux_pgid"] (the
   generation queue's slot), and the cancel route kills it.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
import unittest
from pathlib import Path
from unittest import mock

import mlx_ltx_panel as panel
import storyboard_editor as sedit
from test_render_job import Sandbox, _board

FFPROBE = shutil.which("ffprobe")


def _real_clip(path: Path, w=640, h=360, secs=1) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"testsrc2=s={w}x{h}:d={secs}", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(path)], check=True)


def _dims(path) -> tuple[int, int]:
    out = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height", "-of", "json",
                          str(path)], capture_output=True, text=True, check=True).stdout
    st = json.loads(out)["streams"][0]
    return int(st["width"]), int(st["height"])


class TheFilmJobHonoursTheSequenceAspect(Sandbox):
    def setUp(self):
        super().setUp()
        edit = sedit.load_edit(self.bdir)
        edit["settings"] = {"aspect": "9:16"}
        sedit.save_edit(self.bdir, edit)

        def fake_assemble(cmd, label):          # a real 16:9 film on disk
            _real_clip(Path(cmd[-1]))
        p = mock.patch.object(panel, "run_ffmpeg_tracked", side_effect=fake_assemble)
        p.start()
        self.addCleanup(p.stop)

    @unittest.skipUnless(FFPROBE, "ffprobe not on PATH")
    def test_the_job_delivers_a_9_16_film(self):
        h = self._post("edit/render/start", {"id": "sb_t"})
        job = h.payload["job"]
        self.assertTrue(self._wait_until(
            lambda: self._get(f"/storyboard/edit/render/status?job={job}")
                        .payload["state"] != "running", timeout=60))
        s = self._get(f"/storyboard/edit/render/status?job={job}").payload
        self.assertEqual(s["state"], "done", s)
        res = s["result"]
        self.assertEqual(res.get("aspect"), "9:16", res)
        w, h_ = _dims(res["path"])
        self.assertAlmostEqual(w / h_, 9 / 16, delta=0.02)


class CancelStopsTheEncode(Sandbox):
    def setUp(self):
        super().setUp()
        real_postprocess = panel.run_postprocess_tracked
        self.seen_mux = []

        def slow_real_ffmpeg(cmd, label):
            # A REAL ffmpeg that would run for minutes, through the panel's
            # own tracked runner — the path the assembler really takes.
            long_cmd = ["ffmpeg", "-y", "-loglevel", "error", "-re", "-f", "lavfi",
                        "-i", "testsrc2=s=320x180:d=300", "-f", "null", "-"]
            return real_postprocess(long_cmd, label)
        p = mock.patch.object(panel, "run_ffmpeg_tracked", side_effect=slow_real_ffmpeg)
        p.start()
        self.addCleanup(p.stop)

    def test_cancel_kills_the_running_ffmpeg_and_leaves_the_queue_slot_alone(self):
        before_mux = panel.STATE.get("mux_pgid")
        h = self._post("edit/render/start", {"id": "sb_t"})
        job = h.payload["job"]
        # wait until the job has an ffmpeg running
        self.assertTrue(self._wait_until(
            lambda: bool(panel._SB_FILM_JOBS[job].get("pgids")), timeout=10))
        pg = panel._SB_FILM_JOBS[job]["pgids"][0]
        self.assertEqual(panel.STATE.get("mux_pgid"), before_mux)   # queue slot untouched
        t0 = time.time()
        c = self._post("edit/render/cancel", {"job": job})
        self.assertTrue(c.payload["ok"])
        self.assertTrue(self._wait_until(
            lambda: self._get(f"/storyboard/edit/render/status?job={job}")
                        .payload["state"] != "running", timeout=10))
        self.assertLess(time.time() - t0, 10)
        self.assertEqual(
            self._get(f"/storyboard/edit/render/status?job={job}").payload["state"],
            "canceled")
        # the process group is gone
        with self.assertRaises(ProcessLookupError):
            import os
            os.killpg(pg, 0)


if __name__ == "__main__":
    unittest.main()
