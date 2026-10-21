"""Every install script that can fail says so in words Pinokio acts on (4.20).

Pinokio 8.2 IGNORES a shell.run step's exit code. The kernel's default `on`
handlers (pinokiod kernel/shells.js) end a script only when the step's OUTPUT
matches /error:/i or /errno /i. A script that refused and printed neither word
let the install march on: a refused 75 GB H3 download started anyway, a failed
engine checkout went on to a 37 GB download, a failed LTX-2.5 fetch ended in
"Phosphene is ready" with no weights.

The contract these tests hold, per script:
  * FAILURE: exits non-zero AND prints a "FATAL error: <reason + next click>"
    line, so the kernel's own regex stops the run;
  * SUCCESS: prints nothing the kernel would stop on;
  * OPTIONAL steps (post_update.sh's `defang`, mflux_pack, the H3 fetches) turn
    a failure into a plain WARN: they never say "error:" / "errno ".

Everything runs the REAL scripts in a throwaway app root whose git, uv, sysctl,
sw_vers and df are stubs. No network, no downloads, no GPU.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
PIN = ROOT / "scripts" / "pinokio"

# pinokiod kernel/shells.js default `on` handlers, exactly as the emulator and
# test_install_deferred.py apply them.
_BREAK = re.compile(r"error:|errno ", re.I)


def stops(text: str) -> bool:
    """True when Pinokio 8.2 would end the script on this step output."""
    return bool(_BREAK.search(re.sub(r"error:.*triton", "", text.replace("\n", ""), flags=re.I)))


def fatal_lines(text: str) -> list[str]:
    return [l for l in text.splitlines() if l.startswith("FATAL error:")]


class Sandbox:
    """A throwaway app root with stub tools first on PATH."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        self.app = self.base / "app"
        (self.app / "scripts" / "pinokio").mkdir(parents=True)
        (self.app / "scripts" / "music").mkdir(parents=True)
        self.bin = self.base / "bin"
        self.bin.mkdir()

    def close(self):
        self._tmp.cleanup()

    def install(self, *rels: str):
        for rel in rels:
            dst = self.app / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / rel, dst)

    def stub(self, name: str, body: str):
        f = self.bin / name
        f.write_text("#!/bin/sh\n" + textwrap.dedent(body))
        f.chmod(0o755)

    def run(self, cmd, cwd=None, env=None, timeout=60):
        e = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.base)}
        e.update(env or {})
        r = subprocess.run(cmd, cwd=cwd or self.app, env=e, capture_output=True,
                           text=True, errors="replace", timeout=timeout)
        return r.returncode, r.stdout + r.stderr


class Base(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.close)

    def fails_loudly(self, rc, out, *needles):
        self.assertNotEqual(rc, 0, out)
        self.assertTrue(stops(out), f"Pinokio would NOT stop on this output:\n{out}")
        lines = fatal_lines(out)
        self.assertEqual(len(lines), 1, f"want exactly one FATAL line, got {lines}\n{out}")
        for n in needles:
            self.assertIn(n, lines[0])
        self.assertNotIn("Traceback", lines[0])

    def runs_clean(self, rc, out):
        self.assertEqual(rc, 0, out)
        self.assertFalse(stops(out), f"a success path Pinokio would stop on:\n{out}")
        self.assertNotIn("FATAL", out)


# --------------------------------------------------------------------------
# Preflights
# --------------------------------------------------------------------------
class H3Preflight(Base):
    def setUp(self):
        super().setUp()
        self.sb.install("scripts/pinokio/h3_preflight.sh")
        self.cmd = ["bash", "scripts/pinokio/h3_preflight.sh"]

    def _df(self, kb):
        self.sb.stub("df", f'echo "Filesystem 1024-blocks Used Available Capacity Mounted on"\n'
                           f'echo "fake 9 1 {kb} 1% /"\n')

    def test_a_small_mac_stops_the_install(self):
        self.sb.stub("sysctl", "echo 17179869184\n")           # 16 GB
        self._df(900_000_000)
        rc, out = self.sb.run(self.cmd)
        self.fails_loudly(rc, out, "36 GB", "17 GB")

    def test_not_enough_disk_stops_the_install(self):
        self.sb.stub("sysctl", "echo 68719476736\n")           # 64 GB
        self._df(5_000_000)                                    # ~4 GB
        rc, out = self.sb.run(self.cmd)
        self.fails_loudly(rc, out, "97 GB", "4 GB")

    def test_a_big_mac_with_room_prints_nothing_scary(self):
        self.sb.stub("sysctl", "echo 68719476736\n")
        self._df(900_000_000)
        self.runs_clean(*self.sb.run(self.cmd))

    def test_unreadable_hardware_is_fail_open(self):
        self.sb.stub("sysctl", "exit 1\n")
        self._df(900_000_000)
        rc, out = self.sb.run(self.cmd)
        self.assertFalse(stops(out), out)


class MusicPreflight(Base):
    def setUp(self):
        super().setUp()
        self.sb.install("scripts/pinokio/music_preflight.sh")
        self.cmd = ["bash", "scripts/pinokio/music_preflight.sh"]

    def _df(self, kb):
        self.sb.stub("df", f'echo "Filesystem 1024-blocks Used Available Capacity Mounted on"\n'
                           f'echo "fake 9 1 {kb} 1% /"\n')

    def test_under_24_gb_stops_the_install(self):
        self.sb.stub("sysctl", "echo 17179869184\n")
        self._df(900_000_000)
        self.fails_loudly(*self.sb.run(self.cmd), "24 GB")

    def test_under_14_gb_free_stops_the_install(self):
        self.sb.stub("sysctl", "echo 68719476736\n")
        self._df(2_000_000)
        self.fails_loudly(*self.sb.run(self.cmd), "14 GB free")

    def test_a_good_mac_prints_nothing_scary(self):
        self.sb.stub("sysctl", "echo 68719476736\n")
        self._df(900_000_000)
        self.runs_clean(*self.sb.run(self.cmd))


class InstallPreflight(Base):
    """The 5b18448 disk preflight: install_plan.py prints the FATAL line; the
    shell adds one only if it ever exits 1 without."""

    def setUp(self):
        super().setUp()
        self.sb.install("scripts/pinokio/install_preflight.sh")
        self.cmd = ["bash", "scripts/pinokio/install_preflight.sh"]

    def _plan(self, body):
        (self.sb.app / "install_plan.py").write_text(body)

    def test_a_plan_that_prints_fatal_is_not_doubled(self):
        self._plan("import sys\nprint('FATAL error: not enough disk space')\nsys.exit(1)\n")
        rc, out = self.sb.run(self.cmd)
        self.fails_loudly(rc, out, "not enough disk space")

    def test_a_plan_that_exits_1_silently_still_stops(self):
        self._plan("import sys\nprint('short on space')\nsys.exit(1)\n")
        self.fails_loudly(*self.sb.run(self.cmd), "preflight")

    def test_a_crashing_preflight_is_fail_open_and_quiet(self):
        self._plan("import sys\nsys.exit(3)\n")
        rc, out = self.sb.run(self.cmd)
        self.assertEqual(rc, 0, out)
        self.assertFalse(stops(out), out)

    def test_success(self):
        self._plan("print('Install preflight OK - 40 GB free, needs 32.5 GB')\n")
        self.runs_clean(*self.sb.run(self.cmd))


# --------------------------------------------------------------------------
# LTX engine lane
# --------------------------------------------------------------------------
class LtxVenv(Base):
    def setUp(self):
        super().setUp()
        self.sb.install("scripts/pinokio/ltx_venv.sh")
        self.eng = self.sb.app / "ltx-2-mlx"
        self.eng.mkdir()
        self.cmd = ["bash", "../scripts/pinokio/ltx_venv.sh"]
        # a uv that builds a venv whose python3.11 runs
        self.sb.stub("uv", '''
            if [ "$1" = venv ]; then
              for a in "$@"; do d="$a"; done
              mkdir -p "$d/bin"
              printf '#!/bin/sh\\nexit 0\\n' > "$d/bin/python3.11"; chmod +x "$d/bin/python3.11"
              cp "$d/bin/python3.11" "$d/bin/python"
            fi
            exit 0
        ''')

    def test_macos_13_stops_the_install(self):
        self.sb.stub("sw_vers", "echo 13.6.1\n")
        self.fails_loudly(*self.sb.run(self.cmd, cwd=self.eng), "macOS 14", "13.6.1")

    def test_a_venv_that_cannot_be_created_stops_the_install(self):
        self.sb.stub("sw_vers", "echo 15.1\n")
        self.sb.stub("uv", "echo 'uv: no network' >&2\nexit 1\n")
        self.fails_loudly(*self.sb.run(self.cmd, cwd=self.eng), "Python 3.11", "Install again")

    def test_a_missing_uv_stops_the_install(self):
        self.sb.stub("sw_vers", "echo 15.1\n")
        (self.sb.bin / "uv").unlink()
        self.fails_loudly(*self.sb.run(self.cmd, cwd=self.eng), "Python 3.11")

    def test_rebuild_success_prints_nothing_scary(self):
        self.sb.stub("sw_vers", "echo 15.1\n")
        rc, out = self.sb.run(self.cmd, cwd=self.eng)
        self.runs_clean(rc, out)
        self.assertTrue((self.eng / "env/bin/python3.11").is_file())

    def test_a_healthy_venv_is_reused_quietly(self):
        (self.eng / "env/bin").mkdir(parents=True)
        (self.eng / "env/bin/python3.11").write_text("#!/bin/sh\nexit 0\n")
        (self.eng / "env/bin/python3.11").chmod(0o755)
        rc, out = self.sb.run(self.cmd, cwd=self.eng)
        self.runs_clean(rc, out)
        self.assertIn("healthy", out)


GIT_STUB = '''
    # args: the whole command; behaviour switched by env
    case "$1" in
      fetch)
        case "$*" in
          *--tags*) [ -n "$FAKE_TAGS_FAIL" ] && { echo "error: RPC failed; curl 56 [Errno 54]" >&2; echo "fatal: early EOF" >&2; exit 1; } ;;
          *) [ -n "$FAKE_FETCH_FAIL" ] && { echo "fatal: unable to access 'https://x/': Could not resolve host" >&2; exit 128; } ;;
        esac ;;
      checkout) [ -n "$FAKE_CHECKOUT_FAIL" ] && { echo "error: pathspec did not match" >&2; exit 1; } ;;
      remote) [ "$2" = get-url ] && exit 1 ;;
      rev-parse)
        case "$*" in
          *--short*) echo abc1234 ;;
          *'^{commit}'*) echo "${FAKE_WANT:-aaaaaaaa}" ;;
          *) echo "${FAKE_GOT:-aaaaaaaa}" ;;
        esac ;;
      status) : ;;
    esac
    exit 0
'''


class LtxCheckout(Base):
    def setUp(self):
        super().setUp()
        self.sb.install("scripts/pinokio/ltx_checkout.sh")
        (self.sb.app / "ltx-2-mlx").mkdir()
        self.sb.stub("git", GIT_STUB)
        self.cmd = ["bash", "scripts/pinokio/ltx_checkout.sh"]

    def test_a_missing_engine_folder_stops_the_install(self):
        shutil.rmtree(self.sb.app / "ltx-2-mlx")
        self.fails_loudly(*self.sb.run(self.cmd), "engine folder is missing")

    def test_an_unfetchable_pin_stops_the_install(self):
        self.fails_loudly(*self.sb.run(self.cmd, env={"FAKE_FETCH_FAIL": "1"}),
                          "v0.14.19", "internet")

    def test_a_failed_checkout_stops_the_install(self):
        self.fails_loudly(*self.sb.run(self.cmd, env={"FAKE_CHECKOUT_FAIL": "1"}), "switch")

    def test_the_wrong_commit_stops_the_install(self):
        self.fails_loudly(*self.sb.run(self.cmd, env={"FAKE_WANT": "aaaa", "FAKE_GOT": "bbbb"}),
                          "wrong version")

    def test_success_prints_nothing_scary(self):
        rc, out = self.sb.run(self.cmd)
        self.runs_clean(rc, out)
        self.assertIn("vendored engine at", out)

    def test_upstream_tags_are_optional_and_never_say_error(self):
        # git prints "error: RPC failed ... [Errno 54]" for a network blip; the
        # tags are best-effort, so that must not end an install that does not
        # need them.
        rc, out = self.sb.run(self.cmd, env={"FAKE_TAGS_FAIL": "1"})
        self.assertEqual(rc, 0, out)
        self.assertFalse(stops(out), out)
        self.assertIn("WARN: could not reach origin", out)


class MfluxPackIsOptional(Base):
    def setUp(self):
        super().setUp()
        self.sb.install("scripts/pinokio/mflux_pack.sh")
        self.cmd = ["bash", "scripts/pinokio/mflux_pack.sh"]

    def test_a_failed_pack_warns_and_never_stops_the_install(self):
        self.sb.stub("uv", "echo 'error: Failed to download mflux' >&2\necho '[Errno 8] x' >&2\nexit 2\n")
        rc, out = self.sb.run(self.cmd)
        self.assertFalse(stops(out), out)
        self.assertIn("WARN: mflux image-engine install", out)

    def test_success(self):
        self.sb.stub("uv", "exit 0\n")
        eng = self.sb.app / "ltx-2-mlx/env/bin"
        eng.mkdir(parents=True)
        (eng / "python3.11").write_text("#!/bin/sh\nexit 0\n")
        (eng / "python3.11").chmod(0o755)
        self.runs_clean(*self.sb.run(self.cmd))


# --------------------------------------------------------------------------
# H3 lane
# --------------------------------------------------------------------------
class H3Roots(Base):
    def setUp(self):
        super().setUp()
        self.sb.install("scripts/pinokio/h3_roots.sh")
        self.ck = self.sb.app / "minimax-h3-mlx"

    def _sh(self, tail, env=None):
        return self.sb.run(["bash", "-c", f". scripts/pinokio/h3_roots.sh && {tail}"], env=env)

    def test_a_missing_checkout_stops_the_install(self):
        self.fails_loudly(*self._sh("h3_enter && echo entered"), "engine folder is missing")

    def test_an_existing_checkout_is_entered_quietly(self):
        self.ck.mkdir()
        rc, out = self._sh("h3_enter && pwd")
        self.assertEqual(rc, 0, out)
        self.assertIn(str(self.ck), out)
        self.assertFalse(stops(out), out)

    def test_a_failed_weights_download_stops_the_install(self):
        (self.ck / ".venv/bin").mkdir(parents=True)
        (self.ck / "scripts").mkdir()
        (self.ck / ".venv/bin/python").write_text("#!/bin/sh\nexit 1\n")
        (self.ck / ".venv/bin/python").chmod(0o755)
        self.fails_loudly(*self._sh("h3_enter && h3_fetch_weights"), "75 GB", "Install again")

    def test_an_incomplete_flat_tree_stops_the_install(self):
        models = self.sb.base / "flat"
        dit = models / "deepbeep-pruned-bf16/MiniMax-H3-FL2VA-pruned_bf16.safetensors"
        dit.parent.mkdir(parents=True)
        dit.write_bytes(b"x")
        self.ck.mkdir()
        self.fails_loudly(*self._sh("h3_enter && h3_fetch_weights", env={"LTX_H3_MODELS": str(models)}),
                          "flat H3 weights tree")

    def test_a_good_download_prints_nothing_scary(self):
        (self.ck / ".venv/bin").mkdir(parents=True)
        (self.ck / ".venv/bin/python").write_text("#!/bin/sh\necho downloading\nexit 0\n")
        (self.ck / ".venv/bin/python").chmod(0o755)
        self.runs_clean(*self._sh("h3_enter && h3_fetch_weights"))


class H3Checkout(Base):
    def setUp(self):
        super().setUp()
        self.sb.install("scripts/pinokio/h3_checkout.sh")
        self.cmd = ["bash", "scripts/pinokio/h3_checkout.sh"]

    def test_a_missing_checkout_stops_the_install(self):
        self.fails_loudly(*self.sb.run(self.cmd + [str(self.sb.base / "nope")]), "not on this Mac")

    def test_no_argument_stops_the_install(self):
        self.fails_loudly(*self.sb.run(self.cmd), "not on this Mac")

    def test_an_unfetchable_pin_stops_the_install(self):
        ck = self.sb.base / "ck"
        (ck / ".git").mkdir(parents=True)
        self.sb.stub("git", '''
            case "$1" in
              fetch) exit 128 ;;
              cat-file) exit 1 ;;
              remote) echo https://example.invalid/e.git ;;
            esac
            exit 0
        ''')
        self.fails_loudly(*self.sb.run(self.cmd + [str(ck)]), "download", "left it as it was"
                          if False else "left as it was")

    def test_a_failed_checkout_stops_the_install(self):
        ck = self.sb.base / "ck"
        (ck / ".git").mkdir(parents=True)
        self.sb.stub("git", '''
            case "$1" in
              checkout) exit 1 ;;
              remote) echo https://example.invalid/e.git ;;
            esac
            exit 0
        ''')
        self.fails_loudly(*self.sb.run(self.cmd + [str(ck)]), "switch")

    def test_a_pin_with_no_quantizer_stops_the_install(self):
        ck = self.sb.base / "ck"
        (ck / ".git").mkdir(parents=True)
        sha = re.search(r'^H3_PIN_SHA="([0-9a-f]+)"', (PIN / "h3_checkout.sh").read_text(), re.M).group(1)
        self.sb.stub("git", f'''
            case "$1" in
              rev-parse) [ "$2" = HEAD ] && echo {sha} ;;
              remote) echo https://example.invalid/e.git ;;
            esac
            exit 0
        ''')
        self.fails_loudly(*self.sb.run(self.cmd + [str(ck)]), "quantize_stream.py")

    def test_success_prints_nothing_scary(self):
        ck = self.sb.base / "ck"
        (ck / ".git").mkdir(parents=True)
        (ck / "minimax_h3_mlx").mkdir()
        (ck / "scripts").mkdir()
        (ck / "scripts/quantize_stream.py").write_text("")
        sha = re.search(r'^H3_PIN_SHA="([0-9a-f]+)"', (PIN / "h3_checkout.sh").read_text(), re.M).group(1)
        self.sb.stub("git", f'''
            case "$1" in
              rev-parse) echo {sha} ;;
              remote) echo https://example.invalid/e.git ;;
            esac
            exit 0
        ''')
        self.runs_clean(*self.sb.run(self.cmd + [str(ck)]))


class H3Venv(Base):
    def setUp(self):
        super().setUp()
        self.sb.install("scripts/pinokio/h3_venv.sh")
        self.ck = self.sb.app / "ck"
        self.ck.mkdir()
        self.cmd = ["bash", "../scripts/pinokio/h3_venv.sh"]

    def test_a_venv_that_cannot_be_created_stops_the_install(self):
        self.sb.stub("uv", "exit 1\n")
        self.fails_loudly(*self.sb.run(self.cmd, cwd=self.ck), "Hailuo H3", "Install again")

    def test_rebuild_success_prints_nothing_scary(self):
        self.sb.stub("uv", '''
            if [ "$1" = venv ]; then
              d="${@: -1}"; mkdir -p "$d/bin"
              printf '#!/bin/sh\\nexit 0\\n' > "$d/bin/python"; chmod +x "$d/bin/python"
            fi
            exit 0
        ''')
        self.sb.stub("bash_dummy", "exit 0\n")
        self.runs_clean(*self.sb.run(["bash", "-c", "bash ../scripts/pinokio/h3_venv.sh"], cwd=self.ck))


@unittest.skipUnless(sys.platform == "darwin", "h3_deps.sh is the macOS set")
class H3Deps(Base):
    def setUp(self):
        super().setUp()
        self.sb.install("scripts/pinokio/h3_deps.sh", "scripts/pinokio/h3_constraints.txt")
        self.cmd = ["bash", "scripts/pinokio/h3_deps.sh"]

    def test_no_checkout_stops_the_install(self):
        self.fails_loudly(*self.sb.run(self.cmd + [str(self.sb.base / "nope")]), "no H3 engine checkout")

    def test_a_broken_venv_stops_the_install(self):
        ck = self.sb.base / "ck"
        ck.mkdir()
        (ck / "requirements.txt").write_text("")
        self.fails_loudly(*self.sb.run(self.cmd + [str(ck)]), "no working Python")

    def test_a_failed_package_install_stops_the_install(self):
        ck = self.sb.base / "ck"
        (ck / ".venv/bin").mkdir(parents=True)
        (ck / "requirements.txt").write_text("")
        (ck / ".venv/bin/python").write_text("#!/bin/sh\nexit 0\n")
        (ck / ".venv/bin/python").chmod(0o755)
        self.sb.stub("uv", "echo 'network down' >&2\nexit 1\n")
        self.fails_loudly(*self.sb.run(self.cmd + [str(ck)]), "did not install")


class H3BuildQ8(Base):
    def setUp(self):
        super().setUp()
        self.sb.install("scripts/pinokio/h3_build_q8.sh")
        self.ck = self.sb.app / "minimax-h3-mlx"
        (self.ck / ".venv/bin").mkdir(parents=True)
        self.cmd = ["bash", "../scripts/pinokio/h3_build_q8.sh", str(self.sb.app)]

    def _python(self, body):
        f = self.ck / ".venv/bin/python"
        f.write_text("#!/bin/sh\n" + body)
        f.chmod(0o755)

    def test_started_without_the_app_root_stops_the_install(self):
        self._python("exit 0\n")
        rc, out = self.sb.run(["bash", "../scripts/pinokio/h3_build_q8.sh"], cwd=self.ck)
        self.fails_loudly(rc, out, "app folder")

    def test_a_build_that_does_not_validate_stops_the_install(self):
        self._python("exit 1\n")
        rc, out = self.sb.run(self.cmd, cwd=self.ck)
        self.fails_loudly(rc, out, "compact Hailuo H3 engine", "Install again")

    def test_an_already_built_engine_prints_nothing_scary(self):
        self._python("exit 0\n")
        pack = self.sb.app / "mlx_models/hailuo-h3/models/h3-dit-q8"
        pack.mkdir(parents=True)
        (pack / ".built_ok").write_text("")
        (pack / "model.safetensors.index.json").write_text("{}")
        dit = self.sb.app / "mlx_models/hailuo-h3/models/deepbeep-pruned-bf16/MiniMax-H3-FL2VA-pruned_bf16.safetensors"
        dit.parent.mkdir(parents=True)
        dit.write_bytes(b"x")
        rc, out = self.sb.run(self.cmd, cwd=self.ck)
        self.runs_clean(rc, out)


# --------------------------------------------------------------------------
# Music lane
# --------------------------------------------------------------------------
class MusicScripts(Base):
    def setUp(self):
        super().setUp()
        self.sb.install("scripts/pinokio/music_clone.sh", "scripts/pinokio/music_checkout.sh",
                        "scripts/pinokio/music_venv.sh", "scripts/pinokio/music_sync.sh",
                        "scripts/music/engine_pin.txt")
        self.pin = (ROOT / "scripts/music/engine_pin.txt").read_text().strip()
        self.eng = self.sb.base / "yue2"

    def sh(self, name, *args, env=None):
        return self.sb.run(["bash", f"scripts/pinokio/{name}", *args], env=env)

    def test_clone_failure_stops_the_install(self):
        self.sb.stub("git", "echo \"fatal: unable to access 'https://x'\" >&2\nexit 128\n")
        self.fails_loudly(*self.sh("music_clone.sh", env={"LTX_MUSIC_ROOT": str(self.eng)}),
                          "music engine", "Install again")

    def test_clone_success(self):
        self.sb.stub("git", 'for a in "$@"; do d="$a"; done; mkdir -p "$d/.git"\nexit 0\n')
        self.runs_clean(*self.sh("music_clone.sh", env={"LTX_MUSIC_ROOT": str(self.eng)}))

    def test_checkout_without_a_clone_stops_the_install(self):
        self.fails_loudly(*self.sh("music_checkout.sh", str(self.eng)), "not on this Mac")

    def test_checkout_without_an_argument_stops_the_install(self):
        self.fails_loudly(*self.sh("music_checkout.sh"), "checkout step")

    def test_checkout_fetch_failure_stops_the_install(self):
        (self.eng / ".git").mkdir(parents=True)
        self.sb.stub("git", 'case "$1" in fetch) echo "fatal: x" >&2; exit 128;; esac\nexit 0\n')
        self.fails_loudly(*self.sh("music_checkout.sh", str(self.eng)), "download")

    def test_checkout_on_the_wrong_commit_stops_the_install(self):
        (self.eng / ".git").mkdir(parents=True)
        self.sb.stub("git", 'case "$1" in rev-parse) echo 0000000000000000000000000000000000000000;; esac\nexit 0\n')
        self.fails_loudly(*self.sh("music_checkout.sh", str(self.eng)), "wrong version")

    def test_checkout_success(self):
        (self.eng / ".git").mkdir(parents=True)
        self.sb.stub("git", f'case "$1" in rev-parse) echo {self.pin};; esac\nexit 0\n')
        self.runs_clean(*self.sh("music_checkout.sh", str(self.eng)))

    def test_venv_failure_stops_the_install(self):
        self.eng.mkdir()
        self.sb.stub("uv", "exit 1\n")
        self.fails_loudly(*self.sh("music_venv.sh", str(self.eng)), "Python 3.12")

    def test_venv_without_an_argument_stops_the_install(self):
        self.fails_loudly(*self.sh("music_venv.sh"), "venv step")

    def test_venv_success(self):
        self.eng.mkdir()
        self.sb.stub("uv", "exit 0\n")
        self.runs_clean(*self.sh("music_venv.sh", str(self.eng)))

    def test_sync_without_a_folder_stops_the_install(self):
        self.fails_loudly(*self.sh("music_sync.sh", str(self.eng)), "folder is missing")

    def test_sync_failure_stops_the_install(self):
        self.eng.mkdir()
        self.sb.stub("uv", "echo 'resolution failed' >&2\nexit 2\n")
        self.fails_loudly(*self.sh("music_sync.sh", str(self.eng)), "packages did not install")

    def test_sync_success(self):
        self.eng.mkdir()
        self.sb.stub("uv", "exit 0\n")
        self.runs_clean(*self.sh("music_sync.sh", str(self.eng)))


class MusicFetch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("music_fetch_under_test", PIN / "music_fetch.py")
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def _main(self, argv, **patches):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), mock.patch.multiple(self.mod, **patches):
            rc = self.mod.main(argv)
        return rc, out.getvalue() + err.getvalue()

    def test_a_failed_download_stops_the_install(self):
        def boom(*a, **k):
            raise ValueError("network unreachable")
        with tempfile.TemporaryDirectory() as d:
            rc, out = self._main(["--models", d], fetch_pack=boom)
        self.assertEqual(rc, 1)
        self.assertTrue(stops(out), out)
        self.assertEqual(len(fatal_lines(out)), 1, out)
        self.assertIn("network unreachable", fatal_lines(out)[0])      # the panel shows this line

    def test_the_update_verification_stays_quiet(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out = self._main(["--models", d, "--check"], pack_problems=lambda *a, **k: ["generator missing"])
        self.assertEqual(rc, 1)
        self.assertNotIn("FATAL", out)


# --------------------------------------------------------------------------
# Optional fetches never say error:
# --------------------------------------------------------------------------
class OptionalFetchesAreQuiet(unittest.TestCase):
    def _load(self, path, name):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_plain_defuses_both_stop_words(self):
        for path, name in ((ROOT / "scripts/fetch_h3_turbo.py", "turbo_t"),
                           (PIN / "h3_fetch_tae.py", "tae_t")):
            mod = self._load(path, name)
            msg = mod._plain("<urlopen error [Errno 8] nodename nor servname provided> Error: x")
            self.assertFalse(stops(msg), msg)
            self.assertIn("nodename", msg)                              # still says what happened

    def test_the_turbo_adapter_failing_says_nothing_pinokio_stops_on(self):
        import urllib.error
        mod = self._load(ROOT / "scripts/fetch_h3_turbo.py", "turbo_t2")
        err = urllib.error.URLError(OSError(8, "nodename nor servname provided"))
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as d, redirect_stderr(buf), redirect_stdout(buf), \
                mock.patch.object(mod.urllib.request, "urlopen", side_effect=err), \
                mock.patch.object(sys, "argv", ["x", "--dir", d]):
            rc = mod.main()
        self.assertEqual(rc, 1)
        self.assertIn("TURBO FETCH FAILED", buf.getvalue())
        self.assertFalse(stops(buf.getvalue()), buf.getvalue())

    def test_the_h3_draft_decoder_failing_warns_and_never_stops(self):
        import urllib.error
        mod = self._load(PIN / "h3_fetch_tae.py", "tae_t2")
        err = urllib.error.URLError(OSError(8, "nodename nor servname provided"))
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as d, redirect_stderr(buf), redirect_stdout(buf), \
                mock.patch.object(mod.urllib.request, "urlopen", side_effect=err), \
                mock.patch.object(sys, "argv", ["x", str(Path(d) / "tae.safetensors")]):
            rc = mod.main()
        self.assertEqual(rc, 1)
        self.assertIn("WARN: could not fetch a verified TAE", buf.getvalue())
        self.assertFalse(stops(buf.getvalue()), buf.getvalue())


class MustWrapper(Base):
    def setUp(self):
        super().setUp()
        self.sb.install("scripts/pinokio/must.sh")
        self.cmd = ["bash", "scripts/pinokio/must.sh"]

    def test_a_failing_command_stops_the_install_with_its_own_exit_code(self):
        rc, out = self.sb.run(self.cmd + ["The codec patch did not apply - click Install again", "sh", "-c", "echo banner; exit 4"])
        self.assertEqual(rc, 4, out)
        self.assertTrue(stops(out), out)
        self.assertIn("FATAL error: The codec patch did not apply - click Install again (exit 4)", out)

    def test_a_missing_command_stops_the_install(self):
        rc, out = self.sb.run(self.cmd + ["The engine is missing", "./no/such/python", "x.py"])
        self.assertEqual(rc, 127, out)
        self.assertTrue(stops(out), out)

    def test_a_good_command_prints_nothing_scary(self):
        rc, out = self.sb.run(self.cmd + ["never shown", "echo", "patched"])
        self.assertEqual((rc, out.strip()), (0, "patched"))

    def test_called_without_a_reason_is_loud(self):
        rc, out = self.sb.run(self.cmd)
        self.assertNotEqual(rc, 0)
        self.assertTrue(stops(out), out)


# --------------------------------------------------------------------------
# post_update.sh: optional steps defang, required steps stay required
# --------------------------------------------------------------------------
class PostUpdateDefang(Base):
    def _defang_fn(self) -> str:
        text = (ROOT / "scripts/post_update.sh").read_text()
        m = re.search(r"^defang\(\) \(\n.*?\n\)\n", text, re.S | re.M)
        self.assertTrue(m, "post_update.sh must define defang()")
        return m.group(0)

    def test_defang_turns_a_fatal_into_a_warn_and_keeps_the_exit_status(self):
        script = self._defang_fn() + textwrap.dedent('''
            defang bash -c 'echo "FATAL error: could not fetch the pin"; echo "error: git said no"; echo "[Errno 8] dns"; exit 3'
            echo "rc=$?"
        ''')
        rc, out = self.sb.run(["bash", "-c", script])
        self.assertFalse(stops(out), out)
        self.assertIn("WARN: could not fetch the pin", out)
        self.assertIn("rc=3", out)

    def test_defang_passes_a_success_through(self):
        script = self._defang_fn() + "\ndefang echo hello; echo rc=$?\n"
        rc, out = self.sb.run(["bash", "-c", script])
        self.assertIn("hello", out)
        self.assertIn("rc=0", out)

    def test_every_fatal_capable_script_is_required_or_defanged_in_post_update(self):
        text = (ROOT / "scripts/post_update.sh").read_text()
        bad = []
        for n, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for m in re.finditer(r"scripts/pinokio/([\w.-]+\.sh)", line):
                f = PIN / m.group(1)
                if "FATAL error:" not in f.read_text():
                    continue
                if not re.match(r"\s*require\b", line) and "defang" not in line:
                    bad.append((n, m.group(1)))
        self.assertEqual(bad, [])

    def test_the_optional_music_and_h3_steps_each_go_through_defang(self):
        text = (ROOT / "scripts/post_update.sh").read_text()
        for needle in ("h3_checkout.sh", "h3_deps.sh", "h3_build_q8.sh",
                       "music_checkout.sh", "music_venv.sh", "music_sync.sh", "music_fetch.py"):
            lines = [l for l in text.splitlines() if needle in l and not l.lstrip().startswith("#")
                     and not l.lstrip().startswith("echo")]
            self.assertTrue(lines, needle)
            for l in lines:
                self.assertIn("defang", l, f"{needle} runs bare in post_update.sh: {l.strip()}")

    def test_the_required_steps_are_not_defanged(self):
        text = (ROOT / "scripts/post_update.sh").read_text()
        for needle in ("ltx_venv.sh", "ltx_checkout.sh", "patch_ltx_codec.py"):
            line = next(l for l in text.splitlines() if needle in l and l.lstrip().startswith("require"))
            self.assertNotIn("defang", line)


# --------------------------------------------------------------------------
# The lanes themselves
# --------------------------------------------------------------------------
DISPATCHES_JS = r"""
const mod = require(process.argv[1]);
const out = [];
(function walk(n, trail) {
  if (Array.isArray(n)) return n.forEach((v, i) => walk(v, trail.concat(i)));
  if (!n || typeof n !== "object") return;
  if (n.method === "shell.run" && n.params) {
    const m = n.params.message;
    const list = (typeof m === "string") ? [m]
      : (n.params.chain ? [m.join(" && ")] : m);
    list.forEach((d) => out.push({d: String(d), path: n.params.path || "", env: n.params.env || {}}));
  }
  for (const k of Object.keys(n)) if (k !== "params" && k !== "method") walk(n[k], trail.concat(k));
})(mod.run, []);
console.log(JSON.stringify(out));
"""


def lane(name: str) -> list[dict]:
    r = subprocess.run(["node", "-e", DISPATCHES_JS, str(ROOT / name)], capture_output=True,
                       text=True, errors="replace", check=True)
    return json.loads(r.stdout)


@unittest.skipUnless(shutil.which("node"), "node not on PATH")
class LaneWiring(unittest.TestCase):
    def test_install_js_guards_the_two_inline_python_steps(self):
        ds = [d["d"] for d in lane("install.js")]
        patch = [d for d in ds if "patch_ltx_codec.py" in d]
        imp = [d for d in ds if "import ltx_core_mlx" in d]
        self.assertEqual(len(patch), 1)
        self.assertEqual(len(imp), 1)
        for d in patch + imp:
            self.assertTrue(d.startswith("bash scripts/pinokio/must.sh '"), d)
            self.assertFalse(stops(re.search(r"must\.sh '([^']*)'", d).group(1)))

    def test_every_install_line_is_under_350_chars(self):
        for name in ("install.js", "install_h3.js", "install_music.js", "update.js"):
            for d in lane(name):
                self.assertLess(len(d["d"]), 350, f"{name}: {d['d'][:80]}")

    def test_the_h3_steps_enter_the_checkout_through_h3_enter(self):
        for d in lane("install_h3.js"):
            if "h3_roots.sh" in d["d"] and "git clone" not in d["d"]:
                self.assertIn("h3_enter", d["d"])
            self.assertNotIn('cd "$H3_CHECKOUT"', d["d"])

    def test_optional_steps_never_name_a_stop_word(self):
        for name in ("install.js", "install_h3.js", "install_music.js", "update.js"):
            for d in lane(name):
                for m in re.finditer(r"\|\|\s*echo\s+(['\"])(.*?)\1", d["d"], re.S):
                    self.assertFalse(stops(m.group(2)), f"{name}: {m.group(2)}")

    def test_every_script_a_lane_calls_exists(self):
        for name in ("install.js", "install_h3.js", "install_music.js", "update.js"):
            for d in lane(name):
                for m in re.finditer(r"scripts/pinokio/([\w.-]+)", d["d"]):
                    self.assertTrue((PIN / m.group(1)).is_file(), f"{name} -> {m.group(1)}")


@unittest.skipUnless(shutil.which("node") and shutil.which("bash"), "node/bash not on PATH")
class H3InstallStopsAtTheFirstRefusal(Base):
    """install_h3.js's REAL dispatches, run one shell each as Pinokio 8.2 runs
    them, stopped by the kernel's own regex. Before 4.20 a refused preflight
    and a failed clone both marched on to the end."""

    def setUp(self):
        super().setUp()
        self.sb.install("scripts/pinokio/h3_preflight.sh", "scripts/pinokio/h3_roots.sh",
                        "scripts/pinokio/h3_checkout.sh", "scripts/pinokio/h3_venv.sh",
                        "scripts/pinokio/h3_deps.sh", "scripts/pinokio/h3_build_q8.sh",
                        "scripts/pinokio/h3_licence_gate.sh")
        self.calls = self.sb.base / "calls.log"
        self.dispatches = lane("install_h3.js")
        # 4.19.8: dispatch 0 is the licence gate. These cases are about the
        # steps after it, so the licence is accepted the way the panel does.
        sys.path.insert(0, str(ROOT))
        import model_licences
        model_licences.accept(self.sb.app / "state", "h3")

    def _emulate(self, env=None):
        """-> (index of the dispatch that stopped the run or None, [outputs])"""
        outs = []
        for i, d in enumerate(self.dispatches):
            cmd = d["d"].replace("{{cwd}}", str(self.sb.app))
            e = dict(env or {}, CALLS=str(self.calls))
            e.update({k: v.replace("{{cwd}}", str(self.sb.app)) for k, v in d["env"].items()})
            rc, out = self.sb.run(["bash", "-c", cmd], env=e)
            outs.append(out)
            if stops(out):
                return i, outs
        return None, outs

    def test_a_refused_preflight_ends_the_install_before_anything_runs(self):
        self.sb.stub("sysctl", "echo 17179869184\n")
        self.sb.stub("git", 'echo "git $*" >> "$CALLS"\nexit 0\n')
        stopped, outs = self._emulate()
        self.assertEqual(stopped, 1, outs)            # 0 = the licence gate, passed
        self.assertIn("h3_licence_gate.sh", self.dispatches[0]["d"])
        self.assertIn("FATAL error: Hailuo H3 needs", outs[1])
        self.assertFalse(self.calls.exists(), "something ran after the refusal")

    def test_an_unaccepted_licence_ends_a_new_install_before_anything_runs(self):
        for f in (self.sb.app / "state").glob("*"):
            f.unlink()
        self.sb.stub("sysctl", "echo 68719476736\n")
        self.sb.stub("git", 'echo "git $*" >> "$CALLS"\nexit 0\n')
        stopped, outs = self._emulate()
        self.assertEqual(stopped, 0, outs)
        self.assertTrue(outs[0].strip().startswith("FATAL error: "), outs[0])
        self.assertIn("MiniMax H3 licence", outs[0])
        self.assertFalse(self.calls.exists(), "something ran after the refusal")

    def test_a_failed_clone_ends_the_install_at_the_checkout_step(self):
        self.sb.stub("sysctl", "echo 68719476736\n")
        self.sb.stub("df", 'echo "Filesystem 1024-blocks Used Available Capacity Mounted on"\n'
                           'echo "fake 9 1 900000000 1% /"\n')
        self.sb.stub("git", 'echo "git $*" >> "$CALLS"\n'
                            'echo "fatal: unable to access https://x" >&2\nexit 128\n')
        stopped, outs = self._emulate()
        self.assertIsNotNone(stopped, "the install marched on past a failed clone")
        self.assertIn("h3_enter", self.dispatches[stopped]["d"])
        self.assertIn("FATAL error: the Hailuo H3 engine folder is missing", outs[stopped])


# --------------------------------------------------------------------------
# The gate itself: it must fail when a call has no way to stop the install
# --------------------------------------------------------------------------
@unittest.skipUnless(shutil.which("node"), "node not on PATH")
class GateCatchesAMissingFatalPath(unittest.TestCase):
    FILES = ("install.js", "install_h3.js", "install_music.js", "update.js", "install_qwen.js",
             "install_sharp.js", "download_q8.js", "download_upscaler.js", "start.js", "reset.js",
             "pinokio.js", "scripts/check_pinokio_scripts.js", "scripts/post_update.sh",
             "scripts/music/engine_pin.txt")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for rel in self.FILES:
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / rel, self.root / rel)
        shutil.copytree(PIN, self.root / "scripts/pinokio")

    def gate(self):
        r = subprocess.run(["node", "scripts/check_pinokio_scripts.js"], cwd=self.root,
                           capture_output=True, text=True, errors="replace")
        return r.returncode, r.stdout + r.stderr

    def edit(self, rel, old, new):
        f = self.root / rel
        text = f.read_text()
        self.assertIn(old, text, f"{rel}: {old[:40]}")
        f.write_text(text.replace(old, new, 1))

    def test_the_real_tree_passes(self):
        rc, out = self.gate()
        self.assertEqual(rc, 0, out)
        self.assertIn("FATAL coverage", out)

    def test_a_script_that_lost_its_fatal_line_fails_the_gate(self):
        for f in ("ltx_venv.sh", "h3_preflight.sh", "music_preflight.sh", "ltx_checkout.sh"):
            text = (self.root / "scripts/pinokio" / f).read_text().replace("FATAL error:", "FATAL:")
            (self.root / "scripts/pinokio" / f).write_text(text)
        rc, out = self.gate()
        self.assertNotEqual(rc, 0, out)
        for f in ("ltx_venv.sh", "h3_preflight.sh", "music_preflight.sh", "ltx_checkout.sh"):
            self.assertIn(f, out)

    def test_a_silent_exit_added_to_a_fatal_script_fails_the_gate(self):
        f = self.root / "scripts/pinokio/h3_venv.sh"
        f.write_text(f.read_text() + "\n[ -d .venv ] || exit 1\n")
        rc, out = self.gate()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("h3_venv.sh", out)

    def test_an_unwrapped_required_inline_step_fails_the_gate(self):
        self.edit("install.js",
                  "bash scripts/pinokio/must.sh 'The video codec patch did not apply - click Install again; if it repeats, report the lines above' ",
                  "")
        rc, out = self.gate()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("no way to stop the install", out)

    def test_a_new_script_call_with_no_fatal_path_fails_the_gate(self):
        (self.root / "scripts/pinokio/new_step.sh").write_text("#!/usr/bin/env bash\nfalse || exit 1\n")
        self.edit("install.js", 'message: "bash scripts/pinokio/ltx_engine_env.sh"',
                  'message: "bash scripts/pinokio/new_step.sh"')
        rc, out = self.gate()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("new_step.sh", out)

    def test_an_optional_echo_that_says_error_fails_the_gate(self):
        self.edit("install.js", "WARN: could not queue the extras", "error: could not queue the extras")
        rc, out = self.gate()
        self.assertNotEqual(rc, 0, out)
        self.assertIn('says "error:"', out)

    def test_a_bare_optional_script_in_post_update_fails_the_gate(self):
        self.edit("scripts/post_update.sh",
                  'defang bash "$ROOT/scripts/pinokio/h3_checkout.sh"',
                  'bash "$ROOT/scripts/pinokio/h3_checkout.sh"')
        rc, out = self.gate()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("post_update.sh", out)

    def test_a_dispatch_over_350_chars_fails_the_gate(self):
        self.edit("install.js", 'message: "bash scripts/pinokio/ltx_checkout.sh"',
                  'message: "bash scripts/pinokio/ltx_checkout.sh ' + "# x" * 120 + '"')
        rc, out = self.gate()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("DISPATCH", out)

    def test_an_optional_script_that_prints_fatal_fails_the_gate(self):
        f = self.root / "scripts/pinokio/mflux_pack.sh"
        f.write_text(f.read_text() + "\necho 'FATAL error: nope'\n")
        rc, out = self.gate()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("mflux_pack.sh", out)


if __name__ == "__main__":
    unittest.main()
