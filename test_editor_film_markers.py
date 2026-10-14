#!/usr/bin/env python3
"""FILM-58 — markers, copy/paste, sequence aspect.

Locked here:
  * `markers_of` — the read accessor: absent is no markers, an invalid
    entry is dropped rather than crashing the payload, sorted by time.
  * `validate_edit` — a marker's shape and `at`/`kind` are checked the same
    way an overlay's are; `settings.aspect` is checked against `SEQ_ASPECTS`.
  * `_sb_crop_to_aspect` — the sequence aspect's render-time effect: a
    center crop of the FINISHED film, '16:9' and an unknown key are a
    no-op, the wider dimension is the one cropped (never padded).
  * The client model (`sbeMarkerAdd`/`Remove`/`SetKind`, the copy/paste
    functions, `sbeSeqAspect`) run in node against the real functions.
  * NLE marker export (coordinator follow-up, 2026-09-29) — `_fcp7_markers`
    and `ae_jsx`'s marker lines, and `export_nle` actually threading
    `markers_of(edit)` through to both files: a marker lands on the FCP7
    sequence ruler as a point marker (`<out>-1</out>`) and on the AE comp's
    own `markerProperty`, in film-clock order, an empty lane writing neither
    a `<marker>` nor a `setValueAtTime` line.

Run:  ./ltx-2-mlx/env/bin/python3.11 -m pytest -q test_editor_film_markers.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as panel                                        # noqa: E402
import storyboard_editor as sedit                                    # noqa: E402


def _clip(cid, path, start, end, film_start, **kw):
    c = sedit.new_clip(path, start, end, film_start, id=cid, duration=10.0, source="human")
    c.update(kw)
    return c


def _doc(clips, **kw):
    d = {"version": sedit.EDIT_VERSION, "board_id": "sb_m", "revision": 0, "source": "human",
         "audio": None, "beats": None, "clips": clips, "settings": {}}
    d.update(kw)
    return d


def _codes(doc):
    return [e["code"] for e in sedit.validate_edit(doc)]


# =============================================================================
# THE MODEL — markers_of
# =============================================================================
class TheMarkersAccessor(unittest.TestCase):
    def test_absent_is_no_markers(self):
        self.assertEqual(sedit.markers_of({}), [])
        self.assertEqual(sedit.markers_of({"markers": None}), [])

    def test_reads_sorts_and_defaults_kind(self):
        doc = {"markers": [{"id": "b", "at": 5.0}, {"id": "a", "at": 1.0, "kind": "beat"}]}
        got = sedit.markers_of(doc)
        self.assertEqual([m["id"] for m in got], ["a", "b"])
        self.assertEqual(got[0]["kind"], "beat")
        self.assertEqual(got[1]["kind"], "note")   # default

    def test_a_bad_entry_is_dropped_not_fatal(self):
        doc = {"markers": ["not a dict", {"id": "a", "at": -5}, {"id": "b", "at": 3.0}]}
        got = sedit.markers_of(doc)
        self.assertEqual([m["id"] for m in got], ["b"])


class TheMarkerValidation(unittest.TestCase):
    def test_a_legal_marker_validates(self):
        doc = _doc([], markers=[{"id": "m1", "at": 3.0, "kind": "beat", "label": "drop"}])
        self.assertEqual(_codes(doc), [])

    def test_bad_shape_is_refused(self):
        self.assertIn("markers_shape", _codes(_doc([], markers="nope")))
        self.assertIn("marker_shape", _codes(_doc([], markers=["nope"])))

    def test_bad_at_is_refused(self):
        self.assertIn("marker_at", _codes(_doc([], markers=[{"id": "m", "at": "soon"}])))
        self.assertIn("marker_at_range", _codes(_doc([], markers=[{"id": "m", "at": -1}])))

    def test_bad_kind_is_refused(self):
        self.assertIn("marker_kind", _codes(_doc([], markers=[{"id": "m", "at": 1, "kind": "chorus"}])))


# =============================================================================
# NLE MARKER EXPORT — FCP7 XML + the AE script
# =============================================================================
class TheNleMarkerExport(unittest.TestCase):
    """Coordinator follow-up, 2026-09-29: the drift fix (FILM-51) had to
    reach users; this is FILM-58's own last deferral — markers measured and
    validated, but never written into the project folder `export_nle`
    produces. Now `export_nle` takes `markers` (the caller passes
    `markers_of(edit)`) and both writers place them."""

    def _export(self, markers):
        c = _clip("a", "/x/a.mp4", 0.0, 4.0, 0.0)
        probe = lambda p: {"w": 768, "h": 416, "duration": 10.0, "has_audio": True}
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(sedit.Path, "is_file", return_value=True), \
                mock.patch.object(sedit, "_link_or_copy", return_value="link"):
            res = sedit.export_nle([c], d, name="m", probe=probe, markers=markers)
            xml = Path(res["xml"]).read_text()
            jsx = Path(res["jsx"]).read_text()
            return res, xml, jsx

    def test_no_markers_writes_neither_element(self):
        res, xml, jsx = self._export(None)
        self.assertEqual(res["markers"], 0)
        self.assertNotIn("<marker>", xml)
        self.assertNotIn("markerProperty", jsx)

    def test_a_labelled_beat_lands_on_both(self):
        res, xml, jsx = self._export(
            sedit.markers_of({"markers": [{"id": "m1", "at": 1.5, "kind": "beat",
                                           "label": "drop"}]}))
        self.assertEqual(res["markers"], 1)
        # FCP7: point marker (out=-1), NAME is the label, COMMENT is the kind
        # — the frame is `at` on the SAME clock `_frames` uses everywhere
        # else in the file (fps defaults to NLE_FPS here).
        self.assertIn("<marker>", xml)
        self.assertIn("<name>drop</name>", xml)
        self.assertIn("<comment>beat</comment>", xml)
        self.assertIn(f"<in>{sedit._frames(1.5, sedit.NLE_FPS)}</in>", xml)
        self.assertIn("<out>-1</out>", xml)
        # AE: a comp-level marker, not a layer marker — no layer to hang it
        # off that would survive every segment being a different shape.
        self.assertIn("comp.markerProperty.setValueAtTime(1.500000,", jsx)
        self.assertIn("drop (beat)", jsx)

    def test_an_unlabelled_marker_falls_back_to_its_kind(self):
        res, xml, jsx = self._export(
            sedit.markers_of({"markers": [{"id": "m1", "at": 0.0, "kind": "lyric"}]}))
        self.assertIn("<name>Lyric</name>", xml)
        self.assertIn("<comment>lyric</comment>", xml)
        self.assertIn("new MarkerValue(\"Lyric\")", jsx)
        self.assertNotIn("Lyric (lyric)", jsx)   # no redundant kind suffix

    def test_several_markers_stay_in_film_order_and_none_are_dropped(self):
        res, xml, jsx = self._export(
            sedit.markers_of({"markers": [
                {"id": "z", "at": 3.0, "kind": "note", "label": "outro"},
                {"id": "a", "at": 0.5, "kind": "beat", "label": "intro"},
            ]}))
        self.assertEqual(res["markers"], 2)
        self.assertEqual(xml.count("<marker>"), 2)
        self.assertEqual(jsx.count("markerProperty.setValueAtTime"), 2)
        # markers_of already sorts by `at`; the writers must not re-shuffle.
        self.assertLess(xml.index("intro"), xml.index("outro"))
        self.assertLess(jsx.index("intro"), jsx.index("outro"))

    def test_markers_sit_after_media_and_before_the_sequence_closes(self):
        res, xml, jsx = self._export(
            sedit.markers_of({"markers": [{"id": "m1", "at": 1.0, "kind": "note"}]}))
        self.assertLess(xml.index("</media>"), xml.index("<marker>"))
        self.assertLess(xml.index("<marker>"), xml.index("</sequence>"))


class TheAspectValidation(unittest.TestCase):
    def test_a_legal_aspect_validates(self):
        for a in ("16:9", "9:16", "1:1"):
            self.assertEqual(_codes(_doc([], settings={"aspect": a})), [])

    def test_an_unknown_aspect_is_refused(self):
        self.assertIn("settings_aspect", _codes(_doc([], settings={"aspect": "4:3"})))


# =============================================================================
# THE RENDER-TIME CROP
# =============================================================================
class TheAspectCrop(unittest.TestCase):
    def test_16_9_is_a_no_op(self):
        self.assertIsNone(panel._sb_crop_to_aspect("/x/f.mp4", "16:9"))

    def test_an_unknown_key_is_a_no_op(self):
        self.assertIsNone(panel._sb_crop_to_aspect("/x/f.mp4", "4:3"))

    def test_9_16_crops_the_width_not_the_height(self):
        with mock.patch("storyboard_edit.probe_media", return_value={"w": 1920, "h": 1080}), \
             mock.patch.object(panel.subprocess, "run") as run, \
             mock.patch.object(panel.os, "replace") as replace:
            err = panel._sb_crop_to_aspect("/x/f.mp4", "9:16")
        self.assertIsNone(err)
        run.assert_called_once()
        cmd = run.call_args[0][0]
        vf = cmd[cmd.index("-vf") + 1]
        # target height stays 1080 (the full source height fits), target
        # width = 1080 * 9/16 = 607.5 -> round-half-to-even -> 608.
        self.assertIn("crop=608:1080:", vf)
        replace.assert_called_once()

    def test_1_1_crops_the_taller_dimension(self):
        with mock.patch("storyboard_edit.probe_media", return_value={"w": 1920, "h": 1080}), \
             mock.patch.object(panel.subprocess, "run") as run, \
             mock.patch.object(panel.os, "replace"):
            panel._sb_crop_to_aspect("/x/f.mp4", "1:1")
        cmd = run.call_args[0][0]
        vf = cmd[cmd.index("-vf") + 1]
        self.assertIn("crop=1080:1080:", vf)

    def test_unreadable_source_reports_an_error_not_an_exception(self):
        with mock.patch("storyboard_edit.probe_media", return_value=None):
            err = panel._sb_crop_to_aspect("/x/f.mp4", "9:16")
        self.assertIsNotNone(err)

    def test_ffmpeg_failure_reports_and_cleans_up_the_temp_file(self):
        import subprocess as sp
        with mock.patch("storyboard_edit.probe_media", return_value={"w": 1920, "h": 1080}), \
             mock.patch.object(panel.subprocess, "run",
                               side_effect=sp.CalledProcessError(1, "ffmpeg")), \
             mock.patch.object(panel.Path, "unlink") as unlink:
            err = panel._sb_crop_to_aspect("/x/f.mp4", "9:16")
        self.assertIsNotNone(err)
        unlink.assert_called_once()


# =============================================================================
# THE RENDER PATH WIRES THE CROP IN
# =============================================================================
class TheRenderEditAppliesAspect(unittest.TestCase):
    def test_aspect_is_applied_after_a_successful_assembly(self):
        edit = sedit.normalise_edit(_doc(
            [_clip("a", "/x/a.mp4", 0.0, 4.0, 0.0)], settings={"aspect": "9:16"}))
        board = {"id": "b1", "shots": []}
        fake_film = {"ok": True, "path": "/out/film.mp4", "width": 1920, "height": 1080}
        with mock.patch.object(panel, "_sbe_import", return_value=sedit), \
             mock.patch.object(panel, "_sb_film_dir_for_write", return_value=Path("/out")), \
             mock.patch.object(panel, "_sb_deliver", return_value={"format": "h264", "size": "native",
                                                                    "finish": "none", "label": "H.264"}), \
             mock.patch.object(panel, "_sb_film_name", return_value="film.mp4"), \
             mock.patch.object(panel, "_sb_assemble_film", return_value=dict(fake_film)), \
             mock.patch.object(panel, "_sb_crop_to_aspect", return_value=None) as crop:
            got = panel._sbe_render_edit(board, edit)
        crop.assert_called_once_with("/out/film.mp4", "9:16", deliver=mock.ANY)
        self.assertEqual(got["aspect"], "9:16")
        self.assertEqual(got["width"], 608)
        self.assertEqual(got["height"], 1080)

    def test_a_crop_failure_is_disclosed_not_fatal(self):
        edit = sedit.normalise_edit(_doc(
            [_clip("a", "/x/a.mp4", 0.0, 4.0, 0.0)], settings={"aspect": "1:1"}))
        board = {"id": "b1", "shots": []}
        fake_film = {"ok": True, "path": "/out/film.mp4", "width": 1920, "height": 1080}
        with mock.patch.object(panel, "_sbe_import", return_value=sedit), \
             mock.patch.object(panel, "_sb_film_dir_for_write", return_value=Path("/out")), \
             mock.patch.object(panel, "_sb_deliver", return_value={"format": "h264", "size": "native",
                                                                    "finish": "none", "label": "H.264"}), \
             mock.patch.object(panel, "_sb_film_name", return_value="film.mp4"), \
             mock.patch.object(panel, "_sb_assemble_film", return_value=dict(fake_film)), \
             mock.patch.object(panel, "_sb_crop_to_aspect", return_value="ffmpeg is missing"):
            got = panel._sbe_render_edit(board, edit)
        self.assertTrue(got["ok"])                # the render itself still succeeded
        self.assertIn("ffmpeg is missing", got["aspect_note"])
        self.assertEqual(got["width"], 1920)       # unmodified — the crop never landed

    def test_16_9_never_calls_the_crop_at_all(self):
        edit = sedit.normalise_edit(_doc([_clip("a", "/x/a.mp4", 0.0, 4.0, 0.0)]))
        board = {"id": "b1", "shots": []}
        fake_film = {"ok": True, "path": "/out/film.mp4", "width": 1920, "height": 1080}
        with mock.patch.object(panel, "_sbe_import", return_value=sedit), \
             mock.patch.object(panel, "_sb_film_dir_for_write", return_value=Path("/out")), \
             mock.patch.object(panel, "_sb_deliver", return_value={"format": "h264", "size": "native",
                                                                    "finish": "none", "label": "H.264"}), \
             mock.patch.object(panel, "_sb_film_name", return_value="film.mp4"), \
             mock.patch.object(panel, "_sb_assemble_film", return_value=dict(fake_film)), \
             mock.patch.object(panel, "_sb_crop_to_aspect") as crop:
            panel._sbe_render_edit(board, edit)
        crop.assert_not_called()


# =============================================================================
# THE CLIENT MODEL
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


class TheClientMarkerModel(unittest.TestCase):
    def test_add_sorts_by_time(self):
        r = run_client("""
let ms = [];
ms = sbeMarkerAdd(ms, 5.0, 'beat', '');
ms = sbeMarkerAdd(ms, 1.0, 'lyric', 'hey');
out.order = ms.map(m => m.at);
out.kinds = ms.map(m => m.kind);
""")
        self.assertEqual(r["order"], [1.0, 5.0])
        self.assertEqual(r["kinds"], ["lyric", "beat"])

    def test_remove_and_set_kind(self):
        r = run_client("""
let ms = sbeMarkerAdd([], 2.0, 'note', '');
const id = ms[0].id;
ms = sbeMarkerSetKind(ms, id, 'beat');
out.kind = ms[0].kind;
ms = sbeMarkerRemove(ms, id);
out.left = ms.length;
""")
        self.assertEqual(r["kind"], "beat")
        self.assertEqual(r["left"], 0)

    def test_negative_at_clamps_to_zero(self):
        r = run_client("out.m = sbeMarkerAdd([], -5, 'note', '')[0];")
        self.assertEqual(r["m"]["at"], 0)


class TheClientCopyPaste(unittest.TestCase):
    def test_paste_attrs_copies_grade_frame_fx_speed_only(self):
        r = run_client("""
const src = { id: 'a', kind: 'video', path: '/x/a.mp4', start: 0, end: 8, film_start: 0, film_end: 4,
              adjust: { exposure: 0.2 }, frame: { zoom: 2 }, fx: { fade_in: 0.5 }, speed: 2.0 };
const dst = { id: 'b', kind: 'video', path: '/x/b.mp4', start: 1, end: 5, film_start: 10, film_end: 14 };
const r = sbeClipboardPasteAttrs(lay([src, dst]), ['b'], src);
out.ok = r.ok;
const b = sbeById(r.clips, 'b');
out.adjust = b.adjust; out.frame = b.frame; out.fx = b.fx; out.speed = b.speed;
out.film_start = b.film_start; out.path = b.path;  // position and identity untouched
// EDITOR-5: speed is timing — the window stays, the slot follows it.
out.window = [b.start, b.end]; out.film_end = b.film_end;
""")
        self.assertTrue(r["ok"])
        self.assertEqual(r["adjust"], {"exposure": 0.2})
        self.assertEqual(r["frame"], {"zoom": 2})
        self.assertEqual(r["fx"], {"fade_in": 0.5})
        self.assertEqual(r["speed"], 2.0)
        self.assertEqual(r["film_start"], 10)          # the cut never moves
        self.assertEqual(r["path"], "/x/b.mp4")         # identity never changes
        self.assertEqual(r["window"], [1, 5])           # nor does the source window
        self.assertEqual(r["film_end"], 12)             # 4 s at 2x plays in 2 s

    def test_paste_attrs_refuses_a_locked_clip(self):
        r = run_client("""
const src = { id: 'a', kind: 'video', adjust: { exposure: 0.1 } };
const dst = { id: 'b', kind: 'video', locked: true };
const r = sbeClipboardPasteAttrs([src, dst], ['b'], src);
out.ok = r.ok;
""")
        self.assertFalse(r["r"]["ok"] if "r" in r else r["ok"])

    def test_paste_clips_inserts_new_clips_with_the_sources_look(self):
        r = run_client("""
const src = { id: 'a', kind: 'video', path: '/x/a.mp4', start: 1, end: 4, film_start: 0, film_end: 3,
              duration: 20, adjust: { exposure: 0.1 } };
const r = sbeClipboardPasteClips([], [src], 0);
out.ok = r.ok;
out.n = r.clips.length;
out.newId = r.clips[0].id !== 'a';
out.adjust = r.clips[0].adjust;
out.path = r.clips[0].path;
""")
        self.assertTrue(r["ok"])
        self.assertEqual(r["n"], 1)
        self.assertTrue(r["newId"])
        self.assertEqual(r["adjust"], {"exposure": 0.1})
        self.assertEqual(r["path"], "/x/a.mp4")


class TheClientSeqAspect(unittest.TestCase):
    def test_default_is_16_9(self):
        r = run_client("""
SBE.edit = {};
out.a = sbeSeqAspect();
""")
        self.assertEqual(r["a"], "16:9")

    def test_set_reads_back(self):
        r = run_client("""
SBE.open = true; SBE.id = 'sb_1'; SBE.edit = {};
SBE.dirty = false;
sbeSetSeqAspect('9:16');
out.a = sbeSeqAspect();
out.dirty = SBE.dirty;
""")
        self.assertEqual(r["a"], "9:16")
        self.assertTrue(r["dirty"])

    def test_an_unknown_key_is_ignored(self):
        r = run_client("""
SBE.open = true; SBE.id = 'sb_1'; SBE.edit = {};
sbeSetSeqAspect('4:3');
out.a = sbeSeqAspect();
""")
        self.assertEqual(r["a"], "16:9")


if __name__ == "__main__":
    unittest.main()
