#!/usr/bin/env python3
"""FILM-28: the loudness-normalize toggle in the Render popover.

`-14 LUFS` is a fourth, independent delivery axis (server side: `test_deliver.py`,
`test_storyboard_film_integrity.py`). This file locks the client half — the
checkbox exists, persists in the same `phos_deliver` bag as format/size/finish,
survives a repaint, is reflected in the Render button's own label, and is
published so its `onchange` is not a silent no-op in the browser.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

from extract_panel_js import extract_function, panel_source  # noqa: E402

from test_editor_save_integrity import run  # noqa: E402

EXTRA = ("sbeDeliverGet", "sbeDeliverPick", "sbeDeliverPaint",
         "sbeDeliverToggleLoudnorm")


class TheToggle(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.src = panel_source()
        # sbeDeliverGet reads these three module-level const arrays — not
        # functions, so extract_function can't pull them; grab the exact
        # served lines instead of restating the values by hand.
        consts = "\n".join(re.findall(
            r"^const SBE_DELIVER_(?:FORMATS|SIZES|FINISH) = \[.*?\];$",
            cls.src, re.M))
        assert consts.count("const") == 3, consts
        # sbeDeliverPaint also toggles the format/size/finish pill buttons
        # via document.querySelectorAll — irrelevant to this file (that's
        # covered by hand elsewhere the same way every other popover pill
        # is), so it's a no-op query here rather than a full DOM.
        dom_shim = consts + "\nglobal.document = { querySelectorAll: () => [] };\n"
        cls.r = run(r"""
els.sbeRenderBtn = Object.assign(stubEl('sbeRenderBtn'), { textContent: 'Render' });
out.defaultLoudnorm = sbeDeliverGet().loudnorm;
sbeDeliverToggleLoudnorm(true);
out.afterOn = sbeDeliverGet().loudnorm;
out.checkboxAfterOn = els.sbeDeliverLoudnorm.checked;
out.labelOn = els.sbeRenderBtn.textContent;
out.titleOn = els.sbeRenderBtn.title;
sbeDeliverToggleLoudnorm(false);
out.afterOff = sbeDeliverGet().loudnorm;
out.checkboxAfterOff = els.sbeDeliverLoudnorm.checked;
out.labelOff = els.sbeRenderBtn.textContent;
// It rides the SAME bag as format/size/finish — one localStorage key, not two.
sbeDeliverPick('format', 'hevc');
out.survivesOtherPicks = sbeDeliverGet().loudnorm;
sbeDeliverToggleLoudnorm(true);
sbeDeliverPick('size', '1080p');
out.stillOnAfterSizePick = sbeDeliverGet().loudnorm;
out.combinedLabel = els.sbeRenderBtn.textContent;
""", extra=EXTRA, shim=dom_shim)

    def test_off_by_default(self):
        self.assertFalse(self.r["defaultLoudnorm"])

    def test_toggling_flips_the_stored_value_and_the_checkbox(self):
        self.assertTrue(self.r["afterOn"])
        self.assertTrue(self.r["checkboxAfterOn"])
        self.assertFalse(self.r["afterOff"])
        self.assertFalse(self.r["checkboxAfterOff"])

    def test_the_render_button_says_so(self):
        self.assertIn("LUFS", self.r["labelOn"])
        self.assertIn("LUFS", self.r["titleOn"])
        self.assertNotIn("LUFS", self.r["labelOff"])

    def test_it_shares_the_bag_and_survives_other_picks(self):
        self.assertFalse(self.r["survivesOtherPicks"])
        self.assertTrue(self.r["stillOnAfterSizePick"])
        self.assertIn("HEVC", self.r["combinedLabel"])
        self.assertIn("1080", self.r["combinedLabel"])
        self.assertIn("LUFS", self.r["combinedLabel"])

    def test_the_checkbox_exists_in_the_render_popover_and_calls_the_setter(self):
        m = re.search(
            r'<input type="checkbox" id="sbeDeliverLoudnorm"\s+'
            r'onchange="sbeDeliverToggleLoudnorm\(this\.checked\)">', self.src)
        self.assertIsNotNone(m, "loudnorm checkbox markup missing or "
                             "not wired to sbeDeliverToggleLoudnorm")
        # It lives in the Deliver-as popover, before the Export-for-NLE
        # separator — same surface as format/size/finish, not a new one.
        pop = self.src[self.src.index('id="sbeDeliverFinish"'):
                       self.src.index('id="sbeNleBtn"')]
        self.assertIn('id="sbeDeliverLoudnorm"', pop)

    def test_it_is_published_so_the_onchange_is_not_a_dead_click(self):
        publish = self.src[self.src.index("Object.assign(globalThis, {\n  sbeStripY"):]
        publish = publish[:publish.index("});")]
        self.assertRegex(publish, r"\bsbeDeliverToggleLoudnorm\b")

    def test_paint_reads_the_stored_value_into_the_checkbox_on_repaint(self):
        # A repaint (e.g. after opening the popover fresh) must not fight a
        # value the setter already wrote — sbeDeliverPaint has to read the
        # SAME source of truth, not reset it to false.
        fn = extract_function("sbeDeliverPaint", self.src)
        self.assertIn("sbeDeliverLoudnorm", fn)
        self.assertIn("d.loudnorm", fn)


if __name__ == "__main__":
    unittest.main()
