#!/usr/bin/env python3
"""Lip-sync quality (4.17.x): "Listen to the voice only" must really listen to
the voice, and a character LoRA must not mute the mouth.

What was wrong, measured on the 4.17 power-ballad post
(review-2026-09-29-ux/lipsync_quality.txt in the PM hub):

1. The Lip-sync form ticks "Listen to the voice only" by default, and nothing
   installed the vocal separator. The panel quietly conditioned on the FULL MIX
   and said so in one log line. The run-it-yourself script that did install
   demucs produced a CLI that dies at the very end (torchaudio >= 2.9 saves
   through torchcodec), which the panel ALSO turned into the full mix.
2. A character LoRA at full strength through both a2v stages halves the mouth
   opening and cuts its movement to about a third.

JS is executed in node where the behaviour is a function's.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mlx_ltx_panel as P                                             # noqa: E402
from extract_panel_js import extract_function                         # noqa: E402

routes_queue = sys.modules["panel.routes_queue"]
routes_models = sys.modules["panel.routes_models"]
NODE = shutil.which("node")
JS = ROOT / "webapp" / "js"
HELPER_SRC = (ROOT / "mlx_warm_helper.py").read_text(encoding="utf-8")


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


@pytest.fixture
def song(tmp_path):
    p = tmp_path / "song.wav"
    p.write_bytes(b"RIFF....WAVE")
    return p


# ---------------------------------------------------------------------------
# 1. NEVER A SILENT FALLBACK TO THE FULL MIX
# ---------------------------------------------------------------------------
def test_voice_only_without_a_separator_is_refused_not_rendered_on_the_mix(song):
    with mock.patch.object(P, "_a2v_separator_command", return_value=None):
        with pytest.raises(P.RenderRefused) as ei:
            P.a2v_conditioning_audio({"audio_stem_auto": "on"}, str(song))
    assert ei.value.reason == "vocal_separator"
    assert "not installed" in str(ei.value)
    # It is an install prompt, and analytics must count it as a refusal.
    assert P._analytics_refusal_reason(str(ei.value)) == "vocal_separator"


def test_a_separation_that_fails_fails_the_job_with_its_cause(song):
    with mock.patch.object(P, "_a2v_separator_command", return_value=["sep"]), \
         mock.patch.object(P, "_a2v_separate_vocals",
                           side_effect=RuntimeError("the separator wrote no vocal")):
        with pytest.raises(RuntimeError) as ei:
            P.a2v_conditioning_audio({"audio_stem_auto": "1"}, str(song))
    msg = str(ei.value)
    assert "the separator wrote no vocal" in msg
    assert "Nothing was rendered" in msg
    assert "full mix" in msg          # names the way out: untick the box


def test_stop_during_the_separation_is_still_a_cancellation(song):
    with mock.patch.object(P, "_a2v_separator_command", return_value=["sep"]), \
         mock.patch.object(P, "_a2v_separate_vocals",
                           side_effect=P.JobCancelled("stopped")):
        with pytest.raises(P.JobCancelled):
            P.a2v_conditioning_audio({"audio_stem_auto": "on"}, str(song))


def test_voice_only_off_and_explicit_stems_are_untouched(song, tmp_path):
    with mock.patch.object(P, "_a2v_separator_command") as sep:
        assert P.a2v_conditioning_audio({}, str(song)) == (str(song), "")
        stem = tmp_path / "v.wav"
        stem.write_bytes(b"RIFF")
        assert P.a2v_conditioning_audio({"audio_stem": str(stem),
                                         "audio_stem_auto": "on"}, str(song))[0] == str(stem)
    sep.assert_not_called()


def test_queue_add_refuses_voice_only_when_the_separator_is_missing(tmp_path):
    # A song that EXISTS: 4.17.5 refuses a missing one first (input_missing),
    # and this test is about the separator.
    song = tmp_path / "song.wav"
    song.write_bytes(b"RIFF")
    form = {"mode": ["a2v"], "prompt": ["a singer"], "audio": [str(song)],
            "audio_stem_auto": ["on"]}
    h = _H(form)
    before = len(P.STATE["queue"])
    with mock.patch.object(P, "_a2v_separator_command", return_value=None):
        routes_queue.post_run(h, "/queue/add", {}, "")
    assert h.status == 400
    assert h.payload["code"] == "vocal_separator_missing"
    assert "not installed" in h.payload["error"]
    assert len(P.STATE["queue"]) == before, "the refused job was queued anyway"


def test_queue_add_refusal_only_for_voice_only_a2v():
    base = {"mode": "a2v", "audio_stem_auto": "on"}
    with mock.patch.object(P, "_a2v_separator_command", return_value=None):
        assert P.a2v_stem_refusal(base)
        assert P.a2v_stem_refusal({**base, "audio_stem": "/have/one.wav"}) is None
        assert P.a2v_stem_refusal({**base, "audio_stem_auto": ""}) is None
        assert P.a2v_stem_refusal({**base, "mode": "i2v"}) is None
    with mock.patch.object(P, "_a2v_separator_command", return_value=["sep"]):
        assert P.a2v_stem_refusal(base) is None


# ---------------------------------------------------------------------------
# 2. THE SEPARATOR THE PANEL RUNS IS OURS, AND IT CANNOT DIE ON AUDIO I/O
# ---------------------------------------------------------------------------
def test_status_reads_the_engine_venv_without_importing_torch(tmp_path):
    venv = tmp_path / "env"
    site = venv / "lib" / "python3.11" / "site-packages"
    (venv / "bin").mkdir(parents=True)
    site.mkdir(parents=True)
    py = venv / "bin" / "python3.11"
    py.write_text("")
    models = tmp_path / "models"
    with mock.patch.object(P, "HELPER_PYTHON", py), \
         mock.patch.object(P, "MODELS_DIR", models):
        assert P.a2v_separator_status()["ready"] is False
        assert P._a2v_separator_command() is None
        (site / "demucs").mkdir()
        (site / "demucs" / "apply.py").write_text("")
        assert P.a2v_separator_status()["ready"] is False, "demucs without torch is not ready"
        (site / "torch").mkdir()
        (site / "torch" / "__init__.py").write_text("")
        st = P.a2v_separator_status()
        assert st["ready"] is True and st["weights"] is False
        cmd = P._a2v_separator_command()
        assert cmd[:2] == [str(py), str(P.A2V_SEPARATOR_RUNNER)]
        assert "--ffmpeg" in cmd
        ck = models / "demucs" / "hub" / "checkpoints" / P.A2V_SEPARATOR_WEIGHTS
        ck.parent.mkdir(parents=True)
        ck.write_bytes(b"x")
        assert P.a2v_separator_status()["weights"] is True


def test_the_demucs_cli_is_never_what_runs():
    src = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
    block = src.split("def _a2v_separator_command")[1].split("\ndef ")[0]
    assert "A2V_SEPARATOR_RUNNER" in block
    assert '"demucs"' not in block and "--two-stems" not in src
    assert "_resolve_demucs" not in src, "the CLI resolver came back"


def test_the_runner_never_touches_torchaudio():
    import ast                                                        # noqa: PLC0415
    runner = (ROOT / "scripts" / "a2v_separate.py").read_text(encoding="utf-8")
    mods = set()
    for node in ast.walk(ast.parse(runner)):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add((node.module or "").split(".")[0])
    assert "torchaudio" not in mods and "soundfile" not in mods
    assert {"wave", "demucs"} <= mods


def test_the_runner_writes_a_real_wav(tmp_path):
    torch = pytest.importorskip("torch")
    import a2v_separate as sep                                        # noqa: PLC0415
    out = tmp_path / "v.wav"
    sig = torch.zeros(2, 4410)
    sig[0, :100] = 0.5
    sep.write_wav(str(out), sig)
    with wave.open(str(out)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == \
            (2, 2, sep.SAMPLE_RATE, 4410)


def test_a_separation_runs_tracked_and_caches(tmp_path, song):
    # A stand-in runner with the real runner's CLI: --in SRC --out DST.
    fake = tmp_path / "fake_sep.sh"
    fake.write_text(
        "#!/bin/sh\n"
        'out=""\n'
        'while [ $# -gt 0 ]; do [ "$1" = "--out" ] && out="$2"; shift; done\n'
        'head -c 200 /dev/zero > "$out"\n')
    fake.chmod(0o755)
    job = {"id": "job-sep", "cancel_requested": False}
    seen = []
    real = P._register_job_pgid
    with mock.patch.object(P, "STATE_DIR", tmp_path / "state"), \
         mock.patch.object(P, "_thread_job", return_value=job), \
         mock.patch.object(P, "_register_job_pgid",
                           side_effect=lambda k, pg, job=None: (seen.append(k), real(k, pg, job))[1]):
        stem = P._a2v_separate_vocals(str(song), [str(fake)])
        assert stem.is_file() and stem.stat().st_size == 200
        assert "mux_pgid" in seen, "Stop cannot reach the separator"
        fake.write_text("#!/bin/sh\nexit 9\n")
        assert P._a2v_separate_vocals(str(song), [str(fake)]) == stem   # cached


def test_a_failed_separation_names_the_runners_last_line(tmp_path, song):
    fake = tmp_path / "fake_sep.sh"
    fake.write_text("#!/bin/sh\necho 'Traceback (most recent call last):' >&2\n"
                    "echo 'RuntimeError: could not read the song (bad header)' >&2\nexit 1\n")
    fake.chmod(0o755)
    with mock.patch.object(P, "STATE_DIR", tmp_path / "state"), \
         mock.patch.object(P, "_thread_job", return_value={"id": "j", "cancel_requested": False}):
        with pytest.raises(RuntimeError) as ei:
            P._a2v_separate_vocals(str(song), [str(fake)])
    assert "could not read the song" in str(ei.value)
    assert not list((tmp_path / "state" / P.A2V_STEM_DIR).rglob("work")), "the work dir leaked"


def test_the_separator_env_drops_the_mps_fallback_and_points_torch_home():
    with mock.patch.dict(os.environ, {"PYTORCH_ENABLE_MPS_FALLBACK": "1",
                                      "PYTORCH_MPS_FAST_MATH": "1"}):
        env = P._a2v_separator_env()
    assert "PYTORCH_ENABLE_MPS_FALLBACK" not in env
    assert "PYTORCH_MPS_FAST_MATH" not in env
    assert env["TORCH_HOME"] == str(P.a2v_separator_torch_home())


# ---------------------------------------------------------------------------
# 3. EVERY INSTALL AND EVERY UPDATE GET IT
# ---------------------------------------------------------------------------
def _install_steps():
    src = (ROOT / "install.js").read_text(encoding="utf-8")
    return src


def test_install_js_installs_vocal_separation_after_the_image_pack():
    src = _install_steps()
    assert "a2v_stems_deps.sh" in src
    assert src.index("mflux_pack.sh") < src.index("a2v_stems_deps.sh"), \
        "torch must be settled by the image pack before demucs pins it"
    line = next(l for l in src.splitlines() if "a2v_stems_deps.sh ./ltx-2-mlx" in l)
    assert len(line.strip()) <= 350, "Pinokio 8 wedges on long dispatch lines"
    assert "|| echo 'WARN" in line, "a separator hiccup must not fail the install"


def test_update_installs_vocal_separation_best_effort_after_step_7():
    src = (ROOT / "scripts" / "post_update.sh").read_text(encoding="utf-8")
    i = src.index("a2v_stems_deps.sh")
    assert src.index("patch_mflux_fbcache.py") < i
    assert "|| echo 'WARN" in src[i:i + 300]
    assert "a2v_stems_deps" not in (ROOT / "update.js").read_text(encoding="utf-8"), \
        "update.js stays thin: the work lives in post_update.sh (read after the pull)"


def test_the_installer_never_moves_torch_and_never_uses_the_cli():
    sh = (ROOT / "scripts" / "pinokio" / "a2v_stems_deps.sh").read_text(encoding="utf-8")
    assert "'demucs==4.0.1'" in sh
    assert '-c "$PINS"' in sh                       # constraints = what is on disk
    assert '"torch", "numpy"' in sh
    assert "uv pip install" in sh
    assert "--prefetch" in sh                       # weights fetched at install time
    assert "PHOSPHENE_SEPARATOR_HOME" in sh
    assert subprocess.run(["bash", "-n", str(ROOT / "scripts" / "pinokio" / "a2v_stems_deps.sh")]).returncode == 0
    for line in sh.splitlines():
        assert len(line) <= 350


def test_the_installer_refuses_a_checkout_without_a_venv(tmp_path):
    # PHOSPHENE_SEPARATOR_HOME keeps the 4.17.4 outcome record out of the real
    # mlx_models/demucs: a test must never leave a "failed" for a panel to report.
    sep_home = tmp_path / "sep"
    env = dict(os.environ, PHOSPHENE_SEPARATOR_HOME=str(sep_home))
    r = subprocess.run(["bash", str(ROOT / "scripts" / "pinokio" / "a2v_stems_deps.sh"),
                        str(tmp_path)], capture_output=True, text=True, timeout=30,
                       env=env)
    assert r.returncode != 0
    assert "no engine venv" in r.stderr
    rec = json.loads((sep_home / "last_install.json").read_text())
    assert rec["outcome"] == "failed" and rec["error_class"] == "no_venv"
    assert rec["via"] == "install" and isinstance(rec["ts"], int)


def test_separator_routes_are_registered_with_the_dispatch_signatures():
    from panel import routes as R                                     # noqa: PLC0415
    import inspect                                                    # noqa: PLC0415
    assert R.GET_ROUTES["/a2v/separator"] is routes_models.get_a2v_separator
    assert R.POST_ROUTES["/a2v/separator/install"] is routes_models.post_a2v_separator_install
    # the same arity as a neighbouring route of each kind
    assert len(inspect.signature(routes_models.get_a2v_separator).parameters) == \
        len(inspect.signature(routes_models.get_music_lora_status).parameters)
    assert len(inspect.signature(routes_models.post_a2v_separator_install).parameters) == \
        len(inspect.signature(routes_models.post_music_install).parameters)


def test_separator_routes_exist():
    h = _H()
    with mock.patch.object(P, "a2v_separator_status", return_value={"ready": True}):
        routes_models.get_a2v_separator(h, None)             # GET handlers take (h, parsed)
    assert h.payload == {"ready": True}
    h = _H({})
    with mock.patch.object(P, "a2v_separator_install_start", return_value=(202, {"ok": True})):
        routes_models.post_a2v_separator_install(h, "/a2v/separator/install", {}, "")
    assert h.status == 202


# ---------------------------------------------------------------------------
# 4. THE FORM SAYS SO AND OFFERS THE INSTALL
# ---------------------------------------------------------------------------
def _note(state: dict, wants: bool) -> str:
    src = (JS / "characters.js").read_text(encoding="utf-8")
    fn = extract_function("audioStudioSeparatorNote", src)
    return _node("function escapeHtml(s){return String(s).replace(/</g,'&lt;');}\n" + fn
                 + f"\nconsole.log(JSON.stringify(audioStudioSeparatorNote({json.dumps(state)}, {json.dumps(wants)})));")


def test_form_offers_the_install_when_missing_and_wanted():
    got = _note({"ready": False, "install": {"state": "idle"}}, True)
    assert "not installed" in got and "audioStudioInstallSeparator()" in got
    assert _note({"ready": False, "install": {"state": "idle"}}, False) == ""
    assert _note({"ready": True, "install": {"state": "idle"}}, True) == ""


def test_form_shows_progress_and_failure():
    got = _note({"ready": False, "install": {"active": True, "log": ["Resolved 26 packages"]}}, True)
    assert got.startswith("<b>Installing vocal separation") and "Resolved 26" in got
    got = _note({"ready": False, "install": {"state": "failed", "error": "no network <x>"}}, True)
    assert "did not install" in got and "&lt;x>" in got and "Try again" in got


def test_a_failed_weight_download_is_not_swallowed():
    """4.17.3 review: package installed, the 80 MB voice model failed to
    download -> ready:true, weights:false, install failed. The ready branch
    returned "" and the failure and its Try again vanished."""
    st = {"ready": True, "weights": False,
          "install": {"state": "failed", "error": "model did not load (network?)"}}
    got = _note(st, True)
    assert "did not download" in got and "Try again" in got and "network" in got
    assert _note(st, False) == ""                     # box unticked: nothing to say
    ok = {"ready": True, "weights": True, "install": {"state": "done"}}
    assert "is installed" in _note(ok, True)


def test_the_checkbox_hint_no_longer_promises_a_fallback():
    html = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
    i = html.index('id="audioStudioStemAuto"')
    label = html[i:i + 700]
    assert "falls back to the full mix" not in label
    assert 'id="audioStudioStemStatus"' in label
    assert 'onchange="audioStudioRenderSeparator()"' in label


# ---------------------------------------------------------------------------
# 5. THE CHARACTER LORA PER A2V STAGE (helper)
# ---------------------------------------------------------------------------
def _helper_ns(*names):
    import ast                                                        # noqa: PLC0415
    from contextlib import contextmanager                             # noqa: PLC0415
    tree = ast.parse(HELPER_SRC)
    defs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert sorted(d.name for d in defs) == sorted(names)
    logs: list = []
    ns: dict = {"contextmanager": contextmanager, "emit": logs.append}
    exec(compile(ast.Module(body=defs, type_ignores=[]), "mlx_warm_helper.py", "exec"), ns)
    ns["_logs"] = logs
    return ns


def test_stage_scales_parse_strictly():
    ns = _helper_ns("_a2v_lora_stage_scales")
    f = ns["_a2v_lora_stage_scales"]
    assert f([0.0, 1.0]) == (0.0, 1.0)
    assert f(["0.25", "1"]) == (0.25, 1.0)
    for bad in (None, "", [1, 1], [1.0, 1.0], [0.5], "0,1", [3, 1], [-1, 1], [None, 1]):
        assert f(bad) is None, bad


def test_the_schedule_scales_real_adapters_per_stage_and_restores_them():
    mx = pytest.importorskip("mlx.core")
    nn = pytest.importorskip("mlx.nn")
    rl = pytest.importorskip("ltx_core_mlx.loader.runtime_loras")
    ns = _helper_ns("_runtime_lora_adapters", "_a2v_lora_stage_schedule")

    a = rl.LoRALinear(nn.Linear(4, 4), mx.zeros((2, 4)), mx.zeros((4, 2)), 0.8)
    b = rl.LoRALinear(nn.Linear(4, 4), mx.zeros((2, 4)), mx.zeros((4, 2)), 1.0)

    class Dit(nn.Module):
        def __init__(self):
            super().__init__()
            self.blocks = [a, nn.Linear(4, 4), b]

    seen = []

    class Pipe:
        dit = Dit()

        def _denoise_stage1(self, *args, **kw):
            seen.append(("s1", a.lora_scale, b.lora_scale))

        def _fuse_distilled_lora(self, dit):
            seen.append(("fuse", a.lora_scale, b.lora_scale))

        def generate_and_save(self):
            self._denoise_stage1()
            self._fuse_distilled_lora(self.dit)
            seen.append(("s2", a.lora_scale, b.lora_scale))

    pipe = Pipe()
    with ns["_a2v_lora_stage_schedule"](pipe, (0.0, 1.0)):
        pipe.generate_and_save()
    assert seen == [("s1", 0.0, 0.0), ("fuse", 0.8, 1.0), ("s2", 0.8, 1.0)]
    # restored for the cached pipeline's next job, and the class seams are back
    assert (a.lora_scale, b.lora_scale) == (0.8, 1.0)
    assert "_denoise_stage1" not in vars(pipe) and "_fuse_distilled_lora" not in vars(pipe)
    # a render that dies mid-way restores too
    seen.clear()
    with pytest.raises(RuntimeError):
        with ns["_a2v_lora_stage_schedule"](pipe, (0.5, 0.25)):
            pipe._denoise_stage1()
            raise RuntimeError("boom")
    assert seen == [("s1", 0.4, 0.5)]
    assert (a.lora_scale, b.lora_scale) == (0.8, 1.0)


def test_the_schedule_does_not_keep_a_freed_dit_alive():
    """4.17.3 review: `saved` held STRONG references to every adapter (each
    wraps its base linear layer), so a DiT the low-memory path drops before
    the VAE decode stayed resident until the render returned — ~20 GB of Q8
    weights during decode on a 48-64 GB Mac."""
    import gc                                                         # noqa: PLC0415
    import weakref                                                    # noqa: PLC0415
    mx = pytest.importorskip("mlx.core")
    nn = pytest.importorskip("mlx.nn")
    rl = pytest.importorskip("ltx_core_mlx.loader.runtime_loras")
    ns = _helper_ns("_runtime_lora_adapters", "_a2v_lora_stage_schedule")

    class Dit(nn.Module):
        def __init__(self):
            super().__init__()
            self.blocks = [rl.LoRALinear(nn.Linear(4, 4), mx.zeros((2, 4)),
                                         mx.zeros((4, 2)), 0.8)]

    class Pipe:
        def __init__(self):
            self.dit = Dit()

        def _denoise_stage1(self, *args, **kw):
            pass

        def _fuse_distilled_lora(self, dit):
            pass

    pipe = Pipe()
    probe = weakref.ref(pipe.dit.blocks[0])
    with ns["_a2v_lora_stage_schedule"](pipe, (0.0, 1.0)):
        pipe._denoise_stage1()
        pipe._fuse_distilled_lora(pipe.dit)
        pipe.dit = None          # the low-memory path drops the DiT before decode
        gc.collect()
        assert probe() is None, "the schedule kept the freed DiT's adapters alive"
    # and leaving the block with nothing to restore is not an error


def test_the_schedule_is_wired_into_the_q8_a2v_render():
    block = HELPER_SRC.split('if action == "generate_a2v":')[1].split('if action == "generate_a2v_distilled":')[0]
    assert '_a2v_lora_stage_scales(p.get("lora_stage_scales"))' in block
    assert "with _a2v_lora_stage_schedule(pipe, _stage_scales):" in block
    i = block.index("with _a2v_lora_stage_schedule(pipe, _stage_scales):")
    assert "pipe.generate_and_save(**kwargs)" in block[i:i + 200]


# ---------------------------------------------------------------------------
# 6. KEEP THE FACE, FREE THE MOUTH (panel policy)
# ---------------------------------------------------------------------------
def test_the_lipsync_lora_default_leaves_the_motion_stage_alone():
    assert P.a2v_lora_stage_scales({}) == (0.0, 1.0)
    assert P.a2v_lora_stage_scales({}, q8=False) is None      # no seam on Q4
    assert P.a2v_lora_stage_scales({"a2v_lora_stages": "full"}) is None
    assert P.a2v_lora_stage_scales({"a2v_lora_stages": "0.5,1"}) == (0.5, 1.0)
    assert P.a2v_lora_stage_scales({"a2v_lora_stages": "1,1"}) is None
    for junk in ("x", "1", "3,1", "-1,1"):
        assert P.a2v_lora_stage_scales({"a2v_lora_stages": junk}) == (0.0, 1.0), junk
    job = P.make_job({"mode": "a2v", "prompt": "a singer", "audio": "/x.wav",
                      "a2v_lora_stages": "full"})
    assert job["params"]["a2v_lora_stages"] == "full", "make_job dropped the override"


def _dispatch_a2v(tmp_path, form, *, q8=True, loras=True):
    import time as _t                                                 # noqa: PLC0415
    import test_review_416_ltx as r416                                # noqa: PLC0415
    helper = r416._Helper()
    wav = tmp_path / "song.wav"
    wav.write_bytes(b"RIFF0000WAVE")
    job = P.make_job({"mode": "a2v", "prompt": "a singer", "audio": str(wav),
                      "width": "512", "height": "288", "frames": "49", **form})
    job["params"]["loras"] = [{"path": "/loras/face.safetensors", "strength": 1.0}] if loras else []
    job["started_ts"] = _t.time()
    caps = dict(P.CAPABILITIES["pro"], allows_q8=q8)
    with mock.patch.object(P, "HELPER", helper), \
         mock.patch.object(P, "OUTPUT", tmp_path), \
         mock.patch.object(P, "SYSTEM_CAPS", caps), \
         mock.patch.object(P, "ltx_pack_preflight", lambda *a, **k: None), \
         mock.patch.object(P, "hq_surface_missing", lambda *a, **k: []), \
         mock.patch.object(P, "run_ffmpeg_tracked", lambda *a, **k: ("", "")), \
         mock.patch.object(P, "write_sidecar", lambda *a, **k: True):
        try:
            P.run_job_inner(job)
        except Exception:                                             # noqa: BLE001
            pass
    assert helper.sent, "the lane never reached the helper"
    return helper.sent[0]["params"]


def test_a_q8_lipsync_with_a_lora_asks_the_helper_for_the_stage_split(tmp_path):
    assert _dispatch_a2v(tmp_path, {})["lora_stage_scales"] == [0.0, 1.0]
    assert "lora_stage_scales" not in _dispatch_a2v(tmp_path, {"a2v_lora_stages": "full"})
    assert "lora_stage_scales" not in _dispatch_a2v(tmp_path, {}, loras=False)
    assert "lora_stage_scales" not in _dispatch_a2v(tmp_path, {}, q8=False)


def test_the_form_says_so_in_one_plain_line():
    src = (JS / "characters.js").read_text(encoding="utf-8")
    fn = extract_function("a2vLoraStageLine", src)
    got = _node(fn + "\nconsole.log(JSON.stringify([a2vLoraStageLine(false), a2vLoraStageLine(true)]));")
    assert "face detail only" in got[0] and "stiffening the mouth" in got[0]
    assert "whole lip-sync render" in got[1]
    note = extract_function("audioStudioRenderLoraNote", src)
    assert "a2vLoraStageLine(" in note
