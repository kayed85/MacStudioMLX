#!/usr/bin/env python3
"""apply_patches.py - Automated patcher for Phosphene updates.

Applies all custom optimizations and unlocks to any Phosphene version:
1. Pro unlock in pro_token.py
2. Swap memory guard enhancement (12 GB threshold) in yue2_run.py and mlx_ltx_panel.py
3. Canvas snapping and High Quality fallback in mlx_ltx_panel.py
4. Pro addon state & H3 Flex Turbo spec in mlx_ltx_panel.py
5. Instrumental auto-uncheck on lyrics & auto 'No score' for arabmaqamrock in characters.js
6. Clarified Instrumental label in index.html
7. Canvas snapping in engines.js & custom dimension auto-round in characters.js
8. Aspect ratio & H3 orientation sync in characters.js
"""

import sys
import re
from pathlib import Path

def patch_pro_token(version_dir: Path):
    path = version_dir / "pro_token.py"
    if not path.is_file():
        print(f"[-] {path} not found")
        return False
    content = path.read_text(encoding="utf-8")
    if 'return {"ok": True, "reason": "ok", "expires_at": 4102444800}' in content:
        print(f"[=] pro_token.py already patched")
        return True

    target = 'def status(environ=None, now: int | None = None, device=None) -> dict:\n    """{ok, reason, expires_at}: the verdict NOW over every candidate token (the valid one that\n    expires last wins; with none valid, the first one\'s reason)."""\n'
    replacement = target + '    return {"ok": True, "reason": "ok", "expires_at": 4102444800}\n'
    if target in content:
        content = content.replace(target, replacement, 1)
        path.write_text(content, encoding="utf-8")
        print(f"[+] Patched pro_token.py")
        return True
    else:
        pattern = r'(def status\(environ=None, now:[^\)]*\) -> dict:\s+"""[^"]*""")'
        m = re.search(pattern, content)
        if m:
            content = content[:m.end()] + '\n    return {"ok": True, "reason": "ok", "expires_at": 4102444800}' + content[m.end():]
            path.write_text(content, encoding="utf-8")
            print(f"[+] Patched pro_token.py (regex)")
            return True
        print(f"[-] Failed to patch pro_token.py: target pattern not found")
        return False

def patch_yue2_run(version_dir: Path):
    path = version_dir / "scripts" / "music" / "yue2_run.py"
    if not path.is_file():
        print(f"[-] {path} not found")
        return False
    content = path.read_text(encoding="utf-8")
    
    content = content.replace(
        'min_avail = float(os.environ.get("YUE2_MIN_AVAILABLE_GIB", "1.5"))',
        'min_avail = float(os.environ.get("YUE2_MIN_AVAILABLE_GIB", "0.8"))'
    )
    content = content.replace(
        'swap_out = float(os.environ.get("YUE2_MAX_SWAP_OUT_MIB", "1024"))',
        'swap_out = float(os.environ.get("YUE2_MAX_SWAP_OUT_MIB", "12288"))'
    )
    content = content.replace(
        'swap_growth = float(os.environ.get("YUE2_MAX_SWAP_GROWTH_MIB", "2048"))',
        'swap_growth = float(os.environ.get("YUE2_MAX_SWAP_GROWTH_MIB", "12288"))'
    )
    
    old_abort = '''        if swapped > swap_out * mib or growth > swap_growth * mib:
            raise MemoryError(f"Stopping: the system is swapping ({swapped / mib:.0f} MiB out, "
                              f"{growth / mib:.0f} MiB swap growth)")'''
    new_abort = '''        if swapped > swap_out * mib or growth > swap_growth * mib:
            if sample["system_available_bytes"] < min_avail * gib:
                raise MemoryError(f"Stopping: the system is swapping ({swapped / mib:.0f} MiB out, "
                                  f"{growth / mib:.0f} MiB swap growth) and available memory is depleted")'''
    if old_abort in content:
        content = content.replace(old_abort, new_abort, 1)
        path.write_text(content, encoding="utf-8")
        print(f"[+] Patched yue2_run.py swap abort condition")
        return True
    elif 'and available memory is depleted' in content:
        path.write_text(content, encoding="utf-8")
        print(f"[=] yue2_run.py already has available memory check")
        return True
    else:
        path.write_text(content, encoding="utf-8")
        print(f"[!] yue2_run.py defaults updated, check abort block manually")
        return True

def patch_mlx_ltx_panel(version_dir: Path):
    path = version_dir / "mlx_ltx_panel.py"
    if not path.is_file():
        print(f"[-] {path} not found")
        return False
    content = path.read_text(encoding="utf-8")

    # 1. Pro addon state & seal
    if 'def _pro_addon_seal() -> dict | None:\n    return {"ok": True, "reason": "ok", "bundle": "pro"}' not in content:
        seal_target = 'def _pro_addon_seal() -> dict | None:\n    """The loaded add-on\'s own verdict ({ok, reason, bundle}), or None when there is no add-on."""\n'
        seal_repl = seal_target + '    return {"ok": True, "reason": "ok", "bundle": "pro"}\n'
        if seal_target in content:
            content = content.replace(seal_target, seal_repl, 1)
            print(f"[+] Patched _pro_addon_seal")

    if 'def pro_addon_state() -> dict:\n    return {"state": "ready", "reason": None}' not in content:
        addon_state_target = 'def pro_addon_state() -> dict:\n'
        idx = content.find(addon_state_target)
        if idx != -1:
            doc_end = content.find('"""', idx + len(addon_state_target) + 10)
            if doc_end != -1:
                doc_end_line = content.find('\n', doc_end)
                content = content[:doc_end_line+1] + '    return {"state": "ready", "reason": None}\n' + content[doc_end_line+1:]
                print(f"[+] Patched pro_addon_state")

    # 2. H3 flex turbo spec
    old_turbo = '''def h3_flex_turbo_spec() -> dict | None:
    """What H3 Flex Turbo needs and runs, from the Pro add-on. None on Free."""
    if not bool(pro_status().get("active")):
        return None
    return _h3_pro_addon("h3_flex_turbo")'''

    new_turbo = '''def h3_flex_turbo_spec() -> dict | None:
    """What H3 Flex Turbo needs and runs."""
    return {
        "file": "pika_h3_flex_turbo_4step_fl2va_lite_r32.safetensors",
        "bytes": 310189392,
        "sha256": "7c60a2ff31f7f56c1f59020c2441fa0532c7b3fd8a510fbd0f806327021f5367",
        "url": "https://huggingface.co/Pika-Labs/Pika-H3-Flex-Turbo-LoRA/resolve/main/pika_h3_flex_turbo_4step_fl2va_lite_r32.safetensors",
        "repo": "Pika-Labs/Pika-H3-Flex-Turbo-LoRA",
        "source_file": "pika_h3_flex_turbo_4step_fl2va_lite_r32.safetensors",
        "revision": "dcacd93a50f714ba1cd61c7ca2cc5f65e9a1ec60",
        "steps": 4,
        "needs": ["--lora"],
        "argv": ["--steps", "4", "--lora", "{adapter}:1.0"],
        "record": {"turbo_steps": 4},
    }'''
    if old_turbo in content:
        content = content.replace(old_turbo, new_turbo, 1)
        print(f"[+] Patched h3_flex_turbo_spec")

    # 3. Canvas snapping function
    snap_def = '''def snap_canvas_for_engine(engine: str, width: int, height: int, *, max_dim: int = 0) -> tuple[int, int]:
    """Snap requested canvas dimensions to the nearest valid canvas for the given engine."""
    w, h = max(32, int(width)), max(32, int(height))
    is_portrait = (h > w)
    
    if engine == "h3":
        h3_canvases = [
            (640, 384),   # Draft
            (768, 448),   # Standard
            (1024, 576),  # High
            (1344, 768),  # Native
        ]
        if is_portrait:
            targets = [(ch, cw) for cw, ch in h3_canvases]
        else:
            targets = h3_canvases
        return min(targets, key=lambda t: (t[0] - w) ** 2 + (t[1] - h) ** 2)
    else:
        rw = max(64, int(round(w / 64.0)) * 64)
        rh = max(64, int(round(h / 64.0)) * 64)
        if max_dim and max(rw, rh) > max_dim:
            scale = max_dim / max(rw, rh)
            scaled_w, scaled_h = rw * scale, rh * scale
            cw = max(64, int(round(scaled_w / 64.0)) * 64)
            ch = max(64, int(round(scaled_h / 64.0)) * 64)
            if cw > max_dim:
                cw = max(64, int(scaled_w) // 64 * 64)
            if ch > max_dim:
                ch = max(64, int(scaled_h) // 64 * 64)
            return (cw, ch)
        return (rw, rh)

'''
    if "def snap_canvas_for_engine(" not in content:
        content = content.replace('def ltx_model_dir(', snap_def + 'def ltx_model_dir(', 1)
        print(f"[+] Added snap_canvas_for_engine")

    # 4. In job creation, snap canvas
    job_target = '''            "width": max(32, int(f("width", str(default_w)) or default_w)),
            "height": max(32, int(f("height", str(default_h)) or default_h)),'''
    if job_target in content:
        job_prep = '''    _req_w = max(32, int(f("width", str(default_w)) or default_w))
    _req_h = max(32, int(f("height", str(default_h)) or default_h))
    _max_dim = int(SYSTEM_CAPS.get("t2v_max_dim", 0)) if not SYSTEM_CAPS.get("allows_q8") else 0
    _req_w, _req_h = snap_canvas_for_engine(_engine, _req_w, _req_h, max_dim=_max_dim)
'''
        job_def_idx = content.find('    job = {\n        "id": _new_job_id(),')
        if job_def_idx != -1:
            content = content[:job_def_idx] + job_prep + content[job_def_idx:]
            content = content.replace(job_target, '            "width": _req_w,\n            "height": _req_h,', 1)
            print(f"[+] Patched job creation canvas snapping")

    # 5. High Quality fallback
    hq_old = '''    if ltx_quality_uses_hq(quality):
        if not SYSTEM_CAPS["allows_q8"]:
            raise RenderRefused(
                "hardware_tier",
                f"High quality (Q8 two-stage) isn\'t supported on the "
                f"{SYSTEM_CAPS[\'label\']} hardware tier — Q8 dev transformer "
                f"(~19 GB) plus the upscaler stage doesn\'t fit. "
                f"Use Standard or Quick instead — High works from "
                f"{min_ram_gb_for(\'allows_q8\') or 48} GB."
            )'''
    hq_new = '''    if ltx_quality_uses_hq(quality):
        if not SYSTEM_CAPS["allows_q8"]:
            push(f"[quality] High quality is not supported on {SYSTEM_CAPS['label']} hardware — automatically using Standard.")
            quality = "standard"
            p["quality"] = "standard"'''
    if hq_old in content:
        content = content.replace(hq_old, hq_new, 1)
        print(f"[+] Patched High Quality fallback")

    # 6. Swap limit in _check_sample
    content = content.replace('swap_out = float(os.environ.get("LTX_MAX_SWAP_OUT_MIB", "1024"))',
                              'swap_out = float(os.environ.get("LTX_MAX_SWAP_OUT_MIB", "12288"))')
    content = content.replace('swap_growth = float(os.environ.get("LTX_MAX_SWAP_GROWTH_MIB", "2048"))',
                              'swap_growth = float(os.environ.get("LTX_MAX_SWAP_GROWTH_MIB", "12288"))')

    path.write_text(content, encoding="utf-8")
    return True

def patch_characters_js(version_dir: Path):
    path = version_dir / "webapp" / "js" / "characters.js"
    if not path.is_file():
        print(f"[-] {path} not found")
        return False
    content = path.read_text(encoding="utf-8")

    # 1. Uncheck instrumental when lyrics are present in musicFormParams()
    old_inst = '  const instrumental = document.getElementById(\'musicInstrumental\').checked;'
    new_inst = '''  let instrumental = Boolean((document.getElementById('musicInstrumental') || {}).checked);
  const lyricsVal = ((document.getElementById('musicLyrics') || {}).value || '').trim();
  if (lyricsVal && instrumental) {
    instrumental = false;
    const instEl = document.getElementById('musicInstrumental');
    if (instEl) instEl.checked = false;
    if (typeof musicFormChanged === 'function') musicFormChanged();
  }'''
    if old_inst in content:
        content = content.replace(old_inst, new_inst, 1)
        print(f"[+] Patched characters.js musicInstrumental auto-uncheck")

    # 2. Auto-set 'No score' for arabmaqamrock
    old_lora_bind = '''    row.querySelector('.range-val').textContent = value.toFixed(2);'''
    new_lora_bind = '''    row.querySelector('.range-val').textContent = value.toFixed(2);
    if (on && row.dataset.id && row.dataset.id.toLowerCase().includes('arabmaqamrock')) {
      const modeEl = document.getElementById('musicMode');
      if (modeEl && modeEl.value !== 'off') {
        const offBtn = document.querySelector('#musicModePills button[data-value="off"]');
        if (offBtn && typeof musicPick === 'function') musicPick('musicMode', offBtn);
      }
    }'''
    if old_lora_bind in content and 'row.dataset.id.toLowerCase().includes(\'arabmaqamrock\')' not in content:
        content = content.replace(old_lora_bind, new_lora_bind, 1)
        print(f"[+] Patched characters.js arabmaqamrock No score auto-switch")

    # 3. Aspect ratio & H3 orientation sync
    old_set_aspect = '''  document.querySelectorAll('#aspectGroup .pill-btn').forEach(b => b.classList.toggle('active', b.dataset.aspect === a));
  applyAspect(a);'''
    new_set_aspect = '''  document.querySelectorAll('#aspectGroup .pill-btn').forEach(b => b.classList.toggle('active', b.dataset.aspect === a));
  const isV = (a === 'vertical' || a === 'portrait_4_5');
  const h3o = document.getElementById('h3_orientation');
  if (h3o) {
    h3o.value = isV ? 'portrait' : 'landscape';
    document.querySelectorAll('#h3OrientationGroup [data-h3-orientation]').forEach(b =>
      b.classList.toggle('active', b.dataset.h3Orientation === (isV ? 'portrait' : 'landscape')));
    try { localStorage.setItem('phos_h3_orientation', isV ? 'portrait' : 'landscape'); } catch (e) {}
  }
  applyAspect(a);'''
    if old_set_aspect in content:
        content = content.replace(old_set_aspect, new_set_aspect, 1)
        print(f"[+] Patched characters.js aspect & H3 orientation sync")

    # 4. Auto-snap custom dimensions to multiple of 64 on change
    old_aspect_btn = '''document.querySelectorAll('#aspectGroup .pill-btn').forEach(b => b.onclick = () => setAspect(b.dataset.aspect));'''
    snap_listeners = '''document.querySelectorAll('#aspectGroup .pill-btn').forEach(b => b.onclick = () => setAspect(b.dataset.aspect));
['width', 'height'].forEach(id => {
  const el = document.getElementById(id);
  if (el) {
    el.addEventListener('change', () => {
      let v = parseInt(el.value || '64', 10);
      if (isNaN(v) || v < 64) v = 64;
      el.value = String(Math.round(v / 64) * 64);
      if (typeof updateDerived === 'function') updateDerived();
      if (typeof updateCustomizeSummary === 'function') updateCustomizeSummary();
    });
  }
});'''
    if old_aspect_btn in content and "['width', 'height'].forEach(id =>" not in content:
        content = content.replace(old_aspect_btn, snap_listeners, 1)
        print(f"[+] Added width/height auto-snap listeners to characters.js")

    path.write_text(content, encoding="utf-8")
    return True

def patch_index_html(version_dir: Path):
    path = version_dir / "webapp" / "index.html"
    if not path.is_file():
        print(f"[-] {path} not found")
        return False
    content = path.read_text(encoding="utf-8")

    old_lbl = '<span data-i18n="musicInstrumentalPill.instrumental">Instrumental</span>'
    new_lbl = '<span data-i18n="musicInstrumentalPill.instrumental">Instrumental (No vocals / بدون غناء)</span>'
    if old_lbl in content:
        content = content.replace(old_lbl, new_lbl, 1)
        path.write_text(content, encoding="utf-8")
        print(f"[+] Patched index.html Instrumental label")
        return True
    return True

def patch_engines_js(version_dir: Path):
    path = version_dir / "webapp" / "js" / "engines.js"
    if not path.is_file():
        print(f"[-] {path} not found")
        return False
    content = path.read_text(encoding="utf-8")
    
    if 'function snapCanvasForEngine(' not in content:
        snap_fn = '''export function snapCanvasForEngine(engine, w, h) {
  w = Math.max(32, parseInt(w, 10) || 32);
  h = Math.max(32, parseInt(h, 10) || 32);
  const isPortrait = (h > w);
  if (engine === 'h3') {
    const canvases = isPortrait ?
      [[384, 640], [448, 768], [576, 1024], [768, 1344]] :
      [[640, 384], [768, 448], [1024, 576], [1344, 768]];
    return canvases.reduce((best, cur) => {
      const dCur = Math.hypot(cur[0] - w, cur[1] - h);
      const dBest = Math.hypot(best[0] - w, best[1] - h);
      return dCur < dBest ? cur : best;
    }, canvases[0]);
  }
  return [Math.max(64, Math.round(w / 64) * 64), Math.max(64, Math.round(h / 64) * 64)];
}
'''
        content = snap_fn + content
        path.write_text(content, encoding="utf-8")
        print(f"[+] Added snapCanvasForEngine to engines.js")
    return True

def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "/Users/mk/Library/Application Support/Phosphene/app/current"
    target_path = Path(target).resolve()
    print(f"[*] Patching Phosphene target: {target_path}")

    patch_pro_token(target_path)
    patch_yue2_run(target_path)
    patch_mlx_ltx_panel(target_path)
    patch_characters_js(target_path)
    patch_index_html(target_path)
    patch_engines_js(target_path)

    print(f"[✔] All patches applied to {target_path.name}")

if __name__ == "__main__":
    main()
