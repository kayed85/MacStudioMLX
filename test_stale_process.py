"""The "Restart to finish update" detector, and the reason it never fired.

v4.0.5 compared disk HEAD against `_VERSION_STATE["local_sha"]`, calling that
"the boot snapshot". It is not one: `_check_remote_once()` re-runs
`_detect_local_install_state()` at the top of every poll — on purpose, so a
tree that was dirty at boot and is clean now stops being suppressed without a
restart. That same call refreshes `local_sha`, so within one poll interval it
equals disk again and `disk_sha != boot_sha` is false forever.

Consequence, reported 2026-08-15: pull an update, get no prompt to restart, run
the old code, conclude the update did nothing. Seven hours on a 4.0.9 UI with
4.3.0 on disk.

The fix is a separate `_BOOT_HEAD_SHA` captured once at process start. These
tests pin the behaviour AND the trap — `test_polled_state_does_not_defeat_it`
is the one that would have caught the original bug.
"""

import unittest
from unittest import mock

import mlx_ltx_panel as panel


class StaleProcessDetection(unittest.TestCase):
    def setUp(self):
        self._saved = panel._BOOT_HEAD_SHA

    def tearDown(self):
        panel._BOOT_HEAD_SHA = self._saved

    def test_capture_is_idempotent(self):
        """A second call must not re-point the snapshot at whatever is on disk now."""
        panel._BOOT_HEAD_SHA = None
        with mock.patch.object(panel, "_git_capture", return_value="aaaa111"):
            panel._capture_boot_head()
        with mock.patch.object(panel, "_git_capture", return_value="bbbb222"):
            panel._capture_boot_head()
        self.assertEqual(panel._BOOT_HEAD_SHA, "aaaa111")

    def test_stale_when_disk_moved_under_the_process(self):
        panel._BOOT_HEAD_SHA = "aaaa111"
        with mock.patch.object(panel, "_git_capture", return_value="bbbb222"):
            snap = panel.get_version_state()
        self.assertTrue(snap["stale_process"])
        self.assertEqual(snap.get("disk_short"), "bbbb222"[:7])

    def test_not_stale_when_disk_matches_boot(self):
        panel._BOOT_HEAD_SHA = "aaaa111"
        with mock.patch.object(panel, "_git_capture", return_value="aaaa111"):
            snap = panel.get_version_state()
        self.assertFalse(snap["stale_process"])

    def test_polled_state_does_not_defeat_it(self):
        """THE regression. A poll refreshing local_sha must not clear staleness.

        This is the original bug expressed as a test: local_sha catches up to
        disk on every remote check, so anything comparing against it reports
        "not stale" no matter how old the running process is.
        """
        panel._BOOT_HEAD_SHA = "aaaa111"
        with panel._VERSION_LOCK:
            panel._VERSION_STATE["local_sha"] = "bbbb222"   # what a poll would write
        with mock.patch.object(panel, "_git_capture", return_value="bbbb222"):
            snap = panel.get_version_state()
        self.assertTrue(snap["stale_process"],
                        "stale_process must key off the boot SHA, not the polled one")

    def test_missing_git_never_claims_staleness(self):
        """No git binary must read as 'fine', not as a permanent restart nag."""
        panel._BOOT_HEAD_SHA = "aaaa111"
        with mock.patch.object(panel, "_git_capture", return_value=None):
            snap = panel.get_version_state()
        self.assertFalse(snap["stale_process"])


if __name__ == "__main__":
    unittest.main()
