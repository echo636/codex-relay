import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_remote.server import Gateway, WebServer, VENDOR_FILES


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.work = self.root / 'workspace'
        self.work.mkdir()
        self.state = self.root / 'state'
        self.state.mkdir()
        self.command = [sys.executable, str(Path(__file__).with_name('fake_codex.py'))]
        self.gateway = Gateway(self.state, [self.work], self.command)
        self.token = 'fixture-token-' + 'x' * 32
        self.web = WebServer(('127.0.0.1', 0), self.gateway, self.token, '')
        self.port = self.web.server_port
        self.web.origin = f'http://127.0.0.1:{self.port}'
        self.worker = threading.Thread(target=self.web.serve_forever, daemon=True)
        self.worker.start()
        status, _, headers = self.request('/api/login', {'token': self.token})
        self.assertEqual(status, 200)
        self.cookie = headers['Set-Cookie'].split(';')[0]

    def tearDown(self):
        self.web.stopping.set()
        self.web.shutdown()
        self.web.server_close()
        self.gateway.rpc.close()
        self.worker.join(2)
        # Allow disconnected streaming handlers to observe the stop flag.
        with self.gateway.wake:
            self.gateway.wake.notify_all()
        time.sleep(0.03)
        self.gateway.store.close()
        self.temp.cleanup()

    def request(self, path, body=None, cookie=None, origin=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        headers = {}
        if cookie is not None:
            headers['Cookie'] = cookie
        if origin is not None:
            headers['Origin'] = origin
        if body is not None:
            headers['Content-Type'] = 'application/json'
        conn.request('POST' if body is not None else 'GET', path,
                     json.dumps(body) if body is not None else None, headers)
        response = conn.getresponse()
        raw = response.read()
        result = json.loads(raw) if response.getheader('Content-Type', '').startswith('application/json') else raw
        value = response.status, result, dict(response.getheaders())
        conn.close()
        return value

    def new(self):
        status, thread, _ = self.request('/api/threads', {'cwd': str(self.work)}, self.cookie)
        self.assertEqual(status, 201, thread)
        return thread['id']

    def send(self, tid, text='hello', key='fixture-' + 's' * 16):
        return self.request(f'/api/threads/{tid}/message', {'text': text, 'submissionId': key}, self.cookie)

    def until(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail('Timed out waiting for fixture')

    def stream(self, tid, after=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        headers = {'Cookie': self.cookie}
        if after is not None:
            headers['Last-Event-ID'] = str(after)
        conn.request('GET', f'/api/threads/{tid}/events', headers=headers)
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        events = []
        current = {}
        while True:
            line = response.readline().decode().rstrip('\r\n')
            if not line:
                if current.get('event') == 'state':
                    break
                if current:
                    events.append(current)
                current = {}
            elif ': ' in line:
                k, v = line.split(': ', 1)
                current[k] = json.loads(v) if k == 'data' else v
        conn.close()
        return events, current['data']

    def test_auth_origin_cookie_and_no_arbitrary_rpc(self):
        self.assertEqual(self.request('/api/bootstrap')[0], 401)
        self.assertEqual(self.request('/api/login', {'token': 'wrong'})[0], 401)
        self.assertEqual(self.request('/api/bootstrap', cookie=self.cookie, origin='https://evil.example')[0], 403)
        self.assertEqual(self.request('/api/rpc', {'method': 'command/exec'}, self.cookie)[0], 404)
        self.assertEqual(self.request('/api/bootstrap', cookie=self.cookie)[0], 200)
        self.request('/api/logout', {}, self.cookie)
        self.assertEqual(self.request('/api/bootstrap', cookie=self.cookie)[0], 401)

    def test_login_rate_limit(self):
        for _ in range(10):
            self.assertEqual(self.request('/api/login', {'token': 'wrong'})[0], 401)
        self.assertEqual(self.request('/api/login', {'token': 'wrong'})[0], 429)
        self.assertEqual(self.request('/api/login', {'token': self.token})[0], 200)

    def test_workspace_allowlist_and_unknown_threads(self):
        self.assertEqual(self.request('/api/threads', {'cwd': '/tmp'}, self.cookie)[0], 400)
        self.assertEqual(self.send('unknown')[0], 404)
        tid = self.new()
        self.gateway.workspaces = []
        self.assertEqual(self.send(tid)[0], 404)

    def test_execution_survives_no_browser_and_duplicate_is_not_executed(self):
        tid = self.new()
        first = self.send(tid)
        self.assertEqual(first[0], 200)
        second = self.send(tid)
        self.assertEqual(first[1], second[1])
        self.assertEqual(self.send(tid, 'different')[0], 409)
        self.until(lambda: self.gateway.thread(tid)['status'] == 'completed')
        rows = self.gateway.store.rows('SELECT payload FROM events WHERE thread_id=?', (tid,))
        self.assertEqual(sum(json.loads(r['payload'])['method'] == 'turn/started' for r in rows), 1)
        self.assertNotIn('AUTH_SENTINEL', str(rows))

    def test_two_devices_and_resume_cursor(self):
        tid = self.new()
        self.send(tid)
        self.until(lambda: self.gateway.thread(tid)['status'] == 'completed')
        first, snapshot = self.stream(tid)
        second, _ = self.stream(tid)
        self.assertEqual(first, second)
        self.assertEqual(snapshot['thread']['status'], 'completed')
        cursor = int(first[1]['id'])
        resumed, _ = self.stream(tid, cursor)
        self.assertEqual(resumed, first[2:])

    def test_approval_is_pending_until_answered_and_cannot_be_reused(self):
        tid = self.new()
        self.send(tid, 'approval')
        self.until(lambda: bool(self.gateway.pending_requests(tid)))
        request = self.gateway.pending_requests(tid)[0]
        self.assertEqual(self.gateway.thread(tid)['status'], 'running')
        self.assertEqual(self.send(tid, 'next', 'another-submission-123')[0], 409)
        path = '/api/requests/' + request['key'] + '/respond'
        self.assertEqual(self.request(path, {'decision': 'acceptForSession'}, self.cookie)[0], 400)
        self.assertEqual(self.request(path, {'decision': 'decline'}, self.cookie)[0], 200)
        self.assertEqual(self.request(path, {'decision': 'accept'}, self.cookie)[0], 409)
        self.until(lambda: self.gateway.thread(tid)['status'] == 'completed')

    def test_question_answer_shape(self):
        tid = self.new()
        self.send(tid, 'question')
        self.until(lambda: bool(self.gateway.pending_requests(tid)))
        key = self.gateway.pending_requests(tid)[0]['key']
        path = '/api/requests/' + key + '/respond'
        self.assertEqual(self.request(path, {'answers': {}}, self.cookie)[0], 400)
        self.assertEqual(self.request(path, {'answers': {'choice': 'Option A'}}, self.cookie)[0], 200)
        self.until(lambda: self.gateway.thread(tid)['status'] == 'completed')

    def test_interrupt_and_process_loss(self):
        tid = self.new()
        self.send(tid, 'hang')
        self.assertEqual(self.request(f'/api/threads/{tid}/interrupt', {}, self.cookie)[0], 200)
        self.until(lambda: self.gateway.thread(tid)['status'] == 'interrupted')
        self.send(tid, 'hang', 'second-submission-1234')
        self.gateway.rpc.close()
        self.until(lambda: not self.gateway.online and self.gateway.thread(tid)['turn_id'] is None)
        self.assertEqual(self.gateway.thread(tid)['status'], 'interrupted')

    def test_restart_keeps_history_and_allows_continuation(self):
        tid = self.new()
        self.send(tid)
        self.until(lambda: self.gateway.thread(tid)['status'] == 'completed')
        count = len(self.gateway.store.rows('SELECT seq FROM events'))
        self.gateway.rpc.close()
        self.until(lambda: not self.gateway.online)
        self.gateway.store.close()
        self.gateway = Gateway(self.state, [self.work], self.command)
        self.web.gateway = self.gateway
        self.assertEqual(len(self.gateway.store.rows('SELECT seq FROM events')), count)
        self.assertEqual(self.send(tid, 'continue', 'continue-submission-123')[0], 200)
        self.until(lambda: self.gateway.thread(tid)['status'] == 'completed')

    def test_mermaid_document_is_compressed_self_contained_and_hash_restricted(self):
        import base64
        import gzip
        import hashlib
        import re

        status, plain, headers = self.request('/mermaid-frame.html')
        self.assertEqual(status, 200)
        self.assertNotIn(b'<script src=', plain)
        self.assertEqual(headers['Vary'], 'Accept-Encoding')
        scripts = re.findall(rb'<script>(.*?)</script>', plain, re.DOTALL)
        self.assertEqual(len(scripts), 2)
        policy = headers['Content-Security-Policy']
        for script in scripts:
            digest = base64.b64encode(hashlib.sha256(script).digest()).decode()
            self.assertIn("'sha256-" + digest + "'", policy)
        self.assertNotIn("script-src 'self'", policy)
        self.assertNotIn("unsafe-eval", policy)
        self.assertIn("sandbox allow-scripts", policy)
        for accept, compressed in [('gzip', True), ('br, gzip;q=0.8', True),
                                   ('gzip;q=0, *;q=1', False), ('identity', False)]:
            conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
            conn.request('GET', '/mermaid-frame.html', headers={'Accept-Encoding': accept})
            response = conn.getresponse()
            body = response.read()
            self.assertEqual(response.status, 200)
            self.assertEqual(int(response.getheader('Content-Length')), len(body))
            self.assertEqual(response.getheader('Content-Encoding'), 'gzip' if compressed else None)
            if compressed:
                self.assertEqual(gzip.decompress(body), plain)
                self.assertLess(len(body), len(plain) * 0.4)
            else:
                self.assertEqual(body, plain)
            conn.close()

    def test_static_assets_and_no_private_cache(self):
        for path in ['/', '/app.js', '/features.js', '/theme.js', '/activity.js', '/markdown.js', '/diagrams.js', '/mermaid-frame.js', '/commands.js', '/commands.css', '/style.css', '/manifest.webmanifest', '/sw.js', '/icon-192.png', '/icon-512.png', *sorted(VENDOR_FILES)]:
            status, _, headers = self.request(path)
            self.assertEqual(status, 200, path)
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertIn("script-src 'self';", headers['Content-Security-Policy'])
            self.assertIn("font-src 'self';", headers['Content-Security-Policy'])
            if path.endswith('.woff2'):
                self.assertEqual(headers['Content-Type'], 'font/woff2')
        status, _, headers = self.request('/mermaid-frame.html')
        self.assertEqual(status, 200)
        self.assertIn('sandbox allow-scripts', headers['Content-Security-Policy'])
        self.assertIn("connect-src 'none'", headers['Content-Security-Policy'])
        self.assertNotIn('allow-same-origin', headers['Content-Security-Policy'])
        for path in ['/mermaid-frame.js', '/vendor/mermaid/mermaid.min.js']:
            self.assertEqual(self.request(path, origin='null')[0], 200)
        self.assertEqual(self.request('/api/bootstrap', cookie=self.cookie, origin='null')[0], 403)
        self.assertEqual(self.request('/../server.py')[0], 404)
        for path in ['/vendor/../server.py', '/vendor/katex/fonts/../../../../server.py', '/vendor/katex/fonts/%2e%2e/server.py', '/vendor/packages.json']:
            self.assertEqual(self.request(path)[0], 404)


if __name__ == '__main__':
    unittest.main()
