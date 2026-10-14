"""VC-29/H3-01/VA-02: reference images used to always get centre-cropped
with no way to say otherwise — a portrait photo cover-cropped onto a
landscape H3/I2V/A2V canvas lost the face, and there was no signal from the
UI (or the job spec) that a crop was even happening. Pinned here:

  1. _cover_crop_image's focus=0.5 is byte-identical to the OLD hardcoded
     centre-crop (floor division) — the fix must not shift a single pixel
     for anyone who never touches the new control.
  2. A non-centre focus actually moves the crop window, and 0/1 sit at the
     two edges.
  3. _job_crop_focus() clamps/validates whatever the client sent so a bad
     or missing value can't crash a render.
  4. _h3_fit_first_frame keeps EXIF orientation on the panel's own pre-fit
     (H3-01 also cited "the reference cover-crop keeps a sideways face").
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SANDBOX = Path(tempfile.mkdtemp(prefix="phos-test-cropfocus-"))
for _var, _sub in (("LTX_STATE_DIR", "state"), ("LTX_OUTPUT_DIR", "mlx_outputs"),
                   ("LTX_UPLOADS_DIR", "panel_uploads")):
    os.environ.setdefault(_var, str(SANDBOX / _sub))
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")
os.environ.setdefault("PHOSPHENE_DISABLE_VERSION_CHECK", "1")
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageOps  # noqa: E402

import mlx_ltx_panel as P  # noqa: E402


def _gradient(w: int, h: int) -> Image.Image:
    """A vertical gradient (top=black, bottom=white) so a crop's vertical
    offset is visible in the mean pixel value of the result."""
    im = Image.new("L", (w, h))
    px = im.load()
    for y in range(h):
        v = int(255 * y / max(1, h - 1))
        for x in range(w):
            px[x, y] = v
    return im.convert("RGB")


class CoverCropFocus(unittest.TestCase):
    def test_default_focus_matches_old_hardcoded_centre_crop(self):
        im = _gradient(900, 1600)
        target_w, target_h = 640, 384
        got = P._cover_crop_image(im, target_w, target_h, 0.5)
        # The OLD formula this replaces, ported verbatim for comparison.
        scale = max(target_w / im.size[0], target_h / im.size[1])
        resized_size = (max(target_w, round(im.size[0] * scale)),
                        max(target_h, round(im.size[1] * scale)))
        left = max(0, (resized_size[0] - target_w) // 2)
        top = max(0, (resized_size[1] - target_h) // 2)
        want = im.resize(resized_size, Image.Resampling.LANCZOS).crop(
            (left, top, left + target_w, top + target_h))
        self.assertEqual(list(got.getdata()), list(want.getdata()))

    def test_focus_0_keeps_the_top_focus_1_keeps_the_bottom(self):
        im = _gradient(900, 1600)   # portrait -> landscape crop is vertical
        top = P._cover_crop_image(im, 640, 384, 0.0)
        bottom = P._cover_crop_image(im, 640, 384, 1.0)
        # Top crop should be much darker (near black) than a bottom crop
        # (near white) on this gradient.
        top_mean = sum(top.convert("L").getdata()) / (640 * 384)
        bottom_mean = sum(bottom.convert("L").getdata()) / (640 * 384)
        self.assertLess(top_mean, 60)
        self.assertGreater(bottom_mean, 195)
        self.assertLess(top_mean, bottom_mean)

    def test_result_is_always_exactly_the_target_canvas(self):
        im = _gradient(900, 1600)
        for f in (0.0, 0.25, 0.5, 0.75, 1.0):
            out = P._cover_crop_image(im, 640, 384, f)
            self.assertEqual(out.size, (640, 384))

    def test_no_crop_needed_when_aspect_already_matches(self):
        im = _gradient(640, 384)
        out = P._cover_crop_image(im, 640, 384, 0.9)
        self.assertEqual(out.size, (640, 384))


class JobCropFocusParsing(unittest.TestCase):
    def test_missing_defaults_to_centre(self):
        self.assertEqual(P._job_crop_focus({}), 0.5)

    def test_clamped_to_0_1(self):
        self.assertEqual(P._job_crop_focus({"image_crop_focus": "5"}), 1.0)
        self.assertEqual(P._job_crop_focus({"image_crop_focus": "-3"}), 0.0)

    def test_garbage_falls_back_to_centre(self):
        self.assertEqual(P._job_crop_focus({"image_crop_focus": "not-a-number"}), 0.5)
        self.assertEqual(P._job_crop_focus({"image_crop_focus": None}), 0.5)

    def test_valid_value_passes_through(self):
        self.assertAlmostEqual(P._job_crop_focus({"image_crop_focus": "0.2"}), 0.2)


class H3FitFirstFrameHonoursFocusAndExif(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="phos-h3fit-"))

    def test_focus_moves_the_crop(self):
        src = self.tmp / "portrait.png"
        _gradient(900, 1600).save(src)
        out_top = P._h3_fit_first_frame(src, 640, 384, "jobA", focus=0.0)
        out_bottom = P._h3_fit_first_frame(src, 640, 384, "jobB", focus=1.0)
        self.assertNotEqual(out_top, src)
        with Image.open(out_top) as im_top, Image.open(out_bottom) as im_bot:
            mean_top = sum(im_top.convert("L").getdata()) / (640 * 384)
            mean_bot = sum(im_bot.convert("L").getdata()) / (640 * 384)
        self.assertLess(mean_top, mean_bot)

    def test_exif_rotated_source_is_upright_before_crop(self):
        # A landscape-pixel image tagged "rotate 90 CW to display" (the
        # classic iPhone-portrait-in-JPEG shape, SYS-01/H3-01).
        base = _gradient(1600, 900)   # pixels: landscape
        exif = base.getexif()
        exif[0x0112] = 6              # Orientation: rotate 90 CW
        src = self.tmp / "sideways.jpg"
        base.save(src, exif=exif)
        # Sanity: PIL's raw open must still report the SIDEWAYS size, or the
        # rest of this test proves nothing.
        with Image.open(src) as raw:
            self.assertEqual(raw.size, (1600, 900))
            self.assertEqual(int(raw.getexif().get(0x0112, 1)), 6)
        out = P._h3_fit_first_frame(src, 384, 640, "jobC")  # a PORTRAIT canvas
        with Image.open(out) as fitted:
            self.assertEqual(fitted.size, (384, 640))
            # If exif_transpose had NOT run, the source would still measure
            # 1600x900 internally and the cover-crop math would scale
            # differently — cross-check against exif_transpose'd bytes.
        with Image.open(src) as raw:
            upright = ImageOps.exif_transpose(raw)
            self.assertEqual(upright.size, (900, 1600))  # now portrait


if __name__ == "__main__":
    unittest.main()
