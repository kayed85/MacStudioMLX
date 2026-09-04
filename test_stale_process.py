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

2026-08-20 — the same bug had survived twice more, in the two places that
mattered most:

  * `_capture_boot_head()` was called from `version_check_loop` ONLY, a thread
    `__main__` starts `if VERSION_CHECK_ENABLED`. With the check disabled the
    boot SHA stayed None for the life of the process, and `disk != boot` is
    False when boot is None. The detector was dead again, for exactly the
    users who had opted out of update nagging.
  * `stale_process` was a BOOLEAN bolted onto a stamp that still read the
    working tree. `local_short` / `local_version` / `local_branch` /
    `local_commit_date` are refreshed from disk by every poll, so the header
    advertised the new build the moment a pull landed — while the old code
    kept serving — and the restart tooltip printed the disk label on BOTH
    sides of "X is on disk but this process loaded Y".

`BuildStampNamesTheRunningCode` is that half of the story.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mlx_ltx_panel as panel


_BOOT_FIELDS = ("_BOOT_HEAD_SHA", "_BOOT_VERSION", "_BOOT_BRANCH",
                "_BOOT_COMMIT_DATE")


def _save_boot():
    return {f: getattr(panel, f) for f in _BOOT_FIELDS}


def _restore_boot(saved):
    for f, v in saved.items():
        setattr(panel, f, v)


def _set_boot(sha, version=None, branch="dev", commit_date="2026-01-01"):
    panel._BOOT_HEAD_SHA = sha
    panel._BOOT_VERSION = version
    panel._BOOT_BRANCH = branch
    panel._BOOT_COMMIT_DATE = commit_date


class StaleProcessDetection(unittest.TestCase):
    def setUp(self):
        self._saved = _save_boot()

    def tearDown(self):
        _restore_boot(self._saved)

    def test_capture_is_idempotent(self):
        """A second call must not re-point the snapshot at whatever is on disk now."""
        panel._BOOT_HEAD_SHA = None
        with mock.patch.object(panel, "_git_capture", return_value="aaaa111"):
            panel._capture_boot_head()
        with mock.patch.object(panel, "_git_capture", return_value="bbbb222"):
            panel._capture_boot_head()
        self.assertEqual(panel._BOOT_HEAD_SHA, "aaaa111")

    def test_stale_when_disk_moved_under_the_process(self):
        _set_boot("aaaa111")
        with mock.patch.object(panel, "_git_capture", return_value="bbbb222"):
            snap = panel.get_version_state()
        self.assertTrue(snap["stale_process"])
        self.assertEqual(snap.get("disk_short"), "bbbb222"[:7])

    def test_not_stale_when_disk_matches_boot(self):
        _set_boot("aaaa111")
        with mock.patch.object(panel, "_git_capture", return_value="aaaa111"):
            snap = panel.get_version_state()
        self.assertFalse(snap["stale_process"])

    def test_polled_state_does_not_defeat_it(self):
        """THE regression. A poll refreshing local_sha must not clear staleness.

        This is the original bug expressed as a test: local_sha catches up to
        disk on every remote check, so anything comparing against it reports
        "not stale" no matter how old the running process is.
        """
        _set_boot("aaaa111")
        with panel._VERSION_LOCK:
            panel._VERSION_STATE["local_sha"] = "bbbb222"   # what a poll would write
        with mock.patch.object(panel, "_git_capture", return_value="bbbb222"):
            snap = panel.get_version_state()
        self.assertTrue(snap["stale_process"],
                        "stale_process must key off the boot SHA, not the polled one")

    def test_missing_git_never_claims_staleness(self):
        """No git binary must read as 'fine', not as a permanent restart nag."""
        _set_boot("aaaa111")
        with mock.patch.object(panel, "_git_capture", return_value=None):
            snap = panel.get_version_state()
        self.assertFalse(snap["stale_process"])



class BuildStampNamesTheRunningCode(unittest.TestCase):
    """The header stamp is how the owner tells which build he is on. It has to
    name the code that is ANSWERING him, not the code sitting in the working
    tree — those differ for exactly as long as a panel goes un-restarted after
    an update, which is the window the whole trust surface exists to cover."""

    def setUp(self):
        self._saved = _save_boot()

    def tearDown(self):
        _restore_boot(self._saved)

    def test_the_boot_snapshot_exists_without_the_poll_thread_ever_running(self):
        """THE regression, second form.

        Importing the module is the only thing that has happened in this
        process — `version_check_loop` has never been started, which is
        precisely the state of an install running with
        PHOSPHENE_DISABLE_VERSION_CHECK=1. The boot SHA must already be
        captured, because `disk != boot` is False when boot is None and the
        stale detector silently dies.
        """
        if not panel._git_capture(["rev-parse", "HEAD"]):
            self.skipTest("not a git checkout — nothing to snapshot")
        self.assertIsNotNone(
            panel._BOOT_HEAD_SHA,
            "boot SHA must be captured at import, not by the version-check "
            "thread — that thread does not start when the update check is off")
        self.assertIsNotNone(panel._BOOT_BRANCH)
        self.assertIsNotNone(panel._BOOT_COMMIT_DATE)

    def test_the_stamp_is_the_boot_build_not_the_tree(self):
        """THE regression, first form. A poll writes disk into _VERSION_STATE
        every 30 minutes; the stamp must not follow it."""
        _set_boot("0ld0ld0" + "0" * 33, version="4.5.0",
                  branch="dev", commit_date="2026-08-18")
        with panel._VERSION_LOCK:                  # what a poll leaves behind
            panel._VERSION_STATE["local_sha"] = "n3wn3wn" + "0" * 33
            panel._VERSION_STATE["local_short"] = "n3wn3wn"
            panel._VERSION_STATE["local_version"] = "4.6.0"
            panel._VERSION_STATE["local_commit_date"] = "2026-08-20"
        with mock.patch.object(panel, "_git_capture",
                               return_value="n3wn3wn" + "0" * 33):
            with mock.patch.object(panel, "_read_local_version",
                                   return_value="4.6.0"):
                snap = panel.get_version_state()
        self.assertEqual(snap["local_short"], "0ld0ld0",
                         "the stamp followed the working tree instead of the "
                         "code this process loaded")
        self.assertEqual(snap["local_version"], "4.5.0")
        self.assertEqual(snap["local_commit_date"], "2026-08-18")
        self.assertEqual(snap["disk_short"], "n3wn3wn")
        self.assertEqual(snap["disk_version"], "4.6.0")
        self.assertTrue(snap["stale_process"])

    def test_both_builds_are_nameable_when_the_version_label_did_not_move(self):
        """Most fixes land without a VERSION bump, so the two labels read the
        same number. The tooltip that told the owner to restart said
        'Phosphene 4.6.0 is on disk, but this panel process loaded 4.6.0' —
        an alarm that names nothing. The SHAs have to survive to the payload."""
        _set_boot("0ld0ld0" + "0" * 33, version="4.6.0")
        with mock.patch.object(panel, "_git_capture",
                               return_value="n3wn3wn" + "0" * 33):
            with mock.patch.object(panel, "_read_local_version",
                                   return_value="4.6.0"):
                snap = panel.get_version_state()
        self.assertEqual(snap["local_version"], snap["disk_version"])
        self.assertNotEqual(snap["local_short"], snap["disk_short"])

    def test_disk_fields_are_always_present(self):
        """Not 'only when stale'. A consumer that must check stale_process
        before it may trust disk_short is one that will forget to."""
        _set_boot("aaaa111" + "0" * 33, version="4.6.0")
        with mock.patch.object(panel, "_git_capture",
                               return_value="aaaa111" + "0" * 33):
            snap = panel.get_version_state()
        self.assertFalse(snap["stale_process"])
        self.assertEqual(snap["disk_short"], "aaaa111")
        self.assertIn("disk_version", snap)

    def test_a_version_file_edited_under_the_process_does_not_move_the_stamp(self):
        """The VERSION file is read fresh on every poll too."""
        _set_boot("aaaa111" + "0" * 33, version="4.5.0")
        with mock.patch.object(panel, "_git_capture",
                               return_value="aaaa111" + "0" * 33):
            with mock.patch.object(panel, "_read_local_version",
                                   return_value="9.9.9"):
                snap = panel.get_version_state()
        self.assertEqual(snap["local_version"], "4.5.0")
        self.assertEqual(snap["disk_version"], "9.9.9")

    def test_capture_freezes_the_whole_snapshot_together(self):
        """Half a snapshot is worse than none — a fresh branch name beside a
        boot SHA reads as authoritative and is not."""
        saved = _save_boot()
        try:
            panel._BOOT_HEAD_SHA = None
            panel._BOOT_VERSION = None
            panel._BOOT_BRANCH = None
            panel._BOOT_COMMIT_DATE = None
            with mock.patch.object(panel, "_git_capture", return_value="first"):
                with mock.patch.object(panel, "_read_local_version",
                                       return_value="1.0.0"):
                    panel._capture_boot_head()
            with mock.patch.object(panel, "_git_capture", return_value="second"):
                with mock.patch.object(panel, "_read_local_version",
                                       return_value="2.0.0"):
                    panel._capture_boot_head()
            self.assertEqual(panel._BOOT_HEAD_SHA, "first")
            self.assertEqual(panel._BOOT_VERSION, "1.0.0")
            self.assertEqual(panel._BOOT_BRANCH, "first")
        finally:
            _restore_boot(saved)

    def test_a_zip_install_still_freezes_its_version(self):
        """No git, so no SHA — but VERSION ships in the zip and is still a
        property of the code that got loaded."""
        saved = _save_boot()
        try:
            panel._BOOT_HEAD_SHA = None
            panel._BOOT_VERSION = None
            with mock.patch.object(panel, "_git_capture", return_value=None):
                with mock.patch.object(panel, "_read_local_version",
                                       return_value="4.6.0"):
                    panel._capture_boot_head()
            self.assertIsNone(panel._BOOT_HEAD_SHA)
            self.assertEqual(panel._BOOT_VERSION, "4.6.0")
        finally:
            _restore_boot(saved)


class ThePageAnswersWhichBuildItIs(unittest.TestCase):
    """`c5dc04c` is a real commit from 2026-05-21 that shipped inside the
    served page, in a comment, formatted exactly like the header stamp it
    described: "you're on 3.0.0 · dev · c5dc04c (2026-05-21)". The running
    build appeared nowhere in the page at all. So the page answered "which
    build is this?" with a decoy, confidently, in the right format — the
    2026-08-19 report. The page carries the true answer now."""

    def test_the_page_names_the_running_build(self):
        stamp = panel.boot_build_stamp()
        page = panel.page()
        self.assertIn('<meta name="phosphene-build"', page)
        if stamp["short"]:
            self.assertIn(stamp["short"], page,
                          "the running SHA must appear in the served page")
        if stamp["version"]:
            self.assertIn(stamp["version"], panel.build_stamp_text())

    def test_the_version_pill_ships_no_sha_literal(self):
        """The decoy lived in this function's comments. Nothing in the code
        that RENDERS the stamp may carry a SHA of its own."""
        import re
        # renderVersionPill ships to the browser from webapp/js/health.js
        # since the slice-3 extraction (docs/ARCHITECTURE.md) — the module
        # file IS served bytes, so the guard reads it there.
        page = (Path(__file__).resolve().parent
                / "webapp" / "js" / "health.js").read_text(encoding="utf-8")
        start = page.index("function renderVersionPill(")
        end = page.index("\nfunction ", start + 1)
        body = page[start:end]
        found = re.findall(r"(?<![0-9a-zA-Z_#/])[0-9a-f]{7}(?![0-9a-zA-Z_])",
                           body)
        self.assertEqual(found, [],
                         f"SHA-shaped literals in renderVersionPill: {found}. "
                         "A comment there is served to the browser and reads "
                         "as the build stamp to anyone searching the page.")



class ATestPanelCannotImpersonateTheRealOne(unittest.TestCase):
    """A verifying agent runs a second copy against a throwaway state dir so
    it can cut, save and restore without touching the owner's films. An
    abandoned tab of THAT is indistinguishable from his own panel showing a
    stale build — and it cost an evening: he read a test instance as his
    install and reported the app had gone backwards."""

    def test_the_install_serving_its_own_films_is_native(self):
        import mlx_ltx_panel as panel
        with mock.patch.object(panel, "STATE_DIR", panel.ROOT / "state"):
            self.assertTrue(panel._state_dir_is_native())

    def test_a_foreign_state_dir_is_not(self):
        import mlx_ltx_panel as panel
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(panel, "STATE_DIR", Path(tmp)):
                self.assertFalse(panel._state_dir_is_native())

    def test_the_page_shouts_it_in_the_two_places_a_tab_shows(self):
        # The badge for a tab you are looking at, the TITLE for one you are
        # not — a background tab is a title and a favicon and nothing else.
        import mlx_ltx_panel as panel
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(panel, "STATE_DIR", Path(tmp)):
                page = panel.page()
        self.assertIn("<title>MacStudio MLX TEST</title>", page)
        self.assertIn("TEST PANEL", page)
        self.assertIn("test-badge", page)

    def test_the_real_install_carries_neither(self):
        import mlx_ltx_panel as panel
        with mock.patch.object(panel, "STATE_DIR", panel.ROOT / "state"):
            page = panel.page()
        self.assertIn("<title>MacStudio MLX</title>", page)
        self.assertNotIn("TEST PANEL", page)


if __name__ == "__main__":
    unittest.main()
