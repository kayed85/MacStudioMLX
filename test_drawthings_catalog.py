"""Unit tests for Draw Things dynamic catalog, bilingual knowledge base, styles, and AI explanation."""

import unittest
from unittest.mock import MagicMock
import drawthings_catalog
from panel import routes_drawthings


class TestDrawThingsCatalog(unittest.TestCase):
    def test_catalog_structure(self):
        cat = drawthings_catalog.get_full_catalog()
        self.assertIn("models", cat)
        self.assertIn("loras", cat)
        self.assertIn("presets", cat)
        self.assertIn("styles", cat)
        self.assertIn("scripts", cat)
        self.assertGreater(cat["total_models"], 0)
        self.assertGreater(cat["total_loras"], 0)
        self.assertGreater(cat["total_styles"], 0)
        self.assertGreater(cat["total_scripts"], 0)

    def test_bilingual_metadata_presence(self):
        cat = drawthings_catalog.get_full_catalog()
        # Verify first model has both English and Arabic metadata
        first_model = cat["models"][0]
        self.assertIn("name_en", first_model)
        self.assertIn("name_ar", first_model)
        self.assertIn("desc_en", first_model)
        self.assertIn("desc_ar", first_model)

        # Verify first LoRA has bilingual metadata + trigger word
        first_lora = cat["loras"][0]
        self.assertIn("name_en", first_lora)
        self.assertIn("name_ar", first_lora)
        self.assertIn("trigger", first_lora)

    def test_heuristic_synthesis_unknown(self):
        # Unknown video LoRA
        syn_lora = drawthings_catalog._heuristic_synthesize("cyber_robot_v1_lora_f16.ckpt", 50 * 1024 * 1024)
        self.assertEqual(syn_lora["type"], "lora")
        self.assertEqual(syn_lora["trigger"], "cyber")
        self.assertIn("Cyber Robot V1", syn_lora["name_en"])
        self.assertIn("لورا", syn_lora["name_ar"])

        # Unknown base model
        syn_model = drawthings_catalog._heuristic_synthesize("anime_diffusion_xl_v2.ckpt", 2048 * 1024 * 1024)
        self.assertEqual(syn_model["type"], "image_model")
        self.assertIn("Anime Diffusion Xl V2", syn_model["name_en"])
        self.assertIn("محرك", syn_model["name_ar"])

    def test_prompt_styles(self):
        cat = drawthings_catalog.get_full_catalog()
        styles = cat["styles"]
        self.assertGreater(len(styles), 10)
        # Check an iconic style
        cinematic = next((s for s in styles if "cinematic" in s["name"].lower()), None)
        self.assertIsNotNone(cinematic)
        self.assertIn("prompt", cinematic)
        self.assertIn("label_ar", cinematic)

    def test_routes_drawthings_catalog(self):
        handler = MagicMock()
        routes_drawthings.get_drawthings_catalog(handler, None)
        handler._json.assert_called_once()
        args, _ = handler._json.call_args
        self.assertIn("models", args[0])
        self.assertIn("loras", args[0])


if __name__ == "__main__":
    unittest.main()
