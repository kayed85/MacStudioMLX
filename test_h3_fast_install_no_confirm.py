#!/usr/bin/env python3
"""H3-27: the Fast install must not use a native confirm(), and must
mention Turbo when it's already installed and Fast isn't.

h3SpeedClick('fast') used `confirm('Install Fast (3 steps)?\\n\\n...')` — a
blocking browser dialog, not the panel's own UI (the same rule Stop's own
confirmation is held to). It also never mentioned Turbo, so a Mac with
Turbo already installed and Fast still missing was told nothing about the
quicker option sitting one click away on the exact same switch.

Fix: the panel's existing "toast + action link" confirm pattern (already
used for the engine-offer nudge) replaces confirm() — one informative
toast naming the adapter, source and license, an explicit "Install Fast"
link to proceed, nothing blocking. When h3TurboState().available is true,
the toast adds a one-line Turbo mention.

Verified live (booted the real "Fast missing, Turbo installed" scenario,
Playwright, dialog listener attached): clicking the Fast pill fired NO
native dialog, produced a toast containing "Turbo (already installed) is
a quicker option too", and clicking its "Install Fast" action link POSTed
to /h3/tristep/install and showed the real installing-toast.
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENGINES_JS = (ROOT / "webapp" / "js" / "engines.js").read_text(encoding="utf-8")


def _function_body(src: str, signature: str) -> str:
    start = src.index(signature)
    depth = 0
    i = src.index("{", start)
    j = i
    while True:
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
        j += 1


class FastInstallHasNoNativeDialog(unittest.TestCase):
    def setUp(self):
        self.body = _function_body(ENGINES_JS, "async function h3SpeedClick(which) {")

    def test_no_confirm_call(self):
        # Check the executable statement form, not the explanatory comment
        # above it (which legitimately names confirm() as what used to run).
        self.assertNotIn("if (!confirm(", self.body,
                          "h3SpeedClick still blocks the tab with a native confirm()")
        self.assertNotIn(")) return;\n  try {\n    const r = await fetch('/h3/tristep/install'",
                          self.body)

    def test_uses_toast_with_action_link(self):
        self.assertIn("phosToast(", self.body)
        self.assertIn("phos-toast-action", self.body)
        self.assertIn("'Install Fast'", self.body)

    def test_mentions_turbo_when_available(self):
        self.assertIn("h3TurboState()", self.body)
        self.assertIn("turbo && turbo.available", self.body)
        self.assertIn("quicker option too", self.body)

    def test_the_actual_install_post_still_happens_on_confirm(self):
        self.assertIn("/h3/tristep/install", self.body)


if __name__ == "__main__":
    unittest.main()
