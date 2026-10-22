"""Is the Hailuo H3 venv on its known-good set, and does the runner load? Exit 0 = yes.

Run by scripts/pinokio/h3_deps.sh with the H3 venv's OWN Python, right after
the constrained install:

    .venv/bin/python scripts/pinokio/h3_env_check.py scripts/pinokio/h3_constraints.txt

1. Every exact pin (`name==version`) in the constraints file whose package is
   installed matches it. A pin for a package the resolve did not need is not a
   problem - a constraint never installs anything by itself.
2. The packages the runner cannot start without are installed at all.
3. The imports scripts/generate_staged.py makes before its first step load -
   mlx, the Qwen3-VL text encoder from mlx-vlm, transformers' tokenizer and
   processor. transformers checks its own dependencies' versions at import and
   refuses a mismatch (the 4.19.4 tokenizers class), so this also proves the
   set fits together.

Stdlib only (it runs in the H3 venv, which does not have the panel's code);
prints one line per problem and never a path under the home directory
(Pinokio shows this output and a bug report quotes it). The panel's preflight
(mlx_ltx_panel.h3_env_fault) reads the same file the same way, without
importing anything.
"""
from __future__ import annotations

import importlib
import importlib.metadata as md
import re
import sys
from pathlib import Path

#: Without these the runner cannot start. Kept in step with H3_ENV_JUDGED in
#: mlx_ltx_panel.py (test_h3_env_lock.py holds the two together).
REQUIRED = ("mlx", "mlx-metal", "mlx-vlm", "transformers", "tokenizers",
            "huggingface-hub", "safetensors", "numpy", "pillow")
IMPORTS = (
    "mlx.core",
    "numpy",
    "PIL.Image",
    "safetensors",
    "mlx_vlm.models.qwen3_vl.config",
    "mlx_vlm.models.qwen3_vl.language",
    "mlx_vlm.models.qwen3_vl.vision",
    "transformers",
)


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pins(path: Path) -> dict:
    """{normalised name: version} for every `name==version` line."""
    out: dict = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        m = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([^\s,;]+)", line)
        if m:
            out[norm(m.group(1))] = m.group(2)
    return out


def installed() -> dict:
    out: dict = {}
    for d in md.distributions():
        name = d.metadata.get("Name") if d.metadata else None
        if name:
            out[norm(name)] = d.version
    return out


def problems(constraints: Path) -> list[str]:
    found: list[str] = []
    want = pins(constraints)
    have = installed()
    for name in REQUIRED:
        if name not in have:
            found.append(f"{name} is not installed")
    for name, ver in sorted(want.items()):
        got = have.get(name)
        if got is not None and got != ver:
            found.append(f"{name} {got} is installed, the known-good set says {ver}")
    for mod in IMPORTS:
        try:
            importlib.import_module(mod)
        except Exception as exc:                                 # noqa: BLE001
            found.append(f"import {mod} fails: {type(exc).__name__}: "
                         f"{str(exc).splitlines()[0][:200] if str(exc) else ''}")
    if not found:
        try:
            from transformers import AutoProcessor, AutoTokenizer  # noqa: F401
        except Exception as exc:                                 # noqa: BLE001
            found.append(f"transformers' tokenizer/processor do not load: "
                         f"{type(exc).__name__}")
    return found


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: h3_env_check.py <h3_constraints.txt>")
        return 2
    found = problems(Path(argv[1]))
    for p in found:
        print("H3 check: " + p)
    if not found:
        print("H3 check: ok - the H3 venv is on its known-good set and the runner's imports load")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
