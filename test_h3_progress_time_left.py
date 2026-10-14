"""The H3 Now card says how long is LEFT, not a number that reads as a total.

REPORTED (Pinokio, 2026-09-20, a 48 GB Mac): a 15 s Draft showed
"27m 23s / ~18m 21s" in window 2 of 3, and the user read ~18 min as what the
whole render was supposed to take. It was the time left: the H3 run loop put
its remaining estimate in `eta_sec`, and the Now card prints `eta_sec` as
"<elapsed> / ~<eta>". The card already has a remaining-time form
("<elapsed> in · ~<left> left") for `remaining_sec`; the H3 loop now fills it.
The same estimate also left out the staged load / prompt encode every later
window pays before its first step (H3_LOAD_SEC in the cost model).

A text-level check: the loop is a subprocess reader with no seam to drive it,
and the card logic is a few lines in queue.js's poll renderer.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PANEL = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
QUEUE = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")


class H3ProgressSaysTimeLeft(unittest.TestCase):
    def _h3_progress_block(self) -> str:
        # Anchored on a stable start marker rather than a fixed lookback
        # offset from the end — a fixed offset silently truncates the
        # window's START as unrelated code grows between the two (it did,
        # for H3-04's pace-warning addition).
        i = PANEL.index('win_span = 87.0 / float(tot_windows)')
        j = PANEL.index('"window_total": tot_windows', i)
        return PANEL[i:j]

    def test_h3_progress_carries_remaining_sec(self):
        # H3-23: remaining_sec used to be None until the first denoise step
        # landed (eta if last_step else None) — the load phase (staged
        # weight loads, which can take minutes on a 48 GB Mac) showed no ETA
        # at all. It is now populated unconditionally: eta is seeded from
        # the cell's own estimate during load, then replaced by real
        # per-step extrapolation once denoising starts.
        self.assertRegex(self._h3_progress_block(),
                         r'"remaining_sec":\s*eta,')

    def test_later_windows_pay_their_loads(self):
        block = self._h3_progress_block()
        self.assertIn("_load_est * later", block)
        self.assertRegex(PANEL, r"_load_est = H3_LOAD_SEC \* _h3_speed_factor\(chain_windows\)")

    def test_now_card_prefers_the_remaining_form(self):
        i = QUEUE.index("prog.remaining_sec != null")
        j = QUEUE.index("prog.eta_sec", i)
        self.assertLess(i, j, "the card must try remaining_sec before eta_sec")
        self.assertIn("left`", QUEUE[i:j])


if __name__ == "__main__":
    unittest.main()
