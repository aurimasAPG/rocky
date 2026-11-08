import json

import pytest

from rocky.funnel import funnel_analyze, funnel_analyze_tool
from rocky.tools import ToolError, WorkspaceTools

STAGES = ["visits", "product_views", "add_to_cart", "checkout", "orders"]

WIDE_CSV = """date,channel,visits,product_views,add_to_cart,checkout,orders
2026-09-01,google,4000,2000,600,250,100
2026-09-10,google,4000,1900,700,220,110
2026-09-20,meta,3000,1500,400,180,70
2026-09-30,meta,1400,600,200,70,30
"""


def _ws(tmp_path, name, text):
    (tmp_path / name).write_text(text, encoding="utf-8")
    return WorkspaceTools(tmp_path)


def test_wide_csv_gives_counts_percentages_and_the_largest_dropoff(tmp_path):
    tools = _ws(tmp_path, "funnel.csv", WIDE_CSV)
    out = funnel_analyze(tools, {"path": "funnel.csv", "stages": STAGES, "date_column": "date"})
    assert [s["name"] for s in out["stages"]] == STAGES
    assert [s["count"] for s in out["stages"]] == [12400, 6000, 1900, 720, 310]
    assert out["stages"][0]["from_previous_pct"] is None and out["stages"][0]["from_first_pct"] == 100.0
    assert out["stages"][2]["from_previous_pct"] == 31.7 and out["stages"][2]["from_first_pct"] == 15.3
    assert out["overall_conversion_pct"] == 2.5
    drop = out["largest_dropoff"]
    assert (drop["from_stage"], drop["to_stage"], drop["lost"]) == ("visits", "product_views", 6400)
    assert drop["lost_pct_of_previous"] == 51.6 and drop["lost_pct_of_first"] == 51.6
    # the relative loss is at a different step and is named separately
    rel = out["largest_relative_dropoff"]
    assert (rel["from_stage"], rel["to_stage"]) == ("product_views", "add_to_cart") and rel["lost_pct_of_previous"] == 68.3
    assert out["window"] == {"from": "2026-09-01", "to": "2026-09-30", "rows_in_window": 4, "rows_total": 4, "rows_without_date": 0}
    assert out["missing"] == [] and out["warnings"] == []
    assert out["summary"].startswith("Between 2026-09-01 and 2026-09-30: 12,400 visits → 310 orders (2.5 %).")
    assert "Largest loss: visits → product_views, 6,400 of 12,400 (51.6 %)." in out["summary"]


def test_date_filter_narrows_the_rows_and_the_window_shows_the_dates_used(tmp_path):
    tools = _ws(tmp_path, "funnel.csv", WIDE_CSV)
    out = funnel_analyze(
        tools, {"path": "funnel.csv", "stages": STAGES, "date_column": "date", "date_from": "2026-09-05", "date_to": "2026-09-25"}
    )
    assert out["window"]["from"] == "2026-09-10" and out["window"]["to"] == "2026-09-20"
    assert out["window"]["rows_in_window"] == 2 and out["window"]["rows_total"] == 4
    assert out["stages"][0]["count"] == 7000 and out["stages"][-1]["count"] == 180
    assert out["inputs"]["date_from"] == "2026-09-05"


def test_empty_window_returns_zeros_and_says_why(tmp_path):
    tools = _ws(tmp_path, "funnel.csv", WIDE_CSV)
    out = funnel_analyze(tools, {"path": "funnel.csv", "stages": STAGES, "date_column": "date", "date_from": "2027-01-01"})
    assert [s["count"] for s in out["stages"]] == [0, 0, 0, 0, 0]
    assert out["overall_conversion_pct"] is None and out["largest_dropoff"] is None
    assert any("window is empty" in m for m in out["missing"])
    assert out["window"]["from"] is None


def test_group_by_gives_per_channel_funnels_and_a_worst_group(tmp_path):
    tools = _ws(tmp_path, "funnel.csv", WIDE_CSV)
    out = funnel_analyze(tools, {"path": "funnel.csv", "stages": STAGES, "date_column": "date", "group_by": "channel"})
    groups = {g["group"]: g for g in out["groups"]}
    assert [g["group"] for g in out["groups"]] == ["google", "meta"]  # first-stage count desc
    assert groups["google"]["stages"][0]["count"] == 8000 and groups["google"]["overall_conversion_pct"] == 2.6
    assert groups["meta"]["stages"][-1]["count"] == 100 and groups["meta"]["overall_conversion_pct"] == 2.3
    assert out["worst_group"]["group"] == "meta" and out["worst_group"]["overall_conversion_pct"] == 2.3
    assert "30" in out["worst_group_threshold"]


def test_small_groups_are_listed_but_not_ranked(tmp_path):
    csv = "channel,visits,orders\nbig,500,20\ntiny,10,0\n"
    tools = _ws(tmp_path, "f.csv", csv)
    out = funnel_analyze(tools, {"path": "f.csv", "stages": ["visits", "orders"], "group_by": "channel"})
    assert [g["group"] for g in out["groups"]] == ["big", "tiny"]
    assert out["worst_group"]["group"] == "big"


def test_long_layout_with_id_column_counts_unique_users_per_stage(tmp_path):
    csv = (
        "date,user,event\n"
        "01.09.2026,u1,visit\n01.09.2026,u1,visit\n01.09.2026,u2,visit\n02.09.2026,u3,visit\n"
        "02.09.2026,u1,cart\n02.09.2026,u1,cart\n03.09.2026,u2,cart\n"
        "03.09.2026,u1,order\n"
    )
    tools = _ws(tmp_path, "events.csv", csv)
    out = funnel_analyze(tools, {"path": "events.csv", "stage_column": "event", "id_column": "user", "date_column": "date"})
    assert out["inputs"]["layout"] == "long"
    assert [(s["name"], s["count"]) for s in out["stages"]] == [("visit", 3), ("cart", 2), ("order", 1)]
    assert out["window"]["from"] == "2026-09-01" and out["window"]["to"] == "2026-09-03"
    assert out["largest_dropoff"]["from_stage"] == "visit" and out["largest_dropoff"]["lost"] == 1
    assert any("Fewer than 100" in w for w in out["warnings"])


def test_long_layout_without_count_column_counts_rows_and_respects_stage_order(tmp_path):
    csv = "step\ncheckout\nvisit\nvisit\nvisit\ncart\ncart\ncheckout\nrefund\n"
    tools = _ws(tmp_path, "rows.csv", csv)
    out = funnel_analyze(tools, {"path": "rows.csv", "stage_column": "step", "stage_order": ["visit", "cart", "checkout", "paid"]})
    assert [(s["name"], s["count"]) for s in out["stages"]] == [("visit", 3), ("cart", 2), ("checkout", 2), ("paid", 0)]
    assert any("paid" in m for m in out["missing"]) and any("refund" in m for m in out["missing"])
    assert out["window"]["from"] is None and out["summary"].startswith("Across 8 rows:")


def test_long_layout_sums_a_count_column(tmp_path):
    csv = "stage;count\nvisits;1 200\nleads;300\ncustomers;45\n"
    tools = _ws(tmp_path, "agg.csv", csv)
    out = funnel_analyze(tools, {"path": "agg.csv", "stage_column": "stage", "count_column": "count"})
    assert [s["count"] for s in out["stages"]] == [1200, 300, 45]
    assert out["overall_conversion_pct"] == 3.8


def test_xlsx_input_is_read_with_openpyxl(tmp_path):
    from openpyxl import Workbook

    from datetime import datetime

    wb = Workbook()
    ws = wb.active
    ws.title = "data"
    ws.append(["Date", "Visits", "Leads", "Deals"])
    ws.append([datetime(2026, 10, 1), 500, 50, 5])
    ws.append([datetime(2026, 10, 15), 700, 70, 9])
    wb.save(tmp_path / "pipeline.xlsx")
    tools = WorkspaceTools(tmp_path)
    out = funnel_analyze(
        tools, {"path": "pipeline.xlsx", "sheet": "data", "stages": ["visits", "leads", "deals"], "date_column": "date"}
    )
    assert [s["count"] for s in out["stages"]] == [1200, 120, 14]
    assert out["window"]["from"] == "2026-10-01" and out["window"]["to"] == "2026-10-15"
    assert out["inputs"]["stages"] == ["Visits", "Leads", "Deals"]


def test_blank_cells_bad_numbers_and_bad_dates_are_reported_under_missing(tmp_path):
    csv = (
        "date,visits,orders\n"
        "2026-09-01,1000,20\n"
        "2026-09-02,,10\n"
        "2026-09-03,abc,5\n"
        "not a date,500,7\n"
    )
    tools = _ws(tmp_path, "messy.csv", csv)
    out = funnel_analyze(tools, {"path": "messy.csv", "stages": ["visits", "orders"], "date_column": "date"})
    assert [s["count"] for s in out["stages"]] == [1000, 30]
    assert out["window"]["rows_without_date"] == 1 and out["window"]["rows_in_window"] == 3
    joined = " ".join(out["missing"])
    assert "visits: 1 blank cell counted as 0" in joined
    assert "1 row dropped because visits was not a number" in joined
    assert "1 row without a readable date" in joined


def test_a_later_stage_larger_than_an_earlier_one_warns(tmp_path):
    csv = "visits,carts,orders\n1000,1200,50\n"
    tools = _ws(tmp_path, "odd.csv", csv)
    out = funnel_analyze(tools, {"path": "odd.csv", "stages": ["visits", "carts", "orders"]})
    assert any("carts (1,200) is larger than visits (1,000): stage order or data is wrong" in w for w in out["warnings"])
    assert out["largest_dropoff"]["from_stage"] == "carts"


def test_all_zero_counts_are_named_not_invented(tmp_path):
    csv = "visits,orders\n0,0\n0,0\n"
    tools = _ws(tmp_path, "zero.csv", csv)
    out = funnel_analyze(tools, {"path": "zero.csv", "stages": ["visits", "orders"]})
    assert [s["count"] for s in out["stages"]] == [0, 0]
    assert out["largest_dropoff"] is None and out["overall_conversion_pct"] is None
    assert any("Every stage count is zero" in m for m in out["missing"])


def test_missing_column_error_lists_the_available_columns(tmp_path):
    tools = _ws(tmp_path, "funnel.csv", WIDE_CSV)
    with pytest.raises(ToolError) as err:
        funnel_analyze(tools, {"path": "funnel.csv", "stages": ["visits", "purchases"]})
    message = str(err.value)
    assert "'purchases' not found" in message
    assert "visits, product_views, add_to_cart, checkout, orders" in message


def test_plain_errors_for_missing_file_bad_extension_and_too_few_stages(tmp_path):
    tools = _ws(tmp_path, "funnel.csv", WIDE_CSV)
    (tmp_path / "notes.pdf").write_bytes(b"%PDF")
    with pytest.raises(ToolError, match="not found"):
        funnel_analyze(tools, {"path": "nope.csv", "stages": STAGES})
    with pytest.raises(ToolError, match="unsupported file type"):
        funnel_analyze(tools, {"path": "notes.pdf", "stages": STAGES})
    with pytest.raises(ToolError, match="at least two stages"):
        funnel_analyze(tools, {"path": "funnel.csv", "stages": ["visits"]})
    with pytest.raises(ToolError, match="stages"):
        funnel_analyze(tools, {"path": "funnel.csv"})


def test_a_path_outside_the_workspace_is_refused(tmp_path):
    outside = tmp_path / "outside.csv"
    outside.write_text(WIDE_CSV, encoding="utf-8")
    tools = WorkspaceTools(tmp_path / "ws")
    with pytest.raises(ToolError, match="escapes workspace"):
        funnel_analyze(tools, {"path": "../outside.csv", "stages": STAGES})
    with pytest.raises(ToolError, match="escapes workspace"):
        funnel_analyze(tools, {"path": str(outside), "stages": STAGES})


def test_tool_wrapper_returns_json(tmp_path):
    tools = _ws(tmp_path, "funnel.csv", WIDE_CSV)
    text = funnel_analyze_tool(tools, {"path": "funnel.csv", "stages": STAGES, "date_column": "date"})
    data = json.loads(text)
    assert data["overall_conversion_pct"] == 2.5 and "→" in data["summary"]


# --- group comparison with statistics and costs ------------------------------

SAMPLES_CSV = """arm,date,requested,paid
50g,2026-09-01,1200,96
250g,2026-09-03,1150,161
"""


def test_compare_groups_runs_a_two_proportion_test_and_prices_each_arm(tmp_path):
    tools = _ws(tmp_path, "samples.csv", SAMPLES_CSV)
    out = funnel_analyze(
        tools,
        {
            "path": "samples.csv",
            "stages": ["requested", "paid"],
            "date_column": "date",
            "group_by": "arm",
            "cost_per_group": {"50g": {"fulfilment_per_unit": 1.10}, "250g": {"fulfilment_per_unit": 3.40}},
        },
    )
    cmp = out["comparison"]
    assert cmp["stage"] == "paid" and cmp["baseline_group"] == "50g" and cmp["min_n"] == 100
    base, other = cmp["groups"]
    assert (base["group"], base["rate_pct"], base["verdict"]) == ("50g", 8.0, "baseline")
    assert (other["group"], other["rate_pct"], other["diff_pp"], other["verdict"]) == ("250g", 14.0, 6.0, "better")
    assert other["ci95_pp"] == [3.47, 8.53] and other["z"] == 4.66 and other["p_value"] < 0.001
    assert other["incremental_vs_baseline"] == 69  # 161 paid against 1150 × 8 % = 92 at the 50 g rate
    assert cmp["concurrent"] is True and "z-test" in cmp["method"]
    costs = {c["group"]: c for c in out["cost"]["groups"]}
    assert costs["50g"]["net_cost"] == 1320 and costs["50g"]["cost_per_converted"] == 13.75
    assert costs["250g"]["net_cost"] == 3910 and costs["250g"]["cost_per_converted"] == 24.29
    assert out["cost"]["pooled_cost_per_converted"] == 20.35 and out["cost"]["naive_mean_of_group_costs"] == 19.02
    assert out["cost"]["cheapest_group"] == "50g"
    assert "At paid: 50g 8 % vs 250g 14 % (+6 pp, p = 0, better)." in out["summary"]
    assert "Cost per paid (EUR): 50g 13.75, 250g 24.29; pooled 20.35." in out["summary"]
    assert not any("not concurrent" in w for w in out["warnings"])


def test_cost_components_give_full_acquisition_cost_and_media_per_converted(tmp_path):
    csv = "arm,requests,paid_90d\nfree,2400,120\nfee1,600,90\n"
    tools = _ws(tmp_path, "sampling.csv", csv)
    out = funnel_analyze(
        tools,
        {
            "path": "sampling.csv",
            "stages": ["requests", "paid_90d"],
            "group_by": "arm",
            "compare_groups": ["free", "fee1"],
            "cost_per_group": {
                "free": {"media": 1800, "fulfilment_per_unit": 2.0},
                "fee1": {"media": 1800, "fulfilment_per_unit": 2.0, "fee_per_unit": 1.0},
            },
        },
    )
    costs = {c["group"]: c for c in out["cost"]["groups"]}
    assert costs["free"]["net_cost"] == 6600 and costs["free"]["cost_per_converted"] == 55.0 and costs["free"]["media_per_converted"] == 15.0
    assert costs["fee1"]["net_cost"] == 2400 and costs["fee1"]["cost_per_converted"] == 26.67 and costs["fee1"]["media_per_converted"] == 20.0
    fee = out["comparison"]["groups"][1]
    assert (fee["group"], fee["rate_pct"], fee["diff_pp"], fee["verdict"]) == ("fee1", 15.0, 10.0, "better")
    assert out["comparison"]["concurrent"] is None and "no date_column" in out["comparison"]["concurrency_note"]
    # pooled 9000/210 = 42.86 against the mean of the two ratios 40.84: within 10 %, so no warning
    assert out["cost"]["pooled_cost_per_converted"] == 42.86 and out["cost"]["naive_mean_of_group_costs"] == 40.84
    assert not any("pooled" in w for w in out["warnings"])


def test_small_groups_get_insufficient_data_and_unknown_compare_groups_are_refused(tmp_path):
    csv = "channel,visits,orders\nbig,500,20\ntiny,10,0\n"
    tools = _ws(tmp_path, "f.csv", csv)
    out = funnel_analyze(tools, {"path": "f.csv", "stages": ["visits", "orders"], "group_by": "channel"})
    tiny = out["comparison"]["groups"][1]
    assert tiny["verdict"] == "insufficient_data" and "fewer than 100" in tiny["reason"]
    assert tiny["diff_pp"] == -4.0  # the figures are still reported, only the verdict is withheld
    with pytest.raises(ToolError, match="not in the data: huge"):
        funnel_analyze(tools, {"path": "f.csv", "stages": ["visits", "orders"], "group_by": "channel", "compare_groups": ["big", "huge"]})
    with pytest.raises(ToolError, match="not a stage"):
        funnel_analyze(tools, {"path": "f.csv", "stages": ["visits", "orders"], "group_by": "channel", "compare_stage": "paid"})
    # no groups: the comparison inputs are named under missing instead of silently ignored
    out = funnel_analyze(tools, {"path": "f.csv", "stages": ["visits", "orders"], "compare_groups": ["big", "tiny"]})
    assert "comparison" not in out and any("need group_by" in m for m in out["missing"])


def test_groups_with_different_date_windows_warn_not_concurrent_and_pooled_cost_beats_the_naive_mean(tmp_path):
    csv = (
        "placement,date,impressions,inquiries,spend\n"
        "Delfi,2026-10-01,10000,60,1200\n"
        "15min,2026-10-15,9000,50,900\n"
        "Lrytas,2026-10-28,3000,6,300\n"
        "Facebook,2026-10-02,20000,84,600\n"
    )
    tools = _ws(tmp_path, "leads.csv", csv)
    out = funnel_analyze(
        tools,
        {"path": "leads.csv", "stages": ["impressions", "inquiries"], "date_column": "date", "group_by": "placement", "cost_column": "spend"},
    )
    assert out["comparison"]["concurrent"] is False
    assert any(w.startswith("groups not concurrent") for w in out["warnings"])
    cost = out["cost"]
    assert cost["pooled_cost_per_converted"] == 15.0 and cost["naive_mean_of_group_costs"] == 23.79
    assert any("pooled" in w and "23.79" in w for w in out["warnings"])
    by = {c["group"]: c for c in cost["groups"]}
    assert by["Lrytas"]["cost_per_converted"] == 50.0 and cost["cheapest_group"] == "Facebook" and cost["dearest_group"] == "Delfi"
    assert by["Facebook"]["spend_share_pct"] == 20.0 and by["Facebook"]["converted_share_pct"] == 42.0 and by["Facebook"]["contribution_index"] == 2.1


def test_attention_column_flags_publicity_without_sales(tmp_path):
    csv = (
        "channel,impressions,orders,spend\n"
        "TikTok,2100000,310,18000\n"
        "Meta,640000,1240,22000\n"
        "Google,90000,980,15000\n"
    )
    tools = _ws(tmp_path, "channels.csv", csv)
    out = funnel_analyze(
        tools,
        {
            "path": "channels.csv",
            "stages": ["impressions", "orders"],
            "group_by": "channel",
            "cost_per_group": {"TikTok": 18000, "Meta": 22000, "Google": 15000},
            "attention_column": "impressions",
        },
    )
    by = {c["group"]: c for c in out["cost"]["groups"]}
    assert by["TikTok"]["spend_share_pct"] == 32.7 and by["TikTok"]["converted_share_pct"] == 12.3
    assert by["TikTok"]["contribution_index"] == 0.38 and by["TikTok"]["attention_share_pct"] == 74.2
    assert by["TikTok"]["flags"] == ["publicity_without_sales"]
    assert by["Google"]["flags"] == ["quiet_contributor"] and by["Google"]["cost_per_converted"] == 15.31
    assert by["Google"]["contribution_index"] == 1.42


def test_cost_for_an_unknown_group_or_a_bad_component_is_reported_or_refused(tmp_path):
    tools = _ws(tmp_path, "samples.csv", SAMPLES_CSV)
    base = {"path": "samples.csv", "stages": ["requested", "paid"], "group_by": "arm"}
    out = funnel_analyze(tools, dict(base, cost_per_group={"50g": 1320, "1kg": 5}))
    assert any("not in the data: 1kg" in m for m in out["missing"])
    assert any("no cost for group 250g" in m for m in out["missing"])
    assert out["cost"]["groups"][1]["cost_per_converted"] is None
    with pytest.raises(ToolError, match="unknown keys"):
        funnel_analyze(tools, dict(base, cost_per_group={"50g": {"shipping": 3}}))
    with pytest.raises(ToolError, match="must be a number"):
        funnel_analyze(tools, dict(base, cost_per_group={"50g": "cheap"}))


# --- metrics per group ---------------------------------------------------------


def test_metrics_and_share_of_wallet_flag_selection_bias(tmp_path):
    rows = ["hh_id,visited_site,households,buyer,spend_brand,spend_category"]
    for i in range(4):
        rows.append(f"v{i},1,1,1,310,1240")
    for i in range(4):
        rows.append(f"n{i},0,1,1,120,560")
    tools = _ws(tmp_path, "panel.csv", "\n".join(rows) + "\n")
    out = funnel_analyze(
        tools,
        {
            "path": "panel.csv",
            "stages": ["households", "buyer"],
            "group_by": "visited_site",
            "compare_groups": ["1", "0"],
            "share_of": {"numerator": "spend_brand", "denominator": "spend_category"},
        },
    )
    m = out["metrics"]
    assert m["groups"]["1"]["spend_brand"]["mean"] == 310.0 and m["groups"]["1"]["share_of_wallet_pct"] == 25.0
    assert m["groups"]["0"]["share_of_wallet_pct"] == 21.4
    assert m["selection_bias"] == "likely" and m["share_of"]["exposed_group"] == "1"
    assert any("selection bias likely" in w and "121.4 %" in w for w in out["warnings"])


def test_metrics_flag_unequal_treatment_and_report_blank_cells(tmp_path):
    rows = ["ad,lead,signed,response_hours"]
    rows += [f"A,1,{1 if i < 2 else 0},{48 if i else ''}" for i in range(5)]
    rows += [f"B,1,{1 if i < 1 else 0},6" for i in range(5)]
    rows += [f"C,1,{1 if i < 3 else 0},5" for i in range(5)]
    tools = _ws(tmp_path, "leads.csv", "\n".join(rows) + "\n")
    out = funnel_analyze(tools, {"path": "leads.csv", "stages": ["lead", "signed"], "group_by": "ad", "metrics": ["response_hours"]})
    m = out["metrics"]
    assert m["groups"]["A"]["response_hours"] == {"n": 4, "mean": 48.0, "median": 48.0, "sum": 192}
    assert m["unequal"] == [{"group": "A", "column": "response_hours", "median": 48.0, "others_median": 5.5}]
    assert m["comparable"] is False and "selection_bias" not in m
    assert any("response_hours (A): 1 blank cell" in x for x in out["missing"])
    with pytest.raises(ToolError, match="metrics column 'sla'"):
        funnel_analyze(tools, {"path": "leads.csv", "stages": ["lead", "signed"], "group_by": "ad", "metrics": ["sla"]})


# --- lag between two dates -----------------------------------------------------

REDEMPTIONS_CSV = """code,sent_date,redeemed_date
EMAIL,2026-11-24,2026-11-24
EMAIL,2026-11-24,2026-11-24
EMAIL,2026-11-24,2026-11-26
META,2026-11-24,2026-11-29
META,2026-11-24,2026-12-03
META,2026-11-24,
META,2026-11-25,2026-11-20
"""


def test_lag_buckets_median_and_share_after_day_0_per_group(tmp_path):
    tools = _ws(tmp_path, "red.csv", REDEMPTIONS_CSV)
    out = funnel_analyze(
        tools,
        {"path": "red.csv", "stage_column": "code", "lag_from": "sent_date", "lag_to": "redeemed_date", "group_by": "code"},
    )
    lag = out["lag"]
    assert lag["rows_with_both_dates"] == 5
    assert [(b["label"], b["count"], b["share_pct"]) for b in lag["buckets"]] == [
        ("0 days", 2, 40.0), ("1–3 days", 1, 20.0), ("4–7 days", 1, 20.0), ("8+ days", 1, 20.0)
    ]
    assert lag["median_days"] == 2 and lag["after_day_0_share_pct"] == 60.0
    assert lag["groups"]["EMAIL"]["after_day_0_share_pct"] == 33.3 and lag["groups"]["META"]["rows_with_both_dates"] == 2
    joined = " ".join(out["missing"])
    assert "1 row without both sent_date and redeemed_date" in joined and "1 row with redeemed_date before sent_date" in joined
    assert "Of 5 rows with both dates, 60 % had redeemed_date one or more days after sent_date (median 2 days)." in out["summary"]


def test_lag_maturity_excludes_young_groups_and_needs_both_columns(tmp_path):
    csv = (
        "placement,insertion_date,lead_date,leads\n"
        "Delfi,2026-10-01,2026-10-03,1\nDelfi,2026-10-01,2026-10-20,1\nDelfi,2026-10-01,2026-10-30,1\n"
        "Lrytas,2026-10-28,2026-10-29,1\nLrytas,2026-10-28,2026-11-05,1\n"
    )
    tools = _ws(tmp_path, "leads.csv", csv)
    args = {
        "path": "leads.csv", "stages": ["leads", "leads"], "group_by": "placement",
        "lag_from": "insertion_date", "lag_to": "lead_date", "maturity_days": 14, "as_of": "2026-11-08", "min_n": 1,
    }
    out = funnel_analyze(tools, args)
    mat = out["lag"]["maturity"]
    assert mat["maturity_days"] == 14 and mat["maturity_source"] == "given" and mat["as_of"] == "2026-11-08"
    assert mat["groups"]["Delfi"] == {
        "start": "2026-10-01", "age_days": 38, "raw_count": 3, "count_at_maturity": 1, "pct_of_raw_at_maturity": 33.3, "immature": False
    }
    assert mat["groups"]["Lrytas"]["age_days"] == 11 and mat["groups_excluded_as_immature"] == ["Lrytas"]
    assert out["comparison"]["groups"][1]["verdict"] == "insufficient_data" and "maturity" in out["comparison"]["groups"][1]["reason"]
    assert any("younger than 14 days" in w for w in out["warnings"])
    # default maturity is the youngest group's age
    out = funnel_analyze(tools, {k: v for k, v in args.items() if k != "maturity_days"})
    assert out["lag"]["maturity"]["maturity_days"] == 11 and out["lag"]["maturity"]["groups_excluded_as_immature"] == []
    with pytest.raises(ToolError, match="both lag_from and lag_to"):
        funnel_analyze(tools, {"path": "leads.csv", "stages": ["leads", "leads"], "lag_from": "insertion_date"})


# --- time slots -------------------------------------------------------------------


def _orders_by_hour():
    rows = ["order_time,status"]
    plan = {12: (10, 4), 18: (20, 10), 19: (20, 8), 3: (5, 0)}
    for hour, (clicked, delivered) in plan.items():
        rows += [f"2026-10-0{1 + i % 7} {hour:02d}:{i % 60:02d},clicked" for i in range(clicked)]
        rows += [f"2026-10-0{1 + i % 7} {hour:02d}:30,delivered" for i in range(delivered)]
    rows.append("2026-10-05,clicked")  # a date without a time
    return "\n".join(rows) + "\n"


def test_bucket_by_hour_gives_slots_and_the_fewest_slots_covering_90_pct(tmp_path):
    tools = _ws(tmp_path, "orders.csv", _orders_by_hour())
    out = funnel_analyze(
        tools,
        {
            "path": "orders.csv", "stage_column": "status", "stage_order": ["clicked", "delivered"],
            "bucket_by": {"column": "order_time", "unit": "hour"},
        },
    )
    slots = out["slots"]
    assert slots["unit"] == "hour" and slots["rows_without_time"] == 1 and slots["fulfilled_stage"] == "delivered"
    by = {s["slot"]: s for s in slots["slots"]}
    assert [s["slot"] for s in slots["slots"]] == ["03:00", "12:00", "18:00", "19:00"]
    assert by["18:00"]["stage_counts"] == {"clicked": 20, "delivered": 10} and by["18:00"]["fulfilled_pct_of_first"] == 50.0
    assert by["18:00"]["share_of_fulfilled_pct"] == 45.5 and by["03:00"]["low_volume"] is True and by["03:00"]["fulfilled"] == 0
    assert [(b["slot"], b["cumulative_share_pct"]) for b in slots["best_slots"]] == [("18:00", 45.5), ("19:00", 81.8), ("12:00", 100.0)]
    assert slots["slots_for_cover_share"] == 3
    assert "3 of 4 hour slots (18:00, 19:00, 12:00) hold 100 % of delivered." in out["summary"]
    assert any("1 row without a readable time" in m for m in out["missing"])


def test_bucket_by_weekday_accepts_dates_and_bad_units_are_refused(tmp_path):
    csv = "day,visits,orders\n2026-10-05,100,5\n2026-10-12,100,7\n2026-10-06,50,1\n"
    tools = _ws(tmp_path, "w.csv", csv)
    out = funnel_analyze(tools, {"path": "w.csv", "stages": ["visits", "orders"], "bucket_by": {"column": "day", "unit": "weekday"}, "cover_share": 0.5})
    by = {s["slot"]: s for s in out["slots"]["slots"]}
    assert by["Mon"]["stage_counts"] == {"visits": 200, "orders": 12} and by["Tue"]["fulfilled"] == 1
    assert [b["slot"] for b in out["slots"]["best_slots"]] == ["Mon"] and out["slots"]["rows_without_time"] == 0
    with pytest.raises(ToolError, match="unit must be"):
        funnel_analyze(tools, {"path": "w.csv", "stages": ["visits", "orders"], "bucket_by": {"column": "day", "unit": "minute"}})
    with pytest.raises(ToolError, match="cover_share"):
        funnel_analyze(tools, {"path": "w.csv", "stages": ["visits", "orders"], "bucket_by": {"column": "day", "unit": "weekday"}, "cover_share": 2})


# --- derived stages, filters, derived groups -----------------------------------------

TRIALS_CSV = """user_id,trial_start,last_login,paid_at,invites_sent
u1,2026-09-01,2026-09-20,2026-09-21,2
u2,2026-09-01,2026-09-10,,0
u3,2026-09-02,2026-09-30,,0
u4,2026-09-03,2026-10-01,2026-10-02,0
u5,2026-09-03,,,
u6,2026-09-04,2026-09-25,2026-09-26,x
"""


def test_derived_stages_turn_a_per_user_table_into_a_funnel(tmp_path):
    tools = _ws(tmp_path, "trials.csv", TRIALS_CSV)
    out = funnel_analyze(
        tools,
        {
            "path": "trials.csv",
            "derived_stages": [
                {"name": "trial", "column": "trial_start"},
                {"name": "returned", "column": "last_login", "rule": "after:trial_start+14d"},
                {"name": "adopted", "column": "paid_at", "rule": "not_blank"},
                {"name": "recommended", "column": "invites_sent", "rule": "gt:0"},
                {"name": "referred", "column": "referrals", "rule": "gt:0"},
            ],
        },
    )
    assert out["inputs"]["layout"] == "wide" and out["inputs"]["stages"] == ["trial", "returned", "adopted", "recommended"]
    assert [s["count"] for s in out["stages"]] == [6, 4, 3, 1]
    assert out["unavailable_stages"] == [{"stage": "referred", "column": "referrals"}]
    joined = " ".join(out["missing"])
    assert "stage referred: column 'referrals' is not in the file" in joined
    assert "stage recommended: 1 row with unreadable values in invites_sent" in joined


def test_derived_stage_rules_are_validated(tmp_path):
    tools = _ws(tmp_path, "trials.csv", TRIALS_CSV)
    with pytest.raises(ToolError, match="not understood"):
        funnel_analyze(tools, {"path": "trials.csv", "derived_stages": [{"name": "a", "column": "trial_start", "rule": "sometimes"}, {"name": "b", "column": "paid_at"}]})
    with pytest.raises(ToolError, match="needs a number"):
        funnel_analyze(tools, {"path": "trials.csv", "derived_stages": [{"name": "a", "column": "invites_sent", "rule": "gt:many"}, {"name": "b", "column": "paid_at"}]})
    with pytest.raises(ToolError, match="at least two stages"):
        funnel_analyze(tools, {"path": "trials.csv", "derived_stages": [{"name": "a", "column": "trial_start"}, {"name": "b", "column": "nope"}]})
    with pytest.raises(ToolError, match="not both"):
        funnel_analyze(tools, {"path": "trials.csv", "stages": ["trial_start", "paid_at"], "derived_stages": [{"name": "a", "column": "trial_start"}]})


def test_cohort_by_splits_groups_at_a_date_and_within_counts_an_outcome_inside_n_days(tmp_path):
    rows = ["email_id,signup_date,unsubscribe_date"]
    rows += [f"s{i},2026-09-{10 + i:02d},{'2026-09-' + str(20 + i) if i < 3 else ('2026-10-25' if i == 3 else '')}" for i in range(6)]
    rows += [f"o{i},2026-10-0{1 + i},{'2026-10-20' if i == 0 else ''}" for i in range(5)]
    tools = _ws(tmp_path, "subs.csv", "\n".join(rows) + "\n")
    out = funnel_analyze(
        tools,
        {
            "path": "subs.csv",
            "derived_stages": [
                {"name": "signed_up", "column": "signup_date"},
                {"name": "unsubscribed_30d", "column": "unsubscribe_date", "rule": "within:signup_date+30d"},
            ],
            "cohort_by": {"column": "signup_date", "boundaries": ["2026-10-01"]},
            "min_n": 5,
        },
    )
    assert out["inputs"]["group_by"] == "signup_date split at 2026-10-01"
    groups = {g["group"]: g for g in out["groups"]}
    assert groups["before_2026-10-01"]["stages"][1]["count"] == 3 and groups["from_2026-10-01"]["stages"][1]["count"] == 1
    cmp = out["comparison"]
    assert cmp["baseline_group"] == "before_2026-10-01"
    assert (cmp["groups"][0]["rate_pct"], cmp["groups"][1]["rate_pct"], cmp["groups"][1]["diff_pp"]) == (50.0, 20.0, -30.0)
    assert cmp["groups"][1]["verdict"] == "no_difference"  # 6 vs 5 rows cannot separate 50 % from 20 %
    with pytest.raises(ToolError, match="boundaries"):
        funnel_analyze(tools, {"path": "subs.csv", "stages": ["signup_date", "unsubscribe_date"], "cohort_by": {"column": "signup_date", "boundaries": ["soon"]}})


def test_filter_and_periods_give_an_outcome_rate_per_period(tmp_path):
    rows = ["order_id,order_date,status"]
    rows += [f"a{i},2026-10-{1 + i % 19:02d},{'delivered_ok' if i < 97 else 'wrong_item'}" for i in range(100)]
    rows += [f"b{i},2026-10-{20 + i % 12:02d},{'delivered_ok' if i < 94 else 'wrong_qty'}" for i in range(100)]
    rows += [f"p{i},2026-10-3{i},pending" for i in range(2)]
    rows.append("x,2026-11-02,delivered_ok")
    tools = _ws(tmp_path, "orders.csv", "\n".join(rows) + "\n")
    out = funnel_analyze(
        tools,
        {
            "path": "orders.csv",
            "filter": {"column": "status", "exclude_values": ["pending"]},
            "derived_stages": [
                {"name": "delivered", "column": "status"},
                {"name": "correct", "column": "status", "rule": "in:delivered_ok"},
            ],
            "date_column": "order_date",
            "periods": [
                {"label": "before", "from": "2026-10-01", "to": "2026-10-19"},
                {"label": "campaign", "from": "2026-10-20", "to": "2026-10-31"},
            ],
        },
    )
    assert out["inputs"]["filter"]["rows_removed"] == 2 and out["inputs"]["filter"]["rows_kept"] == 201
    assert any("1 row outside every period" in m for m in out["missing"])
    cmp = out["comparison"]
    assert [g["group"] for g in cmp["groups"]] == ["before", "campaign"]
    assert (cmp["groups"][0]["rate_pct"], cmp["groups"][1]["rate_pct"], cmp["groups"][1]["diff_pp"]) == (97.0, 94.0, -3.0)
    assert cmp["groups"][1]["verdict"] == "no_difference" and cmp["groups"][1]["ci95_pp"] == [-8.73, 2.73]
    assert cmp["windows"]["before"] == {"from": "2026-10-01", "to": "2026-10-19"}
    with pytest.raises(ToolError, match="overlap"):
        funnel_analyze(
            tools,
            {
                "path": "orders.csv", "derived_stages": [{"name": "d", "column": "status"}, {"name": "c", "column": "status", "rule": "in:delivered_ok"}],
                "date_column": "order_date",
                "periods": [{"label": "a", "from": "2026-10-01", "to": "2026-10-20"}, {"label": "b", "from": "2026-10-20", "to": "2026-10-31"}],
            },
        )
    with pytest.raises(ToolError, match="needs values"):
        funnel_analyze(tools, {"path": "orders.csv", "stages": ["order_id", "status"], "filter": {"column": "status"}})


# --- two-column group_by: matrix ---------------------------------------------------


def test_two_group_columns_give_a_teller_listener_matrix(tmp_path):
    csv = (
        "referrer_segment,invitee_segment,invited,signed_up,activated\n"
        "accountant,accountant,100,60,38\n"
        "accountant,owner,50,10,3\n"
        "owner,owner,8,4,2\n"
    )
    tools = _ws(tmp_path, "ref.csv", csv)
    out = funnel_analyze(
        tools,
        {
            "path": "ref.csv", "stages": ["invited", "signed_up", "activated"],
            "group_by": ["referrer_segment", "invitee_segment"], "target_segment": "owner",
        },
    )
    assert out["inputs"]["group_by"] == ["referrer_segment", "invitee_segment"]
    assert [g["group"] for g in out["groups"]] == ["accountant → accountant", "accountant → owner", "owner → owner"]
    matrix = out["matrix"]
    assert matrix["row_values"] == ["accountant", "owner"] and matrix["col_values"] == ["accountant", "owner"]
    cells = {(c["referrer_segment"], c["invitee_segment"]): c for c in matrix["cells"]}
    assert cells[("accountant", "owner")]["conversion_pct"] == 6.0 and cells[("owner", "owner")]["small"] is True
    assert matrix["weakest_cell"]["invitee_segment"] == "owner" and matrix["weakest_cell"]["referrer_segment"] == "accountant"
    assert matrix["largest_cell"]["first_stage_count"] == 100
    assert matrix["share_to_target_pct"] == 36.7


def test_three_group_columns_or_a_missing_target_are_handled(tmp_path):
    csv = "a,b,c,x,y\n1,2,3,10,5\n"
    tools = _ws(tmp_path, "m.csv", csv)
    with pytest.raises(ToolError, match="two columns"):
        funnel_analyze(tools, {"path": "m.csv", "stages": ["x", "y"], "group_by": ["a", "b", "c"]})
    out = funnel_analyze(tools, {"path": "m.csv", "stages": ["x", "y"], "group_by": ["a", "b"], "target_segment": "9"})
    assert out["matrix"]["share_to_target_pct"] == 0.0
    assert any("target_segment '9' never appears" in m for m in out["missing"])
    assert any("No matrix cell reaches 30" in m for m in out["missing"])


# --- paid stage vs final stage ----------------------------------------------------------


def test_paid_at_stage_reports_stranded_units_per_rep(tmp_path):
    csv = "rep,units_shipped,units_sold_through,units_returned\nJonas,1200,700,150\nRūta,800,760,10\n"
    tools = _ws(tmp_path, "sales.csv", csv)
    out = funnel_analyze(
        tools,
        {
            "path": "sales.csv", "stages": ["units_shipped", "units_sold_through"], "group_by": "rep",
            "paid_at_stage": "units_shipped", "returns_column": "units_returned",
        },
    )
    inc = out["incentive"]
    assert (inc["paid_units"], inc["final_units"], inc["stranded_units"], inc["stranded_share_pct"]) == (2000, 1460, 540, 27.0)
    assert inc["paid_to_final_ratio"] == 1.37 and inc["incentive_gap"] is True
    assert inc["returns_units"] == 160 and inc["returns_share_of_paid_pct"] == 8.0
    jonas = inc["per_group"][0]
    assert (jonas["group"], jonas["stranded_units"], jonas["stranded_share_pct"], jonas["paid_to_final_ratio"], jonas["returns_units"]) == ("Jonas", 500, 41.7, 1.71, 150)
    assert inc["worst_group"] == "Jonas"
    assert "Paid at units_shipped: 540 of 2,000 units (27 %) never reached units_sold_through; worst group Jonas; the plan pays before the step that loses the most." in out["summary"]


def test_paid_at_stage_must_be_a_stage_and_the_final_stage_has_no_gap(tmp_path):
    csv = "shipped,sold\n100,80\n"
    tools = _ws(tmp_path, "s.csv", csv)
    with pytest.raises(ToolError, match="not a stage; stages: shipped, sold"):
        funnel_analyze(tools, {"path": "s.csv", "stages": ["shipped", "sold"], "paid_at_stage": "invoiced"})
    out = funnel_analyze(tools, {"path": "s.csv", "stages": ["shipped", "sold"], "paid_at_stage": "sold"})
    assert out["incentive"]["stranded_units"] == 0 and out["incentive"]["incentive_gap"] is False


# --- mode: buyer_classes --------------------------------------------------------------------

LOYALTY_CSV = """card_id,date,qty
H1,2026-01-04,2
H1,2026-01-20,2
H1,2026-02-05,2
H1,2026-02-20,2
H1,2026-03-02,2
H1,2026-03-10,2
H1,2026-03-20,2
H1,2026-04-10,1
H2,2026-01-10,1
H2,2026-02-01,1
H2,2026-02-15,1
H2,2026-03-05,3
L1,2026-02-10,1
L1,2026-03-03,1
L1,2026-03-15,1
L1,2026-04-12,1
L2,2026-02-12,2
N1,2026-03-07,4
N1,2026-04-15,1
N2,2026-03-09,1
X,2026-04-20,1
"""


def test_buyer_classes_split_promo_units_into_expected_incremental_and_repurchase(tmp_path):
    tools = _ws(tmp_path, "loyalty.csv", LOYALTY_CSV)
    out = funnel_analyze(
        tools,
        {
            "path": "loyalty.csv", "mode": "buyer_classes", "id_column": "card_id", "date_column": "date", "quantity_column": "qty",
            "promo_start": "2026-03-01", "promo_end": "2026-03-28", "pre_start": "2026-01-04", "pre_end": "2026-02-28",
        },
    )
    assert out["promo_days"] == 28 and out["pre_days"] == 56
    by = {c["class"]: c for c in out["classes"]}
    assert [c["class"] for c in out["classes"]] == ["non", "light", "medium", "heavy"]
    assert by["medium"]["buyers"] == 0 and by["medium"]["definition"] == "2–2 purchases in the pre-period"
    heavy = by["heavy"]
    assert (heavy["buyers"], heavy["promo_buyers"], heavy["pre_units"], heavy["promo_units"]) == (2, 2, 11, 9)
    assert (heavy["expected_promo_units"], heavy["incremental_units"], heavy["substitution_share_pct"]) == (5.5, 3.5, 61.1)
    assert heavy["post_promo_repurchase_pct"] == 50.0 and heavy["share_of_promo_units_pct"] == 56.2  # 9/16, half-to-even
    light = by["light"]
    assert (light["buyers"], light["promo_buyers"], light["expected_promo_units"], light["incremental_units"], light["substitution_share_pct"]) == (2, 1, 1.5, 0.5, 75.0)
    non = by["non"]
    assert (non["buyers"], non["promo_units"], non["expected_promo_units"], non["incremental_units"], non["post_promo_repurchase_pct"]) == (2, 5, 0, 5, 50.0)
    assert out["totals"] == {
        "promo_buyers": 5, "promo_units": 16, "expected_units": 7, "incremental_units": 9, "incremental_share_pct": 56.2,
        "units_from_previous_nonbuyers_pct": 31.2,
    }
    assert out["missing"] == [] and out["warnings"] == []
    assert out["summary"].startswith("16 promo units from 5 buyers between 2026-03-01 and 2026-03-28; 9 (56.2 %) above the pre-period pace")


def test_buyer_classes_without_a_pre_period_report_the_split_only(tmp_path):
    csv = "card_id,date\nA,2026-03-02\nA,2026-03-09\nB,2026-03-10\n"
    tools = _ws(tmp_path, "promo_only.csv", csv)
    out = funnel_analyze(
        tools, {"path": "promo_only.csv", "mode": "buyer_classes", "id_column": "card_id", "date_column": "date", "promo_start": "2026-03-01", "promo_end": "2026-03-31"}
    )
    by = {c["class"]: c for c in out["classes"]}
    assert by["non"]["promo_units"] == 3 and by["non"]["expected_promo_units"] is None and out["totals"]["incremental_units"] is None
    joined = " ".join(out["missing"])
    assert "pre_period: the file starts 2026-03-02" in joined and "no rows after promo_end" in joined
    assert by["non"]["post_promo_repurchase_pct"] is None
    with pytest.raises(ToolError, match="promo_start is required"):
        funnel_analyze(tools, {"path": "promo_only.csv", "mode": "buyer_classes", "id_column": "card_id", "date_column": "date"})
    with pytest.raises(ToolError, match="heavy_min"):
        funnel_analyze(
            tools,
            {"path": "promo_only.csv", "mode": "buyer_classes", "id_column": "card_id", "date_column": "date", "promo_start": "2026-03-01", "promo_end": "2026-03-31", "class_cutoffs": {"light_max": 3, "heavy_min": 2}},
        )


# --- mode: offers -------------------------------------------------------------------------------

ORDERS_CSV = """order_date,customer_id,net_price,discount_pct
2026-01-05,c1,16,20
2026-01-06,c3,20,0
2026-01-07,c4,20,
2026-01-08,c5,20,0
2026-10-05,c1,16,20
2026-10-06,c2,15,25
2026-10-07,c4,16,20
2026-10-08,c3,20,0
"""


def test_offers_mode_reports_discount_share_realized_price_and_discount_only_customers(tmp_path):
    tools = _ws(tmp_path, "orders.csv", ORDERS_CSV)
    out = funnel_analyze(
        tools,
        {"path": "orders.csv", "mode": "offers", "date_column": "order_date", "revenue_column": "net_price", "discount_column": "discount_pct", "customer_column": "customer_id"},
    )
    jan, octo = out["periods"]
    assert (jan["period"], jan["orders"], jan["discounted_orders"], jan["discount_share_pct"], jan["avg_discount_pct"]) == ("2026-01", 4, 1, 25.0, 20.0)
    assert (jan["revenue"], jan["realized_avg_price"], jan["full_price_avg_price"]) == (76, 19.0, 20.0)
    assert (octo["discount_share_pct"], octo["avg_discount_pct"], octo["realized_avg_price"]) == (75.0, 21.7, 16.75)
    assert out["customers"] == {
        "with_orders": 5, "discount_only": 2, "discount_only_share_pct": 40.0,
        "acquired_on_discount": 2, "acquired_on_discount_full_price_later": 0, "full_price_repeat_rate_pct": 0.0,
    }
    assert out["change"] == {"from": "2026-01", "to": "2026-10", "discount_share_pp": 50.0, "realized_avg_price_change_pct": -11.8}
    assert any("discount share rose from 25 % to 75 %" in w for w in out["warnings"])
    assert any("discount_pct: 1 blank cell counted as not discounted" in m for m in out["missing"])
    assert out["inputs"]["discount_read_as"] == "percent"
    assert out["summary"].startswith("Discounted share of orders 25 % (2026-01) → 75 % (2026-10); realized average price 19.00 → 16.75.")
    assert "40 % of 5 customers only ever buy on discount; of 2 acquired on discount, 0 % later paid full price." in out["summary"]


def test_offers_mode_reads_a_yes_no_flag_and_says_what_it_cannot_measure(tmp_path):
    csv = "day,price,promo\n2026-05-01,10,yes\n2026-05-02,12,no\n2026-05-03,abc,yes\n"
    tools = _ws(tmp_path, "o.csv", csv)
    out = funnel_analyze(tools, {"path": "o.csv", "mode": "offers", "date_column": "day", "revenue_column": "price", "discount_column": "promo", "period": "week"})
    assert out["inputs"]["discount_read_as"] == "flag"
    (week,) = out["periods"]
    assert week["period"] == "2026-W18" and week["orders"] == 2 and week["discount_share_pct"] == 50.0 and week["avg_discount_pct"] is None
    joined = " ".join(out["missing"])
    assert "1 row dropped because price was not a number" in joined and "no customer_column" in joined and "fewer than two periods" in joined
    assert "customers" not in out and "change" not in out
    with pytest.raises(ToolError, match="period must be"):
        funnel_analyze(tools, {"path": "o.csv", "mode": "offers", "date_column": "day", "revenue_column": "price", "discount_column": "promo", "period": "day"})
    with pytest.raises(ToolError, match="discount_column is required"):
        funnel_analyze(tools, {"path": "o.csv", "mode": "offers", "date_column": "day", "revenue_column": "price"})


# --- mode: trend ----------------------------------------------------------------------------------

LAUNCH_CSV = """period,spend_activation,spend_brand,new_buyers,repeat_buyers
2026-04,40000,0,1200,300
2026-05,40000,0,1150,520
2026-06,40000,0,1020,700
2026-07,40000,0,860,780
2026-08,40000,0,640,790
2026-09,40000,0,520,800
2026-10,40000,0,480,790
"""


def test_trend_mode_prices_a_new_buyer_per_period_and_names_when_trial_fades(tmp_path):
    tools = _ws(tmp_path, "launch.csv", LAUNCH_CSV)
    out = funnel_analyze(
        tools,
        {
            "path": "launch.csv", "mode": "trend", "period_column": "period", "spend_column": "spend_activation",
            "new_column": "new_buyers", "repeat_column": "repeat_buyers", "brand_spend_column": "spend_brand",
        },
    )
    assert [p["period"] for p in out["periods"]] == ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]
    assert out["periods"][0]["cost_per_new"] == 33.33 and out["periods"][0]["repeat_share_pct"] == 20.0 and out["periods"][0]["brand_share_of_spend_pct"] == 0.0
    assert out["baseline"] == {"periods": ["2026-04", "2026-05", "2026-06"], "mean_new": 1123.3, "cost_per_new": 35.61}
    assert out["latest"] == {"period": "2026-10", "new": 480, "cost_per_new": 83.33, "repeat_share_pct": 62.2, "new_change_pct": -57.3, "cost_change_pct": 134.0}
    assert out["trial_fading"] is True and out["trial_fading_since"] == "2026-08"
    assert out["repeat_exceeds_new_from"] == "2026-08" and out["repeat_share_trend"] == "rising"
    assert out["drift"] == {"prior_pooled_cost_per_new": 44.53, "latest_cost_per_new": 83.33, "drift_pct": 87.1} and out["baseline_is_stale"] is True
    assert out["findings"] == ["baseline_is_stale", "trial_fading"]
    assert out["summary"] == (
        "2026-10: 480 new buyers against a first-3-period mean of 1,123.3 (-57.3 %); cost per new buyer 83.33 vs baseline 35.61 (+134 %). "
        "Repeat buyers outnumber new buyers from 2026-08. Trial is fading since 2026-08."
    )


def test_trend_mode_aggregates_daily_rows_by_month_and_refuses_to_judge_two_periods(tmp_path):
    csv = "date,spend,inquiries\n2026-09-01,100,5\n2026-09-15,100,5\n2026-10-01,240,10\n2026-10-20,0,0\n"
    tools = _ws(tmp_path, "daily.csv", csv)
    out = funnel_analyze(tools, {"path": "daily.csv", "mode": "trend", "date_column": "date", "spend_column": "spend", "new_column": "inquiries"})
    assert [(p["period"], p["spend"], p["new"], p["cost_per_new"]) for p in out["periods"]] == [("2026-09", 200, 10, 20.0), ("2026-10", 240, 10, 24.0)]
    assert "too_few_periods" in out["findings"] and out["trial_fading"] is False and out["trial_fading_since"] is None
    assert out["drift"]["drift_pct"] == 20.0 and out["baseline_is_stale"] is False
    assert any("only 2 periods" in w for w in out["warnings"])
    out = funnel_analyze(tools, {"path": "daily.csv", "mode": "trend", "date_column": "date", "new_column": "inquiries"})
    assert "cost_per_new" not in out["periods"][0] and any("no spend_column" in m for m in out["missing"])
    with pytest.raises(ToolError, match="period_column"):
        funnel_analyze(tools, {"path": "daily.csv", "mode": "trend", "new_column": "inquiries"})


# --- mode: recruits ---------------------------------------------------------------------------------

RECRUITS_CSV = """customer_id,sku,date,price
u1,MILK,2025-10-01,1.20
u1,OAT,2026-03-05,1.99
u1,MILK,2026-04-01,1.20
u2,OAT,2026-03-10,1.99
u2,OAT,2026-04-10,1.99
u3,OAT,2026-03-12,1.99
u3,CHEESE,2026-05-01,3.50
u4,MILK,2025-09-01,1.20
u5,MILK,2026-01-01,1.20
u5,OAT,2026-03-20,1.99
"""


def test_recruits_mode_splits_new_product_buyers_into_new_to_brand_and_existing(tmp_path):
    tools = _ws(tmp_path, "orders.csv", RECRUITS_CSV)
    out = funnel_analyze(
        tools,
        {
            "path": "orders.csv", "mode": "recruits", "id_column": "customer_id", "product_column": "sku", "date_column": "date",
            "new_products": ["OAT"], "launch_date": "2026-03-01", "price_column": "price", "cycle_days": 30, "as_of": "2026-06-30",
        },
    )
    assert out["pre_launch_history_days"] == 181 and out["buyers_of_new_product"] == 4 and out["unsuitable"] is None
    assert (out["new_to_brand"], out["existing"], out["new_to_brand_share_pct"], out["existing_lapsed_other_products"]) == (2, 2, 50.0, 1)
    new = out["per_group"]["new_to_brand"]
    assert (new["buyers"], new["new_product_orders"], new["repeat_on_new_product_pct"], new["bought_other_products_later_pct"]) == (2, 3, 50.0, 50.0)
    assert new["top_other_products"] == ["CHEESE"] and new["median_days_to_other_product"] == 50 and new["avg_order_value"] == 2.37
    existing = out["per_group"]["existing"]
    assert (existing["buyers"], existing["bought_other_products_later_pct"], existing["orders_per_buyer"]) == (2, 50.0, 2.5)
    assert existing["buyers_past_one_cycle"] == 2 and existing["bought_other_products_later_pct_matured"] == 50.0
    assert out["maturity"] == {"as_of": "2026-06-30", "last_first_purchase": "2026-03-20", "matured_on": "2026-04-19", "all_matured": True}
    assert out["summary"].startswith("2 of 4 buyers of OAT (50 %) are new to the brand (181 days of history before 2026-03-01)")


def test_recruits_mode_refuses_a_short_history_and_names_absent_products(tmp_path):
    tools = _ws(tmp_path, "orders.csv", RECRUITS_CSV)
    out = funnel_analyze(
        tools,
        {"path": "orders.csv", "mode": "recruits", "id_column": "customer_id", "product_column": "sku", "date_column": "date", "new_products": ["OAT", "SOY"], "launch_date": "2026-03-01", "min_history_days": 365},
    )
    assert out["unsuitable"].startswith("only 181 days of history before 2026-03-01")
    assert out["new_to_brand"] is None and out["new_to_brand_share_pct"] is None and out["per_group"] is None
    assert any("new_products not found in sku: SOY" in m for m in out["missing"])
    assert any("no cycle_days" in m for m in out["missing"])
    assert out["summary"].startswith("Cannot split 4 buyers of OAT, SOY")
    with pytest.raises(ToolError, match="new_products is required"):
        funnel_analyze(tools, {"path": "orders.csv", "mode": "recruits", "id_column": "customer_id", "product_column": "sku", "date_column": "date"})
    with pytest.raises(ToolError, match="id_column column 'email'"):
        funnel_analyze(tools, {"path": "orders.csv", "mode": "recruits", "id_column": "email", "product_column": "sku", "date_column": "date", "new_products": ["OAT"]})


# --- mode: categories -----------------------------------------------------------------------------------


def test_categories_mode_counts_shares_pareto_and_repeat_ratio(tmp_path):
    rows = ["ticket_id,customer_id,category"]
    rows += [f"t{i},c{i % 5},{'Importas' if i < 5 else 'PVM' if i < 8 else 'Sablonas'}" for i in range(10)]
    rows.append("t10,c1,")
    tools = _ws(tmp_path, "tickets.csv", "\n".join(rows) + "\n")
    out = funnel_analyze(tools, {"path": "tickets.csv", "mode": "categories", "column": "category", "id_column": "customer_id"})
    assert out["rows_total"] == 11 and out["rows_matched"] == 10
    assert out["groups"] == [
        {"value": "Importas", "count": 5, "share_pct": 50.0},
        {"value": "PVM", "count": 3, "share_pct": 30.0},
        {"value": "Sablonas", "count": 2, "share_pct": 20.0},
    ]
    assert out["pareto_groups"] == ["Importas", "PVM"] and out["distinct_ids"] == 5 and out["repeat_ratio"] == 2.0
    assert any("1 row with a blank category" in m for m in out["missing"])
    assert any("sample too small: 11 rows" in w for w in out["warnings"])
    assert out["summary"] == (
        "Of 10 rows, the largest category is Importas with 5 (50 %); 2 categories cover 80 %: Importas, PVM. 10 rows over 5 distinct customer_id: 2 per id."
    )


def test_categories_mode_maps_to_given_categories_and_names_the_rest(tmp_path):
    rows = ["store,outcome"] + ["s,stockout"] * 15 + ["s,clerk_substituted"] * 11 + ["s,buyer_indifferent"] * 6 + ["s,bought"] * 6 + ["s,closed"] * 2
    tools = _ws(tmp_path, "stores.csv", "\n".join(rows) + "\n")
    out = funnel_analyze(
        tools,
        {"path": "stores.csv", "mode": "categories", "column": "outcome", "categories": ["stockout", "clerk_substituted", "buyer_indifferent", "bought"], "top_n": 2},
    )
    assert out["rows_total"] == 40 and out["rows_matched"] == 38 and out["unmapped_values"] == ["closed"]
    assert out["groups"] == [{"value": "stockout", "count": 15, "share_pct": 39.5}, {"value": "clerk_substituted", "count": 11, "share_pct": 28.9}]
    # bought and buyer_indifferent tie at 6; ties sort alphabetically, so bought completes the 80 % set
    assert out["other_count"] == 12 and out["pareto_groups"] == ["stockout", "clerk_substituted", "bought"]
    assert out["warnings"] == [] and any("outside categories: closed" in m for m in out["missing"])
    with pytest.raises(ToolError, match="column column 'reason'"):
        funnel_analyze(tools, {"path": "stores.csv", "mode": "categories", "column": "reason"})
    with pytest.raises(ToolError, match="mode must be one of"):
        funnel_analyze(tools, {"path": "stores.csv", "mode": "pivot", "column": "outcome"})



def test_the_tool_spec_advertises_the_funnel_modes():
    from rocky.tools import worker_tools

    spec = next(t for t in worker_tools(False) if t["function"]["name"] == "funnel_analyze")
    props = spec["function"]["parameters"]["properties"]
    assert props["mode"]["enum"] == ["funnel", "buyer_classes", "offers", "trend", "recruits", "categories"]
    for key in ("compare_groups", "cost_per_group", "lag_from", "bucket_by", "derived_stages", "paid_at_stage"):
        assert key in props, key
    assert "anyOf" in props["group_by"]
