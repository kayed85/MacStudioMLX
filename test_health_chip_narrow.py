"""VC-32: the header health pill names what's wrong, and never clips mid-
character with no sign anything was cut.

WHAT THIS GUARDS.
1. Below 1400px, `updateHealthChip()` set `face.dataset.short = 'attention'`
   whenever memory/models/helper had a warn/danger state - the CSS swap
   (panel.css, `@media (max-width: 1400px)`) then showed that bare word with
   ZERO indication of what needed attention, on a chip whose whole job is a
   one-glance read.
2. At widths just above that breakpoint (1440x900 - a real laptop size), the
   FULL text ("43/64 GB · 68%") still got clipped mid-number to "43/64 G" -
   not by the JS short-text swap (which doesn't apply there), but by
   #healthCluster's own `overflow: hidden` (it has `min-width: 0;
   flex-shrink: 1` so the header wraps other content instead of it). Flex
   items default to `min-width: auto`, which refuses to shrink below their
   own text's natural width, so `.hc-face` never got the chance to add an
   ellipsis - the ancestor's raw pixel clip was the only thing that ever
   fired, silently.

Fixed: (1) the short-text fallback now names the row ("models"/"helper"/
"memory") instead of the bare word. (2) `.hc-face` gets `min-width: 0;
overflow: hidden; text-overflow: ellipsis` (plus `#healthChip { min-width:
0 }` so the flex chain can actually shrink down to it) — any clip is now a
visible "…", at any width, not just below the JS breakpoint.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
from extract_panel_js import extract_function  # noqa: E402

CSS = (ROOT / "webapp" / "style" / "panel.css").read_text(encoding="utf-8")
HEALTH_JS = (ROOT / "webapp" / "js" / "health.js").read_text(encoding="utf-8")


class ShortTextNamesTheProblem(unittest.TestCase):
    def _fn(self) -> str:
        return extract_function("updateHealthChip", HEALTH_JS)

    def test_no_longer_hardcodes_the_bare_word(self):
        fn = self._fn()
        # The literal assignment this used to be - must be gone. The word
        # "attention" is still allowed to exist as a LAST-RESORT fallback
        # (see next test), just never as the unconditional value.
        self.assertNotIn("face.dataset.short = 'attention';", fn)

    def test_three_real_row_names_are_reachable(self):
        fn = self._fn()
        for label in ("'models'", "'helper'", "'memory'"):
            self.assertIn(label, fn, f"{label} not used to name the short text")

    def test_still_has_a_never_empty_fallback(self):
        """Belt and braces: if none of the three pills is even findable
        (shouldn't happen), the chip must still show SOMETHING, not an
        empty face."""
        fn = self._fn()
        self.assertIn(": 'attention'", fn)


class NoSilentMidCharacterClip(unittest.TestCase):
    def test_hc_face_can_shrink_and_ellipsize(self):
        m = re.search(r"#healthChip \.hc-face\s*\{([^}]*)\}", CSS)
        self.assertIsNotNone(m, "no base #healthChip .hc-face rule")
        body = m.group(1)
        self.assertIn("text-overflow: ellipsis", body)
        self.assertIn("overflow: hidden", body)
        self.assertIn("min-width: 0", body)

    def test_health_chip_itself_allows_shrinking(self):
        self.assertIn("#healthChip { min-width: 0; }", CSS)

    def test_narrow_breakpoint_rule_is_unchanged(self):
        """The below-1400px data-short swap is a separate, already-correct
        mechanism - this fix must not remove it, only stop relying on it
        being the ONLY thing standing between the user and a raw clip."""
        self.assertIn("@media (max-width: 1400px)", CSS)
        self.assertIn("content: attr(data-short)", CSS)


if __name__ == "__main__":
    unittest.main()
