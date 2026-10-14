#!/usr/bin/env python3
"""VC-20 [P2] [BUG] The "Audio" gallery filter turns the Video tab's player
into the Music studio and relabels the model as YuE2.

WHAT THIS GUARDS. selectOutput() runs for every gallery selection on every
tab (it is the one function behind clicking any card). For a song
(kind=='audio', engine=='music') it unconditionally: (1) swapped the whole
player pane for songHero() - cover art, transport, the Music tab's own
chrome; (2) filled the Song card underneath with New take / Re-roll the
sound / New style on this score / Cover it / Sheet music; (3) via
updateModelCredit(), relabelled the header's model chip "YuE2 · MLX by
vanch007", even though the LTX form on the left was still the one about to
run. None of that is wrong on the Audio tab itself - it is the Audio tab's
whole purpose. It was wrong on the Video tab: filtering Outputs to Audio and
clicking a song transplanted another tab's UI into this one.

Fixed by gating all three on `document.body.dataset.workflow === 'audio'`:
off that tab a song gets the same compact inline <audio> player any other
audio clip gets, with an "Open in Audio tab" link (openSongInAudioTab, new)
that switches tabs and re-selects the same clip - which DOES get the full
treatment, because now onAudioTab is true.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
from extract_panel_js import extract_function  # noqa: E402

NODE = shutil.which("node")
BOOT_JS = (ROOT / "webapp" / "js" / "boot.js").read_text(encoding="utf-8")
MUSIC_JS = (ROOT / "webapp" / "js" / "music.js").read_text(encoding="utf-8")
QUEUE_JS = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")


def _run_node(script: str) -> dict:
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        r = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise AssertionError("node failed:\n%s\n%s" % (r.stdout[-3000:], r.stderr[-3000:]))
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------- 1
# updateModelCredit: a real node execution, since the function is small and
# self-contained (one DOM element, BOOT, currentOutputs, activePath).
class TestModelCreditFollowsTheTabNotJustTheClip(unittest.TestCase):
    def _fn(self) -> str:
        return extract_function("updateModelCredit", BOOT_JS) + "\n" + \
            extract_function("_modelCreditLabel", BOOT_JS)

    def _run(self, workflow: str) -> dict:
        script = f"""
const BOOT = {{ model: "mlx_models/ltx-2.5-mlx-q4/transformer-distilled.safetensors" }};
let activePath = '/out/song1.mp3';
const currentOutputs = [
  {{ path: '/out/song1.mp3', kind: 'audio', engine: 'music', model: 'yue2' }},
];
const modelTagEl = {{ textContent: '', title: '', href: '' }};
const document = {{
  body: {{ dataset: {{ workflow: {workflow!r} }} }},
  getElementById(id) {{
    if (id !== 'modelTag') return null;
    return modelTagEl;
  }},
}};
{self._fn()}
updateModelCredit();
console.log(JSON.stringify({{ text: modelTagEl.textContent, title: modelTagEl.title }}));
"""
        return _run_node(script)

    def test_on_the_audio_tab_the_song_is_named(self):
        out = self._run('audio')
        self.assertIn('YuE2', out['text'])

    def test_off_the_audio_tab_the_forms_engine_wins_instead(self):
        out = self._run('manual')
        self.assertNotIn('YuE2', out['text'])
        self.assertIn('ltx-2.5-mlx-q4', out['text'])


# ---------------------------------------------------------------- 2
class TestSongCardGatedOnTab(unittest.TestCase):
    def _fn(self) -> str:
        return extract_function("songCardRender", MUSIC_JS)

    def test_is_song_requires_the_audio_tab(self):
        fn = self._fn()
        i = fn.index("const isSong")
        line = fn[i:fn.index("\n", i)]
        self.assertIn("onAudioTab", line)
        self.assertIn("document.body.dataset.workflow === 'audio'", fn)

    def test_card_still_hides_for_a_non_song(self):
        fn = self._fn()
        self.assertIn("card.hidden = !isSong", fn)


# ---------------------------------------------------------------- 3
class TestPlayerPaneGatedOnTab(unittest.TestCase):
    def _fn(self) -> str:
        return extract_function("selectOutput", QUEUE_JS)

    def test_songhero_swap_requires_the_audio_tab(self):
        fn = self._fn()
        i = fn.index("if (isAudio && o && o.engine === 'music'")
        line = fn[i:fn.index("\n", i)]
        self.assertIn("onAudioTab", line)

    def test_off_tab_audio_gets_a_compact_player_and_an_open_link(self):
        fn = self._fn()
        self.assertIn("player-audio-inline", fn)
        self.assertIn("openSongInAudioTab", fn)
        # the open link is conditional on it actually being a song — a plain
        # (non-music) audio clip gets the inline player with no dead link
        i = fn.index("const openLink")
        block = fn[i:fn.index(";", i)]
        self.assertIn("o.engine === 'music'", block)

    def test_no_autoplay_regression_for_the_inline_player(self):
        fn = self._fn()
        i = fn.index("player-audio-inline")
        block = fn[fn.rindex("wrap.innerHTML", 0, i):fn.index("</div>", i)]
        self.assertIn("autoplay ? ' autoplay' : ''", block)


# ---------------------------------------------------------------- 4
class TestOpenSongInAudioTab(unittest.TestCase):
    def test_switches_tab_before_selecting(self):
        fn = extract_function("openSongInAudioTab", MUSIC_JS)
        self.assertIn("workflowSwitch('audio')", fn)
        self.assertIn("selectOutput(path)", fn)
        self.assertLess(fn.index("workflowSwitch"), fn.index("selectOutput(path)"))

    def test_published_on_globalthis(self):
        self.assertIn(
            "openSongInAudioTab",
            MUSIC_JS[MUSIC_JS.index("Object.assign(globalThis"):])

    def test_path_argument_is_safely_escaped_in_the_open_link(self):
        # Consistent with every other per-item onclick in this file: JSON
        # stringify + HTML-escape the quotes, never a raw string concat that
        # would break on an apostrophe in the filename.
        fn = extract_function("selectOutput", QUEUE_JS)
        i = fn.index("player-audio-open")
        block = fn[fn.rindex("const pathAttr", 0, i):i]
        self.assertIn("JSON.stringify(path)", block)
        self.assertIn("&quot;", block)


if __name__ == "__main__":
    unittest.main()
