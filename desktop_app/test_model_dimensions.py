"""Unit tests for dimension safety and 64-stride alignment across all models."""

import unittest
from image_engine import ASPECT_DIMS, align_dim_64
import drawthings_bridge
import mlx_ltx_panel as P


class TestModelDimensions(unittest.TestCase):
    def test_all_aspect_dims_are_64_aligned(self):
        """Verify that every preset in ASPECT_DIMS is divisible by 64."""
        for aspect_key, (w, h) in ASPECT_DIMS.items():
            self.assertEqual(
                w % 64, 0,
                f"Aspect {aspect_key} width {w} is not a multiple of 64"
            )
            self.assertEqual(
                h % 64, 0,
                f"Aspect {aspect_key} height {h} is not a multiple of 64"
            )
            self.assertGreaterEqual(w, 256)
            self.assertGreaterEqual(h, 256)

    def test_align_dim_64_behavior(self):
        """Test rounding and clamping of dimensions."""
        cases = [
            (720, 704),
            (1280, 1280),
            (432, 448),
            (544, 512),
            (1024, 1024),
            (768, 768),
            (512, 512),
            (1080, 1088),
            (1920, 1920),
            (384, 384),
            (100, 256),  # clamped to minimum 256
            (0, 512),    # fallback to default
            (None, 512),
            ("invalid", 512),
        ]
        for inp, expected in cases:
            self.assertEqual(align_dim_64(inp, default=512), expected)
            self.assertEqual(drawthings_bridge.align_dim_64(inp, default=512), expected)

    def test_make_job_image_dimensions(self):
        """Ensure make_job for mode='image' assigns 64-aligned width and height."""
        for aspect_key in ASPECT_DIMS.keys():
            job = P.make_job({
                "mode": "image",
                "aspect": aspect_key,
                "prompt": "Test cinematic prompt",
            })
            params = job["params"]
            self.assertIn("width", params)
            self.assertIn("height", params)
            self.assertEqual(params["width"] % 64, 0)
            self.assertEqual(params["height"] % 64, 0)

    def test_h3_main_qualities_are_aligned(self):
        """Verify main H3 video qualities have safe dimensions."""
        for q_key in ("draft", "standard", "high", "native"):
            q = P.H3_QUALITIES.get(q_key)
            self.assertIsNotNone(q)
            self.assertEqual(int(q["width"]) % 64, 0)
            self.assertEqual(int(q["height"]) % 64, 0)


if __name__ == "__main__":
    unittest.main()
