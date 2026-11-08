"""Marketing arithmetic workers must not do in prose.

``unit_economics`` answers the questions behind pricing, promotions, offer
design and acquisition decisions: what one sale contributes, how many sales a
price cut has to win back, where break-even sits, and which assumption would
flip the decision. Every function is pure, takes numbers with stated units,
returns a dict, and says plainly what is missing instead of guessing.
"""

from __future__ import annotations

import json
import math
from statistics import NormalDist
from typing import Any

from rocky.tools import ToolError

# The marketer usually knows price and volume; cost is the number that goes missing.
# When it does, the answer is shown for these margins so the question is concrete.
CONDITIONAL_MARGINS = (0.2, 0.4, 0.6, 0.8)
# How far an assumption is moved to see whether the decision survives.
SENSITIVITY = 0.10


def _number(args: dict, key: str, *, required: bool = False, minimum: float | None = None) -> float | None:
    raw = args.get(key)
    if raw is None or raw == "":
        if required:
            raise ToolError(f"{key} is required")
        return None
    try:
        value = float(str(raw).replace(",", ".").replace("€", "").replace("%", "").strip())
    except ValueError as exc:
        raise ToolError(f"{key} must be a number, got {raw!r}") from exc
    if math.isnan(value) or math.isinf(value):
        raise ToolError(f"{key} must be a finite number")
    if minimum is not None and value < minimum:
        raise ToolError(f"{key} must be at least {minimum:g}")
    return value


def _money(value: float) -> float:
    return round(float(value), 2)


def _pct(value: float) -> float:
    return round(float(value) * 100, 1)


def unit_economics(args: dict) -> dict[str, Any]:
    """Contribution, break-even and the volume a price change must win back.

    Inputs (all per unit unless said): ``price`` (currency), ``variable_cost``
    (currency), ``units`` (current sales per period), ``fixed_costs`` (currency
    per period), and for a change: ``new_price`` or ``discount_pct``,
    ``new_variable_cost``, ``expected_uplift_pct`` (the volume gain the marketer
    believes the change will bring). ``currency`` defaults to EUR.

    Missing ``variable_cost`` does not stop the answer: the result lists it under
    ``missing`` and gives a ``conditional`` table across typical margins so the
    worker can ask for the real figure or label the assumption.
    """
    currency = str(args.get("currency") or "EUR").strip() or "EUR"
    price = _number(args, "price", required=True, minimum=0.0)
    if price <= 0:
        raise ToolError("price must be greater than zero")
    units = _number(args, "units", minimum=0.0)
    fixed = _number(args, "fixed_costs", minimum=0.0)
    cost = _number(args, "variable_cost", minimum=0.0)
    new_cost = _number(args, "new_variable_cost", minimum=0.0)
    expected_uplift = _number(args, "expected_uplift_pct")
    new_price = _number(args, "new_price", minimum=0.0)
    discount = _number(args, "discount_pct")
    if new_price is None and discount is not None:
        if not 0 <= discount < 100:
            raise ToolError("discount_pct must be between 0 and 100")
        new_price = price * (1 - discount / 100)
    if new_price is not None and new_price <= 0:
        raise ToolError("new_price must be greater than zero")

    out: dict[str, Any] = {
        "currency": currency,
        "inputs": {
            "price": _money(price),
            "variable_cost": None if cost is None else _money(cost),
            "units": units,
            "fixed_costs": None if fixed is None else _money(fixed),
            "new_price": None if new_price is None else _money(new_price),
            "new_variable_cost": None if new_cost is None else _money(new_cost),
            "expected_uplift_pct": expected_uplift,
        },
        "missing": [],
        "assumptions": ["Variable cost per unit stays the same at the new volume unless new_variable_cost says otherwise."],
    }

    if cost is None:
        out["missing"].append("variable_cost")
        out["conditional"] = [
            _scenario(price, price * (1 - margin), units, fixed, new_price, None, expected_uplift, currency)
            | {"assumed_margin_pct": _pct(margin)}
            for margin in CONDITIONAL_MARGINS
        ]
        out["summary"] = (
            "The variable cost per unit is missing, so the answer depends on the margin. "
            "Ask for the unit cost, or report one of the conditional rows with its assumption stated."
        )
        return out
    if cost >= price:
        out["warnings"] = [f"variable_cost {cost:g} is not below price {price:g}: every sale loses money before fixed costs."]

    result = _scenario(price, cost, units, fixed, new_price, new_cost, expected_uplift, currency)
    out.update(result)
    if new_price is not None:
        out["sensitivity"] = _sensitivity(price, cost, units, new_price, new_cost, expected_uplift)
    return out


def _scenario(
    price: float,
    cost: float,
    units: float | None,
    fixed: float | None,
    new_price: float | None,
    new_cost: float | None,
    expected_uplift: float | None,
    currency: str,
) -> dict[str, Any]:
    unit_contribution = price - cost
    row: dict[str, Any] = {
        "contribution_per_unit": _money(unit_contribution),
        "margin_pct": _pct(unit_contribution / price),
    }
    if units is not None:
        row["total_contribution"] = _money(unit_contribution * units)
        row["revenue"] = _money(price * units)
    if fixed is not None:
        if unit_contribution > 0:
            be_units = fixed / unit_contribution
            row["break_even_units"] = math.ceil(be_units - 1e-9)
            row["break_even_revenue"] = _money(row["break_even_units"] * price)
            if units is not None:
                row["profit"] = _money(unit_contribution * units - fixed)
                row["safety_margin_pct"] = _pct((units - be_units) / units) if units else None
        else:
            row["break_even_units"] = None
            row["note"] = "No break-even: contribution per unit is zero or negative."
    if new_price is None:
        row["summary"] = _summary_current(row, currency)
        return row

    after_cost = cost if new_cost is None else new_cost
    new_unit = new_price - after_cost
    change: dict[str, Any] = {
        "new_price": _money(new_price),
        "price_change_pct": _pct((new_price - price) / price),
        "new_contribution_per_unit": _money(new_unit),
        "new_margin_pct": _pct(new_unit / new_price),
    }
    if new_unit <= 0:
        change["required_units"] = None
        change["note"] = "At the new price each sale contributes nothing or less. No volume recovers the contribution."
    elif units is not None:
        target = unit_contribution * units
        required = target / new_unit
        required_units = math.ceil(required - 1e-9)
        change["required_units"] = required_units
        change["extra_units"] = max(0, required_units - math.ceil(units - 1e-9))
        change["required_uplift_pct"] = _pct((required - units) / units) if units else None
        change["contribution_preserved"] = _money(target)
        if expected_uplift is not None and units:
            expected_units = units * (1 + expected_uplift / 100)
            expected_total = new_unit * expected_units
            change["expected_units"] = round(expected_units, 1)
            change["expected_total_contribution"] = _money(expected_total)
            change["contribution_delta"] = _money(expected_total - target)
            change["decision"] = "change pays" if expected_total >= target - 1e-9 else "change loses contribution"
    else:
        # No volume: give the uplift as a ratio, which is volume-independent.
        change["required_uplift_pct"] = _pct(unit_contribution / new_unit - 1) if unit_contribution > 0 else None
        if expected_uplift is not None and change["required_uplift_pct"] is not None:
            change["decision"] = (
                "change pays" if expected_uplift >= change["required_uplift_pct"] - 1e-9 else "change loses contribution"
            )
    row["change"] = change
    row["summary"] = _summary_change(row, change, currency)
    return row


def _sensitivity(
    price: float, cost: float, units: float | None, new_price: float, new_cost: float | None, expected_uplift: float | None
) -> dict[str, Any]:
    """The assumption that moves the decision: unit cost, and the expected uplift if one was given."""
    out: dict[str, Any] = {}
    rows = []
    for factor in (1 - SENSITIVITY, 1.0, 1 + SENSITIVITY):
        c = (cost if new_cost is None else new_cost) * factor
        base = price - cost * factor if new_cost is None else price - cost
        new_unit = new_price - c
        if base <= 0 or new_unit <= 0:
            rows.append({"variable_cost": _money(c), "required_uplift_pct": None})
            continue
        rows.append({"variable_cost": _money(c), "required_uplift_pct": _pct(base / new_unit - 1)})
    out["variable_cost"] = rows
    base_unit = price - cost
    if expected_uplift is not None and base_unit > 0:
        # The unit cost at which the expected uplift exactly preserves contribution:
        # (price - c) * 1 = (new_price - c) * (1 + u)  →  c = (new_price*(1+u) - price) / u
        u = expected_uplift / 100
        if u > 0:
            flip = (new_price * (1 + u) - price) / u
            out["decision_flips_at_variable_cost"] = _money(flip)
        out["decision_flips_at_uplift_pct"] = _pct(base_unit / (new_price - (cost if new_cost is None else new_cost)) - 1) if new_price - (cost if new_cost is None else new_cost) > 0 else None
    return out


def _summary_current(row: dict[str, Any], currency: str) -> str:
    parts = [f"Each sale contributes {row['contribution_per_unit']:g} {currency} ({row['margin_pct']:g} % margin)."]
    if "total_contribution" in row:
        parts.append(f"Total contribution {row['total_contribution']:g} {currency}.")
    if row.get("break_even_units") is not None:
        parts.append(f"Break-even at {row['break_even_units']} units ({row['break_even_revenue']:g} {currency}).")
    return " ".join(parts)


def _summary_change(row: dict[str, Any], change: dict[str, Any], currency: str) -> str:
    head = (
        f"Contribution per unit goes from {row['contribution_per_unit']:g} to "
        f"{change['new_contribution_per_unit']:g} {currency}."
    )
    if change.get("required_units") is not None:
        head += (
            f" To keep {change['contribution_preserved']:g} {currency} you need {change['required_units']} sales"
            f" ({change['extra_units']} extra, +{change['required_uplift_pct']:g} %)."
        )
    elif change.get("required_uplift_pct") is not None:
        head += f" Volume must rise {change['required_uplift_pct']:g} % to keep the same contribution."
    else:
        head += " " + str(change.get("note") or "")
    if change.get("decision"):
        head += f" Decision at the expected uplift: {change['decision']}."
    return head


# --- experiment planning -------------------------------------------------

DEFAULT_ALPHA = 0.05
DEFAULT_POWER = 0.8
_Z = NormalDist()


def _sample_per_variant(p1: float, p2: float, alpha: float, power: float, two_sided: bool) -> int:
    """Normal-approximation sample size per arm for a difference in proportions."""
    z_alpha = _Z.inv_cdf(1 - alpha / 2) if two_sided else _Z.inv_cdf(1 - alpha)
    z_beta = _Z.inv_cdf(power)
    pbar = (p1 + p2) / 2
    numerator = z_alpha * math.sqrt(2 * pbar * (1 - pbar)) + z_beta * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))
    return math.ceil((numerator / (p2 - p1)) ** 2 - 1e-9)


def _detectable_relative_effect(p1: float, per_variant: int, alpha: float, power: float, two_sided: bool) -> float:
    """Smallest relative lift (fraction) the given sample per arm can detect: bisection on the lift."""
    lo, hi = 1e-4, 5.0
    for _ in range(60):
        mid = (lo + hi) / 2
        p2 = min(p1 * (1 + mid), 0.999999)
        if _sample_per_variant(p1, p2, alpha, power, two_sided) <= per_variant:
            hi = mid
        else:
            lo = mid
    return hi


def experiment_plan(args: dict) -> dict[str, Any]:
    """Sample size per variant, duration and a decision rule for an A/B test on a rate.

    Inputs: ``baseline_rate`` (percent, e.g. 3.2 for 3.2 % conversion),
    ``minimum_effect`` with ``effect_kind`` ``relative`` (percent of baseline,
    default) or ``absolute`` (percentage points), ``weekly_traffic`` (eligible
    visitors or orders per week, all variants together), ``variants`` (arms
    including control, default 2), ``alpha`` (default 0.05), ``power`` (default
    0.8), ``two_sided`` (default true), ``max_weeks`` (how long the marketer can
    wait). Missing traffic → duration is reported as unknown, never guessed.
    """
    baseline = _number(args, "baseline_rate", required=True)
    if not 0 < baseline < 100:
        raise ToolError("baseline_rate must be a percentage between 0 and 100")
    effect = _number(args, "minimum_effect", required=True)
    if effect <= 0:
        raise ToolError("minimum_effect must be greater than zero")
    kind = str(args.get("effect_kind") or "relative").strip().lower()
    if kind not in {"relative", "absolute"}:
        raise ToolError("effect_kind must be relative or absolute")
    variants = int(_number(args, "variants", minimum=2) or 2)
    alpha = _number(args, "alpha") or DEFAULT_ALPHA
    power = _number(args, "power") or DEFAULT_POWER
    if not 0 < alpha < 0.5 or not 0.5 <= power < 1:
        raise ToolError("alpha must be between 0 and 0.5, power between 0.5 and 1")
    two_sided = str(args.get("two_sided", True)).strip().lower() not in {"false", "0", "no"}
    traffic = _number(args, "weekly_traffic", minimum=0.0)
    max_weeks = _number(args, "max_weeks", minimum=0.0)
    metric = str(args.get("metric") or "conversion rate").strip()

    p1 = baseline / 100
    p2 = p1 * (1 + effect / 100) if kind == "relative" else p1 + effect / 100
    if not 0 < p2 < 1:
        raise ToolError("the target rate falls outside 0–100 %; check minimum_effect and effect_kind")
    comparisons = variants - 1
    alpha_each = alpha / comparisons  # Bonferroni when several challengers share one control
    per_variant = _sample_per_variant(p1, p2, alpha_each, power, two_sided)
    total = per_variant * variants
    out: dict[str, Any] = {
        "inputs": {
            "baseline_rate_pct": baseline,
            "target_rate_pct": round(p2 * 100, 3),
            "minimum_effect": effect,
            "effect_kind": kind,
            "variants": variants,
            "alpha": alpha,
            "alpha_per_comparison": round(alpha_each, 4),
            "power": power,
            "two_sided": two_sided,
            "weekly_traffic": traffic,
            "max_weeks": max_weeks,
        },
        "sample_per_variant": per_variant,
        "total_sample": total,
        "missing": [],
        "warnings": [],
        "method": "normal approximation for two proportions; Bonferroni split of alpha across challengers",
    }
    if comparisons > 1:
        out["warnings"].append(f"{comparisons} challengers against one control: alpha is split to {alpha_each:.4f} per comparison, which raises the sample.")
    if per_variant < 100:
        out["warnings"].append("Fewer than 100 per variant: the normal approximation is rough at this size.")
    if traffic is None:
        out["missing"].append("weekly_traffic")
        out["weeks"] = None
        out["summary"] = (
            f"{per_variant:,} per variant ({total:,} in all) to detect {metric} moving from {baseline:g} % to "
            f"{p2 * 100:.3g} %. Duration is unknown until weekly_traffic is given; ask for it."
        )
    elif traffic <= 0:
        out["missing"].append("weekly_traffic")
        out["weeks"] = None
        out["summary"] = "weekly_traffic is zero: the test cannot run."
    else:
        weeks = math.ceil(total / traffic - 1e-9)
        out["weeks"] = weeks
        out["days"] = weeks * 7
        out["feasible_in_max_weeks"] = None if max_weeks is None else weeks <= max_weeks
        if max_weeks is not None and weeks > max_weeks:
            reachable = int(traffic * max_weeks / variants)
            if reachable > 0:
                mde = _detectable_relative_effect(p1, reachable, alpha_each, power, two_sided)
                out["detectable_in_max_weeks"] = {
                    "per_variant": reachable,
                    "relative_effect_pct": round(mde * 100, 1),
                    "target_rate_pct": round(p1 * (1 + mde) * 100, 3),
                }
                out["warnings"].append(
                    f"Needs {weeks} weeks but only {max_weeks:g} are available. In {max_weeks:g} weeks the smallest "
                    f"detectable lift is {mde * 100:.1f} % relative ({p1 * (1 + mde) * 100:.3g} %)."
                )
        if weeks < 2:
            out["warnings"].append("Run at least two full weeks anyway so every weekday is covered twice.")
        out["summary"] = (
            f"{per_variant:,} per variant ({total:,} in all): about {weeks} week{'s' if weeks != 1 else ''} at "
            f"{traffic:,.0f} eligible per week, to detect {metric} moving from {baseline:g} % to {p2 * 100:.3g} %."
        )
    tail = "two-sided" if two_sided else "one-sided"
    out["decision_rule"] = (
        f"Fix the sample in advance: {per_variant:,} per variant. Run whole weeks and do not stop early on a "
        f"good day. Call a variant the winner only if its {metric} beats the control by at least "
        f"{(p2 - p1) * 100:.3g} percentage points with a {tail} p-value below {alpha_each:.4g}"
        f"{' (per comparison)' if comparisons > 1 else ''}. Otherwise keep the control and record the result."
    )
    return out


def experiment_plan_tool(args: dict) -> str:
    return json.dumps(experiment_plan(args or {}), ensure_ascii=False, indent=1)


def unit_economics_tool(args: dict) -> str:
    """Backs the worker-facing ``unit_economics`` tool: JSON the worker can quote from."""
    return json.dumps(unit_economics(args or {}), ensure_ascii=False, indent=1)
