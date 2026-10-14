#!/usr/bin/env python3
"""H3-05 / H3-18 / H3-39: the "compact engine not built" state must say
Build, everywhere, and the install card must not overclaim.

`H3.repairable` is true for TWO different real states — missing_q8_dit
(every weight present, only the local Q8 build is missing: Pinokio's
sidebar shows ONLY a "Build Hailuo H3 compact engine..." entry, no Repair
entry at all) and a genuinely broken venv/runner (Pinokio shows "Repair
Hailuo H3..."). Every UI surface that branched on `repairable` alone said
"Repair" unconditionally: the install-card CTA, its title ("Hailuo H3 · 75
GB" even though nothing downloads — H3-39), the repeat-click toast nudge,
the engine badge ('repair'), and the engine-menu tooltip. A 48 GB Mac in
exactly this state is the rescue scenario recent releases targeted, sent
looking for a sidebar button that does not exist.

Also fixes H3-18's false claim in the real-install blurb ("either engine
can drive any render you start" — H3 is Text/Image only).

Verified live (booted panel, 48 GB / weights-present / no-Q8-build
scenario, Playwright): openH3InstallCard() title reads "Build the compact
engine · ~5 min, no download", body contains "Build Hailuo H3" and not
"Repair Hailuo H3", and the engine-menu badge reads "build" not "repair".
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
QUEUE_JS = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
ENGINES_JS = (ROOT / "webapp" / "js" / "engines.js").read_text(encoding="utf-8")


class BuildVsRepairCopy(unittest.TestCase):
    def test_install_card_branches_on_missing_q8_dit(self):
        self.assertIn("const needsBuildOnly = H3.reason === 'missing_q8_dit';", QUEUE_JS)
        self.assertIn("fixMenuText", QUEUE_JS)
        self.assertIn("Build Hailuo H3 compact engine (weights kept — no re-download)", QUEUE_JS)

    def test_install_card_title_is_dynamic(self):
        self.assertIn("Build the compact engine · ~5 min, no download", QUEUE_JS)
        self.assertNotIn('<h2 id="h3InstallTitle">Hailuo H3 · 75 GB</h2>\n', QUEUE_JS)  # sanity: title lives in html, not js
        html = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<h2 id="h3InstallTitle">Hailuo H3 · 75 GB</h2>', html,
                       "markup default changed — update the JS branches too if intentional")
        self.assertIn("document.getElementById('h3InstallTitle')", QUEUE_JS,
                       "nothing rewrites the title at open time")

    def test_repeat_click_nudge_branches_too(self):
        self.assertIn("build the compact engine, no download", QUEUE_JS)
        self.assertIn("repair, no download", QUEUE_JS)

    def test_no_overclaim_about_which_modes_h3_covers(self):
        self.assertNotIn("either engine can drive any render you start", QUEUE_JS)
        self.assertRegex(QUEUE_JS, r"H3 covers Text and\s+Image")

    def test_engine_badge_says_build_not_repair(self):
        self.assertIn("st.reason === 'missing_q8_dit') ? 'build'", ENGINES_JS)

    def test_engine_menu_tooltip_distinguishes_build_from_repair(self):
        self.assertIn("needs its compact engine built", ENGINES_JS)


if __name__ == "__main__":
    unittest.main()
