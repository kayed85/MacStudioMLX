#!/usr/bin/env python3
"""H3-28: the JS last-resort RAM-floor fallback must match the real floor.

`(st.ram_floor_gb || st.min_ram_gb || N)` only reaches the literal N when the
server's own fields are both absent — belt-and-suspenders, not the normal
path — but N still gets shown to a user in that case, and it was 46: not
H3_MIN_RAM_GB (60, the bf16 lane) and not H3_MIN_RAM_GB_Q8 (36, the real
floor, per mlx_ltx_panel.py) — a number no floor in this codebase has ever
been. Two call sites carried it (engines.js's engine-menu tooltip, queue.js's
engine-switch refusal reason); this pins both at 36 and pins the backend
constant they're supposed to agree with, so a future change to the real
floor is caught here too.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class RamFloorFallbackMatchesTheRealFloor(unittest.TestCase):
    def test_backend_floor_is_36(self):
        src = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
        m = re.search(r"^H3_MIN_RAM_GB_Q8\s*=\s*([\d.]+)", src, re.M)
        self.assertIsNotNone(m, "H3_MIN_RAM_GB_Q8 constant not found")
        self.assertEqual(float(m.group(1)), 36.0)

    def test_engines_js_fallback_is_36(self):
        src = (ROOT / "webapp" / "js" / "engines.js").read_text(encoding="utf-8")
        self.assertIn("st.ram_floor_gb || st.min_ram_gb || 36", src)
        self.assertNotIn("st.ram_floor_gb || st.min_ram_gb || 46", src)
        self.assertNotIn("st.ram_floor_gb || st.min_ram_gb || 64", src)

    def test_queue_js_fallback_is_36(self):
        src = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
        self.assertIn("st.ram_floor_gb || st.min_ram_gb || 36", src)
        self.assertNotIn("st.ram_floor_gb || st.min_ram_gb || 46", src)


if __name__ == "__main__":
    unittest.main()
