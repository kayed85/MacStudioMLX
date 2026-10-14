"""The H3 runner's mux WAVs do not pile up in the gallery (4.19.0).

minimax_h3_mlx/media.py writes the soundtrack to <out>.wav (and
<out>_source.wav when it time-stretches) only to mux it, and never removes
it, so every H3 render left a stray WAV in OUTPUT that the gallery and the
Editor list as a separate audio output (188 on one install). The panel now
removes the ones THIS render wrote - same stem, written since the job started
- after the runner exits, keeping <out>.wav for Extend's splice until it is
done. A user's own older WAV with the same name and the #48 FLAC masters are
never touched.
"""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

import mlx_ltx_panel as P

ROOT = Path(__file__).resolve().parent


class DropRunnerWavs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.out = self.d / "a_scene_h3.mp4"
        self.out.write_bytes(b"mp4")

    def tearDown(self):
        self.tmp.cleanup()

    def test_removes_both_wavs_this_render_wrote(self):
        t0 = time.time()
        for n in ("a_scene_h3.wav", "a_scene_h3_source.wav"):
            (self.d / n).write_bytes(b"RIFF")
        removed = P._h3_drop_runner_wavs(self.out, t0)
        self.assertEqual(sorted(removed), ["a_scene_h3.wav", "a_scene_h3_source.wav"])
        self.assertTrue(self.out.exists())
        self.assertEqual(sorted(p.name for p in self.d.iterdir()), ["a_scene_h3.mp4"])

    def test_extend_keeps_the_main_wav_until_its_splice(self):
        t0 = time.time()
        (self.d / "a_scene_h3.wav").write_bytes(b"RIFF")
        P._h3_drop_runner_wavs(self.out, t0, keep_main=True)
        self.assertTrue((self.d / "a_scene_h3.wav").exists())

    def test_an_older_wav_with_the_same_name_is_never_removed(self):
        w = self.d / "a_scene_h3.wav"
        w.write_bytes(b"users own")
        old = time.time() - 3600
        os.utime(w, (old, old))
        self.assertEqual(P._h3_drop_runner_wavs(self.out, time.time()), [])
        self.assertTrue(w.exists())

    def test_other_files_and_extend_masters_are_never_matched(self):
        t0 = time.time()
        masters = self.d / ".extend" / "masters"
        masters.mkdir(parents=True)
        (masters / "a_scene_h3.flac").write_bytes(b"fLaC")
        (self.d / "a_scene_h3_2.wav").write_bytes(b"RIFF")
        (self.d / "song.wav").write_bytes(b"RIFF")
        self.assertEqual(P._h3_drop_runner_wavs(self.out, t0), [])
        self.assertTrue((masters / "a_scene_h3.flac").exists())


class WiredIntoTheH3Job(unittest.TestCase):
    def test_the_job_cleans_up_after_every_exit_and_after_extend(self):
        src = (ROOT / "mlx_ltx_panel.py").read_text()
        body = src[src.index("def run_h3_job_inner("):]
        body = body[: body.index("\ndef ", 10)]
        fin = body[body.index("    finally:"):body.index("if not out_path.is_file():")]
        self.assertIn('_h3_drop_runner_wavs(out_path, t0, keep_main=(mode == "extend"))', fin)
        after = body[body.index("generated_lossless=h3_extend_lossless)"):body.index('if mode == "v2a":')]
        self.assertIn("_h3_drop_runner_wavs(out_path, t0)", after)


if __name__ == "__main__":
    unittest.main()
