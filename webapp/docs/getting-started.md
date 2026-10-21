# Getting started

Phosphene makes video, images, songs and sound on your Mac — nothing is uploaded, nothing is rented. You describe a shot, it goes into a queue, and the finished file lands in Outputs.

Not installed yet, or short on disk space? See [Install, update and disk space](#docs/install).

## The tabs {#tabs}

The row of tabs at the top of the left column is where you choose what to make. [[sc:tabs.switch]] switches between them from the keyboard.

| Tab | What it is for |
|---|---|
| **Video** | A clip from a prompt — or from an image, a first and last frame, keyframes, a clip to extend, or your own media (Remix). See [Video](#docs/video). |
| **One Shot** | One continuous shot of 30 seconds to 2 minutes that never cuts. See [One Shot](#docs/one-shot). |
| **Images** | Stills: edit a reference image, or put words on a picture with Ideogram 4. See [Images](#docs/images). |
| **Storyboard** | Plan a whole film from one idea, render every shot, grade them, cut them. See [Storyboard](#docs/storyboard). |
| **Editor** | A timeline: trim, split, move sound, add titles and music, render the film. See [Editor](#docs/editor). |
| **Audio** | **Compose** writes songs with vocals or instrumentals ([Music Studio](#docs/music)). **Music video** plans a film from a song and your pictures. **Lip-sync** makes a face talk or sing to a sound file you bring ([Audio](#docs/audio)). |
| **Train Character** | Teach Phosphene a face (and a voice) from your photos. See [Train Character](#docs/train-character). |

## The engine {#engine}

The engine switch in the header chooses which model renders: **LTX** or **Hailuo H3**. They are two different engines with different strengths, not a good one and a spare:

- **LTX** — every Video mode, LoRAs and trained characters.
- **Hailuo H3** — joint video, dialogue and sound: Text, Image, Start & end frame, Keyframes, Extend, Add sound and Lip-sync. It is a separate install of about 75 GB and needs 36 GB of memory. See [Hailuo H3](#docs/hailuo-h3).

The form changes with the engine: controls one engine does not use are hidden, and the Quality and Length chips show that engine's own sizes. The switch only appears when this Mac can run more than one engine. [How they compare](#docs/video/engines).

## The queue {#queue}

Everything you generate goes into one queue and renders one job at a time. The panel at the bottom has four tabs:

- **Now** — the render in progress, with a live preview of the shot as it forms. **Stop early** (on the preview) asks first, then stops it: *"nothing is saved"*, and the queue carries on with the next job.
- **Queue** — the jobs waiting. The × on a card removes it.
- **Recent** — what finished, filtered by All / Videos / Photos / Audio.
- **Logs** — the render log. See [Reading the log](#docs/troubleshooting/logs).

Under the Generate button: **⊞ Batch** pastes many prompts at once and queues them all; **Pause queue** holds the queue (the button then reads **Resume queue**); **Clear** removes every waiting job — the one running carries on.

## Outputs {#outputs}

The gallery on the right shows what you have made. Click a card to put it on the player.

- **All / Videos / Photos / Audio** filter it, and the search box finds outputs by prompt words, model, LoRA, size or seed ([[sc:search.focus]] jumps to it).
- **Show all** loads older renders than the newest 60.
- [[sc:outputs.step]] steps through the gallery, [[sc:player.toggle]] plays and pauses, [[sc:outputs.expand]] expands the player to full screen.
- Over the player, the action row offers what you can do next with that clip: **Extend** it, run **Upscale & Face Fix** on it, send it **To film**, **Animate** a still, see its **Params**.
- A clip with no sound carries a **No sound · Add sound** pill on its gallery card (and the player says the same). One click puts the clip on the player and opens Hailuo H3's **Add sound** with it picked.
- **Share** (in the clip's **More** menu and in the full-screen viewer) copies a one-line caption with the Phosphene link and shows the file in Finder, ready to attach. Nothing is uploaded.
- The eye icon hides a clip from the gallery (there is currently no way to bring it back from the UI — see [Video](#docs/video/more) for the manual restore). The trash button on a card moves the file to the macOS Trash after asking — restore it from Finder if you change your mind. [[sc:outputs.trash]] does the same for the selected output.

## Where files go {#files}

| What | Where |
|---|---|
| Your renders | the `mlx_outputs` folder inside the Phosphene install. The folder button in the Outputs header opens it in Finder; Pinokio's **Outputs** item opens it too. |
| Trained characters and downloaded LoRAs | `mlx_models/loras/` |
| Settings | `panel_settings.json` |

Every render also writes a small sidecar file beside it with its settings. That is what **Params** reads, and what lets Outputs search by LoRA or seed.

## The first start {#first-start}

On a new install, 27.5 GB downloads before **Start** appears: the LTX-2.5 engine, its text encoder and the preview decoder. The installer checks disk space first: you need about 32.5 GB free, and it tells you if there is not enough.

After **Start**, 10.7 GB more downloads in the background, one at a time: Gemma 3 (what **Enhance** and the **Storyboard planner** run on) and the adapters for **Upscale & Face Fix**, **Control**, **Ingredients** and **Colorize**.

You can follow progress in the panel. The background download keeps 10 GB free on the drive and waits if there is less. Until a model arrives, the feature that needs it says it is downloading and offers **Download now**.

Updating an existing install downloads and removes nothing.

## Updating {#update}

- The version pill in the header says where you stand: **Up to date**, or **Update to** a newer version. When one is out, a banner offers **Update now** / **Later**.
- In Pinokio, the **Update** item in the Phosphene sidebar updates everything, including Python dependencies. Updating keeps your queue, settings and models. If an update stops, see [When an install stops](#docs/install/fatal).
- After an in-app update the banner reads *"Updated to … — restart to finish"*: click **Stop**, then **Start** in Pinokio. If the update touched dependencies, it tells you to use Pinokio's **Update** instead.
- If an update started from a very old version only seems to move the panel, click **Update** once more — an old version updates its updater first.
- If Pinokio's **Update** stops in its first second (it can on 4.19.6 and older), use **Update now** in Phosphene's own banner or version pill instead, then restart. From 4.19.7 on, Pinokio's button works again. See [troubleshooting](#docs/troubleshooting/update-stops).

> **Tip** Press [[sc:docs.open]] anywhere to open these docs on the shortcuts page, and use the search box on the left to find any button by the word written on it.
