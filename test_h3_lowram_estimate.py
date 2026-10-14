"""H3 time estimates on a 36-59 GB Mac price the reduced-RAM lane.

THE REPORT (Pinokio, 2026-09-20). M4 Pro, 48 GB, Hailuo H3, a 15 s Draft:
the panel said about 18 minutes, the render took 43+. Two things were wrong
with the number and both are pinned here:

  1. Every H3 estimate was measured on a 64 GB M4 Max (the bf16 lane) and only
     scaled by CHIP. Below 60 GB H3 runs the Q8 DiT inside a tighter memory
     budget, and the fleet (60 days, 6,587 H3 renders, one median per install)
     shows chained 10/15 s clips there running 1.32x what the same chip is
     priced at on a >=60 GB Mac, single windows 1.07x.
  2. The same fleet shows that user's install rendering its 15 s Draft at the
     full 9-step sampler (Best, 40-60 min bucket) the day H3 was installed,
     while ~17 min is the FAST (3-step) price - Fast needs its one-click
     180 MB adapter. Best on that Mac is now priced ~42 min, Fast ~22.

The fleet buckets these assertions use are the ones that Mac's class actually
landed in (M4 Pro 48 GB, 4.15/4.16): draft_15s Fast 15/15 renders in the
20-30 min bucket, draft_15s Best 10/10 in 40-60.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("LTX_STATE_DIR", tempfile.mkdtemp(prefix="phos-lowram-eta-"))
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402


class _Mac:
    """Rebuild the H3 table as a given Mac would at import — a fresh install,
    with no learned ETA calibration (issue #90: an install that had learned a
    1.45 H3 correction priced the 26.6-minute receipt at 38.57, because
    `_hw_speed_factor` folds this install's own eta_calibration.json in)."""

    def __init__(self, ram_gb: float, chip: str):
        self.ram_gb, self.chip = ram_gb, chip

    def __enter__(self):
        self.saved = (P.SYSTEM_RAM_GB, P._HW_CHIP_FAMILY,
                      os.environ.pop("PHOSPHENE_SPEED_FACTOR", None))
        self._calibration = mock.patch.object(P, "_load_eta_calibration", lambda: {})
        self._calibration.start()
        P.SYSTEM_RAM_GB = self.ram_gb
        P._HW_CHIP_FAMILY = self.chip
        return P._build_h3_tiers()

    def __exit__(self, *exc):
        self._calibration.stop()
        P.SYSTEM_RAM_GB, P._HW_CHIP_FAMILY, env = self.saved
        if env is not None:
            os.environ["PHOSPHENE_SPEED_FACTOR"] = env


class ReducedRamLanePrice(unittest.TestCase):
    def test_the_reporters_mac_lands_in_its_fleet_bucket(self):
        with _Mac(48.0, "M4 Pro") as t:
            c = t["draft_15s"]
            # Fast: 15 of 15 fleet renders at 20-30 min. 4.16.1 said ~17.
            self.assertGreaterEqual(c["tristep_min"], 20.0)
            self.assertLess(c["tristep_min"], 30.0)
            # Best: 10 of 10 at 40-60 min. 4.16.1 said ~32.
            self.assertGreaterEqual(c["eta_min"], 40.0)
            self.assertLess(c["eta_min"], 60.0)

    def test_chained_cells_carry_the_chain_factor(self):
        with _Mac(64.0, "M4 Pro") as t64:
            base = {k: (c["eta_min"], c.get("tristep_min")) for k, c in t64.items()}
        with _Mac(48.0, "M4 Pro") as t48:
            for key, c in t48.items():
                with self.subTest(cell=key):
                    k = (P.H3_LOWRAM_FACTOR_CHAIN if c["chain_windows"] > 1
                         else P.H3_LOWRAM_FACTOR_SINGLE)
                    if c.get("fast_hd") and c.get("tristep_min") is not None:
                        # Fast HD = the H3 Fast pass (RAM lane factor applies)
                        # + an LTX Face Fix (chip factor only, no H3 lane).
                        self.assertAlmostEqual(
                            c["eta_min"], c["tristep_min"] + c["facefix_min"], delta=0.02)
                        self.assertAlmostEqual(c["tristep_min"], base[key][1] * k, delta=0.02)
                        continue
                    self.assertAlmostEqual(c["eta_min"], base[key][0] * k, delta=0.02)
                    if base[key][1] is not None:
                        self.assertAlmostEqual(c["tristep_min"], base[key][1] * k,
                                               delta=0.02)

    def test_sixty_gb_and_up_is_unchanged(self):
        """The bf16 lane is what every receipt was measured on."""
        for ram in (60.0, 64.0, 128.0):
            with _Mac(ram, "M4 Max") as t:
                self.assertEqual(t["standard_15s"]["eta_min"], 26.6)  # receipt
                self.assertEqual(t["draft_3s"]["eta_min"], 3.0)       # receipt
                self.assertAlmostEqual(t["draft_15s"]["tristep_min"], 8.53, delta=0.01)

    def test_browser_repricing_uses_the_same_factor(self):
        """A pinned Steps count is priced in JS from per_forward_sec and
        fixed_sec; at Auto that sum must equal the server's own model."""
        with _Mac(48.0, "M4 Pro") as t:
            for key in ("draft_15s", "standard_10s", "high_15s", "draft_5s"):
                c = t[key]
                if c["eta_measured"]:
                    continue
                win = c["chain_windows"]
                fwd = max(1, c["steps"] - 1)
                js = (win * fwd * c["per_forward_sec"] + win * c["fixed_sec"]) / 60
                with self.subTest(cell=key):
                    self.assertAlmostEqual(js, c["eta_min"], delta=0.05)

    def test_explicit_speed_override_is_total(self):
        os.environ["PHOSPHENE_SPEED_FACTOR"] = "2.0"
        try:
            self.assertEqual(P._h3_ram_factor(3, ram_gb=48.0), 1.0)
        finally:
            del os.environ["PHOSPHENE_SPEED_FACTOR"]
        # An override the chip table would ignore must not switch this off.
        os.environ["PHOSPHENE_SPEED_FACTOR"] = "auto"
        try:
            self.assertEqual(P._h3_ram_factor(3, ram_gb=48.0), P.H3_LOWRAM_FACTOR_CHAIN)
        finally:
            del os.environ["PHOSPHENE_SPEED_FACTOR"]
        self.assertEqual(P._h3_ram_factor(3, ram_gb=48.0), P.H3_LOWRAM_FACTOR_CHAIN)
        self.assertEqual(P._h3_ram_factor(1, ram_gb=48.0), P.H3_LOWRAM_FACTOR_SINGLE)
        self.assertEqual(P._h3_ram_factor(3, ram_gb=64.0), 1.0)


class HonestNote(unittest.TestCase):
    def test_chained_lengths_say_why_and_what_helps(self):
        with _Mac(48.0, "M4 Pro") as t:
            for key in ("draft_10s", "draft_15s", "high_15s"):
                with self.subTest(cell=key):
                    note = t[key]["note"]
                    self.assertIn("48 GB Mac", note)
                    self.assertIn("swap", note)
                    self.assertIn("includes that", note)
            for key in ("draft_5s", "high_3s", "standard_10s_dense"):
                with self.subTest(cell=key):
                    self.assertNotIn("reduced-memory", t[key]["note"])

    def test_no_note_where_there_is_room(self):
        with _Mac(64.0, "M4 Max") as t:
            self.assertNotIn("reduced-memory", t["draft_15s"]["note"])

    def test_the_legacy_chain_swap_keeps_the_ram_note(self):
        """h3_visible_tiers swaps ONE note for its legacy text by value; the
        RAM sentence is a separate entry and has to survive that."""
        with _Mac(48.0, "M4 Pro") as t:
            notes = t["draft_15s"]["notes"]
            self.assertIn(P.H3_TIER_CHAIN_NOTE, notes)
            self.assertEqual(sum("reduced-memory" in n for n in notes), 1)

    def test_note_is_ascii_safe_for_copying(self):
        self.assertTrue(P.H3_TIER_LOWRAM_CHAIN_NOTE.isascii())


if __name__ == "__main__":
    unittest.main()
