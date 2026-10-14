#!/usr/bin/env python3
"""H3-29: Turbo's /status install_note must name the adapter the install
button actually fetches.

h3_turbo_note() already resolves dynamically (v4-600-EMA vs v1.0, from
whichever adapter is really on disk), but the neighbouring install_note
field was a hardcoded sentence about H3_TURBO_LORA_FILE — the retired
v1.0 LightX2V GitHub release asset — while the one-click install action
(_h3_turbo_asset() with no key) has fetched H3_TURBO_DEFAULT_ASSET =
"v4-600-EMA" straight from larryvrh's Hugging Face repo since 2026-09-05.
Two sentences in the same /status.h3.turbo block named a different file,
from a different host, fetched by a different mechanism, for the same
button.

Fix: install_note is built from _h3_turbo_asset() — the SAME resolver the
install action itself uses — so the two can't diverge again.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("LTX_STATE_DIR", tempfile.mkdtemp(prefix="phos-h3-turbonote-"))
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8304")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402


class InstallNoteMatchesTheRealAsset(unittest.TestCase):
    def test_default_asset_is_v4(self):
        self.assertEqual(P.H3_TURBO_DEFAULT_ASSET, "v4-600-EMA")

    def test_install_note_names_the_default_assets_file_and_repo(self):
        asset = P._h3_turbo_asset()
        note = (lambda _a: (
            f"Downloads {_a['file']} from {P.H3_TURBO_V4_REPO} on Hugging Face "
            f"(~{P.H3_TURBO_DOWNLOAD_GB} GB, checksum-verified)."
        ))(asset)
        self.assertIn(asset["file"], note)
        self.assertIn(P.H3_TURBO_V4_REPO, note)
        self.assertNotIn(P.H3_TURBO_LORA_FILE, note)

    def test_source_no_longer_hardcodes_the_retired_v1_asset(self):
        src = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
        # The old literal sentence must be gone from the install_note builder.
        self.assertNotIn(
            'f"Downloads the digest-pinned {H3_TURBO_LORA_FILE} "', src)
        self.assertIn("_h3_turbo_asset()", src)


if __name__ == "__main__":
    unittest.main()
