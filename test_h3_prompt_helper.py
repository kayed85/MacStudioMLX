#!/usr/bin/env python3
"""H3-07: the Video tab needs help with H3's dialogue-tag prompt format.

Before this, index.html:847 showed LTX's audio-cue placeholder on H3 too,
and there was no control anywhere in the Video tab that named the format
docs/H3_ENGINE.md's "Dialogue on H3" describes: spoken words go ONLY
inside `<d>[Language] …</d>`, at most ~8 words per 5 s window, and the
sentence must stop the mouth in the same breath. A line written as prose
quotes (`She says: "..."`) leaves H3's voice switched off with no error —
the exact failure this control exists to prevent.

Adds, gated `data-h3-only` (webapp/index.html):
  - Structure: inserts the three-field skeleton (wraps existing text as
    the first field rather than discarding it).
  - + Line: inserts one dialogue line — the tag, a speaker id, and the
    mouth-stop clause — with the placeholder words selected so typing
    the line is one keystroke away. A language <select> tags it.
  - A live word-budget counter reading H3.speech_words_per_sec — the SAME
    number sent from storyboard.SPEECH_WORDS_PER_SEC server-side
    (mlx_ltx_panel.py), so the two validators can't disagree.
  - A quote-outside-tag warning: any `"`/curly quote whose position falls
    outside every `<d>…</d>` span.
  - An H3-specific placeholder naming the format, shown only on H3.

Verified live (booted panel, Playwright): switching to H3 unhid the
toolbar and swapped the placeholder; Structure wrapped typed text into
the three-field skeleton; + Line inserted a tagged line;
h3SyncPromptHelper() read "10/7 dialogue words" (over-budget, styled) for
a 10-word line at Draft/3s, and correctly warned only on the untagged
prose-quote case, not the properly-tagged one. Switching back to LTX
hid the toolbar and restored the original placeholder.
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX_HTML = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
ENGINES_JS = (ROOT / "webapp" / "js" / "engines.js").read_text(encoding="utf-8")
QUEUE_JS = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
PANEL_CSS = (ROOT / "webapp" / "style" / "panel.css").read_text(encoding="utf-8")
PANEL_PY = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")


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


class MarkupAddsTheHelperGatedToH3(unittest.TestCase):
    def test_toolbar_exists_and_is_h3_only(self):
        self.assertIn('id="h3PromptHelper" data-h3-only hidden', INDEX_HTML)

    def test_structure_and_add_line_buttons_exist(self):
        self.assertIn('onclick="h3InsertPromptStructure()"', INDEX_HTML)
        self.assertIn('onclick="h3InsertDialogueLine()"', INDEX_HTML)

    def test_language_picker_and_indicators_exist(self):
        self.assertIn('id="h3DialogueLang"', INDEX_HTML)
        self.assertIn('id="h3DialogueWordCount"', INDEX_HTML)
        self.assertIn('id="h3QuoteWarning"', INDEX_HTML)

    def test_css_defines_the_toolbar_layout(self):
        self.assertIn(".h3-prompt-helper {", PANEL_CSS)
        self.assertIn(".h3-prompt-helper-words.over-budget", PANEL_CSS)


class VisibilityAndPlaceholderSwapOnEngineChange(unittest.TestCase):
    def setUp(self):
        self.body = _function_body(QUEUE_JS, "function _syncEnginePromptTools() {")

    def test_helper_visibility_keyed_off_engine(self):
        self.assertIn("helper.hidden = (eng !== 'h3');", self.body)

    def test_placeholder_names_the_dialogue_tag_on_h3(self):
        self.assertIn("<d>[English] I thought it was ashwagandha!</d>", self.body)

    def test_ltx_placeholder_is_unchanged_text(self):
        self.assertIn("Audio is generated jointly with video", self.body)


class StructureAndLineInsertion(unittest.TestCase):
    def test_structure_wraps_existing_text_not_discards_it(self):
        body = _function_body(ENGINES_JS, "function h3InsertPromptStructure() {")
        self.assertIn("cur.trim()", body)
        self.assertIn("overall_soundscape:", body)
        self.assertIn("non_diegetic_music:", body)

    def test_line_insertion_includes_tag_speaker_and_mouth_stop(self):
        body = _function_body(ENGINES_JS, "function h3InsertDialogueLine() {")
        self.assertIn("(S1) says: ", body)
        self.assertIn("<d>[${lang}]", body)
        self.assertIn("mouth settles closed", body)

    def test_functions_are_published_for_inline_onclick(self):
        self.assertIn("h3InsertPromptStructure, h3InsertDialogueLine, h3SyncPromptHelper,",
                       ENGINES_JS)


class WordBudgetAndQuoteWarning(unittest.TestCase):
    def setUp(self):
        self.body = _function_body(ENGINES_JS, "function h3SyncPromptHelper() {")

    def test_budget_reads_the_server_speech_rate(self):
        self.assertIn("(H3 && H3.speech_words_per_sec) || 2.4", self.body)

    def test_counts_words_inside_every_dialogue_tag(self):
        self.assertIn("tagRe", self.body)
        self.assertIn("words +=", self.body)

    def test_flags_a_quote_outside_every_tag_span(self):
        self.assertIn("tagSpans", self.body)
        self.assertIn("hasStrayQuote", self.body)

    def test_wired_to_live_input(self):
        self.assertIn("h3SyncPromptHelper()", QUEUE_JS)  # called from engine sync too
        self.assertIn(".addEventListener('input'", ENGINES_JS)


class ServerSharesTheSpeechRate(unittest.TestCase):
    def test_h3_status_carries_speech_words_per_sec(self):
        self.assertIn('"speech_words_per_sec": storyboard.SPEECH_WORDS_PER_SEC,', PANEL_PY)


if __name__ == "__main__":
    unittest.main()
