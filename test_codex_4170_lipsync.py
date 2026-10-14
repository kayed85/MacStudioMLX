#!/usr/bin/env python3
"""4.17.0 Codex review — lipsync area (a2v lip-sync, Continue the song, Split
into clips, segment Retake, Windows plan, Load Params for a2v / One Shot).
Findings: ~/AI/projects/phosphene/review-2026-09-29-ux/codex/findings_lipsync.md.
JS is executed in node where the behaviour is a function's.
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

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mlx_ltx_panel as p                                             # noqa: E402,F401
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
    def __init__(self, form=None):
        self.status, self.payload, self._form = None, None, form or {}

    def _json(self, payload, status=200):
        self.payload, self.status = payload, status

    def _read_form_body(self):
        return b"", self._form


# ---- LIPSYNC-1: lip-sync helpers stay inside outputs/uploads/state ----------
@pytest.fixture
def _roots(tmp_path):
    P = routes_queue.P
    out, up, st, priv = (tmp_path / n for n in ("out", "up", "state", "private"))
    for d in (out, up, st, priv):
        d.mkdir()
    with mock.patch.object(P, "OUTPUT", out), mock.patch.object(P, "UPLOADS", up), \
            mock.patch.object(P, "STATE_DIR", st):
        yield P, out, up, priv


def test_lipsync_1_continue_song_refuses_media_outside_the_roots(_roots):
    P, out, up, priv = _roots
    secret = priv / "private.mp4"
    secret.write_bytes(b"x")
    link = out / "escape.mp4"
    link.symlink_to(secret)
    calls = []
    with mock.patch.object(P, "find_closed_mouth_frame",
                           side_effect=lambda s: calls.append(("find", s)) or
                           {"time_s": 4.8, "openness": 0.01}), \
            mock.patch.object(P, "extract_frame_png",
                              side_effect=lambda s, t, d: calls.append(("extract", s)) or True):
        for raw in (str(secret), str(link), str(out / ".." / "private" / "private.mp4")):
            h = _H({"clip": [raw]})
            routes_queue.post_a2v_continue_song(h, "/a2v/continue_song", {}, "")
            assert h.status == 403, (raw, h.status, h.payload)
        assert calls == []
        # A real output clip still works, and its sidecar offset is read.
        clip = out / "a2v_1.mp4"
        clip.write_bytes(b"x")
        (out / "a2v_1.mp4.json").write_text(json.dumps({"params": {"audio_start_time": 10.0}}))
        h = _H({"clip": [str(clip)]})
        routes_queue.post_a2v_continue_song(h, "/a2v/continue_song", {}, "")
    assert h.status == 200 and h.payload["ok"], h.payload
    assert h.payload["audio_start_time"] == 14.8
    assert [c[0] for c in calls] == ["find", "extract"]


def test_lipsync_1_mouth_check_refuses_media_outside_the_roots(_roots):
    P, out, up, priv = _roots
    secret = priv / "face.png"
    secret.write_bytes(b"x")
    (up / "link.png").symlink_to(secret)
    seen = []
    with mock.patch.object(P, "mouth_open_ratio", side_effect=lambda i: seen.append(i) or 0.1):
        for raw in (str(secret), str(up / "link.png")):
            h = _H({"image": [raw]})
            routes_queue.post_a2v_mouth_check(h, "/a2v/mouth_check", {}, "")
            assert h.status == 403, (raw, h.payload)
        assert seen == []
        ok = up / "face.png"
        ok.write_bytes(b"x")
        h = _H({"image": [str(ok)]})
        routes_queue.post_a2v_mouth_check(h, "/a2v/mouth_check", {}, "")
    assert h.status == 200 and h.payload["measured"] is True
    assert seen == [str(ok.resolve())]


# ---- LIPSYNC-3 / 5 / 11: segment Retake -------------------------------------
import ast                                                            # noqa: E402
import math                                                           # noqa: E402

HELPER_SRC = (ROOT / "mlx_warm_helper.py").read_text(encoding="utf-8")


def _helper_fns(*names, extra=None):
    tree = ast.parse(HELPER_SRC)
    defs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert sorted(d.name for d in defs) == sorted(names), names
    ns: dict = {"math": math, **(extra or {})}
    exec(compile(ast.Module(body=defs, type_ignores=[]), "mlx_warm_helper.py", "exec"), ns)
    return ns


def _engine_latent_count(pixel_frames: int) -> int:
    # compute_video_latent_shape() in the pinned ltx-core-mlx (patchifiers.py):
    # F = (num_frames + temporal_compression - 1) // temporal_compression.
    return (pixel_frames + 8 - 1) // 8


def test_lipsync_5_retake_window_matches_the_engine_latent_count():
    win = _helper_fns("_retake_latent_window")["_retake_latent_window"]
    # 121-frame source: retake.py keeps 1 + 8*15 = 121 pixels = 16 latents.
    w = win(4.9, 5.0, 24.0, 121)
    assert w["pixel_frames"] == 121 and w["latent_frames"] == _engine_latent_count(121) == 16
    # The end of the clip reaches the LAST latent (15), exclusive end 16.
    assert (w["start_frame"], w["end_frame"]) == (15, 16)
    # First stretch: pixel 0 is latent 0 alone; 0-1 s = pixels 0..23 = latents 0..3, exclusive end 4.
    w = win(0.0, 1.0, 24.0, 121)
    assert (w["start_frame"], w["end_frame"]) == (0, 4)
    # Interior 2.0-3.0 s = pixels 48..71 -> latents 6..9, end 10 (48 is in latent 6 = px 41..48).
    w = win(2.0, 3.0, 24.0, 121)
    assert (w["start_frame"], w["end_frame"]) == (6, 10)
    # Whole clip: every latent.
    w = win(0.0, 99.0, 24.0, 121)
    assert (w["start_frame"], w["end_frame"]) == (0, 16)
    # Every requested pixel frame lands inside the regenerated latents, for
    # any range, rate and source length (the cover never rounds inward).
    for frames, fps in ((121, 24.0), (241, 24.0), (61, 12.0), (100, 25.0), (97, 30.0)):
        k = (frames - 1) // 8
        pixels = 1 + 8 * k
        for a10 in range(0, int(pixels / fps * 10)):
            for b10 in range(a10 + 1, int(pixels / fps * 10) + 2):
                w = win(a10 / 10, b10 / 10, fps, frames)
                assert 0 <= w["start_frame"] < w["end_frame"] <= w["latent_frames"]
                assert w["latent_frames"] == _engine_latent_count(pixels)
                lat = lambda px: 0 if px <= 0 else (px - 1) // 8 + 1   # noqa: E731
                first = min(pixels - 1, math.floor(a10 / 10 * fps + 1e-6))
                last = min(pixels, math.ceil(b10 / 10 * fps - 1e-6)) - 1
                if last >= first:
                    assert w["start_frame"] <= lat(first)
                    assert lat(last) < w["end_frame"]


def test_lipsync_3_retake_and_extend_decode_at_the_source_rate():
    # The probe helper returns the source's own rate, the panel rate only on failure.
    fake_mod = type(sys)("ltx_core_mlx.utils.ffmpeg")
    fake_mod.probe_video_info = lambda path: type("I", (), {"fps": 12.0})()
    with mock.patch.dict(sys.modules, {"ltx_core_mlx": type(sys)("ltx_core_mlx"),
                                       "ltx_core_mlx.utils": type(sys)("ltx_core_mlx.utils"),
                                       "ltx_core_mlx.utils.ffmpeg": fake_mod}):
        src_fps = _helper_fns("_source_fps")["_source_fps"]
        assert src_fps("/x.mp4", 24.0) == 12.0
        fake_mod.probe_video_info = lambda path: (_ for _ in ()).throw(OSError("no"))
        assert src_fps("/x.mp4", 24.0) == 24.0
    # Retake: probed fps drives BOTH the latent window and the decode.
    blk = HELPER_SRC[HELPER_SRC.index('if action == "retake_segment":'):]
    blk = blk[:blk.index('if action == "generate_hq":')]
    assert "_retake_latent_window(start_sec, end_sec, fps, src_frames)" in blk
    assert "frame_rate=fps)" in blk
    assert 'frame_rate=float(p.get("frame_rate", 24.0))' not in blk
    assert '"fps": fps, "frames": win["pixel_frames"]' in blk
    # Extend: same class — the decode reads the source rate.
    ext = HELPER_SRC[HELPER_SRC.index('if action == "extend":'):HELPER_SRC.index('if action == "retake_segment":')]
    # 4.19.0 (#48): the extend decode has two paths (the splice-ready
    # _decode_extend_stems and the old one), both fed one source-rate value.
    assert "_ext_fps = _source_fps(video_path, p.get(\"frame_rate\"))" in ext
    assert "frame_rate=_ext_fps" in ext and ", _ext_fps)" in ext, \
        "both extend decodes (stems + mp4) must use the source rate"
    assert 'frame_rate=float(p.get("frame_rate", 24.0))' not in ext


def test_lipsync_11_retake_sidecar_records_the_delivered_clip(tmp_path):
    P = p
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x" * 4096)
    job = P.make_job({"mode": ["retake"], "prompt": ["fix the hand"],
                      "video_path": [str(src)], "retake_start_sec": ["2.0"],
                      "retake_end_sec": ["3.0"]})
    job["id"] = "rt1"
    assert job["params"]["frames"] == 121            # make_job's default — not the source
    written = {}

    def _fake_run(spec):
        return {"seed_used": 7, "elapsed_sec": 1.0, "fps": 12.0, "frames": 241,
                "width": 768, "height": 432, "retake_start_frame": 6, "retake_end_frame": 9}

    def _probe_dims(path):
        return (768, 432)

    with mock.patch.object(P, "OUTPUT", tmp_path), \
            mock.patch.dict(P.SYSTEM_CAPS, {"allows_extend": True}), \
            mock.patch.object(P, "hq_surface_missing", return_value=[]), \
            mock.patch.object(P, "hq_weights", return_value={"dev_transformer": "/dev"}), \
            mock.patch.object(P, "tier_max_dim", return_value=0), \
            mock.patch.object(P, "_apply_generation_profile_to_job"), \
            mock.patch.object(P, "_gemma4_tower_supported", return_value=True), \
            mock.patch.object(P.HELPER, "run", side_effect=_fake_run), \
            mock.patch.object(P, "_probe_video_dims", side_effect=_probe_dims), \
            mock.patch.object(P, "_probe_video_frames", return_value=241), \
            mock.patch.object(P, "_probe_video_fps", return_value=12.0), \
            mock.patch.object(P, "write_sidecar",
                              side_effect=lambda path, data: written.update({str(path): data}) or True):
        P.run_job_inner(job)
    (sc,) = written.values()
    prm = sc["params"]
    assert (prm["width"], prm["height"], prm["frames"], prm["frame_rate"]) == (768, 432, 241, 12.0)
    assert sc["fps"] == 12.0
    # The gallery's clip length (frames / frame_rate) is the delivered 20 s.
    assert round(prm["frames"] / prm["frame_rate"], 2) == 20.08
    # Probe failure: the helper's own report fills in.
    with mock.patch.object(P, "_probe_video_dims", return_value=(0, 0)), \
            mock.patch.object(P, "_probe_video_frames", return_value=0), \
            mock.patch.object(P, "_probe_video_fps", return_value=0.0):
        got = P._retake_delivered_geometry(tmp_path / "none.mp4",
                                           {"fps": 12.0, "frames": 241, "width": 768, "height": 432})
    assert got == {"width": 768, "height": 432, "frames": 241, "frame_rate": 12.0}


# ---- LIPSYNC-4 / 6 / 7: Split into clips ------------------------------------
def _split_harness(body: str) -> dict:
    src = (JS / "characters.js").read_text(encoding="utf-8")
    names = ["_a2vFramesForSeconds", "audioStudioSplitCancel", "_waitForJob",
             "audioStudioSplitIntoClips"]
    if "function _a2vSplitChain(" in src:
        names.append("_a2vSplitChain")
    fns = "\n".join(extract_function(n, src) for n in names)
    has_chain = "_a2vSplitRunning" in src
    return _node(r"""
let _a2vSplitCancel = false;
""" + ("let _a2vSplitRunning = false;\n" if has_chain else "") + r"""
const els = {};
function el(id, v) { els[id] = { id, value: v, checked: false, dataset: {}, textContent: '' }; return els[id]; }
el('audioStudioStart', '0'); el('audioStudioWidth', '1024'); el('audioStudioHeight', '576');
el('audioStudioPrompt', 'She sings.'); el('a2v_image', '/up/face.png');
el('a2v_image_crop_focus', '0.2'); el('audioStudioStatus', '');
const acs = el('audioConditioningScale', '3'); acs.dataset.auto = '1';
el('audioStudioStemAuto', '');
const document = { getElementById: id => els[id] || null };
const AUDIO_STUDIO = { busy: false, audioDuration: 30, audioPath: '/up/song.wav' };
let _activeLoras = [{ path: '/loras/me.safetensors', strength: 0.9, name: 'me' }];
const toasts = [];
function phosToast(m, o) { toasts.push([m, (o || {}).kind]); }
function confirm() { return true; }
function alert(m) { toasts.push([m, 'alert']); }
const setTimeout = (fn) => Promise.resolve().then(fn);
const adds = [];
let anchorFor = (start, clip) => ({ ok: true, anchor_image: '/up/anchor_' + adds.length + '.png',
                                    audio_start_time: +(start + 9.542).toFixed(3) });
let holdAdd = null;
let failAdd = false;
async function fetch(url, opts) {
  if (url === '/queue/add') {
    const fd = Object.fromEntries(opts.body.entries());
    adds.push(fd);
    if (holdAdd) await holdAdd;
    if (failAdd) return { ok: false, status: 500, json: async () => ({ error: 'boom' }) };
    return { ok: true, json: async () => ({ id: 'j' + adds.length }) };
  }
  if (url === '/status') {
    return { json: async () => ({ history: adds.map((a, k) => ({ id: 'j' + (k + 1), status: 'done',
                                                                 output_path: '/out/p' + (k + 1) + '.mp4' })) }) };
  }
  if (url === '/a2v/continue_song') {
    const clip = opts.body.get('clip');
    const k = parseInt(clip.match(/p(\d+)/)[1], 10);
    return { json: async () => anchorFor(parseFloat(adds[k - 1].audio_start_time), clip) };
  }
  throw new Error('unexpected ' + url);
}
""" + fns + "\n(async () => {\n" + body + "\n})().catch(e => { console.log(JSON.stringify({error: String(e.stack || e)})); });\n")


def test_lipsync_4_split_covers_the_whole_song_with_overlapping_anchors():
    got = _split_harness(r"""
      await audioStudioSplitIntoClips(10);
      const starts = adds.map(a => parseFloat(a.audio_start_time));
      const ends = adds.map(a => parseFloat(a.audio_start_time) + parseInt(a.frames, 10) / 24);
      const plain = { status: els.audioStudioStatus.textContent, toasts: toasts.slice() };
      // A non-advancing anchor (same start as the part) falls back to the plain cut.
      adds.length = 0; toasts.length = 0;
      anchorFor = (start) => ({ ok: true, anchor_image: '/up/a.png', audio_start_time: start });
      await audioStudioSplitIntoClips(10);
      console.log(JSON.stringify({ starts, ends, plain,
        stuckStarts: adds.map(a => parseFloat(a.audio_start_time)),
        stuckStatus: els.audioStudioStatus.textContent }));
    """)
    assert "error" not in got, got
    assert got["starts"] == [0, 9.542, 19.084, 28.626]
    assert max(got["ends"]) >= 30 - 0.05
    assert got["plain"]["status"].startswith("Split into 4 clips done")
    assert got["stuckStarts"] == [0, 10, 20]
    assert got["stuckStatus"].startswith("Split into 3 clips done")


def test_lipsync_6_split_parts_carry_loras_and_the_first_crop():
    got = _split_harness(r"""
      await audioStudioSplitIntoClips(10);
      console.log(JSON.stringify({ adds }));
    """)
    assert "error" not in got, got
    adds = got["adds"]
    assert len(adds) >= 3
    lor = json.loads(adds[0]["loras"])
    assert lor == [{"path": "/loras/me.safetensors", "strength": 0.9}]
    assert all(a.get("loras") == adds[0]["loras"] for a in adds)
    assert adds[0]["image"] == "/up/face.png" and adds[0]["image_crop_focus"] == "0.2"
    for a in adds[1:]:
        assert a["image"].startswith("/up/anchor_")
        assert "image_crop_focus" not in a


def test_lipsync_7_only_one_split_chain_runs_and_the_guard_is_released():
    got = _split_harness(r"""
      let release; holdAdd = new Promise(r => { release = r; });
      const first = audioStudioSplitIntoClips(10);
      await Promise.resolve();
      const second = audioStudioSplitIntoClips(10);     // while part 1 is still queueing
      await second;
      const whileHeld = adds.length;
      release(); holdAdd = null;
      await first;
      const afterFirst = adds.length;
      // A failed chain releases the guard too.
      adds.length = 0; failAdd = true;
      await audioStudioSplitIntoClips(10);
      failAdd = false;
      const afterFail = adds.length;
      adds.length = 0;
      await audioStudioSplitIntoClips(10);
      console.log(JSON.stringify({ whileHeld, afterFirst, afterFail, again: adds.length,
                                   toasts }));
    """)
    assert "error" not in got, got
    assert got["whileHeld"] == 1
    assert got["afterFirst"] == 4
    assert got["afterFail"] == 1 and got["again"] == 4
    assert any("already running" in t[0] for t in got["toasts"])


# ---- LIPSYNC-8: One Shot Load Params on first visit keeps the take's length --
def _oneshot_harness(body: str) -> dict:
    import re
    src = (JS / "oneshot.js").read_text(encoding="utf-8")
    state = re.search(r"^const OS = \{.*?^\};", src, re.M | re.S).group(0)
    consts = "\n".join(re.search(r"^const %s = .*?;$" % n, src, re.M).group(0)
                       for n in ("OS_SECONDS_LS_KEY", "OS_SECONDS"))
    names = ["_osStoredSeconds", "oneshotOpenFromParams", "osWire"]
    if "function _osApplyStoredPrefs(" in src:
        names.append("_osApplyStoredPrefs")
    fns = "\n".join(extract_function(n, src) for n in names)
    return _node(r"""
let _store = {};
const localStorage = { getItem: k => (k in _store ? _store[k] : null), setItem: (k, v) => { _store[k] = String(v); } };
const document = { getElementById: () => null, querySelectorAll: () => [], querySelector: () => null };
function osEl() { return null; }
function workflowSwitch(w) { if (w === 'oneshot') osWire(); }
""" + consts + "\n" + state + "\n" + fns + "\n" + body)


@pytest.mark.parametrize("stored", [None, "45", "30"])
def test_lipsync_8_first_use_load_params_keeps_the_saved_duration(stored):
    pre = "" if stored is None else "_store.phos_oneshot_seconds = %s;" % json.dumps(stored)
    got = _oneshot_harness(pre + r"""
      oneshotOpenFromParams({ prompt: 'x', take: { seconds: 90, engine: 'ltx' } });
      const restored = OS.seconds;
      osWire();                          // a later tab entry never re-applies the stored length
      console.log(JSON.stringify({ restored, after: OS.seconds }));
    """)
    assert got == {"restored": 90, "after": 90}


def test_lipsync_8_plain_first_visit_still_uses_the_stored_length():
    got = _oneshot_harness(r"""
      _store.phos_oneshot_seconds = '45';
      osWire();
      console.log(JSON.stringify({ seconds: OS.seconds }));
    """)
    assert got == {"seconds": 45}


# ---- LIPSYNC-9: a stale Windows plan never replaces the current one ---------
def test_lipsync_9_out_of_order_windows_plans_apply_only_the_current_length():
    src = (JS / "characters.js").read_text(encoding="utf-8")
    fn = extract_function("windowPromptsInput", src)
    got = _node(r"""
let _windowsPlanCache = { frames: null, plan: null };
let _windowsPlanFetching = null;
const els = { window_prompts: { value: '' }, windowsDynamicSlots: {}, temporal_mode: { value: 'windows' },
              frames: { value: '241' } };
const document = { getElementById: id => els[id] || null };
const rendered = [];
function _renderWindowsSlotsFromPlan(plan) { rendered.push(plan.count); }
const pending = {};
function fetch(url) {
  const f = url.split('frames=')[1];
  return new Promise(res => { pending[f] = () => res({ json: async () => ({ ok: true, count: f === '241' ? 3 : 7 }) }); });
}
""" + fn + r"""
(async () => {
  windowPromptsInput();                  // 241 in flight
  els.frames.value = '721';
  windowPromptsInput();                  // 721 in flight
  pending['721'](); await new Promise(r => setTimeout(r, 10));
  pending['241'](); await new Promise(r => setTimeout(r, 10));   // the older answer lands last
  const cached = _windowsPlanCache.frames;
  const before = rendered.slice();
  // Back to 241: fetched fresh and applied (a stale answer never blocks the next one).
  els.frames.value = '241';
  windowPromptsInput();
  pending['241'](); await new Promise(r => setTimeout(r, 10));
  console.log(JSON.stringify({ before, rendered, cached, now: _windowsPlanCache.frames, fetching: _windowsPlanFetching }));
})();
""")
    assert got["before"] == [7], got                 # the stale 3-window plan was dropped
    assert got["rendered"] == [7, 3], got
    assert got["cached"] == 721 and got["now"] == 241 and got["fetching"] is None


# ---- LIPSYNC-10: A2V Load Params restores "Listen to the voice only" ---------
def _a2v_roundtrip(params: dict, checked_before: bool, via_continue: bool = False) -> dict:
    import test_review_416_ltx as r416                                # the shared A2V DOM shim
    from extract_panel_js import panel_source                        # noqa: PLC0415
    src = panel_source()
    fns = ["_a2vFramesForSeconds", "audioStudioDurationChanged", "a2vLaneAudioScale",
           "audioConditioningScaleChanged", "audioConditioningScaleReset",
           "a2vLoadParams", "loadParams", "audioStudioGenerate"]
    if via_continue:
        fns.append("continueSongFromClip")
    js = r416.A2V_DOM + (
        "el('audioStudioStemAuto'); els.audioStudioStemAuto.checked = %s;\n"
        "function requestAnimationFrame(f) { f(); }\n"
        # characters.js module state audioStudioGenerate reads (vocal separation)
        "const A2V_SEPARATOR = { ready: null };\n" % json.dumps(checked_before)
    ) + "\n".join(extract_function(n, src) for n in fns) + """
const sidecar = %s;
(async () => {
  globalThis.fetch = async (url) => ({ok: true, json: async () => (String(url).startsWith('/a2v/continue_song')
      ? {ok: true, anchor_image: '/up/anchor.png', audio_start_time: 12.5} : sidecar)});
  if (%s) await continueSongFromClip('/x.mp4'); else await loadParams();
  const restored = els.audioStudioStemAuto.checked;
  globalThis.fetch = async (url, opts) => { posted = Object.fromEntries(opts.body); return {ok: true}; };
  await audioStudioGenerate();
  console.log(JSON.stringify({restored, stem: posted.audio_stem_auto || null, start: posted.audio_start_time}));
})();
""" % (json.dumps({"output": "/x.mp4", "params": params}), "true" if via_continue else "false")
    return _node(js)


def _a2v_params(**over):
    base = {"mode": "a2v", "prompt": "a singer", "audio": "/songs/take.wav",
            "image": "/uploads/singer.png", "width": 832, "height": 480, "frames": 241,
            "seed": "7", "audio_start_time": 0, "audio_conditioning_scale": ""}
    base.update(over)
    return base


@pytest.mark.parametrize("via_continue", [False, True])
@pytest.mark.parametrize("saved,before,want", [
    ("", True, False),        # rendered with the full mix; the form had it on
    ("on", False, True),      # rendered voice-only; the form had it off
    ("true", False, True),
])
def test_lipsync_10_load_params_restores_voice_only(saved, before, want, via_continue):
    got = _a2v_roundtrip(_a2v_params(audio_stem_auto=saved), before, via_continue)
    assert got["restored"] is want
    assert got["stem"] == ("on" if want else None)
    if via_continue:
        assert got["start"] == "12.5"


def test_lipsync_10_legacy_and_explicit_stem_sidecars():
    # No field at all: the clip predates the control and heard the full mix.
    p = _a2v_params()
    got = _a2v_roundtrip(p, True)
    assert got["restored"] is False and got["stem"] is None
    # A music-video shot rendered from an explicit vocal stem listened to the voice alone.
    got = _a2v_roundtrip(_a2v_params(audio_stem="/stems/vocals.wav", audio_stem_auto=""), False)
    assert got["restored"] is True and got["stem"] == "on"
