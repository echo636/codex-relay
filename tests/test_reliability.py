"""Storage failure and resume regressions, using only the local Codex fixture."""
import sqlite3
import unittest
from unittest.mock import patch

import test_remote
from codex_remote.server import APIError


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.h = test_remote.RemoteTests()
        self.h.setUp()

    def tearDown(self):
        self.h.tearDown()

    def test_storage_errors_are_actionable(self):
        h = self.h
        tid = h.new()
        for error in [APIError(502, 'failed to read thread: Quota exceeded (os error 122)'), sqlite3.OperationalError('disk I/O error')]:
            with patch.object(h.gateway, 'preview', side_effect=error):
                status, result, _ = h.request(f'/api/threads/{tid}/preview', cookie=h.cookie)
            self.assertEqual(status, 507)
            self.assertIn('quota', result['error'])

    def test_failed_thread_backoff_does_not_starve_healthy_thread(self):
        h = self.h
        broken, healthy = h.new(), h.new()
        h.gateway.loaded.clear()
        real = h.gateway.attach
        def attach(tid, **kw):
            if tid == broken:
                raise APIError(502, f'thread {tid} already has a live local writer')
            return real(tid, **kw)
        with patch.object(h.gateway, 'attach', side_effect=attach) as calls:
            self.assertIn('writer lock', h.gateway._reconnect_threads()[0])
            count = calls.call_count
            h.gateway._reconnect_threads()
            self.assertEqual(calls.call_count, count)
            self.assertIn(healthy, h.gateway.loaded)
            # User-initiated retry bypasses the automatic delay and never kills an owner.
            result = h.request(f'/api/threads/{broken}/control', {}, h.cookie)
            self.assertEqual(result[0], 409)
            self.assertIn('does not prove a task is still running', result[1]['error'])
            self.assertEqual(calls.call_count, count + 1)

    def test_copy_of_unselected_history_does_not_resume_or_modify_original(self):
        h = self.h
        h.gateway.shared = True
        original = h.new()
        h.message(original, 'preserve this context', 'copy-context-fixture')
        h.until(lambda: h.gateway.thread(original)['status'] == 'completed')
        before = h.gateway.history(original)['turns']
        h.gateway.loaded.discard(original)
        h.gateway.store.execute('UPDATE threads SET selected=0 WHERE id=?', (original,))
        with patch.object(h.gateway.rpc, 'call', wraps=h.gateway.rpc.call) as calls:
            status, copy, _ = h.request(f'/api/threads/{original}/fork', {}, h.cookie)
        self.assertEqual(status, 201, copy)
        self.assertNotEqual(copy['id'], original)
        self.assertEqual(h.gateway.thread(original)['selected'], 0)
        original_calls = [(c.args[0], c.args[1]) for c in calls.call_args_list if c.args[1].get('threadId') == original]
        self.assertEqual(original_calls, [('thread/fork', {'threadId': original, 'excludeTurns': True})])
        self.assertEqual(h.gateway.history(copy['id'])['turns'], before)
        self.assertEqual(h.gateway.history(original)['turns'], before)

    def test_created_copy_remains_accessible_if_its_connection_fails(self):
        h = self.h
        tid = h.new()
        with patch.object(h.gateway, 'attach', side_effect=APIError(503, 'connection unavailable')):
            status, result, _ = h.request(f'/api/threads/{tid}/fork', {}, h.cookie)
        self.assertEqual(status, 201, result)
        self.assertNotEqual(result['id'], tid)
        self.assertEqual(result['connection_error'], 'connection unavailable')
        self.assertEqual(h.gateway.thread(result['id'])['selected'], 1)

    def test_resume_fetches_only_latest_history_page(self):
        h = self.h
        tid = h.new()
        for index in range(15):
            turn = {'id': f'turn-{index}', 'status': 'inProgress'}
            h.gateway.rpc.call('fixture/event', {'method': 'turn/started', 'params': {'threadId': tid, 'turn': turn}})
            h.gateway.rpc.call('fixture/event', {'method': 'turn/completed', 'params': {'threadId': tid, 'turn': {**turn, 'status': 'completed'}}})
        with patch.object(h.gateway.rpc, 'call', wraps=h.gateway.rpc.call) as calls:
            h.gateway.attach(tid, force=True)
        self.assertEqual(calls.call_args_list[0].args, ('thread/resume', {'threadId': tid, 'excludeTurns': True}))
        self.assertEqual(calls.call_args_list[1].args[0], 'thread/turns/list')
        self.assertEqual(calls.call_args_list[1].args[1]['limit'], 10)
        self.assertEqual(h.gateway.thread(tid)['history_cursor'], '10')
        self.assertEqual(h.gateway.thread(tid)['status'], 'completed')


if __name__ == '__main__':
    unittest.main()
