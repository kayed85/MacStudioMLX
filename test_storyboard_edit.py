#!/usr/bin/env python3
"""Tests for the auto-editor: which window, where the beats, and the plan.

Three things are locked here.

1. **The window search.** A generated clip is not uniformly good, and the whole
   point of `best_window()` is to prefer the part that is. These tests feed it
   SYNTHETIC frame stacks with a known answer — a sharp middle and a soft tail,
   a frozen stretch, a blown-out ending — and assert it finds the answer, that
   the head/tail prior only breaks ties, and that two identical calls produce
   identical dicts.

2. **The beat grid.** A click track written by `wave` at a known tempo is the
   only honest way to test a tempo estimator without shipping a reference
   implementation to disagree with. The assertions are the numbers that matter:
   BPM within a fraction of a beat, downbeats on the accents.

3. **The plan.** `min_shot` is never violated, snapping never invents a cut
   that the shot length forbids, the order is the order given, the total lands
   near the target, and the same inputs give the same plan.

No GPU, no weights, no model download, and nothing longer than a few seconds
of synthesised audio. The only external process is ffmpeg, and only where a
real decode is the thing being tested.

Run:  python3 -m unittest test_storyboard_edit
"""

from __future__ import annotations

import json
import math
import struct
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import numpy as np

import storyboard_edit as se

ROOT = Path(__file__).resolve().parent
HAVE_FFMPEG = Path(se.FFMPEG).is_file() and Path(se.FFPROBE).is_file()


# ---------------------------------------------------------------------------
# Synthetic footage. `amp` drives sharpness (contrast of a fixed noise
# pattern), `dc` drives both brightness and — through its frame-to-frame
# change — motion. Nothing here is random at call time: one seeded pattern,
# reused, so the whole suite is reproducible.
# ---------------------------------------------------------------------------
PATTERN = (np.random.default_rng(20260815).integers(0, 256, size=(36, 64))
           .astype(np.float32) - 128.0) / 128.0


def synth(amp: list[float], dc: list[float]) -> np.ndarray:
    frames = [np.clip(d + 60.0 * a * PATTERN, 0, 255) for a, d in zip(amp, dc)]
    return np.stack(frames).astype(np.uint8)


def probe(duration: float, w: int = 640, h: int = 360) -> dict:
    return {"w": w, "h": h, "duration": duration, "fps": 24.0,
            "has_audio": True, "sample_rate": 48000}


def window(frames: np.ndarray, want: float, duration: float | None = None,
           **kw) -> dict:
    n = frames.shape[0]
    dur = duration if duration is not None else n / se.ANALYSIS_FPS
    with mock.patch.object(se, "_decode_grey", return_value=frames):
        return se.best_window("/nope/clip.mp4", want, probe=probe(dur), **kw)


# =============================================================================
# The scoring primitives
# =============================================================================
class ScoreShapes(unittest.TestCase):
    def test_motion_is_a_band_not_a_maximum(self):
        # THE reason this is not "more movement is better": a frozen window is
        # dead footage and a thrashing one is the model breaking up.
        self.assertEqual(se._motion_score(0.0), 0.0)
        self.assertEqual(se._motion_score(se.MOTION_DEAD), 0.0)
        self.assertEqual(se._motion_score(se.MOTION_LOW), 1.0)
        self.assertEqual(se._motion_score(se.MOTION_HIGH), 1.0)
        self.assertEqual(se._motion_score(se.MOTION_THRASH), 0.0)
        self.assertEqual(se._motion_score(999.0), 0.0)
        # and it really does ramp, in both directions
        mid_low = se._motion_score((se.MOTION_DEAD + se.MOTION_LOW) / 2)
        mid_high = se._motion_score((se.MOTION_HIGH + se.MOTION_THRASH) / 2)
        for v in (mid_low, mid_high):
            self.assertGreater(v, 0.0)
            self.assertLess(v, 1.0)

    def test_luma_rejects_both_ends(self):
        self.assertEqual(se._luma_score(0.0), 0.0)
        self.assertEqual(se._luma_score(255.0), 0.0)
        self.assertEqual(se._luma_score(128.0), 1.0)
        self.assertGreater(se._luma_score(se.LUMA_GOOD_LO), 0.99)

    def test_ramp_handles_a_zero_width_ramp(self):
        self.assertEqual(se._ramp(5.0, 3.0, 3.0), 1.0)
        self.assertEqual(se._ramp(1.0, 3.0, 3.0), 0.0)


# =============================================================================
# best_window
# =============================================================================
class BestWindow(unittest.TestCase):
    def test_it_finds_the_sharp_middle_of_a_clip_that_softens(self):
        # The documented failure mode of generated video: soft head, good
        # middle, degrading tail. 8 s at 4 fps.
        amp = ([0.35] * 4) + ([1.0] * 16) + ([0.30] * 12)
        dc = [110.0 + 2.0 * (i % 2) for i in range(32)]
        got = window(synth(amp, dc), 4.0)
        self.assertAlmostEqual(got["end"] - got["start"], 4.0, places=6)
        self.assertGreaterEqual(got["start"], 0.75)
        self.assertLessEqual(got["end"], 5.5)
        self.assertIn("sharpness", got["reason"])

    def test_a_frozen_stretch_loses_to_a_moving_one(self):
        # Same sharpness throughout; the only difference is that the first six
        # seconds do not move at all. A four-second window fits entirely
        # inside either half, so there is no excuse for straddling.
        amp = [1.0] * 48
        dc = [110.0] * 24 + [110.0 + 4.0 * (i % 2) for i in range(24)]
        got = window(synth(amp, dc), 4.0)
        self.assertGreaterEqual(got["start"], 5.0, "picked the frozen half")
        self.assertEqual(got["components"]["stability"], 1.0)

    def test_a_blown_out_tail_is_vetoed_not_merely_marked_down(self):
        # The real bug class: a render that washes to white in its last
        # seconds. No window may include it.
        amp = [0.5] * 20 + [0.05] * 12
        dc = [110.0 + 3.0 * (i % 2) for i in range(20)] + [252.0] * 12
        got = window(synth(amp, dc), 3.0)
        self.assertLessEqual(got["end"], 5.0)
        self.assertTrue(got["usable"])

    def test_every_window_blown_is_reported_as_unusable(self):
        frames = synth([0.05] * 24, [251.0] * 24)
        got = window(frames, 3.0)
        self.assertFalse(got["usable"])
        self.assertIn("luma check", got["reason"])

    def test_a_black_clip_is_rejected_too(self):
        frames = synth([0.05] * 24, [3.0] * 24)
        self.assertFalse(window(frames, 3.0)["usable"])

    def test_the_positional_prior_breaks_a_tie_away_from_both_guards(self):
        # Uniform footage: nothing in the pixels prefers any position, so the
        # head and tail guards are the only thing left. The winner must clear
        # BOTH guard zones, and among the windows that do, it must be the
        # earliest — determinism, not taste.
        frames = synth([1.0] * 24, [110.0 + 3.0 * (i % 2) for i in range(24)])
        got = window(frames, 4.0)
        self.assertEqual(got["components"]["penalty"], 0.0)
        self.assertGreaterEqual(got["start"], se.HEAD_GUARD_S)
        self.assertLessEqual(got["end"], got["duration"] - se.TAIL_GUARD_S)
        self.assertEqual(got["start"], 0.5)          # first sample past 0.30
        # ...and it is a PRIOR, so turning it off changes the answer's basis
        # rather than crashing.
        off = window(frames, 4.0, head_weight=0.0, tail_weight=0.0)
        self.assertEqual(off["components"]["penalty"], 0.0)
        self.assertEqual(off["start"], 0.0)

    def test_a_bigger_tail_guard_pushes_the_window_earlier(self):
        frames = synth([1.0] * 40, [110.0 + 3.0 * (i % 2) for i in range(40)])
        near = window(frames, 4.0, tail_guard=0.5, tail_weight=0.25)
        far = window(frames, 4.0, tail_guard=5.0, tail_weight=0.25)
        self.assertLessEqual(far["start"], near["start"])

    def test_the_window_is_exactly_as_long_as_asked(self):
        frames = synth([1.0] * 40, [110.0 + 3.0 * (i % 2) for i in range(40)])
        for want in (1.0, 2.5, 3.75, 7.0):
            got = window(frames, want)
            self.assertAlmostEqual(got["end"] - got["start"], want, places=6)
            self.assertGreaterEqual(got["start"], 0.0)
            self.assertLessEqual(got["end"], got["duration"] + 1e-9)

    def test_asking_for_the_whole_clip_or_more_returns_the_whole_clip(self):
        frames = synth([1.0] * 20, [110.0] * 20)
        for want in (5.0, 9.0):
            got = window(frames, want, duration=5.0)
            self.assertEqual((got["start"], got["end"]), (0.0, 5.0))
            self.assertTrue(got["forced"])
            self.assertIn("nothing to choose", got["reason"])
            # It is still SCORED — a forced window is not a scoreless one.
            self.assertIn("stability", got["components"])

    def test_per_second_diagnostics_are_returned_for_a_human(self):
        frames = synth([1.0] * 24, [110.0] * 24)
        got = window(frames, 2.0)
        self.assertEqual(len(got["per_second"]), 6)
        for row in got["per_second"]:
            for key in ("t", "sharpness", "motion", "luma", "crushed", "blown"):
                self.assertIn(key, row)

    def test_it_is_deterministic(self):
        frames = synth([0.4, 1.0] * 16, [110.0 + 2.0 * (i % 3) for i in range(32)])
        a = window(frames, 3.0)
        b = window(frames, 3.0)
        self.assertEqual(json.dumps(a, sort_keys=True),
                         json.dumps(b, sort_keys=True))

    def test_a_flat_clip_says_sharpness_did_not_decide(self):
        frames = synth([1.0] * 24, [110.0] * 24)
        got = window(frames, 2.0)
        self.assertIn("sharpness was flat", got["reason"])

    def test_an_unprobeable_clip_is_an_honest_error(self):
        with mock.patch.object(se, "probe_media", return_value=None):
            with self.assertRaises(RuntimeError):
                se.best_window("/nope/clip.mp4", 2.0)


# =============================================================================
# snap + the planner's constrained variant
# =============================================================================
class Snap(unittest.TestCase):
    BEATS = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]

    def test_it_moves_to_the_nearest_beat_inside_the_tolerance(self):
        self.assertEqual(se.snap(1.05, self.BEATS, 0.2), 1.0)
        self.assertEqual(se.snap(1.45, self.BEATS, 0.2), 1.5)

    def test_it_leaves_the_time_alone_outside_the_tolerance(self):
        self.assertEqual(se.snap(1.25, self.BEATS, 0.1), 1.25)
        self.assertEqual(se.snap(1.25, self.BEATS, 0.0), 1.25)
        self.assertEqual(se.snap(1.25, [], 5.0), 1.25)
        self.assertEqual(se.snap(1.25, None, 5.0), 1.25)

    def test_an_exact_midpoint_takes_the_earlier_beat(self):
        # Determinism, not musicality: a tie has to resolve the same way every
        # run or the plan is not reproducible.
        self.assertEqual(se.snap(1.25, self.BEATS, 0.5), 1.0)

    def test_the_tolerance_is_inclusive_of_its_own_edge(self):
        self.assertEqual(se.snap(1.2, self.BEATS, 0.2), 1.0)

    def test_constrained_snapping_refuses_a_cut_the_shot_cannot_reach(self):
        # The nearest beat is 2.0 but the shot may not run past 1.6, so the
        # answer is the best REACHABLE beat, not "no beat".
        self.assertEqual(se._snap_in_range(1.9, self.BEATS, 0.5, 0.0, 1.6), 1.5)
        self.assertIsNone(se._snap_in_range(1.9, self.BEATS, 0.1, 0.0, 1.6))
        self.assertIsNone(se._snap_in_range(1.9, [], 0.5, 0.0, 1.6))
        self.assertIsNone(se._snap_in_range(1.9, self.BEATS, 0.5, 2.0, 1.0))


class Balance(unittest.TestCase):
    def test_it_hits_the_target_when_the_material_allows(self):
        got = se._balance([10.0] * 5, 30.0, 1.5)
        self.assertAlmostEqual(sum(got), 30.0, places=4)

    def test_no_clip_is_asked_for_more_than_it_has(self):
        avail = [2.0, 10.0, 3.0]
        got = se._balance(avail, 30.0, 1.5)
        for a, g in zip(avail, got):
            self.assertLessEqual(g, a + 1e-9)

    def test_a_target_the_material_cannot_reach_is_capped_not_faked(self):
        got = se._balance([2.0, 2.0], 60.0, 1.5)
        self.assertAlmostEqual(sum(got), 4.0, places=6)

    def test_it_terminates_on_degenerate_input(self):
        self.assertEqual(se._balance([], 10.0, 1.5), [])
        self.assertEqual(len(se._balance([5.0], 0.0, 1.5)), 1)


# =============================================================================
# beat_map, against a click track with a known tempo
# =============================================================================
def click_track(path: Path, bpm: float, seconds: float, *, sr: int = 22050,
                meter: int = 4, accent: float = 1.0) -> Path:
    """A 4/4 click at a known tempo. Every beat is a short decaying burst;
    beat one is louder. Written with the stdlib so the fixture cannot drift
    with an ffmpeg version."""
    n = int(seconds * sr)
    buf = np.zeros(n, dtype=np.float64)
    period = 60.0 / bpm
    tick = np.exp(-np.arange(int(0.03 * sr)) / (0.004 * sr))
    tone = np.sin(2 * math.pi * 1800 * np.arange(tick.size) / sr) * tick
    k = 0
    while True:
        t = k * period
        i = int(round(t * sr))
        if i + tone.size >= n:
            break
        buf[i:i + tone.size] += tone * (accent if k % meter == 0 else 0.45)
        k += 1
    buf = np.clip(buf, -1.0, 1.0)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(struct.pack(f"<{n}h", *(buf * 30000).astype(np.int16)))
    return path


@unittest.skipUnless(HAVE_FFMPEG, "needs a real ffmpeg")
class BeatMap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.click = click_track(cls.dir / "click125.wav", 125.0, 40.0)
        cls.map = se.beat_map(cls.click)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_it_recovers_the_tempo_it_was_given(self):
        # Half a BPM at 125 is 1.6 ms per beat — over a 40 s track that is a
        # third of a beat of accumulated drift, which is the real tolerance.
        self.assertAlmostEqual(self.map["bpm"], 125.0, delta=0.5)

    def test_the_beats_are_where_the_clicks_are(self):
        period = 60.0 / 125.0
        for b in self.map["beats"][:40]:
            self.assertLess(abs(b - round(b / period) * period), 0.02,
                            f"beat at {b:.3f}s is not on the click grid")

    def test_downbeats_are_every_fourth_beat_and_land_on_the_accent(self):
        beats, downs = self.map["beats"], self.map["downbeats"]
        self.assertEqual(self.map["meter"], 4)
        self.assertEqual(len(downs), len(beats[self.map["diagnostics"]
                                               ["downbeat_offset"]::4]))
        period = 60.0 / 125.0
        for d in downs[:8]:
            self.assertLess(abs(d - round(d / (4 * period)) * 4 * period), 0.03,
                            f"downbeat at {d:.3f}s is not on an accent")

    def test_a_click_track_is_high_confidence_and_tightly_locked(self):
        self.assertGreater(self.map["confidence"], 0.6)
        self.assertLess(self.map["diagnostics"]["grid_lock_ms"], 25.0)
        self.assertGreater(self.map["diagnostics"]["pulse_ratio"], 2.0)

    def test_it_is_deterministic(self):
        again = se.beat_map(self.click)
        self.assertEqual(again["bpm"], self.map["bpm"])
        self.assertEqual(again["beats"], self.map["beats"])
        self.assertEqual(again["downbeats"], self.map["downbeats"])

    def test_a_span_limits_both_the_fit_and_the_beats(self):
        part = se.beat_map(self.click, span=(10.0, 25.0))
        self.assertAlmostEqual(part["bpm"], 125.0, delta=0.8)
        self.assertGreaterEqual(min(part["beats"]), 9.99)
        self.assertLessEqual(max(part["beats"]), 25.01)
        self.assertEqual(part["span"], [10.0, 25.0])

    def test_noise_is_not_reported_as_a_confident_tempo(self):
        rng = np.random.default_rng(7)
        n = 22050 * 20
        noise = (rng.standard_normal(n) * 3000).astype(np.int16)
        p = self.dir / "noise.wav"
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(22050)
            w.writeframes(noise.tobytes())
        got = se.beat_map(p)
        self.assertLess(got["confidence"], self.map["confidence"],
                        "white noise must not look as beat-like as a click track")

    def test_the_span_helper_is_the_one_definition_of_the_fit_window(self):
        self.assertIsNone(se.audio_span_for(None))
        self.assertIsNone(se.audio_span_for(0))
        self.assertEqual(se.audio_span_for(60.0), (0.0, 100.0))


# =============================================================================
# plan_cut
# =============================================================================
def fake_map(bpm: float = 120.0, phase: float = 0.0, seconds: float = 120.0,
             meter: int = 4) -> dict:
    period = 60.0 / bpm
    beats = [round(phase + k * period, 6)
             for k in range(int((seconds - phase) / period) + 1)]
    return {"bpm": bpm, "period": period, "phase": phase, "meter": meter,
            "beats": beats, "downbeats": beats[::meter], "confidence": 0.9,
            "span": [0.0, seconds], "duration": seconds, "diagnostics": {}}


class PlanCut(unittest.TestCase):
    """The planner, with the window search stubbed out — this suite is about
    lengths, boundaries and order, and a real decode would only make it slow
    and dependent on footage."""

    def setUp(self):
        def fake_window(path, want, *, probe=None, **kw):
            return {"path": str(path), "start": 0.25,
                    "end": round(0.25 + want, 6), "score": 0.5,
                    "usable": True, "reason": "stub", "components": {},
                    "runner_up": None, "per_second": [],
                    "duration": probe["duration"], "forced": False,
                    "candidates": 3}
        self.patch = mock.patch.object(se, "best_window",
                                       side_effect=fake_window)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def plan(self, durations, **kw):
        probes = {f"/c/{i}.mp4": probe(d) for i, d in enumerate(durations)}
        return se.plan_cut(list(probes), probes=probes, **kw)

    def test_the_order_given_is_the_order_planned(self):
        got = self.plan([6.0, 6.0, 6.0])
        self.assertEqual([p["path"] for p in got],
                         ["/c/0.mp4", "/c/1.mp4", "/c/2.mp4"])
        self.assertEqual([p["n"] for p in got], [1, 2, 3])

    def test_shots_are_laid_end_to_end_with_no_gap(self):
        got = self.plan([6.0] * 4, audio=fake_map(), target_seconds=16.0)
        self.assertEqual(got[0]["film_start"], 0.0)
        for a, b in zip(got, got[1:]):
            self.assertAlmostEqual(a["film_end"], b["film_start"], places=6)
            self.assertAlmostEqual(a["film_end"] - a["film_start"],
                                   a["duration"], places=6)

    def test_min_shot_is_never_violated(self):
        got = self.plan([10.0] * 8, audio=fake_map(bpm=140.0),
                        target_seconds=12.0, min_shot=2.0)
        for p in got:
            self.assertGreaterEqual(p["duration"], 2.0 - 1e-9,
                                    f"shot {p['n']} is {p['duration']}s")

    def test_a_source_shorter_than_min_shot_is_used_whole_and_flagged(self):
        got = self.plan([0.8, 6.0], min_shot=1.5)
        self.assertAlmostEqual(got[0]["duration"], 0.8, places=6)
        self.assertTrue(any("shorter than" in n for n in got[0]["notes"]))

    def test_max_shot_caps_every_shot(self):
        got = self.plan([20.0] * 3, max_shot=4.0)
        for p in got:
            self.assertLessEqual(p["duration"], 4.0 + 1e-9)

    def test_the_total_lands_near_the_target(self):
        got = self.plan([8.0] * 6, audio=fake_map(), target_seconds=24.0)
        self.assertAlmostEqual(se.plan_total(got), 24.0, delta=1.0)

    def test_a_target_the_material_cannot_reach_is_not_faked(self):
        got = self.plan([2.0] * 3, target_seconds=60.0)
        self.assertAlmostEqual(se.plan_total(got), 6.0, places=6)

    def test_every_cut_lands_on_the_grid_and_says_which(self):
        bm = fake_map(bpm=120.0, phase=0.13)
        got = self.plan([8.0] * 5, audio=bm, target_seconds=20.0)
        for p in got:
            self.assertIn(p["snap"]["kind"], ("downbeat", "beat", "none"))
            if p["snap"]["kind"] == "downbeat":
                self.assertTrue(any(abs(p["film_end"] - d) < 1e-6
                                    for d in bm["downbeats"]))
            elif p["snap"]["kind"] == "beat":
                self.assertTrue(any(abs(p["film_end"] - b) < 1e-6
                                    for b in bm["beats"]))

    def test_the_shift_is_reported_in_milliseconds_with_a_sign(self):
        got = self.plan([8.0] * 4, audio=fake_map(phase=0.21),
                        target_seconds=18.0)
        for p in got:
            self.assertAlmostEqual(
                p["snap"]["shift_ms"],
                (p["snap"]["landed"] - p["snap"]["wanted"]) * 1000.0, places=3)

    def test_without_music_nothing_is_snapped(self):
        got = self.plan([6.0] * 3, target_seconds=12.0)
        for p in got:
            self.assertEqual(p["snap"]["kind"], "none")
            self.assertEqual(p["snap"]["shift_ms"], 0.0)

    def test_a_grid_below_min_confidence_is_refused_out_loud(self):
        bm = fake_map()
        bm["confidence"] = 0.2
        got = self.plan([6.0] * 3, audio=bm, target_seconds=12.0,
                        min_confidence=0.6)
        for p in got:
            self.assertEqual(p["snap"]["kind"], "none")
        self.assertTrue(any("below the" in n for n in got[0]["notes"]))

    def test_the_window_never_runs_past_the_end_of_its_source(self):
        got = self.plan([3.0, 3.0], audio=fake_map(), target_seconds=6.0)
        for p in got:
            self.assertLessEqual(p["end"], p["window"]["source_duration"] + 1e-6)
            self.assertGreaterEqual(p["start"], 0.0)
            self.assertAlmostEqual(p["end"] - p["start"], p["duration"],
                                   places=6)

    def test_it_is_deterministic(self):
        a = self.plan([6.0, 9.0, 4.0], audio=fake_map(phase=0.07),
                      target_seconds=15.0)
        b = self.plan([6.0, 9.0, 4.0], audio=fake_map(phase=0.07),
                      target_seconds=15.0)
        self.assertEqual(json.dumps(a, sort_keys=True),
                         json.dumps(b, sort_keys=True))

    def test_an_empty_list_plans_nothing_rather_than_raising(self):
        self.assertEqual(se.plan_cut([]), [])

    def test_a_clip_that_cannot_be_probed_is_an_honest_error(self):
        with mock.patch.object(se, "probe_media", return_value=None):
            with self.assertRaises(RuntimeError):
                se.plan_cut(["/nope/clip.mp4"])

    def test_the_plan_formats_as_something_a_human_can_read(self):
        got = self.plan([6.0] * 3, audio=fake_map(), target_seconds=12.0)
        text = se.format_plan(got, fake_map())
        self.assertIn("3 shots", text)
        self.assertIn("BPM", text)
        for p in got:
            self.assertIn(Path(p["path"]).name, text)


# =============================================================================
# The real decode paths, on fixtures ffmpeg makes in a fraction of a second
# =============================================================================
@unittest.skipUnless(HAVE_FFMPEG, "needs a real ffmpeg")
class RealDecode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def make(self, name, w, h, seconds) -> Path:
        out = self.dir / name
        subprocess.run(
            [str(se.FFMPEG), "-y", "-f", "lavfi",
             "-i", f"testsrc=size={w}x{h}:rate=24:duration={seconds}",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
            capture_output=True, check=True, timeout=120)
        return out

    def test_probe_reads_a_real_clip(self):
        p = self.make("a.mp4", 320, 240, 2)
        got = se.probe_media(p)
        self.assertEqual((got["w"], got["h"]), (320, 240))
        self.assertAlmostEqual(got["duration"], 2.0, delta=0.1)
        self.assertFalse(got["has_audio"])

    def test_probe_reads_an_AUDIO_ONLY_file(self):
        # An mp3 has no video stream; the clip probe answers None for that and
        # is right to. This one must not.
        wav = click_track(self.dir / "click.wav", 120.0, 4.0)
        got = se.probe_media(wav)
        self.assertIsNotNone(got)
        self.assertTrue(got["has_audio"])
        self.assertEqual(got["sample_rate"], 22050)

    def test_probe_returns_None_for_something_that_is_not_media(self):
        junk = self.dir / "junk.mp4"
        junk.write_bytes(b"not an mp4 at all")
        self.assertIsNone(se.probe_media(junk))

    def test_the_analysis_decode_samples_rather_than_decoding_everything(self):
        p = self.make("b.mp4", 640, 360, 3)
        frames = se._decode_grey(p, 320, 180, 4.0)
        self.assertEqual(frames.shape[1:], (180, 320))
        self.assertAlmostEqual(frames.shape[0], 12, delta=1)  # 3 s at 4 fps
        self.assertEqual(frames.dtype, np.uint8)

    def test_best_window_runs_end_to_end_on_a_real_clip(self):
        p = self.make("c.mp4", 320, 240, 4)
        got = se.best_window(p, 2.0)
        self.assertAlmostEqual(got["end"] - got["start"], 2.0, places=6)
        self.assertGreaterEqual(got["start"], 0.0)
        self.assertLessEqual(got["end"], 4.05)
        self.assertTrue(got["per_second"])

    def test_a_clip_that_cannot_be_decoded_raises_rather_than_guessing(self):
        junk = self.dir / "junk2.mp4"
        junk.write_bytes(b"still not an mp4")
        with self.assertRaises(RuntimeError):
            se._decode_grey(junk, 32, 32, 4.0)


if __name__ == "__main__":
    unittest.main()
