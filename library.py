"""Version-bound local library. SPDX-License-Identifier: AGPL-3.0-or-later."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import uuid

ROOT = Path(__file__).resolve().parent
VERSION = '0.1.0-alpha.1'
CATEGORIES = ('教师课件', '教材', '补充资料')
MAX_FILE = 50 * 1024 * 1024


class UserError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class Cancelled(UserError):
    def __init__(self):
        super().__init__('操作已取消，资料未提交。', 409)


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def normalized_map(text):
    parts, starts, ends = [], [], []
    i = 0
    while i < len(text):
        end = i + 1
        while end < len(text) and unicodedata.combining(text[end]):
            end += 1
        for char in unicodedata.normalize('NFKC', text[i:end]).casefold():
            if not char.isspace():
                parts.append(char); starts.append(i); ends.append(end)
        i = end
    return ''.join(parts), starts, ends


def normalized(text):
    return normalized_map(text)[0]


def matches(text, query):
    needle = normalized(query)
    if not needle:
        return []
    haystack, starts, ends = normalized_map(text)
    output, offset = [], 0
    while len(output) < 100:
        found = haystack.find(needle, offset)
        if found < 0:
            break
        start, end = starts[found], ends[found + len(needle) - 1]
        left, right = max(0, start - 90), min(len(text), end + 160)
        output.append({'start': left, 'end': right, 'hit_start': start, 'hit_end': end,
                       'text': text[left:right], 'truncated': left > 0 or right < len(text)})
        offset = found + len(needle)
    return output


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class Library:
    def __init__(self, directory, worker_timeout=120):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / 'files').mkdir(exist_ok=True)
        self.dbfile = self.directory / 'library.sqlite3'
        self.lock = threading.RLock()
        self.worker_timeout = worker_timeout
        existed = self.dbfile.exists()
        with self.connect() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > 1:
                raise UserError('资料库版本较新，请使用对应的新版本程序。')
            if existed and version == 0:
                backup_dir = self.directory / 'backups'
                backup_dir.mkdir(exist_ok=True)
                backup = sqlite3.connect(backup_dir / ('library-v0-' + uuid.uuid4().hex[:10] + '.sqlite3'))
                try:
                    db.backup(backup)
                finally:
                    backup.close()
            db.executescript('''
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL,
                    path TEXT NOT NULL UNIQUE, managed INTEGER NOT NULL, sha TEXT NOT NULL,
                    page_count INTEGER NOT NULL, empty_pages INTEGER NOT NULL,
                    indexed_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ready');
                CREATE TABLE IF NOT EXISTS pages (
                    doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    number INTEGER NOT NULL, text TEXT NOT NULL, searchable TEXT NOT NULL,
                    PRIMARY KEY(doc_id,number));
            ''')
            if version == 0:
                for row in db.execute('SELECT id,path FROM documents WHERE managed=1').fetchall():
                    filename = PureWindowsPath(row['path']).name
                    db.execute('UPDATE documents SET path=? WHERE id=?', ('files/' + filename, row['id']))
                for row in db.execute('SELECT doc_id,number,text FROM pages').fetchall():
                    db.execute('UPDATE pages SET searchable=? WHERE doc_id=? AND number=?',
                               (normalized(row['text']), row['doc_id'], row['number']))
                db.execute('PRAGMA user_version=1')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.dbfile, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def public(self, row):
        doc = dict(row)
        doc['stored_path'] = doc['path']
        if doc['managed']:
            target = (self.directory / doc['path']).resolve()
            if not target.is_relative_to(self.directory / 'files'):
                raise UserError('资料库中的副本路径无效。')
            doc['path'] = str(target)
        return doc

    def get(self, doc_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM documents WHERE id=?', (doc_id,)).fetchone()
        if not row:
            raise UserError('资料不存在。', 404)
        return self.public(row)

    def read_pdf(self, path):
        try:
            if path.suffix.lower() != '.pdf' or not path.is_file():
                raise UserError('请选择存在的 PDF 文件。')
            if path.stat().st_size > MAX_FILE:
                raise UserError('单个 PDF 最大支持 50 MB。')
            with path.open('rb') as stream:
                raw = stream.read(MAX_FILE + 1)
            if len(raw) > MAX_FILE:
                raise UserError('单个 PDF 最大支持 50 MB。')
            return raw
        except OSError as error:
            raise UserError('无法读取文件，请检查路径和读取权限。') from error

    def run_pdf(self, raw, mode='extract', number=1, progress=None, cancel=None):
        check_cancel(cancel)
        scratch = self.directory / '.tasks'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as tmp:
            folder = Path(tmp)
            (folder / 'input.pdf').write_bytes(raw)
            process = subprocess.Popen([sys.executable, str(ROOT / 'pdf_worker.py'), str(folder), mode, str(number)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            deadline = time.monotonic() + self.worker_timeout
            try:
                while process.poll() is None:
                    check_cancel(cancel)
                    if time.monotonic() > deadline:
                        raise UserError('PDF 处理超过时间限制，请拆分文件后重试。', 408)
                    if progress and (folder / 'progress.json').exists():
                        try:
                            progress(**json.loads((folder / 'progress.json').read_text('utf-8')))
                        except (OSError, ValueError):
                            pass
                    time.sleep(.03)
                check_cancel(cancel)
                try:
                    result = json.loads((folder / 'result.json').read_text('utf-8'))
                except (OSError, ValueError) as error:
                    raise UserError('PDF 处理进程意外退出，资料未提交。') from error
                if 'error' in result:
                    raise UserError(result['error'])
                if mode == 'extract':
                    return result['texts']
                return (folder / 'page.png').read_bytes()
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()

    def extract(self, raw, progress=None, cancel=None):
        return self.run_pdf(raw, progress=progress, cancel=cancel)

    def store(self, path, raw, texts, title, category, managed, doc_id=None):
        stored = path.relative_to(self.directory).as_posix() if managed else str(path)
        with self.connect() as db:
            existing = db.execute('SELECT id FROM documents WHERE path=?', (stored,)).fetchone()
            if existing and doc_id and existing['id'] != doc_id:
                raise UserError('此路径已被另一份资料使用。')
            doc_id = doc_id or (existing['id'] if existing else uuid.uuid4().hex)
            db.execute('''INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,'ready')
                ON CONFLICT(id) DO UPDATE SET title=excluded.title,category=excluded.category,
                path=excluded.path,managed=excluded.managed,sha=excluded.sha,page_count=excluded.page_count,
                empty_pages=excluded.empty_pages,indexed_at=excluded.indexed_at,status='ready' ''',
                (doc_id,(title.strip() or path.stem)[:180],category,stored,int(managed),
                 hashlib.sha256(raw).hexdigest(),len(texts),sum(not t.strip() for t in texts),now()))
            db.execute('DELETE FROM pages WHERE doc_id=?', (doc_id,))
            db.executemany('INSERT INTO pages VALUES (?,?,?,?)',
                           [(doc_id,i+1,t,normalized(t)) for i,t in enumerate(texts)])
        return self.get(doc_id)

    def index(self, path, title='', category='补充资料', managed=False, doc_id=None, progress=None, cancel=None):
        if category not in CATEGORIES:
            raise UserError('请选择有效的资料类型。')
        path = Path(path).expanduser().resolve()
        previous = self.get(doc_id) if doc_id else None
        raw = self.read_pdf(path)
        texts = self.extract(raw, progress, cancel)
        with self.lock:
            check_cancel(cancel)
            if previous and self.get(doc_id)['sha'] != previous['sha']:
                raise UserError('资料已被另一个操作更新，请重试。', 409)
            if hashlib.sha256(self.read_pdf(path)).digest() != hashlib.sha256(raw).digest():
                raise UserError('文件在索引期间发生变化，请重试。', 409)
            if progress:
                progress(phase='committing')
            return self.store(path,raw,texts,title,category,managed,doc_id)

    def upload(self, filename, data, title='', category='补充资料', replace_id=None, progress=None, cancel=None):
        if not str(filename).lower().endswith('.pdf') or len(data) > MAX_FILE:
            raise UserError('请选择不超过 50 MB 的 PDF 文件。')
        previous = self.get(replace_id) if replace_id else None
        if previous:
            if not previous['managed']:
                raise UserError('此资料跟踪原文件，请在原路径修改后更新索引。')
            title, category = previous['title'], previous['category']
        if category not in CATEGORIES:
            raise UserError('请选择有效的资料类型。')
        texts = self.extract(data, progress, cancel)
        path = Path(previous['path']) if previous else self.directory / 'files' / (uuid.uuid4().hex + '.pdf')
        with self.lock:
            check_cancel(cancel)
            if previous and self.get(replace_id)['sha'] != previous['sha']:
                raise UserError('资料已被另一个操作更新，请重试。', 409)
            if progress:
                progress(phase='committing')
            old = path.read_bytes() if path.exists() else None
            temporary = path.with_suffix('.tmp')
            try:
                temporary.write_bytes(data)
                os.replace(temporary,path)
                return self.store(path,data,texts,title or Path(filename).stem,category,True,replace_id)
            except Exception:
                if old is None:
                    path.unlink(missing_ok=True)
                else:
                    temporary.write_bytes(old); os.replace(temporary,path)
                raise
            finally:
                temporary.unlink(missing_ok=True)

    def status(self, doc):
        try:
            path = Path(doc['path'])
            if not path.is_file():
                return 'missing'
            if path.stat().st_size > MAX_FILE:
                return 'changed'
            return 'ready' if hashlib.sha256(path.read_bytes()).hexdigest() == doc['sha'] else 'changed'
        except OSError:
            return 'unreadable'

    def documents(self, category='', doc_id=''):
        with self.connect() as db:
            rows = db.execute('''SELECT * FROM documents WHERE (?='' OR category=?)
                AND (?='' OR id=?) ORDER BY indexed_at DESC,title''', (category,category,doc_id,doc_id)).fetchall()
        with self.lock:
            docs = [self.public(row) for row in rows]
            for doc in docs:
                doc['status'] = self.status(doc)
        return docs

    def current(self, doc_id, expected_sha=None):
        doc = self.get(doc_id)
        if self.status(doc) != 'ready':
            raise UserError('原文件已变化或不可读取。请先检查文件并更新索引，再查看或导出引用。', 409)
        if expected_sha and expected_sha != doc['sha']:
            raise UserError('页面属于旧版本，请重新检索后再导出引用。', 409)
        return doc

    def reindex(self, doc_id, progress=None, cancel=None):
        doc = self.get(doc_id)
        return self.index(doc['path'],doc['title'],doc['category'],doc['managed'],doc_id,progress,cancel)

    def relocate(self, doc_id, path, allow_changed=False, progress=None, cancel=None):
        doc = self.get(doc_id)
        if doc['managed']:
            raise UserError('上传副本请使用替换副本；重新定位用于外部原文件。')
        target = Path(path).expanduser().resolve()
        digest = hashlib.sha256(self.read_pdf(target)).hexdigest()
        if digest != doc['sha'] and not allow_changed:
            raise UserError('新位置文件的内容不同。确认后将更新版本并重新建立索引。', 409)
        return self.index(target,doc['title'],doc['category'],False,doc_id,progress,cancel)

    def search(self, query, category='', doc_id=''):
        query = query.strip()
        if not query or len(query) > 200:
            raise UserError('请输入 1–200 个字符的检索词。')
        docs = self.documents(category,doc_id)
        needle, results, total = normalized(query), [], 0
        with self.connect() as db:
            for doc in docs:
                if doc['status'] != 'ready':
                    continue
                for page in db.execute('SELECT number,text,searchable FROM pages WHERE doc_id=? ORDER BY number',(doc['id'],)):
                    if needle in page['searchable']:
                        total += 1
                        if len(results) < 200:
                            hit = matches(page['text'],query)[0]
                            excerpt = re.sub(r'\s+',' ',hit['text']).strip()
                            results.append({'doc_id':doc['id'],'title':doc['title'],'category':doc['category'],
                                'page':page['number'],'sha':doc['sha'],'excerpt':excerpt,'selection':hit})
        return {'results':results,'total':total,'excluded':sum(d['status']!='ready' for d in docs)}

    def page(self, doc_id, number, expected_sha=None, query=''):
        with self.lock:
            doc = self.current(doc_id,expected_sha)
            with self.connect() as db:
                row = db.execute('SELECT text FROM pages WHERE doc_id=? AND number=?',(doc_id,number)).fetchone()
            if not row:
                raise UserError('页码超出范围。',404)
            return {'document':doc,'page':number,'text':row['text'],'query':query,
                    'matches':matches(row['text'],query) if query else []}

    def image(self, doc_id, number, expected_sha=None):
        with self.lock:
            doc = self.current(doc_id,expected_sha)
            if not 1 <= number <= doc['page_count']:
                raise UserError('页码超出范围。',404)
            raw = self.read_pdf(Path(doc['path']))
            if hashlib.sha256(raw).hexdigest() != doc['sha']:
                raise UserError('文件读取期间发生变化，请更新索引。',409)
        return self.run_pdf(raw,'image',number)

    def citation(self, doc_id, number, expected_sha=None, start=None, end=None):
        result = self.page(doc_id,number,expected_sha)
        doc, text = result['document'], result['text']
        if start is None and end is None:
            excerpt, truncated = '', False
        else:
            if not expected_sha or type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text) or end-start > 10000:
                raise UserError('引用范围无效：请选择当前版本中 1–10000 个字符的片段。')
            excerpt, truncated = text[start:end], start > 0 or end < len(text)
        citation = {'title':doc['title'],'category':doc['category'],'pdf_page':number,'printed_page':None,
            'sha256':doc['sha'],'indexed_at':doc['indexed_at'],'exported_at':now(),'text_excerpt':excerpt,
            'selection_start':start,'selection_end':end,'partial_page':truncated,
            'note':'PDF 页码从 1 开始；印刷页码未核对。文本为自动提取，公式与图表请核对原页。'}
        citation['markdown'] = (f"《{doc['title']}》｜{doc['category']}｜PDF 第 {number} 页\n\n"
            f"SHA-256: `{doc['sha']}`\n\n索引时间：{doc['indexed_at']}\n\n印刷页码未核对。\n\n")
        if excerpt:
            citation['markdown'] += ('以下为选定的自动提取片段（页面局部），请核对原页。\n\n' if truncated else '以下为整页自动提取文本，请核对原页。\n\n')
            citation['markdown'] += '\n'.join('> '+line for line in excerpt.splitlines())
        else:
            citation['markdown'] += '仅记录页级出处，未选取正文片段。'
        return citation

    def remove(self, doc_id):
        with self.lock:
            self.get(doc_id)
            with self.connect() as db:
                db.execute('DELETE FROM documents WHERE id=?',(doc_id,))
        return {'ok':True}
