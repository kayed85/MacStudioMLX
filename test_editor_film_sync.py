#!/usr/bin/env python3
"""FILM-05 — "sync to song" and "Auto-align lip-sync".

Locked here:
  * `_lipsync_best_lag_core` — the pure cross-correlation search shared by
    the self-check (`take_lipsync_score`) and the new song check
    (`take_lipsync_best_lag_vs_song`). Tested with synthetic series so the
    SIGN of the returned lag can be trusted without a real face and voice
    on hand: a positive lag means the mouth matches reference content
    further ALONG the reference — later — than where the clip sits today.
  * `take_lipsync_score` / `_take_lipsync_score_and_lag` — the refactor
    that split the lag out of the score keeps the score identical (the gate
    that already ships mocks this function; this file checks the real
    implementation is still the same shape it was).
  * The client's `sbeSongSyncOffset` / `sbeSnapToSong` — measured against a
    synthetic shot whose `audio_start_time` is known, the same numbers the
    report published for the owner's cut.
  * FILM-05 item 4 — `sbeSongLocked` (the pure predicate) run live in node
    against synthetic shots, and `sbeOnTrackDown`'s guard checked against
    the real extracted source: move/movemany/reorder refuse a song-locked
    clip, trim/roll/slip do not (coordinator follow-up, 2026-09-29 — Editor
    side only, board's own lock is FILM-21).

Run:  ./ltx-2-mlx/env/bin/python3.11 -m pytest -q test_editor_film_sync.py
"""
from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as panel                                        # noqa: E402
from test_storyboard_editor_ui import (FUNCTIONS, NODE, SHIM,        # noqa: E402
                                       extract_function, panel_source)

try:
    import numpy as np
    HAVE_NUMPY = True
except Exception:                                                   # noqa: BLE001
    HAVE_NUMPY = False


@unittest.skipUnless(HAVE_NUMPY, "numpy not importable")
class TheLagSearchCore(unittest.TestCase):
    def test_a_shifted_slice_recovers_its_own_shift_as_the_lag(self):
        # A reference series long enough to search ±6 either side of head=50,
        # with enough structure (not a pure sine, which is its own alias at
        # some lag) that only one offset correlates near 1.0.
        rng = np.random.default_rng(7)
        ref = rng.normal(size=100).cumsum()
        head = 50
        shift = 3
        mouth = ref[head + shift: head + shift + 20]
        got = panel._lipsync_best_lag_core(mouth, ref, head, 6)
        self.assertIsNotNone(got)
        score, lag = got
        self.assertEqual(lag, shift)
        self.assertGreater(score, 0.99)

    def test_a_negative_shift_recovers_a_negative_lag(self):
        rng = np.random.default_rng(11)
        ref = rng.normal(size=100).cumsum()
        head = 50
        mouth = ref[head - 4: head - 4 + 20]
        score, lag = panel._lipsync_best_lag_core(mouth, ref, head, 6)
        self.assertEqual(lag, -4)

    def test_no_match_in_range_returns_none_rather_than_a_guess(self):
        rng = np.random.default_rng(3)
        ref = rng.normal(size=40)                      # too short to hold head+search+n
        mouth = rng.normal(size=20)
        self.assertIsNone(panel._lipsync_best_lag_core(mouth, ref, 30, 6))

    def test_flat_series_refuses_rather_than_correlating_noise(self):
        ref = [0.0] * 100
        mouth = [0.0] * 20
        self.assertIsNone(panel._lipsync_best_lag_core(mouth, ref, 50, 6))


class TheScoreRefactorIsIdentical(unittest.TestCase):
    """The split into `_take_lipsync_score_and_lag` must not change what
    `take_lipsync_score` returns — the gate's mocks stand in for it, so
    nothing else proves the real function is unchanged in shape."""

    def test_take_lipsync_score_still_exists_and_reads_the_lag_helper(self):
        src = inspect.getsource(panel.take_lipsync_score)
        self.assertIn("_take_lipsync_score_and_lag", src)

    def test_missing_cv2_is_none_not_an_exception(self):
        import unittest.mock as mock
        import builtins
        real_import = builtins.__import__

        def blocked(name, *a, **kw):
            if name == "cv2":
                raise ImportError("no cv2")
            return real_import(name, *a, **kw)

        with mock.patch.object(builtins, "__import__", side_effect=blocked):
            self.assertIsNone(panel.take_lipsync_score("/x/nope.mp4"))
            self.assertIsNone(panel.take_lipsync_best_lag("/x/nope.mp4"))
            self.assertIsNone(
                panel.take_lipsync_best_lag_vs_song("/x/nope.mp4", "/x/song.wav", 0.0, 4.0))


# =============================================================================
# The client half — sbeSongSyncOffset / sbeSnapToSong
# =============================================================================
def run_client(body: str) -> dict:
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
    import json
    import subprocess as sp
    import tempfile
    src = panel_source()
    script = (SHIM + "\n".join(extract_function(n, src) for n in FUNCTIONS)
              + "\nconst out = {};\n" + body
              + "\nprocess.stdout.write(JSON.stringify(out));\n")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = Path(fh.name)
    try:
        res = sp.run([NODE, str(path)], capture_output=True, text=True, timeout=60)
        if res.returncode:
            raise AssertionError(res.stdout + "\n" + res.stderr)
        return json.loads(res.stdout)
    finally:
        path.unlink(missing_ok=True)


class TheSongSyncReadout(unittest.TestCase):
    def test_offset_matches_the_reports_own_measurement_shape(self):
        # Owner's cut: audio_start_time + source_in is the expected film
        # position; the clip sat 0.19s LATE of it (one of the report's
        # measured offsets).
        r = run_client("""
const shot = { mode: 'a2v', audio_start_time: 10.0 };
const c = { id: 'a', kind: 'video', start: 0.0, end: 4.0,
            film_start: 10.19, film_end: 14.19 };
out.off = sbeSongSyncOffset(c, shot);
""")
        self.assertAlmostEqual(r["off"], 0.19, places=6)

    def test_offset_accounts_for_a_trimmed_head(self):
        r = run_client("""
const shot = { mode: 'a2v', audio_start_time: 10.0 };
const c = { id: 'a', kind: 'video', start: 1.5, end: 5.0,
            film_start: 11.5, film_end: 15.0 };
out.off = sbeSongSyncOffset(c, shot);
""")
        self.assertAlmostEqual(r["off"], 0.0, places=6)

    def test_non_a2v_shots_have_no_song_offset(self):
        r = run_client("""
const shot = { mode: 'text', audio_start_time: 10.0 };
const c = { id: 'a', kind: 'video', start: 0, end: 4, film_start: 10.19, film_end: 14.19 };
out.off = sbeSongSyncOffset(c, shot);
""")
        self.assertIsNone(r["off"])

    def test_snap_to_song_is_a_slip_that_zeroes_the_offset(self):
        r = run_client("""
const shot = { mode: 'a2v', audio_start_time: 10.0 };
SBE.clips = [{ id: 'a', kind: 'video', start: 0.0, end: 4.0, duration: 20.0,
              film_start: 10.19, film_end: 14.19, locked: false }];
SBE.pool = [{ path: undefined, ...shot }];
// sbeShotForClip matches by path; give the clip and the pool row the same one.
SBE.clips[0].path = '/x/a.mp4';
SBE.pool[0].path = '/x/a.mp4';
sbeSnapToSong('a');
const c = sbeById(SBE.clips, 'a');
out.start = c.start;
out.film_start = c.film_start;   // the slot must not move
out.offset_after = sbeSongSyncOffset(c, shot);
""")
        self.assertAlmostEqual(r["film_start"], 10.19)
        self.assertAlmostEqual(r["offset_after"], 0.0, places=2)


class TheSongLockPredicate(unittest.TestCase):
    """`sbeSongLocked` — the pure check behind FILM-05 item 4's move guard."""

    def test_an_a2v_clip_with_a_known_start_time_is_locked(self):
        r = run_client("""
SBE.pool = [{ path: '/x/a.mp4', mode: 'a2v', audio_start_time: 10.0 }];
const c = { id: 'a', kind: 'video', path: '/x/a.mp4', start: 0.0, end: 4.0,
            film_start: 10.0, film_end: 14.0 };
out.locked = sbeSongLocked(c);
""")
        self.assertTrue(r["locked"])

    def test_a_non_a2v_shot_is_never_locked(self):
        r = run_client("""
SBE.pool = [{ path: '/x/a.mp4', mode: 'text', audio_start_time: 10.0 }];
const c = { id: 'a', kind: 'video', path: '/x/a.mp4', start: 0.0, end: 4.0,
            film_start: 10.0, film_end: 14.0 };
out.locked = sbeSongLocked(c);
""")
        self.assertFalse(r["locked"])

    def test_an_a2v_shot_with_no_audio_start_time_yet_is_not_locked(self):
        # The song offset is undefined without a start time — nothing to
        # protect, and nothing sbeSongSyncOffset could even measure.
        r = run_client("""
SBE.pool = [{ path: '/x/a.mp4', mode: 'a2v', audio_start_time: null }];
const c = { id: 'a', kind: 'video', path: '/x/a.mp4', start: 0.0, end: 4.0,
            film_start: 10.0, film_end: 14.0 };
out.locked = sbeSongLocked(c);
""")
        self.assertFalse(r["locked"])

    def test_a_clip_with_no_matching_shot_is_not_locked(self):
        r = run_client("""
SBE.pool = [];
const c = { id: 'a', kind: 'video', path: '/x/a.mp4', start: 0.0, end: 4.0,
            film_start: 10.0, film_end: 14.0 };
out.locked = sbeSongLocked(c);
""")
        self.assertFalse(r["locked"])


class TheSongLockGuardsMoveNotTrimOrSlip(unittest.TestCase):
    """FILM-05 item 4: `sbeOnTrackDown` refuses move/movemany/reorder on a
    song-locked clip and leaves trim/roll/slip alone — trim and roll can
    never move the offset (film side and source side move together; see
    the trim comment), and slip is the sanctioned way to correct it.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.src = panel_source()
        cls.down = extract_function("sbeOnTrackDown", cls.src)

    def test_the_guard_runs_after_mode_is_decided_and_before_the_drag_starts(self):
        # It must see the FINAL mode (roll/slip/trim already excluded by the
        # ternary above it), and it must run before `SBE.drag = {` so a
        # refused clip never enters a drag at all.
        mode_at = self.down.index("const mode = rollWith ? 'roll'")
        guard_at = self.down.index("sbeSongLocked")
        drag_at = self.down.index("SBE.drag = { id: id, mode: mode")
        self.assertLess(mode_at, guard_at)
        self.assertLess(guard_at, drag_at)

    def test_the_three_gestures_that_move_the_slot_are_all_covered(self):
        guard = self.down[self.down.index("if (mode === 'move' || mode === 'movemany'"):]
        guard = guard[:guard.index("SBE.drag = { id: id, mode: mode")]
        for m in ("'move'", "'movemany'", "'reorder'"):
            self.assertIn(m, guard)
        # ...and only those three — a bare `return` inside a wider `if` would
        # silently also catch trim/roll/slip if the boundaries ever moved.
        self.assertNotIn("'trimL'", guard)
        self.assertNotIn("'trimR'", guard)
        self.assertNotIn("'roll'", guard)
        self.assertNotIn("'slip'", guard)

    def test_movemany_checks_every_id_in_the_selection_not_just_the_primary(self):
        guard = self.down[self.down.index("if (mode === 'move' || mode === 'movemany'"):]
        guard = guard[:guard.index("SBE.drag = { id: id, mode: mode")]
        self.assertIn("sbeSelIds()", guard)
        self.assertIn(".find(", guard)

    def test_a_blocked_drag_returns_before_pointer_capture_or_undo_snapshot(self):
        guard = self.down[self.down.index("if (mode === 'move' || mode === 'movemany'"):]
        guard = guard[:guard.index("SBE.drag = { id: id, mode: mode")]
        self.assertIn("return;", guard)
        self.assertNotIn("setPointerCapture", guard)

    def test_the_toast_names_the_way_out(self):
        guard = self.down[self.down.index("if (mode === 'move' || mode === 'movemany'"):]
        guard = guard[:guard.index("SBE.drag = { id: id, mode: mode")]
        self.assertIn("phosToast(", guard)
        self.assertIn("Snap to song", guard)
        self.assertIn("slip", guard)


# =============================================================================
# The route — /storyboard/edit/auto-align
# =============================================================================
class TheAutoAlignRoute(unittest.TestCase):
    def setUp(self):
        import tempfile
        from unittest import mock

        from test_storyboard_editor_api import FakeHandler, _board, _clip, _edit
        import storyboard
        import storyboard_editor as sedit

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.song = self.root / "song.wav"
        self.song.write_bytes(b"x")
        self.clipfile = self.root / "s1.mp4"
        self.clipfile.write_bytes(b"x")
        self.board = _board([self.clipfile])
        self.board["shots"][0]["mode"] = "a2v"
        c = _clip(str(self.clipfile), 0.0, 4.0, 0.0, id="c1")
        self.edit = sedit.normalise_edit(
            _edit([c], audio={"path": str(self.song), "mode": "replace", "offset": 0.0}))
        bdir = self.root / "sb_t"
        bdir.mkdir()
        sedit.save_edit(bdir, self.edit)
        self.FakeHandler = FakeHandler
        self.patches = [
            mock.patch.object(panel, "STATE_DIR", self.root),
            mock.patch.object(panel, "_sbe_board_dir", return_value=bdir),
            mock.patch.object(storyboard, "load_storyboard", return_value=self.board),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_a_lagging_shot_is_proposed(self):
        from unittest import mock
        with mock.patch.object(panel, "take_lipsync_best_lag_vs_song",
                               return_value={"score": 0.7, "lag_frames": 3, "fps": 24.0,
                                             "delta_sec": -0.125}):
            h = self.FakeHandler()
            h.post("edit/auto-align", {"id": "sb_t"})
        self.assertEqual(h.status, 200, h.payload)
        self.assertEqual(len(h.payload["proposals"]), 1)
        p = h.payload["proposals"][0]
        self.assertEqual(p["id"], "c1")
        self.assertEqual(p["lag_frames"], 3)
        # EDITOR-8 (Codex 4.17.0): the slip that CORRECTS a +3 frame lag is
        # three frames EARLIER in the source — the helper owns the sign.
        self.assertAlmostEqual(p["delta_sec"], -0.125)

    def test_a_shot_already_aligned_is_not_proposed(self):
        from unittest import mock
        with mock.patch.object(panel, "take_lipsync_best_lag_vs_song",
                               return_value={"score": 0.9, "lag_frames": 0, "fps": 24.0,
                                             "delta_sec": 0.0}):
            h = self.FakeHandler()
            h.post("edit/auto-align", {"id": "sb_t"})
        self.assertEqual(h.payload["proposals"], [])

    def test_a_non_a2v_shot_is_never_scored(self):
        from unittest import mock
        self.board["shots"][0]["mode"] = "text"
        with mock.patch.object(panel, "take_lipsync_best_lag_vs_song") as fn:
            h = self.FakeHandler()
            h.post("edit/auto-align", {"id": "sb_t"})
        fn.assert_not_called()
        self.assertEqual(h.payload["proposals"], [])

    def test_no_song_refuses_with_a_reason(self):
        import storyboard_editor as sedit
        from unittest import mock
        c = sedit.new_clip(str(self.clipfile), 0.0, 4.0, 0.0, id="c1")
        edit = sedit.normalise_edit(
            {"version": sedit.EDIT_VERSION, "board_id": "sb_t", "revision": 0,
             "source": "auto", "audio": None, "beats": None, "clips": [c], "settings": {}})
        sedit.save_edit(self.root / "sb_t", edit)
        h = self.FakeHandler()
        h.post("edit/auto-align", {"id": "sb_t"})
        self.assertEqual(h.status, 400)
        self.assertIn("song", h.payload["error"])


if __name__ == "__main__":
    unittest.main()
