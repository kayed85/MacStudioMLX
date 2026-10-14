#!/usr/bin/env python3
"""H3-13: the Fast/Best pill must show what will actually render, not just
the stored preference.

Native and the single-pass 10s have no Fast path (`cell.tristep_min == null`)
— they always render Best. Before this fix, `_h3ApplySpeed()` lit the Fast
pill from `pref === 'fast' && tri.available` alone: the adapter being
installed was enough to keep Fast pink even on a cell that cannot run it,
with only a sub-label ("Best only here") saying otherwise. A render could
take 22 min to 3h48m while the pill told the user they had picked the ~4x
faster option.

The fix lights the pill from `fastOn` (`h3TriStepOn(cell)`, which already
folds in per-cell availability) so the highlighted half always matches what
`h3_tristep` is about to post, adds a `cell-only-best` class (same
dashed/dimmed grammar as `needs-download`) so the chip itself looks
unavailable rather than relying on a sub-label, and keeps the "you prefer
Fast but this shape can't" note keyed off `pref` (not `lit`, which can no
longer disagree with the actual behaviour).

WHY THIS IS A STATIC TEST, NOT A DRIVEN ONE. `scripts/webapp_import_shim.mjs`
(the node-side DOM shim other test_h3_* files use) stubs
`document.querySelector`/`querySelectorAll` as permanent no-ops — fine for
code that only touches elements by id, but `_h3ApplySpeed()`'s pill-lighting
loop is a `querySelectorAll('#h3SpeedGroup [data-h3-speed]')`, which the shim
cannot resolve. Extending the shim's selector engine is out of scope for one
finding and risks the other packages editing shared JS in parallel this same
review. So this file pins the fixed LOGIC by reading the real function
bodies (a rename or a reverted condition fails it), and the full behaviour
was verified live against a booted panel with Playwright: switching to the
Native 3s tier with a stored Fast preference greyed the Fast pill
(`cell-only-best`), lit Best, and set `h3_tristep=0`; switching back to a
tristep-capable tier re-lit Fast without touching the stored preference.
"""
from __future__ import annotations

import re
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


class SpeedPillMatchesWhatWillRender(unittest.TestCase):
    def test_lit_half_follows_fastOn_not_bare_preference(self):
        body = _function_body(ENGINES_JS, "function _h3ApplySpeed(")
        self.assertIn("const lit = fastOn ? 'fast' : 'best';", body,
                       "the pill's lit half no longer tracks what will "
                       "actually render (H3-13 regression)")
        # The old, wrong condition must not still be the one deciding `lit`.
        self.assertNotIn("const lit = (pref === 'fast' && tri.available)", body)

    def test_bestOnly_note_keys_off_preference_not_lit(self):
        body = _function_body(ENGINES_JS, "function _h3ApplySpeed(")
        self.assertIn("const bestOnly = pref === 'fast' && cell && "
                       "cell.tristep_min == null;", body)

    def test_fast_pill_greys_out_on_a_best_only_cell(self):
        body = _function_body(ENGINES_JS, "function renderH3Turbo(")
        self.assertIn("cellOnlyBest", body)
        self.assertIn("cell-only-best", body)
        # It must be gated on THIS cell having no fast path, independent of
        # whether the adapter is installed (that's needs-download's job).
        self.assertIn("cell.tristep_min == null", body)

    def test_css_defines_the_greyed_state(self):
        css = (ROOT / "webapp" / "style" / "panel.css").read_text(encoding="utf-8")
        self.assertIn(".pill-btn.cell-only-best", css)


if __name__ == "__main__":
    unittest.main()
