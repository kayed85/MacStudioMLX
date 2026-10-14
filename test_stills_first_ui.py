#!/usr/bin/env python3
"""FILM-46 (client half): the Stills-first checkbox implies Anchor stills,
and a card shows Approve for a still awaiting one."""
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


def _node(script: str) -> str:
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


class TheCheckboxImpliesAnchorStills(unittest.TestCase):
    def test_checking_stills_first_forces_and_locks_anchor_stills(self):
        fn = extract_function("sbStillsFirstSync", STORYBOARD_JS)
        script = f"""
'use strict';
const els = {{
  sbStillsFirst: {{ checked: true }},
  sbAnchorStills: {{ checked: false, disabled: false }},
}};
function sbEl(id) {{ return els[id]; }}
{fn}
sbStillsFirstSync();
console.log(JSON.stringify(els));
"""
        out = json.loads(_node(script))
        self.assertTrue(out["sbAnchorStills"]["checked"])
        self.assertTrue(out["sbAnchorStills"]["disabled"])

    def test_unchecking_stills_first_re_enables_anchor_stills(self):
        # Anchor stills was locked (checked + disabled) while Stills first
        # was on; turning Stills first back off must free it again so the
        # user can independently decide whether to keep anchor stills.
        fn = extract_function("sbStillsFirstSync", STORYBOARD_JS)
        script = f"""
'use strict';
const els = {{
  sbStillsFirst: {{ checked: false }},
  sbAnchorStills: {{ checked: true, disabled: true }},
}};
function sbEl(id) {{ return els[id]; }}
{fn}
sbStillsFirstSync();
console.log(JSON.stringify(els));
"""
        out = json.loads(_node(script))
        self.assertFalse(out["sbAnchorStills"]["disabled"])
        # still checked — turning Stills first off does not itself uncheck
        # anchor stills, it only unlocks the choice
        self.assertTrue(out["sbAnchorStills"]["checked"])


class TheCardShowsApproveWhenAwaiting(unittest.TestCase):
    def _card(self, shot_over, board_over=None):
        fn = extract_function("sbShotCard", STORYBOARD_JS)
        shot = {"n": 1, "mode": "text", "prompt": "x", "status": "pending",
               "duration_s": 5, "seed": -1}
        shot.update(shot_over)
        board = {"shots": [shot], "stills_first": True}
        if board_over:
            board.update(board_over)
        r = {"per_shot_est": {}, "characters": [], "h3_available": False, "board": board}
        script = r"""
'use strict';
function escapeHtml(s) { if (!s) return ''; return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function friendlyJobError(raw) { return { friendly: String(raw || ''), hint: '' }; }
function sbEngineChip() { return ''; }
function sbFmtClock(s) { return '0:00'; }
function sbShotEst() { return ''; }
globalThis.BOOT = { ltx: { lengths: [] }, h3: { lengths: [] } };
""" + fn + f"""
console.log(sbShotCard({json.dumps(shot)}, {json.dumps(r)}, []));
"""
        return _node(script)

    def test_an_unapproved_still_shows_an_approve_button(self):
        html = self._card({"still": "/o/s1.png", "still_approved": False})
        self.assertIn('data-act="approve-still"', html)
        self.assertIn("sb-still-awaiting", html)

    def test_an_approved_still_shows_no_approve_button(self):
        html = self._card({"still": "/o/s1.png", "still_approved": True})
        self.assertNotIn('data-act="approve-still"', html)

    def test_stills_first_off_shows_no_approve_button_either(self):
        html = self._card({"still": "/o/s1.png", "still_approved": False},
                          board_over={"stills_first": False})
        self.assertNotIn('data-act="approve-still"', html)

    def test_a_user_photo_never_shows_approve(self):
        html = self._card({"still": "/o/singer.png", "still_source": "user",
                           "still_approved": False})
        self.assertNotIn('data-act="approve-still"', html)


if __name__ == "__main__":
    unittest.main()
