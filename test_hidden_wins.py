"""[hidden] must always win over component CSS (VC-07).

WHAT THIS GUARDS. `panel.css` had no single rule making `[hidden]` outrank a
component's own `display` declaration, so the stylesheet grew two dozen
one-off `<selector>[hidden] { display: none !important }` patches - and two
spots were missed: `.toggle-pill { display: inline-flex }` kept LTX's
"No voice" pill on screen after `queue.js` set `noVoicePill.hidden = true`
(a user ticks it, the render speaks anyway - a wasted render), and
`.loras-summary .loras-browse-btn` kept the H3 LoRA "Import" button visible
in the LTX engine (posts to `/h3/loras/import` from the wrong engine). Both
elements were correctly `.hidden === true` in the DOM the whole time; only
the paint was wrong.

The fix is ONE global rule, `[hidden] { display: none !important; }`, added
once near the top of `panel.css` (right after the `* { box-sizing }` reset,
before any component styles). A `!important` declaration on `[hidden]`
outranks a plain `display` declaration on ANY other selector regardless of
specificity, so this closes the whole class rather than the two known
instances - the next component that forgets to guard its own `[hidden]`
state is covered for free.

This file holds the static, always-runnable half of the gate (the rule
exists, sits early, and the two originally-broken selectors are still on
the books as evidence of what it now overrides). The dynamic half - a real
browser confirming `getComputedStyle` actually reports `none` for every
`[hidden]` element, in both engines - is `scripts/measure_hidden_elements.py`
/ `TestHiddenElementsRenderNone` below; it SKIPS without Chrome instead of
lying green.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CSS = ROOT / "webapp" / "style" / "panel.css"
SCRIPT = ROOT / "scripts" / "measure_hidden_elements.py"
VENV_PY = ROOT / "ltx-2-mlx" / "env" / "bin" / "python3.11"
NO_BROWSER_EXIT = 3

GLOBAL_RULE = re.compile(r"\[hidden\]\s*\{\s*display:\s*none\s*!important\s*;?\s*\}")


def _python() -> str:
    return str(VENV_PY) if VENV_PY.exists() else sys.executable


def _browser_present() -> bool:
    import os
    import shutil

    env = os.environ.get("CHROME_PATH", "").strip()
    if env and Path(env).exists():
        return True
    for c in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Chromium.app/Contents/MacOS/Chromium",
              "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"):
        if Path(c).exists():
            return True
    return any(shutil.which(n) for n in
               ("google-chrome", "chromium", "chromium-browser"))


class GlobalHiddenRule(unittest.TestCase):
    def test_a_bare_hidden_rule_exists(self):
        css = CSS.read_text(encoding="utf-8")
        self.assertRegex(css, GLOBAL_RULE,
                          "no global `[hidden] { display: none !important }` "
                          "rule in panel.css")

    def test_the_rule_lands_before_any_component_style(self):
        """It must sit early so it reads as the house rule, not a patch bolted
        on after the fact - and, structurally, so every component rule below
        it is a rule this one is known to outrank."""
        css = CSS.read_text(encoding="utf-8")
        m = GLOBAL_RULE.search(css)
        self.assertIsNotNone(m)
        # ".toggle-pill" is the first of the two originally-broken component
        # rules (panel.css ~471); the global rule must precede it.
        toggle_pill_idx = css.index(".toggle-pill {")
        self.assertLess(m.start(), toggle_pill_idx,
                         "[hidden] rule must appear before .toggle-pill")

    def test_previously_broken_selectors_are_still_on_the_books(self):
        """Not asserting these were deleted - only that the two elements the
        review caught are still real (a component-CSS conflict for the
        global rule to outrank), so this test can't silently stop meaning
        anything if the markup is refactored without re-checking the gate."""
        html = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="noVoicePill"', html)
        self.assertIn('id="h3LoraImportBtn"', html)
        css = CSS.read_text(encoding="utf-8")
        self.assertIn(".toggle-pill {", css)
        self.assertIn(".loras-browse-btn", css)


class ScriptShape(unittest.TestCase):
    def test_script_parses(self):
        compile(SCRIPT.read_text(encoding="utf-8"), str(SCRIPT), "exec")

    def test_never_binds_the_owners_port(self):
        src = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("FORBIDDEN_PORTS", src)
        for line in src.splitlines():
            if "8199" in line:
                self.assertTrue(re.search(r"FORBIDDEN_PORTS\s*=", line),
                                f"8199 outside the forbidden set: {line}")


class TestHiddenElementsRenderNone(unittest.TestCase):
    """The dynamic gate: boot the real panel, walk every `[hidden]` element
    in a real browser, in both engines, and assert none of them paint."""

    doc: dict | None = None
    skip_why: str | None = None
    fail_why: str | None = None
    returncode: int | None = None

    @classmethod
    def setUpClass(cls):
        if not _browser_present():
            cls.skip_why = "no Chrome/Chromium/Edge - set CHROME_PATH"
            return
        try:
            proc = subprocess.run([_python(), str(SCRIPT)], cwd=str(ROOT),
                                  capture_output=True, text=True,
                                  errors="replace", timeout=180)
        except subprocess.TimeoutExpired:
            cls.fail_why = "the hidden-elements gate did not finish in 180s"
            return
        if proc.returncode == NO_BROWSER_EXIT:
            cls.skip_why = "no browser: " + (proc.stderr or "").strip()
            return
        try:
            cls.doc = json.loads(proc.stdout)
        except json.JSONDecodeError:
            cls.fail_why = (f"no JSON (exit {proc.returncode}): "
                            f"{(proc.stderr or '').strip()[-800:]}")
            return
        cls.returncode = proc.returncode

    def setUp(self):
        if self.skip_why:
            self.skipTest(self.skip_why)
        if self.doc is None:
            self.fail(self.fail_why or "no measurement")

    def test_every_hidden_element_computes_display_none(self):
        self.assertEqual(self.doc.get("failures") or [], [],
                         "an element with `hidden` is still rendering:\n  "
                         + "\n  ".join(self.doc.get("failures") or []))
        self.assertTrue(self.doc.get("ok"))
        self.assertEqual(self.returncode, 0)


if __name__ == "__main__":
    unittest.main()
