"""Read the marketer's own spreadsheet as data, so no worker summarises a table by eye.

``table_analyze`` is the general tabular tool behind survey audits, service-quality
gaps, complaint analysis, search-term splits, press benchmarks and before/after
checks. It reads a CSV or XLSX inside the workspace, narrows the rows by a date
window and filters, and runs one ``op`` over them: ``describe``, ``group``,
``crosstab``, ``before_after``, ``period_compare``, ``proportion_diff``,
``intervals``, ``coverage``, ``text_themes`` or ``search_terms_split``; and the
marketer's-file ops added for the 500-list: ``cohort_compare`` (an affected cohort
vs everyone else, first-event retention with censoring, a two-group effort
comparison), ``change_impact`` (pre / early / late windows around a change with a
noise band, change points and shared drops per segment), ``media_response``
(lagged spend–sales correlation and post-burst decay), ``price_response`` (arc and
cross elasticity), ``promo_effect`` (baseline, promo and post-promo dip per pack),
``choice_mix``, ``basket``, ``buyer_frequency`` (with and without outliers, NBD
bands, reporter split), ``panel`` (share stability, duplication law, denominator
guard), ``survey``, ``brand_recall``, ``keyword_demand``, ``page_breadth``,
``market_match``, ``campaign_compare``, ``session_log`` and ``customer_audit``.
``price_response`` and ``campaign_compare`` also take their numbers inline without a
file. Every result reports the dates actually covered, the rows used, the blanks
and bad cells it met under ``missing``, the method behind any statistic, and a
one-sentence ``summary`` written to be quoted. Groups below ``min_rows`` are listed
as thin and never ranked.

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
from datetime import date, timedelta
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
    "cohort_compare",
    "change_impact",
    "media_response",
    "price_response",
    "promo_effect",
    "choice_mix",
    "basket",
    "buyer_frequency",
    "panel",
    "survey",
    "brand_recall",
    "keyword_demand",
    "page_breadth",
    "market_match",
    "campaign_compare",
    "session_log",
    "customer_audit",
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
    def __init__(
        self, args: dict, header: list[str], rows: list[dict[str, Any]], min_rows: int, missing: list[str], warnings: list[str], tools: WorkspaceTools | None = None
    ):
        self.tools = tools
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


# --- helpers shared by the marketer's-file ops -------------------------------------

INLINE_OPS = ("price_response", "campaign_compare")
NBD_BANDS: tuple[tuple[int, int | None], ...] = ((0, 0), (1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 9), (10, None))
ROLE_LOCAL_PARTS = ("info", "office", "sales", "admin", "support", "contact", "hello", "noreply", "no-reply", "marketing", "hr")
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
PHONE_HEADERS = ("phone", "tel", "telefonas", "mobile", "mob")
EMAIL_HEADERS = ("email", "e-mail", "el. paštas", "el_pastas", "paštas", "mail")
NAME_HEADERS = ("name", "vardas", "customer", "klientas", "contact")
URL_HEADERS = ("top pages", "landing page", "page", "url", "puslapis", "address")
TRAFFIC_HEADERS = ("clicks", "sessions", "paspaudimai", "sesijos", "users", "visits")
CORRELATION_NOTE = "Correlation, not causation: a lagged correlation says the two series move together, not that spend caused sales."


def _int_of(args: dict, key: str, default: int, minimum: int = 1) -> int:
    value = args.get(key)
    if value in (None, ""):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ToolError(f"{key} must be a whole number") from exc
    if number < minimum:
        raise ToolError(f"{key} must be at least {minimum}")
    return number


def _float_of(args: dict, key: str, default: float | None = None) -> float | None:
    value = args.get(key)
    if value in (None, ""):
        return default
    number, status = _number(value)
    if status != "ok":
        raise ToolError(f"{key} must be a number")
    return number


def _date_of(args: dict, key: str, required: bool = False) -> date | None:
    value = args.get(key)
    if value in (None, ""):
        if required:
            raise ToolError(f"{key} is required (YYYY-MM-DD)")
        return None
    d = _parse_date(value)
    if d is None:
        raise ToolError(f"{key} {value!r} is not a date; use YYYY-MM-DD")
    return d


def _positive_set(args: dict, key: str = "positive_values") -> set[str]:
    items = _list(args.get(key), key)
    return {p.casefold() for p in (items or sorted(TRUTHY))}


def _is_positive(cell: Any, positives: set[str]) -> bool:
    text = _text(cell)
    return bool(text) and text.casefold() in positives


def _numbers(rows: list[dict[str, Any]], col: str) -> tuple[list[float], int, int]:
    """(values, blank, bad) for a numeric column, without touching ``missing``."""
    values: list[float] = []
    blank = bad = 0
    for r in rows:
        number, status = _number(r.get(col))
        if status == "ok":
            values.append(number)  # type: ignore[arg-type]
        elif status == "blank":
            blank += 1
        else:
            bad += 1
    return values, blank, bad


def _two_proportions(k1: int, n1: int, k2: int, n2: int) -> dict[str, Any]:
    """diff = p2 − p1 in pp with a Wald 95 % interval, pooled z and two-sided p; None when a side is empty."""
    if n1 == 0 or n2 == 0:
        return {"diff_pp": None, "ci95_pp": None, "z": None, "p_value": None}
    p1, p2 = k1 / n1, k2 / n2
    diff = p2 - p1
    se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    pooled = (k1 + k2) / (n1 + n2)
    se_pooled = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    z = None if se_pooled == 0 else diff / se_pooled
    return {
        "diff_pp": round(diff * 100, 1),
        "ci95_pp": [round((diff - _Z95 * se) * 100, 1), round((diff + _Z95 * se) * 100, 1)],
        "z": _r(z),
        "p_value": None if z is None else _pv(_norm_p_two_sided(z)),
    }


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx == 0 or syy == 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def _series(
    rows: list[dict[str, Any]], date_col: str, cols: list[str | None], missing: list[str]
) -> list[tuple[date, dict[str, float]]]:
    """One point per distinct date, rows on the same date summed per column; blanks count 0 and are reported."""
    points: dict[date, dict[str, float]] = {}
    undated = 0
    wanted: list[str] = []
    for c in cols:
        if c and c not in wanted:
            wanted.append(c)
    blanks = {c: 0 for c in wanted}
    for r in rows:
        d = _parse_date(r.get(date_col))
        if d is None:
            undated += 1
            continue
        slot = points.setdefault(d, {c: 0.0 for c in wanted})
        for c in wanted:
            number, status = _number(r.get(c))
            if status == "ok":
                slot[c] += number  # type: ignore[operator]
            else:
                blanks[c] += 1
    if undated:
        missing.append(f"{_plural(undated, 'row')} without a readable date in {date_col} left out.")
    for c, n in blanks.items():
        if n:
            missing.append(f"{c}: {_plural(n, 'blank or non-numeric cell')} counted as 0.")
    return sorted(points.items())


def _period_of(d: date, period: str) -> int:
    if period == "week":
        return (d - timedelta(days=d.weekday())).toordinal() // 7
    return d.year * 12 + d.month - 1


def _period_first_day(idx: int, period: str) -> date:
    if period == "week":
        return date.fromordinal(idx * 7 + 1)
    return date(idx // 12, idx % 12 + 1, 1)


def _period_name(idx: int, period: str) -> str:
    return _bucket(_period_first_day(idx, period), period)


def _period_arg(args: dict, key: str = "period", required: bool = False) -> str | None:
    value = args.get(key)
    if value in (None, ""):
        if required:
            raise ToolError(f"{key} is required: week or month")
        return None
    period = str(value).strip().lower()
    if period not in {"week", "month"}:
        raise ToolError(f"{key} must be week or month")
    return period


def _load_other(ctx: _Ctx, path_key: str, sheet_key: str) -> tuple[list[str], list[dict[str, Any]]]:
    if ctx.tools is None:
        raise ToolError(f"{path_key} needs a workspace to read from")
    path = str(ctx.args.get(path_key) or "").strip()
    header, rows, notes = _load_table(ctx.tools, path, ctx.args.get(sheet_key), None)
    ctx.missing.extend(f"{path}: {n}" for n in notes)
    return header, rows


def _events(ctx: _Ctx, id_col: str, date_col: str) -> list[tuple[str, date, dict[str, Any]]]:
    events = []
    blank_id = undated = 0
    for r in ctx.rows:
        ident = _text(r.get(id_col))
        if not ident:
            blank_id += 1
            continue
        d = _parse_date(r.get(date_col))
        if d is None:
            undated += 1
            continue
        events.append((ident, d, r))
    if blank_id:
        ctx.missing.append(f"{_plural(blank_id, 'row')} with a blank {id_col} left out.")
    if undated:
        ctx.missing.append(f"{_plural(undated, 'row')} without a readable date in {date_col} left out.")
    return events


def _pp(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+g} pp"


# --- cohort_compare (#135, #264, #453, #454) --------------------------------------------


def _cohort_members(ctx: _Ctx, events: list[tuple[str, date, dict[str, Any]]], id_col: str) -> tuple[set[str] | None, str]:
    args = ctx.args
    ids_given = _list(args.get("cohort_ids"), "cohort_ids")
    filters = _filters(args.get("cohort_filter"), ctx.header)
    first_from, first_to = _date_of(args, "cohort_first_from"), _date_of(args, "cohort_first_to")
    if not (ids_given or filters or first_from or first_to):
        return None, ""
    present: dict[str, str] = {}
    for ident, _, _ in events:
        present.setdefault(ident.casefold(), ident)
    members: set[str] = set()
    how = []
    if ids_given:
        wanted = {i.casefold() for i in ids_given}
        found = {present[w] for w in wanted if w in present}
        members |= found
        how.append(f"{len(wanted)} ids given")
        if len(wanted) - len(found):
            ctx.missing.append(f"{_plural(len(wanted) - len(found), 'cohort id')} not found in {id_col}.")
    if filters:
        members |= {ident for ident, _, r in events if all(_match(r.get(f["column"]), f["op"], f["value"]) for f in filters)}
        how.append("ids with a row matching " + ", ".join(f"{f['column']} {f['op']} {f['value']}" for f in filters))
    if first_from or first_to:
        first: dict[str, date] = {}
        for ident, d, _ in events:
            if ident not in first or d < first[ident]:
                first[ident] = d
        members |= {i for i, d in first.items() if (first_from is None or d >= first_from) and (first_to is None or d <= first_to)}
        how.append(f"first date between {first_from.isoformat() if first_from else 'the start'} and {first_to.isoformat() if first_to else 'the end'}")
    return members, "; ".join(how)


def _cohort_vs_others(
    ctx: _Ctx, events: list[tuple[str, date, dict[str, Any]]], members: set[str], how: str, success_col: str | None, positives: set[str]
) -> dict[str, Any]:
    args = ctx.args
    from_date = _date_of(args, "from_date") or (min(d for _, d, _ in events) if events else None)
    window_days = _int_of(args, "window_days", 0, 0) or None
    to_date = from_date + timedelta(days=window_days) if from_date and window_days else None
    min_cohort = _int_of(args, "min_cohort", 5)
    all_ids = {ident for ident, _, _ in events}
    sides = {"cohort": members & all_ids, "others": all_ids - members}
    returned: dict[str, set[str]] = {"cohort": set(), "others": set()}
    succeeded: dict[str, set[str]] = {"cohort": set(), "others": set()}
    for ident, d, r in events:
        if (from_date and d < from_date) or (to_date and d > to_date):
            continue
        side = "cohort" if ident in members else "others"
        returned[side].add(ident)
        if success_col and _is_positive(r.get(success_col), positives):
            succeeded[side].add(ident)
    n_c, n_o = len(sides["cohort"]), len(sides["others"])
    insufficient = n_c < min_cohort or n_o < ctx.min_rows
    block: dict[str, Any] = {
        "definition": how,
        "from_date": from_date.isoformat() if from_date else None,
        "to_date": to_date.isoformat() if to_date else None,
        "window_days": window_days,
        "status": "insufficient" if insufficient else "ok",
    }
    for side in ("cohort", "others"):
        n, k, s = len(sides[side]), len(returned[side]), len(succeeded[side])
        block[side] = {
            "n": n,
            "returned": k,
            "returned_pct": None if insufficient else _pct(k, n),
            "succeeded": s if success_col else None,
            "succeeded_pct": None if insufficient or not success_col else _pct(s, n),
        }
    if insufficient:
        block["returned_diff"] = block["succeeded_diff"] = None
        ctx.warnings.append(f"Cohort {n_c} ids and others {n_o}: below min_cohort {min_cohort} or min_rows {ctx.min_rows}, so no percentages or difference.")
        block["summary"] = f"Cohort {n_c} ids vs {n_o} others: too few to compare (status insufficient)."
        return block
    block["returned_diff"] = _two_proportions(len(returned["others"]), n_o, len(returned["cohort"]), n_c)
    block["succeeded_diff"] = _two_proportions(len(succeeded["others"]), n_o, len(succeeded["cohort"]), n_c) if success_col else None
    rd = block["returned_diff"]
    text = (
        f"{len(returned['cohort'])} of {n_c} cohort ids ({block['cohort']['returned_pct']:g} %) returned vs {block['others']['returned_pct']:g} % of {n_o} others"
        f" ({_pp(rd['diff_pp'])}, 95 % CI {rd['ci95_pp'][0]:+g} to {rd['ci95_pp'][1]:+g} pp)"
    )
    if success_col:
        sd = block["succeeded_diff"]
        text += f"; succeeded {block['cohort']['succeeded_pct']:g} % vs {block['others']['succeeded_pct']:g} % ({_pp(sd['diff_pp'])}, CI {sd['ci95_pp'][0]:+g} to {sd['ci95_pp'][1]:+g} pp)"
    block["summary"] = text + "."
    return block


def _retention(
    ctx: _Ctx, events: list[tuple[str, date, dict[str, Any]]], period: str, success_col: str | None, positives: set[str], counterparty_col: str | None
) -> dict[str, Any]:
    horizon = _int_of(ctx.args, "horizon_periods", 3)
    if not events:
        return {"period": period, "actors": 0, "events": 0, "cohorts": [], "periods": [], "summary": "No dated events, so no cohorts."}
    by_id: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for ident, d, r in events:
        by_id.setdefault(ident, []).append((_period_of(d, period), r))
    last_date = max(d for _, d, _ in events)
    first_idx = {i: min(p for p, _ in evs) for i, evs in by_id.items()}
    lo, hi = min(first_idx.values()), _period_of(last_date, period)
    single = sum(1 for evs in by_id.values() if len(evs) == 1)
    spans = sorted(float(max(p for p, _ in evs) - min(p for p, _ in evs) + 1) for evs in by_id.values())
    active_in: dict[int, set[str]] = {}
    for ident, evs in by_id.items():
        for p, _ in evs:
            active_in.setdefault(p, set()).add(ident)
    cohorts = []
    for c in sorted({v for v in first_idx.values()}):
        ids = {i for i, f in first_idx.items() if f == c}
        retention = []
        for k in range(1, horizon + 1):
            censored = _period_first_day(c + k, period) > last_date
            active = len(ids & active_in.get(c + k, set()))
            retention.append({"offset": k, "active": None if censored else active, "pct": None if censored else _pct(active, len(ids)), "censored": censored})
        cohorts.append({"cohort": _period_name(c, period), "size": len(ids), "thin": len(ids) < ctx.min_rows, "retention": retention})
    first_cohort = {i for i, f in first_idx.items() if f == lo}
    periods = []
    for idx in range(lo, hi + 1):
        rows_here = [(ident, r) for ident, evs in by_id.items() for p, r in evs if p == idx]
        active = {ident for ident, _ in rows_here}
        successes = [(ident, r) for ident, r in rows_here if success_col and _is_positive(r.get(success_col), positives)]
        pairs = None
        if counterparty_col:
            pairs = len({frozenset((ident, _text(r.get(counterparty_col)))) for ident, r in rows_here if _text(r.get(counterparty_col))})
        entry: dict[str, Any] = {
            "period": _period_name(idx, period),
            "new_ids": sum(1 for f in first_idx.values() if f == idx),
            "active_ids": len(active),
            "cumulative_registered": sum(1 for f in first_idx.values() if f <= idx),
            "events": len(rows_here),
            "ids_with_success": len({ident for ident, _ in successes}) if success_col else None,
            "success_events": len(successes) if success_col else None,
            "success_per_active": round(len(successes) / len(active), 3) if success_col and active else None,
            "connected_pairs": pairs,
            "from_first_cohort_pct": _pct(sum(1 for ident, _ in rows_here if ident in first_cohort), len(rows_here)),
            "thin": len(active) < ctx.min_rows,
        }
        periods.append(entry)
    solid = [p for p in periods if not p["thin"]]
    trend: dict[str, Any] | None
    if len(solid) >= 2:
        key = "success_per_active" if success_col else "active_ids"
        a, b = solid[0][key], solid[-1][key]
        trend = {
            "first_period": solid[0]["period"],
            "last_period": solid[-1]["period"],
            "metric": key,
            "change_pct": None if not a or b is None else round((b - a) / abs(a) * 100, 1),
        }
    else:
        trend = {"reason": f"fewer than 2 periods with at least {ctx.min_rows} active ids"}
    oldest = cohorts[0]
    at_horizon = oldest["retention"][-1]
    text = (
        f"{len(by_id)} ids, {len(events)} events by {period}: {single} ({_pct(single, len(by_id)):g} %) had a single event; "
        f"median active span {_fmt(_num(_median(spans) or 0.0))} periods; oldest cohort {oldest['cohort']} (n {oldest['size']}) "
        + (f"retained {at_horizon['pct']:g} % at +{horizon}" if at_horizon["pct"] is not None else f"censored at +{horizon} (data ends {last_date.isoformat()})")
    )
    censored_cohorts = [c["cohort"] for c in cohorts if c["retention"][-1]["censored"]]
    if censored_cohorts:
        text += f"; too young to judge at +{horizon}: {', '.join(censored_cohorts)}"
    if trend and "change_pct" in trend and trend["change_pct"] is not None:
        text += f"; {trend['metric']} {trend['change_pct']:+g} % from {trend['first_period']} to {trend['last_period']}"
    return {
        "period": period,
        "horizon_periods": horizon,
        "data_ends": last_date.isoformat(),
        "actors": len(by_id),
        "events": len(events),
        "single_event_share": {"count": single, "pct": _pct(single, len(by_id))},
        "active_span": {"median_periods": _num(_median(spans) or 0.0), "p75_periods": _num(_percentile(spans, 0.75) or 0.0)},
        "cohorts": cohorts,
        "periods": periods,
        "trend": trend,
        "summary": text + ".",
    }


def _compare_groups(ctx: _Ctx, compare_col: str, id_col: str, success_col: str | None, positives: set[str]) -> dict[str, Any]:
    args = ctx.args
    metrics = [_column(m, ctx.header, "metrics") for m in (_list(args.get("metrics"), "metrics") or [])]
    levels, keys = _levels(ctx.rows, compare_col, ctx.missing, "compare_column")
    groups = []
    for lv in levels:
        rows = [r for r, k in zip(ctx.rows, keys) if k == lv]
        entry: dict[str, Any] = {
            "value": lv,
            "n_rows": len(rows),
            "n_ids": len({_text(r.get(id_col)) for r in rows if _text(r.get(id_col))}),
            "metrics": {},
            "completion_rate_pct": None,
            "reliability": "ok" if len(rows) >= ctx.min_rows else f"unreliable (n<{ctx.min_rows})",
        }
        for m in metrics:
            vals, blank, bad = _numbers(rows, m)
            coverage = _pct(len(vals), len(rows))
            entry["metrics"][m] = {"n": len(vals), "mean": None if not vals else _num(_mean(vals) or 0.0), "median": None if not vals else _num(_median(vals) or 0.0), "coverage_pct": coverage}
            if rows and coverage is not None and coverage < 80:
                ctx.warnings.append(f"{compare_col}={lv}: {m} has a number in only {coverage:g} % of rows ({blank} blank, {bad} bad).")
        if success_col:
            answered = [r for r in rows if _text(r.get(success_col))]
            entry["completed"] = sum(1 for r in answered if _is_positive(r.get(success_col), positives))
            entry["completion_rate_pct"] = _pct(entry["completed"], len(answered))
            entry["answered"] = len(answered)
        groups.append(entry)
    a_arg, b_arg = args.get("group_a"), args.get("group_b")
    folded = {g["value"].casefold(): g for g in groups}
    if a_arg not in (None, "") and b_arg not in (None, ""):
        try:
            ga, gb = folded[str(a_arg).strip().casefold()], folded[str(b_arg).strip().casefold()]
        except KeyError as exc:
            raise ToolError(f"group_a/group_b not both found in {compare_col}; values: {', '.join(levels)}") from exc
    elif len(groups) >= 2:
        ga, gb = groups[0], groups[1]
    else:
        ga = gb = None
    difference: dict[str, Any] | None = None
    if ga and gb:
        difference = {"group_a": ga["value"], "group_b": gb["value"], "metrics": {}, "completion_diff": None}
        for m in metrics:
            ma, mb = ga["metrics"][m]["median"], gb["metrics"][m]["median"]
            if ma is None or mb is None:
                difference["metrics"][m] = {"absolute": None, "pct": None, "direction": None}
            else:
                diff = mb - ma
                difference["metrics"][m] = {"absolute": _num(diff), "pct": None if ma == 0 else round(diff / abs(ma) * 100, 1), "direction": "higher" if diff > 0 else "lower" if diff < 0 else "same"}
        if success_col:
            difference["completion_diff"] = _two_proportions(ga["completed"], ga["answered"], gb["completed"], gb["answered"])
        unreliable = [g["value"] for g in (ga, gb) if g["reliability"] != "ok"]
        if unreliable:
            ctx.warnings.append(f"Comparison unreliable: {', '.join(unreliable)} below {ctx.min_rows} rows.")
    parts = []
    for g in groups[:4]:
        bits = [f"{m} median {_fmt(g['metrics'][m]['median'])}" for m in metrics if g["metrics"][m]["median"] is not None]
        if g["completion_rate_pct"] is not None:
            bits.append(f"completion {g['completion_rate_pct']:g} %")
        parts.append(f"{compare_col}={g['value']} (n {g['n_rows']}): " + (", ".join(bits) or "no metric"))
    text = "; ".join(parts)
    if difference and metrics:
        m0 = metrics[0]
        dm = difference["metrics"][m0]
        if dm["absolute"] is not None:
            text += f"; {gb['value']} vs {ga['value']}: {m0} {dm['absolute']:+g} ({dm['pct']:+g} %)" if dm["pct"] is not None else f"; {m0} {dm['absolute']:+g}"
    if difference and difference["completion_diff"] and difference["completion_diff"]["diff_pp"] is not None:
        text += f", completion {_pp(difference['completion_diff']['diff_pp'])}"
    return {"compare_column": compare_col, "metrics_columns": metrics, "groups": groups, "difference": difference, "summary": text + "."}


def _op_cohort_compare(ctx: _Ctx) -> dict[str, Any]:
    args = ctx.args
    id_col = ctx.col("id_column")
    date_col = ctx.col("date_column")
    success_col = ctx.col("success_column", required=False)
    compare_col = ctx.col("compare_column", required=False)
    counterparty_col = ctx.col("counterparty_column", required=False)
    period = _period_arg(args)
    positives = _positive_set(args)
    events = _events(ctx, id_col, date_col)
    members, how = _cohort_members(ctx, events, id_col)
    if members is None and period is None and compare_col is None:
        raise ToolError(
            "cohort_compare needs a cohort (cohort_ids, cohort_filter or cohort_first_from/cohort_first_to), a period (week or month) for retention, or a compare_column"
        )
    out: dict[str, Any] = {"id_column": id_col, "date_column": date_col, "success_column": success_col, "positive_values": sorted(positives) if success_col else None, "ids": len({i for i, _, _ in events}), "events": len(events)}
    summaries = []
    methods = []
    if members is not None:
        out["cohort"] = _cohort_vs_others(ctx, events, members, how, success_col, positives)
        summaries.append(out["cohort"].pop("summary"))
        methods.append(
            "cohort vs others: an id 'returned' when it has a row dated from from_date (default: the first date) through from_date + window_days, 'succeeded' when such a row is positive on success_column; "
            "diff = cohort − others in pp with a Wald 95 % interval and a pooled two-proportion z; no percentages when the cohort has fewer than min_cohort (5) ids or the others fewer than min_rows"
        )
    if period:
        out["retention"] = _retention(ctx, events, period, success_col, positives, counterparty_col)
        summaries.append(out["retention"].pop("summary"))
        methods.append(
            f"retention: each id joins the {period} cohort of its first event; retention at +k is the share of the cohort with an event in cohort period + k, null and censored when that period starts after the last date in the data; "
            "per period: new ids (first event), active ids, success events ÷ active ids, distinct unordered id–counterparty pairs when counterparty_column is given; trend uses periods with at least min_rows active ids"
        )
    if compare_col:
        out["compare"] = _compare_groups(ctx, compare_col, id_col, success_col, positives)
        summaries.append(out["compare"].pop("summary"))
        methods.append("compare: rows split by compare_column; per group mean and median of each metric, completion rate = positives ÷ non-blank success_column; differences on medians (group_b − group_a) and completion in pp with a Wald interval")
    out["method"] = "; ".join(methods) + "."
    out["summary"] = " ".join(summaries)
    return out


# --- change_impact (#141, #338) ---------------------------------------------------------


def _change_point(dates: list[date], vals: list[float], rolling: int) -> dict[str, Any] | None:
    n = len(vals)
    if n < 2 * rolling:
        return None
    best: tuple[float, int, float, float] | None = None
    for i in range(rolling, n - rolling + 1):
        before = sum(vals[i - rolling : i]) / rolling
        after = sum(vals[i : i + rolling]) / rolling
        drop = before - after
        if best is None or drop > best[0]:
            best = (drop, i, before, after)
    assert best is not None
    drop, i, before, after = best
    if drop <= 0:
        return {"date": None, "before_mean": None, "after_mean": None, "drop_pct": None, "note": "no drop in the rolling mean"}
    return {"date": dates[i].isoformat(), "before_mean": _num(before), "after_mean": _num(after), "drop_pct": None if before == 0 else round((after - before) / before * 100, 1)}


def _impact_windows(
    points: list[tuple[date, dict[str, float]]], split: date, metric: str | None, complaints: str | None, exposure: str | None, cfg: dict[str, int | float]
) -> dict[str, Any]:
    pre = [p for p in points if p[0] < split]
    post = [p for p in points if p[0] >= split]
    early = post[: int(cfg["early"])]
    late = post[-int(cfg["late"]) :] if post else []

    def window(pts: list[tuple[date, dict[str, float]]]) -> dict[str, Any]:
        vals = [p[1][metric] for p in pts] if metric else []
        comp = sum(p[1][complaints] for p in pts) if complaints else None
        expo = sum(p[1][exposure] for p in pts) if exposure else None
        return {
            "periods": len(pts),
            "from": pts[0][0].isoformat() if pts else None,
            "to": pts[-1][0].isoformat() if pts else None,
            "metric_mean": None if not vals else _num(_mean(vals) or 0.0),
            "metric_sd": None if _sd(vals) is None else _num(_sd(vals) or 0.0),
            "complaints": None if comp is None else _num(comp),
            "exposure": None if expo is None else _num(expo),
            "complaint_rate_per_1000": None if comp is None or not expo else round(comp / expo * 1000, 1),
        }

    w_pre, w_early, w_late = window(pre), window(early), window(late)
    band = None
    recovered = None
    if metric and w_pre["metric_mean"] is not None and w_pre["metric_sd"] is not None:
        half = float(cfg["sd"]) * w_pre["metric_sd"]
        band = [_num(w_pre["metric_mean"] - half), _num(w_pre["metric_mean"] + half)]
        if w_late["metric_mean"] is not None:
            recovered = band[0] <= w_late["metric_mean"] <= band[1]
    ratio = None
    if w_pre["complaint_rate_per_1000"] and w_late["complaint_rate_per_1000"] is not None:
        ratio = round(w_late["complaint_rate_per_1000"] / w_pre["complaint_rate_per_1000"], 2)
    pval = None
    if complaints and exposure and w_pre["exposure"] and w_late["exposure"]:
        pval = _two_proportions(int(round(w_pre["complaints"])), int(round(w_pre["exposure"])), int(round(w_late["complaints"])), int(round(w_late["exposure"])))["p_value"]
    n_pre, n_post = len(pre), len(post)
    if n_pre < cfg["min_pre"] or n_post < cfg["min_post"]:
        need = max(0, int(cfg["min_pre"]) - n_pre), max(0, int(cfg["min_post"]) - n_post)
        verdict = "too_early" if n_pre >= cfg["min_pre"] else "insufficient_data"
        reason = f"{n_pre} pre and {n_post} post periods; needs {need[0]} more pre and {need[1]} more post."
    elif complaints and ratio is None and metric is None:
        verdict, reason = "insufficient_data", "complaint rate needs exposure_column, and there is no metric."
    else:
        complaints_fell = ratio is None or ratio < 2
        below = recovered is False and w_late["metric_mean"] is not None and w_late["metric_mean"] < (band[0] if band else 0)
        if complaints_fell and recovered in (True, None) and not (ratio is None and recovered is None):
            verdict = "adjustment"
        elif (ratio is not None and ratio >= 2) and (below or metric is None):
            verdict = "rejection"
        elif complaints is None and recovered is False:
            verdict = "rejection" if below else "mixed"
        else:
            verdict = "mixed"
        bits = []
        if ratio is not None:
            bits.append(f"late complaint rate {w_late['complaint_rate_per_1000']}/1000 is {ratio}× the pre rate {w_pre['complaint_rate_per_1000']}/1000")
        if recovered is not None:
            bits.append(f"late {metric} mean {_fmt(w_late['metric_mean'])} is {'inside' if recovered else 'outside'} the pre band {band[0]}–{band[1]}")
        reason = "; ".join(bits) + "."
    return {"pre": w_pre, "early": w_early, "late": w_late, "noise_band": band, "metric_recovered": recovered, "complaint_ratio_late_vs_pre": ratio, "complaint_p_value": pval, "verdict": verdict, "reason": reason}


def _op_change_impact(ctx: _Ctx) -> dict[str, Any]:
    args = ctx.args
    date_col = ctx.col("date_column")
    metric = ctx.col("metric", required=False)
    complaints = ctx.col("complaints_column", required=False)
    exposure = ctx.col("exposure_column", required=False)
    if metric is None and complaints is None:
        raise ToolError("change_impact needs metric (the behaviour series) and/or complaints_column (with exposure_column for a rate)")
    change = _date_of(args, "change_date")
    cfg = {
        "sd": _float_of(args, "noise_band_sd", 2.0),
        "early": _int_of(args, "early_periods", 2),
        "late": _int_of(args, "late_periods", 2),
        "min_pre": _int_of(args, "min_pre_periods", 4),
        "min_post": _int_of(args, "min_post_periods", 4),
    }
    rolling = _int_of(args, "rolling", 7)
    min_level = _float_of(args, "min_level", 30.0)
    shared_days = _int_of(args, "shared_drop_days", 3, 0)
    specs = _by_specs(args.get("by"), ctx.header) if args.get("by") else None
    cols = [metric, complaints, exposure]
    series_col = metric or complaints
    segments = []
    if specs:
        groups = _buckets(ctx.rows, specs, ctx.missing)
    else:
        groups = {"all": ctx.rows}
    for key, rows in groups.items():
        points = _series(rows, date_col, cols, ctx.missing)
        if not points:
            continue
        dates = [p[0] for p in points]
        vals = [p[1][series_col] for p in points]  # type: ignore[index]
        level = _mean(vals) or 0.0
        if specs and level < (min_level or 0):
            ctx.missing.append(f"{key}: mean {series_col} {_fmt(_num(level))} per period is below min_level {min_level:g}; not scored.")
            continue
        cp = _change_point(dates, vals, rolling)
        split = change or (_parse_date(cp["date"]) if cp and cp.get("date") else None)
        entry: dict[str, Any] = {"segment": key, "periods": len(points), "first": dates[0].isoformat(), "last": dates[-1].isoformat(), "mean_level": _num(level), "change_point": cp}
        if cp is None:
            ctx.warnings.append(f"{key}: {len(points)} periods is too short for a change point (needs 2 × rolling {rolling}).")
        if split is None:
            entry.update({"verdict": "insufficient_data", "reason": "no change_date given and no change point found."})
        else:
            entry["split_date"] = split.isoformat()
            entry.update(_impact_windows(points, split, metric, complaints, exposure, cfg))
        segments.append(entry)
    out: dict[str, Any] = {
        "date_column": date_col,
        "metric": metric,
        "complaints_column": complaints,
        "exposure_column": exposure,
        "change_date": change.isoformat() if change else None,
        "by": _by_label(specs) if specs else None,
        "settings": {**cfg, "rolling": rolling, "min_level": min_level, "shared_drop_days": shared_days},
    }
    scored = [s for s in segments if s.get("change_point") and s["change_point"].get("date")]
    if specs:
        out["segments"] = segments
        shared: bool | None = None
        reason = None
        if len(scored) < 2:
            reason = "fewer than two segments with a change point, so the drops cannot be compared across segments"
        else:
            drops = [_parse_date(s["change_point"]["date"]) for s in scored]
            spread = (max(drops) - min(drops)).days  # type: ignore[operator]
            big = [s for s in scored if (s["change_point"]["drop_pct"] or 0) <= -15]
            shared = spread <= shared_days and len(big) == len(scored)
            if not shared:
                odd = [s["segment"] for s in scored if s not in big]
                reason = f"drop dates spread over {spread} days" + (f"; without a drop over 15 %: {', '.join(odd)}" if odd else "")
        out["shared_drop"] = shared
        out["shared_drop_reason"] = reason
        big_drops = [s for s in scored if (s["change_point"]["drop_pct"] or 0) <= -15]
        if shared:
            out["drop_verdict"] = "shared"
        elif shared is False:
            out["drop_verdict"] = "single_segment" if len(big_drops) == 1 else "none"
        else:
            out["drop_verdict"] = None
        out["next_checks"] = ["robots.txt", "http_status", "meta_robots", "analytics_tag"] if shared else []
        head = "; ".join(
            f"{s['segment']} fell {s['change_point']['drop_pct']:g} % on {s['change_point']['date']} ({_fmt(s['change_point']['before_mean'])} → {_fmt(s['change_point']['after_mean'])})"
            for s in scored[:4]
        )
        summary = head or "no segment shows a drop"
        if shared is not None:
            summary += "; shared drop across segments" if shared else f"; not a shared drop ({reason})"
    else:
        seg = segments[0] if segments else None
        out.update({k: v for k, v in (seg or {}).items() if k != "segment"})
        if seg is None:
            out.update({"verdict": "insufficient_data", "reason": "no dated rows."})
            summary = "No dated rows, so nothing to compare."
        elif seg.get("pre"):
            pre, early, late = seg["pre"], seg["early"], seg["late"]
            bits = []
            if pre["complaint_rate_per_1000"] is not None:
                bits.append(f"complaints per 1 000: pre {pre['complaint_rate_per_1000']}, early {early['complaint_rate_per_1000']}, late {late['complaint_rate_per_1000']}")
            if pre["metric_mean"] is not None:
                bits.append(f"{metric}: pre {_fmt(pre['metric_mean'])}, early {_fmt(early['metric_mean']) if early['metric_mean'] is not None else 'n/a'}, late {_fmt(late['metric_mean']) if late['metric_mean'] is not None else 'n/a'}" + (f" (band {seg['noise_band'][0]}–{seg['noise_band'][1]}, {'recovered' if seg['metric_recovered'] else 'not recovered'})" if seg.get("metric_recovered") is not None else ""))
            summary = f"Around {seg['split_date']} ({pre['periods']} pre, {early['periods']} early, {late['periods']} late periods): " + "; ".join(bits) + f" → verdict {seg['verdict']}: {seg['reason']}"
        else:
            summary = f"Verdict {seg['verdict']}: {seg['reason']}"
    out["method"] = (
        f"Rows summed per date; change point = start of the window where the {rolling}-period rolling mean drops most against the preceding {rolling} periods; "
        "pre = periods before change_date (or the change point), early = the first early_periods after it, late = the last late_periods; complaint rate = complaints ÷ exposure × 1 000; "
        f"noise band = pre mean ± {cfg['sd']:g} sample sd; recovered when the late mean lies inside it; complaint p-value from a pooled two-proportion z on complaints over exposure, pre vs late. "
        "Verdict: too_early when fewer than min_post_periods after the change, adjustment when late complaints are under 2× pre and the metric is back in band, rejection when complaints stay ≥ 2× and the metric sits below the band, otherwise mixed. "
        "Shared drop when every scored segment's change point falls within shared_drop_days and every drop exceeds 15 %."
    )
    out["summary"] = summary + ("" if summary.endswith(".") else ".")
    return out


# --- media_response (#475) ----------------------------------------------------------------


def _op_media_response(ctx: _Ctx) -> dict[str, Any]:
    args = ctx.args
    date_col = ctx.col("date_column")
    metric = ctx.col("metric")
    spend_cols = [_column(c, ctx.header, "spend_columns") for c in (_list(args.get("spend_columns"), "spend_columns") or [])]
    if not spend_cols and args.get("spend_column"):
        spend_cols = [ctx.col("spend_column")]
    max_lag = _int_of(args, "max_lag", 4, 0)
    baseline_n = _int_of(args, "baseline_periods", 4)
    noise_sd = _float_of(args, "noise_sd", 1.0)
    max_post = _int_of(args, "max_post_periods", 12)
    points = _series(ctx.rows, date_col, [metric, *spend_cols], ctx.missing)
    dates = [p[0] for p in points]
    sales = [p[1][metric] for p in points]
    spend = [sum(p[1][c] for c in spend_cols) for p in points] if spend_cols else None
    out: dict[str, Any] = {"date_column": date_col, "metric": metric, "spend_columns": spend_cols, "periods": len(points)}
    lags = []
    if spend is not None:
        for lag in range(0, max_lag + 1):
            xs = spend[: len(spend) - lag] if lag else spend
            ys = sales[lag:]
            r = _pearson(xs, ys)
            lags.append({"lag": lag, "n": len(ys), "r": _r(r), "r_squared": None if r is None else _r(r * r)})
        best = max((lg for lg in lags if lg["r"] is not None), key=lambda lg: lg["r"], default=None)
        out["lag_correlation"] = {"lags": lags, "best_lag": None if best is None else best["lag"], "best_r": None if best is None else best["r"], "note": CORRELATION_NOTE}
    campaigns_arg = args.get("campaigns")
    campaigns: list[dict[str, Any]] = []
    if isinstance(campaigns_arg, list) and campaigns_arg:
        for item in campaigns_arg:
            if not isinstance(item, dict):
                raise ToolError("each campaign is an object {name, start, end}")
            start, end = _date_of(item, "start", True), _date_of(item, "end", True)
            if end < start:
                raise ToolError(f"campaign {item.get('name')}: end before start")
            campaigns.append({"name": str(item.get("name") or f"{start}–{end}"), "start": start, "end": end})
    elif spend is not None:
        run: list[int] = []
        for i, s in enumerate(spend + [0.0]):
            if s > 0:
                run.append(i)
            elif run:
                campaigns.append({"name": f"burst {len(campaigns) + 1}", "start": dates[run[0]], "end": dates[run[-1]]})
                run = []
        if campaigns:
            ctx.warnings.append(f"{len(campaigns)} burst(s) detected as runs of periods with spend > 0; give campaigns[] to pin them.")
    campaigns.sort(key=lambda c: c["start"])
    results = []
    for n, camp in enumerate(campaigns):
        idx_in = [i for i, d in enumerate(dates) if camp["start"] <= d <= camp["end"]]
        if not idx_in:
            ctx.missing.append(f"{camp['name']}: no periods between {camp['start']} and {camp['end']}.")
            continue
        pre = [sales[i] for i in range(0, idx_in[0])][-baseline_n:]
        entry: dict[str, Any] = {"name": camp["name"], "start": camp["start"].isoformat(), "end": camp["end"].isoformat(), "in_flight_periods": len(idx_in), "spend": None if spend is None else _num(sum(spend[i] for i in idx_in))}
        if len(pre) < baseline_n:
            entry.update({"baseline": None, "status": "insufficient_baseline", "reason": f"only {len(pre)} periods before the burst; needs {baseline_n}."})
            ctx.missing.append(f"{camp['name']}: {entry['reason']}")
            results.append(entry)
            continue
        base, base_sd = _mean(pre) or 0.0, _sd(pre) or 0.0
        in_flight = sum(sales[i] - base for i in idx_in)
        stop_at = campaigns[n + 1]["start"] if n + 1 < len(campaigns) else None
        post: list[dict[str, Any]] = []
        contaminated = False
        for i in range(idx_in[-1] + 1, len(dates)):
            if stop_at and dates[i] >= stop_at:
                contaminated = len(post) < max_post
                break
            excess = sales[i] - base
            if len(post) >= max_post or abs(excess) <= noise_sd * base_sd:
                break
            post.append({"date": dates[i].isoformat(), "excess": _num(excess)})
        if contaminated:
            ctx.warnings.append(f"{camp['name']}: post-period cut at {stop_at} where {campaigns[n + 1]['name']} starts; residual may be understated.")
        positive = [(k, p["excess"]) for k, p in enumerate(post) if p["excess"] > 0]
        fit: dict[str, Any] = {"retention_rate": None, "half_life_periods": None, "r_squared": None, "n_points": len(positive), "reason": None}
        if len(positive) >= 3:
            xs = [float(k) for k, _ in positive]
            ys = [math.log(e) for _, e in positive]
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            sxx = sum((x - mx) ** 2 for x in xs)
            slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
            rr = _pearson(xs, ys)
            r2 = None if rr is None else rr * rr
            rate = math.exp(slope)
            if r2 is not None and r2 >= 0.3 and 0 < rate < 1:
                fit.update({"retention_rate": round(rate, 3), "half_life_periods": round(math.log(0.5) / math.log(rate), 2), "r_squared": round(r2, 3)})
            else:
                fit.update({"r_squared": None if r2 is None else round(r2, 3), "reason": "fit too weak (r² < 0.3) or rate not between 0 and 1; no half-life"})
        else:
            fit["reason"] = f"only {len(positive)} positive post-burst periods; needs 3 for a decay fit"
        residual = sum(p["excess"] for p in post)
        total = in_flight + residual
        entry.update(
            {
                "baseline": {"periods": len(pre), "mean": _num(base), "sd": _num(base_sd)},
                "in_flight_excess": _num(in_flight),
                "post_burst": post,
                "residual_excess": _num(residual),
                "total_incremental": _num(total),
                "residual_share_pct": _pct(residual, total) if total > 0 else None,
                "decay_fit": fit,
                "status": "ok",
            }
        )
        results.append(entry)
    ok = [r for r in results if r.get("status") == "ok"]
    comparison = None
    if len(ok) >= 2:
        ranked = sorted(ok, key=lambda r: -r["residual_excess"])
        a, b = ranked[0], ranked[1]
        comparison = {
            "ranking": [r["name"] for r in ranked],
            "best": a["name"],
            "residual_difference": _num(a["residual_excess"] - b["residual_excess"]),
            "residual_difference_pct": None if not b["residual_excess"] else round((a["residual_excess"] - b["residual_excess"]) / abs(b["residual_excess"]) * 100, 1),
        }
    out.update({"campaigns": results, "comparison": comparison})
    bits = []
    for r in ok[:3]:
        hl = r["decay_fit"]["half_life_periods"]
        bits.append(f"{r['name']}: in-flight {_fmt(r['in_flight_excess'])}, residual {_fmt(r['residual_excess'])} over {len(r['post_burst'])} periods" + (f", half-life {hl:g}" if hl is not None else ", no half-life"))
    summary = "; ".join(bits)
    if comparison:
        summary += f"; {comparison['best']} left {_fmt(comparison['residual_difference'])} more after media stopped" + (f" ({comparison['residual_difference_pct']:+g} %)" if comparison["residual_difference_pct"] is not None else "")
    if out.get("lag_correlation"):
        lc = out["lag_correlation"]
        summary += (("; " if summary else "") + (f"spend–{metric} correlation strongest at lag {lc['best_lag']} (r {lc['best_r']:g}); correlation, not causation" if lc["best_r"] is not None else "no usable lag correlation"))
    out["method"] = (
        f"Rows summed per date. Baseline = mean of the {baseline_n} periods before the burst; in-flight excess = Σ(metric − baseline) over the burst; post-burst excess is followed until it falls within ±{noise_sd:g} baseline sd, {max_post} periods or the next burst; "
        "geometric decay fitted by least squares on ln(excess) over positive post periods (rate = e^slope, half-life = ln 0.5 ÷ ln rate, needs 3 points and r² ≥ 0.3). "
        f"Lag correlation = Pearson r between spend at t − lag and {metric} at t for lag 0..{max_lag}. " + CORRELATION_NOTE
    )
    out["summary"] = (summary or "No campaigns or spend to read.") + "."
    return out


# --- price_response (#178, #180) ----------------------------------------------------------


def _arc(a: float, b: float) -> float | None:
    mid = (a + b) / 2
    return None if mid == 0 else (b - a) / mid


def _side_from_rows(ctx: _Ctx, rows: list[dict[str, Any]], price_col: str, units_col: str, date_col: str | None, label: str) -> dict[str, Any] | None:
    prices, _, _ = _numbers(rows, price_col)
    units, blank, bad = _numbers(rows, units_col)
    if blank or bad:
        ctx.missing.append(f"{label}: {units_col} blank in {blank} and not a number in {bad} rows; those rows left out.")
    if not prices or not units:
        return None
    side: dict[str, Any] = {"rows": len(rows), "price": _num(_mean(prices) or 0.0), "units_per_period": _num(_mean(units) or 0.0), "units_total": _num(sum(units))}
    if len({round(p, 6) for p in prices}) > 1:
        side["price_levels"] = sorted({_num(p) for p in prices})
    if date_col:
        ds = sorted(d for d in (_parse_date(r.get(date_col)) for r in rows) if d)
        if ds:
            side["from"], side["to"], side["days"] = ds[0].isoformat(), ds[-1].isoformat(), (ds[-1] - ds[0]).days + 1
            side["months"] = sorted({d.month for d in ds})
    return side


def _op_price_response(ctx: _Ctx) -> dict[str, Any]:
    args = ctx.args
    caveats: list[str] = []
    before_arg, after_arg = args.get("before"), args.get("after")
    date_col = None
    if isinstance(before_arg, dict) and isinstance(after_arg, dict):
        sides = {}
        for label, item in (("before", before_arg), ("after", after_arg)):
            price, units = _float_of(item, "price"), _float_of(item, "units")
            if price is None or units is None:
                raise ToolError(f"{label} needs price and units")
            side: dict[str, Any] = {"price": _num(price), "units_per_period": _num(units), "units_total": _num(units), "rows": None}
            s, e = _date_of(item, "period_start"), _date_of(item, "period_end")
            if s and e:
                side["from"], side["to"], side["days"] = s.isoformat(), e.isoformat(), (e - s).days + 1
                side["months"] = sorted({(s + timedelta(days=k)).month for k in range(0, (e - s).days + 1, 7)} | {s.month, e.month})
            sides[label] = side
        before, after = sides["before"], sides["after"]
        source = "given before/after"
    else:
        if not ctx.header:
            raise ToolError("price_response needs before {price, units} and after {price, units}, or a path with price_column, units_column, date_column and split_date")
        price_col, units_col = ctx.col("price_column"), ctx.col("units_column")
        date_col = ctx.col("date_column")
        split = _date_of(args, "split_date", True)
        promo_col, stock_col = ctx.col("promo_column", required=False), ctx.col("stock_column", required=False)
        positives = _positive_set(args)
        rows = []
        promo = stock = 0
        for r in ctx.rows:
            if promo_col and _is_positive(r.get(promo_col), positives):
                promo += 1
                continue
            if stock_col and _is_positive(r.get(stock_col), positives):
                stock += 1
                continue
            rows.append(r)
        if promo:
            caveats.append(f"{_plural(promo, 'promo row')} excluded ({promo_col} positive).")
        if stock:
            caveats.append(f"{_plural(stock, 'stock-out row')} excluded ({stock_col} positive).")
        dated = [(r, _parse_date(r.get(date_col))) for r in rows]
        undated = sum(1 for _, d in dated if d is None)
        if undated:
            ctx.missing.append(f"{_plural(undated, 'row')} without a readable date in {date_col} left out.")
        b = _side_from_rows(ctx, [r for r, d in dated if d and d < split], price_col, units_col, date_col, "before")
        a = _side_from_rows(ctx, [r for r, d in dated if d and d >= split], price_col, units_col, date_col, "after")
        if b is None or a is None:
            raise ToolError(f"one side of {split.isoformat()} has no usable rows (before {'ok' if b else 'none'}, after {'ok' if a else 'none'})")
        before, after = b, a
        source = f"rows split at {split.isoformat()}"
        for side, label in ((before, "before"), (after, "after")):
            if side.get("price_levels"):
                caveats.append(f"{label}: {len(side['price_levels'])} price levels inside the window ({', '.join(str(p) for p in side['price_levels'])}); the side price is their mean.")
    if before.get("days") and after.get("days") and abs(before["days"] - after["days"]) > 0.2 * max(before["days"], after["days"]):
        caveats.append(f"unequal period length: {before['days']} vs {after['days']} days.")
    if before.get("months") and after.get("months") and set(before["months"]) != set(after["months"]):
        caveats.append("season mismatch: the two periods cover different months of the year.")
    out: dict[str, Any] = {"source": source, "before": before, "after": after, "caveats": caveats, "cross": [], "next_step": None}
    p0, p1, q0, q1 = before["price"], after["price"], before["units_per_period"], after["units_per_period"]
    if p0 == p1:
        out.update({"elasticity_arc": None, "elasticity_point": None, "classification": None, "next_step": "experiment_plan"})
        ctx.missing.append("second_price_period: both sides have the same price, so elasticity cannot be estimated; size a price test with experiment_plan.")
        out["method"] = "Arc elasticity needs two price levels; none here."
        out["summary"] = f"Price {p0} on both sides, so no elasticity; run a price test (experiment_plan)."
        return out
    dp, dq = _arc(p0, p1), _arc(q0, q1)
    e_arc = None if dp in (None, 0) or dq is None else dq / dp
    e_point = None if p0 == 0 or q0 == 0 else ((q1 - q0) / q0) / ((p1 - p0) / p0)
    if e_arc is None:
        classification = None
    elif abs(e_arc) > 1.05:
        classification = "elastic"
    elif abs(e_arc) < 0.95:
        classification = "inelastic"
    else:
        classification = "unit"
    rev0, rev1 = p0 * q0, p1 * q1
    out.update(
        {
            "pct_change_price": round(dp * 100, 1) if dp is not None else None,
            "pct_change_units": round(dq * 100, 1) if dq is not None else None,
            "elasticity_arc": _r(e_arc, 2),
            "elasticity_point": _r(e_point, 2),
            "classification": classification,
            "revenue_before": _num(rev0),
            "revenue_after": _num(rev1),
            "revenue_change_pct": None if rev0 == 0 else round((rev1 - rev0) / abs(rev0) * 100, 1),
        }
    )
    if min(q0, q1) < 30:
        out["low_confidence"] = True
        ctx.warnings.append("fewer than 30 units per period on a side: the elasticity is rough.")
    vc = _float_of(args, "variable_cost")
    contribution_change = None
    if vc is not None:
        c0, c1 = (p0 - vc) * q0, (p1 - vc) * q1
        contribution_change = c1 - c0
        out["contribution_before"], out["contribution_after"], out["contribution_change"] = _num(c0), _num(c1), _num(contribution_change)
    related = args.get("related")
    range_total = contribution_change
    cross = []
    if isinstance(related, list):
        for item in related:
            if not isinstance(item, dict):
                raise ToolError("each related item is an object {name, units_before, units_after, price_before?, price_after?, variable_cost?}")
            ub, ua = _float_of(item, "units_before"), _float_of(item, "units_after")
            if ub is None or ua is None:
                ctx.missing.append(f"related {item.get('name')}: units_before/units_after missing; range effect unknown.")
                continue
            dq_r = _arc(ub, ua)
            e_cross = None if dq_r is None or dp in (None, 0) else dq_r / dp
            kind = None if e_cross is None else ("substitute" if e_cross > 0 else "complement" if e_cross < 0 else "independent")
            pb = _float_of(item, "price_before")
            pa = _float_of(item, "price_after", pb)
            rvc = _float_of(item, "variable_cost")
            entry: dict[str, Any] = {"name": str(item.get("name") or "related"), "units_before": _num(ub), "units_after": _num(ua), "pct_change_units": None if dq_r is None else round(dq_r * 100, 1), "cross_elasticity": _r(e_cross, 2), "type": kind}
            if pb is not None and rvc is not None and pa is not None:
                cc = (pa - rvc) * ua - (pb - rvc) * ub
                entry["contribution_change"] = _num(cc)
                range_total = (range_total or 0.0) + cc if contribution_change is not None else None
                if contribution_change is None:
                    ctx.missing.append("variable_cost for the repriced product is missing, so the range total is conditional.")
            else:
                entry["contribution_change"] = None
                ctx.missing.append(f"related {entry['name']}: price_before and variable_cost needed for its contribution change.")
                range_total = None
            cross.append(entry)
    out["cross"] = cross
    out["range_contribution_change"] = None if range_total is None or not cross else _num(range_total)
    out["method"] = (
        "Arc (midpoint) elasticity = (ΔQ ÷ mean Q) ÷ (ΔP ÷ mean P); point elasticity = (ΔQ ÷ Q₀) ÷ (ΔP ÷ P₀); units per period are the mean over each side's rows; elastic when |arc| > 1.05, inelastic when < 0.95. "
        "Revenue = price × units per period; contribution = (price − variable_cost) × units. Cross elasticity = arc %Δ units of the related product ÷ arc %Δ price of the repriced one; positive → substitute, negative → complement; "
        "range_contribution_change = own contribution change + Σ related contribution changes."
    )
    text = (
        f"Price {_fmt(p0)} → {_fmt(p1)} ({out['pct_change_price']:+g} % midpoint), units {_fmt(q0)} → {_fmt(q1)} per period ({out['pct_change_units']:+g} %): arc elasticity {out['elasticity_arc']} ({classification}); "
        f"revenue {_fmt(out['revenue_before'])} → {_fmt(out['revenue_after'])} ({out['revenue_change_pct']:+g} %)"
    )
    if contribution_change is not None:
        text += f"; contribution {out['contribution_change']:+,}" if isinstance(out["contribution_change"], int) else f"; contribution {out['contribution_change']:+,.2f}"
    if cross:
        text += "; related: " + ", ".join(f"{c['name']} cross {c['cross_elasticity']} ({c['type']})" for c in cross)
        if out["range_contribution_change"] is not None:
            text += f"; range contribution change {out['range_contribution_change']:+,}" if isinstance(out["range_contribution_change"], int) else f"; range contribution change {out['range_contribution_change']:+,.2f}"
    if caveats:
        text += f"; caveats: {len(caveats)}"
    out["summary"] = text + "."
    return out


# --- promo_effect (#199) ---------------------------------------------------------------


def _op_promo_effect(ctx: _Ctx) -> dict[str, Any]:
    args = ctx.args
    date_col, item_col, metric = ctx.col("date_column"), ctx.col("item_column"), ctx.col("metric")
    promo_item = _text(args.get("promo_item"))
    if not promo_item:
        raise ToolError("promo_item is required: the value of item_column that was promoted")
    promo_from, promo_to = _date_of(args, "promo_from", True), _date_of(args, "promo_to", True)
    if promo_to < promo_from:
        raise ToolError("promo_to is before promo_from")
    related = _list(args.get("related_items"), "related_items") or []
    baseline_n = _int_of(args, "baseline_periods", 8)
    tolerance = _float_of(args, "recovery_tolerance_pct", 10.0)
    by_item: dict[str, list[dict[str, Any]]] = {}
    blank = 0
    for r in ctx.rows:
        key = _text(r.get(item_col))
        if not key:
            blank += 1
            continue
        by_item.setdefault(key, []).append(r)
    if blank:
        ctx.missing.append(f"{_plural(blank, 'row')} with a blank {item_col} left out.")
    folded = {k.casefold(): k for k in by_item}
    all_points = _series(ctx.rows, date_col, [metric], [])
    calendar = [d for d, _ in all_points]
    promo_dates = [d for d in calendar if promo_from <= d <= promo_to]
    if not promo_dates:
        raise ToolError(f"no periods between {promo_from} and {promo_to} in {date_col}; dates run {calendar[0] if calendar else 'n/a'} to {calendar[-1] if calendar else 'n/a'}")
    post_n = _int_of(args, "post_periods", 2 * len(promo_dates))
    pre_dates = [d for d in calendar if d < promo_from][-baseline_n:]
    post_dates = [d for d in calendar if d > promo_to][:post_n]
    items_out = []
    portfolio = 0.0
    portfolio_ok = True
    for name in [promo_item, *related]:
        actual = folded.get(name.casefold())
        if actual is None:
            ctx.missing.append(f"item {name!r} not in {item_col}; values: {', '.join(list(by_item)[:10])}.")
            portfolio_ok = False
            continue
        pts = dict(_series(by_item[actual], date_col, [metric], ctx.missing))
        val = lambda d: pts.get(d, {}).get(metric, 0.0)  # noqa: E731
        entry: dict[str, Any] = {"name": actual, "role": "promoted" if actual.casefold() == promo_item.casefold() else "related", "baseline_periods": len(pre_dates), "promo_periods": len(promo_dates), "post_periods": len(post_dates)}
        if len(pre_dates) < 4 or len(post_dates) < 4:
            entry.update({"status": "insufficient", "reason": f"{len(pre_dates)} baseline and {len(post_dates)} post periods; needs 4 of each for a net verdict."})
            ctx.missing.append(f"{actual}: {entry['reason']}")
            portfolio_ok = False
            items_out.append(entry)
            continue
        base = _mean([val(d) for d in pre_dates]) or 0.0
        during = [val(d) for d in promo_dates]
        after = [val(d) for d in post_dates]
        during_mean, after_mean = _mean(during) or 0.0, _mean(after) or 0.0
        incremental = sum(v - base for v in during)
        lost = sum(v - base for v in after)
        recovery: int | str = "not yet"
        for k, v in enumerate(after, 1):
            if base and abs(v - base) / abs(base) * 100 <= tolerance:
                recovery = k
                break
        entry.update(
            {
                "status": "ok",
                "baseline_mean": _num(base),
                "during_mean": _num(during_mean),
                "after_mean": _num(after_mean),
                "lift_pct": None if base == 0 else round((during_mean - base) / abs(base) * 100, 1),
                "dip_pct": None if base == 0 else round((after_mean - base) / abs(base) * 100, 1),
                "trough_pct": None if base == 0 or not after else round((min(after) - base) / abs(base) * 100, 1),
                "recovery_periods": recovery,
                "incremental_during": _num(incremental),
                "change_after": _num(lost),
                "net_units": _num(incremental + lost),
            }
        )
        portfolio += incremental + lost
        items_out.append(entry)
    out = {
        "item_column": item_col,
        "metric": metric,
        "promo_item": promo_item,
        "promo_window": {"from": promo_from.isoformat(), "to": promo_to.isoformat(), "periods": len(promo_dates)},
        "baseline_window": {"from": pre_dates[0].isoformat() if pre_dates else None, "to": pre_dates[-1].isoformat() if pre_dates else None, "periods": len(pre_dates)},
        "post_window": {"from": post_dates[0].isoformat() if post_dates else None, "to": post_dates[-1].isoformat() if post_dates else None, "periods": len(post_dates)},
        "items": items_out,
        "portfolio_net_units": _num(portfolio) if portfolio_ok and items_out else None,
        "method": (
            f"Periods are the distinct dates in the file; baseline = mean {metric} over the {baseline_n} periods before promo_from; during = promo_from..promo_to; post = the {post_n} periods after promo_to. "
            "lift/dip % against baseline; incremental = Σ(during − baseline); change_after = Σ(post − baseline); net = incremental + change_after; recovery = first post period within ±recovery_tolerance_pct of baseline; "
            "portfolio net = Σ net over the promoted and related items (null when any item lacks 4 baseline or 4 post periods)."
        ),
    }
    ok_items = [i for i in items_out if i.get("status") == "ok"]
    if ok_items:
        p = ok_items[0]
        text = f"{p['name']}: baseline {_fmt(p['baseline_mean'])} per period, during {_fmt(p['during_mean'])} ({p['lift_pct']:+g} %), after {_fmt(p['after_mean'])} ({p['dip_pct']:+g} %), back within {tolerance:g} % after {p['recovery_periods']} period(s); net {p['net_units']:+,}"
        for i in ok_items[1:]:
            text += f"; {i['name']} net {i['net_units']:+,}"
        if out["portfolio_net_units"] is not None:
            text += f"; portfolio net {out['portfolio_net_units']:+,} {metric}"
    else:
        text = "No item has enough baseline and post periods for a verdict"
    out["summary"] = text + "."
    return out


# --- choice_mix (#169) -------------------------------------------------------------------


def _op_choice_mix(ctx: _Ctx) -> dict[str, Any]:
    args = ctx.args
    option_cols = [_column(c, ctx.header, "option_columns") for c in (_list(args.get("option_columns"), "option_columns") or [])]
    if not option_cols:
        raise ToolError("option_columns is required: one column per configurator option")
    outcome = ctx.col("outcome_column", required=False)
    positives = _positive_set(args)
    default_thr = _float_of(args, "default_share_threshold", 70.0)
    specify_thr = _float_of(args, "specify_share_threshold", 40.0)
    max_values = _int_of(args, "max_values", 50)
    columns = []
    for col in option_cols:
        counts: dict[str, int] = {}
        wins: dict[str, int] = {}
        answered: dict[str, int] = {}
        blank = 0
        for r in ctx.rows:
            v = _text(r.get(col))
            if not v:
                blank += 1
                continue
            counts[v] = counts.get(v, 0) + 1
            if outcome and _text(r.get(outcome)):
                answered[v] = answered.get(v, 0) + 1
                wins[v] = wins.get(v, 0) + _is_positive(r.get(outcome), positives)
        n = sum(counts.values())
        if blank:
            ctx.missing.append(f"{col}: {_plural(blank, 'blank cell')} left out.")
        entry: dict[str, Any] = {"column": col, "n": n, "blank": blank, "distinct": len(counts)}
        if len(counts) > max_values:
            entry.update({"values": [], "verdict": None, "reason": f"{len(counts)} distinct values exceed max_values {max_values}; not an option column"})
            ctx.missing.append(f"{col}: {entry['reason']}.")
            columns.append(entry)
            continue
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        values = [{"value": v, "count": c, "share_pct": _pct(c, n), "outcome_rate_pct": _pct(wins.get(v, 0), answered.get(v, 0)) if outcome else None, "thin": c < ctx.min_rows} for v, c in ordered]
        cumulative, k90 = 0, 0
        for v, c in ordered:
            cumulative += c
            k90 += 1
            if cumulative >= 0.9 * n:
                break
        top = ordered[0] if ordered else None
        top_share = _pct(top[1], n) if top else None
        if n < ctx.min_rows or top_share is None:
            verdict, reason = None, f"{n} rows is below min_rows {ctx.min_rows}; no verdict"
        elif top_share >= default_thr:
            verdict, reason = "shared_default", f"{top[0]} takes {top_share:g} % (≥ {default_thr:g} %)"
        elif top_share < specify_thr:
            verdict, reason = "buyers_specify", f"no value above {specify_thr:g} % (top {top[0]} {top_share:g} %)"
        else:
            verdict, reason = "unclear", f"top value {top[0]} at {top_share:g} % sits between {specify_thr:g} and {default_thr:g} %; test it"
        entry.update({"values": values, "top_value": top[0] if top else None, "top_share_pct": top_share, "values_for_90pct": k90 if ordered else None, "verdict": verdict, "reason": reason})
        if verdict is None and n:
            ctx.warnings.append(f"{col}: {reason}.")
        columns.append(entry)
    out = {
        "option_columns": option_cols,
        "outcome_column": outcome,
        "thresholds": {"shared_default_pct": default_thr, "buyers_specify_pct": specify_thr},
        "columns": columns,
        "method": (
            f"Per option column: share of non-blank rows per value; values_for_90pct = fewest top values covering 90 % of rows; verdict shared_default when the top value holds ≥ {default_thr:g} %, "
            f"buyers_specify when it holds < {specify_thr:g} %, otherwise unclear; no verdict below min_rows rows or above max_values distinct values. outcome_rate_pct = positives ÷ non-blank outcome rows per value."
        ),
    }
    bits = [f"{c['column']}: {c['top_value']} {c['top_share_pct']:g} % → {c['verdict'] or 'no verdict'}" for c in columns if c.get("top_value")]
    out["summary"] = ("; ".join(bits) or "no option column had values") + "."
    return out


# --- basket (#496) -------------------------------------------------------------------------


def _op_basket(ctx: _Ctx) -> dict[str, Any]:
    args = ctx.args
    order_col, sku_col = ctx.col("order_column"), ctx.col("sku_column")
    focus = _list(args.get("focus_skus"), "focus_skus")
    if not focus:
        raise ToolError("focus_skus is required: the sku values to anchor the baskets on")
    focus_set = {f.casefold() for f in focus}
    revenue_col = ctx.col("revenue_column", required=False)
    customer_col = ctx.col("customer_column", required=False)
    date_col = ctx.col("date_column", required=False)
    top = _int_of(args, "top", 10)
    baskets: dict[str, dict[str, Any]] = {}
    blank = 0
    for r in ctx.rows:
        oid, sku = _text(r.get(order_col)), _text(r.get(sku_col))
        if not oid or not sku:
            blank += 1
            continue
        b = baskets.setdefault(oid, {"skus": set(), "revenue": 0.0, "customer": None, "date": None})
        b["skus"].add(sku)
        if revenue_col:
            number, status = _number(r.get(revenue_col))
            if status == "ok":
                b["revenue"] += number  # type: ignore[operator]
        if customer_col and not b["customer"]:
            b["customer"] = _text(r.get(customer_col)) or None
        if date_col and b["date"] is None:
            b["date"] = _parse_date(r.get(date_col))
    if blank:
        ctx.missing.append(f"{_plural(blank, 'row')} with a blank {order_col} or {sku_col} left out.")
    total = len(baskets)
    with_focus = {oid: b for oid, b in baskets.items() if any(s.casefold() in focus_set for s in b["skus"])}
    nf = len(with_focus)
    found = {s for b in baskets.values() for s in b["skus"] if s.casefold() in focus_set}
    absent = [f for f in focus if f.casefold() not in {s.casefold() for s in found}]
    if absent:
        ctx.missing.append(f"focus skus not in the file: {', '.join(absent)}.")
    sku_all: dict[str, int] = {}
    sku_focus: dict[str, int] = {}
    for oid, b in baskets.items():
        for s in b["skus"]:
            sku_all[s] = sku_all.get(s, 0) + 1
            if oid in with_focus:
                sku_focus[s] = sku_focus.get(s, 0) + 1
    attached = []
    for s, k in sku_focus.items():
        if s.casefold() in focus_set:
            continue
        attach = _pct(k, nf)
        base = sku_all[s] / total if total else None
        attached.append({"sku": s, "baskets": k, "attach_rate_pct": attach, "base_rate_pct": _pct(sku_all[s], total), "lift": None if not base or attach is None else round(attach / 100 / base, 2), "thin": k < ctx.min_rows, "rank": None})
    ranked = sorted([a for a in attached if not a["thin"]], key=lambda a: (-a["attach_rate_pct"], a["sku"]))
    for i, a in enumerate(ranked, 1):
        a["rank"] = i
    thin = sorted([a for a in attached if a["thin"]], key=lambda a: (-a["baskets"], a["sku"]))
    if thin:
        ctx.warnings.append(f"{_plural(len(thin), 'attached sku')} below {ctx.min_rows} focus baskets listed as thin and not ranked.")
    if nf and nf < ctx.min_rows:
        ctx.warnings.append(f"Only {nf} baskets contain a focus sku: attach rates are thin.")
    out: dict[str, Any] = {
        "focus_skus": focus,
        "baskets_total": total,
        "baskets_with_focus": nf,
        "focus_share_pct": _pct(nf, total),
        "attached": (ranked + thin)[:top],
        "attached_total": len(attached),
    }
    if revenue_col:
        rev_with = [b["revenue"] for oid, b in baskets.items() if oid in with_focus]
        rev_without = [b["revenue"] for oid, b in baskets.items() if oid not in with_focus]
        out["revenue_per_basket"] = {"with_focus": None if not rev_with else _num(_mean(rev_with) or 0.0), "without_focus": None if not rev_without else _num(_mean(rev_without) or 0.0)}
    else:
        out["revenue_per_basket"] = None
        ctx.missing.append("no revenue_column: revenue per basket not available.")
    if customer_col:
        buyers: dict[str, list[date | None]] = {}
        for oid, b in with_focus.items():
            if b["customer"]:
                buyers.setdefault(b["customer"], []).append(b["date"])
        repeaters = sum(1 for ds in buyers.values() if len(ds) >= 2)
        gaps: list[float] = []
        for ds in buyers.values():
            dated = sorted(d for d in ds if d)
            gaps.extend(float((b2 - a2).days) for a2, b2 in zip(dated, dated[1:]))
        line_rev = None
        if revenue_col:
            focus_buyers = set(buyers)
            line_rev = sum(b["revenue"] for b in baskets.values() if b["customer"] in focus_buyers)
        out["repeat"] = {
            "focus_buyers": len(buyers),
            "repeat_buyers": repeaters,
            "repeat_rate_pct": _pct(repeaters, len(buyers)),
            "median_days_between_focus_orders": None if not gaps else _num(_median(gaps) or 0.0),
            "line_revenue_per_focus_buyer": None if line_rev is None or not buyers else _num(line_rev / len(buyers)),
        }
        if not date_col:
            ctx.missing.append("no date_column: days between orders not available.")
    else:
        out["repeat"] = "unavailable: no customer_column"
        ctx.missing.append("no customer_column: repeat rate not available.")
    out["method"] = (
        "A basket is the set of distinct skus under one order id; attach rate = focus baskets containing the sku ÷ focus baskets; base rate = baskets containing it ÷ all baskets; lift = attach rate ÷ base rate; "
        "skus in fewer than min_rows focus baskets are thin and unranked. Repeat rate = focus buyers with 2+ focus baskets ÷ focus buyers; days between consecutive focus orders by median."
    )
    text = f"{nf} of {_fmt(total)} baskets ({out['focus_share_pct']:g} %) contain {', '.join(focus)}" if out["focus_share_pct"] is not None else f"{nf} of {_fmt(total)} baskets contain {', '.join(focus)}"
    if ranked:
        text += "; top attached: " + ", ".join(f"{a['sku']} {a['attach_rate_pct']:g} % (lift {a['lift']})" for a in ranked[:3])
    if out["revenue_per_basket"] and out["revenue_per_basket"]["with_focus"] is not None and out["revenue_per_basket"]["without_focus"] is not None:
        text += f"; revenue per basket {_fmt(out['revenue_per_basket']['with_focus'])} with vs {_fmt(out['revenue_per_basket']['without_focus'])} without"
    if isinstance(out["repeat"], dict) and out["repeat"]["repeat_rate_pct"] is not None:
        text += f"; {out['repeat']['repeat_rate_pct']:g} % of {out['repeat']['focus_buyers']} focus buyers bought again" + (f", median {_fmt(out['repeat']['median_days_between_focus_orders'])} days apart" if out["repeat"]["median_days_between_focus_orders"] is not None else "")
    out["summary"] = text + "."
    return out


# --- buyer_frequency (#466, #467, #468) ---------------------------------------------------


def _nbd_pmf(k: float, p: float, x: int) -> float:
    return math.exp(math.lgamma(x + k) - math.lgamma(k) - math.lgamma(x + 1) + k * math.log(p) + x * math.log(1 - p))


def _frequency_block(counts: list[int]) -> dict[str, Any]:
    n = len(counts)
    purchases = sum(counts)
    ordered = sorted(counts, reverse=True)
    half = ordered[: math.ceil(n / 2)]
    top20 = ordered[: max(1, math.ceil(n * 0.2))] if n else []
    return {
        "buyers": n,
        "purchases": purchases,
        "mean_frequency": None if not n else round(purchases / n, 2),
        "median_frequency": None if not n else _num(_median([float(c) for c in counts]) or 0.0),
        "heavy_half_share_pct": _pct(sum(half), purchases),
        "top_20pct_buyers_share_pct": _pct(sum(top20), purchases),
    }


def _op_buyer_frequency(ctx: _Ctx) -> dict[str, Any]:
    args = ctx.args
    id_col = ctx.col("id_column")
    qty_col = ctx.col("qty_column", required=False)
    group_col = ctx.col("group_column", required=False)
    per_id: dict[str, dict[str, Any]] = {}
    blank = 0
    for r in ctx.rows:
        ident = _text(r.get(id_col))
        if not ident:
            blank += 1
            continue
        slot = per_id.setdefault(ident, {"purchases": 0, "qty": 0.0, "group": None})
        slot["purchases"] += 1
        if qty_col:
            number, status = _number(r.get(qty_col))
            if status == "ok":
                slot["qty"] += number  # type: ignore[operator]
        if group_col and not slot["group"]:
            slot["group"] = _text(r.get(group_col)) or None
    if blank:
        ctx.missing.append(f"{_plural(blank, 'row')} with a blank {id_col} left out.")
    if not qty_col:
        ctx.missing.append("no qty_column: outliers are judged on order count only.")
    counts = [v["purchases"] for v in per_id.values()]
    n = len(counts)
    out: dict[str, Any] = {"id_column": id_col, "qty_column": qty_col, "group_column": group_col}
    if n < ctx.min_rows:
        ctx.warnings.append(f"Only {n} buyers: below {ctx.min_rows}, every figure is rough.")
    universe = _int_of(args, "universe", 0, 0) or None
    out["penetration_pct"] = _pct(n, universe) if universe else None
    if not universe:
        ctx.missing.append("no universe given, so no penetration.")
    rule = args.get("outlier_rule") if isinstance(args.get("outlier_rule"), dict) else {}
    method = str(rule.get("method") or "iqr").lower()
    if method not in {"iqr", "multiple_of_median", "zscore"}:
        raise ToolError("outlier_rule.method must be iqr, multiple_of_median or zscore")
    threshold = _float_of(rule, "threshold", {"iqr": 3.0, "multiple_of_median": 10.0, "zscore": 3.0}[method])
    outliers: list[dict[str, Any]] = []
    cut: float | None = None
    if n >= 4:
        sorted_counts = sorted(float(c) for c in counts)
        if method == "iqr":
            q1, q3 = _percentile(sorted_counts, 0.25) or 0.0, _percentile(sorted_counts, 0.75) or 0.0
            cut = q3 + threshold * (q3 - q1)  # type: ignore[operator]
        elif method == "multiple_of_median":
            cut = (_median(sorted_counts) or 0.0) * threshold  # type: ignore[operator]
        else:
            m, s = _mean(sorted_counts) or 0.0, _sd(sorted_counts) or 0.0
            cut = m + threshold * s  # type: ignore[operator]
        total_purchases = sum(counts)
        for ident, v in per_id.items():
            if v["purchases"] > cut and v["purchases"] > 1:
                outliers.append({"id": ident, "purchases": v["purchases"], "qty": _num(v["qty"]) if qty_col else None, "group": v["group"], "share_of_purchases_pct": _pct(v["purchases"], total_purchases), "decision": "investigate"})
        outliers.sort(key=lambda o: -o["purchases"])
    out["outlier_rule"] = {"method": method, "threshold": threshold, "cut_above": None if cut is None else _num(cut)}
    out["outliers"] = outliers
    with_all = _frequency_block(counts)
    excluded = {o["id"] for o in outliers}
    without = _frequency_block([v["purchases"] for i, v in per_id.items() if i not in excluded])
    out["with_outliers"], out["without_outliers"] = with_all, without
    out["delta_pct"] = None if not with_all["mean_frequency"] or without["mean_frequency"] is None else round((without["mean_frequency"] - with_all["mean_frequency"]) / with_all["mean_frequency"] * 100, 1)
    dist: dict[int, int] = {}
    for c in counts:
        dist[c] = dist.get(c, 0) + 1
    out["distribution"] = [{"purchases": k, "buyers": v, "share_pct": _pct(v, n)} for k, v in sorted(dist.items())][:50]
    # negative binomial fit (method of moments) on counts, with the zero class when population_size is given
    population = _int_of(args, "population_size", 0, 0) or None
    fitted_counts = list(counts)
    zero_class = "absent"
    if population and population > n:
        fitted_counts += [0] * (population - n)
        zero_class = "added"
    elif 0 in dist:
        zero_class = "present"
    nbd: dict[str, Any] = {"k": None, "p": None, "fit": None, "zero_class": zero_class}
    bands: list[dict[str, Any]] = []
    if len(fitted_counts) < 50:
        nbd["fit"] = "too_few_buyers"
    else:
        m = _mean([float(c) for c in fitted_counts]) or 0.0
        v = (_sd([float(c) for c in fitted_counts]) or 0.0) ** 2
        nbd["mean"], nbd["variance"], nbd["dispersion"] = _num(m), _num(v), None if not m else round(v / m, 2)
        if v <= m or m == 0:
            nbd["fit"] = "not_overdispersed"
        else:
            k, p = m * m / (v - m), m / v
            nbd.update({"k": round(k, 3), "p": round(p, 3), "fit": "ok" if zero_class != "absent" else "ok_conditional_on_buying"})
            total_n = len(fitted_counts)
            cum = 0.0
            for lo, hi in NBD_BANDS:
                observed = sum(1 for c in fitted_counts if c >= lo and (hi is None or c <= hi))
                if hi is None:
                    prob = max(0.0, 1 - cum)
                else:
                    prob = sum(_nbd_pmf(k, p, x) for x in range(lo, hi + 1))
                    cum += prob
                fitted = total_n * prob
                label = str(lo) if hi == lo else (f"{lo}+" if hi is None else f"{lo}–{hi}")
                bands.append({"band": label, "observed": observed, "fitted": _num(fitted), "diff_pct": None if fitted < 1e-9 else round((observed - fitted) / fitted * 100, 1), "divergent": observed >= 5 and fitted > 1e-9 and abs((observed - fitted) / fitted * 100) > 20})
    out["nbd"] = nbd
    out["bands"] = bands
    out["divergent_bands"] = [b["band"] for b in bands if b["divergent"]]
    # panel reporting completeness (#468)
    reporting = None
    rep_col = args.get("reporting_column")
    if rep_col:
        expected = _int_of(args, "expected_periods", 0, 0) or None
        brand_col = ctx.col("brand_column", required=False)
        focal = _text(args.get("focal_brand"))
        units_col = ctx.col("units_column", required=False)
        if args.get("reporting_path"):
            r_header, r_rows = _load_other(ctx, "reporting_path", "reporting_sheet")
            r_id = _column(args.get("reporting_id_column") or id_col, r_header, "reporting_id_column")
            r_col = _column(rep_col, r_header, "reporting_column")
        else:
            r_header, r_rows, r_id, r_col = ctx.header, ctx.rows, id_col, _column(rep_col, ctx.header, "reporting_column")
        reported: dict[str, float] = {}
        for r in r_rows:
            ident = _text(r.get(r_id))
            number, status = _number(r.get(r_col))
            if ident and status == "ok":
                reported[ident] = max(reported.get(ident, 0.0), number)  # type: ignore[type-var]
        if expected is None:
            expected = int(max(reported.values())) if reported else 0
            ctx.warnings.append(f"expected_periods not given; using the most periods any id reported ({expected}).")
        no_record = [i for i in per_id if i not in reported]
        if no_record:
            ctx.missing.append(f"{_plural(len(no_record), 'id')} with purchases but no reporting record left out of the reporter split.")
        groups: dict[str, dict[str, Any]] = {}
        for label in ("continuous", "intermittent"):
            ids = [i for i in per_id if i in reported and ((reported[i] >= expected) == (label == "continuous"))]
            rows = [r for r in ctx.rows if _text(r.get(id_col)) in set(ids)]
            focal_rows = [r for r in rows if brand_col and focal and _text(r.get(brand_col)).casefold() == focal.casefold()]
            unit_of = (lambda r: _number(r.get(units_col))[0] or 0.0) if units_col else (lambda r: 1.0)
            all_units = sum(unit_of(r) for r in rows)
            focal_units = sum(unit_of(r) for r in focal_rows)
            focal_buyers = {_text(r.get(id_col)) for r in focal_rows}
            mean_reported = _mean([reported[i] for i in ids]) if ids else None
            freq = None if not ids else round(len(focal_rows) / len(ids), 2) if brand_col and focal else round(len(rows) / len(ids), 2)
            groups[label] = {
                "households": len(ids),
                "mean_periods_reported": None if mean_reported is None else _num(mean_reported),
                "brand_share_pct": _pct(focal_units, all_units) if brand_col and focal else None,
                "penetration_pct": _pct(len(focal_buyers), len(ids)) if brand_col and focal else None,
                "mean_frequency": freq,
                "frequency_per_expected_periods": None if freq is None or not mean_reported else round(freq * expected / mean_reported, 2),
                "thin": len(ids) < ctx.min_rows,
            }
        c, i = groups["continuous"], groups["intermittent"]
        gap = {
            "brand_share_pp": None if c["brand_share_pct"] is None or i["brand_share_pct"] is None else round(c["brand_share_pct"] - i["brand_share_pct"], 1),
            "frequency_pct": None if not c["mean_frequency"] or i["mean_frequency"] is None else round((i["mean_frequency"] - c["mean_frequency"]) / c["mean_frequency"] * 100, 1),
        }
        base = "continuous" if (gap["brand_share_pp"] is not None and abs(gap["brand_share_pp"]) > 2) or (gap["frequency_pct"] is not None and abs(gap["frequency_pct"]) > 10) else "all"
        reporting = {"expected_periods": expected, "focal_brand": focal or None, "groups": groups, "gap": gap, "base": base, "reporting_gap": i["households"] > 0}
    else:
        reporting = {"reporting_unknown": True, "note": "no reporting_column: missing periods cannot be told from non-purchase."}
    out["reporting"] = reporting
    out["method"] = (
        "Purchases per buyer = rows per id. Outliers: buyers above the cut (iqr: Q3 + threshold × IQR; multiple_of_median: median × threshold; zscore: mean + threshold × sd) with 2+ purchases, marked investigate; every frequency figure is given with and without them. "
        "heavy_half_share = purchases from the top half of buyers; top_20pct = from the top fifth. NBD by method of moments (k = m² ÷ (v − m), p = m ÷ v) on the counts, with population_size − buyers zeros when population_size is given; "
        "fitted counts per band from the negative binomial pmf; divergent when observed ≥ 5 and |observed − fitted| > 20 %. Reporter split: continuous when periods reported ≥ expected_periods; brand share = focal units ÷ all units within the group; "
        "frequency_per_expected_periods scales the intermittent frequency by expected ÷ mean periods reported; base continuous when the share gap exceeds 2 pp or the frequency gap 10 %."
    )
    text = f"{n} buyers, {with_all['purchases']} purchases: {with_all['mean_frequency']} per buyer"
    if outliers:
        text += f"; {len(outliers)} outlier(s) ({', '.join(o['id'] for o in outliers[:3])}, {outliers[0]['share_of_purchases_pct']:g} % of purchases) → {without['mean_frequency']} without them ({out['delta_pct']:+g} %); decide whether they belong"
    else:
        text += "; no outlier under the rule"
    if with_all["heavy_half_share_pct"] is not None:
        text += f"; the top half of buyers brings {with_all['heavy_half_share_pct']:g} % of purchases"
    if nbd.get("fit") == "ok" or nbd.get("fit") == "ok_conditional_on_buying":
        text += f"; NBD k {nbd['k']}, divergent bands: {', '.join(out['divergent_bands']) or 'none'}"
    elif nbd.get("fit"):
        text += f"; NBD {nbd['fit']}"
    if reporting and "groups" in reporting:
        c, i = reporting["groups"]["continuous"], reporting["groups"]["intermittent"]
        text += f"; continuous reporters ({c['households']}) share {c['brand_share_pct']} %, frequency {c['mean_frequency']} vs intermittent ({i['households']}) {i['brand_share_pct']} %, {i['mean_frequency']} ({i['frequency_per_expected_periods']} per {reporting['expected_periods']} periods); base {reporting['base']}"
    out["summary"] = text + "."
    return out


# --- panel (#469, #470) -------------------------------------------------------------------


def _op_panel(ctx: _Ctx) -> dict[str, Any]:
    args = ctx.args
    hh_col, brand_col = ctx.col("household_column"), ctx.col("brand_column")
    units_col = ctx.col("units_column", required=False)
    period_col = ctx.col("period_column", required=False)
    period = _period_arg(args)
    date_col = ctx.col("date_column", required=False)
    if period_col is None and not (period and date_col):
        raise ToolError("panel needs period_column (a period label per row) or period (week|month) with date_column")
    total_hh = _int_of(args, "total_households", 0, 0) or None
    swing_thr = _float_of(args, "share_swing_threshold_pp", 5.0)
    dup_tol = _float_of(args, "duplication_tolerance", 1.5)
    rows_ok = []
    blank = bad_period = 0
    for r in ctx.rows:
        hh, brand = _text(r.get(hh_col)), _text(r.get(brand_col))
        if not hh or not brand:
            blank += 1
            continue
        if period_col:
            label = _text(r.get(period_col))
            if not label:
                bad_period += 1
                continue
        else:
            d = _parse_date(r.get(date_col))
            if d is None:
                bad_period += 1
                continue
            label = _bucket(d, period)  # type: ignore[arg-type]
        units = 1.0
        if units_col:
            number, status = _number(r.get(units_col))
            units = number if status == "ok" else 0.0  # type: ignore[assignment]
        rows_ok.append((hh, brand, label, units))
    if blank:
        ctx.missing.append(f"{_plural(blank, 'row')} with a blank {hh_col} or {brand_col} left out.")
    if bad_period:
        ctx.missing.append(f"{_plural(bad_period, 'row')} with a blank or unreadable period left out.")
    households = {hh for hh, _, _, _ in rows_ok}
    brands = sorted({b for _, b, _, _ in rows_ok})
    periods = sorted({p for _, _, p, _ in rows_ok}, key=lambda p: (_period_key(p) or ("z", ()))[1])
    denominator = total_hh or len(households)
    denominator_source = "supplied" if total_hh else "households in file"
    hh_units: dict[str, float] = {}
    for hh, _, _, u in rows_ok:
        hh_units[hh] = hh_units.get(hh, 0.0) + u
    brand_out = []
    buyers_of: dict[str, set[str]] = {}
    for b in brands:
        rows_b = [(hh, p, u) for hh, br, p, u in rows_ok if br == b]
        buyers = {hh for hh, _, _ in rows_b}
        buyers_of[b] = buyers
        units_b = sum(u for _, _, u in rows_b)
        requirement = sum(hh_units[hh] for hh in buyers)
        by_period = {}
        for p in periods:
            cat = sum(u for _, _, pp, u in rows_ok if pp == p)
            own = sum(u for _, pp, u in rows_b if pp == p)
            by_period[p] = _pct(own, cat)
        shares = [s for s in by_period.values() if s is not None]
        swings = [abs(b2 - a2) for a2, b2 in zip(shares, shares[1:])]
        entry: dict[str, Any] = {
            "name": b,
            "buyers": len(buyers),
            "purchases": len(rows_b),
            "units": _num(units_b),
            "penetration_pct": _pct(len(buyers), total_hh) if total_hh else None,
            "penetration_reason": None if total_hh else "buyer-only file, no population denominator",
            "purchase_frequency": round(len(rows_b) / len(buyers), 2) if buyers else None,
            "share_of_requirement_pct": _pct(units_b, requirement),
            "volume_share_pct_by_period": by_period,
            "max_swing_pp": None if not swings else round(max(swings), 1),
            "thin": len(buyers) < ctx.min_rows,
        }
        if len(buyers) == len(households) and households:
            entry["warning"] = "buyers_as_denominator"
            ctx.warnings.append(f"{b}: every household in the file bought it, so the file's denominator is buyers, not population (buyers_as_denominator).")
        brand_out.append(entry)
    over = [b["name"] for b in brand_out if b["max_swing_pp"] is not None and b["max_swing_pp"] > swing_thr]
    # duplication law: share of A's buyers who also bought B ≈ D × penetration of B
    pairs = []
    ratios = []
    pen = {b: len(buyers_of[b]) / denominator for b in brands} if denominator else {}
    for a in brands:
        for b in brands:
            if a == b or not buyers_of[a]:
                continue
            observed = len(buyers_of[a] & buyers_of[b]) / len(buyers_of[a])
            if pen.get(b):
                ratios.append(observed / pen[b])
    d_coef = _mean(ratios) if ratios else None
    for a in brands:
        for b in brands:
            if a >= b or not buyers_of[a]:
                continue
            observed = len(buyers_of[a] & buyers_of[b]) / len(buyers_of[a])
            expected = None if d_coef is None or not pen.get(b) else d_coef * pen[b]
            ratio = None if not expected else observed / expected
            pairs.append({"a": a, "b": b, "observed_pct": round(observed * 100, 1), "expected_pct": None if expected is None else round(expected * 100, 1), "ratio": _r(ratio, 2), "flag": ratio is not None and (ratio > dup_tol or ratio < 1 / dup_tol)})
    flagged = [p for p in pairs if p["flag"]]
    violations = [f"{b} share swings {next(x['max_swing_pp'] for x in brand_out if x['name'] == b):g} pp between periods (> {swing_thr:g} pp)" for b in over]
    violations += [f"{p['a']}–{p['b']} duplication {p['ratio']}× expected (outside 1/{dup_tol:g}–{dup_tol:g}×)" for p in flagged]
    if len(periods) < 2 or len(brands) < 3:
        assumptions_ok = None
        reason = f"insufficient periods/brands ({len(periods)} periods, {len(brands)} brands; needs 2 and 3)"
    else:
        assumptions_ok = not violations
        reason = None
    out = {
        "household_column": hh_col,
        "brand_column": brand_col,
        "units_column": units_col,
        "households_in_file": len(households),
        "total_households": total_hh,
        "denominator_source": denominator_source,
        "ask_for": None if total_hh else "count of all continuously reporting panel households in the window, including category non-buyers",
        "periods": periods,
        "brands": brand_out,
        "share_stability": {"threshold_pp": swing_thr, "max_swing_pp": max((b["max_swing_pp"] or 0) for b in brand_out) if brand_out else None, "brands_over_threshold": over},
        "duplication": {"d": _r(d_coef, 2), "denominator": denominator_source, "tolerance": dup_tol, "pairs": pairs},
        "assumptions_ok": assumptions_ok,
        "assumptions_reason": reason,
        "violations": violations,
        "method": (
            "Per brand: buyers = distinct households with a row; penetration = buyers ÷ total_households only when supplied (null with reason otherwise); purchase frequency = rows ÷ buyers; share of requirement = brand units ÷ all category units of its buyers; "
            "volume share per period = brand units ÷ category units; max swing = largest period-to-period change in pp. Duplication law: share of A's buyers also buying B = D × penetration(B), D fitted as the mean observed ÷ penetration over ordered pairs "
            f"(penetration on {denominator_source}); a pair is flagged when observed ÷ expected lies outside 1/{dup_tol:g}–{dup_tol:g}. assumptions_ok is false on any swing over the threshold or flagged pair, null with fewer than 2 periods or 3 brands."
        ),
    }
    text = f"{len(brands)} brands, {len(households)} households, {len(periods)} periods"
    if total_hh is None:
        text += "; penetration not computable (buyer-only file): ask for the continuously reporting household count"
    if assumptions_ok is None:
        text += f"; Dirichlet check not run: {reason}"
    elif assumptions_ok:
        text += f"; share swings ≤ {swing_thr:g} pp and duplication within {dup_tol:g}× of expected, so Dirichlet-type norms may be quoted"
    else:
        text += "; Dirichlet norms do not apply: " + "; ".join(violations[:3])
    out["summary"] = text + "."
    return out


# --- survey (#263, #465) --------------------------------------------------------------------


def _tally(ctx: _Ctx, col: str, threshold: float | None, group_col: str | None) -> dict[str, Any]:
    answered: list[tuple[dict[str, Any], str, float | None]] = []
    blank = 0
    for r in ctx.rows:
        text = _text(r.get(col))
        if not text:
            blank += 1
            continue
        number, status = _number(text)
        answered.append((r, text, number if status == "ok" else None))
    nums = [x for _, _, x in answered if x is not None]
    numeric = bool(answered) and len(nums) >= 0.8 * len(answered)
    counts: dict[str, int] = {}
    for _, text, x in answered:
        key = _fmt(_num(x)) if numeric and x is not None else text
        counts[key] = counts.get(key, 0) + 1
    if numeric:
        items = sorted(counts.items(), key=lambda kv: (_number(kv[0].replace(",", ""))[0] if _number(kv[0].replace(",", ""))[1] == "ok" else float("inf"), kv[0]))
    else:
        items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    entry: dict[str, Any] = {
        "question": col,
        "n_rows": len(ctx.rows),
        "answered": len(answered),
        "blank": blank,
        "non_numeric": len(answered) - len(nums) if numeric else None,
        "type": "numeric" if numeric else "categorical",
        "distribution": [{"value": v, "count": c, "pct_of_answered": _pct(c, len(answered))} for v, c in items[:MAX_LEVELS]],
        "distinct": len(counts),
    }
    if numeric:
        entry["median"], entry["mean"] = (_num(_median(nums) or 0.0), _num(_mean(nums) or 0.0)) if nums else (None, None)
        if threshold is not None:
            k = sum(1 for x in nums if x >= threshold)
            entry["share_at_or_above_threshold"] = {"threshold": threshold, "count": k, "pct": _pct(k, len(nums))}
        if entry["non_numeric"]:
            ctx.missing.append(f"{col}: {_plural(entry['non_numeric'], 'non-numeric answer')} left out of median/mean.")
    if blank:
        ctx.missing.append(f"{col}: {_plural(blank, 'blank answer')} left out.")
    if answered and len(answered) < ctx.min_rows:
        ctx.warnings.append(f"{col}: only {len(answered)} answers (small_sample).")
    if group_col:
        groups = []
        levels, keys = _levels([r for r, _, _ in answered], group_col, ctx.missing, "group_column")
        for lv in levels:
            sub = [x for (r, _, x), k in zip(answered, keys) if k == lv]
            sub_nums = [x for x in sub if x is not None]
            g: dict[str, Any] = {"group": lv, "n": len(sub), "thin": len(sub) < ctx.min_rows}
            if numeric:
                g["median"] = None if not sub_nums else _num(_median(sub_nums) or 0.0)
                g["mean"] = None if not sub_nums else _num(_mean(sub_nums) or 0.0)
                if threshold is not None:
                    k = sum(1 for x in sub_nums if x >= threshold)
                    g["share_at_or_above_threshold_pct"] = _pct(k, len(sub_nums))
            groups.append(g)
        entry["by_group"] = groups
    return entry


def _op_survey(ctx: _Ctx) -> dict[str, Any]:
    args = ctx.args
    questions = _list(args.get("questions"), "questions") or []
    response = args.get("response_column")
    if response and response not in questions:
        questions.append(response)
    if not questions:
        raise ToolError("questions (answer columns to tally) or response_column (for a scored scale) is required")
    cols = [_column(q, ctx.header, "questions") for q in questions]
    group_col = ctx.col("group_column", required=False)
    threshold = _float_of(args, "threshold")
    out: dict[str, Any] = {"questions": [_tally(ctx, c, threshold, group_col) for c in cols], "group_column": group_col}
    scale = args.get("scale")
    score: dict[str, Any] | None = None
    if scale:
        if not isinstance(scale, dict) or _float_of(scale, "min") is None or _float_of(scale, "max") is None:
            raise ToolError("scale must be {min, max}, e.g. {\"min\": 1, \"max\": 5}")
        lo, hi = _float_of(scale, "min"), _float_of(scale, "max")
        method = str(args.get("method") or "top_two_box").lower()
        if method not in {"top_two_box", "top_box", "mean", "nps"}:
            raise ToolError("method must be top_two_box, top_box, mean or nps")
        col = _column(response or questions[0], ctx.header, "response_column")
        if method == "top_two_box":
            counted = {hi, hi - 1}  # type: ignore[operator]
        elif method == "top_box":
            counted = {hi}
        else:
            counted = set()

        def score_of(values: list[float]) -> float | None:
            if not values:
                return None
            if method == "mean":
                return round(_mean(values) or 0.0, 2)
            if method == "nps":
                promoters = sum(1 for v in values if v >= 9)
                detractors = sum(1 for v in values if v <= 6)
                return round((promoters - detractors) / len(values) * 100, 1)
            return round(sum(1 for v in values if v in counted) / len(values) * 100, 1)

        valid: list[tuple[dict[str, Any], float]] = []
        out_of_range = blank = 0
        for r in ctx.rows:
            number, status = _number(r.get(col))
            if status == "blank":
                blank += 1
            elif status == "bad" or number < lo or number > hi:  # type: ignore[operator]
                out_of_range += 1
            else:
                valid.append((r, number))  # type: ignore[arg-type]
        per_group = []
        comparable: bool | None = None
        reason = None
        if group_col:
            levels, keys = _levels(ctx.rows, group_col, [], "group_column")
            ranges: dict[str, set[float]] = {}
            for r, k in zip(ctx.rows, keys):
                number, status = _number(r.get(col))
                if status == "ok":
                    ranges.setdefault(k, set()).add(number)  # type: ignore[arg-type]
            for lv in levels:
                values = [x for r, x in valid if _text(r.get(group_col)) == lv or (not _text(r.get(group_col)) and lv == BLANK_GROUP)]
                off = sum(1 for v in ranges.get(lv, set()) if v < lo or v > hi)  # type: ignore[operator]
                per_group.append({"group": lv, "n": len(values), "score": score_of(values), "out_of_range": off, "thin": len(values) < ctx.min_rows})
            bad_groups = [g["group"] for g in per_group if g["out_of_range"]]
            comparable = not bad_groups
            if bad_groups:
                reason = f"groups with answers outside {lo:g}–{hi:g}: {', '.join(bad_groups)}; a different scale is likely, so the scores cannot be compared"
        if out_of_range:
            ctx.missing.append(f"{col}: {_plural(out_of_range, 'answer')} outside the scale {lo:g}–{hi:g} or not numeric, excluded.")
        if len(valid) < ctx.min_rows:
            ctx.warnings.append(f"{col}: only {len(valid)} valid answers (small_sample).")
        score = {
            "definition": {"question": _text(args.get("question")) or col, "column": col, "scale": {"min": _num(lo), "max": _num(hi)}, "method": method, "counted_values": sorted(_num(v) for v in counted) if counted else None},
            "n_answered": len(valid),
            "n_blank": blank,
            "n_out_of_range": out_of_range,
            "score": score_of([x for _, x in valid]),
            "unit": "mean" if method == "mean" else ("nps" if method == "nps" else "pct"),
            "per_group": per_group,
            "comparable": comparable,
            "comparable_reason": reason,
        }
    out["score"] = score
    out["method"] = (
        "Tally: non-blank answers per question with shares of answered; numeric questions (≥ 80 % numbers) get median, mean and the share at or above threshold; groups below min_rows are thin. "
        "Score: answers inside scale.min–max only; top_two_box = share of the two highest values, top_box = the highest, mean = arithmetic mean, nps = % 9–10 minus % 0–6; comparable is false when a group has answers outside the scale."
    )
    q0 = out["questions"][0]
    if score:
        d = score["definition"]
        label = {"top_two_box": "top-two-box", "top_box": "top-box", "mean": "mean", "nps": "NPS"}[d["method"]]
        text = f"{score['score']}{' %' if score['unit'] == 'pct' else ''} {label}" + (f" ({_fmt(d['counted_values'][0])}–{_fmt(d['counted_values'][-1])} on a {_fmt(d['scale']['min'])}–{_fmt(d['scale']['max'])} scale" if d["counted_values"] else f" (on a {_fmt(d['scale']['min'])}–{_fmt(d['scale']['max'])} scale") + f", “{d['question']}”, n={_fmt(score['n_answered'])}" + (f", {score['n_blank']} blank excluded" if score["n_blank"] else "") + ")"
        if score["per_group"]:
            text += "; by group: " + ", ".join(f"{g['group']} {g['score']}{' %' if score['unit'] == 'pct' else ''} (n={_fmt(g['n'])})" for g in score["per_group"][:6])
            if score["comparable"] is False:
                text += f"; not comparable: {score['comparable_reason']}"
    else:
        text = f"{q0['question']}: {q0['answered']} answered, {q0['blank']} blank"
        if q0["type"] == "numeric":
            text += f"; median {_fmt(q0['median'])}, mean {_fmt(q0['mean'])}"
            if q0.get("share_at_or_above_threshold"):
                s = q0["share_at_or_above_threshold"]
                text += f"; {s['count']} of {q0['answered'] - (q0['non_numeric'] or 0)} ({s['pct']:g} %) at or above {s['threshold']:g}"
        else:
            text += "; top: " + ", ".join(f"{d['value']} {d['count']} ({d['pct_of_answered']:g} %)" for d in q0["distribution"][:3])
    out["summary"] = text + "."
    return out


# --- brand_recall (#493, #494) -----------------------------------------------------------


def _alias_map(args: dict) -> dict[str, list[str]]:
    aliases = args.get("brand_aliases")
    if aliases in (None, "", {}):
        return {}
    if not isinstance(aliases, dict):
        raise ToolError("brand_aliases must be {brand: [spellings]}")
    out = {}
    for brand, items in aliases.items():
        spellings = _list(items, "brand_aliases") or []
        out[str(brand)] = [s.casefold() for s in spellings + [str(brand)]]
    return out


def _brand_of(text: str, aliases: dict[str, list[str]]) -> str | None:
    folded = text.casefold().strip()
    for brand, spellings in aliases.items():
        if any(folded == s or s in folded for s in spellings):
            return brand
    return None


def _op_brand_recall(ctx: _Ctx) -> dict[str, Any]:
    args = ctx.args
    aliases = _alias_map(args)
    own = _text(args.get("own_brand"))
    competitors = _list(args.get("competitor_brands"), "competitor_brands") or []
    for b in [own, *competitors]:
        if b and b not in aliases:
            aliases[b] = [b.casefold()]
    unaided_col = ctx.col("unaided_column", required=False)
    recognition = args.get("recognition_columns") if isinstance(args.get("recognition_columns"), dict) else {}
    attribution = args.get("attribution_columns") if isinstance(args.get("attribution_columns"), dict) else {}
    remembered = args.get("remembered_columns") if isinstance(args.get("remembered_columns"), dict) else {}
    if not unaided_col and not recognition and not attribution:
        raise ToolError("brand_recall needs unaided_column, recognition_columns {ad: column} and/or attribution_columns {message: column}")
    positives = _positive_set(args)
    dont_know = {s.casefold() for s in (_list(args.get("dont_know_values"), "dont_know_values") or ["nežinau", "don't know", "dont know", "nezinau", "n/a", "-"])}
    base_n = len(ctx.rows)
    out: dict[str, Any] = {"base_n": base_n, "own_brand": own or None, "competitor_brands": competitors}
    if base_n < ctx.min_rows:
        ctx.warnings.append(f"Only {base_n} respondents: below {ctx.min_rows} (small_base).")
    if unaided_col:
        if not aliases:
            raise ToolError("unaided_column needs brand_aliases {brand: [spellings]} (or own_brand/competitor_brands) to match mentions")
        counts: dict[str, int] = {b: 0 for b in aliases}
        first: dict[str, int] = {b: 0 for b in aliases}
        unmatched: dict[str, int] = {}
        no_answer = 0
        for r in ctx.rows:
            text = _text(r.get(unaided_col))
            if not text:
                no_answer += 1
                continue
            mentions = [m.strip() for m in re.split(r"[;,/\n]+", text) if m.strip()]
            seen: set[str] = set()
            for i, m in enumerate(mentions):
                brand = _brand_of(m, aliases)
                if brand is None:
                    unmatched[m] = unmatched.get(m, 0) + 1
                    continue
                if brand not in seen:
                    counts[brand] += 1
                    seen.add(brand)
                    if i == 0:
                        first[brand] += 1
        out["unaided"] = {b: {"count": counts[b], "pct": _pct(counts[b], base_n), "first_mention_pct": _pct(first[b], base_n)} for b in aliases}
        out["unaided_no_answer"] = no_answer
        out["unaided_unmatched"] = [{"text": t, "count": c} for t, c in sorted(unmatched.items(), key=lambda kv: (-kv[1], kv[0]))[:20]]
        if unmatched:
            ctx.missing.append(f"{unaided_col}: {_plural(sum(unmatched.values()), 'mention')} matched no brand alias (e.g. {next(iter(sorted(unmatched, key=lambda t: -unmatched[t])))!r}); add them to brand_aliases.")
    if recognition:
        rec = {}
        for ad, col_name in recognition.items():
            col = _column(col_name, ctx.header, "recognition_columns")
            k = sum(1 for r in ctx.rows if _is_positive(r.get(col), positives))
            rec[str(ad)] = {"column": col, "count": k, "pct": _pct(k, base_n)}
        out["recognition"] = rec
        if unaided_col and own and own in out["unaided"]:
            out["recall_vs_recognition_pp"] = {ad: None if v["pct"] is None or out["unaided"][own]["pct"] is None else round(v["pct"] - out["unaided"][own]["pct"], 1) for ad, v in rec.items()}
    if attribution:
        if not own:
            raise ToolError("attribution_columns need own_brand (and competitor_brands) to score who got the credit")
        att = {}
        for message, col_name in attribution.items():
            col = _column(col_name, ctx.header, "attribution_columns")
            rem_col = _column(remembered[message], ctx.header, "remembered_columns") if message in remembered else None
            rows = [r for r in ctx.rows if (_is_positive(r.get(rem_col), positives) if rem_col else bool(_text(r.get(col))))]
            n = len(rows)
            tally: dict[str, int] = {"own": 0, "dont_know": 0}
            comp: dict[str, int] = {c: 0 for c in competitors}
            other: dict[str, int] = {}
            for r in rows:
                text = _text(r.get(col))
                if not text or text.casefold() in dont_know:
                    tally["dont_know"] += 1
                    continue
                brand = _brand_of(text, aliases)
                if brand == own:
                    tally["own"] += 1
                elif brand in comp:
                    comp[brand] += 1
                else:
                    other[text] = other.get(text, 0) + 1
            mis = sum(comp.values())
            top_comp = max(comp.items(), key=lambda kv: kv[1], default=None)
            entry = {
                "column": col,
                "remembered_n": n,
                "remembered_pct_of_base": _pct(n, base_n),
                "own": {"count": tally["own"], "pct": _pct(tally["own"], n)},
                "competitors": {c: {"count": k, "pct": _pct(k, n)} for c, k in comp.items()},
                "dont_know": {"count": tally["dont_know"], "pct": _pct(tally["dont_know"], n)},
                "other_unmatched": [{"text": t, "count": c} for t, c in sorted(other.items(), key=lambda kv: (-kv[1], kv[0]))[:20]],
                "misattribution_pct": _pct(mis, n),
                "top_misattributed_to": top_comp[0] if top_comp and top_comp[1] > 0 else None,
                "small_base": n < ctx.min_rows,
            }
            if other:
                ctx.missing.append(f"{col}: {_plural(sum(other.values()), 'answer')} matched neither {own}, a competitor nor a don't-know value; listed under other_unmatched, not counted as correct.")
            if n < ctx.min_rows:
                ctx.warnings.append(f"{message}: only {n} remembered the message (small_base).")
            att[str(message)] = entry
        out["attribution"] = att
    out["method"] = (
        "Unaided: each respondent's free-text answer is split on ; , / and newlines; a mention counts once per brand when it equals or contains an alias; pct of all respondents (base_n). Recognition: positive values per ad column over base_n. "
        "Attribution: among respondents who remembered the message (remembered_columns positive, else any non-blank answer), the answer is matched to own_brand, a competitor or a don't-know value; misattribution = competitor share of those who remembered. "
        "Unaided recall and recognition are reported as two figures and never combined."
    )
    bits = []
    if unaided_col:
        named = sorted(out["unaided"].items(), key=lambda kv: (-kv[1]["count"], kv[0]))[:3]
        bits.append("unaided: " + ", ".join(f"{b} {v['pct']:g} % ({v['count']}/{base_n})" for b, v in named))
    if recognition:
        bits.append("recognition when shown: " + ", ".join(f"{ad} {v['pct']:g} % ({v['count']}/{base_n})" for ad, v in out["recognition"].items()))
    if attribution:
        for message, a in out["attribution"].items():
            comp_text = ", ".join(f"{c} {v['pct']:g} %" for c, v in a["competitors"].items())
            bits.append(f"{message}: of {a['remembered_n']} who remembered ({a['remembered_pct_of_base']:g} %), {a['own']['pct']:g} % credited {own}, {comp_text}, {a['dont_know']['pct']:g} % did not know; misattribution {a['misattribution_pct']:g} %" + (f", mainly {a['top_misattributed_to']}" if a["top_misattributed_to"] else ""))
    out["summary"] = ("; ".join(bits) or "nothing to tally") + "."
    return out


# --- keyword_demand (#331) ----------------------------------------------------------------


def _op_keyword_demand(ctx: _Ctx) -> dict[str, Any]:
    args = ctx.args
    kw_col = _detect(ctx.header, args.get("keyword_column"), ("keyword", "keywords", "search term", "query", "raktažodis", "užklausa"), "keyword", True)
    vol_col = _detect(ctx.header, args.get("volume_column"), ("volume", "search volume", "avg. monthly searches", "searches", "monthly searches", "paieškos", "apimtis"), "volume", True)
    cpc_col = _detect(ctx.header, args.get("cpc_column"), ("cpc", "top of page bid (low range)", "avg. cpc"), "cpc", False)
    kd_col = _detect(ctx.header, args.get("kd_column"), ("kd", "difficulty", "keyword difficulty", "competition"), "kd", False)
    head = _text(args.get("head_term"))
    groups_arg = args.get("groups")
    if not isinstance(groups_arg, list) or not groups_arg:
        raise ToolError("groups is required: [{name, keywords: [...]} or {name, modifiers: [...]}]")
    ctr = _float_of(args, "ctr_share_pct", 25.0)
    conv_arg = args.get("conversion_rate_pct")
    order_value = _float_of(args, "order_value_eur")
    conv_by: dict[str, float] = {}
    conv_global: float | None = None
    if isinstance(conv_arg, dict):
        for k, v in conv_arg.items():
            conv_by[str(k).casefold()] = float(v)
    elif conv_arg not in (None, ""):
        conv_global = _float_of(args, "conversion_rate_pct")
    rows = []
    bad = blank = 0
    for r in ctx.rows:
        kw = _text(r.get(kw_col))
        if not kw:
            blank += 1
            continue
        number, status = _number(r.get(vol_col))
        if status != "ok":
            bad += 1
            continue
        extra = {}
        for name, col in (("cpc", cpc_col), ("kd", kd_col)):
            if col:
                n2, s2 = _number(r.get(col))
                extra[name] = n2 if s2 == "ok" else None
        rows.append((kw, number, extra))
    if blank:
        ctx.missing.append(f"{_plural(blank, 'row')} with a blank {kw_col} left out.")
    if bad:
        ctx.warnings.append(f"{_plural(bad, 'row')} with a non-numeric {vol_col} left out.")
    if not rows:
        ctx.missing.append(f"{vol_col} has no numeric values, so no estimate.")
    assumptions = [f"ctr_share_pct {ctr:g} %: the share of searches that click the ranking page (assumption, not measured)"]
    if conv_global is not None or conv_by:
        assumptions.append("conversion_rate_pct per group as given by the marketer (assumption)")
    if order_value is not None:
        assumptions.append(f"order_value_eur {order_value:g} per conversion (assumption)")

    def estimate(volume: float, conv: float | None) -> tuple[float, float | None, float | None]:
        visits = volume * ctr / 100
        orders = None if conv is None else visits * conv / 100
        value = None if orders is None or order_value is None else orders * order_value
        return visits, orders, value

    head_row = next((row for row in rows if row[0].casefold() == head.casefold()), None) if head else None
    if head and head_row is None:
        ctx.missing.append(f"head_term {head!r} not found in {kw_col}.")
    head_out = None
    if head_row:
        conv = conv_by.get("head", conv_by.get(head.casefold(), conv_global))
        visits, orders, value = estimate(head_row[1], conv)
        head_out = {"keyword": head_row[0], "volume": _num(head_row[1]), "cpc": None if head_row[2].get("cpc") is None else _num(head_row[2]["cpc"]), "kd": None if head_row[2].get("kd") is None else _num(head_row[2]["kd"]), "conversion_rate_pct": conv, "est_visits": _num(visits), "est_orders": None if orders is None else _num(orders), "est_value_eur": None if value is None else _num(value)}
    assigned: set[str] = set()
    groups_out = []
    for g in groups_arg:
        if not isinstance(g, dict) or not g.get("name"):
            raise ToolError("each group needs a name and keywords[] or modifiers[]")
        name = str(g["name"])
        keywords = {k.casefold() for k in (_list(g.get("keywords"), "keywords") or [])}
        modifiers = [m.casefold() for m in (_list(g.get("modifiers"), "modifiers") or [])]
        members = []
        for kw, vol, extra in rows:
            key = kw.casefold()
            if head and key == head.casefold():
                continue
            if key in assigned:
                continue
            if key in keywords or any(m in key for m in modifiers):
                members.append((kw, vol, extra))
                assigned.add(key)
        listed_missing = [k for k in keywords if k not in {m[0].casefold() for m in members} and k not in assigned]
        if listed_missing:
            ctx.missing.append(f"group {name}: {_plural(len(listed_missing), 'listed keyword')} not in the file ({', '.join(sorted(listed_missing)[:5])}).")
        volume = sum(v for _, v, _ in members)
        conv = conv_by.get(name.casefold(), conv_global)
        visits, orders, value = estimate(volume, conv)
        cpcs = [e["cpc"] for _, _, e in members if e.get("cpc") is not None]
        kds = [e["kd"] for _, _, e in members if e.get("kd") is not None]
        groups_out.append(
            {
                "name": name,
                "n_keywords": len(members),
                "volume_sum": _num(volume),
                "cpc_avg": None if not cpcs else _num(_mean(cpcs) or 0.0),
                "kd_avg": None if not kds else _num(_mean(kds) or 0.0),
                "conversion_rate_pct": conv,
                "est_visits": _num(visits),
                "est_orders": None if orders is None else _num(orders),
                "est_value_eur": None if value is None else _num(value),
                "keywords": [k for k, _, _ in sorted(members, key=lambda m: -m[1])][:20],
            }
        )
    unmatched = [{"keyword": kw, "volume": _num(vol)} for kw, vol, _ in rows if kw.casefold() not in assigned and not (head and kw.casefold() == head.casefold())]
    unmatched.sort(key=lambda u: -u["volume"])
    if unmatched:
        ctx.missing.append(f"{_plural(len(unmatched), 'keyword')} matched no group and are left out of the long tail ({sum(u['volume'] for u in unmatched):,} searches).")
    tail_volume = sum(g["volume_sum"] for g in groups_out)
    tail_value = sum(g["est_value_eur"] for g in groups_out) if groups_out and all(g["est_value_eur"] is not None for g in groups_out) else None
    tail_visits = sum(g["est_visits"] for g in groups_out)
    ratio = None
    basis = None
    if head_out:
        if tail_value is not None and head_out["est_value_eur"]:
            ratio, basis = round(tail_value / head_out["est_value_eur"], 2), "value"
        elif head_out["volume"]:
            ratio, basis = round(tail_volume / head_out["volume"], 2), "volume"
    out = {
        "keyword_column": kw_col,
        "volume_column": vol_col,
        "cpc_column": cpc_col,
        "kd_column": kd_col,
        "keywords_read": len(rows),
        "head": head_out,
        "groups": groups_out,
        "long_tail": {"n_keywords": sum(g["n_keywords"] for g in groups_out), "volume_sum": _num(tail_volume), "est_visits": _num(tail_visits), "est_value_eur": None if tail_value is None else _num(tail_value)},
        "ratio_long_tail_to_head": ratio,
        "ratio_basis": basis,
        "unmatched_keywords": unmatched[:50],
        "assumptions": assumptions,
        "method": "est_visits = volume × ctr_share_pct; est_orders = visits × conversion_rate_pct (per group, 'head' or global); est_value = orders × order_value_eur; a keyword joins the first group whose keywords list it or whose modifier it contains; the head term is excluded from every group.",
    }
    if head_out:
        text = f"Head {head_out['keyword']} {_fmt(head_out['volume'])} searches/month" + (f" → ~{_fmt(head_out['est_visits'])} visits, €{_fmt(head_out['est_value_eur'])}" if head_out["est_value_eur"] is not None else "")
        text += f"; long tail {out['long_tail']['n_keywords']} keywords {_fmt(out['long_tail']['volume_sum'])} searches" + (f" → ~{_fmt(out['long_tail']['est_visits'])} visits, €{_fmt(out['long_tail']['est_value_eur'])}" if tail_value is not None else "")
        if ratio is not None:
            text += f"; long tail ÷ head = {ratio} by {basis}"
        text += f" (assuming {ctr:g} % click share" + (f", conversion as given, €{order_value:g} per order)" if order_value is not None else ")")
    else:
        text = f"{len(groups_out)} groups cover {out['long_tail']['n_keywords']} keywords and {_fmt(out['long_tail']['volume_sum'])} searches/month; no head term matched"
    out["summary"] = text + "."
    return out


# --- page_breadth (#339) ------------------------------------------------------------------


def _norm_url(text: str, strip_query: bool = True) -> str:
    u = text.strip()
    if not u:
        return ""
    if strip_query:
        u = u.split("#", 1)[0].split("?", 1)[0]
    m = re.match(r"^(https?://)([^/]+)(.*)$", u, re.I)
    if m:
        u = m.group(1).lower() + m.group(2).lower() + m.group(3)
    if len(u) > 1 and u.endswith("/"):
        u = u[:-1]
    return u


def _pages(header: list[str], rows: list[dict[str, Any]], args: dict, label: str, missing: list[str], min_value: float) -> dict[str, Any]:
    url_col = _detect(header, args.get("url_column"), URL_HEADERS, "url", True)
    metric_col = _detect(header, args.get("metric_column"), TRAFFIC_HEADERS, "metric", True)
    traffic: dict[str, float] = {}
    blank_url = bad = 0
    for r in rows:
        u = _norm_url(_text(r.get(url_col)))
        if not u:
            blank_url += 1
            continue
        number, status = _number(r.get(metric_col))
        if status != "ok":
            bad += 1
            continue
        traffic[u] = traffic.get(u, 0.0) + number  # type: ignore[operator]
    if blank_url:
        missing.append(f"{label}: {_plural(blank_url, 'row')} with a blank {url_col} left out.")
    if bad:
        missing.append(f"{label}: {_plural(bad, 'row')} with a blank or non-numeric {metric_col} left out.")
    with_traffic = {u: v for u, v in traffic.items() if v >= min_value}
    total = sum(with_traffic.values())
    top10 = sorted(with_traffic.values(), reverse=True)[:10]
    return {
        "label": label,
        "url_column": url_col,
        "metric_column": metric_col,
        "rows": len(rows),
        "distinct_pages": len(traffic),
        "distinct_pages_with_traffic": len(with_traffic),
        "metric_total": _num(total),
        "top_10_share_pct": _pct(sum(top10), total),
        "_pages": with_traffic,
    }


def _op_page_breadth(ctx: _Ctx) -> dict[str, Any]:
    args = ctx.args
    min_value = _float_of(args, "min_value", 1.0) or 1.0
    first = _pages(ctx.header, ctx.rows, args, _text(args.get("period_label")) or "period 1", ctx.missing, min_value)
    periods = [first]
    if args.get("compare_path"):
        header2, rows2 = _load_other(ctx, "compare_path", "compare_sheet")
        periods.append(_pages(header2, rows2, args, _text(args.get("compare_label")) or "period 2", ctx.missing, min_value))
    delta = None
    pages_new = pages_lost = None
    if len(periods) == 2:
        a, b = periods
        d = b["distinct_pages_with_traffic"] - a["distinct_pages_with_traffic"]
        delta = {"pages_abs": d, "pages_pct": None if not a["distinct_pages_with_traffic"] else round(d / a["distinct_pages_with_traffic"] * 100, 1), "metric_abs": _num(b["metric_total"] - a["metric_total"]), "metric_pct": None if not a["metric_total"] else round((b["metric_total"] - a["metric_total"]) / a["metric_total"] * 100, 1)}
        new_pages = sorted(set(b["_pages"]) - set(a["_pages"]))
        lost_pages = sorted(set(a["_pages"]) - set(b["_pages"]))
        pages_new = {"count": len(new_pages), "sample": new_pages[:50]}
        pages_lost = {"count": len(lost_pages), "sample": lost_pages[:50]}
    index: dict[str, Any] | None = None
    if args.get("index_path"):
        if ctx.tools is None:
            raise ToolError("index_path needs a workspace")
        target = ctx.tools.resolve(str(args["index_path"]))
        if not target.exists():
            raise ToolError(f"not found: {args['index_path']}")
        urls: set[str] = set()
        if target.suffix.lower() in {".xml", ".txt"}:
            text = target.read_text(encoding="utf-8", errors="replace")
            if target.suffix.lower() == ".xml":
                import xml.etree.ElementTree as ET

                try:
                    root = ET.fromstring(text)
                except ET.ParseError as exc:
                    raise ToolError(f"{args['index_path']} is not a readable sitemap: {exc}") from exc
                for el in root.iter():
                    if el.tag.rsplit("}", 1)[-1] == "loc" and el.text:
                        urls.add(_norm_url(el.text))
            else:
                urls = {_norm_url(line) for line in text.splitlines() if line.strip()}
        else:
            header3, rows3 = _load_other(ctx, "index_path", "index_sheet")
            col = _detect(header3, args.get("index_url_column"), URL_HEADERS, "index url", True)
            urls = {_norm_url(_text(r.get(col))) for r in rows3 if _text(r.get(col))}
        urls.discard("")
        traffic_urls = set().union(*(set(p["_pages"]) for p in periods))
        silent = sorted(urls - traffic_urls)
        off_index = sorted(traffic_urls - urls)
        index = {"indexed_total": len(urls), "indexed_without_traffic": {"count": len(silent), "pct": _pct(len(silent), len(urls)), "sample": silent[:50]}, "traffic_not_in_index": {"count": len(off_index), "sample": off_index[:50]}}
    else:
        index = {"status": "not_available", "note": "no index_path (sitemap.xml or URL list), so indexed-but-silent pages are not computed."}
    for p in periods:
        p.pop("_pages")
    out = {
        "periods": periods,
        "delta": delta,
        "pages_new": pages_new,
        "pages_lost": pages_lost,
        "index": index,
        "min_value": min_value,
        "method": f"URLs normalised (query and fragment stripped, trailing slash removed, scheme and host lower-cased) and their metric summed; a page counts when its metric ≥ min_value ({min_value:g}); top_10_share = the ten largest pages' share of the period's metric; pages_new/lost compare the two periods' page sets; indexed_without_traffic = index URLs with no qualifying traffic in any period.",
    }
    text = "; ".join(f"{p['label']}: {p['distinct_pages_with_traffic']} pages with ≥ {min_value:g} {p['metric_column']} ({_fmt(p['metric_total'])} total, top 10 = {p['top_10_share_pct']:g} %)" if p["top_10_share_pct"] is not None else f"{p['label']}: {p['distinct_pages_with_traffic']} pages with traffic" for p in periods)
    if delta:
        text += f"; {delta['pages_abs']:+d} pages" + (f" ({delta['pages_pct']:+g} %)" if delta["pages_pct"] is not None else "") + f", {pages_new['count']} new, {pages_lost['count']} lost"  # type: ignore[index]
    if index and "indexed_total" in index:
        text += f"; {index['indexed_total']} indexed URLs, {index['indexed_without_traffic']['count']} ({index['indexed_without_traffic']['pct']:g} %) without traffic"
    out["summary"] = text + "."
    return out


# --- market_match (#456) -------------------------------------------------------------------


def _op_market_match(ctx: _Ctx) -> dict[str, Any]:
    args = ctx.args
    market_col, date_col, metric = ctx.col("market_column"), ctx.col("date_column"), ctx.col("metric")
    candidates = _list(args.get("candidates"), "candidates")
    test_market = _text(args.get("test_market"))
    test_weeks = _int_of(args, "test_weeks", 6)
    min_periods = _int_of(args, "min_periods", 8)
    confounds = args.get("confounds") if isinstance(args.get("confounds"), list) else []
    per_market: dict[str, dict[date, float]] = {}
    blank = 0
    for r in ctx.rows:
        m = _text(r.get(market_col))
        if not m:
            blank += 1
            continue
        per_market.setdefault(m, {})
    if blank:
        ctx.missing.append(f"{_plural(blank, 'row')} with a blank {market_col} left out.")
    for m in per_market:
        per_market[m] = {d: v[metric] for d, v in _series([r for r in ctx.rows if _text(r.get(market_col)) == m], date_col, [metric], ctx.missing)}
    folded = {m.casefold(): m for m in per_market}
    if candidates:
        absent = [c for c in candidates if c.casefold() not in folded]
        if absent:
            ctx.missing.append(f"markets not in the file: {', '.join(absent)}.")
        markets = [folded[c.casefold()] for c in candidates if c.casefold() in folded]
    else:
        markets = sorted(per_market)
    if test_market and test_market.casefold() not in folded:
        raise ToolError(f"test_market {test_market!r} not in {market_col}; values: {', '.join(sorted(per_market))}")
    usable = []
    for m in markets:
        if len(per_market[m]) < min_periods:
            ctx.missing.append(f"{m}: only {len(per_market[m])} periods (needs {min_periods}); listed as unsuitable.")
        else:
            usable.append(m)
    pairs = []
    tests = [folded[test_market.casefold()]] if test_market else usable
    for t in tests:
        for c in usable:
            if c == t:
                continue
            common = sorted(set(per_market[t]) & set(per_market[c]))
            if len(common) < min_periods:
                continue
            xs, ys = [per_market[t][d] for d in common], [per_market[c][d] for d in common]
            mt, mc = _mean(xs) or 0.0, _mean(ys) or 0.0
            r = _pearson(xs, ys)
            cv_t = (_sd(xs) or 0.0) / mt if mt else None
            cv_c = (_sd(ys) or 0.0) / mc if mc else None
            cv_diff = None if cv_t is None or cv_c is None else abs(cv_t - cv_c)
            ratio = None if not mc else mt / mc
            gaps = [x / mt - y / mc for x, y in zip(xs, ys)] if mt and mc else []
            sd_gap = _sd(gaps)
            mdl = None if sd_gap is None else _Z95 * sd_gap / math.sqrt(test_weeks) * 100
            if r is None:
                score = None
                verdict = "unsuitable"
            else:
                score = 0.6 * max(r, 0.0) + 0.2 * (1 - min(1.0, (cv_diff or 0) / 0.5)) + 0.2 * (1 - min(1.0, abs(math.log(ratio)) / math.log(3))) if ratio else 0.6 * max(r, 0.0)
                verdict = "comparable" if r >= 0.8 else "weak" if r >= 0.6 else "unsuitable"
            pairs.append(
                {
                    "test": t,
                    "control": c,
                    "periods": len(common),
                    "level_ratio": _r(ratio, 2),
                    "trend_correlation": _r(r, 2),
                    "cv_test": _r(cv_t, 3),
                    "cv_control": _r(cv_c, 3),
                    "cv_difference": _r(cv_diff, 3),
                    "score": _r(score, 2),
                    "verdict": verdict,
                    "min_detectable_lift_pct": _r(mdl, 1),
                }
            )
    pairs.sort(key=lambda p: (-(p["score"] or -1), p["test"], p["control"]))
    best = pairs[0] if pairs and pairs[0]["verdict"] != "unsuitable" else None
    first_dates = [min(s) for s in per_market.values() if s]
    last_dates = [max(s) for s in per_market.values() if s]
    out = {
        "market_column": market_col,
        "metric": metric,
        "test_weeks": test_weeks,
        "pre_period": {"start": min(first_dates).isoformat() if first_dates else None, "end": max(last_dates).isoformat() if last_dates else None, "markets": {m: len(s) for m, s in per_market.items()}},
        "pairs": pairs,
        "best_pair": None if best is None else {"test": best["test"], "control": best["control"]},
        "readout_rule": "lift = (test_during ÷ test_pre) − (control_during ÷ control_pre), in % of the test market's pre level; believe it only above min_detectable_lift_pct for the chosen pair",
        "confounds": confounds,
        "method": (
            "Per pair over the periods both markets share: level_ratio = mean test ÷ mean control; trend_correlation = Pearson r of the two series; cv = sd ÷ mean; "
            "score = 0.6 × max(r, 0) + 0.2 × (1 − cv difference ÷ 0.5, floored at 0) + 0.2 × (1 − |ln level_ratio| ÷ ln 3, floored at 0); comparable when r ≥ 0.8, weak when ≥ 0.6, else unsuitable; "
            f"min_detectable_lift_pct = 1.96 × sd of the weekly relative gap (test ÷ mean test − control ÷ mean control) ÷ √test_weeks × 100 over {test_weeks} test weeks. Markets with fewer than min_periods periods are not scored."
        ),
    }
    if best:
        text = f"Best pair {best['test']} vs {best['control']}: r = {best['trend_correlation']}, {best['test']} sells {best['level_ratio']}× {best['control']}, a lift of at least {best['min_detectable_lift_pct']} % over {test_weeks} weeks is readable above noise"
        rejected = [p for p in pairs if p["test"] == best["test"] and p["verdict"] == "unsuitable"]
        if rejected:
            text += "; unsuitable: " + ", ".join(f"{p['control']} (r = {p['trend_correlation']})" for p in rejected[:3])
    else:
        text = "No comparable pair: " + ("no market has enough periods" if not usable else "no pair reaches r 0.6")
    out["summary"] = text + "."
    return out


# --- campaign_compare (#477, #478) ----------------------------------------------------------


def _op_campaign_compare(ctx: _Ctx) -> dict[str, Any]:
    args = ctx.args
    items: list[dict[str, Any]] = []
    campaigns = args.get("campaigns")
    if isinstance(campaigns, list) and campaigns:
        for c in campaigns:
            if not isinstance(c, dict):
                raise ToolError("each campaign is an object {name, sov_pct, som_start_pct, som_end_pct, months, spend_eur, brand_type}")
            items.append(dict(c))
    elif ctx.header:
        name_col = _detect(ctx.header, args.get("name_column"), ("brand", "campaign", "name", "prekės ženklas"), "name", True)
        cols = {k: ctx.col(f"{k}_column", required=False) for k in ("sov_pct", "esov_pts", "som_start_pct", "som_end_pct", "share_change_pp", "months", "spend_eur", "brand_type")}
        months_default = _float_of(args, "months")
        for r in ctx.rows:
            item: dict[str, Any] = {"name": _text(r.get(name_col))}
            for k, col in cols.items():
                if col:
                    item[k] = r.get(col)
            if "months" not in item and months_default is not None:
                item["months"] = months_default
            items.append(item)
    else:
        raise ToolError("campaign_compare needs campaigns[] inline or a path with name_column and the sov/som/months columns")
    rows_out = []
    for item in items:
        name = _text(item.get("name")) or f"row {len(rows_out) + 1}"
        sov, esov = _float_of(item, "sov_pct"), _float_of(item, "esov_pts")
        s0, s1, change = _float_of(item, "som_start_pct"), _float_of(item, "som_end_pct"), _float_of(item, "share_change_pp")
        months, spend = _float_of(item, "months"), _float_of(item, "spend_eur")
        brand_type = _text(item.get("brand_type")).lower() or None
        if change is None and s0 is not None and s1 is not None:
            change = s1 - s0
        if esov is None and sov is not None and s0 is not None:
            esov = sov - s0
        missing_fields = [f for f, v in (("sov_pct or esov_pts", esov), ("som change (som_start/end or share_change_pp)", change), ("months", months)) if v is None]
        row: dict[str, Any] = {"name": name, "sov_pct": sov, "som_start_pct": s0, "som_end_pct": s1, "months": months, "spend_eur": spend, "brand_type": brand_type, "esov_pts": _r(esov, 2), "share_change_pp": _r(change, 2)}
        if missing_fields:
            row.update({"status": "incomplete", "reason": "missing " + ", ".join(missing_fields), "annualized_change_pp": None, "growth_per_esov_point": None})
            ctx.missing.append(f"{name}: {row['reason']}.")
        else:
            annual = change * 12 / months  # type: ignore[operator]
            row["annualized_change_pp"] = round(annual, 2)
            if esov <= 0:  # type: ignore[operator]
                row["growth_per_esov_point"] = None
                row["growth_reason"] = "ESOV ≤ 0: no excess voice to divide by"
            else:
                row["growth_per_esov_point"] = round(annual / esov, 3)  # type: ignore[operator]
            row["spend_per_pp_annualized"] = None if spend is None or annual <= 0 else _num(spend / annual)
            row["status"] = "ok"
        rows_out.append(row)
    scored = [r for r in rows_out if r.get("growth_per_esov_point") is not None]
    ranking = [r["name"] for r in sorted(scored, key=lambda r: -r["growth_per_esov_point"])]
    warnings_cmp = []
    comparable = None
    comparability: dict[str, Any] = {}
    ok_rows = [r for r in rows_out if r.get("status") == "ok"]
    if len(ok_rows) >= 2:
        months_list = [r["months"] for r in ok_rows]
        spends = [r["spend_eur"] for r in ok_rows if r["spend_eur"]]
        esovs = [r["esov_pts"] for r in ok_rows]
        duration_ratio = max(months_list) / min(months_list) if min(months_list) else None
        spend_ratio = max(spends) / min(spends) if len(spends) == len(ok_rows) and min(spends) else None
        if duration_ratio is not None and duration_ratio >= 2:
            warnings_cmp.append(f"durations differ {duration_ratio:g}×: match duration before comparing")
        if spend_ratio is not None and spend_ratio >= 2:
            warnings_cmp.append(f"spending weight differs {spend_ratio:g}×")
        types = {r["brand_type"] for r in ok_rows}
        if len(types) > 1:
            warnings_cmp.append("mixed brand types: do not pool established, launch and niche brands")
        comparability = {"duration_ratio": _r(duration_ratio, 2), "spend_ratio": _r(spend_ratio, 2), "esov_gap_pts": _r(max(esovs) - min(esovs), 2), "comparable": not warnings_cmp, "warnings": warnings_cmp}
        comparable = not warnings_cmp
    benchmark = None
    if any(r["brand_type"] for r in rows_out):
        groups: dict[str, list[float]] = {}
        untyped = 0
        for r in scored:
            if r["brand_type"]:
                groups.setdefault(r["brand_type"], []).append(r["growth_per_esov_point"])
            else:
                untyped += 1
        group_out = {t: {"n": len(v), "median_growth_per_pt": _num(_median(v) or 0.0), "mean_growth_per_pt": round(_mean(v) or 0.0, 3), "too_few": len(v) < 3} for t, v in groups.items()}
        pooled_vals = [r["growth_per_esov_point"] for r in scored]
        pooled = {"n": len(pooled_vals), "median_growth_per_pt": None if not pooled_vals else _num(_median(pooled_vals) or 0.0), "mean_growth_per_pt": None if not pooled_vals else round(_mean(pooled_vals) or 0.0, 3)}
        reasons = []
        if untyped:
            reasons.append(f"brand_type missing for {untyped} rows: no pooled verdict")
        est = group_out.get("established")
        if est and pooled["mean_growth_per_pt"] is not None and est["mean_growth_per_pt"] and abs(pooled["mean_growth_per_pt"] - est["mean_growth_per_pt"]) / abs(est["mean_growth_per_pt"]) > 0.5:
            reasons.append(f"pooled mean {pooled['mean_growth_per_pt']} differs from the established group {est['mean_growth_per_pt']} by more than 50 %")
        benchmark = {"groups": group_out, "pooled": pooled, "contamination": {"flag": bool(reasons), "reason": "; ".join(reasons) or None}}
    out = {
        "rows": rows_out,
        "ranking": ranking,
        "comparability": comparability or None,
        "benchmark": benchmark,
        "method": (
            "ESOV = share of voice − market share at start (pts); share change = end − start (pp); annualized change = change × 12 ÷ months; growth_per_esov_point = annualized change ÷ ESOV (null when ESOV ≤ 0); "
            "spend_per_pp_annualized = spend ÷ annualized change. Comparability warns when durations or spends differ 2× or more, or brand types are mixed. Benchmark: median and mean growth per point per brand_type with n; "
            "pooled is contaminated when any row lacks brand_type or the pooled mean differs from the established group by more than 50 %."
        ),
    }
    bits = [f"{r['name']}: ESOV {r['esov_pts']:g} pts, {r['annualized_change_pp']:+g} pp/year → {r['growth_per_esov_point']} pp per ESOV point" for r in scored[:4]]
    text = "; ".join(bits) or "no complete campaign row"
    if len(ranking) >= 2:
        text += f"; most efficient per point: {ranking[0]}"
        if comparable is False:
            text += " (indicative only: " + "; ".join(warnings_cmp) + ")"
    if benchmark:
        text += "; per brand type: " + ", ".join(f"{t} {g['mean_growth_per_pt']} (n={g['n']}{', too few' if g['too_few'] else ''})" for t, g in benchmark["groups"].items())
        if benchmark["contamination"]["flag"]:
            text += f"; pooled {benchmark['pooled']['mean_growth_per_pt']} is contaminated ({benchmark['contamination']['reason']})"
    out["summary"] = text + "."
    return out


# --- session_log (#79) ---------------------------------------------------------------------


def _op_session_log(ctx: _Ctx) -> dict[str, Any]:
    args = ctx.args
    session_col = ctx.col("session_column")
    action_col = ctx.col("action_column")
    source_col = ctx.col("source_column", required=False)
    reason_col = ctx.col("reason_column", required=False)
    kinds = {"open": ["open", "opened", "atidarė", "atidaryta", "view"], "discard": ["discard", "discarded", "drop", "atmetė", "atmesta", "closed"], "revisit": ["revisit", "revisited", "back", "grįžo", "return"]}
    aliases = args.get("action_values")
    if isinstance(aliases, dict):
        for k, v in aliases.items():
            if str(k).lower() in kinds:
                kinds[str(k).lower()] = [s.casefold() for s in (_list(v, "action_values") or [])]
    sessions: dict[str, list[dict[str, Any]]] = {}
    blank = 0
    unknown: dict[str, int] = {}
    counts = {"open": 0, "discard": 0, "revisit": 0}
    per_source: dict[str, dict[str, int]] = {}
    reasons: dict[str, int] = {}
    opened_per_session: dict[str, set[str]] = {}
    for r in ctx.rows:
        sid = _text(r.get(session_col))
        if not sid:
            blank += 1
            continue
        sessions.setdefault(sid, []).append(r)
        action = _text(r.get(action_col)).casefold()
        kind = next((k for k, vals in kinds.items() if action in vals), None)
        if kind is None:
            unknown[action or "(blank)"] = unknown.get(action or "(blank)", 0) + 1
            continue
        counts[kind] += 1
        source = _text(r.get(source_col)) if source_col else ""
        if source:
            per_source.setdefault(source, {"open": 0, "discard": 0, "revisit": 0})[kind] += 1
        if kind == "open" and source:
            opened_per_session.setdefault(sid, set()).add(source)
        if kind == "discard" and reason_col:
            reason = _text(r.get(reason_col))
            if reason:
                reasons[reason] = reasons.get(reason, 0) + 1
    if blank:
        ctx.missing.append(f"{_plural(blank, 'row')} with a blank {session_col} left out.")
    if unknown:
        ctx.missing.append(f"{_plural(sum(unknown.values()), 'row')} with an action outside open/discard/revisit left out: " + ", ".join(f"{k} ({v})" for k, v in sorted(unknown.items(), key=lambda kv: -kv[1])[:5]) + ".")
    n_sessions = len(sessions)
    actions = sum(counts.values())
    status = "ok" if n_sessions >= 2 else "unsuitable"
    if status == "unsuitable":
        ctx.warnings.append(f"Only {n_sessions} session(s): fewer than 2, so counts are listed but nothing is ranked.")
    elif n_sessions < ctx.min_rows:
        ctx.warnings.append(f"{n_sessions} sessions: below {ctx.min_rows}, read the shares as rough.")
    sources = [{"source": s, **c, "revisit_share_of_actions_pct": _pct(c["revisit"], actions)} for s, c in per_source.items()]
    sources.sort(key=lambda s: (-(s["open"] + s["discard"] + s["revisit"]), s["source"]))
    opened = sorted(float(len(v)) for v in opened_per_session.values()) + [0.0] * (n_sessions - len(opened_per_session))
    top_reasons = [{"reason": k, "count": v} for k, v in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))[:5]] if status == "ok" else []
    out = {
        "session_column": session_col,
        "sessions": n_sessions,
        "actions": actions,
        "status": status,
        "counts": counts,
        "revisit_rate_pct": _pct(counts["revisit"], actions),
        "discard_rate_pct": _pct(counts["discard"], actions),
        "median_options_opened_per_session": None if not opened else _num(_median(opened) or 0.0),
        "by_source": sources if status == "ok" else [],
        "top_discard_reasons": top_reasons,
        "method": "One row per action; actions mapped to open / discard / revisit by value (override with action_values); revisit rate = revisits ÷ all mapped actions; options opened per session = distinct sources opened; discard reasons are verbatim counts; nothing is ranked with fewer than 2 sessions.",
    }
    text = f"{n_sessions} sessions, {actions} actions: {counts['open']} opens, {counts['discard']} discards, {counts['revisit']} revisits ({out['revisit_rate_pct'] if out['revisit_rate_pct'] is not None else 'n/a'} %)"
    if out["median_options_opened_per_session"] is not None:
        text += f"; median {_fmt(out['median_options_opened_per_session'])} options opened per session"
    if sources and status == "ok":
        top = max(sources, key=lambda s: s["revisit"])
        if top["revisit"]:
            text += f"; most revisited {top['source']} ({top['revisit']} revisits, {top['revisit_share_of_actions_pct']:g} % of actions)"
    if top_reasons:
        text += f"; top discard reason “{top_reasons[0]['reason']}” ({top_reasons[0]['count']})"
    out["summary"] = text + "."
    return out


# --- customer_audit (#483, #484) ---------------------------------------------------------------


def _norm_phone(text: str) -> str:
    digits = re.sub(r"\D", "", text)
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("370") and len(digits) == 11:
        return digits[3:]
    if digits.startswith("8") and len(digits) == 9:
        return digits[1:]
    return digits


def _norm_email(text: str) -> str:
    e = text.strip().lower()
    if "@" not in e:
        return e
    local, domain = e.rsplit("@", 1)
    if domain in {"gmail.com", "googlemail.com"}:
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


def _op_customer_audit(ctx: _Ctx) -> dict[str, Any]:
    args = ctx.args
    mode = str(args.get("mode") or "both").lower()
    if mode not in {"duplicates", "stale", "both"}:
        raise ToolError("mode must be duplicates, stale or both")
    phone_col = _detect(ctx.header, args.get("phone_column"), PHONE_HEADERS, "phone", False)
    email_col = _detect(ctx.header, args.get("email_column"), EMAIL_HEADERS, "email", False)
    name_col = _detect(ctx.header, args.get("name_column"), NAME_HEADERS, "name", False)
    source_col = ctx.col("source_column", required=False)
    id_col = ctx.col("id_column", required=False)
    fuzzy = args.get("fuzzy_names") in (None, "", True, "true", 1)
    rows = ctx.rows
    n = len(rows)
    labels = [(_text(r.get(id_col)) if id_col else "") or str(i + 1) for i, r in enumerate(rows)]
    out: dict[str, Any] = {"rows": n, "columns": {"phone": phone_col, "email": email_col, "name": name_col, "source": source_col}, "mode": mode}
    summaries = []
    if mode in {"duplicates", "both"}:
        if phone_col is None and email_col is None:
            raise ToolError(f"neither a phone nor an email column was found; columns: {', '.join(ctx.header)}. Give phone_column or email_column.")
        parent = list(range(n))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i: int, j: int) -> None:
            parent[find(i)] = find(j)

        keys: dict[tuple[str, str], list[int]] = {}
        no_contact = []
        for i, r in enumerate(rows):
            p = _norm_phone(_text(r.get(phone_col))) if phone_col else ""
            e = _norm_email(_text(r.get(email_col))) if email_col else ""
            if p and len(p) >= 6:
                keys.setdefault(("phone", p), []).append(i)
            if e and "@" in e:
                keys.setdefault(("email", e), []).append(i)
            if not (p and len(p) >= 6) and not (e and "@" in e):
                no_contact.append(i)
        linked: dict[int, set[str]] = {}
        for (kind, key), members in keys.items():
            if len(members) > 1:
                for m in members[1:]:
                    union(members[0], m)
                for m in members:
                    linked.setdefault(m, set()).add(kind)
        clusters: dict[int, list[int]] = {}
        for i in range(n):
            if i in linked:
                clusters.setdefault(find(i), []).append(i)
        confirmed = []
        cross_source = 0
        for members in clusters.values():
            if len(members) < 2:
                continue
            sources = sorted({_text(rows[m].get(source_col)) for m in members if source_col and _text(rows[m].get(source_col))})
            kinds = sorted({k for m in members for k in linked.get(m, set())})
            if len(sources) > 1:
                cross_source += 1
            confirmed.append({"key_type": "+".join(kinds), "rows": [labels[m] for m in members], "sources": sources})
        confirmed.sort(key=lambda c: c["rows"])
        confirmed_surplus = sum(len(c["rows"]) - 1 for c in confirmed)
        candidates = []
        candidate_surplus = 0
        if fuzzy and name_col:
            by_name: dict[str, list[int]] = {}
            for i, r in enumerate(rows):
                nm = re.sub(r"\s+", " ", _text(r.get(name_col)).casefold())
                if nm:
                    by_name.setdefault(nm, []).append(i)
            for nm, members in by_name.items():
                roots = {find(m) for m in members}
                if len(members) > 1 and len(roots) > 1:
                    distinct = sorted({find(m) for m in members})
                    candidates.append({"name": _text(rows[members[0]].get(name_col)), "rows": [labels[m] for m in members], "reason": "same name, different contacts; needs confirmation"})
                    candidate_surplus += len(distinct) - 1
        if no_contact:
            ctx.missing.append(f"{_plural(len(no_contact), 'row')} without a usable phone or email: {', '.join(labels[i] for i in no_contact[:10])}.")
        out["duplicates"] = {
            "confirmed_clusters": confirmed,
            "candidate_clusters": candidates,
            "cross_source_matches": cross_source,
            "rows_without_contact": len(no_contact),
            "distinct_customers": {"min": n - confirmed_surplus - candidate_surplus, "max": n - confirmed_surplus},
            "remove_for_send": sorted({c["rows"][k] for c in confirmed for k in range(1, len(c["rows"]))}),
        }
        summaries.append(
            f"{n} rows: {len(confirmed)} confirmed duplicate cluster(s)" + (f" ({cross_source} across sources)" if cross_source else "") + f", {len(candidates)} name-only candidate(s) to confirm, {len(no_contact)} without usable contact; "
            f"distinct customers {out['duplicates']['distinct_customers']['min']}–{out['duplicates']['distinct_customers']['max']}, not {n}"
        )
    if mode in {"stale", "both"}:
        act_cols = [_column(c, ctx.header, "last_activity_column") for c in (_list(args.get("last_activity_column"), "last_activity_column") or [])]
        months = _int_of(args, "stale_after_months", 18)
        as_of = _date_of(args, "as_of") or date.today()
        bounce_col = ctx.col("bounce_column", required=False)
        consent_col = ctx.col("consent_column", required=False)
        positives = _positive_set(args)
        cutoff_year, cutoff_month = divmod(as_of.year * 12 + as_of.month - 1 - months, 12)
        cutoff = date(cutoff_year, cutoff_month + 1, min(as_of.day, 28))
        dated: list[tuple[int, date]] = []
        undated = []
        for i, r in enumerate(rows):
            ds = [d for d in (_parse_date(r.get(c)) for c in act_cols) if d]
            if ds:
                dated.append((i, max(ds)))
            else:
                undated.append(i)
        stale = {i for i, d in dated if d < cutoff}
        invalid = {i for i, r in enumerate(rows) if email_col and _text(r.get(email_col)) and not _EMAIL_RE.match(_text(r.get(email_col)))}
        role = {i for i, r in enumerate(rows) if email_col and _text(r.get(email_col)).split("@")[0].lower() in ROLE_LOCAL_PARTS}
        bounced = {i for i, r in enumerate(rows) if bounce_col and _is_positive(r.get(bounce_col), positives)}
        no_consent = {i for i, r in enumerate(rows) if consent_col and _text(r.get(consent_col)) and not _is_positive(r.get(consent_col), positives)}
        if not act_cols:
            ctx.missing.append("no last_activity_column: stale share not computed, only syntax, role and bounce findings.")
        elif undated:
            ctx.missing.append(f"{_plural(len(undated), 'row')} without a readable activity date left out of the stale share: {', '.join(labels[i] for i in undated[:10])}.")
        unsendable = invalid | bounced | no_consent
        dates_only = [d for _, d in dated]
        out["stale"] = {
            "stale_after_months": months,
            "as_of": as_of.isoformat(),
            "cutoff": cutoff.isoformat(),
            "activity_columns": act_cols,
            "dated_rows": len(dated),
            "undated_rows": len(undated),
            "stale_rows": len(stale),
            "stale_share_pct": _pct(len(stale), len(dated)),
            "oldest": min(dates_only).isoformat() if dates_only else None,
            "newest": max(dates_only).isoformat() if dates_only else None,
            "invalid_email_rows": len(invalid),
            "role_address_rows": len(role),
            "bounced_rows": len(bounced),
            "no_consent_rows": len(no_consent),
            "sendable_estimate": {"max": n - len(unsendable), "min": n - len(unsendable | stale)},
            "stale_row_labels": [labels[i] for i in sorted(stale)][:50],
        }
        if act_cols:
            summaries.append(
                f"{len(dated)} dated rows, {len(stale)} stale (older than {months} months before {as_of.isoformat()}, {out['stale']['stale_share_pct'] if out['stale']['stale_share_pct'] is not None else 'n/a'} % of dated rows), "
                f"{len(undated)} undated, {len(invalid)} invalid email(s), {len(role)} role address(es), {len(bounced)} bounced; sendable {out['stale']['sendable_estimate']['min']}–{out['stale']['sendable_estimate']['max']} of {n}"
            )
        else:
            summaries.append(f"{len(invalid)} invalid email(s), {len(role)} role address(es), {len(bounced)} bounced; no activity date column, so no stale share")
    out["method"] = (
        "Duplicates: phones reduced to digits with the +370 / 8 prefix dropped, emails lower-cased (Gmail dots and +tags removed); rows sharing a phone or an email are one confirmed cluster (union-find); rows sharing only a name are candidates, never merged; "
        "distinct customers: max = rows − confirmed surplus, min = also minus candidate surplus. Stale: the latest of the activity columns is compared with as_of − stale_after_months; stale share is of dated rows; "
        "invalid email by shape, role addresses by local part (info, office, sales…); sendable max = rows − (invalid ∪ bounced ∪ no consent), min = also minus stale rows."
    )
    out["summary"] = "; ".join(summaries) + "."
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
    "cohort_compare": _op_cohort_compare,
    "change_impact": _op_change_impact,
    "media_response": _op_media_response,
    "price_response": _op_price_response,
    "promo_effect": _op_promo_effect,
    "choice_mix": _op_choice_mix,
    "basket": _op_basket,
    "buyer_frequency": _op_buyer_frequency,
    "panel": _op_panel,
    "survey": _op_survey,
    "brand_recall": _op_brand_recall,
    "keyword_demand": _op_keyword_demand,
    "page_breadth": _op_page_breadth,
    "market_match": _op_market_match,
    "campaign_compare": _op_campaign_compare,
    "session_log": _op_session_log,
    "customer_audit": _op_customer_audit,
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
    inline = op in INLINE_OPS and (isinstance(args.get("before"), dict) or isinstance(args.get("campaigns"), list))
    if not path and not inline:
        if op in INLINE_OPS:
            raise ToolError(f"{op} needs {'campaigns[]' if op == 'campaign_compare' else 'before/after {price, units}'} inline or a path to a CSV or XLSX file inside the workspace")
        raise ToolError("path is required: a CSV or XLSX file inside the workspace")
    try:
        min_rows = int(args.get("min_rows") if args.get("min_rows") not in (None, "") else DEFAULT_MIN_ROWS)
    except (TypeError, ValueError) as exc:
        raise ToolError("min_rows must be a whole number") from exc
    if min_rows < 1:
        raise ToolError("min_rows must be at least 1")

    if path:
        header, rows, notes = _load_table(tools, path, args.get("sheet"), args.get("header_row"))
    else:
        header, rows, notes = [], [], []
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

    ctx = _Ctx(args, header, kept, min_rows, missing, warnings, tools)
    result = _OPS[op](ctx)
    out: dict[str, Any] = {
        "op": op,
        "inputs": {
            "path": path or None,
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
