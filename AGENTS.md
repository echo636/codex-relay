# Development constraints

- Never start, stop, restart, upgrade or kill an existing user's Codex daemon or terminal session.
- Do not alter user authentication, Codex configuration or shell startup files.
- Default to isolated transport. Shared daemon access requires explicit configuration.
- Passive discovery reads metadata/history only. Never resume every discovered thread.
- Explicitly selecting a thread may connect it; respect native active-writer rejection.
- Automated tests use the local protocol fixture. Native tests use their own temporary
  configuration, socket, workspace and daemon with a local model fixture.
- Cleanup must affect only test-owned resources. Shared mode closes its own connection.
- Preserve the native CLI originator `codex_cli_rs` with a truthful gateway title.
- Never commit credentials, transcripts, uploads, logs or real deployment details.
- Run `scripts/check_publication.py --staged` before committing release changes.
