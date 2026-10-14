#!/usr/bin/env python3
"""FILM-44: one music-video entrance, not two hidden from each other.

  * mvFillLibrary() only listed songs COMPOSED in-app (engine === 'music')
    — any audio the user already had never appeared. It now lists every
    audio file the gallery knows about.
  * Storyboard's raw Soundtrack path box now signposts the real Music
    Video planner (sbGoToMusicVideo switches to the Audio tab in Music
    Video mode) so a creator who starts in Storyboard can still find it.
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


class TheLibraryListsAnyAudio(unittest.TestCase):
    def test_an_uploaded_track_with_no_music_engine_still_appears(self):
        fn = extract_function("mvFillLibrary", MUSIC_JS)
        script = f"""
'use strict';
function _esc(s) {{ return String(s == null ? '' : s); }}
function _songNameFromFile(name) {{ return String(name || '').replace(/\\.(wav|mp3|m4a|flac|aac|ogg|opus)$/i, ''); }}
const _els = {{}};
function mk(id) {{ return {{ id, innerHTML: '', value: '' }}; }}
function _el(id) {{ if (!_els[id]) _els[id] = mk(id); return _els[id]; }}
globalThis.currentOutputs = [
  {{ kind: 'audio', engine: 'music', name: 'composed.wav', path: '/o/composed.wav', music: {{ title: 'Composed Song' }} }},
  {{ kind: 'audio', engine: 'upload', name: 'my_track.mp3', path: '/u/my_track.mp3' }},
  {{ kind: 'video', engine: 'ltx', name: 'clip.mp4', path: '/o/clip.mp4' }},
];
{fn}
mvFillLibrary();
console.log(_el('mvSongLibrary').innerHTML);
"""
        html = _node(script)
        self.assertIn("Composed Song", html)
        self.assertIn("my_track", html)
        self.assertNotIn("clip.mp4", html)

    def test_no_audio_at_all_says_so_honestly(self):
        fn = extract_function("mvFillLibrary", MUSIC_JS)
        script = f"""
'use strict';
function _esc(s) {{ return String(s == null ? '' : s); }}
function _songNameFromFile(name) {{ return name; }}
const _els = {{}};
function mk(id) {{ return {{ id, innerHTML: '', value: '' }}; }}
function _el(id) {{ if (!_els[id]) _els[id] = mk(id); return _els[id]; }}
globalThis.currentOutputs = [];
{fn}
mvFillLibrary();
console.log(_el('mvSongLibrary').innerHTML);
"""
        html = _node(script)
        self.assertNotIn("Nothing composed", html)
        self.assertIn("outputs or uploads", html)


class TheSignpostSwitchesToTheRealPlanner(unittest.TestCase):
    def test_sb_go_to_music_video_switches_tab_and_mode(self):
        fn = extract_function("sbGoToMusicVideo", STORYBOARD_JS)
        script = f"""
'use strict';
const calls = [];
globalThis.workflowSwitch = (w) => calls.push(['workflowSwitch', w]);
globalThis.audioModeSet = (m) => calls.push(['audioModeSet', m]);
{fn}
sbGoToMusicVideo();
console.log(JSON.stringify(calls));
"""
        calls = json.loads(_node(script))
        self.assertIn(["workflowSwitch", "audio"], calls)
        self.assertIn(["audioModeSet", "drive"], calls)


if __name__ == "__main__":
    unittest.main()
