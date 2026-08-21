#!/usr/bin/env python3
"""The auto-captioner must report what it WROTE, not what it was handed.

`caption_with_gemma.py` deliberately does not abort the whole run on one bad
image — it logs an `error` event and moves to the next. That is the right
behaviour and it made the summary a lie: `done` carried `count=len(images)`,
so a dataset where four images failed still printed

    [caption] done - 24 captions in 91.4s

while 20 caption files existed. The four uncaptioned images then trained on
the 3-word `<trigger> man` fallback and nobody was told. Same class as the zip
filter in #61 that accepted 12 of 18 images without a word: a silent loss
under a green summary.

And a run where EVERY image failed still exited 0 and reported `done`, which
is not a finished run by any reading.
"""

import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import caption_with_gemma


class _FakeProcessor:
    pass


class _FakeModel:
    config = object()


def _install_fake_mlx_vlm(fail_on: set[str]) -> dict[str, types.ModuleType]:
    """A stand-in for `mlx_vlm` that fails on the named image basenames."""

    def _load(_path):
        return _FakeModel(), _FakeProcessor()

    def _generate(_model, _processor, _formatted, image, **_kw):
        name = Path(image[0]).name
        if name in fail_on:
            raise RuntimeError("simulated caption failure")
        return f"[VISUAL]: tok, a description of {name}"

    root = types.ModuleType("mlx_vlm")
    root.load = _load
    root.generate = _generate
    prompt_utils = types.ModuleType("mlx_vlm.prompt_utils")
    prompt_utils.apply_chat_template = lambda *a, **k: "PROMPT"
    root.prompt_utils = prompt_utils
    return {"mlx_vlm": root, "mlx_vlm.prompt_utils": prompt_utils}


class CaptionCountTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dataset = Path(self.tmp.name)
        self.images = self.dataset / "images"
        self.images.mkdir()
        for i in range(1, 6):
            (self.images / f"char_{i:03d}.png").write_bytes(b"not really a png")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, fail_on: set[str]) -> tuple[int, list[dict]]:
        stream = io.StringIO()
        argv = [
            "caption_with_gemma",
            "--dataset", str(self.dataset),
            "--trigger", "tok",
            "--gemma-path", str(self.dataset),  # any existing dir passes the check
        ]
        with patch.dict(sys.modules, _install_fake_mlx_vlm(fail_on)), \
                patch.object(sys, "argv", argv), \
                patch.object(sys, "stdout", stream):
            code = caption_with_gemma.main()
        events = [
            json.loads(line)
            for line in stream.getvalue().splitlines()
            if line.strip().startswith("{")
        ]
        return code, events

    def _event(self, events: list[dict], name: str) -> dict | None:
        return next((e for e in events if e.get("event") == name), None)

    def test_a_clean_run_counts_every_image(self) -> None:
        code, events = self._run(fail_on=set())
        done = self._event(events, "done")
        self.assertEqual(code, 0)
        self.assertIsNotNone(done)
        self.assertEqual(done["count"], 5)
        self.assertEqual(done["failed"], 0)
        self.assertEqual(done["total"], 5)
        self.assertEqual(len(list((self.dataset / "captions").glob("*.txt"))), 5)

    def test_the_summary_counts_files_written_not_images_seen(self) -> None:
        code, events = self._run(fail_on={"char_002.png", "char_004.png"})
        done = self._event(events, "done")
        on_disk = len(list((self.dataset / "captions").glob("*.txt")))
        self.assertEqual(code, 0)
        self.assertEqual(on_disk, 3)
        self.assertEqual(done["count"], on_disk)
        self.assertEqual(done["failed"], 2)
        self.assertEqual(done["total"], 5)

    def test_every_image_failing_is_not_a_finished_run(self) -> None:
        names = {f"char_{i:03d}.png" for i in range(1, 6)}
        code, events = self._run(fail_on=names)
        self.assertEqual(code, 1)
        self.assertIsNone(self._event(events, "done"))
        errors = [e for e in events if e.get("event") == "error"]
        # One per image, plus the fatal summary.
        self.assertEqual(len(errors), 6)
        self.assertEqual(errors[-1]["count"], 0)
        self.assertEqual(errors[-1]["failed"], 5)
        self.assertIn("no captions were written", errors[-1]["message"])

    def test_a_failed_image_still_lets_the_others_through(self) -> None:
        _, events = self._run(fail_on={"char_003.png"})
        captioned = {e["file"] for e in events if e.get("event") == "progress"}
        self.assertNotIn("char_003.png", captioned)
        self.assertEqual(len(captioned), 4)


if __name__ == "__main__":
    unittest.main()
