"""The route tables and their registration decorators.

`GET_ROUTES` maps an exact `urlparse(path).path` to a handler called as
`handler(h, parsed)`; `POST_ROUTES` maps an exact query-stripped path to
a handler called as `handler(h, path, qs, ctype)` — the same locals the
legacy chains in mlx_ltx_panel.py work with, so a handler body moves
between the two worlds without rewording. `h` is the live Handler
instance (what the chain calls `self`).

Registration refuses a duplicate at import time: two modules claiming
one route is the routes version of the built-twice incident, and the
later registration silently winning is exactly the failure mode this
table exists to end. test_routes.py additionally asserts, statically,
that no table route still has a twin in the legacy chain.
"""
from __future__ import annotations

from typing import Callable

GET_ROUTES: dict[str, Callable] = {}
POST_ROUTES: dict[str, Callable] = {}


class DuplicateRouteError(RuntimeError):
    pass


def _register(table: dict, method: str, path: str):
    def deco(fn):
        if path in table:
            raise DuplicateRouteError(
                f"{method} {path} registered twice: "
                f"{table[path].__module__}.{table[path].__name__} and "
                f"{fn.__module__}.{fn.__name__}")
        table[path] = fn
        return fn
    return deco


def get(path: str):
    return _register(GET_ROUTES, "GET", path)


def post(path: str):
    return _register(POST_ROUTES, "POST", path)


# Pattern routes — the chain's startswith/endswith arms. Each entry is
# (matcher, handler): the matcher takes the path STRING and answers
# bool; a matched handler always handles (exactly the old arms' shape,
# so their bodies move verbatim, bare returns and all). Dispatch walks
# the list in REGISTRATION order after an exact-table miss — order is
# load-bearing: "/x/sheet/generate" endswith both "/sheet/generate"
# and "/generate", and the chain always tested the longer one first.
GET_PATTERNS: list[tuple[Callable, Callable]] = []
POST_PATTERNS: list[tuple[Callable, Callable]] = []


def get_when(matcher: Callable):
    def deco(fn):
        GET_PATTERNS.append((matcher, fn))
        return fn
    return deco


def post_when(matcher: Callable):
    def deco(fn):
        POST_PATTERNS.append((matcher, fn))
        return fn
    return deco
