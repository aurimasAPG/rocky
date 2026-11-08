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


def unit_economics_tool(args: dict) -> str:
    """Backs the worker-facing ``unit_economics`` tool: JSON the worker can quote from."""
    return json.dumps(unit_economics(args or {}), ensure_ascii=False, indent=1)
