#!/usr/bin/env python3
"""`importH3Lora()` RUN, not grepped.

`scripts/extract_panel_js.py` exists precisely so the panel's client functions
can be executed in node against a DOM shim — the module's own docstring says a
test that passes by finding strings is worse than no test, because it also
reports that the area is covered. The import control shipped with two such
tests (`test_h3_lora_manual_import`, `test_picker_markup_has_a_real_import_control`)
and no execution. This is the executable half: the real function body, driven
against a stub fetch, asserting what the user is actually told.
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
STATE = Path(tempfile.mkdtemp(prefix="phos-h3-import-ui-"))
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8300")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from extract_panel_js import attr, extract_element, extract_function  # noqa: E402

import mlx_ltx_panel as P  # noqa: E402

NODE = shutil.which("node")
SOURCE = P.page()

DOM_SHIM = r"""
const _els = {};
function _mk(id, props) {
  const el = Object.assign({
    id, hidden: false, disabled: false, textContent: '', value: '',
    click(){ this.clicked = (this.clicked || 0) + 1; },
  }, props || {});
  _els[id] = el;
  return el;
}
_mk('h3LoraImportBtn', {textContent: 'Import H3 LoRA',
                        innerHTML: '<svg></svg>Import H3 LoRA'});
_mk('h3LoraImportFile');
global.document = { getElementById: (id) => _els[id] || null };
global.FormData = class { constructor(){ this.parts = []; }
                          append(k, v, n){ this.parts.push([k, n]); } };
global._alerts = [];
global.alert = (m) => global._alerts.push(String(m));
global._refreshed = 0;
global.refreshLoras = async () => { global._refreshed++; };
global._btnStates = [];
"""


def run_node(script: str) -> dict:
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
    proc = subprocess.run([NODE, "-e", script], capture_output=True,
                          text=True, timeout=30)
    if proc.returncode:
        raise AssertionError(f"node failed:\n{proc.stdout}\n{proc.stderr}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def drive(fetch_js: str, file_js: str) -> dict:
    script = "\n".join([
        DOM_SHIM,
        f"global.fetch = {fetch_js};",
        extract_function("importH3Lora", SOURCE),
        f"""
        (async () => {{
          const btn = document.getElementById('h3LoraImportBtn');
          await importH3Lora({file_js});
          console.log(JSON.stringify({{
            alerts: global._alerts,
            refreshed: global._refreshed,
            fetched: global._fetched || null,
            btnDisabled: btn.disabled,
            btnLabel: btn.textContent,
            btnHTML: btn.innerHTML,
          }}));
        }})();
        """,
    ])
    return run_node(script)


def ok_fetch(payload: dict) -> str:
    return ("async (url, init) => { global._fetched = {url, method: init.method}; "
            "return {ok: true, status: 200, json: async () => (" 
            + json.dumps(payload) + ")}; }")


class TestImportH3LoraClient(unittest.TestCase):
    def test_a_non_safetensors_file_never_reaches_the_network(self):
        out = drive(ok_fetch({"ok": True}), "{name: 'adapter.zip', size: 10}")
        self.assertIsNone(out["fetched"])
        self.assertEqual(len(out["alerts"]), 1)
        self.assertIn(".safetensors", out["alerts"][0])

    def test_a_successful_import_posts_to_the_documented_route(self):
        out = drive(
            ok_fetch({"ok": True, "filename": "a.safetensors", "pairs": 208,
                      "converted": False, "recommended_strength": 1.0}),
            "{name: 'a.safetensors', size: 999}")
        self.assertEqual(out["fetched"], {"url": "/h3/loras/import",
                                          "method": "POST"})
        self.assertEqual(out["refreshed"], 1)
        self.assertIn("208 module pairs", out["alerts"][0])

    def test_one_pair_is_not_reported_as_1_module_pairs(self):
        out = drive(
            ok_fetch({"ok": True, "filename": "a.safetensors", "pairs": 1,
                      "converted": False, "recommended_strength": 1.0}),
            "{name: 'a.safetensors', size: 999}")
        self.assertIn("1 module pair)", out["alerts"][0])
        self.assertNotIn("1 module pairs", out["alerts"][0])

    def test_a_non_unit_scale_is_told_to_the_user_not_just_the_sidecar(self):
        """The H3 loader applies no alpha, so the strength control is where the
        adapter's own scale gets applied. Burying the number in a JSON file
        next to the weights would make the import technically correct and
        practically useless."""
        out = drive(
            ok_fetch({"ok": True, "filename": "a.safetensors", "pairs": 208,
                      "converted": True, "recommended_strength": 0.0625}),
            "{name: 'a.safetensors', size: 999}")
        self.assertIn("0.0625", out["alerts"][0])
        self.assertIn("Key namespace converted safely", out["alerts"][0])

    def test_a_unit_scale_does_not_clutter_the_message(self):
        out = drive(
            ok_fetch({"ok": True, "filename": "a.safetensors", "pairs": 2,
                      "converted": False, "recommended_strength": 1.0}),
            "{name: 'a.safetensors', size: 999}")
        self.assertNotIn("Recommended strength", out["alerts"][0])

    def test_a_server_refusal_is_shown_verbatim_and_the_button_recovers(self):
        fetch_js = ("async () => ({ok: false, status: 400, json: async () => "
                    "({ok: false, error: 'my-adapter.safetensors has unmatched "
                    "H3 LoRA tensors.'})})")
        out = drive(fetch_js, "{name: 'my-adapter.safetensors', size: 999}")
        self.assertIn("my-adapter.safetensors has unmatched", out["alerts"][0])
        self.assertEqual(out["refreshed"], 0)
        self.assertFalse(out["btnDisabled"])
        self.assertIn("Import H3 LoRA", out["btnHTML"])

    def test_a_thrown_network_error_still_restores_the_button(self):
        out = drive("async () => { throw new Error('offline'); }",
                    "{name: 'a.safetensors', size: 999}")
        self.assertIn("offline", out["alerts"][0])
        self.assertFalse(out["btnDisabled"])
        self.assertIn("Import H3 LoRA", out["btnHTML"])

    def test_the_buttons_icon_survives_an_import(self):
        """`textContent = original` would restore the label and silently drop
        the inline <svg>, so the icon vanishes from the SECOND use onward."""
        out = drive(
            ok_fetch({"ok": True, "filename": "a.safetensors", "pairs": 2,
                      "converted": False, "recommended_strength": 1.0}),
            "{name: 'a.safetensors', size: 999}")
        self.assertIn("<svg>", out["btnHTML"])


class TestImportControlMarkup(unittest.TestCase):
    def test_the_import_button_is_secondary_to_browse_civitai(self):
        """Two --accent buttons in one header is two primaries. The row has one
        call to action; the import path is for people who already have a file."""
        markup = extract_element("h3LoraImportBtn", SOURCE)
        classes = (attr(markup, "class") or "").split()
        self.assertIn("loras-browse-btn", classes)
        self.assertIn("is-ghost", classes)
        self.assertIn(".loras-summary .loras-browse-btn.is-ghost", SOURCE)

    def test_the_file_input_accepts_only_safetensors_and_clears_itself(self):
        markup = extract_element("h3LoraImportFile", SOURCE)
        self.assertIn(".safetensors", attr(markup, "accept") or "")
        # Without the reset, picking the same file twice is a silent no-op.
        self.assertIn("this.value = ''", attr(markup, "onchange") or "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
