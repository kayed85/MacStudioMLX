"""4.17.5 (fleet): the Metal watchdog loop on M1/M2 Macs cannot repeat.

THE FIELD SHAPE (4.17.4, the top error for weeks): one M1 Max 32 GB install
rendered LTX Standard 768x448 121f i2v from the form 50 times in 15 hours and
macOS's GPU watchdog killed 48 of them about 30 s in (SIGABRT,
kIOGPUCommandBufferCallbackErrorTimeout); every Retry of the same job worked
(33 of 33). An M2 Max 96 GB and an M2 Pro 32 GB hit it too. Nothing made the
next attempt different, nothing remembered the last one, and nothing stopped
the loop.

THE FIX, each part tested here:
  * M1/M2-class GPUs spawn the helper with smaller Metal command buffers
    (MLX_MAX_OPS_PER_BUFFER / MLX_MAX_MB_PER_BUFFER - what MLX exposes for
    exactly this); a value the user set is never overridden;
  * a watchdog kill re-runs THAT job once, at once, on short GPU steps (one
    op per command buffer), with one plain line in the log, the
    note on the Queue row, and `watchdog_retry` / `gpu_steps` on the event;
  * the setting is remembered: its next run starts short;
  * a kill on short steps refuses the setting up front on this Mac for 24 h
    (or until the next update), with Retry smaller on the card - so the
    50-failure loop becomes at most two failures.
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
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")

import mlx_ltx_panel as P                                            # noqa: E402
from extract_panel_js import extract_function                        # noqa: E402

NODE = shutil.which("node")
WATCHDOG = ("helper exited from SIGABRT (the macOS GPU watchdog killed a Metal "
            "command buffer (kIOGPUCommandBufferCallbackErrorTimeout)); returncode=-6")


def _job(**params) -> dict:
    p = {"mode": "i2v", "engine": "ltx", "quality": "standard", "width": 768,
         "height": 448, "frames": 121}
    p.update(params)
    return {"id": "j1", "status": "running", "params": p}


class _Store(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wd-"))
        self._p = mock.patch.object(P, "METAL_WATCHDOG_FILE", self.tmp / "metal_watchdog.json")
        self._p.start()

    def tearDown(self):
        self._p.stop()
        P._JOB_CTX.job = None


class TheStore(_Store):
    def test_key_is_closed_fields_only(self):
        key = P.metal_watchdog_key(_job(prompt="secret words", image="/Users/x/a.png")["params"])
        self.assertEqual(key, "ltx|i2v|standard|768x448|121")
        self.assertEqual(P.metal_watchdog_key({}), "")

    def test_a_kill_asks_for_short_and_a_short_kill_refuses(self):
        k = "ltx|i2v|standard|768x448|121"
        self.assertFalse(P.metal_watchdog_wants_short(k))
        P.metal_watchdog_record_kill(k, short=False, phase="encode")
        self.assertTrue(P.metal_watchdog_wants_short(k))
        self.assertIsNone(P.metal_watchdog_refusal(k))
        P.metal_watchdog_record_kill(k, short=True, phase="denoise")
        msg = P.metal_watchdog_refusal(k)
        self.assertIn("GPU watchdog stopped this exact render", msg)
        self.assertIn("Retry smaller", msg)
        # ...for 24 hours, then it gets a try again.
        self.assertIsNone(P.metal_watchdog_refusal(k, now=time.time() + 25 * 3600))
        # Other settings are untouched.
        self.assertFalse(P.metal_watchdog_wants_short("ltx|t2v|quick|640x448|121"))
        data = json.loads((self.tmp / "metal_watchdog.json").read_text())
        self.assertEqual(data["settings"][k]["phase"], "denoise")

    def test_source_driven_modes_are_keyed_by_what_they_consume(self):
        """Codex 4.17.5: Retake/Extend take geometry and length from the
        source and their own fields - one big Retake must not pause every
        small one."""
        a = {"mode": "retake", "quality": "balanced", "width": 1280, "height": 704,
             "frames": 121, "video_path": "/x/big.mp4", "retake_start_sec": "0",
             "retake_end_sec": "8"}
        b = dict(a, video_path="/x/small.mp4")
        c = dict(a, retake_end_sec="2")
        keys = {P.metal_watchdog_key(x) for x in (a, b, c)}
        self.assertEqual(len(keys), 3)
        self.assertNotIn("/x/", "".join(keys), "no path in the key")
        e1 = {"mode": "extend", "video_path": "/x/a.mp4", "extend_frames": 5}
        e2 = dict(e1, extend_frames=2)
        self.assertNotEqual(P.metal_watchdog_key(e1), P.metal_watchdog_key(e2))

    def test_the_refusal_names_a_way_out_for_the_mode(self):
        k = "ltx|retake|balanced|1280x704|121|abc"
        P.metal_watchdog_record_kill(k, short=True)
        self.assertIn("shorter extension or section", P.metal_watchdog_refusal(k, mode="retake"))
        self.assertIn("Retry smaller", P.metal_watchdog_refusal(k, mode="i2v"))

    def test_an_update_clears_the_store(self):
        k = "ltx|i2v|standard|768x448|121"
        P.metal_watchdog_record_kill(k, short=True)
        with mock.patch.object(P, "running_version", lambda: "9.9.9"):
            self.assertIsNone(P.metal_watchdog_refusal(k))
            self.assertFalse(P.metal_watchdog_wants_short(k))


class _FakeHelper(P.WarmHelper):
    """A WarmHelper whose renders are scripted: each _run_once call pops the
    next outcome - "ok", "watchdog", or "boom" (an ordinary error)."""

    def __init__(self, outcomes):
        super().__init__()
        self.outcomes = list(outcomes)
        self.runs = []          # the step size each run used

    def _run_once(self, job_spec, timeout=None):
        self._metal_timeout_seen = False
        self._past_prompt_encode = False
        self._last_phase = ""
        self.runs.append(self._gpu_short_wanted)
        what = self.outcomes.pop(0) if self.outcomes else "watchdog"
        if what == "ok":
            return {"event": "done"}
        if what.startswith("watchdog"):
            self._metal_timeout_seen = True
            self._last_phase = "encode" if what == "watchdog_encode" else "denoise"
            self._past_prompt_encode = what != "watchdog_encode"
            raise RuntimeError(WATCHDOG)
        raise RuntimeError("something else broke")


class TheHelperRetriesOnShortSteps(_Store):
    def run_job(self, helper, job):
        P._JOB_CTX.job = job
        try:
            return helper.run({"action": "generate", "id": job["id"], "params": {}})
        finally:
            P._JOB_CTX.job = None

    def test_a_kill_is_rerun_once_on_short_steps(self):
        h = _FakeHelper(["watchdog", "ok"])
        job = _job()
        self.assertEqual(self.run_job(h, job), {"event": "done"})
        self.assertEqual(h.runs, [False, True])
        self.assertTrue(job["watchdog_retry"])
        self.assertEqual(job["watchdog_phase"], "denoise")
        self.assertEqual(job["gpu_steps"], "short")
        self.assertIn(P.GPU_STEPS_NOTE, job["params"]["generation_clamp_notes"])
        self.assertTrue(P.metal_watchdog_wants_short(P.metal_watchdog_key(job["params"])))
        log = "\n".join(list(P.STATE.get("log") or [])[-10:])
        self.assertIn("Rendering it again in short GPU steps", log)

    def test_the_next_run_of_that_setting_starts_short(self):
        self.run_job(_FakeHelper(["watchdog", "ok"]), _job())
        fresh = _FakeHelper(["ok"])                  # a new boot
        job = _job()
        self.run_job(fresh, job)
        self.assertEqual(fresh.runs, [True])
        self.assertEqual(job["gpu_steps"], "short")
        self.assertNotIn("watchdog_retry", job)

    def test_a_kill_during_prompt_encoding_also_shortens_the_encode(self):
        h = _FakeHelper(["watchdog_encode", "ok"])
        self.run_job(h, _job())
        self.assertEqual(h.gemma_max_length, P.WarmHelper.GEMMA_FALLBACK_MAX_LENGTH)
        self.assertEqual(h.runs, [False, True])

    def test_a_kill_on_short_steps_is_not_rerun_and_says_what_next(self):
        h = _FakeHelper(["watchdog", "watchdog"])
        with self.assertRaises(RuntimeError) as cm:
            self.run_job(h, _job())
        self.assertEqual(h.runs, [False, True])
        self.assertIn("24 hours", str(cm.exception))
        self.assertIn("kIOGPUCommandBufferCallbackErrorTimeout", str(cm.exception),
                      "the class must still read as metal_watchdog")
        self.assertEqual(P._analytics_error_class(str(cm.exception)), "metal_watchdog")

    def test_the_fifty_failure_loop_is_impossible(self):
        """The field case: the same setting submitted again and again on a Mac
        where every run dies. At most two renders fail; the rest are refused
        before any GPU work."""
        h = _FakeHelper(["watchdog"] * 200)
        failed = refused = 0
        for _ in range(50):
            try:
                self.run_job(h, _job())
            except P.RenderRefused as exc:
                self.assertEqual(exc.reason, "hardware_tier")
                refused += 1
            except RuntimeError:
                failed += 1
        self.assertEqual(failed, 1, "one job: default run + its short re-run")
        self.assertEqual(len(h.runs), 2)
        self.assertEqual(refused, 49)

    def test_a_restart_keeps_the_shorter_encode_too(self):
        """Codex 4.17.5: a setting saved by short steps AND the 256-token
        encode lost the encode after a restart, then got refused on the next
        encode kill without ever trying it."""
        self.run_job(_FakeHelper(["watchdog_encode", "ok"]), _job())
        fresh = _FakeHelper(["ok"])
        self.run_job(fresh, _job())
        self.assertEqual(fresh.gemma_max_length, P.WarmHelper.GEMMA_FALLBACK_MAX_LENGTH)

    def test_short_steps_still_get_the_unused_encode_retry(self):
        """Short steps already on (another size needed them this boot), then
        an encode kill: the shorter encode is still untried, so it runs again
        with it instead of refusing the size."""
        h = _FakeHelper(["watchdog_encode", "ok"])
        h.gpu_short_session = True
        job = _job()
        self.assertEqual(self.run_job(h, job), {"event": "done"})
        self.assertEqual(h.runs, [True, True])
        self.assertEqual(h.gemma_max_length, P.WarmHelper.GEMMA_FALLBACK_MAX_LENGTH)
        self.assertIsNone(P.metal_watchdog_refusal(P.metal_watchdog_key(job["params"])))

    def test_runs_are_bounded_even_when_every_mitigation_is_tried(self):
        h = _FakeHelper(["watchdog_encode", "watchdog_encode", "watchdog_encode", "ok"])
        with self.assertRaises(RuntimeError):
            self.run_job(h, _job())
        self.assertLessEqual(len(h.runs), 3)

    def test_a_stopped_job_is_not_rerun(self):
        h = _FakeHelper(["watchdog", "ok"])
        job = _job()
        job["cancel_requested"] = True
        with self.assertRaises(RuntimeError):
            self.run_job(h, job)
        self.assertEqual(h.runs, [False])

    def test_an_ordinary_failure_is_not_a_watchdog(self):
        h = _FakeHelper(["boom", "ok"])
        with self.assertRaises(RuntimeError):
            self.run_job(h, _job())
        self.assertEqual(h.runs, [False])
        self.assertFalse(P.metal_watchdog_wants_short("ltx|i2v|standard|768x448|121"))


class _Proc:
    pid = 4242
    stdin = stdout = None

    def poll(self):
        return None


class TheSpawnEnvironment(_Store):
    def spawn(self, helper, chip="M1 Max", extra_env=None):
        captured = {}

        def popen(cmd, **kw):
            captured.update(kw["env"])
            return _Proc()
        env = dict(os.environ)
        for k in list(P.GPU_STEPS_SHORT) + list(P.GPU_STEPS_M1M2):
            env.pop(k, None)
        env.update(extra_env or {})
        with mock.patch.object(P.subprocess, "Popen", popen), \
                mock.patch.object(P, "engine_env_fault", lambda: ""), \
                mock.patch.object(P, "engine_env_busy", lambda: ""), \
                mock.patch.object(P, "_proc_guard_write", lambda *a, **k: None), \
                mock.patch.object(P, "_hw_chip_family", lambda: chip), \
                mock.patch.dict(os.environ, env, clear=True):
            try:
                helper._ensure()
            except Exception:                                 # noqa: BLE001
                pass            # the fake proc has no pipes; the env is what we want
        helper.proc = None
        return captured

    def test_m1_m2_get_smaller_buffers(self):
        env = self.spawn(P.WarmHelper(), chip="M1 Max")
        for k, v in P.GPU_STEPS_M1M2.items():
            self.assertEqual(env.get(k), v)

    def test_m3_and_later_are_untouched(self):
        env = self.spawn(P.WarmHelper(), chip="M4 Max")
        for k in P.GPU_STEPS_M1M2:
            self.assertNotIn(k, env)

    def test_short_steps_on_any_chip(self):
        h = P.WarmHelper()
        h._gpu_short_wanted = True
        env = self.spawn(h, chip="M4 Max")
        for k, v in P.GPU_STEPS_SHORT.items():
            self.assertEqual(env.get(k), v)
        self.assertTrue(h._spawned_gpu_short)

    def test_the_users_own_value_wins(self):
        env = self.spawn(P.WarmHelper(), chip="M2 Pro",
                         extra_env={"MLX_MAX_OPS_PER_BUFFER": "77"})
        self.assertEqual(env["MLX_MAX_OPS_PER_BUFFER"], "77")

    def test_a_live_helper_of_the_other_size_is_retired(self):
        h = P.WarmHelper()
        h._spawned_gpu_short = False
        killed = []
        with mock.patch.object(h, "is_alive", lambda: True), \
                mock.patch.object(h, "kill", lambda: killed.append(1)):
            h._set_gpu_steps(False)
            self.assertEqual(killed, [])
            h._set_gpu_steps(True)
            self.assertEqual(killed, [1])


class TheEventSaysWhatHappened(unittest.TestCase):
    def test_props(self):
        sent = []
        with mock.patch.object(P, "_analytics_capture", lambda e, p: sent.append((e, p))), \
                mock.patch.object(P, "get_settings",
                                  lambda: {"analytics_first_render_reported": True}):
            P._analytics_render_event({"status": "done", "elapsed_sec": 300,
                                       "gpu_steps": "short", "watchdog_retry": True,
                                       "watchdog_phase": "load",
                                       "params": _job()["params"]})
            P._analytics_render_event({"status": "done", "elapsed_sec": 300,
                                       "params": _job()["params"]})
        self.assertEqual(sent[0][1]["gpu_steps"], "short")
        self.assertIs(sent[0][1]["watchdog_retry"], True)
        self.assertEqual(sent[0][1]["watchdog_phase"], "load")
        self.assertNotIn("gpu_steps", sent[1][1])
        self.assertNotIn("watchdog_retry", sent[1][1])


@unittest.skipUnless(NODE, "node not on PATH")
class TheCardOffersSmaller(unittest.TestCase):
    def test_paused_size_card(self):
        js = (ROOT / "webapp" / "js" / "queue.js").read_text()
        fns = "\n".join(extract_function(n, js) for n in
                        ("friendlyJobError", "nowCardFailureActions"))
        refusal = P.metal_watchdog_refusal.__doc__ and (
            "macOS's GPU watchdog stopped this exact render twice on this Mac")
        prog = ("function _redactLocalPaths(s){return s;}\nfunction escapeHtml(s){return s;}\n"
                + fns + "\n"
                "const info = friendlyJobError(" + json.dumps(refusal) + ", 'ltx');\n"
                "const html = nowCardFailureActions({refused_reason:'hardware_tier'}, info);\n"
                "console.log(JSON.stringify({smaller: !!info.smaller, friendly: info.friendly, html}));")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(prog)
        try:
            r = subprocess.run([NODE, fh.name], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            out = json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            os.unlink(fh.name)
        self.assertTrue(out["smaller"])
        self.assertIn('data-action="retry-smaller"', out["html"])
        self.assertNotIn('data-action="retry"', out["html"].replace("retry-smaller", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
