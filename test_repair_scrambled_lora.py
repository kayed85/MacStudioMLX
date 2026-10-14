#!/usr/bin/env python3
"""The scrambled-save repair (#62) must fix exactly the files it should.

`lora_lab.repair_scrambled_lora` reverses the safetensors-0.8.0 raw-buffer
write that scrambled every Train-tab adapter (see
test_train_checkpoint_layout.py). Unscrambling a CORRECT file would scramble
it, so the repair is gated on a content check. Pinned here, numpy only:

- a file written the way the trainer wrote it is called "scrambled" and comes
  back bit-exact to the trained factors;
- a correctly saved file is called "ok" and is never touched, by --in-place
  or otherwise;
- structureless noise is "undetermined", not guessed;
- --in-place keeps a backup and notes the repair in the sidecar.

4.19.0 additions (on top of @tanis2000's PR #89): the header metadata and
dtypes survive; an in-place repair keeps hard links (the character bundle);
the sidecar's adapter_strength verdict is re-measured; voice-branch adapters
are decided rather than "undetermined"; --auto repairs only adapters this app
trained and never reads anything else.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from lora_lab import repair_scrambled_lora as R  # noqa: E402
from safetensors.numpy import load_file, save_file  # noqa: E402

N_IN, N_OUT, RANK, MODULES = 512, 384, 8, 6


def _trained_like(seed: int) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """lora_a (in, r), lora_b (r, out) with the structure training leaves:
    every rank component scaled by the same per-input-channel profile."""
    rng = np.random.default_rng(seed)
    out = {}
    for m in range(MODULES):
        channel_scale = rng.lognormal(0.0, 1.0, size=(N_IN, 1))
        a = (rng.standard_normal((N_IN, RANK)) * channel_scale * 1e-2).astype(np.float32)
        b = (rng.standard_normal((RANK, N_OUT)) * 1e-3).astype(np.float32)
        out[f"diffusion_model.transformer_blocks.{m}.attn1.to_q"] = (a, b)
    return out


def _as_trainer_wrote(factors: dict) -> dict[str, np.ndarray]:
    """The safetensors-0.8.0 write: transposed shape, untransposed bytes."""
    w = {}
    for k, (a, b) in factors.items():
        w[k + ".lora_A.weight"] = np.ascontiguousarray(a).reshape(RANK, N_IN)
        w[k + ".lora_B.weight"] = np.ascontiguousarray(b).reshape(N_OUT, RANK)
    return w


def _as_correct(factors: dict) -> dict[str, np.ndarray]:
    w = {}
    for k, (a, b) in factors.items():
        w[k + ".lora_A.weight"] = np.ascontiguousarray(a.T)
        w[k + ".lora_B.weight"] = np.ascontiguousarray(b.T)
    return w


class Detection(unittest.TestCase):
    @staticmethod
    def _s(w: dict) -> tuple[float, float, float]:
        as_is, unscr, _n, floor = R.scramble_scores(w)
        return as_is, unscr, floor

    def test_a_trainer_written_file_is_scrambled_and_repairs_exactly(self) -> None:
        f = _trained_like(1)
        bad = _as_trainer_wrote(f)
        self.assertEqual(R.classify(*self._s(bad)), "scrambled")
        fixed = R.unscramble(bad)
        for k, v in _as_correct(f).items():
            np.testing.assert_array_equal(fixed[k], v)

    def test_a_correct_file_is_ok(self) -> None:
        self.assertEqual(R.classify(*self._s(_as_correct(_trained_like(2)))), "ok")

    def test_structureless_noise_is_not_guessed(self) -> None:
        rng = np.random.default_rng(3)
        w = {f"m{i}.lora_A.weight": rng.standard_normal((RANK, N_IN)).astype(np.float32) for i in range(MODULES)}
        self.assertEqual(R.classify(*self._s(w)), "undetermined")


class InPlace(unittest.TestCase):
    def _write(self, d: Path, weights: dict) -> Path:
        p = d / "char_v2.safetensors"
        save_file(weights, str(p))
        p.with_suffix(".safetensors.json").write_text(json.dumps({"trigger": "abctrn"}))
        return p

    def test_repairs_with_backup_and_a_sidecar_note(self) -> None:
        f = _trained_like(4)
        with tempfile.TemporaryDirectory() as d:
            p = self._write(Path(d), _as_trainer_wrote(f))
            self.assertEqual(R.main([str(p), "--in-place"]), 0)
            self.assertTrue((Path(d) / "char_v2.safetensors.scrambled.bak").exists())
            got = load_file(str(p))
            for k, v in _as_correct(f).items():
                np.testing.assert_array_equal(got[k], v)
            side = json.loads(p.with_suffix(".safetensors.json").read_text())
            self.assertIn("repaired_scrambled_save", side)
            self.assertEqual(side["trigger"], "abctrn")
            # Second pass: already correct, must not touch it again.
            self.assertEqual(R.main([str(p), "--in-place"]), 0)
            np.testing.assert_array_equal(load_file(str(p))[next(iter(got))], got[next(iter(got))])

    def test_a_correct_file_is_never_modified(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = self._write(Path(d), _as_correct(_trained_like(5)))
            before = p.read_bytes()
            self.assertEqual(R.main([str(p), "--in-place"]), 0)
            self.assertEqual(p.read_bytes(), before)
            self.assertFalse((Path(d) / "char_v2.safetensors.scrambled.bak").exists())


def _voice_like(seed: int, n_in: int = 2048, rank: int = 16, modules: int = 96,
                weak: float = 0.05) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Audio-branch factors with the FAINT channel profile real voice adapters
    carry: 0.05 reads ~0.004 as saved; the two real voice adapters measured
    here read 0.0042 and 0.0070, against ~0.02-0.04 for a face)."""
    rng = np.random.default_rng(seed)
    out = {}
    for m in range(modules):
        channel_scale = rng.lognormal(0.0, weak, size=(n_in, 1))
        a = (rng.standard_normal((n_in, rank)) * channel_scale * 1e-2).astype(np.float32)
        b = (rng.standard_normal((rank, n_in)) * 1e-3).astype(np.float32)
        out[f"diffusion_model.transformer_blocks.{m}.audio_attn1.to_q"] = (a, b)
    return out


def _wrote(factors: dict) -> dict[str, np.ndarray]:
    w = {}
    for k, (a, b) in factors.items():
        w[k + ".lora_A.weight"] = np.ascontiguousarray(a).reshape(a.shape[1], a.shape[0])
        w[k + ".lora_B.weight"] = np.ascontiguousarray(b).reshape(b.shape[1], b.shape[0])
    return w


class Additions4190(unittest.TestCase):
    def test_voice_adapters_are_decided_both_ways(self) -> None:
        f = _voice_like(7)
        as_is, unscr, _n, floor = R.scramble_scores(_as_correct(f))
        self.assertLess(as_is, R.MIN_SCORE)          # what made them "undetermined"
        self.assertEqual(R.classify(as_is, unscr, floor), "ok")
        as_is, unscr, _n, floor = R.scramble_scores(_wrote(f))
        self.assertEqual(R.classify(as_is, unscr, floor), "scrambled")

    def test_metadata_dtype_and_hard_links_survive_an_in_place_repair(self) -> None:
        f = _trained_like(8)
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            p = d / "loras" / "abctrn_v2.safetensors"
            p.parent.mkdir()
            bad = {k: v.astype(np.float16) for k, v in _as_trainer_wrote(f).items()}
            save_file(bad, str(p), metadata={"ss_trigger": "abctrn"})
            twin = d / "abctrn.video.safetensors"
            twin.hardlink_to(p)
            self.assertEqual(R.main([str(p), "--in-place"]), 0)
            dtypes, meta = R.read_header(p)
            self.assertEqual(meta, {"ss_trigger": "abctrn"})
            self.assertEqual(set(dtypes.values()), {"F16"})
            self.assertEqual(twin.read_bytes(), p.read_bytes())   # same inode, both names fixed
            got = load_file(str(p))
            for k, v in _as_correct(f).items():
                np.testing.assert_array_equal(got[k], v.astype(np.float16))

    def test_the_weak_verdict_is_re_measured_after_repair(self) -> None:
        f = _trained_like(9)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "abctrn_v2.safetensors"
            save_file(_as_trainer_wrote(f), str(p))
            stale = {"verdict": "weak", "delta_rms_median": 1e-9}
            p.with_suffix(".json").write_text(json.dumps(
                {"kind": "train_character", "adapter_strength": stale}))
            self.assertEqual(R.main([str(p), "--in-place"]), 0)
            side = json.loads(p.with_suffix(".json").read_text())
            self.assertEqual(side["adapter_strength_as_saved"], stale)
            self.assertNotEqual(side["adapter_strength"], stale)
            self.assertIn("repaired_scrambled_save", side)

    def test_auto_repairs_what_this_app_trained_and_nothing_else(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            models = Path(d)
            loras = models / "loras"
            loras.mkdir()
            trained = loras / "abctrn_v2.safetensors"
            save_file(_as_trainer_wrote(_trained_like(10)), str(trained))
            trained.with_suffix(".safetensors.json").write_text(json.dumps({"lora_lab_version": "iter6"}))
            voice = loras / "abctrn.audio.safetensors"
            save_file(_wrote(_voice_like(11)), str(voice))
            # A downloaded LoRA that LOOKS scrambled: --auto must not even read it.
            foreign = loras / "civitai_style.safetensors"
            save_file(_as_trainer_wrote(_trained_like(12)), str(foreign))
            foreign_before = foreign.read_bytes()
            bundle = models / "characters" / "abctrn"
            bundle.mkdir(parents=True)
            (bundle / "abctrn.video.safetensors").hardlink_to(trained)
            self.assertEqual(R.main(["--auto", str(models)]), 0)
            self.assertTrue((loras / "abctrn_v2.safetensors.scrambled.bak").exists())
            self.assertTrue((loras / "abctrn.audio.safetensors.scrambled.bak").exists())
            self.assertFalse((bundle / "abctrn.video.safetensors.scrambled.bak").exists())  # one inode, one repair
            self.assertEqual(foreign.read_bytes(), foreign_before)
            self.assertFalse((loras / "civitai_style.safetensors.scrambled.bak").exists())
            # A second Update finds nothing to do.
            self.assertEqual(R.main(["--auto", str(models)]), 0)
            self.assertEqual(len(list(models.rglob("*.scrambled.bak"))), 2)


    def test_auto_never_trusts_a_name_or_follows_a_link(self) -> None:
        """Codex 4.19.0: a downloaded voice named like ours, and a symlink to a
        file outside the library, are never read or rewritten by --auto."""
        with tempfile.TemporaryDirectory() as d:
            models = Path(d) / "models"
            loras = models / "loras"
            loras.mkdir(parents=True)
            stray = loras / "someone.audio.safetensors"       # no trained face beside it
            save_file(_wrote(_voice_like(13)), str(stray))
            outside = Path(d) / "elsewhere.safetensors"
            save_file(_as_trainer_wrote(_trained_like(14)), str(outside))
            link = loras / "linked_v2.safetensors"
            link.symlink_to(outside)
            (loras / "linked_v2.safetensors.json").write_text(json.dumps({"lora_lab_version": "iter6"}))
            before = (stray.read_bytes(), outside.read_bytes())
            self.assertEqual(R.auto_candidates(models), [])
            self.assertEqual(R.main(["--auto", str(models)]), 0)
            self.assertEqual((stray.read_bytes(), outside.read_bytes()), before)
            self.assertEqual(list(Path(d).rglob("*.scrambled.bak")), [])


if __name__ == "__main__":
    unittest.main()
