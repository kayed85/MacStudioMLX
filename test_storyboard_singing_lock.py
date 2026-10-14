#!/usr/bin/env python3
"""FILM-21 (client half): a singing shot's mode/length/order/Rewrite are
locked in the card, not just fixed up server-side.

`sbShotCard()` used to show the same editable Text/Character toggle, length
select, ↑/↓ reorder buttons and RE-ROLL grade button on every shot alike.
For a music-video singing (a2v) shot — rendered against ONE exact stretch of
the song — any of those could silently knock it (and every later singing
shot) off its own words: the toggle could flip it to silent i2v, a length
change or reorder desyncs its film_start, and Rewrite drops its
music_video/audio block entirely (see FILM-01/13 fixes in
storyboard_planner.py). This extracts the REAL sbShotCard/sbFmtClock/
sbShotEst and runs them in node to prove the locked controls are actually
disabled (or replaced) in the rendered markup for a singing shot, and that
an ordinary shot is completely unaffected.
"""
from __future__ import annotations

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
FUNCTIONS = ("sbShotCard", "sbFmtClock", "sbShotEst")

SHIM = r"""
'use strict';
function escapeHtml(s) { if (!s) return ''; return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function friendlyJobError(raw) { return { friendly: String(raw || ''), hint: '' }; }
function sbEngineChip(id) { return '<span class="sb-chip-engine-stub"></span>'; }
globalThis.BOOT = { ltx: { lengths: [] }, h3: { lengths: [] } };
"""


def _card(shot_over, r_over=None):
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
    fns = "\n\n".join(extract_function(n, STORYBOARD_JS) for n in FUNCTIONS)
    shot = {"n": 3, "mode": "text", "prompt": "a plain shot", "status": "pending",
           "duration_s": 5, "seed": -1, "draft_output": "/out/s3.mp4"}
    shot.update(shot_over)
    r = {"per_shot_est": {}, "characters": [], "h3_available": False,
        "board": {"shots": [{"n": 1}, {"n": 2}, shot, {"n": 4}]}}
    if r_over:
        r.update(r_over)
    import json as _json
    script = SHIM + fns + f"""
const out = sbShotCard({_json.dumps(shot)}, {_json.dumps(r)}, []);
console.log(out);
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = Path(fh.name)
    try:
        result = subprocess.run([NODE, str(path)], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise AssertionError(result.stdout + "\n" + result.stderr)
        return result.stdout
    finally:
        path.unlink(missing_ok=True)


class SingingShotIsLocked(unittest.TestCase):
    def test_the_mode_toggle_is_replaced_by_a_readonly_chip(self):
        html = _card({"mode": "a2v",
                      "music_video": {"kind": "singing", "section": "verse 2",
                                      "film_start": 31.0}})
        self.assertNotIn('data-act="mode"', html)
        self.assertIn("sb-chip-singing", html)
        self.assertIn("Singing", html)
        self.assertIn("verse 2", html)
        self.assertIn("0:31", html)

    def test_duration_up_down_and_reroll_are_disabled(self):
        html = _card({"mode": "a2v",
                      "music_video": {"kind": "singing", "film_start": 0.0}})
        dur = re.search(r'<select class="sb-select sb-shot-dur"[^>]*>', html).group(0)
        self.assertIn("disabled", dur)
        up = re.search(r'data-act="up"[^>]*>', html).group(0)
        down = re.search(r'data-act="down"[^>]*>', html).group(0)
        self.assertIn("disabled", up)
        self.assertIn("disabled", down)
        reroll = re.search(r'data-act="grade" data-g="reroll"[^>]*>', html).group(0)
        self.assertIn("disabled", reroll)

    def test_the_card_is_not_draggable(self):
        html = _card({"mode": "a2v",
                      "music_video": {"kind": "singing", "film_start": 0.0}})
        self.assertIn('draggable="false"', html)

    def test_an_ordinary_shot_is_completely_unaffected(self):
        html = _card({"mode": "text"})
        self.assertIn('data-act="mode"', html)
        self.assertNotIn("sb-chip-singing", html)
        dur = re.search(r'<select class="sb-select sb-shot-dur"[^>]*>', html).group(0)
        self.assertNotIn("disabled", dur)
        up = re.search(r'data-act="up"[^>]*>', html).group(0)
        self.assertNotIn("disabled", up)
        reroll = re.search(r'data-act="grade" data-g="reroll"[^>]*>', html).group(0)
        self.assertNotIn("disabled", reroll)
        self.assertIn('draggable="true"', html)

    def test_a_broll_music_video_shot_is_not_locked(self):
        # Only SINGING (a2v) music-video shots lock — B-roll shots on the
        # same board are ordinary i2v shots and stay fully editable.
        html = _card({"mode": "text",
                      "music_video": {"kind": "broll", "film_start": 4.0}})
        self.assertNotIn("sb-chip-singing", html)
        dur = re.search(r'<select class="sb-select sb-shot-dur"[^>]*>', html).group(0)
        self.assertNotIn("disabled", dur)


class NewTakeButton(unittest.TestCase):
    """FILM-22: a done card gets an "Edit & re-render" action — no planner
    call, just archives the clip and reseeds."""

    def test_the_button_only_appears_on_a_done_shot(self):
        html = _card({"status": "done"})
        self.assertIn('data-act="newtake"', html)

    def test_the_button_is_absent_on_a_pending_or_queued_shot(self):
        for status in ("pending", "queued", "rendering", "failed"):
            html = _card({"status": status})
            self.assertNotIn('data-act="newtake"', html, status)


if __name__ == "__main__":
    unittest.main()
