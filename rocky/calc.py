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
Further blocks cover perishable capacity, the cost of ownership with consumables,
offer mechanics (trial, refund, paid option, referral), a new line or
repositioning over a horizon with a price-history decomposition, retail and
channel arithmetic, media money against what it brings back, and routes scored
against ranked outcomes.
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
    return round(float(value), 2) + 0.0


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

    Further blocks, each switched on by its own object: ``capacity`` (unsold slots
    by time band and the shift break-even of an off-peak price), ``ownership``
    (total cost of a base product plus its consumable against an alternative),
    ``offers`` (trial-then-bill vs refund, a paid option such as express delivery,
    a referral reward), ``line`` (a new variant or premium line and a repositioning
    over a horizon, a period-by-period price history with its decomposition),
    ``retail`` (bulk breaking, assortment, channel risk, channel functions, the
    price waterfall, shelf displacement, routes with transit losses, a coupon
    promotion, delivered prices, payment terms, GMROI, a returns reserve, sales
    incentives), ``media_value`` (keyword test, incremental space, a catalogue
    drop, spend levels on profit, a public outcome, a partnership's three figures,
    a context-checked benchmark) and ``routes`` (routes scored against ranked
    outcomes). ``price`` may be left out when only ``ownership``, ``retail``,
    ``media_value`` or ``routes`` is asked for.
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
    capacity = _obj(args, "capacity")
    ownership = _obj(args, "ownership")
    offers = _rows(args, "offers")
    line = _obj(args, "line")
    retail = _obj(args, "retail")
    media = _obj(args, "media_value")
    routes = _obj(args, "routes")
    # Blocks that carry their own prices may be called without a base price.
    standalone = any(b is not None for b in (ownership, retail, media, routes))

    price = _number(args, "price", required=not standalone, minimum=0.0)
    free_tier = price == 0 and (obligations is not None or variants is not None)
    if price is not None and price <= 0 and not free_tier:
        raise ToolError("price must be greater than zero")
    units = _number(args, "units", minimum=0.0)
    fixed = _number(args, "fixed_costs", minimum=0.0)
    cost = _number(args, "variable_cost", minimum=0.0)
    margin_in = _number(args, "margin_pct")
    markup_in = _number(args, "markup_pct")
    cost_from = None
    if cost is None and price is not None and (margin_in is not None or markup_in is not None):
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
    if price is None and (new_price is not None or discount is not None or plans is not None or variants is not None or obligations is not None or sales_mix is not None or markdown is not None):
        raise ToolError("price is required to judge a price change, plans, variants, obligations or the realized price")
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
            "price": None if price is None else _money(price),
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

    if price is None:
        out["note"] = "price was not given: only the blocks that carry their own prices are computed."
        out["summary"] = ""
    elif cost is None:
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
        if price is None:
            attach(key, {"missing": ["price"], "summary": f"The {key} block needs price; ask for it."})
            return True
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
    if capacity is not None and not needs_cost("capacity"):
        attach("capacity", _capacity(capacity, price, cost, currency))
    if ownership is not None:
        attach("ownership", _ownership(ownership, currency))
    if offers is not None and not needs_cost("offers"):
        attach("offers", _offers(offers, args, price, cost, units, currency, warnings))
    if line is not None and not needs_cost("line"):
        attach("line", _line(line, price, cost, units, currency, warnings))
    if retail is not None:
        attach("retail", _retail(retail, price, cost, units, fixed, currency, warnings))
    if media is not None:
        attach("media_value", _media_value(media, price, cost, currency, warnings))
    if routes is not None:
        attach("routes", _routes(routes, currency))

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


# --- capacity, ownership, offers, line -------------------------------------

OCCUPANCY_LEVELS = (0.3, 0.5, 0.7)
RETURN_LEVELS = (5.0, 10.0, 20.0)
NON_PAYMENT_LEVELS = (2.0, 5.0, 10.0)
OPTION_TAKE_RATES = (10.0, 25.0, 50.0, 100.0)
SWITCHING_LEVELS = (0.0, 25.0, 50.0)
VOLUME_CHANGE_LEVELS = (-10.0, -20.0, -30.0)
REFERRAL_GRID = ((0.5, 1.0, 1.5), (10.0, 25.0, 40.0))
SELL_THROUGH_LEVELS = (60.0, 70.0, 80.0, 90.0)
LOST_BASKET_LEVELS = (0.0, 25.0, 50.0, 100.0)
REDEMPTION_LEVELS = (1.0, 3.0, 5.0)
FINANCE_RATE_LEVELS = (5.0, 9.0, 15.0)
CAMPAIGN_MARGINS = (30.0, 40.0, 50.0)
CHANNEL_FUNCTIONS = ("storage", "order handling", "delivery", "credit/collections", "returns", "selling/visits", "promotion", "stock risk")


def _share(row: dict, key: str, where: str, *, required: bool = False) -> float | None:
    """A percentage between 0 and 100, or None when absent."""
    value = _field(row, key, where, required=required, minimum=0.0)
    if value is not None and value > 100:
        raise ToolError(f"{where}: {key} must be between 0 and 100")
    return value


def _positive(row: dict, key: str, where: str, *, required: bool = False) -> float | None:
    value = _field(row, key, where, required=required, minimum=0.0)
    if value is not None and value <= 0:
        raise ToolError(f"{where}: {key} must be greater than zero")
    return value


def _list(parent: dict, key: str, where: str, *, required: bool = True) -> list[dict] | None:
    raw = _parse_nested(parent.get(key), f"{where}.{key}")
    if raw is None:
        if required:
            raise ToolError(f"{where}.{key} is required: a non-empty list of objects")
        return None
    if not isinstance(raw, list) or not raw or not all(isinstance(r, dict) for r in raw):
        raise ToolError(f"{where}.{key} must be a non-empty list of objects")
    return raw


def _sub(parent: dict, key: str, where: str) -> dict | None:
    raw = _parse_nested(parent.get(key), f"{where}.{key}")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ToolError(f"{where}.{key} must be an object")
    return raw


def _capacity(cap: dict, price: float, cost: float, currency: str) -> dict[str, Any]:
    """Perishable capacity by time band: unsold slots, their value at list, and how much shifting an off-peak price absorbs."""
    period = str(cap.get("period") or "period").strip() or "period"
    bands = _parse_nested(cap.get("bands"), "capacity.bands")
    if bands is None:
        bands = [{"band": cap.get("band") or "all", "capacity": cap.get("capacity_units"), "occupied": cap.get("occupied_units"), "occupancy_pct": cap.get("occupancy_pct")}]
    if not isinstance(bands, list) or not bands or not all(isinstance(b, dict) for b in bands):
        raise ToolError("capacity.bands must be a non-empty list of objects with band, capacity and occupied or occupancy_pct")
    offpeak = _field(cap, "offpeak_price", "capacity", minimum=0.0)
    if offpeak is not None and offpeak >= price:
        raise ToolError(f"capacity.offpeak_price {offpeak:g} must be below the list price {price:g}: an off-peak offer is a lower price")
    missing: list[str] = []
    rows: list[dict[str, Any]] = []
    unsold_total = 0.0
    for i, b in enumerate(bands):
        where = f"capacity.bands[{i}]"
        name = str(b.get("band") or b.get("name") or f"band {i + 1}").strip()
        capacity = _positive(b, "capacity", where, required=True)
        occupied = _field(b, "occupied", where, minimum=0.0)
        occ_pct = _share(b, "occupancy_pct", where)
        if occupied is None and occ_pct is not None:
            occupied = capacity * occ_pct / 100
        if occupied is not None and occupied > capacity + 1e-9:
            raise ToolError(f"{where}: occupied {occupied:g} is more than capacity {capacity:g}")
        row: dict[str, Any] = {"band": name, "capacity": capacity}
        if occupied is None:
            missing.append(f"{where}.occupied")
            row["conditional"] = [
                {"assumed_occupancy_pct": _pct(level), "unsold_units": round(capacity * (1 - level), 1), "unsold_value_at_list": _money(capacity * (1 - level) * price)}
                for level in OCCUPANCY_LEVELS
            ]
        else:
            unsold = capacity - occupied
            unsold_total += unsold
            row.update(
                {
                    "occupied": round(occupied, 1),
                    "occupancy_pct": _pct(occupied / capacity),
                    "unsold_units": round(unsold, 1),
                    "unsold_value_at_list": _money(unsold * price),
                    "new_bookings_to_fill": round(unsold, 1),
                }
            )
            if offpeak is not None and offpeak > cost:
                row["max_gain_per_period"] = _money(unsold * (offpeak - cost))
        rows.append(row)
    block: dict[str, Any] = {
        "period": period,
        "list_price": _money(price),
        "variable_cost": _money(cost),
        "bands": rows,
        "unsold_units_total": round(unsold_total, 1),
        "unsold_value_at_list_total": _money(unsold_total * price),
        "break_even_discount_pct": _pct((price - cost) / price) if price > cost else None,
        "assumptions": [
            "A shifted booking is a regular who would have paid list price in another band; its slot there is not refilled.",
            "break_even_discount_pct is the deepest discount at which a filled slot still covers its variable cost.",
        ],
    }
    if missing:
        block["missing"] = missing
    head = f"{unsold_total:g} slots unsold per {period} across {len(rows)} band(s), {unsold_total * price:.2f} {currency} at list."
    if offpeak is None:
        block.setdefault("missing", []).append("offpeak_price")
        block["summary"] = head + " Give offpeak_price for the contribution of filling them and the shift break-even."
        return block
    fill = offpeak - cost
    loss = price - offpeak
    offer: dict[str, Any] = {
        "offpeak_price": _money(offpeak),
        "discount_pct": _pct(loss / price),
        "contribution_per_filled_slot": _money(fill),
        "loss_per_shifted_booking": _money(loss),
    }
    if fill <= 0:
        offer["shift_break_even_pct"] = None
        offer["note"] = "The off-peak price does not cover the variable cost: no fill is profitable."
        head += f" At {offpeak:g} a filled slot loses {-fill:.2f}; the offer cannot pay."
    else:
        offer["shift_break_even_pct"] = _pct(fill / (fill + loss))
        offer["new_share_needed_pct"] = _pct(loss / (fill + loss))
        offer["max_gain_per_period"] = _money(unsold_total * fill)
        head += (
            f" At {offpeak:g} a filled slot contributes {fill:.2f}; each regular who shifts costs {loss:.2f}; the offer stays positive while"
            f" shifted bookings are under {offer['shift_break_even_pct']:g} % of off-peak bookings. Filling every unsold slot with new clients adds"
            f" {unsold_total * fill:.2f} per {period}."
        )
    block["offer"] = offer
    block["summary"] = head
    return block


def _ownership(own: dict, currency: str) -> dict[str, Any]:
    """Total cost of ownership of a base product plus its consumable, against an alternative."""
    base = _field(own, "base_price", "ownership", required=True, minimum=0.0)
    cons = _field(own, "consumable_price", "ownership", required=True, minimum=0.0)
    periods = _positive(own, "periods", "ownership", required=True)
    label = str(own.get("period_label") or "period").strip() or "period"
    usage = _positive(own, "consumable_units_per_period", "ownership")
    alt = _sub(own, "alternative", "ownership")
    block: dict[str, Any] = {
        "base_price": _money(base),
        "consumable_price": _money(cons),
        "periods": periods,
        "period_label": label,
        "consumable_cost_per_unit_of_usage": _money(cons * periods),
        "assumptions": [f"consumable_cost_per_unit_of_usage is what one consumable per {label} costs over the {periods:g} {label}s."],
    }
    alt_base = alt_cons = None
    alt_name = "alternative"
    if alt is not None:
        alt_name = _name(alt, "alternative")
        alt_base = _field(alt, "base_price", "ownership.alternative", required=True, minimum=0.0)
        alt_cons = _field(alt, "consumable_price", "ownership.alternative", required=True, minimum=0.0)
        d_base = alt_base - base
        d_cons = cons - alt_cons
        block["alternative"] = {"name": alt_name, "base_price": _money(alt_base), "consumable_price": _money(alt_cons)}
        if d_base * d_cons > 0:
            block["alternative"]["tie_usage_per_period"] = round(d_base / (d_cons * periods), 2)
        else:
            block["alternative"]["tie_usage_per_period"] = None
    if usage is None:
        block["missing"] = ["consumable_units_per_period"]
        head = f"Base {base:g} {currency}; every consumable per {label} adds {cons * periods:.2f} over {periods:g} {label}s. Give consumable_units_per_period for the total."
        if alt is not None and block["alternative"]["tie_usage_per_period"] is not None:
            head += f" The two options tie at {block['alternative']['tie_usage_per_period']:g} consumables per {label}."
        block["summary"] = head
        return block
    cons_total_units = usage * periods
    cons_cost = cons * cons_total_units
    total = base + cons_cost
    block.update(
        {
            "consumable_units_per_period": usage,
            "consumable_units_total": round(cons_total_units, 1),
            "consumable_cost": _money(cons_cost),
            "total_ownership_cost": _money(total),
            "consumable_share_pct": _pct_of(cons_cost, total),
            "cost_per_period": _money(total / periods),
        }
    )
    head = f"Total cost of ownership {total:.2f} {currency} over {periods:g} {label}s ({cons_cost:.2f} in consumables, {block['consumable_share_pct']:g} %), {total / periods:.2f} per {label}."
    if alt is not None:
        alt_total = alt_base + alt_cons * cons_total_units
        diff = total - alt_total
        a = block["alternative"]
        a.update({"total_ownership_cost": _money(alt_total), "difference": _money(diff), "cheaper": alt_name if diff > 1e-9 else "ours" if diff < -1e-9 else "same"})
        d_base = alt_base - base
        d_cons_per_period = (cons - alt_cons) * usage
        if d_base * d_cons_per_period > 0:
            cross = d_base / d_cons_per_period
            a["crossover_periods"] = round(cross, 1)
            cheaper_first = "ours" if d_base > 0 else alt_name
            dearer_after = alt_name if d_base > 0 else "ours"
            a["note"] = f"{cheaper_first} is cheaper until {label} {cross:.1f}; after that {dearer_after} is cheaper."
            head += f" {alt_name}: {alt_total:.2f} ({diff:+.2f} against ours); the lines cross after {cross:.1f} {label}s."
        else:
            a["crossover_periods"] = None
            a["note"] = "The lines never cross: one option is cheaper on both the base and the consumable."
            head += f" {alt_name}: {alt_total:.2f} ({diff:+.2f} against ours); the lines never cross."
    block["summary"] = head
    return block


def _offers(offers: list[dict], args: dict, price: float, cost: float, units: float | None, currency: str, warnings: list[str]) -> dict[str, Any]:
    """Offer mechanics on one base: trial-then-bill or refund, a paid option such as express delivery, a referral reward."""
    base_c = price - cost
    exposures = _positive(args, "exposures", "exposures")
    conv_top = _share(args, "baseline_conversion_pct", "baseline_conversion_pct")
    base_orders = units
    if base_orders is None and exposures is not None and conv_top is not None:
        base_orders = exposures * conv_top / 100
    per_1000 = (lambda v: _money(v * 1000 / exposures)) if exposures else None
    missing: list[str] = []
    rows: list[dict[str, Any]] = []
    block: dict[str, Any] = {
        "base": {"price": _money(price), "variable_cost": _money(cost), "contribution_per_unit": _money(base_c), "orders": base_orders},
        "assumptions": [
            "take_up_pct is the change in orders the offer brings against today's orders; counts are rounded to whole orders.",
            "A returned unit is restocked at no loss unless returned_unit_loss says otherwise; an unpaid unit is lost at its variable cost.",
        ],
    }
    if base_orders is not None:
        block["base"]["total_contribution"] = _money(base_c * base_orders)
        if per_1000:
            block["base"]["net_contribution_per_1000_exposures"] = per_1000(base_c * base_orders)
    for i, o in enumerate(offers):
        where = f"offers[{i}]"
        kind = str(o.get("kind") or "").strip().lower()
        if kind not in {"trial_then_bill", "refund", "option", "referral"}:
            raise ToolError(f"{where}: kind must be trial_then_bill, refund, option or referral")
        name = _name(o, kind.replace("_", " "))
        if kind == "option":
            block["option"] = _option(o, where, name, base_c, conv_top, missing)
            continue
        if kind == "referral":
            block["referral"] = _referral(o, where, name, base_c, missing, warnings)
            continue
        take_up = _field(o, "take_up_pct", where, minimum=-100.0) or 0.0
        ret = _share(o, "return_pct", where)
        non_pay = _share(o, "non_payment_pct", where)
        if kind == "refund":
            if non_pay not in (None, 0.0):
                raise ToolError(f"{where}: a refund offer is paid up front; non_payment_pct must be 0 or left out")
            non_pay = 0.0
        coll = _field(o, "collection_cost_per_order", where, minimum=0.0) or 0.0
        per_return = sum(_field(o, k, where, minimum=0.0) or 0.0 for k in ("return_shipping_cost", "refund_handling_cost", "returned_unit_loss"))
        row: dict[str, Any] = {"name": name, "kind": kind, "take_up_pct": take_up, "return_pct": ret, "non_payment_pct": non_pay}

        def per_order(r: float, n: float) -> float:
            kept = 1 - r / 100
            return kept * (1 - n / 100) * base_c - kept * n / 100 * cost - r / 100 * per_return - coll

        if ret is None or non_pay is None:
            for key, value in (("return_pct", ret), ("non_payment_pct", non_pay)):
                if value is None:
                    missing.append(f"{where}.{key}")
            rets = RETURN_LEVELS if ret is None else (ret,)
            nps = NON_PAYMENT_LEVELS if non_pay is None else (non_pay,)
            row["conditional"] = [
                {"assumed_return_pct": r, "assumed_non_payment_pct": n, "net_contribution_per_order": _money(per_order(r, n))} for r in rets for n in nps
            ]
            rows.append(row)
            continue
        row["net_contribution_per_order"] = _money(per_order(ret, non_pay))
        row["_A"] = (1 - non_pay / 100) * base_c - non_pay / 100 * cost
        row["_R"] = per_return
        row["_coll"] = coll
        if base_orders is not None:
            orders = round(base_orders * (1 + take_up / 100))
            returned = round(orders * ret / 100)
            unpaid = round((orders - returned) * non_pay / 100)
            retained = orders - returned - unpaid
            net = retained * base_c - unpaid * cost - returned * per_return - orders * coll
            row.update({"orders": orders, "returned": returned, "unpaid": unpaid, "retained_customers": retained, "net_contribution": _money(net)})
            row["net_contribution_per_retained"] = _money(net / retained) if retained > 0 else None
            row["gain_vs_no_offer"] = _money(net - base_c * base_orders)
            if per_1000:
                row["net_contribution_per_1000_exposures"] = per_1000(net)
        rows.append(row)
    if rows:
        block["offers"] = rows
        ranked = [r for r in rows if "net_contribution" in r]
        if base_orders is None and any("net_contribution_per_order" in r for r in rows):
            missing.append("units")
        if len(ranked) >= 2:
            ranked.sort(key=lambda r: r["net_contribution"], reverse=True)
            best, second = ranked[0], ranked[1]
            block["best_offer"] = best["name"]
            k = second["net_contribution"] / best["orders"] + best["_coll"] if best["orders"] else None
            tie = None if k is None else (best["_A"] - k) / (best["_A"] + best["_R"]) if best["_A"] + best["_R"] > 0 else None
            if tie is not None and 0 <= tie <= 1:
                block["tie_point"] = {"offer": best["name"], "against": second["name"], "return_pct": _pct(tie), "sentence": f"{best['name']} beats {second['name']} until its return rate exceeds {_pct(tie):g} %."}
            block["summary"] = (
                f"{best['name']} nets {best['net_contribution']:.2f} {currency} against {second['net_contribution']:.2f} for {second['name']}"
                f" (no offer: {base_c * base_orders:.2f})." + (f" {block['tie_point']['sentence']}" if "tie_point" in block else "")
            )
        elif len(ranked) == 1:
            block["best_offer"] = ranked[0]["name"]
            block["summary"] = f"{ranked[0]['name']} nets {ranked[0]['net_contribution']:.2f} {currency} against {base_c * base_orders:.2f} with no offer."
        else:
            block["summary"] = f"{len(rows)} offer(s) priced per order placed; give units (or exposures and baseline_conversion_pct) and every rate for the volume figures."
        for r in rows:
            for k in ("_A", "_R", "_coll"):
                r.pop(k, None)
    if missing:
        block["missing"] = sorted(set(missing))
    parts = [block.get("summary"), block.get("option", {}).get("summary"), block.get("referral", {}).get("summary")]
    block["summary"] = " ".join(p for p in parts if p) or "No offer could be priced; see missing."
    return block


def _option(o: dict, where: str, name: str, base_c: float, conv_top: float | None, missing: list[str]) -> dict[str, Any]:
    """A paid option (express delivery, instant access) that costs more than its fee: the conversion lift it must bring."""
    fee = _field(o, "option_fee", where, required=True, minimum=0.0)
    ocost = _field(o, "option_cost", where, minimum=0.0)
    take = _share(o, "take_rate_pct", where, required=True)
    std_fee = _field(o, "standard_fee", where, minimum=0.0) or 0.0
    std_cost = _field(o, "standard_cost", where, minimum=0.0) or 0.0
    conv = _share(o, "baseline_conversion_pct", where)
    conv = conv_top if conv is None else conv
    visits = _positive(o, "weekly_visits", where)
    c_std = base_c + std_fee - std_cost
    out: dict[str, Any] = {
        "name": name,
        "option_fee": _money(fee),
        "take_rate_pct": take,
        "contribution_per_order_standard": _money(c_std),
        "option_cost_at_which_option_is_neutral": _money(fee - std_fee + std_cost),
        "assumptions": ["Every order pays the standard fee and cost unless it takes the option; the option replaces them."],
    }
    if ocost is None:
        missing.append(f"{where}.option_cost")
        out["summary"] = f"{name}: give option_cost; the option neither adds nor loses contribution at a cost of {fee - std_fee + std_cost:.2f}."
        return out
    c_opt = base_c + fee - ocost
    delta = c_opt - c_std

    def blended(t: float) -> float:
        return c_std + t / 100 * delta

    out.update({"option_cost": _money(ocost), "contribution_per_order_with_option": _money(c_opt), "option_delta_per_order": _money(delta), "blended_contribution_per_order": _money(blended(take))})
    if blended(take) <= 0:
        out["required_conversion_lift_pct"] = None
        out["note"] = "At this take rate the blended order contributes nothing or less; no conversion lift recovers it."
        out["summary"] = f"{name}: blended contribution per order is {blended(take):.2f}; the option cannot pay at a {take:g} % take rate."
        return out
    lift = c_std / blended(take) - 1
    out["required_conversion_lift_pct"] = _pct(lift)
    out["sensitivity"] = [
        {"take_rate_pct": t, "blended_contribution_per_order": _money(blended(t)), "required_conversion_lift_pct": _pct(c_std / blended(t) - 1) if blended(t) > 0 else None}
        for t in OPTION_TAKE_RATES
    ]
    head = f"{name}: an order with the option contributes {c_opt:.2f} ({delta:+.2f} against {c_std:.2f}); blended at {take:g} % take-up {blended(take):.2f}, so conversion must rise {_pct(lift):g} %"
    if conv is not None:
        req = conv * (1 + lift)
        out["baseline_conversion_pct"] = conv
        out["required_conversion_pct"] = round(req, 2)
        out["required_conversion_lift_pp"] = round(req - conv, 2)
        head += f" (from {conv:g} % to {req:.2f} %)"
        if visits is not None:
            out["extra_orders_per_week_needed"] = round(visits * (req - conv) / 100, 1)
    else:
        missing.append(f"{where}.baseline_conversion_pct")
    out["verdict"] = "option adds contribution on every order; no lift needed" if delta >= 0 else "needs the conversion lift above"
    out["summary"] = head + "."
    return out


def _referral(o: dict, where: str, name: str, base_c: float, missing: list[str], warnings: list[str]) -> dict[str, Any]:
    """Give-and-get referral rewards: cost per acquired customer, liability and whether the reward fits inside the contribution."""
    rr = _field(o, "reward_referrer", where, minimum=0.0) or 0.0
    re_ = _field(o, "reward_referee", where, minimum=0.0) or 0.0
    referrers = _field(o, "referrers", where, required=True, minimum=0.0)
    rpr = _field(o, "referrals_per_referrer", where, minimum=0.0)
    conv = _share(o, "referee_conversion_pct", where)
    cap = _field(o, "payout_cap_per_referrer", where, minimum=0.0)
    total_reward = rr + re_
    net = base_c - total_reward
    out: dict[str, Any] = {
        "name": name,
        "reward_per_referred_sale": _money(total_reward),
        "cost_per_acquired_customer": _money(total_reward),
        "net_contribution_per_referred_sale": _money(net),
        "max_total_reward": _money(base_c),
        "decision": "pays per referred sale" if net > 1e-9 else f"never pays at this reward; the most the two rewards can add up to is {base_c:.2f}",
    }
    if cap is None:
        out["worst_case_liability"] = None
        warnings.append(f"{where}: no payout_cap_per_referrer, so the liability is unbounded; set a cap and a grandfathering sentence before launch.")
    else:
        out["worst_case_liability"] = _money(referrers * cap)
    if rpr is None or conv is None:
        for key, value in (("referrals_per_referrer", rpr), ("referee_conversion_pct", conv)):
            if value is None:
                missing.append(f"{where}.{key}")
        out["conditional"] = [
            {"assumed_referrals_per_referrer": r, "assumed_referee_conversion_pct": c, "referred_sales": round(referrers * r * c / 100), "total_reward_cost": _money(referrers * r * c / 100 * total_reward)}
            for r in (REFERRAL_GRID[0] if rpr is None else (rpr,))
            for c in (REFERRAL_GRID[1] if conv is None else (conv,))
        ]
        out["summary"] = f"{name}: each referred sale nets {net:.2f} after {total_reward:.2f} of rewards; give referrals_per_referrer and referee_conversion_pct for the volume and liability."
        return out
    sales = referrers * rpr * conv / 100
    out.update({"referred_sales": round(sales), "total_reward_cost": _money(sales * total_reward), "net_contribution_total": _money(sales * net)})
    out["summary"] = f"{name}: {round(sales)} referred sales cost {sales * total_reward:.2f} in rewards ({total_reward:.2f} per acquired customer) and net {net:.2f} each; {out['decision']}."
    return out


def _payback(gains: list[float], investment: float) -> float | None:
    """Months until cumulative monthly gains cover the investment; fractional inside the month, None if never."""
    if investment <= 0:
        return 0.0
    cumulative = 0.0
    for m, gain in enumerate(gains):
        if gain > 0 and cumulative + gain >= investment - 1e-9:
            return round(m + (investment - cumulative) / gain, 1)
        cumulative += gain
    return None


def _line(line: dict, price: float, cost: float, units: float | None, currency: str, warnings: list[str]) -> dict[str, Any]:
    """A new variant or premium line, a repositioning of the base, and a period-by-period price history, on one base."""
    horizon = _positive(line, "horizon_months", "line") or 24.0
    horizon_n = _ceil(horizon)
    base_c = price - cost
    base_month = None if units is None else base_c * units
    block: dict[str, Any] = {
        "horizon_months": horizon_n,
        "base": {"price": _money(price), "variable_cost": _money(cost), "contribution_per_unit": _money(base_c), "units_per_month": units, "contribution_per_month": None if base_month is None else _money(base_month)},
        "assumptions": ["All volumes are per month in one currency; development and launch costs fall in month 1."],
    }
    missing: list[str] = []
    summary: list[str] = []
    new = _sub(line, "new", "line")
    repos = _sub(line, "reposition", "line")
    periods = _list(line, "periods", "line", required=False)
    if new is None and repos is None and periods is None:
        raise ToolError("line needs at least one of new, reposition or periods")

    if new is not None:
        where = "line.new"
        name = _name(new, "new line")
        nprice = _field(new, "price", where, required=True, minimum=0.0)
        ncost = _field(new, "variable_cost", where, minimum=0.0)
        nmargin = _share(new, "margin_pct", where)
        if ncost is None:
            if nmargin is None:
                raise ToolError(f"{where}: variable_cost or margin_pct is required")
            ncost = nprice * (1 - nmargin / 100)
        per_month = _field(new, "expected_units_per_month", where, minimum=0.0)
        forecast = _field(new, "forecast_units", where, minimum=0.0)
        if per_month is not None and forecast is not None:
            raise ToolError(f"{where}: give expected_units_per_month or forecast_units (total over the horizon), not both")
        if per_month is None:
            if forecast is None:
                raise ToolError(f"{where}: expected_units_per_month or forecast_units is required")
            per_month = forecast / horizon_n
        dev = _field(new, "development_cost", where, minimum=0.0) or 0.0
        launch = _field(new, "launch_cost", where, minimum=0.0) or 0.0
        switching = _share(new, "switching_pct", where)
        if switching is None:
            switching = _share(new, "cannibalisation_pct", where)
        cann_c = _field(new, "cannibalised_contribution_per_unit", where, minimum=0.0)
        cann_c = base_c if cann_c is None else cann_c
        c_new = nprice - ncost
        invest = dev + launch
        row: dict[str, Any] = {
            "name": name,
            "price": _money(nprice),
            "variable_cost": _money(ncost),
            "contribution_per_unit": _money(c_new),
            "units_per_month": round(per_month, 1),
            "investment": _money(invest),
            "cannibalised_contribution_per_unit": _money(cann_c),
            "gross_contribution_per_month": _money(c_new * per_month),
            "revenue_over_horizon": _money(nprice * per_month * horizon_n),
        }

        def incremental(s: float) -> float:
            return per_month * (c_new - s / 100 * cann_c)

        if switching is None:
            missing.append(f"{where}.switching_pct")
            row["conditional"] = [{"assumed_switching_pct": s, "incremental_contribution_per_month": _money(incremental(s)), "net_over_horizon": _money(incremental(s) * horizon_n - invest)} for s in SWITCHING_LEVELS]
            summary.append(f"{name}: give switching_pct (the share of its buyers who come from the base); without it the verdict is withheld.")
        else:
            incr = incremental(switching)
            per_unit_net = c_new - switching / 100 * cann_c
            row.update(
                {
                    "switching_pct": switching,
                    "cannibalised_contribution_per_month": _money(per_month * switching / 100 * cann_c),
                    "incremental_contribution_per_month": _money(incr),
                    "incremental_contribution_over_horizon": _money(incr * horizon_n),
                    "net_over_horizon": _money(incr * horizon_n - invest),
                    "roi_pct": None if invest <= 0 else _pct((incr * horizon_n - invest) / invest),
                    "payback_months": _payback([incr] * horizon_n, invest) if incr > 0 else None,
                    "break_even_units": _ceil(invest / per_unit_net) if per_unit_net > 0 else None,
                }
            )
            flip = None if cann_c <= 0 else c_new / cann_c
            if flip is not None and flip <= 1:
                row["switching_flip_pct"] = _pct(flip)
            else:
                row["switching_flip_pct"] = None
                row["flip_note"] = f"Even if every buyer switched from the base the line would still add {incremental(100):.2f} per month."
            row["verdict"] = "clears_hurdle" if incr * horizon_n - invest > 1e-9 else "below_hurdle"
            if row["payback_months"] is None and incr > 0:
                row["payback_note"] = f"The investment pays back after the {horizon_n}-month horizon ({invest / incr:.1f} months)."
            summary.append(
                f"{name} adds {incr:.2f} {currency} per month after {switching:g} % switching ({incr * horizon_n - invest:+.2f} over {horizon_n} months net of {invest:.2f}); "
                + (f"payback {row['payback_months']:g} months; " if row["payback_months"] is not None else "")
                + (f"break-even {row['break_even_units']} units; " if row["break_even_units"] is not None else "")
                + f"verdict {row['verdict']}."
            )
        alt = _sub(new, "alternative", where)
        if alt is not None:
            spend = _field(alt, "spend", f"{where}.alternative", required=True, minimum=0.0)
            alt_units = _field(alt, "expected_incremental_units", f"{where}.alternative", required=True, minimum=0.0)
            alt_c = _field(alt, "contribution_per_unit", f"{where}.alternative", minimum=0.0)
            alt_c = base_c if alt_c is None else alt_c
            alt_net = alt_units * alt_c - spend
            row["alternative"] = {"spend": _money(spend), "expected_incremental_units": alt_units, "contribution_per_unit": _money(alt_c), "net": _money(alt_net)}
            if "net_over_horizon" in row:
                row["alternative"]["better"] = "alternative" if alt_net > row["net_over_horizon"] + 1e-9 else name
                summary.append(f"The same money on the existing product nets {alt_net:+.2f}; {row['alternative']['better']} is better.")
        block["new"] = row

    if repos is not None:
        where = "line.reposition"
        new_price = _field(repos, "new_price", where, required=True, minimum=0.0)
        new_cost = _field(repos, "new_variable_cost", where, minimum=0.0)
        new_cost = cost if new_cost is None else new_cost
        vol = _field(repos, "volume_change_pct", where, minimum=-100.0)
        one_off = _field(repos, "one_off_cost", where, minimum=0.0) or 0.0
        trans_m = _field(repos, "transition_months", where, minimum=0.0) or 0.0
        trans_n = min(_ceil(trans_m), horizon_n)
        trans_vol = _field(repos, "transition_volume_change_pct", where, minimum=-100.0)
        new_c = new_price - new_cost
        row = {"new_price": _money(new_price), "variable_cost": _money(new_cost), "contribution_per_unit": _money(new_c), "one_off_cost": _money(one_off), "transition_months": trans_n}
        if units is None:
            missing.append("units")
            row["note"] = "units per month is needed to value the repositioning."
        elif vol is None:
            missing.append(f"{where}.volume_change_pct")
            row["conditional"] = [{"assumed_volume_change_pct": v, "contribution_per_month_after": _money(units * (1 + v / 100) * new_c), "gain_per_month": _money(units * (1 + v / 100) * new_c - base_month)} for v in VOLUME_CHANGE_LEVELS]
            summary.append("Repositioning: give volume_change_pct; without it the verdict is withheld.")
        else:
            trans_vol = vol if trans_vol is None else trans_vol
            steady = units * (1 + vol / 100) * new_c
            trans = units * (1 + trans_vol / 100) * new_c
            gains = [trans - base_month] * trans_n + [steady - base_month] * (horizon_n - trans_n)
            cumulative = sum(gains) - one_off
            flip = base_c / new_c - 1 if new_c > 0 else None
            row.update(
                {
                    "volume_change_pct": vol,
                    "transition_volume_change_pct": trans_vol,
                    "contribution_per_month_after": _money(steady),
                    "gain_per_month_after": _money(steady - base_month),
                    "contribution_per_month_in_transition": _money(trans),
                    "gain_per_month_in_transition": _money(trans - base_month),
                    "net_over_horizon": _money(cumulative),
                    "payback_months": _payback(gains, one_off),
                    "volume_change_flip_pct": None if flip is None else _pct(flip),
                    "verdict": "clears_hurdle" if cumulative > 1e-9 else "below_hurdle",
                }
            )
            summary.append(
                f"Repositioning at {new_price:g} adds {steady - base_month:+.2f} per month after {trans_n} transition months ({trans - base_month:+.2f} during them), {cumulative:+.2f} over {horizon_n} months net of {one_off:.2f}"
                + (f"; payback {row['payback_months']:g} months" if row["payback_months"] is not None else "")
                + (f"; it adds nothing once volume falls {abs(flip) * 100:.1f} %." if flip is not None and flip < 0 else ".")
            )
        block["reposition"] = row
        if new is not None and "net_over_horizon" in block["new"] and "net_over_horizon" in row:
            a, b = block["new"]["net_over_horizon"], row["net_over_horizon"]
            block["comparison"] = {"better": block["new"]["name"] if a >= b else "reposition", "gap_over_horizon": _money(abs(a - b))}
            summary.append(f"{block['comparison']['better']} comes out {abs(a - b):.2f} ahead over {horizon_n} months.")

    if periods is not None:
        rows: list[dict[str, Any]] = []
        for i, p in enumerate(periods):
            where = f"line.periods[{i}]"
            label = _name(p, f"period {i + 1}")
            u = _positive(p, "units", where, required=True)
            revenue = _field(p, "revenue", where, minimum=0.0)
            realized = _field(p, "realized_price", where, minimum=0.0)
            if revenue is None and realized is None:
                raise ToolError(f"{where}: revenue or realized_price is required")
            realized = revenue / u if realized is None else realized
            list_price = _field(p, "list_price", where, minimum=0.0)
            list_price = price if list_price is None else list_price
            vc = _field(p, "variable_cost", where, minimum=0.0)
            vc = cost if vc is None else vc
            c = realized - vc
            rows.append(
                {
                    "label": label,
                    "units": u,
                    "revenue": _money(realized * u),
                    "list_price": _money(list_price),
                    "realized_price": _money(realized),
                    "discount_depth_pct": _pct_of(list_price - realized, list_price),
                    "variable_cost": _money(vc),
                    "contribution_per_unit": _money(c),
                    "margin_pct": _pct_of(c, realized),
                    "total_contribution": _money(c * u),
                    "_p": realized,
                    "_u": u,
                    "_c": vc,
                }
            )
        decomps = []
        for prev, cur in zip(rows, rows[1:]):
            price_effect = (cur["_p"] - prev["_p"]) * cur["_u"]
            volume_effect = (cur["_u"] - prev["_u"]) * (prev["_p"] - prev["_c"])
            cost_effect = -(cur["_c"] - prev["_c"]) * cur["_u"]
            decomps.append(
                {
                    "from": prev["label"],
                    "to": cur["label"],
                    "price_effect": _money(price_effect),
                    "volume_effect": _money(volume_effect),
                    "cost_effect": _money(cost_effect),
                    "total_change": _money(price_effect + volume_effect + cost_effect),
                }
            )
        for r in rows:
            for k in ("_p", "_u", "_c"):
                r.pop(k)
        pblock: dict[str, Any] = {"rows": rows}
        if decomps:
            pblock["decomposition"] = decomps
            last = decomps[-1]
            driver = max(("price_effect", "volume_effect", "cost_effect"), key=lambda k: abs(last[k]))
            summary.append(
                f"Realized price {rows[-2]['realized_price']:g} → {rows[-1]['realized_price']:g} {currency} (discount depth {rows[-1]['discount_depth_pct']:g} %), contribution per unit"
                f" {rows[-2]['contribution_per_unit']:g} → {rows[-1]['contribution_per_unit']:g}; total contribution {last['total_change']:+.2f}, mostly {driver.replace('_', ' ')} ({last[driver]:+.2f})."
            )
        else:
            pblock["note"] = "One period only: the decomposition needs two."
            summary.append(f"Realized price {rows[0]['realized_price']:g} {currency} against list {rows[0]['list_price']:g} ({rows[0]['discount_depth_pct']:g} % discount depth).")
        block["periods"] = pblock
    if missing:
        block["missing"] = sorted(set(missing))
    block["summary"] = " ".join(summary)
    return block


# --- retail ---------------------------------------------------------------


def _retail(retail: dict, price: float | None, cost: float | None, units: float | None, fixed: float | None, currency: str, warnings: list[str]) -> dict[str, Any]:
    """Channel and shelf arithmetic: bulk breaking, assortment, inventory risk, channel functions, the price waterfall,
    shelf displacement, routes with transit losses, a coupon promotion, delivered prices, payment terms, GMROI,
    a returns reserve and sales incentives. Each sub-block is computed only when its object is present."""
    parts = {
        "bulk": _bulk,
        "assortment": _assortment,
        "channel_risk": _channel_risk,
        "functions": _functions,
        "waterfall": _waterfall,
        "displacement": _displacement,
        "handling": _handling,
        "promotion": _promotion,
        "regions": _regions,
        "payment_terms": _payment_terms,
        "gmroi": _gmroi,
        "returns_reserve": _returns_reserve,
        "incentives": _incentives,
    }
    ctx = {"price": price, "cost": cost, "units": units, "fixed": fixed, "currency": currency, "warnings": warnings}
    block: dict[str, Any] = {}
    summary: list[str] = []
    for key, fn in parts.items():
        sub = _sub(retail, key, "retail")
        if sub is None:
            continue
        block[key] = fn(sub, ctx)
        if block[key].get("summary"):
            summary.append(block[key]["summary"])
    if not block:
        raise ToolError(f"retail needs at least one of: {', '.join(parts)}")
    block["summary"] = " ".join(summary)
    return block


def _need(ctx: dict, *keys: str) -> list[str]:
    names = {"price": "price", "cost": "variable_cost", "units": "units"}
    return [names[k] for k in keys if ctx[k] is None]


def _bulk(b: dict, ctx: dict) -> dict[str, Any]:
    """Breaking bulk: what bridging the supplier lot to the customer lot costs per unit, against a distributor's margin."""
    w = "retail.bulk"
    lot = _positive(b, "supplier_lot_units", w, required=True)
    demand = _positive(b, "monthly_demand_units", w, required=True)
    clot = _positive(b, "customer_lot_units", w, required=True)
    ucost = _field(b, "unit_cost", w, minimum=0.0)
    ucost = ctx["cost"] if ucost is None else ucost
    storage = _field(b, "storage_cost_per_unit_month", w, minimum=0.0)
    handling = _field(b, "handling_cost_per_order", w, minimum=0.0)
    rate = _field(b, "capital_rate_pct_year", w, minimum=0.0)
    dist_margin = _share(b, "distributor_margin_pct", w)
    wholesale = _field(b, "wholesale_price", w, minimum=0.0)
    wholesale = ctx["price"] if wholesale is None else wholesale
    if clot > lot:
        raise ToolError(f"{w}: customer_lot_units {clot:g} is larger than supplier_lot_units {lot:g}; there is no bulk to break")
    months = lot / demand
    held = months / 2
    out: dict[str, Any] = {
        "months_to_sell_lot": round(months, 2),
        "days_to_sell_lot": round(months * 30, 1),
        "average_inventory_units": round(lot / 2, 1),
        "orders_per_month": round(demand / clot, 2),
        "assumptions": ["Stock falls evenly from a full lot to zero, so the average unit is held for half the time the lot takes to sell; a month is 30 days."],
    }
    missing = []
    for key, value in (("unit_cost", ucost), ("storage_cost_per_unit_month", storage), ("handling_cost_per_order", handling)):
        if value is None:
            missing.append(f"{w}.{key}")
    if rate is None:
        rate = 0.0
        out["assumptions"].append("capital_rate_pct_year was not given and is taken as 0: no cost of the money tied up.")
    if ucost is not None:
        out["average_capital_tied"] = _money(lot / 2 * ucost)
    if missing:
        out["missing"] = missing
        out["summary"] = f"A lot takes {months:.1f} months to sell; give {', '.join(m.split('.')[-1] for m in missing)} for the cost of bridging it."
        return out
    storage_u = storage * held
    capital_u = ucost * rate / 100 / 12 * held
    handling_u = handling / clot
    bridge = storage_u + capital_u + handling_u
    out.update(
        {
            "storage_cost_per_unit": _money(storage_u),
            "capital_cost_per_unit": _money(capital_u),
            "carrying_cost_per_unit": _money(storage_u + capital_u),
            "handling_cost_per_unit": _money(handling_u),
            "bridge_cost_per_unit": _money(bridge),
            "bridge_cost_pct_of_unit_cost": _pct_of(bridge, ucost),
        }
    )
    head = f"A lot takes {months:.1f} months to sell, {lot / 2:g} units on average ({lot / 2 * ucost:.2f} {ctx['currency']} tied up); bridging costs {bridge:.2f} per unit ({out['bridge_cost_pct_of_unit_cost']:g} % of unit cost)."
    if dist_margin is not None:
        if wholesale is None:
            out.setdefault("missing", []).append(f"{w}.wholesale_price")
            head += " Give wholesale_price to value the distributor's margin."
        else:
            dist_u = wholesale * dist_margin / 100
            out["distributor_cost_per_unit"] = _money(dist_u)
            out["breakeven_distributor_margin_pct"] = _pct_of(bridge, wholesale)
            ratio = dist_u / bridge if bridge > 0 else None
            out["verdict"] = "indifferent" if ratio is not None and abs(ratio - 1) <= 0.05 else "in_house" if dist_u > bridge else "distributor"
            monthly_fixed = (storage + ucost * rate / 100 / 12) * lot / 2
            if dist_u - handling_u > 0:
                out["distributor_wins_below_monthly_demand"] = round(monthly_fixed / (dist_u - handling_u), 1)
            head += f" The distributor's {dist_margin:g} % is {dist_u:.2f} per unit against {bridge:.2f} in-house (break-even margin {out['breakeven_distributor_margin_pct']:g} %): {out['verdict'].replace('_', ' ')}."
    out["summary"] = head
    return out


def _assortment(a: dict, ctx: dict) -> dict[str, Any]:
    """A low-margin line valued by the baskets it anchors, not by its own margin."""
    w = "retail.assortment"
    rev = _field(a, "line_revenue", w, required=True, minimum=0.0)
    margin = _share(a, "line_margin_pct", w, required=True)
    baskets = _positive(a, "baskets_per_period", w, required=True)
    share = _share(a, "basket_share_pct", w, required=True)
    attached_rev = _field(a, "attached_revenue_per_basket", w, required=True, minimum=0.0)
    attached_margin = _share(a, "attached_margin_pct", w, required=True)
    lost = _share(a, "lost_basket_share_pct", w)
    line_c = rev * margin / 100
    anchored = baskets * share / 100
    attached_c = anchored * attached_rev * attached_margin / 100
    out: dict[str, Any] = {
        "line_contribution": _money(line_c),
        "anchored_baskets": round(anchored, 1),
        "attached_contribution": _money(attached_c),
        "total_contribution_with_line": _money(line_c + attached_c),
        "breakeven_lost_share_pct": _pct_of(line_c, attached_c),
        "assumptions": ["Cutting the line saves nothing beyond its own contribution; the baskets it anchored keep their other margin unless they are lost."],
    }

    def without(s: float) -> float:
        return attached_c * (1 - s / 100)

    if lost is None:
        out["missing"] = [f"{w}.lost_basket_share_pct"]
        out["conditional"] = [{"assumed_lost_basket_share_pct": s, "attached_contribution_lost": _money(attached_c * s / 100), "total_contribution_without_line": _money(without(s)), "cut_pays": line_c > attached_c * s / 100} for s in LOST_BASKET_LEVELS]
        out["decision"] = "depends — see conditional"
        out["summary"] = f"The line earns {line_c:.2f} {ctx['currency']} itself but anchors {anchored:g} baskets carrying {attached_c:.2f} of other margin; the cut only pays if fewer than {out['breakeven_lost_share_pct']:g} % of those baskets are lost."
        return out
    at_risk = attached_c * lost / 100
    out.update(
        {
            "lost_basket_share_pct": lost,
            "attached_contribution_at_risk": _money(at_risk),
            "total_contribution_without_line": _money(without(lost)),
            "net_effect_of_cut": _money(-line_c - at_risk),
        }
    )
    out["decision"] = "keep as assortment service" if at_risk > line_c else "cut"
    out["summary"] = (
        f"The line earns {line_c:.2f} {ctx['currency']} itself but anchors {anchored:g} baskets carrying {attached_c:.2f} of other margin; losing {lost:g} % of them costs {at_risk:.2f}"
        f" ({at_risk / line_c:.1f}× the line's own margin). Break-even: the cut only pays if fewer than {out['breakeven_lost_share_pct']:g} % leave. Decision: {out['decision']}."
        if line_c > 0
        else f"The line earns nothing itself; it anchors {anchored:g} baskets carrying {attached_c:.2f}; losing {lost:g} % costs {at_risk:.2f}. Decision: {out['decision']}."
    )
    return out


def _risk_share(risk: dict, key: str, w: str) -> float:
    raw = risk.get(key, "brand")
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s in {"brand", "us", ""}:
            return 1.0
        if s in {"retailer", "partner", "dealer"}:
            return 0.0
        try:
            raw = float(s.replace("%", ""))
        except ValueError as exc:
            raise ToolError(f"{w}.risk.{key} must be brand, retailer or the brand's share in percent") from exc
    value = float(raw)
    if not 0 <= value <= 100:
        raise ToolError(f"{w}.risk.{key} must be between 0 and 100")
    return value / 100


def _channel_risk(c: dict, ctx: dict) -> dict[str, Any]:
    """Who carries markdowns, unsold stock and damage in a channel, and the retailer-margin equivalence."""
    w = "retail.channel_risk"
    need = _need(ctx, "price", "cost")
    if need:
        return {"missing": need, "summary": f"channel_risk needs {' and '.join(need)} (retail price and the brand's unit cost)."}
    price, cost = ctx["price"], ctx["cost"]
    margin = _share(c, "margin_pct", w)
    wholesale = _field(c, "wholesale_price", w, minimum=0.0)
    if margin is None and wholesale is None:
        raise ToolError(f"{w}: margin_pct (retailer margin on the retail price) or wholesale_price is required")
    if margin is not None and wholesale is not None and abs(price * (1 - margin / 100) - wholesale) > 0.01:
        raise ToolError(f"{w}: margin_pct {margin:g} and wholesale_price {wholesale:g} contradict each other at a retail price of {price:g}")
    wholesale = price * (1 - margin / 100) if wholesale is None else wholesale
    margin = (1 - wholesale / price) * 100 if margin is None else margin
    sell_through = _share(c, "sell_through_pct", w)
    markdown = _share(c, "markdown_pct", w)
    unsold = _share(c, "unsold_pct", w) or 0.0
    damage = _share(c, "damage_pct", w) or 0.0
    salvage = _field(c, "salvage_value_per_unit", w, minimum=0.0) or 0.0
    risk = c.get("risk") or {}
    if not isinstance(risk, dict):
        raise ToolError(f"{w}.risk must be an object with markdowns, unsold and damage")
    s_md, s_un, s_dm = (_risk_share(risk, k, w) for k in ("markdowns", "unsold", "damage"))
    no_risk = wholesale - cost
    out: dict[str, Any] = {
        "wholesale_price": _money(wholesale),
        "retailer_margin_pct": round(margin, 1),
        "brand_contribution_no_risk": _money(no_risk),
        "assumptions": [
            "Markdown money keeps the retailer's margin percentage: the brand's cost of a marked-down unit is markdown_pct of the wholesale price.",
            "A unit taken back is refunded at the wholesale price and worth salvage_value_per_unit (default 0) to the brand.",
            "risk shares default to brand (the take-back the retailer asks for); set retailer or a percent to move them.",
        ],
    }
    unsold_cost = unsold / 100 * (wholesale - salvage)
    damage_cost = damage / 100 * wholesale
    if markdown is None:
        out["missing"] = [f"{w}.markdown_pct"]
        out["summary"] = f"Wholesale {wholesale:.2f} {ctx['currency']} leaves the brand {no_risk:.2f} per unit before risk; give markdown_pct for the cost of take-back."
        return out

    def with_risk(st: float) -> tuple[float, float]:
        md_share = 100 - st - unsold
        if md_share < -1e-9:
            raise ToolError(f"{w}: sell_through_pct {st:g} and unsold_pct {unsold:g} add up to more than 100")
        md_cost = md_share / 100 * markdown / 100 * wholesale
        risk_cost = s_md * md_cost + s_un * unsold_cost + s_dm * damage_cost
        return risk_cost, no_risk - risk_cost

    if sell_through is None:
        out["missing"] = [f"{w}.sell_through_pct"]
        out["conditional"] = [{"assumed_sell_through_pct": st, "risk_cost_per_unit": _money(with_risk(st)[0]), "brand_contribution_with_risk": _money(with_risk(st)[1])} for st in SELL_THROUGH_LEVELS if st + unsold <= 100]
        out["summary"] = f"Wholesale {wholesale:.2f} {ctx['currency']} leaves {no_risk:.2f} per unit before risk; give sell_through_pct for the cost of carrying it."
        return out
    risk_cost, contribution = with_risk(sell_through)
    out.update(
        {
            "sell_through_pct": sell_through,
            "markdown_share_pct": round(100 - sell_through - unsold, 1),
            "unsold_pct": unsold,
            "risk_cost_per_unit": _money(risk_cost),
            "risk_cost_margin_points": round(risk_cost / price * 100, 1),
            "brand_contribution_with_risk": _money(contribution),
            "equivalent_margin_pct_without_risk": _pct((price - cost - contribution) / price),
        }
    )
    md_unit = s_md * markdown / 100 * wholesale
    if md_unit > 0:
        md_share_be = (no_risk - s_un * unsold_cost - s_dm * damage_cost) / md_unit * 100
        st_be = 100 - unsold - md_share_be
        out["breakeven_sell_through_pct"] = round(st_be, 1) if 0 <= st_be <= 100 else None
    else:
        out["breakeven_sell_through_pct"] = None
    out["summary"] = (
        f"At {sell_through:g} % sell-through, carrying the risk costs the brand {risk_cost:.2f} {ctx['currency']} per unit shipped ({out['risk_cost_margin_points']:g} margin points): contribution falls from {no_risk:.2f} to {contribution:.2f}."
        f" A {margin:g} % margin with take-back equals a {out['equivalent_margin_pct_without_risk']:g} % margin where the retailer keeps the risk"
        + (f"; break-even sell-through {out['breakeven_sell_through_pct']:g} %." if out["breakeven_sell_through_pct"] is not None else ".")
    )
    return out


def _functions(f: dict, ctx: dict) -> dict[str, Any]:
    """Going direct: the intermediary's margin kept against the functions the brand must take on, each with an owner and a cost."""
    w = "retail.functions"
    need = _need(ctx, "price", "units")
    if need:
        raise ToolError(f"{w}: cannot compare without price and units ({' and '.join(need)} missing)")
    price, units = ctx["price"], ctx["units"]
    margin = _share(f, "intermediary_margin_pct", w, required=True)
    items = _list(f, "functions", w)
    kept = price * margin / 100
    rows = []
    missing = []
    per_unit_total = per_period_total = 0.0
    matched: set[str] = set()
    for i, it in enumerate(items):
        where = f"{w}.functions[{i}]"
        name = str(it.get("function") or it.get("name") or "").strip()
        if not name:
            raise ToolError(f"{where}: function is required")
        text = name.lower()
        for check in CHANNEL_FUNCTIONS:
            if any(word in text for word in check.replace("/", " ").split()):
                matched.add(check)
        new_owner = str(it.get("new_owner") or "unassigned").strip().lower()
        per_unit = _field(it, "cost_per_unit", where, minimum=0.0)
        per_period = _field(it, "cost_per_period", where, minimum=0.0)
        row = {"function": name, "current_owner": str(it.get("current_owner") or "intermediary"), "new_owner": new_owner, "cost_per_unit": None if per_unit is None else _money(per_unit), "cost_per_period": None if per_period is None else _money(per_period)}
        if new_owner == "unassigned":
            missing.append(f"{name} (unassigned)")
        if per_unit is None and per_period is None:
            if new_owner != "unassigned":
                missing.append(f"{name} (no cost)")
            row["cost_per_unit_direct"] = None
        else:
            direct = (per_unit or 0.0) + (per_period or 0.0) / units
            row["cost_per_unit_direct"] = _money(direct)
            per_unit_total += per_unit or 0.0
            per_period_total += per_period or 0.0
        rows.append(row)
    direct_u = per_unit_total + per_period_total / units
    gain_u = kept - direct_u
    out: dict[str, Any] = {
        "functions_table": rows,
        "margin_kept_per_unit": _money(kept),
        "direct_cost_per_unit": _money(direct_u),
        "net_gain_per_unit": _money(gain_u),
        "net_gain_per_period": _money(gain_u * units),
        "break_even_units": _ceil(per_period_total / (kept - per_unit_total)) if kept - per_unit_total > 0 and per_period_total > 0 else (0 if per_period_total == 0 else None),
        "checklist_gaps": [c for c in CHANNEL_FUNCTIONS if c not in matched],
    }
    if missing:
        out["missing"] = missing
    open_items = out["checklist_gaps"] + [m for m in missing]
    out["summary"] = f"Direct sales keep {gain_u:.2f} {ctx['currency']} per unit ({gain_u * units:.2f} per period) after {direct_u:.2f} of costed functions" + (
        f", before {len(open_items)} function(s) not yet owned or costed: {', '.join(open_items)}. Assign them before deciding." if open_items else "; every checklist function is owned and costed."
    )
    return out


def _waterfall(wf: dict, ctx: dict) -> dict[str, Any]:
    """From shelf price to net-to-brand through the channel layers, and what is left for demand creation."""
    w = "retail.waterfall"
    need = _need(ctx, "price", "cost")
    if need:
        return {"missing": need, "summary": f"waterfall needs {' and '.join(need)} (shelf price and unit cost)."}
    price, cost, units, fixed = ctx["price"], ctx["cost"], ctx["units"], ctx["fixed"]
    layers = _list(wf, "layers", w)
    listing = _field(wf, "listing_fees", w, minimum=0.0) or 0.0
    promo_free = _field(wf, "promo_units_free_pct", w, minimum=0.0) or 0.0
    promo_weeks = _field(wf, "promo_weeks", w, minimum=0.0) or 0.0
    period_weeks = _positive(wf, "period_weeks", w) or 4.0
    if promo_weeks > period_weeks:
        raise ToolError(f"{w}: promo_weeks {promo_weeks:g} is longer than period_weeks {period_weeks:g}")
    rows = []
    missing = []
    assumptions = ["Layers are listed from the shelf downwards; each takes its margin off the price it sells at (selling_price) or on top of its buying price (cost_plus).", f"period_weeks {period_weeks:g}: the promo cost is averaged over the period."]
    after = price
    for i, layer in enumerate(layers):
        where = f"{w}.layers[{i}]"
        name = _name(layer, f"layer {i + 1}")
        m = _share(layer, "margin_pct", where, required=True)
        base = str(layer.get("margin_base") or "").strip().lower()
        if not base:
            missing.append(f"{where}.margin_base")
            assumptions.append(f"{name}: margin_base was not given and is taken as selling_price.")
            base = "selling_price"
        if base not in {"selling_price", "cost_plus"}:
            raise ToolError(f"{where}: margin_base must be selling_price or cost_plus")
        if base == "selling_price":
            if m >= 100:
                raise ToolError(f"{where}: margin_pct of selling_price must be below 100")
            takes = after * m / 100
        else:
            takes = after - after / (1 + m / 100)
        rows.append({"name": name, "sells_at": _money(after), "margin_pct": m, "margin_base": base, "takes_per_unit": _money(takes), "price_after": _money(after - takes)})
        after -= takes
    net = after
    contribution = net - cost
    promo_u = cost * promo_free / 100 * (promo_weeks / period_weeks)
    demand = max(0.0, contribution - promo_u)
    flags = []
    if contribution <= 0:
        flags.append("channel_eats_margin")
    if promo_u > contribution > 0:
        flags.append("promo_exceeds_contribution")
    out: dict[str, Any] = {
        "layers": rows,
        "shelf_price": _money(price),
        "net_to_brand": _money(net),
        "channel_take_pct": _pct((price - net) / price),
        "contribution_after_channel": _money(contribution),
        "promo_cost_per_unit": _money(promo_u),
        "demand_budget_per_unit": _money(demand),
        "listing_fees": _money(listing),
        "assumptions": assumptions,
    }
    fixed_total = (fixed or 0.0) + listing
    out["break_even_units"] = _ceil(fixed_total / contribution) if contribution > 0 and fixed_total > 0 else (0 if fixed_total == 0 else None)
    if promo_u > 0:
        out["break_even_units_during_promo"] = _ceil(fixed_total / demand) if demand > 0 and fixed_total > 0 else (0 if fixed_total == 0 else None)
    if units is not None:
        out["demand_budget_per_period"] = _money(demand * units)
    else:
        missing.append("units")
    if flags:
        out["flags"] = flags
    if missing:
        out["missing"] = missing
    head = f"Of the {price:g} {ctx['currency']} shelf price {net:.2f} reaches the brand ({out['channel_take_pct']:g} % to the channel); contribution {contribution:.2f} per unit"
    head += f", {demand:.2f} left for demand creation after {promo_u:.2f} of promo cost" if promo_u > 0 else ""
    head += f" ({demand * units:.2f} per period)" if units is not None else ""
    head += f"; break-even {out['break_even_units']} units on {fixed_total:.2f} of fixed and listing costs." if out["break_even_units"] else "."
    if "channel_eats_margin" in flags:
        head += " The channel takes more than the margin: nothing is left for demand creation."
    out["summary"] = head
    return out


def _displacement(d: dict, ctx: dict) -> dict[str, Any]:
    """The retailer's gross profit per facing-week each existing item earns, and the hurdle a new SKU must clear."""
    w = "retail.displacement"
    candidates = _list(d, "candidates", w)
    new = _sub(d, "new_sku", w)
    rows = []
    missing = []
    for i, c in enumerate(candidates):
        where = f"{w}.candidates[{i}]"
        name = str(c.get("sku") or c.get("name") or f"item {i + 1}").strip()
        weekly = _field(c, "weekly_units", where, minimum=0.0)
        facings = _positive(c, "facings", where)
        rprice = _field(c, "retail_price", where, required=True, minimum=0.0)
        m = _share(c, "retailer_margin_pct", where, required=True)
        if weekly is None or facings is None:
            missing.append(f"{where}.{'weekly_units' if weekly is None else 'facings'}")
            rows.append({"sku": name, "retailer_gross_profit_per_facing_week": None})
            continue
        rows.append({"sku": name, "weekly_units": weekly, "facings": facings, "units_per_facing_week": round(weekly / facings, 1), "retailer_gross_profit_per_facing_week": _money(weekly / facings * rprice * m / 100)})
    usable = [r for r in rows if r["retailer_gross_profit_per_facing_week"] is not None]
    out: dict[str, Any] = {"candidates": rows, "assumptions": ["The buyer drops the item earning the least gross profit per facing-week; the new SKU must beat that figure on each facing it takes."]}
    if missing:
        out["missing"] = missing
    if not usable:
        out["hurdle"] = None
        out["verdict"] = "unknown"
        out["summary"] = "No candidate has weekly_units and facings: ask the buyer which SKU the new item would displace."
        return out
    weakest = min(usable, key=lambda r: r["retailer_gross_profit_per_facing_week"])
    hurdle = weakest["retailer_gross_profit_per_facing_week"]
    out.update({"likely_displaced": weakest["sku"], "hurdle": hurdle})
    head = f"{weakest['sku']} earns the retailer the least, {hurdle:.2f} {ctx['currency']} per facing-week, so it is the likely displacement candidate."
    if new is not None:
        nprice = _field(new, "retail_price", f"{w}.new_sku", required=True, minimum=0.0)
        nm = _share(new, "retailer_margin_pct", f"{w}.new_sku", required=True)
        per_unit = nprice * nm / 100
        required = hurdle / per_unit if per_unit > 0 else None
        expected = _field(new, "expected_weekly_units", f"{w}.new_sku", minimum=0.0)
        nfacings = _positive(new, "facings", f"{w}.new_sku") or 1.0
        out["new_sku"] = {"retailer_gross_profit_per_unit": _money(per_unit), "required_weekly_units_per_facing": None if required is None else round(required, 1), "facings": nfacings}
        if required is not None:
            head += f" The new SKU must sell at least {required:.1f} units per facing per week to justify a facing"
            if expected is None:
                out["verdict"] = "unknown"
                out.setdefault("missing", []).append(f"{w}.new_sku.expected_weekly_units")
                head += "; give expected_weekly_units for a verdict."
            else:
                per_facing = expected / nfacings
                out["new_sku"]["expected_units_per_facing_week"] = round(per_facing, 1)
                out["verdict"] = "ok" if per_facing >= required - 1e-9 else "below_hurdle"
                head += f"; at {per_facing:.1f} expected it is {out['verdict'].replace('_', ' ')}."
    else:
        out["verdict"] = "unknown"
    out["summary"] = head
    return out


def _handling(h: dict, ctx: dict) -> dict[str, Any]:
    """Routes to market with transit losses: margin per unit shipped, not per unit sold."""
    w = "retail.handling"
    routes = _list(h, "routes", w)
    rows = []
    for i, r in enumerate(routes):
        where = f"{w}.routes[{i}]"
        name = _name(r, f"route {i + 1}")
        rprice = _field(r, "price", where, required=True, minimum=0.0)
        rcost = _field(r, "cost", where, minimum=0.0)
        rcost = ctx["cost"] if rcost is None else rcost
        handling = _field(r, "handling", where, minimum=0.0) or 0.0
        days = _field(r, "transit_days", where, minimum=0.0) or 0.0
        daily = _share(r, "loss_pct_per_day", where)
        fixed_loss = _share(r, "fixed_loss_pct", where)
        shelf = _field(r, "shelf_life_days", where, minimum=0.0)
        row: dict[str, Any] = {"name": name, "price": _money(rprice), "handling": _money(handling), "transit_days": days}
        needs = []
        if rcost is None:
            needs.append("cost")
        if daily is None and fixed_loss is None and shelf is None:
            needs.append("loss_pct_per_day or fixed_loss_pct")
        if needs:
            row.update({"needs": needs, "usable_fraction": None, "margin_per_unit_shipped": None})
            rows.append(row)
            continue
        if shelf is not None and shelf < days:
            row.update({"usable_fraction": 0.0, "margin_per_unit_shipped": _money(-rcost - handling), "note": "unusable route: the shelf life is shorter than the transit"})
            rows.append(row)
            continue
        usable = (1 - (fixed_loss or 0.0) / 100) * (1 - (daily or 0.0) / 100) ** days
        row.update(
            {
                "cost": _money(rcost),
                "loss_model": {"fixed_loss_pct": fixed_loss, "loss_pct_per_day": daily, "shelf_life_days": shelf},
                "usable_fraction": round(usable, 4),
                "revenue_per_unit_shipped": _money(rprice * usable),
                "margin_per_unit_shipped": _money(rprice * usable - rcost - handling),
                "margin_per_unit_sold": _money(rprice - (rcost + handling) / usable) if usable > 0 else None,
                "_daily": daily,
                "_fixed": fixed_loss or 0.0,
                "_cost": rcost,
                "_handling": handling,
                "_days": days,
            }
        )
        rows.append(row)
    complete = [r for r in rows if r.get("margin_per_unit_shipped") is not None and "_cost" in r]
    complete.sort(key=lambda r: r["margin_per_unit_shipped"], reverse=True)
    for r in complete:
        others = [o["margin_per_unit_shipped"] for o in complete if o is not r]
        if others:
            target = max(others)
            needed_usable = (target + r["_cost"] + r["_handling"]) / r["price"] if r["price"] > 0 else None
            if needed_usable is None or needed_usable <= 0 or needed_usable > 1:
                r["breakeven_loss_pct"] = None
            elif r["_daily"] is not None and r["_days"] > 0:
                base = needed_usable / (1 - r["_fixed"] / 100)
                r["breakeven_loss_pct"] = _pct(1 - base ** (1 / r["_days"])) if 0 < base <= 1 else None
                r["breakeven_loss_basis"] = "per day"
            else:
                r["breakeven_loss_pct"] = _pct(1 - needed_usable)
                r["breakeven_loss_basis"] = "fixed"
    for r in rows:
        for k in ("_daily", "_fixed", "_cost", "_handling", "_days"):
            r.pop(k, None)
    out: dict[str, Any] = {"routes": rows, "ranking": [r["name"] for r in complete], "assumptions": ["usable_fraction = (1 - fixed_loss_pct) × (1 - loss_pct_per_day)^transit_days; margin per unit shipped is what a harvested or produced unit leaves."]}
    needs = [f"{r['name']}: {', '.join(r['needs'])}" for r in rows if r.get("needs")]
    if needs:
        out["missing"] = needs
    if complete:
        best = complete[0]
        head = f"{best['name']} leaves {best['margin_per_unit_shipped']:.2f} {ctx['currency']} per unit shipped ({best['usable_fraction'] * 100:.1f} % usable)"
        if len(complete) > 1:
            second = complete[1]
            head += f" against {second['margin_per_unit_shipped']:.2f} for {second['name']}"
            if second.get("breakeven_loss_pct") is not None:
                head += f", which overtakes if its loss falls below {second['breakeven_loss_pct']:g} % {second['breakeven_loss_basis']}"
        out["summary"] = head + "."
    else:
        out["summary"] = "No route has both a cost and a loss model; see missing."
    return out


def _promotion(p: dict, ctx: dict) -> dict[str, Any]:
    """A coupon or redemption promotion: expected redemptions, who pays what, stock cover and shortfall."""
    w = "retail.promotion"
    reach = _positive(p, "reach", w, required=True)
    rate = _share(p, "redemption_rate_pct", w)
    face = _field(p, "face_value", w, required=True, minimum=0.0)
    fee = _field(p, "handling_fee", w, minimum=0.0) or 0.0
    stock = _field(p, "stock_on_hand", w, minimum=0.0)
    days = _positive(p, "period_days", w)
    rmargin = _share(p, "retailer_margin_pct", w)
    out: dict[str, Any] = {"reach": reach, "face_value": _money(face), "handling_fee": _money(fee), "who_pays": f"the brand reimburses {face:.2f} face value + {fee:.2f} handling per redeemed coupon; the retailer carries nothing", "assumptions": ["Redeemed units are incremental sales for the retailer and the brand."]}
    missing = []

    def figures(r: float) -> dict[str, Any]:
        red = reach * r / 100
        row: dict[str, Any] = {"expected_redemptions": round(red), "brand_cost": _money(red * (face + fee))}
        if stock is not None:
            row["shortfall_units"] = max(0, _ceil(red - stock))
            row["stock_cover_days"] = None if days is None or red <= 0 else round(stock / (red / days), 1)
        if ctx["price"] is not None and rmargin is not None:
            row["retailer_margin_on_redeemed_units"] = _money(red * ctx["price"] * rmargin / 100)
        if ctx["price"] is not None and ctx["cost"] is not None:
            row["brand_contribution_on_redeemed_units"] = _money(red * (ctx["price"] - ctx["cost"]) - red * (face + fee))
        return row

    if stock is None:
        missing.append(f"{w}.stock_on_hand")
    if rate is None:
        missing.append(f"{w}.redemption_rate_pct")
        out["conditional"] = [{"assumed_redemption_rate_pct": r} | figures(r) for r in REDEMPTION_LEVELS]
        out["summary"] = f"Give redemption_rate_pct; at 1/3/5 % of {reach:g} people the brand pays {', '.join(f'{c['brand_cost']:.2f}' for c in out['conditional'])} {ctx['currency']}."
    else:
        out.update(figures(rate))
        out["redemption_rate_pct"] = rate
        head = f"Expected redemptions {out['expected_redemptions']} ({rate:g} % of {reach:g}); the brand pays {out['brand_cost']:.2f} {ctx['currency']} ({face:.2f} + {fee:.2f} handling each)."
        if stock is None:
            head += " Stock on hand unknown: the shortfall cannot be judged."
        else:
            head += f" Stock on hand {stock:g}: shortfall {out['shortfall_units']} units" + (f", cover {out['stock_cover_days']:g} days of the {days:g}-day period." if out.get("stock_cover_days") is not None else ".")
        out["summary"] = head
    if missing:
        out["missing"] = missing
    return out


def _regions(rg: dict, ctx: dict) -> dict[str, Any]:
    """Delivered, not ex-works: landed price per region against the local competing price."""
    w = "retail.regions"
    if ctx["price"] is None:
        return {"missing": ["price"], "summary": "regions needs price (the ex-works price per unit)."}
    price, cost = ctx["price"], ctx["cost"]
    regions = _list(rg, "regions", w)
    rows = []
    missing = []
    for i, r in enumerate(regions):
        where = f"{w}.regions[{i}]"
        name = _name(r, f"region {i + 1}")
        freight_u = _field(r, "freight_per_unit", where, minimum=0.0)
        if freight_u is None:
            per_ship = _field(r, "freight_per_shipment", where, minimum=0.0)
            per_units = _positive(r, "units_per_shipment", where)
            if per_ship is None or per_units is None:
                missing.append(f"{where}.freight_per_unit (or freight_per_shipment + units_per_shipment)")
                rows.append({"name": name, "landed_price": None})
                continue
            freight_u = per_ship / per_units
        duty = _share(r, "duty_pct", where) or 0.0
        extra = _field(r, "extra_cost_per_unit", where, minimum=0.0) or 0.0
        competitor = _field(r, "competitor_price", where, minimum=0.0)
        duty_amount = (price + freight_u + extra) * duty / 100
        landed_price = price + freight_u + extra + duty_amount
        row: dict[str, Any] = {"name": name, "freight_per_unit": _money(freight_u), "duty_per_unit": _money(duty_amount), "extra_cost_per_unit": _money(extra), "landed_price": _money(landed_price)}
        if cost is not None:
            landed_cost = cost + freight_u + extra + duty_amount
            row["landed_cost_per_unit"] = _money(landed_cost)
            row["margin_pct_if_seller_absorbs"] = _pct_of(price - landed_cost, price)
        if competitor is None:
            row["competitive"] = "unknown"
        else:
            row["competitor_price"] = _money(competitor)
            row["price_gap_pct"] = _pct_of(landed_price - competitor, competitor)
            row["competitive"] = landed_price <= competitor + 1e-9
        rows.append(row)
    ranked = sorted([r for r in rows if r.get("price_gap_pct") is not None], key=lambda r: r["price_gap_pct"])
    out: dict[str, Any] = {"regions": rows, "ranking": [r["name"] for r in ranked], "assumptions": ["Duty is charged on price + freight + extras; landed_price is what the buyer pays delivered when the seller passes freight on."]}
    if missing:
        out["missing"] = missing
    if ranked:
        competitive = [r["name"] for r in ranked if r["competitive"] is True]
        best = ranked[0]
        out["summary"] = (
            f"Delivered, {', '.join(competitive)} beat the local price." if competitive else f"Delivered, none of the {len(ranked)} regions beats the local price; {best['name']} is closest ({best['price_gap_pct']:+g} %, {best['landed_price']:.2f} {ctx['currency']} against {best['competitor_price']:.2f})."
        )
    else:
        out["summary"] = f"Landed prices computed for {sum(1 for r in rows if r['landed_price'] is not None)} region(s); give competitor_price to rank them."
    return out


def _payment_terms(pt: dict, ctx: dict) -> dict[str, Any]:
    """Who finances the gap between production and payment: financing cost per unit and the adjusted contribution per offer."""
    w = "retail.payment_terms"
    if ctx["cost"] is None:
        return {"missing": ["variable_cost"], "summary": "payment_terms needs variable_cost (what each unit costs before it is paid for)."}
    cost = ctx["cost"]
    offers = _list(pt, "offers", w)
    shared_rate = _field(pt, "annual_finance_rate_pct", w, minimum=0.0)
    rows = []
    missing = []
    for i, o in enumerate(offers):
        where = f"{w}.offers[{i}]"
        name = _name(o, f"offer {i + 1}")
        oprice = _field(o, "price", where, required=True, minimum=0.0)
        pay_days = _field(o, "days_to_payment", where, minimum=0.0)
        transit = _field(o, "days_in_transit", where, minimum=0.0) or 0.0
        order_units = _field(o, "order_units", where, minimum=0.0)
        freight = _field(o, "freight_per_unit", where, minimum=0.0) or 0.0
        rate = _field(o, "annual_finance_rate_pct", where, minimum=0.0)
        rate = shared_rate if rate is None else rate
        row: dict[str, Any] = {"name": name, "price": _money(oprice), "freight_per_unit": _money(freight), "contribution_before_financing": _money(oprice - cost - freight)}
        if pay_days is None:
            missing.append(f"{where}.days_to_payment")
            row["financing_cost_per_unit"] = None
            rows.append(row)
            continue
        days = pay_days + transit
        row["days_financed"] = days
        if order_units is not None:
            row["capital_tied_up"] = _money(cost * order_units)

        def fin(r: float) -> float:
            return cost * r / 100 * days / 365

        if rate is None:
            missing.append(f"{where}.annual_finance_rate_pct")
            row["conditional"] = [{"assumed_annual_finance_rate_pct": r, "financing_cost_per_unit": _money(fin(r)), "adjusted_contribution_per_unit": _money(oprice - cost - freight - fin(r))} for r in FINANCE_RATE_LEVELS]
            rows.append(row)
            continue
        adj = oprice - cost - freight - fin(rate)
        row.update({"annual_finance_rate_pct": rate, "financing_cost_per_unit": _money(fin(rate)), "adjusted_contribution_per_unit": _money(adj), "adjusted_margin_pct": _pct_of(adj, oprice), "_days": days})
        if order_units is not None:
            row["adjusted_contribution_total"] = _money(adj * order_units)
            row["financing_cost_total"] = _money(fin(rate) * order_units)
        rows.append(row)
    complete = [r for r in rows if "adjusted_contribution_per_unit" in r]
    key = "adjusted_contribution_total" if all("adjusted_contribution_total" in r for r in complete) else "adjusted_contribution_per_unit"
    complete.sort(key=lambda r: r[key], reverse=True)
    out: dict[str, Any] = {"offers": rows, "assumptions": ["financing_cost_per_unit = variable_cost × rate × (days_to_payment + days_in_transit) / 365: the seller finances the goods from dispatch until the money arrives."]}
    if missing:
        out["missing"] = missing
    if complete:
        best = complete[0]
        out["best_offer"] = best["name"]
        head = f"{best['name']} leaves {best['adjusted_contribution_per_unit']:.2f} {ctx['currency']} per unit after {best['financing_cost_per_unit']:.2f} of financing over {best['_days']:g} days"
        if len(complete) > 1:
            second = complete[1]
            head += f" against {second['adjusted_contribution_per_unit']:.2f} for {second['name']}"
            d_days = best["_days"] - second["_days"]
            gross_gap = (best["price"] - best["freight_per_unit"]) - (second["price"] - second["freight_per_unit"])
            if d_days > 0 and gross_gap > 0:
                tie = gross_gap / (cost * d_days / 365) * 100
                out["break_even_rate_pct"] = round(tie, 1)
                head += f"; they tie at a borrowing rate of {tie:.1f} % a year"
        out["summary"] = head + "."
    else:
        out["summary"] = "No offer could be adjusted for financing; see missing."
    for r in rows:
        r.pop("_days", None)
    return out


def _gmroi(g: dict, ctx: dict) -> dict[str, Any]:
    """Turnover × margin: gross margin return on the inventory investment per line."""
    w = "retail.gmroi"
    items = _list(g, "items", w)
    target = _field(g, "target_gmroi", w, minimum=0.0)
    rows = []
    for i, it in enumerate(items):
        where = f"{w}.items[{i}]"
        name = _name(it, f"line {i + 1}")
        inventory = _positive(it, "average_inventory_at_cost", where, required=True)
        revenue = _field(it, "revenue", where, required=True, minimum=0.0)
        cogs = _field(it, "cogs", where, minimum=0.0)
        margin = _share(it, "gross_margin_pct", where)
        if cogs is None and margin is None:
            raise ToolError(f"{where}: cogs or gross_margin_pct is required")
        if cogs is not None and margin is not None and abs(revenue * (1 - margin / 100) - cogs) > 0.01:
            raise ToolError(f"{where}: cogs {cogs:g} and gross_margin_pct {margin:g} contradict each other on revenue {revenue:g}")
        cogs = revenue * (1 - margin / 100) if cogs is None else cogs
        gross = revenue - cogs
        rows.append({"name": name, "revenue": _money(revenue), "gross_margin": _money(gross), "gross_margin_pct": _pct_of(gross, revenue), "inventory_turns": round(cogs / inventory, 2), "gmroi": round(gross / inventory, 2), "sales_to_stock": round(revenue / inventory, 2)})
    rows.sort(key=lambda r: r["gmroi"], reverse=True)
    out: dict[str, Any] = {"items": rows, "ranking": [r["name"] for r in rows], "assumptions": ["GMROI = gross margin / average inventory at cost = turns × margin on cost; a line below 1 returns less than the stock tied up in it."]}
    if target is not None:
        out["below_target"] = [r["name"] for r in rows if r["gmroi"] < target]
    best, worst = rows[0], rows[-1]
    out["summary"] = f"{best['name']} returns {best['gmroi']:g} {ctx['currency']} of gross margin per {ctx['currency']} of stock ({best['inventory_turns']:g} turns × {best['gross_margin_pct']:g} % margin)" + (
        f"; {worst['name']} returns {worst['gmroi']:g} ({worst['inventory_turns']:g} turns × {worst['gross_margin_pct']:g} %)." if len(rows) > 1 else "."
    )
    return out


def _returns_reserve(rr: dict, ctx: dict) -> dict[str, Any]:
    """What returns cost per unit sold and the reserve to hold against them."""
    w = "retail.returns_reserve"
    need = _need(ctx, "price", "cost")
    if need:
        return {"missing": need, "summary": f"returns_reserve needs {' and '.join(need)}."}
    price, cost = ctx["price"], ctx["cost"]
    rate = _share(rr, "return_rate_pct", w, required=True)
    handling = _field(rr, "handling_cost_per_return", w, minimum=0.0) or 0.0
    recovery = _share(rr, "resale_recovery_pct", w)
    units = _field(rr, "units", w, minimum=0.0)
    units = ctx["units"] if units is None else units
    out: dict[str, Any] = {"return_rate_pct": rate, "assumptions": ["A return reverses the sale (its contribution is lost), writes off the share of the unit cost not recovered on resale, and costs the handling."]}
    if recovery is None:
        recovery = 0.0
        out["missing"] = [f"{w}.resale_recovery_pct"]
        out["assumptions"].append("resale_recovery_pct was not given and is taken as 0: returned units are written off in full.")
    loss = (price - cost) + cost * (1 - recovery / 100) + handling
    reserve = rate / 100 * loss
    out.update({"loss_per_return": _money(loss), "reserve_per_unit_sold": _money(reserve), "reserve_pct_of_revenue": _pct_of(reserve, price), "contribution_after_returns": _money(price - cost - reserve), "margin_pct_after_returns": _pct_of(price - cost - reserve, price)})
    if units is not None:
        out["reserve_per_period"] = _money(reserve * units)
        out["returns_per_period"] = round(units * rate / 100, 1)
    out["summary"] = f"Each return costs {loss:.2f} {ctx['currency']}; at {rate:g} % returns hold {reserve:.2f} per unit sold ({out['reserve_pct_of_revenue']:g} % of revenue), leaving {price - cost - reserve:.2f} of contribution" + (f" ({reserve * units:.2f} reserve per period)." if units is not None else ".")
    return out


def _incentives(inc: dict, ctx: dict) -> dict[str, Any]:
    """Commission on revenue against commission on gross profit: which item the rep pushes under each, and which the company wants pushed."""
    w = "retail.incentives"
    items = _list(inc, "items", w)
    on_rev = _share(inc, "revenue_commission_pct", w)
    on_gp = _share(inc, "gross_profit_commission_pct", w)
    if on_rev is None and on_gp is None:
        raise ToolError(f"{w}: revenue_commission_pct or gross_profit_commission_pct is required")
    rows = []
    for i, it in enumerate(items):
        where = f"{w}.items[{i}]"
        name = _name(it, f"item {i + 1}")
        p = _field(it, "price", where, required=True, minimum=0.0)
        c = _field(it, "variable_cost", where, required=True, minimum=0.0)
        gp = p - c
        row: dict[str, Any] = {"name": name, "price": _money(p), "gross_profit": _money(gp), "margin_pct": _pct_of(gp, p)}
        if on_rev is not None:
            row["rep_pay_on_revenue"] = _money(p * on_rev / 100)
            row["company_keeps_on_revenue_scheme"] = _money(gp - p * on_rev / 100)
        if on_gp is not None:
            row["rep_pay_on_gross_profit"] = _money(max(gp, 0.0) * on_gp / 100)
            row["company_keeps_on_gross_profit_scheme"] = _money(gp - max(gp, 0.0) * on_gp / 100)
        rows.append(row)
    company = max(rows, key=lambda r: r["gross_profit"])["name"]
    out: dict[str, Any] = {"items": rows, "company_prefers": company, "assumptions": ["The rep pushes the item that pays them most per sale; the company wants the item with the most gross profit per sale sold."]}
    if on_rev is not None:
        out["rep_pushes_on_revenue_scheme"] = max(rows, key=lambda r: r["rep_pay_on_revenue"])["name"]
        out["revenue_scheme_aligned"] = out["rep_pushes_on_revenue_scheme"] == company
    if on_gp is not None:
        out["rep_pushes_on_gross_profit_scheme"] = max(rows, key=lambda r: r["rep_pay_on_gross_profit"])["name"]
        out["gross_profit_scheme_aligned"] = out["rep_pushes_on_gross_profit_scheme"] == company
    parts = [f"The company earns most on {company}."]
    if on_rev is not None:
        parts.append(f"Paid {on_rev:g} % of revenue the rep pushes {out['rep_pushes_on_revenue_scheme']} ({'aligned' if out['revenue_scheme_aligned'] else 'not aligned'}).")
    if on_gp is not None:
        parts.append(f"Paid {on_gp:g} % of gross profit the rep pushes {out['rep_pushes_on_gross_profit_scheme']} ({'aligned' if out['gross_profit_scheme_aligned'] else 'not aligned'}).")
    out["summary"] = " ".join(parts)
    return out


# --- media value and routes ---------------------------------------------------


def _wilson(k: float, n: float, z: float = 1.96) -> tuple[float, float]:
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _media_value(mv: dict, price: float | None, cost: float | None, currency: str, warnings: list[str]) -> dict[str, Any]:
    """Media money against what it brings back: a paid keyword test, incremental creative space, a catalogue or mailing,
    spend levels compared on profit, a public-interest outcome, a partnership's cash / in-kind / outcomes, and a return
    benchmark checked for context."""
    w = "media_value"
    margin_in = _share(mv, "margin_pct", w)
    margin = None
    if margin_in is not None:
        margin = margin_in / 100
    elif price is not None and cost is not None and price > 0:
        margin = (price - cost) / price
    contribution = None if price is None or cost is None else price - cost
    ctx = {"price": price, "cost": cost, "margin": margin, "contribution": contribution, "currency": currency, "warnings": warnings}
    parts = {
        "keyword_test": _keyword_test,
        "incremental": _incremental,
        "campaign": _campaign,
        "spend_options": _spend_options,
        "outcome": _outcome,
        "partnership": _partnership,
        "benchmark": _benchmark,
    }
    block: dict[str, Any] = {}
    summary: list[str] = []
    for key, fn in parts.items():
        sub = _sub(mv, key, w)
        if sub is None:
            continue
        block[key] = fn(sub, ctx)
        if block[key].get("summary"):
            summary.append(block[key]["summary"])
    if not block:
        raise ToolError(f"media_value needs at least one of: {', '.join(parts)}")
    if margin is not None:
        block["margin_pct"] = _pct(margin)
    block["summary"] = " ".join(summary)
    return block


def _keyword_test(k: dict, ctx: dict) -> dict[str, Any]:
    """A paid click sample before an organic project: clicks the budget buys, break-even conversion, the read and the SEO payback."""
    w = "media_value.keyword_test"
    budget = _positive(k, "budget", w, required=True)
    cpc = _positive(k, "cpc", w, required=True)
    expected = _share(k, "expected_cvr_pct", w)
    clicks_obs = _field(k, "observed_clicks", w, minimum=0.0)
    orders_obs = _field(k, "observed_orders", w, minimum=0.0)
    seo_cost = _field(k, "seo_cost", w, minimum=0.0)
    seo_months = _positive(k, "seo_months", w)
    c = ctx["contribution"]
    out: dict[str, Any] = {"budget": _money(budget), "cpc": _money(cpc), "clicks_affordable": int(budget // cpc), "assumptions": ["Wilson 95 % interval on the conversion rate; a read below 5 orders is reported but not trusted."]}
    if c is None or c <= 0:
        out["missing"] = ["variable_cost" if ctx["price"] is not None else "price"]
        out["summary"] = f"The budget buys {out['clicks_affordable']} clicks at {cpc:g}; give price and variable_cost (contribution per order) for the break-even conversion."
        return out
    be = cpc / c
    out["contribution_per_order"] = _money(c)
    out["break_even_cvr_pct"] = round(be * 100, 2)
    p_for_sample = (orders_obs / clicks_obs) if clicks_obs and orders_obs is not None else (expected / 100 if expected is not None else be)
    out["clicks_for_1pp_read"] = _ceil(1.96**2 * p_for_sample * (1 - p_for_sample) / 0.01**2)
    head = f"{budget:g} {ctx['currency']} buys {out['clicks_affordable']} clicks at {cpc:g}; with {c:.2f} per order the test breaks even at {be * 100:.2f} % conversion."
    if expected is not None:
        out["design"] = {"expected_cvr_pct": expected, "orders_expected": round(out["clicks_affordable"] * expected / 100, 1), "contribution_per_visit_expected": _money(expected / 100 * c)}
    if clicks_obs is not None and orders_obs is not None:
        if clicks_obs <= 0:
            raise ToolError(f"{w}: observed_clicks must be greater than zero when a read is given")
        if orders_obs > clicks_obs:
            raise ToolError(f"{w}: observed_orders cannot exceed observed_clicks")
        cvr = orders_obs / clicks_obs
        lo, hi = _wilson(orders_obs, clicks_obs)
        spend = clicks_obs * cpc
        read: dict[str, Any] = {
            "clicks": clicks_obs,
            "orders": orders_obs,
            "cvr_pct": round(cvr * 100, 2),
            "cvr_ci95_pct": [round(lo * 100, 2), round(hi * 100, 2)],
            "contribution_per_visit": _money(cvr * c),
            "contribution_per_visit_ci95": [_money(lo * c), _money(hi * c)],
            "spend": _money(spend),
            "paid_roi_pct": _pct_of(orders_obs * c - spend, spend),
            "interval_includes_break_even": lo <= be <= hi,
        }
        out["read"] = read
        head += f" Read: {orders_obs:g} orders from {clicks_obs:g} clicks = {cvr * 100:.2f} % (95 % interval {lo * 100:.2f}–{hi * 100:.2f} %), {cvr * c:.2f} per visit, paid ROI {read['paid_roi_pct']:g} %."
        if seo_cost is not None and seo_months is not None:
            per_month = seo_cost / seo_months
            visits_needed = per_month / (cvr * c) if cvr > 0 else None
            if orders_obs < 5:
                verdict = "insufficient_sample"
            elif cvr > be:
                verdict = "worth_testing_organic"
            else:
                verdict = "not_yet"
            out["seo"] = {"seo_cost": _money(seo_cost), "seo_months": seo_months, "visits_needed_per_month_to_repay": None if visits_needed is None else _ceil(visits_needed), "verdict": verdict}
            head += f" SEO at {seo_cost:g} over {seo_months:g} months repays from {_ceil(visits_needed) if visits_needed else 'n/a'} organic visits a month: {verdict.replace('_', ' ')}, preliminary evidence."
        elif orders_obs < 5:
            out["verdict"] = "insufficient_sample"
            head += f" Fewer than 5 orders: not a read yet; about {out['clicks_for_1pp_read']} clicks give a ±1 pp read."
    elif (clicks_obs is None) != (orders_obs is None):
        raise ToolError(f"{w}: observed_clicks and observed_orders go together")
    out["summary"] = head
    return out


def _incremental(inc: dict, ctx: dict) -> dict[str, Any]:
    """A bigger placement, colour or an illustration: the extra orders the extra cost needs and the lift they imply."""
    w = "media_value.incremental"
    extra_cost = _positive(inc, "incremental_cost", w, required=True)
    baseline = _field(inc, "baseline_units", w, minimum=0.0)
    expected = _field(inc, "expected_uplift_pct", w)
    c = ctx["contribution"]
    out: dict[str, Any] = {"incremental_cost": _money(extra_cost)}
    if c is None:
        margins = [m for m in CONDITIONAL_MARGINS]
        if ctx["price"] is None:
            out["missing"] = ["price"]
            out["summary"] = f"The option costs {extra_cost:g} {ctx['currency']} more; give price and variable_cost for the extra orders it needs."
            return out
        out["missing"] = ["variable_cost"]
        out["conditional"] = [{"assumed_margin_pct": _pct(m), "extra_units_needed": _ceil(extra_cost / (ctx["price"] * m))} for m in margins]
        out["verdict"] = "unknown"
        out["summary"] = f"The option costs {extra_cost:g} {ctx['currency']} more; the extra orders it needs depend on the unit cost (see conditional)."
        return out
    out["contribution_per_unit"] = _money(c)
    if c <= 0:
        out["extra_units_needed"] = None
        out["note"] = "No number of extra orders recovers the cost: each order contributes nothing or less."
        out["summary"] = out["note"]
        return out
    extra = _ceil(extra_cost / c)
    out["extra_units_needed"] = extra
    head = f"The option costs {extra_cost:g} {ctx['currency']} more; at {c:.2f} per order it must bring at least {extra} extra orders"
    if baseline is None or baseline <= 0:
        out["required_lift_pct"] = None
        out["missing"] = ["baseline_units"]
        out["verdict"] = "unknown"
        head += "; give baseline_units for the lift that implies."
    else:
        lift = extra / baseline
        out["baseline_units"] = baseline
        out["required_lift_pct"] = _pct(lift)
        head += f", a {_pct(lift):g} % lift over the current {baseline:g}"
        if expected is None:
            out["verdict"] = "unknown"
            head += ". Only a split run says whether it does."
        else:
            out["expected_extra_units"] = round(baseline * expected / 100, 1)
            out["verdict"] = "pays" if baseline * expected / 100 >= extra - 1e-9 else "does_not_pay"
            head += f"; at the expected +{expected:g} % ({baseline * expected / 100:.1f} orders) it {out['verdict'].replace('_', ' ')}."
    out["summary"] = head if head.endswith(".") else head + "."
    return out


def _campaign(cp: dict, ctx: dict) -> dict[str, Any]:
    """A catalogue, mailing, flyer or sampling drop costed per piece issued."""
    w = "media_value.campaign"
    pieces = _positive(cp, "pieces_sent", w, required=True)
    per_piece = _field(cp, "cost_per_piece", w, minimum=0.0)
    if per_piece is None:
        print_c = _field(cp, "print_cost_per_piece", w, minimum=0.0)
        delivery_c = _field(cp, "delivery_cost_per_piece", w, minimum=0.0)
        if print_c is None and delivery_c is None:
            raise ToolError(f"{w}: cost_per_piece or print_cost_per_piece + delivery_cost_per_piece is required")
        per_piece = (print_c or 0.0) + (delivery_c or 0.0)
    fixed = _field(cp, "fixed_costs", w, minimum=0.0) or 0.0
    responses = _field(cp, "responses", w, minimum=0.0)
    inquiry_cost = _field(cp, "inquiry_cost_per_response", w, minimum=0.0) or 0.0
    orders = _field(cp, "attributed_orders", w, minimum=0.0)
    revenue = _field(cp, "attributed_revenue", w, minimum=0.0)
    margin_in = _share(cp, "gross_margin_pct", w)
    margin = margin_in / 100 if margin_in is not None else ctx["margin"]
    total_cost = pieces * per_piece + fixed + (responses or 0.0) * inquiry_cost
    out: dict[str, Any] = {
        "pieces_sent": pieces,
        "cost_all_in": _money(total_cost),
        "cost_per_piece_all_in": _money(total_cost / pieces),
        "assumptions": ["All-in cost = pieces × cost per piece + fixed costs + responses × inquiry cost; break-even holds the current average order."],
    }
    missing = []
    if responses is not None:
        out["response_rate_pct"] = round(responses / pieces * 100, 2)
        out["cost_per_inquiry"] = _money(total_cost / responses) if responses > 0 else None
    if orders is not None:
        out["order_rate_pct"] = round(orders / pieces * 100, 2)
        out["cost_per_order"] = _money(total_cost / orders) if orders > 0 else None
    if revenue is None:
        missing.append(f"{w}.attributed_revenue")
        out["summary"] = f"The drop cost {total_cost:.2f} {ctx['currency']} all in ({total_cost / pieces:.2f} per piece); attribution is unknown, so no return is computed."
        out["missing"] = missing
        return out
    out["revenue_per_piece"] = _money(revenue / pieces)
    out["roas"] = round(revenue / total_cost, 2) if total_cost > 0 else None
    aov = revenue / orders if orders else None
    if aov is not None:
        out["average_order_value"] = _money(aov)
    if margin is None:
        missing.append(f"{w}.gross_margin_pct")
        out["conditional"] = [{"assumed_gross_margin_pct": m, "gross_profit": _money(revenue * m / 100), "net": _money(revenue * m / 100 - total_cost)} for m in CAMPAIGN_MARGINS]
        out["summary"] = f"Revenue {revenue / pieces:.2f} per piece against {total_cost / pieces:.2f} of all-in cost (ROAS {out['roas']}); the net needs gross_margin_pct (see conditional)."
        out["missing"] = missing
        return out
    gross = revenue * margin
    net = gross - total_cost
    out.update({"gross_margin_pct": _pct(margin), "gross_profit": _money(gross), "net": _money(net), "per_piece": {"revenue": _money(revenue / pieces), "cost_all_in": _money(total_cost / pieces), "gross_profit": _money(gross / pieces), "net": _money(net / pieces)}})
    be: dict[str, Any] = {}
    if aov is not None and aov * margin > 0:
        be_orders = total_cost / (aov * margin)
        be["orders"] = _ceil(be_orders)
        be["order_rate_pct_at_current_aov"] = round(be_orders / pieces * 100, 2)
        be["aov_at_current_orders"] = _money(total_cost / (orders * margin)) if orders else None
    out["break_even"] = be
    head = f"The drop {'paid back' if net >= 0 else 'did not pay back'}: {net / pieces:+.2f} {ctx['currency']} per piece issued ({net:+.2f} in all, ROAS {out['roas']})"
    if "order_rate_pct_at_current_aov" in be:
        head += f"; break-even needs a {be['order_rate_pct_at_current_aov']:g} % order rate at the current {aov:.2f} average order"
    out["summary"] = head + "."
    if missing:
        out["missing"] = missing
    return out


def _spend_options(so: dict, ctx: dict) -> dict[str, Any]:
    """Spend levels compared on absolute profit, with the marginal return of each step up next to the percentage."""
    w = "media_value.spend_options"
    options = _list(so, "options", w)
    if len(options) < 2:
        raise ToolError(f"{w}.options needs at least two spend levels")
    margin = ctx["margin"]
    rows = []
    for i, o in enumerate(options):
        where = f"{w}.options[{i}]"
        name = _name(o, f"option {i + 1}")
        spend = _positive(o, "spend", where, required=True)
        revenue = _field(o, "revenue", where, minimum=0.0)
        roas = _field(o, "roas", where, minimum=0.0)
        if revenue is None:
            if roas is None:
                raise ToolError(f"{where}: revenue or roas is required")
            revenue = spend * roas
        elif roas is not None and abs(spend * roas - revenue) > 0.01:
            raise ToolError(f"{where}: revenue {revenue:g} and roas {roas:g} contradict each other at spend {spend:g}")
        m = _share(o, "margin_pct", where)
        fixed = _field(o, "fixed_costs", where, minimum=0.0) or 0.0
        rows.append({"label": name, "spend": _money(spend), "revenue": _money(revenue), "roas": round(revenue / spend, 2), "fixed_costs": _money(fixed), "_m": None if m is None else m / 100, "_spend": spend, "_rev": revenue, "_fixed": fixed})
    rows.sort(key=lambda r: r["_spend"])
    if any(abs(a["_spend"] - b["_spend"]) < 1e-9 for a, b in zip(rows, rows[1:])):
        raise ToolError(f"{w}: two options have the same spend; each option is one spend level")
    out: dict[str, Any] = {"assumptions": ["Revenue per option is treated as attributed to the spend, not proven incremental, unless a control says so."]}
    if margin is None and any(r["_m"] is None for r in rows):
        out["missing"] = ["margin_pct"]
        out["conditional"] = [
            {"assumed_margin_pct": _pct(m), "net_profit": {r["label"]: _money(r["_rev"] * m - r["_spend"] - r["_fixed"]) for r in rows}, "best_by_profit": max(rows, key=lambda r: r["_rev"] * m - r["_spend"] - r["_fixed"])["label"]}
            for m in CONDITIONAL_MARGINS
        ]
        out["options"] = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
        out["summary"] = "The ranking by profit depends on the margin; give margin_pct (or price and variable_cost) or report a conditional row with its assumption."
        return out
    for r in rows:
        m = margin if r["_m"] is None else r["_m"]
        gp = r["_rev"] * m
        r["margin_pct"] = _pct(m)
        r["gross_profit"] = _money(gp)
        r["net_profit"] = _money(gp - r["_spend"] - r["_fixed"])
        r["roi_pct"] = _pct((gp - r["_spend"] - r["_fixed"]) / r["_spend"])
        r["_net"] = gp - r["_spend"] - r["_fixed"]
        r["_gp"] = gp
    steps = []
    for a, b in zip(rows, rows[1:]):
        d_spend = b["_spend"] - a["_spend"]
        d_rev = b["_rev"] - a["_rev"]
        m = margin if b["_m"] is None else b["_m"]
        steps.append({"from": a["label"], "to": b["label"], "delta_spend": _money(d_spend), "delta_revenue": _money(d_rev), "marginal_roas": round(d_rev / d_spend, 2), "marginal_breakeven_roas": round(1 / m, 2) if m > 0 else None, "marginal_profit": _money(b["_net"] - a["_net"]), "next_euro_profitable": b["_net"] - a["_net"] > 1e-9, "flip_margin_pct": _pct(d_spend / d_rev) if d_rev > 0 else None})
    best_profit = max(rows, key=lambda r: r["_net"])
    best_roi = max(rows, key=lambda r: r["roi_pct"])
    out.update({"options": [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows], "steps": steps, "best_by_profit": best_profit["label"], "best_by_roi": best_roi["label"]})
    last = steps[-1]
    head = f"{best_profit['label']} makes the most money: {best_profit['net_profit']:.2f} {ctx['currency']} net (ROI {best_profit['roi_pct']:g} %)"
    if best_roi["label"] != best_profit["label"]:
        head += f"; {best_roi['label']} has the higher return percentage but less profit"
    head += f". The step {last['from']} → {last['to']} returns {last['marginal_roas']:g}× against a break-even of {last['marginal_breakeven_roas']}×: marginal profit {last['marginal_profit']:+.2f}"
    if last["flip_margin_pct"] is not None:
        head += f"; the answer flips above a {last['flip_margin_pct']:g} % margin" if not last["next_euro_profitable"] else f"; it stops paying below a {last['flip_margin_pct']:g} % margin"
    out["summary"] = head + "."
    return out


def _outcome(oc: dict, ctx: dict) -> dict[str, Any]:
    """A public-interest campaign: cost per outcome against the public value of one outcome."""
    w = "media_value.outcome"
    budget = _positive(oc, "budget", w, required=True)
    target = _positive(oc, "target_outcomes", w, required=True)
    baseline = _field(oc, "baseline_outcomes", w, minimum=0.0)
    value = _field(oc, "value_per_outcome", w, minimum=0.0)
    unit = str(oc.get("outcome_unit") or "outcome").strip() or "outcome"
    out: dict[str, Any] = {"budget": _money(budget), "target_outcomes": target, "outcome_unit": unit, "cost_per_outcome": _money(budget / target)}
    head = f"{budget:g} {ctx['currency']} for {target:g} {unit}s is {budget / target:.2f} per {unit}"
    incremental = None
    if baseline is not None:
        incremental = target - baseline
        out["baseline_outcomes"] = baseline
        out["incremental_outcomes"] = incremental
        if incremental > 0:
            out["cost_per_incremental_outcome"] = _money(budget / incremental)
            head += f"; {incremental:g} above the baseline of {baseline:g}, {budget / incremental:.2f} per incremental {unit}"
        else:
            out["cost_per_incremental_outcome"] = None
            out["note"] = "The target is not above the baseline: nothing incremental is bought."
    if value is None:
        out["missing"] = ["value_per_outcome"]
        out["decision"] = "unknown"
        head += ". Give value_per_outcome (with a source) to say whether it pays."
    else:
        out["value_per_outcome"] = _money(value)
        out["breakeven_outcomes"] = _ceil(budget / value) if value > 0 else None
        compare_cost = out.get("cost_per_incremental_outcome") if incremental is not None else out["cost_per_outcome"]
        if compare_cost is None:
            out["decision"] = "does_not_pay"
        else:
            out["ratio_value_to_cost"] = round(value / compare_cost, 2)
            out["decision"] = "pays" if value >= compare_cost - 1e-9 else "does_not_pay"
        head += f" against a value of {value:.2f} per {unit}: {out['decision'].replace('_', ' ')}."
    out["summary"] = head if head.endswith(".") else head + "."
    out["assumptions"] = ["Awareness and reach are leading indicators; only the counted behaviour is an outcome."]
    return out


def _partnership(pt: dict, ctx: dict) -> dict[str, Any]:
    """A partnership or sponsorship: cash, in-kind estimate and observed outcomes kept as three separate figures."""
    w = "media_value.partnership"
    cash = _field(pt, "cash_paid", w, required=True, minimum=0.0)
    in_kind = _list(pt, "in_kind", w, required=False) or []
    outcomes = _list(pt, "outcomes", w, required=False) or []
    missing = []
    kind_rows = []
    kind_total = 0.0
    kind_known = True
    by_basis: dict[str, float] = {}
    for i, item in enumerate(in_kind):
        where = f"{w}.in_kind[{i}]"
        name = str(item.get("item") or item.get("name") or f"item {i + 1}").strip()
        basis = str(item.get("basis") or "").strip().lower()
        if basis not in {"rate_card", "quote", "analogue", "own_estimate"}:
            raise ToolError(f"{where}: basis must be rate_card, quote, analogue or own_estimate")
        value = _field(item, "estimated_value", where, minimum=0.0)
        if value is None:
            qty = _field(item, "quantity", where, minimum=0.0)
            unit_value = _field(item, "unit_value", where, minimum=0.0)
            value = qty * unit_value if qty is not None and unit_value is not None else None
        row: dict[str, Any] = {"item": name, "basis": basis, "estimated_value": None if value is None else _money(value)}
        if item.get("source"):
            row["source"] = str(item["source"])
        if value is None:
            missing.append(f"{where}.estimated_value")
            kind_known = False
        else:
            kind_total += value
            by_basis[basis] = by_basis.get(basis, 0.0) + value
        kind_rows.append(row)
    out: dict[str, Any] = {"cash_total": _money(cash), "in_kind": kind_rows, "in_kind_total": _money(kind_total) if kind_known else None, "in_kind_by_basis": {k: _money(v) for k, v in by_basis.items()}}
    out_rows = []
    cost_cash: dict[str, float | None] = {}
    cost_all: dict[str, float | None] = {}
    for i, oc in enumerate(outcomes):
        where = f"{w}.outcomes[{i}]"
        metric = str(oc.get("metric") or oc.get("name") or f"outcome {i + 1}").strip()
        value = _field(oc, "value", where, required=True, minimum=0.0)
        attribution = str(oc.get("attribution") or "none").strip().lower()
        row = {"metric": metric, "value": value, "unit": str(oc.get("unit") or metric), "attribution": attribution, "label": "observed, not attributed" if attribution == "none" else f"attributed by {attribution}"}
        out_rows.append(row)
        cost_cash[metric] = _money(cash / value) if value > 0 else None
        cost_all[metric] = _money((cash + kind_total) / value) if value > 0 and kind_known else None
    out["outcomes"] = out_rows
    out["cost_per_outcome_cash"] = cost_cash
    out["cost_per_outcome_all_in"] = cost_all
    lines = [f"Cash paid: {cash:.2f} {ctx['currency']}."]
    if kind_rows:
        if kind_known:
            own = by_basis.get("own_estimate", 0.0)
            basis_text = ", ".join(f"{k.replace('_', ' ')} {v:.2f}" for k, v in by_basis.items())
            lines.append(f"In kind (estimate, {basis_text}): {kind_total:.2f} {ctx['currency']}" + (f", of which {own:.2f} is our own estimate." if own else "."))
        else:
            lines.append("In kind: value not known for every item; see missing.")
    if out_rows:
        lines.append("Outcomes: " + "; ".join(f"{r['value']:g} {r['unit']} ({r['label']})" for r in out_rows) + ".")
        for metric, c in cost_cash.items():
            if c is not None:
                lines.append(f"Cost per {metric}: {c:.2f} on cash" + (f", {cost_all[metric]:.2f} all in." if cost_all.get(metric) is not None else "."))
    out["report_lines"] = lines
    out["mixed_total_forbidden"] = True
    out["assumptions"] = ["Cash, in-kind estimate and outcomes are never added into one 'total value'."]
    if missing:
        out["missing"] = missing
    out["summary"] = " ".join(lines)
    return out


def _benchmark(bm: dict, ctx: dict) -> dict[str, Any]:
    """An expected return checked against benchmarks that share the context, and what it implies in contribution."""
    w = "media_value.benchmark"
    context = _sub(bm, "context", w) or {}
    figures = _list(bm, "figures", w, required=False) or []
    expected = _positive(bm, "expected_return_multiple", w)
    spend = _positive(bm, "campaign_spend", w)
    revenue_per_customer = _positive(bm, "revenue_per_customer", w)
    keys = ("sector", "b2b_or_b2c", "contract", "price_position", "cycle_days")

    def norm(ctx_: dict, key: str) -> str | float | None:
        v = ctx_.get(key)
        if v is None or v == "":
            return None
        if key == "cycle_days":
            return _field(ctx_, key, f"{w}.context", minimum=0.0)
        return str(v).strip().lower()

    ours = {k: norm(context, k) for k in keys}
    matched, mismatched = [], []
    for i, fig in enumerate(figures):
        where = f"{w}.figures[{i}]"
        multiple = _positive(fig, "return_multiple", where, required=True)
        fctx = fig.get("context") or {}
        if not isinstance(fctx, dict):
            raise ToolError(f"{where}.context must be an object")
        differing = []
        for k in keys:
            a, b = ours.get(k), norm(fctx, k)
            if a is None or b is None:
                continue
            if k == "cycle_days":
                if max(a, b) > 2 * min(a, b):
                    differing.append(k)
            elif a != b:
                differing.append(k)
        row = {"return_multiple": multiple, "source_url": str(fig.get("source_url") or ""), "differing_keys": differing}
        (mismatched if len(differing) >= 2 else matched).append(row)
    rng = [min(r["return_multiple"] for r in matched), max(r["return_multiple"] for r in matched)] if matched else None
    out: dict[str, Any] = {"matched_figures": matched, "mismatched_figures": mismatched, "matched_range": rng, "assumptions": ["A figure differing on two or more context keys is excluded; mismatched figures are never averaged in.", "return_multiple is revenue over spend (ROAS)."]}
    missing = []
    if expected is None:
        missing.append(f"{w}.expected_return_multiple")
        out["expected_vs_range"] = "no_expectation"
    elif rng is None:
        out["expected_vs_range"] = "no_match"
    else:
        out["expected_vs_range"] = "inside" if rng[0] - 1e-9 <= expected <= rng[1] + 1e-9 else "above" if expected > rng[1] else "below"
    head = ""
    if expected is not None:
        head = f"A {expected:g}× return is {out['expected_vs_range'].replace('_', ' ')}" + (f" the {rng[0]:g}–{rng[1]:g}× seen in comparable cases" if rng else "; no like-for-like benchmark was found, so the client's own break-even is the only reference")
        if mismatched:
            head += f"; {len(mismatched)} figure(s) excluded for differing context"
        head += "."
        if spend is not None:
            revenue_implied = spend * expected
            out["implied"] = {"revenue": _money(revenue_implied)}
            if ctx["margin"] is not None:
                out["implied"]["contribution"] = _money(revenue_implied * ctx["margin"])
            else:
                missing.append("margin_pct")
            if revenue_per_customer is not None:
                out["implied"]["customers"] = _ceil(revenue_implied / revenue_per_customer)
                head += f" {expected:g}× on {spend:g} means {revenue_implied:.2f} {ctx['currency']} of revenue, {out['implied']['customers']} customers at {revenue_per_customer:g} each."
            else:
                head += f" {expected:g}× on {spend:g} means {revenue_implied:.2f} {ctx['currency']} of revenue."
    else:
        head = f"{len(matched)} matched and {len(mismatched)} mismatched benchmark(s); give expected_return_multiple to judge the expectation."
    if missing:
        out["missing"] = missing
    out["summary"] = head
    return out


def _routes(routes: dict, currency: str) -> dict[str, Any]:
    """Routes (a rebuild, an improvement) scored against the outcomes the segment ranks: weighted gain and cost per gain point."""
    w = "routes"
    outcomes = _list(routes, "outcomes", w)
    options = _list(routes, "routes", w)
    weights: dict[str, float] = {}
    out_rows = []
    for i, oc in enumerate(outcomes):
        where = f"{w}.outcomes[{i}]"
        name = _name(oc, f"outcome {i + 1}")
        weight = _field(oc, "weight", where, required=True)
        if not 1 <= weight <= 5:
            raise ToolError(f"{where}: weight must be between 1 and 5")
        weights[name] = weight
        out_rows.append({"name": name, "weight": weight, "source": str(oc.get("source") or "")})
    missing = []
    rows = []
    for i, r in enumerate(options):
        where = f"{w}.routes[{i}]"
        name = _name(r, f"route {i + 1}")
        cost = _field(r, "cost", where, required=True, minimum=0.0)
        months = _field(r, "months", where, minimum=0.0)
        effects = _parse_nested(r.get("effects"), f"{where}.effects")
        if not isinstance(effects, dict):
            raise ToolError(f"{where}.effects must be an object mapping each outcome name to 0, 1 or 2")
        gain = 0.0
        complete = True
        scored = {}
        for oname, weight in weights.items():
            raw = effects.get(oname)
            if raw is None or raw == "":
                missing.append(f"{where}.effects[{oname}]")
                complete = False
                continue
            effect = _field({"e": raw}, "e", f"{where}.effects[{oname}]", required=True)
            if effect not in (0, 1, 2):
                raise ToolError(f"{where}.effects[{oname}] must be 0, 1 or 2")
            scored[oname] = int(effect)
            gain += weight * effect
        for extra in effects:
            if extra not in weights:
                raise ToolError(f"{where}.effects names {extra!r}, which is not one of the outcomes")
        row: dict[str, Any] = {"name": name, "cost": _money(cost), "months": months, "effects": scored}
        if complete:
            row["weighted_gain"] = gain
            row["cost_per_gain_point"] = _money(cost / gain) if gain > 0 else None
            row["_cost"] = cost
        else:
            row["weighted_gain"] = None
            row["cost_per_gain_point"] = None
        rows.append(row)
    complete_rows = [r for r in rows if r["weighted_gain"] is not None]
    eliminate = []
    for r in complete_rows:
        for other in complete_rows:
            if other is r:
                continue
            if other["_cost"] > 0 and r["_cost"] >= 2 * other["_cost"] and r["weighted_gain"] < 1.2 * other["weighted_gain"]:
                eliminate.append({"route": r["name"], "against": other["name"], "cost_ratio": round(r["_cost"] / other["_cost"], 1), "reason": f"{r['name']} costs {r['_cost'] / other['_cost']:.1f}× more than {other['name']} and adds less than 20 % more to the outcomes the segment values ({r['weighted_gain']:g} vs {other['weighted_gain']:g})."})
                break
    for r in rows:
        r.pop("_cost", None)
    out: dict[str, Any] = {"outcomes": out_rows, "routes": rows, "eliminate": [e["route"] for e in eliminate], "elimination": eliminate, "status": "insufficient" if missing else "ok", "max_weighted_gain": 2 * sum(weights.values()), "assumptions": ["weighted_gain = Σ weight × effect (0–2); a route is eliminated when it costs at least 2× another and adds under 20 % more weighted gain."]}
    if missing:
        out["missing"] = missing
    if complete_rows:
        ranked = sorted(complete_rows, key=lambda r: (r["cost_per_gain_point"] is None, r["cost_per_gain_point"] or 0))
        best = ranked[0]
        head = f"{best['name']} buys the outcomes cheapest: {best['weighted_gain']:g} of {out['max_weighted_gain']:g} gain points at {best['cost_per_gain_point']:g} {currency} each" if best["cost_per_gain_point"] is not None else f"{best['name']} adds nothing to the ranked outcomes"
        head += "; " + " ".join(e["reason"] for e in eliminate) if eliminate else "."
        if missing:
            head += f" {len(missing)} effect(s) are missing, so the comparison is incomplete."
        out["summary"] = head if head.endswith(".") else head + "."
    else:
        out["summary"] = "No route has every effect scored; see missing."
    return out


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
