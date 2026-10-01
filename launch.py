"""Double-click entry point; also supports headless demo/diagnostics/backtest."""
import argparse
import json
import sys
from pathlib import Path

# The application package lives under function/ in the source tree.  PyInstaller
# bundles it as stockreport/; that bundled location is already on sys.path.
source_packages = Path(__file__).resolve().parent / 'function'
if source_packages.is_dir():
    sys.path.insert(0, str(source_packages))

from stockreport.storage import Store,write_report
from stockreport.providers import LiveProvider,DemoProvider,SOURCES
from stockreport.engine import screen

def main():
    p=argparse.ArgumentParser(description='Stock Report Desktop')
    p.add_argument('--demo',action='store_true',help='生成离线人工演示报告，不启动窗口')
    p.add_argument('--probe',choices=SOURCES,help='只检测真实接口，输出JSON')
    p.add_argument('--backtest',help='对指定历史报告（.md 或 .json）做无界面回测，输出CSV路径')
    p.add_argument('--source',choices=SOURCES,help='回测使用的行情来源；默认沿用原报告记录的来源')
    p.add_argument('--data-dir',help='本机数据目录')
    args=p.parse_args()
    if args.probe:
        result=LiveProvider(args.probe).probe()
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0 if all(item['ok'] for item in result.values()) else 1
    store=Store(args.data_dir)
    if args.backtest:
        from stockreport import backtest as bt
        report=bt.load_report(args.backtest)
        provider=LiveProvider(args.source) if args.source else None
        run=bt.backtest(report,provider)
        path=bt.write_backtest(store,run)
        print(path)
        print(json.dumps({**run['counts'],'status':run['status'],'baseline_date':run['baseline_date'],
                          'source':run['source'],'error':run.get('error')},ensure_ascii=False))
        return 0 if run['status'].startswith('完成') else 1
    if args.demo:
        version,strategy=store.get(); run=screen(strategy,version,DemoProvider()); print(write_report(store,run)); print(json.dumps(run['counts'],ensure_ascii=False)); return
    from stockreport.ui import App
    app=App(store); app.mainloop()

if __name__=='__main__': raise SystemExit(main())
