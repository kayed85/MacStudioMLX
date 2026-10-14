#!/usr/bin/env python3
"""VC-41 + SYS-16: ONE first-run card, sized to this Mac (4.17 integration).

VC-41 (an empty-gallery card with a sample prompt and this Mac's ETA) and
SYS-16 ("Make your first clip" in the form, three starter prompts that queue
at Balanced) were two first-run cards for the same moment; VC-41's shared a
class with SYS-16's and pushed Generate off-screen, and it never showed on the
Video tab anyway (the Videos filter excluded it). Coordinator ruling: keep
SYS-16's card, give it VC-41's "sized to this Mac" price, remove VC-41's card.

Gated here (JS executed in node, not grepped, where it is behaviour):
  1  _firstRunEta() quotes the default cell — the fleet range first, then the
     modelled range, then the point estimate
  2  applyFirstRunCard() shows the card on a genuine first run, writes the
     price into it once, and hides it once the gallery has anything in it
  3  VC-41's separate gallery card and its helpers are gone
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
QUEUE_JS = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
HTML = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "webapp" / "style" / "panel.css").read_text(encoding="utf-8")


def _node(script: str):
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        r = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise AssertionError(r.stderr[-3000:])
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        Path(path).unlink(missing_ok=True)


FNS = extract_function("_firstRunEta", QUEUE_JS) + "\n" + extract_function("applyFirstRunCard", QUEUE_JS)


class TheEtaIsTheDefaultCells(unittest.TestCase):
    def _eta(self, tiers):
        return _node(FNS + f"""
global.BOOT = {{ ltx: {{ tiers: {json.dumps(tiers)} }} }};
console.log(JSON.stringify(_firstRunEta()));
""")

    def test_fleet_range_first(self):
        self.assertEqual(self._eta([{"key": "balanced_5s", "is_default": True, "eta": "~3 min",
                                     "eta_range": "~3–4 min",
                                     "fleet_range": {"eta_range": "~2–3 min"}}]), "~2–3 min")

    def test_then_the_modelled_range_then_the_point(self):
        self.assertEqual(self._eta([{"key": "balanced_5s", "is_default": True, "eta": "~3 min",
                                     "eta_range": "~3–4 min"}]), "~3–4 min")
        self.assertEqual(self._eta([{"key": "balanced_5s", "eta": "~3 min"}]), "~3 min")
        self.assertEqual(self._eta([]), "")


class TheCardShowsOnceAndOnlyOnAFirstRun(unittest.TestCase):
    def _run(self, status):
        return _node(FNS + f"""
const els = {{ firstRunCard: {{ style: {{ display: 'none' }} }}, firstRunEta: {{ textContent: '' }} }};
global.document = {{ getElementById: id => els[id] || null }};
global.BOOT = {{ first_run: true, ltx: {{ tiers: [{{ key: 'balanced_5s', is_default: true, eta: '~3 min' }}] }} }};
applyFirstRunCard({json.dumps(status)});
applyFirstRunCard({json.dumps(status)});
console.log(JSON.stringify({{ display: els.firstRunCard.style.display, eta: els.firstRunEta.textContent }}));
""")

    def test_a_fresh_install_sees_the_card_with_this_macs_price(self):
        out = self._run({"outputs": [], "history": [], "settings": {}})
        self.assertEqual(out["display"], "")
        self.assertEqual(out["eta"], " — about 3 min on this Mac")

    def test_a_gallery_with_anything_in_it_is_not_a_first_run(self):
        out = self._run({"outputs": [{"path": "/o/a.mp4"}], "history": [], "settings": {}})
        self.assertEqual(out["display"], "none")

    def test_dismissed_or_finished_hides_it(self):
        self.assertEqual(self._run({"outputs": [], "history": [],
                                    "settings": {"first_run_card_dismissed": True}})["display"], "none")
        self.assertEqual(self._run({"outputs": [], "history": [{"status": "done"}],
                                    "settings": {}})["display"], "none")


class OnlyOneFirstRunCardShips(unittest.TestCase):
    def test_vc41s_gallery_card_is_gone(self):
        for gone in ("firstRunCardHtml", "useFirstRunSample", "rerollFirstRunSample",
                     "FIRST_RUN_SAMPLE_PROMPTS", "gallery-first-run"):
            self.assertNotIn(gone, QUEUE_JS, gone)
            self.assertNotIn(gone, CSS, gone)
        self.assertEqual(HTML.count('id="firstRunCard"'), 1)
        self.assertIn('<span id="firstRunEta"></span>', HTML)


if __name__ == "__main__":
    unittest.main()
