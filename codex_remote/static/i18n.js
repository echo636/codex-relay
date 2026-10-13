'use strict';
(() => {
  const languages = {en: 'English', 'zh-CN': '简体中文'};
  let language = 'en';
  try { const saved = localStorage.getItem('codex-remote-language'); if (saved in languages) language = saved; } catch (_) {}
  function t(key) { return ((globalThis.CODEX_REMOTE_LOCALES || {})[language] || {})[key] || key; }
  function format(key, values) { return t(key).replace(/\{(\d+)\}/g, (_, index) => String(values[Number(index)] === undefined ? '' : values[Number(index)])); }
  function apply() {
    document.documentElement.lang = language;
    document.querySelectorAll('[data-i18n]').forEach(node => { node.textContent = t(node.dataset.i18n); });
    for (const attribute of ['title', 'placeholder', 'aria-label', 'alt']) {
      document.querySelectorAll('[data-i18n-' + attribute + ']').forEach(node => node.setAttribute(attribute, t(node.getAttribute('data-i18n-' + attribute))));
    }
    document.querySelectorAll('[data-language-select]').forEach(select => {
      select.value = language; select.setAttribute('aria-label', t('Language'));
      select.addEventListener('change', () => {
        const next = select.value; if (!(next in languages) || next === language) return;
        try {
          if (document.querySelector('dialog[open]')) throw new Error(t('Close the open dialog before changing language.'));
          if (window.remoteUI) window.remoteUI.saveLanguageDrafts();
          localStorage.setItem('codex-remote-language', next);
          location.reload();
        } catch (error) {
          select.value = language;
          const output = document.getElementById('notice') || document.getElementById('login-error');
          if (output) output.textContent = error instanceof DOMException ? t('Unable to preserve drafts. Your language was not changed.') : error.message;
        }
      });
    });
  }
  globalThis.I18n = {language, languages, t, format, apply};
  document.documentElement.lang = language;
  document.addEventListener('DOMContentLoaded', apply);
})();
