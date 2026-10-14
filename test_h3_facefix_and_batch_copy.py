#!/usr/bin/env python3
"""H3-26 and H3-37: two small copy fixes that both named the wrong thing.

H3-26: Upscale & Face Fix's "after the draft" copy was unconditional, but
h3FaceFixAfterRow is shown whenever H3.upscale_modes allows ltx_x2 — not
only on the Draft tier (setH3Upscale in engines.js gates its visibility
on the allowed-modes list, with no quality check). A Standard or High
render offering the checkbox got copy naming the wrong tier, both in the
static label/title (index.html) and in the server-built export note
(_h3_export_notes, mlx_ltx_panel.py). Both now say "after this render" /
"about the same time again".

H3-37: h3FmtEtaMin() appended " · batch" to any chip estimated at 25+
minutes, with no explanation anywhere — and "Batch" is also the name of
an unrelated real feature elsewhere in the app (queueing several
renders), so the suffix read as pointing at the wrong thing. Reworded to
say what it actually means: "· long, start and walk away".

Verified live: setH3FaceFixAfter(true) on a Standard/5s H3 render
produced the export note "after this render ... About the same time
again." (previously named "the draft").
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX_HTML = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
ENGINES_JS = (ROOT / "webapp" / "js" / "engines.js").read_text(encoding="utf-8")
PANEL_PY = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")


class FaceFixCopyIsTierAgnostic(unittest.TestCase):
    def test_html_label_and_title_say_this_render(self):
        self.assertIn("Also run <b>Upscale &amp; Face Fix</b> after this render", INDEX_HTML)
        self.assertIn("about the same time again", INDEX_HTML)
        self.assertNotIn("after the draft", INDEX_HTML)

    def test_backend_export_note_says_this_render(self):
        self.assertIn('f"{FACE_FIX_NAME}: after this render, a second job re-renders it at "',
                       PANEL_PY)
        self.assertIn('"About the same time again."', PANEL_PY)
        self.assertNotIn('"after the draft, a second job', PANEL_PY)


class BatchSuffixExplainsItself(unittest.TestCase):
    def test_long_render_suffix_is_not_the_bare_word_batch(self):
        self.assertIn("long, start and walk away", ENGINES_JS)
        self.assertNotIn("' · batch'", ENGINES_JS)


if __name__ == "__main__":
    unittest.main()
