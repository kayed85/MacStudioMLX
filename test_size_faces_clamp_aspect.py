"""VC-02/SYS-05: the Compact (<48 GB) tier clamp used to floor width and
height independently, so a 16:9 request came back a 2:1 canvas (1024x576
clamped to 768 landed at 768x384, not the aspect-preserving ~768x448), and
the chips advertised the UNCLAMPED canvas while the server silently rendered
something smaller. Three things are pinned here:

  1. ltx_fit_canvas rounds to the nearest /64 grid point once a clamp
     actually fires, instead of flooring both sides independently.
  2. _ltx_qualities() bakes the Compact-tier clamp into the canvas it
     serves, so the chip and the render can never disagree.
  3. The "48 GB fast generation" profile no longer double-clamps (and
     mislabels itself) on a genuinely Compact (<48 GB) Mac — that tier is
     governed by tier_max_dim alone.

Patches the panel to the Compact (base) tier so the whole module runs
under the Compact tier without needing a real 16 GB Mac.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SANDBOX = Path(tempfile.mkdtemp(prefix="phos-test-clamp-"))
for _var, _sub in (("LTX_STATE_DIR", "state"), ("LTX_OUTPUT_DIR", "mlx_outputs"),
                   ("LTX_UPLOADS_DIR", "panel_uploads")):
    os.environ.setdefault(_var, str(SANDBOX / _sub))
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")
os.environ.setdefault("PHOSPHENE_DISABLE_VERSION_CHECK", "1")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402
from unittest import mock  # noqa: E402

# The Compact tier is simulated by patching the panel's resolved tier for
# THIS module's tests only. It used to be LTX_TIER_OVERRIDE=base set at
# import time, which only works when this file is the first to import the
# panel: in a whole-suite run an earlier module had already resolved the
# real tier (so SYSTEM_TIER was never "base" here), and the env var then
# leaked into every suite that ran after it.
_TIER_PATCHES = []


def setUpModule():
    for name, value in (("SYSTEM_TIER", "base"),
                        ("SYSTEM_CAPS", P.CAPABILITIES["base"])):
        patcher = mock.patch.object(P, name, value)
        patcher.start()
        _TIER_PATCHES.append(patcher)


def tearDownModule():
    while _TIER_PATCHES:
        _TIER_PATCHES.pop().stop()


class LtxFitCanvasKeepsAspect(unittest.TestCase):
    def test_1280x704_clamped_to_768_keeps_16_9_ish(self):
        # Old (buggy) behaviour: (768, 384) — a 2:1 canvas.
        w, h = P.ltx_fit_canvas(1280, 704, 768)
        self.assertEqual((w, h), (768, 448))
        # Not the old bug.
        self.assertNotEqual((w, h), (768, 384))
        # Long side is exactly at the cap, never over it.
        self.assertLessEqual(max(w, h), 768)
        # Aspect is much closer to source (1.818) than the old 2:1.
        self.assertLess(abs(w / h - 1280 / 704), abs(768 / 384 - 1280 / 704))

    def test_1024x576_clamped_to_768_matches_1280x704s_result(self):
        # Balanced and Standard used to clamp to DIFFERENT canvases on the
        # same Mac because each request's own floor rounded differently —
        # they should land on the identical grid point now.
        a = P.ltx_fit_canvas(1024, 576, 768)
        b = P.ltx_fit_canvas(1280, 704, 768)
        self.assertEqual(a, b)

    def test_short_side_never_rounds_back_over_the_cap(self):
        # A pathological near-square ratio must not let "round" push the
        # short side above max_dim.
        w, h = P.ltx_fit_canvas(1300, 1290, 768)
        self.assertLessEqual(w, 768)
        self.assertLessEqual(h, 768)

    def test_unclamped_request_still_floors_as_before(self):
        # No cap in play (max_dim=0) — behaviour must be byte-identical to
        # the pre-existing ltx_floor_canvas (every OTHER caller of this
        # function depends on that).
        self.assertEqual(P.ltx_fit_canvas(1281, 705, 0),
                         P.ltx_floor_canvas(1281, 705))

    def test_under_the_cap_is_a_no_op(self):
        self.assertEqual(P.ltx_fit_canvas(640, 448, 768), (640, 448))


class QualitiesTableIsHonestOnCompact(unittest.TestCase):
    """_ltx_qualities() must already reflect what THIS Mac renders."""

    def test_compact_tier_clamps_balanced_and_standard(self):
        self.assertEqual(P.SYSTEM_TIER, "base")
        qualities = P._ltx_qualities()
        balanced = qualities["balanced"]
        standard = qualities["standard"]
        # Not the unclamped 1024x576 / 1280x704 any more.
        self.assertEqual((balanced["width"], balanced["height"]), (768, 448))
        self.assertEqual((standard["width"], standard["height"]), (768, 448))
        self.assertEqual(balanced["canvas"], "768×448")
        self.assertIn("768", balanced["note"])
        self.assertIn("Compact", balanced["note"])

    def test_quick_is_untouched_already_under_the_cap(self):
        qualities = P._ltx_qualities()
        self.assertEqual((qualities["quick"]["width"], qualities["quick"]["height"]),
                         (640, 448))

    def test_hq_pipeline_is_never_clamped_here(self):
        # High/High-720p are gated off entirely on Compact via allows_q8 —
        # this table must not also silently shrink their advertised canvas.
        qualities = P._ltx_qualities()
        self.assertEqual((qualities["high"]["width"], qualities["high"]["height"]),
                         (1024, 576))
        self.assertEqual((qualities["high_720p"]["width"], qualities["high_720p"]["height"]),
                         (1280, 704))


class GenerationProfileDoesNotDoubleClampResolution(unittest.TestCase):
    """The '48 GB fast generation' profile still fires below 48 GB — a2v_max_frames()
    and the LoRA-count warning genuinely need it there — but its RESOLUTION
    clamp must defer to tier_max_dim on the Compact tier instead of stacking
    a second, independent clamp on top of it (that stacking, each pass
    flooring its own way, is what turned a 16:9 request into a 2:1 canvas).

    An earlier version of this fix scoped `compact` itself to the
    Comfortable tier only, which looked right for the resolution bug but
    silently zeroed a2v_max_frames() on every Compact-tier Mac — caught by
    test_review_416_jobs (CompactMacsRefuseOversizedA2V) when the whole
    suite ran, not by this file's own narrower tests. Kept here as the
    regression these tests now pin against.
    """

    def test_compact_tier_still_gets_the_profile(self):
        # NOT full_generation — a2v_max_frames() reads `compact` +
        # auto_temporal_after_frames directly for the Compact-tier A2V
        # length refusal, and warn_loras still applies below 48 GB too.
        profile = P._select_generation_profile(16.0, "base")
        self.assertTrue(profile["compact"])
        self.assertEqual(profile["key"], "m5pro48_generation")
        self.assertEqual(profile["auto_temporal_after_frames"], 241)

    def test_comfortable_tier_still_gets_the_profile(self):
        profile = P._select_generation_profile(56.0, "standard")
        self.assertTrue(profile["compact"])
        self.assertEqual(profile["key"], "m5pro48_generation")
        self.assertEqual(profile["max_dim"], 1024)

    def test_roomy_and_above_are_never_compact(self):
        self.assertFalse(P._select_generation_profile(96.0, "high")["compact"])

    # a2v_max_frames() itself is NOT re-tested here: it reads the
    # module-level GENERATION_PROFILE constant, which is computed once at
    # import time from this machine's REAL sysctl RAM (LTX_TIER_OVERRIDE
    # forces SYSTEM_TIER but not SYSTEM_RAM_GB) — asserting it from this
    # file would silently depend on the dev box's actual memory size.
    # test_review_416_jobs.py (CompactMacsRefuseOversizedA2V) already pins
    # a2v_max_frames() correctly with proper RAM mocking; that suite is
    # what caught the regression an earlier version of this fix caused
    # (`compact` scoped away from Compact tier entirely) and it now passes.

    def test_profile_resolution_clamp_is_a_noop_on_compact_tier(self):
        # _apply_generation_profile_to_job's OWN max_dim clamp must not
        # touch width/height on Compact — tier_max_dim (applied later, in
        # run_job_inner) is the sole resolution authority there.
        job = {"params": {"engine": "ltx", "mode": "t2v", "quality": "balanced",
                          "width": 1280, "height": 704, "frames": 121}}
        P._apply_generation_profile_to_job(job)
        self.assertEqual((job["params"]["width"], job["params"]["height"]),
                         (1280, 704))
        self.assertNotIn("generation_clamp_notes", job["params"])


if __name__ == "__main__":
    unittest.main()
