#!/usr/bin/env python3
"""Phosphene wrapper that invokes mflux flux2 inside a TeaCache context.

When MFLUX_TC_FLUX=1 (set by image_engine.py for fam='flux2'), Phosphene
launches this script instead of the bare `mflux-generate-flux2` CLI.
The wrapper imports mlx_teacache and inlines a near-copy of mflux's
``flux2_generate.main`` that calls ``apply_teacache(model, ...)`` between
``Flux2Klein(...)`` construction and the ``model.generate_image(...)``
loop. ``apply_teacache`` itself wraps ``model.generate_image`` (see
mlx_teacache.api.apply_teacache step B: wrap_generate_image) so the
TeaCache step-decode logic runs for the entire per-seed loop without any
further plumbing on our side.

Expected speedup at SSIM > 0.99 per the library's published numbers
(github.com/IonDen/mlx-teacache v0.4.1):
  - flux2-klein-4b 4-step distilled: ~1.25x (mostly mx.compile avoid;
    the polynomial gate does not engage at default threshold on a 4-step
    schedule, but the wrap still avoids one mx.compile path per run)
  - flux2-klein-base-4b 25-step:     ~1.41x (3/25 cached, polynomial gate
    active because the base model is non-distilled at guidance=1.0)
  - flux2-klein-base-4b w/ CFG > 1.0: NO-op (TeaCache 0.4.1 falls back
    to vanilla mflux pending v0.5; wrapper still safe, just no speedup)

Env vars:
  MFLUX_TC_FLUX_THRESH (default 0.20) — library-recommended sweet spot.
  MFLUX_TC_FLUX=0/false/no            — disable wrap, fall through to
                                        bare mflux-generate-flux2.

Fallback behaviour: if ANY import, wrap, or runtime step fails, we shell
out to the original ``mflux-generate-flux2`` CLI with the same argv. The
user gets a working render every time; the only loss is the TeaCache
speedup and a one-line stderr diagnostic explaining why.

Compatibility: mflux==0.17.5 (pinned by install_qwen.js / update.js),
mlx-teacache==0.4.1 (pinned alongside). The inline main() mirrors mflux's
``mflux/models/flux2/cli/flux2_generate.py`` — re-validate after any
mflux bump that touches Flux2Klein construction or generate_image kwargs.
"""
import os
import sys


THRESH = float(os.environ.get("MFLUX_TC_FLUX_THRESH", "0.20"))


def _fallback(reason: str) -> int:
    """Shell out to the unwrapped CLI. Returns the child's exit code so
    sys.exit(_fallback(...)) propagates it to the caller (image_engine).

    Prefer the venv-local binary (sibling of sys.executable) — the
    wrapper is launched from image_engine.py via the venv python so the
    sibling mflux-generate-flux2 is guaranteed installed alongside it.
    Falls through to PATH lookup if that file isn't there (covers the
    rare case where the wrapper is invoked manually outside the venv).
    """
    import shutil
    import subprocess
    venv_bin = os.path.dirname(sys.executable)
    candidate = os.path.join(venv_bin, "mflux-generate-flux2")
    if not (os.path.isfile(candidate) and os.access(candidate, os.X_OK)):
        candidate = shutil.which("mflux-generate-flux2") or "mflux-generate-flux2"
    print(f"[mflux-teacache] falling back to {candidate} ({reason})",
          file=sys.stderr, flush=True)
    return subprocess.call([candidate, *sys.argv[1:]])


def _wrapped_main() -> int:
    """Inline near-copy of ``mflux.models.flux2.cli.flux2_generate.main``
    with a single ``apply_teacache(model, rel_l1_thresh=THRESH)`` call
    inserted between model construction and the per-seed generate loop.

    Mirroring upstream rather than calling it lets us avoid monkey-patch
    races on ``Flux2Klein.generate_image`` — ``apply_teacache`` itself
    wraps ``generate_image`` via ``wrap_generate_image`` (see
    mlx_teacache.api line ~210), and a class-level monkey-patch from us
    would either no-op or fight that wrap. The inline copy is ~50 LoC
    and stable across mflux 0.17.x (only Klein bumps would touch it).
    """
    # All upstream imports are lazy so a fast `--help` fallback doesn't
    # pay the model-loading cost.
    from mflux.callbacks.callback_manager import CallbackManager
    from mflux.cli.parser.parsers import CommandLineParser
    from mflux.models.common.config import ModelConfig
    from mflux.models.flux2.latent_creator.flux2_latent_creator import Flux2LatentCreator
    from mflux.models.flux2.variants import Flux2Klein
    from mflux.utils.dimension_resolver import DimensionResolver
    from mflux.utils.exceptions import PromptFileReadError, StopImageGenerationException
    from mflux.utils.image_util import ImageUtil
    from mflux.utils.prompt_util import PromptUtil
    from mlx_teacache import apply_teacache
    from mlx_teacache.errors import (
        AlreadyPatchedError,
        IncompatibleModelError,
        TeaCacheError,
    )

    # 0. Parse command line arguments — identical shape to upstream main().
    parser = CommandLineParser(description="Generate an image using Flux2 Klein.")
    parser.add_general_arguments()
    parser.add_model_arguments(require_model_arg=False)
    parser.add_lora_arguments()
    parser.add_image_generator_arguments(supports_metadata_config=True, supports_dimension_scale_factor=True)
    parser.add_image_to_image_arguments(required=False)
    parser.add_output_arguments()
    args = parser.parse_args()

    if getattr(args, "negative_prompt", ""):
        parser.error("--negative-prompt is not supported for FLUX.2. Focus on describing what you want.")

    model_name = args.model or "flux2-klein-4b"
    model_config = ModelConfig.from_name(model_name=model_name)

    if args.guidance is None:
        args.guidance = 1.0
    is_distilled = "base" not in model_config.model_name.lower()
    if args.guidance != 1.0 and is_distilled:
        parser.error("--guidance is only supported for FLUX.2 base models. Use --guidance 1.0.")

    model = Flux2Klein(
        model_config=model_config,
        quantize=args.quantize,
        model_path=args.model_path,
        lora_paths=args.lora_paths,
        lora_scales=args.lora_scales,
    )

    # === TeaCache wrap ============================================
    # apply_teacache mutates `model` in place (wraps generate_image,
    # patches _predict, registers a generation callback). The returned
    # TeaCacheHandle is also a context manager — entering is a no-op
    # but __exit__ calls handle.restore(), which is harmless in a
    # one-shot subprocess. Wrapping in `with` keeps the lifecycle
    # tidy and would matter if we ever re-used `model` after the
    # generation loop.
    #
    # Three failure modes the library raises that we tolerate
    # gracefully:
    #   - IncompatibleModelError: model variant outside the supported
    #     set (e.g. an upstream Klein bump we haven't validated). The
    #     wrap is purely a speedup so skipping it just costs us the
    #     speedup, not the render.
    #   - AlreadyPatchedError: shouldn't happen in our subprocess
    #     because Flux2Klein is fresh, but defensive in case mflux
    #     ever pre-patches.
    #   - Other TeaCacheError: same — skip the wrap, still render.
    handle = None
    try:
        handle = apply_teacache(model, rel_l1_thresh=THRESH)
        print(f"[mflux-teacache] wrap on (variant={handle.variant_id}, thresh={THRESH})",
              file=sys.stderr, flush=True)
    except (IncompatibleModelError, AlreadyPatchedError, TeaCacheError) as exc:
        print(f"[mflux-teacache] wrap skipped ({type(exc).__name__}: {exc}); rendering vanilla",
              file=sys.stderr, flush=True)
        handle = None
    # ============================================================

    memory_saver = CallbackManager.register_callbacks(
        args=args,
        model=model,
        latent_creator=Flux2LatentCreator,
    )

    try:
        width, height = DimensionResolver.resolve(
            width=args.width,
            height=args.height,
            reference_image_path=args.image_path,
        )

        for seed in args.seed:
            image = model.generate_image(
                seed=seed,
                prompt=PromptUtil.read_prompt(args),
                width=width,
                height=height,
                guidance=args.guidance,
                image_path=args.image_path,
                num_inference_steps=args.steps,
                image_strength=args.image_strength,
                scheduler="flow_match_euler_discrete",
            )
            ImageUtil.save_image(
                image=image,
                path=args.output.format(seed=seed),
                export_json_metadata=args.metadata,
            )
    except (StopImageGenerationException, PromptFileReadError) as exc:
        print(exc)
    finally:
        if memory_saver:
            print(memory_saver.memory_stats())
        # Restore on the way out so any future re-use of `model` in the
        # same interpreter (we don't currently re-use, but harmless to
        # be tidy) starts from a clean state. handle.restore() is
        # idempotent — safe to call even if we never wrapped.
        if handle is not None:
            try:
                handle.restore()
            except Exception:  # noqa: BLE001
                pass
    return 0


def main() -> int:
    # Quick disable knob — if image_engine sets MFLUX_TC_FLUX=0 we
    # transparently fall through. (image_engine wouldn't normally launch
    # the wrapper in that case but this defensive check keeps the script
    # callable by hand for A/B testing.)
    if os.environ.get("MFLUX_TC_FLUX", "1").strip().lower() in ("0", "false", "no"):
        return _fallback("MFLUX_TC_FLUX disabled")

    try:
        import mlx_teacache  # noqa: F401 — import probe
    except Exception as exc:  # noqa: BLE001
        return _fallback(f"mlx_teacache import: {exc!r}")

    try:
        from mflux.models.flux2.variants import Flux2Klein  # noqa: F401
        from mflux.models.flux2.cli import flux2_generate    # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return _fallback(f"mflux flux2 import: {exc!r}")

    try:
        return _wrapped_main()
    except SystemExit as exc:
        # argparse / mflux clean exit — propagate the code as-is.
        return int(exc.code) if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except Exception as exc:  # noqa: BLE001
        return _fallback(f"wrap failed at runtime: {exc!r}")


if __name__ == "__main__":
    sys.exit(main())
