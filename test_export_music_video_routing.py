#!/usr/bin/env python3
"""FILM-48: Export on a music-video board must not ignore the song.

sbExport() used to post only `{id}` to /storyboard/export — with no
edit.json (the Editor never opened), the server had no song to lay under
the film and silently concatenated each clip's own audio. A music-video
board now routes straight to /music/video/film (the dedicated endpoint
that already knows where the board's song is and calls the same _sb_export
underneath with music_mode=replace); an ordinary board is unaffected.
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


def _run(board):
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
    fns = "\n\n".join(extract_function(n, STORYBOARD_JS) for n in
                      ("sbExport", "sbMusicVideoBlock"))
    script = f"""
'use strict';
const _els = {{}};
function mk(id) {{ return {{ id, textContent: '', dataset: {{}}, disabled: false }}; }}
function sbEl(id) {{ if (!_els[id]) _els[id] = mk(id); return _els[id]; }}
globalThis.phosToast = () => {{}};
globalThis.sbFilmOpen = () => {{}};
globalThis.SB = {{ id: 'sb_test', payload: {{ board: {json.dumps(board)} }} }};
globalThis.__fetchCalls = [];
globalThis.fetch = (url, opts) => {{
  const body = opts && opts.body ? String(opts.body) : '';
  __fetchCalls.push({{ url, body }});
  return Promise.resolve({{
    json: async () => ({{ ok: true, files: [], dir: '/out', film_name: null, film_error: 'skip' }}),
  }});
}};
{fns}
sbExport().then(() => {{
  console.log(JSON.stringify(__fetchCalls));
}});
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = Path(fh.name)
    try:
        result = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise AssertionError(result.stdout + "\n" + result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])
    finally:
        path.unlink(missing_ok=True)


class ExportRoutesMusicVideoBoardsToTheSongAwareEndpoint(unittest.TestCase):
    def test_a_music_video_board_routes_to_music_video_film(self):
        calls = _run({"id": "sb_mv", "music_video": {"song": "/o/song.wav"}})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["url"], "/music/video/film")
        self.assertIn("board_id=sb_test", calls[0]["body"])

    def test_an_ordinary_board_still_uses_the_plain_export_route(self):
        calls = _run({"id": "sb_plain"})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["url"], "/storyboard/export")
        self.assertIn("id=sb_test", calls[0]["body"])

    def test_a_music_video_block_with_no_song_yet_is_not_treated_as_one(self):
        # An in-progress plan (e.g. cast chosen, no song set) must not be
        # routed at a song that does not exist.
        calls = _run({"id": "sb_partial", "music_video": {"song": ""}})
        self.assertEqual(calls[0]["url"], "/storyboard/export")


if __name__ == "__main__":
    unittest.main()
