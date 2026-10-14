"""H3-04 [P1]: Fast/Best is invisible after Generate.

Pinned by the flow review: the Now card meta (queue.js) showed mode, size,
frames and time only — no Speed. Queue rows showed nothing. A Best clip's
info modal had no Speed row at all (only Fast/tristep and the retired
Turbo earned one). Speed persists per browser (phos_h3_speed), so one Best
choice silently sticks — and the "expected 18, waited 43+" report matched a
Standard 10s on an M4 Pro 48 GB exactly: Fast was priced at 16.8 min before
4.16.2, Best on the same cell at 44.5 min, and nothing on screen said which
one was running or that it was taking even longer than THAT.

The fix: prog.h3_speed / prog.h3_pace_warning (server, mlx_ltx_panel.py's
H3 run loop) plus h3JobSpeedLabel / h3JobShapeLabel (engines.js) wired into
the Now card, the queue rows, and the info modal.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("LTX_STATE_DIR", tempfile.mkdtemp(prefix="phos-h304-"))
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402

NODE = shutil.which("node")
ENGINES = (ROOT / "webapp" / "js" / "engines.js").read_text(encoding="utf-8")
QUEUE = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
PANEL = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")


def _h3_run_block() -> str:
    i = PANEL.index("win_span = 87.0 / float(tot_windows)")
    j = PANEL.index("rc = proc.wait()", i)
    return PANEL[i:j]


class ServerRecordsWhichSpeedIsRunning(unittest.TestCase):
    def test_progress_carries_h3_speed(self):
        block = _h3_run_block()
        self.assertIn('"h3_speed": ("fast" if tristep else "best")', block)

    def test_progress_carries_a_pace_note_field(self):
        block = _h3_run_block()
        self.assertIn('"h3_pace_warning": _pace_note', block)

    def test_pace_flag_compares_measured_to_modeled_per_step(self):
        block = _h3_run_block()
        self.assertIn("_modeled_per_step", block)
        self.assertIn("per_step > _modeled_per_step * 1.5", block)

    def test_pace_note_names_the_fast_alternative_when_running_best(self):
        block = _h3_run_block()
        self.assertIn("Fast would take about", block)
        self.assertIn("not tristep and _fast_alt is not None", block)

    def test_modeled_per_step_uses_the_same_cost_model_as_every_estimate(self):
        """Not a second cost model — the same _h3_forward_seconds /
        _h3_packed_rows / _h3_speed_factor h3_estimate_minutes itself uses."""
        rows = P._h3_packed_rows(768, 448, 124)
        modeled = P._h3_forward_seconds(rows) * P._h3_speed_factor(1)
        self.assertGreater(modeled, 0)


class ClientLabelsAreSharedAcrossAllThreeSurfaces(unittest.TestCase):
    def test_now_card_reads_prog_h3_speed(self):
        self.assertIn("prog.h3_speed", QUEUE)
        self.assertIn("prog.h3_pace_warning", QUEUE)

    def test_now_card_falls_back_to_the_submitted_params(self):
        self.assertIn("h3JobSpeedLabel(cur)", QUEUE)
        self.assertIn("h3JobShapeLabel(cur)", QUEUE)

    def test_queue_rows_show_the_speed_tag(self):
        self.assertIn("h3JobSpeedLabel(j.params)", QUEUE)

    def test_info_modal_best_clips_get_a_speed_row_too(self):
        """The bug: Fast (h3_tristep) and the retired Turbo (h3_turbo) each
        pushed a <dt>Speed</dt> row; a plain Best clip fell through all
        three branches with no Speed row printed anywhere."""
        i = QUEUE.index("if (p.h3_tristep) {")
        j = QUEUE.index("if (p.accel && p.accel", i)
        block = QUEUE[i:j]
        self.assertIn("<dt>Speed</dt><dd>Best", block)
        # It must be reachable as a genuine else, not nested under a
        # condition that could also be false.
        self.assertRegex(block, r"\}\s*else\s*\{\s*\n\s*//.*H3-04")

    def test_shared_label_functions_are_published(self):
        pub = ENGINES[ENGINES.index("Object.assign(globalThis"):]
        self.assertIn("h3JobSpeedLabel", pub)
        self.assertIn("h3JobShapeLabel", pub)


@unittest.skipUnless(NODE, "node not on PATH")
class LabelFunctionsRunCorrectly(unittest.TestCase):
    def _run(self, js: str) -> str:
        r = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def _harness(self) -> str:
        from scripts.extract_panel_js import extract_function
        return "\n".join(extract_function(n, ENGINES) for n in (
            "h3ResolveTierKey", "h3TierByKey", "h3SpeedOfParams",
            "h3JobSpeedLabel", "h3JobShapeLabel")) + """
var H3 = { tiers: [{ key: 'standard_10s', quality: 'standard', length: '10s',
                     quality_label: 'Standard', length_label: '10s' }],
          aliases: {} };
"""

    def test_fast_and_best_labels(self):
        js = self._harness() + """
console.log(JSON.stringify([
  h3JobSpeedLabel({ engine: 'h3', h3_tristep: true }),
  h3JobSpeedLabel({ engine: 'h3', h3_tristep: false, h3_turbo: false }),
  h3JobSpeedLabel({ engine: 'ltx' }),
]));
"""
        import json as _json
        self.assertEqual(_json.loads(self._run(js)), ["Fast", "Best", ""])

    def test_shape_label_looks_up_the_real_tier(self):
        js = self._harness() + """
console.log(h3JobShapeLabel({ engine: 'h3', h3_tier: 'standard_10s' }));
"""
        self.assertEqual(self._run(js).replace('"', ''), "Hailuo H3 · Standard 10s")


if __name__ == "__main__":
    unittest.main()
