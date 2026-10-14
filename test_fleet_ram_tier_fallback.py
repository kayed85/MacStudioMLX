"""4.17.1 fix: fleet_estimate_range() no longer mixes RAM tiers.

THE BUG (found driving beta b7a176f on the owner's real 64 GB M4 Max, report
in ~/AI/projects/phosphene/review-2026-09-29-ux/est_fix.txt): the LTX "High *
720p, 10s" chip read "~30-40 min". A real render at that exact setting,
INCLUDING the 1080p upscale, took 17.4 min (four renders in the 1042-1045 s
range — see work/outputs/bizarrotrn_stands_alone_at_a_support*_1080p.mp4.json
under review-2026-09-29-ux/post_bizarro/).

ROOT CAUSE: fleet_estimate_range()'s "chip" fallback level (same chip family,
ANY RAM) is built by scripts/fleet_timings_build.py with ram_gb dropped from
its GROUP BY on purpose — a deliberately coarser rung. For LTX High*720p at
241 frames, the fleet's only M4 Max samples happened to be 13 renders on
128 GB installs, all landing in the 1800-2400s ("30-40 min") bucket. Sold to
a 64 GB owner as "based on 13 renders on M4 Max Macs" — true of the chip,
false of the Mac.

THE FIX: two new levels (chip_ram_tier / chip_ram_tier_scaled) try the SAME
chip within this Mac's own RAM TIER first, reconstructed from the "cell"
level (the one place ram_gb still exists per row). The old RAM-blind "chip"
level is now GATED: skipped when the cell rows backing it are EXCLUSIVELY a
different RAM tier than this Mac's own. A new ram_tier / ram_tier_scaled
level (any chip, same RAM tier, chip-speed-adjusted via
_fleet_chip_adjust_ratio) sits between the gated "chip" and the unchanged,
still-ungated "model" level.

This suite is split in two: `RealShippedTableRegression` replays the exact
bug against the REAL committed data/fleet_timings.json (skipped if that file
is ever pulled from the checkout) — this is the test that failed before the
fix and passes after. `SyntheticFallbackLevels` pins the new fallback
mechanics against a small hand-built table, the same style
test_fleet_timings.py already uses, so the order doesn't depend on what next
week's real fleet pull happens to contain.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("LTX_STATE_DIR", tempfile.mkdtemp(prefix="phos-ramtier-"))
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402


class RamTierForGb(unittest.TestCase):
    """_ram_tier_for_gb must agree with _detect_tier's own thresholds
    (TIER_MIN_RAM_GB) at every boundary — the fallback logic is only as
    honest as this classification."""

    def test_matches_tier_min_ram_gb_boundaries(self):
        self.assertEqual(P._ram_tier_for_gb(0), "standard")     # safe default
        self.assertEqual(P._ram_tier_for_gb(8), "base")
        self.assertEqual(P._ram_tier_for_gb(47.9), "base")
        self.assertEqual(P._ram_tier_for_gb(48), "standard")
        self.assertEqual(P._ram_tier_for_gb(64), "standard")
        self.assertEqual(P._ram_tier_for_gb(79.9), "standard")
        self.assertEqual(P._ram_tier_for_gb(80), "high")
        self.assertEqual(P._ram_tier_for_gb(119.9), "high")
        self.assertEqual(P._ram_tier_for_gb(120), "pro")
        self.assertEqual(P._ram_tier_for_gb(128), "pro")

    def test_64gb_and_128gb_are_different_tiers(self):
        """The exact split that matters for the bug: a 64 GB Mac and the
        128 GB Macs that dominated the High*720p fleet sample must land in
        DIFFERENT tiers, or the whole fix is a no-op."""
        self.assertNotEqual(P._ram_tier_for_gb(64.0), P._ram_tier_for_gb(128.0))


class RealShippedTableRegression(unittest.TestCase):
    """The literal bug, replayed against the real, git-committed
    data/fleet_timings.json. FAILED before this fix (source == "chip",
    p50_min == 35.0 — "~30-40 min"); PASSES after."""

    def setUp(self):
        if not P.FLEET_TIMINGS.get("levels"):
            self.skipTest("no data/fleet_timings.json shipped in this checkout")
        cell_cells = (P.FLEET_TIMINGS.get("levels") or {}).get("cell", {}).get("cells") or {}
        has_bug_fixture = any(
            k.startswith("ltx|t2v|high_720p|241.0|M4 Max|") for k in cell_cells)
        if not has_bug_fixture:
            self.skipTest("the real table no longer has the M4 Max high_720p "
                          "241f fixture this regression test replays")

    def test_high_720p_10s_on_64gb_m4_max_is_not_priced_off_128gb_macs(self):
        r = P.fleet_estimate_range("ltx", "t2v", "high_720p", 241,
                                   chip="M4 Max", ram=64.0)
        self.assertIsNotNone(r)
        # The real measured wall clock for this EXACT setting (four renders,
        # 1042-1045 s, including the 1080p upscale) is 17.4 min. The old
        # "chip" level said "~30-40 min" (p50 35.0) -- more than double.
        # The fix must land within honest shouting distance of reality.
        self.assertLess(r["p50_min"], 25.0,
                        f"still pricing off a different RAM class: {r}")
        self.assertNotEqual(r["source"], "chip",
                            "must not silently reuse the RAM-blind chip "
                            "level when its own cell rows are all a "
                            "different RAM tier than this Mac's")

    def test_a_128gb_mac_still_sees_its_own_real_number(self):
        """The fix must not erase the 128 GB Mac's own honest number —
        it's real data for a real Mac, just not THIS Mac."""
        r = P.fleet_estimate_range("ltx", "t2v", "high_720p", 241,
                                   chip="M4 Max", ram=128.0)
        self.assertIsNotNone(r)
        self.assertEqual(r["source"], "cell")   # exact chip+ram+frames match


class SyntheticFallbackLevels(unittest.TestCase):
    """The new fallback mechanics, pinned against a small hand-built table
    so they don't depend on what next week's real fleet pull contains."""

    def _table(self) -> dict:
        return {
            "schema": "fleet_timings/1",
            "wall_sec": "seconds",
            "levels": {
                "cell": {
                    "dims": ["engine", "mode", "tier", "frames", "chip_family", "ram_gb", "speed"],
                    "cells": {
                        # Bug shape: M4 Max's only high_720p/241f samples are
                        # ALL on a 128 GB (pro-tier) install.
                        "ltx|t2v|high_720p|241.0|M4 Max|128.0|any": {
                            "p25_sec": 1800.0, "p50_sec": 1800.0, "p75_sec": 1800.0, "n": 13},
                        # A DIFFERENT chip (M4 Pro), but the SAME RAM tier
                        # (64 GB -> standard) as the Mac under test, at a
                        # shorter length (121f) -- the ram_tier_scaled rung.
                        "ltx|t2v|high_720p|121.0|M4 Pro|64.0|any": {
                            "p25_sec": 900.0, "p50_sec": 900.0, "p75_sec": 900.0, "n": 3},
                        # SAME chip, SAME ram tier as target (56 GB is also
                        # "standard"), exact frames -- should win over
                        # everything below it once it exists.
                        "ltx|t2v|balanced|241.0|M4 Max|56.0|any": {
                            "p25_sec": 200.0, "p50_sec": 220.0, "p75_sec": 260.0, "n": 5},
                    },
                },
                "chip": {
                    "dims": ["engine", "mode", "tier", "frames", "chip_family", "speed"],
                    "cells": {
                        # Precomputed RAM-blind pooled cell that mirrors the
                        # 128 GB-only cell above exactly (as the real build
                        # script would produce when that's the only data).
                        "ltx|t2v|high_720p|241.0|M4 Max|any": {
                            "p25_sec": 1800.0, "p50_sec": 1800.0, "p75_sec": 1800.0, "n": 13},
                        # A chip with NO "cell"-level breakdown at all --
                        # nothing to contradict it, so it must still be
                        # trusted (matches test_fleet_timings.py's pinned
                        # `test_falls_back_to_chip_level_when_ram_differs`).
                        "ltx|t2v|balanced|121.0|M2 Pro|any": {
                            "p25_sec": 300.0, "p50_sec": 360.0, "p75_sec": 420.0, "n": 20},
                    },
                },
                "model": {
                    "dims": ["engine", "mode", "tier", "frames", "speed"],
                    "cells": {
                        "ltx|t2v|high_720p|241.0|any": {
                            "p25_sec": 1200.0, "p50_sec": 1800.0, "p75_sec": 1800.0, "n": 23},
                        "ltx|t2v|balanced|121.0|any": {
                            "p25_sec": 150.0, "p50_sec": 200.0, "p75_sec": 260.0, "n": 500},
                    },
                },
            },
        }

    def setUp(self):
        self._patch = mock.patch.object(P, "FLEET_TIMINGS", self._table())
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_chip_level_skipped_when_backing_cells_are_a_different_ram_tier(self):
        """The exact bug: a 64 GB M4 Max must not get the 128 GB-only
        pooled 'chip' number."""
        r = P.fleet_estimate_range("ltx", "t2v", "high_720p", 241,
                                   chip="M4 Max", ram=64.0)
        self.assertIsNotNone(r)
        self.assertNotEqual(r["source"], "chip")
        self.assertNotAlmostEqual(r["p50_min"], 30.0, delta=0.01)

    def test_falls_through_to_ram_tier_scaled_with_chip_adjustment(self):
        """No same-chip same-tier data at all -> the M4 Pro/64 GB/121f cell
        wins, chip-adjusted (M4 Pro is 1.7x slower than M4 Max per
        HW_SPEED_FACTOR_LTX) and frame-scaled to 241f."""
        r = P.fleet_estimate_range("ltx", "t2v", "high_720p", 241,
                                   chip="M4 Max", ram=64.0)
        self.assertEqual(r["source"], "ram_tier_scaled")
        # Chip-adjusted-and-scaled must land well under the untouched
        # 128 GB pooled number (30 min) -- the whole point of the fix.
        self.assertLess(r["p50_min"], 25.0)

    def test_chip_ram_tier_wins_when_it_exists(self):
        """Same chip, a DIFFERENT RAM value but the same tier (56 GB is
        "standard", same as the 64 GB target) beats every coarser level."""
        r = P.fleet_estimate_range("ltx", "t2v", "balanced", 241,
                                   chip="M4 Max", ram=64.0)
        self.assertEqual(r["source"], "chip_ram_tier")
        self.assertEqual(r["n"], 5)
        self.assertAlmostEqual(r["p50_min"], 220.0 / 60.0, places=2)

    def test_pro_tier_mac_still_gets_its_own_chip_level_number(self):
        """A 128 GB Mac IS the RAM tier the "chip" cell was built from --
        the gate must not block a Mac the pooled number actually describes."""
        r = P.fleet_estimate_range("ltx", "t2v", "high_720p", 241,
                                   chip="M4 Max", ram=128.0)
        # cell_scaled and chip_ram_tier both look at 241f/128GB first and
        # would hit the SAME row as "chip" (n=13) via a different path --
        # any of "cell"/"chip_ram_tier"/"chip" is correct here as long as
        # the number itself is the real 128 GB one, unscaled.
        self.assertIsNotNone(r)
        self.assertAlmostEqual(r["p50_min"], 30.0, places=2)

    def test_existing_chip_fallback_unaffected_when_no_cell_data_exists(self):
        """Pinned parity with test_fleet_timings.py's
        test_falls_back_to_chip_level_when_ram_differs: a chip with NO
        "cell" rows at all must still fall back to the RAM-blind "chip"
        level exactly as before -- the gate only fires when it has
        evidence to contradict the pooled number, never on silence."""
        r = P.fleet_estimate_range("ltx", "t2v", "balanced", 121,
                                   chip="M2 Pro", ram=999.0)
        self.assertEqual(r["source"], "chip")
        self.assertEqual(r["n"], 20)

    def test_model_level_still_reachable_as_last_resort(self):
        """When NOTHING ram-tier-aware exists for this chip or any chip,
        the fully pooled "model" level remains the final real-data rung
        before the cost model -- unchanged behavior."""
        table = self._table()
        del table["levels"]["cell"]["cells"]["ltx|t2v|high_720p|121.0|M4 Pro|64.0|any"]
        del table["levels"]["chip"]["cells"]["ltx|t2v|high_720p|241.0|M4 Max|any"]
        with mock.patch.object(P, "FLEET_TIMINGS", table):
            r = P.fleet_estimate_range("ltx", "t2v", "high_720p", 241,
                                       chip="M4 Max", ram=64.0)
        self.assertEqual(r["source"], "model")
        self.assertEqual(r["n"], 23)


class CombineAndChipAdjustHelpers(unittest.TestCase):
    def test_combine_weights_by_sample_count(self):
        combined = P._combine_fleet_cells([
            {"p25_sec": 100.0, "p50_sec": 100.0, "p75_sec": 100.0, "n": 90},
            {"p25_sec": 1000.0, "p50_sec": 1000.0, "p75_sec": 1000.0, "n": 10},
        ])
        self.assertEqual(combined["n"], 100)
        # Weighted toward the 90-sample cell, not a plain average of 550.
        self.assertLess(combined["p50_sec"], 300.0)

    def test_far_apart_cohorts_give_a_range_that_holds_both(self):
        # 4.17.3 review: averaging quartiles turned two equal cohorts at
        # 60-90 s and 600-900 s into ~330-495 s, a range containing neither.
        combined = P._combine_fleet_cells([
            {"p25_sec": 60.0, "p50_sec": 60.0, "p75_sec": 90.0, "n": 10},
            {"p25_sec": 600.0, "p50_sec": 600.0, "p75_sec": 900.0, "n": 10},
        ])
        self.assertLessEqual(combined["p25_sec"], 90.0)
        self.assertGreaterEqual(combined["p75_sec"], 600.0)
        for f in ("p25_sec", "p50_sec", "p75_sec"):
            self.assertIn(combined[f], (60.0, 90.0, 600.0, 900.0))

    def test_close_cohorts_stay_tight(self):
        combined = P._combine_fleet_cells([
            {"p25_sec": 420.0, "p50_sec": 600.0, "p75_sec": 600.0, "n": 12},
            {"p25_sec": 420.0, "p50_sec": 420.0, "p75_sec": 600.0, "n": 5},
        ])
        self.assertEqual((combined["p25_sec"], combined["p75_sec"]), (420.0, 600.0))
        self.assertEqual(combined["n"], 17)

    def test_chip_adjust_ratio_is_a_noop_for_the_same_chip(self):
        self.assertEqual(P._fleet_chip_adjust_ratio("ltx", "M4 Max", "M4 Max"), 1.0)

    def test_chip_adjust_ratio_uses_the_shared_speed_table(self):
        # M4 Pro is 1.7x SLOWER than M4 Max (HW_SPEED_FACTOR_LTX): a time
        # measured on an M4 Pro should be scaled DOWN to price an M4 Max.
        ratio = P._fleet_chip_adjust_ratio("ltx", "M4 Pro", "M4 Max")
        self.assertAlmostEqual(ratio, 1.0 / 1.7, places=3)

    def test_chip_adjust_ratio_unknown_chip_is_a_noop(self):
        self.assertEqual(
            P._fleet_chip_adjust_ratio("ltx", "SomeFutureChip", "M4 Max"), 1.0)


if __name__ == "__main__":
    unittest.main()
