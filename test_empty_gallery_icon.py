"""VC-40: the empty gallery's blue dot doesn't read as a stuck spinner.

WHAT THIS GUARDS. `.empty-msg::before` painted a plain 28px circle in
`var(--accent-dim)` (the panel's blue accent) with a `box-shadow` glow, sitting
alone above "No outputs yet" on a first boot with nothing else on screen. It
never animated, but blue + glow + alone in an otherwise empty screen reads as
a spinner that got stuck - reported from a 16 GB first-open screenshot
(16gb_first_open_1440.png).

Fixed by giving the empty state a real icon instead: `queue.js`'s
`renderCarousel()` now renders `<svg class="ph empty-msg-icon"><use
href="#ph-…"/></svg>` picked per view (search / photos / audio / videos), and
`panel.css` drops the glowing `::before` circle for a plain muted icon style.

This file is the static half - it does not need a browser, just confirms the
dot is gone and every empty-state branch names a real icon in the sprite.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
JS = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
CSS = (ROOT / "webapp" / "style" / "panel.css").read_text(encoding="utf-8")
HTML = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")


class NoStrayDot(unittest.TestCase):
    def test_the_glowing_circle_is_gone(self):
        self.assertNotIn(".empty-msg::before", CSS)
        self.assertNotIn("box-shadow: 0 0 12px rgba(47,129,247", CSS)

    def test_empty_msg_renders_a_real_icon(self):
        self.assertIn("empty-msg-icon", JS)
        m = re.search(r"const icon = [\s\S]*?el\.innerHTML = [\s\S]*?</div>`;", JS)
        self.assertIsNotNone(m, "no `const icon = …` block feeding empty-msg")
        body = m.group(0)
        self.assertIn('svg class="ph empty-msg-icon"', body)
        # every branch of the icon ternary must name a symbol that actually
        # exists in the sprite - a typo here would silently render nothing
        for icon in re.findall(r"'(ph-[a-z0-9-]+)'", body):
            self.assertIn(f'<symbol id="{icon}"', HTML,
                          f"{icon} referenced by the empty state isn't in the sprite")
        # and every one of the four views (search / photos / audio /
        # videos / fallback) is represented, not just the default
        for icon in ("ph-magnifying-glass", "ph-image", "ph-music-notes",
                     "ph-film-strip"):
            self.assertIn(icon, body)

    def test_empty_msg_icon_has_a_style_rule(self):
        self.assertIn(".empty-msg-icon {", CSS)


if __name__ == "__main__":
    unittest.main()
