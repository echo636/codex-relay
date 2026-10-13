"""Opt-in dedicated Codex + local model fixture; no paid API or user-thread mutations."""
import argparse
from http.server import ThreadingHTTPServer
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
from native_empty_check import LocalModel


class HeaderModel(LocalModel):
    agents = []

    def do_POST(self):
        agent = self.headers.get('User-Agent', '')
        type(self).agents.append(agent)
        if not agent.startswith('codex_cli_rs/') or self.headers.get('originator') != 'codex_cli_rs':
            self.rfile.read(int(self.headers.get('Content-Length', '0')))
            self.send_error(403, 'Client not allowed')
            return
        super().do_POST()


def main(binary):
    model = ThreadingHTTPServer(('127.0.0.1', 0), HeaderModel)
    worker = threading.Thread(target=model.serve_forever, daemon=True); worker.start()
    flags = []
    provider = 'remote_identity_fixture'
    flags += ['-c', 'model_provider=' + json.dumps(provider), '-c', 'model="fixture-model"',
              '-c', 'model_providers.' + provider + '={name="Local identity fixture", base_url="http://127.0.0.1:' + str(model.server_port) + '/v1", wire_api="responses", requires_openai_auth=false, supports_websockets=false}']
    try:
        with tempfile.TemporaryDirectory(prefix='codex-identity-fixture-') as directory:
            root = Path(directory); socket = root/'test.sock'
            for name in ('sqlite', 'workspace', 'gateway'):
                (root/name).mkdir()
            with (root/'native.log').open('w+') as log:
                env = {k: v for k, v in os.environ.items() if k in ('PATH', 'LANG', 'LC_ALL', 'TMPDIR')}
                env['CODEX_HOME'] = str(root/'native-home')
                (root/'native-home').mkdir()
                for key in ('NO_PROXY', 'no_proxy'):
                    env[key] = ','.join(filter(None, [env.get(key, ''), '127.0.0.1', 'localhost']))
                daemon = subprocess.Popen([binary, *flags, '-c', 'sqlite_home='+json.dumps(str(root/'sqlite')),
                                           'app-server', '--listen', 'unix://'+str(socket)], stdout=log, stderr=log, env=env)
                gateway = peer = None
                try:
                    deadline = time.monotonic()+60
                    while not socket.exists() and daemon.poll() is None and time.monotonic() < deadline:
                        time.sleep(.1)
                    assert socket.exists(), 'Dedicated native fixture did not start'
                    gateway = RemoteGateway(root/'gateway', [root/'workspace'], str(socket), rpc_factory=SocketRPC, shared=True, background=False)
                    assert gateway.info['userAgent'].startswith('codex_cli_rs/'), gateway.info['userAgent']
                    peer = SocketRPC(str(socket), lambda _: None, lambda: None)
                    peer.call('initialize', {'clientInfo': {'name': 'codex_cli_rs', 'version': 'fixture'}, 'capabilities': {'experimentalApi': True}})
                    peer.notify('initialized')
                    native = gateway.rpc.call('thread/start', {'cwd': str(root/'workspace'), 'ephemeral': True,
                        'model': 'fixture-model', 'modelProvider': provider, 'sandbox': 'read-only', 'approvalPolicy': 'never'})['thread']
                    tid = native['id']; gateway._adopt({**native, 'name': 'Identity fixture'}); gateway.loaded.add(tid)
                    gateway.store.execute('UPDATE threads SET selected=1 WHERE id=?', (tid,))
                    peer.close(); peer = None  # Simulate closing the terminal before the next turn.
                    HeaderModel.gate.set()
                    gateway.send(tid, 'Reply with the local fixture message.', 'native-identity-after-cli-close')
                    deadline = time.monotonic()+60
                    while gateway.thread(tid)['status'] not in ('completed', 'failed') and time.monotonic() < deadline:
                        time.sleep(.1)
                    assert gateway.thread(tid)['status'] == 'completed', gateway.thread(tid)
                    assert HeaderModel.agents and all(a.startswith('codex_cli_rs/') for a in HeaderModel.agents)
                    events = [json.loads(row['payload']) for row in gateway.store.rows('SELECT payload FROM events WHERE thread_id=?', (tid,))]
                    assert any(e['method'] == 'item/agentMessage/delta' and 'EMPTY_THREAD_OK' in e.get('params', {}).get('delta', '') for e in events)
                    print('PASS: native upstream CLI User-Agent, gateway-first initialization, terminal disconnect, and completed streamed reply from local model fixture.')
                finally:
                    HeaderModel.gate.set()
                    if peer: peer.close()
                    if gateway: gateway.close(); gateway.store.close()
                    daemon.terminate()
                    try: daemon.wait(timeout=8)
                    except subprocess.TimeoutExpired: daemon.kill(); daemon.wait(timeout=5)
    finally:
        model.shutdown(); model.server_close(); worker.join(2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native', action='store_true')
    parser.add_argument('--codex', required=True)
    args = parser.parse_args()
    if not args.native: parser.error('Explicit --native opt-in is required.')
    main(args.codex)
