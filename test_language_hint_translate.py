#!/usr/bin/env python3
"""VC-13 [P1] [OPPORTUNITY] Non-English creators get no language guidance,
and Enhance silently translates.

Coordinator ruling, 2026-09-29: "a language hint on the prompt box. Enhance
states that it translated, with a 'Translate to English' toggle."

WHAT THIS GUARDS.
  1  prompt_looks_non_latin() (server) / promptLooksNonLatin() (client) are
     the SAME signal (same Unicode ranges): CJK, Hangul, Cyrillic, Arabic,
     Hebrew, Thai trigger it; French/Spanish/German-style Latin-script
     accented text does NOT (character-range guessing between Latin-script
     languages would misfire constantly on ordinary text, so this is
     deliberately narrow — see the shared docstring)
  2  the prompt box's language hint (#langHint) is hidden until
     updateLangHint() sees non-Latin text in the textarea, and carries the
     "Translate to English" toggle (checked by default — unchanged
     behaviour when nobody touches it)
  3  enhancePrompt() sends the toggle's state as `translate` on every
     request (not just when the hint is showing), defaulting true when
     the element is absent
  4  the server: /prompt/enhance reads `translate` (default true, "0"/
     "false"/"off" opt out), computes source_non_latin independently, and
     returns both `source_non_latin` and `translated` so the client never
     re-derives what the server already knows
  5  the helper: translate=False appends a system-prompt addendum telling
     Gemma to keep the ORIGINAL language instead of the base instruction
     to answer in English; translate=True (default) is byte-identical to
     the pre-VC-13 behaviour (no addendum added)
  6  the Enhance panel (VC-16) shows "Translated to English." up front —
     not buried behind Accept — when the server says it translated
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = Path(tempfile.mkdtemp(prefix="phos-langhint-"))
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8329")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mlx_ltx_panel as P  # noqa: E402
from extract_panel_js import extract_function  # noqa: E402

NODE = shutil.which("node")
QJS = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
HTML = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "webapp" / "style" / "panel.css").read_text(encoding="utf-8")
ROUTES_SRC = (ROOT / "panel" / "routes_queue.py").read_text(encoding="utf-8")
HELPER_SRC = (ROOT / "mlx_warm_helper.py").read_text(encoding="utf-8")


def _run_node(script: str) -> dict:
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        r = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise AssertionError("node failed:\n%s\n%s" % (r.stdout[-3000:], r.stderr[-3000:]))
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------- 1
class TestNonLatinDetection(unittest.TestCase):
    NON_LATIN = [
        "夕暮れの港で彼は今日は大漁だと笑う",   # Japanese
        "안녕하세요 오늘 날씨가 좋네요",           # Korean
        "Женщина идёт через лес",              # Russian/Cyrillic
        "مرحبا بك في هذا المكان",                # Arabic
    ]
    LATIN = [
        "A woman walks through a forest",
        "Une femme élégante marche",           # French, accented Latin
        "Una mujer camina por el bosque",       # Spanish
        "Eine Frau geht durch den Wald",        # German
    ]

    def test_server_flags_non_latin_scripts(self):
        for s in self.NON_LATIN:
            self.assertTrue(P.prompt_looks_non_latin(s), s)

    def test_server_does_not_flag_latin_script_languages(self):
        for s in self.LATIN:
            self.assertFalse(P.prompt_looks_non_latin(s), s)

    def test_client_signal_matches_server_ranges_exactly(self):
        fn = extract_function("promptLooksNonLatin", QJS)
        import re
        m = re.search(r"/\[(.+?)\]/\.test", fn)
        self.assertTrue(m)
        client_ranges = m.group(1)
        server_pattern = P._NON_LATIN_SCRIPT_RE.pattern.strip("[]")
        # Same code points, not necessarily byte-identical escaping/order.
        self.assertEqual(
            set(client_ranges.replace("\\u", "").split("-")),
            set(server_pattern.replace("\\u", "").split("-")))


# ---------------------------------------------------------------- 2 & 6
class TestLangHintMarkup(unittest.TestCase):
    def test_hint_hidden_by_default_with_a_translate_toggle(self):
        i = HTML.index('id="langHint"')
        blk = HTML[i - 30:i + 500]
        self.assertIn("hidden", blk[:60])
        self.assertIn('id="translateToEnglish"', blk)
        self.assertIn("checked", blk)

    def test_textarea_wires_up_the_live_check(self):
        i = HTML.index('id="prompt" class="composer-prompt"')
        blk = HTML[i:i + 400]
        self.assertIn('oninput="updateLangHint()"', blk)

    def test_css_exists(self):
        self.assertIn(".lang-hint", CSS)
        self.assertIn(".lang-hint-toggle", CSS)


# ---------------------------------------------------------------- 3
class TestEnhancePromptSendsTranslateFlag(unittest.TestCase):
    def test_reads_the_toggle_and_defaults_true(self):
        fn = extract_function("enhancePrompt", QJS)
        self.assertIn("translateToEnglish", fn)
        self.assertIn("!translateEl || translateEl.checked", fn)
        self.assertIn("fd.set('translate'", fn)

    def test_passes_translated_flag_into_the_panel(self):
        fn = extract_function("enhancePrompt", QJS)
        self.assertIn("showEnhancePanel(res.original, res.enhanced, !!res.translated, enhanceLoraNote(res))", fn)


# ---------------------------------------------------------------- 4
class TestRouteComputesAndReturnsBothFlags(unittest.TestCase):
    def test_translate_default_true_and_opt_out_values(self):
        i = ROUTES_SRC.index('translate = (form.get("translate"')
        line = ROUTES_SRC[i:ROUTES_SRC.index("\n", i)]
        self.assertIn('["1"]', line)
        self.assertIn('"0", "false", "off"', line)

    def test_response_carries_both_fields(self):
        i = ROUTES_SRC.index('"original": user_prompt,')
        blk = ROUTES_SRC[i:i + 400]
        self.assertIn('"source_non_latin": source_non_latin', blk)
        self.assertIn('"translated": bool(source_non_latin and translate)', blk)

    def test_translate_param_reaches_the_helper(self):
        i = ROUTES_SRC.index('"action": "enhance_prompt"')
        blk = ROUTES_SRC[i:i + 400]
        self.assertIn('"translate": translate', blk)


# ---------------------------------------------------------------- 5
class TestHelperAddendum(unittest.TestCase):
    def test_translate_false_adds_a_same_language_addendum(self):
        i = HELPER_SRC.index('if not translate:')
        blk = HELPER_SRC[i:i + 400]
        self.assertIn("SAME language", blk)
        self.assertIn("Do NOT", blk)
        self.assertIn("translate", blk.lower())

    def test_default_is_true_unchanged_behaviour(self):
        i = HELPER_SRC.index('translate = bool(p.get("translate"')
        line = HELPER_SRC[i:HELPER_SRC.index("\n", i)]
        self.assertIn("True", line)


# ---------------------------------------------------------------- 6
class TestEnhancePanelShowsTranslatedNote(unittest.TestCase):
    def test_panel_state_machine_with_translated_true(self):
        script = """
class FakeCL { constructor() { this._s = new Set(); } toggle(){} contains(){return false;} }
function makeEl(extra) { return Object.assign({ hidden: true, textContent: '', value: '', dataset: {}, classList: new FakeCL(), events: {},
  addEventListener(t, fn) { this.events[t] = fn; },
  dispatchEvent(ev) { const fn = this.events[ev.type]; if (fn) fn(ev); } }, extra || {}); }
const ELS = {
  enhancePanel: makeEl(), enhanceOriginalText: makeEl(), enhanceEnhancedText: makeEl(),
  enhanceAcceptBtn: makeEl(), enhanceKeepBtn: makeEl(), enhanceUndoBtn: makeEl(),
  enhancePanelNote: makeEl(), prompt: makeEl(),
};
const document = { getElementById: (id) => ELS[id] || null };
%s
%s
%s
showEnhancePanel('日本語のプロンプト', 'A Japanese-language prompt, translated', true);
const afterShow = { note: ELS.enhancePanelNote.textContent, translatedFlag: ELS.enhancePanel.dataset.translated };
acceptEnhance();
const afterAccept = { note: ELS.enhancePanelNote.textContent };
console.log(JSON.stringify({ afterShow, afterAccept }));
""" % (
            extract_function("showEnhancePanel", QJS),
            extract_function("_setPromptValue", QJS),
            extract_function("acceptEnhance", QJS),
        )
        out = _run_node(script)
        self.assertEqual(out["afterShow"]["note"], "Translated to English.")
        self.assertEqual(out["afterShow"]["translatedFlag"], "1")
        self.assertIn("Translated to English.", out["afterAccept"]["note"])
        self.assertIn("Applied.", out["afterAccept"]["note"])

    def test_panel_note_empty_when_not_translated(self):
        script = """
function makeEl(extra) { return Object.assign({ hidden: true, textContent: '', value: '', dataset: {}, classList: { toggle(){}, contains(){return false;} }, events: {},
  addEventListener(t, fn) { this.events[t] = fn; }, dispatchEvent(){} }, extra || {}); }
const ELS = {
  enhancePanel: makeEl(), enhanceOriginalText: makeEl(), enhanceEnhancedText: makeEl(),
  enhanceAcceptBtn: makeEl(), enhanceKeepBtn: makeEl(), enhanceUndoBtn: makeEl(), enhancePanelNote: makeEl(),
};
const document = { getElementById: (id) => ELS[id] || null };
%s
showEnhancePanel('a cat', 'a cat, cinematic', false);
console.log(JSON.stringify({ note: ELS.enhancePanelNote.textContent }));
""" % extract_function("showEnhancePanel", QJS)
        out = _run_node(script)
        self.assertEqual(out["note"], "")


if __name__ == "__main__":
    unittest.main()
