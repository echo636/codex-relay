# Deployment

## Direct or VPN access

Use a Linux account that already runs Codex successfully. Install this package in a
virtual environment and keep state outside the checkout. A workspace can be on NAS;
the gateway SQLite database should be on a local filesystem with sufficient quota.

For a private VPN hostname:

```bash
codex-remote --workspace "$HOME/projects/example" \
  --host 0.0.0.0 --port 8765 --origin http://agent.example.com:8765 \
  --state "$HOME/.local/state/codex-remote"
```

Restrict this listener to the private network. HTTP does not encrypt the login token
or conversation traffic. Use HTTPS for untrusted networks and for full PWA features.
Use the exact configured hostname; the gateway checks both Host and Origin.

## HTTPS through a relay

The agent server makes an outbound SSH connection to a relay you control. Caddy on
the relay terminates HTTPS and forwards requests through the reverse tunnel:

```text
Phone/browser -> HTTPS -> Caddy -> loopback TCP -> SSH tunnel -> gateway -> Codex
```

The relay can see traffic after TLS termination. This is not end-to-end encryption
against the relay. Use a trusted host. The native Codex socket stays on the agent server.

1. Configure DNS for `codex.example.com` to the relay.
2. Install Caddy and adapt `deploy/Caddyfile.example`.
3. Configure the agent server's SSH alias using `deploy/ssh-config.example` and verify
   the relay host key. Limit the relay account to the required forwarding where possible.
4. Keep `GatewayPorts no` and bind the forwarded port only to relay loopback.
5. Generate the gateway and tunnel units:

```bash
codex-remote-manage install --workspace "$HOME/projects/example" \
  --state "$HOME/.local/state/codex-remote" \
  --origin https://codex.example.com --relay codex-relay
```

Inspect the generated units and use the printed systemctl commands. On the relay,
open only HTTPS/certificate-validation ports and necessary SSH. Do not expose port
18765 or the native daemon socket. Proxy buffering must be disabled for SSE; Caddy's
`flush_interval -1` handles this in the supplied template.

## Shared mode

Use `--transport shared --browse-existing` only with an already running compatible
native daemon. Optionally specify `--socket /absolute/path/to/tested/socket`.
The tool will not start or restart that daemon. First verify the daemon can call your
model provider. A gateway `--env-file` does not change an existing daemon's environment.
A thread owned by a different live writer cannot always be connected; release ownership
normally in that client or work from a fork. Never delete native lock files.

## Permissions

The default is workspace-write plus on-request approval. Full Access is opt-in:
`--sandbox danger-full-access --approval never`. It applies to new sessions and
explicit connections/reconnections; current running turns retain native semantics.
Use a dedicated OS user when isolation from other accounts is required.

## PWA and notifications

Open the HTTPS site, log in, and add it to the home screen. On compatible iPhones,
open the installed web app and use the device/notification panel to subscribe and
send a test notification. Safari/OS support, permission, network reachability and
push delivery remain platform-dependent. Generic notifications do not include
conversation titles or message text; the encrypted payload includes a thread ID.

The gateway must reach browser push services. An installed icon over a plain HTTP
LAN origin does not establish push support. Browser closing or device suspension
cannot be used as proof that a server task has completed.

## Service management

The installer generates user-level units without changing shell startup files or
Codex authentication. `--start` explicitly starts/restarts only generated gateway
and tunnel units. Persistent execution after logout may require user lingering,
configured by the server administrator. No root installation is required by the app.

Uninstall: stop/disable only your gateway/tunnel units, remove their generated files,
then remove the package or virtual environment. Preserve state until you have a
verified backup. Do not stop shared Codex as part of uninstalling this gateway.
