#!/usr/bin/env python3
"""A CivitAI LoRA download shows its progress and can be cancelled (4.18.0).

Before: the browser's Install button read "Downloading…" for the whole of a
300 MB-2 GB file, with no number and no way out short of quitting. Now the
browser names the download with a token, polls /civitai/download/state for
bytes, and can POST /civitai/download/cancel; the stream stops at the next
chunk, the .partial is removed and nothing is registered as a LoRA.

The download runs for real against a fake CivitAI response (no network: the
opener is replaced) into a scratch LoRA folder; the routes are the real
handlers; the button label runs in node.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")
os.environ.setdefault("PHOSPHENE_DISABLE_VERSION_CHECK", "1")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mlx_ltx_panel as P                                            # noqa: E402
from extract_panel_js import extract_function                        # noqa: E402

routes_loras = sys.modules["panel.routes_loras"]
NODE = shutil.which("node")
CHUNK = 1024 * 256
TOKEN = "dltesttoken0001"


class _Resp(io.BytesIO):
    """A urllib response: a body, headers, a context manager. `on_read`
    runs before every chunk so a test can press Cancel mid-stream."""

    def __init__(self, body: bytes, on_read=None):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))}
        self.on_read = on_read
        self.reads = 0

    def read(self, n=-1):
        self.reads += 1
        if self.on_read:
            self.on_read(self.reads)
        return super().read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Opener:
    def __init__(self, resp):
        self.resp = resp

    def open(self, req, timeout=None):
        return self.resp


class _H:
    def __init__(self, form=None):
        self.form = {k: [v] for k, v in (form or {}).items()}
        self.payload = self.status = None

    def _read_form_body(self):
        return b"", self.form

    def _json(self, payload, status=200):
        self.payload, self.status = payload, status


class _Parsed:
    def __init__(self, query):
        self.query = query


class CivitaiCancel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.loras = Path(self.tmp.name) / "loras"
        self.loras.mkdir()
        for p in (mock.patch.object(P, "_safe_loras_dir", lambda: self.loras),
                  mock.patch.object(P, "_active_civitai_key", lambda: ""),
                  mock.patch.object(P, "push", lambda *_a, **_k: None)):
            p.start()
            self.addCleanup(p.stop)
        P.CIVITAI_DL.clear()
        self.meta = {"name": "Test LoRA", "filename": "test_lora.safetensors", "base_model": "LTXV 2.3"}

    def _route(self, resp, token=TOKEN):
        h = _H({"download_url": "https://civitai.com/api/download/models/1",
                "meta": json.dumps(self.meta), "token": token})
        with mock.patch.object(P, "_civitai_opener", lambda: _Opener(resp)):
            routes_loras.post_civitai_download(h, "/civitai/download", {}, "")
        return h

    def test_cancel_mid_stream_keeps_nothing(self):
        seen = {}

        def on_read(n):
            if n == 3:
                st = _H()
                routes_loras.get_civitai_download_state(st, _Parsed(f"token={TOKEN}"))
                seen["state"] = st.payload
                c = _H({"token": TOKEN})
                routes_loras.post_civitai_download_cancel(c, "/civitai/download/cancel", {}, "")
                seen["cancel"] = c.payload

        h = self._route(_Resp(b"\1" * (CHUNK * 10), on_read))
        self.assertEqual(h.status, 200)
        self.assertEqual(h.payload, {"ok": False, "cancelled": True,
                                     "error": "Download cancelled — nothing was kept."})
        self.assertEqual(seen["cancel"], {"ok": True})
        st = seen["state"]
        self.assertEqual((st["ok"], st["state"], st["total"], st["name"]),
                         (True, "running", CHUNK * 10, "Test LoRA"))
        self.assertEqual(st["bytes"], CHUNK * 2)
        self.assertNotIn("cancel", st)
        self.assertEqual(list(self.loras.iterdir()), [])          # no .partial, no LoRA, no sidecar
        self.assertEqual(P.CIVITAI_DL, {})                       # the entry is gone

    def test_cancel_after_the_last_chunk_still_keeps_nothing(self):
        """Codex 4.18.0: a Cancel while the stream reports EOF used to be
        accepted and then ignored — the LoRA installed under a 'cancelled'."""
        body = b"\1" * (CHUNK * 2)

        def on_read(n):
            if n == 3:   # the read that returns b"" (EOF)
                c = _H({"token": TOKEN})
                routes_loras.post_civitai_download_cancel(c, "/civitai/download/cancel", {}, "")
                self.assertEqual(c.payload, {"ok": True})

        h = self._route(_Resp(body, on_read))
        self.assertTrue(h.payload.get("cancelled"), h.payload)
        self.assertEqual(list(self.loras.iterdir()), [])

    def test_cancel_after_the_commit_point_is_refused(self):
        P.civitai_dl_begin("dlcommit0001", "C")
        P._civitai_dl_commit("dlcommit0001")
        self.assertEqual(P.civitai_dl_state("dlcommit0001")["state"], "installing")
        self.assertFalse(P.civitai_dl_cancel("dlcommit0001"))
        c = _H({"token": "dlcommit0001"})
        routes_loras.post_civitai_download_cancel(c, "/civitai/download/cancel", {}, "")
        self.assertEqual(c.status, 404)

    def test_a_finished_download_is_unchanged(self):
        h = self._route(_Resp(b"\1" * (CHUNK * 3)))
        self.assertTrue(h.payload["ok"], h.payload)
        self.assertTrue((self.loras / "test_lora.safetensors").is_file())
        self.assertTrue((self.loras / "test_lora.json").is_file())
        self.assertEqual(P.CIVITAI_DL, {})

    def test_no_token_runs_as_before(self):
        h = self._route(_Resp(b"\1" * CHUNK), token="")
        self.assertTrue(h.payload["ok"], h.payload)

    def test_state_and_cancel_refuse_unknown_or_malformed_tokens(self):
        for q in ("token=nope", "token=../../etc", "", "token=" + "a" * 80):
            st = _H()
            routes_loras.get_civitai_download_state(st, _Parsed(q))
            self.assertEqual(st.status, 404, q)
        c = _H({"token": "dlnotrunning01"})
        routes_loras.post_civitai_download_cancel(c, "/civitai/download/cancel", {}, "")
        self.assertEqual(c.status, 404)

    def test_concurrent_downloads_cancel_independently(self):
        P.civitai_dl_begin("dlaaaaaaaaaa", "A")
        P.civitai_dl_begin("dlbbbbbbbbbb", "B")
        self.assertTrue(P.civitai_dl_cancel("dlaaaaaaaaaa"))
        with self.assertRaises(P.CivitaiDownloadCancelled):
            P._civitai_dl_tick("dlaaaaaaaaaa", 10, 100)
        P._civitai_dl_tick("dlbbbbbbbbbb", 10, 100)                # B keeps going
        self.assertEqual(P.civitai_dl_state("dlbbbbbbbbbb")["bytes"], 10)
        self.assertFalse(P.civitai_dl_cancel("dlaaaaaaaaaa"))     # already stopping


class ButtonLabel(unittest.TestCase):
    def test_label(self):
        if NODE is None:
            raise unittest.SkipTest("node not on PATH")
        src = (ROOT / "webapp" / "js" / "preview.js").read_text(encoding="utf-8")
        fn = extract_function("civitaiProgressLabel", src)
        mb = 1024 * 1024
        calls = [
            "civitaiProgressLabel(null)",
            f"civitaiProgressLabel({{ok:true, state:'running', bytes:{120*mb}, total:{290*mb}}})",
            f"civitaiProgressLabel({{ok:true, state:'running', bytes:{12*mb}, total:0}})",
            "civitaiProgressLabel({ok:true, state:'cancelled', bytes:1, total:2})",
            "civitaiProgressLabel({ok:true, state:'running', bytes:0, total:0})",
            "civitaiProgressLabel({ok:true, state:'installing', bytes:2, total:2})",
        ]
        script = fn + "\nconsole.log(JSON.stringify([" + ",".join(calls) + "]));"
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(script)
            path = fh.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, errors="replace", timeout=60)
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), [
            "Downloading…", "Downloading 41% · 120 of 290 MB", "Downloading · 12 MB",
            "Cancelling…", "Downloading…", "Installing…"])

    def test_install_wires_cancel_only_for_civitai(self):
        body = extract_function("civitaiInstall", (ROOT / "webapp" / "js" / "preview.js").read_text())
        self.assertIn("if (!fromHf) {", body)
        self.assertIn("/civitai/download/cancel", body)
        self.assertIn("/civitai/download/state?token=", body)
        self.assertIn("data.cancelled", body)


if __name__ == "__main__":
    unittest.main()
