#!/usr/bin/env python3
"""Separate the sung vocal out of a song, for a2v "Listen to the voice only".

Runs in the ENGINE venv (the one the helper uses): demucs + torch live there,
installed by scripts/pinokio/a2v_stems_deps.sh from install.js and from every
Update (scripts/post_update.sh). The panel calls this as a tracked subprocess
so Stop reaches it.

WHY A RUNNER AND NOT THE `demucs` CLI. The CLI reads and writes audio through
torchaudio, and torchaudio 2.9+ moved load/save onto torchcodec (a native
package with its own FFmpeg ABI rules). demucs 4.0.1's `save_audio` therefore
dies on a current torchaudio after it has spent the whole separation. This
runner uses demucs only for the MODEL: audio comes in through the panel's own
ffmpeg and goes out through the stdlib `wave` module, so no audio I/O library
can break it.

    a2v_separate.py --in SONG --out VOCALS.wav [--ffmpeg PATH] [--model NAME]
    a2v_separate.py --prefetch [--model NAME]      # download the weights, exit

Model weights (~80 MB for htdemucs) are cached by torch.hub under $TORCH_HOME;
the panel points TORCH_HOME at mlx_models/demucs so they live with the rest of
Phosphene's weights. Exit 0 = the vocal file is written and non-empty.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import wave

SAMPLE_RATE = 44100          # htdemucs' own rate; the input is resampled to it
DEFAULT_MODEL = "htdemucs"


def _scrub_env() -> None:
    # The engine venv is shared with MLX work; nothing here needs the MPS
    # fallback Pinokio injects, and demucs runs on the CPU on purpose (the GPU
    # belongs to the render this separation is the first step of).
    os.environ.pop("PYTORCH_ENABLE_MPS_FALLBACK", None)
    os.environ.pop("PYTORCH_MPS_FAST_MATH", None)


def load_model(name: str):
    from demucs.pretrained import get_model          # noqa: PLC0415
    model = get_model(name)
    model.eval()
    return model


# ---- the weights download, made to survive a bad connection (4.17.5) -------
# Fleet 4.17.4: two installs got the package but not the 80 MB model
# (weights_failed). demucs fetches it through torch.hub in ONE request with no
# timeout and no resume, from dl.fbaipublicfiles.com: a dropped connection
# loses the whole file and the next attempt starts from zero. This fetches the
# same file into the same place torch.hub looks (TORCH_HOME/hub/checkpoints),
# resuming a partial download, with a timeout and four attempts, and checks it
# the way torch.hub does (the sha256 prefix in its name). get_model then finds
# it on disk and downloads nothing. PHOSPHENE_DEMUCS_MIRROR (a base URL) is
# tried after the official host; the hash check applies to it the same.
DOWNLOAD_ATTEMPTS = 4
DOWNLOAD_TIMEOUT_S = 60


class DownloadExhausted(RuntimeError):
    """Every attempt at the model download failed (not a table problem)."""


def model_files(name: str) -> list[tuple[str, str]]:
    """[(file name, official URL)] for a pretrained bag, from demucs' own
    remote list - the same table get_model reads."""
    from demucs import pretrained                    # noqa: PLC0415
    import yaml                                      # noqa: PLC0415
    files = pretrained._parse_remote_files(pretrained.REMOTE_ROOT / "files.txt")
    bag = pretrained.REMOTE_ROOT / f"{name}.yaml"
    sigs = yaml.safe_load(bag.read_text())["models"] if bag.is_file() else [name]
    return [(files[sig].rsplit("/", 1)[-1], files[sig]) for sig in sigs]


def _hash_ok(path: str, file_name: str) -> bool:
    import hashlib                                   # noqa: PLC0415
    import re                                        # noqa: PLC0415
    m = re.search(r"-([a-f0-9]*)\.", file_name)
    if not m:
        return os.path.getsize(path) > 0
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest().startswith(m.group(1))


def _fetch_one(url: str, dest: str) -> None:
    """Download url -> dest, resuming dest + '.partial'. Raises on failure."""
    import urllib.request                            # noqa: PLC0415
    part = dest + ".partial"
    have = os.path.getsize(part) if os.path.isfile(part) else 0
    req = urllib.request.Request(url, headers={"User-Agent": "phosphene-separator"})
    if have:
        req.add_header("Range", f"bytes={have}-")
    import urllib.error                              # noqa: PLC0415
    try:
        resp_cm = urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT_S)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and have:     # the partial file is already whole
            os.replace(part, dest)
            return
        raise
    with resp_cm as resp:
        resumed = bool(have and getattr(resp, "status", 200) == 206)
        try:
            expect = int(resp.headers.get("Content-Length") or -1)
        except ValueError:
            expect = -1
        expect = (have + expect) if (resumed and expect >= 0) else expect
        with open(part, "ab" if resumed else "wb") as out:
            while True:
                block = resp.read(1 << 20)
                if not block:
                    break
                out.write(block)
    # A connection that drops can end the body without an exception; the
    # partial file is KEPT, so the next attempt resumes from where it stopped.
    got = os.path.getsize(part)
    if expect >= 0 and got < expect:
        raise ConnectionError(f"the connection dropped at {got} of {expect} bytes")
    os.replace(part, dest)


def prefetch(name: str, *, sleep=None) -> None:
    """Every weight file the bag needs, on disk and hash-checked."""
    import time                                      # noqa: PLC0415
    sleep = sleep or time.sleep
    home = os.environ.get("TORCH_HOME") or os.path.expanduser("~/.cache/torch")
    ckpt = os.path.join(home, "hub", "checkpoints")
    os.makedirs(ckpt, exist_ok=True)
    mirror = (os.environ.get("PHOSPHENE_DEMUCS_MIRROR") or "").rstrip("/")
    for file_name, url in model_files(name):
        dest = os.path.join(ckpt, file_name)
        if os.path.isfile(dest) and _hash_ok(dest, file_name):
            continue
        urls = [url] + ([f"{mirror}/{file_name}"] if mirror else [])
        last = None
        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            src = urls[-1] if attempt > 2 else urls[0]   # mirror (if any) last
            try:
                _fetch_one(src, dest)
                if _hash_ok(dest, file_name):
                    break
                # A corrupt file is not resumable: start that one over.
                os.replace(dest, dest + ".bad")
                last = RuntimeError("downloaded file failed its checksum")
            except Exception as exc:                 # noqa: BLE001
                last = exc
            print(f"separator weights: attempt {attempt} of {DOWNLOAD_ATTEMPTS} "
                  f"did not finish ({type(last).__name__}: {last})", file=sys.stderr)
            if attempt < DOWNLOAD_ATTEMPTS:
                sleep(min(30, 5 * attempt))
        else:
            raise DownloadExhausted(f"the separator model did not download: {last}")


def read_audio(path: str, ffmpeg: str):
    import numpy as np                               # noqa: PLC0415
    import torch                                     # noqa: PLC0415
    proc = subprocess.run(
        [ffmpeg, "-v", "error", "-nostdin", "-i", path, "-f", "f32le",
         "-ac", "2", "-ar", str(SAMPLE_RATE), "-"],
        capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        tail = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1:]
        raise RuntimeError(f"could not read the song ({tail[0] if tail else 'ffmpeg wrote nothing'})")
    pcm = np.frombuffer(proc.stdout, dtype=np.float32).reshape(-1, 2).T.copy()
    return torch.from_numpy(pcm)


def write_wav(path: str, audio) -> None:
    import numpy as np                               # noqa: PLC0415
    pcm = np.clip(audio.numpy().T, -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype("<i2")
    tmp = path + ".part"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(pcm16.shape[1])
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm16.tobytes())
    os.replace(tmp, path)


def separate(src: str, dst: str, ffmpeg: str, model_name: str) -> None:
    import torch                                     # noqa: PLC0415
    from demucs.apply import apply_model             # noqa: PLC0415
    model = load_model(model_name)
    wav = read_audio(src, ffmpeg)
    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std()
    std = std if float(std) > 1e-8 else torch.tensor(1.0)
    with torch.no_grad():
        sources = apply_model(model, ((wav - mean) / std)[None], device="cpu",
                              shifts=1, split=True, overlap=0.25, progress=False)[0]
    vocals = sources[model.sources.index("vocals")] * std + mean
    write_wav(dst, vocals)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--in", dest="src")
    ap.add_argument("--out", dest="dst")
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--prefetch", action="store_true")
    a = ap.parse_args(argv)
    _scrub_env()
    if a.prefetch:
        try:
            prefetch(a.model)
        except DownloadExhausted as exc:
            # Four timed, resumed attempts failed: the host is not reachable
            # from here. Falling through to torch.hub's own download (one
            # request, no timeout) could hang an Install for good (Codex 4.17.5).
            print(f"separator weights: {exc}", file=sys.stderr)
            return 3
        except Exception as exc:                     # noqa: BLE001
            # demucs reorganised its model table: torch.hub's own download is
            # the fallback, with a socket timeout so a stalled host cannot hang.
            print(f"separator weights: {exc}", file=sys.stderr)
            import socket                            # noqa: PLC0415
            socket.setdefaulttimeout(DOWNLOAD_TIMEOUT_S)
        load_model(a.model)
        print(f"vocal separator ready ({a.model})")
        return 0
    if not a.src or not a.dst:
        ap.error("--in and --out are required (or --prefetch)")
    separate(a.src, a.dst, a.ffmpeg, a.model)
    if not os.path.isfile(a.dst) or os.path.getsize(a.dst) <= 44:
        print("the separator wrote no audio", file=sys.stderr)
        return 1
    print(f"vocals written: {a.dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
