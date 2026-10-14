#!/usr/bin/env python3
"""Retake — send a timeline clip back through the renderer and get the new
take offered against it, in place.

Locked: a retake CLONES the clip's own shot (character, refs, engine) and
changes only the prompt, the length and the seed; a finished retake shows up
in the relink rows FLAGGED, is never part of the batch drafts→finals rewrite,
and is adopted one clip at a time by id; the pool carries the shot's full
prompt so the retake can start from it.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as panel                                        # noqa: E402
import storyboard                                                    # noqa: E402
import storyboard_editor as sedit                                    # noqa: E402
from test_storyboard_editor_api import FakeHandler, _board, _edit, _clip   # noqa: E402


class TheRetake(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.clipfile = self.root / "shot1.mp4"
        self.clipfile.write_bytes(b"x")
        self.newfile = self.root / "shot1_take2.mp4"
        self.board = _board([self.clipfile])
        self.board["shots"][0].update({"character_id": "bizarrotrn", "trigger": "bizarrotrn",
                                       "mode": "character", "location": "the study",
                                       "refs": ["/x/ref.png"]})
        c = _clip(str(self.clipfile), 0.0, 4.0, 0.0, id="c1")
        self.edit = sedit.normalise_edit(_edit([c]))
        self.patches = [
            mock.patch.object(panel, "STATE_DIR", self.root),
            mock.patch.object(panel, "_sbe_board_dir", return_value=self.root / "sb_t"),
            mock.patch.object(storyboard, "load_storyboard", return_value=self.board),
            mock.patch.object(storyboard, "save_storyboard"),
            mock.patch.object(panel, "_sb_enqueue", return_value="j-take"),
        ]
        for p in self.patches:
            p.start()
        (self.root / "sb_t").mkdir()
        sedit.save_edit(self.root / "sb_t", self.edit)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_the_pool_carries_the_prompt(self):
        rows = panel._sbe_board_clips(self.board)
        self.assertEqual(rows[0]["prompt"], "shot 1 happens")
        self.assertEqual(rows[0]["character_id"], "bizarrotrn")

    def test_generate_with_retake_of_clones_the_shot(self):
        h = FakeHandler()
        with mock.patch.object(panel, "_sb_known_character_ids", return_value=["bizarrotrn"]), \
                mock.patch.object(panel, "_sb_h3_available", return_value=False), \
                mock.patch.object(storyboard, "shot_to_job", return_value={"mode": "t2v", "prompt": "p"}) as stj:
            h.post("edit/generate", {"id": "sb_t", "prompt": "he turns, slower",
                                     "duration": "4", "film_start": "0",
                                     "retake_of": "c1"})
        self.assertEqual(h.status, 202, h.payload)          # queued, like any shot
        new = self.board["shots"][-1]
        self.assertEqual(new["n"], 2)
        # the trigger is prepended by the board's own normaliser, as for
        # every character shot
        self.assertIn("he turns, slower", new["prompt"])
        self.assertEqual(new["character_id"], "bizarrotrn")
        self.assertEqual(new["location"], "the study")
        self.assertEqual(new["refs"], ["/x/ref.png"])
        self.assertEqual(new["status"], "queued")
        self.assertEqual(new["edit_slot"]["retake_of"], "c1")
        self.assertNotIn("draft_output", new)
        stj.assert_called_once()

    def test_a_retake_does_not_inherit_the_sources_job_ids(self):
        # FILM-09: the source shot carries a FINISHED delivery job. The old
        # exclude list checked `k.endswith("_job")`, but the real field is
        # `final_job_id` — which does NOT end in "_job" — so it rode along
        # onto the "new" retake shot, and the reconciler folded the OLD
        # final clip back onto it: "Retake (Delivery)" silently handed back
        # the same clip it started from.
        self.board["shots"][0].update({
            "draft_job_id": "j-old-draft", "final_job_id": "j-old-final",
            "still_job_id": "j-old-still", "error": "a stale error",
            "lipsync": {"score": 0.9, "attempts": 1, "kept": "/x/old.mp4"},
        })
        h = FakeHandler()
        with mock.patch.object(panel, "_sb_known_character_ids", return_value=["bizarrotrn"]), \
                mock.patch.object(panel, "_sb_h3_available", return_value=False), \
                mock.patch.object(storyboard, "shot_to_job", return_value={"mode": "t2v", "prompt": "p"}):
            h.post("edit/generate", {"id": "sb_t", "prompt": "he turns, slower",
                                     "duration": "4", "film_start": "0",
                                     "retake_of": "c1"})
        self.assertEqual(h.status, 202, h.payload)
        new = self.board["shots"][-1]
        # `final_job_id`, `still_job_id`, `error` and `lipsync` must never
        # ride along at all — the new shot has not rendered yet.
        for key in ("final_job_id", "still_job_id", "error", "lipsync"):
            self.assertNotIn(key, new, f"retake inherited the source's {key!r}")
        # `draft_job_id` IS legitimately set — by queuing THIS shot's OWN
        # job a moment later — but it must be the fresh id, never the
        # source's old one.
        self.assertEqual(new.get("draft_job_id"), "j-take")
        self.assertNotEqual(new.get("draft_job_id"), "j-old-draft")
        # the clone contract for everything else still holds
        self.assertEqual(new["character_id"], "bizarrotrn")
        self.assertEqual(new["location"], "the study")

    def test_a_retake_of_a_clip_that_left_is_refused(self):
        h = FakeHandler()
        h.post("edit/generate", {"id": "sb_t", "prompt": "x", "retake_of": "zz"})
        self.assertEqual(h.status, 400)
        self.assertIn("not on this timeline", h.payload["error"])

    def test_a_finished_retake_is_offered_flagged_and_adopted_by_id(self):
        self.newfile.write_bytes(b"y")
        self.board["shots"].append({"n": 2, "title": "take 2", "prompt": "p2",
                                    "status": "done", "draft_output": str(self.newfile),
                                    "edit_slot": {"film_start": 0.0, "duration": 4.0,
                                                  "retake_of": "c1"}})
        rows = panel._sbe_relinks(self.board, self.edit)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["retake"])
        self.assertEqual([rows[0]["id"], rows[0]["to"]], ["c1", str(self.newfile)])
        # the batch relink leaves the retake alone…
        h = FakeHandler()
        with mock.patch.object(panel, "_sbe_proxy_now"):
            h.post("edit/relink", {"id": "sb_t"})
        self.assertEqual(h.status, 200)
        self.assertEqual(sedit.load_edit(self.root / "sb_t")["clips"][0]["path"], str(self.clipfile))
        # …and `only` adopts it for that clip alone
        h = FakeHandler()
        with mock.patch.object(panel, "_sbe_proxy_now"):
            h.post("edit/relink", {"id": "sb_t", "only": "c1"})
        self.assertEqual(h.status, 200, h.payload)
        got = sedit.load_edit(self.root / "sb_t")["clips"][0]
        self.assertEqual(got["path"], str(self.newfile))
        self.assertEqual([got["start"], got["end"]], [0.0, 4.0])
        self.assertEqual(h.payload.get("relinked"), 1)
        # adopted, so no longer offered
        self.assertEqual(panel._sbe_relinks(self.board, sedit.load_edit(self.root / "sb_t")), [])


if __name__ == "__main__":
    unittest.main()
