#!/usr/bin/env python3
"""4.17.0 Codex review — board area (Storyboard + Music video) fixes, one pin
per finding. Findings: ~/AI/projects/phosphene/review-2026-09-29-ux/codex/
findings_board.md (+ LIPSYNC-2 from findings_lipsync.md, same a2v prompt path
as BOARD-5). JS is executed in node where it is behaviour.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import music_video as mv                                              # noqa: E402
import storyboard as sb                                               # noqa: E402

NODE = shutil.which("node")
JS = ROOT / "webapp" / "js"


def _node(script: str):
    if NODE is None:
        pytest.skip("node not on PATH")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        r = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr[-3000:]
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        Path(path).unlink(missing_ok=True)


# ---- BOARD-5 / LIPSYNC-2: the worker's a2v safety pass keeps a prepared prompt --
_LOOK = "cobalt and amber neon lighting, heavy film grain"
_LONG = " ".join(["she sways"] * 20) + ", eyes on the lens"          # > 40 words


def _worker(prompt: str) -> str:
    # The worker's exact call (mlx_ltx_panel.run_job_inner, a2v branch).
    return sb.a2v_prompt(prompt, silent=False)


@pytest.mark.parametrize("silent", [False, True])
def test_board5_lipsync2_prepared_prompt_survives_the_worker_pass(silent):
    planned = mv._sing_prompt(_LONG, _LOOK, "singing", silent=silent)
    assert "cobalt and amber" in planned
    shot = {"n": 1, "mode": "a2v", "engine": "ltx", "prompt": planned,
            "duration": 5, "audio": "/tmp/song.wav", "still": "/tmp/a.png"}
    job = sb.shot_to_job(shot, sb.default_policy()["final"], engine_mode="ltx")
    assert "cobalt and amber" in job["prompt"]
    sent = _worker(job["prompt"])
    # The look reaches the engine, and so does whatever compose appended.
    assert "cobalt and amber neon lighting" in sent
    assert sent == job["prompt"]
    # The contract the planner chose survives, once, and is not swapped.
    if silent:
        assert sent.count("relaxed closed lips") == 1
        assert "lip-syncs" not in sent
    else:
        assert sent.count("lip-syncs every vocal syllable") == 1
        assert "relaxed closed lips" not in sent


def test_board5_manual_audio_prompt_is_still_capped_and_contracted():
    # The manual Audio tab (no contract yet) keeps VA-05's law: cap + contract.
    raw = " ".join(["word"] * 60) + " while he stands perfectly still"
    out = _worker(raw)
    assert out.count("lip-syncs every vocal syllable") == 1
    body = out.split("The singer lip-syncs")[0].split()
    assert len(body) <= sb.A2V_MAX_WORDS
    assert "perfectly still" not in out


# ---- BOARD-1: re-planning refuses to replace a board that is rendering ---------
import mlx_ltx_panel as P                                              # noqa: E402
from panel import routes_music as RM                                   # noqa: E402
from test_music_video_routes import _Fixture, _Handler                 # noqa: E402

RM.P = P


def _plan(fx, **over):
    h = _Handler(fx.form(**over))
    RM.post_music_video_plan(h, "/music/video/plan", {}, "")
    return h


def _mark_rendering(bid):
    board = sb.load_storyboard(P.STATE_DIR, bid)
    for i, s in enumerate(board["shots"]):
        s["draft_job_id"] = f"job_b1_{i}"
        s["status"] = "queued"
    board["render_intent"] = {"pass": "draft", "only": [], "auto": False}
    sb.save_storyboard(P.STATE_DIR, board)
    return board


@pytest.mark.parametrize("how", ["dispatcher", "queued_jobs", "claimed_mid_plan"])
def test_board1_replan_refuses_while_the_board_renders(how):
    with _Fixture() as fx:
        h1 = _plan(fx)
        assert h1.status == 200, h1.body
        bid = h1.body["board_id"]
        before = _mark_rendering(bid)
        saved_queue = list(P.STATE["queue"])
        try:
            if how == "dispatcher":
                with P._SB_LOCK:
                    P._SB_RENDERS[bid] = {"stop": False, "pass": "draft", "queued": 1}
                h2 = _plan(fx, board_id=bid, style="a different look")
            elif how == "queued_jobs":
                with P.LOCK:
                    P.STATE["queue"] = saved_queue + [{"id": "job_b1_0", "params": {}}]
                h2 = _plan(fx, board_id=bid, style="a different look")
            else:
                # The render is claimed WHILE the replacement is being planned.
                real = mv.plan_music_video

                def planning(*a, **k):
                    with P._SB_LOCK:
                        P._SB_RENDERS[bid] = {"stop": False, "pass": "draft", "queued": 0}
                    return real(*a, **k)
                from unittest import mock
                with mock.patch.object(mv, "plan_music_video", planning):
                    h2 = _plan(fx, board_id=bid, style="a different look")
            assert h2.status == 409, h2.body
            assert h2.body.get("busy") is True
            after = sb.load_storyboard(P.STATE_DIR, bid)
            assert [s.get("draft_job_id") for s in after["shots"]] == \
                [s.get("draft_job_id") for s in before["shots"]]
            assert after.get("render_intent") == before["render_intent"]
        finally:
            with P._SB_LOCK:
                P._SB_RENDERS.pop(bid, None)
            with P.LOCK:
                P.STATE["queue"] = saved_queue


def test_board1_replan_of_an_idle_board_still_updates_in_place():
    with _Fixture() as fx:
        bid = _plan(fx).body["board_id"]
        h2 = _plan(fx, board_id=bid, style="a different look")
        assert h2.status == 200 and h2.body["board_id"] == bid


# ---- BOARD-2: a late reply for board A never paints under board B ---------------
from extract_panel_js import extract_function                          # noqa: E402


def _sb_js(*names):
    src = (JS / "storyboard.js").read_text(encoding="utf-8")
    return "\n".join(extract_function(n, src) for n in names)


def test_board2_late_keep_all_and_grade_replies_are_dropped_after_a_switch():
    fns = _sb_js("sbKeepAllUngraded", "sbGrade", "sbFlushSave", "sbShotById")
    out = _node(fns + r"""
const calls = [];
let painted = [];
const SB = { id: 'board-A', payload: { board: { id: 'board-A', shots: [
  { n: 1, uid: 'shot_A1' }] } }, saveInFlight: false, saveAgain: false };
function sbRenderPlan(r) { painted.push(r.board.id); }
function phosToast() {}
function sbAdoptLiveEdits() { return false; }
function sbQueueSave() {}
let release;
const gate = new Promise(res => { release = res; });
globalThis.fetch = async (url, init) => {
  const body = init && init.body;
  calls.push([url, body && body.toString ? body.toString() : String(body)]);
  if (url.includes('keep-all')) await gate;          // A's reply is slow
  if (url.includes('save')) await gate;
  const id = url.includes('save') ? JSON.parse(body).id : body.get('id');
  return { json: async () => ({ ok: true, kept: 1,
    board: { id, shots: [{ n: 1, uid: id === 'board-A' ? 'shot_A1' : 'shot_B1' }] } }) };
};
(async () => {
  const keep = sbKeepAllUngraded();                 // pressed on A
  const save = sbFlushSave();                       // a save of A in flight
  // The user opens B before either reply lands.
  SB.id = 'board-B';
  SB.payload = { board: { id: 'board-B', shots: [{ n: 1, uid: 'shot_B1' }] } };
  release();
  await keep; await save;
  const shown = SB.payload.board.id;
  await sbGrade(1, 'cut', '');                       // CUT on what is displayed
  const grade = calls.find(c => c[0] === '/storyboard/grade')[1];
  console.log(JSON.stringify({ shown, painted, grade }));
})();
""")
    assert out["shown"] == "board-B"
    assert "board-A" not in out["painted"]
    assert "id=board-B" in out["grade"] and "uid=shot_B1" in out["grade"]


def _post_sb(action, form, body=""):
    h = _H()
    P.Handler._storyboard_post(h, action, body, {k: [v] for k, v in form.items()})
    return h.status, h.payload


class _H:
    def __init__(self):
        self.status, self.payload = None, None

    def _json(self, payload, status=200):
        self.payload, self.status = payload, status


def test_board2_server_refuses_a_grade_whose_uid_is_another_shot():
    board = sb.new_storyboard("sb_b2_grade", "Grade target")
    board["shots"] = [{"n": 1, "prompt": "a", "mode": "text", "uid": sb.new_shot_uid()}]
    sb.save_storyboard(P.STATE_DIR, board)
    status, body = _post_sb("grade", {"id": "sb_b2_grade", "n": "1",
                                      "grade": "cut", "uid": "shot_someone_else"})
    assert status == 409 and body["ok"] is False
    assert sb.load_storyboard(P.STATE_DIR, "sb_b2_grade")["shots"][0].get("grade") is None
    status, body = _post_sb("grade", {"id": "sb_b2_grade", "n": "1", "grade": "cut",
                                      "uid": board["shots"][0]["uid"]})
    assert status in (None, 200) and body["ok"] is True
    assert sb.load_storyboard(P.STATE_DIR, "sb_b2_grade")["shots"][0].get("grade") == "cut"


# ---- BOARD-3: a retake (and an imported copy) is a shot with its OWN uid --------
import test_retake as _tr                                               # noqa: E402
from unittest import mock                                                # noqa: E402


def test_board3_retake_gets_a_fresh_uid_and_patches_stay_apart():
    t = _tr.TheRetake("test_the_pool_carries_the_prompt")
    t.setUp()
    try:
        t.board["shots"][0]["uid"] = "shot_original"
        h = _tr.FakeHandler()
        with mock.patch.object(P, "_sb_known_character_ids", return_value=["bizarrotrn"]), \
                mock.patch.object(P, "_sb_h3_available", return_value=False), \
                mock.patch.object(sb, "shot_to_job", return_value={"mode": "t2v", "prompt": "p"}):
            h.post("edit/generate", {"id": "sb_t", "prompt": "he turns, slower",
                                     "duration": "4", "film_start": "0",
                                     "retake_of": "c1"})
        assert h.status == 202, h.payload
        orig, take = t.board["shots"][0], t.board["shots"][-1]
        assert orig["uid"] == "shot_original"
        assert take["uid"] and take["uid"] != "shot_original"
        P._sb_patch_board("sb_t", shots={"shot_original": {"draft_output": "/o/a.mp4"}})
        assert orig.get("draft_output") == "/o/a.mp4"
        assert take.get("draft_output") is None
        P._sb_patch_board("sb_t", shots={take["uid"]: {"draft_output": "/o/b.mp4"}})
        assert take.get("draft_output") == "/o/b.mp4"
        assert orig.get("draft_output") == "/o/a.mp4"
    finally:
        t.tearDown()


def test_board3_normalize_splits_a_duplicate_uid_first_holder_keeps_it():
    board = sb.new_storyboard("sb_b3_dup", "Dup")
    board["shots"] = [{"n": 1, "prompt": "a", "mode": "text", "uid": "shot_same"},
                      {"n": 2, "prompt": "b", "mode": "text", "uid": "shot_same"}]
    P._sb_normalize(board)
    uids = [s["uid"] for s in board["shots"]]
    assert uids[0] == "shot_same" and uids[1] != "shot_same" and len(set(uids)) == 2
    # The GET path (reconcile) repairs a board saved by an earlier build too.
    board["shots"][1]["uid"] = "shot_same"
    with mock.patch.object(P, "_sb_job_index", return_value={}):
        assert P._sb_reconcile(board) is True
    assert len({s["uid"] for s in board["shots"]}) == 2


def test_board3_imported_copy_gets_its_own_uid(tmp_path):
    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"x")
    src = {"id": "sb_src", "title": "Src",
           "shots": [{"n": 1, "uid": "shot_src1", "draft_output": str(clip), "prompt": "a"}]}
    dst = {"id": "sb_dst", "shots": []}
    imported, _ = P._sb_import_shots(dst, src)
    assert imported and dst["shots"][0]["uid"] != "shot_src1"
    assert src["shots"][0]["uid"] == "shot_src1"


# ---- BOARD-4: "Edit & re-render" take history reaches the Editor's relink offer --
import storyboard_editor as _sedit                                      # noqa: E402
from test_storyboard_editor_api import _board as _ed_board, _clip as _ed_clip, \
    _edit as _ed_edit                                                   # noqa: E402


def test_board4_new_take_then_render_offers_exactly_one_replacement(tmp_path):
    old, new = tmp_path / "old.mp4", tmp_path / "new.mp4"
    old.write_bytes(b"x")
    new.write_bytes(b"y")
    board = _ed_board([old])
    edit = _sedit.normalise_edit(_ed_edit([_ed_clip(str(old), 0.0, 4.0, 0.0, id="c1")]))
    shot = P._sb_new_take(board, 1)                       # Storyboard "Edit & re-render"
    assert shot["takes"] and isinstance(shot["takes"][0], dict)
    shot["draft_job_id"] = "j-new"
    with mock.patch.object(P, "_sb_job_index",
                           return_value={"j-new": {"status": "done",
                                                   "output_path": str(new)}}), \
            mock.patch.object(P, "_probe_video_dims", return_value=(0, 0)):
        P._sb_reconcile(board)
    assert shot["draft_output"] == str(new)
    rows = [r for r in P._sbe_relinks(board, edit) if r.get("id") == "c1"]
    assert len(rows) == 1, rows
    assert rows[0]["path"] == str(old) and rows[0]["to"] == str(new)


def test_board4_take_path_reads_both_shapes_and_reconcile_dedupes_across_them():
    assert sb.take_path("/o/a.mp4") == "/o/a.mp4"
    assert sb.take_path({"path": "/o/a.mp4", "pass": "draft"}) == "/o/a.mp4"
    assert sb.take_path({"pass": "draft"}) == "" and sb.take_path(None) == ""
    board = _ed_board([Path("/o/s1_v1.mp4")])
    board["shots"][0]["takes"] = [{"path": "/o/s1_v1.mp4", "pass": "draft"}]
    board["shots"][0]["draft_job_id"] = "j2"
    with mock.patch.object(P, "_sb_job_index",
                           return_value={"j2": {"status": "done",
                                                "output_path": "/o/s1_v2.mp4"}}), \
            mock.patch.object(P, "_probe_video_dims", return_value=(0, 0)):
        P._sb_reconcile(board)
    assert [sb.take_path(t) for t in board["shots"][0]["takes"]] == ["/o/s1_v1.mp4"]


# ---- BOARD-6: a resumed render waits for the jobs a restart restored ------------
def test_board6_resume_waits_for_restored_jobs_then_films_once(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    a, b = tmp_path / "s1.mp4", tmp_path / "s2.mp4"
    a.write_bytes(b"x")
    board = _ed_board([a], bid="sb_b6")
    board["shots"].append({"n": 2, "title": "shot 2", "mode": "text", "engine": "ltx",
                           "prompt": "shot 2 happens", "duration_s": 5.0, "seed": 7,
                           "refs": [], "status": "queued", "draft_job_id": "j2"})
    board["auto"] = True
    board["render_intent"] = {"pass": "draft", "only": None, "auto": True}
    qstate = {"queue": [{"id": "j2", "status": "queued", "params": {}}],
              "current": None, "history": []}
    films, intent_while_waiting = [], []

    def fake_sleep(_s):
        # The restored job is still queued: the intent must still be on disk.
        intent_while_waiting.append(
            "render_intent" in sb.load_storyboard(state, "sb_b6"))
        b.write_bytes(b"y")
        qstate["queue"] = []
        qstate["history"] = [{"id": "j2", "status": "done", "output_path": str(b)}]

    with mock.patch.object(P, "STATE_DIR", state), \
            mock.patch.object(P, "STATE", qstate), \
            mock.patch.object(P, "push", lambda *a_, **k: None), \
            mock.patch.object(P, "persist_queue", lambda: None), \
            mock.patch.object(P, "_sb_h3_available", return_value=False), \
            mock.patch.object(P, "_probe_video_dims", return_value=(0, 0)), \
            mock.patch.object(P, "_sb_lipsync_gate", lambda *a_, **k: None), \
            mock.patch.object(P, "_sb_sweep_stage_a", lambda *a_, **k: None), \
            mock.patch.object(P, "_sb_enqueue", side_effect=AssertionError("re-queued")), \
            mock.patch.object(P, "_sb_auto_film",
                              side_effect=lambda bd: films.append(
                                  [s.get("draft_output") for s in bd["shots"]])), \
            mock.patch.object(P.time, "sleep", fake_sleep):
        sb.save_storyboard(state, board)
        with P._SB_LOCK:
            P._SB_RENDERS["sb_b6"] = {"stop": False, "pass": "draft", "queued": 0,
                                      "auto": True, "resumed": True}
        try:
            P._sb_render_thread("sb_b6", "draft", None)
        finally:
            with P._SB_LOCK:
                P._SB_RENDERS.pop("sb_b6", None)
        after = sb.load_storyboard(state, "sb_b6")
    assert intent_while_waiting and all(intent_while_waiting)
    assert films == [[str(a), str(b)]]                       # ONE film, every output
    assert "render_intent" not in after


# ---- BOARD-7: re-planning sets the old timeline aside instead of exporting it ----
def test_board7_replan_keeps_old_timeline_as_a_draft_and_export_leaves_it(tmp_path):
    old_clip = tmp_path / "old-plan.mp4"
    old_clip.write_bytes(b"x")
    with _Fixture() as fx:
        bid = _plan(fx).body["board_id"]
        bdir = P._sbe_board_dir(bid)
        bdir.mkdir(parents=True, exist_ok=True)
        edit = _sedit.normalise_edit(_ed_edit([_ed_clip(str(old_clip), 0.0, 4.0, 0.0, id="c1")]))
        _sedit.save_edit(bdir, edit)
        assert _sedit.load_edit(bdir)["clips"]

        h2 = _plan(fx, board_id=bid, style="a different look")
        assert h2.status == 200, h2.body
        # Export's rule is "a saved timeline with clips wins" — there is none now.
        assert _sedit.load_edit(bdir) is None
        rows = _sedit.list_drafts(bdir)
        kept = [r for r in rows if not r["active"]]
        assert len(kept) == 1 and kept[0]["clips"] == 1
        assert "before re-plan" in kept[0]["name"]
        assert any("kept as the draft" in n for n in h2.body["notes"])
        # The old cut is intact and can be switched back to.
        _sedit.activate_draft(bdir, kept[0]["slug"])
        assert _sedit.load_edit(bdir)["clips"][0]["path"] == str(old_clip)

        # A re-plan with no timeline yet sets nothing aside.
        h3 = _plan(fx, board_id=bid)
        assert h3.status == 200
        n_drafts = len(_sedit.list_drafts(bdir))
        _sedit.activate_draft(bdir, [r for r in _sedit.list_drafts(bdir)
                                     if "New plan" in r["name"]][0]["slug"])
        h4 = _plan(fx, board_id=bid)
        assert h4.status == 200 and len(_sedit.list_drafts(bdir)) == n_drafts


def test_board7_export_uses_the_new_plans_shots_after_replan(tmp_path):
    old_clip = tmp_path / "old-plan.mp4"
    old_clip.write_bytes(b"x")
    with _Fixture() as fx:
        bid = _plan(fx).body["board_id"]
        bdir = P._sbe_board_dir(bid)
        bdir.mkdir(parents=True, exist_ok=True)
        _sedit.save_edit(bdir, _sedit.normalise_edit(
            _ed_edit([_ed_clip(str(old_clip), 0.0, 4.0, 0.0, id="c1")])))
        assert _plan(fx, board_id=bid, style="another").status == 200
        board = sb.load_storyboard(P.STATE_DIR, bid)
        timeline_used = []
        with mock.patch.object(P, "_sbe_render_edit",
                               side_effect=lambda *a, **k: timeline_used.append(a) or
                               {"ok": False, "error": "mocked"}), \
                mock.patch.object(P, "_sb_assemble_film",
                                  return_value={"ok": False, "error": "mocked"}):
            P._sb_export(board)
        assert timeline_used == [], "Export assembled the old plan's timeline"


# ---- BOARD-8: Storyboard Export honours the Editor's soundtrack and mix ---------
def test_board8_both_export_entrances_send_the_saved_audio(tmp_path):
    clip = tmp_path / "s1.mp4"
    clip.write_bytes(b"x")
    edited_song = tmp_path / "edited-song.wav"
    edited_song.write_bytes(b"RIFF")
    with _Fixture() as fx:
        board = sb.new_storyboard("sb_b8_mv", "Music film")
        board["music_video"] = {"song": str(fx.song), "images": []}
        board["shots"] = [{"n": 1, "uid": "shot_b8", "mode": "text", "engine": "ltx",
                           "prompt": "a", "duration_s": 4.0, "status": "done",
                           "draft_output": str(clip)}]
        sb.save_storyboard(P.STATE_DIR, board)
        bdir = P._sbe_board_dir("sb_b8_mv")
        bdir.mkdir(parents=True, exist_ok=True)
        edit = _sedit.normalise_edit(_ed_edit([_ed_clip(str(clip), 0.0, 4.0, 0.0, id="c1")]))
        edit["audio"] = {"path": str(edited_song), "offset": 0.0, "mode": "under"}
        _sedit.save_edit(bdir, edit)
        got = []

        def capture(*a, **k):
            got.append((str(k.get("music")), k.get("music_mode")))
            return {"ok": False, "error": "mocked"}

        with mock.patch.object(P, "_sb_assemble_film", side_effect=capture), \
                mock.patch.object(P, "_sb_crop_to_aspect", return_value=None):
            # Storyboard's Export button on a music-video board:
            h = _Handler({"board_id": "sb_b8_mv"})
            RM.post_music_video_film(h, "/music/video/film", {}, "")
            # The Editor's Render (the client posts the document's own values):
            P._sbe_render_edit(sb.load_storyboard(P.STATE_DIR, "sb_b8_mv"),
                               _sedit.load_edit(bdir), music=str(edited_song),
                               music_mode="under")
        assert got == [(str(edited_song), "under"), (str(edited_song), "under")], got


# ---- BOARD-9: Stills first never renders video past a failed still -------------
import test_stills_first as _tsf                                        # noqa: E402
import test_stills_first_ui as _tsfui                                   # noqa: E402


def _hold_case():
    t = _tsf.TheRenderThreadHoldsForApproval("test_an_approved_shot_proceeds_to_video")
    t.setUp()
    return t


def _teardown(t):
    t.doCleanups()


def _run9(t, board, *, index, refuse_first=False):
    """The render thread, with the still job's REAL verdict in the index."""
    bid = board["id"]
    t.save(board)
    P._SB_RENDERS[bid] = {"stop": False, "queued": 0}
    video_forms, calls = [], []

    def enqueue(form):
        calls.append(form)
        if refuse_first and len(calls) == 1:
            raise RuntimeError("image engine refused")          # the still
        video_forms.append(form)
        return f"video-{len(video_forms)}"
    with mock.patch.object(P, "_sb_enqueue", side_effect=enqueue), \
            mock.patch.object(P, "_sb_job_index", return_value=index), \
            mock.patch.object(P, "_sb_h3_available", return_value=False), \
            mock.patch.object(P, "h3_supports_chain_prompts", return_value=False), \
            mock.patch.object(P, "h3_supports_first_frame", return_value=False), \
            mock.patch.object(P, "_sb_lipsync_gate", lambda *a, **k: None), \
            mock.patch.object(P, "_sb_sweep_stage_a", lambda *a, **k: None):
        P._sb_render_thread(bid, "draft", None)
    return video_forms


_FAILED_STILL = {"j-still": {"status": "failed", "output_path": None,
                             "error": "the still could not be made"}}


def test_board9_a_failed_still_holds_the_video():
    t = _hold_case()
    try:
        board = _tsf._board("sb_b9_fail", [_tsf._shot(1, still_job_id="j-still")])
        assert _run9(t, board, index=_FAILED_STILL) == []
        shot = t.load("sb_b9_fail")["shots"][0]
        assert shot.get("draft_job_id") is None and shot.get("still_error")
    finally:
        _teardown(t)


def test_board9_a_still_that_could_not_be_queued_holds_the_video():
    t = _hold_case()
    try:
        board = _tsf._board("sb_b9_refused", [_tsf._shot(1)])
        assert _run9(t, board, index={}, refuse_first=True) == []
        shot = t.load("sb_b9_refused")["shots"][0]
        assert shot.get("still_job_id") == "skipped" and shot.get("still_error")
    finally:
        _teardown(t)


def test_board9_render_without_a_still_is_an_explicit_approval():
    t = _hold_case()
    try:
        board = _tsf._board("sb_b9_ok", [
            _tsf._shot(1, still_job_id="j-still", still_error="the still could not be made")])
        t.save(board)
        h = t.post("approve-still", {"id": ["sb_b9_ok"], "n": ["1"]})
        assert h.status in (None, 200), h.payload
        assert t.load("sb_b9_ok")["shots"][0]["still_approved"] is True
        # Now it may render — unanchored, because the user said so.
        assert len(_run9(t, t.load("sb_b9_ok"), index=_FAILED_STILL)) == 1
    finally:
        _teardown(t)


def test_board9_the_failed_still_card_offers_retry_and_unanchored():
    c = _tsfui.TheCardShowsApproveWhenAwaiting("test_an_unapproved_still_shows_an_approve_button")
    html = c._card({"still_job_id": "j", "still_error": "boom"})
    assert 'data-act="restill"' in html and 'data-act="approve-still"' in html
    assert "Render without a still" in html
    html_off = c._card({"still_job_id": "j", "still_error": "boom"},
                       board_over={"stills_first": False})
    assert 'data-act="approve-still"' not in html_off


# ---- BOARD-10: a pre-FILM-11 music-video still is still the user's photo --------
def _legacy_singing_shot(fx):
    bid = _plan(fx).body["board_id"]
    board = sb.load_storyboard(P.STATE_DIR, bid)
    shot = next(s for s in board["shots"] if s["music_video"]["kind"] == "singing")
    for s in board["shots"]:
        s.pop("still_source", None)                   # the pre-change shape
    return board, shot


def test_board10_new_still_keeps_a_legacy_cast_photo_and_it_reaches_the_job():
    with _Fixture() as fx:
        board, shot = _legacy_singing_shot(fx)
        photo = shot["still"]
        assert photo == shot["music_video"]["image"] and "still_source" not in shot
        # Defensive: the clear-still path recognises the provenance on its own.
        P._sb_clear_still(board, shot["n"])
        assert shot["still"] == photo and shot["still_source"] == "user"
        job = sb.shot_to_job(shot, sb.default_policy()["draft"], engine_mode="ltx")
        assert job["mode"] == "a2v" and job["image"] == photo


def test_board10_normalize_migrates_the_marker_and_stills_first_never_holds_it():
    with _Fixture() as fx:
        board, shot = _legacy_singing_shot(fx)
        P._sb_normalize(board)
        assert all(s.get("still_source") == "user" for s in board["shots"]
                   if s.get("still") == (s.get("music_video") or {}).get("image"))
        board2, _ = _legacy_singing_shot(fx)
        with mock.patch.object(P, "_sb_job_index", return_value={}):
            assert P._sb_reconcile(board2) is True
        assert all(s.get("still_source") == "user" for s in board2["shots"])
    # A machine-made still is NOT the user's.
    assert not sb.is_user_still({"still": "/o/gen.png", "music_video": {"image": "/u/a.png"}})
    assert not sb.is_user_still({"still": "/o/gen.png"})


def test_board10_stills_first_does_not_hold_a_broll_shot_on_the_users_photo():
    t = _hold_case()
    try:
        board = _tsf._board("sb_b10_broll", [_tsf._shot(
            1, still="/u/piano.png", music_video={"image": "/u/piano.png", "kind": "broll"})])
        assert len(_run9(t, board, index={})) == 1
    finally:
        _teardown(t)
