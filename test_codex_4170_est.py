#!/usr/bin/env python3
"""4.17.0 Codex review — the estimates + sizes area (findings_est.md). Every
real finding fixed gets its pin here; JS is executed in node where it is
behaviour. Findings: ~/AI/projects/phosphene/review-2026-09-29-ux/codex/
findings_est.md.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mlx_ltx_panel as p                                             # noqa: E402
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


class _Parsed:
    def __init__(self, query: str):
        self.query = query


@pytest.fixture
def calib(tmp_path, monkeypatch):
    path = tmp_path / "eta_calibration.json"
    monkeypatch.setattr(p, "_eta_calibration_path", lambda: path)
    monkeypatch.delenv("PHOSPHENE_SPEED_FACTOR", raising=False)
    monkeypatch.setattr(p, "_ETA_CAL_PRICED", {"h3": 1.0, "ltx": 1.0})
    return path


def _ltx_job(minutes, **params):
    base = {"engine": "ltx", "mode": "t2v", "quality": "balanced",
            "frames": p.LTX_TIERS["balanced_5s"]["frames"],
            "width": p.LTX_TIERS["balanced_5s"]["width"],
            "height": p.LTX_TIERS["balanced_5s"]["height"]}
    base.update(params)
    return {"status": "done", "elapsed_sec": minutes * 60.0, "params": base}


# ---- EST-2: calibration prices the LTX recipe that actually ran -------------
def test_est_2_ltx_fast_draft_and_default_each_calibrate_against_their_own_price(calib):
    cell = p.LTX_TIERS["balanced_5s"]
    if cell.get("fast_min") is None:
        pytest.skip("this registry has no Fast draft schedule")
    p._record_eta_calibration_from_job(_ltx_job(cell["fast_min"], schedule_preset="fast"))
    p._record_eta_calibration_from_job(_ltx_job(cell["eta_min"]))
    assert json.loads(calib.read_text())["ltx"] == [1.0, 1.0]


def test_est_2_other_recipes_do_not_contaminate_t2v_calibration(calib):
    slow = p.LTX_TIERS["balanced_5s"]["eta_min"] * 3
    for extra in ({"mode": "a2v"}, {"mode": "extend"}, {"mode": "keyframe"},
                  {"mode": "retake"}, {"width": 768, "height": 768},     # a 1:1 canvas
                  {"long_mode": "windows"}, {"temporal_mode": "fps12_interp24"},
                  {"upscale": "x2"}):
        p._record_eta_calibration_from_job(_ltx_job(slow, **extra))
    assert not calib.exists()
    # Portrait is the cell's own canvas turned — same pixels, same price.
    w, h = p.LTX_TIERS["balanced_5s"]["width"], p.LTX_TIERS["balanced_5s"]["height"]
    p._record_eta_calibration_from_job(_ltx_job(p.LTX_TIERS["balanced_5s"]["eta_min"],
                                                width=h, height=w))
    assert json.loads(calib.read_text())["ltx"] == [1.0]


# ---- EST-5: a queued job's ETA is the price of its own recipe ---------------
def test_est_5_queue_eta_prices_h3_fast_and_turbo_by_their_own_numbers():
    key, cell = next((k, c) for k, c in p.H3_TIERS.items() if c.get("tristep_min") is not None)
    base = {"engine": "h3", "mode": "t2v", "h3_tier": key}
    assert p.job_priced_eta_sec(base) == pytest.approx(cell["eta_min"] * 60)
    assert p.job_priced_eta_sec({**base, "h3_tristep": True}) == pytest.approx(cell["tristep_min"] * 60)
    assert p.job_priced_eta_sec({**base, "h3_turbo": True}) == pytest.approx(cell["turbo_min"] * 60)
    if cell.get("tristep_min_i2v") is not None:
        assert p.job_priced_eta_sec({**base, "mode": "i2v", "h3_tristep": True}) == \
            pytest.approx(cell["tristep_min_i2v"] * 60)
    win = max(1, cell["chain_windows"])
    pinned = (win * 11 * cell["per_forward_sec"] + win * cell["fixed_sec"])
    assert p.job_priced_eta_sec({**base, "h3_steps": 12}) == pytest.approx(pinned)


def test_est_5_queue_eta_prices_ltx_schedule_and_modes_like_their_pre_submit_price():
    cell = p.LTX_TIERS["balanced_5s"]
    t2v = {"engine": "ltx", "mode": "t2v", "quality": "balanced", "frames": cell["frames"],
           "width": cell["width"], "height": cell["height"]}
    assert p.job_priced_eta_sec(t2v) == pytest.approx(cell["eta_min"] * 60)
    if cell.get("fast_min") is not None:
        assert p.job_priced_eta_sec({**t2v, "schedule_preset": "fast"}) == \
            pytest.approx(cell["fast_min"] * 60)
    got = {}
    if p.SYSTEM_CAPS.get("allows_extend"):
        card = p.ltx_mode_price_card("extend", steps=30, extend_frames=12)
        got["extend"] = p.job_priced_eta_sec({**t2v, "mode": "extend",
                                              "extend_steps": 30, "extend_frames": 12})
        assert got["extend"] == pytest.approx(card["eta_min"] * 60)
    if p.SYSTEM_CAPS.get("allows_keyframe"):
        card = p.ltx_mode_price_card("keyframe", frames=241, width=cell["width"],
                                     height=cell["height"])
        got["keyframe"] = p.job_priced_eta_sec({**t2v, "mode": "keyframe", "frames": 241})
        assert got["keyframe"] == pytest.approx(card["eta_min"] * 60)
    a2v = p.ltx_a2v_price(cell["frames"], cell["width"], cell["height"])
    got["a2v"] = p.job_priced_eta_sec({**t2v, "mode": "a2v"})
    assert got["a2v"] == pytest.approx(a2v["minutes"] * 60)
    # None of them is the default T2V cell's price any more.
    assert all(abs(v - cell["eta_min"] * 60) > 1 for v in got.values())


def test_est_5_a2v_route_and_queue_share_one_price():
    h = _H()
    routes_queue.get_a2v_estimate(h, _Parsed("frames=121&width=1024&height=576"))
    assert h.payload["ok"] is True
    assert h.payload["minutes"] == p.ltx_a2v_price(121, 1024, 576)["minutes"]


# ---- EST-7: Keyframe is priced for the duration and canvas asked for -------
@pytest.fixture
def keyframe_on(monkeypatch):
    monkeypatch.setitem(p.SYSTEM_CAPS, "allows_keyframe", True)


def test_est_7_keyframe_card_follows_frames_and_canvas(keyframe_on):
    five = p.ltx_mode_price_card("keyframe")
    ten = p.ltx_mode_price_card("keyframe", frames=241)
    assert five["frames"] == 121 and ten["frames"] == 241
    assert ten["eta_min"] > five["eta_min"]
    wide = p.ltx_mode_price_card("keyframe", width=640, height=448)
    assert (wide["width"], wide["height"]) == p.ltx_keyframe_estimate_minutes(121, 640, 448)[:2]
    assert wide["eta_min"] < five["eta_min"]
    h = _H()
    routes_queue.get_keyframe_estimate(h, _Parsed("frames=241&width=1024&height=576"))
    assert h.payload["ok"] and h.payload["eta_min"] == \
        p.ltx_mode_price_card("keyframe", frames=241, width=1024, height=576)["eta_min"]


def test_est_7_footer_and_summary_reprice_when_the_duration_changes():
    q = (JS / "queue.js").read_text(encoding="utf-8")
    c = (JS / "characters.js").read_text(encoding="utf-8")
    fns = "\n".join([extract_function("keyframePriceFor", q),
                     extract_function("updateDerivedForClampedMode", q),
                     extract_function("updateShotSetupSummary", c)])
    out = _node("""
const _kfPriceCache = {}; let _kfPriceSeq = 0; let _kfPriceTimer = null;
const els = {};
const el = (id, v) => (els[id] = els[id] || { id, value: v, innerHTML: '', textContent: '' });
el('mode', 'keyframe'); el('frames', '121'); el('width', '1024'); el('height', '576');
el('derivedFooter'); el('qualityMeta'); el('shotSetupSummary'); el('seed', '-1');
global.document = { getElementById: id => els[id] || null, body: { dataset: { engine: 'ltx' } } };
global.escapeHtml = s => String(s);
let currentMode = 'keyframe';
const BOOT = { tier: { keyframe_price: { width: 768, height: 448, frames: 121,
  eta: '~8–10 min', pipeline_note: 'Q8 two-stage' } } };
const asked = [];
global.fetch = async (url) => { asked.push(url);
  const f = /frames=(\\d+)/.exec(url)[1];
  return { json: async () => ({ ok: true, width: 768, height: 448, frames: +f,
    eta: f === '241' ? '~17–21 min' : '~8–10 min', pipeline_note: 'Q8 two-stage' }) }; };
""" + fns + """
const wait = () => new Promise(r => setTimeout(r, 220));
(async () => {
  const res = {};
  updateDerivedForClampedMode('keyframe'); await wait();
  res.five = [els.derivedFooter.innerHTML, els.shotSetupSummary.textContent];
  els.frames.value = '241';
  updateDerivedForClampedMode('keyframe');
  res.pending = els.derivedFooter.innerHTML;
  await wait();
  res.ten = [els.derivedFooter.innerHTML, els.shotSetupSummary.textContent];
  res.asked = asked;
  console.log(JSON.stringify(res));
})();
""")
    assert "~8–10 min" in out["five"][0] and "~8–10 min" in out["five"][1]
    assert "pricing" in out["pending"] and "~8–10" not in out["pending"]
    assert "~17–21 min" in out["ten"][0] and "~17–21 min" in out["ten"][1]
    assert any("frames=241" in u for u in out["asked"])


# ---- EST-3: fleet ranges calibrate against the fleet, not the cost model ----
def _fleet_table(wall_sec=None):
    t = {"schema": "fleet_timings/1", "levels": {"cell": {"cells": {
        "ltx|t2v|balanced|121.0|M4 Max|64.0|any":
            {"p25_sec": 220.0, "p50_sec": 244.0, "p75_sec": 270.0, "n": 50},
        "h3|t2v|standard_5s|124.0|M4 Max|64.0|best":
            {"p25_sec": 600.0, "p50_sec": 600.0, "p75_sec": 600.0, "n": 7},
    }}}}
    if wall_sec:
        t["wall_sec"] = wall_sec
    return t


def test_est_3_renders_matching_the_fleet_leave_the_fleet_range_alone(calib, monkeypatch):
    monkeypatch.setattr(p, "FLEET_TIMINGS", _fleet_table("seconds"))
    monkeypatch.setattr(p, "_hw_chip_family", lambda: "M4 Max")
    monkeypatch.setattr(p, "SYSTEM_RAM_GB", 64.0)
    cell = p.LTX_TIERS["balanced_5s"]
    fleet_p50 = 244.0 / 60.0
    # Local renders take exactly the fleet median, i.e. 1.5x the model.
    monkeypatch.setitem(cell, "eta_min", round(fleet_p50 / 1.5, 4))
    for _ in range(3):
        p._record_eta_calibration_from_job(_ltx_job(fleet_p50))
    assert p._eta_calibration_factor("ltx") == pytest.approx(1.5, abs=0.01)   # model learns
    r = p.fleet_calibrated_range("ltx", "t2v", "balanced", 121)
    assert r["p50_min"] == pytest.approx(fleet_p50, abs=0.01)                  # fleet stays


def test_est_3_a_mac_slower_than_its_fleet_moves_only_the_fleet_factor(calib, monkeypatch):
    monkeypatch.setattr(p, "FLEET_TIMINGS", _fleet_table("seconds"))
    monkeypatch.setattr(p, "_hw_chip_family", lambda: "M4 Max")
    monkeypatch.setattr(p, "SYSTEM_RAM_GB", 64.0)
    fleet_p50 = 244.0 / 60.0
    monkeypatch.setitem(p.LTX_TIERS["balanced_5s"], "eta_min", round(fleet_p50 * 2, 4))
    for _ in range(2):
        p._record_eta_calibration_from_job(_ltx_job(fleet_p50 * 2))
    assert p._eta_calibration_factor("ltx") == pytest.approx(1.0, abs=0.01)
    r = p.fleet_calibrated_range("ltx", "t2v", "balanced", 121)
    assert r["p50_min"] == pytest.approx(fleet_p50 * 2, abs=0.02)
    assert "adjusted for this Mac's own renders" in r["basis"]


# ---- EST-4: a fleet cell keeps its timing bucket's width --------------------
def test_est_4_one_bucket_cell_reads_as_that_bucket_not_its_lower_edge(monkeypatch):
    monkeypatch.setattr(p, "FLEET_TIMINGS", _fleet_table())        # as built: lower edges
    r = p.fleet_estimate_range("h3", "t2v", "standard_5s", 124, chip="M4 Max",
                               ram=64.0, speed="best")
    assert (r["p25_min"], r["p50_min"], r["p75_min"]) == (10.0, 12.5, 15.0)
    assert p._fmt_eta_range(r["p25_min"], r["p75_min"]) == "~10–15 min"
    # A render just under the rung's top reports the rung's lower edge ...
    assert p._analytics_wall_sec_bucket(1199) == 900
    # ... and the displayed range still contains it.
    lo, hi = p._wall_bucket_bounds(900)
    assert lo <= 1199 / 1.0 and 1199 <= hi == 1200


def test_est_4_the_reader_and_the_sender_share_one_ladder():
    assert p._ANALYTICS_WALL_LADDER is p.FLEET_WALL_SEC_LADDER
    build = (ROOT / "scripts" / "fleet_timings_build.py").read_text(encoding="utf-8")
    assert '"wall_sec": "bucket_lower_edge"' in build
    shipped = json.loads((ROOT / "data" / "fleet_timings.json").read_text(encoding="utf-8"))
    assert shipped.get("wall_sec", "bucket_lower_edge") == "bucket_lower_edge"


# ---- EST-6: film time left counts the current shot and survives mixed queues -
def _film_time_left(*, current, queue, per_shot, shots):
    sb_js = (JS / "storyboard.js").read_text(encoding="utf-8")
    fns = "\n".join(extract_function(n, sb_js) for n in ("sbFmtWall", "_sbTagOf", "sbRenderRunBar"))
    return _node(f"""
globalThis.SB = {{ id: 'film1' }};
const _els = {{}};
function el(id) {{ return _els[id] || (_els[id] = {{ id, textContent: '', innerHTML: '', hidden: true }}); }}
const document = {{ getElementById: el }};
function sbEl(id) {{ return el(id); }}
function snippet(s, n) {{ return String(s || '').slice(0, n); }}
function escapeHtml(s) {{ return String(s || ''); }}
{fns}
const queue = {json.dumps(queue)};
const LAST_STATUS = {{ current: {json.dumps(current)}, queue, paused: false,
  eta_sec: queue.reduce((a, j) => a + (j.eta_sec || 0), 0) }};
sbRenderRunBar({{ rendering: true, pass: 'draft', board: {{ shots: {json.dumps(shots)} }},
                 per_shot_est: {json.dumps(per_shot)} }});
console.log(JSON.stringify({{ sub: el('sbRunSub').textContent, want: sbFmtWall(1500) }}));
""")


def _tag(n, eta=None, board="film1"):
    j = {"params": {"session_tag": f"sb:{board}#{n}"}}
    if eta is not None:
        j["eta_sec"] = eta
    return j


def test_est_6_the_current_shot_counts_toward_the_film_time_left():
    shots = [{"n": 1, "draft_output": None, "status": "rendering"},
             {"n": 2, "draft_output": None, "status": "pending"}]
    cur = {**_tag(1), "progress": {"remaining_sec": 1200}}
    out = _film_time_left(current=cur, queue=[], per_shot={"1": 1500, "2": 300}, shots=shots)
    assert out["sub"].endswith(f"{out['want']} left")          # 20 min + 5 min


def test_est_6_an_unrelated_queued_job_does_not_erase_the_films_queued_work():
    shots = [{"n": 1, "draft_output": "a.mp4", "status": "done"},
             {"n": 2, "draft_output": None, "status": "pending"},
             {"n": 3, "draft_output": None, "status": "pending"}]
    film_only = [_tag(2, 1200), _tag(3, 300)]
    mixed = film_only + [_tag(9, 4000, board="other")]
    a = _film_time_left(current=None, queue=film_only, per_shot={"2": 1200, "3": 300}, shots=shots)
    b = _film_time_left(current=None, queue=mixed, per_shot={"2": 1200, "3": 300}, shots=shots)
    assert a["sub"] == b["sub"] and a["sub"].endswith(f"{a['want']} left")


# ---- EST-8: moving the crop never opens the file chooser -------------------
def test_est_8_crop_clicks_do_not_browse_but_the_tile_still_does():
    fn = extract_function("pickerWire", (JS / "queue.js").read_text(encoding="utf-8"))
    out = _node(fn + """
const handlers = {};
const mk = (name) => ({ name, addEventListener(t, f) { (handlers[name + ':' + t] = f); } });
let browsed = 0;
const els = { drop: mk('drop'), file: { ...mk('file'), click() { browsed++; } },
              clear: mk('clear'), preview: mk('preview') };
function pickerEls() { return els; }
function _pickerCropSupported() { return true; }
function pickerUploadFile() {}
function _renderCropOverlay() {}
pickerWire('image');
// A target inside `sel` (and every ancestor selector it belongs to).
const target = (...inside) => ({ closest: s => s.split(',').some(x => inside.includes(x.trim())) ? {} : null });
const res = {};
for (const [name, t] of [['overlay', target('.crop-overlay')],
                         ['window', target('.crop-overlay')],
                         ['quick', target('.crop-overlay-controls')],
                         ['clear', target('.picker-clear')],
                         ['tile', target()]]) {
  browsed = 0; handlers['drop:click']({ target: t }); res[name] = browsed;
}
console.log(JSON.stringify(res));
""")
    assert out == {"overlay": 0, "window": 0, "quick": 0, "clear": 0, "tile": 1}


# ---- EST-9: the sidecar keeps the ORIGINAL reference, the engine the crop ---
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


def _fake_ffmpeg(cmd, *a, **k):
    Path(cmd[-1]).write_bytes(b"enc")
    return "", ""


@pytest.mark.parametrize("mode", ["i2v", "a2v"])
def test_est_9_load_params_reopens_the_original_upload_not_the_crop(tmp_path, mode):
    from unittest import mock
    from PIL import Image
    portrait = tmp_path / "portrait.png"
    Image.new("RGB", (360, 640), (200, 30, 30)).save(portrait)
    wav = tmp_path / "song.wav"
    wav.write_bytes(b"RIFF0000WAVE")
    form = {"mode": mode, "prompt": "a singer", "image": str(portrait), "quality": "balanced",
            "width": "1024", "height": "576", "frames": "49", "image_crop_focus": "0.2"}
    if mode == "a2v":
        form["audio"] = str(wav)
    helper, sidecars = _Helper(), []
    job = p.make_job(form)
    job["started_ts"] = __import__("time").time()
    with mock.patch.object(p, "HELPER", helper), \
         mock.patch.object(p, "OUTPUT", tmp_path), \
         mock.patch.object(p, "SYSTEM_TIER", "pro"), \
         mock.patch.object(p, "SYSTEM_CAPS", p.CAPABILITIES["pro"]), \
         mock.patch.object(p, "GENERATION_PROFILE", p._select_generation_profile(128.0, "pro")), \
         mock.patch.object(p, "ltx_pack_preflight", lambda *a, **k: None), \
         mock.patch.object(p, "hq_surface_missing", lambda *a, **k: []), \
         mock.patch.object(p, "run_ffmpeg_tracked", _fake_ffmpeg), \
         mock.patch.object(p, "set_hidden", lambda *a, **k: None), \
         mock.patch.object(p, "write_sidecar", lambda _p, data: sidecars.append(data) or True):
        p.run_job_inner(job)
    engine_image = helper.sent[0]["params"]["image"]
    assert engine_image and Path(engine_image) != portrait              # the crop rendered
    with Image.open(engine_image) as im:
        assert im.size[0] > im.size[1]                                 # landscape canvas
    assert sidecars[-1]["params"]["image"] == str(portrait)            # the pick is kept
    assert job["params"]["image"] == str(portrait)
    assert sidecars[-1]["params"]["image_crop_focus"] in ("0.2", 0.2)


# ---- EST-10: an Extend's Face Fix follow-up never disappears silently -------
class _FormH(_H):
    def __init__(self, form):
        super().__init__()
        self._form = form

    def _read_form_body(self):
        return b"", self._form


def test_est_10_extend_asking_for_face_fix_without_the_adapter_is_refused_up_front(monkeypatch, tmp_path):
    monkeypatch.setattr(p, "face_fix_adapter_ready", lambda: False)
    src = tmp_path / "src.mp4"
    src.write_bytes(b"x")
    form = {"mode": ["extend"], "prompt": ["more"], "video_path": [str(src)],
            "extend_face_fix_after": ["1"]}
    before = list(p.STATE["queue"])
    h = _FormH(form)
    routes_queue.post_run(h, "/queue/add", {}, "")
    assert h.status == 400 and h.payload["code"] == "pack_missing"
    assert "Settings → Models" in h.payload["error"]
    assert p.STATE["queue"] == before
    # Without the follow-up the same Extend is accepted.
    form.pop("extend_face_fix_after")
    h = _FormH(form)
    monkeypatch.setattr(p, "persist_queue", lambda: None)
    routes_queue.post_run(h, "/queue/add", {}, "")
    try:
        assert h.status == 200 and h.payload["ok"]
    finally:
        with p.QUEUE_COND:
            p.STATE["queue"][:] = before


def test_est_10_a_follow_up_that_cannot_queue_stays_on_the_finished_job(monkeypatch):
    monkeypatch.setattr(p, "queue_face_fix", lambda path: {
        "ok": False, "code": "hardware_tier", "error": "clip.mp4 is already 1280×704."})
    job = {"status": "done", "output_path": "/x/clip.mp4",
           "params": {"mode": "extend", "extend_face_fix_after": True}}
    p._queue_extend_face_fix_after(job)
    assert "was not queued" in job["warning"] and "1280×704" in job["warning"]
    js = (JS / "queue.js").read_text(encoding="utf-8")
    assert "(j.warning || '')" in js            # the history row repaints for it


def test_est_10_the_submit_shows_the_servers_refusal_not_offline():
    fn = extract_function("api", (JS / "queue.js").read_text(encoding="utf-8"))
    out = _node(fn + """
global.fetch = async () => ({ ok: false, status: 400,
  json: async () => ({ error: 'needs the adapter', code: 'pack_missing' }) });
(async () => {
  try { await api('/queue/add', 'POST', null); }
  catch (e) { console.log(JSON.stringify({ msg: e.message, code: e.code, status: e.status })); }
})();
""")
    assert out == {"msg": "needs the adapter", "code": "pack_missing", "status": 400}
    js = (JS / "queue.js").read_text(encoding="utf-8")
    sub = js[js.index("document.getElementById('genForm').addEventListener('submit'"):]
    sub = sub[:sub.index("\n});\n")]
    assert "e.code === 'pack_missing'" in sub and "openModelsModal()" in sub


# ---- EST-11: every orientation preset survives normalization and the clamp --
@pytest.mark.parametrize("tier,ram", [("pro", 128.0), ("high", 96.0), ("standard", 48.0), ("base", 16.0)])
def test_est_11_social_presets_keep_their_exact_ratio_on_every_tier(monkeypatch, tier, ram):
    from unittest import mock
    caps = p.CAPABILITIES[tier]
    profile = p._select_generation_profile(ram, tier)
    with mock.patch.object(p, "SYSTEM_TIER", tier), \
         mock.patch.object(p, "SYSTEM_CAPS", caps), \
         mock.patch.object(p, "GENERATION_PROFILE", profile):
        presets = p.aspect_presets_for_this_mac()
        for key, (rw, rh) in (("square", (1, 1)), ("portrait_4_5", (4, 5))):
            a = presets[key]
            assert a["w"] * rh == a["h"] * rw, (tier, key, a)
            assert f"{a['w']}×{a['h']}" in a["label"]
            job = p.make_job({"mode": "t2v", "prompt": "x", "quality": "balanced",
                              "width": str(a["w"]), "height": str(a["h"]), "frames": "121"})
            got = (job["params"]["width"], job["params"]["height"])
            assert got == (a["w"], a["h"]), (tier, key, got)          # make_job's /64 floor
            p._apply_generation_profile_to_job(job)                   # the profile clamp
            got = (job["params"]["width"], job["params"]["height"])
            cap = int(caps.get("t2v_max_dim") or 0)                   # run_job_inner's clamp
            if cap and max(got) > cap:
                got = p.ltx_fit_canvas(*got, cap)
            assert got == (a["w"], a["h"]), (tier, key, got)


# ---- EST-13: a clamped canvas never claims another canvas's measurement -----
def _tiers_on(monkeypatch, tier):
    from unittest import mock
    monkeypatch.setenv("PHOSPHENE_SPEED_FACTOR", "1.0")        # neutral speed
    with mock.patch.object(p, "SYSTEM_CAPS", p.CAPABILITIES[tier]):
        quals = p._ltx_qualities()
        with mock.patch.object(p, "LTX_QUALITIES", quals), \
             mock.patch.object(p, "FLEET_TIMINGS", {}):
            return quals, p._build_ltx_tiers()


def test_est_13_compact_equal_recipes_get_equal_unmeasured_prices(monkeypatch):
    quals, tiers = _tiers_on(monkeypatch, "base")
    bal, std = tiers["balanced_5s"], tiers["standard_5s"]
    if (bal["width"], bal["height"]) != (std["width"], std["height"]):
        pytest.skip("this registry clamps Balanced and Standard to different canvases")
    assert (bal["width"], bal["height"]) != (quals["balanced"]["native_width"],
                                             quals["balanced"]["native_height"])
    assert bal["eta_measured"] is False and std["eta_measured"] is False
    assert bal["eta_min"] == pytest.approx(std["eta_min"])
    for key, cell in tiers.items():                              # every measured hit
        if cell["eta_measured"]:
            q = quals[cell["quality"]]
            assert (cell["width"], cell["height"]) == (q["native_width"], q["native_height"]), key


def test_est_13_uncapped_macs_keep_every_measured_row(monkeypatch):
    _, tiers = _tiers_on(monkeypatch, "pro")
    assert tiers["balanced_5s"]["eta_measured"] is True
    assert tiers["standard_5s"]["eta_measured"] is True
    assert tiers["balanced_5s"]["eta_min"] == pytest.approx(
        p.LTX_MEASURED_ETA[(p.ACTIVE_MODEL_VERSION, "balanced", "5s", "q4")][0])
