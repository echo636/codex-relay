"""Explicit local file links in assistant messages and bounded download I/O."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from codex_remote.server import APIError


def local_link_path(href):
    if not isinstance(href, str) or not href or len(href) > 8192:
        return None
    # Code references can include a line/column suffix. Strip URL fragments
    # before decoding so an encoded # or ? remains part of the actual filename.
    href = re.sub(r':\d+(?::\d+)?$', '', href.split('#', 1)[0].split('?', 1)[0])
    if not href or href.startswith(('//', '/api/')):
        return None
    try:
        url = urlsplit(href)
        if url.scheme not in ('', 'file', 'sandbox') or url.netloc not in ('', 'localhost'):
            return None
        path = unquote(url.path, errors='strict')
        if not path or len(path) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in path):
            return None
        return path
    except (ValueError, UnicodeError):
        return None


_markdown = MarkdownIt('commonmark', {'html': False})
_validate_link = _markdown.validateLink
_markdown.validateLink = lambda href: local_link_path(href) is not None or _validate_link(href)


def file_links(text):
    paths = set()
    if '[' not in text and '<' not in text:
        return paths
    for block in _markdown.parse(text):
        for token in block.children or []:
            href = token.attrGet('href') if token.type == 'link_open' else token.attrGet('src') if token.type == 'image' else None
            path = local_link_path(href)
            if path:
                paths.add(path)
    return paths


class ArtifactMixin:
    def _remember_files(self, tid, turns):
        for turn in turns:
            for item in turn.get('items', []):
                if item.get('type') != 'agentMessage' or not item.get('id') or not isinstance(item.get('text'), str):
                    continue
                text = item['text']
                digest = hashlib.sha256(text.encode()).hexdigest()
                old = self.store.rows('SELECT digest FROM file_links WHERE thread_id=? AND item_id=?', (tid, item['id']))
                if old and old[0]['digest'] == digest:
                    continue
                cwd = self.thread(tid)['cwd']
                # Index only destinations, never another copy of message text.
                # abspath is lexical: browsing history does not stat NAS files.
                links = {path: os.path.abspath(os.path.join(cwd, os.path.expanduser(path))) for path in file_links(text)}
                self.store.execute('INSERT INTO file_links VALUES(?,?,?,?) ON CONFLICT(thread_id,item_id) DO UPDATE SET links=excluded.links,digest=excluded.digest',
                                   (tid, item['id'], json.dumps(links), digest))

    def linked_file(self, tid, item_id, href):
        self.thread(tid)
        path = local_link_path(href)
        if not isinstance(item_id, str) or not 1 <= len(item_id) <= 200 or path is None:
            raise APIError(400, 'Invalid file link.')

        def lookup():
            rows = self.store.rows('SELECT links FROM file_links WHERE thread_id=? AND item_id=?', (tid, item_id))
            return json.loads(rows[0]['links']).get(path) if rows else None

        target = lookup()
        if target is None:
            # A link can be clicked while its message is still streaming. Read
            # the latest page once; never resume a thread or scan all history.
            self.history(tid, limit=10)
            target = lookup()
        if target is None:
            raise APIError(404, 'This file link is not in the conversation. Refresh the message and try again.')
        return target


def open_download(path, root=None):
    """Open a stable regular-file descriptor, optionally confined to a workspace."""
    fd = None
    try:
        target = Path(path).resolve(strict=True)
        boundary = Path(root).resolve(strict=True) if root is not None else None
        if boundary is not None and target != boundary and boundary not in target.parents:
            raise APIError(403, 'Cannot access files outside the working directory.')
        fd = os.open(target, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise APIError(400, 'This link points to a directory or a non-regular file. Ask Codex to create an archive first.')
        # Recheck the opened descriptor, including parent-directory symlink races.
        if Path(f'/proc/self/fd/{fd}').resolve(strict=True) != target:
            raise APIError(409, 'The file changed while opening. Try the download again.')
        file = os.fdopen(fd, 'rb'); fd = None
        return file, info.st_size
    except PermissionError:
        raise APIError(403, 'The server cannot read this file. Check its permissions.')
    except (OSError, ValueError, RuntimeError):
        raise APIError(404, 'The file is missing, moved, or no longer readable. Ask Codex to generate it again.')
    finally:
        if fd is not None:
            os.close(fd)


def download_range(header, size):
    if not header:
        return 0, size, False
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', header.strip())
    if not match or not any(match.groups()) or not size:
        raise ValueError('Invalid range')
    first, last = match.groups()
    if not first:
        start, end = max(0, size - int(last)), size
    else:
        start, end = int(first), min(size, int(last) + 1) if last else size
    if not 0 <= start < end <= size:
        raise ValueError('Unsatisfiable range')
    return start, end, True
