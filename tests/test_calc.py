import json

import pytest

from rocky.calc import unit_economics, unit_economics_tool
from rocky.engine import orchestrate
from rocky.llm import Completion, ToolCall
from rocky.tools import ToolError, WorkspaceTools, dispatch, worker_tools
from tests.test_loop import MODELS, ScriptedClient


def test_a_price_cut_says_how_many_extra_sales_it_needs():
    out = unit_economics({"price": 100, "variable_cost": 60, "units": 100, "new_price": 90})
    assert out["contribution_per_unit"] == 40 and out["margin_pct"] == 40.0
    assert out["total_contribution"] == 4000
    change = out["change"]
    assert change["new_contribution_per_unit"] == 30
    assert change["required_units"] == 134 and change["extra_units"] == 34
    assert change["required_uplift_pct"] == 33.3
    assert change["contribution_preserved"] == 4000
    assert "134 sales" in out["summary"] and "34 extra" in out["summary"]
    assert out["missing"] == []


def test_discount_pct_is_the_same_question():
    out = unit_economics({"price": "100", "variable_cost": "60", "units": "100", "discount_pct": "10"})
    assert out["change"]["required_units"] == 134


def test_an_expected_uplift_turns_the_numbers_into_a_decision():
    out = unit_economics({"price": 100, "variable_cost": 60, "units": 100, "new_price": 90, "expected_uplift_pct": 20})
    assert out["change"]["decision"] == "change loses contribution"
    assert out["change"]["contribution_delta"] == -400
    assert out["sensitivity"]["decision_flips_at_uplift_pct"] == 33.3
    # at a 20 % uplift the cut only pays if the unit cost is at most 40: (100-40) = 1.2 * (90-40)
    assert out["sensitivity"]["decision_flips_at_variable_cost"] == 40
    better = unit_economics({"price": 100, "variable_cost": 60, "units": 100, "new_price": 90, "expected_uplift_pct": 40})
    assert better["change"]["decision"] == "change pays"


def test_sensitivity_shows_the_unit_cost_assumption_moving():
    out = unit_economics({"price": 100, "variable_cost": 60, "units": 100, "new_price": 90})
    rows = out["sensitivity"]["variable_cost"]
    assert [r["variable_cost"] for r in rows] == [54, 60, 66]
    assert rows[0]["required_uplift_pct"] < rows[1]["required_uplift_pct"] < rows[2]["required_uplift_pct"]


def test_break_even_with_fixed_costs():
    out = unit_economics({"price": 25, "variable_cost": 10, "fixed_costs": 3000, "units": 250})
    assert out["break_even_units"] == 200 and out["break_even_revenue"] == 5000
    assert out["profit"] == 750 and out["safety_margin_pct"] == 20.0
    assert "Break-even at 200 units" in out["summary"]


def test_missing_variable_cost_is_named_and_answered_conditionally():
    out = unit_economics({"price": 100, "units": 100, "new_price": 90})
    assert out["missing"] == ["variable_cost"]
    assert "change" not in out
    rows = {r["assumed_margin_pct"]: r for r in out["conditional"]}
    assert set(rows) == {20.0, 40.0, 60.0, 80.0}
    assert rows[40.0]["change"]["required_units"] == 134
    assert rows[20.0]["change"]["required_units"] == 200
    assert "Ask for the unit cost" in out["summary"]


def test_without_volume_the_uplift_is_still_known():
    out = unit_economics({"price": 100, "variable_cost": 60, "new_price": 90, "expected_uplift_pct": 30})
    assert out["change"]["required_uplift_pct"] == 33.3
    assert "required_units" not in out["change"]
    assert out["change"]["decision"] == "change loses contribution"


def test_a_cut_below_cost_cannot_be_recovered():
    out = unit_economics({"price": 100, "variable_cost": 60, "units": 100, "new_price": 55})
    assert out["change"]["required_units"] is None
    assert "No volume recovers" in out["change"]["note"]


def test_bad_inputs_are_refused_in_plain_words():
    with pytest.raises(ToolError, match="price is required"):
        unit_economics({})
    with pytest.raises(ToolError, match="must be a number"):
        unit_economics({"price": "a hundred"})
    with pytest.raises(ToolError, match="discount_pct"):
        unit_economics({"price": 100, "variable_cost": 60, "discount_pct": 120})
    assert "loses money" in unit_economics({"price": 10, "variable_cost": 12})["warnings"][0]


# --- pricing extensions ------------------------------------------------------


def test_markup_sits_next_to_margin_everywhere():
    # 100 - 60 = 40 contribution: margin 40/100 = 40 %, markup 40/60 = 66.7 %; after the cut 30/90 = 33.3 %, 30/60 = 50 %
    out = unit_economics({"price": 100, "variable_cost": 60, "units": 100, "new_price": 90})
    assert out["markup_pct"] == 66.7 and out["change"]["new_markup_pct"] == 50.0
    assert "markup_note" not in out
    # the conditional table carries it too: at a 40 % margin cost is 60 → markup 66.7 %
    rows = {r["assumed_margin_pct"]: r for r in unit_economics({"price": 100})["conditional"]}
    assert rows[40.0]["markup_pct"] == 66.7
    # a zero cost has no markup: null with a note, margin still 100 %
    zero = unit_economics({"price": 10, "variable_cost": 0})
    assert zero["markup_pct"] is None and zero["margin_pct"] == 100.0 and "undefined" in zero["markup_note"]


def test_margin_or_markup_can_stand_in_for_the_unit_cost():
    # margin 40 % of 100 → cost 60; markup 25 % on cost → cost 100 / 1.25 = 80
    by_margin = unit_economics({"price": 100, "margin_pct": 40, "units": 100, "new_price": 90})
    assert by_margin["inputs"]["variable_cost"] == 60 and by_margin["inputs"]["variable_cost_from"] == "margin_pct"
    assert by_margin["change"]["required_units"] == 134 and by_margin["missing"] == []
    assert any("derived from margin_pct" in a for a in by_margin["assumptions"])
    by_markup = unit_economics({"price": 100, "markup_pct": 25})
    assert by_markup["inputs"]["variable_cost"] == 80 and by_markup["contribution_per_unit"] == 20
    with pytest.raises(ToolError, match="margin_pct must be between 0 and 100"):
        unit_economics({"price": 100, "margin_pct": 120})


def test_realized_markup_after_markdowns_from_a_sales_mix_or_a_markdown_share():
    # list 100, cost 60: initial markup 40/60 = 66.7 %. Mix 70 units at 100 + 30 at 70 → realized (7000+2100)/100 = 91
    # realized markup 31/60 = 51.7 %, realized margin 31/91 = 34.1 %, erosion 66.7 - 51.7 = 15.0 pp
    mix = unit_economics({"price": 100, "variable_cost": 60, "sales_mix": [{"price": 100, "units": 70}, {"price": 70, "units": 30}]})["realized"]
    assert mix["realized_price"] == 91 and mix["initial_markup_pct"] == 66.7 and mix["realized_markup_pct"] == 51.7
    assert mix["realized_margin_pct"] == 34.1 and mix["markup_erosion_pp"] == 15.0 and mix["full_price_share_pct"] == 70.0
    # the same picture as a markdown: 30 % off on 30 % of units → 100 × (1 - 0.3 × 0.3) = 91
    md = unit_economics({"price": 100, "variable_cost": 60, "markdown_pct": 30, "markdown_share_pct": 30})["realized"]
    assert md["realized_price"] == 91 and md["markup_erosion_pp"] == 15.0
    assert "15 pp lost to markdowns" in md["summary"]
    with pytest.raises(ToolError, match="go together"):
        unit_economics({"price": 100, "variable_cost": 60, "markdown_pct": 30})
    with pytest.raises(ToolError, match=r"sales_mix\[1\]: units is required"):
        unit_economics({"price": 100, "variable_cost": 60, "sales_mix": [{"price": 100, "units": 70}, {"price": 70}]})
    # without a cost the block names what is missing instead of guessing
    no_cost = unit_economics({"price": 100, "markdown_pct": 30, "markdown_share_pct": 30})
    assert no_cost["realized"]["missing"] == ["variable_cost"] and "realized_price" not in no_cost["realized"]


def test_several_plans_are_ranked_by_net_contribution_with_the_uplift_gap_named():
    # today: 100 units × (100 - 60) = 4000.
    # ten off: 30 per unit → 4000/30 = 133.3 → 134 units (+33.3 %); expected +20 % → 120 × 30 = 3600, net -400
    # gift: cost 65 → 35 per unit, promo 200 → (4000+200)/35 = 120 units (+20 %); expected +15 % → 115 × 35 - 200 = 3825, net -175
    # deep: 55 - 60 < 0 → nothing recovers it
    out = unit_economics(
        {
            "price": 100,
            "variable_cost": 60,
            "units": 100,
            "fixed_costs": 1000,
            "target_contribution": 6000,
            "plans": [
                {"name": "ten off", "discount_pct": 10, "expected_volume_change_pct": 20},
                {"name": "gift", "new_price": 100, "added_variable_cost": 5, "promo_cost": 200, "expected_volume_change_pct": 15},
                {"name": "deep", "new_price": 55, "expected_volume_change_pct": 80},
            ],
        }
    )
    block = out["plans"]
    ten, gift, deep = block["plans"]
    assert block["today"]["total_contribution"] == 4000
    assert ten["required_units"] == 134 and ten["required_uplift_pct"] == 33.3 and ten["markup_pct"] == 50.0
    assert ten["expected_total_contribution"] == 3600 and ten["net_vs_today"] == -400 and ten["decision"] == "plan loses contribution"
    assert gift["required_units"] == 120 and gift["expected_total_contribution"] == 3825 and gift["net_vs_today"] == -175
    # units for a 6000 target: ten off (6000+0)/30 = 200; gift (6000+200)/35 = 177.1 → 178; today 6000/40 = 150
    assert ten["units_for_target"] == 200 and gift["units_for_target"] == 178 and out["units_for_target"] == 150
    # break-even with fixed 1000: ten off 1000/30 → 34; gift (1000+200)/35 → 35
    assert ten["break_even_units"] == 34 and gift["break_even_units"] == 35
    assert deep["required_units"] is None and deep["decision"] == "plan loses contribution"
    assert block["best_plan"] == "gift"
    assert "needs +20 % volume against +15 % expected" in block["note"]
    assert block["note"] in out["summary"]


def test_a_plan_with_expected_units_pays_and_plans_without_volumes_are_not_ranked():
    # expected_units 150 at 30 per unit = 4500 > 4000 → pays, net +500
    out = unit_economics({"price": 100, "variable_cost": 60, "units": 100, "plans": [{"name": "cut", "new_price": 90, "expected_units": 150}]})
    plan = out["plans"]["plans"][0]
    assert plan["expected_total_contribution"] == 4500 and plan["net_vs_today"] == 500 and plan["decision"] == "plan pays"
    assert out["plans"]["best_plan"] == "cut" and "+500" in out["plans"]["summary"]
    # no units at all: the required uplift is still a ratio (40/30 - 1 = 33.3 %), nothing can be ranked
    bare = unit_economics({"price": 100, "variable_cost": 60, "plans": [{"name": "cut", "discount_pct": 10}]})["plans"]
    assert bare["plans"][0]["required_uplift_pct"] == 33.3 and "best_plan" not in bare and "expected_volume_change_pct" in bare["note"]
    # nested JSON that arrives as a string is accepted the way numbers-as-strings are
    as_text = unit_economics({"price": 100, "variable_cost": 60, "plans": json.dumps([{"name": "cut", "discount_pct": 10}])})
    assert as_text["plans"]["plans"][0]["new_price"] == 90
    with pytest.raises(ToolError, match=r"plans\[0\]: new_price or discount_pct is required"):
        unit_economics({"price": 100, "variable_cost": 60, "plans": [{"name": "nothing"}]})
    with pytest.raises(ToolError, match="plans must be a non-empty list"):
        unit_economics({"price": 100, "variable_cost": 60, "plans": []})


def test_a_bundle_is_judged_against_the_items_bought_separately():
    # A 100/60 (40), B 50/20 (30), bundle 130: list 150, discount 20 = 13.3 %, cost 80, contribution 50, margin 50/130 = 38.5 %
    # counterfactual per A buyer = 40 + 0.2 × 30 = 46 → incremental +4
    # break-even attach: anyway contribution 6 / (50 - 40) = 60 %; at 70 % attach 0.7 × 50 + 0.3 × 40 = 47 ≥ 46 → pays
    out = unit_economics(
        {
            "price": 100,
            "variable_cost": 60,
            "bundle": {
                "items": [{"name": "A", "price": 100, "variable_cost": 60}, {"name": "B", "price": 50, "variable_cost": 20, "would_buy_anyway_pct": 20}],
                "bundle_price": 130,
                "expected_attach_rate_pct": 70,
            },
        }
    )["bundle"]
    assert out["list_price_sum"] == 150 and out["discount_eur"] == 20 and out["discount_pct"] == 13.3
    assert out["bundle_cost"] == 80 and out["contribution_per_bundle"] == 50 and out["bundle_margin_pct"] == 38.5
    assert out["baseline_item"] == "A" and out["counterfactual_contribution"] == 46 and out["incremental_contribution_vs_counterfactual"] == 4
    assert out["breakeven_attach_rate_pct"] == 60.0
    assert out["expected_contribution_per_baseline_buyer"] == 47 and out["decision"] == "bundle pays"
    assert "60 % of A buyers" in out["summary"]


def test_a_bundle_that_never_pays_and_a_bundle_with_a_missing_cost():
    # 80 % of A buyers would buy B anyway: counterfactual 40 + 24 = 64 > 50 even at 100 % attach → no break-even
    never = unit_economics(
        {
            "price": 100,
            "bundle": {
                "items": [{"name": "A", "price": 100, "variable_cost": 60}, {"name": "B", "price": 50, "variable_cost": 20, "would_buy_anyway_pct": 80}],
                "bundle_price": 130,
                "baseline_item": "A",
            },
        }
    )["bundle"]
    assert never["breakeven_attach_rate_pct"] is None and "not recovered" in never["note"]
    # a missing item cost stops at the discount and says so
    partial = unit_economics({"price": 100, "bundle": {"items": [{"name": "A", "price": 100, "variable_cost": 60}, {"name": "B", "price": 50}], "bundle_price": 130}})["bundle"]
    assert partial["missing"] == ["bundle.items[1].variable_cost"] and partial["discount_pct"] == 13.3 and "contribution_per_bundle" not in partial
    with pytest.raises(ToolError, match="baseline_item 'C' is not one of the items"):
        unit_economics({"price": 100, "bundle": {"items": [{"name": "A", "price": 100}, {"name": "B", "price": 50}], "bundle_price": 130, "baseline_item": "C"}})
    with pytest.raises(ToolError, match="at least two"):
        unit_economics({"price": 100, "bundle": {"items": [{"name": "A", "price": 100}], "bundle_price": 90}})


def test_variants_blend_against_the_baseline_and_give_the_cannibalization_break_even():
    # baseline 100/60 (40). premium 150, cost 70 → 80 (20 % share); basic 80, cost 60 → 20 (30 % share); 50 % stay
    # blended price 0.2×150 + 0.3×80 + 0.5×100 = 104; blended contribution 16 + 6 + 20 = 42 → +2 per unit, +2000 on 1000 units
    # premium gain 0.2 × 40 = 8 per buyer; each switcher to basic loses 20 → 8/20 = 40 % may switch
    out = unit_economics(
        {
            "price": 100,
            "variable_cost": 60,
            "units": 1000,
            "variants": [
                {"name": "premium", "price": 150, "share_pct": 20, "extra_variable_cost": 10},
                {"name": "basic", "price": 80, "share_pct": 30, "effort_note": "self pickup, 25-min round trip"},
            ],
        }
    )
    block = out["variants"]
    premium, basic = block["variants"]
    assert premium["contribution_per_unit"] == 80 and premium["margin_pct"] == 53.3 and premium["markup_pct"] == 114.3 and premium["tier"] == "premium"
    assert basic["contribution_per_unit"] == 20 and basic["tier"] == "cheaper" and basic["effort_note"] == "self pickup, 25-min round trip"
    # one switcher to basic (-20) is covered by one new basic buyer (+20)
    assert basic["new_buyers_per_switcher"] == 1.0
    assert block["baseline"]["share_pct"] == 50.0 and block["blended_price"] == 104 and block["blended_contribution"] == 42
    assert block["gain_per_unit_vs_baseline"] == 2 and block["gain_vs_today"] == 2000
    assert block["cannibalization_breakeven_share_pct"] == 40.0
    assert "warnings" not in out


def test_variant_shares_above_100_are_warned_and_a_free_tier_is_allowed():
    over = unit_economics({"price": 100, "variable_cost": 60, "variants": [{"name": "a", "price": 120, "share_pct": 70}, {"name": "b", "price": 110, "share_pct": 50}]})
    assert any("above 100" in w for w in over["warnings"]) and over["variants"]["baseline"]["share_pct"] == 0.0
    assert over["variants"]["cannibalization_breakeven_share_pct"] is None
    # free tier: price 0 is accepted with variants, margin is undefined, no "greater than zero" error
    free = unit_economics({"price": 0, "variable_cost": 0.5, "variants": [{"name": "pro", "price": 10, "share_pct": 16, "variable_cost": 1}]})
    assert free["contribution_per_unit"] == -0.5 and free["margin_pct"] is None
    # blended: 0.16 × 9 + 0.84 × (-0.5) = 1.44 - 0.42 = 1.02 per user
    assert free["variants"]["blended_contribution"] == 1.02 and any("free tier" in w for w in free["warnings"])
    with pytest.raises(ToolError, match="price must be greater than zero"):
        unit_economics({"price": 0, "variable_cost": 0.5})
    with pytest.raises(ToolError, match=r"variants\[0\]: share_pct is required"):
        unit_economics({"price": 100, "variable_cost": 60, "variants": [{"name": "a", "price": 120}]})


def test_the_price_builds_up_through_the_chain_and_back_solves_to_a_shelf_target():
    # production 4 → distributor adds 0.5 and 20 % on cost: (4 + 0.5) × 1.2 = 5.4 (margin 0.9)
    # retailer 40 % of price: 5.4 / 0.6 = 9.0 (margin 3.6); VAT 21 % → 10.89 gross; producer share 4/9 = 44.4 %
    # target 9.99 gross → 8.2562 net → ×0.6 = 4.9537 → /1.2 - 0.5 = 3.628 → 3.63 production cost
    out = unit_economics(
        {
            "price": 10,
            "chain": {
                "production_cost": 4,
                "stages": [
                    {"name": "distributor", "added_cost": 0.5, "margin_pct": 20, "basis": "on_cost"},
                    {"name": "retailer", "margin_pct": 40, "basis": "of_price"},
                ],
                "vat_pct": 21,
                "target_shelf_price": 9.99,
            },
        }
    )["chain"]
    dist, retail = out["stages"]
    assert (dist["buy_price"], dist["added_cost"], dist["margin_eur"], dist["sell_price"]) == (4, 0.5, 0.9, 5.4)
    assert (retail["buy_price"], retail["margin_eur"], retail["sell_price"]) == (5.4, 3.6, 9.0)
    assert out["shelf_price_net"] == 9.0 and out["shelf_price_gross"] == 10.89 and out["producer_share_pct"] == 44.4
    assert out["target"]["verdict"] == "overshoots" and out["target"]["gap"] == 0.9 and out["target"]["production_cost_for_target"] == 3.63
    assert "3.63 would hit it" in out["summary"]


def test_a_chain_without_vat_names_it_and_bad_stages_are_refused():
    out = unit_economics({"price": 10, "chain": {"production_cost": 5, "stages": [{"name": "shop", "margin_pct": 50}]}})["chain"]
    # of_price is the default: 5 / 0.5 = 10 net, gross unknown
    assert out["shelf_price_net"] == 10 and out["shelf_price_gross"] is None and out["missing"] == ["vat_pct"]
    # a target no production cost can reach: added costs alone are above it
    low = unit_economics({"price": 10, "chain": {"production_cost": 5, "stages": [{"name": "shop", "added_cost": 3, "margin_pct": 50}], "target_shelf_price": 4}})["chain"]
    assert low["target"]["production_cost_for_target"] is None and low["target"]["verdict"] == "overshoots"
    with pytest.raises(ToolError, match=r"chain.stages\[0\]: basis must be of_price"):
        unit_economics({"price": 10, "chain": {"production_cost": 5, "stages": [{"name": "shop", "margin_pct": 50, "basis": "percent"}]}})
    with pytest.raises(ToolError, match="of_price must be below 100"):
        unit_economics({"price": 10, "chain": {"production_cost": 5, "stages": [{"name": "shop", "margin_pct": 100}]}})
    with pytest.raises(ToolError, match="chain: production_cost is required"):
        unit_economics({"price": 10, "chain": {"stages": [{"name": "shop", "margin_pct": 50}]}})


def test_obligations_move_break_even_and_give_the_payback_of_a_one_off():
    # price 25, cost 10, fixed 3000, 250 units: base break-even 3000/15 = 200
    # obligations: licence 1200 per period, setup 6000 one-off, royalty 2 per unit
    # fixed_total 4200, variable_total 12, contribution 13 → 4200/13 = 323.1 → 324 units
    # surplus at 250 units = 13 × 250 - 4200 = -950 → the one-off never pays back
    out = unit_economics(
        {
            "price": 25,
            "variable_cost": 10,
            "fixed_costs": 3000,
            "units": 250,
            "obligations": [{"name": "licence", "per_period": 1200}, {"name": "setup", "one_off": 6000}, {"name": "royalty", "per_unit": 2}],
        }
    )["obligations"]
    assert out["fixed_total"] == 4200 and out["variable_total"] == 12 and out["marginal_cost_per_unit"] == 12
    assert out["break_even_units_base"] == 200 and out["break_even_units_with_obligations"] == 324
    assert out["period_surplus"] == -950 and out["payback_periods_for_one_off"] is None and "never pays back" in out["payback_note"]
    # at 500 units the surplus is 13 × 500 - 4200 = 2300 → 6000 / 2300 = 2.6 periods
    more = unit_economics(
        {"price": 25, "variable_cost": 10, "fixed_costs": 3000, "units": 500, "obligations": [{"name": "licence", "per_period": 1200}, {"name": "setup", "one_off": 6000}, {"name": "royalty", "per_unit": 2}]}
    )["obligations"]
    assert more["period_surplus"] == 2300 and more["payback_periods_for_one_off"] == 2.6 and "2.6 periods" in more["summary"]


def test_a_free_tier_is_priced_from_its_obligations_and_empty_obligations_are_refused():
    # price 0, 0.5 per user, hosting 300 per period, support 0.2 per user, 1000 users:
    # marginal cost 0.7, serving cost 0.7 × 1000 + 300 = 1000 per period
    out = unit_economics({"price": 0, "variable_cost": 0.5, "units": 1000, "obligations": [{"name": "hosting", "per_period": 300}, {"name": "support", "per_unit": 0.2}]})
    assert out["margin_pct"] is None and out["contribution_per_unit"] == -0.5
    assert out["obligations"]["marginal_cost_per_unit"] == 0.7 and out["obligations"]["serving_cost_per_period"] == 1000
    assert out["obligations"]["break_even_units_with_obligations"] is None
    assert any("free tier" in w for w in out["warnings"]) and "error" not in out
    # no unit cost on the free tier: taken as 0 but said under missing, serving cost comes from per_unit only
    bare = unit_economics({"price": 0, "obligations": [{"name": "support", "per_unit": 0.2}]})
    assert bare["missing"] == ["variable_cost"] and bare["obligations"]["marginal_cost_per_unit"] == 0.2 and bare["obligations"]["missing"] == ["units"]
    with pytest.raises(ToolError, match=r"obligations\[0\]: give one_off, per_period or per_unit"):
        unit_economics({"price": 25, "variable_cost": 10, "obligations": [{"name": "nothing"}]})
    with pytest.raises(ToolError, match="cannot replace variable_cost when price is 0"):
        unit_economics({"price": 0, "margin_pct": 40, "obligations": [{"name": "support", "per_unit": 0.2}]})


def test_blocks_that_need_the_unit_cost_say_so_instead_of_guessing():
    out = unit_economics({"price": 100, "units": 100, "plans": [{"name": "cut", "discount_pct": 10}], "obligations": [{"name": "licence", "per_period": 100}]})
    assert out["missing"] == ["variable_cost"] and "conditional" in out
    assert out["plans"]["missing"] == ["variable_cost"] and out["obligations"]["missing"] == ["variable_cost"]
    assert "needs variable_cost" in out["summary"]


def test_dispatch_routes_the_tool_and_returns_json():
    out = dispatch(WorkspaceTools("."), "unit_economics", {"price": 100, "variable_cost": 60, "units": 100, "new_price": 90})
    assert json.loads(out)["change"]["required_units"] == 134
    assert "unit_economics" in [t["function"]["name"] for t in worker_tools(False)]


class PricingClient(ScriptedClient):
    """Worker that answers a pricing question only from what the calculator returns."""

    def __init__(self, ask_missing=False):
        super().__init__()
        self.ask_missing = ask_missing
        self.tool_args = None
        self.clarified = None

    def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
        system = messages[0]["content"] if messages else ""
        if "You assess tasks" in system:
            return Completion(
                content=json.dumps(
                    {
                        "goal": "Extra sales needed after a price cut",
                        "complexity": "trivial",
                        "domain": "marketing",
                        "success_criteria": ["answer.md states the extra units"],
                        "needs_web": False,
                        "deliverable": "answer.md",
                    }
                )
            )
        if "You execute this subtask" in system:
            self.worker_round += 1
            last = messages[-1]
            if last.get("role") == "tool":
                if last["content"].startswith("wrote "):
                    return Completion(content="done")
                payload = json.loads(last["content"]) if last["content"].startswith("{") else {}
                if payload.get("missing") and self.ask_missing and self.clarified is None:
                    self.clarified = "pending"
                    return Completion(
                        content="",
                        tool_calls=[
                            ToolCall(id="q", name="clarify", arguments={"question": "What is the variable cost per unit?"})
                        ],
                    )
                if last["content"].startswith("user:"):
                    self.clarified = last["content"]
                    return Completion(content="", tool_calls=[ToolCall(id="c2", name="unit_economics", arguments={"price": 100, "variable_cost": 60, "units": 100, "new_price": 90})])
                change = payload.get("change") or {}
                text = f"{change.get('extra_units')} extra sales, {change.get('required_units')} total, keeping {change.get('contribution_preserved')} EUR."
                return Completion(
                    content="",
                    tool_calls=[ToolCall(id="w", name="write_file", arguments={"path": "deliverables/answer.md", "content": text + "\n"})],
                )
            if self.worker_round == 1:
                args = {"price": 100, "units": 100, "new_price": 90}
                if not self.ask_missing:
                    args["variable_cost"] = 60
                self.tool_args = args
                return Completion(content="", tool_calls=[ToolCall(id="c1", name="unit_economics", arguments=args)])
            return Completion(content="done")
        return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)


def test_a_worker_answers_the_pricing_brief_from_the_calculator(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    events = []
    client = PricingClient()
    result = orchestrate(
        goal="We sell 100 units at €100 each. Variable cost is €60 per unit. If we cut the price to €90, how many extra sales do we need?",
        workspace=tmp_path,
        client=client,
        models=MODELS,
        emit=events.append,
        max_loops=1,
        budget_eur=1.0,
        eur_rate=0.92,
    )
    assert result.passed is True
    calls = [e.payload for e in events if e.type == "tool_call" and e.payload["name"] == "unit_economics"]
    assert calls and calls[0]["arguments"] == {"price": "100", "units": "100", "new_price": "90", "variable_cost": "60"}
    # the figures in the deliverable are the tool's, not the model's
    assert (tmp_path / "deliverables" / "answer.md").read_text() == "34 extra sales, 134 total, keeping 4000.0 EUR.\n"


def test_a_worker_asks_for_the_missing_unit_cost_before_answering(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    events = []
    client = PricingClient(ask_missing=True)
    questions = []

    def ask_user(question, options=None):
        questions.append(question)
        return "60"

    result = orchestrate(
        goal="We sell 100 units at €100 each. If we cut the price to €90, how many extra sales do we need?",
        workspace=tmp_path,
        client=client,
        models=MODELS,
        emit=events.append,
        max_loops=1,
        budget_eur=1.0,
        eur_rate=0.92,
        ask_user=ask_user,
    )
    assert result.passed is True
    names = [e.payload["name"] for e in events if e.type == "tool_call"]
    assert names == ["unit_economics", "clarify", "unit_economics", "write_file"]
    assert questions == ["What is the variable cost per unit?"]
    first = next(e.payload["result"] for e in events if e.type == "tool_result" and e.payload["name"] == "unit_economics")
    assert '"missing": [\n  "variable_cost"' in first or '"variable_cost"' in json.loads(first)["missing"]
    assert (tmp_path / "deliverables" / "answer.md").read_text().startswith("34 extra sales, 134 total")


def test_the_worker_prompt_sends_arithmetic_to_the_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    seen = []

    class Recording(ScriptedClient):
        def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
            seen.append(messages[0]["content"])
            return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)

    orchestrate(goal="Write a short article", workspace=tmp_path, client=Recording(), models=MODELS, max_loops=1, budget_eur=1.0, eur_rate=0.92)
    worker = next(s for s in seen if "You execute this subtask" in s)
    assert "unit_economics" in worker and "never do that arithmetic in prose" in worker



def test_the_tool_spec_advertises_the_pricing_blocks():
    spec = next(t for t in worker_tools(False) if t["function"]["name"] == "unit_economics")
    props = spec["function"]["parameters"]["properties"]
    for key in ("plans", "bundle", "variants", "chain", "obligations", "sales_mix", "markup_pct"):
        assert key in props, key
