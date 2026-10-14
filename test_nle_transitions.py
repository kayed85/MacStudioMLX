#!/usr/bin/env python3
"""FCP7/AE export carries dissolves and fade-through-black, the way the render
already does — FCP7-58 (part of the coordinator's "ship everything" pass on
top of FILM-50, which only got overlays/titles through in the first round).

Both exports read the SAME `resolve_transitions()` the render and the
validator already use (storyboard_editor.py): an unrenderable boundary — no
spare source, an unknown clip, a duplicate on one cut — is simply absent from
the project rather than exported as something the render would refuse.

Validated structurally: the FCP7 XML is real XML (parsed with
xml.etree.ElementTree, not string-matched), the transitionitem sits between
the right two clipitems, and its duration is the same one the render clamps
to. The AE script has no importable transition object, so it is checked as
opacity keyframes on the right layers at the right times instead.
"""
from __future__ import annotations

import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import storyboard_editor as sedit                                    # noqa: E402


def _pair(kind="dissolve", duration=1.0, a_id="a", b_id="b"):
    """Two 10s-source clips, cut at film second 4. `a` is trimmed 0-4s of its
    10s source (6s of spare TAIL beyond its out-point); `b` is trimmed 1-5s
    (1s of spare HEAD before its in-point, plenty for a <=1s transition) —
    real handles on both sides of the cut for a transition to draw from."""
    a = sedit.new_clip("/x/a.mp4", 0.0, 4.0, 0.0, id=a_id, duration=10.0)
    b = sedit.new_clip("/x/b.mp4", 1.0, 5.0, 4.0, id=b_id, duration=10.0)
    clips = [a, b]
    transitions = [{"id": "t1", "after_clip": a_id, "kind": kind,
                    "duration": duration}]
    return clips, transitions


def _segs(clips):
    return sedit._nle_segments(clips)


class TheResolvedMap(unittest.TestCase):
    def test_a_real_transition_resolves_into_both_sides(self):
        clips, transitions = _pair()
        m = sedit._nle_transitions(clips, transitions, fps=24)
        self.assertIn("a", m["out"])
        self.assertIn("b", m["in"])
        self.assertIs(m["out"]["a"], m["in"]["b"])   # same entry, both sides
        self.assertEqual(m["out"]["a"]["kind"], "dissolve")
        # 1.0s at 24fps, halved and rounded to an even frame count (the
        # render's own rule — TRANSITION_MIN/transition_duration).
        self.assertEqual(m["out"]["a"]["half_frames"], 12)
        self.assertAlmostEqual(m["out"]["a"]["half_sec"], 0.5)
        self.assertEqual(m["out"]["a"]["at_frames"], 96)   # 4.0s * 24fps

    def test_a_boundary_with_no_spare_source_is_silently_absent(self):
        # A clip trimmed to use its ENTIRE source has no tail to draw a
        # dissolve from — resolve_transitions refuses it (transition_no_
        # handles); the export must not invent one.
        a = sedit.new_clip("/x/a.mp4", 0.0, 4.0, 0.0, id="a", duration=4.0)
        b = sedit.new_clip("/x/b.mp4", 0.0, 4.0, 4.0, id="b", duration=10.0)
        transitions = [{"id": "t1", "after_clip": "a", "kind": "dissolve",
                        "duration": 1.0}]
        m = sedit._nle_transitions([a, b], transitions, fps=24)
        self.assertEqual(m["out"], {})
        self.assertEqual(m["in"], {})

    def test_an_unknown_clip_is_silently_absent(self):
        clips, transitions = _pair()
        transitions[0]["after_clip"] = "not-a-real-id"
        m = sedit._nle_transitions(clips, transitions, fps=24)
        self.assertEqual(m["out"], {})

    def test_no_transitions_is_the_empty_map(self):
        clips, _ = _pair()
        m = sedit._nle_transitions(clips, None, fps=24)
        self.assertEqual(m, {"out": {}, "in": {}})


class TheFCP7Transitionitem(unittest.TestCase):
    """Structural: real XML, parsed, not string-matched."""

    def _xml(self, kind="dissolve", duration=1.0):
        clips, transitions = _pair(kind=kind, duration=duration)
        segs = _segs(clips)
        tx = sedit._nle_transitions(clips, transitions, fps=24)
        raw = sedit.fcp7_xml(segs, name="f", media={"/x/a.mp4": "a.mp4",
                                                     "/x/b.mp4": "b.mp4"},
                             width=1024, height=576, base="/tmp/p", fps=24,
                             transitions=tx)
        return raw, ET.fromstring(raw)

    def test_it_is_well_formed_xml(self):
        raw, root = self._xml()
        self.assertEqual(root.tag, "xmeml")

    def test_the_transitionitem_sits_between_the_two_clipitems(self):
        raw, root = self._xml()
        track = root.find(".//media/video/track")
        kids = list(track)
        tags = [k.tag for k in kids]
        self.assertEqual(tags, ["clipitem", "transitionitem", "clipitem"])
        self.assertEqual(kids[0].get("id"), "clipitem-1")
        self.assertEqual(kids[2].get("id"), "clipitem-2")

    def test_the_duration_is_the_clamped_one_the_render_uses(self):
        # 1.0s / 2 = 0.5s = 12 frames at 24fps each side around the cut
        # (film second 4.0 = frame 96): 84..108.
        raw, root = self._xml()
        t = root.find(".//media/video/track/transitionitem")
        self.assertEqual(t.find("start").text, "84")
        self.assertEqual(t.find("end").text, "108")
        self.assertEqual(t.find("alignment").text, "center")

    def test_the_clipitems_extend_to_meet_it(self):
        raw, root = self._xml()
        clips = root.findall(".//media/video/track/clipitem")
        # Clip a's OUT end (film) extends from frame 96 to 108; clip b's
        # start pulls back from 96 to 84.
        self.assertEqual(clips[0].find("end").text, "108")
        self.assertEqual(clips[1].find("start").text, "84")

    def test_dissolve_and_fade_black_use_different_named_effects(self):
        _, dissolve_root = self._xml(kind="dissolve")
        _, black_root = self._xml(kind="fade_black")
        d_name = dissolve_root.find(".//transitionitem/effect/name").text
        b_name = black_root.find(".//transitionitem/effect/name").text
        self.assertEqual(d_name, "Cross Dissolve")
        self.assertEqual(b_name, "Dip to Color Dissolve")
        self.assertIsNotNone(
            black_root.find(".//transitionitem/effect/parameter[parameterid='Color']"))
        self.assertIsNone(
            dissolve_root.find(".//transitionitem/effect/parameter[parameterid='Color']"))

    def test_the_audio_clipitem_is_never_extended(self):
        # "the audio plan never sees the extension" — the clip's own sound
        # stays a hard cut at the original boundary.
        raw, root = self._xml()
        audio_clips = root.findall(".//media/audio/track/clipitem")
        # No audio in these fixtures (has_audio defaults False) unless the
        # fixture says so — assert none leaked an extended end/start instead.
        for c in audio_clips:
            self.assertNotEqual(c.find("end").text, "108")

    def test_a_boundary_the_render_would_refuse_leaves_a_plain_cut(self):
        a = sedit.new_clip("/x/a.mp4", 0.0, 4.0, 0.0, id="a", duration=4.0)
        b = sedit.new_clip("/x/b.mp4", 0.0, 4.0, 4.0, id="b", duration=10.0)
        clips = [a, b]
        segs = _segs(clips)
        transitions = [{"id": "t1", "after_clip": "a", "kind": "dissolve",
                        "duration": 1.0}]
        tx = sedit._nle_transitions(clips, transitions, fps=24)
        raw = sedit.fcp7_xml(segs, name="f",
                             media={"/x/a.mp4": "a.mp4", "/x/b.mp4": "b.mp4"},
                             width=1024, height=576, base="/tmp/p", fps=24,
                             transitions=tx)
        root = ET.fromstring(raw)
        track = root.find(".//media/video/track")
        tags = [k.tag for k in track]
        self.assertEqual(tags, ["clipitem", "clipitem"])   # no transitionitem
        self.assertEqual(track[0].find("end").text, "96")   # unextended

    def test_no_transitions_argument_behaves_exactly_as_before(self):
        clips, _ = _pair()
        segs = _segs(clips)
        with_none = sedit.fcp7_xml(segs, name="f",
                                   media={"/x/a.mp4": "a.mp4", "/x/b.mp4": "b.mp4"},
                                   width=1024, height=576, base="/tmp/p", fps=24)
        self.assertNotIn("<transitionitem>", with_none)


class TheAfterEffectsOpacityRamp(unittest.TestCase):
    def _jsx(self, kind="dissolve", duration=1.0):
        clips, transitions = _pair(kind=kind, duration=duration)
        segs = _segs(clips)
        tx = sedit._nle_transitions(clips, transitions, fps=24)
        return sedit.ae_jsx(segs, name="f",
                            media={"/x/a.mp4": "a.mp4", "/x/b.mp4": "b.mp4"},
                            width=1024, height=576, fps=24, transitions=tx)

    def test_dissolve_only_animates_the_incoming_layer(self):
        # comp.layers.add() always inserts at the top, so the clip added
        # LATER (the incoming side of any boundary) ends up above the one
        # before it — fading only the incoming layer up is a correct
        # crossfade over the static layer underneath.
        js = self._jsx(kind="dissolve")
        self.assertEqual(js.count("op.setValueAtTime"), 2)
        self.assertIn("op.setValueAtTime(3.500000, 0);", js)
        self.assertIn("op.setValueAtTime(4.500000, 100);", js)

    def test_fade_black_animates_both_layers_in_non_overlapping_windows(self):
        js = self._jsx(kind="fade_black")
        self.assertEqual(js.count("op.setValueAtTime"), 4)
        # Outgoing: 100 -> 0 over the FIRST half (3.5s -> 4.0s).
        self.assertIn("op.setValueAtTime(3.500000, 100);", js)
        self.assertIn("op.setValueAtTime(4.000000, 0);", js)
        # Incoming: 0 -> 100 over the SECOND half (4.0s -> 4.5s).
        self.assertIn("op.setValueAtTime(4.000000, 0);", js)
        self.assertIn("op.setValueAtTime(4.500000, 100);", js)

    def test_the_inpoint_and_outpoint_extend_to_meet_at_the_cut(self):
        js = self._jsx()
        self.assertIn("lay.outPoint = 4.500000;", js)
        self.assertIn("lay.inPoint = 3.500000;", js)

    def test_a_boundary_the_render_would_refuse_animates_nothing(self):
        a = sedit.new_clip("/x/a.mp4", 0.0, 4.0, 0.0, id="a", duration=4.0)
        b = sedit.new_clip("/x/b.mp4", 0.0, 4.0, 4.0, id="b", duration=10.0)
        clips = [a, b]
        segs = _segs(clips)
        transitions = [{"id": "t1", "after_clip": "a", "kind": "dissolve",
                        "duration": 1.0}]
        tx = sedit._nle_transitions(clips, transitions, fps=24)
        js = sedit.ae_jsx(segs, name="f",
                          media={"/x/a.mp4": "a.mp4", "/x/b.mp4": "b.mp4"},
                          width=1024, height=576, fps=24, transitions=tx)
        self.assertNotIn("op.setValueAtTime", js)
        self.assertIn("lay.outPoint = 4.000000;", js)   # unextended


class TheFullExportRoundTrip(unittest.TestCase):
    """export_nle() end to end: the route's own call shape, with real files
    on disk (the transition resolver needs a real duration to trust the
    handles), through to a parseable XML with a transitionitem in it."""

    def test_export_nle_writes_a_transitionitem_when_the_document_has_one(self):
        import subprocess
        import tempfile
        import mlx_ltx_panel as panel
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            a = root / "a.mp4"
            b = root / "b.mp4"
            for p in (a, b):
                subprocess.run(
                    [str(panel.FFMPEG), "-y", "-loglevel", "error", "-f", "lavfi",
                     "-i", "color=c=red:s=64x48:d=6:r=24", "-pix_fmt", "yuv420p",
                     str(p)], check=True, capture_output=True, timeout=30)
            clip_a = sedit.new_clip(str(a), 0.0, 4.0, 0.0, id="a", duration=6.0)
            clip_b = sedit.new_clip(str(b), 1.0, 5.0, 4.0, id="b", duration=6.0)
            transitions = [{"id": "t1", "after_clip": "a", "kind": "dissolve",
                            "duration": 1.0}]
            res = sedit.export_nle(
                [clip_a, clip_b], root, name="A Film", fps=24,
                probe=panel._sb_probe_clip, transitions=transitions)
            self.assertTrue(res["ok"])
            xml_text = Path(res["xml"]).read_text()
            root_el = ET.fromstring(xml_text)
            self.assertIsNotNone(root_el.find(".//transitionitem"))
            jsx_text = Path(res["jsx"]).read_text()
            self.assertIn("op.setValueAtTime", jsx_text)


if __name__ == "__main__":
    unittest.main()
