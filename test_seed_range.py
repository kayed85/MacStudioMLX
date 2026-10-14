"""4.17.3 fleet fix: a seed MLX refuses no longer fails the render.

THE BUG. mx.random.seed() takes an unsigned 64-bit int and nothing else.
The Seed box is free text, make_job stored it verbatim, and the helper did
`int(p.get("seed", -1))` and handed the result to the engine. So "-5" or a
20-digit seed pasted from another tool reached mx.random.seed() and the
render died with

    seed(): incompatible function arguments. The following argument types
    are supported: ...

(fleet: 22 failed renders 4.6.0 -> 4.17.2, every one retried with the same
seed and failing again). Text that is not a number ("abc") died one step
earlier, on int().

THE FIX. normalize_seed() at the panel boundary (make_job, every mode) and
the same rule on the helper's side (_coerce_seed) for queued jobs saved by an
older panel: -1 / empty / text -> random; out of range -> |n| mod 2**63,
deterministic so a pinned seed stays repeatable.

These tests EXECUTE mx.random.seed() on what the job actually carries — the
assertion is "the engine accepts it", not "the code looks right".
"""
from __future__ import annotations

import ast
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")
os.environ.setdefault("PHOSPHENE_DISABLE_VERSION_CHECK", "1")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402

try:
    import mlx.core as mx  # noqa: E402
except Exception:  # pragma: no cover - the gate venv always has mlx
    mx = None


def _helper_coerce():
    """_coerce_seed + _SEED_LIMIT straight out of mlx_warm_helper.py, without
    importing the helper (its import side effects start a pipeline world)."""
    src = (ROOT / "mlx_warm_helper.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    keep = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_coerce_seed":
            keep.append(node)
        elif (isinstance(node, ast.Assign) and len(node.targets) == 1
              and isinstance(node.targets[0], ast.Name)
              and node.targets[0].id == "_SEED_LIMIT"):
            keep.append(node)
    assert len(keep) == 2, "mlx_warm_helper.py lost _coerce_seed or _SEED_LIMIT"
    ns: dict = {}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "mlx_warm_helper.py", "exec"), ns)
    return ns["_coerce_seed"]


BAD_SEEDS = [
    "-5", "-2", "-9223372036854775809",
    "18446744073709551616",            # 2**64
    "123456789012345678901234567890",  # pasted from somewhere else
    "1.2345678901234568e+21",          # JS Number() of a long seed
    "abc", "  42  ", "1_000", "7.0",
]


class NormalizeSeed(unittest.TestCase):
    def test_contract(self):
        cases = {
            None: -1, "": -1, "-1": -1, -1: -1, "random": -1, "abc": -1,
            "1.5": -1, float("nan"): -1, True: -1,
            "0": 0, "42": 42, " 42 ": 42, "1_000": 1000, "7.0": 7, 7.0: 7,
            "-5": 5, -5: 5,
            str(2 ** 63 - 1): 2 ** 63 - 1,
            str(2 ** 63): 0,
            str(2 ** 64): (2 ** 64) % (2 ** 63),
        }
        for raw, want in cases.items():
            self.assertEqual(P.normalize_seed(raw), want, f"normalize_seed({raw!r})")

    def test_deterministic(self):
        big = "123456789012345678901234567890"
        self.assertEqual(P.normalize_seed(big), P.normalize_seed(big))
        self.assertGreaterEqual(P.normalize_seed(big), 0)
        self.assertLess(P.normalize_seed(big), P.SEED_LIMIT)

    def test_helper_agrees_with_panel(self):
        coerce = _helper_coerce()
        for raw in BAD_SEEDS + [None, "", "-1", "0", "42", 42, -1, 7.0,
                                str(2 ** 63), str(2 ** 64), "nan"]:
            self.assertEqual(coerce(raw), P.normalize_seed(raw), f"seed {raw!r}")

    def test_retry_offsets_stay_in_range(self):
        top = str(P.SEED_LIMIT - 1)
        r = int(P._take_retry_seed(top, 211 * 3))
        self.assertTrue(0 <= r < P.SEED_LIMIT)
        self.assertEqual(P._take_retry_seed("-1", 101), "-1")
        self.assertEqual(P._take_retry_seed("abc", 101), "-1")
        self.assertEqual(P._take_retry_seed("10", 101), "111")


@unittest.skipIf(mx is None, "mlx not importable")
class EngineAcceptsWhatTheJobCarries(unittest.TestCase):
    def _engine_seed(self, job_seed):
        # What the helper does with the job's seed before this release:
        # int() it, -1 -> random, hand it to mx.random.seed.
        n = int(str(job_seed))
        if n == -1:
            return
        mx.random.seed(n)

    def test_video_job_seeds(self):
        for raw in BAD_SEEDS:
            job = P.make_job({"mode": "t2v", "prompt": "a lighthouse at dusk",
                              "seed": raw})
            seed = job["params"]["seed"]
            try:
                self._engine_seed(seed)
            except (TypeError, ValueError) as exc:
                self.fail(f"seed {raw!r} -> job seed {seed!r} -> {exc}")

    def test_helper_side_for_old_saved_jobs(self):
        # A job queued by 4.17.2 still carries the raw text; the helper must
        # cope on its own.
        coerce = _helper_coerce()
        for raw in BAD_SEEDS:
            n = coerce(raw)
            if n >= 0:
                mx.random.seed(n)
                mx.random.seed(n + 2)   # a2vid_distilled's own +2


if __name__ == "__main__":
    unittest.main()
