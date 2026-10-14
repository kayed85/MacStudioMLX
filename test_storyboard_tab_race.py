#!/usr/bin/env python3
"""FILM-02 / FILM-19: the storyboard tab-entry race.

`sbInit()` runs on every entry into the Storyboard tab. It reads the last
board the user had open from localStorage, awaits `/storyboard/list`, and
then (with the old code) unconditionally opens that board — even when a
caller (Plan the video in music.js, a finished render in editor.js, the
gallery's "open this film" badge) already called `workflowSwitch('storyboard')`
then `sbOpen(theBoardItMeans)` in between. Because `sbOpen` sets `SB.id`
SYNCHRONOUSLY before its own await, the caller's board is on screen the
instant it runs — and then the slower `/storyboard/list` restore used to
land anyway and silently swap it back to whatever tab was open last time.
`sbLoad()` had the matching half of the same bug: it adopted whatever reply
came back with no check that it was still the board the user is looking at,
so a slow `/storyboard/get` reply for a board the user has since navigated
away from could overwrite the screen (and, via a later save, the board on
disk — FILM-19) with stale data.

This extracts the REAL `sbInit`/`sbOpen`/`sbLoad`/`sbRefreshBoards`/
`sbBackToList` functions from storyboard.js and runs them in node against a
`fetch` mock whose timing is controlled, so the race is reproduced (or not)
by the actual client code, not by a re-description of it.
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

FUNCTIONS = ("sbInit", "sbOpen", "sbLoad", "sbRefreshBoards", "sbBackToList")

STORYBOARD_JS = (ROOT / "webapp" / "js" / "storyboard.js").read_text(encoding="utf-8")


def _run(js: str) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(js)
        path = Path(fh.name)
    try:
        result = subprocess.run([NODE, str(path)], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise AssertionError(result.stdout + "\n" + result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])
    finally:
        path.unlink(missing_ok=True)


# Minimal shim: real SB state shape, real _sbShots/_sbEngineMode module
# globals sbInit touches, and stubs for everything sbInit/sbLoad call out to
# that is NOT part of the race itself (rendering, the run poller, etc).
SHIM = r"""
'use strict';
const _ls = {};
globalThis.localStorage = {
  getItem: k => (k in _ls ? _ls[k] : null),
  setItem: (k, v) => { _ls[k] = String(v); },
  removeItem: k => { delete _ls[k]; },
};
globalThis.sessionStorage = globalThis.localStorage;
globalThis.setInterval = () => 0;
globalThis.clearInterval = () => {};
globalThis.activePath = null;
function stub(name) { globalThis[name] = () => {}; }
['sbRenderDraftQualities', 'sbRenderFinalQualities', 'sbSetShots', 'sbRenderEnginePicker',
 'sbRenderCast', 'sbConceptInput', 'sbMustInput', 'sbShow', 'sbRenderPlan', 'sbSetPlanningStage',
 'sbPlanBtnReset', 'sbAdoptLiveEdits', 'selectOutput', 'sbRenderBoardLists', 'sbRenderRunBar',
 'sbTick',
].forEach(stub);
globalThis.sbEl = () => null;
globalThis.sbTypingInShots = () => false;

globalThis.SB = {
  id: '', payload: null, timer: null, saveTimer: null, saveInFlight: false,
  saveAgain: false, stageMode: 'auto', primed: false, boards: [], lastUndo: null,
  stage: '', films: [], filmsFor: '', filmDir: '', filmShort: '', filmOpen: '',
  boardsSig: '',
};
const SB_BOOT = {};
let _sbShots = 12;
let _sbEngineMode = 'auto';
"""


class TheTabRestoreRace(unittest.TestCase):
    """FILM-02: a caller's own sbOpen must win over the tab's last-board restore."""

    def setUp(self):
        if NODE is None:
            self.skipTest("node not on PATH")
        self.fns = "\n\n".join(extract_function(n, STORYBOARD_JS) for n in FUNCTIONS)

    def _script(self, list_delay_ms: int, get_delay_ms: int) -> str:
        return SHIM + f"""
globalThis.__fetchLog = [];
globalThis.fetch = (url) => {{
  __fetchLog.push(url);
  if (url === '/storyboard/list') {{
    return new Promise(resolve => setTimeout(() => resolve({{
      json: async () => ({{ ok: true, boards: [{{ id: 'old_board' }}] }}),
    }}), {list_delay_ms}));
  }}
  const m = /^\\/storyboard\\/get\\?id=(.+)$/.exec(url);
  if (m) {{
    const id = decodeURIComponent(m[1]);
    return new Promise(resolve => setTimeout(() => resolve({{
      json: async () => ({{ ok: true, board: {{ id, shots: [] }}, planner: {{ state: 'idle' }} }}),
    }}), {get_delay_ms}));
  }}
  return Promise.resolve({{ json: async () => ({{ ok: false }}) }});
}};

{self.fns}

// The exact caller pattern of music.js's Plan the video, editor.js's
// render-finished redirect, and the gallery's "open this film" badge:
// workflowSwitch('storyboard') (which runs sbInit) THEN sbOpen(target),
// with a board already remembered in localStorage from a previous tab.
_ls['phos_sb_open'] = 'old_board';
sbInit();
sbOpen('new_board');

setTimeout(() => {{
  console.log(JSON.stringify({{
    finalId: SB.id,
    finalPayloadBoardId: SB.payload && SB.payload.board && SB.payload.board.id,
  }}));
}}, 150);
"""

    def test_the_callers_board_wins_when_the_list_restore_is_slower(self):
        out = _run(self._script(list_delay_ms=40, get_delay_ms=5))
        self.assertEqual(out["finalId"], "new_board")
        self.assertEqual(out["finalPayloadBoardId"], "new_board")

    def test_the_callers_board_wins_even_when_the_list_restore_is_faster(self):
        # The restore's OWN sbOpen(last) is also async (sbLoad); even if
        # /storyboard/list comes back quickly, sbInit's snapshot guard must
        # have already bailed before issuing it.
        out = _run(self._script(list_delay_ms=1, get_delay_ms=30))
        self.assertEqual(out["finalId"], "new_board")
        self.assertEqual(out["finalPayloadBoardId"], "new_board")


class StaleLoadRepliesAreDropped(unittest.TestCase):
    """FILM-19 (client half): a slow /storyboard/get for a board the user has
    since navigated away from must not repaint the screen or seed a save."""

    def setUp(self):
        if NODE is None:
            self.skipTest("node not on PATH")
        self.fns = "\n\n".join(extract_function(n, STORYBOARD_JS)
                               for n in ("sbLoad",))

    def test_a_stale_reply_never_overwrites_the_newer_board(self):
        script = SHIM + """
globalThis.fetch = (url) => {
  const m = /^\\/storyboard\\/get\\?id=(.+)$/.exec(url);
  const id = decodeURIComponent(m[1]);
  const delay = id === 'stale_target' ? 40 : 5;
  return new Promise(resolve => setTimeout(() => resolve({
    json: async () => ({ ok: true, board: { id, shots: [] }, planner: { state: 'idle' } }),
  }), delay));
};

""" + self.fns + """

SB.id = 'stale_target';
sbLoad('stale_target');
// While that fetch is still in flight, another sbOpen moves SB.id on.
setTimeout(() => { SB.id = 'someone_else_opened_this'; }, 10);

setTimeout(() => {
  console.log(JSON.stringify({
    finalId: SB.id,
    payloadAdopted: !!(SB.payload && SB.payload.board),
    payloadBoardId: SB.payload && SB.payload.board && SB.payload.board.id,
  }));
}, 80);
"""
        out = _run(script)
        self.assertEqual(out["finalId"], "someone_else_opened_this")
        self.assertFalse(out["payloadAdopted"],
                         "a reply for a board nobody is looking at anymore "
                         "must never be adopted into SB.payload")


if __name__ == "__main__":
    unittest.main()
