#!/usr/bin/env python3
"""H3-06: a typed per-window prompt must ADD TO the main prompt, not
replace it, and the tier note pointing at the control must name the
right direction.

Before this, `_filled = [x.strip() or prompt for x in _asked]` used a
typed window box VERBATIM as that window's entire prompt. A beat like
"he raises his glass" lost the subject, setting, soundscape and any LoRA
trigger words the main prompt carried — a real risk across a 10-27 minute
chained render, since LTX's own windowed path already has a shared-text
field for exactly this and H3 didn't.

h3_compose_window_prompts(prompt, asked, windows) is the extracted, pure
version of the fix: every non-empty box composes as
`f"{prompt} {beat}"`; an empty box still falls back to the bare main
prompt; a box that happens to equal the main prompt verbatim is not
doubled.

Separately, H3_TIER_CHAIN_NOTE told the user to "open Per-window prompts
below" — but the control (#h3WindowPromptsRow) sits ABOVE the note's own
container (#h3TierNote) in the page, by deliberate design (2026-09-18
layout pass: it lives with the prompt, not folded inside the closed Shot
setup section). Fixed to say "above" instead of moving a documented,
reasoned layout decision to match a wrong sentence.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("LTX_STATE_DIR", tempfile.mkdtemp(prefix="phos-h3-winprompts-"))
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8305")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402

PROMPT = ("A woman in a red coat stands in a rainy alley, neon signs "
          "reflecting in puddles. Audio: rain, distant traffic.")


class WindowPromptsComposeSharedTextPlusBeat(unittest.TestCase):
    def test_empty_box_falls_back_to_the_bare_prompt(self):
        out = P.h3_compose_window_prompts(PROMPT, ["", None, "  "], 3)
        self.assertEqual(out, [PROMPT, PROMPT, PROMPT])

    def test_a_beat_is_appended_not_substituted(self):
        out = P.h3_compose_window_prompts(PROMPT, ["", "he raises his glass", ""], 3)
        self.assertEqual(out[1], PROMPT + " he raises his glass")
        # The critical regression: the subject/setting/soundscape must
        # survive into the composed window prompt.
        self.assertIn("red coat", out[1])
        self.assertIn("rain", out[1])

    def test_identical_box_is_not_doubled(self):
        out = P.h3_compose_window_prompts(PROMPT, [PROMPT], 1)
        self.assertEqual(out, [PROMPT])

    def test_short_list_is_padded_with_the_bare_prompt(self):
        out = P.h3_compose_window_prompts(PROMPT, ["beat one"], 3)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0], PROMPT + " beat one")
        self.assertEqual(out[1], PROMPT)
        self.assertEqual(out[2], PROMPT)

    def test_render_path_uses_the_shared_helper(self):
        src = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
        # 4.17: routed through h3_window_prompts_for_job, which composes typed
        # beats (this test's contract) and leaves a caller's COMPLETE prompts
        # (One Shot, storyboard) whole.
        self.assertIn("_filled = h3_window_prompts_for_job(", src)
        self.assertEqual(P.h3_window_prompts_for_job("A", ["", "b"], 2), ["A", "A b"])


class ChainNotePointsTheRightDirection(unittest.TestCase):
    def test_note_says_above_not_below(self):
        self.assertIn("Per-window prompts above", P.H3_TIER_CHAIN_NOTE)
        self.assertNotIn("Per-window prompts below", P.H3_TIER_CHAIN_NOTE)


if __name__ == "__main__":
    unittest.main()
