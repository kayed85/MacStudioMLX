#!/usr/bin/env python3
"""H3 was leaving the user one gigabyte of their own Mac.

The runner defaults to `--wired-gb 50` / `--memory-gb 58` and clamps with

    memory_bytes = min(memory_gb * GiB, device.memory_size - 1 GiB)

so on any Mac below 59 GB the 58 never binds and the clamp does — MLX is
allowed everything except ONE gigabyte. Measured:

    32 GB -> 31.0 GB limit, 1.0 GB left     48 GB -> 47.0 GB limit, 1.0 GB left
    36 GB -> 35.0 GB limit, 1.0 GB left     64 GB -> 58.0 GB limit, 6.0 GB left

64 GB is the machine the defaults were written on, which is why nobody saw it.
The owner's words: "even if it is tight, when it's tight, you cannot use your
Mac, so this is bad."

It costs nothing to fix, because H3 never needed that memory: measured peak is
25.63 GiB on Q8 and 42.17 GiB on bf16. The limit was standing in front of
headroom without ever being the binding constraint.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ["LTX_STATE_DIR"] = str(Path(tempfile.mkdtemp(prefix="phos-h3-mem-")))
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8305")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402

PANEL_SRC = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")

# Measured 2026-08-29 on the staged runner, 640x384 73f and 768x416 243f.
H3_PEAK_Q8_GIB = 25.63
H3_PEAK_BF16_GIB = 42.17


class EveryMacKeepsUsableHeadroom(unittest.TestCase):

    def test_no_machine_is_left_with_the_old_one_gigabyte(self):
        for ram in (32, 36, 48, 64, 96, 128, 256):
            with self.subTest(ram=ram):
                mem, _ = P.h3_memory_budget(ram)
                self.assertGreaterEqual(
                    ram - mem, 6.0,
                    f"a {ram} GB Mac keeps only {ram - mem:.1f} GB")

    def test_headroom_grows_with_the_machine(self):
        small = 32 - P.h3_memory_budget(32)[0]
        big = 128 - P.h3_memory_budget(128)[0]
        self.assertGreater(big, small)

    def test_wired_is_below_the_overall_limit(self):
        """Wired memory cannot be paged out, so it must be the tighter of the
        two or the limit is meaningless."""
        for ram in (32, 48, 64, 128):
            mem, wired = P.h3_memory_budget(ram)
            with self.subTest(ram=ram):
                self.assertLess(wired, mem)

    def test_the_budget_still_clears_h3s_measured_peak(self):
        """Headroom must not be bought by making renders fail. The smallest Mac
        we serve H3 on is the 46 GB Q8 floor, i.e. a 48 GB machine."""
        mem48, _ = P.h3_memory_budget(48)
        self.assertGreater(mem48, H3_PEAK_Q8_GIB + 4)
        mem64, _ = P.h3_memory_budget(64)
        self.assertGreater(mem64, H3_PEAK_BF16_GIB + 4)

    def test_unknown_ram_disables_the_override(self):
        """Better the runner's own defaults than a number computed from zero."""
        self.assertEqual(P.h3_memory_budget(0), (0.0, 0.0))
        self.assertEqual(P.h3_memory_budget(-1), (0.0, 0.0))

    def test_a_tiny_machine_still_gets_a_sane_floor(self):
        mem, wired = P.h3_memory_budget(8)
        self.assertGreaterEqual(mem, 8.0)
        self.assertGreaterEqual(wired, 4.0)


class TheQ8FloorMatchesTheMeasurement(unittest.TestCase):
    """The floor must follow the peak, not a guard band round a marketing number."""

    def test_the_floor_clears_the_measured_peak_plus_real_headroom(self):
        floor = P.H3_MIN_RAM_GB_Q8
        peak_gb = H3_PEAK_Q8_GIB * (1024 ** 3) / (1000 ** 3)   # GiB -> GB
        mem, _ = P.h3_memory_budget(floor)
        self.assertGreater(
            mem, peak_gb,
            f"a {floor} GB Mac is offered H3 but its budget ({mem} GB) is "
            f"below the measured peak ({peak_gb:.1f} GB)")
        self.assertGreaterEqual(floor - mem, 6.0, "no headroom left for the user")

    def test_the_floor_is_not_set_below_what_was_demonstrated(self):
        """32 GB ran in SIMULATION only, on a 64 GB machine with a flag. That
        is not evidence a real 32 GB Mac survives, so the floor stays above it."""
        self.assertGreater(P.H3_MIN_RAM_GB_Q8, 32.0)

    def test_q8_floor_is_below_the_bf16_floor(self):
        self.assertLess(P.H3_MIN_RAM_GB_Q8, P.H3_MIN_RAM_GB)


class ItIsProbedAndWired(unittest.TestCase):

    def test_the_probe_asks_for_both_flags(self):
        import inspect
        src = inspect.getsource(P.h3_supports_memory_limits)
        self.assertIn('_h3_runner_has_flag("--memory-gb")', src)
        self.assertIn('_h3_runner_has_flag("--wired-gb")', src)

    def test_the_argv_passes_both_and_is_gated(self):
        self.assertIn('cmd += ["--memory-gb", str(_mem_gb), "--wired-gb", str(_wired_gb)]',
                      PANEL_SRC)
        self.assertIn("if h3_supports_memory_limits():", PANEL_SRC)

    def test_a_zero_budget_passes_nothing(self):
        self.assertIn("if _mem_gb > 0:", PANEL_SRC)


if __name__ == "__main__":
    unittest.main()
