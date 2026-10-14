#!/usr/bin/env python3
"""FILM-56 (client half): the planning-modal copy scales with the shot
count actually submitted, and folds in a low-RAM notice — against the real
`_sbPlanningTimeCopy()`, not a re-implementation of its rules."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

from extract_panel_js import extract_function                        # noqa: E402

NODE = shutil.which("node")
STORYBOARD_JS = (ROOT / "webapp" / "js" / "storyboard.js").read_text(encoding="utf-8")


def _run(shots_submitted, ram_status):
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
    fn = extract_function("_sbPlanningTimeCopy", STORYBOARD_JS)
    script = f"""
'use strict';
let _sbPlanShotsSubmitted = {shots_submitted};
const SB_BOOT = {{ ram_status: {ram_status} }};
{fn}
console.log(_sbPlanningTimeCopy());
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = Path(fh.name)
    try:
        result = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise AssertionError(result.stdout + "\n" + result.stderr)
        return result.stdout.strip()
    finally:
        path.unlink(missing_ok=True)


class PlanningCopyScalesWithShotCount(unittest.TestCase):
    def test_a_small_board_still_says_about_a_minute(self):
        text = _run(4, "{ ok: true }")
        self.assertIn("About a minute", text)

    def test_a_medium_board_names_its_own_shot_count(self):
        text = _run(8, "{ ok: true }")
        self.assertIn("8 shots", text)
        self.assertNotIn("About a minute", text)

    def test_a_large_board_says_several_minutes(self):
        text = _run(20, "{ ok: true }")
        self.assertIn("Several minutes", text)
        self.assertIn("20 shots", text)

    def test_a_ram_short_mac_keeps_saying_so_through_every_stage(self):
        text = _run(4, "{ ok: false, message: 'This Mac reports 8 GB.' }")
        self.assertIn("short on memory", text)

    def test_a_capable_mac_says_nothing_about_memory(self):
        text = _run(4, "{ ok: true }")
        self.assertNotIn("memory", text)


if __name__ == "__main__":
    unittest.main()
