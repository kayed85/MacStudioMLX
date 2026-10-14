#!/usr/bin/env python3
"""FILM-45 (client half): sbParseLocations mirrors the server's full-width
colon acceptance for Japanese location lines."""
from __future__ import annotations

import json
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


class TheClientMirrorsTheFullWidthColon(unittest.TestCase):
    def test_a_japanese_line_splits_on_the_fullwidth_colon(self):
        if NODE is None:
            self.skipTest("node not on PATH")
        fn = extract_function("sbParseLocations", STORYBOARD_JS)
        script = f"""
'use strict';
{fn}
console.log(JSON.stringify(sbParseLocations('台所：小さな台所')));
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(script)
            path = Path(fh.name)
        try:
            result = subprocess.run([NODE, str(path)], capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise AssertionError(result.stdout + "\n" + result.stderr)
            out = json.loads(result.stdout.strip())
        finally:
            path.unlink(missing_ok=True)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["name"], "台所")
        self.assertEqual(out[0]["description"], "小さな台所")


if __name__ == "__main__":
    unittest.main()
