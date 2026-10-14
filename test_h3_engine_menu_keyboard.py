#!/usr/bin/env python3
"""H3-17: the engine menu must be reachable and operable from the
keyboard.

#engineMenu is portaled to <body> (it has to be — <header> is
overflow:hidden and would slice it), which is exactly what broke
keyboard access: a portaled element sits at the END of the DOM
regardless of where its trigger lives, so Tab from the trigger button
skipped clear over the menu to whatever else the header holds (the
health chip). Opening the menu never moved focus into it, and there was
no arrow-key navigation between rows at all — Escape was the only
keyboard affordance that existed.

Fix: toggleEngineMenu() now focuses the active engine's row (or the
first row) the moment the menu opens, instead of requiring Tab to reach
a portaled element. A keydown handler scoped to the open menu adds
ArrowUp/ArrowDown (cyclic) and Home/End; Enter/Space already worked
natively since the rows are plain <button> elements. closeEngineMenu()
now returns focus to the trigger by default (opt out via
`returnFocus:false` for the click-outside and resize paths, where
yanking focus back would be surprising).

Verified live: focused the trigger, pressed Enter -> menu opened with
document.activeElement on the active engine's row (eng-opt.active,
data-engine="ltx"); ArrowDown moved focus to the next row
(data-engine="h3"); ArrowUp moved it back; Escape closed the menu and
returned focus to the trigger button.
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


class EngineMenuIsKeyboardReachable(unittest.TestCase):
    def test_opening_focuses_the_active_row(self):
        body = _function_body(ENGINES_JS, "function toggleEngineMenu() {")
        self.assertIn("opts.find(o => o.classList.contains('active')) || opts[0]", body)
        self.assertIn("target.focus();", body)

    def test_closing_returns_focus_to_the_trigger_by_default(self):
        body = _function_body(ENGINES_JS, "function closeEngineMenu(opts) {")
        self.assertIn("if (!opts || opts.returnFocus !== false) t.focus();", body)

    def test_click_outside_and_resize_dont_yank_focus_back(self):
        self.assertIn("closeEngineMenu({ returnFocus: false });", ENGINES_JS)
        self.assertIn("() => closeEngineMenu({ returnFocus: false })", ENGINES_JS)


class EngineMenuHasArrowKeyNavigation(unittest.TestCase):
    def test_arrow_and_home_end_keys_are_handled(self):
        self.assertIn("ev.key !== 'ArrowDown' && ev.key !== 'ArrowUp'", ENGINES_JS)
        self.assertIn("ev.key === 'Home'", ENGINES_JS)
        self.assertIn("ev.key === 'End'", ENGINES_JS)

    def test_navigation_is_cyclic(self):
        self.assertIn("(cur + 1) % opts.length", ENGINES_JS)
        self.assertIn("(cur - 1 + opts.length) % opts.length", ENGINES_JS)

    def test_escape_still_closes(self):
        self.assertIn("if (ev.key === 'Escape') { closeEngineMenu(); return; }", ENGINES_JS)


if __name__ == "__main__":
    unittest.main()
