# Phosphene debug recipes and patch authoring

Moved out of CLAUDE.md (former §19–§20).

## 19. Debug recipes

### "Helper died silently — where did it die?"

```bash
# Scan the panel log for the last step:* breadcrumb
curl -s http://127.0.0.1:8198/status | python3 -c \
  "import json,sys; [print(l) for l in json.load(sys.stdin)['log'] if 'step:' in l][-3:]"
```

If last step is `step:get_pipe` → death during pipeline init (model
load). Likely OOM or weight-corrupt. If last step is
`step:generate_and_save` → death during gen (denoise / VAE decode).
Likely OOM peak or Metal kernel issue.

For non-OOM crashes, check `~/Library/Logs/DiagnosticReports/python3.11_*.crash`
or `*.ips` from around the crash time.

### "Panel says port 8198 already in use"

```bash
lsof -nP -iTCP:8198 -sTCP:LISTEN          # who has it
pkill -f mlx_ltx_panel.py                  # nuke all panels
```

Common cause: a previous manual `python mlx_ltx_panel.py` is still
running. Pinokio's start.js can't take over a port someone else owns.
**Don't spawn the panel manually unless you're going to kill it before
handing back.**

### "I edited the UI but the browser hasn't changed"

The frontend lives in files under `webapp/` (docs/ARCHITECTURE.md), and
what you must restart depends on which file:

- **CSS (`webapp/style/panel.css`) or JS (`webapp/js/*.js`)** — served
  from disk on every request with `Cache-Control: no-cache`. NO panel
  restart needed; just hard-refresh the browser (Cmd+Shift+R).
- **Markup (`webapp/index.html`)** — read ONCE at panel import (the
  served page is pinned to the booted process on purpose; see the
  build-stamp comment in the template). Restart the panel (Pinokio
  Stop → Start, or `pkill -f mlx_ltx_panel.py` then Start), then
  hard-refresh.
- **Python (`mlx_ltx_panel.py`, `panel/routes_*.py`)** — restart the
  panel.

### "I edited the helper or a patch but nothing changed"

```bash
# 1. Re-apply patches (idempotent, applies to site-packages)
cd ~/pinokio/api/phosphene.git
./ltx-2-mlx/env/bin/python3.11 patch_ltx_codec.py

# 2. Restart the helper subprocess (panel keeps running)
curl -X POST http://127.0.0.1:8198/helper/restart

# 3. Submit a test job
curl -X POST http://127.0.0.1:8198/run \
  -d "mode=t2v" -d "prompt=quick test" \
  -d "width=512" -d "height=288" -d "frames=49" \
  -d "steps=8" -d "quality=draft"

# 4. Wait for done
until [ "$(curl -s http://127.0.0.1:8198/status | python3 -c 'import json,sys;d=json.load(sys.stdin);print(0 if d.get(\"current\") else 1)')" = "1" ]; do sleep 5; done
```

### "How loud / clean is the audio in this clip?"

```bash
FFPROBE="/Volumes/Topaz Video AI 4.0.6/Topaz Video AI.app/Contents/MacOS/ffprobe"
FFMPEG="/Volumes/Topaz Video AI 4.0.6/Topaz Video AI.app/Contents/MacOS/ffmpeg"
"$FFMPEG" -i out.mp4 -af "volumedetect" -f null - 2>&1 | grep -E "max_volume|mean_volume"
```

Healthy on dialogue: `max_volume` between -2 and -15 dB. Below -25 dB
peak is the regression fingerprint — usually means a wrong mlx version.

### "Re-encode for X / web (yuv420p, faststart, 8 Mbps)"

```bash
"$FFMPEG" -y -i in.mp4 \
  -c:v h264_videotoolbox -profile:v high -pix_fmt yuv420p \
  -b:v 8M -maxrate 12M -bufsize 16M \
  -movflags +faststart \
  -c:a aac -b:a 192k \
  out_x.mp4
```

X (Twitter) rejects yuv444p with a misleading "aspect ratio" error.
Use this recipe for any social upload.

### "Stuck queue / can't cancel"

```bash
curl -X POST http://127.0.0.1:8198/stop          # cancel current job
curl -X POST http://127.0.0.1:8198/queue/clear   # empty the queue
curl -X POST http://127.0.0.1:8198/helper/restart # nuke the helper
```

### "Force re-download a corrupt model file"

```bash
# Delete the file in question, then click Install in Pinokio (resumes
# only the missing files; intact ones are skipped via `hf` hash check).
rm ~/pinokio/api/phosphene.git/mlx_models/ltx-2.3-mlx-q4/<bad-file>.safetensors
# Then in Pinokio: Phosphene → Install (or Resume Install)
```

### "Force a different hardware tier for testing"

```bash
LTX_TIER_OVERRIDE=base ./run_panel.sh    # pretend this Mac is < 48 GB
```

`run_panel.sh` actively unsets this on every run unless explicitly set
on the command line — there's a hard-learned bug history about leaked
overrides bleeding across panel restarts.

### "What does `/status` actually return?"

```bash
curl -s http://127.0.0.1:8198/status | python3 -m json.tool | head -60
```

Top-level keys: `running`, `paused`, `current`, `queue`, `history`,
`log`, `tier`, `memory`, `helper`, `download`, `repos_ready`,
`repos_total`, `base_available`, `q8_available`, `q8_missing`,
`comfy_pids`, `outputs`, `server_now`, `avg_elapsed_sec`.

## 20. Patch authoring template (`patch_ltx_codec.py`)

When adding a new patch:

```python
# ---- Patch N: <one-line summary> --------------------------------------------
# Why: <symptom + root cause + why this fix>. Reference upstream
# file:line and any external context (commit SHA, GitHub issue, paper).
PATCH_<NAME>_OLD = '''<exact text expected to be in the upstream file>'''
PATCH_<NAME>_NEW = '''<replacement text — include a `# PATCHED (LTX23MLX): ...` comment>'''
```

Then in `main()`:

```python
target = _find("<relative/path/to/upstream.py>")
outcome = apply_patch(
    target, PATCH_<NAME>_OLD, PATCH_<NAME>_NEW,
    marker="<unique substring that proves NEW is on disk>",
    label="<NAME>",
    upgrade_marker="<optional: substring that distinguishes a newer NEW from an older NEW>",
)
# If this patch is optional (don't fail install on drift):
if outcome in (OUTCOME_DRIFT, OUTCOME_MISSING):
    print("  [<NAME>] note: ...", file=sys.stderr)
    outcome = OUTCOME_ALREADY
outcomes.append(("<NAME>", outcome))
```

Conventions:
- `marker` is a substring **inside** `PATCH_<NAME>_NEW` that's unlikely
  to appear in the unpatched file. Convention: include
  `PATCHED (LTX23MLX...)` somewhere in the new text.
- `upgrade_marker` lets you ship a v2 of a patch without forcing users
  to nuke their venv. Example: codec patch's `+faststart` was added
  later; `upgrade_marker="+faststart"` distinguishes v1 (no faststart)
  from v2 and rewrites the line in place.
- Patches that target an upstream code structure that may legitimately
  vanish (e.g. an internal helper that gets refactored away) should be
  marked **optional** in main() so install warns instead of fails.

