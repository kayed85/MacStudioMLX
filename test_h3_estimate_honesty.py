"""H3-23 / H3-24 / H3-25 / H3-40: the numbers H3 shows before and during a
render didn't agree with each other or with the mechanism they described.

H3-23 [P2]: no ETA during H3's load phase (staged weight loads — the text
encoder, then the transformer — can take minutes on a 48 GB Mac). The
progress loop hardcoded eta=0.0 until the first denoise step landed, and
0.0 is falsy in JS, so the Now card's own "elapsed / ~eta left" branch never
fired and only "X elapsed" showed.

H3-24 [P3]: one cell showed three different numbers at once — the chip and
the Shot setup summary round to the nearest WHOLE minute (the server's own
_fmt_eta / the client's h3FmtEtaMin), but the footer's h3EstimateLine()
rounded to the nearest HALF minute below 10 min, so a 6.8-min cell read
"~7" on the chip and "6.5" in the footer.

H3-25 [P3]: the Speed row said "Fast = 3 steps" (FORWARDS), but the
#steps mirror used to fall back to `tri.steps` (SIGMA POINTS, e.g. 4) —
different units, same render, two numbers on screen.

H3-40 [P2]: "Fast = 3 steps * Best = full steps" stated the mechanism, not
the trade-off a user is actually choosing between.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("LTX_STATE_DIR", tempfile.mkdtemp(prefix="phos-h3est-"))
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402

NODE = shutil.which("node")
ENGINES = (ROOT / "webapp" / "js" / "engines.js").read_text(encoding="utf-8")
INDEX = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
PANEL = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")


def _h3_run_block() -> str:
    i = PANEL.index('_load_est = H3_LOAD_SEC * _h3_speed_factor(chain_windows)')
    j = PANEL.index('rc = proc.wait()', i)
    return PANEL[i:j]


class H3_23_LoadPhaseEta(unittest.TestCase):
    def test_load_eta_is_seeded_from_the_cell_not_hardcoded_zero(self):
        block = _h3_run_block()
        self.assertNotRegex(block, r"pct, eta = max\(3\.0, win_base\), 0\.0")
        self.assertIn("_load_eta_seed", block)
        self.assertIn("_load_eta_seed - elapsed", block)

    def test_seed_prefers_tristep_min_when_fast_is_on(self):
        block = _h3_run_block()
        self.assertRegex(
            block,
            r'tier\.get\("tristep_min"\) if tristep and tier\.get\("tristep_min"\) is not None')
        self.assertIn('else tier.get("eta_min")', block)

    def test_remaining_sec_no_longer_requires_a_completed_step(self):
        block = _h3_run_block()
        self.assertNotIn('"remaining_sec": eta if last_step else None', block)
        self.assertIn('"remaining_sec": eta,', block)

    def test_seed_computation_is_reachable_at_import(self):
        """The formula itself, exercised directly against the real cost
        model (not just grepped) — H3_LOAD_SEC times the same speed factor
        every other H3 estimate uses."""
        factor = P._h3_speed_factor(1)
        self.assertGreater(P.H3_LOAD_SEC * factor, 0)


@unittest.skipUnless(NODE, "node not on PATH")
class H3_24_OneFormatterEverywhere(unittest.TestCase):
    def _run(self, js: str):
        r = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_h3_fmt_eta_min_rounds_to_a_whole_minute(self):
        from scripts.extract_panel_js import extract_function
        js = extract_function("h3FmtEtaMin", ENGINES) + """
console.log(JSON.stringify([h3FmtEtaMin(6.8), h3FmtEtaMin(6.2), h3FmtEtaMin(0.4)]));
"""
        out = self._run(js)
        vals = __import__("json").loads(out)
        for v in vals:
            self.assertNotIn(".", v, f"{v!r} is not a whole-minute string")

    def test_h3_estimate_line_now_calls_the_shared_formatter(self):
        """Regression pin: the removed CODE line was
        `Math.round(m * 2) / 2` for m < 10 (a bespoke half-minute round) —
        h3EstimateLine must now compute its number through h3FmtEtaMin,
        the same formatter the chip and the Shot setup summary use."""
        from scripts.extract_panel_js import extract_function
        fn_src = extract_function("h3EstimateLine", ENGINES)
        code_lines = [ln for ln in fn_src.splitlines() if not ln.strip().startswith("//")]
        self.assertFalse(any("Math.round(m * 2) / 2" in ln for ln in code_lines))
        self.assertIn("h3FmtEtaMin(m)", fn_src)

    def test_h3_estimate_line_matches_h3_fmt_eta_min_exactly(self):
        """End to end: run both functions on the same fractional-minute
        value and require byte-identical numbers (modulo the ~/batch
        decorations h3EstimateLine strips for its own "≈ N min" shape)."""
        from scripts.extract_panel_js import extract_function
        js = "\n".join(extract_function(n, ENGINES) for n in (
            "h3FmtEtaMin", "h3CurrentCell", "h3TriStepOn", "h3TriStepState",
            "h3SpeedPref", "_h3IsI2V", "_h3TriStepMin", "h3CellEtaMin",
            "h3FaceFixOn", "h3EstimateLine")) + """
var H3 = { tristep: { available: false } };
var document = {
  getElementById: (id) => (id === 'mode') ? { value: 't2v' }
    : (id === 'h3_upscale') ? { value: 'off' } : (id === 'h3_steps') ? { value: 'auto' } : null,
};
function h3CurrentCell() {
  return { quality_label: 'Draft', eta_min: 6.8, tristep_min: null, chain_windows: 1,
           per_forward_sec: 1, fixed_sec: 1 };
}
console.log(JSON.stringify([h3FmtEtaMin(6.8), h3EstimateLine()]));
"""
        r = self._run(js)
        import json as _json
        fmt, line = _json.loads(r)
        bare = fmt.replace('~', '').replace(' · batch', '')
        self.assertIn(bare, line)


class H3_25_StepsMatchTheSpeedRow(unittest.TestCase):
    def test_apply_speed_no_longer_mirrors_sigma_points_as_forwards(self):
        """The old line: `if (s) s.value = tri.steps || 4;` — tri.steps is
        the ADAPTER's sigma-point count (the server's H3_TRISTEP_STEPS,
        4), not the forwards count the Speed row's "Fast = 3 steps" text
        names. Regression-pinned by absence, then by the replacement's
        shape."""
        self.assertNotRegex(ENGINES, r"s\.value = tri\.steps \|\| 4;")
        self.assertIn("cell.tristep_forwards", ENGINES)

    def test_server_forwards_and_sigma_points_are_related_by_one(self):
        """The units the two client numbers must agree on: forwards =
        sigma points - 1, exactly the H3_TRISTEP_FORWARDS definition."""
        self.assertEqual(P.H3_TRISTEP_FORWARDS, P.H3_TRISTEP_STEPS - 1)

    def test_a_cell_carries_its_own_tristep_forwards(self):
        cell = next((c for c in P.H3_TIERS.values() if c.get("tristep_min") is not None), None)
        self.assertIsNotNone(cell, "no H3 tier offers TriStep — cannot pin the field")
        self.assertIn("tristep_forwards", cell)
        self.assertGreater(cell["tristep_forwards"], 0)


class H3_40_TradeOffNotMechanism(unittest.TestCase):
    def test_speed_row_states_the_tradeoff(self):
        self.assertNotIn("Fast = 3 steps &middot; Best = full steps", INDEX)
        self.assertIn("quicker, a touch softer", INDEX)
        self.assertIn("sharpest faces", INDEX)


if __name__ == "__main__":
    unittest.main()
