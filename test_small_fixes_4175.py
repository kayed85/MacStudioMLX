"""4.17.5 (fleet) - the small ones, each a field failure on 4.17.x.

  * "ffprobe not found on PATH. Install it with: brew install ffmpeg" on a
    Control export: the engine library looks ffmpeg AND ffprobe up on PATH,
    and every child spawn prepended only FFMPEG's folder - an ffprobe the
    panel had resolved from another folder was invisible to the child.
  * A YuE2 song failed after the whole model load on "unsupported tensor
    targets: diffusion_model..." - a video (LTX) LoRA had been copied into the
    music LoRA folder and picked. It is now refused BY NAME at the queue (and
    the picker says what the file is), from the safetensors header alone.
  * "[METAL] Command buffer execution failed: Impacting Interactivity" - the
    GPU watchdog under its other name - was filed as `other` (4 renders).
"""
from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")

import mlx_ltx_panel as P                                            # noqa: E402
from scripts.pinokio import music_lora_fetch as M                   # noqa: E402


def write_safetensors(path: Path, keys: list[str]) -> None:
    header = {k: {"dtype": "BF16", "shape": [1, 1], "data_offsets": [0, 2]} for k in keys}
    raw = json.dumps(header).encode()
    path.write_bytes(struct.pack("<Q", len(raw)) + raw + b"\0\0")


class FfprobeReachesTheChild(unittest.TestCase):
    def test_both_folders_lead_the_path(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            ffmpeg, ffprobe = Path(a) / "ffmpeg", Path(b) / "ffprobe"
            ffmpeg.write_text(""); ffprobe.write_text("")
            with mock.patch.object(P, "FFMPEG_BIN", Path(a)), \
                    mock.patch.object(P, "FFPROBE", ffprobe):
                path = P.media_tool_path(f"/usr/bin:{a}:/bin")
            parts = path.split(":")
            self.assertEqual(parts[:2], [a, b])
            self.assertEqual(parts.count(a), 1, "no duplicate entries")
            self.assertIn("/usr/bin", parts)

    def test_a_missing_ffprobe_adds_nothing(self):
        with tempfile.TemporaryDirectory() as a:
            with mock.patch.object(P, "FFMPEG_BIN", Path(a)), \
                    mock.patch.object(P, "FFPROBE", Path("/nonexistent/ffprobe")):
                self.assertEqual(P.media_tool_path("/bin"), f"{a}:/bin")

    def test_no_child_spawn_prepends_ffmpeg_alone(self):
        src = (ROOT / "mlx_ltx_panel.py").read_text()
        self.assertNotIn('env["PATH"] = f"{FFMPEG_BIN}:', src)
        self.assertGreaterEqual(src.count('media_tool_path(env.get("PATH", ""))'), 5)


class AVideoLoraIsNotAMusicVoice(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="music-loras-"))
        (self.tmp / "user").mkdir()
        self.video = self.tmp / "user" / "my_ltx_style.safetensors"
        write_safetensors(self.video, [
            "diffusion_model.transformer_blocks.0.attn1.to_q.lora_A.weight",
            "diffusion_model.transformer_blocks.0.attn1.to_q.lora_B.weight"])
        self.voice = self.tmp / "user" / "my_voice.safetensors"
        write_safetensors(self.voice, [
            "base_model.model.model.layers.0.self_attn.q_proj.lora_A.weight",
            "base_model.model.model.layers.0.self_attn.q_proj.lora_B.weight"])
        self.nar = self.tmp / "user" / "nar.safetensors"
        write_safetensors(self.nar, ["layers.3.nar_mlp.up_proj.lora_A",
                                     "layers.3.nar_mlp.up_proj.lora_B"])

    def test_the_header_check(self):
        self.assertIn("video LoRA", M.music_adapter_problem(self.video))
        self.assertIn(self.video.name, M.music_adapter_problem(self.video))
        self.assertEqual(M.music_adapter_problem(self.voice), "")
        self.assertEqual(M.music_adapter_problem(self.nar), "")
        junk = self.tmp / "user" / "junk.safetensors"
        junk.write_bytes(b"not a safetensors file")
        self.assertEqual(M.music_adapter_problem(junk), "",
                         "an unreadable file is the engine's to name, not ours")

    def test_the_engine_agrees_on_what_a_music_adapter_is(self):
        """yue2_lora's own rule, applied to the same keys - the two
        definitions must not drift apart."""
        try:
            from scripts.music import yue2_lora as Y
        except Exception as exc:                                  # noqa: BLE001
            self.skipTest(f"engine module not importable here: {exc}")
        for path, ok in ((self.voice, True), (self.nar, True), (self.video, False)):
            keys = M.safetensors_keys(path)
            engine_ok = any(Y._describe(Y.normalize_key(k).rsplit(".", 1)[0])
                            for k in keys)
            self.assertEqual(engine_ok, ok, path.name)

    def test_the_queue_refuses_it_by_name(self):
        with mock.patch.object(P, "MUSIC_LORAS", self.tmp):
            got = P.music_lora_foreign(["user/my_ltx_style.safetensors:0.8"])
            self.assertEqual(len(got), 1)
            self.assertIn("my_ltx_style.safetensors", got[0])
            self.assertEqual(P.music_lora_foreign(["user/my_voice.safetensors:1.0"]), [])
        src = (ROOT / "mlx_ltx_panel.py").read_text()
        i = src.index("missing = music_lora_unresolved(form.get(\"music_loras\"))")
        self.assertIn("music_lora_foreign(form.get(\"music_loras\"))", src[i:i + 800])

    def test_the_picker_says_what_the_file_is(self):
        entries = {e["file"]: e for e in M.pack_adapters(self.tmp)}
        self.assertIn("video LoRA", entries["my_ltx_style.safetensors"]["note"])
        self.assertTrue(entries["my_ltx_style.safetensors"].get("problem"))
        self.assertNotIn("problem", entries["my_voice.safetensors"])
        js = (ROOT / "webapp" / "js" / "characters.js").read_text()
        self.assertIn("a.problem ? 'disabled' : ''", js)


class TheWatchdogUnderItsOtherName(unittest.TestCase):
    def test_impacting_interactivity_is_the_watchdog(self):
        line = ("[METAL] Command buffer execution failed: Impacting Interactivity "
                "(0000000e:kIOGPUCommandBufferCallbackErrorImpactingInteractivity)")
        self.assertEqual(P._analytics_error_class(line), "metal_watchdog")

    def test_out_of_memory_stays_out_of_memory(self):
        line = ("[METAL] Command buffer execution failed: Insufficient Memory "
                "(00000008:kIOGPUCommandBufferCallbackErrorOutOfMemory)")
        self.assertEqual(P._analytics_error_class(line), "metal_oom")


if __name__ == "__main__":
    unittest.main(verbosity=2)
