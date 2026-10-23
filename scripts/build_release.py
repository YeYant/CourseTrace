"""Create an allowlisted, reproducible source archive (no local data)."""
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from library import VERSION

ROOT_FILES=['app.py','library.py','pdf_worker.py','launcher.py','start.bat','启动 CourseTrace.bat',
            'requirements.txt','requirements-dev.txt','README.md','CHANGELOG.md','CONTRIBUTING.md',
            'LICENSE','THIRD_PARTY_NOTICES.md','.gitignore','.gitattributes','data/.gitkeep']
DIRECTORIES={'static':{'.html','.js','.css'},'tests':{'.py'},'scripts':{'.py'},
             '.github':{'.md','.yml'},'examples':{'.pdf','.py','.md'},'docs':{'.md','.png'}}


def release_files():
    paths=[ROOT/name for name in ROOT_FILES]
    for directory,extensions in DIRECTORIES.items():
        paths.extend(p for p in (ROOT/directory).rglob('*') if p.is_file() and p.suffix in extensions
                     and '__pycache__' not in p.parts)
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT) or not path.is_file():
            raise RuntimeError(f'Invalid release file: {path.name}')
    return sorted(set(paths),key=lambda p:p.relative_to(ROOT).as_posix())


def main():
    output=ROOT/'releases';output.mkdir(exist_ok=True)
    name=f'CourseTrace-{VERSION}-source'
    archive=output/(name+'.zip')
    manifest=[]
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as bundle:
        for path in release_files():
            relative=path.relative_to(ROOT).as_posix();raw=path.read_bytes()
            if path.suffix in {'.py','.js','.css','.html','.md','.yml','.txt'} or path.name in {'.gitignore','.gitattributes','LICENSE'}:
                raw=raw.replace(b'\r\n',b'\n')
            if path.suffix=='.bat':raw=raw.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')
            entry=zipfile.ZipInfo(name+'/'+relative,date_time=(2026,10,23,0,0,0))
            entry.compress_type=zipfile.ZIP_DEFLATED
            entry.external_attr=0o100644<<16
            bundle.writestr(entry,raw)
            manifest.append({'path':relative,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
        bundle.writestr(zipfile.ZipInfo(name+'/MANIFEST.json',date_time=(2026,10,23,0,0,0)),json.dumps(manifest,ensure_ascii=False,indent=2).encode())
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    (output/(name+'.sha256')).write_text(digest+'  '+archive.name+'\n',encoding='utf-8')
    print(json.dumps({'archive':archive.name,'files':len(manifest),'sha256':digest},ensure_ascii=False))


if __name__=='__main__':main()
