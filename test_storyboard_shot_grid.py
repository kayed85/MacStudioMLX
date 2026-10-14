#!/usr/bin/env python3
"""FILM-41 (grid half): a dense contact-sheet view for a long board, so
finding a shot doesn't mean scrolling a 54,540 px column of full cards.

Extracts the real sbShotGridTile and sbSetShotView and proves the tile
carries a thumbnail, number, duration, status and grade badge, and that the
List/Grid toggle actually switches the shot list's rendered layout.
"""
from __future__ import annotations

import json
import re
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


def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") \
        .replace('"', "&quot;").replace("'", "&#39;")


def _tile(shot: dict) -> str:
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
    fn = extract_function("sbShotGridTile", STORYBOARD_JS)
    script = f"""
'use strict';
function escapeHtml(s) {{ return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c])); }}
{fn}
console.log(sbShotGridTile({json.dumps(shot)}));
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = Path(fh.name)
    try:
        result = subprocess.run([NODE, str(path)], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise AssertionError(result.stdout + "\n" + result.stderr)
        return result.stdout
    finally:
        path.unlink(missing_ok=True)


class TheGridTile(unittest.TestCase):
    def test_a_rendered_shot_shows_a_thumbnail_number_length_and_status(self):
        html = _tile({"n": 7, "duration_s": 5.2, "status": "done",
                      "draft_output": "/o/s7.mp4", "title": "S7"})
        self.assertIn('data-n="7"', html)
        self.assertIn("07", html)
        self.assertIn("5.2s", html)
        self.assertIn("sb-grid-status-done", html)
        self.assertIn("<video", html)
        self.assertIn("path=%2Fo%2Fs7.mp4", html)

    def test_a_shot_with_no_clip_gets_an_empty_thumbnail_not_a_broken_video(self):
        html = _tile({"n": 3, "status": "pending"})
        self.assertNotIn("<video", html)
        self.assertIn("sb-grid-thumb-empty", html)

    def test_the_grade_shows_as_a_letter_badge(self):
        html = _tile({"n": 1, "status": "done", "grade": "keep", "draft_output": "/o/s1.mp4"})
        self.assertIn("sb-grid-grade-keep", html)
        self.assertIn(">K<", html)

    def test_no_grade_means_no_badge(self):
        html = _tile({"n": 1, "status": "pending"})
        self.assertNotIn("sb-grid-grade", html)

    def test_clicking_a_tile_opens_it_in_the_full_list(self):
        html = _tile({"n": 12, "status": "done", "draft_output": "/o/s12.mp4"})
        self.assertIn("sbGridTileOpen(12)", html)


class TheViewToggle(unittest.TestCase):
    def test_switching_to_grid_adds_the_grid_class_and_repaints(self):
        if NODE is None:
            self.skipTest("node not on PATH")
        fn = extract_function("sbSetShotView", STORYBOARD_JS)
        script = f"""
'use strict';
globalThis.SB = {{ shotView: 'list', payload: {{}} }};
globalThis.sessionStorage = {{ setItem() {{}}, getItem() {{ return null; }} }};
let _class = new Set();
const shotsBox = {{
  classList: {{ toggle(c, on) {{ on ? _class.add(c) : _class.delete(c); }},
                contains(c) {{ return _class.has(c); }} }},
}};
globalThis.sbEl = (id) => (id === 'sbShots' ? shotsBox : {{ querySelectorAll: () => [] }});
let repainted = false;
globalThis.sbRenderPlan = () => {{ repainted = true; }};
let _sbShotsHtml = 'stale';
{fn}
sbSetShotView('grid');
console.log(JSON.stringify({{
  view: SB.shotView, hasGridClass: shotsBox.classList.contains('sb-shots-grid'),
  repainted,
}}));
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(script)
            path = Path(fh.name)
        try:
            result = subprocess.run([NODE, str(path)], capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise AssertionError(result.stdout + "\n" + result.stderr)
            out = json.loads(result.stdout.strip().splitlines()[-1])
        finally:
            path.unlink(missing_ok=True)
        self.assertEqual(out["view"], "grid")
        self.assertTrue(out["hasGridClass"])
        self.assertTrue(out["repainted"])


if __name__ == "__main__":
    unittest.main()
