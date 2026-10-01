"""CSV export helpers.

The runtime deliberately keeps zero third-party dependencies, so every CSV is
written with the standard library.  Two rules matter for the delivered files:

* UTF-8 BOM + CRLF, so Excel on a Chinese Windows opens the file without a
  manual encoding choice.
* Text that starts with a formula trigger is neutralised, so a stock name can
  never turn into an executed formula in the spreadsheet.
"""
import csv
import os
import tempfile
from pathlib import Path

FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r')

# One label set is shared by the Markdown report, the CSV and the in-app table
# so the three can never disagree about what a status means.
STATUS_LABELS = {
    'total': '目标股票',
    'matched': '符合',
    'rejected': '不符合',
    'missing': '数据不足',
    'failed': '获取或处理失败',
    'cancelled': '未处理/已取消',
}

REPORT_COLUMNS = ('股票代码', '名称', '数据日期', '收盘价', '财报期', '净利润(元)', '状态')


def text(value):
    """Return a spreadsheet-safe string for one CSV field."""
    if value is None:
        return ''
    if isinstance(value, str):
        s = value
    else:
        s = str(value)
    if s.startswith(FORMULA_PREFIXES):
        # Excel treats a leading quote as "this is text" and hides the quote.
        return "'" + s
    return s


def number(value, digits=4):
    """Format a finite number for sorting-friendly CSV output.

    Returns '' for missing values instead of 0/NaN/None, and keeps the shortest
    representation that still round-trips, so 95.0 becomes 95 and a percentage
    reads as 1.1611 rather than the full float repr.
    """
    if value is None or isinstance(value, bool):
        return ''
    try:
        v = float(value)
    except (TypeError, ValueError):
        return text(value)
    if v != v or v in (float('inf'), float('-inf')):
        return ''
    for spec in ('.0f', '.2f', f'.{digits}f'):
        try:
            formatted = format(v, spec)
        except (ValueError, TypeError):
            continue
        if float(formatted) == v:
            return formatted
    return format(v, f'.{digits}f')


def cell(value):
    """Render one value as a CSV cell: numbers stay numeric, text stays text."""
    if value is None:
        return ''
    if isinstance(value, bool):
        return '是' if value else '否'
    if isinstance(value, (int, float)):
        # Display formatting must not round the exported calculation evidence.
        if isinstance(value,float) and (value != value or value in (float('inf'),float('-inf'))): return ''
        return str(value)
    return text(value)


def write_csv(path, header, rows):
    """Write a UTF-8-BOM CSV atomically and return the final path."""
    path = Path(path)
    fd,temp_name=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.writer(handle, lineterminator='\r\n',quoting=csv.QUOTE_ALL)
            writer.writerow([text(h) for h in header])
            for row in rows:
                writer.writerow([cell(v) for v in row])
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return path


def report_rows(run):
    """Build [代码, 名称, 数据日期, 收盘价, 财报期, 净利润, 状态] for every checked stock."""
    rows = []
    for r in run.get('results', []):
        fin = r.get('financial') or {}
        status = STATUS_LABELS.get(r.get('status'), r.get('status', ''))
        reason = r.get('reason')
        if reason and r.get('status') in ('missing', 'failed', 'cancelled'):
            status = f'{status}：{reason}'
        rows.append([
            r.get('code', ''),
            r.get('name', ''),
            r.get('date', ''),
            r.get('close'),
            fin.get('report_date', ''),
            fin.get('net_profit'),
            status,
        ])
    rows.sort(key=lambda row: str(row[0]))
    return rows


def write_report_csv(path, run):
    """Write the screening CSV next to the Markdown report."""
    return write_csv(path, REPORT_COLUMNS, report_rows(run))


def coverage_rows(run):
    counts = run.get('counts') or {}
    rows = [[STATUS_LABELS[key], counts.get(key, 0)] for key in STATUS_LABELS]
    rows.append(['数据源', run.get('source', '')])
    rows.append(['行情截止', run.get('market_date') or '未取得'])
    rows.append(['运行状态', run.get('status', '')])
    return rows


def write_coverage_csv(path, run):
    """Optional side export: the same coverage table the Markdown report shows."""
    return write_csv(path, ('项目', '数值'), coverage_rows(run))
