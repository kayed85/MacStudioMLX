"""Remaining size-faces package findings, grouped because each is a small,
independent assertion rather than its own scenario:

  - VC-02(c): make_job's allowlist actually carries image_crop_focus and
    extend_face_fix_after through to the job's params (the "leave it out of
    the dict and it silently no-ops" trap this codebase warns about
    everywhere — see CLAUDE.md's make_job allowlist note).
  - VA-13: BOOT exposes extend_max_dim/allows_extend so the client can say
    the clamp BEFORE Generate.
  - VC-39: the two new aspect presets exist, sit on the /32 model-safe grid,
    and are genuinely 1:1 / 4:5.
  - SYS-02: the widened upload-accept set for Train really is a superset of
    the on-disk set (nothing was silently narrowed).
  - H3-02: the fold rule that hides #aspectRow on non-LTX engines has equal
    or higher CSS specificity than the rule that turns it back on, so it
    wins regardless of source order (a real browser cascade is not run
    here — this is the hand-computed specificity check the finding itself
    asked for).
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SANDBOX = Path(tempfile.mkdtemp(prefix="phos-test-misc-"))
for _var, _sub in (("LTX_STATE_DIR", "state"), ("LTX_OUTPUT_DIR", "mlx_outputs"),
                   ("LTX_UPLOADS_DIR", "panel_uploads")):
    os.environ.setdefault(_var, str(SANDBOX / _sub))
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")
os.environ.setdefault("PHOSPHENE_DISABLE_VERSION_CHECK", "1")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402


class MakeJobAllowlist(unittest.TestCase):
    def test_image_crop_focus_reaches_the_job(self):
        job = P.make_job({"mode": ["i2v"], "image_crop_focus": ["0.15"]})
        self.assertEqual(job["params"]["image_crop_focus"], "0.15")

    def test_image_crop_focus_defaults_to_centre(self):
        job = P.make_job({"mode": ["i2v"]})
        self.assertEqual(job["params"]["image_crop_focus"], "0.5")

    def test_extend_face_fix_after_reaches_the_job(self):
        job = P.make_job({"mode": ["extend"], "video_path": ["/tmp/x.mp4"],
                          "extend_face_fix_after": ["1"]})
        self.assertTrue(job["params"]["extend_face_fix_after"])

    def test_extend_face_fix_after_defaults_off(self):
        job = P.make_job({"mode": ["extend"], "video_path": ["/tmp/x.mp4"]})
        self.assertFalse(job["params"]["extend_face_fix_after"])


class ExtendClampBootData(unittest.TestCase):
    def test_capabilities_table_has_extend_max_dim_per_tier(self):
        for tier_key, caps in P.CAPABILITIES.items():
            self.assertIn("extend_max_dim", caps, tier_key)
            self.assertIn("allows_extend", caps, tier_key)


class AspectPresetsForSocialFormats(unittest.TestCase):
    def test_square_is_exactly_1_to_1_on_the_32_grid(self):
        a = P.ASPECTS["square"]
        self.assertEqual(a["w"], a["h"])
        self.assertEqual(a["w"] % 32, 0)

    def test_portrait_4_5_is_exactly_4_to_5_on_the_64_grid(self):
        # /64, not /32: make_job floors every LTX canvas to /64, and the
        # /32-only 896x1120 rendered 896x1088 (4.17 Codex EST-11).
        a = P.ASPECTS["portrait_4_5"]
        self.assertEqual(a["w"] % 64, 0)
        self.assertEqual(a["h"] % 64, 0)
        self.assertEqual(a["w"] * 5, a["h"] * 4)


class TrainUploadAcceptsHeicSupersetOfOnDisk(unittest.TestCase):
    def test_upload_exts_are_a_strict_superset(self):
        self.assertTrue(P.TRAIN_IMAGE_EXTS.issubset(P.TRAIN_IMAGE_UPLOAD_EXTS))
        for heic_ext in (".heic", ".heif", ".avif"):
            self.assertIn(heic_ext, P.TRAIN_IMAGE_UPLOAD_EXTS)
            self.assertNotIn(heic_ext, P.TRAIN_IMAGE_EXTS)  # never written as-is


def _specificity(selector: str) -> tuple[int, int, int]:
    """Cheap (ids, classes/attrs/pseudo-classes, elements) specificity
    count for the small, single-compound-selector-chain subset of CSS this
    file's two #aspectRow rules use — not a general CSS parser."""
    ids = len(re.findall(r"#[\w-]+", selector))
    classes = len(re.findall(r"\.[\w-]+", selector))
    attrs = len(re.findall(r"\[[^\]]+\]", selector))
    # :not(X) counts as X's specificity, not an extra pseudo-class of its
    # own — approximate by counting the attribute selectors INSIDE :not()
    # (already caught by the attrs count above) and any bare pseudo-class.
    pseudo = len(re.findall(r":(?!not\()[\w-]+", selector))
    elements = len(re.findall(r"(?:^|[\s>+~])(?!\.|\#|\[|:)[a-zA-Z][\w-]*", selector))
    return (ids, classes + attrs + pseudo, elements)


class H3AspectRowFoldSpecificity(unittest.TestCase):
    def setUp(self):
        self.css = (ROOT / "webapp" / "style" / "panel.css").read_text(encoding="utf-8")

    def test_both_aspectrow_rules_are_present(self):
        self.assertIn('.cz-body > #aspectRow.mode-only.show { display: flex !important', self.css)
        self.assertIn('body:not([data-engine="ltx"]) .cz-body > #aspectRow.mode-only.show { display: none !important', self.css)

    def test_fold_rule_specificity_beats_the_show_rule(self):
        show_rule = '.cz-body > #aspectRow.mode-only.show'
        fold_rule = 'body:not([data-engine="ltx"]) .cz-body > #aspectRow.mode-only.show'
        self.assertGreater(_specificity(fold_rule), _specificity(show_rule))

    def test_generated_engine_fold_rule_alone_was_the_old_losing_rule(self):
        # The ORIGINAL bug: _engine_css()'s generated fold rule for
        # data-ltx-only elements has lower specificity than the show rule —
        # this is what let #aspectRow leak through on H3. Confirms the new
        # ID-qualified rule was necessary, not just cosmetic.
        show_rule = '.cz-body > #aspectRow.mode-only.show'
        generated_fold = 'body:not([data-engine="ltx"]) [data-ltx-only]'
        self.assertLess(_specificity(generated_fold), _specificity(show_rule))


if __name__ == "__main__":
    unittest.main()
