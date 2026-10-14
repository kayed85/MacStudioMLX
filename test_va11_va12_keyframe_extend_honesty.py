"""VA-11 [P1] / VA-12 [P2]: FFLF/Keyframe and Extend priced a DIFFERENT
pipeline than the one that actually runs.

Keyframe always renders the Q8 two-stage pass (LTX_HQ_STAGE1/2) regardless
of the selected Quality pill, and clamps its canvas to tier_max_dim
("keyframe") at RUN TIME (mlx_ltx_panel.py's keyframe branch). Before this
fix the Shot setup summary and #derivedFooter showed whatever the Quality
strip's SELECTED cell said — e.g. "Balanced * 5s * 9:16 * ~3 min" for a
448x768 FFLF request that actually rendered Q8 two-stage, clamped to
432x768, at a real ~5 min. Extend similarly showed hardcoded "~16 min" /
"~38 min" pill text and sent 12 steps on "Draft" when the server's own
validated default (owner ruling 2026-05-21) is 8 — 50% slower than what
was supposed to ship.

ltx_keyframe_estimate_minutes / ltx_extend_estimate_minutes /
ltx_mode_price_card give both surfaces ONE real number apiece, computed
from the exact clamp and exact step count run_job_inner uses.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("LTX_STATE_DIR", tempfile.mkdtemp(prefix="phos-va11-"))
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402


class KeyframeEstimateMatchesTheRealClamp(unittest.TestCase):
    def test_clamps_to_the_tier_max_dim(self):
        with mock.patch.object(P, "SYSTEM_CAPS", {**P.SYSTEM_CAPS, "keyframe_max_dim": 768}):
            w, h, minutes = P.ltx_keyframe_estimate_minutes(121, width=1280, height=704)
        self.assertLessEqual(max(w, h), 768)
        self.assertGreater(minutes, 0)

    def test_uncapped_tier_passes_the_requested_shape_through(self):
        with mock.patch.object(P, "SYSTEM_CAPS", {**P.SYSTEM_CAPS, "keyframe_max_dim": 0}):
            w, h, _ = P.ltx_keyframe_estimate_minutes(121, width=1280, height=704)
        self.assertEqual((w, h), (1280, 704))

    def test_prices_at_hq_two_stage_steps_not_whatever_quality_is_selected(self):
        """The bug: the UI could show a Quick/Balanced (8-step distilled)
        price for a render that always runs LTX_HQ_STAGE1/2. Confirm the
        keyframe estimate is strictly the HQ-step price, by comparing
        against a same-shape distilled estimate."""
        w, h, kf_minutes = P.ltx_keyframe_estimate_minutes(121, width=768, height=432)
        distilled_minutes = P.ltx_estimate_minutes(
            w, h, 121, P.LTX_DISTILLED_STAGE1, P.LTX_DISTILLED_STAGE2)
        hq_minutes = P.ltx_estimate_minutes(w, h, 121, P.LTX_HQ_STAGE1, P.LTX_HQ_STAGE2)
        self.assertAlmostEqual(kf_minutes, hq_minutes, delta=0.01)
        self.assertNotAlmostEqual(kf_minutes, distilled_minutes, delta=0.01)


class ExtendEstimateMatchesTheRealDefault(unittest.TestCase):
    def test_default_steps_is_eight_not_twelve(self):
        """VA-12: the client used to send 12 on Draft — 50% slower than the
        validated 8-step default. The price function's own default must be
        the validated one, so a caller that forgets `steps=` can't silently
        reintroduce the regression."""
        w, h, eight = P.ltx_extend_estimate_minutes(5)
        _, _, twelve = P.ltx_extend_estimate_minutes(5, steps=12)
        self.assertLess(eight, twelve)

    def test_clamps_to_the_tier_max_dim(self):
        with mock.patch.object(P, "SYSTEM_CAPS", {**P.SYSTEM_CAPS, "extend_max_dim": 768}):
            w, h, _ = P.ltx_extend_estimate_minutes(5, steps=8, width=1280, height=704)
        self.assertLessEqual(max(w, h), 768)

    def test_prices_the_delivered_length_not_just_the_added_seconds(self):
        w, h, five_latents = P.ltx_extend_estimate_minutes(5, steps=8)
        _, _, ten_latents = P.ltx_extend_estimate_minutes(10, steps=8)
        self.assertGreater(ten_latents, five_latents)


class ModePriceCard(unittest.TestCase):
    def test_keyframe_card_is_none_when_tier_disallows_it(self):
        with mock.patch.object(P, "SYSTEM_CAPS", {**P.SYSTEM_CAPS, "allows_keyframe": False}):
            self.assertIsNone(P.ltx_mode_price_card("keyframe"))

    def test_extend_card_is_none_when_tier_disallows_it(self):
        with mock.patch.object(P, "SYSTEM_CAPS", {**P.SYSTEM_CAPS, "allows_extend": False}):
            self.assertIsNone(P.ltx_mode_price_card("extend"))

    def test_keyframe_card_shape(self):
        with mock.patch.object(P, "SYSTEM_CAPS", {**P.SYSTEM_CAPS, "allows_keyframe": True,
                                                   "keyframe_max_dim": 768}):
            card = P.ltx_mode_price_card("keyframe")
        self.assertEqual(card["mode"], "keyframe")
        self.assertIn("width", card)
        self.assertIn("height", card)
        self.assertIn("eta", card)
        self.assertEqual(card["pipeline_note"], "Q8 two-stage")

    def test_extend_card_carries_added_and_total_seconds(self):
        with mock.patch.object(P, "SYSTEM_CAPS", {**P.SYSTEM_CAPS, "allows_extend": True,
                                                   "extend_max_dim": 768}):
            draft = P.ltx_mode_price_card("extend", steps=8)
            pro = P.ltx_mode_price_card("extend", steps=30)
        self.assertEqual(draft["steps"], 8)
        self.assertEqual(pro["steps"], 30)
        self.assertGreater(draft["added_seconds"], 0)
        self.assertGreater(draft["total_seconds"], draft["added_seconds"])
        # Pro (more steps, same shape) must never be cheaper than Draft.
        self.assertGreaterEqual(pro["eta_min"], draft["eta_min"])

    def test_unknown_mode_returns_none(self):
        self.assertIsNone(P.ltx_mode_price_card("t2v"))


class BootAndStatusExposeTheCards(unittest.TestCase):
    def test_boot_payload_has_the_keyframe_and_extend_prices(self):
        page_src = P.page()
        self.assertIn('"keyframe_price"', page_src)
        self.assertIn('"extend_price_draft"', page_src)
        self.assertIn('"extend_price_pro"', page_src)

    def test_routes_queue_exposes_the_same_fields(self):
        import panel.routes_queue as rq
        src = Path(rq.__file__).read_text(encoding="utf-8")
        self.assertIn("ltx_mode_price_card", src)


if __name__ == "__main__":
    unittest.main()
