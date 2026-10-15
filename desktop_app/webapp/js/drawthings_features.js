// Draw Things Integration Features: Dynamic Catalog, Bilingual Info Modals, Styles & Auto-Animate

globalThis._dtCatalog = null;

async function loadDrawThingsCatalog() {
  if (globalThis._dtCatalog) return globalThis._dtCatalog;
  try {
    const res = await fetch('/drawthings/catalog');
    if (res.ok) {
      globalThis._dtCatalog = await res.json();
    }
  } catch (e) {
    console.error('Failed to load Draw Things catalog:', e);
  }
  return globalThis._dtCatalog || { models: [], loras: [], presets: [], styles: [], scripts: [] };
}

// Populate the prompt styles dropdown
async function populatePromptStyles() {
  const sel = document.getElementById('dtPromptStylesSelect');
  if (!sel) return;
  const cat = await loadDrawThingsCatalog();
  const styles = cat.styles || [];
  if (!styles.length) return;

  sel.innerHTML = '<option value="">🎨 Style / النمط...</option>';
  styles.forEach(s => {
    const opt = document.createElement('option');
    opt.value = s.name;
    opt.textContent = `${s.label_ar || s.label_en} (${s.label_en})`;
    sel.appendChild(opt);
  });
}

// Insert trigger word into active prompt
function insertPromptTrigger(trigger) {
  if (!trigger || trigger === 'None' || trigger === 'auto-applied') return;
  const surface = (typeof _currentSurface === 'function') ? _currentSurface() : 'video';
  const promptEl = (surface === 'image') ? document.getElementById('imgStudioPrompt') : document.getElementById('prompt');
  if (promptEl) {
    const cur = promptEl.value.trim();
    if (!cur.includes(trigger)) {
      promptEl.value = `${trigger}, ${cur}`;
      if (typeof phosToast === 'function') phosToast(`Trigger '${trigger}' inserted into prompt`);
    } else {
      if (typeof phosToast === 'function') phosToast(`Trigger '${trigger}' is already in prompt`);
    }
    promptEl.focus();
  }
}

// Show bilingual info modal for any engine (Video or Image)
async function showEngineInfoModal(engineName) {
  const cat = await loadDrawThingsCatalog();
  const surface = (typeof _currentSurface === 'function') ? _currentSurface() : 'video';
  
  let targetId = engineName;
  if (!targetId) {
    if (surface === 'image') {
      const sel = document.getElementById('imgStudioEngine');
      targetId = sel ? sel.value : 'auto';
    } else {
      targetId = (typeof currentEngine === 'function') ? currentEngine() : 'ltx';
    }
  }

  // Builtin engine definitions for high fidelity
  const builtinEngines = {
    'ltx': {
      name_en: 'Lightricks LTX-Video 2.5 (Audio-Visual Diffusion)',
      name_ar: 'محرك الفيديو LTX-Video 2.5 (توليد سينمائي مع صوت متزامن)',
      category: 'Cinematic Video Generation',
      category_ar: 'توليد فيديو سينمائي متقدم',
      desc_en: 'State-of-the-art open audio-visual video diffusion model by Lightricks. Native 24 fps, synchronized speech and ambient sound, text-to-video and image-to-video.',
      desc_ar: 'أحدث محرك فيديو سينمائي مفتوح المصدر من Lightricks؛ يولد فيديو بدقة عالية بمعدل 24 إطار/ثانية مع دمج الأصوات والحوارات المتزامنة تلقائياً.',
      recommended_steps: 30,
      recommended_guidance: 3.0,
      size_mb: '14,200 MB',
      hardware_fit: '16 GB+ RAM (M-series Pro/Max)',
    },
    'h3': {
      name_en: 'Hailuo MiniMax H3 (Turbo DiT)',
      name_ar: 'محرك الفيديو Hailuo MiniMax H3 (تيربو سينمائي)',
      category: 'Turbo Video Diffusion',
      category_ar: 'توليد فيديو سينمائي فائق السرعة',
      desc_en: 'High-motion cinematic video diffusion model with synchronized audio. 3-step Turbo LoRA allows fast 2-minute renders directly on Apple Silicon.',
      desc_ar: 'محرك فيديو سينمائي فائق السرعة يتميز بحركات واقعية وفيزيائية معقدة وصوت متزامن. بفضل لورا التيربو (3 خطوات) يولد المشاهد خلال دقيقتين فقط.',
      recommended_steps: 3,
      recommended_guidance: 1.0,
      size_mb: '12,800 MB',
      hardware_fit: '16 GB RAM',
    },
    'auto': {
      name_en: 'Auto &mdash; FLUX.2 Klein 4B',
      name_ar: 'المحرك التلقائي &mdash; FLUX.2 Klein 4B',
      category: 'Smart Auto Selection',
      category_ar: 'اختيار تلقائي ذكي',
      desc_en: 'Automatically selects the best-fit model for your Mac. Fast 4-step generation with rich detail and low memory footprint.',
      desc_ar: 'يختار تلقائياً المحرك الأنسب والأخف لجهاز الماك الخاص بك، مع سرعة فائقة في 4 خطوات وجودة تصويرية عالية.',
      recommended_steps: 4,
      recommended_guidance: 1.0,
      size_mb: '4,300 MB',
      hardware_fit: 'Fits all Macs (8GB+ RAM)',
    }
  };

  // Find in builtins, catalog models, or fallback
  let model = builtinEngines[targetId] || null;
  if (!model) {
    model = (cat.models || []).find(m => m.filename.includes(targetId.replace('dt_', '').split('_')[0]))
      || (cat.models || []).find(m => m.filename.includes('qwen') && targetId.includes('qwen'))
      || (cat.models || []).find(m => m.filename.includes('krea') && targetId.includes('krea'))
      || (cat.models || []).find(m => m.filename.includes('flux') && targetId.includes('flux'))
      || null;
  }

  const titleEn = model ? model.name_en : targetId.toUpperCase();
  const titleAr = model ? model.name_ar : `محرك ${targetId}`;
  const descEn = model ? model.desc_en : 'Fast local inference engine running directly on Apple Silicon MLX & Draw Things.';
  const descAr = model ? model.desc_ar : 'محرك معالجة محلي عالي السرعة يعمل مباشرة على شريحة Apple Silicon و Draw Things بدون استهلاك عالي للذاكرة.';
  const steps = model ? (model.recommended_steps || 4) : 4;
  const guidance = model ? (model.recommended_guidance || 1.0) : 1.0;
  const sizeMb = model ? (model.size_mb ? `${model.size_mb}` : 'Local') : 'Local';
  const hardware = model ? (model.hardware_fit || '16 GB RAM') : '16 GB RAM';

  const htmlBody = `
    <div style="font-family:system-ui,-apple-system,sans-serif; text-align:left;">
      <div dir="rtl" style="text-align:right; margin-bottom:14px; background:rgba(99,102,241,0.08); padding:12px; border-radius:8px; border-right:3px solid #6366f1;">
        <div style="font-weight:700; font-size:14px; color:#e2e8f0; margin-bottom:4px;">${titleAr}</div>
        <div style="font-size:13px; color:#cbd5e1; line-height:1.6;">${descAr}</div>
      </div>

      <div dir="ltr" style="margin-bottom:14px; background:rgba(30,41,59,0.5); padding:12px; border-radius:8px; border-left:3px solid #38bdf8;">
        <div style="font-weight:700; font-size:13px; color:#f8fafc; margin-bottom:4px;">${titleEn}</div>
        <div style="font-size:12px; color:#94a3b8; line-height:1.5;">${descEn}</div>
      </div>

      <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px; font-size:12px; margin-top:12px;">
        <div style="background:#0f172a; padding:8px 12px; border-radius:6px; border:1px solid #1e293b;">
          <span style="color:#64748b; display:block;">⚡ Recommended Steps / الخطوات:</span>
          <strong style="color:#38bdf8; font-size:14px;">${steps} steps</strong>
        </div>
        <div style="background:#0f172a; padding:8px 12px; border-radius:6px; border:1px solid #1e293b;">
          <span style="color:#64748b; display:block;">🎯 Guidance Scale / مقياس التوجيه:</span>
          <strong style="color:#38bdf8; font-size:14px;">${guidance}</strong>
        </div>
        <div style="background:#0f172a; padding:8px 12px; border-radius:6px; border:1px solid #1e293b;">
          <span style="color:#64748b; display:block;">💾 Model Size / حجم الملف:</span>
          <strong style="color:#e2e8f0;">${sizeMb}</strong>
        </div>
        <div style="background:#0f172a; padding:8px 12px; border-radius:6px; border:1px solid #1e293b;">
          <span style="color:#64748b; display:block;">🖥️ Hardware Fit / توافق الجهاز:</span>
          <strong style="color:#4ade80;">${hardware}</strong>
        </div>
      </div>

      <div style="margin-top:14px; text-align:center;">
        <button type="button" class="ghost-btn" onclick="askAiDeepExplain('${escape(targetId)}')" style="font-size:12px; padding:6px 14px; border:1px solid rgba(168,85,247,0.4); color:#c084fc; border-radius:6px; cursor:pointer;">
          ✨ Ask AI for Detailed Breakdown / استخراج شرح معمق بالذكاء الاصطناعي
        </button>
      </div>
      <div id="aiDeepResult" style="margin-top:12px; display:none;"></div>
    </div>
  `;

  if (typeof _phModalShow === 'function') {
    _phModalShow({
      kicker: 'AI Engine Details · تفاصيل المحرك',
      title: `${titleEn}`,
      htmlBody: htmlBody,
      primaryLabel: 'Close · إغلاق'
    });
  }
}

// Show bilingual info modal for any LoRA
async function showLoraBilingualInfo(loraPath) {
  const cat = await loadDrawThingsCatalog();
  const fn = String(loraPath || '').split('/').pop();
  const lora = (cat.loras || []).find(l => l.filename === fn || l.path === loraPath) || {
    filename: fn,
    name_en: fn.replace('_lora_f16', '').replace('_', ' ').toUpperCase(),
    name_ar: `لورا ${fn}`,
    desc_en: 'Custom LoRA fine-tune adapter for local generative acceleration and artistic styling.',
    desc_ar: 'لورا متخصصة تم تدريبها لإضافة أسلوب فني أو تسريع رندرة الفيديو والصور.',
    trigger: fn.split('_')[0],
    recommended_weight: 1.0,
    lane: fn.includes('h3') ? 'h3' : 'image',
    size_mb: 'Unknown'
  };

  const trigger = lora.trigger || 'auto-applied';

  const htmlBody = `
    <div style="font-family:system-ui,-apple-system,sans-serif; text-align:left;">
      <div dir="rtl" style="text-align:right; margin-bottom:14px; background:rgba(168,85,247,0.08); padding:12px; border-radius:8px; border-right:3px solid #a855f7;">
        <div style="font-weight:700; font-size:14px; color:#e2e8f0; margin-bottom:4px;">${lora.name_ar}</div>
        <div style="font-size:13px; color:#cbd5e1; line-height:1.6;">${lora.desc_ar}</div>
      </div>

      <div dir="ltr" style="margin-bottom:14px; background:rgba(30,41,59,0.5); padding:12px; border-radius:8px; border-left:3px solid #38bdf8;">
        <div style="font-weight:700; font-size:13px; color:#f8fafc; margin-bottom:4px;">${lora.name_en}</div>
        <div style="font-size:12px; color:#94a3b8; line-height:1.5;">${lora.desc_en}</div>
      </div>

      <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px; font-size:12px; margin-top:12px;">
        <div style="background:#0f172a; padding:8px 12px; border-radius:6px; border:1px solid #1e293b;">
          <span style="color:#64748b; display:block;">🏷️ Trigger Word / كلمة التفعيل:</span>
          <div style="display:flex; align-items:center; gap:6px; margin-top:4px;">
            <code style="background:#1e293b; color:#38bdf8; padding:2px 8px; border-radius:4px; font-weight:700; font-size:13px;">${trigger}</code>
            ${trigger !== 'auto-applied' ? `
              <button type="button" class="ghost-btn" onclick="insertPromptTrigger('${escape(trigger)}')" title="Insert trigger into prompt" style="padding:2px 6px; font-size:11px; cursor:pointer;">➕ Insert</button>
            ` : ''}
          </div>
        </div>
        <div style="background:#0f172a; padding:8px 12px; border-radius:6px; border:1px solid #1e293b;">
          <span style="color:#64748b; display:block;">⚖️ Recommended Weight / الوزن:</span>
          <strong style="color:#38bdf8; font-size:14px;">${lora.recommended_weight || 1.0}</strong>
        </div>
        <div style="background:#0f172a; padding:8px 12px; border-radius:6px; border:1px solid #1e293b;">
          <span style="color:#64748b; display:block;">🎯 Compatibility / التوافق:</span>
          <strong style="color:#e2e8f0;">${lora.lane === 'h3' ? 'MiniMax H3 Video' : 'Image Studio'}</strong>
        </div>
        <div style="background:#0f172a; padding:8px 12px; border-radius:6px; border:1px solid #1e293b;">
          <span style="color:#64748b; display:block;">💾 File Size / الحجم:</span>
          <strong style="color:#4ade80;">${lora.size_mb || '—'} MB</strong>
        </div>
      </div>

      <div style="margin-top:14px; text-align:center;">
        <button type="button" class="ghost-btn" onclick="askAiDeepExplain('${escape(fn)}')" style="font-size:12px; padding:6px 14px; border:1px solid rgba(168,85,247,0.4); color:#c084fc; border-radius:6px; cursor:pointer;">
          ✨ Ask AI for Detailed Breakdown / استخراج شرح معمق بالذكاء الاصطناعي
        </button>
      </div>
      <div id="aiDeepResult" style="margin-top:12px; display:none;"></div>
    </div>
  `;

  if (typeof _phModalShow === 'function') {
    _phModalShow({
      kicker: 'LoRA Knowledge Base · تفاصيل اللورا',
      title: `${lora.name_en}`,
      htmlBody: htmlBody,
      primaryLabel: 'Close · إغلاق'
    });
  }
}

// Dynamic AI deep explanation call
async function askAiDeepExplain(itemName) {
  const box = document.getElementById('aiDeepResult');
  if (!box) return;
  box.style.display = 'block';
  box.innerHTML = '<div style="color:#94a3b8; font-size:12px; text-align:center;">🤖 Asking AI model for deep synthesis... / جارٍ التحليل بالذكاء الاصطناعي...</div>';

  try {
    const res = await fetch('/drawthings/ai_explain', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: `name=${encodeURIComponent(unescape(itemName))}&ai_deep=1`
    });
    const data = await res.json();
    if (data.ok && data.item) {
      const it = data.item;
      box.innerHTML = `
        <div style="background:#0b1120; border:1px solid #1e293b; border-radius:6px; padding:10px; margin-top:8px;">
          <div dir="rtl" style="text-align:right; color:#a5b4fc; font-size:12px; line-height:1.6; margin-bottom:8px; white-space:pre-line;">
            ${it.deep_ar || it.desc_ar}
          </div>
          <div dir="ltr" style="text-align:left; color:#94a3b8; font-size:11px; line-height:1.5; white-space:pre-line;">
            ${it.deep_en || it.desc_en}
          </div>
        </div>
      `;
    } else {
      box.innerHTML = `<div style="color:#f87171; font-size:12px;">Failed to generate AI explanation.</div>`;
    }
  } catch (err) {
    box.innerHTML = `<div style="color:#f87171; font-size:12px;">AI service error: ${err.message}</div>`;
  }
}

// Apply selected prompt style
async function applyDtPromptStyle(styleName) {
  if (!styleName) return;
  const cat = await loadDrawThingsCatalog();
  const style = (cat.styles || []).find(s => s.name === styleName);
  if (!style) return;

  const promptEl = document.getElementById('prompt');
  const avoidEl = document.getElementById('avoid') || document.getElementById('negative_prompt');

  if (promptEl) {
    const cur = promptEl.value.trim();
    if (style.prompt && style.prompt.includes('[prompt]')) {
      promptEl.value = cur ? style.prompt.replace('[prompt]', cur) : style.prompt.replace('[prompt]', '').trim();
    } else if (style.prompt) {
      promptEl.value = cur ? `${cur}, ${style.prompt}` : style.prompt;
    }
  }

  if (avoidEl && style.negative_prompt) {
    const curNeg = avoidEl.value.trim();
    avoidEl.value = curNeg ? `${curNeg}, ${style.negative_prompt}` : style.negative_prompt;
    const avoidRow = document.getElementById('avoidRow');
    if (avoidRow) avoidRow.style.display = '';
  }

  try {
    if (typeof phosToast === 'function') {
      phosToast(`Applied style: ${style.label_ar || style.label_en}`);
    }
  } catch (_) {}
}

// ⚡ Auto-Animate: Image to Video with Qwen Vision + MiniMax H3
async function runDtAutoAnimate() {
  const initImgInput = document.getElementById('image') || document.getElementById('init_image') || document.querySelector('input[name="image"]') || document.querySelector('input[name="init_image"]');
  const imgPath = initImgInput ? initImgInput.value : '';

  if (!imgPath) {
    if (typeof _phModalShow === 'function') {
      _phModalShow({
        kicker: 'Auto-Animate · التحريك التلقائي',
        title: 'Select an Image First · اختر صورة أولاً',
        body: 'Please select or upload a character or scene photo to animate it automatically.\nالرجاء اختيار أو رفع صورة أولاً ليقوم الذكاء الاصطناعي بتحريكها تلقائياً.',
        primaryLabel: 'OK'
      });
    }
    return;
  }

  try {
    if (typeof phosToast === 'function') phosToast('⚡ Analyzing image & generating video with MiniMax H3...');
    const res = await fetch('/drawthings/auto_animate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: `image=${encodeURIComponent(imgPath)}&steps=4&frames=17`
    });
    const data = await res.json();
    if (data.ok) {
      if (typeof phosToast === 'function') phosToast('✓ MiniMax H3 video queued for rendering!');
      if (typeof pollStatus === 'function') pollStatus();
    } else {
      throw new Error(data.error || 'Server error');
    }
  } catch (err) {
    alert(`Auto-Animate failed: ${err.message}`);
  }
}

// Expose functions globally
Object.assign(globalThis, {
  loadDrawThingsCatalog,
  populatePromptStyles,
  insertPromptTrigger,
  showEngineInfoModal,
  showLoraBilingualInfo,
  askAiDeepExplain,
  applyDtPromptStyle,
  runDtAutoAnimate,
});

// Preload catalog on document ready & populate styles
if (typeof document !== 'undefined') {
  document.addEventListener('DOMContentLoaded', () => {
    loadDrawThingsCatalog().then(() => {
      populatePromptStyles();
    });
  });
}
