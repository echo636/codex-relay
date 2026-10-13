# Operations

## Storage layout

`--state` contains `gateway.sqlite3` (WAL), an access token, uploaded files and, when
configured, a Web Push private key. Keep it private and outside this repository.
Native Codex owns its own conversation files and authentication. The gateway never
migrates them. Use a local filesystem for SQLite; home quotas are separate from
whole-disk free space.

`codex-remote-manage doctor --state /absolute/local/state` prints versions,
dependency checks and storage health without user names, host names, paths,
credentials, thread IDs, environment variables or logs. It does not attach to a daemon.
Filesystem free space does not prove that your account quota is sufficient; doctor
explicitly reports that quotas were not checked. Resolve a network-filesystem warning
before relying on SQLite WAL. `/healthz` only reports HTTP liveness; authenticated
bootstrap reports the native connection state.

## Cleanup

The device/notification panel can delete never-submitted uploads older than seven
days. Used uploads, including ambiguous submissions, are retained conservatively.
No conversation is deleted by this action. A stale browser draft may need its old
attachment reuploaded after cleanup.

For an offline preview and optional database compaction:

```bash
codex-remote-manage cleanup --state /absolute/local/state
codex-remote-manage cleanup --state /absolute/local/state --apply
```

Stop only the gateway first. Offline commands refuse the state lock of a running
gateway; they do not stop anything for you. Stopping an isolated gateway interrupts
its private tasks. Shared native tasks remain owned by their daemon.
Event pruning frees SQLite pages for reuse; `--apply` also compacts the database.
Never remove a used upload just to reset the quota: native history may reference it.

## Backup and restore

After stopping the gateway:

```bash
codex-remote-manage backup --state /absolute/local/state --output /absolute/private/backup
codex-remote-manage restore --backup /absolute/private/backup --state /absolute/new/state
```

Backup destinations must be new directories outside the state directory. Restore
also requires a new directory. Backups use SQLite's backup API, verify integrity and
record file checksums. Restore verifies them and rebases the gateway upload index.
**Backups include login and push credentials and private conversation metadata. Never
publish them.** They do not include native Codex history, credentials or workspaces.

Preserve native history separately using your own Codex backup procedure. Native
turns can contain absolute attachment paths: restore to the original state location
or retain that original path when you need historical attachments. Rebasing the gateway
index cannot rewrite native history. To restore to an original location, first move
its stopped state aside; do not overwrite it.

## Upgrade and rollback

1. Check release compatibility notes and wait for isolated tasks to finish.
2. Stop only the gateway and make a verified gateway backup.
3. Install the new release with its pinned dependencies and run `doctor`.
4. Start the gateway and verify a synthetic/new test conversation.

Schema v1 rejects databases marked with newer schema versions. Legacy schema-zero
databases receive a private `gateway.sqlite3.pre-v1.sqlite3` backup before migration.
If a migration fails, inspect the snapshot and restore deliberately; the app refuses
to overwrite an existing migration snapshot. Rollback uses a compatible full backup,
not just an old binary against a newer database. Never upgrade or restart shared
Codex automatically as part of a gateway update.

## Common failures

| Symptom | Check |
| --- | --- |
| 403 in browser | Exact Origin/Host, reverse proxy and login state |
| Model provider rejects a client | Provider policy and native Codex version/configuration; do not bypass policy |
| Writer conflict | Another native process owns the thread; let it release normally |
| Quota or disk I/O error | Account quota, free space, local database placement and filesystem health |
| Proxy 502 / no streaming | Gateway, SSH tunnel, reverse proxy and SSE buffering |
| Missing push | HTTPS, installed PWA, permission and outbound push-service connectivity |

Share only the redacted doctor output in public bug reports. Screenshots, exported
conversations and raw service logs may contain secrets or private project details.
