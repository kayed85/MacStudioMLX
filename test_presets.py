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

if __name__ == "__main__":
    unittest.main()
