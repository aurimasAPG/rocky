import json

import pytest

from rocky.customer_econ import customer_economics, customer_economics_tool
from rocky.tools import ToolError

# price 50, cost 20 → 30 per purchase; 4 purchases a year → 120 per year; 25 % annual churn.
BASE = {"price": 50, "variable_cost": 20, "purchases_per_period": 4, "period": "year", "annual_churn_pct": 25, "cac": 90}


# ---------------------------------------------------------------- per customer + decision


def test_clv_over_a_three_year_horizon_against_cac():
    out = customer_economics(BASE)
    pc = out["per_customer"]
    assert pc["contribution_per_purchase"] == 30.0 and pc["margin_pct"] == 60.0
    assert pc["contribution_per_period"] == 120.0
    # 1 / 0.25 = 4 years, but the default horizon for years is 3, so the lifetime is capped and said so.
    assert pc["expected_lifetime_uncapped_periods"] == 4.0
    assert pc["expected_lifetime_periods"] == 3.0 and pc["lifetime_capped_at_horizon"] is True
    assert any("3-year horizon" in note for note in pc["notes"])
    # 120 × (1 + 0.75 + 0.5625) = 277.5
    assert pc["clv_contribution"] == 277.5
    assert pc["clv_to_cac_ratio"] == 3.08  # 277.5 / 90
    assert pc["payback_periods"] == 1  # 120 ≥ 90 in year 1
    assert out["decision"]["verdict"] == "acquire"
    assert out["decision"]["max_cac_at_target_ratio"] == 92.5  # 277.5 / 3
    assert out["missing"] == []
    assert "277.50" in out["summary"] and "acquire" in out["summary"]


def test_horizon_and_churn_per_period_are_honoured():
    # 30/month, 10 % churn, 3 months: 30 × (1 + 0.9 + 0.81) = 81.3
    out = customer_economics({"price": 30, "margin_pct": 100, "churn_pct_per_period": 10, "horizon_periods": 3})
    assert out["per_customer"]["clv_contribution"] == 81.3
    assert out["per_customer"]["expected_lifetime_periods"] == 3.0  # 1 / 0.1 = 10 > 3


def test_periods_retained_and_service_cost():
    # periods_retained 2 → churn 50 %; contribution 100 - 10 service = 90; 36 months: 90 × (1 + 0.5 + 0.25 + ...) → 180 (geometric sum, horizon long)
    out = customer_economics({"arpu": 200, "gross_margin_pct": 50, "periods_retained": 2, "service_cost_per_customer_per_period": 10})
    pc = out["per_customer"]
    assert pc["contribution_per_period"] == 90.0 and pc["churn_pct_per_period"] == 50.0
    assert pc["clv_contribution"] == 180.0
    assert pc["expected_lifetime_periods"] == 2.0 and pc["lifetime_capped_at_horizon"] is False


def test_annual_churn_is_converted_for_monthly_periods():
    out = customer_economics({"arpu": 10, "gross_margin_pct": 100, "annual_churn_pct": 25})
    # 1 - 0.75^(1/12) = 2.37 % per month
    assert out["per_customer"]["churn_pct_per_period"] == 2.4
    assert any("converted" in a for a in out["assumptions"])


def test_discounting_lowers_clv():
    plain = customer_economics(BASE)["per_customer"]["clv_contribution"]
    # 120 × (1 + 0.75/1.1 + 0.5625/1.21) = 120 × (1 + 0.6818 + 0.4649) = 257.6
    discounted = customer_economics(BASE | {"discount_rate_pct_per_year": 10})["per_customer"]["clv_contribution"]
    assert discounted == 257.6 and discounted < plain


def test_marginal_and_do_not_acquire_verdicts():
    marginal = customer_economics(BASE | {"cac": 200})  # 277.5 / 200 = 1.39: covers CAC, misses 3×
    assert marginal["decision"]["verdict"] == "marginal"
    no = customer_economics(BASE | {"cac": 400})  # 277.5 / 400 = 0.69
    assert no["decision"]["verdict"] == "do not acquire at this CAC"
    assert no["per_customer"]["payback_periods"] is None
    assert any("never reaches" in n for n in no["per_customer"]["notes"])


def test_cac_from_spend_and_customers_and_payback_year_two():
    # 9 000 / 50 = 180 CAC; year 1 gives 120 < 180, year 2 cumulative 210 ≥ 180
    out = customer_economics(BASE | {"cac": None, "acquisition_spend": 9000, "new_customers": 50})
    assert out["per_customer"]["cac"] == 180.0
    assert out["per_customer"]["payback_periods"] == 2


def test_missing_cac_still_gives_the_ceiling():
    out = customer_economics({k: v for k, v in BASE.items() if k != "cac"})
    assert out["decision"]["verdict"] is None
    assert out["decision"]["max_cac_at_target_ratio"] == 92.5
    assert "cac" in out["decision"]["missing"][0]
    assert "92.50" in out["summary"]


def test_max_churn_for_payback_within_target_is_back_solved():
    # 120/year, CAC 200, payback within 2 years: 120 + 120 × (1 - c) ≥ 200 → c ≤ 1/3
    out = customer_economics(BASE | {"cac": 200, "payback_target_periods": 2})
    assert out["decision"]["max_churn_pct_for_payback_within"] == 33.3
    # 30/month, CAC 500: even 12 months at zero churn give 360 < 500
    out = customer_economics({"price": 50, "variable_cost": 20, "churn_pct_per_period": 5, "cac": 500, "payback_target_periods": 12})
    assert out["decision"]["max_churn_pct_for_payback_within"] is None
    assert "zero churn" in out["decision"]["payback_note"]


def test_missing_cost_gives_a_conditional_table_not_a_guess():
    out = customer_economics({k: v for k, v in BASE.items() if k != "variable_cost"})
    pc = out["per_customer"]
    assert pc["missing"] == ["variable_cost or margin_pct"]
    assert "clv_contribution" not in pc
    rows = pc["conditional"]
    assert [r["assumed_margin_pct"] for r in rows] == [20.0, 40.0, 60.0, 80.0]
    # 40 % margin: 200 × 0.4 = 80/year; 80 × 2.3125 = 185; 185 / 90 = 2.06 → marginal
    assert rows[1]["contribution_per_period"] == 80.0 and rows[1]["clv_contribution"] == 185.0
    assert rows[1]["verdict"] == "marginal" and rows[2]["verdict"] == "acquire"
    assert out["missing"] == ["per_customer: variable_cost or margin_pct"]
    assert "decision" not in out


def test_missing_retention_gives_a_conditional_table_per_churn():
    out = customer_economics({"price": 50, "variable_cost": 20, "cac": 90})
    pc = out["per_customer"]
    assert pc["missing"] == ["retention (churn_pct_per_period, periods_retained or annual_churn_pct)"]
    assert [r["assumed_churn_pct_per_period"] for r in pc["conditional"]] == [5.0, 10.0, 25.0, 50.0]


# ---------------------------------------------------------------- returns


def test_returns_change_the_decision():
    out = customer_economics(BASE | {"return_rate_pct": 30, "return_cost_per_return": 5})
    r = out["returns"]
    # revenue kept 50 × 0.7 = 35; cost 20 + 0.3 × 5 = 21.5; net 13.5 per order; × 4 = 54/year
    assert r["net_contribution_per_order"] == 13.5 and r["net_contribution_per_period"] == 54.0
    assert r["effective_cac_per_kept_order"] == 128.57  # 90 / 0.7
    # 54 × 2.3125 = 124.875 → 124.88; / 90 = 1.39
    assert r["clv_contribution_after_returns"] == 124.88 and r["clv_to_cac_ratio_after_returns"] == 1.39
    assert r["verdict_before_returns"] == "acquire" and r["verdict_after_returns"] == "marginal"
    assert r["decision_changes"] is True
    assert "changes from acquire to marginal" in r["summary"]


def test_returns_with_exchanges_and_missing_handling_cost():
    # 20 % returns, half refunded, exchange 4: revenue kept 50 × 0.9 = 45; cost 20 + 0.2 × 0.5 × 4 = 20.4; net 24.6
    out = customer_economics(BASE | {"return_rate_pct": 20, "refund_share_pct": 50, "exchange_cost": 4})
    r = out["returns"]
    assert r["net_contribution_per_order"] == 24.6
    assert r["missing"] == ["return_cost_per_return"]
    assert "returns: return_cost_per_return" in out["missing"]


# ---------------------------------------------------------------- category ceiling


def test_category_ceiling_warns_when_the_plan_exceeds_the_wallet():
    # 300 × 50 % = 150 obtainable; stated revenue 200 exceeds it; implied contribution 150 × 60 % = 90
    out = customer_economics(BASE | {"category_spend_per_buyer_per_period": 300, "share_of_wallet_pct": 50})
    c = out["category_ceiling"]
    assert c["obtainable_revenue_per_period"] == 150.0 and c["implied_contribution_per_period"] == 90.0
    assert c["stated_revenue_exceeds_ceiling"] is True and "exceeds" in c["warning"]
    assert c["clv_at_ceiling"] == 208.12  # 90 × 2.3125 = 208.125, rounded half-even
    partial = customer_economics(BASE | {"category_spend_per_buyer_per_period": 300})
    assert partial["category_ceiling"]["missing"] == ["share_of_wallet_pct"]


# ---------------------------------------------------------------- funding


def test_price_cut_as_acquisition_funding():
    out = customer_economics(BASE | {"customers": 1000, "new_price": 45})
    f = out["funding"]
    assert f["contribution_pool_current"] == 120000.0  # 1000 × 120
    assert f["new_contribution_per_period"] == 100.0  # (45 - 20) × 4
    assert f["contribution_pool_new_same_volume"] == 100000.0
    assert f["units_tolerable"] == 1200.0 and f["extra_customers_needed"] == 200.0
    assert f["pool_drop_as_customers_at_cac"] == 222.2  # 20 000 / 90
    pct = customer_economics(BASE | {"customers": 1000, "acquisition_price_cut_pct": 10})["funding"]
    assert pct["new_price"] == 45.0 and pct["units_tolerable"] == 1200.0
    partial = customer_economics(BASE | {"new_price": 45})
    assert partial["funding"]["missing"] == ["customers"]


# ---------------------------------------------------------------- follow-on


def test_follow_on_break_even_attach_rate():
    out = customer_economics(
        {"entry_price": 0, "entry_variable_cost": 10, "follow_on": {"price": 30, "variable_cost": 10, "attach_rate_pct": 20, "periods": 6}}
    )
    f = out["follow_on"]
    assert f["subsidy_per_entrant"] == 10.0
    assert f["follow_on_contribution_per_attached_buyer"] == 120.0  # 20 × 6, no churn given
    assert f["expected_follow_on_per_entrant"] == 24.0 and f["net_per_entrant"] == 14.0
    assert f["breakeven_attach_rate_pct"] == 8.3  # 10 / 120
    assert f["verdict"] == "pays"


def test_follow_on_with_churn_items_and_missing_attach_rate():
    # items 2 + 3 = 5 per user; entry free → subsidy 5; follow-on 10/period over 3 periods at 50 % churn: 10 × (1 + 0.5 + 0.25) = 17.5
    out = customer_economics(
        {
            "entry_price": 0,
            "variable_cost_items": {"hosting": 2, "support": 3},
            "users": 100,
            "follow_on": {"price": 15, "variable_cost": 5, "periods": 3, "churn_pct_per_period": 50},
        }
    )
    f = out["follow_on"]
    assert f["marginal_cost_per_user_per_period"] == 5.0 and f["serving_cost_per_period"] == 500.0
    assert f["follow_on_contribution_per_attached_buyer"] == 17.5
    assert f["breakeven_attach_rate_pct"] == 28.6  # 5 / 17.5
    assert "net_per_entrant" not in f
    assert f["missing"] and "attach_rate_pct" in f["missing"][0]


# ---------------------------------------------------------------- subscription rollout


SUB = {"arpu": 20, "gross_margin_pct": 50, "churn_pct_per_period": 5, "cac": 100, "subscribers_now": 1000, "target_subscribers": 2000}


def test_rollout_reaches_the_target():
    out = customer_economics(SUB | {"new_per_period": 200})
    r = out["subscription_rollout"]
    # 1000 → 1150 → 1292.5 → 1427.9 → 1556.5 → 1678.7 → 1794.7 → 1905.0 → 2009.7
    assert r["table"][0] == {"period": 1, "subscribers": 1150.0, "churned": 50.0, "added": 200.0, "cumulative_acquisition_spend": 20000.0, "cumulative_contribution_all": 11500.0, "cumulative_contribution_new": 2000.0}
    assert r["periods_to_target"] == 8 and r["reachable"] is True
    assert r["steady_state_subscribers"] == 4000.0
    assert len(r["table"]) == 36
    assert r["cumulative_acquisition_spend"] == 720000.0  # 36 × 200 × 100
    assert r["cumulative_payback_period"] == 30
    assert "reached in month 8" in r["summary"]


def test_rollout_unreachable_at_this_churn_gives_the_fix():
    out = customer_economics(SUB | {"new_per_period": 80})
    r = out["subscription_rollout"]
    assert r["periods_to_target"] is None and r["status"] == "unreachable at this churn"
    assert r["steady_state_subscribers"] == 1600.0  # 80 / 0.05
    assert r["churn_pct_for_target_at_steady_state"] == 4.0  # 80 / 2000
    # (2000 - 1000 × 0.95^36) × 0.05 / (1 - 0.95^36) = 109.4
    assert r["new_per_period_needed_for_target_in_horizon"] == 109.4
    assert "unreachable at this churn" in r["summary"]


def test_rollout_back_solves_new_per_period_when_missing():
    out = customer_economics(SUB)
    r = out["subscription_rollout"]
    assert r["new_per_period"] == 109.4
    assert r["periods_to_target"] == 36
    assert any("back-solved" in a for a in r["assumptions"])
    partial = customer_economics({"arpu": 20, "gross_margin_pct": 50, "subscribers_now": 1000, "target_subscribers": 2000})
    assert partial["subscription_rollout"]["missing"] == ["retention (churn_pct_per_period, periods_retained or annual_churn_pct)"]


# ---------------------------------------------------------------- channels


def test_channels_ranked_with_the_secondary_flag():
    out = customer_economics(
        {"margin_pct": 40, "channels": [{"name": "A", "spend": 1000, "conversions": 100, "revenue": 5000}, {"name": "B", "spend": 500, "conversions": 10, "revenue": 1500}]}
    )
    rows = {r["name"]: r for r in out["channels"]["rows"]}
    # A: CPA 10, AOV 50, contribution 2000, net 1000, ROI 100 %, 90.9 % of conversions
    assert rows["A"]["cpa"] == 10.0 and rows["A"]["aov"] == 50.0 and rows["A"]["net_after_spend"] == 1000.0 and rows["A"]["roi_pct"] == 100.0
    # B: CPA 50, AOV 150, contribution 600, net 100, ROI 20 %, 9.1 % of conversions → profitable but secondary
    assert rows["B"]["net_after_spend"] == 100.0 and rows["B"]["roi_pct"] == 20.0 and rows["B"]["share_of_conversions_pct"] == 9.1
    assert rows["A"]["profitable_but_secondary"] is False and rows["B"]["profitable_but_secondary"] is True
    assert rows["A"]["rank"] == 1 and rows["B"]["rank"] == 2
    assert out["channels"]["profitable_but_secondary"] == ["B"]
    assert "secondary" in out["channels"]["summary"]


def test_channels_derive_revenue_from_price_and_flag_losers():
    out = customer_economics(
        {"price": 50, "variable_cost": 30, "channels": [{"name": "search", "spend": 300, "conversions": 20}, {"name": "display", "spend": 300, "conversions": 5}]}
    )
    rows = {r["name"]: r for r in out["channels"]["rows"]}
    # search: revenue 1000, contribution 400, net 100; display: revenue 250, contribution 100, net -200
    assert rows["search"]["revenue"] == 1000.0 and rows["search"]["net_after_spend"] == 100.0
    assert rows["display"]["net_after_spend"] == -200.0
    assert out["channels"]["losing_money"] == ["display"]
    assert "per_customer" in out  # the price also gives the per-customer block


# ---------------------------------------------------------------- research value


def test_research_value_positive_recommends_the_option():
    out = customer_economics({"decision_amount": 100000, "p_wrong": 0.3, "research_options": [{"name": "survey", "cost": 5000, "p_wrong_after": 0.1}]})
    r = out["research_value"]
    assert r["expected_loss_now"] == 30000.0
    opt = r["options"][0]
    assert opt["expected_loss_after"] == 10000.0 and opt["value_of_research"] == 20000.0 and opt["net_value"] == 15000.0
    assert r["recommendation"] == "survey"


def test_research_value_negative_says_skip():
    out = customer_economics(
        {"decision_amount": 100000, "p_wrong": 0.3, "loss_if_wrong_share": 0.5, "research_options": [{"name": "panel", "cost": 12000, "p_wrong_after": 0.1}]}
    )
    r = out["research_value"]
    # 100 000 × 0.3 × 0.5 = 15 000 now; after 5 000; value 10 000; cost 12 000 → net -2 000
    assert r["expected_loss_now"] == 15000.0 and r["options"][0]["net_value"] == -2000.0
    assert r["recommendation"] == "skip research"
    assert "skip research" in out["summary"]


# ---------------------------------------------------------------- errors and wrapper


@pytest.mark.parametrize(
    "args, match",
    [
        ({"price": -1}, "price must be at least 0"),
        ({"price": "abc"}, "must be a number"),
        ({"price": 50, "arpu": 20}, "not both"),
        ({"price": 50, "variable_cost": 20, "churn_pct_per_period": 120}, "churn_pct_per_period must be at most 100"),
        ({"price": 50, "variable_cost": 20, "periods_retained": 0}, "periods_retained"),
        ({"price": 50, "period": "week"}, "period must be month or year"),
        ({"price": 50, "horizon_periods": 0}, "horizon_periods must be at least 1"),
        ({"price": 50, "acquisition_spend": 1000, "new_customers": 0}, "new_customers must be greater than zero"),
        ({"price": 50, "variable_cost": 20, "customers": 10, "acquisition_price_cut_pct": 100}, "acquisition_price_cut_pct"),
        ({"price": 50, "return_rate_pct": 130}, "return_rate_pct must be at most 100"),
        ({"price": 50, "share_of_wallet_pct": 150}, "share_of_wallet_pct must be at most 100"),
        ({"channels": [{"name": "A", "conversions": 10}]}, "A.spend is required"),
        ({"channels": []}, "channels must be a non-empty list"),
        ({"decision_amount": 100, "p_wrong": 1.5, "research_options": [{"cost": 1, "p_wrong_after": 0.1}]}, "p_wrong must be between 0 and 1"),
        ({"decision_amount": 100, "p_wrong": 0.5, "research_options": [{"name": "x", "cost": 1}]}, "x.p_wrong_after is required"),
        ({"entry_price": 0, "entry_variable_cost": 1, "follow_on": "yes"}, "follow_on must be an object"),
        ({"entry_price": 0, "variable_cost_items": [1, 2]}, "variable_cost_items must be an object"),
        ({"target_clv_cac_ratio": 3}, "nothing to compute"),
    ],
)
def test_contradictory_or_impossible_inputs_raise(args, match):
    with pytest.raises(ToolError, match=match):
        customer_economics(args)


def test_cost_above_price_is_a_warning_not_an_error():
    out = customer_economics({"price": 10, "variable_cost": 12, "churn_pct_per_period": 10, "cac": 5})
    assert any("loses money" in w for w in out["warnings"])
    assert out["per_customer"]["payback_periods"] is None


def test_lenient_number_parsing_and_period_default():
    out = customer_economics({"price": "50 €", "variable_cost": "20,00", "annual_churn_pct": "25 %", "cac": "90", "period": "Year"})
    assert out["period"] == "year"
    # purchases_per_period defaulted to 1: 30 × (1 + 0.75 + 0.5625) = 69.375 → 69.38 — and the assumption says so
    assert out["per_customer"]["clv_contribution"] == 69.38
    assert any("one purchase per year" in a for a in out["assumptions"])


def test_tool_wrapper_returns_json_with_every_computed_block():
    text = customer_economics_tool(BASE | {"return_rate_pct": 10, "return_cost_per_return": 2, "customers": 500})
    data = json.loads(text)
    assert data["computed"] == ["per_customer", "decision", "returns", "funding"]
    assert data["currency"] == "EUR" and data["per_customer"]["clv_contribution"] == 277.5
    assert data["returns"]["summary"] in data["summary"] and data["funding"]["summary"] in data["summary"]
    assert "×" in text  # ensure_ascii=False keeps the multiplication sign readable



def test_dispatch_routes_customer_economics_and_the_skill_names_it(tmp_path):
    import json as _json

    from rocky.skills import view_skill
    from rocky.tools import WorkspaceTools, dispatch, worker_tools

    out = _json.loads(dispatch(WorkspaceTools(tmp_path), "customer_economics", {"price": 50, "variable_cost": 20, "purchases_per_period": 4, "period": "year", "annual_churn_pct": 25, "cac": 90}))
    assert out["per_customer"]["clv_contribution"] == 277.5
    assert "customer_economics" in [t["function"]["name"] for t in worker_tools(False)]
    assert "call `customer_economics`" in view_skill("marketing-numbers")
