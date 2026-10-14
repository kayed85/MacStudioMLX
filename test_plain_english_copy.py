#!/usr/bin/env python3
"""VC-23 [P2] [COPY] Engineering jargon and idioms in the main form.

Coordinator ruling, 2026-09-29: "rewrite the jargon in plain words
everywhere touched" — scoped to the copy this session's other fixes
already put hands on (VC-16's Enhance flow, VC-28's LTX length blurbs and
tier-chip ETA tail, VC-34's Tier dialog), rather than the full report's
broader sweep across every idiom in the app (FFLF/Speed/Upscale chip
relabelling, the OOM card, friendlyJobError) — those live in areas this
session did not touch and weren't re-opened for a copy-only pass.

Gated here:
  1  "Loading Gemma..." -> "Loading the prompt helper..." — the model
     name means nothing to a user; what it's FOR does. Fixed in both
     enhancePrompt() (queue.js, VC-16) and Character mode's own enhance
     button + status line (characters.js) for consistency — three near-
     identical strings, not left half-fixed.
  2  the LTX length blurbs (VC-28's own dict) dropped screenwriting
     jargon ("beat") for a plain sentence naming the duration
  3  the tier-chip ETA's "Q8 HQ" tail (VC-28's own ltxCellEta) -> "High
     detail" — Q8/HQ are internal pipeline names
  4  the Extend duration hint dropped its inline frame-math parenthetical
     ("6 latent frames x 8 video frames at 24 fps") for "Adds 2.0 s",
     with the math kept as a tooltip for anyone who wants it — both the
     JS-computed live text and the HTML's static default
  5  the Tier dialog's env-var testing instruction
     (LTX_TIER_OVERRIDE=...) is gone from the user-facing dialog — it
     already lives in CLAUDE.md for developers, and had nothing to do
     with what the dialog itself shows
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402

QJS = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
CJS = (ROOT / "webapp" / "js" / "characters.js").read_text(encoding="utf-8")
EJS = (ROOT / "webapp" / "js" / "engines.js").read_text(encoding="utf-8")
HTML = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")


class TestLoadingCopy(unittest.TestCase):
    def test_no_bare_gemma_name_in_a_loading_message(self):
        for src, label in ((QJS, "queue.js"), (CJS, "characters.js")):
            self.assertNotIn("Loading Gemma", src, label)

    def test_replacement_says_what_it_does(self):
        self.assertIn("Loading the prompt helper", QJS)
        self.assertEqual(CJS.count("Loading the prompt helper"), 2)


class TestLtxLengthBlurbs(unittest.TestCase):
    def test_no_screenwriting_jargon_remains(self):
        for key, cell in P.LTX_LENGTHS.items():
            self.assertNotIn("beat", cell["blurb"].lower(), key)

    def test_each_blurb_names_its_duration_in_plain_words(self):
        self.assertIn("3-second", P.LTX_LENGTHS["3s"]["blurb"])
        self.assertIn("5-second", P.LTX_LENGTHS["5s"]["blurb"])
        self.assertIn("7 seconds", P.LTX_LENGTHS["7s"]["blurb"])
        self.assertIn("10 seconds", P.LTX_LENGTHS["10s"]["blurb"])


class TestTierChipEtaTail(unittest.TestCase):
    def test_q8_hq_jargon_replaced(self):
        self.assertNotIn("' · Q8 HQ'", EJS)
        self.assertIn("' · High detail'", EJS)


class TestExtendDurationHint(unittest.TestCase):
    def test_js_drops_the_frame_math_from_the_visible_text(self):
        i = QJS.index("hint.textContent = `Adds")
        line = QJS[i:QJS.index("\n", i)]
        self.assertNotIn("latent frames", line)
        self.assertIn("${actualSec.toFixed(2)} s", line)

    def test_frame_math_survives_as_a_tooltip(self):
        i = QJS.index("hint.textContent = `Adds")
        block = QJS[i:i + 300]
        self.assertIn("hint.title = `${latents} latent frames", block)

    def test_html_default_matches(self):
        i = HTML.index('id="extendDurationHint"')
        tag = HTML[i:HTML.index(">", i) + 1]
        self.assertIn("latent frames", tag)          # in the title attr
        after = HTML[HTML.index(">", i) + 1:]
        visible = after[:after.index("</div>")]
        self.assertEqual(visible.strip(), "Adds 2.0 s")
        self.assertNotIn("latent frames", visible)


class TestTierModalFooter(unittest.TestCase):
    def test_env_var_testing_instruction_removed_from_the_dialog(self):
        i = HTML.index('id="tierModal"')
        j = HTML.index('id="h3InstallTitle"')   # next unrelated dialog
        block = HTML[i:j]
        # It's fine for a comment to MENTION the old instruction while
        # explaining why it's gone; it must not still be live markup a
        # user sees — i.e. no <code> tag carrying it any more.
        self.assertNotIn("<code>LTX_TIER_OVERRIDE", block)


if __name__ == "__main__":
    unittest.main()
