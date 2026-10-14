"""VC-32 (second half): "models N/M" counts only repos relevant to the
generation actually running, and the pill says "optional missing" in words
instead of a bare fraction once the required set is complete.

WHAT THIS GUARDS. required_files.json lists 12 repos: 2.3's own trio (q4,
gemma, q8), 2.5's trio+add-on (q4_25, gemma4_25, q8_25, hq_25), four IC-LoRA
adapters and the temporal autoencoder. The header pill's "models N/M" used
ALL twelve as the denominator regardless of which generation is active, so
a complete, fully-working 2.5 install with no IC-LoRAs installed read
"models 4/12" — and a correctly GREEN pill (every file the active render
needs is present) still showed a fraction that reads as broken, which is
the "models 10/12 shown green" confusion from the review.

Fixed: relevant_repo_keys() (mlx_ltx_panel.py) scopes the count to the
ACTIVE generation's own base pack + text encoder + High add-on, plus every
repo that isn't claimed by ANY generation (the IC-LoRAs, tae — usable
regardless of which generation is active). /status's repos_ready/
repos_total (routes_queue.py) now report against that set. The pill
(queue.js) shows "models ✓" when nothing relevant is missing, "models ✓ (N
optional not installed)" when the required set is done but extras aren't,
and only falls back to a raw fraction when the REQUIRED set itself is
incomplete (still the urgent, bad-color case).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlencode

ROOT = Path(__file__).resolve().parent
STATE = Path(tempfile.mkdtemp(prefix="phos-models-pill-"))
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8319")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mlx_ltx_panel as P  # noqa: E402
from extract_panel_js import extract_function  # noqa: E402

NODE = shutil.which("node")


class RelevantRepoKeys(unittest.TestCase):
    def test_2_5_excludes_2_3s_own_trio(self):
        keys = P.relevant_repo_keys("ltx25")
        self.assertNotIn("q4", keys)
        self.assertNotIn("gemma", keys)
        self.assertNotIn("q8", keys)

    def test_2_5_includes_its_own_packs(self):
        keys = P.relevant_repo_keys("ltx25")
        for k in ("q4_25", "gemma4_25", "q8_25", "hq_25"):
            self.assertIn(k, keys)

    def test_2_5_includes_version_agnostic_extras(self):
        keys = P.relevant_repo_keys("ltx25")
        for k in ("ic_colorize", "ic_ingredients", "ic_union_control",
                 "ic_upscale_x2", "tae"):
            self.assertIn(k, keys)

    def test_2_3_excludes_2_5s_own_trio_and_has_no_addon(self):
        keys = P.relevant_repo_keys("ltx23")
        self.assertNotIn("q4_25", keys)
        self.assertNotIn("gemma4_25", keys)
        self.assertNotIn("hq_25", keys)
        self.assertIn("q4", keys)
        self.assertIn("gemma", keys)
        self.assertIn("q8", keys)

    def test_total_is_nine_not_twelve(self):
        """Pins the exact number the review's own repro would have seen -
        a change to required_files.json that shifts this is worth a
        deliberate look, not a silent drift back toward "everything"."""
        self.assertEqual(len(P.relevant_repo_keys("ltx25")), 9)


class StatusPayloadUsesTheScopedCount(unittest.TestCase):
    def test_repos_total_matches_relevant_keys_not_all_repos(self):
        all_repos = len(P._repos())
        relevant = len(P.relevant_repo_keys())
        self.assertLess(relevant, all_repos, "the fixture manifest should have irrelevant repos to exclude")
        snap = P.repo_status_list()
        keys = P.relevant_repo_keys()
        scoped = [r for r in snap if r.get("key") in keys]
        self.assertEqual(len(scoped), relevant)


DOM_SHIM = r"""
function makeEl(id) {
  return { id, innerHTML: '', className: '', title: '',
           classList: { toggle(){}, add(){}, remove(){} } };
}
const ELS = {};
global.document = { getElementById: (id) => (ELS[id] || (ELS[id] = makeEl(id))) };
"""


class PillTextPhrasing(unittest.TestCase):
    def _run_node(self, script: str) -> dict:
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

    def _snippet(self) -> str:
        src = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
        i = src.rindex("\n", 0, src.index("Models pill — roll-up status")) + 1
        j = src.index("\n  }\n", i)
        return src[i:j + 5]

    def _run(self, *, base_available: bool, q8_available: bool, ready: int, total: int) -> str:
        snippet = self._snippet()
        script = DOM_SHIM + f"""
const s = {{ base_available: {json.dumps(base_available)}, q8_available: {json.dumps(q8_available)},
             repos_ready: {ready}, repos_total: {total}, download: null,
             server_now: 0 }};
{snippet}
console.log(JSON.stringify({{ html: ELS.modelsPill.innerHTML }}));
"""
        return self._run_node(script)["html"]

    def test_everything_ready_shows_a_checkmark(self):
        html = self._run(base_available=True, q8_available=True, ready=9, total=9)
        self.assertIn("models ✓", html)
        self.assertNotIn("9/9", html)

    def test_required_done_optional_missing_names_the_count(self):
        html = self._run(base_available=True, q8_available=False, ready=7, total=9)
        self.assertIn("models ✓ (2 optional not installed)", html)

    def test_base_incomplete_still_shows_the_raw_fraction(self):
        """The urgent case (render literally cannot happen) keeps the raw
        count - it's the ONLY case where a fraction is the right shape."""
        html = self._run(base_available=False, q8_available=False, ready=1, total=9)
        self.assertIn("models 1/9", html)
        self.assertNotIn("✓", html)


if __name__ == "__main__":
    unittest.main()
