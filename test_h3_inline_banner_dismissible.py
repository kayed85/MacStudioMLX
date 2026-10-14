#!/usr/bin/env python3
"""H3-20: the 36-59 GB "H3 can run here" inline banner must be dismissible
and short.

updateModelsCard()'s `h3s.needs_q8_dit` branch never added the
'dismissible' class (the only thing that makes panel.css show the ×:
`.models-inline.dismissible .models-inline-dismiss { display: inline-block
}`) and never checked the `dismissed` flag the other branches all respect
— so this banner was permanent on every 36-59 GB Mac, with no way to hide
it, once per session or ever. It also repeated its own heading: the title
said "Hailuo H3 runs on this Mac..." and the body was h3s.ram_note, the
FULL server sentence built for the install-card modal, which starts
"Hailuo H3 runs on this Mac — on its reduced-RAM lane...". Two near-
identical openings wrapped the card to ~270px at 1440x900, pushing
Generate under the Now card.

Fix: respect `dismissed` and add the 'dismissible' class like every other
branch; one short title ("Hailuo H3 can run on this Mac") and a one-line
sub with no repetition — the full explanation still lives in the install
card one click away, which reads h3s.ram_note itself.

Verified live: booted a 48 GB / weights-present / no-Q8-build panel.
Before the fix the card was 275px with a duplicated opening sentence and
an invisible dismiss button; after, it is 107px, the × is visible, and
clicking dismissModelsCard() hides the card. The empty-install (48 GB,
nothing downloaded) sub-case was checked too: correct copy, dismissible.
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SETTINGS_JS = (ROOT / "webapp" / "js" / "settings.js").read_text(encoding="utf-8")


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


class InlineH3BannerIsDismissible(unittest.TestCase):
    def setUp(self):
        self.body = _function_body(SETTINGS_JS, "if (h3s.needs_q8_dit) {")

    def test_checks_dismissed_first(self):
        self.assertIn("if (dismissed) { card.style.display = 'none'; return; }",
                       self.body)

    def test_adds_the_dismissible_class(self):
        self.assertIn("card.classList.add('dismissible')", self.body)

    def test_title_no_longer_repeats_into_the_body(self):
        self.assertIn("'Hailuo H3 can run on this Mac'", self.body)
        # The old body was the raw server ram_note sentence, which itself
        # opens with "Hailuo H3 runs on this Mac" — the exact duplication.
        # Check the executable statement, not the explanatory comment above
        # it (which legitimately names ram_note as what the install card
        # still reads).
        self.assertNotIn("sub.textContent = h3s.ram_note", self.body)

    def test_button_offers_setup_not_a_long_sentence(self):
        self.assertIn("Set up H3", self.body)


if __name__ == "__main__":
    unittest.main()
