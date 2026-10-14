#!/usr/bin/env python3
"""H3-22: H3 failure messages must not borrow LTX-only advice or leak
absolute local paths.

Three defects in friendlyJobError() (queue.js), shared by the Now card and
every storyboard shot:

  1. A SIGKILL (jetsam OOM) hint said "switch Quality to Quick" on every
     engine — H3 has no Quick tier at all.
  2. "H3 render exited with code N" (the non-signal exit path) and "H3
     render timed out — no output for 20 minutes" matched none of the
     specific branches (no signal name, no "helper exited from"), so both
     fell to the generic `{friendly: 'Job failed.', hint: raw}` — the
     exit-code one WITH its raw `metrics_path`, an absolute path under the
     user's home directory (often their real name on macOS), shown
     verbatim on screen.
  3. storyboard.js's caller never passed an engine at all, so a failed H3
     shot on the Storyboard surface got the LTX-worded SIGKILL hint even
     when the Now-card version (fixed here too) would have known better.

Fix: friendlyJobError(raw, engine) branches SIGKILL advice on engine
('h3' -> "try Fast, a shorter length, or Draft"; else unchanged Quick
Quality advice); a new branch catches both H3-only messages with a
_redactLocalPaths() pass over the hint; the generic fallback also redacts.
Both call sites (queue.js's Now card, storyboard.js's per-shot failure)
now pass the job's engine.

Verified live: friendlyJobError() run against real backend message shapes
(booted panel) — H3 SIGKILL -> "...try Fast, a shorter length, or Draft
(all use less memory)." with no Quick-quality mention; LTX SIGKILL ->
unchanged "...switch Quality to Quick..."; H3 exit-code message with a
real /Users/... metrics path -> hint contains no /Users/ path; H3 timeout
-> a real friendly title instead of "Job failed."
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
QUEUE_JS = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
STORYBOARD_JS = (ROOT / "webapp" / "js" / "storyboard.js").read_text(encoding="utf-8")


def _function_body(src: str, signature: str) -> str:
    start = src.index(signature)
    depth = 0
    i = src.index("{", start)
    j = i
    while True:
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
        j += 1


class FriendlyErrorIsEngineAware(unittest.TestCase):
    def setUp(self):
        self.body = _function_body(QUEUE_JS, "function friendlyJobError(raw, engine) {")

    def test_sigkill_branches_on_h3(self):
        self.assertIn("const onH3 = engine === 'h3';", self.body)
        self.assertIn("try Fast, a shorter length, or Draft", self.body)
        self.assertIn("switch Quality to Quick", self.body)

    def test_h3_exit_and_timeout_get_their_own_branch(self):
        self.assertIn("/h3 render (timed out|exited with code)/i.test(raw)", self.body)

    def test_redaction_helper_exists_and_is_used(self):
        self.assertIn("function _redactLocalPaths(", QUEUE_JS)
        self.assertIn("_redactLocalPaths(raw)", self.body)

    def test_generic_fallback_also_redacts(self):
        self.assertIn("hint: _redactLocalPaths(raw) || raw", self.body)

    def test_redaction_strips_users_paths_and_metrics_clause(self):
        helper = _function_body(QUEUE_JS, "function _redactLocalPaths(")
        self.assertIn("Users", helper)
        self.assertIn("metrics at", helper)


class BothCallersPassTheEngine(unittest.TestCase):
    def test_now_card_passes_engine(self):
        self.assertIn(
            "friendlyJobError(last.error || 'unknown error',\n"
            "        last.params && last.params.engine);",
            QUEUE_JS)

    def test_storyboard_passes_engine(self):
        self.assertIn("friendlyJobError(s.error || '', s.engine);", STORYBOARD_JS)


if __name__ == "__main__":
    unittest.main()
