"""Offline regression cases authored during static review; not run for v1.2.1."""
import unittest
from datetime import date
from unittest.mock import patch
from stockreport import backtest as bt
from stockreport.providers import SourceUnavailable


class ReviewRegressions(unittest.TestCase):
    def report(self):
        return {'market_date':'2026-01-05','source':'新浪未复权日线＋东方财富股票名单',
                'results':[{'code':'600000','name':'甲','status':'matched','close':10}]}

    def test_source_label_resolved_without_silent_tencent_fallback(self):
        self.assertEqual(bt.recorded_source(self.report()),'sina')
        with self.assertRaises(bt.BacktestError): bt._pick_provider({'source':'未知'},None)

    def test_generated_date_fallback_is_explicit(self):
        day,note=bt.baseline_info({'generated':'2026-01-05T12:00:00+08:00'})
        self.assertEqual(day,date(2026,1,5)); self.assertTrue(note)

    def test_long_history_request_uses_elapsed_time(self):
        calls=[]
        class Provider:
            source='sina'
            def latest_date(self): return date(2026,9,18)
            def bars_from(self,code,start,count,cutoff):
                calls.append(count)
                return [{'date':'2026-01-05','close':10},{'date':'2026-09-18','close':11}]
        with patch('stockreport.backtest.completed_cutoff',return_value=date(2026,9,18)):
            run=bt.backtest(self.report(),Provider())
        self.assertGreater(calls[0],250); self.assertEqual(run['counts']['ok'],1)

    def test_market_rejection_does_not_start_individual_requests(self):
        class Provider:
            def latest_date(self): raise SourceUnavailable('HTTP 429')
            def bars_from(self,*args): raise AssertionError('must not request stocks')
        with patch('stockreport.backtest.completed_cutoff',return_value=date(2026,9,18)):
            with self.assertRaises(SourceUnavailable): bt.backtest(self.report(),Provider())
