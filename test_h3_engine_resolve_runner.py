"""The enqueue seam agrees with make_job about an older H3 runner (Codex 4.19.0).

make_job renders Keyframes / Extend / Lip-sync on LTX when the INSTALLED H3
runner predates the mode. `_engine_would_be_h3` (used by the character check at
/queue/add) did not know that, so a character job the panel would have rendered
on LTX was refused with "H3 cannot load the character".
"""
from __future__ import annotations

import unittest
from unittest import mock

import mlx_ltx_panel as P


class OlderRunner(unittest.TestCase):
    def _would(self, mode, supported):
        with mock.patch.object(P, "h3_capable", lambda *a, **k: True), \
             mock.patch.object(P, "h3_available", lambda *a, **k: True), \
             mock.patch.object(P, "h3_mode_supported",
                               lambda m: supported if m in P.H3_MODE_CAPABILITY else True):
            return P._engine_would_be_h3("h3", mode)

    def test_modes_the_runner_lacks_resolve_to_ltx(self):
        for mode in ("keyframe", "extend", "a2v"):
            self.assertFalse(self._would(mode, False), mode)
            self.assertTrue(self._would(mode, True), mode)

    def test_add_sound_and_plain_modes_stay_h3(self):
        self.assertTrue(self._would("v2a", False))
        self.assertTrue(self._would("t2v", False))


if __name__ == "__main__":
    unittest.main()
