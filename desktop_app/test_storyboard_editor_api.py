#!/usr/bin/env python3
"""Tests for the TIMELINE EDITOR's server: proxies, peaks, edit.json, routes.

Four things are locked here, and every one of them is a thing that would fail
silently if it broke.

1. **The proxy recipe.** All-intra or nothing. Our rendered clips are a single
   GOP — one keyframe for the whole clip — so a browser seeking into a source
   clip decodes from frame zero (235 ms median / 1266 ms p90 in Chrome; 3.5 ms
   against a proxy). Lose `-g 1` and the timeline still "works": it just
   becomes unusable, with no error anywhere. So the flags are asserted, by
   name, in the argv.

2. **One source used twice.** The whole reason `edit.json` is a separate file
   from `storyboard.json`. The cut index used to be a path->one-window map,
   which collapsed both uses of a repeated shot onto the last window — a
   silent wrong film. It is a list per path now, consumed in order.

3. **A bad edit never lands on a good one.** Validation runs before the write,
   the write is atomic, and the failure returns every problem at once instead
   of the first.

4. **The make_job allowlist.** `edit/generate` builds a shot and enqueues it
   through the panel's ordinary lane. A form field make_job does not name is
   dropped in silence — the known trap in this codebase — so the test enqueues
   for real and reads the params back off the queued job.

No GPU, no weights, no model download, no network. ffmpeg is only ever invoked
as an argv assertion or a mock.

Run:  python3 -m unittest test_storyboard_editor_api
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse

import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as panel                                        # noqa: E402
import storyboard                                                    # noqa: E402
import storyboard_editor as sedit                                    # noqa: E402


# =============================================================================
# helpers
# =============================================================================
def _clip(path, start, end, film_start, **kw) -> dict:
    return sedit.new_clip(path, start, end, film_start, **kw)


def _edit(clips, **kw) -> dict:
    doc = {"version": sedit.EDIT_VERSION, "board_id": "sb_t", "revision": 0,
           "source": "auto",
           "audio": None, "beats": None, "clips": clips, "settings": {}}
    doc.update(kw)
    return doc


def _board(clips, bid="sb_t", title="A Film") -> dict:
    return {"schema": 1, "id": bid, "title": title, "created_at": 1_700_000_000,
            "policy": storyboard.default_policy(), "cast": [], "engine_mode": "ltx",
            "shots": [
                {"n": i + 1, "title": f"shot {i + 1}", "mode": "text",
                 "engine": "ltx", "prompt": f"shot {i + 1} happens",
                 "duration_s": 5.0, "seed": 100 + i, "refs": [],
                 "status": "done", "draft_output": str(p)}
                for i, p in enumerate(clips)]}


class FakeHandler:
    """Just enough of `Handler` for the two dispatch methods under test.

    Deliberately not a real `BaseHTTPRequestHandler`: these routes are pure
    functions of (query, disk, queue) and a socket would only add ways for the
    test to be flaky.
    """

    def __init__(self):
        self.status = None
        self.payload = None
        self.error = None
        self.served = None
        # WHAT THE DISPATCHER IS TOLD, which is a different thing from what
        # was written. This used to be discarded, so the one branch that
        # answered a request and then reported it unhandled — writing a second
        # complete 404 response onto the same socket — was invisible to 250
        # green tests.
        self.handled = None

    def _json(self, payload, status: int = 200):
        self.payload, self.status = payload, status

    def send_error(self, code, *a):
        self.error, self.status = code, code

    def _serve_video_with_range(self, path):
        self.served = Path(path)

    # the two methods under test, bound to this stand-in
    def get(self, url: str):
        self.handled = panel.Handler._storyboard_edit_get(self, urlparse(url))
        return self

    def post(self, action: str, form: dict | None = None, body: str = ""):
        panel.Handler._storyboard_post(self, action, body, form or {})
        return self


class EditorCase(unittest.TestCase):
    """A scratch STATE_DIR with one board, patched over the module globals."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root / "state"
        self.out = self.root / "outputs"
        self.state.mkdir()
        self.out.mkdir()
        self.clips = []
        for i in range(3):
            p = self.root / f"S{i + 1:02d}.mp4"
            p.write_bytes(b"x" * (1000 + i))
            self.clips.append(p)
        self.board = _board(self.clips)
        self._patches = [
            mock.patch.object(panel, "STATE_DIR", self.state),
            mock.patch.object(panel, "OUTPUT", self.out),
            mock.patch.object(panel, "push", lambda *a, **k: None),
        ]
        for p in self._patches:
            p.start()
        storyboard.save_storyboard(self.state, self.board)
        self.bdir = storyboard.board_dir(self.state, "sb_t")
        panel._SBE_JOBS.clear()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        panel._SBE_JOBS.clear()
        self.tmp.cleanup()


# =============================================================================
# 1. The proxy recipe — the measurement this whole feature stands on
# =============================================================================
class ProxyRecipe(unittest.TestCase):
    def cmd(self, **kw) -> list[str]:
        return sedit.proxy_cmd("/bin/ffmpeg", "/x/S01.mp4", "/y/p.mp4", **kw)

    def test_every_frame_is_a_keyframe(self):
        # THE flag. Without it a proxy is a single GOP exactly like its source
        # and the 235 ms seek is unchanged — the feature looks built and is not.
        c = self.cmd()
        self.assertEqual(c[c.index("-g") + 1], "1")
        # libx264 will make its own GOP decisions regardless of -g unless both
        # of these say otherwise.
        self.assertEqual(c[c.index("-keyint_min") + 1], "1")
        self.assertEqual(c[c.index("-sc_threshold") + 1], "0")

    def test_no_b_frames(self):
        # Display order == decode order, so a backwards scrub does not re-read
        # a pyramid to show one frame.
        self.assertEqual(self.cmd()[self.cmd().index("-bf") + 1], "0")

    def test_carries_audio(self):
        # Recipe v2. The old recipe dropped audio on the reasoning that "the
        # timeline's sound is the soundtrack" — true for a music video, wrong
        # for a dialogue film, where a silent preview cannot tell you whether a
        # cut lands on the line or halfway through the word.
        c = self.cmd()
        self.assertNotIn("-an", c)
        self.assertIn("0:a:0?", c)          # OPTIONAL: a silent clip still proxies
        self.assertEqual(c[c.index("-c:a") + 1], "aac")
        self.assertEqual(c[c.index("-b:a") + 1], sedit.PROXY_ABITRATE)

    def test_audio_mapping_is_optional_so_silent_clips_still_build(self):
        # Without the trailing '?', a clip with no audio track fails the whole
        # prepare instead of producing a picture-only proxy.
        self.assertIn("0:a:0?", self.cmd())
        self.assertNotIn("0:a:0", [x for x in self.cmd() if x == "0:a:0"])

    def test_recipe_version_moved_so_silent_proxies_rebuild(self):
        # The version is inside the content hash, so bumping it invalidates
        # every proxy built by the old recipe exactly once.
        self.assertGreaterEqual(sedit.PROXY_RECIPE_VERSION, 2)

    def test_scaled_to_the_requested_width_with_an_even_height(self):
        vf = self.cmd(width=480)[self.cmd().index("-vf") + 1]
        self.assertEqual(vf, "scale='min(480,iw)':-2:flags=bicubic")

    def test_the_proxy_never_upscales_its_source(self):
        # The width is a CEILING, not a target. Engine output is commonly 1024
        # wide; a flat `scale=1280` would spend bytes and encode time inventing
        # detail that is not in the source and look no better for it.
        vf = self.cmd()[self.cmd().index("-vf") + 1]
        self.assertIn(f"min({sedit.PROXY_WIDTH},iw)", vf)

    def test_the_default_width_is_big_enough_to_watch(self):
        # 640 was chosen for seek speed and produced a preview upscaled >2x on a
        # normal window — reported as "the quality is pretty bad". All-intra is
        # what makes seeking fast, not the pixel count.
        self.assertGreaterEqual(sedit.PROXY_WIDTH, 1024)

    def test_faststart_so_the_browser_gets_the_moov_atom_first(self):
        c = self.cmd()
        self.assertEqual(c[c.index("-movflags") + 1], "+faststart")

    def test_argv_is_a_list_never_a_shell_string(self):
        self.assertTrue(all(isinstance(a, str) for a in self.cmd()))
        self.assertNotIn(" ", self.cmd()[0])


class ProxyAddressing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.src = self.root / "S01.mp4"
        self.src.write_bytes(b"a" * 100)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_same_file_gets_the_same_name(self):
        self.assertEqual(sedit.proxy_name(self.src), sedit.proxy_name(self.src))

    def test_a_rewritten_source_gets_a_different_name(self):
        before = sedit.proxy_name(self.src)
        self.src.write_bytes(b"b" * 250)          # different size AND mtime
        self.assertNotEqual(before, sedit.proxy_name(self.src))

    def test_two_sources_sharing_a_basename_do_not_collide(self):
        other = self.root / "sub"
        other.mkdir()
        twin = other / "S01.mp4"
        twin.write_bytes(b"a" * 100)
        self.assertNotEqual(sedit.proxy_name(self.src), sedit.proxy_name(twin))

    def test_changing_the_recipe_invalidates_every_proxy_exactly_once(self):
        before = sedit.proxy_name(self.src)
        with mock.patch.object(sedit, "PROXY_RECIPE_VERSION", 99):
            self.assertNotEqual(before, sedit.proxy_name(self.src))

    def test_a_missing_source_still_plans(self):
        # ffmpeg produces the honest error; the planner must not raise first.
        self.assertTrue(sedit.proxy_name(self.root / "gone.mp4").endswith(".mp4"))

    def test_a_repeated_clip_builds_ONE_proxy(self):
        plan = sedit.plan_proxies([{"path": str(self.src)},
                                   {"path": str(self.src)}], self.root)
        self.assertEqual(len(plan["build"]), 1)

    def test_an_existing_proxy_is_reused_not_rebuilt(self):
        d = sedit.proxy_dir(self.root)
        d.mkdir()
        (d / sedit.proxy_name(self.src)).write_bytes(b"proxy")
        plan = sedit.plan_proxies([{"path": str(self.src)}], self.root)
        self.assertEqual(plan["build"], [])
        self.assertEqual(len(plan["reuse"]), 1)

    def test_a_proxy_nothing_points_at_is_stale(self):
        d = sedit.proxy_dir(self.root)
        d.mkdir()
        junk = d / "orphan_deadbeef1234.mp4"
        junk.write_bytes(b"junk")
        plan = sedit.plan_proxies([{"path": str(self.src)}], self.root)
        self.assertEqual(plan["stale"], [junk])
        self.assertEqual(sedit.prune_proxies(plan["stale"]), 1)
        self.assertFalse(junk.exists())

    def test_pruning_never_raises_on_a_file_that_is_already_gone(self):
        self.assertEqual(sedit.prune_proxies([self.root / "nope.mp4"]), 0)


# =============================================================================
# 2. Peaks — server-side, because the client alternative is 100 MB of RAM
# =============================================================================
class Peaks(unittest.TestCase):
    SR = 22050

    def pcm(self, seconds=5.0):
        t = np.arange(int(self.SR * seconds)) / self.SR
        return (0.8 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)

    def peaks(self, **kw):
        return sedit.compute_peaks(
            "/x/song.mp3", decoder=lambda p, sr: self.pcm(), **kw)

    def test_min_and_max_are_interleaved_and_min_never_exceeds_max(self):
        p = self.peaks()
        vals = p["peaks"]
        self.assertEqual(len(vals), p["count"] * 2)
        for i in range(0, len(vals), 2):
            self.assertLessEqual(vals[i], vals[i + 1])

    def test_values_are_small_integers_not_floats(self):
        p = self.peaks()
        self.assertTrue(all(isinstance(v, int) for v in p["peaks"]))
        self.assertLessEqual(max(abs(v) for v in p["peaks"]), sedit.PEAKS_SCALE)

    def test_a_full_scale_signal_reaches_the_top_of_the_scale(self):
        p = sedit.compute_peaks("/x/s.wav",
                                decoder=lambda a, b: np.ones(22050, "float32"))
        self.assertEqual(max(p["peaks"]), sedit.PEAKS_SCALE)

    def test_the_bucket_rate_is_what_was_asked_for(self):
        p = self.peaks(buckets_per_second=50)
        self.assertAlmostEqual(p["buckets_per_second"], 50.0, delta=0.5)
        self.assertAlmostEqual(p["count"], 250, delta=2)

    def test_silence_is_not_an_error(self):
        p = sedit.compute_peaks("/x/s.wav",
                                decoder=lambda a, b: np.zeros(22050, "float32"))
        self.assertEqual(set(p["peaks"]), {0})

    def test_an_empty_decode_is_an_honest_refusal(self):
        with self.assertRaises(sedit.EditError):
            sedit.compute_peaks("/x/s.wav",
                                decoder=lambda a, b: np.zeros(0, "float32"))

    def test_the_file_on_disk_is_compact_not_pretty(self):
        # indent=2 puts every one of ~96,000 integers on its own line and
        # doubles the file. Measured: 795 KB pretty vs 345 KB compact on the
        # real 479 s track.
        with tempfile.TemporaryDirectory() as d:
            sedit.save_peaks(Path(d), self.peaks())
            text = sedit.peaks_path(Path(d)).read_text()
        self.assertNotIn("\n", text.strip())
        self.assertLess(len(text), 5.0 * 1024 * self.peaks()["duration"])

    def test_it_round_trips_through_disk(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.peaks()
            sedit.save_peaks(Path(d), p)
            self.assertEqual(sedit.load_peaks(Path(d))["peaks"], p["peaks"])

    def test_a_missing_or_corrupt_peaks_file_reads_as_None(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(sedit.load_peaks(Path(d)))
            sedit.peaks_path(Path(d)).write_text("{not json")
            self.assertIsNone(sedit.load_peaks(Path(d)))


# =============================================================================
# 3. edit.json — the arrangement, and everything that must not corrupt it
# =============================================================================
class EditSchema(unittest.TestCase):
    def test_a_clean_edit_validates(self):
        e = _edit([_clip("/x/a.mp4", 0.0, 3.0, 0.0),
                   _clip("/x/b.mp4", 1.0, 4.0, 3.0)])
        self.assertEqual(sedit.validate_edit(e), [])

    def test_film_slot_and_source_window_must_be_the_same_length(self):
        c = _clip("/x/a.mp4", 0.0, 3.0, 0.0)
        c["film_end"] = 9.0                      # 3 s of clip in a 9 s slot
        errs = sedit.validate_edit(_edit([c]))
        self.assertEqual([e["code"] for e in errs], ["clip_length_mismatch"])

    def test_a_window_past_the_end_of_its_source_is_refused(self):
        c = _clip("/x/a.mp4", 8.0, 12.0, 0.0, duration=10.0)
        self.assertIn("clip_past_the_end",
                      [e["code"] for e in sedit.validate_edit(_edit([c]))])

    def test_overlapping_clips_are_an_error(self):
        # One video track can only play one of them; a concat would silently
        # pick whichever came first.
        e = _edit([_clip("/x/a.mp4", 0.0, 4.0, 0.0),
                   _clip("/x/b.mp4", 0.0, 4.0, 2.0)])
        self.assertIn("clips_overlap",
                      [x["code"] for x in sedit.validate_edit(e)])

    def test_the_soundtrack_mode_is_checked_because_one_value_deletes_dialogue(self):
        # `replace` silences every line in the film. A typo must not fall
        # through to it, and the two good values must both survive the round
        # trip — reopening a film that was mixed `under` and silently
        # re-arming `replace` is the same bug arriving later.
        for mode in ("under", "replace"):
            e = _edit([_clip("/x/a.mp4", 0.0, 4.0, 0.0)])
            e["audio"] = {"path": "/x/song.wav", "offset": 0.0, "mode": mode}
            self.assertEqual(sedit.validate_edit(e), [], f"{mode} should be valid")
        e = _edit([_clip("/x/a.mp4", 0.0, 4.0, 0.0)])
        e["audio"] = {"path": "/x/song.wav", "offset": 0.0, "mode": "duck"}
        self.assertIn("audio_mode", [x["code"] for x in sedit.validate_edit(e)])

    def test_a_soundtrack_with_no_mode_is_still_valid(self):
        # Every edit.json written before the mode existed has no mode.
        e = _edit([_clip("/x/a.mp4", 0.0, 4.0, 0.0)])
        e["audio"] = {"path": "/x/song.wav", "offset": 0.0}
        self.assertEqual(sedit.validate_edit(e), [])

    def test_clips_that_merely_TOUCH_are_not_an_overlap(self):
        e = _edit([_clip("/x/a.mp4", 0.0, 4.0, 0.0),
                   _clip("/x/b.mp4", 0.0, 4.0, 4.0)])
        self.assertEqual(sedit.validate_edit(e), [])

    def test_a_GAP_is_not_an_error(self):
        # It is the hole somebody is about to generate a shot into. Refusing to
        # save it would make the feature impossible.
        e = _edit([_clip("/x/a.mp4", 0.0, 4.0, 0.0),
                   _clip("/x/b.mp4", 0.0, 4.0, 10.0)])
        self.assertEqual(sedit.validate_edit(e), [])
        gaps = sedit.edit_gaps(e)
        self.assertEqual(len(gaps), 1)
        self.assertAlmostEqual(gaps[0]["duration"], 6.0)
        self.assertEqual(gaps[0]["after"], 0)

    def test_a_gap_at_the_head_of_the_film_is_reported_as_after_minus_one(self):
        gaps = sedit.edit_gaps(_edit([_clip("/x/a.mp4", 0.0, 2.0, 5.0)]))
        self.assertEqual(gaps[0]["after"], -1)

    def test_the_same_source_twice_is_LEGAL_and_is_the_point(self):
        e = _edit([_clip("/x/a.mp4", 0.0, 2.0, 0.0),
                   _clip("/x/a.mp4", 6.0, 8.0, 2.0)])
        self.assertEqual(sedit.validate_edit(e), [])
        cuts = sedit.edit_to_cuts(e)
        self.assertEqual([(c["start"], c["end"]) for c in cuts],
                         [(0.0, 2.0), (6.0, 8.0)])

    def test_a_proxy_that_escapes_the_board_folder_is_refused(self):
        for bad in ("/etc/passwd", "../../secrets.mp4"):
            c = _clip("/x/a.mp4", 0.0, 2.0, 0.0, proxy=bad)
            self.assertIn("clip_proxy_escapes",
                          [e["code"] for e in sedit.validate_edit(_edit([c]))],
                          bad)

    def test_every_broken_clip_is_reported_not_just_the_first(self):
        e = _edit([_clip("/x/a.mp4", 0.0, 0.0, 0.0),
                   dict(_clip("/x/b.mp4", 0.0, 2.0, 5.0), source="sideways"),
                   dict(_clip("/x/c.mp4", 0.0, 2.0, 9.0), locked="yes")])
        codes = {e2["code"] for e2 in sedit.validate_edit(e)}
        self.assertEqual(codes, {"clip_window", "clip_film_window",
                                 "clip_source", "clip_locked"})

    def test_a_wrong_version_is_refused(self):
        self.assertIn("version",
                      [e["code"] for e in sedit.validate_edit(_edit([], version=7))])

    def test_junk_is_refused_rather_than_crashing(self):
        for junk in (None, [], "edit", 3):
            self.assertTrue(sedit.validate_edit(junk))

    def test_normalise_sorts_by_film_position_and_never_moves_a_cut(self):
        e = _edit([_clip("/x/b.mp4", 1.0, 3.0, 8.0),
                   _clip("/x/a.mp4", 0.0, 2.0, 0.0)])
        n = sedit.normalise_edit(e)
        self.assertEqual([c["path"] for c in n["clips"]],
                         ["/x/a.mp4", "/x/b.mp4"])
        self.assertEqual(n["clips"][1]["start"], 1.0)      # untouched
        self.assertEqual(n["duration"], 10.0)              # 8.0 + a 2 s clip

    def test_new_clip_derives_the_film_slot_rather_than_taking_it_twice(self):
        c = sedit.new_clip("/x/a.mp4", 2.0, 5.5, 10.0)
        self.assertEqual(c["film_end"], 13.5)


class EditPersistence(EditorCase):
    def test_a_malformed_edit_never_lands_on_a_good_one(self):
        good = _edit([_clip("/x/a.mp4", 0.0, 3.0, 0.0)])
        sedit.save_edit(self.bdir, good)
        before = sedit.edit_path(self.bdir).read_text()
        bad = _edit([_clip("/x/a.mp4", 5.0, 1.0, 0.0)])
        with self.assertRaises(sedit.EditError):
            sedit.save_edit(self.bdir, bad)
        self.assertEqual(sedit.edit_path(self.bdir).read_text(), before)

    def test_the_write_is_atomic_and_leaves_no_temp_files(self):
        sedit.save_edit(self.bdir, _edit([_clip("/x/a.mp4", 0.0, 3.0, 0.0)]))
        leftovers = [p.name for p in self.bdir.iterdir()
                     if p.name.startswith(".edit-")]
        self.assertEqual(leftovers, [])

    def test_the_revision_counts_up_on_every_save(self):
        e = _edit([_clip("/x/a.mp4", 0.0, 3.0, 0.0)])
        sedit.save_edit(self.bdir, e)
        self.assertEqual(sedit.load_edit(self.bdir)["revision"], 1)
        sedit.save_edit(self.bdir, sedit.load_edit(self.bdir))
        self.assertEqual(sedit.load_edit(self.bdir)["revision"], 2)

    def test_a_corrupt_edit_raises_rather_than_pretending_there_is_none(self):
        sedit.edit_path(self.bdir).write_text("{ half a fi")
        with self.assertRaises(sedit.EditError):
            sedit.load_edit(self.bdir)

    def test_no_edit_at_all_is_None_not_an_error(self):
        self.assertIsNone(sedit.load_edit(self.bdir))

    def test_a_human_edit_survives_a_full_round_trip(self):
        auto = sedit.edit_from_plan(
            [{"n": 1, "path": "/x/a.mp4", "start": 0.0, "end": 4.0,
              "film_start": 0.0, "film_end": 4.0,
              "snap": {"kind": "downbeat", "shift_ms": 12.0},
              "window": {"score": 0.8, "reason": "because", "usable": True,
                         "source_duration": 10.0},
              "notes": []}],
            board_id="sb_t")
        self.assertEqual(auto["clips"][0]["source"], "auto")
        sedit.save_edit(self.bdir, auto)
        human = sedit.load_edit(self.bdir)
        human["clips"][0]["start"] = 1.0
        human["clips"][0]["end"] = 5.0
        human["clips"][0]["source"] = "human"
        human["clips"][0]["locked"] = True
        sedit.save_edit(self.bdir, human)
        back = sedit.load_edit(self.bdir)
        self.assertEqual(back["clips"][0]["source"], "human")
        self.assertTrue(back["clips"][0]["locked"])
        self.assertEqual(back["clips"][0]["start"], 1.0)
        self.assertEqual(sedit.edit_to_cuts(back),
                         [{"path": "/x/a.mp4", "start": 1.0, "end": 5.0,
                           "film_start": 0.0}])

    def test_the_analysis_rides_along_so_the_UI_can_say_why(self):
        auto = sedit.edit_from_plan(
            [{"n": 1, "path": "/x/a.mp4", "start": 0.0, "end": 4.0,
              "film_start": 0.0, "film_end": 4.0,
              "snap": {"kind": "beat", "shift_ms": -30.0},
              "window": {"score": 0.42, "reason": "sharpness 0.9",
                         "usable": True, "source_duration": 10.0},
              "notes": ["a note"]}])
        a = auto["clips"][0]["analysis"]
        self.assertEqual(a["snap"]["kind"], "beat")
        self.assertEqual(a["reason"], "sharpness 0.9")
        self.assertEqual(a["notes"], ["a note"])

    def test_the_beat_grid_is_slimmed_but_the_grid_itself_survives(self):
        fat = {"bpm": 126.0, "period": 0.476, "phase": 0.1, "meter": 4,
               "confidence": 0.7, "span": [0, 60],
               "beats": [0.1, 0.6], "downbeats": [0.1],
               "diagnostics": {"grid_lock_ms": 9.0, "tempo_drift_bpm": 0.2,
                               "runners_up": [{"bpm": 63.0}] * 3,
                               "n_onsets": 400}}
        slim = sedit.edit_from_plan([], beats=fat)["beats"]
        self.assertEqual(slim["beats"], [0.1, 0.6])
        self.assertEqual(slim["grid_lock_ms"], 9.0)
        self.assertNotIn("runners_up", slim)


# =============================================================================
# 4. A shot used twice must actually RENDER twice
# =============================================================================
class RepeatedShots(unittest.TestCase):
    PROBES = [
        (Path("/x/A.mp4"), {"w": 1024, "h": 576, "duration": 10.0,
                            "has_audio": True, "sample_rate": 48000}),
        (Path("/x/A.mp4"), {"w": 1024, "h": 576, "duration": 10.0,
                            "has_audio": True, "sample_rate": 48000}),
        (Path("/x/B.mp4"), {"w": 1024, "h": 576, "duration": 10.0,
                            "has_audio": True, "sample_rate": 48000}),
    ]
    PLAN = [{"path": "/x/A.mp4", "start": 0.0, "end": 2.0},
            {"path": "/x/A.mp4", "start": 7.0, "end": 9.0},
            {"path": "/x/B.mp4", "start": 1.0, "end": 4.0}]

    def test_the_index_keeps_every_occurrence(self):
        idx = panel._sb_cut_index(self.PLAN)
        self.assertEqual(len(idx["/x/A.mp4"]), 2)
        self.assertEqual(idx["/x/A.mp4"][1]["start"], 7.0)

    def test_each_use_gets_its_OWN_window_in_the_graph(self):
        g, _ = panel._sb_film_filtergraph(
            self.PROBES, 1024, 576, 48000, "yuv420p",
            cuts=panel._sb_cut_index(self.PLAN))
        self.assertIn("[0:v]trim=start=0.000000:end=2.000000", g)
        self.assertIn("[1:v]trim=start=7.000000:end=9.000000", g)
        self.assertIn("[2:v]trim=start=1.000000:end=4.000000", g)

    def test_a_hand_built_single_window_dict_still_works(self):
        # The pre-editor shape. Nothing that passed a dict may break.
        g, _ = panel._sb_film_filtergraph(
            self.PROBES[:1], 1024, 576, 48000, "yuv420p",
            cuts={"/x/A.mp4": {"start": 1.0, "end": 2.0}})
        self.assertIn("[0:v]trim=start=1.000000:end=2.000000", g)

    def test_windows_are_consumed_in_order_and_then_the_clip_plays_whole(self):
        w = panel._sb_segment_windows(self.PROBES + [self.PROBES[0]],
                                      panel._sb_cut_index(self.PLAN))
        self.assertEqual([x["start"] if x else None for x in w],
                         [0.0, 7.0, 1.0, None])

    def test_an_unreadable_clip_still_cannot_shift_anyone_elses_window(self):
        # The property the path-keying exists for, preserved through the
        # change to a list.
        idx = panel._sb_cut_index(self.PLAN)
        g, _ = panel._sb_film_filtergraph(
            [self.PROBES[0], self.PROBES[2]], 1024, 576, 48000, "yuv420p",
            cuts=idx)
        self.assertIn("[1:v]trim=start=1.000000:end=4.000000", g)


# =============================================================================
# 5. The routes
# =============================================================================
class ReadRoutes(EditorCase):
    def test_every_read_route_tells_the_dispatcher_it_answered(self):
        # do_GET reads the return value as "did anybody serve this" and falls
        # through to send_error(404) when it is false. The uploads branch
        # ended in a bare `return`, so a request that had already been
        # answered in full — status, headers, Content-Length and body on the
        # wire — got a second complete 404 response written behind it. It is
        # survivable only because this handler never sets protocol_version;
        # with keep-alive those bytes become the head of the next response.
        sedit.save_edit(self.bdir, _edit([]))     # so `edit` reads rather than cuts
        for url in ("/storyboard/edit?id=sb_t",
                    "/storyboard/edit/status?id=sb_t",
                    "/storyboard/edit/peaks?id=sb_t",
                    "/storyboard/edit/uploads",
                    "/storyboard/edit/drafts?id=sb_t",
                    "/storyboard/edit/versions?id=sb_t"):
            self.assertIs(FakeHandler().get(url).handled, True, url)
        # ...and a path this method does not own says so, exactly once.
        self.assertIs(FakeHandler().get("/outputs").handled, False)

    def test_the_drafts_and_versions_routes_check_the_film_exists(self):
        # Every other editor route opens with a load that 404s an unknown id;
        # these two built a board path straight out of the query string, and
        # `storyboard.board_dir` does no sanitising at all.
        for url in ("/storyboard/edit/drafts?id=nope",
                    "/storyboard/edit/versions?id=nope",
                    "/storyboard/edit/drafts?id=../../..",
                    "/storyboard/edit/versions?id=../../.."):
            h = FakeHandler().get(url)
            self.assertEqual(h.status, 404, url)
            self.assertFalse(h.payload["ok"], url)

    def test_GET_edit_auto_generates_once_and_then_reads_from_disk(self):
        plan = [{"n": i + 1, "path": str(p), "start": 0.0, "end": 3.0,
                 "film_start": i * 3.0, "film_end": (i + 1) * 3.0,
                 "snap": {"kind": "none", "shift_ms": 0.0},
                 "window": {"score": 0.5, "reason": "r", "usable": True,
                            "source_duration": 5.0},
                 "notes": []}
                for i, p in enumerate(self.clips)]
        with mock.patch("storyboard_edit.plan_cut", return_value=plan) as pc:
            h = FakeHandler().get("/storyboard/edit?id=sb_t")
            self.assertTrue(h.payload["ok"])
            self.assertTrue(h.payload["generated"])
            self.assertEqual(len(h.payload["edit"]["clips"]), 3)
            h2 = FakeHandler().get("/storyboard/edit?id=sb_t")
            self.assertFalse(h2.payload["generated"])
            self.assertEqual(pc.call_count, 1)      # not re-analysed

    def test_GET_edit_on_an_unknown_board_is_a_404(self):
        h = FakeHandler().get("/storyboard/edit?id=nope")
        self.assertEqual(h.status, 404)

    def test_GET_edit_on_a_CORRUPT_edit_refuses_rather_than_replacing_it(self):
        sedit.edit_path(self.bdir).write_text("{ broken")
        h = FakeHandler().get("/storyboard/edit?id=sb_t")
        self.assertEqual(h.status, 500)
        self.assertTrue(h.payload["corrupt"])
        self.assertEqual(sedit.edit_path(self.bdir).read_text(), "{ broken")

    def test_the_payload_carries_the_gaps_and_the_unplaced_clips(self):
        sedit.save_edit(self.bdir, _edit(
            [_clip(str(self.clips[0]), 0.0, 2.0, 0.0),
             _clip(str(self.clips[1]), 0.0, 2.0, 6.0)]))
        h = FakeHandler().get("/storyboard/edit?id=sb_t")
        self.assertEqual(len(h.payload["gaps"]), 1)
        self.assertEqual([u["path"] for u in h.payload["unplaced"]],
                         [str(self.clips[2])])
        self.assertEqual(h.payload["duration"], 8.0)

    def test_the_proxy_pointer_is_refreshed_on_every_read(self):
        d = sedit.proxy_dir(self.bdir)
        d.mkdir(parents=True)
        name = sedit.proxy_name(self.clips[0])
        (d / name).write_bytes(b"proxy")
        sedit.save_edit(self.bdir, _edit(
            [_clip(str(self.clips[0]), 0.0, 2.0, 0.0)]))
        h = FakeHandler().get("/storyboard/edit?id=sb_t")
        self.assertEqual(h.payload["edit"]["clips"][0]["proxy"],
                         f"proxy/{name}")
        (d / name).unlink()
        h = FakeHandler().get("/storyboard/edit?id=sb_t")
        self.assertIsNone(h.payload["edit"]["clips"][0]["proxy"])

    def test_GET_peaks_is_404_before_prepare_and_the_data_after(self):
        h = FakeHandler().get("/storyboard/edit/peaks?id=sb_t")
        self.assertEqual(h.status, 404)
        sedit.save_peaks(self.bdir, {"version": 1, "count": 2,
                                     "peaks": [-3, 4, -5, 6]})
        h = FakeHandler().get("/storyboard/edit/peaks?id=sb_t")
        self.assertEqual(h.payload["peaks"], [-3, 4, -5, 6])

    def test_GET_status_answers_even_before_a_prepare_has_ever_run(self):
        h = FakeHandler().get("/storyboard/edit/status?id=sb_t")
        self.assertEqual(h.payload, {"ok": True, "id": "sb_t", "prepare": {}})

    def test_a_proxy_is_served_through_the_RANGE_path(self):
        # Safari will not seek a <video> whose server answers a plain 200, so
        # serving these any other way undoes the reason proxies exist.
        d = sedit.proxy_dir(self.bdir)
        d.mkdir(parents=True)
        (d / "a_1234.mp4").write_bytes(b"proxy bytes")
        h = FakeHandler().get("/storyboard/edit/proxy?id=sb_t&name=a_1234.mp4")
        self.assertEqual(h.served, (d / "a_1234.mp4").resolve())

    def test_a_proxy_name_cannot_escape_the_board_folder(self):
        for name in ("../../../../etc/passwd", "..%2Fx.mp4", "/etc/hosts",
                     "sub/dir.mp4", "", "x.mp4x"):
            h = FakeHandler().get(
                f"/storyboard/edit/proxy?id=sb_t&name={name}")
            self.assertIn(h.status, (400, 404), name)
            self.assertIsNone(h.served, name)

    def test_a_non_editor_path_is_not_claimed(self):
        h = FakeHandler()
        self.assertFalse(
            panel.Handler._storyboard_edit_get(h, urlparse("/storyboard/list")))


class TheCompareAndSwapItself(EditorCase):
    """`save_edit(expect=…)` — the guard, at the place the write happens."""

    def _one(self, clip):
        return _edit([_clip(str(clip), 0.0, 2.0, 0.0)])

    def test_a_matching_expectation_writes_and_bumps_from_the_disk(self):
        sedit.save_edit(self.bdir, self._one(self.clips[0]))
        sedit.save_edit(self.bdir, self._one(self.clips[1]), expect=1)
        self.assertEqual(sedit.load_edit(self.bdir)["revision"], 2)

    def test_a_stale_expectation_raises_and_writes_NOTHING(self):
        sedit.save_edit(self.bdir, self._one(self.clips[0]))
        sedit.save_edit(self.bdir, sedit.load_edit(self.bdir))
        self.assertEqual(sedit.on_disk_revision(self.bdir), 2)
        with self.assertRaises(sedit.EditConflict) as caught:
            sedit.save_edit(self.bdir, self._one(self.clips[1]), expect=1)
        self.assertEqual(caught.exception.revision, 2)
        got = sedit.load_edit(self.bdir)
        self.assertEqual(got["revision"], 2)
        self.assertEqual(got["clips"][0]["path"], str(self.clips[0]))

    def test_a_conflict_IS_an_edit_error_so_old_callers_still_refuse(self):
        # Every `except EditError` around a save still stops the write and
        # still has a sentence to show. Nobody has to learn a new exception
        # to stay correct.
        self.assertTrue(issubclass(sedit.EditConflict, sedit.EditError))

    def test_expecting_a_revision_on_an_empty_board_is_zero(self):
        sedit.save_edit(self.bdir, self._one(self.clips[0]), expect=0)
        self.assertEqual(sedit.load_edit(self.bdir)["revision"], 1)

    def test_the_raw_revision_read_matches_what_load_edit_reports(self):
        self.assertEqual(sedit.on_disk_revision(self.bdir), 0)
        for want in (1, 2, 3):
            sedit.save_edit(self.bdir, sedit.load_edit(self.bdir)
                            or self._one(self.clips[0]))
            self.assertEqual(sedit.on_disk_revision(self.bdir), want)
            self.assertEqual(sedit.load_edit(self.bdir)["revision"], want)

    def test_the_lock_is_PER_BOARD_and_not_one_queue_for_the_whole_panel(self):
        # Two people cutting two films have nothing to say to each other.
        other = self.state / "boards" / "sb_other"
        other.mkdir(parents=True, exist_ok=True)
        self.assertIs(sedit.board_write_lock(self.bdir),
                      sedit.board_write_lock(self.bdir))
        self.assertIsNot(sedit.board_write_lock(self.bdir),
                         sedit.board_write_lock(other))
        # ...and holding one must not block the other, or a slow save on one
        # film would stall every other tab in the panel.
        done = threading.Event()

        def other_board():
            with sedit.board_write_lock(other):
                done.set()

        with sedit.board_write_lock(self.bdir):
            t = threading.Thread(target=other_board, daemon=True)
            t.start()
            self.assertTrue(done.wait(timeout=5),
                            "an unrelated board queued behind this one")
        t.join(timeout=5)


class WriteRoutes(EditorCase):
    def test_save_persists_and_answers_with_the_full_payload(self):
        e = _edit([_clip(str(self.clips[0]), 0.0, 2.0, 0.0)])
        h = FakeHandler().post("edit/save", {},
                               json.dumps({"id": "sb_t", "edit": e}))
        self.assertTrue(h.payload["ok"])
        self.assertEqual(sedit.load_edit(self.bdir)["revision"], 1)
        self.assertEqual(h.payload["duration"], 2.0)

    def test_save_returns_EVERY_error_and_writes_nothing(self):
        good = _edit([_clip(str(self.clips[0]), 0.0, 2.0, 0.0)])
        sedit.save_edit(self.bdir, good)
        bad = _edit([_clip("/x/a.mp4", 4.0, 1.0, 0.0),
                     dict(_clip("/x/b.mp4", 0.0, 2.0, 5.0), source="nope")])
        h = FakeHandler().post("edit/save", {},
                               json.dumps({"id": "sb_t", "edit": bad}))
        self.assertEqual(h.status, 400)
        self.assertEqual({e["code"] for e in h.payload["errors"]},
                         {"clip_window", "clip_film_window", "clip_source"})
        self.assertEqual(sedit.load_edit(self.bdir)["revision"], 1)

    def test_a_stale_tab_cannot_wind_the_revision_backwards(self):
        e = _edit([_clip(str(self.clips[0]), 0.0, 2.0, 0.0)])
        for _ in range(3):
            sedit.save_edit(self.bdir, sedit.load_edit(self.bdir) or e)
        self.assertEqual(sedit.load_edit(self.bdir)["revision"], 3)
        stale = _edit([_clip(str(self.clips[0]), 0.0, 2.0, 0.0)], revision=0)
        FakeHandler().post("edit/save", {},
                           json.dumps({"id": "sb_t", "edit": stale}))
        self.assertEqual(sedit.load_edit(self.bdir)["revision"], 4)

    def test_expect_revision_turns_a_silent_overwrite_into_a_409(self):
        sedit.save_edit(self.bdir, _edit([_clip(str(self.clips[0]),
                                                0.0, 2.0, 0.0)]))
        body = json.dumps({"id": "sb_t", "expect_revision": 0,
                           "edit": _edit([_clip(str(self.clips[1]),
                                                0.0, 2.0, 0.0)])})
        h = FakeHandler().post("edit/save", {}, body)
        self.assertEqual(h.status, 409)
        self.assertTrue(h.payload["conflict"])
        self.assertEqual(h.payload["revision"], 1)
        # ...and the other tab's arrangement is untouched
        self.assertEqual(sedit.load_edit(self.bdir)["clips"][0]["path"],
                         str(self.clips[0]))

    # -- THE RACE, and it is the one the guard above could not catch --------
    # `expect_revision` is a compare-and-swap whose compare and whose swap
    # used to be in different critical sections. The panel is a
    # ThreadingHTTPServer, so two tabs whose debounces landed together both
    # read revision 7, both compared 7 == 7, and both wrote. Both got HTTP
    # 200 and one arrangement was gone — recoverable only from history/ and
    # only by somebody who knew to look.
    def _race(self, expect, gate_after=2):
        """Two tabs, both held past the read-and-compare, then released."""
        gate = threading.Barrier(gate_after, timeout=20)
        real = sedit.validate_edit
        held = set()
        held_lock = threading.Lock()

        def gated(doc):
            out = real(doc)
            # ONLY THE FIRST CALL ON EACH THREAD, which is the handler's own
            # check. `save_edit` validates again on its way in, and waiting
            # there would be waiting for a partner that has already gone past.
            tid = threading.get_ident()
            with held_lock:
                first = tid not in held
                held.add(tid)
            if first:
                gate.wait()
            return out

        got = {}

        def tab(name, clip):
            body = json.dumps(
                {"id": "sb_t", "edit": _edit([_clip(str(clip), 0.0, 2.0, 0.0)]),
                 **({} if expect is None else {"expect_revision": expect})})
            got[name] = FakeHandler().post("edit/save", {}, body)

        with mock.patch.object(sedit, "validate_edit", gated):
            ts = [threading.Thread(target=tab, args=(n, c), daemon=True)
                  for n, c in (("a", self.clips[1]), ("b", self.clips[2]))]
            for t in ts:
                t.start()
            for t in ts:
                t.join(timeout=25)
            for t in ts:
                self.assertFalse(t.is_alive(), "a save thread never returned")
        return got

    def test_TWO_TABS_SAVING_AT_ONCE_CANNOT_BOTH_BE_TOLD_THEY_WON(self):
        sedit.save_edit(self.bdir, _edit([_clip(str(self.clips[0]),
                                                0.0, 2.0, 0.0)]))
        self.assertEqual(sedit.load_edit(self.bdir)["revision"], 1)
        got = self._race(expect=1)
        codes = sorted((got["a"].status or 200, got["b"].status or 200))
        self.assertEqual(codes, [200, 409],
                         "both tabs were told they won — the write race is "
                         "back and one arrangement was overwritten in silence")
        loser = got["a"] if (got["a"].status == 409) else got["b"]
        winner = got["b"] if loser is got["a"] else got["a"]
        # THE LOSER GETS THE SAME HONEST 409 THE SEQUENTIAL CASE PRODUCES —
        # the body the client already knows how to answer, naming the
        # revision it was overtaken by.
        self.assertTrue(loser.payload["conflict"])
        self.assertEqual(loser.payload["revision"], 2)
        self.assertIn("moved on without you", loser.payload["error"])
        self.assertFalse(loser.payload["ok"])
        self.assertTrue(winner.payload["ok"])
        # ...and the file holds exactly one of the two arrangements, at one
        # revision, rather than a bump per writer.
        on_disk = sedit.load_edit(self.bdir)
        self.assertEqual(on_disk["revision"], 2)
        self.assertEqual(on_disk["clips"][0]["path"],
                         winner.payload["edit"]["clips"][0]["path"])

    def test_the_loser_did_not_lose_the_revision_counter_as_well(self):
        # A refused write must leave the counter where the WINNER put it: a
        # second bump would make the next legitimate save look stale.
        sedit.save_edit(self.bdir, _edit([_clip(str(self.clips[0]),
                                                0.0, 2.0, 0.0)]))
        self._race(expect=1)
        self.assertEqual(sedit.load_edit(self.bdir)["revision"], 2)

    def test_an_absent_guard_is_still_last_write_wins_and_says_so(self):
        # THE DECISION: accepted, and logged. The client deliberately sends no
        # `expect_revision` for "Keep mine" — a user looking at a conflict and
        # choosing to overwrite — so refusing an unguarded save would strand
        # the arrangement on screen with no button that could answer.
        sedit.save_edit(self.bdir, _edit([_clip(str(self.clips[0]),
                                                0.0, 2.0, 0.0)]))
        lines = []
        with mock.patch.object(panel, "push", lambda s: lines.append(s)):
            got = self._race(expect=None)
        self.assertEqual([got["a"].status, got["b"].status], [200, 200])
        self.assertTrue(got["a"].payload["ok"] and got["b"].payload["ok"])
        # AND THIS IS WHAT LAST-WRITE-WINS COSTS, stated rather than hidden:
        # both tabs read revision 1, both wrote 2, and only one arrangement
        # survives. That is the deal "Keep mine" asks for — the difference is
        # that the log now names it and the loser is in history/.
        self.assertEqual(sedit.load_edit(self.bdir)["revision"], 2)
        unguarded = [ln for ln in lines if "unguarded save" in ln]
        self.assertEqual(len(unguarded), 2, lines)
        self.assertIn("last write wins", unguarded[0])

    def test_save_refuses_junk_bodies_politely(self):
        self.assertEqual(FakeHandler().post("edit/save", {}, "{oops").status, 400)
        self.assertEqual(
            FakeHandler().post("edit/save", {},
                               json.dumps({"id": "sb_t"})).status, 400)

    def test_prepare_starts_a_job_and_refuses_a_second_one(self):
        started = []

        def fake_thread(bid, music, target):
            started.append((bid, music, target))

        with mock.patch.object(panel, "_sbe_prepare_thread", fake_thread):
            h = FakeHandler().post("edit/prepare", {"id": "sb_t"})
            self.assertEqual(h.status, 202)
        self.assertEqual(started, [("sb_t", None, None)])
        # the fake thread never finished the job, so the slot is still held
        h = FakeHandler().post("edit/prepare", {"id": "sb_t"})
        self.assertEqual(h.status, 409)
        self.assertTrue(h.payload["busy"])

    def test_prepare_refuses_a_soundtrack_that_is_not_there(self):
        h = FakeHandler().post("edit/prepare",
                               {"id": "sb_t", "music": "/x/nope.mp3"})
        self.assertEqual(h.status, 400)

    def test_cancel_sets_the_flag_the_build_loop_reads(self):
        with mock.patch.object(panel, "_sbe_prepare_thread",
                               lambda *a: None):
            FakeHandler().post("edit/prepare", {"id": "sb_t"})
        FakeHandler().post("edit/cancel", {"id": "sb_t"})
        self.assertTrue(panel._sbe_cancelled("sb_t"))

    def test_auto_rebuilds_the_arrangement_ON_PURPOSE(self):
        human = _edit([_clip(str(self.clips[0]), 1.0, 2.0, 0.0,
                             source="human")])
        sedit.save_edit(self.bdir, human)
        plan = [{"n": 1, "path": str(self.clips[0]), "start": 0.0, "end": 3.0,
                 "film_start": 0.0, "film_end": 3.0,
                 "snap": {"kind": "none", "shift_ms": 0.0},
                 "window": {"score": 0.5, "reason": "r", "usable": True,
                            "source_duration": 5.0},
                 "notes": []}]
        with mock.patch("storyboard_edit.plan_cut", return_value=plan):
            h = FakeHandler().post("edit/auto", {"id": "sb_t"})
        self.assertTrue(h.payload["ok"])
        back = sedit.load_edit(self.bdir)
        self.assertEqual(back["clips"][0]["source"], "auto")
        self.assertEqual(back["clips"][0]["end"], 3.0)
        self.assertEqual(back["revision"], 2)      # continues the history


class RenderRoute(EditorCase):
    def edit_with(self, clips):
        sedit.save_edit(self.bdir, _edit(clips))

    def test_the_render_uses_the_EXISTING_assembler_with_the_edits_cuts(self):
        self.edit_with([_clip(str(self.clips[0]), 0.0, 2.0, 0.0),
                        _clip(str(self.clips[0]), 5.0, 7.0, 2.0),
                        _clip(str(self.clips[1]), 1.0, 3.0, 4.0)])
        with mock.patch.object(panel, "_sb_assemble_film",
                               return_value={"ok": True, "clips": 3}) as af:
            h = FakeHandler().post("edit/render", {"id": "sb_t"})
        self.assertTrue(h.payload["ok"])
        args, kw = af.call_args
        # one entry PER TIMELINE CLIP, repeats included, in film order
        self.assertEqual(args[0], [str(self.clips[0]), str(self.clips[0]),
                                   str(self.clips[1])])
        # `timeline=`, not `plan=`. Same list, same order, same windows — but
        # the argument that can also carry a kind, because a slug has no path
        # and so cannot be described by args[0] at all.
        self.assertIsNone(kw.get("plan"))
        self.assertEqual([(c["start"], c["end"]) for c in kw["timeline"]],
                         [(0.0, 2.0), (5.0, 7.0), (1.0, 3.0)])

    def test_the_soundtrack_and_its_offset_ride_to_the_assembler(self):
        song = self.root / "song.mp3"
        song.write_bytes(b"mp3")
        sedit.save_edit(self.bdir, _edit(
            [_clip(str(self.clips[0]), 0.0, 2.0, 0.0)],
            audio={"path": str(song), "offset": 12.5}))
        with mock.patch.object(panel, "_sb_assemble_film",
                               return_value={"ok": True}) as af:
            FakeHandler().post("edit/render", {"id": "sb_t"})
        self.assertEqual(af.call_args.kwargs["music"], str(song))
        self.assertEqual(af.call_args.kwargs["music_start"], 12.5)

    def test_gaps_are_DISCLOSED_because_the_concat_closes_them(self):
        self.edit_with([_clip(str(self.clips[0]), 0.0, 2.0, 0.0),
                        _clip(str(self.clips[1]), 0.0, 2.0, 7.0)])
        with mock.patch.object(panel, "_sb_assemble_film",
                               return_value={"ok": True, "duration": 4.0}):
            h = FakeHandler().post("edit/render", {"id": "sb_t"})
        self.assertEqual(len(h.payload["gaps"]), 1)
        self.assertIn("closed by the concatenation", h.payload["gaps_note"])
        self.assertEqual(h.payload["timeline_duration"], 9.0)

    def test_an_empty_timeline_is_an_honest_refusal(self):
        self.edit_with([])
        h = FakeHandler().post("edit/render", {"id": "sb_t"})
        self.assertEqual(h.status, 400)

    def test_no_edit_at_all_says_so(self):
        self.assertEqual(FakeHandler().post("edit/render",
                                            {"id": "sb_t"}).status, 404)

    def test_the_output_name_cannot_escape_the_export_folder(self):
        self.edit_with([_clip(str(self.clips[0]), 0.0, 2.0, 0.0)])
        h = FakeHandler().post("edit/render",
                               {"id": "sb_t", "out": "../../evil.mp4"})
        self.assertEqual(h.status, 400)

    def test_a_failed_assembly_is_a_500_carrying_the_reason(self):
        self.edit_with([_clip(str(self.clips[0]), 0.0, 2.0, 0.0)])
        with mock.patch.object(panel, "_sb_assemble_film",
                               return_value={"ok": False, "error": "boom"}):
            h = FakeHandler().post("edit/render", {"id": "sb_t"})
        self.assertEqual(h.status, 500)
        self.assertEqual(h.payload["error"], "boom")


class GenerateIntoAGap(EditorCase):
    """The make_job allowlist trap, checked against the real queue."""

    def setUp(self):
        super().setUp()
        self._q = mock.patch.dict(panel.STATE, {"queue": [], "history": [],
                                                "current": None}, clear=False)
        self._q.start()
        self._pq = mock.patch.object(panel, "persist_queue", lambda: None)
        self._pq.start()

    def tearDown(self):
        self._pq.stop()
        self._q.stop()
        super().tearDown()

    def gen(self, **form):
        f = {"id": "sb_t", "prompt": "a slow push in on the gate",
             "duration": "5", "film_start": "12.5"}
        f.update(form)
        return FakeHandler().post("edit/generate", f)

    def test_it_enqueues_through_the_panels_ONE_queue(self):
        h = self.gen()
        self.assertEqual(h.status, 202)
        self.assertEqual(len(panel.STATE["queue"]), 1)
        self.assertEqual(panel.STATE["queue"][0]["id"], h.payload["job_id"])

    def test_the_fields_SURVIVE_make_jobs_allowlist(self):
        # The trap: a form field make_job does not name is dropped in silence,
        # so a control can look wired and do nothing. This asserts against the
        # params actually stamped onto the queued job — not against the form.
        h = self.gen(prompt="the gate opens onto white light", duration="3")
        p = panel.STATE["queue"][0]["params"]
        self.assertEqual(p["prompt"], "the gate opens onto white light")
        self.assertEqual(p["mode"], "t2v")
        self.assertEqual(p["engine"], "ltx")
        self.assertEqual(p["frames"], storyboard.ltx_frames_for(3.0))
        self.assertEqual(p["session_tag"], f"sb:sb_t#{h.payload['n']}")
        self.assertEqual(p["label"], f"S{h.payload['n']:02d} · A Film")
        self.assertFalse(p["enhance"])          # never re-write a planned prompt
        self.assertFalse(p["open_when_done"])   # no QuickTime window per shot
        # ...and the endpoint REPORTS what landed, so a client sees it too.
        self.assertEqual(h.payload["params"]["prompt"], p["prompt"])
        self.assertEqual(h.payload["params"]["frames"], p["frames"])

    def test_the_duration_control_actually_changes_the_render(self):
        self.gen(duration="3")
        short = panel.STATE["queue"][0]["params"]["frames"]
        self.gen(duration="8")
        long = panel.STATE["queue"][1]["params"]["frames"]
        self.assertLess(short, long)

    def test_the_slot_is_remembered_on_the_board(self):
        h = self.gen(film_start="21.25")
        board = storyboard.load_storyboard(self.state, "sb_t")
        shot = board["shots"][-1]
        self.assertEqual(shot["edit_slot"],
                         {"film_start": 21.25, "duration": 5.0})
        self.assertEqual(shot["draft_job_id"], h.payload["job_id"])
        self.assertEqual(shot["status"], "queued")

    def test_the_new_shot_shows_up_as_UNPLACED_never_auto_inserted(self):
        # The arrangement belongs to the human. A server that appends to it
        # moves their cuts.
        sedit.save_edit(self.bdir, _edit(
            [_clip(str(self.clips[0]), 0.0, 2.0, 0.0)]))
        self.gen()
        board = storyboard.load_storyboard(self.state, "sb_t")
        landed = self.root / "generated.mp4"
        landed.write_bytes(b"clip")
        board["shots"][-1]["draft_output"] = str(landed)
        board["shots"][-1]["status"] = "done"
        storyboard.save_storyboard(self.state, board)
        h = FakeHandler().get("/storyboard/edit?id=sb_t")
        self.assertEqual(len(h.payload["edit"]["clips"]), 1)
        slots = [u["slot"] for u in h.payload["unplaced"]
                 if u["path"] == str(landed)]
        self.assertEqual(slots, [{"film_start": 12.5, "duration": 5.0}])

    def test_an_empty_prompt_is_refused_before_anything_is_written(self):
        h = self.gen(prompt="")
        self.assertEqual(h.status, 400)
        self.assertEqual(panel.STATE["queue"], [])
        self.assertEqual(
            len(storyboard.load_storyboard(self.state, "sb_t")["shots"]), 3)

    def test_an_impossible_duration_is_refused(self):
        for d in ("0", "-4", "600"):
            self.assertEqual(self.gen(duration=d).status, 400, d)
        self.assertEqual(panel.STATE["queue"], [])

    def test_an_uninstalled_character_is_refused_rather_than_rendering_a_stranger(self):
        with mock.patch.object(panel, "_sb_known_character_ids",
                               return_value=["aria"]):
            h = self.gen(character_id="ghost")
        self.assertEqual(h.status, 400)
        self.assertEqual(panel.STATE["queue"], [])
        # ...and the board is not left carrying a shot that cannot render.
        self.assertEqual(
            len(storyboard.load_storyboard(self.state, "sb_t")["shots"]), 3)

    def test_an_INSTALLED_character_queues_with_its_face_lora_and_trigger(self):
        # Machine-independent: the character library is mocked, because a test
        # that only passes on a Mac with `aria` trained is not a test.
        face = self.root / "aria.safetensors"
        face.write_bytes(b"lora")
        rec = {"id": "aria", "trigger": "ariatrn",
               "face_lora_path": str(face), "audio_lora_path": None}
        compat = {"ltx_compatible": True, "ltx_compat_reason": "",
                  "ltx_fusion_tally": None}
        with mock.patch.object(panel, "_sb_known_character_ids",
                               return_value=["aria"]), \
             mock.patch.object(panel, "list_characters", return_value=[rec]), \
             mock.patch.object(panel, "_ltx_lora_compatibility",
                               return_value=compat):
            h = self.gen(character_id="aria", trigger="ariatrn",
                         prompt="ariatrn walks through the gate")
        self.assertEqual(h.status, 202)
        p = panel.STATE["queue"][0]["params"]
        self.assertEqual(p["character_id"], "aria")
        self.assertIn("ariatrn", p["prompt"])       # the trigger, mechanically
        self.assertTrue(p["loras"])                 # the face actually loads
        # The pass quality was translated into the character vocabulary on the
        # way through, or this request would have been refused outright.
        self.assertIsNotNone(panel.resolve_character_quality(
            storyboard.CHARACTER_QUALITY_FOR_PASS[
                self.board["policy"]["draft"]["quality"]],
            panel.ACTIVE_MODEL_VERSION))

    def test_the_final_pass_uses_the_boards_delivery_policy(self):
        h = self.gen(**{"pass": "final"})
        p = panel.STATE["queue"][0]["params"]
        policy = self.board["policy"]["final"]
        self.assertEqual((p["width"], p["height"]),
                         (policy["width"], policy["height"]))
        board = storyboard.load_storyboard(self.state, "sb_t")
        self.assertEqual(board["shots"][-1]["final_job_id"], h.payload["job_id"])


class PrepareRun(EditorCase):
    """The build loop itself — with ffmpeg mocked, but every decision real."""

    def run_prepare(self, music=None, fail=(), cancel_after=None):
        calls = []

        def fake_ffmpeg(cmd, label):
            calls.append(cmd)
            dest = Path(cmd[-1])
            if dest.name in fail:
                raise RuntimeError("ffmpeg said no")
            dest.write_bytes(b"proxy")
            if cancel_after is not None and len(calls) >= cancel_after:
                panel._SBE_JOBS["sb_t"]["cancel"] = True
            return "", ""

        panel._SBE_JOBS["sb_t"] = {"id": "sb_t", "state": "running",
                                   "cancel": False}
        with mock.patch.object(panel, "run_ffmpeg_tracked", fake_ffmpeg):
            panel._sbe_prepare_inner("sb_t", music, None)
        return calls, panel._sbe_job_state("sb_t")

    def test_it_builds_one_proxy_per_clip_and_reports_progress(self):
        calls, st = self.run_prepare()
        self.assertEqual(len(calls), 3)
        self.assertEqual(st["state"], "done")
        self.assertEqual((st["built"], st["total"], st["done"]), (3, 3, 3))
        self.assertEqual(len(list(sedit.proxy_dir(self.bdir).glob("*.mp4"))), 3)

    def test_a_second_run_rebuilds_NOTHING(self):
        self.run_prepare()
        calls, st = self.run_prepare()
        self.assertEqual(calls, [])
        self.assertEqual((st["built"], st["reused"]), (0, 3))

    def test_one_broken_clip_does_not_cost_the_others_their_timeline(self):
        name = sedit.proxy_name(self.clips[1])
        calls, st = self.run_prepare(fail={name})
        self.assertEqual(st["built"], 2)
        self.assertEqual(len(st["failed"]), 1)
        self.assertEqual(st["state"], "done")
        # ...and no half-written proxy is left claiming to be one
        self.assertFalse((sedit.proxy_dir(self.bdir) / name).exists())

    def test_a_proxy_for_a_shot_that_is_gone_is_pruned(self):
        self.run_prepare()
        board = storyboard.load_storyboard(self.state, "sb_t")
        board["shots"] = board["shots"][:2]
        storyboard.save_storyboard(self.state, board)
        _calls, st = self.run_prepare()
        self.assertEqual(st["pruned"], 1)
        self.assertEqual(len(list(sedit.proxy_dir(self.bdir).glob("*.mp4"))), 2)

    def test_cancel_stops_the_loop_between_clips(self):
        calls, st = self.run_prepare(cancel_after=1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(st["state"], "cancelled")

    def test_the_soundtrack_produces_peaks_and_a_grid(self):
        song = self.root / "song.mp3"
        song.write_bytes(b"mp3")
        fake_beats = {"bpm": 126.0, "period": 0.4762, "phase": 0.1, "meter": 4,
                      "confidence": 0.8, "span": [0, 60], "beats": [0.1, 0.6],
                      "downbeats": [0.1], "diagnostics": {}}
        with mock.patch("storyboard_edit._decode_pcm",
                        return_value=np.zeros(22050, "float32")), \
             mock.patch("storyboard_edit.beat_map", return_value=fake_beats):
            _calls, st = self.run_prepare(music=str(song))
        self.assertEqual(st["peaks"]["count"], 100)
        self.assertEqual(st["beats"]["bpm"], 126.0)
        self.assertTrue(sedit.peaks_path(self.bdir).is_file())
        # cached, so `GET /storyboard/edit` does not re-run the tracker
        cache = json.loads((self.bdir / "edit_prepare.json").read_text())
        self.assertEqual(cache["music"], str(song))
        self.assertEqual(cache["beats"]["bpm"], 126.0)

    def test_a_soundtrack_that_cannot_be_read_does_not_lose_the_proxies(self):
        song = self.root / "song.mp3"
        song.write_bytes(b"mp3")
        with mock.patch("storyboard_edit._decode_pcm",
                        side_effect=RuntimeError("no audio")):
            _calls, st = self.run_prepare(music=str(song))
        self.assertEqual(st["state"], "done")
        self.assertEqual(st["built"], 3)
        # BOTH halves are reported. Folding them into one string meant the
        # tracker's failure silently overwrote the waveform's.
        self.assertIn("waveform failed", st["peaks_error"])
        self.assertIn("beat tracking failed", st["beats_error"])
        self.assertIn("waveform failed", st["music_error"])

    def test_a_thread_that_dies_leaves_a_job_the_client_can_SEE_die(self):
        # A vanished thread leaves "running" on the poll forever, which reads
        # as a hang — the worst of the available failures.
        panel._SBE_JOBS["sb_t"] = {"id": "sb_t", "state": "running",
                                   "cancel": False}
        with mock.patch.object(panel, "_sbe_prepare_inner",
                               side_effect=RuntimeError("kaboom")):
            panel._sbe_prepare_thread("sb_t", None, None)
        st = panel._sbe_job_state("sb_t")
        self.assertEqual(st["state"], "failed")
        self.assertIn("kaboom", st["error"])


# =============================================================================
# 6. A cast shot must be ENQUEUEABLE — found by building `edit/generate`
# =============================================================================
class CharacterQualityContract(unittest.TestCase):
    """`make_job` speaks a different quality vocabulary for character jobs.

    Found while wiring `edit/generate`: a shot with a `character_id` and the
    DEFAULT board policy raised `CharacterRequestError("character quality must
    be draft, pro, high or high720")` — because the pass policy says "quick" /
    "standard" and `resolve_character_quality()` accepts only the four
    character tokens. `_sb_render_thread` catches that and marks the shot
    failed, so every cast shot in every film failed to queue, both passes, with
    that sentence as the only clue.

    `shot_to_job` translates now. These tests pin the translation against the
    panel's own resolver, so the table cannot drift away from the thing it
    is a table OF.
    """

    def job_form(self, quality: str, character=True) -> dict:
        shot = {"n": 1, "mode": "character" if character else "text",
                "engine": "ltx", "prompt": "trig walks through the gate",
                "duration_s": 5.0, "seed": 7, "refs": [], "status": "pending"}
        if character:
            shot["character_id"] = "trig"
            shot["trigger"] = "trig"
        return storyboard.shot_to_job(
            shot, {"quality": quality, "width": 1024, "height": 576,
                   "frames": 121}, h3_available=False)

    def test_every_pass_quality_maps_to_something_make_job_ACCEPTS(self):
        for q in ("quick", "balanced", "standard", "high", "high_720p"):
            token = self.job_form(q)["quality"]
            self.assertIsNotNone(
                panel.resolve_character_quality(token, panel.ACTIVE_MODEL_VERSION),
                f"pass quality {q!r} -> {token!r} is not a character quality")

    def test_the_default_board_policy_produces_a_queueable_cast_shot(self):
        # The exact pair `_sb_policy_for` hands `_sb_render_thread`.
        for pass_name in ("draft", "final"):
            q = storyboard.DEFAULT_POLICY[pass_name]["quality"]
            self.assertIn(self.job_form(q)["quality"],
                          ("draft", "pro", "high", "high720"), pass_name)

    def test_an_unknown_quality_lands_on_the_validated_recipe(self):
        self.assertEqual(self.job_form("something_new")["quality"], "pro")

    def test_a_shot_with_no_character_keeps_the_passes_own_quality(self):
        self.assertEqual(self.job_form("quick", character=False)["quality"],
                         "quick")
        self.assertEqual(self.job_form("standard", character=False)["quality"],
                         "standard")

    def test_the_draft_pass_stays_SMALLER_than_the_delivery_pass(self):
        # The translation must not collapse the two passes into one recipe —
        # the whole point of a draft is that it is cheaper.
        cells = [panel.resolve_character_quality(
            self.job_form(storyboard.DEFAULT_POLICY[p]["quality"])["quality"],
            panel.ACTIVE_MODEL_VERSION) for p in ("draft", "final")]
        self.assertLess(cells[0]["width"] * cells[0]["height"],
                        cells[1]["width"] * cells[1]["height"])


# =============================================================================
# THE MEDIA POOL — one verb, three sources, and a proxy before the clip lands
# =============================================================================
class AddClipRoute(EditorCase):
    """`POST /storyboard/edit/add-clip` — what replaced a window.prompt().

    Until Wave 1 the only way to bring a clip into a cut was a native text box
    asking for the NUMBER of a film, and the generations gallery — every clip
    the panel has ever made — was unreachable from the editor entirely. This
    route is the whole of the answer: a path in, a proxied clip out.
    """

    def setUp(self):
        super().setUp()
        # A clip in the gallery that belongs to no board — the case the pool
        # exists for.
        self.loose = self.out / "a_loose_render.mp4"
        self.loose.write_bytes(b"y" * 2048)

    def add(self, **form):
        built = []

        def fake_ffmpeg(cmd, label):
            built.append(cmd)
            Path(cmd[-1]).write_bytes(b"proxy")
            return "", ""

        with mock.patch.object(panel, "run_ffmpeg_tracked", fake_ffmpeg), \
             mock.patch("storyboard_edit.probe_media",
                        return_value={"duration": 7.5}):
            h = FakeHandler().post("edit/add-clip", form)
        return h, built

    def test_a_gallery_clip_arrives_with_its_proxy_already_built(self):
        # THE POINT OF THE ROUTE. A clip placed without a proxy scrubs at
        # source speed — 235 ms a seek, measured — which reads as the editor
        # being broken rather than as a missing build step.
        h, built = self.add(id="sb_t", path=str(self.loose))
        self.assertEqual(h.status, 200)
        self.assertTrue(h.payload["ok"])
        clip = h.payload["clip"]
        self.assertEqual(clip["path"], str(self.loose))
        self.assertEqual(clip["duration_s"], 7.5)
        self.assertTrue(clip["proxy"].startswith("proxy/"))
        self.assertEqual(len(built), 1)
        self.assertTrue((sedit.proxy_dir(self.bdir) /
                         Path(clip["proxy"]).name).is_file())

    def test_a_proxy_that_already_exists_is_not_built_again(self):
        self.add(id="sb_t", path=str(self.loose))
        h, built = self.add(id="sb_t", path=str(self.loose))
        self.assertTrue(h.payload["ok"])
        self.assertEqual(built, [])
        self.assertEqual(h.payload["proxy"]["reused"], 1)

    def test_it_prunes_nothing(self):
        # The full Prepare re-plans the WHOLE board and deletes every proxy no
        # live clip points at. Scoping matters: adding one clip must not cost
        # the timeline the proxies of clips the board does not know about.
        d = sedit.proxy_dir(self.bdir)
        d.mkdir(parents=True, exist_ok=True)
        keeper = d / "someone-elses-proxy.mp4"
        keeper.write_bytes(b"proxy")
        self.add(id="sb_t", path=str(self.loose))
        self.assertTrue(keeper.is_file())

    def test_a_path_outside_the_panels_own_outputs_is_refused(self):
        # `path` comes off a request. Anything not under OUTPUT or STATE_DIR
        # is refused HERE, rather than at the ffmpeg that would read it.
        outsider = self.root / "not_an_output.mp4"
        outsider.write_bytes(b"z")
        h, built = self.add(id="sb_t", path=str(outsider))
        self.assertEqual(h.status, 400)
        self.assertFalse(h.payload["ok"])
        self.assertEqual(built, [])

    def test_a_path_that_is_not_a_file_is_refused(self):
        h, _ = self.add(id="sb_t", path=str(self.out / "ghost.mp4"))
        self.assertEqual(h.status, 400)

    def test_a_clip_from_another_film_is_imported_then_proxied(self):
        # The `only=` subset param the server has always supported and no
        # screen could reach. The clip is REFERENCED, never copied.
        other = self.out / "other_film_s01.mp4"
        other.write_bytes(b"w" * 900)
        src = _board([other], bid="sb_other", title="B-roll")
        storyboard.save_storyboard(self.state, src)
        h, built = self.add(id="sb_t", **{"from": "sb_other", "only": "1"})
        self.assertTrue(h.payload["ok"])
        self.assertEqual(h.payload["clip"]["path"], str(other))
        self.assertEqual(len(built), 1)
        # …and the board it landed on now carries the shot, with provenance.
        board = storyboard.load_storyboard(self.state, "sb_t")
        got = [s for s in board["shots"] if s.get("draft_output") == str(other)]
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["imported_from"]["board"], "sb_other")
        self.assertEqual(h.payload["clip"]["n"], got[0]["n"])
        # The file itself was not copied anywhere.
        self.assertFalse((self.bdir / other.name).exists())

    def test_importing_from_a_film_that_is_rendering_is_refused_not_raced(self):
        other = self.out / "busy_s01.mp4"
        other.write_bytes(b"w")
        storyboard.save_storyboard(self.state,
                                   _board([other], bid="sb_other"))
        panel._SB_RENDERS["sb_t"] = {"stop": False}
        try:
            h, built = self.add(id="sb_t", **{"from": "sb_other", "only": "1"})
        finally:
            panel._SB_RENDERS.pop("sb_t", None)
        self.assertEqual(h.status, 409)
        self.assertEqual(built, [])

    def test_the_pool_is_on_the_payload_with_what_is_already_on_the_track(self):
        # The pool shows placed clips too — marked — so putting one on twice
        # is a choice and not an accident.
        edit = _edit([_clip(self.clips[0], 0, 4, 0)])
        sedit.save_edit(self.bdir, edit)
        h = FakeHandler().get("/storyboard/edit?id=sb_t")
        pool = h.payload["clips"]
        self.assertEqual(len(pool), 3)
        self.assertEqual([c["placed"] for c in pool], [True, False, False])

    def test_a_timeline_only_clips_proxy_is_reported_not_dropped(self):
        # The map used to be built from BOARD shots alone, so a clip added
        # from the pool got `proxy: null` in the payload while its freshly
        # built proxy sat on disk — and the player fell back to the source.
        self.add(id="sb_t", path=str(self.loose))
        edit = _edit([_clip(self.loose, 0, 4, 0)])
        sedit.save_edit(self.bdir, edit)
        h = FakeHandler().get("/storyboard/edit?id=sb_t")
        clip = h.payload["edit"]["clips"][0]
        self.assertTrue(str(clip["proxy"]).startswith("proxy/"))


# =============================================================================
# RELINK — the drafts a delivery pass left behind
# =============================================================================
class RelinkDraftToDelivery(EditorCase):
    """The live bug: "Finish keepers" made files the film never used.

    `_sbe_board_clips` picks delivery over draft — but `edit.json` freezes the
    path at the moment of the cut and nothing ever rewrote it, so a delivery
    pass produced full-size renders that the timeline went on ignoring, and
    the next Prepare pruned their proxies for good measure.
    """

    def deliver(self, n: int, name: str) -> Path:
        final = self.out / name
        final.write_bytes(b"final" * 100)
        board = storyboard.load_storyboard(self.state, "sb_t")
        for sh in board["shots"]:
            if sh["n"] == n:
                sh["final_output"] = str(final)
        storyboard.save_storyboard(self.state, board)
        return final

    def test_a_draft_on_the_timeline_with_a_delivery_on_disk_is_flagged(self):
        final = self.deliver(1, "S01_final.mp4")
        sedit.save_edit(self.bdir, _edit([_clip(self.clips[0], 0, 4, 0)]))
        h = FakeHandler().get("/storyboard/edit?id=sb_t")
        rows = h.payload["relink"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["path"], str(self.clips[0]))
        self.assertEqual(rows[0]["to"], str(final))
        self.assertEqual(rows[0]["n"], 1)

    def test_nothing_is_flagged_when_the_delivery_file_is_gone(self):
        final = self.deliver(1, "S01_final.mp4")
        final.unlink()
        sedit.save_edit(self.bdir, _edit([_clip(self.clips[0], 0, 4, 0)]))
        h = FakeHandler().get("/storyboard/edit?id=sb_t")
        self.assertEqual(h.payload["relink"], [])

    def test_a_timeline_already_on_the_delivery_is_not_flagged(self):
        final = self.deliver(1, "S01_final.mp4")
        sedit.save_edit(self.bdir, _edit([_clip(final, 0, 4, 0)]))
        h = FakeHandler().get("/storyboard/edit?id=sb_t")
        self.assertEqual(h.payload["relink"], [])

    def test_it_is_never_applied_without_being_asked(self):
        # The arrangement is the human's — the rule `_sbe_payload` already
        # states about `unplaced`, applied to paths.
        self.deliver(1, "S01_final.mp4")
        sedit.save_edit(self.bdir, _edit([_clip(self.clips[0], 0, 4, 0)]))
        FakeHandler().get("/storyboard/edit?id=sb_t")
        on_disk = sedit.load_edit(self.bdir)
        self.assertEqual(on_disk["clips"][0]["path"], str(self.clips[0]))

    def test_the_button_rewrites_the_paths_and_keeps_the_cuts(self):
        final = self.deliver(2, "S02_final.mp4")
        sedit.save_edit(self.bdir, _edit([
            _clip(self.clips[0], 0, 4, 0),
            _clip(self.clips[1], 1.5, 5.5, 4)]))
        built = []

        def fake_ffmpeg(cmd, label):
            built.append(cmd)
            Path(cmd[-1]).write_bytes(b"proxy")
            return "", ""

        with mock.patch.object(panel, "run_ffmpeg_tracked", fake_ffmpeg):
            h = FakeHandler().post("edit/relink", {"id": "sb_t"})
        self.assertTrue(h.payload["ok"])
        self.assertEqual(h.payload["relinked"], 1)
        clips = sedit.load_edit(self.bdir)["clips"]
        self.assertEqual(clips[0]["path"], str(self.clips[0]))     # untouched
        self.assertEqual(clips[1]["path"], str(final))             # relinked
        # SAME CUTS. Only the files change — the window and the slot on the
        # film are exactly what the human left there.
        self.assertAlmostEqual(clips[1]["start"], 1.5)
        self.assertAlmostEqual(clips[1]["end"], 5.5)
        self.assertAlmostEqual(clips[1]["film_start"], 4.0)
        self.assertAlmostEqual(clips[1]["film_end"], 8.0)
        # …and the delivery's proxy is built before the answer goes out, so
        # the timeline that comes back is scrubbable.
        self.assertEqual(len(built), 1)
        self.assertEqual(Path(built[0][-1]).name, sedit.proxy_name(final))
        self.assertEqual(h.payload["relink"], [])

    def test_relinking_nothing_is_not_an_error(self):
        sedit.save_edit(self.bdir, _edit([_clip(self.clips[0], 0, 4, 0)]))
        h = FakeHandler().post("edit/relink", {"id": "sb_t"})
        self.assertTrue(h.payload["ok"])
        self.assertEqual(h.payload["relink"], [])

    def test_relinking_without_a_timeline_says_so(self):
        h = FakeHandler().post("edit/relink", {"id": "sb_t"})
        self.assertEqual(h.status, 404)


class UnknownActions(EditorCase):
    def test_an_unknown_editor_action_is_a_404_not_a_500(self):
        h = FakeHandler().post("edit/nonsense", {"id": "sb_t"})
        self.assertEqual(h.status, 404)

    def test_a_missing_editor_module_is_a_503_not_a_dead_panel(self):
        with mock.patch.object(panel, "_sbe_import",
                               side_effect=ImportError("no module")):
            self.assertEqual(FakeHandler().post("edit/save", {}, "{}").status,
                             503)
            self.assertEqual(FakeHandler().get("/storyboard/edit?id=sb_t")
                             .status, 503)


# =============================================================================
# WAVE 2 — the three kinds, the version bump, and the project folder
# =============================================================================
class KindsAndAdjustments(unittest.TestCase):
    """`clip.kind` and `clip.adjust`, and what the validator will not take."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.img = self.root / "card.png"
        self.img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 40)

    def tearDown(self):
        self.tmp.cleanup()

    def still(self, **kw):
        return dict({"id": "s", "kind": "still", "path": str(self.img),
                     "start": 0, "end": 0, "film_start": 0.0,
                     "film_end": 3.0, "source": "human", "locked": False}, **kw)

    def slug(self, **kw):
        return dict({"id": "k", "kind": "slug", "path": None,
                     "start": 0, "end": 0, "film_start": 0.0,
                     "film_end": 2.0, "source": "human", "locked": False}, **kw)

    def codes(self, clips):
        return [e["code"] for e in sedit.validate_edit(_edit(clips))]

    # ---- kind ------------------------------------------------------------
    def test_an_absent_kind_is_a_video_which_is_the_whole_migration(self):
        self.assertEqual(sedit.clip_kind({}), "video")
        self.assertEqual(sedit.clip_kind({"kind": "still"}), "still")
        self.assertEqual(sedit.clip_kind({"kind": "SLUG"}), "slug")
        self.assertEqual(sedit.clip_kind({"kind": "banana"}), "video")
        self.assertEqual(sedit.clip_kind(None), "video")

    def test_a_kind_the_build_does_not_know_is_refused_by_name(self):
        self.assertEqual(self.codes([self.still(kind="dissolve")]),
                         ["clip_kind"])

    def test_a_still_needs_an_image_that_is_actually_there(self):
        self.assertEqual(self.codes([self.still()]), [])
        gone = self.still(path=str(self.root / "nope.png"))
        self.assertEqual(self.codes([gone]), ["still_missing"])
        self.assertEqual(self.codes([self.still(path="")]), ["clip_path"])

    def test_a_still_needs_a_duration_and_that_is_its_slot(self):
        self.assertEqual(self.codes([self.still(film_end=0.0)]),
                         ["clip_film_window"])

    def test_a_slug_needs_only_a_duration_and_never_a_path(self):
        self.assertEqual(self.codes([self.slug()]), [])
        self.assertEqual(self.codes([self.slug(film_end=0.0)]),
                         ["clip_film_window"])

    def test_the_1x_rule_does_not_apply_to_a_synthesised_window(self):
        # A video whose window and slot disagree is refused, and must stay
        # refused. A still and a slug have their window SYNTHESISED from the
        # slot by normalise_edit, so policing it would refuse the document for
        # a number the server itself wrote.
        bad_video = _clip("/x/a.mp4", 0.0, 2.0, 0.0)
        bad_video["film_end"] = 5.0
        self.assertIn("clip_length_mismatch", self.codes([bad_video]))
        self.assertEqual(self.codes([self.still(end=0, film_end=9.0)]), [])
        self.assertEqual(self.codes([self.slug(end=0, film_end=9.0)]), [])
        # …and a still is never "past the end" of a source it does not have.
        self.assertEqual(self.codes([self.still(duration=0.5, film_end=9.0)]),
                         [])

    def test_normalise_synthesises_the_window_and_clears_the_source_clock(self):
        doc = sedit.normalise_edit(_edit([self.still(film_start=2.0,
                                                     film_end=5.5,
                                                     duration=99),
                                          self.slug(film_start=5.5,
                                                    film_end=7.0)]))
        still, slug = doc["clips"]
        self.assertEqual((still["start"], still["end"]), (0.0, 3.5))
        self.assertIsNone(still["duration"])
        self.assertEqual((slug["start"], slug["end"]), (0.0, 1.5))
        self.assertIsNone(slug["path"])
        self.assertIsNone(slug["proxy"])

    def test_a_video_clip_never_grows_a_kind_field_on_disk(self):
        # Stamping "video" on every clip would rewrite every edit.json on the
        # machine to say the thing its absence already says.
        doc = sedit.normalise_edit(_edit([_clip("/x/a.mp4", 0.0, 2.0, 0.0)]))
        self.assertNotIn("kind", doc["clips"][0])

    # ---- adjust ----------------------------------------------------------
    def test_brightness_is_refused_outside_the_clamp(self):
        c = _clip("/x/a.mp4", 0.0, 2.0, 0.0)
        self.assertEqual(self.codes([dict(c, adjust={"brightness": 0.5})]), [])
        self.assertEqual(self.codes([dict(c, adjust={"brightness": -0.5})]), [])
        self.assertEqual(self.codes([dict(c, adjust={"brightness": 0.51})]),
                         ["clip_brightness_range"])
        self.assertEqual(self.codes([dict(c, adjust={"brightness": -9})]),
                         ["clip_brightness_range"])
        self.assertEqual(self.codes([dict(c, adjust={"brightness": "dark"})]),
                         ["clip_brightness"])
        self.assertEqual(self.codes([dict(c, adjust={"brightness": True})]),
                         ["clip_brightness"])
        self.assertEqual(self.codes([dict(c, adjust="dark")]), ["clip_adjust"])

    def test_a_neutral_grade_is_dropped_rather_than_written(self):
        # An untouched edit.json and one whose slider went out and came back
        # must be the same document.
        doc = sedit.normalise_edit(_edit([
            dict(_clip("/x/a.mp4", 0.0, 2.0, 0.0), adjust={"brightness": 0.0}),
            dict(_clip("/x/b.mp4", 0.0, 2.0, 2.0), adjust={"brightness": 0.2}),
        ]))
        self.assertNotIn("adjust", doc["clips"][0])
        self.assertEqual(doc["clips"][1]["adjust"], {"brightness": 0.2})

    def test_the_cut_list_carries_the_kinds_and_nothing_it_need_not(self):
        doc = sedit.normalise_edit(_edit([
            dict(_clip("/x/a.mp4", 1.0, 3.0, 0.0), adjust={"brightness": 0.2}),
            self.still(film_start=2.0, film_end=5.0),
            self.slug(film_start=5.0, film_end=6.0),
        ]))
        cuts = sedit.edit_to_cuts(doc)
        self.assertEqual([c.get("kind") for c in cuts],
                         [None, "still", "slug"])
        self.assertEqual(cuts[0]["adjust"], {"brightness": 0.2})
        self.assertNotIn("adjust", cuts[1])
        self.assertIsNone(cuts[2]["path"])
        # A slug is emitted, not skipped — the old edit_to_cuts dropped every
        # pathless entry, which is exactly what black looks like.
        self.assertEqual((cuts[2]["start"], cuts[2]["end"]), (0.0, 1.0))

    def test_a_still_and_a_slug_are_never_proxy_work(self):
        # A slug has no file; a still is one frame a browser paints from an
        # <img>. Planning a proxy for either builds an mp4 nothing will play
        # and puts a name in `wanted` that the prune then has to keep.
        plan = sedit.plan_proxies([
            {"path": "/x/a.mp4"},
            {"path": str(self.img), "kind": "still"},
            {"path": None, "kind": "slug"},
        ], self.root)
        self.assertEqual([Path(r["path"]).name
                          for r in plan["build"] + plan["reuse"]], ["a.mp4"])


class TheVersionBumpAndItsMigration(unittest.TestCase):
    """EDIT_VERSION 2 refuses old builds — WITHOUT refusing old documents."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bdir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def v1(self) -> dict:
        return {"version": 1, "board_id": "sb_t", "revision": 3,
                "source": "human", "audio": None, "beats": None, "settings": {},
                "clips": [{"id": "a", "path": "/x/a.mp4", "proxy": None,
                           "start": 0.0, "end": 2.0, "film_start": 0.0,
                           "film_end": 2.0, "source": "auto",
                           "locked": False}]}

    def test_a_version_1_document_on_disk_loads_clean(self):
        # THE TRAP THE AUDIT NAMED. validate_edit hard-refuses any version but
        # the current one and save_edit refuses to write anything it
        # complained about — so a bump with no read-path upgrade would not
        # have refused old BUILDS, it would have refused every timeline
        # anybody already had, on their own machine.
        sedit.edit_path(self.bdir).write_text(json.dumps(self.v1()))
        doc = sedit.load_edit(self.bdir)
        self.assertEqual(doc["version"], sedit.EDIT_VERSION)
        self.assertEqual(doc["migrated_from"], 1)
        self.assertEqual(sedit.validate_edit(doc), [])
        # The clips are untouched: absent already MEANS video, so there is
        # nothing to rewrite and nothing that can half-fail.
        self.assertNotIn("kind", doc["clips"][0])
        self.assertEqual(doc["revision"], 3)

    def test_a_migrated_document_saves_and_reloads(self):
        sedit.edit_path(self.bdir).write_text(json.dumps(self.v1()))
        sedit.save_edit(self.bdir, sedit.load_edit(self.bdir))
        back = sedit.load_edit(self.bdir)
        self.assertEqual(back["version"], 2)
        self.assertEqual(back["revision"], 4)

    def test_a_document_from_the_future_is_refused_not_pretended_about(self):
        future = dict(self.v1(), version=99)
        self.assertEqual(sedit.migrate_edit(future)["version"], 99)
        self.assertEqual([e["code"] for e in sedit.validate_edit(future)],
                         ["version"])

    def test_the_bump_is_what_stops_an_old_build_rendering_a_new_film(self):
        # An older Phosphene reading a slugged timeline must stop loudly. The
        # alternative is edit_to_cuts skipping every pathless clip and the
        # whole film sliding off the beats it was cut to, silently.
        self.assertEqual(sedit.EDIT_VERSION, 2)
        self.assertEqual([e["code"] for e in
                          sedit.validate_edit(dict(self.v1(), version=1))],
                         ["version"])


class NleProjectExport(unittest.TestCase):
    """`<film>_project/` — the film as a project somebody else's editor opens."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.mp4 = self.root / "a shot's take.mp4"
        self.mp4.write_bytes(b"v" * 64)
        self.png = self.root / "card.png"
        self.png.write_bytes(b"i" * 64)
        self.mp3 = self.root / "the track.mp3"
        self.mp3.write_bytes(b"m" * 64)

    def tearDown(self):
        self.tmp.cleanup()

    def probe(self, p):
        return {"w": 1024, "h": 576, "duration": 10.0,
                "has_audio": str(p).endswith(".mp4")}

    def clips(self):
        return [
            {"id": "1", "path": str(self.mp4), "title": "Open",
             "start": 1.0, "end": 3.0, "film_start": 0.0, "film_end": 2.0,
             "adjust": {"brightness": 0.25}},
            {"id": "2", "kind": "still", "path": str(self.png), "title": "Card",
             "start": 0.0, "end": 1.5, "film_start": 2.0, "film_end": 3.5},
            {"id": "3", "kind": "slug", "path": None,
             "start": 0.0, "end": 1.0, "film_start": 3.5, "film_end": 4.5},
            {"id": "4", "path": str(self.mp4), "title": "Close",
             "start": 4.0, "end": 6.0, "film_start": 4.5, "film_end": 6.5},
        ]

    _NOTHING = object()

    def export(self, **kw):
        clips = kw.pop("clips", self._NOTHING)
        return sedit.export_nle(
            self.clips() if clips is self._NOTHING else clips, self.root,
            name=kw.pop("name", "the long walk_film"), probe=self.probe, **kw)

    def seq(self, res):
        return ET.parse(res["xml"]).getroot().find("sequence")

    # ---- the folder ------------------------------------------------------
    def test_the_folder_holds_the_xml_the_script_and_the_media(self):
        res = self.export()
        d = Path(res["dir"])
        self.assertTrue(d.is_dir())
        self.assertEqual(d.name, "the-long-walk-film_project")
        self.assertTrue(Path(res["xml"]).is_file())
        self.assertTrue(Path(res["jsx"]).is_file())
        self.assertEqual(sorted(p.name for p in (d / "media").iterdir()),
                         ["a shot's take.mp4", "card.png"])

    def test_the_media_is_hardlinked_so_a_30_shot_film_costs_nothing(self):
        res = self.export()
        self.assertEqual((res["linked"], res["copied"]), (2, 0))
        linked = Path(res["dir"]) / "media" / "a shot's take.mp4"
        self.assertEqual(linked.stat().st_ino, self.mp4.stat().st_ino)
        # The same source used twice is ONE file in media/.
        self.assertEqual(len(res["media"]), 2)

    def test_a_cross_device_export_copies_rather_than_refusing(self):
        # os.link raises EXDEV across filesystems, and an export that refuses
        # to happen is worse than an export that costs disk.
        def no_link(_a, _b):
            raise OSError(18, "Cross-device link")

        res = self.export(link=no_link)
        self.assertEqual((res["linked"], res["copied"]), (0, 2))
        copy = Path(res["dir"]) / "media" / "card.png"
        self.assertTrue(copy.is_file())
        self.assertNotEqual(copy.stat().st_ino, self.png.stat().st_ino)
        self.assertEqual(copy.read_bytes(), self.png.read_bytes())

    def test_a_soundtrack_rides_along_into_media(self):
        res = self.export(audio={"path": str(self.mp3), "offset": 2.0,
                                 "duration": 60.0})
        self.assertIn("the track.mp3", res["media"])
        self.assertTrue((Path(res["dir"]) / "media" / "the track.mp3").is_file())

    def test_a_file_that_has_gone_is_left_out_and_said_so(self):
        clips = self.clips()
        clips[0]["path"] = str(self.root / "vanished.mp4")
        res = self.export(clips=clips)
        self.assertEqual([Path(m).name for m in res["missing"]],
                         ["vanished.mp4"])
        names = [c.findtext("name")
                 for c in self.seq(res).iter("clipitem")]
        self.assertNotIn("Open", names)

    # ---- the XML ---------------------------------------------------------
    def test_it_is_the_one_xml_both_premiere_and_resolve_import(self):
        root = ET.parse(self.export()["xml"]).getroot()
        self.assertEqual(root.tag, "xmeml")
        self.assertEqual(root.get("version"), "4")

    def test_the_sequence_runs_at_24_and_is_not_ntsc(self):
        # ntsc TRUE reads timebase 24 as 23.976 and every cut past the first
        # drifts a frame per thousand.
        seq = self.seq(self.export())
        rate = seq.find("rate")
        self.assertEqual(rate.findtext("timebase"), "24")
        self.assertEqual(rate.findtext("ntsc"), "FALSE")
        self.assertEqual(seq.findtext("duration"), "156")   # 6.5 s at 24 fps

    def test_one_video_track_because_the_validator_allows_no_other(self):
        seq = self.seq(self.export())
        self.assertEqual(len(seq.findall("media/video/track")), 1)

    def test_a_slug_is_a_gap_not_a_generator(self):
        # Generator effect ids differ between the two NLEs, so "one XML for
        # both" would have quietly become "one XML for one of them". A gap
        # reads as black in every editor ever made.
        items = self.seq(self.export()).findall("media/video/track/clipitem")
        self.assertEqual([i.findtext("name") for i in items],
                         ["Open", "Card", "Close"])
        # 3.5 s to 4.5 s is simply nobody's slot.
        self.assertEqual([(i.findtext("start"), i.findtext("end"))
                          for i in items],
                         [("0", "48"), ("48", "84"), ("108", "156")])
        self.assertNotIn("generatoritem", ET.tostring(self.seq(self.export()),
                                                      encoding="unicode"))

    def test_in_and_out_are_the_source_window_start_and_end_are_the_slot(self):
        # Two different clocks. Conflating them is the classic FCP7 XML bug:
        # the film plays, in the wrong order, at the wrong lengths.
        items = self.seq(self.export()).findall("media/video/track/clipitem")
        self.assertEqual((items[0].findtext("in"), items[0].findtext("out")),
                         ("24", "72"))          # 1.0-3.0 s of the source
        self.assertEqual((items[0].findtext("start"), items[0].findtext("end")),
                         ("0", "48"))           # 0.0-2.0 s of the film
        self.assertEqual((items[2].findtext("in"), items[2].findtext("out")),
                         ("96", "144"))         # 4.0-6.0 s of the same source

    def test_a_still_is_held_for_its_slot(self):
        card = self.seq(self.export()).findall(
            "media/video/track/clipitem")[1]
        self.assertEqual((card.findtext("in"), card.findtext("out")),
                         ("0", "36"))           # 1.5 s of hold
        self.assertEqual((card.findtext("start"), card.findtext("end")),
                         ("48", "84"))

    def test_a_file_is_described_once_and_referenced_by_id_after(self):
        # Re-describing it makes Premiere import the same clip several times
        # as several master items.
        seq = self.seq(self.export())
        full = [f for f in seq.iter("file") if f.find("pathurl") is not None]
        self.assertEqual(len(full), 2)          # the mp4 and the png, once each
        self.assertEqual(sorted(f.get("id") for f in full),
                         ["file-1", "file-2"])
        # THE ID IS THE SOURCE'S, NOT THE SEGMENT'S. The same mp4 in two slots
        # is one master item pointed at four times — twice on the video track,
        # twice on A1 — not two imports of the same file.
        ids = [f.get("id") for f in seq.iter("file")]
        self.assertEqual(ids.count("file-1"), 4)
        self.assertEqual(sorted(set(ids)), ["file-1", "file-2"])

    def test_the_pathurls_are_absolute_into_the_projects_own_media(self):
        # Absolute-into-media/ is the pair that works: it opens instantly here,
        # and when the folder is handed over both importers fall back to
        # matching by NAME inside the project's own directory.
        res = self.export()
        urls = [f.findtext("pathurl") for f in self.seq(res).iter("file")
                if f.findtext("pathurl")]
        for u in urls:
            self.assertTrue(u.startswith("file://localhost/"), u)
            self.assertIn("/the-long-walk-film_project/media/", u)
        # Spaces and quotes are percent-encoded — an un-encoded space makes
        # the pathurl unparseable and the clip imports offline, silently.
        self.assertTrue(any("a%20shot%27s%20take.mp4" in u for u in urls))
        self.assertFalse(any(" " in u for u in urls))

    def test_the_audio_comes_out_as_stems_not_the_ducked_mix(self):
        # The render's sidechained under-mix has no representation in an NLE
        # timeline, so baking it in would hand an editor a bed they cannot
        # unmix. A1 is the clips' own sound; A2 is the soundtrack.
        seq = self.seq(self.export(audio={"path": str(self.mp3),
                                          "offset": 2.0, "duration": 60.0}))
        tracks = seq.findall("media/audio/track")
        self.assertEqual(len(tracks), 2)
        self.assertEqual([i.findtext("name") for i in tracks[0]],
                         ["Open", "Close"])     # the still is silent
        self.assertEqual(len(tracks[1].findall("clipitem")), 1)
        self.assertEqual(tracks[1].find("clipitem").findtext("name"),
                         "the track.mp3")
        # The offset is where in the TRACK the film begins.
        self.assertEqual(tracks[1].find("clipitem").findtext("in"), "48")

    def test_a_title_with_an_ampersand_does_not_break_the_document(self):
        clips = self.clips()
        clips[0]["title"] = 'Salt & <Pepper> "1"'
        res = self.export(clips=clips)
        names = [i.findtext("name")
                 for i in self.seq(res).findall("media/video/track/clipitem")]
        self.assertEqual(names[0], 'Salt & <Pepper> "1"')

    # ---- the After Effects script ---------------------------------------
    def test_the_script_finds_its_own_folder_so_it_survives_a_move(self):
        jsx = Path(self.export()["jsx"]).read_text()
        self.assertIn("File($.fileName).parent", jsx)
        self.assertIn("here.fsName +", jsx)
        self.assertIn("proj.items.addComp", jsx)

    def test_a_slug_becomes_a_real_black_solid_in_after_effects(self):
        # AE has no ambiguity about a colour source, so it gets the real
        # thing rather than the gap the XML uses.
        jsx = Path(self.export()["jsx"]).read_text()
        self.assertIn("comp.layers.addSolid([0, 0, 0]", jsx)
        self.assertIn("lay.inPoint = 3.500000;", jsx)
        self.assertIn("lay.outPoint = 4.500000;", jsx)

    def test_brightness_maps_to_the_ae_effect_and_says_it_is_approximate(self):
        jsx = Path(self.export()["jsx"]).read_text()
        self.assertIn("ADBE Brightness & Contrast 2", jsx)
        self.assertIn("bright(lay, 37.5);", jsx)     # 0.25 of +/-0.5 -> +/-75
        self.assertIn("APPROXIMATE", jsx)
        # Only the graded clip gets one.
        self.assertEqual(jsx.count("bright(lay,"), 1)

    def test_a_trimmed_clip_starts_before_its_own_in_point(self):
        # startTime is where frame 0 of the SOURCE would sit. Setting it after
        # in/out makes AE clamp the trim to the wrong window.
        jsx = Path(self.export()["jsx"]).read_text()
        self.assertIn("lay.startTime = -1.000000;", jsx)   # 0.0 film - 1.0 src
        self.assertLess(jsx.index("lay.startTime = -1.000000;"),
                        jsx.index("lay.inPoint = 0.000000;"))

    def test_a_path_with_a_quote_and_a_space_stays_one_string_literal(self):
        # A backslash escapes the quote after it, a quote ends the literal,
        # and a newline in a filename (legal on macOS) makes the whole script
        # a syntax error.
        self.assertEqual(sedit._jsx_string('a "b" c'), '"a \\"b\\" c"')
        self.assertEqual(sedit._jsx_string("back\\slash"),
                         '"back\\\\slash"')
        self.assertEqual(sedit._jsx_string("two\nlines"), '"two\\nlines"')
        jsx = Path(self.export()["jsx"]).read_text()
        self.assertIn('bring("/media/a shot\'s take.mp4")', jsx)

    def test_an_empty_timeline_is_refused_rather_than_written_empty(self):
        with self.assertRaises(sedit.EditError):
            self.export(clips=[])


class NleExportRoute(EditorCase):
    """`POST /storyboard/edit/export-nle`, and the reveal that follows it."""

    def setUp(self):
        super().setUp()
        sedit.save_edit(self.bdir, _edit([
            _clip(str(self.clips[0]), 0.0, 2.0, 0.0),
            {"id": "k", "kind": "slug", "path": None, "start": 0, "end": 0,
             "film_start": 2.0, "film_end": 3.0, "source": "human",
             "locked": False},
        ]))

    def test_the_route_writes_the_folder_next_to_the_film(self):
        with mock.patch.object(panel, "_sb_probe_clip",
                               return_value={"w": 1024, "h": 576,
                                             "duration": 9.0,
                                             "has_audio": True,
                                             "sample_rate": 48000}):
            h = FakeHandler().post("edit/export-nle", {"id": "sb_t"})
        self.assertEqual(h.status, 200)
        self.assertTrue(h.payload["ok"])
        d = Path(h.payload["dir"])
        self.assertTrue(d.is_dir())
        self.assertEqual(d.parent, panel._sb_film_dir(self.board))
        self.assertEqual(h.payload["clips"], 2)      # the slug counts
        self.assertTrue(Path(h.payload["xml"]).is_file())
        self.assertTrue(Path(h.payload["jsx"]).is_file())

    def test_a_still_is_probed_by_the_probe_that_accepts_an_image(self):
        # `_sb_probe_clip` refuses anything with no duration — correct for a
        # shot, and exactly what an image is.
        img = self.out / "card.png"
        img.write_bytes(b"i" * 32)
        sedit.save_edit(self.bdir, _edit([
            {"id": "s", "kind": "still", "path": str(img), "start": 0,
             "end": 0, "film_start": 0.0, "film_end": 3.0, "source": "human",
             "locked": False}]))
        with mock.patch.object(panel, "_sb_probe_clip", return_value=None),              mock.patch.object(panel, "_sb_probe_still",
                               return_value={"w": 1920, "h": 1080,
                                             "duration": 0.0,
                                             "has_audio": False,
                                             "sample_rate": 0}) as ps:
            h = FakeHandler().post("edit/export-nle", {"id": "sb_t"})
        self.assertTrue(h.payload["ok"])
        ps.assert_called()
        self.assertEqual((h.payload["width"], h.payload["height"]),
                         (1920, 1080))

    def test_a_board_with_no_timeline_is_a_404_not_an_empty_folder(self):
        sedit.edit_path(self.bdir).unlink()
        h = FakeHandler().post("edit/export-nle", {"id": "sb_t"})
        self.assertEqual(h.status, 404)
        self.assertFalse(h.payload["ok"])

    def test_reveal_computes_its_own_path_from_the_board_id_alone(self):
        # The client sends nothing but the board id, so this can never become
        # an argument to `open` pointing outside the board's folder.
        with mock.patch.object(panel, "_sb_probe_clip",
                               return_value={"w": 1024, "h": 576,
                                             "duration": 9.0,
                                             "has_audio": True,
                                             "sample_rate": 48000}):
            FakeHandler().post("edit/export-nle", {"id": "sb_t"})
        with mock.patch.object(panel.subprocess, "Popen") as popen:
            h = FakeHandler().post("edit/reveal",
                                   {"id": "sb_t", "what": "project"})
        self.assertTrue(h.payload["ok"])
        cmd = popen.call_args[0][0]
        self.assertEqual(cmd[0], "open")
        self.assertTrue(cmd[1].endswith("_project"))
        self.assertTrue(Path(cmd[1]).is_dir())




class EverySaveKeepsItsPredecessor(unittest.TestCase):
    """No arrangement is ever only-overwritten again.

    The owner, after a crash-heavy day: "There was a version I was working on
    that was better than this one. Is it lost?" It was. The editor held one
    save per film and the undo stack dies with the tab, so editing past a good
    arrangement destroyed it silently.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)

    def _edit(self, rev=0):
        return {"version": sedit.EDIT_VERSION, "board_id": "b", "revision": rev,
                "source": "manual", "audio": None, "beats": None, "settings": {},
                "clips": [{"id": "a", "path": "/x/a.mp4", "proxy": None,
                           "start": 0.0, "end": 2.0, "film_start": 0.0,
                           "film_end": 2.0, "source": "human", "locked": False}]}

    def test_the_outgoing_save_lands_in_history(self):
        sedit.save_edit(self.d, self._edit())          # rev 1 on disk
        sedit.save_edit(self.d, sedit.load_edit(self.d))   # rev 2; rev 1 -> history
        # A save the user pressed lands in the MANUAL lane, which the prune
        # never walks — "so the user can go back and see the manual saves".
        hist = sorted(sedit.history_dir(self.d).glob("save-r*.json"))
        self.assertEqual(len(hist), 1)
        old = json.loads(hist[0].read_text())
        self.assertEqual(old["revision"], 1)
        self.assertEqual(old["origin"], "manual")
        self.assertEqual(list(sedit.history_dir(self.d).glob("edit-r*.json")), [])

    def test_the_automatic_lane_is_capped(self):
        e = self._edit()
        sedit.save_edit(self.d, e, origin="auto")
        for _ in range(sedit.EDIT_HISTORY_KEEP + 10):
            sedit.save_edit(self.d, sedit.load_edit(self.d), origin="auto")
        hist = list(sedit.history_dir(self.d).glob("edit-r*.json"))
        self.assertLessEqual(len(hist), sedit.EDIT_HISTORY_KEEP)

    def test_the_users_own_saves_are_never_pruned(self):
        # The lane the prune must not be able to reach: fifty debounced
        # snapshots would otherwise delete an afternoon of decisions.
        sedit.save_edit(self.d, self._edit())
        for _ in range(sedit.EDIT_HISTORY_KEEP + 20):
            sedit.save_edit(self.d, sedit.load_edit(self.d))
        mine = list(sedit.history_dir(self.d).glob("save-r*.json"))
        self.assertGreater(len(mine), sedit.EDIT_HISTORY_KEEP)
        rows = sedit.list_history(self.d)
        self.assertTrue(all(r["manual"] for r in rows))
        self.assertTrue(all(r["origin"] == "manual" for r in rows))

    def test_a_failing_history_write_never_blocks_the_save(self):
        sedit.save_edit(self.d, self._edit())
        # a FILE named history blocks mkdir — the save must still land
        (self.d / "history").write_text("not a directory") if not (self.d / "history").exists() else None
        import shutil
        shutil.rmtree(self.d / "history", ignore_errors=True)
        (self.d / "history").write_text("not a directory")
        out = sedit.save_edit(self.d, sedit.load_edit(self.d))
        self.assertTrue(out.is_file())
        self.assertEqual(sedit.load_edit(self.d)["revision"], 2)


class TheSoundtrackIsAnObject(unittest.TestCase):
    """Wave 4 — `offset` + `trim_start` + `trim_end`, and the one function
    that turns them into what ffmpeg is told.

    The owner, cutting: the music must drag "back and forth however you want"
    and trim at both ends, "features similar to what you did with the clips".
    Everything here is the arithmetic behind that, and the promise that a
    timeline written before today still means exactly what it meant.
    """

    def test_an_untouched_track_asks_for_nothing(self):
        w = sedit.music_window({"path": "/x/s.wav", "offset": 0,
                                "duration": 60})
        self.assertEqual([w["start"], w["end"], w["delay"]], [0.0, None, 0.0])

    def test_a_positive_offset_is_still_a_head_trim(self):
        # Its meaning predates the editor and every edit.json on disk relies
        # on it: the TRACK second that plays at film zero.
        w = sedit.music_window({"offset": 5, "duration": 60})
        self.assertEqual([w["start"], w["delay"]], [5.0, 0.0])

    def test_a_negative_offset_delays_the_music_into_the_film(self):
        w = sedit.music_window({"offset": -4, "duration": 60})
        self.assertEqual([w["start"], w["delay"]], [0.0, 4.0])

    def test_a_head_trim_becomes_silence_in_front_not_a_ripple(self):
        # Trimming the music's left edge must not slide the rest of the track
        # earlier — music does not ripple.
        w = sedit.music_window({"offset": 0, "trim_start": 8, "duration": 60})
        self.assertEqual([w["start"], w["delay"]], [8.0, 8.0])

    def test_a_tail_trim_is_a_track_second(self):
        w = sedit.music_window({"offset": 0, "trim_start": 8, "trim_end": 20,
                                "duration": 60})
        self.assertEqual([w["start"], w["end"], w["delay"]], [8.0, 20.0, 8.0])

    def test_a_trim_past_the_end_of_the_track_is_no_trim(self):
        w = sedit.music_window({"offset": 0, "trim_end": 99, "duration": 60})
        self.assertIsNone(w["end"])

    def test_a_window_the_trims_closed_is_no_window(self):
        # Not a zero-length atrim, which ffmpeg refuses outright.
        w = sedit.music_window({"offset": 30, "trim_end": 20, "duration": 60})
        self.assertIsNone(w["end"])

    def test_the_offset_and_a_head_trim_do_not_add_up(self):
        # They are the same gesture from two directions; whichever cuts more
        # wins, and neither is applied twice.
        w = sedit.music_window({"offset": 5, "trim_start": 9, "duration": 60})
        self.assertEqual([w["start"], w["delay"]], [9.0, 4.0])
        w = sedit.music_window({"offset": 9, "trim_start": 5, "duration": 60})
        self.assertEqual([w["start"], w["delay"]], [9.0, 0.0])

    # ---- what the validator will and will not take -----------------------
    def test_the_trims_are_optional_so_every_old_document_is_valid(self):
        e = _edit([_clip("/x/a.mp4", 0.0, 3.0, 0.0)],
                  audio={"path": "/x/s.wav", "offset": 0, "mode": "under"})
        self.assertEqual(sedit.validate_edit(e), [])

    def test_a_negative_offset_is_legal_and_a_negative_trim_is_not(self):
        ok = _edit([_clip("/x/a.mp4", 0.0, 3.0, 0.0)],
                   audio={"path": "/x/s.wav", "offset": -4})
        self.assertEqual(sedit.validate_edit(ok), [])
        bad = _edit([_clip("/x/a.mp4", 0.0, 3.0, 0.0)],
                    audio={"path": "/x/s.wav", "offset": 0, "trim_start": -1})
        self.assertIn("audio_trim_start_range",
                      [e["code"] for e in sedit.validate_edit(bad)])

    def test_a_trim_window_that_closes_is_refused_on_the_way_in(self):
        bad = _edit([_clip("/x/a.mp4", 0.0, 3.0, 0.0)],
                    audio={"path": "/x/s.wav", "trim_start": 9,
                           "trim_end": 4})
        self.assertIn("audio_trim_window",
                      [e["code"] for e in sedit.validate_edit(bad)])

    def test_a_non_numeric_offset_is_refused(self):
        bad = _edit([_clip("/x/a.mp4", 0.0, 3.0, 0.0)],
                    audio={"path": "/x/s.wav", "offset": "later"})
        self.assertIn("audio_offset",
                      [e["code"] for e in sedit.validate_edit(bad)])

    # ---- neutral is absent, the same rule `adjust` follows ---------------
    def test_dragging_a_trim_back_to_the_edge_leaves_no_trace(self):
        doc = sedit.normalise_edit(_edit(
            [_clip("/x/a.mp4", 0.0, 3.0, 0.0)],
            audio={"path": "/x/s.wav", "offset": 0, "duration": 60,
                   "trim_start": 0, "trim_end": 60}))
        self.assertNotIn("trim_start", doc["audio"])
        self.assertNotIn("trim_end", doc["audio"])

    def test_a_real_trim_survives_the_round_trip(self):
        doc = sedit.normalise_edit(_edit(
            [_clip("/x/a.mp4", 0.0, 3.0, 0.0)],
            audio={"path": "/x/s.wav", "offset": -2.5, "duration": 60,
                   "trim_start": 4, "trim_end": 30}))
        self.assertEqual(doc["audio"]["offset"], -2.5)
        self.assertEqual(doc["audio"]["trim_start"], 4.0)
        self.assertEqual(doc["audio"]["trim_end"], 30.0)

    def test_no_soundtrack_still_normalises(self):
        doc = sedit.normalise_edit(_edit([_clip("/x/a.mp4", 0.0, 3.0, 0.0)]))
        self.assertIsNone(doc["audio"])

    def test_the_version_does_not_move_for_an_optional_field(self):
        # A bump would refuse every timeline anybody already has. Absent means
        # untrimmed, which is what absence already meant.
        self.assertEqual(sedit.EDIT_VERSION, 2)


class HistoryAndVersions(unittest.TestCase):
    """Wave 4 — a named version the prune cannot take, and a way back to it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)

    def _doc(self, rev=0, n=1):
        return {"version": sedit.EDIT_VERSION, "board_id": "b", "revision": rev,
                "source": "manual", "audio": None, "beats": None,
                "settings": {},
                "clips": [{"id": f"c{i}", "path": "/x/a.mp4", "proxy": None,
                           "start": 0.0, "end": 2.0, "film_start": 2.0 * i,
                           "film_end": 2.0 * (i + 1), "source": "human",
                           "locked": False} for i in range(n)]}

    # ---- naming one -----------------------------------------------------
    def test_a_named_version_lands_under_its_own_prefix(self):
        sedit.save_edit(self.d, self._doc())
        dst = sedit.archive_edit(self.d, sedit.load_edit(self.d), "the good one")
        self.assertTrue(dst.name.startswith("keep-r"))
        self.assertIn("the-good-one", dst.name)
        self.assertEqual(json.loads(dst.read_text())["label"], "the good one")

    def test_naming_a_version_does_not_move_the_revision(self):
        sedit.save_edit(self.d, self._doc())
        before = sedit.load_edit(self.d)["revision"]
        sedit.archive_edit(self.d, sedit.load_edit(self.d), "keeper")
        self.assertEqual(sedit.load_edit(self.d)["revision"], before)

    def test_the_prune_never_takes_a_named_version(self):
        sedit.save_edit(self.d, self._doc())
        sedit.archive_edit(self.d, sedit.load_edit(self.d), "keeper")
        for _ in range(sedit.EDIT_HISTORY_KEEP + 20):
            sedit.save_edit(self.d, sedit.load_edit(self.d))
        kept = list(sedit.history_dir(self.d).glob("keep-r*.json"))
        auto = list(sedit.history_dir(self.d).glob("edit-r*.json"))
        self.assertEqual(len(kept), 1)
        self.assertLessEqual(len(auto), sedit.EDIT_HISTORY_KEEP)

    def test_archiving_the_same_revision_twice_is_not_two_files(self):
        sedit.save_edit(self.d, self._doc())
        doc = sedit.load_edit(self.d)
        self.assertIsNotNone(sedit.archive_edit(self.d, doc, "one"))
        self.assertIsNone(sedit.archive_edit(self.d, doc, "one"))

    # ---- listing --------------------------------------------------------
    def test_the_listing_is_newest_first_and_says_what_it_would_restore(self):
        sedit.save_edit(self.d, self._doc(n=1))
        sedit.save_edit(self.d, self._doc(rev=1, n=3))
        rows = sedit.list_history(self.d)
        self.assertTrue(rows)
        self.assertEqual(rows[0]["clips"], 1)      # the rev-1 doc, archived last
        self.assertEqual(rows[0]["duration"], 2.0)
        self.assertFalse(rows[0]["kept"])

    def test_an_unreadable_entry_is_reported_and_not_swallowed(self):
        # A file that vanishes from the list is indistinguishable from one that
        # was never written, and the point of the folder is seeing what is left.
        sedit.history_dir(self.d).mkdir(parents=True, exist_ok=True)
        (sedit.history_dir(self.d) / "edit-r00007.json").write_text("{ broken")
        rows = sedit.list_history(self.d)
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["readable"])

    def test_no_history_is_an_empty_list_not_an_error(self):
        self.assertEqual(sedit.list_history(self.d), [])

    # ---- restoring ------------------------------------------------------
    def test_restore_puts_the_arrangement_back(self):
        sedit.save_edit(self.d, self._doc(n=3))           # rev 1: three clips
        sedit.save_edit(self.d, self._doc(rev=1, n=1))    # rev 2: one clip
        old = [r for r in sedit.list_history(self.d) if r["clips"] == 3][0]
        doc = sedit.restore_edit(self.d, old["file"])
        self.assertEqual(len(doc["clips"]), 3)

    def test_restore_keeps_what_it_replaced(self):
        sedit.save_edit(self.d, self._doc(n=3))
        sedit.save_edit(self.d, self._doc(rev=1, n=1))
        old = [r for r in sedit.list_history(self.d) if r["clips"] == 3][0]
        sedit.restore_edit(self.d, old["file"])
        # the one-clip arrangement it overwrote is still in the folder
        self.assertTrue(any(r["clips"] == 1 for r in sedit.list_history(self.d)))

    def test_restore_moves_the_revision_forward_never_back(self):
        sedit.save_edit(self.d, self._doc(n=3))
        sedit.save_edit(self.d, self._doc(rev=1, n=1))
        now = sedit.load_edit(self.d)["revision"]
        old = [r for r in sedit.list_history(self.d) if r["clips"] == 3][0]
        doc = sedit.restore_edit(self.d, old["file"])
        self.assertEqual(doc["revision"], now + 1)

    def test_restore_migrates_a_version_written_by_an_older_build(self):
        sedit.save_edit(self.d, self._doc())
        hist = sedit.history_dir(self.d)
        hist.mkdir(parents=True, exist_ok=True)
        v1 = dict(self._doc(rev=4, n=2), version=1)
        (hist / "keep-r00004-old.json").write_text(json.dumps(v1))
        doc = sedit.restore_edit(self.d, "keep-r00004-old.json")
        self.assertEqual(doc["version"], sedit.EDIT_VERSION)
        self.assertEqual(len(doc["clips"]), 2)

    def test_the_label_does_not_ride_back_into_the_live_document(self):
        sedit.save_edit(self.d, self._doc())
        sedit.archive_edit(self.d, sedit.load_edit(self.d), "keeper")
        name = [r["file"] for r in sedit.list_history(self.d) if r["kept"]][0]
        doc = sedit.restore_edit(self.d, name)
        self.assertNotIn("label", doc)
        self.assertNotIn("archived_at", doc)

    def test_a_name_that_leaves_the_folder_is_refused(self):
        sedit.save_edit(self.d, self._doc())
        for bad in ("../edit.json", "/etc/passwd", "sub/x.json", "", "x.txt"):
            with self.subTest(bad=bad):
                with self.assertRaises(sedit.EditError):
                    sedit.restore_edit(self.d, bad)

    def test_restoring_something_that_is_not_there_is_an_error(self):
        with self.assertRaises(sedit.EditError):
            sedit.restore_edit(self.d, "keep-r09999-nope.json")


class VersionRoutes(EditorCase):
    """The three seams the panel exposes for it."""

    def _prime(self):
        clips = [self.out / "a.mp4"]
        for c in clips:
            c.write_bytes(b"0" * 32)
        board = _board(clips)
        storyboard.save_storyboard(self.state, board)
        bdir = self.state / "storyboards" / board["id"]
        sedit.save_edit(bdir, _edit([_clip(str(clips[0]), 0.0, 2.0, 0.0)]))
        return board, bdir

    def test_versions_lists_and_version_keeps(self):
        board, bdir = self._prime()
        h = FakeHandler().post("edit/version", {"id": board["id"],
                                                "label": "first pass"})
        self.assertTrue(h.payload["ok"])
        self.assertTrue(h.payload["file"].startswith("keep-r"))
        g = FakeHandler().get("/storyboard/edit/versions?id=" + board["id"])
        self.assertTrue(g.payload["ok"])
        self.assertEqual(g.payload["keep"], sedit.EDIT_HISTORY_KEEP)
        self.assertTrue(any(v["label"] == "first pass"
                            for v in g.payload["versions"]))

    def test_a_version_needs_a_name(self):
        board, _ = self._prime()
        h = FakeHandler().post("edit/version", {"id": board["id"], "label": "  "})
        self.assertFalse(h.payload["ok"])
        self.assertEqual(h.status, 400)

    def test_restore_answers_with_the_whole_payload(self):
        board, bdir = self._prime()
        sedit.save_edit(bdir, _edit([]))          # wipe it, on purpose
        old = [v for v in sedit.list_history(bdir) if v["clips"] == 1][0]
        h = FakeHandler().post("edit/restore", {"id": board["id"],
                                                "file": old["file"]})
        self.assertTrue(h.payload["ok"])
        self.assertEqual(len(h.payload["edit"]["clips"]), 1)
        self.assertIn("versions", h.payload)
        self.assertIn("proxy_url", h.payload)      # the full read shape

    def test_restore_is_refused_while_the_film_is_rendering(self):
        board, bdir = self._prime()
        sedit.save_edit(bdir, sedit.load_edit(bdir))   # one entry in history
        old = sedit.list_history(bdir)[0]
        with mock.patch.dict(panel._SB_RENDERS,
                             {board["id"]: {"stop": False}}, clear=False):
            h = FakeHandler().post("edit/restore", {"id": board["id"],
                                                    "file": old["file"]})
        self.assertFalse(h.payload["ok"])
        self.assertEqual(h.status, 409)
        self.assertTrue(h.payload["busy"])

    def test_a_restore_that_escapes_the_folder_is_a_400(self):
        board, _ = self._prime()
        h = FakeHandler().post("edit/restore", {"id": board["id"],
                                               "file": "../edit.json"})
        self.assertFalse(h.payload["ok"])
        self.assertEqual(h.status, 400)


class UploadIntoThePool(unittest.TestCase):
    """Wave 4 — "you cannot upload your own images and insert them into the
    timeline". Where the bytes land is the whole design."""

    IMAGES = {".png", ".jpg", ".jpeg", ".webp"}
    VIDEOS = {".mp4", ".mov", ".m4v", ".webm"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.up = self.root / "panel_uploads"
        self.out = self.root / "outputs"
        self.up.mkdir()
        self.out.mkdir()
        self._patches = [
            mock.patch.object(panel, "UPLOADS", self.up),
            mock.patch.object(panel, "OUTPUT", self.out),
            mock.patch.object(panel, "STATE_DIR", self.root / "state"),
            mock.patch.object(panel, "push", lambda *a, **k: None),
        ]
        for q in self._patches:
            q.start()
        self.addCleanup(lambda: [q.stop() for q in reversed(self._patches)])

    def accept(self, name, data=b"bytes"):
        return panel._sbe_accept_upload(name, data, images=self.IMAGES,
                                        videos=self.VIDEOS)

    def test_a_picture_lands_where_the_gallery_already_looks(self):
        # list_outputs walks UPLOADS/library/manual — so the still turns up in
        # the Images source by itself, with no second listing to keep in sync.
        with mock.patch.object(panel, "_sb_probe_still",
                               return_value={"w": 1024, "h": 576}):
            r = self.accept("Title Card.png")
        self.assertTrue(r["ok"])
        self.assertEqual(r["kind"], "still")
        self.assertIn("library/manual", r["path"])
        self.assertTrue(Path(r["path"]).is_file())

    def test_a_clip_is_kept_out_of_the_renders_folder(self):
        # OUTPUT means "the panel made this". A phone clip did not.
        with mock.patch.object(panel, "_sb_probe_clip",
                               return_value={"w": 1920, "h": 1080,
                                             "duration": 7.5,
                                             "sample_rate": 48000,
                                             "has_audio": True}):
            r = self.accept("IMG_2213.mov")
        self.assertTrue(r["ok"])
        self.assertEqual(r["kind"], "video")
        self.assertEqual(r["duration_s"], 7.5)
        self.assertEqual(Path(r["path"]).parent, self.up / "timeline")
        self.assertEqual(list(self.out.iterdir()), [])

    def test_both_roots_are_ones_the_timeline_may_already_read(self):
        # The containment rule is not widened for uploads — they land inside
        # what it already allows.
        with mock.patch.object(panel, "_sb_probe_still",
                               return_value={"w": 8, "h": 8}):
            img = self.accept("a.png")["path"]
        with mock.patch.object(panel, "_sb_probe_clip",
                               return_value={"w": 8, "h": 8, "duration": 1.0,
                                             "sample_rate": 0, "has_audio": False}):
            vid = self.accept("b.mp4")["path"]
        self.assertTrue(panel._sbe_pool_path_ok(img))
        self.assertTrue(panel._sbe_pool_path_ok(vid))

    def test_a_file_that_is_not_what_it_claims_is_refused_and_deleted(self):
        # A broken .png would otherwise sit in the pool looking fine and fail
        # at the render hours later, with the upload long forgotten.
        with mock.patch.object(panel, "_sb_probe_still", return_value=None):
            r = self.accept("not-really.png")
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], 400)
        lib = self.up / "library" / "manual"
        self.assertFalse(any(p.is_file() for p in lib.rglob("*")))

    def test_an_extension_nobody_asked_for_is_refused_before_it_is_written(self):
        for name in ("payload.exe", "notes.txt", "archive.zip", "noextension"):
            with self.subTest(name=name):
                r = self.accept(name)
                self.assertFalse(r["ok"])
                self.assertEqual(r["status"], 400)
        self.assertFalse(any(p.is_file() for p in self.up.rglob("*")))

    def test_a_name_that_tries_to_walk_out_cannot(self):
        with mock.patch.object(panel, "_sb_probe_still",
                               return_value={"w": 8, "h": 8}):
            r = self.accept("../../../../etc/evil.png")
        self.assertTrue(r["ok"])
        # basename only, then sanitised — it lands in the library like any other
        self.assertTrue(Path(r["path"]).resolve().is_relative_to(self.up.resolve()))
        self.assertNotIn("..", Path(r["path"]).name)

    def test_the_uploads_listing_hides_our_own_bookkeeping(self):
        (self.up / "timeline").mkdir(parents=True)
        (self.up / "timeline" / "1787000000000_IMG_2213.mov").write_bytes(b"x")
        (self.up / "timeline" / "notes.txt").write_bytes(b"x")
        h = FakeHandler().get("/storyboard/edit/uploads")
        self.assertTrue(h.payload["ok"])
        self.assertEqual([u["name"] for u in h.payload["uploads"]],
                         ["IMG_2213.mov"])

    def test_no_uploads_folder_is_an_empty_list_not_an_error(self):
        h = FakeHandler().get("/storyboard/edit/uploads")
        self.assertTrue(h.payload["ok"])
        self.assertEqual(h.payload["uploads"], [])


class DraftsAreTheUsersOwn(unittest.TestCase):
    """"He should have control over the saving, and only he should have that
    control... renaming and access to all the drafts he makes are essential."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)

    def _doc(self, n=1, rev=0):
        return {"version": sedit.EDIT_VERSION, "board_id": "b", "revision": rev,
                "source": "human", "audio": None, "beats": None, "settings": {},
                "clips": [{"id": f"c{i}", "path": "/x/a.mp4", "proxy": None,
                           "start": 0.0, "end": 2.0, "film_start": 2.0 * i,
                           "film_end": 2.0 * (i + 1), "source": "human",
                           "locked": False} for i in range(n)]}

    def _save(self, n=1):
        """A save that carries the counter forward, the way the route does.

        `revision` is the server's, read off disk — a fresh document at 0
        every time would make two consecutive saves land on the same archive
        name, which is the collision these tests are about.
        """
        doc = self._doc(n=n)
        doc["revision"] = int((sedit._read_json(sedit.edit_path(self.d)) or {})
                              .get("revision") or 0)
        return sedit.save_edit(self.d, doc)

    # ---- migration -------------------------------------------------------
    def test_a_board_written_before_drafts_has_exactly_one(self):
        # No migration pass, nothing to half-finish: the file it already has
        # IS the draft, and looking is what names it.
        sedit.save_edit(self.d, self._doc(n=3))
        rows = sedit.list_drafts(self.d)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["active"])
        self.assertEqual(rows[0]["clips"], 3)
        self.assertEqual(rows[0]["name"], "Draft 1")

    def test_the_active_draft_is_edit_json_and_nothing_shadows_it(self):
        # One copy of any draft, ever — the active one lives where every
        # existing reader already looks.
        sedit.save_edit(self.d, self._doc())
        sedit.list_drafts(self.d)
        self.assertFalse((sedit.drafts_dir(self.d) / "draft-1.json").exists())

    # ---- the verbs -------------------------------------------------------
    def test_a_new_draft_from_current_copies_the_cut(self):
        sedit.save_edit(self.d, self._doc(n=4))
        out = sedit.create_draft(self.d, "variation", from_current=True)
        self.assertEqual(out["name"], "variation")
        self.assertEqual(len(sedit.load_edit(self.d)["clips"]), 4)
        # ...and the one it came from is still whole, in its own file.
        stashed = json.loads((sedit.drafts_dir(self.d) / "draft-1.json").read_text())
        self.assertEqual(len(stashed["clips"]), 4)

    def test_an_empty_draft_keeps_the_soundtrack(self):
        # The track is a fact about the film, not about the arrangement.
        doc = self._doc(n=2)
        doc["audio"] = {"path": "/x/s.wav", "offset": 0, "mode": "under"}
        sedit.save_edit(self.d, doc)
        sedit.create_draft(self.d, "from scratch")
        fresh = sedit.load_edit(self.d)
        self.assertEqual(fresh["clips"], [])
        self.assertEqual(fresh["audio"]["path"], "/x/s.wav")

    def test_switching_drafts_never_loses_the_one_being_left(self):
        sedit.save_edit(self.d, self._doc(n=5))
        sedit.create_draft(self.d, "second")
        sedit.save_edit(self.d, dict(sedit.load_edit(self.d), clips=[]))
        sedit.activate_draft(self.d, "draft-1")
        self.assertEqual(len(sedit.load_edit(self.d)["clips"]), 5)
        sedit.activate_draft(self.d, "second")
        self.assertEqual(sedit.load_edit(self.d)["clips"], [])

    def test_duplicate_lands_on_the_copy(self):
        sedit.save_edit(self.d, self._doc(n=2))
        out = sedit.duplicate_draft(self.d, "draft-1", "take two")
        rows = {r["slug"]: r for r in sedit.list_drafts(self.d)}
        self.assertTrue(rows[out["slug"]]["active"])
        self.assertEqual(rows[out["slug"]]["clips"], 2)
        self.assertEqual(len(rows), 2)

    def test_renaming_does_not_move_a_file(self):
        # The slug is the identity, so a rename can never orphan a draft's
        # file or its backup.
        sedit.save_edit(self.d, self._doc())
        sedit.create_draft(self.d, "b roll")
        before = sorted(p.name for p in sedit.drafts_dir(self.d).iterdir())
        sedit.rename_draft(self.d, "b-roll", "the good b roll")
        after = sorted(p.name for p in sedit.drafts_dir(self.d).iterdir())
        self.assertEqual(before, after)
        names = [r["name"] for r in sedit.list_drafts(self.d)]
        self.assertIn("the good b roll", names)

    def test_the_last_draft_cannot_be_deleted(self):
        # An editor with no document is not a state this app has.
        sedit.save_edit(self.d, self._doc())
        with self.assertRaises(sedit.EditError):
            sedit.delete_draft(self.d, "draft-1")

    def test_deleting_the_active_draft_lands_on_a_neighbour_first(self):
        sedit.save_edit(self.d, self._doc(n=3))
        sedit.create_draft(self.d, "scratch")
        sedit.delete_draft(self.d, "scratch")
        self.assertEqual(len(sedit.load_edit(self.d)["clips"]), 3)
        self.assertEqual([r["slug"] for r in sedit.list_drafts(self.d)], ["draft-1"])

    def _make_v1_on_disk(self):
        """The state every board written before EDIT_VERSION 2 is still in.

        Migration is READ-path only, so `edit.json` stays v1 on disk until its
        next save — which for a board somebody opens and immediately copies a
        draft from is never.
        """
        sedit.save_edit(self.d, self._doc(n=3))
        p = sedit.edit_path(self.d)
        doc = json.loads(p.read_text())
        doc["version"] = 1
        for c in doc["clips"]:
            c.pop("kind", None)
        p.write_text(json.dumps(doc))
        return doc

    def test_a_draft_can_be_copied_from_a_board_written_before_v2(self):
        self._make_v1_on_disk()
        out = sedit.create_draft(self.d, "take two", from_current=True)
        self.assertEqual(len(sedit.load_edit(self.d)["clips"]), 3)
        self.assertEqual(sedit.load_draft_index(self.d)["active"], out["slug"])

    def test_duplicate_works_on_a_board_written_before_v2(self):
        self._make_v1_on_disk()
        out = sedit.duplicate_draft(self.d, "draft-1")
        self.assertEqual(len(sedit.load_edit(self.d)["clips"]), 3)
        self.assertEqual(sedit.load_draft_index(self.d)["active"], out["slug"])

    def test_an_empty_draft_off_a_v1_board_carries_its_soundtrack(self):
        self._make_v1_on_disk()
        sedit.create_draft(self.d, "empty one")
        self.assertEqual(sedit.load_edit(self.d)["clips"], [])

    def test_a_refused_draft_never_moves_the_active_pointer(self):
        # The compound failure: the index was written BEFORE the document, so
        # a save the validator refused left `list_drafts` reporting the new
        # name as active while `edit.json` still held the old draft's cut.
        sedit.save_edit(self.d, self._doc(n=3))
        with mock.patch.object(sedit, "save_edit",
                               side_effect=sedit.EditError("nope")):
            with self.assertRaises(sedit.EditError):
                sedit.create_draft(self.d, "doomed", from_current=True)
        idx = sedit.load_draft_index(self.d)
        self.assertEqual(idx["active"], "draft-1")
        self.assertEqual([d["slug"] for d in idx["drafts"]], ["draft-1"])
        self.assertEqual(len(sedit.load_edit(self.d)["clips"]), 3)

    def test_a_refused_activate_leaves_the_film_on_the_draft_it_was_on(self):
        sedit.save_edit(self.d, self._doc(n=3))
        sedit.create_draft(self.d, "other")
        with mock.patch.object(sedit, "save_edit",
                               side_effect=sedit.EditError("nope")):
            with self.assertRaises(sedit.EditError):
                sedit.activate_draft(self.d, "draft-1")
        self.assertEqual(sedit.load_draft_index(self.d)["active"], "other")

    def test_two_drafts_with_one_name_get_two_slugs(self):
        sedit.save_edit(self.d, self._doc())
        a = sedit.create_draft(self.d, "take")
        b = sedit.create_draft(self.d, "take")
        self.assertNotEqual(a["slug"], b["slug"])

    # ---- history belongs to a draft --------------------------------------
    def test_two_drafts_do_not_share_one_history(self):
        # THE COLLISION: entries were named by revision alone in one folder
        # per BOARD, while every new draft restarts its revision at zero. So
        # draft B's rev 1 collided with draft A's, archive_edit dropped it
        # silently, and the picker shown while B was open listed A's saves.
        self._save(n=1)
        self._save(n=2)                                # rev 1 -> draft-1's
        sedit.create_draft(self.d, "take two")
        self._save(n=7)
        self._save(n=8)                                # -> take-two's own
        self.assertIn(7, [r["clips"] for r in sedit.list_history(self.d)])
        self.assertNotIn(1, [r["clips"] for r in sedit.list_history(self.d)])
        sedit.activate_draft(self.d, "draft-1")
        # Draft 1's own two: the save it made, and the cut it was holding when
        # the second draft was branched off it. Neither of the other draft's.
        self.assertEqual([r["clips"] for r in sedit.list_history(self.d)], [2, 1])

    def test_restore_refuses_a_version_belonging_to_another_draft(self):
        self._save(n=1)
        sedit.archive_edit(self.d, sedit.load_edit(self.d), "almost final")
        theirs = [r["file"] for r in sedit.list_history(self.d) if r["kept"]][0]
        sedit.create_draft(self.d, "take two")
        self._save(n=7)
        with self.assertRaises(sedit.EditError):
            sedit.restore_edit(self.d, theirs)
        self.assertEqual(len(sedit.load_edit(self.d)["clips"]), 7)
        # ...and it is still there for the draft it belongs to.
        sedit.activate_draft(self.d, "draft-1")
        self.assertEqual(len(sedit.restore_edit(self.d, theirs)["clips"]), 1)

    def test_deleting_a_draft_takes_its_past_saves_with_it(self):
        # What the panel says when it asks, made true.
        sedit.save_edit(self.d, self._doc(n=1))
        sedit.create_draft(self.d, "scratch")
        sedit.save_edit(self.d, self._doc(n=4))
        sedit.save_edit(self.d, self._doc(n=5))
        self.assertTrue(sedit.history_dir(self.d, "scratch").is_dir())
        sedit.delete_draft(self.d, "scratch")
        self.assertFalse(sedit.history_dir(self.d, "scratch").exists())
        self.assertTrue(sedit.history_dir(self.d, "draft-1").exists()
                        or not sedit.list_history(self.d))

    def test_history_written_before_drafts_is_folded_into_the_first_draft(self):
        # A board that already has fifty saves must not lose them to a folder
        # nothing lists any more.
        sedit.save_edit(self.d, self._doc(n=1))
        sedit.save_edit(self.d, self._doc(n=2))
        home = sedit.history_dir(self.d, "draft-1")
        root = self.d / "history"
        for p in list(home.glob("*.json")):
            p.replace(root / p.name)            # put it back the old way
        home.rmdir()
        rows = sedit.list_history(self.d)
        self.assertEqual([r["clips"] for r in rows], [1])
        self.assertTrue((sedit.history_dir(self.d, "draft-1")
                         / rows[0]["file"]).is_file())

    def test_an_interrupted_atomic_write_is_not_a_version(self):
        # pathlib's glob returns dotfiles and `_atomic_json` writes its temp
        # beside the target, so a crash mid-write used to leave a clickable
        # row with no name and revision None.
        sedit.save_edit(self.d, self._doc(n=1))
        sedit.save_edit(self.d, self._doc(n=2))
        hist = sedit.history_dir(self.d)
        (hist / ".edit-abcdef.json").write_text('{"version": 2}')
        self.assertEqual(len(sedit.list_history(self.d)), 1)
        with self.assertRaises(sedit.EditError):
            sedit.restore_edit(self.d, ".edit-abcdef.json")

    # ---- the quiet lane --------------------------------------------------
    def test_a_backup_never_touches_the_saved_draft(self):
        sedit.save_edit(self.d, self._doc(n=2))
        saved = sedit.load_edit(self.d)
        sedit.write_backup(self.d, dict(self._doc(n=7), revision=saved["revision"]))
        after = sedit.load_edit(self.d)
        self.assertEqual(len(after["clips"]), 2)
        self.assertEqual(after["revision"], saved["revision"])

    def test_a_newer_backup_is_offered_not_applied(self):
        sedit.save_edit(self.d, self._doc(n=2))
        sedit.write_backup(self.d, self._doc(n=6))
        offer = sedit.pending_backup(self.d)
        self.assertIsNotNone(offer)
        self.assertEqual(offer["clips"], 6)
        self.assertEqual(len(sedit.load_edit(self.d)["clips"]), 2)   # untouched

    def test_a_backup_of_what_is_already_saved_is_not_a_nag(self):
        sedit.save_edit(self.d, self._doc(n=2))
        sedit.write_backup(self.d, sedit.load_edit(self.d))
        self.assertIsNone(sedit.pending_backup(self.d))

    def test_recovering_keeps_the_draft_it_replaces(self):
        sedit.save_edit(self.d, self._doc(n=2))
        sedit.write_backup(self.d, self._doc(n=6))
        doc = sedit.recover_backup(self.d)
        self.assertEqual(len(doc["clips"]), 6)
        self.assertTrue(any(r["clips"] == 2 for r in sedit.list_history(self.d)))
        # ...and the offer is spent, not left to be made again.
        self.assertIsNone(sedit.pending_backup(self.d))

    def test_the_backup_is_not_listed_as_a_past_save(self):
        # "Recover" and "Restore" mean two different things; side by side in
        # one list they would read as one.
        sedit.save_edit(self.d, self._doc())
        sedit.write_backup(self.d, self._doc(n=3))
        self.assertFalse(any(r["file"].startswith("backup-")
                             for r in sedit.list_history(self.d)))

    def test_each_draft_has_its_own_backup(self):
        sedit.save_edit(self.d, self._doc(n=2))
        sedit.write_backup(self.d, self._doc(n=9))
        sedit.create_draft(self.d, "other")
        self.assertIsNone(sedit.pending_backup(self.d))     # not this draft's
        sedit.activate_draft(self.d, "draft-1")
        self.assertIsNotNone(sedit.pending_backup(self.d))

    def test_an_offer_survives_everything_that_is_not_the_user_answering(self):
        # THE TWENTY MINUTES, THE SECOND TIME. The offer used to be suppressed
        # by a wall clock: `backed_up_at < updated_at` and it was gone. But
        # `updated_at` moves on every write, and three of them are not the
        # user's — a draft switch, an auto-edit, a restore — so unsaved work
        # that had only ever reached the crash lane became unreachable while
        # the file sat on disk holding the only copy of it.
        sedit.save_edit(self.d, self._doc(n=2))
        sedit.write_backup(self.d, self._doc(n=9))
        p = sedit.latest_snapshot(self.d)[0]
        doc = json.loads(p.read_text())
        doc["backed_up_at"] = 1            # as old as the clock can make it
        p.write_text(json.dumps(doc))
        sedit.create_draft(self.d, "other")
        sedit.activate_draft(self.d, "draft-1")
        sedit.save_edit(self.d, self._doc(n=2), origin="auto")
        offer = sedit.pending_backup(self.d)
        self.assertIsNotNone(offer)
        self.assertEqual(offer["clips"], 9)

    def test_the_backup_records_the_revision_it_followed(self):
        sedit.save_edit(self.d, self._doc(n=2))
        rev = sedit.load_edit(self.d)["revision"]
        sedit.write_backup(self.d, self._doc(n=9))
        doc = sedit.latest_snapshot(self.d)[1]
        self.assertEqual(doc["backup_revision"], rev)

    def test_a_backup_composed_on_a_draft_the_user_has_left_is_refused(self):
        # The client debounces this write and the server is threaded, so a
        # backup composed while draft A was on screen can arrive after the
        # user clicked draft B. Filed under B, it is A's arrangement offered
        # back as B's unsaved work — and recovering it would install A's cut
        # over B's saved document.
        sedit.save_edit(self.d, self._doc(n=2))
        sedit.create_draft(self.d, "other")
        with self.assertRaises(sedit.EditError):
            sedit.write_backup(self.d, self._doc(n=9), draft="draft-1")
        self.assertIsNone(sedit.pending_backup(self.d))
        # ...and a caller that names the draft it IS on is written normally.
        sedit.write_backup(self.d, self._doc(n=9), draft="other")
        self.assertIsNotNone(sedit.pending_backup(self.d))

    def test_recover_refuses_a_backup_the_save_has_already_answered(self):
        # A client can hold a stale offer — it saved a second ago and the
        # amber bar is still on screen in another tab. Applying that would
        # archive the good save and install an older arrangement over it.
        sedit.save_edit(self.d, self._doc(n=2))
        sedit.write_backup(self.d, self._doc(n=6))
        sedit.save_edit(self.d, self._doc(n=6))       # the user saved it
        with self.assertRaises(sedit.EditError):
            sedit.recover_backup(self.d)
        self.assertEqual(len(sedit.load_edit(self.d)["clips"]), 6)


class DraftRoutes(EditorCase):
    def _prime(self):
        clip = self.out / "a.mp4"
        clip.write_bytes(b"0" * 32)
        board = _board([clip])
        storyboard.save_storyboard(self.state, board)
        bdir = self.state / "storyboards" / board["id"]
        sedit.save_edit(bdir, _edit([_clip(str(clip), 0.0, 2.0, 0.0)]))
        return board, bdir

    def test_the_read_payload_carries_the_drafts_and_any_offer(self):
        board, _ = self._prime()
        h = FakeHandler().get("/storyboard/edit?id=" + board["id"])
        self.assertIn("drafts", h.payload)
        self.assertEqual(h.payload["active_draft"], "draft-1")
        self.assertIsNone(h.payload["backup"])

    def test_one_route_serves_every_draft_verb(self):
        board, bdir = self._prime()
        h = FakeHandler().post("edit/draft", {"id": board["id"], "op": "new",
                                              "name": "variation",
                                              "from": "current"})
        self.assertTrue(h.payload["ok"])
        self.assertEqual(h.payload["draft"]["name"], "variation")
        self.assertEqual(h.payload["active_draft"], h.payload["draft"]["slug"])
        h = FakeHandler().post("edit/draft", {"id": board["id"], "op": "rename",
                                              "slug": "variation",
                                              "name": "the good one"})
        self.assertTrue(h.payload["ok"])
        h = FakeHandler().post("edit/draft", {"id": board["id"],
                                              "op": "activate",
                                              "slug": "draft-1"})
        self.assertTrue(h.payload["ok"])
        self.assertEqual(h.payload["active_draft"], "draft-1")

    def test_an_unknown_draft_verb_is_refused(self):
        board, _ = self._prime()
        h = FakeHandler().post("edit/draft", {"id": board["id"], "op": "yolo"})
        self.assertFalse(h.payload["ok"])
        self.assertEqual(h.status, 400)

    def test_changing_drafts_is_refused_while_the_film_renders(self):
        board, _ = self._prime()
        with mock.patch.dict(panel._SB_RENDERS,
                             {board["id"]: {"stop": False}}, clear=False):
            h = FakeHandler().post("edit/draft", {"id": board["id"],
                                                  "op": "activate",
                                                  "slug": "draft-1"})
        self.assertEqual(h.status, 409)
        self.assertTrue(h.payload["busy"])

    def test_the_backup_route_writes_no_revision(self):
        board, bdir = self._prime()
        before = sedit.load_edit(bdir)["revision"]
        payload = {"id": board["id"], "edit": _edit([])}
        h = FakeHandler().post("edit/backup", None, json.dumps(payload))
        self.assertTrue(h.payload["ok"])
        after = sedit.load_edit(bdir)
        self.assertEqual(after["revision"], before)
        self.assertEqual(len(after["clips"]), 1)      # the draft is untouched

    def test_recover_answers_with_the_whole_payload(self):
        board, bdir = self._prime()
        sedit.write_backup(bdir, _edit([]))
        h = FakeHandler().post("edit/recover", {"id": board["id"]})
        self.assertTrue(h.payload["ok"])
        self.assertEqual(h.payload["edit"]["clips"], [])
        self.assertIn("proxy_url", h.payload)

    def test_saving_answers_the_offer_and_takes_the_file_with_it(self):
        # A SAVE IS THE USER ANSWERING. Left behind, the file kept the amber
        # bar on screen over a saved film, armed a Recover button that would
        # have reverted the save, and — because write_backup refuses to
        # overwrite an unanswered offer — killed the crash lane for the rest
        # of the session, at which point the watchdog started calling a
        # healthy panel broken.
        board, bdir = self._prime()
        sedit.write_backup(bdir, _edit([]))
        self.assertIsNotNone(sedit.pending_backup(bdir))
        payload = {"id": board["id"], "edit": sedit.load_edit(bdir)}
        h = FakeHandler().post("edit/save", None, json.dumps(payload))
        self.assertTrue(h.payload["ok"])
        self.assertIsNone(sedit.pending_backup(bdir))
        self.assertIsNone(h.payload["backup"])
        self.assertIsNone(sedit.latest_snapshot(bdir))
        # ...and the lane is usable again on the very next write.
        sedit.write_backup(bdir, _edit([]), draft="draft-1")

    def test_the_save_payload_carries_the_drafts_it_just_changed(self):
        board, bdir = self._prime()
        payload = {"id": board["id"], "edit": sedit.load_edit(bdir)}
        h = FakeHandler().post("edit/save", None, json.dumps(payload))
        rows = h.payload["drafts"]
        self.assertEqual([r["slug"] for r in rows], ["draft-1"])
        self.assertEqual(rows[0]["revision"], sedit.load_edit(bdir)["revision"])

    def test_discard_takes_the_offer_away(self):
        board, bdir = self._prime()
        sedit.write_backup(bdir, _edit([]))
        self.assertIsNotNone(sedit.pending_backup(bdir))
        h = FakeHandler().post("edit/discard-backup", {"id": board["id"]})
        self.assertTrue(h.payload["ok"])
        self.assertIsNone(sedit.pending_backup(bdir))


class AnUploadedPictureIsAStill(unittest.TestCase):
    """Hit live: a PNG uploaded and dragged onto the track landed with no
    `kind`, so clip_kind() called it a video, the client asked /file instead
    of /image, a <video> was handed a PNG and the preview went black — with
    nothing on screen to say why."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "state"
        self.out = self.root / "outputs"
        self.up = self.root / "panel_uploads"
        for d in (self.state, self.out, self.up):
            d.mkdir()
        self.img = self.up / "library" / "manual" / "20260818" / "uploads"
        self.img.mkdir(parents=True)
        self.png = self.img / "1787_title_card.png"
        self.png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        self._p = [mock.patch.object(panel, "STATE_DIR", self.state),
                   mock.patch.object(panel, "OUTPUT", self.out),
                   mock.patch.object(panel, "UPLOADS", self.up),
                   mock.patch.object(panel, "push", lambda *a, **k: None)]
        for q in self._p:
            q.start()
        self.addCleanup(lambda: [q.stop() for q in reversed(self._p)])
        self.board = _board([self.out / "a.mp4"])
        (self.out / "a.mp4").write_bytes(b"0" * 16)
        storyboard.save_storyboard(self.state, self.board)

    def test_the_server_calls_a_png_a_still_even_when_nobody_said_so(self):
        # This is the exact request the drag sent: a path, a title, no kind.
        with mock.patch.object(panel, "_sb_probe_still",
                               return_value={"w": 1024, "h": 576}):
            h = FakeHandler().post("edit/add-clip",
                                   {"id": self.board["id"],
                                    "path": str(self.png),
                                    "title": "title card"})
        self.assertTrue(h.payload["ok"], h.payload)
        self.assertEqual(h.payload["clip"]["kind"], "still")
        self.assertIsNone(h.payload["clip"]["proxy"])

    def test_every_picture_extension_the_exporter_knows_is_covered(self):
        for sfx in sedit._STILL_SUFFIXES:
            with self.subTest(sfx=sfx):
                f = self.img / ("card" + sfx)
                f.write_bytes(b"0" * 32)
                with mock.patch.object(panel, "_sb_probe_still",
                                       return_value={"w": 8, "h": 8}):
                    h = FakeHandler().post("edit/add-clip",
                                           {"id": self.board["id"],
                                            "path": str(f)})
                self.assertEqual(h.payload["clip"]["kind"], "still")

    def test_a_still_that_lands_this_way_is_a_valid_document(self):
        # The end of the bug: a clip whose kind survives into edit.json and
        # whose window is the slot, not a source clock it does not have.
        c = sedit.new_clip(str(self.png), 0.0, 3.0, 0.0, kind="still",
                           source="human")
        doc = sedit.normalise_edit(_edit([c]))
        self.assertEqual(doc["clips"][0]["kind"], "still")
        self.assertIsNone(doc["clips"][0]["duration"])
        self.assertEqual(sedit.validate_edit(doc), [])

    def test_a_video_is_still_a_video(self):
        # The suffix test must not reach past pictures.
        mp4 = self.up / "timeline" / "clip.mp4"
        mp4.parent.mkdir(parents=True)
        mp4.write_bytes(b"0" * 32)
        with mock.patch.object(panel, "_sbe_proxy_now",
                               return_value={"built": 1, "reused": 0, "failed": []}), \
             mock.patch("storyboard_edit.probe_media",
                        return_value={"duration": 4.0}):
            h = FakeHandler().post("edit/add-clip",
                                   {"id": self.board["id"], "path": str(mp4)})
        self.assertTrue(h.payload["ok"], h.payload)
        self.assertNotEqual(h.payload["clip"].get("kind"), "still")


class SplitEdits(unittest.TestCase):
    """J-cuts and L-cuts: `clip.audio`, and the promise that absent means
    linked so no document written before tonight has to change."""

    def _clip(self, **kw):
        return dict({"id": "a", "path": "/x/a.mp4", "proxy": None,
                     "start": 0.0, "end": 4.0, "film_start": 0.0,
                     "film_end": 4.0, "source": "human", "locked": False,
                     "duration": 10.0}, **kw)

    def test_absent_means_linked_and_that_is_the_migration(self):
        w = sedit.clip_audio(self._clip())
        self.assertEqual([w["start"], w["end"], w["film_start"]], [0.0, 4.0, 0.0])
        self.assertTrue(w["linked"])
        self.assertEqual(sedit.EDIT_VERSION, 2)

    def test_an_l_cut_leaves_the_sound_running_under_the_neighbour(self):
        w = sedit.clip_audio(self._clip(
            audio={"start": 0.0, "end": 6.0, "film_start": 0.0}))
        self.assertEqual(w["end"], 6.0)
        self.assertFalse(w["linked"])

    def test_a_j_cut_leads_the_sound_in_before_the_picture(self):
        w = sedit.clip_audio(self._clip(
            film_start=4.0, film_end=8.0,
            audio={"start": 0.0, "end": 4.0, "film_start": 3.0}))
        self.assertEqual(w["film_start"], 3.0)
        self.assertFalse(w["linked"])

    def test_the_field_itself_is_the_switch(self):
        # Deriving "linked" from equality looked tidier and was wrong in the
        # one case that matters: unlinking writes the window the clip already
        # had, so a just-unlinked clip read as linked and refused to be
        # dragged. Presence decides; the toggle adds or deletes.
        c = self._clip(audio={"start": 0.0, "end": 4.0, "film_start": 0.0})
        self.assertFalse(sedit.clip_audio(c)["linked"])
        doc = sedit.normalise_edit(_edit([c]))
        self.assertIn("audio", doc["clips"][0])
        self.assertTrue(sedit.clip_audio(self._clip())["linked"])

    def test_a_real_split_survives_the_round_trip(self):
        doc = sedit.normalise_edit(_edit([self._clip(
            audio={"start": 0.5, "end": 4.0, "film_start": 0.5})]))
        self.assertEqual(doc["clips"][0]["audio"],
                         {"start": 0.5, "end": 4.0, "film_start": 0.5})
        self.assertEqual(sedit.validate_edit(doc), [])

    def test_only_a_video_clip_has_sound_to_unlink(self):
        c = {"id": "s", "kind": "slug", "path": None, "start": 0.0, "end": 2.0,
             "film_start": 0.0, "film_end": 2.0, "source": "human",
             "locked": False, "audio": {"start": 0, "end": 1, "film_start": 0}}
        self.assertIn("clip_audio_kind",
                      [e["code"] for e in sedit.validate_edit(_edit([c]))])
        # ...and normalise takes it off rather than leaving a document that
        # cannot be saved.
        self.assertNotIn("audio", sedit.normalise_edit(_edit([c]))["clips"][0])

    def test_a_backwards_sound_window_is_refused(self):
        c = self._clip(audio={"start": 3.0, "end": 1.0, "film_start": 0.0})
        self.assertIn("clip_audio_window",
                      [e["code"] for e in sedit.validate_edit(_edit([c]))])

    def test_sound_past_the_end_of_its_source_is_refused(self):
        c = self._clip(duration=5.0,
                       audio={"start": 0.0, "end": 9.0, "film_start": 0.0})
        self.assertIn("clip_audio_past_the_end",
                      [e["code"] for e in sedit.validate_edit(_edit([c]))])

    def test_two_clips_sound_may_not_overlap_either(self):
        # This is what keeps a split edit from becoming the multi-track mixer
        # the refuse list bans: one lane, still.
        a = self._clip(id="a", audio={"start": 0.0, "end": 6.0,
                                      "film_start": 0.0})
        b = self._clip(id="b", film_start=4.0, film_end=8.0)
        codes = [e["code"] for e in sedit.validate_edit(_edit([a, b]))]
        self.assertIn("clips_audio_overlap", codes)
        self.assertNotIn("clips_overlap", codes)      # the pictures are fine

    def test_the_cut_list_says_nothing_when_nothing_is_split(self):
        # A plan of ordinary cuts must produce the identical list it always
        # did, or every film ever exported renders a different graph.
        cuts = sedit.edit_to_cuts(_edit([self._clip()]))
        self.assertNotIn("audio", cuts[0])

    def test_the_cut_list_carries_the_window_when_it_is_split(self):
        cuts = sedit.edit_to_cuts(_edit([self._clip(
            audio={"start": 0.0, "end": 4.0, "film_start": 1.0})]))
        self.assertEqual(cuts[0]["audio"],
                         {"start": 0.0, "end": 4.0, "film_start": 1.0})


class TheSyncFlag(unittest.TestCase):
    """How far an unlinked pair has come apart, and where it goes back to.

    The sibling of `edit_gaps` and information for the same reason: a J-cut IS
    a deliberate drift, so this can never be an error. What it can be is
    VISIBLE. The owner unlinked a clip, moved the picture, and had no way to
    read how far the two had separated or to put them back — "it is actually
    getting the audio out of sync... what should happen is that I only cut the
    video and left the sound in place."
    """

    def _clip(self, **kw):
        return dict({"id": "a", "path": "/x/a.mp4", "proxy": None,
                     "start": 0.0, "end": 4.0, "film_start": 0.0,
                     "film_end": 4.0, "source": "human", "locked": False,
                     "duration": 10.0}, **kw)

    def test_a_linked_clip_cannot_drift(self):
        self.assertEqual(sedit.clip_audio_drift(self._clip()), 0.0)

    def test_an_unlinked_strip_that_never_moved_reads_zero(self):
        c = self._clip(audio={"start": 0.0, "end": 4.0, "film_start": 0.0})
        self.assertEqual(sedit.clip_audio_drift(c), 0.0)

    def test_late_is_positive_and_early_is_negative(self):
        # The picture moved to film 6 and the sound stayed at 4: the sound runs
        # two seconds AHEAD of the frame it was recorded with.
        early = self._clip(film_start=6.0, film_end=10.0,
                           audio={"start": 0.0, "end": 4.0, "film_start": 4.0})
        self.assertEqual(sedit.clip_audio_drift(early), -2.0)
        late = self._clip(audio={"start": 0.0, "end": 4.0, "film_start": 2.0})
        self.assertEqual(sedit.clip_audio_drift(late), 2.0)

    def test_a_head_trim_on_both_halves_is_not_a_drift(self):
        # Trimming the picture's head moves the in-point and the slot together,
        # and trimming the sound's head does the same to the strip. Neither
        # changes what plays when, so neither is out of sync.
        c = self._clip(start=1.0, film_start=1.0,
                       audio={"start": 2.0, "end": 4.0, "film_start": 2.0})
        self.assertEqual(sedit.clip_audio_drift(c), 0.0)

    def test_resync_keeps_the_in_point_and_moves_the_strip(self):
        # Rematching is not un-trimming: a sound shortened to start a second
        # into the take lands a second after the picture does.
        c = self._clip(film_start=5.0, film_end=9.0,
                       audio={"start": 1.0, "end": 4.0, "film_start": 1.0})
        self.assertEqual(sedit.clip_audio_resync(c), 6.0)
        fixed = dict(c, audio=dict(c["audio"], film_start=6.0))
        self.assertEqual(sedit.clip_audio_drift(fixed), 0.0)
        self.assertEqual(sedit.validate_edit(_edit([fixed])), [])

    def test_the_flag_list_names_only_the_pairs_that_are_out(self):
        ok = self._clip(id="ok", audio={"start": 0.0, "end": 4.0,
                                        "film_start": 0.0})
        linked = self._clip(id="linked", film_start=4.0, film_end=8.0)
        out = self._clip(id="out", film_start=8.0, film_end=12.0,
                         audio={"start": 0.0, "end": 4.0, "film_start": 9.5})
        flags = sedit.edit_sync_flags(_edit([ok, linked, out]))
        self.assertEqual([f["id"] for f in flags], ["out"])
        self.assertEqual(flags[0]["drift"], 1.5)
        self.assertEqual(flags[0]["resync_to"], 8.0)
        self.assertEqual(flags[0]["where"], 2)

    def test_half_a_frame_is_not_a_drift(self):
        # Every window on the timeline is rounded to a microsecond, so an
        # exact-zero test would flag a strip one float away from home.
        near = self._clip(audio={"start": 0.0, "end": 4.0,
                                 "film_start": 1.0 / 96.0})
        self.assertEqual(sedit.edit_sync_flags(_edit([near])), [])
        past = self._clip(audio={"start": 0.0, "end": 4.0,
                                 "film_start": 1.0 / 12.0})
        self.assertEqual(len(sedit.edit_sync_flags(_edit([past]))), 1)

    def test_a_drifted_pair_is_still_a_VALID_document(self):
        # A J-cut is a deliberate drift. Refusing to save one would refuse the
        # feature.
        out = self._clip(film_start=8.0, film_end=12.0,
                         audio={"start": 0.0, "end": 4.0, "film_start": 9.5})
        self.assertEqual(sedit.validate_edit(_edit([out])), [])


class LinkingFreezesTheOffset(unittest.TestCase):
    """`audio.linked` — split, but travelling with its picture.

    Re-linking used to DELETE the field, which snapped the sound back under
    the picture and threw away the J-cut the moment it was made. So the owner
    reached for LOCK instead, and a locked clip refuses every drag with a
    forbidden cursor and no grips — the editor looked broken. Re-linking now
    freezes the relationship: "You just drag it, and the sound below stays,
    and then you can lock it and move it, and then the sound starts before the
    clip starts."
    """

    def _clip(self, **kw):
        return dict({"id": "a", "path": "/x/a.mp4", "proxy": None,
                     "start": 0.0, "end": 4.0, "film_start": 6.0,
                     "film_end": 10.0, "source": "human", "locked": False,
                     "duration": 10.0}, **kw)

    def _coupled(self, **kw):
        return self._clip(audio={"start": 0.0, "end": 4.0, "film_start": 4.0,
                                 "linked": True}, **kw)

    def test_a_coupled_strip_reads_as_linked_AND_split(self):
        w = sedit.clip_audio(self._coupled())
        self.assertTrue(w["linked"])        # cannot be dragged on its own
        self.assertTrue(w["coupled"])
        self.assertTrue(w["split"])         # ...but the window is its own
        self.assertEqual([w["start"], w["end"], w["film_start"]],
                         [0.0, 4.0, 4.0])

    def test_the_offset_is_real_and_is_reported(self):
        self.assertEqual(sedit.clip_audio_drift(self._coupled()), -2.0)
        self.assertEqual(sedit.clip_audio_resync(self._coupled()), 6.0)

    def test_a_coupled_pair_is_never_a_sync_FLAG(self):
        # Its offset is the relationship the user froze and the two travel
        # together, so flagging it would put a permanent warning on every
        # J-cut in the film.
        self.assertEqual(sedit.edit_sync_flags(_edit([self._coupled()])), [])
        free = self._clip(audio={"start": 0.0, "end": 4.0, "film_start": 4.0})
        self.assertEqual(len(sedit.edit_sync_flags(_edit([free]))), 1)

    def test_the_flag_survives_the_round_trip_and_only_when_true(self):
        doc = sedit.normalise_edit(_edit([self._coupled()]))
        self.assertEqual(doc["clips"][0]["audio"],
                         {"start": 0.0, "end": 4.0, "film_start": 4.0,
                          "linked": True})
        self.assertEqual(sedit.validate_edit(doc), [])
        # A FREE strip is byte-identical to what every edit.json on disk
        # already carries — the flag is written only when it says something.
        free = sedit.normalise_edit(_edit([
            self._clip(audio={"start": 0.0, "end": 4.0, "film_start": 4.0})]))
        self.assertEqual(free["clips"][0]["audio"],
                         {"start": 0.0, "end": 4.0, "film_start": 4.0})

    def test_a_non_boolean_flag_is_refused(self):
        c = self._clip(audio={"start": 0.0, "end": 4.0, "film_start": 4.0,
                              "linked": "yes"})
        self.assertIn("clip_audio_linked",
                      [e["code"] for e in sedit.validate_edit(_edit([c]))])

    def test_the_cut_list_carries_a_coupled_window_too(self):
        # An assembler that ignored it would render the sound back under the
        # picture — the J-cut would exist in the timeline and not in the film.
        cuts = sedit.edit_to_cuts(_edit([self._coupled()]))
        self.assertEqual(cuts[0]["audio"],
                         {"start": 0.0, "end": 4.0, "film_start": 4.0})
        # ...and an ordinary clip still says nothing at all.
        plain = sedit.edit_to_cuts(_edit([self._clip()]))
        self.assertNotIn("audio", plain[0])


ENDCARD = Path("/Users/salo/pinokio/api/phosphene-dev.git/mlx_outputs/"
               "endcard_phosphene_46_overlay_A.png")
PULLOUT = Path("/Users/salo/pinokio/api/phosphene-dev.git/mlx_outputs/"
               "h3_pullout_ending_turbo_aria.mp4")


class TheSoundsEnvelope(unittest.TestCase):
    """"Fading and fade-out with keyframes should be very simple and intuitive
    for the sound as well."

    BOTH, FROM ONE MODEL. `fade_in`/`fade_out` are the simple case and the only
    thing the corner handles touch; `points` are the control case.
    `audio_gain_points()` folds them into ONE breakpoint curve, and the
    preview, the render and the export all read that — so the simple case
    never has to discover keyframes and a keyframed envelope never has to be
    re-expressed.
    """

    def _clip(self, **kw):
        return dict({"id": "a", "path": "/x/a.mp4", "proxy": None,
                     "start": 0.0, "end": 4.0, "film_start": 0.0,
                     "film_end": 4.0, "source": "human", "locked": False,
                     "duration": 10.0}, **kw)

    def test_a_flat_unity_curve_is_no_curve_at_all(self):
        # Saying "the volume is 1 the whole way" would put a filter in every
        # graph to express nothing.
        self.assertEqual(sedit.audio_gain_points({}, 4.0), [])
        self.assertEqual(sedit.audio_gain_at({}, 4.0, 2.0), 1.0)

    def test_the_simple_case_is_two_ramps(self):
        c = self._clip(afx={"fade_in": 1.0, "fade_out": 0.5})
        self.assertEqual(sedit.audio_gain_points(c, 4.0),
                         [[0.0, 0.0], [1.0, 1.0], [3.5, 1.0], [4.0, 0.0]])
        self.assertEqual(sedit.audio_gain_at(c, 4.0, 0.5), 0.5)

    def test_keyframes_are_the_same_curve_by_another_door(self):
        c = self._clip(afx={"points": [[1.0, 0.3], [2.0, 1.0]]})
        self.assertEqual(sedit.audio_gain_points(c, 4.0),
                         [[0.0, 0.3], [1.0, 0.3], [2.0, 1.0], [4.0, 1.0]])
        self.assertAlmostEqual(sedit.audio_gain_at(c, 4.0, 1.5), 0.65, places=3)

    def test_a_fade_and_a_keyframed_level_COMPOSE(self):
        # Not one overriding the other: the fade multiplies the point curve,
        # which is what makes "fade in to a 25% bed" a thing you can say.
        c = self._clip(afx={"fade_in": 1.0, "points": [[2.0, 0.25]]})
        curve = sedit.audio_gain_points(c, 4.0)
        self.assertEqual(curve[0], [0.0, 0.0])
        self.assertEqual(curve[1], [1.0, 0.25])
        self.assertEqual(curve[-1][1], 0.25)

    def test_the_clamp_and_the_bounds(self):
        c = self._clip(afx={"fade_in": 9.0, "fade_out": 9.0})
        e = sedit.audio_effects(c, 4.0)
        self.assertEqual([e["fade_in"], e["fade_out"]], [2.0, 2.0])
        loud = self._clip(afx={"points": [[1.0, 5.0], [2.0, -3.0]]})
        pts = sedit.audio_effects(loud, 4.0)["points"]
        self.assertEqual([p[1] for p in pts], [1.0, 0.0])

    def test_t_is_STRIP_relative_so_a_j_cut_does_not_drag_the_points(self):
        # The strip is 4 s of source wherever it sits on the film; a point at
        # 1.0 is one second into the SOUND, and sliding the J-cut must not
        # move it.
        early = self._clip(film_start=10.0, film_end=14.0,
                           audio={"start": 0.0, "end": 4.0, "film_start": 9.5},
                           afx={"points": [[1.0, 0.5]]})
        late = dict(early, audio={"start": 0.0, "end": 4.0, "film_start": 11.0})
        self.assertEqual(sedit.audio_gain_points(early, 4.0),
                         sedit.audio_gain_points(late, 4.0))

    def test_it_saves_reloads_and_reaches_the_cut_list(self):
        with tempfile.TemporaryDirectory() as d:
            bdir = Path(d)
            sedit.save_edit(bdir, _edit([self._clip(
                afx={"fade_in": 1.0, "points": [[2.0, 0.5]]})]))
            got = sedit.load_edit(bdir)
            self.assertEqual(got["clips"][0]["afx"],
                             {"fade_in": 1.0, "points": [[2.0, 0.5]]})
            self.assertEqual(sedit.validate_edit(got), [])
            cuts = sedit.edit_to_cuts(got)
            self.assertTrue(cuts[0]["gain"])
        # ...and neutral is absent.
        doc = sedit.normalise_edit(_edit([self._clip(afx={"fade_in": 0.0})]))
        self.assertNotIn("afx", doc["clips"][0])

    def test_a_nonsense_envelope_is_refused(self):
        codes = [e["code"] for e in sedit.validate_edit(
            _edit([self._clip(afx={"fade_in": -1})]))]
        self.assertIn("clip_afade_in_range", codes)
        codes = [e["code"] for e in sedit.validate_edit(
            _edit([self._clip(afx={"points": [[1.0]]})]))]
        self.assertIn("clip_afx_point", codes)

    # ---- output 2: the render -----------------------------------------
    def test_the_volume_expression_is_the_curve(self):
        curve = sedit.audio_gain_points(
            self._clip(afx={"fade_in": 1.0, "fade_out": 0.5}), 4.0)
        term = panel._sb_volume_term(curve)
        self.assertIn("eval=frame", term)
        self.assertIn("lt(t,1.000000)", term)
        self.assertIn("lt(t,3.500000)", term)
        # `eval=frame` or ffmpeg evaluates once at init and the envelope
        # becomes a constant.
        self.assertEqual(panel._sb_volume_term([]), "")
        self.assertEqual(panel._sb_volume_term([[0.0, 1.0]]), "")

    def test_an_unshaped_sound_builds_the_chain_it_always_did(self):
        info = {"has_audio": True, "duration": 10.0, "w": 1024, "h": 576,
                "sample_rate": 48000}
        seg = {"kind": "video", "input": 0, "info": dict(info),
               "window": {"start": 0.0, "end": 4.0}, "adjust": None,
               "duration": 4.0, "path": "/x/a.mp4"}
        plain, _ = panel._sb_film_filtergraph([], 1024, 576, 48000, "yuv420p",
                                              segments=[dict(seg)])
        self.assertNotIn("volume=", plain)
        self.assertNotIn("anull", plain)
        shaped, _ = panel._sb_film_filtergraph(
            [], 1024, 576, 48000, "yuv420p",
            segments=[dict(seg, gain=[[0.0, 0.0], [1.0, 1.0]])])
        self.assertIn("volume=volume=", shaped)

    # ---- output 3: the export ------------------------------------------
    def test_fcp7_gets_linear_level_keyframes(self):
        probe = lambda p: {"w": 1024, "h": 576, "duration": 10.0,
                           "has_audio": True}
        rows = sedit._nle_segments(
            [self._clip(afx={"fade_in": 1.0, "fade_out": 0.5})], probe=probe)
        self.assertEqual(rows[0]["gain"],
                         [[0.0, 0.0], [1.0, 1.0], [3.5, 1.0], [4.0, 0.0]])
        xml = sedit.fcp7_xml(rows, name="f", media={"/x/a.mp4": "a.mp4"},
                             width=1024, height=576, base="/tmp/p")
        self.assertIn("<name>Audio Levels</name>", xml)
        root = ET.fromstring(xml)
        lv = [e for e in root.iter("effect")
              if e.find("name") is not None
              and e.find("name").text == "Audio Levels"]
        keys = [(int(k.find("when").text), float(k.find("value").text))
                for k in lv[0].iter("keyframe")]
        # Linear, with 1.0 as unity — the same number the render uses, so
        # there is nothing to get wrong between them.
        self.assertEqual(keys, [(0, 0.0), (24, 1.0), (84, 1.0), (96, 0.0)])

    def test_after_effects_gets_the_same_curve_in_dB(self):
        probe = lambda p: {"w": 1024, "h": 576, "duration": 10.0,
                           "has_audio": True}
        rows = sedit._nle_segments(
            [self._clip(afx={"fade_in": 1.0})], probe=probe)
        jsx = sedit.ae_jsx(rows, name="f", media={"/x/a.mp4": "a.mp4"},
                           width=1024, height=576)
        self.assertIn("ADBE Audio Levels", jsx)
        # AE's unit is dB, so the conversion happens at that ONE seam:
        # unity is 0 dB and silence is AE's own -96 floor, because log(0) is
        # not a number.
        self.assertIn("au.setValueAtTime(0.000000, [-96.000, -96.000]);", jsx)
        self.assertIn("au.setValueAtTime(1.000000, [0.000, 0.000]);", jsx)

    def test_the_three_outputs_agree_on_the_breakpoints(self):
        probe = lambda p: {"w": 1024, "h": 576, "duration": 10.0,
                           "has_audio": True}
        c = self._clip(afx={"fade_in": 1.0, "fade_out": 0.5})
        curve = sedit.audio_gain_points(c, 4.0)
        rows = sedit._nle_segments([c], probe=probe)
        xml = sedit.fcp7_xml(rows, name="f", media={"/x/a.mp4": "a.mp4"},
                             width=1024, height=576, base="/tmp/p")
        root = ET.fromstring(xml)
        lv = [e for e in root.iter("effect")
              if e.find("name") is not None
              and e.find("name").text == "Audio Levels"][0]
        fcp = [round(int(k.find("when").text) / 24.0, 3) for k in lv.iter("keyframe")]
        jsx = sedit.ae_jsx(rows, name="f", media={"/x/a.mp4": "a.mp4"},
                           width=1024, height=576)
        ae = [round(float(m), 3) for m in re.findall(
            r"au\.setValueAtTime\(([\d.]+), \[", jsx)]
        self.assertEqual(fcp, [round(t, 3) for t, _ in curve])
        self.assertEqual(ae, fcp)
        term = panel._sb_volume_term(curve)
        for t, _ in curve[1:]:
            self.assertIn(f"lt(t,{t:.6f})", term)


class TheOverlayLane(unittest.TestCase):
    """A SECOND video track, above the picture — and the real endcard.

    Tested with his actual asset: 1536x832 RGBA authored at 2x the frame
    aspect of a 768x416 pull-out, so the default full-frame fit DOWNSCALES it.
    A scale that drops the alpha channel delivers a black rectangle where the
    transparency was, and that is the failure this lane exists to avoid.
    """

    def _board(self, **ovkw):
        clip = {"id": "c1", "path": str(PULLOUT), "proxy": None, "start": 0.0,
                "end": 6.0, "film_start": 0.0, "film_end": 6.0,
                "source": "human", "locked": False, "duration": 10.12}
        ov = dict({"id": "o1", "kind": "still", "path": str(ENDCARD),
                   "start": 0.0, "end": 3.0, "film_start": 3.0,
                   "film_end": 6.0, "source": "human", "locked": False},
                  **ovkw)
        doc = _edit([clip])
        doc["overlays"] = [ov]
        return doc

    def test_the_asset_is_what_it_claims_to_be(self):
        # The doctrine is to test with the real thing, so the test says what
        # the real thing is: if the card stops being RGBA this fails here
        # rather than three assertions later.
        if not ENDCARD.is_file():
            raise unittest.SkipTest("endcard asset not present")
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=width,height,pix_fmt", "-of", "csv=p=0", str(ENDCARD)],
            capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, "1536,832,rgba")

    def test_an_absent_lane_is_an_absent_key(self):
        doc = sedit.normalise_edit(_edit([]))
        self.assertNotIn("overlays", doc)
        self.assertEqual(sedit.overlay_items(doc), [])

    def test_a_still_is_its_slot(self):
        doc = sedit.normalise_edit(self._board())
        o = doc["overlays"][0]
        self.assertEqual([o["start"], o["end"]], [0.0, 3.0])
        self.assertIsNone(o["duration"])
        self.assertEqual(sedit.validate_edit(doc), [])

    def test_an_overlay_over_a_clip_is_NOT_an_overlap(self):
        # The picture lane may not overlap itself; a card sitting ON a picture
        # is the feature, which is why the lane is its own list.
        self.assertEqual(sedit.validate_edit(sedit.normalise_edit(self._board())), [])

    def test_but_two_cards_at_once_are(self):
        doc = self._board()
        doc["overlays"].append(dict(doc["overlays"][0], id="o2",
                                    film_start=4.0, film_end=7.0))
        self.assertIn("overlays_overlap",
                      [e["code"] for e in sedit.validate_edit(doc)])

    def test_the_lane_saves_and_reloads(self):
        with tempfile.TemporaryDirectory() as d:
            bdir = Path(d)
            sedit.save_edit(bdir, self._board(fx={"fade_in": 1.0}))
            got = sedit.load_edit(bdir)
            self.assertEqual(len(got["overlays"]), 1)
            self.assertEqual(got["overlays"][0]["fx"], {"fade_in": 1.0})
            self.assertEqual(sedit.validate_edit(got), [])

    # ---- the same accessor as a clip: the foundation paying off --------
    def test_an_overlay_gets_its_fades_from_clip_effects(self):
        o = sedit.normalise_edit(self._board(fx={"fade_in": 1.0}))["overlays"][0]
        self.assertEqual(sedit.clip_effects(o)["fade_in"], 1.0)
        # ...and the SAME clamp: a fade cannot outrun the card.
        wide = sedit.normalise_edit(
            self._board(fx={"fade_in": 9.0}))["overlays"][0]
        self.assertEqual(sedit.clip_effects(wide)["fade_in"], 3.0)

    # ---- output 2: the render -----------------------------------------
    def test_the_graph_keeps_the_alpha_on_BOTH_sides_of_the_scale(self):
        ov = [{"kind": "still", "path": str(ENDCARD), "film_start": 3.0,
               "film_end": 6.0, "fx": {"fade_in": 1.0}}]
        g, _ = panel._sb_film_filtergraph(
            [], 768, 416, 48000, "yuv420p",
            segments=[{"kind": "video", "input": 0,
                       "info": {"has_audio": True, "duration": 10.0, "w": 768,
                                "h": 416, "sample_rate": 48000},
                       "window": {"start": 0.0, "end": 6.0}, "adjust": None,
                       "duration": 6.0, "path": str(PULLOUT)}],
            overlays=ov, overlay_base=1)
        chain = [c for c in g.split(";") if c.startswith("[1:v]")][0]
        # Once so what enters the scaler carries alpha, once because a scaler
        # is free to pick an output format that does not.
        self.assertEqual(chain.count("format=rgba"), 2)
        self.assertLess(chain.index("format=rgba"), chain.index("scale="))
        self.assertLess(chain.index("scale="), chain.rindex("format=rgba"))
        # A fade on the overlay lane ramps ALPHA, not toward black — fading to
        # black would paint a black card over the picture.
        self.assertIn("fade=t=in:st=0:d=1.000000:alpha=1", chain)
        self.assertIn("setpts=PTS+3.000000/TB", chain)
        ovl = [c for c in g.split(";") if "overlay=" in c][0]
        self.assertIn("enable='between(t,3.000000,6.000000)'", ovl)
        self.assertIn("repeatlast=0", ovl)

    def test_a_card_that_outlives_the_picture_composites_over_black(self):
        g, _ = panel._sb_film_filtergraph(
            [], 768, 416, 48000, "yuv420p",
            segments=[{"kind": "video", "input": 0,
                       "info": {"has_audio": True, "duration": 10.0, "w": 768,
                                "h": 416, "sample_rate": 48000},
                       "window": {"start": 0.0, "end": 4.0}, "adjust": None,
                       "duration": 4.0, "path": str(PULLOUT)}],
            overlays=[{"kind": "still", "path": str(ENDCARD),
                       "film_start": 3.0, "film_end": 7.0}],
            overlay_base=1)
        self.assertIn("tpad=stop_mode=add:stop_duration=3.000000:color=black", g)

    def test_no_overlay_builds_the_graph_it_always_did(self):
        seg = {"kind": "video", "input": 0,
               "info": {"has_audio": True, "duration": 10.0, "w": 768,
                        "h": 416, "sample_rate": 48000},
               "window": {"start": 0.0, "end": 4.0}, "adjust": None,
               "duration": 4.0, "path": str(PULLOUT)}
        g, lbl = panel._sb_film_filtergraph([], 768, 416, 48000, "yuv420p",
                                            segments=[seg])
        self.assertNotIn("overlay=", g)
        self.assertNotIn("tpad", g)

    # ---- output 3: the export -----------------------------------------
    def test_fcp7_puts_the_card_on_V2_above_the_picture(self):
        probe = lambda p: {"w": 768, "h": 416, "duration": 10.12,
                           "has_audio": True}
        doc = sedit.normalise_edit(self._board(fx={"fade_in": 1.0}))
        rows = sedit._nle_segments(doc["clips"], probe=probe)
        media = {str(PULLOUT): "pullout.mp4", str(ENDCARD): "card.png"}
        xml = sedit.fcp7_xml(rows, name="c", media=media, width=768,
                             height=416, base="/tmp/p",
                             overlays=doc["overlays"])
        root = ET.fromstring(xml)
        seq = root if root.tag == "sequence" else root.find("sequence")
        tracks = seq.find("./media/video").findall("track")
        self.assertEqual(len(tracks), 2)          # V1 picture, V2 overlay
        self.assertEqual([ci.find("name").text
                          for ci in tracks[1].findall("clipitem")],
                         ["endcard_phosphene_46_overlay_A"])
        keys = [(int(k.find("when").text), float(k.find("value").text))
                for k in tracks[1].iter("keyframe")]
        self.assertEqual(keys[:2], [(0, 0.0), (24, 100.0)])   # 1 s at 24 fps

    def test_after_effects_puts_the_card_on_top(self):
        probe = lambda p: {"w": 768, "h": 416, "duration": 10.12,
                           "has_audio": True}
        doc = sedit.normalise_edit(self._board(fx={"fade_in": 1.0}))
        rows = sedit._nle_segments(doc["clips"], probe=probe)
        media = {str(PULLOUT): "pullout.mp4", str(ENDCARD): "card.png"}
        jsx = sedit.ae_jsx(rows, name="c", media=media, width=768, height=416,
                           overlays=doc["overlays"])
        # `comp.layers.add` inserts at index 1, so whatever is added LAST sits
        # highest: the card must come after every picture layer.
        self.assertLess(jsx.index("pullout.mp4"), jsx.index("card.png"))
        self.assertIn("op.setValueAtTime(3.000000, 0);", jsx)
        self.assertIn("op.setValueAtTime(4.000000, 100);", jsx)

    def test_the_three_outputs_agree_on_the_overlays_fade(self):
        probe = lambda p: {"w": 768, "h": 416, "duration": 10.12,
                           "has_audio": True}
        doc = sedit.normalise_edit(self._board(fx={"fade_in": 1.0}))
        o = doc["overlays"][0]
        media = {str(PULLOUT): "pullout.mp4", str(ENDCARD): "card.png"}
        rows = sedit._nle_segments(doc["clips"], probe=probe)
        xml = sedit.fcp7_xml(rows, name="c", media=media, width=768,
                             height=416, base="/tmp/p", overlays=[o])
        root = ET.fromstring(xml)
        seq = root if root.tag == "sequence" else root.find("sequence")
        v2 = seq.find("./media/video").findall("track")[1]
        fcp_in = [int(k.find("when").text) / 24.0 for k in v2.iter("keyframe")][:2]
        jsx = sedit.ae_jsx(rows, name="c", media=media, width=768, height=416,
                           overlays=[o])
        ae = [float(m) for m in re.findall(
            r"op\.setValueAtTime\(([\d.]+), \d+\);", jsx)][:2]
        # FCP7 counts from the clipitem's own head; AE from the film clock.
        self.assertEqual(round(fcp_in[1] - fcp_in[0], 3),
                         round(ae[1] - ae[0], 3))
        term = panel._sb_fade_term(o.get("fx"), 3.0, alpha=True)
        self.assertIn("d=1.000000", term)      # the same ramp length, again


class TheOverlayRendersWithItsAlpha(unittest.TestCase):
    """The failure mode, tested by actually rendering it.

    A card that arrives as a black rectangle instead of transparency is the
    thing this lane must never do, and no amount of reading the filter string
    proves it did not. So: composite the real endcard over a KNOWN solid base
    and count the pixels that still show the base through it.
    """

    def test_the_real_endcard_downscales_with_its_transparency_intact(self):
        if not ENDCARD.is_file() or not shutil.which("ffmpeg"):
            raise unittest.SkipTest("endcard or ffmpeg not present")
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "frame.png"
            # The EXACT chain the panel builds, over solid red.
            chain = ("[1:v]format=rgba,scale=768:416:flags=lanczos,"
                     "format=rgba,setsar=1,setpts=PTS+0/TB[ov0];"
                     "[0:v][ov0]overlay=0:0:eof_action=pass:repeatlast=0:"
                     "enable='between(t,0,2)'[vout]")
            subprocess.run(
                ["ffmpeg", "-v", "error", "-f", "lavfi",
                 "-i", "color=c=red:s=768x416:r=24:d=2",
                 "-loop", "1", "-framerate", "24", "-t", "2", "-i", str(ENDCARD),
                 "-filter_complex", chain, "-map", "[vout]", "-frames:v", "1",
                 "-y", str(out)], check=True, capture_output=True)
            raw = subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(out), "-f", "rawvideo",
                 "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
            a = np.frombuffer(raw, np.uint8).reshape(416, 768, 3).astype(int)
            through = ((a[:, :, 0] > 200) & (a[:, :, 1] < 60)
                       & (a[:, :, 2] < 60)).mean()
            black = (a.sum(axis=2) < 24).mean()
            # If alpha were dropped the card's transparent region would render
            # BLACK and cover the base entirely.
            self.assertGreater(through, 0.5,
                               "the base is not visible through the card")
            self.assertLess(black, 0.05, "the card arrived as a black box")


class AReadMayNotDisarmTheNet(EditorCase):
    """The owner lost unsaved work because somebody else OPENED his film.

    A page LOAD claimed the board; the tab he was cutting in was answered
    `stale_session` on its next snapshot and stopped writing for seven hours.
    Two rules come out of that and both are asserted here: a read claims
    nothing, and the lane refuses nobody.
    """

    def _doc(self, end=4.0):
        return _edit([dict(_clip("/x/a.mp4", 0.0, end, 0.0), id="c1")])

    def setUp(self):
        super().setUp()
        # A document on disk, so the GET reads rather than auto-cutting a
        # board whose clips are stub bytes ffprobe cannot read.
        sedit.save_edit(self.bdir, self._doc(), origin="human")

    def test_a_read_claims_nothing(self):
        h = FakeHandler().get("/storyboard/edit?id=sb_t&session=ssREADER")
        self.assertTrue(h.payload.get("ok", True))
        self.assertEqual(sedit.current_session(self.bdir)["token"], "",
                         "a page load took the claim")

    def test_a_write_claims_the_board(self):
        sedit.write_backup(self.bdir, self._doc(), session="ssEDITOR")
        self.assertEqual(sedit.current_session(self.bdir)["token"], "ssEDITOR")

    def test_a_passive_load_does_not_stop_the_editing_tabs_snapshots(self):
        # The exact sequence that cost him the afternoon.
        sedit.write_backup(self.bdir, self._doc(4.0), session="ssEDITOR")
        FakeHandler().get("/storyboard/edit?id=sb_t&session=ssPASSIVEVIEWER")
        before = len(sedit.list_history(self.bdir))
        sedit.write_backup(self.bdir, self._doc(5.0), session="ssEDITOR")
        sedit.write_backup(self.bdir, self._doc(6.0), session="ssEDITOR")
        self.assertEqual(len(sedit.list_history(self.bdir)), before + 2,
                         "the snapshot lane went quiet after a passive load")

    def test_even_a_genuinely_older_tab_is_never_refused(self):
        # The lane is ONE FILE PER SNAPSHOT and never overwrites, so the
        # failure the token was introduced for cannot recur — and a snapshot
        # from any tab can only ever ADD a way back. Refusing one removes one.
        sedit.write_backup(self.bdir, self._doc(4.0), session="ssNEW")
        n = len(sedit.list_history(self.bdir))
        sedit.write_backup(self.bdir, self._doc(9.0), session="ssOLD")
        self.assertEqual(len(sedit.list_history(self.bdir)), n + 1)

    def test_the_claim_follows_the_last_EDITOR_not_the_last_loader(self):
        sedit.write_backup(self.bdir, self._doc(4.0), session="ssA")
        FakeHandler().get("/storyboard/edit?id=sb_t&session=ssB")
        self.assertEqual(sedit.current_session(self.bdir)["token"], "ssA")
        sedit.write_backup(self.bdir, self._doc(5.0), session="ssB")
        self.assertEqual(sedit.current_session(self.bdir)["token"], "ssB")

    def test_the_route_reports_who_holds_the_claim_so_a_tab_can_say_so(self):
        sedit.write_backup(self.bdir, self._doc(), session="ssA")
        h = FakeHandler().post("edit/backup", body=json.dumps(
            {"id": "sb_t", "session": "ssB", "draft": "",
             "edit": self._doc(7.0)}))
        self.assertTrue(h.payload["ok"])
        self.assertEqual(h.payload["session"]["token"], "ssB")
        # ...and never with a refusal.
        self.assertNotIn("stale_session", h.payload)


class AnIdenticalSnapshotIsNotASnapshot(EditorCase):
    """Ten files in fifteen seconds, same revision, same clips, same digest —
    half of a twenty-deep net spent on one arrangement, and half the DISTINCT
    history evicted to hold it."""

    def _doc(self, end=4.0):
        return _edit([dict(_clip("/x/a.mp4", 0.0, end, 0.0), id="c1")])

    def test_a_burst_of_identical_writes_leaves_one_file(self):
        for _ in range(10):
            sedit.write_backup(self.bdir, self._doc(4.0), session="ssA")
        self.assertEqual(len(sedit.list_history(self.bdir)), 1)

    def test_a_real_change_still_lands(self):
        for end in (4.0, 5.0, 6.0):
            sedit.write_backup(self.bdir, self._doc(end), session="ssA")
        self.assertEqual(len(sedit.list_history(self.bdir)), 3)

    def test_the_dedupe_uses_the_SAME_fingerprint_the_offer_compares_on(self):
        # A derived field the user has never heard of may not be the reason a
        # snapshot is written OR the reason they are asked a question.
        a = self._doc(4.0)
        sedit.write_backup(self.bdir, a, session="ssA")
        b = dict(a)
        b["clips"] = [dict(a["clips"][0], proxy="rebuilt.mp4")]
        sedit.write_backup(self.bdir, b, session="ssA")
        self.assertEqual(len(sedit.list_history(self.bdir)), 1)


class AHoleShorterThanAFrame(unittest.TestCase):
    """"A black frame that flashes for a microsecond... I tried to drag them
    close and whatever."

    The second half is the defect. Measured on the sequence that produced the
    report, at 24 fps: three holes of 20.94 ms, 15.84 ms and 4.00 ms — 0.503,
    0.380 and 0.096 of a frame. Too small to see at any zoom, too small for a
    pixel to address, and the stage paints black wherever no clip is playing.
    """

    FRAME = 1.0 / 24.0

    def _doc(self, *gaps, **kw):
        """A film of 4-second clips separated by the gaps given, in seconds."""
        clips, at = [], 0.0
        for i, g in enumerate((0.0,) + gaps):
            at += g
            clips.append(dict(_clip(f"/x/{i}.mp4", 0.0, 4.0, at), id=f"c{i}",
                              film_end=round(at + 4.0, 6)))
            at += 4.0
        return _edit(clips, **kw)

    def test_his_three_holes_all_close(self):
        doc = self._doc(0.02094, 0.01584, 0.004)
        closed = sedit.heal_subframe_gaps(doc)
        self.assertEqual(len(closed), 3)
        self.assertEqual([round(c["frames"], 3) for c in closed],
                         [0.503, 0.380, 0.096])
        self.assertEqual([(c["film_start"], c["film_end"]) for c in doc["clips"]],
                         [(0.0, 4.0), (4.0, 8.0), (8.0, 12.0), (12.0, 16.0)])
        self.assertEqual(sedit.edit_gaps(doc), [])

    def test_the_heal_happens_on_READ_so_an_old_film_fixes_itself_on_open(self):
        healed = sedit.migrate_edit(self._doc(0.02094))
        self.assertEqual(healed["clips"][1]["film_start"], 4.0)
        self.assertEqual(len(healed["healed_subframe_gaps"]), 1)

    def test_the_read_does_not_mutate_the_document_it_was_given(self):
        # `migrate_edit` is a read-path upgrade, and the backup lane compares
        # RAW json on both sides — a read that edited its input in place would
        # make the saved file and the snapshot differ over a repair neither of
        # them contains, which is the interrogation the save model exists to
        # stop.
        raw = self._doc(0.02094)
        sedit.migrate_edit(raw)
        self.assertEqual(raw["clips"][1]["film_start"], 4.02094)

    def test_it_is_idempotent_and_the_record_does_not_survive_a_clean_read(self):
        # "Fixes itself on open" and "drifts a little every open" look
        # identical from one screenshot.
        once = sedit.migrate_edit(self._doc(0.02094, 0.01584, 0.004))
        twice = sedit.migrate_edit(once)
        self.assertEqual([(c["film_start"], c["film_end"]) for c in twice["clips"]],
                         [(c["film_start"], c["film_end"]) for c in once["clips"]])
        # ...and the second read announces nothing, because it closed nothing.
        self.assertNotIn("healed_subframe_gaps", twice)

    def test_a_gap_somebody_can_SEE_is_never_touched(self):
        # The false positive to avoid: a black slug the user placed on purpose.
        for gap in (self.FRAME, 0.5, 6.0):
            with self.subTest(gap=gap):
                doc = self._doc(gap)
                self.assertEqual(sedit.heal_subframe_gaps(doc), [])
                self.assertAlmostEqual(doc["clips"][1]["film_start"],
                                       4.0 + gap, places=6)
        # ...and it is not snapped to the frame grid either. 1.8 s is 43.2
        # frames; moving it 8 ms to sit on 43 would rewrite a number the user
        # chose to buy a property nothing reads.
        doc = self._doc(1.8)
        sedit.heal_subframe_gaps(doc)
        self.assertEqual(doc["clips"][1]["film_start"], 5.8)

    def test_an_unlinked_strip_keeps_its_offset_through_a_heal(self):
        doc = self._doc(0.02094)
        doc["clips"][1]["audio"] = {"split": True, "start": 0.5, "end": 4.5,
                                    "film_start": 3.52094}
        drift = sedit.clip_audio_drift(doc["clips"][1])
        sedit.heal_subframe_gaps(doc)
        self.assertAlmostEqual(sedit.clip_audio_drift(doc["clips"][1]),
                               drift, places=6)
        # The strip moved by the SAME delta as its picture, not by nothing.
        self.assertAlmostEqual(doc["clips"][1]["audio"]["film_start"], 3.5,
                               places=6)

    def test_a_locked_clip_is_an_anchor_and_does_not_move(self):
        doc = self._doc(0.02094, 0.004)
        doc["clips"][1]["locked"] = True
        sedit.heal_subframe_gaps(doc)
        # The pin holds: honouring it by breaking it is not honouring it.
        self.assertEqual(doc["clips"][1]["film_start"], 4.02094)
        # ...and the hole after the anchor is measured from where it actually
        # is, so nothing downstream inherits a shift the anchor did not take.
        self.assertEqual(doc["clips"][2]["film_start"], 8.02094)

    def test_the_hole_counter_stops_lying(self):
        doc = self._doc(0.02094, 0.01584, 0.004)
        # WHAT THE HEADER SAID: one hole, because the threshold was the literal
        # 1/48 and two of the three were under it.
        self.assertEqual(len(sedit.edit_gaps(doc, tolerance=1.0 / 48.0)), 1)
        self.assertEqual(len(sedit.edit_gaps(doc, tolerance=1e-9)), 3)
        # WHAT IT SAYS NOW: nothing, because there is nothing left to report.
        self.assertEqual(sedit.edit_gaps(sedit.migrate_edit(doc)), [])

    def test_the_threshold_follows_the_sequences_rate(self):
        # 1/48 is half a frame at 24 fps and the wrong number at any other.
        doc = self._doc(0.03, fps=12)                       # 0.36 of a frame
        self.assertEqual(sedit.frame_seconds(doc), 1.0 / 12.0)
        self.assertEqual(sedit.edit_gaps(doc), [])
        self.assertEqual(len(sedit.heal_subframe_gaps(doc)), 1)

    def test_the_render_and_the_timeline_now_agree_on_the_length(self):
        """Where the black frame came from, and where it did not.

        The assembler consumes `start`/`end` per clip and CONCATENATES, so a
        hole never reached ffmpeg at all — the rendered film was already the
        healed length. The PREVIEW is the half that painted black in it. The
        two therefore disagreed by exactly the hole, silently, and healing is
        what makes the number on the header the number that comes out.
        """
        doc = self._doc(0.02094, 0.01584, 0.004)
        rendered = sum(c["end"] - c["start"] for c in sedit.edit_to_cuts(doc))
        self.assertAlmostEqual(sedit.edit_duration(doc) - rendered, 0.04078,
                               places=5)
        healed = sedit.migrate_edit(doc)
        self.assertAlmostEqual(sedit.edit_duration(healed), rendered, places=6)


# =============================================================================
# An overlay that arrives on a black plate
# =============================================================================
# "If the picture comes in a format that doesn't work, automatically make it
# work." The reported failure: a card generated elsewhere HAS an alpha channel,
# so it looks transparent, but the artwork sits on an opaque black backdrop —
# the alpha over the neon burst is a FILLED DISC, so the black between the rays
# is declared opaque and the lane composited it over the sky.
#
# The fixtures are BUILT HERE rather than read off this machine, so the gate
# runs on a fresh install and so the population the detector must refuse is
# visible in the test rather than implied by it. The real reported card is
# asserted too, when it happens to be present.
PLATE_CARD = Path("/Users/salo/pinokio/api/phosphene-dev.git/panel_uploads/"
                  "library/manual/20260820/uploads/"
                  "1787241298782_ChatGPT_Image_Aug_20_2026_06_54_48_PM.png")


def _burst(h, w):
    """Neon dashes on a smooth dark backdrop, inside a disc. Returns
    (rgb, disc, ink) — the same picture, mattted two different ways below."""
    cx, cy, r = w * 0.5, h * 0.5, min(w, h) * 0.44
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    rad = np.hypot(xx - cx, yy - cy)
    ang = np.arctan2(yy - cy, xx - cx)
    disc = rad <= r
    ink = disc & (np.cos(ang * 28.0) > 0.35) & (rad > r * 0.30) & (rad < r * 0.92)
    rgb = np.zeros((h, w, 3), np.float32)
    back = np.clip(18.0 - rad / r * 10.0, 0.0, 255.0)     # a smooth backdrop
    rgb[disc] = np.stack([back * 0.6, back * 0.3, back], -1)[disc]
    hot = np.stack([np.full((h, w), 255.0),
                    60.0 + 120.0 * np.cos(ang * 5.0),
                    np.full((h, w), 235.0)], -1)          # ...and saturated ink
    rgb[ink] = hot[ink]
    return rgb, disc, ink


def _rgba(path, rgb, alpha):
    from PIL import Image                                          # noqa: PLC0415
    out = np.concatenate([rgb, alpha[..., None]], -1)
    Image.fromarray(np.rint(out).astype(np.uint8), "RGBA").save(path)


def _photoish(h, w, seed=7, scale=1.0):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    lum = np.clip(((90 + 70 * np.sin(xx / 40.0) * np.cos(yy / 31.0)
                    + 50 * np.sin((xx + yy) / 17.0))
                   + rng.normal(0.0, 9.0, (h, w))) * scale, 0, 255)
    return np.stack([lum, lum * 0.92, lum * 0.86], -1).astype(np.float32)


def _ellipse(h, w):
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    inside = (((xx - w / 2) / (w * 0.34)) ** 2
              + ((yy - h / 2) / (h * 0.45)) ** 2) <= 1.0
    return np.where(inside, 255.0, 0.0)


class AnOverlayThatArrivesOnABlackPlate(unittest.TestCase):
    """The detector, the key, and the identity the key stands on."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    # ---- the fixtures, as files -------------------------------------------
    def plate(self, name="plate.png", h=440, w=800):
        """The reported shape: a FILLED matte over art drawn on black."""
        rgb, disc, _ = _burst(h, w)
        p = self.d / name
        _rgba(p, rgb, np.where(disc, 255.0, 0.0))
        return p

    def honest(self, name="honest.png", h=440, w=800):
        """The same art with a matte that hugs the ink."""
        rgb, _, ink = _burst(h, w)
        rgb[~ink] = 0.0
        p = self.d / name
        _rgba(p, rgb, np.where(ink, 255.0, 0.0))
        return p

    def photo(self, name="photo.jpg"):
        from PIL import Image                                      # noqa: PLC0415
        p = self.d / name
        Image.fromarray(np.rint(_photoish(440, 800)).astype(np.uint8),
                        "RGB").save(p)
        return p

    def matted_photo(self, name="cutout.png", scale=1.0):
        p = self.d / name
        _rgba(p, _photoish(440, 800, scale=scale), _ellipse(440, 800))
        return p

    def black_ink(self, name="ink.png"):
        a = np.zeros((440, 800), np.float32)
        for i in range(6):
            a[120:320, 60 + i * 125: 150 + i * 125] = 255.0
        p = self.d / name
        _rgba(p, np.zeros((440, 800, 3), np.float32), a)
        return p

    # ---- detection --------------------------------------------------------
    def test_a_filled_matte_over_art_on_black_is_detected(self):
        r = panel._sbe_plate_report(self.plate())
        self.assertTrue(r["plate"], r)
        # The clauses, by name, so a threshold that drifts says which one.
        self.assertGreaterEqual(r["clear_share"], panel.SBE_PLATE_CLEAR_MIN)
        self.assertGreaterEqual(r["opaque_share"], panel.SBE_PLATE_OPAQUE_MIN)
        self.assertGreaterEqual(r["dark_share"], panel.SBE_PLATE_DARK_SHARE_MIN)
        self.assertGreaterEqual(r["hot_share"], panel.SBE_PLATE_HOT_SHARE_MIN)
        self.assertLessEqual(r["flat"], panel.SBE_PLATE_FLAT_MAX)
        self.assertGreaterEqual(r["ink_kept"], panel.SBE_PLATE_INK_MIN)

    def test_a_photograph_can_never_be_reached_by_any_of_the_maths(self):
        # THE GUARANTEE THAT MATTERS. A file with no alpha channel never
        # claimed transparency, so it is refused before a single pixel is
        # weighed — no threshold can ever let a plain photo through.
        r = panel._sbe_plate_report(self.photo())
        self.assertFalse(r["plate"])
        self.assertEqual(r["why"], "no alpha channel")

    def test_a_photograph_matted_into_an_overlay_is_left_alone(self):
        for scale, tag in ((1.0, "as shot"), (0.22, "darkened")):
            with self.subTest(tag):
                r = panel._sbe_plate_report(
                    self.matted_photo(f"cut_{tag.replace(' ', '_')}.png", scale))
                self.assertFalse(r["plate"], r)

    def test_a_card_with_a_matte_that_hugs_its_ink_is_left_alone(self):
        r = panel._sbe_plate_report(self.honest())
        self.assertFalse(r["plate"], r)

    def test_black_artwork_on_transparency_is_left_alone(self):
        r = panel._sbe_plate_report(self.black_ink())
        self.assertFalse(r["plate"], r)

    def test_a_design_that_keying_would_erase_is_refused_by_the_safety_net(self):
        # A CLAUSE THAT DOES NOT CARE WHICH POPULATION THE FILE IS IN. This
        # fixture is a black plate with saturated ink on it — it passes the
        # transparency, backdrop, flatness and saturation clauses — but the
        # ink is a sixth of the plate, so keying would hand back a hole where
        # a card was. `ink_kept` is the only thing that refuses it.
        h, w = 440, 800
        rgb = np.zeros((h, w, 3), np.float32)
        a = np.zeros((h, w), np.float32)
        a[40:400, 40:760] = 255.0                      # the plate
        rgb[40:400, 40:760] = np.array([6.0, 3.0, 9.0], np.float32)
        rgb[170:270, 100:700] = np.array([255.0, 90.0, 240.0], np.float32)
        p = self.d / "tiny_logo.png"
        _rgba(p, rgb, a)
        r = panel._sbe_plate_report(p)
        self.assertGreaterEqual(r["hot_share"], panel.SBE_PLATE_HOT_SHARE_MIN)
        self.assertLessEqual(r["flat"], panel.SBE_PLATE_FLAT_MAX)
        self.assertFalse(r["plate"])
        self.assertEqual(r["why"], "keying it would erase the picture")
        self.assertLess(r["ink_kept"], panel.SBE_PLATE_INK_MIN)

    def test_the_reported_card_itself(self):
        if not PLATE_CARD.is_file():
            raise unittest.SkipTest("the reported card is not on this machine")
        r = panel._sbe_plate_report(PLATE_CARD)
        self.assertTrue(r["plate"], r)
        # The numbers in the comment block above `_sbe_plate_report`.
        self.assertAlmostEqual(r["clear_share"], 0.433, places=2)
        self.assertAlmostEqual(r["opaque_share"], 0.412, places=2)
        self.assertAlmostEqual(r["hot_share"], 0.272, places=2)

    def test_a_genuinely_transparent_card_on_this_machine_is_left_alone(self):
        if not ENDCARD.is_file():
            raise unittest.SkipTest("endcard not present")
        self.assertFalse(panel._sbe_plate_report(ENDCARD)["plate"])

    # ---- keying -----------------------------------------------------------
    def test_the_original_is_never_modified(self):
        import hashlib                                             # noqa: PLC0415
        p = self.plate()
        before = hashlib.sha256(p.read_bytes()).hexdigest()
        made = panel._sbe_key_plate(p)
        self.assertTrue(made["ok"])
        self.assertNotEqual(Path(made["path"]), p)
        self.assertEqual(Path(made["path"]).parent, p.parent)
        self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(), before)

    def test_the_derivative_is_the_original_over_black(self):
        """The identity the whole transform stands on.

        `a'·C' = a·(m/255) · C·(255/m) = a·C`, so compositing the derivative
        over black reproduces the source to within 8-bit rounding — and
        everywhere the backdrop was, the background now shows through instead.
        """
        from PIL import Image                                      # noqa: PLC0415
        p = self.plate()
        k = Path(panel._sbe_key_plate(p)["path"])
        o = np.asarray(Image.open(p).convert("RGBA"), np.float32)
        d = np.asarray(Image.open(k).convert("RGBA"), np.float32)
        over_black = lambda x: x[..., :3] * (x[..., 3:] / 255.0)   # noqa: E731
        self.assertLess(np.abs(over_black(o) - over_black(d)).max(), 1.5)
        # ...and the plate is gone: nothing is opaque-and-black any more.
        # Measured under a NEUTRAL name so it is the PIXELS that refuse a
        # second key, not the `.keyed.png` shortcut.
        neutral = self.d / "second_pass.png"
        neutral.write_bytes(k.read_bytes())
        r = panel._sbe_plate_report(neutral)
        self.assertEqual(r["dark_share"], 0.0)
        self.assertFalse(r["plate"])

    def test_keying_is_idempotent(self):
        p = self.plate()
        k = Path(panel._sbe_key_plate(p)["path"])
        self.assertFalse(panel._sbe_plate_report(k)["plate"])
        self.assertEqual(panel._sbe_plate_report(k)["why"], "already keyed")
        # ...and asking twice reuses the file rather than making a second one.
        self.assertTrue(panel._sbe_key_plate(p)["reused"])
        self.assertEqual(len(list(self.d.glob("*.png"))), 2)

    def test_it_composites_with_NO_BLACK_over_a_known_background(self):
        """Measured in pixels, through the chain the panel actually builds.

        Reading the filter string proves nothing — the plated card and the
        keyed one produce the same filtergraph. So: render both over the same
        solid base and count.
        """
        if not shutil.which("ffmpeg"):
            raise unittest.SkipTest("ffmpeg not present")
        p = self.plate()
        k = Path(panel._sbe_key_plate(p)["path"])

        def black_share(card: Path) -> float:
            out = self.d / (card.stem + "_frame.png")
            chain = ("[1:v]format=rgba,scale=768:416:flags=lanczos,"
                     "format=rgba,setsar=1,setpts=PTS+0/TB[ov0];"
                     "[0:v][ov0]overlay=0:0:eof_action=pass:repeatlast=0:"
                     "enable='between(t,0,2)'[vout]")
            subprocess.run(
                ["ffmpeg", "-v", "error", "-f", "lavfi",
                 "-i", "color=c=0x5A96DC:s=768x416:r=24:d=2",
                 "-loop", "1", "-framerate", "24", "-t", "2", "-i", str(card),
                 "-filter_complex", chain, "-map", "[vout]", "-frames:v", "1",
                 "-y", str(out)], check=True, capture_output=True)
            raw = subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(out), "-f", "rawvideo",
                 "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
            a = np.frombuffer(raw, np.uint8).reshape(416, 768, 3).astype(int)
            return float((a.max(axis=2) < 40).mean())

        self.assertGreater(black_share(p), 0.01,
                           "the fixture is not reproducing the reported bug")
        self.assertEqual(black_share(k), 0.0,
                         "the keyed card still puts black over the picture")


class TheOverlayKeyRoute(EditorCase):
    """`POST /storyboard/edit/overlay-key` — the one seam the client uses."""

    def _plate(self, name="plate.png"):
        rgb, disc, _ = _burst(440, 800)
        p = self.out / name
        _rgba(p, rgb, np.where(disc, 255.0, 0.0))
        return p

    def test_a_plate_comes_back_as_a_derivative(self):
        p = self._plate()
        r = FakeHandler().post("edit/overlay-key", {"path": str(p)}).payload
        self.assertTrue(r["ok"])
        self.assertTrue(r["keyed"])
        self.assertNotEqual(r["path"], str(p))
        self.assertEqual(r["original"], str(p))
        self.assertTrue(Path(r["path"]).is_file())
        # The measurement travels with the answer, so a false negative in the
        # field can be diagnosed from the response.
        self.assertIn("hot_share", r["measured"])

    def test_an_honest_card_comes_back_untouched(self):
        rgb, _, ink = _burst(440, 800)
        rgb[~ink] = 0.0
        p = self.out / "honest.png"
        _rgba(p, rgb, np.where(ink, 255.0, 0.0))
        r = FakeHandler().post("edit/overlay-key", {"path": str(p)}).payload
        self.assertTrue(r["ok"])
        self.assertFalse(r["keyed"])
        self.assertEqual(r["path"], str(p))
        self.assertFalse(list(self.out.glob("*keyed*")))

    def test_a_path_outside_the_panels_own_folders_is_refused(self):
        h = FakeHandler().post("edit/overlay-key", {"path": "/etc/hosts"})
        self.assertEqual(h.status, 400)
        self.assertFalse(h.payload["ok"])

    def test_the_render_and_the_export_receive_the_SAME_file(self):
        """Parity is by construction: one path, three consumers."""
        p = self._plate()
        r = FakeHandler().post("edit/overlay-key", {"path": str(p)}).payload
        kept = r["path"]
        ov = [{"id": "o1", "kind": "still", "path": kept, "start": 0.0,
               "end": 2.0, "film_start": 1.0, "film_end": 3.0}]
        graph, _ = panel._sb_film_filtergraph(
            [], 768, 416, 48000, "yuv420p",
            segments=[{"kind": "video", "input": 0,
                       "info": {"has_audio": True, "duration": 10.0, "w": 768,
                                "h": 416, "sample_rate": 48000},
                       "window": {"start": 0.0, "end": 4.0}, "adjust": None,
                       "duration": 4.0, "path": "/x/a.mp4"}],
            overlays=ov, overlay_base=1)
        # The overlay stream ffmpeg is handed is input 1 — the derivative.
        self.assertIn("[1:v]format=rgba", graph)
        self.assertIn("overlay=0:0", graph)
        xml = sedit.fcp7_xml(
            [{"kind": "video", "path": "/x/a.mp4", "title": "a", "start": 0.0,
              "end": 4.0, "film_start": 0.0, "film_end": 4.0, "w": 768,
              "h": 416, "has_audio": True, "source_duration": 4.0}],
            name="f", media={"/x/a.mp4": "a.mp4", kept: Path(kept).name},
            width=768, height=416, base=str(self.out), overlays=ov)
        # The NLE is handed the derivative by name, on its own V2 track — the
        # same file the stage previewed and the same file ffmpeg composited.
        self.assertIn(Path(kept).stem, xml)
        self.assertEqual(len(ET.fromstring(xml).findall(".//video/track")), 2)


class AThumbnailKeepsItsTransparency(unittest.TestCase):
    """The preview half of the same bug, and it was the louder half.

    The overlay's stage layer asks `/image?w=1280`, which resizes through
    `_ensure_thumbnail` — and that used to `convert("RGB")` and save JPEG
    unconditionally. Every card wider than the requested width was previewed
    as a solid rectangle of its own background colour while the ffmpeg render
    composited the very same file correctly. A stage that disagrees with the
    render is worse than no overlay lane.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self._p = mock.patch.object(panel, "_THUMBCACHE", self.d / "cache")
        self._p.start()

    def tearDown(self):
        self._p.stop()
        self.tmp.cleanup()

    def test_a_transparent_source_resizes_with_its_alpha(self):
        from PIL import Image                                      # noqa: PLC0415
        rgb, _, ink = _burst(900, 1600)
        rgb[~ink] = 0.0
        src = self.d / "card.png"
        _rgba(src, rgb, np.where(ink, 255.0, 0.0))
        out = panel._ensure_thumbnail(src, 640)
        with Image.open(out) as im:
            self.assertIn("A", im.getbands(),
                          "the thumbnail dropped the alpha channel")
            self.assertLess(np.asarray(im.convert("RGBA"))[..., 3].mean(), 200,
                            "every pixel came back opaque")

    def test_an_opaque_source_is_still_a_jpeg(self):
        from PIL import Image                                      # noqa: PLC0415
        src = self.d / "photo.png"
        Image.fromarray(np.rint(_photoish(900, 1600)).astype(np.uint8),
                        "RGB").save(src)
        out = panel._ensure_thumbnail(src, 640)
        self.assertEqual(out.suffix, ".jpg")


class TheImagesTabSeesTheOutputsFolder(unittest.TestCase):
    """The pool's Images tab reads `/outputs`, and `/outputs` used to walk the
    image library only — so a card rendered or delivered into `mlx_outputs/`
    was invisible in the one list that turns a still into an overlay."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        (self.d / "outputs").mkdir()
        (self.d / "uploads").mkdir()
        self._patches = [
            mock.patch.object(panel, "OUTPUT", self.d / "outputs"),
            mock.patch.object(panel, "UPLOADS", self.d / "uploads"),
            mock.patch.object(panel, "HIDDEN_PATHS", set()),
            mock.patch.dict(panel.STATE, {"current": None, "history": []}),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self.tmp.cleanup()

    def _png(self, name):
        from PIL import Image                                      # noqa: PLC0415
        p = self.d / "outputs" / name
        Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(p)
        import os                                                  # noqa: PLC0415
        os.utime(p, (1_700_000_000, 1_700_000_000))
        return p

    def test_a_picture_in_the_outputs_folder_reaches_the_gallery(self):
        p = self._png("endcard.png")
        rows = panel.list_outputs(limit=0)
        got = [r for r in rows if r["path"] == str(p)]
        self.assertEqual(len(got), 1, "the outputs image never appeared")
        self.assertEqual(got[0]["kind"], "image")
        # ...and it is addressable by the route the pool paints it with.
        self.assertTrue(got[0]["url"].startswith("/image?"))

    def test_a_working_subfolder_is_NOT_a_gallery(self):
        # Proxies, per-film project folders and report intermediates live under
        # OUTPUT and are not outputs. The video scan is root-only; so is this.
        sub = self.d / "outputs" / "storyboards"
        sub.mkdir()
        from PIL import Image                                      # noqa: PLC0415
        Image.new("RGB", (8, 8)).save(sub / "proxy_frame.png")
        rows = panel.list_outputs(limit=0)
        self.assertFalse([r for r in rows if "proxy_frame" in r["path"]])


class TheEffectsModel(unittest.TestCase):
    """docs/EDITOR_EFFECTS_MODEL.md — the base, and its first citizen.

    "Just set the base to have effects somewhere, and then we will find a way
    to better integrate them." The rule that decides what ships: an effect
    that cannot be honestly expressed in ALL THREE outputs does not ship.
    """

    def _clip(self, **kw):
        return dict({"id": "a", "path": "/x/a.mp4", "proxy": None,
                     "start": 0.0, "end": 4.0, "film_start": 0.0,
                     "film_end": 4.0, "source": "human", "locked": False,
                     "duration": 10.0}, **kw)

    def test_absent_means_no_effect_and_no_version_bump(self):
        e = sedit.clip_effects(self._clip())
        self.assertEqual([e["fade_in"], e["fade_out"], e["brightness"]],
                         [0.0, 0.0, 0.0])
        self.assertEqual(sedit.EDIT_VERSION, 2)

    def test_ONE_accessor_whatever_the_storage(self):
        # Brightness is the legacy citizen and stays at `adjust.brightness`;
        # fades live in `fx`. A consumer never has to know that.
        c = self._clip(fx={"fade_in": 0.5}, adjust={"brightness": 0.25})
        e = sedit.clip_effects(c)
        self.assertEqual(e["fade_in"], 0.5)
        self.assertEqual(e["brightness"], 0.25)

    def test_the_clamp_lives_in_the_model_so_every_output_gets_legal_numbers(self):
        # Two fades that crossed would ask ffmpeg for an opacity that is two
        # things at once and hand the NLEs keyframes out of order.
        e = sedit.clip_effects(self._clip(fx={"fade_in": 3.0, "fade_out": 3.0}))
        self.assertEqual(e["fade_in"], 2.0)
        self.assertEqual(e["fade_out"], 2.0)
        self.assertLessEqual(e["fade_in"] + e["fade_out"], 4.0)
        # ...and it is proportional, not a truncation of one of them.
        e2 = sedit.clip_effects(self._clip(fx={"fade_in": 3.0, "fade_out": 1.0}))
        self.assertGreater(e2["fade_in"], e2["fade_out"])
        self.assertAlmostEqual(e2["fade_in"] + e2["fade_out"], 4.0, places=6)

    def test_neutral_is_absent_on_the_way_to_disk(self):
        doc = sedit.normalise_edit(_edit([self._clip(fx={"fade_in": 0.0})]))
        self.assertNotIn("fx", doc["clips"][0])
        doc = sedit.normalise_edit(_edit([self._clip(fx={"fade_in": 0.5})]))
        self.assertEqual(doc["clips"][0]["fx"], {"fade_in": 0.5})
        # ...and what lands on disk is already CLAMPED, so all three outputs
        # read the same legal numbers.
        doc = sedit.normalise_edit(_edit([self._clip(fx={"fade_in": 9.0})]))
        self.assertEqual(doc["clips"][0]["fx"], {"fade_in": 4.0})

    def test_a_negative_or_nonsense_fade_is_refused(self):
        for fx in ({"fade_in": -1}, {"fade_in": "half"}):
            codes = [e["code"] for e in
                     sedit.validate_edit(_edit([self._clip(fx=fx)]))]
            self.assertTrue(any(c.startswith("clip_fade") for c in codes), fx)
        self.assertIn("clip_fx", [e["code"] for e in
                                  sedit.validate_edit(_edit([self._clip(fx=7)]))])

    def test_a_fade_saves_and_reloads(self):
        with tempfile.TemporaryDirectory() as d:
            bdir = Path(d)
            sedit.save_edit(bdir, _edit([self._clip(fx={"fade_in": 0.5,
                                                       "fade_out": 1.0})]))
            got = sedit.load_edit(bdir)
            self.assertEqual(got["clips"][0]["fx"],
                             {"fade_in": 0.5, "fade_out": 1.0})
            self.assertEqual(sedit.validate_edit(got), [])

    # ---- output 2: the render ----------------------------------------
    def test_the_cut_list_and_the_graph_carry_it(self):
        cuts = sedit.edit_to_cuts(_edit([self._clip(fx={"fade_in": 0.5,
                                                        "fade_out": 1.0})]))
        self.assertEqual(cuts[0]["fx"], {"fade_in": 0.5, "fade_out": 1.0})
        # ...and an unfaded clip says nothing at all, so every film ever
        # exported builds the identical graph.
        self.assertNotIn("fx", sedit.edit_to_cuts(_edit([self._clip()]))[0])

    def test_the_fade_filter_runs_on_the_SEGMENTS_own_timeline(self):
        # After trim+setpts, so `st=0` is this clip's first frame whatever
        # window it plays: fades compose with trims without either knowing
        # about the other.
        term = panel._sb_fade_term({"fade_in": 0.5, "fade_out": 1.0}, 4.0)
        self.assertIn("fade=t=in:st=0:d=0.500000,", term)
        self.assertIn("fade=t=out:st=3.000000:d=1.000000,", term)
        self.assertEqual(panel._sb_fade_term(None, 4.0), "")
        self.assertEqual(panel._sb_fade_term({}, 4.0), "")

    def test_an_unfaded_film_builds_the_graph_it_always_did(self):
        info = {"has_audio": True, "duration": 10.0, "w": 1024, "h": 576,
                "sample_rate": 48000}
        seg = {"kind": "video", "input": 0, "info": dict(info),
               "window": {"start": 0.0, "end": 4.0}, "adjust": None,
               "duration": 4.0, "path": "/x/a.mp4"}
        plain, _ = panel._sb_film_filtergraph([], 1024, 576, 48000, "yuv420p",
                                              segments=[dict(seg)])
        self.assertNotIn("fade=", plain)
        faded, _ = panel._sb_film_filtergraph(
            [], 1024, 576, 48000, "yuv420p",
            segments=[dict(seg, fx={"fade_in": 0.5})])
        self.assertIn("fade=t=in:st=0:d=0.500000", faded)

    # ---- output 3: the export ----------------------------------------
    def _rows(self, **fx):
        return [{"kind": "video", "path": "/x/a.mp4", "title": "shot",
                 "start": 0.0, "end": 4.0, "film_start": 0.0, "film_end": 4.0,
                 "brightness": 0.0, "w": 1024, "h": 576, "has_audio": True,
                 "muted": False, "source_duration": 10.0,
                 "fx": dict({"fade_in": 0.0, "fade_out": 0.0}, **fx)}]

    def test_fcp7_gets_real_opacity_keyframes(self):
        # NOBODY RECEIVES BAKED PIXELS: the decision travels, not just its
        # result, so the editor downstream can drag the fade.
        xml = sedit.fcp7_xml(self._rows(fade_in=0.5, fade_out=1.0), name="f",
                             media={"/x/a.mp4": "a.mp4"}, width=1024,
                             height=576, base="/tmp/p")
        root = ET.fromstring(xml)
        keys = [(int(k.find("when").text), float(k.find("value").text))
                for k in root.iter("keyframe")]
        self.assertEqual(keys, [(0, 0.0), (12, 100.0), (72, 100.0), (96, 0.0)])
        self.assertIn("<name>Opacity</name>", xml)
        # An unfaded clip carries no filter at all.
        plain = sedit.fcp7_xml(self._rows(), name="f",
                               media={"/x/a.mp4": "a.mp4"}, width=1024,
                               height=576, base="/tmp/p")
        self.assertNotIn("<name>Opacity</name>", plain)

    def test_after_effects_gets_the_same_decision_in_its_own_idiom(self):
        jsx = sedit.ae_jsx(self._rows(fade_in=0.5, fade_out=1.0), name="f",
                           media={"/x/a.mp4": "a.mp4"}, width=1024, height=576)
        self.assertIn("ADBE Opacity", jsx)
        for line in ("op.setValueAtTime(0.000000, 0);",
                     "op.setValueAtTime(0.500000, 100);",
                     "op.setValueAtTime(3.000000, 100);",
                     "op.setValueAtTime(4.000000, 0);"):
            self.assertIn(line, jsx)
        self.assertNotIn("ADBE Opacity", sedit.ae_jsx(
            self._rows(), name="f", media={"/x/a.mp4": "a.mp4"},
            width=1024, height=576))

    def test_the_three_outputs_agree_on_WHERE_the_ramps_are(self):
        # The equivalence the model exists to guarantee: one clamped pair of
        # numbers, three idioms, the same two ramps in the same two places.
        rows = self._rows(fade_in=0.5, fade_out=1.0)
        xml = sedit.fcp7_xml(rows, name="f", media={"/x/a.mp4": "a.mp4"},
                             width=1024, height=576, base="/tmp/p")
        root = ET.fromstring(xml)
        keys = [(int(k.find("when").text) / 24.0, float(k.find("value").text))
                for k in root.iter("keyframe")]
        jsx = sedit.ae_jsx(rows, name="f", media={"/x/a.mp4": "a.mp4"},
                           width=1024, height=576)
        ae = [(float(m[0]), float(m[1])) for m in re.findall(
            r"op\.setValueAtTime\(([\d.]+), (\d+)\);", jsx)]
        self.assertEqual([round(t, 3) for t, _ in keys],
                         [round(t, 3) for t, _ in ae])
        self.assertEqual([v for _, v in keys], [v for _, v in ae])
        term = panel._sb_fade_term(rows[0]["fx"], 4.0)
        self.assertIn("d=0.500000", term)     # the in-ramp, same length
        self.assertIn("st=3.000000", term)    # the out-ramp, same place


class TheSameTakeTwiceIsOrdinaryEditing(unittest.TestCase):
    """sb_carwash rev 97, and the repair that would have eaten it.

    "I wanted it a little before her showing up, and I cut it that way. But no
    matter what I do, this is always out, even if I started with the full
    clip."

    Two uses of one take: the first is the whole thing with no strip of its
    own, the second is trimmed 0.42 s off the head with a strip that reaches
    back to cover it. That is a J-cut, and it is the most ordinary edit this
    feature exists to make.
    """

    def _board(self):
        src = "/x/carwash_15_buggy_but_it_works__warm.mp4"
        five = {"id": "c5", "path": src, "proxy": None, "start": 0.0,
                "end": 4.042, "film_start": 25.99, "film_end": 30.032,
                "source": "human", "locked": False, "duration": 4.042}
        six = {"id": "c6", "path": src, "proxy": None, "start": 0.42,
               "end": 4.042, "film_start": 30.77, "film_end": 34.392,
               "source": "human", "locked": False, "duration": 4.042,
               "audio": {"start": 0.0, "end": 4.042, "film_start": 30.35}}
        return _edit([five, six])

    def test_the_repair_does_not_touch_it(self):
        doc = self._board()
        self.assertEqual(sedit.repair_audio_overlaps(doc), 0)
        self.assertEqual(doc["clips"][1]["audio"],
                         {"start": 0.0, "end": 4.042, "film_start": 30.35})
        self.assertNotIn("audio", doc["clips"][0])

    def test_his_j_cut_reads_as_IN_SYNC(self):
        # He trimmed 0.42 s off the picture head and left the sound reaching
        # back, so the same source second still plays at the same film second.
        six = self._board()["clips"][1]
        self.assertEqual(sedit.clip_audio_drift(six), 0.0)
        self.assertEqual(sedit.edit_sync_flags(self._board()), [])

    def test_load_save_load_is_byte_stable(self):
        with tempfile.TemporaryDirectory() as d:
            bdir = Path(d)
            sedit.save_edit(bdir, self._board())
            first = sedit.load_edit(bdir)
            clips_a = json.dumps(first["clips"], sort_keys=True)
            sedit.save_edit(bdir, dict(first))
            second = sedit.load_edit(bdir)
            self.assertEqual(json.dumps(second["clips"], sort_keys=True), clips_a)
            self.assertEqual(second["clips"][1]["audio"],
                             {"start": 0.0, "end": 4.042, "film_start": 30.35})
            # ...and a third pass changes nothing either.
            sedit.save_edit(bdir, dict(second))
            self.assertEqual(json.dumps(sedit.load_edit(bdir)["clips"],
                                        sort_keys=True), clips_a)


class TheRepairIsAOneTimeMigration(unittest.TestCase):
    """A repair that runs on every read is a rival author.

    That is the thing the save-model ruling abolished, and it applies to our
    own migrations too: wrong once means wrong forever, invisibly, on every
    load of that film.
    """

    def _legacy(self):
        aud = {"start": 0.0, "end": 4.0, "film_start": 4.0}
        a = {"id": "a", "path": "/x/s.mp4", "proxy": None, "start": 0.0,
             "end": 2.0, "film_start": 4.0, "film_end": 6.0, "source": "human",
             "locked": False, "duration": 10.0, "audio": dict(aud)}
        b = {"id": "b", "path": "/x/s.mp4", "proxy": None, "start": 2.0,
             "end": 4.0, "film_start": 6.0, "film_end": 8.0, "source": "human",
             "locked": False, "duration": 10.0, "audio": dict(aud)}
        return _edit([a, b])

    def test_the_true_artifact_still_repairs_exactly_once(self):
        doc = sedit.migrate_edit(self._legacy())
        self.assertTrue(doc.get("repaired_audio_overlaps"))
        self.assertEqual(doc["audio_repair"], sedit.AUDIO_REPAIR_VERSION)
        self.assertEqual(doc["clips"][0]["audio"],
                         {"start": 0.0, "end": 2.0, "film_start": 4.0})
        self.assertEqual(doc["clips"][1]["audio"],
                         {"start": 2.0, "end": 4.0, "film_start": 6.0})

    def test_THE_REPAIR_DOES_NOT_REACH_BACK_INTO_THE_CALLERS_DOCUMENT(self):
        # `migrate_edit` shallow-copied the document and then let
        # `repair_audio_overlaps` write `clip["audio"]` — on clip dicts still
        # shared with the caller. `pending_backup` compares raw JSON on both
        # sides, so a read that edits its argument makes the file and the
        # snapshot differ over a repair neither of them contains: the
        # permanent interrogation docs/EDITOR_SAVE_MODEL.md exists to kill,
        # one level deeper than the mix stamp's own guard was looking.
        doc = self._legacy()
        before = json.dumps(doc, sort_keys=True)
        got = sedit.migrate_edit(doc)
        self.assertTrue(got.get("repaired_audio_overlaps"),
                        "the fixture stopped triggering the repair")
        self.assertEqual(json.dumps(doc, sort_keys=True), before)
        self.assertEqual(doc["clips"][0]["audio"],
                         {"start": 0.0, "end": 4.0, "film_start": 4.0})
        self.assertIsNot(got["clips"][0], doc["clips"][0])

    def test_the_marker_prevents_a_second_pass(self):
        stamped = dict(self._legacy(), audio_repair=sedit.AUDIO_REPAIR_VERSION)
        before = json.dumps(stamped["clips"], sort_keys=True)
        got = sedit.migrate_edit(stamped)
        self.assertEqual(json.dumps(got["clips"], sort_keys=True), before)
        self.assertNotIn("repaired_audio_overlaps", got)

    def test_a_repaired_board_is_never_examined_again(self):
        with tempfile.TemporaryDirectory() as d:
            bdir = Path(d)
            sedit.save_edit(bdir, sedit.migrate_edit(self._legacy()))
            once = json.dumps(sedit.load_edit(bdir)["clips"], sort_keys=True)
            for _ in range(3):
                self.assertEqual(
                    json.dumps(sedit.load_edit(bdir)["clips"], sort_keys=True),
                    once)

    def test_the_marker_is_bookkeeping_and_not_content(self):
        a = self._board_like()
        b = dict(a, audio_repair=sedit.AUDIO_REPAIR_VERSION)
        self.assertEqual(sedit.edit_digest(a), sedit.edit_digest(b))

    def _board_like(self):
        return _edit([{"id": "a", "path": "/x/a.mp4", "proxy": None,
                       "start": 0.0, "end": 2.0, "film_start": 0.0,
                       "film_end": 2.0, "source": "human", "locked": False,
                       "duration": 10.0}])


class TheSaveModel(unittest.TestCase):
    """The owner's architecture ruling, as a gate. docs/EDITOR_SAVE_MODEL.md.

    "Either the autosave is something on the side that you can always go and
    check the older versions... it only saves the real saves that are manually
    saved and named by the author... It's intertwined in a way that is causing
    a lot of problems."

    The last sentence is the defect: two writers of record and no rule about
    which one won.
    """

    def _doc(self, **kw):
        c = dict({"id": "a", "path": "/x/a.mp4", "proxy": None, "start": 0.0,
                  "end": 4.0, "film_start": 0.0, "film_end": 4.0,
                  "source": "human", "locked": False, "duration": 10.0},
                 **kw)
        return _edit([c])

    # ---- rule 4: content-equal shows NOTHING --------------------------
    def test_a_rewritten_proxy_pointer_is_not_a_content_difference(self):
        # THE BUG. `_sbe_payload` rewrites every clip's `proxy` on the way out,
        # deliberately, so a proxy built after the last save becomes visible
        # without a re-save. The old comparison was `json.dumps(clips)`, so the
        # client's copy differed from the file in a field the user has never
        # heard of — on any board whose proxies were built after its last save,
        # forever.
        a = self._doc(proxy="proxy/a_d83ae3ddf0b5.mp4")
        b = self._doc(proxy=None)
        self.assertEqual(sedit.edit_digest(a), sedit.edit_digest(b))

    def test_neither_is_a_revision_a_timestamp_or_an_origin(self):
        a = dict(self._doc(), revision=93, updated_at=111, origin="auto")
        b = dict(self._doc(), revision=94, updated_at=222, origin="manual")
        self.assertEqual(sedit.edit_digest(a), sedit.edit_digest(b))

    def test_but_a_REAL_edit_is(self):
        for kw in ({"end": 3.5}, {"film_start": 1.0}, {"locked": True},
                   {"mute": True},
                   {"audio": {"start": 0.0, "end": 4.0, "film_start": 1.0}},
                   {"adjust": {"brightness": 0.2}}):
            self.assertNotEqual(sedit.edit_digest(self._doc()),
                                sedit.edit_digest(self._doc(**kw)), kw)
        # ...and so is the soundtrack, and the beats.
        self.assertNotEqual(
            sedit.edit_digest(self._doc()),
            sedit.edit_digest(dict(self._doc(),
                                   audio={"path": "/t.wav", "offset": 0})))

    def test_a_content_equal_snapshot_raises_no_offer_at_all(self):
        with tempfile.TemporaryDirectory() as d:
            bdir = Path(d)
            sedit.save_edit(bdir, self._doc())
            # what the CLIENT holds: the same film with proxies filled in
            sedit.write_backup(bdir, self._doc(proxy="proxy/a_abc123.mp4"))
            self.assertIsNone(sedit.pending_backup(bdir))

    def test_a_real_difference_still_raises_one(self):
        with tempfile.TemporaryDirectory() as d:
            bdir = Path(d)
            sedit.save_edit(bdir, self._doc())
            sedit.write_backup(bdir, self._doc(end=3.0, film_end=3.0))
            offer = sedit.pending_backup(bdir)
            self.assertIsNotNone(offer)
            self.assertEqual(offer["clips"], 1)

    # ---- rule 1: loading always gives the last manual save ------------
    def test_loading_a_draft_ALWAYS_gives_the_last_manual_save(self):
        with tempfile.TemporaryDirectory() as d:
            bdir = Path(d)
            sedit.save_edit(bdir, self._doc())                 # rev 1
            sedit.write_backup(bdir, self._doc(end=2.0, film_end=2.0))
            got = sedit.load_edit(bdir)
            self.assertEqual(got["revision"], 1)
            self.assertEqual(got["clips"][0]["end"], 4.0)      # NOT the snapshot
            # ...and the snapshot is still there to be looked at.
            self.assertIsNotNone(sedit.pending_backup(bdir))

    # ---- rule 5: a superseded session writes nothing ------------------
    def test_an_unclaimed_board_refuses_nobody(self):
        # Every board written before this existed, plus every agent and script
        # posting to the routes with no tab to claim one.
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(sedit.session_is_current(Path(d), ""))
            self.assertTrue(sedit.session_is_current(Path(d), "anything"))

    def test_the_newest_loader_owns_the_lane_and_the_older_tab_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            bdir = Path(d)
            sedit.save_edit(bdir, self._doc())
            sedit.claim_session(bdir, "tab-one")
            self.assertTrue(sedit.session_is_current(bdir, "tab-one"))
            sedit.claim_session(bdir, "tab-two")               # a reload
            self.assertFalse(sedit.session_is_current(bdir, "tab-one"))
            self.assertTrue(sedit.session_is_current(bdir, "tab-two"))

    def test_a_snapshot_from_ANY_tab_is_kept(self):
        """Invariant 4 was "a snapshot from a superseded session is refused",
        and it was written for a lane with ONE slot per draft, where a stale
        tab's write destroyed the newer content in it.

        That lane is gone — one file per snapshot, pruned, never overwritten —
        and the invariant outlived its reason. What it did instead was cost the
        owner an afternoon: the claim was taken by LOADING, so a passive page
        load in another browser refused the tab he was cutting in, and it
        stopped writing snapshots for seven hours without saying so.

        The rule now: the lane refuses nobody. A snapshot from any tab costs
        one file and can only ever ADD a way back; refusing one can only ever
        remove one. Which tab is editing is still recorded — as something a
        person can be TOLD, never as a reason to stop protecting them.
        """
        with tempfile.TemporaryDirectory() as d:
            bdir = Path(d)
            sedit.save_edit(bdir, self._doc())
            sedit.write_backup(bdir, self._doc(end=3.0, film_end=3.0),
                               session="old")
            sedit.claim_session(bdir, "new")                   # another tab
            sedit.write_backup(bdir, self._doc(end=1.0, film_end=1.0),
                               session="old")
            # BOTH are on disk, and the older tab's work is one of them.
            self.assertEqual(len(sedit.list_history(bdir)), 2)
            self.assertIsNotNone(sedit.pending_backup(bdir))

    # ---- rule 3: restore can never lose anything ----------------------
    def _save(self, bdir, doc):
        """What the route does: the revision is the SERVER's, from disk."""
        cur = sedit.load_edit(bdir) or {}
        doc = dict(doc, revision=int(cur.get("revision") or 0))
        sedit.save_edit(bdir, doc)

    def test_restore_archives_the_current_document_first(self):
        with tempfile.TemporaryDirectory() as d:
            bdir = Path(d)
            self._save(bdir, self._doc())                      # rev 1
            self._save(bdir, self._doc(end=2.0, film_end=2.0))  # rev 2
            rows = sedit.list_history(bdir)
            self.assertTrue(rows)
            before = {r["file"] for r in rows}
            sedit.restore_edit(bdir, rows[-1]["file"])
            after = {r["file"] for r in sedit.list_history(bdir)}
            # The document that was on screen when Restore was pressed is now
            # itself a version — restoring by mistake loses nothing.
            self.assertTrue(after - before)
            self.assertGreater(sedit.load_edit(bdir)["revision"], 2)

    # ---- rule 2: the lane is versioned, bounded, and never stops ------
    def test_a_new_snapshot_never_destroys_the_last_one(self):
        # The single slot is why the client carried "if there is an offer, do
        # not write" — which switched the safety net off for the rest of the
        # session the moment a chip appeared and nobody dismissed it.
        with tempfile.TemporaryDirectory() as d:
            bdir = Path(d)
            self._save(bdir, self._doc())
            sedit.write_backup(bdir, self._doc(end=3.0, film_end=3.0))
            first = sedit.latest_snapshot(bdir)[0]
            sedit.write_backup(bdir, self._doc(end=2.0, film_end=2.0))
            second = sedit.latest_snapshot(bdir)[0]
            self.assertNotEqual(first, second)
            self.assertTrue(first.is_file())          # the older one survives
            self.assertEqual(sedit.pending_backup(bdir)["duration"], 2.0)

    def test_the_lane_is_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            bdir = Path(d)
            self._save(bdir, self._doc())
            for i in range(sedit.SNAPSHOT_KEEP + 6):
                sedit.write_backup(bdir, self._doc(end=4.0 - i * 0.01,
                                                   film_end=4.0 - i * 0.01))
            self.assertLessEqual(len(sedit._snapshot_paths(bdir)),
                                 sedit.SNAPSHOT_KEEP)

    def test_snapshots_are_listed_in_the_versions_browser_and_marked(self):
        # Rule 3: manual saves, named versions and snapshots together, and
        # visually distinct.
        with tempfile.TemporaryDirectory() as d:
            bdir = Path(d)
            self._save(bdir, self._doc())
            self._save(bdir, self._doc(end=3.0, film_end=3.0))
            sedit.write_backup(bdir, self._doc(end=2.0, film_end=2.0))
            rows = sedit.list_history(bdir)
            snaps = [r for r in rows if r["snapshot"]]
            saves = [r for r in rows if r["manual"]]
            self.assertEqual(len(snaps), 1)
            self.assertTrue(saves)
            self.assertFalse(snaps[0]["manual"])
            # ...and one is restorable like any other version.
            sedit.restore_edit(bdir, snaps[0]["file"])
            self.assertEqual(sedit.load_edit(bdir)["clips"][0]["end"], 2.0)

    def test_a_legacy_single_slot_board_migrates_losslessly(self):
        # Rule 6: nothing on disk is deleted. A board written before the lane
        # was versioned is read, not ignored.
        with tempfile.TemporaryDirectory() as d:
            bdir = Path(d)
            self._save(bdir, self._doc())
            legacy = sedit._backup_path(bdir, "draft-1")
            legacy.parent.mkdir(parents=True, exist_ok=True)
            legacy.write_text(json.dumps(sedit.normalise_edit(
                self._doc(end=1.5, film_end=1.5))))
            got = sedit.latest_snapshot(bdir)
            self.assertIsNotNone(got)
            # MOVED into the lane, not read where it lay: a file at the root
            # of history/ is invisible to the versions list and unreachable by
            # restore, so it would have been a snapshot the user could be told
            # about and could not open.
            self.assertFalse(legacy.exists())
            self.assertTrue(got[0].name.startswith("snap-"))
            self.assertEqual(sedit.pending_backup(bdir)["duration"], 1.5)
            rows = sedit.list_history(bdir)
            self.assertIn(got[0].name, [r["file"] for r in rows])
            sedit.restore_edit(bdir, got[0].name)      # and it opens
            self.assertEqual(sedit.load_edit(bdir)["clips"][0]["end"], 1.5)

    # ---- rule 2: the side archive never has an opinion ----------------
    def test_the_snapshot_lane_takes_anything_a_manual_save_would(self):
        with tempfile.TemporaryDirectory() as d:
            bdir = Path(d)
            # An overlapping J-cut: a WARNING, and neither writer may refuse.
            doc = _edit([
                dict(self._doc()["clips"][0], id="a"),
                {"id": "b", "path": "/x/b.mp4", "proxy": None, "start": 0.0,
                 "end": 4.0, "film_start": 4.0, "film_end": 8.0,
                 "source": "human", "locked": False, "duration": 10.0,
                 "audio": {"start": 0.0, "end": 4.0, "film_start": 3.75}}])
            sedit.save_edit(bdir, doc)
            sedit.write_backup(bdir, doc)      # neither raises

    # ---- rule 7: a cache may not outlive its subject ------------------
    def test_peaks_are_invalidated_when_the_soundtrack_changes(self):
        with tempfile.TemporaryDirectory() as d:
            bdir = Path(d)
            sedit.save_peaks(bdir, {"version": 1, "path": "/music/old.wav",
                                    "duration": 44.99, "peaks": [], "count": 0,
                                    "scale": 127, "buckets_per_second": 100})
            self.assertIsNotNone(sedit.load_peaks(bdir))
            self.assertIsNotNone(sedit.load_peaks(bdir, path="/music/old.wav"))
            # The strip read "44.99s" under a different file for exactly this
            # reason: nothing compared the cache to its subject.
            self.assertIsNone(sedit.load_peaks(bdir, path="/music/new.wav"))

    def test_the_same_file_spelled_two_ways_is_NOT_a_change(self):
        # `mlx_outputs/` is a symlink into Pinokio's shared drive, so the very
        # same soundtrack is spelled two ways depending on which side wrote it
        # down. A raw string compare would have blanked the waveform on boards
        # where nothing had changed — the invalidation fix arriving as a worse
        # bug than the one it fixed. Caught on the live test panel.
        with tempfile.TemporaryDirectory() as d:
            bdir = Path(d)
            real = bdir / "track.wav"
            real.write_bytes(b"x")
            link = bdir / "via-a-link.wav"
            try:
                link.symlink_to(real)
            except OSError:
                raise unittest.SkipTest("no symlinks here")
            sedit.save_peaks(bdir, {"version": 1, "path": str(real),
                                    "duration": 12.0, "peaks": [], "count": 0,
                                    "scale": 127, "buckets_per_second": 100})
            self.assertIsNotNone(sedit.load_peaks(bdir, path=str(link)))
            self.assertIsNone(sedit.load_peaks(bdir, path=str(bdir / "no.wav")))


class MutingAClipsOwnSound(unittest.TestCase):
    """`clip.mute` — one additive flag, three outputs, one meaning.

    "We should have an option to mute the clip sound." The H3 shot with wind
    baked in under the line, on a cut where the music is meant to carry it.
    """

    def _clip(self, **kw):
        return dict({"id": "a", "path": "/x/a.mp4", "proxy": None,
                     "start": 0.0, "end": 4.0, "film_start": 0.0,
                     "film_end": 4.0, "source": "human", "locked": False,
                     "duration": 10.0}, **kw)

    def test_absent_is_audible_and_no_document_is_rewritten(self):
        self.assertFalse(sedit.clip_muted(self._clip()))
        self.assertTrue(sedit.clip_muted(self._clip(mute=True)))
        self.assertEqual(sedit.EDIT_VERSION, 2)

    def test_mute_save_reload_still_muted(self):
        with tempfile.TemporaryDirectory() as d:
            bdir = Path(d)
            sedit.save_edit(bdir, _edit([self._clip(mute=True)]))
            got = sedit.load_edit(bdir)
            self.assertTrue(got["clips"][0]["mute"])
            self.assertTrue(sedit.clip_muted(got["clips"][0]))
            self.assertEqual(sedit.validate_edit(got), [])
            # ...and unmuting leaves a document identical to one never muted.
            sedit.save_edit(bdir, _edit([self._clip()]))
            self.assertNotIn("mute", sedit.load_edit(bdir)["clips"][0])

    def test_the_flag_is_only_written_when_it_says_something(self):
        doc = sedit.normalise_edit(_edit([self._clip(mute=False)]))
        self.assertNotIn("mute", doc["clips"][0])
        slug = {"id": "s", "kind": "slug", "path": None, "start": 0.0,
                "end": 2.0, "film_start": 0.0, "film_end": 2.0,
                "source": "human", "locked": False, "mute": True}
        self.assertIn("clip_mute_kind",
                      [e["code"] for e in sedit.validate_edit(_edit([slug]))])
        self.assertNotIn("mute", sedit.normalise_edit(_edit([slug]))["clips"][0])
        bad = self._clip(mute="yes")
        self.assertIn("clip_mute",
                      [e["code"] for e in sedit.validate_edit(_edit([bad]))])

    def test_mute_and_unlink_compose(self):
        c = self._clip(mute=True, film_start=4.0, film_end=8.0,
                       audio={"start": 0.0, "end": 4.0, "film_start": 3.0})
        self.assertTrue(sedit.clip_muted(c))
        w = sedit.clip_audio(c)
        self.assertTrue(w["split"])                 # the strip stays put
        self.assertEqual(w["film_start"], 3.0)
        cuts = sedit.edit_to_cuts(_edit([c]))
        self.assertTrue(cuts[0]["mute"])
        self.assertEqual(cuts[0]["audio"]["film_start"], 3.0)

    def test_the_cut_list_says_nothing_when_nothing_is_muted(self):
        self.assertNotIn("mute", sedit.edit_to_cuts(_edit([self._clip()]))[0])


class TheRenderMixIsActuallySilent(unittest.TestCase):
    """Mute is expressed as the ABSENCE of an audio lane.

    The film's sound is `concat` of lanes and `anullsrc` hushes laid end to
    end, so "do not play this clip" is a row that is not there — no volume
    filter, no mixer, nothing that could sum.
    """

    def _segs(self, mute_second: bool):
        info = {"has_audio": True, "duration": 10.0, "w": 1024, "h": 576,
                "sample_rate": 48000}
        segs = []
        for i, name in enumerate(("a", "b")):
            sg = {"kind": "video", "input": i, "info": dict(info),
                  "window": {"start": 0.0, "end": 4.0}, "adjust": None,
                  "duration": 4.0, "path": f"/x/{name}.mp4"}
            if mute_second and i == 1:
                sg["mute"] = True
            segs.append(sg)
        return segs

    def test_an_unmuted_film_builds_the_graph_it_always_did(self):
        plan = panel._sb_split_audio_plan(self._segs(False))
        self.assertFalse(plan["split"])
        self.assertEqual([L["idx"] for L in plan["lanes"]], [0, 1])

    def test_a_muted_clip_contributes_no_lane_and_forces_the_split_path(self):
        plan = panel._sb_split_audio_plan(self._segs(True))
        # Without the split flag the graph would take the plain
        # `concat ... a=1` branch and play the audio just switched off.
        self.assertTrue(plan["split"])
        self.assertEqual([L["idx"] for L in plan["lanes"]], [0])

    def test_the_muted_seconds_come_out_as_REAL_silence(self):
        plan = panel._sb_split_audio_plan(self._segs(True))
        chains = panel._sb_split_audio_chains(plan["lanes"], plan["total"],
                                              48000, "[aout]")
        graph = ";".join(chains)
        self.assertIn("[a0]", graph)
        self.assertNotIn("[a1]", graph)          # the muted clip is not in it
        # ...and its slot is an honest anullsrc of exactly its length, not an
        # input that happens to be quiet.
        self.assertIn("anullsrc=channel_layout=stereo:sample_rate=48000:"
                      "d=4.000000", graph)
        self.assertIn("concat=n=2:v=0:a=1[aout]", graph)

    def test_the_flag_reaches_the_segments_from_the_timeline(self):
        entry = {"path": "/x/a.mp4", "start": 0.0, "end": 4.0,
                 "film_start": 0.0, "mute": True}
        win = panel._sb_cut_index([entry])["/x/a.mp4"][0]
        self.assertTrue(win["mute"])


class TheExportDisablesTheTrackRatherThanDroppingIt(unittest.TestCase):
    """An editor on the far end must be able to see the decision and undo it."""

    def _segs(self, muted: bool):
        return [{"kind": "video", "path": "/x/a.mp4", "title": "shot one",
                 "start": 0.0, "end": 4.0, "film_start": 0.0, "film_end": 4.0,
                 "brightness": 0.0, "w": 1024, "h": 576, "has_audio": True,
                 "muted": muted, "source_duration": 10.0}]

    def test_the_fcp7_audio_clipitem_is_present_and_DISABLED(self):
        on = sedit.fcp7_xml(self._segs(False), name="f", media={"/x/a.mp4": "a.mp4"},
                            width=1024, height=576, base="/tmp/p")
        off = sedit.fcp7_xml(self._segs(True), name="f", media={"/x/a.mp4": "a.mp4"},
                             width=1024, height=576, base="/tmp/p")
        # The clipitem exists in BOTH — omitting it would arrive as a shot that
        # never had sound, and no editor could tell a decision from a source.
        self.assertIn('id="clipitem-a1"', on)
        self.assertIn('id="clipitem-a1"', off)
        self.assertEqual(on.count("<enabled>TRUE</enabled>"),
                         off.count("<enabled>TRUE</enabled>") + 1)
        self.assertIn("<enabled>FALSE</enabled>", off)
        self.assertNotIn("<enabled>FALSE</enabled>", on)
        # ...and the PICTURE is untouched either way.
        self.assertIn('id="clipitem-1"', off)

    def test_the_after_effects_layer_keeps_its_sound_and_does_not_play_it(self):
        on = sedit.ae_jsx(self._segs(False), name="f",
                          media={"/x/a.mp4": "a.mp4"}, width=1024, height=576)
        off = sedit.ae_jsx(self._segs(True), name="f",
                           media={"/x/a.mp4": "a.mp4"}, width=1024, height=576)
        self.assertNotIn("audioEnabled", on)
        self.assertIn("lay.audioEnabled = false;", off)

    def test_the_flag_reaches_the_export_from_the_document(self):
        rows = sedit._nle_segments([
            {"id": "a", "path": "/x/a.mp4", "start": 0.0, "end": 4.0,
             "film_start": 0.0, "film_end": 4.0, "source": "human",
             "locked": False, "duration": 10.0, "mute": True}])
        self.assertTrue(rows[0]["muted"])


class SavingIsNeverBlockedByOverlappingSound(unittest.TestCase):
    """The afternoon this cost, as a gate.

    `clips_audio_overlap` was an ERROR, and every writer of this document —
    Save, the autosave and the crash backup alike — refuses on any error. So
    the moment a J-cut pulled one line a quarter of a second under the outgoing
    shot, the board became unsaveable AND unbackupable, with a red "SAVING IS
    FAILING" banner over work that could not be stored anywhere. His exact
    report: "the sound of clips 1 and 2 overlaps (0.000-3.354s and
    3.102-8.143s)".
    """

    def _pair(self, lead=0.25):
        a = {"id": "a", "path": "/x/a.mp4", "proxy": None, "start": 0.0,
             "end": 3.354, "film_start": 0.0, "film_end": 3.354,
             "source": "human", "locked": False, "duration": 10.0}
        b = {"id": "b", "path": "/x/b.mp4", "proxy": None, "start": 0.0,
             "end": 5.041, "film_start": 3.354, "film_end": 8.395,
             "source": "human", "locked": False, "duration": 10.0,
             "audio": {"start": 0.0, "end": 5.041,
                       "film_start": round(3.354 - lead, 6)}}
        return _edit([a, b])

    def test_a_quarter_second_j_cut_is_a_WARNING_not_an_error(self):
        errs = sedit.validate_edit(self._pair())
        self.assertEqual([e["code"] for e in errs], ["clips_audio_overlap"])
        self.assertEqual(errs[0].get("severity"), "warning")
        self.assertEqual(sedit.blocking_errors(errs), [])

    def test_a_real_defect_still_blocks(self):
        broken = self._pair()
        broken["clips"][0]["end"] = -1.0
        self.assertTrue(sedit.blocking_errors(sedit.validate_edit(broken)))

    def test_save_and_backup_both_write_it(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            bdir = Path(d)
            sedit.save_edit(bdir, self._pair())            # must not raise
            got = sedit.load_edit(bdir)
            self.assertEqual(got["revision"], 1)
            self.assertEqual(got["clips"][1]["audio"]["film_start"], 3.104)
            sedit.write_backup(bdir, self._pair(lead=0.5))  # nor this
            self.assertIsNotNone(sedit.pending_backup(bdir))

    # ---- ...AND EVERY READER MUST GIVE IT BACK -------------------------
    # The severity split was applied to the two WRITERS and stopped there.
    # `recover_backup` and `restore_edit` kept refusing on the raw list, so
    # the work went to disk and could not come off it again: a crash over a
    # J-cut wrote a backup and then answered "Recover it" with the user's own
    # edit quoted back as the reason it was refused. Nothing discards the
    # offer on that path, and while an offer is unanswered the client stops
    # taking new snapshots — so the one failure disarmed the safety net for
    # the rest of the session. Caught by the v4.6.0 release gate.

    def test_a_crash_backup_holding_a_j_cut_can_be_RECOVERED(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            bdir = Path(d)
            sedit.save_edit(bdir, _edit([self._pair()["clips"][0]]))
            sedit.write_backup(bdir, self._pair())
            got = sedit.recover_backup(bdir)        # must not raise
            self.assertEqual(len(got["clips"]), 2)
            self.assertIsNone(sedit.pending_backup(bdir))

    def test_a_history_version_holding_a_j_cut_can_be_RESTORED(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            bdir = Path(d)
            sedit.save_edit(bdir, self._pair())          # J-cut -> edit.json
            sedit.save_edit(bdir, _edit([self._pair()["clips"][0]]))
            rows = sedit.list_history(bdir)
            self.assertTrue(rows)
            got = sedit.restore_edit(bdir, rows[0]["file"])   # must not raise
            self.assertEqual(len(got["clips"]), 2)

    def test_a_genuinely_broken_version_is_STILL_refused(self):
        # The relaxation is scoped to WARNING_CODES and nothing else: a
        # history entry that would not validate as a document must still be
        # refused, or this fix would have traded one bug for a worse one.
        import tempfile
        broken = self._pair()
        broken["clips"][0]["end"] = -1.0
        self.assertTrue(sedit.blocking_errors(sedit.validate_edit(broken)))
        with tempfile.TemporaryDirectory() as d:
            bdir = Path(d)
            sedit.save_edit(bdir, _edit([self._pair()["clips"][0]]))
            hist = sedit.history_dir(bdir)
            hist.mkdir(parents=True, exist_ok=True)
            (hist / "save-r00009.json").write_text(json.dumps(broken))
            with self.assertRaises(sedit.EditError):
                sedit.restore_edit(bdir, "save-r00009.json")


class TheDuplicatedStripArtifactIsRepairedOnLoad(unittest.TestCase):
    """A board split before the J-cut fix carries two strips claiming the same
    seconds of the same take. It was legal when it was written and became
    unsaveable the day the overlap rule arrived — a migration problem wearing a
    validation error's clothes."""

    def _legacy(self):
        # What the old `sbeSplitAt` produced: a deep copy of `audio` on both
        # halves of the split.
        aud = {"start": 0.0, "end": 4.0, "film_start": 4.0}
        a = {"id": "a", "path": "/x/s.mp4", "proxy": None, "start": 0.0,
             "end": 2.0, "film_start": 4.0, "film_end": 6.0, "source": "human",
             "locked": False, "duration": 10.0, "audio": dict(aud)}
        b = {"id": "b", "path": "/x/s.mp4", "proxy": None, "start": 2.0,
             "end": 4.0, "film_start": 6.0, "film_end": 8.0, "source": "human",
             "locked": False, "duration": 10.0, "audio": dict(aud)}
        return _edit([a, b])

    def test_the_two_halves_are_re_cut_in_their_shared_source_clock(self):
        doc = self._legacy()
        self.assertEqual(sedit.repair_audio_overlaps(doc), 1)
        a, b = doc["clips"]
        self.assertEqual(a["audio"], {"start": 0.0, "end": 2.0,
                                      "film_start": 4.0})
        self.assertEqual(b["audio"], {"start": 2.0, "end": 4.0,
                                      "film_start": 6.0})
        # ...and the document it produces has nothing left to warn about.
        self.assertEqual(sedit.validate_edit(doc), [])

    def test_it_happens_on_the_way_IN_so_a_legacy_board_just_opens(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            bdir = Path(d)
            sedit.edit_path(bdir).write_text(json.dumps(self._legacy()))
            got = sedit.load_edit(bdir)
            self.assertTrue(got.get("repaired_audio_overlaps"))
            self.assertEqual(sedit.validate_edit(got), [])
            sedit.save_edit(bdir, got)

    def test_a_DELIBERATE_overlap_is_left_exactly_where_he_put_it(self):
        # A J-cut that merely overlaps its neighbour is a decision, not a
        # defect: the two strips play different seconds of different takes.
        a = {"id": "a", "path": "/x/a.mp4", "proxy": None, "start": 0.0,
             "end": 4.0, "film_start": 0.0, "film_end": 4.0, "source": "human",
             "locked": False, "duration": 10.0}
        b = {"id": "b", "path": "/x/b.mp4", "proxy": None, "start": 0.0,
             "end": 4.0, "film_start": 4.0, "film_end": 8.0, "source": "human",
             "locked": False, "duration": 10.0,
             "audio": {"start": 0.0, "end": 4.0, "film_start": 3.75}}
        doc = _edit([a, b])
        self.assertEqual(sedit.repair_audio_overlaps(doc), 0)
        self.assertEqual(doc["clips"][1]["audio"]["film_start"], 3.75)


class TheSaveRouteAnswersWithWarnings(EditorCase):

    def test_an_overlapping_j_cut_saves_and_the_note_rides_back(self):
        a = _clip(str(self.clips[0]), 0.0, 2.0, 0.0)
        a["duration"] = 10.0
        b = _clip(str(self.clips[1]), 0.0, 2.0, 2.0)
        b["duration"] = 10.0
        b["audio"] = {"start": 0.0, "end": 2.0, "film_start": 1.75}
        h = FakeHandler().post("edit/save", {},
                               json.dumps({"id": "sb_t",
                                           "edit": _edit([a, b])}))
        self.assertEqual(h.status, 200)
        self.assertTrue(h.payload["ok"])
        self.assertEqual([w["code"] for w in h.payload["warnings"]],
                         ["clips_audio_overlap"])
        self.assertEqual(h.payload["edit"]["revision"], 1)


class TheReadRouteReportsTheSyncFlags(EditorCase):
    """The panel cannot flag what the server does not say."""

    def test_the_payload_carries_the_sync_flags_beside_the_gaps(self):
        c = _clip(str(self.clips[0]), 0.0, 2.0, 0.0)
        c["duration"] = 10.0
        c["audio"] = {"start": 0.0, "end": 2.0, "film_start": 1.25}
        sedit.save_edit(self.bdir, _edit([c]))
        h = FakeHandler().get("/storyboard/edit?id=sb_t")
        self.assertEqual(h.status, 200)
        self.assertEqual(len(h.payload["sync"]), 1)
        self.assertEqual(h.payload["sync"][0]["drift"], 1.25)
        self.assertEqual(h.payload["sync"][0]["resync_to"], 0.0)

    def test_an_ordinary_film_reports_an_empty_list(self):
        sedit.save_edit(self.bdir, _edit(
            [_clip(str(self.clips[0]), 0.0, 2.0, 0.0)]))
        h = FakeHandler().get("/storyboard/edit?id=sb_t")
        self.assertEqual(h.payload["sync"], [])


if __name__ == "__main__":
    unittest.main()
