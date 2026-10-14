#!/usr/bin/env python3
"""H3-34: the footer model-credit chip must name H3 when H3 is what's
running, and never show a bare path fragment for it.

Two defects in updateModelCredit()/_modelCreditLabel() (boot.js):

  1. With no clip selected (or one that predates the sidecar `model`
     field), the credit fell back to BOOT.model unconditionally — a
     server-rendered snapshot from page load that is ALWAYS the LTX pack.
     On H3, idle, the footer read "ltx-2.5-mlx-q4" while the form was set
     up to render entirely on H3.
  2. An H3 clip's raw model path (.../mlx_models/hailuo-h3/...models/
     h3-dit-q8/...) went through the same "slice off the last two path
     segments" logic LTX paths use, producing a bare "h3-dit-q8/<file>"
     fragment with no indication it names an engine at all.

Fix: _modelCreditLabel() recognizes the hailuo-h3 tree and returns
"Hailuo H3 · compact (Q8)" / "Hailuo H3 · full (bf16)"; when nothing is
selected, updateModelCredit() falls back to the ACTIVE engine (reading
H3.dit_choice.kind) rather than always BOOT.model.

Verified live: idle on H3 -> "Hailuo H3 · compact (Q8)"; idle on LTX ->
unchanged "ltx-2.5-mlx-q4"; an H3-rendered clip selected while the form
is on LTX -> "Hailuo H3 · compact (Q8)" (follows the clip, not the
surface, as designed).
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BOOT_JS = (ROOT / "webapp" / "js" / "boot.js").read_text(encoding="utf-8")


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


class ModelCreditLabelRecognizesH3(unittest.TestCase):
    def test_label_function_detects_the_hailuo_h3_tree(self):
        body = _function_body(BOOT_JS, "function _modelCreditLabel(raw) {")
        self.assertIn("hailuo-h3", body)
        self.assertIn("Hailuo H3 · compact (Q8)", body)
        self.assertIn("Hailuo H3 · full (bf16)", body)


class UpdateModelCreditFallsBackToTheActiveEngine(unittest.TestCase):
    def setUp(self):
        self.body = _function_body(BOOT_JS, "function updateModelCredit(path) {")

    def test_idle_h3_reads_dit_choice_not_boot_model(self):
        self.assertIn("document.body.dataset.engine === 'h3'", self.body)
        self.assertIn("H3.dit_choice", self.body)

    def test_still_falls_back_to_boot_model_for_ltx(self):
        self.assertIn("_modelCreditLabel(raw || BOOT.model)", self.body)

    def test_link_and_title_follow_which_engine_is_credited(self):
        self.assertIn("minimax-h3-mlx", self.body)
        self.assertIn("dgrauet/ltx-2-mlx", self.body)


if __name__ == "__main__":
    unittest.main()
