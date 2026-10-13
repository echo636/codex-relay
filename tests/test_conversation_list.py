"""List filtering is reversible and must never control native conversations."""
import json
import unittest
from unittest.mock import patch

import test_remote
from codex_remote.conversation_list import visibility_fields


class ClassificationTests(unittest.TestCase):
    def test_batch_and_subagent_sources_are_hidden_but_project_coding_is_kept(self):
        coding = {'title': 'Fix the example application', 'source': '"vscode"',
                  'cwd': '/workspace/example-project', 'has_content': 1}
        self.assertFalse(visibility_fields(coding)['list_hidden'])
        for source in ['exec', '"exec"', {'subAgent': {'thread_spawn': {}}}, json.dumps({'subagent': {}})]:
            with self.subTest(source=source):
                self.assertTrue(visibility_fields({**coding, 'source': source})['list_hidden'])
        task = {**coding, 'title': 'Run a batch task', 'source': 'exec'}
        self.assertEqual(visibility_fields(task)['list_hidden_reason'], 'automation')
        # A directory name alone never identifies an experiment.
        self.assertFalse(visibility_fields({**coding, 'cwd': '/project/example-project'})['list_hidden'])

    def test_empty_names_and_overrides(self):
        empty = {'title': 'New conversation', 'source': '"cli"', 'has_content': 0}
        self.assertEqual(visibility_fields(empty)['list_hidden_reason'], 'empty')
        self.assertFalse(visibility_fields({**empty, 'has_content': 1})['list_hidden'])
        self.assertFalse(visibility_fields({**empty, 'title': 'My coding project'})['list_hidden'])
        self.assertFalse(visibility_fields({**empty, 'list_visibility': 'show'})['list_hidden'])
        self.assertEqual(visibility_fields({**empty, 'has_content': 1, 'list_visibility': 'hide'})['list_hidden_reason'], 'manual')


class ListPreferenceTests(unittest.TestCase):
    def setUp(self):
        self.h = test_remote.RemoteTests()
        self.h.setUp()
        self.h.gateway.shared = True

    def tearDown(self):
        self.h.tearDown()

    def test_override_is_shared_persistent_and_never_calls_native_control(self):
        h = self.h
        native = h.gateway.rpc.call('thread/start', {'cwd': str(h.work)})['thread']
        native.update(source='exec', preview='experiment')
        h.gateway._adopt(native)
        tid = native['id']
        self.assertTrue(h.gateway.thread(tid)['list_hidden'])
        with patch.object(h.gateway.rpc, 'call') as rpc:
            self.assertEqual(h.request(f'/api/threads/{tid}/visibility', {'visibility': 'show'})[0], 401)
            for value in ['show', 'hide', 'auto']:
                status, thread, _ = h.request(f'/api/threads/{tid}/visibility', {'visibility': value}, h.cookie)
                self.assertEqual(status, 200)
                h.gateway._adopt(native)  # A later sync cannot undo the user's choice.
                self.assertEqual(h.gateway.thread(tid)['list_visibility'], value)
                self.assertEqual(h.gateway.thread(tid)['selected'], 0)
                data = h.request('/api/bootstrap', cookie=h.cookie)[1]
                row = next(r for r in data['threads'] if r['id'] == tid)
                self.assertEqual(row['list_hidden'], value != 'show')
            self.assertEqual(h.request(f'/api/threads/{tid}/visibility', {'visibility': 'delete'}, h.cookie)[0], 400)
            rpc.assert_not_called()
        self.assertEqual(h.gateway.store.rows('SELECT archived FROM threads WHERE id=?', (tid,))[0]['archived'], 0)

    def test_discovery_asks_for_interactive_sources_and_can_include_automation(self):
        h = self.h
        with patch.object(h.gateway.rpc, 'call', wraps=h.gateway.rpc.call) as rpc:
            h.gateway.discover()
            request = next(c for c in rpc.call_args_list if c.args[0] == 'thread/list')
            self.assertEqual(request.args[1]['sourceKinds'], ['cli', 'vscode', 'appServer'])
            self.assertTrue(request.args[1]['useStateDbOnly'])
            self.assertLessEqual({c.args[0] for c in rpc.call_args_list}, {'thread/list', 'thread/loaded/list', 'thread/read'})
            rpc.reset_mock()
            h.request('/api/discover', {'includeAutomation': True}, h.cookie)
            request = next(c for c in rpc.call_args_list if c.args[0] == 'thread/list')
            self.assertIn('exec', request.args[1]['sourceKinds'])

    def test_first_message_makes_untitled_chat_visible_even_after_stale_metadata(self):
        h = self.h
        tid = h.new()
        self.assertTrue(h.gateway.thread(tid)['list_hidden'])
        h.message(tid, 'help fix the project', 'visibility-first-message')
        h.until(lambda: h.gateway.thread(tid)['status'] == 'completed')
        self.assertFalse(h.gateway.thread(tid)['list_hidden'])
        h.gateway._adopt({'id': tid, 'cwd': str(h.work), 'preview': '', 'source': 'cli'})
        self.assertFalse(h.gateway.thread(tid)['list_hidden'])


if __name__ == '__main__':
    unittest.main()
