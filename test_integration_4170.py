#!/usr/bin/env python3
"""Integration pins for the 4.17.0 mega merge — behaviour that only exists
because two packages' fixes meet in the same code path.

1. VA-28 (lipsync: record each One Shot part's real seed in
   take.parts_meta) x VA-17 (safety: resume a stopped One Shot from the next
   part). parts_meta is indexed by part and Load Params restores part 1's
   seed as the take's own seed, so a RESUMED take must carry the seeds of
   the parts it did not re-render — read from their sidecars — instead of
   starting the list at the first resumed part.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as p                                             # noqa: E402


def _tiny_clip(path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=32x32:d=0.2",
         "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "0.2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )


@pytest.fixture
def take_env(tmp_path, monkeypatch):
    out_dir = tmp_path / "out"; out_dir.mkdir()
    monkeypatch.setattr(p, "OUTPUT", out_dir)
    monkeypatch.setattr(p, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(p, "set_hidden", lambda *a, **k: None)
    monkeypatch.setattr(p, "take_drift", lambda *a, **k: {"ok": True, "delta": 0.0, "drifted": False})
    made = []

    def fake_h3(child):
        out = out_dir / f"{child['id']}.mp4"
        _tiny_clip(out)
        child["output_path"] = str(out)
        child["params"]["seed_used"] = 500 + len(made)
        made.append(out)

    monkeypatch.setattr(p, "run_h3_job_inner", fake_h3)
    return out_dir, made


def _job():
    return p.make_job({"mode": "t2v", "engine": "h3", "prompt": "a hen skates",
                       "take_seconds": "45",
                       "beats": json.dumps([f"b{i}" for i in range(9)])})


def _final_take(job) -> dict:
    final = Path(job["output_path"])
    return json.loads(final.with_suffix(final.suffix + ".json").read_text())["take"]


def test_a_fresh_take_records_every_parts_seed(take_env):
    _, made = take_env
    job = _job()
    p.run_take_job_inner(job)
    assert len(made) == 3
    assert _final_take(job)["parts_meta"] == [
        {"seed_used": 500}, {"seed_used": 501}, {"seed_used": 502}]


def test_a_resumed_take_keeps_the_seeds_of_the_parts_it_did_not_rerender(take_env):
    out_dir, made = take_env
    part1 = out_dir / "earlier-p1.mp4"
    _tiny_clip(part1)
    part1.with_suffix(".mp4.json").write_text(json.dumps({"seed_used": 111}))
    job = _job()
    job["params"]["take"]["_resume"] = {"outs": [str(part1)], "start_k": 1}
    p.run_take_job_inner(job)
    assert len(made) == 2                      # parts 2 and 3 only
    meta = _final_take(job)["parts_meta"]
    assert meta[0] == {"seed_used": 111}       # part 1's real seed, from its sidecar
    assert meta[1:] == [{"seed_used": 500}, {"seed_used": 501}]


# 2. The page's bootstrap is `const BOOT = __BOOTSTRAP__;` in index.html's
#    one inline <script>. A top-level `const` is NOT a property of `window`,
#    so `window.BOOT && BOOT.tier` is always {} in a real browser. est's
#    VA-11/12 read the keyframe/extend price cards that way, and every Mac —
#    a 64 GB M4 Max included — was told "Needs more memory than this Mac has"
#    for Keyframe and Extend (found driving the merged panel in Chrome; the
#    node harnesses define BOOT as a global, so they never saw it).
def test_no_module_reads_boot_through_window():
    root = Path(__file__).resolve().parent
    html = (root / "webapp" / "index.html").read_text(encoding="utf-8")
    assert "const BOOT = __BOOTSTRAP__;" in html
    offenders = [f"{js.name}:{i}" for js in sorted((root / "webapp" / "js").glob("*.js"))
                 for i, line in enumerate(js.read_text(encoding="utf-8").splitlines(), 1)
                 if "window.BOOT" in line]
    assert offenders == [], offenders


# 3. est's fleet range formatter printed _fmt_eta's " · batch" tail on BOTH
#    ends of an hours-long range ("~36 min · batch–1h 19m · batch"), which
#    the fleet-priced Extend Pro pill showed as soon as it was fleet-priced.
def test_an_hours_long_range_carries_one_batch_tail():
    out = p._fmt_eta_range(36.0, 79.0)
    assert out == "~36 min–1h 19m · batch", out
    assert out.count("batch") == 1


# 4. Extend and Keyframe are priced from the fleet's own wall clocks where
#    the fleet has them (est's cost model is the distilled-Q4 t2v model; it
#    had Extend at ~1.5 min on a 64 GB M4 Max against a fleet median of
#    ~20 min). The model only scales between Extend's variants.
def test_extend_and_keyframe_prices_follow_the_fleet(monkeypatch):
    fake = {"p25_min": 15.0, "p50_min": 20.0, "p75_min": 30.0,
            "basis": "based on 40 renders fleet-wide",
            "eta_range": "~15–30 min · batch", "eta_mid": "~20 min"}
    monkeypatch.setattr(p, "SYSTEM_CAPS", {**p.SYSTEM_CAPS, "allows_extend": True,
                                           "allows_keyframe": True})
    monkeypatch.setattr(p, "fleet_calibrated_range", lambda *a, **k: dict(fake))
    kf = p.ltx_mode_price_card("keyframe")
    assert kf["eta"] == fake["eta_range"] and kf["basis"] == fake["basis"]
    draft = p.ltx_mode_price_card("extend", steps=8)
    assert draft["eta_min"] == 20.0
    # more added seconds and more steps never price cheaper
    by = draft["eta_by_latents"]
    assert by["5"] == draft["eta"]
    pro = p.ltx_mode_price_card("extend", steps=30)
    assert pro["eta_min"] > draft["eta_min"]
    monkeypatch.setattr(p, "fleet_calibrated_range", lambda *a, **k: None)
    assert p.ltx_mode_price_card("keyframe")["basis"] == "estimated"


def test_a2v_estimate_route_prices_from_the_fleet(monkeypatch):
    from urllib.parse import urlencode
    from panel.routes import GET_ROUTES

    class H:
        out = None
        def _json(self, obj, code=200):
            self.out = (code, obj)

    class Parsed:
        def __init__(self, q):
            self.query = q

    fake = {"p25_min": 7.0, "p50_min": 10.0, "p75_min": 15.0,
            "basis": "based on 82 renders fleet-wide"}
    monkeypatch.setattr(p, "fleet_calibrated_range", lambda *a, **k: dict(fake))
    h = H()
    GET_ROUTES["/a2v/estimate"](h, Parsed(urlencode({"frames": 121})))
    code, body = h.out
    assert code == 200 and body["basis"] == fake["basis"]
    assert body["eta"] == "~7–15 min" and body["minutes"] == 10.0
    monkeypatch.setattr(p, "fleet_calibrated_range", lambda *a, **k: None)
    h = H()
    GET_ROUTES["/a2v/estimate"](h, Parsed(urlencode({"frames": 121})))
    assert h.out[1]["basis"] == "estimated"


# 5. SYS-16's form-side "Make your first clip" card and VC-41's empty-gallery
#    card were both `.first-run-card`. VC-41's later rule (centred column,
#    32 px padding, max-width 420 px) restyled SYS-16's card into a tall block
#    above the form, and the sticky Generate footer — which can only stick
#    inside its own <form> — ended up under the Now pane at 1440x900 and off
#    screen at 1366x768 on a first run (measured in Chrome; beta/main keeps
#    Generate in view). One class per component, the card lives inside
#    #genForm, and its prompts are chips.
def test_first_run_cards_do_not_share_a_class_and_generate_stays_reachable():
    root = Path(__file__).resolve().parent
    css = (root / "webapp" / "style" / "panel.css").read_text(encoding="utf-8")
    html = (root / "webapp" / "index.html").read_text(encoding="utf-8")
    queue_js = (root / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
    assert css.count(".first-run-card {") == 1
    assert "gallery-first-run" not in queue_js     # one first-run card ships (SYS-16's)
    form = html[html.index('<form id="genForm">'):]
    form = form[:form.index("</form>")]
    assert 'id="firstRunCard"' in form
    btn_rule = css[css.index(".first-run-prompt-btn {"):]
    btn_rule = btn_rule[:btn_rule.index("}")]
    assert "width: auto" in btn_rule


# 6. VC-31 (gallery poster: one arg, 2.5 s frame, None on failure) and
#    FILM-39 (Editor filmstrip poster: width arg, proxy's first frame,
#    raises) were both `_ensure_video_poster`. The later definition silently
#    replaced the first, and the gallery's /poster route — which calls it
#    with one argument — raised TypeError for every card.
def test_gallery_poster_route_serves_a_poster(tmp_path, monkeypatch):
    import inspect
    assert list(inspect.signature(p._ensure_video_poster).parameters) == ["src"]
    assert list(inspect.signature(p._ensure_proxy_poster).parameters) == ["src", "width"]
    clip = p.OUTPUT / "int4170_poster_probe.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    _tiny_clip(clip)
    try:
        poster = p._ensure_video_poster(clip)
        assert poster is not None and poster.stat().st_size > 0
    finally:
        clip.unlink(missing_ok=True)
