"""Provider adapters. Errors are explicit; source switching is never silent."""
import json
import math
import threading
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta, timezone, date
from .rules import in_pool, MissingData

CST=timezone(timedelta(hours=8))
SOURCES=('tencent','eastmoney','sina')
DEFAULT_SOURCE='tencent'
EASTMONEY_TOKEN='fa5fd1943c7b386f172d6893dbfba10b'
LIST_ENDPOINT='https://push2delay.eastmoney.com/api/qt/clist/get'
TENCENT_LIST_ENDPOINT='https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList'
TENCENT_LIST_PAGE_SIZE=200
TENCENT_ENDPOINT='https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get'
class FetchError(RuntimeError): pass
class SourceUnavailable(FetchError): pass
class Cancelled(RuntimeError): pass

def completed_cutoff(now=None):
    now=now or datetime.now(CST)
    now=now.astimezone(CST)
    return now.date() if now.hour>=16 else now.date()-timedelta(days=1)

def completed_closes(bars,cutoff):
    """Completed rows no later than cutoff. Used by the backtest anchor lookup."""
    return [b for b in bars if date.fromisoformat(b['date'])<=cutoff]

def normalize_bars(rows,cutoff):
    result={}
    for row in rows:
        try:
            d=str(row.get('date',row.get('day','')))[:10]
            dt=date.fromisoformat(d)
            if dt>cutoff: continue
            b={'date':d,**{k:float(row[k]) for k in ('open','close','high','low','volume')}}
            if any(not math.isfinite(b[k]) for k in ('open','close','high','low','volume')): raise ValueError('非有限数字')
            if min(b[k] for k in ('open','close','high','low'))<=0 or b['volume']<0: raise ValueError('价格或量无效')
            if b['low']>min(b['open'],b['close']) or b['high']<max(b['open'],b['close']): raise ValueError('高低价不一致')
            if d in result and result[d]!=b: raise ValueError('日期重复且内容不同')
            result[d]=b
        except (KeyError,ValueError,TypeError) as e: raise FetchError(f'日线结构校验失败：{e}') from e
    if not result: raise MissingData('没有可用的已完成日线')
    return [result[d] for d in sorted(result)]

class Http:
    def __init__(self,cancel=None):
        self.cancel=cancel or threading.Event(); self.lock=threading.Lock(); self.last=0.0
        self.blocked=None
    def json(self,url,params=None):
        if params: url+='?'+urllib.parse.urlencode(params)
        err=None
        for attempt in range(2):
            if self.cancel.is_set(): raise Cancelled()
            if self.blocked: raise SourceUnavailable(self.blocked)
            with self.lock:
                # Public APIs are not bulk-download services. Pace even when
                # multiple worker threads are active, and stop on access blocks.
                delay=max(0,.8-(time.monotonic()-self.last))
                if self.cancel.wait(delay): raise Cancelled()
                if self.blocked: raise SourceUnavailable(self.blocked)
                self.last=time.monotonic()
            try:
                host=urllib.parse.urlsplit(url).hostname or ''
                referer='https://gu.qq.com/' if host.endswith(('.gtimg.cn','.qq.com')) else 'https://finance.sina.com.cn/' if 'sina.' in host else 'https://quote.eastmoney.com/'
                req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36','Referer':referer})
                with urllib.request.urlopen(req,timeout=12) as response:
                    raw=response.read(8_000_001)
                if len(raw)>8_000_000: raise FetchError('响应过大')
                return json.loads(raw)
            except (OSError,ValueError) as e:
                err=e
                if isinstance(e,urllib.error.HTTPError) and e.code in (403,429,501):
                    e.close()
                    self.blocked=f'{urllib.parse.urlsplit(url).hostname} HTTP {e.code}：上游拒绝请求或触发访问保护，已停止本次扫描；稍后重试'
                    raise SourceUnavailable(self.blocked) from e
                if self.cancel.wait(.5*(attempt+1)): raise Cancelled()
        if isinstance(err,urllib.error.HTTPError):
            detail=f'HTTP {err.code} {err.reason}'
        elif isinstance(err,urllib.error.URLError):
            detail=f'连接错误：{err.reason}'
        elif type(err).__name__=='RemoteDisconnected':
            detail='服务器主动断开连接（未返回 HTTP 响应）'
        elif isinstance(err,ValueError):
            detail='响应不是有效 JSON'
        else: detail=f'{type(err).__name__}: {err}'
        raise FetchError(f'{urllib.parse.urlparse(url).hostname} 请求失败：{detail}') from err


def parse_tencent_bars(data,symbol,cutoff,index=False):
    if not isinstance(data,dict) or data.get('code')!=0:
        raise FetchError('腾讯日线接口返回业务错误')
    body=data.get('data')
    if not isinstance(body,dict) or not isinstance(body.get(symbol),dict):
        raise FetchError('腾讯日线响应缺少目标证券')
    item=body[symbol]
    # Indices have no corporate-action adjustment and use day. Stocks must
    # have the explicitly forward-adjusted key; never substitute raw day.
    rows=item.get('day') if index else item.get('qfqday')
    if rows is None:
        if not index and 'day' in item:
            raise MissingData('腾讯未提供明确标记的前复权日线；未使用未复权价格代替')
        raise MissingData('腾讯未返回可用日线')
    if not isinstance(rows,list): raise FetchError('腾讯日线不是数组')
    normalized=[]
    for row in rows:
        if not isinstance(row,list) or len(row)<6: raise FetchError('腾讯日线列数不足')
        normalized.append(dict(zip(('date','open','close','high','low','volume'),row[:6])))
    return normalize_bars(normalized,cutoff)

def parse_financial(data,cutoff):
    if not isinstance(data,dict): raise FetchError('财务响应不是对象')
    if data.get('success') is False: raise FetchError('财务接口返回失败')
    result=data.get('result') or {}
    if not isinstance(result,dict): raise FetchError('财务结果结构无效')
    rows=result.get('data') or []
    if not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows): raise FetchError('财务明细结构无效')
    for r in sorted(rows,key=lambda x:str(x.get('REPORT_DATE','')),reverse=True):
        # Missing publication date is not allowed to pass as current financial evidence.
        period=str(r.get('REPORT_DATE',''))[:10]; notice=str(r.get('NOTICE_DATE',''))[:10]
        try:
            if date.fromisoformat(period)>cutoff or date.fromisoformat(notice)>cutoff: continue
            if r.get('NETPROFIT') in (None,'','-'): continue
            value=float(r['NETPROFIT'])
            if not math.isfinite(value): continue
        except (TypeError,ValueError): continue
        return {'net_profit':value,'report_date':period,'notice_date':notice,'source_field':'RPT_F10_FINANCE_GINCOME.NETPROFIT','unit':'CNY'}
    return {}

def parse_tencent_stock_page(data,expected_offset):
    """Read identities only. Ranking quote fields must never feed calculations."""
    if not isinstance(data,dict) or type(data.get('code')) is not int or data['code']!=0:
        raise FetchError('腾讯股票名单接口返回业务错误')
    body=data.get('data')
    if not isinstance(body,dict): raise FetchError('腾讯股票名单缺少 data')
    total=body.get('total'); offset=body.get('offset'); rows=body.get('rank_list')
    if type(total) is not int or not 0<total<=20000:
        raise FetchError('腾讯股票名单总数无效')
    if type(offset) is not int or offset!=expected_offset:
        raise FetchError('腾讯股票名单分页偏移不一致')
    if not isinstance(rows,list): raise FetchError('腾讯股票名单不是数组')
    out=[]
    for row in rows:
        if not isinstance(row,dict): raise FetchError('腾讯股票名单行结构无效')
        symbol=row.get('code'); name=row.get('name')
        if not isinstance(symbol,str) or len(symbol)!=8 or symbol[:2] not in ('sh','sz','bj') or not symbol[2:].isascii() or not symbol[2:].isdigit():
            raise FetchError('腾讯股票名单证券代码无效')
        if not isinstance(name,str) or not name.strip(): raise FetchError('腾讯股票名单名称缺失')
        if (symbol.startswith('sh') and not symbol[2:].startswith(('6','9'))) or (symbol.startswith('sz') and not symbol[2:].startswith(('0','2','3'))):
            raise FetchError('腾讯股票名单代码与交易所不一致')
        out.append({'symbol':symbol,'code':symbol[2:],'name':name.strip()})
    return total,out

class LiveProvider:
    demo=False
    def __init__(self,source=DEFAULT_SOURCE,cancel=None):
        if source not in SOURCES: raise ValueError('未知数据源')
        self.source=source; self.http=Http(cancel)
        title={'tencent':'腾讯前复权日线','eastmoney':'东方财富前复权日线','sina':'新浪未复权日线'}[source]
        self.label=title+('＋腾讯股票名单＋东方财富利润表' if source=='tencent' else '＋东方财富股票名单（延迟节点）及利润表')
        self.metadata={'provider':source,'adjustment':'none' if source=='sina' else 'qfq',
                       'universe_provider':'tencent' if source=='tencent' else 'eastmoney',
                       'universe_endpoint':TENCENT_LIST_ENDPOINT if source=='tencent' else LIST_ENDPOINT,
                       'universe_fields':['code','name'] if source=='tencent' else ['f12','f14'],
                       'financial_field':'RPT_F10_FINANCE_GINCOME.NETPROFIT'}
        if source=='tencent': self.metadata['bars_endpoint']=TENCENT_ENDPOINT
    def stocks(self,pool):
        if self.source=='tencent': return self._tencent_stocks(pool)
        # A fresh, paginated universe on each run; no stale permanent code cache.
        seen={}; total=None
        for page in range(1,151):
            data=self.http.json(LIST_ENDPOINT,{'pn':page,'pz':100,'po':1,'np':1,'fid':'f12','fields':'f12,f14','fs':'m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23','ut':EASTMONEY_TOKEN})
            if not isinstance(data,dict) or data.get('rc',0)!=0: raise FetchError('股票列表接口返回业务错误')
            d=data.get('data')
            if not isinstance(d,dict) or 'total' not in d: raise FetchError('股票列表缺少总数，无法确认完整性')
            if type(d['total']) is not int or d['total']<=0: raise FetchError('股票列表总数无效')
            if total is None: total=int(d['total'])
            elif total!=int(d['total']): raise FetchError('分页期间股票总数变化，请重试')
            rows=d.get('diff') or []
            if isinstance(rows,dict): rows=list(rows.values())
            if not isinstance(rows,list): raise FetchError('股票列表不是数组')
            before=len(seen)
            for r in rows:
                if not isinstance(r,dict): raise FetchError('股票列表字段无效')
                c=str(r.get('f12','')); name=str(r.get('f14','')).strip()
                if len(c)!=6 or not c.isdigit() or not name: raise FetchError('股票列表字段无效')
                if c in seen: raise FetchError('股票分页包含重复代码；停止，避免漏筛')
                seen[c]={'code':c,'name':name}
            if len(seen)>=total: break
            if len(seen)==before: raise FetchError('股票分页重复或提前结束；停止，避免漏筛')
        if total is None or len(seen)!=total: raise FetchError('股票列表未完整获取')
        out=[r for c,r in sorted(seen.items()) if in_pool(c,pool)]
        if not out: raise FetchError('目标股票池为空')
        self.metadata.update(universe_total=total,universe_pages=page,pool=pool,pool_total=len(out))
        return out
    def _tencent_stocks(self,pool):
        # The public endpoint rejects code sorting, despite the web UI listing it.
        # Price order can move intraday: never deduplicate-and-return a short pool.
        params={'_appver':'11.17.0','board_code':'aStock','sort_type':'price','direct':'down',
                'offset':0,'count':TENCENT_LIST_PAGE_SIZE}
        seen={}; total=None; offset=0; pages=0
        while total is None or offset<total:
            if pages>=100: raise FetchError('腾讯股票名单超过分页上限，停止避免不完整名单')
            params['offset']=offset
            page_total,rows=parse_tencent_stock_page(self.http.json(TENCENT_LIST_ENDPOINT,dict(params)),offset)
            if total is None: total=page_total
            elif total!=page_total: raise FetchError('腾讯名单分页期间总数变化，请稍后重试')
            expected=min(TENCENT_LIST_PAGE_SIZE,total-offset)
            if len(rows)!=expected:
                raise FetchError(f'腾讯名单分页提前结束或返回数量异常：期望{expected}，实际{len(rows)}')
            for row in rows:
                code=row['code']
                if code in seen:
                    raise FetchError('腾讯名单分页出现重复代码（盘中排名可能变化），已停止避免漏筛；请稍后或收盘后重试')
                seen[code]=row
            offset+=len(rows); pages+=1
        # Verify count once more after the pages; an error never falls back to EM.
        check={**params,'offset':0,'count':1}
        end_total,end_rows=parse_tencent_stock_page(self.http.json(TENCENT_LIST_ENDPOINT,check),0)
        if end_total!=total or len(end_rows)!=1 or end_rows[0]['code'] not in seen or len(seen)!=total:
            raise FetchError('腾讯名单完整性复核失败，请重试')
        out=[{'code':code,'name':row['name']} for code,row in sorted(seen.items())
             if row['symbol'][:2] in ('sh','sz') and in_pool(code,pool)]
        if not out: raise FetchError('腾讯名单中的目标股票池为空')
        self.metadata.update(universe_total=total,universe_pages=pages,universe_count_rechecked=True,
                             universe_sort='price/down',pool=pool,pool_total=len(out))
        return out
    def bars(self,code,days,cutoff,index=False):
        if self.source=='tencent':
            symbol=('sh' if index or code.startswith('6') else 'sz')+code
            data=self.http.json(TENCENT_ENDPOINT,
                                {'param':f'{symbol},day,,,{min(800,days+20)},qfq'})
            return parse_tencent_bars(data,symbol,cutoff,index)
        if self.source=='eastmoney':
            return self._eastmoney_bars(code,min(800,days+20),cutoff,index)
        else:
            symbol=('sh' if index or code.startswith('6') else 'sz')+code
            rows=self.http.json('https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData',{'symbol':symbol,'scale':240,'ma':'no','datalen':min(800,days+20)})
            if not isinstance(rows,list): raise FetchError('新浪未返回日线数组')
        return normalize_bars(rows,cutoff)

    def _eastmoney_bars(self,code,limit,cutoff,index=False):
        market='1' if index or code.startswith('6') else '0'
        # This endpoint ignores lmt when beg=0 and may send decades of data.
        # Old forward-adjusted prices can be negative; only the requested
        # recent series is relevant to screening and backtesting.
        begin=cutoff-timedelta(days=max(60,limit*2+30))
        data=self.http.json('https://push2his.eastmoney.com/api/qt/stock/kline/get',
                            {'fields1':'f1,f2,f3,f4,f5,f6','fields2':'f51,f52,f53,f54,f55,f56',
                             'klt':101,'fqt':1,'beg':begin.strftime('%Y%m%d'),
                             'end':cutoff.strftime('%Y%m%d'),'lmt':limit,
                             'secid':f'{market}.{code}','ut':EASTMONEY_TOKEN})
        if not isinstance(data,dict) or data.get('rc',0)!=0: raise FetchError('东方财富日线接口返回业务错误')
        body=data.get('data')
        if not isinstance(body,dict): raise FetchError('东方财富日线接口未返回证券数据')
        lines=body.get('klines')
        if not isinstance(lines,list): raise FetchError('东方财富日线不是数组')
        rows=[]
        for line in lines[-limit:]:
            if not isinstance(line,str): raise FetchError('东方财富日线行结构无效')
            parts=line.split(',')
            if len(parts)<6: raise FetchError('东方财富日线列数不足')
            rows.append(dict(zip(('date','open','close','high','low','volume'),parts[:6])))
        return normalize_bars(rows,cutoff)

    def bars_from(self,code,start,count,cutoff=None,index=False):
        """One continuously fetched series ending at `cutoff`.

        A single request yields a single adjustment basis for every row. The
        backtest depends on this: comparing a stored old price with a newly
        fetched price can invent a move that is purely a re-computed
        forward-adjustment factor.

        Returns the newest `count` completed bars. Raises MissingData when the
        requested `start` is older than what the provider still serves, so the
        caller can report 超出可回测范围 instead of guessing.
        """
        if cutoff is None: cutoff=completed_cutoff()
        limit=min(800,max(1,int(count)))
        if self.source=='tencent':
            symbol=('sh' if index or code.startswith('6') else 'sz')+code
            data=self.http.json(TENCENT_ENDPOINT,{'param':f'{symbol},day,,,{limit},qfq'})
            bars=parse_tencent_bars(data,symbol,cutoff,index)
        elif self.source=='eastmoney':
            bars=self._eastmoney_bars(code,limit,cutoff,index)
        else:
            symbol=('sh' if index or code.startswith('6') else 'sz')+code
            rows=self.http.json('https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData',{'symbol':symbol,'scale':240,'ma':'no','datalen':limit})
            if not isinstance(rows,list): raise FetchError('新浪未返回日线数组')
            bars=normalize_bars(rows,cutoff)
        if start is not None and bars and date.fromisoformat(bars[0]['date'])>start:
            raise MissingData(f'连续序列最早只到{bars[0]["date"]}，晚于需要的{start.isoformat()}')
        return bars

    def financial(self,code,cutoff):
        data=self.http.json('https://datacenter-web.eastmoney.com/api/data/v1/get',{'reportName':'RPT_F10_FINANCE_GINCOME','columns':'SECURITY_CODE,SECURITY_NAME_ABBR,REPORT_DATE,NOTICE_DATE,NETPROFIT,PARENT_NETPROFIT','filter':f'(SECURITY_CODE="{code}")','pageSize':8,'pageNumber':1,'sortColumns':'REPORT_DATE','sortTypes':-1})
        return parse_financial(data,cutoff)
    def reference_date(self,cutoff):
        return date.fromisoformat(self.bars('000001',20,cutoff,index=True)[-1]['date'])
    def latest_date(self):
        """Newest completed trading day the provider currently serves."""
        return self.reference_date(completed_cutoff())
    def probe(self):
        cutoff=completed_cutoff(); result={}
        try:
            stocks=self.stocks('mainboard')
            result['股票列表']={'ok':True,'detail':f'{self.metadata["universe_provider"]} 名单：完整 {self.metadata["universe_pages"]} 页，共 {self.metadata["universe_total"]} 只；严格主板 {len(stocks)} 只。名单只读取代码与名称。'}
        except Exception as e: result['股票列表']={'ok':False,'detail':str(e)}
        try:
            market=self.reference_date(cutoff)
            if (cutoff-market).days>10: raise MissingData('指数日线超过10个自然日未更新')
            samples=[]
            for code in ('600000','000001'):
                bars=self.bars(code,60,cutoff)
                if len(bars)<60: raise MissingData(f'{code} 日线不足60条')
                if bars[-1]['date']!=market.isoformat(): raise MissingData(f'{code} 与指数截止日不一致')
                samples.append(f'{code}: {len(bars)}条')
            result['日线']={'ok':True,'detail':f'{self.source} · 指数/沪市/深市截止日一致 {market}；'+', '.join(samples)}
        except Exception as e:
            hint='；可在顶部明确选择 tencent（腾讯前复权）后重试' if self.source=='eastmoney' else ''
            result['日线']={'ok':False,'detail':str(e)+hint}
        try:
            financial=self.financial('600000',cutoff)
            result['净利润']={'ok':bool(financial),'detail':f'合并累计净利润（元），报告期 {financial.get("report_date","缺失")}，披露日 {financial.get("notice_date","缺失")}'}
        except Exception as e: result['净利润']={'ok':False,'detail':str(e)}
        return result

class DemoProvider:
    """Deterministic, visibly synthetic fixture. Never a live fallback."""
    demo=True; label='演示数据（人工构造，非真实行情）'; source='demo'
    def stocks(self,pool): return [{'code':'600000','name':'演示甲'},{'code':'000001','name':'演示乙'},{'code':'600002','name':'演示缺财务'}] if pool in ('mainboard','all') else []
    def reference_date(self,cutoff):
        while cutoff.weekday()>4: cutoff-=timedelta(days=1)
        return cutoff
    def latest_date(self): return self.reference_date(completed_cutoff())
    def bars(self,code,days,cutoff,index=False):
        end=self.reference_date(cutoff); dates=[]; d=end
        while len(dates)<days+30:
            if d.weekday()<5: dates.append(d)
            d-=timedelta(days=1)
        dates.reverse(); n=len(dates); out=[]
        for i,d in enumerate(dates):
            close=100 if i<n-5 else 100-(i-(n-6))
            if code=='000001': close=110
            out.append({'date':d.isoformat(),'open':close,'close':close,'high':close+2,'low':close-2,'volume':1000 if i<n-5 else 100})
        return out
    def bars_from(self,code,start,count,cutoff=None,index=False):
        """Same contract as LiveProvider.bars_from, over the synthetic fixture."""
        cutoff=completed_cutoff() if cutoff is None else cutoff
        rows=self.bars(code,max(1,int(count)),cutoff,index)
        rows=sorted(rows,key=lambda b:b['date'])[-max(1,int(count)):]
        if start is not None and rows and date.fromisoformat(rows[0]['date'])>start:
            raise MissingData(f'连续序列最早只到{rows[0]["date"]}，晚于需要的{start.isoformat()}')
        return rows
    def financial(self,code,cutoff):
        return {} if code=='600002' else {'net_profit':1000000,'report_date':f'{cutoff.year-1}-12-31','notice_date':f'{cutoff.year}-04-01','unit':'CNY'}
    def probe(self): return {'演示':{'ok':True,'detail':'人工构造的离线数据；不是联网测试'}}
