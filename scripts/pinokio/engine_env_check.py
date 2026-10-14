"""Can a render start in this venv? Exit 0 = yes.

Run by scripts/pinokio/ltx_engine_env.sh with the ENGINE venv's own Python,
after the package install. It asks the panel's question
(mlx_ltx_panel.engine_env_fault): the engine packages exist as real
directories in site-packages - not only as the workspace's editable links,
which is what the dependency pass alone leaves, and which the codec patch
cannot find - and they import.

Stdlib only; prints one line per problem and never a path under the home
directory (Pinokio shows this output, and a bug report quotes it).
"""
from __future__ import annotations

import importlib
import sys
import sysconfig
from pathlib import Path

PACKAGES = ("ltx_core_mlx", "ltx_pipelines_mlx")
IMPORTS = ("ltx_core_mlx", "ltx_pipelines_mlx", "mlx.core")


def problems() -> list[str]:
    out: list[str] = []
    site = Path(sysconfig.get_paths()["purelib"])
    for name in PACKAGES:
        if not (site / name).is_dir():
            out.append(f"{name} is not installed as a package directory "
                       f"(missing, or only an editable link)")
    for mod in IMPORTS:
        try:
            importlib.import_module(mod)
        except Exception as exc:                                 # noqa: BLE001
            out.append(f"import {mod} fails: {type(exc).__name__}")
    return out


def main() -> int:
    found = problems()
    for p in found:
        print("engine check: " + p)
    if not found:
        print("engine check: ok")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
