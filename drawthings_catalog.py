"""Draw Things Dynamic Catalog & Bilingual Knowledge Base.

Scans all models, LoRAs, community configs, prompt styles, and scripts
from Draw Things, generating rich bilingual (Arabic & English) documentation,
trigger keywords, recommended settings, and automated descriptions for
any newly discovered files.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

DRAWTHINGS_BASE_DIR = Path("/Users/mk/Library/Containers/com.liuliu.draw-things/Data/Documents")
DRAWTHINGS_MODELS_DIR = DRAWTHINGS_BASE_DIR / "Models"
DRAWTHINGS_SCRIPTS_DIR = DRAWTHINGS_BASE_DIR / "Scripts"

# Curated knowledge base for all known models & LoRAs
KNOWN_CATALOG: Dict[str, Dict[str, Any]] = {
    # System Video Engines
    "ltx_2_5": {
        "name_en": "Lightricks LTX-Video 2.5 (Audio-Visual Diffusion)",
        "name_ar": "محرك الفيديو LTX-Video 2.5 (توليد سينمائي مع صوت متزامن)",
        "type": "video_model",
        "category": "Cinematic Video",
        "category_ar": "توليد فيديو سينمائي",
        "desc_en": "State-of-the-art open audio-visual video diffusion model by Lightricks. Native 24 fps, synchronized speech and ambient sound, text-to-video and image-to-video.",
        "desc_ar": "أحدث محرك فيديو مفتوح المصدر من Lightricks يولد فيديو سينمائي بدقة عالية بمعدل 24 إطار/ثانية مع دمج الأصوات والحوارات المتزامنة.",
        "recommended_steps": 30,
        "recommended_guidance": 3.0,
        "hardware_fit": "16 GB+ RAM",
    },
    "hailuo_h3": {
        "name_en": "Hailuo MiniMax H3 (Turbo DiT)",
        "name_ar": "محرك الفيديو Hailuo MiniMax H3 (تيربو فائق السرعة)",
        "type": "video_model",
        "category": "Cinematic Video",
        "category_ar": "توليد فيديو سينمائي",
        "desc_en": "High-motion cinematic video diffusion model with synchronized audio. 3-step Turbo LoRA allows fast 2-minute renders on Apple Silicon.",
        "desc_ar": "محرك فيديو فائق السرعة يتميز بحركات واقعية وفيزيائية معقدة وصوت متزامن. بفضل لورا التيربو (3 خطوات) يولد المشاهد خلال دقيقتين فقط.",
        "recommended_steps": 3,
        "recommended_guidance": 1.0,
        "hardware_fit": "16 GB RAM",
    },
    # Base Video Models
    "minimax_h3_fl2va_q8p.ckpt": {
        "name_en": "MiniMax H3 Text/Frame-to-Video (Turbo)",
        "name_ar": "فيديو MiniMax H3 (توليد سينمائي من النص أو الإطار الأولي)",
        "type": "video_model",
        "category": "Video Generation",
        "category_ar": "توليد الفيديو",
        "desc_en": "Advanced video diffusion model with synchronized audio. Capable of generating cinematic 17-100+ frame scenes at 12 fps with 3-step or 4-step Turbo acceleration.",
        "desc_ar": "أحدث محرك فيديو سينمائي فائق السرعة مع استخراج ودمج الصوت المتزامن تلقائياً. يولد مشاهد من 17 إلى 100+ إطار بـ 3 أو 4 خطوات فقط، ويعمل بسلاسة على أجهزة 16GB RAM.",
        "recommended_steps": 3,
        "recommended_guidance": 1.0,
        "recommended_resolution": "512x512 or 768x512",
        "best_loras": ["minimax_h3_3step_lora_f16.ckpt", "minimax_h3_turbo_v4_lora_f16.ckpt"],
        "hardware_fit": "16 GB RAM (M1/M2/M3/M4 Pro/Max)",
    },
    "minimax_h3_ref2va_i8x.ckpt": {
        "name_en": "MiniMax H3 Reference-to-Video (I2V)",
        "name_ar": "تحريك الصور MiniMax H3 (فيديو من صورة مرجعية)",
        "type": "video_model",
        "category": "Image to Video",
        "category_ar": "تحريك الصور",
        "desc_en": "Transforms any static character or product photograph into a fluid, animated 5-second cinematic clip while preserving high character fidelity.",
        "desc_ar": "يحوّل أي صورة ثابتة لشخصية أو منتج إلى مقطع سينمائي متحرك مدته 5 ثوانٍ مع الحفاظ التام على ملامح وتفاصيل الصورة الأصلية.",
        "recommended_steps": 4,
        "recommended_guidance": 1.5,
        "recommended_resolution": "512x512",
        "hardware_fit": "16 GB RAM",
    },
    # Base Image Models
    "qwen_image_2.1_i8x.ckpt": {
        "name_en": "Qwen Image 2.1 (8-bit)",
        "name_ar": "محرك الصور Qwen 2.1 عالي الواقعية",
        "type": "image_model",
        "category": "Photorealistic Image",
        "category_ar": "صور واقعية وفوتوغرافية",
        "desc_en": "Top-tier photorealistic text-to-image model. Exceptional skin textures, natural lighting, and intricate compositional details.",
        "desc_ar": "من أقوى وأدق موديلات توليد الصور الواقعية على الإطلاق؛ دقة متناهية في تفاصيل الوجه والعيون والجلد والإضاءة الطبيعية.",
        "recommended_steps": 6,
        "recommended_guidance": 1.0,
        "recommended_lora": "qwen_image_2.16step_lora_f16.ckpt",
        "hardware_fit": "16 GB RAM",
    },
    "krea_2_turbo_i8x.ckpt": {
        "name_en": "Krea 2 Turbo (8-bit)",
        "name_ar": "محرك الصور Krea 2 Turbo فائق السرعة",
        "type": "image_model",
        "category": "Fast Diffusion",
        "category_ar": "توليد صور سريع وفني",
        "desc_en": "Ultra-fast diffusion model capable of generating high-resolution images in just 4 steps with rich artistic aesthetics and vibrant colors.",
        "desc_ar": "محرك توليد صور فائق السرعة في 4 خطوات فقط، يجمع بين الواقعية والأسلوب الفني المشرق والألوان الجذابة.",
        "recommended_steps": 4,
        "recommended_guidance": 1.0,
        "recommended_lora": "krea2_turbo_4step_lora_f16.ckpt",
        "hardware_fit": "16 GB RAM",
    },
    "flux_2_klein_4b_i8x.ckpt": {
        "name_en": "FLUX.2 Klein 4B",
        "name_ar": "محرك FLUX.2 كلاين 4B السريع",
        "type": "image_model",
        "category": "Lightweight Image",
        "category_ar": "توليد خفيف وسريع",
        "desc_en": "Compact 4B parameter model with impressive prompt adherence and quick generation times. Runs smoothly on any Mac.",
        "desc_ar": "نموذج 4B ذكي وخفيف جداً، دقيق في فهم الأوصاف والاستجابة للبرومبت وموفر للذاكرة.",
        "recommended_steps": 4,
        "recommended_guidance": 1.0,
        "hardware_fit": "8 GB+ RAM",
    },
    # Video LoRAs
    "minimax_h3_3step_lora_f16.ckpt": {
        "name_en": "MiniMax H3 3-Step Lightning Turbo",
        "name_ar": "لورا H3 تيربو الخارقة (3 خطوات فقط)",
        "type": "lora",
        "lane": "h3",
        "trigger": "h3_3steps",
        "recommended_weight": 1.0,
        "desc_en": "Distillation adapter that accelerates H3 video generation down to just 3 steps (~2 minutes per clip) without noticeable quality drop.",
        "desc_ar": "لورا تسريع مذهلة تقلص خطوات رندرة الفيديو إلى 3 خطوات فقط (~دقيقتين للمقطع الكامل) مع الحفاظ على نعومة وسلاسة الحركة.",
    },
    "minimax_h3_turbo_v4_lora_f16.ckpt": {
        "name_en": "MiniMax H3 Turbo v4 (High Quality)",
        "name_ar": "لورا H3 تيربو v4 عالية الجودة (4 خطوات)",
        "type": "lora",
        "lane": "h3",
        "trigger": "h3tturbo",
        "recommended_weight": 0.6,
        "desc_en": "Refined 4-step Turbo adapter providing higher temporal stability and richer visual fidelity for cinematic YouTube/short-form content.",
        "desc_ar": "إصدار v4 المطور لتسريع الفيديو في 4 خطوات مع ثبات أعلى في الأبعاد وتفاصيل أوضح تناسب مقاطع اليوتيوب والريلز.",
    },
    "h3_photoreal_lora_f16.ckpt": {
        "name_en": "MiniMax H3 Photorealism",
        "name_ar": "لورا الواقعية السينمائية لـ H3",
        "type": "lora",
        "lane": "h3",
        "trigger": "h3image",
        "recommended_weight": 0.8,
        "desc_en": "Enhances human skin realism, cinematic depth of field, and removes AI plastic artifacts from H3 generations.",
        "desc_ar": "تحسّن واقعية ملامح البشرة والإضاءة الطبيعية وتلغي المظهر البلاستيكي أو الاصطناعي من الفيديو.",
    },
    "relim_v2_lora_500__1__lora_f16.ckpt": {
        "name_en": "MiniMax H3 Relim v2 (Moody Atmosphere)",
        "name_ar": "لورا Relim v2 للأجواء السينمائية والدرامية",
        "type": "lora",
        "lane": "h3",
        "trigger": "h3relim",
        "recommended_weight": 0.7,
        "desc_en": "Creates moody, atmospheric, filmic lighting with rich shadows and color grading for cinematic story videos.",
        "desc_ar": "تضفي طابعاً سينمائياً درامياً بتدرجات إضاءة وظلال غنية وأجواء غامضة للأفلام والقصص.",
    },
    # Image LoRAs
    "qwen_image_2.16step_lora_f16.ckpt": {
        "name_en": "Qwen 2.1 6-Step Turbo",
        "name_ar": "لورا تسريع Qwen 2.1 (6 خطوات)",
        "type": "lora",
        "lane": "image",
        "trigger": "qwenturbo",
        "recommended_weight": 1.0,
        "desc_en": "Accelerates Qwen Image 2.1 from 30+ steps down to 6 steps while preserving high photorealism.",
        "desc_ar": "تسريع موديل Qwen 2.1 ليعمل بـ 6 خطوات فقط بدلاً من 30 خطوة مع بقاء نفس الدقة الفوتوغرافية العالية.",
    },
    "krea2_turbo_4step_lora_f16.ckpt": {
        "name_en": "Krea 2 Turbo 4-Step",
        "name_ar": "لورا تسريع Krea 2 (4 خطوات)",
        "type": "lora",
        "lane": "image",
        "trigger": "krea2rurbo",
        "recommended_weight": 1.0,
        "desc_en": "Accelerates Krea 2 to ultra-fast 4-step generation for instant creative iterations.",
        "desc_ar": "تسريع محرك Krea 2 ليولد صوراً فائقة الجودة في 4 خطوات فقط وخلال ثوانٍ معدودة.",
    },
    "krea2turbo_radiance_lora_f16.ckpt": {
        "name_en": "Krea 2 Radiance (Vivid Lighting)",
        "name_ar": "لورا Radiance للإضاءة الحيوية والـ HDR",
        "type": "lora",
        "lane": "image",
        "trigger": "kreaditals",
        "recommended_weight": 1.0,
        "desc_en": "Boosts dynamic contrast, radiant lighting highlights, and rich visual textures on Krea 2.",
        "desc_ar": "تعزز التباين اللوني، إشعاع الإضاءة، وبريق التفاصيل الحادة لإبراز الصور بشكل مبهر.",
    },
    "ideokrea_v1_lora_f16.ckpt": {
        "name_en": "IdeoKrea v1 (Typography & Poster Design)",
        "name_ar": "لورا IdeoKrea v1 للخطوط والبوسترات وتصميم الجرافيك",
        "type": "lora",
        "lane": "image",
        "trigger": "ideokrea",
        "recommended_weight": 1.0,
        "desc_en": "Optimized for clear typography, graphic layouts, logo placements, and poster aesthetics.",
        "desc_ar": "متخصصة في كتابة النصوص الإنجليزية وتصميم البوسترات، أغلفة المجلات، والشعارات بدقة متناهية.",
    },
    # Upscalers
    "realesrgan_x4plus_f16.ckpt": {
        "name_en": "RealESRGAN 4x Plus (Detail Upscaler)",
        "name_ar": "مكبر الدقة RealESRGAN 4x Plus",
        "type": "upscaler",
        "category": "Super Resolution",
        "category_ar": "مضاعفة الدقة وجودة الصور",
        "desc_en": "Upscales images 4x with sharp edge restoration and texture enhancement without blurring.",
        "desc_ar": "يكبر أبعاد وجودة الصور 4 أضعاف مع استعادة حدة الحواف وتوضيح الملامح بدقة عالية وبدون ضبابية.",
    },
    "realesrgan_x2plus_f16.ckpt": {
        "name_en": "RealESRGAN 2x Plus (Fast Upscaler)",
        "name_ar": "مكبر الدقة RealESRGAN 2x Plus",
        "type": "upscaler",
        "category": "Super Resolution",
        "category_ar": "مضاعفة الدقة وجودة الصور",
        "desc_en": "Fast 2x resolution upscaler ideal for quick refinement before final export.",
        "desc_ar": "تكبير سريع يضاعف أبعاد الصورة مرتين بجودة عالية قبل التصدير النهائي.",
    },
}


def _heuristic_synthesize(filename: str, size_bytes: int) -> Dict[str, Any]:
    """Synthesize smart bilingual documentation for any unknown .ckpt file."""
    name_clean = Path(filename).stem.replace("_f16", "").replace("_i8x", "").replace("_q8p", "").replace("_", " ").title()
    fn_lower = filename.lower()
    size_mb = round(size_bytes / (1024 * 1024), 1)

    is_lora = "lora" in fn_lower
    is_video = any(k in fn_lower for k in ("h3", "minimax", "wan", "svd", "ltx", "video"))
    is_upscaler = "esrgan" in fn_lower or "upscale" in fn_lower
    is_vae = "vae" in fn_lower

    if is_upscaler:
        return {
            "name_en": name_clean,
            "name_ar": f"مكبر الدقة {name_clean}",
            "type": "upscaler",
            "category": "Super Resolution",
            "category_ar": "تكبير وتوضيح الصور",
            "desc_en": f"Specialized AI upscaler ({size_mb} MB) for restoring details and scaling images.",
            "desc_ar": f"محرك تكبير ذكي بحجم ({size_mb} ميجابايت) لرفع دقة وجودة الصور وتوضيح تفاصيلها.",
            "size_mb": size_mb,
        }

    if is_vae:
        return {
            "name_en": f"{name_clean} (VAE)",
            "name_ar": f"مشفر بصري {name_clean} (VAE)",
            "type": "vae",
            "category": "Variational Autoencoder",
            "category_ar": "مشفر ومفكك صور وفيديو",
            "desc_en": f"Latent space autoencoder ({size_mb} MB) responsible for encoding and decoding pixel frames.",
            "desc_ar": f"نموذج تشفير وفك تشفير الصور والفيديو بحجم ({size_mb} ميجابايت).",
            "size_mb": size_mb,
        }

    if is_lora:
        # Heuristic trigger extraction: first word of name
        trigger = re.sub(r'[^a-zA-Z0-9]', '', filename.split("_")[0]).lower()
        lane = "h3" if is_video else "image"
        return {
            "name_en": name_clean,
            "name_ar": f"لورا {name_clean}",
            "type": "lora",
            "lane": lane,
            "trigger": trigger,
            "recommended_weight": 0.8,
            "desc_en": f"Custom LoRA adapter ({size_mb} MB). Fine-tuned style or acceleration module. Trigger keyword: '{trigger}'.",
            "desc_ar": f"إضافة لورا مخصصة بحجم ({size_mb} ميجابايت) لإضافة أسلوب خاص أو تسريع الأداء. الكلمة المفتاحية: '{trigger}'.",
            "size_mb": size_mb,
        }

    # Base Model
    cat = "Video Model" if is_video else "Image Model"
    cat_ar = "محرك فيديو" if is_video else "محرك صور"
    return {
        "name_en": name_clean,
        "name_ar": f"محرك {name_clean}",
        "type": "video_model" if is_video else "image_model",
        "category": cat,
        "category_ar": cat_ar,
        "desc_en": f"Full generative model ({size_mb} MB) discovered in Draw Things library.",
        "desc_ar": f"محرك ذكاء اصطناعي كامل بحجم ({size_mb} ميجابايت) تم اكتشافه وإضافته من مكتبة Draw Things.",
        "recommended_steps": 4 if "turbo" in fn_lower else 20,
        "recommended_guidance": 1.0,
        "size_mb": size_mb,
    }


def get_full_catalog() -> Dict[str, Any]:
    """Scan Draw Things directory and build full enriched bilingual catalog."""
    models: List[Dict[str, Any]] = []
    loras: List[Dict[str, Any]] = []
    presets: List[Dict[str, Any]] = []
    styles: List[Dict[str, Any]] = []
    scripts: List[Dict[str, Any]] = []

    # 1. Custom Triggers from custom_lora.json
    custom_lora_triggers: Dict[str, str] = {}
    custom_lora_file = DRAWTHINGS_MODELS_DIR / "custom_lora.json"
    if custom_lora_file.is_file():
        try:
            with open(custom_lora_file, "r") as f:
                c_data = json.load(f)
                for item in c_data:
                    fn = item.get("file")
                    prefix = (item.get("prefix") or "").strip()
                    if fn and prefix:
                        custom_lora_triggers[fn] = prefix
        except Exception:
            pass

    # 2. Scan all .ckpt files
    if DRAWTHINGS_MODELS_DIR.is_dir():
        for f in sorted(DRAWTHINGS_MODELS_DIR.glob("*.ckpt")):
            fn = f.name
            size_bytes = f.stat().st_size
            meta = KNOWN_CATALOG.get(fn)
            if not meta:
                meta = _heuristic_synthesize(fn, size_bytes)
            else:
                meta = dict(meta)

            meta["filename"] = fn
            meta["path"] = str(f)
            meta["size_mb"] = round(size_bytes / (1024 * 1024), 1)

            # Apply custom trigger if available
            if fn in custom_lora_triggers:
                meta["trigger"] = custom_lora_triggers[fn]

            if meta.get("type") == "lora":
                loras.append(meta)
            else:
                models.append(meta)

    # 3. Community / Custom Configs from custom_configs.json
    custom_configs_file = DRAWTHINGS_MODELS_DIR / "custom_configs.json"
    if custom_configs_file.is_file():
        try:
            with open(custom_configs_file, "r") as f:
                configs_data = json.load(f)
                for item in configs_data:
                    c_name = item.get("name", "Custom Preset")
                    cfg = item.get("configuration", {})
                    presets.append({
                        "name": c_name,
                        "name_en": f"{c_name} (Community Preset)",
                        "name_ar": f"إعداد جاهز: {c_name}",
                        "model": cfg.get("model", ""),
                        "steps": cfg.get("steps", 4),
                        "sampler": cfg.get("sampler", 17),
                        "shift": cfg.get("shift", 2),
                        "guidance": cfg.get("guidanceScale", 1.0),
                        "loras": cfg.get("loras", []),
                        "width": cfg.get("width", 512),
                        "height": cfg.get("height", 512),
                        "num_frames": cfg.get("numFrames", 17),
                        "fps": cfg.get("fps", 12),
                        "desc_en": f"Preconfigured community recipe for {cfg.get('model', 'generation')} with {cfg.get('steps', 4)} steps.",
                        "desc_ar": f"إعدادات مجتمعية مسبقة الضبط للمحرك {cfg.get('model', '')} بـ {cfg.get('steps', 4)} خطوات.",
                    })
        except Exception:
            pass

    # 4. Prompt Styles from custom_prompt_style.json
    styles_file = DRAWTHINGS_MODELS_DIR / "custom_prompt_style.json"
    if styles_file.is_file():
        try:
            with open(styles_file, "r") as f:
                styles_data = json.load(f)
                arabic_names = {
                    "cinematic-default": "سينمائي كلاسيكي",
                    "sai-cinematic": "سينمائي احترافي",
                    "sai-analog film": "فيلم تناظري 35mm",
                    "sai-anime": "أنيمي ورسوم يابانية",
                    "sai-comic book": "قصص مصورة (كوميكس)",
                    "sai-3d-model": "مجسم ثلاثي الأبعاد (Octane)",
                    "sai-digital art": "رسم رقمي وفني",
                    "sai-fantasy art": "فن الفانتازيا والأسطورة",
                    "sai-isometric": "منظور متساوي الأبعاد (Isometric)",
                    "sai-cyberpunk": "سايبربانك ومستقبلي",
                    "sai-origami": "فن الأوريغامي والورق",
                    "sai-pixel art": "فن البكسل (Pixel Art)",
                    "sai-enhance": "تحسين إضاءة وتفاصيل فائقة",
                }
                for s in styles_data:
                    s_name = s.get("name", "")
                    if s_name == "Empty":
                        continue
                    ar_name = arabic_names.get(s_name, s_name.replace("sai-", "").title())
                    styles.append({
                        "name": s_name,
                        "label_en": s_name.replace("sai-", "").title(),
                        "label_ar": ar_name,
                        "prompt": s.get("prompt", "[prompt]"),
                        "negative_prompt": s.get("negative_prompt", ""),
                    })
        except Exception:
            pass

    # 5. Scripts from Scripts/
    if DRAWTHINGS_SCRIPTS_DIR.is_dir():
        script_info = {
            "Qwen → MiniMax H3.js": {
                "name_en": "⚡ Auto-Animate (Qwen Vision ➔ MiniMax H3)",
                "name_ar": "⚡ التحريك التلقائي للصور (Qwen Vision ➔ MiniMax H3)",
                "desc_en": "Analyzes any static photo with Qwen Vision, crafts an action camera prompt, and animates it with MiniMax H3.",
                "desc_ar": "تحليل أي صورة بالذكاء الاصطناعي وصياغة برومبت حركة وتحريكها سينمائياً بضغطة زر واحدة.",
            },
            "Prompt Enhancer En.js": {
                "name_en": "Prompt Enhancer (Ernie / Z-Image / Anima)",
                "name_ar": "المحسّن الذكي للأوامر والبرومبت",
                "desc_en": "Expands simple keywords into rich cinematic prompts using intelligent prompt engineering rules.",
                "desc_ar": "تحويل الكلمات البسيطة إلى برومبت سينمائي مفصل فائق الجودة والواقعية.",
            },
            "Get Config Pro MinmaxH3.js": {
                "name_en": "Config Exporter Pro",
                "name_ar": "تصدير وضبط الإعدادات الاحترافية",
                "desc_en": "Extracts, formats and validates high-precision model generation parameters.",
                "desc_ar": "استخراج وتوحيد قيم ومحددات الرندرة للموديلات.",
            },
        }
        for s in sorted(DRAWTHINGS_SCRIPTS_DIR.glob("*.js")):
            info = script_info.get(s.name, {
                "name_en": s.stem.title(),
                "name_ar": f"سكربت {s.stem}",
                "desc_en": f"Automated Draw Things JS pipeline workflow: {s.name}",
                "desc_ar": f"سكربت أتمتة وتنفيذ مهام: {s.name}",
            })
            scripts.append({
                "filename": s.name,
                "path": str(s),
                "name_en": info["name_en"],
                "name_ar": info["name_ar"],
                "desc_en": info["desc_en"],
                "desc_ar": info["desc_ar"],
            })

    return {
        "models": models,
        "loras": loras,
        "presets": presets,
        "styles": styles,
        "scripts": scripts,
        "total_models": len(models),
        "total_loras": len(loras),
        "total_presets": len(presets),
        "total_styles": len(styles),
        "total_scripts": len(scripts),
    }
