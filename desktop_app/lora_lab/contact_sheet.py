"""Build a simple HTML contact sheet over one or more eval folders.

Each eval folder is expected to contain `manifest.json` (written by
`evaluate.py`) and a matching `.png` per render entry.

Usage::

    ./scripts/run.sh python -m lora_lab.contact_sheet \
        --tags lora_step1500_str10_n9 lora_step1500_str15_n9 \
        --output outputs/morning/index.html \
        --title "lora-lab — overnight evaluation"
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path


def _render(tags: list[str], eval_root: Path, output: Path, title: str) -> int:
    cells = []
    for tag in tags:
        folder = eval_root / tag
        manifest_path = folder / "manifest.json"
        if not manifest_path.exists():
            print(f"skip: no manifest in {folder}", file=sys.stderr)
            continue
        manifest = json.loads(manifest_path.read_text())
        lora = manifest.get("lora") or "(no LoRA — baseline)"
        strength = manifest.get("strength")
        header_label = f"<b>{html.escape(tag)}</b><br><span class='meta'>lora={html.escape(Path(str(lora)).name)} · str={strength}</span>"
        for entry in manifest["renders"]:
            if entry.get("error"):
                continue
            png_name = entry.get("png")
            if not png_name:
                # fall back to mp4 stem.png (post-render ffmpeg extraction)
                mp4_name = entry.get("mp4")
                if mp4_name:
                    png_name = mp4_name.rsplit(".", 1)[0] + ".png"
            if not png_name:
                continue
            png_path = (folder / png_name)
            if not png_path.exists():
                continue
            rel_png = png_path.relative_to(output.parent.resolve()) if output.parent.resolve() in png_path.resolve().parents else png_path.resolve()
            cells.append({
                "img": str(rel_png),
                "prompt": entry["prompt"],
                "seed": entry["seed"],
                "tag_header": header_label,
                "wall_s": entry.get("wall_s"),
            })

    # Group cells by tag header so each iteration block is visually grouped.
    blocks = {}
    for c in cells:
        blocks.setdefault(c["tag_header"], []).append(c)

    parts = [f"""<!doctype html>
<html><head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Helvetica Neue', sans-serif;
       background: #0d0d10; color: #e6e6ea; margin: 24px; }}
h1 {{ font-weight: 600; }}
.block {{ margin-top: 32px; padding: 16px; background: #15151a; border-radius: 10px; }}
.block-header {{ font-size: 13px; color: #aab; margin-bottom: 12px; }}
.meta {{ color: #8a8a96; font-weight: 400; font-size: 12px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 14px; }}
.cell {{ background: #1d1d24; border-radius: 8px; overflow: hidden; }}
.cell img {{ width: 100%; display: block; }}
.cap {{ padding: 8px 10px; font-size: 12px; line-height: 1.4; }}
.cap .p {{ color: #d6d6e0; }}
.cap .s {{ color: #6f6f7a; font-size: 11px; margin-top: 4px; }}
</style></head><body>
<h1>{html.escape(title)}</h1>"""]

    for header, items in blocks.items():
        parts.append(f"<div class='block'><div class='block-header'>{header}</div><div class='grid'>")
        for c in items:
            parts.append(
                f"<div class='cell'>"
                f"<img loading='lazy' src='{html.escape(c['img'])}'>"
                f"<div class='cap'><div class='p'>{html.escape(c['prompt'])}</div>"
                f"<div class='s'>seed {c['seed']}"
                + (f" · {c['wall_s']}s" if c.get('wall_s') is not None else "")
                + "</div></div></div>"
            )
        parts.append("</div></div>")
    parts.append("</body></html>")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(parts))
    print(f"wrote {output} ({len(cells)} cells across {len(blocks)} blocks)")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tags", nargs="+", required=True, help="eval tag folder names under outputs/eval/")
    p.add_argument("--eval-root", default="outputs/eval")
    p.add_argument("--output", default="outputs/morning/index.html")
    p.add_argument("--title", default="lora-lab — eval contact sheet")
    args = p.parse_args()

    return _render(
        tags=args.tags,
        eval_root=Path(args.eval_root).resolve(),
        output=Path(args.output).resolve(),
        title=args.title,
    )


if __name__ == "__main__":
    sys.exit(main())
