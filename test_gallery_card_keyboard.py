"""VC-21: gallery cards are reachable and actionable from the keyboard.

WHAT THIS GUARDS. `.car-card` was a plain `<div onclick>` with no tabindex
or role — 0 of 39 cards focusable. Tabbing from the prompt reached the
toolbar and each card's own "Show generation info" / trash buttons, but
never the card itself, so a keyboard-only user could reach Trash but never
reach "select this clip". The thumbnail `<video>` (no `controls`, hover-
scrub preview only) sat in the default tab order too — Chrome makes a bare
`<video>` focusable even without `controls` — so with ~40 cards on screen
that was ~40 extra stops before reaching anything actionable.

Fixed: the card gets `role="button" tabindex="0"` and an Enter/Space
handler that calls the same selectOutput() its onclick does; the preview
`<video>` gets `tabindex="-1"` so it no longer competes for a stop. Arrow-
key stepping through the gallery already existed as a global shortcut
(shortcuts.js `outputs.step`, arrowleft/arrowright) — this fix is scoped to
what was actually missing: reaching a card at all, and activating it once
reached.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
QUEUE_JS = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")

NODE = shutil.which("node")


def _card_template() -> str:
    i = QUEUE_JS.index('class="car-card${o.path')
    j = QUEUE_JS.index("</div>`;", i)
    return QUEUE_JS[i - 20:j]


class CardMarkup(unittest.TestCase):
    def test_card_is_focusable_with_a_button_role(self):
        tpl = _card_template()
        self.assertIn('role="button"', tpl)
        self.assertIn('tabindex="0"', tpl)

    def test_card_has_a_keydown_handler_for_enter_and_space(self):
        tpl = _card_template()
        m = re.search(r'onkeydown="([^"]*)"', tpl)
        self.assertIsNotNone(m, "no onkeydown handler on the card")
        handler = m.group(1)
        self.assertIn("Enter", handler)
        self.assertIn("' '", handler)
        self.assertIn("selectOutput(", handler)
        self.assertIn("preventDefault", handler, "Space must not also scroll the page")

    def test_card_has_an_accessible_label(self):
        tpl = _card_template()
        self.assertIn("aria-label=", tpl)

    def test_preview_video_is_removed_from_tab_order(self):
        # VC-31 (2026-09-29): the card's video thumbnail is a plain
        # <img src="/poster?path=..."> now, not a live <video> element —
        # an <img> was never in the tab order to begin with, so the
        # tabindex="-1" fix this test used to pin no longer has anything
        # to guard. Assert the stronger, now-true thing instead: no
        # focusable <video> ships in the card template at all.
        i = QUEUE_JS.index("const thumbHtml = isAudio")
        j = QUEUE_JS.index(";", QUEUE_JS.index('car-thumb" src="/poster', i))
        snippet = QUEUE_JS[i:j]
        self.assertNotIn("<video", snippet)

    def test_audio_element_keeps_its_own_controls_focusable(self):
        """The <audio controls> card IS meant to be tabbable/interactive -
        this fix must not blanket every media element with tabindex=-1."""
        i = QUEUE_JS.index("music-card-player")
        j = QUEUE_JS.index("></audio>", i)
        snippet = QUEUE_JS[i:j]
        self.assertNotIn('tabindex="-1"', snippet)
        self.assertIn("controls", snippet)


class LiveKeyboardActivation(unittest.TestCase):
    """Runs the actual template logic in node against a DOM-ish stand-in to
    confirm the keydown handler string is valid JS and calls through."""

    def _run_node(self, script: str) -> dict:
        if NODE is None:
            raise unittest.SkipTest("node not on PATH")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(script)
            path = fh.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                raise AssertionError("node failed:\n%s\n%s" % (r.stdout[-2000:], r.stderr[-2000:]))
            return json.loads(r.stdout.strip().splitlines()[-1])
        finally:
            Path(path).unlink(missing_ok=True)

    def test_enter_and_space_both_call_selectoutput_once(self):
        tpl = _card_template()
        m = re.search(r'onkeydown="([^"]*)"', tpl)
        # The extracted string is the raw template-literal SOURCE (it still
        # contains the `${pathAttr}` placeholder); substitute a concrete
        # value to get real, runnable JS - same as what actually reaches
        # the browser once renderCarousel() interpolates it.
        handler_src = (m.group(1).replace("&quot;", '"')
                       .replace("${pathAttr}", "'/tmp/clip.mp4'"))
        script = f"""
let calls = [];
function selectOutput(p) {{ calls.push(p); }}
function run(key) {{
  let prevented = false;
  const event = {{ key, preventDefault: () => {{ prevented = true; }} }};
  (function() {{ {handler_src} }})();
  return prevented;
}}
const preventedEnter = run('Enter');
const preventedSpace = run(' ');
const preventedOther = run('Tab');
console.log(JSON.stringify({{ calls, preventedEnter, preventedSpace, preventedOther }}));
"""
        out = self._run_node(script)
        self.assertEqual(len(out["calls"]), 2)
        self.assertTrue(out["preventedEnter"])
        self.assertTrue(out["preventedSpace"])
        self.assertFalse(out["preventedOther"])


if __name__ == "__main__":
    unittest.main()
