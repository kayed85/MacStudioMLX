import unittest
import mlx_ltx_panel as P

class TestPresets(unittest.TestCase):
    def test_presets_defined(self):
        self.assertIn("orbit", P.CAMERA_MOTION_PRESETS)
        self.assertIn("cinematic_35mm", P.VISUAL_STYLE_PRESETS)

    def test_make_job_preset_injection(self):
        form = {
            "prompt": "A heroic warrior standing on a mountain peak",
            "camera_motion": "orbit",
            "visual_style": "cyberpunk_neon",
        }
        job = P.make_job(form)
        params = job["params"]
        self.assertEqual(params["camera_motion"], "orbit")
        self.assertEqual(params["visual_style"], "cyberpunk_neon")
        self.assertIn("Futuristic cyberpunk scene", params["prompt"])
        self.assertIn("cinematic 360 degree smooth orbit", params["prompt"])
        self.assertIn("magenta and cyan atmosphere", params["prompt"])
        self.assertIn("daylight, sepia", params["negative_prompt"])

    def test_presets_keys(self):
        self.assertIn("drone", P.CAMERA_MOTION_PRESETS)
        self.assertIn("anime_ghibli", P.VISUAL_STYLE_PRESETS)

    def test_parse_comfyui_workflow(self):
        sample_graph = {
            "3": {
                "class_type": "KSampler",
                "inputs": {"seed": 424242, "steps": 12}
            },
            "6": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "Cyberpunk warrior in neon rain"}
            }
        }
        res = P.parse_comfyui_workflow(sample_graph)
        self.assertEqual(res["seed"], 424242)
        self.assertEqual(res["steps"], 12)
        self.assertEqual(res["prompt"], "Cyberpunk warrior in neon rain")

    def test_system_ram_detection(self):
        ram = P.SYSTEM_RAM_GB
        self.assertGreater(ram, 0.0)
        self.assertIn(P.SYSTEM_TIER, ("base", "standard", "high", "pro"))

    def test_arabic_dialogue_extraction(self):
        import re
        prompt = 'بنت بتمشي بالشارع و بتقول "يا سلام شو هذا"'
        quote_pattern = r'["\'«”]([^"\'»“]+)["\'»”]'
        matches = re.findall(quote_pattern, prompt)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0], "يا سلام شو هذا")

if __name__ == "__main__":
    unittest.main()
