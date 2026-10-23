"""Open only a verified instance of this project, otherwise start one."""
import json
from pathlib import Path
import subprocess
import sys
from urllib.request import build_opener, ProxyHandler
import webbrowser

ROOT = Path(__file__).resolve().parent


def find_instance(root=ROOT):
    ports = [8765]
    try:
        record = json.loads((root/'data'/'server.json').read_text('utf-8'))
        if isinstance(record,dict) and type(record.get('port')) is int and 0 < record['port'] < 65536:
            ports.insert(0,record['port'])
    except (OSError,ValueError):
        pass
    opener = build_opener(ProxyHandler({}))
    for port in dict.fromkeys(ports):
        url = f'http://127.0.0.1:{port}'
        try:
            with opener.open(url+'/api/health',timeout=.7) as response:
                info = json.loads(response.read(8192))
            if (isinstance(info,dict) and info.get('app')=='CourseTrace'
                    and isinstance(info.get('root'),str) and Path(info['root']).resolve()==root.resolve()):
                return url
        except (OSError,ValueError,TypeError):
            pass
    return None


def main():
    url = find_instance()
    if url:
        webbrowser.open(url)
        return 0
    return subprocess.call([sys.executable,str(ROOT/'app.py')],cwd=ROOT)


if __name__ == '__main__':
    raise SystemExit(main())
