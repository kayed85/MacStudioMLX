"""NO TEST MAY TOUCH THE PANEL SOMEBODY IS USING.

2026-09-18: a full-suite run on this machine wrote
`analytics_query_key = "phx_secret_from_the_other_panel"` and
`civitai_api_key = "civ_from_the_other_panel"` into the LIVE
`state/panel_settings.json` — destroying the owner's real PostHog and CivitAI
keys — and `analytics_last_version = "9.9.9"` along with them. The test that
did it (`test_settings_merge.py`) sets `LTX_STATE_DIR` to a temp dir at import
time and is correct on its own: run alone it is airtight. But STATE_DIR is
resolved ONCE, when `mlx_ltx_panel` is first imported, so in a whole-suite run
any earlier test module that imported the panel had already fixed it to the
real `state/` — and every "temp dir" assignment after that was decoration.
The same trap explains `state/usage-log.jsonl.bak-fixtures` (an earlier
session's rescue) and the suite's order-dependent failures.

A conftest is imported before any test module, so this is the one place the
sandbox can be set up in time. Everything a test could write — state, outputs,
uploads — is pointed at a per-run temp tree unless the caller set it
deliberately. `test_state_sandbox.py` holds the line.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

#: One tree per pytest run; the OS reaps it.
SANDBOX = Path(tempfile.mkdtemp(prefix="phos-test-sandbox-"))

for _var, _sub in (("LTX_STATE_DIR", "state"),
                   ("LTX_OUTPUT_DIR", "mlx_outputs"),
                   ("LTX_UPLOADS_DIR", "panel_uploads")):
    if not (os.environ.get(_var) or "").strip():
        _dir = SANDBOX / _sub
        _dir.mkdir(parents=True, exist_ok=True)
        os.environ[_var] = str(_dir)

# A test run also has no business reporting to PostHog or phoning home for a
# version check; both were already set per-file, inconsistently.
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")
os.environ.setdefault("PHOSPHENE_DISABLE_VERSION_CHECK", "1")


# ---- HERMETIC BY DEFAULT (4.17.5) -------------------------------------------
# A full run used to leave an 810 MB Hugging Face download in the system temp
# dir EVERY time (149 phos-hf-* folders, 14 GB, on the maintainer's Mac on
# 2026-10-03, with / at 2-3 GB free): test_image_partial_download pointed
# HF_HOME at a fresh temp dir for the whole process, and a later image-engine
# test resolved the Qwen-Edit Lightning LoRA through hf_hub_download - a real
# network fetch into that dir, never removed. So, for every pytest run:
#   * every tempfile.mkdtemp / TemporaryDirectory (and every subprocess's
#     TMPDIR) lands inside SANDBOX, and SANDBOX is removed when the run ends;
#   * Hugging Face is offline (HF_HUB_OFFLINE=1) and hf_hub_download /
#     snapshot_download RAISE inside a test, unless the test is marked
#     @pytest.mark.hf_network on purpose - a test that downloads must say so.
# The release gate's unittest sweep does not load this file; scripts/
# release_gates.sh sets the same two environment rules for it.
import shutil  # noqa: E402

import pytest  # noqa: E402

_SANDBOX_TMP = SANDBOX / "tmp"
_SANDBOX_TMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(_SANDBOX_TMP)
os.environ["TMPDIR"] = str(_SANDBOX_TMP) + "/"
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


class HfDownloadBlocked(RuntimeError):
    """A test reached a real Hugging Face download."""


def _blocked(name: str):
    def _refuse(*args, **kwargs):
        what = kwargs.get("repo_id") or (args[0] if args else "?")
        # Product code often catches a failed fetch and carries on, which
        # would hide the attempt: PHOS_HF_GUARD_LOG=<file> lists every one.
        log = os.environ.get("PHOS_HF_GUARD_LOG")
        if log:
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(f"{os.environ.get('PYTEST_CURRENT_TEST', '?')} {name} {what}\n")
        raise HfDownloadBlocked(
            f"a test called huggingface_hub.{name}({what!r}) - a real download. "
            f"Mock it, or mark the test @pytest.mark.hf_network if it must.")
    return _refuse


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "hf_network: this test may really download from Hugging Face")
    config.addinivalue_line(
        "markers", "network: this test may open connections off this machine")


@pytest.fixture(autouse=True)
def _no_hf_downloads(request, monkeypatch):
    if request.node.get_closest_marker("hf_network"):
        yield
        return
    try:
        import huggingface_hub
        import huggingface_hub.file_download as _fd
        import huggingface_hub._snapshot_download as _sd
    except ImportError:
        yield
        return
    for mod, name in ((huggingface_hub, "hf_hub_download"), (_fd, "hf_hub_download"),
                      (huggingface_hub, "snapshot_download"), (_sd, "snapshot_download")):
        monkeypatch.setattr(mod, name, _blocked(name), raising=False)
    yield


# ...and no test reaches the internet at all unless marked @pytest.mark.network:
# a connect() to anything but loopback raises. Local test servers, git over
# file paths and unix sockets are untouched.
import socket as _socket  # noqa: E402

_REAL_CONNECT = _socket.socket.connect
_REAL_CONNECT_EX = _socket.socket.connect_ex


class NetworkBlocked(OSError):
    """A test tried to open a connection off this machine."""


def _is_local(address) -> bool:
    if not isinstance(address, tuple) or not address:
        return True                      # AF_UNIX path, etc.
    host = str(address[0])
    return host in ("localhost", "::1", "0.0.0.0") or host.startswith("127.")


def _guarded(real):
    def connect(self, address, *a, **k):
        if not _is_local(address):
            log = os.environ.get("PHOS_HF_GUARD_LOG")
            if log:
                import threading
                import traceback
                with open(log, "a", encoding="utf-8") as fh:
                    fh.write(f"{os.environ.get('PYTEST_CURRENT_TEST', '?')} "
                             f"connect {address[0]} "
                             f"thread={threading.current_thread().name}\n")
                    if os.environ.get("PHOS_HF_GUARD_STACK"):
                        fh.write("".join(traceback.format_stack(limit=30)))
            raise NetworkBlocked(
                f"a test tried to connect to {address[0]} - mock the network, or "
                f"mark the test @pytest.mark.network if it must")
        return real(self, address, *a, **k)
    return connect


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    if request.node.get_closest_marker("network"):
        yield
        return
    monkeypatch.setattr(_socket.socket, "connect", _guarded(_REAL_CONNECT))
    monkeypatch.setattr(_socket.socket, "connect_ex", _guarded(_REAL_CONNECT_EX))
    yield


def pytest_sessionfinish(session, exitstatus):
    # Everything this run wrote under SANDBOX (state, outputs, uploads, every
    # temp dir) goes when it ends - nothing is left in the system temp dir.
    shutil.rmtree(SANDBOX, ignore_errors=True)
