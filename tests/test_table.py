import json

import pytest

from rocky.table import table_analyze, table_analyze_tool
from rocky.tools import ToolError, WorkspaceTools

# Service-quality records: 9 rows, one blank rating (row 8), one bad rating "abc" (row 9),
# one blank comment (row 7). Sep 1–6 2026 fall in ISO week 36, Sep 7–9 in week 37.
RATINGS_CSV = """date,city,channel,rating,wait_min,comment
2026-09-01,Vilnius,phone,5,3,Greitai atsakė
2026-09-02,Vilnius,email,4,10,Ilgai laukiau atsakymo
2026-09-03,Vilnius,phone,3,12,laukiau per ilgai
2026-09-04,Kaunas,phone,2,20,Niekas neatsiliepė
2026-09-05,Kaunas,email,4,8,ok
2026-09-06,Kaunas,chat,5,1,puiku
2026-09-07,Klaipėda,phone,1,30,
2026-09-08,Vilnius,chat,,2,blank rating here
2026-09-09,Kaunas,phone,abc,5,bad rating here
"""

# 2 × 2 survey: A yes 30 / no 20, B yes 10 / no 40 (N = 100).
SURVEY_CSV = "group,answer\n" + "A,yes\n" * 30 + "A,no\n" * 20 + "B,yes\n" * 10 + "B,no\n" * 40

# Heavy user's meter readings: three weekly readings before 2026-02-01 and three after.
ENERGY_CSV = """date,site,kwh,m2
2026-01-05,A,100,50
2026-01-12,A,110,50
2026-01-19,A,90,50
2026-02-02,A,80,50
2026-02-09,A,70,50
2026-02-16,A,60,50
"""

# Quarterly press benchmark, one row per quarter.
PRESS_CSV = """quarter,avg_basket,orders,merchants
2025-Q1,40,1000,20
2025-Q2,42,1100,22
2025-Q3,44,1200,25
2025-Q4,50,1500,25
2026-Q1,46,1300,30
"""

# Brief → report lead times: 10, 20, 30, 40, 50 days, then one end-before-start and one blank end.
LEADTIME_CSV = """brief_date,report_date
2026-01-01,2026-01-11
2026-01-05,2026-01-25
2026-02-01,2026-03-03
2026-02-10,2026-03-22
2026-03-01,2026-04-20
2026-03-05,2026-03-01
2026-03-10,
"""

# Customer list: Vilnius 60, Kaunas 30, Klaipėda 10 rows.
LIST_CSV = "id,city\n" + "".join(f"{i},Vilnius\n" for i in range(60)) + "".join(f"{i},Kaunas\n" for i in range(60, 90)) + "".join(
    f"{i},Klaipėda\n" for i in range(90, 100)
)

# A Google Ads search terms export: two preamble lines, the header, six terms, a Total row.
ADS_CSV = """Search terms report
1 Sep 2026 - 30 Sep 2026
Search term,Match type,Added/Excluded,Campaign,Ad group,Clicks,Impr.,CTR,Currency code,Avg. CPC,Cost,Conversions,Conv. value
perlas batai,Broad,None,Brand,Brand,100,1000,10.00%,EUR,0.20,20.00,10.00,500.00
PERLAS internetu,Phrase,None,Brand,Brand,50,500,10.00%,EUR,0.20,10.00,5.00,250.00
šilas parduotuvė,Broad,None,Brand,Brand,10,100,10.00%,EUR,0.50,5.00,1.00,50.00
odiniai batai vyrams,Broad,None,Generic,Shoes,200,4000,5.00%,EUR,0.50,100.00,4.00,200.00
žieminiai batai,Broad,None,Generic,Shoes,100,3000,3.33%,EUR,0.60,60.00,2.00,100.00
batai pigiai,Broad,None,Generic,Shoes,40,2000,2.00%,EUR,0.50,20.00,0.00,0.00
Total: Search terms,,,,,500,10600,4.72%,EUR,0.43,215.00,22.00,1100.00
"""


def _ws(tmp_path, name, text):
    (tmp_path / name).write_text(text, encoding="utf-8")
    return WorkspaceTools(tmp_path)


def _run(tmp_path, text, **args):
    tools = _ws(tmp_path, "data.csv", text)
    return table_analyze(tools, {"path": "data.csv", **args})


# --- describe -----------------------------------------------------------------


def test_describe_types_nulls_and_ranges(tmp_path):
    out = _run(tmp_path, RATINGS_CSV, op="describe")
    cols = {c["name"]: c for c in out["columns"]}
    assert out["rows_total"] == out["rows_used"] == 9
    assert cols["date"]["type"] == "date" and cols["date"]["min"] == "2026-09-01" and cols["date"]["max"] == "2026-09-09"
    # rating: 8 non-blank cells, 7 numbers (1 bad) → numeric; min 1, max 5, mean 24/7 = 3.43
    assert cols["rating"] == {"name": "rating", "nulls": 1, "distinct": 6, "type": "numeric", "min": 1, "max": 5, "mean": 3.43, "bad": 1}
    assert cols["city"]["type"] == "text" and cols["city"]["distinct"] == 3
    assert cols["city"]["top_values"][:2] == [{"value": "Kaunas", "n": 4}, {"value": "Vilnius", "n": 4}]
    assert cols["comment"]["nulls"] == 1
    assert out["summary"].startswith("9 rows and 6 columns (2 numeric, 1 date, 3 text)")
    assert out["missing"] == []


# --- group --------------------------------------------------------------------


def test_group_mean_reports_gap_sd_and_leaves_the_thin_group_unranked(tmp_path):
    out = _run(tmp_path, RATINGS_CSV, op="group", by="city", metric="rating", agg="mean", min_rows=2)
    groups = {g["key"]: g for g in out["groups"]}
    # Vilnius 5,4,3 → mean 4, sd 1; Kaunas 2,4,5 → 3.67, sd 1.53; Klaipėda one row → thin
    assert groups["Vilnius"] == {"key": "Vilnius", "n": 3, "value": 4, "share_pct": 42.9, "thin": False, "rank": 1, "sd": 1}
    assert groups["Kaunas"]["value"] == 3.67 and groups["Kaunas"]["sd"] == 1.53 and groups["Kaunas"]["rank"] == 2
    assert groups["Klaipėda"]["thin"] is True and groups["Klaipėda"]["rank"] is None and groups["Klaipėda"]["sd"] is None
    assert [g["key"] for g in out["groups"]] == ["Vilnius", "Kaunas", "Klaipėda"]
    assert out["total"] == 3.43  # 24 / 7
    assert out["gap"] == {"best": "Vilnius", "worst": "Kaunas", "gap": 0.33}
    assert "rating: 1 blank cell left out." in out["missing"]
    assert any("1 row dropped because rating was not a number" in m for m in out["missing"])
    assert out["warnings"] == ["1 group below 2 rows listed as thin and not ranked."]
    assert out["summary"].startswith("By city, mean of rating over 7 rows: best Vilnius 4 (n 3), worst Kaunas 3.67 (n 3), gap 0.33")


def test_group_count_and_shares(tmp_path):
    out = _run(tmp_path, RATINGS_CSV, op="group", by="channel", min_rows=1)
    # phone 5, email 2, chat 2 of 9
    assert out["agg"] == "count" and out["total"] == 9
    assert [(g["key"], g["value"], g["share_pct"]) for g in out["groups"]] == [("phone", 5, 55.6), ("chat", 2, 22.2), ("email", 2, 22.2)]
    assert out["gap"] is None


def test_group_sum_median_and_distinct(tmp_path):
    out = _run(tmp_path, RATINGS_CSV, op="group", by="city", metric="wait_min", agg="sum", min_rows=1)
    # Vilnius 3+10+12+2 = 27, Kaunas 20+8+1+5 = 34, Klaipėda 30 → total 91
    assert [(g["key"], g["value"]) for g in out["groups"]] == [("Kaunas", 34), ("Klaipėda", 30), ("Vilnius", 27)]
    assert out["total"] == 91 and out["groups"][0]["share_pct"] == 37.4
    med = _run(tmp_path, RATINGS_CSV, op="group", by="city", metric="rating", agg="median", min_rows=1)
    assert {g["key"]: g["value"] for g in med["groups"]} == {"Vilnius": 4, "Kaunas": 4, "Klaipėda": 1}
    dis = _run(tmp_path, RATINGS_CSV, op="group", by="city", agg="distinct", distinct_by="channel", min_rows=1)
    assert {g["key"]: g["value"] for g in dis["groups"]} == {"Vilnius": 3, "Kaunas": 3, "Klaipėda": 1}


def test_group_by_week_bucket(tmp_path):
    out = _run(tmp_path, RATINGS_CSV, op="group", by="week:date", min_rows=1)
    assert out["by"] == "week:date"
    assert [(g["key"], g["n"]) for g in out["groups"]] == [("2026-W36", 6), ("2026-W37", 3)]


def test_default_min_rows_makes_every_small_group_thin(tmp_path):
    out = _run(tmp_path, RATINGS_CSV, op="group", by="city", metric="rating", agg="mean")
    assert all(g["thin"] and g["rank"] is None for g in out["groups"])
    assert out["gap"] is None
    assert "no group reaches 30 rows" in out["summary"]


def test_group_mean_with_population_shares_gives_a_weighted_mean(tmp_path):
    out = _run(
        tmp_path, RATINGS_CSV, op="group", by="city", metric="rating", agg="mean", min_rows=1,
        population_shares={"Vilnius": 0.5, "Kaunas": 0.3, "Klaipėda": 0.2},
    )
    # 0.5·4 + 0.3·3.67 + 0.2·1 = 3.30 against the unweighted 3.43
    assert out["pooled_unweighted_mean"] == 3.43 and out["pooled_weighted_mean"] == 3.3


# --- window and filters ---------------------------------------------------------


def test_date_window_reports_the_dates_actually_covered(tmp_path):
    out = _run(tmp_path, RATINGS_CSV, op="group", by="city", date_column="date", date_from="2026-09-04", date_to="2026-09-06", min_rows=1)
    assert out["window"] == {"date_column": "date", "from": "2026-09-04", "to": "2026-09-06", "requested_from": "2026-09-04", "requested_to": "2026-09-06"}
    assert out["rows_total"] == 9 and out["rows_in_window"] == 3 and out["rows_used"] == 3
    assert [(g["key"], g["n"]) for g in out["groups"]] == [("Kaunas", 3)]


def test_empty_window_is_reported_not_guessed(tmp_path):
    out = _run(tmp_path, RATINGS_CSV, op="group", by="city", date_column="date", date_from="2027-01-01")
    assert out["rows_used"] == 0 and out["groups"] == []
    assert any("window is empty" in m for m in out["missing"])


def test_filters_equal_in_greater_contains_and_not_equal(tmp_path):
    eq = _run(tmp_path, RATINGS_CSV, op="describe", filter=[{"column": "city", "op": "==", "value": "vilnius"}])
    assert eq["rows_used"] == 4 and eq["inputs"]["filter"] == [{"column": "city", "op": "==", "value": "vilnius"}]
    assert _run(tmp_path, RATINGS_CSV, op="describe", filter=[{"column": "city", "op": "in", "value": ["Vilnius", "Kaunas"]}])["rows_used"] == 8
    # rating > 3 keeps 5, 4, 4, 5; the blank and "abc" cannot be compared and drop out
    assert _run(tmp_path, RATINGS_CSV, op="describe", filter=[{"column": "rating", "op": ">", "value": 3}])["rows_used"] == 4
    assert _run(tmp_path, RATINGS_CSV, op="describe", filter={"column": "comment", "op": "contains", "value": "LAUK"})["rows_used"] == 2
    assert _run(tmp_path, RATINGS_CSV, op="describe", filter=[{"column": "channel", "op": "!=", "value": "phone"}])["rows_used"] == 4
    with pytest.raises(ToolError, match="filter op"):
        _run(tmp_path, RATINGS_CSV, op="describe", filter=[{"column": "city", "op": "like", "value": "V"}])


# --- crosstab -------------------------------------------------------------------


def test_crosstab_cells_totals_and_no_chi_square_when_expected_counts_are_small(tmp_path):
    out = _run(tmp_path, RATINGS_CSV, op="crosstab", rows="city", cols="channel", min_rows=1)
    assert out["row_levels"] == ["Vilnius", "Kaunas", "Klaipėda"] and out["col_levels"] == ["phone", "email", "chat"]
    assert out["cells"]["Vilnius"]["phone"] == {"n": 2, "row_pct": 50.0}
    assert out["row_totals"] == {"Vilnius": 4, "Kaunas": 4, "Klaipėda": 1}
    assert out["col_totals"] == {"phone": 5, "email": 2, "chat": 2}
    # the two empty Klaipėda cells are below min_rows = 1 and are listed as thin
    assert out["thin_cells"] == [{"row": "Klaipėda", "col": "email", "n": 0}, {"row": "Klaipėda", "col": "chat", "n": 0}]
    assert out["chi2"] is None
    # smallest expected cell: Klaipėda × email = 1·2/9 = 0.2
    assert out["chi2_reason"] == "expected count below 5 in 9 cells (smallest 0.2); chi-square not computed"
    assert "no chi-square: expected count below 5" in out["summary"]


def test_crosstab_chi_square_with_a_known_statistic(tmp_path):
    out = _run(tmp_path, SURVEY_CSV, op="crosstab", rows="group", cols="answer")
    # χ² = N(ad − bc)² / (r1 r2 c1 c2) = 100·(30·40 − 20·10)² / (50·50·40·60) = 16.667, df 1, p ≈ 4.5e-5
    assert out["chi2"]["stat"] == 16.667 and out["chi2"]["df"] == 1 and out["chi2"]["p_value"] == pytest.approx(4.5e-5, rel=0.02)
    assert out["cells"]["A"]["yes"] == {"n": 30, "row_pct": 60.0}
    assert out["thin_cells"] == [{"row": "A", "col": "no", "n": 20}, {"row": "B", "col": "yes", "n": 10}]
    assert "incomplete gamma" in out["method"]


def test_crosstab_refuses_more_than_twenty_levels(tmp_path):
    text = "id,flag\n" + "".join(f"{i},{'a' if i % 2 else 'b'}\n" for i in range(25))
    with pytest.raises(ToolError, match="25 distinct values"):
        _run(tmp_path, text, op="crosstab", rows="id", cols="flag")


# --- before_after -----------------------------------------------------------------


def test_before_after_means_change_and_welch_t(tmp_path):
    out = _run(tmp_path, ENERGY_CSV, op="before_after", date_column="date", metric="kwh", split_date="2026-02-01")
    # before 100, 110, 90 → mean 100, sd 10; after 80, 70, 60 → mean 70, sd 10
    assert out["before"] == {"n": 3, "mean": 100, "sd": 10} and out["after"] == {"n": 3, "mean": 70, "sd": 10}
    assert out["change_abs"] == -30 and out["change_pct"] == -30.0
    # t = −30 / √(100/3 + 100/3) = −3.674, Welch df = 4, two-sided p from t(4) ≈ 0.0213
    assert out["t"] == -3.674 and out["df"] == 4.0 and out["p_value"] == pytest.approx(0.0213, abs=0.0005)
    assert any("fewer than 10 rows" in w for w in out["warnings"])
    assert "Welch" in out["method"]
    assert out["summary"].startswith("Mean kwh went from 100 (n 3) before 2026-02-01 to 70 (n 3) after, -30 %")


def test_before_after_with_normalize_by(tmp_path):
    out = _run(tmp_path, ENERGY_CSV, op="before_after", date_column="date", metric="kwh", split_date="2026-02-01", normalize_by="m2")
    # per m²: 2.0 before, 1.4 after — the relative change is unchanged
    assert out["before"]["mean"] == 2 and out["after"]["mean"] == 1.4
    assert out["change_abs"] == -0.6 and out["change_pct"] == -30.0
    assert "kwh ÷ m2" in out["method"] and "kwh per m2" in out["summary"]


def test_before_after_per_group(tmp_path):
    out = _run(tmp_path, ENERGY_CSV, op="before_after", date_column="date", metric="kwh", split_date="2026-02-01", by="site", min_rows=3)
    assert out["by"] == "site" and len(out["groups"]) == 1
    assert out["groups"][0]["key"] == "A" and out["groups"][0]["change_pct"] == -30.0 and out["groups"][0]["thin"] is False


# --- period_compare ---------------------------------------------------------------


def test_period_compare_latest_previous_and_year_ago(tmp_path):
    out = _run(tmp_path, PRESS_CSV, op="period_compare", period_column="quarter", metrics=["avg_basket", "orders"], sample_column="merchants")
    assert [p["period"] for p in out["periods"]] == ["2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4", "2026-Q1"]
    assert out["latest"] == {"period": "2026-Q1", "rows": 1, "metrics": {"avg_basket": 46, "orders": 1300}, "sample": 30}
    # vs 2025-Q4: basket 46 − 50 = −4 (−8.0 %), orders 1300 − 1500 = −200 (−13.3 %)
    assert out["change_vs_previous"] == {"avg_basket": {"abs": -4, "pct": -8.0}, "orders": {"abs": -200, "pct": -13.3}}
    # vs 2025-Q1: basket +6 (+15.0 %), orders +300 (+30.0 %)
    assert out["year_ago_period"] == "2025-Q1"
    assert out["change_vs_year_ago"] == {"avg_basket": {"abs": 6, "pct": 15.0}, "orders": {"abs": 300, "pct": 30.0}}
    assert out["summary"] == "2026-Q1: avg_basket 46 (-8 % vs 2025-Q4), orders 1,300 (-13.3 % vs 2025-Q4); avg_basket +15 % vs 2025-Q1; sample 30 merchants."


def test_period_compare_year_ago_is_null_with_reason_and_month_buckets_work(tmp_path):
    out = _run(tmp_path, PRESS_CSV, op="period_compare", period_column="quarter", metrics="avg_basket", filter=[{"column": "quarter", "op": "!=", "value": "2025-Q1"}])
    assert out["change_vs_year_ago"] is None and out["change_vs_year_ago_reason"] == "no period one year before 2026-Q1 in the file"
    monthly = _run(tmp_path, ENERGY_CSV, op="period_compare", period_column="month:date", metrics=["kwh"])
    # January 300 kWh, February 210 kWh
    assert [(p["period"], p["metrics"]["kwh"]) for p in monthly["periods"]] == [("2026-01", 300), ("2026-02", 210)]
    assert monthly["change_vs_previous"] == {"kwh": {"abs": -90, "pct": -30.0}}


# --- proportion_diff ------------------------------------------------------------


def test_proportion_diff_with_a_known_z(tmp_path):
    out = _run(tmp_path, SURVEY_CSV, op="proportion_diff", group_column="group", outcome_column="answer", positive_values=["yes"])
    # p_a = 30/50 = 0.6, p_b = 10/50 = 0.2, diff −40 pp; Wald se = √(0.0048 + 0.0032) = 0.0894 → ±17.5 pp
    # pooled p 0.4, se = √(0.4·0.6·(2/50)) = 0.098 → z = −0.4/0.098 = −4.082, p ≈ 4.5e-5
    assert (out["group_a"], out["group_b"], out["n_a"], out["n_b"]) == ("A", "B", 50, 50)
    assert out["p_a"] == 0.6 and out["p_b"] == 0.2 and out["diff_pp"] == -40.0
    assert out["ci95_pp"] == [-57.5, -22.5]
    assert out["z"] == -4.082 and out["p_value"] == pytest.approx(4.5e-5, rel=0.02) and out["significant"] is True
    assert out["summary"].startswith("B 20.0 % vs A 60.0 % positive on answer (n 50 vs 50): difference -40 pp, 95 % CI -57.5 to -22.5 pp, z -4.082")
    assert out["warnings"] == []


def test_proportion_diff_needs_exactly_two_groups_or_named_ones(tmp_path):
    with pytest.raises(ToolError, match="needs exactly two"):
        _run(tmp_path, RATINGS_CSV, op="proportion_diff", group_column="city", outcome_column="channel", positive_values=["phone"])
    out = _run(
        tmp_path, RATINGS_CSV, op="proportion_diff", group_column="city", group_a="Vilnius", group_b="Kaunas",
        outcome_column="channel", positive_values=["phone"],
    )
    # Vilnius 2 of 4 phone, Kaunas 2 of 4 → no difference
    assert out["p_a"] == 0.5 and out["p_b"] == 0.5 and out["diff_pp"] == 0.0 and out["significant"] is False
    assert any("fewer than 30 rows" in w for w in out["warnings"])


def test_single_proportion_with_wilson_interval_and_response_rate(tmp_path):
    out = _run(tmp_path, SURVEY_CSV, op="proportion_diff", outcome_column="answer", positive_values="yes", invited=250)
    # 40 of 100 → 40 %, Wilson 95 % [30.9, 49.8]; 100 of 250 invited answered
    assert out["n"] == 100 and out["positives"] == 40 and out["share_pct"] == 40.0
    assert out["ci95_pct"] == [30.9, 49.8] and out["response_rate_pct"] == 40.0
    assert "Wilson" in out["method"]


# --- intervals ------------------------------------------------------------------


def test_intervals_percentiles_and_invalid_rows(tmp_path):
    out = _run(tmp_path, LEADTIME_CSV, op="intervals", start_column="brief_date", end_column="report_date", min_rows=5)
    # 10, 20, 30, 40, 50 days: p25 20, p50 30, p75 40, p90 46 (40 + 0.6·10), max 50
    assert (out["n"], out["p25"], out["p50"], out["p75"], out["p90"], out["max"], out["mean"]) == (5, 20, 30, 40, 46, 50, 30)
    assert out["n_dropped_invalid"] == 2 and out["unit"] == "days"
    assert out["summary"] == "5 intervals from brief_date to report_date: median 30 days, p75 40, p90 46, max 50; 2 invalid dropped."
    assert out["warnings"] == []


# --- coverage -------------------------------------------------------------------


def test_coverage_against_population_shares(tmp_path):
    out = _run(tmp_path, LIST_CSV, op="coverage", by="city", population_shares={"Vilnius": 0.4, "Kaunas": 0.3, "Klaipėda": 0.2, "Šiauliai": 0.1})
    groups = {g["key"]: g for g in out["groups"]}
    # Vilnius 60 % of the list vs 40 % of the population → ratio 1.5, over; Kaunas 30/30 ok;
    # Klaipėda has 10 rows → thin; Šiauliai has none → under
    assert groups["Vilnius"] == {"key": "Vilnius", "rows": 60, "list_share_pct": 60.0, "population_share_pct": 40.0, "ratio": 1.5, "status": "over"}
    assert groups["Kaunas"]["ratio"] == 1.0 and groups["Kaunas"]["status"] == "ok"
    assert groups["Klaipėda"]["status"] == "thin"
    assert groups["Šiauliai"] == {"key": "Šiauliai", "rows": 0, "list_share_pct": 0.0, "population_share_pct": 10.0, "ratio": 0.0, "status": "under"}
    assert any("Šiauliai" in m for m in out["missing"])
    assert "under-represented Šiauliai; over-represented Vilnius" in out["summary"]


# --- text_themes ----------------------------------------------------------------


def test_text_themes_counts_shares_samples_and_groups(tmp_path):
    out = _run(
        tmp_path, RATINGS_CSV, op="text_themes", text_column="comment",
        patterns={"waiting": "lauk|wait", "nobody": "neatsiliep|no answer"}, by="city", min_rows=1,
    )
    # 8 rows have text; "lauk" matches rows 2 and 3 (both Vilnius), "neatsiliep" matches row 4 (Kaunas)
    assert out["rows_with_text"] == 8
    themes = {t["theme"]: t for t in out["themes"]}
    assert themes["waiting"]["n"] == 2 and themes["waiting"]["share_pct"] == 25.0
    assert themes["waiting"]["sample"] == ["Ilgai laukiau atsakymo", "laukiau per ilgai"]
    assert themes["nobody"]["n"] == 1 and themes["nobody"]["share_pct"] == 12.5
    vilnius = next(g for g in themes["waiting"]["by_group"] if g["key"] == "Vilnius")
    assert vilnius == {"key": "Vilnius", "n": 2, "share_pct": 50.0, "thin": False}
    assert out["unmatched"] == 5 and out["unmatched_pct"] == 62.5
    assert "1 row with a blank comment left out." in out["missing"]
    assert out["summary"].startswith("Of 8 rows with text in comment: waiting 2 (25 %), nobody 1 (12.5 %); 5 (62.5 %) matched no theme.")


def test_text_themes_truncates_samples_and_rejects_bad_regex(tmp_path):
    long_text = "x" * 400
    text = f"id,comment\n1,{long_text}\n"
    out = _run(tmp_path, text, op="text_themes", text_column="comment", patterns={"x": "x+"})
    assert len(out["themes"][0]["sample"][0]) == 160
    with pytest.raises(ToolError, match="not a valid regex"):
        _run(tmp_path, text, op="text_themes", text_column="comment", patterns={"bad": "("})


# --- search_terms_split ---------------------------------------------------------


def test_search_terms_split_on_a_google_ads_export_with_lithuanian_brand_terms(tmp_path):
    out = _run(tmp_path, ADS_CSV, op="search_terms_split", brand_terms=["Perlas", "ŠILAS"])
    assert out["term_column"] == "Search term"
    assert out["columns"] == {"cost": "Cost", "conversions": "Conversions", "value": "Conv. value"}
    # branded: 20 + 10 + 5 = 35 spend, 16 conversions, 800 value → CPA 2.19, ROAS 22.86
    assert out["branded"]["rows"] == 3 and out["branded"]["spend"] == 35 and out["branded"]["conversions"] == 16
    assert out["branded"]["value"] == 800 and out["branded"]["cpa"] == 2.19 and out["branded"]["roas"] == 22.86
    # non-branded: 100 + 60 + 20 = 180 spend, 6 conversions, 300 value → CPA 30, ROAS 1.67
    assert out["non_branded"]["rows"] == 3 and out["non_branded"]["spend"] == 180 and out["non_branded"]["cpa"] == 30 and out["non_branded"]["roas"] == 1.67
    # 35 of 215 → 16.3 %
    assert out["brand_share_of_spend_pct"] == 16.3 and out["branded"]["share_of_spend_pct"] == 16.3
    assert out["brand_term_matches"] == {"Perlas": 2, "ŠILAS": 1}
    assert [t["term"] for t in out["top_non_branded"]] == ["odiniai batai vyrams", "žieminiai batai", "batai pigiai"]
    assert out["top_non_branded"][0]["cpa"] == 25 and out["top_non_branded"][2]["cpa"] is None
    assert "1 total row from the export left out." in out["missing"]
    assert any("2 lines above the header skipped" in m for m in out["missing"])
    assert out["summary"].startswith("Branded terms take 16.3 % of spend (35 of 215) across 3 of 6 terms, CPA 2.19 vs 30 non-branded")


def test_search_terms_split_without_a_cost_column_still_splits(tmp_path):
    text = "Search term,Clicks\nperlas batai,10\nbatai,20\n"
    out = _run(tmp_path, text, op="search_terms_split", brand_terms="perlas")
    assert out["branded"]["rows"] == 1 and out["non_branded"]["rows"] == 1 and out["branded"]["spend"] is None
    assert any("No cost column" in w for w in out["warnings"])


# --- files, errors, wrapper -------------------------------------------------------


def test_xlsx_via_openpyxl_with_real_dates(tmp_path):
    from datetime import datetime

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Atsakymai"
    ws.append(["date", "city", "rating"])
    ws.append([datetime(2026, 9, 1), "Vilnius", 5])
    ws.append([datetime(2026, 9, 2), "Vilnius", 3])
    ws.append([datetime(2026, 9, 3), "Kaunas", 4])
    wb.save(tmp_path / "survey.xlsx")
    tools = WorkspaceTools(tmp_path)
    out = table_analyze(tools, {"path": "survey.xlsx", "sheet": "Atsakymai", "op": "group", "by": "city", "metric": "rating", "agg": "mean", "date_column": "date", "min_rows": 1})
    assert out["window"]["from"] == "2026-09-01" and out["window"]["to"] == "2026-09-03"
    assert {g["key"]: g["value"] for g in out["groups"]} == {"Vilnius": 4, "Kaunas": 4}
    with pytest.raises(ToolError, match="sheet 'Kita' not found"):
        table_analyze(tools, {"path": "survey.xlsx", "sheet": "Kita", "op": "describe"})


def test_missing_column_lists_the_columns_found(tmp_path):
    with pytest.raises(ToolError) as exc:
        _run(tmp_path, RATINGS_CSV, op="group", by="town")
    assert "columns found: date, city, channel, rating, wait_min, comment" in str(exc.value)


def test_non_numeric_metric_says_how_many_cells_failed(tmp_path):
    with pytest.raises(ToolError, match=r"city is not numeric: 9 of 9 non-blank cells failed"):
        _run(tmp_path, RATINGS_CSV, op="group", by="channel", metric="city", agg="mean")


def test_path_escape_is_refused_and_unknown_op_rejected(tmp_path):
    tools = _ws(tmp_path, "data.csv", RATINGS_CSV)
    with pytest.raises(ToolError, match="escapes workspace"):
        table_analyze(tools, {"path": "../../etc/passwd.csv", "op": "describe"})
    with pytest.raises(ToolError, match="op must be one of"):
        table_analyze(tools, {"path": "data.csv", "op": "pivot"})
    with pytest.raises(ToolError, match="not found"):
        table_analyze(tools, {"path": "nope.csv", "op": "describe"})


def test_tool_wrapper_returns_json(tmp_path):
    tools = _ws(tmp_path, "data.csv", RATINGS_CSV)
    text = table_analyze_tool(tools, {"path": "data.csv", "op": "group", "by": "city", "min_rows": 1})
    data = json.loads(text)
    assert data["op"] == "group" and data["rows_used"] == 9 and "Klaipėda" in text
    assert set(data) >= {"window", "rows_used", "rows_total", "missing", "warnings", "summary", "groups"}



def test_dispatch_routes_table_analyze(tmp_path):
    import json as _json

    from rocky.tools import WorkspaceTools, dispatch, worker_tools

    tools = WorkspaceTools(tmp_path)
    tools.write_file("data/t.csv", "branch,score\nA,4\nA,5\nB,2\nB,3\n")
    out = _json.loads(dispatch(tools, "table_analyze", {"op": "group", "path": "data/t.csv", "by": ["branch"], "metric": "score", "agg": "mean", "min_rows": 1}))
    assert out["op"] == "group" if "op" in out else True
    assert "table_analyze" in [t["function"]["name"] for t in worker_tools(False)]


# --- cohort_compare ---------------------------------------------------------------


def _orders_csv():
    # 6 cohort ids c1..c6 and 10 others u1..u10, all with a row on 2026-10-01 (before the fix).
    # After the fix (2026-10-15, 30-day window): c1..c4 return (c1..c3 paid), u1..u5 return (u1..u4 paid).
    rows = ["user_id,order_date,status,error_code"]
    rows += [f"c{i},2026-10-01,failed,ADDR_SAVE" for i in range(1, 7)]
    rows += [f"u{i},2026-10-01,paid," for i in range(1, 11)]
    rows += [f"c{i},2026-10-20,{'paid' if i <= 3 else 'open'}," for i in range(1, 5)]
    rows += [f"u{i},2026-10-22,{'paid' if i <= 4 else 'open'}," for i in range(1, 6)]
    rows += ["u1,2026-12-30,paid,"]  # outside the 30-day window
    return "\n".join(rows) + "\n"


def test_cohort_compare_return_and_success_rates_with_wald_interval(tmp_path):
    out = _run(
        tmp_path, _orders_csv(), op="cohort_compare", id_column="user_id", date_column="order_date", success_column="status",
        positive_values=["paid"], cohort_filter={"column": "error_code", "op": "==", "value": "ADDR_SAVE"}, from_date="2026-10-15", window_days=30, min_rows=10,
    )
    c = out["cohort"]
    # cohort 4/6 = 66.7 % returned, 3/6 = 50 % paid; others 5/10 = 50 % returned, 4/10 = 40 % paid
    assert c["status"] == "ok" and c["cohort"]["n"] == 6 and c["others"]["n"] == 10
    assert c["cohort"]["returned_pct"] == 66.7 and c["others"]["returned_pct"] == 50.0
    assert c["cohort"]["succeeded_pct"] == 50.0 and c["others"]["succeeded_pct"] == 40.0
    # diff +16.7 pp; Wald se = √(0.5·0.5/10 + 0.667·0.333/6) = √(0.025 + 0.037) = 0.249 → ±48.8 pp
    assert c["returned_diff"]["diff_pp"] == 16.7 and c["returned_diff"]["ci95_pp"] == [-32.2, 65.5]
    assert c["succeeded_diff"]["diff_pp"] == 10.0
    assert out["summary"].startswith("4 of 6 cohort ids (66.7 %) returned vs 50 % of 10 others (+16.7 pp, 95 % CI -32.2 to +65.5 pp)")
    assert "Wald" in out["method"]


def test_cohort_compare_insufficient_cohort_gives_no_percentages(tmp_path):
    out = _run(tmp_path, _orders_csv(), op="cohort_compare", id_column="user_id", date_column="order_date", cohort_ids=["c1", "c2", "c3", "zz"], from_date="2026-10-15", min_rows=5)
    assert out["cohort"]["status"] == "insufficient" and out["cohort"]["cohort"]["n"] == 3
    assert out["cohort"]["cohort"]["returned_pct"] is None and out["cohort"]["returned_diff"] is None
    assert "1 cohort id not found in user_id." in out["missing"]
    assert any("min_cohort" in w for w in out["warnings"])
    with pytest.raises(ToolError, match="needs a cohort"):
        _run(tmp_path, _orders_csv(), op="cohort_compare", id_column="user_id", date_column="order_date")


# Referral log from #264: A Jan–Apr (4 events), B Jan, C Jan, D Feb–Mar, E Mar, F Mar–May.
REFERRALS_CSV = """referrer_id,activated_at
A,2026-01-10
A,2026-02-03
A,2026-03-12
A,2026-04-20
B,2026-01-15
C,2026-01-28
D,2026-02-09
D,2026-03-02
E,2026-03-05
F,2026-03-18
F,2026-04-11
F,2026-05-06
"""


def test_cohort_compare_retention_by_month_with_censoring(tmp_path):
    out = _run(tmp_path, REFERRALS_CSV, op="cohort_compare", id_column="referrer_id", date_column="activated_at", period="month", horizon_periods=3, min_rows=1)
    r = out["retention"]
    # 6 actors, 3 with a single event (B, C, E) = 50 %; spans 4,1,1,2,1,3 → median 1.5
    assert r["actors"] == 6 and r["events"] == 12 and r["single_event_share"] == {"count": 3, "pct": 50.0}
    assert r["active_span"]["median_periods"] == 1.5
    jan, feb, mar = r["cohorts"]
    # Jan cohort A, B, C: only A active in Feb (+1) and Apr (+3) → 33.3 %
    assert jan["cohort"] == "2026-01" and jan["size"] == 3
    assert jan["retention"][0] == {"offset": 1, "active": 1, "pct": 33.3, "censored": False}
    assert jan["retention"][2] == {"offset": 3, "active": 1, "pct": 33.3, "censored": False}
    # Feb cohort D: nobody in May (+3) → 0 %; Mar cohort E, F: June (+3) starts after the data ends 2026-05-06 → censored
    assert feb["retention"][2]["pct"] == 0.0 and mar["retention"][2] == {"offset": 3, "active": None, "pct": None, "censored": True}
    assert r["data_ends"] == "2026-05-06"
    periods = {p["period"]: p for p in r["periods"]}
    assert periods["2026-01"]["new_ids"] == 3 and periods["2026-03"]["active_ids"] == 4 and periods["2026-03"]["cumulative_registered"] == 6
    assert "3 (50 %) had a single event; median active span 1.50 periods; oldest cohort 2026-01 (n 3) retained 33.3 % at +3; too young to judge at +3: 2026-03" in out["summary"]


def test_cohort_compare_compare_column_medians_and_completion(tmp_path):
    text = """customer_id,order_date,saved,checkout_seconds,completed
a,2026-05-01,0,100,1
b,2026-05-01,0,112,0
c,2026-05-01,0,130,1
d,2026-05-02,1,40,1
e,2026-05-02,1,48,1
f,2026-05-02,1,56,1
"""
    out = _run(tmp_path, text, op="cohort_compare", id_column="customer_id", date_column="order_date", compare_column="saved", metrics=["checkout_seconds"], success_column="completed", min_rows=3)
    g = {x["value"]: x for x in out["compare"]["groups"]}
    # saved=0 median 112 s, 2 of 3 completed; saved=1 median 48 s, 3 of 3 → −64 s (−57.1 %), +33.3 pp
    assert g["0"]["metrics"]["checkout_seconds"]["median"] == 112 and g["0"]["completion_rate_pct"] == 66.7
    assert g["1"]["metrics"]["checkout_seconds"]["median"] == 48 and g["1"]["completion_rate_pct"] == 100.0
    d = out["compare"]["difference"]
    assert d["metrics"]["checkout_seconds"] == {"absolute": -64, "pct": -57.1, "direction": "lower"}
    assert d["completion_diff"]["diff_pp"] == 33.3
    assert "saved=1 (n 3): checkout_seconds median 48, completion 100 %; 1 vs 0: checkout_seconds -64 (-57.1 %), completion +33.3 pp." in out["summary"]


# --- change_impact ------------------------------------------------------------------


def _checkout_weekly(late_orders=(2410, 2420), late_complaints=(14, 11), post_weeks=6):
    # 8 pre weeks: 12 complaints on ~2 400 orders (mean 2 400, sd 16.04 → band 2 368–2 432); 5.0 per 1 000.
    pre = [(2380, 12), (2420, 12), (2390, 12), (2410, 12), (2400, 12), (2420, 12), (2380, 12), (2400, 12)]
    post = [(2150, 61), (2300, 44), (2350, 30), (2400, 20), (late_orders[0], late_complaints[0]), (late_orders[1], late_complaints[1])][:post_weeks]
    from datetime import date, timedelta

    rows = ["week_start,orders,complaints"]
    start = date(2026, 8, 6)
    for i, (o, c) in enumerate(pre + post):
        rows.append(f"{(start + timedelta(weeks=i)).isoformat()},{o},{c}")
    return "\n".join(rows) + "\n"


def test_change_impact_three_windows_and_adjustment_verdict(tmp_path):
    out = _run(tmp_path, _checkout_weekly(), op="change_impact", date_column="week_start", metric="orders", complaints_column="complaints", exposure_column="orders", change_date="2026-10-01", rolling=2)
    # pre 96/19 200 = 5.0; early (61+44)/(2150+2300) = 23.6; late (14+11)/(2410+2420) = 5.2 per 1 000
    assert out["pre"]["complaint_rate_per_1000"] == 5.0 and out["early"]["complaint_rate_per_1000"] == 23.6 and out["late"]["complaint_rate_per_1000"] == 5.2
    assert out["pre"]["metric_mean"] == 2400 and out["pre"]["metric_sd"] == 16.04 and out["noise_band"] == [2367.92, 2432.08]
    assert out["late"]["metric_mean"] == 2415 and out["metric_recovered"] is True
    assert out["complaint_ratio_late_vs_pre"] == 1.04 and out["verdict"] == "adjustment"
    # largest 2-week drop: (2380+2400)/2 = 2390 → (2150+2300)/2 = 2225 on 2026-10-01
    assert out["change_point"]["date"] == "2026-10-01" and out["change_point"]["before_mean"] == 2390 and out["change_point"]["after_mean"] == 2225
    assert "complaints per 1 000: pre 5.0, early 23.6, late 5.2" in out["summary"] and "verdict adjustment" in out["summary"]


def test_change_impact_rejection_and_too_early(tmp_path):
    rej = _run(tmp_path, _checkout_weekly(late_orders=(2000, 2010), late_complaints=(42, 40)), op="change_impact", date_column="week_start", metric="orders", complaints_column="complaints", exposure_column="orders", change_date="2026-10-01", rolling=2)
    # late 82/4 010 = 20.4 per 1 000 → 4.08× pre; late orders 2 005 below the band
    assert rej["late"]["complaint_rate_per_1000"] == 20.4 and rej["complaint_ratio_late_vs_pre"] == 4.08
    assert rej["metric_recovered"] is False and rej["verdict"] == "rejection"
    early = _run(tmp_path, _checkout_weekly(post_weeks=2), op="change_impact", date_column="week_start", metric="orders", complaints_column="complaints", exposure_column="orders", change_date="2026-10-01", rolling=2)
    assert early["verdict"] == "too_early" and "2 more post" in early["reason"]
    with pytest.raises(ToolError, match="needs metric"):
        _run(tmp_path, _checkout_weekly(), op="change_impact", date_column="week_start")


def test_change_impact_per_segment_shared_drop(tmp_path):
    from datetime import date, timedelta

    rows = ["date,source,sessions"]
    start = date(2026, 10, 6)
    for i in range(28):
        d = (start + timedelta(days=i)).isoformat()
        after = i >= 14  # 2026-10-20 onwards
        rows += [f"{d},google,{580 if after else 900}", f"{d},bing,{38 if after else 60}", f"{d},duckduckgo,20"]
    out = _run(tmp_path, "\n".join(rows) + "\n", op="change_impact", date_column="date", metric="sessions", by="source", min_level=30)
    seg = {s["segment"]: s for s in out["segments"]}
    # google 900 → 580 = −35.6 %, bing 60 → 38 = −36.7 %, both on 2026-10-20; duckduckgo (20/day) below min_level
    assert seg["google"]["change_point"]["date"] == "2026-10-20" and seg["google"]["change_point"]["drop_pct"] == -35.6
    assert seg["bing"]["change_point"]["drop_pct"] == -36.7 and "duckduckgo" not in seg
    assert out["shared_drop"] is True and out["drop_verdict"] == "shared" and out["next_checks"] == ["robots.txt", "http_status", "meta_robots", "analytics_tag"]
    assert any("duckduckgo" in m and "below min_level" in m for m in out["missing"])
    assert out["summary"].startswith("google fell -35.6 % on 2026-10-20 (900 → 580); bing fell -36.7 % on 2026-10-20 (60 → 38); shared drop")


# --- media_response -------------------------------------------------------------------


def _sales_weekly():
    # 40 weeks at 1 000; March burst weeks 10–13 (+180, €10k), post excess 120, 72, 43, 26, 16 (r 0.6);
    # September burst weeks 30–33 (+180, €10.5k), post excess 120, 36, 11, 3 (r ≈ 0.3).
    from datetime import date, timedelta

    excess = {w: 180 for w in range(10, 14)} | dict(zip(range(14, 19), (120, 72, 43, 26, 16))) | {w: 180 for w in range(30, 34)} | dict(zip(range(34, 38), (120, 36, 11, 3)))
    spend = {w: 10000 for w in range(10, 14)} | {w: 10500 for w in range(30, 34)}
    rows = ["week,sales,tv_spend"]
    for w in range(1, 41):
        rows.append(f"{(date(2026, 1, 5) + timedelta(weeks=w - 1)).isoformat()},{1000 + excess.get(w, 0)},{spend.get(w, 0)}")
    return "\n".join(rows) + "\n"


def test_media_response_carryover_half_life_and_comparison(tmp_path):
    from datetime import date, timedelta

    wk = lambda w: (date(2026, 1, 5) + timedelta(weeks=w - 1)).isoformat()  # noqa: E731
    out = _run(
        tmp_path, _sales_weekly(), op="media_response", date_column="week", metric="sales", spend_column="tv_spend",
        campaigns=[{"name": "March", "start": wk(10), "end": wk(13)}, {"name": "September", "start": wk(30), "end": wk(33)}],
    )
    march, sept = out["campaigns"]
    # in-flight 4 × 180 = 720; residual 120+72+43+26+16 = 277; week-on-week ratios 0.60, 0.60, 0.60, 0.62 → fitted r ≈ 0.60, half-life ln 0.5 / ln 0.6 ≈ 1.36
    assert march["baseline"] == {"periods": 4, "mean": 1000, "sd": 0} and march["in_flight_excess"] == 720 and march["residual_excess"] == 277
    assert march["decay_fit"]["retention_rate"] == pytest.approx(0.60, abs=0.01) and march["decay_fit"]["half_life_periods"] == pytest.approx(1.37, abs=0.03)
    assert march["decay_fit"]["r_squared"] > 0.99 and march["decay_fit"]["n_points"] == 5
    # September residual 120+36+11+3 = 170; log-linear slope on (0..3) → r ≈ 0.29, half-life ≈ 0.56
    assert sept["residual_excess"] == 170 and sept["decay_fit"]["retention_rate"] == pytest.approx(0.29, abs=0.01)
    assert sept["decay_fit"]["half_life_periods"] == pytest.approx(0.56, abs=0.02)
    assert march["total_incremental"] == 997 and march["residual_share_pct"] == 27.8
    # March − September = 107 units, +62.9 %
    assert out["comparison"] == {"ranking": ["March", "September"], "best": "March", "residual_difference": 107, "residual_difference_pct": 62.9}
    assert out["lag_correlation"]["best_lag"] == 0 and "not causation" in out["lag_correlation"]["note"]
    assert "March left 107 more after media stopped (+62.9 %)" in out["summary"]


def test_media_response_detects_bursts_and_refuses_a_short_baseline(tmp_path):
    out = _run(tmp_path, _sales_weekly(), op="media_response", date_column="week", metric="sales", spend_column="tv_spend")
    assert [c["name"] for c in out["campaigns"]] == ["burst 1", "burst 2"] and out["campaigns"][0]["residual_excess"] == 277
    assert any("2 burst(s) detected" in w for w in out["warnings"])
    short = _run(tmp_path, _sales_weekly(), op="media_response", date_column="week", metric="sales", campaigns=[{"name": "early", "start": "2026-01-12", "end": "2026-01-19"}])
    assert short["campaigns"][0]["status"] == "insufficient_baseline" and any("only 1 periods before the burst" in m for m in short["missing"])
    assert "lag_correlation" not in short


# --- price_response -----------------------------------------------------------------------


SIGNUPS_CSV = """month,price,signups,promo
2025-12-01,19,405,no
2026-01-01,19,430,no
2026-02-01,19,395,no
2026-03-01,24,360,yes
2026-04-01,24,350,no
2026-05-01,24,340,no
2026-06-01,24,366,no
"""


def test_price_response_from_a_file_excludes_promo_rows(tmp_path):
    out = _run(tmp_path, SIGNUPS_CSV, op="price_response", date_column="month", price_column="price", units_column="signups", split_date="2026-03-01", promo_column="promo", positive_values=["yes"], variable_cost=3)
    # before 410/month at €19, after (352) at €24 with March excluded; ΔP/P̄ = 5/21.5 = +23.3 %, ΔQ/Q̄ = −58/381 = −15.2 % → arc −0.65
    assert out["before"]["units_per_period"] == 410 and out["after"]["units_per_period"] == 352 and out["after"]["rows"] == 3
    assert out["pct_change_price"] == 23.3 and out["pct_change_units"] == -15.2 and out["elasticity_arc"] == -0.65 and out["classification"] == "inelastic"
    # revenue 19·410 = 7 790 → 24·352 = 8 448 (+8.4 %); contribution (19−3)·410 = 6 560 → (24−3)·352 = 7 392
    assert out["revenue_before"] == 7790 and out["revenue_after"] == 8448 and out["revenue_change_pct"] == 8.4
    assert out["contribution_before"] == 6560 and out["contribution_after"] == 7392 and out["contribution_change"] == 832
    assert "1 promo row excluded (promo positive)." in out["caveats"] and any("season mismatch" in c for c in out["caveats"])
    assert out["summary"].startswith("Price 19 → 24 (+23.3 % midpoint), units 410 → 352 per period (-15.2 %): arc elasticity -0.65 (inelastic); revenue 7,790 → 8,448 (+8.4 %)")


def test_price_response_inline_with_cross_elasticity_and_range_total(tmp_path):
    tools = WorkspaceTools(tmp_path)
    out = table_analyze(
        tools,
        {
            "op": "price_response", "before": {"price": 34.9, "units": 1200}, "after": {"price": 29.9, "units": 1650}, "variable_cost": 18,
            "related": [
                {"name": "Whey 2 kg", "price_before": 59.9, "units_before": 480, "units_after": 390, "variable_cost": 33},
                {"name": "Shaker", "price_before": 9.9, "units_before": 300, "units_after": 410, "variable_cost": 3},
            ],
        },
    )
    # ΔP/P̄ = −5/32.4 = −15.4 %, ΔQ/Q̄ = 450/1425 = 31.6 % → arc −2.05 (elastic); contribution 16.9·1200 = 20 280 → 11.9·1650 = 19 635 (−645)
    assert out["inputs"]["path"] is None and out["elasticity_arc"] == -2.05 and out["classification"] == "elastic" and out["contribution_change"] == -645
    two, shaker = out["cross"]
    # 2 kg: −90/435 = −20.7 % ÷ −15.4 % = +1.34 substitute, contribution −90·26.9 = −2 421; shaker: 110/355 = 31.0 % ÷ −15.4 % = −2.01 complement, +110·6.9 = +759
    assert (two["cross_elasticity"], two["type"], two["contribution_change"]) == (1.34, "substitute", -2421)
    assert (shaker["cross_elasticity"], shaker["type"], shaker["contribution_change"]) == (-2.01, "complement", 759)
    assert out["range_contribution_change"] == -2307
    assert "range contribution change -2,307" in out["summary"]
    same = table_analyze(tools, {"op": "price_response", "before": {"price": 19, "units": 400}, "after": {"price": 19, "units": 380}})
    assert same["elasticity_arc"] is None and same["next_step"] == "experiment_plan" and any("second_price_period" in m for m in same["missing"])


# --- promo_effect ------------------------------------------------------------------------


def _juice_weekly():
    # weeks 20–45; 1L baseline 1 000, weeks 30–33 at 2 100, 34–39 at 700, then 1 000; 2L 400, weeks 30–37 at 320; 0.5L flat 600.
    from datetime import date, timedelta

    rows = ["week,item,units"]
    for w in range(20, 46):
        d = (date(2026, 5, 11) + timedelta(weeks=w - 20)).isoformat()
        one = 2100 if 30 <= w <= 33 else 700 if 34 <= w <= 39 else 1000
        two = 320 if 30 <= w <= 37 else 400
        rows += [f"{d},1L,{one}", f"{d},2L,{two}", f"{d},0.5L,600"]
    return "\n".join(rows) + "\n"


def test_promo_effect_lift_dip_recovery_and_portfolio_net(tmp_path):
    from datetime import date, timedelta

    wk = lambda w: (date(2026, 5, 11) + timedelta(weeks=w - 20)).isoformat()  # noqa: E731
    out = _run(tmp_path, _juice_weekly(), op="promo_effect", date_column="week", item_column="item", metric="units", promo_item="1L", promo_from=wk(30), promo_to=wk(33), related_items=["2L", "0.5L"])
    one, two, half = out["items"]
    # 1L: +1 100 × 4 = +4 400 during; post 8 weeks: 6 × −300 = −1 800 → net +2 600; after mean (4 200 + 2 000)/8 = 775 (−22.5 %), trough −30 %, back in band at post week 7
    assert one["baseline_mean"] == 1000 and one["during_mean"] == 2100 and one["lift_pct"] == 110.0
    assert one["incremental_during"] == 4400 and one["change_after"] == -1800 and one["net_units"] == 2600
    assert one["after_mean"] == 775 and one["dip_pct"] == -22.5 and one["trough_pct"] == -30.0 and one["recovery_periods"] == 7
    # 2L: −80 × 4 during and −80 × 4 of the 8 post weeks → −640; 0.5L flat → 0; portfolio +1 960
    assert two["net_units"] == -640 and two["recovery_periods"] == 5 and half["net_units"] == 0
    assert out["portfolio_net_units"] == 1960 and out["post_window"]["periods"] == 8 and out["baseline_window"]["periods"] == 8
    assert out["summary"].startswith("1L: baseline 1,000 per period, during 2,100 (+110 %), after 775 (-22.5 %), back within 10 % after 7 period(s); net +2,600; 2L net -640; 0.5L net +0; portfolio net +1,960 units")


def test_promo_effect_without_enough_post_periods_gives_no_verdict(tmp_path):
    from datetime import date, timedelta

    wk = lambda w: (date(2026, 5, 11) + timedelta(weeks=w - 20)).isoformat()  # noqa: E731
    out = _run(tmp_path, _juice_weekly(), op="promo_effect", date_column="week", item_column="item", metric="units", promo_item="1L", promo_from=wk(42), promo_to=wk(43), related_items=["3L"])
    assert out["items"][0]["status"] == "insufficient" and out["portfolio_net_units"] is None
    assert any("item '3L' not in item" in m for m in out["missing"])
    with pytest.raises(ToolError, match="no periods between"):
        _run(tmp_path, _juice_weekly(), op="promo_effect", date_column="week", item_column="item", metric="units", promo_item="1L", promo_from="2027-01-01", promo_to="2027-01-31")


# --- choice_mix -----------------------------------------------------------------------------


def _mattress_csv():
    # 100 orders: size 160x200 ×71, 140x200 ×20, 180x200 ×9; firmness medium ×54, firm ×46; fabric 4 values 30/28/22/20.
    sizes = ["160x200"] * 71 + ["140x200"] * 20 + ["180x200"] * 9
    firm = ["medium"] * 54 + ["firm"] * 46
    fabric = ["grey"] * 30 + ["blue"] * 28 + ["beige"] * 22 + ["green"] * 20
    status = ["paid"] * 92 + ["open"] * 8
    return "size,firmness,fabric,status\n" + "".join(f"{s},{f},{fa},{st}\n" for s, f, fa, st in zip(sizes, firm, fabric, status))


def test_choice_mix_verdicts_per_option_column(tmp_path):
    out = _run(tmp_path, _mattress_csv(), op="choice_mix", option_columns=["size", "firmness", "fabric"], outcome_column="status", positive_values=["paid"])
    size, firm, fabric = out["columns"]
    assert (size["top_value"], size["top_share_pct"], size["verdict"], size["values_for_90pct"]) == ("160x200", 71.0, "shared_default", 2)
    assert (firm["top_share_pct"], firm["verdict"]) == (54.0, "unclear")
    assert (fabric["top_share_pct"], fabric["verdict"], fabric["values_for_90pct"]) == (30.0, "buyers_specify", 4)
    # the first 71 rows (all 160x200) are paid → 100 %; 180x200 rows 92–100: one paid, 8 open → 11.1 %
    assert size["values"][0]["outcome_rate_pct"] == 100.0 and size["values"][2] == {"value": "180x200", "count": 9, "share_pct": 9.0, "outcome_rate_pct": 11.1, "thin": True}
    assert out["summary"] == "size: 160x200 71 % → shared_default; firmness: medium 54 % → unclear; fabric: grey 30 % → buyers_specify."


def test_choice_mix_thin_column_and_missing_column(tmp_path):
    out = _run(tmp_path, RATINGS_CSV, op="choice_mix", option_columns=["channel"])
    assert out["columns"][0]["verdict"] is None and "below min_rows 30" in out["columns"][0]["reason"]
    with pytest.raises(ToolError, match="columns found"):
        _run(tmp_path, RATINGS_CSV, op="choice_mix", option_columns=["colour"])


# --- basket -------------------------------------------------------------------------------------


BASKET_CSV = """order_id,customer_id,sku,net_eur,date
o1,c1,BAR,10,2026-01-01
o1,c1,SHAKER,8,2026-01-01
o2,c1,BAR,10,2026-02-15
o2,c1,SHAKER,8,2026-02-15
o3,c2,BAR,10,2026-01-10
o3,c2,SHAKER,8,2026-01-10
o3,c2,TEE,20,2026-01-10
o4,c3,BAR,10,2026-01-12
o5,c4,SHAKER,8,2026-01-03
o6,c5,TEE,20,2026-01-04
o7,c6,TEE,20,2026-01-05
o8,c7,CAP,12,2026-01-06
o9,c8,CAP,12,2026-01-07
o10,c9,TEE,20,2026-01-08
"""


def test_basket_attach_rate_lift_revenue_and_repeat(tmp_path):
    out = _run(tmp_path, BASKET_CSV, op="basket", order_column="order_id", sku_column="sku", focus_skus=["BAR"], revenue_column="net_eur", customer_column="customer_id", date_column="date", min_rows=1)
    # 4 of 10 baskets hold BAR; SHAKER in 3 of them (75 %) vs 4 of 10 overall (40 %) → lift 1.88; TEE in 1 of 4 (25 %) vs 40 % → 0.62
    assert out["baskets_total"] == 10 and out["baskets_with_focus"] == 4 and out["focus_share_pct"] == 40.0
    shaker, tee = out["attached"]
    assert (shaker["sku"], shaker["baskets"], shaker["attach_rate_pct"], shaker["base_rate_pct"], shaker["lift"], shaker["rank"]) == ("SHAKER", 3, 75.0, 40.0, 1.88, 1)
    assert (tee["sku"], tee["attach_rate_pct"], tee["lift"]) == ("TEE", 25.0, 0.62)
    # revenue per basket: with BAR (18+18+38+10)/4 = 21; without (8+20+20+12+12+20)/6 = 15.33
    assert out["revenue_per_basket"] == {"with_focus": 21, "without_focus": 15.33}
    # c1 bought BAR twice (45 days apart), c2 and c3 once → repeat 33.3 %; line revenue (36 + 38 + 10)/3 = 28
    assert out["repeat"] == {"focus_buyers": 3, "repeat_buyers": 1, "repeat_rate_pct": 33.3, "median_days_between_focus_orders": 45, "line_revenue_per_focus_buyer": 28}
    assert out["summary"].startswith("4 of 10 baskets (40 %) contain BAR; top attached: SHAKER 75 % (lift 1.88), TEE 25 % (lift 0.62); revenue per basket 21 with vs 15.33 without; 33.3 % of 3 focus buyers bought again, median 45 days apart")


def test_basket_without_customer_column_says_repeat_is_unavailable(tmp_path):
    out = _run(tmp_path, BASKET_CSV, op="basket", order_column="order_id", sku_column="sku", focus_skus="BAR")
    assert out["repeat"] == "unavailable: no customer_column" and out["revenue_per_basket"] is None
    assert all(a["thin"] and a["rank"] is None for a in out["attached"])  # min_rows 30
    assert any("no customer_column" in m for m in out["missing"]) and any("thin" in w for w in out["warnings"])
    with pytest.raises(ToolError, match="focus_skus is required"):
        _run(tmp_path, BASKET_CSV, op="basket", order_column="order_id", sku_column="sku")


# --- buyer_frequency -------------------------------------------------------------------------


def _shop_orders():
    # 47 consumers: 30 × 2 orders, 10 × 3, 7 × 5 (125 orders) plus wholesale W with 35 → 160 orders, 48 buyers.
    rows = ["customer_id,channel"]
    k = 0
    for count, n in ((2, 30), (3, 10), (5, 7)):
        for _ in range(n):
            k += 1
            rows += [f"b{k},web"] * count
    rows += ["W,wholesale"] * 35
    return "\n".join(rows) + "\n"


def test_buyer_frequency_reports_with_and_without_the_outlier(tmp_path):
    out = _run(tmp_path, _shop_orders(), op="buyer_frequency", id_column="customer_id", group_column="channel")
    # 160/48 = 3.33 with W; 125/47 = 2.66 without → −20.1 %; Q1 2, Q3 3 → cut 3 + 3·1 = 6, only W (35) is above
    assert out["with_outliers"]["buyers"] == 48 and out["with_outliers"]["mean_frequency"] == 3.33
    assert out["without_outliers"]["mean_frequency"] == 2.66 and out["delta_pct"] == -20.1
    assert out["outlier_rule"] == {"method": "iqr", "threshold": 3.0, "cut_above": 6}
    assert out["outliers"] == [{"id": "W", "purchases": 35, "qty": None, "group": "wholesale", "share_of_purchases_pct": 21.9, "decision": "investigate"}]
    # top 24 buyers: 35 + 7·5 + 10·3 + 6·2 = 112 of 160 = 70 %
    assert out["with_outliers"]["heavy_half_share_pct"] == 70.0
    assert {d["purchases"]: d["buyers"] for d in out["distribution"]} == {2: 30, 3: 10, 5: 7, 35: 1}
    assert out["nbd"]["fit"] == "too_few_buyers" and out["reporting"]["reporting_unknown"] is True
    assert out["summary"].startswith("48 buyers, 160 purchases: 3.33 per buyer; 1 outlier(s) (W, 21.9 % of purchases) → 2.66 without them (-20.1 %); decide whether they belong; the top half of buyers brings 70 % of purchases")


def test_buyer_frequency_nbd_fit_and_zero_class(tmp_path):
    # 100 buyers: 60 × 1, 20 × 2, 10 × 3, 5 × 6, 5 × 15 → 235 purchases, mean 2.35, sample variance 9.93
    rows = ["hh"]
    k = 0
    for count, n in ((1, 60), (2, 20), (3, 10), (6, 5), (15, 5)):
        for _ in range(n):
            k += 1
            rows += [f"h{k}"] * count
    text = "\n".join(rows) + "\n"
    out = _run(tmp_path, text, op="buyer_frequency", id_column="hh", outlier_rule={"method": "multiple_of_median", "threshold": 10})
    # k = m²/(v − m) = 5.5225/7.577 = 0.729, p = m/v = 0.237; no zero class in the file
    assert out["nbd"]["mean"] == 2.35 and out["nbd"]["variance"] == 9.93 and out["nbd"]["k"] == 0.729 and out["nbd"]["p"] == 0.237
    assert out["nbd"]["fit"] == "ok_conditional_on_buying" and out["nbd"]["zero_class"] == "absent"
    assert [b["band"] for b in out["bands"]] == ["0", "1", "2", "3", "4", "5", "6–9", "10+"] and sum(b["observed"] for b in out["bands"]) == 100
    assert out["bands"][7]["observed"] == 5 and "10+" in out["divergent_bands"]
    # the 5 buyers with 15 purchases exceed 10 × median 1 → outliers
    assert len(out["outliers"]) == 5 and out["without_outliers"]["mean_frequency"] == 1.68
    added = _run(tmp_path, text, op="buyer_frequency", id_column="hh", population_size=200)
    assert added["nbd"]["zero_class"] == "added" and added["nbd"]["fit"] == "ok" and added["bands"][0]["observed"] == 100


def test_buyer_frequency_reporter_split_from_a_second_file(tmp_path):
    # continuous h1..h4 (52 weeks): 5 × brand A (1 unit) + 1 × B (15 units) each → A share 5/20 = 25 %, frequency 5
    # intermittent h5, h6 (26 weeks): 3 × A + 1 × B (17 units) → 15 %, frequency 3 → 6.0 per 52 weeks
    rows = ["household_id,brand,units"]
    for h in range(1, 5):
        rows += [f"h{h},A,1"] * 5 + [f"h{h},B,15"]
    for h in range(5, 7):
        rows += [f"h{h},A,1"] * 3 + [f"h{h},B,17"]
    (tmp_path / "reporting.csv").write_text("household_id,weeks_reported\nh1,52\nh2,52\nh3,52\nh4,52\nh5,26\nh6,26\n", encoding="utf-8")
    out = _run(
        tmp_path, "\n".join(rows) + "\n", op="buyer_frequency", id_column="household_id", min_rows=1,
        reporting_path="reporting.csv", reporting_column="weeks_reported", expected_periods=52, brand_column="brand", focal_brand="A", units_column="units",
    )
    rep = out["reporting"]
    c, i = rep["groups"]["continuous"], rep["groups"]["intermittent"]
    assert (c["households"], c["brand_share_pct"], c["mean_frequency"], c["penetration_pct"]) == (4, 25.0, 5.0, 100.0)
    assert (i["households"], i["brand_share_pct"], i["mean_frequency"], i["frequency_per_expected_periods"]) == (2, 15.0, 3.0, 6.0)
    assert rep["gap"] == {"brand_share_pp": 10.0, "frequency_pct": -40.0} and rep["base"] == "continuous"
    assert "base continuous" in out["summary"]


# --- panel ----------------------------------------------------------------------------------------


PANEL_CSV = """hh_id,quarter,brand,units
h1,2025-Q1,Dvaro,10
h2,2025-Q1,Dvaro,10
h3,2025-Q1,Dvaro,10
h4,2025-Q1,Dvaro,10
h1,2025-Q2,Dvaro,30
h2,2025-Q2,Dvaro,30
h3,2025-Q2,Dvaro,30
h4,2025-Q2,Dvaro,30
h3,2025-Q1,Rokiskio,10
h4,2025-Q1,Rokiskio,10
h5,2025-Q1,Rokiskio,10
h6,2025-Q1,Rokiskio,10
h3,2025-Q2,Rokiskio,10
h4,2025-Q2,Rokiskio,10
h5,2025-Q2,Rokiskio,10
h6,2025-Q2,Rokiskio,10
h7,2025-Q1,Zemaitijos,10
h8,2025-Q1,Zemaitijos,10
h7,2025-Q2,Zemaitijos,10
h8,2025-Q2,Zemaitijos,10
"""


def test_panel_share_swing_duplication_and_missing_denominator(tmp_path):
    out = _run(tmp_path, PANEL_CSV, op="panel", household_column="hh_id", brand_column="brand", period_column="quarter", units_column="units", min_rows=1)
    b = {x["name"]: x for x in out["brands"]}
    # Q1 shares 40/40/20; Q2 Dvaro 120 of 180 = 66.7, Rokiskio 22.2, Zemaitijos 11.1 → Dvaro swings 26.7 pp, Rokiskio 17.8, Zemaitijos 8.9
    assert b["Dvaro"]["volume_share_pct_by_period"] == {"2025-Q1": 40.0, "2025-Q2": 66.7} and b["Dvaro"]["max_swing_pp"] == 26.7
    assert b["Dvaro"]["penetration_pct"] is None and b["Dvaro"]["penetration_reason"] == "buyer-only file, no population denominator"
    # Dvaro buyers h1–h4: 160 Dvaro units of their 200 category units (h3, h4 also buy 20 Rokiskio each) → share of requirement 80 %; 8 rows over 4 buyers → frequency 2
    assert b["Dvaro"]["share_of_requirement_pct"] == 80.0 and b["Dvaro"]["purchase_frequency"] == 2.0
    # duplication: Dvaro∩Rokiskio = 2 of 4 = 50 %; D = mean(1, 1, 0, 0, 0, 0) = 0.33 → expected 0.33 × 0.5 = 16.7 % → 3.0×
    dr = next(p for p in out["duplication"]["pairs"] if p["a"] == "Dvaro" and p["b"] == "Rokiskio")
    assert dr["observed_pct"] == 50.0 and dr["expected_pct"] == 16.7 and dr["ratio"] == 3.0 and dr["flag"] is True
    assert out["assumptions_ok"] is False and out["violations"][0] == "Dvaro share swings 26.7 pp between periods (> 5 pp)"
    assert out["ask_for"].startswith("count of all continuously reporting panel households")
    assert "penetration not computable (buyer-only file)" in out["summary"] and "Dirichlet norms do not apply" in out["summary"]
    with_total = _run(tmp_path, PANEL_CSV, op="panel", household_column="hh_id", brand_column="brand", period_column="quarter", units_column="units", total_households=20, min_rows=1)
    assert {x["name"]: x["penetration_pct"] for x in with_total["brands"]} == {"Dvaro": 20.0, "Rokiskio": 20.0, "Zemaitijos": 10.0} and with_total["ask_for"] is None


def test_panel_needs_three_brands_and_a_period(tmp_path):
    out = _run(tmp_path, PANEL_CSV, op="panel", household_column="hh_id", brand_column="brand", period_column="quarter", filter=[{"column": "brand", "op": "!=", "value": "Zemaitijos"}], min_rows=1)
    assert out["assumptions_ok"] is None and "insufficient periods/brands (2 periods, 2 brands" in out["assumptions_reason"]
    with pytest.raises(ToolError, match="period_column"):
        _run(tmp_path, PANEL_CSV, op="panel", household_column="hh_id", brand_column="brand")
    every = _run(tmp_path, PANEL_CSV, op="panel", household_column="hh_id", brand_column="brand", period_column="quarter", filter=[{"column": "brand", "op": "==", "value": "Dvaro"}], min_rows=1)
    assert every["brands"][0]["warning"] == "buyers_as_denominator"


# --- survey ------------------------------------------------------------------------------------


def test_survey_tally_median_and_share_at_threshold(tmp_path):
    # 10 respondents, the last one left the answer blank (an all-blank row would be dropped by the loader, so the id column keeps it)
    text = "id,recommenders_before_trial\n" + "".join(f"{i + 1},{v}\n" for i, v in enumerate(["0", "1", "1", "2", "3", "2", "1", "5", "2", ""]))
    out = _run(tmp_path, text, op="survey", questions=["recommenders_before_trial"], threshold=2)
    q = out["questions"][0]
    # 9 answered, 1 blank; sorted 0 1 1 1 2 2 2 3 5 → median 2, mean 17/9 = 1.89; 5 of 9 at ≥ 2 = 55.6 %
    assert (q["answered"], q["blank"], q["median"], q["mean"]) == (9, 1, 2, 1.89)
    assert q["share_at_or_above_threshold"] == {"threshold": 2.0, "count": 5, "pct": 55.6}
    assert q["distribution"][0] == {"value": "0", "count": 1, "pct_of_answered": 11.1}
    assert any("small_sample" in w for w in out["warnings"])
    assert out["summary"] == "recommenders_before_trial: 9 answered, 1 blank; median 2, mean 1.89; 5 of 9 (55.6 %) at or above 2."


def test_survey_top_two_box_per_period_and_scale_mismatch(tmp_path):
    # Q2: 12 answers, 9 at 4–5 → 75.0 %; Q3: 10 answers, 8 at 4–5 → 80.0 %, plus 2 blanks
    q2 = ["5"] * 5 + ["4"] * 4 + ["3", "2", "1"]
    q3 = ["5"] * 4 + ["4"] * 4 + ["3", "2", "", ""]
    text = "period,q_sat\n" + "".join(f"Q2,{v}\n" for v in q2) + "".join(f"Q3,{v}\n" for v in q3)
    out = _run(tmp_path, text, op="survey", response_column="q_sat", scale={"min": 1, "max": 5}, method="top_two_box", group_column="period", question="How satisfied are you with delivery?", min_rows=5)
    s = out["score"]
    # overall 17 of 22 = 77.3 %
    assert s["score"] == 77.3 and s["n_answered"] == 22 and s["n_blank"] == 2 and s["n_out_of_range"] == 0
    assert [(g["group"], g["n"], g["score"]) for g in s["per_group"]] == [("Q2", 12, 75.0), ("Q3", 10, 80.0)]
    assert s["comparable"] is True and s["definition"]["counted_values"] == [4, 5]
    assert out["summary"].startswith("77.3 % top-two-box (4–5 on a 1–5 scale, “How satisfied are you with delivery?”, n=22, 2 blank excluded); by group: Q2 75.0 % (n=12), Q3 80.0 % (n=10)")
    bad = _run(tmp_path, text.replace("Q2,1\n", "Q2,9\n"), op="survey", response_column="q_sat", scale={"min": 1, "max": 5}, group_column="period", min_rows=5)
    assert bad["score"]["n_out_of_range"] == 1 and bad["score"]["comparable"] is False and "Q2" in bad["score"]["comparable_reason"]
    with pytest.raises(ToolError, match="method must be"):
        _run(tmp_path, text, op="survey", response_column="q_sat", scale={"min": 1, "max": 5}, method="average")


# --- brand_recall ---------------------------------------------------------------------------


def _responses_csv():
    # 20 respondents: 6 name Pieno žvaigždės unaided (30 %), 13 recognise ad A (65 %);
    # 10 remembered the message: Varlė 4, Pigu 3, Senukai 1, nežinau 2.
    rows = ["id,q1_unaided,recog_a,remembered,who"]
    unaided = ["Pieno žvaigždės; Rokiškio"] * 4 + ["pieno zvaigzdes"] * 2 + ["Rokiškio"] * 8 + [""] * 6
    recog = ["1"] * 13 + ["0"] * 7
    remembered = ["1"] * 10 + ["0"] * 10
    who = ["Varlė"] * 4 + ["Pigu.lt"] * 3 + ["Senukai"] + ["nežinau"] * 2 + [""] * 10
    for i in range(20):
        rows.append(f"{i + 1},{unaided[i]},{recog[i]},{remembered[i]},{who[i]}")
    return "\n".join(rows) + "\n"


def test_brand_recall_keeps_unaided_and_recognition_apart_and_scores_attribution(tmp_path):
    out = _run(
        tmp_path, _responses_csv(), op="brand_recall", unaided_column="q1_unaided", brand_aliases={"Pieno žvaigždės": ["pieno zvaigzdes"], "Rokiškio": ["rokiskio"]},
        recognition_columns={"ad A": "recog_a"}, attribution_columns={"free delivery": "who"}, remembered_columns={"free delivery": "remembered"},
        own_brand="Varlė", competitor_brands=["Pigu.lt", "Senukai"], min_rows=5,
    )
    assert out["base_n"] == 20
    assert out["unaided"]["Pieno žvaigždės"] == {"count": 6, "pct": 30.0, "first_mention_pct": 30.0} and out["unaided"]["Rokiškio"]["pct"] == 60.0
    assert out["recognition"]["ad A"]["pct"] == 65.0
    a = out["attribution"]["free delivery"]
    # of 10 who remembered: 40 % Varlė, 30 % Pigu, 10 % Senukai, 20 % don't know → misattribution 40 %, mainly Pigu
    assert a["remembered_n"] == 10 and a["own"]["pct"] == 40.0 and a["competitors"]["Pigu.lt"]["pct"] == 30.0 and a["dont_know"]["pct"] == 20.0
    assert a["misattribution_pct"] == 40.0 and a["top_misattributed_to"] == "Pigu.lt" and a["small_base"] is False
    assert "never combined" in out["method"]
    assert out["summary"].startswith("unaided: Rokiškio 60 % (12/20), Pieno žvaigždės 30 % (6/20), Pigu.lt 0 % (0/20); recognition when shown: ad A 65 % (13/20); free delivery: of 10 who remembered (50 %), 40 % credited Varlė, Pigu.lt 30 %, Senukai 10 %, 20 % did not know; misattribution 40 %, mainly Pigu.lt")


def test_brand_recall_lists_unmatched_mentions_and_needs_inputs(tmp_path):
    out = _run(tmp_path, _responses_csv(), op="brand_recall", unaided_column="q1_unaided", own_brand="Pieno žvaigždės", min_rows=5)
    # without aliases the ASCII spelling and Rokiškio are unmatched: 2 + 4 + 8 = 14 mentions
    assert out["unaided"]["Pieno žvaigždės"]["count"] == 4 and sum(u["count"] for u in out["unaided_unmatched"]) == 14
    assert any("matched no brand alias" in m for m in out["missing"])
    with pytest.raises(ToolError, match="brand_recall needs"):
        _run(tmp_path, _responses_csv(), op="brand_recall")


# --- keyword_demand ----------------------------------------------------------------------------


KEYWORDS_CSV = """Keyword,Avg. monthly searches,CPC,KD
dantų implantai,12100,2.5,60
dantų implantai kaina,2400,2.0,40
dantų implantai kaina Kaune,1000,1.8,30
implantų kaina Vilniuje,800,1.9,30
dantų implantai Kaune,1900,2.1,35
implantai Vilnius,1200,2.2,35
implantas per vieną dieną,900,1.5,20
ar skauda dėti implantą,1200,0.5,10
zubnye implanty,300,1.0,10
"""


def test_keyword_demand_long_tail_versus_head_with_stated_assumptions(tmp_path):
    out = _run(
        tmp_path, KEYWORDS_CSV, op="keyword_demand", head_term="dantų implantai",
        groups=[
            {"name": "price", "keywords": ["dantų implantai kaina", "dantų implantai kaina Kaune", "implantų kaina Vilniuje"]},
            {"name": "location", "keywords": ["dantų implantai Kaune", "implantai Vilnius"]},
            {"name": "procedure", "keywords": ["implantas per vieną dieną", "ar skauda dėti implantą"]},
        ],
        ctr_share_pct=25, conversion_rate_pct={"head": 1, "price": 3, "location": 3, "procedure": 3}, order_value_eur=1500,
    )
    # head 12 100 × 25 % = 3 025 visits × 1 % × €1 500 = €45 375; groups 4 200 / 3 100 / 2 100 = 9 400 → 2 350 visits × 3 % × 1 500 = €105 750; ratio 2.33
    assert out["head"]["volume"] == 12100 and out["head"]["est_visits"] == 3025 and out["head"]["est_value_eur"] == 45375
    assert [(g["name"], g["volume_sum"]) for g in out["groups"]] == [("price", 4200), ("location", 3100), ("procedure", 2100)]
    assert out["long_tail"] == {"n_keywords": 7, "volume_sum": 9400, "est_visits": 2350, "est_value_eur": 105750}
    assert out["ratio_long_tail_to_head"] == 2.33 and out["ratio_basis"] == "value"
    assert out["unmatched_keywords"] == [{"keyword": "zubnye implanty", "volume": 300}] and out["groups"][0]["cpc_avg"] == 1.9
    assert out["assumptions"][0].startswith("ctr_share_pct 25 %")
    assert out["summary"].startswith("Head dantų implantai 12,100 searches/month → ~3,025 visits, €45,375; long tail 7 keywords 9,400 searches → ~2,350 visits, €105,750; long tail ÷ head = 2.33 by value (assuming 25 % click share, conversion as given, €1500 per order)")


def test_keyword_demand_by_modifiers_and_without_conversion(tmp_path):
    out = _run(tmp_path, KEYWORDS_CSV, op="keyword_demand", head_term="dantų implantai", groups=[{"name": "price", "modifiers": ["kaina"]}])
    assert out["groups"][0]["n_keywords"] == 3 and out["groups"][0]["est_orders"] is None and out["ratio_basis"] == "volume"
    # 4 200 / 12 100 = 0.35 by volume; 5 keywords left unmatched
    assert out["ratio_long_tail_to_head"] == 0.35 and len(out["unmatched_keywords"]) == 5
    with pytest.raises(ToolError, match="groups is required"):
        _run(tmp_path, KEYWORDS_CSV, op="keyword_demand", head_term="dantų implantai")
    with pytest.raises(ToolError, match="could not find the volume column"):
        _run(tmp_path, RATINGS_CSV, op="keyword_demand", keyword_column="comment", groups=[{"name": "x", "modifiers": ["a"]}])


# --- page_breadth -------------------------------------------------------------------------------


GSC_Q1 = """Top pages,Clicks,Impressions
https://dovanos.lt/,120,3000
https://dovanos.lt/dovanos-jam,40,900
https://dovanos.lt/dovanos-jam?utm=x,5,100
https://dovanos.lt/dovanos-jai/,30,800
https://dovanos.lt/blog/kaledos,1,50
https://dovanos.lt/blog/velykos,0,40
"""

GSC_Q2 = """Top pages,Clicks,Impressions
https://dovanos.lt/,150,3200
https://dovanos.lt/dovanos-jam,60,1000
https://dovanos.lt/dovanos-jai,35,800
https://dovanos.lt/blog/kaledos,3,80
https://dovanos.lt/blog/velykos,2,60
https://dovanos.lt/akcijos,10,200
https://dovanos.lt/dovanu-kuponai,0,30
"""

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://dovanos.lt/</loc></url>
<url><loc>https://dovanos.lt/dovanos-jam</loc></url>
<url><loc>https://dovanos.lt/dovanos-jai</loc></url>
<url><loc>https://dovanos.lt/blog/kaledos</loc></url>
<url><loc>https://dovanos.lt/blog/velykos</loc></url>
<url><loc>https://dovanos.lt/dovanu-kuponai</loc></url>
<url><loc>https://dovanos.lt/apie</loc></url>
<url><loc>https://dovanos.lt/kontaktai</loc></url>
</urlset>
"""


def test_page_breadth_two_periods_and_sitemap_cross_check(tmp_path):
    (tmp_path / "q1.csv").write_text(GSC_Q1, encoding="utf-8")
    (tmp_path / "q2.csv").write_text(GSC_Q2, encoding="utf-8")
    (tmp_path / "sitemap.xml").write_text(SITEMAP, encoding="utf-8")
    out = table_analyze(WorkspaceTools(tmp_path), {"op": "page_breadth", "path": "q1.csv", "period_label": "Q1", "compare_path": "q2.csv", "compare_label": "Q2", "index_path": "sitemap.xml"})
    q1, q2 = out["periods"]
    # Q1: the ?utm row merges into /dovanos-jam (45) and the trailing slash is dropped → 4 pages with ≥ 1 click of 5 distinct, 196 clicks; Q2: 6 of 7, 260 clicks
    assert (q1["distinct_pages"], q1["distinct_pages_with_traffic"], q1["metric_total"], q1["top_10_share_pct"]) == (5, 4, 196, 100.0)
    assert (q2["distinct_pages_with_traffic"], q2["metric_total"]) == (6, 260)
    assert out["delta"] == {"pages_abs": 2, "pages_pct": 50.0, "metric_abs": 64, "metric_pct": 32.7}
    assert out["pages_new"] == {"count": 2, "sample": ["https://dovanos.lt/akcijos", "https://dovanos.lt/blog/velykos"]} and out["pages_lost"]["count"] == 0
    # 8 sitemap URLs; apie, kontaktai and dovanu-kuponai never had a click → 3 (37.5 %); /akcijos has traffic but is not in the sitemap
    assert out["index"]["indexed_total"] == 8 and out["index"]["indexed_without_traffic"]["count"] == 3 and out["index"]["indexed_without_traffic"]["pct"] == 37.5
    assert out["index"]["traffic_not_in_index"] == {"count": 1, "sample": ["https://dovanos.lt/akcijos"]}
    assert out["summary"] == "Q1: 4 pages with ≥ 1 Clicks (196 total, top 10 = 100 %); Q2: 6 pages with ≥ 1 Clicks (260 total, top 10 = 100 %); +2 pages (+50 %), 2 new, 0 lost; 8 indexed URLs, 3 (37.5 %) without traffic."


def test_page_breadth_single_period_and_missing_url_column(tmp_path):
    out = _run(tmp_path, GSC_Q1, op="page_breadth")
    assert out["periods"][0]["label"] == "period 1" and out["delta"] is None and out["index"]["status"] == "not_available"
    with pytest.raises(ToolError, match="could not find the url column"):
        _run(tmp_path, RATINGS_CSV, op="page_breadth")


# --- market_match --------------------------------------------------------------------------------


def _city_weekly():
    # 10 weeks: Kaunas = 1.4 × Klaipėda exactly (r 1.0, no relative gap); Šiauliai moves the other way; Vilnius has 5 weeks only.
    from datetime import date, timedelta

    klaipeda = [100, 110, 95, 120, 105, 115, 90, 125, 100, 110]
    siauliai = [80, 60, 90, 50, 85, 55, 95, 45, 80, 60]
    rows = ["week,city,units"]
    for i in range(10):
        d = (date(2026, 1, 5) + timedelta(weeks=i)).isoformat()
        rows += [f"{d},Kaunas,{klaipeda[i] * 1.4:g}", f"{d},Klaipėda,{klaipeda[i]}", f"{d},Šiauliai,{siauliai[i]}"]
        if i < 5:
            rows.append(f"{d},Vilnius,{200 + i}")
    return "\n".join(rows) + "\n"


def test_market_match_ranks_the_control_city(tmp_path):
    out = _run(tmp_path, _city_weekly(), op="market_match", market_column="city", date_column="week", metric="units", test_market="Kaunas", test_weeks=6)
    pairs = {p["control"]: p for p in out["pairs"]}
    kl = pairs["Klaipėda"]
    assert (kl["trend_correlation"], kl["level_ratio"], kl["cv_difference"], kl["min_detectable_lift_pct"], kl["verdict"]) == (1.0, 1.4, 0.0, 0.0, "comparable")
    assert pairs["Šiauliai"]["trend_correlation"] < 0 and pairs["Šiauliai"]["verdict"] == "unsuitable"
    assert out["best_pair"] == {"test": "Kaunas", "control": "Klaipėda"} and "Vilnius" not in pairs
    assert any("Vilnius: only 5 periods" in m for m in out["missing"])
    assert out["readout_rule"].startswith("lift = (test_during ÷ test_pre) − (control_during ÷ control_pre)")
    assert out["summary"].startswith("Best pair Kaunas vs Klaipėda: r = 1.0, Kaunas sells 1.4× Klaipėda, a lift of at least 0.0 % over 6 weeks is readable above noise; unsuitable: Šiauliai")


def test_market_match_unknown_test_market_and_no_pairs(tmp_path):
    with pytest.raises(ToolError, match="test_market 'Panevėžys' not in city"):
        _run(tmp_path, _city_weekly(), op="market_match", market_column="city", date_column="week", metric="units", test_market="Panevėžys")
    out = _run(tmp_path, _city_weekly(), op="market_match", market_column="city", date_column="week", metric="units", min_periods=20)
    assert out["pairs"] == [] and out["best_pair"] is None and out["summary"] == "No comparable pair: no market has enough periods."


# --- campaign_compare ------------------------------------------------------------------------------


def test_campaign_compare_growth_per_esov_point_inline(tmp_path):
    out = table_analyze(
        WorkspaceTools(tmp_path),
        {
            "op": "campaign_compare",
            "campaigns": [
                {"name": "2024", "sov_pct": 22, "som_start_pct": 9.0, "som_end_pct": 10.5, "months": 6, "spend_eur": 180000},
                {"name": "2025", "sov_pct": 14, "som_start_pct": 10.5, "som_end_pct": 11.0, "months": 3, "spend_eur": 90000},
            ],
        },
    )
    a, b = out["rows"]
    # 2024: ESOV 13, +1.5 pp in 6 months → 3.0 pp/year → 0.231 per point; 2025: ESOV 3.5, +0.5 pp in 3 months → 2.0 → 0.571
    assert (a["esov_pts"], a["share_change_pp"], a["annualized_change_pp"], a["growth_per_esov_point"], a["spend_per_pp_annualized"]) == (13, 1.5, 3.0, 0.231, 60000)
    assert (b["esov_pts"], b["annualized_change_pp"], b["growth_per_esov_point"]) == (3.5, 2.0, 0.571)
    assert out["ranking"] == ["2025", "2024"]
    assert out["comparability"]["duration_ratio"] == 2.0 and out["comparability"]["spend_ratio"] == 2.0 and out["comparability"]["comparable"] is False
    assert out["comparability"]["warnings"] == ["durations differ 2×: match duration before comparing", "spending weight differs 2×"]
    assert out["summary"] == "2024: ESOV 13 pts, +3 pp/year → 0.231 pp per ESOV point; 2025: ESOV 3.5 pts, +2 pp/year → 0.571 pp per ESOV point; most efficient per point: 2025 (indicative only: durations differ 2×: match duration before comparing; spending weight differs 2×)."


def test_campaign_compare_benchmark_by_brand_type_from_a_file(tmp_path):
    # established ×3 at 0.08 per point, launch ×3 at 0.35, niche ×2 at 0.02 (ESOV 10, 24 months) and one row without months
    text = "brand,esov,change,months,type\n"
    text += "".join(f"E{i},10,1.6,24,established\n" for i in range(3)) + "".join(f"L{i},10,7,24,launch\n" for i in range(3)) + "".join(f"N{i},10,0.4,24,niche\n" for i in range(2))
    text += "X,10,1,,established\n"
    out = _run(tmp_path, text, op="campaign_compare", name_column="brand", esov_pts_column="esov", share_change_pp_column="change", months_column="months", brand_type_column="type")
    g = out["benchmark"]["groups"]
    assert (g["established"]["n"], g["established"]["mean_growth_per_pt"]) == (3, 0.08) and g["launch"]["mean_growth_per_pt"] == 0.35
    assert g["niche"] == {"n": 2, "median_growth_per_pt": 0.02, "mean_growth_per_pt": 0.02, "too_few": True}
    # pooled (3·0.08 + 3·0.35 + 2·0.02)/8 = 0.166, more than 50 % away from the established 0.08 → contaminated
    assert out["benchmark"]["pooled"]["mean_growth_per_pt"] == 0.166 and out["benchmark"]["contamination"]["flag"] is True
    assert out["rows"][-1]["status"] == "incomplete" and "X: missing months." in out["missing"]
    assert "pooled 0.166 is contaminated" in out["summary"]
    with pytest.raises(ToolError, match=r"campaign_compare needs campaigns\[\] inline or a path"):
        table_analyze(WorkspaceTools(tmp_path), {"op": "campaign_compare"})


# --- session_log ---------------------------------------------------------------------------------


SESSIONS_CSV = """session,time,action,source,reason
s1,10:00,open,g2.com,
s1,10:02,open,capterra.com,
s1,10:05,discard,capterra.com,nėra lietuviško šablono
s1,10:06,revisit,g2.com,
s2,11:00,open,g2.com,
s2,11:03,open,invohub.lt,
s2,11:05,revisit,g2.com,
s2,11:07,discard,invohub.lt,per brangu
s3,12:00,open,g2.com,
s3,12:01,revisit,g2.com,
s3,12:02,discard,g2.com,nėra lietuviško šablono
s3,12:03,scroll,g2.com,
"""


def test_session_log_counts_revisits_and_discard_reasons(tmp_path):
    out = _run(tmp_path, SESSIONS_CSV, op="session_log", session_column="session", action_column="action", source_column="source", reason_column="reason", min_rows=3)
    # 3 sessions, 11 mapped actions (one 'scroll' left out): 5 opens, 3 discards, 3 revisits → revisit rate 3/11 = 27.3 %; g2.com has all 3 revisits
    assert (out["sessions"], out["actions"], out["counts"]) == (3, 11, {"open": 5, "discard": 3, "revisit": 3}) and out["revisit_rate_pct"] == 27.3
    assert out["by_source"][0] == {"source": "g2.com", "open": 3, "discard": 1, "revisit": 3, "revisit_share_of_actions_pct": 27.3}
    # options opened per session: 2, 2, 1 → median 2
    assert out["median_options_opened_per_session"] == 2
    assert out["top_discard_reasons"] == [{"reason": "nėra lietuviško šablono", "count": 2}, {"reason": "per brangu", "count": 1}]
    assert any("scroll (1)" in m for m in out["missing"])
    assert out["summary"] == "3 sessions, 11 actions: 5 opens, 3 discards, 3 revisits (27.3 %); median 2 options opened per session; most revisited g2.com (3 revisits, 27.3 % of actions); top discard reason “nėra lietuviško šablono” (2)."


def test_session_log_with_one_session_is_unsuitable(tmp_path):
    out = _run(tmp_path, SESSIONS_CSV, op="session_log", session_column="session", action_column="action", source_column="source", reason_column="reason", filter=[{"column": "session", "value": "s1"}])
    assert out["status"] == "unsuitable" and out["by_source"] == [] and out["top_discard_reasons"] == [] and out["counts"]["open"] == 2
    assert any("fewer than 2" in w for w in out["warnings"])


# --- customer_audit --------------------------------------------------------------------------------


CUSTOMERS_CSV = """phone,email,name,source
+37061234567,jonas@example.lt,Jonas Jonaitis,phone
,Jonas.P@gmail.com,Jonas Petraitis,e-shop
+37069999999,ona@example.lt,Ona Onaitė,e-shop
8 612 34567,,Jonas J.,e-shop
+37061111111,rasa1@example.lt,Rasa Kazlauskienė,phone
+37062222222,tomas@example.lt,Tomas T.,phone
,jonas.p@gmail.com,J. Petraitis,phone
+37063333333,rasa2@example.lt,Rasa Kazlauskienė,e-shop
,,Nežinomas,phone
+37064444444,lina@example.lt,Lina L.,e-shop
"""


def test_customer_audit_duplicates_by_phone_and_email_with_name_candidates(tmp_path):
    out = _run(tmp_path, CUSTOMERS_CSV, op="customer_audit", mode="duplicates", source_column="source")
    d = out["duplicates"]
    # rows 1+4 share the phone (written two ways, phone + e-shop sources), rows 2+7 the Gmail address; rows 5+8 share only a name; row 9 has no contact
    assert d["confirmed_clusters"] == [{"key_type": "phone", "rows": ["1", "4"], "sources": ["e-shop", "phone"]}, {"key_type": "email", "rows": ["2", "7"], "sources": ["e-shop", "phone"]}]
    assert d["candidate_clusters"] == [{"name": "Rasa Kazlauskienė", "rows": ["5", "8"], "reason": "same name, different contacts; needs confirmation"}]
    assert d["cross_source_matches"] == 2 and d["rows_without_contact"] == 1
    assert d["distinct_customers"] == {"min": 7, "max": 8} and d["remove_for_send"] == ["4", "7"]
    assert out["columns"] == {"phone": "phone", "email": "email", "name": "name", "source": "source"}
    assert out["summary"] == "10 rows: 2 confirmed duplicate cluster(s) (2 across sources), 1 name-only candidate(s) to confirm, 1 without usable contact; distinct customers 7–8, not 10."
    with pytest.raises(ToolError, match="neither a phone nor an email column"):
        _run(tmp_path, RATINGS_CSV, op="customer_audit", mode="duplicates")


def test_customer_audit_stale_share_and_sendable_range(tmp_path):
    text = """email,last_order,bounced
a@example.lt,2022-03-10,
b@example.lt,2022-07-01,
rasa@@gmail,2022-11-20,
d@example.lt,2025-06-01,
e@example.lt,2026-01-15,TRUE
info@uab-lumina.lt,2026-01-20,
g@example.lt,2026-10-01,
h@example.lt,n/a,
"""
    out = _run(tmp_path, text, op="customer_audit", mode="stale", last_activity_column="last_order", stale_after_months=18, as_of="2026-11-08", bounce_column="bounced")
    s = out["stale"]
    # cutoff 2025-05-08: 7 dated rows, 3 stale (42.9 %), 1 undated; 1 invalid email (a stale row), 1 role address, 1 bounced
    assert s["cutoff"] == "2025-05-08" and (s["dated_rows"], s["stale_rows"], s["stale_share_pct"], s["undated_rows"]) == (7, 3, 42.9, 1)
    assert (s["invalid_email_rows"], s["role_address_rows"], s["bounced_rows"]) == (1, 1, 1)
    # max = 8 − |invalid ∪ bounced| = 6; min = 8 − |invalid ∪ bounced ∪ stale| = 8 − 4 = 4
    assert s["sendable_estimate"] == {"max": 6, "min": 4} and s["oldest"] == "2022-03-10" and s["newest"] == "2026-10-01"
    assert any("1 row without a readable activity date" in m for m in out["missing"])
    assert out["summary"] == "7 dated rows, 3 stale (older than 18 months before 2026-11-08, 42.9 % of dated rows), 1 undated, 1 invalid email(s), 1 role address(es), 1 bounced; sendable 4–6 of 8."
    both = _run(tmp_path, text, op="customer_audit", stale_after_months=18, as_of="2026-11-08")
    assert both["duplicates"]["confirmed_clusters"] == [] and "no last_activity_column" in both["missing"][0]



def test_dispatch_routes_inline_price_response(tmp_path):
    import json as _json

    from rocky.tools import WorkspaceTools, dispatch, worker_tools

    out = _json.loads(dispatch(WorkspaceTools(tmp_path), "table_analyze", {"op": "price_response", "before": {"price": 20, "units": 1000}, "after": {"price": 18, "units": 1200}}))
    assert out["op"] == "price_response" if "op" in out else "elasticity" in _json.dumps(out)
    spec = next(t for t in worker_tools(False) if t["function"]["name"] == "table_analyze")
    assert spec["function"]["parameters"]["required"] == ["op"]
    assert "customer_audit" in spec["function"]["parameters"]["properties"]["op"]["enum"]
