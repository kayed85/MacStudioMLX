"""VC-33: the maintainer's GitHub-stats refresh never writes into a user's
Logs tab.

WHAT THIS GUARDED AGAINST. `_run_stats_fetch_once()` (feeds the /stats
dashboard's historic data — a maintainer-only nicety, nothing a Phosphene
user renders or clicks touches it) reported through `push()`, the SAME
in-memory log `/status.log` and the panel's Logs tab read. A fresh install
with a resolvable GitHub token (a maintainer's own machine, or any dev box
with `gh auth token` set) got exactly one line in an otherwise-empty Logs
tab: "stats: rate-limit remaining: 4848/5000" - cryptic, about a feature the
user has never heard of, and indistinguishable from a real warning.

Fixed by routing every message in `_run_stats_fetch_once()` through
`_stats_debug()`, which writes to stderr (the maintainer's own terminal /
Pinokio process log) instead of the shared `push()` log.
"""
from __future__ import annotations

import io
import sys
import unittest
from unittest import mock

import mlx_ltx_panel as p


class StatsFetchNeverTouchesTheSharedLog(unittest.TestCase):
    def setUp(self):
        # These two module-level warned-once latches persist across tests
        # in a whole-suite run; reset so each case actually exercises the
        # branch it means to.
        p._STATS_WARNED_NO_TOKEN = False
        p._STATS_WARNED_FETCH_FAIL = False
        self.addCleanup(setattr, p, "_STATS_WARNED_NO_TOKEN", False)
        self.addCleanup(setattr, p, "_STATS_WARNED_FETCH_FAIL", False)

    def _log_len(self) -> int:
        with p.LOCK:
            return len(p.STATE["log"])

    def test_no_token_case_does_not_push(self):
        before = self._log_len()
        with mock.patch.object(p, "_resolve_github_token", return_value=""), \
             mock.patch.object(sys, "stderr", io.StringIO()) as err:
            p._run_stats_fetch_once()
        self.assertEqual(self._log_len(), before, "no-token case wrote to the shared log")
        self.assertIn("no GitHub token resolvable", err.getvalue())

    def test_successful_fetch_does_not_push(self):
        before = self._log_len()
        fake = mock.Mock(returncode=0, stdout="repo stats: rate-limit remaining: 4848/5000\n", stderr="")
        with mock.patch.object(p, "_resolve_github_token", return_value="ghp_fake"), \
             mock.patch.object(p, "STATS_FETCHER") as fetcher, \
             mock.patch.object(p.subprocess, "run", return_value=fake), \
             mock.patch.object(sys, "stderr", io.StringIO()) as err:
            fetcher.is_file.return_value = True
            p._run_stats_fetch_once()
        self.assertEqual(self._log_len(), before, "a successful fetch wrote to the shared log")
        self.assertIn("rate-limit remaining", err.getvalue())

    def test_fetch_failure_does_not_push(self):
        before = self._log_len()
        fake = mock.Mock(returncode=1, stdout="", stderr="HTTP 403: rate limited")
        with mock.patch.object(p, "_resolve_github_token", return_value="ghp_fake"), \
             mock.patch.object(p, "STATS_FETCHER") as fetcher, \
             mock.patch.object(p.subprocess, "run", return_value=fake), \
             mock.patch.object(sys, "stderr", io.StringIO()) as err:
            fetcher.is_file.return_value = True
            p._run_stats_fetch_once()
        self.assertEqual(self._log_len(), before, "a failed fetch wrote to the shared log")
        self.assertIn("dashboard refresh skipped", err.getvalue())

    def test_the_function_source_never_calls_push(self):
        """Belt + braces: even a future edit that adds a new branch can't
        reintroduce push() without this failing, independent of which
        branches the tests above happen to exercise."""
        import inspect
        src = inspect.getsource(p._run_stats_fetch_once)
        self.assertNotIn("push(", src)


if __name__ == "__main__":
    unittest.main()
