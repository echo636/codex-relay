"""Run existing UI regressions in Chinese; i18n_check covers the English default."""
from playwright.sync_api import Browser


def install():
    for name in ('new_context', 'new_page'):
        original = getattr(Browser, name)
        def create(self, *args, _original=original, **kwargs):
            result = _original(self, *args, **kwargs)
            result.add_init_script("try { if (window === window.top) localStorage.setItem('codex-remote-language', 'zh-CN'); } catch (_) {}")
            return result
        setattr(Browser, name, create)
