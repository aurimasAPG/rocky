"""One structured brief per workspace: the inputs the marketer has and did not type.

Across the reviewed concepts the most common gap is an input Rocky needs and the
marketer already knows: audience, offer, price, margin, channel, KPI, budget,
constraints, what success looks like. Workers used to rediscover them from the
goal text or guess. The brief store keeps them in ``<workspace>/.rocky/brief.json``,
every role reads them as a prompt block, tools default from them, and the
decisions a run made are recorded next to the facts they rest on.

Every field remembers where it came from: ``source`` is ``user`` (the marketer
said so), ``worker`` (a role inferred it) or ``file`` (read from a document in
the workspace); ``evidence`` is a URL or a workspace file. A worker setting a
field without evidence is an assumption, and the brief says so until the
marketer confirms it or evidence is attached.

The file path is fixed; nothing a worker passes can move it outside ``.rocky``.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from rocky.tools import ToolError, WorkspaceTools

BRIEF_FILE = Path(".rocky") / "brief.json"
BLOCK_CAP = 2000
DEFAULT_CURRENCY = "EUR"
SOURCES = ("user", "worker", "file")
LANGUAGES = ("lt", "en")
PERIODS = ("day", "week", "month", "quarter", "year", "campaign", "total")
SHOWN_DECISIONS = 3
VALUE_CLIP = 160

# Which brief fields each kind of work needs before it can be done without guessing.
# A tuple inside the list means "any one of these".
MISSING_FOR: dict[str, list[str | tuple[str, ...]]] = {
    # Facts → ads → visuals. Without the audience's struggle and the offer the copy is generic;
    # without the URL the landing page cannot be checked; language decides which copy to write.
    "ads": ["brand", "url", "product", "audience", "offer", "language"],
    # Contribution, break-even and the volume a price change must win back (unit_economics).
    "pricing": ["price", ("variable_cost", "margin_pct"), "units_per_period"],
    # An A/B test on a rate: the KPI with its baseline, the page it runs on, weekly volume and how long the marketer can wait.
    "test": ["kpi", "url", "units_per_period", "deadline"],
    # A channel comparison or media budget: who, how many, where, how much, measured by what.
    "media": ["audience", "audience_size", ("channel", "channels"), "budget", "kpi"],
    # Market and competitor research: what is sold, to whom, where, against whom.
    "research": ["product", "audience", "market", "competitors"],
}

# Brief field → tool input name, per tool. ``defaults_for`` applies these.
TOOL_DEFAULTS: dict[str, dict[str, str]] = {
    "unit_economics": {"price": "price", "currency": "currency", "variable_cost": "variable_cost", "units_per_period": "units"},
    "experiment_plan": {"kpi.baseline": "baseline_rate", "kpi.name": "metric", "weekly_units": "weekly_traffic"},
    "reach_overlap": {"audience_size": "audience", "budget.amount": "budget"},
    "ad_board": {"brand": "brand", "url": "url"},
    "page_inspect": {"url": "url", "language": "language"},
}

ALLOWED_FIELDS = (
    "brand",
    "url",
    "product",
    "audience",
    "audience_size",
    "offer",
    "price",
    "currency",
    "variable_cost",
    "margin_pct",
    "units_per_period",
    "period",
    "channel",
    "channels",
    "kpi",
    "budget",
    "language",
    "constraints",
    "market",
    "competitors",
    "deadline",
    "source_notes",
)
LIST_FIELDS = ("channels", "constraints", "competitors")
TEXT_FIELDS = ("brand", "product", "audience", "offer", "channel", "market", "source_notes")

_CURRENCY_SYMBOLS = {"€": "EUR", "$": "USD", "£": "GBP"}
_LANGUAGE_ALIASES = {"lt": "lt", "lit": "lt", "lietuvių": "lt", "lietuviu": "lt", "lithuanian": "lt", "en": "en", "eng": "en", "english": "en", "anglų": "en", "anglu": "en"}
_PERIOD_ALIASES = {
    "d": "day", "day": "day", "daily": "day", "diena": "day", "dienа": "day", "per day": "day",
    "w": "week", "wk": "week", "week": "week", "weekly": "week", "sav": "week", "savaitė": "week", "savaite": "week", "per week": "week",
    "m": "month", "mo": "month", "month": "month", "monthly": "month", "mėn": "month", "men": "month", "mėnuo": "month", "menuo": "month", "per month": "month",
    "q": "quarter", "quarter": "quarter", "ketvirtis": "quarter",
    "y": "year", "yr": "year", "year": "year", "yearly": "year", "annual": "year", "metai": "year", "per year": "year",
    "campaign": "campaign", "kampanija": "campaign", "total": "total", "viso": "total", "iš viso": "total", "once": "total",
}
_PCT_UNITS = {"%", "pct", "percent", "proc", "proc.", "procentai", "procentų"}
_URL = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)

# Audience: demographics alone are not a segment (500list #2, #7). A struggle word shows an unmet need.
_DEMOGRAPHIC = re.compile(
    r"\d|\b(moterys|moterims|vyrai|vyrams|men|women|male|female|metų|metu|years?|age|aged|y\.?o\.?|m\.)\b",
    re.IGNORECASE,
)
_STRUGGLE = re.compile(
    r"\b(who|that|struggl\w*|want\w*|need\w*|trying|tired|worr\w*|looking for|problem\w*|pain|because|cannot|can't|frustrat\w*|"
    r"kuri\w*|nori\w*|reikia|bando|pavarg\w*|rūpi|rupi|ieško|iesko|nes|sunku|negali|problem\w*|skauda|nespėja|nespeja|trūksta|truksta)\b",
    re.IGNORECASE,
)

# A SWOT part that says nothing a competitor could not also say.
_GENERIC = re.compile(
    r"^(strong|good|great|big|large|growing|weak|bad|small)?\s*(brand|team|market|economy|competition|competitors|product|quality|service|"
    r"price|prices|demand|growth|sales|more sales|nothing|none|n/?a|tbd|unknown|"
    r"stipri komanda|gera komanda|geras produktas|kokybė|kokybe|konkurencija|konkurentai|rinka|rinka auga|ekonomika|kaina|kainos|"
    r"daugiau pardavimų|daugiau pardavimu|nieko|nežinau|nezinau)\.?$",
    re.IGNORECASE,
)
MIN_PART_CHARS = 15


# --- storage -------------------------------------------------------------


def brief_path(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / BRIEF_FILE


def _empty() -> dict[str, Any]:
    return {"version": 1, "fields": {}, "decisions": []}


def load_brief(workspace: str | Path) -> dict[str, Any]:
    path = brief_path(workspace)
    if not path.is_file():
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    out = _empty()
    fields = data.get("fields")
    if isinstance(fields, dict):
        out["fields"] = {k: v for k, v in fields.items() if k in ALLOWED_FIELDS and isinstance(v, dict) and "value" in v}
    decisions = data.get("decisions")
    if isinstance(decisions, list):
        out["decisions"] = [d for d in decisions if isinstance(d, dict)]
    return out


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def save_brief(workspace: str | Path, data: dict[str, Any]) -> None:
    _atomic_write(brief_path(workspace), data)


# --- validation ------------------------------------------------------------


def _text(name: str, value: Any) -> str:
    if isinstance(value, (list, dict)):
        raise ToolError(f"{name} must be text")
    text = " ".join(str(value if value is not None else "").split())
    if not text:
        raise ToolError(f"{name} is empty")
    return text


def _num(name: str, value: Any, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or value is None or value == "":
        raise ToolError(f"{name} must be a number")
    try:
        number = float(str(value).replace(",", ".").replace("€", "").replace("%", "").strip())
    except ValueError as exc:
        raise ToolError(f"{name} must be a number, got {value!r}") from exc
    if math.isnan(number) or math.isinf(number):
        raise ToolError(f"{name} must be a finite number")
    if minimum is not None and number < minimum:
        raise ToolError(f"{name} must be at least {minimum:g}")
    return number


def _currency(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return DEFAULT_CURRENCY
    raw = _CURRENCY_SYMBOLS.get(raw, raw).upper()
    if not re.fullmatch(r"[A-Z]{3}", raw):
        raise ToolError(f"currency must be a 3-letter code such as EUR, got {value!r}")
    return raw


def _period(value: Any) -> str:
    raw = str(value or "").strip().lower().rstrip(".")
    if not raw:
        raise ToolError("period is empty")
    period = _PERIOD_ALIASES.get(raw)
    if period is None:
        raise ToolError(f"period must be one of {', '.join(PERIODS)}, got {value!r}")
    return period


def _url(name: str, value: Any) -> str:
    text = _text(name, value)
    if not _URL.match(text):
        raise ToolError(f"{name} must start with http:// or https://, got {text!r}")
    return text


def _list(name: str, value: Any) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise ToolError(f"{name} must be a list of texts")
    out: list[str] = []
    for item in items:
        text = " ".join(str(item if item is not None else "").split())
        if text and text not in out:
            out.append(text)
    if not out:
        raise ToolError(f"{name} is empty")
    return out


def _money(name: str, value: Any, fallback_currency: str | None) -> dict[str, Any]:
    if isinstance(value, dict):
        amount = _num(f"{name}.amount", value.get("amount"), minimum=0.0)
        currency = _currency(value.get("currency") or fallback_currency)
    elif isinstance(value, str):
        # "49 €", "€49", "49 EUR", "49,90"
        text = value.strip()
        symbol = next((sym for sym in _CURRENCY_SYMBOLS if sym in text), None)
        code = re.search(r"(?<![A-Za-z])([A-Za-z]{3})(?![A-Za-z])", text)
        found = symbol or (code.group(1) if code else None)
        number_text = text.replace(symbol, "") if symbol else (text.replace(code.group(1), "") if code else text)
        amount = _num(name, number_text, minimum=0.0)
        currency = _currency(found or fallback_currency)
    else:
        amount = _num(name, value, minimum=0.0)
        currency = _currency(fallback_currency)
    if amount <= 0:
        raise ToolError(f"{name} must be greater than zero")
    return {"amount": round(amount, 2), "currency": currency}


def _kpi(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = {"name": value}
    if not isinstance(value, dict):
        raise ToolError("kpi must be an object {name, baseline?, target?, unit?}")
    allowed = {"name", "baseline", "target", "unit"}
    extra = sorted(set(value) - allowed)
    if extra:
        raise ToolError(f"kpi has unknown keys {', '.join(extra)}; allowed: name, baseline, target, unit")
    out: dict[str, Any] = {"name": _text("kpi.name", value.get("name"))}
    unit = str(value.get("unit") or "").strip()
    if unit:
        out["unit"] = "%" if unit.lower() in _PCT_UNITS else unit
    for key in ("baseline", "target"):
        if value.get(key) not in (None, ""):
            raw = value[key]
            if isinstance(raw, str) and "%" in raw and "unit" not in out:
                out["unit"] = "%"
            out[key] = _num(f"kpi.{key}", raw)
    if out.get("unit") == "%":
        for key in ("baseline", "target"):
            if key in out and not 0 <= out[key] <= 100:
                raise ToolError(f"kpi.{key} is a percentage and must be between 0 and 100")
    return out


def _budget(value: Any, fallback_currency: str | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        money = _money("budget", value, fallback_currency)
        return {**money, "period": "total"}
    extra = sorted(set(value) - {"amount", "currency", "period"})
    if extra:
        raise ToolError(f"budget has unknown keys {', '.join(extra)}; allowed: amount, currency, period")
    money = _money("budget", {"amount": value.get("amount"), "currency": value.get("currency")}, fallback_currency)
    return {**money, "period": _period(value.get("period") or "total")}


def _language(value: Any) -> str:
    raw = str(value or "").strip().lower()
    code = _LANGUAGE_ALIASES.get(raw)
    if code is None and len(raw) > 2 and raw[2] in "-_" and raw[:2] in LANGUAGES:
        code = raw[:2]  # lt-LT, en_GB
    if code is None:
        raise ToolError(f"language must be lt or en, got {value!r}")
    return code


def _deadline(value: Any) -> str:
    text = str(value or "").strip()
    for parse in (lambda t: date.fromisoformat(t), lambda t: datetime.fromisoformat(t.replace("Z", "+00:00")).date()):
        try:
            return parse(text).isoformat()
        except ValueError:
            continue
    raise ToolError(f"deadline must be an ISO date (YYYY-MM-DD), got {value!r}")


def _demographic_only(text: str) -> bool:
    return bool(_DEMOGRAPHIC.search(text)) and not _STRUGGLE.search(text)


def normalise_field(name: str, value: Any, *, currency: str | None, brief_fields: dict[str, Any], incoming: dict[str, Any]) -> Any:
    """The stored value for one field, or ToolError in plain words."""
    if name in TEXT_FIELDS:
        return _text(name, value)
    if name == "url":
        return _url("url", value)
    if name == "audience_size":
        size = _num("audience_size", value, minimum=1.0)
        return int(round(size))
    if name == "price":
        return _money("price", value, currency)
    if name == "variable_cost":
        cost = _num("variable_cost", value, minimum=0.0)
        return round(cost, 2)
    if name == "margin_pct":
        pct = _num("margin_pct", value)
        if not 0 <= pct <= 100:
            raise ToolError("margin_pct must be between 0 and 100")
        return round(pct, 1)
    if name == "units_per_period":
        units = _num("units_per_period", value, minimum=0.0)
        if "period" not in incoming and "period" not in brief_fields:
            raise ToolError("units_per_period needs a period (day, week, month, quarter or year)")
        return units
    if name == "period":
        return _period(value)
    if name in LIST_FIELDS:
        return _list(name, value)
    if name == "kpi":
        return _kpi(value)
    if name == "budget":
        return _budget(value, currency)
    if name == "language":
        return _language(value)
    if name == "deadline":
        return _deadline(value)
    raise ToolError(f"unknown field {name}; allowed: {', '.join(k for k in ALLOWED_FIELDS if k != 'currency')}")


# --- views -----------------------------------------------------------------


def _is_assumed(record: dict[str, Any]) -> bool:
    return record.get("source") == "worker" and not record.get("evidence")


def _has(fields: dict[str, Any], item: str | tuple[str, ...]) -> bool:
    names = item if isinstance(item, tuple) else (item,)
    return any(name in fields for name in names)


def _label(item: str | tuple[str, ...]) -> str:
    return "|".join(item) if isinstance(item, tuple) else item


def missing_for(fields: dict[str, Any]) -> dict[str, list[str]]:
    return {kind: [_label(item) for item in needs if not _has(fields, item)] for kind, needs in MISSING_FOR.items()}


def _show(name: str, value: Any) -> str:
    if name in ("price",):
        return f"{value['amount']:g} {value['currency']}"
    if name == "budget":
        return f"{value['amount']:g} {value['currency']} / {value['period']}"
    if name == "kpi":
        text = value["name"]
        unit = value.get("unit", "")
        parts = []
        if "baseline" in value:
            parts.append(f"{value['baseline']:g}{unit}")
        if "target" in value:
            parts.append(f"→ {value['target']:g}{unit}")
        return text + (f" ({' '.join(parts)})" if parts else "")
    if name == "margin_pct":
        return f"{value:g} %"
    if name == "audience_size":
        return f"{int(value):,}".replace(",", " ")
    if isinstance(value, list):
        text = ", ".join(value)
    elif isinstance(value, float):
        text = f"{value:g}"
    else:
        text = str(value)
    return text if len(text) <= VALUE_CLIP else text[: VALUE_CLIP - 3] + "..."


def _view(data: dict[str, Any]) -> dict[str, Any]:
    fields = data["fields"]
    return {
        "fields": {name: {**record, "assumed": _is_assumed(record)} for name, record in fields.items()},
        "decisions": list(data["decisions"]),
        "assumed": [name for name, record in fields.items() if _is_assumed(record)],
        "missing_for": missing_for(fields),
    }


def render_block(data: dict[str, Any]) -> str:
    """Prompt block for every role, in the style of memory.py: field lines, decisions, what is still unknown."""
    fields = data["fields"]
    assumed = [n for n, r in fields.items() if _is_assumed(r)]
    line = "═" * 46
    head = f"BRIEF (this workspace) [{len(fields)} fields, {len(assumed)} assumed]"
    rows: list[str] = []
    for name in ALLOWED_FIELDS:
        record = fields.get(name)
        if record is None:
            continue
        shown = _show(name, record["value"])
        if name == "units_per_period" and "period" in fields:
            shown += f" / {fields['period']['value']}"
        if name == "period" and "units_per_period" in fields:
            continue
        tag = str(record.get("source") or "worker")
        if _is_assumed(record):
            tag += ", assumed — confirm with the marketer or add evidence"
        rows.append(f"- {name}: {shown} ({tag})")
    if not rows:
        rows.append("(empty — ask the marketer for the basics and store them with the brief tool)")
    decisions = data["decisions"]
    if decisions:
        rows.append("Decisions:")
        for dec in decisions[-SHOWN_DECISIONS:]:
            inv = dec.get("investment") or {}
            what = inv.get("what") or "?"
            if inv.get("amount") is not None:
                what += f" ({inv['amount']:g} {inv.get('currency') or DEFAULT_CURRENCY})"
            internal = (dec.get("internal") or {}).get("text") or "?"
            external = (dec.get("external") or {}).get("text") or "?"
            change = dec.get("would_change_mind") or "?"
            rows.append(f"- {dec.get('id')} {str(dec.get('at') or '')[:10]}: {what} ← {internal} × {external}; reversed by: {change}")
        if len(decisions) > SHOWN_DECISIONS:
            rows.append(f"  (+{len(decisions) - SHOWN_DECISIONS} earlier; brief get shows all)")
    for kind, names in missing_for(fields).items():
        if names:
            rows.append(f"Still unknown for {kind}: {', '.join(names)}")
    body = "\n".join(rows)
    text = f"{line}\n{head}\n{line}\n{body}"
    if len(text) > BLOCK_CAP:
        note = f"\n… (clipped at {BLOCK_CAP} chars; brief get has the rest)"
        text = text[: BLOCK_CAP - len(note)] + note
    return text


def brief_block(workspace: str | Path) -> str:
    """For the engine: the block to inject into every role's prompt."""
    return render_block(load_brief(workspace))


def _get(data: dict[str, Any], dotted: str) -> Any:
    name, _, sub = dotted.partition(".")
    fields = data["fields"]
    if dotted == "weekly_units":
        if "units_per_period" in fields and fields.get("period", {}).get("value") == "week":
            return fields["units_per_period"]["value"]
        return None
    if dotted == "currency":
        return fields["price"]["value"]["currency"] if "price" in fields else None
    record = fields.get(name)
    if record is None:
        return None
    value = record["value"]
    if not sub:
        return value
    if not isinstance(value, dict):
        return None
    if name == "kpi" and sub == "baseline" and value.get("unit") != "%":
        return None
    return value.get(sub)


def defaults_for(workspace: str | Path, tool_name: str) -> dict[str, Any]:
    """Brief values mapped to the tool's input names; unset values are left out.

    unit_economics: price, currency, variable_cost, units. experiment_plan:
    baseline_rate (kpi.baseline when the unit is %), metric, weekly_traffic
    (units_per_period when the period is a week). reach_overlap: audience
    (audience_size), budget. ad_board: brand, url. page_inspect: url, language.
    """
    mapping = TOOL_DEFAULTS.get(str(tool_name or "").strip())
    if not mapping:
        return {}
    data = load_brief(workspace)
    out: dict[str, Any] = {}
    for source, target in mapping.items():
        value = _get(data, source)
        if value is None:
            continue
        if source == "price":
            value = value["amount"]
        out[target] = value
    return out


# --- actions ---------------------------------------------------------------


def _finding(code: str, message: str, field: str | None = None) -> dict[str, Any]:
    out = {"code": code, "message": message}
    if field:
        out["field"] = field
    return out


def _evidence(tools: WorkspaceTools, value: Any) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if _URL.match(text):
        return text
    target = tools.resolve(text)
    if not target.is_file():
        raise ToolError(f"evidence must be a URL or a file in the workspace; {text!r} is neither")
    return str(target.relative_to(tools.root))


def _set(tools: WorkspaceTools, data: dict[str, Any], args: dict) -> list[dict[str, Any]]:
    fields = args.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise ToolError("set needs fields: an object of field → value")
    unknown = sorted(k for k in fields if k not in ALLOWED_FIELDS)
    if unknown:
        raise ToolError(f"unknown field(s) {', '.join(unknown)}; allowed: {', '.join(ALLOWED_FIELDS)}")
    source = str(args.get("source") or "worker").strip().lower()
    if source not in SOURCES:
        raise ToolError("source must be user, worker or file")
    raw_evidence = args.get("evidence")
    per_field = raw_evidence if isinstance(raw_evidence, dict) else {}
    shared = "" if isinstance(raw_evidence, dict) else _evidence(tools, raw_evidence)
    set_at = str(args.get("run_id") or "manual").strip() or "manual"
    currency = _currency(fields.get("currency")) if fields.get("currency") else None
    findings: list[dict[str, Any]] = []
    for name, raw in fields.items():
        if name == "currency":
            continue
        value = normalise_field(name, raw, currency=currency, brief_fields=data["fields"], incoming=fields)
        record: dict[str, Any] = {"value": value, "source": source, "set_at": set_at}
        evidence = _evidence(tools, per_field.get(name)) if per_field else shared
        if evidence:
            record["evidence"] = evidence
        data["fields"][name] = record
        if name == "audience" and _demographic_only(value):
            findings.append(
                _finding(
                    "audience_demographic_only",
                    "audience names demographics only (age, gender, numbers). Add the struggle or unmet need that makes these people one segment.",
                    "audience",
                )
            )
        if name == "variable_cost" and "price" in data["fields"] and value >= data["fields"]["price"]["value"]["amount"]:
            findings.append(_finding("cost_not_below_price", "variable_cost is not below price: every sale loses money before fixed costs.", "variable_cost"))
        if _is_assumed(record):
            findings.append(_finding("assumed", f"{name} was set by a worker without evidence; it is an assumption until the marketer confirms it.", name))
    if "kpi" in data["fields"]:
        kpi = data["fields"]["kpi"]["value"]
        if "baseline" not in kpi and "kpi" in fields:
            findings.append(_finding("kpi_no_baseline", "kpi has no baseline; a test or a target cannot be sized without it.", "kpi"))
    return findings


def _swot_part(raw: Any, name: str, kinds: tuple[str, ...], findings: list[dict[str, Any]]) -> dict[str, Any] | None:
    if raw in (None, "", {}):
        findings.append(_finding("decision_missing_part", f"{name} is missing: name one {' or '.join(kinds)} with its source.", name))
        return None
    if isinstance(raw, str):
        raw = {"kind": "", "text": raw}
    if not isinstance(raw, dict):
        raise ToolError(f"{name} must be an object {{kind, text, source}}")
    kind = str(raw.get("kind") or "").strip().lower()
    text = " ".join(str(raw.get("text") or "").split())
    source = " ".join(str(raw.get("source") or "").split())
    if kind and kind not in kinds:
        raise ToolError(f"{name}.kind must be {' or '.join(kinds)}, got {kind!r}")
    if not kind:
        findings.append(_finding("decision_missing_part", f"{name}.kind is missing: {' or '.join(kinds)}.", name))
    if not text:
        findings.append(_finding("decision_missing_part", f"{name}.text is missing.", name))
    elif len(text) < MIN_PART_CHARS or _GENERIC.match(text):
        findings.append(_finding("decision_generic", f"{name} '{text}' is generic: say what specifically, measured how, compared with whom.", name))
    if not source:
        findings.append(_finding("decision_no_source", f"{name} has no source; name the document, data or interview it comes from.", name))
    return {"kind": kind or None, "text": text or None, "source": source or None}


def _decision(data: dict[str, Any], args: dict, now: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    present = [k for k in ("internal", "external", "investment", "would_change_mind") if args.get(k) not in (None, "", {})]
    if not present:
        raise ToolError("decision needs internal {kind, text, source}, external {kind, text, source}, investment {what, amount?, currency?} and would_change_mind")
    internal = _swot_part(args.get("internal"), "internal", ("strength", "weakness"), findings)
    external = _swot_part(args.get("external"), "external", ("opportunity", "threat"), findings)
    raw_inv = args.get("investment")
    investment: dict[str, Any] | None = None
    if raw_inv in (None, "", {}):
        findings.append(_finding("decision_missing_part", "investment is missing: what will be bought or built, and for how much.", "investment"))
    else:
        if isinstance(raw_inv, str):
            raw_inv = {"what": raw_inv}
        if not isinstance(raw_inv, dict):
            raise ToolError("investment must be an object {what, amount?, currency?}")
        what = " ".join(str(raw_inv.get("what") or "").split())
        investment = {"what": what or None}
        if not what:
            findings.append(_finding("decision_missing_part", "investment.what is missing.", "investment"))
        elif len(what) < MIN_PART_CHARS or _GENERIC.match(what):
            findings.append(_finding("decision_generic", f"investment '{what}' is generic: name the activity, channel or asset and its size.", "investment"))
        if raw_inv.get("amount") not in (None, ""):
            investment["amount"] = round(_num("investment.amount", raw_inv.get("amount"), minimum=0.0), 2)
            investment["currency"] = _currency(raw_inv.get("currency"))
        else:
            findings.append(_finding("decision_no_amount", "investment has no amount; the budget line cannot be checked against the brief.", "investment"))
    change = " ".join(str(args.get("would_change_mind") or "").split())
    if not change:
        findings.append(_finding("decision_missing_part", "would_change_mind is missing: the evidence that would reverse this decision.", "would_change_mind"))
    elif len(change) < MIN_PART_CHARS or _GENERIC.match(change):
        findings.append(_finding("decision_generic", f"would_change_mind '{change}' is generic: name a number, a date or an observation.", "would_change_mind"))
    ids = [int(str(d.get("id") or "d0")[1:]) for d in data["decisions"] if str(d.get("id") or "").startswith("d") and str(d.get("id"))[1:].isdigit()]
    record = {
        "id": f"d{(max(ids) if ids else 0) + 1}",
        "at": now,
        "run_id": str(args.get("run_id") or "manual").strip() or "manual",
        "internal": internal,
        "external": external,
        "investment": investment,
        "would_change_mind": change or None,
        "status": "open",
        "gaps": sorted({str(f.get("field")) for f in findings if f.get("field")}),
    }
    data["decisions"].append(record)
    return record, findings


def _clear(data: dict[str, Any], args: dict) -> str:
    names = args.get("fields")
    if names in (None, "", []):
        count = len(data["fields"]) + len(data["decisions"])
        data["fields"] = {}
        data["decisions"] = []
        return f"Cleared the whole brief ({count} entries)."
    if isinstance(names, str):
        names = [names]
    if not isinstance(names, list):
        raise ToolError("clear takes fields: a list of field names, or nothing to clear everything")
    unknown = sorted(n for n in names if n not in ALLOWED_FIELDS and n != "decisions")
    if unknown:
        raise ToolError(f"unknown field(s) {', '.join(unknown)}; allowed: {', '.join(ALLOWED_FIELDS)}, decisions")
    removed = []
    for name in names:
        if name == "decisions":
            if data["decisions"]:
                data["decisions"] = []
                removed.append("decisions")
        elif data["fields"].pop(name, None) is not None:
            removed.append(name)
    return f"Cleared {', '.join(removed)}." if removed else "Nothing to clear: none of those fields was set."


def _now(args: dict) -> str:
    raw = args.get("now")
    if raw:
        text = str(raw).strip()
        try:
            datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ToolError(f"now must be an ISO datetime, got {raw!r}") from exc
        return text
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _summary(action: str, view: dict[str, Any], findings: list[dict[str, Any]], extra: str = "") -> str:
    n_fields = len(view["fields"])
    n_assumed = len(view["assumed"])
    open_kinds = [k for k, v in view["missing_for"].items() if v]
    parts = [extra] if extra else []
    parts.append(f"Brief has {n_fields} field{'s' if n_fields != 1 else ''} ({n_assumed} assumed) and {len(view['decisions'])} decision{'s' if len(view['decisions']) != 1 else ''}.")
    if open_kinds:
        parts.append("Still unknown for " + "; ".join(f"{k}: {', '.join(view['missing_for'][k])}" for k in open_kinds) + ".")
    else:
        parts.append("Every kind of work has its inputs.")
    if findings:
        parts.append(f"{len(findings)} finding{'s' if len(findings) != 1 else ''} to resolve.")
    return " ".join(parts)


def brief(tools: WorkspaceTools, args: dict) -> dict[str, Any]:
    """The brief store. Actions: get, set, decision, clear, block. The file is always <workspace>/.rocky/brief.json."""
    args = args or {}
    action = str(args.get("action") or "get").strip().lower()
    data = load_brief(tools.root)
    findings: list[dict[str, Any]] = []
    extra = ""
    out: dict[str, Any] = {"action": action}
    if action == "get":
        pass
    elif action == "set":
        findings = _set(tools, data, args)
        save_brief(tools.root, data)
        extra = f"Set {', '.join(k for k in args['fields'] if k != 'currency')}."
    elif action == "decision":
        record, findings = _decision(data, args, _now(args))
        save_brief(tools.root, data)
        out["decision"] = record
        extra = f"Recorded decision {record['id']}." + (" It has gaps: " + ", ".join(record["gaps"]) + "." if record["gaps"] else "")
    elif action == "clear":
        extra = _clear(data, args)
        save_brief(tools.root, data)
    elif action == "block":
        out["block"] = render_block(data)
    else:
        raise ToolError("action must be get, set, decision, clear or block")
    view = _view(data)
    out.update(view)
    out["findings"] = findings
    out["path"] = str(BRIEF_FILE)
    out["summary"] = _summary(action, view, findings, extra)
    return out


def brief_tool(tools: WorkspaceTools, args: dict) -> str:
    """Backs the worker-facing ``brief`` tool. ``block`` returns the prompt block itself; other actions return JSON."""
    out = brief(tools, args or {})
    if out["action"] == "block":
        return out["block"]
    return json.dumps(out, ensure_ascii=False, indent=1)
