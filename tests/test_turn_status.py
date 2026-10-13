"""A native failure or idle status must not leave the UI permanently working."""
import unittest
import test_remote


class TurnStatusTests(unittest.TestCase):
    def setUp(self):
        self.h = test_remote.RemoteTests(); self.h.setUp()

    def tearDown(self):
        self.h.tearDown()

    def event(self, method, params):
        self.h.gateway.rpc.call('fixture/event', {'method': method, 'params': params})

    def test_retry_details_and_final_failure_survive_native_idle_and_reload(self):
        h = self.h; tid = h.new()
        h.message(tid, 'hang', 'status-hang-1234')
        h.until(lambda: h.gateway.thread(tid)['turn_id'] not in (None, 'starting'))
        turn = h.gateway.thread(tid)['turn_id']
        self.event('item/started', {'threadId': tid, 'turnId': turn, 'item': {'id': 'recovery', 'type': 'agentMessage', 'text': ''}})
        error = {'message': 'Reconnecting... 1/8', 'additionalDetails': 'HTTP 403: Client not allowed'}
        self.event('error', {'threadId': tid, 'turnId': turn, 'error': error, 'willRetry': True})
        h.until(lambda: '403' in (h.gateway.thread(tid)['retry_error'] or ''))
        self.assertEqual(h.gateway.thread(tid)['status'], 'running')
        self.event('item/agentMessage/delta', {'threadId': tid, 'turnId': turn, 'itemId': 'recovery', 'delta': 'Recovered'})
        h.until(lambda: h.gateway.thread(tid)['retry_error'] is None)
        self.assertEqual(h.gateway.thread(tid)['status'], 'running')
        self.event('error', {'threadId': tid, 'turnId': turn, 'error': error, 'willRetry': True})
        h.until(lambda: h.gateway.thread(tid)['retry_error'] is not None)
        self.event('error', {'threadId': tid, 'turnId': turn, 'error': error, 'willRetry': False})
        h.until(lambda: h.gateway.thread(tid)['status'] == 'failed')
        self.assertIsNone(h.gateway.thread(tid)['turn_id'])
        self.event('turn/completed', {'threadId': tid, 'turn': {'id': turn, 'status': 'failed', 'error': error}})
        self.event('thread/status/changed', {'threadId': tid, 'status': {'type': 'idle'}})
        self.assertEqual(h.gateway.thread(tid)['status'], 'failed')
        h.gateway.attach(tid, force=True)
        self.assertEqual(h.gateway.thread(tid)['status'], 'failed')
        self.assertIn('Client not allowed', h.gateway.thread(tid)['last_error'])

    def test_idle_and_system_error_clear_stale_turn_and_success_is_not_overwritten(self):
        h = self.h; tid = h.new()
        h.gateway.store.execute("UPDATE threads SET status='running',turn_id='stale' WHERE id=?", (tid,))
        self.event('thread/status/changed', {'threadId': tid, 'status': {'type': 'systemError'}})
        h.until(lambda: h.gateway.thread(tid)['status'] == 'failed')
        self.assertIsNone(h.gateway.thread(tid)['turn_id'])
        h.gateway.store.execute("UPDATE threads SET status='running',turn_id='stale' WHERE id=?", (tid,))
        self.event('thread/status/changed', {'threadId': tid, 'status': {'type': 'idle'}})
        h.until(lambda: h.gateway.thread(tid)['turn_id'] is None)
        h.message(tid, 'hello', 'status-completed-1234')
        h.until(lambda: h.gateway.thread(tid)['status'] == 'completed')
        self.event('thread/status/changed', {'threadId': tid, 'status': {'type': 'idle'}})
        self.assertEqual(h.gateway.thread(tid)['status'], 'completed')


if __name__ == '__main__':
    unittest.main()
