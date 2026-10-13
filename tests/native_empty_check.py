"""Opt-in native daemon regression with a local HTTP model fixture (no paid API calls).

Run: .venv/bin/python tests/native_empty_check.py --native --codex /path/to/codex
Starts and stops only its own daemon at a temporary socket; never uses the normal daemon.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_remote.features import RemoteGateway
from codex_remote.transport import SocketRPC


def until(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError('Native empty-thread check timed out')
        time.sleep(.05)


class LocalModel(BaseHTTPRequestHandler):
    gate = threading.Event()
    requests_seen = 0

    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', '0')))
        if self.path != '/v1/responses':
            self.send_error(404)
            return
        type(self).requests_seen += 1
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True

        def emit(kind, **body):
            self.wfile.write(('event: '+kind+'\ndata: '+json.dumps({'type': kind, **body})+'\n\n').encode())
            self.wfile.flush()

        response = {'id': 'resp_empty_fixture', 'object': 'response', 'created_at': int(time.time()), 'status': 'in_progress', 'output': []}
        emit('response.created', response=response)
        if not self.gate.wait(20):
            return
        item = {'id': 'msg_empty_fixture', 'type': 'message', 'role': 'assistant', 'status': 'in_progress', 'content': []}
        emit('response.output_item.added', output_index=0, item=item)
        part = {'type': 'output_text', 'text': '', 'annotations': []}
        emit('response.content_part.added', item_id=item['id'], output_index=0, content_index=0, part=part)
        emit('response.output_text.delta', item_id=item['id'], output_index=0, content_index=0, delta='EMPTY_THREAD_OK')
        part['text'] = 'EMPTY_THREAD_OK'
        emit('response.output_text.done', item_id=item['id'], output_index=0, content_index=0, text=part['text'])
        emit('response.content_part.done', item_id=item['id'], output_index=0, content_index=0, part=part)
        item.update(status='completed', content=[part])
        emit('response.output_item.done', output_index=0, item=item)
        response.update(status='completed', output=[item], usage={'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2,
                                                                'input_tokens_details': {'cached_tokens': 0},
                                                                'output_tokens_details': {'reasoning_tokens': 0}})
        emit('response.completed', response=response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native', action='store_true')
    parser.add_argument('--codex', required=True)
    args = parser.parse_args()
    if not args.native:
        parser.error('Pass --native to start a dedicated test daemon with a local model fixture.')
    LocalModel.gate.clear()
    LocalModel.requests_seen = 0
    model = ThreadingHTTPServer(('127.0.0.1', 0), LocalModel)
    worker = threading.Thread(target=model.serve_forever, daemon=True)
    worker.start()
    flags = []
    provider = 'remote_local_fixture'
    flags += ['-c', 'model_provider='+json.dumps(provider), '-c', 'model="fixture-model"',
              '-c', 'model_providers.'+provider+'={name="Local test fixture", base_url="http://127.0.0.1:'+str(model.server_port)+'/v1", wire_api="responses", requires_openai_auth=false, supports_websockets=false}']
    try:
        with tempfile.TemporaryDirectory(prefix='codex-remote-empty-') as directory:
            root = Path(directory)
            socket = root/'test.sock'
            state, work = root/'gateway', root/'workspace'
            state.mkdir(); work.mkdir()
            with (root/'daemon.log').open('w+') as log:
                test_env = {k: v for k, v in os.environ.items() if k in ('PATH', 'LANG', 'LC_ALL', 'TMPDIR')}
                test_env['CODEX_HOME'] = str(root/'native-home')
                (root/'native-home').mkdir()
                for name in ('NO_PROXY', 'no_proxy'):
                    test_env[name] = ','.join(filter(None, [test_env.get(name, ''), '127.0.0.1', 'localhost']))
                daemon = subprocess.Popen([args.codex, *flags, 'app-server', '--listen', 'unix://'+str(socket)], stdout=log, stderr=log, env=test_env)
                original = gateway = tid = None
                try:
                    until(lambda: socket.exists() or daemon.poll() is not None)
                    assert daemon.poll() is None, 'Dedicated daemon failed to start'
                    original = SocketRPC(str(socket), lambda m: None, lambda: None)
                    original.call('initialize', {'clientInfo': {'name': 'empty_native_fixture', 'version': '1'}, 'capabilities': {'experimentalApi': True}})
                    original.notify('initialized')
                    meta = original.call('thread/start', {'cwd': str(work), 'historyMode': 'paginated', 'sandbox': 'read-only', 'approvalPolicy': 'on-request', 'modelProvider': provider, 'model': 'fixture-model'})['thread']
                    tid = meta['id']
                    gateway = RemoteGateway(state, [work], str(socket), rpc_factory=SocketRPC, shared=True, background=False)
                    gateway.import_thread(tid)
                    preview = gateway.preview(tid)
                    assert preview['turns'] == [] and preview['materialized'] is False
                    gateway.enable_control(tid)
                    assert tid in gateway.awaiting_materialization and tid not in gateway.loaded
                    assert gateway.queue_for(tid) == []
                    gateway.attach(tid, force=True)
                    result = gateway.send(tid, 'Return the local fixture response.', 'native-empty-first-message-123')
                    assert result['turnId']
                    assert tid in gateway.loaded, 'First message did not establish live subscription'
                    LocalModel.gate.set()
                    try:
                        until(lambda: gateway.thread(tid)['turn_id'] is None)
                    except AssertionError:
                        print('Local fixture requests:', LocalModel.requests_seen, flush=True)
                        diagnostic = [json.loads(r['payload']) for r in gateway.store.rows('SELECT payload FROM events WHERE thread_id=?', (tid,))]
                        print('Native test event types:', [e['method'] for e in diagnostic], flush=True)
                        print('Native test errors:', [e for e in diagnostic if e['method'] in ('error', 'gateway/error')], flush=True)
                        raise
                    turns = gateway.history(tid)['turns']
                    assert len(turns) == 1, turns
                    assert any(i.get('text') == 'EMPTY_THREAD_OK' for i in turns[0]['items'])
                    events = [json.loads(r['payload']) for r in gateway.store.rows('SELECT payload FROM events WHERE thread_id=?', (tid,))]
                    assert any(e['method'] == 'item/agentMessage/delta' and 'EMPTY_THREAD_OK' in e['params'].get('delta', '') for e in events)
                    assert LocalModel.requests_seen == 1
                    gateway.close(); gateway.store.close(); gateway = None
                    assert tid in original.call('thread/loaded/list', {})['data']
                    print('PASS: native empty-thread preview, deferred connection, SAME-ID first send, real event subscription, streamed local fixture reply, original peer preserved. No paid API calls.')
                finally:
                    LocalModel.gate.set()
                    if gateway:
                        gateway.close(); gateway.store.close()
                    if original:
                        if tid:
                            try:
                                original.call('thread/archive', {'threadId': tid})
                            except Exception:
                                pass
                        original.close()
                    daemon.terminate()
                    try:
                        daemon.wait(8)
                    except subprocess.TimeoutExpired:
                        daemon.kill(); daemon.wait()
    finally:
        model.shutdown(); model.server_close(); worker.join(2)


if __name__ == '__main__':
    main()
