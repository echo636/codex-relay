# Compatibility and limitations

- Supported release target: Linux and Python 3.11+. Other operating systems are not
  advertised: the gateway uses POSIX locks and optional Unix sockets/systemd.
- UI languages: English (default) and Simplified Chinese, with a per-device selector.
  Translation catalogs are separate from conversation content.
- The implementation was developed against Codex 0.160.0 and 0.162.1 protocol shapes.
  Native empty-thread and client-identity fixtures were verified with 0.162.1 for this alpha, using a local model and temporary state. These are compatibility references, not a promise that every feature works on
  every build or future version. See the release notes for checks actually executed.
- Async question cards support the `agentMessage.questions` shape in the Codex 0.163.0
  schema. Choice/free-text replies use the native question-reply text envelope,
  with active-turn steering and idle-turn continuation covered by local fixtures.
- Goals, queues, thread settings and shared daemon access use evolving native APIs.
  Unsupported calls surface errors; there is no silent emulation of unsupported goals.
- Shared mode requires an existing native daemon exposing the expected Unix socket
  protocol. Private stdio is the default and does not import external terminal threads.
- Closing a browser preserves execution. Restarting an isolated gateway interrupts its
  private child. Shared mode cannot recover a task whose native process itself exits.
- Active-writer conflicts are respected. Passive history visibility does not guarantee
  writable access to every terminal session.
- Authentication is single-user. All authenticated devices share the same privileges.
- Model/provider access, billing and client restrictions are managed by Codex and the
  provider. This project neither bypasses them nor bundles authentication material.
- PWA notifications require HTTPS and compatible browser/OS support. Mobile viewport
  tests do not certify iPhone background push. Validate your actual phone and relay.
- Assistant file links support relative/absolute paths, `file:` and `sandbox:` destinations,
  reference links and code line suffixes. Downloads stream regular files and support HTTP
  byte ranges; they do not have the upload size limit. Directory links require an archive
  created by the agent first. Missing or temporary files must be generated again.
- Uploaded files: 10 MiB per file, 8 per message, 1 GiB total. Used files are retained;
  only never-submitted files older than seven days are eligible for automatic cleanup.
- Replay events are capped at approximately 100,000 entries, pruned every 1,000 inserts.
  Older clients reload native history when their replay cursor expires. This bounds
  event count, not total bytes; large content and other tables still require monitoring.
- The gateway retains submission IDs to prevent duplicate execution after timeouts.
  Full conversation history belongs to Codex; gateway backups do not include native
  configuration, authentication or session storage.

Reference documentation: [App Server](https://developers.openai.com/codex/app-server/)
and [configuration](https://developers.openai.com/codex/config-advanced/).
Always verify behavior using the dedicated native fixtures before changing the supported
version range. CI's protocol fixture is not a substitute for a native compatibility check.
