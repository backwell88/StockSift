import copy
import threading
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime,date
from .rules import validate,required_bars,leaves,evaluate,MissingData
from .providers import completed_cutoff,Cancelled,SourceUnavailable

def screen(strategy,version,provider,cancel=None,progress=None):
    s=validate(strategy); cancel=cancel or threading.Event(); progress=progress or (lambda *a:None)
    run={'generated':datetime.now().astimezone().isoformat(),'version':version,'strategy':copy.deepcopy(s),'source':provider.label,'demo':provider.demo,'status':'完成','market_date':None,'results':[], 'counts':{k:0 for k in ('total','matched','rejected','missing','failed','cancelled')}}
    cutoff=completed_cutoff(); count=run['counts']; n=required_bars(s)
    need_fin=any(r['metric']=='net_profit' for r in leaves(s['rule']))
    try:
        progress(0,0,'正在获取完整股票列表')
        stocks=provider.stocks(s['pool']); count['total']=len(stocks)
        if not stocks: raise MissingData('目标股票池为空')
        progress(0,0,f"股票列表已获取（共 {count['total']} 只），正在确认市场日期")
        market=provider.reference_date(cutoff); run['market_date']=market.isoformat()
        if (cutoff-market).days>10: raise MissingData('指数数据超过10个自然日未更新，停止本次筛选')
        progress(0,count['total'],'正在逐股筛选')
        def one(stock):
            r={**stock}
            try:
                if cancel.is_set(): raise Cancelled()
                if s['exclude_st'] and 'ST' in stock['name'].upper(): return {**r,'status':'rejected','reason':'排除ST'}
                bars=provider.bars(stock['code'],n,cutoff)
                if cancel.is_set(): raise Cancelled()
                if date.fromisoformat(bars[-1]['date'])!=market: raise MissingData('末日与市场截止日期不一致（停牌或数据过期）')
                if bars[-1]['volume']==0: raise MissingData('末日成交量为0')
                financial={}; financial_error=None
                # Only fetch fundamentals if price rules did not already reject.
                preliminary,_=evaluate(s,bars,{},market)
                if need_fin and preliminary is not False:
                    try: financial=provider.financial(stock['code'],cutoff)
                    except (Cancelled,SourceUnavailable): raise
                    except Exception as e: financial_error=str(e)
                answer,details=evaluate(s,bars,financial,market)
                status='matched' if answer is True else 'rejected' if answer is False else 'failed' if financial_error else 'missing'
                return {**r,'status':status,'date':bars[-1]['date'],'close':bars[-1]['close'],'financial':financial,'details':details,'bars':bars,'reason':financial_error or '; '.join(d.get('reason','') for d in details if d['pass'] is None)}
            except Cancelled: return {**r,'status':'cancelled','reason':'已取消'}
            except SourceUnavailable as e: return {**r,'status':'failed','reason':str(e),'source_unavailable':True}
            except MissingData as e: return {**r,'status':'missing','reason':str(e)}
            except Exception as e: return {**r,'status':'failed','reason':str(e)}
        # Bounded queue: cancel does not leave thousands of queued requests.
        iterator=iter(stocks); pending=set(); source_failure=None
        with ThreadPoolExecutor(max_workers=6) as executor:
            def fill():
                while len(pending)<6 and not cancel.is_set() and source_failure is None:
                    try: stock=next(iterator)
                    except StopIteration: break
                    pending.add(executor.submit(one,stock))
            fill()
            while pending:
                done,pending=wait(pending,timeout=.2,return_when=FIRST_COMPLETED)
                for future in done:
                    r=future.result(); run['results'].append(r); count[r['status']]+=1
                    if r.get('source_unavailable'): source_failure=r['reason']
                    progress(len(run['results']),count['total'],f"符合 {count['matched']} · 数据不足 {count['missing']} · 失败 {count['failed']}")
                fill()
        count['cancelled']+=count['total']-sum(count[k] for k in ('matched','rejected','missing','failed','cancelled'))
        if source_failure:
            run['status']='未完成'; run['error']=source_failure
        elif cancel.is_set(): run['status']='已取消（部分结果）'
        elif count['failed'] or count['missing']: run['status']='完成（含失败或数据不足）'
    except Cancelled: run['status']='已取消（部分结果）'
    except Exception as e: run['status']='未完成'; run['error']=str(e)
    count['cancelled']=count['total']-sum(count[k] for k in ('matched','rejected','missing','failed'))
    run['provider_metadata']=copy.deepcopy(getattr(provider,'metadata',{}))
    run['results'].sort(key=lambda x:x['code'])
    return run
