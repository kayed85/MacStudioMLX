"""VC-12 [P1] / SYS-20 [P2]: the Tier modal and the Quality chips priced the
SAME render two different ways and disagreed.

Pinned by the flow review: on a 64 GB Mac the chips said "Balanced ~3, Quick
~3, High ~4, High·720p ~8" while the Tier modal said "Text->video about 8
min, Quick about 2 min, High about 7 min" — because the modal read
CAPABILITIES[tier]["times"], a hand-typed table keyed by RAM TIER only
(oblivious to chip speed, and never updated when the chip-aware cost model
changed underneath it), while the chips read LTX_TIERS (ltx_estimate_minutes
+ VC-01's chip factor). SYS-20 is the same bug from the system-flow review,
plus a stray dev instruction line the modal doesn't need to show.

The fix retires CAPABILITIES[...]['times'] / ['quality_times'] entirely and
has BOTH surfaces read from the SAME model: honest_tier_times() for the
modal (panel/routes_queue.py's /status 'tier.times'), ltx_tiers_payload()
for the chips (BOOT.ltx / already covered by test_vc01_*). This suite pins
that the two now agree, and that honest_tier_times reprices with chip speed
(closing the loophole where a RAM-tier-only table could drift again).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("LTX_STATE_DIR", tempfile.mkdtemp(prefix="phos-vc12-"))
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402


class CapabilitiesNoLongerCarriesAStaticPriceTable(unittest.TestCase):
    def test_times_and_quality_times_are_gone(self):
        for tier, caps in P.CAPABILITIES.items():
            with self.subTest(tier=tier):
                self.assertNotIn("times", caps)
                self.assertNotIn("quality_times", caps)


class HonestTierTimesAgreesWithTheChips(unittest.TestCase):
    def test_t2v_standard_matches_the_standard_5s_chip(self):
        chip = P._fmt_eta(P.LTX_TIERS["standard_5s"]["eta_min"]).lstrip("~")
        self.assertEqual(P.honest_tier_times()["t2v_standard"], chip)

    def test_t2v_draft_matches_the_quick_5s_chip(self):
        chip = P._fmt_eta(P.LTX_TIERS["quick_5s"]["eta_min"]).lstrip("~")
        self.assertEqual(P.honest_tier_times()["t2v_draft"], chip)

    def test_i2v_prices_identically_to_t2v(self):
        times = P.honest_tier_times()
        self.assertEqual(times["i2v_standard"], times["t2v_standard"])

    def test_high_is_none_when_q8_not_allowed(self):
        with mock.patch.object(P, "SYSTEM_CAPS", {**P.SYSTEM_CAPS, "allows_q8": False,
                                                   "allows_keyframe": False,
                                                   "allows_extend": False}):
            times = P.honest_tier_times()
        self.assertIsNone(times["high"])
        self.assertIsNone(times["keyframe"])
        self.assertIsNone(times["extend"])

    def test_keyframe_and_extend_carry_a_real_clamped_canvas(self):
        with mock.patch.object(P, "SYSTEM_CAPS", {**P.SYSTEM_CAPS, "allows_q8": True,
                                                   "allows_keyframe": True,
                                                   "allows_extend": True,
                                                   "keyframe_max_dim": 768,
                                                   "extend_max_dim": 768}):
            times = P.honest_tier_times()
        self.assertIn("768", times["keyframe"])
        self.assertIn("768", times["extend"])

    def test_reprices_with_chip_speed(self):
        """The old bug: a hand-typed table keyed by RAM tier only, blind to
        chip. honest_tier_times reads LTX_TIERS, which test_vc01_* already
        proves is chip-scaled — rebuild it here (LTX_TIERS is a module
        constant fixed at import, like every other tier table) to confirm
        honest_tier_times tracks whatever LTX_TIERS says rather than
        carrying its own second number."""
        with mock.patch.object(P, "LTX_TIERS", {
            **P.LTX_TIERS,
            "standard_5s": {**P.LTX_TIERS["standard_5s"], "eta_min": 40.0},
        }):
            slow = P.honest_tier_times()["t2v_standard"]
        self.assertIn("40", slow)
        self.assertNotEqual(slow, P.honest_tier_times()["t2v_standard"])


class RouteExposesTheHonestTable(unittest.TestCase):
    def test_status_tier_times_is_honest_tier_times(self):
        import panel.routes_queue as rq
        self.assertIn("P.honest_tier_times()",
                      Path(rq.__file__).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
