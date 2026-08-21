module.exports = {
  // On-demand Q4 HQ components download for M1/M2/16GB Macs.
  // ~12 GB, resumable. The panel auto-detects when they land and unlocks
  // High quality, FFLF, and Extend modes in 4-bit precision.
  run: [
    {
      method: "notify",
      params: {
        html: "<b>Downloading Q4 HQ Components (~12 GB)…</b><br>This is the High quality / FFLF / Extend configuration for 16GB Macs. Resumable if interrupted."
      }
    },
    {
      method: "shell.run",
      params: {
        venv: "ltx-2-mlx/env",
        env: { HF_HUB_ENABLE_HF_TRANSFER: "1" },
        message: [
          "hf download dgrauet/ltx-2.3-mlx-q4 --local-dir mlx_models/ltx-2.3-mlx-q4 --include 'transformer-dev.safetensors' --include 'ltx-2.3-22b-distilled-lora-384.safetensors'"
        ]
      }
    },
    {
      method: "notify",
      params: {
        html: "<b>Q4 HQ Components ready.</b><br>High quality, FFLF keyframing, and Extend options are now unlocked in the panel."
      }
    }
  ]
}
