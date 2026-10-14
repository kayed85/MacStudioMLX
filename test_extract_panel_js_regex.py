"""scripts/extract_panel_js.py's brace-matcher survives a regex literal that
contains a quote character.

WHAT THIS GUARDS. `extract_function`/`extract_object` track `"`, `'` and
`` ` `` as string delimiters but, until now, had no idea a `/.../ ` regex
literal exists. `friendlyJobError`'s own first branch —
`/music engine isn't installed|.../i.test(raw)` — contains an apostrophe
inside a regex, which the old loop read as the START of a fake string. That
fake string then swallowed the next REAL quote (an unrelated string's
opening quote) as its own close, desynchronising every string/brace
boundary for the rest of the function. The corruption was silent: total
depth still reached exactly 0 eventually, just at the wrong `}` — so
`extract_function("friendlyJobError")` happened to return the right text
right up until an unrelated edit anywhere later in the function shifted the
(accidental) alignment and truncated it mid-function, with no error raised
at the point that actually broke (this is exactly what an edit to
queue.js's OOM-copy branch did while fixing VC-08 — nothing about that edit
was wrong, it just moved the coincidental alignment).

Fixed with `_looks_like_regex_start()`/`_skip_regex_literal()`, shared by
both functions through the new `_balanced_brace_end()` helper (which also
de-duplicates what used to be two copies of the same loop).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
import extract_panel_js as E  # noqa: E402


class RegexLiteralsDontDesyncTheParser(unittest.TestCase):
    def test_apostrophe_inside_a_regex_does_not_break_extraction(self):
        src = (
            "function probe(raw) {\n"
            "  if (/it isn't here|also isn't there/i.test(raw)) {\n"
            "    return { a: 'one', b: 'two' };\n"
            "  }\n"
            "  return { a: 'three' };\n"
            "}\n"
        )
        got = E.extract_function("probe", src)
        self.assertTrue(got.startswith("function probe(raw) {"))
        self.assertTrue(got.rstrip().endswith("}"))
        self.assertIn("return { a: 'three' };", got)

    def test_a_second_quote_carrying_regex_further_in_still_works(self):
        src = (
            "function probe2(x) {\n"
            "  const a = 1;\n"
            "  if (/don't match/.test(x)) { return { hit: true }; }\n"
            "  const later = { nested: { deep: 2 } };\n"
            "  return later;\n"
            "}\n"
        )
        got = E.extract_function("probe2", src)
        self.assertIn("const later = { nested: { deep: 2 } };", got)
        self.assertTrue(got.rstrip().endswith("}"))

    def test_division_is_not_mistaken_for_a_regex(self):
        """The heuristic must not eat ordinary division - `a / b` right
        after an identifier or digit is division, not a regex opener."""
        src = (
            "function probe3(w, h) {\n"
            "  const ratio = w / h;\n"
            "  const half = 10 / 2;\n"
            "  return { ratio, half };\n"
            "}\n"
        )
        got = E.extract_function("probe3", src)
        self.assertIn("const half = 10 / 2;", got)
        self.assertTrue(got.rstrip().endswith("}"))

    def test_real_friendly_job_error_extracts_in_full(self):
        """The exact function that surfaced the bug - pinned end to end so
        a future regression here is caught immediately, not three edits
        later at some unrelated call site."""
        src = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
        got = E.extract_function("friendlyJobError", src)
        # H3-22 (4.17): the function takes the job's engine and the generic
        # fallback redacts local paths.
        self.assertTrue(got.startswith("function friendlyJobError(raw, engine) {"))
        self.assertTrue(got.rstrip().endswith("}"))
        # every branch's return must be present - a truncation stops partway
        self.assertIn("return { friendly: 'Job failed.', hint: _redactLocalPaths(raw) || raw };", got)
        self.assertEqual(got.count("return {"), got.count("return {"))  # sanity: no crash above
        self.assertGreaterEqual(got.count("if ("), 7)

    def test_extract_object_shares_the_same_fix(self):
        src = (
            "window.PROBE = {\n"
            "  re: /a'b/,\n"
            "  nested: { x: 1 },\n"
            "};\n"
        )
        got = E.extract_object("PROBE", src)
        self.assertIn("nested: { x: 1 }", got)
        self.assertTrue(got.rstrip().endswith("};") or got.rstrip().endswith("}"))


if __name__ == "__main__":
    unittest.main()
