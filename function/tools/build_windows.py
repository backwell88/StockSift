"""Windows-only build, with tests and a recorded dependency manifest."""
import json
import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from datetime import datetime

def main():
    root=Path(__file__).resolve().parents[2]
    parser=argparse.ArgumentParser()
    parser.add_argument('--skip-tests',action='store_true',help='仅构建，记录测试未运行')
    parser.add_argument('--output',default='dist',help='项目内构建成品根目录；版本子目录自动创建')
    args=parser.parse_args()
    output_root=(root/args.output).resolve()
    output_root.relative_to(root)
    if sys.platform!='win32':
        print('Windows build requires a Windows Python interpreter. No Windows binary was produced.',file=sys.stderr)
        return 2
    sys.path.insert(0,str(root/'function'))
    from stockreport import __version__
    output_dir=output_root/f'v{__version__}'
    local_temp=root/'build'/'temp'
    pyinstaller_config=root/'build'/'pyinstaller-config'
    local_temp.mkdir(parents=True,exist_ok=True)
    pyinstaller_config.mkdir(parents=True,exist_ok=True)
    os.environ.update({'TMP':str(local_temp),'TEMP':str(local_temp),'TMPDIR':str(local_temp),
                       'PYINSTALLER_CONFIG_DIR':str(pyinstaller_config),'PIP_NO_CACHE_DIR':'1'})
    env={**os.environ,'PYTHONUTF8':'1','PYTHONPATH':str(root/'function')}
    if not args.skip_tests:
        tests=subprocess.run([sys.executable,'-m','unittest','discover','-s','function/tests','-v'],cwd=root,
                             capture_output=True,text=True,encoding='utf-8',env=env)
        output=tests.stdout+tests.stderr
        (root/'test-output.txt').write_text(output,encoding='utf-8')
        print(output)
        tests.check_returncode()
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--windowed',
                    '--paths',str(root/'function'),'--add-data','function/stockreport/starter;stockreport/starter',
                    '--add-data','function/stockreport/assets;stockreport/assets',
                    '--icon',str(root/'function'/'stockreport'/'assets'/'stockreport.ico'),
                    '--distpath',str(output_dir),'--name','StockReport','launch.py'],cwd=root,check=True,env=env)
    freeze=subprocess.check_output([sys.executable,'-m','pip','freeze'],text=True,env=env)
    manifest={'version':__version__,'created':datetime.now().astimezone().isoformat(),'platform':platform.platform(),'python':sys.version,'packages':freeze.splitlines(),'entry':'StockReport.exe','tests':'not run (explicit skip)' if args.skip_tests else 'passed','runtime_acceptance':'not run','status':'portable source build'}
    (output_dir/'StockReport'/'BUILD_MANIFEST.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    for source in (root/'README.md',root/'CHANGELOG.md'):
        shutil.copy2(source,output_dir/'StockReport'/source.name)
    print(output_dir/'StockReport'/'StockReport.exe')
    return 0

if __name__=='__main__': raise SystemExit(main())
