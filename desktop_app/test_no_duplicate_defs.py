"""No symbol in the panel may be defined twice.

This gate exists because the same accident has shipped twice: a feature
(the health chip, both times) applied to mlx_ltx_panel.py by two parallel
sessions, leaving two complete implementations in one file. In JS the later
declaration silently wins by hoisting; in HTML duplicate ids make
getElementById nondeterministic by spec; and the extraction-based tests were
covering the dead copy. Nothing crashed — which is why nothing was noticed
until a fleet review diffed referenced ids against defined ones.

Three assertions, all cheap greps over the panel source:
  1. no JS `function NAME(` declared twice at top level,
  2. no HTML id="X" appearing twice OUTSIDE comments,
  3. no Python `def name(` at module top level twice.
"""

import re
import unittest
from pathlib import Path

PANEL = Path(__file__).with_name("mlx_ltx_panel.py")
# Slices 2–3 of the extraction (docs/ARCHITECTURE.md) moved the page to
# webapp/index.html and its JS into ES modules under webapp/js/. The
# JS/id region is the page PLUS every module: ES module scope would
# technically permit the same top-level name in two files, but that is
# exactly the built-twice shape this gate exists for, so it stays
# forbidden across the whole frontend.
INDEX = Path(__file__).parent / "webapp" / "index.html"
JSDIR = Path(__file__).parent / "webapp" / "js"


def _regions():
    py = PANEL.read_text()
    html = INDEX.read_text()
    for f in sorted(JSDIR.glob("*.js")) if JSDIR.is_dir() else []:
        html += "\n" + f.read_text()
    return py, html


class NoDuplicateDefinitions(unittest.TestCase):
    def test_no_duplicate_js_functions(self):
        _, html = _regions()
        # strip HTML comments so commented-out markup/scripts don't count
        html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
        names = re.findall(r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(",
                           html, re.M)
        seen, dupes = set(), set()
        for n in names:
            (dupes if n in seen else seen).add(n)
        self.assertFalse(
            dupes,
            "JS functions declared more than once (later shadows earlier, "
            "and tests cover the dead copy): %s" % sorted(dupes))

    def test_no_duplicate_html_ids(self):
        _, html = _regions()
        html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
        # static markup only: ids minted inside template literals are runtime
        # (and often intentionally repeated per-card), so restrict to lines
        # that look like markup, not JS strings.
        ids = re.findall(r'^\s*<[^>]*\bid="([A-Za-z_][\w-]*)"', html, re.M)
        seen, dupes = set(), set()
        for n in ids:
            (dupes if n in seen else seen).add(n)
        self.assertFalse(
            dupes,
            "HTML ids defined more than once (getElementById picks one "
            "arbitrarily): %s" % sorted(dupes))

    def test_no_duplicate_python_defs(self):
        py, _ = _regions()
        names = re.findall(r"^def\s+([A-Za-z_]\w*)\s*\(", py, re.M)
        seen, dupes = set(), set()
        for n in names:
            (dupes if n in seen else seen).add(n)
        self.assertFalse(
            dupes,
            "module-level Python functions defined twice (the later "
            "silently replaces the earlier): %s" % sorted(dupes))

    def test_extractor_refuses_duplicates(self):
        """The gate that failed last time: extract_function returned the FIRST
        match, so JS tests passed against the copy the browser never ran."""
        import sys
        sys.path.insert(0, str(PANEL.parent / "scripts"))
        import extract_panel_js as ex
        src = "function alpha() { return 1; }\nfunction alpha() { return 2; }\n"
        with self.assertRaises(ex.ExtractError):
            ex.extract_function("alpha", src)


if __name__ == "__main__":
    unittest.main()
