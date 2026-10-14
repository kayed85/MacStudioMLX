"""VC-24: `friendlyJobError` only calls a failure "needs the Q8 model" when
the raw error actually says so.

WHAT THIS GUARDS. The old check was `rawLower.includes('q8') ||
rawLower.includes('keyframe')`. A traceback or file path for essentially ANY
error on the High / Extend / Keyframe lanes contains one of those two
substrings - a path through `mlx_models/ltx-2.5-mlx-q8/…`, a stack frame in
`keyframe_pipeline.py` - so an unrelated OOM or shape error on those lanes was
mislabelled "This mode needs the Q8 model", sending a user who already has Q8
installed off to re-download weights they already have.

Every REAL "needs Q8" refusal (`mlx_ltx_panel.py`'s `RenderRefused` for
Extend / Keyframe / High) says one of two things verbatim:
  - "...(the Q8 model)..." when the pack isn't downloaded
  - "...<tier label> hardware tier — ..." when the Mac can't run it at all
`friendlyJobError` (webapp/js/queue.js) now matches those phrases instead of
the bare substrings.
"""
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

# Verbatim (trimmed) from the real refusals in mlx_ltx_panel.py, so this test
# breaks if the server-side wording ever changes without the client following.
REAL_Q8_MISSING = (
    "Keyframe mode needs the LTX-2.5 High add-on (the Q8 model), which "
    "isn't downloaded on this Mac yet. Missing 2 file(s): a, b."
)
REAL_HARDWARE_TIER = (
    "Extend isn't supported on the Compact hardware "
    "tier — the dev transformer needs more headroom than this Mac has."
)
# What a genuinely unrelated failure looks like when it happens to be
# raised WHILE rendering on the High/Extend/Keyframe lane - the traceback
# or file path routinely contains "q8" or "keyframe" with nothing to do
# with the pack being missing.
FALSE_POSITIVE_OOM = (
    "Helper killed by the OS: SIGKILL while loading "
    "mlx_models/ltx-2.5-mlx-q8/transformer-dev.safetensors"
)
FALSE_POSITIVE_SHAPE = (
    'Traceback (most recent call last):\n  File "keyframe_pipeline.py", '
    "line 214, in generate_two_stage\nRuntimeError: shape mismatch (256, 256) vs (272, 272)"
)


def _run_node(script: str) -> dict:
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


class FriendlyJobErrorMatchesRealRefusals(unittest.TestCase):
    def _classify(self, *raws: str) -> dict:
        src = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
        # H3-22 (4.17 merge): the generic fallback redacts local paths via
        # _redactLocalPaths, so extract it alongside.
        fn = (extract_function("_redactLocalPaths", src) + "\n"
              + extract_function("friendlyJobError", src))
        calls = ",\n  ".join(f"friendlyJobError({json.dumps(r)})" for r in raws)
        return _run_node(fn + f"\nconsole.log(JSON.stringify([\n  {calls}\n]));")

    def test_real_q8_pack_missing_is_labelled_correctly(self):
        got = self._classify(REAL_Q8_MISSING)[0]
        self.assertEqual(got["friendly"], "This mode needs the Q8 model.")

    def test_real_hardware_tier_refusal_is_labelled_correctly(self):
        got = self._classify(REAL_HARDWARE_TIER)[0]
        self.assertEqual(got["friendly"], "This mode needs the Q8 model.")

    def test_an_oom_on_the_q8_lane_is_not_mislabelled(self):
        got = self._classify(FALSE_POSITIVE_OOM)[0]
        self.assertNotEqual(got["friendly"], "This mode needs the Q8 model.")
        # SIGKILL is caught earlier in the function - it should still get
        # ITS OWN correct label, just never the Q8 one.
        self.assertIn("out of memory", got["friendly"].lower())

    def test_a_shape_error_naming_keyframe_pipeline_is_not_mislabelled(self):
        got = self._classify(FALSE_POSITIVE_SHAPE)[0]
        self.assertNotEqual(got["friendly"], "This mode needs the Q8 model.")
        self.assertEqual(got["friendly"], "Job failed.")

    def test_no_longer_a_bare_substring_check(self):
        src = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
        body = extract_function("friendlyJobError", src)
        self.assertNotIn("rawLower.includes('q8')", body)
        self.assertNotIn("rawLower.includes('keyframe')", body)


if __name__ == "__main__":
    unittest.main()
