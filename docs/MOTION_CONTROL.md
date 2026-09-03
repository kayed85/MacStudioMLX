# Motion Control — driving a render from a clip you already have

**Video tab → Remix → Motion Control.**

You give Phosphene a clip. The render copies its **motion, camera move,
composition and pose**; your prompt paints a **new subject and a new scene**
onto that structure. A crane-out from a man's face over a suburban street
becomes a crane-out from a monk's face over a salt flat — same shot, different
world.

This is the official **`Lightricks/LTX-2.3-22b-IC-LoRA-Union-Control`**
adapter. It is public, un-gated, needs **no Hugging Face token**, and it is
fetched by the base install (`required_files.json` → `ic_union_control`,
654,465,352 bytes into `mlx_models/loras/ic/`).

---

## Why this document exists

The feature has shipped and worked for months, and **two long-time users asked
for it as if it did not exist** — @sohaibpp on Pinokio ("Motion control"), and
a user on X asking for "IC-LoRA support (especially Union Control / Pose /
Depth) exposed in the UI", noting correctly that the underlying `ltx-2-mlx`
already supports it.

Both were right about the naming and wrong about the capability. The mode was
called **"Control"**, nested one click inside a group called **"Remix"** whose
own sub-line said "your media → new video". Nothing on screen contained the
words anybody was searching for. That is a discoverability defect, not a
missing feature, and the fix was the copy:

* the Remix pill's sub-line now reads **"motion control · refs · color"** — the
  only Remix text visible without a click, so it carries the searchable words;
* the sub-chip is **"Motion Control"**, not "Control", and its tooltip names
  the Union Control adapter;
* the section header names the adapter, and the copy under the picker says
  what the mode does and — below — what it cannot do.

---

## How to use it

1. **Video** tab → **Remix** → **Motion Control**.
2. Pick a control clip in the picker, or paste a path.
3. Write a prompt describing the **new** subject and scene. Leave camera
   direction out of it: the camera is the control clip's job, and a prompt that
   also asks for a move is two instructions for one axis.
4. Generate.

Output **matches the control clip's resolution and length**. It runs the **Q4
distilled** checkpoint, so no Q8 pack is required and it works on every
hardware tier the panel serves.

### What you can feed it

| Input | Works? |
|---|---|
| An ordinary video (raw RGB) | **Yes** — this is the normal path. The Union adapter reads raw RGB; nothing has to be preprocessed. |
| A pose / depth / canny / segmentation **sequence** you already have | **Yes**, and it follows more tightly. Feed it exactly like any other clip. |
| An ordinary video you want turned *into* pose or depth | **No.** Phosphene ships **no preprocessor**. There is no OpenPose, no depth estimator, no canny pass anywhere in this install, and this document is not promising one. |

That last row is the honest half of the X request. "Union Control / Pose /
Depth" is one feature and three input formats: Phosphene serves the feature and
two of the three formats, and cannot derive the third for you.

### What makes a good control clip

The measured lesson from building the examples below: **the mode transfers
structure so faithfully that the prompt only gets room where the structure
leaves some.**

* **Works well** — shots whose frame content changes across the clip: camera
  moves, crane-outs, tracking shots, wide scenes, a figure moving through a
  space.
* **Works badly** — a single high-contrast subject filling a static frame. A
  hand-painted sign shot dead-on, driven with a "brass diving helmet" prompt,
  came back as the same sign in a colder palette with garbled lettering. There
  was nothing for the prompt to repaint.

If the render is coming back as a re-tell of your source, that is the reason.
`LTX_CONTROL_REF_STRENGTH` (default `1.0`, clamped to `[0, 1]`) loosens the
follow into an "inspired-by" — there is no UI control for it today.

---

## The 2.3 lane, stated as fact

**Lightricks has published exactly one LTX-2.5 IC-LoRA to date** — the Pixel
Spatial Upscaler, 2026-08-11. There is no 2.5 Union Control adapter, so Motion
Control is a **LTX-2.3** feature, and the panel does not pretend otherwise.

Phosphene's default generation is **LTX-2.5**. On 2.5, Motion Control is
**half-working, and it is the half that is hard to notice**:

| | LTX-2.3 | LTX-2.5 (default) |
|---|---|---|
| Motion / camera / composition transfer | Yes | **Yes** — the control clip rides a *pinned* reference latent (follow strength 1.0 ⇒ `mask_value = 0.0`), so the structure lands whether or not the adapter contributes anything. |
| Prompt repaints the subject | Yes | **Weak.** The render comes back as a warped re-tell of the control clip's own content, with visible smear. |

**Measured 2026-08-28**, not projected: same 4 s control clip (a crane-out from
a face to an aerial), same prompt, same seed `4242`, follow 1.0, each
generation with its own text encoder. On 2.3 the prompt produced the monk on
the salt flat that was asked for. On 2.5 it produced a young man in a European
plaza — the control clip's subject, relocated — and smeared through the middle
of the pull.

This is **not** the Ingredients failure. Ingredients on 2.5 is inert and is
**refused** (`RenderRefused("ingredients_generation")`), because a silently
ignored reference sheet costs ~11 GPU-minutes and returns an unrelated clip.
Motion Control still delivers the camera work, so refusing it would take a
working feature away from the default lane. It is offered, with the caveat
shown next to the picker:

* `ltx_control_full_repaint()` — the predicate, its own function beside
  `ltx_generation_serves_ingredients()` because the two will be fixed on
  different days.
* `LTX_CONTROL_GENERATION_NOTE` — the sentence, server-owned.
* `ltx_tiers_payload()` ships both as `control_full_repaint` /
  `control_generation_note`; `_paintControlGenNote()` places the note in
  `#controlGenNote` and shows it **only** where it is true.

To get the full repaint: install the 2.3 pack from the **Train** tab, or start
the panel with `LTX_MODEL_VERSION=ltx23`. The day a 2.5 Union adapter ships,
`ltx_control_full_repaint()` is the one line that changes.

---

## How it runs

`mlx_ltx_panel.py`, `mode == "control"` in `run_job_inner`. Structurally
identical to Colorize (`restore`), with one deliberate difference.

```
control_video_path
      │  ffprobe dims → floored to the engine's /64 grid; frames to 8k+1
      │  downscaled to the tier's i2v max side if it exceeds it
      ▼
job_spec  action = "generate_restore"
      │   model_dir            = Q4 distilled pack   (what the adapter was trained against)
      │   loras                = [union-control @ 1.0]  + whatever the form carried
      │   video_conditioning   = [[control_clip, 1.0]]   ← the FOLLOW lever
      │   stage1_steps=8, stage2_steps=3                 ← two-stage, like Colorize
      ▼
HELPER.run → ICLoraPipeline → mlx_outputs/<source>_control_<stamp>.mp4
```

**The follow lever.** `video_conditioning`'s second element is the reference
strength. `VideoConditionByReferenceLatent` holds the reference latent with
`mask_value = 1.0 - strength`: at **1.0** the control clip is pinned pristine
and the render follows its structure; at **0.0** it is fully denoisable, which
is what **Ingredients** uses to recompose *away* from its reference sheet.
Control and Ingredients are the same pipeline with opposite ends of one dial.
Override with `LTX_CONTROL_REF_STRENGTH`.

**Reference downscale.** The Union weight carries
`reference_downscale_factor=2` — the IC encoder halves the reference itself
(`iclora_utils`) — so the panel feeds the **full** output dimensions, exactly
as Colorize does.

**The /64 grid.** Control and Colorize derive their canvas from the *source
clip*, after `make_job` has run, so they missed the normalisation every other
LTX lane gets. They floored to /32 while the two-stage pipeline snaps to /64 on
its own: a 768×416 control clip rendered **768×384** while the log line and the
sidecar both said 416 — the "Width × Height LIES" defect, and here it also
crushed the picture 8% vertically, because the reference is resized onto the
canvas we name. Both branches now floor to /64. **If your control clip's
dimensions are multiples of 64, nothing is resized at all** — which is the
cheapest quality win available on this mode.

---

## Worked examples

Rendered 2026-08-28 on an M4 Max 64 GB, LTX-2.3 Q4 distilled, follow 1.0,
~90 s each at 4 s / 97 frames. Sources are existing clips from `mlx_outputs/`;
the prompts contain **no camera direction at all**, so every camera move in the
output arrived from the control clip.

| Output | Control clip | Prompt (abridged) | What it proves |
|---|---|---|---|
| `example_control_craneout.mp4` | `example_control_craneout_source.mp4` (a 10 s H3 crane-out, retimed 2.5× to 4 s) | "a monk in a saffron robe on a vast white salt flat…" | A full crane choreography — face → street → rooftops → cloud — transferred beat for beat onto a different world. Driveway geometry maps to salt-flat cracks; rooftops map to scrub tufts. |
| `example_control_skater.mp4` | `example_control_skater_source.mp4` | "a knight in dented plate armour bounds across a moonlit courtyard…" | Human **pose and body motion**, including the jump, on a tracking camera. |
| `example_control_aerial.mp4` | `example_control_aerial_source.mp4` | "an endless field of purple lavender under a black storm front…" | A wide moving landscape repainted wholesale while the move is preserved. |

Play each pair side by side. The frame-by-frame correspondence is the point.

---

## Related

* `docs/H3_ENGINE.md` — the other video engine.
* `README.md` → Features → Video → Remix.
* `required_files.json` → `ic_union_control`, `ic_ingredients`, `ic_colorize`.
