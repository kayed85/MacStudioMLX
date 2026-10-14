"""VC-34: a second, visible door into the Tier dialog ("What this Mac can
do"), next to the Quality label where the decision actually happens.

WHAT THIS GUARDS. The only way to reach the Tier dialog was Health pill ->
the Tier row inside its popover (#tierPill is not visible until that
popover is opened) — despite the docs already sending people with memory
questions there. Added a plain-text link, "On this Mac: <tier label>",
next to the Quality chip strip's own label, reusing the existing
openTierModal() (the same function #tierPill already calls) and BOOT.tier
(already shipped in the bootstrap payload, fixed at boot since RAM
doesn't change at runtime).
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HTML = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
MAIN_JS = (ROOT / "webapp" / "js" / "main.js").read_text(encoding="utf-8")
CSS = (ROOT / "webapp" / "style" / "panel.css").read_text(encoding="utf-8")


class MarkupAndWiring(unittest.TestCase):
    def test_link_sits_next_to_the_quality_label(self):
        i = HTML.index('id="qualityLabelName"')
        j = HTML.index('id="qualityMeta"')
        k = HTML.index('id="qualityTierLink"')
        self.assertLess(i, k)
        self.assertLess(k, j)

    def test_link_calls_the_same_opener_the_health_pill_uses(self):
        i = HTML.index('id="qualityTierLink"')
        j = HTML.index('</a>', i)
        snippet = HTML[i:j]
        self.assertIn("openTierModal()", snippet)
        # the pre-existing door, still there, still calling the same function
        self.assertIn('onclick="openTierModal()"', HTML)

    def test_label_populated_from_the_boot_payload_not_a_second_fetch(self):
        self.assertIn("qualityTierLinkLabel", MAIN_JS)
        self.assertIn("BOOT.tier", MAIN_JS)
        self.assertNotIn("fetch('/status')", MAIN_JS)

    def test_css_rule_exists(self):
        self.assertIn(".tier-quick-link {", CSS)


class BootPayloadCarriesTheLabel(unittest.TestCase):
    def test_boot_tier_label_field_exists_in_server_source(self):
        src = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
        m = re.search(r'"tier":\s*\{\s*"key":\s*SYSTEM_TIER,\s*"label":\s*SYSTEM_CAPS\["label"\]', src)
        self.assertIsNotNone(m, "BOOT.tier.label wiring not found server-side")


if __name__ == "__main__":
    unittest.main()
