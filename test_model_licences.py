#!/usr/bin/env python3
"""4.19.8 licence compliance: per-model licence screens, the H3 install gate,
and the H3 Turbo adapter built locally from its upstream source.

What these pin, and why each one exists:

* model_licences: which work needs which licence, that acceptance is stored
  per licence VERSION (a changed licence asks again), and that every refusal
  carries the analytics needle the panel's refusal table matches on.
* The non-commercial models' screens say "non-commercial use only"; H3's
  names the four excluded territories, carries a misuse-report link and a
  "MiniMax H3" credit (MiniMax H3 licence s. IV.2, V.4, V.5).
* scripts/pinokio/h3_licence_gate.sh: a NEW H3 install stops (one
  "FATAL error:" line, the only thing Pinokio 8.2 stops on) until the licence
  is accepted; an install that already has the weights continues; its version
  equals the panel's.
* scripts/fetch_h3_turbo.py: the re-layout is exact on a synthetic adapter
  (q/k/v fused block-diagonally, fc1 halves swapped, A/B copied, header in
  the pinned order), refuses a source whose alpha != rank, and never touches
  a file that is already installed. The REAL byte-identity proof (the full
  1.38 GB upstream file -> SHA-256 d51d626f..., the digest of the file
  Phosphene used to host) was run once at release time; it needs the network.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import model_licences as ML  # noqa: E402

STOP = re.compile(r"error:|errno ", re.I)


def _load_fetch():
    spec = importlib.util.spec_from_file_location(
        "fetch_h3_turbo_t", ROOT / "scripts" / "fetch_h3_turbo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LicenceRegistry(unittest.TestCase):
    def test_every_licence_has_its_screen(self):
        for key, lic in ML.LICENCES.items():
            info = ML.describe(tempfile.mkdtemp(), key)
            for field in ("title", "summary", "terms", "badge", "accept_label"):
                self.assertTrue(info.get(field), f"{key}.{field}")
            self.assertTrue(lic["url"].startswith("https://"), key)
            if lic["non_commercial"]:
                self.assertEqual(info["badge"], "Non-commercial use only", key)
                self.assertIn("non-commercial use only", info["accept_label"].lower(), key)

    def test_the_four_non_commercial_models(self):
        nc = sorted(k for k, v in ML.LICENCES.items() if v["non_commercial"])
        self.assertEqual(nc, ["fibo", "flux1_dev", "ideogram4", "yue2"])
        self.assertFalse(ML.LICENCES["h3"]["non_commercial"])

    def test_h3_screen_names_the_territory_credit_and_report_path(self):
        info = ML.describe(tempfile.mkdtemp(), "h3")
        for region in ("European Union", "United Kingdom", "South Korea", "United States"):
            self.assertIn(region, info["territory_notice"])
            self.assertIn(region, ML.LICENCES["h3"]["excluded_territories"])
        self.assertIn("MiniMax H3", info["credit_line"])
        self.assertTrue(info["report_url"].startswith("https://github.com/mrbizarro/Phosphene/issues/new"))
        self.assertIn("territory", info["accept_label"])

    def test_copy_is_ascii(self):
        blob = json.dumps(ML.COPY, ensure_ascii=False)
        self.assertEqual(blob, blob.encode("ascii", "ignore").decode())


class Acceptance(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp()

    def test_accept_is_stored_with_marker_and_version(self):
        self.assertFalse(ML.is_accepted(self.state, "h3"))
        rec = ML.accept(self.state, "h3", "4.19.8")
        self.assertEqual(rec["version"], ML.LICENCES["h3"]["version"])
        self.assertTrue(ML.is_accepted(self.state, "h3"))
        self.assertTrue(ML.marker_path(self.state, "h3").is_file())
        self.assertFalse(ML.is_accepted(self.state, "ideogram4"))

    def test_a_new_licence_version_asks_again(self):
        ML.accept(self.state, "yue2")
        data = json.loads((Path(self.state) / ML.STORE_NAME).read_text())
        data["yue2"]["version"] = "older"
        (Path(self.state) / ML.STORE_NAME).write_text(json.dumps(data))
        self.assertFalse(ML.is_accepted(self.state, "yue2"))

    def test_refusal_payload_and_its_analytics_needle(self):
        ref = ML.refusal(self.state, "h3")
        self.assertEqual(ref["code"], "licence_required")
        self.assertEqual(ref["licence"], "h3")
        self.assertIn("accept the licence once", ref["error"].lower())
        panel = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
        self.assertIn('("licence_required", ("accept the licence once",))', panel)
        ML.accept(self.state, "h3")
        self.assertIsNone(ML.refusal(self.state, "h3"))
        self.assertIsNone(ML.refusal(self.state, None))


class WhichLicence(unittest.TestCase):
    def test_jobs(self):
        f = ML.licence_for_job
        self.assertEqual(f({"engine": "h3", "mode": "t2v"}), "h3")
        self.assertEqual(f({"engine": "h3", "mode": "keyframe"}), "h3")
        self.assertEqual(f({"mode": "music"}), "yue2")
        self.assertEqual(f({"mode": "image", "engine_override": "ideogram4_inline"}), "ideogram4")
        self.assertIsNone(f({"mode": "t2v"}))
        self.assertIsNone(f({"mode": "image", "engine_override": "qwen_edit_inline"}))

    def test_image_configs(self):
        class C:
            def __init__(self, family, model, kind="mflux"):
                self.kind, self.mflux_family, self.mflux_model = kind, family, model
        g = ML.licence_for_image_cfg
        self.assertEqual(g(C("ideogram", "cocktailpeanut/ideogram-4-fp8")), "ideogram4")
        self.assertEqual(g(C("flux1", "krea-dev")), "flux1_dev")
        self.assertEqual(g(C("auto", "krea-dev")), "flux1_dev")       # the engine default family
        self.assertEqual(g(C("flux1", "filipstrand/FLUX.1-Krea-dev-mflux-4bit")), "flux1_dev")
        self.assertEqual(g(C("fibo", "briaai/FIBO")), "fibo")
        self.assertIsNone(g(C("flux1", "schnell")))
        self.assertIsNone(g(C("flux2", "Runpod/FLUX.2-klein-4B-mflux-4bit")))
        self.assertIsNone(g(C("qwen_edit", "Qwen/Qwen-Image-Edit-2511")))
        self.assertIsNone(g(C("", "", kind="bfl")))


class ServerWiring(unittest.TestCase):
    """Static: every door that downloads or runs a licensed model checks."""

    def test_routes(self):
        q = (ROOT / "panel" / "routes_queue.py").read_text(encoding="utf-8")
        self.assertEqual(q.count("P.job_licence_refusal("), 5)        # add, retry, batch, newtake, take/resume
        one = (ROOT / "panel" / "routes_oneshot.py").read_text(encoding="utf-8")
        self.assertIn("P.job_licence_refusal(", one)
        mus = (ROOT / "panel" / "routes_music.py").read_text(encoding="utf-8")
        self.assertEqual(mus.count('P.licence_refusal_for("yue2")'), 3)  # variation, transcribe, score
        img = (ROOT / "panel" / "routes_image.py").read_text(encoding="utf-8")
        self.assertEqual(img.count('P.licence_refusal_for("h3")'), 2)  # turbo, tristep
        self.assertIn("licence_for_image_cfg(cfg)", img)               # /image/generate
        mod = (ROOT / "panel" / "routes_models.py").read_text(encoding="utf-8")
        self.assertEqual(mod.count('P.licence_refusal_for("yue2")'), 3)
        panel = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
        body = panel[panel.index("def run_job_inner("):]
        self.assertLess(body.index("_raise_if_licence_missing("),
                        body.index("run_music_job_inner("))
        pre = panel[panel.index("def _preflight_image_job("):]
        self.assertLess(pre.index("_raise_if_licence_missing("),
                        pre.index('os.environ.get("PHOSPHENE_SKIP_PREFLIGHT")'))

    def test_the_page_carries_the_gate_and_the_h3_credit(self):
        html = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="licenceModal"', html)
        hint = html[html.index('id="h3Hint"'):]
        hint = hint[:hint.index("</div>")]
        self.assertIn("MiniMax H3", hint)
        self.assertIn("Report misuse", hint)
        main = (ROOT / "webapp" / "js" / "main.js").read_text(encoding="utf-8")
        self.assertIn("installLicenceGate();", main)
        js = (ROOT / "webapp" / "js" / "licences.js").read_text(encoding="utf-8")
        self.assertIn("r.status !== 451", js)
        self.assertIn("'licence_required'", js)


class H3InstallGate(unittest.TestCase):
    def run_gate(self, state, models):
        env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp"),
               "LTX_STATE_DIR": state, "LTX_H3_MODELS": models}
        return subprocess.run(["bash", "scripts/pinokio/h3_licence_gate.sh"], cwd=ROOT,
                              env=env, capture_output=True, text=True, errors="replace")

    def test_version_matches_the_panel(self):
        sh = (ROOT / "scripts/pinokio/h3_licence_gate.sh").read_text(encoding="utf-8")
        self.assertIn(f'H3_LICENCE_VERSION="{ML.LICENCES["h3"]["version"]}"', sh)
        js = (ROOT / "install_h3.js").read_text(encoding="utf-8")
        gate = js.index('"bash scripts/pinokio/h3_licence_gate.sh"')
        self.assertLess(gate, js.index('"bash scripts/pinokio/h3_preflight.sh"'))
        self.assertLess(gate, js.index("git clone --branch"))

    def test_new_install_stops_until_accepted(self):
        with tempfile.TemporaryDirectory() as st, tempfile.TemporaryDirectory() as mo:
            r = self.run_gate(st, mo)
            self.assertNotEqual(r.returncode, 0)
            self.assertTrue(r.stdout.strip().startswith("FATAL error: "), r.stdout)
            self.assertNotIn("errno", r.stdout.lower())
            self.assertLessEqual(len(r.stdout.strip()), 330)
            ML.accept(st, "h3")
            r = self.run_gate(st, mo)
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertFalse(STOP.search(r.stdout), r.stdout)

    def test_an_install_with_weights_continues(self):
        with tempfile.TemporaryDirectory() as st, tempfile.TemporaryDirectory() as mo:
            q8 = Path(mo) / "models" / "h3-dit-q8"
            q8.mkdir(parents=True)
            r = self.run_gate(st, mo)                    # an EMPTY folder is not an install
            self.assertNotEqual(r.returncode, 0, r.stdout)
            with open(q8 / "model-00001.safetensors", "wb") as fh:
                fh.truncate(2 << 20)
            r = self.run_gate(st, mo)
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertFalse(STOP.search(r.stdout), r.stdout)


class MusicInstallGate(unittest.TestCase):
    def run_gate(self, state, models):
        env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp"),
               "LTX_STATE_DIR": state, "LTX_MUSIC_MODELS": models}
        return subprocess.run(["bash", "scripts/pinokio/music_licence_gate.sh"], cwd=ROOT,
                              env=env, capture_output=True, text=True, errors="replace")

    def test_version_and_order(self):
        sh = (ROOT / "scripts/pinokio/music_licence_gate.sh").read_text(encoding="utf-8")
        self.assertIn(f'YUE2_LICENCE_VERSION="{ML.LICENCES["yue2"]["version"]}"', sh)
        js = (ROOT / "install_music.js").read_text(encoding="utf-8")
        self.assertLess(js.index("bash scripts/pinokio/music_licence_gate.sh"), js.index("scripts/pinokio/music_fetch.py --min"))

    def test_gate(self):
        with tempfile.TemporaryDirectory() as st, tempfile.TemporaryDirectory() as mo:
            r = self.run_gate(st, mo)
            self.assertNotEqual(r.returncode, 0)
            self.assertTrue(r.stdout.startswith("FATAL error: "), r.stdout)
            self.assertIn("non-commercial use only", r.stdout)
            self.assertNotIn("errno", r.stdout.lower())
            with open(Path(mo) / "model.safetensors", "wb") as fh:
                fh.truncate(2 << 20)
            r = self.run_gate(st, mo)
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertFalse(STOP.search(r.stdout), r.stdout)
        with tempfile.TemporaryDirectory() as st, tempfile.TemporaryDirectory() as mo:
            ML.accept(st, "yue2")
            r = self.run_gate(st, mo)
            self.assertEqual(r.returncode, 0, r.stdout)


def _synthetic_source(alpha="128", rank=128, cin=4, cout=6, ff=8, fc2in=5):
    """A tiny adapter in LightX2V's diffusers/PEFT layout (bf16 bytes)."""
    tensors = {}
    n = 0
    prefixes = ([f"transformer_blocks.{i}" for i in range(50)]
                + [f"token_refiner.refiner_blocks.{i}" for i in range(2)])
    shapes = {}
    for p in prefixes:
        for proj in ("to_q", "to_k", "to_v"):
            shapes[f"{p}.attn.{proj}"] = ([rank, cin], [cout, rank])
        shapes[f"{p}.attn.to_out.0"] = ([rank, cout], [cin, rank])
        shapes[f"{p}.ff.net.0.proj"] = ([rank, cin], [2 * ff, rank])
        shapes[f"{p}.ff.net.2"] = ([rank, fc2in], [cin, rank])
    header, blobs, off = {"__metadata__": {"alpha": alpha}}, [], 0
    for mod, (sa, sb) in shapes.items():
        for suffix, shape in ((".lora_A.default.weight", sa), (".lora_B.default.weight", sb)):
            count = shape[0] * shape[1]
            data = struct.pack(f"<{count}H", *(((n + i) * 7919) & 0xFFFF for i in range(count)))
            n += count
            header[mod + suffix] = {"dtype": "BF16", "shape": shape,
                                    "data_offsets": [off, off + len(data)]}
            blobs.append(data)
            off += len(data)
    h = json.dumps(header).encode()
    raw = struct.pack("<Q", len(h)) + h + b"".join(blobs)
    return raw, header


class TurboRelayout(unittest.TestCase):
    def setUp(self):
        self.mod = _load_fetch()

    def convert(self, raw):
        out = io.BytesIO()
        self.mod.convert(io.BytesIO(raw), out.write)
        b = out.getvalue()
        (n,) = struct.unpack("<Q", b[:8])
        hdr = json.loads(b[8:8 + n])
        return hdr, b[8 + n:]

    def test_relayout_is_exact(self):
        raw, src = _synthetic_source()
        base = 8 + struct.unpack("<Q", raw[:8])[0]

        def s(key):
            a, b = src[key]["data_offsets"]
            return raw[base + a:base + b]

        hdr, data = self.convert(raw)
        meta = hdr.pop("__metadata__")
        self.assertEqual(meta, self.mod.METADATA)
        self.assertEqual(len(hdr), 416)

        def o(key):
            a, b = hdr[key]["data_offsets"]
            return data[a:b]

        # header order on disk follows DATA_ORDER
        keys = self.mod.output_keys()
        by_off = sorted(hdr, key=lambda k: hdr[k]["data_offsets"][0])
        self.assertEqual(by_off, [keys[i] for i in self.mod.DATA_ORDER])
        p, d = "transformer_blocks.7", "blocks.7"
        A, B = ".lora_A.default.weight", ".lora_B.default.weight"
        # q/k/v: A stacked, B block-diagonal
        self.assertEqual(o(d + ".attn.qkv_proj.lora_A.weight"),
                         s(p + ".attn.to_q" + A) + s(p + ".attn.to_k" + A) + s(p + ".attn.to_v" + A))
        qkvb = o(d + ".attn.qkv_proj.lora_B.weight")
        self.assertEqual(hdr[d + ".attn.qkv_proj.lora_B.weight"]["shape"], [18, 384])
        row = 384 * 2
        z = bytes(256)
        kb = s(p + ".attn.to_k" + B)
        self.assertEqual(qkvb[6 * row:7 * row], z + kb[:256] + z)          # first k row
        vb = s(p + ".attn.to_v" + B)
        self.assertEqual(qkvb[17 * row:], z + z + vb[-256:])               # last v row
        # fc1: [value; gate] -> [gate; value]; fc2 / out_proj copied
        fb = s(p + ".ff.net.0.proj" + B)
        self.assertEqual(o(d + ".mlp.fc1.lora_B.weight"), fb[len(fb) // 2:] + fb[:len(fb) // 2])
        self.assertEqual(o(d + ".mlp.fc2.lora_B.weight"), s(p + ".ff.net.2" + B))
        self.assertEqual(o(d + ".attn.out_proj.lora_A.weight"), s(p + ".attn.to_out.0" + A))
        self.assertEqual(o("token_refiner.blocks.1.mlp.fc2.lora_A.weight"),
                         s("token_refiner.refiner_blocks.1.ff.net.2" + A))

    def test_refuses_a_scale_other_than_one(self):
        raw, _ = _synthetic_source(alpha="8")
        with self.assertRaises(ValueError):
            self.mod.convert(io.BytesIO(raw), lambda b: None)

    def test_an_installed_adapter_is_left_alone(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / self.mod.ASSET_NAME
            with open(target, "wb") as fh:
                fh.truncate(self.mod.ASSET_BYTES)        # sparse, right size
            argv = sys.argv
            sys.argv = ["x", "--dir", d]
            try:
                from unittest import mock
                with mock.patch.object(self.mod.urllib.request, "urlopen",
                                       side_effect=AssertionError("network")):
                    self.assertEqual(self.mod.main(), 0)
            finally:
                sys.argv = argv

    def test_pins(self):
        self.assertEqual(sorted(self.mod.DATA_ORDER), list(range(416)))
        self.assertIn("/resolve/" + self.mod.SOURCE_REVISION + "/", self.mod.SOURCE_URL)
        self.assertEqual(len(self.mod.SOURCE_REVISION), 40)


if __name__ == "__main__":
    unittest.main()
