"""Two fleet failures after 4.16.x, reproduced and pinned (2026-09-28).

1. A LoRA whose download was cut short. Its safetensors HEADER is intact (it
   is the first bytes of the file), so every key check passed, and the
   strength probe then read short and died with numpy's
   "cannot reshape array of size 57408 into shape (32,2048)" — 14 failed
   renders on one install, 4.16.1/4.16.2, naming no file. The test below
   builds a rank-32 adapter and cuts exactly that many bytes off: the old code
   raises that exact sentence; the new one names the file and says why.

2. An ffmpeg without the libx264 encoder. Every export and the engine's own
   writer pass `-c:v libx264`; a minimal ffmpeg build runs fine and fails all
   of them with "Unknown encoder 'libx264'" at the END of a render. One
   4.16.1 install (M4 Max) never finished a render. The resolver now skips an
   ffmpeg that lists its encoders without libx264.
"""
from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = Path(tempfile.mkdtemp(prefix="phos-4163-"))
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

import lora_compat as L  # noqa: E402
import mlx_ltx_panel as P  # noqa: E402

MODULE = "transformer_blocks.0.attn1.to_q"


def _write_lora(path: Path, *, a_shape=(32, 2048), b_shape=(2048, 32)) -> None:
    rng = np.random.default_rng(0)
    tensors = {MODULE + ".lora_B.weight": rng.standard_normal(b_shape) * 0.01,
               MODULE + ".lora_A.weight": rng.standard_normal(a_shape) * 0.01}
    header, blobs, off = {}, [], 0
    for key, arr in tensors.items():
        raw = arr.astype(np.float16).tobytes()
        header[key] = {"dtype": "F16", "shape": list(arr.shape),
                       "data_offsets": [off, off + len(raw)]}
        off += len(raw)
        blobs.append(raw)
    h = json.dumps(header).encode()
    h += b" " * ((8 - len(h) % 8) % 8)
    path.write_bytes(struct.pack("<Q", len(h)) + h + b"".join(blobs))


class TruncatedLora(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="phos-lora-"))
        self.path = self.dir / "style.safetensors"
        _write_lora(self.path)
        # The fleet's exact numbers: 57408 of 65536 F16 values in lora_A.
        with open(self.path, "r+b") as fh:
            fh.truncate(self.path.stat().st_size - (65536 - 57408) * 2)
        L._tensor_header_cached.cache_clear()

    def test_the_header_read_names_the_file_and_the_cause(self):
        with self.assertRaises(L.LoraCompatibilityError) as ctx:
            L.read_tensor_header(self.path)
        msg = str(ctx.exception)
        self.assertIn("style.safetensors", msg)
        self.assertIn("incomplete", msg)
        self.assertIn("download it again", msg)
        self.assertNotIn("cannot reshape", msg)

    def test_the_render_preflight_refuses_with_that_sentence(self):
        for call in (lambda: L.measure_adapter_effect(self.path),
                     lambda: L.validate_adapter_effects([(str(self.path), 1.0)])):
            with self.assertRaises(L.LoraCompatibilityError) as ctx:
                call()
            self.assertIn("incomplete", str(ctx.exception))

    def test_a_whole_file_still_measures(self):
        whole = self.dir / "whole.safetensors"
        _write_lora(whole)
        effect = L.measure_adapter_effect(whole)
        self.assertIsNotNone(effect)
        self.assertEqual(effect.modules, 1)

    def test_a_header_that_lies_about_a_shape_is_named_too(self):
        # Complete file, but one tensor's shape disagrees with its bytes.
        lying = self.dir / "lying.safetensors"
        _write_lora(lying)
        data = lying.read_bytes()
        n = struct.unpack("<Q", data[:8])[0]
        header = json.loads(data[8:8 + n])
        header[MODULE + ".lora_A.weight"]["shape"] = [32, 2100]
        h = json.dumps(header).encode()
        h += b" " * ((8 - len(h) % 8) % 8)
        lying.write_bytes(struct.pack("<Q", len(h)) + h + data[8 + n:])
        L._tensor_header_cached.cache_clear()
        with self.assertRaises(L.LoraCompatibilityError) as ctx:
            L.measure_adapter_effect(lying)
        self.assertIn("lying.safetensors", str(ctx.exception))
        self.assertIn("damaged", str(ctx.exception))

    def test_the_picker_marks_it_unavailable_instead_of_crashing(self):
        # _ltx_lora_compatibility already catches LoraCompatibilityError; the
        # truncated file now lands there with its reason instead of passing.
        src = (ROOT / "mlx_ltx_panel.py").read_text()
        block = src.split("def _ltx_lora_compatibility")[1][:2000]
        self.assertIn("except (LoraCompatibilityError, OSError, ValueError)", block)


def _fake_ffmpeg(dirpath: Path, with_x264: bool) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    exe = dirpath / "ffmpeg"
    enc = (" V....D libx264              libx264 H.264 / AVC\n" if with_x264 else "")
    exe.write_text("#!/bin/sh\n"
                   "if [ \"$2\" = \"-encoders\" ]; then\n"
                   f"  printf 'Encoders:\\n V....D mpeg4   MPEG-4 part 2\\n{enc}'\n"
                   "  exit 0\nfi\nexit 0\n")
    exe.chmod(0o755)
    return exe


class FfmpegWithoutLibx264(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="phos-ff-"))
        self.bad = _fake_ffmpeg(self.tmp / "bad", with_x264=False)
        self.good = _fake_ffmpeg(self.tmp / "good", with_x264=True)
        self._cands = P._ffmpeg_candidates
        self._note = P.FFMPEG_NOTE

    def tearDown(self):
        P._ffmpeg_candidates = self._cands
        P.FFMPEG_NOTE = self._note

    def test_the_probe_reads_the_encoder_list(self):
        self.assertIs(P._ffmpeg_has_libx264(self.bad), False)
        self.assertIs(P._ffmpeg_has_libx264(self.good), True)
        self.assertIsNone(P._ffmpeg_has_libx264(self.tmp / "missing"))

    def test_a_build_without_libx264_is_skipped(self):
        P._ffmpeg_candidates = lambda: [self.bad, self.good]
        P.FFMPEG_NOTE = None
        self.assertEqual(P._resolve_ffmpeg(), self.good)
        self.assertIn("no libx264", P.FFMPEG_NOTE)

    def test_the_first_good_one_wins_silently(self):
        P._ffmpeg_candidates = lambda: [self.good, self.bad]
        P.FFMPEG_NOTE = None
        self.assertEqual(P._resolve_ffmpeg(), self.good)
        self.assertIsNone(P.FFMPEG_NOTE)

    def test_none_good_keeps_the_first_and_says_so(self):
        P._ffmpeg_candidates = lambda: [self.bad]
        P.FFMPEG_NOTE = None
        self.assertEqual(P._resolve_ffmpeg(), self.bad)
        self.assertIn("Unknown encoder 'libx264'", P.FFMPEG_NOTE)

    def test_candidates_follow_the_old_order_override_first(self):
        old = os.environ.get("LTX_FFMPEG")
        os.environ["LTX_FFMPEG"] = str(self.good)
        try:
            self.assertEqual(P._ffmpeg_candidates()[0], self.good)
        finally:
            if old is None:
                os.environ.pop("LTX_FFMPEG", None)
            else:
                os.environ["LTX_FFMPEG"] = old

    def test_this_machine_resolved_an_ffmpeg_that_encodes_h264(self):
        if P.FFMPEG.exists() and P._ffmpeg_has_libx264(P.FFMPEG) is not None:
            self.assertTrue(P._ffmpeg_has_libx264(P.FFMPEG))


if __name__ == "__main__":
    unittest.main()
