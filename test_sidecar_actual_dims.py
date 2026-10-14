"""VC-19: the info modal's "Format" line reports what the FILE actually is,
not just the render request that produced it.

WHAT THIS GUARDS. renderOutputInfoBody() built the "Format" line from
params.width/params.height alone — the RENDER canvas, not the delivered
file. Balanced's default 720p-fit export (or any upscale) changes the two:
a 1024x576 render that fit-exports to 1280x720 reported "Format 1024 x
576" for a file that actually opens at 1280x720.

Fixed at the source: GET /sidecar (panel/routes_files.py) now probes the
REAL media file with ffprobe and attaches actual_width/actual_height to
the JSON it returns (one probe, only when a human opens the info modal -
never on the gallery's poll path). The client (queue.js) prefers those
fields, and when they disagree with the render params says both:
"Delivered 1280 x 720 (rendered 1024 x 576, 720p fit)".
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
from urllib.parse import urlencode, urlparse

ROOT = Path(__file__).resolve().parent
STATE = Path(tempfile.mkdtemp(prefix="phos-sidecar-dims-"))
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["LTX_OUTPUT_DIR"] = str(STATE / "out")
os.environ["LTX_UPLOADS_DIR"] = str(STATE / "up")
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8320")
(STATE / "out").mkdir(parents=True, exist_ok=True)
(STATE / "up").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mlx_ltx_panel as P  # noqa: E402
from panel.routes import GET_ROUTES  # noqa: E402
from extract_panel_js import extract_function  # noqa: E402

NODE = shutil.which("node")
FFMPEG = shutil.which("ffmpeg")


class _H:
    def __init__(s):
        s.out = None
        s.code = None

    def _json(s, obj, code=200):
        s.out, s.code = obj, code

    def send_error(s, code, msg=None):
        s.code = code


def _get_sidecar(path: str):
    h = _H()
    parsed = urlparse("/sidecar?" + urlencode({"path": path}))
    GET_ROUTES["/sidecar"](h, parsed)
    return h.code, h.out


@unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
class RouteProbesTheRealFile(unittest.TestCase):
    # KNOWN TRAP (conftest.py's own docstring): mlx_ltx_panel resolves
    # P.OUTPUT/P.UPLOADS from LTX_OUTPUT_DIR/LTX_UPLOADS_DIR ONCE, at first
    # import. In a whole-suite run some earlier module has usually already
    # imported the panel (fixing those constants to ITS OWN temp dirs), so
    # writing fixtures under THIS file's own `STATE / "out"` lands outside
    # whatever P.OUTPUT actually resolved to — the /sidecar route's own
    # root check then 404s a file that is really on disk. Fixtures go under
    # the panel's OWN resolved P.OUTPUT/P.UPLOADS instead, which is correct
    # regardless of import order.
    def setUp(self):
        P.OUTPUT.mkdir(parents=True, exist_ok=True)
        P.UPLOADS.mkdir(parents=True, exist_ok=True)

    def _make_mp4(self, w: int, h: int) -> Path:
        p = Path(P.OUTPUT / f"clip_{w}x{h}_{id(self)}.mp4")
        subprocess.run(
            [FFMPEG, "-y", "-f", "lavfi", "-i", f"color=c=blue:s={w}x{h}:d=1",
             "-frames:v", "1", str(p)],
            capture_output=True, timeout=30, check=True,
        )
        return p

    def test_dims_differ_from_render_params_are_both_reported(self):
        clip = self._make_mp4(1280, 720)  # delivered (post fit_720p export)
        sidecar = Path(str(clip) + ".json")
        sidecar.write_text(json.dumps({
            "params": {"mode": "t2v", "width": 1024, "height": 576,
                      "upscale": "fit_720p", "seed": "-1"},
        }))
        code, out = _get_sidecar(str(clip))
        self.assertEqual(code, 200)
        self.assertEqual(out["actual_width"], 1280)
        self.assertEqual(out["actual_height"], 720)
        # the render params are untouched - only a new field was added
        self.assertEqual(out["params"]["width"], 1024)

    def test_matching_dims_still_reported(self):
        clip = self._make_mp4(640, 480)
        sidecar = Path(str(clip) + ".json")
        sidecar.write_text(json.dumps({
            "params": {"mode": "t2v", "width": 640, "height": 480, "upscale": "off"},
        }))
        code, out = _get_sidecar(str(clip))
        self.assertEqual(code, 200)
        self.assertEqual(out["actual_width"], 640)
        self.assertEqual(out["actual_height"], 480)

    def test_non_mp4_sidecars_are_untouched(self):
        """Images (.png/.webp/...) go through the same route - never run
        ffprobe against them, and never claim a bogus actual_width."""
        img = Path(P.UPLOADS / "still.png")
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 16)
        sidecar = Path(str(img) + ".json")
        sidecar.write_text(json.dumps({"params": {"mode": "image", "width": 512}}))
        code, out = _get_sidecar(str(img))
        self.assertEqual(code, 200)
        self.assertNotIn("actual_width", out)
        self.assertEqual(out["params"]["width"], 512)


class ClientFormatsBothNumbers(unittest.TestCase):
    def _snippet(self) -> str:
        src = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
        i = src.index("Compose the dimensions + duration")
        i = src.rindex("\n", 0, i) + 1
        j = src.index("\n\n  let html", i)
        return src[i:j]

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

    def test_delivered_and_rendered_both_shown_for_a_fit_export(self):
        snippet = self._snippet()
        script = f"""
const p = {{ width: 1024, height: 576, upscale: 'fit_720p' }};
const data = {{ actual_width: 1280, actual_height: 720, video_duration_sec: 5.0, fps: 24 }};
{snippet}
console.log(JSON.stringify({{ line: formatBits[0] }}));
"""
        out = self._run_node(script)
        self.assertIn("Delivered 1280 × 720", out["line"])
        self.assertIn("rendered 1024 × 576", out["line"])
        self.assertIn("720p fit", out["line"])

    def test_matching_dims_show_once(self):
        snippet = self._snippet()
        script = f"""
const p = {{ width: 640, height: 480, upscale: 'off' }};
const data = {{ actual_width: 640, actual_height: 480 }};
{snippet}
console.log(JSON.stringify({{ line: formatBits[0] }}));
"""
        out = self._run_node(script)
        self.assertEqual(out["line"], "640 × 480")
        self.assertNotIn("Delivered", out["line"])
        self.assertNotIn("rendered", out["line"])

    def test_no_actual_dims_falls_back_to_render_params(self):
        snippet = self._snippet()
        script = f"""
const p = {{ width: 800, height: 450, upscale: 'off' }};
const data = {{}};
{snippet}
console.log(JSON.stringify({{ line: formatBits[0] }}));
"""
        out = self._run_node(script)
        self.assertEqual(out["line"], "800 × 450")


if __name__ == "__main__":
    unittest.main()
