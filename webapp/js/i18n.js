// webapp/js/i18n.js — Bilingual English/Arabic localization engine for MacStudio MLX
// Default language: English ('en'). Arabic ('ar') toggles full RTL layout and translation.

(function (root, factory) {
  if (typeof define === 'function' && define.amd) {
    define([], factory);
  } else if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.I18N = factory();
    root.t = root.I18N.t;
    root.setLanguage = root.I18N.setLanguage;
    root.getLanguage = root.I18N.getLanguage;
    root.toggleLanguage = root.I18N.toggleLanguage;
  }
}(typeof self !== 'undefined' ? self : this, function () {

  const STORAGE_KEY = 'phos_ui_lang';
  const DEFAULT_LANG = 'en';

  const TRANSLATIONS = {
    en: {
      // Header & System
      "app_title": "MacStudio MLX",
      "switch_lang": "عربي",
      "switch_lang_title": "Switch to Arabic / التبديل إلى العربية",
      "docs": "Docs",
      "docs_title": "Docs — how every tool works, and every shortcut",
      "settings": "Settings",
      "settings_title": "Settings — models, rendering, output, accounts",
      "report_bug": "Report a bug",
      "report_bug_title": "Report a bug — opens GitHub issue with environment pre-filled",
      "stop_comfy": "Stop Comfy",
      "update_now": "Update now",
      "later": "Later",
      "new_version_available": "A new version is available",
      "tier": "Tier",
      "memory": "Memory",
      "engine": "Engine",
      "models": "Models",
      "queue": "Queue",
      "render": "Render",
      "system_health": "System health",
      "sys_health_click": "System health — click for detail",

      // Navigation Tabs
      "tab_video": "Video",
      "tab_oneshot": "One Shot",
      "tab_images": "Images",
      "tab_storyboard": "Storyboard",
      "tab_editor": "Editor",
      "tab_audio": "Audio",
      "tab_train": "Train Character",

      // Mode Bar
      "mode_text": "Text",
      "mode_text_sub": "prompt → video",
      "mode_character": "Character",
      "mode_character_sub": "trained face + voice",
      "mode_image": "Image",
      "mode_image_sub": "image + prompt",
      "mode_keyframe": "Start & end frame",
      "mode_keyframe_sub": "first + last",
      "mode_keyframes": "Keyframes",
      "mode_keyframes_sub": "3–8 frames",
      "mode_extend": "Extend",
      "mode_extend_sub": "continue a clip",
      "mode_add_sound": "Add sound",
      "mode_add_sound_sub": "your clip → its sound",
      "mode_lipsync": "Lip-sync",
      "mode_lipsync_sub": "picture + audio → it talks",
      "mode_remix": "Remix",
      "mode_remix_sub": "motion control · refs · color",

      // Remix Tools
      "remix_header": "Remix tool · bring your own media · IC-LoRA",
      "remix_ingredients": "Ingredients",
      "remix_ingredients_sub": "2–8 refs → one clip",
      "remix_control": "Motion Control",
      "remix_control_sub": "pose · depth · canny",
      "remix_colorize": "Colorize & Restore",
      "remix_colorize_sub": "vintage / b&w → vivid video",

      // Form Controls & Labels
      "prompt_label": "Prompt",
      "prompt_placeholder": "Describe what you want to see…",
      "negative_prompt_label": "Negative Prompt",
      "negative_prompt_placeholder": "What to avoid: blurry, distorted, jitter, bad hands…",
      "enhance": "Enhance",
      "enhance_title": "Use AI to expand and refine your prompt",
      "clear": "Clear",
      "random": "Random",
      "seed": "Seed",
      "quality": "Quality",
      "duration": "Duration",
      "frames": "Frames",
      "fps": "FPS",
      "aspect_ratio": "Aspect Ratio",
      "resolution": "Resolution",
      "steps": "Steps",
      "guidance": "Guidance (CFG)",
      "loras": "LoRAs",
      "camera": "Camera",
      "camera_motion": "Camera Motion",
      "generate": "Generate",
      "render_video": "Render Video",
      "stop": "Stop",
      "stop_title": "Stop the current render",
      "cancel": "Cancel",
      "apply": "Apply",
      "close": "Close",
      "save": "Save",
      "delete": "Delete",
      "retry": "Retry",
      "download": "Download",
      "play": "Play",
      "pause": "Pause",

      // Bottom Pane
      "now": "Now",
      "recent": "Recent",
      "logs": "Logs",
      "idle": "Idle",
      "running": "Running",
      "completed": "Completed",
      "failed": "Failed",

      // Image Studio & Storyboard & Audio & Train
      "studio_generate": "Generate Image",
      "studio_style": "Style",
      "os_title": "One Shot Film",
      "os_generate": "Generate Full Film",
      "sb_new": "New Storyboard",
      "sb_render_board": "Render Storyboard",
      "train_name": "Character Name",
      "train_start": "Start Training",
      "audio_compose": "Compose Music",
      "audio_lyrics": "Lyrics",

      // Settings Modal
      "appearance": "Appearance",
      "theme_dark": "Dark",
      "theme_light": "Light",
      "theme_system": "System",
      "language": "Language",
      "language_en": "English",
      "language_ar": "العربية (Arabic)",
      "codec_preset": "Output Codec Preset",
      "save_settings": "Save Settings"
    },
    ar: {
      // Header & System
      "app_title": "ماك ستوديو MLX",
      "switch_lang": "English",
      "switch_lang_title": "Switch to English / التبديل إلى الإنجليزية",
      "docs": "التوثيق",
      "docs_title": "التوثيق — شرح كامل لجميع الأدوات والاختصارات",
      "settings": "الإعدادات",
      "settings_title": "الإعدادات — النماذج، خيارات التوليد، الإخراج والحسابات",
      "report_bug": "إبلاغ عن خطأ",
      "report_bug_title": "إبلاغ عن مشكلة — يفتح تذكرة على GitHub مع تضمين تفاصيل النظام",
      "stop_comfy": "إيقاف Comfy",
      "update_now": "تحديث الآن",
      "later": "لاحقاً",
      "new_version_available": "يتوفر إصدار جديد",
      "tier": "فئة العتاد",
      "memory": "الذاكرة",
      "engine": "المحرك",
      "models": "النماذج",
      "queue": "الانتظار",
      "render": "التوليد",
      "system_health": "حالة النظام",
      "sys_health_click": "حالة النظام — اضغط للتفاصيل",

      // Navigation Tabs
      "tab_video": "فيديو",
      "tab_oneshot": "لقطة واحدة",
      "tab_images": "صور",
      "tab_storyboard": "لوحة القصة",
      "tab_editor": "المحرر",
      "tab_audio": "صوت",
      "tab_train": "تدريب شخصية",

      // Mode Bar
      "mode_text": "نص",
      "mode_text_sub": "نص ← فيديو",
      "mode_character": "شخصية",
      "mode_character_sub": "وجه وصوت مدرّب",
      "mode_image": "صورة",
      "mode_image_sub": "صورة + نص",
      "mode_keyframe": "البداية والنهاية",
      "mode_keyframe_sub": "الأول والأخير",
      "mode_keyframes": "إطارات مفتاحية",
      "mode_keyframes_sub": "3–8 إطارات",
      "mode_extend": "تمديد",
      "mode_extend_sub": "مواصلة المشهد",
      "mode_add_sound": "إضافة صوت",
      "mode_add_sound_sub": "المقطع ← صوته",
      "mode_lipsync": "مزامنة الشفاه",
      "mode_lipsync_sub": "صورة + صوت ← حديث",
      "mode_remix": "ريمكس",
      "mode_remix_sub": "تحكم بالحركة · مراجع · ألوان",

      // Remix Tools
      "remix_header": "أداة ريمكس · أضف وسائطك الخاصة · IC-LoRA",
      "remix_ingredients": "المكونات",
      "remix_ingredients_sub": "2–8 صور مرجعية ← مقطع واحد",
      "remix_control": "التحكم بالحركة",
      "remix_control_sub": "وضعيات الجسم · العمق · الحواف",
      "remix_colorize": "تلوين واستعادة",
      "remix_colorize_sub": "أبيض وأسود / قديم ← فيديو ملون حي",

      // Form Controls & Labels
      "prompt_label": "الوصف التوجيهي (Prompt)",
      "prompt_placeholder": "صف المشهد الذي تريد توليده بدقة…",
      "negative_prompt_label": "الوصف السلبي (تجنب)",
      "negative_prompt_placeholder": "ما ترغب بتجنبه: تشويه، ضبابية، اهتزاز، أيدي مشوهة…",
      "enhance": "تحسين الوصف",
      "enhance_title": "استخدام الذكاء الاصطناعي لتحسين وتوسيع النص التوجيهي",
      "clear": "مسح",
      "random": "عشوائي",
      "seed": "البذرة (Seed)",
      "quality": "الجودة",
      "duration": "المدة",
      "frames": "الإطارات",
      "fps": "معدل الإطارات",
      "aspect_ratio": "نسبة الأبعاد",
      "resolution": "الدقة",
      "steps": "الخطوات",
      "guidance": "قوة التوجيه (CFG)",
      "loras": "نماذج LoRA",
      "camera": "الكاميرا",
      "camera_motion": "حركة الكاميرا",
      "generate": "توليد",
      "render_video": "توليد الفيديو",
      "stop": "إيقاف",
      "stop_title": "إيقاف التوليد الحالي",
      "cancel": "إلغاء",
      "apply": "تطبيق",
      "close": "إغلاق",
      "save": "حفظ",
      "delete": "حذف",
      "retry": "إعادة المحاولة",
      "download": "تنزيل",
      "play": "تشغيل",
      "pause": "إيقاف مؤقت",

      // Bottom Pane
      "now": "الآن",
      "recent": "السابق",
      "logs": "السجلات",
      "idle": "خامل",
      "running": "قيد التشغيل",
      "completed": "مكتمل",
      "failed": "فشل",

      // Image Studio & Storyboard & Audio & Train
      "studio_generate": "توليد صورة",
      "studio_style": "النمط",
      "os_title": "فيلم بلقطة واحدة",
      "os_generate": "توليد الفيلم كاملاً",
      "sb_new": "لوحة قصة جديدة",
      "sb_render_board": "توليد لوحة القصة",
      "train_name": "اسم الشخصية",
      "train_start": "بدء التدريب",
      "audio_compose": "تلحين وتوليد",
      "audio_lyrics": "كلمات الأغنية",

      // Settings Modal
      "appearance": "المظهر",
      "theme_dark": "داكن",
      "theme_light": "فاتح",
      "theme_system": "حسب النظام",
      "language": "اللغة",
      "language_en": "English",
      "language_ar": "العربية (Arabic)",
      "codec_preset": "ترميز الفيديو المخرج",
      "save_settings": "حفظ الإعدادات"
    }
  };

  function getLanguage() {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved === 'ar' || saved === 'en') return saved;
    } catch (e) {}
    return DEFAULT_LANG;
  }

  function t(key, fallback) {
    const lang = getLanguage();
    if (TRANSLATIONS[lang] && TRANSLATIONS[lang][key] !== undefined) {
      return TRANSLATIONS[lang][key];
    }
    if (TRANSLATIONS[DEFAULT_LANG] && TRANSLATIONS[DEFAULT_LANG][key] !== undefined) {
      return TRANSLATIONS[DEFAULT_LANG][key];
    }
    return fallback !== undefined ? fallback : key;
  }

  function updateToggleButtons(lang) {
    const nextLang = lang === 'ar' ? 'en' : 'ar';
    const label = lang === 'ar' ? 'English' : 'عربي';
    const title = lang === 'ar' ? 'Switch to English / التبديل للإنجليزية' : 'Switch to Arabic / التبديل للعربية';

    document.querySelectorAll('.lang-toggle-btn').forEach(btn => {
      const word = btn.querySelector('.lang-word');
      if (word) {
        word.textContent = label;
      } else {
        btn.textContent = (btn.querySelector('.lang-globe') ? '' : '🌐 ') + label;
      }
      btn.title = title;
      btn.setAttribute('aria-label', title);
    });
  }

  function applyTranslations(root) {
    const doc = root || document;
    const lang = getLanguage();
    const isRtl = lang === 'ar';

    // Update document language and direction
    document.documentElement.lang = lang;
    document.documentElement.setAttribute('dir', isRtl ? 'rtl' : 'ltr');
    if (document.body) {
      document.body.setAttribute('data-lang', lang);
      if (isRtl) {
        document.body.classList.add('rtl-layout');
      } else {
        document.body.classList.remove('rtl-layout');
      }
    }

    // Translate text nodes with data-i18n
    doc.querySelectorAll('[data-i18n]').forEach(el => {
      const key = el.getAttribute('data-i18n');
      const val = t(key);
      if (val) el.textContent = val;
    });

    // Translate HTML contents with data-i18n-html
    doc.querySelectorAll('[data-i18n-html]').forEach(el => {
      const key = el.getAttribute('data-i18n-html');
      const val = t(key);
      if (val) el.innerHTML = val;
    });

    // Translate placeholders
    doc.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
      const key = el.getAttribute('data-i18n-placeholder');
      const val = t(key);
      if (val) el.setAttribute('placeholder', val);
    });

    // Translate titles
    doc.querySelectorAll('[data-i18n-title]').forEach(el => {
      const key = el.getAttribute('data-i18n-title');
      const val = t(key);
      if (val) el.setAttribute('title', val);
    });

    // Translate aria-labels
    doc.querySelectorAll('[data-i18n-aria]').forEach(el => {
      const key = el.getAttribute('data-i18n-aria');
      const val = t(key);
      if (val) el.setAttribute('aria-label', val);
    });

    // Update switcher buttons
    updateToggleButtons(lang);
  }

  function setLanguage(lang) {
    const target = (lang === 'ar' || lang === 'en') ? lang : DEFAULT_LANG;
    try {
      localStorage.setItem(STORAGE_KEY, target);
    } catch (e) {}

    applyTranslations();

    window.dispatchEvent(new CustomEvent('languagechange', {
      detail: { language: target, isRtl: target === 'ar' }
    }));
  }

  function toggleLanguage() {
    const cur = getLanguage();
    const next = cur === 'ar' ? 'en' : 'ar';
    setLanguage(next);
  }

  // Initialize on load
  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => applyTranslations());
    } else {
      applyTranslations();
    }
  }

  return {
    t,
    getLanguage,
    setLanguage,
    toggleLanguage,
    applyTranslations,
    translations: TRANSLATIONS
  };
}));
