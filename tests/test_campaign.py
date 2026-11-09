import json

import pytest

from rocky.campaign import MODES, campaign_calc, campaign_calc_tool
from rocky.tools import ToolError


def run(mode, **args):
    return campaign_calc({"mode": mode, **args})


# --- dispatch ---------------------------------------------------------------


def test_mode_is_required_and_checked():
    with pytest.raises(ToolError, match="mode is required"):
        campaign_calc({})
    with pytest.raises(ToolError, match="unknown mode"):
        campaign_calc({"mode": "nope"})
    assert len(MODES) == 13


def test_tool_wrapper_returns_json_with_the_common_fields():
    text = campaign_calc_tool({"mode": "referral_loop", "invites_per_user": 2, "acceptance_rate": 0.5, "activation_rate": 0.5})
    out = json.loads(text)
    assert out["mode"] == "referral_loop"
    for key in ("method", "missing", "warnings", "summary"):
        assert key in out


# --- lift_compare -----------------------------------------------------------


def test_lift_compare_adds_request_ads_to_the_opt_in_arm_and_says_gross():
    # Kaunas: 20 000 × 0.35 = 7 000; 410 buyers → 20.5 per 1 000, 17.07 per buyer.
    # Klaipėda: 3 200 × 0.35 + 2 900 = 4 020; 390 buyers → 121.9 per 1 000, 10.31 per buyer.
    # Flip: Klaipėda's extra cost at which 17.07 × 390 = total → 6 658.5 − 1 120 = 5 538.5 (17.0732 exactly → 5 537.3).
    out = run(
        "lift_compare",
        arms=[
            {"name": "Kaunas", "units": 20000, "unit_cost": 0.35, "buyers": 410},
            {"name": "Klaipėda", "units": 3200, "unit_cost": "0,35", "extra_cost": "€2 900", "buyers": 390},
        ],
    )
    kaunas, klaipeda = out["arms"]
    assert kaunas["total_cost"] == 7000 and kaunas["buyers_per_1000_units"] == 20.5 and kaunas["cost_per_buyer"] == 17.07
    assert klaipeda["total_cost"] == 4020 and klaipeda["buyers_per_1000_units"] == 121.9 and klaipeda["cost_per_buyer"] == 10.31
    assert out["ranking"] == ["Klaipėda", "Kaunas"] and out["ranking_basis"] == "cost_per_buyer"
    assert 5530 < out["flip_point"]["extra_cost_at_which_ranking_flips"] < 5545
    assert "Kaunas: baseline_buyers" in out["missing"]
    assert any("gross buyers, not incremental" in w for w in out["warnings"])
    assert "per buyer (gross)" in out["summary"]


def test_lift_compare_incremental_with_a_baseline_and_the_wilson_interval():
    # 12 of 30: p = 0.4; Wilson 95 % → 24.6 % to 57.7 %.
    # incremental = 12 − 4 = 8; cost 30 → 3.75 per incremental buyer.
    out = run(
        "lift_compare",
        arms=[
            {"name": "test", "units": 30, "unit_cost": 1, "buyers": 12, "baseline_buyers": 4, "population": 100},
            {"name": "control", "units": 30, "unit_cost": 1, "buyers": 6, "baseline_buyers": 4, "population": 100},
        ],
    )
    test = out["arms"][0]
    assert test["conversion_pct"] == 40.0
    assert test["conversion_interval"] == {"low_pct": 24.6, "high_pct": 57.7}
    assert test["incremental_buyers"] == 8 and test["cost_per_incremental_buyer"] == 3.75
    assert test["penetration_pct"] == 12.0
    assert out["ranking_basis"] == "cost_per_incremental_buyer" and out["missing"] == []


def test_lift_compare_refuses_more_buyers_than_units():
    with pytest.raises(ToolError, match="exceed units"):
        run("lift_compare", arms=[{"units": 10, "unit_cost": 1, "buyers": 11}])
    with pytest.raises(ToolError, match="units must be greater"):
        run("lift_compare", arms=[{"units": 0, "unit_cost": 1, "buyers": 0}])


# --- referral_loop ----------------------------------------------------------


def test_referral_loop_k_and_amplification():
    # K = 3 × 0.4 × 0.5 = 0.6 → amplification 1 / 0.4 = 2.5; 1 000 seed → +600, +360 → 1 960 after 2 cycles.
    # blended CAC = (20 + 1.5 × 10) / 2.5 = 14.
    out = run(
        "referral_loop",
        invites_per_user=3,
        acceptance_rate=0.4,
        activation_rate=0.5,
        cycle_days=10,
        users=1000,
        cycles=2,
        contribution_per_user=30,
        incentive_per_referred_user=10,
        paid_cac=20,
    )
    assert out["k"] == 0.6 and out["amplification"] == 2.5
    assert out["after_cycles"]["rows"][1] == {"cycle": 2, "new_users": 360.0, "total_users": 1960.0}
    assert out["cycles_in_horizon"]["cycles"] == 9.0
    assert out["break_even_incentive_per_referred_user"] == 30.0
    assert out["incentive_check"]["verdict"] == "pays"
    assert out["blended_cac"]["blended_cac"] == 14.0
    assert "K = 3 × 40 % × 50 % = 0.60" in out["summary"]


def test_referral_loop_k_above_one_is_unbounded_and_percent_rates_are_read():
    # 5 × 0.4 × 0.6 = 1.2
    out = run("referral_loop", invites_per_user=5, acceptance_rate="40 %", activation_rate=0.6)
    assert out["k"] == 1.2 and out["amplification"] is None
    assert any("unbounded above 1" in w for w in out["warnings"])
    assert out["assumptions"] == ["acceptance_rate 40 read as 0.4 (a percentage)"]
    assert any(m.startswith("users") for m in out["missing"])


def test_referral_loop_refuses_impossible_rates():
    with pytest.raises(ToolError, match="acceptance_rate"):
        run("referral_loop", invites_per_user=2, acceptance_rate=150)
    with pytest.raises(ToolError, match="cycle_days"):
        run("referral_loop", invites_per_user=2, acceptance_rate=0.5, cycle_days=0)


# --- seeding_compare --------------------------------------------------------


def test_seeding_compare_cost_per_review_and_small_base():
    # seeded: 500 / 25 = 20 per review; 500 / 50 000 × 1 000 = 10 per 1 000 reach.
    # organic: 300 / 5 = 60 per review; 300 / 10 000 × 1 000 = 30; 5 reviews < 20 → small base.
    out = run(
        "seeding_compare",
        arms=[
            {"name": "seeded", "units": 100, "cost": 500, "reviews": 25, "reach": 50000},
            {"name": "organic", "units": 100, "cost": 300, "reviews": 5, "reach": 10000},
        ],
    )
    seeded, organic = out["arms"]
    assert seeded["cost_per_review"] == 20.0 and seeded["cost_per_1000_reach"] == 10.0 and seeded["reviews_per_100_units"] == 25.0
    assert organic["cost_per_review"] == 60.0 and organic["cost_per_1000_reach"] == 30.0 and organic["small_base"] is True
    assert out["verdict"]["cheapest_per_review"] == "seeded" and out["verdict"]["confident"] is False
    assert any("only 5 reviews" in w for w in out["warnings"])
    assert "low confidence" in out["summary"]


def test_seeding_compare_is_confident_with_enough_reviews_and_refuses_bad_numbers():
    out = run("seeding_compare", arms=[{"name": "a", "cost": 400, "reviews": 40}, {"name": "b", "cost": 400, "reviews": 20}])
    assert out["verdict"]["confident"] is True and out["ranking_by_cost_per_review"] == ["a", "b"]
    assert "a: reach" in out["missing"]
    with pytest.raises(ToolError, match="cost"):
        run("seeding_compare", arms=[{"name": "a", "cost": -5, "reviews": 3}])


# --- bass -------------------------------------------------------------------


def test_bass_given_p_q_peak_formula():
    # p = 0.03, q = 0.38: t* = ln(0.38/0.03) / 0.41 = 2.539 / 0.41 = 6.19; peak new = m (p+q)² / 4q = 1 000 × 0.1681 / 1.52 = 110.6.
    # First period new = p × m = 30.
    out = run("bass", market_size=1000, p=0.03, q=0.38, horizon=20)
    assert out["assumptions"]["source"] == "given"
    assert out["forecast"][0]["new"] == 30.0
    assert out["theoretical_peak"] == {"periods_from_launch": 6.2, "new_adopters": 110.6, "note": "continuous Bass formula, counted from the first period of adoption"}
    assert out["peak"]["where"] == "forecast" and out["peak"]["period"] in (7, 8)
    assert abs(out["peak"]["new_adopters"] - 110.6) < 2
    assert out["period_when_90pct_reached"] == 12
    assert out["warnings"] == []


def test_bass_fits_p_and_q_from_an_exact_history():
    m, p, q = 10000, 0.02, 0.3
    cumulative, history = 0.0, []
    for _ in range(8):
        new = p * (m - cumulative) + q * cumulative * (1 - cumulative / m)
        history.append(round(new, 3))
        cumulative += new
    out = run("bass", market_size=m, history=history, horizon=30)
    a = out["assumptions"]
    assert a["source"] == "fitted" and a["p"] == 0.02 and a["q"] == 0.3 and a["fit_r2"] == 1.0
    assert a["history_periods"] == 8 and out["forecast"][0]["period"] == 9
    assert out["forecast"][-1]["share_of_pool_pct"] > 99


def test_bass_defaults_are_flagged_and_impossible_inputs_refused():
    out = run("bass", market_size=1000, history=[10, 20, 30], horizon=5)
    assert out["assumptions"]["source"] == "default" and out["assumptions"]["p"] == 0.03 and out["assumptions"]["q"] == 0.38
    assert any("generic defaults" in w and "3 history periods" in w for w in out["warnings"])
    assert out["missing"] == ["p and q (or at least 6 periods of history)"]
    with pytest.raises(ToolError, match="market_size is required"):
        run("bass", p=0.03, q=0.38)
    with pytest.raises(ToolError, match="exceed market_size"):
        run("bass", market_size=100, adopters_to_date=150, p=0.03, q=0.38)
    with pytest.raises(ToolError, match="both p and q"):
        run("bass", market_size=100, p=0.03)


# --- buyer_overlap ----------------------------------------------------------


def panel_rows():
    rows = []
    for b in (1, 2, 3, 4, 5, 6):
        rows.append({"buyer": f"h{b}", "brand": "A", "units": 1})
    for b in (1, 2, 3, 4, 7):
        rows.append({"buyer": f"h{b}", "brand": "B", "units": 2})
    for b in (8, 9, 10, 5):
        rows.append({"buyer": f"h{b}", "brand": "C", "units": 1})
    return rows


def test_buyer_overlap_duplication_law_finds_the_partition():
    # 10 buyers; A 6 (60 %), B 5 (50 %), C 4 (40 %). A→B = 4/6 = 66.7 %, B→A = 4/5 = 80 %.
    # D = Σ obs·pen / Σ pen² over the 6 ordered pairs = 1.03 / 1.54 = 0.669.
    # Expected A→B = 0.669 × 50 % = 33.4 %, ratio 1.99; B→A = 0.669 × 60 % = 40.1 %, ratio 1.99 → partition {A, B}.
    out = run("buyer_overlap", rows=panel_rows(), min_buyers=3)
    assert out["n_buyers"] == 10 and out["status"] == "ok"
    assert out["brands"][0] == {"brand": "A", "buyers": 6, "penetration_pct": 60.0}
    assert out["duplication_coefficient_d"] == 0.669
    ab = next(p for p in out["pairs"] if p["a"] == "A" and p["b"] == "B")
    assert ab["observed_pct"] == 66.7 and ab["expected_independence_pct"] == 50.0 and ab["expected_duplication_law_pct"] == 33.4
    assert ab["ratio"] == 1.99 and ab["above_threshold"] is True
    assert out["partitions"] == [{"brands": ["A", "B"], "mean_ratio": 1.99, "pairs": 1}]
    assert "A, B" in out["summary"]


def test_buyer_overlap_independence_baseline_and_min_buyers():
    # Against independence A→B = 66.7 / 50 = 1.33 → below 1.5, no partition.
    out = run("buyer_overlap", rows=panel_rows(), min_buyers=3, baseline="independence")
    ab = next(p for p in out["pairs"] if p["a"] == "A" and p["b"] == "B")
    assert ab["ratio"] == 1.33 and out["partitions"] == []
    thin = run("buyer_overlap", rows=panel_rows(), min_buyers=5)
    assert [p["a"] for p in thin["pairs"]] == ["A", "B"] or len(thin["pairs"]) == 2
    assert any("C: 4 buyers" in w for w in thin["warnings"])
    none = run("buyer_overlap", rows=panel_rows())
    assert none["status"] == "insufficient_data" and none["pairs"] == []


def test_buyer_overlap_refuses_one_brand():
    with pytest.raises(ToolError, match="at least two brands"):
        run("buyer_overlap", rows=[{"buyer": "h1", "brand": "A"}, {"buyer": "h2", "brand": "A"}])


# --- network_value ----------------------------------------------------------


def test_network_value_reach_gain_is_an_upper_bound_without_overlap():
    # 3 200 → 48 200 (+45 000, +1 406.25 %); relevant to 38 % → 17 100.
    # break-even users = 10 000 / (10 − 2) = 1 250.
    out = run(
        "network_value",
        users=3200,
        partner_users=45000,
        counterparty_relevance=0.38,
        interactions_per_user=10,
        value_per_interaction=2,
        fee_per_user=10,
        fixed_costs=10000,
        cost_per_user=2,
    )
    reach = out["reach"]
    assert reach["reachable_after"] == 48200 and reach["gain_abs"] == 45000 and abs(reach["gain_pct"] - 1406.25) < 0.1
    assert reach["bound"] == "upper_bound" and out["status"] == "partial"
    assert reach["effective_gain"] == 17100
    assert reach["models"]["linear"]["value_index_gain_pct"] == reach["gain_pct"]
    assert reach["models"]["metcalfe"]["value_index_gain_pct"] > reach["models"]["linear"]["value_index_gain_pct"]
    assert "model" in reach["models"]["metcalfe"]["label"]
    assert out["value_per_user"]["value_per_user"] == 20.0 and out["break_even_users"] == 1250
    assert "upper bound" in out["summary"]


def test_network_value_with_overlap_and_a_fee_above_value():
    # 1 000 + 4 000 × (1 − 0.25) = 4 000 → +3 000, +300 %. value per user 2 × 3 = 6 < fee 12 → warning.
    # Metcalfe users to cover the fee: 1 + 999 × 12 / 6 = 1 999.
    out = run("network_value", users=1000, partner_users=4000, overlap_fraction=0.25, interactions_per_user=3, value_per_interaction=2, fee_per_user=12)
    assert out["reach"]["reachable_after"] == 4000 and out["reach"]["gain_pct"] == 300.0 and out["reach"]["bound"] == "estimate"
    assert out["status"] == "ok"
    assert out["fee_check"]["verdict"].startswith("fee at or above")
    assert out["fee_check"]["users_where_metcalfe_value_covers_fee"] == 1999
    assert any("at or above the value" in w for w in out["warnings"])
    with pytest.raises(ToolError, match="users"):
        run("network_value", users=0, partner_users=10)


# --- commission_compare -----------------------------------------------------


def reps_args():
    return {
        "products": [
            {"name": "basic", "price": 1200, "variable_cost": 900, "units_per_period": 20, "discount_pct": 15},
            {"name": "pro", "price": 2400, "variable_cost": 1400, "units_per_period": 3},
        ],
        "plans": [{"name": "A", "kind": "revenue", "rate_pct": 5}, {"name": "B", "kind": "gross_profit", "rate_pct": 15}],
        "sellers": 6,
    }


def test_commission_compare_pays_and_alignment():
    # basic at 15 % off: net 1 020, GP 120; pro: GP 1 000.
    # A (5 % revenue): basic 51, pro 120; total 20 × 51 + 3 × 120 = 1 380; discount cuts pay 15 %, GP 60 %.
    # B (15 % GP): basic 18, pro 150; total 20 × 18 + 3 × 150 = 810.
    # total GP = 2 400 + 3 000 = 5 400 → neutral GP rate 1 380 / 5 400 = 25.6 %.
    out = run("commission_compare", **reps_args())
    a, b = out["plans"]
    assert [p["commission_per_unit"] for p in a["per_product"]] == [51.0, 120.0]
    assert [p["commission_per_unit"] for p in b["per_product"]] == [18.0, 150.0]
    assert a["payout_per_rep"] == 1380.0 and b["payout_per_rep"] == 810.0
    assert a["payroll"] == 8280.0
    sens = a["per_product"][0]["discount_sensitivity"]
    assert sens["commission_change_pct"] == -15.0 and sens["gross_profit_change_pct"] == -60.0
    assert b["per_product"][0]["discount_sensitivity"]["commission_change_pct"] == -60.0
    assert out["neutral_gp_rate_pct"] == 25.6
    assert a["alignment"]["aligned"] is False and "3.5×" in a["alignment"]["findings"][0]
    assert b["alignment"]["aligned"] is True
    assert "A rewards lower-margin sales" in out["summary"]


def test_commission_compare_base_threshold_and_missing_cost():
    # base 500 + 10 % of revenue above 20 000: revenue 27 600 → 500 + 760 = 1 260.
    args = reps_args()
    args["plans"] = [{"name": "quota", "kind": "revenue", "rate_pct": 10, "base": 500, "threshold": 20000}]
    out = run("commission_compare", **args)
    assert out["plans"][0]["payout_per_rep"] == 1260.0
    args = reps_args()
    del args["products"][1]["variable_cost"]
    out = run("commission_compare", **args)
    assert "pro: variable_cost (gross-profit rows omitted)" in out["missing"]
    assert out["plans"][1]["per_product"][1]["commission_per_unit"] is None
    assert out["plans"][1]["payout_per_rep"] is None and out["neutral_gp_rate_pct"] is None
    with pytest.raises(ToolError, match="kind must be"):
        run("commission_compare", products=reps_args()["products"], plans=[{"kind": "bonus", "rate_pct": 5}])


# --- catchment_compare ------------------------------------------------------


def sites():
    return [
        {"name": "A", "bands": [{"label": "0-5 min", "population": 1000}, {"label": "5-10 min", "population": 500}], "competitors": 1, "drive_time_minutes": 10, "rent": 2400},
        {"name": "B", "bands": [{"label": "0-5 min", "population": 600}, {"label": "5-10 min", "population": 2000}], "competitors": 0, "drive_time_minutes": 5, "rent": 3100},
    ]


def test_catchment_compare_scores_with_stated_weights():
    # A weighted pop = 1 000 + 0.5 × 500 = 1 250; B = 600 + 1 000 = 1 600. Per competitor: A 625, B 1 600.
    # Components A: 1 250/1 600 = 0.781, 625/1 600 = 0.391, 5/10 = 0.5 → 0.5 × 0.781 + 0.3 × 0.391 + 0.2 × 0.5 = 0.608; B = 1.
    out = run("catchment_compare", sites=sites())
    a, b = out["sites"]
    assert a["weighted_population"] == 1250.0 and b["weighted_population"] == 1600.0
    assert a["components"] == {"population": 0.781, "competitors": 0.391, "drive_time": 0.5}
    assert a["score"] == 0.608 and b["score"] == 1.0
    assert out["best"] == "B" and out["weights"] == {"population": 0.5, "competitors": 0.3, "drive_time": 0.2}
    assert out["robust_to_weights"] is True and all(s["same_winner"] for s in out["sensitivity"])
    assert a["rent_per_1000_weighted_residents"] == 1920.0  # 2 400 / 1 250 × 1 000


def test_catchment_compare_weights_can_flip_the_winner():
    # A: big near population, many competitors; B: smaller but no competitors. Population-only picks A.
    out = run(
        "catchment_compare",
        sites=[
            {"name": "A", "bands": [{"population": 3000}], "competitors": 4, "drive_time_minutes": 5},
            {"name": "B", "bands": [{"population": 1000}], "competitors": 0, "drive_time_minutes": 5},
        ],
        weights={"population": 0.2, "competitors": 0.8, "drive_time": 0},
    )
    assert out["best"] == "B" and out["robust_to_weights"] is False
    pop_only = next(s for s in out["sensitivity"] if s["weights"] == "population_only")
    assert pop_only["best"] == "A"
    with pytest.raises(ToolError, match="bands"):
        run("catchment_compare", sites=[{"name": "A", "competitors": 1}])
    with pytest.raises(ToolError, match="population must be at least 0"):
        run("catchment_compare", sites=[{"name": "A", "bands": [{"population": -5}]}])


# --- account_penetration ----------------------------------------------------


def test_account_penetration_headroom_and_verdict():
    # 20 / 100 = 20 %; 80 remaining. Ceiling 40: 10 × 30 + 10 × 10 = 400 headroom; revenue 400 of 800 = 50 %.
    # Growth 100, 80 % from existing = 80 → 400 / 80 = 5 periods; penetration < 50 % → mixed.
    out = run("account_penetration", universe_accounts=100, customer_accounts=20, revenues=[10] * 10 + [30] * 10, ceiling=40, growth_last_period=100, growth_from_existing_pct=80)
    assert out["penetration_pct"] == 20.0 and out["accounts_remaining"] == 80
    assert out["expansion_headroom"] == 400.0 and out["share_of_ceiling_pct"] == 50.0
    assert out["growth_split"]["from_existing"] == 80.0 and out["growth_split"]["from_new"] == 20.0
    assert out["headroom_periods_at_current_growth"] == 5.0
    assert out["verdict"] == "mixed" and out["assumptions"] == []
    assert "20 / 100 = 20 %" in out["summary"]


def test_account_penetration_assumed_ceiling_and_shift_verdict():
    # Revenues 1..10: 90th percentile (inclusive) = 9.1; headroom = Σ max(0, 9.1 − r) = 8.1+7.1+...+0.1 = 36.9 → 2 decimals 36.9.
    # Growth 100, all from existing → 36.9 / 100 = 0.4 periods → shift_to_acquisition.
    out = run("account_penetration", universe_accounts=50, customer_accounts=10, revenues=list(range(1, 11)), growth_last_period=100, growth_from_existing_pct=100)
    assert out["ceiling"] == 9.1 and out["expansion_headroom"] == 36.9
    assert out["assumptions"][0].startswith("ceiling 9.10 per account assumed from the 90th percentile")
    assert out["verdict"] == "shift_to_acquisition"
    with pytest.raises(ToolError, match="exceed universe"):
        run("account_penetration", universe_accounts=10, customer_accounts=12)
    with pytest.raises(ToolError, match="universe_accounts is required"):
        run("account_penetration", customer_accounts=12)
    with pytest.raises(ToolError, match="negative"):
        run("account_penetration", universe_accounts=100, customer_accounts=2, revenues=[5, -1])


# --- mental_availability ----------------------------------------------------


def survey_rows():
    rows = []
    for r in range(1, 11):
        if r <= 4:
            rows.append({"respondent": f"r{r}", "situation": "S1", "brand": "F"})
        if r <= 6:
            rows.append({"respondent": f"r{r}", "situation": "S1", "brand": "G"})
        else:
            rows.append({"respondent": f"r{r}", "situation": "S1", "brand": "H"})
    for r in range(1, 6):
        rows.append({"respondent": f"r{r}", "situation": "S2", "brand": "G" if r <= 3 else "H"})
    return rows


def test_mental_availability_per_situation_and_overall():
    # S1: 10 respondents, F 4 (40 %), G 6 (60 %), H 4 → F rank 2, gap −20 pp. S2: 5 respondents, F 0 → missing.
    # Mental penetration 4 / 10 = 40 %; mentions 4 + 6 + 4 + 3 + 2 = 19 → F share 4/19 = 21.1 %.
    out = run("mental_availability", rows=survey_rows(), focus_brand="f")
    s1, s2 = out["situations"]
    assert s1["n"] == 10 and s1["focus_pct"] == 40.0 and s1["rank"] == 2 and s1["leader"] == "G" and s1["gap_pp"] == -20.0
    assert s2["focus_pct"] == 0.0 and s2["rank"] is None and s2["low_confidence"] is True
    assert out["mental_penetration_pct"] == 40.0 and out["mental_market_share_pct"] == 21.1
    assert out["missing_from"] == ["S2"] and out["strongest"] == "S1"
    assert out["focus_brand"] == "f"


def test_mental_availability_excludes_aided_rows_and_respects_min_n():
    rows = survey_rows() + [{"respondent": "r1", "situation": "S2", "brand": "F", "aided": True}, {"respondent": "r9", "situation": "", "brand": "F"}]
    out = run("mental_availability", rows=rows, focus_brand="F", min_n=5, n_respondents=12)
    assert "1 aided rows excluded" in out["missing"] and any(m.startswith("1 rows with a blank") for m in out["missing"])
    assert all(s["low_confidence"] is False for s in out["situations"])
    assert out["n_respondents"] == 12 and out["mental_penetration_pct"] == 33.3  # 4 / 12
    with pytest.raises(ToolError, match="focus_brand is required"):
        run("mental_availability", rows=survey_rows())
    with pytest.raises(ToolError, match="below the 10 distinct"):
        run("mental_availability", rows=survey_rows(), focus_brand="F", n_respondents=4)


# --- benchmark_fit ----------------------------------------------------------


def ipa():
    return {
        "metric": "brand share of budget",
        "value": 60,
        "unit": "%",
        "source_url": "https://ipa.co.uk/knowledge/effectiveness",
        "selection": "awards",
        "industries": ["FMCG", "retail", "finance"],
        "markets": ["GB", "US"],
        "budget_min": 500000,
        "objective": "mixed",
        "sample_n": 1400,
        "period": "1980–2022",
    }


def test_benchmark_fit_awards_population_is_not_comparable_and_never_a_point():
    # budget_ratio = 500 000 / 18 000 = 27.8. f = 0.15 + 3 × 0.2 + 2 × 0.1 (partials) + 0.3 = 1.25 → range 60/2.25 = 26.7 … 60 × 2.25 = 135.
    out = run("benchmark_fit", benchmark=ipa(), own={"industry": "B2B software", "market": "LT", "budget": 18000, "objective": "activation", "period": "2026"})
    assert out["selection_bias"]["flag"] is True
    assert out["fit"]["market"]["fit"] == "mismatch" and out["fit"]["industry"]["fit"] == "mismatch" and out["fit"]["budget"]["fit"] == "mismatch"
    assert out["budget_ratio"] == 27.8
    assert out["verdict"] == "not_comparable" and out["mismatches"] == ["industry", "market", "budget"]
    assert out["expectation_range"]["low"] == 26.667 and out["expectation_range"]["high"] == 135.0
    assert "use it as a hypothesis only" in out["caveat"]
    assert "never quote the benchmark value as a point" in out["expectation_range"]["note"]


def test_benchmark_fit_applicable_when_everything_matches():
    bench = {**ipa(), "selection": "panel", "industries": ["B2B software"], "markets": ["LT"], "budget_min": 10000, "budget_max": 50000, "objective": "activation", "period": "2024–2025"}
    out = run("benchmark_fit", benchmark=bench, own={"industry": "B2B software", "market": "LT", "budget": 18000, "objective": "activation", "period": "2026"})
    assert out["verdict"] == "applicable" and out["fit_score_pct"] == 100.0 and out["mismatches"] == []
    # f = 0.15 → 60 / 1.15 = 52.174, 60 × 1.15 = 69
    assert out["expectation_range"] == {"low": 52.174, "high": 69.0, "unit": "%", "spread_factor": 1.15, "note": out["expectation_range"]["note"]}


def test_benchmark_fit_scores_the_brands_own_comparable_set_first():
    # 9 values: median 2.9, Q1 2.4, Q3 3.3 (inclusive); 3.1 → within IQR, 6 of 9 below → percentile 67.
    history = {"values": [1.8, 2.2, 2.4, 2.6, 2.9, 3.0, 3.3, 3.6, 4.1], "new_value": 3.1}
    out = run("benchmark_fit", benchmark={"metric": "blog CTA CTR", "value": 1.2, "unit": "%", "selection": "platform", "industries": ["cross-industry"]}, own={"industry": "B2B software"}, own_history=history)
    h = out["own_history"]
    assert h["status"] == "ok" and h["median"] == 2.9 and h["q1"] == 2.4 and h["q3"] == 3.3
    assert h["verdict"] == "within_iqr" and h["percentile"] == 67 and h["rank_of_n"] == "4 of 10"
    assert out["fit"]["industry"]["fit"] == "mismatch" and out["verdict"] == "unknown"
    assert out["summary"].startswith("Against 9 comparable own pieces the new value 3.1 is within iqr")
    thin = run("benchmark_fit", benchmark={"metric": "x", "value": 1}, own_history={"values": [1, 2, 3], "new_value": 2})
    assert thin["own_history"]["status"] == "insufficient_own_history" and "median" not in thin["own_history"]
    with pytest.raises(ToolError, match="selection must be"):
        run("benchmark_fit", benchmark={"metric": "x", "value": 1, "selection": "guess"})
    with pytest.raises(ToolError, match="benchmark.value is required"):
        run("benchmark_fit", benchmark={"metric": "x"})


# --- line_metrics -----------------------------------------------------------


def test_line_metrics_counts_words_syllables_and_reading_time():
    # A: Šva-ru grei-ta čia pat = 2+2+1+1 = 6 syllables, 4 words → 4/200 × 60 = 1.2 s; 'greita' is a benefit cue.
    # B: Mes iš-skalb-si-me jū-sų dra-bu-žius ko-ky-biš-kai = 1+4+2+3+4 = 14, 5 words → 1.5 s.
    out = run("line_metrics", lines=["Švaru, greita, čia pat", "Mes išskalbsime jūsų drabužius kokybiškai"], language="lt")
    a, b = out["lines"]
    assert a["words"] == 4 and a["syllables"] == 6 and a["reading_seconds"] == 1.2 and a["first_line_benefit"] is True
    assert b["words"] == 5 and b["syllables"] == 14 and b["reading_seconds"] == 1.5
    assert out["shortest"] == "line 1" and out["rhyme_pairs"] == []
    assert "not memorability" in out["note"]


def test_line_metrics_english_benefit_rhyme_and_empty_lines():
    out = run(
        "line_metrics",
        lines=[{"name": "A", "text": "Save two hours every week."}, {"name": "B", "text": "Our platform is the leader in its space."}, {"name": "C", "text": "Ship in minutes, be the geek."}, {"name": "D", "text": ""}],
        language="en",
        wpm=300,
    )
    a, b, c = out["lines"]
    assert a["first_line_benefit"] is True and "save" in a["benefit_cues"] and "number" not in a["benefit_cues"]
    assert b["first_line_benefit"] is False and out["without_first_line_benefit"] == ["B"]
    assert c["first_line_benefit"] is True  # 'in minutes'
    assert a["words"] == 5 and a["reading_seconds"] == 1.0  # 5 / 300 × 60
    assert out["rhyme_pairs"] == [["A", "C"]]  # week / geek
    assert "D: empty" in out["missing"]
    with pytest.raises(ToolError, match="language must be"):
        run("line_metrics", lines=["x"], language="fr")
    with pytest.raises(ToolError, match="lines is required"):
        run("line_metrics")


# --- audience_panel ---------------------------------------------------------


def test_audience_panel_coverage_and_gaps():
    # 7 F, 3 M against 50/50: observed 70/30 → coverage min(70,50) + min(30,50) = 80; M short by 20 pp, 2 more respondents.
    out = run("audience_panel", respondents=[{"gender": "F", "age": "25-34"}] * 7 + [{"gender": "M", "age": "35-44"}] * 3, target={"gender": {"F": 50, "M": 50}, "age": {"25-34": 60, "35-44": 40}})
    gender, age = out["dimensions"]
    assert gender["coverage_pct"] == 80.0 and gender["gaps"] == [{"value": "M", "short_by_pp": 20.0, "respondents_needed": 2}]
    # age 70/30 vs 60/40 → coverage 60 + 30 = 90; 35-44 short by 10 pp, 0.4 × 10 − 3 = 1 more respondent
    assert age["coverage_pct"] == 90.0 and age["gaps"] == [{"value": "35-44", "short_by_pp": 10.0, "respondents_needed": 1}]
    assert out["overall_coverage_pct"] == 80.0
    assert any("under 30" in w for w in out["warnings"])
    assert "gender M (−20 pp)" in out["summary"]


def test_audience_panel_blanks_unexpected_values_and_bad_targets():
    out = run("audience_panel", respondents=[{"region": "Vilnius"}, {"region": "Kaunas"}, {"region": ""}, {"region": "Riga"}], target={"region": {"Vilnius": 50, "Kaunas": 50}})
    region = out["dimensions"][0]
    assert region["n"] == 3 and region["unexpected"] == ["Riga"]
    assert "region: 1 respondents without a value" in out["missing"]
    assert region["values"][0]["observed_pct"] == 33.3
    with pytest.raises(ToolError, match="sum to 90"):
        run("audience_panel", respondents=[{"g": "a"}], target={"g": {"a": 40, "b": 50}})
    with pytest.raises(ToolError, match="target"):
        run("audience_panel", respondents=[{"g": "a"}])



def test_dispatch_routes_campaign_calc():
    import json as _json

    from rocky.tools import WorkspaceTools, dispatch, worker_tools

    out = _json.loads(dispatch(WorkspaceTools("."), "campaign_calc", {"mode": "referral_loop", "invites_per_user": 3, "acceptance_rate": 0.4, "activation_rate": 0.5}))
    assert out["k"] == 0.6 if "k" in out else True
    assert "campaign_calc" in [t["function"]["name"] for t in worker_tools(False)]
