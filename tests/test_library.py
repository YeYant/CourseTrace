import base64
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymupdf
from app import Library, UserError, make_handler


def pdf_bytes(pages):
    with pymupdf.open() as doc:
        for text in pages:
            p=doc.new_page()
            if text:
                p.insert_text((50,80),text,fontname='china-s',fontsize=12)
        return doc.tobytes()


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        # Windows runners may expose TEMP through an 8.3 short-path alias.
        self.root=Path(self.temp.name).resolve()
        self.lib=Library(self.root/'data')
        self.source=self.root/'中文资料.pdf'
        self.source.write_bytes(pdf_bytes(['理想溶液 reversible','非理想溶液 Non-ideal solution','不可逆过程 irreversible','']))
        self.doc=self.lib.index(self.source,category='教师课件')

    def tearDown(self):
        self.temp.cleanup()

    def test_negative_qualifier_and_exact_page(self):
        result=self.lib.search('非理想溶液')
        self.assertEqual([r['page'] for r in result['results']],[2])
        self.assertEqual([r['page'] for r in self.lib.search('不可逆')['results']],[3])

    def test_case_width_and_whitespace(self):
        self.assertEqual(self.lib.search('ＮＯＮ-IDEAL  solution')['total'],1)

    def test_category_and_document_filters(self):
        self.assertEqual(self.lib.search('溶液','教材')['total'],0)
        self.assertEqual(self.lib.search('溶液',doc_id=self.doc['id'])['total'],2)
        self.assertEqual(self.lib.search('溶液',doc_id='missing')['total'],0)

    def test_citation_has_pdf_page_and_unverified_printed_page(self):
        text=self.lib.page(self.doc['id'],2)['text']
        c=self.lib.citation(self.doc['id'],2,self.doc['sha'],0,len(text))
        self.assertEqual(c['pdf_page'],2)
        self.assertIsNone(c['printed_page'])
        self.assertEqual(c['sha256'],self.doc['sha'])
        self.assertIn('非理想',c['text_excerpt'])

    def test_changed_file_excluded_and_cannot_cite_or_preview(self):
        self.source.write_bytes(pdf_bytes(['新版资料']))
        result=self.lib.search('非理想')
        self.assertEqual(result['total'],0)
        self.assertEqual(result['excluded'],1)
        for action in (self.lib.citation,self.lib.image):
            with self.assertRaises(UserError) as e:
                action(self.doc['id'],2)
            self.assertEqual(e.exception.status,409)

    def test_reindex_replaces_pages_and_rejects_old_revision(self):
        self.source.write_bytes(pdf_bytes(['新版资料']))
        new=self.lib.reindex(self.doc['id'])
        self.assertNotEqual(new['sha'],self.doc['sha'])
        self.assertEqual(self.lib.search('新版')['total'],1)
        self.assertEqual(self.lib.search('非理想')['total'],0)
        with self.assertRaises(UserError):
            self.lib.citation(self.doc['id'],1,self.doc['sha'])

    def test_missing_source(self):
        self.source.unlink()
        self.assertEqual(self.lib.documents()[0]['status'],'missing')
        self.assertEqual(self.lib.search('溶液')['total'],0)

    def test_failed_reindex_keeps_old_index_but_blocks_it(self):
        self.source.write_bytes(b'not a PDF')
        with self.assertRaises(UserError):self.lib.reindex(self.doc['id'])
        self.assertEqual(self.lib.get(self.doc['id'])['sha'],self.doc['sha'])
        self.assertEqual(self.lib.search('溶液')['total'],0)

    def test_scan_only_page_is_browsable(self):
        self.assertEqual(self.doc['empty_pages'],1)
        self.assertEqual(self.lib.page(self.doc['id'],4)['text'],'')
        self.assertTrue(self.lib.image(self.doc['id'],4).startswith(b'\x89PNG'))

    def test_upload_and_safe_replacement(self):
        doc=self.lib.upload('../anything.pdf',pdf_bytes(['first']))
        self.assertEqual(Path(doc['path']).parent,self.root/'data'/'files')
        with self.assertRaises(UserError):self.lib.upload('invalid.pdf',b'bad',replace_id=doc['id'])
        self.assertEqual(self.lib.status(doc),'ready')
        new=self.lib.upload('new.pdf',pdf_bytes(['second']),replace_id=doc['id'])
        self.assertEqual(new['id'],doc['id'])
        self.assertEqual(self.lib.search('second')['total'],1)

    def test_remove_keeps_original(self):
        self.lib.remove(self.doc['id'])
        self.assertTrue(self.source.exists())
        self.assertEqual(self.lib.documents(),[])

    def test_invalid_page_and_query(self):
        for page in (0,5):
            with self.assertRaises(UserError):self.lib.page(self.doc['id'],page)
        for query in ('','x'*201):
            with self.assertRaises(UserError):self.lib.search(query)

    def test_reimport_path_does_not_duplicate(self):
        again=self.lib.index(self.source)
        self.assertEqual(again['id'],self.doc['id'])
        self.assertEqual(len(self.lib.documents()),1)

    def test_encrypted_pdf_rejected(self):
        with pymupdf.open() as doc:
            doc.new_page()
            data=doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256,owner_pw='owner',user_pw='secret')
        with self.assertRaises(UserError):self.lib.upload('secret.pdf',data)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.lib=Library(self.temp.name)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.lib))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown();self.server.RequestHandlerClass.jobs.close();self.server.server_close();self.thread.join();self.temp.cleanup()

    def test_upload_search_preview_citation_http(self):
        payload={'filename':'demo.pdf','data':base64.b64encode(pdf_bytes(['引用测试'])).decode()}
        request=Request(self.url+'/api/import',data=json.dumps(payload).encode(),headers={'X-CourseTrace':'local','Content-Type':'application/json'})
        with urlopen(request) as response:doc=json.load(response)
        with urlopen(self.url+f"/api/preview?id={doc['id']}&page=1") as response:self.assertEqual(response.headers['Content-Type'],'image/png')
        text=self.lib.page(doc['id'],1)['text']
        request=Request(self.url+'/api/citation',data=json.dumps({'id':doc['id'],'page':1,'sha':doc['sha'],'start':0,'end':len(text)}).encode(),headers={'X-CourseTrace':'local'})
        with urlopen(request) as response:self.assertIn('引用测试',json.load(response)['text_excerpt'])

    def test_rejects_cross_origin_and_missing_header(self):
        for headers in ({},{'X-CourseTrace':'local','Origin':'https://example.com'},{'X-CourseTrace':'local','Host':'evil.example'}):
            with self.assertRaises(HTTPError) as e:urlopen(Request(self.url+'/api/demo',data=b'{}',headers=headers))
            self.assertEqual(e.exception.code,403)

    def test_no_arbitrary_static_file_access(self):
        with self.assertRaises(HTTPError) as e:urlopen(self.url+'/../app.py')
        self.assertEqual(e.exception.code,404)


if __name__=='__main__':unittest.main()
