#!/usr/bin/env python3
"""Training survives the caption-encode watchdog kill by relaunching once at a
shorter Gemma length (#61) — the render helper's mitigation, ported.

The trainer runs in its own process and dies by SIGABRT when macOS kills its
GPU command buffer partway through the caption list (the 1st, 7th or 11th
caption on different runs: a race on accumulated GPU pressure, not a bad
input). What is pinned here: the panel places the kill from the trainer's
own lines, relaunches ONLY for that phase and only once, discards the
half-written caption encodings so the relaunch encodes every caption at one
length, and the preprocessor honours the shorter length the relaunch passes.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ["LTX_STATE_DIR"] = tempfile.mkdtemp(prefix="phos-train-retry-")
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8313")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402


class PhasePlacement(unittest.TestCase):
    def test_follows_the_preprocessor_banners_and_step_events(self) -> None:
        f = P._train_phase_from_line
        self.assertEqual(f("found 51 images in /x", None, "start"), "start")
        self.assertEqual(f("Phase 1: encoding text captions (Gemma)...", None, "start"), "text_encode")
        self.assertEqual(f("  [7/51] Encoding: 'a caption'", None, "text_encode"), "text_encode")
        self.assertEqual(f("Phase 2: encoding image latents at 512x512 (1 frame each, crop_anchor=center)...",
                           None, "text_encode"), "image_encode")
        self.assertEqual(f('{"event":"train_progress","step":1}', {"event": "train_progress", "step": 1},
                           "image_encode"), "train")
        self.assertEqual(f('{"event":"log","msg":"x"}', {"event": "log", "msg": "x"}, "train"), "train")

    def test_watchdog_signature_is_the_same_family_the_helper_watches(self) -> None:
        self.assertTrue(P._TRAIN_ABORT_RX.search(
            "libc++abi: terminating due to uncaught exception"))
        self.assertTrue(P._TRAIN_ABORT_RX.search(
            "Error: kIOGPUCommandBufferCallbackErrorTimeout"))
        self.assertFalse(P._TRAIN_ABORT_RX.search("step 100/5000 loss=0.12"))


class RetryDecision(unittest.TestCase):
    def test_only_the_caption_encode_kill_is_relaunched_and_only_once(self) -> None:
        want = P._train_encode_retry_wanted
        self.assertTrue(want(-6, True, "text_encode", False))
        self.assertFalse(want(-6, True, "text_encode", True), "one relaunch, not a loop")
        self.assertFalse(want(-6, True, "image_encode", False), "canvas-shaped kill: the existing error")
        self.assertFalse(want(-6, True, "train", False))
        self.assertFalse(want(-6, False, "text_encode", False), "no signature: a crash, not a timeout")
        self.assertFalse(want(1, True, "text_encode", False))
        self.assertFalse(want(0, True, "text_encode", False))

    def test_fallback_length_shares_the_render_helper_knob(self) -> None:
        old = os.environ.pop("LTX_GEMMA_FALLBACK_MAX_LENGTH", None)
        try:
            self.assertEqual(P._train_gemma_fallback_max_length(), 256)
            os.environ["LTX_GEMMA_FALLBACK_MAX_LENGTH"] = "16"
            self.assertEqual(P._train_gemma_fallback_max_length(), 64, "floor 64")
            os.environ["LTX_GEMMA_FALLBACK_MAX_LENGTH"] = "512"
            self.assertEqual(P._train_gemma_fallback_max_length(), 512)
            os.environ["LTX_GEMMA_FALLBACK_MAX_LENGTH"] = "junk"
            self.assertEqual(P._train_gemma_fallback_max_length(), 256)
        finally:
            if old is None:
                os.environ.pop("LTX_GEMMA_FALLBACK_MAX_LENGTH", None)
            else:
                os.environ["LTX_GEMMA_FALLBACK_MAX_LENGTH"] = old


class ConditionsWipe(unittest.TestCase):
    def test_discards_caption_encodings_and_keeps_image_latents(self) -> None:
        ds = Path(tempfile.mkdtemp())
        pre = ds / "training_data" / ".precomputed"
        (pre / "conditions").mkdir(parents=True)
        (pre / "latents").mkdir(parents=True)
        for i in range(3):
            (pre / "conditions" / f"condition_{i:04d}.safetensors").write_bytes(b"c")
            (pre / "latents" / f"latent_{i:04d}.safetensors").write_bytes(b"l")
        self.assertEqual(P._train_wipe_caption_conditions(ds), 3)
        self.assertEqual(list((pre / "conditions").iterdir()), [])
        self.assertEqual(len(list((pre / "latents").iterdir())), 3, "the expensive half stays")
        self.assertEqual(P._train_wipe_caption_conditions(Path(tempfile.mkdtemp())), 0,
                         "no dataset yet: nothing to do, no error")


class PreprocessHonoursTheShorterLength(unittest.TestCase):
    def test_encode_all_layers_is_capped_when_the_variable_is_set(self) -> None:
        try:
            from lora_lab import preprocess_images as pp
            from ltx_core_mlx.text_encoders.gemma.encoders import base_encoder as be
        except Exception as e:  # noqa: BLE001 — the vendored trainer is an install-time dep
            self.skipTest(f"trainer deps not importable here: {e}")
        old = os.environ.pop("LTX2_GEMMA_MAX_LENGTH", None)
        orig = be.GemmaLanguageModel.encode_all_layers
        try:
            self.assertIsNone(pp._apply_gemma_max_length_override(), "unset: the default 1024 stays")
            self.assertIs(be.GemmaLanguageModel.encode_all_layers, orig)
            os.environ["LTX2_GEMMA_MAX_LENGTH"] = "256"
            self.assertEqual(pp._apply_gemma_max_length_override(), 256)
            seen = {}

            def fake(self, text, max_length=1024):
                seen["max_length"] = max_length
                return ([], None)

            # The wrapper closes over the ORIGINAL; stub what it calls through
            # to and check the length it forwards.
            wrapped = be.GemmaLanguageModel.encode_all_layers
            self.assertIsNot(wrapped, orig)
            be.GemmaLanguageModel.encode_all_layers = orig
            be.GemmaLanguageModel.encode_all_layers = fake
            pp._apply_gemma_max_length_override()
            be.GemmaLanguageModel.encode_all_layers(object(), "a caption")
            self.assertEqual(seen["max_length"], 256)
            be.GemmaLanguageModel.encode_all_layers(object(), "a caption", 128)
            self.assertEqual(seen["max_length"], 128, "an explicit shorter length is kept")
            self.assertEqual(pp._apply_gemma_max_length_override(), 256, "idempotent")
        finally:
            be.GemmaLanguageModel.encode_all_layers = orig
            if old is None:
                os.environ.pop("LTX2_GEMMA_MAX_LENGTH", None)
            else:
                os.environ["LTX2_GEMMA_MAX_LENGTH"] = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
