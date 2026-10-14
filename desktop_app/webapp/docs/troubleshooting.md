# Troubleshooting

When Phosphene refuses a render, it says why and what to do. These are the messages people actually see.

## Memory and this Mac's tier {#memory}

The health chip in the header shows memory at a glance; click it for the **Tier**, **Memory**, **Helper**, **Models**, **Queue** and **Render** rows. Click **Tier** to see what this Mac can run.

- *"Helper killed by the OS — out of memory"* — macOS stopped the render. Close memory-heavy apps (browsers, Slack, the iOS Simulator) and try again, or switch Quality to **Quick**, which uses about half the memory.
- *"Image pre-flight: … needs a N GB Mac"* — that image engine does not fit this Mac. Pick **Auto** or a lighter preset. *"… needs ~N GB free"* means it fits, but other apps are holding the memory right now.
- *"Training needs at least 24 GB of memory"* — character training cannot run on this Mac.

### Hailuo H3 on a 36–60 GB Mac {#h3-compact}

H3's full engine needs 60 GB. From 36 GB it runs on its compact Q8 engine, which is built on your Mac once:

- **H3 not installed yet:** in Pinokio, open **Phosphene** in the sidebar and click **Install Hailuo H3 (second video engine, ~75 GB)**. The install builds the compact engine at the end by itself (about 5 more minutes).
- **H3 installed, compact engine missing** — *"Hailuo H3 runs on this Mac — on its reduced-RAM lane … it is not built here yet"*: click **Build Hailuo H3 compact engine** in the same sidebar (~5 minutes, no download).

Both entries are in the sidebar while the panel is running.

Do that, then choose Settings → **Hailuo H3 model** → Automatic or Compact. Below 36 GB, H3 is not available: render on LTX, which serves every mode.

## A model or add-on is not downloaded {#missing}

- *"Extend needs the LTX-2.5 High add-on (the Q8 model), which isn't downloaded on this Mac yet"* — the same for Keyframes and High quality. Install it from the Models window (click the health chip, then **Models**).
- *"Upscale & Face Fix needs the LTX-2.5 Pixel Spatial Upscaler adapter"* — download it from the Models window, then render again.
- *"Stopped before rendering — the model weights are incomplete"* — a download was interrupted. Open the Models window and resume it. Settings → **Verify model files** checks every file and offers a re-download of any that are damaged.

## "Click Update" — an engine older than the panel {#stale-engine}

*"This install's vendored engine predates LTX-2.5's text encoder … Click Update — and if the first click only moves the panel, click it once more."*

The panel was updated but the engine underneath was not. Click **Update** in Pinokio's Phosphene sidebar. If nothing seems to change, click it a second time: an update started by an old version updates itself first. For H3, *"the installed Hailuo H3 runner is behind this panel"* means: re-run **Install Hailuo H3** — every weight already on disk is kept.

If you updated and the panel still behaves like the old version, it is still running the old code. The version pill in the header then reads **Restart Phosphene** — click it, or click **Stop**, then **Start**, in Pinokio.

## The GPU watchdog {#gpu-watchdog}

*"the macOS GPU watchdog killed a Metal command buffer"* — macOS itself stopped a GPU task that ran too long. It is a driver-level kill, not a Phosphene bug report, and it is most common on M1 and M2 Macs. Phosphene renders the same job again once, straight away, in **short GPU steps** (smaller pieces of GPU work, a little slower), and remembers the size: its next render on this Mac starts in short steps, and the Queue row says so. If macOS stops it even in short steps, that size is paused on this Mac for 24 hours (or until the next update) and the card offers **Retry smaller**. If it keeps happening, the message links to the GitHub issue where chip, macOS version and the crash log help most.

## A character training run failed {#training-failed}

*"training exited with code 1"* — the trainer stopped before finishing. Check the **Logs** tab for the actual reason: an out-of-memory kill (switch to a Mac with more RAM, or a smaller preset if one exists), a missing LTX-2.3 training download (the Train tab's own preflight card offers it), or a caption-encode timeout on a very long caption. Training under 24 GB always fails — that Mac cannot run it.

## The panel says it's offline {#offline}

*"Phosphene offline"*, and pressing **Generate** does nothing — the panel process isn't answering. Nothing you were typing is lost: the prompt stays as you left it, and the page reconnects and picks the queue back up on its own once the panel is running again. Start it from Pinokio's Phosphene sidebar if it doesn't come back within a few seconds.

## The queue paused itself {#queue-paused}

*"Queue paused: the last N renders failed the same way"* — after three renders in a row fail with the same problem, Phosphene stops burning through the rest of the queue on something a retry can't fix. Fix the cause the message names, then press **Resume**; or **Clear queue** to drop the rest and start over.

## The queue after a restart {#queue-restart}

Restarting the panel resumes the queue: a queue that was paused when the panel stopped starts again on its own, and the log says so.

## Reading the log {#logs}

The **Logs** tab in the bottom panel shows the render log. When a render dies, the last line that starts with `step:` names the stage it reached — that line is the most useful thing to include in a report. Pinokio's **Terminal** shows the same output.

## Reporting an issue {#report}

The bug button in the header opens **Report a bug**: it fills in the version, your Mac's details and the last 50 log lines, and opens a GitHub issue in a new tab for you to finish — nothing is sent until you submit it there. Issues live at [github.com/mrbizarro/phosphene/issues](https://github.com/mrbizarro/phosphene/issues).
