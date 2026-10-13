"""Gateway-only storage maintenance. Never opens or edits native Codex storage."""
from contextlib import contextmanager, closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time

SCHEMA_VERSION = 1

def file_digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()



def migrate(connection, path):
    version = connection.execute('PRAGMA user_version').fetchone()[0]
    if version > SCHEMA_VERSION:
        raise ValueError('Gateway database is newer than this release; restore a compatible backup before downgrading.')
    if version == 0 and connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1").fetchone():
        snapshot = Path(str(path) + '.pre-v1.sqlite3')
        if snapshot.exists():
            raise ValueError('A pre-migration snapshot already exists; inspect it before retrying migration.')
        with closing(sqlite3.connect(snapshot)) as backup:
            connection.backup(backup)
        snapshot.chmod(0o600)
    with connection:
        connection.execute('CREATE TABLE IF NOT EXISTS event_floors(thread_id TEXT PRIMARY KEY, seq INTEGER NOT NULL)')
        connection.execute('PRAGMA user_version=1')


def initialize_uploads(store):
    store.execute('CREATE TABLE IF NOT EXISTS upload_lifecycle(id TEXT PRIMARY KEY, created REAL NOT NULL, used INTEGER NOT NULL)')
    # Existing attachments may already be referenced by native history; retain them.
    store.execute('INSERT OR IGNORE INTO upload_lifecycle SELECT id,?,1 FROM uploads', (time.time(),))


def clean_uploads(store, upload_dir, *, age_days=7, apply=False):
    if age_days < 1:
        raise ValueError('Unused uploads must be retained for at least one day.')
    rows = store.rows('SELECT u.id,u.path,u.size FROM uploads u JOIN upload_lifecycle l ON u.id=l.id WHERE l.used=0 AND l.created<?',
                      (time.time() - age_days * 86400,))
    total = 0
    for row in rows:
        path = Path(row['path'])
        root = Path(upload_dir).resolve()
        if path.is_symlink() or path.parent.is_symlink() or path.parent.resolve() != root / row['id']:
            raise ValueError('Unexpected upload path; cleanup refused.')
        if apply:
            path.unlink(missing_ok=True)
            try:
                path.parent.rmdir()
            except OSError:
                pass
            store.execute('DELETE FROM uploads WHERE id=?', (row['id'],))
            store.execute('DELETE FROM upload_lifecycle WHERE id=?', (row['id'],))
        total += row['size']
    return {'eligibleFiles': len(rows), 'eligibleBytes': total, 'applied': apply, 'minimumAgeDays': age_days}


@contextmanager
def offline(state):
    state = Path(state).resolve()
    if not state.is_dir():
        raise ValueError('State directory does not exist.')
    with (state / 'gateway.lock').open('a') as gate:
        try:
            fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Gateway is running. Stop only this gateway before offline maintenance; isolated tasks will stop too.')
        yield state


def backup_state(state, destination):
    destination = Path(destination).absolute()
    with offline(state) as state:
        if destination.exists() or state == destination or state in destination.resolve().parents:
            raise ValueError('Backup destination must be a new directory outside the state directory.')
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.codex-remote-backup-', dir=destination.parent) as temporary:
            target = Path(temporary)
            manifest = {'format': 1, 'schema': SCHEMA_VERSION, 'files': {}}
            candidates = [state/name for name in ('access-token', 'webpush-private.pem', 'gateway.sqlite3')]
            if (state/'uploads').is_symlink():
                raise ValueError('Symlinks are not accepted in backups.')
            candidates += list((state/'uploads').rglob('*')) if (state/'uploads').exists() else []
            for source in candidates:
                if source.is_symlink():
                    raise ValueError('Symlinks are not accepted in backups.')
                if not source.is_file():
                    continue
                relative = source.relative_to(state)
                output = target/relative
                output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if relative.as_posix() == 'gateway.sqlite3':
                    src = sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)
                    dst = sqlite3.connect(output)
                    try:
                        src.backup(dst)
                        if dst.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                            raise ValueError('Backup database failed integrity validation.')
                    finally:
                        src.close(); dst.close()
                else:
                    shutil.copyfile(source, output)
                output.chmod(0o600)
                manifest['files'][relative.as_posix()] = file_digest(output)
            (target/'backup-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
            (target/'backup-manifest.json').chmod(0o600)
            target.rename(destination)
    return {'files': len(manifest['files']), 'containsPrivateData': True}


def restore_state(source, destination):
    source, destination = Path(source).resolve(), Path(destination).absolute()
    if destination.exists():
        raise ValueError('Restore requires a new state directory; existing data is never overwritten.')
    manifest = json.loads((source/'backup-manifest.json').read_text())
    if manifest.get('format') != 1 or manifest.get('schema', 999) > SCHEMA_VERSION:
        raise ValueError('Unsupported backup version.')
    files = manifest.get('files', {})
    if 'gateway.sqlite3' not in files or 'access-token' not in files:
        raise ValueError('Backup is missing the database or access token.')
    for name, digest in files.items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or not (name in ('gateway.sqlite3','access-token','webpush-private.pem') or (len(relative.parts)==3 and relative.parts[0]=='uploads')):
            raise ValueError('Unexpected path in backup manifest.')
        path = source/relative
        if path.is_symlink() or path.parent.is_symlink() or source not in path.resolve().parents or file_digest(path) != digest:
            raise ValueError('Backup checksum/path verification failed.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.codex-remote-restore-', dir=destination.parent) as temporary:
        target = Path(temporary)
        for name in files:
            output = target/name
            output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(source/name, output); output.chmod(0o600)
        db = sqlite3.connect(target/'gateway.sqlite3')
        try:
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('Restored database failed integrity validation.')
            # Upload paths are absolute. Rebase only the gateway's file index.
            with db:
                for aid, name in db.execute('SELECT id,name FROM uploads').fetchall():
                    if len(aid)!=32 or any(c not in '0123456789abcdef' for c in aid) or Path(name).name!=name:
                        raise ValueError('Invalid upload index.')
                    db.execute('UPDATE uploads SET path=? WHERE id=?',(str(destination/'uploads'/aid/name),aid))
        finally:
            db.close()
        target.rename(destination)
    return {'files': len(files), 'nativeHistoryIncluded': False}
