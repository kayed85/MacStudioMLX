"""VA-06 [P1]: A2V showed no time estimate before a 10-25 minute render.

`#audioStudioEstimate` (index.html:3645, roughly) existed in the markup and
nothing wrote to it — Generate, then "Submitted. Watch Now / Recent." with
no price shown anywhere, unlike the Video tab and One Shot which always
price a render first. ltx_a2v_estimate_minutes prices the LANE that
actually runs (Q8 two-stage 20+3 steps vs Q4 distilled 8+3 — the branch in
run_job_inner's `mode == "a2v"` arm) at the real clamped canvas
(tier_max_dim("t2v"), the same clamp the render applies), and GET
/a2v/estimate exposes it the same way /oneshot/estimate and /take/estimate
already price their own modes.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("LTX_STATE_DIR", tempfile.mkdtemp(prefix="phos-va06-"))
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402


class EstimateMatchesTheLaneThatRuns(unittest.TestCase):
    def test_q4_distilled_when_q8_missing(self):
        with mock.patch.object(P, "SYSTEM_CAPS", {**P.SYSTEM_CAPS, "allows_q8": True}), \
             mock.patch.object(P, "hq_surface_missing", lambda: ["some.safetensors"]):
            w, h, minutes, uses_q8 = P.ltx_a2v_estimate_minutes(121)
        self.assertFalse(uses_q8)
        distilled = P.ltx_estimate_minutes(w, h, 121, 8, 3)
        self.assertAlmostEqual(minutes, distilled, delta=0.01)

    def test_q8_two_stage_when_available(self):
        with mock.patch.object(P, "SYSTEM_CAPS", {**P.SYSTEM_CAPS, "allows_q8": True}), \
             mock.patch.object(P, "hq_surface_missing", lambda: []):
            w, h, minutes, uses_q8 = P.ltx_a2v_estimate_minutes(121)
        self.assertTrue(uses_q8)
        hq = P.ltx_estimate_minutes(w, h, 121, 20, 3)
        self.assertAlmostEqual(minutes, hq, delta=0.01)
        # Q8's 20+3 must price slower than Q4's 8+3 at the same shape.
        q4 = P.ltx_estimate_minutes(w, h, 121, 8, 3)
        self.assertGreater(minutes, q4)

    def test_falls_back_to_q4_when_q8_disallowed_by_tier(self):
        with mock.patch.object(P, "SYSTEM_CAPS", {**P.SYSTEM_CAPS, "allows_q8": False}):
            _, _, _, uses_q8 = P.ltx_a2v_estimate_minutes(121)
        self.assertFalse(uses_q8)

    def test_clamps_to_tier_max_dim_like_the_real_render(self):
        with mock.patch.object(P, "SYSTEM_CAPS", {**P.SYSTEM_CAPS, "t2v_max_dim": 768}):
            w, h, _, _ = P.ltx_a2v_estimate_minutes(121, width=1280, height=704)
        self.assertLessEqual(max(w, h), 768)

    def test_longer_duration_prices_higher(self):
        _, _, short, _ = P.ltx_a2v_estimate_minutes(97)
        _, _, long_, _ = P.ltx_a2v_estimate_minutes(241)
        self.assertGreater(long_, short)


class TheRoute(unittest.TestCase):
    class H:
        def __init__(self):
            self.out = None

        def _json(self, obj, code=200):
            self.out = (code, obj)

    class Parsed:
        def __init__(self, query):
            self.query = query

    def get(self, **qs):
        from panel.routes import GET_ROUTES
        h = self.H()
        GET_ROUTES["/a2v/estimate"](h, self.Parsed(urlencode(qs)))
        return h.out

    def test_route_is_registered(self):
        import panel.routes_queue  # noqa: F401
        from panel.routes import GET_ROUTES
        self.assertIn("/a2v/estimate", GET_ROUTES)

    def test_default_frames_and_canvas(self):
        code, body = self.get()
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertIn("eta", body)
        self.assertIn("minutes", body)
        self.assertIn("pack", body)
        self.assertGreater(body["minutes"], 0)

    def test_explicit_frames_and_canvas_are_honoured(self):
        # On a standard-tier Mac (issue #90): a <48 GB Mac prices the canvas
        # its tier clamps to (768x448) by design, which is the exact clamp
        # run_job_inner applies, not a request being ignored.
        with mock.patch.object(P, "SYSTEM_TIER", "standard"), \
                mock.patch.object(P, "SYSTEM_CAPS", P.CAPABILITIES["standard"]):
            code, body = self.get(frames=241, width=1024, height=576)
        self.assertEqual(code, 200)
        self.assertEqual(body["width"], 1024)
        self.assertEqual(body["height"], 576)

    def test_garbage_query_does_not_crash(self):
        code, body = self.get(frames="not-a-number", width="also-not")
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])


if __name__ == "__main__":
    unittest.main()
