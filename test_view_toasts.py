#!/usr/bin/env python3
"""FILM-57: a toast describing the screen does not outlive the screen.

Reported: "Picture mode —…" appeared over Storyboard after a fast tab
switch. `phosToast` (queue.js) is a shared, global stack with no notion of
which tab asked — and it should stay that way, since most of this file's
100+ toasts (a save failed, a take landed, a render finished) are exactly
as true after a tab switch as before it. Only the handful that describe the
CURRENT ON-SCREEN LAYOUT (Picture/Sound mode, side panels hidden) are wrong
once you have left, so only those go through `sbeViewToast`, tagged and
swept by `sbeSweepViewToasts` on the one signal this file already has for
"the user is leaving" — `sbeSuspend`, called from `workflowSwitch` whenever
the active tab stops being 'editor'.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

from extract_panel_js import extract_function, panel_source  # noqa: E402

from test_editor_save_integrity import run  # noqa: E402

EXTRA = ("sbeViewToast", "sbeSweepViewToasts", "sbeSoundModeToggle",
         "sbePanelsToggle", "sbeSoundMode", "sbeSoundModeSet")

DOC_SHIM = r"""
// A toast is a plain object carrying a dataset bag and a self-remove, the
// two things sbeViewToast/sbeSweepViewToasts touch — not a real DOM.
const TOASTS = [];
function makeToast() {
  const t = { dataset: {}, removed: false, remove() { t.removed = true; } };
  TOASTS.push(t);
  return t;
}
let TOAST_KIND = null;   // last call's opts, for the mode-toggle assertions
function phosToast(msg, opts) { TOAST_KIND = { msg, opts }; return makeToast(); }
global.document = {
  querySelectorAll(sel) {
    if (sel === '.phos-toast[data-sbe-view-toast="1"]') {
      return TOASTS.filter(t => !t.removed && t.dataset.sbeViewToast === '1');
    }
    return [];
  },
  body: { classList: { contains: () => false } },
};
"""


class TheScopedToast(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.src = panel_source()
        cls.r = run(r"""
const t1 = sbeViewToast('Picture mode — the picture takes the height.', { duration: 4000 });
out.tagged = t1.dataset.sbeViewToast;
const t2 = phosToast('The film could not be assembled.', { kind: 'danger' });
out.untaggedByDefault = t2.dataset.sbeViewToast || null;
out.beforeSweep = TOASTS.filter(t => !t.removed).length;
sbeSweepViewToasts();
out.afterSweep = TOASTS.filter(t => !t.removed).length;
out.taggedRemoved = t1.removed;
out.untaggedSurvives = !t2.removed;
""", extra=EXTRA, shim=DOC_SHIM)

    def test_a_view_toast_is_tagged_and_a_normal_one_is_not(self):
        self.assertEqual(self.r["tagged"], "1")
        self.assertIsNone(self.r["untaggedByDefault"])

    def test_sweeping_removes_only_the_tagged_ones(self):
        self.assertEqual(self.r["beforeSweep"], 2)
        self.assertEqual(self.r["afterSweep"], 1)
        self.assertTrue(self.r["taggedRemoved"])
        self.assertTrue(self.r["untaggedSurvives"])

    def test_the_mode_toggle_and_panels_toggle_use_the_scoped_helper(self):
        fn = extract_function("sbeSoundModeToggle", self.src)
        self.assertIn("sbeViewToast(", fn)
        self.assertNotIn("phosToast(", fn)
        fn = extract_function("sbePanelsToggle", self.src)
        self.assertIn("sbeViewToast(", fn)
        self.assertNotIn("phosToast(", fn)

    def test_leaving_the_editor_sweeps(self):
        fn = extract_function("sbeSuspend", self.src)
        self.assertIn("sbeSweepViewToasts()", fn)
        # sbeSuspend is exactly what workflowSwitch calls the moment the
        # active tab stops being 'editor' — the mechanism this test locks
        # is reached by every ordinary tab switch, not just Storyboard's.
        switch = self.src[self.src.index("function workflowSwitch"):]
        switch = switch[:switch.index("\n}\n")]
        self.assertIn("name !== 'editor'", switch)
        self.assertIn("sbeSuspend()", switch)


if __name__ == "__main__":
    unittest.main()
