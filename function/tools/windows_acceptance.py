"""Scoped local deployment checks: public market endpoints, local reports/build only."""
import argparse
import json
import os
import ctypes
import hashlib
import shutil
import zipfile
from collections import Counter
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FUNCTION = ROOT / 'function'
sys.path.insert(0, str(FUNCTION))
from stockreport import __version__
OUT = ROOT / 'acceptance'
EXE = ROOT / 'dist' / f'v{__version__}' / 'StockReport' / 'StockReport.exe'


def save(name, value):
    OUT.mkdir(exist_ok=True)
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def diagnostic():
    stocks = {'pn': 1, 'pz': 10, 'po': 1, 'np': 1, 'fid': 'f12',
              'fields': 'f12,f14', 'fs': 'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23'}
    bars = {'fields1': 'f1,f2,f3,f4,f5,f6', 'fields2': 'f51,f52,f53,f54,f55,f56',
            'klt': 101, 'fqt': 1, 'end': '20500101', 'lmt': 80, 'secid': '1.600000'}
    token = 'fa5fd1943c7b386f172d6893dbfba10b'
    cases = [
        ('bars-cutoff', 'https://push2his.eastmoney.com/api/qt/stock/kline/get',
         {**bars, 'beg': '20260101', 'end': '20260911', 'ut': token}),
        ('bars-http', 'http://push2his.eastmoney.com/api/qt/stock/kline/get',
         {**bars, 'beg': '20260101', 'end': '20260911', 'ut': token}),
        ('bars-official-token', 'https://push2his.eastmoney.com/api/qt/stock/kline/get',
         {**bars, 'beg': '0', 'end': '20500000', 'ut': '7eea3edcaed734bea9cbfc24409ed989'}),
        ('bars-tencent-index', 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get',
         {'param': 'sh000001,day,,,30,qfq'}),
        ('bars-tencent-sz', 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get',
         {'param': 'sz000001,day,,,271,qfq'}),
        ('bars-tencent-gem', 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get',
         {'param': 'sz300750,day,,,100,qfq'}),
    ]

    def one(case):
        label, base, params = case
        started = time.monotonic()
        result = {'name': label, 'host': urllib.parse.urlsplit(base).hostname}
        req = urllib.request.Request(base + '?' + urllib.parse.urlencode(params), headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36',
            'Referer': 'https://quote.eastmoney.com/'})
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                result['http_status'] = response.status
                raw = response.read(2000000)
            data = json.loads(raw)
            result['response'] = data
        except urllib.error.HTTPError as error:
            result.update(error=type(error).__name__, http_status=error.code,
                          detail=error.read(500).decode('utf-8', errors='replace'))
        except Exception as error:
            result.update(error=type(error).__name__, detail=str(error))
        result['seconds'] = round(time.monotonic() - started, 2)
        preview = dict(result)
        if 'response' in preview:
            preview['response_preview'] = str(preview.pop('response'))[:1800]
        print(json.dumps(preview, ensure_ascii=False), flush=True)
        return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(one, cases))
    save('diagnostic-kline.json', {'time': datetime.now().astimezone().isoformat(),
                           'platform': platform.platform(),
                           'proxy_types_present': sorted(urllib.request.getproxies()), 'results': results})


def probe(source):
    from stockreport.providers import LiveProvider
    provider=LiveProvider(source)
    trace_list(provider)
    result = provider.probe()
    save('probe-' + source + '.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if all(item['ok'] for item in result.values()) else 1


def trace_list(provider):
    original=provider.http.json
    def request(url, params=None):
        data=original(url,params)
        if '/clist/' in url:
            body=data.get('data') or {}
            if params['pn']==1 or params['pn']%10==0:
                print(f'List page {params["pn"]}: {len(body.get("diff") or [])} rows / total {body.get("total")}',flush=True)
        return data
    provider.http.json=request


def scan(source):
    from stockreport.providers import LiveProvider
    from stockreport.rules import presets
    from stockreport.engine import screen
    from stockreport.storage import Store, write_report
    last = [0.0]
    def progress(done, total, detail):
        if time.monotonic() - last[0] > 15 or done == total:
            print(f'{done}/{total} {detail}', flush=True)
            last[0] = time.monotonic()
    provider=LiveProvider(source)
    trace_list(provider)
    result = screen(presets()[0], 1, provider, progress=progress)
    path = write_report(Store(OUT / ('scan-' + source)), result)
    summary = {k: result.get(k) for k in ('status', 'counts', 'error', 'market_date', 'source')}
    summary['report'] = str(path)
    save('scan-' + source + '.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if result['status'].startswith('完成') and not result['counts']['failed'] else 1


def main():
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['diagnose', 'batch-check', 'list-info', 'sample', 'stop-scan', 'probe', 'scan', 'summarize', 'build', 'smoke', 'exe-probe', 'gui-smoke', 'package', 'backtest'])
    parser.add_argument('--source', choices=['tencent', 'eastmoney', 'sina'], default='tencent')
    args = parser.parse_args()
    if args.mode == 'summarize':
        from stockreport.rules import presets,in_pool,evaluate
        from datetime import date
        summary=json.loads((OUT/('scan-'+args.source+'.json')).read_text(encoding='utf-8'))
        run=json.loads(Path(summary['report']).with_suffix('.json').read_text(encoding='utf-8'))
        counts=Counter(row['status'] for row in run['results'])
        reasons=Counter(row.get('reason','') for row in run['results'] if row['status'] in ('missing','failed'))
        checks={'original_default_preserved':run['strategy']==presets()[0],
                'unique_codes':len({row['code'] for row in run['results']})==len(run['results']),
                'strict_mainboard':all(in_pool(row['code'],'mainboard') for row in run['results']),
                'counts_consistent':all(counts.get(key,0)==run['counts'][key] for key in ('matched','rejected','missing','failed')),
                'matched_evidence_replays':all(evaluate(run['strategy'],row['bars'],row['financial'],date.fromisoformat(run['market_date']))[0] is True for row in run['results'] if row['status']=='matched')}
        result={**summary,'provider_metadata':run.get('provider_metadata'),
                'checked_rows':len(run['results']),'reasons':dict(reasons),'checks':checks}
        save('audit-'+args.source+'.json',result)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0 if all(checks.values()) else 1
    if args.mode == 'package':
        target=EXE.parent
        for source in (ROOT/'README.md',ROOT/'CHANGELOG.md'):
            shutil.copy2(source,target/source.name)
        manifest_path=target/'BUILD_MANIFEST.json'
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest.get('version')!=__version__:
            raise RuntimeError('拒绝把旧构建包装为新版本；请使用匹配版本的构建目录')
        for name in ('exe-gui.json','exe-probe-tencent.json','audit-tencent.json'):
            if (OUT/name).exists(): manifest[name]=json.loads((OUT/name).read_text(encoding='utf-8'))
        manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        source_manifest=ROOT/'MANIFEST.sha256'
        paths={path.relative_to(ROOT).as_posix() for path in [ROOT/'launch.py',
               *(FUNCTION/'stockreport').glob('*.py'),*(FUNCTION/'stockreport'/'starter').glob('*'),
               *(FUNCTION/'tests').glob('*.py'),*(FUNCTION/'tools').glob('*.py'),
               ROOT/'README.md',ROOT/'CHANGELOG.md'] if path.is_file()}
        source_manifest.write_text(''.join(hashlib.sha256((ROOT/path).read_bytes()).hexdigest()+'  '+path+'\n' for path in sorted(paths)),encoding='utf-8')
        archive=ROOT/'dist'/f'StockReport-v{__version__}-Windows-x64.zip'
        with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(target.rglob('*')):
                if path.is_file(): bundle.write(path,path.relative_to(target.parent).as_posix())
        with zipfile.ZipFile(archive) as bundle:
            assert bundle.testzip() is None
            assert 'StockReport/StockReport.exe' in bundle.namelist()
        result={'archive':str(archive),'archive_bytes':archive.stat().st_size,
                'zip_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
                'exe_sha256':hashlib.sha256((target/'StockReport.exe').read_bytes()).hexdigest()}
        save('release.json',result)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    if args.mode == 'batch-check':
        cases=[('qq-web-sh','https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get',
                {'param':'sh600000,day,,,75,qfq'}),
               ('qq-web-sz','https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get',
                {'param':'sz000001,day,,,271,qfq'})]
        output=[]
        for label,base,params in cases:
            request=urllib.request.Request(base+'?'+urllib.parse.urlencode(params),
                     headers={'User-Agent':'Mozilla/5.0','Referer':'https://gu.qq.com/'})
            try:
                with urllib.request.urlopen(request,timeout=15) as response: data=json.loads(response.read(2000000))
                result={'name':label,'response':data}
            except urllib.error.HTTPError as error:
                result={'name':label,'http_status':error.code,'body':error.read(1000).decode('utf-8',errors='replace')}
            except Exception as error: result={'name':label,'error':str(error)}
            output.append(result)
            print(json.dumps(result,ensure_ascii=False)[:2500],flush=True)
            time.sleep(2)
        save('batch-check.json',output)
        return 0
    if args.mode == 'stop-scan':
        script="Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | Where-Object { $_.CommandLine -match 'windows_acceptance.py scan --source tencent$' } | Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress"
        found=subprocess.check_output(['powershell.exe','-NoProfile','-Command',script],text=True)
        processes=json.loads(found) if found.strip() else []
        if isinstance(processes,dict): processes=[processes]
        allowed={str(Path(sys.executable).resolve()).casefold(),str(Path(sys.base_prefix,'python.exe').resolve()).casefold()}
        for item in processes:
            if str(Path(item['ExecutablePath']).resolve()).casefold() not in allowed:
                raise RuntimeError('Unexpected executable path; refusing to stop process')
            os.kill(item['ProcessId'],15)
            print('Stopped project scan process',item['ProcessId'])
        return 0
    if args.mode == 'sample':
        from stockreport.providers import LiveProvider,completed_cutoff
        try:
            rows=LiveProvider(args.source).bars('600000',60,completed_cutoff())
            result={'ok':True,'rows':len(rows),'last':rows[-1]}
        except Exception as error:
            result={'ok':False,'error':str(error),'cause':repr(error.__cause__)}
        save('sample-after-scan.json',result)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    if args.mode == 'list-info':
        from stockreport.providers import Http, LIST_ENDPOINT
        data=Http().json(LIST_ENDPOINT,{'pn':1,'pz':100,'po':1,'np':1,'fid':'f12','fields':'f12,f14',
                       'fs':'m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23'})
        print(json.dumps(data,ensure_ascii=False))
        return 0
    if args.mode == 'diagnose':
        diagnostic()
        return 0
    if args.mode == 'probe':
        return probe(args.source)
    if args.mode == 'scan':
        return scan(args.source)
    if args.mode == 'backtest':
        # Offline end-to-end check of the backtest chain. A real historical report
        # is written as a non-demo Markdown report (so the Markdown fallback parser
        # is exercised), then backtested with the deterministic demo provider.
        # This checks report reading, continuous-series comparison, CSV output,
        # the audit sidecar, and the demo-report refusal - without live quotes.
        from datetime import date, timedelta
        from stockreport.providers import DemoProvider
        from stockreport.storage import Store
        from stockreport import backtest as bt
        store = Store(OUT / 'backtest-demo')
        provider = DemoProvider()
        baseline = provider.reference_date(date.today()) - timedelta(days=12)
        header = ['# Stock Report 选股报告', '', '生成时间：2026-01-01T00:00:00+08:00  ',
                  '模式：实时数据源  ', '状态：完成  ', '数据源：验收夹具（非真实行情）  ',
                  f'行情截止：{baseline.isoformat()}  ', '策略：验收夹具 · v1  ',
                  '股票范围：mainboard；排除 ST：False', '', '## 入选股票', '',
                  '| 代码 | 名称 | 数据日期 | 收盘价 | 财报期 | 净利润（元） |', '| --- | --- | --- | ---: | --- | ---: |']
        entries = [('600000', '验收甲', 95.0), ('000001', '验收乙', 105.0), ('600009', '验收缺数据', 10.0)]
        table = [f'| {code} | {name} | {baseline.isoformat()} | {close} |  |  |' for code, name, close in entries]
        report_dir = store.reports
        report_dir.mkdir(parents=True, exist_ok=True)
        for index in range(1, 1000):
            source_report = report_dir / f'StockReport_{baseline.strftime("%Y-%m-%d")}_{index:03d}.md'
            if not source_report.exists():
                break
        source_report.write_text('\n'.join(header + table) + '\n', encoding='utf-8')
        report = bt.load_report(source_report)
        refusals = {}
        try:
            bt.backtest({'market_date': baseline.isoformat(), 'demo': True, '_path': 'demo.md',
                         'results': [{'code': '600000', 'name': 'x', 'status': 'matched', 'close': 1.0,
                                      'date': baseline.isoformat()}]}, provider)
            refusals['demo_report'] = 'NOT REFUSED'
        except bt.BacktestError as error:
            refusals['demo_report'] = str(error)
        try:
            bt.backtest({'market_date': baseline.isoformat(), 'demo': False, '_path': 'empty.md', 'results': []},
                        provider)
            refusals['no_matches'] = 'NOT REFUSED'
        except bt.BacktestError as error:
            refusals['no_matches'] = str(error)
        result = bt.backtest(report, provider)
        path = bt.write_backtest(store, result)
        rows = bt.rows(result)
        summary = {'exit_ok': False, 'source_report': str(source_report), 'backtest_csv': str(path),
                   'report_basis': report['_basis'], 'baseline_date': result['baseline_date'],
                   'cutoff_date': result.get('cutoff_date'), 'counts': result['counts'],
                   'status': result['status'], 'refusals': refusals,
                   'csv_columns': list(bt.BACKTEST_COLUMNS), 'rows': rows,
                   'csv_bytes': path.stat().st_size, 'audit_json': str(path.with_suffix('.json'))}
        counts = result['counts']

        def is_number(value):
            try:
                return value not in (None, '') and float(value) == float(value)
            except (TypeError, ValueError):
                return False

        price_columns = all(is_number(row[index]) for row in rows for index in (4, 5, 7, 8))
        checks = {
            'status_completed': result['status'].startswith('完成'),
            'every_stock_backtested': counts['ok'] == counts['total'],
            'counts_add_up_to_total': sum(counts[k] for k in ('ok','unavailable','cancelled')) == counts['total'],
            'markdown_fallback_used': report['_basis'] == bt.SOURCE_MD,
            'csv_has_utf8_bom': path.read_bytes().startswith(b'\xef\xbb\xbf'),
            'demo_and_empty_reports_refused': all(value != 'NOT REFUSED' for value in refusals.values()),
            'every_row_backtested': all(row[12].startswith('已回测') for row in rows),
            'prices_and_changes_are_numbers': price_columns,
            'audit_json_written': path.with_suffix('.json').is_file(),
        }
        summary['checks'] = checks
        summary['exit_ok'] = all(checks.values())
        save('backtest-demo.json', summary)
        preview = {k: v for k, v in summary.items() if k != 'rows'}
        preview['rows'] = rows
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return 0 if summary['exit_ok'] else 1
    if args.mode == 'build':
        return subprocess.run([sys.executable, str(FUNCTION / 'tools' / 'build_windows.py')], cwd=ROOT).returncode
    if args.mode == 'exe-probe':
        result=subprocess.run([str(EXE),'--probe',args.source],
                              cwd=ROOT,capture_output=True,timeout=360)
        def decode(value):
            try: return value.decode('utf-8')
            except UnicodeDecodeError: return value.decode('gb18030')
        summary={'exit_code':result.returncode,'source':args.source,
                 'stdout':decode(result.stdout),'stderr':decode(result.stderr)}
        if result.stdout:
            summary['checks']=json.loads(summary['stdout'])
        save('exe-probe-'+args.source+'.json',summary)
        print(json.dumps(summary,ensure_ascii=False,indent=2))
        return result.returncode
    if args.mode == 'gui-smoke':
        return gui_smoke()
    if args.mode == 'smoke':
        process = subprocess.run([str(EXE), '--demo',
                                  '--data-dir', str(OUT / 'EXE演示验收')], cwd=ROOT, timeout=60)
        print('EXE exit:', process.returncode)
        return process.returncode


def gui_smoke():
    # Inspect only windows owned by this newly started test process.
    from ctypes import wintypes
    executable=EXE
    startup=subprocess.STARTUPINFO()
    startup.dwFlags=subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow=0
    process=subprocess.Popen([str(executable),'--data-dir',str(OUT/'EXE窗口验收')],cwd=ROOT,
                             startupinfo=startup)
    user32=ctypes.WinDLL('user32',use_last_error=True)
    callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    windows=[]
    @callback_type
    def enum_window(hwnd, unused):
        pid=wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        if pid.value==process.pid:
            title=ctypes.create_unicode_buffer(512)
            cls=ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd,title,512)
            user32.GetClassNameW(hwnd,cls,256)
            if title.value.startswith('Stock Report'):
                windows.append({'handle':hwnd,'title':title.value,'class':cls.value})
        return True
    try:
        deadline=time.monotonic()+20
        while time.monotonic()<deadline and process.poll() is None and not windows:
            user32.EnumWindows(enum_window,0)
            time.sleep(.2)
        if not windows: raise RuntimeError('EXE 未创建预期的 Stock Report 窗口')
        user32.PostMessageW(windows[0]['handle'],0x0010,0,0)
        process.wait(timeout=15)
        summary={'pid':process.pid,'windows':windows,'exit_code':process.returncode,'closed_by':'WM_CLOSE on verified application window'}
        save('exe-gui.json',summary)
        print(json.dumps(summary,ensure_ascii=False,indent=2))
        return process.returncode
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    raise SystemExit(main())
