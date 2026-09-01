#!/usr/bin/env python3
"""Every HTTP route is claimed exactly once — and only in panel/routes_*.py.

Slice 4 of the restructuring (docs/ARCHITECTURE.md) moved all 101 routes
out of do_GET/do_POST's if/elif chains into the panel.routes tables
(exact paths) and ordered pattern lists (startswith/endswith families).
The end state this file pins:

  * the chains hold ZERO path arms — a new `if path == ...` in do_GET or
    do_POST is refused outright, because a chain arm shadows or is
    shadowed by the tables depending on where it lands, and either way
    it is a second dispatch mechanism nobody will remember to check;
  * per method, an exact path is registered exactly once (the
    registration decorator enforces this at import; asserted here too so
    a refactor of the decorator cannot silently drop the property);
  * the route census stays at full strength — the tables shrinking to a
    handful would mean registration silently stopped importing.

The pattern lists are ORDER-SENSITIVE ("/x/sheet/generate" ends with
both "/sheet/generate" and "/generate"); the sheet/generate-before-
generate ordering is asserted concretely.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = Path(tempfile.mkdtemp(prefix="phos-routes-"))
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8309")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402  (imports and wires panel.routes_*)
from panel.routes import (GET_PATTERNS, GET_ROUTES, POST_PATTERNS,  # noqa: E402
                          POST_ROUTES)

SRC_LINES = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8").split("\n")


def _method_region(name: str) -> list[str]:
    start = next(n for n, l in enumerate(SRC_LINES)
                 if l.strip() == f"def {name}(self) -> None:")
    end = next(n for n in range(start + 1, len(SRC_LINES))
               if re.match(r"    def \w+", SRC_LINES[n]))
    return SRC_LINES[start:end]


class TheChainsStayEmpty(unittest.TestCase):
    def test_no_path_arm_may_return_to_the_chains(self) -> None:
        for fn, var in (("do_GET", "parsed.path"), ("do_POST", "path")):
            region = _method_region(fn)
            arms = [l.strip() for l in region
                    if re.match(rf"\s+if {re.escape(var)}\s*(==|in |\.startswith|\.endswith)", l)]
            self.assertEqual(arms, [],
                             f"{fn} grew a path arm again — routes register "
                             f"in panel/routes_*.py, never as chain arms: {arms}")

    def test_the_dispatch_methods_stay_small(self) -> None:
        # 31 and 41 lines at the close of slice 4. Growth here means logic
        # is accreting in the dispatcher instead of the route modules.
        for fn, ceiling in (("do_GET", 45), ("do_POST", 55)):
            n = len(_method_region(fn))
            self.assertLessEqual(n, ceiling,
                                 f"{fn} is {n} lines — dispatch only, "
                                 f"handlers belong in panel/routes_*.py")


class RoutesRegisteredOnce(unittest.TestCase):
    def test_the_census_is_at_full_strength(self) -> None:
        total = (len(GET_ROUTES) + len(POST_ROUTES)
                 + len(GET_PATTERNS) + len(POST_PATTERNS))
        self.assertGreaterEqual(
            total, 95,
            f"only {total} routes registered — registration silently "
            f"stopped importing a family?")

    def test_registration_refuses_duplicates(self) -> None:
        from panel.routes import DuplicateRouteError, get
        with self.assertRaises(DuplicateRouteError):
            get("/status")(lambda h, parsed: None)

    def test_table_handlers_are_wired_to_the_running_panel(self) -> None:
        tables = [GET_ROUTES.values(), POST_ROUTES.values(),
                  (fn for _, fn in GET_PATTERNS),
                  (fn for _, fn in POST_PATTERNS)]
        for fns in tables:
            for fn in fns:
                mod = sys.modules[fn.__module__]
                self.assertIs(getattr(mod, "P", None), P,
                              f"{fn.__module__}.{fn.__name__} was never "
                              f"wired: its P is not the running panel module")

    def test_pattern_order_sheet_generate_before_generate(self) -> None:
        # "/x/sheet/generate" matches both matchers; the list order is the
        # only thing keeping the specific one first.
        probe = "/characters/x/sheet/generate"
        matches = [fn.__name__ for m, fn in POST_PATTERNS if m(probe)]
        self.assertGreaterEqual(len(matches), 2,
                                "expected both generate matchers to fire")
        self.assertEqual(matches[0], "post_character_sheet_generate",
                         "sheet/generate must be registered before the "
                         "plain /generate matcher")


if __name__ == "__main__":
    unittest.main(verbosity=2)
