"""CourseTrace local server. SPDX-License-Identifier: AGPL-3.0-or-later."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import base64
import json
import os
import socket
from pathlib import Path
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from library import Library, UserError, Cancelled, ROOT, VERSION, MAX_FILE


class Jobs:
    def __init__(self, library):
        self.library = library
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.lock = threading.RLock()
        self.jobs = {}

    def operation(self, action, args, progress=None, cancel=None):
        lib = self.library
        if action == 'import':
            if args.get('path'):
                return lib.index(args['path'],args.get('title',''),args.get('category','补充资料'),progress=progress,cancel=cancel)
            try:
                data = base64.b64decode(args.get('data',''),validate=True)
            except Exception as error:
                raise UserError('上传数据格式无效。') from error
            return lib.upload(args.get('filename',''),data,args.get('title',''),args.get('category','补充资料'),args.get('replace_id'),progress,cancel)
        if action == 'reindex':
            return lib.reindex(args.get('id'),progress,cancel)
        if action == 'relocate':
            return lib.relocate(args.get('id'),args.get('path',''),args.get('allow_changed') is True,progress,cancel)
        if action == 'demo':
            return lib.index(ROOT/'examples'/'CourseTrace-demo.pdf','从概念回到原页 · 示例资料','补充资料',progress=progress,cancel=cancel)
        raise UserError('未知任务类型。')

    def submit(self, action, args):
        if action not in ('import','reindex','relocate','demo') or not isinstance(args,dict):
            raise UserError('未知任务类型或参数无效。')
        with self.lock:
            active = sum(j['state'] in ('queued','running','cancelling') for j in self.jobs.values())
            if active >= 8:
                raise UserError('待处理任务过多，请稍后重试。',429)
            for key in list(self.jobs):
                if len(self.jobs) <= 40:
                    break
                if self.jobs[key]['state'] in ('completed','cancelled','failed'):
                    del self.jobs[key]
            job_id = uuid.uuid4().hex
            self.jobs[job_id] = {'id':job_id,'state':'queued','phase':'extracting','page':0,'total':0,'cancel':threading.Event()}
            self.pool.submit(self.run,job_id,action,args)
            return self.get(job_id)

    def run(self, job_id, action, args):
        def progress(**values):
            with self.lock:
                job = self.jobs[job_id]
                if values.get('phase') == 'committing' and job['cancel'].is_set():
                    raise Cancelled()
                job.update(values)
        try:
            with self.lock:
                job = self.jobs[job_id]
                if job['cancel'].is_set():
                    raise Cancelled()
                job['state'] = 'running'
            result = self.operation(action,args,progress,job['cancel'])
            with self.lock:
                job.update(state='completed',result=result)
        except Cancelled as error:
            with self.lock:
                self.jobs[job_id].update(state='cancelled',error=str(error))
        except Exception as error:
            with self.lock:
                self.jobs[job_id].update(state='failed',error=str(error) if isinstance(error,UserError) else '任务失败，请检查文件和存储空间。',status=getattr(error,'status',500))

    def get(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise UserError('任务不存在或已过期。',404)
            return {k:v for k,v in self.jobs[job_id].items() if k != 'cancel'}

    def cancel(self, job_id):
        with self.lock:
            result = self.get(job_id)
            job = self.jobs[job_id]
            if result['state'] in ('completed','failed','cancelled'):
                return result
            if job['phase'] == 'committing':
                raise UserError('资料正在提交，请等待操作完成。',409)
            job['cancel'].set()
            job['state'] = 'cancelling'
            return self.get(job_id)

    def close(self):
        with self.lock:
            for job in self.jobs.values():
                if job['phase'] != 'committing':
                    job['cancel'].set()
        self.pool.shutdown(wait=True)


def make_handler(library):
    operations = Jobs(library)
    class Handler(BaseHTTPRequestHandler):
        body_timeout = 10
        body_deadline = 30

        def setup(self):
            super().setup()
            self.connection.settimeout(self.body_timeout)

        def respond(self, value, status=200, mime='application/json; charset=utf-8'):
            raw = json.dumps(value,ensure_ascii=False).encode() if mime.startswith('application/json') else value
            try:
                self.send_response(status)
                self.send_header('Content-Type',mime)
                self.send_header('Content-Length',str(len(raw)))
                self.send_header('Cache-Control','no-store')
                self.send_header('X-Content-Type-Options','nosniff')
                self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
                self.end_headers()
                self.wfile.write(raw)
            except (ConnectionError,TimeoutError,OSError):
                self.close_connection = True

        def body(self):
            if self.headers.get('Transfer-Encoding'):
                raise UserError('请使用固定长度上传。',400)
            length = int(self.headers.get('Content-Length','0'))
            if length <= 0 or length > int(MAX_FILE*1.4):
                raise UserError('请求大小无效。',413)
            deadline = time.monotonic()+self.body_deadline
            chunks, remaining = [], length
            while remaining:
                left = deadline-time.monotonic()
                if left <= 0:
                    raise UserError('上传超时，请重试。',408)
                self.connection.settimeout(min(self.body_timeout,left))
                try:
                    chunk = self.rfile.read1(min(65536,remaining))
                except TimeoutError as error:
                    raise UserError('上传超时，请重试。',408) from error
                if not chunk:
                    raise UserError('上传未完成，请重试。')
                chunks.append(chunk); remaining -= len(chunk)
            self.connection.settimeout(self.body_timeout)
            args = json.loads(b''.join(chunks).decode('utf-8'))
            if not isinstance(args,dict):
                raise UserError('请求格式无效。')
            return args

        def dispatch(self, post=False):
            host = f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') != host or self.headers.get('Origin') not in (None,f'http://{host}'):
                raise UserError('请使用本机启动器提供的地址；不允许跨站请求。',403)
            parsed = urlparse(self.path)
            endpoint = parsed.path
            args = {k:v[0] for k,v in parse_qs(parsed.query).items()}
            if post:
                if self.headers.get('X-CourseTrace') != 'local':
                    raise UserError('请求缺少本地应用标识。',403)
                args = self.body()
            if not post and endpoint == '/api/health':
                return {'app':'CourseTrace','version':VERSION,'root':str(ROOT),'pid':os.getpid()}
            if not post and endpoint == '/api/documents':
                return library.documents()
            if not post and endpoint == '/api/search':
                return library.search(args.get('q',''),args.get('category',''),args.get('doc_id',''))
            if not post and endpoint in ('/api/page','/api/preview'):
                number = int(args.get('page',1))
                if endpoint == '/api/preview':
                    self.respond(library.image(args.get('id'),number,args.get('sha')),mime='image/png')
                    return None
                query = args.get('q','')
                if len(query)>200:
                    raise UserError('检索词过长。')
                return library.page(args.get('id'),number,args.get('sha'),query)
            if endpoint == '/api/jobs':
                return operations.submit(args.get('action'),args.get('args',{})) if post else operations.get(args.get('id'))
            if post and endpoint == '/api/jobs/cancel':
                return operations.cancel(args.get('id'))
            if post and endpoint in ('/api/import','/api/reindex','/api/relocate','/api/demo'):
                return operations.operation(endpoint.rsplit('/',1)[1],args)
            if post and endpoint == '/api/remove':
                return library.remove(args.get('id'))
            if post and endpoint == '/api/citation':
                return library.citation(args.get('id'),int(args.get('page',1)),args.get('sha'),args.get('start'),args.get('end'))
            if not post and endpoint in ('/','/app.js','/style.css'):
                filename,mime = {'/':('index.html','text/html'),'/app.js':('app.js','text/javascript'),'/style.css':('style.css','text/css')}[endpoint]
                self.respond((ROOT/'static'/filename).read_bytes(),mime=mime+'; charset=utf-8')
                return None
            raise UserError('未找到此页面。',404)

        def handle_request(self, post=False):
            try:
                result = self.dispatch(post)
                if result is not None:
                    self.respond(result)
            except UserError as error:
                self.close_connection = True
                self.respond({'error':str(error)},error.status)
            except (ValueError,TypeError,KeyError,AttributeError):
                self.close_connection = True
                self.respond({'error':'请求参数无效。'},400)
            except (ConnectionError,TimeoutError):
                self.close_connection = True
            except Exception:
                import traceback
                traceback.print_exc()
                self.respond({'error':'操作失败，请查看启动窗口日志。'},500)

        def do_GET(self):
            self.handle_request()

        def do_POST(self):
            self.handle_request(True)

        def log_message(self, fmt, *args):
            pass

    Handler.jobs = operations
    return Handler


class LocalHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False
    allow_reuse_port = False

    def server_bind(self):
        if os.name == 'nt':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def create_server(library, port):
    handler = make_handler(library)
    try:
        return LocalHTTPServer(('127.0.0.1',port),handler)
    except OSError:
        if port == 0:
            raise
        return LocalHTTPServer(('127.0.0.1',0),handler)


class InstanceLock:
    def __init__(self, directory):
        self.path = Path(directory)/'.server.lock'
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.stream = self.path.open('a+b')
        # Keep one byte for Windows locking; never write into an already-locked byte.
        if self.path.stat().st_size == 0:
            self.stream.write(b'0'); self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise UserError('此资料库已有 CourseTrace 正在运行，请重新使用启动器打开。')
        return self

    def __exit__(self, *_):
        self.stream.close()


def main():
    parser = argparse.ArgumentParser(description='CourseTrace local PDF library')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--data',type=Path,default=ROOT/'data')
    parser.add_argument('--no-browser',action='store_true')
    args = parser.parse_args()
    try:
        with InstanceLock(args.data):
            lib = Library(args.data)
            server = create_server(lib,args.port)
            url = f'http://127.0.0.1:{server.server_port}'
            runtime = args.data/'server.json'
            runtime.write_text(json.dumps({'port':server.server_port,'root':str(ROOT),'pid':os.getpid()}),encoding='utf-8')
            print(f'CourseTrace {VERSION} | {url}\nData: {args.data}\nClose this window or press Ctrl+C to stop.',flush=True)
            if not args.no_browser:
                webbrowser.open(url)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.RequestHandlerClass.jobs.close()
                server.server_close()
                runtime.unlink(missing_ok=True)
        return 0
    except UserError as error:
        print(str(error))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
