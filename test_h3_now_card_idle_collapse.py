#!/usr/bin/env python3
"""H3-32: reclaim vertical space by collapsing the idle Now card's
decoration, not its information.

The review measured Shot setup's visible height at 153-178px across
1280-1440px windows, with the pinned footer plus the Now card eating
about 45% of the column, and suggested two independent fixes: "collapse
the idle Now card, or move Speed next to the ETA in the footer." The
second was already true (h3EstimateLine() has shown "<Quality> · <Speed>
<approx> <eta>" in #derivedFooter since H3-04's fix) — this closes the
first.

An idle Now card showed a 0%-filled progress bar and full padding for
nothing there is to progress: pure decoration. Collapsed that
specifically (`.now-card.idle`): tighter padding, the empty bar hidden,
tighter meta spacing. "Idle" and the meta text ("No jobs queued...")
stay exactly as visible as before — nothing informative is hidden,
which is the distinction the 2026-05-12 "remove the collapse button"
ruling drew (that button hid ACTIVE job progress and the queue; the
idle state has neither to hide).

Verified live: the idle Now card's measured height dropped from 82px to
57px (a ~30% reduction) with the progress bar's computed display: none
and both text lines still present and readable.
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PANEL_CSS = (ROOT / "webapp" / "style" / "panel.css").read_text(encoding="utf-8")


class IdleNowCardIsCompact(unittest.TestCase):
    def test_idle_padding_is_tightened(self):
        self.assertIn(".now-card.idle { padding: 8px 14px; }", PANEL_CSS)

    def test_idle_progress_bar_is_hidden(self):
        self.assertIn(".now-card.idle .progress-bar { display: none; }", PANEL_CSS)

    def test_idle_meta_spacing_is_tightened(self):
        self.assertIn(".now-card.idle .meta { margin-top: 2px; }", PANEL_CSS)

    def test_non_idle_states_are_untouched(self):
        # Only the .idle variant collapses — a running/failed/stopped card
        # must keep its full progress bar and padding.
        self.assertIn(".now-card:not(.idle):not(.failed)", PANEL_CSS)
        self.assertNotIn(".now-card.failed .progress-bar { display: none; }", PANEL_CSS)


if __name__ == "__main__":
    unittest.main()
