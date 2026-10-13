"""Opt-in real-daemon integration check. Sends two small model requests in read-only mode."""
import argparse
import json
from pathlib import Path
import os
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codex_remote.features import RemoteGateway
from codex_remote.transport import SocketRPC
from codex_remote.server import APIError


def wait_for(predicate, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.2)
    raise RuntimeError('Timed out waiting for real Codex')


def completed_reply(gateway, tid, expected):
    return any(turn.get('status') == 'completed' and
               any(item.get('type') == 'agentMessage' and expected in item.get('text', '')
                   for item in turn.get('items', []))
               for turn in gateway.history(tid)['turns'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Allow two real model requests using existing server credentials')
    parser.add_argument('--socket', required=True, type=Path, help='Dedicated test daemon socket; never the normal terminal daemon')
    args = parser.parse_args()
    if not args.live:
        parser.error('Pass --live to explicitly enable real model requests.')
    homes = {Path.home()/'.codex', Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex')))}
    normal_sockets = {(home/'app-server-control/app-server-control.sock').resolve() for home in homes}
    if args.socket.resolve() in normal_sockets:
        parser.error('Refusing to run integration tests against the normal terminal daemon. Use a dedicated test daemon.')
    socket_path = str(args.socket.resolve(strict=True))
    with tempfile.TemporaryDirectory(prefix='codex-remote-live-') as directory:
        root = Path(directory); workspace = root/'workspace'; workspace.mkdir()
        for name in ('a', 'b'): (root/name).mkdir()
        a = RemoteGateway(root/'a', [workspace], socket_path, sandbox='read-only', rpc_factory=SocketRPC, shared=True, background=False)
        b = RemoteGateway(root/'b', [workspace], socket_path, sandbox='read-only', rpc_factory=SocketRPC, shared=True, background=False)
        tid = None
        try:
            tid = a.new_thread(str(workspace), 'Remote integration fixture')['id']
            a.send(tid, 'Reply with exactly REMOTE_SYNC_OK. Do not use tools or read files.', 'live-two-client-initial-123')
            b.import_thread(tid)
            b.attach(tid)
            print('PASS: second client attached to the same real server thread', flush=True)
            wait_for(lambda: completed_reply(b, tid, 'REMOTE_SYNC_OK'))
            for gateway in (a, b):
                history = gateway.history(tid)['turns']
                replies = [i.get('text', '') for t in history for i in t.get('items', []) if i['type'] == 'agentMessage']
                assert any('REMOTE_SYNC_OK' in text for text in replies), 'Missing real reply'
            print('PASS: both clients read the same real model reply', flush=True)
            result = a.send(tid, 'Reply with exactly REMOTE_QUEUE_OK. Do not use tools or read files.', 'live-native-queue-1234', mode='queue')
            peer_queue = b.queue_for(tid)
            # An idle daemon may already have consumed the queue before list returns.
            if peer_queue:
                assert any(q['id'] == result['id'] for q in peer_queue), 'Unexpected native queue identity'
            print('PASS: native queue accepted the request', flush=True)
            a._drain_queue()
            # Queue consumption can precede turn/started delivery; an empty queue and
            # stale idle status don't prove that the queued request has finished.
            wait_for(lambda: completed_reply(b, tid, 'REMOTE_QUEUE_OK'))
            history = b.history(tid)['turns']
            replies = [i.get('text', '') for t in history for i in t.get('items', []) if i['type'] == 'agentMessage']
            assert any('REMOTE_QUEUE_OK' in text for text in replies), 'Queued request did not execute'
            print('PASS: native queue executed and peer received result', flush=True)
            b.close()
            b.store.close()
            b = RemoteGateway(root/'b', [workspace], socket_path, rpc_factory=SocketRPC, shared=True, background=False)
            b.attach(tid)
            assert len(b.history(tid)['turns']) >= 2
            print('PASS: gateway reconnect preserved shared daemon and full history', flush=True)
        finally:
            if tid:
                try:
                    a.interrupt(tid)
                    a.rpc.call('thread/archive', {'threadId': tid})
                except Exception:
                    pass
            a.close(); b.close(); a.store.close(); b.store.close()


if __name__ == '__main__':
    main()
