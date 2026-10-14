#!/usr/bin/env python3
"""FILM-43 (client half): the board title keeps its filename extension
("song40.wav"), and the board list has no search or duplicate/rename UI.

Extracts the real _songNameFromFile (music.js) and sbFilterBoardList/
sbBoardRow (storyboard.js) and proves: any common audio extension is
stripped from an auto-derived title, and the search box actually filters
the rendered board list by title.
"""
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
MUSIC_JS = (ROOT / "webapp" / "js" / "music.js").read_text(encoding="utf-8")
STORYBOARD_JS = (ROOT / "webapp" / "js" / "storyboard.js").read_text(encoding="utf-8")


def _node(script: str) -> str:
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
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


class TheTitleLosesItsExtension(unittest.TestCase):
    def test_every_common_audio_extension_is_stripped(self):
        fn = extract_function("_songNameFromFile", MUSIC_JS)
        script = fn + r"""
console.log(JSON.stringify({
  wav: _songNameFromFile('song40.wav'),
  mp3: _songNameFromFile('my track.mp3'),
  m4a: _songNameFromFile('Voice Memo.m4a'),
  flac: _songNameFromFile('master.flac'),
  noext: _songNameFromFile('Already Clean'),
  prefixed: _songNameFromFile('music_20260101_120000_lullaby.wav'),
}));
"""
        out = json.loads(_node(script))
        self.assertEqual(out["wav"], "song40")
        self.assertEqual(out["mp3"], "my track")
        self.assertEqual(out["m4a"], "Voice Memo")
        self.assertEqual(out["flac"], "master")
        self.assertEqual(out["noext"], "Already Clean")
        self.assertEqual(out["prefixed"], "lullaby")


class TheBoardListSearchFilters(unittest.TestCase):
    def test_filtering_hides_non_matching_titles(self):
        if NODE is None:
            self.skipTest("node not on PATH")
        fns = "\n\n".join(extract_function(n, STORYBOARD_JS) for n in
                          ("sbFilterBoardList", "sbRenderBoardLists", "sbBoardRowMenu", "sbBoardRow",
                           "sbBoardRowMini", "sbFmtAgo", "sbBoardChip", "sbRowAction"))
        script = f"""
'use strict';
function escapeHtml(s) {{ return String(s == null ? '' : s); }}
const _els = {{}};
function mk(id) {{ return {{ id, innerHTML: '' }}; }}
function sbEl(id) {{ if (!_els[id]) _els[id] = mk(id); return _els[id]; }}
globalThis.SB = {{ boards: [
  {{ id: 'a', title: 'Lighthouse Keeper', shots: 3, done: 3, clips: 3 }},
  {{ id: 'b', title: 'Harbour at Dawn', shots: 5, done: 0, clips: 0 }},
  {{ id: 'c', title: 'Kitchen Table Talk', shots: 2, done: 2, clips: 2 }},
] }};
let _sbBoardFilter = '';
{fns}
sbFilterBoardList('lighthouse');
const afterSearch = sbEl('sbBoardList').innerHTML;
sbFilterBoardList('');
const afterClear = sbEl('sbBoardList').innerHTML;
console.log(JSON.stringify({{
  searchFindsIt: afterSearch.includes('Lighthouse Keeper'),
  searchHidesOthers: !afterSearch.includes('Harbour at Dawn'),
  clearedShowsAll: ['Lighthouse Keeper', 'Harbour at Dawn', 'Kitchen Table Talk']
    .every(t => afterClear.includes(t)),
}}));
"""
        out = json.loads(_node(script))
        self.assertTrue(out["searchFindsIt"])
        self.assertTrue(out["searchHidesOthers"])
        self.assertTrue(out["clearedShowsAll"])


if __name__ == "__main__":
    unittest.main()
