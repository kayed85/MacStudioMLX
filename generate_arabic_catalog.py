#!/usr/bin/env python3
"""generate_arabic_catalog.py - High-speed parallel batch Arabic translation for Phosphene.
"""

import concurrent.futures
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path("/Users/mk/Library/Application Support/Phosphene/app/v5.0.73")
STUDIO_DIR = ROOT / "i18n" / "studio"
EN_PATH = STUDIO_DIR / "en.json"
AR_PATH = STUDIO_DIR / "ar.json"
MLX_AR_PATH = Path("/Users/mk/MacStudioMLX/i18n/studio/ar.json")

PROTECTED_TERMS = [
    "Phosphene Pro", "Phosphene", "Pro", "Free", "Studio", "Remote",
    "LTX 2.5", "LTX 2.3", "LTX", "MiniMax H3", "MiniMax", "H3", "Hailuo H3", "Hailuo",
    "MiniMax-H3-Image", "FLUX 3", "FLUX 2", "FLUX", "Qwen", "Gemma", "Ideogram", "YuE2", "YuE",
    "CivitAI", "Hugging Face", "LoRA", "Pinokio", "Q4", "Q6", "Q8", "MLX",
    "Apple Silicon", "macOS", "Mac Studio", "Mac", "iPhone", "GitHub", "Discord"
]

CORE_GLOSSARY = {
    "Outputs": "معرض الأعمال",
    "outputs": "أعمالي",
    "Queue": "قائمة الانتظار",
    "queue": "قائمة الانتظار",
    "Render": "توليد",
    "render": "توليد",
    "Draft": "مسودة سريعة",
    "draft": "مسودة",
    "Extend": "تمديد المقطع",
    "extend": "تمديد",
    "Seed": "رقم البذرة (Seed)",
    "seed": "بذرة",
    "LoRA": "LoRA",
    "Character": "شخصية",
    "character": "شخصية",
    "Storyboard": "لوحة القصة (ستوري بورد)",
    "storyboard": "ستوري بورد",
    "Upscale": "رفع الدقة",
    "upscale": "رفع الدقة",
    "Settings": "الإعدادات",
    "settings": "الإعدادات",
    "Language": "اللغة",
    "Appearance": "المظهر",
    "Dark": "داكن",
    "Light": "فاتح",
    "System": "تلقائي حسب النظام",
    "Enhance": "تحسين الوصف",
    "Prompt": "الوصف",
    "prompt": "الوصف",
    "Negative prompt": "الكلمات المستبعدة",
    "Generate": "توليد",
    "generate": "توليد",
    "Avoid": "تجنب",
    "Lip-sync": "مزامنة الشفاه",
    "One Shot": "لقطة متصلة (One Shot)",
    "Audio": "الصوتيات والموسيقى",
    "Images": "الصور",
    "Video": "الفيديو",
    "Editor": "المحرر",
    "Train Character": "تدريب شخصية",
    "Remote Control": "التحكم عن بُعد",
    "Now": "الآن",
    "Recent": "الأحدث",
    "Logs": "السجلات",
    "Cancel": "إلغاء",
    "Save": "حفظ",
    "Delete": "حذف",
    "Clear": "مسح",
    "Done": "تم",
    "Download": "تنزيل",
    "Export": "تصدير",
    "Aspect": "الأبعاد",
    "Quality": "الجودة",
    "Frames": "الإطارات",
    "Steps": "الخطوات",
    "Guidance": "التوجيه",
    "Instrumental": "بدون غناء (موسيقى فقط)",
    "Lyrics": "كلمات الأغنية",
    "Style": "الأسلوب / الستايل",
    "Title": "العنوان",
    "Duration": "المدة",
    "Seconds": "ثوانٍ",
    "Minutes": "دقائق",
}

def translate_http(text: str) -> str:
    url = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=ar&dt=t&q=" + urllib.parse.quote(text)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return "".join(part[0] for part in data[0] if part[0])
        except Exception:
            time.sleep(0.5 * (attempt + 1))
    return text

def mask_text(text: str) -> tuple[str, list[str]]:
    placeholders = re.findall(r"(\{[A-Za-z_][A-Za-z0-9_]*\}|</?\d+/?>)", text)
    masked = text
    for i, p in enumerate(placeholders):
        masked = masked.replace(p, f" §P{i}§ ", 1)
    return masked, placeholders

def unmask_text(text: str, placeholders: list[str]) -> str:
    res = text
    for i, p in enumerate(placeholders):
        pattern = re.compile(rf"§\s*P{i}\s*§", re.IGNORECASE)
        res = pattern.sub(p, res)
    # Ensure any dropped placeholder is re-appended
    for p in placeholders:
        if p not in res:
            res += " " + p
    return res

def translate_single(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        return text
    clean = text.strip()
    if clean in CORE_GLOSSARY:
        return CORE_GLOSSARY[clean]
    masked, placeholders = mask_text(text)
    tr = translate_http(masked)
    return unmask_text(tr, placeholders)

DELIM = " §D§ "

def translate_batch(texts: list[str]) -> list[str]:
    """Translate a batch of strings joined by DELIM."""
    masked_list = []
    ph_list = []
    for t in texts:
        clean = t.strip()
        if clean in CORE_GLOSSARY:
            masked_list.append(CORE_GLOSSARY[clean])
            ph_list.append([])
        else:
            m, p = mask_text(t)
            masked_list.append(m)
            ph_list.append(p)

    to_translate_idx = [i for i, t in enumerate(texts) if t.strip() not in CORE_GLOSSARY]
    if not to_translate_idx:
        return masked_list

    joined_text = DELIM.join(masked_list[i] for i in to_translate_idx)
    raw_tr = translate_http(joined_text)
    
    # Split by delimiter
    parts = re.split(r"§\s*D\s*§", raw_tr)
    if len(parts) == len(to_translate_idx):
        for idx, sub_tr in zip(to_translate_idx, parts):
            masked_list[idx] = unmask_text(sub_tr.strip(), ph_list[idx])
        return masked_list
    else:
        # Fallback to single translate if delimiter split mismatched
        for idx in to_translate_idx:
            masked_list[idx] = translate_single(texts[idx])
        return masked_list

def translate_plural(val: dict) -> dict:
    base_en = val.get("other") or val.get("one") or ""
    tr = translate_single(base_en)
    return {
        "zero": tr, "one": tr, "two": tr,
        "few": tr, "many": tr, "other": tr
    }

def main():
    print(f"[*] Loading source English catalog from {EN_PATH}...")
    with open(EN_PATH, "r", encoding="utf-8") as f:
        en_catalog = json.load(f)["strings"]
    print(f"[*] Total keys in en.json: {len(en_catalog)}")

    ar_data = {"meta": {"lang": "ar", "name": "العربية", "dir": "rtl"}, "strings": {}, "source": {}}
    if AR_PATH.is_file():
        try:
            with open(AR_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded.get("strings"), dict):
                    ar_data["strings"] = loaded["strings"]
        except Exception:
            pass
    print(f"[*] Already cached: {len(ar_data['strings'])} / {len(en_catalog)}")

    # Remaining keys
    pending_keys = [k for k in en_catalog if k not in ar_data["strings"] or not ar_data["strings"][k]]
    print(f"[*] Keys to translate: {len(pending_keys)}")

    # Separate plurals and regular strings
    plural_keys = [k for k in pending_keys if isinstance(en_catalog[k].get("text"), dict)]
    regular_keys = [k for k in pending_keys if isinstance(en_catalog[k].get("text"), str)]

    # Handle plurals
    for k in plural_keys:
        ar_data["strings"][k] = translate_plural(en_catalog[k]["text"])

    BATCH_SIZE = 25
    chunks = [regular_keys[i:i + BATCH_SIZE] for i in range(0, len(regular_keys), BATCH_SIZE)]
    print(f"[*] Total batches: {len(chunks)} (batch size: {BATCH_SIZE})")

    lock = threading.Lock()
    done_batches = [0]
    start_time = time.time()

    def process_chunk(chunk):
        texts = [en_catalog[k]["text"] for k in chunk]
        translated = translate_batch(texts)
        with lock:
            for k, tr in zip(chunk, translated):
                ar_data["strings"][k] = tr
            done_batches[0] += 1
            if done_batches[0] % 5 == 0 or done_batches[0] == len(chunks):
                # Save checkpoint
                with open(AR_PATH, "w", encoding="utf-8") as f:
                    json.dump(ar_data, f, ensure_ascii=False, indent=1)
                elapsed = time.time() - start_time
                done_keys = len(ar_data["strings"])
                rate = done_keys / max(elapsed, 0.1)
                rem = (len(en_catalog) - done_keys) / max(rate, 0.1)
                print(f"[{done_keys}/{len(en_catalog)}] Saved checkpoint ({done_batches[0]}/{len(chunks)} batches). Rate: {rate:.1f} k/s. Est remaining: {rem/60:.1f}m")

    # Run with 4 concurrent worker threads
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(process_chunk, ch) for ch in chunks]
        concurrent.futures.wait(futures)

    # Final write to both locations
    with open(AR_PATH, "w", encoding="utf-8") as f:
        json.dump(ar_data, f, ensure_ascii=False, indent=1)
    
    MLX_AR_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MLX_AR_PATH, "w", encoding="utf-8") as f:
        json.dump(ar_data, f, ensure_ascii=False, indent=1)

    print(f"[✔] Translation complete! Total strings in ar.json: {len(ar_data['strings'])}")

if __name__ == "__main__":
    main()
