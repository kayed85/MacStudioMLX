"""4.17.3 fleet fix: a helper that dies is named by its real exit signal.

THE BUG. "helper pipe closed without an event; returncode=None" — 23 failed
renders over 30 days on 15 installs, three on 4.17.2 right after a GPU
watchdog crash. A process's stdout reaches EOF as the kernel tears it down,
a moment BEFORE its exit status can be reaped. WarmHelper read EOF and then
sampled proc.poll() once; that lost the race and returned None, so the user
got a message that named nothing (and the Now card no remedy) for what was
really a SIGABRT (Metal watchdog) or SIGKILL (out of memory).

THE TEST uses a REAL child process that closes its stdout first and aborts a
moment later — exactly the order the kernel produces — and asserts that the
error the panel raises names SIGABRT.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")
os.environ.setdefault("PHOSPHENE_DISABLE_VERSION_CHECK", "1")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402

CHILD = (
    "import os, signal, time\n"
    "os.close(1)\n"            # stdout EOF first ...
    "time.sleep(0.6)\n"        # ... the exit status arrives a moment later
    "os.kill(os.getpid(), signal.SIGABRT)\n"
)


def _helper_with(proc):
    h = P.WarmHelper.__new__(P.WarmHelper)
    h.proc = proc
    h._metal_timeout_seen = False
    h.gemma_max_length = None
    return h


class HelperExitIsNamed(unittest.TestCase):
    def test_eof_before_exit_status_names_the_signal(self):
        proc = subprocess.Popen([sys.executable, "-c", CHILD],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            self.assertEqual(proc.stdout.read(), b"")   # EOF, child still alive
            h = _helper_with(proc)
            with self.assertRaises(RuntimeError) as cm:
                h._dispatch_run_event(None)
            msg = str(cm.exception)
            self.assertIn("SIGABRT", msg)
            self.assertNotIn("returncode=None", msg)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()

    def test_a_child_that_never_exits_still_reports(self):
        # Bounded: a helper that closed its pipe and hangs must not hang the
        # worker. The wait is capped and the old wording is the fallback.
        proc = subprocess.Popen(
            [sys.executable, "-c", "import os, time\nos.close(1)\ntime.sleep(30)\n"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            proc.stdout.read()
            h = _helper_with(proc)
            h.REAP_WAIT_SEC = 0.3
            with self.assertRaises(RuntimeError) as cm:
                h._dispatch_run_event(None)
            self.assertIn("pipe closed without an event", str(cm.exception))
        finally:
            proc.kill()
            proc.wait()


if __name__ == "__main__":
    unittest.main()
