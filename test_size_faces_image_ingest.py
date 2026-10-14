"""SYS-01/SYS-02: EXIF-rotated JPEGs and HEIC/HEIF/AVIF used to reach the
render pipeline, the trainer and the thumbnail cache un-normalized — an
EXIF-rotated phone photo rendered sideways while its own preview looked
upright, and a HEIC upload left a blank picker with no message and later
died deep in the helper. normalize_ingested_image_bytes() is the one choke
point both /upload and /train/upload now funnel through.
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SANDBOX = Path(tempfile.mkdtemp(prefix="phos-test-ingest-"))
for _var, _sub in (("LTX_STATE_DIR", "state"), ("LTX_OUTPUT_DIR", "mlx_outputs"),
                   ("LTX_UPLOADS_DIR", "panel_uploads")):
    os.environ.setdefault(_var, str(SANDBOX / _sub))
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")
os.environ.setdefault("PHOSPHENE_DISABLE_VERSION_CHECK", "1")
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

import mlx_ltx_panel as P  # noqa: E402


def _gradient_jpeg_bytes(w: int, h: int, orientation: int | None = None) -> bytes:
    im = Image.new("RGB", (w, h))
    px = im.load()
    for y in range(h):
        v = int(255 * y / max(1, h - 1))
        for x in range(w):
            px[x, y] = (v, v, v)
    buf = io.BytesIO()
    if orientation is not None:
        exif = im.getexif()
        exif[0x0112] = orientation
        im.save(buf, format="JPEG", exif=exif)
    else:
        im.save(buf, format="JPEG")
    return buf.getvalue()


class ExifOrientation(unittest.TestCase):
    def test_sideways_jpeg_comes_back_upright_and_untagged(self):
        # Pixels landscape, EXIF says "rotate 90 CW to display" — the
        # classic iPhone-portrait shape.
        data = _gradient_jpeg_bytes(1600, 900, orientation=6)
        out, ext, err = P.normalize_ingested_image_bytes(data, "IMG_0001.jpg")
        self.assertIsNone(err)
        self.assertEqual(ext, ".jpg")
        with Image.open(io.BytesIO(out)) as im:
            # Now genuinely portrait — the rotation was BAKED into pixels,
            # not left as a tag a renderer might ignore.
            self.assertEqual(im.size, (900, 1600))
            self.assertNotIn(0x0112, dict(im.getexif()))

    def test_already_upright_jpeg_is_untouched_in_shape(self):
        data = _gradient_jpeg_bytes(1280, 704)
        out, ext, err = P.normalize_ingested_image_bytes(data, "clip.jpg")
        self.assertIsNone(err)
        with Image.open(io.BytesIO(out)) as im:
            self.assertEqual(im.size, (1280, 704))

    def test_png_round_trips_and_strips_no_alpha_it_doesnt_have(self):
        im = Image.new("RGB", (64, 32), (10, 20, 30))
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        out, ext, err = P.normalize_ingested_image_bytes(buf.getvalue(), "still.png")
        self.assertIsNone(err)
        self.assertEqual(ext, ".png")
        with Image.open(io.BytesIO(out)) as got:
            self.assertEqual(got.size, (64, 32))

    def test_corrupt_file_refuses_with_a_message_not_a_crash(self):
        out, ext, err = P.normalize_ingested_image_bytes(b"not an image", "broken.jpg")
        self.assertIsNotNone(err)
        self.assertIn("image", err.lower())


class HeicHandling(unittest.TestCase):
    def test_heic_without_sips_refuses_clearly(self):
        # Simulate "sips unavailable" (a non-Mac dev box, or a sandboxed
        # test run) without depending on the real binary being absent.
        real_which = shutil.which
        try:
            shutil.which = lambda cmd: (None if cmd == "sips" else real_which(cmd))
            out, ext, err = P.normalize_ingested_image_bytes(b"\x00\x00fake heic", "IMG_1.heic")
        finally:
            shutil.which = real_which
        self.assertIsNotNone(err)
        self.assertIn("HEIC", err)
        self.assertIn("export", err.lower())

    @unittest.skipUnless(shutil.which("sips"), "sips not available on this host")
    def test_real_heic_converts_via_sips_when_available(self):
        # Build a tiny HEIC on the fly with sips itself (round-trip proof —
        # this only runs on the macOS boxes this feature targets).
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            src_png = Path(td) / "in.png"
            Image.new("RGB", (32, 24), (200, 100, 50)).save(src_png)
            src_heic = Path(td) / "in.heic"
            subprocess.run(["sips", "-s", "format", "heic", str(src_png),
                            "--out", str(src_heic)], check=True, capture_output=True)
            data = src_heic.read_bytes()
        out, ext, err = P.normalize_ingested_image_bytes(data, "photo.heic")
        self.assertIsNone(err)
        self.assertEqual(ext, ".jpg")
        with Image.open(io.BytesIO(out)) as im:
            self.assertEqual(im.size, (32, 24))


class ThumbnailDefensiveTranspose(unittest.TestCase):
    def test_thumbnail_of_a_pre_existing_rotated_file_is_upright(self):
        # Simulates a file already on disk from BEFORE this fix landed —
        # never normalized at ingest — proving the belt-and-suspenders
        # exif_transpose in _ensure_thumbnail covers it too.
        tmp = Path(tempfile.mkdtemp(prefix="phos-thumb-"))
        src = tmp / "old_upload.jpg"
        src.write_bytes(_gradient_jpeg_bytes(1600, 900, orientation=6))
        thumb = P._ensure_thumbnail(src, 200)
        with Image.open(thumb) as im:
            # Portrait aspect preserved after the defensive transpose —
            # width < height, matching the EXIF-corrected orientation.
            self.assertLess(im.size[0], im.size[1])


if __name__ == "__main__":
    unittest.main()
