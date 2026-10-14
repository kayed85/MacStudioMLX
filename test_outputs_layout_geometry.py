"""The Video tab's player and Outputs pane, measured in a real browser.

WHAT THIS GUARDS. A Pinokio user on a 1080p monitor (2026-09-25): "video
generations that are square or more widescreen push down the outputs pane so
far that it goes out of view" - the gallery and its delete buttons could only
be reached by making Pinokio's window narrower. The player took the column's
full width and its height followed the clip's shape, so a square clip in a
~1000 px column wanted a ~1000 px player on a ~940 px page. On 4.16.1 this
gate fails at every one of its seven screen sizes for a square clip and at
1366x650 / 1280x720 for a 16:9 one.

`scripts/measure_outputs_layout.py` boots a panel from this tree against a
throwaway state directory with four stills (16:9, 1:1, 9:16, 21:9), drives a
headless Chrome over CDP with the Editor gate's standard-library websocket
client, selects each still through `selectOutput`, and reads the laid-out DOM
at seven viewports. This file runs it and asserts on the result.

NO NEW DEPENDENCY, same rule as test_editor_layout_geometry.py: the script is
standard library only, and on a machine with no browser the measurement SKIPS
while the shape tests still run.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "scripts" / "measure_outputs_layout.py"
VENV_PY = ROOT / "ltx-2-mlx" / "env" / "bin" / "python3.11"
NO_BROWSER_EXIT = 3


def _python() -> str:
    return str(VENV_PY) if VENV_PY.exists() else sys.executable


def _browser_present() -> bool:
    import os
    import shutil

    env = os.environ.get("CHROME_PATH", "").strip()
    if env and Path(env).exists():
        return True
    for c in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Chromium.app/Contents/MacOS/Chromium",
              "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"):
        if Path(c).exists():
            return True
    return any(shutil.which(n) for n in
               ("google-chrome", "chromium", "chromium-browser"))


class ScriptShape(unittest.TestCase):
    def test_script_parses(self):
        compile(SCRIPT.read_text(encoding="utf-8"), str(SCRIPT), "exec")

    def test_script_is_stdlib_only(self):
        """A pip dependency here would ride into every Pinokio install."""
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"), str(SCRIPT))
        allowed = set(sys.stdlib_module_names) | {"__future__"}
        seen: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                seen.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                self.assertFalse(node.level, "relative import")
                if node.module:
                    seen.add(node.module.split(".")[0])
        self.assertEqual(sorted(seen - allowed), [])

    def test_never_binds_the_owners_port(self):
        src = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("FORBIDDEN_PORTS", src)
        for line in src.splitlines():
            if "8199" in line:
                self.assertTrue(re.search(r"FORBIDDEN_PORTS\s*=", line),
                                f"8199 outside the forbidden set: {line}")

    def test_measures_the_reporters_screen(self):
        """1920 wide with Pinokio's chrome taken off 1080 is the report."""
        src = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("(1920, 940)", src)
        self.assertIn("(1366, 650)", src)
        self.assertIn('"square_1x1"', src)


class PlayerWiring(unittest.TestCase):
    """The pieces the browser measurement depends on, read from the source so
    a machine with no Chrome still notices one going missing."""

    def test_css_width_follows_the_fitted_height(self):
        css = (ROOT / "webapp" / "style" / "panel.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.player-surface \{[^}]*width: min\(100%, calc\("
                              r"var\(--player-max-h")
        self.assertIn("var(--media-ar", css)

    def test_every_shape_write_goes_through_the_helper(self):
        """A write that sets --media-aspect without --media-ar would size a
        square clip as 16:9 wide - the helper is the one door."""
        for name in ("queue.js", "preview.js", "stage.js", "editor.js",
                     "storyboard.js", "music.js"):
            src = (ROOT / "webapp" / "js" / name).read_text(encoding="utf-8")
            code = "\n".join(l for l in src.splitlines()
                             if not l.lstrip().startswith("//"))
            hits = code.count("setProperty('--media-aspect'")
            if name == "queue.js":
                self.assertEqual(hits, 1, "only setStageAspect may write it")
            else:
                self.assertEqual(hits, 0, f"{name} writes --media-aspect "
                                          f"directly - use setStageAspect")

    def test_a_late_load_cannot_restamp_the_shape(self):
        src = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
        i = src.index("const apply = () => {")
        self.assertIn("if (!media.isConnected) return;", src[i:i + 600])

    def test_fitter_is_started_once_at_boot(self):
        main = (ROOT / "webapp" / "js" / "main.js").read_text(encoding="utf-8")
        self.assertEqual(main.count("initStagePlayerFit();"), 1)


class OutputsLayoutGeometry(unittest.TestCase):
    doc: dict | None = None
    skip_why: str | None = None
    fail_why: str | None = None
    returncode: int | None = None

    @classmethod
    def setUpClass(cls):
        if not _browser_present():
            cls.skip_why = "no Chrome/Chromium/Edge - set CHROME_PATH"
            return
        try:
            proc = subprocess.run([_python(), str(SCRIPT)], cwd=str(ROOT),
                                  capture_output=True, text=True,
                                  errors="replace", timeout=400)
        except subprocess.TimeoutExpired:
            cls.fail_why = "the outputs geometry gate did not finish in 400s"
            return
        if proc.returncode == NO_BROWSER_EXIT:
            cls.skip_why = "no browser: " + (proc.stderr or "").strip()
            return
        try:
            cls.doc = json.loads(proc.stdout)
        except json.JSONDecodeError:
            cls.fail_why = (f"no JSON (exit {proc.returncode}): "
                            f"{(proc.stderr or '').strip()[-800:]}")
            return
        cls.returncode = proc.returncode

    def setUp(self):
        if self.skip_why:
            self.skipTest(self.skip_why)
        if self.doc is None:
            self.fail(self.fail_why or "no measurement")

    def test_gate_reports_no_failures(self):
        self.assertEqual(self.doc.get("failures") or [], [],
                         "the player/Outputs layout is wrong:\n  "
                         + "\n  ".join(self.doc.get("failures") or []))
        self.assertTrue(self.doc.get("ok"))
        self.assertEqual(self.returncode, 0)

    def test_every_case_was_measured_side_by_side(self):
        """28 picture cases, none in the stacked layout (which scrolls on
        purpose), plus the song hero at every viewport."""
        res = self.doc.get("results") or {}
        songs = {t: m for t, m in res.items() if t.endswith("/song")}
        pics = {t: m for t, m in res.items() if not t.endswith("/song")}
        self.assertEqual(len(pics), 28, sorted(pics))
        self.assertEqual(len(songs), 7, sorted(songs))
        for tag, m in songs.items():
            with self.subTest(case=tag):
                self.assertTrue(m.get("song"), f"{tag}: no song hero")
                self.assertGreaterEqual(m["surface"]["h"], 244,
                                        f"{tag}: hero squeezed below its cover")
        for tag, m in pics.items():
            with self.subTest(case=tag):
                self.assertFalse(m.get("stacked"), f"{tag} measured stacked")
                self.assertTrue(m.get("shaped"), f"{tag} never took its shape")

    def test_square_on_the_reporters_screen(self):
        """The report itself: 1920x940, square clip, delete button reachable."""
        m = (self.doc.get("results") or {}).get("1920x940/square_1x1")
        self.assertIsNotNone(m)
        self.assertLessEqual(m["del"]["b"], m["vh"])
        self.assertLessEqual(m["surface"]["b"], m["wrap"]["y"] + 1)


if __name__ == "__main__":
    unittest.main()
