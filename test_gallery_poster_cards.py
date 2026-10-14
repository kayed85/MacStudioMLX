#!/usr/bin/env python3
"""VC-31 [P3] [PERF] `/status` is 97 KB every 1.5s, and gallery cards are
live `<video>` elements. VC-36 [P3] [UX] Gallery cards say little.

Coordinator ruling, 2026-09-29: "Gallery poster thumbnails: the ffmpeg
first frame, cached once per clip, instead of live <video> elements.
Richer cards (prompt snippet, mode, size). Trim the /status payload."

WHAT THIS GUARDS.
  1  _ensure_video_poster(): idempotent, disk-cached (same idiom as the
     existing _ensure_thumbnail for images — sha1 of resolved path/mtime/
     size), ffmpeg extract at 2.5s with a 0s fallback for short clips,
     returns None (never raises) when ffmpeg is missing or the source is
     unreadable
  2  GET /poster: same containment rule as /image (OUTPUT/UPLOADS only),
     video containers only, generates-then-serves the cached JPEG with a
     long-lived Cache-Control (the cache key already changes if the
     source does)
  3  list_outputs() lifts a `prompt` snippet (capped, not the full text)
     and `mode` for the gallery card's richer info line — and
     _output_search_text's own per-field cap dropped from 400 to 160
     chars, a real trim given it runs on every /status poll
  4  the client: gallery cards are plain <img src="/poster?path=..."> for
     video, matching photos — no more live <video> element, no more the
     IntersectionObserver/data-src dance that existed only to defer
     <video> loading (img's own loading="lazy" already does that job)
  5  _cardTitleText() prefers the prompt snippet over the filename
     (falling back to the filename only when there is no prompt), and
     _cardModeSizeChip() builds the "mode · size" line from fields
     already in the payload — no new request per card (the size is the
     file's own, read from its mp4 header: Codex UI-8 / EST-12)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import urlencode, urlparse

ROOT = Path(__file__).resolve().parent
STATE = Path(tempfile.mkdtemp(prefix="phos-postercards-"))
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8337")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mlx_ltx_panel as P  # noqa: E402
from panel.routes import GET_ROUTES  # noqa: E402
from extract_panel_js import extract_function  # noqa: E402

NODE = shutil.which("node")
FFMPEG = shutil.which("ffmpeg")
QJS = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
CSS = (ROOT / "webapp" / "style" / "panel.css").read_text(encoding="utf-8")


def _run_node(script: str) -> dict:
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        r = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise AssertionError("node failed:\n%s\n%s" % (r.stdout[-3000:], r.stderr[-3000:]))
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        Path(path).unlink(missing_ok=True)


class _H:
    def _json(self, obj, code=200):
        self.out = (code, obj)

    def send_error(self, code, msg=None):
        self.out = (code, None)

    def send_response(self, code):
        self.out = (code, None)

    def send_header(self, k, v):
        self.headers = getattr(self, "headers", {})
        self.headers[k] = v

    def end_headers(self):
        pass

    class _WFile:
        def write(self, b):
            pass
    wfile = _WFile()


# ---------------------------------------------------------------- 1
@unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
class TestEnsureVideoPoster(unittest.TestCase):
    def setUp(self):
        P.OUTPUT.mkdir(parents=True, exist_ok=True)

    def _make_mp4(self, name, dur=5, frames=120):
        p = P.OUTPUT / name
        subprocess.run(
            [FFMPEG, "-y", "-f", "lavfi", "-i", f"color=c=blue:s=64x64:d={dur}",
             "-frames:v", str(frames), str(p)],
            capture_output=True, timeout=30, check=True,
        )
        self.addCleanup(lambda: p.unlink(missing_ok=True))
        return p

    def test_generates_and_caches_a_real_jpeg(self):
        clip = self._make_mp4(f"poster_{id(self)}.mp4")
        poster = P._ensure_video_poster(clip)
        self.assertIsNotNone(poster)
        self.assertTrue(poster.is_file())
        self.assertGreater(poster.stat().st_size, 0)
        self.addCleanup(lambda: poster.unlink(missing_ok=True))

    def test_second_call_returns_the_same_cached_file(self):
        clip = self._make_mp4(f"poster2_{id(self)}.mp4")
        p1 = P._ensure_video_poster(clip)
        p2 = P._ensure_video_poster(clip)
        self.assertEqual(p1, p2)
        self.addCleanup(lambda: p1.unlink(missing_ok=True))

    def test_short_clip_falls_back_to_0s_seek(self):
        clip = self._make_mp4(f"poster_short_{id(self)}.mp4", dur=1, frames=10)
        poster = P._ensure_video_poster(clip)
        self.assertIsNotNone(poster)
        self.addCleanup(lambda: poster.unlink(missing_ok=True))

    def test_missing_source_returns_none(self):
        self.assertIsNone(P._ensure_video_poster(P.OUTPUT / "does_not_exist_xyz.mp4"))

    def test_no_ffmpeg_returns_none_gracefully(self):
        clip = self._make_mp4(f"poster_noff_{id(self)}.mp4")
        # The poster uses the panel's resolved ffmpeg (FFMPEG) first, a PATH
        # lookup only when none was resolved — take both away.
        old, old_ff = P.shutil.which, P.FFMPEG
        P.shutil.which = lambda name: None
        P.FFMPEG = None
        try:
            self.assertIsNone(P._ensure_video_poster(clip))
        finally:
            P.shutil.which, P.FFMPEG = old, old_ff


# ---------------------------------------------------------------- 2
class TestPosterRoute(unittest.TestCase):
    def test_route_registered(self):
        self.assertIn("/poster", GET_ROUTES)

    def test_refuses_a_path_outside_output_and_uploads(self):
        outside = Path(tempfile.mkdtemp(prefix="pc-outside-")) / "x.mp4"
        outside.write_bytes(b"x")
        h = _H()
        GET_ROUTES["/poster"](h, urlparse("/poster?" + urlencode({"path": str(outside)})))
        self.assertEqual(h.out[0], 403)

    def test_refuses_a_non_video_extension(self):
        P.UPLOADS.mkdir(parents=True, exist_ok=True)
        img = P.UPLOADS / f"pc_{id(self)}.png"
        img.write_bytes(b"x")
        h = _H()
        GET_ROUTES["/poster"](h, urlparse("/poster?" + urlencode({"path": str(img)})))
        self.assertEqual(h.out[0], 403)
        img.unlink(missing_ok=True)

    def test_refuses_a_missing_file(self):
        h = _H()
        GET_ROUTES["/poster"](h, urlparse("/poster?" + urlencode(
            {"path": str(P.OUTPUT / "nope_xyz.mp4")})))
        self.assertEqual(h.out[0], 404)

    @unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
    def test_serves_a_real_clip_as_jpeg(self):
        P.OUTPUT.mkdir(parents=True, exist_ok=True)
        clip = P.OUTPUT / f"pc_route_{id(self)}.mp4"
        subprocess.run(
            [FFMPEG, "-y", "-f", "lavfi", "-i", "color=c=green:s=64x64:d=3",
             "-frames:v", "60", str(clip)],
            capture_output=True, timeout=30, check=True,
        )
        h = _H()
        GET_ROUTES["/poster"](h, urlparse("/poster?" + urlencode({"path": str(clip)})))
        self.assertEqual(h.out[0], 200)
        self.assertEqual(h.headers["Content-Type"], "image/jpeg")
        self.assertIn("max-age", h.headers["Cache-Control"])
        clip.unlink(missing_ok=True)


# ---------------------------------------------------------------- 3
class TestListOutputsRicherFields(unittest.TestCase):
    def setUp(self):
        P.OUTPUT.mkdir(parents=True, exist_ok=True)

    def _fixture(self, name, params):
        clip = P.OUTPUT / name
        clip.write_bytes(b"x")
        sidecar = Path(str(clip) + ".json")
        sidecar.write_text(json.dumps({"params": params}))
        old = time.time() - 10
        os.utime(clip, (old, old))
        os.utime(sidecar, (old, old))
        self.addCleanup(lambda: (clip.unlink(missing_ok=True), sidecar.unlink(missing_ok=True)))
        return clip

    def _row(self, clip):
        return next(o for o in P.list_outputs() if o["path"] == str(clip))

    def test_prompt_and_mode_are_lifted(self):
        clip = self._fixture(f"pc_rich_{id(self)}.mp4",
                             {"mode": "i2v", "quality": "balanced", "frames": 121,
                              "prompt": "A golden retriever runs through a meadow"})
        row = self._row(clip)
        self.assertEqual(row["prompt"], "A golden retriever runs through a meadow")
        self.assertEqual(row["mode"], "i2v")

    def test_prompt_is_capped_not_the_full_text(self):
        long_prompt = "word " * 200
        clip = self._fixture(f"pc_long_{id(self)}.mp4",
                             {"mode": "t2v", "prompt": long_prompt})
        row = self._row(clip)
        self.assertLessEqual(len(row["prompt"]), 80)

    def test_music_row_gets_a_mode_even_without_a_params_mode(self):
        clip = self._fixture(f"pc_music_{id(self)}.wav",
                             {"engine": "music"})
        Path(str(clip) + ".json").write_text(json.dumps(
            {"engine": "music", "params": {"engine": "music"}}))
        row = self._row(clip)
        self.assertEqual(row["mode"], "music")

    def test_search_text_field_cap_shrunk_to_160(self):
        src = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
        i = src.index("def _output_search_text")
        fn = src[i:src.index("\n\n\n", i)]
        self.assertIn("[:160]", fn)
        self.assertNotIn("[:400]", fn)


# ---------------------------------------------------------------- 4
class TestClientCardIsPlainImg(unittest.TestCase):
    def test_no_more_video_element_in_the_card_template(self):
        i = QJS.index("const thumbHtml = isAudio")
        block = QJS[i:QJS.index(";", QJS.index("car-thumb\" src=\"/poster", i))]
        self.assertNotIn("<video", block)
        self.assertIn('/poster?path=', block)
        self.assertIn('loading="lazy"', block)

    def test_intersection_observer_removed(self):
        self.assertNotIn("_carThumbObserver", QJS)
        self.assertNotIn("new IntersectionObserver", QJS)

    def test_css_no_longer_targets_a_bare_video_selector(self):
        self.assertNotIn(".car-card video,", CSS)
        self.assertIn(".car-card img.car-thumb", CSS)


# ---------------------------------------------------------------- 5
class TestCardTitleAndChip(unittest.TestCase):
    def test_title_prefers_prompt_over_filename(self):
        fn = "\n".join([extract_function("_cardTitleText", QJS),
                        extract_function("_songCardName", QJS),
                        "function escapeHtml(s) { return String(s == null ? '' : s); }"])
        script = fn + """
console.log(JSON.stringify({
  withPrompt: _cardTitleText({ name: 'ltx_20260929_120000.mp4', prompt: 'A woman walks through a rain-lit alley at night' }),
  withoutPrompt: _cardTitleText({ name: 'ltx_20260929_120000.mp4' }),
}));
"""
        out = _run_node(script)
        self.assertIn("A woman walks through", out["withPrompt"])
        self.assertNotIn("ltx_20260929", out["withPrompt"])
        self.assertEqual(out["withoutPrompt"], "ltx_20260929_120000.mp4")

    def test_title_truncates_long_prompts_with_an_ellipsis(self):
        fn = extract_function("_cardTitleText", QJS)
        script = fn + """
function escapeHtml(s) { return String(s); }
console.log(JSON.stringify({ t: _cardTitleText({ name: 'x.mp4', prompt: 'one two three four five six seven eight nine ten' }) }));
"""
        out = _run_node(script)
        self.assertTrue(out["t"].endswith('…'))
        self.assertEqual(len(out["t"].rstrip('…').split(' ')), 8)

    # Codex UI-8 / EST-12: the chip used to print the quality preset's /
    # H3 tier's DEFAULT canvas; it prints the file's own dimensions now
    # (list_outputs reads them from the mp4 header) and nothing when unknown.
    def test_chip_row_reads_the_files_own_size_not_the_preset(self):
        fn = extract_function("_cardModeSizeChip", QJS)
        script = f"""
const BOOT = {{ ltx: {{ qualities: [
  {{ key: 'balanced', label: 'Balanced', canvas: '1024×576', delivered_canvas: '1280×720' }},
] }} }};
{fn}
console.log(JSON.stringify({{
  sq: _cardModeSizeChip({{ mode: 't2v', quality: 'balanced', width: 768, height: 768 }}),
  unknown: _cardModeSizeChip({{ mode: 't2v', quality: 'balanced', width: null, height: null }}) }}));
"""
        out = _run_node(script)
        self.assertEqual(out["sq"], "t2v · 768×768")
        self.assertEqual(out["unknown"], "t2v")

    def test_chip_row_ignores_the_h3_tiers_default_geometry(self):
        fn = extract_function("_cardModeSizeChip", QJS)
        script = f"""
const BOOT = {{ ltx: {{ qualities: [] }} }};
function h3TierByKeyExact(k) {{ return k === 'standard_5s' ? {{ spec: '1280×704 · 121f' }} : null; }}
{fn}
console.log(JSON.stringify({{ chip: _cardModeSizeChip({{ mode: 't2v', h3_tier: 'standard_5s', width: 768, height: 448 }}) }}));
"""
        out = _run_node(script)
        self.assertEqual(out["chip"], "t2v · 768×448")

    def test_empty_chip_for_a_row_with_neither(self):
        fn = extract_function("_cardModeSizeChip", QJS)
        script = f"""
const BOOT = {{}};
{fn}
console.log(JSON.stringify({{ chip: _cardModeSizeChip({{}}) }}));
"""
        out = _run_node(script)
        self.assertEqual(out["chip"], "")


if __name__ == "__main__":
    unittest.main()
