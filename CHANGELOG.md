# Changelog

## Unreleased

- Rename the project to Codex Relay and the repository to `echo636/codex-relay`.
- Update the web/PWA branding and English/Chinese documentation; add `codex-relay`
  and `codex-relay-manage` command aliases. Existing commands, Python distribution
  and module names, state directories, browser settings and service IDs remain compatible.

## 0.3.0a1

Initial public alpha for a self-hosted, single-user remote Codex workspace.

- Package installation and console entry points; English documentation and UI by
  default, with Simplified Chinese UI, errors, notifications and documentation.
- Independent and explicitly enabled shared transports; live history, approvals,
  follow-ups, queues, workspace browsing, attachments and conversation management.
- Markdown, code/diff highlighting, math and sandboxed Mermaid rendering.
- Replay retention with native-history resync, conservative unused-upload cleanup,
  versioned gateway schema, private backups/restores and redacted diagnostics.
- Bounded HTTP connections, opt-in Full Access, HTTPS/SSH deployment templates,
  local fixture tests, dedicated native checks and GitHub CI.
- Generic server terminology and synthetic examples; no environment-specific
  maintenance scripts or project identifiers are distributed.

Known limits and protocol compatibility references are in docs/compatibility.md.
Real-device push delivery and public relay configuration require deployment-specific
verification; mobile browser viewport tests do not establish those behaviors.
