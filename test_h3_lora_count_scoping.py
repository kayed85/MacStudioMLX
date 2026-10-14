#!/usr/bin/env python3
"""H3-09: the LoRA picker's header must count THIS engine's library, not
both libraries combined, and must point at the right folder.

`renderLorasList()` computed its header summary from `allRows.length` —
every LoRA in the shared fetch, LTX and H3 together — so an install with 4
H3-compatible adapters and 16 LTX ones read "20 installed" under the H3
picker. The mode-filtered `rows` array (built earlier in the same function,
purely to decide what to SHOW) already had the right count; the summary
just never used it. Same story for the Rescan button's tooltip, which named
`mlx_models/loras/` — the LTX folder — unconditionally, including while H3's
picker (a different directory, `_lorasDirs.h3`) was open.

Verified live: booted panel with 5 H3 LoRAs + 16 LTX LoRAs installed.
Switching to H3 read "5 H3 LoRAs · 0 active" and the Rescan tooltip named
the H3 loras dir; switching to LTX read "16 installed · 0 active" and the
Rescan tooltip named mlx_models/loras. This file pins the source.
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LORAS_JS = (ROOT / "webapp" / "js" / "loras.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")


class LoraCountsAreScopedToTheActiveEngine(unittest.TestCase):
    def test_summary_no_longer_reads_allRows_length(self):
        # The exact defect: total counted from the UNFILTERED library.
        self.assertNotIn("const total = allRows.length;", LORAS_JS)

    def test_summary_uses_mode_filtered_counts(self):
        self.assertIn("const modeFilteredCount = rows.length;", LORAS_JS)
        self.assertIn("modeFilteredCount", LORAS_JS)
        self.assertIn("modeFilteredActive", LORAS_JS)

    def test_h3_gets_its_own_unit_label(self):
        self.assertIn("(modeTag === 'video:h3') ? 'H3 LoRAs' : 'installed'", LORAS_JS)

    def test_hidden_from_other_modes_names_the_engine_on_h3(self):
        self.assertIn("won't load on H3", LORAS_JS)

    def test_rescan_tooltip_is_dynamic_and_engine_scoped(self):
        self.assertIn("lorasRescanBtn", LORAS_JS)
        self.assertIn("_lorasDirs.h3", LORAS_JS.split("rescanBtn.title")[0][-400:])

    def test_rescan_button_has_an_id_for_js_to_target(self):
        self.assertIn('id="lorasRescanBtn"', INDEX_HTML)


if __name__ == "__main__":
    unittest.main()
