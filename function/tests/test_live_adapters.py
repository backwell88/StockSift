"""Offline regression for the failure modes found during Windows live checks."""
import io
import threading
import unittest
import urllib.error
from contextlib import redirect_stdout
from datetime import date, timedelta
from unittest.mock import patch

from stockreport.providers import (Cancelled, FetchError, Http, LiveProvider,
                                   MissingData, SourceUnavailable, DemoProvider, parse_tencent_bars)


class AdapterRegressionTests(unittest.TestCase):
    cutoff=date(2026,9,11)

    def response(self, key='qfqday', symbol='sh600000'):
        return {'code':0,'data':{symbol:{key:[
            ['2026-09-10','10','11','12','9','100',{'corporate_action':'ignored metadata'}],
            ['2026-09-11','11','12','13','10','200']]}}}

    def test_tencent_qfq_column_mapping_and_intraday_cutoff(self):
        rows=parse_tencent_bars(self.response(),'sh600000',date(2026,9,10))
        self.assertEqual(rows,[{'date':'2026-09-10','open':10.0,'close':11.0,
                               'high':12.0,'low':9.0,'volume':100.0}])

    def test_no_raw_prices_substituted_for_missing_qfq(self):
        with self.assertRaises(MissingData):
            parse_tencent_bars(self.response('day'),'sh600000',self.cutoff)

    def test_index_accepts_unadjusted_index_series(self):
        rows=parse_tencent_bars(self.response('day','sh000001'),'sh000001',self.cutoff,index=True)
        self.assertEqual(len(rows),2)

    def test_upstream_rejection_and_wrong_symbol_are_failures(self):
        for response in ({'code':-1,'msg':'unavailable'}, {'code':0,'data':{}}):
            with self.assertRaises(FetchError): parse_tencent_bars(response,'sh600000',self.cutoff)

    def test_malformed_kline_is_failure(self):
        response=self.response()
        response['data']['sh600000']['qfqday'][0]=['2026-09-10']
        with self.assertRaises(FetchError): parse_tencent_bars(response,'sh600000',self.cutoff)

    def test_tencent_requests_correct_shenzhen_symbol_and_qfq(self):
        provider=LiveProvider('tencent')
        response=self.response(symbol='sz000001')
        with patch.object(provider.http,'json',return_value=response) as request:
            provider.bars('000001',251,self.cutoff)
        self.assertEqual(request.call_args.args[1],{'param':'sz000001,day,,,271,qfq'})

    def test_failed_tencent_does_not_call_other_sources(self):
        provider=LiveProvider('tencent')
        with patch.object(provider.http,'json',side_effect=FetchError('offline')) as request:
            with self.assertRaises(FetchError): provider.bars('600000',60,self.cutoff)
        self.assertEqual(request.call_count,1)
        self.assertIn('proxy.finance.qq.com',request.call_args.args[0])

    def test_eastmoney_ignores_lmt_without_loading_old_adjusted_prices(self):
        provider=LiveProvider('eastmoney')
        first=date(2026,5,1)
        recent=[f'{(first+timedelta(days=i)).isoformat()},10,11,12,9,100' for i in range(80)]
        response={'rc':0,'data':{'klines':['1999-11-10,-1.48,-1.75,-1.44,-1.86,100',*recent]}}
        with patch.object(provider.http,'json',return_value=response) as request:
            bars=provider.bars('600000',60,date(2026,9,30))
            history=provider.bars_from('600000',first,80,date(2026,9,30))
        self.assertEqual(len(bars),80)
        self.assertEqual(history,bars)
        self.assertEqual(bars[0]['date'],first.isoformat())
        self.assertEqual(request.call_count,2)
        for call in request.call_args_list:
            self.assertNotEqual(call.args[1]['beg'],'0')
            self.assertEqual(call.args[1]['lmt'],80)

    def test_cancelled_http_does_not_open_network(self):
        event=threading.Event(); event.set()
        with patch('urllib.request.urlopen') as request:
            with self.assertRaises(Cancelled): Http(event).json('https://example.com/')
        request.assert_not_called()

    def test_http_status_retained_in_error(self):
        error=urllib.error.HTTPError('https://example.com/',502,'Bad Gateway',{},None)
        with patch('urllib.request.urlopen',side_effect=error):
            http=Http()
            with patch.object(http.cancel,'wait',return_value=False):
                with self.assertRaisesRegex(FetchError,'HTTP 502 Bad Gateway'):
                    http.json('https://example.com/')

    def test_access_block_stops_retries_and_later_requests(self):
        error=urllib.error.HTTPError('https://example.com/',501,'Not Implemented',{},None)
        with patch('urllib.request.urlopen',side_effect=error) as request:
            http=Http()
            for _ in range(2):
                with self.assertRaises(SourceUnavailable): http.json('https://example.com/')
        self.assertEqual(request.call_count,1)

    def test_access_block_marks_scan_incomplete_without_draining_pool(self):
        from stockreport.engine import screen
        from stockreport.rules import presets
        class Blocked(DemoProvider):
            def stocks(self,pool): return [{'code':f'600{i:03d}','name':'样本'} for i in range(100)]
            def bars(self,*args,**kwargs): raise SourceUnavailable('HTTP 501 WAF')
        run=screen(presets()[0],1,Blocked())
        self.assertEqual(run['status'],'未完成')
        self.assertIn('501',run['error'])
        self.assertLessEqual(len(run['results']),6)
        self.assertEqual(sum(run['counts'][k] for k in ('matched','rejected','missing','failed','cancelled')),100)

    def test_partial_duplicate_page_is_rejected(self):
        provider=LiveProvider('eastmoney')
        pages=[{'data':{'total':3,'diff':[{'f12':'600000','f14':'甲'},{'f12':'000001','f14':'乙'}]}},
               {'data':{'total':3,'diff':[{'f12':'000001','f14':'乙'},{'f12':'600001','f14':'丙'}]}}]
        with patch.object(provider.http,'json',side_effect=pages):
            with self.assertRaisesRegex(FetchError,'重复'): provider.stocks('mainboard')

    def test_changed_total_and_midway_failure_never_return_partial_list(self):
        first={'data':{'total':2,'diff':[{'f12':'600000','f14':'甲'}]}}
        for next_page in ({'data':{'total':3,'diff':[{'f12':'000001','f14':'乙'}]}},FetchError('offline')):
            provider=LiveProvider('eastmoney')
            with patch.object(provider.http,'json',side_effect=[first,next_page]):
                with self.assertRaises(FetchError): provider.stocks('mainboard')

    def test_probe_cli_exit_indicates_failure(self):
        import launch
        with patch('sys.argv',['launch.py','--probe','tencent']), patch.object(
                LiveProvider,'probe',return_value={'日线':{'ok':False,'detail':'offline'}}), redirect_stdout(io.StringIO()):
            self.assertEqual(launch.main(),1)


if __name__=='__main__': unittest.main()
