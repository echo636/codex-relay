'use strict';
(() => {
  const key = 'codex-remote-theme', media = window.matchMedia('(prefers-color-scheme: dark)');
  let preference = null;
  try { preference = localStorage.getItem(key); } catch (_) {}
  const explicit = () => ['light', 'dark'].includes(preference);
  function apply() {
    const dark = explicit() ? preference === 'dark' : media.matches;
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    document.querySelector('meta[name="theme-color"]').content = dark ? '#141a18' : '#fbfaf7';
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      const label = dark ? I18n.t("Switch to light mode") : I18n.t("Switch to dark mode");
      button.textContent = dark ? '☀' : '☾';
      button.title = label; button.setAttribute('aria-label', label);
      button.setAttribute('aria-pressed', String(dark));
    });
  }
  apply(); // Runs before the stylesheet to avoid a light flash when dark mode is saved.
  document.addEventListener('DOMContentLoaded', () => {
    apply();
    document.querySelectorAll('[data-theme-toggle]').forEach(button => button.addEventListener('click', () => {
      preference = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      try { localStorage.setItem(key, preference); } catch (_) {}
      apply();
    }));
  });
  media.addEventListener('change', () => { if (!explicit()) apply(); });
  window.addEventListener('storage', event => {
    if (event.key === key || event.key === null) { preference = event.newValue; apply(); }
  });
})();
