"""Downloads authorize explicit assistant links and stream regular files only."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import unittest
from urllib.parse import urlencode, quote
from unittest.mock import patch

import test_remote
from codex_remote.artifacts import file_links, local_link_path


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.h = test_remote.RemoteTests(); self.h.setUp()
        self.tid = self.h.new(); self.h.message(self.tid, 'hang', 'artifact-fixture-start')
        self.turn = self.h.gateway.thread(self.tid)['turn_id']

    def tearDown(self):
        self.h.tearDown()

    def item(self, text, item_id='artifact-message', kind='agentMessage'):
        item = {'id': item_id, 'type': kind, 'text': text}
        self.h.gateway.rpc.call('fixture/event', {'method': 'item/completed', 'params': {'threadId': self.tid, 'turnId': self.turn, 'item': item}})
        # Synchronize fixture event delivery without making a native mutation.
        self.h.gateway.history(self.tid)

    def request(self, href, *, item_id='artifact-message', tid=None, headers=None, info=False, authenticated=True):
        path = f'/api/threads/{tid or self.tid}/artifact?' + urlencode({'itemId': item_id, 'path': href, **({'info': '1'} if info else {})})
        conn = http.client.HTTPConnection('127.0.0.1', self.h.port, timeout=10)
        conn.request('GET', path, headers={**({'Cookie': self.h.cookie} if authenticated else {}), **(headers or {})})
        response = conn.getresponse(); data = response.read(); result = response.status, data, dict(response.getheaders()); conn.close()
        return result

    def test_markdown_destinations_and_line_references(self):
        text = '''[relative](outputs/report.pdf:12:4) [absolute](</tmp/report (final).pdf>)
[sandbox](sandbox:/tmp/report.pdf) ![picture](./image.png)
[reference][result] <file:///tmp/raw.txt>

[result]: outputs/report%20final.pdf#L8

`[code](secret.txt)`
```
[not a link](private.key)
```
[external](https://example.org/a) [bad](javascript:alert%281%29)
'''
        self.assertEqual(file_links(text), {'outputs/report.pdf', '/tmp/report (final).pdf', '/tmp/report.pdf', './image.png', 'outputs/report final.pdf', '/tmp/raw.txt'})
        self.assertEqual(local_link_path('file://localhost/tmp/a%23b%3Fc.txt#L9'), '/tmp/a#b?c.txt')
        for value in ('#section', '?query', '//example.org/path', 'file://other/path', 'javascript:alert(1)', 'data:text/plain,test', '/api/bootstrap', '/tmp/a%00b'):
            self.assertIsNone(local_link_path(value), value)

    def test_linked_file_outside_workspace_and_auth_boundaries(self):
        outside = self.h.root / '结果 (final).txt'; outside.write_text('artifact content')
        href = 'sandbox:' + quote(str(outside), safe='/')
        self.item(f'[Download](<{href}>)')
        code, data, headers = self.request(href)
        self.assertEqual((code, data), (200, b'artifact content'))
        self.assertIn("filename*=UTF-8''", headers['Content-Disposition'])
        self.assertTrue(headers['Content-Disposition'].startswith('attachment;'))
        self.assertEqual(headers['Content-Type'], 'application/octet-stream')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(json.loads(self.request(href, info=True)[1]), {'name': outside.name, 'size': outside.stat().st_size})
        self.assertEqual(self.request(href, authenticated=False)[0], 401)
        self.assertEqual(self.request(href, headers={'Origin': 'https://example.org'})[0], 403)
        self.assertEqual(self.request(str(self.h.root / 'codex-state.json'))[0], 404)
        other = self.h.new()
        self.assertEqual(self.request(href, tid=other)[0], 404)
        self.item(f'[fake]({href})', item_id='user-reference', kind='userMessage')
        self.assertEqual(self.request(href, item_id='user-reference')[0], 404)
        # Browsing an unselected saved conversation does not resume it.
        self.h.gateway.shared = True
        self.h.gateway.store.execute('UPDATE threads SET selected=0 WHERE id=?', (self.tid,))
        with patch.object(self.h.gateway.rpc, 'call', wraps=self.h.gateway.rpc.call) as rpc:
            self.assertEqual(self.request(href)[0], 200)
        rpc.assert_not_called()

    def test_large_download_ranges_and_bounded_preview(self):
        path = self.h.work / 'large.bin'
        content = bytes(range(256)) * (48 * 1024 + 1)
        path.write_bytes(content); self.item('[Binary](large.bin)')
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('Download must stream')):
            code, data, headers = self.request('large.bin')
        self.assertEqual(code, 200)
        self.assertEqual(hashlib.sha256(data).digest(), hashlib.sha256(content).digest())
        self.assertEqual(int(headers['Content-Length']), len(content))
        self.assertEqual(headers['Accept-Ranges'], 'bytes')
        for request, start, end in [('bytes=7-20', 7, 21), ('bytes=-17', len(content)-17, len(content)), ('bytes=100-', 100, len(content))]:
            code, data, headers = self.request('large.bin', headers={'Range': request})
            self.assertEqual(code, 206); self.assertEqual(data, content[start:end])
            self.assertEqual(headers['Content-Range'], f'bytes {start}-{end-1}/{len(content)}')
        for request in ('bytes=999999999-', 'bytes=5-2', 'bytes=-0', 'bytes=0-1,3-4', 'invalid'):
            self.assertEqual(self.request('large.bin', headers={'Range': request})[0], 416)
        url = '/api/files?' + urlencode({'threadId': self.tid, 'path': 'large.bin'})
        code, preview, _ = self.h.request(url, cookie=self.h.cookie)
        self.assertEqual(code, 200); self.assertTrue(preview['truncated'])
        code, downloaded, _ = self.h.request(url + '&download=1', cookie=self.h.cookie)
        self.assertEqual(code, 200); self.assertEqual(downloaded, content)

    def test_streaming_history_and_unreadable_files(self):
        path = self.h.work / 'empty.txt'; path.touch()
        self.item('[Empty](empty.txt) [Missing](missing.txt) [Directory](.) [Pipe](pipe)')
        os.mkfifo(self.h.work / 'pipe')
        self.assertEqual(self.request('empty.txt')[:2], (200, b''))
        self.assertEqual(self.request('missing.txt')[0], 404)
        self.assertEqual(self.request('.')[0], 400)
        self.assertEqual(self.request('pipe')[0], 400)
        path.write_text('streamed')
        for method, params in [
            ('item/started', {'item': {'id': 'live-link', 'type': 'agentMessage', 'text': ''}}),
            ('item/agentMessage/delta', {'itemId': 'live-link', 'delta': '[Download](empty.txt)'}),
        ]:
            self.h.gateway.rpc.call('fixture/event', {'method': method, 'params': {'threadId': self.tid, 'turnId': self.turn, **params}})
        self.assertEqual(self.request('empty.txt', item_id='live-link')[:2], (200, b'streamed'))
        self.h.gateway.store.execute('DELETE FROM file_links')
        self.h.gateway.history(self.tid)
        self.assertEqual(self.request('empty.txt')[:2], (200, b'streamed'))


if __name__ == '__main__': unittest.main()
