#!/usr/bin/env python3
"""4.17.0 Codex review — UI area (Video-tab UI + system / i18n / privacy).

Findings: ~/AI/projects/phosphene/review-2026-09-29-ux/codex/findings_ui.md
(UI-1..UI-10) + EST-12 (duplicate of UI-8). Every pin here fails on e724af7
and passes after the fix. JS is executed in node where it is behaviour.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mlx_ltx_panel as p                                             # noqa: E402
from extract_panel_js import extract_function                         # noqa: E402
from panel.routes import POST_ROUTES                                  # noqa: E402

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
    def __init__(self, body: str = ""):
        self.body, self.out = body, None

    def _read_form_body(self):
        from urllib.parse import parse_qs
        return self.body, parse_qs(self.body)

    def _json(self, obj, code=200):
        self.out = (code, obj)


# ---- UI-1: Sharp export only reads clips in outputs/ or uploads/ ----------
@pytest.fixture
def sharp_env(tmp_path):
    saved = (p.PIPERSR_UPSCALE_ENABLED, p.persist_queue)
    p.PIPERSR_UPSCALE_ENABLED = True
    p.persist_queue = lambda: None
    with p.QUEUE_COND:
        before = list(p.STATE["queue"])
    dirs = []
    for root in (p.OUTPUT, p.UPLOADS):
        root.mkdir(parents=True, exist_ok=True)
        dirs.append(Path(tempfile.mkdtemp(prefix="ui1-", dir=root)))
    outside = tmp_path / "private"
    outside.mkdir()
    yield dirs, outside
    for d in dirs:
        shutil.rmtree(d, ignore_errors=True)
    p.PIPERSR_UPSCALE_ENABLED, p.persist_queue = saved
    with p.QUEUE_COND:
        p.STATE["queue"] = before


def test_ui1_sharp_export_refuses_files_and_symlinks_outside_outputs_uploads(sharp_env):
    (out_dir, up_dir), outside = sharp_env
    secret = outside / "private.mp4"
    secret.write_bytes(b"\0" * 64)
    r = p.queue_sharp_export(str(secret))
    assert r["ok"] is False and "outputs or uploads" in r["error"]
    link = out_dir / "innocent.mp4"                     # a symlink in outputs/ escaping it
    link.symlink_to(secret)
    r = p.queue_sharp_export(str(link))
    assert r["ok"] is False and "outputs or uploads" in r["error"]
    h = _H(urlencode({"path": str(secret)}))
    POST_ROUTES["/queue/sharp_export"](h, "/queue/sharp_export", {}, "")
    assert h.out[0] == 400 and h.out[1]["ok"] is False
    with p.QUEUE_COND:
        assert not any((j.get("params") or {}).get("mode") == "sharp_export"
                       and "private" in str(j["params"].get("source_path"))
                       for j in p.STATE["queue"])


def test_ui1_sharp_export_accepts_clips_in_both_roots(sharp_env):
    (out_dir, up_dir), _ = sharp_env
    for d in (out_dir, up_dir):
        clip = d / "ok.mp4"
        clip.write_bytes(b"\0" * 64)
        r = p.queue_sharp_export(str(clip))
        assert r["ok"] is True, r


def test_ui1_sharp_export_revalidates_when_the_job_runs(sharp_env):
    _, outside = sharp_env
    secret = outside / "private.mp4"
    secret.write_bytes(b"\0" * 64)
    job = {"id": "j1", "params": {"mode": "sharp_export", "source_path": str(secret)},
           "started_at": None, "started_ts": None}
    ran = []
    with mock.patch.object(p, "run_pipersr_tracked", lambda *a, **k: ran.append(a)), \
            mock.patch.object(p, "_probe_video_dims", lambda path: (640, 480)):
        with pytest.raises(RuntimeError, match="outputs or uploads"):
            p.run_sharp_export_job_inner(job)
    assert ran == []


# ---- UI-2 / UI-3: LTX Finish keeps the source's mode, aspect and export ----
QJS = (JS / "queue.js").read_text(encoding="utf-8")
CJS = (JS / "characters.js").read_text(encoding="utf-8")
EJS = (JS / "engines.js").read_text(encoding="utf-8")

_FINISH_DEPS = """
const CELLS = {
  balanced_5s: { key: 'balanced_5s', quality: 'balanced', length: '5s', width: 1024, height: 576,
                 frames: 121, seconds: 5, label: 'Balanced · 5s', eta: '~3 min', available: true },
  standard_5s: { key: 'standard_5s', quality: 'standard', length: '5s', width: 1280, height: 704,
                 frames: 121, seconds: 5, label: 'Standard · 5s', eta: '~5 min', available: true },
};
global.ltxTierByKeyExact = k => CELLS[k] || null;
global.ltxCellFor = (q, l) => CELLS[q + '_' + l] || null;
global._ltxLengthKeyForFrames = f => (f === 121 ? '5s' : null);
global.ltxFinishTierKey = src => (src.quality === 'balanced' ? 'standard_5s' : null);
global.ltxFinishTargets = src => (src.quality === 'balanced' ? [CELLS.standard_5s] : []);
global.escapeHtml = s => String(s);
"""


def test_ui2_finish_refuses_every_mode_but_t2v_and_i2v():
    fns = "\n".join(extract_function(n, QJS) for n in
                    ("ltxFinishFieldsFromSidecar", "_ltxFinishableMode", "_syncLtxFinishAffordance"))
    out = _node(_FINISH_DEPS + fns + """
const els = {};
global.document = { getElementById: id => (els[id] = els[id] || { style: {}, dataset: {} }) };
global.ltxFinishActive = () => {}; global.ltxFinishSetTier = () => {};
const base = { engine: 'ltx', prompt: 'x', seed_used: 7, width: 1024, height: 576,
               image: '/u/a.png', start_image: '/u/a.png', end_image: '/u/b.png' };
const res = {};
for (const m of ['keyframe', 'extend', 'a2v', 'i2v_clean_audio', 'remix', 't2v', 'i2v', undefined]) {
  const f = ltxFinishFieldsFromSidecar(Object.assign({}, base, { mode: m }), 'standard_5s');
  const o = { engine: 'ltx', quality: 'balanced', frames: 121, mode: m };
  res[String(m)] = [f ? f.mode : null, _syncLtxFinishAffordance(o)];
}
console.log(JSON.stringify(res));
""")
    for m in ("keyframe", "extend", "a2v", "i2v_clean_audio", "remix"):
        assert out[m] == [None, False], (m, out[m])
    assert out["t2v"] == ["t2v", True]
    assert out["i2v"] == ["i2v", True]
    assert out["undefined"] == ["t2v", True]        # old sidecars with no mode = t2v


def _finish_harness(sidecar: dict, form_aspect: str) -> dict:
    fns = "\n".join([
        extract_function("ltxFinishFieldsFromSidecar", QJS),
        extract_function("_ltxFinishableMode", QJS),
        extract_function("ltxFinishActive", QJS),
        extract_function("applyAspect", QJS),
        extract_function("_aspectDims", QJS),
        extract_function("setQuality", CJS),
        extract_function("setAspect", CJS),
        extract_function("setUpscale", CJS),
        extract_function("setUpscaleMethod", CJS),
        extract_function("_ltxApplyShape", EJS),
    ])
    return _node(_FINISH_DEPS + """
const els = {};
const el = id => (els[id] = els[id] || { id, value: '', style: {}, dataset: {}, classList: { toggle() {} } });
global.document = {
  getElementById: el, querySelectorAll: () => [], querySelector: () => null,
  body: { dataset: { engine: 'ltx' } },
};
global.window = global;
global.ASPECTS = { landscape: { w: 1280, h: 704 }, vertical: { w: 704, h: 1280 },
                   square: { w: 768, h: 768 }, portrait_4_5: { w: 896, h: 1120 } };
global.QUALITY_PRESETS = {
  quick: { w: 640, h: 448, upscale: 'off' }, balanced: { w: 1024, h: 576, upscale: 'fit_720p' },
  standard: { w: 1280, h: 704, upscale: 'off' }, high: { w: 1024, h: 576, upscale: 'off' } };
global.PIPERSR_UPSCALE_ENABLED = true;
global.LAST_STATUS = null;
for (const n of ['applyQuality', 'updateAccelAvailability', 'updateTemporalAvailability',
                 'updateCustomizeSummary', 'updateDerived', 'renderTierAxes', 'workflowSwitch',
                 'setMode', '_restoreLoraPicker', '_syncSeedLockChip', 'syncAvoidRowFromValue',
                 'phosToast', 'pickerSetImage', '_setCropFocus']) global[n] = () => {};
global.setEngine = () => 'ltx';
global.activePath = '/out/clip.mp4';
global.findOutputByPath = () => ({ engine: 'ltx', quality: 'balanced', frames: 121, mode: SIDE.mode });
const SIDE = %s;
global.fetch = async () => ({ ok: true, json: async () => ({ params: SIDE }) });
el('aspect').value = %s;
let submitted = null;
el('genForm').requestSubmit = () => {
  submitted = { width: el('width').value, height: el('height').value, aspect: el('aspect').value,
                upscale: el('upscale').value, method: el('upscale_method').value,
                quality: el('quality').value };
};
%s
ltxFinishActive().then(() => console.log(JSON.stringify(submitted)));
""" % (json.dumps(sidecar), json.dumps(form_aspect), fns))


def test_ui3_finish_submits_the_source_orientation_and_export():
    side = {"engine": "ltx", "mode": "t2v", "quality": "balanced", "frames": 121,
            "width": 1024, "height": 576, "prompt": "x", "seed_used": 9,
            "upscale": "fit_720p", "upscale_method": "pipersr"}
    got = _finish_harness(side, "vertical")
    assert got["quality"] == "standard"
    assert (got["width"], got["height"], got["aspect"]) == (1280, 704, "landscape")
    assert (got["upscale"], got["method"]) == ("fit_720p", "pipersr")
    # and the other way round: a portrait draft stays portrait with a landscape form
    side.update(width=576, height=1024)
    got = _finish_harness(side, "landscape")
    assert (got["width"], got["height"], got["aspect"]) == (704, 1280, "vertical")
    # a square draft stays square
    side.update(width=768, height=768)
    assert _finish_harness(side, "vertical")["aspect"] == "square"


# ---- UI-4: Train's "Download all" runs every missing pack, in order --------
def test_ui4_download_all_button_starts_each_download_sequentially():
    fns = "\n".join(extract_function(n, CJS) for n in
                    ("trainCheckPreflight", "trainInstallAll", "trainInstallAndWait"))
    out = _node("""
global.LAST_STATUS = { q8_available: true };
const btn = { disabled: false, textContent: '' };
const box = { style: {}, _html: '',
  set innerHTML(v) { this._html = v; }, get innerHTML() { return this._html; },
  querySelector(sel) { return (sel === '#trainInstallAllBtn' && this._html.includes('id="trainInstallAllBtn"')) ? btn : null; } };
global.document = { getElementById: id => (id === 'trainPreflight' ? box
                                            : id === 'trainInstallAllBtn' ? btn : null) };
global.fetch = async () => ({ json: async () => ({ ok: true, required: [
  { key: 'ltx23_dev', label: 'Dev transformer', size_gb: 21, blurb: 'x', ready: false },
  { key: 'ltx23_gemma', label: 'Text encoder', size_gb: 20, blurb: 'y', ready: false },
  { key: 'done_one', label: 'Z', size_gb: 1, blurb: 'z', ready: true }] }) });
const started = [];
let active = 0, overlap = false;
global.trainInstall = (key, onDone) => {
  started.push(key); active += 1; if (active > 1) overlap = true;
  setTimeout(() => { active -= 1; onDone(); }, 5);
};
""" + fns + """
(async () => {
  await trainCheckPreflight();
  // every inline handler left in the markup must at least be valid JS
  const bad = [];
  for (const m of box.innerHTML.matchAll(/onclick="([^"]*)"/g)) {
    try { new Function(m[1]); } catch (e) { bad.push(m[1]); }
  }
  let clicked = false;
  if (typeof btn.onclick === 'function') { clicked = true; await btn.onclick(); }
  global.trainCheckPreflight = async () => {};
  await new Promise(r => setTimeout(r, 50));
  console.log(JSON.stringify({ bad, clicked, started, overlap }));
})();
""")
    assert out["bad"] == []
    assert out["clicked"] is True
    assert out["started"] == ["ltx23_dev", "ltx23_gemma"]
    assert out["overlap"] is False


# ---- UI-5: keys on a card's own buttons reach those buttons ----------------
def test_ui5_card_keydown_ignores_keys_aimed_at_nested_controls():
    import re
    i = QJS.index('class="car-card${o.path')
    tpl = QJS[i:QJS.index("</div>`;", i)]
    handler = re.search(r'onkeydown="([^"]*)"', tpl).group(1).replace("${pathAttr}", "'/o/c.mp4'")
    out = _node("""
const calls = [];
function selectOutput(p) { calls.push(p); }
const card = { tag: 'card' };
function run(key, target) {
  let prevented = false;
  const event = { key, target, currentTarget: card, preventDefault: () => { prevented = true; } };
  (function () { %s })();
  return prevented;
}
const res = {
  card_enter: run('Enter', card), card_space: run(' ', card),
  info_enter: run('Enter', { tag: 'button' }), trash_space: run(' ', { tag: 'button' }),
  audio_space: run(' ', { tag: 'audio' }),
};
res.calls = calls.length;
console.log(JSON.stringify(res));
""" % handler)
    assert out == {"card_enter": True, "card_space": True, "info_enter": False,
                   "trash_space": False, "audio_space": False, "calls": 2}


# ---- UI-6: crash reports = only this install's own, positively identified --
def _ips(path: Path, proc: str, images: list[str]) -> None:
    header = json.dumps({"app_name": path.name.split("-")[0], "bug_type": "309"})
    body = json.dumps({"procPath": proc, "procName": "python3.11",
                       "usedImages": [{"path": x, "name": Path(x).name} for x in images]})
    path.write_text(header + "\n" + body, encoding="utf-8")


def test_ui6_crash_count_and_zip_hold_only_phosphene_processes(tmp_path):
    import io
    import zipfile
    from panel.routes import GET_ROUTES
    routes_meta = sys.modules["panel.routes_meta"]
    diag = tmp_path / "Library" / "Logs" / "DiagnosticReports"
    diag.mkdir(parents=True)
    ours = str(p.ROOT / "ltx-2-mlx" / "env" / "lib" / "python3.11" / "site-packages"
               / "mlx" / "core.cpython-311-darwin.so")
    _ips(diag / "python3.11-2026-09-29-100000.ips", "/opt/uv/python3.11/bin/python3.11",
         ["/usr/lib/dyld", ours])                                        # the helper
    _ips(diag / "python3.12-2026-09-29-110000.ips", "/opt/homebrew/bin/python3.12",
         ["/usr/lib/dyld", "/Users/x/other-app/.venv/lib/site-packages/torch/_C.so"])
    _ips(diag / "Python-2026-09-29-120000.ips", "/Users/x/notebook/bin/python",
         ["/usr/lib/dyld"])                                              # another app
    (diag / "python3.11-2026-09-29-130000.ips").write_text("{}\nnot json", encoding="utf-8")
    _ips(diag / "Safari-2026-09-29-140000.ips", "/Applications/Safari.app", [ours])
    with mock.patch.object(p.Path, "home", classmethod(lambda cls: tmp_path)):
        h = _H()
        GET_ROUTES["/panel/bug-context"](h, None)
        assert h.out[1]["crashCount"] == 1
        body = json.dumps({"title": "t", "body": "b", "includeCrashReports": True}).encode()
        h = _H()
        h.headers = {"Content-Length": str(len(body))}
        h.rfile = io.BytesIO(body)
        POST_ROUTES["/panel/bug-report"](h, "/panel/bug-report", {}, "application/json")
    zp = h.out[1]["zipPath"]
    assert zp, h.out
    with zipfile.ZipFile(zp) as zf:
        assert zf.namelist() == ["python3.11-2026-09-29-100000.ips"]
    shutil.move(str(Path(zp).parent), str(tmp_path / "zipdir"))       # keep /tmp clean
    assert routes_meta._phosphene_crash_reports(diag)[0].name.startswith("python3.11-2026-09-29-10")


# ---- UI-7: status polls never re-show Style training's hidden Voice card --
def test_ui7_ram_gate_leaves_feature_owned_visibility_alone():
    fn = extract_function("trainApplyRamGate", CJS)
    out = _node("""
const mk = id => ({ id, hidden: false, textContent: '' });
const card = mk('trainGateCard'), voice = mk('trainVoiceCard'), drop = mk('trainDropCard');
const classes = new Set();
const section = { children: [card, drop, voice],
  classList: { toggle(c, on) { if (on) classes.add(c); else classes.delete(c); } } };
const els = { trainGateCard: card, trainSection: section, trainVoiceCard: voice,
              trainGateTitle: mk('t'), trainGateBody: mk('b') };
global.document = { getElementById: id => els[id] || null };
global.LAST_STATUS = { tier: { ram_gb: 64, train_min_ram_gb: 24 } };
""" + fn + """
voice.hidden = true;                          // setTrainType('style')
for (let i = 0; i < 3; i++) trainApplyRamGate();   // three status polls
const roomy = { voice: voice.hidden, drop: drop.hidden, card: card.hidden, gated: classes.has('train-ram-gated') };
LAST_STATUS.tier.ram_gb = 16;
const gatedRet = trainApplyRamGate();
const small = { voice: voice.hidden, drop: drop.hidden, card: card.hidden, gated: classes.has('train-ram-gated'), ret: gatedRet };
console.log(JSON.stringify({ roomy, small }));
""")
    assert out["roomy"] == {"voice": True, "drop": False, "card": True, "gated": False}
    assert out["small"] == {"voice": True, "drop": False, "card": False, "gated": True, "ret": True}
    css = (ROOT / "webapp" / "style" / "panel.css").read_text(encoding="utf-8")
    assert "#trainSection.train-ram-gated > :not(#trainGateCard) { display: none !important; }" in css


# ---- UI-8 / EST-12: gallery size = the saved file's own dimensions ---------
def _box(t: bytes, payload: bytes) -> bytes:
    import struct
    return struct.pack(">I4s", 8 + len(payload), t) + payload


def _tkhd(w: int, h: int, version: int = 0) -> bytes:
    import struct
    head = bytes([version, 0, 0, 7]) + b"\0" * (20 if version == 0 else 32)
    return _box(b"tkhd", head + b"\0" * 8 + b"\0" * 8 + b"\0" * 36
                + struct.pack(">II", w << 16, h << 16))


def _fake_mp4(path: Path, w: int, h: int, *, version: int = 0, moov_last: bool = True) -> None:
    moov = _box(b"moov", _box(b"mvhd", b"\0" * 100)
                + _box(b"trak", _tkhd(0, 0, version))            # the audio track
                + _box(b"trak", _tkhd(w, h, version)))           # the video track
    parts = [_box(b"ftyp", b"isom\0\0\0\0isom"), _box(b"mdat", b"\x55" * 50000)]
    parts.insert(2 if moov_last else 1, moov)
    path.write_bytes(b"".join(parts))


def test_ui8_list_outputs_reports_each_files_own_dimensions():
    d = Path(tempfile.mkdtemp(prefix="ui8-", dir=p.OUTPUT))
    try:
        cases = {"square": (768, 768, 0, True), "portrait": (704, 1280, 1, False),
                 "native": (1024, 576, 0, True), "fit": (1280, 720, 0, False),
                 "sharp": (2048, 1152, 1, True)}
        for name, (w, h, ver, last) in cases.items():
            clip = p.OUTPUT / f"ui8_{name}_{d.name}.mp4"
            _fake_mp4(clip, w, h, version=ver, moov_last=last)
            os.utime(clip, (1_700_000_000, 1_700_000_000))  # past the in-flight cutoff
            # every sidecar claims a landscape Balanced render — the card must not care
            Path(str(clip) + ".json").write_text(json.dumps({"params": {
                "mode": "t2v", "quality": "balanced", "frames": 121,
                "width": 1024, "height": 576, "upscale": "fit_720p"}}), encoding="utf-8")
        garbage = p.OUTPUT / f"ui8_garbage_{d.name}.mp4"
        garbage.write_bytes(b"not an mp4 at all")
        os.utime(garbage, (1_700_000_000, 1_700_000_000))
        rows = {o["name"]: o for o in p.list_outputs(limit=0)}
        for name, (w, h, _, _) in cases.items():
            o = rows[f"ui8_{name}_{d.name}.mp4"]
            assert (o["width"], o["height"]) == (w, h), name
        g = rows[garbage.name]
        assert (g["width"], g["height"]) == (None, None)
        # a clip replaced at the same path re-reads (cache keyed on mtime/size)
        sq = p.OUTPUT / f"ui8_square_{d.name}.mp4"
        _fake_mp4(sq, 896, 1120, moov_last=False)
        os.utime(sq, (1, 1))
        assert p._output_dims(sq) == (896, 1120)
    finally:
        for f in p.OUTPUT.glob(f"ui8_*_{d.name}.mp4*"):
            f.unlink()
        shutil.rmtree(d, ignore_errors=True)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
def test_ui8_reads_a_real_ffmpeg_file():
    with tempfile.TemporaryDirectory() as td:
        clip = Path(td) / "real.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=704x1280:d=0.2",
                        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-shortest",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip)], check=True)
        assert p._mp4_display_dims(clip) == (704, 1280)


def test_ui8_card_chip_prints_the_files_size_or_none():
    fn = extract_function("_cardModeSizeChip", QJS)
    out = _node("""
const BOOT = { ltx: { qualities: [{ key: 'balanced', canvas: '1024×576', delivered_canvas: '1280×720' }] } };
function h3TierByKeyExact() { return { spec: '1344×768', width: 1344, height: 768 }; }
""" + fn + """
console.log(JSON.stringify([
  _cardModeSizeChip({ mode: 't2v', quality: 'balanced', width: 768, height: 768 }),
  _cardModeSizeChip({ mode: 't2v', quality: 'balanced', width: 704, height: 1280 }),
  _cardModeSizeChip({ mode: 'i2v', h3_tier: 'high_5s', width: 768, height: 448 }),
  _cardModeSizeChip({ mode: 't2v', quality: 'balanced', width: null, height: null })]));
""")
    assert out == ["t2v · 768×768", "t2v · 704×1280", "i2v · 768×448", "t2v"]


# ---- UI-9: a replaced clip gets a new poster URL ---------------------------
def test_ui9_poster_url_changes_when_the_source_changes():
    fn = extract_function("_posterVersionQuery", (JS / "queue.js").read_text(encoding="utf-8"))
    out = _node(fn + """
const a = { path: '/o/c.mp4', url: '/file?path=%2Fo%2Fc.mp4&v=1700000000', size_mb: 1.5 };
const b = Object.assign({}, a, { url: '/file?path=%2Fo%2Fc.mp4&v=1700000123' });   // re-rendered
const c = Object.assign({}, a, { size_mb: 1.75 });                                   // same second, new bytes
console.log(JSON.stringify([_posterVersionQuery(a), _posterVersionQuery(b), _posterVersionQuery(c),
                            _posterVersionQuery({ path: '/o/x.mp4' })]));
""")
    assert out[0] == "&v=1700000000-1572864"
    assert len(set(out[:3])) == 3
    assert out[3] == ""
    qjs = (JS / "queue.js").read_text(encoding="utf-8")
    assert 'src="/poster?path=${encodeURIComponent(o.path)}${_posterVersionQuery(o)}"' in qjs


# ---- UI-10: a refused restart leaves "Restart now" usable -------------------
def test_ui10_refused_restart_rearms_the_banner_button():
    hjs = (JS / "health.js").read_text(encoding="utf-8")
    fns = extract_function("_ubRestartState", hjs) + "\n" + extract_function("panelRestart", hjs)
    out = _node("""
const mk = () => ({ hidden: false, disabled: false, textContent: '', classList: { add() {} } });
const els = { updateBanner: mk(), ubStar: mk(), ubTitle: mk(), ubSub: mk(), ubUpdate: mk(), ubLater: mk(), versionPill: mk() };
global.document = { getElementById: id => els[id] || null };
global._uiEvent = () => {};
// health.js's module-level state (bc09bf3 reads it in _ubRestartState).
global._versionState = null;
const toasts = [];
global.phosToast = (t) => toasts.push(t);
let reloaded = false;
global.location = { reload: () => { reloaded = true; } };
const replies = [
  { status: 409, body: { ok: false, error: 'A render is running — stop it first.' } },
  { status: 200, body: { ok: true } },
];
global.fetch = async (url) => {
  if (url === '/version') return { ok: true };
  const r = replies.shift();
  return { status: r.status, json: async () => r.body };
};
global.setTimeout = (fn) => fn();
""" + fns + """
(async () => {
  _ubRestartState('4.17.0', false);
  const go = els.ubUpdate;
  await go.onclick();
  const afterRefusal = { disabled: go.disabled, label: go.textContent };
  await go.onclick();
  console.log(JSON.stringify({ afterRefusal, reloaded, toasts: toasts.length }));
})();
""")
    assert out["afterRefusal"] == {"disabled": False, "label": "Restart now"}
    assert out["reloaded"] is True            # the second click restarts for real
    assert out["toasts"] == 1
