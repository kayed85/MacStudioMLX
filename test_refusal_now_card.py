"""4.17.3 fleet fix: a refused render no longer offers a Retry that is refused
again.

THE BUG (fleet, 4.17.2, a 16 GB Mac): a Lip-sync clip past the Compact-tier
length cap was refused (correctly) — and the failed Now card showed the
generic "Job failed." with a Retry button. Retry re-queues the identical job,
so it was refused again: ten times in ten minutes, then the user gave up on
that clip. The refusal text never matched friendlyJobError (it has no
"hardware tier —"), and the card offered Retry for every refusal.

THE FIX: friendlyJobError names the Lip-sync length refusal and returns
action 'split' (Split into N s clips — the one action that renders the song
on that Mac); nowCardFailureActions withholds Retry for every hardware-tier
refusal. Both run here in node, fed the server's REAL refusal sentence.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")
os.environ.setdefault("PHOSPHENE_DISABLE_VERSION_CHECK", "1")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mlx_ltx_panel as P  # noqa: E402
from extract_panel_js import extract_function  # noqa: E402

NODE = shutil.which("node")


def _node(calls: list[str]) -> list:
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
    src = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
    fn = "\n".join(extract_function(n, src) for n in
                   ("_redactLocalPaths", "friendlyJobError", "nowCardFailureActions"))
    script = fn + "\nconsole.log(JSON.stringify([\n  " + ",\n  ".join(calls) + "\n]));"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        r = subprocess.run([NODE, path], capture_output=True, text=True,
                           errors="replace", timeout=60)
    finally:
        Path(path).unlink(missing_ok=True)
    if r.returncode != 0:
        raise AssertionError(r.stderr[-2000:])
    return json.loads(r.stdout.strip().splitlines()[-1])


class RefusedJobsDoNotOfferRetry(unittest.TestCase):
    def test_lipsync_length_refusal_offers_split(self):
        cap = 241
        raw = P.a2v_length_refusal(313, cap)       # the server's real sentence
        cap_s = int(round(P._frames_to_model_duration(cap)))
        job = {"id": "j1", "status": "failed", "error": raw, "refused_reason": "hardware_tier"}
        fr, html = _node([
            f"friendlyJobError({json.dumps(raw)})",
            f"(() => {{ const f = friendlyJobError({json.dumps(raw)}); "
            f"return nowCardFailureActions({json.dumps(job)}, f); }})()",
        ])
        self.assertEqual(fr["action"], "split")
        self.assertEqual(fr["splitSec"], cap_s)
        self.assertNotEqual(fr["friendly"], "Job failed.")
        self.assertIn('data-action="split"', html)
        self.assertIn(f'data-sec="{cap_s}"', html)
        self.assertNotIn('data-action="retry"', html)

    def test_any_hardware_tier_refusal_withholds_retry(self):
        job = {"id": "j2", "status": "failed", "refused_reason": "hardware_tier",
               "error": "This clip is too long for this Mac's GPU on the Compact hardware tier."}
        (html,) = _node([f"nowCardFailureActions({json.dumps(job)}, {{}})"])
        self.assertNotIn('data-action="retry"', html)
        self.assertIn('data-action="dismiss"', html)

    def test_a_real_failure_still_offers_retry(self):
        job = {"id": "j3", "status": "failed", "error": "something broke"}
        oom = {"id": "j4", "status": "failed", "error": "helper exited from SIGKILL"}
        plain, smaller = _node([
            f"nowCardFailureActions({json.dumps(job)}, friendlyJobError('something broke'))",
            f"nowCardFailureActions({json.dumps(oom)}, friendlyJobError('helper exited from SIGKILL'))",
        ])
        self.assertIn('data-action="retry"', plain)
        self.assertIn('data-action="retry-smaller"', smaller)

    def test_a_missing_input_file_reopens_the_job_instead_of_retrying(self):
        # Fleet 4.17.x: 11 input_missing failures on 3 installs, each re-tried
        # as is and failing the same way (the file is still gone).
        lines = [
            "The reference image is no longer on disk: /Users/someone/Desktop/a.png. "
            "It was moved, renamed or deleted after it was picked.",
            "Image mode on H3 needs a reference image \u2014 pick one, or switch to Text mode.",
            "LoRA file not found: /Users/someone/pinokio/api/phosphene.git/mlx_models/loras/x.safetensors",
            "audio file not found: /Users/someone/Music/take3.wav",
        ]
        calls = []
        for i, raw in enumerate(lines):
            job = {"id": f"m{i}", "status": "failed", "error": raw}
            calls.append(f"(() => {{ const f = friendlyJobError({json.dumps(raw)}); "
                         f"return [f, nowCardFailureActions({json.dumps(job)}, f)]; }})()")
        model = "X can't run: the LTX-2.5 Q4 model is incomplete. Missing 2 file(s) in /m: a, b."
        calls.append(f"friendlyJobError({json.dumps(model)})")
        out = _node(calls)
        for raw, (fr, html) in zip(lines, out[:-1]):
            self.assertEqual(fr.get("action"), "reopen", raw)
            self.assertIn('data-action="reopen"', html, raw)
            self.assertNotIn('data-action="retry"', html, raw)
            self.assertNotIn("/Users/", fr["hint"], raw)
        self.assertEqual(out[-1].get("action"), "models")   # weights stay Open Models

    def test_reopen_click_is_wired(self):
        main = (ROOT / "webapp" / "js" / "main.js").read_text(encoding="utf-8")
        self.assertIn('[data-action="reopen"]', main)
        self.assertIn("btn.dataset.action === 'reopen'", main)

    def test_split_reopens_the_failed_job_not_the_current_form(self):
        # Codex 4.17.3: splitting "whatever the form holds now" would queue a
        # different song after the user moved on, or fail after a reload. The
        # button reopens THE REFUSED JOB's own recipe by id.
        if NODE is None:
            self.skipTest("node not on PATH")
        src = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
        fn = extract_function("openFailedJobInForm", src)
        script = (
            "const loaded = [];\n"
            "function loadParams(r) { loaded.push(r.params); }\n"
            "globalThis.LAST_STATUS = { history: [\n"
            "  { id: 'newer', params: { mode: 't2v', prompt: 'other' } },\n"
            "  { id: 'j9', params: { mode: 'a2v', audio: '/u/song_a.wav', frames: 313 } } ] };\n"
            + fn + "\n"
            "const ok = openFailedJobInForm('j9');\n"
            "const missing = openFailedJobInForm('gone');\n"
            "console.log(JSON.stringify([ok, missing, loaded]));\n")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(script)
            path = fh.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True,
                               errors="replace", timeout=60)
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        ok, missing, loaded = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertTrue(ok)
        self.assertFalse(missing)
        self.assertEqual(loaded, [{"mode": "a2v", "audio": "/u/song_a.wav", "frames": 313}])

    def test_split_click_is_wired(self):
        main = (ROOT / "webapp" / "js" / "main.js").read_text(encoding="utf-8")
        self.assertIn('[data-action="split"]', main)
        self.assertIn("openFailedJobInForm(id)", main)
        q = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
        pub = q[q.index("Object.assign(globalThis, {"):]
        self.assertIn("openFailedJobInForm,", pub[:4000])   # published for main.js


if __name__ == "__main__":
    unittest.main()
