"""VC-15: the Avoid box shows when it's being ignored, instead of quietly
doing nothing.

WHAT THIS GUARDS. The Avoid (negative prompt) textarea took input at every
quality, but the distilled Q4 lane (Quick/Balanced/Standard, T2V/I2V) has no
unconditional branch to run it against — the ONLY place that said so was 10px
grey hint text above the box. A user opens Avoid on the default (Balanced),
types something, clicks Generate, and gets a clip that ignores every word of
it with zero feedback anywhere on the control itself.

Fixed with three pieces, all reading the same `_qualityUsesHq` fact the
quality chips themselves use (so this can never disagree with them):
  1. `avoidIsIgnored()` / `updateAvoidIgnoredState()` (queue.js, called from
     updateDerived() and toggleAvoidRow()) - dims the textarea
     (.is-ignored) and shows an inline note ("Ignored at Balanced — Use
     High") with a working shortcut into High quality.
  2. The collapsed toggle label itself gains "(off at Balanced)" whenever
     the row is closed and guidance would be ignored.
  3. Submitting a non-empty, ignored Avoid box shows a one-line toast
     instead of silently rendering without it.
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
from extract_panel_js import extract_function  # noqa: E402

NODE = shutil.which("node")
JS_DIR = ROOT / "webapp" / "js"
HTML = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")


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


class MarkupHasTheNote(unittest.TestCase):
    def test_ignored_note_exists_between_label_and_textarea(self):
        self.assertIn('id="avoidIgnoredNote"', HTML)
        self.assertIn('id="avoidIgnoredQuality"', HTML)
        i = HTML.index('id="avoidRow"')
        j = HTML.index('id="avoidIgnoredNote"')
        k = HTML.index('id="negative_prompt"')
        self.assertLess(i, j)
        self.assertLess(j, k)

    def test_use_high_shortcut_calls_setquality(self):
        i = HTML.index('id="avoidIgnoredNote"')
        j = HTML.index('</div>', i)
        snippet = HTML[i:j]
        self.assertIn("setQuality('high')", snippet)


DOM_SHIM = r"""
function makeEl(id, value) {
  return { id, value, textContent: '', innerHTML: '', hidden: false,
           className: '', classList: { _s: new Set(),
             toggle(c, force) { if (force === undefined) { this._s.has(c) ? this._s.delete(c) : this._s.add(c); }
                                else if (force) this._s.add(c); else this._s.delete(c);
                                return this._s.has(c); },
             contains(c) { return this._s.has(c); },
             add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); } },
           addEventListener(){}, getAttribute(){ return null; }, dataset: {}, style: {} };
}
const FIELDS = { mode: 't2v', quality: 'balanced' };
const ELS = {};
for (const [id, value] of Object.entries(FIELDS)) ELS[id] = makeEl(id, value);
ELS.avoidIgnoredNote = makeEl('avoidIgnoredNote');
ELS.negative_prompt = makeEl('negative_prompt');
ELS.avoidIgnoredQuality = makeEl('avoidIgnoredQuality');
ELS.avoidToggleLabel = makeEl('avoidToggleLabel');
ELS.avoidRow = makeEl('avoidRow');
global.document = { getElementById: (id) => (ELS[id] || (ELS[id] = makeEl(id, ''))) };
global.currentMode = FIELDS.mode;
global._qualityUsesHq = (q) => q === 'high';
global.ltxCurrentCell = () => (global.__cell || null);
"""


class AvoidIgnoredLogic(unittest.TestCase):
    def _fns(self) -> str:
        src = (JS_DIR / "queue.js").read_text(encoding="utf-8")
        return (extract_function("avoidIsIgnored", src) + "\n"
                + extract_function("updateAvoidIgnoredState", src))

    def _run(self, mode: str, quality: str, *, row_open: bool = False,
             cell: dict | None = None) -> dict:
        fns = self._fns()
        script = DOM_SHIM + f"""
global.currentMode = {json.dumps(mode)};
ELS.mode.value = {json.dumps(mode)};
ELS.quality.value = {json.dumps(quality)};
ELS.avoidRow.classList.toggle('show', {json.dumps(row_open)});
global.__cell = {json.dumps(cell)};
{fns}
updateAvoidIgnoredState();
console.log(JSON.stringify({{
  noteHidden: ELS.avoidIgnoredNote.hidden,
  taIgnored: ELS.negative_prompt.classList.contains('is-ignored'),
  toggleLabel: ELS.avoidToggleLabel.textContent,
}}));
"""
        return _run_node(script)

    def test_balanced_t2v_is_ignored(self):
        out = self._run("t2v", "balanced", cell={"quality_label": "Balanced"})
        self.assertFalse(out["noteHidden"])
        self.assertTrue(out["taIgnored"])

    def test_high_t2v_is_not_ignored(self):
        out = self._run("t2v", "high", cell={"quality_label": "High"})
        self.assertTrue(out["noteHidden"])
        self.assertFalse(out["taIgnored"])

    def test_i2v_balanced_is_also_ignored(self):
        out = self._run("i2v", "balanced", cell={"quality_label": "Balanced"})
        self.assertFalse(out["noteHidden"])

    def test_extend_is_never_flagged_ignored(self):
        """Extend always renders on Q8 with guidance - never dim it, even
        though its own #quality field (if any leftover value) might read
        as a distilled quality string."""
        out = self._run("extend", "balanced", cell=None)
        self.assertTrue(out["noteHidden"])

    def test_collapsed_toggle_label_shows_the_off_quality(self):
        out = self._run("t2v", "balanced", row_open=False,
                        cell={"quality_label": "Balanced"})
        self.assertIn("off at Balanced", out["toggleLabel"])

    def test_open_toggle_label_is_unchanged(self):
        out = self._run("t2v", "balanced", row_open=True,
                        cell={"quality_label": "Balanced"})
        self.assertEqual(out["toggleLabel"], "Avoid −")


class CrossModulePublishing(unittest.TestCase):
    """boot.js's toggleAvoidRow() calls updateAvoidIgnoredState() from a
    DIFFERENT module than the one that defines it (queue.js) - a top-level
    `function` in an ES module is module-private unless explicitly
    published (Object.assign(globalThis, {...}) at the bottom of the file,
    the pattern every other cross-module call in this codebase follows).
    Caught live: without this, toggleAvoidRow's own `typeof
    updateAvoidIgnoredState === 'function'` guard silently evaluated false
    and the toggle label never grew its "(off at Balanced)" suffix -- no
    error anywhere, just a no-op."""

    def test_both_functions_are_on_the_publish_block(self):
        src = (JS_DIR / "queue.js").read_text(encoding="utf-8")
        i = src.index("Object.assign(globalThis, {")
        j = src.index("\n});", i)
        block = src[i:j]
        self.assertIn("avoidIsIgnored", block)
        self.assertIn("updateAvoidIgnoredState", block)

    def test_boot_js_actually_calls_it_from_toggle(self):
        boot = (JS_DIR / "boot.js").read_text(encoding="utf-8")
        fn = extract_function("toggleAvoidRow", boot)
        self.assertIn("updateAvoidIgnoredState", fn)


class SubmitTimeToast(unittest.TestCase):
    def test_submit_handler_warns_on_ignored_nonempty_avoid(self):
        src = (JS_DIR / "queue.js").read_text(encoding="utf-8")
        i = src.index("genForm').addEventListener('submit'")
        j = src.index("engine payload scrub", i)
        snippet = src[i:j]
        self.assertIn("negative_prompt", snippet)
        self.assertIn("avoidIsIgnored", snippet)
        self.assertIn("phosToast", snippet)


if __name__ == "__main__":
    unittest.main()
