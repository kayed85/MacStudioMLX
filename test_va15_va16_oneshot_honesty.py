"""VA-15 [P1] / VA-16 [P2]: One Shot's default committed ~28 minutes of GPU
before showing a price, and the price it eventually showed was always the
best case.

VA-15: oneshot.js defaulted to seconds: 60 (6 parts, ~28 min) with no price
in the sticky footer next to Generate — only in the composer card above,
easy to scroll past. VA-16: take_estimate_minutes priced the base render
only; at run time each part can ALSO get one light-drift retake
(_take_retry_seed's caller in mlx_ltx_panel.py's take-part loop) plus, for a
spoken part, up to TAKE_LIPSYNC_RETAKES lip-sync retakes — up to ~4x the
base render — and the copy said only "a part that drifts is rendered once
more", never mentioning lip-sync retakes. Separately: with the form's seed
left on "-1" (random, the default), every retake of every part landed on
the exact same fixed seed (int("-1") + 101 == 100, every time) instead of a
fresh random roll.
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
os.environ.setdefault("LTX_STATE_DIR", tempfile.mkdtemp(prefix="phos-va1516-"))
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402


class WorstCaseEstimate(unittest.TestCase):
    def test_worst_is_a_multiple_of_the_base(self):
        base = P.take_estimate_minutes("ltx", "balanced", 60)
        worst = P.take_estimate_minutes_worst("ltx", "balanced", 60)
        self.assertIsNotNone(base)
        self.assertIsNotNone(worst)
        factor = 1 + P.TAKE_LIGHT_DRIFT_RETAKES + P.TAKE_LIPSYNC_RETAKES
        self.assertAlmostEqual(worst, round(base * factor, 1), delta=0.05)

    def test_worst_is_never_smaller_than_base(self):
        base = P.take_estimate_minutes("h3", "standard", 30)
        worst = P.take_estimate_minutes_worst("h3", "standard", 30)
        if base is not None:
            self.assertGreaterEqual(worst, base)

    def test_none_when_base_is_none(self):
        self.assertIsNone(P.take_estimate_minutes_worst("ltx", "balanced", 999999))


class RouteExposesBoth(unittest.TestCase):
    class H:
        def __init__(self):
            self.out = None

        def _json(self, obj, code=200):
            self.out = (code, obj)

    class Parsed:
        def __init__(self, query):
            self.query = query

    def test_estimate_route_carries_the_worst_case(self):
        from panel.routes import GET_ROUTES
        h = self.H()
        GET_ROUTES["/oneshot/estimate"](h, self.Parsed(urlencode(
            {"engine": "ltx", "quality": "balanced", "seconds": 60})))
        code, body = h.out
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertIn("minutes_worst", body)
        self.assertIn("eta_worst", body)
        self.assertGreaterEqual(body["minutes_worst"], body["minutes"])


class RetakeSeedIsRandomWhenTheTakeWasRandom(unittest.TestCase):
    def test_random_seed_stays_random_on_retry(self):
        """The bug: int('-1') never raised, so the deterministic +offset
        branch ran even for a random take, landing every retry on the same
        fixed seed (100, then 210, then 421) regardless of part or install."""
        self.assertEqual(P._take_retry_seed("-1", 101), "-1")
        self.assertEqual(P._take_retry_seed(-1, 101), "-1")
        self.assertEqual(P._take_retry_seed(None, 101), "-1")
        self.assertEqual(P._take_retry_seed("", 211), "-1")

    def test_explicit_seed_still_gets_the_deterministic_offset(self):
        """A user who pinned a seed to reproduce a take must still get a
        reproducible (not colliding) retake seed."""
        self.assertEqual(P._take_retry_seed("7", 101), "108")
        self.assertEqual(P._take_retry_seed("7", 211), "218")
        self.assertEqual(P._take_retry_seed(7, 422), "429")

    def test_garbage_seed_falls_back_to_random(self):
        self.assertEqual(P._take_retry_seed("not-a-number", 101), "-1")

    def test_two_random_retries_no_longer_collide_by_construction(self):
        """Regression pin: the OLD code computed int('-1') + 101 == 100 and
        int('-1') + 211 == 210 — always the same two numbers. The new
        behaviour returns '-1' for both, deferring the actual random draw
        to wherever '-1' is resolved (a fresh roll per render), so the two
        calls are no longer forced to the same fixed values."""
        light_drift_seed = P._take_retry_seed("-1", 101)
        lipsync_seed = P._take_retry_seed("-1", 211)
        self.assertEqual(light_drift_seed, "-1")
        self.assertEqual(lipsync_seed, "-1")
        self.assertNotEqual(light_drift_seed, "100")
        self.assertNotEqual(lipsync_seed, "210")


if __name__ == "__main__":
    unittest.main()
