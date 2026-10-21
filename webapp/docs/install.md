# Install, update and disk space

Phosphene runs on Apple Silicon Macs (M1 or newer) through Pinokio. This page explains how to install and update it, how much disk space you need, and what to do if a step fails.

## Install with Pinokio {#pinokio}

1. Install [Pinokio](https://pinokio.computer).
2. In Pinokio, open **Discover**, choose **Download from URL** and paste `https://github.com/mrbizarro/phosphene`.
3. Click **Install**. The installer checks your free disk space first. If there is not enough, it stops and shows how much you need and how much you have.
4. When the install finishes, click **Start**, then **Open Panel**. The panel opens at `http://127.0.0.1:8198`.

You can start creating as soon as the panel opens. A few extras keep downloading while you work, as listed below.

> **Note** Pinokio cannot install Phosphene on an Intel Mac or on Windows. Linux support on Apple Silicon is a separate community project that Pinokio does not install. See [Linux (community)](https://github.com/mrbizarro/phosphene/blob/main/docs/LINUX.md).

## What gets downloaded, and how much space you need {#disk}

| Part | Download | When |
|---|---|---|
| **Base install**: LTX-2.5 video engine and its files for reading prompts and showing live previews | 27.5 GB | Before you can click **Start**. You need about **32.5 GB free** to begin |
| **Background extras**: Gemma 3 (for **Enhance** and the Storyboard planner) and four small add-ons (LoRAs) for Upscale & Face Fix, Motion Control, Ingredients and Colorize | 10.7 GB | After **Start**, one at a time while you work. These downloads leave at least 10 GB free |
| **LTX-2.5 Q8 weights**, for trained characters and voices | 30.02 GB | optional, from **Settings → Models** or the Pinokio sidebar |
| **High add-on**, for High quality, Keyframes and Extend on LTX | 29.50 GB | Optional, from the Models window (click the health chip, then **Models**) |
| **Hailuo H3**, the second video engine | about 75 GB | optional, from the Pinokio sidebar. See [Hailuo H3](#docs/hailuo-h3/install) |
| **Music engine (YuE2)** | about 11 GB | optional, from the Audio tab or the Pinokio sidebar. See [Music Studio](#docs/music/install) |
| **LTX-2.3**, needed only to *train* a character | about 20 GB, plus about 21 GB for the trainer | offered when you open **Train Character** |

At a connection speed of 100 Mbps, the base install takes about 37 minutes to download. At 300 Mbps, it takes about 12 minutes; at 1 Gbps, about 4 minutes. Setting up the software adds a few minutes. If a download stops, it resumes where it left off.

If a feature needs an extra that has not finished downloading, it shows a download message or offers **Download now**. It does not fail without a message.

> **Tip** You can add a Hugging Face access token under **Settings** for roughly ten times faster downloads. HDR is the only add-on that requires this token. Everything else works without it.

## Check your Mac's memory {#memory}

Your Mac's memory determines which quality settings and engines you can use. Phosphene checks this automatically. To see what your Mac can run, click the health chip in the header, then **Tier**.

| Feature | Memory it needs |
|---|---|
| LTX video and Images | Any Apple Silicon Mac. Below 48 GB, you get smaller video sizes. LTX's High quality, Start & end frame and Extend are hidden |
| Music Studio | 24 GB or more |
| Training a character | 24 GB or more |
| Hailuo H3 | 36 GB or more for the compact engine. With 60 GB or more, you can also choose the full engine |
| LTX High · 720p | A 64 GB Mac; memory use peaks near 50 GB |

Before a long render, close apps that use a lot of memory, such as browsers with many tabs, Slack or the iOS Simulator. A render is the process of creating your video or audio. If your Mac runs out of memory, it stops the render. See [Troubleshooting](#docs/troubleshooting/memory).

## The Pinokio sidebar {#sidebar}

Select Phosphene in Pinokio to see these actions in the left sidebar. The available actions depend on what is installed and whether the panel is running.

- **Install** / **Resume Install**: install the required files. **Resume Install** appears if the last attempt did not finish. Files that finished downloading are kept.
- **Start**, **Open Panel**, **Terminal**: click **Start** to run Phosphene, then **Open Panel** to use it. **Terminal** shows its activity log.
- **Update**: see [Updating](#update).
- **Outputs**, **Models**, **Uploads**: open those folders in Finder.
- **Install Hailuo H3 (second video engine, ~75 GB)**: after installation, this becomes **Repair Hailuo H3** or **Update Hailuo H3 runner**. Both keep the downloaded model files.
- **Build Hailuo H3 compact engine**: appears on a 36–59 GB Mac when the model files are downloaded but the compact engine is not ready. It takes about 5 minutes and downloads nothing.
- **Install the music engine (YuE2, ~11 GB)**: after installation, this becomes **Repair the music engine**, which keeps the downloaded model files.
- **Reset engine (keeps models & outputs)**: rebuild the LTX engine and the software it needs. This keeps your models and finished work. It does not affect Hailuo H3; to repair H3, use **Repair Hailuo H3**.

## Update Phosphene {#update}

- The version pill in the header shows **Up to date**, or **Update to** followed by a newer version. When an update is available, click **Update now** in the banner to install it, or **Later** to wait.
- Click **Update** in the Pinokio sidebar to update everything, including the supporting software. Your queue, settings, models and finished work are kept.
- After an in-app update, the banner reads *"Updated to … — restart to finish"*. Click **Stop**, then **Start**, in Pinokio.
- If you update from a very old version and only the panel seems to change, click **Update** once more. The first update replaces the updater itself.
- If Pinokio's **Update** stops in its first second (versions 4.19.6 and older can do this), click **Update now** in Phosphene's own banner or version pill instead, then restart. See [Update stops right away](#docs/troubleshooting/update-stops).
- If the panel still behaves like the old version, it needs a restart. The version pill then reads **Restart Phosphene**. Click it, or click **Stop** and then **Start** in Pinokio.
- Hailuo H3 and the music engine have separate supporting software. If the panel says either needs an update, click that engine's **Update** or **Repair** entry in Pinokio's sidebar. Your downloaded model files are kept.

## If an install or update stops with an error {#fatal}

If a required step fails, Pinokio stops and shows a line beginning with `FATAL error:`. Scroll up in the log Pinokio shows for that install or update to find it. The line explains the problem and what to click next. In almost every case, **nothing was deleted**. After fixing the problem, click **Install** or **Update** again to continue.

Use the table below to match the text after `FATAL error:`:

| The line says | What to do |
|---|---|
| *not enough disk space to install Phosphene* | Free the space it names, then click **Install** again |
| *could not download the video engine version Phosphene needs … (a VPN, proxy or blocked GitHub is the usual cause)* | Check your connection, turn off the VPN or proxy, then click **Install** or **Update** again |
| *the engine environment did not install after 3 attempts* | Check your internet connection and free disk space, then click **Install** or **Update** again. If it repeats, report the line using the instructions below |
| *the video engine is on the wrong version* | Click **Install** or **Update** again. If it repeats, report the line |
| *Hailuo H3 needs a Mac with at least 36 GB of memory* | Your Mac can't run H3. Keep using LTX, which has every mode except Add sound |
| *Hailuo H3 needs about N GB of free disk space* | You need about 97 GB for a new install (the 75 GB download plus the 22 GB compact engine), or about 25 GB if the model files are already downloaded. Free the space, then click **Install Hailuo H3** again |
| *the compact Hailuo H3 engine could not be built* | This usually means the disk is full (the build needs about 22 GB). Free space, then click the H3 entry in Pinokio's sidebar again. It rebuilds from the downloaded model files |
| *a Phosphene render has been running for over 60 minutes, so the compact Hailuo H3 engine was not built* | Wait until Phosphene finishes its current render, then click the H3 entry in Pinokio's sidebar again |
| *the H3 venv has no working Python* | Click **Repair Hailuo H3** in Pinokio's sidebar. Your downloaded model files are kept |
| *YuE2 (the music engine) needs a Mac with at least 24 GB of memory* | This Mac can't run the music engine. The rest of Phosphene is unaffected |
| *YuE2 (the music engine) needs at least 14 GB free* | Free the space, then click **Install the music engine** again |
| *could not download the YuE2 music engine* | Check your internet connection and whether a VPN or proxy is blocking GitHub, then click **Install the music engine** again |
| *untracked/ignored paths obstruct Update. Move them; nothing deleted.* | Files you added inside the app folder are in the way of the update. The lines just above list them. Move them out of the Phosphene folder, then click **Update** again |
| *fetch failed (offline?) - nothing changed, re-run Update* | Reconnect, then click **Update** again |
| *the Update safety check did not pass - not updating* | Nothing was changed. Click **Update** again. If it repeats, report the line |

If your error is not listed, follow the instructions in its `FATAL error:` line. To report an error, copy that line and the few lines above it into a bug report. See [Reporting an issue](#docs/troubleshooting/report).

## Starting over without losing your work {#start-over}

- Click **Reset engine** in Pinokio to rebuild the LTX engine. Your models, finished work and settings are kept.
- To find your finished work, click **Outputs** in Pinokio's sidebar. This opens `mlx_outputs` inside the Phosphene folder.
- To find your downloaded models, click **Models** in the sidebar. This opens `mlx_models`. Don't delete this folder unless you want to download everything again.

If Phosphene opens but a task fails, models are missing or your queue pauses, see [Troubleshooting](#docs/troubleshooting).
