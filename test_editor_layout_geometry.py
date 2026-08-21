"""The Editor's layout, measured in a real browser.

WHAT THIS GUARDS. Three defects shipped to the owner's screen inside one
month, and the suite was green through all three: Save resolved to 1554px wide
because the global `button { width: 100% }` rule was never cancelled for
`.primary`, the header wrapped to two rows around it, and the timeline floor
was shorter than the lanes standing in it so the soundtrack was clipped off the
bottom. Not one of those is visible to a test that reads markup or stylesheet
text - the rule that fixes them and the rule that does not are both just
strings in the file. They are only visible to a browser that has laid the page
out.

So `scripts/measure_editor_layout.py` boots a panel from this tree against a
throwaway state directory, drives a headless Chrome over CDP with a websocket
client written in the standard library, opens the Editor, and reads five
numbers off the live DOM at four widths. This file runs it and asserts on
them, one subtest per width, so a failure names the width and the measurement.

NO NEW DEPENDENCY. The script imports nothing outside the standard library -
`test_script_is_stdlib_only` is the guard on that - so nothing here reaches a
Pinokio install. On a machine with no browser the script exits 3 and the
browser test SKIPS; the stdlib and syntax tests still run, so the file is never
completely unguarded.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "scripts" / "measure_editor_layout.py"

# The panel's own interpreter, which is the one this has to run under: it has
# no selenium, no playwright and no `websockets`, and that is the point.
VENV_PY = ROOT / "ltx-2-mlx" / "env" / "bin" / "python3.11"

NO_BROWSER_EXIT = 3
WIDTHS = ("1920", "1520", "1280", "1100")

SAVE_MIN, SAVE_MAX = 40.0, 200.0
OVERFLOW_TOL = 1.0


def _python() -> str:
    return str(VENV_PY) if VENV_PY.exists() else sys.executable


def _browser_present() -> bool:
    """Same search the script does, without importing it."""
    import os
    import shutil

    env = os.environ.get("CHROME_PATH", "").strip()
    if env and Path(env).exists():
        return True
    candidates = (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    )
    if any(Path(c).exists() for c in candidates):
        return True
    return any(shutil.which(n) for n in
               ("google-chrome", "chromium", "chromium-browser"))


# =============================================================================
# 1. The file itself - runs everywhere, browser or not
# =============================================================================
class ScriptShape(unittest.TestCase):
    def test_script_exists(self):
        self.assertTrue(SCRIPT.is_file(), f"{SCRIPT} is missing")

    def test_script_parses(self):
        """A gate nobody can run is a gate that is not there."""
        src = SCRIPT.read_text(encoding="utf-8")
        compile(src, str(SCRIPT), "exec")

    def test_script_is_stdlib_only(self):
        """THE RULE THAT KEEPS THIS OUT OF THE INSTALL.

        The moment this script imports selenium, playwright or `websockets`,
        running the suite means a pip install into every Pinokio copy of
        Phosphene - and the panel's own python has none of them, so it would
        not even run there. The websocket client is hand-written for exactly
        this reason and this test is what keeps it that way.
        """
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"), str(SCRIPT))
        allowed = set(sys.stdlib_module_names) | {"__future__"}
        seen: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    seen.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level:                      # relative: not a package
                    self.fail(f"relative import in {SCRIPT.name}")
                if node.module:
                    seen.add(node.module.split(".")[0])
        outside = sorted(seen - allowed)
        self.assertEqual(outside, [],
                         f"{SCRIPT.name} imports non-stdlib modules {outside} "
                         f"- that would put a pip dependency in the install")

    def test_never_binds_the_owners_port(self):
        """8199 is the live panel. The gate boots its own on an ephemeral one."""
        import re

        src = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("FORBIDDEN_PORTS", src)
        # The only 8199 in the file may be the one it refuses to use.
        for line in src.splitlines():
            if "8199" in line:
                self.assertTrue(
                    re.search(r"FORBIDDEN_PORTS\s*=", line),
                    f"8199 appears outside the forbidden set: {line.strip()}")


# =============================================================================
# 2. The measurement - skips when there is no browser
# =============================================================================
class EditorLayoutGeometry(unittest.TestCase):
    """One panel, one Chrome, four widths. Measured once for the whole class."""

    doc: dict | None = None
    skip_why: str | None = None
    fail_why: str | None = None
    returncode: int | None = None

    @classmethod
    def setUpClass(cls):
        if not SCRIPT.is_file():
            cls.skip_why = f"{SCRIPT} is missing"
            return
        if not _browser_present():
            cls.skip_why = ("no Chrome/Chromium/Edge on this machine - set "
                            "CHROME_PATH to measure the Editor's layout")
            return
        try:
            proc = subprocess.run(
                [_python(), str(SCRIPT)],
                cwd=str(ROOT), capture_output=True, text=True, timeout=300,
            )
        except subprocess.TimeoutExpired:
            cls.fail_why = "the geometry gate did not finish in 300s"
            return
        if proc.returncode == NO_BROWSER_EXIT:
            cls.skip_why = ("the geometry gate found no browser: "
                            + (proc.stderr or "").strip())
            return
        try:
            cls.doc = json.loads(proc.stdout)
        except json.JSONDecodeError:
            cls.doc = None
            cls.fail_why = (f"the gate printed no JSON (exit "
                            f"{proc.returncode}). stderr: "
                            f"{(proc.stderr or '').strip()[-800:]}")
            return
        cls.returncode = proc.returncode

    def setUp(self):
        if self.skip_why:
            self.skipTest(self.skip_why)
        if self.doc is None:
            self.fail(self.fail_why or "the geometry gate produced no "
                                       "measurement")

    def _width(self, w: str) -> dict:
        widths = self.doc.get("widths") or {}
        self.assertIn(w, widths,
                      f"nothing was measured at {w}px - the gate reported "
                      f"{sorted(widths)} and said {self.doc.get('failures')}")
        return widths[w]

    # -- the gate's own verdict -------------------------------------------
    def test_gate_reports_no_failures(self):
        """The whole verdict in one place, so a run that fails says why."""
        self.assertEqual(self.doc.get("failures") or [], [],
                         "the Editor's layout is wrong:\n  "
                         + "\n  ".join(self.doc.get("failures") or []))
        self.assertTrue(self.doc.get("ok"))
        # The exit code and the document have to agree, or a red gate could be
        # green in CI simply because nobody read the JSON.
        self.assertEqual(self.returncode, 0,
                         f"the gate exited {self.returncode} while reporting "
                         f"no failures")

    def test_the_editor_was_actually_open(self):
        """Zeros measured on a closed Editor would be five false passes."""
        for w in WIDTHS:
            with self.subTest(width=w):
                m = self._width(w)
                self.assertTrue(m.get("editor_open"),
                                f"{w}: the Editor was not open, so nothing "
                                f"measured here means anything")
                self.assertEqual(m.get("missing") or [], [],
                                 f"{w}: the gate could not find "
                                 f"{m.get('missing')}")
                self.assertFalse(m.get("stacked"),
                                 f"{w}: the page was in its stacked (<=900px) "
                                 f"layout, which is not what is being gated")

    # -- the five measurements --------------------------------------------
    def test_header_is_one_row(self):
        for w in WIDTHS:
            with self.subTest(width=w):
                m = self._width(w)
                self.assertEqual(
                    m.get("header_rows"), 1,
                    f"{w}: header_rows {m.get('header_rows')} != 1 "
                    f"(row centres {m.get('header_row_centres')}, "
                    f"children {m.get('header_children')})")

    def test_save_is_a_button_not_a_bar(self):
        """The 1554px defect, measured in the state it appeared in."""
        for w in WIDTHS:
            with self.subTest(width=w):
                m = self._width(w)
                sw = m.get("save_width")
                self.assertIsNotNone(sw, f"{w}: Save was not measured")
                self.assertTrue(
                    m.get("save_primary"),
                    f"{w}: Save was not in its `.primary` state, which is the "
                    f"only state the 1554px defect ever appeared in "
                    f"(class {m.get('save_class')!r})")
                self.assertTrue(
                    SAVE_MIN <= sw <= SAVE_MAX,
                    f"{w}: save_width {sw} outside [{SAVE_MIN:g}, "
                    f"{SAVE_MAX:g}] (class {m.get('save_class')!r})")

    def test_column_does_not_scroll_sideways(self):
        for w in WIDTHS:
            with self.subTest(width=w):
                m = self._width(w)
                over = m.get("column_overflow")
                self.assertIsNotNone(over, f"{w}: #edStage was not measured")
                self.assertLessEqual(
                    over, OVERFLOW_TOL,
                    f"{w}: column_overflow {over} > {OVERFLOW_TOL:g} "
                    f"(scrollWidth {m.get('column_scroll_width')} vs "
                    f"clientWidth {m.get('column_client_width')})")

    def test_timeline_floor_does_not_clip_its_lanes(self):
        for w in WIDTHS:
            with self.subTest(width=w):
                m = self._width(w)
                clip = m.get("timeline_clip")
                self.assertIsNotNone(clip, f"{w}: #sbeScroll was not measured")
                self.assertLessEqual(
                    clip, OVERFLOW_TOL,
                    f"{w}: timeline_clip {clip} > {OVERFLOW_TOL:g} - the "
                    f"soundtrack lane is cut off (scrollHeight "
                    f"{m.get('timeline_scroll_height')} vs clientHeight "
                    f"{m.get('timeline_client_height')}, floor "
                    f"{m.get('timeline_floor_h')})")

    # -- the draggable affordances ----------------------------------------
    def test_every_draggable_affordance_was_actually_measured(self):
        """A gate that measured nothing would report nothing and pass.

        The verdict on hit sizes and overlaps lands in `failures`, which
        `test_gate_reports_no_failures` already asserts is empty — but an empty
        list is also what an empty timeline produces, and an empty timeline is
        exactly what the seed used to be. So this asserts the rows were THERE:
        both blocks, at both ends of the lane range, with a rectangle for every
        affordance the table names.
        """
        handles = self.doc.get("handles") or {}
        self.assertEqual(sorted(handles), ["ceiling", "floor"],
                         f"the gate measured {sorted(handles)}, not both ends "
                         f"of the lane range")
        want = {"clip": ("grip_l", "grip_r", "fade_in", "fade_out"),
                "aclip": ("grip_l", "grip_r", "fade_in", "fade_out",
                          "level", "kf")}
        for end, m in handles.items():
            for block, names in want.items():
                with self.subTest(end=end, block=block):
                    rows = (m.get("blocks") or {}).get(block)
                    self.assertTrue(rows, f"{end}: no {block} on screen")
                    for n in names:
                        self.assertIn(n, rows,
                                      f"{end}/{block}: {n} was never found, so "
                                      f"nothing was checked about it")
                        self.assertGreater(rows[n]["w"], 0)
                        self.assertGreater(rows[n]["h"], 0)
            # ...and the floor really is the floor: the lanes at their bases,
            # not a track that flex has grown in a tall window.
            if end == "floor":
                self.assertEqual(m.get("lane_track"), "64px")
                self.assertEqual(m.get("lane_alane"), "44px")

    def test_stage_never_shows_both_layers(self):
        for w in WIDTHS:
            with self.subTest(width=w):
                m = self._width(w)
                self.assertIs(
                    m.get("stage_layers_both_visible"), False,
                    f"{w}: the <video> and the still are both visible "
                    f"(video opacity {m.get('video_opacity')}, still opacity "
                    f"{m.get('still_opacity')})")


if __name__ == "__main__":
    unittest.main()
