# Contributing

English is the primary language for code, documentation and issues. The UI defaults
to English and includes Simplified Chinese. See [localization](docs/localization.md).
Contributions are licensed under Apache-2.0 unless explicitly marked otherwise.

Use Python 3.11+ on Linux. Install `requirements-dev.txt` and the package in editable
mode. Run `python -m unittest discover -s tests` and `python scripts/check_browser.py`.
Install Playwright Chromium as described in the README. Browser checks run on local
synthetic fixtures and never require real model credentials.

Native tests are explicit: `python tests/native_empty_check.py --native --codex
/absolute/path/to/codex` and the corresponding `native_identity_check.py`. They create
a temporary CODEX_HOME, workspace, socket and local model endpoint. Never run tests
against a user's normal daemon or mutate real conversations. Paid-model checks are
separate, opt-in and require a dedicated test daemon.

Keep fixes focused and include regressions for ownership, duplicate submission,
replay, authentication or data-loss changes. Do not write tests that assert private
machine details. Follow AGENTS.md. No automated change may restart a user's native
Codex daemon, modify their auth or rewrite their shell startup configuration.

Before committing, run `python scripts/check_publication.py --staged` and a secret
scanner. Do not include real screenshots, API keys, environment files, transcripts,
logs, native binaries, deployment identifiers or generated test artifacts.

Releases must pass an installed-wheel smoke test so missing static assets are caught.
Keep versioning in `codex_remote/__init__.py`; record user-visible changes in CHANGELOG.md.
