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

    def test_generate_3d_mesh(self):
        from PIL import Image
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            img = Image.new("RGB", (32, 32), color="red")
            img.save(tf.name)
            res = P.generate_3d_mesh_from_image(tf.name)
            self.assertTrue(res["ok"])
            self.assertTrue(res["obj_path"].endswith(".obj"))

    def test_build_storyboard_script(self):
        shots = P.build_storyboard_script("Girl walking in forest with rabbit", num_shots=6, character_name="Alice", product_name="Magic Bottle")
        self.assertEqual(len(shots), 6)
        self.assertIn("Alice", shots[0]["prompt"])
        self.assertIn("Magic Bottle", shots[0]["prompt"])
        self.assertEqual(shots[0]["shot"], 1)
        self.assertEqual(shots[5]["shot"], 6)

    def test_stitch_storyboard_videos_validation(self):
        with self.assertRaises(ValueError):
            P.stitch_storyboard_videos([])

if __name__ == "__main__":
    unittest.main()
