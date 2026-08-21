"""M1 — gradient-flow smoke test for LoRA on LTX-2 (MLX).

Run with:
    ./scripts/run.sh python -m lora_lab.smoke_test

What this checks
----------------
1. Tier A — wrap a tiny QuantizedLinear with `LoRALinear`, prove `value_and_grad`
   emits non-zero gradients on the LoRA params and zero/None on the base.
2. Tier B — load one real LTX transformer block (Q4), wrap its attention
   projections using the same helpers `ltx-trainer-mlx` uses, freeze base,
   forward random hidden states through self-attention, prove gradients flow
   to LoRA params and not to base.

Tier A is the must-pass mechanism check. Tier B confirms the mechanism works
against a real quantized transformer block — the harder case.

If both pass, the trainer's LoRA setup is wired correctly for our environment
and we can move to M2 (preprocessor) without surprises.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import mlx.core as mx
import mlx.nn as nn
from mlx_lm.tuner.lora import LoRALinear

from ltx_trainer_mlx.trainer import _find_lora_targets, _set_module_by_path

# Optional: only load the transformer for Tier B if it's available.
MODEL_DIR = Path(
    os.environ.get(
        "LORA_LAB_MODEL_DIR",
        str(
            Path.home()
            / ".cache/huggingface/hub/models--dgrauet--ltx-2.3-mlx-q4"
            / "snapshots/53a6f5f39d9c074bc73e6a18ba391f40ddffaa68"
        ),
    )
)


# ---------------------------------------------------------------------------
# Tier A — tiny QuantizedLinear + LoRA
# ---------------------------------------------------------------------------


def _quantize_linear(layer: nn.Linear, bits: int = 4, group_size: int = 64) -> nn.QuantizedLinear:
    """Quantize an nn.Linear in place and return the equivalent nn.QuantizedLinear.

    Mirrors what `apply_quantization` does at load time, but for one layer.
    """
    return nn.QuantizedLinear.from_linear(layer, group_size=group_size, bits=bits)


def tier_a() -> bool:
    print("\n=== Tier A: tiny QuantizedLinear + LoRA ===")
    mx.random.seed(0)

    in_dim, out_dim, rank = 64, 64, 8
    base_linear = nn.Linear(in_dim, out_dim, bias=True)
    qlinear = _quantize_linear(base_linear, bits=4, group_size=64)

    lora = LoRALinear(input_dims=in_dim, output_dims=out_dim, r=rank, scale=1.0, bias=True)
    lora.linear = qlinear  # swap the inner Linear for the quantized one

    # Freeze everything, then unfreeze only LoRA params (this is what the trainer does).
    lora.freeze()
    lora.unfreeze(keys=["lora_a", "lora_b"], strict=False)

    trainable = dict(nn.utils.tree_flatten(lora.trainable_parameters()))
    print(f"  trainable params: {list(trainable.keys())}")
    assert "lora_a" in trainable and "lora_b" in trainable, "LoRA params not trainable"
    assert all("weight" not in k for k in trainable.keys()), "base weight should be frozen"

    x = mx.random.normal((4, in_dim))
    target = mx.random.normal((4, out_dim))

    def loss_fn(m, x, t):
        return mx.mean((m(x) - t) ** 2)

    loss_and_grad = nn.value_and_grad(lora, loss_fn)
    loss, grads = loss_and_grad(lora, x, target)
    mx.eval(loss, grads)

    flat_grads = dict(nn.utils.tree_flatten(grads))
    print(f"  loss: {float(loss.item()):.4f}")
    print(f"  grad keys: {list(flat_grads.keys())}")

    lora_a_grad = flat_grads.get("lora_a")
    lora_b_grad = flat_grads.get("lora_b")
    assert lora_a_grad is not None and lora_b_grad is not None, "missing LoRA grads"

    a_norm = float(mx.linalg.norm(lora_a_grad).item())
    b_norm = float(mx.linalg.norm(lora_b_grad).item())
    print(f"  ||grad lora_a|| = {a_norm:.6f}")
    print(f"  ||grad lora_b|| = {b_norm:.6f}")
    # lora_b is initialized to zero in standard LoRA, so its grad CAN be nonzero
    # after one step. lora_a's grad starts at zero (because B is zero) — that's
    # the standard LoRA initialization. We don't assert nonzero on `a` here.
    assert b_norm > 0.0, "lora_b grad is zero — backprop did not reach LoRA"

    base_grad = flat_grads.get("linear.weight")
    if base_grad is not None:
        base_norm = float(mx.linalg.norm(base_grad).item())
        print(f"  ||grad base.weight|| = {base_norm:.6f} (expected ~0)")
        assert base_norm < 1e-8, "frozen base received gradients"
    else:
        print("  base.weight: no grad entry (expected)")

    print("  Tier A PASS")
    return True


# ---------------------------------------------------------------------------
# Tier B — one real LTX transformer block (Q4)
# ---------------------------------------------------------------------------


def tier_b() -> bool:
    print("\n=== Tier B: one real LTX transformer block (Q4) ===")
    if not MODEL_DIR.exists():
        print(f"  model dir missing: {MODEL_DIR}")
        print("  skipping Tier B")
        return False

    t0 = time.time()
    from ltx_trainer_mlx.model_loader import load_transformer

    model = load_transformer(MODEL_DIR)
    print(f"  transformer loaded in {time.time() - t0:.1f}s")

    block = model.transformer_blocks[0]
    print(f"  block class: {type(block).__name__}")

    # Scope the LoRA wrap to JUST this block. We use the same helpers the
    # trainer uses, but rooted at the block (not the whole model).
    targets = _find_lora_targets(block, target_names=["to_q", "to_k", "to_v", "to_out"])
    print(f"  found {len(targets)} LoRA targets on block[0]:")
    for path, mod in targets[:8]:
        kind = "Quant" if isinstance(mod, nn.QuantizedLinear) else "Linear"
        print(f"    {path}  ({kind})")
    if len(targets) > 8:
        print(f"    ... and {len(targets) - 8} more")

    rank = 8
    for path, module in targets:
        if isinstance(module, nn.QuantizedLinear):
            in_dims = module.scales.shape[-1] * module.group_size
            out_dims = module.weight.shape[0]
            has_bias = hasattr(module, "bias") and module.bias is not None
        else:
            in_dims = module.weight.shape[-1]
            out_dims = module.weight.shape[0]
            has_bias = hasattr(module, "bias") and module.bias is not None
        lora_linear = LoRALinear(
            input_dims=in_dims,
            output_dims=out_dims,
            r=rank,
            scale=1.0,
            bias=has_bias,
        )
        lora_linear.linear = module
        _set_module_by_path(block, path, lora_linear)

    block.freeze()
    block.unfreeze(keys=["lora_a", "lora_b"], strict=False)

    trainable = dict(nn.utils.tree_flatten(block.trainable_parameters()))
    lora_keys = [k for k in trainable.keys() if "lora_" in k]
    base_keys = [k for k in trainable.keys() if "lora_" not in k]
    print(f"  trainable param count: {len(trainable)} ({len(lora_keys)} LoRA, {len(base_keys)} other)")
    assert len(lora_keys) > 0, "no LoRA params became trainable"
    assert len(base_keys) == 0, f"unexpected trainable base params: {base_keys[:3]}"

    # Drive a tiny forward through ONLY the video self-attention path of the
    # block. We don't need the full block call (which expects audio, text,
    # positions, etc.) — we just need any path through the LoRA-wrapped layers.
    attn = block.attn1
    bs, seq, dim = 1, 16, attn.to_q.linear.weight.shape[0]
    x = mx.random.normal((bs, seq, dim)).astype(mx.bfloat16)

    def loss_fn(_attn, x):
        q = _attn.to_q(x)
        k = _attn.to_k(x)
        v = _attn.to_v(x)
        out = _attn.to_out(q + k + v)  # synthetic: not real attention, just exercises all 4 LoRAs
        return mx.mean(out.astype(mx.float32) ** 2)

    loss_and_grad = nn.value_and_grad(attn, loss_fn)
    t0 = time.time()
    loss, grads = loss_and_grad(attn, x)
    mx.eval(loss, grads)
    print(f"  fwd+bwd on block[0] attn: {time.time() - t0:.2f}s, loss={float(loss.item()):.4f}")

    flat = dict(nn.utils.tree_flatten(grads))
    lora_b_grads = {k: v for k, v in flat.items() if k.endswith(".lora_b")}
    assert lora_b_grads, "no lora_b gradients emitted"

    any_nonzero = False
    for k, g in lora_b_grads.items():
        if g is None:
            continue
        norm = float(mx.linalg.norm(g).item())
        if norm > 0.0:
            any_nonzero = True
        print(f"    ||grad {k}|| = {norm:.6f}")
    assert any_nonzero, "all lora_b grads are zero"

    # Sample a base-weight grad — should not appear (frozen).
    base_grads = {k: v for k, v in flat.items() if k.endswith(".linear.weight") and v is not None}
    print(f"  frozen base weight grad entries: {len(base_grads)} (expected 0)")
    assert len(base_grads) == 0, f"frozen base received gradients: {list(base_grads)[:3]}"

    print("  Tier B PASS")
    return True


def main() -> int:
    print(f"mlx {mx.__version__ if hasattr(mx, '__version__') else 'n/a'}")
    print(f"model dir: {MODEL_DIR}")
    a_ok = tier_a()
    b_ok = tier_b()
    if a_ok and b_ok:
        print("\nM1 SMOKE TEST PASS")
        return 0
    print("\nM1 SMOKE TEST FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
