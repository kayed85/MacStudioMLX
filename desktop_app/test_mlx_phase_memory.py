"""Per-phase MLX memory reporting — the profile a Compact Mac renders against.

`_log_memory_pressure()` measures the whole MACHINE (vm_stat). That is a
different question from "which phase of OUR render is the spike", and only the
second one tells you what to fix. H3's staged runner has printed a per-phase
peak since the port; LTX printed nothing, which is why the allocator-cache win
was found by a global A/B instead of by reading a profile.

38.8% of the fleet boots under 48 GB. For those machines the PEAK PHASE decides
whether a clip finishes or the Mac swaps, so these lines are a user-facing
diagnostic as much as an internal one.

Like `test_mlx_cache_policy`, the functions are pulled out of the real helper
with `ast` rather than imported — the helper's module body ends in a blocking
stdin read, so importing it would hang. Extraction RAISES if a function is
renamed, so this cannot rot into testing a copy.
"""

import ast
import sys
import types
import unittest
from pathlib import Path

HELPER = Path(__file__).with_name("mlx_warm_helper.py")
GIB = 1024 ** 3

_WANTED = ("_mlx_mem_reset_run", "_mlx_phase_mem", "_mlx_mem_summary")
_WANTED_CONST = "_MLX_RUN_PEAK_GIB"


def _load(fake_mlx):
    """Exec the three real functions with a fake `emit` and a fake mlx.core."""
    tree = ast.parse(HELPER.read_text())
    picked, seen_fns, seen_const = [], set(), False
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if {t.id for t in node.targets if isinstance(t, ast.Name)} & {_WANTED_CONST}:
                picked.append(node)
                seen_const = True
        elif isinstance(node, ast.FunctionDef) and node.name in _WANTED:
            picked.append(node)
            seen_fns.add(node.name)
    missing = set(_WANTED) - seen_fns
    if missing or not seen_const:
        raise AssertionError(
            f"helper no longer defines {sorted(missing) or _WANTED_CONST} — "
            "the memory profile was renamed or removed, not merely edited")

    logged = []
    ns = {"emit": lambda msg: logged.append(msg)}
    # The functions do `import mlx.core as mx` at call time, so a fake package
    # in sys.modules is enough and no real MLX is needed to run this suite.
    pkg = types.ModuleType("mlx"); pkg.core = fake_mlx
    saved = {k: sys.modules.get(k) for k in ("mlx", "mlx.core")}
    sys.modules["mlx"], sys.modules["mlx.core"] = pkg, fake_mlx
    try:
        exec(compile(ast.Module(body=picked, type_ignores=[]), str(HELPER), "exec"), ns)
        ns["_run"] = lambda: None
        return ns, logged, saved
    except BaseException:
        for k, v in saved.items():
            if v is None: sys.modules.pop(k, None)
            else: sys.modules[k] = v
        raise


def _fake_mlx(peaks, active=1.0, cache=0.5, broken=False):
    """A stand-in that hands out `peaks` (GiB) one call at a time."""
    m = types.ModuleType("mlx.core")
    seq = list(peaks)
    m.resets = 0
    if not broken:
        m.get_peak_memory = lambda: (seq.pop(0) if seq else 0.0) * GIB
        m.get_active_memory = lambda: active * GIB
        m.get_cache_memory = lambda: cache * GIB
        def _reset():
            m.resets += 1
        m.reset_peak_memory = _reset
    return m


class PhaseMemory(unittest.TestCase):

    def _restore(self, saved):
        for k, v in saved.items():
            if v is None: sys.modules.pop(k, None)
            else: sys.modules[k] = v

    def test_each_phase_reports_its_own_peak_and_resets(self):
        fake = _fake_mlx([4.0, 11.5, 6.25])
        ns, logged, saved = _load(fake)
        try:
            ns["_mlx_mem_reset_run"]()
            self.assertEqual(ns["_mlx_phase_mem"]("weights"), 4.0)
            self.assertEqual(ns["_mlx_phase_mem"]("denoise"), 11.5)
            self.assertEqual(ns["_mlx_phase_mem"]("decode+save"), 6.25)
            lines = [m["line"] for m in logged]
            self.assertIn("[mlx] weights: peak 4.00 GiB", lines[0])
            self.assertIn("[mlx] denoise: peak 11.50 GiB", lines[1])
            self.assertIn("[mlx] decode+save: peak 6.25 GiB", lines[2])
            # A reset after every phase is what makes each line that phase's
            # OWN high-water mark instead of a running maximum.
            self.assertGreaterEqual(fake.resets, 3)
        finally:
            self._restore(saved)

    def test_run_summary_is_the_max_phase_not_the_last(self):
        """The decode phase is smaller than denoise; the run peak is denoise."""
        fake = _fake_mlx([4.0, 11.5, 6.25])
        ns, logged, saved = _load(fake)
        try:
            ns["_mlx_mem_reset_run"]()
            for label in ("weights", "denoise", "decode+save"):
                ns["_mlx_phase_mem"](label)
            self.assertEqual(ns["_mlx_mem_summary"](), 11.5)
            self.assertIn("[mlx] run peak 11.50 GiB", logged[-1]["line"])
        finally:
            self._restore(saved)

    def test_reset_run_clears_the_previous_jobs_peak(self):
        """Job N's spike must not be reported as job N+1's."""
        fake = _fake_mlx([30.0, 2.0])
        ns, logged, saved = _load(fake)
        try:
            ns["_mlx_mem_reset_run"]()
            ns["_mlx_phase_mem"]("huge job")
            self.assertEqual(ns["_mlx_mem_summary"](), 30.0)
            ns["_mlx_mem_reset_run"]()          # next job starts here
            ns["_mlx_phase_mem"]("small job")
            self.assertEqual(ns["_mlx_mem_summary"](), 2.0)
        finally:
            self._restore(saved)

    def test_missing_mlx_memory_api_is_silent_not_fatal(self):
        """Telemetry may never take a render down on an older library."""
        ns, logged, saved = _load(_fake_mlx([], broken=True))
        try:
            ns["_mlx_mem_reset_run"]()
            self.assertIsNone(ns["_mlx_phase_mem"]("weights"))
            self.assertEqual(logged, [])
            self.assertEqual(ns["_mlx_mem_summary"](), 0.0)
        finally:
            self._restore(saved)

    def test_summary_is_silent_when_nothing_was_measured(self):
        ns, logged, saved = _load(_fake_mlx([]))
        try:
            ns["_mlx_mem_reset_run"]()
            self.assertEqual(ns["_mlx_mem_summary"](), 0.0)
            self.assertEqual([m for m in logged if "run peak" in m["line"]], [])
        finally:
            self._restore(saved)


class WiredIntoTheRenderPath(unittest.TestCase):
    """The probes are useless if they are defined but never called."""

    def test_every_phase_boundary_calls_the_probe(self):
        src = HELPER.read_text()
        for call, least in (('_mlx_mem_reset_run()', 1),
                            ('_mlx_phase_mem("pipeline init")', 1),
                            ('_mlx_phase_mem("denoise")', 2),
                            ('_mlx_phase_mem("decode+save")', 2),
                            ('_mlx_mem_summary()', 2)):
            self.assertGreaterEqual(
                src.count(call), least,
                f"{call} is no longer wired into the render path")


if __name__ == "__main__":
    unittest.main()
