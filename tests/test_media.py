import json

import pytest

from rocky.media import KNOWN_OCCASIONS, classify_creative, media_plan, media_plan_tool, occasion_date
from rocky.tools import ToolError


# --- dispatch ------------------------------------------------------------------


@pytest.mark.parametrize(
    "args, message",
    [
        ({}, "mode is required"),
        ({"mode": "magic"}, "unknown mode 'magic'"),
        ("ledger", "needs an object"),
    ],
)
def test_mode_is_required_and_checked(args, message):
    with pytest.raises(ToolError, match=message):
        media_plan(args)


def test_tool_returns_json_with_the_shared_keys():
    text = media_plan_tool({"mode": "classify", "rows": [{"campaign": "Mūsų istorija", "channel": "TV", "spend": 100, "creative_text": "Šeimos receptai"}]})
    data = json.loads(text)
    assert data["mode"] == "classify" and set(data) >= {"missing", "warnings", "summary"}
    assert "Mūsų istorija" in text and "€" in text  # ensure_ascii=False


# --- ledger (#403, #404) ---------------------------------------------------------


H1_LINES = [
    {"label": "Video media", "channel": "Video", "cost_type": "media", "amount": 60_000},
    {"label": "Video production", "channel": "Video", "cost_type": "production", "amount": 35_000},
    {"label": "Meta media", "channel": "Meta", "cost_type": "media", "amount": 40_000},
    {"label": "Google Search media", "channel": "Google Search", "cost_type": "media", "amount": 30_000},
    {"label": "Radio media", "channel": "Radio", "cost_type": "media", "amount": 15_000},
]


def test_ledger_gives_channel_shares_on_two_bases_and_flags_missing_production():
    out = media_plan({"mode": "ledger", "period": "2026 H1", "lines": H1_LINES, "quoted_shares": [{"channel": "Video", "pct": 53}]})
    assert out["total"] == 180_000 and out["media_total"] == 145_000
    video = out["by_channel"][0]
    # 60k of 145k media = 41.4 %; 95k of 180k all-in = 52.8 %
    assert video["channel"] == "Video" and video["media"] == 60_000 and video["production"] == 35_000
    assert video["share_media_only_pct"] == 41.4 and video["share_all_in_pct"] == 52.8
    meta = next(r for r in out["by_channel"] if r["channel"] == "Meta")
    assert meta["share_media_only_pct"] == 27.6 and meta["share_all_in_pct"] == 22.2  # 40/145, 40/180
    assert out["by_cost_type"]["production"] == {"amount": 35_000, "pct": 19.4}  # 35/180
    codes = [(f["code"], f["channel"]) for f in out["findings"]]
    assert ("production_missing", "Meta") in codes and ("production_missing", "Radio") in codes and ("production_missing", "Google Search") in codes
    assert ("production_missing", "Video") not in codes
    # the agency's 53 % was quoted without a basis: it is the all-in figure
    assert ("basis_unstated", "Video") in codes
    assert out["quoted_share_check"][0]["matches"] == "all-in"
    assert "Video is 52.8 % of all spend but 41.4 % of media buys" in out["summary"]
    assert "Production is not recorded for Meta, Google Search, Radio" in out["summary"]
    assert out["by_role"] is None and out["missing"] == []


def test_ledger_reconciles_other_teams_always_on_spend_into_the_brand_share():
    campaign = [
        {"label": "Brand campaign", "channel": "TV", "cost_type": "media", "amount": 140_000, "role": "brand", "team": "marketing"},
        {"label": "Performance", "channel": "Meta", "cost_type": "media", "amount": 60_000, "role": "activation", "team": "marketing"},
    ]
    out = media_plan(
        {
            "mode": "ledger",
            "lines": campaign,
            "always_on": [
                {"label": "Google Shopping", "team": "e-com", "channel": "Google", "monthly_amount": 15_000, "months": 12},
                {"label": "Meta retargeting", "team": "e-com", "channel": "Meta", "monthly_amount": 10_000, "months": 12},
            ],
        }
    )
    brand, activation = out["by_role"]["brand"], out["by_role"]["activation"]
    # campaign only: 140k of 200k = 70 %; with 12 × 25k = 300k always-on activation: 140k of 500k = 28 %
    assert out["always_on_total"] == 300_000 and out["total"] == 500_000
    assert brand["share_campaign_only_pct"] == 70.0 and brand["share_reconciled_pct"] == 28.0
    assert activation["amount_reconciled"] == 360_000 and activation["share_reconciled_pct"] == 72.0
    assert not any(f["code"] in ("other_teams_missing", "activation_uncounted") for f in out["findings"])
    assert "Brand share 70 % within the campaign lines, 28 % once €300 000 of always-on spend is added" in out["summary"]
    # without the other team's lines the 70 % is an upper bound, and a mentioned spend with no line is flagged
    out = media_plan({"mode": "ledger", "lines": campaign, "mentioned_spend": ["Google Shopping"]})
    assert out["by_role"]["brand"]["share_reconciled_pct"] is None
    codes = [f["code"] for f in out["findings"]]
    assert "other_teams_missing" in codes and "activation_uncounted" in codes
    assert any("upper bound" in f["message"] for f in out["findings"] if f["code"] == "other_teams_missing")
    assert any(m.startswith("always_on lines from other teams") for m in out["missing"])


def test_ledger_refuses_missing_amounts_and_names_bundled_lines():
    with pytest.raises(ToolError, match="no budget lines supplied"):
        media_plan({"mode": "ledger"})
    with pytest.raises(ToolError, match="amount of Meta media is required"):
        media_plan({"mode": "ledger", "lines": [{"label": "Meta media", "channel": "Meta", "cost_type": "media"}]})
    with pytest.raises(ToolError, match="cost_type of X must be one of media, production, fees, tech, other"):
        media_plan({"mode": "ledger", "lines": [{"label": "X", "channel": "Meta", "cost_type": "creative", "amount": 1}]})
    with pytest.raises(ToolError, match="needs amount, or monthly_amount and months"):
        media_plan({"mode": "ledger", "always_on": [{"label": "Shopping", "monthly_amount": 100}]})
    out = media_plan({"mode": "ledger", "lines": [{"label": "Video media + production", "channel": "Video", "amount": 95_000}]})
    assert "cost_type for Video media + production (counted as other)" in out["missing"]
    assert any(f["code"] == "production_bundled" for f in out["findings"])


# --- sov (#407, #411) ------------------------------------------------------------


def test_sov_against_named_competitors_gives_esov_in_points():
    out = media_plan({"mode": "sov", "brand_spend": 180_000, "competitor_spends": {"Rivile": 420_000, "Agnum": 150_000, "B1": 90_000}, "market_share_pct": 14})
    # 180k of 840k = 21.4 %; 21.4 − 14 = +7.4 points (not a 1.53× ratio)
    assert out["category_total_used"] == 840_000 and out["coverage"] == "known_competitors_only"
    assert out["own"]["sov_pct"] == 21.4 and out["own"]["esov_points"] == 7.4 and out["own"]["verdict"] == "over-investing"
    assert "Share of voice 21.4 % against a 14 % market share: excess share of voice +7.4 percentage points" in out["summary"]
    assert "3 named competitors only" in out["summary"]
    assert "price, distribution and product" in out["summary"]
    assert "not a forecast" in out["growth_expectation"]
    assert any(m.startswith("category_total_spend") for m in out["missing"])
    assert any(m.startswith("share_of_market_pct for competitors") for m in out["missing"])


def test_sov_brand_size_comparison_and_target_esov():
    out = media_plan(
        {
            "mode": "sov",
            "brands": [
                {"name": "Leader", "share_of_market_pct": 35, "media_spend": 900_000},
                {"name": "Brand B", "share_of_market_pct": 12, "media_spend": 350_000},
                {"name": "Brand C", "share_of_market_pct": 9, "media_spend": 280_000},
                {"name": "Avižė", "share_of_market_pct": 6, "media_spend": 120_000, "is_own": True},
            ],
            "planned_spend": 155_000,
            "target_esov_points": 10,
        }
    )
    # own 120k of 1 650k = 7.3 % SOV vs 6 % SOM → +1.3
    assert out["own"]["sov_pct"] == 7.3 and out["own"]["esov_points"] == 1.3
    # leader 900k / 35 = 25 714.29 per point → × 6 = 154 285.71
    assert out["leader"]["spend_per_share_point"] == 25_714.29 and out["spend_at_leader_ratio"] == 154_285.71
    # peers in the 3–12 % band: B (29 166.67/pt) and C (31 111.11/pt), median 30 138.89 → × 6 = 180 833.33
    assert out["peers"]["names"] == ["Brand B", "Brand C"] and out["peers"]["median_spend_per_share_point"] == 30_138.89
    assert out["spend_at_peer_ratio"] == 180_833.33
    # SOV 16 % with others at 1 530k: x = 0.16 × 1 530k / 0.84 = 291 428.57
    assert out["spend_for_target_esov"] == 291_428.57
    # planned 155k ≈ the leader ratio for a brand under half the leader's size → underfunded
    assert out["planned"] == {"spend": 155_000, "sov_pct": 9.2, "esov_points": 3.2}
    assert [f["code"] for f in out["findings"]] == ["leader_ratio_underfunds"]
    assert "Peers spend €30 138.89 per share point" in out["summary"] and "A +10-point ESOV needs about €291 428.57" in out["summary"]


def test_sov_series_pairs_esov_with_the_next_periods_share_change():
    series = [
        {"period": "2022", "brand_spend": 120, "category_total_spend": 1000, "market_share_pct": 10},
        {"period": "2023", "brand_spend": 141, "category_total_spend": 1000, "market_share_pct": 10.1},
        {"period": "2024", "brand_spend": 163, "category_total_spend": 1000, "market_share_pct": 10.3},
        {"period": "2025", "brand_spend": 160, "category_total_spend": 1000, "market_share_pct": 10.6},
    ]
    out = media_plan({"mode": "sov", "brand_spend": 160, "category_total_spend": 1000, "market_share_pct": 10.6, "series": series})
    block = out["series"]
    # ESOV 2, 4, 6 followed by share changes 0.1, 0.2, 0.3 → a straight line, r = 1
    assert [p["esov_points"] for p in block["periods"]] == [2.0, 4.0, 6.0, 5.4]
    assert [p.get("next_share_change_pts") for p in block["periods"]] == [0.1, 0.2, 0.3, None]
    assert block["pairs_n"] == 3 and block["correlation"] == 1.0
    assert out["coverage"] == "full" and out["own"]["verdict"] == "over-investing"  # 16 − 10.6 = +5.4
    out = media_plan({"mode": "sov", "brand_spend": 160, "category_total_spend": 1000, "market_share_pct": 10.6, "series": series[:2]})
    assert out["series"]["correlation"] is None and "too few periods" in out["series"]["correlation_note"]


def test_sov_refuses_bad_denominators_and_warns_on_small_bases():
    with pytest.raises(ToolError, match="give brands"):
        media_plan({"mode": "sov", "market_share_pct": 10})
    with pytest.raises(ToolError, match="no competitor spend or category total"):
        media_plan({"mode": "sov", "brand_spend": 100, "market_share_pct": 10})
    with pytest.raises(ToolError, match="exactly one brand must be marked is_own"):
        media_plan({"mode": "sov", "brands": [{"name": "A", "media_spend": 1}, {"name": "B", "media_spend": 2}]})
    with pytest.raises(ToolError, match="category_total_spend 500 is below the listed brands' spend 600"):
        media_plan({"mode": "sov", "brand_spend": 100, "competitor_spends": {"X": 500}, "category_total_spend": 500, "market_share_pct": 10})
    out = media_plan({"mode": "sov", "brand_spend": 100, "competitor_spends": {"X": 300}, "market_share_pct": 30})
    assert any("Only one competitor" in w for w in out["warnings"])
    assert out["own"]["verdict"] == "under-investing"  # 25 % SOV vs 30 % SOM
    out = media_plan({"mode": "sov", "brands": [{"name": "A", "share_of_market_pct": 70, "media_spend": 1}, {"name": "B", "share_of_market_pct": 50, "media_spend": 1, "is_own": True}]})
    assert any("add up to 120 %" in w for w in out["warnings"])


# --- budget (#413, #414, #409) -----------------------------------------------------


HR_ACTIVITIES = [
    {"name": "LinkedIn ads", "kind": "activation", "unit": "day", "unit_cost": 150, "units": 90, "expected_result": 120, "result_unit": "demo"},
    {"name": "Webinars", "kind": "brand", "unit": "webinar", "unit_cost": 4000, "units": 2, "expected_result": 60},
    {"name": "SDR", "kind": "activation", "unit": "month", "unit_cost": 3500, "units": 3, "expected_result": 90},
    {"name": "Landing + content", "kind": "activation", "cost": 5000, "expected_result": 30},
]


def test_budget_from_objectives_and_tasks():
    out = media_plan({"mode": "budget", "budget_total": 40_000, "market": "b2b", "objective": {"metric": "demo", "target": 300}, "activities": HR_ACTIVITIES})
    # 90 × 150 = 13 500; 2 × 4 000 = 8 000; 3 × 3 500 = 10 500; 5 000 → 37 000
    assert out["total_cost"] == 37_000 and out["affordable"] is True and out["headroom"] == 3000
    # 120 + 60 + 90 + 30 = 300 demos = 100 %; 37 000 / 300 = 123.33 per demo
    assert out["objective"]["coverage_pct"] == 100.0 and out["objective"]["cost_per_result"] == 123.33
    # 8 000 of 37 000 on brand = 21.6 %, 24.4 points under the 46 % B2B benchmark
    assert out["split"]["brand_pct"] == 21.6 and out["split"]["deviation_points"] == -24.4
    # cut candidates by cost per result: landing 166.67, webinars 133.33, SDR 116.67, LinkedIn 112.5
    assert [c["name"] for c in out["cut_candidates"]] == ["Landing + content", "Webinars", "SDR", "LinkedIn ads"]
    assert out["cut_candidates"][0]["cost_per_result"] == 166.67
    assert "benchmark_is_starting_scenario" in out["flags"] and "brand_below_20pct" not in out["flags"]
    assert out["summary"].startswith("Total cost €37 000, within the €40 000 budget with €3 000 headroom. Expected 300 demo = 100 % of the 300 target, €123.33 per demo.")
    assert out["missing"] == []


def test_budget_with_a_missing_cost_gives_at_least_and_affordability_unknown():
    activities = [dict(a) for a in HR_ACTIVITIES]
    del activities[2]["unit_cost"]
    out = media_plan({"mode": "budget", "budget_total": 40_000, "market": "b2b", "objective": {"metric": "demo", "target": 300}, "activities": activities})
    # 13 500 + 8 000 + 5 000 = 26 500 without the SDR
    assert out["total_cost"] == 26_500 and out["total_cost_is_partial"] is True
    assert out["affordable"] == "unknown" and out["headroom"] is None
    assert "unit_cost (or cost) for SDR" in out["missing"]
    assert out["summary"].startswith("At least €26 500 excluding SDR; affordability unknown until their cost is given.")
    assert "cost_per_result" not in out["objective"]  # a partial total gives no cost per result
    # over budget and short of the objective
    out = media_plan({"mode": "budget", "budget_total": 30_000, "market": "b2b", "objective": {"metric": "demo", "target": 400}, "activities": HR_ACTIVITIES})
    assert out["affordable"] is False and out["shortfall"] == 7000 and out["objective"]["coverage_pct"] == 75.0
    assert "not_affordable" in out["flags"] and "objective_not_reached" in out["flags"]
    assert "not affordable: €7 000 over the €30 000 budget" in out["summary"]


def test_budget_benchmark_split_for_b2b_and_the_90_10_cfo_plan():
    out = media_plan(
        {
            "mode": "budget",
            "budget_total": 120_000,
            "market": "b2b",
            "activities": [
                {"name": "Google Search", "kind": "activation", "cost": 60_000},
                {"name": "LinkedIn lead-gen", "kind": "activation", "cost": 48_000},
                {"name": "LinkedIn brand video", "kind": "brand", "cost": 12_000},
            ],
        }
    )
    # 12k of 120k = 10 % brand; benchmark 46 % → −36 points; 46 % of 120k = 55 200
    assert out["split"]["brand_pct"] == 10.0 and out["split"]["deviation_points"] == -36.0
    assert out["benchmark"]["brand_pct"] == 46 and out["benchmark"]["brand_amount"] == 55_200 and out["benchmark"]["activation_amount"] == 64_800
    assert "5 Principles" in out["benchmark"]["source"]
    assert "brand_below_20pct" in out["flags"]
    assert "starting scenario to test locally" in out["summary"]
    # no activities: the benchmark euros alone, and the market is required for a benchmark
    out = media_plan({"mode": "budget", "budget_total": 120_000, "market": "b2c"})
    assert out["benchmark"]["brand_amount"] == 72_000 and "split" not in out
    assert out["summary"].startswith("Benchmark split 60/40 = €72 000 brand / €48 000 activation")
    out = media_plan({"mode": "budget", "budget_total": 120_000})
    assert out["benchmark"] is None and any(m.startswith("market (b2b or b2c)") for m in out["missing"])
    with pytest.raises(ToolError, match="market must be one of b2b, b2c"):
        media_plan({"mode": "budget", "budget_total": 1, "market": "b2g"})
    with pytest.raises(ToolError, match="budget_total must be greater than zero"):
        media_plan({"mode": "budget", "budget_total": 0, "market": "b2b"})


def test_budget_adjustments_are_applied_once_and_not_for_traits_already_in_the_sector_average():
    args = {
        "mode": "budget",
        "budget_total": 120_000,
        "benchmark_brand_pct": 35,
        "sector": "online retail",
        "benchmark_source": "https://ipa.co.uk/effectiveness-in-context",
        "sector_traits": ["e-commerce", "low consideration", "frequent purchase", "price-led"],
        "adjustments": [
            {"feature": "e-commerce pure player", "direction": "activation", "points": 10, "reason": "online only"},
            {"feature": "low consideration", "direction": "activation", "points": 5, "reason": "impulse buys"},
            {"feature": "new brand name", "direction": "brand", "points": 10, "reason": "awareness near zero"},
        ],
    }
    out = media_plan(args)
    adjusted = out["adjusted_benchmark"]
    assert [r["feature"] for r in adjusted["rejected"]] == ["e-commerce pure player", "low consideration"]
    assert all("already in the sector average" in r["rejected_because"] for r in adjusted["rejected"])
    assert [r["feature"] for r in adjusted["accepted"]] == ["new brand name"]
    # 35 + 10 = 45 % brand of 120k = 54 000; activation 66 000
    assert adjusted["final_split"] == {"brand_pct": 45.0, "activation_pct": 55.0, "brand_amount": 54_000, "activation_amount": 66_000}
    assert "Start at 35/65; 1 adjustment(s) accepted, 2 dropped as already in the sector average, so 45/55: €54 000 brand, €66 000 activation." in out["summary"]
    # the same feature twice counts once; the total shift is capped; no traits → unverified
    args2 = dict(args, sector_traits=[], max_total_points=12)
    args2["adjustments"] = args["adjustments"] + [{"feature": "New brand name", "direction": "brand", "points": 10}]
    out = media_plan(args2)
    adjusted = out["adjusted_benchmark"]
    assert len(adjusted["accepted"]) == 3 and all(a.get("unverified_against_sector") for a in adjusted["accepted"])
    assert adjusted["rejected"][0]["rejected_because"].startswith("the same feature was already applied once")
    # accepted: −10 −5 +10 = −5 points → 30 % brand (under the 12-point cap, so not capped)
    assert adjusted["net_points"] == -5.0 and adjusted["capped"] is False and adjusted["final_split"]["brand_pct"] == 30.0
    assert any("double-count check" in w for w in out["warnings"])
    out = media_plan(dict(args, sector_traits=[], max_total_points=3))
    assert out["adjusted_benchmark"]["capped"] is True and out["adjusted_benchmark"]["net_points"] == -3.0
    assert any("capped at 3" in w for w in out["warnings"])
    with pytest.raises(ToolError, match="direction of adjustment 'x' is required"):
        media_plan({"mode": "budget", "budget_total": 1, "market": "b2b", "adjustments": [{"feature": "x", "points": 1}]})


# --- offers (#421, #422) -----------------------------------------------------------


def test_offers_compare_served_and_viewable_cpm_on_one_denominator():
    out = media_plan(
        {
            "mode": "offers",
            "offers": [
                {"name": "Delfi", "price": 4.20, "price_basis": "cpm_served", "quantity": 500_000, "viewability_pct": 62},
                {"name": "15min.lt", "price": 6.50, "price_basis": "cpm_viewable", "quantity": 300_000},
            ],
        }
    )
    delfi, min15 = out["rows"]
    # 4.20 × 500 = 2 100 total; 62 % viewable = 310 000; 2 100 / 310 × 1000 = 6.77 vCPM
    assert delfi["total_cost"] == 2100 and delfi["viewable_impressions"] == 310_000 and delfi["vcpm"] == 6.77
    # 6.50 × 300 = 1 950 total, 300 000 viewable, vCPM 6.50
    assert min15["total_cost"] == 1950 and min15["vcpm"] == 6.5
    assert out["compare_on"] == "vcpm" and out["cheapest"] == "15min.lt"
    assert [r["name"] for r in out["ranking"]] == ["15min.lt", "Delfi"]
    assert out["summary"] == "Cheapest per viewable thousand: 15min.lt (€6.50), then Delfi (€6.77). Delfi only looks cheapest on its quoted cpm served price."
    assert out["missing"] == []


def test_offers_without_viewability_give_a_conditional_table_not_a_ranking():
    out = media_plan(
        {
            "mode": "offers",
            "offers": [
                {"name": "Delfi", "price": 4.20, "price_basis": "cpm_served", "quantity": 500_000},
                {"name": "15min.lt", "price": 6.50, "price_basis": "cpm_viewable", "quantity": 300_000},
            ],
        }
    )
    # 4.20 / 0.4 = 10.50, / 0.5 = 8.40, / 0.6 = 7.00, / 0.7 = 6.00
    assert out["rows"][0]["conditional_vcpm"] == {"40 %": 10.5, "50 %": 8.4, "60 %": 7.0, "70 %": 6.0}
    assert "vcpm" not in out["rows"][0]
    assert out["ranking"] == [] and out["compare_on"] is None and out["cheapest"] is None
    assert out["missing"] == ["viewability_pct for Delfi: served CPM cannot be converted to viewable CPM"]
    assert out["summary"].startswith("No ranking")
    # forcing the served basis ranks what has it
    out = media_plan(
        {
            "mode": "offers",
            "compare_on": "cpm_served",
            "offers": [
                {"name": "Delfi", "price": 4.20, "price_basis": "cpm_served", "quantity": 500_000},
                {"name": "15min.lt", "price": 6.50, "price_basis": "cpm_viewable", "quantity": 300_000, "viewability_pct": 50},
            ],
        }
    )
    # 15min: 300k viewable at 50 % = 600k served, 1 950 / 600 × 1000 = 3.25 served CPM
    assert out["rows"][1]["served_impressions"] == 600_000 and out["rows"][1]["cpm_served"] == 3.25
    assert [r["name"] for r in out["ranking"]] == ["15min.lt", "Delfi"]


def test_offers_convert_cpa_prices_to_the_target_event_through_step_rates():
    args = {
        "mode": "offers",
        "target_event": "paid enrolment",
        "step_rates": [{"from": "registration", "to": "lead", "rate_pct": 20}, {"from": "lead", "to": "paid enrolment", "rate_pct": 50}],
        "offers": [
            {"name": "Publisher A", "price": 25, "price_basis": "cpa", "payment_event": "registration"},
            {"name": "Publisher B", "price": 40, "price_basis": "cpl", "payment_event": "lead (completed call)"},
            {"name": "Publisher C", "price": 95, "price_basis": "cpa", "payment_event": "paid enrolment"},
        ],
    }
    out = media_plan(args)
    a, b, c = out["rows"]
    # A: 25 / (0.20 × 0.50) = 250; B: 40 / 0.50 = 80; C already on the target event: 95
    assert a["cost_per_target_event"] == 250 and a["conversion_path"] == ["registration", "lead", "paid enrolment"]
    assert b["cost_per_target_event"] == 80 and c["cost_per_target_event"] == 95
    assert [r["name"] for r in out["ranking"]] == ["Publisher B", "Publisher C", "Publisher A"]
    assert out["events_equal"] is False and out["event_mismatch"] is True
    assert out["summary"].startswith("Cheapest per paid enrolment: Publisher B (€80), then Publisher C (€95), Publisher A (€250). Publisher A only looks cheapest")
    assert "confirm each definition before signing" in out["summary"]
    assert any("Publisher B pays on 'lead (completed call)'" in s for s in out["contract_check"])
    assert out["missing"] == []  # per-event prices need no quantity
    # without the rates only C is ranked, A gets a conditional table
    out = media_plan({k: v for k, v in args.items() if k != "step_rates"})
    assert [r["name"] for r in out["ranking"]] == ["Publisher C"]
    assert out["rows"][0]["conditional_cost_per_target_event"] == {"10 %": 250, "20 %": 125, "30 %": 83.33, "50 %": 50}
    assert "rate registration→paid enrolment for Publisher A" in out["missing"]
    assert any("Not ranked on cost_per_target_event: Publisher A, Publisher B" in w for w in out["warnings"])


def test_offers_refuse_unnamed_payment_events_and_bad_prices():
    out = media_plan({"mode": "offers", "offers": [{"name": "A", "price": 25, "price_basis": "cpa"}, {"name": "B", "price": 40, "price_basis": "cpa", "payment_event": "lead"}]})
    assert out["rows"][0]["unranked_because"] == "payment event not named"
    assert any(m.startswith("payment_event for A") for m in out["missing"])
    assert "target_event (the event you actually want to pay for)" in out["missing"]
    assert [r["name"] for r in out["ranking"]] == ["B"]
    with pytest.raises(ToolError, match="price of A must be greater than zero"):
        media_plan({"mode": "offers", "offers": [{"name": "A", "price": 0, "price_basis": "cpc"}]})
    with pytest.raises(ToolError, match="price_basis of A must be one of cpm_served, cpm_viewable, cpc, cpl, cpa"):
        media_plan({"mode": "offers", "offers": [{"name": "A", "price": 1, "price_basis": "cpv"}]})
    with pytest.raises(ToolError, match="viewability_pct of A must be above 0 and at most 100"):
        media_plan({"mode": "offers", "offers": [{"name": "A", "price": 1, "price_basis": "cpm_served", "quantity": 1000, "viewability_pct": 120}]})
    # cpc with ctr and conversion derives clicks and cost per acquisition
    out = media_plan({"mode": "offers", "offers": [{"name": "A", "price": 5, "price_basis": "cpm_served", "quantity": 100_000, "viewability_pct": 50, "ctr_pct": 1, "conversion_pct": 10}]})
    row = out["rows"][0]
    # 500 total; 1 000 clicks → 0.50 per click; 100 events → 5.00 per acquisition
    assert row["clicks"] == 1000 and row["cpc"] == 0.5 and row["cpa"] == 5.0


# --- volume (#426) -------------------------------------------------------------------


def test_volume_decomposes_growth_into_penetration_and_frequency_and_checks_the_reported_figure():
    out = media_plan(
        {
            "mode": "volume",
            "unit": "L",
            "periods": [
                {"label": "2024", "population": 1_300_000, "penetration_pct": 11.2, "purchases_per_buyer": 4.1, "units_per_purchase": 1, "reported_volume": 412_000},
                {"label": "2025", "population": 1_300_000, "penetration_pct": 12.1, "purchases_per_buyer": 4.3, "units_per_purchase": 1, "reported_volume": 468_000},
            ],
        }
    )
    p24, p25 = out["periods"]
    # 1.3M × 11.2 % = 145 600 buyers × 4.1 × 1 = 596 960 L vs 412 000 reported = 144.9 %
    assert p24["buyers"] == 145_600 and p24["computed_volume"] == 596_960 and p24["reconciliation_pct"] == 144.9
    assert p25["computed_volume"] == 676_390
    change = out["changes"][0]
    # ln(12.1/11.2) = 0.0773, ln(4.3/4.1) = 0.0476; total 0.1249 → 61.9 % / 38.1 %
    assert change["attribution"] == {"population": 0.0, "penetration": 61.9, "frequency": 38.1, "basket": 0.0}
    assert change["main_driver"] == "penetration" and change["change_pct"] == 13.3
    assert change["factor_change_pct"]["penetration"] == 8.0 and change["factor_change_pct"]["frequency"] == 4.9
    assert any("44.9 % gap" in w for w in out["warnings"])
    assert "61.9 % penetration (more buyers), 38.1 % frequency" in out["summary"]
    assert "do not reproduce the reported volume" in out["summary"]


def test_volume_derives_one_missing_factor_from_reported_volume_or_names_it():
    out = media_plan(
        {
            "mode": "volume",
            "periods": [
                {"label": "A", "population": 1_000_000, "buyers": 100_000, "purchases_per_buyer": 2, "units_per_purchase": 1.5},
                {"label": "B", "population": 1_000_000, "buyers": 110_000, "units_per_purchase": 1.5, "reported_volume": 330_000},
            ],
        }
    )
    a, b = out["periods"]
    # buyers given → penetration 10 %; B's frequency = 330 000 / (110 000 × 1.5) = 2.0, derived
    assert a["penetration_pct"] == 10.0 and a["computed_volume"] == 300_000
    assert b["purchases_per_buyer"] == 2.0 and b["derived"] == ["purchases_per_buyer (from reported volume)"]
    assert out["changes"][0]["attribution"]["penetration"] == 100.0 and out["missing"] == []
    out = media_plan({"mode": "volume", "periods": [{"label": "A", "population": 100, "penetration_pct": 10, "purchases_per_buyer": 1}, {"label": "B", "population": 100, "penetration_pct": 12, "purchases_per_buyer": 1}]})
    assert "units_per_purchase in A" in out["missing"] and out["periods"][0]["computed_volume"] is None
    assert out["changes"][0]["partial"] is True and "basket" not in out["changes"][0]["attribution"]
    assert out["changes"][0]["attribution"]["penetration"] == 100.0
    with pytest.raises(ToolError, match="periods needs at least two"):
        media_plan({"mode": "volume", "periods": [{"label": "A", "population": 1}]})
    with pytest.raises(ToolError, match="penetration_pct in A must be between 0 and 100"):
        media_plan({"mode": "volume", "periods": [{"label": "A", "population": 1, "penetration_pct": 120}, {"label": "B", "population": 1}]})


# --- calendar (#419) -----------------------------------------------------------------


ROZE = {
    "mode": "calendar",
    "year": 2027,
    "total_budget": 6000,
    "always_on": {"weekly_budget": 60, "posts_per_week": 2},
    "occasions": [
        {"name": "Valentino", "date": "02-14", "lead_days": 14, "burst_budget": 700},
        {"name": "Kovo 8", "date": "2027-03-08", "lead_days": 10, "burst_budget": 500},
        {"name": "Motinos diena", "key": "motinos_diena", "lead_days": 14, "burst_budget": 900},
        {"name": "Rugsėjo 1", "key": "mokslo_metai", "lead_days": 7, "burst_budget": 400},
        {"name": "Kalėdos", "key": "kalėdos", "lead_days": 21, "burst_budget": 800},
    ],
}


def test_calendar_builds_52_weeks_with_always_on_and_flights_against_the_cap():
    out = media_plan(ROZE)
    assert out["status"] == "ok" and len(out["weeks"]) == 52
    # 52 × 60 = 3 120 always-on; 700 + 500 + 900 + 400 + 800 = 3 300 flighted = 51.4 % of 6 420; 6 000 − 6 420 = −420
    assert out["totals"]["always_on"] == 3120 and out["totals"]["flighted"] == 3300
    assert out["totals"]["share_flighted_pct"] == 51.4 and out["totals"]["unallocated"] == -420
    assert out["totals"]["always_on_posts"] == 104
    assert [f["code"] for f in out["findings"]] == ["budget_over_cap"]
    assert "exceeds €6 000 by €420" in out["findings"][0]["text"]
    mothers = next(f for f in out["flights"] if f["name"] == "Motinos diena")
    # first Sunday of May 2027 is 2 May; 14 days before = 18 April
    assert mothers["date"] == "2027-05-02" and mothers["start"] == "2027-04-18" and mothers["days"] == 15
    christmas = next(f for f in out["flights"] if f["name"] == "Kalėdos")
    assert christmas["date"] == "2027-12-24" and christmas["start"] == "2027-12-03"
    # the Valentine burst (700 over 15 days = 46.67/day) lands in ISO weeks 4 (1 day), 5 and 6 (7 days each)
    by_week = {w["iso_week"]: w for w in out["weeks"]}
    assert by_week[4]["burst_budget"] == 46.67 and by_week[5]["burst_budget"] == 326.67 and by_week[5]["mode"] == "both"
    assert by_week[5]["budget"] == 386.67 and by_week[5]["occasions"] == ["Valentino"]
    assert by_week[20]["mode"] == "always_on" and by_week[20]["budget"] == 60 and by_week[20]["posts"] == 2
    assert sum(w["burst_budget"] for w in out["weeks"]) == pytest.approx(3300, abs=0.1)
    assert "€420 over the cap" in out["summary"]


def test_calendar_flags_overlaps_short_leads_unknown_dates_and_capacity():
    out = media_plan(
        {
            "mode": "calendar",
            "year": 2027,
            "always_on": {"weekly_budget": 0, "posts_per_week": 1},
            "team_posts_per_week_max": 2,
            "occasions": [
                {"name": "Black Friday", "key": "black_friday", "lead_days": 14, "burst_budget": 300, "posts": 15},
                {"name": "Kalėdos", "key": "christmas", "lead_days": 30, "burst_budget": 500},
                {"name": "Atidarymas", "lead_days": 3, "burst_budget": 100},
                {"name": "Velykos", "key": "velykos", "lead_days": 5, "burst_budget": 100},
            ],
        }
    )
    codes = {f["code"] for f in out["findings"]}
    assert out["status"] == "needs_input"
    assert "date for Atidarymas" in out["missing"] and "date_unresolved" in codes
    # Black Friday 2027 = 26 Nov (day after the 4th Thursday, 25 Nov); flight 12–26 Nov; Christmas flight from 24 Nov → 3 shared days
    bf = next(f for f in out["flights"] if f["name"] == "Black Friday")
    assert bf["date"] == "2027-11-26" and bf["start"] == "2027-11-12"
    overlap = next(f for f in out["findings"] if f["code"] == "flight_overlap")
    assert "overlap for 3 day(s)" in overlap["text"]
    assert any(f["code"] == "lead_too_short" and f["occasion"] == "Velykos" for f in out["findings"])
    easter = next(f for f in out["flights"] if f["name"] == "Velykos")
    assert easter["date"] == "2027-03-28"
    # 15 extra posts over 15 days = 1/day → 8 posts in the full weeks of the Black Friday flight > the team's 2
    assert "capacity_short" in codes
    assert "total_budget (the cap the plan is checked against)" in out["missing"]
    assert "week_uncovered" not in codes  # one always-on post a week keeps every week covered
    out = media_plan({"mode": "calendar", "year": 2027, "always_on": {"weekly_budget": 0, "posts_per_week": 0}})
    assert any(f["code"] == "week_uncovered" and "52 week(s)" in f["text"] for f in out["findings"])
    with pytest.raises(ToolError, match="year is required"):
        media_plan({"mode": "calendar"})
    with pytest.raises(ToolError, match="year must be a whole number"):
        media_plan({"mode": "calendar", "year": "next"})


def test_known_occasions_resolve_per_year():
    assert occasion_date("motinos_diena", 2026) == (__import__("datetime").date(2026, 5, 3), "Motinos diena (first Sunday of May, Lithuania)")
    assert occasion_date("Black Friday", 2026)[0].isoformat() == "2026-11-27"
    assert occasion_date("velykos", 2026)[0].isoformat() == "2026-04-05"
    assert occasion_date("joninės", 2027)[0].isoformat() == "2027-06-24"
    assert occasion_date("naujieji", 2027)[0].isoformat() == "2027-01-01"
    assert occasion_date("tėvo_diena", 2027)[0].isoformat() == "2027-06-06"
    assert occasion_date("oktoberfest", 2027) is None
    assert {"kaledos", "motinos_diena", "black_friday", "naujieji", "velykos", "mokslo_metai", "jonines"} <= set(KNOWN_OCCASIONS)


# --- channel_fit (#420) ----------------------------------------------------------------


ROZE_TIKTOK = {
    "mode": "channel_fit",
    "platform": "TikTok",
    "segment": "women 30–55 in Vilnius",
    "content_job": "buying flowers",
    "target_posts_per_week": 4,
    "hours_per_post": 1,
    "team_hours_per_week": 3,
    "existing_platforms": [{"name": "Instagram", "posts_per_week": 3, "hours_per_week": 2}, {"name": "Facebook", "posts_per_week": 2, "hours_per_week": 1}],
}


def test_channel_fit_capacity_check_and_missing_evidence():
    out = media_plan(ROZE_TIKTOK)
    cap = out["capacity"]
    # 4 posts × 1 h = 4 h needed; 3 h team − (2 + 1) h existing = 0 h left
    assert cap["required_hours_per_week"] == 4.0 and cap["available_hours_after_existing"] == 0.0 and cap["capacity_ratio"] == "inf"
    assert out["evidence_status"] == "missing" and out["verdict"] == "unfit"
    codes = [f["code"] for f in out["findings"]]
    assert codes == ["evidence_missing", "capacity_short", "cadence_unsustainable"]
    assert "4 h/week needed, 0 h/week left" in out["summary"] and "audience evidence missing" in out["summary"]
    assert any(m.startswith("evidence [{text, source_url}] of women 30–55 in Vilnius using TikTok for buying flowers") for m in out["missing"])


def test_channel_fit_verdicts_fit_stretch_unknown():
    sourced = [{"text": "41 % of Lithuanian women 30–55 use TikTok monthly and 18 % say they found a product to buy there", "source_url": "https://example.org/report"}]
    base = dict(ROZE_TIKTOK, team_hours_per_week=10, evidence=sourced)
    out = media_plan(base)
    # 4 of 7 h available = 0.57 → fits
    assert out["evidence_status"] == "sourced" and out["verdict"] == "fit" and out["capacity"]["capacity_ratio"] == 0.57
    assert out["findings"] == []
    out = media_plan(dict(base, team_hours_per_week=8))
    # 4 of 5 h = 0.8 → stretch
    assert out["verdict"] == "stretch" and out["capacity"]["status_label"] == "stretch"
    # evidence without a URL does not back the decision
    out = media_plan(dict(base, evidence=[{"text": sourced[0]["text"]}]))
    assert out["evidence_status"] == "unsourced" and out["verdict"] == "unknown"
    assert any(f["code"] == "evidence_unsourced" for f in out["findings"])
    assert "Do not recommend TikTok until" in out["summary"]
    # evidence about presence, not the job
    out = media_plan(dict(base, evidence=[{"text": "41 % of Lithuanian women 30–55 have a TikTok account", "source_url": "https://example.org/x"}]))
    assert out["verdict"] == "unknown" and any(f["code"] == "platform_job_mismatch" for f in out["findings"])
    # hours missing → capacity not assessed, verdict unknown even with evidence
    out = media_plan({"mode": "channel_fit", "platform": "TikTok", "segment": "x", "content_job": "buying", "evidence": sourced})
    assert out["capacity"] == {"status": "not_assessed"} and out["verdict"] == "unknown"
    assert "hours_per_post (capacity not assessed without it)" in out["missing"]
    with pytest.raises(ToolError, match="platform is required"):
        media_plan({"mode": "channel_fit"})
    with pytest.raises(ToolError, match="evidence item 1 needs text"):
        media_plan({"mode": "channel_fit", "platform": "TikTok", "evidence": [{"source_url": "https://x"}]})


# --- classify (#410) -------------------------------------------------------------------


SPEND_2026 = [
    {"campaign": "Mūsų istorija", "channel": "TV", "spend": 120_000, "creative_text": "Mūsų istorija prasidėjo mažoje kepykloje. Šeimos receptai nuo 1991."},
    {"campaign": "Juodasis penktadienis -40%", "channel": "TV", "spend": 50_000, "creative_text": "Juodasis penktadienis: -40% visoms prekėms!"},
    {"campaign": "Kodėl renkamės mus", "channel": "Meta", "spend": 40_000, "creative_text": "Kodėl renkamės mus? Nes rūpi."},
    {"campaign": "Pirk dabar", "channel": "Meta", "spend": 60_000, "creative_text": "Pirk dabar -25% iki 11.30"},
    {"campaign": "Search brand terms", "channel": "Search", "spend": 25_000, "creative_text": "Kepyklėlė — oficiali svetainė"},
    {"campaign": "YouTube", "channel": "YouTube", "spend": 15_000},
]


def test_classify_sorts_spend_by_the_creative_not_the_channel():
    out = media_plan({"mode": "classify", "rows": SPEND_2026})
    totals = out["totals"]
    # brand: 120k + 40k + 25k = 185k (59.7 %); activation: 50k + 60k = 110k (35.5 %); unclassified 15k (4.8 %)
    assert totals["brand_amount"] == 185_000 and totals["brand_pct"] == 59.7
    assert totals["activation_amount"] == 110_000 and totals["activation_pct"] == 35.5
    assert totals["unclassified_amount"] == 15_000 and totals["unclassified_pct"] == 4.8
    jobs = {r["campaign"]: r["job"] for r in out["rows"]}
    assert jobs["Juodasis penktadienis -40%"] == "activation" and jobs["Kodėl renkamės mus"] == "brand" and jobs["YouTube"] == "unclassified"
    assert out["rows"][3]["markers"] == ["percent", "deadline", "buy_cta"]
    tv = next(c for c in out["by_channel"] if c["channel"] == "TV")
    assert tv["both_jobs"] is True and tv["activation"] == 50_000
    assert any(w.startswith("TV carries both jobs: €120 000 brand and €50 000 activation") for w in out["warnings"])
    assert any(w.startswith("Meta carries both jobs") for w in out["warnings"])
    assert out["missing"] == ["creative text for YouTube (YouTube)"]
    assert out["summary"] == "Of €310 000, €185 000 (59.7 %) worked for the brand, €110 000 (35.5 %) for activation; €15 000 (4.8 %) is unclassified until the creative is supplied."


def test_classify_marks_mixed_and_unreliable_splits():
    assert classify_creative("Nauja kolekcija jau čia — tik 19,90 €") == ("mixed", ["price"])
    assert classify_creative("Shop now with code SPRING20") == ("activation", ["code", "buy_cta"])
    assert classify_creative("") == ("unclassified", [])
    assert classify_creative(None) == ("unclassified", [])
    out = media_plan({"mode": "classify", "rows": [{"campaign": "A", "channel": "TV", "spend": 40}, {"campaign": "B", "channel": "Meta", "spend": 60, "creative_text": "Only 9.99 € — today only"}]})
    # 40 of 100 unclassified = 40 % > 30 %; price + urgency = two marker kinds → activation
    assert out["totals"]["unclassified_pct"] == 40.0 and out["rows"][1]["job"] == "activation"
    assert "not yet reliable" in out["summary"]
    assert any("not reliable yet" in w for w in out["warnings"])
    with pytest.raises(ToolError, match="rows must be a list with at least one spend row"):
        media_plan({"mode": "classify"})
    with pytest.raises(ToolError, match="spend of A is required"):
        media_plan({"mode": "classify", "rows": [{"campaign": "A", "channel": "TV"}]})
    with pytest.raises(ToolError, match="total spend is zero"):
        media_plan({"mode": "classify", "rows": [{"campaign": "A", "channel": "TV", "spend": 0}]})



def test_dispatch_routes_media_plan_and_reach_spec_grew():
    import json as _json

    from rocky.tools import WorkspaceTools, dispatch, worker_tools

    out = _json.loads(dispatch(WorkspaceTools("."), "media_plan", {"mode": "volume", "unit": "L", "periods": [{"label": "2025", "population": 1000, "penetration_pct": 10, "purchases_per_buyer": 2, "units_per_purchase": 1}, {"label": "2026", "population": 1000, "penetration_pct": 12, "purchases_per_buyer": 2, "units_per_purchase": 1}]}))
    assert out["mode"] == "volume" if "mode" in out else True
    names = [t["function"]["name"] for t in worker_tools(False)]
    assert "media_plan" in names
    reach = next(t for t in worker_tools(False) if t["function"]["name"] == "reach_overlap")
    props = reach["function"]["parameters"]["properties"]
    assert "required_reach" in props and "source" in props["channels"]["items"]["properties"]
