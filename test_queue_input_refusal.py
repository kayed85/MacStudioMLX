"""4.17.5 (fleet): a job that can only fail for want of its input is refused
at the queue, in the form's own words - never queued to fail as a red card.

THE FIELD SHAPE (4.17.4, one install, all source "form"): Upscale & Face Fix
with source '' (2), Control with a control video of '' (2), Image mode with no
picture (2, and once i2v_clean_audio), an a2v whose audio was not on disk, and
one job in mode "retain" - no surface offers that mode; it travelled all the
way to the helper and failed as "unsupported mode: retain". The page's own
submit guards cover some of these, but /queue/add is also the API, and a
stale page or a script never runs those guards. /queue/retry copies params
verbatim, so a Retry of such a job failed the same way.
"""
from __future__ import annotations

import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")

import mlx_ltx_panel as P                                            # noqa: E402

routes_queue = sys.modules["panel.routes_queue"]


class _H:
    def __init__(self, form=None):
        self.status, self.payload = None, None
        self._form = {k: [v] for k, v in (form or {}).items()}

    def _read_form_body(self):
        return b"", self._form

    def _json(self, payload, status=200):
        self.payload, self.status = payload, status


class _QueueTest(unittest.TestCase):
    def setUp(self):
        with P.LOCK:
            self.saved = {k: copy.deepcopy(P.STATE.get(k))
                          for k in ("queue", "history", "current")}
            P.STATE["queue"], P.STATE["history"], P.STATE["current"] = [], [], None
        self._p = mock.patch.object(P, "persist_queue", lambda: None)
        self._p.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.clip = Path(self.tmp.name) / "clip.mp4"
        self.clip.write_bytes(b"\0" * 64)
        self.pic = Path(self.tmp.name) / "pic.png"
        self.pic.write_bytes(b"\0" * 64)

    def tearDown(self):
        self._p.stop()
        with P.LOCK:
            for k, v in self.saved.items():
                P.STATE[k] = v
        self.tmp.cleanup()

    def add(self, **form) -> _H:
        h = _H(form)
        routes_queue.post_run(h, "/queue/add", {}, "")
        return h


class TheQueueRefusesEmptyInputs(_QueueTest):
    def assertRefused(self, h, *words):
        self.assertEqual(h.status, 400, h.payload)
        self.assertEqual(h.payload.get("code"), "input_missing", h.payload)
        for w in words:
            self.assertIn(w, h.payload["error"])
        self.assertEqual(P.STATE["queue"], [], "nothing may be queued")

    def test_upscale_with_no_clip(self):
        self.assertRefused(self.add(mode="upscale", upscale_source_path=""),
                           "Pick the clip to fix first")

    def test_upscale_with_a_clip_that_is_gone(self):
        self.assertRefused(self.add(mode="upscale",
                                    upscale_source_path=str(self.clip) + ".gone"),
                           "not found")

    def test_control_with_no_clip(self):
        self.assertRefused(self.add(mode="control", control_video_path=""),
                           "Control needs a clip to follow")

    def test_image_mode_with_no_picture(self):
        self.assertRefused(self.add(mode="i2v", prompt="x"),
                           "Image mode needs a reference image")

    def test_image_with_clean_audio_and_no_picture(self):
        self.assertRefused(self.add(mode="i2v_clean_audio", prompt="x"),
                           "Image mode needs a reference image")

    def test_lipsync_with_no_audio(self):
        self.assertRefused(self.add(mode="a2v", prompt="x", image=str(self.pic)),
                           "Lip-sync needs a song")

    def test_an_unknown_mode(self):
        self.assertRefused(self.add(mode="retain", prompt="x"),
                           "Unknown render mode 'retain'")

    def test_jobs_with_their_inputs_still_queue(self):
        for form in ({"mode": "t2v", "prompt": "a lighthouse"},
                     {"mode": "i2v", "prompt": "x", "image": str(self.pic)},
                     {"mode": "upscale", "upscale_source_path": str(self.clip)},
                     {"mode": "control", "prompt": "x",
                      "control_video_path": str(self.clip)}):
            with self.subTest(form=form):
                h = self.add(**form)
                self.assertNotEqual(h.payload.get("code"), "input_missing", h.payload)


class RetryRefusesTheSameWay(_QueueTest):
    def test_retry_of_an_input_less_job_is_refused(self):
        src = {"id": "old1", "status": "failed",
               "params": {"mode": "upscale", "upscale_source_path": "",
                          "quality": "balanced"}}
        P.STATE["history"].append(src)
        h = _H({"id": "old1"})
        routes_queue.post_queue_retry(h, "/queue/retry", {}, "")
        self.assertEqual(h.status, 400, h.payload)
        self.assertEqual(h.payload.get("code"), "input_missing")
        self.assertEqual(P.STATE["queue"], [])


class RetrySmallerShortensAnExtend(_QueueTest):
    def test_extend_frames_are_halved(self):
        """Codex 4.17.5: Extend renders extend_frames, not frames - "smaller"
        must shrink what that pipeline consumes."""
        P.STATE["history"].append({"id": "ext1", "status": "failed", "params": {
            "mode": "extend", "video_path": str(self.clip), "extend_frames": 6,
            "quality": "standard", "width": 768, "height": 448, "frames": 121}})
        h = _H({"id": "ext1", "smaller": "1"})
        routes_queue.post_queue_retry(h, "/queue/retry", {}, "")
        self.assertEqual(h.status, 200, h.payload)
        self.assertEqual(P.STATE["queue"][-1]["params"]["extend_frames"], 3)


class ThePureCheck(unittest.TestCase):
    def test_music_image_and_train_are_not_judged_here(self):
        for mode in ("music", "image", "train"):
            self.assertIsNone(P.job_input_refusal({"mode": mode}))

    def test_every_dispatched_video_mode_is_known(self):
        """The set must cover every mode run_job_inner dispatches, or a real
        mode would be refused as unknown."""
        for mode in ("t2v", "i2v", "i2v_clean_audio", "extend", "keyframe",
                     "a2v", "retake", "restore", "ingredients", "control",
                     "upscale", "sharp_export"):
            self.assertIn(mode, P.QUEUEABLE_VIDEO_MODES)

    def test_the_unpicked_audio_placeholder_reads_as_not_picked(self):
        msg = P.job_input_refusal({"mode": "a2v", "audio": str(P.AUDIO_DEFAULT)})
        if not P.AUDIO_DEFAULT.exists():
            self.assertEqual(msg, P.INPUT_MISSING_AUDIO)


if __name__ == "__main__":
    unittest.main(verbosity=2)
