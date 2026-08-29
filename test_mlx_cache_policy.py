"""The MLX allocator cache cap — asserted per tier, from any Mac.

Phosphene shipped for a year with NO MLX cache policy. MLX's own default
ceiling is `0.95 * hw.memsize`: a number about the machine, not about the job.
So a 5 s clip's allocator cache grew until it was near the ceiling, and on
mlx 0.32.x — where the cache actually fills what it is allowed — the same
render's peak footprint went from 75% of a 32 GB Mac to 97% of it.

The policy is one eighth of physical RAM, floored at 2 GiB and ceilinged at
8 GiB. This suite pins the value at every tier the capability table names,
because the tier table those numbers were measured against is a *simulation*
of exactly this function, and a simulation that drifts from the shipped policy
measures nothing.

The function is read out of `mlx_warm_helper.py` with `ast` rather than
imported: the helper is a script whose module body ends in a blocking read of
stdin, so importing it in a test would hang. Extraction RAISES if the function
or a constant is renamed, so this cannot decay into testing a copy.
"""

import ast
import unittest
from pathlib import Path

HELPER = Path(__file__).with_name("mlx_warm_helper.py")

GIB = 1024 ** 3

_WANTED_CONSTANTS = {
    "MLX_CACHE_RAM_DIVISOR",
    "MLX_CACHE_FLOOR_BYTES",
    "MLX_CACHE_CEIL_BYTES",
}
_WANTED_FUNCTION = "mlx_cache_limit_bytes"


def _load_policy() -> dict:
    """Exec ONLY the policy constants + the pure function, out of the real file."""
    tree = ast.parse(HELPER.read_text())
    picked, seen_constants = [], set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if names & _WANTED_CONSTANTS:
                picked.append(node)
                seen_constants |= names & _WANTED_CONSTANTS
        elif isinstance(node, ast.FunctionDef) and node.name == _WANTED_FUNCTION:
            picked.append(node)
    missing = _WANTED_CONSTANTS - seen_constants
    if missing:
        raise AssertionError(f"mlx_warm_helper.py no longer defines: {sorted(missing)}")
    if not any(isinstance(n, ast.FunctionDef) for n in picked):
        raise AssertionError(f"mlx_warm_helper.py no longer defines {_WANTED_FUNCTION}()")
    ns: dict = {"__name__": "mlx_cache_policy_extract"}
    exec(compile(ast.Module(body=picked, type_ignores=[]), str(HELPER), "exec"), ns)
    return ns


class CacheCapPerTier(unittest.TestCase):
    """The tier table, in bytes. These ARE the numbers the renders were run at."""

    def setUp(self):
        ns = _load_policy()
        self.cap = ns[_WANTED_FUNCTION]

    def test_every_tier(self):
        for ram_gib, want_gib in (
            (8, 2),      # below the floor — floored, not 1 GiB
            (16, 2),     # Compact
            (24, 3),
            (32, 4),     # Compact
            (36, 4.5),
            (48, 6),     # Comfortable — the commonest paying-attention Mac
            (64, 8),     # Comfortable — this box, where the 8 GiB cap was measured
            (96, 8),     # Roomy — ceilinged
            (128, 8),    # Studio — ceilinged
            (512, 8),
        ):
            with self.subTest(ram_gib=ram_gib):
                self.assertEqual(self.cap(ram_gib * GIB), int(want_gib * GIB))

    def test_never_above_the_ceiling_or_below_the_floor(self):
        for ram_gib in range(1, 257):
            got = self.cap(ram_gib * GIB)
            self.assertGreaterEqual(got, 2 * GIB)
            self.assertLessEqual(got, 8 * GIB)

    def test_monotonic_in_ram(self):
        prev = 0
        for ram_gib in range(1, 257):
            got = self.cap(ram_gib * GIB)
            self.assertGreaterEqual(got, prev)
            prev = got

    def test_always_far_under_mlx_own_default(self):
        """MLX defaults the cache to 0.95 * RAM. The whole point is to be under
        it by an order of magnitude on the machines that were at the wall."""
        for ram_gib in (16, 32, 48, 64, 96, 128):
            self.assertLess(self.cap(ram_gib * GIB), 0.95 * ram_gib * GIB / 4)


class Overrides(unittest.TestCase):
    def setUp(self):
        ns = _load_policy()
        self.cap = ns[_WANTED_FUNCTION]

    def test_explicit_gib(self):
        self.assertEqual(self.cap(64 * GIB, "4"), 4 * GIB)
        self.assertEqual(self.cap(64 * GIB, "0.5"), GIB // 2)

    def test_zero_means_no_cache_not_no_policy(self):
        """`0` is a real setting — the smallest footprint measured — and must
        not be confused with "unset"."""
        self.assertEqual(self.cap(64 * GIB, "0"), 0)

    def test_off_restores_mlx_default(self):
        for word in ("off", "OFF", " default ", "unset"):
            self.assertIsNone(self.cap(64 * GIB, word))

    def test_auto_and_empty_run_the_policy(self):
        for word in (None, "", "   ", "auto", "AUTO"):
            self.assertEqual(self.cap(64 * GIB, word), 8 * GIB)

    def test_typo_runs_the_policy_rather_than_failing_a_render(self):
        for word in ("eight", "8gb", "-3", "??"):
            self.assertEqual(self.cap(64 * GIB, word), 8 * GIB)

    def test_unreadable_sysctl_takes_the_floor_not_the_machine(self):
        self.assertEqual(self.cap(0), 2 * GIB)
        self.assertEqual(self.cap(-1), 2 * GIB)


class WiredIntoTheHelper(unittest.TestCase):
    """A policy nothing calls is a policy that does not exist — the exact shape
    of the `_base.py` `set_cache_limit(0)` that sat on a path we never take."""

    def setUp(self):
        self.src = HELPER.read_text()

    def test_applied_at_startup(self):
        self.assertIn("\napply_mlx_cache_policy()\n", self.src)

    def test_reasserted_before_every_render(self):
        """The pipelines drop the cache to 0 on their low-memory paths and never
        put it back, so startup-only would decay to "no cache" after one A2V."""
        i = self.src.index('if isinstance(action, str) and action.startswith(("generate", "extend")):')
        self.assertIn("apply_mlx_cache_policy()", self.src[i:i + 400])

    def test_ready_event_reports_the_cap(self):
        self.assertIn('"mlx_cache_limit_gib"', self.src)


if __name__ == "__main__":
    unittest.main()
