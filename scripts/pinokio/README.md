# `scripts/pinokio/` — payloads too big to hand Pinokio directly

Every file here is a step body lifted out of `install.js` / `install_h3.js`
because **Pinokio 8.0.x wedges when a single dispatch is large** — one CPU core
at 100%, no child process ever spawns, and the install is dead until the user
force-quits.

## What Pinokio actually dispatches

`pinokiod/kernel/shells.js` `launch()`:

| `message` | what happens |
|---|---|
| an **array**, no `chain` param | each element is launched in its **own shell** — so an array is already safe and the *element* is the unit |
| an **array** with `chain` | `Shell.build()` joins them into **one** dispatch |
| a **string** | **one** dispatch, as-is |

and `shell.js:1911` writes whichever it is in a single unchunked call:

```js
this.ptyProcess.write(`${this.exec_cmd || this.cmd}${this.EOL}`)
```

The same file's `Shell.emit()` (shell.js:665-711) chunks anything over **1024**
chars into 256-byte pieces with a 10 ms gap — so the kernel already knows large
pty writes are unsafe. The `shell.run` path just doesn't do it.

## Why v3.6.2's fix wasn't enough

@Morac2 bisected the ceiling in #50: **764 chars hangs, ~373 passes**. v3.6.2
adopted "no generated shell line over 350" and the issue was closed. The rule
was right; the *measurement* was wrong. Every long payload in this repo is
written `[ ... ].join("\n")` — deliberately a **string**, so an `if/else` runs
in one shell — and those are exactly the dispatches nobody counted. The venv
step's source lines were all under 110 chars and it dispatched **1,417** in one
go:

| report | finding | their fix |
|---|---|---|
| #50 @Morac2 | 764-char line hangs, ~373 passes | shortened the line (v3.6.2) |
| #50 @davidaircloud | lines short, joined message still **1,417** — still hangs | moved the body to a `.sh`, dispatched `bash ../x.sh` |
| #56 @itrejomx | same stall before `uv venv` | split the payload into smaller steps |

A local pty probe (bash `--noprofile --norc` — Pinokio's own shell and args —
with one unchunked `os.write`) completes a 1,475-byte payload fine, so this is
**not** a kernel tty limit. It is Pinokio's terminal layer above the pty, and
dispatch size is the only lever we have from this side.

## The rule

`scripts/check_pinokio_scripts.js` enforces it against the **loaded modules**,
modelling `launch()` exactly:

> **no single dispatch over 350 chars**

350 is the width the field bisection supports (764 hangs, ~373 passes); the worst
dispatch in the repo is 313 (4.20; the gate was 500 while six sat between 350 and
498). Anything bigger belongs here, behind one short `bash …` line.

## Failing so Pinokio notices (4.20)

**Pinokio 8.2 ignores a `shell.run` step's exit code.** The kernel's default `on`
handlers (pinokiod `kernel/shells.js`) end a script only when the step's **output**
matches `/error:/i` or `/errno /i` (`error:…triton` exempt). So an `exit 1` that
prints neither word marches on — a refused 75 GB H3 download started anyway, a
failed engine checkout went on to a 37 GB download, a failed LTX-2.5 fetch ended
in "Phosphene is ready" with no weights.

The convention (5b18448, swept through every script by 4.20):

- A **real** failure prints ONE line, then exits non-zero:
  `FATAL error: <plain reason + what to do>` — e.g. "…did not finish downloading -
  check your internet connection and free disk space, then click Install again".
  Plain words, who it hits and the next click; no stack trace.
- An **optional** failure (a best-effort fetch, a pack the panel can retry) prints
  neither word. `scripts/post_update.sh` wraps its optional steps in `defang`, which
  turns `FATAL error:` into `WARN:`, any other `error:` into `problem -` and
  `Errno ` into `errno-`, and hands back the command's own exit status.
- An inline command with no script behind it goes through `must.sh`:
  `bash scripts/pinokio/must.sh '<reason>' <command…>` runs it unchanged and adds
  the FATAL line when it exits non-zero.
- Stay under ~350 characters per dispatch — the reason text counts.

`scripts/check_pinokio_scripts.js` holds the convention (a call with no way to stop
the install, a non-zero `exit` with no FATAL line, an optional `|| echo` that says
`error:`, an optional script that prints FATAL, a bare script call in
`post_update.sh`), and `test_fatal_install_paths.py` runs every script's failure and
success path against the kernel's own regex.

## House rules for files here

- Dispatched as `bash <path>` — **never** `./<path>`, so no execute bit is
  needed and nothing depends on git preserving the mode.
- Preserve the semantics the inline payload had. These were all `"\n"`-joined
  strings, which ran **without** `set -e`: the step's exit code was the last
  command's, and an intermediate failure did not stop the step. Keep it that
  way unless you mean to change it, and say so if you do.
- Values that belong to the release — the vendored pin SHA above all — stay in
  the `.js` beside the comment block that documents them and arrive here as
  arguments. Never inline a SHA in this directory.
- `{{cwd}}` is substituted by Pinokio in the *message*, so anything path-shaped
  must be passed in as an argument rather than written here.

## What is here

| file | dispatched by | what it does |
|---|---|---|
| `ltx_checkout.sh` | `install.js`, `scripts/post_update.sh` | the vendored engine pin move — the ONE implementation, and the pin literal itself (its header argues the exception to the rule above) |
| `ltx_venv.sh` | `install.js` | the venv build that dispatched 1,417 chars and hung Pinokio 8.0.x |
| `ltx25_weights.sh` | `install.js` | LTX-2.5 base + Gemma 4 encoder (27.47 GB), the default generation. Fatal on failure: a panel that boots into a generation with no weights cannot render |
| `q8_weights.sh` | `download_q8.js` | the optional LTX-2.5 Q8 pack (30.02 GB) — what trained characters and voices need. NOT the High add-on, which is a separate 29.5 GB click |
| `mflux_pack.sh` | `install.js` | the image-engine pack (Ideogram 4 + Qwen-Edit). OPTIONAL: defangs its own output |
| `h3_preflight.sh` | `install_h3.js` | Hailuo H3 disk/RAM preflight (FATAL on a refusal) |
| `h3_build_q8.sh` | `install_h3.js`, `scripts/post_update.sh` (defanged) | the H3 q8 DiT build |
| `update_fetch.sh` | `update.js` | resolve the configured upstream and fetch its remote; a failed fetch stops Update before the tree can move. Moved out of update.js for #114 — its `echo 'FATAL error:'` tripped Pinokio 8.2's matcher while the shell echoed the dispatch, killing Update on the success path |
| `must.sh` | `install.js`, `update.js` (the obstruction guard, converge and post_update steps) | run one required command; print `FATAL error: <reason>` when it exits non-zero — at runtime, so the word never rides the echoed dispatch (#114). A missing target script exits non-zero, so must.sh is also the "script is missing, repair the tree" abort |
| `install_preflight.sh` | `install.js` | the install disk check (FATAL when the free space is known to be short) |
| `h3_roots.sh`, `h3_checkout.sh`, `h3_venv.sh`, `h3_deps.sh` | `install_h3.js` (`h3_checkout.sh`/`h3_deps.sh` also `post_update.sh`, defanged) | H3 roots + `h3_enter` / `h3_fetch_weights`, the engine pin, its venv, its packages |
| `music_preflight.sh`, `music_clone.sh`, `music_checkout.sh`, `music_venv.sh`, `music_sync.sh` | `install_music.js`, the panel's music installer (`post_update.sh`, defanged) | the YuE2 install steps |
| `a2v_stems_deps.sh` | `install.js`, `scripts/post_update.sh` (step 7b), `POST /a2v/separator/install` | vocal separation for Lip-sync "Listen to the voice only": demucs 4.0.1 into the engine venv with torch/numpy PINNED to what is on disk, htdemucs weights into `mlx_models/demucs`. The panel runs `scripts/a2v_separate.py` (not the demucs CLI, which dies on torchaudio >= 2.9). Missing separator = the render is refused with an install offer, never quietly run on the full mix |
