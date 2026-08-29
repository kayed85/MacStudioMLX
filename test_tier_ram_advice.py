#!/usr/bin/env python3
"""The RAM number a refusal quotes must be the one the gate actually uses.

REPORTED (@fiction, Pinokio, 2026-08-29): "the high720P Q8 is not clickable,
could you solve this?" — the chip is disabled on purpose below the Q8 tier,
so the honest answer is the reason text. Reading it is what found the real
bug: four refusals told the user to "bump to 64+ GB" for High, Extend, FFLF
and the HQ chip, and all four are `allows_*` flags the table grants on
`standard`, whose floor is 48 GB. The tier modal a few clicks away already
said "48-79 GB ... Every video mode works", so the app contradicted itself and
the losing sentence cost 16 GB of hardware to act on.

`test_refusal_gates` records that this same High refusal was 23 events across
6 people in one 14-day fleet read, so the text is not hypothetical: people
reach it, and some of them will go buy a Mac because of it.

The fix derives the number from CAPABILITIES + TIER_MIN_RAM_GB. This suite
pins the derivation AND scans the shipped strings, because the failure mode
was never a wrong function — it was prose drifting away from a correct gate.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = Path(tempfile.mkdtemp(prefix="phos-tier-ram-"))
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8298")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402

PANEL_SRC = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")


class TheFloorTableIsTheOneSourceOfTruth(unittest.TestCase):

    def test_detect_tier_gates_on_the_published_floors(self):
        """A boundary the table names must be the boundary the gate uses."""
        for tier, floor in P.TIER_MIN_RAM_GB.items():
            if floor == 0:
                continue
            self.assertEqual(
                P._detect_tier.__globals__["TIER_MIN_RAM_GB"][tier], floor)
        # Exercised at the edges, since off-by-one here mis-tiers real Macs.
        cases = [(47.9, "base"), (48.0, "standard"), (79.9, "standard"),
                 (80.0, "high"), (119.9, "high"), (128.0, "pro")]
        for gb, expected in cases:
            with self.subTest(gb=gb):
                old = P.SYSTEM_RAM_GB
                try:
                    P.SYSTEM_RAM_GB = gb
                    self.assertEqual(P._detect_tier(), expected)
                finally:
                    P.SYSTEM_RAM_GB = old

    def test_the_big_three_are_available_from_48(self):
        """High / FFLF / Extend all unlock on `standard`, not on 64 GB."""
        for cap in ("allows_q8", "allows_keyframe", "allows_extend"):
            with self.subTest(cap=cap):
                self.assertEqual(P.min_ram_gb_for(cap), 48)

    def test_unknown_capability_returns_none_not_a_number(self):
        """Better no number than a confidently wrong one."""
        self.assertIsNone(P.min_ram_gb_for("allows_a_thing_that_does_not_exist"))

    def test_derivation_follows_the_table_if_the_table_moves(self):
        real = P.CAPABILITIES
        try:
            P.CAPABILITIES = {
                "base":     dict(real["base"],     allows_q8=False),
                "standard": dict(real["standard"], allows_q8=False),
                "high":     dict(real["high"],     allows_q8=True),
            }
            self.assertEqual(P.min_ram_gb_for("allows_q8"), 80)
        finally:
            P.CAPABILITIES = real


class NoRefusalQuotesAHardcodedRamNumber(unittest.TestCase):
    """The regression was prose, so the prose is what gets scanned."""

    # H3 is the one honest 64: H3_MIN_RAM_GB is 60, so a 64 GB machine really
    # is the floor. Its lines are allowed to say so.
    _H3_OK = re.compile(r"h3|hailuo|~75 GB", re.IGNORECASE)

    def test_no_ltx_capability_message_hardcodes_a_ram_figure(self):
        """Scan ADVICE only.

        The tier table's own `ram_label`/blurb legitimately name GB — they are
        the source the advice should be derived FROM. What must never be
        hardcoded is the imperative: "bump to N", "upgrade to N", "needs N".
        """
        advice = re.compile(r"(bump to|upgrade to|needs \d+\+? GB"
                            r"|requires \d+\+? GB)", re.IGNORECASE)
        offenders = []
        for i, line in enumerate(PANEL_SRC.splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith(("#", "*", "//")):
                continue                      # commentary may cite history
            if not advice.search(line):
                continue
            if self._H3_OK.search(line):
                continue                      # H3's 64 is real
            if "min_ram_gb_for" in line:
                continue                      # derived: exactly right
            offenders.append(f"{i}: {stripped[:110]}")
        self.assertEqual(
            offenders, [],
            "these tell the user a RAM number the tier table does not own — "
            "derive it with min_ram_gb_for() instead:\n  "
            + "\n  ".join(offenders))

    def test_the_four_fixed_sites_derive_their_number(self):
        """Pin the specific sites @fiction's report exposed."""
        self.assertGreaterEqual(
            PANEL_SRC.count("min_ram_gb_for('allows_q8')"), 2,
            "the High chip reason and the HQ refusal must both derive it")
        for cap in ("allows_extend", "allows_keyframe"):
            self.assertIn(f"min_ram_gb_for('{cap}')", PANEL_SRC)


if __name__ == "__main__":
    unittest.main()
