"""4.17.5 (fleet): an in-panel "Update now" that pulls NOTHING is not an update.

THE FIELD SHAPE: two installs sent update_outcome {stage restart_pending,
outcome failed, from 4.17.4, version 4.17.4}. Both had the update pop-up shown
on 4.17.4 itself, pressed "Update now", and came back on 4.17.4. The pull
found nothing new, but the panel still turned the pill into "Restart
Phosphene" and the banner into "Updated to 4.17.4 - restart to finish"; the
restart booted the same build and the boot report counted it as a failed
update. A false failure, and users told to restart for nothing.

THE FIX, tested here against real throwaway git repos:
  * /version/pull answers moved=False when HEAD did not move and the process
    already runs what is on disk - no post_update, no restart, and the
    "pressed Update" marker is cleared so the next boot reports nothing;
  * a pull that moved HEAD still says moved=True;
  * the marker carries the build SHA: a different build under the SAME
    VERSION counts as landed, the same build does not;
  * a local HEAD that is not among public main's commits, with the remote's
    own VERSION, is not "30+ behind" (the pop-up said "4.17.4 is out - you're
    on 4.17.4");
  * the page handles moved=False without arming the restart pill.
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

import mlx_ltx_panel as P                                            # noqa: E402

routes_meta = sys.modules["panel.routes_meta"]
GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t")


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), env=GIT_ENV, check=True,
                          capture_output=True, text=True, errors="replace").stdout.strip()


class _H:
    def __init__(self):
        self.status, self.payload = None, None

    def _json(self, payload, status=200):
        self.payload, self.status = payload, status


@unittest.skipUnless(shutil.which("git"), "git not on PATH")
class NothingNewIsNotAnUpdate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.up, self.clone = base / "up", base / "clone"
        self.up.mkdir()
        git(self.up, "init", "-q", "-b", "main", ".")
        (self.up / "VERSION").write_text("4.17.4\n")
        guard = ROOT / "scripts" / "pinokio" / "update_obstruction_guard.sh"
        (self.up / "scripts" / "pinokio").mkdir(parents=True)
        shutil.copy(guard, self.up / "scripts" / "pinokio" / guard.name)
        git(self.up, "add", ".")
        git(self.up, "commit", "-qm", "v1")
        git(base, "clone", "-q", str(self.up), str(self.clone))
        self.settings = {"analytics_update_pressed": "4.17.4@" + "a" * 40}
        self.post_update = []
        real_capture = P._git_capture
        self._patches = [
            mock.patch.object(P, "ROOT", self.clone),
            mock.patch.object(P, "_git_capture",
                              lambda args, cwd=None: real_capture(args, cwd=self.clone)),
            mock.patch.object(P, "_detect_local_install_state", lambda: None),
            mock.patch.object(P, "_check_remote_once", lambda: None),
            mock.patch.object(P, "get_version_state", lambda: {"stale_process": False}),
            mock.patch.object(P, "post_update_start",
                              lambda trigger: self.post_update.append(trigger)),
            mock.patch.object(P, "_settings_set_internal",
                              lambda **kw: self.settings.update(kw)),
            mock.patch.dict(P._VERSION_STATE, {}, clear=False),
            mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": "/dev/null",
                                         "GIT_CONFIG_SYSTEM": "/dev/null"}),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        self.tmp.cleanup()

    def _pull(self) -> _H:
        h = _H()
        routes_meta.post_version_pull(h, "/version/pull", {}, "")
        return h

    def test_an_up_to_date_pull_asks_for_no_restart(self):
        h = self._pull()
        self.assertTrue(h.payload["ok"], h.payload)
        self.assertIs(h.payload.get("moved"), False, h.payload)
        self.assertEqual(self.post_update, [])
        self.assertEqual(self.settings["analytics_update_pressed"], "",
                         "a no-op pull must not leave a marker the next boot "
                         "reports as a failed update")
        self.assertEqual(P._VERSION_STATE.get("pull_state"), "current")

    def test_a_pull_that_moved_head_still_finishes_the_update(self):
        (self.up / "VERSION").write_text("4.17.5\n")
        git(self.up, "commit", "-qam", "v2")
        h = self._pull()
        self.assertTrue(h.payload["ok"], h.payload)
        self.assertIs(h.payload.get("moved"), True, h.payload)
        self.assertEqual(self.post_update, ["update_now"])
        self.assertNotEqual(self.settings["analytics_update_pressed"], "")

    def test_a_stale_process_keeps_its_restart(self):
        """Disk already moved under this process: nothing to pull, but the
        restart IS needed, and the marker must survive to measure it."""
        with mock.patch.object(P, "get_version_state",
                               lambda: {"stale_process": True}):
            h = self._pull()
        self.assertTrue(h.payload["ok"], h.payload)
        self.assertNotEqual(self.settings["analytics_update_pressed"], "")


class FinishingTheUpdateHoldsTheGpuGate(unittest.TestCase):
    """Codex 4.17.5 (on bc09bf3): post_update.sh ran while Prompt Enhance or an
    inline image render held _GPU_LOCK - Enhance had its helper killed under
    it, an image render ran against packages being replaced."""

    def test_the_run_waits_for_the_gate_and_holds_it(self):
        import threading
        ran, held = [], []

        def fake_run():
            ran.append(1)
            held.append(not P._GPU_LOCK.acquire(blocking=False))
        P._GPU_LOCK.acquire()               # an Enhance is in flight
        try:
            with mock.patch.object(P, "_post_update_run", fake_run):
                t = threading.Thread(target=P._post_update_thread, daemon=True)
                t.start()
                t.join(0.5)
                self.assertEqual(ran, [], "the script started under an Enhance")
                P._GPU_LOCK.release()
                t.join(5)
        finally:
            if P._GPU_LOCK.locked() and not ran:
                P._GPU_LOCK.release()
        self.assertEqual(ran, [1])
        self.assertEqual(held, [True], "the gate was not held during the run")
        self.assertFalse(P._GPU_LOCK.locked(), "the gate must be released after")


class TheMarkerKnowsTheBuild(unittest.TestCase):
    def _stamp(self, version, sha):
        return {"sha": sha, "short": sha[:7] if sha else None, "version": version,
                "branch": "main", "commit_date": None}

    def test_same_version_same_build_did_not_land(self):
        with mock.patch.object(P, "boot_build_stamp", lambda: self._stamp("4.17.4", "b" * 40)):
            self.assertEqual(P.update_pressed_landed("4.17.4@" + "b" * 40), (False, "4.17.4"))

    def test_same_version_new_build_landed(self):
        with mock.patch.object(P, "boot_build_stamp", lambda: self._stamp("4.17.4", "c" * 40)):
            self.assertEqual(P.update_pressed_landed("4.17.4@" + "b" * 40), (True, "4.17.4"))

    def test_new_version_landed_and_old_markers_still_read(self):
        with mock.patch.object(P, "boot_build_stamp", lambda: self._stamp("4.17.5", "c" * 40)):
            self.assertEqual(P.update_pressed_landed("4.17.4"), (True, "4.17.4"))
            self.assertEqual(P.update_pressed_landed("4.17.4@" + "b" * 40), (True, "4.17.4"))
        with mock.patch.object(P, "boot_build_stamp", lambda: self._stamp("4.17.4", "c" * 40)):
            self.assertEqual(P.update_pressed_landed("4.17.4"), (False, "4.17.4"))

    def test_the_route_writes_version_at_sha(self):
        with mock.patch.object(P, "boot_build_stamp", lambda: self._stamp("4.17.4", "d" * 40)):
            self.assertEqual(P.update_pressed_marker(), "4.17.4@" + "d" * 40)
        src = (ROOT / "panel" / "routes_meta.py").read_text()
        self.assertIn("analytics_update_pressed=P.update_pressed_marker()", src)


class AnUnlistedHeadOnTheLatestReleaseIsNotBehind(unittest.TestCase):
    def _check(self, local_sha, local_version, remote_version):
        commits = [{"sha": "r" * 40, "commit": {"message": "release(v4.17.4)",
                                               "author": {"name": "x", "date": ""}}}]
        state = dict(P._VERSION_STATE)
        with mock.patch.dict(P._VERSION_STATE, state, clear=True), \
                mock.patch.object(P, "_detect_local_install_state",
                                  lambda: P._VERSION_STATE.update(
                                      local_sha=local_sha, local_version=local_version,
                                      suppress_reason=None)), \
                mock.patch.object(P, "_fetch_remote_commits", lambda limit=30: commits), \
                mock.patch.object(P, "_fetch_raw_text", lambda url, timeout=10: remote_version), \
                mock.patch.object(P, "_fetch_broadcast", lambda: None):
            P._check_remote_once()
            return dict(P._VERSION_STATE)

    def test_same_release_unknown_sha_is_current(self):
        st = self._check("m" * 40, "4.17.4", "4.17.4")
        self.assertEqual(st["behind_by"], 0)
        self.assertFalse(st["behind_more_than"])
        self.assertEqual(st["commits_ahead"], [])

    def test_an_older_release_is_still_behind(self):
        st = self._check("o" * 40, "4.17.3", "4.17.4")
        self.assertGreater(st["behind_by"], 0)

    def test_the_tip_itself_is_current(self):
        st = self._check("r" * 40, "4.17.4", "4.17.4")
        self.assertEqual(st["behind_by"], 0)


class ThePageDoesNotArmARestartForNothing(unittest.TestCase):
    def test_moved_false_returns_before_the_restart_pill(self):
        js = (ROOT / "webapp" / "js" / "health.js").read_text()
        body = js[js.index("async function versionDoPull("):]
        body = body[:body.index("\n}\n")]
        i_moved = body.index("data.moved === false")
        i_arm = body.index("_versionRestartPending = true")
        self.assertLess(i_moved, i_arm)
        branch = body[i_moved:i_arm]
        self.assertIn("return;", branch)
        self.assertIn("Already up to date", branch)


if __name__ == "__main__":
    unittest.main(verbosity=2)
