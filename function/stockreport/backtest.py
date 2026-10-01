"""Backtest a past screening report against freshly fetched prices.

Design notes that matter for correctness:

* The baseline price and the latest price are read from **one** continuously
  fetched series.  Forward-adjusted (qfq) history is recomputed whenever a new
  corporate action happens, so mixing "the price recorded back then" with "the
  price fetched now" can show a fake move.  A single series has one adjustment
  basis by construction.
* Missing data is never filled with 0 or with the old price; those rows are
  reported as 无法回测 with a reason.
* A demo report is refused outright: backtesting synthetic fixtures against
  real quotes would be meaningless.
"""
import copy
import json
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import date, datetime
from pathlib import Path

from .exports import number, write_csv
from .providers import Cancelled, SourceUnavailable, completed_cutoff, SOURCES
from .rules import MissingData

BACKTEST_COLUMNS = ('股票代码', '名称', '入选日期', '当时收盘价(报告记录)', '回测基准价', '最新价',
                    '最新数据日期', '涨跌额', '涨跌幅(%)', '持有交易日数', '数据来源', '基准价来源', '状态', '涨跌比例(原始值)', '口径说明')

SOURCE_JSON = '报告JSON（精确）'
SOURCE_MD = 'Markdown表格（近似值）'

# The provider caps a single request at 800 daily bars (~3 years of trading).
MAX_SERIES = 800


class BacktestError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Reading a past report
# --------------------------------------------------------------------------

def find_sidecar(path):
    """Return the .json audit sidecar for a report path, if it exists."""
    path = Path(path)
    side = path.with_suffix('.json')
    return side if side.is_file() else None


def parse_json_report(path):
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as e:
        raise BacktestError(f'报告 JSON 无法解析：{type(e).__name__}') from e
    if not isinstance(data, dict) or not isinstance(data.get('results'), list) or any(not isinstance(r, dict) for r in data['results']):
        raise BacktestError('报告 JSON 结构不符合预期（缺少 results 列表）')
    if 'baseline_date' in data and 'strategy' not in data:
        raise BacktestError('请选择原始选股报告，不能把回测结果再次作为选股报告')
    return data


def _rows_from_markdown(text):
    """Extract the 入选股票 table from a Markdown report.

    Reported values are taken verbatim; the Markdown cell is a rounded display
    value, which is why the result is labelled as an approximation.
    """
    rows = []
    in_table = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('| 代码 ') or stripped.startswith('| 代码|'):
            in_table = True
            continue
        if not in_table:
            continue
        if not stripped.startswith('|'):
            break
        cells = [c.strip() for c in stripped.strip('|').split('|')]
        if len(cells) < 4:
            break
        if set(cells[0]) <= set('-: '):
            continue
        if len(cells[0]) != 6 or not cells[0].isdigit():
            break
        entry = {'code': cells[0], 'name': cells[1], 'baseline_date': cells[2], 'close': cells[3]}
        for label, key in (('财报期', 'report_date'), ('净利润', 'net_profit')):
            if len(cells) >= 6:
                value = cells[4] if label == '财报期' else cells[5]
                entry[key] = value
        rows.append(entry)
    return rows


def parse_markdown_report(path):
    path = Path(path)
    try:
        text = path.read_text(encoding='utf-8-sig')
    except (OSError, UnicodeError) as e:
        raise BacktestError(f'报告无法读取：{type(e).__name__}') from e
    data = {'results': [], 'demo': False, 'status': '', 'source': '', 'market_date': None}
    for line in text.splitlines():
        if line.startswith('生成时间：'):
            data['generated'] = line.split('：', 1)[1].strip()
        elif line.startswith('行情截止：'):
            value = line.split('：', 1)[1].strip()
            data['market_date'] = None if value in ('', '未取得') else value
        elif line.startswith('数据源：'):
            data['source'] = line.split('：', 1)[1].strip()
        elif line.startswith('状态：'):
            data['status'] = line.split('：', 1)[1].strip()
        elif line.startswith('模式：') and '演示数据' in line:
            data['demo'] = True
    for row in _rows_from_markdown(text):
        close = None
        try:
            close = float(row['close'])
        except (TypeError, ValueError):
            close = None
        data['results'].append({
            'code': row['code'],
            'name': row['name'],
            'status': 'matched',
            'date': row['baseline_date'],
            'close': close,
            'financial': {'report_date': row.get('report_date', ''), 'net_profit': row.get('net_profit', '')},
        })
    if not data['results']:
        raise BacktestError('报告中没有解析到入选股票表格（可能不是本程序生成的报告）')
    return data


def load_report(path):
    """Load a past report, preferring the precise JSON sidecar over the Markdown."""
    path = Path(path)
    if path.suffix.lower() not in ('.md', '.json'):
        raise BacktestError('请选择 Markdown 或 JSON 选股报告')
    if not path.is_file():
        raise BacktestError(f'报告文件不存在：{path.name}')
    side = find_sidecar(path) if path.suffix.lower() == '.md' else None
    if path.suffix.lower() == '.json' or side:
        source = side if side else path
        try:
            data = parse_json_report(source)
            data['_basis'] = SOURCE_JSON
            data['_path'] = path
            data['_json_path'] = source
            return data
        except BacktestError:
            if path.suffix.lower() == '.json':
                if not path.with_suffix('.md').is_file():
                    raise
                path = path.with_suffix('.md')
    data = parse_markdown_report(path)
    data['_basis'] = SOURCE_MD
    data['_path'] = path
    data['_json_path'] = None
    return data


def matched_stocks(report):
    """Return the 入选股票 rows of a parsed report, in code order."""
    out = []
    seen = set()
    for r in report.get('results') or []:
        if r.get('status') != 'matched':
            continue
        code = str(r.get('code', ''))
        if len(code) != 6 or not code.isdigit():
            raise BacktestError('入选股票代码无效')
        if code in seen:
            raise BacktestError(f'报告包含重复入选股票：{code}')
        seen.add(code)
        close = r.get('close')
        try:
            close = None if close in (None, '') else float(close)
        except (TypeError, ValueError):
            close = None
        if close is not None and not math.isfinite(close):
            close = None
        out.append({'code': code, 'name': str(r.get('name', '')), 'recorded_close': close,
                    'baseline_date': str(r.get('date') or '')[:10] or None})
    out.sort(key=lambda x: x['code'])
    return out


def parse_baseline(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Running the backtest
# --------------------------------------------------------------------------

def recorded_source(report):
    metadata = report.get('provider_metadata')
    value = metadata.get('provider') if isinstance(metadata, dict) else None
    if value in SOURCES:
        return value
    label = str(report.get('source') or '')
    if label in SOURCES:
        return label
    for prefix, source in (('腾讯', 'tencent'), ('东方财富', 'eastmoney'), ('新浪', 'sina')):
        if label.startswith(prefix):
            return source
    return None


def baseline_info(report):
    baseline = parse_baseline(report.get('market_date'))
    if baseline:
        return baseline, ''
    baseline = parse_baseline(report.get('generated'))
    if baseline:
        return baseline, '报告缺少行情截止日，使用报告生成日期作为基准日'
    raise BacktestError('报告缺少有效的行情截止日和生成日期，无法回测')


def _pick_provider(report, provider):
    """Backtest with the provider that produced the report unless told otherwise."""
    if provider is not None:
        return provider
    recorded = recorded_source(report)
    from .providers import LiveProvider
    if recorded is None:
        raise BacktestError('无法识别原报告数据源，请明确选择回测来源')
    return LiveProvider(recorded)


def backtest(report, provider=None, cancel=None, progress=None, baseline=None):
    """Re-fetch the selected stocks of a past report and measure the price move."""
    cancel = cancel or threading.Event()
    progress = progress or (lambda *a: None)
    if report.get('demo'):
        raise BacktestError('这是演示数据报告，不能对真实行情回测')
    provider = _pick_provider(report, provider)
    baseline, baseline_note = (baseline, '') if baseline else baseline_info(report)
    if baseline > completed_cutoff():
        raise BacktestError('回测基准日晚于最新可完成日线日期')
    stocks = matched_stocks(report)
    if not stocks:
        raise BacktestError('报告中没有状态为“符合”的股票，无法回测')

    run = {
        'generated': datetime.now().astimezone().isoformat(),
        'baseline_date': baseline.isoformat(),
        'baseline_note': baseline_note,
        'disclaimer': '历史入选股票价格核对，不模拟成交、费用，不是收益承诺。',
        'cutoff_date': None,
        'report_path': str(report.get('_path', '')),
        'report_json': str(report.get('_json_path') or '') or None,
        'baseline_basis': report.get('_basis', SOURCE_JSON),
        'source_report_status': report.get('status', ''),
        'provider_label': getattr(provider, 'label', ''),
        'status': '完成',
        'results': [],
        'counts': {'total': len(stocks), 'ok': 0, 'unavailable': 0, 'cancelled': 0},
    }
    results = run['results']
    if cancel.is_set():
        run.update(status='已取消（部分结果）',source=getattr(provider,'source',''),provider_metadata=copy.deepcopy(getattr(provider,'metadata',{})))
        run['counts']['cancelled']=len(stocks)
        return run
    progress(0, len(stocks), '正在获取市场截止日，请等待网络响应；可取消')
    # Ask the provider where its newest completed bar is, so a stale index
    # cannot silently pretend that a later price does not exist yet.
    try:
        if cancel.is_set():
            raise Cancelled()
        cutoff = provider.latest_date()
        if cutoff < baseline or (completed_cutoff() - cutoff).days > 10:
            raise BacktestError('市场截止日早于基准日或超过10天未更新，停止回测')
        run['cutoff_date'] = cutoff.isoformat()
    except (Cancelled, SourceUnavailable, BacktestError):
        raise
    except Exception as e:
        raise BacktestError(f'无法取得市场截止日，未开始逐股回测：{e}') from e

    def one(stock):
        row = {'code': stock['code'], 'name': stock['name'],
               'recorded_close': stock['recorded_close'],
               'selection_date': stock['baseline_date'] or baseline.isoformat()}
        try:
            if cancel.is_set():
                raise Cancelled()
            span = max((cutoff - baseline).days, 0)
            count = min(MAX_SERIES, max(30, int((span + 10) * 1.6)))
            series = provider.bars_from(stock['code'], baseline, count, cutoff)
            if cancel.is_set():
                raise Cancelled()
            bars = [b for b in series if date.fromisoformat(b['date']) <= baseline]
            if not bars:
                first = series[0]['date'] if series else '无数据'
                raise MissingData(f'连续序列起点{first}晚于基准日，超出可回测范围')
            anchor = bars[-1]
            latest = series[-1]
            base_price = float(anchor['close'])
            last_price = float(latest['close'])
            if not (math.isfinite(base_price) and math.isfinite(last_price)) or min(base_price, last_price) <= 0:
                raise MissingData('最新价或基准价无效')
            change = last_price - base_price
            pct = (last_price / base_price - 1) * 100
            held = [b for b in series if anchor['date'] < b['date'] <= latest['date']]
            row.update(status='已回测', baseline_price=base_price, baseline_used_date=anchor['date'],
                       latest_price=last_price, latest_date=latest['date'], change=change, change_pct=pct,
                       trading_days=len(held),
                       baseline_source=f'同次连续行情 {anchor["date"]}；原记录：{report.get("_basis", SOURCE_JSON)}',
                       reason='')
            row['bars'] = series
        except Cancelled:
            row.update(status='已取消', reason='已取消')
        except SourceUnavailable as e:
            row.update(status='无法回测', reason=str(e), source_unavailable=True)
        except Exception as e:
            row.update(status='无法回测', reason=str(e) or type(e).__name__)
        return row

    iterator = iter(stocks)
    pending = set()
    source_failure = None
    with ThreadPoolExecutor(max_workers=6) as executor:
        def fill():
            while len(pending) < 6 and not cancel.is_set() and source_failure is None:
                try:
                    stock = next(iterator)
                except StopIteration:
                    break
                pending.add(executor.submit(one, stock))
        fill()
        while pending:
            done, pending = wait(pending, timeout=.2, return_when=FIRST_COMPLETED)
            for future in done:
                row = future.result()
                results.append(row)
                if row.get('source_unavailable'):
                    source_failure = row['reason']
                progress(len(results), len(stocks), f"已回测 {sum(1 for r in results if r['status'] == '已回测')} · 无法回测 {sum(1 for r in results if r['status'] == '无法回测')}")
            fill()

    results.sort(key=lambda r: r['code'])
    run['counts']['ok'] = sum(1 for r in results if r['status'] == '已回测')
    run['counts']['unavailable'] = sum(1 for r in results if r['status'] == '无法回测')
    # Same convention as the screening engine: stocks never submitted because the
    # run was cancelled are counted as cancelled. This keeps counts['total']
    # accountable even when no row was produced for them.
    accounted = run['counts']['ok'] + run['counts']['unavailable']
    run['counts']['cancelled'] = run['counts']['total'] - accounted
    if source_failure:
        run['status'] = '未完成'
        run['error'] = source_failure
    elif cancel.is_set():
        run['status'] = '已取消（部分结果）'
    elif not run['counts']['ok']:
        run['status'] = '未完成'
        run['error'] = '全部股票均无法回测，详见逐股原因'
    elif run['counts']['unavailable']:
        run['status'] = '完成（含无法回测）'
    provider_metadata = copy.deepcopy(getattr(provider, 'metadata', {}))
    if provider_metadata.get('provider'):
        run['source'] = provider_metadata['provider']
    else:
        run['source'] = getattr(provider, 'source', '')
    run['provider_metadata'] = provider_metadata
    return run


def rows(run):
    """Build the CSV/table rows for a backtest run."""
    out = []
    for r in run.get('results', []):
        out.append([
            r.get('code', ''), r.get('name', ''), r.get('selection_date', ''),
            r.get('recorded_close'), r.get('baseline_price'), r.get('latest_price'),
            r.get('latest_date', ''), r.get('change'), r.get('change_pct'),
            r.get('trading_days', ''), run.get('source', ''), r.get('baseline_source', ''),
            r.get('status', '') + (f"：{r['reason']}" if r.get('reason') else ''),
            r['change_pct']/100 if 'change_pct' in r else None,
            run.get('disclaimer','历史入选股票价格核对，不是收益承诺'),
        ])
    return out


def write_backtest(store, run, day=None):
    """Write the backtest CSV plus its JSON audit sidecar; never overwrite."""
    day = day or datetime.now().strftime('%Y-%m-%d')
    store.reports.mkdir(parents=True, exist_ok=True)
    for i in range(1, 1000000):
        path = store.reports / f'StockReport_Backtest_{day}_{i:03d}.csv'
        if path.with_suffix('.json').exists():
            continue
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            break
        except FileExistsError:
            continue
    else:
        raise RuntimeError('当日回测编号已耗尽')
    try:
        write_csv(path, BACKTEST_COLUMNS, rows(run))
        side = path.with_suffix('.json')
        temp = side.with_suffix('.json.tmp')
        temp.write_text(json.dumps(run, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        os.replace(temp, side)
    except Exception:
        path.unlink(missing_ok=True)
        path.with_suffix('.json').unlink(missing_ok=True)
        path.with_suffix('.json.tmp').unlink(missing_ok=True)
        raise
    return path
