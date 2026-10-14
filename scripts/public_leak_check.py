#!/usr/bin/env python3
"""Gate: the tree we are about to publish carries none of the internal-only
material that has leaked into public `main` before.

WHY THIS FILE IS IN THE REPO
=============================
`docs/STATE.md` has been shipping on public `main` since the very first
commit that added it — beta and public were tree-identical the day this
script was written, which is exactly how a large internal handoff doc ended
up public for months without anyone noticing. Every prior gate in this repo
is static-analysis or render-level; nothing ever looked at *what the public
tree actually contains*. This does.

Two independent checks, both must pass:

1. PATH check — nothing on `scripts/public_exclude.txt` may exist in the
   tree. That file is the single source of truth for "must never ship";
   the promote recipe's `git rm --cached` step (docs/RELEASE_CHECKLIST.md,
   "Promoting — a curated SNAPSHOT", step 1) reads the same file, so the
   removal list and the verification list cannot drift apart.

2. CONTENT check — none of a private pattern list (kept OUT of the public
   tree — see PATTERN_FILE_PATH below) may appear ANYWHERE in the tree,
   exclude list or not. A file can leak the same information under a
   different path than the ones we happened to find. The pattern list
   itself necessarily spells out the strings it detects, so it can never
   ship publicly without republishing exactly what it exists to catch —
   which is why it lives only in the private tree this script is run from,
   and this script's content check REFUSES to run without it (fails
   closed: see `load_patterns()`). A public checkout of this script can
   still do the PATH check; it cannot do the CONTENT check, and says so
   loudly rather than reporting a false PASS.

Deliberately NOT in the content check: a couple of path fragments that are
already public, in many files, by design (they point at sibling
private-notes folders with no personal information in the path itself) —
gating on them here would fail every release on content that was already
reviewed and shipped safely. See the private pattern list's own comments
for specifics; if a future release wants one of those gated too, add a
per-hit allowlist rather than a blanket ban.

Usage:
    python3 scripts/public_leak_check.py <tree-ish>

<tree-ish> is anything `git ls-tree` / `git grep` accept: a branch, a tag,
a commit SHA — including an unreferenced `commit-tree` SHA that exists only
as a loose object (the exact case this exists for: verifying a promote
snapshot BEFORE it is pushed).

Exit 0 = PASS (clean). Exit 1 = FAIL (leak found, printed below). Exit 2 =
usage / environment error, INCLUDING the pattern list being unavailable —
neither counts as a leak verdict, but neither counts as a PASS either.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXCLUDE_FILE = REPO_ROOT / "scripts" / "public_exclude.txt"

# Repo-relative path to the private pattern list. Never present in a public
# snapshot (it's on scripts/public_exclude.txt); the check below re-verifies
# that explicitly, on top of the generic exclude-list check, because this is
# the one file whose presence would defeat the whole point of this script.
PATTERN_FILE_PATH = "scripts/public_leak_patterns.txt"
PATTERN_FILE = REPO_ROOT / PATTERN_FILE_PATH


def _git(*args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True
    )
    if out.returncode not in (0, 1):
        # git grep uses exit 1 for "no matches" — that's not an error here.
        sys.stderr.write(out.stderr)
        raise SystemExit(2)
    return out.stdout


def load_exclude_list() -> list[str]:
    if not EXCLUDE_FILE.is_file():
        sys.stderr.write(f"FAIL setup: {EXCLUDE_FILE} does not exist\n")
        raise SystemExit(2)
    paths = []
    for line in EXCLUDE_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        paths.append(line)
    if not paths:
        sys.stderr.write(f"FAIL setup: {EXCLUDE_FILE} has no active entries\n")
        raise SystemExit(2)
    return paths


def load_patterns() -> list[tuple[str, str, bool]]:
    """Load (label, regex, case_insensitive) from the private pattern list.

    Fails closed: a missing pattern file is a setup error, never a silent
    "nothing to check." This is what makes it safe for this script to ship
    publicly while the pattern list itself does not — a public checkout
    that tries to run the content check gets a loud, unambiguous refusal
    instead of a false PASS.
    """
    if not PATTERN_FILE.is_file():
        sys.stderr.write(
            f"FAIL setup: {PATTERN_FILE} not found — pattern list not found, "
            "run from the private tree\n"
        )
        raise SystemExit(2)
    patterns: list[tuple[str, str, bool]] = []
    for lineno, line in enumerate(PATTERN_FILE.read_text().splitlines(), 1):
        line = line.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            sys.stderr.write(
                f"FAIL setup: {PATTERN_FILE}:{lineno} is not "
                "'<0|1>\\t<label>\\t<regex>'\n"
            )
            raise SystemExit(2)
        ci_flag, label, pattern = parts
        patterns.append((label, pattern, ci_flag == "1"))
    if not patterns:
        sys.stderr.write(f"FAIL setup: {PATTERN_FILE} has no active entries\n")
        raise SystemExit(2)
    return patterns


def check_excluded_paths(tree_ish: str, excluded: list[str]) -> list[str]:
    listing = _git("ls-tree", "-r", "--name-only", tree_ish)
    present = set(listing.splitlines())
    return [p for p in excluded if p in present]


def check_pattern_file_absent(tree_ish: str) -> bool:
    """Dedicated check, on top of the generic exclude-list pass: the private
    pattern list must not be in the tree we're verifying, full stop."""
    listing = _git("ls-tree", "-r", "--name-only", tree_ish)
    return PATTERN_FILE_PATH in set(listing.splitlines())


def check_banned_content(tree_ish: str, patterns: list[tuple[str, str, bool]]) -> list[str]:
    hits: list[str] = []
    # The pattern list necessarily spells out every string it detects, so
    # scanning it against itself is a guaranteed, meaningless self-match —
    # exclude only this one path (never a whole directory) so a check run
    # against a tree that still carries it (e.g. beta HEAD, for local
    # testing) isn't spammed with hits on the pattern file's own lines.
    # This does NOT weaken the security property: PATTERN_FILE_PATH's
    # presence is separately hard-checked by check_pattern_file_absent().
    args_pathspec = ["--", ".", f":!{PATTERN_FILE_PATH}"]
    for label, pattern, ci in patterns:
        args = ["grep", "-nI"]
        if ci:
            args.append("-i")
        args += ["-P", "-e", pattern, tree_ish] + args_pathspec
        out = _git(*args)
        for line in out.splitlines():
            hits.append(f"[{label}] {line}")
    return hits


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write(f"usage: {argv[0]} <tree-ish>\n")
        return 2
    tree_ish = argv[1]

    # Confirm the tree-ish resolves before running two checks against it —
    # a typo'd SHA would otherwise silently produce two clean-looking
    # "no output" passes.
    resolved = subprocess.run(
        ["git", "rev-parse", "--verify", f"{tree_ish}^{{tree}}"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if resolved.returncode != 0:
        sys.stderr.write(f"FAIL setup: {tree_ish!r} does not resolve to a tree\n")
        sys.stderr.write(resolved.stderr)
        return 2

    excluded = load_exclude_list()
    patterns = load_patterns()  # exits 2 if the private list is unavailable

    leaked_paths = check_excluded_paths(tree_ish, excluded)
    pattern_file_leaked = check_pattern_file_absent(tree_ish)
    content_hits = check_banned_content(tree_ish, patterns)

    ok = True
    if leaked_paths:
        ok = False
        print(f"FAIL excluded path present in {tree_ish}:")
        for p in leaked_paths:
            print(f"  {p}")
    if pattern_file_leaked and PATTERN_FILE_PATH not in leaked_paths:
        ok = False
        print(f"FAIL private pattern list present in {tree_ish}: {PATTERN_FILE_PATH}")
    if content_hits:
        ok = False
        print(f"FAIL banned pattern present in {tree_ish}:")
        for h in content_hits:
            print(f"  {h}")

    if ok:
        print(f"PASS: {tree_ish} carries none of {len(excluded)} excluded "
              f"path(s), does not carry the private pattern list, and none "
              f"of {len(patterns)} banned patterns.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
