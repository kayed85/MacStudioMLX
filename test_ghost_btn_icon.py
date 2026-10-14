"""VC-17: the Enhance button (and every ghost-btn like it) lays its icon and
label out side by side, and every "-fill" icon actually takes the button's
text colour instead of defaulting to black.

TWO INDEPENDENT DEFECTS, same button, both from the same review shot
(zoom_enhance_row.png).

1. LAYOUT. `#enhanceBtn`'s parent, `.composer-tools`, is `display: flex`.
   Per the CSS Display spec a flex item is blockified for layout purposes,
   so a plain `<button>` (UA-default `display: inline-block`) computes to
   `display: block` there - confirmed live: 70x30 box, 37px of content
   ("Enhance" plus its icon stacking on separate lines instead of sitting
   side by side). `panel.css` already had this exact fix applied to
   `.row-actions .ghost-btn` and `.sb-film-actions .ghost-btn` individually
   (grep either selector) - `.composer-tools .ghost-btn` was simply never
   one of the patched contexts. Putting `display: inline-flex; align-items:
   center; gap: 6px; white-space: nowrap` on the BASE `.ghost-btn` rule
   instead of another one-off selector closes the whole class: any
   ghost-btn under any flex/grid ancestor, patched or not yet discovered,
   lays out correctly.

2. COLOUR. Every "*-fill" icon in the inline Phosphor sprite is meant to
   render as a solid shape in the current text colour - and every one of
   them does that by carrying `fill="currentColor"` on its own `<path>`
   (`.ph { color: currentColor }` in panel.css does NOT set the SVG `fill`
   property, so this per-path attribute is the only thing making that
   work; SVG's own initial value for `fill` is black). ph-sparkle-fill
   (Enhance's icon), ph-info-fill, ph-pause-fill, ph-play-fill,
   ph-warning-fill and ph-x-circle-fill were all missing it - a solid
   black icon on Enhance's own light text, and on Phosphene's dark theme
   effectively invisible wherever these render. Fixed by adding
   `fill="currentColor"` to each, matching the convention every other
   "-fill" symbol in the sprite already follows.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HTML = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
_RAW_CSS = (ROOT / "webapp" / "style" / "panel.css").read_text(encoding="utf-8")
# Strip comments before brace-matching below - several of them (this file's
# own .ghost-btn block included) contain example CSS with braces of their
# own, which would otherwise end a naive `{...}` capture early.
CSS = re.sub(r"/\*.*?\*/", "", _RAW_CSS, flags=re.S)


def _symbol_body(symbol_id: str) -> str:
    m = re.search(
        r'<symbol id="' + re.escape(symbol_id) + r'"[^>]*>(.*?)</symbol>', HTML)
    assert m, f"symbol #{symbol_id} not found in the sprite"
    return m.group(1)


class GhostBtnLaysOutInline(unittest.TestCase):
    def test_base_rule_is_inline_flex(self):
        m = re.search(r"\.ghost-btn\s*\{([^}]*)\}", CSS)
        self.assertIsNotNone(m, "no base .ghost-btn rule")
        body = m.group(1)
        self.assertIn("display: inline-flex", body)
        self.assertIn("align-items: center", body)
        self.assertIn("white-space: nowrap", body)

    def test_enhance_button_is_a_ghost_btn_under_a_flex_ancestor(self):
        """Pin the exact case the review caught: #enhanceBtn (.ghost-btn)
        sits under .composer-tools, which is display:flex - so this button
        was one of the ones the base-rule fix (above) needed to reach."""
        m = re.search(r'<button[^>]*id="enhanceBtn"[^>]*>', HTML)
        self.assertIsNotNone(m)
        self.assertIn('class="ghost-btn"', m.group(0))
        i = HTML.index('<div class="composer-tools">')
        j = HTML.index('id="enhanceBtn"')
        self.assertLess(i, j, "#enhanceBtn must be inside .composer-tools")
        ct = re.search(r"\.composer-tools\s*\{([^}]*)\}", CSS)
        self.assertIsNotNone(ct)
        self.assertIn("display: flex", ct.group(1))


class FillIconsCarryTheirOwnColour(unittest.TestCase):
    """Every symbol whose id ends "-fill" is meant to paint as a solid shape
    in the current text colour. Assert every one of them says so, not just
    the icon the review happened to screenshot."""

    def test_every_fill_symbol_has_fill_currentcolor(self):
        ids = sorted(set(re.findall(r'<symbol id="(ph-[a-z0-9-]*-fill)"', HTML)))
        self.assertIn("ph-sparkle-fill", ids)
        self.assertGreaterEqual(len(ids), 6, ids)
        missing = []
        for sid in ids:
            body = _symbol_body(sid)
            # every drawable child (path/circle/rect/polygon/ellipse) needs
            # its own fill, since `.ph` sets no CSS `fill` of its own
            for tag in re.finditer(r"<(path|circle|rect|polygon|ellipse)\b[^>]*/>", body):
                if 'fill=' not in tag.group(0):
                    missing.append(f"{sid}: {tag.group(0)[:80]}")
        self.assertEqual(missing, [], "fill icon(s) missing fill=currentColor:\n  "
                                      + "\n  ".join(missing))

    def test_sparkle_fill_is_currentcolor_not_a_literal(self):
        body = _symbol_body("ph-sparkle-fill")
        self.assertIn('fill="currentColor"', body)


if __name__ == "__main__":
    unittest.main()
