#!/usr/bin/env python3
"""H3-33: the "Write a guide" button wraps to three lines, and an H3 LoRA
whose OWN display name mentions LTX reads as an LTX LoRA.

1. `.lora-guide-actions` is a flex row with no `flex-shrink: 0` on its
   button — flex's default `min-width: auto` let the button shrink below
   its own text width whenever the sibling hint span wanted more room,
   wrapping "Write a guide" onto three lines. Pinned the button to its
   natural single-line size; the descriptive span (already the flexible
   one) gives ground instead.

2. A real H3 LoRA can carry "LTX 2.3" in its own display name — the
   string comes from wherever it was downloaded (e.g. a CivitAI listing
   title written before the file was adapted for H3), and the panel has
   no business silently rewriting a user's LoRA name. Added a small,
   honest "H3" lane badge instead, shown only where the name could
   actually mislead: an H3-lane row whose name mentions LTX.

Verified live: booted panel with a real installed LoRA named "H3 - LTX
2.3 - I2V T2V Video Reasoning lora VBVR" (lane h3) — its row now carries
an "H3" badge with an explanatory title; the guide button's computed
style is flex-shrink:0, white-space:nowrap.
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LORAS_JS = (ROOT / "webapp" / "js" / "loras.js").read_text(encoding="utf-8")
PANEL_CSS = (ROOT / "webapp" / "style" / "panel.css").read_text(encoding="utf-8")


class GuideButtonNeverWraps(unittest.TestCase):
    def test_css_pins_the_button_to_its_own_width(self):
        self.assertIn(".lora-guide-actions .ghost-btn { flex-shrink: 0; white-space: nowrap; }",
                       PANEL_CSS)


class H3LoraNamedLikeLtxGetsALaneBadge(unittest.TestCase):
    def test_badge_condition_checks_lane_and_name(self):
        self.assertIn("r.lane === 'h3' && /\\bltx\\b/i.test(r.name || '')", LORAS_JS)

    def test_badge_text_and_explanation(self):
        self.assertIn('>H3</span>', LORAS_JS)
        self.assertIn("its own display name mentions LTX", LORAS_JS)

    def test_does_not_rewrite_the_stored_name(self):
        # The fix must not touch r.name at all — only add a badge.
        body_start = LORAS_JS.index("if (r.lane === 'h3' && /\\bltx\\b/i.test(r.name || '')) {")
        body_end = LORAS_JS.index("}", body_start) + 1
        snippet = LORAS_JS[body_start:body_end]
        self.assertNotIn("r.name =", snippet)


if __name__ == "__main__":
    unittest.main()
