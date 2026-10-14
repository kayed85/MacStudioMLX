#!/usr/bin/env python3
"""FILM-12 (client half): a music-video picture's role must be ASKED, not
guessed, past the first (Singer) one.

Before the fix, `mvAddPictures()` defaulted every picture after the first to
role "room" with zero evidence — a photo of a guitar or a piano was silently
mislabelled "the wide, the lights, the audience" and rendered with that
prompt. This extracts the REAL mvAddPictures/mvRenderCast/mvPlan functions
from music.js and runs them in node: an uploaded cast must come back with an
UNSET role past the first picture, and mvPlan() must refuse to submit (and
never reach fetch) while any picture is unlabelled.
"""
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
MUSIC_JS = (ROOT / "webapp" / "js" / "music.js").read_text(encoding="utf-8")
FUNCTIONS = ("_el", "mvSay", "mvAddPictures", "mvRenderCast", "mvSetRole",
            "mvPlan", "_mvPlanRequest", "mvSaveDraft")

SHIM = r"""
'use strict';
// 4.17: mvRenderCast saves the brief (mvSaveDraft); node has no storage.
const MV_DRAFT_KEY = 'phos_mv_draft_v1';
global.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
function _esc(s) { return String(s == null ? '' : s); }
const _elMap = {};
function mkEl(id) {
  return { id, textContent: '', innerHTML: '', disabled: false,
    querySelectorAll() { return []; } };
}
globalThis.document = {
  getElementById(id) { if (!_elMap[id]) _elMap[id] = mkEl(id); return _elMap[id]; },
};
globalThis.MV = { song: 'song.wav', songName: 'x', songSeconds: 1, cast: [], busy: false, boardId: '' };
globalThis.workflowSwitch = () => {};
globalThis.sbOpen = async () => {};
const MV_ROLES = [
  ['singer', 'Singer', 'Filmed singing, in sync with the song'],
  ['instrument', 'Instrument', 'B-roll — hands, keys, strings'],
  ['room', 'Room', 'B-roll — the wide, the lights, the audience'],
];
"""


class TheRoleIsAskedNotGuessed(unittest.TestCase):
    def setUp(self):
        if NODE is None:
            self.skipTest("node not on PATH")
        self.fns = "\n\n".join(extract_function(n, MUSIC_JS) for n in FUNCTIONS)

    def _run(self, tail: str) -> dict:
        script = SHIM + self.fns + "\n" + tail
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(script)
            path = Path(fh.name)
        try:
            result = subprocess.run([NODE, str(path)], capture_output=True,
                                    text=True, timeout=30)
            if result.returncode:
                raise AssertionError(result.stdout + "\n" + result.stderr)
            return json.loads(result.stdout.strip().splitlines()[-1])
        finally:
            path.unlink(missing_ok=True)

    def test_only_the_first_uploaded_picture_defaults_to_singer(self):
        out = self._run(r"""
globalThis.fetch = () => Promise.resolve({
  ok: true, json: async () => ({ path: '/uploads/' + Math.random() + '.png' }),
});
(async () => {
  await mvAddPictures([{ name: 'a.png' }, { name: 'b.png' }, { name: 'c.png' }]);
  console.log(JSON.stringify({ roles: MV.cast.map(c => c.role) }));
})();
""")
        self.assertEqual(out["roles"], ["singer", "", ""])

    def test_plan_refuses_and_never_calls_the_route_while_a_picture_is_unlabelled(self):
        out = self._run(r"""
globalThis.fetch = (url) => {
  if (String(url).includes('/music/video/plan')) {
    globalThis.__planCalled = true;
  }
  return Promise.resolve({
    ok: true,
    json: async () => (String(url).includes('/upload')
      ? { path: '/uploads/' + Math.random() + '.png' }
      : { ok: true, board_id: 'sb_x', summary: '1 shots', notes: [] }),
  });
};
(async () => {
  await mvAddPictures([{ name: 'a.png' }, { name: 'b.png' }]);
  await mvPlan();
  console.log(JSON.stringify({
    planCalled: !!globalThis.__planCalled,
    status: document.getElementById('mvStatus').textContent,
    needsRoleCount: MV.cast.filter(c => !c.role).length,
  }));
})();
""")
        self.assertFalse(out["planCalled"], "must not plan with an unlabelled picture")
        self.assertIn("role", out["status"])
        self.assertEqual(out["needsRoleCount"], 1)

    def test_plan_proceeds_once_every_picture_has_a_role(self):
        out = self._run(r"""
globalThis.fetch = (url) => {
  if (String(url).includes('/music/video/plan')) globalThis.__planCalled = true;
  return Promise.resolve({
    ok: true,
    json: async () => (String(url).includes('/upload')
      ? { path: '/uploads/' + Math.random() + '.png' }
      : { ok: true, board_id: 'sb_x', summary: '1 shots', notes: [] }),
  });
};
(async () => {
  await mvAddPictures([{ name: 'a.png' }, { name: 'b.png' }]);
  mvSetRole(1, 'room');
  await mvPlan();
  console.log(JSON.stringify({ planCalled: !!globalThis.__planCalled }));
})();
""")
        self.assertTrue(out["planCalled"])


if __name__ == "__main__":
    unittest.main()
