#!/usr/bin/env python3
"""Priority-0 for the `board` mega-ship package: shots get a stable `uid`.

  * FILM-10  the board save route merged server-owned fields (draft_output,
             final_output, still, job ids, ...) from the OLD shot at the same
             `n` onto the incoming shot. `n` is renumbered on every reorder,
             insert or delete, so moving or deleting a shot next to a
             rendered one grafted that rendered clip onto a completely
             different, unrendered shot — which then flipped to "done" and
             locked, and its own prompt never rendered.

The fix gives every shot a stable `uid` (storyboard.new_shot_uid()) and
merges by it instead of by position. Old boards on disk get a uid assigned
on load (`_sb_reconcile`) and on normalize (`_sb_normalize`), migrated in
place and persisted on the next save.

Everything here runs against a scratch STATE_DIR; no GPU, no weights.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as panel                                        # noqa: E402
import storyboard                                                    # noqa: E402


def _shot(n, uid=None, **kw):
    s = {"n": n, "mode": "text", "engine": "ltx", "prompt": f"shot {n}",
         "duration_s": 5.0, "seed": 1000 + n, "refs": [], "status": "pending"}
    if uid is not None:
        s["uid"] = uid
    s.update(kw)
    return s


def _board(bid, shots):
    return {"schema": 1, "id": bid, "title": "T", "created_at": 1,
            "policy": storyboard.default_policy(), "cast": [],
            "engine_mode": "ltx", "shots": shots}


class FakeHandler:
    def __init__(self):
        self.status = None
        self.payload = None

    def _json(self, payload, status: int = 200):
        self.payload, self.status = payload, status


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.state, self.out = root / "state", root / "outputs"
        self.state.mkdir()
        self.out.mkdir()
        for p in (mock.patch.object(panel, "STATE_DIR", self.state),
                  mock.patch.object(panel, "OUTPUT", self.out),
                  mock.patch.object(panel, "push", lambda *a, **k: None)):
            p.start()
            self.addCleanup(p.stop)

    def save(self, board):
        storyboard.save_storyboard(self.state, board)
        return board

    def post_save(self, board_id, shots):
        h = FakeHandler()
        import json as _json
        body = _json.dumps({"id": board_id, "board": {"shots": shots}})
        panel.Handler._storyboard_post(h, "save", body, {})
        return h


class ReorderDoesNotGraftClips(Sandbox):
    """FILM-10: the corrupting repro from the report."""

    def test_moving_an_unrendered_shot_up_does_not_inherit_the_neighbours_clip(self):
        # Shot 2 is rendered; shot 3 is not. The user presses up-arrow on shot
        # 3 — the client renumbers locally (3 -> 2, 2 -> 3) and posts both
        # shots back with their SAME uid, just a new `n`.
        rendered = _shot(2, uid="shot_aaaaaaaa", draft_output="/out/S02.mp4",
                          status="done")
        unrendered = _shot(3, uid="shot_bbbbbbbb")
        self.save(_board("sb_10", [_shot(1, uid="shot_00000000"),
                                    rendered, unrendered]))

        # Client-side up-arrow: swap positions, keep identities, renumber n.
        moved_up = dict(unrendered); moved_up["n"] = 2
        moved_down = dict(rendered); moved_down["n"] = 3
        h = self.post_save("sb_10", [_shot(1, uid="shot_00000000"),
                                      moved_up, moved_down])
        self.assertEqual(h.status, 200)
        by_uid = {s["uid"]: s for s in h.payload["board"]["shots"]}
        # The shot that was never rendered must still show no clip — the old
        # bug matched by `n`=2 and grafted rendered's draft_output onto it.
        self.assertIsNone(by_uid["shot_bbbbbbbb"].get("draft_output"))
        self.assertNotEqual(by_uid["shot_bbbbbbbb"].get("status"), "done")
        # The shot that WAS rendered keeps its own clip, just at a new n.
        self.assertEqual(by_uid["shot_aaaaaaaa"].get("draft_output"), "/out/S02.mp4")
        self.assertEqual(by_uid["shot_aaaaaaaa"]["n"], 3)

    def test_deleting_a_shot_does_not_graft_its_neighbours_clip(self):
        rendered = _shot(2, uid="shot_cccccccc", draft_output="/out/S02.mp4",
                          status="done")
        after = _shot(3, uid="shot_dddddddd")
        self.save(_board("sb_10b", [_shot(1, uid="shot_11111111"),
                                     rendered, after]))
        # Delete shot 1: client filters it out and renumbers 2->1, 3->2.
        moved_rendered = dict(rendered); moved_rendered["n"] = 1
        moved_after = dict(after); moved_after["n"] = 2
        h = self.post_save("sb_10b", [moved_rendered, moved_after])
        by_uid = {s["uid"]: s for s in h.payload["board"]["shots"]}
        self.assertIsNone(by_uid["shot_dddddddd"].get("draft_output"))
        self.assertEqual(by_uid["shot_cccccccc"].get("draft_output"), "/out/S02.mp4")

    def test_a_genuinely_new_shot_with_no_uid_never_inherits_an_old_clip(self):
        rendered = _shot(1, uid="shot_eeeeeeee", draft_output="/out/S01.mp4",
                          status="done")
        self.save(_board("sb_10c", [rendered]))
        brand_new = _shot(2)  # no uid: just typed by the user, never saved
        h = self.post_save("sb_10c", [dict(rendered), brand_new])
        shots = h.payload["board"]["shots"]
        new_shot = next(s for s in shots if s["n"] == 2)
        self.assertIsNone(new_shot.get("draft_output"))
        self.assertTrue(new_shot.get("uid", "").startswith("shot_"))


class MigrationAssignsStableUids(Sandbox):
    def test_a_pre_uid_board_gets_uids_on_reconcile_and_they_persist(self):
        legacy = _board("sb_mig", [_shot(1), _shot(2)])  # no uid field at all
        self.save(legacy)
        loaded = storyboard.load_storyboard(self.state, "sb_mig")
        self.assertNotIn("uid", loaded["shots"][0])
        changed = panel._sb_reconcile(loaded)
        self.assertTrue(changed)
        uids = [s["uid"] for s in loaded["shots"]]
        self.assertEqual(len(set(uids)), 2)
        for u in uids:
            self.assertTrue(u.startswith("shot_"))
        storyboard.save_storyboard(self.state, loaded)
        reloaded = storyboard.load_storyboard(self.state, "sb_mig")
        self.assertEqual([s["uid"] for s in reloaded["shots"]], uids)

    def test_normalize_also_assigns_missing_uids(self):
        board = _board("sb_norm", [_shot(1), _shot(2)])
        for s in board["shots"]:
            s.pop("uid", None)
        panel._sb_normalize(board)
        uids = [s["uid"] for s in board["shots"]]
        self.assertEqual(len(set(uids)), 2)


if __name__ == "__main__":
    unittest.main()
