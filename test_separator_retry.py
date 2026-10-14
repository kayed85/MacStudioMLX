"""4.17.5 (fleet): the voice separator install survives a bad connection, and
when it does fail it says why.

THE FIELD SHAPE (4.17.4): separator_install failed with weights_failed on two
installs (the package installed, the 80 MB htdemucs model did not download)
and pip_failed on one (twice, 33 s apart, at panel boot). demucs fetches its
model through torch.hub in ONE request - no timeout, no resume - from
dl.fbaipublicfiles.com; the package step ran once, and a dropped connection
ended both. Neither failure said whether the network, the disk or the
resolver was to blame.

THE FIX, each part tested here:
  * a2v_stems_deps.sh retries a NETWORK or TIMEOUT package failure 3 times -
    and only those (a resolver conflict fails the same way again); it records
    the cause (install_cause.sh words) next to the class, prints a plain last
    line the panel's card shows with its Try again, and never lets a raw
    "error:" line reach Pinokio (which ends the whole Install/Update on it);
  * a2v_separate.py --prefetch downloads the model itself into torch.hub's
    own cache path: resumable (HTTP Range), with a timeout, four attempts, a
    sha256 check the way torch.hub checks it, and an optional mirror;
  * the panel's separator_install event carries the cause.
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")

import a2v_separate as SEP                                           # noqa: E402

SCRIPT = ROOT / "scripts" / "pinokio" / "a2v_stems_deps.sh"
CAUSE = ROOT / "scripts" / "pinokio" / "install_cause.sh"
PINOKIO_BREAK = re.compile(r"error:|errno ", re.I)


def _x(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class _App:
    """The installer in a fake app tree. A fake engine `python` answers the pin
    dump, the import probe (ok once uv "installed") and the runner's prefetch;
    a fake `uv` fails `uv_fails` times with `uv_text` first."""

    def __init__(self, uv_fails=0, uv_text="", prefetch_ok=True):
        self.tmp = Path(tempfile.mkdtemp(prefix="sep-"))
        pin = self.tmp / "scripts" / "pinokio"
        pin.mkdir(parents=True)
        shutil.copy(SCRIPT, pin / SCRIPT.name)
        shutil.copy(CAUSE, pin / CAUSE.name)
        (self.tmp / "scripts" / "a2v_separate.py").write_text("# fake runner\n")
        self.mlx = self.tmp / "ltx-2-mlx"
        (self.mlx / "env" / "bin").mkdir(parents=True)
        self.mark = self.tmp / "installed.mark"
        self.count = self.tmp / "uv.count"
        self.count.write_text("0")
        prefetch = ("echo 'vocal separator ready'; exit 0" if prefetch_ok else
                    "echo 'separator weights: attempt 4 of 4 did not finish (URLError: "
                    "<urlopen error [Errno 8] nodename nor servname provided>)' >&2; "
                    "echo 'Traceback (most recent call last):' >&2; exit 1")
        _x(self.mlx / "env" / "bin" / "python3.11",
           "#!/bin/bash\n"
           f"if [ \"$1\" = - ]; then cat >/dev/null; echo 'torch==2.11.0'; exit 0; fi\n"
           f"if [ \"$1\" = -c ]; then [ -f '{self.mark}' ] && exit 0;"
           " echo \"ModuleNotFoundError: No module named 'demucs'\" >&2; exit 1; fi\n"
           f"case \"$*\" in *--prefetch*) {prefetch} ;; esac\nexit 0\n")
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        _x(self.bin / "uv",
           "#!/bin/bash\n"
           f"n=$(cat '{self.count}'); n=$((n+1)); echo $n > '{self.count}'\n"
           f"if [ $n -le {uv_fails} ]; then printf '%s\\n' \"{uv_text}\" >&2; exit 2; fi\n"
           f"touch '{self.mark}'; echo 'Installed 11 packages'\n")

    def run(self):
        env = dict(os.environ, PATH=f"{self.bin}:/usr/bin:/bin",
                   PHOSPHENE_SEPARATOR_RETRY_WAIT="0",
                   PHOSPHENE_SEPARATOR_HOME=str(self.tmp / "demucs"))
        return subprocess.run(["bash", str(self.tmp / "scripts" / "pinokio" / SCRIPT.name),
                               str(self.mlx)], capture_output=True, text=True,
                              env=env, timeout=60)

    def record(self) -> dict:
        return json.loads((self.tmp / "demucs" / "last_install.json").read_text())


NET = "error: Failed to fetch: https://pypi.org/simple/demucs/\n  Caused by: Connection reset by peer"
RESOLVER = ("  x No solution found when resolving dependencies:\n  Because torchaudio==2.11.0 "
            "depends on torch==2.11.0 and you require torch==2.1.0, we can conclude")


class TheInstaller(unittest.TestCase):
    def test_a_dropped_connection_is_retried(self):
        app = _App(uv_fails=2, uv_text=NET)
        r = app.run()
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertEqual(app.count.read_text().strip(), "3")
        self.assertEqual(app.record()["outcome"], "ok")
        self.assertIsNone(PINOKIO_BREAK.search(out),
                          "a retried attempt printed a line Pinokio stops on")

    def test_a_resolver_conflict_is_not_retried_and_says_so(self):
        app = _App(uv_fails=9, uv_text=RESOLVER)
        r = app.run()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(app.count.read_text().strip(), "1")
        rec = app.record()
        self.assertEqual((rec["outcome"], rec["error_class"], rec["cause"]),
                         ("failed", "pip_failed", "uv_error"))
        self.assertIn("could not resolve demucs", r.stderr.strip().splitlines()[-1])

    def test_a_network_failure_that_persists_names_the_network(self):
        app = _App(uv_fails=9, uv_text=NET)
        r = app.run()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(app.count.read_text().strip(), "3")
        self.assertEqual(app.record()["cause"], "network")
        self.assertIn("could not be reached", r.stderr.strip().splitlines()[-1])
        self.assertIsNone(PINOKIO_BREAK.search(r.stdout + r.stderr))

    def test_a_weights_failure_records_its_cause(self):
        app = _App(prefetch_ok=False)
        r = app.run()
        self.assertEqual(r.returncode, 1)
        rec = app.record()
        self.assertEqual((rec["error_class"], rec["cause"]), ("weights_failed", "network"))
        self.assertIsNone(PINOKIO_BREAK.search(r.stdout + r.stderr),
                          "the runner's URLError / Errno lines must be defused")

    def test_the_record_carries_no_path(self):
        app = _App(uv_fails=9, uv_text=NET)
        app.run()
        raw = (app.tmp / "demucs" / "last_install.json").read_text()
        self.assertNotIn("/", raw.replace('"via"', ""))


class _Server:
    """A one-file HTTP server: the first GET dies half way, later GETs honour
    Range - a dropped connection and a resume."""

    def __init__(self, payload: bytes, *, cut_first=True, corrupt_first=False):
        self.payload, self.hits = payload, []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.hits.append(self.headers.get("Range"))
                first = len(outer.hits) == 1
                body = outer.payload
                if first and corrupt_first:
                    body = b"x" * len(body)
                rng = self.headers.get("Range")
                start = int(rng.split("=")[1].split("-")[0]) if rng else 0
                self.send_response(206 if rng else 200)
                self.send_header("Content-Length", str(len(body) - start))
                self.end_headers()
                chunk = body[start:]
                if first and cut_first:
                    self.wfile.write(chunk[: len(chunk) // 2])
                    self.wfile.flush()
                    self.connection.shutdown(2)
                    return
                self.wfile.write(chunk)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


class ThePrefetch(unittest.TestCase):
    def setUp(self):
        self.payload = os.urandom(300_000)
        sha = hashlib.sha256(self.payload).hexdigest()
        self.name = f"955717e8-{sha[:8]}.th"
        self.home = Path(tempfile.mkdtemp(prefix="torchhome-"))
        self.env = mock.patch.dict(os.environ, {"TORCH_HOME": str(self.home)})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def _files(self, srv):
        return [(self.name, f"{srv.url}/{self.name}")]

    def dest(self) -> Path:
        return self.home / "hub" / "checkpoints" / self.name

    def test_a_dropped_download_resumes(self):
        srv = _Server(self.payload, cut_first=True)
        try:
            with mock.patch.object(SEP, "model_files", lambda name: self._files(srv)):
                SEP.prefetch("htdemucs", sleep=lambda s: None)
        finally:
            srv.close()
        self.assertEqual(self.dest().read_bytes(), self.payload)
        self.assertIsNone(srv.hits[0])
        self.assertTrue(srv.hits[1] and srv.hits[1].startswith("bytes="),
                        "the second attempt must resume, not start over")

    def test_a_corrupt_file_is_refetched(self):
        srv = _Server(self.payload, cut_first=False, corrupt_first=True)
        try:
            with mock.patch.object(SEP, "model_files", lambda name: self._files(srv)):
                SEP.prefetch("htdemucs", sleep=lambda s: None)
        finally:
            srv.close()
        self.assertEqual(self.dest().read_bytes(), self.payload)

    def test_an_unreachable_host_fails_after_four_attempts(self):
        calls = []
        with mock.patch.object(SEP, "model_files",
                               lambda name: [(self.name, "http://127.0.0.1:9/x.th")]), \
                mock.patch.object(SEP, "DOWNLOAD_TIMEOUT_S", 2):
            with self.assertRaises(SEP.DownloadExhausted):
                SEP.prefetch("htdemucs", sleep=calls.append)
        self.assertEqual(len(calls), 3)

    def test_the_mirror_is_tried_after_the_official_host(self):
        srv = _Server(self.payload, cut_first=False)
        try:
            with mock.patch.object(SEP, "model_files",
                                   lambda name: [(self.name, "http://127.0.0.1:9/" + self.name)]), \
                    mock.patch.object(SEP, "DOWNLOAD_TIMEOUT_S", 2), \
                    mock.patch.dict(os.environ, {"PHOSPHENE_DEMUCS_MIRROR": srv.url}):
                SEP.prefetch("htdemucs", sleep=lambda s: None)
        finally:
            srv.close()
        self.assertEqual(self.dest().read_bytes(), self.payload)

    def test_an_already_complete_file_downloads_nothing(self):
        self.dest().parent.mkdir(parents=True)
        self.dest().write_bytes(self.payload)
        with mock.patch.object(SEP, "_fetch_one", side_effect=AssertionError("fetched")), \
                mock.patch.object(SEP, "model_files",
                                  lambda name: [(self.name, "http://x/" + self.name)]):
            SEP.prefetch("htdemucs", sleep=lambda s: None)

    def test_exhausted_downloads_end_the_prefetch_without_torch_hub(self):
        """Codex 4.17.5: after four failed attempts main() fell through to
        torch.hub's own download - one request, no timeout - which could hang
        an Install for good."""
        loaded = []
        with mock.patch.object(SEP, "prefetch",
                               side_effect=SEP.DownloadExhausted("unreachable")), \
                mock.patch.object(SEP, "load_model", lambda name: loaded.append(name)):
            self.assertEqual(SEP.main(["--prefetch"]), 3)
        self.assertEqual(loaded, [])

    def test_a_table_problem_still_falls_back_with_a_timeout(self):
        import socket
        loaded = []
        saved = socket.getdefaulttimeout()
        try:
            with mock.patch.object(SEP, "prefetch", side_effect=KeyError("htdemucs")), \
                    mock.patch.object(SEP, "load_model", lambda name: loaded.append(name)):
                self.assertEqual(SEP.main(["--prefetch"]), 0)
            self.assertEqual(socket.getdefaulttimeout(), SEP.DOWNLOAD_TIMEOUT_S)
        finally:
            socket.setdefaulttimeout(saved)
        self.assertEqual(loaded, ["htdemucs"])

    def test_the_real_table_names_htdemucs(self):
        try:
            files = SEP.model_files("htdemucs")
        except ImportError:
            self.skipTest("demucs not in this interpreter")
        self.assertEqual(files[0][0], "955717e8-8726e21a.th")
        self.assertTrue(files[0][1].startswith("https://dl.fbaipublicfiles.com/"))


class ThePanelReportsTheCause(unittest.TestCase):
    def test_event_cause(self):
        import mlx_ltx_panel as P
        sent = []
        with mock.patch.object(P, "_analytics_capture", lambda e, p: sent.append(p)):
            P._analytics_separator_install("failed", "update", "weights_failed",
                                           ready=True, weights=False, cause="network")
            P._analytics_separator_install("failed", "update", "pip_failed",
                                           ready=False, weights=False, cause="/etc/x")
            P._analytics_separator_install("ok", "update", ready=True, weights=True,
                                           cause="network")
        self.assertEqual(sent[0]["cause"], "network")
        self.assertEqual(sent[1]["cause"], "other")
        self.assertNotIn("cause", sent[2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
