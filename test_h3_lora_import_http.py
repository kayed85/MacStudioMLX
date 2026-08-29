#!/usr/bin/env python3
"""The HTTP layer of `POST /h3/loras/import` — a real socket, the real Handler.

Nothing covered this. The PR's suite exercised `import_h3_lora_file(name, data)`
directly, which is the half that does not touch the wire: the 411/413 gates, the
multipart framing, and the streaming reader were all untested. Streaming is the
whole reason the size cap could be raised from 512 MiB (which returned a flat
413 to the community flagship at 780 MB) to 4 GiB, so it needs a gate that
fails when someone reintroduces a buffered read.
"""
from __future__ import annotations

import http.client
import io
import json
import os
import struct
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = Path(tempfile.mkdtemp(prefix="phos-h3-import-http-"))
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8299")

import mlx_ltx_panel as P  # noqa: E402

BOUNDARY = "----PhospheneTestBoundary9f2c"


def _adapter(module: str = "blocks.24.attn.qkv_proj", pad: int = 0) -> bytes:
    """A minimal valid bare-layout H3 adapter, optionally padded to `pad` bytes
    of tensor buffer so a test can send something big without holding a real
    weight file."""
    body = struct.pack("<f", 0.0)
    values = [(module + ".lora_A.weight", body), (module + ".lora_B.weight", body)]
    header, offset = {}, 0
    for key, raw in values:
        header[key] = {"dtype": "F32", "shape": [1, 1],
                       "data_offsets": [offset, offset + len(raw)]}
        offset += len(raw)
    encoded = json.dumps(header).encode("utf-8")
    out = len(encoded).to_bytes(8, "little") + encoded + b"".join(r for _, r in values)
    # Trailing slack is legal safetensors: no entry points at it.
    return out + (b"\0" * max(0, pad - len(out)))


def _multipart(parts) -> bytes:
    """`parts` is a list of (name, filename|None, bytes)."""
    out = bytearray()
    for name, filename, payload in parts:
        out += f"--{BOUNDARY}\r\n".encode()
        disp = f'form-data; name="{name}"'
        if filename is not None:
            disp += f'; filename="{filename}"'
        out += f"Content-Disposition: {disp}\r\n".encode()
        if filename is not None:
            out += b"Content-Type: application/octet-stream\r\n"
        out += b"\r\n" + payload + b"\r\n"
    out += f"--{BOUNDARY}--\r\n".encode()
    return bytes(out)


class TestH3LoraImportHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), P.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="phos-h3-http-lib-")
        self.dir = Path(self.tmp.name)
        self.old_dir = P._safe_h3_loras_dir
        P._safe_h3_loras_dir = lambda: self.dir

    def tearDown(self):
        P._safe_h3_loras_dir = self.old_dir
        self.tmp.cleanup()

    def _post(self, body: bytes, *, ctype: str | None = None,
              content_length: int | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            headers = {
                "Content-Type": ctype or f"multipart/form-data; boundary={BOUNDARY}",
                "Content-Length": str(len(body) if content_length is None
                                      else content_length),
            }
            conn.request("POST", "/h3/loras/import", body=body, headers=headers)
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read() or b"{}")
        finally:
            conn.close()

    def test_imports_over_the_wire_and_lands_in_the_library(self):
        body = _multipart([("file", "wire_import.safetensors", _adapter())])
        status, data = self._post(body)

        self.assertEqual(status, 200, data)
        self.assertTrue(data["ok"])
        self.assertEqual(data["filename"], "wire_import.safetensors")
        self.assertEqual(data["pairs"], 1)
        self.assertTrue((self.dir / "wire_import.safetensors").is_file())
        self.assertTrue((self.dir / "wire_import.json").is_file())

    def test_reads_the_file_field_after_a_text_field(self):
        """The browser is free to order the parts however it likes."""
        body = _multipart([
            ("note", None, b"hello"),
            ("file", "after_text.safetensors", _adapter()),
        ])
        status, data = self._post(body)
        self.assertEqual(status, 200, data)
        self.assertTrue((self.dir / "after_text.safetensors").is_file())

    def test_missing_content_length_is_411(self):
        status, data = self._post(b"", content_length=0)
        self.assertEqual(status, 411)
        self.assertIn("Content-Length", data["error"])

    def test_over_the_cap_is_413_and_the_body_is_never_read(self):
        """The declared length is checked BEFORE anything reads the socket."""
        status, data = self._post(b"x",
                                  content_length=P.H3_LORA_UPLOAD_MAX_BYTES + 1)
        self.assertEqual(status, 413)
        self.assertIn("too large", data["error"])
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_the_cap_admits_the_adapters_people_actually_install(self):
        """Measured on this machine: larryvrh turbo v4 (the community flagship)
        is 780 MB, drbaph's repack 620 MB, the lightx2v adapters 1.38–1.96 GB.
        The original 512 MiB cap returned a flat 413 to every one of them."""
        self.assertGreaterEqual(P.H3_LORA_UPLOAD_MAX_BYTES, 2 * 1024 ** 3)

    def test_a_body_with_no_file_field_is_a_400_not_a_500(self):
        body = _multipart([("note", None, b"no file here")])
        status, data = self._post(body)
        self.assertEqual(status, 400, data)
        self.assertFalse(data["ok"])

    def test_a_wrong_extension_is_refused_by_name(self):
        body = _multipart([("file", "adapter.zip", b"not an adapter")])
        status, data = self._post(body)
        self.assertEqual(status, 400)
        self.assertIn(".safetensors", data["error"])
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_a_traversing_filename_lands_on_its_basename(self):
        body = _multipart([
            ("file", "../../../../etc/evil.safetensors", _adapter())])
        status, data = self._post(body)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["filename"], "evil.safetensors")
        self.assertEqual(Path(data["path"]).parent, self.dir)

    def test_a_refusal_still_consumes_the_whole_request_body(self):
        """A refused upload must not leave the adapter unread on the socket.

        Driven through the Handler directly, with a BytesIO for `rfile`, so the
        assertion is on the real property — bytes consumed — rather than on a
        second request appearing to succeed. (It would appear to succeed either
        way: this handler never sets `protocol_version`, so HTTP/1.0 closes
        after every response and `http.client` silently reconnects. The panel's
        own do_GET docstring names that same dependency. This route streams
        gigabytes, so it does not lean on it.)
        """
        # Larger than H3_LORA_STREAM_CHUNK on purpose: below one chunk the
        # reader's first `pull()` empties the socket by accident and the
        # assertion would hold with the drain removed.
        body = _multipart([("file", "nope.zip",
                            _adapter(pad=4 * P.H3_LORA_STREAM_CHUNK))])
        handler = object.__new__(P.Handler)
        handler.path = "/h3/loras/import"
        handler.command = "POST"
        handler.request_version = "HTTP/1.1"
        handler.requestline = "POST /h3/loras/import HTTP/1.1"
        handler.client_address = ("127.0.0.1", 0)
        handler.headers = {
            "Content-Type": f"multipart/form-data; boundary={BOUNDARY}",
            "Content-Length": str(len(body)),
        }
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler.do_POST()

        wire = handler.wfile.getvalue()
        status = int(wire.split(b"\r\n", 1)[0].split()[1])
        self.assertEqual(status, 400)
        self.assertEqual(handler.rfile.read(), b"",
                         "the refused body was left on the socket")

    def test_a_successful_import_also_leaves_nothing_on_the_socket(self):
        body = _multipart([("file", "drained.safetensors",
                            _adapter(pad=4 * P.H3_LORA_STREAM_CHUNK)),
                           ("trailing", None, b"after the file")])
        handler = object.__new__(P.Handler)
        handler.path = "/h3/loras/import"
        handler.command = "POST"
        handler.request_version = "HTTP/1.1"
        handler.requestline = "POST /h3/loras/import HTTP/1.1"
        handler.client_address = ("127.0.0.1", 0)
        handler.headers = {
            "Content-Type": f"multipart/form-data; boundary={BOUNDARY}",
            "Content-Length": str(len(body)),
        }
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler.do_POST()

        self.assertTrue((self.dir / "drained.safetensors").is_file())
        self.assertEqual(handler.rfile.read(), b"")

    def test_a_refusal_over_the_wire_names_the_users_file(self):
        body = _multipart([("file", "mine.safetensors", _adapter()[:-8])])
        status, data = self._post(body)
        self.assertEqual(status, 400)
        self.assertIn("mine.safetensors", data["error"])
        self.assertNotIn("uploading", data["error"])
        self.assertNotIn(".import.", data["error"])


class TestStreamingReader(unittest.TestCase):
    """`_stream_multipart_file_part` must never hold the payload.

    A watermark assertion on process RSS would be the obvious gate and is a bad
    one — `ru_maxrss` is a high-water mark, so it silently passes once any
    earlier test has raised the mark. This asserts the mechanism instead: the
    reader is handed a source that records the largest single `read()` it ever
    serves, and the largest block it ever writes downstream.
    """

    def test_a_large_body_moves_through_in_bounded_blocks(self):
        payload = os.urandom(6 * 1024 * 1024) + b"\r\ntail-not-a-boundary\r\n"
        body = _multipart([("file", "big.safetensors", payload)])

        class Recorder:
            def __init__(self, data): self.buf, self.pos, self.biggest = data, 0, 0
            def read(self, n):
                self.biggest = max(self.biggest, n)
                out = self.buf[self.pos:self.pos + n]
                self.pos += len(out)
                return out

        src = Recorder(body)
        chunk = 64 * 1024
        filename, drain, _ = P._stream_multipart_file_part(
            src, f"multipart/form-data; boundary={BOUNDARY}", len(body),
            "file", chunk=chunk)
        self.assertEqual(filename, "big.safetensors")

        biggest_write = 0

        class CountingFile:
            def __init__(self, path): self.fh = path.open("wb")
            def write(self, block):
                nonlocal biggest_write
                biggest_write = max(biggest_write, len(block))
                return self.fh.write(block)

        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out.bin"
            sink = CountingFile(out)
            written = drain(sink, P.H3_LORA_UPLOAD_MAX_BYTES)
            sink.fh.close()

            # Byte-exact: a payload containing a CRLF that is NOT the boundary
            # must survive intact, which is the case a naive split() loses.
            self.assertEqual(written, len(payload))
            self.assertEqual(out.read_bytes(), payload)

        self.assertLessEqual(src.biggest, chunk)
        self.assertLessEqual(biggest_write, chunk)

    def test_a_body_cut_short_raises_instead_of_installing_a_partial_file(self):
        payload = b"\0" * 4096
        body = _multipart([("file", "cut.safetensors", payload)])
        truncated = body[: len(body) - 40]

        class Src:
            def __init__(self, data): self.buf, self.pos = data, 0
            def read(self, n):
                out = self.buf[self.pos:self.pos + n]
                self.pos += len(out)
                return out

        _, drain, _ = P._stream_multipart_file_part(
            Src(truncated), f"multipart/form-data; boundary={BOUNDARY}",
            len(body), "file")
        with tempfile.TemporaryDirectory() as td:
            with (Path(td) / "o.bin").open("wb") as fh:
                with self.assertRaisesRegex(ValueError, "truncated"):
                    drain(fh, P.H3_LORA_UPLOAD_MAX_BYTES)

    def test_a_part_over_the_drain_cap_raises_MultipartTooLarge(self):
        body = _multipart([("file", "big.safetensors", b"\0" * 8192)])

        class Src:
            def __init__(self, data): self.buf, self.pos = data, 0
            def read(self, n):
                out = self.buf[self.pos:self.pos + n]
                self.pos += len(out)
                return out

        _, drain, _ = P._stream_multipart_file_part(
            Src(body), f"multipart/form-data; boundary={BOUNDARY}",
            len(body), "file", chunk=1024)
        with tempfile.TemporaryDirectory() as td:
            with (Path(td) / "o.bin").open("wb") as fh:
                with self.assertRaises(P.MultipartTooLarge):
                    drain(fh, 4096)


if __name__ == "__main__":
    unittest.main(verbosity=2)
