"""The economics of acquiring and keeping a customer, done by the tool, not in prose.

``unit_economics`` (rocky.calc) answers questions about one sale. This module
answers questions about one customer over time and about the money spent to
win them: what a customer contributes per period, what that is worth over a
horizon once churn is counted, how that compares with the acquisition cost,
when the acquisition pays back, and how the picture changes with returns, a
category spending ceiling, a price cut used as acquisition funding, a
subsidised entry offer with paid follow-ons, a subscription rollout, a set of
acquisition channels, or research bought before a decision.

Every function is pure, takes numbers with stated units (one currency, amounts
per purchase or per period), returns a dict, and says plainly what is
``missing`` instead of guessing. Blocks are computed only when their inputs are
present; a block whose inputs are only partly present lists what it still needs.
"""

from __future__ import annotations

import json
import math
from typing import Any

from rocky.tools import ToolError

# When the cost is missing the answer is shown at these margins so the question is concrete.
CONDITIONAL_MARGINS = (0.2, 0.4, 0.6, 0.8)
# When retention is missing the answer is shown at these churn rates per period.
CONDITIONAL_CHURN = (0.05, 0.10, 0.25, 0.50)
DEFAULT_TARGET_RATIO = 3.0
DEFAULT_HORIZON = {"month": 36, "year": 3}
# A channel that earns money but brings fewer than this share of conversions is "profitable but secondary".
SECONDARY_SHARE = 0.20
RETENTION_KEYS = "retention (churn_pct_per_period, periods_retained or annual_churn_pct)"
CAC_KEYS = "cac (or acquisition_spend + new_customers)"
COST_KEYS = "variable_cost or margin_pct"


def _num(
    args: dict,
    key: str,
    *,
    required: bool = False,
    minimum: float | None = None,
    maximum: float | None = None,
    label: str | None = None,
) -> float | None:
    """Lenient number parsing: accepts '1 200,50 €', '35 %', ints, floats."""
    name = label or key
    raw = args.get(key)
    if raw is None or raw == "":
        if required:
            raise ToolError(f"{name} is required")
        return None
    if isinstance(raw, bool):
        raise ToolError(f"{name} must be a number, got {raw!r}")
    try:
        value = float(str(raw).replace(",", ".").replace("€", "").replace("%", "").replace(" ", "").strip())
    except ValueError as exc:
        raise ToolError(f"{name} must be a number, got {raw!r}") from exc
    if math.isnan(value) or math.isinf(value):
        raise ToolError(f"{name} must be a finite number")
    if minimum is not None and value < minimum:
        raise ToolError(f"{name} must be at least {minimum:g}")
    if maximum is not None and value > maximum:
        raise ToolError(f"{name} must be at most {maximum:g}")
    return value


def _money(value: float | None) -> float | None:
    return None if value is None else round(float(value), 2)


def _pct(fraction: float | None) -> float | None:
    return None if fraction is None else round(float(fraction) * 100, 1)


def _ratio(value: float | None) -> float | None:
    return None if value is None else round(float(value), 2)


def _periods(value: float | None) -> float | None:
    return None if value is None else round(float(value), 1)


def _plural(n: float, word: str) -> str:
    return f"{n:g} {word}{'' if n == 1 else 's'}"


# ---------------------------------------------------------------- lifetime arithmetic


def _schedule(contribution: float, churn: float, discount: float, periods: int) -> list[float]:
    """Expected contribution per period of a customer acquired at period 1.

    Period 1 is paid in full; from period 2 each period is weighted by survival
    (1 - churn)^(t-1) and, if asked, discounted by (1 + rate)^(t-1).
    """
    factor = (1.0 - churn) / (1.0 + discount)
    return [contribution * factor**t for t in range(periods)]


def _lifetime(contribution: float, churn: float, discount: float, horizon: int, cac: float | None) -> dict[str, Any]:
    sched = _schedule(contribution, churn, discount, horizon)
    clv = sum(sched)
    uncapped = math.inf if churn <= 0 else 1.0 / churn
    capped = uncapped > horizon
    payback = None
    cumulative = 0.0
    if cac is not None:
        for index, value in enumerate(sched, start=1):
            cumulative += value
            if cumulative >= cac - 1e-9:
                payback = index
                break
    return {
        "expected_lifetime_periods": _periods(min(uncapped, horizon)),
        "expected_lifetime_uncapped_periods": None if math.isinf(uncapped) else _periods(uncapped),
        "lifetime_capped_at_horizon": capped,
        "clv_contribution": _money(clv),
        "clv_to_cac_ratio": None if cac is None or cac <= 0 else _ratio(clv / cac),
        "payback_periods": payback,
        "_clv": clv,
        "_schedule": sched,
    }


def _verdict(ratio: float | None, target: float) -> str | None:
    if ratio is None:
        return None
    if ratio >= target:
        return "acquire"
    if ratio >= 1.0:
        return "marginal"
    return "do not acquire at this CAC"


def _max_churn_for_payback(contribution: float, discount: float, cac: float, periods: int) -> float | None:
    """The highest churn per period at which cumulative contribution over ``periods`` still covers ``cac``."""
    if periods < 1 or contribution <= 0:
        return None
    if sum(_schedule(contribution, 0.0, discount, periods)) < cac - 1e-9:
        return None  # not even zero churn pays back in time
    if sum(_schedule(contribution, 1.0, discount, periods)) >= cac - 1e-9:
        return 1.0  # the first period alone pays back: churn does not matter
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if sum(_schedule(contribution, mid, discount, periods)) >= cac:
            lo = mid
        else:
            hi = mid
    return lo


# ---------------------------------------------------------------- core state


def _core_state(args: dict, period: str, horizon: int) -> dict[str, Any]:
    """Parse the per-customer inputs shared by every block."""
    s: dict[str, Any] = {"missing": [], "warnings": [], "assumptions": [], "period": period, "horizon": horizon}
    price = _num(args, "price", minimum=0.0)
    variable_cost = _num(args, "variable_cost", minimum=0.0)
    margin_pct = _num(args, "margin_pct", minimum=0.0, maximum=100.0)
    arpu = _num(args, "arpu", minimum=0.0)
    gross_margin_pct = _num(args, "gross_margin_pct", minimum=0.0, maximum=100.0)
    purchases = _num(args, "purchases_per_period", minimum=0.0)
    service = _num(args, "service_cost_per_customer_per_period", minimum=0.0) or 0.0
    if price is not None and arpu is not None:
        raise ToolError("give price (per purchase) or arpu (per period), not both")
    if purchases is None:
        purchases = 1.0
        if price is not None:
            s["assumptions"].append(f"purchases_per_period not given: one purchase per {period} assumed.")
    s.update(price=price, purchases=purchases, service=service, arpu=arpu)

    margin_fraction: float | None = None
    revenue_per_period: float | None = None
    contribution_per_purchase: float | None = None
    if price is not None:
        if variable_cost is None and margin_pct is not None:
            variable_cost = price * (1 - margin_pct / 100)
        revenue_per_period = price * purchases
        if variable_cost is not None:
            contribution_per_purchase = price - variable_cost
            margin_fraction = contribution_per_purchase / price if price > 0 else None
            if variable_cost >= price:
                s["warnings"].append(
                    f"variable_cost {variable_cost:g} is not below price {price:g}: every purchase loses money before service and acquisition costs."
                )
        else:
            s["missing"].append(COST_KEYS)
    elif arpu is not None:
        revenue_per_period = arpu
        if gross_margin_pct is None:
            gross_margin_pct = margin_pct
        if gross_margin_pct is not None:
            margin_fraction = gross_margin_pct / 100
        else:
            s["missing"].append("gross_margin_pct")
    else:
        # No customer revenue given: a bare margin still serves the channels block.
        bare = margin_pct if margin_pct is not None else gross_margin_pct
        margin_fraction = None if bare is None else bare / 100
    s.update(variable_cost=variable_cost, margin_fraction=margin_fraction, revenue_per_period=revenue_per_period)
    s["contribution_per_purchase"] = contribution_per_purchase
    s["contribution_per_period"] = (
        None if revenue_per_period is None or margin_fraction is None else revenue_per_period * margin_fraction - service
    )

    churn_pp = _num(args, "churn_pct_per_period", minimum=0.0, maximum=100.0)
    retained = _num(args, "periods_retained", minimum=0.0)
    annual = _num(args, "annual_churn_pct", minimum=0.0, maximum=100.0)
    churn: float | None = None
    if churn_pp is not None:
        churn = churn_pp / 100
    elif retained is not None:
        if retained <= 0:
            raise ToolError("periods_retained must be greater than zero")
        churn = min(1.0, 1.0 / retained)
        s["assumptions"].append(f"churn per {period} taken as 1 / periods_retained = {churn * 100:.1f} %.")
    elif annual is not None:
        if period == "year":
            churn = annual / 100
        else:
            churn = 1 - (1 - annual / 100) ** (1 / 12)
            s["assumptions"].append(f"annual_churn_pct {annual:g} % converted to {churn * 100:.2f} % per month.")
    s["churn"] = churn

    discount_annual = _num(args, "discount_rate_pct_per_year", minimum=0.0)
    discount = 0.0
    if discount_annual is not None and discount_annual > 0:
        discount = discount_annual / 100 if period == "year" else (1 + discount_annual / 100) ** (1 / 12) - 1
        s["assumptions"].append(f"Future periods discounted at {discount_annual:g} % a year ({discount * 100:.3g} % per {period}).")
    s["discount"] = discount

    cac = _num(args, "cac", minimum=0.0)
    spend = _num(args, "acquisition_spend", minimum=0.0)
    new_customers = _num(args, "new_customers", minimum=0.0)
    s["cac_missing"] = None
    if cac is None:
        if spend is not None and new_customers is not None:
            if new_customers <= 0:
                raise ToolError("new_customers must be greater than zero when acquisition_spend is given")
            cac = spend / new_customers
        elif spend is not None:
            s["cac_missing"] = "new_customers"
        elif new_customers is not None:
            s["cac_missing"] = "acquisition_spend"
        else:
            s["cac_missing"] = CAC_KEYS
    s["cac"] = cac
    s["acquisition_spend"] = spend
    s["new_customers"] = new_customers
    s["target_ratio"] = _num(args, "target_clv_cac_ratio", minimum=0.0) or DEFAULT_TARGET_RATIO
    if s["target_ratio"] <= 0:
        raise ToolError("target_clv_cac_ratio must be greater than zero")
    return s


# ---------------------------------------------------------------- blocks


def _per_customer(args: dict, s: dict) -> tuple[dict | None, dict | None, str]:
    """The core block and the decision block; returns (per_customer, decision, summary)."""
    if s["revenue_per_period"] is None:
        return None, None, ""
    period = s["period"]
    horizon = s["horizon"]
    cac = s["cac"]
    contribution = s["contribution_per_period"]
    churn = s["churn"]
    block: dict[str, Any] = {
        "revenue_per_period": _money(s["revenue_per_period"]),
        "contribution_per_purchase": _money(s["contribution_per_purchase"]),
        "margin_pct": _pct(s["margin_fraction"]),
        "service_cost_per_period": _money(s["service"]),
        "contribution_per_period": _money(contribution),
        "churn_pct_per_period": _pct(churn),
        "cac": _money(cac),
        "missing": [],
        "notes": [],
    }
    if contribution is None:
        block["missing"].append(COST_KEYS)
    if churn is None:
        block["missing"].append(RETENTION_KEYS)
    if cac is None:
        block["missing"].append(s["cac_missing"])

    decision: dict[str, Any] | None = None
    if contribution is not None and churn is not None:
        life = _lifetime(contribution, churn, s["discount"], horizon, cac)
        s["lifetime"] = life
        block.update({k: v for k, v in life.items() if not k.startswith("_")})
        if life["lifetime_capped_at_horizon"]:
            uncapped = life["expected_lifetime_uncapped_periods"]
            block["notes"].append(
                (f"1 / churn = {uncapped:g} {period}s" if uncapped is not None else "Zero churn means an unbounded lifetime")
                + f"; CLV counts only the {horizon}-{period} horizon."
            )
        if cac is not None and life["payback_periods"] is None:
            block["notes"].append(f"Cumulative contribution never reaches the CAC of {cac:,.2f} within {horizon} {period}s.")
        if contribution <= 0:
            block["notes"].append("Contribution per period is not positive: the customer never pays back any acquisition cost.")
        decision = _decision(args, s, life)
        summary = (
            f"A customer contributes {contribution:,.2f} per {period} and, at {churn * 100:.1f} % churn per {period}, "
            f"{life['_clv']:,.2f} over {horizon} {period}s"
        )
        if cac is not None:
            ratio = life["clv_to_cac_ratio"]
            summary += (
                f"; against a CAC of {cac:,.2f} that is {ratio:g}× (target {s['target_ratio']:g}×): {decision['verdict']}"
                + (f", payback in {period} {life['payback_periods']}." if life["payback_periods"] else ", no payback within the horizon.")
            )
        else:
            summary += f"; CAC is unknown, so acquire only below {decision['max_cac_at_target_ratio']:,.2f} per customer."
    else:
        block["conditional"] = _conditional(s)
        if contribution is None and churn is None:
            summary = f"Both the cost and the retention are missing, so CLV is shown conditionally; ask for {COST_KEYS} and {RETENTION_KEYS}."
        elif contribution is None:
            summary = f"The cost per purchase is missing, so CLV is shown per margin; ask for {COST_KEYS} or state the assumption."
        else:
            summary = f"Retention is missing, so CLV is shown per churn rate; ask for {RETENTION_KEYS} or state the assumption."
    return block, decision, summary


def _conditional(s: dict) -> list[dict[str, Any]]:
    revenue = s["revenue_per_period"]
    rows: list[dict[str, Any]] = []
    margins = [s["margin_fraction"]] if s["margin_fraction"] is not None else list(CONDITIONAL_MARGINS)
    churns = [s["churn"]] if s["churn"] is not None else list(CONDITIONAL_CHURN)
    for margin in margins:
        contribution = revenue * margin - s["service"]
        for churn in churns:
            life = _lifetime(contribution, churn, s["discount"], s["horizon"], s["cac"])
            rows.append(
                {
                    "assumed_margin_pct": _pct(margin),
                    "assumed_churn_pct_per_period": _pct(churn),
                    "contribution_per_period": _money(contribution),
                    "clv_contribution": life["clv_contribution"],
                    "clv_to_cac_ratio": life["clv_to_cac_ratio"],
                    "payback_periods": life["payback_periods"],
                    "verdict": _verdict(life["clv_to_cac_ratio"], s["target_ratio"]),
                    "max_cac_at_target_ratio": _money(life["_clv"] / s["target_ratio"]),
                }
            )
    return rows


def _decision(args: dict, s: dict, life: dict) -> dict[str, Any]:
    target = s["target_ratio"]
    cac = s["cac"]
    clv = life["_clv"]
    block: dict[str, Any] = {
        "target_clv_cac_ratio": target,
        "clv_to_cac_ratio": life["clv_to_cac_ratio"],
        "verdict": _verdict(life["clv_to_cac_ratio"], target),
        "rule": f"acquire when CLV/CAC ≥ {target:g}; marginal when CLV covers CAC but misses the target; otherwise do not acquire at this CAC",
        "max_cac_at_target_ratio": _money(clv / target),
        "max_cac_for_payback": _money(clv),
        "missing": [] if cac is not None else [s["cac_missing"]],
    }
    payback_target = _num(args, "payback_target_periods", minimum=1.0)
    if payback_target is not None:
        if cac is None:
            block["missing"].append(f"{CAC_KEYS} to back-solve max_churn_pct_for_payback_within")
        else:
            periods = int(round(payback_target))
            max_churn = _max_churn_for_payback(s["contribution_per_period"], s["discount"], cac, periods)
            block["payback_target_periods"] = periods
            block["max_churn_pct_for_payback_within"] = _pct(max_churn)
            if max_churn is None:
                block["payback_note"] = (
                    f"Even at zero churn, {periods} {s['period']}s of contribution do not cover the CAC of {cac:,.2f}."
                )
            elif max_churn >= 1.0:
                block["payback_note"] = f"The first {s['period']} alone covers the CAC; churn does not affect payback."
    return block


def _returns(args: dict, s: dict) -> tuple[dict | None, str]:
    rate = _num(args, "return_rate_pct", minimum=0.0, maximum=100.0)
    if rate is None:
        return None, ""
    return_cost = _num(args, "return_cost_per_return", minimum=0.0)
    refund_share = _num(args, "refund_share_pct", minimum=0.0, maximum=100.0)
    exchange_cost = _num(args, "exchange_cost", minimum=0.0)
    block: dict[str, Any] = {"return_rate_pct": round(rate, 1), "missing": [], "assumptions": []}
    if s["price"] is None:
        block["missing"].append("price (returns are counted per order; arpu cannot be split into orders)")
        return block, "Returns need a price per order to be costed; give price and variable_cost."
    if s["contribution_per_purchase"] is None:
        block["missing"].append(COST_KEYS)
        return block, f"Returns cannot be costed without {COST_KEYS}."
    if return_cost is None:
        block["missing"].append("return_cost_per_return")
        block["assumptions"].append("return_cost_per_return not given: reverse shipping and handling counted as zero until it is.")
        return_cost = 0.0
    if refund_share is None:
        refund_share = 100.0
        block["assumptions"].append("refund_share_pct not given: every return is refunded (no exchanges).")
    if exchange_cost is None:
        exchange_cost = 0.0
        if refund_share < 100:
            block["assumptions"].append("exchange_cost not given: re-shipping an exchange counted as zero.")
    block["assumptions"].append("A refunded return loses the whole revenue; the product cost is treated as sunk (not restocked).")
    r, f = rate / 100, refund_share / 100
    price, vc = s["price"], s["variable_cost"]
    revenue_kept = price * (1 - r * f)
    cost_per_order = vc + r * return_cost + r * (1 - f) * exchange_cost
    net_per_order = revenue_kept - cost_per_order
    net_per_period = net_per_order * s["purchases"] - s["service"]
    block.update(
        {
            "refund_share_pct": round(refund_share, 1),
            "return_cost_per_return": _money(return_cost),
            "exchange_cost": _money(exchange_cost),
            "gross_contribution_per_order": _money(s["contribution_per_purchase"]),
            "revenue_lost_per_order": _money(price * r * f),
            "handling_cost_per_order": _money(r * return_cost + r * (1 - f) * exchange_cost),
            "net_contribution_per_order": _money(net_per_order),
            "net_contribution_per_period": _money(net_per_period),
            "kept_order_share_pct": _pct(1 - r * f),
        }
    )
    cac = s["cac"]
    if cac is not None:
        block["effective_cac_per_kept_order"] = _money(cac / (1 - r * f)) if r * f < 1 else None
    summary = f"With {rate:g} % returns the contribution per order falls from {s['contribution_per_purchase']:,.2f} to {net_per_order:,.2f}"
    if s["churn"] is not None and "lifetime" in s:
        before = s["lifetime"]
        after = _lifetime(net_per_period, s["churn"], s["discount"], s["horizon"], cac)
        verdict_before = _verdict(before["clv_to_cac_ratio"], s["target_ratio"])
        verdict_after = _verdict(after["clv_to_cac_ratio"], s["target_ratio"])
        block["clv_contribution_after_returns"] = after["clv_contribution"]
        block["clv_to_cac_ratio_after_returns"] = after["clv_to_cac_ratio"]
        block["payback_periods_after_returns"] = after["payback_periods"]
        block["max_cac_at_target_ratio_after_returns"] = _money(after["_clv"] / s["target_ratio"])
        block["verdict_before_returns"] = verdict_before
        block["verdict_after_returns"] = verdict_after
        block["decision_changes"] = verdict_before != verdict_after
        summary += f"; CLV falls from {before['_clv']:,.2f} to {after['_clv']:,.2f}"
        if cac is not None:
            summary += (
                f" and the verdict {'changes from ' + verdict_before + ' to ' + verdict_after if verdict_before != verdict_after else 'stays ' + verdict_after}"
            )
    return block, summary + "."


def _category_ceiling(args: dict, s: dict) -> tuple[dict | None, str]:
    spend = _num(args, "category_spend_per_buyer_per_period", minimum=0.0)
    share = _num(args, "share_of_wallet_pct", minimum=0.0, maximum=100.0)
    if spend is None and share is None:
        return None, ""
    block: dict[str, Any] = {"missing": []}
    if spend is None:
        block["missing"].append("category_spend_per_buyer_per_period")
    if share is None:
        block["missing"].append("share_of_wallet_pct")
    if block["missing"]:
        return block, "The category ceiling needs both the buyer's category spend per period and the obtainable share of wallet."
    obtainable = spend * share / 100
    block.update(
        {
            "category_spend_per_buyer_per_period": _money(spend),
            "share_of_wallet_pct": round(share, 1),
            "obtainable_revenue_per_period": _money(obtainable),
            "stated_revenue_per_period": _money(s["revenue_per_period"]),
        }
    )
    summary = f"A buyer spends {spend:,.2f} per {s['period']} on the category; {share:g} % of that is {obtainable:,.2f} obtainable revenue"
    if s["margin_fraction"] is not None:
        implied = obtainable * s["margin_fraction"] - s["service"]
        block["implied_contribution_per_period"] = _money(implied)
        if s["churn"] is not None:
            life = _lifetime(implied, s["churn"], s["discount"], s["horizon"], s["cac"])
            block["clv_at_ceiling"] = life["clv_contribution"]
            block["max_cac_at_target_ratio_at_ceiling"] = _money(life["_clv"] / s["target_ratio"])
        summary += f", {implied:,.2f} contribution per {s['period']}"
    else:
        block["missing"].append(COST_KEYS if s["price"] is not None else "gross_margin_pct")
    if s["revenue_per_period"] is not None:
        exceeds = s["revenue_per_period"] > obtainable + 1e-9
        block["stated_revenue_exceeds_ceiling"] = exceeds
        if exceeds:
            block["warning"] = (
                f"The stated revenue per {s['period']} ({s['revenue_per_period']:,.2f}) exceeds what {share:g} % of the category spend allows "
                f"({obtainable:,.2f}); the plan assumes more than the wallet holds."
            )
            summary += f". Warning: the stated {s['revenue_per_period']:,.2f} per {s['period']} exceeds this ceiling"
    return block, summary + "."


def _funding(args: dict, s: dict) -> tuple[dict | None, str]:
    customers = _num(args, "customers", minimum=0.0)
    new_price = _num(args, "new_price", minimum=0.0)
    cut = _num(args, "acquisition_price_cut_pct")
    if customers is None and new_price is None and cut is None:
        return None, ""
    if cut is not None and not 0 <= cut < 100:
        raise ToolError("acquisition_price_cut_pct must be between 0 and 100")
    block: dict[str, Any] = {"missing": []}
    if customers is None:
        block["missing"].append("customers")
    if s["price"] is None and (new_price is not None or cut is not None):
        block["missing"].append("price (a price cut needs the current price per purchase)")
    if s["contribution_per_period"] is None:
        block["missing"].append(COST_KEYS if s["price"] is not None or s["arpu"] is None else "gross_margin_pct")
    if block["missing"]:
        return block, "The contribution pool needs the number of customers and the contribution per period."
    contribution = s["contribution_per_period"]
    pool = customers * contribution
    block.update({"customers": customers, "contribution_per_period": _money(contribution), "contribution_pool_current": _money(pool)})
    summary = f"{customers:g} customers × {contribution:,.2f} = a contribution pool of {pool:,.2f} per {s['period']}"
    if new_price is None and cut is not None:
        new_price = s["price"] * (1 - cut / 100)
    if new_price is not None:
        if new_price <= 0:
            raise ToolError("new_price must be greater than zero")
        new_contribution = (new_price - s["variable_cost"]) * s["purchases"] - s["service"]
        pool_new = customers * new_contribution
        drop = pool - pool_new
        block.update(
            {
                "new_price": _money(new_price),
                "new_contribution_per_period": _money(new_contribution),
                "contribution_pool_new_same_volume": _money(pool_new),
                "pool_drop_per_period": _money(drop),
            }
        )
        if new_contribution > 0:
            tolerable = pool / new_contribution
            block["units_tolerable"] = round(tolerable, 1)
            block["extra_customers_needed"] = round(max(0.0, tolerable - customers), 1)
            block["required_uplift_pct"] = _pct(tolerable / customers - 1) if customers > 0 else None
            summary += (
                f"; at {new_price:,.2f} the pool falls to {pool_new:,.2f} unless the base grows to {tolerable:,.1f} customers "
                f"({max(0.0, tolerable - customers):,.1f} more)"
            )
        else:
            block["units_tolerable"] = None
            block["extra_customers_needed"] = None
            block["note"] = "The new price leaves no contribution per customer, so no volume restores the pool."
            summary += f"; at {new_price:,.2f} each customer contributes {new_contribution:,.2f}, so no volume restores the pool"
        if s["cac"] is not None and s["cac"] > 0:
            block["pool_drop_as_customers_at_cac"] = round(drop / s["cac"], 1)
            summary += f". The cut costs {drop:,.2f} per {s['period']}, the price of {drop / s['cac']:,.1f} new customers at a CAC of {s['cac']:,.2f}"
    return block, summary + "."


def _follow_on(args: dict, s: dict) -> tuple[dict | None, str]:
    entry_price = _num(args, "entry_price", minimum=0.0)
    entry_cost = _num(args, "entry_variable_cost", minimum=0.0)
    follow = args.get("follow_on")
    items = args.get("variable_cost_items")
    if entry_price is None and entry_cost is None and follow is None and items is None:
        return None, ""
    block: dict[str, Any] = {"missing": [], "assumptions": []}
    if items is not None:
        if not isinstance(items, dict):
            raise ToolError("variable_cost_items must be an object of {name: cost per user per period}")
        parsed = {str(name): _num({"v": cost}, "v", required=True, minimum=0.0, label=f"variable_cost_items[{name}]") for name, cost in items.items()}
        marginal = sum(parsed.values())
        block["variable_cost_items"] = {k: _money(v) for k, v in parsed.items()}
        block["marginal_cost_per_user_per_period"] = _money(marginal)
        users = _num(args, "users", minimum=0.0)
        if users is not None:
            block["serving_cost_per_period"] = _money(marginal * users)
        if entry_cost is None:
            entry_cost = marginal
            block["assumptions"].append("entry_variable_cost taken as the sum of variable_cost_items.")
    if entry_price is None:
        block["missing"].append("entry_price (0 is allowed for a free offer)")
    if entry_cost is None:
        block["missing"].append("entry_variable_cost (or variable_cost_items)")
    if follow is None:
        block["missing"].append("follow_on {price, variable_cost, units_per_period, attach_rate_pct, periods}")
    elif not isinstance(follow, dict):
        raise ToolError("follow_on must be an object with price, variable_cost, units_per_period, attach_rate_pct, periods")
    if block["missing"]:
        return block, "The entry offer needs entry_price, entry_variable_cost and a follow_on block to be judged."
    fo_price = _num(follow, "price", minimum=0.0, label="follow_on.price")
    fo_cost = _num(follow, "variable_cost", minimum=0.0, label="follow_on.variable_cost")
    units = _num(follow, "units_per_period", minimum=0.0, label="follow_on.units_per_period")
    attach = _num(follow, "attach_rate_pct", minimum=0.0, maximum=100.0, label="follow_on.attach_rate_pct")
    periods_raw = _num(follow, "periods", minimum=1.0, label="follow_on.periods")
    fo_churn_pct = _num(follow, "churn_pct_per_period", minimum=0.0, maximum=100.0, label="follow_on.churn_pct_per_period")
    if fo_price is None:
        block["missing"].append("follow_on.price")
    if fo_cost is None:
        block["missing"].append("follow_on.variable_cost")
    if block["missing"]:
        return block, "The follow-on offer needs its own price and variable cost."
    if units is None:
        units = 1.0
        block["assumptions"].append("follow_on.units_per_period not given: one unit per period assumed.")
    periods = int(round(periods_raw)) if periods_raw is not None else s["horizon"]
    if periods_raw is None:
        block["assumptions"].append(f"follow_on.periods not given: the {s['horizon']}-{s['period']} horizon is used.")
    if fo_churn_pct is not None:
        churn = fo_churn_pct / 100
    elif s["churn"] is not None:
        churn = s["churn"]
        block["assumptions"].append("Follow-on buyers churn at the customer churn rate given above.")
    else:
        churn = 0.0
        block["assumptions"].append("No churn given for follow-on buyers: they are assumed to stay for all periods.")

    entry_contribution = entry_price - entry_cost
    subsidy = max(0.0, -entry_contribution)
    per_attached = sum(_schedule((fo_price - fo_cost) * units, churn, s["discount"], periods))
    block.update(
        {
            "entry_price": _money(entry_price),
            "entry_variable_cost": _money(entry_cost),
            "entry_contribution_per_entrant": _money(entry_contribution),
            "subsidy_per_entrant": _money(subsidy),
            "follow_on_contribution_per_unit": _money(fo_price - fo_cost),
            "follow_on_periods": periods,
            "follow_on_churn_pct_per_period": _pct(churn),
            "follow_on_contribution_per_attached_buyer": _money(per_attached),
            "attach_rate_pct": None if attach is None else round(attach, 1),
        }
    )
    if per_attached > 0:
        breakeven = subsidy / per_attached
        block["breakeven_attach_rate_pct"] = _pct(breakeven)
        if breakeven > 1:
            block["note"] = "Even if every entrant bought the follow-on the subsidy would not be covered."
    else:
        block["breakeven_attach_rate_pct"] = None
        block["note"] = "The follow-on itself contributes nothing, so no attach rate covers the subsidy."
    summary = f"Each entrant costs {subsidy:,.2f} in subsidy" if subsidy > 0 else f"Each entrant already contributes {entry_contribution:,.2f}"
    summary += f"; a buyer who attaches the follow-on is worth {per_attached:,.2f} over {periods} {s['period']}s"
    if attach is None:
        block["missing"].append("follow_on.attach_rate_pct (break-even is given; the expected value needs the real rate)")
        if block.get("breakeven_attach_rate_pct") is not None:
            summary += f", so break-even is at {block['breakeven_attach_rate_pct']:g} % attach; the expected rate is missing"
    else:
        expected = attach / 100 * per_attached
        net = entry_contribution + expected
        block["expected_follow_on_per_entrant"] = _money(expected)
        block["net_per_entrant"] = _money(net)
        block["verdict"] = "pays" if net > 0 else "does not pay"
        summary += f"; at {attach:g} % attach that is {expected:,.2f} per entrant, net {net:,.2f}"
        if block.get("breakeven_attach_rate_pct") is not None:
            summary += f" (break-even {block['breakeven_attach_rate_pct']:g} %)"
    return block, summary + "."


def _rollout(args: dict, s: dict) -> tuple[dict | None, str]:
    now = _num(args, "subscribers_now", minimum=0.0)
    target = _num(args, "target_subscribers", minimum=0.0)
    new = _num(args, "new_per_period", minimum=0.0)
    if now is None and target is None and new is None:
        return None, ""
    period, horizon = s["period"], s["horizon"]
    block: dict[str, Any] = {"missing": [], "assumptions": []}
    if now is None:
        block["missing"].append("subscribers_now")
    if s["churn"] is None:
        block["missing"].append(RETENTION_KEYS)
    if target is None and new is None:
        block["missing"].append("target_subscribers or new_per_period")
    if block["missing"]:
        return block, "The rollout table needs subscribers_now, churn per period and a target or the number added per period."
    churn = s["churn"]
    cac = s["cac"]
    contribution = s["contribution_per_period"]
    block["churn_pct_per_period"] = _pct(churn)

    needed = None
    if target is not None:
        decay = (1 - churn) ** horizon
        needed = (target - now) / horizon if churn <= 0 else (target - now * decay) * churn / (1 - decay)
        needed = max(0.0, needed)
        block["new_per_period_needed_for_target_in_horizon"] = round(needed, 1)
    if new is None:
        new = needed
        block["assumptions"].append(f"new_per_period back-solved so the target is reached in {horizon} {period}s.")
    block["new_per_period"] = round(new, 1)

    rows: list[dict[str, Any]] = []
    subs, new_active, cum_added, cum_contribution_all, cum_contribution_new = now, 0.0, 0.0, 0.0, 0.0
    periods_to_target: int | None = 0 if target is not None and now >= target else None
    cohort_payback: int | None = None
    for t in range(1, horizon + 1):
        churned = subs * churn
        subs = subs - churned + new
        new_active = new_active * (1 - churn) + new
        cum_added += new
        row: dict[str, Any] = {"period": t, "subscribers": round(subs, 1), "churned": round(churned, 1), "added": round(new, 1)}
        if cac is not None:
            row["cumulative_acquisition_spend"] = _money(cum_added * cac)
        if contribution is not None:
            cum_contribution_all += subs * contribution
            cum_contribution_new += new_active * contribution
            row["cumulative_contribution_all"] = _money(cum_contribution_all)
            row["cumulative_contribution_new"] = _money(cum_contribution_new)
            if cac is not None and cohort_payback is None and cum_contribution_new >= cum_added * cac - 1e-9 and cum_added > 0:
                cohort_payback = t
        rows.append(row)
        if periods_to_target is None and target is not None and subs >= target - 1e-9:
            periods_to_target = t
    block["table"] = rows
    block["subscribers_at_horizon"] = round(subs, 1)
    steady = None if churn <= 0 else new / churn
    block["steady_state_subscribers"] = None if steady is None else round(steady, 1)
    if cac is not None:
        block["cumulative_acquisition_spend"] = _money(cum_added * cac)
    if contribution is not None:
        block["cumulative_contribution_all"] = _money(cum_contribution_all)
        block["cumulative_contribution_new"] = _money(cum_contribution_new)
        if cac is not None:
            block["cumulative_payback_period"] = cohort_payback
            block["payback_basis"] = "contribution of the subscribers added during the rollout versus their acquisition spend"
    else:
        block["missing"].append(COST_KEYS if s["price"] is not None else "arpu + gross_margin_pct for the contribution columns")
    if cac is None:
        block["missing"].append(f"{s['cac_missing']} for the spend columns")

    summary = f"Starting from {now:g} subscribers, adding {new:,.1f} per {period} at {churn * 100:.1f} % churn gives {subs:,.1f} after {horizon} {period}s"
    if target is not None:
        block["target_subscribers"] = target
        block["periods_to_target"] = periods_to_target
        if periods_to_target is not None:
            block["reachable"] = True
            summary += f"; the target of {target:g} is reached in {period} {periods_to_target}" if periods_to_target else f"; the target of {target:g} is already met"
        else:
            block["reachable"] = False
            churn_for_target = new / target if target > 0 else None
            block["churn_pct_for_target_at_steady_state"] = _pct(churn_for_target)
            if steady is not None and steady <= target:
                block["status"] = "unreachable at this churn"
                summary += (
                    f"; the target of {target:g} is unreachable at this churn (steady state {steady:,.1f}); it becomes the steady state at "
                    f"{(churn_for_target or 0) * 100:.1f} % churn, or needs {needed:,.1f} added per {period} to be reached in {horizon} {period}s"
                )
            else:
                block["status"] = "unreachable within the horizon"
                summary += f"; the target of {target:g} is not reached within {horizon} {period}s (needs {needed:,.1f} added per {period})"
    if cac is not None and contribution is not None:
        summary += (
            f". Acquisition spend {cum_added * cac:,.2f} against {cum_contribution_new:,.2f} contribution from the new subscribers"
            + (f", paid back in {period} {cohort_payback}" if cohort_payback else ", not paid back within the horizon")
        )
    return block, summary + "."


def _channels(args: dict, s: dict) -> tuple[dict | None, str]:
    channels = args.get("channels")
    if channels is None:
        return None, ""
    if not isinstance(channels, list) or not channels:
        raise ToolError("channels must be a non-empty list of {name, spend, conversions, revenue?}")
    channel_margin = _num(args, "channel_margin_pct", minimum=0.0, maximum=100.0)
    margin = channel_margin / 100 if channel_margin is not None else s["margin_fraction"]
    block: dict[str, Any] = {"missing": [], "assumptions": [], "rows": []}
    if margin is None:
        block["missing"].append("margin (margin_pct, gross_margin_pct, or price + variable_cost)")
    rows: list[dict[str, Any]] = []
    total_conversions = 0.0
    for index, raw in enumerate(channels):
        if not isinstance(raw, dict):
            raise ToolError(f"channels[{index}] must be an object")
        name = str(raw.get("name") or f"channel {index + 1}")
        spend = _num(raw, "spend", minimum=0.0, label=f"{name}.spend")
        conversions = _num(raw, "conversions", minimum=0.0, label=f"{name}.conversions")
        revenue = _num(raw, "revenue", minimum=0.0, label=f"{name}.revenue")
        if conversions is None:
            raise ToolError(f"{name}.conversions is required")
        if spend is None:
            raise ToolError(f"{name}.spend is required when conversions are reported")
        if revenue is None and s["price"] is not None:
            revenue = conversions * s["price"]
            block["assumptions"].append(f"{name}: revenue taken as conversions × price.")
        total_conversions += conversions
        rows.append({"name": name, "spend": spend, "conversions": conversions, "revenue": revenue})
    out_rows: list[dict[str, Any]] = []
    for row in rows:
        conv, spend, revenue = row["conversions"], row["spend"], row["revenue"]
        contribution = None if revenue is None or margin is None else revenue * margin
        net = None if contribution is None else contribution - spend
        share = conv / total_conversions if total_conversions > 0 else None
        out_rows.append(
            {
                "name": row["name"],
                "spend": _money(spend),
                "conversions": conv,
                "revenue": _money(revenue),
                "cpa": None if conv <= 0 else _money(spend / conv),
                "aov": None if conv <= 0 or revenue is None else _money(revenue / conv),
                "contribution": _money(contribution),
                "net_after_spend": _money(net),
                "roi_pct": None if net is None or spend <= 0 else _pct(net / spend),
                "share_of_conversions_pct": _pct(share),
                "profitable_but_secondary": bool(net is not None and net > 0 and share is not None and share < SECONDARY_SHARE),
                "_net": net,
            }
        )
        if row["revenue"] is None and "revenue (per channel, or price to derive it)" not in block["missing"]:
            block["missing"].append("revenue (per channel, or price to derive it)")
    ranked = sorted(out_rows, key=lambda r: (r["_net"] is None, -(r["_net"] or 0.0), r["cpa"] if r["cpa"] is not None else math.inf))
    for rank, row in enumerate(ranked, start=1):
        row["rank"] = rank if row["_net"] is not None else None
    for row in out_rows:
        row.pop("_net")
    block["rows"] = out_rows
    block["ranking"] = [r["name"] for r in ranked]
    block["secondary_rule"] = f"net after spend > 0 and under {SECONDARY_SHARE * 100:.0f} % of conversions"
    secondary = [r["name"] for r in out_rows if r["profitable_but_secondary"]]
    losing = [r["name"] for r in out_rows if r["net_after_spend"] is not None and r["net_after_spend"] < 0]
    block["profitable_but_secondary"] = secondary
    block["losing_money"] = losing
    if margin is None:
        summary = f"{len(rows)} channels compared on CPA and AOV only; contribution needs a margin."
    else:
        summary = f"Channels by net contribution after spend: {', '.join(block['ranking'])}"
        if secondary:
            summary += f"; profitable but secondary (under {SECONDARY_SHARE * 100:.0f} % of conversions): {', '.join(secondary)}"
        if losing:
            summary += f"; losing money after spend: {', '.join(losing)}"
        summary += "."
    return block, summary


def _research_value(args: dict, s: dict) -> tuple[dict | None, str]:
    amount = _num(args, "decision_amount", minimum=0.0)
    p_wrong = _num(args, "p_wrong")
    options = args.get("research_options")
    if amount is None and p_wrong is None and options is None:
        return None, ""
    block: dict[str, Any] = {"missing": []}
    if amount is None:
        block["missing"].append("decision_amount")
    if p_wrong is None:
        block["missing"].append("p_wrong (0–1)")
    elif not 0 <= p_wrong <= 1:
        raise ToolError("p_wrong must be between 0 and 1")
    if options is None:
        block["missing"].append("research_options [{name, cost, p_wrong_after}]")
    elif not isinstance(options, list) or not options:
        raise ToolError("research_options must be a non-empty list of {name, cost, p_wrong_after}")
    share = _num(args, "loss_if_wrong_share", minimum=0.0, maximum=1.0)
    if share is None:
        share = 1.0
    if amount is not None and p_wrong is not None:
        block["expected_loss_now"] = _money(amount * p_wrong * share)
        block["loss_if_wrong_share"] = share
    if block["missing"]:
        return block, "The value of research needs the amount at stake, the chance of being wrong and the research options."
    loss_now = amount * p_wrong * share
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(options):
        if not isinstance(raw, dict):
            raise ToolError(f"research_options[{index}] must be an object")
        name = str(raw.get("name") or f"option {index + 1}")
        cost = _num(raw, "cost", required=True, minimum=0.0, label=f"{name}.cost")
        p_after = _num(raw, "p_wrong_after", required=True, label=f"{name}.p_wrong_after")
        if not 0 <= p_after <= 1:
            raise ToolError(f"{name}.p_wrong_after must be between 0 and 1")
        loss_after = amount * p_after * share
        value = loss_now - loss_after
        rows.append(
            {
                "name": name,
                "cost": _money(cost),
                "p_wrong_after": p_after,
                "expected_loss_after": _money(loss_after),
                "value_of_research": _money(value),
                "net_value": _money(value - cost),
                "_net": value - cost,
            }
        )
    best = max(rows, key=lambda r: r["_net"])
    for row in rows:
        row.pop("_net")
    block["options"] = rows
    if best["net_value"] > 0:
        block["recommendation"] = best["name"]
        summary = (
            f"Being wrong on {amount:,.2f} with probability {p_wrong:g} costs {loss_now:,.2f} in expectation; "
            f"{best['name']} cuts that to {best['expected_loss_after']:,.2f} for {best['cost']:,.2f}, net {best['net_value']:,.2f}: buy it."
        )
    else:
        block["recommendation"] = "skip research"
        summary = (
            f"Being wrong on {amount:,.2f} with probability {p_wrong:g} costs {loss_now:,.2f} in expectation; "
            f"no research option is worth its cost (best net {best['net_value']:,.2f} for {best['name']}): skip research and decide."
        )
    return block, summary


# ---------------------------------------------------------------- entry points


def customer_economics(args: dict) -> dict[str, Any]:
    """CLV against CAC and everything around it; see the module docstring for the blocks.

    Shared inputs: ``price`` + ``variable_cost`` (or ``margin_pct``) per purchase, or
    ``arpu`` + ``gross_margin_pct`` per period; ``purchases_per_period`` (default 1);
    ``period`` 'month' | 'year'; retention as ``churn_pct_per_period``,
    ``periods_retained`` or ``annual_churn_pct``; ``service_cost_per_customer_per_period``;
    ``cac`` or ``acquisition_spend`` + ``new_customers``; ``target_clv_cac_ratio`` (3);
    ``horizon_periods`` (36 months / 3 years); ``discount_rate_pct_per_year``;
    ``payback_target_periods``. Each optional block has its own inputs and is
    computed only when at least one of them is present.
    """
    args = args or {}
    if not isinstance(args, dict):
        raise ToolError("arguments must be an object")
    currency = str(args.get("currency") or "EUR").strip() or "EUR"
    period = str(args.get("period") or "month").strip().lower()
    if period not in DEFAULT_HORIZON:
        raise ToolError("period must be month or year")
    horizon_raw = _num(args, "horizon_periods", minimum=1.0)
    horizon = DEFAULT_HORIZON[period] if horizon_raw is None else int(round(horizon_raw))
    if horizon > 600:
        raise ToolError("horizon_periods must be at most 600")
    s = _core_state(args, period, horizon)

    out: dict[str, Any] = {
        "currency": currency,
        "period": period,
        "horizon_periods": horizon,
        "inputs": {
            "price": _money(s["price"]),
            "variable_cost": _money(s["variable_cost"]),
            "arpu": _money(s["arpu"]),
            "purchases_per_period": s["purchases"],
            "service_cost_per_customer_per_period": _money(s["service"]),
            "churn_pct_per_period": _pct(s["churn"]),
            "cac": _money(s["cac"]),
            "target_clv_cac_ratio": s["target_ratio"],
            "discount_rate_pct_per_period": _pct(s["discount"]) if s["discount"] else 0.0,
        },
        "missing": [],
        "warnings": list(s["warnings"]),
        "assumptions": list(s["assumptions"]),
        "method": (
            "CLV = contribution per period summed over the horizon, each period weighted by survival (1 - churn)^(t-1) "
            "and optional discounting; payback = first period whose cumulative expected contribution covers the CAC."
        ),
    }
    summaries: list[str] = []
    computed: list[str] = []

    per_customer, decision, core_summary = _per_customer(args, s)
    if per_customer is not None:
        out["per_customer"] = per_customer
        computed.append("per_customer")
        if decision is not None:
            out["decision"] = decision
            computed.append("decision")
        summaries.append(core_summary)
        out["per_customer"]["summary"] = core_summary

    for key, fn in (
        ("returns", _returns),
        ("category_ceiling", _category_ceiling),
        ("funding", _funding),
        ("follow_on", _follow_on),
        ("subscription_rollout", _rollout),
        ("channels", _channels),
        ("research_value", _research_value),
    ):
        block, summary = fn(args, s)
        if block is None:
            continue
        block["summary"] = summary
        out[key] = block
        computed.append(key)
        summaries.append(summary)

    if not computed:
        raise ToolError(
            "nothing to compute: give price (or arpu) for a customer, or the inputs of one block "
            "(return_rate_pct, category_spend_per_buyer_per_period, customers, entry_price, subscribers_now, channels, decision_amount)"
        )
    missing: list[str] = []
    for key in computed:
        for item in out.get(key, {}).get("missing", []) if isinstance(out.get(key), dict) else []:
            label = f"{key}: {item}"
            if label not in missing:
                missing.append(label)
    out["missing"] = missing
    out["computed"] = computed
    out["summary"] = " ".join(x for x in summaries if x)
    return out


def customer_economics_tool(args: dict) -> str:
    """Backs the worker-facing ``customer_economics`` tool: JSON the worker can quote from."""
    return json.dumps(customer_economics(args or {}), ensure_ascii=False, indent=1)
