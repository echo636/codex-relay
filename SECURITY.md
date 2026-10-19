# Security policy

This is a single-user remote control surface for a server-side coding agent.
Authenticated devices share the same authority. Full Access may execute commands,
modify files and access the network as the server user. Workspace selection is not
an OS isolation boundary. Use separate OS accounts/instances for separate users.

Defaults are loopback-only, isolated transport, workspace-write and on-request
approval. Public access should use a trusted HTTPS reverse proxy. HTTP does not
protect tokens or conversations in transit. A TLS-terminating relay can see traffic.
Native daemon sockets must never be exposed directly to the public internet.

Sessions use random tokens with HttpOnly/SameSite cookies; HTTPS adds Secure.
Origin/Host checks, JSON-only mutations, upload limits, bounded HTTP/SSE connections
and login throttling reduce exposure. These are not a substitute for an independent
security review or protection against all denial-of-service attacks.

Model text is untrusted. Raw Markdown HTML is disabled, math rendering disables trust,
and Mermaid executes in an opaque sandbox with no API/network access; the main page
receives an image. Browser dependencies are local and versioned.
File downloads require authentication. The project browser stays within its workspace.
An assistant message can also offer a specific file outside that directory: the download
endpoint checks the destination against that message’s indexed Markdown links. Merely
rendering a message never reads or downloads those files. Files are sent as attachments;
HTML and SVG downloads do not execute inside the web console.

API responses and transcripts are not cached by the service worker. Language and
UI preferences use local storage. During an explicit language change, unsent drafts
are temporarily placed in tab session storage, consumed on reload, expire after
five minutes and are cleared on logout. Do not share a signed-in browser profile.
Notification payloads omit message text and titles; encrypted payloads include a
thread ID. Push delivery uses the browser vendor service.

State directories, backups, diagnostic logs, screenshots and exports may contain
credentials or private project details. Never attach them to public issues. The
`doctor` command intentionally omits identifying paths, environment and conversations.

Report vulnerabilities through this repository's GitHub **Security → Report a
vulnerability** when enabled. If that option is unavailable, open an issue requesting
a private reporting channel without including exploit details or sensitive data.
Do not post credentials. Only the latest alpha release is maintained; compatibility
and security fixes may require upgrading.
