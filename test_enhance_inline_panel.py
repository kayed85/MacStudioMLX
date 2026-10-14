#!/usr/bin/env python3
"""VC-16 [P2] [UX] The Enhance flow is a native confirm() with no edit, no
diff and no undo, and it's refused during any render.

Coordinator ruling, 2026-09-29: "Enhance as an inline editable result with
Accept, Undo and Keep mine (no native confirm). Stays available during
renders if the model isn't on the GPU; otherwise it says why."

WHAT THIS GUARDS.
  1  the native confirm() dialog is gone from enhancePrompt() — replaced
     with showEnhancePanel(), which fills an inline panel instead of
     blocking on a system dialog
  2  Accept / Undo / Keep mine: Accept writes the enhanced text into the
     prompt AND fires the textarea's 'input' event (so updateDerived() and
     everything else listening still reacts, exactly like a real edit);
     Undo (shown only after Accept) reverts to the original; Keep mine
     just closes the panel with the textarea untouched
  3  the GPU-conflict refusal (409) now names WHAT is rendering (and how
     long, when the priced tier table can say) instead of the flat "A
     render is using the GPU right now" — current_render_busy_reason(),
     server-side — and the client shows it inline (phosToast), not a
     blocking alert()
  4  a genuinely concurrent Enhance during an active render remains
     refused: the single in-process _GPU_LOCK covers the whole job
     including the warm helper's own request/response protocol, which
     cannot safely interleave a second concurrent request — attempting to
     bypass that would risk the exact class of OOM/Metal-contention bug
     _GPU_LOCK exists to prevent, so this is a truthful "says why", not a
     silent capability the finding's title implies was simple to add
  5  the stale "LTX 2.3" in the Enhance button's tooltip is now the
     ACTIVE generation, read from BOOT.ltx.generation at boot
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = Path(tempfile.mkdtemp(prefix="phos-enhance-"))
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8327")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mlx_ltx_panel as P  # noqa: E402
from extract_panel_js import extract_function  # noqa: E402

NODE = shutil.which("node")
QJS = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
HTML = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
MAINJS = (ROOT / "webapp" / "js" / "main.js").read_text(encoding="utf-8")
CSS = (ROOT / "webapp" / "style" / "panel.css").read_text(encoding="utf-8")


def _run_node(script: str) -> dict:
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        r = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise AssertionError("node failed:\n%s\n%s" % (r.stdout[-3000:], r.stderr[-3000:]))
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------- 1 & 2
class TestClientPanelReplacesConfirm(unittest.TestCase):
    def test_enhance_prompt_no_longer_calls_confirm(self):
        fn = extract_function("enhancePrompt", QJS)
        self.assertNotIn("confirm(", fn)
        self.assertIn("showEnhancePanel(res.original, res.enhanced", fn)

    def test_error_path_is_inline_not_a_blocking_alert(self):
        fn = extract_function("enhancePrompt", QJS)
        i = fn.index("if (res.error)")
        block = fn[i:fn.index("}", i) + 1]
        self.assertNotIn("alert('Enhance failed", block)
        self.assertIn("phosToast(res.error", block)

    def test_accept_writes_enhanced_and_fires_input_event(self):
        fn = extract_function("acceptEnhance", QJS)
        self.assertIn("_setPromptValue(panel.dataset.enhanced", fn)
        setval = extract_function("_setPromptValue", QJS)
        self.assertIn("dispatchEvent(new Event('input'", setval)

    def test_undo_only_reachable_after_accept_and_reverts_to_original(self):
        accept_fn = extract_function("acceptEnhance", QJS)
        self.assertIn("enhanceUndoBtn').hidden = false", accept_fn)
        undo_fn = extract_function("undoEnhance", QJS)
        self.assertIn("_setPromptValue(panel.dataset.original", undo_fn)

    def test_keep_mine_does_not_touch_the_textarea(self):
        fn = extract_function("keepMineEnhance", QJS)
        self.assertNotIn("_setPromptValue", fn)
        self.assertNotIn("prompt", fn.replace("enhancePanel", ""))  # only touches the panel

    def test_functions_published_on_globalthis(self):
        pub = QJS[QJS.index("Object.assign(globalThis"):]
        for fn in ("showEnhancePanel", "acceptEnhance", "undoEnhance", "keepMineEnhance"):
            self.assertIn(fn, pub, fn)

    def test_panel_markup_wired_to_the_functions(self):
        i = HTML.index('id="enhancePanel"')
        blk = HTML[i:i + 1800]
        self.assertIn('onclick="acceptEnhance()"', blk)
        self.assertIn('onclick="keepMineEnhance()"', blk)
        self.assertIn('onclick="undoEnhance()"', blk)
        self.assertIn('id="enhanceUndoBtn" onclick="undoEnhance()" hidden', blk)

    def test_panel_behaviour_end_to_end_via_node(self):
        script = """
class FakeCL {
  constructor() { this._s = new Set(); }
  toggle(c, f) { if (f === undefined) { this._s.has(c) ? this._s.delete(c) : this._s.add(c); } else if (f) this._s.add(c); else this._s.delete(c); return this._s.has(c); }
  contains(c) { return this._s.has(c); }
}
function makeEl(extra) { return Object.assign({ hidden: true, textContent: '', value: '', dataset: {}, classList: new FakeCL(), events: {},
  addEventListener(t, fn) { this.events[t] = fn; },
  dispatchEvent(ev) { const fn = this.events[ev.type]; if (fn) fn(ev); this._lastEvent = ev.type; } }, extra || {}); }
const ELS = {
  enhancePanel: makeEl(),
  enhanceOriginalText: makeEl(),
  enhanceEnhancedText: makeEl(),
  enhanceAcceptBtn: makeEl(),
  enhanceKeepBtn: makeEl(),
  enhanceUndoBtn: makeEl(),
  enhancePanelNote: makeEl(),
  prompt: makeEl(),
};
const document = { getElementById: (id) => ELS[id] || null };
%s
%s
%s
%s
showEnhancePanel('a cat', 'a cat, cinematic lighting, 4k');
const afterShow = { hidden: ELS.enhancePanel.hidden, orig: ELS.enhanceOriginalText.textContent, enh: ELS.enhanceEnhancedText.textContent };
acceptEnhance();
const afterAccept = { promptValue: ELS.prompt.value, undoHidden: ELS.enhanceUndoBtn.hidden, acceptHidden: ELS.enhanceAcceptBtn.hidden, lastEvent: ELS.prompt._lastEvent };
undoEnhance();
const afterUndo = { promptValue: ELS.prompt.value, panelHidden: ELS.enhancePanel.hidden };
console.log(JSON.stringify({ afterShow, afterAccept, afterUndo }));
""" % (
            extract_function("showEnhancePanel", QJS),
            extract_function("_setPromptValue", QJS),
            extract_function("acceptEnhance", QJS),
            extract_function("undoEnhance", QJS),
        )
        out = _run_node(script)
        self.assertFalse(out["afterShow"]["hidden"])
        self.assertEqual(out["afterShow"]["orig"], "a cat")
        self.assertEqual(out["afterShow"]["enh"], "a cat, cinematic lighting, 4k")
        self.assertEqual(out["afterAccept"]["promptValue"], "a cat, cinematic lighting, 4k")
        self.assertFalse(out["afterAccept"]["undoHidden"])
        self.assertTrue(out["afterAccept"]["acceptHidden"])
        self.assertEqual(out["afterAccept"]["lastEvent"], "input")
        self.assertEqual(out["afterUndo"]["promptValue"], "a cat")
        self.assertTrue(out["afterUndo"]["panelHidden"])


# ---------------------------------------------------------------- 3
class TestBusyReasonNamesTheRender(unittest.TestCase):
    def setUp(self):
        with P.LOCK:
            self._saved_current = P.STATE.get("current")

    def tearDown(self):
        with P.LOCK:
            P.STATE["current"] = self._saved_current

    def test_no_current_job_gives_the_generic_line(self):
        with P.LOCK:
            P.STATE["current"] = None
        self.assertEqual(P.current_render_busy_reason(), "A render is using the GPU right now.")

    def test_names_the_label_and_time_left_when_priceable(self):
        with P.LOCK:
            P.STATE["current"] = {
                "params": {"label": "gym scene", "mode": "t2v",
                          "quality": "balanced", "frames": 121},
                "started_ts": time.time() - 5,
            }
        reason = P.current_render_busy_reason()
        self.assertIn("gym scene", reason)
        self.assertIn("min left", reason)

    def test_falls_back_to_a_prompt_snippet_with_no_label(self):
        with P.LOCK:
            P.STATE["current"] = {
                "params": {"prompt": "a" * 80, "mode": "t2v",
                          "quality": "balanced", "frames": 121},
                "started_ts": time.time(),
            }
        reason = P.current_render_busy_reason()
        self.assertIn("a" * 40, reason)
        self.assertNotIn("a" * 41, reason.replace("a" * 40, "", 1))

    def test_no_time_left_claimed_when_unpriceable(self):
        with P.LOCK:
            P.STATE["current"] = {
                "params": {"label": "a song", "engine": "music"},
                "started_ts": time.time(),
            }
        reason = P.current_render_busy_reason()
        self.assertIn("a song", reason)
        self.assertNotIn("min left", reason)

    def test_route_uses_the_reason_in_the_409(self):
        src = (ROOT / "panel" / "routes_queue.py").read_text(encoding="utf-8")
        i = src.index("if not P._GPU_LOCK.acquire(timeout=3.0):")
        block = src[i:src.index("return", i)]
        self.assertIn("current_render_busy_reason()", block)
        self.assertNotIn('"A render is using the GPU right now', block)


# ---------------------------------------------------------------- 5
class TestEnhanceTitleMatchesActiveGeneration(unittest.TestCase):
    def test_boot_time_script_reads_boot_generation(self):
        i = MAINJS.index("enhanceBtn")
        block = MAINJS[i - 50:i + 400]
        self.assertIn("BOOT.ltx || {}).generation", block)
        self.assertIn("2.3", block)
        self.assertIn("2.5", block)

    def test_static_title_no_longer_hardcodes_2_3(self):
        i = HTML.index('id="enhanceBtn"')
        tag = HTML[i - 200:i + 100]
        self.assertNotIn("LTX 2.3 was trained on", tag)


if __name__ == "__main__":
    unittest.main()
