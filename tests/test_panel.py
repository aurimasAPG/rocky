import json

import pytest

from rocky.panel import repeat_buying, repeat_buying_tool
from rocky.tools import ToolError, WorkspaceTools

# 43 rows, 40 purchases (order ids 1001-1040), 14 buyers c01-c14, three brands, Jan-Jun 2026.
# Order 1001 has two lines (A 20 + B 5) -> one purchase worth 25. Row 1041 has a blank customer,
# row 1042 an unreadable date: both are left out and reported under missing.
#
# Purchases per buyer: c01-c06 once (amount 10 each), c07/c08/c09 twice, c10/c11 three times,
# c12 four times (all 10 each), c13 six times at 20, c14 twelve times at 20 (+5 on order 1001).
# Revenue = 6*10 + 6*10 + 6*10 + 4*10 + 120 + 245 = 585.
ORDERS_CSV = """order_id,customer,date,amount,brand,sku
1001,c14,2026-01-03,20,A,sku-a
1001,c14,2026-01-03,5,B,sku-b
1002,c01,2026-01-05,10,A,sku-a
1003,c13,2026-01-08,20,B,sku-b
1004,c07,2026-01-10,10,A,sku-a
1005,c08,2026-01-15,10,B,sku-b
1006,c14,2026-01-18,20,C,sku-c
1007,c10,2026-01-20,10,A,sku-a
1008,c02,2026-02-03,10,B,sku-b
1009,c14,2026-02-03,20,A,sku-a
1010,c12,2026-02-06,10,A,sku-a
1011,c13,2026-02-08,20,B,sku-b
1012,c07,2026-02-12,10,A,sku-a
1013,c14,2026-02-18,20,C,sku-c
1014,c14,2026-03-03,20,A,sku-a
1015,c12,2026-03-06,10,A,sku-a
1016,c03,2026-03-07,10,A,sku-a
1017,c13,2026-03-08,20,B,sku-b
1018,c14,2026-03-18,20,C,sku-c
1019,c09,2026-03-20,10,A,sku-a
1020,c10,2026-03-20,10,A,sku-a
1021,c09,2026-03-25,10,B,sku-b
1022,c14,2026-04-03,20,A,sku-a
1023,c11,2026-04-04,10,C,sku-c
1024,c12,2026-04-06,10,B,sku-b
1025,c13,2026-04-08,20,B,sku-b
1026,c04,2026-04-09,10,C,sku-c
1027,c08,2026-04-15,10,B,sku-b
1028,c14,2026-04-18,20,C,sku-c
1029,c14,2026-05-03,20,A,sku-a
1030,c11,2026-05-04,10,C,sku-c
1031,c12,2026-05-06,10,A,sku-a
1032,c13,2026-05-08,20,B,sku-b
1033,c05,2026-05-11,10,B,sku-b
1034,c14,2026-05-18,20,C,sku-c
1035,c10,2026-05-20,10,A,sku-a
1036,c06,2026-06-02,10,A,sku-a
1037,c14,2026-06-03,20,A,sku-a
1038,c11,2026-06-04,10,A,sku-a
1039,c13,2026-06-08,20,B,sku-b
1040,c14,2026-06-18,20,C,sku-c
1041,,2026-06-20,10,A,sku-a
1042,c01,not a date,10,A,sku-a
"""

COLUMNS = {"customer": "customer", "date": "date", "amount": "amount", "order_id": "order_id", "brand": "brand", "sku": "sku"}

# 20 Mondays of unit sales hovering around 100 with one injected spike (320) in the 15th week.
WEEKLY_UNITS = [100, 104, 97, 101, 99, 103, 98, 102, 100, 105, 96, 101, 99, 103, 320, 100, 102, 98, 101, 104]


def _ws(tmp_path, name="orders.csv", text=ORDERS_CSV):
    (tmp_path / name).write_text(text, encoding="utf-8")
    return WorkspaceTools(tmp_path)


def _run(tmp_path, analysis, **extra):
    tools = _ws(tmp_path)
    args = {"path": "orders.csv", "analysis": analysis, "columns": COLUMNS}
    args.update(extra)
    return repeat_buying(tools, args)


def _weekly_csv(tmp_path):
    from datetime import date, timedelta

    start = date(2026, 1, 5)
    lines = ["date,units"] + [f"{(start + timedelta(days=7 * i)).isoformat()},{u}" for i, u in enumerate(WEEKLY_UNITS)]
    return _ws(tmp_path, "weekly.csv", "\n".join(lines) + "\n")


# --- summary ------------------------------------------------------------------


def test_summary_counts_buyers_purchases_and_revenue_and_reports_missing_rows(tmp_path):
    out = _run(tmp_path, "summary")
    assert out["window"]["from"] == "2026-01-03" and out["window"]["to"] == "2026-06-18"
    assert out["window"]["rows_total"] == 43 and out["rows_used"] == 41
    assert out["buyers"] == 14 and out["purchases"] == 40  # order 1001 has two lines but is one purchase
    assert out["purchases_per_buyer"] == 2.86  # 40 / 14
    assert out["revenue"] == 585 and out["revenue_per_buyer"] == 41.79  # 585 / 14
    assert out["buyers_with_one_purchase"] == 6 and out["buyers_with_one_purchase_pct"] == 42.9  # 6 / 14
    assert len(out["per_period"]) == 6 and [p["buyers"] for p in out["per_period"]] == [6, 5, 6, 6, 6, 4]
    assert any("blank customer" in m for m in out["missing"]) and any("readable date" in m for m in out["missing"])
    assert "14 buyers made 40 purchases (2.86 per buyer)" in out["summary"]
    assert "42.9 % of buyers bought once" in out["summary"]


def test_small_base_is_flagged_when_buyers_are_below_min_buyers(tmp_path):
    out = _run(tmp_path, "summary")
    assert "small_base" in out["flags"] and any("14 buyers" in w for w in out["warnings"])
    out = _run(tmp_path, "summary", min_buyers=10)
    assert "small_base" not in out["flags"]


def test_date_window_narrows_the_rows_and_says_how_many_fell_outside(tmp_path):
    out = _run(tmp_path, "summary", date_from="2026-04-01", date_to="2026-06-30")
    assert out["window"]["from"] == "2026-04-03" and out["window"]["to"] == "2026-06-18"
    assert out["buyers"] == 9 and out["purchases"] == 19  # Apr 7 + May 7 + Jun 5 purchases
    assert any("outside 2026-04-01" in m for m in out["missing"])


def test_without_order_id_a_purchase_is_customer_plus_date(tmp_path):
    cols = {k: v for k, v in COLUMNS.items() if k != "order_id"}
    out = _run(tmp_path, "summary", columns=cols)
    # c09 and c10 both bought on 2026-03-20 but are different customers -> still 40 purchases
    assert out["purchases"] == 40 and out["buyers"] == 14


# --- frequency ----------------------------------------------------------------


def test_frequency_table_gives_buyer_purchase_and_revenue_shares_per_bin(tmp_path):
    out = _run(tmp_path, "frequency")
    table = {row["purchases"]: row for row in out["table"]}
    assert list(table) == ["1", "2", "3", "4", "5+"]
    assert (table["1"]["buyers"], table["1"]["buyers_pct"], table["1"]["purchases_pct"], table["1"]["revenue_pct"]) == (6, 42.9, 15.0, 10.3)
    assert (table["2"]["buyers"], table["2"]["purchases_count"]) == (3, 6)
    assert (table["4"]["buyers"], table["4"]["revenue"]) == (1, 40)
    assert (table["5+"]["buyers"], table["5+"]["purchases_count"], table["5+"]["revenue"], table["5+"]["revenue_pct"]) == (2, 18, 365, 62.4)
    assert out["light_buyers"] == "42.9 % of buyers bought 1 time and brought 10.3 % of sales."
    assert "14.3 % of buyers bought 5+ times and brought 62.4 % of sales" in out["summary"]


def test_frequency_accepts_custom_bins(tmp_path):
    out = _run(tmp_path, "frequency", bins=[1, 2, "3+"])
    assert [row["purchases"] for row in out["table"]] == ["1", "2", "3+"]
    assert out["table"][2]["buyers"] == 5  # c10, c11, c12, c13, c14


# --- repeat ---------------------------------------------------------------------


def test_repeat_two_definitions_and_intervals(tmp_path):
    out = _run(tmp_path, "repeat", pack_life_days=45)
    a, b = out["definition_a"], out["definition_b"]
    assert (a["buyers"], a["repeaters"], a["rate_pct"]) == (14, 8, 57.1)
    # halves of six months: Jan-Mar buyers = 10, of whom c08, c10, c12, c13, c14 bought again Apr-Jun
    assert b["period1"]["periods"] == ["2026-01", "2026-02", "2026-03"]
    assert (b["period1_buyers"], b["also_bought_period2"], b["rate_pct"]) == (10, 5, 50.0)
    assert out["definitions_differ"] is True and "7.1 points apart" in out["explanation"]
    iv = out["intervals"]
    # 26 gaps between consecutive purchase dates: sorted 5,13,15x7,16x3,28,28,30,30,30,31x5,33,59,61,90
    assert (iv["n_repeaters"], iv["n_intervals"], iv["median_days"], iv["p25_days"], iv["p75_days"]) == (8, 26, 28.0, 15.0, 31.0)
    assert iv["recommended_window_days"] == 45  # max(median 28, pack life 45)
    r = out["repeat_rate_at"]
    # 30 days: 13 buyers started at least 30 days before 2026-06-18; c09, c11, c12, c14 bought again within 30 days
    assert (r["30"]["base_buyers"], r["30"]["repeated"], r["30"]["rate_pct"]) == (13, 4, 30.8)
    # 90 days: first purchase on or before 2026-03-20 -> 10 buyers; 7 bought again within 90 days
    assert (r["90"]["base_buyers"], r["90"]["repeated"], r["90"]["rate_pct"]) == (10, 7, 70.0)
    # 45 days: first purchase on or before 2026-05-04 -> 12 buyers; c07, c09, c11, c12, c13, c14 repeated
    assert (r["recommended"]["days"], r["recommended"]["base_buyers"], r["recommended"]["repeated"], r["recommended"]["rate_pct"]) == (45, 12, 6, 50.0)
    assert "57.1 % of 14 buyers bought 2+ times" in out["summary"] and "median 28 days" in out["summary"]


def test_repeat_explicit_windows_drive_both_definitions(tmp_path):
    out = _run(
        tmp_path, "repeat",
        window_a={"from": "2026-01-01", "to": "2026-02-28"},
        window_b={"from": "2026-03-01", "to": "2026-04-30"},
    )
    a, b = out["definition_a"], out["definition_b"]
    # Jan-Feb: 8 buyers, repeaters c07, c13, c14 -> 37.5 %
    assert (a["buyers"], a["repeaters"], a["rate_pct"]) == (8, 3, 37.5)
    # of those 8, c08 (Apr 15), c10 (Mar 20), c12, c13, c14 bought in Mar-Apr -> 62.5 %
    assert (b["period1_buyers"], b["also_bought_period2"], b["rate_pct"]) == (8, 5, 62.5)
    assert out["definitions_differ"] is True  # 25 points apart: the two definitions tell different stories
    assert out["intervals"]["recommended_window_days"] == 28  # no pack life -> the median


# --- skip_return ------------------------------------------------------------------


def test_skip_return_separates_skipping_from_leaving(tmp_path):
    out = _run(tmp_path, "skip_return")
    rows = {row["period"]: row for row in out["periods"]}
    assert list(rows) == ["2026-01", "2026-02", "2026-03", "2026-04"]
    jan = rows["2026-01"]  # 6 buyers; 3 back in Feb, 3 in Mar, c10 skipped Feb and came back, c01 and c08 in neither
    assert (jan["buyers"], jan["returned_next_pct"], jan["returned_plus_2_pct"], jan["skip_then_return_pct"], jan["lost_two_periods_pct"]) == (6, 50.0, 50.0, 16.7, 33.3)
    feb = rows["2026-02"]  # 5 buyers; c12, c13, c14 back both times; c02, c07 gone
    assert (feb["returned_next_pct"], feb["skip_then_return"], feb["lost_two_periods_pct"]) == (60.0, 0, 40.0)
    assert out["mean_skip_then_return_pct"] == 8.3  # (16.7 + 0 + 16.7 + 0) / 4
    assert out["intermittent"] is False
    out2 = _run(tmp_path, "skip_return", intermittent_threshold_pct=5)
    assert out2["intermittent"] is True
    assert "8.3 % of a month's buyers skip the next month" in out["summary"]


# --- cohorts -----------------------------------------------------------------------


def test_cohorts_retention_and_revenue(tmp_path):
    out = _run(tmp_path, "cohorts")
    cohorts = {c["cohort"]: c for c in out["cohorts"]}
    jan = cohorts["2026-01"]
    assert jan["buyers"] == 6 and jan["revenue"] == 445  # 10 + 20 + 20 + 30 + 120 + 245
    assert [(r["period"], r["buyers"], r["pct"]) for r in jan["retained"]] == [
        ("2026-02", 3, 50.0), ("2026-03", 3, 50.0), ("2026-04", 3, 50.0), ("2026-05", 3, 50.0), ("2026-06", 2, 33.3),
    ]
    assert cohorts["2026-02"]["buyers"] == 2 and [r["buyers"] for r in cohorts["2026-02"]["retained"]] == [1, 1, 1, 0]
    assert cohorts["2026-04"]["buyers"] == 2 and cohorts["2026-06"]["retained"] == []
    assert sum(c["revenue"] for c in out["cohorts"]) == 585
    assert out["baseline"] is None
    assert "2026-01 cohort of 6 buyers had 2 (33.3 %) still buying in 2026-06" in out["summary"]


def test_cohorts_baseline_splits_test_buyers_into_new_and_returning(tmp_path):
    out = _run(
        tmp_path, "cohorts",
        baseline_from="2026-01-01", baseline_to="2026-03-31", test_from="2026-04-01", test_to="2026-06-30", min_baseline_periods=3,
    )
    b = out["baseline"]
    assert b["baseline"]["mean_buyers_per_period"] == 5.67 and b["test"]["mean_buyers_per_period"] == 5.33  # (6+5+6)/3, (6+6+4)/3
    assert b["change_pct"] == -5.9
    # Apr-Jun buyers: c04 c05 c06 c08 c10 c11 c12 c13 c14; first seen in the test window: c04 c05 c06 c11
    assert (b["test_buyers"], b["test_new_buyers"], b["test_new_pct"], b["test_returning_buyers"]) == (9, 4, 44.4, 5)
    assert "short_baseline" not in out["flags"]
    out2 = _run(tmp_path, "cohorts", baseline_from="2026-01-01", baseline_to="2026-03-31", test_from="2026-04-01", test_to="2026-06-30")
    assert "short_baseline" in out2["flags"]  # default needs 4 baseline periods


# --- entrants_leavers ------------------------------------------------------------------


def test_entrants_leavers_per_period_and_double_count(tmp_path):
    out = _run(tmp_path, "entrants_leavers")
    rows = {row["period"]: row for row in out["periods"]}
    assert (rows["2026-01"]["buyers"], rows["2026-01"]["new"], rows["2026-01"]["lapsed_from_previous"]) == (6, 6, None)
    mar = rows["2026-03"]  # new c03 c09; repeat c12 c13 c14; c10 back after skipping Feb; c02 c07 lapsed
    assert (mar["buyers"], mar["new"], mar["repeat_from_previous"], mar["returned_after_gap"], mar["lapsed_from_previous"]) == (6, 2, 3, 1, 2)
    jun = rows["2026-06"]
    assert (jun["buyers"], jun["new"], jun["returned_after_gap"], jun["lapsed_from_previous"]) == (4, 1, 0, 3)
    assert [c["distinct_buyers_to_date"] for c in out["cumulative"]] == [6, 8, 10, 12, 13, 14]
    assert (out["sum_of_period_buyers"], out["distinct_buyers"], out["double_count"]) == (33, 14, 19)
    assert "universe_missing" in out["flags"] and out["penetration_pct"] is None
    assert "19 would be double-counted" in out["summary"]


def test_entrants_leavers_penetration_uses_the_universe(tmp_path):
    out = _run(tmp_path, "entrants_leavers", universe=100, universe_source="CRM household count")
    assert out["penetration_pct"] == 14.0
    assert [c["penetration_to_date_pct"] for c in out["cumulative"]] == [6.0, 8.0, 10.0, 12.0, 13.0, 14.0]
    assert "universe_missing" not in out["flags"] and "universe_source_missing" not in out["flags"]
    assert "Penetration to date 14 % of 100" in out["summary"]
    out2 = _run(tmp_path, "entrants_leavers", universe=100)
    assert "universe_source_missing" in out2["flags"]


# --- duplication --------------------------------------------------------------------------


def test_duplication_matrix_ratio_and_repertoire(tmp_path):
    out = _run(tmp_path, "duplication", focus_brand="A")
    brands = {b["name"]: b for b in out["brands"]}
    # A buyers: c01 c03 c06 c07 c09 c10 c11 c12 c14 = 9; B: c02 c05 c08 c09 c12 c13 c14 = 7; C: c04 c11 c14 = 3
    assert (brands["A"]["buyers"], brands["A"]["penetration_pct"]) == (9, 64.3)
    assert (brands["B"]["buyers"], brands["B"]["penetration_pct"]) == (7, 50.0)
    assert (brands["C"]["buyers"], brands["C"]["penetration_pct"]) == (3, 21.4)
    pairs = {(m["brand_a"], m["brand_b"]): m for m in out["matrix"]}
    ab = pairs[("A", "B")]  # shared c09 c12 c14 = 3 of 9 A buyers = 33.3 %; B penetration 50 -> ratio 0.67
    assert (ab["shared_buyers"], ab["pct_of_a_buyers"], ab["penetration_b_pct"], ab["ratio"], ab["label"]) == (3, 33.3, 50.0, 0.67, "below expected")
    ac = pairs[("A", "C")]  # shared c11 c14 = 2 of 9 = 22.2 %; C penetration 21.4 -> 1.04
    assert (ac["shared_buyers"], ac["ratio"], ac["label"]) == (2, 1.04, "as expected")
    assert pairs[("C", "B")]["shared_buyers"] == 1
    assert out["average_duplication_coefficient"] == 0.79  # (4 x 0.667 + 2 x 1.037) / 6
    rep = out["repertoire"]
    assert rep["mean_distinct_brands"] == 1.36 and rep["single_brand_pct"] == 71.4  # 19 / 14 brands, 10 of 14 single-brand
    # A buyers' brand purchases: A 19 of 30 -> 63.3 %; c01 c03 c06 c07 c10 buy only A -> 5 of 9
    assert out["focus"]["share_of_requirements_pct"] == 63.3 and out["focus"]["exclusive_pct"] == 55.6
    assert "small_base" in out["flags"] and "universe_missing" in out["flags"]
    assert "A leads with 64.3 % penetration" in out["summary"] and "63.3 % of their brand purchases" in out["summary"]


def test_duplication_penetration_from_universe_changes_the_ratio(tmp_path):
    out = _run(tmp_path, "duplication", universe=100, universe_source="panel households")
    brands = {b["name"]: b for b in out["brands"]}
    assert (brands["A"]["penetration_pct"], brands["B"]["penetration_pct"], brands["C"]["penetration_pct"]) == (9.0, 7.0, 3.0)
    ab = next(m for m in out["matrix"] if (m["brand_a"], m["brand_b"]) == ("A", "B"))
    assert ab["ratio"] == 4.76  # 33.3 % of A buyers also buy B against 7 % penetration
    assert out["penetration_base"].startswith("universe of 100")


def test_duplication_needs_a_brand_column_and_a_known_focus_brand(tmp_path):
    tools = _ws(tmp_path, "nobrand.csv", "customer,date,amount\nx1,2026-01-01,10\nx2,2026-01-02,10\n")
    with pytest.raises(ToolError, match="columns.brand"):
        repeat_buying(tools, {"path": "nobrand.csv", "analysis": "duplication"})
    with pytest.raises(ToolError, match="brands found: A, B, C"):
        _run(tmp_path, "duplication", focus_brand="Z")


# --- double jeopardy ------------------------------------------------------------------------


DJ_BRANDS = [
    {"name": "Alpha", "penetration_pct": 40, "loyalty": 57},
    {"name": "Beta", "penetration_pct": 30, "loyalty": 54},
    {"name": "Gamma", "penetration_pct": 20, "loyalty": 50},
    {"name": "Ours", "penetration_pct": 10, "loyalty": 30},  # the line predicts about 39 here
    {"name": "Epsilon", "penetration_pct": 5, "loyalty": 36},
]


def test_double_jeopardy_fit_puts_the_focus_brand_below_the_line(tmp_path):
    out = repeat_buying(WorkspaceTools(tmp_path), {"analysis": "double_jeopardy", "brands": DJ_BRANDS, "focus_brand": "Ours", "loyalty_kind": "repeat"})
    assert out["fit"]["n_brands"] == 5 and out["fit"]["slope"] > 0 and out["fit"]["r"] > 0.7
    # hand fit on ln(penetration): slope ~12.3, intercept ~10.9 -> expected at ln(10) ~39.3
    assert abs(out["fit"]["slope"] - 12.34) < 0.05 and abs(out["fit"]["intercept"] - 10.85) < 0.1
    focus = out["focus"]
    assert focus["name"] == "Ours" and abs(focus["expected_from_size"] - 39.3) < 0.1
    assert focus["residual"] < -9 and focus["verdict"] == "below" and out["verdict"] == "below"
    rows = {r["name"]: r for r in out["rows"]}
    assert rows["Gamma"]["verdict"] == "in line with size"
    assert out["flags"] == []
    assert "Ours has 10 % penetration" in out["summary"] and "below the line" in out["summary"]


def test_double_jeopardy_warns_below_four_brands_and_needs_the_focus_brand(tmp_path):
    tools = WorkspaceTools(tmp_path)
    out = repeat_buying(tools, {"analysis": "double_jeopardy", "brands": DJ_BRANDS[:3], "focus_brand": "Beta"})
    assert "few_brands" in out["flags"] and out["verdict"] == "in line with size"
    with pytest.raises(ToolError, match="not among the brands"):
        repeat_buying(tools, {"analysis": "double_jeopardy", "brands": DJ_BRANDS, "focus_brand": "Nobody"})
    with pytest.raises(ToolError, match="brands is required"):
        repeat_buying(tools, {"analysis": "double_jeopardy"})


def test_double_jeopardy_frequency_uses_a_relative_tolerance(tmp_path):
    brands = [{"name": n, "penetration_pct": p, "frequency": f} for n, p, f in [("A", 40, 3.6), ("B", 20, 3.3), ("C", 10, 3.0), ("D", 5, 2.7), ("E", 2.5, 2.4)]]
    out = repeat_buying(WorkspaceTools(tmp_path), {"analysis": "double_jeopardy", "brands": brands, "loyalty_kind": "frequency", "focus_brand": "C"})
    assert out["loyalty_unit"] == "purchases per buyer" and out["verdict"] == "in line with size"
    assert abs(out["focus"]["residual"]) < 0.05


# --- share vs growth ----------------------------------------------------------------------------


def test_share_vs_growth_lost_share_while_sales_grew(tmp_path):
    out = repeat_buying(
        WorkspaceTools(tmp_path),
        {"analysis": "share_vs_growth", "brand": {"values": [100, 110, 120], "label": "Ours"}, "category": {"values": [1000, 1200, 1500]}, "labels": ["2024", "2025", "2026"]},
    )
    assert [p["share_pct"] for p in out["periods"]] == [10.0, 9.17, 8.0]
    assert out["brand_growth_pct"] == 20.0 and out["category_growth_pct"] == 50.0
    assert out["share_change_points"] == -2.0 and out["share_change_relative_pct"] == -20.0
    assert out["verdict"] == "lost"
    assert "Ours +20 % from 2024 to 2026" in out["summary"] and "slower than the market" in out["summary"]


def test_share_vs_growth_held_and_gained_verdicts(tmp_path):
    tools = WorkspaceTools(tmp_path)
    held = repeat_buying(tools, {"analysis": "share_vs_growth", "brand": {"values": [100, 105]}, "category": {"values": [1000, 1050]}})
    assert held["verdict"] == "held" and held["share_change_points"] == 0
    gained = repeat_buying(tools, {"analysis": "share_vs_growth", "brand": {"values": [100, 130]}, "category": {"values": [1000, 1100]}})
    assert gained["verdict"] == "gained" and gained["share_change_points"] == 1.82  # 11.82 - 10
    with pytest.raises(ToolError, match="one value per period"):
        repeat_buying(tools, {"analysis": "share_vs_growth", "brand": {"values": [1, 2, 3]}, "category": {"values": [10, 20]}})


# --- spikes ------------------------------------------------------------------------------------


def test_spikes_detects_the_injected_spike_and_not_the_noise(tmp_path):
    tools = _weekly_csv(tmp_path)
    out = repeat_buying(tools, {"path": "weekly.csv", "analysis": "spikes", "value_column": "units", "period": "week", "change_date": "2026-03-02"})
    assert len(out["periods"]) == 20 and out["periods"][0]["baseline"] is None and out["periods"][8]["baseline"] is not None
    assert len(out["spikes"]) == 1
    spike = out["spikes"][0]
    # median of the 8 weeks before (98,102,100,105,96,101,99,103) = 100.5; 320 is +218.4 %
    assert (spike["period"], spike["actual"], spike["baseline"], spike["uplift_pct"]) == ("2026-W16", 320, 100.5, 218.4)
    g = out["baseline_growth"]
    assert g["first_window"]["level"] == 100.5 and g["last_window"]["level"] == 101.0 and g["growth_pct"] == 0.5
    c = out["change"]  # 8 weeks before 2026-03-02 (median 100.5), 11 non-spike weeks after (median 101)
    assert (c["periods_before"], c["periods_after"], c["level_before"], c["level_after"], c["change_pct"]) == (8, 11, 100.5, 101.0, 0.5)
    assert "1 spike above 1.5 × the rolling baseline" in out["summary"] and "+218.4 %" in out["summary"]
    assert "median of the previous 8" in out["method"]


def test_spikes_with_too_few_periods_warns_and_finds_nothing(tmp_path):
    tools = _ws(tmp_path, "short.csv", "date,units\n2026-01-05,100\n2026-01-12,300\n2026-01-19,100\n")
    out = repeat_buying(tools, {"path": "short.csv", "analysis": "spikes", "value_column": "units", "period": "week"})
    assert out["spikes"] == [] and any("no spike can be detected" in w for w in out["warnings"])


# --- input handling -------------------------------------------------------------------------------


def test_xlsx_input_is_read_with_openpyxl(tmp_path):
    from datetime import datetime

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "orders"
    ws.append(["Customer", "Date", "Amount", "Brand"])
    for line in ORDERS_CSV.strip().splitlines()[1:]:
        order_id, customer, day, amount, brand, _sku = line.split(",")
        if not customer or day == "not a date":
            continue
        ws.append([customer, datetime.fromisoformat(day), float(amount), brand])
    wb.save(tmp_path / "orders.xlsx")
    tools = WorkspaceTools(tmp_path)
    out = repeat_buying(tools, {"path": "orders.xlsx", "sheet": "orders", "analysis": "summary", "columns": {"customer": "customer", "date": "date", "amount": "amount"}})
    assert out["buyers"] == 14 and out["purchases"] == 40 and out["revenue"] == 585
    assert out["columns"]["customer"] == "Customer" and out["missing"] == []


def test_columns_are_found_case_insensitively_and_by_synonym(tmp_path):
    tools = _ws(tmp_path, "crm.csv", "Customer_ID;Order_Date;Revenue;Brand\nx1;2026-01-01;10;A\nx1;2026-02-01;10;A\nx2;2026-01-15;10;B\n")
    out = repeat_buying(tools, {"path": "crm.csv", "analysis": "summary"})
    assert out["columns"]["customer"] == "Customer_ID" and out["columns"]["date"] == "Order_Date" and out["columns"]["amount"] == "Revenue"
    assert out["buyers"] == 2 and out["purchases"] == 3 and out["revenue"] == 30


def test_missing_column_lists_the_columns_found(tmp_path):
    with pytest.raises(ToolError, match=r"columns.customer column 'cust' not found; columns found: order_id, customer, date, amount, brand, sku"):
        _run(tmp_path, "summary", columns={"customer": "cust", "date": "date"})
    tools = _ws(tmp_path, "odd.csv", "who,when\nx,2026-01-01\n")
    with pytest.raises(ToolError, match="columns.customer is required.*columns found: who, when"):
        repeat_buying(tools, {"path": "odd.csv", "analysis": "summary"})


def test_path_escape_missing_file_and_bad_analysis_are_refused(tmp_path):
    tools = _ws(tmp_path)
    with pytest.raises(ToolError, match="escapes workspace"):
        repeat_buying(tools, {"path": "../orders.csv", "analysis": "summary"})
    with pytest.raises(ToolError, match="not found"):
        repeat_buying(tools, {"path": "nope.csv", "analysis": "summary"})
    with pytest.raises(ToolError, match="analysis must be one of"):
        repeat_buying(tools, {"path": "orders.csv", "analysis": "magic"})
    with pytest.raises(ToolError, match="period must be one of"):
        repeat_buying(tools, {"path": "orders.csv", "analysis": "summary", "period": "fortnight"})
    with pytest.raises(ToolError, match="date_from .* is not a date"):
        repeat_buying(tools, {"path": "orders.csv", "analysis": "summary", "date_from": "yesterday"})


def test_empty_window_reports_nothing_to_count(tmp_path):
    out = _run(tmp_path, "summary", date_from="2027-01-01")
    assert out["buyers"] == 0 and out["purchases"] == 0 and out["revenue"] == 0
    assert any("nothing to count" in m for m in out["missing"])
    assert out["summary"] == "No usable purchases in the window."


def test_every_analysis_carries_the_common_fields(tmp_path):
    for analysis in ("summary", "frequency", "repeat", "skip_return", "cohorts", "entrants_leavers", "duplication"):
        out = _run(tmp_path, analysis)
        for key in ("analysis", "window", "rows_used", "missing", "warnings", "flags", "method", "summary"):
            assert key in out, (analysis, key)
        assert out["analysis"] == analysis and out["rows_used"] == 41
        assert isinstance(out["summary"], str) and out["summary"].endswith(".")


def test_quarter_and_week_periods_are_labelled(tmp_path):
    out = _run(tmp_path, "entrants_leavers", period="quarter")
    assert [p["period"] for p in out["periods"]] == ["2026-Q1", "2026-Q2"]
    assert [p["buyers"] for p in out["periods"]] == [10, 9]
    out = _run(tmp_path, "summary", period="week")
    assert out["per_period"][0]["period"] == "2026-W01" and len(out["per_period"]) == 25  # 2026-01-03 (W01) .. 2026-06-18 (W25)


def test_tool_wrapper_returns_json(tmp_path):
    tools = _ws(tmp_path)
    text = repeat_buying_tool(tools, {"path": "orders.csv", "analysis": "frequency", "columns": COLUMNS})
    data = json.loads(text)
    assert data["analysis"] == "frequency" and data["buyers"] == 14
    assert "→" in repeat_buying_tool(tools, {"analysis": "share_vs_growth", "brand": {"values": [1, 2]}, "category": {"values": [10, 10]}})  # ensure_ascii=False



def test_dispatch_routes_repeat_buying():
    from rocky.tools import WorkspaceTools, dispatch, worker_tools
    import json as _json

    out = _json.loads(
        dispatch(
            WorkspaceTools("."),
            "repeat_buying",
            {"analysis": "share_vs_growth", "brand": {"values": [100, 110]}, "category": {"values": [1000, 1200]}, "labels": ["2025", "2026"]},
        )
    )
    assert out["verdict"] in {"gained", "lost", "held"}
    assert "repeat_buying" in [t["function"]["name"] for t in worker_tools(False)]
