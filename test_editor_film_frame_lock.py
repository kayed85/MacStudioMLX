#!/usr/bin/env python3
"""FILM-51 — does rounding clips to whole frames drift the picture against
the song over many cuts? Measured here, analytically, instead of with a
GPU render: the render trims each segment on its OWN local clock
(`setpts=PTS-STARTPTS` then `fps={FPS}` in `mlx_ltx_panel.py`'s film
filtergraph), which is exactly `round(length * fps) / fps` per segment,
independently of every other segment — the same arithmetic this file
simulates without spending a render on it.

Locked here:
  * `TheMeasurement` — the drift IS real: a synthetic 52-cut film with
    non-frame-aligned windows accumulates more than a frame of drift
    between the (frame-rounded) picture and the (exact) song well before
    the last cut, confirming the report's own estimate ("potentially 1 to
    2 frames... late in the film").
  * `heal_subframe_lengths` — the fix. Every clip's FILM length becomes an
    exact multiple of 1/fps, so the render's `fps=` filter has nothing
    left to round and the simulated drift is zero at every cut, not just
    the last one. Gaps are untouched, a locked clip is an anchor, an
    unlinked sound strip keeps its exact drift, the take's own source
    bound is respected (a fix that cannot fit is skipped, not clamped into
    a mismatch), and a second pass is a no-op.
  * `load_edit` deliberately does NOT call it. Wiring it in there first
    surfaced a real hazard: unlike the gap heal, it can rewrite a clip's
    own `end`, and a save-history entry hand-corrupted for
    `test_a_genuinely_broken_version_is_STILL_refused`
    (test_storyboard_editor_api.py) stopped being refused, because the
    heal recomputed a plausible `end` before `validate_edit` ever saw the
    corrupt one.
  * `TheSaveHeals` / `TheRenderPathHealsAndValidates` — where it IS wired,
    both strictly after their own `validate_edit`/`blocking_errors` check:
    `storyboard_editor.save_edit` (right after `validate_edit` +
    `normalise_edit`, so every manual save converges to frame-exact) and
    `mlx_ltx_panel._sbe_render_edit` (its own `validate_edit` check, refuse
    on failure, heal on success, THEN `edit_to_cuts` — so a film saved
    before this fix existed still renders frame-exact without a resave).
    Both re-prove the exact regression above stays fixed on the path that
    now runs unconditionally.

Run:  ./ltx-2-mlx/env/bin/python3.11 -m pytest -q test_editor_film_frame_lock.py
"""
from __future__ import annotations

import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as panel                                        # noqa: E402
import storyboard_editor as sedit                                    # noqa: E402

FPS = 24.0


def _clip(cid, path, start, end, film_start, **kw):
    c = sedit.new_clip(path, start, end, film_start, id=cid,
                       duration=kw.pop("duration", 999.0), source="human")
    c.update(kw)
    return c


def _doc(clips, **kw):
    d = {"version": sedit.EDIT_VERSION, "board_id": "sb_lock", "revision": 0,
         "source": "human", "audio": None, "beats": None, "clips": clips,
         "settings": {}}
    d.update(kw)
    return d


def _realistic_52_cut_film(seed: int = 42) -> list[dict]:
    """52 clips, each 2-8s, non-frame-aligned — the shape a music-video
    board actually produces (durations come from planned beats and a2v
    audio segments, not from the editor's own frame grid)."""
    rng = random.Random(seed)
    clips = []
    cursor = 0.0
    for i in range(52):
        length = round(rng.uniform(2.0, 8.0) + rng.random() / FPS * rng.choice([0.3, 0.7, 1.3]), 6)
        clips.append(_clip(f"c{i}", f"/x/s{i}.mp4", 0.0, length, cursor, duration=length + 5.0))
        cursor = round(cursor + length, 6)
    return clips


def _simulate_render_cumulative(clips: list[dict], fps: float = FPS) -> list[float]:
    """What the render's picture clock reads after each cut: each segment's
    length independently rounded to the nearest frame (ffmpeg's `fps=`
    filter on its own local PTS), then summed — exactly `_sb_film_filtergraph`'s
    per-segment `trim=...,fps={FPS}` behaviour."""
    cum = 0.0
    out = []
    for c in clips:
        length = c["end"] - c["start"]
        frames = round(length * fps)
        cum += frames / fps
        out.append(cum)
    return out


def _exact_cumulative(clips: list[dict]) -> list[float]:
    cum = 0.0
    out = []
    for c in clips:
        cum += c["end"] - c["start"]
        out.append(cum)
    return out


class TheMeasurement(unittest.TestCase):
    def test_a_52_cut_film_drifts_more_than_a_frame_before_the_last_cut(self):
        clips = _realistic_52_cut_film()
        rendered = _simulate_render_cumulative(clips)
        exact = _exact_cumulative(clips)
        drift_frames = [abs(r - e) * FPS for r, e in zip(rendered, exact)]
        # The report's own estimate: "potentially 1 to 2 frames of lip
        # drift late in the film, and only in the render."
        self.assertGreater(max(drift_frames), 1.0,
                           "expected the unhealed film to drift past a full frame")
        self.assertLess(max(drift_frames), 6.0,
                        "a multi-frame drift this large would mean the simulation is wrong, "
                        "not that the bug is worse than reported")

    def test_the_drift_is_not_an_artifact_of_this_one_seed(self):
        # Three more seeds, same shape of film — the bug is structural
        # (independent per-segment rounding), not a one-off.
        for seed in (1, 7, 99):
            clips = _realistic_52_cut_film(seed)
            rendered = _simulate_render_cumulative(clips)
            exact = _exact_cumulative(clips)
            max_drift = max(abs(r - e) * FPS for r, e in zip(rendered, exact))
            self.assertGreater(max_drift, 0.3, f"seed {seed}: expected measurable drift")


class TheFix(unittest.TestCase):
    def test_every_clip_length_becomes_frame_exact(self):
        doc = _doc(_realistic_52_cut_film())
        fixed = sedit.heal_subframe_lengths(doc)
        self.assertEqual(len(fixed), 52)   # none of these 52 started frame-exact
        for c in doc["clips"]:
            length = c["film_end"] - c["film_start"]
            frames = length * FPS
            # Millisecond precision: comfortably above the residual noise a
            # 6-decimal-place store leaves on a repeating fraction like
            # 1/24, comfortably below anything a viewer could perceive.
            self.assertAlmostEqual(frames, round(frames), places=3)

    def test_the_simulated_drift_is_zero_at_every_cut_after_the_fix(self):
        doc = _doc(_realistic_52_cut_film())
        sedit.heal_subframe_lengths(doc)
        clips = [dict(c, start=c["film_start"], end=c["film_end"]) for c in doc["clips"]]
        rendered = _simulate_render_cumulative(clips)
        exact = _exact_cumulative(clips)
        for r, e in zip(rendered, exact):
            self.assertAlmostEqual(r, e, places=6)

    def test_gaps_are_preserved_not_closed(self):
        a = _clip("a", "/x/a.mp4", 0.0, 3.001, 0.0, duration=99.0)
        b = _clip("b", "/x/b.mp4", 0.0, 4.0, 5.0, duration=99.0)   # ~2s deliberate gap
        doc = _doc([a, b])
        sedit.heal_subframe_lengths(doc)
        got = sedit.load_edit  # not called; direct healer test
        a2, b2 = doc["clips"]
        gap = b2["film_start"] - a2["film_end"]
        self.assertGreater(gap, 1.9)   # still roughly 2s, not zeroed

    def test_a_locked_clip_is_never_moved(self):
        a = _clip("a", "/x/a.mp4", 0.0, 3.001, 0.0, duration=99.0)
        b = _clip("b", "/x/b.mp4", 0.0, 4.002, 3.001, locked=True, duration=99.0)
        c = _clip("c", "/x/c.mp4", 0.0, 2.003, 7.003, duration=99.0)
        doc = _doc([a, b, c])
        sedit.heal_subframe_lengths(doc)
        b2 = next(x for x in doc["clips"] if x["id"] == "b")
        self.assertEqual(b2["film_start"], 3.001)
        self.assertEqual(b2["film_end"], 7.003)

    def test_an_unlinked_strip_keeps_its_exact_drift(self):
        a = _clip("a", "/x/a.mp4", 0.0, 3.003, 0.0, duration=99.0,
                 audio={"start": 0.2, "end": 3.2, "film_start": 0.15})
        b = _clip("b", "/x/b.mp4", 0.0, 4.0, 3.003, duration=99.0)
        doc = _doc([a, b])
        before_drift = sedit.clip_audio_drift(a)
        sedit.heal_subframe_lengths(doc)
        a2 = next(x for x in doc["clips"] if x["id"] == "a")
        after_drift = sedit.clip_audio_drift(a2)
        self.assertAlmostEqual(before_drift, after_drift, places=6)

    def test_a_correction_past_the_takes_own_end_is_skipped_not_clamped(self):
        # 3.03s is 72.72 frames -> rounds UP to 73 (3.041667s), needing
        # ~0.0117s more than the take has (duration leaves 0.005s of room).
        a = _clip("a", "/x/a.mp4", 0.0, 3.03, 0.0, duration=3.035)
        doc = _doc([a])
        fixed = sedit.heal_subframe_lengths(doc)
        self.assertEqual(fixed, [])
        a2 = doc["clips"][0]
        self.assertEqual(a2["end"], 3.03)   # untouched
        # Document stays internally consistent — no length_mismatch.
        self.assertEqual([e["code"] for e in sedit.validate_edit(doc)], [])

    def test_idempotent(self):
        doc = _doc(_realistic_52_cut_film())
        sedit.heal_subframe_lengths(doc)
        second = sedit.heal_subframe_lengths(doc)
        self.assertEqual(second, [])

    def test_a_frame_exact_film_reports_nothing_to_fix(self):
        a = _clip("a", "/x/a.mp4", 0.0, 3.0, 0.0, duration=99.0)     # 72 frames exactly
        b = _clip("b", "/x/b.mp4", 0.0, 2.0 + 1 / FPS, 3.0, duration=99.0)
        doc = _doc([a, b])
        self.assertEqual(sedit.heal_subframe_lengths(doc), [])

    def test_load_edit_does_not_call_it_yet(self):
        # Deliberately NOT wired into the automatic read path — see the
        # comment above the (absent) call in `load_edit`. Rewriting a
        # clip's own `end` turned out to be able to launder a genuinely
        # corrupt history entry (a hand-set `end: -1.0`) into a plausible
        # one before `validate_edit` ever saw it
        # (`test_a_genuinely_broken_version_is_STILL_refused` in
        # test_storyboard_editor_api.py), which is a worse defect than the
        # drift this function fixes. It is correct and available for a
        # caller that has already validated its document; this pins that
        # `load_edit` is not (yet) that caller.
        tmp = Path(tempfile.mkdtemp())
        doc = _doc(_realistic_52_cut_film())
        sedit.save_edit(tmp, doc)
        raw = sedit.edit_path(tmp)
        import json
        on_disk = json.loads(raw.read_text())
        on_disk["clips"][3]["film_end"] = on_disk["clips"][3]["film_start"] + 3.3003
        raw.write_text(json.dumps(on_disk))
        loaded = sedit.load_edit(tmp)
        self.assertNotIn("healed_subframe_lengths", loaded)
        length = loaded["clips"][3]["film_end"] - loaded["clips"][3]["film_start"]
        self.assertAlmostEqual(length, 3.3003, places=6)   # untouched


# =============================================================================
# WHERE IT IS ACTUALLY WIRED — save_edit and the render path, both AFTER
# validate_edit, never before.
# =============================================================================
class TheSaveHeals(unittest.TestCase):
    """`save_edit`: validate, normalise, THEN heal — so every manual save is
    a frame-exact document, and a corrupt one is still refused before this
    ever touches it."""

    def test_a_saved_film_is_frame_exact(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        doc = _doc(_realistic_52_cut_film())
        sedit.save_edit(tmp, doc)
        loaded = sedit.load_edit(tmp)
        for c in loaded["clips"]:
            length = c["film_end"] - c["film_start"]
            frames = length * FPS
            self.assertAlmostEqual(frames, round(frames), places=3)

    def test_a_second_save_is_stable(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        sedit.save_edit(tmp, _doc(_realistic_52_cut_film()))
        first = sedit.load_edit(tmp)
        sedit.save_edit(tmp, dict(first))
        second = sedit.load_edit(tmp)
        self.assertEqual(
            [(c["film_start"], c["film_end"]) for c in first["clips"]],
            [(c["film_start"], c["film_end"]) for c in second["clips"]])

    def test_a_genuinely_broken_document_is_still_refused(self):
        # The exact regression that made auto-wiring into load_edit unsafe,
        # now proven against the path this DOES run on: a bad `end` must
        # still be refused by save_edit, not silently repaired into
        # something plausible.
        a = _clip("a", "/x/a.mp4", 0.0, 4.0, 0.0, duration=10.0)
        broken = _doc([a])
        broken["clips"][0]["end"] = -1.0
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        with self.assertRaises(sedit.EditError):
            sedit.save_edit(tmp, broken)


class TheRenderPathHealsAndValidates(unittest.TestCase):
    """`_sbe_render_edit`: its own validate_edit check refuses a corrupt
    document before heal_subframe_lengths ever sees it; a valid one is
    healed before `edit_to_cuts` builds the plan, so a 52-cut film saved
    BEFORE this fix existed still renders frame-exact without a resave."""

    def _mocked(self, edit, extra=None):
        board = {"id": "b1", "shots": []}
        fake_film = {"ok": True, "path": "/out/film.mp4", "width": 1920, "height": 1080}
        patches = [
            mock.patch.object(panel, "_sbe_import", return_value=sedit),
            mock.patch.object(panel, "_sb_film_dir_for_write", return_value=Path("/out")),
            mock.patch.object(panel, "_sb_deliver", return_value={"format": "h264", "size": "native",
                                                                   "finish": "none", "label": "H.264"}),
            mock.patch.object(panel, "_sb_film_name", return_value="film.mp4"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        captured = {}

        def fake_assemble(paths, out, *, timeline=None, **kw):
            captured["timeline"] = timeline
            return dict(fake_film)
        m = mock.patch.object(panel, "_sb_assemble_film", side_effect=fake_assemble)
        m.start()
        self.addCleanup(m.stop)
        got = panel._sbe_render_edit(board, edit)
        return got, captured.get("timeline")

    def test_a_corrupted_edit_is_refused_before_rendering(self):
        a = _clip("a", "/x/a.mp4", 0.0, 4.0, 0.0, duration=10.0)
        edit = _doc([a])
        edit["clips"][0]["end"] = -1.0
        with mock.patch.object(panel, "_sbe_import", return_value=sedit), \
             mock.patch.object(panel, "_sb_assemble_film") as assemble:
            got = panel._sbe_render_edit({"id": "b1", "shots": []}, edit)
        self.assertFalse(got["ok"])
        self.assertEqual(got.get("status"), 400)
        assemble.assert_not_called()          # never reached the assembler

    def test_a_52_cut_film_renders_with_no_cumulative_drift(self):
        edit = sedit.normalise_edit(_doc(_realistic_52_cut_film()))
        got, timeline = self._mocked(edit)
        self.assertTrue(got["ok"])
        self.assertIsNotNone(timeline)
        # The plan `_sb_assemble_film` actually receives: every entry's own
        # film length (end - start, at 1x — none of these clips are
        # retimed) is frame-exact, and the simulated picture clock (what
        # ffmpeg's fps= filter would produce from these windows) never
        # drifts from the exact sum by more than float noise, at ANY cut —
        # not just the last one.
        exact_cum = 0.0
        render_cum = 0.0
        for seg in timeline:
            length = seg["end"] - seg["start"]
            frames = length * FPS
            self.assertAlmostEqual(frames, round(frames), places=3)
            exact_cum += length
            render_cum += round(length * FPS) / FPS
            self.assertAlmostEqual(render_cum, exact_cum, places=3)

    def test_an_already_frame_exact_film_asks_nothing_new_of_the_assembler(self):
        # A film saved AFTER save_edit's own healing lands here unchanged —
        # this is the "old edit, rendered today" case converging without a
        # human ever having to resave it first.
        clips = _realistic_52_cut_film()
        edit = sedit.normalise_edit(_doc(clips))
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        sedit.save_edit(tmp, edit)          # heals it
        healed = sedit.load_edit(tmp)
        got, timeline = self._mocked(healed)
        self.assertTrue(got["ok"])
        for seg in timeline:
            length = seg["end"] - seg["start"]
            self.assertAlmostEqual(length * FPS, round(length * FPS), places=3)


if __name__ == "__main__":
    unittest.main()
