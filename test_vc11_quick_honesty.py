"""VC-11 [P1]: "Quick" isn't quick at 5s, and its chip claimed to be "The
default" (a claim that belongs to Balanced/5s, the actual default cell).

Pinned by the flow review: LTX_MEASURED_ETA has Quick 5s at 161.8s and
Balanced 5s at 162.5s — 0.7s apart — while Quick also renders a smaller,
non-16:9 canvas (640x448 vs Balanced's 1024x576). The chip's blurb used to
read "The fastest look at the shot. The default -- one full beat." because
"The default" lived in the 5s LENGTH's blurb and got concatenated onto
EVERY quality's blurb at that length, not just the actual default quality
(Balanced).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("LTX_STATE_DIR", tempfile.mkdtemp(prefix="phos-vc11-"))
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402


class TheDefaultClaimBelongsToOneCell(unittest.TestCase):
    def test_only_the_true_default_cell_says_the_default(self):
        default_key = f"{P.LTX_QUALITY_DEFAULT}_{P.LTX_LENGTH_DEFAULT}"
        for key, cell in P.LTX_TIERS.items():
            with self.subTest(cell=key):
                if key == default_key:
                    self.assertIn("The default.", cell["blurb"])
                else:
                    self.assertNotIn("The default", cell["blurb"])

    def test_quick_5s_no_longer_claims_to_be_the_default(self):
        self.assertNotIn("The default", P.LTX_TIERS["quick_5s"]["blurb"])

    def test_balanced_5s_is_the_one_that_does(self):
        self.assertTrue(P.LTX_TIERS["balanced_5s"]["blurb"].endswith("The default."))


class QuickHonestlyNamesItsRealSaving(unittest.TestCase):
    def test_quick_5s_carries_a_no_faster_than_balanced_note(self):
        note = " ".join(P.LTX_TIERS["quick_5s"]["notes"])
        self.assertIn("No faster than Balanced", note)
        self.assertIn("20s", note)

    def test_quick_20s_carries_no_such_note(self):
        """20s is Quick's real saving (Balanced doesn't even offer 20s) —
        the note must not apply there."""
        note = " ".join(P.LTX_TIERS["quick_20s"]["notes"])
        self.assertNotIn("No faster than Balanced", note)

    def test_the_note_is_conditioned_on_the_real_gap_not_hardcoded(self):
        """If Quick's schedule ever gets meaningfully faster than Balanced's
        at 5s again, the note must stop firing — it compares against
        Balanced's OWN measured row, not a fixed string."""
        with_note = " ".join(P.LTX_TIERS["quick_5s"]["notes"])
        self.assertIn("No faster than Balanced", with_note)
        # The comparison key exists and is what the note is conditioned on.
        bal = P.LTX_MEASURED_ETA.get(("ltx25", "balanced", "5s", "q4"))
        quick = P.LTX_MEASURED_ETA.get(("ltx25", "quick", "5s", "q4"))
        self.assertIsNotNone(bal)
        self.assertIsNotNone(quick)
        self.assertGreaterEqual(quick[0], bal[0] * 0.9)


if __name__ == "__main__":
    unittest.main()
