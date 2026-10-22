# Phosphene panel reference: settings, LoRAs, required_files, helper protocol

Moved out of CLAUDE.md (former §15a, §15b, §16, §17).

## 15a. `panel_settings.json` schema

User-controllable preferences. Persisted at `<repo>/state/panel_settings.json`
(Y1.004+); pre-Y1.004 lived at `<repo>/panel_settings.json` and is migrated
on first boot by `_migrate_state_dir()`. State directory is gitignored.

The whole `state/` dir is symlinked into Pinokio's fs.link drive so it
survives Reset → Reinstall (see "Persistent storage / fs.link" in install.js
+ update.js). All persisted-state files (`panel_settings.json`,
`panel_queue.json`, `panel_hidden.json`) live there.

Read at panel startup; mutated via `POST /settings`. The helper subprocess
inherits codec env vars from this at spawn — when settings change, the
panel kills the helper so the next job picks up new values.

```jsonc
{
  "version": 2,                              // bumped from v1 when secrets added
  // Metadata so the UI knows which preset to highlight as active.
  // "custom" means pix_fmt + crf were set manually outside any preset.
  "output_preset": "standard" | "archival" | "web" | "custom",
  "output_pix_fmt": "yuv420p" | "yuv444p" | "yuv422p"
                  | "yuv420p10le" | "yuv422p10le" | "yuv444p10le",
  "output_crf": "0".."30",                   // string (passed via env vars)

  // Secrets. Stored locally only, never sent over the network except
  // as Authorization headers / ?token= URL params to the upstream
  // services (civitai.com, huggingface.co). NEVER returned by GET
  // /settings — the public response surfaces booleans only.
  "civitai_api_key": "",                     // empty = not configured
  "hf_token": ""                             // empty = not configured
}
```

### `GET /settings` security contract

The full settings dict — including `civitai_api_key` and `hf_token` —
is read by the backend only. The HTTP API exposes a *masked* view via
`get_settings_public()`:

```jsonc
{
  "version": 2,
  "output_preset": "standard",
  "output_pix_fmt": "yuv420p",
  "output_crf": "18",
  "has_civitai_key": false,                  // bool, never the value
  "has_hf_token": false                      // bool, never the value
}
```

This applies on every response that includes settings — success path,
validation errors, helper-restart confirmations. A user with the panel
proxied through ngrok / tunneled to the LAN never leaks their saved
secrets through a status poll.

### Token resolution priority

`_active_civitai_key()` and `_active_hf_token()` resolve the active
token in this order:

1. The saved value in `panel_settings.json` (set via the Settings
   modal). This is the path normal users take.
2. The corresponding env var (`CIVITAI_API_KEY` / `HF_TOKEN`). Power
   users with shell-level config keep working unchanged.

Empty string in #1 falls through to #2. The settings UI's "Clear"
button writes an empty string to #1, so a user who configured a key
in the UI and then wants to revert to env-var-based config can do
so cleanly.

For HF specifically, `huggingface_hub.snapshot_download` *also* reads
`~/.cache/huggingface/token` (the file `hf auth login` writes). We
don't override that file — if neither settings nor `HF_TOKEN` env is
set, the library falls back to that cached file. This means power
users who already ran `hf auth login` get gated LoRAs working without
touching the panel's token settings.

Preset table is the single source of truth, defined in `OUTPUT_PRESETS`
in `mlx_ltx_panel.py`:

| key | pix_fmt | crf | Approx 5s @ 1280×704 | Use case |
|---|---|---|---|---|
| `standard` ⭐ default | yuv420p | 18 | ~7 MB | Visually lossless, plays everywhere (X / IG / Discord) |
| `archival` (UI label "Video production") | yuv444p | 0 | ~50 MB | Mathematically lossless, full 4:4:4 chroma. For color grading, compositing, VFX, anywhere downstream re-encode is expected. Internal key kept as `archival` for backwards compat with settings files saved before the rename. |
| `web` | yuv420p | 23 | ~3 MB | Smallest, mobile / embedding |

`+faststart` is always applied (codec patch) regardless of preset, so
gallery thumbnails always render the first frame instantly.

When adding a new preset: edit `OUTPUT_PRESETS` in `mlx_ltx_panel.py`
and the `order` array in the settings modal JS (`openSettingsModal`).
The blurb shown in the UI is read from the server at modal-open time,
so server is the source of truth — no JS strings to keep in sync.

## 15b. LoRA system

LTX 2.3 supports LoRA adapters via the upstream `apply_loras()` function
in `ltx_core_mlx.loader.fuse_loras`. The fusion is **weight-level**: at
pipeline load time, each LoRA's `(lora_A, lora_B)` matrix pair is
multiplied + added to the corresponding base-model weight, modified in
place, then quantized. This is *not* a runtime adapter — switching
LoRAs requires reloading the pipeline.

### Integration hook

The base `TextToVideoPipeline.load()` checks
`getattr(self, "_pending_loras", None)` and, if present, calls
`_fuse_pending_loras(transformer_weights, lora_paths)` before
quantizing the transformer. Set `pipe._pending_loras = [(path, strength), ...]`
between construction and the first job.

The wrapper in `mlx_warm_helper.py:_attach_loras` does this. It also
resolves HuggingFace repo IDs (`Lightricks/LTX-2.3-22b-IC-LoRA-HDR`)
to local files via `snapshot_download`, picking the largest
`.safetensors` in the repo (LoRA repos sometimes ship auxiliary files
like scene embeddings alongside the main weights).

### Pipeline cache invalidation

`get_pipe(kind, loras=...)` keys the cache on `(kind, frozen LoRA set)`.
Changing the LoRA set forces a pipeline rebuild — the cached pipeline's
weights have the previous LoRA set fused in and there's no clean way
to "unfuse." Cost is the standard ~30s pipeline reload.

The frozen LoRA set is order-insensitive (set hash, not list hash) —
fusion is commutative so `[A, B]` and `[B, A]` produce identical
weights and should hit the same cache entry.

### `mlx_models/loras/` directory

User-installed LoRAs live as `<name>.safetensors` files at the install
root's `mlx_models/loras/` directory. An optional sidecar JSON next to
each `.safetensors` carries display metadata:

```jsonc
{
  "name": "Crisp Enhance",
  "description": "Sharper detail and edge enhancement.",
  "trigger_words": [],                      // optional, prepended to prompt at submit
  "recommended_strength": 0.8,              // 0.0..1.5 typical, displayed as default in slider
  "preview_url": "https://...",             // optional, shown in the picker
  "base_model": "LTXV 2.3",
  "civitai_id": 2530917,                    // optional, for attribution
  "civitai_version_id": 2844417,
  "civitai_url": "https://civitai.com/models/2530917",
  "downloaded_at": "2026-05-01T..."
}
```

Sidecar absent → panel falls back to the filename as the display name
and zero metadata. Sidecar shape matches the JSON `_civitai_download`
writes, so a CivitAI install lands a usable picker entry without any
hand-editing.

### Curated LoRA registry

`CURATED_LORAS` in `mlx_ltx_panel.py` lists Lightricks' official LoRAs
that we know about. Currently:

| key | repo | UI surface | Note |
|---|---|---|---|
| `hdr` | `Lightricks/LTX-2.3-22b-IC-LoRA-HDR` | "HDR" toggle in form | Hidden from picker (`is_hdr_toggle: True`); exposed as a plain checkbox |
| `motion-track` | `Lightricks/LTX-2.3-22b-IC-LoRA-Motion-Track-Control` | Listed in picker | IC-LoRA, needs video conditioning (not yet wired in panel UI) |
| `union-control` | `Lightricks/LTX-2.3-22b-IC-LoRA-Union-Control` | Listed in picker | Same |

All three are **gated** on Hugging Face — users must accept the license
on the model page AND have a valid HF token (`hf auth login`) before
the helper's `snapshot_download` will work. The helper translates the
upstream `GatedRepoError` into a clear actionable error.

### Job spec extension

The `generate` and `extend` actions accept an optional `loras` param:

```jsonc
{
  "action": "generate",
  "id": "...",
  "params": {
    "mode": "t2v",
    "prompt": "...",
    "loras": [
      {"path": "/abs/path/to/style.safetensors", "strength": 0.8},
      {"path": "Lightricks/LTX-2.3-22b-IC-LoRA-HDR", "strength": 1.0}
    ],
    ...
  }
}
```

Path may be a local file or an HF repo id. Strength is clamped to
±2.0 by the panel before sending; values outside the typical 0.0..1.5
range are usually nonsense and risk numerical issues during fusion.

### HDR toggle semantics

The `hdr` boolean on the job (separate from `loras`) is a UI shortcut.
Worker-side, `run_job_inner` resolves it to
`{"path": "Lightricks/LTX-2.3-22b-IC-LoRA-HDR", "strength": 1.0}`
and appends to the LoRA list before calling the helper. Users see
"HDR" as a feature; the LoRA implementation is hidden.

### CivitAI bridge

CivitAI's REST API is the de-facto LoRA distribution hub for LTX 2.3.
Two endpoints proxy through the panel:

- `/civitai/search` filtered to `baseModels=LTXV 2.3` (the canonical
  string CivitAI uses for LTX-2.3 LoRAs as of 2026-05). Cursor-paginated.
- `/civitai/download` streams a `.safetensors` into `mlx_models/loras/`
  + writes a sidecar JSON.

Both endpoints accept an optional `CIVITAI_API_KEY` env var. As of 2025,
CivitAI requires a token for most LoRA downloads (search still works
without). The token goes in `?token=<key>` on the URL — many CDN hops
only honor that form, not the Authorization header.

### Adding a new curated LoRA

1. Edit `CURATED_LORAS` in `mlx_ltx_panel.py` with `id`, `name`,
   `description`, `repo_id`, `default_strength`, and either
   `is_hdr_toggle: True` (= surfaces as a header pill, hidden from
   picker) or `False` (= shows in picker as a usable entry).
2. If `is_hdr_toggle`, also add the HTML toggle pill (next to "No
   music"), the JS sync block, and the `make_job` resolution that maps
   the boolean to the curated repo id.

## 16. `required_files.json` schema

Single source of truth for "what counts as installed." Read by:
- `pinokio.js` → menu rendering (Install vs Resume Install vs Start)
- `mlx_ltx_panel.py` → `/status` and per-job validation
- (indirectly) `install.js` and `download_q8.js` — their `hf download`
  paths must match the `local_dir` declared here.

```jsonc
{
  "_comment": "...",
  "repos": [
    {
      "key": "q4" | "gemma" | "q8",
      "kind": "base" | "optional",          // "base" = blocks Start, "optional" = unlock features
      "name": "human-readable label",
      "blurb": "what this is for",
      "repo_id": "<hf-org/repo>",            // for `hf download`
      "local_dir": "mlx_models/<repo-name>", // relative to install root
      "size_gb": 25,                          // approximate, displayed in UI
      "files": [                              // every required file inside local_dir
        "transformer-distilled.safetensors",
        "connector.safetensors",
        ...
      ]
    }
  ],
  "env": {
    "marker_paths": [                         // OR-checked; first existing path wins
      "ltx-2-mlx/env/pyvenv.cfg",             // canonical Pinokio install
      "ltx-2-mlx/.venv/pyvenv.cfg",           // alternate manual layouts
      ...
    ]
  },
  "min_size_bytes": 1024                      // file must be >= this to count as "complete"
}
```

When the upstream model layout changes, **edit this file first**. The
menu, panel, and install scripts auto-pick up the change.

## 17. Helper protocol (panel ↔ helper)

The helper subprocess (`mlx_warm_helper.py`) speaks newline-delimited
JSON over stdin/stdout. The panel's `WarmHelper` class wraps it.

### Actions panel sends

```jsonc
// Generation request (T2V, I2V, I2V-clean-audio)
{ "action": "generate",
  "id": "<job-id>",
  "params": { "mode": "t2v"|"i2v"|"i2v_clean_audio",
              "prompt": "...", "output_path": "...",
              "height": 704, "width": 1280, "frames": 121,
              "seed": 42, "steps": 8, "image": "<path>" } }

// Extend request
{ "action": "extend",
  "id": "<job-id>",
  "params": { "video_path": "...", "prompt": "...",
              "extend_frames": 5, "extend_direction": "after",
              "extend_steps": 12, "cfg_scale": 1.0,
              "output_path": "...", "seed": 42 } }

// Keyframe (FFLF)
{ "action": "keyframe", "id": "...", "params": {...} }

// Prompt rewrite (Gemma)
{ "action": "enhance", "prompt": "..." }
```

### Events helper emits

```jsonc
{ "event": "ready",  "model": "<path>", "low_memory": true }    // on startup
{ "event": "log",    "line": "free-form panel log line" }        // any informational message
{ "event": "done",   "id": "...", "output": "<path>",
                     "elapsed_sec": 33.6, "seed_used": 42 }
{ "event": "error",  "id": "...", "error": "<message>",
                     "trace": "<python traceback>" }
{ "event": "exit",   "reason": "python_normal_exit"
                            | "sigterm(15)"
                            | "sigterm(2)"
                            | "idle" }                            // graceful exit
{ "event": "enhanced", "original": "...", "enhanced": "..." }    // /prompt/enhance result
```

**No exit event + stdout pipe closes = non-graceful death** (SIGKILL,
SIGSEGV, SIGABRT, SIGBUS). The panel inspects `proc.poll()` returncode
and translates the negative signal number to a name in the user-facing
error.

### Step breadcrumb log lines

The helper emits these inside `generate`/`extend` actions so the panel
can localize a silent death to a phase:

```
step:get_pipe kind=t2v
step:get_pipe done
step:generate_and_save mode=i2v 1280x704 121f steps=8
step:generate_and_save done
```

When a job dies silently, the panel log's last `step:*` line tells you
which phase (pipeline init vs actual generation).

