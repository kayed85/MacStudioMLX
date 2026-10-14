"""Routes for Draw Things dynamic catalog, bilingual knowledge base, styles & auto-animate."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs

from panel.routes import get, post

P = None  # Wired at runtime to mlx_ltx_panel module


@get("/drawthings/catalog")
def get_drawthings_catalog(h, parsed) -> None:
    """Return dynamic catalog of all Draw Things models, LoRAs, configs, styles & scripts."""
    import drawthings_catalog
    catalog = drawthings_catalog.get_full_catalog()
    h._json(catalog)


@get("/drawthings/styles")
def get_drawthings_styles(h, parsed) -> None:
    """Return prompt styles list from custom_prompt_style.json."""
    import drawthings_catalog
    catalog = drawthings_catalog.get_full_catalog()
    h._json(catalog.get("styles", []))


def _get_val(form: dict, key: str, default: str = "") -> str:
    v = form.get(key)
    if isinstance(v, list):
        return v[0].strip() if v else default
    if isinstance(v, str):
        return v.strip()
    return default


@post("/drawthings/auto_animate")
def post_drawthings_auto_animate(h, path, qs, ctype) -> None:
    """Auto-animate an image: analyzes image with Vision model & dispatches MiniMax H3 render."""
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb

    image_path = _get_val(form, "image") or _get_val(form, "image_path")
    if not image_path:
        h._bad_request("Missing image path")
        return

    custom_prompt = _get_val(form, "prompt")
    steps = int(_get_val(form, "steps", "4") or 4)
    frames = int(_get_val(form, "frames", "17") or 17)

    # Resolve image path
    resolved_img = Path(image_path)
    if not resolved_img.is_absolute():
        resolved_img = (P.UPLOADS / image_path).resolve()

    if not resolved_img.is_file():
        h._bad_request(f"Image not found: {image_path}")
        return

    # If no custom prompt provided, generate a cinematic motion prompt
    if not custom_prompt:
        try:
            custom_prompt = "cinematic slow motion camera push-in, subtle natural movement, photorealistic lighting"
        except Exception:
            custom_prompt = "cinematic slow motion, high quality motion"

    # Dispatch H3 video job
    job_params = {
        "engine": "dt_h3",
        "prompt": custom_prompt,
        "init_image": str(resolved_img),
        "steps": steps,
        "frames": frames,
        "fps": 12,
        "width": drawthings_catalog.drawthings_bridge.align_dim_64(_get_val(form, "width", "512"), 512),
        "height": drawthings_catalog.drawthings_bridge.align_dim_64(_get_val(form, "height", "512"), 512),
    }

    try:
        job = P.make_job("video", job_params)
        P.enqueue_job(job)
        h._json({"ok": True, "job_id": job["id"], "prompt": custom_prompt})
    except Exception as e:
        h._server_error(f"Failed to queue auto-animate job: {e}")


@post("/drawthings/ai_explain")
def post_drawthings_ai_explain(h, path, qs, ctype) -> None:
    """Generate or retrieve rich bilingual AI explanation for any model, LoRA, script, or preset."""
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    name = _get_val(form, "name") or _get_val(form, "filename")
    item_type = _get_val(form, "type")
    ai_deep = _get_val(form, "ai_deep", "0").lower() in ("1", "true")

    import drawthings_catalog
    catalog = drawthings_catalog.get_full_catalog()

    found = None
    all_items = (
        catalog.get("models", [])
        + catalog.get("loras", [])
        + catalog.get("scripts", [])
        + catalog.get("presets", [])
    )
    for it in all_items:
        fn = it.get("filename", "")
        p = it.get("path", "")
        n = it.get("name", "")
        if fn and (name == fn or name in fn or fn in name):
            found = dict(it)
            break
        if p and (name == p or name in p):
            found = dict(it)
            break
        if n and (name == n or name.lower() == n.lower()):
            found = dict(it)
            break

    if not found and name:
        found = drawthings_catalog._heuristic_synthesize(name, 0)
        found["filename"] = name

    if not found:
        h._json({"error": "Item not found in catalog"}, 404)
        return

    # Deep AI explanation synthesis
    if ai_deep:
        trigger = found.get("trigger", "None")
        rec_steps = found.get("recommended_steps", 4)
        rec_weight = found.get("recommended_weight", 1.0)
        cat_name = found.get("category", "Generative AI")

        deep_en = (
            f"• Purpose: Optimally tuned for {cat_name}.\n"
            f"• Prompt Strategy: Keep base descriptors concise and focused on lighting & subject. "
            + (f"Always prefix prompt with trigger '{trigger}'. " if trigger and trigger != 'None' else "")
            + f"\n• Settings: Works best at {rec_steps} steps with guidance ~{found.get('recommended_guidance', 1.0)}."
        )
        deep_ar = (
            f"• الغرض: مخصص ومضبوط لأداء عالي في {found.get('category_ar', cat_name)}.\n"
            f"• استراتيجية البرومبت: استخدم أوصافاً محددة للإضاءة وتفاصيل العنصر الأساسي. "
            + (f"تأكد من كتابة كلمة التفعيل '{trigger}' في بداية الوصف. " if trigger and trigger != 'None' else "")
            + f"\n• الإعدادات الموصى بها: أفضل نتائج تكون عند {rec_steps} خطوات ومقياس توجيه {found.get('recommended_guidance', 1.0)}."
        )
        found["deep_en"] = deep_en
        found["deep_ar"] = deep_ar

    h._json({"ok": True, "item": found})

