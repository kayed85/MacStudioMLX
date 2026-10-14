"""VC-01 [P0]: every LTX estimate skipped the per-chip speed factor.

Pinned by the flow review (2026-09-29, report_video-core.txt VC-01): boot as
a 16 GB "Apple M3" and the Shot setup chips show the SAME "~3 min" the panel
shows on a real M4 Max — because `_build_ltx_tiers` returned the measured
row's raw M4 Max seconds (`hit[0]`) unmultiplied, and the anchored branch's
own `eta_min / model_5s` ratio cancelled the factor out (both sides came
from `ltx_estimate_minutes`, which carried the identical factor). H3's
tiers already multiplied their measured rows by the chip factor
(`_h3_speed_factor`); LTX's never did.

The fix adds three things, all exercised here:
  1. the chip factor actually reaches both the measured and the anchored
     LTX tier cells (mirrors the fix already proven for H3);
  2. a low-RAM factor (`_ltx_ram_factor`), the LTX equivalent of
     `_h3_ram_factor`, for Macs LOW_RAM_STREAM pushes onto the
     disk-streamed DiT load;
  3. per-install self-calibration: a running median of actual/estimate,
     clamped 0.5-3x, folded into `_hw_speed_factor` so it corrects BOTH
     engines' estimates without a second call site.

H3-03 (48 GB M5 Pro Best estimates ~30% low) is pinned alongside: the
table's M5 Pro entry was a fleet bucket shared with M5 Max.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("LTX_STATE_DIR", tempfile.mkdtemp(prefix="phos-vc01-"))
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402


class _Mac:
    """Rebuild the LTX tier table as a given Mac (chip + RAM) would at boot,
    with this install's calibration state isolated to a throwaway file so
    one test's recorded ratios can never leak into another's."""

    def __init__(self, ram_gb: float, chip: str, calib_path: Path | None = None):
        self.ram_gb, self.chip = ram_gb, chip
        self.calib_path = calib_path or Path(
            tempfile.mkdtemp(prefix="phos-vc01-calib-")) / "eta_calibration.json"

    def __enter__(self):
        self.saved = (P.SYSTEM_RAM_GB, P._HW_CHIP_FAMILY,
                      os.environ.pop("PHOSPHENE_SPEED_FACTOR", None))
        self._path_patch = mock.patch.object(
            P, "_eta_calibration_path", lambda: self.calib_path)
        self._path_patch.start()
        P.SYSTEM_RAM_GB = self.ram_gb
        P._HW_CHIP_FAMILY = self.chip
        # ...on the full, unclamped canvases (issue #90). The quality table
        # bakes the BOOTING Mac's t2v_max_dim in at import, so on a <48 GB
        # Mac running this suite every Mac below was priced on clamped,
        # non-native canvases and balanced_5s stopped being a measured cell.
        # Pinned to the standard tier rather than derived from `ram_gb`:
        # these tests vary chip and RAM FACTOR at a fixed canvas, which is
        # how the measured rows were taken.
        tier = "standard"
        self._tier_patches = [
            mock.patch.object(P, "SYSTEM_TIER", tier),
            mock.patch.object(P, "SYSTEM_CAPS", P.CAPABILITIES[tier]),
        ]
        for patch in self._tier_patches:
            patch.start()
        self._tier_patches.append(
            mock.patch.object(P, "LTX_QUALITIES", P._ltx_qualities()))
        self._tier_patches[-1].start()
        return P._build_ltx_tiers()

    def __exit__(self, *exc):
        for patch in reversed(self._tier_patches):
            patch.stop()
        self._path_patch.stop()
        P.SYSTEM_RAM_GB, P._HW_CHIP_FAMILY, env = self.saved
        if env is not None:
            os.environ["PHOSPHENE_SPEED_FACTOR"] = env


class MeasuredAndAnchoredCellsCarryTheChipFactor(unittest.TestCase):
    def test_m3_balanced_5s_is_at_least_4x_the_m4_max_cell(self):
        """The gate the finding itself proposes."""
        with _Mac(64.0, "M4 Max") as t64:
            m4max = t64["balanced_5s"]["eta_min"]
        with _Mac(64.0, "M3") as t3:
            m3 = t3["balanced_5s"]["eta_min"]
        self.assertGreaterEqual(m3, m4max * 4)

    def test_measured_row_scales_with_chip(self):
        """balanced_5s is a MEASURED cell (LTX_MEASURED_ETA) — hit[0] must
        not be returned raw."""
        with _Mac(64.0, "M4 Max") as t64:
            cell64 = t64["balanced_5s"]
            self.assertTrue(cell64["eta_measured"])
        with _Mac(64.0, "M2") as t2:  # HW_SPEED_FACTOR_LTX["M2"] == 5.0
            cell2 = t2["balanced_5s"]
        self.assertAlmostEqual(cell2["eta_min"], cell64["eta_min"] * 5.0, delta=0.05)

    def test_anchored_row_scales_with_chip_too(self):
        """10s/20s cells have no direct measurement and are anchored off the
        quality's 5s row — the ratio used to cancel the factor out."""
        with _Mac(64.0, "M4 Max") as t64:
            cell64 = t64.get("balanced_10s") or t64.get("quick_20s")
            key = "balanced_10s" if "balanced_10s" in t64 else "quick_20s"
            self.assertFalse(cell64["eta_measured"])
        with _Mac(64.0, "M1") as t1:  # HW_SPEED_FACTOR_LTX["M1"] == 4.2
            cell1 = t1[key]
        self.assertAlmostEqual(cell1["eta_min"], cell64["eta_min"] * 4.2, delta=0.05)

    def test_unknown_chip_is_unchanged_from_before(self):
        with _Mac(64.0, "unknown") as t:
            self.assertGreater(t["balanced_5s"]["eta_min"], 0)


class LowRamFactor(unittest.TestCase):
    def test_low_ram_mac_prices_slower_than_the_same_chip_at_full_ram(self):
        with _Mac(64.0, "M4 Max") as t64:
            full = t64["balanced_5s"]["eta_min"]
        with _Mac(16.0, "M4 Max") as t16:
            low = t16["balanced_5s"]["eta_min"]
        self.assertAlmostEqual(low, full * P.LTX_LOWRAM_FACTOR, delta=0.05)

    def test_above_the_low_ram_threshold_is_unaffected(self):
        self.assertEqual(P._ltx_ram_factor(ram_gb=48.0), 1.0)
        self.assertEqual(P._ltx_ram_factor(ram_gb=P.LOW_RAM_STREAM_RAM_GB + 1), 1.0)
        self.assertGreater(P._ltx_ram_factor(ram_gb=16.0), 1.0)

    def test_explicit_override_replaces_the_ram_factor_too(self):
        os.environ["PHOSPHENE_SPEED_FACTOR"] = "2.0"
        try:
            self.assertEqual(P._ltx_ram_factor(ram_gb=8.0), 1.0)
        finally:
            del os.environ["PHOSPHENE_SPEED_FACTOR"]


class SelfCalibration(unittest.TestCase):
    def setUp(self):
        self.calib_dir = Path(tempfile.mkdtemp(prefix="phos-vc01-calib-"))
        self.calib_path = self.calib_dir / "eta_calibration.json"
        self._patch = mock.patch.object(P, "_eta_calibration_path", lambda: self.calib_path)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_no_data_means_no_correction(self):
        self.assertEqual(P._eta_calibration_factor("ltx"), 1.0)

    def test_recording_a_ratio_moves_the_factor(self):
        P._record_eta_calibration("ltx", 2.0)
        self.assertAlmostEqual(P._eta_calibration_factor("ltx"), 2.0, delta=0.001)

    def test_median_of_several_samples(self):
        for r in (1.5, 2.0, 2.5):
            P._record_eta_calibration("ltx", r)
        self.assertAlmostEqual(P._eta_calibration_factor("ltx"), 2.0, delta=0.001)

    def test_clamped_to_half_and_triple(self):
        P._record_eta_calibration("ltx", 0.05)
        self.assertEqual(P._eta_calibration_factor("ltx"), P.ETA_CALIBRATION_MIN)
        P._record_eta_calibration("ltx", 50.0)
        # median of [0.05, 50.0] is ~25, still clamps to the ceiling
        self.assertEqual(P._eta_calibration_factor("ltx"), P.ETA_CALIBRATION_MAX)

    def test_engines_are_independent(self):
        P._record_eta_calibration("ltx", 2.0)
        self.assertEqual(P._eta_calibration_factor("h3"), 1.0)

    def test_oldest_samples_drop_off(self):
        for _ in range(P.ETA_CALIBRATION_SAMPLES_MAX):
            P._record_eta_calibration("ltx", 1.0)
        P._record_eta_calibration("ltx", 3.0)
        data = json.loads(self.calib_path.read_text())
        self.assertEqual(len(data["ltx"]), P.ETA_CALIBRATION_SAMPLES_MAX)
        self.assertIn(3.0, data["ltx"])

    def test_folds_into_hw_speed_factor(self):
        with mock.patch.object(P, "_hw_chip_family", lambda: "M4 Max"):
            before = P._hw_speed_factor("ltx")
            P._record_eta_calibration("ltx", 1.5)
            after = P._hw_speed_factor("ltx")
        self.assertAlmostEqual(after, before * 1.5, delta=0.001)

    def test_explicit_override_skips_calibration_entirely(self):
        P._record_eta_calibration("ltx", 3.0)
        os.environ["PHOSPHENE_SPEED_FACTOR"] = "1.0"
        try:
            self.assertEqual(P._hw_speed_factor("ltx"), 1.0)
        finally:
            del os.environ["PHOSPHENE_SPEED_FACTOR"]

    def test_record_from_job_computes_the_right_ratio(self):
        """A finished balanced/5s job that took exactly 2x the chip-only
        prediction should record a ~2.0 ratio."""
        # Booted as an uncalibrated 64 GB M4 Max (issue #90). The baseline is
        # read from the tier table priced at IMPORT and the factor it was
        # priced with (_ETA_CAL_PRICED), so with the live module tables this
        # recorded 2.6 on a Mac with a learned 1.3 correction and 1.18 on an
        # M4 Pro: the mocks below never reached the price being compared.
        with _Mac(64.0, "M4 Max", calib_path=self.calib_path) as tiers, \
             mock.patch.object(P, "LTX_TIERS", tiers), \
             mock.patch.dict(P._ETA_CAL_PRICED, {"ltx": 1.0}), \
             mock.patch.object(P, "_hw_chip_family", lambda: "M4 Max"):
            base_min = tiers["balanced_5s"]["eta_min"]
            job = {"status": "done", "elapsed_sec": base_min * 60.0 * 2.0,
                   "params": {"engine": "ltx", "quality": "balanced",
                              "frames": tiers["balanced_5s"]["frames"]}}
            P._record_eta_calibration_from_job(job)
        self.assertAlmostEqual(P._eta_calibration_factor("ltx"), 2.0, delta=0.05)

    def test_record_from_job_ignores_unfinished_or_unmapped_jobs(self):
        P._record_eta_calibration_from_job({"status": "failed", "elapsed_sec": 999,
                                            "params": {"engine": "ltx"}})
        self.assertEqual(P._eta_calibration_factor("ltx"), 1.0)
        P._record_eta_calibration_from_job({"status": "done", "elapsed_sec": 999,
                                            "params": {"engine": "ltx", "quality": "balanced",
                                                       "frames": 999999}})
        self.assertEqual(P._eta_calibration_factor("ltx"), 1.0)

    def test_record_from_job_never_raises(self):
        P._record_eta_calibration_from_job(None)
        P._record_eta_calibration_from_job({})
        P._record_eta_calibration_from_job({"status": "done"})


class H3M5ProFactor(unittest.TestCase):
    def test_m5_pro_raised_toward_the_measured_ratio(self):
        """Field data: Best ran ~1.29-1.34x the panel's own estimate at the
        old 0.6 factor, flat across three cells — i.e. the real factor is
        close to 0.6 * 1.3 =~ 0.78. Table now prices closer to that."""
        self.assertGreaterEqual(P.HW_SPEED_FACTOR_H3["M5 Pro"], 0.75)
        self.assertLessEqual(P.HW_SPEED_FACTOR_H3["M5 Pro"], 0.85)


if __name__ == "__main__":
    unittest.main()
