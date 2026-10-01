"""Package an already-built static-review release; never launch the app or tests."""
import hashlib
import argparse
import json
import re
import shutil
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
FUNCTION=ROOT/'function'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--version',help='要打包的语义版本号，默认当前软件版本')
    args=parser.parse_args()
    if args.version is None:
        import sys
        sys.path.insert(0,str(FUNCTION))
        from stockreport import __version__
        version=__version__
    else:
        version=args.version
    if not re.fullmatch(r'\d+\.\d+\.\d+',version):
        parser.error('--version 必须是类似 1.4.0 的版本号')
    target=ROOT/'dist'/f'v{version}'/'StockReport'
    manifest=json.loads((target/'BUILD_MANIFEST.json').read_text(encoding='utf-8'))
    if manifest['version']!=version or manifest['tests']!='not run (explicit skip)':
        raise RuntimeError('This packaging step requires the matching static-review build')
    if not (target/'StockReport.exe').is_file(): raise RuntimeError('Missing executable')
    archive=ROOT/'dist'/f'StockReport-v{version}-Windows-x64.zip'
    if archive.exists(): raise FileExistsError(archive)
    for source in (ROOT/'README.md',ROOT/'CHANGELOG.md'):
        shutil.copy2(source,target/source.name)
    files=sorted([ROOT/'launch.py',* (FUNCTION/'stockreport').glob('*.py'),
                  *(FUNCTION/'tools').glob('*.py'),*(FUNCTION/'tests').glob('*.py'),
                  *(FUNCTION/'stockreport'/'starter').glob('*'),ROOT/'README.md',ROOT/'CHANGELOG.md'])
    hashes=''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.relative_to(ROOT).as_posix()+'\n' for p in files)
    (ROOT/'MANIFEST.sha256').write_text(hashes,encoding='utf-8')
    (target/'SOURCE_MANIFEST.sha256').write_text(hashes,encoding='utf-8')
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(target.rglob('*')):
            if path.is_file(): bundle.write(path,path.relative_to(target.parent).as_posix())
    evidence={'version':version,'archive':str(archive),'bytes':archive.stat().st_size,
              'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
              'exe_sha256':hashlib.sha256((target/'StockReport.exe').read_bytes()).hexdigest(),
              'validation':('static review and syntax parsing; targeted endpoint/list probes only; tests/GUI/full screening not run'
                            if version=='1.3.1' else 'static review and syntax parsing only; tests/GUI/network not run')}
    (ROOT/'dist'/f'release-v{version}.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(evidence,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
