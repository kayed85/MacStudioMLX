#!/usr/bin/env python3
"""VC-18/37 — LTX Draft -> Finish, the mirror of H3's "same length, higher
quality" for LTX's own two-axis table (VC-28's honest tier chips).

Coordinator ruling, 2026-09-29 (on the mega-safety cherry-pick + toolbar
pass): "...Draft->Finish for LTX". H3's own Finish was already shipped;
this generalizes the same user concept — commit a draft at higher
quality, same length, same prompt, same seed — to LTX, sharing ONE
toolbar slot (#h3FinishWrap/#h3FinishBtn/#h3FinishTier) between both
engines via a small dispatcher rather than duplicating markup.

WHAT THIS GUARDS.
  1  list_outputs() lifts an LTX clip's own `quality`/`frames` the same
     way it already lifts H3's `h3_tier` — needed so the Finish
     affordance can gate synchronously from the /status payload, no
     extra /sidecar fetch per gallery click. Only for an actual LTX clip
     (engine None or "ltx"); an H3 or music clip's sidecar `quality`-like
     fields (if any) must never leak into this.
  2  ltxFinishTargets(): same length, every quality ABOVE the source's,
     offered and available on this install — never a lower or equal rung
  3  ltxFinishTierKey(): the persisted choice wins when it's still a
     valid target, else the cheapest next rung up (never the most
     expensive by default)
  4  ltxFinishFieldsFromSidecar(): PURE — carries prompt/seed_used(not
     the submitted -1)/upscale/accel/loras forward, refuses a non-LTX or
     i2v-without-image sidecar, and is scoped to t2v/i2v only (same
     restriction H3's own Finish has — Extend/Keyframe don't carry a
     "same length" concept the way a fixed-duration render does)
  5  _syncFinishAffordance(): one toolbar slot, dispatches on the
     selected clip's engine — H3 clip re-points onclick/onchange to the
     H3 functions, LTX clip to the LTX functions, anything else hides
     the control entirely
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
STATE = Path(tempfile.mkdtemp(prefix="phos-ltxfinish-"))
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8333")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mlx_ltx_panel as P  # noqa: E402
from extract_panel_js import extract_function  # noqa: E402

NODE = shutil.which("node")
QJS = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
EJS = (ROOT / "webapp" / "js" / "engines.js").read_text(encoding="utf-8")
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


# A minimal, real-shaped BOOT.ltx fixture: three qualities, one length, so
# the target-selection logic (which quality, never which length) is what's
# under test.
_BOOT_LTX_JS = r"""
const BOOT = { ltx: {
  qualities: [
    { key: 'quick', label: 'Quick', order: 0, width: 640, height: 448, pack: 'q4', pipeline: 'distilled', canvas: '640×448', delivered_canvas: '640×448' },
    { key: 'balanced', label: 'Balanced', order: 1, width: 1024, height: 576, pack: 'q4', pipeline: 'distilled', canvas: '1024×576', delivered_canvas: '1280×720' },
    { key: 'standard', label: 'Standard', order: 2, width: 1280, height: 704, pack: 'q4', pipeline: 'distilled', canvas: '1280×704', delivered_canvas: '1280×704' },
  ],
  lengths: [ { key: '5s', label: '5s', order: 1, seconds: 5, frames: 121, offered: true } ],
  tiers: [
    { key: 'quick_5s', quality: 'quick', quality_label: 'Quick', length: '5s', length_label: '5s', width: 640, height: 448, frames: 121, eta: '~2 min', eta_range: '~2 min', spec: '640×448 · 121f', delivered_spec: '640×448', delivered_differs: false, available: true, offered: true, label: 'Quick · 5s' },
    { key: 'balanced_5s', quality: 'balanced', quality_label: 'Balanced', length: '5s', length_label: '5s', width: 1024, height: 576, frames: 121, eta: '~3 min', eta_range: '~2-3 min', spec: '1024×576 · 121f', delivered_spec: '1280×720', delivered_differs: true, available: true, offered: true, label: 'Balanced · 5s' },
    { key: 'standard_5s', quality: 'standard', quality_label: 'Standard', length: '5s', length_label: '5s', width: 1280, height: 704, frames: 121, eta: '~4 min', eta_range: '~4 min', spec: '1280×704 · 121f', delivered_spec: '1280×704', delivered_differs: false, available: true, offered: true, label: 'Standard · 5s' },
  ],
} };
const _LS = {};
const localStorage = { getItem: (k) => (k in _LS ? _LS[k] : null), setItem: (k, v) => { _LS[k] = v; } };
function escapeHtml(s) { return String(s == null ? '' : s); }
"""


def _ltx_pure_fns() -> str:
    return "\n".join(extract_function(n, EJS if n == "ltxCellFor" else QJS)
                     for n in ("ltxCellFor", "ltxTierByKeyExact",
                              "_ltxLengthKeyForFrames", "ltxFinishTargets",
                              "ltxFinishTierKey", "ltxFinishFieldsFromSidecar"))


# ---------------------------------------------------------------- 1
class TestListOutputsLiftsLtxAxes(unittest.TestCase):
    def setUp(self):
        P.OUTPUT.mkdir(parents=True, exist_ok=True)

    def _fixture(self, name, params):
        clip = P.OUTPUT / name
        clip.write_bytes(b"x")
        sidecar = Path(str(clip) + ".json")
        sidecar.write_text(json.dumps({"params": params}))
        old = time.time() - 10
        os.utime(clip, (old, old))
        os.utime(sidecar, (old, old))
        self.addCleanup(lambda: (clip.unlink(missing_ok=True), sidecar.unlink(missing_ok=True)))
        return clip

    def _row(self, clip):
        outs = P.list_outputs()
        return next((o for o in outs if o["path"] == str(clip)), None)

    def test_lifts_quality_and_frames_for_a_plain_ltx_clip(self):
        clip = self._fixture(f"vc37_ltx_{id(self)}.mp4",
                             {"mode": "t2v", "quality": "balanced", "frames": 121,
                              "prompt": "p", "seed_used": 42})
        row = self._row(clip)
        self.assertIsNotNone(row)
        self.assertEqual(row["quality"], "balanced")
        self.assertEqual(row["frames"], 121)
        self.assertIsNone(row["h3_tier"])

    def test_explicit_ltx_engine_also_lifts(self):
        clip = self._fixture(f"vc37_ltx_explicit_{id(self)}.mp4",
                             {"mode": "i2v", "engine": "ltx", "quality": "standard",
                              "frames": 121, "prompt": "p"})
        row = self._row(clip)
        self.assertEqual(row["quality"], "standard")
        self.assertEqual(row["frames"], 121)

    def test_h3_clip_does_not_leak_a_quality_value(self):
        clip = self._fixture(f"vc37_h3_{id(self)}.mp4",
                             {"mode": "t2v", "engine": "h3", "h3_tier": "standard_5s",
                              "prompt": "p"})
        row = self._row(clip)
        self.assertIsNone(row["quality"])
        self.assertIsNone(row["frames"])
        self.assertIsNotNone(row["h3_tier"])

    def test_music_clip_does_not_leak_a_quality_value(self):
        clip = self._fixture(f"vc37_music_{id(self)}.wav",
                             {"engine": "music", "quality": "high"})
        Path(str(clip) + ".json").write_text(json.dumps(
            {"engine": "music", "params": {"engine": "music", "quality": "high"}}))
        row = self._row(clip)
        self.assertIsNone(row["quality"])


# ---------------------------------------------------------------- 2 & 3
class TestFinishTargetsAndDefaultChoice(unittest.TestCase):
    def test_targets_are_every_quality_above_source_same_length(self):
        script = _BOOT_LTX_JS + _ltx_pure_fns() + """
const src = ltxCellFor('quick', '5s');
const targets = ltxFinishTargets(src);
console.log(JSON.stringify({ keys: targets.map(t => t.key) }));
"""
        out = _run_node(script)
        self.assertEqual(out["keys"], ["balanced_5s", "standard_5s"])

    def test_top_quality_has_no_targets(self):
        script = _BOOT_LTX_JS + _ltx_pure_fns() + """
const src = ltxCellFor('standard', '5s');
console.log(JSON.stringify({ n: ltxFinishTargets(src).length }));
"""
        out = _run_node(script)
        self.assertEqual(out["n"], 0)

    def test_default_choice_is_one_rung_up_not_the_most_expensive(self):
        script = _BOOT_LTX_JS + _ltx_pure_fns() + """
const src = ltxCellFor('quick', '5s');
console.log(JSON.stringify({ key: ltxFinishTierKey(src) }));
"""
        out = _run_node(script)
        self.assertEqual(out["key"], "balanced_5s")

    def test_persisted_choice_wins_when_still_valid(self):
        script = _BOOT_LTX_JS + _ltx_pure_fns() + """
localStorage.setItem('phos_ltx_finish_quality', 'standard');
const src = ltxCellFor('quick', '5s');
console.log(JSON.stringify({ key: ltxFinishTierKey(src) }));
"""
        out = _run_node(script)
        self.assertEqual(out["key"], "standard_5s")

    def test_persisted_choice_below_source_is_ignored(self):
        script = _BOOT_LTX_JS + _ltx_pure_fns() + """
localStorage.setItem('phos_ltx_finish_quality', 'quick');
const src = ltxCellFor('balanced', '5s');
console.log(JSON.stringify({ key: ltxFinishTierKey(src) }));
"""
        out = _run_node(script)
        self.assertEqual(out["key"], "standard_5s")   # falls back to the one rung up


# ---------------------------------------------------------------- 4
class TestFieldsFromSidecar(unittest.TestCase):
    def test_carries_seed_used_not_the_submitted_random(self):
        script = _BOOT_LTX_JS + _ltx_pure_fns() + """
const p = { mode: 't2v', prompt: 'a cat', seed: '-1', seed_used: 424242,
            upscale: 'fit_720p', accel: 'turbo', loras: [{path:'/l.safetensors', strength: 0.8}] };
console.log(JSON.stringify(ltxFinishFieldsFromSidecar(p, 'standard_5s')));
"""
        out = _run_node(script)
        self.assertEqual(out["seed"], "424242")
        self.assertEqual(out["quality"], "standard")
        self.assertEqual(out["width"], 1280)
        self.assertEqual(out["upscale"], "fit_720p")
        self.assertEqual(out["accel"], "turbo")
        self.assertEqual(out["loras"][0]["path"], "/l.safetensors")

    def test_refuses_an_h3_sidecar(self):
        script = _BOOT_LTX_JS + _ltx_pure_fns() + """
const p = { mode: 't2v', engine: 'h3', prompt: 'a cat' };
console.log(JSON.stringify({ out: ltxFinishFieldsFromSidecar(p, 'standard_5s') }));
"""
        out = _run_node(script)
        self.assertIsNone(out["out"])

    def test_refuses_i2v_with_no_image(self):
        script = _BOOT_LTX_JS + _ltx_pure_fns() + """
const p = { mode: 'i2v', prompt: 'a cat' };
console.log(JSON.stringify({ out: ltxFinishFieldsFromSidecar(p, 'standard_5s') }));
"""
        out = _run_node(script)
        self.assertIsNone(out["out"])

    def test_i2v_with_image_carries_it(self):
        script = _BOOT_LTX_JS + _ltx_pure_fns() + """
const p = { mode: 'i2v', prompt: 'a cat', image: '/up/ref.png' };
console.log(JSON.stringify(ltxFinishFieldsFromSidecar(p, 'standard_5s')));
"""
        out = _run_node(script)
        self.assertEqual(out["image"], "/up/ref.png")
        self.assertEqual(out["mode"], "i2v")

    def test_refuses_an_unknown_tier_key(self):
        script = _BOOT_LTX_JS + _ltx_pure_fns() + """
const p = { mode: 't2v', prompt: 'a cat' };
console.log(JSON.stringify({ out: ltxFinishFieldsFromSidecar(p, 'nope_5s') }));
"""
        out = _run_node(script)
        self.assertIsNone(out["out"])


# ---------------------------------------------------------------- 5
class TestSharedDispatcher(unittest.TestCase):
    def test_h3_branch_repoints_the_shared_handlers_and_delegates(self):
        fn = extract_function("_syncFinishAffordance", QJS)
        i = fn.index("if (o && o.engine === 'h3')")
        block = fn[i:fn.index("return;", i) + 8]
        self.assertIn("btn.onclick = h3FinishActive", block)
        self.assertIn("sel.onchange = () => h3FinishSetTier(sel.value)", block)
        self.assertIn("_syncH3FinishAffordance(o)", block)

    def test_ltx_branch_tried_next_then_hides(self):
        fn = extract_function("_syncFinishAffordance", QJS)
        self.assertIn("if (_syncLtxFinishAffordance(o)) return;", fn)
        self.assertIn("wrap.style.display = 'none'", fn)

    def test_ltx_sync_repoints_handlers_too(self):
        fn = extract_function("_syncLtxFinishAffordance", QJS)
        self.assertIn("btn.onclick = ltxFinishActive", fn)
        self.assertIn("sel.onchange = () => ltxFinishSetTier(sel.value)", fn)

    def test_both_real_call_sites_use_the_dispatcher_not_the_h3_only_function(self):
        # The two selection-path call sites must route through the shared
        # dispatcher — calling _syncH3FinishAffordance directly from either
        # would silently drop LTX clips from ever showing Finish.
        for marker in ("_syncFinishAffordance(findOutputByPath(activePath))",
                       "_syncFinishAffordance(isPhoto ? null : o)"):
            self.assertIn(marker, QJS, marker)

    def test_toolbar_markup_mentions_both_engines_now(self):
        i = HTML.index('id="h3FinishWrap"')
        blk = HTML[i - 1400:i]
        self.assertIn("BOTH", blk)
        self.assertIn("VC-18/37", blk)


if __name__ == "__main__":
    unittest.main()
