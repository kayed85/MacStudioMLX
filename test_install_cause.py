"""4.17.5 (fleet): a failed install step says WHY, in one closed word.

THE FIELD SHAPE (4.17.4): four fresh installs reported install_step
engine_env failed (venv_broken); two recovered, two never rendered, and the
event carried nothing about the cause. Two of the four booted the panel in
the same second as app_installed - Pinokio's Install was still writing the
venv. Separator installs failed as weights_failed / pip_failed, likewise with
no reason.

THE FIX, each part tested here:
  * scripts/pinokio/install_cause.sh maps a failed step's own output to one
    word - network, timeout, disk, uv_error, python_missing, other - and never
    mistakes a package NAME (pyopenssl, async-timeout) for a cause;
  * ltx_engine_env.sh records {"outcome","cause","ts"} in the venv on every
    finished run, and its FATAL line names the cause in plain words;
  * the panel's engine_env_cause() answers installing (Install still running),
    python_missing, the record's cause, or unknown; install_step engine_env
    carries it as `cause`; a changed cause is reported again, and an Install
    that was mid-way at boot is reported when it ends;
  * the Repair bar says the cause in the user's words.
"""
from __future__ import annotations

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
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")

import mlx_ltx_panel as P                                            # noqa: E402
import test_engine_env_repair as T                                   # noqa: E402
from extract_panel_js import extract_function                        # noqa: E402

CAUSE = ROOT / "scripts" / "pinokio" / "install_cause.sh"
NODE = shutil.which("node")


def cause_of(text: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as fh:
        fh.write(text)
        log = fh.name
    try:
        r = subprocess.run(["bash", "-c", f'. "{CAUSE}"; install_cause "{log}"'],
                           capture_output=True, text=True, timeout=20)
        return r.stdout.strip()
    finally:
        os.unlink(log)


class TheCauseWords(unittest.TestCase):
    CASES = {
        "disk": ["error: Failed to write to the cache: No space left on device (os error 28)"],
        "timeout": ["error: Failed to download `torch==2.11.0`\n  Caused by: error sending "
                    "request for url (https://files.pythonhosted.org/x)\n  Caused by: operation timed out",
                    "urllib.error.URLError: <urlopen error timed out>"],
        "network": ["error: Failed to fetch: https://pypi.org/simple/mlx/\n"
                    "  Caused by: [Errno 54] Connection reset by peer",
                    "error: dns error: failed to lookup address information: nodename nor servname provided",
                    "ssl.SSLCertVerificationError: certificate verify failed: unable to get local issuer"],
        "uv_error": ["  x No solution found when resolving dependencies:\n  Because torchaudio==2.11.0 "
                     "depends on torch==2.11.0 and you require torch==2.1.0, we can conclude...",
                     "bash: uv: command not found"],
        "python_missing": ["FATAL error: the engine venv has no working Python - run Install"],
        "other": ["something else entirely went wrong"],
    }

    def test_each_cause(self):
        for want, texts in self.CASES.items():
            for text in texts:
                with self.subTest(want=want, text=text[:40]):
                    self.assertEqual(cause_of(text), want)

    def test_package_names_are_not_causes(self):
        """uv lists what it installed; a resolver failure in the same log must
        not read as a network or timeout problem because of a NAME."""
        log = (" + pyopenssl==24.2.1\n + async-timeout==4.0.3\n + requests-toolbelt==1.0\n"
               "  x No solution found when resolving dependencies:\n")
        self.assertEqual(cause_of(log), "uv_error")

    def test_a_missing_log_is_other(self):
        r = subprocess.run(["bash", "-c", f'. "{CAUSE}"; install_cause /nonexistent/x.log'],
                           capture_output=True, text=True, timeout=20)
        self.assertEqual(r.stdout.strip(), "other")

    def test_the_word_list_matches_the_panel(self):
        for word in ("network", "timeout", "disk", "uv_error", "python_missing", "other"):
            self.assertIn(word, P.ENGINE_ENV_CAUSES)
            self.assertIn(f"echo {word}", CAUSE.read_text())


class _App(T._App):
    """The engine step in a fake app tree, with the classifier beside it."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        shutil.copy(CAUSE, self.tmp / "scripts" / "pinokio" / CAUSE.name)

    def record(self) -> dict:
        return json.loads((self.mlx / "env" / P.ENGINE_ENV_RESULT_NAME).read_text())


class TheEngineStepRecordsWhy(unittest.TestCase):
    def test_a_network_failure_is_recorded_and_said(self):
        app = _App(uv_fails=99)
        r = app.run()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(app.record()["outcome"], "failed")
        self.assertEqual(app.record()["cause"], "network")
        fatal = [l for l in r.stdout.splitlines() if l.startswith("FATAL error:")]
        self.assertEqual(len(fatal), 1, r.stdout)
        self.assertIn("(network:", fatal[0])
        self.assertIn("check the network", fatal[0])

    def test_success_records_ok(self):
        app = _App(uv_fails=1)
        self.assertEqual(app.run().returncode, 0)
        self.assertEqual(app.record()["outcome"], "ok")

    def test_no_working_python_is_python_missing(self):
        app = _App()
        (app.mlx / "env" / "bin" / "python").write_text("#!/bin/bash\nexit 1\n")
        r = app.run()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(app.record()["cause"], "python_missing")

    def test_without_the_classifier_it_still_runs(self):
        """An older tree mid-update: the script must not die on the source."""
        app = T._App(uv_fails=99)
        r = app.run()
        self.assertEqual(r.returncode, 1)
        self.assertIn("FATAL error:", r.stdout)
        self.assertNotIn("No such file", r.stdout + r.stderr)

    def test_the_raw_errors_never_reach_pinokio(self):
        app = _App(uv_fails=2)
        r = app.run()
        self.assertEqual(r.returncode, 0)
        self.assertIsNone(T.PINOKIO_BREAK.search(r.stdout + r.stderr))


class ThePanelSaysWhy(unittest.TestCase):
    def test_cause_order(self):
        with T._Venv() as v:
            self.assertEqual(P.engine_env_cause(), "unknown")
            (v.env / P.ENGINE_ENV_RESULT_NAME).write_text(
                json.dumps({"outcome": "failed", "cause": "disk", "ts": 1}))
            self.assertEqual(P.engine_env_cause(), "disk")
            self.assertEqual(P.engine_env_status().get("cause"), "disk")
            (v.env / P.ENGINE_ENV_RESULT_NAME).write_text(
                json.dumps({"outcome": "failed", "cause": "/Users/x/evil", "ts": 1}))
            self.assertEqual(P.engine_env_cause(), "unknown",
                             "a word outside the vocabulary must never travel")
            T._lock(v.env, os.getpid())
            self.assertEqual(P.engine_env_cause(), "installing")
            T._lock(v.env, 999999)
            P.HELPER_PYTHON = v.env / "bin" / "python3.11.gone"
            self.assertEqual(P.engine_env_cause(), "python_missing")

    def test_the_event_carries_the_cause_and_reports_a_change(self):
        store = {"analytics_install_steps": {}}
        sent = []
        with mock.patch.object(P, "get_settings", lambda: store), \
                mock.patch.object(P, "_settings_set_internal", lambda **kw: store.update(kw)), \
                mock.patch.object(P, "_analytics_capture", lambda e, p: sent.append((e, p))):
            P._analytics_install_step("engine_env", "failed", "venv_broken", cause="installing")
            P._analytics_install_step("engine_env", "failed", "venv_broken", cause="installing")
            P._analytics_install_step("engine_env", "failed", "venv_broken", cause="network")
            P._analytics_install_step("engine_env", "ok")
            P._analytics_install_step("engine_env", "failed", "venv_broken", cause="bogus")
        self.assertEqual([p.get("cause") for _, p in sent],
                         ["installing", "network", None, "other"])
        self.assertTrue(all(e == "install_step" for e, _ in sent))

    def test_an_install_that_ends_after_boot_is_reported(self):
        steps = []
        with T._Venv() as v, mock.patch.object(
                P, "_analytics_install_step",
                side_effect=lambda *a, **k: steps.append((a, k))):
            P._ENGINE_ENV_WATCH["installing"] = None
            T._lock(v.env, os.getpid())
            P.engine_env_status()                 # Install running
            self.assertEqual(steps, [])
            (v.env / P.ENGINE_ENV_RESULT_NAME).write_text(
                json.dumps({"outcome": "failed", "cause": "timeout", "ts": 2}))
            T._lock(v.env, 999999)                # Install ended, still broken
            P.engine_env_status()
            P.engine_env_status()                 # once, not per poll
        self.assertEqual(len(steps), 1, steps)
        self.assertEqual(steps[0][0][:2], ("engine_env", "failed"))
        self.assertEqual(steps[0][1].get("cause"), "timeout")


@unittest.skipUnless(NODE, "node not on PATH")
class TheBarSaysWhy(unittest.TestCase):
    def test_bar_text(self):
        js = (ROOT / "webapp" / "js" / "queue.js").read_text()
        fn = extract_function("engineEnvBannerHtml", js)
        prog = ("function escapeHtml(s){return String(s);}\n"
                "function _redactLocalPaths(s){return s;}\n" + fn + "\n"
                "console.log(JSON.stringify(["
                "engineEnvBannerHtml({ok:false,cause:'network',repair:{}}),"
                "engineEnvBannerHtml({ok:false,cause:'unknown',repair:{}})]));")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(prog)
        try:
            out = subprocess.run([NODE, fh.name], capture_output=True, text=True, timeout=30)
            self.assertEqual(out.returncode, 0, out.stderr)
            net, unk = json.loads(out.stdout)
        finally:
            os.unlink(fh.name)
        self.assertIn("could not reach the package server", net)
        self.assertIn("Repair engine", net)
        self.assertNotIn("package server", unk)


if __name__ == "__main__":
    unittest.main(verbosity=2)
