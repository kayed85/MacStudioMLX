#!/usr/bin/env python3.11
"""A REAL browser geometry gate for the Video tab's player + Outputs pane.

WHY THIS EXISTS. A Pinokio user on a 1080p monitor (@fuschichou, 2026-09-25):
"video generations that are square or more widescreen push down the outputs
pane so far that it goes out of view" - the only way back to the thumbnails
(and their delete buttons) was to make Pinokio's window narrower. The player
was sized from the column's WIDTH (`width:100%` + `aspect-ratio`), so a square
clip in a 1300px column asked for a 1300px-tall player on a ~950px screen, and
even a 16:9 clip left the gallery a sliver. Nothing in the suite could see it:
the rule that breaks and the rule that fixes are both just strings in the
stylesheet. Only a browser that has laid the page out can tell.

So this boots the panel out of THIS tree against a throwaway state directory
with four stills in its outputs folder (16:9, 1:1, 9:16 and 21:9 - written by
hand as PNGs so the gate needs no ffmpeg and no codec), drives a headless
Chrome over CDP (the websocket client and process helpers are the Editor
gate's, `scripts/measure_editor_layout.py`, loaded by path so both stay
stdlib-only), selects each still through the app's own `selectOutput`, and
reads, at several real screen sizes:

  outputs_visible   the Outputs header (filters, search) and one whole row of
                    cards - thumbnail, name and the delete button - sit inside
                    the viewport and inside the stage column
  player_fits       the player ends above the Outputs pane (no overlap)
  aspect_kept       the player keeps the clip's shape (it is scaled, never
                    stretched, and never letterboxed into a wrong-shape box)
  fills_on_large    on a big screen a 16:9 clip still spans the whole column
                    (the fix must not shrink the player where there was room)

Exit codes: 0 pass, 1 a measurement FAILED, 3 no browser (the unittest skips),
4 the harness could not get far enough to measure - never reported as a pass.

    ./ltx-2-mlx/env/bin/python3.11 scripts/measure_outputs_layout.py -v
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import struct
import sys
import tempfile
import time
import wave
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The owner's live panel. Nothing here may ever bind it (the Editor gate's
# free_port refuses the same set).
FORBIDDEN_PORTS = {8199}

# (width, height) of the page itself - i.e. the browser viewport, which inside
# Pinokio is the screen minus the OS menu bar / taskbar and Pinokio's own
# title + tab strip. 1920x940 is a maximised Pinokio on a 1080p monitor, the
# reporter's screen; 1366x650 the same on a 768p laptop.
VIEWPORTS = (
    (1920, 1080),
    (1920, 940),
    (1680, 950),
    (1440, 820),
    (1366, 650),
    (1280, 720),
    (2560, 1400),
)
LARGE = (2560, 1400)

# name -> (w, h) of the still written into outputs/
MEDIA = {
    "wide_16x9": (1280, 720),
    "square_1x1": (768, 768),
    "tall_9x16": (720, 1280),
    "ultra_21x9": (1344, 576),
}

TOL = 1.0            # px
ASPECT_TOL = 0.02    # 2 %


def _load_editor_gate():
    """The Editor gate's harness (CDP client, panel/Chrome boot, kill).

    Loaded by file path, not imported as a package: both scripts must run on
    a bare standard library, and `test_script_is_stdlib_only` holds each of
    them to that.
    """
    p = ROOT / "scripts" / "measure_editor_layout.py"
    spec = importlib.util.spec_from_file_location("_phos_editor_gate", p)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {p}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = _load_editor_gate()
HarnessError = G.HarnessError
NoBrowser = G.NoBrowser


def write_png(path: Path, w: int, h: int, rgb: tuple[int, int, int]) -> None:
    """A flat-colour RGB PNG, standard library only."""
    row = b"\x00" + bytes(rgb) * w
    raw = row * h

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 6))
           + chunk(b"IEND", b""))
    path.write_bytes(png)


def seed_outputs(out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    colours = [(40, 70, 140), (140, 60, 90), (50, 120, 80), (120, 110, 40)]
    paths = {}
    now = time.time()
    for i, (name, (w, h)) in enumerate(MEDIA.items()):
        p = out_dir / f"geom_{name}.png"
        write_png(p, w, h, colours[i % len(colours)])
        os.utime(p, (now - i, now - i))
        paths[name] = str(p)
    # A SONG: a Music Studio output shows a hero (cover, title, Play), not a
    # picture, and has no shape of its own - the one case a shape-driven
    # player can squeeze until its own button is clipped (Codex, 4.16.2).
    # One second of silence plus the sidecar that makes it a song.
    song = out_dir / "geom_song.wav"
    with wave.open(str(song), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 8000)
    # With a score, so the Song card draws sheet music and is TALL - the card
    # is what pushed the list of songs off a 1080p screen.
    abc = ("X:1\nT:Gate song\nM:4/4\nL:1/8\nQ:1/4=92\nK:D\n"
           + "V:1 name=\"Vocal\"\n" + "|DEFG ABcd|efga bagf|" * 6 + "\n"
           + "V:2 name=\"Ins\"\n" + "|D2F2 A2d2|e2g2 b2a2|" * 6 + "\n")
    (out_dir / "geom_song.wav.json").write_text(json.dumps(
        {"engine": "music", "title": "Gate song", "style": "a test tone",
         "mode": "full", "score_abc": abc}), encoding="utf-8")
    os.utime(song, (now - 50, now - 50))
    paths["song"] = str(song)
    # Enough extra cards that the gallery has more than one row to scroll.
    for j in range(8):
        p = out_dir / f"geom_extra_{j}.png"
        write_png(p, 640, 360, (30 + j * 12, 40, 60))
        os.utime(p, (now - 100 - j, now - 100 - j))
    return paths


# The Video tab opens its gallery on the Videos filter; the gate's media are
# stills (no codec, no ffmpeg), so it looks at All - the same cards, the same
# player, the same `--media-aspect` path a clip takes.
JS_READY = """
(() => {
  if (typeof selectOutput !== 'function') return 'selectOutput missing';
  if (typeof setMainOutputsFilter === 'function' && mainOutputsFilter !== 'all')
    setMainOutputsFilter('all');
  const cards = document.querySelectorAll('#carousel .car-card');
  if (cards.length < 4) return 'only ' + cards.length + ' cards in the gallery';
  return 'ok';
})()
"""

# Select one still through the app's own door and wait for the surface to take
# its shape, then read everything off the laid-out DOM.
JS_MEASURE = r"""
(async () => {
  const path = %(path)s;
  const want = %(ratio)s;
  const raf = () => new Promise(r => requestAnimationFrame(() => r()));
  const settle = async () => { await raf(); await raf(); await raf(); };
  const num = v => Math.round(v * 100) / 100;
  const box = e => { if (!e) return null; const r = e.getBoundingClientRect();
    return { x: num(r.left), y: num(r.top), w: num(r.width), h: num(r.height),
             b: num(r.bottom), r: num(r.right) }; };
  const out = { missing: [] };
  // This gate measures the VIDEO tab's gallery. The previous viewport's song
  // step (JS_SONG, VC-20) left the page on the Audio tab, whose gallery is
  // songs only — go back, and back to All, before measuring.
  if (typeof workflowSwitch === 'function' && document.body.dataset.workflow !== 'manual')
    workflowSwitch('manual');
  if (typeof setMainOutputsFilter === 'function' && mainOutputsFilter !== 'all')
    setMainOutputsFilter('all');
  const surface = document.querySelector('.stage-pane > .player-surface');
  if (!surface) { out.missing.push('.player-surface'); return out; }
  const shaped = () => {
    const asp = surface.style.getPropertyValue('--media-aspect').trim();
    if (!asp) return false;
    const [a, b] = asp.split('/').map(s => parseFloat(s));
    return !!(a && b && Math.abs(a / b - want) / want < 0.01);
  };
  // The page's own boot selection can land after ours; re-select until the
  // surface carries THIS still's shape (a stale shape would be measured as a
  // pass or a failure that has nothing to do with the layout).
  const t0 = performance.now();
  let tries = 0;
  while (performance.now() - t0 < 10000) {
    if (tries++ %% 20 === 0) selectOutput(path);
    await settle();
    if (shaped()) break;
  }
  out.shaped = shaped();
  // two more frames for fitStagePlayer's rAF, then settle the layout
  await settle(); await settle();
  const stage = document.querySelector('main.layout > .stage-pane')
             || document.querySelector('.stage-pane');
  const wrap = document.querySelector('.stage-pane > .carousel-wrap');
  const head = wrap && wrap.querySelector('.carousel-head');
  const car = document.getElementById('carousel');
  const card = car && car.querySelector('.car-card');
  const del = card && card.querySelector('.card-action-danger');
  const name = card && card.querySelector('.info');
  const media = surface.querySelector('.player-wrap img, .player-wrap video');
  if (!stage) out.missing.push('.stage-pane');
  if (!wrap) out.missing.push('.carousel-wrap');
  if (!head) out.missing.push('.carousel-head');
  if (!card) out.missing.push('.car-card');
  if (!del) out.missing.push('.card-action-danger');
  if (!media) out.missing.push('player media');
  out.aspect_prop = surface.style.getPropertyValue('--media-aspect').trim();
  out.vw = innerWidth; out.vh = innerHeight;
  out.stage = box(stage); out.surface = box(surface); out.wrap = box(wrap);
  out.head = box(head); out.carousel = box(car); out.card = box(card);
  out.del = box(del); out.name = box(name);
  const acts = document.getElementById('playerOverlayActions');
  out.actions = box(acts);
  out.fit = surface.getAttribute('data-fit');
  out.stage_inner_w = stage ? num(stage.clientWidth
      - parseFloat(getComputedStyle(stage).paddingLeft)
      - parseFloat(getComputedStyle(stage).paddingRight)) : null;
  out.stacked = !!(stage && getComputedStyle(document.querySelector('main.layout'))
      .gridTemplateColumns.trim().split(/\s+/).length < 2);
  return out;
})()
"""


JS_SONG = r"""
(async () => {
  const raf = () => new Promise(r => requestAnimationFrame(() => r()));
  const settle = async () => { for (let i = 0; i < 4; i++) await raf(); };
  const num = v => Math.round(v * 100) / 100;
  const box = e => { if (!e) return null; const r = e.getBoundingClientRect();
    return { x: num(r.left), y: num(r.top), w: num(r.width), h: num(r.height),
             b: num(r.bottom), r: num(r.right) }; };
  const surface = document.querySelector('.stage-pane > .player-surface');
  // VC-20: the song hero (this whole scenario) is the Audio/Music tab's
  // own surface now — selectOutput() only builds it when
  // document.body.dataset.workflow === 'audio' (reached by filtering the
  // VIDEO tab's own gallery to Audio and clicking a song used to
  // transplant this same hero into the Video tab's pane, which was the
  // bug). Switch tabs first, the same way a real user (or the new "Open
  // in Audio tab" link) would, before selecting the song.
  if (typeof workflowSwitch === 'function') workflowSwitch('audio');
  const t0 = performance.now();
  let tries = 0;
  while (performance.now() - t0 < 10000) {
    if (tries++ %% 20 === 0) selectOutput(%(path)s);
    await settle();
    if (surface.querySelector('.song-hero-play')) break;
  }
  await settle(); await settle();
  // the card fetches the score; give it a moment to draw
  const t1 = performance.now();
  while (performance.now() - t1 < 4000) {
    await settle();
    if (document.querySelector('#songCard svg')) break;
  }
  await settle(); await settle();
  const play = surface.querySelector('.song-hero-play');
  const cover = surface.querySelector('.song-hero-cover');
  const wrap = document.querySelector('.stage-pane > .carousel-wrap');
  const head = wrap && wrap.querySelector('.carousel-head');
  const first = document.getElementById('carousel').firstElementChild;
  const stage = document.querySelector('.stage-pane');
  return { vh: innerHeight, surface: box(surface), play: box(play),
           cover: box(cover), song: surface.hasAttribute('data-song'),
           card: box(document.getElementById('songCard')),
           card_has_score: !!document.querySelector('#songCard svg'),
           head: box(head), first: box(first), stage: box(stage) };
})()
"""


def check_song(tag: str, m: dict) -> list[str]:
    f: list[str] = []
    s, p, c = m.get("surface"), m.get("play"), m.get("cover")
    if not (s and p and c) or not m.get("song"):
        return [f"{tag}: the song hero never came up ({m})"]
    for label, b in (("Play button", p), ("cover", c)):
        if b["y"] < s["y"] - TOL or b["b"] > s["b"] + TOL:
            f.append(f"{tag}: the song hero's {label} ({b['y']}-{b['b']}) is "
                     f"clipped by the player ({s['y']}-{s['b']})")
    if p["b"] > m["vh"] + TOL:
        f.append(f"{tag}: the song's Play button is below the viewport")
    # The list of songs stays reachable on the screens people have (the
    # smallest two viewports cannot hold a hero, a card and a list at once;
    # there the card and list scroll).
    if m["vh"] >= 820:
        floor = min(m["vh"], m["stage"]["b"])
        for label, b in (("Outputs header", m.get("head")),
                         ("first output", m.get("first"))):
            if not b or b["h"] <= 0:
                f.append(f"{tag}: {label} not drawn")
            elif b["b"] > floor + TOL:
                f.append(f"{tag}: with a song selected the {label} ends at "
                         f"{b['b']}, below the visible floor {floor}")
    return f


def check(tag: str, want: float, m: dict, large: bool, name: str) -> list[str]:
    f: list[str] = []
    if m.get("missing"):
        return [f"{tag}: could not find {m['missing']}"]
    if not m.get("shaped"):
        return [f"{tag}: the player never took the still's shape "
                f"(--media-aspect {m.get('aspect_prop')!r})"]
    vh = m["vh"]
    stage, surf, wrap, head = m["stage"], m["surface"], m["wrap"], m["head"]
    card, dele = m["card"], m["del"]
    floor = min(vh, stage["b"])
    # 1. the Outputs header and a whole first row of cards are on screen
    for label, b in (("outputs header", head), ("first card", card),
                     ("its delete button", dele)):
        if b["h"] <= 0:
            f.append(f"{tag}: {label} has no height")
        elif b["b"] > floor + TOL:
            f.append(f"{tag}: {label} ends at {b['b']} - below the visible "
                     f"floor {floor} (viewport {vh})")
    # 2. the player ends above the Outputs pane
    if surf["b"] > wrap["y"] + TOL:
        f.append(f"{tag}: the player (bottom {surf['b']}) runs into the "
                 f"Outputs pane (top {wrap['y']})")
    if surf["r"] > stage["r"] + TOL or surf["x"] < stage["x"] - TOL:
        f.append(f"{tag}: the player spills sideways out of the stage column")
    # 3. the player keeps the clip's shape
    got = surf["w"] / surf["h"] if surf["h"] else 0
    if not got or abs(got - want) / want > ASPECT_TOL:
        f.append(f"{tag}: player is {surf['w']}x{surf['h']} "
                 f"(ratio {got:.3f}), the clip is {want:.3f}")
    # 3b. a cluster moved beside the picture stays inside the page
    a = m.get("actions")
    if m.get("fit") == "narrow" and a and a["w"] > 0 and a["r"] > m["vw"] + TOL:
        f.append(f"{tag}: the action buttons beside the player run off the "
                 f"right edge ({a['r']} > {m['vw']})")
    # 4. no shrink where there is room
    if large and name == "wide_16x9":
        if surf["w"] < m["stage_inner_w"] - 2:
            f.append(f"{tag}: on a large screen the 16:9 player is "
                     f"{surf['w']}px in a {m['stage_inner_w']}px column - "
                     f"it should fill it")
    return f


def run(verbose: bool) -> tuple[dict, int]:
    binary = G.find_browser()                      # raises NoBrowser -> exit 3
    work = Path(tempfile.mkdtemp(prefix="phos-outputs-geom-"))
    profile = work / "chrome"
    profile.mkdir()
    log = work / "panel.log"
    panel = chrome = ws = None
    try:
        media = seed_outputs(work / "outputs")
        panel, base = G.start_panel(work, log, verbose)
        if base.rsplit(":", 1)[-1] in {str(p) for p in FORBIDDEN_PORTS}:
            raise HarnessError("refusing to measure on a forbidden port")
        chrome, devtools = G.start_chrome(binary, profile, verbose)
        ws = G.WebSocket(G.page_socket(devtools))
        cdp = G.CDP(ws)
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        w0, h0 = VIEWPORTS[0]
        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": w0, "height": h0, "deviceScaleFactor": 1,
                  "mobile": False})
        cdp.call("Page.navigate", {"url": base + "/"})
        deadline = time.monotonic() + 45.0
        while cdp.evaluate("document.readyState") != "complete":
            if time.monotonic() > deadline:
                raise HarnessError("the panel page never finished loading")
            time.sleep(0.2)
        deadline = time.monotonic() + 30.0
        why = "never checked"
        while time.monotonic() < deadline:
            why = cdp.evaluate(JS_READY)
            if why == "ok":
                break
            time.sleep(0.3)
        if why != "ok":
            raise HarnessError(f"the gallery never filled: {why}")

        results: dict[str, dict] = {}
        failures: list[str] = []
        for vw, vh in VIEWPORTS:
            cdp.call("Emulation.setDeviceMetricsOverride",
                     {"width": vw, "height": vh, "deviceScaleFactor": 1,
                      "mobile": False})
            for name, (mw, mh) in MEDIA.items():
                tag = f"{vw}x{vh}/{name}"
                want = mw / mh
                m = cdp.evaluate(JS_MEASURE % {"path": json.dumps(media[name]),
                                               "ratio": repr(want)},
                                 await_promise=True, timeout=30.0)
                if not isinstance(m, dict):
                    raise HarnessError(f"{tag}: the page returned {m!r}")
                results[tag] = m
                if m.get("stacked"):
                    continue          # the <=900px one-column layout scrolls
                fl = check(tag, want, m, (vw, vh) == LARGE, name)
                failures.extend(fl)
                if verbose:
                    s = m.get("surface") or {}
                    c = m.get("card") or {}
                    print(f"{tag:28s} player {s.get('w')}x{s.get('h')} "
                          f"card.bottom {c.get('b')} vh {vh} "
                          f"{'FAIL' if fl else 'ok'}", file=sys.stderr)
            tag = f"{vw}x{vh}/song"
            m = cdp.evaluate(JS_SONG % {"path": json.dumps(media["song"])},
                             await_promise=True, timeout=30.0)
            if not isinstance(m, dict):
                raise HarnessError(f"{tag}: the page returned {m!r}")
            results[tag] = m
            fl = check_song(tag, m)
            failures.extend(fl)
            if verbose:
                s_ = m.get("surface") or {}
                print(f"{tag:28s} player {s_.get('w')}x{s_.get('h')} "
                      f"{'FAIL' if fl else 'ok'}", file=sys.stderr)
        return ({"ok": not failures, "results": results,
                 "failures": failures}, 1 if failures else 0)
    finally:
        if ws is not None:
            ws.close()
        G.kill(chrome)
        G.kill(panel)
        shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[0])
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="progress on stderr; stdout stays one JSON document")
    args = ap.parse_args(argv)
    try:
        doc, code = run(args.verbose)
    except NoBrowser as exc:
        print(f"no browser: {exc}", file=sys.stderr)
        return 3
    except HarnessError as exc:
        print(json.dumps({"ok": False, "results": {},
                          "failures": [f"harness: {exc}"]}))
        print(f"harness: {exc}", file=sys.stderr)
        return 4
    print(json.dumps(doc, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
