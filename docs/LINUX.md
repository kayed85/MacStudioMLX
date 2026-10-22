# Linux on Apple Silicon - community-maintained, best effort

Contributed by [@joshuaswarren](https://github.com/joshuaswarren) (PR #94).
macOS through Pinokio is the supported platform; this target is maintained by
the community and is not covered by the release gates. Reports are welcome.

## Install

From the app root:

```
bash scripts/linux/install.sh        # engine venv, omarchy-mlx wheel pinned by sha256
bash scripts/linux/install_h3.sh     # optional: the Hailuo H3 engine
./run_panel.sh
```

The omarchy-mlx wheel is a third-party build; the installers verify its pinned
release sha256 before installing it. Models download from the panel, as on a Mac.

## Hailuo H3 on Linux

On Linux, H3 can use a complete Q8 DiT pack without the bf16 master. Both config
files and all indexed shards must exist; a `.built_ok` marker alone is not
enough. macOS still requires the bf16 master for install discovery.

## Measured runs on Omarchy

Linux on Apple Silicon uses MLX with a Vulkan backend instead of Metal. These are separate runs of different engines, not a comparison of machines or Best/Fast controls. Each output was viewed.

| Machine | Engine and settings | Actual output | Native job time | Peak memory | Driver source |
| --- | --- | --- | ---: | --- | --- |
| 14-inch M2 Max MacBook Pro | FLUX.2-klein-4B, native queue, browser on another machine | 512x512 PNG | 58.3 s | Not recorded | Not captured at run |
| 14-inch M2 Max MacBook Pro | H3 native UI, Draft, Fast (3-step), seed 42, 640x384 | 73 frames, 1280x720 export, 3.042 s duration | 1610.79 s | Not recorded | Not captured; binary hash below |
| 16-inch M1 Max MacBook Pro | LTX native queue, 8 steps, seed 42 | 9 frames, 256x128, 0.375 s duration | 181.26 s | 31.1G as reported, no swap | [69bde15340](https://github.com/joshuaswarren/mesa-1/commit/69bde153405cf02d52b0c9810307a0c7d0085d52) |

The LTX row used an unreleased diagnostics runtime. The published 0.7.32 wheel cannot run its uint8 Take operation, and the installer pin remains unchanged. A requested height of 160 becomes 128 on the 64-pixel canvas grid. The queue also normalized a four-step request to eight steps; this is not a four-step timing.

The H3 run has a viewed 45-second recording of the prompt, Best/Fast controls and finished clip. A Best/Fast timing comparison waits for machine recovery and restoration of the H3 weights.

Driver provenance:

```text
LTX, 16-inch M1 Max MacBook Pro
  source commit  69bde153405cf02d52b0c9810307a0c7d0085d52
  package        omarchy-mlx-vulkan-omarchy 1:26.2.4.git69bde15340-2
  driver sha256  40282811c6e14ef991f72936bb675008cb4d6b3199b1785bc34c7976ab89c425
  kernel         7.1.12-2-12.6-sep-ARCH

H3, 14-inch M2 Max MacBook Pro
  source commit  not captured; this run predates the package above
  driver sha256  a70fb890552f5282ac6ee91b6800d310de9759fe7d18232a48dcbeb31db7c6af
  kernel         7.1.12-2-12.6-sep-ARCH
```

