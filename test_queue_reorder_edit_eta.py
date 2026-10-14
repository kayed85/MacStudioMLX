#!/usr/bin/env python3
"""VC-26 [P2] [UX] The queue can't manage a series: no reorder, no edit, no
per-job or total time.

Coordinator ruling, 2026-09-29: "queue reorder (drag or up/down), edit a
queued job, and an ETA per job plus a total."

Gated here:
  1  queue_move_job(): up/down/top, boundary no-ops answer ok:True
     moved:False (never a silent failure the client has to interpret), an
     unknown id is refused
  2  queue_update_job(): edits params IN PLACE — same id, same queue
     position — by re-running make_job() on the submitted form; refuses a
     job that already started rendering or finished (it's no longer in
     STATE["queue"] the moment that happens, so the lookup is the guard)
  3  job_priced_eta_sec(): reads the SAME priced tier cell (LTX_TIERS/
     H3_TIERS) the quality chips show (VC-28), returns None for anything
     that doesn't map to a known cell so the caller's average-based
     fallback still applies
  4  /status stamps eta_sec on every queued job AND keeps the aggregate
     total, both fed by the one _eta_for() that now prefers the priced
     number
  5  the two new routes are registered and answer JSON either way
  6  the client: reorder buttons are hidden exactly at the list's own
     boundaries, the badge shows "N · ~M min", edit is gated to the modes
     editQueuedJob() actually knows how to restore, and the edit banner's
     Update/Cancel wiring
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
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent
STATE = Path(tempfile.mkdtemp(prefix="phos-queueedit-"))
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8326")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mlx_ltx_panel as P  # noqa: E402
from panel.routes import POST_ROUTES  # noqa: E402
from extract_panel_js import extract_function  # noqa: E402

NODE = shutil.which("node")
QJS = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
HTML = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "webapp" / "style" / "panel.css").read_text(encoding="utf-8")


class _H:
    def __init__(self, body: str = ""):
        self.body, self.out = body, None

    def _read_form_body(self):
        from urllib.parse import parse_qs
        return self.body, parse_qs(self.body)

    def _json(self, obj, code=200):
        self.out = (code, obj)


class _Env(unittest.TestCase):
    def setUp(self):
        self._saved_persist = P.persist_queue
        P.persist_queue = lambda: None
        with P.QUEUE_COND:
            self._queue_before = list(P.STATE["queue"])
            P.STATE["queue"] = []

    def tearDown(self):
        P.persist_queue = self._saved_persist
        with P.QUEUE_COND:
            P.STATE["queue"] = self._queue_before
            P.STATE["current"] = None

    def _job(self, prompt="p", quality="balanced", frames=121):
        return P.make_job({"mode": ["t2v"], "prompt": [prompt],
                           "quality": [quality], "frames": [str(frames)]})

    def _seed(self, n=3):
        jobs = [self._job(f"p{i}") for i in range(n)]
        with P.QUEUE_COND:
            P.STATE["queue"] = jobs
        return [j["id"] for j in jobs]


# ---------------------------------------------------------------- 1
class TestQueueMoveJob(_Env):
    def test_up_swaps_with_the_previous_row(self):
        ids = self._seed(3)
        r = P.queue_move_job(ids[2], "up")
        self.assertEqual(r, {"ok": True, "moved": True})
        with P.QUEUE_COND:
            self.assertEqual([j["id"] for j in P.STATE["queue"]], [ids[0], ids[2], ids[1]])

    def test_down_swaps_with_the_next_row(self):
        ids = self._seed(3)
        P.queue_move_job(ids[0], "down")
        with P.QUEUE_COND:
            self.assertEqual([j["id"] for j in P.STATE["queue"]], [ids[1], ids[0], ids[2]])

    def test_top_moves_to_the_front(self):
        ids = self._seed(3)
        P.queue_move_job(ids[2], "top")
        with P.QUEUE_COND:
            self.assertEqual([j["id"] for j in P.STATE["queue"]][0], ids[2])

    def test_boundary_moves_are_a_no_op_not_a_failure(self):
        ids = self._seed(3)
        self.assertEqual(P.queue_move_job(ids[0], "up"), {"ok": True, "moved": False})
        self.assertEqual(P.queue_move_job(ids[2], "down"), {"ok": True, "moved": False})
        self.assertEqual(P.queue_move_job(ids[0], "top"), {"ok": True, "moved": False})

    def test_unknown_id_is_refused(self):
        self._seed(2)
        r = P.queue_move_job("nope", "up")
        self.assertFalse(r["ok"])

    def test_bad_direction_is_refused(self):
        ids = self._seed(1)
        r = P.queue_move_job(ids[0], "sideways")
        self.assertFalse(r["ok"])


# ---------------------------------------------------------------- 2
class TestQueueUpdateJob(_Env):
    def test_edits_params_in_place_same_id_same_position(self):
        ids = self._seed(3)
        r = P.queue_update_job(ids[1], {"mode": ["t2v"], "prompt": ["changed"],
                                        "quality": ["standard"], "frames": ["121"]})
        self.assertEqual(r, {"ok": True, "id": ids[1]})
        with P.QUEUE_COND:
            q = P.STATE["queue"]
            self.assertEqual([j["id"] for j in q], ids)   # position unchanged
            job = next(j for j in q if j["id"] == ids[1])
        self.assertEqual(job["params"]["prompt"], "changed")
        self.assertEqual(job["params"]["quality"], "standard")

    def test_refuses_a_job_that_already_started(self):
        ids = self._seed(1)
        with P.QUEUE_COND:
            job = P.STATE["queue"].pop(0)
            P.STATE["current"] = job
        r = P.queue_update_job(ids[0], {"mode": ["t2v"], "prompt": ["x"]})
        self.assertFalse(r["ok"])
        self.assertIn("no longer queued", r["error"])

    def test_refuses_an_unknown_id(self):
        self._seed(1)
        r = P.queue_update_job("nope", {"mode": ["t2v"], "prompt": ["x"]})
        self.assertFalse(r["ok"])

    def test_goes_through_make_job_validation(self):
        # A character request error from make_job surfaces as a refusal,
        # not an unhandled exception — same contract queue_face_fix etc.
        # already follow for CharacterRequestError.
        ids = self._seed(1)
        old = P.make_job

        def fake(form):
            raise P.CharacterRequestError("bad character")
        P.make_job = fake
        try:
            r = P.queue_update_job(ids[0], {"mode": ["t2v"]})
        finally:
            P.make_job = old
        self.assertFalse(r["ok"])
        self.assertIn("bad character", r["error"])


# ---------------------------------------------------------------- 3
class TestJobPricedEtaSec(unittest.TestCase):
    def test_prices_a_known_ltx_cell(self):
        sec = P.job_priced_eta_sec({"mode": "t2v", "quality": "balanced", "frames": 121})
        self.assertIsNotNone(sec)
        cell = P.LTX_TIERS["balanced_5s"]
        self.assertAlmostEqual(sec, cell["eta_min"] * 60.0, places=3)

    def test_none_for_image_train_upscale_sharp_export(self):
        for mode in ("image", "train", "upscale", "sharp_export"):
            self.assertIsNone(P.job_priced_eta_sec({"mode": mode}), mode)

    def test_none_for_music(self):
        self.assertIsNone(P.job_priced_eta_sec({"mode": "t2v", "engine": "music"}))

    def test_none_for_an_off_axis_frame_count(self):
        self.assertIsNone(P.job_priced_eta_sec(
            {"mode": "t2v", "quality": "balanced", "frames": 999}))

    def test_h3_lane_prices_from_h3_tiers(self):
        # Pick any real, offered H3 cell rather than hardcoding one that
        # might not exist on every registry shape.
        cell_key = next((k for k, c in P.H3_TIERS.items() if c.get("eta_min")), None)
        if cell_key is None:
            self.skipTest("no priced H3 cell in this registry")
        q, ln = cell_key.split("_", 1)
        sec = P.job_priced_eta_sec({"mode": "h3", "engine": "h3",
                                    "h3_quality": q, "h3_length": ln})
        self.assertIsNotNone(sec)


# ---------------------------------------------------------------- 4
class TestStatusStampsEta(_Env):
    def test_status_route_stamps_per_job_and_total(self):
        self._seed(2)
        from urllib.parse import urlparse
        from panel.routes import GET_ROUTES
        h = _H()
        GET_ROUTES["/status"](h, urlparse("/status"))
        code, payload = h.out
        self.assertEqual(code, 200)
        self.assertEqual(len(payload["queue"]), 2)
        for j in payload["queue"]:
            self.assertIn("eta_sec", j)
            self.assertGreater(j["eta_sec"], 0)
        self.assertEqual(payload["eta_sec"],
                         round(sum(j["eta_sec"] for j in payload["queue"])))


# ---------------------------------------------------------------- 5
class TestRoutesRegistered(_Env):
    def test_reorder_route_answers_json(self):
        self.assertIn("/queue/reorder", POST_ROUTES)
        ids = self._seed(2)
        h = _H(urlencode({"id": ids[1], "direction": "up"}))
        POST_ROUTES["/queue/reorder"](h, "/queue/reorder", {}, "")
        self.assertEqual(h.out[0], 200)
        self.assertTrue(h.out[1]["ok"])
        h = _H(urlencode({"id": "", "direction": "up"}))
        POST_ROUTES["/queue/reorder"](h, "/queue/reorder", {}, "")
        self.assertEqual(h.out[0], 400)

    def test_update_route_answers_json(self):
        self.assertIn("/queue/update", POST_ROUTES)
        ids = self._seed(1)
        h = _H(urlencode({"id": ids[0], "mode": "t2v", "prompt": "new",
                          "quality": "balanced", "frames": "121"}))
        POST_ROUTES["/queue/update"](h, "/queue/update", {}, "")
        self.assertEqual(h.out[0], 200)
        self.assertTrue(h.out[1]["ok"])
        h = _H(urlencode({"id": "nope", "mode": "t2v"}))
        POST_ROUTES["/queue/update"](h, "/queue/update", {}, "")
        self.assertEqual(h.out[0], 400)


# ---------------------------------------------------------------- 6
class TestClientMarkup(unittest.TestCase):
    def test_reorder_buttons_hidden_at_boundaries(self):
        fn = extract_function("renderCarousel", QJS)
        # renderCarousel doesn't build the queue rows (that's inline in
        # poll()); grep poll() itself for the guard instead.
        self.assertIn("i > 0", QJS)
        self.assertIn("i < s.queue.length - 1", QJS)

    def test_badge_shows_count_and_time(self):
        i = QJS.index("const qb = document.getElementById('queueBadge')")
        block = QJS[i:QJS.index("qb.style.display = ''", i) + 40]
        self.assertIn("fmtEtaCompact", block)
        self.assertIn("s.eta_sec", block)

    def test_editable_gate_excludes_batch_image_music(self):
        self.assertIn("_EDITABLE_QUEUE_MODES", QJS)
        fn = extract_function("editQueuedJob", QJS)
        self.assertIn("p.take", fn)
        self.assertIn("p.engine === 'music'", fn)

    def test_update_and_cancel_functions_exist(self):
        for fn in ("reorderQueuedJob", "editQueuedJob", "cancelEditingQueuedJob", "updateQueuedJob"):
            self.assertIn(f"function {fn}(", QJS, fn)
            self.assertIn(fn, QJS[QJS.index("Object.assign(globalThis"):], fn)

    def test_edit_banner_markup_wired_to_the_functions(self):
        i = HTML.index('id="queueEditBanner"')
        blk = HTML[i:i + 500]
        self.assertIn("updateQueuedJob()", blk)
        self.assertIn("cancelEditingQueuedJob()", blk)

    def test_css_for_new_elements_exists(self):
        for cls in ("#queueList li", ".q-eta", ".q-reorder", ".q-row-editable",
                    ".queue-edit-banner"):
            self.assertIn(cls, CSS, cls)


if __name__ == "__main__":
    unittest.main()
