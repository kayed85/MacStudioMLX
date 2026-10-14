"""Tests for Draw Things bridge and engine integration in MacStudioMLX."""

import unittest
from pathlib import Path
from unittest import mock

import drawthings_bridge
import mlx_ltx_panel as panel


class DrawThingsBridgeTests(unittest.TestCase):
    def test_list_drawthings_loras_structure(self):
        loras = drawthings_bridge.list_drawthings_loras()
        self.assertGreaterEqual(len(loras), 8, "Expected at least 8 Draw Things LoRAs")

        by_fn = {l["filename"]: l for l in loras}
        expected_files = [
            "minimax_h3_3step_lora_f16.ckpt",
            "minimax_h3_turbo_v4_lora_f16.ckpt",
            "h3_photoreal_lora_f16.ckpt",
            "relim_v2_lora_500__1__lora_f16.ckpt",
            "qwen_image_2.16step_lora_f16.ckpt",
            "krea2_turbo_4step_lora_f16.ckpt",
            "krea2turbo_radiance_lora_f16.ckpt",
            "ideokrea_v1_lora_f16.ckpt",
        ]
        for fn in expected_files:
            self.assertIn(fn, by_fn, f"Missing expected LoRA {fn}")
            entry = by_fn[fn]
            self.assertTrue(entry["drawthings"])
            self.assertTrue(entry["id"].startswith("dt:"))
            self.assertTrue(len(entry["trigger"]) > 0)
            self.assertGreater(entry["size_mb"], 0)

            if any(k in fn for k in ("h3", "minimax", "relim")):
                self.assertEqual(entry["lane"], "h3")
                self.assertIn("video:h3", entry["compatible_modes"])
            else:
                self.assertEqual(entry["lane"], "image")
                self.assertIn("image", entry["compatible_modes"])

    def test_lora_is_h3_lane(self):
        dt_dir = drawthings_bridge.DRAWTHINGS_MODELS_DIR
        h3_lora = dt_dir / "minimax_h3_3step_lora_f16.ckpt"
        img_lora = dt_dir / "qwen_image_2.16step_lora_f16.ckpt"

        self.assertTrue(panel._lora_is_h3_lane(str(h3_lora)))
        self.assertFalse(panel._lora_is_h3_lane(str(img_lora)))

    def test_all_dt_image_presets(self):
        for preset, expected_model in [
            ("dt_qwen2_turbo", "qwen_image_2.1_i8x.ckpt"),
            ("dt_krea2_turbo", "krea_2_turbo_i8x.ckpt"),
            ("dt_krea2_radiance", "krea_2_turbo_i8x.ckpt"),
            ("dt_krea2_ideokrea", "krea_2_turbo_i8x.ckpt"),
            ("dt_flux2_klein", "flux_2_klein_4b_i8x.ckpt"),
        ]:
            cfg = panel._build_image_engine_config(preset, {})
            self.assertEqual(cfg.kind, "drawthings")
            self.assertEqual(cfg.mflux_model, expected_model)
            self.assertIn(preset, panel._IMAGE_ENGINE_LABELS)


if __name__ == "__main__":
    unittest.main()
