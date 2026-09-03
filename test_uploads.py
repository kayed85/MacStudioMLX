#!/usr/bin/env python3
"""POST /upload/delete — the Recent-uploads strip's "×" (a Pinokio ask).

Path-bound like every file route: only a file under the uploads dir may go,
the uploads dir itself never, nothing outside. The thumbnail cache is
cleared with it (its keys carry mtime+size, so a per-file purge is not
possible and a stale thumbnail was half of the original report).
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = Path(tempfile.mkdtemp(prefix="phos-uploads-"))
UPLOADS = Path(tempfile.mkdtemp(prefix="phos-uploads-dir-"))
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["LTX_UPLOADS_DIR"] = str(UPLOADS)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8311")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402


class UploadDelete(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), P.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def _post(self, path: str):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/upload/delete",
            data=urllib.parse.urlencode({"path": path}).encode(), method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            r = urllib.request.urlopen(req)
            return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_deletes_an_upload_and_its_thumbnails(self) -> None:
        f = P.UPLOADS / "e2e_delete_me.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        (P._THUMBCACHE).mkdir(parents=True, exist_ok=True)
        (P._THUMBCACHE / "stale.jpg").write_bytes(b"x")
        status, body = self._post(str(f))
        self.assertEqual(status, 200, body)
        self.assertFalse(f.exists(), "the upload must be gone")
        self.assertFalse((P._THUMBCACHE / "stale.jpg").exists(),
                         "the thumbnail cache must be cleared with it")
        self.assertIn(b'"uploads"', body, "the reply carries the fresh strip")

    def test_refuses_paths_outside_uploads(self) -> None:
        outside = Path(tempfile.mkdtemp()) / "not_an_upload.png"
        outside.write_bytes(b"x")
        status, _ = self._post(str(outside))
        self.assertEqual(status, 400)
        self.assertTrue(outside.exists(), "nothing outside uploads may be touched")

    def test_refuses_the_uploads_dir_itself_and_missing_files(self) -> None:
        status, _ = self._post(str(P.UPLOADS))
        self.assertEqual(status, 400)
        status, _ = self._post(str(P.UPLOADS / "never_existed.png"))
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
