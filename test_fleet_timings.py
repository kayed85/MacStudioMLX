"""Fleet timings: real fleet wall clocks, shown as a range, with a
documented fallback hierarchy and this install's own calibration layered
on top only once it has earned it.

Owner ruling (2026-09-29): "If we have the fleet data, we can provide
proper time estimations, or at least a range." scripts/fleet_timings_build.py
pulls real render_completed wall clocks from PostHog (the owner's own
installs excluded) into data/fleet_timings.json at three levels of
granularity (cell / chip / model); mlx_ltx_panel.py's
fleet_estimate_range() / fleet_calibrated_range() walk that table at
runtime, falling back to the panel's existing chip-factor cost model when
the fleet has nothing to say. This suite pins the LOADER and the FALLBACK
ORDER against a synthetic table (never the real, git-committed one — a
real-data assertion here would break every time the table is rebuilt) plus
the range-formatting and calibration-scaling helpers against the real
functions.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("LTX_STATE_DIR", tempfile.mkdtemp(prefix="phos-fleet-"))
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402


def _synthetic_table() -> dict:
    """A small, hand-built fleet_timings table — the shape
    scripts/fleet_timings_build.py produces, with known cells at each
    level so the fallback order can be asserted exactly."""
    return {
        "schema": "fleet_timings/1",
        # Exact seconds, not ladder lower edges: this suite pins the lookup
        # order; the bucket reading is pinned in test_codex_4170_est (EST-4).
        "wall_sec": "seconds",
        "levels": {
            "cell": {
                "dims": ["engine", "mode", "tier", "frames", "chip_family", "ram_gb", "speed"],
                "cells": {
                    # exact cell for (ltx, t2v, balanced, 121f, M4 Max, 64GB)
                    "ltx|t2v|balanced|121.0|M4 Max|64.0|any": {
                        "p25_sec": 120.0, "p50_sec": 150.0, "p75_sec": 180.0, "n": 50},
                    # a DIFFERENT length, same chip+ram+tier — for cell_scaled
                    "ltx|t2v|balanced|241.0|M4 Max|64.0|any": {
                        "p25_sec": 240.0, "p50_sec": 280.0, "p75_sec": 320.0, "n": 40},
                    # H3 fast vs best at the same shape
                    "h3|t2v|draft_3s|73.0|M4 Max|64.0|fast": {
                        "p25_sec": 60.0, "p50_sec": 90.0, "p75_sec": 90.0, "n": 10},
                    "h3|t2v|draft_3s|73.0|M4 Max|64.0|best": {
                        "p25_sec": 180.0, "p50_sec": 210.0, "p75_sec": 240.0, "n": 8},
                },
            },
            "chip": {
                "dims": ["engine", "mode", "tier", "frames", "chip_family", "speed"],
                "cells": {
                    "ltx|t2v|balanced|121.0|M2 Pro|any": {
                        "p25_sec": 300.0, "p50_sec": 360.0, "p75_sec": 420.0, "n": 20},
                },
            },
            "model": {
                "dims": ["engine", "mode", "tier", "frames", "speed"],
                "cells": {
                    "ltx|t2v|balanced|121.0|any": {
                        "p25_sec": 150.0, "p50_sec": 200.0, "p75_sec": 260.0, "n": 500},
                    "ltx|t2v|quick|73.0|any": {
                        "p25_sec": 50.0, "p50_sec": 60.0, "p75_sec": 70.0, "n": 100},
                },
            },
        },
    }


class WithSyntheticTable(unittest.TestCase):
    def setUp(self):
        self._patch = mock.patch.object(P, "FLEET_TIMINGS", _synthetic_table())
        self._patch.start()
        self.addCleanup(self._patch.stop)
        # Calibration must not bleed in from a real state dir.
        self._calib_dir = Path(tempfile.mkdtemp(prefix="phos-fleet-calib-"))
        self._calib_patch = mock.patch.object(
            P, "_eta_calibration_path", lambda: self._calib_dir / "eta_calibration.json")
        self._calib_patch.start()
        self.addCleanup(self._calib_patch.stop)


class FallbackOrder(WithSyntheticTable):
    def test_exact_cell_wins_first(self):
        r = P.fleet_estimate_range("ltx", "t2v", "balanced", 121,
                                   chip="M4 Max", ram=64.0)
        self.assertEqual(r["source"], "cell")
        self.assertEqual(r["n"], 50)
        self.assertAlmostEqual(r["p50_min"], 2.5, places=2)  # 150s

    def test_falls_back_to_scaled_same_chip_different_length(self):
        """No exact cell at 169f on this chip+ram, but 121f and 241f both
        exist — nearest (121f) wins and is scaled by the cost model's own
        ratio between 169f and 121f (both balanced canvas)."""
        r = P.fleet_estimate_range("ltx", "t2v", "balanced", 169,
                                   chip="M4 Max", ram=64.0)
        self.assertEqual(r["source"], "cell_scaled")
        self.assertEqual(r["n"], 50)  # from the 121f cell (nearer than 241f)
        base_121 = P.ltx_estimate_minutes(
            *[P.LTX_QUALITIES["balanced"][k] for k in ("width", "height")],
            121, P.LTX_QUALITIES["balanced"]["stage1"], P.LTX_QUALITIES["balanced"]["stage2"])
        base_169 = P.ltx_estimate_minutes(
            *[P.LTX_QUALITIES["balanced"][k] for k in ("width", "height")],
            169, P.LTX_QUALITIES["balanced"]["stage1"], P.LTX_QUALITIES["balanced"]["stage2"])
        expected_ratio = base_169 / base_121
        self.assertAlmostEqual(r["p50_min"], round(2.5 * expected_ratio, 2), delta=0.05)

    def test_falls_back_to_chip_level_when_ram_differs(self):
        r = P.fleet_estimate_range("ltx", "t2v", "balanced", 121,
                                   chip="M2 Pro", ram=999.0)
        self.assertEqual(r["source"], "chip")
        self.assertEqual(r["n"], 20)

    def test_falls_back_to_model_level_when_chip_is_unknown(self):
        r = P.fleet_estimate_range("ltx", "t2v", "balanced", 121,
                                   chip="SomeFutureChip", ram=999.0)
        self.assertEqual(r["source"], "model")
        self.assertEqual(r["n"], 500)

    def test_none_when_nothing_matches_at_any_level(self):
        r = P.fleet_estimate_range("ltx", "t2v", "high_720p", 481,
                                   chip="SomeFutureChip", ram=999.0)
        self.assertIsNone(r)

    def test_h3_fast_and_best_are_independent_cells(self):
        fast = P.fleet_estimate_range("h3", "t2v", "draft_3s", 73,
                                      chip="M4 Max", ram=64.0, speed="fast")
        best = P.fleet_estimate_range("h3", "t2v", "draft_3s", 73,
                                      chip="M4 Max", ram=64.0, speed="best")
        self.assertLess(fast["p50_min"], best["p50_min"])


class RangeFormatting(unittest.TestCase):
    def test_tight_range_collapses_to_one_number(self):
        self.assertEqual(P._fmt_eta_range(3.0, 3.4), P._fmt_eta(3.4))

    def test_wide_range_shows_both_ends(self):
        self.assertEqual(P._fmt_eta_range(12.0, 18.0), "~12–18 min")

    def test_batch_suffix_rides_on_the_high_end(self):
        self.assertIn("batch", P._fmt_eta_range(20.0, 30.0))

    def test_hours_scale_uses_the_hour_form_on_both_ends(self):
        out = P._fmt_eta_range(65.0, 130.0)
        self.assertIn("h", out)
        self.assertIn("–", out)

    def test_never_shows_a_backwards_range(self):
        # p25 > p75 can happen with a thin, noisy sample; the function must
        # not print a range that reads lo > hi.
        out = P._fmt_eta_range(20.0, 5.0)
        self.assertNotIn("20–5", out)


class CalibrationScaling(WithSyntheticTable):
    def test_no_calibration_before_two_samples(self):
        P._record_eta_calibration("fleet_ltx", 2.0)  # only ONE sample
        r = P.fleet_calibrated_range("ltx", "t2v", "balanced", 121,
                                     chip="M4 Max", ram=64.0)
        self.assertAlmostEqual(r["p50_min"], 2.5, places=2)  # unscaled

    def test_calibration_applies_once_two_samples_exist(self):
        P._record_eta_calibration("fleet_ltx", 2.0)
        P._record_eta_calibration("fleet_ltx", 2.0)
        r = P.fleet_calibrated_range("ltx", "t2v", "balanced", 121,
                                     chip="M4 Max", ram=64.0)
        self.assertAlmostEqual(r["p50_min"], 5.0, places=2)  # 2.5 * 2.0
        self.assertIn("adjusted for this Mac's own renders", r["basis"])

    def test_calibration_scales_all_three_percentiles(self):
        P._record_eta_calibration("fleet_ltx", 1.5)
        P._record_eta_calibration("fleet_ltx", 1.5)
        r = P.fleet_calibrated_range("ltx", "t2v", "balanced", 121,
                                     chip="M4 Max", ram=64.0)
        self.assertAlmostEqual(r["p25_min"], 2.0 * 1.5, places=2)
        self.assertAlmostEqual(r["p75_min"], 3.0 * 1.5, places=2)

    def test_carries_a_formatted_range_and_midpoint(self):
        r = P.fleet_calibrated_range("ltx", "t2v", "balanced", 121,
                                     chip="M4 Max", ram=64.0)
        self.assertIn("eta_range", r)
        self.assertIn("eta_mid", r)

    def test_none_passes_through_cleanly(self):
        r = P.fleet_calibrated_range("ltx", "t2v", "nonexistent-tier", 999999,
                                     chip="NoSuchChip", ram=1.0)
        self.assertIsNone(r)


class KeyFormatMatchesTheBuildScript(unittest.TestCase):
    def test_fleet_key_matches_build_scripts_key_for(self):
        """The runtime key-builder and the offline build script's key
        builder must produce byte-identical keys for the same row, or every
        lookup silently misses."""
        sys.path.insert(0, str(ROOT / "scripts"))
        import fleet_timings_build as B  # noqa: PLC0415
        rec = {"engine": "ltx", "mode": "t2v", "tier": "balanced",
               "frames": 121.0, "chip_family": "M4 Max", "ram_gb": 64.0, "speed": ""}
        build_key = B._key_for(rec, ["engine", "mode", "tier", "frames",
                                     "chip_family", "ram_gb", "speed"])
        runtime_key = P._fleet_key("ltx", "t2v", "balanced", 121.0,
                                   chip="M4 Max", ram=64.0, speed="")
        self.assertEqual(build_key, runtime_key)


class TheLoaderNeverRaises(unittest.TestCase):
    def test_missing_file_returns_empty_dict(self):
        with mock.patch.object(P, "FLEET_TIMINGS_PATH", Path("/no/such/file.json")):
            self.assertEqual(P._load_fleet_timings(), {})

    def test_malformed_json_returns_empty_dict(self):
        bad = Path(tempfile.mkdtemp(prefix="phos-fleet-bad-")) / "fleet_timings.json"
        bad.write_text("{not json", encoding="utf-8")
        with mock.patch.object(P, "FLEET_TIMINGS_PATH", bad):
            self.assertEqual(P._load_fleet_timings(), {})

    def test_the_real_shipped_table_loads_without_raising(self):
        """The actual committed data/fleet_timings.json — schema and
        structural sanity only, never a value assertion (real data changes
        every rebuild)."""
        data = P._load_fleet_timings()
        if not data:
            self.skipTest("no data/fleet_timings.json shipped in this checkout")
        self.assertEqual(data.get("schema"), "fleet_timings/1")
        self.assertIn("levels", data)
        for level_name in ("cell", "chip", "model"):
            self.assertIn(level_name, data["levels"])
            self.assertIn("cells", data["levels"][level_name])


if __name__ == "__main__":
    unittest.main()
