"""4.17.4 fleet fix: a half-installed engine venv is retried, caught, and
repaired from the panel in one click.

THE FIELD SHAPE (fleet, 30 days): `venv_broken` — "the engine venv has a
Python but no packages — ltx_pipelines_mlx never finished installing" — on 18
installs, nearly every one at the VERY FIRST BOOT (app_installed and the
failed engine_env check in the same second). Most recovered minutes later
with no repair at all, some on the same running panel: the panel came up
while Pinokio's Install was still writing the venv. Others gave up (one after
three failed renders). The only repair was the whole install.js, and only
from a stopped panel; the failure card offered Retry, which fails in one
second the same way.

THE FIX, each part tested here:
  * scripts/pinokio/ltx_engine_env.sh is install.js's engine step: retries a
    dropped connection (without printing a bare "error:", which would end the
    Pinokio run on the first attempt), VERIFIES what the panel checks, holds
    a lock for its whole run, and fails with "FATAL error:" when it cannot.
  * the panel reads that lock: "still installing" is not "broken", and it
    never starts a second installer into the same venv.
  * POST /engine/repair re-runs only that step; /status.engine_env drives a
    bar with the button; a venv_broken failure card offers Repair engine.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")
os.environ.setdefault("PHOSPHENE_DISABLE_VERSION_CHECK", "1")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mlx_ltx_panel as P  # noqa: E402
from extract_panel_js import extract_function  # noqa: E402
from panel import routes as R  # noqa: E402
from panel import routes_models  # noqa: E402

SCRIPT = ROOT / "scripts" / "pinokio" / "ltx_engine_env.sh"
CHECK = ROOT / "scripts" / "pinokio" / "engine_env_check.py"
PINOKIO_BREAK = re.compile(r"error:|errno ", re.I)   # Pinokio 8.2.0's defaults
NODE = shutil.which("node")


def _lock(env: Path, pid: int) -> None:
    d = env / P.ENGINE_ENV_LOCK_NAME
    d.mkdir(exist_ok=True)
    (d / "pid").write_text(f"{pid}\n0\n")


def _x(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class _App:
    """A copy of the two scripts in a fake app tree, a fake engine venv whose
    `python` answers the probes, and a fake `uv` first on PATH. `uv_fails`
    = how many uv calls fail (with uv's own "error:" text) before they work;
    the engine check passes once the --reinstall pass has run."""

    def __init__(self, uv_fails: int = 0, reinstall_fixes: bool = True):
        self.tmp = Path(tempfile.mkdtemp(prefix="engine-env-"))
        pin = self.tmp / "scripts" / "pinokio"
        pin.mkdir(parents=True)
        shutil.copy(SCRIPT, pin / SCRIPT.name)
        shutil.copy(CHECK, pin / CHECK.name)
        self.mlx = self.tmp / "ltx-2-mlx"
        (self.mlx / "env" / "bin").mkdir(parents=True)
        self.mark = self.tmp / "installed.mark"
        self.count = self.tmp / "uv.count"
        self.count.write_text("0")
        _x(self.mlx / "env" / "bin" / "python",
           "#!/bin/bash\n"
           "case \"$1\" in\n"
           "  -c) exit 0 ;;\n"
           "  --version) echo 'Python 3.11.15'; exit 0 ;;\n"
           "  *engine_env_check.py) if [ -f '%s' ]; then echo 'engine check: ok'; exit 0;"
           " else echo 'engine check: import ltx_pipelines_mlx fails: ModuleNotFoundError';"
           " exit 1; fi ;;\n"
           "esac\nexit 0\n" % self.mark)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        _x(self.bin / "uv",
           "#!/bin/bash\n"
           "n=$(cat '%s'); n=$((n+1)); echo $n > '%s'\n"
           "if [ $n -le %d ]; then echo 'error: Failed to fetch: https://pypi.org/simple/mlx/'"
           " >&2; echo '  Caused by: [Errno 54] Connection reset by peer' >&2; exit 2; fi\n"
           "case \"$*\" in *--reinstall*) %s ;; esac\n"
           "echo 'Installed 3 packages'\n"
           % (self.count, self.count, uv_fails,
              f"touch '{self.mark}'" if reinstall_fixes else ":"))

    def run(self, *extra_env, timeout=60):
        env = dict(os.environ, PATH=f"{self.bin}:/usr/bin:/bin",
                   PHOSPHENE_ENGINE_RETRY_WAIT="0")
        for kv in extra_env:
            k, v = kv.split("=", 1)
            env[k] = v
        return subprocess.run(["bash", str(self.tmp / "scripts" / "pinokio" / SCRIPT.name),
                               str(self.mlx)], capture_output=True, text=True,
                              env=env, timeout=timeout)


class TheEngineStep(unittest.TestCase):
    def test_shape(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK))
        self.assertEqual(subprocess.run(["bash", "-n", str(SCRIPT)]).returncode, 0)
        for n, line in enumerate(SCRIPT.read_text().splitlines(), 1):
            self.assertLessEqual(len(line), 350, f"line {n}: Pinokio 8 long-line hang")

    def test_a_dropped_connection_is_retried_and_never_ends_the_pinokio_run(self):
        app = _App(uv_fails=1)
        r = app.run()
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, out)
        self.assertIn("attempt 2 of 3", out)
        self.assertIn("engine environment ready", out)
        self.assertIsNone(PINOKIO_BREAK.search(out),
                          "a retried attempt printed a line Pinokio stops the Install on")
        self.assertFalse((app.mlx / "env" / P.ENGINE_ENV_LOCK_NAME).exists())

    def test_three_failures_end_loudly(self):
        app = _App(uv_fails=99)
        r = app.run()
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 1)
        hits = [l for l in out.splitlines() if PINOKIO_BREAK.search(l)]
        self.assertEqual(len(hits), 1, hits)
        self.assertTrue(hits[0].startswith("FATAL error: the engine environment did not install"))
        self.assertFalse((app.mlx / "env" / P.ENGINE_ENV_LOCK_NAME).exists())

    def test_uv_exiting_zero_is_not_enough_the_result_is_verified(self):
        """The editable-links-only shape: every uv call 'works', the package
        directories never land. It used to carry on to the downloads."""
        app = _App(uv_fails=0, reinstall_fixes=False)
        r = app.run()
        self.assertEqual(r.returncode, 1)
        self.assertIn("ltx_pipelines_mlx", r.stdout)
        self.assertIn("FATAL error:", r.stdout)

    def test_a_second_run_refuses_while_one_holds_the_lock(self):
        app = _App()
        _lock(app.mlx / "env", os.getpid())
        r = app.run()
        self.assertEqual(r.returncode, 1)
        self.assertIn("another engine install is running", r.stdout)
        self.assertEqual(app.count.read_text().strip(), "0", "it installed anyway")

    def test_a_lock_left_by_a_dead_run_does_not_block(self):
        app = _App()
        _lock(app.mlx / "env", 999999)
        self.assertEqual(app.run().returncode, 0)

    def test_install_js_runs_the_step_and_keeps_no_second_copy(self):
        src = (ROOT / "install.js").read_text(encoding="utf-8")
        runnable = "\n".join(l for l in src.splitlines() if not l.strip().startswith("//"))
        self.assertIn('message: "bash scripts/pinokio/ltx_engine_env.sh"', runnable)
        self.assertNotIn("--reinstall --no-deps", runnable)
        # ...and the import gate still follows it.
        self.assertLess(runnable.index("ltx_engine_env.sh"),
                        runnable.index("import ltx_core_mlx, ltx_pipelines_mlx, mlx"))

    def test_the_check_asks_the_panels_question(self):
        """engine_env_check.py fails exactly where engine_env_fault() does."""
        src = CHECK.read_text(encoding="utf-8")
        self.assertIn('"ltx_pipelines_mlx"', src)
        self.assertIn("is_dir()", src)


class _Venv:
    def __init__(self, package=False):
        self.package = package

    def __enter__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="engine-venv-"))
        env = self.tmp / "ltx-2-mlx" / "env"
        (env / "bin").mkdir(parents=True)
        (env / "pyvenv.cfg").write_text("home = /usr/bin\n")
        self.site = env / "lib" / "python3.11" / "site-packages"
        self.site.mkdir(parents=True)
        (env / "bin" / "python3.11").write_text("#!/bin/sh\n")
        if self.package:
            (self.site / "ltx_pipelines_mlx").mkdir()
        self.env = env
        self.prev = P.HELPER_PYTHON
        P.HELPER_PYTHON = env / "bin" / "python3.11"
        with P.ENGINE_REPAIR_LOCK:
            self.prev_rep = dict(P.ENGINE_REPAIR)
            P.ENGINE_REPAIR.clear()
            P.ENGINE_REPAIR.update({"state": "idle", "active": False})
        return self

    def __exit__(self, *a):
        P.HELPER_PYTHON = self.prev
        with P.ENGINE_REPAIR_LOCK:
            P.ENGINE_REPAIR.clear()
            P.ENGINE_REPAIR.update(self.prev_rep)


class ThePanel(unittest.TestCase):
    def test_status_says_installing_while_the_lock_is_alive(self):
        with _Venv() as v:
            st = P.engine_env_status()
            self.assertFalse(st["ok"])
            self.assertFalse(st["installing"])
            _lock(v.env, os.getpid())
            self.assertTrue(P.engine_env_status()["installing"])
            code, payload = P.engine_env_repair_start()
            self.assertEqual(code, 409)
            self.assertTrue(payload.get("installing"))
            _lock(v.env, 999999)
            self.assertFalse(P.engine_env_status()["installing"])

    def test_a_healthy_engine_has_nothing_to_repair(self):
        with _Venv(package=True):
            self.assertEqual(P.engine_env_repair_start()[0], 200)
            self.assertTrue(P.engine_env_status()["ok"])

    def test_repair_runs_only_the_engine_step_and_clears_the_fault(self):
        with _Venv() as v:
            fake = v.tmp / "fake_engine_env.sh"
            fake.write_text(
                "#!/bin/bash\necho 'attempt 1 of 3'\n"
                f"mkdir -p '{v.site}/ltx_pipelines_mlx'\n"
                "echo 'engine environment ready'\n")
            steps = []
            with mock.patch.object(P, "ENGINE_ENV_INSTALLER", str(fake)), \
                 mock.patch.object(P, "ROOT", Path("/")), \
                 mock.patch.object(P, "_music_install_env", return_value=dict(os.environ)), \
                 mock.patch.object(P, "_analytics_install_step",
                                   side_effect=lambda *a, **k: steps.append(a)):
                code, _ = P.engine_env_repair_start()
                self.assertEqual(code, 202)
                for _ in range(100):
                    if not P.engine_env_status()["repair"]["active"]:
                        break
                    time.sleep(0.05)
            st = P.engine_env_status()
            self.assertTrue(st["ok"], st)
            self.assertEqual(st["repair"]["state"], "done")
            self.assertIn("engine environment ready", st["repair"]["log"])
            self.assertEqual(steps, [("engine_env", "ok", "")])

    def test_a_failed_repair_names_the_scripts_reason(self):
        with _Venv() as v:
            fake = v.tmp / "fake_engine_env.sh"
            fake.write_text("#!/bin/bash\necho 'FATAL error: the engine environment "
                            "did not install after 3 attempts.'\nexit 1\n")
            with mock.patch.object(P, "ENGINE_ENV_INSTALLER", str(fake)), \
                 mock.patch.object(P, "ROOT", Path("/")), \
                 mock.patch.object(P, "_music_install_env", return_value=dict(os.environ)), \
                 mock.patch.object(P, "_analytics_install_step"):
                P.engine_env_repair_start()
                for _ in range(100):
                    if not P.engine_env_status()["repair"]["active"]:
                        break
                    time.sleep(0.05)
            rep = P.engine_env_status()["repair"]
            self.assertEqual(rep["state"], "failed")
            self.assertIn("did not install after 3 attempts", rep["error"])

    def test_route_and_status_field(self):
        self.assertIs(R.POST_ROUTES["/engine/repair"], routes_models.post_engine_repair)
        src = (ROOT / "panel" / "routes_queue.py").read_text(encoding="utf-8")
        self.assertIn('payload["engine_env"] = P.engine_env_status()', src)

    def test_the_render_error_still_files_as_venv_broken_and_names_the_button(self):
        msg = (f"engine venv is not usable: the engine venv has a Python but no "
               f"packages. {P.ENGINE_ENV_REPAIR}")
        self.assertEqual(P._analytics_error_class(msg), "venv_broken")
        self.assertIn("Repair engine", P.ENGINE_ENV_REPAIR)


class CodexFindings(unittest.TestCase):
    """The one Codex pass over 4.17.4 (codex4174/review.log), each pinned."""

    def test_1_not_ready_while_a_repair_is_still_writing_the_venv(self):
        with _Venv(package=True):
            with P.ENGINE_REPAIR_LOCK:
                P.ENGINE_REPAIR.update(active=True, state="running")
            st = P.engine_env_status()
            self.assertFalse(st["ok"], "the package dir appeared mid-repair and read as ready")
            self.assertTrue(st["installing"])
            self.assertTrue(P.engine_env_busy())
            helper = P.WarmHelper.__new__(P.WarmHelper)
            helper.lock = __import__("threading").Lock()
            helper.proc = None
            with self.assertRaises(RuntimeError) as cm:
                helper._ensure()
            self.assertIn("being installed or repaired", str(cm.exception))

    def test_2_the_engine_step_reapplies_the_codec_patch(self):
        src = SCRIPT.read_text(encoding="utf-8")
        body = src.split("attempt() {")[1].split("\n}")[0]
        self.assertLess(body.index("--reinstall"), body.index("patch_ltx_codec.py"))
        self.assertLess(body.index("patch_ltx_codec.py"), body.index("engine_env_check.py"))

    def test_3_a_hung_installer_is_ended_at_the_deadline(self):
        with _Venv() as v:
            fake = v.tmp / "hang.sh"
            fake.write_text("#!/bin/bash\necho starting\nsleep 600\n")
            with mock.patch.object(P, "ENGINE_ENV_INSTALLER", str(fake)), \
                 mock.patch.object(P, "ROOT", Path("/")), \
                 mock.patch.object(P, "ENGINE_REPAIR_TIMEOUT_S", 1.0), \
                 mock.patch.object(P, "_music_install_env", return_value=dict(os.environ)), \
                 mock.patch.object(P, "_analytics_install_step"):
                P.engine_env_repair_start()
                for _ in range(200):
                    if not P.engine_env_status()["repair"]["active"]:
                        break
                    time.sleep(0.05)
            rep = P.engine_env_status()["repair"]
            self.assertFalse(rep["active"], "the repair stayed active past its deadline")
            self.assertIn("longer than 30 minutes", rep["error"])

    def test_4_a_running_separator_install_is_waited_for_even_if_files_exist(self):
        calls = []
        with P.A2V_SEPARATOR_INSTALL_LOCK:
            saved = dict(P.A2V_SEPARATOR_INSTALL)
            P.A2V_SEPARATOR_INSTALL.update(active=True, state="running")
        try:
            def fake_wait(job):
                calls.append("wait")
                with P.A2V_SEPARATOR_INSTALL_LOCK:
                    P.A2V_SEPARATOR_INSTALL.update(active=False, state="done")
            stem = Path(tempfile.mkdtemp(prefix="sep-")) / "v.wav"
            stem.write_bytes(b"RIFF")
            with mock.patch.object(P, "_a2v_separator_command", return_value=["sep"]), \
                 mock.patch.object(P, "_a2v_separator_wait", side_effect=fake_wait), \
                 mock.patch.object(P, "_a2v_separate_vocals",
                                   side_effect=lambda *a: calls.append("separate") or stem):
                P.a2v_conditioning_audio({"audio_stem_auto": "on"}, "/x/song.wav")
        finally:
            with P.A2V_SEPARATOR_INSTALL_LOCK:
                P.A2V_SEPARATOR_INSTALL.clear()
                P.A2V_SEPARATOR_INSTALL.update(saved)
        self.assertEqual(calls, ["wait", "separate"])

    def test_5_two_runs_started_together_install_once(self):
        app = _App()
        # A slower uv so the two runs overlap for real.
        uv = app.bin / "uv"
        uv.write_text(uv.read_text().replace("echo 'Installed 3 packages'",
                                             "sleep 0.3; echo 'Installed 3 packages'"))
        env = dict(os.environ, PATH=f"{app.bin}:/usr/bin:/bin", PHOSPHENE_ENGINE_RETRY_WAIT="0")
        cmd = ["bash", str(app.tmp / "scripts" / "pinokio" / SCRIPT.name), str(app.mlx)]
        a = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, text=True)
        b = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, text=True)
        outs = [p.communicate(timeout=60)[0] for p in (a, b)]
        codes = sorted([a.returncode, b.returncode])
        self.assertEqual(codes, [0, 1], outs)
        self.assertTrue(any("another engine install" in o for o in outs), outs)
        self.assertEqual(int(app.count.read_text()), 5, "both runs installed")
        self.assertFalse((app.mlx / "env" / P.ENGINE_ENV_LOCK_NAME).exists())


def _node(fns: list[str], calls: list[str]) -> list:
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
    src = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
    code = ("function escapeHtml(s){return String(s).replace(/[&<>\"]/g,'');}\n"
            + "\n".join(extract_function(n, src) for n in fns)
            + "\nconsole.log(JSON.stringify([" + ",".join(calls) + "]));")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(code)
        path = fh.name
    r = subprocess.run([NODE, path], capture_output=True, text=True,
                       errors="replace", timeout=60)
    if r.returncode != 0:
        raise AssertionError(r.stderr[-2000:])
    return json.loads(r.stdout.strip().splitlines()[-1])


class TheCardAndTheBar(unittest.TestCase):
    RAW = ("engine venv is not usable: the engine venv has a Python but no packages "
           "— ltx_pipelines_mlx never finished installing into it. ")

    def test_the_failure_card_offers_repair_not_retry(self):
        raw = self.RAW + P.ENGINE_ENV_REPAIR
        job = {"id": "j1", "status": "failed", "error": raw}
        fr, html = _node(
            ["_redactLocalPaths", "friendlyJobError", "nowCardFailureActions"],
            [f"friendlyJobError({json.dumps(raw)})",
             f"nowCardFailureActions({json.dumps(job)}, friendlyJobError({json.dumps(raw)}))"])
        self.assertEqual(fr["action"], "repair_engine")
        self.assertIn("did not finish installing", fr["friendly"])
        self.assertIn('data-action="repair-engine"', html)
        self.assertNotIn('data-action="retry"', html)

    def test_the_bar_says_installing_broken_or_failed(self):
        broken = {"ok": False, "installing": False, "repair": {"state": "idle"}}
        busy = {"ok": False, "installing": True,
                "repair": {"state": "running", "log": ["patched /Users/someone/x/video_vae.py"]}}
        failed = {"ok": False, "installing": False,
                  "repair": {"state": "failed", "error": "no network"}}
        ok = {"ok": True}
        b, i, f, o = _node(["_redactLocalPaths", "engineEnvBannerHtml"],
                           [f"engineEnvBannerHtml({json.dumps(x)})"
                            for x in (broken, busy, failed, ok)])
        self.assertIn('data-action="repair-engine"', b)
        self.assertIn("Repair engine", b)
        self.assertIn("Installing the render engine", i)
        self.assertNotIn("data-action", i, "no second installer while one runs")
        self.assertNotIn("/Users/", i, "a home-directory path reached the bar")
        self.assertIn("no network", f)
        self.assertIn("Try again", f)
        self.assertEqual(o, "")

    def test_the_click_is_wired(self):
        main = (ROOT / "webapp" / "js" / "main.js").read_text(encoding="utf-8")
        self.assertIn('[data-action="repair-engine"]', main)
        self.assertIn("engineRepairStart()", main)
        q = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
        self.assertIn("renderEngineEnvBanner(s.engine_env)", q)
        self.assertRegex(q, r"Object\.assign\(globalThis, \{\s*openFailedJobInForm, engineRepairStart,")


if __name__ == "__main__":
    unittest.main()
