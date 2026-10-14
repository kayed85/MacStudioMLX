#!/usr/bin/env python3
"""FILM-41 (playlist half): "Player" must play the BOARD's own shots, in
order, not whatever the Video tab happened to have selected.

Before this fix, the "Player" toggle just revealed the shared stage pane —
which still showed whatever `selectOutput()` was last called with globally,
anywhere in the panel. This extracts the REAL sbPlayBoard/sbPlaylistPlay/
sbPlaylistPaths and runs them in node against a fake selectOutput + a fake
player <video> element whose 'ended' event is fired manually, proving the
playlist advances through the board's own clips in order and stops advancing
the moment the user (or anything else) picks a different clip.
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
STORYBOARD_JS = (ROOT / "webapp" / "js" / "storyboard.js").read_text(encoding="utf-8")
FUNCTIONS = ("sbPlaylistPaths", "sbPlayBoard", "sbPlaylistPlay")

SHIM = r"""
'use strict';
globalThis.activePath = null;
globalThis.__selectCalls = [];
let _currentVideo = null;
function _mkVideo() {
  const listeners = {};
  return {
    addEventListener(ev, cb) { listeners[ev] = cb; },
    _fireEnded() { if (listeners.ended) listeners.ended(); },
  };
}
globalThis.selectOutput = (path, opts) => {
  globalThis.activePath = path;
  globalThis.__selectCalls.push({ path, opts });
  _currentVideo = _mkVideo();
};
globalThis.document = {
  querySelector(sel) { return sel === '#playerWrap video' ? _currentVideo : null; },
};
globalThis.sbSetStage = () => {};   // layout-only; not the concern here
globalThis.phosToast = () => {};
globalThis.SB = { id: 'sb_test', payload: null, playlist: null };
"""


def _run(board_shots, drive: str) -> dict:
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
    fns = "\n\n".join(extract_function(n, STORYBOARD_JS) for n in FUNCTIONS)
    script = SHIM + fns + f"""
SB.payload = {{ board: {{ shots: {json.dumps(board_shots)} }} }};
{drive}
console.log(JSON.stringify({{
  selectCalls: __selectCalls.map(c => c.path),
  autoplays: __selectCalls.map(c => !!(c.opts && c.opts.autoplay)),
  playlist: SB.playlist,
}}));
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = Path(fh.name)
    try:
        result = subprocess.run([NODE, str(path)], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise AssertionError(result.stdout + "\n" + result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])
    finally:
        path.unlink(missing_ok=True)


SHOTS = [
    {"n": 1, "status": "done", "draft_output": "/o/s1.mp4"},
    {"n": 2, "status": "done", "final_output": "/o/s2_final.mp4",
     "draft_output": "/o/s2_draft.mp4"},
    {"n": 3, "status": "skipped", "draft_output": "/o/s3_cut.mp4"},
    {"n": 4, "status": "pending"},                       # no clip yet
    {"n": 5, "status": "done", "draft_output": "/o/s5.mp4"},
]


class ThePlaylistPlaysTheBoardsOwnShots(unittest.TestCase):
    def test_pressing_player_starts_at_the_first_shots_clip(self):
        out = _run(SHOTS, "sbPlayBoard();")
        self.assertEqual(out["selectCalls"][0], "/o/s1.mp4")
        self.assertTrue(out["autoplays"][0])

    def test_final_output_beats_draft_output(self):
        out = _run(SHOTS, """
sbPlayBoard();
document.querySelector('#playerWrap video')._fireEnded();
""")
        self.assertIn("/o/s2_final.mp4", out["selectCalls"])
        self.assertNotIn("/o/s2_draft.mp4", out["selectCalls"])

    def test_cut_and_unrendered_shots_are_skipped(self):
        out = _run(SHOTS, "sbPlayBoard();")
        paths = out["playlist"]["paths"]
        self.assertNotIn("/o/s3_cut.mp4", paths)
        self.assertEqual(len(paths), 3)   # shots 1, 2, 5 only

    def test_ended_advances_to_the_next_clip_in_order(self):
        out = _run(SHOTS, """
sbPlayBoard();
document.querySelector('#playerWrap video')._fireEnded();
document.querySelector('#playerWrap video')._fireEnded();
""")
        self.assertEqual(out["selectCalls"],
                         ["/o/s1.mp4", "/o/s2_final.mp4", "/o/s5.mp4"])

    def test_playlist_ends_cleanly_after_the_last_clip(self):
        out = _run(SHOTS, """
sbPlayBoard();
document.querySelector('#playerWrap video')._fireEnded();
document.querySelector('#playerWrap video')._fireEnded();
document.querySelector('#playerWrap video')._fireEnded();
""")
        self.assertIsNone(out["playlist"])

    def test_a_manual_selection_mid_playback_stops_the_advance(self):
        out = _run(SHOTS, """
sbPlayBoard();
const v1 = document.querySelector('#playerWrap video');
// the user clicks something else entirely while shot 1 plays
activePath = '/somewhere/else.mp4';
v1._fireEnded();
""")
        # never reached shot 2 — the playlist noticed activePath moved
        self.assertEqual(out["selectCalls"], ["/o/s1.mp4"])

    def test_an_empty_board_does_not_start_a_playlist(self):
        out = _run([{"n": 1, "status": "pending"}], "sbPlayBoard();")
        self.assertEqual(out["selectCalls"], [])
        self.assertIsNone(out["playlist"])


if __name__ == "__main__":
    unittest.main()
