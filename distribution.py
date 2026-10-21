"""Where this panel runs, and which edition it is.

Phosphene runs in two places:

  * Pinokio (the default). Pinokio's launcher sets neither variable below, and
    every answer in this module then matches what the panel did before the
    module existed: the Pinokio wording, the git-based update pill, no badge.
  * The Phosphene Mac app. It starts the panel with
    PHOSPHENE_DISTRIBUTION=mac-app and PHOSPHENE_EDITION=free|pro. There is no
    Pinokio sidebar, no git checkout and no Stop/Start: the app updates the
    studio itself (Phosphene -> Check for Updates...), restarts it from its
    Studio menu, and re-runs its setup check on every launch.

Every reader goes through the functions here, never os.environ directly, so
"which wording does this install get" has one answer. The variables are read
on each call (cheap), so a test can flip them with monkeypatch.

Only the LABEL of the edition lives in this module. Nothing paid is in here.
"""
from __future__ import annotations

import os

ENV_DISTRIBUTION = "PHOSPHENE_DISTRIBUTION"
ENV_EDITION = "PHOSPHENE_EDITION"

PINOKIO = "pinokio"
MAC_APP = "mac-app"

EDITIONS = ("free", "pro")

#: The Mac app's own menu paths. The panel names these and nothing else in the
#: app (Phosphene.app App.swift: the app menu, the Studio menu, the Help menu).
MAC_UPDATE_MENU = "Phosphene → Check for Updates…"
MAC_RESTART_MENU = "Studio → Restart Engine"
MAC_REPORT_MENU = "Help → Report a Bug…"


def distribution(env=None) -> str:
    """'mac-app' when the Mac app started this panel, else 'pinokio'.

    Anything other than exactly 'mac-app' (unset, empty, a typo) is Pinokio:
    the default must never change because of a stray value."""
    raw = (env if env is not None else os.environ).get(ENV_DISTRIBUTION) or ""
    return MAC_APP if raw.strip().lower() == MAC_APP else PINOKIO


def is_mac_app(env=None) -> bool:
    return distribution(env) == MAC_APP


def edition(env=None) -> str:
    """'free' or 'pro' when the launcher said so, '' when it did not (Pinokio)."""
    raw = ((env if env is not None else os.environ).get(ENV_EDITION) or "").strip().lower()
    return raw if raw in EDITIONS else ""


def is_pro(env=None) -> bool:
    return edition(env) == "pro"


def say(pinokio: str, mac_app: str) -> str:
    """The Pinokio text, or its Mac-app variant inside the app."""
    return mac_app if is_mac_app() else pinokio


def info() -> dict:
    """What the page and /version report (BOOT.distribution, the pill)."""
    return {"distribution": distribution(), "edition": edition(),
            "app_updates": is_mac_app()}


# ---- Mac-app wording -------------------------------------------------------
# Each user-facing sentence keeps its Mac-app variant beside the Pinokio one,
# at the call site (say(pinokio, mac_app) in Python, distText() in the page):
# the two read together, and test_mac_app_distribution.py checks that no Mac
# variant names Pinokio or a sidebar. Written with Codex, fact-checked against
# the app (2026-10-21). Only the shared refusal lives here.

#: The studio never updates itself inside the app (no git): the app does.
MAC_APP_UPDATES = "To update Phosphene, use " + MAC_UPDATE_MENU
