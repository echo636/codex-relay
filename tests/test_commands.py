"""Native command routing tests. Only fake_codex.py is used."""
from unittest.mock import patch

import test_remote
from codex_remote.server import APIError


class CommandTests(test_remote.RemoteTests):
    def test_permissions_native_update_and_idempotency(self):
        tid = self.new()
        self.assertFalse(self.gateway.permissions(tid)['fullAccess'])
        body = {'command': 'permissions', 'action': 'full-access', 'submissionId': 'permissions-command-001'}
        self.message(tid, 'hang', 'permission-active-turn-001')
        with patch.object(self.gateway.rpc, 'call', wraps=self.gateway.rpc.call) as calls:
            first = self.request(f'/api/threads/{tid}/command', body, self.cookie)
            second = self.request(f'/api/threads/{tid}/command', body, self.cookie)
        self.assertEqual(first[0], 200, first)
        self.assertEqual(first[1], second[1])
        self.assertTrue(first[1]['permissions']['fullAccess'])
        self.assertTrue(first[1]['permissions']['nextTurn'])
        self.assertEqual([c.args for c in calls.call_args_list], [('thread/settings/update', {
            'threadId': tid, 'sandboxPolicy': {'type': 'dangerFullAccess'}, 'approvalPolicy': 'never'})])
        self.assertEqual(self.gateway.thread(tid)['status'], 'running')
        self.assertEqual(len(self.gateway.history(tid)['turns']), 1)
        self.assertEqual(self.request(f'/api/threads/{tid}/permissions')[0], 401)
        self.assertEqual(self.request(f'/api/threads/{tid}/command', {**body, 'action': 'invalid'}, self.cookie)[0], 400)

    def test_full_access_defaults_apply_only_to_connected_threads(self):
        self.gateway.shared = True
        self.gateway.sandbox, self.gateway.approval = 'danger-full-access', 'never'
        tid = self.new()
        self.assertTrue(self.gateway.permissions(tid)['fullAccess'])
        other = self.gateway.rpc.call('thread/start', {'cwd': str(self.work)})['thread']['id']
        with patch.object(self.gateway.rpc, 'call', wraps=self.gateway.rpc.call) as calls:
            self.gateway.discover()
            self.gateway.preview(other)
            self.assertTrue(self.gateway.permissions(other)['needsConnection'])
        self.assertFalse(any(c.args[0] in ('thread/resume', 'thread/settings/update') for c in calls.call_args_list))
        self.gateway.enable_control(other)
        self.assertTrue(self.gateway.permissions(other)['fullAccess'])
        native = self.gateway.rpc.call('thread/read', {'threadId': other})['thread']
        self.assertEqual(native['settings']['model'], 'fixture-model')
        self.assertEqual(native['settings']['cwd'], str(self.work))
        # Reconnection reads native state and restores the chosen gateway default.
        self.gateway.rpc.call('thread/settings/update', {'threadId': other, 'approvalPolicy': 'on-request'})
        self.gateway.loaded.discard(other)
        self.gateway.attach(other)
        self.assertTrue(self.gateway.permissions(other)['fullAccess'])

    def test_permission_rejection_is_not_reported_as_full_access(self):
        tid = self.new()
        body = {'command': 'permissions', 'action': 'full-access', 'submissionId': 'permissions-reject-001'}
        real = self.gateway.rpc.call
        def reject(method, params=None, **kwargs):
            if method == 'thread/settings/update': raise APIError(502, 'unknown method thread/settings/update')
            return real(method, params, **kwargs)
        with patch.object(self.gateway.rpc, 'call', side_effect=reject):
            self.assertEqual(self.request(f'/api/threads/{tid}/command', body, self.cookie)[0], 501)
        self.assertFalse(self.gateway.permissions(tid)['fullAccess'])
        self.assertEqual(self.request(f'/api/threads/{tid}/command', body, self.cookie)[0], 200)

    def native_command(self, tid, action='set', key='command-fixture-0001', **body):
        return self.request(f'/api/threads/{tid}/command', {'command': 'goal', 'action': action, 'submissionId': key, **body}, self.cookie)

    def test_goal_lifecycle_and_budget(self):
        tid = self.new()
        self.assertIsNone(self.gateway.get_goal(tid)['goal'])
        result = self.native_command(tid, objective='Pass all tests', tokenBudget=50000)
        self.assertEqual(result[0], 200, result)
        self.assertEqual(result[1]['goal']['tokenBudget'], 50000)
        self.assertEqual(result[1]['goal']['status'], 'active')
        self.gateway.rpc.call('fixture/goal/progress', {'threadId': tid, 'progress': {'tokensUsed': 321, 'timeUsedSeconds': 65}})
        self.native_command(tid, 'pause', 'command-pause-0001')
        current = self.gateway.get_goal(tid)['goal']
        self.assertEqual(current['status'], 'paused')
        self.assertEqual(current['tokensUsed'], 321)
        edited = self.native_command(tid, 'edit', 'command-edit-00001', objective='Pass updated tests')[1]['goal']
        self.assertEqual(edited['status'], 'paused')
        self.assertEqual(edited['tokensUsed'], 0)
        self.assertEqual(self.native_command(tid, 'resume', 'command-resume-001')[1]['goal']['status'], 'active')
        self.assertEqual(self.native_command(tid, 'clear', 'command-clear-001')[0], 200)
        self.assertIsNone(self.gateway.get_goal(tid)['goal'])
        self.assertEqual(self.gateway.history(tid)['turns'], [], 'Management commands became model messages')

    def test_goal_no_accidental_activation_and_validation(self):
        tid = self.new()
        with patch.object(self.gateway.rpc, 'call', wraps=self.gateway.rpc.call) as calls:
            for values in ({'objective': ''}, {'objective': 'a'*4001}, {'objective': 4},
                           {'objective': 'x', 'tokenBudget': 0}, {'objective': 'x', 'tokenBudget': True},
                           {'objective': 'x', 'tokenBudget': 1.5}):
                self.assertEqual(self.native_command(tid, **values)[0], 400)
            self.assertFalse(any(c.args[0] == 'thread/goal/set' for c in calls.call_args_list))
        self.assertEqual(self.native_command(tid, 'resume')[0], 409)
        self.assertEqual(self.request(f'/api/threads/{tid}/command', {'command': 'goal', 'action': 'clear', 'submissionId': 'unauth-command-0001'})[0], 401)
        self.assertEqual(self.native_command(tid, command='unsupported')[0], 400)
        self.assertEqual(self.native_command(tid, objective='x', expectedGoal={'objective': 'stale', 'createdAt': 1})[0], 409)

    def test_native_rpc_mapping_and_deduplication(self):
        tid = self.new(); real = self.gateway.rpc.call
        with patch.object(self.gateway.rpc, 'call', wraps=real) as calls:
            first = self.native_command(tid, objective='Unique goal')
            second = self.native_command(tid, objective='Unique goal')
        self.assertEqual(first[1], second[1])
        mutations = [c for c in calls.call_args_list if c.args[0] == 'thread/goal/set']
        self.assertEqual(len(mutations), 1)
        self.assertEqual(mutations[0].args[1], {'threadId': tid, 'objective': 'Unique goal', 'status': 'active'})
        self.assertEqual(self.native_command(tid, objective='Different goal')[0], 409)
        self.assertFalse(any(c.args[0] in ('turn/start', 'turn/steer') for c in calls.call_args_list))
        self.gateway.rpc.call('fixture/goal/progress', {'threadId': tid, 'progress': {'status': 'complete'}})
        self.assertEqual(self.native_command(tid, 'resume', 'resume-complete-001')[0], 409)

    def test_ambiguous_command_is_never_resent(self):
        tid = self.new(); real = self.gateway.rpc.call
        def timeout(method, params=None, **kwargs):
            if method == 'thread/goal/set':
                real(method, params); raise APIError(504, 'fixture timeout after delivery')
            return real(method, params, **kwargs)
        with patch.object(self.gateway.rpc, 'call', side_effect=timeout):
            self.assertEqual(self.native_command(tid, objective='Timeout goal')[0], 504)
        with patch.object(self.gateway.rpc, 'call', wraps=real) as calls:
            self.assertEqual(self.native_command(tid, objective='Timeout goal')[0], 409)
            self.assertFalse(calls.called)
        self.assertEqual(self.gateway.get_goal(tid)['goal']['objective'], 'Timeout goal')

    def test_native_rejection_is_visible_and_retryable(self):
        tid = self.new(); real = self.gateway.rpc.call
        def reject(method, params=None, **kwargs):
            if method == 'thread/goal/set': raise APIError(502, 'unknown method thread/goal/set')
            return real(method, params, **kwargs)
        with patch.object(self.gateway.rpc, 'call', side_effect=reject):
            result = self.native_command(tid, objective='Unsupported')
            self.assertEqual(result[0], 501, result)
        self.assertEqual(self.native_command(tid, objective='Unsupported')[0], 200)

    def test_preview_never_probes_goal_or_resumes(self):
        self.gateway.shared = True
        native = self.gateway.rpc.call('thread/start', {'cwd': str(self.work)})['thread']
        self.gateway.discover(); tid = native['id']
        with patch.object(self.gateway.rpc, 'call', wraps=self.gateway.rpc.call) as calls:
            self.assertTrue(self.gateway.get_goal(tid)['needsConnection'])
            self.assertEqual(self.native_command(tid, objective='Do not take over')[0], 409)
        self.assertFalse(calls.called)
        self.assertNotIn(tid, self.gateway.loaded)

    def test_compaction_and_literal_slash_input(self):
        tid = self.new()
        self.assertEqual(self.message(tid, '/goal hidden', 'slash-message-0001')[0], 400)
        self.assertEqual(self.gateway.history(tid)['turns'], [])
        self.assertEqual(self.message(tid, '/goal literal', 'literal-message-001', literal=True)[0], 200)
        self.until(lambda: self.gateway.thread(tid)['turn_id'] is None)
        result = self.native_command(tid, command='compact', key='compact-fixture-0001')
        self.assertEqual(result[0], 200, result)
        count = len(self.gateway.history(tid)['turns'])
        self.assertEqual(self.native_command(tid, command='compact', key='compact-fixture-0001')[0], 200)
        self.assertEqual(len(self.gateway.history(tid)['turns']), count)
        self.message(tid, 'hang', 'command-active-0001')
        self.assertEqual(self.native_command(tid, command='compact', key='compact-active-0001')[0], 409)
        self.assertEqual(self.native_command(tid, objective='During active turn', key='during-active-0001')[0], 200)
