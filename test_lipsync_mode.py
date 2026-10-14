"""Lip-sync-as-a-first-class-Video-mode (review-2026-09-29-ux, video-advanced
package): findings VA-01/03/04/05/07/14/18/19/20/21/23/24/25/26/27/28/29/30/
31/32/35/36/37/38/39, plus the "Keyframe + lip-sync" build items — anchors
allowlisted, the open-mouth check, and "Continue the song".

Mostly pure-function and structural assertions, matching test_a2v_sync_fixes.py's
own approach: the alternative to a structural check is a real a2v render
(a weights pack, a helper subprocess, 10-25 minutes), and conftest.py's
sandbox does not make that safe to do in a test run. Findings that genuinely
need a GPU render to validate end-to-end (VA-14's scorer wired into a real
a2v render, VA-18's Windows chain, VA-26's Retake, VA-27's preview on a real
a2v/keyframe render, VA-30's multi-part chain, VA-38's resume) are listed in
ledger_lipsync.txt under NEEDS RENDER CHECK with the exact render to run;
this file proves the CODE PATHS are wired, guarded and syntactically sound.
"""

from __future__ import annotations

import ast
import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import mlx_ltx_panel as P
import storyboard
from panel.routes import GET_ROUTES, POST_ROUTES

ROOT = Path(__file__).resolve().parent
PANEL_SRC = (ROOT / "mlx_ltx_panel.py").read_text()
HELPER_SRC = (ROOT / "mlx_warm_helper.py").read_text()
INDEX_HTML = (ROOT / "webapp" / "index.html").read_text()
CHARACTERS_JS = (ROOT / "webapp" / "js" / "characters.js").read_text()
QUEUE_JS = (ROOT / "webapp" / "js" / "queue.js").read_text()
BOOT_JS = (ROOT / "webapp" / "js" / "boot.js").read_text()


def _helper_functions(*names):
    """Exec the named top-level functions of mlx_warm_helper.py in isolation
    (same technique as test_a2v_sync_fixes.py's own helper) — no mlx/ltx
    import needed for functions that only touch dicts, Path and `emit`."""
    tree = ast.parse(HELPER_SRC)
    defs = [n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name in names]
    assert sorted(d.name for d in defs) == sorted(names), names
    ns: dict = {"Path": Path, "emit": lambda evt: None}
    exec(compile(ast.Module(body=defs, type_ignores=[]), "mlx_warm_helper.py",
                 "exec"), ns)
    return ns


def _ffmpeg_ok() -> bool:
    try:
        subprocess.run([str(P.FFMPEG), "-version"], capture_output=True,
                        check=True, timeout=10)
        return True
    except Exception:                                              # noqa: BLE001
        return False


HAVE_FFMPEG = _ffmpeg_ok()


def _make_solid_video(path: Path, seconds: float = 1.0, color: str = "gray"):
    subprocess.run(
        [str(P.FFMPEG), "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"color=c={color}:s=64x64:d={seconds}:r=10",
         "-pix_fmt", "yuv420p", str(path)],
        check=True, timeout=30)


def _make_silence(path: Path, seconds: float = 2.5):
    subprocess.run(
        [str(P.FFMPEG), "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"anullsrc=r=16000:cl=mono", "-t", str(seconds), str(path)],
        check=True, timeout=30)


# ---------------------------------------------------------------------------
# Mouth-openness helpers — pure functions, no face in these synthetic inputs,
# so every positive case here is "handles it without crashing and returns
# the documented None", not "measures a real mouth" (that needs a photo).
# ---------------------------------------------------------------------------
class MouthOpennessHelpers(unittest.TestCase):
    def test_mouth_open_ratio_missing_file_is_none(self):
        self.assertIsNone(P.mouth_open_ratio("/no/such/file.png"))

    def test_mouth_open_ratio_no_face_is_none(self):
        with TemporaryDirectory() as td:
            img = Path(td) / "blank.png"
            if not HAVE_FFMPEG:
                self.skipTest("ffmpeg not available")
            subprocess.run(
                [str(P.FFMPEG), "-y", "-loglevel", "error", "-f", "lavfi",
                 "-i", "color=c=gray:s=64x64", "-frames:v", "1", str(img)],
                check=True, timeout=30)
            self.assertIsNone(P.mouth_open_ratio(img))

    def test_find_closed_mouth_frame_missing_file_is_none(self):
        self.assertIsNone(P.find_closed_mouth_frame("/no/such/clip.mp4"))

    def test_find_closed_mouth_frame_no_face_is_none(self):
        if not HAVE_FFMPEG:
            self.skipTest("ffmpeg not available")
        with TemporaryDirectory() as td:
            clip = Path(td) / "blank.mp4"
            _make_solid_video(clip)
            self.assertIsNone(P.find_closed_mouth_frame(clip, window_s=0.5))

    def test_extract_frame_png_writes_a_real_file(self):
        if not HAVE_FFMPEG:
            self.skipTest("ffmpeg not available")
        with TemporaryDirectory() as td:
            clip = Path(td) / "src.mp4"
            _make_solid_video(clip, seconds=1.0)
            out = Path(td) / "frame.png"
            ok = P.extract_frame_png(clip, 0.2, out)
            self.assertTrue(ok)
            self.assertTrue(out.exists())
            self.assertGreater(out.stat().st_size, 0)

    def test_extract_frame_png_bad_source_is_false(self):
        with TemporaryDirectory() as td:
            out = Path(td) / "frame.png"
            self.assertFalse(P.extract_frame_png("/no/such/clip.mp4", 0.0, out))


# ---------------------------------------------------------------------------
# VA-03: /upload probes audio duration.
# ---------------------------------------------------------------------------
class ProbeMediaDuration(unittest.TestCase):
    def test_probe_audio_duration(self):
        if not HAVE_FFMPEG:
            self.skipTest("ffmpeg not available")
        with TemporaryDirectory() as td:
            wav = Path(td) / "clip.wav"
            _make_silence(wav, seconds=2.5)
            dur = P.probe_media_duration(wav)
            self.assertIsNotNone(dur)
            self.assertAlmostEqual(dur, 2.5, delta=0.15)

    def test_probe_missing_file_is_none(self):
        self.assertIsNone(P.probe_media_duration("/no/such/file.wav"))


# ---------------------------------------------------------------------------
# Anchors: allowlisted on make_job, parsed + clamped in the a2v branch.
# ---------------------------------------------------------------------------
class AnchorsAllowlist(unittest.TestCase):
    def test_make_job_carries_anchors_json(self):
        job = P.make_job({
            "mode": ["a2v"], "prompt": ["a woman sings"],
            "audio": [str(P.AUDIO_DEFAULT)],
            "anchors_json": [json.dumps([{"path": "/x.png", "frame_idx": 0,
                                          "strength": 1.0}])],
        })
        self.assertIn("anchors_json", job["params"])
        parsed = json.loads(job["params"]["anchors_json"])
        self.assertEqual(parsed[0]["frame_idx"], 0)

    def test_run_job_inner_parses_and_clamps_anchors(self):
        # Structural: the a2v branch must parse anchors_json defensively
        # (never raise on a malformed/stale entry) and clamp frame_idx to
        # [0, frames).
        a2v_branch = PANEL_SRC[PANEL_SRC.index('if mode == "a2v":'):
                                PANEL_SRC.index('# T2V / I2V / I2V+clean_audio')]
        self.assertIn("anchors_json", a2v_branch)
        self.assertIn("_idx < 0 or _idx >= frames", a2v_branch)
        self.assertIn('a2v_params["anchors"] = a2v_anchors', a2v_branch)

    def test_helper_anchor_builder_present(self, ):
        # fd2356d, cherry-picked: _a2v_anchor_images in mlx_warm_helper.py.
        helper_src = (ROOT / "mlx_warm_helper.py").read_text()
        self.assertIn("def _a2v_anchor_images(", helper_src)
        self.assertIn('kwargs["images"] = _anchors', helper_src)


# ---------------------------------------------------------------------------
# VA-05: the A2V prompt law now runs on the manual path too.
# ---------------------------------------------------------------------------
class A2vPromptLawOnManualPath(unittest.TestCase):
    def test_run_job_inner_applies_a2v_prompt(self):
        a2v_branch = PANEL_SRC[PANEL_SRC.index('if mode == "a2v":'):
                                PANEL_SRC.index('# T2V / I2V / I2V+clean_audio')]
        self.assertIn("storyboard.a2v_prompt(p[\"prompt\"], silent=False)",
                       a2v_branch)
        self.assertIn('"prompt": a2v_safe_prompt', a2v_branch)

    def test_a2v_prompt_strips_stillness_and_adds_contract(self):
        unsafe = "he stands perfectly still, static camera, singing softly"
        safe = storyboard.a2v_prompt(unsafe, silent=False)
        self.assertNotIn("static camera", safe)
        self.assertNotIn("stands perfectly still", safe)
        self.assertIn("lip-sync", safe.lower())

    def test_a2v_prompt_idempotent_on_already_safe_prompt(self):
        # A storyboard/music-video-composed prompt already has the contract —
        # running it through again (which run_job_inner now always does)
        # must not double it or truncate it further.
        once = storyboard.a2v_prompt("a woman sings to camera", silent=False)
        twice = storyboard.a2v_prompt(once, silent=False, max_words=None)
        self.assertEqual(once, twice)
        self.assertEqual(once.count("lip-syncs"), 1)


# ---------------------------------------------------------------------------
# VA-24: /prompt/enhance accepts mode=a2v and runs the a2v cleanup.
# ---------------------------------------------------------------------------
class EnhanceA2vMode(unittest.TestCase):
    def test_routes_queue_maps_a2v_to_i2v_and_cleans_result(self):
        src = (ROOT / "panel" / "routes_queue.py").read_text()
        fn = src[src.index('def post_prompt_enhance('):
                  src.index('\n@post("/run")') if '\n@post("/run")' in src
                  else src.index('def post_run(')]
        self.assertIn('a2v_mode = (mode == "a2v")', fn)
        self.assertIn('mode = "i2v"', fn)
        self.assertIn('P.storyboard.a2v_prompt(enhanced, silent=False)', fn)


# ---------------------------------------------------------------------------
# New routes exist exactly once (test_routes.py covers global uniqueness;
# this just pins that these three specific ones are the a2v/lip-sync trio).
# ---------------------------------------------------------------------------
class NewRoutesRegistered(unittest.TestCase):
    def test_mouth_check_registered(self):
        self.assertIn("/a2v/mouth_check", POST_ROUTES)

    def test_continue_song_registered(self):
        self.assertIn("/a2v/continue_song", POST_ROUTES)

    def test_prompt_check_registered(self):
        self.assertIn("/a2v/prompt_check", POST_ROUTES)


# ---------------------------------------------------------------------------
# VA-36: outputs carry their render mode; the player swaps Extend for
# "Continue the song" on a2v clips only.
# ---------------------------------------------------------------------------
class OutputRenderMode(unittest.TestCase):
    def test_list_outputs_derives_render_mode(self):
        self.assertIn('render_mode = _rmode', PANEL_SRC)
        self.assertIn('"mode": render_mode,', PANEL_SRC)

    def test_player_swaps_extend_for_continue_song_on_a2v(self):
        self.assertIn("outIsA2v = !!(o && o.mode === 'a2v')", QUEUE_JS)
        self.assertIn("continueSongBtn", QUEUE_JS)
        self.assertIn("async function continueSongFromClip(", QUEUE_JS)


# ---------------------------------------------------------------------------
# VA-01: a first-class Lip-sync entry on the Video mode bar.
# ---------------------------------------------------------------------------
class LipSyncModeBarEntry(unittest.TestCase):
    def test_chip_present_in_mode_bar(self):
        mode_bar = INDEX_HTML[INDEX_HTML.index('id="modeGroup"'):
                               INDEX_HTML.index('id="remixSubGroup"')]
        self.assertIn('data-lipsync="1"', mode_bar)
        self.assertIn('Lip-sync', mode_bar)

    def test_open_lip_sync_entry_defined_and_published(self):
        self.assertIn("function openLipSyncEntry()", CHARACTERS_JS)
        publish_block = CHARACTERS_JS[CHARACTERS_JS.index("Object.assign(globalThis, {"):]
        self.assertIn("openLipSyncEntry", publish_block)

    def test_one_shot_name_not_reused_for_lip_sync_section(self):
        # The folded section used to be titled "One shot (audio → video)",
        # colliding with the top-level One Shot tab.
        self.assertNotIn('cz-title">One shot (audio', INDEX_HTML)


# ---------------------------------------------------------------------------
# VA-23: Remix → Character (or Image / Train) no longer leaves the Remix
# tool panel lit.
# ---------------------------------------------------------------------------
class RemixSubgroupHiddenOnEarlyReturn(unittest.TestCase):
    def test_setmode_hides_remixbar_before_early_returns(self):
        start = BOOT_JS.index("function setMode(mode) {")
        fn = BOOT_JS[start:start + 8000]
        hide_idx = fn.index("_remixBarEarly")
        train_idx = fn.index("if (mode === 'train')")
        char_idx = fn.index("if (mode === 'character')")
        self.assertLess(hide_idx, train_idx)
        self.assertLess(hide_idx, char_idx)


# ---------------------------------------------------------------------------
# VA-32: Keyframes default to 3, not 6.
# ---------------------------------------------------------------------------
class KeyframeDefaultCount(unittest.TestCase):
    def test_three_is_selected_by_default(self):
        select = INDEX_HTML[INDEX_HTML.index('id="keyframe_count"'):
                             INDEX_HTML.index('</select>', INDEX_HTML.index('id="keyframe_count"'))]
        self.assertIn('value="3" selected', select)
        self.assertNotIn('value="6" selected', select)


# ---------------------------------------------------------------------------
# VA-37: the dead "Reference audio" row is gone; the API refuses an
# audio-only request instead of silently dropping it.
# ---------------------------------------------------------------------------
class CharactersReferenceAudioDead(unittest.TestCase):
    def test_reference_audio_row_removed(self):
        self.assertNotIn('id="charactersAudioFile"', INDEX_HTML)
        self.assertNotIn('character lip-syncs to this clip', INDEX_HTML)

    def test_routes_characters_refuses_audio_without_image(self):
        src = (ROOT / "panel" / "routes_characters.py").read_text()
        self.assertIn("if audio_path and not image_path:", src)
        self.assertIn("make a mux", src)


# ---------------------------------------------------------------------------
# VA-19: Q8's audio-strength floor moves off the "off" value.
# ---------------------------------------------------------------------------
class AudioStrengthLaneAwareFloor(unittest.TestCase):
    def test_q8_floor_is_above_the_off_point(self):
        self.assertIn("el.min = isQ4 ? '0.5' : '1.5';", CHARACTERS_JS)


# ---------------------------------------------------------------------------
# VA-28: One Shot Load Params restores a random-seed take's first part's
# real seed instead of leaving Seed blank.
# ---------------------------------------------------------------------------
class OneShotSeedRestore(unittest.TestCase):
    def test_parts_meta_recorded_in_take_block(self):
        # The whole function (it grew past a fixed 20k-char window once the
        # One Shot resume/recovery wrapper, VA-17, landed around the loop).
        _start = PANEL_SRC.index("def run_take_job_inner(")
        branch = PANEL_SRC[_start:PANEL_SRC.index("\ndef run_job_inner(", _start)]
        self.assertIn("take_part_seeds", branch)
        self.assertIn('"parts_meta": [{"seed_used": s} for s in part_seeds]',
                       branch)

    def test_oneshot_js_restores_first_part_seed(self):
        oneshot_js = (ROOT / "webapp" / "js" / "oneshot.js").read_text()
        fn = oneshot_js[oneshot_js.index("function oneshotOpenFromParams("):
                         oneshot_js.index("function oneshotOpenFromParams(") + 2500]
        self.assertIn("take.parts_meta", fn)
        self.assertIn("firstPartSeed", fn)


# ---------------------------------------------------------------------------
# VA-07: a Draft · Final pair for lip-sync — seed-locked, duration slider
# untouched by Draft so Final reuses it at full length.
# ---------------------------------------------------------------------------
class A2vDraftFinal(unittest.TestCase):
    def test_generate_accepts_a_draft_option_and_clamps_frames(self):
        fn = CHARACTERS_JS[CHARACTERS_JS.index("async function audioStudioGenerate("):
                            CHARACTERS_JS.index("async function audioStudioGenerate(") + 2200]
        self.assertIn("opts.draft", fn)
        self.assertIn("Math.min(3, dur)", fn)
        self.assertIn("seedEl.value = String(seed)", fn)

    def test_draft_button_present(self):
        self.assertIn('id="audioStudioDraftBtn"', INDEX_HTML)
        self.assertIn("audioStudioGenerate({draft:true})", INDEX_HTML)


# ---------------------------------------------------------------------------
# VA-14: the lip-sync verdict — scored post-render, CPU-only, guarded.
# ---------------------------------------------------------------------------
class A2vLipsyncVerdict(unittest.TestCase):
    def test_a2v_branch_scores_and_guards(self):
        a2v_branch = PANEL_SRC[PANEL_SRC.index('if mode == "a2v":'):
                                PANEL_SRC.index('# T2V / I2V / I2V+clean_audio')]
        self.assertIn("a2v_lipsync_score = None", a2v_branch)
        self.assertIn("take_lipsync_score(out_path)", a2v_branch)
        self.assertIn('"lipsync_score": a2v_lipsync_score,', a2v_branch)
        # Guarded: a scoring exception must not be allowed to propagate and
        # fail an otherwise-finished render.
        idx = a2v_branch.index("a2v_lipsync_score = take_lipsync_score")
        self.assertIn("except Exception", a2v_branch[idx:idx + 600])

    def test_list_outputs_surfaces_the_score(self):
        self.assertIn('lipsync_score = None  # a2v', PANEL_SRC)
        self.assertIn('"lipsync_score": lipsync_score,', PANEL_SRC)

    def test_player_renders_the_verdict_chip(self):
        self.assertIn("o.mode === 'a2v' && o.lipsync_score", QUEUE_JS)
        self.assertIn("po-ls-ok", QUEUE_JS)
        css = (ROOT / "webapp" / "style" / "panel.css").read_text()
        self.assertIn(".po-ls-ok", css)


# ---------------------------------------------------------------------------
# VA-27: live preview threaded onto Keyframe + A2V's stage-1 loops,
# guarded by the SAME _filter_unsupported_kwargs every other kwarg on
# these call sites already goes through.
# ---------------------------------------------------------------------------
class LivePreviewOnKeyframeAndA2v(unittest.TestCase):
    def test_modes_only_list_what_a_real_render_previews(self):
        # 4.17.0 render check: the pinned a2v/keyframe pipelines drop or
        # swallow `live_preview`, so listing them opened a preview slot that
        # never filled. They are held back until the engine supports it.
        self.assertIn(
            'LTX_LIVE_PREVIEW_MODES = frozenset(("t2v", "i2v", "i2v_clean_audio"))',
            PANEL_SRC)
        self.assertIn(
            'LTX_LIVE_PREVIEW_PENDING_ENGINE = frozenset(("keyframe", "a2v"))',
            PANEL_SRC)

    def test_a2v_and_keyframe_jobs_get_no_preview_params(self):
        for mode in ("a2v", "keyframe"):
            self.assertEqual(P._live_preview_params({"id": "j-x"}, {"mode": mode}), {})

    def test_keyframe_and_a2v_job_specs_carry_it(self):
        # run_job_inner's own keyframe branch — not the first `if mode ==
        # "keyframe":` in the file (ltx_mode_price_card, VA-11, has one too).
        _rji = PANEL_SRC.index("\ndef run_job_inner(")
        _kf = PANEL_SRC.index('if mode == "keyframe":', _rji)
        kf_branch = PANEL_SRC[_kf:_kf + 8000]
        self.assertIn("**_live_preview_params(job, p)", kf_branch)
        a2v_branch = PANEL_SRC[PANEL_SRC.index('if mode == "a2v":'):
                                PANEL_SRC.index('# T2V / I2V / I2V+clean_audio')]
        self.assertIn("**_live_preview_params(job, p)", a2v_branch)

    def test_helper_threads_it_at_all_three_call_sites(self):
        self.assertEqual(HELPER_SRC.count("_thread_live_preview(p, kwargs)"), 3)

    def test_thread_live_preview_is_a_pure_best_effort_function(self):
        ns = _helper_functions("_build_live_preview", "_thread_live_preview")
        # No dir/tae in `p` -> live_preview is None, nothing raises.
        kwargs = {"output_path": "/tmp/x.mp4"}
        ns["_thread_live_preview"]({}, kwargs)
        self.assertIsNone(kwargs["live_preview"])
        # dir/tae present -> the three keys are copied in (the monitor
        # itself may still resolve to None if the checkpoint/dir don't
        # exist — that's _build_live_preview's own guarded behaviour,
        # proven by this call not raising).
        kwargs2 = {"output_path": "/tmp/y.mp4"}
        ns["_thread_live_preview"](
            {"live_preview_dir": "/no/such/dir", "live_preview_tae": "/no/such/tae.safetensors",
             "live_preview_every": 2},
            kwargs2)
        self.assertEqual(kwargs2.get("live_preview_every"), 2)
        self.assertIn("live_preview", kwargs2)


# ---------------------------------------------------------------------------
# VA-26: segment Retake — the engine's RetakePipeline exposed as a player
# action. NEEDS RENDER CHECK (see ledger_lipsync.txt) for the actual
# pipeline call and the seconds-to-latent-frame math; these tests pin the
# allowlist, the refusal conditions and the guard shape.
# ---------------------------------------------------------------------------
class SegmentRetake(unittest.TestCase):
    def test_make_job_allowlists_retake_fields(self):
        job = P.make_job({
            "mode": ["retake"], "prompt": ["fix the hand"],
            "video_path": ["/x.mp4"],
            "retake_start_sec": ["2.5"], "retake_end_sec": ["4.0"],
            "retake_audio_action": ["regenerate"],
        })
        pr = job["params"]
        self.assertEqual(pr["retake_start_sec"], "2.5")
        self.assertEqual(pr["retake_end_sec"], "4.0")
        self.assertEqual(pr["retake_audio_action"], "regenerate")

    def test_run_job_inner_retake_branch_refuses_before_queueing(self):
        branch = PANEL_SRC[PANEL_SRC.index('if mode == "retake":'):
                            PANEL_SRC.index('if mode == "control":')]
        self.assertIn("allows_extend", branch)         # same hardware gate as Extend
        self.assertIn("hq_surface_missing()", branch)  # same Q8-pack gate
        self.assertIn("retake_end_sec <= retake_start_sec", branch)
        self.assertIn('"action": "retake_segment"', branch)

    def test_helper_action_calls_the_real_pipeline_api_and_is_guarded(self):
        idx = HELPER_SRC.index('if action == "retake_segment":')
        block = HELPER_SRC[idx:idx + 7000]
        self.assertIn("get_pipe(\"extend\"", block)
        self.assertIn("pipe.retake_from_video(", block)
        self.assertIn("except Exception as exc:", block)
        self.assertIn('emit({"event": "error"', block)

    def test_retake_exempted_from_the_distilled_step_count_gate(self):
        # 4.17.4: the gate became a clamp (clamp_distilled_steps); retake keeps
        # its own retake_steps and must stay outside it.
        self.assertIn('"retake"', PANEL_SRC.split("_LTX_OWN_STEP_MODES = frozenset((")[1].split("))")[0])
        self.assertIn("_steps_note = clamp_distilled_steps(p)", PANEL_SRC)

    def test_ui_present(self):
        self.assertIn('id="retakeModal"', INDEX_HTML)
        self.assertIn('id="retakeBtn"', INDEX_HTML)
        self.assertIn("async function submitRetake()", QUEUE_JS)
        self.assertIn("function openRetakeModal(", QUEUE_JS)


# ---------------------------------------------------------------------------
# VA-18: the Windows off-by-one — window 1 can no longer be overridden from
# the UI, real per-window timing from a pure-function-backed route.
# ---------------------------------------------------------------------------
class SlidingWindowsOffByOne(unittest.TestCase):
    def test_windows_plan_route_registered(self):
        self.assertIn("/ltx/windows_plan", GET_ROUTES)

    def test_windows_plan_matches_the_servers_own_planner(self):
        import ltx_windows as lw
        plan = lw.plan_windows(721)
        self.assertEqual(plan["count"], 7)
        self.assertEqual(plan["windows"][0]["start_sec"], 0.0)

    def test_windows_plan_refuses_a_bad_length_with_400_not_500(self):
        src = (ROOT / "panel" / "routes_queue.py").read_text()
        fn = src[src.index('def get_ltx_windows_plan('):
                  src.index('def get_ltx_windows_plan(') + 1400]
        self.assertIn("except ValueError as exc:", fn)
        self.assertIn('h._json({"error": str(exc)}, 400)', fn)

    def test_free_text_textarea_removed_for_a_labelled_slot_list(self):
        self.assertNotIn('id="window_prompts_text"', INDEX_HTML)
        self.assertIn('id="windowsDynamicSlots"', INDEX_HTML)

    def test_index_0_is_always_blank_by_construction(self):
        fn = CHARACTERS_JS[CHARACTERS_JS.index("function _windowsSlotChanged() {"):
                            CHARACTERS_JS.index("function _windowsSlotChanged() {") + 700]
        self.assertIn("[''].concat(boxes.map(", fn)

    def test_fetch_is_mode_guarded(self):
        fn = CHARACTERS_JS[CHARACTERS_JS.index("function windowPromptsInput() {"):
                            CHARACTERS_JS.index("function windowPromptsInput() {") + 1200]
        self.assertIn("if (tmode !== 'windows') return;", fn)
        self.assertIn("/ltx/windows_plan?frames=", fn)

    def test_docs_cover_ltx_windows(self):
        video_md = (ROOT / "webapp" / "docs" / "video.md").read_text()
        self.assertIn("Sliding windows on LTX", video_md)


# ---------------------------------------------------------------------------
# VA-30: "Split into N clips" — sequential a2v chain built on Continue the
# song. NEEDS RENDER CHECK (ledger_lipsync.txt) for the real multi-part
# chain; these tests pin the orchestration shape (never loses finished
# parts on a failure, always offers a cancel path).
# ---------------------------------------------------------------------------
class SplitIntoClips(unittest.TestCase):
    def test_split_function_present_and_wired_from_the_cap_warning(self):
        self.assertIn("async function audioStudioSplitIntoClips(capSec)", CHARACTERS_JS)
        self.assertIn("audioStudioSplitIntoClips(", CHARACTERS_JS)
        self.assertIn("audioStudioSplitIntoClips(' + capSec + ')", CHARACTERS_JS)

    def test_a_failed_part_stops_the_chain_without_losing_finished_ones(self):
        fn = CHARACTERS_JS[CHARACTERS_JS.index("async function audioStudioSplitIntoClips("):
                            CHARACTERS_JS.index("async function audioStudioGenerate(")]
        self.assertIn("already rendered are safe in Outputs", fn)
        self.assertIn("return;", fn)   # stops the loop, doesn't try the next part

    def test_continues_from_the_previous_parts_own_anchor(self):
        fn = CHARACTERS_JS[CHARACTERS_JS.index("async function audioStudioSplitIntoClips("):
                            CHARACTERS_JS.index("async function audioStudioGenerate(")]
        self.assertIn("/a2v/continue_song", fn)
        self.assertIn("imagePath = cres.anchor_image", fn)

    def test_stop_button_also_cancels_the_split_loop(self):
        # Stop goes through requestStop (VC-05's confirm) and breaks the
        # split loop only when the user actually stops.
        self.assertIn("requestStop({onStop: audioStudioSplitCancel})", INDEX_HTML)
        queue_js = (ROOT / "webapp" / "js" / "queue.js").read_text()
        fn = queue_js[queue_js.index("function requestStop(opts) {"):]
        fn = fn[:fn.index("\n}\n")]
        # (4.17 SAFETY-3: the stop names the job the dialog was opened for)
        self.assertIn("if (onStop) onStop();\n    api('/stop' + jobQ, 'POST')", fn)


# ---------------------------------------------------------------------------
# VA-38: a Windows chain that fails partway publishes what finished as a
# real, visible clip instead of leaving only hidden working files.
# NEEDS RENDER CHECK (ledger_lipsync.txt) for a real failure scenario.
# ---------------------------------------------------------------------------
class WindowsChainPartialPublish(unittest.TestCase):
    def test_run_windows_chain_wraps_the_loop_and_never_swallows_the_error(self):
        fn = PANEL_SRC[PANEL_SRC.index("def _run_windows_chain("):
                        PANEL_SRC.index("def _run_windows_chain(") + 8000]
        self.assertIn("except Exception as exc:", fn)
        self.assertIn("_publish_windows_partial(", fn)
        self.assertIn("raise exc", fn)

    def test_publish_partial_joins_and_marks_visible(self):
        fn = PANEL_SRC[PANEL_SRC.index("def _publish_windows_partial("):
                        PANEL_SRC.index("def _publish_windows_partial(") + 3000]
        self.assertNotIn("set_hidden(str(partial_out)", fn)   # stays visible, unlike working files
        self.assertIn('"windows_partial":', fn)
        self.assertIn("write_sidecar(", fn)

    def test_recovery_failure_cannot_mask_the_original_error(self):
        # The except-and-republish block itself is guarded — a bug in the
        # publish path must not turn a clear chain-failure error into a
        # confusing one from inside the recovery code.
        idx = PANEL_SRC.index("_publish_windows_partial(job, p, plan, pieces, completed,")
        surrounding = PANEL_SRC[idx - 200:idx + 300]
        self.assertIn("except Exception as pub_exc:", surrounding)

    def test_list_outputs_surfaces_windows_partial(self):
        self.assertIn('windows_partial = None', PANEL_SRC)
        self.assertIn('"windows_partial": windows_partial,', PANEL_SRC)

    def test_player_offers_continue_from_here(self):
        self.assertIn('id="windowsContinueBtn"', INDEX_HTML)
        self.assertIn("function windowsContinueFromPartial(", QUEUE_JS)
        self.assertIn("o.windows_partial", QUEUE_JS)


if __name__ == "__main__":
    unittest.main()
