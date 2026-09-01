#!/usr/bin/env python3
"""The extracted frontend files under webapp/ — served, substituted, and gone
from the embedded page.

Slice 1 of the panel restructuring (docs/ARCHITECTURE.md) moved the page's
CSS out of the HTML template string in mlx_ltx_panel.py into
webapp/style/panel.css, served by do_GET's /webapp/ route. These receipts
pin the contract that move created:

  * the file exists on disk and is the real stylesheet, not a stub;
  * GET /webapp/style/panel.css answers 200 + text/css through the real
    Handler on a real socket;
  * the one dynamic seam — __ENGINE_RULES__ — is substituted at serve time
    from the ENGINES registry, exactly as page() substituted it when the
    CSS was embedded (the registry stays single-source server-side);
  * no <style> block remains in the HTML template, and the page links the
    external stylesheet instead;
  * the route is path-bound to <ROOT>/webapp — a literal ../ sent raw over
    the socket cannot escape it.
"""
from __future__ import annotations

import http.client
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = Path(tempfile.mkdtemp(prefix="phos-panel-assets-"))
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8307")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402

CSS_FILE = ROOT / "webapp" / "style" / "panel.css"


class PanelAssetsHTTP(unittest.TestCase):
    """The /webapp/ route through a real socket and the real Handler."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), P.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path: str):
        return urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}")

    def test_css_served_200_text_css(self) -> None:
        r = self._get("/webapp/style/panel.css")
        self.assertEqual(r.status, 200)
        self.assertTrue(r.headers["Content-Type"].startswith("text/css"))
        body = r.read().decode("utf-8")
        # The real stylesheet, not an empty file that would render the panel
        # as unstyled tag soup while every other check stayed green.
        self.assertGreater(len(body), 100_000)
        self.assertIn(":root", body)

    def test_css_not_cached_stale(self) -> None:
        # The file changes on every git pull under a running panel and the
        # link carries no cache-bust token — a cached stylesheet would ship
        # the previous release's UI against the new markup.
        r = self._get("/webapp/style/panel.css")
        self.assertEqual(r.headers["Cache-Control"], "no-cache")

    def test_engine_rules_substituted_at_serve_time(self) -> None:
        body = self._get("/webapp/style/panel.css").read().decode("utf-8")
        self.assertNotIn("__ENGINE_RULES__", body)
        # Two rules per registered engine, emitted from the ENGINES table.
        for e in P.ENGINES:
            self.assertIn(f'body[data-engine="{e["id"]}"]', body)
            self.assertIn(f'[data-{e["id"]}-only]', body)

    def test_missing_file_404(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._get("/webapp/style/nope.css")
        self.assertEqual(ctx.exception.code, 404)

    def test_literal_dotdot_cannot_escape_webapp(self) -> None:
        # urllib collapses ../ client-side, which would test nothing — send
        # the raw path over the socket so the server sees the literal bytes.
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        try:
            conn.request("GET", "/webapp/../mlx_ltx_panel.py")
            resp = conn.getresponse()
            self.assertIn(resp.status, (400, 404))
            resp.read()
        finally:
            conn.close()


class PanelCSSOnDisk(unittest.TestCase):
    def test_file_exists_and_is_the_stylesheet(self) -> None:
        self.assertTrue(CSS_FILE.is_file(), f"{CSS_FILE} missing")
        text = CSS_FILE.read_text(encoding="utf-8")
        self.assertGreater(len(text), 100_000)
        self.assertIn(":root", text)
        # The dynamic seam must be the placeholder ON DISK — a committed file
        # with the rules already baked in would freeze the engine registry at
        # whatever it was the day someone saved a served copy over it.
        self.assertIn("__ENGINE_RULES__", text)


class PanelHTMLOnDisk(unittest.TestCase):
    """Slice 2: the page template lives at webapp/index.html."""

    def test_index_html_is_the_template_the_panel_serves(self) -> None:
        index = ROOT / "webapp" / "index.html"
        self.assertTrue(index.is_file(), f"{index} missing")
        text = index.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("<!doctype html>"))
        # The substitution seams live in the FILE; page() applies them at
        # request time. A file with them already baked in would freeze the
        # bootstrap at whatever it was when someone saved a served copy.
        self.assertIn("__BOOTSTRAP__", text)
        # The panel must serve THIS file's bytes — HTML re-embedded in the
        # Python source would silently fork the page into two copies.
        self.assertEqual(P.HTML, text)


class NoEmbeddedStyleRemains(unittest.TestCase):
    def test_no_style_block_in_template(self) -> None:
        self.assertNotIn("<style", P.HTML,
                         "a <style> block crept back into the embedded page "
                         "— styles belong in webapp/style/panel.css")

    def test_page_links_external_stylesheet(self) -> None:
        page = P.page()
        self.assertIn('<link rel="stylesheet" href="/webapp/style/panel.css">',
                      page)
        self.assertNotIn("<style", page)


class TheStructureLawHolds(unittest.TestCase):
    """The two creep paths the other gates don't cover.

    The restructuring (docs/ARCHITECTURE.md) ended with every kind of code
    in exactly one home. These pins close the two ways the monolith could
    quietly grow back that no existing gate watches:
    """

    def test_the_inline_script_is_one_line_forever(self) -> None:
        # The page's only inline JS is the substitution seam. The moment a
        # second statement lands here, the 26k-line block has started
        # growing back — new JS goes in the matching webapp/js/ module,
        # run-once startup calls go in webapp/js/main.js.
        html = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
        i = html.index("<script>")
        j = html.index("</script>", i)
        inline = html[i + len("<script>"):j].strip()
        self.assertEqual(
            inline, "const BOOT = __BOOTSTRAP__;",
            "the inline <script> in index.html must stay exactly one line "
            f"(the BOOT seam). It now holds: {inline[:200]!r}")

    def test_no_markup_in_the_server_python(self) -> None:
        # The server carries no frontend any more — no <style>, no <script>.
        # A template string creeping into a route handler is the monolith's
        # other way back in.
        offenders = []
        for path in [ROOT / "mlx_ltx_panel.py",
                     *sorted((ROOT / "panel").glob("*.py"))]:
            text = path.read_text(encoding="utf-8")
            if "<style" in text or "<script" in text:
                offenders.append(path.name)
        self.assertEqual(offenders, [],
                         "frontend markup found in server Python — markup "
                         "belongs in webapp/index.html, JS in webapp/js/: "
                         f"{offenders}")


if __name__ == "__main__":
    unittest.main()
