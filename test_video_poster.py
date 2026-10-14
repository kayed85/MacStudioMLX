#!/usr/bin/env python3
"""FILM-39: a first-frame poster for a video clip, taken from its proxy.

`_ensure_proxy_poster` (Editor filmstrip, FILM-39) mirrors `_ensure_thumbnail` (images) for video, using
ffmpeg instead of PIL since PIL cannot open an mp4. The route
`/storyboard/edit/poster` mirrors `/storyboard/edit/proxy` line for line:
same containment (a basename the server itself minted, resolved under
proxy_dir, never an arbitrary clip path).

Runs real ffmpeg against a synthetic 1-frame clip — not mocked, so a broken
ffmpeg invocation (wrong flags, wrong order) fails this test the same way it
would fail in the field.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as panel                                        # noqa: E402
import storyboard                                                    # noqa: E402


def _make_clip(path: Path, w: int = 64, h: int = 48, color: str = "red") -> None:
    """One second of a solid colour, real ffmpeg, no mocking."""
    import subprocess
    subprocess.run(
        [str(panel.FFMPEG), "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"color=c={color}:s={w}x{h}:d=1:r=1",
         "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True, timeout=30)


@unittest.skipUnless(shutil.which("ffmpeg") or Path(panel.FFMPEG).is_file(),
                     "ffmpeg not available in this environment")
class TheCache(unittest.TestCase):
    def test_a_real_frame_is_extracted_and_cached(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(panel, "_POSTER_CACHE", Path(d) / "cache"):
            src = Path(d) / "proxy.mp4"
            _make_clip(src)
            out = panel._ensure_proxy_poster(src, 32)
            self.assertTrue(out.is_file())
            self.assertGreater(out.stat().st_size, 0)
            # A real JPEG, not just a file that happens to exist.
            self.assertEqual(out.read_bytes()[:2], b"\xff\xd8")
            first_mtime = out.stat().st_mtime_ns
            out2 = panel._ensure_proxy_poster(src, 32)
            self.assertEqual(out, out2)
            self.assertEqual(out.stat().st_mtime_ns, first_mtime)  # not rewritten

    def test_a_different_source_or_width_is_a_different_file(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(panel, "_POSTER_CACHE", Path(d) / "cache"):
            a = Path(d) / "a.mp4"; _make_clip(a, color="red")
            b = Path(d) / "b.mp4"; _make_clip(b, color="blue")
            out_a = panel._ensure_proxy_poster(a, 32)
            out_b = panel._ensure_proxy_poster(b, 32)
            out_a_wide = panel._ensure_proxy_poster(a, 64)
            self.assertNotEqual(out_a, out_b)
            self.assertNotEqual(out_a, out_a_wide)

    def test_a_source_that_changes_gets_a_new_poster(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(panel, "_POSTER_CACHE", Path(d) / "cache"):
            src = Path(d) / "proxy.mp4"
            _make_clip(src, color="red")
            out1 = panel._ensure_proxy_poster(src, 32)
            import time
            time.sleep(0.01)
            src.unlink()
            _make_clip(src, color="green")
            out2 = panel._ensure_proxy_poster(src, 32)
            self.assertNotEqual(out1, out2)

    def test_a_file_ffmpeg_cannot_read_raises_rather_than_serving_junk(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(panel, "_POSTER_CACHE", Path(d) / "cache"):
            junk = Path(d) / "not-a-video.mp4"
            junk.write_bytes(b"not a real mp4 at all")
            with self.assertRaises(Exception):
                panel._ensure_proxy_poster(junk, 32)

    def test_a_missing_source_raises_file_not_found(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(panel, "_POSTER_CACHE", Path(d) / "cache"):
            with self.assertRaises(FileNotFoundError):
                panel._ensure_proxy_poster(Path(d) / "nope.mp4", 32)


@unittest.skipUnless(shutil.which("ffmpeg") or Path(panel.FFMPEG).is_file(),
                     "ffmpeg not available in this environment")
class TheRoute(unittest.TestCase):
    """Mirrors test_storyboard_editor_api.py's proxy-route tests exactly —
    same containment, same shape checks — plus one real end-to-end 200."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.state = root / "state"
        self.state.mkdir()
        self._patches = [
            mock.patch.object(panel, "STATE_DIR", self.state),
            mock.patch.object(panel, "push", lambda *a, **k: None),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)
        board = {"schema": 1, "id": "sb_t", "title": "A Film",
                 "created_at": 1_700_000_000, "policy": storyboard.default_policy(),
                 "cast": [], "engine_mode": "ltx", "shots": []}
        storyboard.save_storyboard(self.state, board)

    def _get(self, url: str):
        from test_storyboard_editor_api import FakeHandler
        return FakeHandler().get(url)

    def test_a_poster_is_served_as_a_real_jpeg(self):
        import storyboard_editor as sedit
        bdir = storyboard.board_dir(self.state, "sb_t")
        d = sedit.proxy_dir(bdir)
        d.mkdir(parents=True)
        _make_clip(d / "a_1234.mp4")
        h = self._get("/storyboard/edit/poster?id=sb_t&name=a_1234.mp4&w=32")
        self.assertEqual(h.status, 200)
        self.assertEqual(h.headers_sent.get("Content-Type"), "image/jpeg")
        self.assertEqual(h.body[:2], b"\xff\xd8")
        self.assertIn("immutable", h.headers_sent.get("Cache-Control", ""))

    def test_a_missing_proxy_is_404(self):
        h = self._get("/storyboard/edit/poster?id=sb_t&name=nope_1234.mp4")
        self.assertEqual(h.status, 404)
        self.assertEqual(h.body, b"")

    def test_the_name_cannot_escape_the_board_folder(self):
        for name in ("../../../../etc/passwd", "..%2Fx.mp4", "/etc/hosts",
                     "sub/dir.mp4", "", "x.mp4x"):
            h = self._get(f"/storyboard/edit/poster?id=sb_t&name={name}")
            self.assertIn(h.status, (400, 404), name)
            self.assertEqual(h.body, b"", name)

    def test_a_broken_proxy_is_404_not_500(self):
        import storyboard_editor as sedit
        bdir = storyboard.board_dir(self.state, "sb_t")
        d = sedit.proxy_dir(bdir)
        d.mkdir(parents=True)
        (d / "broken_1234.mp4").write_bytes(b"not a real video")
        h = self._get("/storyboard/edit/poster?id=sb_t&name=broken_1234.mp4")
        self.assertEqual(h.status, 404)


if __name__ == "__main__":
    unittest.main()
