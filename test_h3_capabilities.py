#!/usr/bin/env python3
"""Contract gate: the 4.19 Hailuo H3 capabilities.

* Carried context — a chained H3 window continues from the last 17 frames of
  the previous one AND their sound (`--chain-context-frames 17`), on by
  default, off with `h3_continuity=off`, never sent to a runner without it.
* Keyframes — Start & end frame (the start optional on H3), Keyframes 3-8
  (`--keyframe PATH@FRAME`), each still pre-fitted to the canvas.
* Extend — any clip, `--extend-from` + `--extend-frames`, windows from the
  seconds asked for.
* Lip-sync — the user's track held (`--audio-drive`), the separated vocal when
  asked, the original delivered (`--audio-mux`), the sync contract appended once.
* Add sound — `--video-to-audio`, H3's alone (LTX excludes the mode).
* Fast HD — the 640×384 Fast pass + Upscale & Face Fix, forced together and
  priced whole.

Every mode is probed on the INSTALLED runner, so an older pack falls back or
refuses with the update sentence instead of an argparse error. No model, no
GPU, no network: the runner is intercepted at Popen.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import ExitStack
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_TMP = Path(tempfile.mkdtemp(prefix="phos-h3-cap-"))
for _k, _d in (("LTX_STATE_DIR", "state"), ("LTX_OUTPUT_DIR", "out"),
               ("LTX_UPLOADS_DIR", "uploads")):
    (_TMP / _d).mkdir(parents=True, exist_ok=True)
    os.environ[_k] = str(_TMP / _d)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ["LTX_H3_FORCE_CAPABLE"] = "1"
os.environ.setdefault("LTX_PORT", "8303")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402

ALL_FLAGS = ("--lora", "--sigma-subset", "--lora-adaln", "--first-frame", "--seam-colour-frames",
             "--chain-windows", "--chain-prompts", "--last-frame", "--keyframe",
             "--chain-context-frames", "--extend-from", "--audio-drive",
             "--video-to-audio")


def _png(name: str, size=(1024, 576)) -> Path:
    from PIL import Image
    p = _TMP / name
    Image.new("RGB", size, (90, 60, 40)).save(p)
    return p


def _media(name: str) -> Path:
    p = _TMP / name
    p.write_bytes(b"\0" * 64)
    return p


class _Env(unittest.TestCase):
    flags = ALL_FLAGS

    def setUp(self):
        self.st = ExitStack()
        self.logs: list[str] = []
        flags = self.flags
        for name, val in {
                "h3_available": lambda: True,
                "h3_capable": lambda: True,
                "_h3_runner_has_flag": lambda flag: flag in flags,
                "push": self.logs.append}.items():
            self.st.enter_context(unittest.mock.patch.object(P, name, val))

    def tearDown(self):
        self.st.close()

    def dispatch(self, params: dict, probes: dict | None = None) -> tuple[list[str], dict]:
        seen = {}

        class Intercept(Exception):
            pass

        def popen(cmd, **kw):
            seen["cmd"] = cmd
            raise Intercept()

        job = {"id": "cap-1", "params": {
            "engine": "h3", "mode": "t2v", "prompt": "a man walks", "seed": "7",
            "h3_tier": "draft_5s", "h3_turbo": False, "h3_tristep": False, "steps": 9,
            **params}}
        paths = dict(missing=[], repairable=False, dit=_TMP / "dit", python=sys.executable,
                     runner=_TMP / "gen.py", compact_root=_TMP / "c", text_config=_TMP / "t")
        ff = _TMP / "ffmpeg"
        ff.write_text("")
        with ExitStack() as st:
            for name, val in {
                    "h3_paths": lambda: paths, "h3_lora_max_stack": lambda: 4,
                    "h3_dit_choice": lambda: ("bf16", None),
                    "output_codec_settings": lambda: {"crf": "18", "pix_fmt": "yuv420p"},
                    "FFMPEG": ff,
                    "_probe_video_frames": lambda path: 73,
                    "_probe_video_fps": lambda path: 24.0,
                    "probe_media_duration": lambda path: 10.0,
                    **(probes or {})}.items():
                st.enter_context(unittest.mock.patch.object(P, name, val))
            for name in ("h3_supports_memory_limits", "h3_supports_prompt_cache",
                         "h3_live_preview_ready", "h3_supports_stage_a", "h3_supports_tae_draft"):
                st.enter_context(unittest.mock.patch.object(P, name, lambda: False))
            st.enter_context(unittest.mock.patch.object(P.HELPER, "is_alive", lambda: False))
            st.enter_context(unittest.mock.patch.object(P.subprocess, "Popen", popen))
            with self.assertRaises(Intercept):
                P.run_h3_job_inner(job)
        return seen["cmd"], job["params"]

    @staticmethod
    def val(cmd, flag):
        return cmd[cmd.index(flag) + 1]

    @staticmethod
    def vals(cmd, flag):
        return [cmd[i + 1] for i, a in enumerate(cmd) if a == flag]


class Probes(_Env):
    def test_every_mode_has_its_flag(self):
        for mode in ("keyframe", "extend", "a2v", "v2a"):
            self.assertTrue(P.h3_mode_supported(mode), mode)
        self.assertTrue(P.h3_mode_supported("t2v"))

    def test_an_old_runner_serves_none_of_them(self):
        with unittest.mock.patch.object(P, "_h3_runner_has_flag", lambda f: f == "--first-frame"):
            for mode in ("keyframe", "extend", "a2v", "v2a"):
                self.assertFalse(P.h3_mode_supported(mode), mode)
            self.assertTrue(P.h3_mode_supported("i2v"))

    def test_registry_and_status(self):
        h3 = P.engine_by_id("h3")
        for mode in ("keyframe", "extend", "a2v", "v2a"):
            self.assertTrue(P.engine_serves_mode(h3, mode), mode)
        # Add sound is H3's alone.
        self.assertFalse(P.engine_serves_mode(P.engine_by_id("ltx"), "v2a"))
        self.assertIn("v2a", P.QUEUEABLE_VIDEO_MODES)
        st = P.h3_status()
        for key in ("keyframes", "continuation", "extend", "audio_drive", "video_to_audio"):
            self.assertIn(key, st)


class MakeJobAllowlist(_Env):
    def test_new_fields_survive_queue_add(self):
        job = P.make_job({"engine": "h3", "mode": "t2v", "prompt": "x",
                          "h3_quality": "standard", "h3_length": "10s",
                          "h3_continuity": "off", "h3_extend_seconds": "7.5"})
        self.assertFalse(job["params"]["h3_continuity"])
        self.assertEqual(job["params"]["h3_extend_seconds"], 7.5)
        job = P.make_job({"engine": "h3", "mode": "t2v", "prompt": "x"})
        self.assertTrue(job["params"]["h3_continuity"])

    def test_add_sound_always_means_h3(self):
        job = P.make_job({"mode": "v2a", "prompt": "rain", "video_path": str(_media("c.mp4"))})
        self.assertEqual(job["params"]["engine"], "h3")
        self.assertEqual(job["params"]["mode"], "v2a")
        self.assertIsNone(P.job_input_refusal(job["params"]))
        self.assertIn("Add sound", P.job_input_refusal({"mode": "v2a", "video_path": ""}))

    def test_lipsync_keeps_its_own_length(self):
        job = P.make_job({"engine": "h3", "mode": "a2v", "prompt": "x", "frames": "200",
                          "audio": str(_media("v.wav")), "h3_quality": "high", "h3_length": "5s"})
        self.assertEqual(job["params"]["engine"], "h3")
        self.assertEqual(job["params"]["h3_a2v_frames"], 200)

    def test_an_old_runner_renders_keyframes_on_ltx(self):
        with unittest.mock.patch.object(P, "_h3_runner_has_flag", lambda f: f == "--first-frame"):
            job = P.make_job({"engine": "h3", "mode": "keyframe", "prompt": "x"})
        self.assertEqual(job["params"]["engine"], "ltx")
        self.assertTrue(any("once its runner is updated" in m for m in self.logs))

    def test_fast_hd_forces_its_recipe(self):
        with unittest.mock.patch.object(P, "h3_tristep_status", lambda: {"available": True,
                                                                          "supported": True,
                                                                          "missing": []}), \
                unittest.mock.patch.object(P, "face_fix_adapter_ready", lambda: True):
            job = P.make_job({"engine": "h3", "mode": "t2v", "prompt": "x",
                              "h3_quality": "fast_hd", "h3_length": "5s",
                              "h3_tristep": "0", "h3_upscale": "fit_720p"})
        p = job["params"]
        self.assertEqual(p["h3_quality"], "fast_hd")
        self.assertTrue(p["h3_tristep"])
        self.assertEqual(p["h3_upscale"], "ltx_x2")
        self.assertEqual((p["width"], p["height"]), (640, 384))


class FastHdCells(unittest.TestCase):
    def test_priced_whole_and_named_by_both_sizes(self):
        c = P.H3_TIERS["fast_hd_5s"]
        self.assertTrue(c["fast_hd"])
        self.assertEqual((c["final_width"], c["final_height"]), (1280, 768))
        self.assertAlmostEqual(c["eta_min"], c["tristep_min"] + c["facefix_min"], delta=0.01)
        q = P.H3_QUALITIES["fast_hd"]
        self.assertIn("→ 1280×768", q["spec"])

    def test_gate_names_what_is_missing(self):
        cell = P.H3_TIERS["fast_hd_5s"]
        with unittest.mock.patch.object(P, "h3_available", lambda: True), \
                unittest.mock.patch.object(P, "h3_tristep_status", lambda: {"available": False}):
            ok, why = P.h3_cell_gate(cell)
        self.assertFalse(ok)
        self.assertIn("Fast", why)
        with unittest.mock.patch.object(P, "h3_available", lambda: True), \
                unittest.mock.patch.object(P, "h3_tristep_status", lambda: {"available": True}), \
                unittest.mock.patch.object(P, "face_fix_adapter_ready", lambda: False):
            ok, why = P.h3_cell_gate(cell)
        self.assertFalse(ok)
        self.assertIn("Upscaler", why)

    def test_the_chained_fix_runs_the_faithful_depth(self):
        queued = []
        job = {"id": "fh-1"}
        p = {"h3_quality": "fast_hd", "prompt": "x", "seed_used": 3, "label": "shot"}
        clip = _media("fh.mp4")
        with unittest.mock.patch.object(P, "make_job", lambda form: queued.append(form) or
                                        {"id": "n", "params": {}}), \
                unittest.mock.patch.object(P, "persist_queue", lambda: None), \
                unittest.mock.patch.object(P, "_probe_video_frames", lambda path: 124), \
                unittest.mock.patch.object(P, "push", lambda m: None):
            P._chain_upscale_after_h3(job, p, clip)
        self.assertEqual(queued[0]["upscale_steps"], str(P.FAST_HD_FACEFIX_STEPS))
        self.assertEqual(queued[0]["upscale_start"], "source")


class ChainContinuity(_Env):
    def test_chained_windows_carry_context_by_default(self):
        cmd, p = self.dispatch({"h3_tier": "standard_10s"})
        self.assertEqual(self.val(cmd, "--chain-context-frames"), "17")
        self.assertEqual(self.val(cmd, "--seam-colour-frames"), "24")
        self.assertEqual(p["h3_context_frames"], 17)

    def test_off_keeps_the_old_hand_over(self):
        cmd, _ = self.dispatch({"h3_tier": "standard_10s", "h3_continuity": False})
        self.assertNotIn("--chain-context-frames", cmd)
        self.assertNotIn("--seam-colour-frames", cmd)

    def test_single_window_and_old_runner_send_nothing(self):
        cmd, _ = self.dispatch({"h3_tier": "standard_5s"})
        self.assertNotIn("--chain-context-frames", cmd)
        with unittest.mock.patch.object(P, "_h3_runner_has_flag",
                                        lambda f: f in ("--chain-windows",)):
            cmd, _ = self.dispatch({"h3_tier": "standard_10s"})
        self.assertNotIn("--chain-context-frames", cmd)


class Keyframes(_Env):
    def test_start_and_end(self):
        a, b = _png("a.png"), _png("b.png", (800, 800))
        cmd, _ = self.dispatch({"mode": "keyframe", "h3_tier": "high_5s",
                                "start_image": str(a), "end_image": str(b)})
        self.assertIn("--first-frame", cmd)
        last = Path(self.val(cmd, "--last-frame"))
        from PIL import Image
        self.assertEqual(Image.open(last).size, (1024, 576))   # pre-fitted, never stretched
        self.assertIn("end_frame", last.name)

    def test_end_only_is_the_official_last_frame_task(self):
        b = _png("b2.png")
        cmd, _ = self.dispatch({"mode": "keyframe", "h3_tier": "high_5s", "end_image": str(b)})
        self.assertNotIn("--first-frame", cmd)
        self.assertIn("--last-frame", cmd)

    def test_beats_land_on_their_frames(self):
        imgs = [_png(f"k{i}.png") for i in range(4)]
        kfs = [{"image_path": str(imgs[0]), "frame_index": 0},
               {"image_path": str(imgs[1]), "frame_index": 60},
               {"image_path": str(imgs[2]), "frame_index": 400},      # clamped inside
               {"image_path": str(imgs[3]), "frame_index": 120}]
        cmd, _ = self.dispatch({"mode": "keyframe", "h3_tier": "standard_10s",
                                "keyframes_json": json.dumps(kfs)})
        beats = self.vals(cmd, "--keyframe")
        self.assertEqual([b.rsplit("@", 1)[1] for b in beats], ["60", "241"])
        self.assertIn("--last-frame", cmd)

    def test_no_end_frame_is_refused_before_the_gpu(self):
        with self.assertRaises(RuntimeError) as cm:
            self.dispatch({"mode": "keyframe", "h3_tier": "high_5s",
                           "start_image": str(_png("s.png"))})
        self.assertIn("end frame", str(cm.exception))

    def test_old_runner_says_update(self):
        with unittest.mock.patch.object(P, "_h3_runner_has_flag", lambda f: f == "--first-frame"):
            with self.assertRaises(RuntimeError) as cm:
                self.dispatch({"mode": "keyframe", "end_image": str(_png("e.png"))})
        self.assertIn(P.H3_RUNNER_BEHIND, str(cm.exception))


class Extend(_Env):
    def test_seconds_become_windows_and_exact_frames(self):
        src = _media("src.mp4")
        cmd, p = self.dispatch({"mode": "extend", "h3_tier": "high_5s",
                                "video_path": str(src), "h3_extend_seconds": 7.5})
        self.assertEqual(self.val(cmd, "--extend-from"), str(src))
        self.assertEqual(self.val(cmd, "--extend-frames"), "180")
        self.assertEqual(self.val(cmd, "--chain-windows"), "2")
        self.assertEqual(self.val(cmd, "--chain-context-frames"), "17")
        self.assertNotIn("--chain-total-frames", cmd)
        self.assertEqual(self.val(cmd, "--frames"), "124")
        self.assertEqual(p["frames"], 73 + 180)

    def test_capped_at_fifteen_seconds(self):
        cmd, _ = self.dispatch({"mode": "extend", "video_path": str(_media("s2.mp4")),
                                "h3_extend_seconds": 99})
        self.assertEqual(self.val(cmd, "--extend-frames"), "360")

    def test_an_over_long_source_is_refused_before_the_gpu(self):
        # The runner holds the whole decoded source through the render.
        with self.assertRaises(RuntimeError) as ctx:
            self.dispatch({"mode": "extend", "video_path": str(_media("long.mp4"))},
                          probes={"probe_media_duration": lambda path: 61.0})
        self.assertIn("60s", str(ctx.exception))


class LipSync(_Env):
    def test_track_held_and_contract_added_once(self):
        voice = _media("voice.wav")
        face = _png("face.png")
        cmd, p = self.dispatch({"mode": "a2v", "h3_tier": "high_5s", "audio": str(voice),
                                "image": str(face), "h3_a2v_frames": 240,
                                "audio_start_time": 1.5,
                                "prompt": "integrated_multimodal_description: [Shot 1] a man talks.\n\n"
                                          "overall_soundscape: his voice.\n\nnon_diegetic_music: N/A"})
        self.assertEqual(self.val(cmd, "--audio-drive"), str(voice))
        self.assertEqual(self.val(cmd, "--audio-drive-offset"), "1.500")
        self.assertNotIn("--audio-mux", cmd)
        self.assertEqual(self.val(cmd, "--chain-windows"), "2")
        self.assertEqual(self.val(cmd, "--chain-total-frames"), "204")   # 8.5 s left after 1.5 s
        self.assertIn("--first-frame", cmd)
        prompt = next(a for a in cmd if a.startswith("integrated_multimodal_description"))
        self.assertEqual(prompt.count("lip-syncs every syllable"), 1)
        self.assertLess(prompt.index("lip-syncs"), prompt.index("overall_soundscape"))

    def test_voice_only_conditions_on_the_stem_and_delivers_the_mix(self):
        song = _media("song.wav")
        stem = _media("vocals.wav")
        with unittest.mock.patch.object(P, "a2v_conditioning_audio",
                                        lambda p, a: (str(stem), "the model listens to the separated vocal")):
            cmd, _ = self.dispatch({"mode": "a2v", "audio": str(song), "h3_a2v_frames": 72,
                                    "audio_stem_auto": "on"})
        self.assertEqual(self.val(cmd, "--audio-drive"), str(stem))
        self.assertEqual(self.val(cmd, "--audio-mux"), str(song))
        self.assertEqual(self.val(cmd, "--frames"), "73")             # one short window
        self.assertNotIn("--chain-windows", cmd)

    def test_a_short_line_is_not_padded_with_silence(self):
        # 0.5 s of voice: one smallest window renders, delivery is trimmed to the line.
        cmd, p = self.dispatch({"mode": "a2v", "audio": str(_media("short.wav")),
                                "h3_a2v_frames": 120},
                               probes={"probe_media_duration": lambda path: 0.5})
        self.assertEqual(self.val(cmd, "--frames"), "22")
        self.assertEqual(self.val(cmd, "--chain-total-frames"), "12")
        self.assertEqual(p["frames"], 12)

    def test_start_past_the_end_is_refused(self):
        with self.assertRaises(RuntimeError):
            self.dispatch({"mode": "a2v", "audio": str(_media("v2.wav")), "audio_start_time": 12})


class AddSound(_Env):
    def test_picture_held_windows_from_the_clip(self):
        clip = _media("silent.mp4")
        cmd, p = self.dispatch({"mode": "v2a", "h3_tier": "standard_5s",
                                "video_path": str(clip), "prompt": "rain on a tin roof"},
                               probes={"probe_media_duration": lambda path: 7.0})
        self.assertEqual(self.val(cmd, "--video-to-audio"), str(clip))
        self.assertNotIn("--chain-windows", cmd)
        self.assertNotIn("--chain-total-frames", cmd)
        self.assertEqual(self.val(cmd, "--chain-context-frames"), "17")
        self.assertEqual(p["frames"], 168)

    def test_too_long_is_refused(self):
        with self.assertRaises(RuntimeError) as cm:
            self.dispatch({"mode": "v2a", "video_path": str(_media("long.mp4"))},
                          probes={"probe_media_duration": lambda path: 600.0})
        self.assertIn("Cut it shorter", str(cm.exception))


class AddSoundDelivery(_Env):
    """A whole Add-sound job through run_h3_job_inner with a fake runner that
    exits 0: no export re-encode, no Face Fix chained, the source's own size
    in the sidecar."""

    def test_no_export_or_face_fix_on_the_users_picture(self):
        clip = _media("scored.mp4")
        calls = {"plan": [], "chain": [], "sidecar": None}

        class FakeProc:
            def __init__(self, cmd, **kw):
                out = Path(cmd[cmd.index("-o") + 1])
                out.write_bytes(b"\0" * 128)
                r, w = os.pipe()
                os.close(w)
                self.stdout = os.fdopen(r, "r")
                self.pid = os.getpid()
                self.returncode = 0

            def wait(self, timeout=None):
                return 0

            def poll(self):
                return 0

        job = {"id": "v2a-1", "params": {
            "engine": "h3", "mode": "v2a", "prompt": "rain", "seed": "7",
            "h3_tier": "draft_5s", "h3_turbo": False, "h3_tristep": False, "steps": 9,
            "video_path": str(clip), "h3_upscale": "ltx_x2"}}
        paths = dict(missing=[], repairable=False, dit=_TMP / "dit", python=sys.executable,
                     runner=_TMP / "gen.py", compact_root=_TMP / "c", text_config=_TMP / "t")
        ff = _TMP / "ffmpeg"
        ff.write_text("")
        with ExitStack() as st:
            for name, val in {
                    "h3_paths": lambda: paths, "h3_lora_max_stack": lambda: 4,
                    "h3_dit_choice": lambda: ("bf16", None),
                    "output_codec_settings": lambda: {"crf": "18", "pix_fmt": "yuv420p"},
                    "FFMPEG": ff, "probe_media_duration": lambda path: 4.0,
                    "_probe_video_dims": lambda path: (1024, 576),
                    "_h3_clip_is_blank": lambda path: False,
                    "_register_job_pgid": lambda *a, **k: None,
                    "_proc_guard_write": lambda *a, **k: None,
                    "_proc_guard_clear": lambda *a, **k: None,
                    "compute_upscale_plan": lambda w, h, m: calls["plan"].append((w, h, m)),
                    "_chain_upscale_after_h3": lambda *a: calls["chain"].append(a),
                    "write_sidecar": lambda path, sc: calls.__setitem__("sidecar", sc)}.items():
                st.enter_context(unittest.mock.patch.object(P, name, val))
            for name in ("h3_supports_memory_limits", "h3_supports_prompt_cache",
                         "h3_live_preview_ready", "h3_supports_stage_a", "h3_supports_tae_draft"):
                st.enter_context(unittest.mock.patch.object(P, name, lambda: False))
            st.enter_context(unittest.mock.patch.object(P.HELPER, "is_alive", lambda: False))
            st.enter_context(unittest.mock.patch.object(P.subprocess, "Popen", FakeProc))
            st.enter_context(unittest.mock.patch.object(P.os, "getpgid", lambda pid: pid))
            P.run_h3_job_inner(job)
        self.assertEqual([m for _, _, m in calls["plan"]], ["off"])
        self.assertEqual(calls["chain"], [])
        sp = calls["sidecar"]["params"]
        self.assertEqual((sp["width"], sp["height"]), (1024, 576))
        self.assertEqual(sp["h3_upscale"], "off")
        self.assertEqual(sp["mode"], "v2a")
        self.assertEqual(sp["frames"], 96)


class A2vPrompt(unittest.TestCase):
    def test_plain_prompt(self):
        self.assertTrue(P.h3_a2v_prompt("a man talks").endswith(P.H3_A2V_CONTRACT))
        self.assertEqual(P.h3_a2v_prompt("he lip-syncs it"), "he lip-syncs it")


class Windows(unittest.TestCase):
    def test_window_math(self):
        self.assertEqual(P.h3_windows_for(124), 1)
        self.assertEqual(P.h3_windows_for(125), 2)
        self.assertEqual(P.h3_windows_for(247), 2)
        self.assertEqual(P.h3_windows_for(248), 3)
        self.assertEqual(P.h3_window_for_short(72), 73)
        self.assertEqual(P.h3_window_for_short(10), 22)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# The browser half, executed in node against stubs (scripts/extract_panel_js).
import shutil as _shutil  # noqa: E402
import subprocess as _sp  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from extract_panel_js import extract_function  # noqa: E402

_NODE = _shutil.which("node")
_JS = {n: (ROOT / "webapp" / "js" / f"{n}.js").read_text(encoding="utf-8")
       for n in ("queue", "engines", "characters")}


def _node(names: tuple, calls: list[str], prelude: str, src: str) -> list:
    if _NODE is None:
        raise unittest.SkipTest("node not on PATH")
    fn = "\n".join(extract_function(n, src) for n in names)
    script = prelude + "\n" + fn + "\nconsole.log(JSON.stringify([\n  " + ",\n  ".join(calls) + "\n]));"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        r = _sp.run([_NODE, path], capture_output=True, text=True, errors="replace", timeout=60)
    finally:
        Path(path).unlink(missing_ok=True)
    if r.returncode != 0:
        raise AssertionError(r.stderr[-2000:])
    return json.loads(r.stdout.strip().splitlines()[-1])


class UseThisAudioForH3(unittest.TestCase):
    def test_h3_lipsync_target_only_when_the_runner_can(self):
        out = _node(("audioUseTargets",), [
            "audioUseTargets({}, {h3: {available: true, audio_drive: true}}).map(t => t.id)",
            "audioUseTargets({}, {h3: {available: true, audio_drive: false}}).map(t => t.id)",
            "audioUseTargets({}, {}).map(t => t.id)",
        ], "", _JS["queue"])
        self.assertEqual(out[0], ["lipsync", "lipsync_h3", "musicvideo", "soundtrack", "cover"])
        self.assertNotIn("lipsync_h3", out[1])
        self.assertNotIn("lipsync_h3", out[2])

    def test_choosing_it_sets_the_form_engine(self):
        pre = r"""
const calls = [];
globalThis.document = { getElementById: () => null, removeEventListener(){} };
globalThis.window = { _ENGINE_PROBES: {} };
function findOutputByPath(p) { return { path: p, clip_sec: 4, url: 'u' }; }
function closeAudioUseMenu() {}
function phosToast() {}
function setA2vEngine(e) { calls.push(['engine', e]); }
function audioStudioUseAudio(p) { calls.push(['lipsync', p]); }
"""
        out = _node(("audioUseApply",), [
            "(() => { audioUseApply('/o/v.wav', 'lipsync_h3'); audioUseApply('/o/v.wav', 'lipsync'); return calls; })()",
        ], pre, _JS["queue"])
        self.assertEqual(out[0], [["engine", "h3"], ["lipsync", "/o/v.wav"],
                                  ["engine", "ltx"], ["lipsync", "/o/v.wav"]])


class H3ModeGatesInTheBrowser(unittest.TestCase):
    PRE = r"""
globalThis.H3_MODE_CAP = { keyframe: 'keyframes', extend: 'extend', a2v: 'audio_drive', v2a: 'video_to_audio' };
globalThis.window = { _ENGINE_PROBES: { h3: { available: true, keyframes: true, extend: false,
                                              audio_drive: true, video_to_audio: true } } };
"""

    def test_can_serve_follows_the_probes(self):
        out = _node(("h3CanServe",), [
            "['t2v','keyframe','extend','a2v','v2a'].map(h3CanServe)",
            "(() => { window._ENGINE_PROBES.h3.available = false; return h3CanServe('t2v'); })()",
        ], self.PRE, _JS["engines"])
        self.assertEqual(out[0], [True, True, False, True, True])
        self.assertFalse(out[1])


class FooterEstimates(unittest.TestCase):
    PRE = r"""
const els = { extend_seconds: { value: '7.5' } };
globalThis.document = { getElementById: id => els[id] || null };
const CELLS = {
  'draft_5s': { key: 'draft_5s', quality: 'draft', quality_label: 'Draft', tristep_min: 3, facefix_min: 2.5 },
  'fast_hd_5s': { key: 'fast_hd_5s', quality: 'fast_hd', quality_label: 'Fast HD', fast_hd: true,
                  width: 640, height: 384, final_width: 1280, final_height: 768,
                  tristep_min: 3, facefix_min: 5.5 },
};
let CUR = 'draft_5s';
var currentMode = 't2v';
function h3CurrentCell() { return CELLS[CUR]; }
function h3CellFor(q, l) { return CELLS[q + '_' + l]; }
function h3TriStepOn(c) { return true; }
function _h3TriStepMin(c) { return c.tristep_min; }
function h3FaceFixOn() { return false; }
function h3FmtEtaMin(m) { return '~' + Math.max(1, Math.round(m)) + ' min'; }
"""

    def test_extend_add_sound_and_fast_hd_lines(self):
        out = _node(("h3CellEtaMin", "h3EstimateLine"), [
            "(() => { currentMode = 'extend'; return h3EstimateLine(); })()",
            "(() => { currentMode = 'v2a'; return h3EstimateLine(); })()",
            "(() => { currentMode = 't2v'; CUR = 'fast_hd_5s'; return h3EstimateLine(); })()",
            "h3CellEtaMin(CELLS['fast_hd_5s'])",
            "(() => { currentMode = 'v2a'; CUR = 'fast_hd_5s'; return h3EstimateLine(); })()",
        ], self.PRE, _JS["engines"])
        self.assertEqual(out[4], "Add sound · picture kept · ≈ 3 min per 5 s of clip")
        self.assertEqual(out[0], "Extend +7.5 s · Draft · 2 windows ≈ 6 min")
        self.assertEqual(out[1], "Add sound · picture kept · ≈ 3 min per 5 s of clip")
        self.assertEqual(out[2], "Fast HD · 640×384 → 1280×768 ≈ 9 min")
        self.assertAlmostEqual(out[3], 8.5)


class LipSyncLoadParamsRoundTrip(unittest.TestCase):
    """An H3 lip-sync sidecar reopens the Lip-sync form on H3, at its canvas and length."""
    PRE = r"""
const calls = [];
const els = {};
const mk = id => (els[id] = els[id] || { id, value: '', checked: false, dataset: {}, classList: { add(){}, remove(){} },
                                       scrollIntoView(){} });
globalThis.document = { getElementById: mk };
globalThis.localStorage = { setItem(){}, getItem(){ return null; } };
globalThis.setTimeout = () => 0;
globalThis.AUDIO_STUDIO = {};
var A2V_H3_QUALITY = 'high';
const A2V_H3_QUALITY_KEY = 'k';
function audioModeSet() {}
function workflowSwitch() {}
function audioStudioRenderSlots() {}
function audioStudioDurationChanged(v) { calls.push(['dur', v]); }
function audioConditioningScaleReset() {}
function audioConditioningScaleChanged() {}
function audioStudioRenderLoraNote() {}
function setA2vEngine(e) { calls.push(['engine', e]); }
"""

    def test_round_trip(self):
        out = _node(("a2vLoadParams",), [
            "(() => { a2vLoadParams({mode:'a2v', engine:'h3', h3_quality:'standard', h3_a2v_frames:240,"
            " frames:124, audio:'/a.wav', prompt:'p'}); return [calls.slice(), A2V_H3_QUALITY]; })()",
            "(() => { calls.length = 0; a2vLoadParams({mode:'a2v', engine:'ltx', frames:121, audio:'/a.wav'});"
            " return calls; })()",
        ], self.PRE, _JS["characters"])
        calls, quality = out[0]
        self.assertEqual(quality, "standard")
        self.assertIn(["engine", "h3"], calls)
        self.assertEqual(calls[-1], ["dur", "10"])
        self.assertIn(["engine", "ltx"], out[1])
