import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from urllib.parse import urlencode
from unittest.mock import patch

import test_gateway
from codex_remote.features import RemoteGateway
from codex_remote.web import RemoteWebServer
from codex_remote.push import PushManager
from codex_remote.server import APIError


class RemoteTests(test_gateway.GatewayTests):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.work = self.root / 'workspace'; self.work.mkdir()
        self.state = self.root / 'state'; self.state.mkdir()
        self.command = [sys.executable, str(Path(__file__).with_name('fake_codex.py')), str(self.root / 'codex-state.json')]
        self.gateway = RemoteGateway(self.state, [self.work], self.command, background=False)
        self.token = 'fixture-token-' + 'x' * 32
        self.web = RemoteWebServer(('127.0.0.1', 0), self.gateway, self.token, '')
        self.port = self.web.server_port
        self.web.origin = f'http://127.0.0.1:{self.port}'
        self.worker = threading.Thread(target=self.web.serve_forever, daemon=True); self.worker.start()
        status, _, headers = self.request('/api/login', {'token': self.token})
        self.assertEqual(status, 200)
        self.cookie = headers['Set-Cookie'].split(';')[0]

    def message(self, tid, text, key, **extra):
        return self.request(f'/api/threads/{tid}/message', {'text': text, 'submissionId': key, **extra}, self.cookie)

    def test_initial_sync_skips_old_event_replay_and_keeps_history_pagination(self):
        tid = self.new()
        for index in range(12):
            self.send(tid, f'turn {index}', f'sync-fixture-turn-{index}')
            self.until(lambda: self.gateway.thread(tid)['status'] == 'completed')
        old = json.dumps({'method': 'item/agentMessage/delta', 'params': {'threadId': tid, 'itemId': 'old', 'delta': 'old replay'}})
        with self.gateway.store.lock, self.gateway.store.db:
            self.gateway.store.db.executemany('INSERT INTO events(thread_id,payload) VALUES(?,?)', [(tid, old)] * 3000)
        with patch.object(self.gateway.rpc, 'call', wraps=self.gateway.rpc.call) as rpc:
            code, sync, _ = self.request(f'/api/threads/{tid}/sync', cookie=self.cookie)
            self.assertEqual(code, 200, sync)
            self.assertEqual([c.args[0] for c in rpc.call_args_list], ['thread/turns/list'])
            self.assertEqual(rpc.call_args.args[1]['limit'], 10)
        self.assertEqual(len(sync['turns']), 10)
        self.assertEqual(sync['nextCursor'], '10')
        self.assertEqual(len(self.gateway.history(tid, sync['nextCursor'])['turns']), 2)
        self.assertEqual(sync['after'], self.gateway.store.rows('SELECT MAX(seq) AS seq FROM events WHERE thread_id=?', (tid,))[0]['seq'])
        events, state = self.stream(tid, sync['after'])
        self.assertEqual(events, [])
        self.assertEqual(state['thread']['status'], 'completed')

    def test_initial_sync_watermark_covers_racing_deltas_without_dropping_them(self):
        tid = self.new(); epoch = self.gateway.event_epoch
        def history(method, params=None, **kwargs):
            self.assertEqual(method, 'thread/turns/list')
            for sequence, text in [(18, 'before'), (22, 'after')]:
                self.gateway.on_message({'method': 'item/agentMessage/delta', '_transportSeq': sequence, '_transportEpoch': epoch,
                                         'params': {'threadId': tid, 'itemId': 'live', 'delta': text}})
            return {'data': [{'id': 't', 'status': 'inProgress', 'items': [{'id': 'live', 'type': 'agentMessage', 'text': 'before'}]}], '_transportSeq': 20}
        with patch.object(self.gateway.rpc, 'call', side_effect=history):
            sync = self.gateway.sync_thread(tid)
        replay, _ = self.stream(tid, sync['after'])
        messages = [e['data'] for e in replay]
        self.assertEqual(len(messages), 2)  # Other open clients still receive both events.
        live = [m for m in messages if m.get('transportEpoch') != sync['transportEpoch'] or m.get('transportSeq', 0) > sync['snapshotSeq']]
        self.assertEqual([m['params']['delta'] for m in live], ['after'])
        self.assertEqual(sync['turns'][0]['items'][0]['text'] + live[0]['params']['delta'], 'beforeafter')

    def test_initial_sync_refuses_unselected_thread_and_changed_connection(self):
        self.gateway.shared = True
        tid = self.new()
        self.gateway.store.execute('UPDATE threads SET selected=0 WHERE id=?', (tid,))
        with patch.object(self.gateway.rpc, 'call') as rpc:
            self.assertEqual(self.request(f'/api/threads/{tid}/sync', cookie=self.cookie)[0], 409)
            rpc.assert_not_called()
        self.gateway.store.execute('UPDATE threads SET selected=1 WHERE id=?', (tid,))
        def changed(*args, **kwargs):
            self.gateway.event_epoch = 'new-connection'
            return {'data': [], '_transportSeq': 10}
        with patch.object(self.gateway.rpc, 'call', side_effect=changed):
            self.assertEqual(self.request(f'/api/threads/{tid}/sync', cookie=self.cookie)[0], 503)

    def test_workspace_browser_is_authenticated_and_never_calls_codex(self):
        child = self.work / '项目 with spaces'; child.mkdir()
        (child / 'nested').mkdir()
        (self.work / '.hidden-project').mkdir()
        (self.work / 'notes.txt').write_text('not a directory')
        outside = self.root / 'outside'; outside.mkdir()
        (self.work / 'outside-link').symlink_to(outside, target_is_directory=True)
        (self.work / 'inside-link').symlink_to(child, target_is_directory=True)
        url = '/api/workspaces/directories?' + urlencode({'path': str(self.work)})
        self.assertEqual(self.request(url)[0], 401)
        with patch.object(self.gateway.rpc, 'call', wraps=self.gateway.rpc.call) as rpc:
            status, data, _ = self.request(url, cookie=self.cookie)
            self.assertEqual(status, 200, data)
            self.assertIsNone(data['parent'])
            self.assertEqual({e['name'] for e in data['entries']}, {child.name, 'inside-link'})
            hidden = self.request(url + '&hidden=1', cookie=self.cookie)[1]
            self.assertIn('.hidden-project', {e['name'] for e in hidden['entries']})
            nested = self.request('/api/workspaces/directories?' + urlencode({'path': str(child)}), cookie=self.cookie)[1]
            self.assertEqual(nested['parent'], str(self.work))
            self.assertEqual(nested['entries'], [{'name': 'nested', 'path': str(child / 'nested')}])
            rpc.assert_not_called()

    def test_workspace_browser_and_creation_reject_invalid_paths(self):
        outside = self.root / 'outside'; outside.mkdir()
        link = self.work / 'escape'; link.symlink_to(outside, target_is_directory=True)
        file = self.work / 'file.txt'; file.write_text('fixture')
        with patch.object(self.gateway.rpc, 'call', wraps=self.gateway.rpc.call) as rpc:
            for path in [str(outside), str(link), str(self.work / '../outside'), str(file), str(self.work / 'missing'), 'relative', '', '\x00']:
                self.assertEqual(self.request('/api/workspaces/directories?' + urlencode({'path': path}), cookie=self.cookie)[0], 400, path)
                self.assertEqual(self.request('/api/threads', {'cwd': path}, self.cookie)[0], 400, path)
            self.assertEqual(self.request('/api/threads', {'cwd': 42}, self.cookie)[0], 400)
            for cursor in ['-1', 'bad', '1000001']:
                self.assertEqual(self.request('/api/workspaces/directories?' + urlencode({'path': str(self.work), 'cursor': cursor}), cookie=self.cookie)[0], 400)
            rpc.assert_not_called()

    def test_new_thread_uses_selected_child_directory(self):
        project = self.work / 'example-project'; project.mkdir()
        link = self.work / 'project-alias'; link.symlink_to(project, target_is_directory=True)
        with patch.object(self.gateway.rpc, 'call', wraps=self.gateway.rpc.call) as rpc:
            status, thread, _ = self.request('/api/threads', {'cwd': str(link), 'title': '子目录会话'}, self.cookie)
            self.assertEqual(status, 201, thread)
            self.assertEqual(thread['cwd'], str(project))
            self.assertEqual(next(c.args[1]['cwd'] for c in rpc.call_args_list if c.args[0] == 'thread/start'), str(project))
        self.assertEqual(self.request('/api/bootstrap', cookie=self.cookie)[1]['threads'][0]['cwd'], str(project))

    def test_workspace_browser_paginates_large_directories(self):
        expected = {f'project-{i:03}' for i in range(205)}
        for name in expected:
            (self.work / name).mkdir()
        for i in range(1010):
            (self.work / f'file-{i}').touch()
        found, cursor = [], None
        for _ in range(10):
            data = self.gateway.workspace_directories(str(self.work), cursor)
            self.assertLessEqual(len(data['entries']), 200)
            found.extend(e['name'] for e in data['entries'])
            cursor = data['nextCursor']
            if cursor is None:
                break
        self.assertIsNone(cursor)
        self.assertEqual(set(found), expected)
        self.assertEqual(len(found), len(expected))

    def test_restart_keeps_history_and_allows_continuation(self):
        tid = self.new(); self.send(tid)
        self.until(lambda: self.gateway.thread(tid)['status'] == 'completed')
        self.gateway.close(); self.gateway.store.close()
        self.gateway = RemoteGateway(self.state, [self.work], self.command, background=False)
        self.web.gateway = self.gateway
        self.assertEqual(self.request('/api/bootstrap', cookie=self.cookie)[0], 200)
        self.gateway.attach(tid)
        self.assertTrue(self.gateway.history(tid)['turns'])
        self.assertEqual(self.send(tid, 'continue', 'continue-submission-123')[0], 200)
        self.until(lambda: self.gateway.thread(tid)['status'] == 'completed')

    def test_steer_and_queue(self):
        tid = self.new(); self.send(tid, 'hang')
        active = self.gateway.thread(tid)['turn_id']
        response = self.message(tid, 'change direction', 'steer-submission-123', mode='steer')
        self.assertEqual(response[0], 200, response)
        self.assertEqual(response[1]['turnId'], active)
        response = self.message(tid, 'next task', 'queue-submission-123', mode='queue')
        self.assertEqual(response[0], 200, response)
        self.gateway._drain_queue()
        self.assertEqual(self.gateway.queue_for(tid)[0]['status'], 'pending')
        self.gateway.rpc.call('turn/interrupt', {'threadId': tid, 'turnId': active})
        self.until(lambda: self.gateway.thread(tid)['turn_id'] is None)
        self.gateway._drain_queue()
        self.until(lambda: self.gateway.thread(tid)['status'] == 'completed')
        self.assertEqual(self.gateway.queue_for(tid), [])
        self.assertTrue(self.message(tid, 'next task', 'queue-submission-123', mode='queue')[1]['queued'])

    def test_auto_followup_steers_active_turn_and_is_idempotent_after_completion(self):
        tid = self.new()
        start = self.message(tid, 'hang', 'auto-start-submit-123', mode='auto', model='fixture-model', effort='high')
        self.assertEqual(start[1]['delivery'], 'send')
        turn_id = start[1]['turnId']
        real_call = self.gateway.rpc.call
        with patch.object(self.gateway.rpc, 'call', wraps=real_call) as calls:
            first = self.message(tid, 'focus on the image', 'auto-steer-submit-123', mode='auto', model='next-model', effort='low')
        self.assertEqual(first[0], 200, first)
        self.assertEqual(first[1], {'turnId': turn_id, 'delivery': 'steer'})
        self.assertEqual([c.args[0] for c in calls.call_args_list], ['turn/steer'])
        params = calls.call_args.args[1]
        self.assertNotIn('model', params)
        self.assertNotIn('effort', params)
        self.assertEqual(self.gateway.thread(tid)['model'], 'fixture-model')
        self.assertEqual(self.gateway.thread(tid)['turn_id'], turn_id)
        self.assertEqual(self.gateway.queue_for(tid), [])
        self.assertEqual(len(self.gateway.history(tid)['turns']), 1)
        self.gateway.interrupt(tid)
        self.until(lambda: self.gateway.thread(tid)['turn_id'] is None)
        retry = self.message(tid, 'focus on the image', 'auto-steer-submit-123', mode='auto', model='next-model', effort='low')
        self.assertEqual(first[1], retry[1])
        self.assertEqual(len(self.gateway.history(tid)['turns']), 1)

    def test_auto_followup_handles_turn_finishing_before_steer(self):
        tid = self.new(); self.send(tid, 'hang')
        old_turn = self.gateway.thread(tid)['turn_id']
        real_call = self.gateway.rpc.call
        def finish_before_steer(method, params=None, **kwargs):
            if method == 'turn/steer':
                real_call('turn/interrupt', {'threadId': tid, 'turnId': old_turn})
                raise APIError(502, 'No active turn to steer')
            return real_call(method, params, **kwargs)
        with patch.object(self.gateway.rpc, 'call', side_effect=finish_before_steer):
            result = self.message(tid, 'hang', 'auto-racing-submit-123', mode='auto', model='next-model')
        self.assertEqual(result[0], 200, result)
        self.assertEqual(result[1]['delivery'], 'send')
        self.assertNotEqual(result[1]['turnId'], old_turn)
        self.assertEqual(len(self.gateway.history(tid)['turns']), 2)
        self.assertEqual(self.gateway.thread(tid)['model'], 'next-model')

    def test_auto_followup_never_resends_an_ambiguous_steer(self):
        tid = self.new(); self.send(tid, 'hang')
        real_call = self.gateway.rpc.call
        def timeout(method, params=None, **kwargs):
            if method == 'turn/steer':
                raise APIError(504, 'Timed out waiting for acknowledgement')
            return real_call(method, params, **kwargs)
        with patch.object(self.gateway.rpc, 'call', side_effect=timeout) as calls:
            response = self.message(tid, 'follow up', 'auto-timeout-submit-123', mode='auto')
            self.assertEqual(response[0], 504)
            self.assertEqual(self.message(tid, 'follow up', 'auto-timeout-submit-123', mode='auto')[0], 409)
            self.assertEqual([c.args[0] for c in calls.call_args_list], ['turn/steer'])
        self.assertEqual(len(self.gateway.history(tid)['turns']), 1)

    def test_auto_followup_does_not_start_over_a_new_external_turn(self):
        tid = self.new(); self.send(tid, 'hang')
        old_turn = self.gateway.thread(tid)['turn_id']
        real_call = self.gateway.rpc.call
        def replace_turn(method, params=None, **kwargs):
            if method == 'turn/steer':
                real_call('turn/interrupt', {'threadId': tid, 'turnId': old_turn})
                real_call('turn/start', {'threadId': tid, 'input': [{'type': 'text', 'text': 'hang'}]})
                raise APIError(502, 'No matching active turn')
            return real_call(method, params, **kwargs)
        with patch.object(self.gateway.rpc, 'call', side_effect=replace_turn):
            result = self.message(tid, 'follow up', 'auto-changed-turn-123', mode='auto')
        self.assertEqual(result[0], 502)
        self.assertEqual(len(self.gateway.history(tid)['turns']), 2)
        new_turn = self.gateway.thread(tid)['turn_id']
        result = self.message(tid, 'follow up', 'auto-changed-turn-123', mode='auto')
        self.assertEqual(result[0], 200, result)
        self.assertEqual(result[1], {'turnId': new_turn, 'delivery': 'steer'})

    def test_image_view_is_authenticated_and_bound_to_a_thread_item(self):
        tid = self.new(); self.send(tid, 'hang')
        image = self.root / 'temporary-preview.png'
        raw = (Path(__file__).resolve().parents[1] / 'codex_remote/static/icon-192.png').read_bytes()
        image.write_bytes(raw)
        self.gateway.on_message({'method': 'item/completed', 'params': {
            'threadId': tid, 'turnId': self.gateway.thread(tid)['turn_id'],
            'item': {'id': 'image-fixture', 'type': 'imageView', 'path': str(image)}}})
        url = f'/api/threads/{tid}/image?itemId=image-fixture'
        self.assertEqual(self.request(url)[0], 401)
        response = self.request(url, cookie=self.cookie)
        self.assertEqual(response[0], 200, response[0])
        self.assertEqual(response[1], raw)
        self.assertEqual(response[2]['Content-Type'], 'image/png')
        self.assertEqual(response[2]['Cache-Control'], 'no-store')
        other = self.new()
        self.assertEqual(self.request(f'/api/threads/{other}/image?itemId=image-fixture', cookie=self.cookie)[0], 404)
        self.assertEqual(self.request(f'/api/threads/{tid}/image?itemId=../temporary-preview.png', cookie=self.cookie)[0], 404)
        image.write_text('not an image, even with a .png extension')
        self.assertEqual(self.request(url, cookie=self.cookie)[0], 415)
        image.unlink()
        self.assertEqual(self.request(url, cookie=self.cookie)[0], 404)
        secret = self.root / 'non-image.txt'; secret.write_text('NON_IMAGE_SENTINEL')
        image.symlink_to(secret)
        self.assertEqual(self.request(url, cookie=self.cookie)[0], 415)
        image.unlink()
        with image.open('wb') as file: file.truncate(33 * 1024 * 1024)
        self.assertEqual(self.request(url, cookie=self.cookie)[0], 413)

    def test_image_view_history_can_be_read_without_resuming(self):
        self.gateway.shared = True
        raw = self.gateway.rpc.call('thread/start', {'cwd': str(self.work)})['thread']
        tid = raw['id']
        self.gateway._adopt(raw)
        image = self.work / 'saved-image.png'
        image.write_bytes((Path(__file__).resolve().parents[1] / 'codex_remote/static/icon-192.png').read_bytes())
        turn = {'id': 'saved-turn', 'status': 'completed', 'items': [{'type': 'imageView', 'id': 'saved-image', 'path': 'saved-image.png'}]}
        def history_only(method, params=None, **kwargs):
            self.assertEqual(method, 'thread/turns/list')
            return {'data': [turn], 'nextCursor': None}
        with patch.object(self.gateway.rpc, 'call', side_effect=history_only):
            response = self.request(f'/api/threads/{tid}/image?itemId=saved-image', cookie=self.cookie)
        self.assertEqual(response[0], 200)
        self.assertNotIn(tid, self.gateway.loaded)
        self.assertTrue(self.gateway.thread(tid)['read_only'])

    def test_cancel_queue_and_stop_do_not_run_followups(self):
        tid = self.new(); self.send(tid, 'hang')
        self.message(tid, 'next', 'queue-cancel-test-123', mode='queue')
        self.gateway.interrupt(tid)
        self.until(lambda: self.gateway.thread(tid)['turn_id'] is None)
        self.gateway._drain_queue()
        self.assertEqual(self.gateway.queue_for(tid), [])

    def test_import_native_session_with_history_and_reject_outside(self):
        self.gateway.shared = True
        raw = self.gateway.rpc.call('thread/start', {'cwd': str(self.work)})['thread']
        response = self.request('/api/import', {'threadId': raw['id']}, self.cookie)
        self.assertEqual(response[0], 200, response)
        self.assertTrue(response[1]['read_only'])
        self.assertNotIn(raw['id'], self.gateway.loaded)
        self.assertEqual(self.request('/api/threads/'+raw['id']+'/control', {}, self.cookie)[0], 200)
        self.assertIn(raw['id'], self.gateway.loaded)
        outside = self.gateway.rpc.call('thread/start', {'cwd': '/tmp'})['thread']
        self.assertEqual(self.request('/api/import', {'threadId': outside['id']}, self.cookie)[0], 403)

    def test_discovery_rename_archive_and_fork(self):
        self.gateway.shared = True
        native = self.gateway.rpc.call('thread/start', {'cwd': str(self.work)})['thread']
        response = self.request('/api/discover', {}, self.cookie)
        self.assertIn(native['id'], response[1]['ids'])
        tid = native['id']; self.gateway.attach(tid)
        self.assertEqual(self.request(f'/api/threads/{tid}/rename', {'title': 'Renamed'}, self.cookie)[0], 200)
        self.assertEqual(self.gateway.thread(tid)['title'], 'Renamed')
        self.assertEqual(self.request(f'/api/threads/{tid}/archive', {'archived': True}, self.cookie)[0], 200)
        self.assertEqual(self.gateway.thread(tid)['archived'], 1)
        self.request(f'/api/threads/{tid}/archive', {'archived': False}, self.cookie)
        response = self.request(f'/api/threads/{tid}/fork', {}, self.cookie)
        self.assertEqual(response[0], 201, response)
        self.assertNotEqual(response[1]['id'], tid)

    def test_attachments_and_file_boundaries(self):
        tid = self.new()
        status, upload, _ = self.request('/api/uploads', {'name': 'notes.txt', 'data': base64.b64encode(b'hello').decode()}, self.cookie)
        self.assertEqual(status, 201)
        self.assertEqual(self.request('/api/uploads/'+upload['id'], cookie=self.cookie)[1], b'hello')
        self.assertEqual(self.request('/api/uploads', {'name': '../outside', 'data': 'YQ=='}, self.cookie)[0], 400)
        self.assertEqual(self.message(tid, '', 'attachment-message-123', attachments=[upload['id']])[0], 200)
        self.until(lambda: self.gateway.thread(tid)['status'] == 'completed')
        (self.work/'safe.txt').write_text('safe file')
        (self.work/'escape').symlink_to(self.root/'codex-state.json')
        self.assertEqual(self.request(f'/api/files?threadId={tid}&path=safe.txt', cookie=self.cookie)[1]['content'], 'safe file')
        self.assertEqual(self.request(f'/api/files?threadId={tid}&path=../codex-state.json', cookie=self.cookie)[0], 403)
        self.assertEqual(self.request(f'/api/files?threadId={tid}&path=escape', cookie=self.cookie)[0], 403)

    def test_elicitation_schema_is_validated_and_decline_works(self):
        tid = self.new(); self.send(tid, 'elicitation')
        self.until(lambda: bool(self.gateway.pending_requests(tid)))
        key = self.gateway.pending_requests(tid)[0]['key']; url = '/api/requests/'+key+'/respond'
        self.assertEqual(self.request(url, {'action': 'accept', 'content': {'count': 0}}, self.cookie)[0], 400)
        self.assertEqual(self.request(url, {'action': 'accept', 'content': {'count': 3}}, self.cookie)[0], 200)
        self.until(lambda: self.gateway.thread(tid)['status'] == 'completed')

    def test_device_revocation_is_immediate(self):
        _, _, h = self.request('/api/login', {'token': self.token})
        other = h['Set-Cookie'].split(';')[0]
        devices = self.request('/api/devices', cookie=self.cookie)[1]['devices']
        self.assertEqual(len(devices), 2)
        second = next(d for d in devices if not d['current'])
        self.request('/api/devices/revoke', {'id': second['id']}, self.cookie)
        self.assertEqual(self.request('/api/bootstrap', cookie=other)[0], 401)
        self.assertEqual(self.request('/api/bootstrap', cookie=self.cookie)[0], 200)

    def test_push_rejects_ssrf_and_dispatches_only_registered_devices(self):
        manager = PushManager(self.gateway.store, self.state, 'mailto:test@example.com')
        self.gateway.push = manager
        try:
            bad = {'endpoint': 'https://127.0.0.1/admin', 'keys': {}}
            self.assertEqual(self.request('/api/push/subscribe', {'subscription': bad}, self.cookie)[0], 400)
            good = {'endpoint': 'https://fcm.googleapis.com/fcm/send/fixture', 'keys': {'auth': base64.urlsafe_b64encode(b'x'*16).decode(), 'p256dh': base64.urlsafe_b64encode(b'\x04'+b'x'*64).decode()}}
            self.assertEqual(self.request('/api/push/subscribe', {'subscription': good}, self.cookie)[0], 200)
            row = self.gateway.store.rows('SELECT * FROM push_subscriptions')[0]
            with patch('codex_remote.push.webpush') as send:
                manager._send(row, '{"title":"Codex"}')
                self.assertEqual(send.call_count, 1)
                self.assertIn('vapid_private_key', send.call_args.kwargs)
            self.request('/api/push/unsubscribe', {}, self.cookie)
            self.assertEqual(self.gateway.store.rows('SELECT * FROM push_subscriptions'), [])
        finally:
            manager.close(); self.gateway.push = None

    def test_push_status_and_test_are_scoped_to_current_device(self):
        status = self.request('/api/push/status', cookie=self.cookie)
        self.assertEqual(status[1], {'configured': False, 'subscribed': False})
        self.assertEqual(self.request('/api/push/test', {}, self.cookie)[0], 503)
        manager = PushManager(self.gateway.store, self.state, 'mailto:test@example.com')
        self.gateway.push = manager
        try:
            self.assertEqual(self.request('/api/push/status')[0], 401)
            self.assertEqual(self.request('/api/push/test', {})[0], 401)
            self.assertEqual(self.request('/api/push/test', {}, self.cookie)[0], 409)
            good = {'endpoint': 'https://web.push.apple.com/fixture', 'keys': {'auth': base64.urlsafe_b64encode(b'x'*16).decode(), 'p256dh': base64.urlsafe_b64encode(b'\x04'+b'x'*64).decode()}}
            self.assertEqual(self.request('/api/push/subscribe', {'subscription': good}, self.cookie)[0], 200)
            self.assertEqual(self.request('/api/push/status', cookie=self.cookie)[1], {'configured': True, 'subscribed': True})
            other = self.request('/api/login', {'token': self.token})[2]['Set-Cookie'].split(';')[0]
            self.assertFalse(self.request('/api/push/status', cookie=other)[1]['subscribed'])
            with patch('codex_remote.push.webpush') as send:
                self.assertEqual(self.request('/api/push/test', {}, other)[0], 409)
                self.assertEqual(self.request('/api/push/test', {}, self.cookie)[1], {'accepted': True})
                self.assertEqual(send.call_count, 1)
                self.assertEqual(send.call_args.kwargs['subscription_info']['endpoint'], good['endpoint'])
                payload = json.loads(send.call_args.kwargs['data'])
                self.assertEqual(payload['title'], 'Codex notification test')
                self.assertNotIn('threadId', payload)
            with patch('codex_remote.push.webpush', side_effect=OSError('fixture network failure')):
                self.assertEqual(self.request('/api/push/test', {}, self.cookie)[0], 502)
            from pywebpush import WebPushException
            from requests import Response
            response = Response(); response.status_code = 410
            with patch('codex_remote.push.webpush', side_effect=WebPushException('expired', response=response)):
                self.assertEqual(self.request('/api/push/test', {}, self.cookie)[0], 409)
            self.assertFalse(self.request('/api/push/status', cookie=self.cookie)[1]['subscribed'])
        finally:
            manager.close(); self.gateway.push = None

    def test_turn_completion_sends_push_without_an_open_page(self):
        manager = PushManager(self.gateway.store, self.state, 'mailto:test@example.com')
        self.gateway.push = manager
        try:
            good = {'endpoint': 'https://web.push.apple.com/fixture', 'keys': {'auth': base64.urlsafe_b64encode(b'x'*16).decode(), 'p256dh': base64.urlsafe_b64encode(b'\x04'+b'x'*64).decode()}}
            self.request('/api/push/subscribe', {'subscription': good}, self.cookie)
            with patch.object(manager.pool, 'submit') as dispatch:
                tid = self.new(); self.send(tid, 'private fixture text', 'completion-push-test')
                self.until(lambda: dispatch.called)
                payload = json.loads(dispatch.call_args.args[2])
                self.assertEqual(payload['threadId'], tid)
                self.assertEqual(payload['title'], 'Codex turn finished')
                self.assertNotIn('private fixture text', json.dumps(payload))
            row = self.gateway.store.rows('SELECT * FROM push_subscriptions')[0]
            self.request('/api/logout', {}, self.cookie)
            with patch('codex_remote.push.webpush') as send:
                self.assertEqual(manager._send(row, '{}'), 'expired')
                send.assert_not_called()
        finally:
            manager.close(); self.gateway.push = None

    def test_models_history_export_and_notifications(self):
        self.assertTrue(self.request('/api/models', cookie=self.cookie)[1]['data'])
        tid = self.new(); self.message(tid, 'hello', 'model-config-test-123', model='fixture-model', effort='high')
        self.until(lambda: self.gateway.thread(tid)['status'] == 'completed')
        self.assertEqual(self.gateway.thread(tid)['effort'], 'high')
        self.assertTrue(self.request(f'/api/threads/{tid}/history', cookie=self.cookie)[1]['turns'])
        self.assertTrue(self.request(f'/api/threads/{tid}/export', cookie=self.cookie)[1]['events'])
        notices = self.request('/api/bootstrap', cookie=self.cookie)[1]['notices']
        self.assertTrue(notices)
        self.request('/api/notifications/seen', {'before': notices[0]['id']}, self.cookie)
        self.assertEqual(self.request('/api/bootstrap', cookie=self.cookie)[1]['notices'], [])

    def test_native_queue_identity_idempotency_and_stop(self):
        self.gateway.shared = True
        tid = self.new(); self.send(tid, 'hang')
        first = self.message(tid, 'next task', 'native-queue-submit-123', mode='queue')
        second = self.message(tid, 'next task', 'native-queue-submit-123', mode='queue')
        self.assertEqual(first[0], 200, first)
        self.assertEqual(first[1], second[1])
        entries = self.gateway.queue_for(tid)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['id'], first[1]['id'])
        self.assertEqual(self.gateway.store.rows('SELECT * FROM queue'), [])
        self.assertEqual(self.message(tid, 'different', 'native-queue-submit-123', mode='queue')[0], 409)
        self.assertEqual(self.message(tid, 'next', 'native-queue-model-123', mode='queue', model='fixture-model')[0], 400)
        self.gateway.interrupt(tid)
        self.until(lambda: self.gateway.thread(tid)['turn_id'] is None)
        self.assertEqual(self.gateway.queue_for(tid), [])

    def test_export_includes_all_pages_of_native_history(self):
        tid = self.new()
        turns = [{'id': f'turn-{i}', 'status': 'completed', 'items': []} for i in range(65)]
        real_call = self.gateway.rpc.call
        def call(method, params=None, **kwargs):
            if method == 'thread/turns/list':
                start = int(params.get('cursor') or 0)
                end = start + params['limit']
                return {'data': list(reversed(turns))[start:end], 'nextCursor': str(end) if end < len(turns) else None}
            return real_call(method, params, **kwargs)
        with patch.object(self.gateway.rpc, 'call', side_effect=call):
            result = self.request(f'/api/threads/{tid}/export', cookie=self.cookie)
            self.assertEqual(result[0], 200)
            self.assertEqual(result[1]['turns'], turns)

    def test_external_metadata_and_approval_resolution(self):
        tid = self.new(); self.send(tid, 'approval')
        self.until(lambda: bool(self.gateway.pending_requests(tid)))
        request = next(iter(self.gateway.requests.values()))
        self.gateway.on_message({'method': 'serverRequest/resolved', 'params': {'threadId': tid, 'requestId': request['rpcId']}})
        self.assertEqual(self.gateway.pending_requests(tid), [])
        self.gateway.on_message({'method': 'thread/settings/updated', 'params': {'threadId': tid, 'threadSettings': {'model': 'new-model', 'effort': 'high'}}})
        self.gateway._adopt({'id': tid, 'cwd': str(self.work), 'name': 'native metadata'})
        self.assertEqual(self.gateway.thread(tid)['model'], 'new-model')
        self.assertEqual(self.gateway.thread(tid)['effort'], 'high')

    def test_sync_hydration_keeps_events_after_snapshot_only(self):
        tid = self.new()
        real_call = self.gateway.rpc.call
        def call(method, params=None, **kwargs):
            if method == 'thread/resume':
                self.gateway.on_message({'method': 'thread/name/updated', 'params': {'threadId': tid, 'threadName': 'superseded'}, '_transportSeq': 9})
                self.gateway.on_message({'method': 'thread/name/updated', 'params': {'threadId': tid, 'threadName': 'latest name'}, '_transportSeq': 11})
                return {'thread': {'id': tid, 'cwd': str(self.work), 'turns': []}, '_transportSeq': 10}
            return real_call(method, params, **kwargs)
        with patch.object(self.gateway.rpc, 'call', side_effect=call):
            self.gateway.attach(tid, force=True)
        self.assertEqual(self.gateway.thread(tid)['title'], 'latest name')
        events = [json.loads(r['payload']) for r in self.gateway.store.rows('SELECT payload FROM events WHERE thread_id=?', (tid,))]
        self.assertFalse(any(e['params'].get('threadName') == 'superseded' for e in events))

    def test_isolated_mode_refuses_terminal_session_import_and_resume(self):
        tid = self.gateway.rpc.call('thread/start', {'cwd': str(self.work)})['thread']['id']
        self.assertEqual(self.request('/api/import', {'threadId': tid}, self.cookie)[0], 403)
        self.assertEqual(self.request('/api/discover', {}, self.cookie)[1]['ids'], [])
        self.gateway._adopt({'id': tid, 'cwd': str(self.work)})
        with patch.object(self.gateway.rpc, 'call') as rpc:
            self.assertEqual(self.request(f'/api/threads/{tid}/attach', {}, self.cookie)[0], 403)
            self.assertEqual(self.message(tid, 'hello', 'external-submit-123')[0], 403)
            rpc.assert_not_called()

    def test_discovery_never_auto_resumes_terminal_sessions(self):
        self.gateway.shared = True
        tid = self.gateway.rpc.call('thread/start', {'cwd': str(self.work)})['thread']['id']
        self.gateway.discover()
        self.assertEqual(self.gateway.thread(tid)['selected'], 0)
        with patch.object(self.gateway.rpc, 'call', wraps=self.gateway.rpc.call) as rpc:
            self.gateway.worker.start()
            try:
                time.sleep(2.3)
            finally:
                self.gateway.stopped.set(); self.gateway.worker.join(3)
            rpc.assert_not_called()
        self.assertNotIn(tid, self.gateway.loaded)
        self.gateway.attach(tid)
        self.assertEqual(self.gateway.thread(tid)['selected'], 1)

    def test_shared_gateway_does_not_reject_other_clients_tool_requests(self):
        self.gateway.shared = True
        tid = self.new()
        with patch.object(self.gateway.rpc, 'reply') as reply:
            self.gateway.on_message({'id': 'external-tool-request', 'method': 'item/tool/call', 'params': {'threadId': tid}})
            reply.assert_not_called()

    def test_live_check_refuses_normal_terminal_daemon(self):
        import contextlib
        import io
        import live_check
        socket = str(self.root/'app-server-control/app-server-control.sock')
        with patch.dict('os.environ', {'CODEX_HOME': str(self.root)}), patch.object(sys, 'argv', ['live_check.py', '--live', '--socket', socket]), patch('live_check.SocketRPC') as rpc, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                live_check.main()
            self.assertEqual(error.exception.code, 2)
            rpc.assert_not_called()

    def test_preview_of_running_child_project_never_resumes_it(self):
        self.gateway.shared = True
        child = self.work/'nested-project'; child.mkdir()
        native = self.gateway.rpc.call('thread/start', {'cwd': str(child)})['thread']
        self.gateway.rpc.call('turn/start', {'threadId': native['id'], 'input': [{'type': 'text', 'text': 'hang'}]})
        with patch.object(self.gateway.rpc, 'call', wraps=self.gateway.rpc.call) as rpc:
            discovered = self.request('/api/discover', {}, self.cookie)
            self.assertIn(native['id'], discovered[1]['ids'])
            response = self.request('/api/threads/'+native['id']+'/preview', cookie=self.cookie)
            self.assertEqual(response[0], 200, response)
            self.assertTrue(response[1]['thread']['read_only'])
            self.assertEqual(response[1]['thread']['status'], 'running')
            self.assertTrue(response[1]['turns'])
            methods = {call.args[0] for call in rpc.call_args_list}
            self.assertLessEqual(methods, {'thread/list', 'thread/read', 'thread/loaded/list', 'thread/turns/list'})
            list_params = next(call.args[1] for call in rpc.call_args_list if call.args[0] == 'thread/list')
            self.assertTrue(list_params['useStateDbOnly'])
        self.assertNotIn(native['id'], self.gateway.loaded)
        self.assertEqual(self.gateway.thread(native['id'])['selected'], 0)

    def test_read_only_view_blocks_control_until_explicitly_enabled(self):
        self.gateway.shared = True
        native = self.gateway.rpc.call('thread/start', {'cwd': str(self.work)})['thread']
        self.gateway.discover(); tid = native['id']
        with patch.object(self.gateway.rpc, 'call', wraps=self.gateway.rpc.call) as rpc:
            for action, body in [('attach', {}), ('message', {'text': 'hello', 'submissionId': 'view-no-send-12345'}), ('interrupt', {}), ('rename', {'title': 'changed'}), ('archive', {}), ('cancel', {'id': 'queued'})]:
                response = self.request(f'/api/threads/{tid}/{action}', body, self.cookie)
                self.assertEqual(response[0], 409, (action, response))
            rpc.assert_not_called()
        self.assertEqual(self.request(f'/api/threads/{tid}/control', {}, self.cookie)[0], 200)
        self.assertFalse(self.gateway.thread(tid)['read_only'])
        self.assertEqual(self.message(tid, 'hello', 'explicit-control-123')[0], 200)
        self.until(lambda: self.gateway.thread(tid)['status'] == 'completed')

    def test_automatic_browse_only_lists_unselected_sessions(self):
        self.gateway.shared = True; self.gateway.browse_existing = True
        native = self.gateway.rpc.call('thread/start', {'cwd': str(self.work)})['thread']
        with patch.object(self.gateway.rpc, 'call', wraps=self.gateway.rpc.call) as rpc:
            self.gateway.worker.start()
            try:
                self.until(lambda: bool(self.gateway.list_threads()))
            finally:
                self.gateway.stopped.set(); self.gateway.worker.join(3)
            methods = {call.args[0] for call in rpc.call_args_list}
            self.assertTrue(methods)
            self.assertLessEqual(methods, {'thread/list', 'thread/loaded/list', 'thread/read'})
        self.assertNotIn(native['id'], self.gateway.loaded)

    def test_saved_unloaded_session_can_resume_and_continue(self):
        self.gateway.shared = True
        native = self.gateway.rpc.call('thread/start', {'cwd': str(self.work)})['thread']
        self.gateway.rpc.call('fixture/unload', {'threadId': native['id']})
        self.gateway.discover()
        self.assertEqual(self.gateway.thread(native['id'])['native_status'], 'notLoaded')
        with patch.object(self.gateway.rpc, 'call', wraps=self.gateway.rpc.call) as rpc:
            response = self.request('/api/threads/'+native['id']+'/control', {}, self.cookie)
            self.assertEqual(response[0], 200, response)
            self.assertEqual([c.args[0] for c in rpc.call_args_list], ['thread/resume', 'thread/turns/list'])
            self.assertTrue(rpc.call_args_list[0].args[1]['excludeTurns'])
            self.assertEqual(rpc.call_args_list[1].args[1]['limit'], 10)
        self.assertFalse(response[1]['read_only'])
        self.assertEqual(response[1]['native_status'], 'idle')
        self.assertEqual(self.send(native['id'], 'continue saved task', 'resume-saved-session-123')[0], 200)
        self.until(lambda: self.gateway.thread(native['id'])['status'] == 'completed')

    def test_native_writer_conflict_never_retries_or_takes_over(self):
        from codex_remote.server import APIError
        self.gateway.shared = True
        native = self.gateway.rpc.call('thread/start', {'cwd': str(self.work)})['thread']
        self.gateway.discover()
        with patch.object(self.gateway.rpc, 'call', side_effect=APIError(502, 'thread already has an active writer')) as rpc:
            response = self.request('/api/threads/'+native['id']+'/control', {}, self.cookie)
            self.assertEqual(response[0], 409)
            self.assertIn('does not prove a task is still running', response[1]['error'])
            rpc.assert_called_once_with('thread/resume', {'threadId': native['id'], 'excludeTurns': True})
        self.assertNotIn(native['id'], self.gateway.loaded)
        self.assertTrue(self.gateway.thread(native['id'])['read_only'])

    def test_closed_controlled_session_is_eligible_for_reconnection(self):
        tid = self.new()
        self.gateway.on_message({'method': 'thread/status/changed', 'params': {'threadId': tid, 'status': {'type': 'notLoaded'}}})
        self.assertNotIn(tid, self.gateway.loaded)
        self.assertEqual(self.gateway.thread(tid)['native_status'], 'notLoaded')
        self.assertTrue(self.gateway.thread(tid)['selected'])
        self.gateway.enable_control(tid)
        self.assertIn(tid, self.gateway.loaded)

    def test_unmaterialized_thread_preview_attach_export_and_first_message(self):
        self.gateway.shared = True
        native = self.gateway.rpc.call('thread/start', {'cwd': str(self.work), 'historyMode': 'paginated'})['thread']
        tid = native['id']; self.gateway.import_thread(tid)
        response = self.request(f'/api/threads/{tid}/preview', cookie=self.cookie)
        self.assertEqual(response[0], 200, response)
        self.assertEqual(response[1]['turns'], [])
        self.assertFalse(response[1]['materialized'])
        self.assertGreater(response[1]['snapshotSeq'], 0)
        self.assertNotIn(tid, self.gateway.loaded)
        self.assertEqual(self.request(f'/api/threads/{tid}/export', cookie=self.cookie)[1]['turns'], [])
        self.assertEqual(self.request(f'/api/threads/{tid}/control', {}, self.cookie)[0], 200)
        self.assertFalse(self.gateway.thread(tid)['read_only'])
        self.assertIn(tid, self.gateway.awaiting_materialization)
        self.assertNotIn(tid, self.gateway.loaded)
        self.assertEqual(self.send(tid, 'first message', 'unmaterialized-first-123')[0], 200)
        self.until(lambda: self.gateway.thread(tid)['status'] == 'completed')
        self.assertIn(tid, self.gateway.loaded)
        self.assertNotIn(tid, self.gateway.awaiting_materialization)
        self.gateway.attach(tid, force=True)
        history = self.gateway.history(tid)
        self.assertEqual(len(history['turns']), 1)
        self.assertTrue(any(i.get('text') == 'Fixture response' for i in history['turns'][0]['items']))

    def test_history_does_not_hide_other_errors_or_invalid_cursors(self):
        from codex_remote.server import APIError
        tid = self.new()
        empty = f'thread {tid} is not materialized yet; thread/turns/list is unavailable before first user message'
        for status, message, cursor in [(502, 'permission denied', None), (502, 'thread not found', None),
                                         (504, empty, None), (502, empty, 'old-cursor'),
                                         (502, empty.replace(tid, 'another-thread'), None)]:
            error = APIError(status, message)
            with self.subTest(status=status, message=message, cursor=cursor):
                with patch.object(self.gateway.rpc, 'call', side_effect=error):
                    with self.assertRaises(APIError) as raised:
                        self.gateway.history(tid, cursor=cursor)
                    self.assertIs(raised.exception, error)

    def test_first_turn_after_empty_history_response_is_not_lost(self):
        from codex_remote.server import APIError
        tid = self.new()
        def call(method, params=None, **kwargs):
            if method == 'thread/resume':
                return {'thread': {'id': tid, 'cwd': str(self.work), 'status': {'type': 'idle'}, 'historyMode': 'paginated'}, '_transportSeq': 90}
            self.assertEqual(method, 'thread/turns/list')
            self.gateway.on_message({'method': 'turn/started', 'params': {'threadId': tid, 'turn': {'id': 'first-live-turn', 'status': 'inProgress'}}, '_transportSeq': 101})
            raise APIError(502, f'thread {tid} is not materialized yet; thread/turns/list is unavailable before first user message', transport_seq=100)
        with patch.object(self.gateway.rpc, 'call', side_effect=call):
            self.gateway.attach(tid, force=True)
        self.assertEqual(self.gateway.thread(tid)['turn_id'], 'first-live-turn')
        self.assertEqual(self.gateway.thread(tid)['status'], 'running')

    def test_missing_rollout_with_existing_history_is_not_treated_as_empty(self):
        from codex_remote.server import APIError
        self.gateway.shared = True
        tid = self.new(); self.send(tid)
        self.until(lambda: self.gateway.thread(tid)['status'] == 'completed')
        real_call = self.gateway.rpc.call
        def call(method, params=None, **kwargs):
            if method == 'thread/resume':
                raise APIError(502, f'no rollout found for thread id {tid}')
            return real_call(method, params, **kwargs)
        with patch.object(self.gateway.rpc, 'call', side_effect=call):
            with self.assertRaises(APIError):
                self.gateway.attach(tid, force=True)
        self.assertNotIn(tid, self.gateway.awaiting_materialization)
