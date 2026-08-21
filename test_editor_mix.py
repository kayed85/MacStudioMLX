"""The mix — one definition, read identically by the preview and the render.

WHAT THESE EXIST FOR. The renderer was a second author of the soundtrack's
level, and an invisible one: under `mode: "under"` the ffmpeg graph held every
bed at a hard-coded `volume=0.20` and then pushed it down a further ~11 dB
through a `sidechaincompress` keyed on the clips' own audio. Neither number was
in any document, on any screen, or in the preview — so the one surface the user
checks his work on played a mix the file never had.

    "when you render it, there are some weird manipulations... the volume of
     the music goes low when the dialogue appears. The mix is weird."

`docs/EDITOR_EFFECTS_MODEL.md` already decided the shape of the fix: if the
preview and the render can disagree about a gain, that gain is not in the
model. So the suite below asks the only question that settles it — does the
BROWSER compute the same number as the SERVER, for the same document, at the
same second — and it asks it by running the panel's real JavaScript in node
against the real Python, rather than by grepping either for a promising string.
"""

import json
import math
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import mlx_ltx_panel as panel
import storyboard_editor as se
from test_storyboard_editor_ui import (FUNCTIONS, NODE, SHIM, extract_function,
                                       panel_source)

ROOT = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Running the panel's own client model, for real
# ---------------------------------------------------------------------------
def run_js(body: str) -> dict:
    """Evaluate `body` with every extracted client function in scope.

    The same harness `test_storyboard_editor_ui` uses, and for the same
    reason: `scripts/extract_panel_js.py` RAISES on a renamed function, so a
    client model that quietly drifts away from the server's turns this red
    instead of turning the preview into a liar.
    """
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
    src = panel_source()
    # THE CONSTANTS COME OUT OF THE PANEL, NOT OUT OF A COPY HERE. A shim that
    # restated `SBE_MIX_DUCK_GAIN` would be a third place for one number to
    # live, and this file exists because two was already one too many.
    consts = "\n".join(m + ";" for m in re.findall(
        r"^const SBE_MIX_\w+ = [^;]+", src, re.MULTILINE))
    assert consts, "the panel declares no SBE_MIX_* constants"
    script = (SHIM + consts + "\n"
              + "\n".join(extract_function(n, src) for n in FUNCTIONS)
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


def _clip(path, film_start, dur, **extra):
    c = {"path": path, "start": 0.0, "end": dur, "source": "human",
         "film_start": film_start, "film_end": film_start + dur}
    c.update(extra)
    return c


def bed_only():
    """A film whose clips carry NO sound: nothing for the duck to key on."""
    return {"version": 2,
            "clips": [_clip("/x/a.mp4", 0.0, 4.0, has_audio=False),
                      _clip("/x/b.mp4", 4.0, 4.0, has_audio=False)],
            "audio": {"path": "/x/song.mp3", "duration": 30.0,
                      "mode": "under", "mix": {"bed_gain": 0.5, "duck": True}}}


def dialogue_only():
    """Sound under every shot, back to back — the duck is on for all of it."""
    return {"version": 2,
            "clips": [_clip("/x/a.mp4", 0.0, 4.0),
                      _clip("/x/b.mp4", 4.0, 4.0)],
            "audio": {"path": "/x/song.mp3", "duration": 30.0,
                      "mode": "under", "mix": {"bed_gain": 0.5, "duck": True}}}


def overlapping():
    """Sound, then a hole, then sound: the duck goes down, up, and down."""
    return {"version": 2,
            "clips": [_clip("/x/a.mp4", 0.0, 3.0),
                      _clip("/x/b.mp4", 5.0, 3.0, has_audio=False),
                      _clip("/x/c.mp4", 8.0, 3.0)],
            "audio": {"path": "/x/song.mp3", "duration": 30.0,
                      "mode": "under", "mix": {"bed_gain": 0.8, "duck": True}}}


def authored():
    """A bed the user has shaped himself. His envelope, nothing else."""
    return {"version": 2,
            "clips": [_clip("/x/a.mp4", 0.0, 4.0),
                      _clip("/x/b.mp4", 4.0, 4.0)],
            "audio": {"path": "/x/song.mp3", "duration": 30.0, "mode": "under",
                      "mix": {"bed_gain": 0.9, "duck": True},
                      "afx": {"fade_in": 1.5, "fade_out": 3.0,
                              "points": [[10.0, 0.4], [18.0, 1.0]]}}}


CASES = {"bed_only": bed_only(), "dialogue_only": dialogue_only(),
         "overlapping": overlapping(), "authored": authored(),
         "default": {"version": 2, "clips": [_clip("/x/a.mp4", 0.0, 4.0)],
                     "audio": {"path": "/x/song.mp3", "duration": 30.0,
                               "mode": "under"}},
         "moved": {"version": 2,
                   "clips": [_clip("/x/a.mp4", 0.0, 6.0)],
                   "audio": {"path": "/x/song.mp3", "duration": 30.0,
                             "mode": "under", "offset": -2.0,
                             "trim_start": 0.0,
                             "mix": {"bed_gain": 0.6, "duck": True}}}}

# Where the two are asked. Deliberately not on the breakpoints alone: an
# agreement that only holds at the knots is an agreement about a list, not
# about a curve.
PROBES = [0.0, 0.004, 0.02, 0.5, 2.9, 3.0, 3.2, 3.4, 4.0, 5.0, 7.5, 8.0,
          10.25, 11.0, 12.0, 15.0, 19.9, 25.0]


class ThePreviewAndTheRenderComputeTheSameMix(unittest.TestCase):
    """THE HEADLINE INVARIANT. Everything else in this file supports it."""

    @classmethod
    def setUpClass(cls) -> None:
        body = """
const CASES = %s;
const PROBES = %s;
out.curves = {}; out.at = {}; out.len = {}; out.suppressed = {};
for (const [name, edit] of Object.entries(CASES)) {
  // THE FILM'S OWN CLOCK, computed from the same document the server reads —
  // not handed in from a probe the render has never seen. See `sbeBedLen`.
  const dur = sbeFilmDuration(edit.clips);
  out.curves[name] = sbeBedGainPoints(edit.audio, edit.clips, dur);
  out.len[name] = sbeBedLen(edit.audio, dur);
  out.suppressed[name] = sbeBedDuckSuppressed(edit.audio, dur);
  out.at[name] = PROBES.map(t => sbeBedGainAt(edit.audio, edit.clips, dur, t));
}
out.mix = {
  absent: sbeAudioMix({}),
  partial: sbeAudioMix({ mix: { duck: true } }),
  clamped: sbeAudioMix({ mix: { bed_gain: 4 } }),
};
""" % (json.dumps(CASES), json.dumps(PROBES))
        cls.js = run_js(body)

    def test_the_curve_the_browser_draws_is_the_curve_the_render_applies(self):
        for name, edit in CASES.items():
            with self.subTest(case=name):
                self.assertEqual(
                    [[round(t, 6), round(g, 6)]
                     for t, g in self.js["curves"][name]],
                    se.bed_gain_points(edit),
                    f"{name}: the client and the server disagree about the bed")

    def test_they_agree_at_every_second_and_not_only_at_the_knots(self):
        for name, edit in CASES.items():
            with self.subTest(case=name):
                want = [se.bed_gain_at(edit, t) for t in PROBES]
                got = [round(v, 6) for v in self.js["at"][name]]
                for t, a, b in zip(PROBES, got, want):
                    self.assertAlmostEqual(
                        a, b, places=5,
                        msg=f"{name} @ {t}s: preview {a} vs render {b}")

    def test_they_agree_about_how_long_the_bed_is(self):
        # The envelope's clock. If these differ, every fade is drawn in one
        # place and rendered in another.
        for name, edit in CASES.items():
            with self.subTest(case=name):
                self.assertAlmostEqual(
                    self.js["len"][name],
                    se.bed_length(edit["audio"], se.edit_duration(edit)),
                    places=5)

    def test_they_agree_about_when_the_duck_stands_down(self):
        for name, edit in CASES.items():
            with self.subTest(case=name):
                self.assertEqual(self.js["suppressed"][name],
                                 se.bed_duck_suppressed(edit))

    def test_the_accessor_reads_the_same_defaults_on_both_sides(self):
        self.assertEqual(self.js["mix"]["absent"],
                         se.audio_mix({}))
        self.assertEqual(self.js["mix"]["partial"],
                         se.audio_mix({"mix": {"duck": True}}))
        # Out of range is CLAMPED, not refused: a slider cannot produce this,
        # but a hand-edited document can, and a gain of 4 is a blown mix.
        self.assertEqual(self.js["mix"]["clamped"],
                         se.audio_mix({"mix": {"bed_gain": 4}}))
        self.assertEqual(se.audio_mix({"mix": {"bed_gain": 4}})["bed_gain"], 1.0)


class TheConstantsCannotDrift(unittest.TestCase):
    """A mirrored constant is a mirror, and a mirror is a thing that drifts."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.src = panel_source()

    def _js_const(self, name: str) -> float:
        m = re.search(rf"const {name} = ([-\d.]+|true|false);", self.src)
        self.assertIsNotNone(m, f"{name} is not declared in the panel")
        v = m.group(1)
        return {"true": True, "false": False}.get(v, None) or float(v)

    def test_the_client_and_the_server_agree_about_the_mix_constants(self):
        self.assertEqual(self._js_const("SBE_MIX_BED_GAIN"), se.MIX_BED_GAIN)
        self.assertEqual(self._js_const("SBE_MIX_DUCK_GAIN"), se.MIX_DUCK_GAIN)
        self.assertEqual(self._js_const("SBE_MIX_DUCK_ATTACK"),
                         se.MIX_DUCK_ATTACK)
        self.assertEqual(self._js_const("SBE_MIX_DUCK_RELEASE"),
                         se.MIX_DUCK_RELEASE)
        self.assertIn("const SBE_MIX_DUCK = false;", self.src)
        self.assertIs(se.MIX_DUCK, False)

    def test_the_panel_and_the_model_agree_about_the_ceiling(self):
        # The panel keeps a last-resort literal so it can boot without the
        # editor module — shipping an UNLIMITED mix would be the worse of the
        # two failures — but the value it USES comes from the model.
        self.assertEqual(panel._sb_mix_ceiling(), se.MIX_CEILING)
        self.assertEqual(panel.SB_MIX_CEILING_FALLBACK, se.MIX_CEILING)

    def test_the_duck_depth_is_the_measured_one(self):
        # 11.4 dB is what the gated-tone rig measured for the compressor this
        # replaces (0.02 / 10), and the envelope reproduces that depth rather
        # than a rounder number somebody preferred.
        self.assertAlmostEqual(20 * math.log10(se.MIX_DUCK_GAIN), -11.4,
                               places=1)


class TheRenderAppliesExactlyWhatTheDocumentSays(unittest.TestCase):
    """The other half of the equality: the ffmpeg expression, evaluated."""

    @staticmethod
    def _volume_expr(edit) -> str:
        term = panel._sb_volume_term(se.bed_render_gain(edit))
        m = re.search(r"volume=volume='(.+)':eval=frame,", term)
        return m.group(1) if m else ""

    @staticmethod
    def _eval(expr: str, t: float) -> float:
        """ffmpeg's own expression, evaluated. Not a re-derivation of it.

        The generated grammar is exactly `if(lt(t,X),A,B)` over arithmetic, so
        rewriting `if(` to a strict three-argument call and `lt` to a
        comparison evaluates the STRING the renderer will hand ffmpeg — which
        is the thing under test. Both branches are arithmetic with a divisor
        `_sb_volume_term` has already refused to make zero, so evaluating them
        eagerly is safe.
        """
        return eval(expr.replace("if(", "IF("),                  # noqa: S307
                    {"__builtins__": {}},
                    {"IF": lambda c, a, b: a if c else b,
                     "lt": lambda a, b: a < b, "t": t})

    def test_the_expression_ffmpeg_gets_is_the_curve_the_preview_played(self):
        for name, edit in CASES.items():
            expr = self._volume_expr(edit)
            if not expr:
                # A unity bed adds NO filter, which is the neutral-is-absent
                # rule: the graph for a film nobody mixed is byte-identical to
                # one from before the mix was a control.
                self.assertEqual(se.bed_gain_points(edit), [], name)
                continue
            delay = se.music_window(edit["audio"])["delay"]
            n = se.bed_length(edit["audio"], se.edit_duration(edit))
            with self.subTest(case=name):
                for t in [p for p in PROBES if p <= n]:
                    self.assertAlmostEqual(
                        self._eval(expr, t + delay),
                        se.bed_gain_at(edit, t), places=5,
                        msg=f"{name} @ {t}s on the bed's clock")

    def test_an_authored_envelope_reaches_the_render_exactly(self):
        # The field the graph always read and NOTHING ever set: a fade on the
        # soundtrack was expressible in the model, drawn on the strip, and
        # absent from the file.
        edit = authored()
        expr = self._volume_expr(edit)
        self.assertTrue(expr)
        n = se.bed_length(edit["audio"])
        # The fade-in ramps from silence, the fade-out lands on it, and the
        # authored dip at 10 s is 0.4 of the fader.
        self.assertAlmostEqual(self._eval(expr, 0.0), 0.0, places=5)
        self.assertAlmostEqual(self._eval(expr, n), 0.0, places=5)
        self.assertAlmostEqual(self._eval(expr, 10.0), 0.4 * 0.9, places=4)
        self.assertAlmostEqual(self._eval(expr, 18.0), 1.0 * 0.9, places=4)

    def test_the_render_stops_inventing_a_bed_gain_of_its_own(self):
        graph, _ = panel._sb_film_filtergraph(
            [], 1024, 576, 24, "yuv420p",
            music={"path": "/x/song.mp3", "start": 0.0, "mode": "under"},
            segments=[{"kind": "slug", "input": None, "info": None,
                       "window": None, "adjust": None, "duration": 4.0}])
        self.assertNotIn("sidechaincompress", graph)
        self.assertNotIn("volume=0.20", graph)

    def test_a_document_nobody_mixed_builds_the_graph_it_always_did(self):
        edit = {"version": 2, "clips": [_clip("/x/a.mp4", 0.0, 4.0)],
                "audio": {"path": "/x/song.mp3", "duration": 30.0,
                          "mode": "replace"}}
        self.assertEqual(se.bed_render_gain(edit), [])
        self.assertEqual(panel._sb_volume_term(se.bed_render_gain(edit)), "")


class TheDefaultsAreSettledAndTheHealIsOneTime(unittest.TestCase):
    def test_a_new_timeline_plays_the_track_and_leaves_it_alone(self):
        self.assertEqual(se.MIX_BED_GAIN, 1.0)
        self.assertIs(se.MIX_DUCK, False)
        fresh = {"version": 2, "clips": [], "audio": {"path": "/x/s.mp3"}}
        self.assertEqual(se.audio_mix(fresh["audio"]),
                         {"bed_gain": 1.0, "duck": False})

    def test_an_existing_under_film_keeps_the_levels_it_was_approved_at(self):
        doc = {"version": 2, "clips": [],
               "audio": {"path": "/x/s.mp3", "mode": "under"}}
        got = se.migrate_edit(doc)
        self.assertEqual(got["audio"]["mix"],
                         {"bed_gain": se.MIX_LEGACY_BED_GAIN, "duck": True})
        self.assertEqual(got["mix_repair"], se.MIX_REPAIR_VERSION)
        self.assertTrue(got["repaired_mix"])
        # The old renderer's own number, so the film is the film.
        self.assertEqual(se.MIX_LEGACY_BED_GAIN, 0.2)

    def test_a_replace_film_is_left_completely_alone(self):
        # `replace` never touched the bed — no gain term, no compressor — so
        # stamping one would invent an attenuation that path never had.
        doc = {"version": 2, "clips": [],
               "audio": {"path": "/x/s.mp3", "mode": "replace"}}
        got = se.migrate_edit(doc)
        self.assertNotIn("mix", got["audio"])
        self.assertNotIn("repaired_mix", got)

    def test_a_film_with_no_soundtrack_is_left_alone(self):
        got = se.migrate_edit({"version": 2, "clips": [], "audio": None})
        self.assertIsNone(got["audio"])

    def test_THE_HEAL_NEVER_RUNS_TWICE_OVER_A_DECISION(self):
        # THE DEFECT THE MARKER EXISTS FOR. `normalise_edit` drops a mix that
        # is back at the default, so without the marker a user who switched
        # the duck OFF would have the next READ helpfully put it back — the
        # rival author, wearing a repair's clothes.
        doc = se.migrate_edit({"version": 2, "clips": [],
                               "audio": {"path": "/x/s.mp3", "mode": "under"}})
        doc["audio"]["mix"] = {"bed_gain": 1.0, "duck": False}
        saved = se.normalise_edit(doc)
        self.assertNotIn("mix", saved["audio"])       # neutral is absent
        self.assertEqual(saved["mix_repair"], se.MIX_REPAIR_VERSION)
        again = se.migrate_edit(saved)
        self.assertNotIn("mix", again["audio"])       # and it STAYS off

    def test_the_read_does_not_mutate_the_document_it_was_given(self):
        # `pending_backup` compares raw JSON on both sides, so a read that
        # edits its argument in place makes the file and the snapshot differ
        # over a repair neither of them contains — which resurrects the
        # permanent interrogation `docs/EDITOR_SAVE_MODEL.md` exists to kill.
        doc = {"version": 2, "clips": [_clip("/x/a.mp4", 0.0, 4.0)],
               "audio": {"path": "/x/s.mp3", "mode": "under"}}
        before = json.dumps(doc, sort_keys=True)
        se.migrate_edit(doc)
        self.assertEqual(json.dumps(doc, sort_keys=True), before)
        self.assertNotIn("mix", doc["audio"])

    def test_the_marker_is_not_a_content_difference(self):
        # A field the user cannot see may never be the reason they are asked a
        # question — invariant 3 of the save model.
        a = {"version": 2, "clips": [], "audio": {"path": "/x/s.mp3"}}
        b = dict(a, mix_repair=se.MIX_REPAIR_VERSION, repaired_mix=True)
        self.assertEqual(se.edit_digest(a), se.edit_digest(b))

    def test_but_the_mix_ITSELF_is_content(self):
        a = {"version": 2, "clips": [], "audio": {"path": "/x/s.mp3"}}
        b = {"version": 2, "clips": [],
             "audio": {"path": "/x/s.mp3", "mix": {"bed_gain": 0.3}}}
        self.assertNotEqual(se.edit_digest(a), se.edit_digest(b))


class TheMixSurvivesTheRoundTrip(unittest.TestCase):
    def test_duck_on_and_off_round_trip_through_save_and_load(self):
        import tempfile as _tf
        for duck, gain in ((True, 0.35), (False, 1.0), (True, 1.0),
                           (False, 0.5)):
            with self.subTest(duck=duck, gain=gain):
                with _tf.TemporaryDirectory() as d:
                    doc = {"version": 2,
                           "clips": [_clip("/x/a.mp4", 0.0, 4.0)],
                           "audio": {"path": "/x/s.mp3", "duration": 30.0,
                                     "mode": "under",
                                     "mix": {"bed_gain": gain, "duck": duck}},
                           # The marker rides along so the heal cannot be what
                           # answers this question instead of the round trip.
                           "mix_repair": se.MIX_REPAIR_VERSION}
                    se.save_edit(d, doc)
                    back = se.load_edit(d)
                    self.assertEqual(se.audio_mix(back["audio"]),
                                     {"bed_gain": gain, "duck": duck})

    def test_a_bed_envelope_round_trips_through_save_and_load(self):
        import tempfile as _tf
        with _tf.TemporaryDirectory() as d:
            doc = {"version": 2, "clips": [_clip("/x/a.mp4", 0.0, 4.0)],
                   "audio": {"path": "/x/s.mp3", "duration": 30.0,
                             "mode": "under",
                             "afx": {"fade_in": 1.0, "fade_out": 2.0,
                                     "points": [[12.0, 0.3]]}},
                   "mix_repair": se.MIX_REPAIR_VERSION}
            se.save_edit(d, doc)
            back = se.load_edit(d)
            self.assertEqual(back["audio"]["afx"],
                             {"fade_in": 1.0, "fade_out": 2.0,
                              "points": [[12.0, 0.3]]})

    def test_a_bad_mix_is_refused_with_a_sentence(self):
        for mix, code in (({"bed_gain": "loud"}, "audio_mix_bed_gain"),
                          ({"bed_gain": 3}, "audio_mix_bed_gain_range"),
                          ({"duck": "yes"}, "audio_mix_duck")):
            doc = {"version": 2, "clips": [],
                   "audio": {"path": "/x/s.mp3", "mix": mix}}
            codes = [e["code"] for e in se.validate_edit(doc)]
            self.assertIn(code, codes, mix)


class ThePrecedenceIsOneCurveAtATime(unittest.TestCase):
    """THE RULE: the user's envelope wins, and the duck stands down."""

    def test_an_authored_envelope_switches_the_auto_duck_off(self):
        edit = authored()
        self.assertTrue(se.audio_mix(edit["audio"])["duck"])
        self.assertTrue(se.bed_duck_suppressed(edit))
        # The curve is the fader times the ENVELOPE, with no duck in it. At
        # 6 s the second clip's sound is playing and the bed does NOT step
        # back: the value is the envelope's own (0.4, held before its first
        # point) times the fader, and 0.269 appears nowhere in it.
        self.assertAlmostEqual(se.bed_gain_at(edit, 6.0), 0.4 * 0.9, places=4)
        with_duck = 0.4 * 0.9 * se.MIX_DUCK_GAIN
        self.assertNotAlmostEqual(se.bed_gain_at(edit, 6.0), with_duck,
                                  places=4)

    def test_two_curves_are_never_multiplied_together(self):
        # The proof is arithmetic: every value on an authored bed's curve is a
        # value on the envelope times the fader, and the duck's depth (0.269)
        # appears nowhere in it.
        edit = authored()
        n = se.bed_length(edit["audio"])
        env = se.audio_gain_points(edit["audio"], n)
        got = dict(se.bed_gain_points(edit))
        for t, g in env:
            self.assertAlmostEqual(got[t], g * 0.9, places=5)

    def test_the_fader_is_a_scalar_and_composes_with_everything(self):
        edit = dialogue_only()
        edit["audio"]["mix"] = {"bed_gain": 1.0, "duck": True}
        full = se.bed_gain_at(edit, 2.0)
        edit["audio"]["mix"] = {"bed_gain": 0.5, "duck": True}
        half = se.bed_gain_at(edit, 2.0)
        self.assertAlmostEqual(half, full * 0.5, places=5)

    def test_the_duck_is_keyed_on_the_document_and_not_on_the_samples(self):
        # A muted clip and a clip with no audio track contribute NO lane to
        # the render, so neither may duck anything.
        edit = dialogue_only()
        self.assertEqual(se.audible_strips(edit), [[0.0, 8.0]])
        edit["clips"][1]["mute"] = True
        self.assertEqual(se.audible_strips(edit), [[0.0, 4.0]])
        edit["clips"][0]["has_audio"] = False
        self.assertEqual(se.audible_strips(edit), [])

    def test_lines_closer_than_one_release_do_not_pump(self):
        edit = {"version": 2,
                "clips": [_clip("/x/a.mp4", 0.0, 3.0),
                          _clip("/x/b.mp4", 3.2, 3.0)],
                "audio": {"path": "/x/s.mp3", "duration": 30.0, "mode": "under",
                          "mix": {"duck": True}}}
        # 0.2 s apart, which is inside the 0.4 s release: one window, so the
        # bed does not scramble back up and down between them.
        self.assertEqual(se.audible_strips(edit), [[0.0, 6.2]])
        self.assertAlmostEqual(se.bed_gain_at(edit, 3.1), se.MIX_DUCK_GAIN,
                               places=4)

    def test_the_bed_is_at_FULL_level_where_nothing_is_speaking(self):
        edit = overlapping()
        # The hole between 5 s and 8 s, one release after the first window.
        self.assertAlmostEqual(se.bed_gain_at(edit, 6.0), 0.8, places=4)
        # ...and down again under the third shot.
        self.assertAlmostEqual(se.bed_gain_at(edit, 9.0),
                               0.8 * se.MIX_DUCK_GAIN, places=4)


class TheNLEExportStillShipsStems(unittest.TestCase):
    """An NLE gets raw stems plus automation. That is deliberate."""

    def test_the_soundtrack_clipitem_carries_no_gain_and_no_duck(self):
        edit = dialogue_only()
        xml = se.fcp7_xml(
            [], name="film", media={"/x/song.mp3": "song.mp3"},
            width=1024, height=576, base="/tmp/p", audio=edit["audio"])
        # The bed is there, at its own level, with nothing done to it: the
        # under-mix has no representation in an NLE's timeline, so baking it
        # in would hand an editor a bed they cannot unmix or re-balance.
        self.assertIn('clipitem-music', xml)
        self.assertNotIn("sidechain", xml.lower())
        head, _, _ = xml.partition('clipitem-music')
        tail = xml[len(head):]
        self.assertNotIn("<name>Audio Levels</name>", tail)
        self.assertNotIn("audiolevels", tail.lower())

    def test_the_bed_is_still_placed_at_the_window_the_render_uses(self):
        # Stems undducked is not stems misplaced.
        aud = {"path": "/x/s.mp3", "duration": 30.0, "offset": 0.0,
               "trim_start": 4.0, "mix": {"bed_gain": 0.2, "duck": True}}
        xml = se.fcp7_xml([], name="film", media={"/x/s.mp3": "s.mp3"},
                          width=1024, height=576, base="/tmp/p", audio=aud)
        win = se.music_window(aud)
        self.assertGreater(win["start"], 0)
        self.assertIn("clipitem-music", xml)


# ---------------------------------------------------------------------------
# THE TABLE. One row per SHAPE a document can have, not one per feature.
# ---------------------------------------------------------------------------
# WHY THIS EXISTS ON TOP OF EVERYTHING ABOVE. The mix is two implementations —
# `storyboard_editor.bed_gain_points` and the panel's `sbeBedGainPoints`,
# function for function — and the suite written to keep them together compared
# the five CONSTANTS and four hand-built documents. It never compared the
# ARITHMETIC over the shapes a real edit.json takes, so a divergence lived
# through it: a bed with no `duration` and no `trim_end` made the server's
# `bed_length` return 0, which made its curve EMPTY, which means "no filter" —
# the render played the bed at unity over the dialogue while the browser drew
# it 19.6 dB lower and the preview played what it drew.
#
# So the gate is a TABLE, and the rule for adding to it is "a shape a document
# can have", not "a feature somebody wrote". Both sides are handed the same
# JSON and nothing else — in particular the client is NOT handed the peaks
# probe, because the renderer cannot see peaks.json and a term only one side
# can compute is how the two came apart in the first place.
def _t(clips, audio, **extra):
    doc = {"version": 2, "clips": clips, "audio": audio}
    doc.update(extra)
    return doc


def _under(**fields):
    a = {"path": "/x/song.mp3", "mode": "under",
         "mix": {"bed_gain": 0.35, "duck": True}}
    a.update(fields)
    return a


_SPEAKING = [_clip("/x/a.mp4", 0.0, 3.0), _clip("/x/b.mp4", 3.0, 3.0)]

TABLE = {
    # THE MEASURED DEFECT. No length anywhere on the bed: the render used to
    # answer this with an empty curve, which is the bed at FULL level.
    "no_duration": _t(_SPEAKING, _under()),
    # ...and the same hole with the block moved, so the fallback is exercised
    # as "what is left of the film after the bed starts" rather than as "the
    # film's length", which are different numbers the moment offset is not 0.
    "no_duration_late": _t(_SPEAKING, _under(offset=-2.0)),
    "no_duration_head_trim": _t(_SPEAKING, _under(trim_start=4.0)),
    "no_duration_authored": _t(_SPEAKING, _under(
        afx={"fade_in": 0.5, "fade_out": 1.0, "points": [[2.0, 0.6]]})),
    "no_duration_no_clips": _t([], _under()),
    # NO TRIM_END, which is the ordinary document: a bed that plays to the end
    # of the track.
    "no_trim_end": _t(_SPEAKING, _under(duration=30.0)),
    "bed_longer_than_film": _t(_SPEAKING, _under(duration=45.0)),
    "bed_shorter_than_film": _t(_SPEAKING, _under(duration=4.0)),
    "bed_exactly_the_film": _t(_SPEAKING, _under(duration=6.0)),
    # TRIMS: both ends, one end, a handle dragged back out past the end of the
    # track (which is not a trim), and a pair that closes the window entirely.
    "trimmed_both_ends": _t(_SPEAKING, _under(duration=30.0, trim_start=5.0,
                                              trim_end=20.0)),
    "trim_end_past_the_track": _t(_SPEAKING, _under(duration=30.0,
                                                    trim_end=30.0)),
    "trims_close_the_window": _t(_SPEAKING, _under(duration=30.0,
                                                   trim_start=12.0,
                                                   trim_end=9.0)),
    "moved_negative_offset": _t(_SPEAKING, _under(duration=30.0, offset=-2.5)),
    "moved_positive_offset": _t(_SPEAKING, _under(duration=30.0, offset=3.0)),
    # THE AUTHORED ENVELOPE, which outranks the duck — with and without a
    # stated length, because the fade-out anchors on the bed's LAST second and
    # that is precisely the number the two sides used to disagree about.
    "authored_fades": _t(_SPEAKING, _under(
        duration=30.0, afx={"fade_in": 1.5, "fade_out": 3.0})),
    "authored_points": _t(_SPEAKING, _under(
        duration=30.0, afx={"points": [[2.0, 0.2], [9.0, 1.0]]})),
    "authored_everything": _t(_SPEAKING, _under(
        duration=30.0, mix={"bed_gain": 0.9, "duck": True},
        afx={"fade_in": 1.0, "fade_out": 2.0,
             "points": [[4.0, 0.4], [12.0, 0.8]]})),
    # THE DUCK, ON AND OFF, and the fader on its own.
    "duck_off": _t(_SPEAKING, _under(duration=30.0,
                                     mix={"bed_gain": 0.35, "duck": False})),
    "duck_on_fader_unity": _t(_SPEAKING, _under(duration=30.0,
                                                mix={"bed_gain": 1.0,
                                                     "duck": True})),
    "fader_only": _t(_SPEAKING, _under(duration=30.0,
                                       mix={"bed_gain": 0.5, "duck": False})),
    "fader_at_zero": _t(_SPEAKING, _under(duration=30.0,
                                          mix={"bed_gain": 0.0, "duck": True})),
    "no_mix_block_at_all": _t(_SPEAKING, {"path": "/x/song.mp3",
                                          "duration": 30.0, "mode": "under"}),
    # WHAT THE DUCK IS KEYED ON. A muted clip and a clip with no audio track
    # are both silence, and neither may duck anything.
    "one_clip_muted": _t([_clip("/x/a.mp4", 0.0, 3.0),
                          _clip("/x/b.mp4", 3.0, 3.0, mute=True)],
                         _under(duration=30.0)),
    "all_clips_muted": _t([_clip("/x/a.mp4", 0.0, 3.0, mute=True),
                           _clip("/x/b.mp4", 3.0, 3.0, mute=True)],
                          _under(duration=30.0)),
    "one_clip_has_no_audio": _t([_clip("/x/a.mp4", 0.0, 3.0, has_audio=False),
                                 _clip("/x/b.mp4", 3.0, 3.0)],
                                _under(duration=30.0)),
    "no_clip_has_audio": _t([_clip("/x/a.mp4", 0.0, 3.0, has_audio=False),
                             _clip("/x/b.mp4", 3.0, 3.0, has_audio=False)],
                            _under(duration=30.0)),
    "muted_and_silent_and_no_duration": _t(
        [_clip("/x/a.mp4", 0.0, 3.0, mute=True),
         _clip("/x/b.mp4", 3.0, 3.0, has_audio=False)], _under()),
    # HOLES, so the duck goes down, up and down again — and a pair of lines
    # closer together than one release, which must NOT pump.
    "a_hole_between_lines": _t([_clip("/x/a.mp4", 0.0, 3.0),
                                _clip("/x/b.mp4", 5.0, 3.0, has_audio=False),
                                _clip("/x/c.mp4", 8.0, 3.0)],
                               _under(duration=30.0)),
    "lines_inside_one_release": _t([_clip("/x/a.mp4", 0.0, 3.0),
                                    _clip("/x/b.mp4", 3.2, 3.0)],
                                   _under(duration=30.0)),
    # A J-CUT: the sound's own window, ahead of its picture.
    "a_j_cut": _t([_clip("/x/a.mp4", 0.0, 3.0),
                   _clip("/x/b.mp4", 3.0, 3.0,
                         audio={"start": 0.0, "end": 3.0, "film_start": 2.0})],
                  _under(duration=30.0)),
    # AND THE DOCUMENTS THAT HAVE NO MIX TO COMPUTE. Both sides must produce
    # the same NOTHING, or a film nobody mixed stops building the graph it
    # always did.
    "replace_mode": _t(_SPEAKING, {"path": "/x/song.mp3", "duration": 30.0,
                                   "mode": "replace"}),
    "no_soundtrack": _t(_SPEAKING, None),
    "soundtrack_without_a_path": _t(_SPEAKING, {"duration": 30.0,
                                                "mode": "under"}),
    "an_empty_film": _t([], _under(duration=30.0)),
}

# Asked BETWEEN the knots as well as on them: an agreement that only holds at
# the breakpoints is an agreement about a list, not about a curve.
TABLE_PROBES = [0.0, 0.001, 0.005, 0.01, 0.25, 1.0, 1.9, 2.0, 2.1, 2.9, 3.0,
                3.05, 3.4, 3.9, 4.0, 5.5, 5.999, 6.0, 6.4, 8.0, 11.0, 15.0,
                19.999, 20.0, 25.0, 44.0]


class TheTwoImplementationsAgreeOverEveryShapeOfDocument(unittest.TestCase):
    """THE GATE THE DIVERGENCE GOT PAST. Real node, real Python, one table."""

    @classmethod
    def setUpClass(cls) -> None:
        body = """
const TABLE = %s;
const PROBES = %s;
out.film = {}; out.len = {}; out.curves = {}; out.at = {}; out.suppressed = {};
for (const [name, edit] of Object.entries(TABLE)) {
  const a = edit.audio || {};
  // EVERY INPUT COMES OUT OF THE DOCUMENT. The film's clock is derived here
  // exactly as the server derives it, so neither side is handed a number the
  // other could not have worked out for itself.
  const film = sbeFilmDuration(edit.clips);
  out.film[name] = film;
  out.len[name] = sbeBedLen(a, film);
  out.curves[name] = sbeBedGainPoints(a, edit.clips, film);
  out.suppressed[name] = sbeBedDuckSuppressed(a, film);
  out.at[name] = PROBES.map(t => sbeBedGainAt(a, edit.clips, film, t));
}
""" % (json.dumps(TABLE), json.dumps(TABLE_PROBES))
        cls.js = run_js(body)

    def test_they_agree_about_the_films_own_clock(self):
        # The fallback is only as good as the number it falls back TO.
        for name, edit in TABLE.items():
            with self.subTest(case=name):
                self.assertAlmostEqual(self.js["film"][name],
                                       se.edit_duration(edit), places=5)

    def test_they_agree_about_how_long_the_bed_is(self):
        for name, edit in TABLE.items():
            with self.subTest(case=name):
                self.assertAlmostEqual(
                    self.js["len"][name],
                    se.bed_length(edit["audio"], se.edit_duration(edit)),
                    places=5)

    def test_THE_CURVES_ARE_IDENTICAL_POINT_FOR_POINT(self):
        for name, edit in TABLE.items():
            with self.subTest(case=name):
                self.assertEqual(
                    [[round(t, 6), round(g, 6)]
                     for t, g in self.js["curves"][name]],
                    se.bed_gain_points(edit),
                    f"{name}: the browser and the render disagree about the bed")

    def test_they_agree_at_every_second_and_not_only_at_the_knots(self):
        for name, edit in TABLE.items():
            want = [se.bed_gain_at(edit, t) for t in TABLE_PROBES]
            got = [round(v, 6) for v in self.js["at"][name]]
            with self.subTest(case=name):
                for t, a, b in zip(TABLE_PROBES, got, want):
                    self.assertAlmostEqual(
                        a, b, places=5,
                        msg=f"{name} @ {t}s: preview {a} vs render {b}")

    def test_they_agree_about_when_the_duck_stands_down(self):
        for name, edit in TABLE.items():
            with self.subTest(case=name):
                self.assertEqual(self.js["suppressed"][name],
                                 se.bed_duck_suppressed(edit))

    def test_THE_FFMPEG_EXPRESSION_IS_THE_CURVE_THE_BROWSER_DREW(self):
        """The other half of the equality, and the one the ear hears.

        Not "the model agrees with the model" but "the string handed to ffmpeg,
        evaluated, is the number the browser drew" — which is what makes this a
        measurement of the render rather than a re-derivation of it.
        """
        ev = TheRenderAppliesExactlyWhatTheDocumentSays()
        for name, edit in TABLE.items():
            expr = ev._volume_expr(edit)
            curve = self.js["curves"][name]
            if not expr:
                # NEUTRAL IS ABSENT: no filter is the honest graph for a bed
                # nobody moved, and both sides have to say so together.
                self.assertEqual(curve, [], name)
                self.assertEqual(se.bed_gain_points(edit), [], name)
                continue
            self.assertTrue(curve, f"{name}: the render filters, the browser "
                                   f"draws unity — that is the defect")
            delay = se.music_window(edit["audio"])["delay"]
            n = se.bed_length(edit["audio"], se.edit_duration(edit))
            with self.subTest(case=name):
                for t, g in [(t, g) for t, g in curve if t <= n]:
                    self.assertAlmostEqual(
                        ev._eval(expr, t + delay), g, places=5,
                        msg=f"{name} @ {t}s: ffmpeg plays "
                            f"{ev._eval(expr, t + delay)}, the browser drew {g}")


class TheBedWithNoLengthPlaysUNDERTheFilm(unittest.TestCase):
    """The ruling, stated as an assertion rather than as a comment.

    A bed whose document says nothing about how long the track is used to
    render at UNITY under the dialogue — 19.6 dB above what the preview drew —
    because a zero-length bed produced an empty curve and an empty curve means
    no filter. The film is the honest clock for a bed of unknown length: the
    renderer trims the mix to the film anyway.
    """

    @staticmethod
    def _doc(**audio):
        return _t(_SPEAKING, _under(**audio))

    def test_the_render_no_longer_answers_an_unknown_length_with_silence(self):
        edit = self._doc()
        self.assertEqual(se.edit_duration(edit), 6.0)
        self.assertEqual(se.bed_length(edit["audio"], 6.0), 6.0)
        # The measured curve, and it is the one the validator read off the
        # browser: the fader, then the duck's 11.4 dB, held to the last frame.
        self.assertEqual(se.bed_gain_points(edit),
                         [[0.0, 0.35], [0.005, 0.09415], [6.0, 0.09415]])

    def test_the_gain_under_a_line_is_the_ducked_one_and_not_full_level(self):
        edit = self._doc()
        self.assertAlmostEqual(se.bed_gain_at(edit, 3.0),
                               0.35 * se.MIX_DUCK_GAIN, places=5)
        # THE SIZE OF THE DEFECT. What the render used to play here was UNITY
        # — the empty curve, which is no filter at all — against the fader
        # times the duck the document asks for. On the rig that found this the
        # 8 kHz bed came out at -30.2 dB where the preview promised -49.8, and
        # the model's own gap is the same order: about 20 dB of soundtrack
        # sitting on top of the dialogue with nothing on screen saying so.
        gap = 20 * math.log10(1.0 / se.bed_gain_at(edit, 3.0))
        self.assertGreater(gap, 19.0)
        self.assertLess(gap, 21.0)

    def test_the_fallback_is_what_is_LEFT_of_the_film_not_its_whole_length(self):
        # A bed that comes in two seconds late is heard for four seconds, and
        # its envelope's clock is those four — otherwise a fade-out dragged
        # onto the corner would land two seconds past the last frame.
        edit = self._doc(offset=-2.0)
        self.assertEqual(se.music_window(edit["audio"])["film_start"], 2.0)
        self.assertEqual(se.bed_length(edit["audio"], 6.0), 4.0)

    def test_a_stated_length_still_wins_over_the_film(self):
        # The film is a FALLBACK, not an override: a document that knows how
        # long its track is renders exactly as it did before this existed.
        edit = self._doc(duration=45.0)
        self.assertEqual(se.bed_length(edit["audio"], 6.0), 45.0)

    def test_a_bed_on_an_empty_film_is_still_nothing(self):
        # No clips, no stated length: there is no clock at all, and inventing
        # one would be worse than the silence.
        edit = _t([], _under())
        self.assertEqual(se.bed_length(edit["audio"], 0.0), 0.0)
        self.assertEqual(se.bed_gain_points(edit), [])


if __name__ == "__main__":
    unittest.main()
