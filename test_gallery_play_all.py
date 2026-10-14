#!/usr/bin/env python3
"""The gallery plays by itself, and gallery audio goes where audio is needed (4.18.0).

PLAY ALL. The expand lightbox had ← / → and nothing else. Now it has a bar —
previous, Play all, next, "n of N", how long a photo stays, full screen — and
the Outputs header has a Play all button: each clip plays once and hands over,
a photo stays 1-10 s, a song plays to its end; controls fade while it plays.

USE THIS AUDIO FOR. An audio output had one card chip ("Music video"). Now the
card's Use… chip, the player's button and the song menu all offer Lip-sync,
Music video, Soundtrack for a video and Cover it.

The pure parts (step order, the counter, the swipe classifier, the menu's
targets) and the routing (which form opens, what lands in which slot) run in
node against stubs; the markup and the shortcut row are read from the real
files. The real-browser half (screenshots at 1280 and 1440) is the release
validation's job.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

from extract_panel_js import extract_function                        # noqa: E402

NODE = shutil.which("node")
QUEUE = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
INDEX = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")


def _node(names: tuple, calls: list[str], prelude: str = "", src: str = QUEUE) -> list:
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
    fn = "\n".join(extract_function(n, src) for n in names)
    script = prelude + "\n" + fn + "\nconsole.log(JSON.stringify([\n  " + ",\n  ".join(calls) + "\n]));"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        r = subprocess.run([NODE, path], capture_output=True, text=True, errors="replace", timeout=60)
    finally:
        Path(path).unlink(missing_ok=True)
    if r.returncode != 0:
        raise AssertionError(r.stderr[-2000:])
    return json.loads(r.stdout.strip().splitlines()[-1])


class PlayAllOrder(unittest.TestCase):
    LIST = "[{path:'a'},{path:'b'},{path:'c'}]"

    def test_steps_wrap_both_ways(self):
        out = _node(("lbStepIndex",), [
            f"lbStepIndex({self.LIST}, 'a', 1)", f"lbStepIndex({self.LIST}, 'c', 1)",
            f"lbStepIndex({self.LIST}, 'a', -1)", f"lbStepIndex({self.LIST}, 'b', -1)",
            f"lbStepIndex({self.LIST}, 'gone', 1)", f"lbStepIndex({self.LIST}, 'gone', -1)",
            "lbStepIndex([], 'a', 1)", "lbStepIndex([{path:'a'}], 'a', 1)",
        ])
        self.assertEqual(out, [1, 0, 2, 0, 0, 2, -1, 0])

    def test_counter(self):
        out = _node(("lbCountLabel",), [
            f"lbCountLabel({self.LIST}, 'b')", f"lbCountLabel({self.LIST}, 'x')", "lbCountLabel([], 'a')",
        ])
        self.assertEqual(out, ["2 of 3", "", ""])

    def test_swipe(self):
        out = _node(("lbSwipeDir",), [
            "lbSwipeDir(-120, 10)",   # swipe left -> next
            "lbSwipeDir(120, -10)",   # swipe right -> previous
            "lbSwipeDir(5, -140)",    # swipe up -> next
            "lbSwipeDir(5, 140)",     # swipe down -> nothing
            "lbSwipeDir(20, 20)",     # a tap -> nothing
        ])
        self.assertEqual(out, [1, -1, 1, 0, 0])


class PlayAllRuns(unittest.TestCase):
    """lbStep / lbSetImageSec / _lbArm against a stub page."""

    PRELUDE = r"""
const calls = [];
const store = {};
globalThis.localStorage = { getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); } };
const els = {};
function el(id) { return els[id] || (els[id] = { id, hidden: false, style: { setProperty(){} },
  classList: { add(){}, remove(){}, toggle(){}, contains(){ return false; } },
  setAttribute(){}, querySelector(){ return null; }, matches(){ return false; }, get offsetWidth(){ return 1; } }); }
globalThis.document = { getElementById: el, querySelector: () => null, querySelectorAll: () => [],
  addEventListener(){}, fullscreenElement: null };
let activePath = 'a';
const outputs = [{path:'a', kind:'video'}, {path:'b', kind:'image'}, {path:'c', kind:'audio'}];
function filteredMainOutputs() { return outputs; }
function findOutputByPath(p) { return outputs.find(o => o.path === p) || null; }
function outputKind(o) { return o ? o.kind : 'video'; }
function selectOutput(p, opts) { calls.push(['select', p, opts]); activePath = p; }
function openExpandLightbox() { calls.push(['open', activePath]); }
function phosToast(m) { calls.push(['toast', m]); }
let lbOpen = true;
function lbIsOpen() { return lbOpen; }
function lbSyncBar() {}
function _lbWake() {}
const timers = [];
globalThis.setTimeout = (fn, ms) => { timers.push([fn, ms]); return timers.length; };
globalThis.clearTimeout = () => {};
const LB = { playing: false, timer: null, idleTimer: null, imageSec: 3, touch: null };
const LB_IMAGE_SEC_KEY = 'phos.lightbox.imageSec';
"""
    NAMES = ("lbStepIndex", "_lbList", "lbStep", "_lbClearTimer", "_lbArm", "lbSetImageSec", "lbPlayAll")

    def test_step_never_autoplays_the_stage_behind(self):
        out = _node(self.NAMES, ["(() => { lbStep(1); lbStep(-1); return calls; })()"], self.PRELUDE)
        self.assertEqual(out[0], [["select", "b", {"autoplay": False}], ["open", "b"],
                                  ["select", "a", {"autoplay": False}], ["open", "a"]])

    def test_a_photo_waits_the_chosen_time_then_moves_on(self):
        out = _node(self.NAMES, [
            "(() => { LB.playing = true; activePath = 'b'; lbSetImageSec('7');"
            " const [fn, ms] = timers[timers.length - 1]; fn();"
            " return [ms, LB.imageSec, store[LB_IMAGE_SEC_KEY], calls]; })()",
            "(() => { lbSetImageSec('99'); const hi = LB.imageSec; lbSetImageSec('0'); return [hi, LB.imageSec]; })()",
        ], self.PRELUDE)
        ms, sec, saved, calls = out[0]
        self.assertEqual((ms, sec, saved), (7000, 7, "7"))
        self.assertEqual(calls[-2:], [["select", "c", {"autoplay": False}], ["open", "c"]])
        self.assertEqual(out[1], [10, 1])

    def test_play_all_starts_from_the_first_when_nothing_is_selected(self):
        out = _node(self.NAMES, [
            "(() => { activePath = null; lbPlayAll(); return [LB.playing, calls]; })()",
        ], self.PRELUDE)
        self.assertTrue(out[0][0])
        self.assertEqual(out[0][1], [["select", "a", {"autoplay": False}], ["open", "a"]])

    def test_play_all_on_an_empty_gallery_says_so(self):
        out = _node(self.NAMES, [
            "(() => { outputs.length = 0; lbPlayAll(); return [LB.playing, calls]; })()",
        ], self.PRELUDE)
        self.assertFalse(out[0][0])
        self.assertEqual(out[0][1][0][0], "toast")


class OpeningSilencesEverythingBehind(unittest.TestCase):
    """Codex 4.18.0: opening the viewer paused only the stage player; a song
    playing in the Music Studio bar (or a card's own audio) kept playing
    under Play all."""

    def test_open_pauses_all_other_media(self):
        prelude = r"""
const media = [];
function M(id, inStage) { const m = { id, inStage, paused: false, pause() { this.paused = true; },
  addEventListener() {} }; media.push(m); return m; }
const barAudio = M('musicBarAudio', false), cardAudio = M('card', false), stageVid = M('stage', false);
let lbMedia = null;
const stage = { set innerHTML(v) { lbMedia = M('lightbox', true); }, contains(m) { return !!m.inStage; },
  querySelector() { return lbMedia; } };
const lb = { style: {}, classList: { toggle() {}, remove() {}, add() {} } };
globalThis.document = { getElementById: id => id === 'expandLightbox' ? lb : id === 'expandStage' ? stage : null,
  querySelectorAll: () => media.slice() };
let activePath = 'a';
function findOutputByPath() { return { path: 'a', url: '/f', name: 'a.mp4', size_mb: 1 }; }
function outputKind() { return 'video'; }
function escapeHtml(s) { return s; }
function _wireStageMutePersistence() {}
function _lbClearTimer() {}
function _lbArm() {}
function _lbWake() {}
function lbSyncBar() {}
const LB = { playing: true };
"""
        out = _node(("openExpandLightbox",), [
            "(() => { openExpandLightbox(); return media.map(m => [m.id, m.paused]); })()"], prelude)
        self.assertEqual(dict(out[0]), {"musicBarAudio": True, "card": True, "stage": True, "lightbox": False})


class PlayAllMarkup(unittest.TestCase):
    def test_bar_and_header_button(self):
        for needle in ('id="expandBar"', 'id="expandPlayBtn"', 'onclick="lbTogglePlayAll()"',
                       'id="expandCount"', 'id="expandImageSec"', 'id="expandFsBtn"',
                       'id="expandProgress"', 'id="outputsPlayAllBtn"', 'onclick="lbPlayAll()"'):
            self.assertIn(needle, INDEX, needle)
        opts = re.findall(r'<option value="(\d+)"', INDEX[INDEX.index('id="expandImageSec"'):][:900])
        self.assertEqual([int(o) for o in opts], list(range(1, 11)))

    def test_controls_fade_while_playing(self):
        css = (ROOT / "webapp" / "style" / "panel.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.expand-lightbox\.is-idle \.expand-bar[\s\S]{0,200}opacity: 0")

    def test_p_is_a_documented_shortcut(self):
        sc = (ROOT / "webapp" / "js" / "shortcuts.js").read_text(encoding="utf-8")
        self.assertIn("id: 'outputs.playall', scope: 'outputs', combos: ['p']", sc)
        self.assertIn("[[sc:outputs.playall]]", (ROOT / "webapp" / "docs" / "buttons.md").read_text())


class AudioUseTargets(unittest.TestCase):
    def test_four_targets_and_the_music_engine_state(self):
        out = _node(("audioUseTargets",), [
            "audioUseTargets({}, {music: {available: true, capable: true}})",
            "audioUseTargets({}, {music: {available: false, capable: true}})",
            "audioUseTargets({}, {music: {capable: false}})",
            "audioUseTargets({}, {})",
        ])
        self.assertEqual([t["id"] for t in out[0]], ["lipsync", "musicvideo", "soundtrack", "cover"])
        self.assertFalse(any(t.get("disabled") for t in out[0]))
        self.assertIn("install card", out[1][3]["sub"])
        self.assertFalse(out[1][3].get("disabled"))
        self.assertTrue(out[2][3]["disabled"])
        self.assertIn("more memory", out[2][3]["why"])
        self.assertFalse(out[3][3].get("disabled"))


class AudioUseRouting(unittest.TestCase):
    PRELUDE = r"""
const calls = [];
const els = { musicCoverDetails: { open: false }, customizeDetails: { open: false },
  i2vMode: { value: 'i2v', dispatchEvent(e) { calls.push(['change', this.value]); } },
  i2vAudioModeSection: { scrollIntoView() {} } };
globalThis.document = { getElementById: id => els[id] || null, removeEventListener(){} };
globalThis.requestAnimationFrame = fn => fn();
globalThis.Event = class { constructor(t) { this.type = t; } };
globalThis.window = { _ENGINE_PROBES: { music: { available: true, capable: true } } };
function findOutputByPath(p) { return { path: p, clip_sec: 12.5, url: '/file?path=' + p }; }
function closeAudioUseMenu() { calls.push(['close']); }
function phosToast(m) { calls.push(['toast', m]); }
function audioStudioUseAudio(p, d, u) { calls.push(['lipsync', p, d, u]); }
function useTrackInA2V(p) { calls.push(['musicvideo', p]); }
function workflowSwitch(t) { calls.push(['tab', t]); }
function musicCoverFromSong(s) { calls.push(['cover', s.path]); }
function openMusicInstallCard() { calls.push(['install-card']); }
let engine = 'h3';
function currentEngine() { return engine; }
function setEngine(e) { calls.push(['engine', e]); engine = e; }
function setMode(m) { calls.push(['mode', m]); }
function i2vAudioSet(p) { calls.push(['i2v-audio', p]); }
"""
    NAMES = ("audioUseApply", "useAudioAsSoundtrack")

    def run_target(self, target, probes=None):
        pre = self.PRELUDE
        if probes is not None:
            pre += f"\nwindow._ENGINE_PROBES = {json.dumps(probes)};"
        return _node(self.NAMES, [f"(() => {{ audioUseApply('/o/song.wav', '{target}'); return [calls, els]; }})()"], pre)[0]

    def test_lipsync_fills_the_slot_with_the_known_length(self):
        calls, _ = self.run_target("lipsync")
        self.assertIn(["lipsync", "/o/song.wav", 12.5, "/file?path=/o/song.wav"], calls)

    def test_music_video(self):
        calls, _ = self.run_target("musicvideo")
        self.assertIn(["musicvideo", "/o/song.wav"], calls)

    def test_soundtrack_is_ltx_i2v_with_external_audio(self):
        calls, els = self.run_target("soundtrack")
        self.assertEqual([c for c in calls if c[0] in ("tab", "engine", "mode", "change", "i2v-audio")],
                         [["tab", "manual"], ["engine", "ltx"], ["mode", "i2v"],
                          ["change", "i2v_clean_audio"], ["i2v-audio", "/o/song.wav"]])
        self.assertTrue(els["customizeDetails"]["open"])
        self.assertIn("Switched to LTX", [c for c in calls if c[0] == "toast"][0][1])

    def test_cover_loads_the_source_song(self):
        calls, els = self.run_target("cover")
        self.assertIn(["tab", "audio"], calls)
        self.assertIn(["cover", "/o/song.wav"], calls)
        self.assertTrue(els["musicCoverDetails"]["open"])

    def test_cover_without_the_engine_opens_its_install_card(self):
        calls, _ = self.run_target("cover", {"music": {"available": False, "capable": True}})
        self.assertIn(["install-card"], calls)
        self.assertNotIn(["tab", "audio"], calls)

    def test_cover_on_a_mac_that_cannot_run_it_does_nothing(self):
        calls, _ = self.run_target("cover", {"music": {"capable": False}})
        self.assertEqual(calls, [["close"]])


class AudioUseMarkup(unittest.TestCase):
    def test_every_entry_point(self):
        self.assertIn('id="audioUseMenu"', INDEX)
        self.assertIn("openAudioUseMenu(event, ${pathAttr})", QUEUE)          # card chip + player button
        self.assertEqual(QUEUE.count("openAudioUseMenu(event, ${pathAttr})"), 2)
        music = (ROOT / "webapp" / "js" / "music.js").read_text(encoding="utf-8")
        for t in ("lipsync", "musicvideo", "soundtrack"):
            self.assertIn(f"audioUseApply(${{attr}}, '{t}')", music)

    def test_the_menu_is_keyboard_reachable(self):
        body = extract_function("openAudioUseMenu", QUEUE)
        self.assertIn('role="menuitem"', body)
        self.assertIn(".focus(", body)
        self.assertIn("e.key === 'Escape'", QUEUE[QUEUE.index("function closeAudioUseMenu"):][:1200])


class ToastsShowTheirWords(unittest.TestCase):
    """4.17.0-4.17.5: the global `button { width: 100% }` made a toast's ×
    as wide as the toast; with flex-shrink 0 it squeezed the message to 0 px,
    so every toast was an icon and a × with no words (measured in Chrome:
    message 0 px, × 230 px). The real-browser half of this lives in the
    release validation (it measures the message width); this pins the rule."""

    def test_close_button_sizes_to_its_glyph(self):
        css = (ROOT / "webapp" / "style" / "panel.css").read_text(encoding="utf-8")
        block = css[css.index(".phos-toast .phos-toast-close {"):]
        block = block[:block.index("\n    }")]
        self.assertIn("width: auto;", block)
        self.assertIn("flex-shrink: 0", block)

    def test_the_bar_controls_size_to_content(self):
        css = (ROOT / "webapp" / "style" / "panel.css").read_text(encoding="utf-8")
        for sel in (".expand-bar-btn {", ".expand-dur select {"):
            block = css[css.index(sel):]
            self.assertIn("width: auto;", block[:block.index("\n    }")], sel)


if __name__ == "__main__":
    unittest.main()
