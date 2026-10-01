import copy
import difflib
import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk,messagebox,filedialog,simpledialog
from tkinter.scrolledtext import ScrolledText
from .rules import CATALOG,POOLS,OPS,validate,describe,template,RuleError
from .storage import Store,write_report
from .providers import LiveProvider,DemoProvider,SOURCES,DEFAULT_SOURCE,Cancelled
from .engine import screen
from .ai import ask,DEFAULT_MODEL,MAX_ATTACHMENTS,build_report_context
from .exports import REPORT_COLUMNS,STATUS_LABELS,write_csv,number
from . import backtest as bt

COLORS = {
    'background': '#19212A',
    'panel': '#242F3A',
    'raised': '#33414D',
    'field': '#1D2832',
    'text': '#EAF0F4',
    'muted': '#B2C0C9',
    'border': '#526370',
    'accent': '#75C4D2',
    'accent_hover': '#98D6DF',
    'accent_text': '#142832',
    'selection': '#3A7184',
    'warning': '#D9BA80',
}

def external(path):
    if sys.platform=='win32': os.startfile(str(path))
    elif sys.platform=='darwin': subprocess.Popen(['open',str(path)])
    else: subprocess.Popen(['xdg-open',str(path)])

def numeric(value):
    """Sort key helper: numbers sort as numbers, everything else sorts last."""
    if isinstance(value,bool): return (0,float(value))
    if isinstance(value,(int,float)):
        v=float(value); return (0,v) if math.isfinite(v) else (2,0.0)
    text=str(value).strip().replace(',','')
    if text.endswith('%'): text=text[:-1]
    try:
        value=float(text)
        return (0,value) if math.isfinite(value) else (2,0.0)
    except ValueError: return (1 if text else 2,0.0)

class ResultsTable(ttk.Frame):
    """Sortable, filterable table over a list of result dicts.

    The same component backs the report view and the backtest view, so what the
    user sees sorted and filtered is exactly what "导出当前表格为 CSV" writes.
    """
    def __init__(self,parent,columns,statuses,on_export=None):
        super().__init__(parent)
        self.columns=columns; self.rows=[]; self.sort_key=columns[0][0]; self.sort_desc=False; self.on_export=on_export
        bar=ttk.Frame(self); bar.pack(fill='x',pady=(0,6))
        ttk.Label(bar,text='筛选').pack(side='left')
        self.query=tk.StringVar(); entry=ttk.Entry(bar,textvariable=self.query,width=18); entry.pack(side='left',padx=6)
        entry.bind('<KeyRelease>',lambda e:self.render())
        ttk.Label(bar,text='状态').pack(side='left',padx=(10,4))
        self.status=tk.StringVar(value='全部'); ttk.Combobox(bar,textvariable=self.status,values=['全部']+list(statuses),state='readonly',width=16).pack(side='left')
        self.status.trace_add('write',lambda *a:self.render())
        ttk.Button(bar,text='清除筛选',command=self.clear_filters).pack(side='left',padx=6)
        self.summary=tk.StringVar(value=''); ttk.Label(bar,textvariable=self.summary).pack(side='right')
        wrap=ttk.Frame(self); wrap.pack(fill='both',expand=True)
        keys=[c[0] for c in columns]
        self.tree=ttk.Treeview(wrap,columns=keys,show='headings',selectmode='browse')
        for key,title,width,kind in columns:
            self.tree.heading(key,text=title,command=lambda k=key:self.sort_by(k))
            self.tree.column(key,width=width,anchor='e' if kind=='num' else 'w',stretch=False)
        scroll=ttk.Scrollbar(wrap,orient='vertical',command=self.tree.yview); self.tree.configure(yscrollcommand=scroll.set)
        horizontal=ttk.Scrollbar(wrap,orient='horizontal',command=self.tree.xview)
        self.tree.configure(xscrollcommand=horizontal.set)
        self.tree.grid(row=0,column=0,sticky='nsew'); scroll.grid(row=0,column=1,sticky='ns'); horizontal.grid(row=1,column=0,sticky='ew')
        wrap.rowconfigure(0,weight=1); wrap.columnconfigure(0,weight=1)
        row=ttk.Frame(self); row.pack(fill='x',pady=6)
        ttk.Button(row,text='导出当前表格为 CSV',command=self.export_visible).pack(side='left')
        ttk.Label(row,text='点击列标题排序；导出的是当前筛选与排序结果。',style='Muted.TLabel').pack(side='left',padx=8)
    def set_rows(self,rows):
        self.rows=list(rows); self.render()
    def clear_filters(self):
        self.query.set(''); self.status.set('全部'); self.render()
    def sort_by(self,key):
        if self.sort_key==key: self.sort_desc=not self.sort_desc
        else: self.sort_key=key; self.sort_desc=False
        self.render()
    def visible(self):
        query=self.query.get().strip().lower(); status=self.status.get()
        out=self.rows
        if query: out=[r for r in out if query in str(r.get('code','')).lower() or query in str(r.get('name','')).lower()]
        if status!='全部': out=[r for r in out if str(r.get('state',''))==status]
        kind=next(c[3] for c in self.columns if c[0]==self.sort_key)
        if kind=='num':
            valid=[r for r in out if numeric(r.get(self.sort_key,''))[0]==0]
            missing=[r for r in out if numeric(r.get(self.sort_key,''))[0]!=0]
            return sorted(valid,key=lambda r:numeric(r.get(self.sort_key,'')),reverse=self.sort_desc)+missing
        return sorted(out,key=lambda r:str(r.get(self.sort_key) or '').casefold(),reverse=self.sort_desc)
    def render(self):
        self.tree.delete(*self.tree.get_children())
        for i,r in enumerate(self.visible()):
            self.tree.insert('','end',iid=str(i),values=[self.display(r.get(key,'')) for key,_,_,_ in self.columns])
        self.summary.set(f'显示 {len(self.tree.get_children())} / 共 {len(self.rows)} 行'
                         + ('（降序）' if self.sort_desc else '（升序）'))
    def display(self,value):
        if value is None: return ''
        if isinstance(value,float): return number(value)
        return value
    def export_visible(self):
        rows=[[r.get(key,'') for key,_,_,_ in self.columns] for r in self.visible()]
        if not rows: messagebox.showinfo('没有数据','当前筛选结果为空，未导出。'); return None
        default=f'StockReport_导出_{len(rows)}行.csv'
        path=filedialog.asksaveasfilename(defaultextension='.csv',initialfile=default,filetypes=[('CSV 表格','*.csv')])
        if not path: return None
        try:
            write_csv(path,[c[1] for c in self.columns],rows); self.summary.set(f'已导出 {len(rows)} 行：{os.path.basename(path)}'); return path
        except Exception as e: messagebox.showerror('导出失败',str(e)); return None

class App(tk.Tk):
    def configure_theme(self):
        c=COLORS
        self.configure(background=c['background'])
        self.option_add('*Font',('Microsoft YaHei UI',10))
        self.option_add('*TCombobox*Listbox.background',c['field'])
        self.option_add('*TCombobox*Listbox.foreground',c['text'])
        self.option_add('*TCombobox*Listbox.selectBackground',c['selection'])
        self.option_add('*TCombobox*Listbox.selectForeground',c['text'])
        style=ttk.Style(self)
        style.theme_use('clam')
        style.configure('TFrame',background=c['background'])
        style.configure('Header.TFrame',background=c['panel'])
        style.configure('Status.TFrame',background=c['panel'])
        style.configure('TLabel',background=c['background'],foreground=c['text'],font=('Microsoft YaHei UI',10))
        style.configure('Header.TLabel',background=c['panel'],foreground=c['text'])
        style.configure('Brand.TLabel',background=c['panel'],foreground=c['text'],font=('Microsoft YaHei UI',18,'bold'))
        style.configure('Muted.TLabel',foreground=c['muted'])
        style.configure('HeaderMuted.TLabel',background=c['panel'],foreground=c['muted'])
        style.configure('Status.TLabel',background=c['panel'],foreground=c['muted'])
        style.configure('Progress.TLabel',background=c['panel'],foreground=c['text'],font=('Microsoft YaHei UI',10,'bold'))
        style.configure('Warning.TLabel',foreground=c['warning'])
        style.configure('TLabelframe',background=c['background'],bordercolor=c['border'],relief='solid')
        style.configure('TLabelframe.Label',background=c['background'],foreground=c['muted'])
        style.configure('TButton',background=c['raised'],foreground=c['text'],bordercolor=c['border'],darkcolor=c['raised'],lightcolor=c['raised'],relief='flat',padding=(12,7),font=('Microsoft YaHei UI',10))
        style.map('TButton',background=[('disabled',c['panel']),('pressed',c['selection']),('active',c['border'])],foreground=[('disabled',c['muted']),('active',c['text'])])
        style.configure('Accent.TButton',background=c['accent'],foreground=c['accent_text'],bordercolor=c['accent'],darkcolor=c['accent'],lightcolor=c['accent'],font=('Microsoft YaHei UI',10,'bold'))
        style.map('Accent.TButton',background=[('disabled',c['raised']),('pressed',c['accent']),('active',c['accent_hover'])],foreground=[('disabled',c['muted']),('active',c['accent_text'])])
        style.configure('TEntry',fieldbackground=c['field'],foreground=c['text'],bordercolor=c['border'],insertcolor=c['text'],padding=5)
        style.map('TEntry',fieldbackground=[('disabled',c['panel']),('readonly',c['field'])],foreground=[('disabled',c['muted'])])
        style.configure('TCombobox',fieldbackground=c['field'],background=c['raised'],foreground=c['text'],bordercolor=c['border'],arrowcolor=c['text'],padding=5)
        style.map('TCombobox',fieldbackground=[('readonly',c['field']),('disabled',c['panel'])],background=[('active',c['border'])],foreground=[('readonly',c['text']),('disabled',c['muted'])])
        style.configure('TCheckbutton',background=c['background'],foreground=c['text'],font=('Microsoft YaHei UI',10))
        style.map('TCheckbutton',background=[('active',c['background'])],foreground=[('disabled',c['muted']),('active',c['text'])])
        style.configure('TNotebook',background=c['background'],bordercolor=c['border'])
        style.configure('TNotebook.Tab',background=c['panel'],foreground=c['muted'],bordercolor=c['border'],padding=(14,9),font=('Microsoft YaHei UI',10))
        style.map('TNotebook.Tab',background=[('selected',c['raised']),('active',c['raised'])],foreground=[('selected',c['text']),('active',c['text'])])
        style.configure('Treeview',background=c['field'],fieldbackground=c['field'],foreground=c['text'],bordercolor=c['border'],rowheight=26,font=('Microsoft YaHei UI',10))
        style.map('Treeview',background=[('selected',c['selection'])],foreground=[('selected',c['text'])])
        style.configure('Treeview.Heading',background=c['raised'],foreground=c['text'],bordercolor=c['border'],padding=(7,6),font=('Microsoft YaHei UI',10,'bold'))
        style.map('Treeview.Heading',background=[('active',c['border'])])
        style.configure('Horizontal.TProgressbar',background=c['accent'],troughcolor=c['field'],bordercolor=c['border'],thickness=9)
        style.configure('TPanedwindow',background=c['border'])
        style.configure('TScrollbar',background=c['raised'],troughcolor=c['field'],bordercolor=c['border'],arrowcolor=c['muted'])

    def style_text(self,widget):
        widget.configure(background=COLORS['field'],foreground=COLORS['text'],insertbackground=COLORS['text'],selectbackground=COLORS['selection'],selectforeground=COLORS['text'],relief='flat',borderwidth=1,highlightthickness=1,highlightbackground=COLORS['border'],highlightcolor=COLORS['accent'],padx=10,pady=8)

    def __init__(self,store=None):
        from . import __version__
        super().__init__(); self.title(f'Stock Report {__version__} · 固定策略选股'); self.geometry('1120x780'); self.minsize(900,640)
        assets=Path(__file__).resolve().parent/'assets'
        self._app_icon=tk.PhotoImage(file=str(assets/'stockreport.png'))
        self.iconphoto(True,self._app_icon)
        if sys.platform=='win32':
            try: self.iconbitmap(default=str(assets/'stockreport.ico'))
            except tk.TclError: pass  # The PNG remains the window icon.
        self.store=store or Store(); self.version,self.strategy=self.store.get()
        self.events=queue.Queue(); self.cancel=threading.Event(); self.running=False; self.chat_busy=False
        self.pending=None; self.history=[]; self.rule_paths={}; self.current_report=None; self.current_report_rows=[]
        self.backtest_rows=[]; self.backtest_run=None; self.report_checks={}; self.chat_files={}
        self.configure_theme()
        top=ttk.Frame(self,padding=(18,14),style='Header.TFrame'); top.pack(fill='x',padx=12,pady=(12,0))
        ttk.Label(top,text='Stock Report',style='Brand.TLabel').pack(side='left')
        ttk.Label(top,text='  固定策略 · 可追溯筛选',style='HeaderMuted.TLabel').pack(side='left')
        self.source=tk.StringVar(value=self.store.setting('source',DEFAULT_SOURCE))
        ttk.Label(top,text='数据源',style='HeaderMuted.TLabel').pack(side='left',padx=(24,5))
        ttk.Combobox(top,textvariable=self.source,values=[*SOURCES,'demo'],state='readonly',width=13).pack(side='left')
        self.start_btn=ttk.Button(top,text='开始筛选',command=self.start,style='Accent.TButton'); self.start_btn.pack(side='right',padx=5)
        self.cancel_btn=ttk.Button(top,text='取消筛选',command=self.request_cancel); self.cancel_btn.pack(side='right')
        self.tabs=ttk.Notebook(self); self.tabs.pack(fill='both',expand=True,padx=12)
        self.strategy_tab=ttk.Frame(self.tabs,padding=10); self.data_tab=ttk.Frame(self.tabs,padding=10); self.chat_tab=ttk.Frame(self.tabs,padding=10); self.report_tab=ttk.Frame(self.tabs,padding=10); self.backtest_tab=ttk.Frame(self.tabs,padding=10); self.settings_tab=ttk.Frame(self.tabs,padding=10)
        for w,label in [(self.strategy_tab,'筛选方案'),(self.data_tab,'数据与指标'),(self.chat_tab,'DeepSeek 聊天'),(self.report_tab,'报告'),(self.backtest_tab,'回测'),(self.settings_tab,'设置')]: self.tabs.add(w,text=label)
        self.build_strategy(); self.build_data(); self.build_chat(); self.build_reports(); self.build_backtest(); self.build_settings()
        bottom=ttk.Frame(self,padding=(16,10),style='Status.TFrame'); bottom.pack(fill='x',padx=12,pady=(0,12))
        self.status=tk.StringVar(value='就绪。tencent = 腾讯前复权；旧 eastmoney 日线若断连可手动切换。demo 为人工演示。')
        ttk.Label(bottom,textvariable=self.status,style='Status.TLabel').pack(anchor='w')
        self.progress_text=tk.StringVar(value='尚未开始筛选或回测')
        ttk.Label(bottom,textvariable=self.progress_text,style='Progress.TLabel').pack(anchor='w')
        self.progress=ttk.Progressbar(bottom,maximum=100); self.progress.pack(fill='x',pady=4)
        self.progress_done=0; self.progress_total=0
        self.populate(); self.refresh_versions(); self.refresh_reports(); self.poll_after=self.after(100,self.poll)
        self.protocol('WM_DELETE_WINDOW',self.close)
    def report_callback_exception(self,exc,val,tb):
        # Windowed executables have no visible stderr. Do not expose exception
        # values here: they may include settings or other private user input.
        message=f'界面操作异常（{exc.__name__}）。请重新选择报告或重启程序；若再次出现，请记录操作步骤。'
        if hasattr(self,'status'): self.status.set(message)
        messagebox.showerror('操作未完成',message,parent=self)
    # ---------------------------------------------------------------- strategy
    def build_strategy(self):
        ttk.Label(self.strategy_tab,text='策略（整套选股方案） → 条件组（全部满足 / 满足任一） → 条件（单条要求）',wraplength=850).pack(anchor='w',pady=(0,6))
        row=ttk.Frame(self.strategy_tab); row.pack(fill='x')
        ttk.Label(row,text='已保存策略 / 版本').pack(side='left')
        self.version_choice=tk.StringVar(); self.version_box=ttk.Combobox(row,textvariable=self.version_choice,state='readonly',width=40); self.version_box.pack(side='left')
        ttk.Button(row,text='载入 / 回退',command=self.load_version).pack(side='left',padx=4)
        ttk.Button(row,text='导出策略',command=self.export_strategy).pack(side='right')
        actions=ttk.Frame(self.strategy_tab); actions.pack(fill='x',pady=5)
        for text,command in [('新建策略',self.new_strategy),('删除当前载入策略',self.delete_strategy),('重命名策略',self.rename_strategy),('保存为新版本',self.save)]:
            ttk.Button(actions,text=text,command=command).pack(side='left',padx=3)
        self.loaded_strategy=tk.StringVar()
        ttk.Label(self.strategy_tab,textvariable=self.loaded_strategy,style='Muted.TLabel').pack(anchor='w')
        row2=ttk.Frame(self.strategy_tab); row2.pack(fill='x',pady=10)
        self.name=tk.StringVar(); self.pool=tk.StringVar(); self.exclude_st=tk.BooleanVar()
        ttk.Label(row2,text='策略名称（草稿）').pack(side='left'); ttk.Entry(row2,textvariable=self.name,width=25).pack(side='left',padx=6)
        ttk.Label(row2,text='范围').pack(side='left'); ttk.Combobox(row2,textvariable=self.pool,values=list(POOLS),state='readonly',width=15).pack(side='left',padx=6)
        ttk.Checkbutton(row2,text='排除 ST',variable=self.exclude_st).pack(side='left')
        ttk.Label(self.strategy_tab,text='双击条件编辑；双击条件组切换“全部 / 任一”。下拉选择后需点“载入”；删除只针对当前载入版本。',wraplength=850).pack(anchor='w')
        self.tree=ttk.Treeview(self.strategy_tab,show='tree',selectmode='browse'); self.tree.pack(fill='both',expand=True,pady=8); self.tree.bind('<Double-1>',lambda e:self.edit_rule())
        bar=ttk.Frame(self.strategy_tab); bar.pack(fill='x')
        for text,fn in [('添加条件',self.add_rule),('添加条件组',self.add_group),('编辑所选',self.edit_rule),('删除所选条件 / 组',self.delete_rule),('JSON 编辑 / 导入',self.json_editor)]: ttk.Button(bar,text=text,command=fn).pack(side='left',padx=3)
        ttk.Label(self.strategy_tab,text='mainboard=沪深主板；gem=创业板；star=科创板；all=沪深A股。默认保留原策略的ST设置。',style='Muted.TLabel').pack(anchor='w',pady=8)
    def draft(self):
        return {'schema_version':1,'name':self.name.get(),'pool':self.pool.get(),'exclude_st':self.exclude_st.get(),'rule':copy.deepcopy(self.strategy['rule'])}
    def current(self):
        return validate(self.draft())
    def populate(self):
        self.name.set(self.strategy['name']); self.pool.set(self.strategy['pool']); self.exclude_st.set(self.strategy['exclude_st']); self.draw_rules()
    def draw_rules(self):
        self.tree.delete(*self.tree.get_children()); self.rule_paths={}
        self.strategy_node=self.tree.insert('','end',text=f'策略：{self.name.get()} · v{self.version}（当前草稿）',open=True)
        def walk(r,parent='',path=()):
            label='条件：'+describe(r) if 'metric' in r else '条件组：全部满足（AND）' if 'all' in r else '条件组：满足任一（OR）'
            node=self.tree.insert(parent,'end',text=label,open=True); self.rule_paths[node]=path
            if 'metric' not in r:
                key=next(iter(r))
                for i,c in enumerate(r[key]): walk(c,node,path+(key,i))
        walk(self.strategy['rule'],self.strategy_node)
    def selected_path(self):
        sel=self.tree.selection(); return self.rule_paths.get(sel[0],()) if sel else ()
    def at(self,path):
        r=self.strategy['rule']
        for p in path: r=r[p]
        return r
    def add_rule(self): self.condition_dialog(None)
    def insert_rule(self,r):
        path=self.selected_path(); parent=self.at(path)
        if 'metric' in parent:
            if not path: self.strategy['rule']={'all':[parent,r]}; self.draw_rules(); return
            parent=self.at(path[:-2])
        parent[next(iter(parent))].append(r); self.draw_rules()
    def add_group(self): self.insert_rule({'all':[template()]})
    def edit_rule(self):
        if self.tree.selection()==(self.strategy_node,): self.rename_strategy(); return
        path=self.selected_path(); r=self.at(path)
        if 'metric' not in r:
            old=next(iter(r)); r['any' if old=='all' else 'all']=r.pop(old); self.draw_rules()
        else: self.condition_dialog(path)
    def delete_rule(self):
        path=self.selected_path()
        if not path: messagebox.showinfo('不能删除','整套规则不能删除；请修改其中条件。'); return
        parent=self.at(path[:-1]);
        if len(parent)==1: messagebox.showinfo('不能删除','条件组至少保留一个条件；可删除整个子组。'); return
        parent.pop(path[-1]); self.draw_rules()
    def condition_dialog(self,path):
        win=tk.Toplevel(self); win.title('条件模板'); win.geometry('900x560'); win.minsize(840,540); win.configure(background=COLORS['background']); win.transient(self); win.grab_set()
        existing=copy.deepcopy(self.at(path)) if path is not None else template()
        metric=tk.StringVar(value=existing['metric']); op=tk.StringVar(value=existing['op']); value=tk.StringVar(value=json.dumps(existing['value']))
        frame=ttk.Frame(win,padding=18); frame.pack(fill='both',expand=True)
        metric_labels={f'{meta[0]} · {key}':key for key,meta in CATALOG.items()}
        selected_label=next(label for label,key in metric_labels.items() if key==existing['metric'])
        metric_display=tk.StringVar(value=selected_label)
        selector=ttk.Frame(frame); selector.pack(fill='x')
        picker=ttk.Frame(selector); picker.pack(side='left',fill='both',expand=True,padx=(0,18))
        ttk.Label(picker,text='选择指标',style='Muted.TLabel').pack(anchor='w',pady=(0,6))
        box=ttk.Combobox(picker,textvariable=metric_display,values=list(metric_labels),state='readonly',width=32); box.pack(fill='x')
        info=tk.StringVar(); ttk.Label(picker,textvariable=info,wraplength=340,style='Muted.TLabel').pack(anchor='w',pady=(10,14))
        ttk.Label(picker,text='这个指标需要的参数',style='Muted.TLabel').pack(anchor='w')
        params_frame=ttk.Frame(picker); params_frame.pack(fill='x',pady=(5,0)); vars={}
        guide=ttk.LabelFrame(selector,text='指标速览 · 选择前可先看',padding=(12,8)); guide.pack(side='right',fill='both')
        summaries={
            'return_pct':'最近 N 天涨了或跌了多少',
            'volume_ratio':'短期成交量和长期平均量相比',
            'active_weeks':'最近几周有多少周波动达到要求',
            'price':'最新一个交易日的收盘价',
            'ma_ratio':'收盘价比近期均价高还是低',
            'breakout_ratio':'收盘价与此前 N 天最高价相比',
            'amplitude_pct':'一段时间内最高价与最低价差多少',
            'net_profit':'财报公布的年初至今累计净利润',
        }
        for key,meta in CATALOG.items():
            ttk.Label(guide,text=f'{meta[0]}  ·  {summaries[key]}',wraplength=410).pack(anchor='w',pady=3)
        param_labels={'days':'交易日数 days','short':'短期天数 short','long':'长期天数 long','weeks':'观察周数 weeks','amplitude':'每周振幅 amplitude'}
        def params_changed(event=None):
            for c in params_frame.winfo_children(): c.destroy()
            vars.clear(); m=metric.get(); meta=CATALOG[m]; info.set(f'{meta[0]}（{meta[1]}）\n{meta[3]}')
            for key,default in meta[2].items():
                row=ttk.Frame(params_frame); row.pack(fill='x',pady=3); ttk.Label(row,text=param_labels.get(key,key),width=19).pack(side='left')
                v=tk.StringVar(value=str(existing['params'].get(key,default) if m==existing['metric'] else default)); vars[key]=v; ttk.Entry(row,textvariable=v).pack(side='left')
        def metric_selected(event=None):
            metric.set(metric_labels[metric_display.get()]); params_changed()
        box.bind('<<ComboboxSelected>>',metric_selected); params_changed()
        ttk.Label(frame,text='比较方式',style='Muted.TLabel').pack(anchor='w',pady=(16,5))
        op_labels={'between':'区间内（含上下限）','>':'大于 >','>=':'大于等于 ≥','<':'小于 <','<=':'小于等于 ≤','==':'等于 ='}
        op_display=tk.StringVar(value=next(label for key,label in op_labels.items() if key==op.get()))
        ttk.Combobox(frame,textvariable=op_display,values=list(op_labels.values()),state='readonly',width=35).pack(anchor='w')
        ttk.Label(frame,text='阈值：单个数字；区间填写 [-8, -2]；量比 60% 填 0.6',style='Muted.TLabel').pack(anchor='w',pady=(12,5))
        ttk.Entry(frame,textvariable=value,width=42).pack(anchor='w')
        def apply():
            try:
                op.set(next(key for key,label in op_labels.items() if label==op_display.get()))
                r={'metric':metric.get(),'params':{k:json.loads(v.get()) for k,v in vars.items()},'op':op.get(),'value':json.loads(value.get())}
                test=self.draft(); test['rule']=r; validate(test)
                if path is None: self.insert_rule(r)
                elif path: self.at(path[:-1])[path[-1]]=r; self.draw_rules()
                else: self.strategy['rule']=r; self.draw_rules()
                win.destroy()
            except Exception as e: messagebox.showerror('条件无效',str(e),parent=win)
        ttk.Button(frame,text='应用到草稿',command=apply,style='Accent.TButton').pack(anchor='e',pady=14)
    def refresh_versions(self):
        self.version_rows=self.store.versions(); labels=[f'v{i} · {s["name"]} · {t[:10]}' for i,t,s in self.version_rows]
        self.version_box['values']=labels
        for label,(i,_,_) in zip(labels,self.version_rows):
            if i==self.version: self.version_choice.set(label)
        saved=next((s for i,_,s in self.version_rows if i==self.version),self.strategy)
        self.loaded_strategy.set(f'当前载入：v{self.version} · {saved["name"]}（删除仅影响这一条，不影响其他版本或报告）')
    def strategy_action_allowed(self):
        if self.running or self.chat_busy:
            messagebox.showinfo('请稍候','筛选、回测或 AI 请求进行中，请结束后再管理策略。'); return False
        return True
    def confirm_discard(self):
        _,saved=self.store.get(self.version)
        return self.draft()==saved or messagebox.askokcancel('未保存的修改','当前草稿尚未保存，继续会放弃这些修改。是否继续？')
    def invalidate_proposal(self):
        self.pending=None
        self.apply_ai.configure(state='disabled')
    def new_strategy(self):
        if not self.strategy_action_allowed(): return
        try:
            name=simpledialog.askstring('新建策略','输入策略名称（1～80字）。初始为“收盘价 > 0”，创建后请编辑条件。',parent=self)
            if name is None: return
            strategy=validate({'schema_version':1,'name':name.strip(),'pool':'mainboard','exclude_st':False,'rule':{'all':[template('price')]}})
            if not self.confirm_discard(): return
            version=self.store.save(strategy)
            self.version,self.strategy=version,strategy
            self.invalidate_proposal(); self.populate(); self.refresh_versions()
            self.status.set(f'已新建并载入 v{version} · {strategy["name"]}，请编辑条件后保存')
        except Exception as e: messagebox.showerror('新建失败',str(e))
    def rename_strategy(self):
        if not self.strategy_action_allowed(): return
        try:
            _,saved=self.store.get(self.version)
            if not self.confirm_discard(): return
            name=simpledialog.askstring('重命名策略','输入新名称（只修改名称，保留当前策略版本和条件）：',initialvalue=saved['name'],parent=self)
            if name is None: return
            self.version,self.strategy=self.store.rename_version(self.version,name.strip())
            self.invalidate_proposal(); self.populate(); self.refresh_versions()
            self.status.set(f'已重命名 v{self.version} · {self.strategy["name"]}；策略条件未改动')
        except Exception as e: messagebox.showerror('命名失败',str(e))
    def delete_strategy(self):
        if not self.strategy_action_allowed(): return
        try:
            target=self.version; _,saved=self.store.get(target)
            if len(self.store.versions())<=1:
                messagebox.showinfo('不能删除','至少保留一条策略，请先新建其他策略。'); return
            if not messagebox.askyesno('删除当前载入策略',f'只删除当前载入的 v{target} · {saved["name"]}。\n其他版本和历史报告不受影响。当前未保存草稿将放弃。\n\n确认删除？',parent=self): return
            self.version,self.strategy=self.store.delete_version(target)
            self.invalidate_proposal(); self.populate(); self.refresh_versions()
            self.status.set(f'已删除 v{target}，保留本机恢复记录；当前载入 v{self.version}')
        except Exception as e: messagebox.showerror('删除失败',str(e))
    def load_version(self):
        if not self.strategy_action_allowed(): return
        try:
            i=int(self.version_choice.get().split(' · ')[0][1:])
            if not self.confirm_discard(): return
            version,strategy=self.store.get(i); self.store.set_default(i)
            self.version,self.strategy=version,strategy
            self.populate(); self.refresh_versions(); self.invalidate_proposal(); self.status.set(f'已载入并设为默认 v{i}')
        except Exception as e: messagebox.showerror('载入失败',str(e))
    def save(self):
        try:
            strategy=self.current(); version=self.store.save(strategy)
            self.strategy,self.version=strategy,version
            self.refresh_versions(); self.draw_rules(); self.status.set(f'已保存默认策略 v{self.version}'); return True
        except Exception as e: messagebox.showerror('保存失败',str(e)); return False
    def export_strategy(self):
        try:
            s=self.current(); path=filedialog.asksaveasfilename(defaultextension='.json',initialfile='strategy.json')
            if path:
                from pathlib import Path
                Path(path).write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding='utf-8')
        except Exception as e: messagebox.showerror('导出失败',str(e))
    def json_editor(self):
        win=tk.Toplevel(self); win.title('完整策略 JSON'); win.geometry('800x600'); win.configure(background=COLORS['background']); editor=ScrolledText(win); self.style_text(editor); editor.pack(fill='both',expand=True,padx=12,pady=12); editor.insert('1.0',json.dumps(self.draft(),ensure_ascii=False,indent=2))
        bar=ttk.Frame(win); bar.pack(fill='x')
        def import_file():
            path=filedialog.askopenfilename(filetypes=[('JSON','*.json')])
            if path:
                try:
                    from pathlib import Path
                    text=Path(path).read_text(encoding='utf-8-sig'); validate(json.loads(text)); editor.delete('1.0','end'); editor.insert('1.0',text)
                except Exception as e: messagebox.showerror('导入失败',str(e),parent=win)
        def apply():
            try: self.strategy=validate(json.loads(editor.get('1.0','end'))); self.populate(); win.destroy()
            except Exception as e: messagebox.showerror('校验失败',str(e),parent=win)
        ttk.Button(bar,text='从文件导入',command=import_file).pack(side='left'); ttk.Button(bar,text='校验并应用到草稿',command=apply).pack(side='right')
    # -------------------------------------------------------------------- data
    def build_data(self):
        ttk.Label(self.data_tab,text='实现状态 ≠ 当前可获取。首次使用实时源，请先检测连接；检查成功也不代表所有公司都有完整数据。',wraplength=1000).pack(anchor='w')
        cols=('title','unit','source','status'); tree=ttk.Treeview(self.data_tab,columns=cols,show='headings',height=5)
        for c,t,w in zip(cols,['字段 / 指标','单位','数据来源','实现状态'],[230,80,340,280]): tree.heading(c,text=t); tree.column(c,width=w)
        tree.pack(fill='x',pady=10)
        for field in ['代码、名称','日期、开盘、最高、最低、收盘','成交量']: tree.insert('','end',values=(field,'见下方口径','腾讯模式名单也来自腾讯；其他模式名单为东财','连接状态见下方检测结果'))
        for k,v in CATALOG.items(): tree.insert('','end',values=(v[0],v[1],'东财利润表' if k=='net_profit' else '由日线按固定公式计算','已实现；依赖原始数据'))
        tree.insert('','end',values=('PE、ROE、行业及其他财务指标','—','旧映射未经确认','未开放筛选'))
        text=ScrolledText(self.data_tab,height=4,wrap='word'); self.style_text(text); text.pack(fill='both',expand=True)
        for k,v in CATALOG.items(): text.insert('end',f'{k} · {v[0]} [{v[1]}]\n{v[3]}；参数默认值 {v[2]}\n\n')
        text.insert('end','价格：tencent 腾讯前复权、eastmoney 东财前复权、sina 新浪未复权。每次筛选固定一个来源，报告标注实际来源。腾讯模式名单来自腾讯，其他模式名单仍来自东财；名单仅读取代码/名称，不用于价格计算。腾讯分页出现重复或数量不足会停止，避免漏筛。净利润仍来自东财独立财务接口：元，最新已披露合并累计口径。\n缺失数据不会被当作盈利或自动通过。')
        text.configure(state='disabled')
        self.probe_btn=ttk.Button(self.data_tab,text='检测当前数据源连接',command=self.probe); self.probe_btn.pack(anchor='w',pady=8)
        ttk.Button(self.data_tab,text='选择腾讯前复权并检测',command=self.select_tencent).pack(anchor='w')
        self.probe_text=tk.StringVar(value='尚未检测'); ttk.Label(self.data_tab,textvariable=self.probe_text,wraplength=1000).pack(anchor='w')
    def provider(self,source,cancel=None): return DemoProvider() if source=='demo' else LiveProvider(source,cancel)
    def select_tencent(self):
        if self.probe_btn.instate(['disabled']): return
        self.source.set('tencent'); self.store.set_setting('source','tencent'); self.probe()
    def probe(self):
        if self.probe_btn.instate(['disabled']): return
        source=self.source.get(); self.probe_btn.configure(state='disabled'); self.probe_text.set('检测中……')
        def worker():
            try: self.events.put(('probe',(source,self.provider(source).probe())))
            except Exception as e: self.events.put(('probe',(source,{'错误':{'ok':False,'detail':str(e)}})))
        threading.Thread(target=worker,daemon=True).start()
    # -------------------------------------------------------------------- chat
    def build_chat(self):
        ttk.Label(self.chat_tab,text='AI 只生成受限规则。发送内容包括当前策略、本窗口最近对话，以及下面你勾选的报告全文；未勾选的文件不会上传。修改须点击应用。').pack(anchor='w')
        attach=ttk.LabelFrame(self.chat_tab,text='随消息发送的本机报告（可选，勾选后才会上传）',padding=6); attach.pack(fill='x',pady=6)
        tools=ttk.Frame(attach); tools.pack(fill='x')
        ttk.Button(tools,text='刷新报告列表',command=self.refresh_chat_reports).pack(side='left')
        ttk.Button(tools,text='全不选',command=lambda:self.set_all_reports(False)).pack(side='left',padx=4)
        ttk.Button(tools,text='把当前报告加入',command=self.attach_current_report).pack(side='left')
        ttk.Label(tools,text=f'最多 {MAX_ATTACHMENTS} 份；单份超长会截断',style='Muted.TLabel').pack(side='left',padx=8)
        report_canvas=tk.Canvas(attach,height=100,highlightthickness=0,background=COLORS['background'],borderwidth=0)
        report_scroll=ttk.Scrollbar(attach,orient='vertical',command=report_canvas.yview)
        report_canvas.configure(yscrollcommand=report_scroll.set)
        report_scroll.pack(side='right',fill='y'); report_canvas.pack(fill='x')
        self.report_checks_frame=ttk.Frame(report_canvas)
        report_canvas.create_window((0,0),window=self.report_checks_frame,anchor='nw')
        self.report_checks_frame.bind('<Configure>',lambda e:report_canvas.configure(scrollregion=report_canvas.bbox('all')))
        self.report_hint=tk.StringVar(value='尚未选择报告')
        ttk.Label(attach,textvariable=self.report_hint,wraplength=1000,style='Warning.TLabel').pack(anchor='w',pady=(4,0))
        self.chat=ScrolledText(self.chat_tab,height=11,wrap='word',state='disabled'); self.style_text(self.chat); self.chat.pack(fill='both',expand=True,pady=8)
        self.input=ScrolledText(self.chat_tab,height=3,wrap='word'); self.style_text(self.input); self.input.pack(fill='x',pady=8)
        for key in ('<Return>','<KP_Enter>'): self.input.bind(key,self.chat_enter)
        row=ttk.Frame(self.chat_tab); row.pack(fill='x')
        self.send_btn=ttk.Button(row,text='发送',command=self.send,style='Accent.TButton'); self.send_btn.pack(side='left')
        ttk.Label(row,text='Enter 发送 · Shift + Enter 换行',style='Muted.TLabel').pack(side='left',padx=8)
        self.apply_ai=ttk.Button(row,text='应用修改并保存新版本',command=self.apply_proposal,state='disabled'); self.apply_ai.pack(side='right')
        self.refresh_chat_reports()
    def set_all_reports(self,value):
        for var in self.report_checks.values(): var.set(value)
        self.update_report_hint()
    def refresh_chat_reports(self):
        selected={name for name,var in self.report_checks.items() if var.get()}
        for child in self.report_checks_frame.winfo_children(): child.destroy()
        self.report_checks={}; self.chat_files={}
        paths=self.report_files()
        if not paths:
            ttk.Label(self.report_checks_frame,text='本机还没有报告；先运行一次筛选。').pack(anchor='w')
        for index,path in enumerate(paths):
            var=tk.BooleanVar(value=path.name in selected); self.report_checks[path.name]=var; self.chat_files[path.name]=path
            ttk.Checkbutton(self.report_checks_frame,text=path.name,variable=var,command=self.update_report_hint).grid(row=index//3,column=index%3,sticky='w',padx=4)
        self.update_report_hint()
    def checked_reports(self):
        return [self.chat_files[name] for name,var in self.report_checks.items() if var.get() and name in self.chat_files]
    def update_report_hint(self):
        chosen=self.checked_reports()
        if not chosen: self.report_hint.set('本次不会发送任何报告内容。'); return
        try:
            _,notes=build_report_context(chosen,[self.store.reports])
            self.report_hint.set('本次将发送给 DeepSeek：'+'、'.join(notes))
        except ValueError as e: self.report_hint.set(str(e))
    def attach_current_report(self):
        if not self.current_report:
            messagebox.showinfo('没有当前报告','请到“报告”页选中一份报告。'); return
        name=self.current_report.name
        if name in self.report_checks: self.report_checks[name].set(True); self.update_report_hint()
        else: messagebox.showinfo('不在列表中','该报告不在当前列表（旧报告请点“刷新报告列表”）。')
    def append_chat(self,text):
        self.chat.configure(state='normal'); self.chat.insert('end',text+'\n\n'); self.chat.see('end'); self.chat.configure(state='disabled')
    def ime_composing(self):
        if sys.platform!='win32': return False
        # Only inspect the input method context of this application's input box.
        import ctypes
        from ctypes import wintypes
        imm=ctypes.WinDLL('imm32'); user=ctypes.WinDLL('user32')
        user.GetFocus.restype=wintypes.HWND
        imm.ImmGetContext.argtypes=[wintypes.HWND]; imm.ImmGetContext.restype=wintypes.HANDLE
        imm.ImmGetCompositionStringW.argtypes=[wintypes.HANDLE,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD]
        imm.ImmGetCompositionStringW.restype=ctypes.c_long
        imm.ImmReleaseContext.argtypes=[wintypes.HWND,wintypes.HANDLE]
        hwnd=user.GetFocus() or self.input.winfo_id(); context=imm.ImmGetContext(hwnd)
        if not context: return False
        try: return imm.ImmGetCompositionStringW(context,8,None,0)>0
        finally: imm.ImmReleaseContext(hwnd,context)
    def chat_enter(self,event):
        if event.state & 0x1: return None
        try:
            if self.ime_composing(): return 'break'
        except (OSError,AttributeError):
            self.status.set('无法检查输入法状态，请点击“发送”按钮'); return 'break'
        self.send()
        return 'break'
    def send(self):
        if self.chat_busy: return
        try: current=self.current()
        except Exception as e: messagebox.showerror('请先修正策略',str(e)); return
        message=self.input.get('1.0','end').strip(); key=self.key.get().strip(); model=self.model.get().strip()
        if not message: return
        if not key: messagebox.showinfo('需要密钥','请到设置填写 DeepSeek API Key。'); self.tabs.select(self.settings_tab); return
        chosen=self.checked_reports()
        try:
            report_context,notes=build_report_context(chosen,[self.store.reports])
        except ValueError as e:
            messagebox.showerror('报告无法发送',str(e)); return
        if chosen and not messagebox.askokcancel('确认发送报告内容',
                '以下报告内容（含行情与财务数字）将发送到 DeepSeek 官方接口：\n\n'+'\n'.join(notes)+'\n\n继续发送？'):
            return
        self.chat_busy=True; self.pending=None; self.apply_ai.configure(state='disabled'); self.send_btn.configure(state='disabled'); self.input.delete('1.0','end')
        self.append_chat('你：'+message+('\n（随消息发送报告：'+'、'.join(p.name for p in chosen)+'）' if chosen else ''))
        history=copy.deepcopy(self.history); base_version=self.version
        def worker():
            try:
                obj=ask(key,model,message,current,history,report_context=report_context)
                self.events.put(('ai',(obj,current,message,[p.name for p in chosen],base_version)))
            except Exception as e: self.events.put(('ai_error',str(e)))
        threading.Thread(target=worker,daemon=True).start()
    def apply_proposal(self):
        if not self.pending: return
        proposal,base=self.pending
        try:
            if self.current()!=base: raise RuleError('策略已在别处修改，请让AI基于当前策略重新生成，避免覆盖你的改动')
            self.strategy=validate(proposal); self.populate()
            if self.save(): self.pending=None; self.apply_ai.configure(state='disabled'); self.append_chat('已应用并保存为新版本。')
        except Exception as e: messagebox.showerror('无法应用',str(e))
    # ----------------------------------------------------------------- reports
    def report_files(self):
        return sorted(self.store.reports.glob('StockReport_*.md'),reverse=True)
    def build_reports(self):
        pane=ttk.Panedwindow(self.report_tab,orient='horizontal'); pane.pack(fill='both',expand=True)
        left=ttk.Frame(pane); right=ttk.Frame(pane); pane.add(left,weight=1); pane.add(right,weight=4)
        self.report_list=tk.Listbox(left,width=32,background=COLORS['field'],foreground=COLORS['text'],selectbackground=COLORS['selection'],selectforeground=COLORS['text'],borderwidth=1,relief='flat',highlightthickness=1,highlightbackground=COLORS['border'],highlightcolor=COLORS['accent']); self.report_list.pack(fill='both',expand=True); self.report_list.bind('<<ListboxSelect>>',self.open_selected)
        ttk.Button(left,text='刷新历史',command=self.refresh_reports).pack(fill='x',pady=5)
        self.report_table=ResultsTable(right,self.report_columns(),list(STATUS_LABELS.values())[1:]); self.report_table.pack(fill='both',expand=True)
        ttk.Label(right,text='报告概览与逐股条件明细（Markdown 原文）',style='Muted.TLabel').pack(anchor='w')
        self.report_view=ScrolledText(right,height=12,wrap='word',state='disabled'); self.style_text(self.report_view); self.report_view.pack(fill='both',expand=True)
        for tag,size in [('h1',20),('h2',15),('h3',12)]: self.report_view.tag_configure(tag,font=('Microsoft YaHei UI',size,'bold'),spacing1=12,spacing3=6)
        self.report_view.tag_configure('code',font=('Consolas',10),background=COLORS['raised'])
        self.report_view.tag_configure('bold',font=('Microsoft YaHei UI',10,'bold'))
        row=ttk.Frame(right); row.pack(fill='x',pady=6)
        ttk.Button(row,text='用外部阅读器打开',command=lambda:self.open_external_report()).pack(side='left')
        ttk.Button(row,text='打开报告文件夹',command=lambda:self.safe_external(self.store.reports)).pack(side='left',padx=5)
        ttk.Button(row,text='把这份报告加入 AI 发送列表',command=self.attach_current_report).pack(side='left')
    def report_columns(self):
        return [('code','代码',90,'text'),('name','名称',130,'text'),('date','数据日期',100,'text'),('close','收盘价',90,'num'),
                ('report_date','财报期',100,'text'),('net_profit','净利润(元)',130,'num'),('state','状态',150,'text')]
    def refresh_reports(self):
        self.report_paths=self.report_files(); self.report_list.delete(0,'end')
        for path in self.report_paths: self.report_list.insert('end',path.name)
        self.refresh_chat_reports(); self.refresh_backtest_reports()
    def open_selected(self,event=None):
        sel=self.report_list.curselection()
        if sel:
            try: self.show_report(self.report_paths[sel[0]])
            except (OSError,ValueError) as e: messagebox.showerror('报告无法打开',str(e))
    def show_report(self,path):
        content=path.read_text(encoding='utf-8-sig')
        self.current_report=path; self.report_view.configure(state='normal'); self.report_view.delete('1.0','end'); code=False
        for line in content.splitlines():
            if line.startswith('```'): code=not code; continue
            if code or line.startswith('|'): self.report_view.insert('end',line+'\n','code'); continue
            m=re.match(r'^(#{1,3}) (.*)',line)
            if m: self.report_view.insert('end',m[2]+'\n','h'+str(len(m[1]))); continue
            for i,part in enumerate(line.split('**')): self.report_view.insert('end',part,'bold' if i%2 else ())
            self.report_view.insert('end','\n')
        self.report_view.configure(state='disabled')
        self.current_report_rows=self.report_rows_for(path); self.report_table.set_rows(self.current_report_rows)
    def report_rows_for(self,path):
        """Rows for the report table: prefer the JSON sidecar, else the Markdown."""
        side=path.with_suffix('.json')
        if side.is_file():
            try:
                data=json.loads(side.read_text(encoding='utf-8-sig'))
                rows=[]
                for r in data.get('results',[]):
                    fin=r.get('financial') or {}
                    status=STATUS_LABELS.get(r.get('status'),r.get('status',''))
                    rows.append({'code':r.get('code',''),'name':r.get('name',''),'date':r.get('date',''),
                                 'close':r.get('close'),'report_date':fin.get('report_date',''),
                                 'net_profit':fin.get('net_profit'),'state':status})
                if rows: return rows
            except (OSError,ValueError): pass
        rows=[]
        try: report=bt.parse_markdown_report(path)
        except bt.BacktestError: return []
        for r in report['results']:
            fin=r.get('financial') or {}
            rows.append({'code':r['code'],'name':r['name'],'date':r.get('date',''),'close':r.get('close'),
                         'report_date':fin.get('report_date',''),'net_profit':fin.get('net_profit'),'state':STATUS_LABELS['matched']})
        return rows
    def safe_external(self,path):
        try: external(path)
        except Exception as e: messagebox.showerror('打开失败',str(e))
    def open_external_report(self):
        if self.current_report: self.safe_external(self.current_report)
    # ---------------------------------------------------------------- backtest
    def build_backtest(self):
        ttk.Label(self.backtest_tab,text='选择一份过去的报告，重新获取这些股票的最新价格，得到“入选时 → 现在”的涨跌。基准价与最新价取自同一次连续行情请求，避免前复权口径变化造成虚假涨跌。结果不是收益预测。',wraplength=1050).pack(anchor='w')
        controls=ttk.Frame(self.backtest_tab); controls.pack(fill='x',pady=8)
        ttk.Label(controls,text='历史报告').pack(side='left')
        self.backtest_choice=tk.StringVar(); self.backtest_box=ttk.Combobox(controls,textvariable=self.backtest_choice,values=[],state='readonly',width=42); self.backtest_box.pack(side='left',padx=6)
        self.backtest_box.bind('<<ComboboxSelected>>',lambda e:self.preview_backtest())
        ttk.Button(controls,text='刷新列表',command=self.refresh_backtest_reports).pack(side='left')
        controls2=ttk.Frame(self.backtest_tab); controls2.pack(fill='x',pady=4)
        ttk.Label(controls2,text='行情来源').pack(side='left',padx=(0,4))
        self.backtest_source=tk.StringVar(value=DEFAULT_SOURCE)
        ttk.Combobox(controls2,textvariable=self.backtest_source,values=list(SOURCES),state='readonly',width=12).pack(side='left')
        self.backtest_btn=ttk.Button(controls2,text='一键回测',command=self.start_backtest,style='Accent.TButton'); self.backtest_btn.pack(side='left',padx=10)
        ttk.Button(controls2,text='打开报告文件夹',command=lambda:self.safe_external(self.store.reports)).pack(side='left')
        self.backtest_hint=tk.StringVar(value='尚未选择报告。默认使用原报告记录的数据源；手动换源会混用不同复权口径，请谨慎。')
        ttk.Label(self.backtest_tab,textvariable=self.backtest_hint,wraplength=1050,style='Warning.TLabel').pack(anchor='w')
        self.backtest_table=ResultsTable(self.backtest_tab,self.backtest_columns(),['已回测','无法回测','已取消']); self.backtest_table.pack(fill='both',expand=True,pady=8)
        self.refresh_backtest_reports()
    def backtest_columns(self):
        return [('code','股票代码',90,'text'),('name','名称',120,'text'),('selection_date','入选日期',100,'text'),
                ('recorded_close','当时收盘价(报告记录)',170,'num'),('baseline_price','回测基准价',110,'num'),
                ('latest_price','最新价',90,'num'),('latest_date','最新数据日期',110,'text'),('change','涨跌额',90,'num'),
                ('change_pct','涨跌幅(%)',100,'num'),('trading_days','持有交易日数',110,'num'),
                ('source','数据来源',80,'text'),('baseline_source','基准价来源',240,'text'),('state','状态',140,'text'),('reason','原因 / 提示',320,'text')]
    def refresh_backtest_reports(self):
        paths=[p for p in self.report_files()]
        paths+=sorted(self.store.reports.glob('StockReport_*.json'),reverse=True)
        seen=set(); ordered=[]
        for path in paths:
            if not re.fullmatch(r'StockReport_\d{4}-\d{2}-\d{2}_\d+\.(md|json)',path.name): continue
            key=path.with_suffix('').name
            if key in seen: continue
            seen.add(key); ordered.append(path)
        self.backtest_paths=ordered
        self.backtest_box['values']=[p.name for p in ordered]
        if self.backtest_choice.get() not in [p.name for p in ordered]:
            self.backtest_choice.set(ordered[0].name if ordered else '')
        if ordered: self.preview_backtest()
        else: self.backtest_hint.set('没有历史选股报告；请先生成真实筛选报告。')
    def selected_backtest_path(self):
        name=self.backtest_choice.get()
        for path in getattr(self,'backtest_paths',[]):
            if path.name==name: return path
        return None
    def preview_backtest(self):
        path=self.selected_backtest_path()
        if not path: return
        token=object(); self.preview_token=token
        self.backtest_source.set('')
        self.backtest_hint.set('正在后台读取历史报告；界面仍可操作。')
        def worker():
            try:
                report=bt.load_report(path); stocks=bt.matched_stocks(report)
                summary={k:report.get(k) for k in ('provider_metadata','source','_basis','demo','market_date','generated')}
                self.events.put(('backtest_preview',(token,path,summary,len(stocks),None)))
            except Exception as e: self.events.put(('backtest_preview',(token,path,{},0,str(e))))
        threading.Thread(target=worker,daemon=True).start()
    def show_backtest_preview(self,payload):
        token,path,report,count,error=payload
        if token is not getattr(self,'preview_token',None) or self.running or path!=self.selected_backtest_path(): return
        if error:
            self.backtest_hint.set('无法读取：'+error); return
        recorded=bt.recorded_source(report)
        if not self.backtest_source.get(): self.backtest_source.set(recorded or '')
        basis=report.get('_basis'); demo='（演示报告，不能回测真实行情）' if report.get('demo') else ''
        self.backtest_hint.set(f'{path.name}：基准日 {report.get("market_date") or "使用生成日期（缺行情日）"}，入选股票 {count} 只，原记录来源 {basis}{demo}。原行情来源：{recorded or "无法识别，请手动选择"}。')
    def start_backtest(self):
        if self.running: messagebox.showinfo('正在运行','请等待当前任务结束或先取消。'); return
        path=self.selected_backtest_path()
        if not path: messagebox.showinfo('请选择报告','先在“报告”页生成报告，或点“刷新列表”选择一份历史报告。'); return
        source=self.backtest_source.get()
        self.running=True; self.cancel=threading.Event()
        self.start_btn.configure(state='disabled'); self.backtest_btn.configure(state='disabled'); self.cancel_btn.configure(text='取消当前任务')
        self._set_progress(0,0,'正在后台读取回测报告'); self.backtest_hint.set('已收到点击，正在读取本地报告；尚未联网。')
        def read_worker():
            try: self.events.put(('backtest_ready',(path,source,bt.load_report(path))))
            except Exception as e: self.events.put(('error','报告读取失败：'+str(e)))
        threading.Thread(target=read_worker,daemon=True).start()
    def begin_backtest(self,payload):
        path,source,report=payload
        cancelled=self.cancel.is_set()
        self._idle()
        self._set_progress(0,0,'回测尚未开始',active=False)
        if cancelled:
            self._set_progress(0,0,'回测已取消，未开始行情请求',active=False)
            self.status.set('回测已取消，未开始行情请求'); return
        try:
            stocks=bt.matched_stocks(report)
            if report.get('demo'): raise bt.BacktestError('这是演示数据报告，不能对真实行情回测')
            if not stocks: raise bt.BacktestError('该报告没有状态为“符合”的股票')
        except bt.BacktestError as e:
            messagebox.showerror('无法回测',str(e)); return
        source=source or bt.recorded_source(report)
        if source not in SOURCES:
            messagebox.showerror('请选择来源','无法识别原报告的数据源，请明确选择行情来源。'); return
        recorded=bt.recorded_source(report)
        if recorded!=source and not messagebox.askokcancel('确认行情来源',f'原报告来源：{recorded or "未知"}；本次：{source}。复权口径可能不同，仍要继续吗？'): return
        try:
            baseline,note=bt.baseline_info(report)
            if note and not messagebox.askokcancel('确认回测日期',note+f'：{baseline}。继续吗？'): return
        except bt.BacktestError as e:
            messagebox.showerror('无法回测',str(e)); return
        self.running=True; self.cancel=threading.Event(); cancel=self.cancel; provider=self.provider(source,cancel)
        self.start_btn.configure(state='disabled'); self.backtest_btn.configure(state='disabled'); self.cancel_btn.configure(text='取消当前任务')
        self._set_progress(0,len(stocks),f'开始回测 {path.name}（来源 {source}）')
        self.backtest_hint.set('任务已启动：正在获取市场截止日。网络较慢时请等待，可点击顶部“取消当前任务”。')
        self.backtest_table.set_rows([]); self.backtest_rows=[]
        def worker():
            try:
                run=bt.backtest(report,provider,cancel,lambda a,b,msg:self.events.put(('progress',(a,b,msg))))
                out=bt.write_backtest(self.store,run); self.events.put(('backtest_done',(out,run)))
            except Cancelled:
                self.events.put(('error','回测已取消，尚未产生可保存的逐股结果'))
            except Exception as e: self.events.put(('error',str(e)))
        threading.Thread(target=worker,daemon=True).start()
    def show_backtest(self,path,run):
        rows=[]
        for r in run.get('results',[]):
            rows.append({'code':r.get('code',''),'name':r.get('name',''),'selection_date':r.get('selection_date',''),
                         'recorded_close':r.get('recorded_close'),'baseline_price':r.get('baseline_price'),
                         'latest_price':r.get('latest_price'),'latest_date':r.get('latest_date',''),
                         'change':r.get('change'),'change_pct':r.get('change_pct'),'trading_days':r.get('trading_days',''),
                         'source':run.get('source',''),'baseline_source':r.get('baseline_source',''),'state':r.get('status',''),'reason':r.get('reason','')})
        self.backtest_rows=rows; self.backtest_run=run; self.backtest_table.set_rows(rows)
        counts=run.get('counts',{})
        self.backtest_hint.set(f"{path.name} · {run['status']} · 基准日 {run['baseline_date']} · 已回测 {counts.get('ok',0)} · 无法回测 {counts.get('unavailable',0)}"
                               + (f" · 运行错误：{run['error']}" if run.get('error') else ''))
    # ---------------------------------------------------------------- settings
    def build_settings(self):
        f=self.settings_tab
        ttk.Label(f,text='DeepSeek API Key').pack(anchor='w',pady=(8,4))
        entry_row=ttk.Frame(f); entry_row.pack(fill='x')
        stored=self.store.get_api_key()
        self.key=tk.StringVar(value=stored or os.environ.get('DEEPSEEK_API_KEY',''))
        self.key_entry=ttk.Entry(entry_row,textvariable=self.key,show='*',width=70); self.key_entry.pack(side='left')
        self.show_key=tk.BooleanVar(value=False)
        ttk.Checkbutton(entry_row,text='显示密钥',variable=self.show_key,command=self.toggle_key_visibility).pack(side='left',padx=8)
        save_row=ttk.Frame(f); save_row.pack(fill='x',pady=6)
        self.keep_key=tk.BooleanVar(value=self.store.setting('keep_api_key','1')!='0')
        ttk.Checkbutton(save_row,text='保存到本机（关闭应用后无需重填）',variable=self.keep_key).pack(side='left')
        ttk.Button(save_row,text='清除已保存密钥',command=self.clear_key).pack(side='left',padx=8)
        self.key_state=tk.StringVar(value='已从本机读取已保存的密钥' if stored else ('当前使用环境变量 DEEPSEEK_API_KEY' if os.environ.get('DEEPSEEK_API_KEY') else '尚未填写密钥'))
        ttk.Label(f,textvariable=self.key_state,style='Muted.TLabel').pack(anchor='w')
        ttk.Label(f,text='密钥保存在本机数据目录的 SQLite 中，是明文记录：能读取该目录的程序也能读到它。不勾选保存时，密钥只在本次会话内存中。密钥不会写入日志、报告或 CSV；复制包含 data 的整个项目时也会复制密钥。',wraplength=950).pack(anchor='w',pady=(4,0))
        ttk.Label(f,text='模型名称（可自行修改；默认依据交付时的官方接口文档）').pack(anchor='w',pady=(18,8))
        self.model=tk.StringVar(value=self.store.setting('model',DEFAULT_MODEL)); ttk.Entry(f,textvariable=self.model,width=35).pack(anchor='w')
        ttk.Button(f,text='保存密钥、模型与数据源设置',command=self.save_settings).pack(anchor='w',pady=12)
        ttk.Label(f,text=f'本机数据目录：{self.store.root}\n\n策略、设置和报告保存在上面显示的目录；备份或移动软件时请一并检查 data 文件夹。\n实时数据需要联网；demo不联网，所有输出明确标为演示。\n报告同时输出 Markdown（阅读）与 CSV（可筛选排序），另有 .json 审计数据。',wraplength=950).pack(anchor='w',pady=12)
        ttk.Button(f,text='打开数据目录',command=lambda:self.safe_external(self.store.root)).pack(anchor='w')
    def toggle_key_visibility(self):
        self.key_entry.configure(show='' if self.show_key.get() else '*')
    def clear_key(self):
        self.store.clear_api_key(); self.key.set(''); self.key_state.set('已清除本机保存的密钥'); self.status.set('已清除本机保存的 DeepSeek 密钥')
    def save_settings(self):
        self.store.set_setting('model',self.model.get()); self.store.set_setting('source',self.source.get())
        keep=self.keep_key.get(); self.store.set_setting('keep_api_key','1' if keep else '0')
        value=self.key.get().strip()
        if keep and value:
            self.store.set_api_key(value); self.key_state.set(f'已保存到本机（{value[:4]}…{value[-4:]}）' if len(value)>8 else '已保存到本机')
            self.status.set('密钥、模型和数据源已保存；下次启动会自动读取密钥')
        elif keep and not value:
            self.store.clear_api_key(); self.key_state.set('密钥为空，已清除本机记录'); self.status.set('模型和数据源已保存；密钥为空')
        else:
            self.store.clear_api_key(); self.key_state.set('未勾选保存：密钥只在本次会话有效'); self.status.set('模型和数据源已保存；密钥未保存到本机')
    # ------------------------------------------------------------ run screening
    def _set_progress(self,done,total,message,active=True):
        """Only the Tk thread calls this; workers send progress through events."""
        done=max(0,int(done)); total=max(0,int(total))
        self.progress_done=done; self.progress_total=total
        if total:
            if str(self.progress['mode'])=='indeterminate': self.progress.stop()
            self.progress.configure(mode='determinate',value=min(done,total)*100/total)
            amount=f'已处理 {done}/{total}（{min(done,total)*100//total}%）'
        else:
            if active:
                if str(self.progress['mode'])!='indeterminate':
                    self.progress.configure(mode='indeterminate')
                    self.progress.start(20)
            else:
                if str(self.progress['mode'])=='indeterminate': self.progress.stop()
                self.progress.configure(mode='determinate',value=0)
            amount='总数尚未取得'
        if active and self.cancel.is_set() and self.running:
            message=f'正在取消，等待当前请求结束（{message}）'
        self.progress_text.set(f'{message} · {amount}')
        if active: self.status.set(message)

    def request_cancel(self):
        if not self.running or self.cancel.is_set(): return
        self.cancel.set()
        self._set_progress(self.progress_done,self.progress_total,'当前请求结束后停止')

    def start(self):
        if self.running: return
        try:
            s=self.current(); _,saved=self.store.get(self.version)
            if s!=saved:
                if not self.save(): return
            source=self.source.get(); self.store.set_setting('source',source)
        except Exception as e: messagebox.showerror('无法运行',str(e)); return
        self.running=True; self.cancel=threading.Event(); self.start_btn.configure(state='disabled'); self.backtest_btn.configure(state='disabled'); self.cancel_btn.configure(text='取消当前任务'); version=self.version; provider=self.provider(source,self.cancel); cancel=self.cancel
        self._set_progress(0,0,'正在获取完整股票列表'+('【人工演示数据】' if source=='demo' else ''))
        def worker():
            try:
                run=screen(s,version,provider,cancel,lambda a,b,msg:self.events.put(('progress',(a,b,msg))))
                path=write_report(self.store,run); self.events.put(('done',(path,run)))
            except Exception as e: self.events.put(('error',str(e)))
        threading.Thread(target=worker,daemon=True).start()
    def _idle(self):
        self.running=False; self.start_btn.configure(state='normal'); self.backtest_btn.configure(state='normal'); self.cancel_btn.configure(text='取消筛选')
    def poll(self):
        try:
            while True:
                kind,payload=self.events.get_nowait()
                if kind=='progress':
                    a,b,msg=payload; self._set_progress(a,b,msg)
                elif kind in ('done','error'):
                    self._idle()
                    if kind=='error':
                        self._set_progress(self.progress_done,self.progress_total,'运行失败',active=False)
                        self.status.set('运行失败：'+payload); messagebox.showerror('运行失败',payload)
                    else:
                        path,run=payload
                        self._set_progress(len(run.get('results',[])),run.get('counts',{}).get('total',0),'筛选'+run['status'],active=False)
                        self.status.set(run['status']+' · '+path.name); self.refresh_reports(); self.show_report(path); self.tabs.select(self.report_tab)
                elif kind=='backtest_done':
                    self._idle(); path,run=payload
                    self._set_progress(len(run.get('results',[])),run.get('counts',{}).get('total',0),'回测'+run['status'],active=False)
                    self.show_backtest(path,run); self.tabs.select(self.backtest_tab)
                    self.status.set(run['status']+' · '+path.name)
                elif kind=='backtest_preview': self.show_backtest_preview(payload)
                elif kind=='backtest_ready': self.begin_backtest(payload)
                elif kind=='probe':
                    source,result=payload
                    self.probe_btn.configure(state='normal'); self.probe_text.set(f'本次检测来源：{source}\n'+'\n'.join(f'{k}：{"连接成功" if v["ok"] else "未通过"} — {v["detail"]}' for k,v in result.items()))
                elif kind in ('ai','ai_error'):
                    self.chat_busy=False; self.send_btn.configure(state='normal')
                    if kind=='ai_error': self.append_chat('请求未应用：'+payload)
                    else:
                        obj,base,message,attached,base_version=payload
                        self.append_chat('DeepSeek：'+obj['reply']+(f"\n（本次参考报告：{'、'.join(attached)}）" if attached else ''))
                        self.history += [{'role':'user','content':message},{'role':'assistant','content':json.dumps(obj,ensure_ascii=False)}]
                        self.history=self.history[-12:]
                        if obj['strategy'] is not None and self.version!=base_version:
                            self.append_chat('策略版本已变化，本次修改建议未启用。请基于当前策略重新提问。')
                        elif obj['strategy'] is not None:
                            self.pending=(obj['strategy'],base); self.apply_ai.configure(state='normal')
                            diff='\n'.join(difflib.unified_diff(json.dumps(base,ensure_ascii=False,indent=2).splitlines(),json.dumps(obj['strategy'],ensure_ascii=False,indent=2).splitlines(),fromfile='当前规则',tofile='建议规则',lineterm=''))
                            self.append_chat('策略修改对比（“-”是原规则，“+”是建议规则）：\n'+(diff or '规则未变化')+'\n确认后可点击“应用修改并保存新版本”。')
        except queue.Empty: pass
        except Exception as e:
            self.report_callback_exception(type(e),e,e.__traceback__)
        finally:
            self.poll_after=self.after(100,self.poll)
    def destroy(self):
        poll_after=getattr(self,'poll_after',None)
        if poll_after is not None:
            try: self.after_cancel(poll_after)
            except tk.TclError: pass
            self.poll_after=None
        if hasattr(self,'progress'):
            try: self.progress.stop()
            except tk.TclError: pass
        super().destroy()
    def close(self):
        if self.running:
            self.request_cancel(); self.status.set('正在取消；待当前请求结束并保存部分报告后自动关闭……'); self.after(200,self.close_when_ready)
        else: self.destroy()
    def close_when_ready(self):
        if self.running: self.after(200,self.close_when_ready)
        else: self.destroy()
