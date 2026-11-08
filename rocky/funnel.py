"""Funnel arithmetic from the marketer's own file, so workers stop describing funnels in prose.

``funnel_analyze`` reads a CSV or XLSX inside the workspace, sums each stage over
the rows that fall in the date window, and reports the counts, the denominators
behind every percentage, the step that loses the most, the dates actually covered
and every data problem it met. Nothing is estimated: a blank cell is reported as
blank, a bad number drops its row and says so, an empty window returns zeros with
the reason under ``missing``.
"""

from __future__ import annotations

import csv
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from rocky.tools import ToolError, WorkspaceTools

# Groups with fewer first-stage units than this are listed but not ranked as "worst":
# a 2-of-5 funnel says nothing about the channel.
MIN_GROUP_FIRST_STAGE = 30
# Below this many at the first stage the percentages are rough and the output says so.
SMALL_FUNNEL = 100
BLANK_GROUP = "(blank)"
_DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%Y/%m/%d", "%Y.%m.%d", "%d/%m/%Y", "%Y%m%d")


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


_THOUSANDS = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")


def _parse_count(value: Any) -> tuple[float | None, str]:
    """(number, status) where status is ok, blank or bad. Counts may not be negative."""
    if value is None:
        return None, "blank"
    if isinstance(value, bool):
        return (1.0 if value else 0.0), "ok"
    if isinstance(value, (int, float)):
        return (float(value), "ok") if value >= 0 else (None, "bad")
    text = str(value).strip().replace(" ", "").replace(" ", "")
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
    if number != number or number in (float("inf"), float("-inf")) or number < 0:
        return None, "bad"
    return number, "ok"


def _num(value: float) -> int | float:
    return int(round(value)) if abs(value - round(value)) < 1e-9 else round(value, 2)


def _pct(part: float, whole: float) -> float | None:
    return None if whole <= 0 else round(part / whole * 100, 1)


def _fmt(value: int | float) -> str:
    return f"{value:,}" if isinstance(value, int) else f"{value:,.2f}"


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


def funnel_analyze(tools: WorkspaceTools, args: dict) -> dict[str, Any]:
    """Counts, denominators, the largest drop-off and the data gaps of a funnel in a workspace file.

    ``path`` is a CSV or XLSX relative to the workspace (``sheet`` picks an XLSX
    sheet). Wide layout: ``stages`` is the ordered list of columns holding counts,
    summed over the rows in the window. Long layout: ``stage_column`` names the
    stage per row, with ``count_column`` (summed), ``id_column`` (unique ids per
    stage) or neither (rows counted); ``stage_order`` fixes the order. Optional
    ``date_column`` with ``date_from``/``date_to`` (ISO) restricts the rows;
    ``group_by`` adds a funnel per channel, campaign or cohort.
    """
    args = args or {}
    path = str(args.get("path") or "").strip()
    if not path:
        raise ToolError("path is required: a CSV or XLSX file inside the workspace")
    stages_arg = _list(args.get("stages"), "stages")
    stage_column = args.get("stage_column")
    layout = str(args.get("layout") or ("long" if stage_column and not stages_arg else "wide")).strip().lower()
    if layout not in {"wide", "long"}:
        raise ToolError("layout must be wide or long")
    if layout == "wide" and not stages_arg:
        raise ToolError("wide layout needs stages: the ordered list of columns holding the counts")
    if layout == "long" and not stage_column:
        raise ToolError("long layout needs stage_column: the column naming the stage of each row")

    header, rows = _load_rows(tools, path, args.get("sheet"))
    date_col = _column(args["date_column"], header, "date_column") if args.get("date_column") else None
    group_col = _column(args["group_by"], header, "group_by") if args.get("group_by") else None
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

    missing: list[str] = []
    inputs: dict[str, Any] = {
        "path": path,
        "layout": layout,
        "date_column": date_col,
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "group_by": group_col,
    }
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

    if group_col is not None:
        out["groups"], out["worst_group"] = _groups(kept, group_col, layout, inputs, count_col, id_col, order, names, missing)
        out["worst_group_threshold"] = (
            f"worst_group is chosen by overall conversion among groups with at least {MIN_GROUP_FIRST_STAGE} at the first stage."
        )

    out["missing"] = missing
    rows_desc = f"Across {window['rows_in_window']} row{'s' if window['rows_in_window'] != 1 else ''}"
    out["summary"] = _summary(window, names, funnel, rows_desc)
    return out


def _groups(
    rows: list[dict[str, Any]],
    group_col: str,
    layout: str,
    inputs: dict[str, Any],
    count_col: str | None,
    id_col: str | None,
    order: list[str] | None,
    names: list[str],
    missing: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = row.get(group_col)
        key = BLANK_GROUP if key is None or str(key).strip() == "" else str(key).strip()
        buckets.setdefault(key, []).append(row)
    if BLANK_GROUP in buckets:
        n = len(buckets[BLANK_GROUP])
        missing.append(f"{n} row{'s' if n != 1 else ''} with a blank {group_col} grouped as {BLANK_GROUP}.")
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


def funnel_analyze_tool(tools: WorkspaceTools, args: dict) -> str:
    """Backs the worker-facing ``funnel_analyze`` tool: JSON the worker can quote from."""
    return json.dumps(funnel_analyze(tools, args or {}), ensure_ascii=False, indent=1)
