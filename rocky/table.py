"""Read the marketer's own spreadsheet as data, so no worker summarises a table by eye.

``table_analyze`` is the general tabular tool behind survey audits, service-quality
gaps, complaint analysis, search-term splits, press benchmarks and before/after
checks. It reads a CSV or XLSX inside the workspace, narrows the rows by a date
window and filters, and runs one ``op`` over them: ``describe``, ``group``,
``crosstab``, ``before_after``, ``period_compare``, ``proportion_diff``,
``intervals``, ``coverage``, ``text_themes`` or ``search_terms_split``. Every
result reports the dates actually covered, the rows used, the blanks and bad cells
it met under ``missing``, the method behind any statistic, and a one-sentence
``summary`` written to be quoted. Groups below ``min_rows`` are listed as thin and
never ranked.

Statistics are stdlib only: the chi-square p-value comes from the regularized
upper incomplete gamma function (series / continued fraction), the Welch t p-value
from the Student t distribution through the regularized incomplete beta function,
and the two-proportion z from the normal distribution via ``math.erfc``.
"""

from __future__ import annotations

import csv
import json
import math
import re
from datetime import date
from pathlib import Path
from typing import Any

from rocky.funnel import BLANK_GROUP, _column, _fmt, _list, _num, _parse_date, _pct
from rocky.tools import ToolError, WorkspaceTools

OPS = (
    "describe",
    "group",
    "crosstab",
    "before_after",
    "period_compare",
    "proportion_diff",
    "intervals",
    "coverage",
    "text_themes",
    "search_terms_split",
)
DEFAULT_MIN_ROWS = 30
DEFAULT_TOP = 20
MAX_LEVELS = 20
SAMPLE_ROWS = 5
SAMPLE_CHARS = 160
FILTER_OPS = ("==", "!=", "contains", ">", "<", ">=", "<=", "in")
AGGS = ("count", "sum", "mean", "median", "share", "distinct")
BUCKETS = ("week", "month", "year", "weekday")
TRUTHY = {"1", "true", "yes", "y", "taip", "x"}
_Z95 = 1.959963984540054

# Header names Google Ads uses in search-term exports (English and Lithuanian UI), lower-cased.
ADS_TERM_HEADERS = ("search term", "search terms", "search_term", "term", "query", "search query", "paieškos terminas", "paieškos užklausa")
ADS_COST_HEADERS = ("cost", "cost (eur)", "spend", "išlaidos", "kaina", "cost_eur")
ADS_CONV_HEADERS = ("conversions", "conv.", "konversijos", "all conv.")
ADS_VALUE_HEADERS = ("conv. value", "conversion value", "all conv. value", "konv. vertė", "konversijų vertė", "conv_value_eur", "value")


# --- reading the file ------------------------------------------------------


def _load_table(
    tools: WorkspaceTools, path: str, sheet: Any, header_row: Any
) -> tuple[list[str], list[dict[str, Any]], list[str]]:
    """Header names, one dict per data row and notes about the header. Like funnel's
    loader, but it also finds the header below the preamble lines that Google Ads and
    GA exports put above the table, and ``header_row`` (1-based) can pin it."""
    target = tools.resolve(path)
    if not target.exists():
        raise ToolError(f"not found: {path}")
    if target.is_dir():
        raise ToolError(f"is a directory: {path}")
    suffix = target.suffix.lower()
    if suffix in {".csv", ".tsv", ".txt"}:
        grid = _csv_grid(target)
    elif suffix in {".xlsx", ".xlsm"}:
        grid = _xlsx_grid(target, sheet)
    else:
        raise ToolError(f"unsupported file type {suffix or '(none)'} for {path}: give a .csv or .xlsx file")
    return _grid_to_rows(grid, header_row, target.name)


def _csv_grid(target: Path) -> list[list[Any]]:
    text = target.read_text(encoding="utf-8-sig", errors="replace")
    if not text.strip():
        raise ToolError(f"{target.name} is empty")
    try:
        dialect: Any = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [row for row in csv.reader(text.splitlines(), dialect)]


def _xlsx_grid(target: Path, sheet: Any) -> list[list[Any]]:
    from openpyxl import load_workbook

    wb = load_workbook(target, data_only=True, read_only=True)
    try:
        if sheet:
            if str(sheet) not in wb.sheetnames:
                raise ToolError(f"sheet {sheet!r} not found in {target.name}; sheets: {', '.join(wb.sheetnames)}")
            ws = wb[str(sheet)]
        else:
            ws = wb.worksheets[0]
        return [list(row) for row in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def _blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def _grid_to_rows(grid: list[list[Any]], header_row: Any, name: str) -> tuple[list[str], list[dict[str, Any]], list[str]]:
    notes: list[str] = []
    if not grid:
        raise ToolError(f"{name} is empty")
    if header_row not in (None, ""):
        try:
            idx = int(header_row) - 1
        except (TypeError, ValueError) as exc:
            raise ToolError("header_row must be a row number counted from 1") from exc
        if idx < 0 or idx >= len(grid):
            raise ToolError(f"header_row {header_row} is outside the file ({len(grid)} rows)")
    else:
        idx = _detect_header(grid)
    if idx > 0:
        skipped = [" ".join(str(c).strip() for c in grid[i] if not _blank(c))[:60] for i in range(idx)]
        shown = "; ".join(s for s in skipped if s)
        notes.append(f"{idx} line{'s' if idx != 1 else ''} above the header skipped" + (f": {shown}." if shown else "."))
    header: list[str] = []
    seen: dict[str, int] = {}
    for cell in grid[idx]:
        h = "" if cell is None else str(cell).strip()
        if not h:
            header.append("")
            continue
        if h in seen:
            seen[h] += 1
            h = f"{h} ({seen[h]})"
        else:
            seen[h] = 1
        header.append(h)
    if not any(header):
        raise ToolError(f"{name} has no header row")
    out = []
    for row in grid[idx + 1 :]:
        if row is None or not any(not _blank(c) for c in row):
            continue
        out.append({header[i]: (row[i] if i < len(row) else None) for i in range(len(header)) if header[i]})
    return [h for h in header if h], out, notes


def _detect_header(grid: list[list[Any]]) -> int:
    """Index of the first early row that is about as wide as the widest early row: the
    header. Preamble lines ('Search terms report', a date range) have one or two cells."""
    fills = [sum(1 for c in row if not _blank(c)) for row in grid[:10]]
    widest = max(fills) if fills else 0
    for i, filled in enumerate(fills):
        if filled >= 2 and filled >= 0.6 * widest:
            return i
    return 0


# --- parsing cells ---------------------------------------------------------


_THOUSANDS = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")
_STRIP_CHARS = str.maketrans("", "", "   €$£%")


def _number(value: Any) -> tuple[float | None, str]:
    """(number, status) with status ok, blank or bad. Unlike funnel counts, metrics may be negative;
    spaces, currency signs and a percent sign are ignored; 1,234.5 and 1234,5 both read."""
    if value is None:
        return None, "blank"
    if isinstance(value, bool):
        return (1.0 if value else 0.0), "ok"
    if isinstance(value, (int, float)):
        return (None, "bad") if value != value or value in (float("inf"), float("-inf")) else (float(value), "ok")
    text = str(value).strip()
    if not text:
        return None, "blank"
    text = text.translate(_STRIP_CHARS)
    if not text:
        return None, "bad"
    if _THOUSANDS.match(text):
        text = text.replace(",", "")
    elif "," in text and "." not in text:
        text = text.replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        return None, "bad"
    if number != number or number in (float("inf"), float("-inf")):
        return None, "bad"
    return number, "ok"


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


def _r(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(value, digits)


def _pv(p: float | None) -> float | None:
    """A p-value to 4 decimals, or two significant figures when it is smaller than 0.0001."""
    if p is None:
        return None
    return round(p, 4) if p >= 0.0001 else float(f"{p:.2g}")


# --- small statistics (stdlib only) ------------------------------------------


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _sd(values: list[float]) -> float | None:
    """Sample standard deviation (n − 1)."""
    if len(values) < 2:
        return None
    m = sum(values) / len(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def _percentile(sorted_values: list[float], q: float) -> float | None:
    """Linear interpolation between order statistics (the same rule as numpy's default)."""
    if not sorted_values:
        return None
    pos = (len(sorted_values) - 1) * q
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def _median(values: list[float]) -> float | None:
    return _percentile(sorted(values), 0.5)


def _norm_p_two_sided(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2))


def _gammq(a: float, x: float) -> float | None:
    """Regularized upper incomplete gamma Q(a, x): series for P when x < a + 1, Lentz continued fraction otherwise."""
    if a <= 0 or x < 0:
        return None
    if x == 0:
        return 1.0
    log_prefix = -x + a * math.log(x) - math.lgamma(a)
    if x < a + 1:
        ap, total, delta = a, 1 / a, 1 / a
        for _ in range(1000):
            ap += 1
            delta *= x / ap
            total += delta
            if abs(delta) < abs(total) * 1e-15:
                break
        return max(0.0, min(1.0, 1 - total * math.exp(log_prefix)))
    tiny = 1e-300
    b = x + 1 - a
    c = 1 / tiny
    d = 1 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1 / d
        de = d * c
        h *= de
        if abs(de - 1) < 1e-15:
            break
    return max(0.0, min(1.0, math.exp(log_prefix) * h))


def _chi2_p(stat: float, df: int) -> float | None:
    return _gammq(df / 2, stat / 2)


def _betacf(a: float, b: float, x: float) -> float:
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1, a - 1
    c = 1.0
    d = 1 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1 / d
    h = d
    for m in range(1, 1000):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1 / d
        de = d * c
        h *= de
        if abs(de - 1) < 1e-15:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b) by continued fraction."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    log_bt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    bt = math.exp(log_bt)
    if x < (a + 1) / (a + b + 2):
        return bt * _betacf(a, b, x) / a
    return 1 - bt * _betacf(b, a, 1 - x) / b


def _t_p_two_sided(t: float, df: float) -> float | None:
    if df <= 0:
        return None
    return max(0.0, min(1.0, _betainc(df / 2, 0.5, df / (df + t * t))))


def _welch(a: list[float], b: list[float]) -> dict[str, Any]:
    """Welch t-statistic, Welch–Satterthwaite df and the two-sided p from Student t."""
    if len(a) < 2 or len(b) < 2:
        return {"t": None, "df": None, "p_value": None}
    va, vb = _sd(a) ** 2 / len(a), _sd(b) ** 2 / len(b)  # type: ignore[operator]
    if va + vb == 0:
        return {"t": None, "df": None, "p_value": None}
    t = (_mean(b) - _mean(a)) / math.sqrt(va + vb)  # type: ignore[operator]
    df = (va + vb) ** 2 / (va**2 / (len(a) - 1) + vb**2 / (len(b) - 1))
    return {"t": _r(t), "df": _r(df, 1), "p_value": _pv(_t_p_two_sided(t, df))}


# --- rows, window, filters, group keys --------------------------------------


def _window(
    rows: list[dict[str, Any]], date_col: str | None, date_from: date | None, date_to: date | None
) -> tuple[list[dict[str, Any]], dict[str, Any], int]:
    if date_col is None:
        return rows, {"date_column": None, "from": None, "to": None, "requested_from": None, "requested_to": None}, 0
    kept, dates, without = [], [], 0
    for row in rows:
        d = _parse_date(row.get(date_col))
        if d is None:
            without += 1
            continue
        if (date_from and d < date_from) or (date_to and d > date_to):
            continue
        kept.append(row)
        dates.append(d)
    window = {
        "date_column": date_col,
        "from": min(dates).isoformat() if dates else None,
        "to": max(dates).isoformat() if dates else None,
        "requested_from": date_from.isoformat() if date_from else None,
        "requested_to": date_to.isoformat() if date_to else None,
    }
    return kept, window, without


def _filters(value: Any, header: list[str]) -> list[dict[str, Any]]:
    if value in (None, "", []):
        return []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        raise ToolError("filter must be a list of {column, op, value}")
    out = []
    for item in value:
        if not isinstance(item, dict):
            raise ToolError("each filter is an object {column, op, value}")
        col = _column(item.get("column"), header, "filter column")
        op = str(item.get("op") or "==").strip()
        if op not in FILTER_OPS:
            raise ToolError(f"filter op {op!r} is not one of {', '.join(FILTER_OPS)}")
        val = item.get("value")
        if op == "in":
            if isinstance(val, str):
                val = [v.strip() for v in val.split(",") if v.strip()]
            if not isinstance(val, list):
                raise ToolError("filter op 'in' needs a list value")
        elif val is None:
            raise ToolError(f"filter on {col} needs a value")
        out.append({"column": col, "op": op, "value": val})
    return out


def _same(cell: Any, wanted: Any) -> bool:
    a, status_a = _number(cell)
    b, status_b = _number(wanted)
    if status_a == "ok" and status_b == "ok":
        return abs(a - b) < 1e-9  # type: ignore[operator]
    return _text(cell).casefold() == _text(wanted).casefold()


def _ordered(cell: Any, wanted: Any) -> tuple[Any, Any] | None:
    a, status_a = _number(cell)
    b, status_b = _number(wanted)
    if status_a == "ok" and status_b == "ok":
        return a, b
    da, db = _parse_date(cell), _parse_date(wanted)
    if da and db:
        return da, db
    return None


def _match(cell: Any, op: str, wanted: Any) -> bool:
    if op == "==":
        return _same(cell, wanted)
    if op == "!=":
        return not _same(cell, wanted)
    if op == "contains":
        return _text(wanted).casefold() in _text(cell).casefold()
    if op == "in":
        return any(_same(cell, w) for w in wanted)
    pair = _ordered(cell, wanted)
    if pair is None:
        return False
    a, b = pair
    return {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[op]


def _apply_filters(rows: list[dict[str, Any]], filters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for f in filters:
        rows = [r for r in rows if _match(r.get(f["column"]), f["op"], f["value"])]
    return rows


def _by_specs(value: Any, header: list[str], what: str = "by") -> list[tuple[str, str]]:
    """[(kind, column)] where kind is 'col' or a date bucket (week, month, year, weekday)."""
    items = _list(value, what)
    if not items:
        raise ToolError(f"{what} is required: one or more column names, or week:<date column> / month:<date column>")
    specs = []
    for item in items:
        m = re.match(r"^(\w+)\s*:\s*(.+)$", item)
        if m and m.group(1).lower() in BUCKETS:
            specs.append((m.group(1).lower(), _column(m.group(2), header, what)))
        else:
            specs.append(("col", _column(item, header, what)))
    return specs


def _by_label(specs: list[tuple[str, str]]) -> str:
    return " / ".join(col if kind == "col" else f"{kind}:{col}" for kind, col in specs)


def _bucket(d: date, kind: str) -> str:
    if kind == "week":
        iso = d.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    if kind == "month":
        return f"{d.year}-{d.month:02d}"
    if kind == "year":
        return str(d.year)
    return d.strftime("%a")


def _row_key(row: dict[str, Any], specs: list[tuple[str, str]]) -> tuple[str, bool]:
    """(key, blank) — blank is True when any part was empty or an unreadable date."""
    parts, blank = [], False
    for kind, col in specs:
        value = row.get(col)
        if kind == "col":
            text = _text(value)
            if not text:
                blank = True
                text = BLANK_GROUP
        else:
            d = _parse_date(value)
            if d is None:
                blank = True
                text = BLANK_GROUP
            else:
                text = _bucket(d, kind)
        parts.append(text)
    return " / ".join(parts), blank


def _buckets(
    rows: list[dict[str, Any]], specs: list[tuple[str, str]], missing: list[str]
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    blanks = 0
    for row in rows:
        key, blank = _row_key(row, specs)
        blanks += blank
        out.setdefault(key, []).append(row)
    if blanks:
        missing.append(f"{_plural(blanks, 'row')} with a blank or unreadable {_by_label(specs)} grouped as {BLANK_GROUP}.")
    return out


def _metric_values(rows: list[dict[str, Any]], col: str, missing: list[str]) -> dict[int, float]:
    """id(row) → number for rows whose cell is a number. Blanks are left out and reported, bad
    cells drop their row and are reported; a column with no numbers at all is a ToolError."""
    values: dict[int, float] = {}
    blank = bad = 0
    example = None
    for row in rows:
        number, status = _number(row.get(col))
        if status == "ok":
            values[id(row)] = number  # type: ignore[assignment]
        elif status == "blank":
            blank += 1
        else:
            bad += 1
            if example is None:
                example = _text(row.get(col))[:40]
    if bad and not values:
        raise ToolError(
            f"{col} is not numeric: {bad} of {bad + blank} non-blank cell{'s' if bad + blank != 1 else ''} "
            f"failed to parse (e.g. {example!r}); pick a numeric column"
        )
    if blank:
        missing.append(f"{col}: {_plural(blank, 'blank cell')} left out.")
    if bad:
        missing.append(f"{_plural(bad, 'row')} dropped because {col} was not a number (e.g. {example!r}).")
    return values


def _levels(rows: list[dict[str, Any]], col: str, missing: list[str], what: str) -> tuple[list[str], list[str]]:
    """Distinct values of ``col`` in order of appearance (blank → BLANK_GROUP) and the per-row keys."""
    keys, levels, blanks = [], [], 0
    for row in rows:
        text = _text(row.get(col))
        if not text:
            blanks += 1
            text = BLANK_GROUP
        keys.append(text)
        if text not in levels:
            levels.append(text)
    if blanks:
        missing.append(f"{_plural(blanks, 'row')} with a blank {col} counted as {BLANK_GROUP}.")
    if len(levels) > MAX_LEVELS:
        raise ToolError(f"{what} {col} has {len(levels)} distinct values; crosstab takes at most {MAX_LEVELS}. Filter or bucket it first.")
    return levels, keys


# --- ops -----------------------------------------------------------------


class _Ctx:
    def __init__(self, args: dict, header: list[str], rows: list[dict[str, Any]], min_rows: int, missing: list[str], warnings: list[str]):
        self.args = args
        self.header = header
        self.rows = rows
        self.min_rows = min_rows
        self.missing = missing
        self.warnings = warnings

    def col(self, key: str, required: bool = True) -> str | None:
        value = self.args.get(key)
        if value in (None, ""):
            if required:
                raise ToolError(f"{key} is required; columns found: {', '.join(self.header) or '(none)'}")
            return None
        return _column(value, self.header, key)


def _op_describe(ctx: _Ctx) -> dict[str, Any]:
    columns = []
    for col in ctx.header:
        raw = [row.get(col) for row in ctx.rows]
        present = [v for v in raw if not _blank(v)]
        nulls = len(raw) - len(present)
        numbers = [n for n, s in (_number(v) for v in present) if s == "ok"]
        entry: dict[str, Any] = {"name": col, "nulls": nulls, "distinct": len({_text(v) for v in present})}
        if present and len(numbers) >= 0.8 * len(present):
            entry["type"] = "numeric"
            entry["min"], entry["max"], entry["mean"] = _num(min(numbers)), _num(max(numbers)), _num(_mean(numbers) or 0.0)
            if len(numbers) < len(present):
                entry["bad"] = len(present) - len(numbers)
        else:
            dates = [d for d in (_parse_date(v) for v in present) if d is not None]
            if present and len(dates) >= 0.8 * len(present):
                entry["type"] = "date"
                entry["min"], entry["max"] = min(dates).isoformat(), max(dates).isoformat()
                if len(dates) < len(present):
                    entry["bad"] = len(present) - len(dates)
            else:
                entry["type"] = "text"
                counts: dict[str, int] = {}
                for v in present:
                    counts[_text(v)] = counts.get(_text(v), 0) + 1
                entry["top_values"] = [
                    {"value": k, "n": n} for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:SAMPLE_ROWS]
                ]
        columns.append(entry)
    kinds = {k: sum(1 for c in columns if c["type"] == k) for k in ("numeric", "date", "text")}
    summary = (
        f"{_fmt(len(ctx.rows))} rows and {len(columns)} columns ({kinds['numeric']} numeric, {kinds['date']} date, {kinds['text']} text)"
        + (f"; columns with blanks: {', '.join(c['name'] for c in columns if c['nulls'])}." if any(c["nulls"] for c in columns) else ".")
    )
    return {"columns": columns, "summary": summary}


def _op_group(ctx: _Ctx) -> dict[str, Any]:
    args = ctx.args
    specs = _by_specs(args.get("by"), ctx.header)
    metric = ctx.col("metric", required=False)
    agg = str(args.get("agg") or ("sum" if metric else "count")).strip().lower()
    if agg not in AGGS:
        raise ToolError(f"agg must be one of {', '.join(AGGS)}")
    if agg in {"sum", "mean", "median"} and metric is None:
        raise ToolError(f"agg {agg} needs metric: the numeric column to aggregate")
    distinct_by = ctx.col("distinct_by", required=False)
    if agg == "distinct" and distinct_by is None:
        raise ToolError("agg distinct needs distinct_by: the id column to count unique values of")
    top = int(args.get("top") or DEFAULT_TOP)
    values = _metric_values(ctx.rows, metric, ctx.missing) if metric and agg != "distinct" else None
    rows = [r for r in ctx.rows if id(r) in values] if values is not None else ctx.rows
    buckets = _buckets(rows, specs, ctx.missing)

    def agg_of(group_rows: list[dict[str, Any]]) -> float:
        if agg == "count":
            return float(len(group_rows))
        if agg == "distinct":
            return float(len({_text(r.get(distinct_by)) for r in group_rows if not _blank(r.get(distinct_by))}))
        nums = [values[id(r)] for r in group_rows]  # type: ignore[index]
        if agg == "sum" or (agg == "share" and metric):
            return sum(nums)
        if agg == "share":
            return float(len(group_rows))
        if agg == "mean":
            return _mean(nums) or 0.0
        return _median(nums) or 0.0

    grand = agg_of(rows)
    if agg == "share":
        total_value: float | None = 100.0
    else:
        total_value = grand
    groups = []
    for key, group_rows in buckets.items():
        value = agg_of(group_rows)
        n = len(group_rows)
        if agg in {"count", "sum", "share", "distinct"} and agg != "distinct":
            share = _pct(value, grand) if grand else None
        else:
            share = _pct(n, len(rows))
        entry: dict[str, Any] = {
            "key": key,
            "n": n,
            "value": _num(_pct(value, grand) or 0.0) if agg == "share" else _num(value),
            "share_pct": share,
            "thin": n < ctx.min_rows,
            "rank": None,
        }
        if agg == "mean":
            sd = _sd([values[id(r)] for r in group_rows])  # type: ignore[index]
            entry["sd"] = None if sd is None else _num(sd)
        groups.append(entry)
    ranked = sorted([g for g in groups if not g["thin"]], key=lambda g: (-g["value"], -g["n"], g["key"]))
    for i, g in enumerate(ranked, 1):
        g["rank"] = i
    thin = sorted([g for g in groups if g["thin"]], key=lambda g: (-g["n"], g["key"]))
    ordered = ranked + thin
    gap = None
    if agg in {"mean", "median"} and len(ranked) >= 2:
        gap = {"best": ranked[0]["key"], "worst": ranked[-1]["key"], "gap": _num(ranked[0]["value"] - ranked[-1]["value"])}
    if thin:
        ctx.warnings.append(f"{_plural(len(thin), 'group')} below {ctx.min_rows} rows listed as thin and not ranked.")
    out: dict[str, Any] = {
        "by": _by_label(specs),
        "metric": metric,
        "agg": agg,
        "groups": ordered[:top],
        "groups_total": len(groups),
        "groups_omitted": max(0, len(groups) - top),
        "total": None if total_value is None else _num(total_value),
        "gap": gap,
    }
    shares = args.get("population_shares")
    if agg == "mean" and isinstance(shares, dict) and shares:
        out["pooled_unweighted_mean"] = _num(grand)
        folded = {str(k).strip().casefold(): float(v) for k, v in shares.items()}
        covered = [(g, folded.get(g["key"].casefold())) for g in groups]
        if any(w is None for _, w in covered):
            absent = [g["key"] for g, w in covered if w is None]
            out["pooled_weighted_mean"] = None
            ctx.warnings.append(f"population_shares has no share for: {', '.join(absent)}; no weighted mean.")
        else:
            wsum = sum(w for _, w in covered)  # type: ignore[misc]
            out["pooled_weighted_mean"] = _num(sum(g["value"] * w for g, w in covered) / wsum) if wsum else None  # type: ignore[operator]
        out["method"] = "weighted mean = Σ share_g × mean_g over the groups present, shares renormalised to the groups present."
    unit = f"{agg} of {metric}" if metric and agg != "count" else ("distinct " + str(distinct_by) if agg == "distinct" else "rows")
    if agg in {"mean", "median"} and gap:
        best, worst = ranked[0], ranked[-1]
        summary = (
            f"By {out['by']}, {unit} over {_fmt(len(rows))} rows: best {best['key']} {_fmt(best['value'])} (n {best['n']}), "
            f"worst {worst['key']} {_fmt(worst['value'])} (n {worst['n']}), gap {_fmt(gap['gap'])}"
        )
    else:
        head = ", ".join(f"{g['key']} {_fmt(g['value'])}" + (f" ({g['share_pct']:g} %)" if g["share_pct"] is not None and agg != "share" else "") for g in ranked[:3])
        summary = f"By {out['by']}, {unit} over {_fmt(len(rows))} rows: {head or 'no group reaches ' + str(ctx.min_rows) + ' rows'}"
    if thin:
        summary += f"; {_plural(len(thin), 'group')} below {ctx.min_rows} rows left unranked"
    out["summary"] = summary + "."
    return out


def _op_crosstab(ctx: _Ctx) -> dict[str, Any]:
    row_col = ctx.col("rows")
    col_col = ctx.col("cols")
    if row_col == col_col:
        raise ToolError("rows and cols must be two different columns")
    row_levels, row_keys = _levels(ctx.rows, row_col, ctx.missing, "rows")
    col_levels, col_keys = _levels(ctx.rows, col_col, ctx.missing, "cols")
    counts: dict[str, dict[str, int]] = {r: {c: 0 for c in col_levels} for r in row_levels}
    for r, c in zip(row_keys, col_keys):
        counts[r][c] += 1
    row_totals = {r: sum(counts[r].values()) for r in row_levels}
    col_totals = {c: sum(counts[r][c] for r in row_levels) for c in col_levels}
    n = len(ctx.rows)
    cells = {r: {c: {"n": counts[r][c], "row_pct": _pct(counts[r][c], row_totals[r])} for c in col_levels} for r in row_levels}
    thin = [{"row": r, "col": c, "n": counts[r][c]} for r in row_levels for c in col_levels if counts[r][c] < ctx.min_rows]
    chi2: dict[str, Any] | None = None
    reason = None
    if n == 0 or len(row_levels) < 2 or len(col_levels) < 2:
        reason = "chi-square needs at least two rows and two columns with data"
    else:
        stat, low = 0.0, []
        for r in row_levels:
            for c in col_levels:
                expected = row_totals[r] * col_totals[c] / n
                if expected < 5:
                    low.append(expected)
                elif expected > 0:
                    stat += (counts[r][c] - expected) ** 2 / expected
        if low:
            reason = f"expected count below 5 in {_plural(len(low), 'cell')} (smallest {min(low):.1f}); chi-square not computed"
        else:
            df = (len(row_levels) - 1) * (len(col_levels) - 1)
            chi2 = {"stat": _r(stat), "df": df, "p_value": _pv(_chi2_p(stat, df))}
    if thin:
        ctx.warnings.append(f"{_plural(len(thin), 'cell')} below {ctx.min_rows} rows: read their percentages as rough.")
    largest = max(((r, c, counts[r][c]) for r in row_levels for c in col_levels), key=lambda t: t[2], default=None)
    summary = f"{row_col} × {col_col} over {_fmt(n)} rows: {len(row_levels)} × {len(col_levels)} cells"
    if largest:
        summary += f"; largest cell {largest[0]} / {largest[1]} with {_fmt(largest[2])} ({_pct(largest[2], row_totals[largest[0]]):g} % of its row)"
    if chi2:
        summary += f"; chi-square {chi2['stat']:g} (df {chi2['df']}), p = {chi2['p_value']:g}"
    elif reason:
        summary += f"; no chi-square: {reason}"
    return {
        "rows_column": row_col,
        "cols_column": col_col,
        "row_levels": row_levels,
        "col_levels": col_levels,
        "cells": cells,
        "row_totals": row_totals,
        "col_totals": col_totals,
        "thin_cells": thin,
        "chi2": chi2,
        "chi2_reason": reason,
        "method": "Pearson chi-square of independence; p-value from the regularized upper incomplete gamma function Q(df/2, χ²/2) (series / continued fraction); computed only when every expected cell count is at least 5.",
        "summary": summary + ".",
    }


def _side_stats(values: list[float]) -> dict[str, Any]:
    return {"n": len(values), "mean": None if not values else _num(_mean(values) or 0.0), "sd": None if _sd(values) is None else _num(_sd(values) or 0.0)}


def _before_after_block(before: list[float], after: list[float], warnings: list[str], label: str = "") -> dict[str, Any]:
    mb, ma = _mean(before), _mean(after)
    change_abs = None if mb is None or ma is None else ma - mb
    change_pct = None if change_abs is None or mb == 0 else change_abs / abs(mb) * 100  # type: ignore[operator]
    block: dict[str, Any] = {
        "before": _side_stats(before),
        "after": _side_stats(after),
        "change_abs": None if change_abs is None else _num(change_abs),
        "change_pct": None if change_pct is None else round(change_pct, 1),
    }
    block.update(_welch(before, after))
    prefix = f"{label}: " if label else ""
    if len(before) < 10 or len(after) < 10:
        warnings.append(f"{prefix}fewer than 10 rows on one side (before {len(before)}, after {len(after)}): the change is rough and the p-value is weak.")
    if mb is not None and mb == 0:
        warnings.append(f"{prefix}the before mean is 0, so no percentage change.")
    return block


def _op_before_after(ctx: _Ctx) -> dict[str, Any]:
    args = ctx.args
    date_col = ctx.col("date_column")
    metric = ctx.col("metric")
    split = _parse_date(args.get("split_date")) if args.get("split_date") else None
    if split is None:
        raise ToolError("split_date is required (YYYY-MM-DD): rows before it are 'before', rows on or after it are 'after'")
    normalize = ctx.col("normalize_by", required=False)
    values = _metric_values(ctx.rows, metric, ctx.missing)
    rows = [r for r in ctx.rows if id(r) in values]
    if normalize:
        norms = _metric_values(rows, normalize, ctx.missing)
        zero = sum(1 for r in rows if id(r) in norms and norms[id(r)] == 0)
        rows = [r for r in rows if id(r) in norms and norms[id(r)] != 0]
        if zero:
            ctx.missing.append(f"{_plural(zero, 'row')} dropped because {normalize} was 0.")
        per_row = {id(r): values[id(r)] / norms[id(r)] for r in rows}
    else:
        per_row = {id(r): values[id(r)] for r in rows}
    sides: dict[str, list[float]] = {"before": [], "after": []}
    unreadable = 0
    for r in rows:
        d = _parse_date(r.get(date_col))
        if d is None:
            unreadable += 1
            continue
        sides["after" if d >= split else "before"].append(per_row[id(r)])
    if unreadable:
        ctx.missing.append(f"{_plural(unreadable, 'row')} without a readable date in {date_col} left out.")
    out: dict[str, Any] = {
        "metric": metric,
        "normalize_by": normalize,
        "split_date": split.isoformat(),
    }
    out.update(_before_after_block(sides["before"], sides["after"], ctx.warnings))
    if args.get("by"):
        specs = _by_specs(args.get("by"), ctx.header)
        groups = []
        for key, group_rows in _buckets(rows, specs, ctx.missing).items():
            gb, ga = [], []
            for r in group_rows:
                d = _parse_date(r.get(date_col))
                if d is not None:
                    (ga if d >= split else gb).append(per_row[id(r)])
            block = _before_after_block(gb, ga, ctx.warnings, key)
            block["key"] = key
            block["thin"] = min(len(gb), len(ga)) < ctx.min_rows
            groups.append(block)
        out["by"] = _by_label(specs)
        out["groups"] = groups
    out["method"] = (
        "Rows dated before split_date are 'before', on or after it 'after'"
        + (f"; each value is {metric} ÷ {normalize}" if normalize else "")
        + ". Welch two-sample t-test (unequal variances), two-sided p from the Student t distribution with Welch–Satterthwaite df via the regularized incomplete beta function; sd is the sample sd (n − 1)."
    )
    unit = f"{metric} per {normalize}" if normalize else metric
    b, a = out["before"], out["after"]
    if b["mean"] is None or a["mean"] is None:
        summary = f"{unit}: one side of {split.isoformat()} has no rows (before {b['n']}, after {a['n']}), so no change can be computed."
    else:
        summary = (
            f"Mean {unit} went from {_fmt(b['mean'])} (n {b['n']}) before {split.isoformat()} to {_fmt(a['mean'])} (n {a['n']}) after"
            + (f", {out['change_pct']:+g} %" if out["change_pct"] is not None else "")
            + (f"; Welch t {out['t']:g}, p = {out['p_value']:g}" if out["p_value"] is not None else "")
            + "."
        )
    out["summary"] = summary
    return out


_QUARTER_A = re.compile(r"^(\d{4})\s*[-_ ]?\s*Q([1-4])$", re.I)
_QUARTER_B = re.compile(r"^Q([1-4])\s*[-_ ]?\s*(\d{4})$", re.I)
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_WEEK = re.compile(r"^(\d{4})-W(\d{2})$")


def _period_key(label: str) -> tuple[str, tuple[int, ...]] | None:
    """(kind, sortable tuple) for a period label, or None when it cannot be ordered."""
    text = label.strip()
    m = _QUARTER_A.match(text) or None
    if m:
        return "quarter", (int(m.group(1)), int(m.group(2)))
    m = _QUARTER_B.match(text)
    if m:
        return "quarter", (int(m.group(2)), int(m.group(1)))
    m = _MONTH.match(text)
    if m:
        return "month", (int(m.group(1)), int(m.group(2)))
    m = _WEEK.match(text)
    if m:
        return "week", (int(m.group(1)), int(m.group(2)))
    if re.fullmatch(r"\d{4}", text):
        return "year", (int(text),)
    d = _parse_date(text)
    if d is not None:
        return "date", (d.year, d.month, d.day)
    return None


def _year_ago(kind: str, key: tuple[int, ...]) -> tuple[int, ...]:
    return (key[0] - 1,) + tuple(key[1:])


def _op_period_compare(ctx: _Ctx) -> dict[str, Any]:
    args = ctx.args
    period_arg = args.get("period_column")
    if not period_arg:
        raise ToolError("period_column is required: a quarter/month/date column, or month:<date column>")
    specs = _by_specs(period_arg, ctx.header, "period_column")
    if len(specs) != 1:
        raise ToolError("period_column takes exactly one column")
    metric_names = _list(args.get("metrics"), "metrics")
    if not metric_names:
        raise ToolError("metrics is required: the numeric columns to compare between periods")
    metrics = [_column(m, ctx.header, "metrics") for m in metric_names]
    sample = ctx.col("sample_column", required=False)
    agg = str(args.get("agg") or "sum").strip().lower()
    if agg not in {"sum", "mean"}:
        raise ToolError("agg must be sum or mean")
    values = {m: _metric_values(ctx.rows, m, ctx.missing) for m in metrics}
    sample_values = _metric_values(ctx.rows, sample, ctx.missing) if sample else None
    buckets = _buckets(ctx.rows, specs, ctx.missing)
    buckets.pop(BLANK_GROUP, None)
    keyed = []
    unordered = []
    for label in buckets:
        pk = _period_key(label)
        if pk is None:
            unordered.append(label)
        else:
            keyed.append((pk[0], pk[1], label))
    if unordered:
        ctx.missing.append(f"{_plural(len(unordered), 'period')} could not be ordered and were left out: {', '.join(unordered[:5])}.")
    keyed.sort(key=lambda t: t[1])
    periods = []
    for kind, key, label in keyed:
        group_rows = buckets[label]
        mvals = {}
        for m in metrics:
            nums = [values[m][id(r)] for r in group_rows if id(r) in values[m]]
            mvals[m] = None if not nums else _num(sum(nums) if agg == "sum" else (_mean(nums) or 0.0))
        entry: dict[str, Any] = {"period": label, "rows": len(group_rows), "metrics": mvals}
        if sample_values is not None:
            nums = [sample_values[id(r)] for r in group_rows if id(r) in sample_values]
            entry["sample"] = None if not nums else _num(sum(nums))
        periods.append(entry)

    def change(cur: dict[str, Any], ref: dict[str, Any] | None) -> dict[str, Any] | None:
        if ref is None:
            return None
        out = {}
        for m in metrics:
            a, b = ref["metrics"].get(m), cur["metrics"].get(m)
            if a is None or b is None:
                out[m] = {"abs": None, "pct": None}
            else:
                out[m] = {"abs": _num(b - a), "pct": None if a == 0 else round((b - a) / abs(a) * 100, 1)}
        return out

    latest = periods[-1] if periods else None
    previous = periods[-2] if len(periods) >= 2 else None
    year_ago = None
    year_ago_reason = None
    if latest:
        kind, key = keyed[-1][0], keyed[-1][1]
        target = _year_ago(kind, key)
        for (k, kk, label), p in zip(keyed, periods):
            if kk == target:
                year_ago = p
        if year_ago is None:
            year_ago_reason = f"no period one year before {latest['period']} in the file"
    else:
        year_ago_reason = "no periods"
    out = {
        "period_column": _by_label(specs),
        "metrics_columns": metrics,
        "sample_column": sample,
        "agg": agg,
        "periods": periods,
        "latest": latest,
        "previous_period": previous["period"] if previous else None,
        "change_vs_previous": change(latest, previous) if latest else None,
        "year_ago_period": year_ago["period"] if year_ago else None,
        "change_vs_year_ago": change(latest, year_ago) if latest and year_ago else None,
        "change_vs_year_ago_reason": year_ago_reason,
        "method": f"Each metric is the {agg} of its column over the rows of a period; pct change = (latest − reference) ÷ |reference| × 100; year-ago match is the same period label one year earlier.",
    }
    if latest and previous:
        parts = []
        for m in metrics:
            c = out["change_vs_previous"][m]
            parts.append(f"{m} {_fmt(latest['metrics'][m]) if latest['metrics'][m] is not None else 'n/a'}" + (f" ({c['pct']:+g} % vs {previous['period']})" if c["pct"] is not None else ""))
        summary = f"{latest['period']}: " + ", ".join(parts)
        if year_ago:
            ya = out["change_vs_year_ago"][metrics[0]]
            if ya["pct"] is not None:
                summary += f"; {metrics[0]} {ya['pct']:+g} % vs {year_ago['period']}"
        if latest.get("sample") is not None:
            summary += f"; sample {_fmt(latest['sample'])} {sample}"
        summary += "."
    elif latest:
        summary = f"Only one period ({latest['period']}) in the file, so there is nothing to compare against."
    else:
        summary = "No periods could be read from the file."
    out["summary"] = summary
    return out


def _wilson(k: int, n: int) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = k / n
    z2 = _Z95**2
    centre = (p + z2 / (2 * n)) / (1 + z2 / n)
    half = _Z95 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def _op_proportion_diff(ctx: _Ctx) -> dict[str, Any]:
    args = ctx.args
    outcome = ctx.col("outcome_column")
    positives_arg = _list(args.get("positive_values"), "positive_values")
    positives = {p.casefold() for p in (positives_arg or sorted(TRUTHY))}
    group_col = ctx.col("group_column", required=False)
    blanks = 0
    outcomes: list[tuple[dict[str, Any], bool]] = []
    for r in ctx.rows:
        text = _text(r.get(outcome))
        if not text:
            blanks += 1
            continue
        outcomes.append((r, text.casefold() in positives))
    if blanks:
        ctx.missing.append(f"{_plural(blanks, 'row')} with a blank {outcome} left out.")
    out: dict[str, Any] = {"outcome_column": outcome, "positive_values": sorted(positives)}
    if group_col is None:
        n = len(outcomes)
        k = sum(1 for _, pos in outcomes if pos)
        ci = _wilson(k, n)
        out.update(
            {
                "n": n,
                "positives": k,
                "share_pct": _pct(k, n),
                "ci95_pct": None if ci is None else [round(ci[0] * 100, 1), round(ci[1] * 100, 1)],
                "method": "One proportion with a Wilson 95 % interval; no group_column, so no comparison.",
            }
        )
        invited = args.get("invited")
        if invited:
            try:
                out["invited"] = int(invited)
                out["response_rate_pct"] = _pct(n, int(invited))
            except (TypeError, ValueError) as exc:
                raise ToolError("invited must be a whole number") from exc
        if n and n < ctx.min_rows:
            ctx.warnings.append(f"Only {n} answers: below {ctx.min_rows}, the share is rough.")
        out["summary"] = (
            f"{_fmt(k)} of {_fmt(n)} rows ({out['share_pct'] if out['share_pct'] is not None else 'n/a'} %) are positive on {outcome}"
            + (f", 95 % CI {out['ci95_pct'][0]}–{out['ci95_pct'][1]} %" if ci else "")
            + (f"; response rate {out['response_rate_pct']} % of {out['invited']} invited" if out.get("response_rate_pct") is not None else "")
            + "."
        )
        return out
    levels = []
    for r, _ in outcomes:
        text = _text(r.get(group_col))
        if text and text not in levels:
            levels.append(text)
    a_arg, b_arg = args.get("group_a"), args.get("group_b")
    if a_arg not in (None, "") and b_arg not in (None, ""):
        folded = {lv.casefold(): lv for lv in levels}
        try:
            group_a, group_b = folded[str(a_arg).strip().casefold()], folded[str(b_arg).strip().casefold()]
        except KeyError as exc:
            raise ToolError(f"group_a/group_b {a_arg!r}/{b_arg!r} not both found in {group_col}; values: {', '.join(levels) or '(none)'}") from exc
    elif len(levels) == 2:
        group_a, group_b = levels
    else:
        raise ToolError(
            f"{group_col} has {len(levels)} values ({', '.join(levels[:10]) or 'none'}); proportion_diff needs exactly two, or give group_a and group_b"
        )
    sides = {group_a: [0, 0], group_b: [0, 0]}
    for r, pos in outcomes:
        text = _text(r.get(group_col))
        if text in sides:
            sides[text][0] += 1
            sides[text][1] += pos
    n1, k1 = sides[group_a]
    n2, k2 = sides[group_b]
    out.update({"group_column": group_col, "group_a": group_a, "group_b": group_b, "n_a": n1, "n_b": n2, "positives_a": k1, "positives_b": k2})
    if n1 == 0 or n2 == 0:
        out.update({"p_a": None, "p_b": None, "diff_pp": None, "ci95_pp": None, "z": None, "p_value": None, "significant": None})
        out["method"] = "Two-proportion z-test; not computed because one group has no rows."
        out["summary"] = f"One of the groups ({group_a}: {n1}, {group_b}: {n2}) has no rows, so the proportions cannot be compared."
        return out
    p1, p2 = k1 / n1, k2 / n2
    diff = p2 - p1
    se_wald = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    pooled = (k1 + k2) / (n1 + n2)
    se_pooled = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    z = None if se_pooled == 0 else diff / se_pooled
    p_value = None if z is None else _norm_p_two_sided(z)
    out.update(
        {
            "p_a": round(p1, 4),
            "p_b": round(p2, 4),
            "diff_pp": round(diff * 100, 1),
            "ci95_pp": [round((diff - _Z95 * se_wald) * 100, 1), round((diff + _Z95 * se_wald) * 100, 1)],
            "z": _r(z),
            "p_value": _pv(p_value),
            "significant": None if p_value is None else p_value < 0.05,
            "method": "diff = p_b − p_a; Wald 95 % interval ± 1.96 × √(p_a(1−p_a)/n_a + p_b(1−p_b)/n_b); two-proportion z-test with pooled p, two-sided p from the normal distribution (erfc). α = 0.05.",
        }
    )
    if min(n1, n2) < ctx.min_rows:
        ctx.warnings.append(f"A group has fewer than {ctx.min_rows} rows ({group_a}: {n1}, {group_b}: {n2}): the test is rough.")
    if min(k1, n1 - k1, k2, n2 - k2) < 5:
        ctx.warnings.append("Fewer than 5 positives or negatives in a group: the normal approximation is weak.")
    verdict = "significant" if out["significant"] else "not significant"
    out["summary"] = (
        f"{group_b} {out['p_b'] * 100:.1f} % vs {group_a} {out['p_a'] * 100:.1f} % positive on {outcome} "
        f"(n {n2} vs {n1}): difference {out['diff_pp']:+g} pp, 95 % CI {out['ci95_pp'][0]:+g} to {out['ci95_pp'][1]:+g} pp"
        + (f", z {out['z']:g}, p = {out['p_value']:g}, {verdict} at α 0.05." if z is not None else ".")
    )
    return out


def _op_intervals(ctx: _Ctx) -> dict[str, Any]:
    start_col = ctx.col("start_column")
    end_col = ctx.col("end_column")
    days, invalid = [], 0
    for r in ctx.rows:
        s, e = _parse_date(r.get(start_col)), _parse_date(r.get(end_col))
        if s is None or e is None or e < s:
            invalid += 1
            continue
        days.append(float((e - s).days))
    if invalid:
        ctx.missing.append(f"{_plural(invalid, 'row')} dropped: start or end date unreadable, or the end before the start.")
    ordered = sorted(days)
    out = {
        "start_column": start_col,
        "end_column": end_col,
        "unit": "days",
        "n": len(days),
        "p25": _num(_percentile(ordered, 0.25)) if days else None,
        "p50": _num(_percentile(ordered, 0.5)) if days else None,
        "p75": _num(_percentile(ordered, 0.75)) if days else None,
        "p90": _num(_percentile(ordered, 0.9)) if days else None,
        "max": _num(ordered[-1]) if days else None,
        "mean": _num(_mean(days) or 0.0) if days else None,
        "n_dropped_invalid": invalid,
        "method": "Interval = end − start in whole days; percentiles by linear interpolation between order statistics.",
    }
    if len(days) and len(days) < ctx.min_rows:
        ctx.warnings.append(f"Only {len(days)} intervals: below {ctx.min_rows}, the percentiles are rough.")
    out["summary"] = (
        f"{_fmt(len(days))} intervals from {start_col} to {end_col}: median {_fmt(out['p50'])} days, p75 {_fmt(out['p75'])}, p90 {_fmt(out['p90'])}, max {_fmt(out['max'])}"
        + (f"; {invalid} invalid dropped" if invalid else "")
        + "."
        if days
        else f"No valid intervals from {start_col} to {end_col}" + (f"; {invalid} rows invalid." if invalid else ".")
    )
    return out


def _op_coverage(ctx: _Ctx) -> dict[str, Any]:
    specs = _by_specs(ctx.args.get("by"), ctx.header)
    shares_arg = ctx.args.get("population_shares")
    shares: dict[str, float] = {}
    if shares_arg not in (None, "", {}):
        if not isinstance(shares_arg, dict):
            raise ToolError("population_shares must be an object {group: fraction}")
        for k, v in shares_arg.items():
            try:
                shares[str(k).strip()] = float(v)
            except (TypeError, ValueError) as exc:
                raise ToolError(f"population_shares[{k!r}] is not a number") from exc
        total_share = sum(shares.values())
        if shares and abs(total_share - 1) > 0.02:
            if abs(total_share - 100) <= 2:
                shares = {k: v / 100 for k, v in shares.items()}
                ctx.warnings.append("population_shares looked like percentages and were divided by 100.")
            else:
                ctx.warnings.append(f"population_shares sum to {total_share:.3f}, not 1; ratios are read as given.")
    buckets = _buckets(ctx.rows, specs, ctx.missing)
    n = len(ctx.rows)
    folded = {k.casefold(): k for k in shares}
    groups = []
    seen_share_keys = set()
    for key, group_rows in sorted(buckets.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        share_key = folded.get(key.casefold())
        pop = shares[share_key] if share_key else None
        if share_key:
            seen_share_keys.add(share_key)
        list_share = len(group_rows) / n if n else None
        ratio = None if pop in (None, 0) or list_share is None else list_share / pop  # type: ignore[operator]
        if len(group_rows) < ctx.min_rows:
            status = "thin"
        elif ratio is None:
            status = "ok"
        elif ratio < 0.8:
            status = "under"
        elif ratio > 1.25:
            status = "over"
        else:
            status = "ok"
        groups.append(
            {
                "key": key,
                "rows": len(group_rows),
                "list_share_pct": None if list_share is None else round(list_share * 100, 1),
                "population_share_pct": None if pop is None else round(pop * 100, 1),
                "ratio": None if ratio is None else round(ratio, 2),
                "status": status,
            }
        )
    absent = [k for k in shares if k not in seen_share_keys]
    for k in absent:
        groups.append({"key": k, "rows": 0, "list_share_pct": 0.0, "population_share_pct": round(shares[k] * 100, 1), "ratio": 0.0, "status": "under"})
    if absent:
        ctx.missing.append(f"Groups in population_shares with no rows in the file: {', '.join(absent)}.")
    under = [g["key"] for g in groups if g["status"] == "under"]
    over = [g["key"] for g in groups if g["status"] == "over"]
    thin = [g["key"] for g in groups if g["status"] == "thin"]
    summary = f"Coverage by {_by_label(specs)} over {_fmt(n)} rows across {len(groups)} groups"
    if shares:
        summary += f": under-represented {', '.join(under) or 'none'}; over-represented {', '.join(over) or 'none'}"
    else:
        summary += "; no population_shares given, so only list shares are reported"
    if thin:
        summary += f"; thin (below {ctx.min_rows} rows): {', '.join(thin)}"
    return {
        "by": _by_label(specs),
        "groups": groups,
        "method": "ratio = list share ÷ population share; under when ratio < 0.8, over when > 1.25, thin when rows < min_rows (thin wins).",
        "summary": summary + ".",
    }


def _op_text_themes(ctx: _Ctx) -> dict[str, Any]:
    text_col = ctx.col("text_column")
    patterns_arg = ctx.args.get("patterns")
    if not isinstance(patterns_arg, dict) or not patterns_arg:
        raise ToolError("patterns is required: an object {theme: regex}, matched case-insensitively")
    compiled: dict[str, re.Pattern[str]] = {}
    for theme, pattern in patterns_arg.items():
        try:
            compiled[str(theme)] = re.compile(str(pattern), re.IGNORECASE)
        except re.error as exc:
            raise ToolError(f"pattern for theme {theme!r} is not a valid regex: {exc}") from exc
    specs = _by_specs(ctx.args["by"], ctx.header) if ctx.args.get("by") else None
    rows_with_text = []
    blank = 0
    for r in ctx.rows:
        text = _text(r.get(text_col))
        if not text:
            blank += 1
            continue
        rows_with_text.append((r, text))
    if blank:
        ctx.missing.append(f"{_plural(blank, 'row')} with a blank {text_col} left out.")
    n = len(rows_with_text)
    themes = []
    unmatched = 0
    group_counts: dict[str, dict[str, int]] = {}
    group_sizes: dict[str, int] = {}
    for r, text in rows_with_text:
        key = _row_key(r, specs)[0] if specs else None
        if key is not None:
            group_sizes[key] = group_sizes.get(key, 0) + 1
        if not any(p.search(text) for p in compiled.values()):
            unmatched += 1
    for theme, pattern in compiled.items():
        hits = [(r, text) for r, text in rows_with_text if pattern.search(text)]
        entry: dict[str, Any] = {
            "theme": theme,
            "n": len(hits),
            "share_pct": _pct(len(hits), n),
            "sample": [text[:SAMPLE_CHARS] for _, text in hits[:SAMPLE_ROWS]],
        }
        if specs:
            per_group: dict[str, int] = {}
            for r, _ in hits:
                key = _row_key(r, specs)[0]
                per_group[key] = per_group.get(key, 0) + 1
            entry["by_group"] = [
                {"key": k, "n": per_group.get(k, 0), "share_pct": _pct(per_group.get(k, 0), size), "thin": size < ctx.min_rows}
                for k, size in sorted(group_sizes.items(), key=lambda kv: (-kv[1], kv[0]))
            ]
        themes.append(entry)
    themes.sort(key=lambda t: (-t["n"], t["theme"]))
    out: dict[str, Any] = {
        "text_column": text_col,
        "by": _by_label(specs) if specs else None,
        "rows_with_text": n,
        "themes": themes,
        "unmatched": unmatched,
        "unmatched_pct": _pct(unmatched, n),
        "method": "Each theme is a case-insensitive regex searched in the text; a row can match several themes; shares are of rows with text.",
    }
    if specs:
        out["groups"] = [{"key": k, "rows": size, "thin": size < ctx.min_rows} for k, size in sorted(group_sizes.items(), key=lambda kv: (-kv[1], kv[0]))]
    head = ", ".join(f"{t['theme']} {t['n']} ({t['share_pct']:g} %)" for t in themes[:3] if t["share_pct"] is not None)
    out["summary"] = f"Of {_fmt(n)} rows with text in {text_col}: {head or 'no theme matched'}; {unmatched} ({out['unmatched_pct'] if out['unmatched_pct'] is not None else 0} %) matched no theme."
    return out


def _detect(header: list[str], explicit: Any, candidates: tuple[str, ...], what: str, required: bool) -> str | None:
    if explicit not in (None, ""):
        return _column(explicit, header, what)
    folded = {h.casefold(): h for h in header}
    for c in candidates:
        if c in folded:
            return folded[c]
    for h in header:
        if any(c in h.casefold() for c in candidates):
            return h
    if required:
        raise ToolError(f"could not find the {what} column (looked for {', '.join(candidates[:4])}); columns found: {', '.join(header)}. Give {what}_column.")
    return None


def _op_search_terms_split(ctx: _Ctx) -> dict[str, Any]:
    args = ctx.args
    brand_terms = _list(args.get("brand_terms"), "brand_terms")
    if not brand_terms:
        raise ToolError("brand_terms is required: the brand name and its spellings, matched as case-insensitive substrings")
    brands = [b.casefold() for b in brand_terms]
    term_col = _detect(ctx.header, args.get("term_column"), ADS_TERM_HEADERS, "term", True)
    cost_col = _detect(ctx.header, args.get("cost_column"), ADS_COST_HEADERS, "cost", False)
    conv_col = _detect(ctx.header, args.get("conversions_column"), ADS_CONV_HEADERS, "conversions", False)
    value_col = _detect(ctx.header, args.get("value_column"), ADS_VALUE_HEADERS, "value", False)
    if cost_col is None:
        ctx.warnings.append("No cost column found: spend, CPA and ROAS are not available.")
    rows = []
    totals = 0
    for r in ctx.rows:
        term = _text(r.get(term_col))
        if not term:
            continue
        if term.casefold().startswith("total"):
            totals += 1
            continue
        rows.append((r, term))
    if totals:
        ctx.missing.append(f"{_plural(totals, 'total row')} from the export left out.")
    if len(rows) < len(ctx.rows) - totals:
        ctx.missing.append(f"{_plural(len(ctx.rows) - totals - len(rows), 'row')} with a blank {term_col} left out.")
    numeric = {}
    for name, col in (("spend", cost_col), ("conversions", conv_col), ("value", value_col)):
        numeric[name] = _metric_values([r for r, _ in rows], col, ctx.missing) if col else {}

    def side(items: list[tuple[dict[str, Any], str]]) -> dict[str, Any]:
        def total(name: str) -> float | None:
            if not (name == "spend" and cost_col or name == "conversions" and conv_col or name == "value" and value_col):
                return None
            return sum(numeric[name].get(id(r), 0.0) for r, _ in items)

        spend, conv, value = total("spend"), total("conversions"), total("value")
        return {
            "rows": len(items),
            "spend": None if spend is None else _num(spend),
            "conversions": None if conv is None else _num(conv),
            "value": None if value is None else _num(value),
            "cpa": None if spend is None or not conv else _num(spend / conv),
            "roas": None if value is None or not spend else _num(value / spend),
        }

    branded = [(r, t) for r, t in rows if any(b in t.casefold() for b in brands)]
    non_branded = [(r, t) for r, t in rows if not any(b in t.casefold() for b in brands)]
    b_side, nb_side = side(branded), side(non_branded)
    total_spend = (b_side["spend"] or 0) + (nb_side["spend"] or 0) if cost_col else None
    total_conv = (b_side["conversions"] or 0) + (nb_side["conversions"] or 0) if conv_col else None
    b_side["share_of_spend_pct"] = _pct(b_side["spend"] or 0, total_spend) if total_spend is not None else None
    nb_side["share_of_spend_pct"] = _pct(nb_side["spend"] or 0, total_spend) if total_spend is not None else None
    b_side["share_of_conversions_pct"] = _pct(b_side["conversions"] or 0, total_conv) if total_conv is not None else None
    nb_side["share_of_conversions_pct"] = _pct(nb_side["conversions"] or 0, total_conv) if total_conv is not None else None
    top = []
    for r, t in non_branded:
        spend = numeric["spend"].get(id(r)) if cost_col else None
        conv = numeric["conversions"].get(id(r)) if conv_col else None
        top.append(
            {
                "term": t,
                "spend": None if spend is None else _num(spend),
                "conversions": None if conv is None else _num(conv),
                "cpa": None if not spend or not conv else _num(spend / conv),
            }
        )
    top.sort(key=lambda x: (-(x["spend"] or 0), -(x["conversions"] or 0), x["term"]))
    matched = {b: sum(1 for _, t in branded if b.casefold() in t.casefold()) for b in brand_terms}
    out = {
        "term_column": term_col,
        "columns": {"cost": cost_col, "conversions": conv_col, "value": value_col},
        "brand_terms": brand_terms,
        "brand_term_matches": matched,
        "branded": b_side,
        "non_branded": nb_side,
        "brand_share_of_spend_pct": b_side["share_of_spend_pct"],
        "top_non_branded": top[:10],
        "method": "A term is branded when it contains any brand term as a case-insensitive substring; CPA = spend ÷ conversions, ROAS = value ÷ spend; 'Total' rows of the export are excluded.",
    }
    if total_spend is not None:
        summary = (
            f"Branded terms take {b_side['share_of_spend_pct']:g} % of spend ({_fmt(b_side['spend'])} of {_fmt(_num(total_spend))}) "
            f"across {b_side['rows']} of {len(rows)} terms"
            + (f", CPA {_fmt(b_side['cpa'])} vs {_fmt(nb_side['cpa'])} non-branded" if b_side["cpa"] is not None and nb_side["cpa"] is not None else "")
            + (f", ROAS {_fmt(b_side['roas'])} vs {_fmt(nb_side['roas'])}" if b_side["roas"] is not None and nb_side["roas"] is not None else "")
            + "."
        )
    else:
        summary = f"{b_side['rows']} of {len(rows)} search terms contain a brand term; no cost column, so spend shares are not available."
    out["summary"] = summary
    return out


_OPS = {
    "describe": _op_describe,
    "group": _op_group,
    "crosstab": _op_crosstab,
    "before_after": _op_before_after,
    "period_compare": _op_period_compare,
    "proportion_diff": _op_proportion_diff,
    "intervals": _op_intervals,
    "coverage": _op_coverage,
    "text_themes": _op_text_themes,
    "search_terms_split": _op_search_terms_split,
}


# --- entry points -------------------------------------------------------------


def table_analyze(tools: WorkspaceTools, args: dict) -> dict[str, Any]:
    """Run one ``op`` over a CSV/XLSX in the workspace and return numbers, gaps and a quotable summary.

    Common inputs: ``path``, ``sheet``, ``header_row``, ``date_column`` with
    ``date_from``/``date_to``, ``filter`` [{column, op, value}], ``min_rows`` (30).
    Ops and their inputs are described in the module docstring and in CLIENT_TOOL_SPECS.
    """
    args = args or {}
    op = str(args.get("op") or "").strip().lower()
    if op not in _OPS:
        raise ToolError(f"op must be one of {', '.join(OPS)}" + (f"; got {op!r}" if op else ""))
    path = str(args.get("path") or "").strip()
    if not path:
        raise ToolError("path is required: a CSV or XLSX file inside the workspace")
    try:
        min_rows = int(args.get("min_rows") if args.get("min_rows") not in (None, "") else DEFAULT_MIN_ROWS)
    except (TypeError, ValueError) as exc:
        raise ToolError("min_rows must be a whole number") from exc
    if min_rows < 1:
        raise ToolError("min_rows must be at least 1")

    header, rows, notes = _load_table(tools, path, args.get("sheet"), args.get("header_row"))
    missing: list[str] = list(notes)
    warnings: list[str] = []
    date_col = _column(args["date_column"], header, "date_column") if args.get("date_column") else None
    date_from = _parse_date(args.get("date_from")) if args.get("date_from") else None
    date_to = _parse_date(args.get("date_to")) if args.get("date_to") else None
    if args.get("date_from") and date_from is None:
        raise ToolError(f"date_from {args.get('date_from')!r} is not a date; use YYYY-MM-DD")
    if args.get("date_to") and date_to is None:
        raise ToolError(f"date_to {args.get('date_to')!r} is not a date; use YYYY-MM-DD")
    if date_from and date_to and date_from > date_to:
        raise ToolError("date_from is after date_to")
    if date_col is None and (date_from or date_to):
        raise ToolError("date_from/date_to need a date_column")
    filters = _filters(args.get("filter"), header)

    kept, window, without = _window(rows, date_col, date_from, date_to)
    if without:
        missing.append(f"{_plural(without, 'row')} without a readable date in {date_col} left out of the window.")
    rows_in_window = len(kept)
    kept = _apply_filters(kept, filters)
    if not rows:
        missing.append("The file has a header but no data rows.")
    elif not kept:
        if rows_in_window == 0 and date_col:
            missing.append(
                f"No rows between {window['requested_from'] or 'the start'} and {window['requested_to'] or 'the end'}: the window is empty."
            )
        elif filters:
            missing.append("No rows pass the filters.")

    ctx = _Ctx(args, header, kept, min_rows, missing, warnings)
    result = _OPS[op](ctx)
    out: dict[str, Any] = {
        "op": op,
        "inputs": {
            "path": path,
            "sheet": args.get("sheet"),
            "min_rows": min_rows,
            "filter": filters,
        },
        "window": window,
        "rows_total": len(rows),
        "rows_in_window": rows_in_window,
        "rows_used": len(kept),
        "columns": header,
    }
    summary = result.pop("summary")
    method = result.pop("method", None)
    out.update(result)
    out["missing"] = missing
    out["warnings"] = warnings
    if method is not None:
        out["method"] = method
    out["summary"] = summary
    return out


def table_analyze_tool(tools: WorkspaceTools, args: dict) -> str:
    """Backs the worker-facing ``table_analyze`` tool: JSON the worker can quote from."""
    return json.dumps(table_analyze(tools, args or {}), ensure_ascii=False, indent=1)
