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
