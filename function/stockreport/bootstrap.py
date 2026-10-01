"""Bundled first-use material. Never read the developer's personal database."""
import hashlib
import json
from pathlib import Path
from .rules import validate

ASSETS=Path(__file__).resolve().parent/'starter'
REPORT='StockReport_2026-09-24_001'

def load_starter():
    manifest=json.loads((ASSETS/'manifest.json').read_text(encoding='utf-8'))
    names=['strategy.json',*(REPORT+ext for ext in ('.md','.json','.csv'))]
    files={name:(ASSETS/name).read_bytes() for name in names}
    for name,raw in files.items():
        if hashlib.sha256(raw).hexdigest()!=manifest['sha256'][name]:
            raise ValueError('初始资料校验失败：'+name)
    strategy=validate(json.loads(files.pop('strategy.json')))
    report=json.loads(files[REPORT+'.json'])
    if report['strategy']!=strategy or report.get('demo') is not False:
        raise ValueError('初始报告与策略不一致')
    return strategy,files

def install_reports(directory,files):
    # Exclusive creation: existing reports, even same-named files, stay untouched.
    # Keep the trio together: do not attach our JSON to a user's existing MD.
    if any((directory/name).exists() for name in files): return
    for name,raw in files.items():
        try:
            with (directory/name).open('xb') as out: out.write(raw)
        except FileExistsError:
            pass
