"""Small, allowlisted public endpoint probes; no stock screening or AI calls."""
import argparse
import json
import re
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'function'))
from stockreport import __version__


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--mode',choices=['sample','list','tencent-shape','tencent-options'],default='sample')
    args=parser.parse_args()
    if sys.stdout is not None: sys.stdout.reconfigure(encoding='utf-8')
    if args.mode=='list':
        from stockreport.providers import LiveProvider
        provider=LiveProvider('tencent')
        rows=provider.stocks('all')
        result={'metadata':provider.metadata,'rows':len(rows),'samples':rows[:3]+rows[-3:]}
        print(json.dumps(result,ensure_ascii=False,indent=2))
        out=ROOT/'acceptance'; out.mkdir(exist_ok=True)
        (out/f'universe-v{__version__}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return
    cases=[
        ('eastmoney-list','https://push2delay.eastmoney.com/api/qt/clist/get',
         {'pn':1,'pz':5,'po':1,'np':1,'fid':'f12','fields':'f12,f14','fs':'m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23','ut':'fa5fd1943c7b386f172d6893dbfba10b'}),
        ('tencent-list-code','https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList',
         {'_appver':'11.17.0','board_code':'aStock','sort_type':'code','direct':'up','offset':'0','count':'5'}),
        ('tencent-list-next','https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList',
         {'_appver':'11.17.0','board_code':'aStock','sort_type':'code','direct':'up','offset':'5','count':'5'}),
        ('eastmoney-financial','https://datacenter-web.eastmoney.com/api/data/v1/get',
         {'reportName':'RPT_F10_FINANCE_GINCOME','columns':'SECURITY_CODE,SECURITY_NAME_ABBR,REPORT_DATE,NOTICE_DATE,NETPROFIT',
          'filter':'(SECURITY_CODE="600000")','pageSize':1,'pageNumber':1,'sortColumns':'REPORT_DATE','sortTypes':-1}),
    ]
    if args.mode=='tencent-shape':
        cases=[
            ('tencent-list-price','https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList',
             {'_appver':'11.17.0','board_code':'aStock','sort_type':'price','direct':'down','offset':'0','count':'5'}),
            ('tencent-legacy-code','https://stock.gtimg.cn/data/index.php',
             {'appn':'rank','t':'rankash/code','p':1,'o':0,'l':5,'v':'list_data'}),
            ('tencent-page','https://stockapp.finance.qq.com/mstats/',{}),
        ]
    if args.mode=='tencent-options':
        cases=[
            ('tencent-official-script','https://st.gtimg.com/quotes_center/assets/index.9eebe9c3.js',{}),
            ('tencent-list-large','https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList',
             {'_appver':'11.17.0','board_code':'aStock','sort_type':'price','direct':'down','offset':'0','count':'10000'}),
        ]
    results=[]
    for label,url,params in cases:
        req=urllib.request.Request(url+'?'+urllib.parse.urlencode(params),headers={
            'User-Agent':'Mozilla/5.0','Referer':'https://stockapp.finance.qq.com/' if 'qq.com' in url else 'https://quote.eastmoney.com/'})
        started=time.monotonic()
        try:
            with urllib.request.urlopen(req,timeout=12) as response:
                raw=response.read(8_000_001)
                if len(raw)>8_000_000: raise ValueError('response exceeds size bound')
            try: data=json.loads(raw)
            except (ValueError,UnicodeError):
                text=raw.decode('utf-8',errors='replace')
                data=[text[max(0,m.start()-200):m.end()+350] for m in re.finditer('getBoardRankList|sort_type|aStock',text)][:40] if label=='tencent-official-script' else text[:15000]
            if label=='tencent-list-large' and isinstance(data,dict) and isinstance(data.get('data'),dict):
                body=data['data']; rows=body.get('rank_list',[])
                data={'code':data.get('code'),'total':body.get('total'),'offset':body.get('offset'),
                      'returned':len(rows),'unique':len({r['code'] for r in rows}),
                      'first':rows[:2],'last':rows[-2:]}
            item={'name':label,'response':data}
        except Exception as e: item={'name':label,'error':type(e).__name__+': '+str(e)}
        item['seconds']=round(time.monotonic()-started,2); results.append(item)
        print(json.dumps(item,ensure_ascii=False),flush=True)
        time.sleep(.8)
    output={'time':datetime.now().astimezone().isoformat(),'results':results}
    out=ROOT/'acceptance'; out.mkdir(exist_ok=True)
    (out/f'universe-probe-{args.mode}-v{__version__}.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__': main()
