"""VC-25: the sticky footer's derived summary line is mode-aware for Extend.

WHAT THIS GUARDS. Clicking Extend from the player (`useAsExtendSourcePath` ->
`setMode('extend')` -> `updateDerived()`) switched the form to Extend (source
picked, "Extend by 2 s"), but the footer's derived line kept showing the T2V
line built from the #width/#height/#frames fields - e.g. "5.00s · 1024×576 ->
1280×720 fit" - none of which describes what Extend is about to render:
Extend's geometry comes from whatever source clip is picked (clamped by this
Mac's tier), not those fields, and it always renders through Q8.

`updateDerived()` now branches on `currentMode === 'extend'` and reports the
seconds actually being added (rounded up to the 8-video-frame latent grid,
same arithmetic as `syncExtendDuration()`), the direction, and "Q8" instead of
the T2V geometry line. `syncExtendDuration()` (fired on every #extend_seconds
input and now every #extend_direction change too) re-runs `updateDerived()`
so the footer stays live instead of showing whatever it said the moment the
mode switched.
"""
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
from extract_panel_js import extract_function  # noqa: E402

NODE = shutil.which("node")
JS_DIR = ROOT / "webapp" / "js"


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


# A minimal DOM shim: just enough getElementById()/value plumbing for the
# handful of fields updateDerived() and syncExtendDuration() touch, plus the
# handful of ids it defensively no-ops on when absent. No jsdom dependency -
# matches the "stdlib/no new deps" rule the rest of this suite's node
# harnesses (extract_panel_js) already follows.
DOM_SHIM = r"""
function makeEl(id, value) {
  return { id, value, textContent: '', innerHTML: '', hidden: false,
           classList: { toggle(){}, add(){}, remove(){} },
           addEventListener(){}, getAttribute(){ return null; },
           dataset: {}, style: {} };
}
const FIELDS = {
  mode: 't2v', width: '1024', height: '576', frames: '121',
  steps: '8', upscale: 'off', accel: 'off', temporal_mode: 'native',
  extend_seconds: '2', extend_direction: 'after', extend_frames: '6',
  derived: '', derivedFooter: '', qualityMeta: '', warnBanner: '',
};
const ELS = {};
for (const [id, value] of Object.entries(FIELDS)) ELS[id] = makeEl(id, value);
ELS.warnBanner.classList = { _cls: new Set(), add(c){this._cls.add(c);}, remove(c){this._cls.delete(c);}, toggle(){} };

global.document = {
  // Any id updateDerived() touches beyond the ones seeded in FIELDS above
  // (section toggles, other mode-only strips) gets a generic stub instead
  // of null - this test cares about the derivedFooter branch, not every
  // section's visibility bookkeeping, and a real page always has these
  // elements.
  getElementById: (id) => (ELS[id] || (ELS[id] = makeEl(id, ''))),
  querySelector: () => null,
  body: { dataset: { engine: 'ltx' } },
};
global.escapeHtml = (s) => String(s);
global.framesToDuration = (f) => (f > 1 ? (f - 1) / 24 : 0).toFixed(2);
global.FPS = 24;
global.currentMode = FIELDS.mode;
// updateDerived() calls these unconditionally (not typeof-guarded like the
// other cross-module calls it makes) - stub them rather than widen the shim
// with the FFLF slot/timing machinery this test has nothing to do with.
global.renderKeyframeDynamicSlots = () => {};
global.syncKeyframeTiming = () => {};
global.maybeScaleTouchedKeyframeTiming = () => {};
global._updateKeyframeFramesGate = () => {};
// VA-12 (est): Extend's footer reads the server's price card — the Mac's
// Extend clamp and a per-latent-count ETA — never a browser-side price.
global.BOOT = { tier: { extend_price_draft: {
  width: 768, height: 432, eta: '~16 min',
  eta_by_latents: { '6': '~17 min', '4': '~15 min', '9': '~19 min' } } } };
global.window = { _kfTimingLastFrames: 0, BOOT: global.BOOT };
// Records the LAST 'show' state each section was toggled to.
const SHOWN = {};
const _origGet = global.document.getElementById;
global.document.getElementById = (id) => {
  const el = _origGet(id);
  if (!el._wrapped) {
    el._wrapped = true;
    el.classList = Object.assign({}, el.classList, {
      toggle(c, on) { if (c === 'show') SHOWN[id] = !!on; } });
  }
  return el;
};
"""


class ExtendFooterIsModeAware(unittest.TestCase):
    def _updateDerived_src(self) -> str:
        src = (JS_DIR / "queue.js").read_text(encoding="utf-8")
        return (extract_function("updateDerived", src) + "\n"
                + extract_function("updateDerivedForClampedMode", src))

    def _run(self, mode: str, extend_seconds: str = "2", extend_direction: str = "after") -> str:
        fn = self._updateDerived_src()
        script = DOM_SHIM + f"""
global.currentMode = {json.dumps(mode)};
ELS.mode.value = {json.dumps(mode)};
ELS.extend_seconds.value = {json.dumps(extend_seconds)};
ELS.extend_direction.value = {json.dumps(extend_direction)};
{fn}
updateDerived();
console.log(JSON.stringify({{ footer: ELS.derivedFooter.innerHTML, shown: SHOWN }}));
"""
        out = _run_node(script)
        self._shown = out["shown"]
        return out["footer"]

    def test_extend_mode_reports_extend_specific_summary(self):
        footer = self._run("extend", extend_seconds="2", extend_direction="after")
        self.assertIn("+2.0s after", footer)
        self.assertIn("Q8", footer)

    def test_extend_mode_never_shows_t2v_geometry(self):
        footer = self._run("extend")
        self.assertNotIn("1024", footer)
        self.assertNotIn("576", footer)
        self.assertNotIn("fit", footer)

    def test_extend_direction_before_is_reflected(self):
        footer = self._run("extend", extend_seconds="3", extend_direction="before")
        self.assertIn("before", footer)
        self.assertNotIn("after", footer)

    def test_extend_seconds_round_to_the_8_frame_latent_grid(self):
        # 1.1s * 24fps / 8 = 3.3 -> ceil 4 latents -> 4*8/24 = 1.33s, same
        # arithmetic as syncExtendDuration()'s own conversion.
        footer = self._run("extend", extend_seconds="1.1", extend_direction="after")
        self.assertIn("+1.3s after", footer)

    def test_extend_eta_follows_the_seconds_typed(self):
        # 2 s -> 6 latents; 3 s -> 9 latents (VC-25 live seconds x VA-12 price).
        self.assertIn("~17 min", self._run("extend", extend_seconds="2"))
        self.assertIn("~19 min", self._run("extend", extend_seconds="3"))

    def test_extend_still_shows_its_own_form_section(self):
        # 4.17 merge: est's first cut RETURNED from updateDerived() for
        # keyframe/extend right after pricing the footer, skipping every
        # section toggle below it — the Extend form never appeared.
        self._run("extend")
        self.assertTrue(self._shown.get("extendSection"))
        self.assertFalse(self._shown.get("sizingSection"))

    def test_t2v_mode_is_unaffected(self):
        footer = self._run("t2v")
        self.assertIn("1024", footer)
        self.assertNotIn("Q8", footer)

    def test_sync_extend_duration_now_refreshes_the_footer(self):
        src = (JS_DIR / "queue.js").read_text(encoding="utf-8")
        body = extract_function("syncExtendDuration", src)
        self.assertIn("updateDerived", body)

    def test_extend_direction_change_is_wired(self):
        src = (JS_DIR / "queue.js").read_text(encoding="utf-8")
        self.assertIn("extend_direction').addEventListener('change', syncExtendDuration)", src)


if __name__ == "__main__":
    unittest.main()
