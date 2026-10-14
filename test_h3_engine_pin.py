"""The H3 engine is pinned to a COMMIT, not a branch tip (4.19.0).

Through 4.18.x `scripts/pinokio/h3_checkout.sh` checked out whatever
`codex/h3-engine-v2` pointed at, so pushing the engine repo changed every
installed Phosphene's H3 at its next Update with no release behind it. These
pin the new contract with a real git: a throwaway "engine remote" whose
branch tip has moved PAST the pin, and the real script (pin rewritten to point
at it) must land on the pinned commit, verify it, survive a dirty tree, and
leave a checkout alone when the pin cannot be fetched.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "scripts/pinokio/h3_checkout.sh"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True, errors="replace",
                          env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                               "HOME": str(cwd), "PATH": "/usr/bin:/bin"}).stdout.strip()


class PinShape(unittest.TestCase):
    def test_the_pin_is_a_full_sha_and_a_ref(self):
        text = SCRIPT.read_text()
        sha = re.search(r'^H3_PIN_SHA="([^"]*)"', text, re.M)
        ref = re.search(r'^H3_PIN_REF="([^"]*)"', text, re.M)
        self.assertTrue(sha and re.fullmatch(r"[0-9a-f]{40}", sha.group(1)), "pin must be a 40-hex commit")
        self.assertTrue(ref and ref.group(1) and ref.group(1) != "codex/h3-engine-v2",
                        "the pin ref must not be the branch 4.18.x installs track")
        # The old branch-tip checkout must be gone, not merely unused.
        self.assertNotIn("FETCH_HEAD", text)

    def test_install_and_update_both_call_the_one_pin(self):
        self.assertIn("scripts/pinokio/h3_checkout.sh", (ROOT / "install_h3.js").read_text())
        self.assertIn("scripts/pinokio/h3_checkout.sh", (ROOT / "scripts/post_update.sh").read_text())
        for f in ("install_h3.js", "scripts/post_update.sh"):
            self.assertNotIn("H3_PIN_SHA", (ROOT / f).read_text(), f"{f} must not carry a second pin")


@unittest.skipUnless(shutil.which("git"), "git not on PATH")
class PinBehaviour(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        # An engine "remote": c1 (old tip) -> c2 (the pin) -> c3 (tip moved on).
        src = base / "engine-src"
        src.mkdir()
        _git(src, "init", "-q", "-b", "codex/h3-engine-v2")
        (src / "minimax_h3_mlx").mkdir()
        (src / "scripts").mkdir()
        (src / "scripts/quantize_stream.py").write_text("")
        for n in ("c1", "c2", "c3"):
            (src / "minimax_h3_mlx" / "VERSION").write_text(n)
            _git(src, "add", "-A")
            _git(src, "commit", "-q", "-m", n)
            if n == "c2":
                self.pin = _git(src, "rev-parse", "HEAD")
                _git(src, "branch", "phosphene/test-pin")
        self.remote = base / "engine.git"
        _git(base, "clone", "-q", "--bare", str(src), str(self.remote))
        # The user's checkout: cloned when the tip was c1 (4.18.x behaviour).
        self.checkout = base / "minimax-h3-mlx"
        _git(base, "clone", "-q", str(self.remote), str(self.checkout))
        _git(self.checkout, "reset", "-q", "--hard", "HEAD~2")
        # The real script, pin pointed at this remote's c2.
        text = SCRIPT.read_text()
        text = re.sub(r'^H3_PIN_SHA=".*"$', f'H3_PIN_SHA="{self.pin}"', text, flags=re.M)
        text = re.sub(r'^H3_PIN_REF=".*"$', 'H3_PIN_REF="phosphene/test-pin"', text, flags=re.M)
        self.script = base / "h3_checkout.sh"
        self.script.write_text(text)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(self.script), str(self.checkout)],
                              capture_output=True, text=True, errors="replace",
                              env={"PATH": "/usr/bin:/bin", "HOME": str(self.checkout)})

    def test_lands_on_the_pin_not_the_moved_tip(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(_git(self.checkout, "rev-parse", "HEAD"), self.pin)
        self.assertEqual((self.checkout / "minimax_h3_mlx/VERSION").read_text(), "c2")
        self.assertIn("pinned", r.stdout)

    def test_a_dirty_tree_does_not_block_the_move(self):
        (self.checkout / "minimax_h3_mlx/VERSION").write_text("local edit")
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(_git(self.checkout, "rev-parse", "HEAD"), self.pin)

    def test_the_pin_reaches_a_checkout_whose_ref_is_gone_by_sha(self):
        # Ref renamed upstream, commit still reachable from another branch.
        _git(self.remote, "branch", "-m", "phosphene/test-pin", "renamed")
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(_git(self.checkout, "rev-parse", "HEAD"), self.pin)

    def test_an_unfetchable_pin_leaves_the_checkout_alone(self):
        before = _git(self.checkout, "rev-parse", "HEAD")
        text = self.script.read_text().replace(self.pin, "f" * 40)
        self.script.write_text(text)
        r = self._run()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("left as is", r.stdout)
        self.assertEqual(_git(self.checkout, "rev-parse", "HEAD"), before)


if __name__ == "__main__":
    unittest.main()
