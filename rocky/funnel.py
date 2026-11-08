"""Funnel arithmetic from the marketer's own file, so workers stop describing funnels in prose.

``funnel_analyze`` reads a CSV or XLSX inside the workspace, sums each stage over
the rows that fall in the date window, and reports the counts, the denominators
behind every percentage, the step that loses the most, the dates actually covered
and every data problem it met. Nothing is estimated: a blank cell is reported as
blank, a bad number drops its row and says so, an empty window returns zeros with
the reason under ``missing``.

Optional additions on top of the plain funnel (each appears only when asked for):

* ``compare_groups`` / ``compare_stage`` — pairwise two-proportion z-tests between
  groups, with ``cost_per_group`` / ``cost_column`` for net cost per converted unit;
* ``metrics`` — per-group mean and median of numeric columns (share of wallet,
  treatment equality);
* ``lag_from`` / ``lag_to`` — days between two dates, bucketed, with ``maturity_days``;
* ``bucket_by`` — time slots (hour, weekday) with the slots covering most volume;
* ``derived_stages`` — stages built from column rules on a per-user table;
* ``filter``, ``cohort_by``, ``periods`` — row filter and groups derived from dates;
* ``paid_at_stage`` — units paid for but never reaching the final stage;
* a two-column ``group_by`` — teller × listener matrix.

Separate ``mode`` values read the same files for other questions: ``buyer_classes``
(promotion substitution by prior buying frequency), ``offers`` (timed discounts over
time), ``trend`` (cost of a new buyer per launch period), ``recruits`` (buyers of a
new product who are new to the brand) and ``categories`` (counts and shares of a
category column).
"""

from __future__ import annotations

import csv
import json
import re
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import NormalDist
from typing import Any

from rocky.tools import ToolError, WorkspaceTools

# Groups with fewer first-stage units than this are listed but not ranked as "worst":
# a 2-of-5 funnel says nothing about the channel.
MIN_GROUP_FIRST_STAGE = 30
# Below this many at the first stage the percentages are rough and the output says so.
SMALL_FUNNEL = 100
# Below this many at the first stage a group comparison gets no verdict.
MIN_COMPARE_N = 100
# compare_groups defaults to every group only up to this many groups.
MAX_COMPARE_GROUPS = 6
# Group date windows that differ by more than this many days are not concurrent.
CONCURRENT_DAYS = 7
BLANK_GROUP = "(blank)"
_DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%Y/%m/%d", "%Y.%m.%d", "%d/%m/%Y", "%Y%m%d")
_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%d/%m/%Y %H:%M",
)
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_Z = NormalDist()
_Z975 = _Z.inv_cdf(0.975)
MODES = ("funnel", "buyer_classes", "offers", "trend", "recruits", "categories")


# --- reading the file ------------------------------------------------------


def _load_rows(tools: WorkspaceTools, path: str, sheet: str | None) -> tuple[list[str], list[dict[str, Any]]]:
    """Header names and one dict per data row. Values stay raw (strings from CSV, cells from XLSX)."""
    target = tools.resolve(path)
    if not target.exists():
        raise ToolError(f"not found: {path}")
    if target.is_dir():
        raise ToolError(f"is a directory: {path}")
    suffix = target.suffix.lower()
    if suffix in {".csv", ".tsv", ".txt"}:
        return _load_csv(target)
    if suffix in {".xlsx", ".xlsm"}:
        return _load_xlsx(target, sheet)
    raise ToolError(f"unsupported file type {suffix or '(none)'} for {path}: give a .csv or .xlsx file")


def _load_csv(target: Path) -> tuple[list[str], list[dict[str, Any]]]:
    text = target.read_text(encoding="utf-8-sig", errors="replace")
    if not text.strip():
        raise ToolError(f"{target.name} is empty")
    sample = text[:4096]
    try:
        dialect: Any = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(text.splitlines(), dialect)
    rows = [row for row in reader]
    if not rows:
        raise ToolError(f"{target.name} is empty")
    header = [str(h).strip() for h in rows[0]]
    out = []
    for row in rows[1:]:
        if not any(str(c).strip() for c in row):
            continue
        out.append({header[i]: (row[i] if i < len(row) else "") for i in range(len(header))})
    return header, out


def _load_xlsx(target: Path, sheet: str | None) -> tuple[list[str], list[dict[str, Any]]]:
    from openpyxl import load_workbook

    wb = load_workbook(target, data_only=True, read_only=True)
    try:
        if sheet:
            if sheet not in wb.sheetnames:
                raise ToolError(f"sheet {sheet!r} not found in {target.name}; sheets: {', '.join(wb.sheetnames)}")
            ws = wb[sheet]
        else:
            ws = wb.worksheets[0]
        rows_iter = ws.iter_rows(values_only=True)
        header_row = next(rows_iter, None)
        if header_row is None:
            raise ToolError(f"{target.name} has no header row")
        header = ["" if h is None else str(h).strip() for h in header_row]
        out = []
        for row in rows_iter:
            if row is None or not any(c is not None and str(c).strip() for c in row):
                continue
            out.append({header[i]: (row[i] if i < len(row) else None) for i in range(len(header)) if header[i]})
        return [h for h in header if h], out
    finally:
        wb.close()


def _column(name: Any, header: list[str], what: str) -> str:
    """The header entry matching ``name`` (exact, then case- and space-insensitive) or a ToolError listing the columns."""
    if name is None or str(name).strip() == "":
        raise ToolError(f"{what} is required")
    wanted = str(name).strip()
    if wanted in header:
        return wanted
    folded = {h.strip().lower(): h for h in header}
    if wanted.lower() in folded:
        return folded[wanted.lower()]
    raise ToolError(f"{what} column {wanted!r} not found; columns found: {', '.join(header) or '(none)'}")


def _optional_column(args: dict, key: str, header: list[str]) -> str | None:
    return _column(args[key], header, key) if args.get(key) else None


def _list(value: Any, key: str) -> list[str] | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        items = [v.strip() for v in value.split(",")]
    elif isinstance(value, (list, tuple)):
        items = [str(v).strip() for v in value]
    else:
        raise ToolError(f"{key} must be a list of column names")
    items = [v for v in items if v]
    return items or None


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _int_arg(args: dict, key: str, default: int, minimum: int = 1) -> int:
    value = args.get(key)
    if value is None or value == "":
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ToolError(f"{key} must be a whole number") from None
    if number < minimum:
        raise ToolError(f"{key} must be at least {minimum}")
    return number


def _float_arg(args: dict, key: str, default: float) -> float:
    value = args.get(key)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ToolError(f"{key} must be a number") from None


def _date_arg(args: dict, key: str, required: bool = False) -> date | None:
    value = args.get(key)
    if value is None or value == "":
        if required:
            raise ToolError(f"{key} is required (ISO date YYYY-MM-DD)")
        return None
    parsed = _parse_date(value)
    if parsed is None:
        raise ToolError(f"{key} {value!r} is not a date; use YYYY-MM-DD")
    return parsed


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


# --- parsing cells ---------------------------------------------------------


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    head = text[:10]
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        for candidate in (head, text):
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    return None


def _parse_datetime(value: Any) -> tuple[datetime | None, bool]:
    """(datetime, has_time). A date without a clock time parses but has_time is False."""
    if value is None:
        return None, False
    if isinstance(value, datetime):
        return value.replace(tzinfo=None), True
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day), False
    text = str(value).strip()
    if not text:
        return None, False
    has_time = bool(re.search(r"\d{1,2}:\d{2}", text))
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None), has_time
    except ValueError:
        pass
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt), True
        except ValueError:
            continue
    d = _parse_date(text)
    return (datetime(d.year, d.month, d.day), False) if d else (None, False)


_THOUSANDS = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")


def _parse_number(value: Any) -> tuple[float | None, str]:
    """(number, status) where status is ok, blank or bad. Negative numbers are allowed."""
    if value is None:
        return None, "blank"
    if isinstance(value, bool):
        return (1.0 if value else 0.0), "ok"
    if isinstance(value, (int, float)):
        number = float(value)
        return (None, "bad") if number != number or number in (float("inf"), float("-inf")) else (number, "ok")
    text = str(value).strip().replace(" ", "").replace(" ", "").rstrip("%")
    if not text:
        return None, "blank"
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


def _parse_count(value: Any) -> tuple[float | None, str]:
    """(number, status) where status is ok, blank or bad. Counts may not be negative."""
    number, status = _parse_number(value)
    if status == "ok" and number is not None and number < 0:
        return None, "bad"
    return number, status


def _parse_flag(value: Any) -> bool | None:
    """True/False for a yes/no, 1/0 or percent cell; None for a blank. Any other text counts as flagged."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value) > 0
    text = str(value).strip().lower()
    if not text:
        return None
    if text in {"true", "yes", "y", "taip", "t", "x", "1", "discount", "promo", "akcija"}:
        return True
    if text in {"false", "no", "n", "ne", "f", "0", "none", "-"}:
        return False
    number, status = _parse_number(text)
    if status == "ok" and number is not None:
        return number > 0
    return True


def _num(value: float) -> int | float:
    return int(round(value)) if abs(value - round(value)) < 1e-9 else round(value, 2)


def _pct(part: float, whole: float) -> float | None:
    return None if whole <= 0 else round(part / whole * 100, 1)


def _ratio(part: float, whole: float, digits: int = 2) -> float | None:
    return None if whole == 0 else round(part / whole, digits)


def _fmt(value: int | float) -> str:
    return f"{value:,}" if isinstance(value, int) else f"{value:,.2f}"


def _sum_column(rows: list[dict[str, Any]], col: str, missing: list[str], what: str | None = None) -> float:
    """Sum a numeric column; blanks count 0 and bad cells are skipped, both reported."""
    total = 0.0
    blank = bad = 0
    for row in rows:
        number, status = _parse_number(row.get(col))
        if status == "bad":
            bad += 1
        elif status == "blank":
            blank += 1
        else:
            total += number or 0.0
    label = what or col
    if blank:
        missing.append(f"{label}: {_plural(blank, 'blank cell')} counted as 0.")
    if bad:
        missing.append(f"{label}: {_plural(bad, 'cell')} that were not numbers skipped.")
    return total


# --- statistics ------------------------------------------------------------

TWO_PROPORTION_METHOD = (
    "two-proportion z-test with pooled standard error for the p-value; 95 % confidence interval of the "
    "difference from the unpooled normal approximation; two-sided; verdict at p < 0.05."
)


def _two_proportion(x1: float, n1: float, x2: float, n2: float) -> dict[str, Any] | None:
    """Group (x1/n1) minus baseline (x2/n2): difference in percentage points, 95 % CI, z and p-value."""
    if n1 <= 0 or n2 <= 0 or x1 > n1 or x2 > n2:
        return None
    p1, p2 = x1 / n1, x2 / n2
    diff = p1 - p2
    pooled = (x1 + x2) / (n1 + n2)
    se_pooled = (pooled * (1 - pooled) * (1 / n1 + 1 / n2)) ** 0.5
    z = diff / se_pooled if se_pooled > 0 else None
    p_value = 2 * (1 - _Z.cdf(abs(z))) if z is not None else None
    se = (p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2) ** 0.5
    return {
        "diff_pp": round(diff * 100, 2),
        "ci95_pp": [round((diff - _Z975 * se) * 100, 2), round((diff + _Z975 * se) * 100, 2)],
        "z": None if z is None else round(z, 2),
        "p_value": None if p_value is None else round(p_value, 4),
    }


# --- the funnel itself -----------------------------------------------------


def _funnel(names: list[str], counts: list[float]) -> dict[str, Any]:
    """Stages with their percentages, the largest absolute and relative losses, the overall rate and warnings."""
    first = counts[0]
    stages = []
    for i, (name, count) in enumerate(zip(names, counts)):
        prev = counts[i - 1] if i else None
        stages.append(
            {
                "name": name,
                "count": _num(count),
                "from_previous_pct": None if prev is None else _pct(count, prev),
                "from_first_pct": _pct(count, first),
            }
        )
    steps = []
    for i in range(1, len(counts)):
        lost = counts[i - 1] - counts[i]
        steps.append(
            {
                "from_stage": names[i - 1],
                "to_stage": names[i],
                "lost": _num(lost),
                "lost_pct_of_previous": _pct(lost, counts[i - 1]),
                "lost_pct_of_first": _pct(lost, first),
            }
        )
    positive = [s for s in steps if s["lost"] > 0]
    largest = max(positive, key=lambda s: s["lost"]) if positive else None
    relative_candidates = [s for s in positive if s["lost_pct_of_previous"] is not None]
    relative = max(relative_candidates, key=lambda s: s["lost_pct_of_previous"]) if relative_candidates else None
    warnings = []
    for i in range(1, len(counts)):
        if counts[i] > counts[i - 1]:
            warnings.append(
                f"{names[i]} ({_fmt(_num(counts[i]))}) is larger than {names[i - 1]} ({_fmt(_num(counts[i - 1]))}): "
                "stage order or data is wrong."
            )
    if 0 < first < SMALL_FUNNEL:
        warnings.append(f"Fewer than {SMALL_FUNNEL} at the first stage ({_fmt(_num(first))}): percentages are rough.")
    out: dict[str, Any] = {
        "stages": stages,
        "largest_dropoff": largest,
        "overall_conversion_pct": _pct(counts[-1], first),
        "warnings": warnings,
    }
    if relative is not None and largest is not None and relative is not largest:
        out["largest_relative_dropoff"] = relative
    return out


def _window_rows(
    rows: list[dict[str, Any]], date_col: str | None, date_from: date | None, date_to: date | None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rows inside the window plus the window description from the dates actually kept."""
    if date_col is None:
        return rows, {"from": None, "to": None, "rows_in_window": len(rows), "rows_total": len(rows), "rows_without_date": 0}
    kept, dates, without = [], [], 0
    for row in rows:
        d = _parse_date(row.get(date_col))
        if d is None:
            without += 1
            continue
        if date_from and d < date_from:
            continue
        if date_to and d > date_to:
            continue
        kept.append(row)
        dates.append(d)
    return kept, {
        "from": min(dates).isoformat() if dates else None,
        "to": max(dates).isoformat() if dates else None,
        "rows_in_window": len(kept),
        "rows_total": len(rows),
        "rows_without_date": without,
    }


def _wide_counts(rows: list[dict[str, Any]], stage_cols: list[str], missing: list[str]) -> list[float]:
    """Sum each stage column; blank cells count 0 and are reported; a bad number drops its row."""
    totals = [0.0] * len(stage_cols)
    blanks = [0] * len(stage_cols)
    dropped: dict[str, int] = {}
    for row in rows:
        parsed = []
        bad_in = None
        for col in stage_cols:
            number, status = _parse_count(row.get(col))
            if status == "bad":
                bad_in = col
                break
            parsed.append((number or 0.0, status))
        if bad_in is not None:
            dropped[bad_in] = dropped.get(bad_in, 0) + 1
            continue
        for i, (number, status) in enumerate(parsed):
            totals[i] += number
            if status == "blank":
                blanks[i] += 1
    for col, n in zip(stage_cols, blanks):
        if n:
            missing.append(f"{col}: {n} blank cell{'s' if n != 1 else ''} counted as 0.")
    for col, n in dropped.items():
        missing.append(f"{n} row{'s' if n != 1 else ''} dropped because {col} was not a number.")
    return totals


def _long_counts(
    rows: list[dict[str, Any]],
    stage_col: str,
    count_col: str | None,
    id_col: str | None,
    order: list[str] | None,
    missing: list[str],
) -> tuple[list[str], list[float]]:
    """Per-stage totals from one-row-per-event data: unique ids, summed counts, or rows."""
    seen: dict[str, Any] = {}
    names: list[str] = list(order or [])
    blank_stage = blank_count = blank_id = bad = 0
    for row in rows:
        stage = row.get(stage_col)
        stage = "" if stage is None else str(stage).strip()
        if not stage:
            blank_stage += 1
            continue
        if stage not in seen:
            seen[stage] = set() if id_col else 0.0
            if stage not in names:
                if order is None:
                    names.append(stage)
                else:
                    continue
        if id_col:
            ident = row.get(id_col)
            ident = "" if ident is None else str(ident).strip()
            if not ident:
                blank_id += 1
                continue
            seen[stage].add(ident)
        elif count_col:
            number, status = _parse_count(row.get(count_col))
            if status == "bad":
                bad += 1
                continue
            if status == "blank":
                blank_count += 1
            seen[stage] += number or 0.0
        else:
            seen[stage] += 1.0
    if blank_stage:
        missing.append(f"{blank_stage} row{'s' if blank_stage != 1 else ''} with a blank {stage_col} left out.")
    if blank_id:
        missing.append(f"{blank_id} row{'s' if blank_id != 1 else ''} with a blank {id_col} left out.")
    if blank_count:
        missing.append(f"{count_col}: {blank_count} blank cell{'s' if blank_count != 1 else ''} counted as 0.")
    if bad:
        missing.append(f"{bad} row{'s' if bad != 1 else ''} dropped because {count_col} was not a number.")
    if order is not None:
        absent = [s for s in order if s not in seen]
        if absent:
            missing.append(f"stage_order names stages absent from the data (counted as 0): {', '.join(absent)}.")
        extra = sorted(set(str(r.get(stage_col)).strip() for r in rows if r.get(stage_col) not in (None, "")) - set(order))
        if extra:
            missing.append(f"stages in the data but not in stage_order were ignored: {', '.join(extra)}.")
    counts = []
    for name in names:
        value = seen.get(name)
        counts.append(float(len(value)) if isinstance(value, set) else float(value or 0.0))
    return names, counts


def _summary(window: dict[str, Any], names: list[str], funnel: dict[str, Any], rows_desc: str) -> str:
    stages = funnel["stages"]
    first, last = stages[0], stages[-1]
    if window["from"] and window["to"]:
        head = f"Between {window['from']} and {window['to']}"
    else:
        head = rows_desc
    rate = funnel["overall_conversion_pct"]
    line = f"{head}: {_fmt(first['count'])} {first['name']} → {_fmt(last['count'])} {last['name']}"
    line += f" ({rate:g} %)." if rate is not None else " (no rate: the first stage is 0)."
    drop = funnel["largest_dropoff"]
    if drop is not None:
        prev = next(s for s in stages if s["name"] == drop["from_stage"])
        pct = drop["lost_pct_of_previous"]
        line += (
            f" Largest loss: {drop['from_stage']} → {drop['to_stage']}, {_fmt(drop['lost'])} of {_fmt(prev['count'])}"
            + (f" ({pct:g} %)." if pct is not None else ".")
        )
    else:
        line += " No step loses anything."
    return line


# --- row filters and derived columns ----------------------------------------


def _apply_filter(rows: list[dict[str, Any]], header: list[str], spec: Any, missing: list[str]) -> tuple[list[dict[str, Any]], dict | None]:
    """Keep rows whose column is in ``values`` and not in ``exclude_values`` (case-insensitive)."""
    if not spec:
        return rows, None
    if not isinstance(spec, dict):
        raise ToolError("filter must be an object {column, values?, exclude_values?}")
    col = _column(spec.get("column"), header, "filter.column")
    values = _list(spec.get("values"), "filter.values")
    exclude = _list(spec.get("exclude_values"), "filter.exclude_values")
    if not values and not exclude:
        raise ToolError("filter needs values (to keep) or exclude_values (to drop)")
    keep = {v.lower() for v in values or []}
    drop = {v.lower() for v in exclude or []}
    kept = []
    found: set[str] = set()
    for row in rows:
        cell = _text(row.get(col)).lower()
        found.add(cell)
        if keep and cell not in keep:
            continue
        if cell in drop:
            continue
        kept.append(row)
    absent = [v for v in values or [] if v.lower() not in found]
    if absent:
        missing.append(f"filter values not present in {col}: {', '.join(absent)}.")
    return kept, {"column": col, "values": values, "exclude_values": exclude, "rows_kept": len(kept), "rows_removed": len(rows) - len(kept)}


_RULE = re.compile(r"^(not_blank|blank|gt|gte|lt|lte|eq|in|after|within)(?::(.*))?$", re.S)
_RULE_REF = re.compile(r"^\s*([^+]+?)\s*(?:\+\s*(-?\d+)\s*d(?:ays?)?)?\s*$")
RULE_HELP = "not_blank, blank, gt:N, gte:N, lt:N, lte:N, eq:X, in:a|b, after:<column>+<N>d or within:<column>+<N>d"


def _rule_holds(value: Any, kind: str, arg: str | None, threshold: float | None, ref: Any, days: int) -> tuple[bool, bool]:
    """(rule holds, value readable)."""
    blank = _text(value) == ""
    if kind == "not_blank":
        return not blank, True
    if kind == "blank":
        return blank, True
    if blank:
        return False, True
    if kind == "eq":
        return _text(value).lower() == _text(arg).lower(), True
    if kind == "in":
        allowed = {a.strip().lower() for a in (arg or "").split("|") if a.strip()}
        return _text(value).lower() in allowed, True
    if kind in {"gt", "gte", "lt", "lte"}:
        number, status = _parse_number(value)
        if status != "ok" or number is None or threshold is None:
            return False, False
        return {"gt": number > threshold, "gte": number >= threshold, "lt": number < threshold, "lte": number <= threshold}[kind], True
    d, ref_d = _parse_date(value), _parse_date(ref)
    if d is None or ref_d is None:
        return False, False
    if kind == "after":
        return d > ref_d + timedelta(days=days), True
    return ref_d <= d <= ref_d + timedelta(days=days), True


def _derive_stages(rows: list[dict[str, Any]], header: list[str], specs: Any, missing: list[str]) -> tuple[list[str], list[dict]]:
    """Add a 0/1 column per derived stage. Returns the new column names and the stages whose column is absent."""
    if not isinstance(specs, list) or not specs:
        raise ToolError("derived_stages must be a list of {name, column, rule}")
    names: list[str] = []
    unavailable: list[dict] = []
    for spec in specs:
        if not isinstance(spec, dict) or not _text(spec.get("name")) or not _text(spec.get("column")):
            raise ToolError("each derived stage needs name and column (rule defaults to not_blank)")
        name = _text(spec["name"])
        rule = _text(spec.get("rule")) or "not_blank"
        m = _RULE.match(rule)
        if not m:
            raise ToolError(f"derived stage {name}: rule {rule!r} not understood; use {RULE_HELP}")
        kind, arg = m.group(1), m.group(2)
        try:
            col = _column(spec["column"], header, f"derived stage {name}")
        except ToolError:
            unavailable.append({"stage": name, "column": _text(spec["column"])})
            missing.append(f"stage {name}: column {_text(spec['column'])!r} is not in the file; the stage is unavailable.")
            continue
        threshold = None
        ref_col = None
        days = 0
        if kind in {"gt", "gte", "lt", "lte"}:
            try:
                threshold = float(arg or "")
            except ValueError:
                raise ToolError(f"derived stage {name}: rule {rule!r} needs a number, e.g. gt:0") from None
        elif kind in {"after", "within"}:
            mm = _RULE_REF.match(arg or "")
            if not mm:
                raise ToolError(f"derived stage {name}: rule {rule!r} should look like {kind}:<column>+<N>d")
            ref_col = _column(mm.group(1), header, f"derived stage {name} reference")
            days = int(mm.group(2) or 0)
        elif kind in {"eq", "in"} and not _text(arg):
            raise ToolError(f"derived stage {name}: rule {rule!r} needs a value, e.g. {kind}:paid")
        key = name if name not in header else f"{name} (derived)"
        unreadable = 0
        for row in rows:
            holds, readable = _rule_holds(row.get(col), kind, arg, threshold, row.get(ref_col) if ref_col else None, days)
            if not readable:
                unreadable += 1
            row[key] = 1 if holds else 0
        if unreadable:
            missing.append(f"stage {name}: {_plural(unreadable, 'row')} with unreadable values in {col} counted as not reaching it.")
        header.append(key)
        names.append(key)
    return names, unavailable


def _derive_group(
    rows: list[dict[str, Any]], header: list[str], cohort_by: Any, periods: Any, missing: list[str]
) -> tuple[list[dict[str, Any]], str, str]:
    """A group column derived from a date: cohorts split at boundaries, or labelled periods. Returns rows, column, description."""
    if cohort_by and periods:
        raise ToolError("give cohort_by or periods, not both")
    if cohort_by:
        if not isinstance(cohort_by, dict):
            raise ToolError("cohort_by must be {column, boundaries: [YYYY-MM-DD, ...]}")
        col = _column(cohort_by.get("column") or cohort_by.get("date_column"), header, "cohort_by.column")
        raw = cohort_by.get("boundaries")
        raw = [raw] if isinstance(raw, str) else raw
        boundaries = sorted({_parse_date(b) for b in (raw or []) if _parse_date(b)})
        if not boundaries or len(boundaries) != len(raw or []):
            raise ToolError("cohort_by.boundaries needs one or more ISO dates")
        key = "cohort" if "cohort" not in header else "cohort (derived)"
        without = 0
        kept = []
        for row in rows:
            d = _parse_date(row.get(col))
            if d is None:
                without += 1
                continue
            label = f"before_{boundaries[0].isoformat()}"
            for b in boundaries:
                if d >= b:
                    label = f"from_{b.isoformat()}"
            row[key] = label
            kept.append(row)
        if without:
            missing.append(f"{_plural(without, 'row')} without a readable date in {col} left out of the cohorts.")
        header.append(key)
        return kept, key, f"{col} split at {', '.join(b.isoformat() for b in boundaries)}"
    if not isinstance(periods, list) or not periods:
        raise ToolError("periods must be a list of {label, from, to}")
    spans = []
    for p in periods:
        if not isinstance(p, dict):
            raise ToolError("each period needs label, from and to")
        start = _parse_date(p.get("from") or p.get("date_from"))
        end = _parse_date(p.get("to") or p.get("date_to"))
        if start is None or end is None or start > end:
            raise ToolError(f"period {p.get('label')!r} needs ISO dates from ≤ to")
        label = _text(p.get("label")) or f"{start.isoformat()}..{end.isoformat()}"
        for other in spans:
            if start <= other[2] and other[1] <= end:
                raise ToolError(f"periods {other[0]!r} and {label!r} overlap")
        spans.append((label, start, end))
    col = _column(periods[0].get("column") or periods[0].get("date_column") or None, header, "periods[0].column") if any(
        p.get("column") or p.get("date_column") for p in periods
    ) else None
    if col is None:
        raise ToolError("periods need the date column: give column in the first period or date_column at the top level")
    key = "period" if "period" not in header else "period (derived)"
    without = outside = 0
    kept = []
    for row in rows:
        d = _parse_date(row.get(col))
        if d is None:
            without += 1
            continue
        label = next((s[0] for s in spans if s[1] <= d <= s[2]), None)
        if label is None:
            outside += 1
            continue
        row[key] = label
        kept.append(row)
    if without:
        missing.append(f"{_plural(without, 'row')} without a readable date in {col} left out of the periods.")
    if outside:
        missing.append(f"{_plural(outside, 'row')} outside every period left out.")
    header.append(key)
    return kept, key, f"{col} in periods {', '.join(s[0] for s in spans)}"


# --- the funnel tool -------------------------------------------------------


def funnel_analyze(tools: WorkspaceTools, args: dict) -> dict[str, Any]:
    """Counts, denominators, the largest drop-off and the data gaps of a funnel in a workspace file.

    ``path`` is a CSV or XLSX relative to the workspace (``sheet`` picks an XLSX
    sheet). Wide layout: ``stages`` is the ordered list of columns holding counts,
    summed over the rows in the window. Long layout: ``stage_column`` names the
    stage per row, with ``count_column`` (summed), ``id_column`` (unique ids per
    stage) or neither (rows counted); ``stage_order`` fixes the order. Optional
    ``date_column`` with ``date_from``/``date_to`` (ISO) restricts the rows;
    ``group_by`` adds a funnel per channel, campaign or cohort. See the module
    docstring for the optional comparison, cost, lag, slot and mode additions.
    """
    args = args or {}
    path = str(args.get("path") or "").strip()
    if not path:
        raise ToolError("path is required: a CSV or XLSX file inside the workspace")
    mode = str(args.get("mode") or "funnel").strip().lower()
    if mode not in MODES:
        raise ToolError(f"mode must be one of {', '.join(MODES)}")
    if mode != "funnel":
        header, rows = _load_rows(tools, path, args.get("sheet"))
        handler = {
            "buyer_classes": _mode_buyer_classes,
            "offers": _mode_offers,
            "trend": _mode_trend,
            "recruits": _mode_recruits,
            "categories": _mode_categories,
        }[mode]
        return handler(header, rows, args, path)

    derived_specs = args.get("derived_stages")
    stages_arg = _list(args.get("stages"), "stages")
    stage_column = args.get("stage_column")
    if derived_specs and stages_arg:
        raise ToolError("give stages or derived_stages, not both")
    layout = str(args.get("layout") or ("long" if stage_column and not stages_arg and not derived_specs else "wide")).strip().lower()
    if layout not in {"wide", "long"}:
        raise ToolError("layout must be wide or long")
    if layout == "wide" and not stages_arg and not derived_specs:
        raise ToolError("wide layout needs stages: the ordered list of columns holding the counts")
    if layout == "long" and not stage_column:
        raise ToolError("long layout needs stage_column: the column naming the stage of each row")

    header, rows = _load_rows(tools, path, args.get("sheet"))
    missing: list[str] = []
    rows, filter_info = _apply_filter(rows, header, args.get("filter"), missing)
    unavailable: list[dict] = []
    if derived_specs:
        stages_arg, unavailable = _derive_stages(rows, header, derived_specs, missing)
        if len(stages_arg) < 2:
            raise ToolError(
                "a funnel needs at least two stages; only "
                f"{len(stages_arg)} derived stage{'s' if len(stages_arg) != 1 else ''} had a column in the file"
                + (f" (unavailable: {', '.join(u['stage'] for u in unavailable)})" if unavailable else "")
            )

    date_col = _column(args["date_column"], header, "date_column") if args.get("date_column") else None
    group_arg = args.get("group_by")
    group_cols = _list(group_arg, "group_by") if group_arg not in (None, "") else None
    matrix_cols: list[str] | None = None
    group_col: str | None = None
    group_desc: str | None = None
    if group_cols and len(group_cols) > 2:
        raise ToolError("group_by takes one column, or two columns for a matrix")
    if group_cols and len(group_cols) == 2:
        matrix_cols = [_column(c, header, "group_by") for c in group_cols]
    elif group_cols:
        group_col = _column(group_cols[0], header, "group_by")
    if args.get("cohort_by") or args.get("periods"):
        if group_col or matrix_cols:
            raise ToolError("cohort_by/periods derive the group: do not pass group_by as well")
        periods = args.get("periods")
        if periods and date_col and isinstance(periods, list) and periods and not any(
            isinstance(p, dict) and (p.get("column") or p.get("date_column")) for p in periods
        ):
            periods = [dict(p, column=date_col) if isinstance(p, dict) and i == 0 else p for i, p in enumerate(periods)]
        rows, group_col, group_desc = _derive_group(rows, header, args.get("cohort_by"), periods, missing)
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

    inputs: dict[str, Any] = {
        "path": path,
        "layout": layout,
        "date_column": date_col,
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "group_by": matrix_cols if matrix_cols else (group_desc or group_col),
    }
    if filter_info:
        inputs["filter"] = filter_info
    if layout == "wide":
        stage_cols = [_column(s, header, "stages") for s in stages_arg or []]
        if len(stage_cols) < 2:
            raise ToolError("a funnel needs at least two stages")
        inputs["stages"] = stage_cols
        count_col = id_col = None
        order = None
    else:
        stage_col = _column(stage_column, header, "stage_column")
        count_col = _column(args["count_column"], header, "count_column") if args.get("count_column") else None
        id_col = _column(args["id_column"], header, "id_column") if args.get("id_column") else None
        order = _list(args.get("stage_order"), "stage_order")
        if order is not None and len(order) < 2:
            raise ToolError("stage_order needs at least two stages")
        inputs.update({"stage_column": stage_col, "count_column": count_col, "id_column": id_col, "stage_order": order})

    kept, window = _window_rows(rows, date_col, date_from, date_to)
    if window["rows_without_date"]:
        n = window["rows_without_date"]
        missing.append(f"{n} row{'s' if n != 1 else ''} without a readable date in {date_col} left out of the window.")
    if not rows:
        missing.append("The file has a header but no data rows.")
    elif not kept:
        if date_from or date_to:
            missing.append(
                f"No rows between {inputs['date_from'] or 'the start'} and {inputs['date_to'] or 'the end'}: the window is empty."
            )
        else:
            missing.append("No rows with a readable date: the window is empty.")

    if layout == "wide":
        names = list(inputs["stages"])
        counts = _wide_counts(kept, names, missing)
    else:
        names, counts = _long_counts(kept, inputs["stage_column"], count_col, id_col, order, missing)
        if len(names) < 2:
            if kept:
                raise ToolError(
                    f"a funnel needs at least two stages; {inputs['stage_column']} holds {len(names)}: {', '.join(names) or '(none)'}"
                )
            names = order or names or ["stage 1", "stage 2"]
            while len(names) < 2:
                names = names + [f"stage {len(names) + 1}"]
            counts = [0.0] * len(names)
    if kept and all(c == 0 for c in counts):
        missing.append("Every stage count is zero in the window: the stage columns may be the wrong ones.")

    funnel = _funnel(names, counts)
    out: dict[str, Any] = {"inputs": inputs, "window": window}
    out.update(funnel)
    if unavailable:
        out["unavailable_stages"] = unavailable
    warnings: list[str] = out["warnings"]

    buckets: dict[str, list[dict[str, Any]]] | None = None
    pair_of: dict[str, tuple[str, str]] = {}
    if matrix_cols is not None:
        buckets = {}
        blank_pairs = 0
        for row in kept:
            a, b = _text(row.get(matrix_cols[0])), _text(row.get(matrix_cols[1]))
            if not a or not b:
                blank_pairs += 1
            a, b = a or BLANK_GROUP, b or BLANK_GROUP
            key = f"{a} → {b}"
            pair_of[key] = (a, b)
            buckets.setdefault(key, []).append(row)
        if blank_pairs:
            missing.append(f"{_plural(blank_pairs, 'row')} with a blank {matrix_cols[0]} or {matrix_cols[1]} grouped as {BLANK_GROUP}.")
    elif group_col is not None:
        buckets = _bucket_rows(kept, group_col, missing)

    if buckets is not None:
        out["groups"], out["worst_group"] = _groups(buckets, layout, inputs, count_col, id_col, order, names, missing)
        out["worst_group_threshold"] = (
            f"worst_group is chosen by overall conversion among groups with at least {MIN_GROUP_FIRST_STAGE} at the first stage."
        )
        if matrix_cols is not None:
            out["matrix"] = _matrix(out["groups"], pair_of, matrix_cols, names, args.get("target_segment"), missing)

    # --- optional additions ---
    compare_stage = names[-1]
    if args.get("compare_stage"):
        compare_stage = _stage_name(args["compare_stage"], names, "compare_stage")
    compare_idx = names.index(compare_stage)
    extra_summary: list[str] = []

    lag_block = None
    if args.get("lag_from") or args.get("lag_to"):
        if not (args.get("lag_from") and args.get("lag_to")):
            raise ToolError("lag needs both lag_from and lag_to (date columns)")
        lag_from = _column(args["lag_from"], header, "lag_from")
        lag_to = _column(args["lag_to"], header, "lag_to")
        maturity = args.get("maturity_days")
        maturity_days = _int_arg(args, "maturity_days", 0, minimum=0) if maturity not in (None, "") else None
        as_of = _date_arg(args, "as_of")
        lag_block = _lag(kept, lag_from, lag_to, buckets, maturity_days, as_of, missing, warnings)
        out["lag"] = lag_block
        extra_summary.append(_lag_sentence(lag_block))

    immature = set(lag_block["maturity"]["groups_excluded_as_immature"]) if lag_block and lag_block.get("maturity") else set()
    group_windows = None
    if buckets is not None and date_col is not None:
        group_windows = {}
        for key, group_rows in buckets.items():
            dates = [d for d in (_parse_date(r.get(date_col)) for r in group_rows) if d]
            group_windows[key] = {"from": min(dates).isoformat() if dates else None, "to": max(dates).isoformat() if dates else None}

    if buckets is not None and out.get("groups"):
        min_n = _int_arg(args, "min_n", MIN_COMPARE_N, minimum=1)
        compare_arg = _list(args.get("compare_groups"), "compare_groups")
        comparison = _compare(out["groups"], names, compare_arg, compare_stage, compare_idx, min_n, group_windows, immature, warnings, missing)
        if comparison is not None:
            out["comparison"] = comparison
            extra_summary.append(_comparison_sentence(comparison))
        if args.get("cost_per_group") or args.get("cost_column") or args.get("attention_column"):
            cost_col = _optional_column(args, "cost_column", header)
            attention_col = _optional_column(args, "attention_column", header)
            out["cost"] = _costs(
                out["groups"], buckets, compare_stage, compare_idx, args.get("cost_per_group"), cost_col, attention_col,
                _text(args.get("currency")) or "EUR", missing, warnings,
            )
            extra_summary.append(_cost_sentence(out["cost"]))
        if args.get("metrics") or args.get("share_of"):
            out["metrics"] = _metrics(buckets, header, args.get("metrics"), args.get("share_of"), comparison, missing, warnings)
    elif any(args.get(k) for k in ("compare_groups", "cost_per_group", "cost_column", "metrics", "share_of", "attention_column")):
        missing.append("compare_groups, cost_per_group, cost_column, metrics and share_of need group_by (or cohort_by/periods) to form groups.")

    if args.get("bucket_by"):
        cover_share = _float_arg(args, "cover_share", 0.9)
        if not 0 < cover_share <= 1:
            raise ToolError("cover_share must be between 0 and 1")
        out["slots"] = _slots(kept, header, args["bucket_by"], layout, names, inputs, count_col, id_col, cover_share, missing, warnings)
        extra_summary.append(_slots_sentence(out["slots"]))

    if args.get("paid_at_stage"):
        paid_stage = _stage_name(args["paid_at_stage"], names, "paid_at_stage")
        returns_col = _optional_column(args, "returns_column", header)
        out["incentive"] = _incentive(names, counts, paid_stage, funnel["largest_dropoff"], out.get("groups"), buckets, returns_col, kept, missing)
        extra_summary.append(_incentive_sentence(out["incentive"]))

    out["missing"] = missing
    rows_desc = f"Across {window['rows_in_window']} row{'s' if window['rows_in_window'] != 1 else ''}"
    out["summary"] = " ".join([_summary(window, names, funnel, rows_desc)] + [s for s in extra_summary if s])
    return out


def _stage_name(value: Any, names: list[str], what: str) -> str:
    wanted = _text(value)
    for name in names:
        if name == wanted or name.lower() == wanted.lower():
            return name
    raise ToolError(f"{what} {wanted!r} is not a stage; stages: {', '.join(names)}")


def _bucket_rows(rows: list[dict[str, Any]], group_col: str, missing: list[str]) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = row.get(group_col)
        key = BLANK_GROUP if key is None or str(key).strip() == "" else str(key).strip()
        buckets.setdefault(key, []).append(row)
    if BLANK_GROUP in buckets:
        n = len(buckets[BLANK_GROUP])
        missing.append(f"{n} row{'s' if n != 1 else ''} with a blank {group_col} grouped as {BLANK_GROUP}.")
    return buckets


def _groups(
    buckets: dict[str, list[dict[str, Any]]],
    layout: str,
    inputs: dict[str, Any],
    count_col: str | None,
    id_col: str | None,
    order: list[str] | None,
    names: list[str],
    missing: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    groups = []
    for key, group_rows in buckets.items():
        local_missing: list[str] = []
        if layout == "wide":
            g_names, g_counts = names, _wide_counts(group_rows, names, local_missing)
        else:
            g_names, g_counts = _long_counts(group_rows, inputs["stage_column"], count_col, id_col, names, local_missing)
        if len(g_counts) < 2:
            continue
        funnel = _funnel(g_names, g_counts)
        groups.append(
            {
                "group": key,
                "rows": len(group_rows),
                "stages": funnel["stages"],
                "largest_dropoff": funnel["largest_dropoff"],
                "overall_conversion_pct": funnel["overall_conversion_pct"],
                "warnings": funnel["warnings"],
            }
        )
    groups.sort(key=lambda g: g["stages"][0]["count"], reverse=True)
    ranked = [
        g for g in groups if g["stages"][0]["count"] >= MIN_GROUP_FIRST_STAGE and g["overall_conversion_pct"] is not None
    ]
    worst = None
    if ranked:
        pick = min(ranked, key=lambda g: g["overall_conversion_pct"])
        worst = {
            "group": pick["group"],
            "overall_conversion_pct": pick["overall_conversion_pct"],
            "first_stage_count": pick["stages"][0]["count"],
            "largest_dropoff": pick["largest_dropoff"],
        }
    elif groups:
        missing.append(f"No group reaches {MIN_GROUP_FIRST_STAGE} at the first stage, so no worst_group is named.")
    return groups, worst


def _matrix(
    groups: list[dict[str, Any]], pair_of: dict[str, tuple[str, str]], cols: list[str], names: list[str], target: Any, missing: list[str]
) -> dict[str, Any]:
    """Teller × listener matrix from a two-column group_by: first and last stage per cell, conversion, the weakest cell."""
    cells = []
    row_values: list[str] = []
    col_values: list[str] = []
    total_first = 0.0
    target_first = 0.0
    target_text = _text(target).lower()
    for g in groups:
        a, b = pair_of[g["group"]]
        first, last = g["stages"][0]["count"], g["stages"][-1]["count"]
        total_first += first
        if target_text and b.lower() == target_text:
            target_first += first
        if a not in row_values:
            row_values.append(a)
        if b not in col_values:
            col_values.append(b)
        cells.append(
            {
                cols[0]: a,
                cols[1]: b,
                "first_stage_count": first,
                "last_stage_count": last,
                "conversion_pct": g["overall_conversion_pct"],
                "small": first < MIN_GROUP_FIRST_STAGE,
            }
        )
    rankable = [c for c in cells if not c["small"] and c["conversion_pct"] is not None]
    weakest = min(rankable, key=lambda c: c["conversion_pct"]) if rankable else None
    largest = max(cells, key=lambda c: c["first_stage_count"]) if cells else None
    out: dict[str, Any] = {
        "rows": cols[0],
        "cols": cols[1],
        "row_values": row_values,
        "col_values": col_values,
        "stages": [names[0], names[-1]],
        "cells": cells,
        "largest_cell": largest,
        "weakest_cell": weakest,
    }
    if target_text:
        out["target_segment"] = _text(target)
        out["share_to_target_pct"] = _pct(target_first, total_first)
        if target_first == 0:
            missing.append(f"target_segment {_text(target)!r} never appears in {cols[1]}.")
    if not rankable and cells:
        missing.append(f"No matrix cell reaches {MIN_GROUP_FIRST_STAGE} at the first stage, so no weakest_cell is named.")
    return out


# --- group comparison, costs, metrics --------------------------------------


def _compare(
    groups: list[dict[str, Any]],
    names: list[str],
    compare_arg: list[str] | None,
    stage: str,
    idx: int,
    min_n: int,
    group_windows: dict[str, dict] | None,
    immature: set[str],
    warnings: list[str],
    missing: list[str],
) -> dict[str, Any] | None:
    order = [g["group"] for g in groups]
    by = {g["group"]: g for g in groups}
    folded = {g.lower(): g for g in order}
    if compare_arg:
        wanted = []
        unknown = []
        for value in compare_arg:
            match = by.get(value) and value or folded.get(value.lower())
            (wanted if match else unknown).append(match or value)
        if unknown:
            raise ToolError(f"compare_groups names groups not in the data: {', '.join(unknown)}; groups present: {', '.join(order)}")
        if len(wanted) < 2:
            raise ToolError("compare_groups needs at least two groups")
    elif len(order) <= MAX_COMPARE_GROUPS:
        wanted = order
    else:
        missing.append(f"{len(order)} groups: pass compare_groups (2–{MAX_COMPARE_GROUPS} of them) to get the significance comparison.")
        return None
    if len(wanted) < 2:
        return None
    base = by[wanted[0]]
    n0, x0 = float(base["stages"][0]["count"]), float(base["stages"][idx]["count"])
    rows = []
    for name in wanted:
        g = by[name]
        n, x = float(g["stages"][0]["count"]), float(g["stages"][idx]["count"])
        entry: dict[str, Any] = {"group": name, "n": _num(n), "converted": _num(x), "rate_pct": _pct(x, n)}
        if name == wanted[0]:
            entry.update({"diff_pp": None, "ci95_pp": None, "z": None, "p_value": None, "verdict": "baseline"})
        else:
            stats = _two_proportion(x, n, x0, n0)
            entry.update(stats or {"diff_pp": None, "ci95_pp": None, "z": None, "p_value": None})
            # how many more (or fewer) converted this group got than it would have at the baseline rate
            entry["incremental_vs_baseline"] = None if stats is None else _num(x - n * x0 / n0)
            reason = None
            if stats is None:
                reason = "a group has 0 at the first stage or more converted than started"
            elif name in immature or wanted[0] in immature:
                reason = "group younger than maturity_days"
            elif n < min_n or n0 < min_n:
                reason = f"fewer than {min_n} at the first stage"
            if reason:
                entry["verdict"] = "insufficient_data"
                entry["reason"] = reason
            elif stats["p_value"] is not None and stats["p_value"] < 0.05:
                entry["verdict"] = "worse" if stats["diff_pp"] < 0 else "better"
            else:
                entry["verdict"] = "no_difference"
        rows.append(entry)
    block: dict[str, Any] = {
        "stage": stage,
        "baseline_group": wanted[0],
        "min_n": min_n,
        "groups": rows,
        "method": TWO_PROPORTION_METHOD,
    }
    if group_windows is not None:
        windows = {name: group_windows.get(name, {"from": None, "to": None}) for name in wanted}
        block["windows"] = windows
        froms = [date.fromisoformat(w["from"]) for w in windows.values() if w["from"]]
        tos = [date.fromisoformat(w["to"]) for w in windows.values() if w["to"]]
        concurrent = None
        if froms and tos:
            concurrent = (max(froms) - min(froms)).days <= CONCURRENT_DAYS and (max(tos) - min(tos)).days <= CONCURRENT_DAYS
            if not concurrent:
                spans = "; ".join(f"{k}: {v['from']}..{v['to']}" for k, v in windows.items())
                warnings.append(f"groups not concurrent: the date windows differ by more than {CONCURRENT_DAYS} days ({spans}).")
        block["concurrent"] = concurrent
    else:
        block["concurrent"] = None
        block["concurrency_note"] = "no date_column: whether the groups ran over the same dates was not checked."
    return block


def _comparison_sentence(block: dict[str, Any]) -> str:
    rows = block["groups"]
    base = rows[0]
    parts = []
    for r in rows[1:]:
        if r["rate_pct"] is None:
            continue
        piece = f"{r['group']} {r['rate_pct']:g} %"
        if r["diff_pp"] is not None:
            piece += f" ({r['diff_pp']:+g} pp"
            if r["verdict"] == "insufficient_data":
                piece += ", insufficient data)"
            elif r["p_value"] is None:
                piece += f", {r['verdict'].replace('_', ' ')})"
            else:
                piece += f", p = {r['p_value']:g}, {r['verdict'].replace('_', ' ')})"
        parts.append(piece)
    if base["rate_pct"] is None:
        return ""
    return f"At {block['stage']}: {base['group']} {base['rate_pct']:g} % vs " + ", ".join(parts) + "." if parts else ""


_COST_KEYS = {"media", "fixed", "fulfilment_per_unit", "fee_per_unit"}
COST_METHOD = (
    "net cost = media + fixed + units × fulfilment_per_unit − units × fee_per_unit, units = first-stage count; "
    "cost per converted = net cost / count at the compared stage; pooled cost = total net cost / total converted "
    "(never the mean of group ratios); contribution index = converted share / spend share."
)


def _cost_spec(value: Any, group: str) -> dict[str, float]:
    if isinstance(value, dict):
        unknown = set(value) - _COST_KEYS
        if unknown:
            raise ToolError(f"cost_per_group[{group!r}] has unknown keys {', '.join(sorted(unknown))}; use {', '.join(sorted(_COST_KEYS))}")
        spec = {}
        for key in _COST_KEYS:
            if value.get(key) in (None, ""):
                continue
            number, status = _parse_number(value[key])
            if status != "ok" or number is None:
                raise ToolError(f"cost_per_group[{group!r}].{key} must be a number")
            spec[key] = number
        return spec
    number, status = _parse_number(value)
    if status != "ok" or number is None:
        raise ToolError(f"cost_per_group[{group!r}] must be a number or {{media, fixed, fulfilment_per_unit, fee_per_unit}}")
    return {"media": number}


def _costs(
    groups: list[dict[str, Any]],
    buckets: dict[str, list[dict[str, Any]]],
    stage: str,
    idx: int,
    cost_per_group: Any,
    cost_col: str | None,
    attention_col: str | None,
    currency: str,
    missing: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    if cost_per_group is not None and not isinstance(cost_per_group, dict):
        raise ToolError("cost_per_group must be an object {group: amount or {media, fixed, fulfilment_per_unit, fee_per_unit}}")
    specs: dict[str, dict[str, float]] = {}
    folded = {g["group"].lower(): g["group"] for g in groups}
    for key, value in (cost_per_group or {}).items():
        group = folded.get(_text(key).lower())
        if group is None:
            missing.append(f"cost_per_group names a group not in the data: {key}; groups: {', '.join(folded.values())}.")
            continue
        specs[group] = _cost_spec(value, group)
    entries = []
    for g in groups:
        name = g["group"]
        units, converted = float(g["stages"][0]["count"]), float(g["stages"][idx]["count"])
        spec = dict(specs.get(name, {}))
        if cost_col:
            spec["media"] = spec.get("media", 0.0) + _sum_column(buckets[name], cost_col, missing, f"{cost_col} ({name})")
        entry: dict[str, Any] = {"group": name, "units": _num(units), "converted": _num(converted)}
        if attention_col:
            entry["attention"] = _num(_sum_column(buckets[name], attention_col, missing, f"{attention_col} ({name})"))
        if not spec:
            entry.update({"net_cost": None, "cost_per_converted": None, "media_per_converted": None})
            missing.append(f"no cost for group {name}: cost per converted not computed.")
            entries.append(entry)
            continue
        media = spec.get("media", 0.0)
        fixed = spec.get("fixed", 0.0)
        fulfilment = units * spec.get("fulfilment_per_unit", 0.0)
        fee = units * spec.get("fee_per_unit", 0.0)
        net = media + fixed + fulfilment - fee
        entry.update(
            {
                "media": _num(media),
                "fixed": _num(fixed),
                "fulfilment": _num(fulfilment),
                "fee_revenue": _num(fee),
                "net_cost": _num(net),
                "cost_per_converted": _ratio(net, converted),
                "media_per_converted": _ratio(media, converted),
            }
        )
        if converted == 0:
            missing.append(f"group {name} has 0 at {stage}: cost per converted is undefined.")
        if net < 0:
            entry.setdefault("flags", []).append("fee_covers_costs")
            warnings.append(f"group {name}: the fee revenue exceeds media, fixed and fulfilment cost (net {_fmt(_num(net))}).")
        entries.append(entry)
    priced = [e for e in entries if e["net_cost"] is not None]
    total_net = sum(float(e["net_cost"]) for e in priced)
    total_conv = sum(float(e["converted"]) for e in priced)
    for e in priced:
        e["spend_share_pct"] = _pct(float(e["net_cost"]), total_net) if total_net > 0 else None
        e["converted_share_pct"] = _pct(float(e["converted"]), total_conv)
        if e["spend_share_pct"] and e["converted_share_pct"] is not None:
            e["contribution_index"] = round(e["converted_share_pct"] / e["spend_share_pct"], 2)
        else:
            e["contribution_index"] = None
    if attention_col:
        total_att = sum(float(e.get("attention") or 0) for e in entries)
        total_conv_all = sum(float(e["converted"]) for e in entries)
        for e in entries:
            e["attention_share_pct"] = _pct(float(e.get("attention") or 0), total_att)
            conv_share = _pct(float(e["converted"]), total_conv_all)
            if e["attention_share_pct"] is not None and conv_share is not None:
                if e["attention_share_pct"] - conv_share > 20:
                    e.setdefault("flags", []).append("publicity_without_sales")
                elif conv_share - e["attention_share_pct"] > 20:
                    e.setdefault("flags", []).append("quiet_contributor")
    ratios = [e["cost_per_converted"] for e in priced if e["cost_per_converted"] is not None]
    pooled = _ratio(total_net, total_conv)
    naive = round(sum(ratios) / len(ratios), 2) if ratios else None
    if pooled and naive is not None and abs(naive - pooled) / abs(pooled) > 0.10:
        warnings.append(
            f"the mean of group costs per converted ({_fmt(naive)}) differs from the pooled cost ({_fmt(pooled)}) by more than 10 %: "
            "quote the pooled figure for the whole campaign."
        )
    ranked = [e for e in priced if e["cost_per_converted"] is not None and float(e["converted"]) >= MIN_GROUP_FIRST_STAGE]
    cheapest = min(ranked, key=lambda e: e["cost_per_converted"]) if ranked else None
    dearest = max(ranked, key=lambda e: e["cost_per_converted"]) if ranked else None
    return {
        "stage": stage,
        "currency": currency,
        "groups": entries,
        "total_net_cost": _num(total_net) if priced else None,
        "total_converted": _num(total_conv) if priced else None,
        "pooled_cost_per_converted": pooled,
        "naive_mean_of_group_costs": naive,
        "cheapest_group": cheapest["group"] if cheapest else None,
        "dearest_group": dearest["group"] if dearest else None,
        "rank_threshold": f"cheapest/dearest ranked among groups with at least {MIN_GROUP_FIRST_STAGE} converted.",
        "method": COST_METHOD,
    }


def _cost_sentence(block: dict[str, Any]) -> str:
    parts = [
        f"{e['group']} {_fmt(e['cost_per_converted'])}" for e in block["groups"] if e.get("cost_per_converted") is not None
    ]
    if not parts:
        return ""
    line = f"Cost per {block['stage']} ({block['currency']}): " + ", ".join(parts)
    if block["pooled_cost_per_converted"] is not None:
        line += f"; pooled {_fmt(block['pooled_cost_per_converted'])}"
    return line + "."


def _metrics(
    buckets: dict[str, list[dict[str, Any]]],
    header: list[str],
    metric_cols: Any,
    share_of: Any,
    comparison: dict[str, Any] | None,
    missing: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    """Per-group mean and median of numeric columns, a share-of-wallet ratio and flags for unequal groups."""
    cols = [_column(c, header, "metrics") for c in (_list(metric_cols, "metrics") or [])]
    share = None
    if share_of:
        if not isinstance(share_of, dict) or not share_of.get("numerator") or not share_of.get("denominator"):
            raise ToolError("share_of must be {numerator: column, denominator: column}")
        share = (_column(share_of["numerator"], header, "share_of.numerator"), _column(share_of["denominator"], header, "share_of.denominator"))
        for c in share:
            if c not in cols:
                cols.append(c)
    per_group: dict[str, dict[str, Any]] = {}
    raw: dict[str, dict[str, list[float]]] = {}
    for name, rows in buckets.items():
        entry: dict[str, Any] = {}
        raw[name] = {}
        for col in cols:
            values: list[float] = []
            raw[name][col] = values
            blank = bad = 0
            for row in rows:
                number, status = _parse_number(row.get(col))
                if status == "ok" and number is not None:
                    values.append(number)
                elif status == "blank":
                    blank += 1
                else:
                    bad += 1
            entry[col] = {
                "n": len(values),
                "mean": round(statistics.fmean(values), 2) if values else None,
                "median": round(statistics.median(values), 2) if values else None,
                "sum": _num(sum(values)) if values else None,
            }
            if blank:
                missing.append(f"{col} ({name}): {_plural(blank, 'blank cell')} left out of the mean.")
            if bad:
                missing.append(f"{col} ({name}): {_plural(bad, 'cell')} that were not numbers left out.")
        if share:
            num, den = entry[share[0]]["sum"], entry[share[1]]["sum"]
            entry["share_of_wallet_pct"] = _pct(float(num or 0), float(den or 0)) if den else None
        per_group[name] = entry
    unequal = []
    for col in cols:
        for name in per_group:
            own = per_group[name][col]["median"]
            others = [v for other, cells in raw.items() if other != name for v in cells[col]]
            if own is None or not others:
                continue
            others_median = statistics.median(others)
            if others_median == 0 and own != 0 or others_median != 0 and abs(own - others_median) / abs(others_median) > 0.5:
                unequal.append({"group": name, "column": col, "median": own, "others_median": round(others_median, 2)})
    out: dict[str, Any] = {
        "columns": cols,
        "groups": per_group,
        "unequal": unequal,
        "comparable": not unequal,
        "method": "mean and median per group over readable numeric cells; a group is unequal when its median differs from the median of all other groups' cells by more than 50 %.",
    }
    if share:
        out["share_of"] = {"numerator": share[0], "denominator": share[1]}
        names = [r["group"] for r in comparison["groups"]] if comparison else list(per_group)
        if len(names) >= 2:
            a, b = names[0], names[1]
            num_a, num_b = per_group[a][share[0]]["mean"], per_group[b][share[0]]["mean"]
            den_a, den_b = per_group[a][share[1]]["mean"], per_group[b][share[1]]["mean"]
            if None in (num_a, num_b, den_a, den_b):
                verdict = "not_assessable"
            else:
                exposed, control = (a, b) if num_a >= num_b else (b, a)
                den_e, den_c = per_group[exposed][share[1]]["mean"], per_group[control][share[1]]["mean"]
                verdict = "likely" if den_c > 0 and den_e > 1.2 * den_c else "unlikely"
                out["share_of"].update({"exposed_group": exposed, "control_group": control})
                if verdict == "likely":
                    warnings.append(
                        f"selection bias likely: {exposed} also spends {_pct(den_e - den_c, den_c):g} % more on {share[1]} than {control}; "
                        f"the gap in {share[0]} cannot be attributed to the exposure."
                    )
            out["selection_bias"] = verdict
            out["selection_bias_rule"] = "likely when the exposed group's mean denominator exceeds the control group's by more than 20 %."
        else:
            out["selection_bias"] = "not_assessable"
    return out


# --- lag ---------------------------------------------------------------------


_LAG_BUCKETS = (("0 days", 0, 0), ("1–3 days", 1, 3), ("4–7 days", 4, 7), ("8+ days", 8, None))


def _lag_table(rows: list[dict[str, Any]], from_col: str, to_col: str) -> tuple[dict[str, Any], list[int], int, int]:
    lags: list[int] = []
    without = negative = 0
    for row in rows:
        a, b = _parse_date(row.get(from_col)), _parse_date(row.get(to_col))
        if a is None or b is None:
            without += 1
            continue
        d = (b - a).days
        if d < 0:
            negative += 1
            continue
        lags.append(d)
    buckets = []
    for label, lo, hi in _LAG_BUCKETS:
        n = sum(1 for d in lags if d >= lo and (hi is None or d <= hi))
        buckets.append({"label": label, "count": n, "share_pct": _pct(n, len(lags))})
    table = {
        "rows_with_both_dates": len(lags),
        "buckets": buckets,
        "median_days": statistics.median(lags) if lags else None,
        "after_day_0_share_pct": _pct(sum(1 for d in lags if d > 0), len(lags)),
    }
    return table, lags, without, negative


def _lag(
    rows: list[dict[str, Any]],
    from_col: str,
    to_col: str,
    buckets: dict[str, list[dict[str, Any]]] | None,
    maturity_days: int | None,
    as_of: date | None,
    missing: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    table, lags, without, negative = _lag_table(rows, from_col, to_col)
    if without:
        missing.append(f"{_plural(without, 'row')} without both {from_col} and {to_col} left out of the lag table.")
    if negative:
        missing.append(f"{_plural(negative, 'row')} with {to_col} before {from_col} left out of the lag table.")
    out: dict[str, Any] = {"from": from_col, "to": to_col, **table}
    if buckets is not None:
        out["groups"] = {}
        for name, group_rows in buckets.items():
            g_table, _, _, _ = _lag_table(group_rows, from_col, to_col)
            out["groups"][name] = g_table
    if maturity_days is not None or buckets is not None:
        to_dates = [d for d in (_parse_date(r.get(to_col)) for r in rows) if d]
        reference = as_of or (max(to_dates) if to_dates else None)
        if reference is None:
            missing.append("no readable dates: maturity cannot be assessed.")
        else:
            ages: dict[str, int] = {}
            starts: dict[str, date] = {}
            for name, group_rows in (buckets or {"(all)": rows}).items():
                froms = [d for d in (_parse_date(r.get(from_col)) for r in group_rows) if d]
                if froms:
                    starts[name] = min(froms)
                    ages[name] = (reference - min(froms)).days
            used = maturity_days if maturity_days is not None else (min(ages.values()) if ages else None)
            maturity: dict[str, Any] = {
                "as_of": reference.isoformat(),
                "maturity_days": used,
                "maturity_source": "given" if maturity_days is not None else "youngest group's age",
                "groups": {},
                "groups_excluded_as_immature": [],
            }
            for name, group_rows in (buckets or {"(all)": rows}).items():
                if name not in ages or used is None:
                    continue
                _, g_lags, _, _ = _lag_table(group_rows, from_col, to_col)
                at_maturity = sum(1 for d in g_lags if d <= used)
                immature = ages[name] < used
                maturity["groups"][name] = {
                    "start": starts[name].isoformat(),
                    "age_days": ages[name],
                    "raw_count": len(g_lags),
                    "count_at_maturity": at_maturity,
                    "pct_of_raw_at_maturity": _pct(at_maturity, len(g_lags)),
                    "immature": immature,
                }
                if immature:
                    maturity["groups_excluded_as_immature"].append(name)
            if maturity["groups_excluded_as_immature"]:
                warnings.append(
                    f"groups younger than {used} days are not compared: {', '.join(maturity['groups_excluded_as_immature'])} "
                    f"(as of {reference.isoformat()})."
                )
            out["maturity"] = maturity
    return out


def _lag_sentence(block: dict[str, Any]) -> str:
    n = block["rows_with_both_dates"]
    if not n:
        return ""
    later = block["after_day_0_share_pct"]
    return f"Of {_fmt(n)} rows with both dates, {later:g} % had {block['to']} one or more days after {block['from']} (median {block['median_days']:g} days)."


# --- time slots ---------------------------------------------------------------


def _slots(
    rows: list[dict[str, Any]],
    header: list[str],
    spec: Any,
    layout: str,
    names: list[str],
    inputs: dict[str, Any],
    count_col: str | None,
    id_col: str | None,
    cover_share: float,
    missing: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise ToolError("bucket_by must be {column, unit: hour|weekday|hour_weekday}")
    col = _column(spec.get("column"), header, "bucket_by.column")
    unit = _text(spec.get("unit")).lower() or "hour"
    if unit not in {"hour", "weekday", "hour_weekday"}:
        raise ToolError("bucket_by.unit must be hour, weekday or hour_weekday")
    buckets: dict[tuple, list[dict[str, Any]]] = {}
    without = 0
    for row in rows:
        dt, has_time = _parse_datetime(row.get(col))
        if dt is None or (unit != "weekday" and not has_time):
            without += 1
            continue
        if unit == "hour":
            key: tuple = (dt.hour,)
        elif unit == "weekday":
            key = (dt.weekday(),)
        else:
            key = (dt.weekday(), dt.hour)
        buckets.setdefault(key, []).append(row)
    if without:
        what = "time" if unit != "weekday" else "date"
        missing.append(f"{_plural(without, 'row')} without a readable {what} in {col} left out of the slots.")
        if rows and without / len(rows) > 0.2:
            warnings.append(f"more than 20 % of rows have no readable {what} in {col}: the slot table is partial.")

    def label(key: tuple) -> str:
        if unit == "hour":
            return f"{key[0]:02d}:00"
        if unit == "weekday":
            return _WEEKDAYS[key[0]]
        return f"{_WEEKDAYS[key[0]]} {key[1]:02d}:00"

    slots = []
    for key in sorted(buckets):
        srows = buckets[key]
        local: list[str] = []
        if layout == "wide":
            counts = _wide_counts(srows, names, local)
        else:
            _, counts = _long_counts(srows, inputs["stage_column"], count_col, id_col, names, local)
        first, last = counts[0], counts[-1]
        slots.append(
            {
                "slot": label(key),
                "rows": len(srows),
                "stage_counts": {n: _num(c) for n, c in zip(names, counts)},
                "fulfilled": _num(last),
                "fulfilled_pct_of_first": _pct(last, first),
                "share_of_fulfilled_pct": None,
                "low_volume": first < MIN_GROUP_FIRST_STAGE,
            }
        )
    total = sum(float(s["fulfilled"]) for s in slots)
    for s in slots:
        s["share_of_fulfilled_pct"] = _pct(float(s["fulfilled"]), total)
    best = []
    cumulative = 0.0
    for s in sorted(slots, key=lambda s: float(s["fulfilled"]), reverse=True):
        if total <= 0 or cumulative >= cover_share * total:
            break
        cumulative += float(s["fulfilled"])
        best.append({"slot": s["slot"], "fulfilled": s["fulfilled"], "cumulative_share_pct": _pct(cumulative, total)})
    if total <= 0 and slots:
        missing.append(f"no {names[-1]} in any slot: best_slots cannot be chosen.")
    return {
        "column": col,
        "unit": unit,
        "fulfilled_stage": names[-1],
        "rows_without_time": without,
        "slots": slots,
        "cover_share": cover_share,
        "best_slots": best,
        "slots_for_cover_share": len(best),
        "low_volume_threshold": MIN_GROUP_FIRST_STAGE,
        "note": "times are taken as written in the file; no time-zone conversion is applied.",
    }


def _slots_sentence(block: dict[str, Any]) -> str:
    best = block["best_slots"]
    if not best:
        return ""
    names = ", ".join(b["slot"] for b in best)
    return (
        f"{len(best)} of {len(block['slots'])} {block['unit'].replace('_', ' ')} slots ({names}) hold "
        f"{best[-1]['cumulative_share_pct']:g} % of {block['fulfilled_stage']}."
    )


# --- incentive (paid stage vs final stage) -----------------------------------


def _incentive(
    names: list[str],
    counts: list[float],
    paid_stage: str,
    largest: dict[str, Any] | None,
    groups: list[dict[str, Any]] | None,
    buckets: dict[str, list[dict[str, Any]]] | None,
    returns_col: str | None,
    rows: list[dict[str, Any]],
    missing: list[str],
) -> dict[str, Any]:
    idx = names.index(paid_stage)
    paid, final = counts[idx], counts[-1]
    stranded = paid - final
    out: dict[str, Any] = {
        "paid_stage": paid_stage,
        "final_stage": names[-1],
        "paid_units": _num(paid),
        "final_units": _num(final),
        "stranded_units": _num(stranded),
        "stranded_share_pct": _pct(stranded, paid),
        "paid_to_final_ratio": _ratio(paid, final),
        "incentive_gap": bool(largest and idx <= names.index(largest["from_stage"])),
        "method": "stranded = units at the paid stage minus units at the final stage; incentive_gap when the paid stage comes at or before the step that loses the most.",
    }
    if idx == len(names) - 1:
        out["incentive_gap"] = False
    if returns_col:
        returns = _sum_column(rows, returns_col, missing)
        out["returns_units"] = _num(returns)
        out["returns_share_of_paid_pct"] = _pct(returns, paid)
    if final == 0:
        missing.append(f"{names[-1]} is 0: the paid-to-final ratio is undefined.")
    if groups is not None and buckets is not None:
        per_group = []
        for g in groups:
            g_paid, g_final = float(g["stages"][idx]["count"]), float(g["stages"][-1]["count"])
            entry = {
                "group": g["group"],
                "paid_units": _num(g_paid),
                "final_units": _num(g_final),
                "stranded_units": _num(g_paid - g_final),
                "stranded_share_pct": _pct(g_paid - g_final, g_paid),
                "paid_to_final_ratio": _ratio(g_paid, g_final),
            }
            if returns_col:
                entry["returns_units"] = _num(_sum_column(buckets[g["group"]], returns_col, []))
            if g_final == 0:
                missing.append(f"group {g['group']} has 0 at {names[-1]}: its ratio is undefined.")
            per_group.append(entry)
        out["per_group"] = per_group
        rankable = [e for e in per_group if e["stranded_share_pct"] is not None and float(e["paid_units"]) >= MIN_GROUP_FIRST_STAGE]
        out["worst_group"] = max(rankable, key=lambda e: e["stranded_share_pct"])["group"] if rankable else None
    return out


def _incentive_sentence(block: dict[str, Any]) -> str:
    share = block["stranded_share_pct"]
    if share is None:
        return ""
    line = (
        f"Paid at {block['paid_stage']}: {_fmt(block['stranded_units'])} of {_fmt(block['paid_units'])} units ({share:g} %) "
        f"never reached {block['final_stage']}"
    )
    if block.get("worst_group"):
        line += f"; worst group {block['worst_group']}"
    return line + ("; the plan pays before the step that loses the most." if block["incentive_gap"] else ".")


# --- modes -------------------------------------------------------------------


def _period_key(d: date, unit: str) -> str:
    if unit == "week":
        year, week, _ = d.isocalendar()
        return f"{year}-W{week:02d}"
    if unit == "quarter":
        return f"{d.year}-Q{(d.month - 1) // 3 + 1}"
    return f"{d.year}-{d.month:02d}"


def _period_unit(args: dict) -> str:
    unit = _text(args.get("period")).lower() or "month"
    if unit not in {"week", "month", "quarter"}:
        raise ToolError("period must be week, month or quarter")
    return unit


def _mode_inputs(path: str, mode: str, **cols: Any) -> dict[str, Any]:
    return {"path": path, "mode": mode, **cols}


def _mode_categories(header: list[str], rows: list[dict[str, Any]], args: dict, path: str) -> dict[str, Any]:
    """Counts and shares of a category column, the Pareto set and the repeat ratio per id."""
    col = _column(args.get("column"), header, "column")
    allowed = _list(args.get("categories"), "categories")
    top_n = _int_arg(args, "top_n", 10)
    pareto_share = _float_arg(args, "pareto_share", 0.8)
    if not 0 < pareto_share <= 1:
        raise ToolError("pareto_share must be between 0 and 1")
    min_rows = _int_arg(args, "min_rows", 20, minimum=1)
    weight_col = _optional_column(args, "weight_column", header)
    id_col = _optional_column(args, "id_column", header)
    date_col = _optional_column(args, "date_column", header)
    date_from, date_to = _date_arg(args, "date_from"), _date_arg(args, "date_to")
    if (date_from or date_to) and not date_col:
        raise ToolError("date_from/date_to need a date_column")
    missing: list[str] = []
    warnings: list[str] = []
    kept, window = _window_rows(rows, date_col, date_from, date_to)
    if window["rows_without_date"]:
        missing.append(f"{_plural(window['rows_without_date'], 'row')} without a readable date in {date_col} left out.")
    allowed_map = {a.lower(): a for a in allowed or []}
    counts: dict[str, float] = {}
    unmapped: dict[str, float] = {}
    blank = bad_weight = 0
    ids: set[str] = set()
    matched = 0.0
    for row in kept:
        value = _text(row.get(col))
        weight = 1.0
        if weight_col:
            number, status = _parse_count(row.get(weight_col))
            if status == "bad":
                bad_weight += 1
                continue
            weight = number or 0.0
        if not value:
            blank += 1
            continue
        if allowed_map and value.lower() not in allowed_map:
            unmapped[value] = unmapped.get(value, 0.0) + weight
            continue
        key = allowed_map.get(value.lower(), value)
        counts[key] = counts.get(key, 0.0) + weight
        matched += weight
        if id_col and _text(row.get(id_col)):
            ids.add(_text(row.get(id_col)))
    if blank:
        missing.append(f"{_plural(blank, 'row')} with a blank {col} left out.")
    if bad_weight:
        missing.append(f"{_plural(bad_weight, 'row')} dropped because {weight_col} was not a number.")
    if not kept:
        missing.append("No rows in the window." if rows else "The file has a header but no data rows.")
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    groups = [{"value": v, "count": _num(c), "share_pct": _pct(c, matched)} for v, c in ordered]
    pareto: list[str] = []
    cumulative = 0.0
    for v, c in ordered:
        if matched <= 0 or cumulative >= pareto_share * matched:
            break
        cumulative += c
        pareto.append(v)
    unmapped_total = sum(unmapped.values())
    if allowed and unmapped:
        missing.append(f"{_num(unmapped_total)} rows hold values outside categories: {', '.join(sorted(unmapped))}.")
    if allowed and matched + unmapped_total > 0 and unmapped_total / (matched + unmapped_total) > 0.2:
        warnings.append("more than 20 % of rows fall outside the given categories: the shares describe only the mapped rows.")
    if 0 < len(kept) < min_rows:
        warnings.append(f"sample too small: {len(kept)} rows (fewer than {min_rows}); shares are rough.")
    distinct = len(ids) if id_col else None
    if id_col and distinct == 0:
        missing.append(f"{id_col} is blank in every row: no repeat ratio.")
    out: dict[str, Any] = {
        "inputs": _mode_inputs(
            path, "categories", column=col, categories=allowed, weight_column=weight_col, id_column=id_col, date_column=date_col,
            top_n=top_n, pareto_share=pareto_share,
        ),
        "window": window,
        "rows_total": len(kept),
        "rows_matched": _num(matched),
        "groups": groups[:top_n],
        "other_count": _num(sum(c for _, c in ordered[top_n:])),
        "pareto_groups": pareto,
        "pareto_share": pareto_share,
        "unmapped_values": sorted(unmapped),
        "distinct_ids": distinct,
        "repeat_ratio": _ratio(matched, distinct) if distinct else None,
        "warnings": warnings,
        "missing": missing,
    }
    if groups:
        top = groups[0]
        line = f"Of {_fmt(_num(matched))} rows, the largest category is {top['value']} with {_fmt(top['count'])} ({top['share_pct']:g} %)"
        line += f"; {len(pareto)} categor{'y' if len(pareto) == 1 else 'ies'} cover {pareto_share * 100:g} %: {', '.join(pareto)}."
        if distinct:
            line += f" {_fmt(_num(matched))} rows over {_fmt(distinct)} distinct {id_col}: {out['repeat_ratio']:g} per id."
    else:
        line = f"No categories counted in {col}."
    out["summary"] = line
    return out


def _mode_trend(header: list[str], rows: list[dict[str, Any]], args: dict, path: str) -> dict[str, Any]:
    """Cost of a new buyer per period, repeat share, baseline vs latest, and when trial fades."""
    new_col = _column(args.get("new_column"), header, "new_column")
    spend_col = _optional_column(args, "spend_column", header)
    repeat_col = _optional_column(args, "repeat_column", header)
    brand_col = _optional_column(args, "brand_spend_column", header)
    period_col = _optional_column(args, "period_column", header)
    date_col = _optional_column(args, "date_column", header)
    if not period_col and not date_col:
        raise ToolError("trend needs period_column (one row per period) or date_column with period=week|month")
    unit = _period_unit(args)
    baseline_n = _int_arg(args, "baseline_periods", 3)
    missing: list[str] = []
    warnings: list[str] = []
    if not spend_col:
        missing.append("no spend_column: cost per new buyer cannot be computed, only counts and repeat share.")
    buckets: dict[str, list[dict[str, Any]]] = {}
    order: dict[str, date | None] = {}
    without = 0
    for row in rows:
        if period_col:
            key = _text(row.get(period_col))
            if not key:
                without += 1
                continue
            order.setdefault(key, _parse_date(key))
        else:
            d = _parse_date(row.get(date_col))
            if d is None:
                without += 1
                continue
            key = _period_key(d, unit)
            order.setdefault(key, d.replace(day=1))
        buckets.setdefault(key, []).append(row)
    if without:
        missing.append(f"{_plural(without, 'row')} without a readable {period_col or date_col} left out.")
    if all(v is not None for v in order.values()) and order:
        keys = sorted(order, key=lambda k: order[k])  # type: ignore[arg-type]
    else:
        keys = list(order)
    periods = []
    for key in keys:
        prow = buckets[key]
        new = _sum_column(prow, new_col, missing, f"{new_col} ({key})")
        spend = _sum_column(prow, spend_col, missing, f"{spend_col} ({key})") if spend_col else None
        repeat = _sum_column(prow, repeat_col, missing, f"{repeat_col} ({key})") if repeat_col else None
        brand = _sum_column(prow, brand_col, missing, f"{brand_col} ({key})") if brand_col else None
        entry: dict[str, Any] = {"period": key, "rows": len(prow), "new": _num(new)}
        if spend is not None:
            entry["spend"] = _num(spend)
            entry["cost_per_new"] = _ratio(spend, new)
            if new == 0:
                missing.append(f"{key}: 0 new buyers, cost per new buyer undefined.")
        if repeat is not None:
            entry["repeat"] = _num(repeat)
            entry["repeat_share_pct"] = _pct(repeat, new + repeat)
        if brand is not None and spend is not None:
            entry["brand_share_of_spend_pct"] = _pct(brand, spend + brand)
        periods.append(entry)
    out: dict[str, Any] = {
        "inputs": _mode_inputs(
            path, "trend", period_column=period_col, date_column=date_col, period=unit if date_col else None, spend_column=spend_col,
            new_column=new_col, repeat_column=repeat_col, brand_spend_column=brand_col, baseline_periods=baseline_n,
        ),
        "periods": periods,
        "findings": [],
        "method": (
            "cost per new buyer = spend / new buyers per period; baseline = pooled over the first baseline_periods "
            "(total spend / total new buyers); trial_fading from the first period where new buyers fall below 70 % of the "
            "baseline mean for two consecutive periods while cost per new buyer exceeds 130 % of the baseline; drift = latest "
            "cost against the pooled cost of all earlier periods, stale beyond ±20 %."
        ),
    }
    findings: list[str] = out["findings"]
    if len(periods) < 2:
        findings.append("too_few_periods")
        missing.append(f"{len(periods)} period{'s' if len(periods) != 1 else ''}: no baseline or trend can be read.")
        out.update({"baseline": None, "latest": None, "warnings": warnings, "missing": missing})
        out["summary"] = f"Only {len(periods)} period{'s' if len(periods) != 1 else ''} in the file: nothing to compare yet."
        return out
    if len(periods) < 4:
        findings.append("too_few_periods")
        warnings.append(f"only {len(periods)} periods: baseline and latest are shown, trial_fading is not judged.")
    base = periods[:baseline_n]
    base_new = sum(float(p["new"]) for p in base)
    base_mean_new = base_new / len(base)
    base_cost = None
    if spend_col:
        base_spend = sum(float(p["spend"]) for p in base)
        base_cost = _ratio(base_spend, base_new)
    latest = periods[-1]
    out["baseline"] = {"periods": [p["period"] for p in base], "mean_new": round(base_mean_new, 1), "cost_per_new": base_cost}
    out["latest"] = {
        "period": latest["period"],
        "new": latest["new"],
        "cost_per_new": latest.get("cost_per_new"),
        "repeat_share_pct": latest.get("repeat_share_pct"),
        "new_change_pct": _pct(float(latest["new"]) - base_mean_new, base_mean_new),
        "cost_change_pct": (
            _pct(latest["cost_per_new"] - base_cost, base_cost) if base_cost and latest.get("cost_per_new") is not None else None
        ),
    }
    if spend_col and len(periods) >= 2:
        prior = periods[:-1]
        prior_spend = sum(float(p["spend"]) for p in prior)
        prior_new = sum(float(p["new"]) for p in prior)
        prior_cost = _ratio(prior_spend, prior_new)
        drift = _pct(latest["cost_per_new"] - prior_cost, prior_cost) if prior_cost and latest.get("cost_per_new") is not None else None
        out["drift"] = {"prior_pooled_cost_per_new": prior_cost, "latest_cost_per_new": latest.get("cost_per_new"), "drift_pct": drift}
        out["baseline_is_stale"] = bool(drift is not None and abs(drift) > 20)
        if out["baseline_is_stale"]:
            findings.append("baseline_is_stale")
    trial_fading_since = None
    if len(periods) >= 4 and spend_col and base_cost:
        for i in range(len(base), len(periods) - 1):
            p, q = periods[i], periods[i + 1]
            if (
                float(p["new"]) < 0.7 * base_mean_new
                and float(q["new"]) < 0.7 * base_mean_new
                and p.get("cost_per_new") is not None
                and p["cost_per_new"] > 1.3 * base_cost
            ):
                trial_fading_since = p["period"]
                break
    elif len(periods) >= 4 and not spend_col:
        missing.append("trial_fading needs spend_column.")
    out["trial_fading"] = trial_fading_since is not None
    out["trial_fading_since"] = trial_fading_since
    if trial_fading_since:
        findings.append("trial_fading")
    if repeat_col:
        crossing = next((p["period"] for p in periods if p.get("repeat") is not None and float(p["repeat"]) > float(p["new"])), None)
        out["repeat_exceeds_new_from"] = crossing
        shares = [p["repeat_share_pct"] for p in periods if p.get("repeat_share_pct") is not None]
        out["repeat_share_trend"] = (
            "rising" if len(shares) >= 2 and shares[-1] > shares[0] else "falling" if len(shares) >= 2 and shares[-1] < shares[0] else "flat"
        )
        if latest.get("repeat_share_pct") is not None and latest["repeat_share_pct"] < 20:
            findings.append("repeat_weak")
    out["warnings"] = warnings
    out["missing"] = missing
    line = f"{latest['period']}: {_fmt(latest['new'])} new buyers against a first-{len(base)}-period mean of {base_mean_new:,.1f}"
    line += f" ({out['latest']['new_change_pct']:+g} %)" if out["latest"]["new_change_pct"] is not None else ""
    if base_cost is not None and latest.get("cost_per_new") is not None:
        line += f"; cost per new buyer {_fmt(latest['cost_per_new'])} vs baseline {_fmt(base_cost)}"
        if out["latest"]["cost_change_pct"] is not None:
            line += f" ({out['latest']['cost_change_pct']:+g} %)"
    line += "."
    if repeat_col and out.get("repeat_exceeds_new_from"):
        line += f" Repeat buyers outnumber new buyers from {out['repeat_exceeds_new_from']}."
    if trial_fading_since:
        line += f" Trial is fading since {trial_fading_since}."
    out["summary"] = line
    return out


def _mode_offers(header: list[str], rows: list[dict[str, Any]], args: dict, path: str) -> dict[str, Any]:
    """Timed offers over periods: discounted share, average discount, realized price, customers who only buy on discount."""
    date_col = _column(args.get("date_column"), header, "date_column")
    revenue_col = _column(args.get("revenue_column"), header, "revenue_column")
    discount_col = _column(args.get("discount_column"), header, "discount_column")
    customer_col = _optional_column(args, "customer_column", header)
    unit = _period_unit(args)
    date_from, date_to = _date_arg(args, "date_from"), _date_arg(args, "date_to")
    missing: list[str] = []
    warnings: list[str] = []
    kept, window = _window_rows(rows, date_col, date_from, date_to)
    if window["rows_without_date"]:
        missing.append(f"{_plural(window['rows_without_date'], 'row')} without a readable date in {date_col} left out.")
    if not kept:
        missing.append("No rows in the window." if rows else "The file has a header but no data rows.")
    numeric_discounts = [n for n in (_parse_number(r.get(discount_col))[0] for r in kept if _text(r.get(discount_col))) if n is not None]
    text_cells = sum(1 for r in kept if _text(r.get(discount_col)) and _parse_number(r.get(discount_col))[1] != "ok")
    pct_scale = None
    if numeric_discounts and text_cells == 0:
        positives = [n for n in numeric_discounts if n > 0]
        pct_scale = 100.0 if positives and max(positives) <= 1 else 1.0
    periods: dict[str, dict[str, Any]] = {}
    customers: dict[str, list[tuple[date, bool]]] = {}
    blank_discount = bad_revenue = blank_customer = 0
    for row in kept:
        d = _parse_date(row.get(date_col))
        assert d is not None
        key = _period_key(d, unit)
        p = periods.setdefault(key, {"orders": 0, "discounted": 0, "revenue": 0.0, "full_price_revenue": 0.0, "full_price_orders": 0, "discount_sum": 0.0, "discount_n": 0})
        flag = _parse_flag(row.get(discount_col))
        if flag is None:
            blank_discount += 1
            flag = False
        revenue, status = _parse_number(row.get(revenue_col))
        if status != "ok" or revenue is None:
            bad_revenue += 1
            continue
        p["orders"] += 1
        p["revenue"] += revenue
        if flag:
            p["discounted"] += 1
            if pct_scale is not None:
                number, _ = _parse_number(row.get(discount_col))
                if number is not None and number > 0:
                    p["discount_sum"] += number * pct_scale
                    p["discount_n"] += 1
        else:
            p["full_price_orders"] += 1
            p["full_price_revenue"] += revenue
        if customer_col:
            ident = _text(row.get(customer_col))
            if not ident:
                blank_customer += 1
            else:
                customers.setdefault(ident, []).append((d, flag))
    if blank_discount:
        missing.append(f"{discount_col}: {_plural(blank_discount, 'blank cell')} counted as not discounted.")
    if bad_revenue:
        missing.append(f"{_plural(bad_revenue, 'row')} dropped because {revenue_col} was not a number.")
    if blank_customer:
        missing.append(f"{_plural(blank_customer, 'row')} with a blank {customer_col} left out of the customer figures.")
    if pct_scale is None and numeric_discounts:
        missing.append(f"{discount_col} mixes numbers and text: read as a yes/no flag, average discount not computed.")
    table = []
    for key in sorted(periods):
        p = periods[key]
        table.append(
            {
                "period": key,
                "orders": p["orders"],
                "discounted_orders": p["discounted"],
                "discount_share_pct": _pct(p["discounted"], p["orders"]),
                "avg_discount_pct": round(p["discount_sum"] / p["discount_n"], 1) if p["discount_n"] else None,
                "revenue": _num(p["revenue"]),
                "realized_avg_price": _ratio(p["revenue"], p["orders"]),
                "full_price_avg_price": _ratio(p["full_price_revenue"], p["full_price_orders"]),
            }
        )
    out: dict[str, Any] = {
        "inputs": _mode_inputs(
            path, "offers", date_column=date_col, revenue_column=revenue_col, discount_column=discount_col, customer_column=customer_col,
            period=unit, discount_read_as="percent" if pct_scale is not None else "flag",
        ),
        "window": window,
        "periods": table,
        "method": (
            "discount share = discounted orders / orders per period; realized average price = revenue / orders; "
            "a customer is discount-only when every order carries the discount flag; acquired-on-discount customers are those "
            "whose first order was discounted, and their full-price repeat rate is the share who later bought without a discount."
        ),
    }
    if customer_col:
        with_orders = len(customers)
        discount_only = sum(1 for orders in customers.values() if all(f for _, f in orders))
        acquired = [orders for orders in customers.values() if sorted(orders)[0][1]]
        full_later = sum(1 for orders in acquired if any(not f for _, f in sorted(orders)[1:]))
        out["customers"] = {
            "with_orders": with_orders,
            "discount_only": discount_only,
            "discount_only_share_pct": _pct(discount_only, with_orders),
            "acquired_on_discount": len(acquired),
            "acquired_on_discount_full_price_later": full_later,
            "full_price_repeat_rate_pct": _pct(full_later, len(acquired)),
        }
    else:
        missing.append("no customer_column: the share of customers who only buy on discount is unavailable.")
    if len(table) >= 2:
        first, last = table[0], table[-1]
        out["change"] = {
            "from": first["period"],
            "to": last["period"],
            "discount_share_pp": (
                round(last["discount_share_pct"] - first["discount_share_pct"], 1)
                if first["discount_share_pct"] is not None and last["discount_share_pct"] is not None
                else None
            ),
            "realized_avg_price_change_pct": (
                _pct(last["realized_avg_price"] - first["realized_avg_price"], first["realized_avg_price"])
                if first["realized_avg_price"] and last["realized_avg_price"] is not None
                else None
            ),
        }
        if (out["change"]["discount_share_pp"] or 0) >= 10 and (out["change"]["realized_avg_price_change_pct"] or 0) < 0:
            warnings.append(
                f"discount share rose from {first['discount_share_pct']:g} % to {last['discount_share_pct']:g} % while the realized "
                f"average price fell from {_fmt(first['realized_avg_price'])} to {_fmt(last['realized_avg_price'])}: buyers may be waiting for the offer."
            )
    else:
        missing.append("fewer than two periods: no change over time can be read.")
    out["warnings"] = warnings
    out["missing"] = missing
    if table:
        first, last = table[0], table[-1]
        line = f"Discounted share of orders {first['discount_share_pct']:g} % ({first['period']})"
        if len(table) >= 2:
            line += f" → {last['discount_share_pct']:g} % ({last['period']})"
        if first["realized_avg_price"] is not None:
            line += f"; realized average price {_fmt(first['realized_avg_price'])}"
            if len(table) >= 2 and last["realized_avg_price"] is not None:
                line += f" → {_fmt(last['realized_avg_price'])}"
        line += "."
        if customer_col and out["customers"]["acquired_on_discount"]:
            c = out["customers"]
            line += (
                f" {c['discount_only_share_pct']:g} % of {_fmt(c['with_orders'])} customers only ever buy on discount; of "
                f"{_fmt(c['acquired_on_discount'])} acquired on discount, {c['full_price_repeat_rate_pct']:g} % later paid full price."
            )
    else:
        line = "No orders to read."
    out["summary"] = line
    return out


def _purchases(
    rows: list[dict[str, Any]], id_col: str, date_col: str, qty_col: str | None, missing: list[str], extra: dict[str, str] | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Per id, the list of purchases {date, qty, ...extra columns}; blank ids, bad dates and bad quantities are reported."""
    per_id: dict[str, list[dict[str, Any]]] = {}
    blank_id = bad_date = bad_qty = 0
    for row in rows:
        ident = _text(row.get(id_col))
        if not ident:
            blank_id += 1
            continue
        d = _parse_date(row.get(date_col))
        if d is None:
            bad_date += 1
            continue
        qty = 1.0
        if qty_col:
            number, status = _parse_count(row.get(qty_col))
            if status == "bad":
                bad_qty += 1
                continue
            qty = number if status == "ok" and number is not None else 1.0
        item: dict[str, Any] = {"date": d, "qty": qty}
        for key, col in (extra or {}).items():
            item[key] = row.get(col)
        per_id.setdefault(ident, []).append(item)
    if blank_id:
        missing.append(f"{_plural(blank_id, 'row')} with a blank {id_col} left out.")
    if bad_date:
        missing.append(f"{_plural(bad_date, 'row')} without a readable date in {date_col} left out.")
    if bad_qty:
        missing.append(f"{_plural(bad_qty, 'row')} dropped because {qty_col} was not a number.")
    return per_id


def _mode_buyer_classes(header: list[str], rows: list[dict[str, Any]], args: dict, path: str) -> dict[str, Any]:
    """Promotion buyers by pre-period frequency: expected vs actual units, substitution, post-promo repurchase."""
    id_col = _column(args.get("id_column"), header, "id_column")
    date_col = _column(args.get("date_column"), header, "date_column")
    qty_col = _optional_column(args, "quantity_column", header)
    flag_col = _optional_column(args, "promo_flag_column", header)
    promo_start = _date_arg(args, "promo_start", required=True)
    promo_end = _date_arg(args, "promo_end", required=True)
    if promo_start > promo_end:
        raise ToolError("promo_start is after promo_end")
    promo_days = (promo_end - promo_start).days + 1
    pre_start, pre_end = _date_arg(args, "pre_start"), _date_arg(args, "pre_end")
    if (pre_start is None) != (pre_end is None):
        raise ToolError("give both pre_start and pre_end, or neither")
    if pre_start is None:
        pre_end = promo_start - timedelta(days=1)
        pre_weeks = args.get("pre_weeks")
        length = _int_arg(args, "pre_weeks", 0, minimum=1) * 7 if pre_weeks not in (None, "") else promo_days
        pre_start = pre_end - timedelta(days=length - 1)
    assert pre_end is not None
    if pre_end >= promo_start:
        raise ToolError("the pre-period must end before promo_start")
    pre_days = (pre_end - pre_start).days + 1
    cutoffs = args.get("class_cutoffs") or {}
    if not isinstance(cutoffs, dict):
        raise ToolError("class_cutoffs must be {light_max, heavy_min}")
    light_max = _int_arg(cutoffs, "light_max", 1)
    heavy_min = _int_arg(cutoffs, "heavy_min", 3)
    if heavy_min <= light_max:
        raise ToolError("class_cutoffs.heavy_min must be greater than light_max")
    missing: list[str] = []
    warnings: list[str] = []
    per_id = _purchases(rows, id_col, date_col, qty_col, missing, {"flag": flag_col} if flag_col else None)
    all_dates = [p["date"] for items in per_id.values() for p in items]
    file_from, file_to = (min(all_dates), max(all_dates)) if all_dates else (None, None)
    expected_ok = True
    if file_from is None:
        missing.append("No rows with an id and a readable date.")
        expected_ok = False
    else:
        if file_from >= promo_start:
            missing.append(
                f"pre_period: the file starts {file_from.isoformat()}, on or after promo_start; every promo buyer looks like a new buyer "
                "and expected purchases cannot be estimated."
            )
            expected_ok = False
        elif file_from > pre_start:
            warnings.append(f"the file starts {file_from.isoformat()}, inside the pre-period {pre_start.isoformat()}..{pre_end.isoformat()}: pre-period rates are understated.")
        if file_to < promo_end:
            warnings.append(f"the file ends {file_to.isoformat()}, before promo_end: promo figures are partial.")
    if pre_days < 28:
        missing.append(f"pre-period is {pre_days} days (under 28): expected purchases are not estimated.")
        expected_ok = False
    ratio = promo_days / pre_days
    has_post = file_to is not None and file_to > promo_end
    if not has_post:
        missing.append("no rows after promo_end: post-promo repurchase cannot be measured.")

    def class_of(n: int) -> str:
        if n == 0:
            return "non"
        if n <= light_max:
            return "light"
        if n >= heavy_min:
            return "heavy"
        return "medium"

    class_names = ["non", "light"] + (["medium"] if heavy_min > light_max + 1 else []) + ["heavy"]
    classes = {
        c: {"buyers": 0, "promo_buyers": 0, "pre_purchases": 0, "pre_units": 0.0, "promo_purchases": 0, "promo_units": 0.0, "post_buyers": 0}
        for c in class_names
    }
    non_promo_rows = 0
    for items in per_id.values():
        pre = [p for p in items if pre_start <= p["date"] <= pre_end]
        promo = [p for p in items if promo_start <= p["date"] <= promo_end]
        if flag_col:
            flagged = [p for p in promo if _parse_flag(p.get("flag"))]
            non_promo_rows += len(promo) - len(flagged)
            promo = flagged
        post = [p for p in items if p["date"] > promo_end]
        cls = class_of(len(pre))
        if cls == "non" and not promo:
            continue  # neither a pre-period buyer nor a promo buyer: outside the question
        c = classes[cls]
        c["buyers"] += 1
        c["pre_purchases"] += len(pre)
        c["pre_units"] += sum(p["qty"] for p in pre)
        if promo:
            c["promo_buyers"] += 1
            c["promo_purchases"] += len(promo)
            c["promo_units"] += sum(p["qty"] for p in promo)
            if post:
                c["post_buyers"] += 1
    if flag_col and non_promo_rows:
        missing.append(f"{_plural(non_promo_rows, 'purchase')} inside the promo window without the {flag_col} flag counted as regular, not promo.")
    total_units = sum(c["promo_units"] for c in classes.values())
    table = []
    total_expected = 0.0
    for name in class_names:
        c = classes[name]
        expected = c["pre_units"] * ratio if expected_ok else None
        entry: dict[str, Any] = {
            "class": name,
            "definition": {
                "non": "no purchase in the pre-period",
                "light": f"1–{light_max} purchase{'s' if light_max != 1 else ''} in the pre-period",
                "medium": f"{light_max + 1}–{heavy_min - 1} purchases in the pre-period",
                "heavy": f"{heavy_min}+ purchases in the pre-period",
            }[name],
            "buyers": c["buyers"],
            "promo_buyers": c["promo_buyers"],
            "pre_purchases": c["pre_purchases"],
            "pre_units": _num(c["pre_units"]),
            "promo_purchases": c["promo_purchases"],
            "promo_units": _num(c["promo_units"]),
            "share_of_promo_units_pct": _pct(c["promo_units"], total_units),
            "expected_promo_units": None if expected is None else _num(expected),
            "incremental_units": None if expected is None else _num(c["promo_units"] - expected),
            "substitution_share_pct": None if expected is None else _pct(min(expected, c["promo_units"]), c["promo_units"]),
            "post_promo_repurchase_pct": _pct(c["post_buyers"], c["promo_buyers"]) if has_post else None,
        }
        if expected is not None:
            total_expected += expected
        table.append(entry)
    out: dict[str, Any] = {
        "inputs": _mode_inputs(
            path, "buyer_classes", id_column=id_col, date_column=date_col, quantity_column=qty_col, promo_flag_column=flag_col,
            promo_start=promo_start.isoformat(), promo_end=promo_end.isoformat(), pre_start=pre_start.isoformat(), pre_end=pre_end.isoformat(),
            class_cutoffs={"light_max": light_max, "heavy_min": heavy_min},
        ),
        "window": {"from": file_from.isoformat() if file_from else None, "to": file_to.isoformat() if file_to else None, "rows_total": len(rows), "ids": len(per_id)},
        "promo_days": promo_days,
        "pre_days": pre_days,
        "classes": table,
        "totals": {
            "promo_buyers": sum(c["promo_buyers"] for c in classes.values()),
            "promo_units": _num(total_units),
            "expected_units": _num(total_expected) if expected_ok else None,
            "incremental_units": _num(total_units - total_expected) if expected_ok else None,
            "incremental_share_pct": _pct(total_units - total_expected, total_units) if expected_ok else None,
            "units_from_previous_nonbuyers_pct": _pct(classes["non"]["promo_units"], total_units),
        },
        "method": (
            "classes by purchases in the pre-period; expected promo units per class = pre-period units × promo days / pre-period days; "
            "incremental = promo units − expected; substitution share = min(expected, actual) / actual; post-promo repurchase = share of "
            "promo buyers in the class with a purchase after promo_end. Units are rows when no quantity_column is given."
        ),
        "warnings": warnings,
        "missing": missing,
    }
    t = out["totals"]
    if total_units > 0:
        line = f"{_fmt(t['promo_units'])} promo units from {_fmt(t['promo_buyers'])} buyers between {promo_start.isoformat()} and {promo_end.isoformat()}"
        if expected_ok:
            line += f"; {_fmt(t['incremental_units'])} ({t['incremental_share_pct']:g} %) above the pre-period pace, the rest replaced ordinary purchases"
        line += f"; {t['units_from_previous_nonbuyers_pct']:g} % came from buyers with no pre-period purchase."
    else:
        line = f"No promo purchases between {promo_start.isoformat()} and {promo_end.isoformat()}."
    out["summary"] = line
    return out


def _mode_recruits(header: list[str], rows: list[dict[str, Any]], args: dict, path: str) -> dict[str, Any]:
    """Buyers of a new product (or a free trial) split into new-to-brand and existing, with their later repeat."""
    id_col = _column(args.get("id_column"), header, "id_column")
    product_col = _column(args.get("product_column"), header, "product_column")
    date_col = _column(args.get("date_column"), header, "date_column")
    new_products = _list(args.get("new_products"), "new_products")
    if not new_products:
        raise ToolError("new_products is required: the SKU or product names of the new product")
    launch = _date_arg(args, "launch_date")
    min_history = _int_arg(args, "min_history_days", 90, minimum=0)
    qty_col = _optional_column(args, "quantity_column", header)
    price_col = _optional_column(args, "price_column", header)
    cycle_days = _int_arg(args, "cycle_days", 0, minimum=1) if args.get("cycle_days") not in (None, "") else None
    as_of = _date_arg(args, "as_of")
    missing: list[str] = []
    warnings: list[str] = []
    extra = {"product": product_col}
    if price_col:
        extra["price"] = price_col
    per_id = _purchases(rows, id_col, date_col, qty_col, missing, extra)
    new_set = {p.lower() for p in new_products}
    all_dates = [p["date"] for items in per_id.values() for p in items]
    file_from, file_to = (min(all_dates), max(all_dates)) if all_dates else (None, None)
    products_seen = {_text(p["product"]) for items in per_id.values() for p in items}
    absent = [p for p in new_products if p.lower() not in {s.lower() for s in products_seen}]
    if absent:
        missing.append(f"new_products not found in {product_col}: {', '.join(absent)}.")
    first_new_dates = [min(p["date"] for p in items if _text(p["product"]).lower() in new_set) for items in per_id.values() if any(_text(p["product"]).lower() in new_set for p in items)]
    if launch is None and first_new_dates:
        launch = min(first_new_dates)
        warnings.append(f"no launch_date given: the first {new_products[0]} purchase on {launch.isoformat()} is used.")
    history_days = (launch - file_from).days if launch and file_from else None
    unsuitable = None
    if file_from is None:
        unsuitable = "no rows with an id, product and readable date"
    elif history_days is not None and history_days < min_history:
        unsuitable = (
            f"only {history_days} days of history before {launch.isoformat()} (min_history_days {min_history}): "
            "existing buyers would look new, so no new-to-brand share is returned"
        )
    reference = as_of or file_to
    groups = {
        "new_to_brand": {"buyers": 0, "new_orders": 0, "new_units": 0.0, "revenue": 0.0, "orders": 0, "repeat_new": 0, "other_after": 0, "other_units_after": 0.0, "past_cycle": 0, "repeat_past_cycle": 0, "lags": []},
        "existing": {"buyers": 0, "new_orders": 0, "new_units": 0.0, "revenue": 0.0, "orders": 0, "repeat_new": 0, "other_after": 0, "other_units_after": 0.0, "past_cycle": 0, "repeat_past_cycle": 0, "lags": []},
    }
    other_skus: dict[str, dict[str, int]] = {"new_to_brand": {}, "existing": {}}
    buyers_of_new = 0
    last_new_date = None
    for items in per_id.values():
        new_items = [p for p in items if _text(p["product"]).lower() in new_set]
        if not new_items:
            continue
        buyers_of_new += 1
        first_new = min(p["date"] for p in new_items)
        last_new_date = max(last_new_date, first_new) if last_new_date else first_new
        before_other = [p for p in items if _text(p["product"]).lower() not in new_set and p["date"] < first_new]
        key = "existing" if before_other else "new_to_brand"
        g = groups[key]
        g["buyers"] += 1
        g["new_orders"] += len(new_items)
        g["new_units"] += sum(p["qty"] for p in new_items)
        g["orders"] += len(items)
        if price_col:
            for p in items:
                number, status = _parse_number(p.get("price"))
                if status == "ok" and number is not None:
                    g["revenue"] += number
        if len(new_items) > 1:
            g["repeat_new"] += 1
        other_after = [p for p in items if _text(p["product"]).lower() not in new_set and p["date"] > first_new]
        if other_after:
            g["other_after"] += 1
            g["other_units_after"] += sum(p["qty"] for p in other_after)
            g["lags"].append((min(p["date"] for p in other_after) - first_new).days)
            for p in other_after:
                name = _text(p["product"])
                other_skus[key][name] = other_skus[key].get(name, 0) + 1
        if cycle_days and reference and first_new + timedelta(days=cycle_days) <= reference:
            g["past_cycle"] += 1
            if other_after:
                g["repeat_past_cycle"] += 1
    per_group = {}
    for key, g in groups.items():
        entry: dict[str, Any] = {
            "buyers": g["buyers"],
            "new_product_orders": g["new_orders"],
            "new_product_units": _num(g["new_units"]),
            "orders_per_buyer": _ratio(g["orders"], g["buyers"]),
            "repeat_on_new_product_pct": _pct(g["repeat_new"], g["buyers"]),
            "bought_other_products_later": g["other_after"],
            "bought_other_products_later_pct": _pct(g["other_after"], g["buyers"]),
            "other_units_after": _num(g["other_units_after"]),
            "other_units_per_buyer": _ratio(g["other_units_after"], g["buyers"]),
            "median_days_to_other_product": statistics.median(g["lags"]) if g["lags"] else None,
            "top_other_products": [k for k, _ in sorted(other_skus[key].items(), key=lambda kv: (-kv[1], kv[0]))[:5]],
        }
        if price_col:
            entry["avg_order_value"] = _ratio(g["revenue"], g["orders"])
        if cycle_days:
            entry["buyers_past_one_cycle"] = g["past_cycle"]
            entry["bought_other_products_later_pct_matured"] = _pct(g["repeat_past_cycle"], g["past_cycle"])
        per_group[key] = entry
    new_group, existing = groups["new_to_brand"], groups["existing"]
    out: dict[str, Any] = {
        "inputs": _mode_inputs(
            path, "recruits", id_column=id_col, product_column=product_col, date_column=date_col, new_products=new_products,
            launch_date=launch.isoformat() if launch else None, min_history_days=min_history, quantity_column=qty_col, price_column=price_col,
            cycle_days=cycle_days,
        ),
        "window": {"from": file_from.isoformat() if file_from else None, "to": file_to.isoformat() if file_to else None, "rows_total": len(rows), "ids": len(per_id)},
        "pre_launch_history_days": history_days,
        "buyers_of_new_product": buyers_of_new,
        "unsuitable": unsuitable,
        "new_to_brand": None if unsuitable else new_group["buyers"],
        "existing": None if unsuitable else existing["buyers"],
        "new_to_brand_share_pct": None if unsuitable else _pct(new_group["buyers"], buyers_of_new),
        "existing_lapsed_other_products": None if unsuitable else existing["buyers"] - existing["other_after"],
        "per_group": None if unsuitable else per_group,
        "method": (
            "a buyer of the new product is new to the brand when no other product was bought before their first new-product purchase; "
            "later repeat = share with any other-product purchase after that date; existing_lapsed_other_products = existing buyers with "
            "no other-product purchase after it."
        ),
        "warnings": warnings,
        "missing": missing,
    }
    if cycle_days and last_new_date:
        matured_on = last_new_date + timedelta(days=cycle_days)
        out["maturity"] = {
            "as_of": reference.isoformat() if reference else None,
            "last_first_purchase": last_new_date.isoformat(),
            "matured_on": matured_on.isoformat(),
            "all_matured": bool(reference and matured_on <= reference),
        }
        if reference and matured_on > reference:
            warnings.append(f"not every buyer has had {cycle_days} days yet: repeat figures are premature until {matured_on.isoformat()}.")
    elif args.get("cycle_days") in (None, "") and buyers_of_new:
        missing.append("no cycle_days: whether buyers have had time to repeat is not assessed.")
    if unsuitable:
        out["summary"] = f"Cannot split {_fmt(buyers_of_new)} buyers of {', '.join(new_products)}: {unsuitable}."
    elif buyers_of_new:
        line = (
            f"{_fmt(new_group['buyers'])} of {_fmt(buyers_of_new)} buyers of {', '.join(new_products)} ({out['new_to_brand_share_pct']:g} %) "
            f"are new to the brand ({history_days} days of history before {launch.isoformat()}); "
            f"{per_group['new_to_brand']['bought_other_products_later_pct']:g} % of them later bought another product, "
            f"as did {per_group['existing']['bought_other_products_later_pct']:g} % of the {_fmt(existing['buyers'])} existing buyers."
        )
        out["summary"] = line
    else:
        out["summary"] = f"No buyer of {', '.join(new_products)} in the file."
    return out


def funnel_analyze_tool(tools: WorkspaceTools, args: dict) -> str:
    """Backs the worker-facing ``funnel_analyze`` tool: JSON the worker can quote from."""
    return json.dumps(funnel_analyze(tools, args or {}), ensure_ascii=False, indent=1, default=str)
