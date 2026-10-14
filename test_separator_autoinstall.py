"""4.17.4 fleet fix: a missing voice separator installs itself; jobs wait.

THE FIELD CASE (fleet, 2026-10-01, one 16 GB M4). The install pressed the
panel's own "Update now" on 4.17.2: /version/pull is `git pull` and nothing
else, and the restart that follows (the "Restart to finish update" pill, or a
Stop/Start) never runs scripts/post_update.sh — so step 7b, the separator
4.17.3 shipped, never ran. 4.17.3 booted at 10:35:41 and its three queued
voice-only Lip-sync jobs were refused within the second (`vocal_separator`),
which tripped queue_paused_breaker (n_failed 3, error_class refused). A
reboot at 11:07 refused again. Nothing in the data said the separator was
missing.

THE FIX, each part tested here:
  (b) at boot the panel installs a missing separator in the background, once
      per process; a voice-only job queued meanwhile WAITS for it (Now card
      says so) and then runs. The auto path is OFF at import, so a test or
      a gate can never pip-install into a real venv.
  (c) a refusal no longer counts toward the queue circuit breaker.
  (d) separator_install telemetry: ok/failed + a closed error class + which
      path ran it, reported once — the installer's own outcome record is read
      at the next boot, never its output text.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")
os.environ.setdefault("PHOSPHENE_DISABLE_VERSION_CHECK", "1")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402
from panel import routes_queue  # noqa: E402


class _Reset(unittest.TestCase):
    def setUp(self):
        self._auto = dict(P.A2V_SEPARATOR_AUTO)
        with P.A2V_SEPARATOR_INSTALL_LOCK:
            self._inst = dict(P.A2V_SEPARATOR_INSTALL)
            P.A2V_SEPARATOR_INSTALL.clear()
            P.A2V_SEPARATOR_INSTALL.update({"state": "idle", "active": False})
        self.tmp = Path(tempfile.mkdtemp(prefix="sep-auto-"))
        self.addCleanup(self._restore)

    def _restore(self):
        P.A2V_SEPARATOR_AUTO.clear()
        P.A2V_SEPARATOR_AUTO.update(self._auto)
        with P.A2V_SEPARATOR_INSTALL_LOCK:
            P.A2V_SEPARATOR_INSTALL.clear()
            P.A2V_SEPARATOR_INSTALL.update(self._inst)


class ImportNeverInstalls(unittest.TestCase):
    def test_auto_install_is_off_until_main_turns_it_on(self):
        self.assertFalse(P.A2V_SEPARATOR_AUTO["enabled"])
        with mock.patch.object(P, "_a2v_separator_command", return_value=None), \
             mock.patch.object(P, "a2v_separator_install_start") as start:
            self.assertFalse(P.a2v_separator_auto_start("job"))
            with self.assertRaises(P.RenderRefused):
                P.a2v_conditioning_audio({"audio_stem_auto": "on"}, "/x/song.wav")
        start.assert_not_called()

    def test_boot_runs_before_the_worker_in_main(self):
        src = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
        main = src.split('if __name__ == "__main__":')[1]
        self.assertLess(main.index("a2v_separator_boot()"),
                        main.index("threading.Thread(target=worker_loop"))


class BootInstallsAMissingSeparator(_Reset):
    def _status(self, ready):
        return {"ready": ready, "weights": False, "package": ready, "torch": True,
                "model": "htdemucs", "install": {}}

    def test_missing_at_boot_starts_one_background_install(self):
        with mock.patch.object(P, "a2v_separator_status", return_value=self._status(False)), \
             mock.patch.object(P, "engine_env_fault", return_value=""), \
             mock.patch.object(P, "_a2v_separator_record", return_value=None), \
             mock.patch.object(P, "a2v_separator_install_start",
                               return_value=(202, {"ok": True})) as start, \
             mock.patch.dict(os.environ, {"PHOSPHENE_SEPARATOR_AUTOINSTALL": ""}):
            P.a2v_separator_boot()
            start.assert_called_once_with("boot")
            self.assertTrue(P.A2V_SEPARATOR_AUTO["enabled"])
            self.assertTrue(P.A2V_SEPARATOR_AUTO["attempted"])
            # ONE automatic attempt per process: a job later does not retry.
            self.assertFalse(P.a2v_separator_auto_start("job"))
            self.assertEqual(start.call_count, 1)

    def test_ready_or_broken_venv_or_opt_out_starts_nothing(self):
        cases = [
            (self._status(True), "", ""),
            (self._status(False), "the engine venv has a Python but no packages", ""),
            (self._status(False), "", "0"),
        ]
        for st, fault, env in cases:
            with self.subTest(fault=fault, env=env):
                P.A2V_SEPARATOR_AUTO.update(enabled=False, attempted=False)
                with mock.patch.object(P, "a2v_separator_status", return_value=st), \
                     mock.patch.object(P, "engine_env_fault", return_value=fault), \
                     mock.patch.object(P, "_a2v_separator_record", return_value=None), \
                     mock.patch.object(P, "a2v_separator_install_start") as start, \
                     mock.patch.dict(os.environ, {"PHOSPHENE_SEPARATOR_AUTOINSTALL": env}):
                    P.a2v_separator_boot()
                start.assert_not_called()


class QueuedVoiceOnlyJobsWait(_Reset):
    def test_queue_add_accepts_while_an_install_runs(self):
        params = {"mode": "a2v", "audio_stem_auto": "on"}
        with P.A2V_SEPARATOR_INSTALL_LOCK:
            P.A2V_SEPARATOR_INSTALL.update(active=True, state="running")
        with mock.patch.object(P, "_a2v_separator_command", return_value=None):
            self.assertIsNone(P.a2v_stem_refusal(params))

    def test_queue_add_still_refuses_once_the_install_failed(self):
        params = {"mode": "a2v", "audio_stem_auto": "on"}
        P.A2V_SEPARATOR_AUTO.update(enabled=True, attempted=True)
        with P.A2V_SEPARATOR_INSTALL_LOCK:
            P.A2V_SEPARATOR_INSTALL.update(active=False, state="failed",
                                           error="the install stopped (exit 1)")
        with mock.patch.object(P, "_a2v_separator_command", return_value=None), \
             mock.patch.object(P, "a2v_separator_status",
                               return_value={"ready": False, "weights": False}):
            note = P.a2v_stem_refusal(params)
        self.assertIn("did not install", note)
        self.assertIn("exit 1", note)
        self.assertIn("full mix", note)

    def test_a_job_waits_for_the_running_install_then_separates(self):
        """The field sequence: the job reaches the worker while the boot
        install runs. Before 4.17.4 it was refused at once."""
        song = self.tmp / "song.wav"
        song.write_bytes(b"RIFF....WAVE")
        stem = self.tmp / "vocals.wav"
        stem.write_bytes(b"RIFF....WAVE")
        with P.A2V_SEPARATOR_INSTALL_LOCK:
            P.A2V_SEPARATOR_INSTALL.update(active=True, state="running",
                                           log=["Installing vocal separation"])
        ready = {"v": False}
        job = {"id": "j1", "params": {"mode": "a2v"}}
        notes: list = []

        def finish():
            time.sleep(1.2)
            notes.append(job.get("waiting_note"))
            ready["v"] = True
            with P.A2V_SEPARATOR_INSTALL_LOCK:
                P.A2V_SEPARATOR_INSTALL.update(active=False, state="done")

        threading.Thread(target=finish, daemon=True).start()
        with mock.patch.object(P, "_a2v_separator_command",
                               side_effect=lambda: ["sep"] if ready["v"] else None), \
             mock.patch.object(P, "a2v_separator_status",
                               side_effect=lambda: {"ready": ready["v"], "weights": True}), \
             mock.patch.object(P, "_thread_job", return_value=job), \
             mock.patch.object(P, "_a2v_separate_vocals", return_value=stem):
            got, note = P.a2v_conditioning_audio({"audio_stem_auto": "on"}, str(song))
        self.assertEqual(got, str(stem))
        self.assertIn("separated vocal", note)
        self.assertTrue(notes and "Installing vocal separation" in notes[0])
        self.assertNotIn("waiting_note", job)

    def test_stop_ends_the_wait(self):
        with P.A2V_SEPARATOR_INSTALL_LOCK:
            P.A2V_SEPARATOR_INSTALL.update(active=True, state="running")
        job = {"id": "j2", "cancel_requested": True}
        with mock.patch.object(P, "_a2v_separator_command", return_value=None), \
             mock.patch.object(P, "_thread_job", return_value=job):
            with self.assertRaises(P.JobCancelled):
                P.a2v_conditioning_audio({"audio_stem_auto": "on"}, "/x/song.wav")

    def test_the_now_card_says_what_it_waits_for(self):
        class _H:
            def _json(self, payload, code=200):
                self.payload, self.code = payload, code
        with P.LOCK:
            saved = P.STATE["current"]
            P.STATE["current"] = {"id": "j3", "params": {"mode": "a2v", "engine": "ltx"},
                                  "started_ts": time.time() - 5,
                                  "waiting_note": "Installing vocal separation (once)"}
        try:
            h = _H()
            routes_queue.get_status(h, P.urlparse("/status"))
        finally:
            with P.LOCK:
                P.STATE["current"] = saved
        prog = h.payload["current"]["progress"]
        self.assertEqual(prog["phase"], "waiting")
        self.assertIn("Installing vocal separation", prog["phase_label"])


class RefusalsDoNotTripTheBreaker(unittest.TestCase):
    def setUp(self):
        self._saved = dict(P._CONSEC_FAIL)
        P._CONSEC_FAIL.update(sig="", n=0)
        self.addCleanup(lambda: P._CONSEC_FAIL.update(self._saved))

    def test_three_refusals_count_zero(self):
        for _ in range(3):
            n = P._breaker_count(P.RenderRefused("vocal_separator", P.A2V_STEM_MISSING_NOTE))
            self.assertEqual(n, 0)
        self.assertEqual(P._CONSEC_FAIL["n"], 0)

    def test_real_failures_still_trip_and_refusals_do_not_break_the_streak(self):
        boom = RuntimeError("model file incomplete: transformer.safetensors")
        self.assertEqual(P._breaker_count(boom), 1)
        self.assertEqual(P._breaker_count(P.RenderRefused("hardware_tier", "no")), 0)
        self.assertEqual(P._breaker_count(boom), 2)
        self.assertEqual(P._breaker_count(boom), 3)

    def test_worker_loop_counts_through_it(self):
        src = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
        body = src.split("def worker_loop() -> None:")[1].split("\ndef ")[0]
        self.assertIn("_breaker_count(exc)", body)
        self.assertNotIn('_CONSEC_FAIL["n"] += 1', body)


class SeparatorInstallTelemetry(_Reset):
    def _spy(self):
        seen: list = []
        return seen, mock.patch.object(P, "_analytics_capture",
                                       side_effect=lambda ev, props=None: seen.append((ev, props)))

    def test_closed_props_only(self):
        seen, spy = self._spy()
        with spy:
            P._analytics_separator_install("failed", "update", "/Users/x/secret path",
                                           ready=False, weights=False)
            P._analytics_separator_install("ok", "panel_boot", "", ready=True, weights=True)
            P._analytics_separator_install("weird", "update", "", ready=True, weights=True)
            P._analytics_separator_install("ok", "somewhere", "", ready=True, weights=True)
        self.assertEqual([e for e, _ in seen], ["separator_install"] * 2)
        failed, ok = seen[0][1], seen[1][1]
        self.assertEqual(failed["error_class"], "other")       # never the raw text
        self.assertEqual(set(failed), {"outcome", "via", "error_class", "ready",
                                       "weights", "version", "ram_gb"})
        self.assertNotIn("error_class", ok)
        self.assertIn("separator_install", P._ANALYTICS_EVENTS)

    def test_boot_reports_the_installers_record_once(self):
        rec_dir = self.tmp / "demucs"
        rec_dir.mkdir()
        (rec_dir / "last_install.json").write_text(json.dumps(
            {"outcome": "failed", "error_class": "pip_failed", "via": "update",
             "ts": 1790000000}))
        store: dict = {}
        seen, spy = self._spy()
        with spy, \
             mock.patch.object(P, "a2v_separator_torch_home", return_value=rec_dir), \
             mock.patch.object(P, "get_settings", side_effect=lambda: dict(store)), \
             mock.patch.object(P, "_settings_set_internal",
                               side_effect=lambda **kv: store.update(kv)), \
             mock.patch.object(P, "a2v_separator_status",
                               return_value={"ready": True, "weights": True}), \
             mock.patch.dict(os.environ, {"PHOSPHENE_SEPARATOR_AUTOINSTALL": "0"}):
            P.a2v_separator_boot()
            P.a2v_separator_boot()          # a second boot does not re-report
        self.assertEqual(len(seen), 1)
        props = seen[0][1]
        self.assertEqual((props["outcome"], props["via"], props["error_class"]),
                         ("failed", "update", "pip_failed"))

    def test_the_update_and_the_panel_tag_who_ran_the_installer(self):
        sh = (ROOT / "scripts" / "post_update.sh").read_text(encoding="utf-8")
        self.assertIn('PHOSPHENE_SEPARATOR_VIA=update bash "$ROOT/scripts/pinokio/a2v_stems_deps.sh"', sh)
        src = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
        self.assertIn('env["PHOSPHENE_SEPARATOR_VIA"] = f"panel_{trigger}"', src)
        doc = (ROOT / "docs" / "ANALYTICS.md").read_text(encoding="utf-8")
        for word in P._SEPARATOR_INSTALL_VIA + P._SEPARATOR_INSTALL_ERRORS:
            self.assertIn(f"`{word}`", doc)


if __name__ == "__main__":
    unittest.main()
