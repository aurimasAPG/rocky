"""Campaign arithmetic a worker must not do in prose: one tool, many modes.

``campaign_calc`` takes a ``mode`` and the numbers the marketer has, and
returns a dict with the figures, the method behind them, what is ``missing``,
``warnings`` and a one-sentence ``summary`` to quote as is. Modes:

- ``lift_compare``: sampling / coupon / geo arms → cost per buyer, buyers per
  1 000 units, incremental buyers against a baseline, Wilson interval.
- ``referral_loop``: invites × acceptance × activation → K, amplification,
  users after n cycles, break-even incentive per referred user.
- ``seeding_compare``: seeded against organic arms → cost per review and per
  1 000 reach, verdict with a small-base warning.
- ``bass``: market size and adoption history or p/q → Bass forecast, peak.
- ``buyer_overlap``: buyer × brand rows → penetration, duplication of
  purchase, the duplication-law baseline and candidate partitions.
- ``network_value``: reachable counterparties before and after an integration
  under a linear and a Metcalfe model, break-even users for a fee.
- ``commission_compare``: commission plans on a product mix → payout per rep,
  alignment with gross profit, payout-neutral gross-profit rate.
- ``catchment_compare``: candidate sites → weighted catchment score with the
  weights stated and the sensitivity of the winner to them.
- ``account_penetration``: universe, customers, revenues, ceiling → share of
  accounts won, headroom, growth split.
- ``mental_availability``: respondent × situation × brand rows → mental
  penetration, mental market share, leader and gap per situation.
- ``benchmark_fit``: an external average against the brand's traits → fit per
  dimension, selection bias, an expectation range (never a bare point), and
  the brand's own comparable set when given.
- ``line_metrics``: words, syllables, reading seconds and a first-line benefit
  check per line of copy. ``audience_panel``: respondents against target
  shares → coverage and gaps.

Every function is pure, stdlib only, and says what is missing instead of
guessing.
"""

from __future__ import annotations

import json
import math
import re
import statistics
from itertools import combinations
from statistics import NormalDist
from typing import Any

from rocky.tools import ToolError

_Z95 = NormalDist().inv_cdf(0.975)
_SPACES = (" ", " ", "\xa0", " ")

BASS_DEFAULT_P = 0.03
BASS_DEFAULT_Q = 0.38
BASS_MIN_HISTORY = 6
OVERLAP_THRESHOLD = 1.5
OVERLAP_MIN_BUYERS = 50
SMALL_BASE_REVIEWS = 20
LOW_CONFIDENCE_N = 30
READING_WPM = 200
CATCHMENT_WEIGHTS = {"population": 0.5, "competitors": 0.3, "drive_time": 0.2}
# Nearer rings count fully; each ring further out counts half as much as the previous one.
BAND_DECAY = 0.5

MODES = (
    "lift_compare",
    "referral_loop",
    "seeding_compare",
    "bass",
    "buyer_overlap",
    "network_value",
    "commission_compare",
    "catchment_compare",
    "account_penetration",
    "mental_availability",
    "benchmark_fit",
    "line_metrics",
    "audience_panel",
)


# --- parsing and formatting ---------------------------------------------


def _num(
    value: Any,
    label: str,
    *,
    required: bool = False,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    """Lenient number: '12 400', '1,5', '350k', '12 400 €', '35 %' all pass."""
    if value is None or value == "":
        if required:
            raise ToolError(f"{label} is required")
        return None
    if isinstance(value, bool):
        raise ToolError(f"{label} must be a number, got {value!r}")
    text = str(value)
    for space in _SPACES:
        text = text.replace(space, "")
    text = text.replace("€", "").replace("EUR", "").replace("%", "").replace(",", ".").strip()
    factor = 1.0
    if text and text[-1] in "kKmM" and len(text) > 1:
        factor = 1_000.0 if text[-1] in "kK" else 1_000_000.0
        text = text[:-1]
    try:
        parsed = float(text) * factor
    except ValueError as exc:
        raise ToolError(f"{label} must be a number, got {value!r}") from exc
    if math.isnan(parsed) or math.isinf(parsed):
        raise ToolError(f"{label} must be a finite number")
    if minimum is not None and parsed < minimum:
        raise ToolError(f"{label} must be at least {minimum:g}, got {parsed:g}")
    if maximum is not None and parsed > maximum:
        raise ToolError(f"{label} must be at most {maximum:g}, got {parsed:g}")
    return parsed


def _rate(value: Any, label: str, *, required: bool = False, notes: list[str] | None = None) -> float | None:
    """A share as a fraction 0–1; 40 or '40 %' is read as 0.4 and said so."""
    parsed = _num(value, label, required=required, minimum=0.0, maximum=100.0)
    if parsed is None:
        return None
    if parsed > 1:
        if notes is not None:
            notes.append(f"{label} {parsed:g} read as {parsed / 100:g} (a percentage)")
        parsed /= 100
    return parsed


def _parse_nested(raw: Any, key: str) -> Any:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise ToolError(f"{key} must be JSON, got {raw[:40]!r}") from exc
    return raw


def _rows(args: dict, key: str, *, required: bool = True, min_rows: int = 1) -> list[dict] | None:
    raw = _parse_nested(args.get(key), key)
    if raw is None:
        if required:
            raise ToolError(f"{key} is required: a list of objects")
        return None
    if not isinstance(raw, list) or not all(isinstance(r, dict) for r in raw):
        raise ToolError(f"{key} must be a list of objects")
    if len(raw) < min_rows:
        raise ToolError(f"{key} needs at least {min_rows} item{'s' if min_rows != 1 else ''}, got {len(raw)}")
    return raw


def _obj(args: dict, key: str, *, required: bool = False) -> dict | None:
    raw = _parse_nested(args.get(key), key)
    if raw is None:
        if required:
            raise ToolError(f"{key} is required: an object")
        return None
    if not isinstance(raw, dict):
        raise ToolError(f"{key} must be an object")
    return raw


def _list(raw: Any, key: str) -> list | None:
    raw = _parse_nested(raw, key)
    if raw is None or raw == "":
        return None
    if isinstance(raw, (str, int, float)):
        raw = [raw]
    if not isinstance(raw, list):
        raise ToolError(f"{key} must be a list")
    return raw


def _name(row: dict, fallback: str) -> str:
    return str(row.get("name") or row.get("label") or row.get("site") or row.get("plan") or fallback).strip() or fallback


def _money(value: float) -> float:
    return round(float(value), 2)


def _pct(value: float) -> float:
    return round(float(value) * 100, 1)


def _share(numerator: float, denominator: float) -> float | None:
    return None if denominator == 0 else _pct(numerator / denominator)


def _whole(value: float) -> int | float:
    return int(value) if float(value).is_integer() else round(value, 2)


def _wilson(k: float, n: float) -> dict[str, float] | None:
    """95 % Wilson score interval for k out of n, in percent."""
    if n <= 0:
        return None
    p = k / n
    z2 = _Z95**2
    centre = (p + z2 / (2 * n)) / (1 + z2 / n)
    half = _Z95 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n)
    return {"low_pct": _pct(max(0.0, centre - half)), "high_pct": _pct(min(1.0, centre + half))}


def _fmt_money(value: float | None, currency: str) -> str:
    if value is None:
        return "n/a"
    return f"{currency} {value:,.2f}"


def _base(mode: str, method: str) -> dict[str, Any]:
    return {"mode": mode, "method": method, "missing": [], "warnings": []}


def _currency(args: dict) -> str:
    return str(args.get("currency") or "EUR").strip() or "EUR"


# --- lift_compare -----------------------------------------------------------


def lift_compare(args: dict) -> dict[str, Any]:
    """Sampling, coupon or geo arms against each other, incremental when a baseline is given.

    ``arms``: [{name, units, unit_cost, extra_cost, buyers, population, baseline_buyers}].
    The cost of generating requests (ads for an opt-in form) belongs in ``extra_cost``
    of that arm. Without ``baseline_buyers`` the figures are gross buyers and the
    result says so under ``missing``; a baseline is never estimated.
    """
    currency = _currency(args)
    out = _base(
        "lift_compare",
        "total cost = units × unit_cost + extra_cost; conversion = buyers / units with a 95 % Wilson interval; "
        "incremental buyers = buyers − baseline_buyers; arms ranked by cost per incremental buyer when every arm "
        "has a baseline, else by cost per (gross) buyer.",
    )
    raw_arms = _rows(args, "arms", min_rows=1)
    arms: list[dict[str, Any]] = []
    for i, item in enumerate(raw_arms):
        name = _name(item, f"arm {i + 1}")
        where = f"arm '{name}'"
        units = _num(item.get("units"), f"{where} units", required=True)
        if units <= 0:
            raise ToolError(f"{where}: units must be greater than zero")
        unit_cost = _num(item.get("unit_cost"), f"{where} unit_cost", minimum=0.0)
        if unit_cost is None:
            unit_cost = 0.0
            out["missing"].append(f"{name}: unit_cost (taken as 0)")
        extra = _num(item.get("extra_cost"), f"{where} extra_cost", minimum=0.0) or 0.0
        buyers = _num(item.get("buyers"), f"{where} buyers", required=True, minimum=0.0)
        if buyers > units:
            raise ToolError(f"{where}: buyers ({buyers:g}) exceed units distributed ({units:g})")
        population = _num(item.get("population"), f"{where} population")
        if population is not None and population <= 0:
            raise ToolError(f"{where}: population must be greater than zero")
        baseline = _num(item.get("baseline_buyers"), f"{where} baseline_buyers", minimum=0.0)
        total_cost = units * unit_cost + extra
        row: dict[str, Any] = {
            "name": name,
            "units": _whole(units),
            "total_cost": _money(total_cost),
            "buyers": _whole(buyers),
            "buyers_per_1000_units": round(buyers / units * 1000, 1),
            "conversion_pct": _pct(buyers / units),
            "conversion_interval": _wilson(buyers, units),
            "cost_per_buyer": _money(total_cost / buyers) if buyers > 0 else None,
            "penetration_pct": _share(buyers, population) if population is not None else None,
            "incremental_buyers": None,
            "cost_per_incremental_buyer": None,
        }
        if buyers == 0:
            out["warnings"].append(f"{name}: no buyers, cost per buyer is undefined.")
        if population is None:
            out["missing"].append(f"{name}: population")
        if baseline is None:
            out["missing"].append(f"{name}: baseline_buyers")
        else:
            incremental = buyers - baseline
            row["incremental_buyers"] = _whole(incremental)
            if incremental > 0:
                row["cost_per_incremental_buyer"] = _money(total_cost / incremental)
            else:
                out["warnings"].append(f"{name}: baseline ({baseline:g}) is not below buyers ({buyers:g}); no incremental effect.")
        row["_population"] = population
        row["_cost"] = total_cost
        row["_buyers"] = buyers
        row["_fixed"] = units * unit_cost
        arms.append(row)

    all_baselines = all(a["incremental_buyers"] is not None for a in arms)
    basis = "cost_per_incremental_buyer" if all_baselines else "cost_per_buyer"
    if not all_baselines:
        out["warnings"].append("No control or baseline for every arm: these are gross buyers, not incremental; the effect cannot be separated from existing buyers.")
    pops = [a["_population"] for a in arms if a["_population"] is not None]
    if len(pops) >= 2 and max(pops) > 2 * min(pops):
        out["warnings"].append("Arm populations differ by more than 2×: areas are not comparable without a baseline per arm.")
    ranked = sorted((a for a in arms if a[basis] is not None), key=lambda a: a[basis])
    out["ranking_basis"] = basis
    out["ranking"] = [a["name"] for a in ranked]
    if len(ranked) >= 2:
        best, second = ranked[0], ranked[1]
        # extra cost at which the best arm's cost per buyer meets the runner-up's
        per_buyer_second = second[basis]
        buyers_basis = best["incremental_buyers"] if all_baselines else best["_buyers"]
        flip_extra = per_buyer_second * buyers_basis - best["_fixed"]
        out["flip_point"] = {
            "arm": best["name"],
            "extra_cost_at_which_ranking_flips": _money(max(0.0, flip_extra)),
            "note": f"above this extra cost (ads, handling) '{second['name']}' becomes the cheaper arm",
        }
    for a in arms:
        for key in ("_population", "_cost", "_buyers", "_fixed"):
            a.pop(key, None)
    out["arms"] = arms
    if ranked:
        best = ranked[0]
        label = "incremental buyer" if all_baselines else "buyer (gross)"
        out["summary"] = (
            f"{best['name']}: {_fmt_money(best[basis], currency)} per {label}, "
            f"{best['buyers_per_1000_units']} buyers per 1 000 units"
            + (f"; next {ranked[1]['name']} at {_fmt_money(ranked[1][basis], currency)}." if len(ranked) > 1 else ".")
        )
    else:
        out["summary"] = "No arm has buyers; nothing to rank."
    return out


# --- referral_loop ----------------------------------------------------------


def referral_loop(args: dict) -> dict[str, Any]:
    """Reproduction rate K and what it means for growth and the incentive.

    ``invites_per_user`` × ``acceptance_rate`` × ``activation_rate`` = K (acceptance
    and activation as fractions; 40 is read as 0.4). ``cycle_days`` and ``horizon_days``
    (default 90) give the cycles that fit; ``users`` (the seed base) and ``cycles``
    (default 3) give users after n cycles; ``contribution_per_user`` gives the
    break-even incentive per referred user, compared with ``incentive_per_referred_user``.
    """
    currency = _currency(args)
    out = _base(
        "referral_loop",
        "K = invites_per_user × acceptance_rate × activation_rate; amplification = 1 / (1 − K) for K < 1; "
        "new users in cycle i = seed × K^i; break-even incentive per referred user = contribution per user.",
    )
    notes: list[str] = []
    invites = _num(args.get("invites_per_user"), "invites_per_user", required=True, minimum=0.0)
    acceptance = _rate(args.get("acceptance_rate"), "acceptance_rate", required=True, notes=notes)
    activation = _rate(args.get("activation_rate"), "activation_rate", notes=notes)
    if activation is None:
        activation = 1.0
        out["missing"].append("activation_rate (taken as 1: every accepted invite counts as a user)")
    cycle_days = _num(args.get("cycle_days"), "cycle_days")
    if cycle_days is not None and cycle_days <= 0:
        raise ToolError("cycle_days must be greater than zero")
    horizon = _num(args.get("horizon_days"), "horizon_days", minimum=1.0) or 90.0
    cycles_arg = _num(args.get("cycles"), "cycles", minimum=0.0)
    cycles = int(cycles_arg) if cycles_arg is not None else 3
    seed = _num(args.get("users"), "users", minimum=0.0)
    contribution = _num(args.get("contribution_per_user"), "contribution_per_user", minimum=0.0)
    incentive = _num(args.get("incentive_per_referred_user"), "incentive_per_referred_user", minimum=0.0)
    paid_cac = _num(args.get("paid_cac"), "paid_cac", minimum=0.0)

    k = invites * acceptance * activation
    out["k"] = round(k, 3)
    out["factors"] = {
        "invites_per_user": invites,
        "acceptance_rate_pct": _pct(acceptance),
        "activation_rate_pct": _pct(activation),
    }
    out["assumptions"] = notes
    if k < 1:
        amplification = 1 / (1 - k)
        out["amplification"] = round(amplification, 2)
        out["amplification_note"] = f"every paid user brings {amplification - 1:.2f} more over the loop's full run"
    else:
        amplification = None
        out["amplification"] = None
        out["warnings"].append(
            f"K = {k:.2f} is 1 or above: unbounded above 1 — this arithmetic has no limit; real loops saturate, so treat the figure as 'self-sustaining', not as a multiple."
        )
    if k < 1:
        out["loop_verdict"] = "amplifies paid acquisition but does not replace it (K below 1)"
    else:
        out["loop_verdict"] = "self-sustaining in this arithmetic (K at or above 1); check saturation"

    if seed is None:
        out["missing"].append("users (seed base) — users after n cycles not computed")
        out["after_cycles"] = None
    else:
        rows = []
        total = seed
        new = seed
        for i in range(1, cycles + 1):
            new = new * k
            total += new
            rows.append({"cycle": i, "new_users": round(new, 1), "total_users": round(total, 1)})
        out["after_cycles"] = {"cycles": cycles, "seed_users": _whole(seed), "rows": rows, "total_users": round(total, 1)}
        if cycle_days is not None:
            out["after_cycles"]["days_covered"] = _whole(cycles * cycle_days)

    if cycle_days is None:
        out["missing"].append("cycle_days — cycles per horizon not computed")
        out["cycles_in_horizon"] = None
    else:
        fits = horizon / cycle_days
        out["cycles_in_horizon"] = {
            "horizon_days": _whole(horizon),
            "cycle_days": _whole(cycle_days),
            "cycles": round(fits, 1),
            "growth_multiple": round(sum(k**i for i in range(0, int(fits) + 1)), 2),
            "note": "growth_multiple = total users after the whole cycles in the horizon per seed user",
        }

    if contribution is None:
        out["missing"].append("contribution_per_user — break-even incentive not computed")
        out["break_even_incentive_per_referred_user"] = None
    else:
        out["break_even_incentive_per_referred_user"] = _money(contribution)
        if incentive is not None:
            out["incentive_check"] = {
                "incentive_per_referred_user": _money(incentive),
                "contribution_per_user": _money(contribution),
                "margin_after_incentive": _money(contribution - incentive),
                "verdict": "pays" if incentive < contribution else "loses on every referred user",
            }
            if incentive >= contribution:
                out["warnings"].append("The incentive per referred user is at or above the contribution a user brings.")
    if paid_cac is not None and amplification is not None:
        blended = (paid_cac + (amplification - 1) * (incentive or 0.0)) / amplification
        out["blended_cac"] = {
            "paid_cac": _money(paid_cac),
            "incentive_per_referred_user": _money(incentive or 0.0),
            "blended_cac": _money(blended),
            "note": "(paid CAC + referred users × incentive) / users acquired per paid user",
        }
    amp_text = f"amplification {out['amplification']}" if amplification is not None else "no finite amplification"
    out["summary"] = (
        f"K = {invites:g} × {_pct(acceptance):g} % × {_pct(activation):g} % = {k:.2f}; {amp_text}"
        + (f"; break-even incentive {_fmt_money(contribution, currency)} per referred user." if contribution is not None else ".")
    )
    return out


# --- seeding_compare --------------------------------------------------------


def seeding_compare(args: dict) -> dict[str, Any]:
    """Seeded (free product, licences) against organic arms by cost per review and per 1 000 reach.

    ``arms``: [{name, units, cost, reviews (or posts), reach}]. Fewer than
    ``min_reviews`` (default 20) reviews makes the cost per review unstable and the
    verdict is marked low-confidence.
    """
    currency = _currency(args)
    out = _base(
        "seeding_compare",
        "cost per review = cost / reviews; cost per 1 000 reach = cost / reach × 1 000; review rate = reviews / units "
        "with a 95 % Wilson interval; the cheapest cost per review wins, low confidence under min_reviews.",
    )
    min_reviews = int(_num(args.get("min_reviews"), "min_reviews", minimum=1.0) or SMALL_BASE_REVIEWS)
    raw_arms = _rows(args, "arms", min_rows=1)
    arms: list[dict[str, Any]] = []
    for i, item in enumerate(raw_arms):
        name = _name(item, f"arm {i + 1}")
        where = f"arm '{name}'"
        units = _num(item.get("units"), f"{where} units", minimum=0.0)
        cost = _num(item.get("cost"), f"{where} cost", required=True, minimum=0.0)
        reviews = _num(item.get("reviews", item.get("posts")), f"{where} reviews", required=True, minimum=0.0)
        reach = _num(item.get("reach"), f"{where} reach", minimum=0.0)
        row: dict[str, Any] = {
            "name": name,
            "kind": str(item.get("kind") or "").strip() or None,
            "units": _whole(units) if units is not None else None,
            "cost": _money(cost),
            "reviews": _whole(reviews),
            "reach": _whole(reach) if reach is not None else None,
            "cost_per_review": _money(cost / reviews) if reviews > 0 else None,
            "cost_per_1000_reach": _money(cost / reach * 1000) if reach else None,
            "reviews_per_100_units": round(reviews / units * 100, 1) if units else None,
            "review_rate_interval": _wilson(reviews, units) if units and reviews <= units else None,
            "reach_per_review": round(reach / reviews, 1) if reach is not None and reviews > 0 else None,
            "small_base": reviews < min_reviews,
        }
        if reviews == 0:
            out["warnings"].append(f"{name}: no reviews, cost per review is undefined.")
        elif reviews < min_reviews:
            out["warnings"].append(f"{name}: only {reviews:g} reviews (under {min_reviews}); the cost per review is unstable.")
        if units is not None and reviews > units:
            out["warnings"].append(f"{name}: more reviews than units; the review rate is not a share of units.")
        if reach is None:
            out["missing"].append(f"{name}: reach")
        if units is None:
            out["missing"].append(f"{name}: units")
        arms.append(row)
    ranked = sorted((a for a in arms if a["cost_per_review"] is not None), key=lambda a: a["cost_per_review"])
    out["arms"] = arms
    out["ranking_by_cost_per_review"] = [a["name"] for a in ranked]
    reach_ranked = sorted((a for a in arms if a["cost_per_1000_reach"] is not None), key=lambda a: a["cost_per_1000_reach"])
    out["ranking_by_cost_per_1000_reach"] = [a["name"] for a in reach_ranked]
    if ranked:
        best = ranked[0]
        confident = not best["small_base"] and (len(ranked) < 2 or not ranked[1]["small_base"])
        out["verdict"] = {
            "cheapest_per_review": best["name"],
            "confident": confident,
            "note": None if confident else "small base: fewer reviews than min_reviews in a compared arm, so the gap may be noise",
        }
        out["summary"] = (
            f"{best['name']}: {_fmt_money(best['cost_per_review'], currency)} per review"
            + (f", {_fmt_money(best['cost_per_1000_reach'], currency)} per 1 000 reach" if best["cost_per_1000_reach"] is not None else "")
            + (f"; next {ranked[1]['name']} at {_fmt_money(ranked[1]['cost_per_review'], currency)} per review" if len(ranked) > 1 else "")
            + ("." if confident else " (small base, low confidence).")
        )
    else:
        out["verdict"] = None
        out["summary"] = "No arm produced reviews; nothing to rank."
    return out


# --- bass -------------------------------------------------------------------


def _fit_bass(history: list[float], m: float) -> tuple[float, float, float]:
    """Least squares on n(t) = p·(m − N) + q·N·(1 − N/m), N = cumulative adopters before t. Returns p, q, R²."""
    xs1, xs2, ys = [], [], []
    cumulative = 0.0
    for n in history:
        xs1.append(m - cumulative)
        xs2.append(cumulative * (1 - cumulative / m))
        ys.append(n)
        cumulative += n
    s11 = sum(x * x for x in xs1)
    s12 = sum(a * b for a, b in zip(xs1, xs2))
    s22 = sum(x * x for x in xs2)
    s1y = sum(x * y for x, y in zip(xs1, ys))
    s2y = sum(x * y for x, y in zip(xs2, ys))
    det = s11 * s22 - s12 * s12
    if abs(det) < 1e-12:
        raise ToolError("history cannot be fitted: the adoption series is degenerate (constant or all zeros)")
    p = (s1y * s22 - s2y * s12) / det
    q = (s11 * s2y - s12 * s1y) / det
    mean_y = sum(ys) / len(ys)
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - (p * a + q * b)) ** 2 for y, a, b in zip(ys, xs1, xs2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 1.0
    return p, q, r2


def bass(args: dict) -> dict[str, Any]:
    """Bass diffusion forecast with p, q and the pool as separately reported assumptions.

    ``market_size`` (the finite pool, required), ``history`` (new adopters per period,
    fitted when at least 6 periods) or ``p`` and ``q``; otherwise the defaults
    0.03 / 0.38 are used and flagged. ``horizon`` periods (default 12),
    ``adopters_to_date`` (default: sum of history), ``period`` label.
    """
    out = _base(
        "bass",
        "discrete Bass: new(t) = p·(m − N) + q·N·(1 − N/m); p and q by least squares on the history when ≥ 6 periods; "
        "continuous peak at ln(q/p)/(p+q) periods from launch with m(p+q)²/(4q) new adopters.",
    )
    m = _num(args.get("market_size"), "market_size")
    if m is None:
        raise ToolError("market_size is required: size the pool first")
    if m <= 0:
        raise ToolError("market_size must be greater than zero")
    period = str(args.get("period") or "period").strip()
    horizon = int(_num(args.get("horizon"), "horizon", minimum=1.0) or 12)
    if horizon > 600:
        raise ToolError("horizon must be at most 600 periods")
    history_raw = _list(args.get("history"), "history")
    history: list[float] = []
    if history_raw:
        for i, value in enumerate(history_raw):
            n = _num(value, f"history[{i}]", required=True, minimum=0.0)
            history.append(n)
    p_given = _num(args.get("p"), "p")
    q_given = _num(args.get("q"), "q")
    if (p_given is None) != (q_given is None):
        raise ToolError("give both p and q, or neither")
    if p_given is not None and (not 0 < p_given < 1 or not 0 <= q_given < 1):
        raise ToolError("p must be between 0 and 1 (exclusive), q between 0 and 1")

    fit_quality = None
    if p_given is not None:
        p, q, source = p_given, q_given, "given"
        if history and len(history) >= BASS_MIN_HISTORY:
            fp, fq, r2 = _fit_bass(history, m)
            out["fit_alternative"] = {"p": round(fp, 4), "q": round(fq, 4), "r2": round(r2, 3), "note": "fitted from history but not used; p and q were given"}
    elif len(history) >= BASS_MIN_HISTORY:
        p, q, fit_quality = _fit_bass(history, m)
        source = "fitted"
        if p <= 0 or q < 0:
            out["warnings"].append(f"Fit gave p = {p:.4f}, q = {q:.4f}: the history does not follow a Bass curve; p floored at 0.0001, q at 0.")
            p = max(p, 1e-4)
            q = max(q, 0.0)
        if fit_quality < 0.7:
            out["warnings"].append(f"Fit R² = {fit_quality:.2f}: the history fits the Bass form poorly; treat the forecast as rough.")
    else:
        p, q, source = BASS_DEFAULT_P, BASS_DEFAULT_Q, "default"
        reason = f"only {len(history)} history periods (fewer than {BASS_MIN_HISTORY})" if history else "no history and no p/q"
        out["warnings"].append(
            f"p = {BASS_DEFAULT_P} and q = {BASS_DEFAULT_Q} are generic defaults ({reason}); they are assumptions, not the brand's figures — name them as such or give p and q."
        )
        out["missing"].append("p and q (or at least 6 periods of history)")

    adopters_to_date = _num(args.get("adopters_to_date"), "adopters_to_date", minimum=0.0)
    cumulative = sum(history)
    if adopters_to_date is not None:
        if history and abs(adopters_to_date - cumulative) > 0.5:
            out["warnings"].append(f"adopters_to_date ({adopters_to_date:g}) differs from the history sum ({cumulative:g}); adopters_to_date is used.")
        cumulative = adopters_to_date
    if cumulative > m:
        raise ToolError(f"adopters to date ({cumulative:g}) exceed market_size ({m:g}); the pool is too small")

    out["assumptions"] = {
        "m": _whole(m),
        "p": round(p, 4),
        "q": round(q, 4),
        "source": source,
        "adopters_to_date": _whole(cumulative),
        "history_periods": len(history),
        "fit_r2": round(fit_quality, 3) if fit_quality is not None else None,
    }
    periods_elapsed = len(history)
    forecast = []
    n_cum = cumulative
    peak_new, peak_period = -1.0, None
    ninety = None
    for t in range(1, horizon + 1):
        new = p * (m - n_cum) + q * n_cum * (1 - n_cum / m)
        new = max(0.0, min(new, m - n_cum))
        n_cum += new
        row = {
            "period": periods_elapsed + t,
            "new": round(new, 1),
            "cumulative": round(n_cum, 1),
            "share_of_pool_pct": _pct(n_cum / m),
        }
        forecast.append(row)
        if new > peak_new:
            peak_new, peak_period = new, periods_elapsed + t
        if ninety is None and n_cum >= 0.9 * m:
            ninety = periods_elapsed + t
    out["forecast"] = forecast
    out["period_label"] = period
    history_peak = max(history) if history else None
    if history_peak is not None and history_peak >= peak_new:
        out["peak"] = {
            "period": history.index(history_peak) + 1,
            "new_adopters": round(history_peak, 1),
            "where": "history",
            "note": "the highest period so far is in the history; the forecast does not exceed it",
        }
    else:
        out["peak"] = {"period": peak_period, "new_adopters": round(peak_new, 1), "where": "forecast"}
    still_rising = len(forecast) > 1 and peak_period == periods_elapsed + horizon and forecast[-1]["new"] >= forecast[-2]["new"]
    if still_rising:
        out["warnings"].append("Adoption is still rising at the end of the horizon; extend horizon to see the peak.")
    if q > p:
        t_star = math.log(q / p) / (p + q)
        out["theoretical_peak"] = {
            "periods_from_launch": round(t_star, 1),
            "new_adopters": round(m * (p + q) ** 2 / (4 * q), 1),
            "note": "continuous Bass formula, counted from the first period of adoption",
        }
    else:
        out["theoretical_peak"] = None
        out["warnings"].append("q ≤ p: imitation is weaker than innovation, so adoption declines from launch and has no interior peak.")
    out["period_when_90pct_reached"] = ninety
    if ninety is None:
        out["warnings"].append(f"90 % of the pool is not reached within the {horizon}-period horizon.")
    out["summary"] = (
        f"Pool {m:,.0f}; p = {p:.4f}, q = {q:.4f} ({source}); peak at {period} {out['peak']['period']} with "
        f"{out['peak']['new_adopters']:,.0f} new adopters; {forecast[-1]['share_of_pool_pct']:g} % of the pool by {period} {forecast[-1]['period']}"
        + (f"; 90 % reached in {period} {ninety}." if ninety else ".")
    )
    return out


# --- buyer_overlap ----------------------------------------------------------


def buyer_overlap(args: dict) -> dict[str, Any]:
    """Duplication of purchase: which brands share buyers more than their size predicts.

    ``rows``: [{buyer, brand, units}] (units optional; a row is a purchase). Per brand:
    buyers and penetration. Per ordered pair: observed share of A's buyers who also
    bought B, expected under independence (= penetration of B), expected under the
    duplication law (D × penetration of B, D fitted across all pairs), and the ratio.
    Pairs above ``threshold`` (default 1.5) both ways form candidate ``partitions``.
    Brands under ``min_buyers`` (default 50) are left out of the pairs.
    """
    out = _base(
        "buyer_overlap",
        "penetration = brand buyers / all buyers; observed duplication A→B = buyers of both / buyers of A; expected under "
        "independence = penetration of B; duplication law expected = D × penetration of B with D fitted by least squares "
        "through the origin over all pairs; ratio = observed / expected (baseline chosen by `baseline`); partitions are "
        "connected groups of pairs above threshold in both directions.",
    )
    rows = _rows(args, "rows", min_rows=1)
    threshold = _num(args.get("threshold"), "threshold", minimum=1.0) or OVERLAP_THRESHOLD
    min_buyers_arg = _num(args.get("min_buyers"), "min_buyers", minimum=1.0)
    min_buyers = int(min_buyers_arg) if min_buyers_arg is not None else OVERLAP_MIN_BUYERS
    baseline = str(args.get("baseline") or "duplication_law").strip().lower()
    if baseline not in {"duplication_law", "independence"}:
        raise ToolError("baseline must be duplication_law or independence")
    focus = str(args.get("focus_brand") or "").strip()

    buyers_by_brand: dict[str, set[str]] = {}
    all_buyers: set[str] = set()
    skipped = 0
    for i, row in enumerate(rows):
        buyer = str(row.get("buyer") or row.get("buyer_id") or row.get("household") or row.get("respondent") or "").strip()
        brand = str(row.get("brand") or "").strip()
        if not buyer or not brand:
            skipped += 1
            continue
        units = _num(row.get("units", 1), f"rows[{i}] units")
        if units is not None and units <= 0:
            continue
        buyers_by_brand.setdefault(brand, set()).add(buyer)
        all_buyers.add(buyer)
    if skipped:
        out["missing"].append(f"{skipped} rows without buyer or brand skipped")
    if len(buyers_by_brand) < 2:
        raise ToolError(f"buyer_overlap needs at least two brands with buyers, found {len(buyers_by_brand)}")
    n = len(all_buyers)
    brands = []
    eligible = []
    for brand, buyers in sorted(buyers_by_brand.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        brands.append({"brand": brand, "buyers": len(buyers), "penetration_pct": _pct(len(buyers) / n)})
        if len(buyers) >= min_buyers:
            eligible.append(brand)
        else:
            out["warnings"].append(f"{brand}: {len(buyers)} buyers, under min_buyers {min_buyers}; left out of the pairs.")
    if focus and focus not in buyers_by_brand:
        out["missing"].append(f"focus_brand '{focus}' not found among the brands")
    if len(eligible) < 2:
        out["status"] = "insufficient_data"
        out["n_buyers"] = n
        out["brands"] = brands
        out["pairs"] = []
        out["partitions"] = []
        out["summary"] = f"{n} buyers, but fewer than two brands reach min_buyers {min_buyers}; no overlap can be tested."
        return out

    pen = {b: len(buyers_by_brand[b]) / n for b in eligible}
    ordered = []
    for a, b in combinations(eligible, 2):
        both = len(buyers_by_brand[a] & buyers_by_brand[b])
        ordered.append((a, b, both / len(buyers_by_brand[a]), pen[b], both))
        ordered.append((b, a, both / len(buyers_by_brand[b]), pen[a], both))
    sxy = sum(obs * pb for _, _, obs, pb, _ in ordered)
    sxx = sum(pb * pb for _, _, _, pb, _ in ordered)
    d = sxy / sxx if sxx > 0 else 1.0
    out["duplication_coefficient_d"] = round(d, 3)
    out["baseline"] = baseline
    pairs = []
    above: dict[tuple[str, str], list[float]] = {}
    for a, b, obs, pb, both in ordered:
        exp_ind = pb
        exp_law = d * pb
        chosen = exp_law if baseline == "duplication_law" else exp_ind
        ratio = obs / chosen if chosen > 0 else None
        pairs.append(
            {
                "a": a,
                "b": b,
                "n": len(buyers_by_brand[a]),
                "both": both,
                "observed_pct": _pct(obs),
                "expected_independence_pct": _pct(exp_ind),
                "expected_duplication_law_pct": _pct(exp_law),
                "ratio": round(ratio, 2) if ratio is not None else None,
                "above_threshold": ratio is not None and ratio >= threshold,
            }
        )
        if ratio is not None and ratio >= threshold:
            above.setdefault(tuple(sorted((a, b))), []).append(ratio)
    if focus:
        pairs.sort(key=lambda p: (p["a"] != focus, -(p["ratio"] or 0)))
    else:
        pairs.sort(key=lambda p: -(p["ratio"] or 0))
    out["n_buyers"] = n
    out["brands"] = brands
    out["pairs"] = pairs
    # partitions: both directions above threshold, then connected components
    edges = {pair: ratios for pair, ratios in above.items() if len(ratios) == 2}
    adjacency: dict[str, set[str]] = {}
    for a, b in edges:
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)
    seen: set[str] = set()
    partitions = []
    for start in sorted(adjacency):
        if start in seen:
            continue
        group, stack = set(), [start]
        while stack:
            node = stack.pop()
            if node in group:
                continue
            group.add(node)
            stack.extend(adjacency[node] - group)
        seen |= group
        ratios = [r for pair, rs in edges.items() if pair[0] in group and pair[1] in group for r in rs]
        partitions.append({"brands": sorted(group), "mean_ratio": round(sum(ratios) / len(ratios), 2), "pairs": len(ratios) // 2})
    out["partitions"] = partitions
    out["threshold"] = threshold
    out["status"] = "ok"
    if partitions:
        best = partitions[0]
        out["summary"] = (
            f"{n} buyers, {len(eligible)} brands compared; candidate partition {', '.join(best['brands'])} "
            f"(mean ratio {best['mean_ratio']} against the {baseline.replace('_', ' ')} baseline, D = {d:.2f})."
        )
    else:
        out["summary"] = f"{n} buyers, {len(eligible)} brands compared; no pair exceeds ratio {threshold:g} both ways — buyers are shared in line with brand size (D = {d:.2f})."
    return out


# --- network_value ----------------------------------------------------------


def network_value(args: dict) -> dict[str, Any]:
    """Reachable counterparties before and after an integration, under two labelled models.

    ``users`` (own network), ``partner_users`` (the network to connect to),
    ``overlap_fraction`` (share of partner users already in own network; unknown →
    upper bound), ``counterparty_relevance`` (share of own users who deal with the
    partner network). ``interactions_per_user`` (or ``connections_per_user``) and
    ``value_per_interaction`` give value per user; ``fee_per_user``, ``fixed_costs``
    and ``cost_per_user`` give the break-even user count.
    """
    currency = _currency(args)
    out = _base(
        "network_value",
        "reachable after = own + partner × (1 − overlap); linear model: value per user = interactions × value per "
        "interaction, unchanged by network size; Metcalfe model: value per user scales with (reachable − 1), density "
        "held constant; both are models, not measurements. Break-even users = fixed_costs / (fee − cost per user).",
    )
    notes: list[str] = []
    users = _num(args.get("users", args.get("own_users")), "users", required=True, minimum=1.0)
    partner = _num(args.get("partner_users"), "partner_users", minimum=0.0)
    overlap = _rate(args.get("overlap_fraction"), "overlap_fraction", notes=notes)
    relevance = _rate(args.get("counterparty_relevance"), "counterparty_relevance", notes=notes)
    interactions = _num(args.get("interactions_per_user", args.get("connections_per_user")), "interactions_per_user", minimum=0.0)
    value_each = _num(args.get("value_per_interaction"), "value_per_interaction", minimum=0.0)
    fee = _num(args.get("fee_per_user"), "fee_per_user", minimum=0.0)
    fixed = _num(args.get("fixed_costs"), "fixed_costs", minimum=0.0)
    cost_per_user = _num(args.get("cost_per_user"), "cost_per_user", minimum=0.0) or 0.0
    out["assumptions"] = notes
    out["users"] = _whole(users)

    status = "ok"
    if partner is None:
        out["missing"].append("partner_users — reach gain not computed")
        out["reach"] = None
        status = "insufficient"
    else:
        if overlap is None:
            out["missing"].append("overlap_fraction — the gain is an upper bound")
            overlap_used = 0.0
            status = "partial"
        else:
            overlap_used = overlap
        after = users + partner * (1 - overlap_used)
        gain = after - users
        reach: dict[str, Any] = {
            "reachable_before": _whole(users),
            "reachable_after": _whole(round(after, 2)),
            "gain_abs": _whole(round(gain, 2)),
            "gain_pct": _pct(gain / users),
            "bound": "upper_bound" if overlap is None else "estimate",
        }
        if relevance is None:
            out["missing"].append("counterparty_relevance — effective gain not computed")
            reach["effective_gain"] = None
        else:
            reach["effective_gain"] = _whole(round(gain * relevance, 2))
            reach["relevance_pct"] = _pct(relevance)
        ratio_after = after / users
        metcalfe_factor = ((after - 1) / (users - 1)) if users > 1 else ratio_after
        reach["models"] = {
            "linear": {
                "label": "model: value per user constant, total value ∝ users",
                "value_index_gain_pct": _pct(ratio_after - 1),
            },
            "metcalfe": {
                "label": "model: value per user ∝ reachable counterparties, total value ∝ users²",
                "value_per_user_gain_pct": _pct(metcalfe_factor - 1),
                "value_index_gain_pct": _pct(ratio_after * metcalfe_factor - 1),
            },
        }
        if relevance is not None:
            reach["models"]["metcalfe"]["value_per_user_gain_pct_relevant_share"] = _pct((metcalfe_factor - 1) * relevance)
        out["reach"] = reach
    out["status"] = status

    if interactions is None or value_each is None:
        out["missing"].append("interactions_per_user and value_per_interaction — value per user not computed")
        out["value_per_user"] = None
    else:
        value_per_user = interactions * value_each
        value_block: dict[str, Any] = {
            "value_per_user": _money(value_per_user),
            "total_value_linear": _money(value_per_user * users),
            "label": "linear model at today's interactions",
        }
        if out.get("reach"):
            factor = 1 + out["reach"]["models"]["metcalfe"]["value_per_user_gain_pct"] / 100
            value_block["value_per_user_after_metcalfe"] = _money(value_per_user * factor)
            value_block["value_per_user_after_linear"] = _money(value_per_user)
        out["value_per_user"] = value_block
        if fee is not None:
            out["fee_check"] = {
                "fee_per_user": _money(fee),
                "fee_share_of_value_pct": _share(fee, value_per_user),
                "verdict": "fee below the value a user gets" if fee < value_per_user else "fee at or above the value a user gets",
            }
            if fee >= value_per_user:
                out["warnings"].append("The fee per user is at or above the value a user gets from the network at today's interactions.")
                if interactions > 0 and users > 1:
                    needed = 1 + (users - 1) * fee / value_per_user
                    out["fee_check"]["users_where_metcalfe_value_covers_fee"] = math.ceil(needed)
                    out["fee_check"]["note"] = "under the Metcalfe model only; value per user grows with reachable counterparties"
    if fee is not None and fixed is not None:
        if fee <= cost_per_user:
            out["warnings"].append("fee_per_user does not exceed cost_per_user: no user count breaks even.")
            out["break_even_users"] = None
        else:
            be = fixed / (fee - cost_per_user)
            out["break_even_users"] = math.ceil(be - 1e-9)
            out["break_even_reached"] = users >= be
    elif fee is not None or fixed is not None:
        out["missing"].append("fee_per_user and fixed_costs together — break-even users not computed")

    if out.get("reach"):
        r = out["reach"]
        out["summary"] = (
            f"Reachable counterparties {r['reachable_before']:,} → {r['reachable_after']:,} (+{r['gain_abs']:,}, +{r['gain_pct']:g} %"
            f"{', upper bound: overlap unknown' if r['bound'] == 'upper_bound' else ''})"
            + (f"; relevant to {r['relevance_pct']:g} % of users → effective gain {r['effective_gain']:,}" if r.get("effective_gain") is not None else "")
            + "; linear and Metcalfe figures are models, not measurements."
        )
    else:
        out["summary"] = f"{users:,.0f} users; give partner_users to compute the reach gain" + (
            f"; break-even at {out['break_even_users']:,} users for a {_fmt_money(fee, currency)} fee." if out.get("break_even_users") else "."
        )
    return out


# --- commission_compare -----------------------------------------------------


def commission_compare(args: dict) -> dict[str, Any]:
    """Commission plans on a product mix: what each pays per sale and whether it rewards profit.

    ``products``: [{name, price, variable_cost or margin_pct, units_per_period, discount_pct}];
    ``plans``: [{name, kind: revenue|gross_profit, rate_pct, base, threshold}] (base = fixed
    pay per period; threshold = revenue or gross profit per period below which no
    commission is paid). ``sellers`` multiplies the payout into payroll.
    """
    currency = _currency(args)
    out = _base(
        "commission_compare",
        "net price = price × (1 − discount); gross profit per unit = net price − variable cost; commission per unit = "
        "rate × net price (revenue plan) or rate × gross profit (gross_profit plan); payout per rep = base + rate × "
        "max(0, measure − threshold); payout-neutral GP rate = plan 1's commission / total gross profit at the baseline mix.",
    )
    raw_products = _rows(args, "products", min_rows=1)
    raw_plans = _rows(args, "plans", min_rows=1)
    sellers = _num(args.get("sellers"), "sellers", minimum=1.0)

    products = []
    for i, item in enumerate(raw_products):
        name = _name(item, f"product {i + 1}")
        where = f"product '{name}'"
        price = _num(item.get("price"), f"{where} price", required=True, minimum=0.0)
        discount = _num(item.get("discount_pct"), f"{where} discount_pct", minimum=0.0, maximum=100.0) or 0.0
        units = _num(item.get("units_per_period", item.get("units")), f"{where} units_per_period", minimum=0.0)
        cost = _num(item.get("variable_cost"), f"{where} variable_cost", minimum=0.0)
        margin = _num(item.get("margin_pct"), f"{where} margin_pct", minimum=0.0, maximum=100.0)
        if cost is None and margin is not None:
            cost = price * (1 - margin / 100)
        if cost is None:
            out["missing"].append(f"{name}: variable_cost (gross-profit rows omitted)")
        if cost is not None and cost > price:
            raise ToolError(f"{where}: variable_cost ({cost:g}) exceeds price ({price:g})")
        if units is None:
            out["missing"].append(f"{name}: units_per_period (totals unavailable)")
        net = price * (1 - discount / 100)
        gp = None if cost is None else net - cost
        full_gp = None if cost is None else price - cost
        row = {
            "name": name,
            "price": _money(price),
            "discount_pct": discount,
            "net_price": _money(net),
            "variable_cost": _money(cost) if cost is not None else None,
            "gross_profit_per_unit": _money(gp) if gp is not None else None,
            "gross_margin_pct": _share(gp, net) if gp is not None else None,
            "units_per_period": _whole(units) if units is not None else None,
            "sells_below_cost": gp is not None and gp < 0,
        }
        if row["sells_below_cost"]:
            out["warnings"].append(f"{name}: at {discount:g} % off the sale is below cost ({_fmt_money(gp, currency)} per unit).")
        products.append((row, net, gp, full_gp, units, price))

    plans = []
    for i, item in enumerate(raw_plans):
        name = _name(item, f"plan {chr(65 + i)}")
        where = f"plan '{name}'"
        kind = str(item.get("kind") or item.get("basis") or "").strip().lower().replace(" ", "_")
        if kind in {"gp", "gross_margin", "margin", "profit"}:
            kind = "gross_profit"
        if kind not in {"revenue", "gross_profit"}:
            raise ToolError(f"{where}: kind must be revenue or gross_profit")
        rate = _num(item.get("rate_pct", item.get("rate")), f"{where} rate_pct", required=True, minimum=0.0, maximum=100.0) / 100
        base = _num(item.get("base"), f"{where} base", minimum=0.0) or 0.0
        threshold = _num(item.get("threshold"), f"{where} threshold", minimum=0.0) or 0.0
        plans.append({"name": name, "kind": kind, "rate_pct": _pct(rate), "base": _money(base), "threshold": _money(threshold), "_rate": rate, "_base": base, "_threshold": threshold})

    totals_available = all(units is not None for _, _, _, _, units, _ in products)
    total_revenue = sum(net * units for _, net, _, _, units, _ in products) if totals_available else None
    gp_known = all(gp is not None for _, _, gp, _, _, _ in products)
    total_gp = sum(gp * units for _, _, gp, _, units, _ in products) if totals_available and gp_known else None

    plan_rows = []
    for plan in plans:
        rate = plan["_rate"]
        per_product = []
        for row, net, gp, full_gp, units, price in products:
            if plan["kind"] == "revenue":
                commission = rate * net
                full_commission = rate * price
            elif gp is None:
                per_product.append({"product": row["name"], "commission_per_unit": None, "note": "variable_cost missing"})
                continue
            else:
                commission = rate * gp
                full_commission = rate * full_gp
            entry: dict[str, Any] = {
                "product": row["name"],
                "commission_per_unit": _money(commission),
                "gross_profit_per_unit": row["gross_profit_per_unit"],
                "commission_per_eur_gross_profit": round(commission / gp, 3) if gp else None,
                "gross_profit_after_commission_per_unit": _money(gp - commission) if gp is not None else None,
            }
            if row["discount_pct"] > 0:
                entry["discount_sensitivity"] = {
                    "discount_pct": row["discount_pct"],
                    "commission_full_price": _money(full_commission),
                    "commission_change_pct": _share(commission - full_commission, full_commission) if full_commission else None,
                    "gross_profit_change_pct": _share(gp - full_gp, full_gp) if full_gp else None,
                }
            per_product.append(entry)
        plan_out: dict[str, Any] = {k: v for k, v in plan.items() if not k.startswith("_")}
        plan_out["per_product"] = per_product
        measure = total_revenue if plan["kind"] == "revenue" else total_gp
        if measure is None or (plan["kind"] == "gross_profit" and not gp_known):
            plan_out["payout_per_rep"] = None
            plan_out["payroll"] = None
        else:
            commission_total = rate * max(0.0, measure - plan["_threshold"])
            payout = plan["_base"] + commission_total
            plan_out["commission_per_rep"] = _money(commission_total)
            plan_out["payout_per_rep"] = _money(payout)
            plan_out["measure_per_rep"] = _money(measure)
            plan_out["payroll"] = _money(payout * sellers) if sellers else None
            if total_gp is not None:
                plan_out["gross_profit_after_payout_per_rep"] = _money(total_gp - payout)
                plan_out["payout_share_of_gross_profit_pct"] = _share(payout, total_gp)
        # alignment: does the plan pay more for a sale that earns the company less?
        rates_per_gp = [(e["product"], e["commission_per_eur_gross_profit"], e["commission_per_unit"], e.get("gross_profit_per_unit")) for e in per_product if e.get("commission_per_eur_gross_profit") is not None]
        findings = []
        for (pa, ra, ca, ga), (pb, rb, cb, gb) in combinations(rates_per_gp, 2):
            lo, hi = (pa, ra, ca, ga), (pb, rb, cb, gb)
            if ga is not None and gb is not None and ga > gb:
                lo, hi = hi, lo
            if lo[2] > hi[2] + 1e-9:
                findings.append(f"pays {_fmt_money(lo[2], currency)} for '{lo[0]}' (gross profit {_fmt_money(lo[3], currency)}) but {_fmt_money(hi[2], currency)} for '{hi[0]}' (gross profit {_fmt_money(hi[3], currency)}): more for the sale that earns less.")
            elif ra is not None and rb is not None and max(ra, rb) > 1.1 * min(ra, rb):
                worse = (pa, ra) if ra > rb else (pb, rb)
                better = (pb, rb) if ra > rb else (pa, ra)
                findings.append(f"pays {worse[1]:.2f} per € of gross profit on '{worse[0]}' but {better[1]:.2f} on '{better[0]}' ({worse[1] / better[1]:.1f}× more per euro of profit for the lower-margin sale).")
        plan_out["alignment"] = {"aligned": not findings, "findings": findings}
        plan_rows.append(plan_out)

    out["products"] = [row for row, *_ in products]
    out["plans"] = plan_rows
    if total_gp is not None and total_gp > 0 and plan_rows[0].get("commission_per_rep") is not None:
        neutral = plan_rows[0]["commission_per_rep"] / total_gp
        out["neutral_gp_rate_pct"] = _pct(neutral)
        out["neutral_gp_rate_note"] = f"a gross_profit plan at {_pct(neutral):g} % pays the same commission as '{plan_rows[0]['name']}' at the baseline mix"
    else:
        out["neutral_gp_rate_pct"] = None
    out["totals"] = {
        "revenue_per_rep": _money(total_revenue) if total_revenue is not None else None,
        "gross_profit_per_rep": _money(total_gp) if total_gp is not None else None,
        "sellers": _whole(sellers) if sellers else None,
    }
    parts = []
    for plan in plan_rows:
        if plan.get("payout_per_rep") is not None:
            parts.append(f"{plan['name']} ({plan['kind'].replace('_', ' ')} {plan['rate_pct']:g} %) pays {_fmt_money(plan['payout_per_rep'], currency)} per rep")
    misaligned = [plan["name"] for plan in plan_rows if not plan["alignment"]["aligned"]]
    out["summary"] = (
        ("; ".join(parts) if parts else "Per-unit commissions only; give units_per_period for totals")
        + (f"; payout-neutral gross-profit rate {out['neutral_gp_rate_pct']:g} %" if out["neutral_gp_rate_pct"] is not None else "")
        + (f"; {', '.join(misaligned)} rewards lower-margin sales more per euro of profit." if misaligned else "; every plan pays in step with gross profit.")
    )
    return out


# --- catchment_compare ------------------------------------------------------


def catchment_compare(args: dict) -> dict[str, Any]:
    """Candidate sites scored on population within radius bands, competitors and drive time.

    ``sites``: [{name, bands: [{label or radius, population}, …] (nearest first),
    competitors, drive_time_minutes, rent}]; ``weights`` {population, competitors,
    drive_time} (default 0.5 / 0.3 / 0.2); ``band_weights`` (default 1, 0.5, 0.25, …).
    Each component is scaled 0–1 against the best site, so the score is relative.
    """
    out = _base(
        "catchment_compare",
        "weighted population = Σ band population × band weight (nearest band 1, each further band × 0.5); competitor "
        "component = weighted population / (competitors + 1); drive component = shortest drive time / site drive time; "
        "each component scaled to the best site = 1; score = Σ weight × component; sensitivity re-scores under other weight sets.",
    )
    raw_sites = _rows(args, "sites", min_rows=1)
    weights_in = _obj(args, "weights") or {}
    weights = {}
    for key, default in CATCHMENT_WEIGHTS.items():
        weights[key] = _num(weights_in.get(key), f"weights.{key}", minimum=0.0)
        if weights[key] is None:
            weights[key] = default
    total_w = sum(weights.values())
    if total_w <= 0:
        raise ToolError("weights must not all be zero")
    weights = {k: v / total_w for k, v in weights.items()}
    band_weights_in = _list(args.get("band_weights"), "band_weights")

    sites = []
    for i, item in enumerate(raw_sites):
        name = _name(item, f"site {i + 1}")
        where = f"site '{name}'"
        bands_raw = item.get("bands", item.get("population_bands"))
        bands_raw = _parse_nested(bands_raw, f"{where} bands")
        if isinstance(bands_raw, dict):
            bands_raw = [{"label": k, "population": v} for k, v in bands_raw.items()]
        if not isinstance(bands_raw, list) or not bands_raw:
            raise ToolError(f"{where}: bands must be a non-empty list of {{label, population}} from the nearest ring outwards")
        bands = []
        weighted = 0.0
        total_pop = 0.0
        for j, band in enumerate(bands_raw):
            if not isinstance(band, dict):
                band = {"population": band}
            pop = _num(band.get("population", band.get("pop")), f"{where} bands[{j}] population", required=True, minimum=0.0)
            if band_weights_in is not None and j < len(band_weights_in):
                bw = _num(band_weights_in[j], f"band_weights[{j}]", required=True, minimum=0.0)
            else:
                bw = BAND_DECAY**j
            label = str(band.get("label") or band.get("radius") or band.get("minutes") or f"band {j + 1}")
            bands.append({"label": label, "population": _whole(pop), "weight": round(bw, 3), "weighted_population": round(pop * bw, 1)})
            weighted += pop * bw
            total_pop += pop
        competitors = _num(item.get("competitors"), f"{where} competitors", minimum=0.0)
        if competitors is None:
            competitors = 0.0
            out["missing"].append(f"{name}: competitors (taken as 0)")
        drive = _num(item.get("drive_time_minutes", item.get("drive_time")), f"{where} drive_time_minutes")
        if drive is not None and drive <= 0:
            raise ToolError(f"{where}: drive_time_minutes must be greater than zero")
        if drive is None:
            out["missing"].append(f"{name}: drive_time_minutes (drive component neutral)")
        rent = _num(item.get("rent"), f"{where} rent", minimum=0.0)
        sites.append(
            {
                "name": name,
                "bands": bands,
                "population_total": _whole(total_pop),
                "weighted_population": round(weighted, 1),
                "competitors": _whole(competitors),
                "population_per_competitor": round(weighted / (competitors + 1), 1),
                "drive_time_minutes": drive,
                "rent": _money(rent) if rent is not None else None,
                "_w": weighted,
                "_c": weighted / (competitors + 1),
                "_d": drive,
            }
        )
    max_w = max(s["_w"] for s in sites) or 1.0
    max_c = max(s["_c"] for s in sites) or 1.0
    drives = [s["_d"] for s in sites if s["_d"] is not None]
    min_d = min(drives) if drives else None

    def components(site: dict) -> dict[str, float]:
        return {
            "population": site["_w"] / max_w,
            "competitors": site["_c"] / max_c,
            "drive_time": (min_d / site["_d"]) if (min_d is not None and site["_d"] is not None) else 1.0,
        }

    def score(site: dict, w: dict[str, float]) -> float:
        comp = components(site)
        return sum(w[k] * comp[k] for k in w)

    for site in sites:
        comp = components(site)
        site["components"] = {k: round(v, 3) for k, v in comp.items()}
        site["score"] = round(score(site, weights), 3)
        if site["rent"] is not None and site["_w"] > 0:
            site["rent_per_1000_weighted_residents"] = _money(site["rent"] / site["_w"] * 1000)
    ranking = sorted(sites, key=lambda s: -s["score"])
    best = ranking[0]
    alternatives = {
        "population_only": {"population": 1.0, "competitors": 0.0, "drive_time": 0.0},
        "equal": {"population": 1 / 3, "competitors": 1 / 3, "drive_time": 1 / 3},
        "competition_heavy": {"population": 0.3, "competitors": 0.5, "drive_time": 0.2},
        "access_heavy": {"population": 0.3, "competitors": 0.2, "drive_time": 0.5},
    }
    sensitivity = []
    for label, w in alternatives.items():
        alt_best = max(sites, key=lambda s: score(s, w))
        sensitivity.append({"weights": label, "weights_detail": {k: round(v, 2) for k, v in w.items()}, "best": alt_best["name"], "same_winner": alt_best["name"] == best["name"]})
    robust = all(s["same_winner"] for s in sensitivity)
    for site in sites:
        for key in ("_w", "_c", "_d"):
            site.pop(key, None)
    out["weights"] = {k: round(v, 3) for k, v in weights.items()}
    out["sites"] = sites
    out["ranking"] = [s["name"] for s in ranking]
    out["best"] = best["name"]
    out["sensitivity"] = sensitivity
    out["robust_to_weights"] = robust
    if len(ranking) > 1 and ranking[0]["score"] - ranking[1]["score"] < 0.05:
        out["warnings"].append(f"{ranking[0]['name']} and {ranking[1]['name']} are within 0.05 of each other; the weights decide, not the data.")
    if len(sites) == 1:
        out["warnings"].append("One site only: the score is 1 by construction; add candidates to compare.")
    out["summary"] = (
        f"{best['name']} scores {best['score']} (weights population {weights['population']:.2f}, competitors {weights['competitors']:.2f}, "
        f"drive time {weights['drive_time']:.2f})"
        + (f"; next {ranking[1]['name']} at {ranking[1]['score']}" if len(ranking) > 1 else "")
        + ("; the winner holds under every alternative weighting." if robust else "; the winner changes under some weightings — see sensitivity.")
    )
    return out


# --- account_penetration ----------------------------------------------------


def account_penetration(args: dict) -> dict[str, Any]:
    """Share of buying organisations won and the room left inside existing accounts.

    ``universe_accounts``, ``customer_accounts``, ``revenues`` (list, or
    [{account, revenue}]) or ``avg_revenue``, ``ceiling`` per account (default: 90th
    percentile of revenues, flagged assumed), ``growth_last_period`` and
    ``growth_from_existing_pct``.
    """
    currency = _currency(args)
    out = _base(
        "account_penetration",
        "penetration = customers / universe; headroom = Σ max(0, ceiling − revenue per account); share of ceiling = "
        "revenue / (customers × ceiling); headroom years = headroom / growth from existing accounts per period.",
    )
    universe = _num(args.get("universe_accounts"), "universe_accounts")
    if universe is None:
        raise ToolError("universe_accounts is required: size the universe first (a sourced count of potential buying organisations)")
    if universe <= 0:
        raise ToolError("universe_accounts must be greater than zero")
    customers = _num(args.get("customer_accounts"), "customer_accounts", required=True, minimum=0.0)
    if customers > universe:
        raise ToolError(f"customer_accounts ({customers:g}) exceed universe_accounts ({universe:g})")
    revenues_raw = _list(args.get("revenues"), "revenues")
    revenues: list[tuple[str, float]] = []
    if revenues_raw:
        for i, item in enumerate(revenues_raw):
            if isinstance(item, dict):
                label = str(item.get("account") or item.get("name") or f"account {i + 1}")
                value = _num(item.get("revenue", item.get("value")), f"revenues[{i}] revenue", required=True)
            else:
                label = f"account {i + 1}"
                value = _num(item, f"revenues[{i}]", required=True)
            if value < 0:
                raise ToolError(f"revenues[{i}]: revenue cannot be negative")
            revenues.append((label, value))
        if customers and len(revenues) != customers:
            out["warnings"].append(f"{len(revenues)} revenue rows for {customers:g} customer accounts; headroom uses the rows given.")
    avg = _num(args.get("avg_revenue"), "avg_revenue", minimum=0.0)
    if not revenues and avg is None:
        out["missing"].append("revenues or avg_revenue — headroom not computed")
    ceiling = _num(args.get("ceiling"), "ceiling", minimum=0.0)
    assumptions = []
    if ceiling is None and len(revenues) >= 2:
        values = sorted(v for _, v in revenues)
        ceiling = statistics.quantiles(values, n=10, method="inclusive")[8]
        assumptions.append(f"ceiling {ceiling:,.2f} per account assumed from the 90th percentile of revenues")
    elif ceiling is None and (revenues or avg is not None):
        out["missing"].append("ceiling per account — headroom not computed")

    penetration = customers / universe
    out["penetration_pct"] = _pct(penetration)
    out["accounts_remaining"] = _whole(universe - customers)
    out["customer_accounts"] = _whole(customers)
    out["universe_accounts"] = _whole(universe)
    headroom = None
    total_revenue = None
    if ceiling is not None and revenues:
        total_revenue = sum(v for _, v in revenues)
        headroom = sum(max(0.0, ceiling - v) for _, v in revenues)
        at_ceiling = sum(1 for _, v in revenues if v >= ceiling)
        per_account = sorted(({"account": a, "revenue": _money(v), "headroom": _money(max(0.0, ceiling - v))} for a, v in revenues), key=lambda r: -r["headroom"])
        out["headroom_per_account"] = per_account[:50]
        out["accounts_at_ceiling"] = at_ceiling
        out["share_of_ceiling_pct"] = _share(total_revenue, ceiling * len(revenues))
    elif ceiling is not None and avg is not None:
        total_revenue = avg * customers
        headroom = max(0.0, ceiling - avg) * customers
        out["share_of_ceiling_pct"] = _share(avg, ceiling)
        assumptions.append("headroom from the average revenue: accounts above the ceiling are not netted out")
    out["ceiling"] = _money(ceiling) if ceiling is not None else None
    out["expansion_headroom"] = _money(headroom) if headroom is not None else None
    out["total_revenue"] = _money(total_revenue) if total_revenue is not None else None

    growth = _num(args.get("growth_last_period"), "growth_last_period", minimum=0.0)
    existing_pct = _num(args.get("growth_from_existing_pct"), "growth_from_existing_pct", minimum=0.0, maximum=100.0)
    headroom_years = None
    if growth is None or existing_pct is None:
        out["missing"].append("growth_last_period and growth_from_existing_pct — headroom years not computed")
        out["growth_split"] = None
    else:
        from_existing = growth * existing_pct / 100
        out["growth_split"] = {
            "growth_last_period": _money(growth),
            "from_existing": _money(from_existing),
            "from_new": _money(growth - from_existing),
            "from_existing_pct": existing_pct,
        }
        if headroom is not None:
            headroom_years = headroom / from_existing if from_existing > 0 else None
            out["headroom_periods_at_current_growth"] = round(headroom_years, 1) if headroom_years is not None else None
            if from_existing <= 0:
                out["warnings"].append("No growth from existing accounts last period; headroom years are undefined.")
    share_of_ceiling = out.get("share_of_ceiling_pct")
    if headroom is None:
        verdict = "insufficient"
    elif (headroom_years is not None and headroom_years < 2) or (share_of_ceiling is not None and share_of_ceiling >= 80):
        verdict = "shift_to_acquisition"
    elif penetration >= 0.5 and (headroom_years is None or headroom_years >= 5):
        verdict = "expansion_room"
    else:
        verdict = "mixed"
    out["verdict"] = verdict
    out["verdict_rule"] = (
        "shift_to_acquisition when headroom covers under 2 periods of existing-account growth or revenue is ≥ 80 % of the ceiling; "
        "expansion_room when half or more of the universe is already won and headroom covers ≥ 5 periods; otherwise mixed."
    )
    out["assumptions"] = assumptions
    out["summary"] = (
        f"Account penetration {customers:,.0f} / {universe:,.0f} = {_pct(penetration):g} %; {universe - customers:,.0f} accounts never bought"
        + (f"; expansion headroom {_fmt_money(headroom, currency)}" if headroom is not None else "")
        + (f" (ceiling {_fmt_money(ceiling, currency)} per account{', assumed' if assumptions and 'assumed' in assumptions[0] else ''})" if ceiling is not None and headroom is not None else "")
        + (f" = about {headroom_years:.1f} periods of existing-account growth" if headroom_years is not None else "")
        + f"; verdict: {verdict.replace('_', ' ')}."
    )
    return out


# --- mental_availability ----------------------------------------------------


def mental_availability(args: dict) -> dict[str, Any]:
    """Unaided recall per buying situation: where the brand is missing and against whom.

    ``rows``: [{respondent, situation, brand, aided?}] one row per mention;
    ``focus_brand``; ``min_n`` (default 30); ``n_respondents`` overrides the count of
    respondents (when some named nobody); ``respondents_per_situation`` {situation: n}
    likewise per situation.
    """
    out = _base(
        "mental_availability",
        "per situation: share of respondents naming the brand unaided; mental penetration = respondents naming the focus in "
        "≥ 1 situation / all respondents; mental market share = focus mentions / all mentions; low confidence under min_n.",
    )
    rows = _rows(args, "rows", min_rows=1)
    focus = str(args.get("focus_brand") or "").strip()
    if not focus:
        raise ToolError("focus_brand is required")
    min_n = int(_num(args.get("min_n"), "min_n", minimum=1.0) or LOW_CONFIDENCE_N)
    missing_threshold = _num(args.get("missing_pct"), "missing_pct", minimum=0.0, maximum=100.0)
    missing_threshold = 5.0 if missing_threshold is None else missing_threshold
    per_situation_n = _obj(args, "respondents_per_situation") or {}

    mentions: dict[str, dict[str, set[str]]] = {}
    respondents_by_situation: dict[str, set[str]] = {}
    all_respondents: set[str] = set()
    aided = 0
    blank = 0
    focus_fold = focus.casefold()
    focus_respondents: set[str] = set()
    total_mentions = 0
    focus_mentions = 0
    for row in rows:
        respondent = str(row.get("respondent") or row.get("respondent_id") or "").strip()
        situation = str(row.get("situation") or "").strip()
        brand = str(row.get("brand") or "").strip()
        if not respondent or not situation:
            blank += 1
            continue
        all_respondents.add(respondent)
        respondents_by_situation.setdefault(situation, set()).add(respondent)
        if not brand:
            blank += 1
            continue
        if str(row.get("aided") or "").strip().lower() in {"1", "true", "yes", "aided"}:
            aided += 1
            continue
        key = focus if brand.casefold() == focus_fold else brand
        mentions.setdefault(situation, {}).setdefault(key, set()).add(respondent)
        total_mentions += 1
        if key == focus:
            focus_mentions += 1
            focus_respondents.add(respondent)
    if blank:
        out["missing"].append(f"{blank} rows with a blank respondent, situation or brand")
    if aided:
        out["missing"].append(f"{aided} aided rows excluded")
    if not respondents_by_situation:
        raise ToolError("no usable rows: each row needs respondent, situation and brand")
    n_total_arg = _num(args.get("n_respondents"), "n_respondents", minimum=1.0)
    n_total = int(n_total_arg) if n_total_arg is not None else len(all_respondents)
    if n_total < len(all_respondents):
        raise ToolError(f"n_respondents ({n_total}) is below the {len(all_respondents)} distinct respondents in the rows")
    found = any(focus in m for m in mentions.values())
    if not found:
        out["missing"].append(f"focus_brand '{focus}' was not named in any situation")

    situations = []
    missing_from = []
    for situation in sorted(respondents_by_situation):
        n_override = per_situation_n.get(situation)
        n = int(_num(n_override, f"respondents_per_situation['{situation}']", minimum=1.0)) if n_override is not None else len(respondents_by_situation[situation])
        brand_counts = {b: len(r) for b, r in mentions.get(situation, {}).items()}
        ranking = sorted(brand_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        focus_count = brand_counts.get(focus, 0)
        focus_pct = _pct(focus_count / n) if n else None
        leader, leader_count = (ranking[0] if ranking else (None, 0))
        rank = (1 + sum(1 for _, c in ranking if c > focus_count)) if focus_count else None
        row = {
            "situation": situation,
            "n": n,
            "focus_pct": focus_pct,
            "rank": rank,
            "leader": leader,
            "leader_pct": _pct(leader_count / n) if n and leader else None,
            "gap_pp": round((focus_count - leader_count) / n * 100, 1) if n and leader else None,
            "low_confidence": n < min_n,
            "brands": [{"brand": b, "pct": _pct(c / n)} for b, c in ranking],
        }
        if n < min_n:
            out["warnings"].append(f"'{situation}': n = {n} under {min_n}, low confidence.")
        if focus_pct is not None and focus_pct <= missing_threshold:
            missing_from.append(situation)
        situations.append(row)
    out["n_respondents"] = n_total
    out["focus_brand"] = focus
    out["situations"] = situations
    out["mental_penetration_pct"] = _pct(len(focus_respondents) / n_total)
    out["mental_market_share_pct"] = _share(focus_mentions, total_mentions)
    out["missing_from"] = missing_from
    with_pct = [s for s in situations if s["focus_pct"] is not None]
    strongest = max(with_pct, key=lambda s: s["focus_pct"]) if with_pct else None
    out["strongest"] = strongest["situation"] if strongest else None
    # competitor shares overall
    competitor_mentions: dict[str, int] = {}
    for m in mentions.values():
        for b, r in m.items():
            competitor_mentions[b] = competitor_mentions.get(b, 0) + len(r)
    out["mental_market_share_by_brand_pct"] = {b: _share(c, total_mentions) for b, c in sorted(competitor_mentions.items(), key=lambda kv: -kv[1])}
    out["summary"] = (
        f"{focus}: mental penetration {out['mental_penetration_pct']:g} % of {n_total} respondents, mental market share "
        f"{out['mental_market_share_pct']:g} %; missing (≤ {missing_threshold:g} %) from {len(missing_from)} of {len(situations)} situations"
        + (f": {', '.join(missing_from)}" if missing_from else "")
        + (f"; strongest in '{strongest['situation']}' at {strongest['focus_pct']:g} %." if strongest and strongest["focus_pct"] else ".")
    )
    return out


# --- benchmark_fit ----------------------------------------------------------


def _match_list(own: str | None, options: list[str]) -> str:
    if not own or not options:
        return "unknown"
    own_fold = own.casefold().strip()
    for opt in options:
        o = str(opt).casefold().strip()
        if o and (o == own_fold or o in own_fold or own_fold in o):
            return "match"
    return "mismatch"


def _years(period: Any) -> tuple[int | None, int | None]:
    if period is None or period == "":
        return None, None
    if isinstance(period, dict):
        start = _num(period.get("start", period.get("from")), "period.start")
        end = _num(period.get("end", period.get("to")), "period.end")
        return (int(start) if start is not None else None, int(end) if end is not None else None)
    found = re.findall(r"(19|20)(\d{2})", str(period))
    years = [int(a + b) for a, b in found]
    if not years:
        return None, None
    return min(years), max(years)


def benchmark_fit(args: dict) -> dict[str, Any]:
    """Does an external average apply to this brand, and what range does it imply?

    ``benchmark`` {metric, value, unit, source_url, selection (awards | self_submitted
    | panel | census | platform | own | unknown), industries [], markets [], budget_min,
    budget_max, objective, sample_n, period}; ``own`` {industry, market, budget,
    objective, period}. Optional ``own_history`` {values [], new_value, min_n} scores
    the brand's own comparable set first (#482).
    """
    out = _base(
        "benchmark_fit",
        "per dimension match/mismatch/unknown; selection bias when the population is award or self-submitted entries; "
        "fit score = matches / known dimensions; expectation range = value / (1 + f) … value × (1 + f) with "
        "f = 0.15 + 0.2 per mismatch + 0.1 per unknown + 0.3 for selection bias (+ 0.1 under 30 cases); own comparable "
        "set: median, quartiles (inclusive method) and the percentile of the new value.",
    )
    bench = _obj(args, "benchmark", required=True)
    own = _obj(args, "own") or {}
    if not own:
        out["missing"].append("own — the brand's traits; without them every dimension is unknown")
    value = _num(bench.get("value"), "benchmark.value", required=True)
    metric = str(bench.get("metric") or "benchmark").strip()
    unit = str(bench.get("unit") or "").strip()
    selection = str(bench.get("selection") or "unknown").strip().lower().replace("-", "_").replace(" ", "_")
    allowed = {"awards", "self_submitted", "panel", "census", "platform", "own", "unknown"}
    if selection not in allowed:
        raise ToolError(f"benchmark.selection must be one of {', '.join(sorted(allowed))}")
    industries = [str(x) for x in (_list(bench.get("industries"), "benchmark.industries") or [])]
    markets = [str(x) for x in (_list(bench.get("markets"), "benchmark.markets") or [])]
    bmin = _num(bench.get("budget_min", bench.get("budget_min_eur")), "benchmark.budget_min", minimum=0.0)
    bmax = _num(bench.get("budget_max", bench.get("budget_max_eur")), "benchmark.budget_max", minimum=0.0)
    if bmin is not None and bmax is not None and bmax < bmin:
        raise ToolError("benchmark.budget_max is below budget_min")
    sample_n = _num(bench.get("sample_n"), "benchmark.sample_n", minimum=0.0)
    b_objective = str(bench.get("objective") or "unknown").strip().lower()
    o_objective = str(own.get("objective") or "").strip().lower()
    own_industry = str(own.get("industry") or "").strip() or None
    own_market = str(own.get("market") or "").strip() or None
    own_budget = _num(own.get("budget", own.get("budget_eur")), "own.budget", minimum=0.0)
    b_start, b_end = _years(bench.get("period"))
    o_start, o_end = _years(own.get("period"))

    fit: dict[str, dict[str, Any]] = {}
    fit["industry"] = {"benchmark": industries or None, "own": own_industry, "fit": _match_list(own_industry, industries)}
    fit["market"] = {"benchmark": markets or None, "own": own_market, "fit": _match_list(own_market, markets)}
    if own_budget is None or (bmin is None and bmax is None):
        budget_fit = "unknown"
    else:
        lo = bmin if bmin is not None else 0.0
        hi = bmax if bmax is not None else math.inf
        budget_fit = "match" if lo <= own_budget <= hi else "mismatch"
    budget_ratio = None
    if own_budget and own_budget > 0:
        if bmin is not None and bmax is not None:
            budget_ratio = ((bmin + bmax) / 2) / own_budget
        elif bmin is not None:
            budget_ratio = bmin / own_budget
        elif bmax is not None:
            budget_ratio = bmax / own_budget
    fit["budget"] = {"benchmark": [bmin, bmax] if (bmin is not None or bmax is not None) else None, "own": own_budget, "fit": budget_fit, "budget_ratio": round(budget_ratio, 1) if budget_ratio is not None else None}
    if b_objective in {"", "unknown"} or not o_objective:
        obj_fit = "unknown"
    elif b_objective == o_objective or b_objective == "mixed":
        obj_fit = "match" if b_objective == o_objective else "partial"
    else:
        obj_fit = "mismatch"
    fit["objective"] = {"benchmark": b_objective, "own": o_objective or None, "fit": obj_fit}
    if b_end is None or o_start is None:
        period_fit = "unknown"
    elif o_start <= b_end + 3:
        period_fit = "match"
    elif o_start - b_end > 10:
        period_fit = "mismatch"
    else:
        period_fit = "partial"
    fit["period"] = {"benchmark": f"{b_start}–{b_end}" if b_start else None, "own": f"{o_start}" if o_start else None, "fit": period_fit}

    mismatches = [k for k, v in fit.items() if v["fit"] == "mismatch"]
    partials = [k for k, v in fit.items() if v["fit"] == "partial"]
    unknowns = [k for k, v in fit.items() if v["fit"] == "unknown"]
    matches = [k for k, v in fit.items() if v["fit"] == "match"]
    known = len(fit) - len(unknowns)
    fit_score = _pct((len(matches) + 0.5 * len(partials)) / known) if known else None
    selection_bias = selection in {"awards", "self_submitted"}
    selection_reason = None
    if selection_bias:
        selection_reason = "the population is entrants chosen or submitted for recognition, not campaigns in general; the average is of winners' stories"
    if sample_n is not None and sample_n < 30:
        out["warnings"].append(f"benchmark rests on {sample_n:g} cases; the average is unstable.")
    if selection_bias and any(k in mismatches for k in ("industry", "market", "budget")):
        verdict = "not_comparable"
    elif len(unknowns) >= 2:
        verdict = "unknown"
    elif mismatches or selection_bias or partials:
        verdict = "weak"
    else:
        verdict = "applicable"
    f = 0.15 + 0.2 * len(mismatches) + 0.1 * (len(partials) + len(unknowns)) + (0.3 if selection_bias else 0.0) + (0.1 if sample_n is not None and sample_n < 30 else 0.0)
    low, high = value / (1 + f), value * (1 + f)
    out["benchmark"] = {"metric": metric, "value": value, "unit": unit or None, "source_url": bench.get("source_url"), "selection": selection, "sample_n": _whole(sample_n) if sample_n is not None else None}
    out["fit"] = fit
    out["fit_score_pct"] = fit_score
    out["mismatches"] = mismatches
    out["unknown_dimensions"] = unknowns
    out["selection_bias"] = {"flag": selection_bias, "reason": selection_reason}
    out["budget_ratio"] = fit["budget"]["budget_ratio"]
    out["verdict"] = verdict
    out["expectation_range"] = {
        "low": round(low, 3),
        "high": round(high, 3),
        "unit": unit or None,
        "spread_factor": round(1 + f, 2),
        "note": "a span to plan against, not a forecast; never quote the benchmark value as a point for this brand",
    }
    for k in unknowns:
        out["missing"].append(f"{k}: cannot be judged — give the benchmark's and the brand's {k}")
    names = {"industry": "industry", "market": "market", "budget": "budget", "objective": "objective", "period": "period"}
    if verdict == "not_comparable":
        caveat = (
            f"The {metric} figure ({value:g}{(' ' + unit) if unit else ''}) comes from {selection.replace('_', ' ')} entries"
            f"{' in ' + ', '.join(markets) if markets else ''}"
            f"{' with budgets from ' + format(bmin, ',.0f') if bmin is not None else ''}; it differs from this brand on "
            f"{', '.join(names[k] for k in mismatches)}, so it is not a norm here — use it as a hypothesis only."
        )
    elif verdict == "weak":
        caveat = (
            f"The {metric} figure ({value:g}{(' ' + unit) if unit else ''}) is a weak guide for this brand"
            + (f": it differs on {', '.join(names[k] for k in mismatches + partials)}" if mismatches or partials else "")
            + (" and its population is self-selected" if selection_bias else "")
            + f"; plan for {low:.3g}–{high:.3g}{(' ' + unit) if unit else ''}, not the point."
        )
    elif verdict == "unknown":
        caveat = f"The {metric} figure cannot be judged: {', '.join(names[k] for k in unknowns)} of the benchmark or the brand are unknown; find the population description before quoting it."
    else:
        caveat = f"The {metric} figure ({value:g}{(' ' + unit) if unit else ''}) comes from a comparable population; still plan for {low:.3g}–{high:.3g}{(' ' + unit) if unit else ''} rather than the point."
    out["caveat"] = caveat

    history = _obj(args, "own_history")
    if history is not None:
        values_raw = _list(history.get("values"), "own_history.values") or []
        values = [_num(v, f"own_history.values[{i}]", required=True) for i, v in enumerate(values_raw)]
        min_n_arg = _num(history.get("min_n"), "own_history.min_n", minimum=2.0)
        min_n = int(min_n_arg) if min_n_arg is not None else 5
        new_value = _num(history.get("new_value"), "own_history.new_value")
        block: dict[str, Any] = {"n_comparable": len(values), "min_n": min_n}
        if len(values) < min_n:
            block["status"] = "insufficient_own_history"
            out["warnings"].append(f"Only {len(values)} comparable pieces (under {min_n}); no own median is quoted.")
        else:
            q1, med, q3 = statistics.quantiles(values, n=4, method="inclusive")
            block.update({"status": "ok", "median": round(med, 3), "q1": round(q1, 3), "q3": round(q3, 3), "min": min(values), "max": max(values)})
            if new_value is not None:
                below = sum(1 for v in values if v < new_value)
                equal = sum(1 for v in values if v == new_value)
                block["new_value"] = new_value
                block["percentile"] = round((below + 0.5 * equal) / len(values) * 100)
                block["rank_of_n"] = f"{sum(1 for v in values if v > new_value) + 1} of {len(values) + 1}"
                block["verdict"] = "above_q3" if new_value > q3 else "below_q1" if new_value < q1 else "within_iqr"
            else:
                out["missing"].append("own_history.new_value — the new piece is not placed against the set")
        out["own_history"] = block

    own_part = ""
    if history is not None and out["own_history"].get("status") == "ok" and out["own_history"].get("verdict"):
        h = out["own_history"]
        own_part = f"Against {h['n_comparable']} comparable own pieces the new value {h['new_value']:g} is {h['verdict'].replace('_', ' ')} (median {h['median']:g}, IQR {h['q1']:g}–{h['q3']:g}). "
    out["summary"] = own_part + f"Benchmark fit: {verdict.replace('_', ' ')}" + (f" (score {fit_score:g} %" if fit_score is not None else " (score n/a") + f", mismatches: {', '.join(mismatches) or 'none'}); expectation range {low:.3g}–{high:.3g}{(' ' + unit) if unit else ''}."
    return out


# --- line_metrics -----------------------------------------------------------

_LT_DIPHTHONGS = ("ai", "au", "ei", "ie", "uo", "ui")
_VOWELS = set("aeiouyąęėįųūAEIOUYĄĘĖĮŲŪ")
_BENEFIT_CUES = {
    "en": ("you", "your", "save", "get", "free", "faster", "more", "less", "without", "easy", "easier", "quick", "quickly", "in minutes", "guarantee", "now", "today", "never", "only", "%", "€"),
    "lt": ("jūs", "jums", "jūsų", "tau", "tavo", "sutaupy", "gauk", "gaukite", "nemokam", "greič", "greita", "daugiau", "mažiau", "be ", "lengv", "paprast", "per", "garantij", "dabar", "šiandien", "tik", "%", "€"),
}


def _syllables(word: str, language: str) -> int:
    text = word.lower()
    count = 0
    i = 0
    while i < len(text):
        if text[i] in _VOWELS or text[i] in "aeiouy":
            count += 1
            # swallow the rest of the vowel group
            j = i + 1
            while j < len(text) and (text[j] in _VOWELS or text[j] in "aeiouy"):
                j += 1
            i = j
        else:
            i += 1
    if language == "en" and len(text) > 2 and text.endswith("e") and not text.endswith(("le", "ee", "ye")) and count > 1:
        count -= 1
    return max(count, 1) if any(c.isalpha() for c in text) else 0


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[^\W\d_]+(?:['’-][^\W\d_]+)*|\d+(?:[.,]\d+)?%?|€\d+", text)]


def line_metrics(args: dict) -> dict[str, Any]:
    """Words, syllables, reading seconds and a first-line benefit check per line or asset.

    ``lines``: [string] or [{name, text}]; ``language`` lt | en (default lt);
    ``wpm`` reading speed (default 200). The metrics describe length and rhythm, not
    memorability; only a delayed recall test proves that.
    """
    out = _base(
        "line_metrics",
        "words by regex; syllables = vowel groups (Lithuanian diphthongs ai/au/ei/ie/uo/ui count once; English silent final e "
        "dropped); reading seconds = words / wpm × 60; first-line benefit = a benefit cue word (you/save/free/… or the Lithuanian "
        "equivalents) or a number in the first sentence; alliteration = word-initial letters repeated; rhyme = last two letters "
        "of the final words match. Rhythm is measured; memory is not.",
    )
    raw = _list(args.get("lines", args.get("items")), "lines")
    if not raw:
        raise ToolError("lines is required: a list of strings or {name, text} objects")
    language = str(args.get("language") or "lt").strip().lower()
    if language not in {"lt", "en"}:
        raise ToolError("language must be lt or en")
    wpm = _num(args.get("wpm"), "wpm", minimum=1.0) or READING_WPM
    cues = _BENEFIT_CUES[language]
    rows = []
    for i, item in enumerate(raw):
        if isinstance(item, dict):
            name = _name(item, f"line {i + 1}")
            text = str(item.get("text") or item.get("copy") or item.get("line") or "").strip()
        else:
            name = f"line {i + 1}"
            text = str(item).strip()
        if not text:
            out["missing"].append(f"{name}: empty")
            continue
        words = _words(text)
        syll = [_syllables(w, language) for w in words]
        first_sentence = re.split(r"(?<=[.!?])\s+|\n", text, maxsplit=1)[0]
        first_fold = first_sentence.casefold()
        cue_hits = [c for c in cues if c in first_fold] + (["number"] if re.search(r"\d", first_sentence) else [])
        initials = [w[0].casefold() for w in words if w[0].isalpha()]
        alliteration = sum(count - 1 for count in {c: initials.count(c) for c in set(initials)}.values() if count > 1)
        lowered = [w.casefold() for w in words if w[0].isalpha()]
        repeated = sorted({w for w in lowered if lowered.count(w) > 1})
        total_syll = sum(syll)
        rows.append(
            {
                "name": name,
                "text": text,
                "words": len(words),
                "characters": len(text),
                "syllables": total_syll,
                "syllables_per_word": round(total_syll / len(words), 2) if words else None,
                "reading_seconds": round(len(words) / wpm * 60, 1),
                "first_line_benefit": bool(cue_hits),
                "benefit_cues": cue_hits,
                "alliteration": alliteration,
                "repeated_words": repeated,
                "_last": lowered[-1] if lowered else "",
            }
        )
    if not rows:
        raise ToolError("no usable lines: every line is empty")
    rhyme_pairs = []
    for a, b in combinations(rows, 2):
        if a["_last"] and b["_last"] and a["_last"] != b["_last"] and a["_last"][-2:] == b["_last"][-2:]:
            rhyme_pairs.append([a["name"], b["name"]])
    for r in rows:
        r.pop("_last", None)
    out["lines"] = rows
    out["rhyme_pairs"] = rhyme_pairs
    out["shortest"] = min(rows, key=lambda r: (r["syllables"], r["words"]))["name"]
    out["most_patterned"] = max(rows, key=lambda r: r["alliteration"] + sum(1 for p in rhyme_pairs if r["name"] in p))["name"]
    without_benefit = [r["name"] for r in rows if not r["first_line_benefit"]]
    out["without_first_line_benefit"] = without_benefit
    if without_benefit:
        out["warnings"].append(f"No benefit cue in the first line of: {', '.join(without_benefit)}.")
    out["note"] = "These metrics describe length and rhythm, not memorability; plan a delayed recall test with experiment_plan to prove that."
    out["summary"] = (
        f"{len(rows)} lines; shortest '{out['shortest']}' ({min(r['syllables'] for r in rows)} syllables); "
        f"{len(rows) - len(without_benefit)} of {len(rows)} open with a benefit cue; reading time "
        f"{min(r['reading_seconds'] for r in rows):g}–{max(r['reading_seconds'] for r in rows):g} s at {wpm:g} wpm."
    )
    return out


# --- audience_panel ---------------------------------------------------------


def audience_panel(args: dict) -> dict[str, Any]:
    """Does a respondent list cover the target audience? Shares per dimension against target shares.

    ``respondents``: [{dimension: value, …}]; ``target``: {dimension: {value: share_pct}}
    (shares per dimension sum to about 100); ``tolerance_pp`` (default 5). Coverage per
    dimension = Σ min(observed, target) share; gaps list the under-represented values.
    """
    out = _base(
        "audience_panel",
        "observed share = respondents with the value / respondents with any value in that dimension; coverage = Σ min(observed, "
        "target) over values (100 = the panel mirrors the target); a gap is a value under its target by more than tolerance_pp; "
        "low confidence under 30 respondents.",
    )
    respondents = _rows(args, "respondents", min_rows=1)
    target = _obj(args, "target", required=True)
    if not target:
        raise ToolError("target must map each dimension to {value: share_pct}")
    tolerance = _num(args.get("tolerance_pp"), "tolerance_pp", minimum=0.0)
    tolerance = 5.0 if tolerance is None else tolerance
    n = len(respondents)
    if n < LOW_CONFIDENCE_N:
        out["warnings"].append(f"{n} respondents (under {LOW_CONFIDENCE_N}): shares are rough.")
    dimensions = []
    worst = None
    for dimension, shares_raw in target.items():
        shares_raw = _parse_nested(shares_raw, f"target.{dimension}")
        if not isinstance(shares_raw, dict) or not shares_raw:
            raise ToolError(f"target.{dimension} must map values to share percentages")
        shares = {str(k).strip(): _num(v, f"target.{dimension}.{k}", required=True, minimum=0.0, maximum=100.0) for k, v in shares_raw.items()}
        total = sum(shares.values())
        if not 95 <= total <= 105:
            raise ToolError(f"target.{dimension} shares sum to {total:g} %, not 100 %")
        counts: dict[str, int] = {}
        blank = 0
        for r in respondents:
            value = str(r.get(dimension) or "").strip()
            if not value:
                blank += 1
                continue
            counts[value] = counts.get(value, 0) + 1
        answered = n - blank
        if blank:
            out["missing"].append(f"{dimension}: {blank} respondents without a value")
        if answered == 0:
            dimensions.append({"dimension": dimension, "n": 0, "coverage_pct": None, "values": [], "gaps": [], "unexpected": sorted(counts)})
            out["warnings"].append(f"{dimension}: no respondent has a value; coverage cannot be computed.")
            continue
        fold = {k.casefold(): k for k in shares}
        observed: dict[str, float] = {k: 0.0 for k in shares}
        unexpected = []
        for value, count in counts.items():
            key = fold.get(value.casefold())
            if key is None:
                unexpected.append(value)
                continue
            observed[key] += count / answered * 100
        coverage = sum(min(observed[k], shares[k]) for k in shares)
        values = []
        gaps = []
        for key, share in shares.items():
            gap = observed[key] - share
            values.append({"value": key, "target_pct": share, "observed_pct": round(observed[key], 1), "gap_pp": round(gap, 1), "respondents": sum(c for v, c in counts.items() if fold.get(v.casefold()) == key)})
            if gap < -tolerance:
                gaps.append({"value": key, "short_by_pp": round(-gap, 1), "respondents_needed": math.ceil(share / 100 * answered - observed[key] / 100 * answered - 1e-9)})
        if unexpected:
            out["warnings"].append(f"{dimension}: values not in the target ({', '.join(sorted(unexpected))}) are left out of the shares.")
        row = {"dimension": dimension, "n": answered, "coverage_pct": round(coverage, 1), "values": values, "gaps": gaps, "unexpected": sorted(unexpected)}
        dimensions.append(row)
        if worst is None or row["coverage_pct"] < worst["coverage_pct"]:
            worst = row
    out["n_respondents"] = n
    out["tolerance_pp"] = tolerance
    out["dimensions"] = dimensions
    out["overall_coverage_pct"] = round(min(d["coverage_pct"] for d in dimensions if d["coverage_pct"] is not None), 1) if any(d["coverage_pct"] is not None for d in dimensions) else None
    all_gaps = [f"{d['dimension']} {g['value']} (−{g['short_by_pp']:g} pp)" for d in dimensions for g in d["gaps"]]
    out["summary"] = (
        f"{n} respondents; coverage {out['overall_coverage_pct']} % on the weakest dimension"
        + (f" ({worst['dimension']})" if worst else "")
        + (f"; under-represented: {', '.join(all_gaps)}." if all_gaps else "; every value within tolerance.")
    )
    return out


# --- dispatch ---------------------------------------------------------------

_MODES = {
    "lift_compare": lift_compare,
    "referral_loop": referral_loop,
    "seeding_compare": seeding_compare,
    "bass": bass,
    "buyer_overlap": buyer_overlap,
    "network_value": network_value,
    "commission_compare": commission_compare,
    "catchment_compare": catchment_compare,
    "account_penetration": account_penetration,
    "mental_availability": mental_availability,
    "benchmark_fit": benchmark_fit,
    "line_metrics": line_metrics,
    "audience_panel": audience_panel,
}


def campaign_calc(args: dict) -> dict[str, Any]:
    """Dispatch on ``mode``; every mode returns mode, method, missing, warnings and summary."""
    args = dict(args or {})
    mode = str(args.pop("mode", "") or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not mode:
        raise ToolError(f"mode is required: one of {', '.join(MODES)}")
    if mode not in _MODES:
        raise ToolError(f"unknown mode '{mode}'; use one of {', '.join(MODES)}")
    return _MODES[mode](args)


def campaign_calc_tool(args: dict) -> str:
    """Backs the worker-facing ``campaign_calc`` tool: JSON the worker can quote from."""
    return json.dumps(campaign_calc(args or {}), ensure_ascii=False, indent=1)
