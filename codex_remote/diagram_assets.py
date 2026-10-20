"""Serve the sandbox as one compressed document, without child script requests."""
import base64
from functools import lru_cache
import gzip
import hashlib
from pathlib import Path
import re
import threading

_LOCK = threading.Lock()
_ASSETS = ('mermaid-frame.html', 'vendor/mermaid/mermaid.min.js', 'mermaid-frame.js')


def renderer_document(static: Path):
    # File signatures allow frontend-only updates without retaining stale scripts.
    with _LOCK:
        signature = tuple((p.stat().st_mtime_ns, p.stat().st_size)
                          for p in (static / name for name in _ASSETS))
        return _build(static, signature)


@lru_cache(maxsize=2)
def _build(static, signature):
    html = (static / _ASSETS[0]).read_text(encoding='utf-8')
    hashes = []
    for name in _ASSETS[1:]:
        script = (static / name).read_text(encoding='utf-8')
        # Reject HTML parser control tokens in future vendor updates. Never
        # rewrite arbitrary JavaScript or let it break out of its script element.
        if re.search(r'</?script\b', script, re.IGNORECASE):
            raise ValueError('Bundled diagram script contains an HTML script delimiter')
        digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode('ascii')
        hashes.append("'sha256-" + digest + "'")
        marker = '<script src="/' + name + '" defer></script>'
        if html.count(marker) != 1:
            raise ValueError('Missing diagram script placeholder')
        html = html.replace(marker, '<script>' + script + '</script>')
    data = html.encode('utf-8')
    policy = ("default-src 'none'; script-src " + ' '.join(hashes) +
              "; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; "
              "font-src 'none'; frame-ancestors 'self'; base-uri 'none'; "
              "form-action 'none'; sandbox allow-scripts")
    return data, gzip.compress(data, compresslevel=6, mtime=0), policy


def accepts_gzip(header):
    # An explicit gzip;q=0 takes precedence over a wildcard.
    encodings = {}
    for item in header.lower().split(','):
        name, *params = item.strip().split(';')
        quality = 1.0
        for param in params:
            key, _, value = param.strip().partition('=')
            if key == 'q':
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        encodings[name] = 0 < quality <= 1
    return encodings.get('gzip', encodings.get('*', False))
