#!/usr/bin/env python3
"""The Storyboard on a Mac that CANNOT deliver 1024x576.

GitHub #71, Mac mini M4 Pro 24 GB (768px cap). Three defects, one film:

  * every freshly planned board carried an over-cap delivery pass, because
    `new_storyboard()` hands back the module DEFAULT_POLICY and the panel's
    `setdefault("policy", _sb_policy_for(...))` could therefore never fire —
    the tier clamp had never run for a new film on any Mac;
  * the one-click fix under the error offered a hardcoded "Use 1024x576",
    itself illegal at a 768px cap, and its write was guarded on `> 1024` so
    clicking it did NOTHING. Render stayed disabled with no way out;
  * the Quality control's chips printed sizes from a literal table in the
    browser, so they advertised canvases the machine would never deliver and
    the click wrote one of them straight into the board.

Everything here is fast: pure functions, one node run for the browser half,
and text pins on the two structural rules (one canvas table, one clamp).
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
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import storyboard  # noqa: E402
from extract_panel_js import extract_function  # noqa: E402

NODE = shutil.which("node")
JS = ROOT / "webapp" / "js" / "storyboard.js"
CSS = ROOT / "webapp" / "style" / "panel.css"
INDEX = ROOT / "webapp" / "index.html"

# The cap a 24 GB Mac reports (CAPABILITIES["base"]["t2v_max_dim"]).
CAP_24GB = 768


class FitCanvas(unittest.TestCase):
    """THE ONE CLAMP FORMULA — everything else must agree with it."""

    def test_leaves_a_legal_canvas_alone(self):
        self.assertEqual(storyboard.fit_canvas(640, 448, CAP_24GB), (640, 448))
        self.assertEqual(storyboard.fit_canvas(768, 432, CAP_24GB), (768, 432))

    def test_no_cap_is_no_clamp(self):
        self.assertEqual(storyboard.fit_canvas(1280, 704, 0), (1280, 704))

    def test_is_IDEMPOTENT(self):
        """The whole bug in one property: the size the UI offers must be a size
        the validator then accepts. Offer 1024x576 against a 768 cap and the
        error the button was meant to clear simply comes back."""
        for w, h in ((1024, 576), (1280, 704), (640, 448)):
            for cap in (512, 768, 1024, 1536):
                once = storyboard.fit_canvas(w, h, cap)
                self.assertEqual(storyboard.fit_canvas(*once, cap), once,
                                 f"{w}x{h} @ {cap} does not survive its own clamp")
                self.assertLessEqual(max(once), cap)
                self.assertEqual(once[0] % 8, 0)
                self.assertEqual(once[1] % 8, 0)


class OverCapError(unittest.TestCase):
    def _board(self, w, h):
        b = storyboard.new_storyboard("sb_cap", "cap")
        b["policy"] = {"draft": {"quality": "quick", "width": 640, "height": 448,
                                 "frames": 49},
                       "final": {"quality": "standard", "width": w, "height": h,
                                 "frames": 121}}
        b["shots"] = [{"n": 1, "title": "one", "mode": "text", "engine": "ltx",
                       "prompt": "a street at night", "duration_s": 5.0, "refs": []}]
        return b

    def test_carries_an_offer_the_validator_accepts(self):
        errs = storyboard.validate_storyboard_detail(
            self._board(1024, 576), max_dim=CAP_24GB)
        cap = [e for e in errs if e["code"] == "over_cap"]
        self.assertEqual(len(cap), 1, errs)
        d = cap[0]["data"]
        self.assertEqual((d["fit_width"], d["fit_height"]), (768, 432))
        # Take the offer; the error must be GONE, not restated.
        fixed = self._board(d["fit_width"], d["fit_height"])
        self.assertEqual(
            [e["code"] for e in storyboard.validate_storyboard_detail(
                fixed, max_dim=CAP_24GB) if e["code"] == "over_cap"], [])


class PanelPolicy(unittest.TestCase):
    """The server half: a new film must be born legal."""

    def setUp(self):
        import mlx_ltx_panel as p                                # noqa: PLC0415
        self.p = p

    def test_policy_for_is_clamped_at_every_quality(self):
        p = self.p
        real = p._sb_max_dim
        p._sb_max_dim = lambda: CAP_24GB                          # noqa: SLF001
        try:
            for fq in p.STORYBOARD_FINAL_QUALITIES:
                for dq in p.STORYBOARD_DRAFT_QUALITIES:
                    pol = p._sb_policy_for(dq, fq)                # noqa: SLF001
                    for key, cell in pol.items():
                        self.assertLessEqual(
                            max(cell["width"], cell["height"]), CAP_24GB,
                            f"{dq}/{fq} {key} is over the cap: {cell}")
            # And the BOOT table the chips are labelled from says the same.
            cv = p._sb_canvases()                                 # noqa: SLF001
            for q, cell in cv.items():
                self.assertLessEqual(max(cell["width"], cell["height"]), CAP_24GB, q)
            self.assertEqual(cv["standard"], {"width": 768, "height": 432})
        finally:
            p._sb_max_dim = real                                  # noqa: SLF001

    def test_a_planned_board_validates_clean(self):
        """The regression itself, end to end over the two pure halves."""
        p = self.p
        real = p._sb_max_dim
        p._sb_max_dim = lambda: CAP_24GB                          # noqa: SLF001
        try:
            board = storyboard.new_storyboard("sb_new", "new")
            board["policy"] = p._sb_policy_for("quick", "standard")  # noqa: SLF001
            board["shots"] = [{"n": 1, "title": "one", "mode": "text",
                               "engine": "ltx", "prompt": "a street at night",
                               "duration_s": 5.0, "refs": []}]
            errs = storyboard.validate_storyboard_detail(board, max_dim=CAP_24GB)
            self.assertEqual([e["code"] for e in errs], [], errs)
        finally:
            p._sb_max_dim = real                                  # noqa: SLF001

    def test_the_plan_route_ASSIGNS_the_policy_it_computes(self):
        """`setdefault` on a board that always already has one is a no-op, and
        that is precisely how the clamp came to never run. Pinned as text
        because the handler is a 200-line arm of do_POST."""
        src = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
        i = src.index('board = storyboard.new_storyboard(bid, "Planning…")')
        window = src[i:i + 1400]
        self.assertIn('board["policy"] = _sb_policy_for(', window,
                      "a brand-new board must be GIVEN the tier-clamped policy; "
                      "the setdefault() below it can never fire")


@unittest.skipIf(NODE is None, "node not installed")
class FixButton(unittest.TestCase):
    """The browser half of the one-click fix, run for real."""

    def _run(self, policy, canvases):
        fn = extract_function("sbFixError", JS.read_text(encoding="utf-8"))
        prog = f"""
        const SB_BOOT = {{ canvases: {json.dumps(canvases)} }};
        const board = {json.dumps({"policy": policy, "shots": []})};
        const SB = {{ payload: {{ board: board }} }};
        function sbShotById() {{ return null; }}
        function sbAddShot() {{}}
        function sbRenderPlan() {{}}
        function sbQueueSave() {{}}
        {fn}
        sbFixError('cap', 0, 'over_cap');
        console.log(JSON.stringify(board.policy));
        """
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "fix.mjs"
            f.write_text(prog, encoding="utf-8")
            out = subprocess.run([NODE, str(f)], capture_output=True, text=True,
                                 timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout.strip().splitlines()[-1])

    def test_lands_under_a_768_cap(self):
        cv = {"quick": {"width": 640, "height": 448},
              "balanced": {"width": 768, "height": 432},
              "standard": {"width": 768, "height": 432}}
        got = self._run(
            {"draft": {"quality": "quick", "width": 640, "height": 448},
             "final": {"quality": "standard", "width": 1024, "height": 576}}, cv)
        # THE BUG: the old button only wrote when max(w,h) > 1024, so at a 768
        # cap it left 1024x576 exactly where it was and the error never cleared.
        self.assertEqual(got["final"], {"quality": "standard",
                                        "width": 768, "height": 432})
        self.assertEqual(got["draft"]["width"], 640)

    def test_an_unknown_quality_still_lands_somewhere_legal(self):
        cv = {"quick": {"width": 640, "height": 448},
              "balanced": {"width": 768, "height": 432}}
        got = self._run(
            {"draft": {"quality": "quick", "width": 640, "height": 448},
             "final": {"quality": "wat", "width": 1920, "height": 1080}}, cv)
        self.assertEqual(got["final"]["width"], 768)
        self.assertEqual(got["final"]["quality"], "balanced")


class NoSecondCanvasTable(unittest.TestCase):
    """One home for the numbers. Three copies is what shipped the bug."""

    def test_the_browser_holds_no_canvas_literal(self):
        js = JS.read_text(encoding="utf-8")
        self.assertNotIn("[1024, 576]", js)
        self.assertNotIn("balanced: [768, 432]", js)
        self.assertIn("SB_BOOT.canvases", js)

    def test_the_markup_holds_no_hand_written_sizes(self):
        html = INDEX.read_text(encoding="utf-8")
        i = html.index('id="sbDraftQuality"')
        self.assertNotIn("640×448", html[i - 400:i + 400],
                         "draft chips must be rendered from the server's "
                         "clamped table, not typed into the markup")


class PlanColumnIsNotSqueezable(unittest.TestCase):
    """The layout half of #71.

    `.customize-section` carries `overflow: hidden`, which gives a flex item an
    automatic minimum size of ZERO. Inside `.sb-plan` — a bounded column flex
    container — it was therefore the ONE child the browser could crush, and it
    absorbed the whole overflow the moment the pane's content wrapped: at 1365px
    the Quality disclosure collapsed to its 2px border. Full screen looked fine,
    which is why it read as a Pinokio-only defect: the app window is narrower.
    """

    def test_children_of_sb_plan_do_not_shrink(self):
        css = CSS.read_text(encoding="utf-8")
        self.assertRegex(
            css, r"\.sb-plan\s*>\s*\*\s*\{[^}]*flex-shrink:\s*0",
            "no rule stops .sb-plan's children being crushed")

    def test_the_scroller_is_still_the_stage(self):
        """If .sb-plan ever became the scroller this rule would trap content."""
        css = CSS.read_text(encoding="utf-8")
        rules = [m.group(0) for m in re.finditer(r"(?m)^\s*#sbStage\s*\{[^}]*\}", css)]
        self.assertTrue(rules, "#sbStage rule missing")
        self.assertTrue(any("overflow-y: auto" in r for r in rules),
                        f"#sbStage no longer scrolls: {rules}")


class ShotListRepaintIsGuarded(unittest.TestCase):
    """A <select> whose native menu is open is not document.activeElement in an
    app window, so the 2 s poll rebuilt the shot list under it and the menu
    blinked shut — "you can't change the length of the video". The list is
    byte-identical on almost every tick, so it must only be painted when it
    CHANGED, and an in-progress dropdown must hold the repaint off outright."""

    def test_only_paints_when_the_html_changed(self):
        js = JS.read_text(encoding="utf-8")
        fn = extract_function("sbRenderPlan", js)
        self.assertIn("_sbShotsHtml", fn)
        self.assertRegex(fn, r"if\s*\(html\s*!==\s*_sbShotsHtml")

    def test_an_open_dropdown_holds_the_repaint_and_the_poll(self):
        js = JS.read_text(encoding="utf-8")
        self.assertIn("sbSelectOpen", extract_function("sbRenderPlan", js))
        self.assertIn("sbSelectOpen", extract_function("sbHoldingShots", js))
        # and the flag cannot be latched forever
        self.assertIn("SB_SELECT_HOLD_MS", js)


if __name__ == "__main__":
    unittest.main()
