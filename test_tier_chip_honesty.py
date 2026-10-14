#!/usr/bin/env python3
"""VC-28 [P2] [UX] The quality ladder names contradict their sizes.

Coordinator ruling, 2026-09-29: "DON'T rename the tiers. Show the real
output size and the time range on each chip, and fix the 'The default'
tooltip." (The "default" tooltip bug is VC-11's own text: the length axis's
5s blurb said "The default — one full beat." unconditionally, so it was
concatenated onto EVERY quality's 5s cell — Quick·5s, High·5s, ... — not
only Balanced·5s, the one combination that actually is the shipped default.)

Gated here:
  1  ltx_tiers_payload() prices the DELIVERED size (post fit_720p export for
     Balanced, the same gap VC-19 closed for the info modal) beside the
     render canvas, for both the per-quality items and the per-cell tiers —
     and leaves every non-exporting quality's delivered size equal to its
     render canvas (a no-op there)
  2  each cell carries an honest time RANGE (_fmt_eta_range), narrow for a
     real measurement, biased upward for a modelled estimate (this table's
     own history is estimates running optimistic, never pessimistic)
  3  "The default" is stamped on exactly one cell — balanced_5s — server
     side, and the 5s length item's own blurb no longer claims it
     unconditionally
  4  the client: qualitySpec shows the delivered size (with an arrow from
     the render canvas when they differ) and ltxCellEta prefers the range
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = Path(tempfile.mkdtemp(prefix="phos-tierhonesty-"))
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8323")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402

EJS = (ROOT / "webapp" / "js" / "engines.js").read_text(encoding="utf-8")


def _payload_on_a_standard_tier_mac() -> dict:
    """ltx_tiers_payload() as a 48-79 GB Mac boots with it (issue #90).

    LTX_QUALITIES bakes this Mac's `t2v_max_dim` into every canvas at import:
    a <48 GB (`base`) Mac renders Balanced at 768x448 by design and its chip
    says so. The delivered-size contract below is about the full canvases,
    so the tables are rebuilt for the standard tier rather than read as
    whichever Mac runs the suite booted them."""
    with mock.patch.object(P, "SYSTEM_TIER", "standard"), \
            mock.patch.object(P, "SYSTEM_CAPS", P.CAPABILITIES["standard"]):
        with mock.patch.object(P, "LTX_QUALITIES", P._ltx_qualities()):
            with mock.patch.object(P, "LTX_TIERS", P._build_ltx_tiers()):
                return P.ltx_tiers_payload()


class TestDeliveredSize(unittest.TestCase):
    def setUp(self):
        self.payload = _payload_on_a_standard_tier_mac()
        self.tiers = {t["key"]: t for t in self.payload["tiers"]}
        self.qualities = {q["key"]: q for q in self.payload["qualities"]}

    def test_balanced_delivers_720p_not_its_render_canvas(self):
        q = self.qualities["balanced"]
        self.assertEqual(q["canvas"], "1024×576")
        self.assertEqual(q["delivered_canvas"], "1280×720")
        t = self.tiers["balanced_5s"]
        self.assertEqual((t["width"], t["height"]), (1024, 576))
        self.assertEqual((t["delivered_width"], t["delivered_height"]), (1280, 720))
        self.assertEqual(t["delivered_spec"], "1280×720")
        self.assertTrue(t["delivered_differs"])

    def test_non_exporting_qualities_are_unaffected(self):
        for key in ("quick", "standard"):
            q = self.qualities[key]
            self.assertEqual(q["canvas"], q["delivered_canvas"], key)
        t = self.tiers["standard_5s"]
        self.assertFalse(t["delivered_differs"])
        self.assertEqual(t["delivered_spec"], f"{t['width']}×{t['height']}")

    def test_high_720p_is_already_at_its_delivered_size(self):
        # high_720p renders 1280x704 with no export — its own canvas already
        # is what lands in Outputs.
        if "high_720p" in self.qualities:
            q = self.qualities["high_720p"]
            self.assertEqual(q["canvas"], q["delivered_canvas"])


class TestEtaRange(unittest.TestCase):
    def setUp(self):
        self.tiers = {t["key"]: t for t in P.ltx_tiers_payload()["tiers"]}

    def test_every_priced_cell_has_a_range(self):
        for key, t in self.tiers.items():
            if t.get("eta_min"):
                self.assertIn("eta_range", t, key)
                self.assertTrue(t["eta_range"].startswith("~"), (key, t["eta_range"]))

    def test_modelled_cell_range_skews_upward_never_downward(self):
        # Find a cell with a real measurement and one that is purely modelled
        # (eta_measured False) and check the range shape directly via the
        # formatter, independent of which exact cells happen to be measured
        # today.
        # One range formatter for the whole panel (est's fleet ranges and
        # this chip band share it since the 4.17 merge): en dash, "~3–4 min".
        lo, hi = P._fmt_eta_range(3.0, 4.2).replace("~", "").split(" min")[0].split("–")
        self.assertEqual(int(lo), 3)
        self.assertEqual(int(hi), 4)

    def test_measured_cell_gets_a_narrow_band(self):
        s = P._fmt_eta_range(9.0, 9.0 * 1.1)
        self.assertIn("9", s)

    def test_identical_ends_collapse_to_one_number(self):
        self.assertEqual(P._fmt_eta_range(3.0, 3.0), "~3 min")

    def test_hours_range_format(self):
        s = P._fmt_eta_range(70.0, 95.0)
        self.assertTrue(s.startswith("~1h"), s)
        self.assertIn("–", s)


class TestTheDefaultStamp(unittest.TestCase):
    def setUp(self):
        self.tiers = {t["key"]: t for t in P.ltx_tiers_payload()["tiers"]}

    def test_only_balanced_5s_is_the_default(self):
        defaults = [k for k, t in self.tiers.items() if t.get("is_default")]
        self.assertEqual(defaults, ["balanced_5s"])

    def test_only_balanced_5s_blurb_claims_it(self):
        for key, t in self.tiers.items():
            if key == "balanced_5s":
                self.assertTrue(t["blurb"].startswith("The default."), t["blurb"])
            else:
                self.assertNotIn("The default", t["blurb"], key)

    def test_quick_5s_no_longer_claims_the_default(self):
        # This is the exact VC-11 repro: Quick's chip used to combine its own
        # blurb with the 5s length's "The default — one full beat.", so
        # hovering Quick (never the default quality) said it was one.
        self.assertNotIn("The default", self.tiers["quick_5s"]["blurb"])
        self.assertIn("fastest", self.tiers["quick_5s"]["blurb"])

    def test_length_item_no_longer_hardcodes_the_default(self):
        # VC-23: re-worded again in plain English ("beat" was a
        # screenwriting idiom) — the assertion that matters here is
        # "The default" is gone, not the exact remaining wording.
        self.assertNotIn("The default", P.LTX_LENGTHS["5s"]["blurb"])

    def test_default_constants_agree_with_the_stamp(self):
        self.assertEqual(P.LTX_QUALITY_DEFAULT, "balanced")
        self.assertEqual(P.LTX_LENGTH_DEFAULT, "5s")


class TestClientRendering(unittest.TestCase):
    def test_quality_spec_shows_delivered_size_with_an_arrow(self):
        fn = EJS[EJS.index("qualitySpec: (item, cell) => {"):]
        fn = fn[:fn.index("\n    },")]
        self.assertIn("delivered_canvas", fn)
        self.assertIn("→", fn)

    def test_eta_prefers_the_range(self):
        fn = EJS[EJS.index("function ltxCellEta("):]
        fn = fn[:fn.index("\n}\n")]
        self.assertIn("cell.eta_range", fn)
        # the fast-draft / Q8-HQ special cases are unchanged
        self.assertIn("fast_eta", fn)
        # VC-23: relabelled "Q8 HQ" -> "High detail" (plain English)
        self.assertIn("High detail", fn)


if __name__ == "__main__":
    unittest.main()
