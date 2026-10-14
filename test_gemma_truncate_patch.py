#!/usr/bin/env python3
"""VC-14 [P2] [BUG] The Gemma watchdog fallback silently dropped the
*beginning* of long prompts.

Where: `ltx_core_mlx/text_encoders/gemma/encoders/base_encoder.py`'s
`tokenize()` left-pads to `max_length`; on an over-length prompt it kept the
LAST `max_length` tokens ("keep last tokens (left-pad = truncate from
left)"). The fallback path (`GEMMA_FALLBACK_MAX_LENGTH = 256`, armed on
chips whose Metal watchdog kills the full 1024-token encode — typically
low-end Macs) meant any prompt over 256 tokens dropped its subject, i.e.
whatever the user wrote FIRST.

Coordinator ruling, 2026-09-29: "truncation keeps the subject: keep the
head, cut from the tail."

This is vendored upstream code (`ltx-2-mlx`, pinned + patched per
`docs`/CLAUDE.md §5 and §20), so the fix is a new idempotent patch in
`patch_ltx_codec.py` — not a hand-edit of the installed package, which
would be silently lost on the next reinstall/update. Gated here:

  1  the patch strings are self-consistent (OLD is the exact live upstream
     text today, NEW keeps the subject) and the grep rule from CLAUDE.md's
     "codec rule, stated correctly" holds for this patch too: exactly one
     unpatched hit in the vendored SOURCE tree, patched copy in site-packages
  2  apply_patch() actually flips the installed file's slice direction
  3  the behavioural claim itself: slicing `tokens[:max_length]` keeps the
     first N items (the subject) where `tokens[-max_length:]` kept the last
     N (arbitrary tail) — proven directly, no tokenizer/model needed
  4  the patch is wired into main() as OPTIONAL (a drift note, not a hard
     install failure) — unlike the codec patch, losing this fix only
     degrades long-prompt quality on a rare fallback path, it never breaks
     a render
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import patch_ltx_codec as PC  # noqa: E402

SRC = (ROOT / "patch_ltx_codec.py").read_text(encoding="utf-8")
VENDOR_SRC = (ROOT / "ltx-2-mlx" / "packages" / "ltx-core-mlx" / "src" /
              "ltx_core_mlx" / "text_encoders" / "gemma" / "encoders" /
              "base_encoder.py")


class TestPatchStringsAreConsistent(unittest.TestCase):
    def test_old_matches_the_live_vendored_source_verbatim(self):
        vendor_text = VENDOR_SRC.read_text(encoding="utf-8")
        self.assertIn(PC.PATCH_GEMMA_TRUNCATE_OLD, vendor_text)

    def test_new_keeps_the_subject_not_the_tail(self):
        self.assertIn("tokens[:max_length]", PC.PATCH_GEMMA_TRUNCATE_NEW)
        self.assertNotIn("tokens[-max_length:]", PC.PATCH_GEMMA_TRUNCATE_NEW)
        self.assertIn("VC-14", PC.PATCH_GEMMA_TRUNCATE_NEW)

    def test_grep_rule_one_unpatched_hit_in_vendored_source(self):
        # Mirrors CLAUDE.md's "codec rule, stated correctly" for this patch:
        # the git-tracked SOURCE tree stays the unpatched upstream text —
        # only the installed site-packages copy carries the fix. A second
        # hit in the source tree would mean the patch was applied to the
        # wrong copy (the editable-install failure class the codec rule
        # already guards against).
        import subprocess
        out = subprocess.run(
            ["grep", "-rnF", "--include=*.py", "tokens[-max_length:]",
             str(ROOT / "ltx-2-mlx" / "packages" / "ltx-core-mlx")],
            capture_output=True, text=True,
        ).stdout
        self.assertEqual(len(out.strip().splitlines()), 1, out)


class TestApplyPatchFlipsSliceDirection(unittest.TestCase):
    def test_apply_patch_on_a_fresh_copy_of_the_unpatched_file(self):
        tmp = Path(tempfile.mkdtemp(prefix="gemma-patch-")) / "base_encoder.py"
        tmp.write_text(VENDOR_SRC.read_text(encoding="utf-8"), encoding="utf-8")
        outcome = PC.apply_patch(
            tmp, PC.PATCH_GEMMA_TRUNCATE_OLD, PC.PATCH_GEMMA_TRUNCATE_NEW,
            marker="VC-14", label="gemma-truncate (test)",
        )
        self.assertEqual(outcome, PC.OUTCOME_APPLIED)
        patched = tmp.read_text(encoding="utf-8")
        self.assertIn("tokens[:max_length]", patched)
        self.assertNotIn("tokens[-max_length:]", patched)

    def test_idempotent_second_run_is_a_no_op(self):
        tmp = Path(tempfile.mkdtemp(prefix="gemma-patch-")) / "base_encoder.py"
        tmp.write_text(VENDOR_SRC.read_text(encoding="utf-8"), encoding="utf-8")
        PC.apply_patch(tmp, PC.PATCH_GEMMA_TRUNCATE_OLD, PC.PATCH_GEMMA_TRUNCATE_NEW,
                       marker="VC-14", label="gemma-truncate (test)")
        outcome2 = PC.apply_patch(tmp, PC.PATCH_GEMMA_TRUNCATE_OLD, PC.PATCH_GEMMA_TRUNCATE_NEW,
                                  marker="VC-14", label="gemma-truncate (test)")
        self.assertEqual(outcome2, PC.OUTCOME_ALREADY)

    def test_a_missing_target_reports_missing_not_a_crash(self):
        outcome = PC.apply_patch(
            Path("/tmp/does-not-exist-gemma-encoder.py"),
            PC.PATCH_GEMMA_TRUNCATE_OLD, PC.PATCH_GEMMA_TRUNCATE_NEW,
            marker="VC-14", label="gemma-truncate (test)",
        )
        self.assertEqual(outcome, PC.OUTCOME_MISSING)


class TestTruncationBehaviourItself(unittest.TestCase):
    """The actual claim: which end of the list survives. No tokenizer
    needed — the bug and the fix are both pure slicing."""

    def test_old_behaviour_drops_the_subject(self):
        # A "prompt" where token 0 is the subject and the rest is filler.
        tokens = ["SUBJECT"] + [f"filler{i}" for i in range(300)]
        max_length = 256
        old_kept = tokens[-max_length:]
        self.assertNotIn("SUBJECT", old_kept)

    def test_new_behaviour_keeps_the_subject(self):
        tokens = ["SUBJECT"] + [f"filler{i}" for i in range(300)]
        max_length = 256
        new_kept = tokens[:max_length]
        self.assertEqual(new_kept[0], "SUBJECT")
        self.assertEqual(len(new_kept), max_length)

    def test_short_prompts_are_unaffected_either_way(self):
        tokens = ["SUBJECT", "a", "b", "c"]
        max_length = 256
        self.assertEqual(tokens[-max_length:], tokens[:max_length])


class TestPatchIsOptionalNotFatal(unittest.TestCase):
    def test_main_calls_gemma_patch_and_treats_drift_as_a_note(self):
        fn = SRC[SRC.index("def main() -> int:"):]
        self.assertIn("PATCH_GEMMA_TRUNCATE_OLD", fn)
        self.assertIn("PATCH_GEMMA_TRUNCATE_NEW", fn)
        # unlike the codec patch, a drift/missing outcome here must NOT
        # return a non-zero exit — assert the gemma block has no early
        # `return` between its apply_patch call and the end of main()
        i = fn.index("gemma_target = _find(")
        tail = fn[i:]
        self.assertNotIn("return 2", tail)
        self.assertNotIn("return 3", tail)
        self.assertNotIn("return 4", tail)
        self.assertIn("return 0", tail)


if __name__ == "__main__":
    unittest.main()
