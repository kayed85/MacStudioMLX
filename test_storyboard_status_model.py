#!/usr/bin/env python3
"""FILM-42: board status numbers must not contradict each other, and a
shot's Draft/Delivery badge must reflect its OWN clip, not whichever pass
the board happens to be on.

Two specific contradictions locked here, against the REAL sbShotCard /
sbRenderPlan-shaped status logic:

  * "Finished · 0 shots" next to a rail correctly reading every draft
    rendered — the old status line unconditionally called the delivery
    pass "Finished" the moment it was current, even with zero delivered
    shots.
  * a clip probed at the Delivery canvas size badged "Draft" — the badge
    used to read one board-wide `r.pass`, not the shot's own measured size.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

from extract_panel_js import extract_function                        # noqa: E402

NODE = shutil.which("node")
STORYBOARD_JS = (ROOT / "webapp" / "js" / "storyboard.js").read_text(encoding="utf-8")


def _run(script: str) -> str:
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = Path(fh.name)
    try:
        result = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise AssertionError(result.stdout + "\n" + result.stderr)
        return result.stdout
    finally:
        path.unlink(missing_ok=True)


def _status_snippet() -> str:
    """Pulls just the status-line block out of sbRenderPlan by locating it
    between its own start/end comments would be brittle; instead this runs
    the block through a tiny stand-in that mirrors its inputs exactly, by
    extracting the WHOLE function and stubbing everything else it touches.
    """
    return extract_function("sbRenderPlan", STORYBOARD_JS)


SHIM = r"""
'use strict';
function escapeHtml(s) { return String(s == null ? '' : s); }
const _els = {};
function mk(id) {
  return { id, textContent: '', value: '', innerHTML: '', hidden: false, disabled: false, title: '',
    checked: false, classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    style: {}, dataset: {}, childElementCount: 0,
    querySelectorAll() { return []; } };
}
function sbEl(id) { if (!_els[id]) _els[id] = mk(id); return _els[id]; }
globalThis.document = { activeElement: null, querySelectorAll: () => [] };
function sbSetShots() {}
function sbSetTake() {}
function sbSyncBriefGates() {}
function sbFmtRuntime() { return ''; }
function sbFmtWall() { return ''; }
function sbHasClip() { return false; }
function sbAutoGrowPrompts() {}
function sbSyncStage() {}
function sbRenderRunBar() {}
function sbRenderTally() {}
function sbTypingInShots() { return false; }
function sbSelectOpen() { return false; }
function sbShotCard() { return ''; }
function sbShotGridTile() { return ''; }
function sbErrRow() { return ''; }
// FILM-46: sbRenderPlan() syncs the Stills-first checkbox pair on every
// render — irrelevant to the status-line math this file locks, so a no-op
// stub is correct (sbStillsFirst.js exercises the real function).
function sbStillsFirstSync() {}
let _sbShotsHtml = '';
const SB_BOOT = {};
const SB = { shotView: 'list' };
"""


class TheStatusLineNeverContradictsTheRail(unittest.TestCase):
    def _status(self, board_over, r_over=None):
        board = {"shots": [], "policy": {}}
        board.update(board_over)
        r = {"board": board, "pass": "draft", "rendering": False, "estimate": {}, "errors": []}
        if r_over:
            r.update(r_over)
        script = SHIM + _status_snippet() + f"""
sbRenderPlan({json.dumps(r)});
console.log(sbEl('sbPlanStatus').textContent);
"""
        return _run(script).strip()

    def test_a_delivery_pass_with_nothing_delivered_never_says_finished(self):
        shots = [{"n": i, "draft_output": f"/o/s{i}.mp4"} for i in range(1, 76)]
        status = self._status({"shots": shots}, {"pass": "final", "rendering": False})
        self.assertNotIn("Finished", status)
        self.assertIn("ready for delivery", status)

    def test_a_delivery_pass_fully_delivered_says_finished_with_the_right_count(self):
        shots = [{"n": i, "draft_output": f"/o/s{i}.mp4", "final_output": f"/o/s{i}_f.mp4"}
                for i in range(1, 76)]
        status = self._status({"shots": shots}, {"pass": "final", "rendering": False})
        self.assertEqual(status, "Finished · 75 shots")

    def test_a_delivery_pass_partway_through_says_so_honestly(self):
        shots = ([{"n": i, "draft_output": f"/o/s{i}.mp4", "final_output": f"/o/s{i}_f.mp4"}
                 for i in range(1, 5)]
                + [{"n": i, "draft_output": f"/o/s{i}.mp4"} for i in range(5, 11)])
        status = self._status({"shots": shots}, {"pass": "final", "rendering": False})
        self.assertEqual(status, "Delivery · 4 of 10")

    def test_draft_pass_status_is_unaffected(self):
        shots = [{"n": i, "draft_output": f"/o/s{i}.mp4"} for i in range(1, 4)]
        status = self._status({"shots": shots}, {"pass": "draft", "rendering": False})
        self.assertEqual(status, "Drafts done · 3 of 3")


CARD_SHIM = r"""
'use strict';
function escapeHtml(s) { if (!s) return ''; return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function friendlyJobError(raw) { return { friendly: String(raw || ''), hint: '' }; }
function sbEngineChip() { return ''; }
function sbFmtClock(s) { const t = Math.max(0, Math.round(s || 0)); return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`; }
function sbShotEst() { return ''; }
globalThis.BOOT = { ltx: { lengths: [] }, h3: { lengths: [] } };
"""


class TheBadgeJudgesEachShotOnItsOwnClip(unittest.TestCase):
    """FILM-42: the Draft/Delivery badge used to be ONE label for the whole
    board (r.pass) — a shot that had already delivered read "Draft" the
    moment any OTHER shot on the board still needed delivery, and vice
    versa. Each shot is judged on its own measured clip now."""

    def _card(self, shot_over, board_policy=None):
        if NODE is None:
            raise unittest.SkipTest("node not on PATH")
        fn = extract_function("sbShotCard", STORYBOARD_JS)
        shot = {"n": 1, "mode": "text", "prompt": "x", "status": "done",
               "duration_s": 5, "seed": -1, "draft_output": "/o/s1.mp4"}
        shot.update(shot_over)
        r = {"per_shot_est": {}, "characters": [], "h3_available": False,
            "board": {"shots": [shot],
                     "policy": {"final": board_policy or {"width": 1280, "height": 704}}}}
        script = CARD_SHIM + fn + f"""
console.log(sbShotCard({json.dumps(shot)}, {json.dumps(r)}, []));
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(script)
            path = Path(fh.name)
        try:
            result = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise AssertionError(result.stdout + "\n" + result.stderr)
            return result.stdout
        finally:
            path.unlink(missing_ok=True)

    def test_a_shot_probed_at_the_delivery_canvas_is_badged_delivery(self):
        html = self._card({"output_dims": [1280, 704]})
        chip = re.search(r'<span class="sb-chip sb-chip-pass"[^>]*>([^<]*)</span>', html)
        self.assertEqual(chip.group(1), "Delivery")

    def test_a_shot_probed_below_the_delivery_canvas_is_badged_draft(self):
        html = self._card({"output_dims": [640, 448]})
        chip = re.search(r'<span class="sb-chip sb-chip-pass"[^>]*>([^<]*)</span>', html)
        self.assertEqual(chip.group(1), "Draft")

    def test_badging_does_not_depend_on_any_other_shots_pass(self):
        # This is the actual reported bug: two shots, each measured, must
        # each get their OWN right answer — neither one is judged by
        # "what pass is the BOARD on" (which this function never even
        # receives — a deliberate structural fix, not just better data).
        delivered = self._card({"output_dims": [1280, 704]})
        still_draft = self._card({"output_dims": [640, 448]})
        d_chip = re.search(r'<span class="sb-chip sb-chip-pass"[^>]*>([^<]*)</span>', delivered).group(1)
        s_chip = re.search(r'<span class="sb-chip sb-chip-pass"[^>]*>([^<]*)</span>', still_draft).group(1)
        self.assertEqual((d_chip, s_chip), ("Delivery", "Draft"))

    def test_an_unmeasured_legacy_shot_falls_back_to_which_file_it_shows(self):
        html = self._card({"final_output": "/o/s1_final.mp4"})
        chip = re.search(r'<span class="sb-chip sb-chip-pass"[^>]*>([^<]*)</span>', html)
        self.assertEqual(chip.group(1), "Delivery")


if __name__ == "__main__":
    unittest.main()
