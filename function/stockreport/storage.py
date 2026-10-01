import json
import os
import sqlite3
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime
from .rules import validate

def data_root():
    override=os.environ.get('STOCKREPORT_HOME')
    if override: return Path(override).expanduser().resolve()
    project=Path(__file__).resolve().parents[2]
    if (project/'function'/'stockreport').is_dir(): return project/'data'
    # In a packaged app, prefer the surrounding project when running from its
    # versioned dist folder. A copied standalone build stores data beside itself.
    executable=Path(sys.executable).resolve().parent
    for parent in (executable,*executable.parents):
        if (parent/'function'/'stockreport').is_dir() and (parent/'launch.py').is_file():
            return parent/'data'
    return executable/'data'

class Store:
    def __init__(self,root=None):
        self.root=Path(root) if root else data_root(); self.root.mkdir(parents=True,exist_ok=True)
        self.reports=self.root/'reports'; self.reports.mkdir(exist_ok=True)
        self.db=self.root/'stockreport.sqlite3'
        fresh=not self.db.exists()
        starter=None
        if fresh:
            from .bootstrap import load_starter,install_reports
            starter=load_starter()
            install_reports(self.reports,starter[1])
        with self.connect() as c:
            c.executescript('CREATE TABLE IF NOT EXISTS versions(id INTEGER PRIMARY KEY, created TEXT NOT NULL, body TEXT NOT NULL); CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);')
            c.execute('BEGIN IMMEDIATE')
            if not c.execute('SELECT 1 FROM versions LIMIT 1').fetchone():
                if starter is None:
                    from .bootstrap import load_starter
                    starter=load_starter()
                version=c.execute('INSERT INTO versions(created,body) VALUES(?,?)',(datetime.now().isoformat(),json.dumps(starter[0],ensure_ascii=False))).lastrowid
                c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('default',str(version)))
                if fresh:
                    c.execute("INSERT OR REPLACE INTO settings VALUES('starter_bundle','1.4.0')")
            if not c.execute("SELECT 1 FROM settings WHERE key='single_starter_strategy' LIMIT 1").fetchone():
                if starter is None:
                    from .bootstrap import load_starter
                    starter=load_starter()
                self._retain_starter_strategy(c,starter[0])
                c.execute("INSERT INTO settings VALUES('single_starter_strategy','1')")

    @staticmethod
    def _retain_starter_strategy(c,strategy):
        """Keep one named Jayson strategy and its edits; archive other rows."""
        rows=c.execute('SELECT id,created,body FROM versions ORDER BY id DESC').fetchall()
        default=c.execute("SELECT value FROM settings WHERE key='default'").fetchone()
        named=[]
        for row in rows:
            saved_strategy=json.loads(row[2])
            if isinstance(saved_strategy,dict) and saved_strategy.get('name')==strategy['name']:
                named.append(row)
        if named:
            chosen=next((row for row in named if default and str(row[0])==default[0]),named[0])
            retained=chosen[0]
        else:
            saved=c.execute("SELECT value FROM settings WHERE key='version_high_water'").fetchone()
            highest=max((row[0] for row in rows),default=0)
            retained=max(highest,int(saved[0]) if saved else 0)+1
            c.execute('INSERT INTO versions(id,created,body) VALUES(?,?,?)',(retained,datetime.now().isoformat(),json.dumps(strategy,ensure_ascii=False)))
        if len(rows)>1 or (rows and rows[0][0]!=retained):
            c.execute('CREATE TABLE IF NOT EXISTS deleted_versions(id INTEGER PRIMARY KEY, created TEXT NOT NULL, body TEXT NOT NULL, deleted TEXT NOT NULL)')
            deleted=datetime.now().isoformat()
            for version,created,body in rows:
                if version!=retained:
                    c.execute('INSERT INTO deleted_versions VALUES(?,?,?,?)',(version,created,body,deleted))
                    c.execute('DELETE FROM versions WHERE id=?',(version,))
        highest=max([retained,*(row[0] for row in rows)])
        saved=c.execute("SELECT value FROM settings WHERE key='version_high_water'").fetchone()
        c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('version_high_water',str(max(highest,int(saved[0]) if saved else 0))))
        c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('default',str(retained)))
    @contextmanager
    def connect(self):
        """Open a short-lived transaction and always release the database file.

        sqlite3.Connection's own context manager commits or rolls back, but does
        not close the connection.  That goes unnoticed on Unix and leaves the
        database locked on Windows, which also prevents temporary data folders
        from being removed.
        """
        connection=sqlite3.connect(self.db,timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()
    def versions(self):
        with self.connect() as c: return [(i,t,json.loads(b)) for i,t,b in c.execute('SELECT id,created,body FROM versions ORDER BY id DESC')]
    def get(self,version=None):
        version=version or int(self.setting('default','1'))
        with self.connect() as c: row=c.execute('SELECT body FROM versions WHERE id=?',(version,)).fetchone()
        if not row: raise ValueError('策略版本不存在')
        return version,validate(json.loads(row[0]))
    def save(self,s,default=True):
        s=validate(s)
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            highest=c.execute('SELECT COALESCE(MAX(id),0) FROM versions').fetchone()[0]
            saved=c.execute("SELECT value FROM settings WHERE key='version_high_water'").fetchone()
            version=max(highest,int(saved[0]) if saved else 0)+1
            c.execute('INSERT INTO versions(id,created,body) VALUES(?,?,?)',(version,datetime.now().isoformat(),json.dumps(s,ensure_ascii=False)))
            c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('version_high_water',str(version)))
            if default: c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('default',str(version)))
        return version
    def rename_version(self,version,name):
        """Change only the name of a saved strategy, preserving its ID and rules."""
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT body FROM versions WHERE id=?',(version,)).fetchone()
            if row is None: raise ValueError('该策略版本已不存在，请重新载入')
            strategy=json.loads(row[0]); strategy['name']=name
            strategy=validate(strategy)
            c.execute('UPDATE versions SET body=? WHERE id=?',(json.dumps(strategy,ensure_ascii=False),version))
        return version,strategy
    def delete_version(self,version):
        """Remove exactly one loaded ID; keep recovery evidence and reports intact."""
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT created,body FROM versions WHERE id=?',(version,)).fetchone()
            if row is None: raise ValueError('该策略版本已不存在，请重新载入')
            remaining=c.execute('SELECT id,body FROM versions WHERE id<>? ORDER BY id DESC',(version,)).fetchall()
            if not remaining: raise ValueError('至少保留一条策略，请先新建或载入其他策略后再删除')
            default=c.execute("SELECT value FROM settings WHERE key='default'").fetchone()
            target=next((r for r in remaining if default and str(r[0])==default[0]),remaining[0])
            body=validate(json.loads(target[1]))
            highest=c.execute('SELECT MAX(id) FROM versions').fetchone()[0]
            saved=c.execute("SELECT value FROM settings WHERE key='version_high_water'").fetchone()
            c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('version_high_water',str(max(highest,int(saved[0]) if saved else 0))))
            c.execute('CREATE TABLE IF NOT EXISTS deleted_versions(id INTEGER PRIMARY KEY, created TEXT NOT NULL, body TEXT NOT NULL, deleted TEXT NOT NULL)')
            c.execute('INSERT INTO deleted_versions VALUES(?,?,?,?)',(version,row[0],row[1],datetime.now().isoformat()))
            c.execute('DELETE FROM versions WHERE id=?',(version,))
            c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('default',str(target[0])))
        return target[0],body
    def setting(self,key,default=''):
        with self.connect() as c: row=c.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        return row[0] if row else default
    def set_setting(self,key,value):
        with self.connect() as c: c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,str(value)))
    def set_default(self,version):
        self.get(version); self.set_setting('default',version)
    # The key lives in the user's own local data directory. It is reversible
    # storage, not encryption: the UI must say so and must offer removal.
    def get_api_key(self): return self.setting('deepseek_api_key','')
    def set_api_key(self,value): self.set_setting('deepseek_api_key',(value or '').strip())
    def clear_api_key(self):
        with self.connect() as c: c.execute('DELETE FROM settings WHERE key=?',('deepseek_api_key',))

def md_cell(v):
    return str(v).replace('|','\\|').replace('\r',' ').replace('\n',' ')

def write_report(store,run):
    from .rules import describe
    from .exports import write_report_csv,STATUS_LABELS
    day=datetime.now().strftime('%Y-%m-%d')
    # O_EXCL protects naming across processes and after restarts.
    for i in range(1,1000000):
        path=store.reports/f'StockReport_{day}_{i:03d}.md'
        if path.with_suffix('.json').exists() or path.with_suffix('.csv').exists(): continue
        try:
            fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600); break
        except FileExistsError: continue
    else: raise RuntimeError('当日报告编号已耗尽')
    counts=run['counts']; s=run['strategy']
    lines=['# Stock Report 选股报告','',f"生成时间：{run['generated']}  ",f"模式：{'**演示数据，非真实行情**' if run['demo'] else '实时数据源'}  ",f"状态：{run['status']}  ",f"数据源：{run['source']}  ",f"行情截止：{run.get('market_date') or '未取得'}  ",f"策略：{s['name']} · v{run['version']}  ",f"股票范围：{s['pool']}；排除 ST：{s['exclude_st']}",'','## 筛选条件','',describe(s['rule']),'','## 覆盖情况','', '| 指标 | 数量 |','| --- | ---: |']
    labels=STATUS_LABELS
    for k,label in labels.items(): lines.append(f'| {label} | {counts[k]} |')
    if run.get('error'): lines+=['',f"运行错误：{run['error']}"]
    lines+=['','## 入选股票','','| 代码 | 名称 | 数据日期 | 收盘价 | 财报期 | 净利润（元） |','| --- | --- | --- | ---: | --- | ---: |']
    for r in run['results']:
        if r['status']=='matched':
            f=r.get('financial',{}); vals=[r['code'],r['name'],r.get('date',''),r.get('close',''),f.get('report_date',''),f.get('net_profit','未获取')]
            lines.append('| '+' | '.join(md_cell(v) for v in vals)+' |')
    if not counts['matched']: lines+=['','本次没有已确认符合全部逻辑条件的股票。请结合覆盖情况判断，不能把失败或数据不足当作未命中。']
    lines+=['','## 入选条件明细','']
    for r in run['results']:
        if r['status']!='matched': continue
        lines += [f"### {md_cell(r['code'])} {md_cell(r['name'])}",'']
        for d in r.get('details',[]):
            lines.append(f"- {d['condition']}：实际 {d['value']}；{'通过' if d['pass'] is True else '不通过' if d['pass'] is False else '数据不足'}")
    lines+=['','## 异常记录','']
    for r in run['results']:
        if r['status'] in ('failed','missing'): lines.append(f"- {r['code']} {md_cell(r['name'])}：{r.get('reason','数据不足')}")
    lines+=['','## 口径说明','','- 日线按北京时间16:00作为当日日线可纳入门槛，并以指数最后有效日检查个股是否过期。','- 周指标只使用已结束自然周；长假无交易周不算振幅达标。','- 新浪与东方财富复权口径不同，不自动混用或故障切换。','- 财务指标为最新已披露报告累计净利润，报告期可能因公司不同而不同。','- 规则不使用四舍五入后的数字比较。结果是条件匹配清单，不是收益预测。','','## 完整策略快照','','```json',json.dumps(s,ensure_ascii=False,indent=2),'```','']
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as out:
            out.write('\n'.join(lines)); out.flush(); os.fsync(out.fileno())
        # Sidecar preserves every checked row, calculation evidence and raw inputs.
        side=path.with_suffix('.json'); temp=side.with_suffix('.json.tmp')
        temp.write_text(json.dumps(run,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8'); os.replace(temp,side)
        # The CSV is a peer deliverable, not a replacement: it carries every
        # checked stock so the user can filter and sort in a spreadsheet.
        write_report_csv(path.with_suffix('.csv'),run)
    except Exception:
        # A partially written report must not appear as a completed deliverable.
        for suffix in ('.md','.csv','.json','.json.tmp'):
            path.with_suffix(suffix).unlink(missing_ok=True)
        raise
    return path
