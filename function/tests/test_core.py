import copy
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date,datetime,timezone,timedelta
from pathlib import Path
from stockreport.rules import *
from stockreport.providers import *
from stockreport.storage import Store,write_report
from stockreport.engine import screen
from stockreport.ai import parse_reply

class RulesTests(unittest.TestCase):
    def setUp(self):
        self.s=presets()[0]; self.p=DemoProvider(); self.d=date(2026,9,11); self.b=self.p.bars('600000',80,self.d)
    def single(self,m,p,op,value):
        s=copy.deepcopy(self.s); s['rule']={'metric':m,'params':p,'op':op,'value':value}; return validate(s)
    def test_original_default_preserved(self):
        self.assertEqual(self.s['rule']['all'][0]['value'],[-8,-2]); self.assertFalse(self.s['exclude_st']); self.assertEqual(len(presets()),6)
    def test_mainboard_excludes_star(self):
        self.assertFalse(in_pool('688001','mainboard')); self.assertTrue(in_pool('605001','mainboard')); self.assertTrue(in_pool('301001','gem'))
    def test_missing_profit_never_passes(self):
        self.assertIsNone(evaluate(self.s,self.b,{},self.d)[0]); self.assertTrue(evaluate(self.s,self.b,{'net_profit':1},self.d)[0])
    def test_no_rounding_before_compare(self):
        self.b[-1]['close']=94.9999
        s=self.single('return_pct',{'days':5},'>=',-5)
        self.assertFalse(evaluate(s,self.b,{},self.d)[0])
    def test_comparison_inclusive_and_strict(self):
        s=self.single('price',{},'between',[95,95]); self.assertTrue(evaluate(s,self.b,{},self.d)[0])
        s['rule']['op']='>';s['rule']['value']=95;self.assertFalse(evaluate(s,self.b,{},self.d)[0])
    def test_invalid_and_injection_rejected(self):
        for field,value in [('metric','__import__("os").system("x")'),('value',float('nan')),('op','eval')]:
            s=self.single('price',{},'>',1);s['rule'][field]=value
            with self.assertRaises(RuleError): validate(s)
        s=copy.deepcopy(self.s);s['extra']='x'
        with self.assertRaises(RuleError): validate(s)
    def test_cycles_and_periods(self):
        with self.assertRaises(RuleError): self.single('volume_ratio',{'short':30,'long':5},'>',1)
        with self.assertRaises(RuleError): self.single('return_pct',{'days':True},'>',0)
        with self.assertRaises(RuleError): self.single('active_weeks',{'weeks':2,'amplitude':1},'>=',3)
        self.assertEqual(required_bars(self.single('return_pct',{'days':250},'>',0)),251)
    def test_conflicting_conditions(self):
        s=self.single('price',{},'>',10);a=s['rule'];b={**a,'op':'<=','value':10};s['rule']={'all':[a,b]}
        with self.assertRaises(RuleError): validate(s)
    def test_any_three_value_logic(self):
        s=self.single('price',{},'>',1);a=s['rule'];b={'metric':'net_profit','params':{},'op':'>','value':0}
        s['rule']={'any':[a,b]};self.assertTrue(evaluate(s,self.b,{},self.d)[0])
        s['rule']={'all':[a,b]};self.assertIsNone(evaluate(s,self.b,{},self.d)[0])
        a['value']=999; self.assertFalse(evaluate(s,self.b,{},self.d)[0])
    def test_calendar_week_not_five_rows(self):
        # Remove a trading day: remaining rows must stay in the same calendar week.
        s=self.single('active_weeks',{'weeks':2,'amplitude':1},'>=',2)
        b=[x for x in self.b if x['date']!='2026-09-02']
        self.assertTrue(evaluate(s,b,{},self.d)[0])
        for x in b:
            if x['date']>='2026-09-07': x['high']=10000
            else: x['high']=x['low']=x['open']
        self.assertFalse(evaluate(s,b,{},self.d)[0])
    def test_breakout_excludes_today_high(self):
        s=self.single('breakout_ratio',{'days':20},'>',1)
        self.b[-1]['close']=103;self.b[-1]['high']=999
        self.assertTrue(evaluate(s,self.b,{},self.d)[0])
    def test_short_history_and_zero_volume(self):
        self.assertIsNone(evaluate(self.s,self.b[-3:],{},self.d)[0])
        for b in self.b: b['volume']=0
        s=self.single('volume_ratio',{'short':5,'long':30},'<',1)
        self.assertIsNone(evaluate(s,self.b,{},self.d)[0])
    def test_ma_and_amplitude_known_values(self):
        bars=[{'date':'2026-09-10','open':10,'close':10,'high':12,'low':8,'volume':10},{'date':'2026-09-11','open':12,'close':14,'high':16,'low':11,'volume':10}]
        s=self.single('ma_ratio',{'days':2},'between',[1.16,1.17])
        self.assertTrue(evaluate(s,bars,{},self.d)[0])
        s=self.single('amplitude_pct',{'days':2},'==',80)
        self.assertTrue(evaluate(s,bars,{},self.d)[0])
    def test_negative_and_zero_profit_rejected(self):
        for v in [-100,0]: self.assertFalse(evaluate(self.s,self.b,{'net_profit':v},self.d)[0])

class ProviderTests(unittest.TestCase):
    def test_cutoff_cst(self):
        self.assertEqual(completed_cutoff(datetime(2026,9,11,7,tzinfo=timezone.utc)),date(2026,9,10))
        self.assertEqual(completed_cutoff(datetime(2026,9,11,8,tzinfo=timezone.utc)),date(2026,9,11))
    def test_validate_bars_and_sort(self):
        row={'date':'2026-09-10','open':10,'close':10,'high':11,'low':9,'volume':1}
        self.assertEqual(normalize_bars([dict(row,date='2026-09-11'),row],date(2026,9,10)),[row])
        with self.assertRaises(FetchError): normalize_bars([dict(row,high=1)],date(2026,9,11))
        with self.assertRaises(FetchError): normalize_bars([dict(row,volume=float('inf'))],date(2026,9,11))
    def test_financial_publication_and_fields(self):
        r={'REPORT_DATE':'2026-06-30','NOTICE_DATE':'2026-08-25','NETPROFIT':100}
        parsed=parse_financial({'result':{'data':[r]}},date(2026,9,11))
        self.assertEqual(parsed['net_profit'],100)
        self.assertEqual(parsed['source_field'],'RPT_F10_FINANCE_GINCOME.NETPROFIT')
        self.assertEqual(parse_financial({'result':{'data':[r]}},date(2026,8,1)),{})
        r.pop('NETPROFIT'); r['f38']=100; r['PARENT_NETPROFIT']=90
        self.assertEqual(parse_financial({'result':{'data':[r]}},date(2026,9,11)),{})
    def test_pagination_cannot_silently_truncate(self):
        p=LiveProvider('eastmoney');p.http.json=lambda *a:{'data':{'total':2,'diff':[{'f12':'600000','f14':'甲'}]}}
        with self.assertRaises(FetchError): p.stocks('mainboard')
    def test_pagination_complete_filters_market(self):
        pages=iter([{'data':{'total':2,'diff':[{'f12':'600000','f14':'甲'}]}},{'data':{'total':2,'diff':[{'f12':'688001','f14':'乙'}]}}])
        p=LiveProvider('eastmoney');p.http.json=lambda *a:next(pages)
        self.assertEqual(len(p.stocks('mainboard')),1)

class PersistenceEngineTests(unittest.TestCase):
    def setUp(self): self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()
    def test_100_iterations_rollback_and_restart(self):
        original_id,original=self.store.get()
        for i in range(100):
            s=copy.deepcopy(original);s['rule']['all'][0]['params']['days']=i+1;v=self.store.save(s)
        self.assertEqual(Store(self.tmp.name).get()[0],v)
        self.store.set_default(original_id);self.assertEqual(self.store.get()[1],original)
    def test_invalid_save_does_not_corrupt_default(self):
        old=self.store.get();s=copy.deepcopy(old[1]);s['rule']['all'][0]['value']=[5,-5]
        with self.assertRaises(RuleError): self.store.save(s)
        self.assertEqual(self.store.get(),old)
    def test_full_run_report_counts_and_snapshot(self):
        v,s=self.store.get();run=screen(s,v,DemoProvider());self.assertEqual(run['counts'],{'total':3,'matched':1,'rejected':1,'missing':1,'failed':0,'cancelled':0})
        s['name']='changed';self.assertNotEqual(run['strategy']['name'],s['name'])
        path=write_report(self.store,run);self.assertIn('演示数据',path.read_text(encoding='utf-8'));self.assertEqual(json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))['version'],v)
    def test_concurrent_report_names_and_restart(self):
        v,s=self.store.get();run=screen(s,v,DemoProvider())
        with ThreadPoolExecutor(max_workers=5) as ex: paths=list(ex.map(lambda _:write_report(self.store,run),range(10)))
        self.assertEqual(len(set(paths)),10);self.assertTrue(all(p.stat().st_size>0 for p in paths))
        p=write_report(Store(self.tmp.name),run);self.assertTrue(p.name.endswith('_011.md'))
    def test_cancel_prevents_work_and_counts(self):
        event=threading.Event();event.set();v,s=self.store.get();r=screen(s,v,DemoProvider(),event)
        self.assertEqual(r['counts']['cancelled'],3);self.assertIn('取消',r['status'])
    def test_network_failure_distinct_from_no_match(self):
        class Broken(DemoProvider):
            def bars(self,*args,**kw): raise FetchError('network unavailable')
        v,s=self.store.get();r=screen(s,v,Broken());self.assertEqual(r['counts']['failed'],3);self.assertEqual(r['counts']['matched'],0)
    def test_universe_failure_is_incomplete(self):
        class Broken(DemoProvider):
            def stocks(self,*args): raise FetchError('universe failed')
        v,s=self.store.get();r=screen(s,v,Broken());self.assertEqual(r['status'],'未完成');self.assertIn('error',r)
    def test_stale_stock_is_missing(self):
        class Stale(DemoProvider):
            def bars(self,*args,**kw): return super().bars(*args,**kw)[:-1]
        v,s=self.store.get();r=screen(s,v,Stale());self.assertEqual(r['counts']['missing'],3)
    def test_ai_rejects_code_and_invalid_schema(self):
        with self.assertRaises((ValueError,RuleError)): parse_reply('```python\nprint(1)\n```')
        with self.assertRaises(RuleError): parse_reply('{"reply":"x","strategy":{"code":"x"}}')
        self.assertIsNone(parse_reply('{"reply":"请明确周期","strategy":null}')['strategy'])
    def test_ai_complete_validated_strategy(self):
        obj=parse_reply(json.dumps({'reply':'修改','strategy':presets()[0]}));self.assertEqual(obj['strategy'],presets()[0])

if __name__=='__main__': unittest.main()
