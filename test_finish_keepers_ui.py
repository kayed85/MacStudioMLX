#!/usr/bin/env python3
"""FILM-47 (client half): the tally bar's "Keep all ungraded" button and
the mixed-resolution warning, against the real sbRenderTally()."""
from __future__ import annotations

import json
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


def _run(shots, r_over=None):
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
    fn = extract_function("sbRenderTally", STORYBOARD_JS)
    r = {"rendering": False}
    if r_over:
        r.update(r_over)
    script = r"""
'use strict';
const _els = {};
function mk(id) { return { id, textContent: '', innerHTML: '', hidden: true, disabled: false, title: '' }; }
function sbEl(id) { if (!_els[id]) _els[id] = mk(id); return _els[id]; }
""" + fn + f"""
sbRenderTally({json.dumps(shots)}, {json.dumps(r)});
console.log(JSON.stringify(Object.fromEntries(Object.entries(_els).map(([k, v]) => [k, v]))));
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = Path(fh.name)
    try:
        result = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise AssertionError(result.stdout + "\n" + result.stderr)
        return json.loads(result.stdout.strip())
    finally:
        path.unlink(missing_ok=True)


def _shot(n, **kw):
    s = {"n": n, "status": "done"}
    s.update(kw)
    return s


class TheKeepAllButton(unittest.TestCase):
    def test_shown_with_a_count_when_shots_are_ungraded(self):
        els = _run([_shot(1, draft_output="/o/1.mp4"),
                    _shot(2, draft_output="/o/2.mp4", grade="keep")])
        self.assertFalse(els["sbKeepAllBtn"]["hidden"])
        self.assertIn("(1)", els["sbKeepAllBtn"]["textContent"])

    def test_hidden_when_everything_is_already_graded(self):
        els = _run([_shot(1, draft_output="/o/1.mp4", grade="keep"),
                    _shot(2, draft_output="/o/2.mp4", grade="cut", status="skipped")])
        self.assertTrue(els["sbKeepAllBtn"]["hidden"])

    def test_an_unrendered_shot_does_not_count(self):
        els = _run([_shot(1, status="pending")])   # no draft_output
        self.assertTrue(els["sbKeepAllBtn"]["hidden"])


class TheMixedResolutionWarning(unittest.TestCase):
    def test_shown_when_clip_sizes_differ(self):
        els = _run([
            _shot(1, draft_output="/o/1.mp4", output_dims=[640, 448]),
            _shot(2, final_output="/o/2.mp4", output_dims=[1024, 576]),
        ])
        self.assertFalse(els["sbMixedResWarn"]["hidden"])
        self.assertIn("640x448", els["sbMixedResWarn"]["textContent"].replace("×", "x"))

    def test_hidden_when_every_clip_is_the_same_size(self):
        els = _run([
            _shot(1, draft_output="/o/1.mp4", output_dims=[1024, 576]),
            _shot(2, final_output="/o/2.mp4", output_dims=[1024, 576]),
        ])
        self.assertTrue(els["sbMixedResWarn"]["hidden"])

    def test_hidden_when_dims_are_unmeasured_legacy_shots(self):
        els = _run([_shot(1, draft_output="/o/1.mp4")])
        self.assertTrue(els["sbMixedResWarn"]["hidden"])


if __name__ == "__main__":
    unittest.main()
