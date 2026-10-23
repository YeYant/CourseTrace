import base64
import io
import json
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, MagicMock
from urllib.request import urlopen
from http.server import ThreadingHTTPServer

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pymupdf
from library import Library, UserError, Cancelled, check_cancel, matches
from app import Jobs, make_handler, create_server, InstanceLock
from launcher import find_instance


def make_pdf(text):
    with pymupdf.open() as d:
        p=d.new_page()
        remaining=p.insert_textbox((30,30,560,810),text,fontsize=9,fontname='china-s')
        assert remaining >= 0
        return d.tobytes()


class ReleaseLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        # Compare canonical paths, including Windows 8.3 TEMP aliases.
        self.root=Path(self.temp.name).resolve()
        self.lib=Library(self.root/'project'/'data')

    def tearDown(self):
        self.temp.cleanup()

    def test_tail_hit_export_and_multiple_occurrences(self):
        text='target at beginning\n'+'\n'.join(['原创测试段落，用于验证较长页面中的引用范围。']*30)+'\ntarget at end'
        doc=self.lib.upload('long.pdf',make_pdf(text))
        r=self.lib.page(doc['id'],1,doc['sha'],'TARGET')
        self.assertEqual(len(r['matches']),2)
        self.assertGreater(r['matches'][-1]['hit_start'],600)
        for hit in r['matches']:
            c=self.lib.citation(doc['id'],1,doc['sha'],hit['start'],hit['end'])
            self.assertEqual(c['text_excerpt'],r['text'][hit['start']:hit['end']])
            self.assertIn('target',c['text_excerpt'])
        self.assertIn('target at end',c['text_excerpt'])
        self.assertTrue(c['partial_page'])

    def test_page_only_and_invalid_ranges(self):
        doc=self.lib.upload('test.pdf',make_pdf('source text'))
        self.assertEqual(self.lib.citation(doc['id'],1,doc['sha'])['text_excerpt'],'')
        for start,end,sha in [(0,9999,doc['sha']),(-1,3,doc['sha']),(3,1,doc['sha']),(True,3,doc['sha']),(0,3,None),(0,3,'old')]:
            with self.assertRaises(UserError):self.lib.citation(doc['id'],1,sha,start,end)

    def test_unicode_offsets_and_all_matches(self):
        text='开头 😀 ＮＯＮ ideal\nsolution，中间 Café，结尾 Cafe\u0301。non ideal solution'
        hits=matches(text,'non IDEAL solution')
        self.assertEqual(len(hits),2)
        self.assertEqual(text[hits[0]['hit_start']:hits[0]['hit_end']],'ＮＯＮ ideal\nsolution')
        self.assertEqual(len(matches(text,'café')),2)

    def test_portable_managed_paths_after_move(self):
        doc=self.lib.upload('copy.pdf',make_pdf('portable'))
        with self.lib.connect() as db:
            stored=db.execute('SELECT path FROM documents').fetchone()[0]
        self.assertTrue(stored.startswith('files/'))
        old=self.root/'project';new=self.root/'moved'
        assert old.resolve().is_relative_to(self.root) and new.resolve().is_relative_to(self.root)
        old.rename(new)
        moved=Library(new/'data')
        self.assertEqual(moved.documents()[0]['status'],'ready')
        self.assertEqual(moved.search('portable')['total'],1)
        self.assertEqual(moved.citation(doc['id'],1,doc['sha'])['sha256'],doc['sha'])

    def test_legacy_migration_backed_up_and_external_path_preserved(self):
        doc=self.lib.upload('copy.pdf',make_pdf('legacy'))
        external=self.root/'external.pdf';external.write_bytes(make_pdf('external'))
        ext=self.lib.index(external)
        with self.lib.connect() as db:
            db.execute('UPDATE documents SET path=? WHERE id=?',(doc['path'],doc['id']))
            db.execute('PRAGMA user_version=0')
        old=self.root/'project';new=self.root/'moved'
        assert old.resolve().is_relative_to(self.root) and new.resolve().is_relative_to(self.root)
        old.rename(new)
        migrated=Library(new/'data')
        self.assertEqual(migrated.status(migrated.get(doc['id'])),'ready')
        self.assertEqual(migrated.get(ext['id'])['path'],str(external.resolve()))
        backups=list((new/'data'/'backups').glob('*.sqlite3'))
        self.assertEqual(len(backups),1)
        db=sqlite3.connect(backups[0])
        try:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT path FROM documents WHERE id=?',(doc['id'],)).fetchone()[0],doc['path'])
        finally:db.close()
        Library(new/'data')
        self.assertEqual(len(list((new/'data'/'backups').glob('*.sqlite3'))),1)

    def test_relocation_requires_changed_content_confirmation(self):
        old=self.root/'old.pdf';old.write_bytes(make_pdf('old content'))
        doc=self.lib.index(old)
        same=self.root/'same.pdf';same.write_bytes(old.read_bytes())
        new=self.root/'new.pdf';new.write_bytes(make_pdf('new content'))
        self.lib.relocate(doc['id'],same)
        with self.assertRaises(UserError):self.lib.relocate(doc['id'],new)
        self.assertEqual(self.lib.get(doc['id'])['path'],str(same))
        self.lib.relocate(doc['id'],new,allow_changed=True)
        self.assertEqual(self.lib.search('new content')['total'],1)
        with self.assertRaises(UserError):self.lib.citation(doc['id'],1,doc['sha'])

    def test_single_document_search_only_reads_selected_file(self):
        a=self.lib.upload('a.pdf',make_pdf('source A'))
        self.lib.upload('b.pdf',make_pdf('source B'))
        read=Path.read_bytes;seen=[]
        def tracked(path):
            if path.suffix=='.pdf':seen.append(str(path))
            return read(path)
        with patch.object(Path,'read_bytes',tracked):self.lib.search('source',doc_id=a['id'])
        self.assertEqual(seen,[a['path']])

    def test_cancel_actual_worker_does_not_commit(self):
        cancel=threading.Event()
        import subprocess
        original=subprocess.Popen;spawned=[]
        def start(*args,**kwargs):
            process=original(*args,**kwargs);spawned.append(process);cancel.set()
            return process
        with patch('library.subprocess.Popen',side_effect=start):
            with self.assertRaises(Cancelled):self.lib.upload('cancel.pdf',make_pdf('cancel'),cancel=cancel)
        self.assertEqual(len(spawned),1)
        self.assertIsNotNone(spawned[0].poll())
        self.assertEqual(self.lib.documents(),[])
        self.assertEqual(list((self.lib.directory/'files').iterdir()),[])

    def test_worker_timeout_preserves_previous_copy(self):
        doc=self.lib.upload('valid.pdf',make_pdf('valid'))
        self.lib.worker_timeout=.001
        with self.assertRaises(UserError):self.lib.upload('new.pdf',make_pdf('new'),replace_id=doc['id'])
        self.assertEqual(self.lib.status(doc),'ready')

    def test_failed_database_commit_restores_copy(self):
        doc=self.lib.upload('valid.pdf',make_pdf('valid'))
        with patch.object(self.lib,'store',side_effect=sqlite3.OperationalError('simulated disk error')):
            with self.assertRaises(sqlite3.OperationalError):self.lib.upload('new.pdf',make_pdf('new'),replace_id=doc['id'])
        self.assertEqual(self.lib.status(doc),'ready')


class ReleaseServerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.lib=Library(self.temp.name)
        handler=make_handler(self.lib);handler.body_timeout=.3
        self.server=ThreadingHTTPServer(('127.0.0.1',0),handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown();self.server.RequestHandlerClass.jobs.close();self.server.server_close();self.thread.join();self.temp.cleanup()

    def test_partial_upload_does_not_block_and_times_out(self):
        with socket.create_connection(('127.0.0.1',self.server.server_port)) as sock:
            sock.settimeout(2)
            sock.sendall((f'POST /api/demo HTTP/1.1\r\nHost: 127.0.0.1:{self.server.server_port}\r\nX-CourseTrace: local\r\nContent-Length: 20\r\n\r\n{{').encode())
            with urlopen(self.url+'/api/health',timeout=1) as r:self.assertEqual(r.status,200)
            with urlopen(self.url+'/',timeout=1) as r:self.assertEqual(r.status,200)
            self.assertIn(b'408',sock.recv(8192))

    def test_job_progress_cancellation_and_server_responsiveness(self):
        ready=threading.Event()
        def slow(raw,progress=None,cancel=None):
            progress(page=1,total=100);ready.set();cancel.wait(3);check_cancel(cancel)
            return ['never committed']
        jobs=self.server.RequestHandlerClass.jobs
        with patch.object(self.lib,'extract',slow):
            job=jobs.submit('import',{'filename':'test.pdf','data':base64.b64encode(b'synthetic').decode()})
            self.assertTrue(ready.wait(2))
            self.assertEqual(jobs.get(job['id'])['page'],1)
            with urlopen(self.url+'/api/health',timeout=1) as r:self.assertEqual(r.status,200)
            jobs.cancel(job['id'])
            for _ in range(100):
                if jobs.get(job['id'])['state']=='cancelled':break
                time.sleep(.01)
            self.assertEqual(jobs.get(job['id'])['state'],'cancelled')
        self.assertEqual(self.lib.documents(),[])

    def test_occupied_port_falls_back_to_loopback(self):
        other=create_server(self.lib,self.server.server_port)
        try:
            self.assertNotEqual(other.server_port,self.server.server_port)
            self.assertEqual(other.server_address[0],'127.0.0.1')
        finally:other.RequestHandlerClass.jobs.close();other.server_close()


class LauncherTests(unittest.TestCase):
    def test_non_object_or_wrong_health_response_ignored(self):
        for value in ([],None,'ok',{'app':'other'},{'app':'CourseTrace','root':[]}):
            opener=MagicMock();opener.open.side_effect=lambda *_args,**_kw:io.BytesIO(json.dumps(value).encode())
            with tempfile.TemporaryDirectory() as td,patch('launcher.build_opener',return_value=opener):
                self.assertIsNone(find_instance(Path(td)))

    def test_recorded_fallback_port_is_verified_and_reused(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'data').mkdir()
            (root/'data'/'server.json').write_text(json.dumps({'port':54321}))
            opener=MagicMock();opener.open.side_effect=lambda *_args,**_kw:io.BytesIO(json.dumps({'app':'CourseTrace','root':str(root)}).encode())
            with patch('launcher.build_opener',return_value=opener):self.assertEqual(find_instance(root),'http://127.0.0.1:54321')

    def test_single_instance_lock_is_released(self):
        with tempfile.TemporaryDirectory() as td:
            with InstanceLock(td):
                with self.assertRaises(UserError):
                    with InstanceLock(td):pass
            with InstanceLock(td):pass


if __name__=='__main__':unittest.main()
