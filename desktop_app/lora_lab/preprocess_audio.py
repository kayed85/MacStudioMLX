"""Audio-side preprocessor for LTX-2 MLX trainer (lora-lab Phase B).

Encodes voice WAV slices into LTX audio VAE latents, written in the layout
the trainer expects (paired with image latents by index):

    output_dir/
      .precomputed/
        audio_latents/
          latent_0000.safetensors   {latents: [1, 8, T, 16]}
          latent_0001.safetensors
          ...

When the audio sample count differs from the image sample count, slices
are cycled so every image index gets a paired audio latent. This keeps
the trainer happy when iterating across the dataset.

The trainer's ``TextToVideoStrategy`` reads ``audio_latents/`` when
``training_strategy.generate_audio: true`` is set in the YAML config —
that's what triggers joint audio-video training with the audio loss
contributing alongside the video loss.

Usage::

    ./scripts/run.sh python -m lora_lab.preprocess_audio \\
        --audio /path/to/bizarrotrn.voice.wav \\
        --output ./dataset_bizarro \\
        --slice-seconds 4.0 \\
        --match-image-count

The model dir defaults to the same LTX-2.3 Q4 snapshot as preprocess_images.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import mlx.core as mx
import numpy as np
from safetensors.numpy import save_file as save_safetensors

from ltx_core_mlx.model.audio_vae import (
    AudioProcessor,
    AudioVAEEncoder,
    encode_audio,
)
from ltx_core_mlx.utils.audio import load_audio
from ltx_core_mlx.utils.memory import aggressive_cleanup
from ltx_core_mlx.utils.weights import (
    load_split_safetensors,
    remap_audio_vae_keys,
)
from ltx_trainer_mlx.preprocess import _resolve_model_dir

logger = logging.getLogger(__name__)

# Resolved dynamically — see lora_lab/__init__.py for the resolution
# order. Public Pinokio installs use the vendored mlx_models dir.
from lora_lab import resolve_default_model_dir
DEFAULT_MODEL_DIR = resolve_default_model_dir()

AUDIO_SAMPLE_RATE = 16000   # LTX audio VAE expects 16 kHz mono


def _load_audio_vae_encoder(model_dir: Path) -> tuple[AudioVAEEncoder, AudioProcessor]:
    """Build + load the LTX audio VAE encoder, mirroring AudioConditioner.load().

    Same weight-loading pattern as ``utils.blocks.AudioConditioner``: strip
    the ``audio_vae.encoder.`` prefix, then layer in the
    ``per_channel_statistics.*`` keys from the full audio_vae blob, then
    run them through ``remap_audio_vae_keys``.
    """
    encoder = AudioVAEEncoder()
    weights = load_split_safetensors(
        model_dir / "audio_vae.safetensors", prefix="audio_vae.encoder."
    )
    all_audio = load_split_safetensors(
        model_dir / "audio_vae.safetensors", prefix="audio_vae."
    )
    for k, v in all_audio.items():
        if k.startswith("per_channel_statistics."):
            weights[k] = v
    weights = remap_audio_vae_keys(weights)
    encoder.load_weights(list(weights.items()))
    processor = AudioProcessor()
    return encoder, processor


def _slice_waveform(waveform: mx.array, sample_rate: int, slice_seconds: float) -> list[mx.array]:
    """Cut waveform into fixed-length slices. Drops the trailing tail if shorter than slice_seconds.

    waveform shape: (1, channels, samples)
    """
    samples_per_slice = int(slice_seconds * sample_rate)
    total_samples = waveform.shape[-1]
    n_slices = total_samples // samples_per_slice
    if n_slices == 0:
        raise ValueError(
            f"Waveform ({total_samples / sample_rate:.2f}s) is shorter than slice ({slice_seconds:.1f}s)"
        )
    slices: list[mx.array] = []
    for i in range(n_slices):
        start = i * samples_per_slice
        end = start + samples_per_slice
        slices.append(waveform[:, :, start:end])
    return slices


def _count_existing_image_latents(precomputed_dir: Path) -> int:
    latents_dir = precomputed_dir / "latents"
    if not latents_dir.exists():
        return 0
    return sum(1 for f in latents_dir.glob("latent_*.safetensors"))


def preprocess_audio(
    audio_path: str,
    output_dir: str,
    model_dir: str = DEFAULT_MODEL_DIR,
    slice_seconds: float = 4.0,
    match_image_count: bool = True,
    start_offset_seconds: float = 0.0,
) -> None:
    """Encode a voice WAV into LTX audio latents, paired with the existing image latents.

    Args:
        audio_path: Path to the source WAV.
        output_dir: Dataset root — preprocessed data goes to ``<output>/.precomputed/``.
            Must already contain ``latents/`` from a prior image-preprocess run if
            ``match_image_count`` is True.
        model_dir: LTX model dir (for ``audio_vae.safetensors``).
        slice_seconds: Length of each audio slice. Codex's smoke pipeline used 4.0s.
        match_image_count: If True, cycle slices to produce one audio latent per
            image latent (so every training sample has a paired voice clip).
        start_offset_seconds: Skip this many seconds at the start of the WAV
            (use to trim silence / breath).
    """
    mx.set_cache_limit(mx.device_info()["memory_size"])
    model_dir_path = Path(_resolve_model_dir(model_dir))

    src = Path(audio_path)
    if not src.exists():
        raise FileNotFoundError(f"audio file not found: {src}")

    precomputed = Path(output_dir) / ".precomputed"
    audio_latents_dir = precomputed / "audio_latents"
    audio_latents_dir.mkdir(parents=True, exist_ok=True)

    print(f"loading WAV: {src}")
    audio_data = load_audio(
        str(src),
        target_sample_rate=AUDIO_SAMPLE_RATE,
        start_time=start_offset_seconds,
        max_duration=None,
    )
    if audio_data is None:
        raise ValueError(f"no audio found in {src}")
    waveform = audio_data.waveform   # (1, channels, samples)
    duration_s = waveform.shape[-1] / audio_data.sample_rate
    print(f"  duration: {duration_s:.2f}s @ {audio_data.sample_rate} Hz, channels={waveform.shape[1]}")

    slices = _slice_waveform(waveform, audio_data.sample_rate, slice_seconds)
    print(f"  cut into {len(slices)} slice(s) of {slice_seconds:.1f}s each")

    if match_image_count:
        n_images = _count_existing_image_latents(precomputed)
        if n_images == 0:
            print(
                "  warning: no image latents found in .precomputed/latents/ — "
                "writing one audio latent per slice without pairing."
            )
            target_count = len(slices)
        else:
            print(f"  matching image latent count: {n_images}")
            target_count = n_images
    else:
        target_count = len(slices)

    print(f"loading audio VAE encoder from {model_dir_path}")
    encoder, processor = _load_audio_vae_encoder(model_dir_path)

    # Encode each unique slice once, then cycle/replicate to fill target_count.
    encoded_unique: list[mx.array] = []
    for i, slice_wf in enumerate(slices):
        print(f"  [{i + 1}/{len(slices)}] encoding {slice_seconds:.1f}s slice...")
        latent = encode_audio(slice_wf, audio_data.sample_rate, encoder, processor)
        mx.eval(latent)
        encoded_unique.append(latent)
        if (i + 1) % 4 == 0:
            aggressive_cleanup()

    del encoder, processor
    aggressive_cleanup()
    print(f"  encoded {len(encoded_unique)} unique slices, shape={encoded_unique[0].shape}")

    # Strip the leading B=1 from each encoded latent: the trainer's dataset class
    # adds a batch dim when collating, so the on-disk per-file shape must NOT
    # include batch. encode_audio returns (1, 8, T, 16); we save (8, T, 16).
    # (Matches preprocess_images.py which writes latent[0] for the same reason.)
    #
    # Write one .safetensors per training-sample index, cycling through encoded slices.
    for i in range(target_count):
        latent = encoded_unique[i % len(encoded_unique)]
        out_path = audio_latents_dir / f"latent_{i:04d}.safetensors"
        save_safetensors(
            {"latents": np.array(latent[0].astype(mx.float32))},
            str(out_path),
        )

    print(f"\npreprocessing complete. {target_count} audio latents written to:")
    print(f"  {audio_latents_dir}")
    print(f"  (sourced from {len(encoded_unique)} unique slice(s) via cycling)")


def main() -> int:
    p = argparse.ArgumentParser(description="LTX-2 audio-only LoRA preprocessor")
    p.add_argument("--audio", required=True, help="source WAV file")
    p.add_argument("--output", required=True, help="dataset root (contains .precomputed/)")
    p.add_argument(
        "--slice-seconds", type=float, default=4.0,
        help="length of each audio slice (default: 4.0 — matches Codex's smoke pipeline)",
    )
    p.add_argument(
        "--match-image-count", action="store_true",
        help="cycle audio slices to produce one audio latent per existing image latent",
    )
    p.add_argument(
        "--start-offset", type=float, default=0.0,
        help="skip this many seconds at the start of the WAV (default: 0)",
    )
    p.add_argument("--model-dir", default=DEFAULT_MODEL_DIR)
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    preprocess_audio(
        audio_path=args.audio,
        output_dir=args.output,
        model_dir=args.model_dir,
        slice_seconds=args.slice_seconds,
        match_image_count=args.match_image_count,
        start_offset_seconds=args.start_offset,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
