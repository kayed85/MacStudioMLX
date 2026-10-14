"""Detect and repair LoRA files the trainer wrote scrambled (#62 root cause).

Every Train-tab adapter (character and voice) saved by a venv with
safetensors 0.8.0 (released 2026-06-09; the packages require only
``safetensors>=0.4.0``) holds each factor's UNTRANSPOSED bytes under the
transposed shape — see ``lora_lab.train._patch_contiguous_checkpoint_save``.
The element values are all there, so the file can be repaired exactly:

    lora_A (r, in)  := file_A.reshape(in, r).T
    lora_B (out, r) := file_B.reshape(r, out).T

Applying that to a file that was saved correctly would scramble it, so the
repair is gated on a content check rather than on dates or versions. In a
real adapter the rank rows of ``lora_A`` share the input-channel magnitude
profile (the gradient into ``lora_a`` is scaled by the input activations), so
``|A|``'s rows correlate; scrambling interleaves rank components and input
channels and the correlation collapses to ~0. The check scores both readings
and only calls a file scrambled when the unscrambled reading wins by
``MARGIN``. Measured: a Phosphene 2-image run 0.0008 as saved vs 0.0146
unscrambled, ``valeriosan_v5`` 0.0015 vs 0.0402, and a third-party PyTorch
LoRA (DoctorDiffusion Colorizer) 0.0050 as-is vs 0.0006 "unscrambled".

    python -m lora_lab.repair_scrambled_lora FILE.safetensors [...]          # report only
    python -m lora_lab.repair_scrambled_lora FILE.safetensors --out FIXED.safetensors
    python -m lora_lab.repair_scrambled_lora FILE.safetensors --in-place     # keeps FILE.scrambled.bak
    python -m lora_lab.repair_scrambled_lora --auto MODELS_DIR               # what Update runs

Found and written by @tanis2000 (PR #89). 4.19.0 additions: the header's
metadata and each tensor's dtype survive a repair; an in-place repair keeps
hard links (a character bundle shares its file with ``loras/``); the stored
``adapter_strength`` verdict is re-measured, so a WEAK badge earned by the
scrambled file goes away; voice adapters are scored against a floor sized for
their short rows; and ``--auto`` repairs only adapters this app trained, so
Update can fix every installed character without touching anything else.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import struct
import sys
from pathlib import Path

import numpy as np

MARGIN = 3.0
MIN_SCORE = 0.005  # real winners measured 0.0118-0.0406; losers <= 0.0032
# Voice adapters (every module on the audio branch) have 2048-wide rows and
# rank 16, so their channel structure is fainter: the two real voice adapters
# measured here read 0.0070 / 0.0042 as saved and 0.0008 / 0.0003 the other
# way, which MIN_SCORE called "undetermined" in BOTH directions — a scrambled
# voice adapter could never be repaired. The floor below is sized from the
# null distribution of the MEDIAN over the scored modules (measured: sd
# 0.00025 at 16x2048 over 96 modules, mean 0), so 0.002 is ~8 sd; the 3x
# MARGIN still has to hold on top of it.
MIN_SCORE_AUDIO = 0.002
MAX_MODULES = 96
_A, _B = ".lora_A.weight", ".lora_B.weight"


def load_weights(path: Path) -> dict[str, np.ndarray]:
    """float32 numpy view of every tensor; bf16 files go through MLX."""
    try:
        from safetensors.numpy import load_file
        return {k: v.astype(np.float32, copy=False) for k, v in load_file(str(path)).items()}
    except TypeError:  # bfloat16 is not a numpy dtype
        import mlx.core as mx
        return {k: np.array(v.astype(mx.float32)) for k, v in mx.load(str(path)).items()}


def _row_corr(a: np.ndarray) -> float:
    """Mean off-diagonal correlation of |A|'s rows across the input axis."""
    m = np.abs(a)
    m = (m - m.mean(1, keepdims=True)) / (m.std(1, keepdims=True) + 1e-12)
    c = m @ m.T / m.shape[1]
    r = c.shape[0]
    return float((c.sum() - np.trace(c)) / (r * r - r)) if r > 1 else 0.0


def _unscramble_a(a: np.ndarray) -> np.ndarray:
    r, n_in = a.shape
    return np.ascontiguousarray(a).reshape(n_in, r).T


def scramble_scores(weights: dict[str, np.ndarray]) -> tuple[float, float, int, float]:
    """(as-is score, unscrambled-reading score, modules scored, noise floor).

    The floor is ~6 sigma of the score structureless noise would produce
    (one row-pair correlation over n_in samples has sd ~1/sqrt(n_in); the
    score averages r(r-1) of them), and never below MIN_SCORE.
    """
    keys = sorted(k for k, v in weights.items() if k.endswith(_A) and v.ndim == 2 and v.shape[0] > 1)
    keys = keys[:: max(1, len(keys) // MAX_MODULES)][:MAX_MODULES]
    if not keys:
        return 0.0, 0.0, 0, MIN_SCORE
    as_is = [_row_corr(weights[k]) for k in keys]
    unscr = [_row_corr(_unscramble_a(weights[k])) for k in keys]
    r, n_in = weights[keys[0]].shape
    # 6 sd of the MEDIAN of len(keys) structureless module scores: one
    # module's score has sd sqrt(2 / (n_in r (r-1))) (r(r-1)/2 independent
    # row pairs, each ~1/sqrt(n_in)), and the median of n draws has
    # sd ~1.2533 * that / sqrt(n).
    one = np.sqrt(2.0 / (n_in * r * (r - 1)))
    stat_floor = 6.0 * 1.2533 * one / np.sqrt(len(keys))
    audio_only = all(".audio_" in k or "audio_attn" in k or "audio_ff" in k for k in keys)
    floor = max(MIN_SCORE_AUDIO if audio_only else MIN_SCORE, stat_floor)
    return statistics.median(as_is), statistics.median(unscr), len(keys), float(floor)


def classify(as_is: float, unscr: float, floor: float = MIN_SCORE) -> str:
    """'scrambled', 'ok', or 'undetermined' (never guess on a close call).

    A reading wins only if it clears the noise floor AND beats the other by
    MARGIN; anything else — including structureless noise — is undetermined.
    """
    if unscr >= floor and unscr >= MARGIN * max(as_is, 1e-6):
        return "scrambled"
    if as_is >= floor and as_is >= MARGIN * max(unscr, 1e-6):
        return "ok"
    return "undetermined"


def unscramble(weights: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    for k, v in weights.items():
        if k.endswith(_A):
            out[k] = np.ascontiguousarray(_unscramble_a(v))
        elif k.endswith(_B):
            n_out, r = v.shape
            out[k] = np.ascontiguousarray(np.ascontiguousarray(v).reshape(r, n_out).T)
        else:
            out[k] = np.ascontiguousarray(v)
    return out


def read_header(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """(tensor name -> safetensors dtype, __metadata__) from the file header."""
    with Path(path).open("rb") as fh:
        n = struct.unpack("<Q", fh.read(8))[0]
        if n <= 0 or n > 100_000_000:
            raise ValueError(f"{path}: not a safetensors file")
        header = json.loads(fh.read(n))
    meta = header.pop("__metadata__", None) or {}
    return ({k: str(v.get("dtype")) for k, v in header.items()},
            {str(k): str(v) for k, v in meta.items()})


def save_like(weights: dict[str, np.ndarray], dest: Path, source: Path) -> None:
    """Write ``weights`` with the SOURCE file's dtypes and header metadata.

    ``load_weights`` reads everything as float32; written back as-is a bf16
    adapter would double in size and a fp16 one would change type, and the
    header's ``__metadata__`` (trigger words, training provenance in many
    third-party files) would be gone.
    """
    dtypes, meta = read_header(source)
    if any(d == "BF16" for d in dtypes.values()):
        import mlx.core as mx
        mxd = {"BF16": mx.bfloat16, "F16": mx.float16, "F32": mx.float32}
        out = {k: mx.array(v).astype(mxd.get(dtypes.get(k, "F32"), mx.float32))
               for k, v in weights.items()}
        mx.save_safetensors(str(dest), out, metadata=meta or None)
        return
    from safetensors.numpy import save_file
    npd = {"F16": np.float16, "F32": np.float32, "F64": np.float64}
    out = {k: np.ascontiguousarray(v.astype(npd[dtypes[k]], copy=False))
           if dtypes.get(k) in npd else np.ascontiguousarray(v)
           for k, v in weights.items()}
    save_file(out, str(dest), metadata=meta or None)


def _write_in_place(fixed: dict[str, np.ndarray], path: Path) -> None:
    """Replace ``path``'s CONTENT, keeping its inode.

    A trained character is one file under two names — ``loras/<id>_v2`` and
    the bundle's ``characters/<id>/<id>.video`` are hard links — so an
    ``os.replace`` would repair one name and leave the other scrambled. The
    repaired bytes are written to a temp file first (a failed save leaves the
    original untouched), then copied over the original.
    """
    tmp = path.with_name(path.name + ".repair.tmp")
    try:
        save_like(fixed, tmp, path)
        shutil.copyfile(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _remeasure(path: Path) -> dict | None:
    """The adapter_strength payload the trainer would have written for the
    repaired file (``lora_lab.train_character.report_adapter_strength``)."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from lora_compat import measure_adapter_effect
        effect = measure_adapter_effect(path)
    except Exception:  # noqa: BLE001 — a probe must never fail a repair
        return None
    if effect is None:
        return None
    return {
        "modules": effect.modules,
        "carrying_modules": effect.modules - effect.zero_modules,
        "delta_rms_median": effect.median_rms,
        "delta_rms_max": effect.max_rms,
        "floor": effect.floor,
        "verdict": "inert" if effect.inert else ("weak" if effect.weak else "ok"),
    }


def sidecars(path: Path) -> list[Path]:
    """The two sidecars a trained adapter can carry: the trainer's
    ``<file>.safetensors.json`` and the panel's ``<stem>.json``."""
    return [p for p in (path.with_suffix(path.suffix + ".json"), path.with_suffix(".json"))
            if p.is_file() and not p.is_symlink()]


def _note_sidecar(path: Path, report: dict) -> None:
    strength = _remeasure(path)
    for sidecar in sidecars(path):
        try:
            data = json.loads(sidecar.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        data["repaired_scrambled_save"] = report
        # The verdict the Train tab shows (WEAK / DEAD badge) was measured on
        # the scrambled file — about half strength by construction. Replace
        # it with the repaired file's, and keep the old one for the record.
        if strength is not None and "adapter_strength" in data:
            data["adapter_strength_as_saved"] = data["adapter_strength"]
            data["adapter_strength"] = strength
        sidecar.write_text(json.dumps(data, indent=2))


def _provenance(path: Path) -> tuple[bool, bool]:
    """(trained in this app, already repaired) from ``path``'s own sidecars."""
    trained = repaired = False
    for sidecar in sidecars(path):
        try:
            data = json.loads(sidecar.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        repaired = repaired or bool(data.get("repaired_scrambled_save"))
        trained = trained or bool(data.get("lora_lab_version")
                                  or data.get("kind") in ("train_character", "train_style"))
    return trained, repaired


def trained_here(path: Path) -> bool:
    """True only for adapters this app's trainer wrote, not yet repaired.

    ``--auto`` runs unattended inside Update, so it never classifies a file it
    cannot vouch for, and a NAME is not provenance (Codex 4.19.0: a downloaded
    ``someone.audio.safetensors`` must never be rewritten). A trained
    character carries a sidecar from the trainer (``lora_lab_version``) or the
    panel (``kind`` train_character / train_style). A voice adapter has no
    sidecar of its own; Train writes it as ``<trigger>.audio.safetensors``
    beside that character's face file ``<trigger>_v2.safetensors``, so it
    qualifies only when that face file's sidecar says this app trained it.
    Anything else (CivitAI, Hugging Face, a file a user dropped in, a bundle
    that came with a download) is skipped without being read.
    """
    trained, repaired = _provenance(path)
    if repaired:
        return False  # repaired already; never read it twice
    if trained:
        return True
    if path.name.endswith(".audio.safetensors"):
        face = path.with_name(path.name[: -len(".audio.safetensors")] + "_v2.safetensors")
        return _provenance(face)[0]
    return False


def auto_candidates(models_dir: Path) -> list[Path]:
    """Every trained adapter under ``loras/`` and ``characters/*/``, one path
    per inode (hard links are the same adapter).

    Only REAL files that live inside those folders: a symlink (or a folder
    that resolves elsewhere) is skipped, so an unattended repair can never
    write through a link to a file outside the model library (Codex 4.19.0).
    """
    seen: set[tuple[int, int]] = set()
    out: list[Path] = []
    roots = [models_dir / "loras", *sorted((models_dir / "characters").glob("*/"))]
    for root in roots:
        if not root.is_dir() or root.is_symlink():
            continue
        real_root = root.resolve()
        for f in sorted(root.glob("*.safetensors")):
            try:
                if f.is_symlink() or f.resolve().parent != real_root:
                    continue
                st = f.stat()
            except OSError:
                continue
            key = (st.st_dev, st.st_ino)
            if key in seen or not trained_here(f):
                continue
            seen.add(key)
            out.append(f)
    return out


def _free_bytes(path: Path) -> int:
    try:
        return shutil.disk_usage(path.parent).free
    except OSError:
        return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("files", nargs="*", type=Path)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--out", type=Path, help="write the repaired copy here (one input file only)")
    g.add_argument("--in-place", action="store_true", help="repair in place, keeping <file>.scrambled.bak")
    g.add_argument("--auto", type=Path, metavar="MODELS_DIR",
                   help="repair, in place, every adapter this app trained under "
                        "MODELS_DIR/loras and MODELS_DIR/characters/* (what Update runs)")
    args = p.parse_args(argv)
    if args.out and len(args.files) != 1:
        p.error("--out takes exactly one input file")
    if args.auto:
        if args.files:
            p.error("--auto takes a models directory, not files")
        args.files = auto_candidates(args.auto)
        args.in_place = True
        print(f"checking {len(args.files)} adapter(s) trained in this app for the "
              "#62 scrambled save")
    elif not args.files:
        p.error("give at least one file, or --auto MODELS_DIR")

    worst = 0
    for f in args.files:
        try:
            w = load_weights(f)
        except Exception as exc:  # noqa: BLE001 — one unreadable file must not stop the rest
            print(f"{f}: unreadable ({exc}) - left as is")
            worst = max(worst, 2)
            continue
        as_is, unscr, n, floor = scramble_scores(w)
        verdict = classify(as_is, unscr, floor)
        print(f"{f}: {verdict}  (as-is {as_is:+.4f}, unscrambled {unscr:+.4f}, {n} modules)")
        if verdict != "scrambled" or not (args.out or args.in_place):
            worst = max(worst, 0 if verdict != "undetermined" else 2)
            continue
        fixed = unscramble(w)
        report = {"as_is": round(as_is, 5), "unscrambled": round(unscr, 5), "modules_scored": n}
        if args.out:
            save_like(fixed, args.out, f)
            print(f"  wrote repaired copy -> {args.out}")
        else:
            backup = f.with_name(f.name + ".scrambled.bak")
            if backup.exists():
                print(f"  refusing: {backup} already exists", file=sys.stderr)
                worst = 1
                continue
            # The backup and the temp copy each need the file's size again.
            need = 2 * f.stat().st_size + 512 * 1024 * 1024
            if _free_bytes(f) < need:
                print(f"  NOT repaired: needs {need / 1e9:.1f} GB free beside it "
                      f"for the backup - free space and run Update again")
                worst = 1
                continue
            shutil.copy2(f, backup)
            _write_in_place(fixed, f)
            _note_sidecar(f, report)
            print(f"  repaired in place (original kept as {backup.name})")
    return worst


if __name__ == "__main__":
    sys.exit(main())
