#!/usr/bin/env python3
"""H3-10: LoRA import results must use the panel's own toasts, not native
alert(), and an LTX-style file must get plain-language guidance instead of
an engineering lecture. Also fixes the stale docs claim that kohya is
refused.

`importH3Lora()` used alert() for all three outcomes: the file-type guard,
success, and failure — each one blocks the whole tab and reads as a browser
error rather than part of the app. Worse, a diffusers/PEFT-layout file (the
shape an LTX export comes in, and the file people most often try here by
mistake) surfaced the backend's engineering explanation verbatim — "the
scale the adapter was trained with (PEFT's alpha/rank) is simply not in the
file" — which answers a question nobody but the implementer asked. Meanwhile
docs/H3_ENGINE.md said kohya files are "refused, by design", when
`_h3_lora_prepare_file` (mlx_ltx_panel.py) has converted them automatically
since 2026-09-06.

Verified live against the real backend (booted panel, real /h3/loras/import
endpoint): a synthetic diffusers-layout file (to_q/to_k/to_v keys) produced
one danger-styled toast reading "This looks like an LTX LoRA, not an H3
one..."; a real kohya-layout test fixture (lora_down/lora_up + .alpha)
imported successfully with a success-styled toast reading "Imported
kohya_import_test.safetensors (1 module pair)." — no alert() fired in
either case.
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LORAS_JS = (ROOT / "webapp" / "js" / "loras.js").read_text(encoding="utf-8")
H3_DOCS = (ROOT / "docs" / "H3_ENGINE.md").read_text(encoding="utf-8")


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


class ImportUsesToastsNotAlerts(unittest.TestCase):
    def test_import_function_calls_no_alert(self):
        body = _function_body(LORAS_JS, "async function importH3Lora(")
        self.assertNotIn("alert(", body,
                          "importH3Lora still blocks the tab with a native alert()")
        self.assertIn("phosToast(", body)

    def test_ltx_lora_detector_exists_and_is_used(self):
        self.assertIn("function _h3ImportIsLtxLora(", LORAS_JS)
        self.assertIn("diffusers layout", LORAS_JS)
        body = _function_body(LORAS_JS, "async function importH3Lora(")
        self.assertIn("_h3ImportIsLtxLora(msg)", body)
        self.assertIn("looks like an LTX LoRA", body)


class DocsMatchTheRealBehaviour(unittest.TestCase):
    def test_kohya_is_documented_as_converted_not_refused(self):
        self.assertNotIn("refused, by design** |\n| kohya", H3_DOCS)
        self.assertIn("kohya / sd-scripts", H3_DOCS)
        self.assertIn("converted in place", H3_DOCS)

    def test_diffusers_still_documented_as_refused(self):
        self.assertIn("diffusers / PEFT", H3_DOCS)
        self.assertIn("refused, by design", H3_DOCS)


if __name__ == "__main__":
    unittest.main()
