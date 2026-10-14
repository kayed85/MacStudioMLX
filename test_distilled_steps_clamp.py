"""4.17.4 fleet fix: a stray step count no longer fails a distilled render.

THE BUG (fleet, every version since 4.9): "steps=9 is above the 8-step
distilled schedule" — 113 failed renders in 30 days, one install at a time,
retried in loops (53 + 41 on one 4.15 install at steps=20, 5 on 4.17.3 at
steps=9). Also seen: 18, 30, and on the other side 1/4/5/6 ("below the 8-step
minimum"). The distilled LTX lane runs a fixed 9-point table, exactly 8
steps, and run_job_inner REFUSED any other count.

WHERE THE NUMBER CAME FROM. LTX and Hailuo H3 share ONE hidden #steps field.
H3 writes its tuned count (9) or a pinned Steps pill (12/16/20) into it, and
setEngine() back to LTX never put LTX's 8 back. Load Params of an H3 clip on
a Mac that lands on LTX restored it too (p.steps, then setEngine), and Retry
re-queues the job's own params, so the refusal repeated forever.

THE FIX, three layers, each tested here:
  1. make_job clamps every LTX distilled job to 8 with one plain note
     (generation_clamp_notes -> Queue row + Now card). The Params/sidecar
     restore path posts the sidecar's values back through /queue/add, i.e.
     through make_job: covered by the sidecar-shaped form below.
  2. run_job_inner clamps instead of refusing, for jobs written by an older
     panel, retries and API callers that never met make_job.
  3. setEngine() re-applies the LTX quality's step count on the way out of
     H3 (run in node against the real functions).
The helper also clamps (belt), asserted on its source.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")
os.environ.setdefault("PHOSPHENE_DISABLE_VERSION_CHECK", "1")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mlx_ltx_panel as P  # noqa: E402
from extract_panel_js import extract_function  # noqa: E402

NODE = shutil.which("node")


def _form(**extra) -> dict:
    form = {"mode": "t2v", "prompt": "a lighthouse keeper waves", "width": "1024",
            "height": "576", "frames": "121", "seed": "-1", "quality": "balanced",
            "accel": "off", "enhance": "off", "upscale": "off", "engine": "ltx"}
    form.update(extra)
    return form


class MakeJobClamps(unittest.TestCase):
    def test_fleet_counts_land_on_8_with_one_note(self):
        for steps in ("9", "20", "18", "30", "4", "1"):
            for quality in ("balanced", "standard", "quick"):
                with self.subTest(steps=steps, quality=quality):
                    job = P.make_job(_form(steps=steps, quality=quality))
                    p = job["params"]
                    self.assertEqual(p["steps"], 8)
                    notes = p.get("generation_clamp_notes") or []
                    self.assertEqual(sum("steps" in n for n in notes), 1, notes)
                    self.assertIn(f"steps {steps} -> 8", notes[0])

    def test_eight_carries_no_note(self):
        p = P.make_job(_form(steps="8"))["params"]
        self.assertEqual(p["steps"], 8)
        self.assertFalse(any("steps" in n for n in p.get("generation_clamp_notes") or []))

    def test_sidecar_restore_of_an_h3_count_onto_ltx(self):
        """The Params restore path: an i2v sidecar carrying H3's tuned 9 posted
        back to /queue/add on the LTX engine — the 4.17.3 Balanced i2v case."""
        img = Path(tempfile.mkdtemp(prefix="steps-")) / "ref.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")
        sidecar_params = {"mode": "i2v", "quality": "balanced", "steps": 9,
                          "width": 1344, "height": 768, "frames": 121,
                          "image": str(img), "engine": "ltx", "seed": "123",
                          "prompt": "she turns to the window"}
        form = {k: [str(v)] for k, v in sidecar_params.items()}
        p = P.make_job(form)["params"]
        self.assertEqual(p["steps"], 8)
        self.assertTrue(p.get("generation_clamp_notes"))

    def test_lanes_with_their_own_steps_are_untouched(self):
        # H3 legitimately runs 9..20 on the same field.
        self.assertIsNone(P.clamp_distilled_steps(
            {"engine": "h3", "mode": "i2v", "quality": "balanced", "steps": 20}))
        # The HQ lane's `steps` is display-only; it reads stage1/stage2.
        hq = next((k for k in P.LTX_QUALITIES if P.ltx_quality_uses_hq(k)), None)
        if hq:
            prm = {"engine": "ltx", "mode": "t2v", "quality": hq, "steps": 18}
            self.assertIsNone(P.clamp_distilled_steps(prm))
            self.assertEqual(prm["steps"], 18)
        for mode in ("extend", "keyframe", "a2v", "retake", "upscale"):
            prm = {"engine": "ltx", "mode": mode, "quality": "balanced", "steps": 12}
            self.assertIsNone(P.clamp_distilled_steps(prm))
            self.assertEqual(prm["steps"], 12)

    def test_garbage_counts_clamp_too(self):
        for raw in ("abc", None, "", "9.0"):
            prm = {"engine": "ltx", "mode": "t2v", "quality": "balanced", "steps": raw}
            P.clamp_distilled_steps(prm)
            self.assertEqual(prm["steps"], 8, raw)


class _Helper:
    ready_info: dict = {}

    def __init__(self):
        self.sent: list[dict] = []

    def is_alive(self):
        return True

    def kill(self, *a, **k):
        pass

    def run(self, spec, *a, **k):
        self.sent.append(spec)
        out = (spec.get("params") or {}).get("output_path")
        if out:
            Path(out).write_bytes(b"x")
        return {"seed_used": 1, "elapsed_sec": 0.1}


class RunJobInnerRendersInsteadOfRefusing(unittest.TestCase):
    """A job that never met this make_job (queued by 4.17.3, a Retry of its
    params, an API caller): it used to die on the refusal; now it reaches the
    helper at 8 steps."""

    def _run(self, steps):
        job = P.make_job(_form())
        job["params"]["steps"] = steps
        job["params"].pop("generation_clamp_notes", None)
        helper = _Helper()
        with mock.patch.object(P, "HELPER", helper), \
             mock.patch.object(P, "OUTPUT", Path(tempfile.mkdtemp(prefix="steps-run-"))), \
             mock.patch.object(P, "hq_surface_missing", lambda *a, **k: []), \
             mock.patch.object(P, "write_sidecar", lambda *a, **k: True), \
             mock.patch.object(P, "_gemma4_tower_supported", lambda *a, **k: True), \
             mock.patch.object(P, "ltx_pack_preflight", lambda *a, **k: None):
            P.run_job_inner(job)
        return helper, job

    def test_steps_9_and_20_render_at_8(self):
        for steps in (9, 20, 4):
            with self.subTest(steps=steps):
                helper, job = self._run(steps)
                self.assertEqual(len(helper.sent), 1)
                self.assertEqual(helper.sent[0]["params"]["steps"], 8)
                self.assertTrue(any("steps" in n for n in
                                    job["params"].get("generation_clamp_notes") or []))

    def test_the_refusals_are_gone(self):
        src = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
        self.assertNotIn("is above the 8-step distilled schedule", src)
        self.assertNotIn("is below the 8-step minimum", src)


class HelperBelt(unittest.TestCase):
    def test_helper_distilled_lane_clamps(self):
        src = (ROOT / "mlx_warm_helper.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        keep = [n for n in tree.body if (
            isinstance(n, ast.FunctionDef) and n.name == "_distilled_steps") or (
            isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id == "_DISTILLED_STEPS")]
        self.assertEqual(len(keep), 2)
        logged: list = []
        ns: dict = {"emit": logged.append}
        exec(compile(ast.Module(body=keep, type_ignores=[]), "helper", "exec"), ns)
        self.assertEqual(ns["_distilled_steps"](9), 8)
        self.assertEqual(ns["_distilled_steps"]("20"), 8)
        self.assertEqual(ns["_distilled_steps"](8), 8)
        self.assertEqual(len(logged), 2)
        self.assertIn("num_steps=_distilled_steps(p.get(\"steps\", 8))", src)


def _node(script: str):
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        r = subprocess.run([NODE, path], capture_output=True, text=True,
                           errors="replace", timeout=60)
    finally:
        Path(path).unlink(missing_ok=True)
    if r.returncode != 0:
        raise AssertionError(r.stderr[-2000:])
    return json.loads(r.stdout.strip().splitlines()[-1])


class SetEngineGivesLtxItsStepsBack(unittest.TestCase):
    """The real setEngine + applyQuality + _qualityUsesHq in node, with the
    DOM and the unrelated surface painters stubbed."""

    def _script(self, quality: str, steps_before: str, engine: str) -> str:
        q = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
        c = (ROOT / "webapp" / "js" / "characters.js").read_text(encoding="utf-8")
        fns = "\n".join([extract_function("setEngine", q),
                         extract_function("applyQuality", q),
                         extract_function("_qualityUsesHq", c)])
        return f"""
const els = {{}};
function el(id) {{ return els[id] || (els[id] = {{ id, value: '', hidden: false,
  textContent: '', classList: {{ toggle() {{}} }}, style: {{}} }}); }}
const document = {{ getElementById: el, body: {{ dataset: {{}} }},
  querySelectorAll: () => [], querySelector: () => null }};
const localStorage = {{ setItem() {{}}, getItem() {{ return null; }} }};
const BOOT = {{ ltx: {{ qualities: [{{ key: 'balanced', pipeline: 'distilled' }},
  {{ key: 'high', pipeline: 'hq' }}] }} }};
const ENGINES = [{{ id: 'ltx', builtin: true }}, {{ id: 'h3', builtin: true }}];
const H3_ENGINE_LS_KEY = 'k'; let currentMode = 't2v';
const QUALITY_PRESETS = {{ balanced: {{ upscale: 'fit_720p' }}, high: {{ upscale: 'off' }} }};
function engineById(id) {{ return ENGINES.find(e => e.id === id); }}
function defaultEngine() {{ return ENGINES[0]; }}
function renderEngineSwitch() {{}} function syncModeStripToEngine() {{}}
function snapFramesTo8kPlus1() {{}} function setUpscale() {{}}
function renderTierAxes() {{}} function _applyCharacterQualityStripVisibility() {{}}
function updateCustomizeSummary() {{}} function updateDerived() {{}}
function _syncEnginePromptTools() {{}} function _syncLoraPickerForEngine() {{}}
{fns}
el('quality').value = {json.dumps(quality)};
el('steps').value = {json.dumps(steps_before)};
setEngine({json.dumps(engine)}, {{ persist: false }});
console.log(JSON.stringify([String(el('steps').value)]));
"""

    def test_back_from_h3_restores_8(self):
        for before in ("9", "12", "16", "20"):
            (steps,) = _node(self._script("balanced", before, "ltx"))
            self.assertEqual(steps, "8", before)

    def test_back_from_h3_on_high_restores_its_own_count(self):
        (steps,) = _node(self._script("high", "9", "ltx"))
        self.assertEqual(steps, "18")

    def test_load_params_lands_the_engine_after_restoring_steps(self):
        """Load Params writes p.steps, THEN calls setEngine(): the order is what
        lets setEngine's LTX branch overwrite a restored H3 count."""
        src = extract_function("loadParams", (ROOT / "webapp" / "js" / "queue.js")
                               .read_text(encoding="utf-8"))
        i_steps = src.index("document.getElementById('steps').value = p.steps")
        i_eng = src.index("setEngine(_eng, { persist: false })")
        self.assertLess(i_steps, i_eng)


if __name__ == "__main__":
    unittest.main()
