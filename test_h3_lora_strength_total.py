#!/usr/bin/env python3
"""H3-08: stacked H3 LoRAs need a combined-strength warning, and the closed
Shot-setup summary needs to say a LoRA is even in the recipe.

The "keep total near 1.5" advice existed only inside renderH3LoraSlot()'s
single-adapter conflict row, which is hidden the moment the installed
runner can stack (today's default, up to 4) — so three LoRAs at 1.0 each,
a real total of 3.0, warned about nothing
(shots_h3/h64_lora_three_active.png). Separately, activating a LoRA never
touched the closed "Shot setup" summary, so a user who folded that section
had no way to see a LoRA was even part of the render.

Fixes:
  - renderLorasList() sums abs(strength) across the MODE-FILTERED active
    rows whenever 2+ are active, colours amber above 1.5 / red above 2.5,
    into #lorasStrengthTotal (new element, hidden by default).
  - addLoraToActive()/removeLoraFromActive() now call
    updateCustomizeSummary() so the count reaches the closed section on
    every activation, not only a full form re-render.
  - updateShotSetupSummary() (characters.js) appends "N LoRA(s)" on H3,
    using the same lane lookup _serializeLoras() trusts
    (_knownUserLoras[path].lane), not an unreliable property on the
    active-entry itself.

Verified live: booted panel, activated 3 real H3 LoRAs at strength 1.0
each via the actual toggleLora() click path (no direct summary calls) —
#lorasStrengthTotal read "Combined strength: 3.00 ... keep the total near
1.5 for a clean result." and #shotSetupSummary read "...  3 LoRAs".
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LORAS_JS = (ROOT / "webapp" / "js" / "loras.js").read_text(encoding="utf-8")
CHARACTERS_JS = (ROOT / "webapp" / "js" / "characters.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")


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


class StrengthTotalWarning(unittest.TestCase):
    def test_total_element_exists_in_markup(self):
        self.assertIn('id="lorasStrengthTotal"', INDEX_HTML)

    def test_render_computes_and_thresholds_the_total(self):
        self.assertIn("modeFilteredActiveRows.reduce(", LORAS_JS)
        self.assertIn("total > 2.5 ? 'high' : total > 1.5 ? 'mid' : 'ok'", LORAS_JS)
        self.assertIn("keep the total near 1.5", LORAS_JS)

    def test_only_shown_with_two_or_more_active(self):
        self.assertIn("if (modeFilteredActive >= 2)", LORAS_JS)


class ActivationReachesTheClosedSummary(unittest.TestCase):
    def test_add_calls_update_customize_summary(self):
        body = _function_body(LORAS_JS, "function addLoraToActive(")
        self.assertIn("updateCustomizeSummary()", body)

    def test_remove_calls_update_customize_summary(self):
        body = _function_body(LORAS_JS, "function removeLoraFromActive(")
        self.assertIn("updateCustomizeSummary()", body)

    def test_shot_setup_summary_names_the_lora_count_on_h3(self):
        body = _function_body(CHARACTERS_JS, "function updateShotSetupSummary(")
        self.assertIn("laneOf(a.path) === 'h3'", body)
        self.assertIn("' LoRAs'", body)


if __name__ == "__main__":
    unittest.main()
