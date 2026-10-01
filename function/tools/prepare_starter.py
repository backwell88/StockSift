"""Export only the explicitly approved strategy and report; never export settings."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'function'))
from stockreport.rules import validate

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,default=ROOT/'data',help='项目内待导出的数据目录，默认 data/')
    source=parser.parse_args().source.expanduser().resolve()
    source.relative_to(ROOT)
    target=ROOT/'function'/'stockreport'/'starter'
    with sqlite3.connect((source/'stockreport.sqlite3').as_uri()+'?mode=ro',uri=True) as conn:
        row=conn.execute('SELECT body FROM versions WHERE id=?',(7,)).fetchone()
    strategy=validate(json.loads(row[0]))
    if strategy['name']!='Jayson缩量回调策略': raise ValueError('Approved strategy changed')
    base='StockReport_2026-09-24_001'
    payloads={base+ext:(source/'reports'/(base+ext)).read_bytes() for ext in ('.json','.md','.csv')}
    report=json.loads(payloads[base+'.json'])
    if report['strategy']!=strategy or report.get('demo') is not False or report['counts']['matched']!=30:
        raise ValueError('Approved report changed')
    payloads['strategy.json']=(json.dumps(strategy,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    for name,raw in payloads.items():
        text=raw.decode('utf-8-sig')
        if re.search(r'sk-[A-Za-z0-9]{16,}|api[_ -]?key|authorization|(?<![A-Za-z])[A-Za-z]:[\\/]',text,re.I):
            raise ValueError('Potential private content in '+name)
    target.mkdir(exist_ok=True)
    for name,raw in payloads.items():
        path=target/name
        if path.exists(): raise FileExistsError(path)
        with path.open('xb') as out: out.write(raw)
    manifest={'report':base,'strategy_name':strategy['name'],
              'sha256':{name:hashlib.sha256(raw).hexdigest() for name,raw in payloads.items()}}
    with (target/'manifest.json').open('x',encoding='utf-8') as out:
        json.dump(manifest,out,ensure_ascii=False,indent=2)
    print(json.dumps({'exported_files':list(payloads),'matched':30,'settings_exported':False}))

if __name__=='__main__': main()
