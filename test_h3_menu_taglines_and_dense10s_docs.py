#!/usr/bin/env python3
"""H3-16 and H3-15 (docs half).

H3-16: the engine-menu taglines didn't help anyone choose. LTX's "every
mode, LoRAs, characters" implied LoRAs (and, next to H3's tagline,
audio) were LTX-exclusive — neither is true: H3 has its own LoRA
library, and LTX 2.3/2.5 already generate audio jointly. Both taglines
now name the capability the other engine might make a user think they'd
lose.

H3-15 (docs half): docs/H3_ENGINE.md said `LTX_H3_DENSE_10S` (unset)
"re-adds" the dense 10 s tier — false. The tier ships `"offered": True"
unconditionally in H3_LENGTHS (mlx_ltx_panel.py) and the env var is not
read anywhere in the codebase. Docs corrected to say so.
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PANEL_PY = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
H3_DOCS = (ROOT / "docs" / "H3_ENGINE.md").read_text(encoding="utf-8")


class EngineMenuTaglinesDontOverclaimExclusivity(unittest.TestCase):
    def test_ltx_tagline_names_h3s_lora_support_and_own_audio(self):
        self.assertIn('"tagline": "every mode · LoRAs, characters, its own joint audio",',
                       PANEL_PY)

    def test_h3_tagline_names_its_own_loras(self):
        # 4.19: H3 serves Keyframes, Extend, Lip-sync and Add sound too, so the
        # old "Text and Image only." clause would now under-claim.
        self.assertIn(
            '"tagline": "joint video + dialogue + sound, its own LoRAs. Keyframes, Extend, Lip-sync and Add sound too.",',
            PANEL_PY)


class DenseTenSecondDocsMatchReality(unittest.TestCase):
    def test_env_var_documented_as_dead(self):
        self.assertIn("dead — not read anywhere in the code (H3-15)", H3_DOCS)

    def test_no_remaining_false_reads_restores_claim(self):
        self.assertNotIn("`LTX_H3_DENSE_10S=1` restores the old dense 10 s tier", H3_DOCS)
        self.assertIn("ships visible by default, always", H3_DOCS)

    def test_source_confirms_offered_is_unconditional(self):
        self.assertIn('"dense": True,\n            "offered": True,', PANEL_PY)
        # And confirm no code path actually branches on the env var.
        self.assertNotIn('os.environ.get("LTX_H3_DENSE_10S"', PANEL_PY)


if __name__ == "__main__":
    unittest.main()
