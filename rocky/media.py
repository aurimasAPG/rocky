"""Media-planning arithmetic the worker must not do in prose: one tool, eight modes.

``media_plan`` takes ``mode`` and the inputs for that mode and returns the figures
with the denominators behind them, what is ``missing``, ``warnings`` and a
``summary`` sentence written to be quoted as is. Nothing is estimated: a line
without an amount is an error, a rate that was not given becomes a conditional
table, a date the tool does not know is returned as a question.

Modes
-----
* ``ledger``      budget lines by channel, cost type and role; media-only vs all-in
                  shares; always-on lines from other teams reconciled into the split.
* ``sov``         share of voice, share of market, excess share of voice in points,
                  spend per share point by brand size, spend for a target ESOV.
* ``budget``      a budget from activities: split against a sourced benchmark,
                  coverage of the objective, cost per result, headroom, cut
                  candidates; adjustments to the benchmark applied once each.
* ``offers``      media offers on one denominator: viewable CPM, cost per click,
                  cost per the target event through the stated conversion path.
* ``volume``      sales volume = population × penetration × frequency × units, the
                  reconciliation with reported volume and what moved between periods.
* ``calendar``    a 52-week always-on plus flights plan with known Lithuanian
                  occasions, totals, overlaps and the budget cap.
* ``channel_fit`` whether a new social platform is backed by sourced evidence for
                  the segment and the job, and whether the team has the hours.
* ``classify``    spend rows sorted into brand / activation / mixed by the creative's
                  own text (price, %, deadline, code, buy CTA), never by channel name.
"""

from __future__ import annotations

import json
import math
import re
import statistics
from collections import defaultdict
from datetime import date, timedelta
from itertools import combinations
from typing import Any

from rocky.reach import _number as _reach_number
from rocky.reach import _parse as _parse_number
from rocky.tools import ToolError

MODES = ("ledger", "sov", "budget", "offers", "volume", "calendar", "channel_fit", "classify")
COST_TYPES = ("media", "production", "fees", "tech", "other")
ROLES = ("brand", "activation")
KINDS = ("brand", "activation")
MARKETS = ("b2b", "b2c")
PRICE_BASES = ("cpm_served", "cpm_viewable", "cpc", "cpl", "cpa")
COMPARE_BASES = ("vcpm", "cpm_served", "cpc", "cpl", "cpa", "cost_per_target_event")
BENCHMARKS = {
    "b2b": {"brand_pct": 46, "source": "The 5 Principles of Growth in B2B Marketing (LinkedIn B2B Institute / Binet & Field, 2019), p. 15 — derived from award entries, a starting scenario to test locally"},
    "b2c": {"brand_pct": 60, "source": "Binet & Field, Effectiveness in Context (IPA, 2018), pp. 9–10 — a cross-category average, adjust for the category and say why"},
}
ESOV_RULE = (
    "Brands whose share of voice exceeds their share of market tend to gain share: the IPA / Binet & Field average is roughly 0.5 share points a year "
    "per 10 points of excess share of voice. That is an average across categories and decades, not a forecast; price, distribution, product launches "
    "and competitor moves also move share and must be allowed for before reading a share change as an ESOV effect."
)
ESOV_SHARE_GAIN_PER_10_POINTS = 0.5
MAX_ADJUSTMENT_POINTS = 20.0
VIEWABILITY_STEPS = (40, 50, 60, 70)
RATE_STEPS = (10, 20, 30, 50)
PRODUCTION_BUNDLE_SHARE = 20.0
UNCLASSIFIED_RELIABLE_PCT = 30.0
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)

# Known occasions: fixed month-day or a rule evaluated per year. Lithuanian and EU retail calendar.
_FIXED_OCCASIONS: dict[str, tuple[int, int, str]] = {
    "naujieji": (1, 1, "Naujieji metai (New Year)"),
    "valentino": (2, 14, "Valentino diena (Valentine's Day)"),
    "vasario_16": (2, 16, "Vasario 16-oji (State restoration day)"),
    "kovo_8": (3, 8, "Kovo 8-oji (Women's Day)"),
    "kovo_11": (3, 11, "Kovo 11-oji (Independence restoration day)"),
    "jonines": (6, 24, "Joninės (Midsummer)"),
    "mokslo_metai": (9, 1, "Rugsėjo 1-oji (school year start)"),
    "helovinas": (10, 31, "Helovinas (Halloween)"),
    "velines": (11, 1, "Vėlinės (All Souls)"),
    "kaledos": (12, 24, "Kūčios / Kalėdos (Christmas Eve, when gifts are given in Lithuania)"),
}
_OCCASION_ALIASES = {
    "kalėdos": "kaledos", "christmas": "kaledos", "kucios": "kaledos", "kūčios": "kaledos",
    "new_year": "naujieji", "naujieji_metai": "naujieji",
    "valentine": "valentino", "valentines": "valentino", "valentino_diena": "valentino",
    "joninės": "jonines", "midsummer": "jonines",
    "rugsejo_1": "mokslo_metai", "rugsėjo_1": "mokslo_metai", "school_year": "mokslo_metai",
    "halloween": "helovinas",
    "vėlinės": "velines",
    "motinos_diena": "motinos_diena", "mothers_day": "motinos_diena", "mother's_day": "motinos_diena",
    "tėvo_diena": "tevo_diena", "fathers_day": "tevo_diena",
    "velykos": "velykos", "easter": "velykos",
    "black_friday": "black_friday", "juodasis_penktadienis": "black_friday",
}
KNOWN_OCCASIONS = tuple(sorted(set(_FIXED_OCCASIONS) | {"motinos_diena", "tevo_diena", "velykos", "black_friday"}))

# Activation markers in creative text: each tuple is (code, regex). Two distinct kinds → activation, one → mixed.
_ACTIVATION_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("price", re.compile(r"(\d[\d\s.,]*\s?(€|eur\b)|€\s?\d)", re.IGNORECASE)),
    ("percent", re.compile(r"-?\d+\s?%")),
    ("deadline", re.compile(r"\b(iki|until|till|by|before)\s+\d", re.IGNORECASE)),
    ("code", re.compile(r"\b(kodas|kodu|kodą|code|promo)\b", re.IGNORECASE)),
    ("buy_cta", re.compile(r"\b(pirk|pirkite|pirkti|užsisakyk|užsisakykite|užsakyk|užsakykite|rezervuok|rezervuokite|buy now|shop now|order now|order today|book now|get yours|sign up now|registruokis dabar)\b", re.IGNORECASE)),
    ("urgency", re.compile(r"(tik šiandien|tik siandien|today only|paskutinė diena|paskutine diena|last chance|skubėk|skubek|hurry|ends soon|baigiasi|limited time|ribotas kiekis)", re.IGNORECASE)),
    ("sale", re.compile(r"(akcij|nuolaid|išpardavim|isparduot|\bsale\b|discount|juodasis penktadienis|black friday|cyber monday|\bdeal\b|pasiūlym|pasiulym|\boffer\b)", re.IGNORECASE)),
)


# --- shared helpers ----------------------------------------------------------


def _num(args: dict, key: str, *, required: bool = False, minimum: float | None = None, label: str | None = None) -> float | None:
    return _reach_number(args, key, required=required, minimum=minimum, label=label)


def _positive(args: dict, key: str, label: str, *, required: bool = False) -> float | None:
    value = _num(args, key, required=required, minimum=0.0, label=label)
    if value is not None and value <= 0:
        raise ToolError(f"{label} must be greater than zero")
    return value


def _enum(item: dict, key: str, allowed: tuple[str, ...], label: str, *, required: bool = False) -> str | None:
    raw = item.get(key)
    if raw is None or raw == "":
        if required:
            raise ToolError(f"{label} is required: one of {', '.join(allowed)}")
        return None
    value = str(raw).strip().lower().replace("-", "_").replace(" ", "_")
    if value not in allowed:
        raise ToolError(f"{label} must be one of {', '.join(allowed)}, got {raw!r}")
    return value


def _rows(args: dict, key: str, what: str, *, required: bool = True) -> list[dict]:
    raw = args.get(key)
    if raw in (None, "", []):
        if required:
            raise ToolError(f"{key} must be a list with at least one {what}")
        return []
    if not isinstance(raw, list):
        raise ToolError(f"{key} must be a list of {what} objects")
    for n, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ToolError(f"{key} item {n} must be an object ({what}), got {type(item).__name__}")
    return raw


def _text(item: dict, key: str, fallback: str = "") -> str:
    value = item.get(key)
    return str(value).strip() if value not in (None, "") else fallback


def _money(value: float) -> float:
    return round(value, 2)


def _pct(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100, 1)


def _fmt_money(value: float, currency: str = "€") -> str:
    text = f"{value:,.0f}".replace(",", " ") if abs(value - round(value)) < 0.005 else f"{value:,.2f}".replace(",", " ")
    return f"{currency}{text}" if currency in ("€", "$", "£") else f"{text} {currency}"


def _tokens(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text) if len(w) >= 3}


def _stems(text: str, size: int = 5) -> set[str]:
    return {t[:size] for t in _tokens(text)}


def _base(out: dict[str, Any]) -> dict[str, Any]:
    out.setdefault("missing", [])
    out.setdefault("warnings", [])
    return out


# --- ledger (#403, #404) -----------------------------------------------------


def _ledger(args: dict) -> dict[str, Any]:
    currency = _text(args, "currency", "€")
    period = _text(args, "period") or None
    raw_lines = _rows(args, "lines", "budget line", required=False)
    raw_always = _rows(args, "always_on", "always-on block", required=False)
    if not raw_lines and not raw_always:
        raise ToolError("no budget lines supplied: give lines [{label, channel, cost_type, amount, team?, role?}] and/or always_on blocks")
    missing: list[str] = []
    warnings: list[str] = []
    lines: list[dict[str, Any]] = []
    for n, item in enumerate(raw_lines, start=1):
        label = _text(item, "label", f"line {n}")
        amount = _num(item, "amount", required=True, minimum=0.0, label=f"amount of {label}")
        cost_type = _enum(item, "cost_type", COST_TYPES, f"cost_type of {label}")
        if cost_type is None:
            missing.append(f"cost_type for {label} (counted as other)")
            cost_type = "other"
        lines.append(
            {
                "label": label,
                "channel": _text(item, "channel", "(no channel)"),
                "cost_type": cost_type,
                "team": _text(item, "team") or None,
                "role": _enum(item, "role", ROLES, f"role of {label}"),
                "amount": amount,
                "always_on": False,
            }
        )
    always_total = 0.0
    for n, item in enumerate(raw_always, start=1):
        label = _text(item, "label", f"always-on {n}")
        months = _num(item, "months", minimum=0.0, label=f"months of {label}")
        monthly = _num(item, "monthly_amount", minimum=0.0, label=f"monthly_amount of {label}")
        amount = _num(item, "amount", minimum=0.0, label=f"amount of {label}")
        if amount is None:
            if monthly is None or months is None:
                raise ToolError(f"always-on block {label} needs amount, or monthly_amount and months")
            amount = monthly * months
        role = _enum(item, "role", ROLES, f"role of {label}") or "activation"
        lines.append(
            {
                "label": label,
                "channel": _text(item, "channel", "(no channel)"),
                "cost_type": _enum(item, "cost_type", COST_TYPES, f"cost_type of {label}") or "media",
                "team": _text(item, "team") or None,
                "role": role,
                "amount": amount,
                "always_on": True,
                "months": months,
                "monthly_amount": monthly,
            }
        )
        always_total += amount

    total = sum(line["amount"] for line in lines)
    campaign_total = sum(line["amount"] for line in lines if not line["always_on"])
    by_type: dict[str, float] = defaultdict(float)
    by_channel: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for line in lines:
        by_type[line["cost_type"]] += line["amount"]
        by_channel[line["channel"]][line["cost_type"]] += line["amount"]
    media_total = by_type.get("media", 0.0)
    channels_out = []
    findings: list[dict[str, Any]] = []
    for channel, parts in by_channel.items():
        channel_total = sum(parts.values())
        row = {"channel": channel}
        for cost_type in COST_TYPES:
            row[cost_type] = _money(parts.get(cost_type, 0.0))
        row["total"] = _money(channel_total)
        row["share_media_only_pct"] = _pct(parts.get("media", 0.0), media_total)
        row["share_all_in_pct"] = _pct(channel_total, total)
        channels_out.append(row)
        if parts.get("media", 0.0) > 0 and parts.get("production", 0.0) == 0:
            findings.append({"code": "production_missing", "channel": channel, "message": f"production not recorded for {channel}: its all-in cost is understated."})
        other = parts.get("other", 0.0)
        if channel_total > 0 and other / channel_total * 100 > PRODUCTION_BUNDLE_SHARE:
            findings.append(
                {"code": "production_bundled", "channel": channel, "message": f"{channel} has {_pct(other, channel_total):g} % under 'other': production may be bundled into it; split the line."}
            )
    for line in lines:
        words = line["label"].lower()
        if ("media" in words or "medij" in words) and ("production" in words or "produkc" in words or "gamyb" in words):
            findings.append({"code": "production_bundled", "channel": line["channel"], "message": f"line '{line['label']}' names both media and production: split it, the shares cannot be computed from a bundled figure."})
    channels_out.sort(key=lambda r: -r["total"])

    by_cost_type = {t: {"amount": _money(v), "pct": _pct(v, total)} for t, v in by_type.items()}
    roles_given = any(line["role"] is not None for line in lines if not line["always_on"])
    by_role: dict[str, Any] | None = None
    if roles_given:
        by_role = {}
        for role in ROLES:
            campaign_amount = sum(l["amount"] for l in lines if not l["always_on"] and l["role"] == role)
            all_amount = sum(l["amount"] for l in lines if l["role"] == role)
            by_role[role] = {
                "amount_campaign_only": _money(campaign_amount),
                "amount_reconciled": _money(all_amount),
                "share_campaign_only_pct": _pct(campaign_amount, campaign_total),
                "share_reconciled_pct": _pct(all_amount, total) if raw_always else None,
            }
        unassigned = [l["label"] for l in lines if l["role"] is None]
        if unassigned:
            by_role["unassigned"] = unassigned
            missing.append("role for " + ", ".join(unassigned))
        teams = {l["team"] for l in lines if l["team"]}
        if not raw_always:
            brand_share = by_role["brand"]["share_campaign_only_pct"]
            findings.append(
                {
                    "code": "other_teams_missing",
                    "channel": None,
                    "message": f"brand share {brand_share:g} % is an upper bound; always-on search, shopping, affiliate and retargeting spend from other teams was not supplied.",
                }
            )
            if len(teams) <= 1:
                missing.append("always_on lines from other teams (search, shopping, retargeting, affiliate)")
        else:
            by_role["note"] = "always-on blocks count as activation unless given a role; reconciled shares include them, campaign-only shares do not."
    mentioned = args.get("mentioned_spend") or []
    if mentioned:
        if not isinstance(mentioned, list):
            raise ToolError("mentioned_spend must be a list of spend names the brief mentions")
        haystack = " ".join(f"{l['label']} {l['channel']}" for l in lines).lower()
        for name in mentioned:
            stems = _stems(str(name))
            if not stems or not any(stem in haystack for stem in stems):
                findings.append({"code": "activation_uncounted", "channel": None, "message": f"the brief mentions {name} spend but no line carries it: add it before quoting a split."})

    quoted = args.get("quoted_shares") or []
    quoted_out = []
    if quoted:
        if not isinstance(quoted, list):
            raise ToolError("quoted_shares must be a list of {channel, pct, basis?}")
        index = {r["channel"].lower(): r for r in channels_out}
        for item in quoted:
            if not isinstance(item, dict):
                raise ToolError("quoted_shares items must be objects with channel and pct")
            channel = _text(item, "channel")
            pct = _num(item, "pct", required=True, minimum=0.0, label=f"quoted pct for {channel}")
            basis = _text(item, "basis") or None
            row = index.get(channel.lower())
            check: dict[str, Any] = {"channel": channel, "quoted_pct": pct, "basis_given": basis}
            if row is None:
                check["note"] = f"no lines for {channel}"
            else:
                media_share, all_share = row["share_media_only_pct"], row["share_all_in_pct"]
                check["media_only_pct"], check["all_in_pct"] = media_share, all_share
                candidates = [(abs(pct - s), name) for s, name in ((media_share, "media-only"), (all_share, "all-in")) if s is not None]
                gap, closest = min(candidates) if candidates else (None, None)
                check["matches"] = closest if gap is not None and gap <= 1.0 else "neither"
                if basis is None:
                    findings.append(
                        {
                            "code": "basis_unstated",
                            "channel": channel,
                            "message": f"{pct:g} % for {channel} was quoted without a basis: it is {all_share:g} % all-in and {media_share:g} % of media only.",
                        }
                    )
            quoted_out.append(check)

    top = channels_out[0] if channels_out else None
    summary = f"Total {_fmt_money(total, currency)}" + (f" ({period})" if period else "") + f", of which media {_fmt_money(media_total, currency)}."
    if top is not None and len(channels_out) > 1 and top["share_media_only_pct"] is not None and abs(media_total - total) > 0.005:
        summary += f" {top['channel']} is {top['share_all_in_pct']:g} % of all spend but {top['share_media_only_pct']:g} % of media buys."
    if by_role is not None:
        brand = by_role["brand"]
        if brand["share_reconciled_pct"] is not None:
            summary += (
                f" Brand share {brand['share_campaign_only_pct']:g} % within the campaign lines, {brand['share_reconciled_pct']:g} % once "
                f"{_fmt_money(always_total, currency)} of always-on spend is added."
            )
        else:
            summary += f" Brand share {brand['share_campaign_only_pct']:g} % of the campaign lines, an upper bound until other teams' always-on spend is added."
    prod_missing = [f["channel"] for f in findings if f["code"] == "production_missing"]
    if prod_missing:
        summary += " Production is not recorded for " + ", ".join(prod_missing) + "."
    out: dict[str, Any] = {
        "mode": "ledger",
        "currency": currency,
        "period": period,
        "total": _money(total),
        "campaign_total": _money(campaign_total),
        "always_on_total": _money(always_total),
        "media_total": _money(media_total),
        "by_channel": channels_out,
        "by_cost_type": by_cost_type,
        "by_role": by_role,
        "findings": findings,
        "lines": len(lines),
        "missing": missing,
        "warnings": warnings,
        "summary": summary,
    }
    if quoted_out:
        out["quoted_share_check"] = quoted_out
    return out


# --- sov (#407, #411) ----------------------------------------------------------


def _sov(args: dict) -> dict[str, Any]:
    currency = _text(args, "currency", "€")
    missing: list[str] = []
    warnings: list[str] = []
    brands: list[dict[str, Any]] = []
    raw_brands = args.get("brands")
    if raw_brands:
        for n, item in enumerate(_rows(args, "brands", "brand"), start=1):
            name = _text(item, "name", f"brand {n}")
            spend = _num(item, "media_spend", required=True, minimum=0.0, label=f"media_spend of {name}")
            share = _num(item, "share_of_market_pct", minimum=0.0, label=f"share_of_market_pct of {name}")
            if share is not None and share > 100:
                raise ToolError(f"share_of_market_pct of {name} must be between 0 and 100")
            brands.append({"name": name, "spend": spend, "share": share, "is_own": bool(item.get("is_own"))})
    else:
        own_spend = _num(args, "brand_spend", minimum=0.0, label="brand_spend")
        if own_spend is None:
            raise ToolError("give brands [{name, share_of_market_pct, media_spend, is_own}] or brand_spend with competitor_spends / category_total_spend and market_share_pct")
        share = _num(args, "market_share_pct", minimum=0.0, label="market_share_pct")
        if share is not None and share > 100:
            raise ToolError("market_share_pct must be between 0 and 100")
        brands.append({"name": _text(args, "brand_name", "own brand"), "spend": own_spend, "share": share, "is_own": True})
        competitors = args.get("competitor_spends") or {}
        if not isinstance(competitors, dict):
            raise ToolError("competitor_spends must be an object {name: spend}")
        for name, raw in competitors.items():
            brands.append({"name": str(name), "spend": _parse_number(raw, f"spend of {name}", minimum=0.0), "share": None, "is_own": False})
    own = [b for b in brands if b["is_own"]]
    if len(own) != 1:
        raise ToolError("exactly one brand must be marked is_own")
    own = own[0]
    if own["share"] is None:
        missing.append("market share of the own brand (share_of_market_pct / market_share_pct)")
    listed_total = sum(b["spend"] for b in brands)
    category_total = _num(args, "category_total_spend", minimum=0.0, label="category_total_spend")
    if category_total is None:
        category_total = _num(args, "category_total", minimum=0.0, label="category_total")
    if category_total is not None and category_total < listed_total - 1e-9:
        raise ToolError(f"category_total_spend {category_total:,.0f} is below the listed brands' spend {listed_total:,.0f}")
    denominator = category_total if category_total is not None else listed_total
    coverage = "full" if category_total is not None else "known_competitors_only"
    if denominator <= 0:
        raise ToolError("total media spend is zero: shares of voice cannot be computed")
    competitors_n = len(brands) - 1
    if competitors_n == 0 and category_total is None:
        raise ToolError("no competitor spend or category total given: share of voice needs a denominator")
    if competitors_n < 2 and category_total is None:
        warnings.append("Only one competitor listed: the share of voice is against that brand alone and says little about the category.")
    if category_total is None:
        missing.append("category_total_spend (share of voice is against the named competitors only)")
    shares_known = [b for b in brands if b["share"] is not None]
    share_sum = sum(b["share"] for b in shares_known)
    if share_sum > 100 + 1e-9:
        warnings.append(f"Market shares add up to {share_sum:g} %, above 100: check the share basis before reading ESOV.")
    planned = _num(args, "planned_spend", minimum=0.0, label="planned_spend")
    target_esov = _num(args, "target_esov_points", label="target_esov_points")

    rows = []
    for b in brands:
        sov = b["spend"] / denominator * 100
        row: dict[str, Any] = {"name": b["name"], "is_own": b["is_own"], "media_spend": _money(b["spend"]), "sov_pct": round(sov, 1), "som_pct": b["share"]}
        if b["share"] is not None:
            row["esov_points"] = round(sov - b["share"], 1)
            row["spend_per_share_point"] = _money(b["spend"] / b["share"]) if b["share"] > 0 else None
        rows.append(row)
    own_row = next(r for r in rows if r["is_own"])
    own_sov = own["spend"] / denominator * 100
    others = denominator - own["spend"]

    out: dict[str, Any] = {
        "mode": "sov",
        "currency": currency,
        "share_basis": _text(args, "share_basis", "value"),
        "period": _text(args, "period") or None,
        "coverage": coverage,
        "category_total_used": _money(denominator),
        "listed_spend": _money(listed_total),
        "brands": rows,
        "own": {"name": own["name"], "sov_pct": own_row["sov_pct"], "som_pct": own["share"]},
        "findings": [],
        "missing": missing,
        "warnings": warnings,
    }
    findings = out["findings"]
    if own["share"] is not None:
        esov = own_sov - own["share"]
        verdict = "parity" if abs(esov) < 1 else ("over-investing" if esov > 0 else "under-investing")
        out["own"].update(
            {
                "esov_points": round(esov, 1),
                "verdict": verdict,
                "esov_rule_of_thumb_share_gain_pts_per_year": round(esov / 10 * ESOV_SHARE_GAIN_PER_10_POINTS, 2),
            }
        )
        out["growth_expectation"] = ESOV_RULE
        if coverage == "known_competitors_only":
            out["own"]["note"] = "share of voice against the named competitors only; with the whole category it would be lower."
        if target_esov is not None:
            t = (own["share"] + target_esov) / 100
            if t >= 1:
                warnings.append("target_esov_points puts the share of voice at or above 100 %: unreachable.")
            elif t <= 0:
                out["spend_for_target_esov"] = 0.0
            else:
                # x / (others + x) = t  →  x = t · others / (1 − t)
                out["spend_for_target_esov"] = _money(t * others / (1 - t))
                out["spend_for_target_esov_note"] = f"spend that gives SOV {t * 100:g} % (= SOM {own['share']:g} + {target_esov:g} points) with competitors' spend held at {_fmt_money(others, currency)}."
        if planned is not None:
            planned_sov = planned / (others + planned) * 100
            out["planned"] = {"spend": _money(planned), "sov_pct": round(planned_sov, 1), "esov_points": round(planned_sov - own["share"], 1)}
    # Brand-size comparison: leader and peers by share.
    competitors_with_share = [b for b in shares_known if not b["is_own"] and b["share"] > 0]
    if competitors_with_share and own["share"]:
        leader = max(competitors_with_share, key=lambda b: b["share"])
        leader_ratio = leader["spend"] / leader["share"]
        out["leader"] = {"name": leader["name"], "som_pct": leader["share"], "spend_per_share_point": _money(leader_ratio)}
        out["spend_at_leader_ratio"] = _money(leader_ratio * own["share"])
        low, high = own["share"] / 2, own["share"] * 2
        peers = [b for b in competitors_with_share if low <= b["share"] <= high]
        if peers:
            ratios = [b["spend"] / b["share"] for b in peers]
            peer_ratio = statistics.median(ratios)
            out["peers"] = {
                "names": [b["name"] for b in peers],
                "band": f"brands with {low:g}–{high:g} % share (half to double the own share)",
                "median_spend_per_share_point": _money(peer_ratio),
            }
            out["spend_at_peer_ratio"] = _money(peer_ratio * own["share"])
        else:
            out["peers"] = None
            findings.append({"code": "no_peers", "message": f"no listed brand has a share between {low:g} and {high:g} %: the peer ratio cannot be computed; the leader's ratio is not a benchmark for a brand this size."})
        if own["share"] < leader["share"] / 2:
            candidate = planned if planned is not None else out["spend_at_leader_ratio"]
            near_leader_ratio = abs(candidate - out["spend_at_leader_ratio"]) <= 0.05 * out["spend_at_leader_ratio"]
            peer_spend = out.get("spend_at_peer_ratio")
            if (planned is None or near_leader_ratio) and (peer_spend is None or out["spend_at_leader_ratio"] < peer_spend):
                message = (
                    f"copying the leader's {_fmt_money(leader_ratio, currency)} per share point gives {_fmt_money(out['spend_at_leader_ratio'], currency)}"
                    + (f", below the peer ratio ({_fmt_money(peer_spend, currency)})" if peer_spend is not None else "")
                    + "; big brands buy share points cheaper, so a brand under half the leader's size is underfunded at the leader's ratio."
                )
                findings.append({"code": "leader_ratio_underfunds", "message": message})
    elif own["share"] is not None and competitors_n:
        missing.append("share_of_market_pct for competitors (needed for the spend-per-share-point comparison by brand size)")
    if category_total is not None and listed_total < category_total - 1e-9:
        findings.append({"code": "partial_category", "message": f"listed brands spend {_fmt_money(listed_total, currency)} of the {_fmt_money(category_total, currency)} category total; the rest is unnamed."})

    out["series"] = _sov_series(args, missing, warnings)
    # Summary.
    if own["share"] is not None:
        against = "the whole category" if coverage == "full" else f"the {competitors_n} named competitor{'s' if competitors_n != 1 else ''} only"
        summary = (
            f"Share of voice {own_row['sov_pct']:g} % against a {own['share']:g} % market share: excess share of voice {out['own']['esov_points']:+g} percentage points "
            f"({out['own']['verdict']}), measured against {against}."
        )
        if "spend_at_peer_ratio" in out:
            summary += f" Peers spend {_fmt_money(out['peers']['median_spend_per_share_point'], currency)} per share point ({_fmt_money(out['spend_at_peer_ratio'], currency)} at your share); the leader's ratio gives {_fmt_money(out['spend_at_leader_ratio'], currency)}."
        if "spend_for_target_esov" in out:
            summary += f" A +{target_esov:g}-point ESOV needs about {_fmt_money(out['spend_for_target_esov'], currency)}."
        summary += " Read share movement only after allowing for price, distribution and product changes."
    else:
        summary = f"Share of voice {own_row['sov_pct']:g} %; give the own market share to compute the excess share of voice."
    out["summary"] = summary
    return out


def _sov_series(args: dict, missing: list[str], warnings: list[str]) -> dict[str, Any] | None:
    raw = args.get("series")
    if not raw:
        return None
    periods = []
    for n, item in enumerate(_rows(args, "series", "period"), start=1):
        label = _text(item, "period", f"period {n}")
        spend = _num(item, "brand_spend", required=True, minimum=0.0, label=f"brand_spend in {label}")
        total = _num(item, "category_total_spend", required=True, minimum=0.0, label=f"category_total_spend in {label}")
        share = _num(item, "market_share_pct", required=True, minimum=0.0, label=f"market_share_pct in {label}")
        if total <= 0:
            raise ToolError(f"category_total_spend in {label} must be greater than zero")
        sov = spend / total * 100
        periods.append({"period": label, "sov_pct": round(sov, 1), "som_pct": share, "esov_points": round(sov - share, 1), "_esov": sov - share})
    pairs = []
    for cur, nxt in zip(periods, periods[1:]):
        change = nxt["som_pct"] - cur["som_pct"]
        cur["next_share_change_pts"] = round(change, 2)
        pairs.append((cur["_esov"], change))
    for p in periods:
        p.pop("_esov")
    block: dict[str, Any] = {"periods": periods, "pairs_n": len(pairs), "correlation": None}
    if len(pairs) < 3:
        block["correlation_note"] = "too few periods: at least 3 ESOV → next-period share-change pairs are needed for a correlation."
    else:
        xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
        sx, sy = statistics.pstdev(xs), statistics.pstdev(ys)
        if sx == 0 or sy == 0:
            block["correlation_note"] = "no variation in ESOV or in share change, so no correlation can be read."
        else:
            block["correlation"] = round(statistics.correlation(xs, ys), 2)
            block["correlation_note"] = f"Pearson r over {len(pairs)} pairs of ESOV and the following period's share change; a correlation, not an effect size."
    return block


# --- budget (#413, #414, #409) --------------------------------------------------


def _budget(args: dict) -> dict[str, Any]:
    currency = _text(args, "currency", "€")
    budget_total = _positive(args, "budget_total", "budget_total")
    if budget_total is None:
        budget_total = _positive(args, "budget_eur", "budget_total")
    market = _enum(args, "market", MARKETS, "market")
    missing: list[str] = []
    warnings: list[str] = []
    flags: list[str] = []
    benchmark_pct = _num(args, "benchmark_brand_pct", minimum=0.0, label="benchmark_brand_pct")
    if benchmark_pct is not None and benchmark_pct > 100:
        raise ToolError("benchmark_brand_pct must be between 0 and 100")
    benchmark_source = _text(args, "benchmark_source") or None
    benchmark: dict[str, Any] | None = None
    if benchmark_pct is not None:
        benchmark = {"brand_pct": benchmark_pct, "activation_pct": round(100 - benchmark_pct, 1), "source": benchmark_source, "sector": _text(args, "sector") or None}
        if benchmark_source is None:
            missing.append("benchmark_source (where the benchmark split comes from)")
    elif market is not None:
        benchmark = {"brand_pct": BENCHMARKS[market]["brand_pct"], "activation_pct": 100 - BENCHMARKS[market]["brand_pct"], "source": BENCHMARKS[market]["source"], "sector": market}
    else:
        missing.append("market (b2b or b2c) or benchmark_brand_pct: no benchmark split without it")
    if benchmark is not None:
        flags.append("benchmark_is_starting_scenario")
        if budget_total is not None:
            benchmark["brand_amount"] = _money(budget_total * benchmark["brand_pct"] / 100)
            benchmark["activation_amount"] = _money(budget_total - benchmark["brand_amount"])

    activities = []
    for n, item in enumerate(_rows(args, "activities", "activity", required=False), start=1):
        name = _text(item, "name", f"activity {n}")
        kind = _enum(item, "kind", KINDS, f"kind of {name}")
        cost = _num(item, "cost", minimum=0.0, label=f"cost of {name}")
        unit_cost = _num(item, "unit_cost", minimum=0.0, label=f"unit_cost of {name}")
        units = _num(item, "units", minimum=0.0, label=f"units of {name}")
        if cost is None and unit_cost is not None and units is not None:
            cost = unit_cost * units
        expected = _num(item, "expected_result", minimum=0.0, label=f"expected_result of {name}")
        activities.append(
            {
                "name": name,
                "kind": kind,
                "unit": _text(item, "unit") or None,
                "unit_cost": unit_cost,
                "units": units,
                "cost": cost,
                "expected_result": expected,
                "result_unit": _text(item, "result_unit") or None,
            }
        )
    out: dict[str, Any] = {"mode": "budget", "currency": currency, "budget_total": None if budget_total is None else _money(budget_total), "market": market, "benchmark": benchmark}
    if budget_total is None:
        missing.append("budget_total")

    rows = []
    uncosted, unkinded, no_result = [], [], []
    brand = activation = 0.0
    for a in activities:
        row: dict[str, Any] = {"name": a["name"], "kind": a["kind"], "cost": None if a["cost"] is None else _money(a["cost"]), "expected_result": a["expected_result"], "result_unit": a["result_unit"]}
        if a["unit_cost"] is not None:
            row["unit_cost"], row["units"], row["unit"] = a["unit_cost"], a["units"], a["unit"]
        if a["cost"] is None:
            uncosted.append(a["name"])
        else:
            if a["kind"] == "brand":
                brand += a["cost"]
            elif a["kind"] == "activation":
                activation += a["cost"]
            else:
                unkinded.append(a["name"])
            if a["expected_result"] is not None and a["expected_result"] > 0:
                row["cost_per_result"] = _money(a["cost"] / a["expected_result"])
        if a["expected_result"] is None:
            no_result.append(a["name"])
        rows.append(row)
    for name in uncosted:
        missing.append(f"unit_cost (or cost) for {name}")
    for name in unkinded:
        missing.append(f"kind (brand or activation) for {name}")
    for name in no_result:
        missing.append(f"expected_result for {name}")
    out["activities"] = rows
    costed_total = sum(a["cost"] for a in activities if a["cost"] is not None)
    out["total_cost"] = _money(costed_total)
    out["total_cost_is_partial"] = bool(uncosted)
    assigned = brand + activation
    if assigned > 0:
        brand_pct = round(brand / assigned * 100, 1)
        split: dict[str, Any] = {"brand_pct": brand_pct, "activation_pct": round(100 - brand_pct, 1), "brand_amount": _money(brand), "activation_amount": _money(activation)}
        if benchmark is not None:
            split["deviation_points"] = round(brand_pct - benchmark["brand_pct"], 1)
        if brand_pct < 20:
            flags.append("brand_below_20pct")
        if unkinded or uncosted:
            split["partial"] = True
        out["split"] = split
    elif activities:
        out["split"] = None

    # Affordability and the objective.
    if budget_total is not None and activities:
        if uncosted:
            out["affordable"] = "unknown"
            out["headroom"] = None
        else:
            out["affordable"] = costed_total <= budget_total + 1e-9
            out["headroom"] = _money(budget_total - costed_total)
            if costed_total > budget_total + 1e-9:
                out["shortfall"] = _money(costed_total - budget_total)
                flags.append("not_affordable")
    objective = args.get("objective")
    if objective is not None:
        if not isinstance(objective, dict):
            raise ToolError("objective must be an object {metric, target}")
        target = _positive(objective, "target", "objective.target", required=True)
        metric = _text(objective, "metric", "result")
        expected_total = sum(a["expected_result"] for a in activities if a["expected_result"] is not None)
        block: dict[str, Any] = {"metric": metric, "target": target, "expected_total": expected_total, "coverage_pct": round(expected_total / target * 100, 1)}
        if expected_total > 0 and not uncosted and activities:
            block["cost_per_result"] = _money(costed_total / expected_total)
        if block["coverage_pct"] < 100:
            block["verdict"] = f"objective not reached by the listed activities: {expected_total:g} of {target:g} {metric} ({block['coverage_pct']:g} %)."
            flags.append("objective_not_reached")
        else:
            block["verdict"] = f"listed activities cover the objective: {expected_total:g} of {target:g} {metric} ({block['coverage_pct']:g} %)."
        if no_result:
            block["note"] = "activities without expected_result count as 0 towards the objective."
        out["objective"] = block
    with_cpr = [r for r in rows if "cost_per_result" in r]
    if with_cpr:
        out["cut_candidates"] = [
            {"name": r["name"], "cost_per_result": r["cost_per_result"], "cost": r["cost"], "expected_result": r["expected_result"]}
            for r in sorted(with_cpr, key=lambda r: -r["cost_per_result"])
        ]
        out["cut_candidates_note"] = "most expensive per result first; cutting them loses the fewest results per euro saved."

    _budget_adjustments(args, out, benchmark, budget_total, missing, warnings)
    out["flags"] = flags
    out["missing"] = missing
    out["warnings"] = warnings
    out["summary"] = _budget_summary(out, activities, uncosted, currency)
    return out


def _budget_adjustments(args: dict, out: dict[str, Any], benchmark: dict[str, Any] | None, budget_total: float | None, missing: list[str], warnings: list[str]) -> None:
    raw = args.get("adjustments")
    if not raw:
        return
    if benchmark is None:
        missing.append("benchmark (market or benchmark_brand_pct) to apply adjustments to")
        return
    traits_raw = args.get("sector_traits") or []
    if not isinstance(traits_raw, list):
        raise ToolError("sector_traits must be a list of strings")
    traits = [str(t) for t in traits_raw if str(t).strip()]
    trait_stems = [_stems(t) for t in traits]
    max_points = _num(args, "max_total_points", minimum=0.0, label="max_total_points")
    if max_points is None:
        max_points = MAX_ADJUSTMENT_POINTS
    accepted, rejected = [], []
    seen: set[str] = set()
    for n, item in enumerate(_rows(args, "adjustments", "adjustment"), start=1):
        feature = _text(item, "feature", f"adjustment {n}")
        direction = _enum(item, "direction", KINDS, f"direction of adjustment '{feature}'", required=True)
        points = _num(item, "points", required=True, minimum=0.0, label=f"points of adjustment '{feature}'")
        row = {"feature": feature, "direction": direction, "points": points, "reason": _text(item, "reason") or None}
        key = " ".join(sorted(_stems(feature))) or feature.lower()
        if key in seen:
            rejected.append(row | {"rejected_because": "the same feature was already applied once; an adjustment counts only once"})
            continue
        seen.add(key)
        stems = _stems(feature)
        matched = [t for t, ts in zip(traits, trait_stems) if ts and ts <= stems]
        if matched:
            rejected.append(row | {"rejected_because": f"already in the sector average (sector trait: {matched[0]})"})
            continue
        if not traits:
            row["unverified_against_sector"] = True
        accepted.append(row)
    if not traits:
        warnings.append("sector_traits not given: the double-count check against the sector average did not run; every adjustment is marked unverified.")
    net = sum(a["points"] if a["direction"] == "brand" else -a["points"] for a in accepted)
    capped = False
    if abs(net) > max_points:
        warnings.append(f"Accepted adjustments move the split by {abs(net):g} points; capped at {max_points:g}.")
        net = math.copysign(max_points, net)
        capped = True
    brand_pct = min(100.0, max(0.0, benchmark["brand_pct"] + net))
    adjusted: dict[str, Any] = {
        "baseline_split": {"brand_pct": benchmark["brand_pct"], "activation_pct": round(100 - benchmark["brand_pct"], 1)},
        "final_split": {"brand_pct": round(brand_pct, 1), "activation_pct": round(100 - brand_pct, 1)},
        "net_points": round(net, 1),
        "capped": capped,
        "accepted": accepted,
        "rejected": rejected,
    }
    if budget_total is not None:
        adjusted["final_split"]["brand_amount"] = _money(budget_total * brand_pct / 100)
        adjusted["final_split"]["activation_amount"] = _money(budget_total - budget_total * brand_pct / 100)
    out["adjusted_benchmark"] = adjusted


def _budget_summary(out: dict[str, Any], activities: list[dict[str, Any]], uncosted: list[str], currency: str) -> str:
    parts = []
    benchmark = out.get("benchmark")
    if activities:
        if uncosted:
            parts.append(f"At least {_fmt_money(out['total_cost'], currency)} excluding {', '.join(uncosted)}; affordability unknown until their cost is given.")
        else:
            text = f"Total cost {_fmt_money(out['total_cost'], currency)}"
            if out.get("affordable") is True:
                text += f", within the {_fmt_money(out['budget_total'], currency)} budget with {_fmt_money(out['headroom'], currency)} headroom."
            elif out.get("affordable") is False:
                text += f", not affordable: {_fmt_money(out['shortfall'], currency)} over the {_fmt_money(out['budget_total'], currency)} budget."
            else:
                text += "."
            parts.append(text)
    objective = out.get("objective")
    if objective is not None:
        text = f"Expected {objective['expected_total']:g} {objective['metric']} = {objective['coverage_pct']:g} % of the {objective['target']:g} target"
        if "cost_per_result" in objective:
            text += f", {_fmt_money(objective['cost_per_result'], currency)} per {objective['metric']}"
        parts.append(text + ".")
    split = out.get("split")
    if split and benchmark is not None:
        parts.append(
            f"Brand {split['brand_pct']:g} % / activation {split['activation_pct']:g} % against the {benchmark['brand_pct']:g}/{benchmark['activation_pct']:g} "
            f"{benchmark.get('sector') or ''} benchmark ({split['deviation_points']:+g} points on brand); the benchmark is a starting scenario to test locally, not a target."
        )
    elif benchmark is not None and out.get("budget_total") is not None:
        parts.append(
            f"Benchmark split {benchmark['brand_pct']:g}/{benchmark['activation_pct']:g} = {_fmt_money(benchmark['brand_amount'], currency)} brand / "
            f"{_fmt_money(benchmark['activation_amount'], currency)} activation; a starting scenario to test locally, not a target."
        )
    adjusted = out.get("adjusted_benchmark")
    if adjusted is not None:
        final = adjusted["final_split"]
        text = f"Start at {adjusted['baseline_split']['brand_pct']:g}/{adjusted['baseline_split']['activation_pct']:g}; {len(adjusted['accepted'])} adjustment(s) accepted, {len(adjusted['rejected'])} dropped as already in the sector average, so {final['brand_pct']:g}/{final['activation_pct']:g}"
        if "brand_amount" in final:
            text += f": {_fmt_money(final['brand_amount'], currency)} brand, {_fmt_money(final['activation_amount'], currency)} activation"
        parts.append(text + ".")
    return " ".join(parts) if parts else "Nothing to compute yet: give budget_total, market and the activities."


# --- offers (#421, #422) --------------------------------------------------------


def _event_key(text: str | None) -> str | None:
    if not text:
        return None
    return re.sub(r"\s*\(.*?\)", "", str(text)).strip().lower() or None


def _conversion_path(start: str, target: str, rates: dict[tuple[str, str], float], labels: dict[str, str]) -> tuple[float, list[str]] | None:
    """Multiply step rates along the first path from the payment event to the target event."""
    frontier = [(start, 1.0, [labels.get(start, start)])]
    seen = {start}
    while frontier:
        node, product, path = frontier.pop(0)
        if node == target:
            return product, path
        for (a, b), rate in rates.items():
            if a == node and b not in seen:
                seen.add(b)
                frontier.append((b, product * rate / 100, path + [labels.get(b, b)]))
    return None


def _offers(args: dict) -> dict[str, Any]:
    currency = _text(args, "currency", "€")
    missing: list[str] = []
    warnings: list[str] = []
    target_event = _text(args, "target_event") or None
    target_key = _event_key(target_event)
    rates: dict[tuple[str, str], float] = {}
    labels: dict[str, str] = {}
    for n, item in enumerate(_rows(args, "step_rates", "step rate", required=False), start=1):
        a, b = _text(item, "from"), _text(item, "to")
        if not a or not b:
            raise ToolError(f"step_rates item {n} needs from and to")
        rate = _num(item, "rate_pct", required=True, minimum=0.0, label=f"rate_pct {a}→{b}")
        if rate > 100:
            raise ToolError(f"rate_pct {a}→{b} must be between 0 and 100")
        rates[(_event_key(a), _event_key(b))] = rate
        labels[_event_key(a)], labels[_event_key(b)] = a, b
    if target_event:
        labels.setdefault(target_key, target_event)

    rows = []
    payment_events: set[str] = set()
    for n, item in enumerate(_rows(args, "offers", "offer"), start=1):
        name = _text(item, "name", f"offer {n}")
        price = _positive(item, "price", f"price of {name}", required=True)
        basis = _enum(item, "price_basis", PRICE_BASES, f"price_basis of {name}", required=True)
        quantity = _positive(item, "quantity", f"quantity of {name}")
        viewability = _num(item, "viewability_pct", minimum=0.0, label=f"viewability_pct of {name}")
        if viewability is not None and (viewability > 100 or viewability == 0):
            raise ToolError(f"viewability_pct of {name} must be above 0 and at most 100")
        ctr = _num(item, "ctr_pct", minimum=0.0, label=f"ctr_pct of {name}")
        conversion = _num(item, "conversion_pct", minimum=0.0, label=f"conversion_pct of {name}")
        payment_event = _text(item, "payment_event") or None
        row: dict[str, Any] = {"name": name, "basis_quoted": basis, "price": price, "quantity": quantity, "derived_from": []}
        if quantity is None and basis in ("cpm_served", "cpm_viewable"):
            missing.append(f"quantity for {name} (impressions the price buys; needed for the total cost)")
        if basis in ("cpm_served", "cpm_viewable"):
            served = viewable = None
            if basis == "cpm_served":
                served = quantity
                if viewability is not None and served is not None:
                    viewable = served * viewability / 100
                    row["derived_from"].append(f"viewable = served × {viewability:g} %")
                elif viewability is None:
                    missing.append(f"viewability_pct for {name}: served CPM cannot be converted to viewable CPM")
                    row["conditional_vcpm"] = {f"{v} %": _money(price / (v / 100)) for v in VIEWABILITY_STEPS}
            else:
                viewable = quantity
                if viewability is not None and viewable is not None:
                    served = viewable / (viewability / 100)
                    row["derived_from"].append(f"served = viewable ÷ {viewability:g} %")
            if quantity is not None:
                row["total_cost"] = _money(price * quantity / 1000)
            row["served_impressions"] = None if served is None else round(served)
            row["viewable_impressions"] = None if viewable is None else round(viewable)
            if served is not None and "total_cost" in row and served > 0:
                row["cpm_served"] = _money(row["total_cost"] / served * 1000)
            if viewable is not None and "total_cost" in row and viewable > 0:
                row["vcpm"] = _money(row["total_cost"] / viewable * 1000)
            if ctr is not None and served is not None and "total_cost" in row and ctr > 0:
                clicks = served * ctr / 100
                row["clicks"] = round(clicks)
                row["cpc"] = _money(row["total_cost"] / clicks)
                row["derived_from"].append(f"clicks = served × {ctr:g} %")
                if conversion is not None and conversion > 0:
                    events = clicks * conversion / 100
                    row["events"] = round(events, 1)
                    row["cpa"] = _money(row["total_cost"] / events)
                    row["derived_from"].append(f"events = clicks × {conversion:g} %")
        elif basis == "cpc":
            row["cpc"] = price
            if quantity is not None:
                row["total_cost"] = _money(price * quantity)
                row["clicks"] = round(quantity)
            if conversion is not None and conversion > 0:
                row["cpa"] = _money(price / (conversion / 100))
                row["derived_from"].append(f"cpa = cpc ÷ {conversion:g} %")
        else:  # cpl / cpa
            row[basis] = price
            if quantity is not None:
                row["total_cost"] = _money(price * quantity)
                row["events"] = round(quantity)
            if payment_event is None:
                missing.append(f"payment_event for {name}: a {basis.upper()} price means nothing until the paid-for event is named")
                row["unranked_because"] = "payment event not named"
            else:
                row["payment_event"] = payment_event
                payment_events.add(_event_key(payment_event))
                row["contract_check"] = f"{name} pays on '{payment_event}' — confirm what counts as one (form submit, qualified call, paid order) before signing."
                if target_key is not None:
                    if _event_key(payment_event) == target_key:
                        row["cost_per_target_event"] = price
                        row["conversion_path"] = [payment_event]
                    else:
                        path = _conversion_path(_event_key(payment_event), target_key, rates, labels)
                        if path is None or path[0] <= 0:
                            missing.append(f"rate {payment_event}→{target_event} for {name}")
                            row["conditional_cost_per_target_event"] = {f"{r} %": _money(price / (r / 100)) for r in RATE_STEPS}
                            row["unranked_because"] = f"no conversion rate from '{payment_event}' to '{target_event}'"
                        else:
                            row["cost_per_target_event"] = _money(price / path[0])
                            row["conversion_path"] = path[1]
                            row["derived_from"].append(f"{price:g} ÷ {path[0] * 100:.4g} % ({' → '.join(path[1])})")
        rows.append(row)

    event_based = [r for r in rows if r["basis_quoted"] in ("cpl", "cpa")]
    events_equal = len(payment_events) <= 1 if event_based else None
    event_mismatch = bool(event_based) and len(payment_events) > 1
    if event_based and target_key is None:
        if event_mismatch:
            missing.append("target_event and step_rates: the offers pay on different events, so their prices are not comparable as given")
        else:
            missing.append("target_event (the event you actually want to pay for)")
    compare_on = _enum(args, "compare_on", COMPARE_BASES, "compare_on")
    if compare_on is None:
        if target_key is not None and event_based:
            compare_on = "cost_per_target_event"
        else:
            for candidate in ("vcpm", "cpm_served", "cpc", "cpa", "cpl"):
                if all(candidate in r for r in rows):
                    compare_on = candidate
                    break
    ranking = []
    if compare_on is not None:
        ranked = [r for r in rows if compare_on in r and "unranked_because" not in r]
        if compare_on == "cost_per_target_event" and events_equal is False and target_key is None:
            ranked = []
        ranked.sort(key=lambda r: r[compare_on])
        ranking = [{"rank": i, "name": r["name"], compare_on: r[compare_on]} for i, r in enumerate(ranked, start=1)]
        left_out = [r["name"] for r in rows if r not in ranked]
        if left_out:
            warnings.append(f"Not ranked on {compare_on}: {', '.join(left_out)} (see missing).")
    else:
        warnings.append("The offers share no common denominator yet: give viewability_pct, ctr_pct / conversion_pct or target_event with step_rates.")
    out: dict[str, Any] = {
        "mode": "offers",
        "currency": currency,
        "target_event": target_event,
        "compare_on": compare_on,
        "rows": rows,
        "ranking": ranking,
        "cheapest": ranking[0]["name"] if ranking else None,
        "events_equal": events_equal,
        "event_mismatch": event_mismatch,
        "contract_check": [r["contract_check"] for r in rows if "contract_check" in r],
        "missing": missing,
        "warnings": warnings,
    }
    if event_mismatch:
        out["contract_check"].append("The offers use different payment events; the nominal prices are not comparable until converted to " + (f"'{target_event}'." if target_event else "one target event."))
    # Summary.
    if len(ranking) >= 1 and compare_on is not None:
        label = {"vcpm": "per viewable thousand", "cpm_served": "per served thousand", "cpc": "per click", "cpa": "per acquisition", "cpl": "per lead", "cost_per_target_event": f"per {target_event or 'target event'}"}[compare_on]
        first = ranking[0]
        summary = f"Cheapest {label}: {first['name']} ({_fmt_money(first[compare_on], currency)})"
        if len(ranking) > 1:
            summary += ", then " + ", ".join(f"{r['name']} ({_fmt_money(r[compare_on], currency)})" for r in ranking[1:])
        summary += "."
        cheapest_quoted = min(rows, key=lambda r: r["price"])
        if cheapest_quoted["name"] != first["name"]:
            summary += f" {cheapest_quoted['name']} only looks cheapest on its quoted {cheapest_quoted['basis_quoted'].replace('_', ' ')} price."
        if len(ranking) < len(rows):
            summary += " Not ranked: " + ", ".join(r["name"] for r in rows if r["name"] not in {x["name"] for x in ranking}) + " (see missing)."
    else:
        summary = "No ranking: the offers cannot be put on one denominator with the figures given (see missing)."
    if event_mismatch:
        summary += " The contracts pay on different events — confirm each definition before signing."
    out["summary"] = summary
    return out


# --- volume (#426) ------------------------------------------------------------


_FACTORS = ("population", "penetration", "frequency", "basket")


def _volume(args: dict) -> dict[str, Any]:
    unit = _text(args, "unit", "units")
    missing: list[str] = []
    warnings: list[str] = []
    raw = _rows(args, "periods", "period")
    if len(raw) < 2:
        raise ToolError("periods needs at least two periods (or two markets / brands) to decompose a change")
    periods = []
    for n, item in enumerate(raw, start=1):
        label = _text(item, "label", f"period {n}")
        population = _positive(item, "population", f"population in {label}")
        penetration = _num(item, "penetration_pct", minimum=0.0, label=f"penetration_pct in {label}")
        if penetration is not None and penetration > 100:
            raise ToolError(f"penetration_pct in {label} must be between 0 and 100")
        buyers = _num(item, "buyers", minimum=0.0, label=f"buyers in {label}")
        if buyers is None and penetration is not None and population is not None:
            buyers = population * penetration / 100
        elif penetration is None and buyers is not None and population:
            penetration = buyers / population * 100
        frequency = _positive(item, "purchases_per_buyer", f"purchases_per_buyer in {label}")
        basket = _positive(item, "units_per_purchase", f"units_per_purchase in {label}")
        reported = _num(item, "reported_volume", minimum=0.0, label=f"reported_volume in {label}")
        row: dict[str, Any] = {"label": label, "population": population, "penetration_pct": penetration, "buyers": buyers, "purchases_per_buyer": frequency, "units_per_purchase": basket, "reported_volume": reported, "derived": []}
        known = {"population": population, "buyers": buyers, "frequency": frequency, "basket": basket}
        absent = [k for k, v in known.items() if v is None]
        if len(absent) == 1 and reported is not None and reported > 0:
            key = absent[0]
            others = math.prod(v for k, v in known.items() if k != key and k != "population")
            if key == "population":
                if penetration is None or penetration <= 0:
                    missing.append(f"population or penetration_pct in {label}")
                else:
                    buyers = reported / (frequency * basket)
                    population = buyers / (penetration / 100)
                    row["buyers"], row["population"] = buyers, population
                    row["derived"].append("population (from reported volume and penetration)")
            elif key == "buyers":
                buyers = reported / others
                row["buyers"] = buyers
                row["penetration_pct"] = buyers / population * 100
                row["derived"].append("buyers and penetration_pct (from reported volume)")
            elif key == "frequency":
                frequency = reported / (buyers * basket) if buyers else None
                row["purchases_per_buyer"] = frequency
                row["derived"].append("purchases_per_buyer (from reported volume)")
            else:
                basket = reported / (buyers * frequency) if buyers else None
                row["units_per_purchase"] = basket
                row["derived"].append("units_per_purchase (from reported volume)")
        elif absent:
            for key in absent:
                name = {"population": "population", "buyers": "penetration_pct or buyers", "frequency": "purchases_per_buyer", "basket": "units_per_purchase"}[key]
                missing.append(f"{name} in {label}" + ("" if reported is None else " (two factors missing, so reported volume cannot fill the gap)"))
        if row["buyers"] is not None and row["purchases_per_buyer"] is not None and row["units_per_purchase"] is not None:
            row["computed_volume"] = row["buyers"] * row["purchases_per_buyer"] * row["units_per_purchase"]
            if reported is not None and reported > 0:
                row["reconciliation_pct"] = round(row["computed_volume"] / reported * 100, 1)
                gap = abs(row["reconciliation_pct"] - 100)
                if gap > 5 and not row["derived"]:
                    warnings.append(
                        f"{label}: the factors reproduce {row['computed_volume']:,.0f} {unit} against {reported:,.0f} reported, a {gap:g} % gap — the panel and the sales figures do not describe the same market; check them before acting."
                    )
        else:
            row["computed_volume"] = None
        periods.append(row)

    changes = []
    for prev, cur in zip(periods, periods[1:]):
        change: dict[str, Any] = {"from": prev["label"], "to": cur["label"]}
        if prev["computed_volume"] and cur["computed_volume"]:
            change["change_pct"] = round((cur["computed_volume"] / prev["computed_volume"] - 1) * 100, 1)
        values = {
            "population": (prev["population"], cur["population"]),
            "penetration": (prev["penetration_pct"], cur["penetration_pct"]),
            "frequency": (prev["purchases_per_buyer"], cur["purchases_per_buyer"]),
            "basket": (prev["units_per_purchase"], cur["units_per_purchase"]),
        }
        logs: dict[str, float] = {}
        skipped = []
        for factor, (a, b) in values.items():
            if a is None or b is None or a <= 0 or b <= 0:
                skipped.append(factor)
                continue
            logs[factor] = math.log(b / a)
        total_log = sum(logs.values())
        change["factor_change_pct"] = {f: round((math.exp(v) - 1) * 100, 1) for f, v in logs.items()}
        if abs(total_log) < 1e-12:
            change["attribution"] = {f: 0.0 for f in logs}
            change["main_driver"] = None
            change["note"] = "no change in the modelled volume between these periods."
        else:
            attribution = {f: round(v / total_log * 100, 1) for f, v in logs.items()}
            change["attribution"] = attribution
            change["main_driver"] = max(logs, key=lambda f: abs(logs[f]))
            change["note"] = "share of the log change in volume explained by each factor (sums to 100; a factor moving against the total shows negative)."
        if skipped:
            change["partial"] = True
            change["note"] = change.get("note", "") + f" Partial: {', '.join(skipped)} not available in both periods."
        changes.append(change)

    out_periods = []
    for p in periods:
        out_periods.append(
            {
                "label": p["label"],
                "population": None if p["population"] is None else round(p["population"]),
                "penetration_pct": None if p["penetration_pct"] is None else round(p["penetration_pct"], 2),
                "buyers": None if p["buyers"] is None else round(p["buyers"]),
                "purchases_per_buyer": None if p["purchases_per_buyer"] is None else round(p["purchases_per_buyer"], 3),
                "units_per_purchase": None if p["units_per_purchase"] is None else round(p["units_per_purchase"], 3),
                "computed_volume": None if p["computed_volume"] is None else round(p["computed_volume"]),
                "reported_volume": p["reported_volume"],
                "reconciliation_pct": p.get("reconciliation_pct"),
                "derived": p["derived"],
            }
        )
    out: dict[str, Any] = {"mode": "volume", "unit": unit, "periods": out_periods, "changes": changes, "missing": missing, "warnings": warnings}
    # Summary from the last change.
    last = changes[-1] if changes else None
    if last and "change_pct" in last and last.get("main_driver"):
        names = {"population": "population", "penetration": "penetration (more buyers)", "frequency": "frequency (purchases per buyer)", "basket": "units per purchase"}
        parts = ", ".join(f"{last['attribution'][f]:g} % {names[f]}" for f in _FACTORS if f in last["attribution"] and last["attribution"][f] != 0)
        summary = f"Modelled volume moved {last['change_pct']:+g} % from {last['from']} to {last['to']}; of the change, {parts}. Main driver: {names[last['main_driver']]}."
        if warnings:
            summary += " The factors do not reproduce the reported volume — check the panel against the sales figures before acting."
    elif last and last.get("note"):
        summary = f"{last['from']} → {last['to']}: {last['note']}"
    else:
        summary = "Volume could not be modelled: see missing."
    out["summary"] = summary
    return out


# --- calendar (#419) ----------------------------------------------------------


def _easter(year: int) -> date:
    """Western Easter Sunday (anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def occasion_date(key: str, year: int) -> tuple[date, str] | None:
    """Date and description of a known occasion key in a year; None when unknown."""
    norm = str(key).strip().lower().replace(" ", "_").replace("-", "_")
    norm = _OCCASION_ALIASES.get(norm, norm)
    if norm in _FIXED_OCCASIONS:
        month, day, label = _FIXED_OCCASIONS[norm]
        return date(year, month, day), label
    if norm == "motinos_diena":
        return _nth_weekday(year, 5, 6, 1), "Motinos diena (first Sunday of May, Lithuania)"
    if norm == "tevo_diena":
        return _nth_weekday(year, 6, 6, 1), "Tėvo diena (first Sunday of June, Lithuania)"
    if norm == "velykos":
        return _easter(year), "Velykos (Western Easter Sunday)"
    if norm == "black_friday":
        return _nth_weekday(year, 11, 3, 4) + timedelta(days=1), "Black Friday (day after the fourth Thursday of November)"
    return None


def _parse_date(raw: Any, year: int) -> date | None:
    text = str(raw).strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        return date(y, mo, d)
    m = re.fullmatch(r"(\d{1,2})[-./](\d{1,2})", text)
    if m:
        mo, d = (int(x) for x in m.groups())
        return date(year, mo, d)
    return None


def _calendar(args: dict) -> dict[str, Any]:
    currency = _text(args, "currency", "€")
    year_raw = args.get("year")
    if year_raw in (None, ""):
        raise ToolError("year is required, e.g. 2027")
    try:
        year = int(str(year_raw).strip())
    except ValueError as exc:
        raise ToolError(f"year must be a whole number, got {year_raw!r}") from exc
    if not 2000 <= year <= 2100:
        raise ToolError("year must be between 2000 and 2100")
    missing: list[str] = []
    warnings: list[str] = []
    findings: list[dict[str, Any]] = []
    always = args.get("always_on") or {}
    if not isinstance(always, dict):
        raise ToolError("always_on must be an object {weekly_budget, posts_per_week, content_types?}")
    weekly_budget = _num(always, "weekly_budget", minimum=0.0, label="always_on.weekly_budget")
    if weekly_budget is None:
        weekly_budget = _num(always, "weekly_budget_eur", minimum=0.0, label="always_on.weekly_budget")
    posts_per_week = _num(always, "posts_per_week", minimum=0.0, label="always_on.posts_per_week")
    if weekly_budget is None:
        missing.append("always_on.weekly_budget (the calendar has no always-on money column without it)")
    if posts_per_week is None:
        missing.append("always_on.posts_per_week")
    total_budget = _num(args, "total_budget", minimum=0.0, label="total_budget")
    if total_budget is None:
        total_budget = _num(args, "total_budget_eur", minimum=0.0, label="total_budget")
    team_max = _num(args, "team_posts_per_week_max", minimum=0.0, label="team_posts_per_week_max")

    # ISO weeks of the year.
    weeks_n = date(year, 12, 28).isocalendar()[1]
    week1_monday = date.fromisocalendar(year, 1, 1)
    weeks: list[dict[str, Any]] = []
    for w in range(1, weeks_n + 1):
        start = week1_monday + timedelta(weeks=w - 1)
        weeks.append({"iso_week": w, "start": start, "end": start + timedelta(days=6), "burst": 0.0, "flights": [], "extra_posts": 0.0})

    flights: list[dict[str, Any]] = []
    for n, item in enumerate(_rows(args, "occasions", "occasion", required=False), start=1):
        name = _text(item, "name", f"occasion {n}")
        raw_date = item.get("date")
        key = item.get("key") or (raw_date if raw_date not in (None, "") and _parse_date(raw_date, year) is None else None)
        when: date | None = None
        description = None
        if raw_date not in (None, ""):
            try:
                when = _parse_date(raw_date, year)
            except ValueError:
                when = None
        if when is None and key not in (None, ""):
            resolved = occasion_date(str(key), year)
            if resolved is not None:
                when, description = resolved
        if when is None:
            findings.append({"code": "date_unresolved", "occasion": name, "text": f"{name}: no date given and no known key; give date YYYY-MM-DD or one of {', '.join(KNOWN_OCCASIONS)}."})
            missing.append(f"date for {name}")
            continue
        lead = _num(item, "lead_days", minimum=0.0, label=f"lead_days of {name}")
        if lead is None:
            missing.append(f"lead_days for {name} (counted as 0)")
            lead = 0.0
        duration = _num(item, "duration_days", minimum=1.0, label=f"duration_days of {name}")
        if duration is None:
            duration = 1.0
        burst = _num(item, "burst_budget", minimum=0.0, label=f"burst_budget of {name}")
        if burst is None:
            burst = _num(item, "burst_budget_eur", minimum=0.0, label=f"burst_budget of {name}")
        if burst is None:
            missing.append(f"burst_budget for {name} (counted as 0)")
            burst = 0.0
        extra_posts = _num(item, "posts", minimum=0.0, label=f"posts of {name}") or 0.0
        start = when - timedelta(days=int(lead))
        end = when + timedelta(days=int(duration) - 1)
        days = (end - start).days + 1
        flight = {"name": name, "date": when, "description": description, "start": start, "end": end, "days": days, "lead_days": int(lead), "duration_days": int(duration), "burst_budget": burst, "posts": extra_posts, "channels": item.get("channels") or []}
        flights.append(flight)
        if lead < 7:
            findings.append({"code": "lead_too_short", "occasion": name, "text": f"{name} starts only {int(lead)} day(s) before {when.isoformat()}: under a week to build reach."})
        if when.year != year:
            warnings.append(f"{name} falls in {when.year}, outside the {year} calendar; only the days inside {year} are counted.")
        per_day = burst / days if days > 0 else 0.0
        posts_per_day = extra_posts / days if days > 0 else 0.0
        for week in weeks:
            overlap_start, overlap_end = max(start, week["start"]), min(end, week["end"])
            if overlap_start <= overlap_end:
                n_days = (overlap_end - overlap_start).days + 1
                week["burst"] += per_day * n_days
                week["extra_posts"] += posts_per_day * n_days
                week["flights"].append(name)
    for a, b in combinations(flights, 2):
        shared_start, shared_end = max(a["start"], b["start"]), min(a["end"], b["end"])
        if shared_start <= shared_end:
            shared = (shared_end - shared_start).days + 1
            findings.append({"code": "flight_overlap", "occasion": f"{a['name']} / {b['name']}", "text": f"{a['name']} and {b['name']} overlap for {shared} day(s) ({shared_start.isoformat()}–{shared_end.isoformat()}): one audience, two messages."})

    rows = []
    always_total = 0.0
    uncovered = []
    capacity_short = []
    for week in weeks:
        base_budget = weekly_budget or 0.0
        base_posts = posts_per_week or 0.0
        always_total += base_budget
        mode = "both" if week["flights"] and base_budget + base_posts > 0 else ("flight" if week["flights"] else "always_on")
        posts = base_posts + week["extra_posts"]
        if posts == 0 and base_budget == 0 and not week["flights"]:
            uncovered.append(week["iso_week"])
        if team_max is not None and posts > team_max + 1e-9:
            capacity_short.append(week["iso_week"])
        rows.append(
            {
                "iso_week": week["iso_week"],
                "start": week["start"].isoformat(),
                "mode": mode,
                "always_on_budget": _money(base_budget),
                "burst_budget": _money(week["burst"]),
                "budget": _money(base_budget + week["burst"]),
                "posts": round(posts, 1),
                "occasions": week["flights"],
            }
        )
    flighted_total = sum(f["burst_budget"] for f in flights)
    plan_total = always_total + flighted_total
    totals: dict[str, Any] = {
        "weeks": weeks_n,
        "always_on": _money(always_total),
        "flighted": _money(flighted_total),
        "plan_total": _money(plan_total),
        "share_flighted_pct": _pct(flighted_total, plan_total),
        "always_on_posts": None if posts_per_week is None else round(posts_per_week * weeks_n),
    }
    if total_budget is not None:
        totals["total_budget"] = _money(total_budget)
        totals["unallocated"] = _money(total_budget - plan_total)
        if plan_total > total_budget + 0.005:
            findings.append({"code": "budget_over_cap", "text": f"plan exceeds {_fmt_money(total_budget, currency)} by {_fmt_money(plan_total - total_budget, currency)}."})
    else:
        missing.append("total_budget (the cap the plan is checked against)")
    if uncovered:
        findings.append({"code": "week_uncovered", "text": f"{len(uncovered)} week(s) with no always-on post and no flight: {', '.join(f'W{w}' for w in uncovered[:8])}{'…' if len(uncovered) > 8 else ''}."})
    if capacity_short:
        findings.append({"code": "capacity_short", "text": f"posts exceed the team's {team_max:g} per week in {len(capacity_short)} week(s): {', '.join(f'W{w}' for w in capacity_short[:8])}{'…' if len(capacity_short) > 8 else ''}."})
    status = "needs_input" if any(f["code"] == "date_unresolved" for f in findings) else "ok"
    out_flights = [
        {
            "name": f["name"],
            "date": f["date"].isoformat(),
            "description": f["description"],
            "start": f["start"].isoformat(),
            "end": f["end"].isoformat(),
            "days": f["days"],
            "lead_days": f["lead_days"],
            "duration_days": f["duration_days"],
            "burst_budget": _money(f["burst_budget"]),
            "posts": f["posts"],
            "channels": f["channels"],
        }
        for f in flights
    ]
    summary = (
        f"{year}: {weeks_n} weeks, always-on {_fmt_money(always_total, currency)}"
        + (f" ({posts_per_week:g} posts/week)" if posts_per_week is not None else "")
        + f" plus {len(flights)} flight(s) worth {_fmt_money(flighted_total, currency)} ({totals['share_flighted_pct'] or 0:g} % of the plan)."
    )
    if total_budget is not None:
        summary += (
            f" Plan {_fmt_money(plan_total, currency)} against a {_fmt_money(total_budget, currency)} budget: "
            + (f"{_fmt_money(total_budget - plan_total, currency)} unallocated." if plan_total <= total_budget + 0.005 else f"{_fmt_money(plan_total - total_budget, currency)} over the cap.")
        )
    if status == "needs_input":
        summary += " Some occasions have no date yet (see missing); the plan covers the rest."
    overlaps = [f for f in findings if f["code"] == "flight_overlap"]
    if overlaps:
        summary += f" {len(overlaps)} flight overlap(s) flagged."
    return {
        "mode": "calendar",
        "status": status,
        "year": year,
        "currency": currency,
        "always_on": {"weekly_budget": weekly_budget, "posts_per_week": posts_per_week, "content_types": always.get("content_types") or []},
        "flights": out_flights,
        "weeks": rows,
        "totals": totals,
        "findings": findings,
        "known_occasions": list(KNOWN_OCCASIONS),
        "missing": missing,
        "warnings": warnings,
        "summary": summary,
    }


# --- channel_fit (#420) --------------------------------------------------------


def _channel_fit(args: dict) -> dict[str, Any]:
    platform = _text(args, "platform")
    if not platform:
        raise ToolError("platform is required, e.g. TikTok")
    segment = _text(args, "segment") or None
    job = _text(args, "content_job") or None
    missing: list[str] = []
    warnings: list[str] = []
    findings: list[dict[str, Any]] = []
    if segment is None:
        missing.append("segment (who the content is for)")
    if job is None:
        missing.append("content_job (what the audience uses the platform for: inspiration, buying, service)")
    evidence_rows = _rows(args, "evidence", "evidence item", required=False)
    evidence_out = []
    sourced_n = 0
    job_hits = 0
    job_words = _tokens(job) if job else set()

    def _same_stem(a: str, b: str) -> bool:
        n = min(len(a), len(b), 4)
        return n >= 3 and a[:n] == b[:n]

    for n, item in enumerate(evidence_rows, start=1):
        text = _text(item, "text")
        url = _text(item, "source_url") or None
        if not text:
            raise ToolError(f"evidence item {n} needs text")
        row = {"text": text, "source_url": url, "sourced": bool(url)}
        if url:
            sourced_n += 1
        if job_words:
            words = _tokens(text)
            row["mentions_job"] = any(_same_stem(w, j) for w in words for j in job_words)
            if row["mentions_job"]:
                job_hits += 1
        evidence_out.append(row)
    if not evidence_rows:
        evidence_status = "missing"
        findings.append({"code": "evidence_missing", "text": f"no evidence that {segment or 'the segment'} use {platform}" + (f" for {job}" if job else "") + "; the question to research: what share of {0} use {1} and for what, with a source URL.".format(segment or "the target segment", platform)})
        missing.append(f"evidence [{{text, source_url}}] of {segment or 'the segment'} using {platform}" + (f" for {job}" if job else ""))
    elif sourced_n < len(evidence_rows):
        evidence_status = "unsourced"
        findings.append({"code": "evidence_unsourced", "text": f"{len(evidence_rows) - sourced_n} of {len(evidence_rows)} evidence item(s) have no source_url; an unsourced statement does not back a platform decision."})
    else:
        evidence_status = "sourced"
    if evidence_rows and job and job_hits == 0:
        findings.append({"code": "platform_job_mismatch", "text": f"the evidence speaks of presence on {platform}, not of using it for {job}; find usage-for-the-job evidence or say the fit is unproven."})

    posts = _num(args, "target_posts_per_week", minimum=0.0, label="target_posts_per_week")
    if posts is None:
        posts = _num(args, "posts_per_week", minimum=0.0, label="target_posts_per_week")
    hours_per_post = _num(args, "hours_per_post", minimum=0.0, label="hours_per_post")
    team_hours = _num(args, "team_hours_per_week", minimum=0.0, label="team_hours_per_week")
    existing_out = []
    existing_hours = 0.0
    for n, item in enumerate(_rows(args, "existing_platforms", "existing platform", required=False), start=1):
        name = _text(item, "name", f"platform {n}")
        hours = _num(item, "hours_per_week", minimum=0.0, label=f"hours_per_week of {name}")
        if hours is None:
            missing.append(f"hours_per_week for existing platform {name} (counted as 0)")
            hours = 0.0
        existing_hours += hours
        existing_out.append({"name": name, "posts_per_week": _num(item, "posts_per_week", minimum=0.0, label=f"posts_per_week of {name}"), "hours_per_week": hours})
    capacity: dict[str, Any] = {"status": "not_assessed"}
    if posts is None or hours_per_post is None or team_hours is None:
        for key, value in (("target_posts_per_week", posts), ("hours_per_post", hours_per_post), ("team_hours_per_week", team_hours)):
            if value is None:
                missing.append(f"{key} (capacity not assessed without it)")
    else:
        required = posts * hours_per_post
        available = team_hours - existing_hours
        ratio = None if available <= 0 else round(required / available, 2)
        capacity = {
            "status": "assessed",
            "required_hours_per_week": round(required, 2),
            "existing_hours_per_week": round(existing_hours, 2),
            "available_hours_after_existing": round(available, 2),
            "capacity_ratio": "inf" if available <= 0 and required > 0 else ratio,
        }
        if required == 0:
            capacity["status_label"] = "nothing planned"
        elif available <= 0 or required > available + 1e-9:
            capacity["status_label"] = "short"
            findings.append({"code": "capacity_short", "text": f"{platform} needs {required:g} h/week; {max(available, 0):g} h/week are left after {existing_hours:g} h on existing platforms."})
            findings.append({"code": "cadence_unsustainable", "text": f"{posts:g} posts/week at {hours_per_post:g} h each cannot be kept with the hours available; cut the cadence, the hours per post or an existing platform first."})
        elif required >= 0.7 * available:
            capacity["status_label"] = "stretch"
        else:
            capacity["status_label"] = "fits"

    label = capacity.get("status_label")
    if label == "short":
        verdict = "unfit"
    elif evidence_status != "sourced" or any(f["code"] == "platform_job_mismatch" for f in findings):
        verdict = "unknown"
    elif label == "stretch":
        verdict = "stretch"
    elif label in ("fits", "nothing planned"):
        verdict = "fit"
    else:
        verdict = "unknown"
    reasons = []
    if label == "short":
        reasons.append(f"capacity: {capacity['required_hours_per_week']:g} h/week needed, {max(capacity['available_hours_after_existing'], 0):g} h/week left")
    elif label:
        reasons.append(f"capacity {label}: {capacity['required_hours_per_week']:g} of {max(capacity['available_hours_after_existing'], 0):g} h/week")
    else:
        reasons.append("capacity not assessed")
    reasons.append(f"audience evidence {evidence_status}" + (" but not about the job" if any(f["code"] == "platform_job_mismatch" for f in findings) else ""))
    summary = f"{platform} for {segment or 'the segment'}" + (f" ({job})" if job else "") + f": {verdict} — " + "; ".join(reasons) + "."
    if verdict == "unknown":
        summary += f" Do not recommend {platform} until a sourced statement shows {segment or 'the segment'} use it" + (f" for {job}" if job else "") + "."
    return {
        "mode": "channel_fit",
        "platform": platform,
        "segment": segment,
        "content_job": job,
        "evidence_status": evidence_status,
        "evidence": evidence_out,
        "capacity": capacity,
        "existing_platforms": existing_out,
        "verdict": verdict,
        "findings": findings,
        "missing": missing,
        "warnings": warnings,
        "summary": summary,
    }


# --- classify (#410, rule-based) -----------------------------------------------


def classify_creative(text: str | None) -> tuple[str, list[str]]:
    """Job of one creative from its own words: activation markers counted by kind."""
    if text is None or not str(text).strip():
        return "unclassified", []
    markers = [code for code, pattern in _ACTIVATION_MARKERS if pattern.search(str(text))]
    if len(markers) >= 2:
        return "activation", markers
    if len(markers) == 1:
        return "mixed", markers
    return "brand", markers


def _classify(args: dict) -> dict[str, Any]:
    currency = _text(args, "currency", "€")
    missing: list[str] = []
    warnings: list[str] = []
    rows_out = []
    totals: dict[str, float] = {"brand": 0.0, "activation": 0.0, "mixed": 0.0, "unclassified": 0.0}
    by_channel: dict[str, dict[str, float]] = defaultdict(lambda: {"brand": 0.0, "activation": 0.0, "mixed": 0.0, "unclassified": 0.0})
    for n, item in enumerate(_rows(args, "rows", "spend row"), start=1):
        campaign = _text(item, "campaign", f"row {n}")
        channel = _text(item, "channel", "(no channel)")
        spend = _num(item, "spend", required=True, minimum=0.0, label=f"spend of {campaign}")
        creative = item.get("creative_text")
        if creative in (None, ""):
            creative = item.get("creative")
        job, markers = classify_creative(None if creative in (None, "") else str(creative))
        if job == "unclassified":
            missing.append(f"creative text for {campaign} ({channel})")
        totals[job] += spend
        by_channel[channel][job] += spend
        rows_out.append({"campaign": campaign, "channel": channel, "spend": _money(spend), "job": job, "markers": markers, "source": "none" if job == "unclassified" else "text"})
    total = sum(totals.values())
    if total <= 0:
        raise ToolError("total spend is zero: nothing to classify")
    totals_out: dict[str, Any] = {f"{k}_amount": _money(v) for k, v in totals.items()}
    totals_out.update({f"{k}_pct": _pct(v, total) for k, v in totals.items()})
    totals_out["total"] = _money(total)
    channels_out = []
    for channel, parts in by_channel.items():
        jobs = [j for j in ("brand", "activation", "mixed") if parts[j] > 0]
        row = {"channel": channel, **{k: _money(v) for k, v in parts.items()}, "total": _money(sum(parts.values())), "jobs": jobs, "both_jobs": len([j for j in jobs if j != "mixed"]) > 1 or ("mixed" in jobs and len(jobs) > 1)}
        channels_out.append(row)
        if row["both_jobs"]:
            parts_text = " and ".join(f"{_fmt_money(parts[j], currency)} {j}" for j in jobs)
            warnings.append(f"{channel} carries both jobs: {parts_text}; the channel is not the job.")
    channels_out.sort(key=lambda r: -r["total"])
    unclassified_pct = totals_out["unclassified_pct"] or 0.0
    summary = (
        f"Of {_fmt_money(total, currency)}, {_fmt_money(totals['brand'], currency)} ({totals_out['brand_pct']:g} %) worked for the brand, "
        f"{_fmt_money(totals['activation'], currency)} ({totals_out['activation_pct']:g} %) for activation"
        + (f", {_fmt_money(totals['mixed'], currency)} ({totals_out['mixed_pct']:g} %) mixed" if totals["mixed"] > 0 else "")
        + (f"; {_fmt_money(totals['unclassified'], currency)} ({unclassified_pct:g} %) is unclassified until the creative is supplied" if totals["unclassified"] > 0 else "")
        + "."
    )
    if unclassified_pct > UNCLASSIFIED_RELIABLE_PCT:
        summary += f" With {unclassified_pct:g} % unclassified the split is not yet reliable."
        warnings.append(f"{unclassified_pct:g} % of spend is unclassified: the brand/activation split is not reliable yet; supply the creatives named in missing.")
    return {
        "mode": "classify",
        "currency": currency,
        "rows": rows_out,
        "totals": totals_out,
        "by_channel": channels_out,
        "rule": "activation markers: price, %, deadline, code, buy CTA, urgency, sale words; two kinds → activation, one → mixed, none → brand; no creative → unclassified. Channel names are never used.",
        "missing": missing,
        "warnings": warnings,
        "summary": summary,
    }


# --- entry points ----------------------------------------------------------------


_MODES = {
    "ledger": _ledger,
    "sov": _sov,
    "budget": _budget,
    "offers": _offers,
    "volume": _volume,
    "calendar": _calendar,
    "channel_fit": _channel_fit,
    "classify": _classify,
}


def media_plan(args: dict) -> dict[str, Any]:
    """Dispatch on ``mode``; every mode returns ``missing``, ``warnings`` and ``summary``."""
    if not isinstance(args, dict):
        raise ToolError("media_plan needs an object with mode and that mode's inputs")
    mode = str(args.get("mode") or "").strip().lower().replace("-", "_")
    if not mode:
        raise ToolError("mode is required: one of " + ", ".join(MODES))
    if mode not in _MODES:
        raise ToolError(f"unknown mode {args.get('mode')!r}: use one of " + ", ".join(MODES))
    return _base(_MODES[mode](args))


def media_plan_tool(args: dict) -> str:
    """Backs the worker-facing ``media_plan`` tool: JSON the worker can quote from."""
    return json.dumps(media_plan(args or {}), ensure_ascii=False, indent=1)
