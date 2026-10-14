#!/usr/bin/env python3
"""4.17.0 Codex review (one round, seven parallel passes) — every real finding
fixed gets its pin here. Findings: ~/AI/projects/phosphene/review-2026-09-29-ux/
codex/findings_<area>.md. JS is executed in node where it is behaviour.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mlx_ltx_panel as p                                             # noqa: E402
import storyboard as sb                                               # noqa: E402
from extract_panel_js import extract_function                         # noqa: E402

routes_queue = sys.modules["panel.routes_queue"]
NODE = shutil.which("node")
JS = ROOT / "webapp" / "js"


def _node(script: str):
    if NODE is None:
        pytest.skip("node not on PATH")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        r = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr[-3000:]
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        Path(path).unlink(missing_ok=True)


class _H:
    def __init__(self):
        self.status, self.payload = None, None

    def _json(self, payload, status=200):
        self.payload, self.status = payload, status


# ---- H3-1: "Stop after this window" is not offered on a plain H3 chain -----
def test_h3_1_plain_chain_stop_after_window_is_refused_until_the_runner_keeps_windows():
    assert p.H3_RUNNER_KEEPS_WINDOWS_ON_STOP is False
    job = {"id": "j1", "params": {"engine": "h3"}, "progress": {"window_total": 3}}
    with p.LOCK:
        prev = p.STATE.get("current")
        p.STATE["current"] = job
    try:
        h = _H()
        routes_queue.post_stop_after_part(h, "/stop/after_part", {}, "")
        assert h.status == 409 and "stop_after_part" not in job
        assert "finished windows" in h.payload["error"]
    finally:
        with p.LOCK:
            p.STATE["current"] = prev
    js = (JS / "queue.js").read_text(encoding="utf-8")
    fn = js[js.index("function requestStop(opts) {"):]
    assert "h3_stop_after_window" in fn[:fn.index("\n}\n")]
    assert '"h3_stop_after_window": bool(H3_RUNNER_KEEPS_WINDOWS_ON_STOP)' in \
        (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")


# ---- H3-2: complete per-window prompts are not prefixed with window 1 -------
def test_h3_2_complete_window_prompts_replace_typed_beats_add():
    beats = ['She says <d>[English] Hello.</d>', "She silently waves goodbye.", "She exits."]
    got = p.h3_window_prompts_for_job(beats[0], beats, 3, complete=True)
    assert got == beats
    assert all("Hello" not in w for w in got[1:])
    # a person's typed boxes still ADD to the main prompt (H3-06)
    assert p.h3_window_prompts_for_job("A cat.", ["", "It jumps."], 2) == ["A cat.", "A cat. It jumps."]
    # blanks fall back to the main prompt either way
    assert p.h3_window_prompts_for_job("M", ["", "x"], 3, complete=True) == ["M", "x", "M"]


def test_h3_2_every_complete_caller_marks_its_prompts():
    src = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
    assert 'cp["h3_chain_prompts_complete"] = True' in src          # One Shot parts
    assert 'form["h3_chain_prompts_complete"] = ["1"]' in src         # One Shot parent job
    job = p.make_job({"mode": "t2v", "engine": "h3", "prompt": "x",
                      "h3_chain_prompts_complete": "1"})
    assert job["params"]["h3_chain_prompts_complete"] is True        # make_job allowlist
    assert 'job["h3_chain_prompts_complete"] = "1"' in (ROOT / "storyboard.py").read_text(encoding="utf-8")


# ---- H3-3: Finish and Load Params keep the saved crop framing --------------
def test_h3_3_finish_fields_carry_the_crop_focus():
    src = (JS / "queue.js").read_text(encoding="utf-8")
    deps = "global.h3TierByKeyExact = () => ({ key: 'high_5s', quality: 'high', length: '5s' });\n" \
           "global._h3FinishSteps = () => 0;\n"
    fn = extract_function("h3FinishFieldsFromSidecar", src)
    out = _node(deps + fn + """
const base = { engine: 'h3', mode: 'i2v', image: '/u/a.png', prompt: 'x' };
console.log(JSON.stringify([
  h3FinishFieldsFromSidecar(Object.assign({}, base, { image_crop_focus: '0' }), 'high_5s').image_crop_focus,
  h3FinishFieldsFromSidecar(Object.assign({}, base, { image_crop_focus: 1 }), 'high_5s').image_crop_focus,
  h3FinishFieldsFromSidecar(base, 'high_5s').image_crop_focus]));
""")
    assert out == [0, 1, 0.5]
    lp = extract_function("loadParams", src)
    assert "_setCropFocus('image', _cropFocusFromParams(p));" in lp
    assert src.count("_setCropFocus('image', fields.image_crop_focus);") == 2   # both Finish paths
    assert "_setCropFocus('a2v_image', _cropFocusFromParams(p));" in (JS / "characters.js").read_text(encoding="utf-8")


# ---- H3-4 / H3-5: self-calibration measures against the right price --------
@pytest.fixture
def calib(tmp_path, monkeypatch):
    path = tmp_path / "eta_calibration.json"
    monkeypatch.setattr(p, "_eta_calibration_path", lambda: path)
    monkeypatch.delenv("PHOSPHENE_SPEED_FACTOR", raising=False)
    monkeypatch.setattr(p, "H3_TIERS", {"draft_5s": {"eta_min": 10.0, "tristep_min": 4.0}})
    monkeypatch.setattr(p, "_ETA_CAL_PRICED", {"h3": 1.0, "ltx": 1.0})
    return path


def _h3_job(minutes, fast):
    return {"status": "done", "elapsed_sec": minutes * 60.0,
            "params": {"engine": "h3", "h3_tier": "draft_5s", "h3_tristep": fast}}


def test_h3_4_fast_and_best_each_calibrate_against_their_own_price(calib):
    p._record_eta_calibration_from_job(_h3_job(4.0, True))      # Fast, exactly as priced
    p._record_eta_calibration_from_job(_h3_job(10.0, False))    # Best, exactly as priced
    assert json.loads(calib.read_text())["h3"] == [1.0, 1.0]


def test_h3_5_identical_renders_record_identical_ratios(calib):
    for _ in range(4):
        p._record_eta_calibration_from_job(_h3_job(20.0, False))
    assert json.loads(calib.read_text())["h3"] == [2.0, 2.0, 2.0, 2.0]
    assert p._eta_calibration_factor("h3") == pytest.approx(2.0)


def test_h3_4_pinned_steps_and_one_shots_are_not_samples(calib):
    j = _h3_job(20.0, False); j["params"]["h3_steps"] = 12
    p._record_eta_calibration_from_job(j)
    j = _h3_job(20.0, False); j["params"]["take"] = {"seconds": 30}
    p._record_eta_calibration_from_job(j)
    assert not calib.exists()


# ---- H3-6 / H3-7: H3 disk preflight ---------------------------------------
PREFLIGHT = ROOT / "scripts" / "pinokio" / "h3_preflight.sh"
DIT_REL = "deepbeep-pruned-bf16/MiniMax-H3-FL2VA-pruned_bf16.safetensors"


def _preflight(tmp: Path, models: Path, avail_gb_for: dict):
    fb = tmp / "fakebin"
    fb.mkdir(exist_ok=True)
    cases = "".join(f'  "{k}"*) A={v};;\n' for k, v in avail_gb_for.items())
    (fb / "df").write_text(
        "#!/bin/sh\nfor last; do :; done\nA=500\ncase \"$last\" in\n" + cases + "esac\n"
        "echo 'Filesystem 1024-blocks Used Available Capacity Mounted'\n"
        "echo \"/dev/x 1 1 $((A*1024*1024)) 1% /\"\n")
    (fb / "sysctl").write_text("#!/bin/sh\necho 68719476736\n")
    for f in ("df", "sysctl"):
        (fb / f).chmod(0o755)
    env = dict(os.environ, PATH=f"{fb}:{os.environ['PATH']}", LTX_H3_MODELS=str(models))
    app = tmp / "app"; app.mkdir(exist_ok=True)
    return subprocess.run(["bash", str(PREFLIGHT)], cwd=str(app), env=env,
                          capture_output=True, text=True, timeout=15)


def test_h3_6_a_built_install_updates_with_little_free_space(tmp_path):
    models = tmp_path / "models"
    (models / DIT_REL).parent.mkdir(parents=True)
    (models / DIT_REL).write_bytes(b"x")
    (models / "h3-dit-q8").mkdir()
    (models / "h3-dit-q8" / ".built_ok").write_text("ok")
    r = _preflight(tmp_path, models, {str(models): 10})
    assert r.returncode == 0, r.stdout
    (models / "h3-dit-q8" / ".built_ok").unlink()                 # Q8 not built: needs the build room
    r = _preflight(tmp_path, models, {str(models): 10})
    assert r.returncode == 1 and "NEEDS ~25 GB" in r.stdout


def test_h3_7_the_weights_volume_is_what_is_measured(tmp_path):
    models = tmp_path / "ext" / "h3models"          # does not exist yet: nearest ancestor
    (tmp_path / "ext").mkdir()
    r = _preflight(tmp_path, models, {str(tmp_path / "ext"): 20})   # app volume roomy (500)
    assert r.returncode == 1 and "NEEDS ~97 GB" in r.stdout
    r = _preflight(tmp_path, models, {str(tmp_path / "ext"): 200})
    assert r.returncode == 0, r.stdout


# ---- H3-8 / H3-9: the prompt helper ---------------------------------------
def test_h3_8_dialogue_budget_is_one_window_not_one_per_tag():
    fn = extract_function("h3SyncPromptHelper", (JS / "engines.js").read_text(encoding="utf-8"))
    ten = " ".join(["word"] * 10)
    out = _node(fn + f"""
const els = {{ prompt: {{ value: 'A <d>[English] {ten}</d> and <d>[English] {ten}</d>' }},
              h3DialogueWordCount: {{ hidden: true, textContent: '', classList: {{ on: false, toggle(c, v) {{ this.on = v; }} }} }},
              h3QuoteWarning: null }};
global.document = {{ getElementById: id => els[id] || null }};
global.H3 = {{ speech_words_per_sec: 2.4 }};
global.h3CurrentCell = () => ({{ seconds: 5, chain_windows: 1 }});
h3SyncPromptHelper();
console.log(JSON.stringify([els.h3DialogueWordCount.textContent, els.h3DialogueWordCount.classList.on]));
""")
    assert out == ["20/12 dialogue words", True]


def test_h3_9_plus_line_selects_its_placeholder_at_the_start():
    fn = extract_function("h3InsertDialogueLine", (JS / "engines.js").read_text(encoding="utf-8"))
    out = _node(fn + """
const res = [];
for (const [val, at] of [['', 0], ['Hi there.', 9], ['Hi there.', 0]]) {
  const ta = { value: val, selectionStart: at, selectionEnd: at, sel: null,
               focus() {}, dispatchEvent() {}, setSelectionRange(a, b) { this.sel = [a, b]; } };
  global.document = { getElementById: id => id === 'prompt' ? ta : (id === 'h3DialogueLang' ? { value: 'English' } : null) };
  global.Event = function () {};
  h3InsertDialogueLine();
  res.push(ta.value.slice(ta.sel[0], ta.sel[1]));
}
console.log(JSON.stringify(res));
""")
    assert out == ["line here", "line here", "line here"]
