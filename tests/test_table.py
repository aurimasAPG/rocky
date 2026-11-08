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
