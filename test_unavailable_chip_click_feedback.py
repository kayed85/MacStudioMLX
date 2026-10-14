"""VC-22 (REJECTED — already fixed, verified against this exact commit; test
added as a regression lock so it can't quietly regress).

The finding: "Clicking an unavailable chip does nothing visible; the reason
exists only as a hover tooltip... On 16 GB, click 'High · UNAVAILABLE'.
Selection stays on Balanced; no message."

Verified live against beta/main 70665d3 (this package's base) with
LTX_TIER_OVERRIDE=base (a 16 GB-class Mac): clicking the disabled High chip
DOES show a message — `_ltxApplyShape()` (engines.js) writes the chip's own
`unavailable_reason` into #engineRowNote and leaves the selection on
Balanced. The code carries an explicit comment dated to an "owner ruling
2026-08-23" describing exactly this fix ("a click has to SAY the reason
rather than swallow it — a tooltip is not discoverable on a chip a user has
just tapped"), which predates the review. Nothing in this package's diff
touches this path; this file exists to prove the claim was checked, not
assumed, and to catch a real future regression.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENGINES_JS = (ROOT / "webapp" / "js" / "engines.js").read_text(encoding="utf-8")


class UnavailableChipAlreadySaysWhy(unittest.TestCase):
    def test_ltx_apply_shape_writes_the_reason_on_an_unavailable_cell(self):
        i = ENGINES_JS.index("function _ltxApplyShape")
        j = ENGINES_JS.index("\n}\n", i)
        body = ENGINES_JS[i:j]
        self.assertIn("cell.available === false", body)
        self.assertIn("unavailable_reason", body)
        self.assertIn("engineRowNote", body)
        self.assertIn("note.hidden = false", body)
        # and it must NOT silently switch the selection - the whole point
        self.assertIn("return;", body)

    def test_the_note_write_does_not_set_packnote_so_the_pack_gate_cant_clobber_it(self):
        """applyPackIncompleteGate's clear() only wipes the note when
        dataset.packNote === '1' - it READS that flag here (to avoid
        overwriting an existing pack note), but must never ASSIGN it, or
        the next 1.5s poll would erase the reason it just showed."""
        i = ENGINES_JS.index("function _ltxApplyShape")
        j = ENGINES_JS.index("\n}\n", i)
        body = ENGINES_JS[i:j]
        self.assertIn("dataset.packNote !== '1'", body)
        self.assertNotIn("dataset.packNote = ", body)
        self.assertNotIn('dataset.packNote=', body)


if __name__ == "__main__":
    unittest.main()
