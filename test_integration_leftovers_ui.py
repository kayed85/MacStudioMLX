#!/usr/bin/env python3
"""4.17 integration leftovers, UI half (coordinator follow-up). Each one only
became possible once the editor/board/music packages were combined.

3. The Editor's SLOW flag is a button: clicking it runs Prepare (FILM-39).
4. A song's file extension (and upload / composer prefixes) never becomes a
   board title — server and client.
5. The Music-video pane (song, pictures, roles, last board) survives a reload.
6. Opening a board no longer rewrites the new-film form's shot count/engine.
7. The storyboard row delete sits behind ⋯ and uses the panel's own dialog.

JS is EXECUTED in node against small shims (scripts/extract_panel_js.py), not
grepped, wherever the behaviour is a function's.
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
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from extract_panel_js import extract_function  # noqa: E402

NODE = shutil.which("node")
JS = ROOT / "webapp" / "js"


def _src(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def _node(script: str):
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        r = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise AssertionError(r.stderr[-3000:])
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        Path(path).unlink(missing_ok=True)


class TheSlowFlagRunsPrepare(unittest.TestCase):
    def _run(self, prepare_state):
        fn = extract_function("sbeOnTrackDown", _src("editor.js"))
        return _node(fn + f"""
const calls = [];
global.SBE = {{ prepare: {json.dumps(prepare_state)} }};
global.sbEl = () => ({{}}); global.sbeEl = () => ({{}});
global.sbePrepare = () => calls.push('prepare');
global.phosToast = (m) => calls.push('toast');
const flag = {{}};
const ev = {{ preventDefault(){{ calls.push('prevent'); }},
             target: {{ closest: (sel) => sel === '.sbe-slow-flag' ? flag : null }} }};
sbeOnTrackDown(ev);
console.log(JSON.stringify(calls));
""")

    def test_click_runs_prepare(self):
        self.assertEqual(self._run(None), ["prevent", "prepare"])

    def test_click_while_prepare_runs_says_so_instead(self):
        self.assertEqual(self._run({"state": "running"}), ["prevent", "toast"])

    def test_the_slow_flag_is_marked_as_that_button(self):
        self.assertIn('sbe-cl-flag sbe-slow-flag" role="button"', _src("editor.js"))


class SongTitlesLoseTheirExtension(unittest.TestCase):
    CASES = [("song40.wav", "song40"), ("1790123456789_take_3.mp3", "take 3"),
             ("music_20260929_101500_neon_rain.wav", "neon rain"),
             ("/Users/x/panel_uploads/1790123456789_Café_été.flac", "Café été"),
             ("My Song", "My Song")]

    def test_server(self):
        import panel.routes_music as rm
        for raw, want in self.CASES:
            self.assertEqual(rm._board_title_from_song(raw), want, raw)
        src = (ROOT / "panel" / "routes_music.py").read_text(encoding="utf-8")
        self.assertIn("title = _board_title_from_song(", src)

    def test_client(self):
        fn = extract_function("_songNameFromFile", _src("music.js"))
        got = _node(fn + "\nconsole.log(JSON.stringify(" + json.dumps([c[0] for c in self.CASES])
                    + ".map(_songNameFromFile)));")
        self.assertEqual(got, [c[1] for c in self.CASES])
        self.assertIn("|| _songNameFromFile(path);", _src("music.js"))


class TheMusicVideoBriefSurvivesAReload(unittest.TestCase):
    def test_round_trip_through_storage(self):
        src = _src("music.js")
        consts = "\n".join(re.findall(r"^(?:const MV_DRAFT_KEY|let _mvDraftRestored).*$", src, re.M))
        script = (consts + "\n" + extract_function("mvSaveDraft", src) + "\n"
                  + extract_function("mvRestoreDraft", src) + """
const store = {};
global.localStorage = { getItem: k => (k in store ? store[k] : null),
                        setItem: (k, v) => { store[k] = String(v); },
                        removeItem: k => { delete store[k]; } };
global.MV = { song: '/o/song.wav', songName: 'Neon rain', songSeconds: 184.2, boardId: 'sb_1',
              cast: [{ path: '/u/a.png', name: 'a.png', role: 'singer', prompt: 'close up' },
                     { path: '/u/b.png', name: 'b.png', role: '', prompt: '' }], busy: false };
mvSaveDraft();
const saved = JSON.parse(JSON.stringify(MV));
global.MV = { song: null, songName: '', songSeconds: null, cast: [], busy: false, boardId: '' };
mvRestoreDraft();
const restored = { song: MV.song, songName: MV.songName, songSeconds: MV.songSeconds,
                   boardId: MV.boardId, cast: MV.cast };
// never over live work, and only once
global.MV = { song: '/o/other.wav', songName: 'x', songSeconds: 1, cast: [], busy: false, boardId: '' };
_mvDraftRestored = false; mvRestoreDraft();
console.log(JSON.stringify({ saved, restored, live: MV.song }));
""")
        out = _node(script)
        s, r = out["saved"], out["restored"]
        for k in ("song", "songName", "songSeconds", "boardId"):
            self.assertEqual(r[k], s[k], k)
        self.assertEqual(r["cast"], s["cast"])
        self.assertEqual(out["live"], "/o/other.wav")

    def test_it_is_restored_on_init_and_saved_on_every_change(self):
        src = _src("music.js")
        init = extract_function("mvInit", src)
        self.assertIn("mvRestoreDraft();", init)
        for fn in ("mvRenderSong", "mvRenderCast", "mvSetPrompt"):
            self.assertIn("mvSaveDraft();", extract_function(fn, src), fn)


class OpeningABoardLeavesTheNewFilmFormAlone(unittest.TestCase):
    def test_render_plan_does_not_touch_the_brief_length_or_engine(self):
        fn = extract_function("sbRenderPlan", _src("storyboard.js"))
        for bad in ("sbSetShots(", "sbSetTake(", "sbSetEngineMode(", "_sbShots =",
                    "_sbEngineMode =", "_sbTake ="):
            self.assertNotIn(bad, fn, bad)


class BoardRowDeleteIsBehindAMenu(unittest.TestCase):
    def test_rows_carry_a_menu_not_a_bare_delete(self):
        src = _src("storyboard.js")
        script = "\n".join(extract_function(n, src) for n in
                           ("sbBoardRowMenu", "sbBoardRow", "sbBoardRowMini")) + """
global.escapeHtml = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
global.sbFmtAgo = () => ''; global.sbBoardChip = () => ''; global.sbRowAction = () => '';
const b = { id: 'sb_1', title: "Neon's rain", shots: 3, done: 1 };
console.log(JSON.stringify([sbBoardRow(b), sbBoardRowMini(b)]));
"""
        for html in _node(script):
            self.assertIn('class="kebab-pop"', html)
            self.assertIn('role="menuitem"', html)
            # the only path to delete is the menu item
            self.assertEqual(html.count("sbDeleteBoard("), 1)
            item = html[html.index('role="menuitem"'):]
            self.assertIn("sbDeleteBoard(", item)

    def _delete(self, modal_ok: bool):
        fn = extract_function("sbDeleteBoard", _src("storyboard.js"))
        return _node(fn + f"""
const calls = [];
global._phModalShow = (o) => {{ calls.push(['modal', o.tone, o.primaryLabel]);
                               if ({json.dumps(modal_ok)}) {{ o.onPrimary(); return true; }} return false; }};
global.confirm = () => {{ calls.push(['confirm']); return true; }};
global.sbDeleteBoardNow = (id) => calls.push(['delete', id]);
sbDeleteBoard('sb_1', 'Night');
console.log(JSON.stringify(calls));
""")

    def test_the_panel_dialog_confirms(self):
        self.assertEqual(self._delete(True),
                         [["modal", "danger", "Delete"], ["delete", "sb_1"]])

    def test_a_busy_dialog_falls_back_to_a_confirm_never_to_none(self):
        self.assertEqual(self._delete(False),
                         [["modal", "danger", "Delete"], ["confirm"], ["delete", "sb_1"]])


if __name__ == "__main__":
    unittest.main()
