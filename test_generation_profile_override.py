"""The 48 GB generation clamp, and the two ways to lift it.

Reported 2026-08-16 on r/... (Mac Studio M4 Max, 48 GB, v4.0.6): a requested
1088x1472 was silently reshaped to 768x1024 while memory sat at 9.4/48 GB. The
reporter also found the real inconsistency — LTX_TIER_OVERRIDE moved the tier
modal to "Studio" while _select_generation_profile kept reading physical RAM,
so the two disagreed about the same machine. They patched the threshold out and
ran the identical i2v job at full size: 12.79 s/it vs 13.4 s/it clamped, 284 s,
no swap, no OOM.

The default threshold stays — it was put there by a real swap thrash and one
clean 48 GB result does not retire it. What changed is that it is no longer the
only voice: an explicit tier override is believed, and LTX_GENERATION_PROFILE
can lift the cap outright.
"""

import os
import unittest
from unittest import mock

import mlx_ltx_panel as panel


class GenerationProfileClamp(unittest.TestCase):
    def _profile(self, ram, tier="standard", **env):
        with mock.patch.dict(os.environ, env, clear=False):
            for k in ("LTX_TIER_OVERRIDE", "LTX_GENERATION_PROFILE"):
                if k not in env:
                    os.environ.pop(k, None)
            return panel._select_generation_profile(ram, tier)

    def test_48gb_still_clamps_by_default(self):
        """The default must not change silently — it exists for a reason."""
        p = self._profile(48.0)
        self.assertTrue(p["compact"])
        self.assertEqual(p["max_dim"], 1024)

    def test_64gb_and_up_never_clamped(self):
        self.assertFalse(self._profile(64.0)["compact"])
        self.assertFalse(self._profile(128.0)["compact"])

    def test_tier_override_lifts_the_clamp(self):
        """THE reported bug: the override moved the modal and nothing else."""
        for tier in ("high", "pro"):
            p = self._profile(48.0, LTX_TIER_OVERRIDE=tier)
            self.assertFalse(p["compact"], tier)
            self.assertEqual(p["max_dim"], 0, tier)

    def test_a_lower_override_does_not_lift_it(self):
        """Forcing 'base' must not accidentally hand you the uncapped profile."""
        p = self._profile(48.0, LTX_TIER_OVERRIDE="base")
        self.assertTrue(p["compact"])

    def test_explicit_flag_lifts_the_clamp_on_its_own(self):
        for val in ("full", "off", "uncapped"):
            p = self._profile(48.0, LTX_GENERATION_PROFILE=val)
            self.assertFalse(p["compact"], val)
            self.assertEqual(p["max_dim"], 0, val)

    def test_unknown_flag_value_is_ignored(self):
        """Junk must fall through to the safe default, not to uncapped."""
        p = self._profile(48.0, LTX_GENERATION_PROFILE="yes-please")
        self.assertTrue(p["compact"])


if __name__ == "__main__":
    unittest.main()
