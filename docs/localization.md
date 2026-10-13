# Localization

English is the source language. The login page and toolbar provide English and
Simplified Chinese selectors; the choice is stored per browser. The switch reloads
the UI, preserves unsent conversation drafts temporarily in tab session storage,
and refuses to switch during submission/uploads or while a dialog is open.

Only UI-owned labels, errors and notification text are translated. User prompts,
model output, code, files, project names and tool payloads are never passed through
the translation catalog. The model's response language is independent of UI language.

Source strings appear in `I18n.t('English text')`, `I18n.format('Text {0}', [value])`
and static HTML `data-i18n` attributes. `codex_remote/static/locales/zh-CN.json` is the
Chinese catalog; its `.js` companion is generated for synchronous, offline-capable
loading. Run `python scripts/check_i18n.py --write` after editing the JSON, then run
without `--write` to verify it. Preserve interpolation placeholders and HTML fragments.

To add a language: add a catalog, register it in i18n.js and the selectors, expose its
asset in the server allowlist/service worker/package data, and extend backend locale
selection and notification handling. Add a browser test for that locale. Do not
translate conversation content using a global DOM observer.
