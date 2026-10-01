"""Offline cases for Tencent universe completeness. Authored, not run this release."""
import unittest
from unittest.mock import patch
from stockreport.providers import LiveProvider,FetchError,TENCENT_LIST_ENDPOINT,parse_tencent_stock_page


def page(offset,total,symbols):
    return {'code':0,'data':{'offset':offset,'total':total,
            'rank_list':[{'code':s,'name':'股票'+s,'zxj':'123.45'} for s in symbols]}}


class TencentUniverseTests(unittest.TestCase):
    def run_pages(self,responses,pool='mainboard'):
        provider=LiveProvider('tencent')
        with patch('stockreport.providers.TENCENT_LIST_PAGE_SIZE',2),patch.object(provider.http,'json',side_effect=responses) as request:
            stocks=provider.stocks(pool)
        self.assertTrue(all(c.args[0]==TENCENT_LIST_ENDPOINT for c in request.call_args_list))
        return stocks,provider

    def test_complete_pages_rechecked_and_only_names_used(self):
        rows,provider=self.run_pages([page(0,3,['sh600000','sz000001']),page(2,3,['sh688001']),page(0,3,['sh600000'])])
        self.assertEqual([r['code'] for r in rows],['000001','600000'])
        self.assertTrue(all(set(r)=={'code','name'} for r in rows))
        self.assertEqual(provider.metadata['universe_provider'],'tencent')
        self.assertTrue(provider.metadata['universe_count_rechecked'])

    def test_duplicate_page_is_error_not_silent_deduplication(self):
        with self.assertRaisesRegex(FetchError,'重复'):
            self.run_pages([page(0,3,['sh600000','sz000001']),page(2,3,['sh600000'])])

    def test_short_page_and_changed_total_are_errors(self):
        for tail in (page(2,3,[]),page(2,4,['sh688001','sz300001'])):
            with self.assertRaises(FetchError):
                self.run_pages([page(0,3,['sh600000','sz000001']),tail])

    def test_final_count_change_rejects_result(self):
        with self.assertRaises(FetchError):
            self.run_pages([page(0,2,['sh600000','sz000001']),page(0,3,['sh600000'])])

    def test_business_error_and_wrong_offset_rejected(self):
        for data in ({'code':123,'msg':'sort_type error'},page(2,2,['sh600000','sz000001'])):
            with self.assertRaises(FetchError): parse_tencent_stock_page(data,0)

    def test_no_eastmoney_fallback_on_tencent_failure(self):
        provider=LiveProvider('tencent')
        with patch.object(provider.http,'json',side_effect=FetchError('unavailable')) as request:
            with self.assertRaises(FetchError): provider.stocks('mainboard')
        self.assertEqual(request.call_count,1)
        self.assertEqual(request.call_args.args[0],TENCENT_LIST_ENDPOINT)
