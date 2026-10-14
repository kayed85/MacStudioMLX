"""VC-10: a missing base (Q4) pack can no longer queue with Generate armed.

WHAT WAS ACTUALLY WRONG, AND WHAT WASN'T. The review's steps ("boot with
only transformer-distilled.safetensors missing ... Generate is enabled;
clicking it adds 'Queue 1' ... a user whose first download was interrupted
can queue a batch that FAILS JOB BY JOB") describe each queued job failing,
not rendering mosaic garbage — because `run_job_inner`'s existing
`ltx_pack_preflight(_char_pack, "This render")` call (the Quick/Standard
Q4-one-stage branch, unconditional, not character-gated despite the
variable's name) already refuses an incomplete Q4 pack before the helper
ever starts, for T2V/I2V exactly like every other mode. That part was
already correct and test_a_real_missing_base_pack_refuses_before_the_helper
below pins it (nothing in the existing suite proved this end to end).

THE REAL BUG was client-side, in `queue.js`'s `applyPackIncompleteGate`: it
returned early for a Q4/base pack on the theory that "an incomplete BASE
pack is already a hard block in the models card above" — but that card
(`updateModelsCard`'s red "Base models needed" state) only ever rendered a
Download button; it never touched `#genBtn`. So Generate stayed clickable,
a batch could be queued, and each entry burned a round trip to the server
(job dequeued, helper NOT started, refused) before failing — needless
churn compared to the button simply being disabled up front, the same way
Q8-incomplete already disables it.

Fixed: `applyPackIncompleteGate` now checks `!s.base_available` FIRST,
ahead of the q8-specific logic, and disables `#genBtn` with the missing
filename(s) named.

Also fixed while in the area: "(1 files left)" (settings.js) always said
"files" regardless of count, and the boot log's "model integrity: OK (19
weight files verified)" said so even when a required file was outright
missing (integrity deliberately only hashes files that exist — a missing
model is the download flow's job — so the line was technically true and
dangerously reassuring); it now folds in the render capability's own
missing-file count.
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
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mlx_ltx_panel as P  # noqa: E402
from extract_panel_js import extract_function  # noqa: E402

NODE = shutil.which("node")


class _Helper:
    ready_info: dict = {}

    def __init__(self):
        self.sent: list[dict] = []

    def is_alive(self):
        return True

    def kill(self, *a, **k):
        pass

    def run(self, spec, *a, **k):
        self.sent.append(spec)
        out = (spec.get("params") or {}).get("output_path")
        if out:
            Path(out).write_bytes(b"x")
        return {"seed_used": 1, "elapsed_sec": 0.1}


class ServerAlreadyRefusesAMissingBasePack(unittest.TestCase):
    """Not a fix - a gap-filling regression pin for a real path nothing in
    the existing suite exercised end to end."""

    def _form(self, **extra) -> dict:
        # upscale=off: this class only cares whether the helper was reached,
        # not the export tail - Balanced's default fit_720p export would
        # shell out to a REAL ffmpeg against the fake 1-byte helper output
        # written by _Helper.run(), which is a different (and irrelevant)
        # way for this test to fail.
        form = {"mode": "t2v", "prompt": "a lighthouse keeper", "width": "1024",
                "height": "576", "frames": "121", "seed": "-1",
                "quality": "balanced", "accel": "off", "enhance": "off",
                "upscale": "off"}
        form.update(extra)
        return form

    def test_a_missing_base_pack_refuses_before_the_helper(self):
        helper = _Helper()
        job = P.make_job(self._form())
        with mock.patch.object(P, "HELPER", helper), \
             mock.patch.object(P, "OUTPUT", Path(tempfile.mkdtemp(prefix="basepack-"))), \
             mock.patch.object(P, "hq_surface_missing", lambda *a, **k: []), \
             mock.patch.object(P, "write_sidecar", lambda *a, **k: True), \
             mock.patch.object(P, "_canonical_layout", return_value=True), \
             mock.patch.object(P, "pack_repo", return_value={"repo_id": "org/pack"}), \
             mock.patch.object(P, "pack_available_anywhere", return_value=False), \
             mock.patch.object(P, "pack_missing_files",
                               return_value=["transformer-distilled.safetensors"]), \
             mock.patch.object(P, "pack_path", return_value=Path("/fake/pack")):
            with self.assertRaises(RuntimeError) as cm:
                P.run_job_inner(job)
        self.assertEqual(helper.sent, [], "the helper must never start on an incomplete pack")
        self.assertIn("transformer-distilled.safetensors", str(cm.exception))
        self.assertIn("incomplete", str(cm.exception))

    def test_a_complete_pack_reaches_the_helper(self):
        helper = _Helper()
        job = P.make_job(self._form())
        with mock.patch.object(P, "HELPER", helper), \
             mock.patch.object(P, "OUTPUT", Path(tempfile.mkdtemp(prefix="basepack-ok-"))), \
             mock.patch.object(P, "hq_surface_missing", lambda *a, **k: []), \
             mock.patch.object(P, "write_sidecar", lambda *a, **k: True), \
             mock.patch.object(P, "ltx_pack_preflight", lambda *a, **k: None):
            P.run_job_inner(job)
        self.assertEqual(len(helper.sent), 1)


class ClientGateDisablesGenerate(unittest.TestCase):
    """The actual VC-10 fix. Run in node via extract_panel_js: confirms the
    base check runs FIRST (ahead of the q8-specific logic) and that a
    complete base pack leaves the rest of the function reachable exactly
    as before."""

    def _fn(self) -> str:
        src = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
        return extract_function("applyPackIncompleteGate", src)

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

    def _shim(self, quality: str = "balanced") -> str:
        return r"""
function makeEl(id) {
  return { id, value: '', innerHTML: '', textContent: '', hidden: true,
           disabled: false, dataset: {}, title: '',
           removeAttribute(n) { delete this[n]; },
           classList: { toggle(){}, add(){}, remove(){} } };
}
const ELS = { engineRowNote: makeEl('engineRowNote'), genBtn: makeEl('genBtn'),
              characterIdInput: makeEl('characterIdInput'), quality: makeEl('quality') };
ELS.quality.value = %(quality)s;
global.document = {
  getElementById: (id) => ELS[id] || (ELS[id] = makeEl(id)),
  querySelector: () => null,
  body: { dataset: { engine: 'ltx' } },
};
global.escapeHtml = (s) => String(s);
global._qualityUsesHq = (q) => q === 'high';
""" % {"quality": json.dumps(quality)}

    def test_base_missing_disables_generate_and_names_the_file(self):
        fn = self._fn()
        out = self._run_node(self._shim() + fn + r"""
applyPackIncompleteGate({ base_available: false,
  base_missing: ['ltx-2.5-mlx-q4/transformer-distilled.safetensors'] });
console.log(JSON.stringify({
  disabled: ELS.genBtn.disabled,
  blocked: ELS.genBtn.dataset.packBlocked,
  note: ELS.engineRowNote.innerHTML,
  hidden: ELS.engineRowNote.hidden,
}));
""")
        self.assertTrue(out["disabled"])
        self.assertEqual(out["blocked"], "1")
        self.assertIn("transformer-distilled.safetensors", out["note"])
        self.assertIn("1 file", out["note"])
        self.assertNotIn("1 files", out["note"])
        self.assertFalse(out["hidden"])

    def test_base_available_leaves_generate_enabled(self):
        fn = self._fn()
        out = self._run_node(self._shim() + fn + r"""
applyPackIncompleteGate({ base_available: true, q8_missing: [] });
console.log(JSON.stringify({ disabled: ELS.genBtn.disabled,
                             blocked: ELS.genBtn.dataset.packBlocked }));
""")
        self.assertFalse(out["disabled"])
        self.assertIsNone(out.get("blocked"))

    def test_base_missing_takes_priority_over_the_q8_check(self):
        """Even mid-High-quality-selection, a missing BASE pack is the
        blocker to report - not silently ignored because the code used to
        assume the models card "already" handled it."""
        fn = self._fn()
        out = self._run_node(self._shim("high") + fn + r"""
applyPackIncompleteGate({ base_available: false, base_missing: ['x.safetensors'],
                          q8_missing: [] });
console.log(JSON.stringify({ disabled: ELS.genBtn.disabled,
                             note: ELS.engineRowNote.innerHTML }));
""")
        self.assertTrue(out["disabled"])
        self.assertIn("Finish the model download", out["note"])

    def test_stale_comment_about_the_models_card_is_gone(self):
        """The specific wrong assumption that broke this - pinned so it
        can't quietly come back in a future edit."""
        src = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
        self.assertNotIn("already a hard block in the models card", src)


class CopyFixes(unittest.TestCase):
    def test_settings_js_pluralizes_the_missing_count(self):
        src = (ROOT / "webapp" / "js" / "settings.js").read_text(encoding="utf-8")
        self.assertIn("missing === 1 ? '' : 's'", src)

    def test_boot_log_mentions_missing_files_not_bare_ok(self):
        src = ROOT.joinpath("mlx_ltx_panel.py").read_text(encoding="utf-8")
        m = re.search(r'print\(f"model integrity: OK[^\n]*\n[^\n]*\n[^\n]*\)', src)
        self.assertIsNotNone(m, "boot integrity OK print not found")
        self.assertIn("capability_missing", src[m.start() - 400:m.end()])


if __name__ == "__main__":
    unittest.main()
