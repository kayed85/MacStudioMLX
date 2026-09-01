#!/usr/bin/env python3
"""Pull named JS functions (and the HTML) out of mlx_ltx_panel.py so tests can RUN them.

The panel is one Python file that contains its own HTML, CSS and client JS as a
string. That is the whole reason the Character contract could drift three ways at
once without a gate noticing: the client half was untestable, so the first
version of the round-trip gate "tested" it by grepping the source for the calls it
hoped were there. It passed while the live loader was still broken.

A test that passes by finding strings is worse than no test, because it also
reports that the area is covered. This module is the fix: it extracts the real
function bodies and the real markup, and the test executes them in node against a
DOM shim. If the function stops doing the thing, the test fails — which is the
only property that makes a gate worth having.

Extraction is deliberately dumb and strict: brace-matching from a known
`function <name>(` header, and it RAISES if a requested function is missing or
unbalanced. A rename must break the test loudly rather than silently reduce
coverage back to grepping.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PANEL = ROOT / "mlx_ltx_panel.py"
# Slices 1–2 of the extraction (docs/ARCHITECTURE.md) moved the CSS to
# webapp/style/panel.css and the page — markup + JS — to webapp/index.html.
CSS = ROOT / "webapp" / "style" / "panel.css"
INDEX = ROOT / "webapp" / "index.html"


class ExtractError(RuntimeError):
    pass


def panel_source() -> str:
    # Everything the panel serves, in the order the pieces sat in the
    # pre-extraction single file: Python, then the CSS, then the page
    # (markup + JS), then the JS modules in the order their <script
    # type="module"> tags load them. This keeps every existing
    # extract_function / extract_element call — and the tests that slice
    # CSS regions out of "the panel source" — addressing the real code
    # wherever it lives. Slice 3 migrates the extraction-based tests to
    # import the real JS module files directly; when the last one is
    # migrated, this module is deleted.
    parts = [PANEL.read_text(encoding="utf-8")]
    # The route handlers moved out of the panel's do_GET/do_POST chains
    # into panel/routes_*.py (slice 4) — still server code the tests
    # address as "the panel source", so they ride directly after it.
    for rf in sorted((ROOT / "panel").glob("*.py")):
        parts.append(rf.read_text(encoding="utf-8"))
    parts.append(CSS.read_text(encoding="utf-8"))
    index = INDEX.read_text(encoding="utf-8")
    parts.append(index)
    for m in re.finditer(
            r'<script type="module" src="/webapp/js/([\w.-]+\.js)"></script>',
            index):
        parts.append((ROOT / "webapp" / "js" / m.group(1)).read_text(encoding="utf-8"))
    return "\n".join(parts)


def extract_function(name: str, src: str | None = None) -> str:
    """The full text of `function <name>(...) { ... }`, braces balanced."""
    s = src if src is not None else panel_source()
    pat = r"^(?:async\s+)?function\s+%s\s*\(" % re.escape(name)
    hits = list(re.finditer(pat, s, re.M))
    if not hits:
        raise ExtractError("function %s() not found in mlx_ltx_panel.py" % name)
    if len(hits) > 1:
        # A duplicated declaration is exactly the parallel-edit accident this
        # repo has now shipped twice (the health chip, both times). Returning
        # the FIRST match here meant the tests covered the copy the browser
        # does not run — JS hoisting makes the LAST declaration win. Refusing
        # is the only honest behaviour: the caller must fix the panel, not
        # pick a copy.
        raise ExtractError(
            "function %s() is declared %d times in mlx_ltx_panel.py — "
            "duplicate definitions must be removed before it can be tested"
            % (name, len(hits)))
    m = hits[0]
    start = m.start()
    i = s.index("{", m.end() - 1)
    depth, j, in_s, esc, in_c, in_lc = 0, i, "", False, False, False
    while j < len(s):
        ch = s[j]
        nxt = s[j + 1] if j + 1 < len(s) else ""
        if in_lc:
            if ch == "\n":
                in_lc = False
        elif in_c:
            if ch == "*" and nxt == "/":
                in_c = False
                j += 1
        elif in_s:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == in_s:
                in_s = ""
        elif ch in "\"'`":
            in_s = ch
        elif ch == "/" and nxt == "/":
            in_lc = True
            j += 1
        elif ch == "/" and nxt == "*":
            in_c = True
            j += 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start:j + 1]
        j += 1
    raise ExtractError("unbalanced braces while extracting %s()" % name)


def extract_object(name: str, src: str | None = None) -> str:
    """`window.CHARACTERS = { ... };` — the state object, verbatim."""
    s = src if src is not None else panel_source()
    m = re.search(r"^window\.%s\s*=\s*" % re.escape(name), s, re.M)
    if not m:
        raise ExtractError("window.%s not found" % name)
    i = s.index("{", m.end() - 1)
    depth, j, in_s, esc, in_c, in_lc = 0, i, "", False, False, False
    while j < len(s):
        ch = s[j]
        nxt = s[j + 1] if j + 1 < len(s) else ""
        if in_lc:
            if ch == "\n":
                in_lc = False
        elif in_c:
            if ch == "*" and nxt == "/":
                in_c = False
                j += 1
        elif in_s:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == in_s:
                in_s = ""
        elif ch in "\"'`":
            in_s = ch
        elif ch == "/" and nxt == "/":
            in_lc = True
            j += 1
        elif ch == "/" and nxt == "*":
            in_c = True
            j += 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[i:j + 1]
        j += 1
    raise ExtractError("unbalanced braces while extracting window.%s" % name)


def extract_element(element_id: str, src: str | None = None) -> str:
    """The raw markup of the tag carrying id="<element_id>". Used to read what a
    control actually DISPLAYS, which is the half a grep-based test cannot see."""
    s = src if src is not None else panel_source()
    m = re.search(r"<[a-zA-Z][^>]*\bid=\"%s\"[^>]*>" % re.escape(element_id), s)
    if not m:
        raise ExtractError('no element with id="%s"' % element_id)
    return m.group(0)


def attr(markup: str, name: str) -> str | None:
    m = re.search(r'\b%s="([^"]*)"' % re.escape(name), markup)
    return m.group(1) if m else None


if __name__ == "__main__":
    import sys
    for fn in sys.argv[1:]:
        print(extract_function(fn))
