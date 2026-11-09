"""Brand-growth and repeat-buying arithmetic from the marketer's own transaction file.

``repeat_buying`` reads one-row-per-order data (customer id, date, optional amount,
order id, sku, brand, units) inside the workspace and answers the loyalty questions
that are usually answered by impression: how many buyers bought once, whether
"repeat" means the same thing under two definitions, whether buyers skip a period
and come back, how cohorts retain, how many buyers enter and leave each period, how
much two brands share buyers, and whether a brand's loyalty is what its size
predicts. Two analyses (``double_jeopardy``, ``share_vs_growth``) take numbers
instead of a file. Every result carries the window, the rows used, what was blank
or unreadable, warnings about small bases, and one sentence a marketer can quote.
"""

from __future__ import annotations

import json
import math
import statistics
from datetime import date, timedelta
from typing import Any

from rocky.funnel import _column, _fmt, _load_rows, _num, _parse_count, _parse_date, _pct
from rocky.tools import ToolError, WorkspaceTools

PERIODS = ("week", "month", "quarter", "year")
DEFAULT_MIN_BUYERS = 30
DEFAULT_BINS: list[Any] = [1, 2, 3, 4, "5+"]
DEFAULT_BASELINE_WINDOW = 8
DEFAULT_SPIKE_FACTOR = 1.5
DEFAULT_TOLERANCE_PCT = 5.0
INTERMITTENT_PCT = 10.0
DEFINITIONS_DIFFER_POINTS = 5.0
FILE_ANALYSES = ("summary", "frequency", "repeat", "skip_return", "cohorts", "entrants_leavers", "duplication", "spikes")
NUMERIC_ANALYSES = ("double_jeopardy", "share_vs_growth")
ANALYSES = FILE_ANALYSES + NUMERIC_ANALYSES

_SYNONYMS = {
    "customer": ("customer", "customer_id", "customerid", "client", "client_id", "household", "household_id", "user_id", "user", "buyer", "id"),
    "date": ("date", "order_date", "purchase_date", "day", "datetime", "timestamp"),
    "amount": ("amount", "revenue", "sales", "value", "total", "price", "net", "eur"),
    "order_id": ("order_id", "orderid", "order", "transaction_id", "transaction", "receipt", "basket_id"),
    "sku": ("sku", "product", "product_id", "item", "article"),
    "brand": ("brand", "brand_name", "manufacturer", "label"),
    "units": ("units", "qty", "quantity", "pieces", "count"),
}


# --- small helpers -----------------------------------------------------------


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _parse_amount(value: Any) -> tuple[float | None, str]:
    """Like funnel's lenient number parsing, but refunds (negatives) are allowed."""
    if isinstance(value, bool):
        return (1.0 if value else 0.0), "ok"
    if isinstance(value, (int, float)):
        if value != value or value in (float("inf"), float("-inf")):
            return None, "bad"
        return float(value), "ok"
    text = "" if value is None else str(value).strip()
    if not text:
        return None, "blank"
    sign = 1.0
    if text[0] in "-−":
        sign, text = -1.0, text[1:].strip()
    number, status = _parse_count(text)
    return (None if number is None else sign * number), status


def _parse_iso(args: dict, key: str) -> date | None:
    raw = args.get(key)
    if raw in (None, ""):
        return None
    parsed = _parse_date(raw)
    if parsed is None:
        raise ToolError(f"{key} {raw!r} is not a date; use YYYY-MM-DD")
    return parsed


def _int_arg(args: dict, key: str, default: int, minimum: int = 1) -> int:
    raw = args.get(key)
    if raw in (None, ""):
        return default
    try:
        value = int(float(raw))
    except (TypeError, ValueError) as exc:
        raise ToolError(f"{key} must be a whole number") from exc
    if value < minimum:
        raise ToolError(f"{key} must be at least {minimum}")
    return value


def _float_arg(args: dict, key: str, default: float | None) -> float | None:
    raw = args.get(key)
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ToolError(f"{key} must be a number") from exc


def _period_arg(args: dict, default: str = "month") -> str:
    period = str(args.get("period") or default).strip().lower()
    if period not in PERIODS:
        raise ToolError(f"period must be one of {', '.join(PERIODS)}")
    return period


def _median(values: list[float]) -> float | None:
    return None if not values else float(statistics.median(values))


def _quartiles(values: list[float]) -> tuple[float | None, float | None]:
    if len(values) < 2:
        return (values[0], values[0]) if values else (None, None)
    q = statistics.quantiles(values, n=4, method="inclusive")
    return float(q[0]), float(q[2])


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _g(value: float | None) -> str:
    """A number for a sentence, or n/a when there was no denominator."""
    return "n/a" if value is None else f"{value:g}"


# --- periods -----------------------------------------------------------------


def _period_start(d: date, period: str) -> date:
    if period == "week":
        return d - timedelta(days=d.weekday())
    if period == "month":
        return d.replace(day=1)
    if period == "quarter":
        return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)
    return date(d.year, 1, 1)


def _period_label(start: date, period: str) -> str:
    if period == "week":
        iso = start.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    if period == "month":
        return start.strftime("%Y-%m")
    if period == "quarter":
        return f"{start.year}-Q{(start.month - 1) // 3 + 1}"
    return str(start.year)


def _next_period(start: date, period: str) -> date:
    if period == "week":
        return start + timedelta(days=7)
    if period == "month":
        return date(start.year + (start.month // 12), start.month % 12 + 1, 1)
    if period == "quarter":
        month = start.month + 3
        return date(start.year + (month - 1) // 12, (month - 1) % 12 + 1, 1)
    return date(start.year + 1, 1, 1)


def _period_range(first: date, last: date, period: str) -> list[date]:
    """Every period start from the one holding ``first`` to the one holding ``last``, gaps included."""
    starts = []
    cursor, end = _period_start(first, period), _period_start(last, period)
    while cursor <= end:
        starts.append(cursor)
        cursor = _next_period(cursor, period)
    return starts


# --- the transaction frame ---------------------------------------------------


class _Frame:
    """Purchases built from the file rows inside the window, with the bookkeeping every analysis reports."""

    def __init__(self) -> None:
        self.purchases: list[dict[str, Any]] = []
        self.by_customer: dict[str, list[dict[str, Any]]] = {}
        self.brand_purchases: dict[str, dict[str, int]] = {}
        self.window: dict[str, Any] = {}
        self.missing: list[str] = []
        self.rows_used = 0
        self.has_amount = False
        self.has_units = False
        self.has_brand = False
        self.has_order_id = False
        self.columns: dict[str, str | None] = {}

    @property
    def buyers(self) -> int:
        return len(self.by_customer)

    def revenue(self, purchases: list[dict[str, Any]] | None = None) -> float | None:
        if not self.has_amount:
            return None
        return sum(p["amount"] or 0.0 for p in (self.purchases if purchases is None else purchases))

    def dates(self) -> tuple[date, date] | None:
        if not self.purchases:
            return None
        return self.purchases[0]["date"], self.purchases[-1]["date"]

    def buyers_by_period(self, period: str) -> tuple[list[str], dict[str, set[str]], dict[str, list[dict[str, Any]]]]:
        """Ordered period labels (gaps included), the buyer set and the purchases of each."""
        span = self.dates()
        if span is None:
            return [], {}, {}
        labels = [_period_label(s, period) for s in _period_range(span[0], span[1], period)]
        sets: dict[str, set[str]] = {label: set() for label in labels}
        groups: dict[str, list[dict[str, Any]]] = {label: [] for label in labels}
        for p in self.purchases:
            label = _period_label(_period_start(p["date"], period), period)
            sets[label].add(p["customer"])
            groups[label].append(p)
        return labels, sets, groups


def _pick(columns: dict[str, Any], header: list[str], role: str, required: bool) -> str | None:
    given = columns.get(role)
    if given not in (None, ""):
        return _column(given, header, f"columns.{role}")
    folded = {h.strip().lower(): h for h in header}
    for name in _SYNONYMS[role]:
        if name in folded:
            return folded[name]
    if required:
        raise ToolError(f"columns.{role} is required (no column named like {role} found); columns found: {', '.join(header) or '(none)'}")
    return None


def _columns_arg(args: dict) -> dict[str, Any]:
    raw = args.get("columns") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise ToolError("columns must be an object like {customer: ..., date: ...}") from exc
    if not isinstance(raw, dict):
        raise ToolError("columns must be an object like {customer: ..., date: ...}")
    return {str(k).strip().lower(): v for k, v in raw.items()}


def _load_frame(tools: WorkspaceTools, args: dict, need_customer: bool = True) -> _Frame:
    path = str(args.get("path") or "").strip()
    if not path:
        raise ToolError("path is required: a CSV or XLSX transaction file inside the workspace")
    header, rows = _load_rows(tools, path, args.get("sheet"))
    columns = _columns_arg(args)
    frame = _Frame()
    cols = {
        "customer": _pick(columns, header, "customer", need_customer),
        "date": _pick(columns, header, "date", True),
        "amount": _pick(columns, header, "amount", False),
        "order_id": _pick(columns, header, "order_id", False),
        "sku": _pick(columns, header, "sku", False),
        "brand": _pick(columns, header, "brand", False),
        "units": _pick(columns, header, "units", False),
    }
    frame.columns = cols
    frame.has_amount = cols["amount"] is not None
    frame.has_units = cols["units"] is not None
    frame.has_brand = cols["brand"] is not None
    frame.has_order_id = cols["order_id"] is not None
    date_from, date_to = _parse_iso(args, "date_from"), _parse_iso(args, "date_to")
    if date_from and date_to and date_from > date_to:
        raise ToolError("date_from is after date_to")
    sku_filter = args.get("sku")
    sku_filter = None if sku_filter in (None, "") else str(sku_filter).strip().lower()
    if sku_filter and cols["sku"] is None:
        raise ToolError("a sku filter needs columns.sku")

    blank_customer = bad_date = out_of_window = bad_amount = blank_amount = bad_units = 0
    purchases: dict[tuple[str, Any], dict[str, Any]] = {}
    for row in rows:
        customer = row.get(cols["customer"]) if cols["customer"] else "(all)"
        customer = "" if customer is None else str(customer).strip()
        if not customer:
            blank_customer += 1
            continue
        d = _parse_date(row.get(cols["date"]))
        if d is None:
            bad_date += 1
            continue
        if (date_from and d < date_from) or (date_to and d > date_to):
            out_of_window += 1
            continue
        if sku_filter:
            sku = row.get(cols["sku"])
            if sku is None or str(sku).strip().lower() != sku_filter:
                continue
        amount: float | None = None
        if frame.has_amount:
            amount, status = _parse_amount(row.get(cols["amount"]))
            if status == "bad":
                bad_amount += 1
                continue
            if status == "blank":
                blank_amount += 1
        units: float | None = None
        if frame.has_units:
            units, status = _parse_count(row.get(cols["units"]))
            if status == "bad":
                bad_units += 1
                units = None
        frame.rows_used += 1
        if frame.has_order_id and row.get(cols["order_id"]) not in (None, ""):
            key = (customer, str(row.get(cols["order_id"])).strip())
        else:
            key = (customer, d)
        purchase = purchases.get(key)
        if purchase is None:
            purchase = {"customer": customer, "date": d, "amount": 0.0 if frame.has_amount else None, "units": 0.0, "brands": set(), "rows": 0}
            purchases[key] = purchase
        purchase["rows"] += 1
        purchase["date"] = min(purchase["date"], d)
        if amount is not None:
            purchase["amount"] = (purchase["amount"] or 0.0) + amount
        if units is not None:
            purchase["units"] += units
        if frame.has_brand:
            brand = row.get(cols["brand"])
            brand = "" if brand is None else str(brand).strip()
            if brand and brand not in purchase["brands"]:
                purchase["brands"].add(brand)
                counter = frame.brand_purchases.setdefault(customer, {})
                counter[brand] = counter.get(brand, 0) + 1

    frame.purchases = sorted(purchases.values(), key=lambda p: (p["date"], p["customer"]))
    for p in frame.purchases:
        frame.by_customer.setdefault(p["customer"], []).append(p)
    span = frame.dates()
    frame.window = {
        "from": span[0].isoformat() if span else None,
        "to": span[1].isoformat() if span else None,
        "requested_from": date_from.isoformat() if date_from else None,
        "requested_to": date_to.isoformat() if date_to else None,
        "rows_total": len(rows),
        "rows_used": frame.rows_used,
        "purchases": len(frame.purchases),
    }
    m = frame.missing
    if blank_customer:
        m.append(f"{_plural(blank_customer, 'row')} with a blank {cols['customer']} left out.")
    if bad_date:
        m.append(f"{_plural(bad_date, 'row')} without a readable date in {cols['date']} left out.")
    if bad_amount:
        m.append(f"{_plural(bad_amount, 'row')} dropped because {cols['amount']} was not a number.")
    if blank_amount:
        m.append(f"{cols['amount']}: {_plural(blank_amount, 'blank cell')} counted as 0.")
    if bad_units:
        m.append(f"{cols['units']}: {_plural(bad_units, 'cell')} not a number, counted as 0 units.")
    if out_of_window:
        m.append(f"{_plural(out_of_window, 'row')} outside {frame.window['requested_from'] or 'the start'}–{frame.window['requested_to'] or 'the end'} left out.")
    if not rows:
        m.append("The file has a header but no data rows.")
    elif not frame.purchases:
        m.append("No usable rows in the window: nothing to count.")
    return frame


# --- shared result shape -----------------------------------------------------


def _base_result(analysis: str, frame: _Frame | None, period: str | None, method: str) -> dict[str, Any]:
    out: dict[str, Any] = {"analysis": analysis}
    if frame is not None:
        out["window"] = frame.window
        out["rows_used"] = frame.rows_used
        out["columns"] = frame.columns
        out["missing"] = list(frame.missing)
    else:
        out["window"] = None
        out["rows_used"] = 0
        out["missing"] = []
    if period:
        out["period"] = period
    out["warnings"] = []
    out["flags"] = []
    out["method"] = method
    return out


def _flag(out: dict[str, Any], flag: str, text: str) -> None:
    if flag not in out["flags"]:
        out["flags"].append(flag)
    out["warnings"].append(f"{flag}: {text}")


def _check_base(out: dict[str, Any], buyers: int, min_buyers: int, what: str = "buyers") -> None:
    if 0 < buyers < min_buyers:
        _flag(out, "small_base", f"only {buyers} {what} (fewer than {min_buyers}); percentages are rough.")


def _universe(args: dict, out: dict[str, Any]) -> tuple[int | None, str | None]:
    universe = args.get("universe")
    if universe in (None, ""):
        return None, None
    try:
        n = int(float(universe))
    except (TypeError, ValueError) as exc:
        raise ToolError("universe must be a whole number of households or customers") from exc
    if n <= 0:
        raise ToolError("universe must be positive")
    source = args.get("universe_source")
    source = None if source in (None, "") else str(source).strip()
    if source is None:
        _flag(out, "universe_source_missing", "universe was given without universe_source; name where the household count comes from.")
    return n, source


def _single_period(out: dict[str, Any], labels: list[str]) -> None:
    if len(labels) < 2:
        _flag(out, "single_period", "the data covers one period; period-to-period figures cannot be computed.")


# --- analyses on the file ----------------------------------------------------


def _summary_analysis(frame: _Frame, args: dict, period: str, min_buyers: int) -> dict[str, Any]:
    out = _base_result(
        "summary", frame, period,
        "A purchase is one distinct customer+order_id (customer+date without order_id). Buyers are distinct customer ids in the window.",
    )
    buyers, purchases = frame.buyers, len(frame.purchases)
    revenue = frame.revenue()
    once = sum(1 for ps in frame.by_customer.values() if len(ps) == 1)
    labels, sets, groups = frame.buyers_by_period(period)
    per_period = []
    for label in labels:
        ps = groups[label]
        row = {"period": label, "buyers": len(sets[label]), "purchases": len(ps), "purchases_per_buyer": _round(len(ps) / len(sets[label])) if sets[label] else None}
        if frame.has_amount:
            row["revenue"] = _num(frame.revenue(ps) or 0.0)
        if frame.has_units:
            row["units"] = _num(sum(p["units"] for p in ps))
        per_period.append(row)
    out.update(
        {
            "rows": frame.rows_used,
            "buyers": buyers,
            "purchases": purchases,
            "purchases_per_buyer": _round(purchases / buyers) if buyers else None,
            "revenue": None if revenue is None else _num(revenue),
            "revenue_per_buyer": None if revenue is None or not buyers else _round(revenue / buyers),
            "revenue_per_purchase": None if revenue is None or not purchases else _round(revenue / purchases),
            "units": _num(sum(p["units"] for p in frame.purchases)) if frame.has_units else None,
            "buyers_with_one_purchase": once,
            "buyers_with_one_purchase_pct": _pct(once, buyers),
            "per_period": per_period,
        }
    )
    _check_base(out, buyers, min_buyers)
    if buyers:
        line = f"Between {frame.window['from']} and {frame.window['to']}: {_fmt(buyers)} buyers made {_fmt(purchases)} purchases ({_g(out['purchases_per_buyer'])} per buyer)"
        if revenue is not None:
            line += f", revenue {_fmt(_num(revenue))} ({_g(out['revenue_per_buyer'])} per buyer)"
        line += f"; {_g(out['buyers_with_one_purchase_pct'])} % of buyers bought once."
    else:
        line = "No usable purchases in the window."
    out["summary"] = line
    return out


def _bins_arg(args: dict) -> list[Any]:
    raw = args.get("bins")
    if raw in (None, ""):
        return list(DEFAULT_BINS)
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ToolError("bins must be a list like [1, 2, 3, 4, '5+']")
    bins: list[Any] = []
    for i, b in enumerate(raw):
        text = str(b).strip()
        if text.endswith("+"):
            if i != len(raw) - 1:
                raise ToolError("only the last bin may be open-ended like '5+'")
            try:
                bins.append(f"{int(text[:-1])}+")
            except ValueError as exc:
                raise ToolError(f"bin {b!r} is not a number") from exc
        else:
            try:
                bins.append(int(text))
            except ValueError as exc:
                raise ToolError(f"bin {b!r} is not a number") from exc
    closed = [b for b in bins if isinstance(b, int)]
    if closed != sorted(closed) or len(set(closed)) != len(closed):
        raise ToolError("bins must be increasing")
    if not isinstance(bins[-1], str):
        bins.append(f"{bins[-1] + 1}+")
    return bins


def _frequency_analysis(frame: _Frame, args: dict, min_buyers: int) -> dict[str, Any]:
    bins = _bins_arg(args)
    out = _base_result(
        "frequency", frame, None,
        "Purchases per buyer in the window, bucketed; each bin shows its share of buyers, of purchases and of revenue, so light buyers' weight in sales is read off the table.",
    )
    buyers, purchases, revenue = frame.buyers, len(frame.purchases), frame.revenue()
    open_from = int(bins[-1][:-1])
    table = []
    for b in bins:
        if isinstance(b, int):
            members = [ps for ps in frame.by_customer.values() if len(ps) == b]
        else:
            members = [ps for ps in frame.by_customer.values() if len(ps) >= open_from]
        n_purchases = sum(len(ps) for ps in members)
        rev = sum(p["amount"] or 0.0 for ps in members for p in ps) if frame.has_amount else None
        table.append(
            {
                "purchases": str(b),
                "buyers": len(members),
                "buyers_pct": _pct(len(members), buyers),
                "purchases_count": n_purchases,
                "purchases_pct": _pct(n_purchases, purchases),
                "revenue": None if rev is None else _num(rev),
                "revenue_pct": None if rev is None or revenue is None else _pct(rev, revenue),
            }
        )
    unbinned = buyers - sum(r["buyers"] for r in table)
    if unbinned:
        out["warnings"].append(f"{unbinned} buyers fall between the bins given and are not shown.")
    out.update({"buyers": buyers, "purchases": purchases, "revenue": None if revenue is None else _num(revenue), "table": table})
    _check_base(out, buyers, min_buyers)
    light, heavy = table[0], table[-1]
    sales_word = "sales" if frame.has_amount else "purchases"
    light_share = light["revenue_pct"] if frame.has_amount else light["purchases_pct"]
    heavy_share = heavy["revenue_pct"] if frame.has_amount else heavy["purchases_pct"]
    if buyers:
        out["light_buyers"] = f"{_g(light['buyers_pct'])} % of buyers bought {light['purchases']} time{'s' if light['purchases'] != '1' else ''} and brought {_g(light_share)} % of {sales_word}."
        out["heavy_buyers"] = f"{_g(heavy['buyers_pct'])} % of buyers bought {heavy['purchases']} times and brought {_g(heavy_share)} % of {sales_word}."
        out["summary"] = f"{_fmt(buyers)} buyers, {_fmt(purchases)} purchases: {out['light_buyers'][:-1]}; {out['heavy_buyers'][0].lower()}{out['heavy_buyers'][1:]}"
    else:
        out["light_buyers"] = out["heavy_buyers"] = None
        out["summary"] = "No usable purchases in the window."
    return out


def _window_arg(args: dict, key: str) -> tuple[date | None, date | None] | None:
    raw = args.get(key)
    if raw in (None, "", {}):
        return None
    if not isinstance(raw, dict):
        raise ToolError(f"{key} must be an object {{from, to}} with ISO dates")
    w_from, w_to = _parse_iso(raw, "from"), _parse_iso(raw, "to")
    if w_from and w_to and w_from > w_to:
        raise ToolError(f"{key}.from is after {key}.to")
    return w_from, w_to


def _in(d: date, w: tuple[date | None, date | None]) -> bool:
    return (w[0] is None or d >= w[0]) and (w[1] is None or d <= w[1])


def _repeat_analysis(frame: _Frame, args: dict, period: str, min_buyers: int) -> dict[str, Any]:
    out = _base_result(
        "repeat", frame, period,
        "Definition A: share of buyers in the window with 2+ purchases. Definition B: share of period-1 buyers who also bought in period 2 "
        "(window_a/window_b, or the first and second half of the periods). Intervals are days between a buyer's consecutive purchase dates. "
        "repeat_rate_at N days counts first-time buyers whose first purchase is at least N days before the window end and who bought again within N days.",
    )
    span = frame.dates()
    if span is None:
        out["summary"] = "No usable purchases in the window."
        out.update({"definition_a": None, "definition_b": None, "intervals": None, "repeat_rate_at": {}, "definitions_differ": None})
        return out
    window_a, window_b = _window_arg(args, "window_a"), _window_arg(args, "window_b")

    # Definition A
    wa = window_a or (span[0], span[1])
    counts_a = {c: sum(1 for p in ps if _in(p["date"], wa)) for c, ps in frame.by_customer.items()}
    buyers_a = sum(1 for n in counts_a.values() if n > 0)
    repeaters_a = sum(1 for n in counts_a.values() if n >= 2)
    definition_a = {
        "window": {"from": (wa[0] or span[0]).isoformat(), "to": (wa[1] or span[1]).isoformat()},
        "buyers": buyers_a,
        "repeaters": repeaters_a,
        "rate_pct": _pct(repeaters_a, buyers_a),
    }
    _check_base(out, buyers_a, min_buyers, "buyers in definition A")

    # Definition B
    definition_b: dict[str, Any] | None
    labels, sets, _groups = frame.buyers_by_period(period)
    if window_a and window_b:
        p1, p2 = window_a, window_b
        p1_desc = {"from": (p1[0] or span[0]).isoformat(), "to": (p1[1] or span[1]).isoformat()}
        p2_desc = {"from": (p2[0] or span[0]).isoformat(), "to": (p2[1] or span[1]).isoformat()}
    elif window_b:
        p2 = window_b
        p1 = (span[0], (window_b[0] - timedelta(days=1)) if window_b[0] else None)
        p1_desc = {"from": span[0].isoformat(), "to": p1[1].isoformat() if p1[1] else None}
        p2_desc = {"from": (p2[0] or span[0]).isoformat(), "to": (p2[1] or span[1]).isoformat()}
    elif len(labels) >= 2:
        half = len(labels) // 2
        starts = _period_range(span[0], span[1], period)
        p1 = (starts[0], starts[half] - timedelta(days=1))
        p2 = (starts[half], span[1])
        p1_desc = {"from": p1[0].isoformat(), "to": p1[1].isoformat(), "periods": labels[:half]}
        p2_desc = {"from": p2[0].isoformat(), "to": p2[1].isoformat(), "periods": labels[half:]}
    else:
        p1 = p2 = None  # type: ignore[assignment]
        p1_desc = p2_desc = {}
    if p1 is None:
        definition_b = None
        out["warnings"].append("definition B needs two periods or window_a and window_b; the data covers one period.")
    else:
        p1_buyers = {c for c, ps in frame.by_customer.items() if any(_in(p["date"], p1) for p in ps)}
        also = {c for c in p1_buyers if any(_in(p["date"], p2) for p in frame.by_customer[c])}
        definition_b = {
            "period1": p1_desc,
            "period2": p2_desc,
            "period1_buyers": len(p1_buyers),
            "also_bought_period2": len(also),
            "rate_pct": _pct(len(also), len(p1_buyers)),
        }
        _check_base(out, len(p1_buyers), min_buyers, "buyers in period 1 of definition B")

    # Intervals
    gaps: list[float] = []
    repeaters = 0
    for ps in frame.by_customer.values():
        days = sorted({p["date"] for p in ps})
        if len(days) >= 2:
            repeaters += 1
            gaps.extend(float((b - a).days) for a, b in zip(days, days[1:]))
    median = _median(gaps)
    p25, p75 = _quartiles(gaps)
    pack_life = _float_arg(args, "pack_life_days", None)
    recommended = None
    if median is not None:
        recommended = int(math.ceil(max(median, pack_life or 0)))
    intervals = {
        "n_repeaters": repeaters,
        "n_intervals": len(gaps),
        "median_days": _round(median),
        "p25_days": _round(p25),
        "p75_days": _round(p75),
        "pack_life_days": pack_life,
        "recommended_window_days": recommended,
    }
    if repeaters and repeaters < min_buyers:
        _flag(out, "small_base", f"only {repeaters} repeat buyers behind the interval figures (fewer than {min_buyers}).")
    if not gaps:
        out["warnings"].append("no buyer bought on two different dates: intervals and repeat_rate_at cannot be computed.")

    # Repeat rate at N days
    rate_at: dict[str, Any] = {}
    windows = [("30", 30), ("90", 90)]
    if recommended is not None:
        windows.append(("recommended", recommended))
    for key, n in windows:
        cutoff = span[1] - timedelta(days=n)
        base = [ps for ps in frame.by_customer.values() if ps[0]["date"] <= cutoff]
        repeated = sum(1 for ps in base if any(0 < (p["date"] - ps[0]["date"]).days <= n for p in ps[1:]))
        rate_at[key] = {"days": n, "base_buyers": len(base), "repeated": repeated, "rate_pct": _pct(repeated, len(base))}
        if not base:
            out["warnings"].append(f"repeat_rate_at {n} days: no buyer's first purchase is {n} days before the window end.")

    differ = None
    explanation = None
    if definition_b is not None and definition_a["rate_pct"] is not None and definition_b["rate_pct"] is not None:
        diff = definition_a["rate_pct"] - definition_b["rate_pct"]
        differ = abs(diff) >= DEFINITIONS_DIFFER_POINTS
        explanation = (
            f"Definition A (2+ purchases in the window) gives {definition_a['rate_pct']:g} %, definition B (bought in period 1 and again in period 2) gives "
            f"{definition_b['rate_pct']:g} %: {abs(diff):.1f} points apart. "
            + ("Say which one you mean before quoting a repeat rate." if differ else "The two definitions agree within 5 points here.")
        )
    out.update(
        {
            "definition_a": definition_a,
            "definition_b": definition_b,
            "definitions_differ": differ,
            "explanation": explanation,
            "intervals": intervals,
            "repeat_rate_at": rate_at,
        }
    )
    line = f"Repeat rate: {_g(definition_a['rate_pct'])} % of {_fmt(buyers_a)} buyers bought 2+ times (definition A)"
    if definition_b is not None and definition_b["rate_pct"] is not None:
        line += f"; {definition_b['rate_pct']:g} % of period-1 buyers bought again in period 2 (definition B)"
    if median is not None:
        line += f"; median {median:g} days between purchases, so a repeat window shorter than {recommended} days undercounts."
    else:
        line += "."
    out["summary"] = line
    return out


def _skip_return_analysis(frame: _Frame, args: dict, period: str, min_buyers: int) -> dict[str, Any]:
    threshold = _float_arg(args, "intermittent_threshold_pct", INTERMITTENT_PCT) or INTERMITTENT_PCT
    out = _base_result(
        "skip_return", frame, period,
        f"For each base period P with two later periods in the data: share of P's buyers who bought in P+1, in P+2, who skipped P+1 and bought in P+2, "
        f"and who bought in neither. intermittent is true when the skip-then-return share averages {threshold:g} % or more.",
    )
    labels, sets, _groups = frame.buyers_by_period(period)
    _single_period(out, labels)
    table = []
    for i in range(len(labels) - 2):
        base, nxt, after = sets[labels[i]], sets[labels[i + 1]], sets[labels[i + 2]]
        n = len(base)
        r1 = len(base & nxt)
        r2 = len(base & after)
        skip = len((base - nxt) & after)
        lost = len(base - nxt - after)
        table.append(
            {
                "period": labels[i],
                "buyers": n,
                "returned_next_period": r1,
                "returned_next_pct": _pct(r1, n),
                "returned_period_plus_2": r2,
                "returned_plus_2_pct": _pct(r2, n),
                "skip_then_return": skip,
                "skip_then_return_pct": _pct(skip, n),
                "lost_two_periods": lost,
                "lost_two_periods_pct": _pct(lost, n),
            }
        )
        _check_base(out, n, min_buyers, f"buyers in {labels[i]}")
    skip_shares = [r["skip_then_return_pct"] for r in table if r["skip_then_return_pct"] is not None]
    mean_skip = _round(sum(skip_shares) / len(skip_shares), 1) if skip_shares else None
    lost_shares = [r["lost_two_periods_pct"] for r in table if r["lost_two_periods_pct"] is not None]
    mean_lost = _round(sum(lost_shares) / len(lost_shares), 1) if lost_shares else None
    out.update(
        {
            "periods": table,
            "mean_skip_then_return_pct": mean_skip,
            "mean_lost_two_periods_pct": mean_lost,
            "intermittent_threshold_pct": threshold,
            "intermittent": None if mean_skip is None else mean_skip >= threshold,
        }
    )
    if len(labels) < 3:
        out["warnings"].append("skip_return needs at least three periods; nothing to compare.")
        out["summary"] = "Too few periods to tell skipping from leaving."
    else:
        out["summary"] = (
            f"On average {_g(mean_skip)} % of a {period}'s buyers skip the next {period} and buy again the one after, and {_g(mean_lost)} % buy in neither; "
            + ("buying is intermittent: a one-period absence is not churn." if out["intermittent"] else "a one-period absence mostly stays absent in this data.")
        )
    return out


def _cohorts_analysis(frame: _Frame, args: dict, period: str, min_buyers: int) -> dict[str, Any]:
    out = _base_result(
        "cohorts", frame, period,
        "A cohort is every buyer whose first purchase in the file falls in the period; retained counts the cohort's buyers who bought in each later period. "
        "baseline compares mean buyers per period in the baseline window with the test window and splits test-window buyers into new (first seen there) and returning.",
    )
    labels, sets, groups = frame.buyers_by_period(period)
    _single_period(out, labels)
    first_period = {c: _period_label(_period_start(ps[0]["date"], period), period) for c, ps in frame.by_customer.items()}
    cohorts = []
    for i, label in enumerate(labels):
        members = {c for c, fp in first_period.items() if fp == label}
        if not members:
            continue
        retained = []
        for later in labels[i + 1 :]:
            kept = len(members & sets[later])
            retained.append({"period": later, "buyers": kept, "pct": _pct(kept, len(members))})
        revenue = sum(p["amount"] or 0.0 for c in members for p in frame.by_customer[c]) if frame.has_amount else None
        cohorts.append(
            {
                "cohort": label,
                "buyers": len(members),
                "revenue": None if revenue is None else _num(revenue),
                "revenue_per_buyer": None if revenue is None else _round(revenue / len(members)),
                "retained": retained,
            }
        )
        _check_base(out, len(members), min_buyers, f"buyers in the {label} cohort")
    out["cohorts"] = cohorts

    baseline_block: dict[str, Any] | None = None
    b_from, b_to = _parse_iso(args, "baseline_from"), _parse_iso(args, "baseline_to")
    t_from, t_to = _parse_iso(args, "test_from"), _parse_iso(args, "test_to")
    if any(x is not None for x in (b_from, b_to, t_from, t_to)):
        if not (b_from and b_to and t_from and t_to):
            raise ToolError("baseline needs all four dates: baseline_from, baseline_to, test_from, test_to")
        if b_from > b_to or t_from > t_to:
            raise ToolError("a baseline or test window has from after to")
        min_periods = _int_arg(args, "min_baseline_periods", 4)
        b_labels = [_period_label(s, period) for s in _period_range(b_from, b_to, period)]
        t_labels = [_period_label(s, period) for s in _period_range(t_from, t_to, period)]
        b_counts = [len(sets.get(label, set())) for label in b_labels]
        t_counts = [len(sets.get(label, set())) for label in t_labels]
        b_mean = sum(b_counts) / len(b_counts) if b_counts else None
        t_mean = sum(t_counts) / len(t_counts) if t_counts else None
        test_buyers = {c for c, ps in frame.by_customer.items() if any(t_from <= p["date"] <= t_to for p in ps)}
        new_in_test = {c for c in test_buyers if frame.by_customer[c][0]["date"] >= t_from}
        returning = test_buyers - new_in_test
        b_rev = sum(p["amount"] or 0.0 for p in frame.purchases if b_from <= p["date"] <= b_to) if frame.has_amount else None
        t_rev = sum(p["amount"] or 0.0 for p in frame.purchases if t_from <= p["date"] <= t_to) if frame.has_amount else None
        baseline_block = {
            "baseline": {"from": b_from.isoformat(), "to": b_to.isoformat(), "periods": len(b_labels), "buyers_per_period": [{"period": l, "buyers": n} for l, n in zip(b_labels, b_counts)], "mean_buyers_per_period": _round(b_mean), "revenue": None if b_rev is None else _num(b_rev)},
            "test": {"from": t_from.isoformat(), "to": t_to.isoformat(), "periods": len(t_labels), "buyers_per_period": [{"period": l, "buyers": n} for l, n in zip(t_labels, t_counts)], "mean_buyers_per_period": _round(t_mean), "revenue": None if t_rev is None else _num(t_rev)},
            "change_pct": None if not b_mean or t_mean is None else round((t_mean - b_mean) / b_mean * 100, 1),
            "test_buyers": len(test_buyers),
            "test_new_buyers": len(new_in_test),
            "test_new_pct": _pct(len(new_in_test), len(test_buyers)),
            "test_returning_buyers": len(returning),
            "test_returning_pct": _pct(len(returning), len(test_buyers)),
        }
        if len(b_labels) < min_periods:
            _flag(out, "short_baseline", f"the baseline covers {len(b_labels)} {period}s, fewer than {min_periods}; its mean is unstable.")
        if b_mean is not None and t_mean is not None and b_mean > 0 and t_mean >= b_mean and baseline_block["test_new_pct"] is not None and baseline_block["test_new_pct"] >= 50:
            out["warnings"].append("buyers per period held up only because most test-window buyers are new: trial is masking attrition among earlier buyers.")
    out["baseline"] = baseline_block

    if cohorts:
        first = cohorts[0]
        line = f"{len(cohorts)} cohorts by first-purchase {period}; the {first['cohort']} cohort of {_fmt(first['buyers'])} buyers"
        if first["retained"]:
            last = first["retained"][-1]
            line += f" had {last['buyers']} ({_g(last['pct'])} %) still buying in {last['period']}."
        else:
            line += " has no later period yet."
        if baseline_block and baseline_block["change_pct"] is not None:
            line += (
                f" Buyers per {period}: {baseline_block['baseline']['mean_buyers_per_period']:g} in the baseline vs {baseline_block['test']['mean_buyers_per_period']:g} in the test window "
                f"({baseline_block['change_pct']:+g} %); {_g(baseline_block['test_new_pct'])} % of test-window buyers were new."
            )
    else:
        line = "No usable purchases in the window."
    out["summary"] = line
    return out


def _entrants_leavers_analysis(frame: _Frame, args: dict, period: str, min_buyers: int) -> dict[str, Any]:
    out = _base_result(
        "entrants_leavers", frame, period,
        "Per period: distinct buyers; new = first purchase in the file falls in the period; lapsed = bought in the previous period but not this one; "
        "returned_after_gap = bought this period, not the previous one, but earlier. cumulative counts distinct buyers to date; double_count is the sum of period buyers minus distinct buyers.",
    )
    universe, source = _universe(args, out)
    labels, sets, groups = frame.buyers_by_period(period)
    _single_period(out, labels)
    seen: set[str] = set()
    prev: set[str] = set()
    periods, cumulative = [], []
    for i, label in enumerate(labels):
        now = sets[label]
        new = now - seen
        repeat_prev = now & prev
        returned = now - prev - new
        lapsed = prev - now
        seen |= now
        row = {
            "period": label,
            "buyers": len(now),
            "purchases": len(groups[label]),
            "new": len(new),
            "new_pct": _pct(len(new), len(now)),
            "repeat_from_previous": len(repeat_prev),
            "returned_after_gap": len(returned),
            "lapsed_from_previous": len(lapsed) if i else None,
            "lapsed_pct_of_previous": _pct(len(lapsed), len(prev)) if i else None,
        }
        if frame.has_amount:
            row["revenue"] = _num(frame.revenue(groups[label]) or 0.0)
        periods.append(row)
        cumulative.append({"period": label, "distinct_buyers_to_date": len(seen), "penetration_to_date_pct": _pct(len(seen), universe) if universe else None})
        _check_base(out, len(now), min_buyers, f"buyers in {label}")
        prev = now
    total = sum(len(s) for s in sets.values())
    out.update(
        {
            "periods": periods,
            "cumulative": cumulative,
            "distinct_buyers": frame.buyers,
            "sum_of_period_buyers": total,
            "double_count": total - frame.buyers,
            "universe": universe,
            "universe_source": source,
            "penetration_pct": _pct(frame.buyers, universe) if universe else None,
        }
    )
    if universe is None:
        _flag(out, "universe_missing", "no universe given, so penetration is not computed; give universe (households or customers) and universe_source.")
    if periods:
        line = f"{_fmt(frame.buyers)} distinct buyers over {len(labels)} {period}s; period buyer counts add up to {_fmt(total)}, so {_fmt(total - frame.buyers)} would be double-counted by summing."
        if universe:
            line += f" Penetration to date {out['penetration_pct']:g} % of {_fmt(universe)}."
        if len(periods) > 1:
            last = periods[-1]
            line += f" In {last['period']}: {last['new']} new, {last['returned_after_gap']} back after a gap, {last['lapsed_from_previous']} lapsed."
    else:
        line = "No usable purchases in the window."
    out["summary"] = line
    return out


def _duplication_analysis(frame: _Frame, args: dict, min_buyers: int, focus: str | None) -> dict[str, Any]:
    out = _base_result(
        "duplication", frame, None,
        "Brand buyers are distinct customers with at least one purchase of the brand in the window. Penetration is of the universe when given, else of all buyers in the file. "
        "For each ordered pair, pct_of_a_buyers = buyers of both / buyers of A; ratio = that share / penetration of B (1.0 = B's buyers are spread as expected; duplication law). "
        "Repertoire is the number of distinct brands per buyer; share of requirements is the focus brand's share of its buyers' brand purchases.",
    )
    if not frame.has_brand:
        raise ToolError("duplication needs columns.brand: the column naming the brand of each row")
    universe, source = _universe(args, out)
    brand_buyers: dict[str, set[str]] = {}
    for customer, counter in frame.brand_purchases.items():
        for brand in counter:
            brand_buyers.setdefault(brand, set()).add(customer)
    if not brand_buyers:
        raise ToolError(f"no brand values in {frame.columns['brand']} inside the window")
    base = universe or frame.buyers
    base_desc = f"universe of {_fmt(universe)}" + (f" ({source})" if source else "") if universe else "all buyers in the file"
    names = sorted(brand_buyers, key=lambda b: (-len(brand_buyers[b]), b))
    brands = [{"name": b, "buyers": len(brand_buyers[b]), "penetration_pct": _pct(len(brand_buyers[b]), base)} for b in names]
    pen = {b["name"]: b["penetration_pct"] for b in brands}
    matrix = []
    ratios = []
    for a in names:
        for b in names:
            if a == b:
                continue
            shared = len(brand_buyers[a] & brand_buyers[b])
            share = _pct(shared, len(brand_buyers[a]))
            ratio = None if share is None or not pen[b] else round(share / pen[b], 2)
            label = None
            if ratio is not None:
                label = "as expected" if 0.8 <= ratio <= 1.2 else ("above expected" if ratio > 1.2 else "below expected")
                ratios.append(ratio)
            matrix.append({"brand_a": a, "brand_b": b, "shared_buyers": shared, "pct_of_a_buyers": share, "penetration_b_pct": pen[b], "ratio": ratio, "label": label})
    avg_ratio = _round(sum(ratios) / len(ratios)) if ratios else None

    sizes = [len(counter) for counter in frame.brand_purchases.values()]
    single = sum(1 for s in sizes if s == 1)
    repertoire: dict[str, Any] = {
        "buyers": len(sizes),
        "mean_distinct_brands": _round(sum(sizes) / len(sizes)) if sizes else None,
        "single_brand_buyers": single,
        "single_brand_pct": _pct(single, len(sizes)),
    }
    bands = [("1", 1, 1), ("2-3", 2, 3), ("4+", 4, None)]
    by_frequency = []
    for name, lo, hi in bands:
        members = {c: counter for c, counter in frame.brand_purchases.items() if lo <= sum(counter.values()) and (hi is None or sum(counter.values()) <= hi)}
        if not members:
            continue
        m_sizes = [len(c) for c in members.values()]
        focal_total = sum(c.get(focus, 0) for c in members.values()) if focus else None
        all_total = sum(sum(c.values()) for c in members.values())
        by_frequency.append(
            {
                "purchases": name,
                "buyers": len(members),
                "mean_distinct_brands": _round(sum(m_sizes) / len(m_sizes)),
                "single_brand_pct": _pct(sum(1 for s in m_sizes if s == 1), len(m_sizes)),
                "focus_share_of_requirements_pct": None if focal_total is None else _pct(focal_total, all_total),
            }
        )
    repertoire["by_frequency"] = by_frequency

    focus_block: dict[str, Any] | None = None
    if focus:
        match = next((b for b in names if b.lower() == focus.lower()), None)
        if match is None:
            raise ToolError(f"focus_brand {focus!r} is not in {frame.columns['brand']}; brands found: {', '.join(names)}")
        focus = match
        owners = brand_buyers[focus]
        focal = sum(frame.brand_purchases[c].get(focus, 0) for c in owners)
        total = sum(sum(frame.brand_purchases[c].values()) for c in owners)
        exclusive = sum(1 for c in owners if len(frame.brand_purchases[c]) == 1)
        focus_block = {
            "brand": focus,
            "buyers": len(owners),
            "penetration_pct": pen[focus],
            "share_of_requirements_pct": _pct(focal, total),
            "exclusive_buyers": exclusive,
            "exclusive_pct": _pct(exclusive, len(owners)),
            "also_buy": [{"brand": r["brand_b"], "pct_of_focus_buyers": r["pct_of_a_buyers"], "ratio": r["ratio"]} for r in matrix if r["brand_a"] == focus],
        }
        _check_base(out, len(owners), min_buyers, f"buyers of {focus}")
    out.update(
        {
            "penetration_base": base_desc,
            "base_count": base,
            "universe": universe,
            "universe_source": source,
            "brands": brands,
            "matrix": matrix,
            "average_duplication_coefficient": avg_ratio,
            "repertoire": repertoire,
            "focus": focus_block,
        }
    )
    _check_base(out, frame.buyers, min_buyers)
    for b in brands:
        if b["buyers"] < min_buyers:
            _flag(out, "small_base", f"{b['name']} has only {b['buyers']} buyers (fewer than {min_buyers}).")
    if len(names) == 1:
        _flag(out, "own_brand_only", "the file holds one brand; duplication needs the competitors' purchases too.")
    if universe is None:
        _flag(out, "universe_missing", "penetration is the share of buyers in this file, not of the market; give universe and universe_source for market penetration.")
    top = brands[0]
    line = f"{len(names)} brands among {_fmt(frame.buyers)} buyers; {top['name']} leads with {_g(top['penetration_pct'])} % penetration ({base_desc})."
    if focus_block:
        line += (
            f" {focus} buyers give it {_g(focus_block['share_of_requirements_pct'])} % of their brand purchases; {_g(focus_block['exclusive_pct'])} % buy it alone."
        )
    if avg_ratio is not None:
        line += f" Buyers hold {_g(repertoire['mean_distinct_brands'])} brands on average; duplication coefficient {avg_ratio:g}."
    out["summary"] = line
    return out


def _spikes_analysis(frame_rows: tuple[list[str], list[dict[str, Any]]], args: dict, period: str) -> dict[str, Any]:
    header, rows = frame_rows
    columns = _columns_arg(args)
    date_col = _pick(columns, header, "date", True)
    value_name = args.get("value_column") or columns.get("value") or columns.get("units") or columns.get("amount")
    if value_name in (None, ""):
        value_col = _pick(columns, header, "units", False) or _pick(columns, header, "amount", False)
        if value_col is None:
            raise ToolError(f"value_column is required (units or revenue per row); columns found: {', '.join(header)}")
    else:
        value_col = _column(value_name, header, "value_column")
    window_n = _int_arg(args, "baseline_window", DEFAULT_BASELINE_WINDOW, 2)
    factor = _float_arg(args, "spike_factor", None)
    if factor is None:
        factor = _float_arg(args, "spike_k", DEFAULT_SPIKE_FACTOR) or DEFAULT_SPIKE_FACTOR
    if factor <= 1:
        raise ToolError("spike_factor must be above 1 (1.5 means 50 % above the baseline)")
    measure = str(args.get("measure") or value_col)
    date_from, date_to = _parse_iso(args, "date_from"), _parse_iso(args, "date_to")
    change_date = _parse_iso(args, "change_date")

    missing: list[str] = []
    bad_date = bad_value = blank_value = 0
    per_period: dict[date, float] = {}
    dates: list[date] = []
    used = 0
    for row in rows:
        d = _parse_date(row.get(date_col))
        if d is None:
            bad_date += 1
            continue
        if (date_from and d < date_from) or (date_to and d > date_to):
            continue
        value, status = _parse_amount(row.get(value_col))
        if status == "bad":
            bad_value += 1
            continue
        if status == "blank":
            blank_value += 1
        used += 1
        dates.append(d)
        start = _period_start(d, period)
        per_period[start] = per_period.get(start, 0.0) + (value or 0.0)
    if bad_date:
        missing.append(f"{_plural(bad_date, 'row')} without a readable date in {date_col} left out.")
    if bad_value:
        missing.append(f"{_plural(bad_value, 'row')} dropped because {value_col} was not a number.")
    if blank_value:
        missing.append(f"{value_col}: {_plural(blank_value, 'blank cell')} counted as 0.")
    out: dict[str, Any] = {
        "analysis": "spikes",
        "window": {"from": min(dates).isoformat() if dates else None, "to": max(dates).isoformat() if dates else None, "rows_total": len(rows), "rows_used": used},
        "rows_used": used,
        "columns": {"date": date_col, "value": value_col},
        "period": period,
        "measure": measure,
        "missing": missing,
        "warnings": [],
        "flags": [],
        "method": (
            f"{measure} summed per {period}; baseline for a period is the median of the previous {window_n} periods (first {window_n} periods have none); "
            f"a spike is a period above {factor:g} × its baseline. Baseline growth compares the median of the first {window_n} periods with the median of the last {window_n} non-spike periods."
        ),
    }
    if not dates:
        missing.append("No usable rows in the window: nothing to count.")
        out.update({"periods": [], "spikes": [], "baseline_growth": None, "change": None, "summary": "No usable rows in the window."})
        return out
    starts = _period_range(min(dates), max(dates), period)
    actuals = [per_period.get(s, 0.0) for s in starts]
    labels = [_period_label(s, period) for s in starts]
    periods, spikes = [], []
    spike_idx: set[int] = set()
    for i, actual in enumerate(actuals):
        baseline = _median(actuals[max(0, i - window_n) : i]) if i >= window_n else None
        is_spike = baseline is not None and baseline > 0 and actual > factor * baseline
        row = {"period": labels[i], "actual": _num(actual), "baseline": _round(baseline), "spike": is_spike}
        periods.append(row)
        if is_spike:
            spike_idx.add(i)
            spikes.append({"period": labels[i], "actual": _num(actual), "baseline": _round(baseline), "uplift_pct": round((actual - baseline) / baseline * 100, 1)})
    if len(actuals) <= window_n:
        out["warnings"].append(f"only {len(actuals)} {period}s in the data; a baseline needs {window_n} earlier periods, so no spike can be detected.")
    clean = [a for i, a in enumerate(actuals) if i not in spike_idx]
    first_level = _median(actuals[:window_n])
    last_level = _median(clean[-window_n:]) if clean else None
    growth = None if not first_level or last_level is None else round((last_level - first_level) / first_level * 100, 1)
    out["baseline_growth"] = {
        "first_window": {"periods": labels[:window_n], "level": _round(first_level)},
        "last_window": {"periods": [labels[i] for i in range(len(labels)) if i not in spike_idx][-window_n:], "level": _round(last_level)},
        "growth_pct": growth,
    }
    change = None
    if change_date is not None:
        cut = _period_start(change_date, period)
        before = [a for i, (s, a) in enumerate(zip(starts, actuals)) if s < cut and i not in spike_idx]
        after = [a for i, (s, a) in enumerate(zip(starts, actuals)) if s >= cut and i not in spike_idx]
        b_level, a_level = _median(before), _median(after)
        change = {
            "change_date": change_date.isoformat(),
            "periods_before": len(before),
            "periods_after": len(after),
            "level_before": _round(b_level),
            "level_after": _round(a_level),
            "change_pct": None if not b_level or a_level is None else round((a_level - b_level) / b_level * 100, 1),
        }
        if len(before) < 3 or len(after) < 3:
            out["warnings"].append("fewer than 3 non-spike periods on one side of change_date; the before/after levels are rough.")
    out.update({"periods": periods, "spikes": spikes, "change": change})
    line = f"{len(labels)} {period}s of {measure}: {len(spikes)} spike{'s' if len(spikes) != 1 else ''} above {factor:g} × the rolling baseline"
    if spikes:
        top = max(spikes, key=lambda s: s["uplift_pct"])
        line += f" (largest {top['period']}, {_fmt(top['actual'])} vs baseline {top['baseline']:g}, +{top['uplift_pct']:g} %)"
    if growth is not None:
        line += f"; the baseline itself moved {growth:+g} % from the first to the last window"
    if change and change["change_pct"] is not None:
        line += f"; after {change['change_date']} the non-spike level is {change['change_pct']:+g} % vs before"
    out["summary"] = line + "."
    return out


# --- analyses on numbers -----------------------------------------------------


def _brands_arg(args: dict) -> list[dict[str, Any]]:
    raw = args.get("brands")
    if not isinstance(raw, list) or not raw:
        raise ToolError("brands is required: a list of {name, penetration_pct, loyalty}")
    universe = args.get("universe")
    brands = []
    for i, b in enumerate(raw):
        if not isinstance(b, dict):
            raise ToolError(f"brands[{i}] must be an object {{name, penetration_pct, loyalty}}")
        name = str(b.get("name") or b.get("brand") or f"brand {i + 1}").strip()
        pen = b.get("penetration_pct")
        if pen in (None, "") and b.get("buyers") not in (None, "") and universe not in (None, ""):
            pen = float(b["buyers"]) / float(universe) * 100
        loyalty = None
        for key in ("loyalty", "loyalty_pct", "repeat_pct", "retention_pct", "frequency", "purchases_per_buyer"):
            if b.get(key) not in (None, ""):
                loyalty = b[key]
                break
        try:
            pen_f = float(pen)  # type: ignore[arg-type]
            loy_f = float(loyalty)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ToolError(f"brands[{i}] ({name}) needs numeric penetration_pct (or buyers with universe) and loyalty") from exc
        if pen_f <= 0:
            raise ToolError(f"brands[{i}] ({name}) has penetration {pen_f:g}; it must be above 0")
        brands.append({"name": name, "penetration_pct": pen_f, "loyalty": loy_f})
    return brands


def _fit(xs: list[float], ys: list[float]) -> tuple[float, float, float | None]:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx == 0:
        raise ToolError("all brands have the same penetration; a double-jeopardy line needs brands of different sizes")
    slope = sxy / sxx
    intercept = my - slope * mx
    r = None if syy == 0 else sxy / math.sqrt(sxx * syy)
    return slope, intercept, r


def _double_jeopardy(args: dict) -> dict[str, Any]:
    brands = _brands_arg(args)
    kind = str(args.get("loyalty_kind") or "repeat").strip().lower()
    if kind not in {"repeat", "frequency", "retention"}:
        raise ToolError("loyalty_kind must be repeat, frequency or retention")
    tolerance = _float_arg(args, "tolerance_pct", DEFAULT_TOLERANCE_PCT) or DEFAULT_TOLERANCE_PCT
    focus = args.get("focus_brand")
    focus = None if focus in (None, "") else str(focus).strip()
    unit = "purchases per buyer" if kind == "frequency" else "%"
    tol_desc = f"{tolerance:g} % of the expected value" if kind == "frequency" else f"{tolerance:g} points"
    out = _base_result(
        "double_jeopardy", None, args.get("period"),
        f"Least-squares line of {kind} on ln(penetration) across the brands given; expected is the line's value at each brand's penetration and the residual is observed minus expected. "
        f"The focus brand is 'in line with size' when |residual| is within {tol_desc}.",
    )
    xs = [math.log(b["penetration_pct"]) for b in brands]
    ys = [b["loyalty"] for b in brands]
    if len(brands) < 2:
        raise ToolError("double jeopardy needs at least two brands")
    slope, intercept, r = _fit(xs, ys)
    rows = []
    for b, x in zip(brands, xs):
        expected = intercept + slope * x
        residual = b["loyalty"] - expected
        beyond = abs(residual) > (abs(expected) * tolerance / 100 if kind == "frequency" else tolerance)
        verdict = "in line with size" if not beyond else ("above" if residual > 0 else "below")
        rows.append({"name": b["name"], "penetration_pct": _round(b["penetration_pct"]), "observed": _round(b["loyalty"]), "expected_from_size": _round(expected), "residual": _round(residual), "verdict": verdict})
    rows.sort(key=lambda row: -row["penetration_pct"])
    focus_row = None
    if focus:
        focus_row = next((row for row in rows if row["name"].lower() == focus.lower()), None)
        if focus_row is None:
            raise ToolError(f"focus_brand {focus!r} is not among the brands given: {', '.join(b['name'] for b in brands)}")
    out.update(
        {
            "loyalty_kind": kind,
            "loyalty_unit": unit,
            "tolerance_pct": tolerance,
            "fit": {"slope": _round(slope, 3), "intercept": _round(intercept, 3), "r": _round(r, 3), "n_brands": len(brands)},
            "rows": rows,
            "focus": focus_row,
            "verdict": focus_row["verdict"] if focus_row else None,
        }
    )
    if len(brands) < 4:
        _flag(out, "few_brands", f"only {len(brands)} brands; a line through fewer than 4 points is a sketch, not a benchmark.")
    if slope <= 0:
        out["warnings"].append("loyalty does not rise with penetration among these brands; the double-jeopardy line is not a safe benchmark here.")
    if focus_row:
        exp, obs, res = focus_row["expected_from_size"], focus_row["observed"], focus_row["residual"]
        suffix = f"{res:+g} points" if kind != "frequency" else f"{res:+g}"
        line = (
            f"{focus_row['name']} has {focus_row['penetration_pct']:g} % penetration; brands that size show about {exp:g}{' %' if kind != 'frequency' else ''} {kind}, "
            f"it shows {obs:g}{' %' if kind != 'frequency' else ''} ({suffix}): {focus_row['verdict']}"
            + ("." if focus_row["verdict"] == "in line with size" else " the line, beyond the tolerance.")
        )
    else:
        line = f"Across {len(brands)} brands {kind} rises {slope:+.2f} per unit of ln(penetration) (r = {out['fit']['r']}); name focus_brand for a verdict."
    out["summary"] = line
    return out


def _series(args: dict, key: str) -> tuple[list[float], str]:
    raw = args.get(key)
    if isinstance(raw, dict):
        values = raw.get("values")
        label = str(raw.get("label") or raw.get("name") or key)
    else:
        values = args.get(f"{key}_values") if raw is None else raw
        label = key
    if not isinstance(values, (list, tuple)) or len(values) < 2:
        raise ToolError(f"{key}.values must be a list of at least two numbers, one per period")
    try:
        numbers = [float(v) for v in values]
    except (TypeError, ValueError) as exc:
        raise ToolError(f"{key}.values must be numbers") from exc
    return numbers, label


def _share_vs_growth(args: dict) -> dict[str, Any]:
    brand, brand_label = _series(args, "brand")
    category, category_label = _series(args, "category")
    if len(brand) != len(category):
        raise ToolError(f"brand has {len(brand)} values and category {len(category)}; give one value per period for both")
    labels = args.get("labels")
    if labels in (None, ""):
        labels = [f"period {i + 1}" for i in range(len(brand))]
    if not isinstance(labels, (list, tuple)) or len(labels) != len(brand):
        raise ToolError("labels must list one label per period")
    out = _base_result(
        "share_vs_growth", None, args.get("period"),
        "share = brand / category per period; growth is last period versus first; share change is in percentage points, and the verdict is held when |points| < 0.1.",
    )
    periods = []
    for label, b, c in zip(labels, brand, category):
        if c <= 0:
            raise ToolError(f"category value for {label} is {c:g}; it must be above 0")
        if b > c:
            out["warnings"].append(f"{label}: brand ({b:g}) exceeds category ({c:g}); check the units.")
        periods.append({"label": str(label), "brand": _num(b), "category": _num(c), "share_pct": round(b / c * 100, 2)})
    brand_growth = None if brand[0] == 0 else round((brand[-1] - brand[0]) / brand[0] * 100, 1)
    category_growth = round((category[-1] - category[0]) / category[0] * 100, 1)
    first_share, last_share = periods[0]["share_pct"], periods[-1]["share_pct"]
    points = round(last_share - first_share, 2)
    verdict = "held" if abs(points) < 0.1 else ("gained" if points > 0 else "lost")
    out.update(
        {
            "brand_label": brand_label,
            "category_label": category_label,
            "periods": periods,
            "brand_growth_pct": brand_growth,
            "category_growth_pct": category_growth,
            "share_first_pct": first_share,
            "share_last_pct": last_share,
            "share_change_points": points,
            "share_change_relative_pct": None if first_share == 0 else round(points / first_share * 100, 1),
            "verdict": verdict,
        }
    )
    growth_txt = "n/a" if brand_growth is None else f"{brand_growth:+g} %"
    line = (
        f"{brand_label} {growth_txt} from {periods[0]['label']} to {periods[-1]['label']} while {category_label} moved {category_growth:+g} %: "
        f"share {first_share:g} % → {last_share:g} % ({points:+g} points), so the brand {verdict} share"
    )
    if brand_growth is not None and brand_growth > 0 and verdict == "lost":
        line += " — sales grew, but slower than the market"
    elif brand_growth is not None and brand_growth < 0 and verdict == "gained":
        line += " — sales fell, but less than the market"
    out["summary"] = line + "."
    return out


# --- entry points --------------------------------------------------------------


def repeat_buying(tools: WorkspaceTools, args: dict) -> dict[str, Any]:
    """Loyalty, penetration, repeat, cohort, duplication and growth arithmetic from a transaction file or from given numbers.

    ``analysis`` picks the question: summary, frequency, repeat, skip_return, cohorts,
    entrants_leavers, duplication and spikes read ``path`` (CSV/XLSX in the workspace)
    with ``columns`` {customer, date, amount?, order_id?, sku?, brand?, units?};
    double_jeopardy takes ``brands`` and share_vs_growth takes ``brand``/``category``
    series, no file needed.
    """
    args = args or {}
    analysis = str(args.get("analysis") or "summary").strip().lower()
    if analysis not in ANALYSES:
        raise ToolError(f"analysis must be one of {', '.join(ANALYSES)}")
    if analysis == "double_jeopardy":
        return _double_jeopardy(args)
    if analysis == "share_vs_growth":
        return _share_vs_growth(args)
    period = _period_arg(args)
    min_buyers = _int_arg(args, "min_buyers", DEFAULT_MIN_BUYERS, 1)
    if analysis == "spikes":
        path = str(args.get("path") or "").strip()
        if not path:
            raise ToolError("path is required: a CSV or XLSX file inside the workspace")
        return _spikes_analysis(_load_rows(tools, path, args.get("sheet")), args, period)
    frame = _load_frame(tools, args)
    focus = args.get("focus_brand")
    focus = None if focus in (None, "") else str(focus).strip()
    if analysis == "summary":
        return _summary_analysis(frame, args, period, min_buyers)
    if analysis == "frequency":
        return _frequency_analysis(frame, args, min_buyers)
    if analysis == "repeat":
        return _repeat_analysis(frame, args, period, min_buyers)
    if analysis == "skip_return":
        return _skip_return_analysis(frame, args, period, min_buyers)
    if analysis == "cohorts":
        return _cohorts_analysis(frame, args, period, min_buyers)
    if analysis == "entrants_leavers":
        return _entrants_leavers_analysis(frame, args, period, min_buyers)
    return _duplication_analysis(frame, args, min_buyers, focus)


def repeat_buying_tool(tools: WorkspaceTools, args: dict) -> str:
    """Backs the worker-facing ``repeat_buying`` tool: JSON the worker can quote from."""
    return json.dumps(repeat_buying(tools, args or {}), ensure_ascii=False, indent=1)
