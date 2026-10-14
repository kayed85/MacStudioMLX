#!/usr/bin/env python3
"""FILM-56: an up-front, plain-language warning when this Mac is unlikely to
clear the planner's own memory footprint, instead of a 900 s wait ending in
an out-of-memory failure with nothing said beforehand.

`storyboard_planner_ram_status()` is the one function both the boot payload
(`storyboard_status()["ram_status"]`, read by the client as
`SB_BOOT.ram_status`) and any future server-side check read — so the two can
never disagree about what this Mac can do.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as panel                                        # noqa: E402


class PlannerRamStatus(unittest.TestCase):
    def test_a_roomy_mac_gets_no_message(self):
        with mock.patch.object(panel, "SYSTEM_RAM_GB", 64.0):
            status = panel.storyboard_planner_ram_status()
        self.assertTrue(status["ok"])
        self.assertEqual(status["message"], "")
        self.assertEqual(status["ram_gb"], 64.0)

    def test_a_tight_mac_gets_a_plain_warning(self):
        with mock.patch.object(panel, "SYSTEM_RAM_GB", 8.0):
            status = panel.storyboard_planner_ram_status()
        self.assertFalse(status["ok"])
        self.assertIn("8 GB", status["message"])
        # Naming Gemma specifically would go stale the moment
        # LTX_STORYBOARD_PLANNER points somewhere else (see the no-model-name
        # note on STORYBOARD_RAM_HELP, which this status follows).
        self.assertNotIn("Gemma", status["message"])
        self.assertNotIn("gemma", status["message"])

    def test_the_floor_is_sixteen_gb(self):
        with mock.patch.object(panel, "SYSTEM_RAM_GB", 16.0):
            self.assertTrue(panel.storyboard_planner_ram_status()["ok"])
        with mock.patch.object(panel, "SYSTEM_RAM_GB", 15.9):
            self.assertFalse(panel.storyboard_planner_ram_status()["ok"])

    def test_it_is_wired_into_the_boot_payload(self):
        status = panel.storyboard_status()
        self.assertIn("ram_status", status)
        self.assertIn("ok", status["ram_status"])
        self.assertIn("message", status["ram_status"])


if __name__ == "__main__":
    unittest.main()
