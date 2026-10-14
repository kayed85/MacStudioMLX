"""4.17.3 fleet fix: a damaged JSON file beside the weights is caught and
named, instead of failing render after render with engine text.

THE BUG (fleet, one fresh 4.17.1 install, M5 Max 64 GB): weights_check "ok",
then five failed renders in a row — "No usable tokenizer beside the text
encoder", "Invalid json header length", "Received 1 parameters not in model",
"Expecting value: line 1 column 1 (char 0)" — until a re-download fixed it.
All four are a damaged or partial SMALL file (config / tokenizer / manifest)
next to intact weights. The integrity scan only parsed .safetensors headers,
so the red Repair banner never appeared, and the Now card's message for each
was the raw engine line with "Retry" as the only action.

THE FIX, two layers:
  1. _model_integrity() parses every JSON file a pack ships (declared, or
     downloaded via its own `*.json` include pattern) and puts the broken
     ones in the same bad[] the banner and /models/repair consume.
  2. friendlyJobError() names those engine lines as a damaged model file and
     points at Repair (action: 'models'), not Retry.
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
from unittest import mock

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")
os.environ.setdefault("PHOSPHENE_DISABLE_VERSION_CHECK", "1")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mlx_ltx_panel as P  # noqa: E402
from extract_panel_js import extract_function  # noqa: E402

NODE = shutil.which("node")

# Verbatim (paths redacted) from the fleet's render_failed signatures.
FLEET_LINES = [
    "No usable tokenizer beside the text encoder at '/Users/someone/pinokio/api/phosphene.git/mlx_models/gemma4-12b-ltx25-q4' "
    "(ValueError: Couldn't instantiate the backend tokenizer from one of: ...)",
    "[load_safetensors] Invalid json header length file /Users/someone/pinokio/api/phosphene.git/mlx_models/ltx-2.5-mlx-q4/connector.safetensors",
    "Received 1 parameters not in model: keyframes_abs_pos_embedding.",
    "Expecting value: line 1 column 1 (char 0)",
]


def _fake_pack(tmp: Path) -> Path:
    d = tmp / "ltx-2.5-mlx-q4"
    d.mkdir()
    # A structurally valid (empty-tensor) safetensors file so the header scan passes.
    hdr = json.dumps({"__metadata__": {}}).encode()
    (d / "transformer-distilled.safetensors").write_bytes(len(hdr).to_bytes(8, "little") + hdr)
    (d / "config.json").write_text('{"ok": true}')
    (d / "embedded_config.json").write_text("")             # empty: interrupted download
    (d / "split_model.json").write_text('{"cut off mid-')   # truncated
    (d / "._config.json").write_bytes(b"\x00\x05\x16\x07")  # AppleDouble twin — not ours
    return d


class IntegrityScanReadsJson(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="phos-json-integ-"))
        self.pack = _fake_pack(self.tmp)
        self.repo = {"key": "q4_25", "local_dir": "mlx_models/ltx-2.5-mlx-q4",
                     "files": ["transformer-distilled.safetensors"],
                     "download_include": ["*.json", "transformer-distilled.safetensors"]}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _scan(self):
        snap = [{"key": "q4_25", "complete": True, "location": str(self.pack),
                 "local_dir": self.repo["local_dir"]}]
        with mock.patch.object(P, "repo_status_list", return_value=snap), \
             mock.patch.object(P, "_repos", return_value=[self.repo]), \
             mock.patch.object(P, "_placement_errors", return_value=[]), \
             mock.patch.object(P, "_output_codec_report", return_value={"ok": True}):
            return P._model_integrity(force=True)

    def test_damaged_json_lands_in_bad(self):
        res = self._scan()
        bad = {b["file"]: b["reason"] for b in res["bad"]}
        self.assertFalse(res["ok"])
        self.assertIn("embedded_config.json", bad)
        self.assertIn("empty", bad["embedded_config.json"])
        self.assertIn("split_model.json", bad)
        self.assertIn("not valid JSON", bad["split_model.json"])
        self.assertNotIn("config.json", bad)
        self.assertNotIn("._config.json", bad)
        for b in res["bad"]:
            self.assertEqual(b["repo"], "q4_25")   # Repair targets the right pack

    def test_a_healthy_pack_stays_ok(self):
        (self.pack / "embedded_config.json").write_text('{"a": 1}')
        (self.pack / "split_model.json").write_text("[]")
        self.assertTrue(self._scan()["ok"])

    def test_no_star_json_pattern_checks_declared_files_only(self):
        self.repo = {"key": "q4_25", "local_dir": "x",
                     "files": ["transformer-distilled.safetensors", "config.json"]}
        names = P._json_files_for_repo(self.repo, self.pack)
        self.assertEqual(names, ["config.json"])


class FriendlyErrorNamesTheDamagedFile(unittest.TestCase):
    def test_fleet_lines_point_at_repair(self):
        if NODE is None:
            self.skipTest("node not on PATH")
        src = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
        fn = (extract_function("_redactLocalPaths", src) + "\n"
              + extract_function("friendlyJobError", src))
        calls = ",\n  ".join(f"friendlyJobError({json.dumps(r)})" for r in FLEET_LINES)
        script = fn + f"\nconsole.log(JSON.stringify([\n  {calls}\n]));"
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(script)
            path = fh.name
        try:
            r = subprocess.run([NODE, path], capture_output=True, text=True,
                               errors="replace", timeout=60)
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        out = json.loads(r.stdout.strip().splitlines()[-1])
        for raw, res in zip(FLEET_LINES, out):
            self.assertIn("damaged", res["friendly"], raw)
            self.assertEqual(res.get("action"), "models", raw)
            self.assertIn("Repair", res["hint"], raw)
            self.assertNotIn("/Users/", res.get("details", ""), raw)


if __name__ == "__main__":
    unittest.main()
