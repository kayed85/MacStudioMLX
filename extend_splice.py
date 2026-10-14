"""Extend delivery: the clip you already had, untouched, plus the part the model made.

Issue #48 — "progressive audio/video distortion when using Extend".

WHY THIS EXISTS. Extend conditions the model on the source clip, and the
model never re-denoises that region (LTX carries it with ``denoise_mask = 0``;
H3 holds it as window 0 of the timeline). But both engines used to DELIVER the
source region as the model pipeline's own copy of it, so every round put the
whole accumulated clip through one more lossy generation:

* LTX: the entire clip was VAE-encoded and decoded again, its audio resampled
  to 16 kHz for the audio VAE and re-synthesised to 48 kHz by the vocoder
  (everything above 8 kHz discarded and re-invented), and the result written
  at the output preset (yuv420p / CRF 18 by default) — per round, over the
  whole clip, compounding.
* H3: the source was decoded, cover-scaled onto the canvas, its audio
  resampled to 32 kHz, and the whole clip re-encoded at CRF 18 + AAC — again
  per round, and the gallery's 720p export (which the Extend picker offers)
  added a lanczos down-and-up resample on top.

The fix keeps the model's conditioning exactly as it was and changes only the
DELIVERY: the source region is the source file itself, the new region is what
the model made, joined at the seam.

* Video — the source's H.264 packets are copied bit-exact (concat demuxer,
  ``-c copy``) whenever the source can be continued that way: same codec,
  canvas and frame rate, every one of its frames used by the model, and the
  new part's encoder parameter sets (``avcC``) byte-identical to the source's.
  Otherwise (a first hop from a foreign file, a downscaled copy, a changed
  output preset) the source is decoded ONCE from the file the user picked and
  encoded once together with the new part — a single generation, after which
  the next round is a copy again.
* Audio — AAC cannot be concatenated without its priming/padding showing at
  the splice, and re-encoding the previous round's AAC accumulates (measured:
  34.1 -> 32.2 -> 29.8 dB SNR over three tandem rounds at 192 kb/s). So every
  extend output keeps a lossless 48 kHz FLAC "audio master" in a dot-folder,
  and each round's AAC is encoded ONCE from master PCM. The source region of
  that PCM is byte-identical round after round, so the delivered audio is
  always exactly one generation from what the user had — never two.
* Seam — a short linear crossfade (40 ms, ending on the seam) from the
  source audio into the generated track. Linear, not equal-power: just before
  the seam the generated track is the model's re-synthesis of the SAME sound,
  so the two signals are correlated and an equal-power pair would bump the
  level. No video blend: the first new frame continues the model's decode of
  the last source frame, and the measured seam jump is reported per job.

This module is pure apart from ffmpeg/ffprobe: every ffmpeg call goes through
the ``run(cmd, label)`` callable the caller passes (the panel passes its
cancellable ``run_ffmpeg_tracked``), so it is unit-testable on synthetic clips
without a GPU.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

SAMPLE_RATE = 48000
CHANNELS = 2
FADE_SECONDS = 0.040
AUDIO_BITRATE = "192k"
# LTX's video VAE is causal in time: latent frame 0 holds one pixel frame and
# every later latent holds 8, so the extend pipeline only ever sees the first
# 1 + 8k frames of its source (retake.py `_encode_source_video`).
VAE_TIME_STRIDE = 8

Runner = Callable[[list, str], object]


class SpliceError(RuntimeError):
    """The splice could not produce a verified file (the caller keeps the
    model's own output, i.e. the pre-#48 delivery, and says so)."""


# --------------------------------------------------------------------------
# Layout: which frames of which file go where
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Layout:
    source_frames: int      # frames of the SOURCE file the delivery keeps
    new_start: int          # first generated frame that is new
    new_end: int            # one past the last generated frame that is new
    source_first: bool      # True = extend after, False = extend before

    @property
    def new_frames(self) -> int:
        return self.new_end - self.new_start

    @property
    def total_frames(self) -> int:
        return self.source_frames + self.new_frames

    @property
    def seam_frame(self) -> int:
        """Index in the DELIVERED clip of the first frame after the seam."""
        return self.source_frames if self.source_first else self.new_frames


def ltx_source_frames(n_source: int) -> int:
    """How many leading frames of an n-frame source the LTX extend pipeline
    encodes (1 + 8k, rounded down; any remainder is dropped by the model)."""
    if n_source < 1:
        raise SpliceError("the source has no frames")
    return 1 + VAE_TIME_STRIDE * max(0, (n_source - 1) // VAE_TIME_STRIDE)


def ltx_layout(n_source: int, n_generated: int, direction: str = "after") -> Layout:
    """LTX extend: the decoded output holds the K = 1+8k source frames plus
    8 per new latent, new ones after (or before) the source region."""
    k = ltx_source_frames(n_source)
    new = n_generated - k
    if new < 1:
        raise SpliceError(f"the model returned {n_generated} frames for a "
                          f"{k}-frame source — no new frames to splice")
    if direction == "before":
        return Layout(source_frames=k, new_start=0, new_end=new, source_first=False)
    return Layout(source_frames=k, new_start=k, new_end=n_generated, source_first=True)


def h3_layout(n_generated: int, new_frames: int) -> Layout:
    """H3 extend: the runner delivers its source frames followed by exactly
    ``new_frames`` new ones (`--extend-frames`). Forward only."""
    src = n_generated - int(new_frames)
    if src < 1 or new_frames < 1:
        raise SpliceError(f"the H3 runner returned {n_generated} frames for "
                          f"{new_frames} new ones — nothing to splice")
    return Layout(source_frames=src, new_start=src, new_end=n_generated, source_first=True)


# --------------------------------------------------------------------------
# Audio: pure numpy
# --------------------------------------------------------------------------

def crossfade_splice(source: np.ndarray, generated: np.ndarray, seam: int,
                     fade: int, source_first: bool = True) -> np.ndarray:
    """Join source PCM and generated PCM at sample ``seam`` of the delivered
    timeline, both ``(channels, samples)`` float32.

    ``source_first`` (extend after): ``generated`` is on the delivered
    timeline (sample 0 = clip start, its first ``seam`` samples are the
    model's copy of the source). Output = source[:seam-fade], a linear fade
    from source to generated over [seam-fade, seam), then generated[seam:].
    A source shorter than the seam hands over where it ends instead.

    Extend before: ``generated`` holds the new part in [0, seam) followed by
    the model's copy of the source; ``source`` starts at the seam. Output =
    generated[:seam], a fade from generated into the source over its first
    ``fade`` samples, then the rest of the source.
    """
    source = np.asarray(source, dtype=np.float32)
    generated = np.asarray(generated, dtype=np.float32)
    if source.ndim != 2 or generated.ndim != 2 or source.shape[0] != generated.shape[0]:
        raise SpliceError("source and generated audio must both be (channels, samples)")
    seam = max(0, int(seam))
    if source_first:
        hand = min(seam, source.shape[1], generated.shape[1])
        f = max(0, min(int(fade), hand))
        head = source[:, :hand - f]
        if f:
            w = ((np.arange(f, dtype=np.float32) + 0.5) / f)[None, :]
            mid = source[:, hand - f:hand] * (1.0 - w) + generated[:, hand - f:hand] * w
        else:
            mid = np.zeros((source.shape[0], 0), np.float32)
        return np.concatenate([head, mid, generated[:, hand:]], axis=1)
    hand = min(seam, generated.shape[1])
    f = max(0, min(int(fade), source.shape[1], generated.shape[1] - hand))
    if f:
        w = ((np.arange(f, dtype=np.float32) + 0.5) / f)[None, :]
        mid = generated[:, hand:hand + f] * (1.0 - w) + source[:, :f] * w
    else:
        mid = np.zeros((source.shape[0], 0), np.float32)
    return np.concatenate([generated[:, :hand], mid, source[:, f:]], axis=1)


def fit_length(pcm: np.ndarray, samples: int) -> np.ndarray:
    """Pad with silence or cut to exactly ``samples``."""
    samples = max(0, int(samples))
    if pcm.shape[1] >= samples:
        return np.ascontiguousarray(pcm[:, :samples])
    return np.pad(pcm, ((0, 0), (0, samples - pcm.shape[1])))


# --------------------------------------------------------------------------
# Probing
# --------------------------------------------------------------------------

def probe(ffprobe: str | Path, path: str | Path) -> dict:
    """Codec, canvas, pix_fmt, frame rate, frame count (packets) and whether
    there is an audio stream. Raises SpliceError on an unreadable file."""
    r = subprocess.run(
        [str(ffprobe), "-v", "error", "-count_packets", "-show_entries",
         "stream=codec_type,codec_name,width,height,pix_fmt,avg_frame_rate,"
         "r_frame_rate,nb_read_packets,start_time", "-of", "json", str(path)],
        capture_output=True, text=True, errors="replace", timeout=120)
    try:
        streams = json.loads(r.stdout or "{}").get("streams") or []
    except ValueError:
        streams = []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if r.returncode != 0 or v is None:
        raise SpliceError(f"cannot read the video stream of {Path(path).name}")

    def _rate(s: str | None) -> float:
        num, _, den = str(s or "0/1").partition("/")
        try:
            return float(num) / float(den or 1)
        except (ValueError, ZeroDivisionError):
            return 0.0

    fps = _rate(v.get("avg_frame_rate")) or _rate(v.get("r_frame_rate"))
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)

    def _start(s: dict | None) -> float:
        try:
            return float((s or {}).get("start_time") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    return {
        # Where the audio starts on the VIDEO's clock (Codex 4.19.0): an
        # imported clip whose sound starts later (or earlier) than its first
        # frame must not have that offset dropped when it is decoded to PCM.
        "audio_offset": (_start(a) - _start(v)) if a is not None else 0.0,
        "codec": v.get("codec_name"), "width": int(v.get("width") or 0),
        "height": int(v.get("height") or 0), "pix_fmt": v.get("pix_fmt"),
        "fps": fps, "frames": int(v.get("nb_read_packets") or 0),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def avcc(path: str | Path) -> bytes | None:
    """The H.264 decoder configuration record (SPS + PPS) of an mp4, or None.

    Two streams can only be joined packet-for-packet when this is
    byte-identical: the joined file carries ONE sample description, so a new
    part whose parameter sets differ (another CRF, profile, colour tags, x264
    build) would be decoded with the source's and break."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    i = data.find(b"avcC")
    if i < 4:
        return None
    size = int.from_bytes(data[i - 4:i], "big")
    if size < 8 or i - 4 + size > len(data):
        return None
    return data[i + 4:i - 4 + size]


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

def _read_pcm(run: Runner, ffmpeg: str, src: Path, raw: Path, label: str,
              offset: float = 0.0) -> np.ndarray:
    """The audio as (channels, samples) on the VIDEO's timeline: ``offset``
    seconds (audio start minus video start) are padded with silence when
    positive and trimmed when negative, so sample 0 is the first frame's."""
    run([ffmpeg, "-y", "-v", "error", "-i", str(src), "-map", "0:a:0", "-vn",
         "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE), "-f", "f32le", str(raw)], label)
    data = np.fromfile(raw, dtype="<f4")
    data = data[: data.size - data.size % CHANNELS]
    pcm = np.ascontiguousarray(data.reshape(-1, CHANNELS).T)
    shift = int(round(float(offset or 0.0) * SAMPLE_RATE))
    if shift > 0:
        pcm = np.concatenate([np.zeros((CHANNELS, shift), dtype=pcm.dtype), pcm], axis=1)
    elif shift < 0:
        pcm = pcm[:, -shift:]
    return np.ascontiguousarray(pcm)


def _write_master(run: Runner, ffmpeg: str, pcm: np.ndarray, raw: Path, master: Path) -> None:
    np.ascontiguousarray(pcm.T, dtype="<f4").tofile(raw)
    master.parent.mkdir(parents=True, exist_ok=True)
    partial = master.with_name(master.name + ".partial")
    run([ffmpeg, "-y", "-v", "error", "-f", "f32le", "-ar", str(SAMPLE_RATE),
         "-ac", str(CHANNELS), "-i", str(raw), "-c:a", "flac", "-sample_fmt", "s16",
         "-f", "flac", str(partial)], "Extend: audio master")
    partial.replace(master)


def _master_matches(master: Path | None, seconds: float) -> bool:
    """A master is only trusted when it still describes the source: same
    length to within a frame (a clip trimmed or replaced since must not get
    the old soundtrack back)."""
    if not master:
        return False
    try:
        size = master.stat().st_size
    except OSError:
        return False
    return size > 0 and abs(_flac_seconds(master) - seconds) <= 0.05


def _flac_seconds(path: Path) -> float:
    """Duration of a FLAC file from its STREAMINFO block (no ffprobe)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(42)
        if head[:4] != b"fLaC":
            return -1.0
        info = head[8:8 + 34]
        sr = (info[10] << 12) | (info[11] << 4) | (info[12] >> 4)
        total = ((info[13] & 0x0F) << 32) | int.from_bytes(info[14:18], "big")
        return total / float(sr) if sr else -1.0
    except OSError:
        return -1.0


def splice(*, ffmpeg: str | Path, ffprobe: str | Path, source: Path, generated: Path,
           generated_audio: Path | None, out: Path, layout: Layout, work: Path,
           pix_fmt: str, crf: str, x264_args: list[str] | None = None,
           generated_lossless: bool = True, source_master: Path | None = None,
           master_out: Path | None = None, run: Runner | None = None) -> dict:
    """Write ``out`` = the source's own frames + the generated new frames,
    with the audio spliced from lossless masters and encoded once.

    ``generated`` is the model's full output (source region + new), on the
    delivered canvas; ``generated_lossless`` says whether its video is a
    lossless intermediate (re-encode the new part once) or already the
    delivery codec (H3's runner without ``--crf 0``). ``generated_audio`` is
    a lossless wav of the same timeline when the engine leaves one (the
    helper's vocoder output, the H3 runner's wav), else the audio is read
    from ``generated``. Returns a report dict for the sidecar.
    """
    run = run or _default_run
    ffmpeg, ffprobe = str(ffmpeg), str(ffprobe)
    x264 = list(x264_args or [])
    work.mkdir(parents=True, exist_ok=True)
    src = probe(ffprobe, source)
    gen = probe(ffprobe, generated)
    fps = gen["fps"] or src["fps"]
    if not fps:
        raise SpliceError("unknown frame rate")
    if gen["frames"] < layout.new_end:
        raise SpliceError(f"the model's output has {gen['frames']} frames, the "
                          f"splice needs {layout.new_end}")
    report = {"version": 1, "source": str(source), "source_frames": layout.source_frames,
              "new_frames": layout.new_frames, "seam_frame": layout.seam_frame,
              "seam_seconds": round(layout.seam_frame / fps, 4),
              "direction": "after" if layout.source_first else "before"}
    same_canvas = (src["width"], src["height"]) == (gen["width"], gen["height"])
    same_rate = abs((src["fps"] or 0) - fps) < 0.01
    enc = ["-c:v", "libx264", "-pix_fmt", pix_fmt, "-crf", str(crf), *x264]

    # ---- video --------------------------------------------------------------
    video = work / "video.mp4"
    mode = None
    why = ""
    if not (same_canvas and same_rate):
        why = (f"source {src['width']}x{src['height']}@{src['fps']:.3f} differs from the "
               f"render {gen['width']}x{gen['height']}@{fps:.3f}")
    elif src["frames"] < layout.source_frames:
        why = f"source has {src['frames']} frames, the model used {layout.source_frames}"
    if why:
        # Nothing of the source file can stand in for the model's input here:
        # deliver the model's own frames (its source region was decoded once
        # from the picked file), exactly the pre-#48 delivery for this round.
        mode = "generated"
        if generated_lossless:
            run([ffmpeg, "-y", "-v", "error", "-i", str(generated), "-map", "0:v:0", "-an",
                 *enc, str(video)], "Extend: encode")
        else:
            run([ffmpeg, "-y", "-v", "error", "-i", str(generated), "-map", "0:v:0", "-an",
                 "-c:v", "copy", str(video)], "Extend: video")
    else:
        new_part = work / "new.mp4"
        sel = f"select='between(n\\,{layout.new_start}\\,{layout.new_end - 1})',setpts=N/FRAME_RATE/TB"
        run([ffmpeg, "-y", "-v", "error", "-i", str(generated), "-map", "0:v:0", "-an",
             "-vf", sel, "-frames:v", str(layout.new_frames), *enc, str(new_part)],
            "Extend: new frames")
        copy_ok = (src["codec"] == "h264" and src["frames"] == layout.source_frames
                   and avcc(new_part) is not None and avcc(new_part) == avcc(source))
        if copy_ok:
            src_v = work / "source_video.mp4"
            run([ffmpeg, "-y", "-v", "error", "-i", str(source), "-map", "0:v:0", "-an",
                 "-c", "copy", str(src_v)], "Extend: source video")
            parts = [src_v, new_part] if layout.source_first else [new_part, src_v]
            lst = work / "concat.txt"
            lst.write_text("".join("file '" + str(p).replace("'", "'\\''") + "'\n"
                                   for p in parts), encoding="utf-8")
            try:
                run([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0",
                     "-i", str(lst), "-c", "copy", str(video)], "Extend: join")
                got = probe(ffprobe, video)["frames"]
            except Exception:                                  # noqa: BLE001
                got = -1
            if got == layout.total_frames:
                mode = "copy"
            else:
                why = f"stream-copy join came out at {got} frames, not {layout.total_frames}"
        else:
            why = ("source is not an H.264 stream the new part can continue "
                   "(codec, frame count or encoder parameter sets differ)")
        if mode is None:
            # Decode the picked file ONCE (never a re-decoded copy of it) and
            # encode it together with the new frames: one generation.
            a, b = layout.new_start, layout.new_end
            chains = (f"[0:v]trim=end_frame={layout.source_frames},setpts=PTS-STARTPTS,"
                      f"setsar=1,format={pix_fmt}[s];"
                      f"[1:v]trim=start_frame={a}:end_frame={b},setpts=PTS-STARTPTS,"
                      f"setsar=1,format={pix_fmt}[n];")
            chains += "[s][n]concat=n=2:v=1:a=0[v]" if layout.source_first \
                else "[n][s]concat=n=2:v=1:a=0[v]"
            run([ffmpeg, "-y", "-v", "error", "-i", str(source), "-i", str(generated),
                 "-filter_complex", chains, "-map", "[v]", "-an", *enc, str(video)],
                "Extend: re-encode once")
            mode = "reencode"
    report["video"] = mode
    if why:
        report["video_note"] = why
    total = probe(ffprobe, video)["frames"]
    if mode != "generated" and total != layout.total_frames:
        raise SpliceError(f"spliced video has {total} frames, expected {layout.total_frames}")
    report["frames"] = total

    # ---- audio --------------------------------------------------------------
    seconds_total = total / fps
    n_total = int(round(seconds_total * SAMPLE_RATE))
    g_src = generated_audio if (generated_audio and Path(generated_audio).is_file()) else generated
    gen_pcm = _read_pcm(run, ffmpeg, Path(g_src), work / "gen.f32", "Extend: generated audio",
                        offset=gen.get("audio_offset", 0.0) if g_src == generated else 0.0)
    # The audio is spliced whatever happened to the picture: it is a time-domain
    # signal, and the source region spans the same seconds either way.
    src_seconds = src["frames"] / (src["fps"] or fps)
    if _master_matches(source_master, src_seconds):
        src_pcm = _read_pcm(run, ffmpeg, Path(source_master), work / "src.f32",
                            "Extend: source audio master")
        report["audio"] = "master"
    elif src["has_audio"]:
        src_pcm = _read_pcm(run, ffmpeg, source, work / "src.f32", "Extend: source audio",
                            offset=src.get("audio_offset", 0.0))
        report["audio"] = "decoded"
    else:
        src_pcm = np.zeros((CHANNELS, int(round(src_seconds * SAMPLE_RATE))), np.float32)
        report["audio"] = "silent-source"
    # Only the stretch the model used: a source longer than its 1+8k frames
    # loses the same tail in the audio as in the picture.
    src_pcm = src_pcm[:, :int(round(layout.source_frames / fps * SAMPLE_RATE))]
    fade = int(round(FADE_SECONDS * SAMPLE_RATE))
    seam = int(round(layout.seam_frame / fps * SAMPLE_RATE))
    pcm = crossfade_splice(src_pcm, gen_pcm, seam, fade, layout.source_first)
    report["fade_ms"] = round(1000 * fade / SAMPLE_RATE, 1)
    pcm = fit_length(pcm, n_total)
    master = master_out or (work / "master.flac")
    _write_master(run, ffmpeg, pcm, work / "master.f32", master)
    report["audio_master"] = str(master)

    # ---- mux ----------------------------------------------------------------
    partial = out.with_name(out.name + ".partial")
    run([ffmpeg, "-y", "-v", "error", "-i", str(video), "-i", str(master),
         "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
         "-b:a", AUDIO_BITRATE, "-movflags", "+faststart", "-f", "mp4", str(partial)],
        "Extend: mux")
    final = probe(ffprobe, partial)
    if final["frames"] != total or not final["has_audio"]:
        raise SpliceError(f"muxed file has {final['frames']} frames / audio "
                          f"{final['has_audio']}, expected {total} + audio")
    partial.replace(out)
    return report


def _default_run(cmd: list, label: str) -> None:
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                       errors="replace")
    if r.returncode != 0:
        raise SpliceError(f"{label} failed: {(r.stderr or r.stdout).strip()[-300:]}")
