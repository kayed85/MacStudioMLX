"""VC-27: the same render summary no longer prints three times, and the one
line that's left (the sticky footer, right above Generate) carries the
quality label and the ETA.

WHAT THIS GUARDS. A plain Balanced 5s T2V render showed essentially the
same numbers in three places at once:
  - the Shot-setup disclosure's collapsed summary ("Balanced · 5s · 16:9 ·
    ~3 min") - a real `<summary>` collapsed-state indicator, kept
  - a standalone always-visible box, #derived ("Duration 5.00s @ 24fps ·
    1024x576 -> 1280x720 fit · Steps 8") - never inside any collapsed
    disclosure, so nothing was lost by dropping it; removed outright
  - the sticky footer's own line, #derivedFooter ("5.00s · 1024x576 ->
    1280x720 fit") - kept, and now the one true summary

Removing #derived wins back real vertical space at 1280-1440px (VC-09) and
ends the duplication. The surviving footer line was upgraded to match the
finding's own suggested shape - "Balanced · 5 s · 1280x720 · ~3 min" -
reading its quality label and ETA from the SAME cell the quality chip
strip renders from (engines.js's ltxCurrentCell/ltxCellEta), so it cannot
disagree with what the chips themselves say.
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


class TheStandaloneDuplicateIsGone(unittest.TestCase):
    def test_derived_div_removed_from_markup(self):
        self.assertNotIn('id="derived"', HTML)
        self.assertNotIn('class="derived"', HTML)

    def test_derived_css_rule_removed(self):
        self.assertNotRegex(CSS, r"\n\s*\.derived\s*\{")

    def test_update_derived_no_longer_writes_it(self):
        src = (JS_DIR / "queue.js").read_text(encoding="utf-8")
        fn = extract_function("updateDerived", src)
        self.assertNotIn("getElementById('derived')", fn)
        self.assertNotIn('getElementById("derived")', fn)

    def test_the_two_real_disclosure_summaries_survive(self):
        """#shotSetupSummary and #customizeSummary are <summary> collapsed-
        state text for their OWN <details> - not the redundant box - and
        must not have been swept up by the same cleanup."""
        self.assertIn('id="shotSetupSummary"', HTML)
        self.assertIn('id="customizeSummary"', HTML)


DOM_SHIM = r"""
function makeEl(id, value) {
  return { id, value, textContent: '', innerHTML: '', hidden: false,
           classList: { toggle(){}, add(){}, remove(){} },
           addEventListener(){}, getAttribute(){ return null; },
           dataset: {}, style: {} };
}
const FIELDS = {
  mode: 't2v', width: '1024', height: '576', frames: '121',
  steps: '8', upscale: 'fit_720p', accel: 'off', temporal_mode: 'native',
  extend_seconds: '2', extend_direction: 'after', extend_frames: '6',
  derivedFooter: '', qualityMeta: '', warnBanner: '',
};
const ELS = {};
for (const [id, value] of Object.entries(FIELDS)) ELS[id] = makeEl(id, value);
ELS.warnBanner.classList = { _cls: new Set(), add(c){this._cls.add(c);}, remove(c){this._cls.delete(c);}, toggle(){} };
global.document = {
  getElementById: (id) => (ELS[id] || (ELS[id] = makeEl(id, ''))),
  querySelector: () => null,
  body: { dataset: { engine: 'ltx' } },
};
global.escapeHtml = (s) => String(s);
global.framesToDuration = (f) => (f > 1 ? (f - 1) / 24 : 0).toFixed(2);
global.FPS = 24;
global.renderKeyframeDynamicSlots = () => {};
global.syncKeyframeTiming = () => {};
global.maybeScaleTouchedKeyframeTiming = () => {};
global._updateKeyframeFramesGate = () => {};
global.window = { _kfTimingLastFrames: 0 };
"""


class FooterLineCarriesQualityAndEta(unittest.TestCase):
    def _updateDerived_src(self) -> str:
        src = (JS_DIR / "queue.js").read_text(encoding="utf-8")
        return extract_function("updateDerived", src)

    def _run(self, mode: str, *, cell) -> str:
        fn = self._updateDerived_src()
        script = DOM_SHIM + f"""
global.currentMode = {json.dumps(mode)};
ELS.mode.value = {json.dumps(mode)};
global.ltxCurrentCell = () => ({json.dumps(cell)});
global.ltxCellEta = (c) => c && c.eta || '';
{fn}
updateDerived();
console.log(JSON.stringify({{ footer: ELS.derivedFooter.innerHTML }}));
"""
        return _run_node(script)["footer"]

    def test_t2v_footer_matches_the_findings_own_example_shape(self):
        cell = {"quality_label": "Balanced", "eta": "~3 min"}
        footer = self._run("t2v", cell=cell)
        self.assertIn("Balanced", footer)
        self.assertIn("5.00s", footer)
        self.assertIn("1280×720", footer)
        self.assertIn("~3 min", footer)

    def test_i2v_also_gets_the_quality_line(self):
        cell = {"quality_label": "Standard", "eta": "~7 min"}
        footer = self._run("i2v", cell=cell)
        self.assertIn("Standard", footer)
        self.assertIn("~7 min", footer)

    def test_no_cell_falls_back_to_the_plain_line(self):
        footer = self._run("t2v", cell=None)
        self.assertIn("5.00s", footer)
        self.assertNotIn("undefined", footer)

    def test_other_modes_are_unaffected(self):
        """Restore/Control/etc. don't sit on the quality ladder - keep the
        plain dimensions+duration line, cell or not."""
        cell = {"quality_label": "Balanced", "eta": "~3 min"}
        footer = self._run("restore", cell=cell)
        self.assertNotIn("Balanced", footer)


if __name__ == "__main__":
    unittest.main()
