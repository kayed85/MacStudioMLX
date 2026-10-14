#!/usr/bin/env python3
"""Contract gate: an Extend delivers the clip you had, untouched (issue #48).

Before this, every round of an extend chain delivered the model pipeline's own
copy of the source — VAE + vocoder round trip (LTX) or decode + canvas resample
+ 32 kHz resample (H3), then a fresh lossy encode — over the WHOLE accumulated
clip, so a chain compounded into a picture "made out of particles" and mushy
audio. `extend_splice` keeps the source's H.264 packets bit-exact when it can,
decodes it exactly once when it cannot, and encodes the soundtrack once from a
lossless master.

What is pinned here, with real ffmpeg on synthetic clips (no GPU, no model):
* the frame layout each engine's output has (LTX 1+8k, before/after; H3);
* the crossfade: the source PCM before the fade and the generated PCM after the
  seam come through sample-exact;
* a 2-round chain keeps the ORIGINAL frames bit-exact and its source-region
  audio PCM bit-exact (master), and keeps the >8 kHz content;
* every way the copy is impossible (frame count off the 1+8k grid, a changed
  output preset, another canvas, a silent source) still yields a verified file;
* the panel's wiring: the H3 lane continues the native render and asks the
  runner for a lossless intermediate, and a failed splice still delivers.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
_TMP = Path(tempfile.mkdtemp(prefix="phos-extend-splice-"))
for _k, _d in (("LTX_STATE_DIR", "state"), ("LTX_OUTPUT_DIR", "out"),
               ("LTX_UPLOADS_DIR", "uploads")):
    (_TMP / _d).mkdir(parents=True, exist_ok=True)
    os.environ[_k] = str(_TMP / _d)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8304")
sys.path.insert(0, str(ROOT))

import extend_splice as X  # noqa: E402

SR = X.SAMPLE_RATE


def _tool(name: str) -> str | None:
    for cand in (shutil.which(name), f"/opt/homebrew/bin/{name}",
                 str(Path.home() / f"pinokio/bin/miniforge/bin/{name}")):
        if cand and Path(cand).is_file():
            return cand
    return None


FF, FP = _tool("ffmpeg"), _tool("ffprobe")


def ff(*args: str) -> None:
    subprocess.run([FF, "-v", "error", "-y", *args], check=True, capture_output=True)


def md5s(path: Path, first: int = 0, count: int | None = None) -> list[str]:
    out = subprocess.run([FF, "-v", "error", "-i", str(path), "-map", "0:v:0", "-f", "framemd5", "-"],
                         capture_output=True, text=True, check=True).stdout
    rows = [ln.rsplit(",", 1)[1].strip() for ln in out.splitlines()
            if ln and not ln.startswith("#")]
    return rows[first:None if count is None else first + count]


def pcm(path: Path) -> np.ndarray:
    raw = subprocess.run([FF, "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "2",
                          "-ar", str(SR), "-f", "f32le", "-"], capture_output=True,
                         check=True).stdout
    return np.frombuffer(raw, "<f4").reshape(-1, 2).T


def hf_share(x: np.ndarray, lo: float = 8000.0) -> float:
    m = x.mean(axis=0)
    spec = np.abs(np.fft.rfft(m)) ** 2
    f = np.fft.rfftfreq(m.size, 1 / SR)
    return float(spec[f >= lo].sum() / (spec.sum() + 1e-20))


# ---------------------------------------------------------------------------
# Pure layout + PCM logic
# ---------------------------------------------------------------------------

class Layouts(unittest.TestCase):
    def test_ltx_uses_the_vae_grid(self):
        self.assertEqual(X.ltx_source_frames(121), 121)
        self.assertEqual(X.ltx_source_frames(124), 121)   # an H3 5 s clip: 3 dropped
        self.assertEqual(X.ltx_source_frames(1), 1)

    def test_ltx_after(self):
        lay = X.ltx_layout(121, 161)
        self.assertEqual((lay.source_frames, lay.new_start, lay.new_end), (121, 121, 161))
        self.assertEqual((lay.total_frames, lay.seam_frame), (161, 121))

    def test_ltx_after_on_an_off_grid_source(self):
        lay = X.ltx_layout(124, 161)                       # model saw 121 of 124
        self.assertEqual((lay.source_frames, lay.new_frames), (121, 40))

    def test_ltx_before(self):
        lay = X.ltx_layout(121, 161, "before")
        self.assertFalse(lay.source_first)
        self.assertEqual((lay.new_start, lay.new_end, lay.seam_frame), (0, 40, 40))

    def test_h3(self):
        lay = X.h3_layout(124 + 120, 120)
        self.assertEqual((lay.source_frames, lay.new_start, lay.total_frames), (124, 124, 244))

    def test_nothing_new_is_refused(self):
        with self.assertRaises(X.SpliceError):
            X.ltx_layout(121, 121)
        with self.assertRaises(X.SpliceError):
            X.h3_layout(50, 0)


class Crossfade(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(1)
        self.src = rng.standard_normal((2, 1000)).astype(np.float32)
        self.gen = rng.standard_normal((2, 1600)).astype(np.float32)

    def test_after_keeps_both_sides_sample_exact(self):
        out = X.crossfade_splice(self.src, self.gen, seam=1000, fade=100)
        self.assertEqual(out.shape, (2, 1600))
        np.testing.assert_array_equal(out[:, :900], self.src[:, :900])
        np.testing.assert_array_equal(out[:, 1000:], self.gen[:, 1000:])
        # linear: the weights of the two inputs sum to one across the fade
        k = 950
        w = (k - 900 + 0.5) / 100
        np.testing.assert_allclose(out[:, k], self.src[:, k] * (1 - w) + self.gen[:, k] * w,
                                   rtol=1e-6)

    def test_short_source_hands_over_where_it_ends(self):
        out = X.crossfade_splice(self.src[:, :600], self.gen, seam=1000, fade=100)
        self.assertEqual(out.shape[1], 1600)
        np.testing.assert_array_equal(out[:, :500], self.src[:, :500])
        np.testing.assert_array_equal(out[:, 600:], self.gen[:, 600:])

    def test_before(self):
        out = X.crossfade_splice(self.src, self.gen, seam=600, fade=100, source_first=False)
        self.assertEqual(out.shape[1], 1600)
        np.testing.assert_array_equal(out[:, :600], self.gen[:, :600])
        np.testing.assert_array_equal(out[:, 700:], self.src[:, 100:])

    def test_fit_length(self):
        self.assertEqual(X.fit_length(self.src, 1200).shape, (2, 1200))
        self.assertEqual(X.fit_length(self.src, 10).shape, (2, 10))


# ---------------------------------------------------------------------------
# Real files
# ---------------------------------------------------------------------------

def _encode_like_the_helper(src_args: list[str], out: Path, frames: int,
                            pix_fmt: str = "yuv420p", crf: str = "18",
                            audio: bool = True, size: str = "256x160",
                            rate: int = 24) -> Path:
    """A clip shaped like an LTX render: libx264 at the preset, no colour tags,
    AAC at ffmpeg's default; a 440 Hz + 11 kHz tone so >8 kHz content exists."""
    secs = f"{frames / rate:.6f}"
    args = ["-f", "lavfi", "-t", secs, "-i", f"{src_args[0]}=size={size}:rate={rate}"]
    if audio:
        args += ["-f", "lavfi", "-t", secs, "-i",
                 f"aevalsrc=0.3*sin(2*PI*440*t)+0.1*sin(2*PI*11000*t)|"
                 f"0.3*sin(2*PI*440*t)+0.1*sin(2*PI*11000*t):s={SR}"]
    args += ["-frames:v", str(frames), "-c:v", "libx264", "-pix_fmt", pix_fmt, "-crf", crf]
    if audio:
        args += ["-c:a", "aac"]
    ff(*args, "-movflags", "+faststart", str(out))
    return out


def _generated(source: Path, new: int, out: Path, wav: Path, size: str = "256x160",
               keep: int | None = None) -> None:
    """What the helper hands the panel: the source's frames (as the model
    decodes them — here a slight blur, so nothing can pass by accident) plus
    `new` frames of other content, lossless; and a 48 kHz wav of the same
    timeline whose new part is a different tone."""
    ff("-i", str(source), "-f", "lavfi", "-i", f"mandelbrot=size={size}:rate=24",
       "-filter_complex",
       f"[0:v]trim=end_frame={keep or 100000},scale={size.replace('x', ':')},setsar=1,"
       f"gblur=sigma=0.6,format=yuv444p[a];"
       f"[1:v]trim=end_frame={new},setsar=1,format=yuv444p[b];"
       f"[a][b]concat=n=2:v=1:a=0[v]", "-map", "[v]",
       "-c:v", "libx264", "-pix_fmt", "yuv444p", "-crf", "0", str(out))
    n = int(X.probe(FP, out)["frames"])
    ff("-f", "lavfi", "-i",
       f"aevalsrc=if(lt(t\\,{(n - new) / 24:.6f})\\,0.25*sin(2*PI*440*t)\\,0.25*sin(2*PI*660*t)):s={SR}",
       "-t", f"{n / 24 + 0.04:.6f}", "-ac", "2", str(wav))


@unittest.skipUnless(FF and FP, "ffmpeg/ffprobe not available")
class Splice(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(dir=_TMP))

    def splice(self, source, generated, wav, out, layout, **kw):
        return X.splice(ffmpeg=FF, ffprobe=FP, source=source, generated=generated,
                        generated_audio=wav, out=out, layout=layout,
                        work=self.d / f"work_{out.stem}", pix_fmt=kw.pop("pix_fmt", "yuv420p"),
                        crf=kw.pop("crf", "18"), **kw)

    def round(self, source: Path, new: int, tag: str, master: Path | None = None, **kw):
        gen, wav = self.d / f"{tag}_gen.mp4", self.d / f"{tag}_gen.wav"
        n_src = X.probe(FP, source)["frames"]
        # Like the model: only the first 1 + 8k source frames come back.
        _generated(source, new, gen, wav, keep=X.ltx_source_frames(n_src))
        lay = X.ltx_layout(n_src, X.probe(FP, gen)["frames"], kw.pop("direction", "after"))
        out = self.d / f"{tag}.mp4"
        rep = self.splice(source, gen, wav, out, lay, source_master=master,
                          master_out=self.d / f"{tag}.flac", **kw)
        return out, rep, lay

    def test_a_chain_keeps_the_original_bit_exact(self):
        orig = _encode_like_the_helper(["testsrc2"], self.d / "orig.mp4", 49)
        r1, rep1, _ = self.round(orig, 16, "r1")
        self.assertEqual(rep1["video"], "copy")
        self.assertEqual(rep1["audio"], "decoded")
        self.assertEqual(X.probe(FP, r1)["frames"], 65)
        self.assertEqual(md5s(r1, 0, 49), md5s(orig))
        r2, rep2, _ = self.round(r1, 16, "r2", master=self.d / "r1.flac")
        self.assertEqual(rep2["video"], "copy")
        self.assertEqual(rep2["audio"], "master")
        self.assertEqual(X.probe(FP, r2)["frames"], 81)
        self.assertEqual(md5s(r2, 0, 49), md5s(orig))          # round 0 frames
        self.assertEqual(md5s(r2, 0, 65), md5s(r1))            # all of round 1
        # The master's source region is carried sample-exact (up to the fade).
        m1, m2 = pcm(self.d / "r1.flac"), pcm(self.d / "r2.flac")
        keep = int((65 / 24 - X.FADE_SECONDS) * SR)
        np.testing.assert_array_equal(m2[:, :keep], m1[:, :keep])
        # ...and so is everything >8 kHz in it (the vocoder would have re-invented it).
        o = pcm(orig)[:, : int(1.9 * SR)]
        self.assertGreater(hf_share(o), 0.01)
        self.assertAlmostEqual(hf_share(pcm(r2)[:, : o.shape[1]]), hf_share(o), delta=0.01)
        # Delivery: H.264 + AAC, faststart, audio exactly as long as the picture.
        info = subprocess.run([FP, "-v", "error", "-show_entries", "stream=codec_name,duration",
                               "-of", "json", str(r2)], capture_output=True, text=True).stdout
        streams = json.loads(info)["streams"]
        self.assertEqual([s["codec_name"] for s in streams], ["h264", "aac"])
        self.assertAlmostEqual(float(streams[1]["duration"]), 81 / 24, delta=0.03)

    def test_the_new_frames_are_the_models(self):
        orig = _encode_like_the_helper(["testsrc2"], self.d / "o.mp4", 49)
        r1, _, _ = self.round(orig, 16, "n1")
        gen = self.d / "n1_gen.mp4"
        res = subprocess.run(
            [FF, "-i", str(r1), "-i", str(gen), "-lavfi",
             "[0:v]trim=start_frame=49,setpts=PTS-STARTPTS,format=yuv420p[a];"
             "[1:v]trim=start_frame=49,setpts=PTS-STARTPTS,format=yuv420p[b];[a][b]psnr",
             "-f", "null", "-"], capture_output=True, text=True).stderr
        psnr = float(res.rsplit("average:", 1)[1].split()[0])
        self.assertGreater(psnr, 35.0)

    def test_off_grid_source_is_decoded_once_and_trimmed(self):
        orig = _encode_like_the_helper(["testsrc2"], self.d / "o52.mp4", 52)   # 1+8k = 49
        out, rep, lay = self.round(orig, 16, "g1")
        self.assertEqual(rep["video"], "reencode")
        self.assertEqual(lay.source_frames, 49)
        self.assertEqual(X.probe(FP, out)["frames"], 65)

    def test_a_changed_preset_cannot_be_copied(self):
        orig = _encode_like_the_helper(["testsrc2"], self.d / "o.mp4", 49, crf="18")
        out, rep, _ = self.round(orig, 16, "p1", crf="23")
        self.assertEqual(rep["video"], "reencode")
        self.assertEqual(X.probe(FP, out)["frames"], 65)
        # ...and the round after it copies again (the chain heals in one hop).
        _, rep2, _ = self.round(out, 16, "p2", crf="23", master=self.d / "p1.flac")
        self.assertEqual(rep2["video"], "copy")

    def test_lossless_preset_chain_copies(self):
        orig = _encode_like_the_helper(["testsrc2"], self.d / "o444.mp4", 49,
                                       pix_fmt="yuv444p", crf="0")
        out, rep, _ = self.round(orig, 16, "l1", pix_fmt="yuv444p", crf="0")
        self.assertEqual(rep["video"], "copy")
        self.assertEqual(md5s(out, 0, 49), md5s(orig))

    def test_another_canvas_delivers_the_models_frames(self):
        orig = _encode_like_the_helper(["testsrc2"], self.d / "big.mp4", 49, size="320x192")
        gen, wav = self.d / "c_gen.mp4", self.d / "c_gen.wav"
        small = self.d / "small.mp4"
        ff("-i", str(orig), "-vf", "scale=256:160", "-c:v", "libx264", "-crf", "0", str(small))
        _generated(small, 16, gen, wav)
        lay = X.ltx_layout(49, X.probe(FP, gen)["frames"])
        rep = self.splice(orig, gen, wav, self.d / "c.mp4", lay)
        self.assertEqual(rep["video"], "generated")
        self.assertEqual(rep["audio"], "decoded")      # the soundtrack still splices
        self.assertEqual(X.probe(FP, self.d / "c.mp4")["frames"], 65)

    def test_silent_source(self):
        orig = _encode_like_the_helper(["testsrc2"], self.d / "mute.mp4", 49, audio=False)
        out, rep, _ = self.round(orig, 16, "s1")
        self.assertEqual(rep["audio"], "silent-source")
        a = pcm(out)
        self.assertLess(float(np.abs(a[:, : int(1.9 * SR)]).max()), 1e-4)
        self.assertGreater(float(np.abs(a[:, int(2.2 * SR):]).max()), 0.1)

    def test_extend_before_puts_the_source_last(self):
        orig = _encode_like_the_helper(["testsrc2"], self.d / "o.mp4", 49)
        gen, wav = self.d / "b_gen.mp4", self.d / "b_gen.wav"
        # the model's "before" output: 16 new frames, then its copy of the source
        ff("-f", "lavfi", "-i", "mandelbrot=size=256x160:rate=24", "-i", str(orig),
           "-filter_complex", "[0:v]trim=end_frame=16,format=yuv444p[a];[1:v]format=yuv444p[b];"
           "[a][b]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-c:v", "libx264", "-pix_fmt",
           "yuv444p", "-crf", "0", str(gen))
        ff("-f", "lavfi", "-i", f"sine=f=660:r={SR}", "-t", "2.75", "-ac", "2", str(wav))
        lay = X.ltx_layout(49, 65, "before")
        out = self.d / "b.mp4"
        rep = self.splice(orig, gen, wav, out, lay)
        self.assertEqual(rep["video"], "copy")
        self.assertEqual(md5s(out, 16, 49), md5s(orig))

    def test_h3_delivery_codec_intermediate_is_copied_through(self):
        orig = _encode_like_the_helper(["testsrc2"], self.d / "h.mp4", 49, size="320x192")
        gen, wav = self.d / "h_gen.mp4", self.d / "h_gen.wav"
        _generated(orig, 24, gen, wav, size="256x160")
        ff("-i", str(gen), "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
           str(self.d / "h_gen18.mp4"))
        lay = X.h3_layout(X.probe(FP, gen)["frames"], 24)
        out = self.d / "h1.mp4"
        X.splice(ffmpeg=FF, ffprobe=FP, source=orig, generated=self.d / "h_gen18.mp4",
                 generated_audio=wav, out=out, layout=lay, work=self.d / "hw",
                 pix_fmt="yuv420p", crf="18", generated_lossless=False)
        self.assertEqual(md5s(out), md5s(self.d / "h_gen18.mp4"))   # copied, not re-encoded

    def test_a_stale_master_is_not_trusted(self):
        orig = _encode_like_the_helper(["testsrc2"], self.d / "o.mp4", 49)
        stale = self.d / "stale.flac"
        ff("-f", "lavfi", "-i", f"sine=f=1000:r={SR}", "-t", "5", "-ac", "2", str(stale))
        _, rep, _ = self.round(orig, 16, "st", master=stale)
        self.assertEqual(rep["audio"], "decoded")


# ---------------------------------------------------------------------------
# Panel wiring
# ---------------------------------------------------------------------------

import mlx_ltx_panel as P  # noqa: E402


@unittest.skipUnless(FF and FP, "ffmpeg/ffprobe not available")
class PanelDelivery(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(dir=_TMP))
        self.logs: list[str] = []
        self.patches = [unittest.mock.patch.object(P, "push", self.logs.append),
                        unittest.mock.patch.object(P, "FFMPEG", Path(FF)),
                        unittest.mock.patch.object(P, "FFPROBE", Path(FP)),
                        unittest.mock.patch.object(
                            P, "run_ffmpeg_tracked",
                            lambda cmd, label: subprocess.run(
                                [str(c) for c in cmd], check=True, capture_output=True))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_master_is_found_through_the_picked_exports_sidecar(self):
        native, export = self.d / "clip.mp4", self.d / "clip_720p.mp4"
        master = self.d / "m.flac"
        for f in (native, export, master):
            f.write_bytes(b"x" * 2048)
        (self.d / "clip_720p.mp4.json").write_text(json.dumps(
            {"native_output": str(native), "extend_audio_master": str(master)}))
        self.assertIsNone(P._extend_audio_master_for(native))
        self.assertEqual(P._extend_audio_master_for(export), master)

    def test_a_failed_splice_still_delivers_the_models_output(self):
        orig = _encode_like_the_helper(["testsrc2"], self.d / "o.mp4", 49)
        gen, wav = self.d / "gen.mp4", self.d / "gen.wav"
        _generated(orig, 16, gen, wav)
        out = self.d / "out.mp4"
        with unittest.mock.patch.object(X, "splice", side_effect=X.SpliceError("boom")):
            rep = P.deliver_extend(orig, gen, wav, out, self.d / "w", engine="ltx",
                                   codec={"pix_fmt": "yuv420p", "crf": "18"})
        self.assertEqual(rep["video"], "fallback")
        self.assertEqual(X.probe(FP, out)["frames"], 65)
        self.assertTrue(any("boom" in ln for ln in self.logs))

    def test_deliver_extend_writes_the_master_where_the_sidecar_points(self):
        orig = _encode_like_the_helper(["testsrc2"], self.d / "o.mp4", 49)
        gen, wav = self.d / "gen.mp4", self.d / "gen.wav"
        _generated(orig, 16, gen, wav)
        out = P.OUTPUT / "o_ext2_x.mp4"
        work = self.d / "work"
        rep = P.deliver_extend(orig, gen, wav, out, work, engine="ltx",
                               codec={"pix_fmt": "yuv420p", "crf": "18"})
        self.assertEqual(rep["video"], "copy")
        self.assertTrue(Path(rep["audio_master"]).is_file())
        self.assertIn(f"{os.sep}.extend{os.sep}masters{os.sep}", rep["audio_master"])
        self.assertFalse(work.exists())                      # intermediates cleaned up


class H3Wiring(unittest.TestCase):
    """The H3 lane continues the NATIVE render and gets a lossless intermediate."""

    def setUp(self):
        sys.path.insert(0, str(ROOT))
        import test_h3_capabilities as T                        # noqa: PLC0415
        self.T = T

    def _dispatch(self, flags, params):
        T = self.T

        class E(T._Env):
            pass
        E.flags = flags
        e = E("run")
        e.setUp()
        try:
            return e.dispatch(params)
        finally:
            e.tearDown()

    def test_native_render_and_lossless_intermediate(self):
        native = self.T._media("ext_native.mp4")
        native.write_bytes(b"\0" * 4096)
        export = self.T._media("ext_native_720p.mp4")
        Path(str(export) + ".json").write_text(json.dumps({"native_output": str(native)}))
        cmd, _ = self._dispatch(self.T.ALL_FLAGS + ("--crf",),
                                {"mode": "extend", "h3_tier": "high_5s",
                                 "video_path": str(export), "h3_extend_seconds": 5})
        self.assertEqual(cmd[cmd.index("--extend-from") + 1], str(native))
        self.assertEqual(cmd[cmd.index("--crf") + 1], "0")
        self.assertEqual(cmd.count("--crf"), 1)

    def test_an_old_runner_without_crf_is_not_asked(self):
        cmd, _ = self._dispatch(self.T.ALL_FLAGS,
                                {"mode": "extend", "video_path": str(self.T._media("e2.mp4"))})
        self.assertNotIn("--crf", cmd)


@unittest.skipUnless(FF and FP, "ffmpeg/ffprobe not found")
class AudioStartOffset(unittest.TestCase):
    """Codex 4.19.0: a source whose sound starts later than its first frame
    kept its PCM from sample 0, so the source part played early after a
    splice. The PCM is now placed on the video's timeline."""

    def test_a_late_audio_start_is_padded_onto_the_video_clock(self):
        d = Path(tempfile.mkdtemp(prefix="offs-", dir=_TMP))
        tone = d / "tone.wav"
        ff("-f", "lavfi", "-i", f"sine=frequency=440:sample_rate={SR}:duration=1", "-ac", "2", str(tone))
        clip = d / "late.mp4"
        ff("-f", "lavfi", "-i", "color=c=gray:s=64x64:r=24:d=2", "-itsoffset", "0.5", "-i", str(tone),
           "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
           str(clip))
        info = X.probe(FP, clip)
        self.assertAlmostEqual(info["audio_offset"], 0.5, delta=0.05)
        run = lambda cmd, label: subprocess.run(cmd, check=True, capture_output=True)  # noqa: E731
        x = X._read_pcm(run, FF, clip, d / "a.f32", "t", offset=info["audio_offset"])
        lead = np.abs(x[:, : int(0.45 * SR)]).max()
        tone_at = np.abs(x[:, int(0.55 * SR): int(0.9 * SR)]).max()
        self.assertLess(lead, 1e-3)          # silence where the sound had not started
        self.assertGreater(tone_at, 0.05)    # the tone where it really plays (sine at 1/8)


if __name__ == "__main__":
    unittest.main()
