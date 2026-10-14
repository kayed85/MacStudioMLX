"""VC-30: the Video docs describe controls where they actually live.

WHAT THIS GUARDS. webapp/docs/video.md and getting-started.md described a
UI that had moved on:
  - "under Customize -> Export" / "under Customize -> Upscale" - the section
    is called "After the render" (index.html #finishDetails, cz-title "After
    the render"); "Customize" survives only as an internal CSS class name
    (.customize-section) and in code comments, never as a visible label.
  - "Native (default)" for LTX Export - the actual default (set by
    setQuality('balanced') at boot, QUALITY_PRESETS.balanced.upscale) is
    fit_720p, confirmed by the hidden #upscale input's own value="fit_720p".
  - "the action row under the player" - the toolbar is CSS-positioned OVER
    the player (an overlay), not below it.
  - "All / Videos / Photos filter" - missing Audio, a real fourth filter
    (#mainOutputsFilterAudio exists in the DOM).
  - "The switch at the top right of the header" - a specific position claim
    that doesn't hold up across viewports; softened to stop promising a
    corner the switch doesn't reliably occupy.
  - No mention of the Hide icon, which permanently removes a clip from the
    gallery with no UI way back (VC-04's own subject).

This file pins each correction against the real markup, not just against
the new doc text in isolation - so a future rename has to touch both.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "webapp" / "docs"
HTML = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
VIDEO_MD = (DOCS / "video.md").read_text(encoding="utf-8")
GETTING_STARTED_MD = (DOCS / "getting-started.md").read_text(encoding="utf-8")


class SectionNamesMatchRealDisclosures(unittest.TestCase):
    def test_after_the_render_is_a_real_cz_title(self):
        self.assertIn('<span class="cz-title">After the render</span>', HTML)

    def test_advanced_is_a_real_cz_title(self):
        self.assertIn('<span class="cz-title">Advanced</span>', HTML)

    def test_docs_no_longer_claim_a_customize_section(self):
        for md, name in ((VIDEO_MD, "video.md"), (GETTING_STARTED_MD, "getting-started.md")):
            self.assertNotIn("Customize", md, f"{name} still claims a 'Customize' section")

    def test_docs_point_export_and_upscale_at_after_the_render(self):
        self.assertIn("After the render** → **Export**", VIDEO_MD)
        self.assertIn("After the render** → **Upscale**", VIDEO_MD)


class DefaultUpscaleIsFit720p(unittest.TestCase):
    def test_hidden_upscale_input_defaults_to_fit_720p(self):
        m = re.search(r'name="upscale" id="upscale" value="([^"]+)"', HTML)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "fit_720p")

    def test_balanced_preset_upscale_is_fit_720p(self):
        src = (ROOT / "webapp" / "js" / "characters.js").read_text(encoding="utf-8")
        m = re.search(r"balanced:\s*\{[^}]*upscale:\s*'([^']+)'", src)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "fit_720p")

    def test_docs_say_720p_fit_is_the_default_not_native(self):
        self.assertIn("720p fit** (default)", VIDEO_MD)
        self.assertNotIn("Native** (default)", VIDEO_MD)


class ActionRowIsOverNotUnder(unittest.TestCase):
    def test_docs_say_over_the_player(self):
        self.assertIn("over the player", VIDEO_MD)
        self.assertIn("Over the player", GETTING_STARTED_MD)
        self.assertNotIn("under the player", VIDEO_MD.lower())
        self.assertNotIn("under the player", GETTING_STARTED_MD.lower())


class AudioFilterIsDocumented(unittest.TestCase):
    def test_audio_filter_exists_in_markup(self):
        self.assertIn('id="mainOutputsFilterAudio"', HTML)

    def test_docs_mention_it(self):
        self.assertIn("Photos / Audio", GETTING_STARTED_MD)


class HideIconIsDocumented(unittest.TestCase):
    def test_hide_mentioned_in_video_docs(self):
        self.assertIn("Hide", VIDEO_MD)
        self.assertIn("eye icon", VIDEO_MD + GETTING_STARTED_MD)


if __name__ == "__main__":
    unittest.main()
