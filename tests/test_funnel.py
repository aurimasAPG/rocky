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
