"""Release boundaries: bounded replay, conservative cleanup and safe backup/restore."""
import base64
import fcntl
import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from codex_remote.server import Store
from codex_remote.storage import backup_state, restore_state, clean_uploads, initialize_uploads
from codex_remote.i18n import translate, locale
import test_remote


class StorageTests(unittest.TestCase):
    def test_replay_floor_is_per_thread_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'gateway.sqlite3'
            store = Store(path)
            for tid in ('a','b','a','b','a'):
                store.event(tid,'fixture',{})
            self.assertEqual(store.prune_events(2),3)
            self.assertTrue(store.replay_expired('a',0))
            self.assertFalse(store.replay_expired('a',3))
            self.assertFalse(store.replay_expired('other',0))
            store.close()
            store = Store(path)
            self.assertTrue(store.replay_expired('b',1))
            self.assertEqual(len(store.rows('SELECT * FROM events')),2)
            store.close()

    def test_migration_backups_and_newer_schema_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'gateway.sqlite3'
            db=sqlite3.connect(path); db.execute('CREATE TABLE legacy(value TEXT)'); db.commit(); db.close()
            store=Store(path)
            self.assertTrue(Path(str(path)+'.pre-v1.sqlite3').is_file())
            store.execute('PRAGMA user_version=999'); store.close()
            with self.assertRaisesRegex(ValueError,'newer'):
                Store(path)

    def test_backup_restore_checksums_paths_and_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); state=root/'state'; state.mkdir()
            store=Store(state/'gateway.sqlite3')
            store.execute('CREATE TABLE uploads(id TEXT PRIMARY KEY,name TEXT,path TEXT,mime TEXT,size INTEGER)')
            initialize_uploads(store)
            aid='a'*32; attachment=state/'uploads'/aid/'example.txt';attachment.parent.mkdir(parents=True);attachment.write_text('fixture')
            store.execute('INSERT INTO uploads VALUES(?,?,?,?,?)',(aid,'example.txt',str(attachment),'text/plain',7))
            store.close();(state/'access-token').write_text('fixture-token-not-a-secret')
            with (state/'gateway.lock').open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX)
                with self.assertRaisesRegex(ValueError,'running'):
                    backup_state(state,root/'backup')
            backup_state(state,root/'backup')
            restore_state(root/'backup',root/'restored')
            db=sqlite3.connect(root/'restored/gateway.sqlite3')
            self.assertEqual(db.execute('SELECT path FROM uploads').fetchone()[0],str(root/'restored/uploads'/aid/'example.txt'));db.close()
            self.assertEqual((root/'restored/uploads'/aid/'example.txt').read_text(),'fixture')
            with self.assertRaisesRegex(ValueError,'new state'):
                restore_state(root/'backup',root/'restored')
            (root/'backup/access-token').write_text('changed')
            with self.assertRaisesRegex(ValueError,'checksum'):
                restore_state(root/'backup',root/'invalid')
            self.assertFalse((root/'invalid').exists())


class ReleaseGatewayTests(unittest.TestCase):
    def setUp(self):
        self.h=test_remote.RemoteTests();self.h.setUp()

    def tearDown(self):
        self.h.tearDown()

    def test_cleanup_keeps_used_and_recent_uploads(self):
        h=self.h; upload=lambda name:h.gateway.upload(name,base64.b64encode(b'fixture').decode())['id']
        old,used,recent=upload('old.txt'),upload('used.txt'),upload('recent.txt')
        h.gateway.store.execute('UPDATE upload_lifecycle SET created=? WHERE id IN (?,?)',(time.time()-8*86400,old,used))
        h.gateway._inputs('fixture',[used])
        self.assertEqual(h.request('/api/storage/cleanup',{})[0],401)
        result=h.request('/api/storage/cleanup',{},h.cookie)
        self.assertEqual(result[1]['eligibleFiles'],1)
        self.assertEqual({r['id'] for r in h.gateway.store.rows('SELECT id FROM uploads')},{used,recent})
        self.assertFalse((h.gateway.upload_dir/old).exists())

    def test_expired_sse_cursor_requests_native_history_refresh(self):
        h=self.h;tid=h.new()
        for i in range(4):h.gateway.store.event(tid,'fixture',{'value':i})
        h.gateway.store.prune_events(1)
        conn=http.client.HTTPConnection('127.0.0.1',h.port,timeout=5)
        conn.request('GET',f'/api/threads/{tid}/events?after=0',headers={'Cookie':h.cookie})
        response=conn.getresponse();self.assertEqual(response.status,200)
        self.assertEqual(response.readline().decode().strip(),'event: reset')
        conn.close()
        self.assertIn('turns',h.gateway.sync_thread(tid))
        other=h.new()
        h.gateway.store.event(other,'fixture',{})
        h.gateway.store.prune_events(1)
        snapshot=h.gateway.sync_thread(tid)
        self.assertFalse(h.gateway.store.replay_expired(tid,snapshot['after']))

    def test_connection_cap_and_localized_gateway_errors(self):
        h=self.h
        h.until(lambda: h.web.http_slots._value == 32)
        for _ in range(32):self.assertTrue(h.web.http_slots.acquire(False))
        try:self.assertEqual(h.request('/healthz')[0],503)
        finally:
            for _ in range(32):h.web.http_slots.release()
        for language,expected in [('en','Sign in first.'),('zh-CN','请先登录。')]:
            conn=http.client.HTTPConnection('127.0.0.1',h.port,timeout=5)
            conn.request('GET','/api/bootstrap',headers={'Accept-Language':language})
            response=conn.getresponse();self.assertEqual(json.loads(response.read())['error'],expected);conn.close()
        self.assertEqual(translate('Private conversation text','zh-CN'),'Private conversation text')
        self.assertEqual(locale('fr-FR'),'en')
