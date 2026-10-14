#!/usr/bin/env python3
"""FILM-57: undo survives a reload, only when it is safe to trust.

The stack (SBE.undo/SBE.redo) is in-memory only, so a reload always starts
both empty — indistinguishable from "the history is gone" unless something
persists it. What makes restoring it safe is not the film's id alone: a
reload re-adopts the last SAVED document, and if the tab had unsaved edits,
the screen coming back is NOT the screen the stack was built for — an undo
stack pointing at states the current arrangement never passed through is
worse than no undo (sbeUndo would jump the arrangement to something
unrelated to what's on screen). So the persisted entry carries a
fingerprint of the exact content it was written beside, and is only ever
adopted when that fingerprint matches what the server just handed back.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

from extract_panel_js import extract_function, panel_source  # noqa: E402

from test_editor_save_integrity import run  # noqa: E402

EXTRA = ("sbePersistUndo", "sbeRestoreUndoIfFresh", "sbeUndoFingerprint",
         "sbeUndoStoreKey")

SESSION_SHIM = r"""
// A module-level `let`, not a function — extract_function only pulls
// function bodies by name, so the two functions' shared bit of state has
// to be declared here, at the same top-level scope they will sit in.
let _sbeUndoLastFingerprint = '';
const SESSION_STORE = new Map();
const setItemCalls = [];
global.sessionStorage = {
  getItem: (k) => (SESSION_STORE.has(k) ? SESSION_STORE.get(k) : null),
  setItem: (k, v) => { setItemCalls.push(k); SESSION_STORE.set(k, String(v)); },
  removeItem: (k) => { SESSION_STORE.delete(k); },
};
"""


class ThePersistedStack(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.src = panel_source()
        consts = "\n".join(re.findall(
            r"^const SBE_UNDO_STORE_PREFIX = .*?;$", cls.src, re.M))
        assert consts.count("const") == 1, consts
        cls.r = run(r"""
SBE.open = true; SBE.id = 'sb_t';
SBE.clips = [{ id: 'a', path: '/x/a.mp4', start: 0, end: 2, film_start: 0 }];
SBE.overlays = []; SBE.tracks = []; SBE.transitions = [];
SBE.undo = []; SBE.redo = [];

// Nothing persisted yet for a fresh tab — restore is a no-op.
out.restoreWithNothingStored = (() => {
  sbeRestoreUndoIfFresh();
  return [SBE.undo.length, SBE.redo.length];
})();

// A real persist writes an entry keyed to the CURRENT content.
SBE.undo = [{ clips: [] }]; SBE.redo = [];
sbePersistUndo();
out.storedAfterFirstPersist = sessionStorage.getItem(sbeUndoStoreKey('sb_t')) !== null;
out.setItemCallsAfterFirst = setItemCalls.length;

// Calling it again with NOTHING changed costs no write.
sbePersistUndo();
out.setItemCallsAfterRepeat = setItemCalls.length;

// The content changes — a new persist DOES write.
SBE.clips = [{ id: 'a', path: '/x/a.mp4', start: 0, end: 3, film_start: 0 }];
sbePersistUndo();
out.setItemCallsAfterRealChange = setItemCalls.length;

// Simulate a reload: fresh in-memory stacks, same on-screen content (the
// server handed back the same document — nothing was lost).
const savedUndo = SBE.undo.slice(), savedRedo = SBE.redo.slice();
SBE.undo = []; SBE.redo = [];
sbeRestoreUndoIfFresh();
out.restoredWhenFingerprintMatches = [SBE.undo.length, SBE.redo.length,
                                      JSON.stringify(SBE.undo) === JSON.stringify(savedUndo)];

// Simulate a reload where the on-screen content is DIFFERENT from what the
// stack was persisted against (unsaved edits that did not survive) — the
// stack must NOT be restored.
SBE.undo = []; SBE.redo = [];
SBE.clips = [{ id: 'a', path: '/x/a.mp4', start: 0, end: 99, film_start: 0 }];
sbeRestoreUndoIfFresh();
out.notRestoredWhenFingerprintDiffers = [SBE.undo.length, SBE.redo.length];

// An empty stack removes its entry rather than leaving a stale write.
SBE.clips = [{ id: 'a', path: '/x/a.mp4', start: 0, end: 3, film_start: 0 }];
SBE.undo = []; SBE.redo = [];
sbePersistUndo();
out.emptyStackClearsTheEntry = sessionStorage.getItem(sbeUndoStoreKey('sb_t'));

// A corrupt entry (oversized undo) is capped on the way back in.
const big = [];
for (let i = 0; i < 200; i++) big.push({ i });
sessionStorage.setItem(sbeUndoStoreKey('sb_t'),
  JSON.stringify({ fp: sbeUndoFingerprint(), undo: big, redo: [] }));
SBE.undo = []; SBE.redo = [];
sbeRestoreUndoIfFresh();
out.restoredIsCapped = SBE.undo.length;

// try/catch: a sessionStorage that throws never breaks the caller.
const realGet = sessionStorage.getItem, realSet = sessionStorage.setItem;
sessionStorage.getItem = () => { throw new Error('blocked'); };
sessionStorage.setItem = () => { throw new Error('blocked'); };
let threw = false;
try { sbePersistUndo(); sbeRestoreUndoIfFresh(); } catch (e) { threw = true; }
out.neverThrowsWhenStorageIsBlocked = threw;
sessionStorage.getItem = realGet; sessionStorage.setItem = realSet;
""", extra=EXTRA, shim=consts + "\n" + SESSION_SHIM)

    def test_a_fresh_tab_has_nothing_to_restore(self):
        self.assertEqual(self.r["restoreWithNothingStored"], [0, 0])

    def test_a_real_persist_writes_and_a_repeat_does_not(self):
        self.assertTrue(self.r["storedAfterFirstPersist"])
        self.assertEqual(self.r["setItemCallsAfterFirst"], 1)
        self.assertEqual(self.r["setItemCallsAfterRepeat"], 1)   # unchanged — no write
        self.assertEqual(self.r["setItemCallsAfterRealChange"], 2)  # changed — writes

    def test_matching_content_restores_the_exact_stack(self):
        n_undo, n_redo, identical = self.r["restoredWhenFingerprintMatches"]
        self.assertEqual(n_undo, 1)
        self.assertEqual(n_redo, 0)
        self.assertTrue(identical)

    def test_a_screen_that_moved_on_gets_nothing_restored(self):
        self.assertEqual(self.r["notRestoredWhenFingerprintDiffers"], [0, 0])

    def test_an_empty_stack_clears_its_own_entry(self):
        self.assertIsNone(self.r["emptyStackClearsTheEntry"])

    def test_a_restored_stack_is_capped(self):
        self.assertLessEqual(self.r["restoredIsCapped"], 80)   # SBE_UNDO_MAX

    def test_a_blocked_storage_never_throws(self):
        self.assertFalse(self.r["neverThrowsWhenStorageIsBlocked"])

    def test_it_is_session_scoped_not_local(self):
        # "did I just reload THIS TAB", not "remember my edits across a
        # browser restart days later" — that promise belongs to the
        # crash-backup lane, which already uses a server-side archive.
        fn = extract_function("sbePersistUndo", self.src)
        self.assertIn("sessionStorage", fn)
        self.assertNotIn("localStorage", fn)
        fn = extract_function("sbeRestoreUndoIfFresh", self.src)
        self.assertIn("sessionStorage", fn)
        self.assertNotIn("localStorage", fn)

    def test_restore_only_runs_on_a_genuine_open_not_a_quiet_reread(self):
        fn = extract_function("sbeAdopt", self.src)
        self.assertIn("if (!quiet) sbeRestoreUndoIfFresh();", fn)

    def test_persist_rides_the_ticks_own_clock(self):
        fn = extract_function("sbeTick", self.src)
        self.assertIn("sbePersistUndo()", fn)


if __name__ == "__main__":
    unittest.main()
