"""Closed, versioned rule language. Never executes user/AI code."""
import copy
import json
import math
from datetime import date, timedelta

class RuleError(ValueError): pass
class MissingData(ValueError): pass

# All numeric templates use the same comparison grammar.
CATALOG = {
 'return_pct': ('区间涨跌幅', '%', {'days': 5}, '(末日收盘/前N交易日收盘-1)×100'),
 'volume_ratio': ('短长均量比', '倍', {'short': 5, 'long': 30}, '短期均量/长期均量；两个窗口都包含末日'),
 'active_weeks': ('振幅达标周数', '周', {'weeks': 8, 'amplitude': 1.0}, '完整自然周内(最高-最低)/周首开盘×100 > 阈值；不含当前自然周'),
 'price': ('收盘价', '元', {}, '所选行情复权口径下的末日收盘价'),
 'ma_ratio': ('收盘价/均线', '倍', {'days': 20}, '末日收盘/近N日平均收盘'),
 'breakout_ratio': ('收盘价/前期最高价', '倍', {'days': 20}, '末日收盘/此前N日最高价；最高价窗口不含末日'),
 'amplitude_pct': ('区间振幅', '%', {'days': 20}, '(N日最高-N日最低)/窗口首日开盘×100'),
 'net_profit': ('累计净利润', '元', {}, '最新已披露合并利润表 NETPROFIT；年初至报告期末累计，不是单季或归母净利润'),
}
OPS = ('between', '>', '>=', '<', '<=', '==')
POOLS = {'mainboard':'沪深主板', 'gem':'创业板', 'star':'科创板', 'all':'沪深A股'}
PREFIXES = {'mainboard':('600','601','603','605','000','001','002'), 'gem':('300','301'), 'star':('688','689')}

def in_pool(code, pool):
    return code.startswith(PREFIXES[pool]) if pool != 'all' else any(code.startswith(p) for ps in PREFIXES.values() for p in ps)

def number(v, label):
    if type(v) not in (int,float) or not math.isfinite(v): raise RuleError(f'{label}必须是有限数字')
    return v

def validate(strategy):
    s = copy.deepcopy(strategy)
    if not isinstance(s,dict) or set(s) != {'schema_version','name','pool','exclude_st','rule'}: raise RuleError('策略字段必须是 schema_version/name/pool/exclude_st/rule')
    if type(s['schema_version']) is not int or s['schema_version'] != 1: raise RuleError('不支持的规则版本')
    if not isinstance(s['name'],str) or not 1 <= len(s['name'].strip()) <= 80: raise RuleError('名称应为1～80字')
    if not isinstance(s['pool'],str) or s['pool'] not in POOLS or type(s['exclude_st']) is not bool: raise RuleError('股票范围或ST设置无效')
    count = [0]
    def walk(r, depth=0):
        if not isinstance(r,dict) or depth > 4: raise RuleError('条件结构无效或嵌套超过4层')
        count[0] += 1
        if count[0] > 80: raise RuleError('条件过多')
        if set(r) in ({'all'},{'any'}):
            children = next(iter(r.values()))
            if not isinstance(children,list) or not children: raise RuleError('条件组不能为空')
            for child in children: walk(child,depth+1)
            return
        if set(r) != {'metric','params','op','value'}: raise RuleError('条件字段必须为 metric/params/op/value')
        m = r['metric']
        if not isinstance(m,str) or m not in CATALOG: raise RuleError(f'不支持指标：{m}')
        params = r['params']
        if not isinstance(params,dict) or set(params) != set(CATALOG[m][2]): raise RuleError(f'{m}参数不匹配')
        for key,v in params.items():
            number(v,key)
            if key == 'amplitude':
                if not 0 <= v <= 1000: raise RuleError('振幅阈值应在0～1000%')
            elif type(v) is not int or not 1 <= v <= (52 if key=='weeks' else 250): raise RuleError('周期必须为1～250交易日或1～52周整数')
        if m == 'volume_ratio' and params['short'] > params['long']: raise RuleError('短周期不能超过长周期')
        if r['op'] not in OPS: raise RuleError('比较方式无效')
        vals = r['value'] if r['op']=='between' else [r['value']]
        if not isinstance(vals,list) or len(vals) != (2 if r['op']=='between' else 1): raise RuleError('区间必须为两个数字')
        for v in vals: number(v,'阈值')
        if r['op']=='between' and vals[0]>vals[1]: raise RuleError('区间下限不能大于上限')
        if m=='active_weeks' and any(type(v) is not int or not 0<=v<=params['weeks'] for v in vals): raise RuleError('达标周数必须是观察周数以内的整数')
    walk(s['rule'])
    # Detect incompatible constraints on identical metrics in a direct AND group.
    def conflicts(r):
        if 'all' in r:
            bounds={}
            for c in r['all']:
                conflicts(c)
                if 'metric' not in c: continue
                key=json.dumps([c['metric'],c['params']],sort_keys=True)
                lo,li,hi,hi_i=bounds.get(key,(-math.inf,True,math.inf,True))
                op,v=c['op'],c['value']
                a,b=(v if op=='between' else (v,v))
                if op in ('between','>','>=','=='):
                    inc=op!='>'
                    if a>lo: lo,li=a,inc
                    elif a==lo: li=li and inc
                if op in ('between','<','<=','=='):
                    inc=op!='<'
                    if b<hi: hi,hi_i=b,inc
                    elif b==hi: hi_i=hi_i and inc
                if lo>hi or (lo==hi and not(li and hi_i)): raise RuleError('同一指标的条件相互冲突')
                bounds[key]=(lo,li,hi,hi_i)
        elif 'any' in r:
            for c in r['any']: conflicts(c)
    conflicts(s['rule'])
    return s

def leaves(r):
    if 'metric' in r: return [r]
    return [x for c in next(iter(r.values())) for x in leaves(c)]

def required_bars(s):
    n=2
    for r in leaves(s['rule']):
        p=r['params']; m=r['metric']
        need=(p['weeks']*5+15 if m=='active_weeks' else p['long'] if m=='volume_ratio' else p.get('days',1)+(1 if m in ('return_pct','breakout_ratio') else 0))
        n=max(n,need)
    return n

def describe(r):
    if 'metric' not in r:
        key=next(iter(r)); return (' 且 ' if key=='all' else ' 或 ').join('('+describe(c)+')' for c in r[key])
    title,unit,_,_=CATALOG[r['metric']]
    return f"{title} {json.dumps(r['params'],ensure_ascii=False)} {r['op']} {r['value']} {unit}"

def compute(r,bars,financial,asof):
    p=r['params']; m=r['metric']
    if m=='net_profit':
        v=financial.get('net_profit')
        if v is None: raise MissingData('缺少净利润')
        return number(v,'净利润')
    if not bars: raise MissingData('无已完成日线')
    close=bars[-1]['close']
    def window(n):
        if len(bars)<n: raise MissingData(f'需要{n}条日线，仅有{len(bars)}条')
        return bars[-n:]
    def ratio(a,b):
        if b<=0: raise MissingData('分母为零或负数')
        return a/b
    if m=='price': return close
    if m=='return_pct': return (ratio(close,window(p['days']+1)[0]['close'])-1)*100
    if m=='volume_ratio':
        return ratio(sum(x['volume'] for x in window(p['short']))/p['short'],sum(x['volume'] for x in window(p['long']))/p['long'])
    if m=='ma_ratio': return ratio(close,sum(x['close'] for x in window(p['days']))/p['days'])
    if m=='breakout_ratio': return ratio(close,max(x['high'] for x in window(p['days']+1)[:-1]))
    if m=='amplitude_pct':
        w=window(p['days']); return ratio(max(x['high'] for x in w)-min(x['low'] for x in w),w[0]['open'])*100
    if m=='active_weeks':
        monday=asof-timedelta(days=asof.weekday()); start=monday-timedelta(weeks=p['weeks'])
        if date.fromisoformat(bars[0]['date'])>start: raise MissingData('历史不足以覆盖完整周窗口')
        groups={}
        for b in bars:
            d=date.fromisoformat(b['date'])
            if start<=d<monday:
                k=d-timedelta(days=d.weekday()); groups.setdefault(k,[]).append(b)
        # A no-trading week has no amplitude; never counted as passing.
        return sum(ratio(max(b['high'] for b in g)-min(b['low'] for b in g),g[0]['open'])*100>p['amplitude'] for g in groups.values())
    raise RuleError(m)

def evaluate(s,bars,financial,asof):
    details=[]
    def walk(r):
        if 'metric' not in r:
            key=next(iter(r)); vals=[walk(c) for c in r[key]]
            if key=='all': return False if False in vals else None if None in vals else True
            return True if True in vals else None if None in vals else False
        try:
            x=compute(r,bars,financial,asof); op=r['op']; v=r['value']
            passed= v[0]<=x<=v[1] if op=='between' else {'>':lambda:x>v,'>=':lambda:x>=v,'<':lambda:x<v,'<=':lambda:x<=v,'==':lambda:x==v}[op]()
            details.append({'condition':describe(r),'value':x,'pass':passed}); return passed
        except MissingData as e:
            details.append({'condition':describe(r),'value':None,'pass':None,'reason':str(e)}); return None
    result=walk(s['rule'])
    return result,details

def template(metric='return_pct'):
    return {'metric':metric,'params':copy.deepcopy(CATALOG[metric][2]),'op':'>','value':0}

def presets():
    def strategy(name,lo,hi,vol,op='<' ,weeks=8,need=6,amp=1.0,price=None):
        rules=[{'metric':'return_pct','params':{'days':5},'op':'between','value':[lo,hi]},
          {'metric':'volume_ratio','params':{'short':5,'long':30},'op':op,'value':vol},
          {'metric':'active_weeks','params':{'weeks':weeks,'amplitude':amp},'op':'>=','value':need},
          {'metric':'net_profit','params':{},'op':'>','value':0}]
        if price is not None: rules.append({'metric':'price','params':{},'op':'>=','value':price})
        return validate({'schema_version':1,'name':name,'pool':'mainboard','exclude_st':False,'rule':{'all':rules}})
    return [strategy('缩量回调',-8,-2,.6),strategy('放量突破',3,15,1.2,'>'),strategy('放量突破加强版',3,15,1.2,'>',price=20),strategy('强势突破',-5,99,1.2,'>',2,2,3.0),strategy('深跌反弹',-15,-8,.4),strategy('横盘潜伏',-3,3,.4,need=8)]
