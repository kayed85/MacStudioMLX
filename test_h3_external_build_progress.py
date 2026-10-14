#!/usr/bin/env python3
"""H3-19: no real in-panel installer exists for H3's ~75 GB install/build
(unlike the small Fast/Turbo adapter installs, which DO run in-process) —
but the panel can now show that a Pinokio-side build IS running, instead
of a silent handoff.

Investigated "install via Pinokio's API": nothing in this codebase calls
into Pinokio's own orchestration layer (port 42000) programmatically, and
the one engine with a REAL in-panel installer (music, _music_install_env())
does not use any such API either — it spawns the actual install
subprocesses directly from the panel process. Doing the same for H3 means
re-implementing install_h3.js's clone/venv/~75 GB download/Q8 build in
Python, none of which could be validated end-to-end in this session
without real bandwidth and disk risk on a shared machine. Not attempted.

What WAS real and safe to ship: the worker already reads
scripts/pinokio/h3_build_q8.sh's lock file to HOLD the render queue while
that script runs (_external_build_hold, mlx_ltx_panel.py) — it just never
told the UI why, so a user who started the build from the Pinokio sidebar
had no way to know it was working without keeping that tab open.
h3_status() now surfaces it as h3.external_build ({active, what}), and
updateModelsCard() (settings.js) shows it as a precedence-topping inline
state: "Building <what> in Pinokio ... Generate resumes here
automatically when it finishes."

Verified live: wrote a real h3_build.lock file (the same shape
h3_build_q8.sh writes: pid + what + started) with this test process's own
pid so the liveness check passes; /status.h3.external_build reported
{"active": true, "what": "the compact Q8 engine"}; the inline models card
showed "Building the compact Q8 engine in Pinokio" with the "no need to
keep that tab open" copy. Removing the lock file made it report
{"active": false, "what": null} again.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("LTX_STATE_DIR", tempfile.mkdtemp(prefix="phos-h3-extbuild-boot-"))
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8308")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402

SETTINGS_JS = (ROOT / "webapp" / "js" / "settings.js").read_text(encoding="utf-8")


class ExternalBuildIsSurfacedInStatus(unittest.TestCase):
    # STATE_DIR is resolved once at module import (whichever test file the
    # pytest session imports mlx_ltx_panel from first wins the env var) — so
    # every other test in this suite that needs a different one patches the
    # module attribute directly instead (see test_h3_build_hold.py). Same
    # pattern here, plus resetting the 3s /status memo each test.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        self.lock = self.state / "h3_build.lock"
        self.patch = mock.patch.object(P, "STATE_DIR", self.state)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(self.tmp.cleanup)
        P._H3_STATUS_CACHE = (0.0, None)

    def test_inactive_by_default(self):
        self.assertFalse(self.lock.exists())
        st = P.h3_status()
        self.assertEqual(st["external_build"], {"active": False, "what": None})

    def test_active_when_a_live_lock_exists(self):
        self.lock.write_text(json.dumps({
            "pid": os.getpid(), "what": "the compact Q8 engine",
            "started": time.time(),
        }))
        st = P.h3_status()
        self.assertEqual(st["external_build"],
                         {"active": True, "what": "the compact Q8 engine"})

    def test_a_dead_pid_is_not_reported_active(self):
        dead_pid = 999999
        try:
            os.kill(dead_pid, 0)
            self.skipTest(f"pid {dead_pid} unexpectedly alive on this machine")
        except (ProcessLookupError, PermissionError):
            pass
        self.lock.write_text(json.dumps({"pid": dead_pid, "what": "ghost", "started": time.time()}))
        st = P.h3_status()
        self.assertEqual(st["external_build"], {"active": False, "what": None})


class InlineCardShowsTheBuildState(unittest.TestCase):
    def test_branch_exists_and_tops_precedence(self):
        self.assertIn("const extBuild = s.h3 && s.h3.external_build;", SETTINGS_JS)
        self.assertIn("if (extBuild && extBuild.active) {", SETTINGS_JS)
        # Must appear before the active-download branch so a build hold is
        # never masked by an unrelated model download.
        self.assertLess(SETTINGS_JS.index("if (extBuild && extBuild.active) {"),
                        SETTINGS_JS.index("if (dl) {"))

    def test_copy_says_no_need_to_keep_the_tab_open(self):
        self.assertIn("No need to keep that tab open.", SETTINGS_JS)
        self.assertIn("Building ${extBuild.what", SETTINGS_JS)


if __name__ == "__main__":
    unittest.main()
