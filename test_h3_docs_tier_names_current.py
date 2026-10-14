#!/usr/bin/env python3
"""H3-31 (docs remainder): docs/H3_ENGINE.md's Tiers table used the
RETIRED fixed-tier vocabulary (HQ / Wide / Long) throughout, even though
the UI moved to two independent axes — Quality (canvas) x Length
(duration) — and H3_TIER_ALIASES (mlx_ltx_panel.py) only keeps those old
composite keys alive to resolve a sidecar written before the split.
Someone reading the docs to understand the current UI would learn names
that don't exist in the picker.

Rewrote the table to the current Quality/Length columns and added an
explicit legacy-key -> current-key table, sourced from the real
H3_TIER_ALIASES dict so it can't drift silently.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("LTX_STATE_DIR", tempfile.mkdtemp(prefix="phos-h3-docsnames-"))
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8306")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402

H3_DOCS = (ROOT / "docs" / "H3_ENGINE.md").read_text(encoding="utf-8")


class TierTableUsesCurrentNames(unittest.TestCase):
    def test_table_header_is_quality_by_length(self):
        self.assertIn("| Quality | Length | Geometry", H3_DOCS)

    def test_no_row_still_labelled_hq_wide_or_long(self):
        # The retired vocabulary may still appear in PROSE explaining the
        # alias mechanism, but not as a table row label.
        for bad in ("| HQ ", "| Wide ", "| Long ", "| Wide / High"):
            self.assertNotIn(bad, H3_DOCS)

    def test_legacy_alias_table_matches_the_real_dict(self):
        aliases = P.H3_TIER_ALIASES
        self.assertTrue(aliases, "H3_TIER_ALIASES is empty or missing")
        # Identity entries (e.g. draft_3s -> draft_3s) exist for uniform
        # lookup, not because "draft_3s" is a retired name — only the
        # genuinely renamed keys belong in a legacy/current translation table.
        renamed = {k: v for k, v in aliases.items() if k != v}
        self.assertTrue(renamed)
        for legacy, current in renamed.items():
            self.assertIn(f"`{legacy}`", H3_DOCS)
            self.assertIn(f"`{current}`", H3_DOCS)


if __name__ == "__main__":
    unittest.main()
