# Codex Relay

[简体中文](README.zh-CN.md) · [Deployment](docs/deployment.md) · [Operations](docs/operations.md) · [Security](SECURITY.md)

Previously named Codex Remote. The `codex-remote` and `codex-remote-manage` commands
remain compatible aliases; existing state directories and systemd services are reused.

A self-hosted mobile web console for Codex running on your own server. Continue a
conversation from your phone, send follow-up instructions, respond to approvals,
and inspect tool output without keeping a browser open.

**Alpha · Linux · Python 3.11+ · single user, multiple devices.** English is the default language; Simplified Chinese can be selected per device. This independent community project is not affiliated
with OpenAI. Install Codex separately and configure its authentication on the server.
The web login takes a gateway access token, never your model API key.

## Features

- Responsive Web/PWA interface, light/dark themes and live execution/completion status.
- Shared conversation history across browsers, reconnect and history pagination.
- Follow up during a turn, queue the next turn, stop, approve actions and answer questions.
- Workspace selection, conversation search, rename, archive, fork and JSON export.
- Clipboard images/files, drag-and-drop, file previews and tool image views.
- Markdown, highlighted code/diffs, KaTeX mathematics and sandboxed Mermaid diagrams.
- Native goal/permission controls when supported by your Codex version.
- Device revocation and opt-in Web Push with generic notification content.
- systemd installation, reverse SSH/HTTPS templates, redacted diagnostics and backups.

## Quick start

First install and authenticate a compatible Codex CLI independently. See
[compatibility](docs/compatibility.md) before using shared mode or experimental features.
From a checkout of this repository:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install --no-deps .
.venv/bin/codex-relay --workspace "$HOME/projects/example" \
  --state "$HOME/.local/state/codex-remote"
```

The workspace must already exist. Open `http://127.0.0.1:8765` on the server, or use
an SSH local forward to access it from your computer. Read
`$HOME/.local/state/codex-remote/access-token` locally and paste it into the login
form. Keep the token private. Choose a large **local filesystem** for `--state` when
home directories have quotas or live on network storage.

For phones, configure a reachable origin and follow the [deployment guide](docs/deployment.md).
HTTPS is required for the full PWA/notification experience. A web app is not an offline
agent: it needs a connection to the gateway to send instructions.

## Two connection modes

| Mode | Behavior |
| --- | --- |
| `isolated` (default) | Starts a private Codex app-server child. Browsers share its sessions. Existing terminal threads are not imported. Stopping this gateway stops its child and can interrupt its tasks. |
| `shared` (explicit opt-in) | Connects to an already running compatible native daemon. `--browse-existing` enables passive discovery. Selecting a thread may resume it; an active-writer conflict is respected. Stopping this gateway closes only its connection. |

Closing a browser does not stop server-side execution. This does **not** imply that
restarting an isolated gateway preserves an in-progress task. The gateway never
starts, stops or upgrades the user's shared daemon.

New sessions default to `workspace-write` with `on-request` approvals. Full Access
is optional. With `--sandbox danger-full-access --approval never`, explicitly
connected/reconnected threads also receive these defaults for subsequent turns.
Passive discovery never changes permissions. Workspace allowlists are not an OS sandbox.

## Install as a service

```bash
.venv/bin/codex-relay-manage install \
  --workspace "$HOME/projects/example" \
  --state "$HOME/.local/state/codex-remote" \
  --origin https://codex.example.com
```

This generates user systemd units and prints startup instructions. Add `--start`
only when you intend to start/restart **this gateway**. Provider environment variables
can be supplied using a private `--env-file`; they are not committed to the repository.
An existing shared daemon uses its own authentication and startup environment.

## Development and releases

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m unittest discover -s tests
PLAYWRIGHT_BROWSERS_PATH="$PWD/.browsers" .venv/bin/python -m playwright install chromium --only-shell
.venv/bin/python scripts/check_browser.py
.venv/bin/python scripts/check_publication.py --tracked
.venv/bin/python -m build
```

Tests use synthetic conversations and a local protocol fixture. Optional native
checks use dedicated temporary state and a local model; normal tests need no API key.
See [CONTRIBUTING.md](CONTRIBUTING.md), [release process](docs/releasing.md) and
[known limitations](docs/compatibility.md). Native Codex binaries and user data are
not bundled.

Apache-2.0 for project code; bundled libraries retain their own licenses. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
