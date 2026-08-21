"""Fail-closed LTX LoRA/checkpoint compatibility inspection.

The MLX LoRA loaders historically treated an adapter whose keys matched zero
transformer weights as a successful no-op.  A video still came out, which is
especially dangerous for trained characters: the result looks like a plausible
stranger instead of an error.  This module reads only safetensors headers and
checks the exact post-remap module names before a pipeline spends memory or
starts denoising.

It deliberately has no MLX or safetensors dependency.  The panel uses it while
building the library, and the helper uses the same code against the transformer
file it is about to load.

Names are not effect (#62)
--------------------------
Matching every key is necessary and **not sufficient**.  In August 2026 two
users trained characters that attached ``576/576`` modules on the unfused
runtime route — a perfectly green tally — and changed nothing anyone could
see.  The adapters were structurally flawless and numerically negligible: the
low-rank product they carry is orders of magnitude below the one recipe that
has ever carried an identity.  ``measure_adapter_effect`` puts a number on
that half of the question, cheaply (the exact Frobenius norm of ``B @ A``
without ever forming ``B @ A``), so "it attached" and "it can do something"
stop being the same claim.
"""

from __future__ import annotations

import json
import struct
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable, Mapping


LORA_A_SUFFIX = ".lora_A.weight"
LORA_B_SUFFIX = ".lora_B.weight"
MIN_MATCH_RATIO = 0.90
MAX_HEADER_BYTES = 64 * 1024 * 1024

#: Per-entry RMS of the low-rank delta, ``‖B @ A‖_F / sqrt(in * out)``, below
#: which an adapter has never been observed to change a render.  Calibrated on
#: the adapters this repo actually ships and grades, all measured with
#: :func:`measure_adapter_effect`:
#:
#:   ===========================================  =========  =========
#:   adapter                                      rank       median
#:   ===========================================  =========  =========
#:   ``elontrn_v2`` (validated recipe)            32         1.63e-03
#:   ``ariatrn_v2`` (validated recipe)            32         1.45e-03
#:   ``eltrumpo_v2`` (validated recipe)           32         1.41e-03
#:   ``bizarrotrn_v2`` (sample character)         32         8.84e-04
#:   ``LTX2.3-Rogue…`` (third-party, works)       16         1.84e-03
#:   ``Fantasy_Painterly`` (third-party, works)   32         5.36e-04
#:   ``bizarrotrn.audio`` (voice, works)          16         4.85e-04
#:   ===========================================  =========  =========
#:
#: The floor sits an order of magnitude under the weakest working adapter, so
#: it accuses nothing that has ever worked, and it is two orders above a
#: freshly-initialised adapter (1.7e-05 after four steps).
WEAK_DELTA_RMS = 2.0e-4


class LoraCompatibilityError(RuntimeError):
    """The named adapter cannot safely affect the named transformer."""


@dataclass(frozen=True)
class LoraCompatibility:
    lora_path: Path
    transformer_path: Path
    declared_tensors: int
    paired_modules: int
    matched_modules: int
    matched_module_names: frozenset[str]
    minimum_match_ratio: float = MIN_MATCH_RATIO

    @property
    def matched_tensors(self) -> int:
        return self.matched_modules * 2

    @property
    def match_ratio(self) -> float:
        if self.paired_modules <= 0:
            return 0.0
        return self.matched_modules / self.paired_modules

    @property
    def compatible(self) -> bool:
        return (
            self.paired_modules > 0
            and self.matched_modules > 0
            and self.match_ratio >= self.minimum_match_ratio
            and self.paired_modules * 2 == self.declared_tensors
        )

    @property
    def tally(self) -> str:
        return (
            f"FUSED={self.matched_tensors}/{self.declared_tensors} tensors "
            f"({self.matched_modules}/{self.paired_modules} modules)"
        )

    def failure_message(self) -> str:
        name = self.lora_path.name
        target = self.transformer_path.name
        if self.paired_modules == 0:
            detail = "no complete LTX lora_A/lora_B module pairs were found"
        elif self.paired_modules * 2 != self.declared_tensors:
            detail = (
                f"only {self.paired_modules * 2} of {self.declared_tensors} "
                "LoRA tensors form complete A/B pairs"
            )
        else:
            detail = (
                f"only {self.match_ratio:.1%} of its module pairs match; "
                f"at least {self.minimum_match_ratio:.0%} is required"
            )
        return (
            f"LoRA '{name}' is incompatible with {target}: {self.tally}; "
            f"{detail}. The file likely targets a different model or key "
            "layout. Rendering was refused before it could produce a "
            "LoRA-free result."
        )

    def require_compatible(self) -> None:
        if not self.compatible:
            raise LoraCompatibilityError(self.failure_message())


def _stat_key(path: Path) -> tuple[str, int, int]:
    resolved = path.resolve()
    stat = resolved.stat()
    return str(resolved), stat.st_size, stat.st_mtime_ns


@lru_cache(maxsize=512)
def _tensor_header_cached(path_raw: str, size: int, mtime_ns: int) -> dict[str, dict]:
    del mtime_ns  # cache-key material; size is also checked against the header
    path = Path(path_raw)
    with path.open("rb") as fh:
        prefix = fh.read(8)
        if len(prefix) != 8:
            raise LoraCompatibilityError(
                f"Cannot inspect '{path.name}': truncated safetensors header."
            )
        header_size = struct.unpack("<Q", prefix)[0]
        if header_size <= 0 or header_size > MAX_HEADER_BYTES:
            raise LoraCompatibilityError(
                f"Cannot inspect '{path.name}': invalid safetensors header size "
                f"{header_size}."
            )
        if header_size > size - 8:
            raise LoraCompatibilityError(
                f"Cannot inspect '{path.name}': safetensors header exceeds the file."
            )
        raw = fh.read(header_size)
    try:
        header = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LoraCompatibilityError(
            f"Cannot inspect '{path.name}': invalid safetensors JSON header ({exc})."
        ) from exc
    if not isinstance(header, dict):
        raise LoraCompatibilityError(
            f"Cannot inspect '{path.name}': safetensors header is not an object."
        )
    return {
        str(key): value
        for key, value in header.items()
        if key != "__metadata__" and isinstance(value, dict)
    }


def read_tensor_header(path: str | Path) -> dict[str, dict]:
    """Return tensor metadata without materialising any tensor payload."""
    p = Path(path)
    return _tensor_header_cached(*_stat_key(p))


def remap_ltx_lora_key(key: str) -> str:
    """Mirror ``LTXV_LORA_COMFY_RENAMING_MAP`` from the pinned engine."""
    replacements = (
        ("diffusion_model.", ""),
        (".to_out.0.", ".to_out."),
        (".ff.net.0.proj.", ".ff.proj_in."),
        (".ff.net.2.", ".ff.proj_out."),
        (".linear_1.", ".linear1."),
        (".linear_2.", ".linear2."),
        ("audio_ff.net.0.proj.", "audio_ff.proj_in."),
        ("audio_ff.net.2.", "audio_ff.proj_out."),
    )
    for old, new in replacements:
        key = key.replace(old, new)
    return key


def _model_weight_keys(header: Mapping[str, object]) -> set[str]:
    out: set[str] = set()
    for key in header:
        # load_split_safetensors(..., prefix="transformer.") removes this
        # namespace before the LoRA matcher sees the model state dict.
        if key.startswith("transformer."):
            key = key[len("transformer.") :]
        out.add(key)
    return out


def inspect_lora_compatibility(
    lora_path: str | Path,
    transformer_path: str | Path,
    *,
    minimum_match_ratio: float = MIN_MATCH_RATIO,
) -> LoraCompatibility:
    """Compare one LoRA's complete A/B pairs with one transformer header."""
    lora = Path(lora_path)
    transformer = Path(transformer_path)
    lora_keys = {remap_ltx_lora_key(key) for key in read_tensor_header(lora)}
    model_keys = _model_weight_keys(read_tensor_header(transformer))

    a_modules = {
        key[: -len(LORA_A_SUFFIX)]
        for key in lora_keys
        if key.endswith(LORA_A_SUFFIX)
    }
    b_modules = {
        key[: -len(LORA_B_SUFFIX)]
        for key in lora_keys
        if key.endswith(LORA_B_SUFFIX)
    }
    paired = a_modules & b_modules
    matched = {module for module in paired if f"{module}.weight" in model_keys}
    return LoraCompatibility(
        lora_path=lora,
        transformer_path=transformer,
        declared_tensors=len(a_modules) + len(b_modules),
        paired_modules=len(paired),
        matched_modules=len(matched),
        matched_module_names=frozenset(matched),
        minimum_match_ratio=minimum_match_ratio,
    )


@dataclass(frozen=True)
class AdapterEffect:
    """How much an adapter can move the weights it attaches to.

    ``deltas`` holds one ``‖B @ A‖_F / sqrt(in * out)`` per complete module —
    the RMS of the delta the module will actually add, which is comparable
    across shapes and ranks in a way the raw Frobenius norm is not.
    """

    lora_path: Path
    modules: int
    zero_modules: int
    median_rms: float
    max_rms: float
    floor: float = WEAK_DELTA_RMS

    @property
    def inert(self) -> bool:
        """Every module's delta is exactly zero — the adapter cannot do anything."""
        return self.modules > 0 and self.zero_modules == self.modules

    @property
    def weak(self) -> bool:
        return self.modules > 0 and not self.inert and self.median_rms < self.floor

    @property
    def summary(self) -> str:
        return (
            f"delta_rms median={self.median_rms:.2e} max={self.max_rms:.2e} "
            f"({self.modules - self.zero_modules}/{self.modules} modules carry a delta)"
        )

    def failure_message(self) -> str:
        return (
            f"LoRA '{self.lora_path.name}' carries no weight delta at all: "
            f"{self.summary}. Every low-rank product is exactly zero, so "
            "attaching it cannot change a single pixel. The file is a "
            "freshly-initialised adapter — training never moved it. Rendering "
            "was refused instead of returning a LoRA-free result."
        )

    def weak_message(self) -> str:
        return (
            f"LoRA '{self.lora_path.name}' is far weaker than any adapter that "
            f"has carried an identity here: {self.summary}, against "
            f"{self.floor:.1e} for the weakest working reference and ~1.4e-03 "
            "for the validated character recipe. Expect little or no visible "
            "effect."
        )

    def require_effective(self) -> None:
        if self.inert:
            raise LoraCompatibilityError(self.failure_message())


def _numpy():
    """Import numpy on demand, or return ``None``.

    This module promises to be importable anywhere — the panel, the helper and
    the trainer all use it, and one of them may be a bare interpreter. The
    strength probe is the only part that needs an array library, so it degrades
    to "not measured" rather than taking the whole module down with it.
    """
    try:
        import numpy  # noqa: PLC0415 — deliberate optional dependency
    except ImportError:  # pragma: no cover — every supported env ships numpy
        return None
    return numpy


_DTYPE_READERS = {"F32": ("<f4", None), "F16": ("<f2", None), "BF16": ("<u2", "bf16")}


def measure_adapter_effect(
    lora_path: str | Path, *, floor: float = WEAK_DELTA_RMS
) -> AdapterEffect | None:
    """Measure the size of the delta an adapter would add, per module.

    ``‖B @ A‖_F`` is computed **without forming** ``B @ A``: for
    ``G = Bᵀ B`` and ``H = A Aᵀ`` (both ``r × r``),
    ``‖B A‖_F² = trace(G H)``.  At rank 32 on a 4096-wide projection that is
    two thin matmuls instead of a 16 M-element product, so measuring a whole
    500 MB character adapter costs about a second and no meaningful memory.

    Returns ``None`` when numpy is unavailable or the file declares no complete
    ``lora_A``/``lora_B`` pair (that second case is already the province of
    :func:`inspect_lora_compatibility`, which fails closed on it).
    """
    np = _numpy()
    if np is None:
        return None
    path = Path(lora_path)
    header = read_tensor_header(path)
    modules = sorted(
        {
            key[: -len(LORA_A_SUFFIX)]
            for key in header
            if key.endswith(LORA_A_SUFFIX)
        }
        & {
            key[: -len(LORA_B_SUFFIX)]
            for key in header
            if key.endswith(LORA_B_SUFFIX)
        }
    )
    if not modules:
        return None

    with path.open("rb") as fh:
        header_size = struct.unpack("<Q", fh.read(8))[0]
        payload_base = 8 + header_size

        def read(key: str):
            info = header[key]
            spec = _DTYPE_READERS.get(str(info.get("dtype")))
            if spec is None:
                return None
            start, end = info["data_offsets"]
            fh.seek(payload_base + start)
            raw = fh.read(end - start)
            dtype, conversion = spec
            flat = np.frombuffer(raw, dtype=dtype)
            if conversion == "bf16":
                flat = (flat.astype(np.uint32) << 16).view(np.float32)
            return flat.astype(np.float32).reshape(info["shape"])

        rms: list[float] = []
        zero = 0
        for module in modules:
            a = read(module + LORA_A_SUFFIX)
            b = read(module + LORA_B_SUFFIX)
            if a is None or b is None or a.ndim != 2 or b.ndim != 2:
                continue
            if a.shape[0] != b.shape[1]:
                continue  # rank disagreement: a shape problem, not a size one
            gram = b.T @ b
            cov = a @ a.T
            frob = float(np.sqrt(max(float((gram * cov.T).sum()), 0.0)))
            entries = float(a.shape[1]) * float(b.shape[0])
            value = frob / (entries**0.5) if entries else 0.0
            rms.append(value)
            if value == 0.0:
                zero += 1

    if not rms:
        return None
    rms.sort()
    middle = len(rms) // 2
    median = rms[middle] if len(rms) % 2 else (rms[middle - 1] + rms[middle]) / 2
    return AdapterEffect(
        lora_path=path,
        modules=len(rms),
        zero_modules=zero,
        median_rms=median,
        max_rms=rms[-1],
        floor=floor,
    )


def validate_adapter_effects(
    loras: Iterable[tuple[str, float]],
    *,
    reporter: Callable[[str], None] | None = None,
) -> list[AdapterEffect]:
    """Report every adapter's strength, and refuse the ones that are all zero.

    Sits beside :func:`validate_lora_stack`: that one answers "can these keys
    land on this checkpoint", this one answers "is there anything in them".
    A weak-but-nonzero adapter is reported, never refused — the owner may be
    deliberately running a light touch, and a threshold has no business
    outranking a person.
    """
    measured: list[AdapterEffect] = []
    for index, (path, strength) in enumerate(loras, start=1):
        if float(strength) == 0.0:
            continue
        effect = measure_adapter_effect(path)
        if effect is None:
            continue
        if reporter is not None:
            reporter(f"LoRA[{index}] {effect.summary} file={effect.lora_path.name}")
            if effect.weak:
                reporter(f"LoRA[{index}] WARNING: {effect.weak_message()}")
        effect.require_effective()
        measured.append(effect)
    return measured


def resolve_distilled_transformer(model_dir: str | Path) -> Path | None:
    """Resolve the distilled file using the pinned pipeline's precedence."""
    root = Path(model_dir)
    direct = root / "transformer.safetensors"
    if direct.is_file():
        return direct
    versioned = sorted(root.glob("transformer-distilled-*.safetensors"))
    if versioned:
        return versioned[-1]
    fallback = root / "transformer-distilled.safetensors"
    return fallback if fallback.is_file() else None


def validate_lora_stack(
    loras: Iterable[tuple[str, float]],
    transformer_path: str | Path,
    *,
    reporter: Callable[[str], None] | None = None,
) -> list[LoraCompatibility]:
    """Validate and report every non-zero adapter before transformer load."""
    reports: list[LoraCompatibility] = []
    for index, (path, strength) in enumerate(loras, start=1):
        strength = float(strength)
        if strength == 0.0:
            if reporter is not None:
                reporter(
                    f"LoRA[{index}] file={Path(path).name} strength=0.00 "
                    "SKIPPED=disabled"
                )
            continue
        report = inspect_lora_compatibility(path, transformer_path)
        if reporter is not None:
            reporter(
                f"LoRA[{index}] file={Path(path).name} strength={strength:.2f} "
                f"{report.tally}"
            )
        report.require_compatible()
        reports.append(report)
    return reports


#: Exit codes for :func:`main`.  ``weak`` deliberately does NOT fail: a light
#: touch is the owner's call, not a threshold's (same doctrine as
#: :func:`validate_adapter_effects`).  Only ``inert`` is unambiguously a failed
#: training, and only an unreadable path is unambiguously the caller's mistake.
_EXIT_FOR_STATUS = {"inert": 1, "missing": 2, "error": 2, "unmeasured": 2}


def describe_adapter(lora_path: str | Path) -> tuple[str, str]:
    """``(status, line)`` describing what a single adapter can do.

    ``status`` is one of ``ok`` / ``weak`` / ``inert`` / ``missing`` /
    ``error`` / ``unmeasured``; ``line`` is the human-readable report.

    The whole #62 investigation was a person spending days proving a negative
    about a file that was sitting on their disk the entire time.  The number
    that answers it costs about a second to compute, so it should not require
    a render, a re-train, or a 500 MB upload to see.
    """
    path = Path(lora_path)
    if not path.is_file():
        return "missing", f"{path.name}: NOT FOUND ({path})"
    try:
        effect = measure_adapter_effect(path)
    except LoraCompatibilityError as exc:
        return "error", f"{path.name}: UNREADABLE — {exc}"
    if effect is None:
        return "unmeasured", (
            f"{path.name}: NOT MEASURED — either numpy is unavailable or the "
            "file declares no complete lora_A/lora_B pair."
        )
    if effect.inert:
        status = "inert"
        verdict = "INERT — training moved nothing; treat the run as failed"
    elif effect.weak:
        status = "weak"
        verdict = (
            f"WEAK — under {effect.floor:.1e}, the floor every adapter that "
            "has carried an identity here sits above"
        )
    else:
        status = "ok"
        verdict = "OK — in the band adapters that work sit in"
    return status, f"{path.name}: {effect.summary} — {verdict}"


def main(argv: list[str] | None = None) -> int:
    """`python3 lora_compat.py <adapter.safetensors> [...]`.

    Prints the delta each adapter carries and a verdict per file.  Exit 1
    means at least one file is INERT (every low-rank product exactly zero) —
    the one state that is unambiguously a failed training.  Exit 2 means a
    path could not be measured at all, which is the caller's mistake rather
    than the adapter's.  WEAK is reported and never fails the command: a
    light touch is the owner's call, not a threshold's.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        sys.stdout.write(
            "usage: python3 lora_compat.py <adapter.safetensors> [more ...]\n"
            "\n"
            "Measures the size of the weight delta each LoRA carries and says\n"
            "whether it is big enough to change a render. Reference band: the\n"
            "characters this repo ships measure 8.8e-04 to 1.6e-03; a freshly\n"
            "initialised adapter that never trained measures ~1.7e-05.\n"
        )
        return 0 if args else 2
    worst = 0
    for raw in args:
        status, line = describe_adapter(raw)
        sys.stdout.write(line + "\n")
        worst = max(worst, _EXIT_FOR_STATUS.get(status, 0))
    return worst


def validate_runtime_application(
    loras: Iterable[tuple[LoraCompatibility, float]],
    applied_module_names: Iterable[str],
    *,
    reporter: Callable[[str], None] | None = None,
) -> None:
    """Refuse when the live loader attached anomalously few expected modules.

    Header compatibility proves that an adapter *can* target the checkpoint.
    This second gate proves that the loader actually routed those modules into
    the live model.  It catches implementation regressions such as a wrong
    namespace/block prefix, which otherwise turn a valid character LoRA into a
    silent no-op after preflight has passed.
    """
    applied = set(applied_module_names)
    for index, (report, strength) in enumerate(loras, start=1):
        strength = float(strength)
        if strength == 0.0:
            continue
        live_count = len(report.matched_module_names & applied)
        expected_count = report.matched_modules
        live_ratio = live_count / expected_count if expected_count else 0.0
        tally = (
            f"FUSED={live_count * 2}/{report.declared_tensors} tensors "
            f"({live_count}/{report.paired_modules} modules)"
        )
        if reporter is not None:
            reporter(
                f"LoRA[{index}] strength={strength:.2f} {tally} "
                f"file={report.lora_path.name}"
            )
        if live_count <= 0 or live_ratio < report.minimum_match_ratio:
            raise LoraCompatibilityError(
                f"LoRA '{report.lora_path.name}' failed to attach to "
                f"{report.transformer_path.name}: {tally}; the live loader "
                f"applied only {live_ratio:.1%} of the compatible modules "
                f"(minimum {report.minimum_match_ratio:.0%}). Rendering was "
                "refused before it could produce a LoRA-free result."
            )


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
