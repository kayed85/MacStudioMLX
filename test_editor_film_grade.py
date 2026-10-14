#!/usr/bin/env python3
"""FILM-14 — colour: the 5-slider grade, Match colour, and Film look.

Locked here:
  * `clip_grade` / `clip_grade_is_neutral` — the model, mirroring
    `clip_brightness` exactly (absent is neutral, clamped, a sibling under
    the same `adjust` dict, no EDIT_VERSION bump).
  * `_sb_grade_term` — the render filter, chained AFTER the legacy
    `_sb_brightness_term` and never reading `adjust.brightness` (the two
    would double-apply otherwise); neutral clips add no filter at all.
  * `film_look_stack` — a whole-film preset combined with a clip's own
    grade, additive fields sum and the two 1.0-centred fields sum their
    distance from neutral.
  * `edit_to_cuts` — the grade (and a Film-look preset) reaches the plan the
    same way brightness always has, so the film render sees exactly what
    the Inspector's sliders show.
  * The AE (After Effects) export gets contrast and saturation alongside
    the existing brightness mapping, without disturbing the exact string
    the pre-existing brightness-only test still checks.
  * `_sb_match_colour_grade` and the `/storyboard/edit/match-colour` route.

Run:  ./ltx-2-mlx/env/bin/python3.11 -m pytest -q test_editor_film_grade.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as panel                                        # noqa: E402
import storyboard_editor as sedit                                    # noqa: E402


def _clip(cid, path, start, end, film_start, **kw):
    c = sedit.new_clip(path, start, end, film_start, id=cid, duration=10.0, source="human")
    c.update(kw)
    return c


def _doc(clips, **kw):
    d = {"version": sedit.EDIT_VERSION, "board_id": "sb_g", "revision": 0, "source": "human",
         "audio": None, "beats": None, "clips": clips, "settings": {}}
    d.update(kw)
    return d


def _codes(doc):
    return [e["code"] for e in sedit.validate_edit(doc)]


# =============================================================================
# THE MODEL
# =============================================================================
class TheGradeModel(unittest.TestCase):
    def test_absent_is_neutral(self):
        g = sedit.clip_grade({})
        self.assertEqual(g, {"exposure": 0.0, "contrast": 1.0, "saturation": 1.0,
                             "temp": 0.0, "tint": 0.0})
        self.assertTrue(sedit.clip_grade_is_neutral({}))

    def test_reads_and_clamps_every_field(self):
        c = {"adjust": {"exposure": 9, "contrast": 9, "saturation": 9, "temp": 9, "tint": -9}}
        g = sedit.clip_grade(c)
        self.assertEqual(g["exposure"], sedit.EXPOSURE_LIMIT)
        self.assertEqual(g["contrast"], sedit.GRADE_CONTRAST_RANGE[1])
        self.assertEqual(g["saturation"], sedit.GRADE_SATURATION_RANGE[1])
        self.assertEqual(g["temp"], sedit.GRADE_TEMP_TINT_LIMIT)
        self.assertEqual(g["tint"], -sedit.GRADE_TEMP_TINT_LIMIT)
        self.assertFalse(sedit.clip_grade_is_neutral(c))

    def test_brightness_and_exposure_are_independent_fields(self):
        c = {"adjust": {"brightness": 0.2, "exposure": 0.1}}
        self.assertEqual(sedit.clip_brightness(c), 0.2)
        self.assertEqual(sedit.clip_grade(c)["exposure"], 0.1)

    def test_clip_effects_is_the_one_accessor_for_the_grade_too(self):
        c = {"adjust": {"brightness": 0.2, "saturation": 1.4}, "fx": {"fade_in": 0.5}}
        e = sedit.clip_effects(c)
        self.assertEqual(e["brightness"], 0.2)
        self.assertEqual(e["saturation"], 1.4)
        self.assertEqual(e["fade_in"], 0.5)

    def test_validation_accepts_a_legal_grade(self):
        c = _clip("a", "/x/a.mp4", 0, 4, 0, adjust={"exposure": 0.2, "contrast": 1.3,
                                                     "saturation": 0.8, "temp": 0.3, "tint": -0.2})
        self.assertEqual(_codes(_doc([c])), [])

    def test_validation_refuses_an_out_of_range_field(self):
        c = _clip("a", "/x/a.mp4", 0, 4, 0, adjust={"contrast": 9})
        self.assertIn("clip_grade_range", _codes(_doc([c])))
        c2 = _clip("a", "/x/a.mp4", 0, 4, 0, adjust={"temp": "warm"})
        self.assertIn("clip_grade", _codes(_doc([c2])))

    def test_normalise_writes_back_clamped_and_drops_neutral_fields(self):
        c = _clip("a", "/x/a.mp4", 0, 4, 0, adjust={"contrast": 9, "saturation": 1.0, "tint": 0.0})
        norm = sedit.normalise_edit(_doc([c]))
        adj = norm["clips"][0].get("adjust") or {}
        self.assertEqual(adj.get("contrast"), sedit.GRADE_CONTRAST_RANGE[1])
        self.assertNotIn("saturation", adj)   # neutral — dropped
        self.assertNotIn("tint", adj)         # neutral — dropped

    def test_normalise_preserves_unknown_adjust_keys(self):
        # Forward-compat: a future field this code does not know about yet
        # must not be silently deleted on every save.
        c = _clip("a", "/x/a.mp4", 0, 4, 0, adjust={"exposure": 0.1, "mystery_future_field": 42})
        norm = sedit.normalise_edit(_doc([c]))
        self.assertEqual(norm["clips"][0]["adjust"].get("mystery_future_field"), 42)


# =============================================================================
# THE RENDER TERM
# =============================================================================
class TheGradeTerm(unittest.TestCase):
    def test_neutral_adds_no_filter(self):
        self.assertEqual(panel._sb_grade_term(None), "")
        self.assertEqual(panel._sb_grade_term({}), "")
        self.assertEqual(panel._sb_grade_term({"exposure": 0.0, "contrast": 1.0}), "")

    def test_exposure_contrast_saturation_share_one_eq_node(self):
        term = panel._sb_grade_term({"exposure": 0.1, "contrast": 1.2, "saturation": 0.8})
        self.assertEqual(term, "eq=brightness=0.100000:contrast=1.200000:saturation=0.800000,")

    def test_it_never_reads_legacy_brightness(self):
        # Only `brightness` set — no exposure/contrast/saturation/temp/tint —
        # must produce nothing; `_sb_brightness_term` owns that field alone.
        self.assertEqual(panel._sb_grade_term({"brightness": 0.4}), "")

    def test_temp_and_tint_ride_colorbalance_and_clamp(self):
        term = panel._sb_grade_term({"temp": 1.0, "tint": -1.0})
        self.assertIn("colorbalance=", term)
        self.assertIn("rm=0.400000", term)
        self.assertIn("bm=-0.400000", term)
        self.assertIn("gm=0.400000", term)

    def test_out_of_range_and_nan_are_clamped_not_raised(self):
        term = panel._sb_grade_term({"exposure": 999, "contrast": float("nan")})
        self.assertIn("brightness=0.500000", term)
        self.assertIn("contrast=1.000000", term)   # nan -> default (neutral)

    def test_brightness_and_grade_chain_without_double_applying(self):
        # The render-graph assignment: bright = brightness_term + grade_term.
        adjust = {"brightness": 0.1, "exposure": 0.1}
        combined = panel._sb_brightness_term(adjust) + panel._sb_grade_term(adjust)
        self.assertEqual(combined,
                         "eq=brightness=0.100000,eq=brightness=0.100000:contrast=1.000000:saturation=1.000000,")


# =============================================================================
# FILM LOOK
# =============================================================================
class TheFilmLook(unittest.TestCase):
    def test_no_preset_is_a_no_op(self):
        adj = {"exposure": 0.1}
        self.assertEqual(sedit.film_look_stack(adj, None), adj)
        self.assertEqual(sedit.film_look_stack(adj, "not_a_real_preset"), adj)

    def test_additive_fields_sum(self):
        out = sedit.film_look_stack({"temp": 0.1}, "warm_tungsten")
        self.assertAlmostEqual(out["temp"], 0.1 + sedit.FILM_LOOK_PRESETS["warm_tungsten"]["temp"])

    def test_multiplicative_fields_sum_their_distance_from_neutral(self):
        out = sedit.film_look_stack({"saturation": 1.2}, "warm_tungsten")
        # preset saturation 1.10 -> +0.10 distance from neutral
        self.assertAlmostEqual(out["saturation"], 1.2 + 0.10)

    def test_a_clip_with_no_grade_of_its_own_still_gets_the_preset(self):
        rows = panel._sbe_board_clips if False else None  # not used; direct edit_to_cuts test below
        c = _clip("a", "/x/a.mp4", 0, 4, 0)   # no adjust at all
        doc = _doc([c], settings={"film_look": "warm_tungsten"})
        plan = sedit.edit_to_cuts(doc)
        self.assertIn("adjust", plan[0])
        self.assertAlmostEqual(plan[0]["adjust"]["temp"],
                               sedit.FILM_LOOK_PRESETS["warm_tungsten"]["temp"])

    def test_a_slug_is_never_graded_by_the_preset(self):
        s = {"id": "s", "kind": "slug", "path": None, "start": 0, "end": 2, "film_start": 0,
             "film_end": 2, "source": "human", "locked": False}
        doc = _doc([s], settings={"film_look": "warm_tungsten"})
        plan = sedit.edit_to_cuts(doc)
        self.assertNotIn("adjust", plan[0])

    def test_the_plan_stays_identical_with_no_preset_selected(self):
        c = _clip("a", "/x/a.mp4", 0, 4, 0, adjust={"exposure": 0.05})
        without = sedit.edit_to_cuts(_doc([c]))
        with_none = sedit.edit_to_cuts(_doc([c], settings={"film_look": "none"}))
        self.assertEqual(without, with_none)


# =============================================================================
# THE AE (AFTER EFFECTS) EXPORT
# =============================================================================
class TheAeExport(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.mp4 = self.root / "a.mp4"
        self.mp4.write_bytes(b"v" * 64)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _probe(_p):
        return {"w": 1024, "h": 576, "duration": 10.0, "has_audio": True}

    def _clip_row(self, **adjust):
        row = {"id": "1", "path": str(self.mp4), "title": "Open",
               "start": 0.0, "end": 4.0, "film_start": 0.0, "film_end": 4.0}
        if adjust:
            row["adjust"] = adjust
        return row

    def _export(self, clips):
        return sedit.export_nle(clips, self.root, name="grade test", probe=self._probe)

    def test_brightness_only_is_unchanged_from_before_the_grade_existed(self):
        # The exact assertion test_storyboard_editor_api.py's own brightness
        # test makes — this file re-proves it is untouched by the grade work.
        jsx = Path(self._export([self._clip_row(brightness=0.25)])["jsx"]).read_text()
        self.assertIn("bright(lay, 37.5);", jsx)
        self.assertEqual(jsx.count("bright(lay,"), 1)

    def test_contrast_rides_alongside_brightness_in_one_call(self):
        c = self._clip_row(brightness=0.1, contrast=1.2)
        jsx = Path(self._export([c])["jsx"]).read_text()
        # (0.1)*150 = 15.0; (1.2-1)*150 = 30.0
        self.assertIn("bright(lay, 15.0, 30.0);", jsx)

    def test_saturation_gets_its_own_call(self):
        c = self._clip_row(saturation=1.5)
        jsx = Path(self._export([c])["jsx"]).read_text()
        self.assertIn("ADBE HUE SATURATION", jsx)
        self.assertIn("satur(lay, 50.0);", jsx)   # (1.5-1)*100

    def test_a_neutral_clip_gets_neither_call(self):
        c = self._clip_row()
        jsx = Path(self._export([c])["jsx"]).read_text()
        self.assertNotIn("bright(lay,", jsx)
        self.assertNotIn("satur(lay,", jsx)


# =============================================================================
# MATCH COLOUR
# =============================================================================
class TheMatchColourMaths(unittest.TestCase):
    def test_identical_frames_propose_nothing(self):
        got = panel._sb_match_colour_grade((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))
        self.assertEqual(got, {"exposure": 0.0, "temp": 0.0, "tint": 0.0})

    def test_a_darker_clip_gets_positive_exposure(self):
        got = panel._sb_match_colour_grade((0.3, 0.3, 0.3), (0.5, 0.5, 0.5))
        self.assertGreater(got["exposure"], 0)

    def test_a_cool_clip_toward_a_warm_reference_gets_positive_temp(self):
        # This clip is cool (low R, high B) vs a neutral reference.
        got = panel._sb_match_colour_grade((0.3, 0.4, 0.5), (0.4, 0.4, 0.4))
        self.assertGreater(got["temp"], 0)

    def test_clamped_to_the_grade_ranges(self):
        got = panel._sb_match_colour_grade((0.0, 0.0, 1.0), (1.0, 1.0, 0.0))
        self.assertLessEqual(got["exposure"], 0.5)
        self.assertLessEqual(abs(got["temp"]), 1.0)
        self.assertLessEqual(abs(got["tint"]), 1.0)


class TheMatchColourRoute(unittest.TestCase):
    def setUp(self):
        import tempfile
        from unittest import mock
        import storyboard

        from test_storyboard_editor_api import FakeHandler, _board, _edit

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.a = self.root / "a.mp4"; self.a.write_bytes(b"x")
        self.b = self.root / "b.mp4"; self.b.write_bytes(b"x")
        self.c = self.root / "c.mp4"; self.c.write_bytes(b"x")
        clips = [_clip("a", str(self.a), 0.0, 4.0, 0.0),
                _clip("b", str(self.b), 0.0, 4.0, 4.0),
                _clip("c", str(self.c), 0.0, 4.0, 8.0)]
        self.edit = sedit.normalise_edit(_edit(clips))
        bdir = self.root / "sb_g"
        bdir.mkdir()
        sedit.save_edit(bdir, self.edit)
        self.FakeHandler = FakeHandler
        self.patches = [
            mock.patch.object(panel, "STATE_DIR", self.root),
            mock.patch.object(panel, "_sbe_board_dir", return_value=bdir),
            mock.patch.object(storyboard, "load_storyboard",
                              return_value=_board([self.a, self.b, self.c])),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def _rgb_by_path(self, mapping):
        def fn(path, at):
            return mapping.get(str(path))
        return fn

    def test_proposals_exclude_the_reference_and_near_matches(self):
        from unittest import mock
        rgb = {str(self.a): (0.5, 0.5, 0.5),      # the reference
              str(self.b): (0.5, 0.5, 0.5),      # already matches -> no proposal
              str(self.c): (0.2, 0.2, 0.2)}      # darker -> proposed
        with mock.patch.object(panel, "take_frame_rgb", side_effect=self._rgb_by_path(rgb)):
            h = self.FakeHandler()
            h.post("edit/match-colour", {"id": "sb_g", "ref": "a"})
        self.assertEqual(h.status, 200, h.payload)
        ids = [p["id"] for p in h.payload["proposals"]]
        self.assertEqual(ids, ["c"])
        self.assertGreater(h.payload["proposals"][0]["exposure"], 0)

    def test_no_ref_uses_the_films_median(self):
        from unittest import mock
        rgb = {str(self.a): (0.2, 0.2, 0.2),
              str(self.b): (0.5, 0.5, 0.5),
              str(self.c): (0.8, 0.8, 0.8)}
        with mock.patch.object(panel, "take_frame_rgb", side_effect=self._rgb_by_path(rgb)):
            h = self.FakeHandler()
            h.post("edit/match-colour", {"id": "sb_g"})
        self.assertEqual(h.status, 200, h.payload)
        self.assertAlmostEqual(h.payload["reference"]["r"], 0.5)
        ids = sorted(p["id"] for p in h.payload["proposals"])
        self.assertEqual(ids, ["a", "c"])   # b IS the median, no proposal

    def test_unreadable_clips_are_skipped_not_fatal(self):
        from unittest import mock
        rgb = {str(self.a): (0.5, 0.5, 0.5), str(self.b): (0.2, 0.2, 0.2)}
        # c returns None (unreadable)
        with mock.patch.object(panel, "take_frame_rgb", side_effect=self._rgb_by_path(rgb)):
            h = self.FakeHandler()
            h.post("edit/match-colour", {"id": "sb_g", "ref": "a"})
        self.assertEqual(h.status, 200, h.payload)
        ids = [p["id"] for p in h.payload["proposals"]]
        self.assertEqual(ids, ["b"])


# =============================================================================
# THE CLIENT MODEL — sbeGrade / sbeSetGrade / sbeCopyGrade / sbeGradeCss
# =============================================================================
def run_client(body: str) -> dict:
    import json
    import subprocess as sp
    import tempfile
    from test_storyboard_editor_ui import (FUNCTIONS, NODE, SHIM,
                                           extract_function, panel_source)
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
        res = sp.run([NODE, str(path)], capture_output=True, text=True, timeout=60)
        if res.returncode:
            raise AssertionError(res.stdout + "\n" + res.stderr)
        return json.loads(res.stdout)
    finally:
        path.unlink(missing_ok=True)


class TheClientGradeModel(unittest.TestCase):
    def test_absent_is_neutral_and_matches_the_server(self):
        r = run_client("out.g = sbeGrade({});")
        self.assertEqual(r["g"], {"exposure": 0.0, "contrast": 1.0, "saturation": 1.0,
                                  "temp": 0.0, "tint": 0.0})

    def test_set_clamps_and_drops_neutral(self):
        r = run_client("""
const clips = [{ id: 'a', kind: 'video', adjust: { contrast: 1.5 } }];
sbeSetGrade(clips, 'a', 'contrast', 999);
out.after_clamp = clips[0].adjust.contrast;
sbeSetGrade(clips, 'a', 'contrast', 1.0);
out.after_neutral = clips[0].adjust;
""")
        self.assertEqual(r["after_clamp"], 1.8)
        self.assertNotIn("contrast", r.get("after_neutral") or {})

    def test_set_refuses_a_locked_clip(self):
        r = run_client("""
const clips = [{ id: 'a', kind: 'video', locked: true }];
out.r = sbeSetGrade(clips, 'a', 'exposure', 0.2);
""")
        self.assertFalse(r["r"]["ok"])
        self.assertEqual(r["r"]["why"], "locked")

    def test_copy_grade_stamps_every_other_selected_clip(self):
        r = run_client("""
const clips = [
  { id: 'a', kind: 'video', adjust: { exposure: 0.2, temp: -0.1 } },
  { id: 'b', kind: 'video' },
  { id: 'c', kind: 'video', locked: true },
];
out.r = sbeCopyGrade(clips, 'a', ['a', 'b', 'c']);
out.b = sbeGrade(sbeById(clips, 'b'));
out.c_untouched = clips[2].adjust;
""")
        self.assertTrue(r["r"]["ok"])
        self.assertEqual(r["b"]["exposure"], 0.2)
        self.assertEqual(r["b"]["temp"], -0.1)
        self.assertIsNone(r.get("c_untouched"))   # locked — skipped

    def test_grade_css_uses_multiplicative_css_functions_directly(self):
        r = run_client("""
out.css = sbeGradeCss({ adjust: { contrast: 1.2, saturation: 0.7 } });
""")
        self.assertIn("contrast(1.200)", r["css"])
        self.assertIn("saturate(0.700)", r["css"])
        self.assertNotIn("temp", r["css"])   # no CSS term for temp/tint

    def test_grade_css_folds_exposure_into_brightness(self):
        r = run_client("""
out.css = sbeGradeCss({ adjust: { brightness: 0.1, exposure: 0.1 } });
""")
        # total = 0.2 -> CSS brightness() = 1 + 2*0.2 = 1.4
        self.assertIn("brightness(1.4)", r["css"])

    def test_neutral_clip_has_no_css_at_all(self):
        r = run_client("out.css = sbeGradeCss({});")
        self.assertEqual(r["css"], "")


if __name__ == "__main__":
    unittest.main()
