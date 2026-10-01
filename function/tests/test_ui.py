"""Run under a real desktop or virtual display. Skips honestly if none exists."""
import csv
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from unittest.mock import patch
from stockreport.storage import Store
from stockreport.ui import App, ResultsTable


class UITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        try: self.app=App(Store(self.tmp.name));self.app.update()
        except tk.TclError as e: self.tmp.cleanup();self.skipTest(str(e))
    def tearDown(self):
        if hasattr(self,'app'): self.app.destroy()
        self.tmp.cleanup()
    def test_tabs_and_edit_save(self):
        self.assertEqual(len(self.app.tabs.tabs()),6)
        self.app.name.set('UI测试');self.assertTrue(self.app.save());self.assertEqual(self.app.store.get()[1]['name'],'UI测试')
    def test_rename_keeps_saved_strategy_id_and_rule(self):
        version,before=self.app.store.get()
        self.app.name.set('未保存的草稿名称')
        with patch('stockreport.ui.messagebox.askokcancel',return_value=True),patch('stockreport.ui.simpledialog.askstring',return_value='新策略名称'):
            self.app.rename_strategy()
        after_version,after=self.app.store.get()
        self.assertEqual(after_version,version)
        self.assertEqual(len(self.app.store.versions()),1)
        self.assertEqual(after['name'],'新策略名称')
        self.assertEqual(after['rule'],before['rule'])
        self.assertEqual(self.app.name.get(),'新策略名称')
    def test_condition_picker_explains_metrics_before_selection(self):
        self.app.condition_dialog(None)
        self.app.update()
        win=next(w for w in self.app.winfo_children() if isinstance(w,tk.Toplevel))
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        widgets=list(descendants(win))
        picker=next(w for w in widgets if isinstance(w,ttk.Combobox) and any('return_pct' in v for v in w['values']))
        options=picker['values']
        self.assertTrue(any('区间涨跌幅' in v for v in options))
        self.assertTrue(any('累计净利润' in v for v in options))
        self.assertTrue(any(isinstance(w,ttk.Label) and '最近 N 天涨了或跌了多少' in w.cget('text') and w.winfo_ismapped() for w in widgets))
        picker.set('收盘价 · price')
        picker.event_generate('<<ComboboxSelected>>')
        self.app.update()
        next(w for w in widgets if isinstance(w,ttk.Button) and w.cget('text')=='应用到草稿').invoke()
        self.assertTrue(any(rule.get('metric')=='price' for rule in self.app.strategy['rule']['all']))
        if win.winfo_exists(): win.destroy()
    def test_new_install_default_and_saved_source_preserved(self):
        self.assertEqual(self.app.source.get(),'tencent')
        self.app.store.set_setting('source','eastmoney')
        self.app.destroy()
        self.app=App(Store(self.tmp.name)); self.app.update()
        self.assertEqual(self.app.source.get(),'eastmoney')
    def test_connection_results_fit_minimum_window(self):
        self.app.geometry('900x640'); self.app.tabs.select(self.app.data_tab)
        self.app.probe_text.set('本次检测来源：tencent\n股票列表：完整56页，共5558只，主板3445只。\n日线：沪市、深市和指数截止日均为2026-09-11。\n净利润：合并累计净利润，报告期2026-06-30。')
        self.app.update()
        label=self.app.data_tab.winfo_children()[-1]
        self.assertGreaterEqual(label.winfo_height(),label.winfo_reqheight())
        self.assertLessEqual(label.winfo_y()+label.winfo_height(),self.app.data_tab.winfo_height())
    def test_demo_button_and_report_view(self):
        import time
        self.app.source.set('demo');self.app.start()
        deadline=time.monotonic()+10
        while self.app.running and time.monotonic()<deadline: self.app.update();time.sleep(.02)
        self.assertFalse(self.app.running);self.assertIsNotNone(self.app.current_report)
        self.assertIn('演示数据',self.app.report_view.get('1.0','end'))
        # The CSV is written next to the Markdown and is listed for the backtest.
        self.assertTrue(self.app.current_report.with_suffix('.csv').is_file())
        self.assertEqual(len(self.app.report_table.rows),3)
        self.assertIn('已处理 3/3（100%）',self.app.progress_text.get())
        self.assertEqual(str(self.app.progress['mode']),'determinate')
        self.assertIn(self.app.current_report.name,[self.app.backtest_box['values'][i] for i in range(len(self.app.backtest_box['values']))])
    def test_screen_progress_stages_and_cancelled_state(self):
        self.app.running=True
        self.app._set_progress(0,0,'正在获取完整股票列表')
        self.assertEqual(str(self.app.progress['mode']),'indeterminate')
        self.assertIn('总数尚未取得',self.app.progress_text.get())
        self.app._set_progress(0,0,'股票列表已获取，正在确认市场日期')
        self.assertEqual(str(self.app.progress['mode']),'indeterminate')
        self.app._set_progress(2,5,'正在逐股筛选')
        self.assertEqual(str(self.app.progress['mode']),'determinate')
        self.assertIn('已处理 2/5（40%）',self.app.progress_text.get())
        self.assertEqual(float(self.app.progress['value']),40)
        self.app.request_cancel()
        self.assertIn('正在取消',self.app.progress_text.get())
        self.app._set_progress(2,5,'筛选已取消（部分结果）',active=False)
        self.assertIn('筛选已取消',self.app.progress_text.get())
        self.assertEqual(str(self.app.progress['mode']),'determinate')
    def test_api_key_is_saved_and_reloaded(self):
        self.app.key.set('sk-测试密钥-123456'); self.app.keep_key.set(True); self.app.save_settings()
        self.assertIn('已保存到本机',self.app.key_state.get())
        self.assertEqual(self.app.store.get_api_key(),'sk-测试密钥-123456')
        self.app.destroy()
        self.app=App(Store(self.tmp.name)); self.app.update()
        self.assertEqual(self.app.key.get(),'sk-测试密钥-123456')
        self.assertIn('已从本机读取已保存的密钥',self.app.key_state.get())
    def test_api_key_memory_only_when_not_kept_and_clearable(self):
        self.app.key.set('sk-临时'); self.app.keep_key.set(False); self.app.save_settings()
        self.assertEqual(self.app.store.get_api_key(),'')
        self.app.destroy()
        self.app=App(Store(self.tmp.name)); self.app.update()
        self.assertNotEqual(self.app.key.get(),'sk-临时')
        self.app.key.set('sk-要删除'); self.app.keep_key.set(True); self.app.save_settings()
        self.app.clear_key(); self.assertEqual(self.app.store.get_api_key(),''); self.assertEqual(self.app.key.get(),'')
    def test_unchecked_reports_are_not_sent(self):
        self.assertEqual(self.app.checked_reports(),[])
        self.assertIn('不会发送任何报告内容',self.app.report_hint.get())
    def test_backtest_preview_reads_the_selected_report(self):
        import time
        self.app.source.set('demo');self.app.start()
        deadline=time.monotonic()+10
        while self.app.running and time.monotonic()<deadline: self.app.update();time.sleep(.02)
        self.app.refresh_backtest_reports()
        self.app.backtest_choice.set(self.app.current_report.name)
        self.app.preview_backtest()
        deadline=time.monotonic()+10
        while '演示报告' not in self.app.backtest_hint.get() and time.monotonic()<deadline:
            self.app.update(); time.sleep(.02)
        self.assertIn('演示报告，不能回测',self.app.backtest_hint.get())
        from unittest.mock import patch
        with patch('stockreport.ui.messagebox.showerror'):
            self.app.start_backtest()
            deadline=time.monotonic()+10
            while self.app.running and time.monotonic()<deadline:
                self.app.update(); time.sleep(.02)
        self.assertFalse(self.app.running)

    def test_report_table_sorts_filters_and_exports(self):
        rows=[{'code':'600000','name':'甲','date':'2026-09-11','close':5.0,'report_date':'','net_profit':100.0,'state':'符合'},
              {'code':'000001','name':'乙','date':'2026-09-10','close':20.0,'report_date':'','net_profit':50.0,'state':'不符合'},
              {'code':'000002','name':'丙','date':'2026-09-09','close':9.0,'report_date':'','net_profit':None,'state':'符合'}]
        table=self.app.report_table; table.set_rows(rows)
        table.sort_by('close')
        self.assertEqual([r['code'] for r in table.visible()],['600000','000002','000001'])
        table.sort_by('close')
        self.assertEqual([r['code'] for r in table.visible()],['000001','000002','600000'])
        table.query.set('00'); self.assertEqual(len(table.visible()),3)
        table.query.set('乙'); self.assertEqual([r['code'] for r in table.visible()],['000001'])
        table.query.set('')
        table.status.set('符合'); self.assertEqual(len(table.visible()),2)
        table.clear_filters(); self.assertEqual(len(table.visible()),3)


class ResultsTableTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        try: self.root=tk.Tk(); self.root.withdraw()
        except tk.TclError as e: self.tmp.cleanup(); self.skipTest(str(e))
    def tearDown(self):
        if hasattr(self,'root'): self.root.destroy()
        self.tmp.cleanup()
    def test_numeric_columns_sort_as_numbers_not_text(self):
        table=ResultsTable(self.root,[('code','代码',80,'text'),('pct','涨跌幅',80,'num')],['已回测'])
        table.set_rows([{'code':'600000','pct':9.0,'state':'已回测'},{'code':'000001','pct':-1.5,'state':'已回测'},
                        {'code':'000002','pct':100.0,'state':'已回测'}])
        table.sort_by('pct')
        self.assertEqual([r['pct'] for r in table.visible()],[-1.5,9.0,100.0])
        self.assertIn('显示 3 / 共 3 行',table.summary.get())
    def test_export_visible_writes_only_filtered_rows(self):
        from stockreport.exports import write_csv
        table=ResultsTable(self.root,[('code','代码',80,'text'),('pct','涨跌幅(%)',80,'num')],['已回测'])
        table.set_rows([{'code':'600001','pct':1.25,'state':'已回测'},{'code':'600002','pct':None,'state':'无法回测'}])
        table.status.set('已回测')
        target=Path(self.tmp.name)/'out.csv'
        write_csv(target,[c[1] for c in table.columns],[[r.get(k,'') for k,_,_,_ in table.columns] for r in table.visible()])
        rows=list(csv.reader(target.read_text(encoding='utf-8-sig').splitlines()))
        self.assertEqual(rows[0],['代码','涨跌幅(%)'])
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[1],['600001','1.25'])


if __name__=='__main__': unittest.main()
