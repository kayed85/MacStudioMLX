#!/usr/bin/env python3
"""The FILM package (2026-09-29 mega review): slip and roll edits, the frame
timecode, takes/replace, and the toast that stops stacking.

Locked here:
  * FILM-03 — `sbeSlip` moves `start`/`end` together and leaves `film_start`/
    `film_end` alone, clamped to the source; `sbeRollEdit` moves a shared cut
    without moving the pair's total length; a repeated identical toast bumps
    a counter instead of stacking a new element.
  * FILM-52 — `sbeFmtTC` is HH:MM:SS:FF at the given fps.
  * FILM-07/08 — `sbeReplaceClip` keeps the slot (`film_start`/`film_end`,
    adjustments, fades) and only swaps the source window.

Run:  ./ltx-2-mlx/env/bin/python3.11 -m pytest -q test_editor_film_findings.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from test_storyboard_editor_ui import (FUNCTIONS, NODE, SHIM,        # noqa: E402
                                       extract_function, panel_source)


def _clip(cid, path, start, end, film_start, **kw):
    c = {"id": cid, "path": path, "start": start, "end": end,
         "film_start": film_start, "film_end": film_start + (end - start),
         "source": "human", "locked": False}
    c.update(kw)
    return c


def run_client(body: str) -> dict:
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
    src = panel_source()
    script = (SHIM + "\n".join(extract_function(n, src) for n in FUNCTIONS)
              + "\nconst out = {};\n" + body
              + "\nprocess.stdout.write(JSON.stringify(out));\n")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = Path(fh.name)
    try:
        res = subprocess.run([NODE, str(path)], capture_output=True,
                             text=True, timeout=60)
        if res.returncode:
            raise AssertionError(res.stdout + "\n" + res.stderr)
        return json.loads(res.stdout)
    finally:
        path.unlink(missing_ok=True)


# =============================================================================
# FILM-52 — frame timecode
# =============================================================================
class TheTimecode(unittest.TestCase):
    def test_hh_mm_ss_ff(self):
        r = run_client("""
out.zero = sbeFmtTC(0, 24);
out.one_frame = sbeFmtTC(1 / 24, 24);
out.round_trip = sbeFmtTC(171.96, 24);   // 2:51.96 from the report
out.hour = sbeFmtTC(3661.5, 24);
""")
        self.assertEqual(r["zero"], "00:00:00:00")
        self.assertEqual(r["one_frame"], "00:00:00:01")
        self.assertEqual(r["round_trip"], "00:02:51:23")
        self.assertEqual(r["hour"], "01:01:01:12")

    def test_default_fps_is_24_never_zero(self):
        r = run_client("out.v = sbeFmtTC(1, 0);")
        self.assertEqual(r["v"], "00:00:01:00")


# =============================================================================
# FILM-03 — SLIP
# =============================================================================
class TheSlip(unittest.TestCase):
    def test_slip_moves_the_window_not_the_slot(self):
        clips = [_clip("a", "/x/a.mp4", 2.0, 6.0, 0.0, duration=10.0)]
        r = run_client("out.r = sbeSlip(%s, 'a', 1.0);" % json.dumps(clips))
        self.assertTrue(r["r"]["ok"])
        c = r["r"]["clips"][0]
        self.assertAlmostEqual(c["start"], 3.0)
        self.assertAlmostEqual(c["end"], 7.0)
        self.assertAlmostEqual(c["film_start"], 0.0)
        self.assertAlmostEqual(c["film_end"], 4.0)

    def test_slip_clamps_to_the_source(self):
        clips = [_clip("a", "/x/a.mp4", 2.0, 6.0, 0.0, duration=10.0)]
        r = run_client("out.r = sbeSlip(%s, 'a', 100);" % json.dumps(clips))
        c = r["r"]["clips"][0]
        self.assertAlmostEqual(c["start"], 6.0)   # duration(10) - len(4)
        self.assertAlmostEqual(c["end"], 10.0)

    def test_slip_at_the_wall_refuses_cleanly(self):
        clips = [_clip("a", "/x/a.mp4", 0.0, 4.0, 0.0, duration=10.0)]
        r = run_client("out.r = sbeSlip(%s, 'a', -1);" % json.dumps(clips))
        self.assertFalse(r["r"]["ok"])
        self.assertIn("start", r["r"]["why"])

    def test_slip_refuses_a_locked_clip(self):
        clips = [_clip("a", "/x/a.mp4", 2.0, 6.0, 0.0, duration=10.0, locked=True)]
        r = run_client("out.r = sbeSlip(%s, 'a', 1.0);" % json.dumps(clips))
        self.assertFalse(r["r"]["ok"])
        self.assertEqual(r["r"]["why"], "locked")


# =============================================================================
# FILM-03 — ROLL
# =============================================================================
class TheRoll(unittest.TestCase):
    def test_roll_moves_the_cut_without_moving_the_pair(self):
        # a: 0..4 of a 10s take, b: 4..7 of a 10s take, sharing the cut at 4.
        a = _clip("a", "/x/a.mp4", 0.0, 4.0, 0.0, duration=10.0)
        b = _clip("b", "/x/b.mp4", 0.0, 3.0, 4.0, duration=10.0)
        r = run_client("out.r = sbeRollEdit(%s, 'a', 'b', 1.0);"
                       % json.dumps([a, b]))
        self.assertTrue(r["r"]["ok"])
        ca, cb = r["r"]["clips"]
        self.assertAlmostEqual(ca["film_end"], 5.0)
        self.assertAlmostEqual(cb["film_start"], 5.0)
        # The pair's total span and each one's HEAD did not move.
        self.assertAlmostEqual(ca["film_start"], 0.0)
        self.assertAlmostEqual(cb["film_end"], 7.0)
        self.assertAlmostEqual(ca["end"], 5.0)      # grew into its own take
        self.assertAlmostEqual(cb["start"], 1.0)    # gave up its head

    def test_roll_refuses_two_clips_that_do_not_share_a_cut(self):
        a = _clip("a", "/x/a.mp4", 0.0, 4.0, 0.0, duration=10.0)
        b = _clip("b", "/x/b.mp4", 0.0, 3.0, 5.0, duration=10.0)  # 1s hole
        r = run_client("out.r = sbeRollEdit(%s, 'a', 'b', 1.0);"
                       % json.dumps([a, b]))
        self.assertFalse(r["r"]["ok"])

    def test_roll_clamps_to_the_shorter_sides_room(self):
        a = _clip("a", "/x/a.mp4", 0.0, 4.0, 0.0, duration=4.0)   # no tail room
        b = _clip("b", "/x/b.mp4", 0.0, 3.0, 4.0, duration=10.0)
        r = run_client("out.r = sbeRollEdit(%s, 'a', 'b', 2.0);"
                       % json.dumps([a, b]))
        self.assertFalse(r["r"]["ok"])   # a has nothing more to give


# =============================================================================
# FILM-04 — the song as master clock
# =============================================================================
class TheSongClock(unittest.TestCase):
    def test_master_only_when_replace_and_playing(self):
        r = run_client("""
SBE.audio = { mode: 'replace', offset: 0 };
SBE.musicOk = true;
SBE.musicEl = { currentTime: 5, paused: false };
out.a = sbeMusicMaster();
SBE.musicEl.paused = true;
out.b = sbeMusicMaster();
SBE.musicEl.paused = false;
SBE.audio.mode = 'under';
out.c = sbeMusicMaster();
SBE.audio.mode = 'replace';
SBE.musicOk = false;
out.d = sbeMusicMaster();
""")
        self.assertEqual([r["a"], r["b"], r["c"], r["d"]], [True, False, False, False])

    def test_song_playhead_is_the_inverse_of_music_at(self):
        r = run_client("""
SBE.audio = { mode: 'replace', offset: 0, trim_start: 0 };
SBE.peaks = { duration: 100 };
SBE.musicEl = { currentTime: 12.5, paused: false };
out.t = sbeSongPlayhead();
out.roundtrip = sbeMusicAt(out.t);
""")
        self.assertAlmostEqual(r["t"], 12.5)
        self.assertAlmostEqual(r["roundtrip"], 12.5)

    def test_song_playhead_honours_a_head_trim(self):
        r = run_client("""
SBE.audio = { mode: 'replace', offset: 0, trim_start: 3.0 };
SBE.peaks = { duration: 100 };
SBE.musicEl = { currentTime: 3.0, paused: false };
out.at_head = sbeSongPlayhead();
SBE.musicEl.currentTime = 10.0;
out.later = sbeSongPlayhead();
""")
        self.assertAlmostEqual(r["at_head"], 3.0)
        self.assertAlmostEqual(r["later"], 10.0)

    def test_song_playhead_is_null_once_the_song_runs_out(self):
        r = run_client("""
SBE.audio = { mode: 'replace', offset: 0 };
SBE.peaks = { duration: 20 };
SBE.musicEl = { currentTime: 25, paused: false };
out.t = sbeSongPlayhead();
""")
        self.assertIsNone(r["t"])


# =============================================================================
# FILM-07/08 — REPLACE keeps the slot
# =============================================================================
class TheReplace(unittest.TestCase):
    def test_replace_keeps_the_slot_and_swaps_the_source(self):
        clips = [_clip("a", "/x/old.mp4", 1.0, 4.0, 10.0, duration=20.0,
                       adjust={"brightness": 0.2}, fx={"fade_in": 0.5})]
        item = {"path": "/x/new.mp4", "duration_s": 8.0, "n": 3}
        r = run_client("out.r = sbeReplaceClip(%s, 'a', %s);"
                       % (json.dumps(clips), json.dumps(item)))
        self.assertTrue(r["r"]["ok"])
        c = r["r"]["clips"][0]
        self.assertEqual(c["path"], "/x/new.mp4")
        self.assertAlmostEqual(c["film_start"], 10.0)
        self.assertAlmostEqual(c["film_end"], 13.0)     # slot unchanged: 3s
        self.assertAlmostEqual(c["end"] - c["start"], 3.0)
        self.assertEqual(c["adjust"], {"brightness": 0.2})
        self.assertEqual(c["fx"], {"fade_in": 0.5})

    def test_replace_clamps_the_in_point_to_a_shorter_take(self):
        clips = [_clip("a", "/x/old.mp4", 5.0, 10.0, 0.0, duration=30.0)]  # 5s slot
        item = {"path": "/x/short.mp4", "duration_s": 7.0}
        r = run_client("out.r = sbeReplaceClip(%s, 'a', %s);"
                       % (json.dumps(clips), json.dumps(item)))
        c = r["r"]["clips"][0]
        self.assertAlmostEqual(c["start"], 2.0)
        self.assertAlmostEqual(c["end"], 7.0)
        self.assertAlmostEqual(c["film_start"], 0.0)
        self.assertAlmostEqual(c["film_end"], 5.0)  # the slot itself is untouched

    def test_replace_refuses_a_take_shorter_than_the_slot(self):
        # EDITOR-4 (Codex 4.17.0): this used to answer ok with a 4 s window
        # in a 5 s slot — a document Save refuses as clip_length_mismatch.
        clips = [_clip("a", "/x/old.mp4", 5.0, 10.0, 0.0, duration=30.0)]  # 5s slot
        item = {"path": "/x/short.mp4", "duration_s": 4.0}
        r = run_client("out.r = sbeReplaceClip(%s, 'a', %s);"
                       % (json.dumps(clips), json.dumps(item)))
        self.assertFalse(r["r"]["ok"])
        self.assertIn("longer take", r["r"]["why"])
        self.assertEqual(r["r"]["clips"][0]["path"], "/x/old.mp4")

    def test_replace_refuses_a_locked_clip(self):
        clips = [_clip("a", "/x/old.mp4", 0.0, 3.0, 0.0, duration=10.0, locked=True)]
        item = {"path": "/x/new.mp4", "duration_s": 10.0}
        r = run_client("out.r = sbeReplaceClip(%s, 'a', %s);"
                       % (json.dumps(clips), json.dumps(item)))
        self.assertFalse(r["r"]["ok"])
        self.assertEqual(r["r"]["why"], "locked")


# =============================================================================
# FILM-03 — toast de-duplication
# =============================================================================
TOAST_SHIM = r"""
'use strict';
class FakeClassList {
  constructor() { this._s = new Set(); }
  add(...c) { c.forEach(x => this._s.add(x)); }
  remove(...c) { c.forEach(x => this._s.delete(x)); }
  contains(c) { return this._s.has(c); }
}
function mkEl(tag) {
  const el = { tagName: tag, children: [], dataset: {}, parentElement: null };
  el.classList = new FakeClassList();
  Object.defineProperty(el, 'className', {
    get() { return Array.from(el.classList._s).join(' '); },
    set(v) { el.classList._s = new Set(String(v || '').split(/\s+/).filter(Boolean)); },
  });
  el.appendChild = (child) => { child._connected = true; child.parentElement = el; el.children.push(child); return child; };
  el.remove = () => { el._connected = false; if (el.parentElement) { const i = el.parentElement.children.indexOf(el); if (i >= 0) el.parentElement.children.splice(i, 1); } };
  el.addEventListener = () => {};
  Object.defineProperty(el, 'isConnected', { get() { return !!el._connected; } });
  el.querySelector = (sel) => {
    const cls = sel.replace('.', '');
    return el.children.find(c => c.classList.contains(cls)) || null;
  };
  let _text = '';
  Object.defineProperty(el, 'textContent', {
    get() { return _text; }, set(v) { _text = String(v); },
  });
  Object.defineProperty(el, 'innerHTML', {
    get() { return ''; },
    set(html) {
      el.children = [];
      const re = /class="([^"]+)"/g; let m;
      while ((m = re.exec(html))) {
        const child = mkEl('span');
        m[1].split(/\s+/).forEach(c => child.classList.add(c));
        child.parentElement = el;
        el.children.push(child);
      }
    },
  });
  return el;
}
const container = mkEl('div');
container._connected = true;
global.document = {
  getElementById: (id) => (id === 'phosToast' ? container : null),
  createElement: (tag) => mkEl(tag),
};
global.setTimeout = () => 0;
global.clearTimeout = () => {};
"""


class TheToastDedup(unittest.TestCase):
    def _run(self, calls: str) -> dict:
        if NODE is None:
            raise unittest.SkipTest("node not on PATH")
        src = panel_source()
        fn = extract_function("phosToast", src)
        script = (TOAST_SHIM + fn + "\n" + calls
                  + "\nprocess.stdout.write(JSON.stringify({"
                  + "n: container.children.length,"
                  + "counts: container.children.map(c => c.dataset.count),"
                  + "}));\n")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(script)
            path = Path(fh.name)
        try:
            res = subprocess.run([NODE, str(path)], capture_output=True,
                                 text=True, timeout=30)
            if res.returncode:
                raise AssertionError(res.stdout + "\n" + res.stderr)
            return json.loads(res.stdout)
        finally:
            path.unlink(missing_ok=True)

    def test_three_identical_toasts_are_one_element_with_a_counter(self):
        r = self._run(
            "phosToast('Already hard against what comes after it.', {duration: 0});\n"
            "phosToast('Already hard against what comes after it.', {duration: 0});\n"
            "phosToast('Already hard against what comes after it.', {duration: 0});\n")
        self.assertEqual(r["n"], 1)
        self.assertEqual(r["counts"], ["3"])

    def test_a_different_message_still_stacks(self):
        r = self._run(
            "phosToast('one', {duration: 0});\n"
            "phosToast('two', {duration: 0});\n")
        self.assertEqual(r["n"], 2)
        self.assertEqual(r["counts"], ["1", "1"])


if __name__ == "__main__":
    unittest.main()
