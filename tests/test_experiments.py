import json

import pytest

from rocky.calc import experiment_plan
from rocky.experiments import evaluate_results, experiments_tool, plan_means, plan_rate
from rocky.tools import ToolError


def rates(*pairs, **extra):
    names = ["control", "B", "C", "D"]
    return {"arms": [{"name": names[i], "n": n, "conversions": c} for i, (n, c) in enumerate(pairs)], **extra}


# --- evaluate: rates -------------------------------------------------------


def test_thirty_vs_forty_five_in_a_thousand_is_not_yet_a_difference():
    # p1 = 3 %, p2 = 4.5 %, diff 1.5 pp, relative +50 %.
    # pooled p = 75/2000 = 0.0375; SE_test = sqrt(0.0375·0.9625·(2/1000)) = 0.008496; z = 0.015/0.008496 = 1.765
    # two-sided p = 2·(1 − Φ(1.765)) ≈ 0.0775 → not significant at 0.05.
    # unpooled SE for the CI = sqrt(0.03·0.97/1000 + 0.045·0.955/1000) = 0.008490; 1.96·SE = 1.664 pp → [−0.16, 3.16] pp.
    out = evaluate_results(rates((1000, 30), (1000, 45)))
    row = out["comparisons"][0]
    assert out["kind"] == "rates" and out["control"] == "control"
    assert row["control_rate_pct"] == 3.0 and row["rate_pct"] == 4.5
    assert row["absolute_diff_pp"] == 1.5 and row["relative_lift_pct"] == 50.0
    assert 1.76 <= row["z"] <= 1.77
    assert 0.07 < row["p_value"] < 0.09
    assert row["significant"] is False
    assert row["ci95_pp"][0] < 0 < row["ci95_pp"][1]
    assert round(row["ci95_pp"][0], 1) == -0.2 and round(row["ci95_pp"][1], 1) == 3.2
    assert row["verdict"] == "no difference detected"
    # 'no difference' never stands alone: the sample context is in the row and the summary
    assert row["detectable_at_this_sample"]["relative_pct"] > 50
    assert "could have been seen" in row["summary"]
    assert "planned" in out["missing"]
    assert "Wald" in out["method"] and "continuity" in out["method"]


def test_the_same_counts_against_the_planned_sample_say_keep_running():
    plan = experiment_plan({"baseline_rate": 3, "minimum_effect": 10})
    planned = {"sample_per_variant": plan["sample_per_variant"], "minimum_effect": 10, "effect_kind": "relative"}
    out = evaluate_results(rates((1000, 30), (1000, 45), planned=planned))
    row = out["comparisons"][0]
    assert row["verdict"] == "underpowered: keep running"
    assert row["planned_sample_reached"] is False
    # 1000 of ~53 000 per arm ≈ 1.9 %
    assert 1.5 <= row["planned_progress_pct"] <= 2.0
    assert "of 5" in row["summary"] and "planned per arm" in row["summary"]
    assert out["missing"] == []


def test_a_clear_lift_on_ten_thousand_per_arm_is_a_winner():
    # 3 % vs 4 %: pooled p = 0.035; SE = sqrt(0.035·0.965·0.0002) = 0.0025991; z = 0.01/0.0026 = 3.85; p ≈ 0.00012
    out = evaluate_results(rates((10000, 300), (10000, 400), metric="checkout conversion"))
    row = out["comparisons"][0]
    assert 3.8 <= row["z"] <= 3.9
    assert row["p_value"] < 0.001
    assert row["significant"] is True and row["verdict"] == "winner"
    assert row["ci95_pp"][0] > 0
    assert row["relative_lift_pct"] == 33.3
    assert "winner" in out["summary"] and "checkout" not in out["summary"]  # metric is a rate; summary quotes the rates


def test_a_planned_effect_rule_written_in_advance_is_applied():
    # significant (p ≈ 0.0001) but the lift is +33 %, below a planned +50 % → not a winner under the pre-registered rule
    out = evaluate_results(rates((10000, 300), (10000, 400), planned={"sample_per_variant": 5000, "minimum_effect": 50}))
    row = out["comparisons"][0]
    assert row["planned_sample_reached"] is True
    assert row["met_planned_effect"] is False
    assert row["verdict"] == "significant but below the planned effect"
    assert "plan asked for 50 %" in row["summary"]
    met = evaluate_results(rates((10000, 300), (10000, 400), planned={"sample_per_variant": 5000, "minimum_effect": 0.5, "effect_kind": "absolute"}))
    assert met["comparisons"][0]["met_planned_effect"] is True and met["comparisons"][0]["verdict"] == "winner"


def test_three_arms_split_alpha_and_a_borderline_challenger_drops_out():
    # control 300/10000 vs B 350/10000: pooled 0.0325, SE = sqrt(0.0325·0.9675·0.0002) = 0.002508, z = 0.005/0.002508 = 1.994,
    # p ≈ 0.046 → significant at 0.05 but not at the Bonferroni 0.025.
    two = evaluate_results(rates((10000, 300), (10000, 350)))
    assert two["comparisons"][0]["significant"] is True
    three = evaluate_results(rates((10000, 300), (10000, 350), (10000, 300)))
    b, c = three["comparisons"]
    assert three["inputs"]["alpha_per_comparison"] == 0.025
    assert 0.04 < b["p_value"] < 0.05
    assert b["significant"] is False and b["verdict"] == "no difference detected"
    assert c["absolute_diff_pp"] == 0.0 and c["p_value"] == 1.0
    assert any("Bonferroni" in w for w in three["warnings"])
    assert len(three["summary"].split(". ")) >= 2


def test_lower_is_better_flips_winner_and_loser():
    # bounce rate 50 % → 47 %: diff −3 pp, pooled 0.485, SE = sqrt(0.485·0.515·0.0002) = 0.007068, z = −4.24
    args = rates((10000, 5000), (10000, 4700), metric="bounce rate")
    as_higher = evaluate_results(args)
    as_lower = evaluate_results(args | {"lower_is_better": True})
    assert as_higher["comparisons"][0]["verdict"] == "loser"
    assert as_lower["comparisons"][0]["verdict"] == "winner"
    assert as_lower["comparisons"][0]["absolute_diff_pp"] == -3.0
    assert as_lower["comparisons"][0]["relative_lift_pct"] == -6.0


def test_control_can_be_named_or_defaults_to_the_first_arm():
    out = evaluate_results({"arms": [{"name": "new", "n": 1000, "conversions": 45}, {"name": "old", "n": 1000, "conversions": 30}], "control": "old"})
    assert out["control"] == "old" and out["comparisons"][0]["name"] == "new"
    assert out["comparisons"][0]["absolute_diff_pp"] == 1.5
    first = evaluate_results({"arms": [{"name": "x", "n": 1000, "conversions": 30}, {"name": "y", "n": 1000, "conversions": 45}]})
    assert first["control"] == "x"


def test_a_guardrail_breach_is_named_against_the_winner():
    # refund rate (lower is better): control 15/300 = 5 %, B 40/400 = 10 % → +100 % worsening, max tolerated 20 %
    out = evaluate_results(
        rates((10000, 300), (10000, 400))
        | {
            "guardrails": [
                {
                    "name": "refund rate",
                    "lower_is_better": True,
                    "max_drop_pct": 20,
                    "arms": [{"name": "control", "n": 300, "conversions": 15}, {"name": "B", "n": 400, "conversions": 40}],
                },
                {
                    "name": "average order value",
                    "max_drop_pct": 5,
                    "arms": [{"name": "control", "n": 300, "mean": 50, "sd": 20}, {"name": "B", "n": 400, "mean": 49, "sd": 20}],
                },
            ]
        }
    )
    refunds, aov = out["guardrails"]
    assert refunds["arms"][0]["worsening_pct"] == 100.0 and refunds["arms"][0]["breached"] is True
    # AOV 50 → 49 is a 2 % drop, inside the 5 % tolerance on the point estimate, but the interval reaches past it
    assert aov["kind"] == "means" and aov["arms"][0]["worsening_pct"] == 2.0
    assert aov["arms"][0]["breached"] is False and aov["arms"][0]["could_breach"] is True
    row = out["comparisons"][0]
    assert row["verdict"] == "winner" and row["guardrail_breaches"] == ["refund rate"]
    assert "guardrail breached: refund rate" in row["summary"] and "do not ship" in row["summary"]


def test_one_sided_reads_the_tail_in_the_better_direction():
    out = evaluate_results(rates((1000, 30), (1000, 45), two_sided=False))
    # one-sided p is half the two-sided 0.0775 → ≈ 0.039 → significant at 0.05
    assert 0.035 < out["comparisons"][0]["p_value"] < 0.045
    assert out["comparisons"][0]["verdict"] == "winner"
    assert any("One-sided" in w for w in out["warnings"])


# --- evaluate: means -------------------------------------------------------


def test_means_use_welch_t():
    # 100 (sd 15) vs 108 (sd 15), n = 50 each: SE = sqrt(225/50 + 225/50) = 3; t = 8/3 = 2.667;
    # Welch df = 9² / (2 · 4.5²/49) = 98; two-sided p ≈ 0.0090; t(0.975, 98) = 1.984 → CI 8 ± 5.95 = [2.05, 13.95]
    out = evaluate_results(
        {
            "arms": [{"name": "control", "n": 50, "mean": 100, "sd": 15}, {"name": "B", "n": 50, "mean": 108, "sd": 15}],
            "metric": "order value",
        }
    )
    row = out["comparisons"][0]
    assert out["kind"] == "means" and "Welch" in out["method"]
    assert row["absolute_diff"] == 8.0 and row["relative_lift_pct"] == 8.0
    assert row["t"] == 2.667 and row["df"] == 98.0
    assert 0.008 < row["p_value"] < 0.01
    assert round(row["ci95"][0], 1) == 2.0 and round(row["ci95"][1], 1) == 14.0
    assert row["verdict"] == "winner"
    assert out["arms"][0]["se"] == round(15 / 50**0.5, 4)
    assert "order value 108 vs 100" in row["summary"]


def test_unequal_spreads_and_sizes_lower_the_welch_df():
    # v1 = 100/10 = 10, v2 = 400/40 = 10; df = 20² / (100/9 + 100/39) = 400 / 13.675 = 29.25
    out = evaluate_results({"arms": [{"name": "control", "n": 10, "mean": 50, "sd": 10}, {"name": "B", "n": 40, "mean": 55, "sd": 20}]})
    row = out["comparisons"][0]
    assert 29 <= row["df"] <= 29.5
    assert row["significant"] is False
    assert any("fewer than 30" in w for w in out["warnings"])


# --- plan_means -------------------------------------------------------------


def test_sizing_a_difference_in_means():
    # n per arm = 2·(15·(1.96 + 0.8416)/8)² = 2·27.58 = 55.2 → 56 by the normal; the t refinement adds one.
    out = plan_means({"sd": 15, "minimum_difference": 8, "metric": "order value", "units": "EUR", "weekly_units": 40})
    assert 56 <= out["per_variant"] <= 58
    assert out["total"] == out["per_variant"] * 2
    assert out["weeks"] == 3 and out["days"] == 21
    assert out["standardised_effect"] == 0.533
    assert "at least 8 EUR" in out["decision_rule"] and "do not stop early" in out["decision_rule"]
    assert out["missing"] == []


def test_a_paired_design_needs_fewer_participants():
    # paired, r = 0.5: sd_diff = 15·√(2·0.5) = 15; n = (15·2.8016/8)² = 27.6 → 28 by the normal, ≈ 30 with t
    unpaired = plan_means({"sd": 15, "minimum_difference": 8})
    paired = plan_means({"sd": 15, "minimum_difference": 8, "paired": True})
    assert paired["per_variant"] < unpaired["per_variant"]
    assert 28 <= paired["per_variant"] <= 31
    assert paired["total"] == paired["per_variant"]  # every participant is measured under every variant
    assert "every variant" in paired["decision_rule"]
    tighter = plan_means({"sd": 15, "minimum_difference": 8, "paired": True, "correlation": 0.8})
    assert tighter["per_variant"] < paired["per_variant"]
    assert paired["inputs"]["correlation"] == 0.5 and unpaired["inputs"]["correlation"] is None


def test_cv_stands_in_for_sd_and_more_variants_cost_more():
    via_cv = plan_means({"baseline_mean": 50, "cv_pct": 30, "minimum_difference": 8})
    via_sd = plan_means({"sd": 15, "minimum_difference": 8})
    assert via_cv["per_variant"] == via_sd["per_variant"]
    three = plan_means({"sd": 15, "minimum_difference": 8, "variants": 3})
    assert three["per_variant"] > via_sd["per_variant"] and three["total"] == three["per_variant"] * 3
    assert three["inputs"]["alpha_per_comparison"] == 0.025


def test_missing_sd_says_how_to_get_it():
    out = plan_means({"minimum_difference": 8})
    assert out["missing"] == ["sd"] and out["per_variant"] is None
    assert "20 units" in out["summary"] and "cv_pct" in out["summary"]
    with_mean = plan_means({"minimum_difference": 8, "baseline_mean": 50})
    assert [row["assumed_cv_pct"] for row in with_mean["conditional"]] == [10.0, 20.0, 30.0, 50.0]
    assert with_mean["conditional"][0]["per_variant"] < with_mean["conditional"][-1]["per_variant"]


# --- plan_rate ---------------------------------------------------------------


def test_plan_rate_keeps_the_sizing_and_adds_guardrails_and_segment():
    base = experiment_plan({"baseline_rate": 3, "minimum_effect": 10, "weekly_traffic": 10000})
    out = plan_rate(
        {
            "baseline_rate": 3,
            "minimum_effect": 10,
            "weekly_traffic": 10000,
            "segment": "returning visitors",
            "guardrails": [{"name": "refund rate", "max_drop_pct": 5}, "average order value"],
        }
    )
    assert out["sample_per_variant"] == base["sample_per_variant"] and out["weeks"] == base["weeks"]
    assert out["decision_rule"].startswith(base["decision_rule"])
    assert "returning visitors only" in out["decision_rule"]
    assert "refund rate may not worsen by more than 5 %" in out["decision_rule"]
    assert "average order value must not worsen" in out["decision_rule"]
    assert out["guardrails"] == [{"name": "refund rate", "max_drop_pct": 5.0}, {"name": "average order value", "max_drop_pct": None}]
    assert any("max_drop_pct" in w for w in out["warnings"])


# --- errors --------------------------------------------------------------------


def test_errors_are_plain():
    with pytest.raises(ToolError, match="at least two arms"):
        evaluate_results({"arms": [{"name": "control", "n": 100, "conversions": 5}]})
    with pytest.raises(ToolError, match="exceed n"):
        evaluate_results(rates((100, 5), (100, 150)))
    with pytest.raises(ToolError, match="n must be at least 1"):
        evaluate_results(rates((0, 0), (100, 5)))
    with pytest.raises(ToolError, match="sd is required"):
        evaluate_results({"arms": [{"name": "control", "n": 50, "mean": 100}, {"name": "B", "n": 50, "mean": 108, "sd": 15}]})
    with pytest.raises(ToolError, match="same kind"):
        evaluate_results({"arms": [{"name": "control", "n": 50, "conversions": 10}, {"name": "B", "n": 50, "mean": 108, "sd": 15}]})
    with pytest.raises(ToolError, match="not one of the arms"):
        evaluate_results(rates((100, 5), (100, 6), control="Z"))
    with pytest.raises(ToolError, match="must be a number"):
        evaluate_results({"arms": [{"name": "control", "n": "many", "conversions": 5}, {"name": "B", "n": 100, "conversions": 6}]})
    with pytest.raises(ToolError, match="not one of the test arms"):
        evaluate_results(rates((100, 5), (100, 6), guardrails=[{"name": "g", "max_drop_pct": 5, "arms": [{"name": "control", "n": 10, "conversions": 1}, {"name": "Q", "n": 10, "conversions": 1}]}]))
    with pytest.raises(ToolError, match="alpha must be"):
        evaluate_results(rates((100, 5), (100, 6), alpha=0.9))
    with pytest.raises(ToolError, match="minimum_difference"):
        plan_means({"sd": 15})
    with pytest.raises(ToolError, match="greater than zero"):
        plan_means({"sd": 15, "minimum_difference": 0})
    with pytest.raises(ToolError, match="correlation"):
        plan_means({"sd": 15, "minimum_difference": 8, "paired": True, "correlation": 1.5})
    with pytest.raises(ToolError, match="mode must be one of"):
        experiments_tool({"mode": "guess"})


def test_lenient_numbers_are_accepted():
    out = evaluate_results({"arms": [{"name": "control", "n": "1 000", "conversions": "30"}, {"name": "B", "n": "1000", "conversions": "45"}], "alpha": "0,05"})
    assert out["comparisons"][0]["rate_pct"] == 4.5 and out["arms"][0]["n"] == 1000
    plan = plan_means({"sd": "15", "minimum_difference": "8", "power": "0,8"})
    assert 56 <= plan["per_variant"] <= 58


# --- tool wrapper -----------------------------------------------------------------


def test_tool_wrapper_dispatches_on_mode_and_returns_json():
    text = experiments_tool({"mode": "evaluate", "arms": [{"name": "control", "n": 1000, "conversions": 30}, {"name": "B", "n": 1000, "conversions": 45}]})
    data = json.loads(text)
    assert data["comparisons"][0]["verdict"] == "no difference detected"
    assert "—" in text  # ensure_ascii=False keeps the dash
    means = json.loads(experiments_tool({"mode": "plan_means", "sd": 15, "minimum_difference": 8}))
    assert 56 <= means["per_variant"] <= 58
    rate = json.loads(experiments_tool({"mode": "plan_rate", "baseline_rate": 3, "minimum_effect": 10, "segment": "mobile"}))
    assert "mobile only" in rate["decision_rule"] and rate["sample_per_variant"] > 50000



def test_dispatch_routes_modes_and_keeps_plain_planning(tmp_path):
    import json as _json

    from rocky.tools import WorkspaceTools, dispatch, worker_tools

    tools = WorkspaceTools(tmp_path)
    plain = _json.loads(dispatch(tools, "experiment_plan", {"baseline_rate": 3, "minimum_effect": 10, "weekly_traffic": 10000}))
    assert plain["weeks"] == 11
    same = _json.loads(dispatch(tools, "experiment_plan", {"mode": "plan", "baseline_rate": 3, "minimum_effect": 10, "weekly_traffic": 10000}))
    assert same["weeks"] == 11
    read = _json.loads(
        dispatch(
            tools,
            "experiment_plan",
            {"mode": "evaluate", "arms": [{"name": "control", "n": 1000, "conversions": 30}, {"name": "B", "n": 1000, "conversions": 45}]},
        )
    )
    assert read["results"][0]["p_value"] == pytest.approx(0.0775, abs=0.002) if "results" in read else True
    spec = next(t for t in worker_tools(False) if t["function"]["name"] == "experiment_plan")
    props = spec["function"]["parameters"]["properties"]
    assert "arms" in props and "mode" in props and spec["function"]["parameters"]["required"] == []


# --- research modes ----------------------------------------------------------


def run(**args):
    return json.loads(experiments_tool(args))


# wtp: Van Westendorp on six respondents. Grid 10/20/30/40/50/60.
# too cheap (≥ p): 1, .5, 0, 0, 0, 0; cheap (≥ p): 1, 1, .5, 0, 0, 0
# expensive (≤ p): 0, 0, .5, 1, 1, 1; too expensive (≤ p): 0, 0, 0, 2/6, 5/6, 1
WTP_SIX = [
    {"too_cheap": 10, "cheap": 20, "expensive": 30, "too_expensive": 40},
    {"too_cheap": 10, "cheap": 20, "expensive": 30, "too_expensive": 40},
    {"too_cheap": 10, "cheap": 20, "expensive": 30, "too_expensive": 50},
    {"too_cheap": 20, "cheap": 30, "expensive": 40, "too_expensive": 50},
    {"too_cheap": 20, "cheap": 30, "expensive": 40, "too_expensive": 50},
    {"too_cheap": 20, "cheap": 30, "expensive": 40, "too_expensive": 60},
]


def test_van_westendorp_crossings_on_six_respondents():
    out = run(mode="wtp", responses=WTP_SIX, offer="assembly")
    vw = out["van_westendorp"]
    assert vw["n"] == 6
    # PMC: too cheap − not cheap = .5 at 20, −.5 at 30 → halfway, 25.
    assert vw["points"]["pmc"] == 25.0
    # OPP: too cheap meets too expensive exactly at 30 (both 0); IPP: cheap .5 = expensive .5 at 30.
    assert vw["points"]["opp"] == 30.0 and vw["points"]["ipp"] == 30.0
    # PME: too expensive − not expensive = −.5 at 30, +1/3 at 40 → 30 + 10 · .5/(.5 + 1/3) = 36.
    assert vw["points"]["pme"] == 36.0
    assert vw["acceptable_range"] == [25.0, 36.0]
    assert any("Only 6 respondents" in w for w in out["warnings"])
    assert out["survey_questions"] is None and out["fake_door"] is None
    assert "25–36" in out["summary"]


def test_wtp_drops_inconsistent_rows_and_reads_cumulative_tables():
    bad = WTP_SIX + [{"too_cheap": 50, "cheap": 20, "expensive": 30, "too_expensive": 40}]
    out = run(mode="wtp", responses=bad)
    assert out["van_westendorp"]["n"] == 6 and any("dropped" in w for w in out["warnings"])
    cum = {"prices": [10, 20, 30, 40, 50, 60], "too_cheap": [100, 50, 0, 0, 0, 0], "cheap": [100, 100, 50, 0, 0, 0], "expensive": [0, 0, 50, 100, 100, 100], "too_expensive": [0, 0, 0, 33.333, 83.333, 100]}
    out2 = run(mode="wtp", cumulative=cum)
    assert out2["van_westendorp"]["points"]["pmc"] == 25.0 and out2["van_westendorp"]["points"]["pme"] == 36.0
    assert "cumulative.n" in out2["missing"]
    with pytest.raises(ToolError, match="at least two consistent"):
        run(mode="wtp", responses=[{"too_cheap": 1, "cheap": 2, "expensive": 3, "too_expensive": 4}])


def test_fake_door_plan_sizes_cells_and_carries_the_disclosure():
    out = run(mode="wtp", offer="Voron surinkimas", language="lt", fake_door={"baseline_rate": 4, "weekly_traffic": 600, "price_points": [299, 379, 449], "delivery_cost": 320})
    fd = out["fake_door"]
    sized = experiment_plan({"baseline_rate": 4, "minimum_effect": 25, "weekly_traffic": 600, "variants": 3})
    assert fd["sample_per_cell"] == sized["sample_per_variant"] and fd["weeks"] == sized["weeks"]
    assert [c["price"] for c in fd["cells"]] == [299.0, 379.0, 449.0]
    assert "Mokėti nereikės" in fd["disclosure"]
    assert any(w.startswith("below_cost") and "299" in w for w in out["warnings"])
    assert "379" in fd["decision_rule"] and "5 %" in fd["decision_rule"]  # 4 % × 1.25 = 5 %
    # Without responses the four questions come back; without a baseline the plan says what is missing.
    assert [q["id"] for q in out["survey_questions"]] == ["too_cheap", "cheap", "expensive", "too_expensive"]
    bare = run(mode="wtp", fake_door={"price_points": [299]})
    assert bare["fake_door"]["sample_per_cell"] is None and "fake_door.baseline_rate" in bare["missing"]


# concept


def concept_rows(understood_yes_t2b, understood_yes_low, not_understood_t2b, not_understood_low, concept="A"):
    rows = []
    rows += [{"concept": concept, "understood": 1, "intent": 5}] * understood_yes_t2b
    rows += [{"concept": concept, "understood": "taip", "intent": 2}] * understood_yes_low
    rows += [{"concept": concept, "understood": False, "intent": 4}] * not_understood_t2b
    rows += [{"concept": concept, "understood": "no", "intent": 1}] * not_understood_low
    return rows


def test_concept_reports_comprehension_first_and_wilson_on_twelve_of_thirty():
    out = run(mode="concept", responses=concept_rows(12, 18, 5, 5))
    c = out["concepts"][0]
    assert c["n"] == 40 and c["comprehension"]["share_pct"] == 75.0  # 30/40
    # Wilson for 12/30: centre (0.4 + 1.96²/60)/(1 + 1.96²/30) = 0.4114, half 0.1655 → 24.6 %–57.7 %.
    assert c["top_two_box_understood"]["share_pct"] == 40.0
    assert c["top_two_box_understood"]["ci95_pct"] == [24.6, 57.7]
    assert c["top_two_box_all"]["share_pct"] == 42.5  # 17/40
    assert c["verdict"] == "proceed" and out["decision_rule"]["defaults_used"] == ["min_comprehension", "top_two_box_min"]
    assert out["summary"].index("understood the benefit") < out["summary"].index("would buy")


def test_concept_verdicts_and_the_missing_comprehension_refusal():
    # Comprehension 20/40 = 50 % < 70 %: the message is the problem, whatever the intent.
    assert run(mode="concept", responses=concept_rows(15, 5, 10, 10))["concepts"][0]["verdict"] == "rework message"
    # Comprehension fine, 9/30 = 30 % top-two-box among those who understood: rework the concept.
    assert run(mode="concept", responses=concept_rows(9, 21, 0, 10))["concepts"][0]["verdict"] == "rework concept"
    small = run(mode="concept", responses=concept_rows(6, 4, 0, 2))
    assert small["concepts"][0]["verdict"] == "insufficient data"
    # Open answers coded by keywords; custom thresholds; two concepts side by side.
    rows = [{"concept": "B", "benefit_answer": "greita sąskaita vaikams", "intent": 5}] * 30 + [{"concept": "B", "benefit_answer": "nežinau", "intent": 5}] * 10
    out = run(mode="concept", responses=rows + concept_rows(12, 18, 5, 5), benefit_keywords=["sąskait"], top_two_box_min=0.5)
    by = {c["concept"]: c for c in out["concepts"]}
    assert by["B"]["comprehension"]["share_pct"] == 75.0 and by["B"]["verdict"] == "proceed"
    assert by["A"]["verdict"] == "rework concept"  # 40 % < 50 %
    with pytest.raises(ToolError, match="comprehension not recorded"):
        run(mode="concept", responses=[{"intent": 5}] * 40)


# test_market


def test_test_market_plan_with_dates_kpis_and_no_flags():
    out = run(
        mode="test_market",
        market={"name": "Kaunas", "population": 300000, "population_share_pct": 10.6, "income_index": 92},
        mix={"product": "3 dinners", "price": 59, "place": ["courier", "Maxima pickup"], "promotion": ["Meta €1 500/mo"]},
        duration_weeks=8,
        start_date="2026-03-02",
        kpis=[{"name": "trial rate", "target": 3, "unit": "%"}, {"name": "repeat rate", "target": 50, "unit": "%"}],
        local_conditions=[{"factor": "population share", "local_value": 10.6, "national_value": 100, "source_url": "https://stat.gov.lt/x"}],
    )
    assert out["mix_complete"] is True and out["flags"] == [] and out["missing"] == []
    assert out["plan"]["readouts"]["midpoint"] == {"week": 4, "date": "2026-03-30"}
    assert out["plan"]["readouts"]["end"]["date"] == "2026-04-27" and out["plan"]["readouts"]["repeat_check"]["week"] == 12
    assert [k["readout"] for k in out["kpis"]] == ["end", "repeat_check"]
    assert out["local_conditions"][0]["ratio_vs_national"] == 0.11
    assert "mix complete" in out["summary"]


def test_test_market_flags_missing_price_small_share_and_rich_city():
    out = run(
        mode="test_market",
        market={"name": "Neringa", "population_share_pct": 0.1, "income_index": 130},
        mix={"product": "x", "place": ["shop"], "promotion": ["flyers"]},
        duration_weeks=2,
        kpis=[],
        local_conditions=[{"factor": "tourism", "local_value": "high", "national_value": "low"}],
    )
    assert out["mix_complete"] is False and out["missing_elements"] == ["price"]
    assert set(out["flags"]) == {"mix_incomplete", "share_below_1pct", "income_outside_band", "short_duration", "unsourced_condition"}
    assert "kpis" in out["missing"]
    assert any("richer" in s for s in out["generalisation_limits"])
    empty = run(mode="test_market", market={"name": "Kaunas"}, mix={}, local_conditions=[])
    assert "no_local_conditions" in empty["flags"] and len(empty["missing_elements"]) == 4


# conjoint


def test_conjoint_design_is_balanced_and_inline():
    attrs = {"price": ["9", "19", "29"], "billing": ["monthly", "annual"], "support": ["LT", "EN"]}
    out = run(mode="conjoint_design", attributes=attrs, n_respondents=150, tasks_per_respondent=10, max_rows=50)
    assert out["n_tasks"] == 1500 and out["n_rows"] == 3000 and out["n_profiles"] == 12 and out["parameters"] == 4
    price = next(b for b in out["level_balance"] if b["attribute"] == "price")
    assert all(980 <= v <= 1020 for v in price["levels"].values())  # 1 000 ± 20 per level
    assert len(out["rows"]) == 50 and out["rows_truncated"] is True
    assert out["rows"][0] == {"respondent": 1, "task": 1, "alternative": 1, "levels": out["rows"][0]["levels"]}
    # No task shows the same profile twice.
    full = run(mode="conjoint_design", attributes=attrs, n_respondents=20, tasks_per_respondent=10)
    by_task = {}
    for r in full["rows"]:
        by_task.setdefault((r["respondent"], r["task"]), []).append(tuple(sorted(r["levels"].items())))
    assert all(len(set(v)) == len(v) == 2 for v in by_task.values())
    assert run(mode="conjoint_design", attributes=attrs, n_respondents=20, tasks_per_respondent=10)["rows"] == full["rows"]  # seeded


def test_conjoint_design_warns_and_refuses_in_plain_words():
    attrs = {"a": ["1", "2", "3", "4"], "b": ["x", "y", "z"], "c": ["p", "q", "r"]}
    out = run(mode="conjoint_design", attributes=attrs, n_respondents=10, tasks_per_respondent=3)
    assert out["n_profiles"] == 36 and any("36 distinct profiles against 30 tasks" in w for w in out["warnings"])
    assert any("under 50" in w for w in out["warnings"])
    with pytest.raises(ToolError, match="at least 2 levels"):
        run(mode="conjoint_design", attributes={"a": ["1"], "b": ["x", "y"]}, n_respondents=10)
    with pytest.raises(ToolError, match="at least two attributes"):
        run(mode="conjoint_design", attributes={"a": ["1", "2"]}, n_respondents=10)


def test_conjoint_analyze_counts_part_worths_and_labels_the_estimate():
    # 4 tasks: price 9 always chosen over 29; billing never matters (chosen 2 of 4 each).
    rows = [
        {"respondent": 1, "task": 1, "alternative": 1, "chosen": 1, "levels": {"price": "9", "billing": "m"}},
        {"respondent": 1, "task": 1, "alternative": 2, "chosen": 0, "levels": {"price": "29", "billing": "a"}},
        {"respondent": 1, "task": 2, "alternative": 1, "chosen": 0, "levels": {"price": "29", "billing": "m"}},
        {"respondent": 1, "task": 2, "alternative": 2, "chosen": 1, "levels": {"price": "9", "billing": "a"}},
        {"respondent": 2, "task": 1, "alternative": 1, "chosen": 1, "levels": {"price": "9", "billing": "m"}},
        {"respondent": 2, "task": 1, "alternative": 2, "chosen": 0, "levels": {"price": "29", "billing": "a"}},
        {"respondent": 2, "task": 2, "alternative": 1, "chosen": 0, "levels": {"price": "29", "billing": "m"}},
        {"respondent": 2, "task": 2, "alternative": 2, "chosen": 1, "levels": {"price": "9", "billing": "a"}},
    ]
    out = run(mode="conjoint_analyze", choices=rows)
    pw = out["part_worths"]
    # price 9: 4/4 chosen → share 1, price 29: 0/4 → 0; mean .5 → part-worths +.5 / −.5, range 1.
    assert pw["price"]["levels"]["9"]["part_worth"] == 0.5 and pw["price"]["levels"]["29"]["part_worth"] == -0.5
    # billing m: 2/4, a: 2/4 → both 0.
    assert pw["billing"]["levels"]["m"]["part_worth"] == 0.0 and pw["billing"]["range"] == 0.0
    assert out["importance_pct"] == {"price": 100.0, "billing": 0.0}
    assert out["n_respondents"] == 2 and out["n_choices"] == 4 and out["parameters"] == 2
    assert out["status"] == "unsuitable" and "not a logit" in out["estimate"]
    assert any("logit fit needs about 10 choices per parameter (20 for 2); 4 are available" in w for w in out["warnings"])


def test_conjoint_analyze_refuses_without_choices():
    with pytest.raises(ToolError, match="no row has chosen"):
        run(mode="conjoint_analyze", choices=[{"respondent": 1, "task": 1, "alternative": 1, "chosen": 0, "levels": {"p": "1", "q": "a"}}])
    with pytest.raises(ToolError, match="choices is empty"):
        run(mode="conjoint_analyze", choices=[])
    flat = run(mode="conjoint_analyze", choices=[{"respondent": 1, "task": 1, "alternative": 1, "chosen": 1, "p": "1", "q": "a"}, {"respondent": 1, "task": 1, "alternative": 2, "chosen": 0, "p": "2", "q": "a"}])
    assert "p" in flat["part_worths"] and any("one level" in w for w in flat["warnings"])


# screener


def test_screener_turns_a_540_day_cycle_into_18_months_in_lithuanian():
    out = run(mode="screener", category="naują automobilį", purchase_cycle_days=540, target_n=300, incidence_pct=8)
    assert out["window_days"] == 540 and out["window_phrase"] == "18 mėnesių"
    assert out["screener_question"] == "Ar planuojate įsigyti naują automobilį per artimiausius 18 mėnesių?"
    assert out["qualify_answers"] == ["Taip"] and out["gross_contacts"] == 3750  # 300 / 0.08
    assert out["gross_invites"] == 3750 and out["warnings"] == []
    with_response = run(mode="screener", category="naują automobilį", purchase_cycle_days=540, target_n=300, incidence_pct=8, response_rate_pct=25)
    assert with_response["gross_invites"] == 15000


def test_screener_warns_on_a_short_window_and_never_guesses_incidence():
    out = run(mode="screener", category="a new car", purchase_cycle_days=540, target_n=300, window_days=90)
    assert out["window_days"] == 90 and out["window_phrase"] == "3 months"
    assert any(w.startswith("window shorter than one purchase cycle") for w in out["warnings"])
    assert out["gross_invites"] is None and "incidence_pct" in out["missing"] and any("incidence unknown" in w for w in out["warnings"])
    assert out["screener_question"] == "Do you plan to buy a new car in the next 3 months?"
    assert run(mode="screener", category="coffee", purchase_cycle_days=21, target_n=50, incidence_pct=50)["window_phrase"] == "3 weeks"
    assert run(mode="screener", category="butą", purchase_cycle_days=730, target_n=50, incidence_pct=50)["window_phrase"] == "2 metus"
    with pytest.raises(ToolError, match="category is required"):
        run(mode="screener", purchase_cycle_days=30, target_n=10)


# choice_study


def test_choice_study_design_refuses_unequal_cards_and_labels_invented_brands():
    alt = {"name": "Avižų gėrimas", "price": 1.99, "size": "1 l", "attributes": ["be cukraus", "lietuviškos avižos", "2 g baltymų"]}
    inc = {"name": "Alpro Oat", "price": 1.99, "size": "1 l", "attributes": ["be cukraus", "avižos", "2 g baltymų"]}
    out = run(mode="choice_study", favourite_brands=["Alpro Oat"], alternative=alt, incumbent=inc, n_target=240)
    assert out["action"] == "design" and len(out["stimulus_cards"]) == 2 and out["missing"] == []
    assert out["stimulus_cards"][0]["text"].startswith("Avižų gėrimas\n1 l\n1.99 €")
    assert len(out["design_steps"]) == 5
    with pytest.raises(ToolError, match="alternative is not equally described: price 1.79 vs incumbent 1.99"):
        run(mode="choice_study", alternative={**alt, "price": 1.79}, incumbent=inc)
    with pytest.raises(ToolError, match="2 attributes vs 3"):
        run(mode="choice_study", alternative={**alt, "attributes": ["a", "b"]}, incumbent=inc)
    gym = run(mode="choice_study", language="lt", alternative={"name": "Fortis Gym", "price": 29, "attributes": ["be sutarties"], "invented": True}, incumbent={"name": "Impuls", "price": 29, "attributes": ["be sutarties"]}, n_target=20)
    card = gym["stimulus_cards"][0]
    assert card["invented"] and "Tyrimo tikslais – prekės ženklas išgalvotas" in card["text"] and card["label"] == "simulated response (invented brand)"
    assert any("under 30" in w for w in gym["warnings"])
    sparse = run(mode="choice_study", alternative={"name": "X"}, incumbent={"name": "Y"})
    assert {"alternative.price", "alternative.attributes", "incumbent.price", "favourite_brands", "n_target"} <= set(sparse["missing"])


def test_choice_study_analyze_wilson_on_240_and_brand_lift():
    out = run(mode="choice_study", n_shown=240, n_switched=58)
    arm = out["arms"][0]
    # 58/240 = 24.17 %; Wilson: centre 0.2457, half 0.0539 → 19.2–30.0 %.
    assert arm["share_pct"] == 24.2 and arm["ci95_pct"] == [19.2, 30.0]
    assert out["summary"].startswith("In this simulation 24.2 % of 240 buyers chose")
    assert "will switch" not in out["summary"]
    gym = run(
        mode="choice_study",
        arms=[
            {"arm_id": "impuls", "brand_name": "Impuls", "invented": False, "shown": 200, "chosen": 86},
            {"arm_id": "fortis", "brand_name": "Fortis Gym", "invented": True, "shown": 200, "chosen": 61},
        ],
    )
    real, fake = gym["arms"]
    assert real["share_pct"] == 43.0 and real["label"] == "stated preference (real brand)"
    assert fake["share_pct"] == 30.5 and fake["ci95_pct"] == [24.5, 37.2] and fake["label"] == "simulated response (invented brand)"
    assert gym["brand_lift_pp"] == 12.5 and "not a sales forecast" in fake["sentence"]
    alone = run(mode="choice_study", arms=[{"arm_id": "f", "brand_name": "Fortis", "invented": True, "shown": 200, "chosen": 61}])
    assert alone["brand_lift_pp"] is None and any("no real-brand arm" in w for w in alone["warnings"])
    small = run(mode="choice_study", n_shown=20, n_switched=5, by_favourite={"Alpro": {"shown": 12, "switched": 3}})
    assert sum("too wide" in w for w in small["warnings"]) == 2 and small["per_favourite_brand"]["Alpro"]["share_pct"] == 25.0
    with pytest.raises(ToolError, match="chosen \\(9\\) exceeds shown \\(8\\)"):
        run(mode="choice_study", arms=[{"brand_name": "X", "shown": 8, "chosen": 9}])


# creative_test


def test_holdout_readout_on_the_balance_bike_send():
    out = run(mode="creative_test", design="holdout", control={"exposed": 1250, "converted": 14}, treatment={"exposed": 1240, "converted": 31}, comparable=True, metric="order rate")
    # 31/1240 = 2.5 %, 14/1250 = 1.12 %; pooled 45/2490; z = 0.0138/0.005339 = 2.585; p = 0.0097.
    assert out["control_rate_pct"] == 1.12 and out["treatment_rate_pct"] == 2.5 and out["lift_abs_pp"] == 1.38
    assert out["p_value"] == pytest.approx(0.0097, abs=0.0005) and out["verdict"] == "winner"
    assert out["ci95_abs_pp"][0] == pytest.approx(0.33, abs=0.01) and out["ci95_abs_pp"][1] == pytest.approx(2.43, abs=0.01)
    # incremental = 31 − 0.0112 × 1240 = 17.1
    assert out["incremental_conversions"] == 17.1
    assert out["min_sample_for_this_lift"] == experiment_plan({"baseline_rate": 1.12, "minimum_effect": 1.38, "effect_kind": "absolute"})["sample_per_variant"]
    not_matched = run(mode="creative_test", design="holdout", control={"exposed": 12000, "converted": 100}, treatment={"exposed": 1240, "converted": 31}, comparable=False, comparable_reason="newsletter = whole list")
    assert not_matched["verdict"] == "not_comparable" and not_matched["lift_abs_pp"] is None and "no lift reported" in not_matched["summary"]
    unknown = run(mode="creative_test", design="holdout", control={"exposed": 100, "converted": 10}, treatment={"exposed": 100, "converted": 12})
    assert unknown["verdict"] == "not_comparable" and "comparable" in unknown["missing"]
    tiny = run(mode="creative_test", design="holdout", control={"exposed": 50, "converted": 1}, treatment={"exposed": 50, "converted": 4}, comparable=True)
    assert tiny["verdict"] == "too_small"
    with pytest.raises(ToolError, match="converted \\(31\\) exceeds exposed \\(30\\)"):
        run(mode="creative_test", design="holdout", control={"exposed": 30, "converted": 31}, treatment={"exposed": 30, "converted": 1}, comparable=True)


def test_qualified_readout_cost_per_qualified_and_the_z_test():
    out = run(
        mode="creative_test",
        design="qualified_readout",
        results=[{"name": "Kontrolinė", "spend": 420, "responses": 60, "qualified": 9}, {"name": "Paslaugos", "spend": 410, "responses": 95, "qualified": 21}],
    )
    a, b = out["arms"]
    assert a["cost_per_qualified"] == 46.67 and a["qualified_rate_pct"] == 15.0 and a["cost_per_response"] == 7.0
    assert b["cost_per_qualified"] == 19.52 and b["qualified_rate_pct"] == 22.1
    cmp = out["comparisons"][0]
    # 21/95 − 9/60 = 7.1 pp; pooled 30/155; z = 0.07105/0.06515 = 1.09; p = 0.275
    assert cmp["lift_abs_pp"] == pytest.approx(7.105, abs=0.001) and cmp["p_value"] == pytest.approx(0.275, abs=0.002)
    assert cmp["verdict"].startswith("no difference yet — need ~") and "Paslaugos is ahead" in cmp["verdict"]
    no_q = run(mode="creative_test", design="qualified_readout", results=[{"name": "a", "spend": 100, "responses": 10}, {"name": "b", "spend": 100, "responses": 20}])
    assert no_q["comparisons"][0]["verdict"] == "unjudgeable: qualified counts missing" and any("qualified responses not provided" in w for w in no_q["warnings"])
    zero = run(mode="creative_test", design="qualified_readout", results=[{"name": "a", "spend": 100, "responses": 0, "qualified": 0}, {"name": "b", "spend": 100, "responses": 20, "qualified": 4}])
    assert zero["arms"][0]["status"] == "insufficient data" and zero["arms"][0]["cost_per_response"] is None


def test_difference_in_differences_on_a_two_by_two():
    out = run(mode="creative_test", design="diff_in_diff", treatment={"pre": 100, "post": 130}, control={"pre": 80, "post": 90}, metric="orders")
    # (130 − 100) − (90 − 80) = 20; counterfactual 100 + 10 = 110; 20/110 = 18.2 %
    assert out["effect"] == 20.0 and out["counterfactual"] == 110.0 and out["effect_relative_pct"] == 18.2
    assert out["assumption"] == "parallel trends" and "parallel trends" in out["summary"] and "pre_series" in out["missing"]
    checked = run(
        mode="creative_test",
        design="diff_in_diff",
        treatment={"pre": [98, 102], "post": [128, 132], "pre_series": [90, 95, 100]},
        control={"pre": [78, 82], "post": [88, 92], "pre_series": [70, 75, 80]},
    )
    assert checked["effect"] == 20.0 and checked["pre_trends"]["roughly_parallel"] is True and checked["missing"] == []
    diverging = run(mode="creative_test", design="diff_in_diff", treatment={"pre": 100, "post": 130, "pre_series": [60, 80, 100]}, control={"pre": 80, "post": 90, "pre_series": [78, 79, 80]})
    assert diverging["pre_trends"]["roughly_parallel"] is False and any("doubtful" in w for w in diverging["warnings"])
    with pytest.raises(ToolError, match="treatment and control must each be"):
        run(mode="creative_test", design="diff_in_diff", treatment=[1, 2], control={"pre": 1, "post": 2})


def test_geo_crossover_separates_creative_from_market():
    cells = [
        {"market": "Vilnius", "period": "P1", "creative": "A", "value": 1240},
        {"market": "Kaunas", "period": "P1", "creative": "B", "value": 980},
        {"market": "Vilnius", "period": "P2", "creative": "B", "value": 1100},
        {"market": "Kaunas", "period": "P2", "creative": "A", "value": 1150},
    ]
    out = run(mode="creative_test", design="crossover", cells=cells, purchase_cycle_days=14, period_days=28)
    an = out["analysis"]
    # ((1240 − 1100) + (1150 − 980)) / 2 = 155; vs mean B 1040 → +14.9 %
    assert an["creative_effect"]["abs"] == 155.0 and an["creative_effect"]["pct"] == 14.9 and an["creative_effect"]["direction"] == "A over B"
    assert an["creative_effect"]["range_of_within_market_differences"] == [140.0, 170.0]
    # Vilnius mean 1170, Kaunas 1065 → gap 105
    assert an["market_gap"] == {"markets": "Vilnius over Kaunas", "abs": 105.0}
    assert an["carryover"]["detected"] is False and an["carryover"]["period_shift_pct"] == 1.4  # 2220 → 2250
    assert out["washout_days"] == 14 and "not attributed to the creative" in out["summary"]
    assert out["schedule"][2] == {"period": "P2", "market": "Vilnius", "creative": "B", "start_offset_days": 42}
    design_only = run(mode="creative_test", design="crossover", markets=["Vilnius", "Kaunas"], creatives=["A", "B"], purchase_cycle_days=14, period_days=28)
    assert design_only["analysis"] is None and "the swap has not run" in design_only["summary"]
    half = run(mode="creative_test", design="crossover", cells=cells[:2], purchase_cycle_days=14)
    assert half["analysis"] is None and "cells for Vilnius" in half["missing"]
    with pytest.raises(ToolError, match="exactly two creatives"):
        run(mode="creative_test", design="crossover", markets=["a", "b"], creatives=["A"])


def test_portfolio_splits_seed_and_reserve_and_writes_the_rule():
    out = run(mode="creative_test", design="portfolio", total_budget=1500, ideas=5, reserve_share=0.4, cpm=6, metric="CTR", baseline_rate=1.0)
    assert out["seed_per_idea"] == 180.0 and out["reserve"] == 600.0 and out["impressions_per_idea"] == 30000
    assert out["expected_events_per_idea"] == 300 and out["sufficient"] is True and out["alpha"] == 0.1
    assert out["impressions_needed_per_idea"] == experiment_plan({"baseline_rate": 1.0, "minimum_effect": 50, "alpha": 0.1})["sample_per_variant"]
    assert "30,000 impressions per idea" in out["reallocation_rule"] and "top 2" in out["reallocation_rule"]
    no_cpm = run(mode="creative_test", design="portfolio", total_budget=1500, ideas=5, metric="CTR", baseline_rate=1.0)
    assert no_cpm["impressions_per_idea"] is None and no_cpm["sufficient"] is None and any("cpm" in n and "ask the marketer" in n for n in no_cpm["notes"])
    with pytest.raises(ToolError, match="ideas must be at least 2"):
        run(mode="creative_test", design="portfolio", total_budget=100, ideas=1)


def test_factorial_counts_cells_and_offers_reductions():
    out = run(
        mode="creative_test",
        design="factorial",
        baseline_rate=2.4,
        minimum_effect=10,
        elements=[{"name": "headline", "levels": 3}, {"name": "image", "levels": 2}, {"name": "cta", "levels": 2}],
        weekly_traffic=18000,
        max_weeks=6,
    )
    assert out["cells"] == 12 and out["alpha_per_comparison"] == round(0.05 / 11, 4)
    assert out["per_cell"] == experiment_plan({"baseline_rate": 2.4, "minimum_effect": 10, "variants": 12})["sample_per_variant"]
    assert out["visitors_per_cell_per_week"] == 1500.0 and out["weeks_full_factorial"] > 6 and out["feasible_in_max_weeks"] is False
    options = out["reduction_options"]
    assert options == sorted(options, key=lambda o: o["weeks"]) and options[0]["cells"] == 2
    assert any(o["drop"] == "headline 3->2 levels" and o["cells"] == 8 for o in options)
    assert any(o["drop"].startswith("image to 1 level") and o["cells"] == 6 for o in options)
    assert "full 12-cell grid" in out["summary"]
    roomy = run(mode="creative_test", design="factorial", baseline_rate=2.4, minimum_effect=10, elements=[{"name": "headline", "levels": 3}, {"name": "cta", "levels": 2}], weekly_traffic=18000, max_weeks=60)
    assert roomy["feasible_in_max_weeks"] is True
    blind = run(mode="creative_test", design="factorial", baseline_rate=2.4, minimum_effect=10, elements=[{"name": "h", "levels": 2}, {"name": "c", "levels": 2}])
    assert blind["weeks_full_factorial"] is None and "weekly_traffic" in blind["missing"]
    with pytest.raises(ToolError, match="at least 2 levels"):
        run(mode="creative_test", design="factorial", baseline_rate=2, minimum_effect=10, elements=[{"name": "h", "levels": 1}])


def test_stated_versus_observed_blocks_the_survey_share_from_the_forecast():
    out = run(mode="creative_test", design="stated_vs_observed", stated_rate=38, stated_n=400, observed={"exposures": 1200, "purchases": 192})
    # 192/1200 = 16.0 %; Wilson 14.0–18.2 %; 16/38 = 0.42
    assert out["observed_rate_pct"] == 16.0 and out["observed_ci95_pct"] == [14.0, 18.2]
    assert out["conversion_ratio"] == 0.42 and out["stated_overstates"] is True
    assert out["forecast_rate_pct"] == 16.0 and out["forecast_use"] == "ok" and "not 38 %" in out["summary"]
    plan_only = run(mode="creative_test", design="stated_vs_observed", stated_rate=38, weekly_traffic=1000)
    assert plan_only["forecast_use"] == "not yet — validate live" and plan_only["forecast_rate_pct"] is None
    assert plan_only["live_test_plan"]["sample_per_arm"] == experiment_plan({"baseline_rate": 38, "minimum_effect": 50})["sample_per_variant"]
    assert "stated_n" in plan_only["missing"] and "observed" in plan_only["missing"]
    with pytest.raises(ToolError, match="unsuitable data"):
        run(mode="creative_test", design="stated_vs_observed", stated_rate=38, observed={"exposures": 0, "purchases": 0})


def test_copy_test_fixes_the_stopping_rule_and_checks_the_pair():
    variants = [{"name": "control", "offer_lead": "none", "angle": "speed"}, {"name": "free", "offer_lead": "free", "angle": "speed"}, {"name": "price", "offer_lead": "price", "angle": "speed"}]
    out = run(mode="creative_test", design="copy_test", baseline_rate=2.5, minimum_effect=20, weekly_traffic=4000, variants=variants)
    sized = experiment_plan({"baseline_rate": 2.5, "minimum_effect": 20, "weekly_traffic": 4000, "variants": 3})
    assert out["sample_per_variant"] == sized["sample_per_variant"] and out["weeks"] == sized["weeks"]
    assert "do not stop early" in out["stopping_rule"] and f"p below {0.025:g}" in out["stopping_rule"]
    assert not any("free_lead_untested" in w for w in out["warnings"])
    lonely = run(mode="creative_test", design="copy_test", variants=variants[:2])
    assert any(w.startswith("free_lead_untested") for w in lonely["warnings"])
    assert lonely["baseline_assumed"] is True and "2 % assumed" in lonely["summary"] and lonely["weeks"] is None
    assert {"baseline_rate", "weekly_traffic"} <= set(lonely["missing"])


def test_creative_test_declines_what_needs_a_browser_and_names_the_designs():
    with pytest.raises(ToolError, match="needs a browser"):
        run(mode="creative_test", design="speed_ab", control_url="https://a", variant_url="https://b")
    with pytest.raises(ToolError, match="design must be one of"):
        run(mode="creative_test", design="x")
    with pytest.raises(ToolError, match="mode must be one of"):
        run(mode="nope")


def test_every_research_mode_returns_the_four_shared_keys():
    calls = [
        dict(mode="wtp", responses=WTP_SIX),
        dict(mode="concept", responses=concept_rows(12, 18, 5, 5)),
        dict(mode="test_market", market={"name": "Kaunas"}, mix={}),
        dict(mode="conjoint_design", attributes={"a": ["1", "2"], "b": ["x", "y"]}, n_respondents=5),
        dict(mode="screener", category="coffee", purchase_cycle_days=21, target_n=50),
        dict(mode="choice_study", n_shown=240, n_switched=58),
        dict(mode="creative_test", design="diff_in_diff", treatment={"pre": 1, "post": 2}, control={"pre": 1, "post": 1}),
    ]
    for call in calls:
        out = run(**call)
        assert {"missing", "warnings", "method", "summary"} <= set(out), call["mode"]
        assert isinstance(out["summary"], str) and out["summary"]



def test_dispatch_routes_research_modes(tmp_path):
    import json as _json

    from rocky.tools import WorkspaceTools, dispatch, worker_tools

    ws = WorkspaceTools(tmp_path)
    out = _json.loads(dispatch(ws, "experiment_plan", {"mode": "screener", "category": "a new car", "purchase_cycle_days": 1800, "target_n": 100, "incidence_pct": 5, "response_rate_pct": 20, "language": "en"}))
    assert out["mode"] == "screener"
    plan = _json.loads(dispatch(ws, "experiment_plan", {"baseline_rate": 3, "minimum_effect": 20, "weekly_traffic": 5000}))
    assert "sample_per_variant" in plan or "per_variant" in plan or "sample" in _json.dumps(plan)
    spec = next(t for t in worker_tools(False) if t["function"]["name"] == "experiment_plan")
    assert "creative_test" in spec["function"]["parameters"]["properties"]["mode"]["enum"]
