#!/usr/bin/env python3
"""H3-14: tier descriptions must be visible (not hover-only) and must not
be stale or contradictory.

Three copy defects, all in the blurbs `_h3_qualities()`/`_h3_lengths()`
build (mlx_ltx_panel.py), shown only via the chip's `title=` tooltip
before this fix:

  - Native said "Worth it with Turbo on; a long wait without" — Turbo was
    folded into the Fast/Best Speed switch, and Native has no Fast pass at
    all (tristep_min is null there, H3-13), so it always renders on Best.
    The sentence described a choice that no longer exists.
  - Standard said "every chained-window measurement on this Mac was taken
    here" — a developer's dev-machine note, not something that tells a
    user what THEY get.
  - The chained "10s" and the single-pass "10s single pass" both framed
    themselves as "the safe one" (no ghosting / no drift), which read as
    each contradicting the other rather than stating its own trade-off.

Fix: reworded all four blurbs in plain user terms, and added a NEW
visible line (#h3TierBlurb, cell.blurb — the exact string the tooltip
already carried) so a trackpad-averse or touch user sees it at all.

Verified live: switching Native -> the visible line reads "...Always
renders on Best — no Fast pass here..."; switching between the two 10s
tiers shows each one's own honest trade-off instead of a "safe one"
claim; the element's `hidden` flag clears whenever a cell resolves.
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PANEL_PY = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
ENGINES_JS = (ROOT / "webapp" / "js" / "engines.js").read_text(encoding="utf-8")


class BlurbsNoLongerStaleOrContradictory(unittest.TestCase):
    def test_native_no_longer_names_turbo(self):
        self.assertNotIn("Worth it with Turbo on", PANEL_PY)
        self.assertIn("Always renders on Best", PANEL_PY)
        self.assertIn("Fast pass here", PANEL_PY)

    def test_standard_no_longer_reads_as_a_dev_machine_note(self):
        self.assertNotIn("every chained-window measurement on this Mac was taken", PANEL_PY)
        self.assertIn("the best-tested shape for", PANEL_PY)
        self.assertIn("chained 10s/15s clips", PANEL_PY)

    def test_the_two_10s_tiers_dont_both_claim_to_be_the_safe_one(self):
        self.assertIn("The default 10s — two chained 5 s windows", PANEL_PY)
        self.assertIn("One continuous shot, no join", PANEL_PY)
        self.assertIn("costs 2-4x the chained", PANEL_PY)
        self.assertNotIn("no duplicated-subject ghosting", PANEL_PY)
        self.assertNotIn("no seam, no chained repeat, no drift between halves", PANEL_PY)


class BlurbIsNowAVisibleLine(unittest.TestCase):
    def test_markup_has_the_visible_blurb_element(self):
        self.assertIn('id="h3TierBlurb" data-h3-only hidden', INDEX_HTML)

    def test_it_sits_before_the_warning_note_not_after(self):
        blurb_i = INDEX_HTML.index('id="h3TierBlurb"')
        note_i = INDEX_HTML.index('id="h3TierNote"')
        self.assertLess(blurb_i, note_i)

    def test_js_writes_cell_blurb_into_it(self):
        self.assertIn("const blurbEl = document.getElementById('h3TierBlurb');", ENGINES_JS)
        self.assertIn("blurbEl.textContent = b;", ENGINES_JS)


if __name__ == "__main__":
    unittest.main()
