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


# --- capacity, ownership, offers, line, retail, media_value, routes ---------------


def test_capacity_values_unsold_slots_and_the_shift_break_even():
    # salon: daytime 30 slots at 40 % → 12 occupied, 18 unsold × 45 = 810 at list.
    # off-peak 33.75: fills contribute 33.75 - 8 = 25.75; a shifted regular costs 45 - 33.75 = 11.25;
    # break-even shifted share 25.75 / (25.75 + 11.25) = 69.6 %; all 18 new → 18 × 25.75 = 463.50; max discount (45-8)/45 = 82.2 %
    out = unit_economics({"price": 45, "variable_cost": 8, "capacity": {"period": "day", "bands": [{"band": "daytime", "capacity": 30, "occupancy_pct": 40}], "offpeak_price": 33.75}})["capacity"]
    band = out["bands"][0]
    assert band["occupied"] == 12 and band["unsold_units"] == 18 and band["unsold_value_at_list"] == 810 and band["max_gain_per_period"] == 463.5
    assert out["offer"]["contribution_per_filled_slot"] == 25.75 and out["offer"]["loss_per_shifted_booking"] == 11.25
    assert out["offer"]["shift_break_even_pct"] == 69.6 and out["break_even_discount_pct"] == 82.2
    assert "under 69.6 % of off-peak bookings" in out["summary"]


def test_capacity_names_missing_occupancy_and_refuses_contradictions():
    out = unit_economics({"price": 45, "variable_cost": 8, "capacity": {"capacity_units": 60}})["capacity"]
    assert out["missing"] == ["capacity.bands[0].occupied", "offpeak_price"]
    assert [r["assumed_occupancy_pct"] for r in out["bands"][0]["conditional"]] == [30.0, 50.0, 70.0]
    assert out["bands"][0]["conditional"][1]["unsold_units"] == 30
    with pytest.raises(ToolError, match="must be below the list price"):
        unit_economics({"price": 45, "variable_cost": 8, "capacity": {"capacity_units": 60, "occupied_units": 30, "offpeak_price": 50}})
    with pytest.raises(ToolError, match="more than capacity"):
        unit_economics({"price": 45, "variable_cost": 8, "capacity": {"capacity_units": 60, "occupied_units": 70}})
    below = unit_economics({"price": 45, "variable_cost": 8, "capacity": {"capacity_units": 60, "occupied_units": 30, "offpeak_price": 7}})["capacity"]
    assert below["offer"]["shift_break_even_pct"] is None and "no fill is profitable" in below["offer"]["note"]
    # without a cost the block says so instead of guessing
    assert unit_economics({"price": 45, "capacity": {"capacity_units": 60, "occupied_units": 30}})["capacity"]["missing"] == ["variable_cost"]


def test_ownership_total_cost_consumable_share_and_crossover():
    # 89 + 2 cups × 1095 days × 0.39 = 89 + 854.10 = 943.10 (90.6 % consumables); competitor 149 + 2190 × 0.29 = 784.10
    # lines cross when 89 + 0.78 t = 149 + 0.58 t → t = 300 days; tie usage over 3 years: 60 / (0.10 × 1095) = 0.55 cups/day
    out = unit_economics({"ownership": {"base_price": 89, "consumable_price": 0.39, "consumable_units_per_period": 2, "periods": 1095, "period_label": "day", "alternative": {"name": "competitor", "base_price": 149, "consumable_price": 0.29}}})
    own = out["ownership"]
    assert out["inputs"]["price"] is None and "price was not given" in out["note"]
    assert own["consumable_cost"] == 854.1 and own["total_ownership_cost"] == 943.1 and own["consumable_share_pct"] == 90.6
    assert own["alternative"]["total_ownership_cost"] == 784.1 and own["alternative"]["difference"] == 159 and own["alternative"]["cheaper"] == "competitor"
    assert own["alternative"]["crossover_periods"] == 300 and own["alternative"]["tie_usage_per_period"] == 0.55
    assert "cross after 300.0 days" in own["summary"]


def test_ownership_without_usage_names_it_and_never_crosses_when_one_option_dominates():
    out = unit_economics({"ownership": {"base_price": 89, "consumable_price": 0.39, "periods": 36, "alternative": {"base_price": 60, "consumable_price": 0.29}}})["ownership"]
    assert out["missing"] == ["consumable_units_per_period"] and "total_ownership_cost" not in out
    assert out["consumable_cost_per_unit_of_usage"] == 14.04 and out["alternative"]["tie_usage_per_period"] is None
    with pytest.raises(ToolError, match="consumable_units_per_period must be greater than zero"):
        unit_economics({"ownership": {"base_price": 89, "consumable_price": 0.39, "consumable_units_per_period": 0, "periods": 36}})
    with pytest.raises(ToolError, match="price is required"):
        unit_economics({})


def test_offers_compare_trial_then_bill_with_refund_and_name_the_tie_point():
    # chair 249 / cost 110 / 180 orders. trial +40 % → 252 orders, 12 % returned = 30, 4 % of the rest unpaid = 9, 213 retained:
    # 213 × 139 − 9 × 110 − 252 × 6 = 29607 − 990 − 1512 = 27105. refund +15 % → 207, 7 % = 14 returned, 193 × 139 − 14 × 18 = 26575.
    # no offer 180 × 139 = 25020. trial ties refund when its return rate reaches (129.04 − (26575/252 + 6)) / 129.04 = 13.6 %
    out = unit_economics({"price": 249, "variable_cost": 110, "units": 180, "offers": [
        {"name": "trial", "kind": "trial_then_bill", "take_up_pct": 40, "return_pct": 12, "non_payment_pct": 4, "collection_cost_per_order": 6},
        {"name": "refund", "kind": "refund", "take_up_pct": 15, "return_pct": 7, "return_shipping_cost": 18},
    ]})["offers"]
    trial, refund = out["offers"]
    assert (trial["orders"], trial["returned"], trial["unpaid"], trial["retained_customers"], trial["net_contribution"]) == (252, 30, 9, 213, 27105)
    assert (refund["orders"], refund["returned"], refund["retained_customers"], refund["net_contribution"]) == (207, 14, 193, 26575)
    assert out["base"]["total_contribution"] == 25020 and out["best_offer"] == "trial"
    assert out["tie_point"]["return_pct"] == 13.6 and "until its return rate exceeds 13.6 %" in out["summary"]


def test_offers_missing_rates_give_a_grid_and_bad_kinds_are_refused():
    out = unit_economics({"price": 249, "variable_cost": 110, "units": 180, "offers": [{"name": "trial", "kind": "trial_then_bill", "take_up_pct": 40}]})["offers"]
    assert out["missing"] == ["offers[0].non_payment_pct", "offers[0].return_pct"]
    grid = out["offers"][0]["conditional"]
    assert len(grid) == 9 and grid[0]["assumed_return_pct"] == 5 and grid[0]["assumed_non_payment_pct"] == 2
    # 5 % returns, 2 % unpaid, no other costs: 0.95 × 0.98 × 139 − 0.95 × 0.02 × 110 = 129.41 − 2.09 = 127.32
    assert grid[0]["net_contribution_per_order"] == 127.32
    with pytest.raises(ToolError, match="kind must be"):
        unit_economics({"price": 249, "variable_cost": 110, "offers": [{"kind": "lottery"}]})
    with pytest.raises(ToolError, match="paid up front"):
        unit_economics({"price": 249, "variable_cost": 110, "offers": [{"kind": "refund", "return_pct": 5, "non_payment_pct": 3}]})
    # per-order figures do not need volume; the volume figures name units
    no_units = unit_economics({"price": 249, "variable_cost": 110, "offers": [{"kind": "refund", "return_pct": 7, "return_shipping_cost": 18}]})["offers"]
    assert no_units["offers"][0]["net_contribution_per_order"] == 128.01 and "orders" not in no_units["offers"][0] and "units" in no_units["missing"]


def test_offers_paid_option_gives_the_conversion_lift_it_must_bring():
    # AOV 85 at 22 % → 18.70 + 2.99 − 3.50 = 18.19 per standard order; same-day 18.70 + 6.99 − 9.50 = 16.19 (−2.00);
    # blended at 30 %: 18.19 − 0.6 = 17.59; conversion must rise 18.19/17.59 − 1 = 3.4 % (2.00 % → 2.07 %); neutral option cost 6.99 − 2.99 + 3.50 = 7.50
    out = unit_economics({"price": 85, "margin_pct": 22, "offers": [{"name": "same-day", "kind": "option", "option_fee": 6.99, "option_cost": 9.50, "take_rate_pct": 30, "standard_fee": 2.99, "standard_cost": 3.50, "baseline_conversion_pct": 2.0, "weekly_visits": 15000}]})["offers"]["option"]
    assert out["contribution_per_order_standard"] == 18.19 and out["contribution_per_order_with_option"] == 16.19 and out["option_delta_per_order"] == -2.0
    assert out["blended_contribution_per_order"] == 17.59 and out["required_conversion_lift_pct"] == 3.4 and out["required_conversion_pct"] == 2.07
    assert out["option_cost_at_which_option_is_neutral"] == 7.5 and [r["take_rate_pct"] for r in out["sensitivity"]] == [10, 25, 50, 100]
    assert out["sensitivity"][3]["required_conversion_lift_pct"] == 12.4
    missing = unit_economics({"price": 85, "margin_pct": 22, "offers": [{"kind": "option", "option_fee": 6.99, "take_rate_pct": 30}]})["offers"]
    assert missing["missing"] == ["offers[0].option_cost"] and "blended_contribution_per_order" not in missing["option"]
    with pytest.raises(ToolError, match="take_rate_pct must be between 0 and 100"):
        unit_economics({"price": 85, "margin_pct": 22, "offers": [{"kind": "option", "option_fee": 6.99, "option_cost": 9.5, "take_rate_pct": 130}]})


def test_offers_referral_reward_against_the_contribution():
    # 3000 × 1.5 × 25 % = 1125 referred sales × 40 = 45000; contribution 34 − 40 = −6 per sale; no cap → unbounded liability
    out = unit_economics({"price": 79, "variable_cost": 45, "offers": [{"kind": "referral", "reward_referrer": 20, "reward_referee": 20, "referrers": 3000, "referrals_per_referrer": 1.5, "referee_conversion_pct": 25}]})
    ref = out["offers"]["referral"]
    assert ref["referred_sales"] == 1125 and ref["total_reward_cost"] == 45000 and ref["cost_per_acquired_customer"] == 40
    assert ref["net_contribution_per_referred_sale"] == -6 and ref["max_total_reward"] == 34 and ref["decision"].startswith("never pays")
    assert ref["worst_case_liability"] is None and any("unbounded" in w for w in out["warnings"])
    capped = unit_economics({"price": 79, "variable_cost": 45, "offers": [{"kind": "referral", "reward_referrer": 15, "reward_referee": 15, "referrers": 3000, "payout_cap_per_referrer": 60}]})["offers"]
    assert capped["referral"]["worst_case_liability"] == 180000 and capped["missing"] == ["offers[0].referee_conversion_pct", "offers[0].referrals_per_referrer"]
    assert len(capped["referral"]["conditional"]) == 9 and capped["referral"]["conditional"][0]["referred_sales"] == 150


def test_line_new_variant_against_the_innovation_hurdle():
    # oat latte: 12 000 units in 12 months at 6 with 45 % margin → 2.70 × 1000/month = 2700; 20 % cannibalise 2.50 → 500; incremental 2200 × 12 = 26 400
    # investment 65 000 → net −38 600, ROI −59.4 %, break-even 65 000 / (2.70 − 0.5) = 29 546 units; payback 29.5 months, after the horizon
    out = unit_economics({"price": 6, "margin_pct": 45, "line": {"horizon_months": 12, "new": {"name": "oat latte", "price": 6, "margin_pct": 45, "forecast_units": 12000, "development_cost": 40000, "launch_cost": 25000, "cannibalisation_pct": 20, "cannibalised_contribution_per_unit": 2.5, "alternative": {"spend": 25000, "expected_incremental_units": 8000, "contribution_per_unit": 2.5}}}})["line"]["new"]
    assert out["revenue_over_horizon"] == 72000 and out["incremental_contribution_over_horizon"] == 26400 and out["net_over_horizon"] == -38600
    assert out["roi_pct"] == -59.4 and out["break_even_units"] == 29546 and out["verdict"] == "below_hurdle"
    assert out["payback_months"] is None and "29.5 months" in out["payback_note"]
    # 8000 × 2.5 − 25000 = −5000 still beats −38 600
    assert out["alternative"]["net"] == -5000 and out["alternative"]["better"] == "alternative"


def test_line_premium_line_against_repositioning_over_a_horizon():
    # Reserve: 3000 × 8 = 24 000 − 1200 switchers × 3.50 = 19 800/month; payback 40 000 / 19 800 = 2.0; 24 × 19 800 − 40 000 = 435 200
    # reposition: 16 000 × 5.50 = 88 000 (+18 000) after 6 months at 14 000 × 5.50 = 77 000 (+7 000): 6 × 7000 + 18 × 18 000 − 25 000 = 341 000;
    # payback 25 000 / 7 000 = 3.6 months; it adds nothing once volume falls 3.5/5.5 − 1 = −36.4 %
    out = unit_economics({"price": 8, "variable_cost": 4.5, "units": 20000, "line": {"horizon_months": 24,
        "new": {"name": "Reserve", "price": 14, "variable_cost": 6, "expected_units_per_month": 3000, "development_cost": 40000, "switching_pct": 40},
        "reposition": {"new_price": 10, "volume_change_pct": -20, "one_off_cost": 25000, "transition_months": 6, "transition_volume_change_pct": -30}}})["line"]
    assert out["new"]["incremental_contribution_per_month"] == 19800 and out["new"]["payback_months"] == 2.0 and out["new"]["net_over_horizon"] == 435200
    assert out["new"]["switching_flip_pct"] is None and "13500.00 per month" in out["new"]["flip_note"]
    rep = out["reposition"]
    assert rep["gain_per_month_after"] == 18000 and rep["gain_per_month_in_transition"] == 7000 and rep["net_over_horizon"] == 341000
    assert rep["payback_months"] == 3.6 and rep["volume_change_flip_pct"] == -36.4
    assert out["comparison"] == {"better": "Reserve", "gap_over_horizon": 94200}


def test_line_periods_decompose_the_change_into_price_volume_and_cost():
    # 1 530 000 / 180 000 = 8.50 → 1 368 000 / 180 000 = 7.60 (10.6 % depth); contribution 3.30 → 2.40 (38.8 % → 31.6 %); −162 000, all price
    out = unit_economics({"price": 8.5, "variable_cost": 5.2, "line": {"periods": [{"label": "2024", "revenue": 1530000, "units": 180000}, {"label": "2025", "revenue": 1368000, "units": 180000}]}})["line"]["periods"]
    a, b = out["rows"]
    assert (a["realized_price"], b["realized_price"], b["discount_depth_pct"]) == (8.5, 7.6, 10.6)
    assert (a["contribution_per_unit"], b["contribution_per_unit"], a["margin_pct"], b["margin_pct"]) == (3.3, 2.4, 38.8, 31.6)
    d = out["decomposition"][0]
    assert d == {"from": "2024", "to": "2025", "price_effect": -162000, "volume_effect": 0, "cost_effect": 0, "total_change": -162000}


def test_line_withholds_verdicts_without_switching_or_volume_change_and_refuses_bad_inputs():
    out = unit_economics({"price": 8, "variable_cost": 4.5, "units": 20000, "line": {"new": {"price": 14, "variable_cost": 6, "expected_units_per_month": 3000, "development_cost": 40000}, "reposition": {"new_price": 10, "one_off_cost": 25000}}})["line"]
    assert out["missing"] == ["line.new.switching_pct", "line.reposition.volume_change_pct"]
    assert [r["assumed_switching_pct"] for r in out["new"]["conditional"]] == [0, 25, 50] and "verdict" not in out["new"]
    assert out["reposition"]["conditional"][1]["gain_per_month"] == 18000 and "comparison" not in out
    with pytest.raises(ToolError, match="not both"):
        unit_economics({"price": 8, "variable_cost": 4.5, "line": {"new": {"price": 14, "variable_cost": 6, "expected_units_per_month": 3000, "forecast_units": 72000}}})
    with pytest.raises(ToolError, match="revenue or realized_price is required"):
        unit_economics({"price": 8, "variable_cost": 4.5, "line": {"periods": [{"label": "2024", "units": 100}]}})
    with pytest.raises(ToolError, match="at least one of new, reposition or periods"):
        unit_economics({"price": 8, "variable_cost": 4.5, "line": {"horizon_months": 12}})


def test_retail_bulk_breaking_and_assortment():
    # pallet 600 at 150/month → 4 months, 300 units (1230) on average, held 2 months: storage 0.10 + capital 4.10 × 10 %/12 × 2 = 0.07;
    # picking 6 / 12 = 0.50 → bridge 0.67 (16.3 % of 4.10); distributor 25 % of 7.50 = 1.88 → break-even margin 0.67/7.50 = 8.9 %, in house
    bulk = unit_economics({"price": 7.5, "retail": {"bulk": {"supplier_lot_units": 600, "unit_cost": 4.10, "monthly_demand_units": 150, "customer_lot_units": 12, "storage_cost_per_unit_month": 0.05, "handling_cost_per_order": 6, "capital_rate_pct_year": 10, "distributor_margin_pct": 25}}})["retail"]["bulk"]
    assert bulk["months_to_sell_lot"] == 4 and bulk["average_capital_tied"] == 1230 and bulk["carrying_cost_per_unit"] == 0.17 and bulk["handling_cost_per_unit"] == 0.5
    assert bulk["bridge_cost_per_unit"] == 0.67 and bulk["bridge_cost_pct_of_unit_cost"] == 16.3 and bulk["breakeven_distributor_margin_pct"] == 8.9 and bulk["verdict"] == "in_house"
    no_storage = unit_economics({"retail": {"bulk": {"supplier_lot_units": 600, "unit_cost": 4.10, "monthly_demand_units": 150, "customer_lot_units": 12, "handling_cost_per_order": 6}}})["retail"]["bulk"]
    assert no_storage["missing"] == ["retail.bulk.storage_cost_per_unit_month"] and "bridge_cost_per_unit" not in no_storage
    with pytest.raises(ToolError, match="monthly_demand_units must be greater than zero"):
        unit_economics({"retail": {"bulk": {"supplier_lot_units": 600, "monthly_demand_units": 0, "customer_lot_units": 12}}})
    # seeds: 48 000 × 11 % = 5280; 60 000 × 18 % = 10 800 baskets × 32 × 42 % = 145 152; losing 30 % costs 43 545.60; break-even 5280/145152 = 3.6 %
    seeds = unit_economics({"retail": {"assortment": {"line_revenue": 48000, "line_margin_pct": 11, "baskets_per_period": 60000, "basket_share_pct": 18, "attached_revenue_per_basket": 32, "attached_margin_pct": 42, "lost_basket_share_pct": 30}}})["retail"]["assortment"]
    assert (seeds["line_contribution"], seeds["anchored_baskets"], seeds["attached_contribution"], seeds["attached_contribution_at_risk"]) == (5280, 10800, 145152, 43545.6)
    assert seeds["breakeven_lost_share_pct"] == 3.6 and seeds["decision"] == "keep as assortment service"
    open_ = unit_economics({"retail": {"assortment": {"line_revenue": 48000, "line_margin_pct": 11, "baskets_per_period": 60000, "basket_share_pct": 18, "attached_revenue_per_basket": 32, "attached_margin_pct": 42}}})["retail"]["assortment"]
    assert open_["missing"] == ["retail.assortment.lost_basket_share_pct"] and [r["assumed_lost_basket_share_pct"] for r in open_["conditional"]] == [0, 25, 50, 100]


def test_retail_channel_risk_functions_and_waterfall():
    # dress 149 / cost 52 / 50 % margin → wholesale 74.50, 22.50 to the brand. 70 % sold, 10 % unsold, 20 % marked down 40 %:
    # markdown money 0.2 × 0.4 × 74.50 = 5.96 + take-back 0.1 × 74.50 = 7.45 → 13.41 (9.0 margin points), contribution 9.09;
    # 50 % with take-back = (149 − 52 − 9.09)/149 = 59.0 % without; break-even sell-through 100 − 10 − (22.50 − 7.45)/29.80 × 100 = 39.5 %
    risk = unit_economics({"price": 149, "variable_cost": 52, "retail": {"channel_risk": {"margin_pct": 50, "sell_through_pct": 70, "markdown_pct": 40, "unsold_pct": 10}}})["retail"]["channel_risk"]
    assert (risk["wholesale_price"], risk["brand_contribution_no_risk"], risk["risk_cost_per_unit"], risk["risk_cost_margin_points"]) == (74.5, 22.5, 13.41, 9.0)
    assert risk["brand_contribution_with_risk"] == 9.09 and risk["equivalent_margin_pct_without_risk"] == 59.0 and risk["breakeven_sell_through_pct"] == 39.5
    retailer_keeps = unit_economics({"price": 149, "variable_cost": 52, "retail": {"channel_risk": {"margin_pct": 50, "sell_through_pct": 70, "markdown_pct": 40, "unsold_pct": 10, "risk": {"markdowns": "retailer", "unsold": "retailer"}}}})["retail"]["channel_risk"]
    assert retailer_keeps["risk_cost_per_unit"] == 0 and retailer_keeps["brand_contribution_with_risk"] == 22.5
    no_st = unit_economics({"price": 149, "variable_cost": 52, "retail": {"channel_risk": {"margin_pct": 50, "markdown_pct": 40, "unsold_pct": 10}}})["retail"]["channel_risk"]
    assert no_st["missing"] == ["retail.channel_risk.sell_through_pct"] and [r["assumed_sell_through_pct"] for r in no_st["conditional"]] == [60, 70, 80, 90]
    with pytest.raises(ToolError, match="add up to more than 100"):
        unit_economics({"price": 149, "variable_cost": 52, "retail": {"channel_risk": {"margin_pct": 50, "sell_through_pct": 95, "markdown_pct": 40, "unsold_pct": 10}}})
    with pytest.raises(ToolError, match="contradict"):
        unit_economics({"price": 149, "variable_cost": 52, "retail": {"channel_risk": {"margin_pct": 50, "wholesale_price": 80, "sell_through_pct": 70, "markdown_pct": 40}}})
    # going direct: 32 % of 48 = 15.36 kept; costed functions 0.40 + 1.10 + 3.90 + 0.35 = 5.75 → 9.61 per bottle, 134 540 a year
    fn = unit_economics({"price": 48, "units": 14000, "retail": {"functions": {"intermediary_margin_pct": 32, "functions": [
        {"function": "storage", "new_owner": "3pl", "cost_per_unit": 0.40}, {"function": "order handling", "new_owner": "3pl", "cost_per_unit": 1.10},
        {"function": "delivery", "new_owner": "3pl", "cost_per_unit": 3.90}, {"function": "returns", "new_owner": "us", "cost_per_unit": 0.35},
        {"function": "credit/collections", "new_owner": "us"}, {"function": "selling/visits to 60 restaurants"}]}}})["retail"]["functions"]
    assert (fn["margin_kept_per_unit"], fn["direct_cost_per_unit"], fn["net_gain_per_unit"], fn["net_gain_per_period"]) == (15.36, 5.75, 9.61, 134540)
    assert fn["missing"] == ["credit/collections (no cost)", "selling/visits to 60 restaurants (unassigned)"] and fn["checklist_gaps"] == ["promotion", "stock risk"]
    with pytest.raises(ToolError, match="cannot compare without price and units"):
        unit_economics({"price": 48, "retail": {"functions": {"intermediary_margin_pct": 32, "functions": [{"function": "storage"}]}}})
    # shelf 2.49: Maxima 35 % → 1.6185; distributor 12 % of that → 1.4243 net; − 0.78 = 0.6443; 2-for-1 for 2 of 4 weeks = 0.78 × 0.5 = 0.39 → 0.25 for demand
    wf = unit_economics({"price": 2.49, "variable_cost": 0.78, "units": 20000, "retail": {"waterfall": {"layers": [{"name": "Maxima", "margin_pct": 35, "margin_base": "selling_price"}, {"name": "distributor", "margin_pct": 12}], "listing_fees": 6000, "promo_units_free_pct": 100, "promo_weeks": 2, "period_weeks": 4}}})["retail"]["waterfall"]
    assert (wf["net_to_brand"], wf["contribution_after_channel"], wf["promo_cost_per_unit"], wf["demand_budget_per_unit"]) == (1.42, 0.64, 0.39, 0.25)
    assert wf["demand_budget_per_period"] == 5085.6 and wf["break_even_units"] == 9313 and wf["break_even_units_during_promo"] == 23597
    assert wf["missing"] == ["retail.waterfall.layers[1].margin_base"] and wf["layers"][0]["takes_per_unit"] == 0.87
    eaten = unit_economics({"price": 2.49, "variable_cost": 1.8, "retail": {"waterfall": {"layers": [{"name": "Maxima", "margin_pct": 35, "margin_base": "selling_price"}]}}})["retail"]["waterfall"]
    assert "channel_eats_margin" in eaten["flags"] and eaten["demand_budget_per_unit"] == 0
    with pytest.raises(ToolError, match="margin_base must be selling_price or cost_plus"):
        unit_economics({"price": 2.49, "variable_cost": 0.78, "retail": {"waterfall": {"layers": [{"name": "x", "margin_pct": 35, "margin_base": "of_price"}]}}})


def test_retail_displacement_handling_promotion_regions_and_terms():
    # Chocolate 40/2 × 2.29 × 30 % = 13.74 per facing-week vs Original 39.42; barista must do 13.74 / (2.49 × 0.3) = 18.4 units per facing
    disp = unit_economics({"retail": {"displacement": {"candidates": [{"sku": "Original", "weekly_units": 120, "facings": 2, "retail_price": 2.19, "retailer_margin_pct": 30}, {"sku": "Chocolate", "weekly_units": 40, "facings": 2, "retail_price": 2.29, "retailer_margin_pct": 30}], "new_sku": {"retail_price": 2.49, "retailer_margin_pct": 30, "expected_weekly_units": 15}}}})["retail"]["displacement"]
    assert disp["likely_displaced"] == "Chocolate" and disp["hurdle"] == 13.74 and disp["new_sku"]["required_weekly_units_per_facing"] == 18.4 and disp["verdict"] == "below_hurdle"
    unknown = unit_economics({"retail": {"displacement": {"candidates": [{"sku": "Original", "retail_price": 2.19, "retailer_margin_pct": 30}]}}})["retail"]["displacement"]
    assert unknown["verdict"] == "unknown" and unknown["missing"] == ["retail.displacement.candidates[0].weekly_units"] and "ask the buyer" in unknown["summary"]
    # chanterelles: Maxima 9.50 × 0.96 − 6.20 = 2.92; Germany 14 × 0.94³ − 6.20 − 2.70 = 2.73; Germany overtakes below 5.5 %/day
    routes = unit_economics({"retail": {"handling": {"routes": [{"name": "Maxima", "price": 9.5, "cost": 6.2, "transit_days": 1, "fixed_loss_pct": 4}, {"name": "Germany", "price": 14, "cost": 6.2, "handling": 2.7, "transit_days": 3, "loss_pct_per_day": 6}]}}})["retail"]["handling"]
    a, b = routes["routes"]
    assert (a["usable_fraction"], a["margin_per_unit_shipped"], b["usable_fraction"], b["margin_per_unit_shipped"]) == (0.96, 2.92, 0.8306, 2.73)
    assert routes["ranking"] == ["Maxima", "Germany"] and b["breakeven_loss_pct"] == 5.5 and b["breakeven_loss_basis"] == "per day"
    needs = unit_economics({"retail": {"handling": {"routes": [{"name": "x", "price": 9.5, "cost": 6.2, "transit_days": 1}, {"name": "y", "price": 9, "cost": 6.2, "transit_days": 5, "shelf_life_days": 3}]}}})["retail"]["handling"]
    assert needs["missing"] == ["x: loss_pct_per_day or fixed_loss_pct"] and needs["routes"][1]["usable_fraction"] == 0 and "unusable route" in needs["routes"][1]["note"]
    # coupon: 3 % of 40 000 = 1200 × (2 + 0.10) = 2520; stock 300 → shortfall 900, cover 300 / (1200/14) = 3.5 days
    promo = unit_economics({"retail": {"promotion": {"reach": 40000, "redemption_rate_pct": 3, "face_value": 2, "handling_fee": 0.10, "stock_on_hand": 300, "period_days": 14}}})["retail"]["promotion"]
    assert (promo["expected_redemptions"], promo["brand_cost"], promo["shortfall_units"], promo["stock_cover_days"]) == (1200, 2520, 900, 3.5)
    no_rate = unit_economics({"retail": {"promotion": {"reach": 40000, "face_value": 2}}})["retail"]["promotion"]
    assert no_rate["missing"] == ["retail.promotion.stock_on_hand", "retail.promotion.redemption_rate_pct"] and [r["assumed_redemption_rate_pct"] for r in no_rate["conditional"]] == [1, 3, 5]
    # pellets: Riga 38/48 = 0.79 → 7.69 landed, +6.8 % on 7.20; seller absorbing freight keeps (6.90 − 4.10 − 0.79)/6.90 = 29.1 %
    reg = unit_economics({"price": 6.9, "variable_cost": 4.1, "retail": {"regions": {"regions": [{"name": "Riga", "freight_per_shipment": 38, "units_per_shipment": 48, "competitor_price": 7.2}, {"name": "Berlin", "freight_per_shipment": 120, "units_per_shipment": 48, "competitor_price": 8.4}, {"name": "Oslo"}]}}})["retail"]["regions"]
    assert reg["regions"][0]["landed_price"] == 7.69 and reg["regions"][0]["price_gap_pct"] == 6.8 and reg["regions"][0]["competitive"] is False and reg["regions"][0]["margin_pct_if_seller_absorbs"] == 29.1
    assert reg["ranking"] == ["Riga", "Berlin"] and reg["regions"][2]["landed_price"] is None and len(reg["missing"]) == 1 and "none of the 2 regions beats" in reg["summary"]
    # terms: Germany 100 days × 5.40 × 9 %/365 = 0.13 → 9 − 5.40 − 0.13 = 3.47; Vilnius 31 days → 0.04 → 2.76; tie at 0.80 / (5.40 × 69/365) = 78.4 %
    terms = unit_economics({"variable_cost": 5.4, "retail": {"payment_terms": {"annual_finance_rate_pct": 9, "offers": [{"name": "Germany", "price": 9, "days_to_payment": 90, "days_in_transit": 10, "order_units": 12000}, {"name": "Vilnius", "price": 8.2, "days_to_payment": 30, "days_in_transit": 1, "order_units": 12000}]}}})["retail"]["payment_terms"]
    g, v = terms["offers"]
    assert (g["days_financed"], g["financing_cost_per_unit"], g["adjusted_contribution_per_unit"], g["capital_tied_up"]) == (100, 0.13, 3.47, 64800)
    assert (v["financing_cost_per_unit"], v["adjusted_contribution_per_unit"]) == (0.04, 2.76) and terms["best_offer"] == "Germany" and terms["break_even_rate_pct"] == 78.4
    no_rate = unit_economics({"variable_cost": 5.4, "retail": {"payment_terms": {"offers": [{"name": "Germany", "price": 9, "days_to_payment": 90}]}}})["retail"]["payment_terms"]
    assert no_rate["missing"] == ["retail.payment_terms.offers[0].annual_finance_rate_pct"] and [r["assumed_annual_finance_rate_pct"] for r in no_rate["offers"][0]["conditional"]] == [5, 9, 15]


def test_retail_gmroi_returns_reserve_and_incentives():
    # pots: 200 000 × 45 % = 90 000 / 50 000 stock = 1.8 (2.2 turns); seeds 5280 / 20 000 = 0.26
    g = unit_economics({"retail": {"gmroi": {"target_gmroi": 1, "items": [{"name": "seeds", "average_inventory_at_cost": 20000, "revenue": 48000, "gross_margin_pct": 11}, {"name": "pots", "average_inventory_at_cost": 50000, "revenue": 200000, "gross_margin_pct": 45}]}}})["retail"]["gmroi"]
    assert g["ranking"] == ["pots", "seeds"] and g["items"][0]["gmroi"] == 1.8 and g["items"][0]["inventory_turns"] == 2.2 and g["items"][1]["gmroi"] == 0.26 and g["below_target"] == ["seeds"]
    with pytest.raises(ToolError, match="contradict"):
        unit_economics({"retail": {"gmroi": {"items": [{"name": "x", "average_inventory_at_cost": 100, "revenue": 1000, "cogs": 500, "gross_margin_pct": 20}]}}})
    # chair: a return loses 139 of contribution + 20 % of 110 written off + 18 handling = 179; at 7 % → 12.53 per unit sold (5.0 % of revenue)
    rr = unit_economics({"price": 249, "variable_cost": 110, "units": 180, "retail": {"returns_reserve": {"return_rate_pct": 7, "handling_cost_per_return": 18, "resale_recovery_pct": 80}}})["retail"]["returns_reserve"]
    assert (rr["loss_per_return"], rr["reserve_per_unit_sold"], rr["reserve_pct_of_revenue"], rr["contribution_after_returns"], rr["reserve_per_period"]) == (179, 12.53, 5.0, 126.47, 2255.4)
    assert unit_economics({"retail": {"returns_reserve": {"return_rate_pct": 7}}})["retail"]["returns_reserve"]["missing"] == ["price", "variable_cost"]
    # A: 100 price / 20 gross profit; B: 60 / 30. 10 % of revenue pays 10 vs 6 → rep pushes A; 25 % of gross profit pays 5 vs 7.5 → B, which the company wants
    inc = unit_economics({"retail": {"incentives": {"items": [{"name": "A", "price": 100, "variable_cost": 80}, {"name": "B", "price": 60, "variable_cost": 30}], "revenue_commission_pct": 10, "gross_profit_commission_pct": 25}}})["retail"]["incentives"]
    assert inc["company_prefers"] == "B" and inc["rep_pushes_on_revenue_scheme"] == "A" and inc["revenue_scheme_aligned"] is False and inc["gross_profit_scheme_aligned"] is True
    with pytest.raises(ToolError, match="retail needs at least one of"):
        unit_economics({"retail": {}})


def test_media_value_keyword_test_incremental_space_and_catalogue():
    # 600 / 0.45 = 1333 clicks; contribution 89 − 55.18 = 33.82 → break-even 0.45/33.82 = 1.33 %; 24/1333 = 1.80 %, Wilson 1.21–2.67 %;
    # 0.61 per visit; ROI (811.68 − 599.85)/599.85 = 35.3 %; SEO 500/month ÷ 0.609 = 822 visits
    kw = unit_economics({"price": 89, "variable_cost": 55.18, "media_value": {"keyword_test": {"budget": 600, "cpc": 0.45, "observed_clicks": 1333, "observed_orders": 24, "seo_cost": 3000, "seo_months": 6}}})["media_value"]["keyword_test"]
    assert kw["clicks_affordable"] == 1333 and kw["break_even_cvr_pct"] == 1.33
    assert kw["read"]["cvr_pct"] == 1.8 and kw["read"]["cvr_ci95_pct"] == [1.21, 2.67] and kw["read"]["contribution_per_visit"] == 0.61 and kw["read"]["paid_roi_pct"] == 35.3
    assert kw["seo"]["visits_needed_per_month_to_repay"] == 822 and kw["seo"]["verdict"] == "worth_testing_organic"
    thin = unit_economics({"price": 89, "variable_cost": 55.18, "media_value": {"keyword_test": {"budget": 600, "cpc": 0.45, "observed_clicks": 300, "observed_orders": 3}}})["media_value"]["keyword_test"]
    assert thin["verdict"] == "insufficient_sample"
    no_cost = unit_economics({"price": 89, "media_value": {"keyword_test": {"budget": 600, "cpc": 0.45}}})["media_value"]["keyword_test"]
    assert no_cost["missing"] == ["variable_cost"] and no_cost["clicks_affordable"] == 1333 and "break_even_cvr_pct" not in no_cost
    with pytest.raises(ToolError, match="cannot exceed observed_clicks"):
        unit_economics({"price": 89, "variable_cost": 55.18, "media_value": {"keyword_test": {"budget": 600, "cpc": 0.45, "observed_clicks": 10, "observed_orders": 20}}})
    # full page costs 1400 more; 109 per order → 13 extra orders = 37.1 % over 35
    inc = unit_economics({"price": 149, "variable_cost": 40, "media_value": {"incremental": {"incremental_cost": 1400, "baseline_units": 35, "expected_uplift_pct": 20}}})["media_value"]["incremental"]
    assert inc["extra_units_needed"] == 13 and inc["required_lift_pct"] == 37.1 and inc["expected_extra_units"] == 7 and inc["verdict"] == "does_not_pay"
    cond = unit_economics({"price": 149, "media_value": {"incremental": {"incremental_cost": 1400}}})["media_value"]["incremental"]
    assert cond["missing"] == ["variable_cost"] and cond["conditional"][1] == {"assumed_margin_pct": 40.0, "extra_units_needed": 24} and cond["verdict"] == "unknown"
    # catalogue: 42 000 × 1.10 + 9800 = 56 000 (1.33/piece); revenue 1.70/piece; gross 71 400 × 38 % = 27 132; net −28 868 (−0.69); ROAS 1.27; order rate 2.43 %;
    # break-even 56 000 / (70 × 0.38) = 2106 orders = 5.01 %
    cat = unit_economics({"media_value": {"campaign": {"pieces_sent": 42000, "print_cost_per_piece": 0.62, "delivery_cost_per_piece": 0.48, "fixed_costs": 9800, "responses": 1350, "attributed_orders": 1020, "attributed_revenue": 71400, "gross_margin_pct": 38}}})["media_value"]["campaign"]
    assert (cat["cost_all_in"], cat["cost_per_piece_all_in"], cat["revenue_per_piece"], cat["gross_profit"], cat["net"]) == (56000, 1.33, 1.7, 27132, -28868)
    assert cat["per_piece"]["net"] == -0.69 and cat["roas"] == 1.27 and cat["order_rate_pct"] == 2.43 and cat["cost_per_order"] == 54.9
    assert cat["break_even"]["order_rate_pct_at_current_aov"] == 5.01 and "did not pay back" in cat["summary"]
    unattributed = unit_economics({"media_value": {"campaign": {"pieces_sent": 42000, "cost_per_piece": 1.10, "fixed_costs": 9800}}})["media_value"]["campaign"]
    assert unattributed["missing"] == ["media_value.campaign.attributed_revenue"] and "attribution is unknown" in unattributed["summary"]
    no_margin = unit_economics({"media_value": {"campaign": {"pieces_sent": 42000, "cost_per_piece": 1.10, "fixed_costs": 9800, "attributed_orders": 1020, "attributed_revenue": 71400}}})["media_value"]["campaign"]
    assert no_margin["missing"] == ["media_value.campaign.gross_margin_pct"] and [r["assumed_gross_margin_pct"] for r in no_margin["conditional"]] == [30, 40, 50]


def test_media_value_spend_levels_outcome_partnership_and_benchmark():
    # 20k × 4.2 = 84 000 × 38 % = 31 920 − 20 000 = 11 920 (59.6 %); 35k × 3.1 = 108 500 → 41 230 − 35 000 = 6230 (17.8 %)
    # step: +15 000 → +24 500, marginal ROAS 1.63 < 1/0.38 = 2.63, marginal profit −5690; flips above 15 000/24 500 = 61.2 % margin
    so = unit_economics({"media_value": {"margin_pct": 38, "spend_options": {"options": [{"label": "35k", "spend": 35000, "roas": 3.1}, {"label": "20k", "spend": 20000, "roas": 4.2}]}}})["media_value"]["spend_options"]
    low, high = so["options"]
    assert (low["revenue"], low["gross_profit"], low["net_profit"], low["roi_pct"]) == (84000, 31920, 11920, 59.6) and (high["net_profit"], high["roi_pct"]) == (6230, 17.8)
    step = so["steps"][0]
    assert (step["marginal_roas"], step["marginal_breakeven_roas"], step["marginal_profit"], step["flip_margin_pct"]) == (1.63, 2.63, -5690, 61.2)
    assert so["best_by_profit"] == "20k" and "flips above a 61.2 % margin" in so["summary"]
    flipped = unit_economics({"media_value": {"margin_pct": 38, "spend_options": {"options": [{"label": "20k", "spend": 20000, "roas": 4.2}, {"label": "35k", "spend": 35000, "roas": 3.6}]}}})["media_value"]["spend_options"]
    # 35k × 3.6 = 126 000 × 38 % = 47 880 − 35 000 = 12 880, i.e. +960 over 20k despite the lower ROAS
    assert flipped["best_by_profit"] == "35k" and flipped["best_by_roi"] == "20k" and flipped["steps"][0]["marginal_profit"] == 960
    cond = unit_economics({"media_value": {"spend_options": {"options": [{"label": "20k", "spend": 20000, "roas": 4.2}, {"label": "35k", "spend": 35000, "roas": 3.1}]}}})["media_value"]["spend_options"]
    assert cond["missing"] == ["margin_pct"] and cond["conditional"][1]["best_by_profit"] == "20k" and cond["conditional"][3]["best_by_profit"] == "35k"
    with pytest.raises(ToolError, match="same spend"):
        unit_economics({"media_value": {"margin_pct": 38, "spend_options": {"options": [{"label": "a", "spend": 20000, "roas": 4.2}, {"label": "b", "spend": 20000, "roas": 3.1}]}}})
    # donors: 40 000 / 1600 = 25.00; 700 incremental → 57.14 against 120 → pays; break-even 334 outcomes
    oc = unit_economics({"media_value": {"outcome": {"budget": 40000, "target_outcomes": 1600, "baseline_outcomes": 900, "value_per_outcome": 120, "outcome_unit": "donor"}}})["media_value"]["outcome"]
    assert (oc["cost_per_outcome"], oc["incremental_outcomes"], oc["cost_per_incremental_outcome"], oc["breakeven_outcomes"], oc["decision"]) == (25, 700, 57.14, 334, "pays")
    unknown = unit_economics({"media_value": {"outcome": {"budget": 40000, "target_outcomes": 1600}}})["media_value"]["outcome"]
    assert unknown["missing"] == ["value_per_outcome"] and unknown["decision"] == "unknown"
    # partnership: 12 000 cash; 8 × 1500 + 40 × 150 = 18 000 in kind; 212 orders → 56.60 on cash, 141.51 all in; never one total
    pt = unit_economics({"media_value": {"partnership": {"cash_paid": 12000, "in_kind": [{"item": "LED board", "quantity": 8, "unit_value": 1500, "basis": "rate_card"}, {"item": "IG stories", "quantity": 40, "unit_value": 150, "basis": "own_estimate"}], "outcomes": [{"metric": "orders", "value": 212, "attribution": "promo_code"}, {"metric": "visits", "value": 3100, "attribution": "none"}]}}})["media_value"]["partnership"]
    assert pt["cash_total"] == 12000 and pt["in_kind_total"] == 18000 and pt["in_kind_by_basis"]["own_estimate"] == 6000
    assert pt["cost_per_outcome_cash"]["orders"] == 56.6 and pt["cost_per_outcome_all_in"]["orders"] == 141.51 and "total_value" not in pt
    assert pt["outcomes"][1]["label"] == "observed, not attributed" and len(pt["report_lines"]) == 5
    unknown_kind = unit_economics({"media_value": {"partnership": {"cash_paid": 12000, "in_kind": [{"item": "LED", "basis": "quote"}], "outcomes": [{"metric": "orders", "value": 212}]}}})["media_value"]["partnership"]
    assert unknown_kind["in_kind_total"] is None and unknown_kind["cost_per_outcome_all_in"]["orders"] is None and unknown_kind["missing"] == ["media_value.partnership.in_kind[0].estimated_value"]
    with pytest.raises(ToolError, match="basis must be"):
        unit_economics({"media_value": {"partnership": {"cash_paid": 1, "in_kind": [{"item": "x", "estimated_value": 5, "basis": "guess"}]}}})
    # benchmark: two B2B annual SaaS figures match (1.5–2.4×); the retail figure differs on four keys and is excluded; 5× on 15 000 = 75 000 revenue, 60 000 at 80 %
    bm = unit_economics({"media_value": {"margin_pct": 80, "benchmark": {"context": {"sector": "saas", "b2b_or_b2c": "b2b", "contract": "annual", "price_position": "mid"}, "figures": [
        {"return_multiple": 1.5, "context": {"sector": "saas", "b2b_or_b2c": "b2b", "contract": "annual"}, "source_url": "u1"},
        {"return_multiple": 2.4, "context": {"sector": "saas", "b2b_or_b2c": "b2b", "contract": "annual", "price_position": "mid"}, "source_url": "u2"},
        {"return_multiple": 5.2, "context": {"sector": "retail", "b2b_or_b2c": "b2c", "contract": "one_off", "price_position": "budget"}, "source_url": "pigu"}],
        "expected_return_multiple": 5, "campaign_spend": 15000, "revenue_per_customer": 348}}})["media_value"]["benchmark"]
    assert bm["matched_range"] == [1.5, 2.4] and bm["expected_vs_range"] == "above" and bm["mismatched_figures"][0]["source_url"] == "pigu"
    assert bm["implied"] == {"revenue": 75000, "contribution": 60000, "customers": 216}
    none = unit_economics({"media_value": {"benchmark": {"context": {"sector": "saas", "b2b_or_b2c": "b2b"}, "figures": [{"return_multiple": 5.2, "context": {"sector": "retail", "b2b_or_b2c": "b2c"}}], "expected_return_multiple": 5}}})["media_value"]["benchmark"]
    assert none["matched_range"] is None and none["expected_vs_range"] == "no_match" and "own break-even is the only reference" in none["summary"]


def test_routes_score_options_against_ranked_outcomes():
    # improvement 5×2 + 5×2 + 2×0 = 20 → 35 000/20 = 1750 per point; platform 5 + 5 + 4 = 14 → 12 857.14; platform costs 5.1× and adds less → eliminated
    args = {"routes": {"outcomes": [{"name": "close month faster", "weight": 5}, {"name": "fewer VAT errors", "weight": 5}, {"name": "nicer dashboards", "weight": 2}], "routes": [
        {"name": "new platform", "cost": 180000, "months": 12, "effects": {"close month faster": 1, "fewer VAT errors": 1, "nicer dashboards": 2}},
        {"name": "improvement", "cost": 35000, "months": 3, "effects": {"close month faster": 2, "fewer VAT errors": 2, "nicer dashboards": 0}}]}}
    out = unit_economics(args)["routes"]
    platform, improvement = out["routes"]
    assert (platform["weighted_gain"], platform["cost_per_gain_point"], improvement["weighted_gain"], improvement["cost_per_gain_point"]) == (14, 12857.14, 20, 1750)
    assert out["eliminate"] == ["new platform"] and out["status"] == "ok" and "5.1× more" in out["summary"]
    incomplete = json.loads(json.dumps(args))
    del incomplete["routes"]["routes"][0]["effects"]["nicer dashboards"]
    out = unit_economics(incomplete)["routes"]
    assert out["status"] == "insufficient" and out["missing"] == ["routes.routes[0].effects[nicer dashboards]"] and out["routes"][0]["weighted_gain"] is None and out["eliminate"] == []
    with pytest.raises(ToolError, match="weight must be between 1 and 5"):
        unit_economics({"routes": {"outcomes": [{"name": "a", "weight": 7}], "routes": [{"name": "r", "cost": 1, "effects": {"a": 1}}]}})
    with pytest.raises(ToolError, match="must be 0, 1 or 2"):
        unit_economics({"routes": {"outcomes": [{"name": "a", "weight": 3}], "routes": [{"name": "r", "cost": 1, "effects": {"a": 3}}]}})
    with pytest.raises(ToolError, match="not one of the outcomes"):
        unit_economics({"routes": {"outcomes": [{"name": "a", "weight": 3}], "routes": [{"name": "r", "cost": 1, "effects": {"a": 1, "b": 1}}]}})


def test_new_blocks_are_additive_and_the_tool_wrapper_carries_them():
    plain = unit_economics({"price": 100, "variable_cost": 60, "units": 100, "new_price": 90})
    assert set(plain) == {"currency", "inputs", "missing", "assumptions", "contribution_per_unit", "margin_pct", "markup_pct", "total_contribution", "revenue", "change", "sensitivity", "summary"}
    with_block = unit_economics({"price": 100, "variable_cost": 60, "units": 100, "new_price": 90, "ownership": {"base_price": 10, "consumable_price": 1, "consumable_units_per_period": 1, "periods": 12}})
    assert {k: v for k, v in with_block.items() if k not in ("ownership", "summary")} == {k: v for k, v in plain.items() if k != "summary"}
    assert with_block["summary"].startswith(plain["summary"]) and "Total cost of ownership 22.00" in with_block["summary"]
    # a price change still needs a price even when a standalone block is present
    with pytest.raises(ToolError, match="price is required to judge"):
        unit_economics({"new_price": 90, "routes": {"outcomes": [{"name": "a", "weight": 3}], "routes": [{"name": "r", "cost": 1, "effects": {"a": 1}}]}})
    text = unit_economics_tool({"price": "45", "variable_cost": "8", "capacity": json.dumps({"capacity_units": 30, "occupancy_pct": 40, "offpeak_price": 33.75})})
    assert json.loads(text)["capacity"]["offer"]["shift_break_even_pct"] == 69.6



def test_dispatch_unit_economics_blocks_without_price(tmp_path):
    import json as _json

    from rocky.tools import WorkspaceTools, dispatch, worker_tools

    out = _json.loads(dispatch(WorkspaceTools(tmp_path), "unit_economics", {"ownership": {"base_price": 100, "consumable_price": 0.5, "consumable_units_per_period": 2, "periods": 365, "period_label": "day"}}))
    assert "ownership" in out
    spec = next(t for t in worker_tools(False) if t["function"]["name"] == "unit_economics")
    assert spec["function"]["parameters"]["required"] == []
    assert "retail" in spec["function"]["parameters"]["properties"]
