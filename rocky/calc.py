"""Marketing arithmetic workers must not do in prose.

``unit_economics`` answers the questions behind pricing, promotions, offer
design and acquisition decisions: what one sale contributes, how many sales a
price cut has to win back, where break-even sits, and which assumption would
flip the decision. Optional blocks on the same call cover the rest of a pricing
brief: markup next to margin and the cost derived from either; the realized
price after markdowns or a sales mix; several price plans ranked by net
contribution; a bundle against the items bought separately; tiers or variants
blended against the baseline with the cannibalization break-even; the price
buildup through a distribution chain with a back-solve to a shelf price; and
fixed, per-period and per-unit obligations with their break-even and payback.
Every function is pure, takes numbers with stated units, returns a dict, and
says plainly what is missing instead of guessing.
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


def _pct_of(numerator: float, denominator: float) -> float | None:
    """Percentage of a base that may be zero (free tier): None instead of a crash."""
    return None if denominator == 0 else _pct(numerator / denominator)


def _markup(contribution: float, cost: float) -> float | None:
    """Markup is contribution over cost; undefined when the cost is zero."""
    return None if cost <= 0 else _pct(contribution / cost)


def _field(row: dict, key: str, where: str, *, required: bool = False, minimum: float | None = None) -> float | None:
    """``_number`` on a nested object, with the object's place in the error message."""
    try:
        return _number(row, key, required=required, minimum=minimum)
    except ToolError as exc:
        raise ToolError(f"{where}: {exc}") from exc


def _parse_nested(raw: Any, key: str) -> Any:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise ToolError(f"{key} must be JSON, got {raw[:40]!r}") from exc
    return raw


def _rows(args: dict, key: str) -> list[dict] | None:
    raw = _parse_nested(args.get(key), key)
    if raw is None:
        return None
    if not isinstance(raw, list) or not raw or not all(isinstance(r, dict) for r in raw):
        raise ToolError(f"{key} must be a non-empty list of objects")
    return raw


def _obj(args: dict, key: str) -> dict | None:
    raw = _parse_nested(args.get(key), key)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ToolError(f"{key} must be an object")
    return raw


def _name(row: dict, fallback: str) -> str:
    return str(row.get("name") or row.get("label") or fallback).strip() or fallback


def _ceil(value: float) -> int:
    return math.ceil(value - 1e-9)


def unit_economics(args: dict) -> dict[str, Any]:
    """Contribution, break-even and the volume a price change must win back.

    Inputs (all per unit unless said): ``price`` (currency), ``variable_cost``
    (currency), ``units`` (current sales per period), ``fixed_costs`` (currency
    per period), and for a change: ``new_price`` or ``discount_pct``,
    ``new_variable_cost``, ``expected_uplift_pct`` (the volume gain the marketer
    believes the change will bring). ``currency`` defaults to EUR.

    Missing ``variable_cost`` does not stop the answer: the result lists it under
    ``missing`` and gives a ``conditional`` table across typical margins so the
    worker can ask for the real figure or label the assumption. ``margin_pct`` or
    ``markup_pct`` may stand in for ``variable_cost``: the cost is derived from the
    price and said so under ``assumptions``.

    Optional blocks, each only when its input is given: ``sales_mix`` or
    ``markdown_pct`` + ``markdown_share_pct`` → ``realized``; ``plans`` (with
    ``target_contribution``) → ``plans``; ``bundle`` → ``bundle``; ``variants`` →
    ``variants``; ``chain`` → ``chain``; ``obligations`` → ``obligations``. A price
    of 0 is accepted only together with ``obligations`` or ``variants`` (a free tier).
    """
    currency = str(args.get("currency") or "EUR").strip() or "EUR"
    plans = _rows(args, "plans")
    bundle = _obj(args, "bundle")
    variants = _rows(args, "variants")
    chain = _obj(args, "chain")
    obligations = _rows(args, "obligations")
    sales_mix = _rows(args, "sales_mix")
    markdown = _number(args, "markdown_pct")
    markdown_share = _number(args, "markdown_share_pct")
    target = _number(args, "target_contribution", minimum=0.0)

    price = _number(args, "price", required=True, minimum=0.0)
    free_tier = price == 0 and (obligations is not None or variants is not None)
    if price <= 0 and not free_tier:
        raise ToolError("price must be greater than zero")
    units = _number(args, "units", minimum=0.0)
    fixed = _number(args, "fixed_costs", minimum=0.0)
    cost = _number(args, "variable_cost", minimum=0.0)
    margin_in = _number(args, "margin_pct")
    markup_in = _number(args, "markup_pct")
    cost_from = None
    if cost is None and (margin_in is not None or markup_in is not None):
        if free_tier:
            raise ToolError("margin_pct or markup_pct cannot replace variable_cost when price is 0; give variable_cost")
        if margin_in is not None:
            if not 0 <= margin_in <= 100:
                raise ToolError("margin_pct must be between 0 and 100")
            cost, cost_from = price * (1 - margin_in / 100), "margin_pct"
        else:
            if markup_in < 0:
                raise ToolError("markup_pct must be 0 or more")
            cost, cost_from = price / (1 + markup_in / 100), "markup_pct"
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
    if markdown is not None or markdown_share is not None:
        if sales_mix is not None:
            raise ToolError("give sales_mix or markdown_pct + markdown_share_pct, not both")
        if markdown is None or markdown_share is None:
            raise ToolError("markdown_pct and markdown_share_pct go together: the depth of the markdown and the share of units sold at it")
        if not 0 <= markdown <= 100 or not 0 <= markdown_share <= 100:
            raise ToolError("markdown_pct and markdown_share_pct must be between 0 and 100")

    warnings: list[str] = []
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
    if cost_from:
        out["inputs"]["variable_cost_from"] = cost_from
        given = margin_in if cost_from == "margin_pct" else markup_in
        out["assumptions"].append(
            f"variable_cost {cost:.2f} {currency} is derived from {cost_from} {given:g} at price {price:g}; confirm it against the real unit cost."
        )
    if target is not None:
        out["inputs"]["target_contribution"] = _money(target)
    if free_tier:
        warnings.append("price is 0 (free tier): every unit costs money; the economics come from the obligations or the paid variants.")
        if cost is None:
            cost = 0.0
            out["missing"].append("variable_cost")
            warnings.append("variable_cost was not given for the free tier and is taken as 0: the serving cost per unit comes only from obligations per_unit.")

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
    else:
        if cost >= price and not free_tier:
            warnings.append(f"variable_cost {cost:g} is not below price {price:g}: every sale loses money before fixed costs.")
        result = _scenario(price, cost, units, fixed, new_price, new_cost, expected_uplift, currency)
        out.update(result)
        if new_price is not None:
            out["sensitivity"] = _sensitivity(price, cost, units, new_price, new_cost, expected_uplift)
        if target is not None:
            out["units_for_target"] = _ceil(target / (price - cost)) if price - cost > 0 else None

    summary = [out["summary"]]

    def attach(key: str, block: dict[str, Any]) -> None:
        out[key] = block
        if block.get("summary"):
            summary.append(block["summary"])

    def needs_cost(key: str) -> bool:
        if cost is None:
            attach(key, {"missing": ["variable_cost"], "summary": f"The {key} block needs variable_cost (or margin_pct / markup_pct); ask for it."})
            return True
        return False

    if (sales_mix is not None or markdown is not None) and not needs_cost("realized"):
        attach("realized", _realized(price, cost, sales_mix, markdown, markdown_share, currency))
    if plans is not None and not needs_cost("plans"):
        attach("plans", _plans(plans, price, cost, units, fixed, target, currency))
    if bundle is not None:
        attach("bundle", _bundle(bundle, currency, warnings))
    if variants is not None and not needs_cost("variants"):
        attach("variants", _variants(variants, price, cost, units, currency, warnings))
    if chain is not None:
        attach("chain", _chain(chain, currency))
    if obligations is not None and not needs_cost("obligations"):
        attach("obligations", _obligations(obligations, price, cost, units, fixed, currency))

    out["summary"] = " ".join(s for s in summary if s)
    if warnings:
        out["warnings"] = warnings
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
        "margin_pct": _pct_of(unit_contribution, price),
        "markup_pct": _markup(unit_contribution, cost),
    }
    if cost <= 0:
        row["markup_note"] = "Markup is undefined when the variable cost is 0."
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
        "new_markup_pct": _markup(new_unit, after_cost),
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
    if row["margin_pct"] is None:
        parts = [f"Each unit contributes {row['contribution_per_unit']:g} {currency} at a price of 0 (margin undefined)."]
    else:
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


# --- pricing blocks ---------------------------------------------------------


def _realized(
    price: float, cost: float, sales_mix: list[dict] | None, markdown: float | None, markdown_share: float | None, currency: str
) -> dict[str, Any]:
    """Initial markup against the markup actually realized after markdowns or a sales mix."""
    if sales_mix is not None:
        weighted = 0.0
        total_units = 0.0
        full_price_units = 0.0
        for i, row in enumerate(sales_mix):
            where = f"sales_mix[{i}]"
            p = _field(row, "price", where, required=True, minimum=0.0)
            u = _field(row, "units", where, required=True, minimum=0.0)
            weighted += p * u
            total_units += u
            if p >= price - 1e-9:
                full_price_units += u
        if total_units <= 0:
            raise ToolError("sales_mix units must add up to more than zero")
        realized = weighted / total_units
        full_price_share = full_price_units / total_units
        basis = f"{len(sales_mix)} price points, {total_units:g} units"
    else:
        assert markdown is not None and markdown_share is not None
        realized = price * (1 - markdown / 100 * markdown_share / 100)
        full_price_share = 1 - markdown_share / 100
        basis = f"{markdown:g} % off on {markdown_share:g} % of units"
    initial_markup = _markup(price - cost, cost)
    realized_markup = _markup(realized - cost, cost)
    block: dict[str, Any] = {
        "basis": basis,
        "list_price": _money(price),
        "realized_price": _money(realized),
        "price_erosion_pct": _pct((price - realized) / price),
        "full_price_share_pct": _pct(full_price_share),
        "initial_margin_pct": _pct((price - cost) / price),
        "realized_margin_pct": _pct_of(realized - cost, realized),
        "initial_markup_pct": initial_markup,
        "realized_markup_pct": realized_markup,
        "markup_erosion_pp": None if initial_markup is None or realized_markup is None else round(initial_markup - realized_markup, 1),
        "realized_contribution_per_unit": _money(realized - cost),
    }
    if cost <= 0:
        block["markup_note"] = "Markup is undefined when the variable cost is 0; margins are still given."
    if realized <= cost:
        block["note"] = "The realized price is at or below the variable cost: on average each unit loses money."
    if block["markup_erosion_pp"] is not None:
        block["summary"] = (
            f"Realized price {realized:.2f} {currency} against list {price:g}: markup falls from {initial_markup:g} % to "
            f"{realized_markup:g} % ({block['markup_erosion_pp']:g} pp lost to markdowns)."
        )
    else:
        block["summary"] = f"Realized price {realized:.2f} {currency} against list {price:g} ({block['price_erosion_pct']:g} % erosion)."
    return block


def _plans(
    plans: list[dict], price: float, cost: float, units: float | None, fixed: float | None, target: float | None, currency: str
) -> dict[str, Any]:
    """Several price plans against today's price, each with what it must win back and what it is expected to net."""
    base_unit = price - cost
    today_total = None if units is None else base_unit * units
    rows: list[dict[str, Any]] = []
    missing: set[str] = set()
    for i, plan in enumerate(plans):
        where = f"plans[{i}]"
        name = _name(plan, f"plan {i + 1}")
        new_price = _field(plan, "new_price", where, minimum=0.0)
        disc = _field(plan, "discount_pct", where)
        if new_price is None:
            if disc is None:
                raise ToolError(f"{where}: new_price or discount_pct is required")
            if not 0 <= disc <= 100:
                raise ToolError(f"{where}: discount_pct must be between 0 and 100")
            new_price = price * (1 - disc / 100)
        added = _field(plan, "added_variable_cost", where, minimum=0.0) or 0.0
        promo = _field(plan, "promo_cost", where, minimum=0.0) or 0.0
        volume_change = _field(plan, "expected_volume_change_pct", where)
        expected_units = _field(plan, "expected_units", where, minimum=0.0)
        plan_cost = cost + added
        c = new_price - plan_cost
        row: dict[str, Any] = {
            "name": name,
            "new_price": _money(new_price),
            "price_change_pct": _pct((new_price - price) / price) if price else None,
            "variable_cost": _money(plan_cost),
            "contribution_per_unit": _money(c),
            "margin_pct": _pct_of(c, new_price),
            "markup_pct": _markup(c, plan_cost),
            "promo_cost": _money(promo),
        }
        if c <= 0:
            row["required_units"] = None
            row["note"] = "Each sale contributes nothing or less at this price; no volume recovers today's contribution."
        else:
            if units is not None:
                required = (today_total + promo) / c
                row["required_units"] = _ceil(required)
                row["required_uplift_pct"] = _pct((required - units) / units) if units else None
            elif promo == 0:
                row["required_uplift_pct"] = _pct(base_unit / c - 1) if base_unit > 0 else None
            else:
                missing.add("units")
                row["note"] = "promo_cost needs units to say what volume recovers it."
            if target is not None:
                row["units_for_target"] = _ceil((target + promo) / c)
            if fixed is not None:
                row["break_even_units"] = _ceil((fixed + promo) / c)
        if expected_units is None and volume_change is not None:
            if units is None:
                missing.add("units")
                row["note"] = "expected_volume_change_pct needs units to turn into a total."
            else:
                expected_units = units * (1 + volume_change / 100)
        if volume_change is not None:
            row["expected_volume_change_pct"] = volume_change
        if expected_units is not None:
            total = c * expected_units - promo
            row["expected_units"] = round(expected_units, 1)
            row["expected_total_contribution"] = _money(total)
            if today_total is not None:
                row["net_vs_today"] = _money(total - today_total)
                row["decision"] = "plan pays" if total >= today_total - 1e-9 else "plan loses contribution"
        rows.append(row)
    block: dict[str, Any] = {
        "today": {"price": _money(price), "contribution_per_unit": _money(base_unit), "total_contribution": None if today_total is None else _money(today_total)},
        "plans": rows,
    }
    if missing:
        block["missing"] = sorted(missing)
    ranked = [r for r in rows if "net_vs_today" in r]
    if ranked:
        best = max(ranked, key=lambda r: r["net_vs_today"])
        block["best_plan"] = best["name"]
        if best["net_vs_today"] < 0:
            expected = best.get("expected_volume_change_pct")
            need = best.get("required_uplift_pct")
            block["note"] = (
                f"No plan beats today's contribution at the expected volumes; the best, {best['name']}, still needs "
                f"+{need:g} % volume" + (f" against +{expected:g} % expected." if expected is not None else ".")
                if need is not None
                else f"No plan beats today's contribution at the expected volumes; the best, {best['name']}, still loses {-best['net_vs_today']:g} {currency}."
            )
            block["summary"] = block["note"]
        else:
            block["summary"] = f"Of {len(rows)} plans, {best['name']} nets the most: {best['net_vs_today']:+g} {currency} against today."
    else:
        block["note"] = "Give expected_volume_change_pct or expected_units per plan (and units) to rank the plans by net contribution."
        block["summary"] = f"{len(rows)} plans priced; each shows the volume it must win back, none can be ranked without expected volumes."
    return block


def _bundle(bundle: dict, currency: str, warnings: list[str]) -> dict[str, Any]:
    """A bundle against the items bought separately, and the attach rate that makes the discount pay."""
    items = _parse_nested(bundle.get("items"), "bundle.items")
    if not isinstance(items, list) or len(items) < 2 or not all(isinstance(r, dict) for r in items):
        raise ToolError("bundle.items must list at least two objects with name, price and variable_cost")
    bundle_price = _field(bundle, "bundle_price", "bundle", required=True, minimum=0.0)
    attach = _field(bundle, "expected_attach_rate_pct", "bundle", minimum=0.0)
    if attach is not None and attach > 100:
        raise ToolError("bundle.expected_attach_rate_pct must be between 0 and 100")
    baseline_name = str(bundle.get("baseline_item") or "").strip()
    names = [_name(it, f"item {i + 1}") for i, it in enumerate(items)]
    if baseline_name and baseline_name not in names:
        raise ToolError(f"bundle.baseline_item {baseline_name!r} is not one of the items: {', '.join(names)}")
    baseline_index = names.index(baseline_name) if baseline_name else 0

    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    list_sum = 0.0
    cost_sum = 0.0
    for i, (it, name) in enumerate(zip(items, names)):
        where = f"bundle.items[{i}]"
        p = _field(it, "price", where, required=True, minimum=0.0)
        c = _field(it, "variable_cost", where, minimum=0.0)
        anyway = _field(it, "would_buy_anyway_pct", where, minimum=0.0)
        if anyway is not None and anyway > 100:
            raise ToolError(f"{where}: would_buy_anyway_pct must be between 0 and 100")
        if anyway is None:
            anyway = 100.0 if i == baseline_index else 0.0
        row: dict[str, Any] = {"name": name, "price": _money(p), "variable_cost": None if c is None else _money(c), "would_buy_anyway_pct": anyway}
        if i == baseline_index:
            row["baseline"] = True
        if c is None:
            missing.append(f"{where}.variable_cost")
        else:
            row["contribution"] = _money(p - c)
            cost_sum += c
        list_sum += p
        rows.append(row)
    if list_sum <= 0:
        raise ToolError("bundle.items prices must add up to more than zero")
    block: dict[str, Any] = {
        "items": rows,
        "bundle_price": _money(bundle_price),
        "list_price_sum": _money(list_sum),
        "discount_eur": _money(list_sum - bundle_price),
        "discount_pct": _pct((list_sum - bundle_price) / list_sum),
        "assumptions": [
            "would_buy_anyway_pct defaults to 100 for the baseline item and 0 for the others.",
            "Everyone who would have bought the add-ons at full price takes the bundle (they gain the discount for nothing).",
        ],
    }
    if bundle_price > list_sum:
        warnings.append(f"bundle_price {bundle_price:g} is above the sum of the item prices {list_sum:g}: no buyer gains from the bundle.")
    if missing:
        block["missing"] = missing
        block["summary"] = f"Bundle at {bundle_price:g} {currency} is {block['discount_pct']:g} % off the {list_sum:g} list sum; the margin needs every item's variable_cost."
        return block
    c_bundle = bundle_price - cost_sum
    base = rows[baseline_index]
    c_base = base["contribution"]
    addon_anyway = sum((r["price"] - r["variable_cost"]) * r["would_buy_anyway_pct"] / 100 for i, r in enumerate(rows) if i != baseline_index)
    counterfactual = c_base + addon_anyway
    block.update(
        {
            "bundle_cost": _money(cost_sum),
            "contribution_per_bundle": _money(c_bundle),
            "bundle_margin_pct": _pct_of(c_bundle, bundle_price),
            "baseline_item": base["name"],
            "baseline_contribution": _money(c_base),
            "counterfactual_contribution": _money(counterfactual),
            "incremental_contribution_vs_counterfactual": _money(c_bundle - counterfactual),
        }
    )
    gain = c_bundle - c_base
    if gain <= 0:
        block["breakeven_attach_rate_pct"] = None
        block["note"] = "The bundle contributes no more than the baseline item alone; no attach rate makes the discount pay."
    else:
        be = addon_anyway / gain
        block["breakeven_attach_rate_pct"] = _pct(be) if be <= 1 else None
        if be > 1:
            block["note"] = "Even if every baseline buyer took the bundle, the discount given to buyers who would have paid full price is not recovered."
    if attach is not None:
        a = attach / 100
        expected = a * c_bundle + (1 - a) * c_base
        block["expected_contribution_per_baseline_buyer"] = _money(expected)
        block["expected_gain_per_baseline_buyer"] = _money(expected - counterfactual)
        block["decision"] = "bundle pays" if expected >= counterfactual - 1e-9 else "bundle loses contribution"
        max_anyway = max((r["would_buy_anyway_pct"] for i, r in enumerate(rows) if i != baseline_index), default=0.0)
        if attach < max_anyway:
            warnings.append(f"expected_attach_rate_pct {attach:g} is below the {max_anyway:g} % who would buy the add-ons anyway; the expected figure is optimistic.")
    head = (
        f"Bundle at {bundle_price:g} {currency} ({block['discount_pct']:g} % off {list_sum:g}) contributes {c_bundle:.2f} per bundle "
        f"against {counterfactual:.2f} when items are bought separately."
    )
    if block["breakeven_attach_rate_pct"] is not None:
        head += f" It pays once {block['breakeven_attach_rate_pct']:g} % of {base['name']} buyers take it."
    if block.get("decision"):
        head += f" At {attach:g} % attach: {block['decision']}."
    block["summary"] = head
    return block


def _variants(
    variants: list[dict], price: float, cost: float, units: float | None, currency: str, warnings: list[str]
) -> dict[str, Any]:
    """Tiers or variants blended against the baseline price, and how much switching the blend can absorb."""
    base_c = price - cost
    rows: list[dict[str, Any]] = []
    share_sum = 0.0
    for i, v in enumerate(variants):
        where = f"variants[{i}]"
        name = _name(v, f"variant {i + 1}")
        vprice = _field(v, "price", where, required=True, minimum=0.0)
        share = _field(v, "share_pct", where, required=True, minimum=0.0)
        vcost = _field(v, "variable_cost", where, minimum=0.0)
        extra = _field(v, "extra_variable_cost", where, minimum=0.0)
        if vcost is None:
            vcost = cost + (extra or 0.0)
        c = vprice - vcost
        row: dict[str, Any] = {
            "name": name,
            "price": _money(vprice),
            "share_pct": share,
            "variable_cost": _money(vcost),
            "contribution_per_unit": _money(c),
            "margin_pct": _pct_of(c, vprice),
            "markup_pct": _markup(c, vcost),
            "contribution_vs_baseline": _money(c - base_c),
            "tier": "premium" if c > base_c + 1e-9 else "cheaper" if c < base_c - 1e-9 else "same",
        }
        if row["tier"] == "cheaper":
            row["new_buyers_per_switcher"] = round((base_c - c) / c, 2) if c > 0 else None
        note = str(v.get("effort_note") or "").strip()
        if note:
            row["effort_note"] = note
        rows.append(row)
        share_sum += share
    shares = [r["share_pct"] for r in rows]
    if share_sum > 100 + 1e-9:
        warnings.append(f"variant shares add up to {share_sum:g} %, above 100: they are scaled to 100 and the baseline keeps no share.")
        shares = [s * 100 / share_sum for s in shares]
    baseline_share = max(0.0, 100 - sum(shares))
    blended_price = sum(s / 100 * r["price"] for s, r in zip(shares, rows)) + baseline_share / 100 * price
    blended_c = sum(s / 100 * (r["price"] - r["variable_cost"]) for s, r in zip(shares, rows)) + baseline_share / 100 * base_c
    block: dict[str, Any] = {
        "baseline": {"price": _money(price), "variable_cost": _money(cost), "contribution_per_unit": _money(base_c), "share_pct": round(baseline_share, 1)},
        "variants": rows,
        "blended_price": _money(blended_price),
        "blended_contribution": _money(blended_c),
        "gain_per_unit_vs_baseline": _money(blended_c - base_c),
        "assumptions": ["share_pct is each variant's share of all buyers; the rest stay at the baseline price and total volume is unchanged."],
    }
    if units is not None:
        block["blended_total_contribution"] = _money(blended_c * units)
        block["gain_vs_today"] = _money((blended_c - base_c) * units)
    premium_gain = sum(s / 100 * (r["price"] - r["variable_cost"] - base_c) for s, r in zip(shares, rows) if r["tier"] == "premium")
    cheaper = [(s, r) for s, r in zip(shares, rows) if r["tier"] == "cheaper"]
    if not cheaper:
        block["cannibalization_breakeven_share_pct"] = None
        block["note"] = "No variant contributes less than the baseline, so switching cannot lower the total."
    else:
        weight = sum(s for s, _ in cheaper)
        loss = (
            sum(s * (base_c - (r["price"] - r["variable_cost"])) for s, r in cheaper) / weight
            if weight > 0
            else sum(base_c - (r["price"] - r["variable_cost"]) for _, r in cheaper) / len(cheaper)
        )
        be = premium_gain / loss
        block["cannibalization_breakeven_share_pct"] = _pct(be)
        block["cannibalization_note"] = (
            f"Up to {_pct(be):g} % of today's full-price buyers may move to the cheaper tier(s) before total contribution "
            f"falls below today's, given the premium shares as stated" + (" (no premium tier: any switch loses)." if premium_gain <= 0 else ".")
        )
    head = f"Blended price {blended_price:.2f} {currency}, blended contribution {blended_c:.2f} ({blended_c - base_c:+.2f} per unit against the baseline)."
    if block["cannibalization_breakeven_share_pct"] is not None:
        head += f" Switching to cheaper tiers breaks even at {block['cannibalization_breakeven_share_pct']:g} % of full-price buyers."
    block["summary"] = head
    return block


def _chain(chain: dict, currency: str) -> dict[str, Any]:
    """Price buildup from production cost to shelf price, stage by stage, with a back-solve to a target."""
    production = _field(chain, "production_cost", "chain", required=True, minimum=0.0)
    stages = _parse_nested(chain.get("stages"), "chain.stages")
    if not isinstance(stages, list) or not stages or not all(isinstance(s, dict) for s in stages):
        raise ToolError("chain.stages must be a non-empty list of objects with name, margin_pct and basis")
    vat = _field(chain, "vat_pct", "chain", minimum=0.0)
    target = _field(chain, "target_shelf_price", "chain", minimum=0.0)
    rows: list[dict[str, Any]] = []
    factors: list[tuple[float, float]] = []
    sell = production
    for i, s in enumerate(stages):
        where = f"chain.stages[{i}]"
        name = _name(s, f"stage {i + 1}")
        added = _field(s, "added_cost", where, minimum=0.0) or 0.0
        m = _field(s, "margin_pct", where, required=True, minimum=0.0)
        basis = str(s.get("basis") or "of_price").strip().lower()
        if basis not in {"of_price", "on_cost"}:
            raise ToolError(f"{where}: basis must be of_price (margin as a share of the selling price) or on_cost (markup on cost)")
        if basis == "of_price" and m >= 100:
            raise ToolError(f"{where}: margin_pct of_price must be below 100")
        factor = 1 / (1 - m / 100) if basis == "of_price" else 1 + m / 100
        buy = sell
        cost_in = buy + added
        sell = cost_in * factor
        factors.append((factor, added))
        rows.append(
            {
                "name": name,
                "buy_price": _money(buy),
                "added_cost": _money(added),
                "margin_pct": m,
                "basis": basis,
                "margin_eur": _money(sell - cost_in),
                "sell_price": _money(sell),
            }
        )
    net = sell
    gross = None if vat is None else net * (1 + vat / 100)
    block: dict[str, Any] = {
        "production_cost": _money(production),
        "stages": rows,
        "shelf_price_net": _money(net),
        "shelf_price_gross": None if gross is None else _money(gross),
        "vat_pct": vat,
        "producer_share_pct": _pct_of(production, net),
        "chain_multiplier": None if production <= 0 else round(net / production, 2),
    }
    if vat is None:
        block["missing"] = ["vat_pct"]
    consumer = net if gross is None else gross
    if target is not None:
        target_net = target if vat is None else target / (1 + vat / 100)
        back = target_net
        for factor, added in reversed(factors):
            back = back / factor - added
        diff = consumer - target
        block["target"] = {
            "target_shelf_price": _money(target),
            "target_shelf_price_net": _money(target_net),
            "current_shelf_price": _money(consumer),
            "gap": _money(diff),
            "gap_pct": _pct_of(diff, target),
            "verdict": "overshoots" if diff > 1e-9 else "undershoots" if diff < -1e-9 else "hits",
            "production_cost_for_target": _money(back) if back >= 0 else None,
        }
        if back < 0:
            block["target"]["note"] = "The chain's added costs and margins alone exceed the target; no production cost reaches it."
    kind = "gross" if gross is not None else "net (vat_pct missing)"
    head = f"Shelf price {consumer:.2f} {currency} {kind} from a production cost of {production:g}; the producer keeps {block['producer_share_pct']:g} % of the net shelf price."
    if target is not None:
        t = block["target"]
        head += f" Against the {target:g} target the chain {t['verdict']} by {abs(t['gap']):g}"
        head += f"; a production cost of {t['production_cost_for_target']:g} would hit it." if t["production_cost_for_target"] is not None else "."
    block["summary"] = head
    return block


def _obligations(
    obligations: list[dict], price: float, cost: float, units: float | None, fixed: float | None, currency: str
) -> dict[str, Any]:
    """Fixed, per-period and per-unit obligations added to the base economics: break-even and payback."""
    rows: list[dict[str, Any]] = []
    one_off_total = per_period_total = per_unit_total = 0.0
    for i, o in enumerate(obligations):
        where = f"obligations[{i}]"
        name = _name(o, f"obligation {i + 1}")
        one_off = _field(o, "one_off", where, minimum=0.0) or 0.0
        per_period = _field(o, "per_period", where, minimum=0.0) or 0.0
        per_unit = _field(o, "per_unit", where, minimum=0.0) or 0.0
        if one_off == per_period == per_unit == 0:
            raise ToolError(f"{where}: give one_off, per_period or per_unit (currency)")
        rows.append({"name": name, "one_off": _money(one_off), "per_period": _money(per_period), "per_unit": _money(per_unit)})
        one_off_total += one_off
        per_period_total += per_period
        per_unit_total += per_unit
    fixed_total = (fixed or 0.0) + per_period_total
    variable_total = cost + per_unit_total
    c = price - variable_total
    base_c = price - cost
    block: dict[str, Any] = {
        "items": rows,
        "one_off_total": _money(one_off_total),
        "per_period_total": _money(per_period_total),
        "per_unit_total": _money(per_unit_total),
        "fixed_total": _money(fixed_total),
        "variable_total": _money(variable_total),
        "marginal_cost_per_unit": _money(variable_total),
        "contribution_per_unit_after_obligations": _money(c),
        "margin_pct_after_obligations": _pct_of(c, price),
        "break_even_units_base": None if fixed is None or base_c <= 0 else _ceil(fixed / base_c),
        "break_even_units_with_obligations": _ceil(fixed_total / c) if c > 0 else None,
    }
    missing: list[str] = []
    if fixed is None:
        block["note"] = "fixed_costs was not given: fixed_total counts the per_period obligations only."
    if c <= 0:
        block["break_even_note"] = "After the per-unit obligations each unit contributes nothing or less; no volume breaks even."
    if units is None:
        missing.append("units")
    else:
        surplus = c * units - fixed_total
        block["period_surplus"] = _money(surplus)
        block["serving_cost_per_period"] = _money(variable_total * units + fixed_total)
        if one_off_total <= 0:
            block["payback_periods_for_one_off"] = 0
        elif surplus > 0:
            block["payback_periods_for_one_off"] = round(one_off_total / surplus, 1)
        else:
            block["payback_periods_for_one_off"] = None
            block["payback_note"] = "The period surplus at current units is not positive, so the one-off never pays back."
    if missing:
        block["missing"] = missing
    head = f"With obligations the marginal cost per unit is {variable_total:.2f} {currency} and fixed costs {fixed_total:.2f} per period"
    if block["break_even_units_with_obligations"] is not None:
        head += f"; break-even moves to {block['break_even_units_with_obligations']} units"
        if block["break_even_units_base"] is not None:
            head += f" from {block['break_even_units_base']}"
    head += "."
    if block.get("payback_periods_for_one_off"):
        head += f" The one-off {one_off_total:g} pays back in {block['payback_periods_for_one_off']:g} periods at current units."
    elif units is None:
        head += " Give units for the payback of the one-off."
    block["summary"] = head
    return block


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
