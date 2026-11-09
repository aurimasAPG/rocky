import json

import pytest

from rocky.brief import (
    ALLOWED_FIELDS,
    BLOCK_CAP,
    MISSING_FOR,
    brief,
    brief_block,
    brief_path,
    brief_tool,
    defaults_for,
    load_brief,
)
from rocky.tools import ToolError, WorkspaceTools


def _tools(tmp_path):
    return WorkspaceTools(tmp_path)


def _set(tools, fields, **extra):
    return brief(tools, {"action": "set", "fields": fields, **extra})


FULL_DECISION = {
    "internal": {"kind": "strength", "text": "Own delivery fleet covers Vilnius in 2 hours", "source": "ops report 2026-09"},
    "external": {"kind": "opportunity", "text": "Competitor X dropped same-day delivery in October", "source": "https://example.com/news"},
    "investment": {"what": "Same-day delivery landing page and 4 weeks of Meta ads", "amount": 1500, "currency": "EUR"},
    "would_change_mind": "Same-day orders below 40 per week after 4 weeks, or competitor restores the service",
}


# --- get / skeleton ------------------------------------------------------------


def test_first_get_returns_skeleton_with_missing_for_lists(tmp_path):
    out = brief(_tools(tmp_path), {"action": "get"})
    assert out["fields"] == {}
    assert out["decisions"] == []
    assert out["assumed"] == []
    assert set(out["missing_for"]) == {"ads", "pricing", "test", "media", "research"}
    assert out["missing_for"]["ads"] == ["brand", "url", "product", "audience", "offer", "language"]
    assert out["missing_for"]["pricing"] == ["price", "variable_cost|margin_pct", "units_per_period"]
    assert "Still unknown for" in out["summary"]
    assert not brief_path(tmp_path).exists()  # a read creates nothing


def test_default_action_is_get_and_every_kind_is_documented(tmp_path):
    out = brief(_tools(tmp_path), {})
    assert out["action"] == "get"
    for kind, needs in MISSING_FOR.items():
        for item in needs:
            for name in (item if isinstance(item, tuple) else (item,)):
                assert name in ALLOWED_FIELDS, f"{kind} needs unknown field {name}"


# --- set: validation and normalisation -----------------------------------------


def test_set_normalises_price_and_currency(tmp_path):
    tools = _tools(tmp_path)
    out = _set(tools, {"price": "49,90 €"}, source="user")
    assert out["fields"]["price"]["value"] == {"amount": 49.9, "currency": "EUR"}
    out = _set(tools, {"price": 120, "currency": "usd"}, source="user")
    assert out["fields"]["price"]["value"] == {"amount": 120.0, "currency": "USD"}
    assert "currency" not in out["fields"]
    out = _set(tools, {"price": {"amount": "35", "currency": "gbp"}}, source="user")
    assert out["fields"]["price"]["value"] == {"amount": 35.0, "currency": "GBP"}
    with pytest.raises(ToolError, match="greater than zero"):
        _set(tools, {"price": 0})
    with pytest.raises(ToolError, match="number"):
        _set(tools, {"price": "cheap"})


def test_set_validates_url_deadline_language(tmp_path):
    tools = _tools(tmp_path)
    out = _set(tools, {"url": "https://shop.lt/akcija", "deadline": "2026-12-01T10:00:00", "language": "LT"}, source="user")
    assert out["fields"]["url"]["value"] == "https://shop.lt/akcija"
    assert out["fields"]["deadline"]["value"] == "2026-12-01"
    assert out["fields"]["language"]["value"] == "lt"
    assert _set(tools, {"language": "English"}, source="user")["fields"]["language"]["value"] == "en"
    with pytest.raises(ToolError, match="http"):
        _set(tools, {"url": "shop.lt"})
    with pytest.raises(ToolError, match="ISO date"):
        _set(tools, {"deadline": "1 December"})
    with pytest.raises(ToolError, match="lt or en"):
        _set(tools, {"language": "de"})


def test_set_normalises_kpi_budget_units_and_lists(tmp_path):
    tools = _tools(tmp_path)
    out = _set(
        tools,
        {
            "kpi": {"name": "conversion rate", "baseline": "3.2%", "target": 4},
            "budget": {"amount": "500", "period": "mėn"},
            "units_per_period": 120,
            "period": "monthly",
            "channels": ["Meta", "Google Search", "Meta"],
            "constraints": "no discounts above 20 %",
            "competitors": ["Pigu.lt"],
            "margin_pct": "55",
            "audience_size": "120000",
        },
        source="user",
    )
    f = out["fields"]
    assert f["kpi"]["value"] == {"name": "conversion rate", "unit": "%", "baseline": 3.2, "target": 4.0}
    assert f["budget"]["value"] == {"amount": 500.0, "currency": "EUR", "period": "month"}
    assert f["units_per_period"]["value"] == 120.0 and f["period"]["value"] == "month"
    assert f["channels"]["value"] == ["Meta", "Google Search"]
    assert f["constraints"]["value"] == ["no discounts above 20 %"]
    assert f["margin_pct"]["value"] == 55.0
    assert f["audience_size"]["value"] == 120000
    assert out["missing_for"]["pricing"] == ["price"]
    with pytest.raises(ToolError, match="needs a period"):
        _set(_tools(tmp_path / "other"), {"units_per_period": 10})
    with pytest.raises(ToolError, match="kpi has unknown keys"):
        _set(tools, {"kpi": {"name": "x", "goal": 1}})
    with pytest.raises(ToolError, match="between 0 and 100"):
        _set(tools, {"margin_pct": 140})


def test_unknown_field_lists_allowed_ones(tmp_path):
    with pytest.raises(ToolError) as exc:
        _set(_tools(tmp_path), {"brand": "Acme", "colour": "red"})
    message = str(exc.value)
    assert "colour" in message
    assert "allowed:" in message and "audience" in message and "kpi" in message
    assert not brief_path(tmp_path).exists()  # nothing was written


def test_set_without_fields_is_an_error(tmp_path):
    with pytest.raises(ToolError, match="set needs fields"):
        brief(_tools(tmp_path), {"action": "set"})
    with pytest.raises(ToolError, match="action must be"):
        brief(_tools(tmp_path), {"action": "dance"})


def test_demographic_only_audience_warns(tmp_path):
    tools = _tools(tmp_path)
    out = _set(tools, {"audience": "moterys 25–45 m. Vilniuje"}, source="user")
    codes = [f["code"] for f in out["findings"]]
    assert "audience_demographic_only" in codes
    out = _set(tools, {"audience": "Women 25–45 who want to stop paying for a gym they never visit"}, source="user")
    assert "audience_demographic_only" not in [f["code"] for f in out["findings"]]
    out = _set(tools, {"audience": "Mamos, kurioms sunku rasti laiko sportui"}, source="user")
    assert out["findings"] == []


# --- provenance -------------------------------------------------------------------


def test_worker_set_without_evidence_is_assumed(tmp_path):
    tools = _tools(tmp_path)
    out = _set(tools, {"offer": "free delivery over 50 EUR"})
    assert out["fields"]["offer"]["source"] == "worker"
    assert out["fields"]["offer"]["assumed"] is True
    assert out["assumed"] == ["offer"]
    assert any(f["code"] == "assumed" and f["field"] == "offer" for f in out["findings"])
    assert out["fields"]["offer"]["set_at"] == "manual"


def test_user_or_evidenced_field_is_not_assumed(tmp_path):
    tools = _tools(tmp_path)
    (tmp_path / "runs" / "notes").mkdir(parents=True)
    (tmp_path / "runs" / "notes" / "facts.md").write_text("price 49 EUR\n", encoding="utf-8")
    out = _set(tools, {"price": 49}, source="user", run_id="run-7")
    assert out["fields"]["price"]["assumed"] is False
    assert out["fields"]["price"]["set_at"] == "run-7"
    out = _set(tools, {"brand": "Acme"}, evidence="https://acme.lt/about")
    assert out["fields"]["brand"]["evidence"] == "https://acme.lt/about"
    assert out["fields"]["brand"]["assumed"] is False
    out = _set(tools, {"product": "Robot vacuum"}, source="file", evidence="runs/notes/facts.md")
    assert out["fields"]["product"]["evidence"] == "runs/notes/facts.md"
    assert out["assumed"] == []
    with pytest.raises(ToolError, match="evidence must be a URL or a file"):
        _set(tools, {"market": "LT"}, evidence="I just know")
    with pytest.raises(ToolError, match="escapes workspace"):
        _set(tools, {"market": "LT"}, evidence="../../etc/passwd")
    with pytest.raises(ToolError, match="source must be"):
        _set(tools, {"market": "LT"}, source="guess")


def test_per_field_evidence_dict(tmp_path):
    out = _set(_tools(tmp_path), {"brand": "Acme", "offer": "-20 %"}, evidence={"brand": "https://acme.lt"})
    assert out["fields"]["brand"]["assumed"] is False
    assert out["fields"]["offer"]["assumed"] is True
    assert out["assumed"] == ["offer"]


# --- decisions ------------------------------------------------------------------


def test_decision_with_all_parts_is_recorded_without_findings(tmp_path):
    tools = _tools(tmp_path)
    out = brief(tools, {"action": "decision", "now": "2026-11-08T10:00:00Z", "run_id": "run-3", **FULL_DECISION})
    assert out["findings"] == []
    dec = out["decision"]
    assert dec["id"] == "d1" and dec["at"] == "2026-11-08T10:00:00Z" and dec["run_id"] == "run-3"
    assert dec["investment"] == {"what": FULL_DECISION["investment"]["what"], "amount": 1500.0, "currency": "EUR"}
    assert dec["internal"]["kind"] == "strength" and dec["external"]["kind"] == "opportunity"
    assert dec["gaps"] == []
    assert out["decisions"] == [dec]
    second = brief(tools, {"action": "decision", "now": "2026-11-09T10:00:00Z", **FULL_DECISION})
    assert second["decision"]["id"] == "d2"
    assert len(load_brief(tmp_path)["decisions"]) == 2


def test_decision_missing_or_generic_part_is_a_finding(tmp_path):
    tools = _tools(tmp_path)
    out = brief(
        tools,
        {
            "action": "decision",
            "now": "2026-11-08T10:00:00Z",
            "internal": {"kind": "strength", "text": "strong team", "source": "me"},
            "external": {"kind": "threat", "text": "Konkurencija"},
            "investment": {"what": "Same-day delivery landing page and 4 weeks of Meta ads"},
        },
    )
    codes = {(f["code"], f["field"]) for f in out["findings"]}
    assert ("decision_generic", "internal") in codes
    assert ("decision_generic", "external") in codes
    assert ("decision_no_source", "external") in codes
    assert ("decision_no_amount", "investment") in codes
    assert ("decision_missing_part", "would_change_mind") in codes
    assert out["decision"]["gaps"] == ["external", "internal", "investment", "would_change_mind"]
    assert out["decision"]["status"] == "open"
    with pytest.raises(ToolError, match="kind must be"):
        brief(tools, {"action": "decision", "internal": {"kind": "chance", "text": "x" * 20, "source": "y"}})
    with pytest.raises(ToolError, match="decision needs"):
        brief(tools, {"action": "decision"})


def test_decision_default_timestamp_is_utc_iso(tmp_path):
    out = brief(_tools(tmp_path), {"action": "decision", **FULL_DECISION})
    assert out["decision"]["at"].endswith("Z") and out["decision"]["at"][:2] == "20"


# --- block ----------------------------------------------------------------------


def test_block_renders_fields_sources_assumed_flag_decisions_and_unknowns(tmp_path):
    tools = _tools(tmp_path)
    _set(tools, {"brand": "Žalia Giria", "price": "49 €", "audience": "Mamos, kurioms sunku rasti laiko sportui", "units_per_period": 120, "period": "week"}, source="user")
    _set(tools, {"offer": "nemokamas pristatymas"})
    brief(tools, {"action": "decision", "now": "2026-11-08T10:00:00Z", **FULL_DECISION})
    block = brief_block(tmp_path)
    assert block.splitlines()[1] == "BRIEF (this workspace) [6 fields, 1 assumed]"
    assert "- brand: Žalia Giria (user)" in block
    assert "- price: 49 EUR (user)" in block
    assert "- units_per_period: 120 / week (user)" in block
    assert "- offer: nemokamas pristatymas (worker, assumed" in block
    assert "Decisions:" in block and "d1 2026-11-08: Same-day delivery landing page" in block
    assert "reversed by: Same-day orders below 40" in block
    assert "Still unknown for ads: url, product, language" in block
    assert "Still unknown for pricing: variable_cost|margin_pct" in block
    assert "Still unknown for media:" in block
    assert brief_tool(tools, {"action": "block"}) == block
    assert brief(tools, {"action": "block"})["block"] == block


def test_block_is_neutral_and_shows_empty_hint(tmp_path):
    block = brief_block(tmp_path)
    assert "BRIEF (this workspace) [0 fields, 0 assumed]" in block
    assert "(empty" in block
    assert "Still unknown for ads:" in block
    for word in ("please", "prašome", "jūs", "you should"):
        assert word not in block.lower()


def test_block_is_capped_at_2000_chars_with_note(tmp_path):
    tools = _tools(tmp_path)
    long = "a long value that keeps going so the block overflows " * 6
    many = [f"item {i} that is quite long to fill the block" for i in range(40)]
    _set(
        tools,
        {"brand": long, "product": long, "audience": long, "offer": long, "channel": long, "market": long, "source_notes": long, "channels": many, "constraints": many, "competitors": many},
        source="user",
    )
    for i in range(5):
        brief(tools, {"action": "decision", "now": f"2026-11-0{i + 1}T10:00:00Z", **FULL_DECISION})
    block = brief_block(tmp_path)
    assert len(block) <= BLOCK_CAP
    assert block.endswith(f"… (clipped at {BLOCK_CAP} chars; brief get has the rest)")


# --- clear ------------------------------------------------------------------------


def test_clear_fields_or_everything(tmp_path):
    tools = _tools(tmp_path)
    _set(tools, {"brand": "Acme", "price": 10, "market": "LT"}, source="user")
    brief(tools, {"action": "decision", **FULL_DECISION})
    out = brief(tools, {"action": "clear", "fields": ["price", "deadline"]})
    assert set(out["fields"]) == {"brand", "market"}
    assert "Cleared price." in out["summary"]
    assert len(out["decisions"]) == 1
    out = brief(tools, {"action": "clear"})
    assert out["fields"] == {} and out["decisions"] == []
    assert load_brief(tmp_path) == {"version": 1, "fields": {}, "decisions": []}
    with pytest.raises(ToolError, match="unknown field"):
        brief(tools, {"action": "clear", "fields": ["colour"]})


# --- defaults_for -------------------------------------------------------------


def test_defaults_for_maps_brief_values_to_tool_inputs(tmp_path):
    tools = _tools(tmp_path)
    _set(
        tools,
        {
            "brand": "Acme",
            "url": "https://acme.lt/offer",
            "price": {"amount": 49, "currency": "USD"},
            "variable_cost": 20,
            "units_per_period": 300,
            "period": "week",
            "kpi": {"name": "conversion rate", "baseline": 3.2, "unit": "%"},
            "audience_size": 250000,
            "budget": {"amount": 2000, "period": "month"},
            "language": "lt",
        },
        source="user",
    )
    assert defaults_for(tmp_path, "unit_economics") == {"price": 49.0, "currency": "USD", "variable_cost": 20.0, "units": 300.0}
    assert defaults_for(tmp_path, "experiment_plan") == {"baseline_rate": 3.2, "metric": "conversion rate", "weekly_traffic": 300.0}
    assert defaults_for(tmp_path, "reach_overlap") == {"audience": 250000, "budget": 2000.0}
    assert defaults_for(tmp_path, "ad_board") == {"brand": "Acme", "url": "https://acme.lt/offer"}
    assert defaults_for(tmp_path, "page_inspect") == {"url": "https://acme.lt/offer", "language": "lt"}
    assert defaults_for(tmp_path, "web_search") == {}


def test_defaults_for_omits_unset_and_non_percent_baseline(tmp_path):
    tools = _tools(tmp_path)
    assert defaults_for(tmp_path, "unit_economics") == {}
    _set(tools, {"price": 49, "kpi": {"name": "orders", "baseline": 120, "unit": "orders"}, "units_per_period": 300, "period": "month"}, source="user")
    assert defaults_for(tmp_path, "unit_economics") == {"price": 49.0, "currency": "EUR", "units": 300.0}
    assert defaults_for(tmp_path, "experiment_plan") == {"metric": "orders"}  # baseline is not a rate, monthly units are not weekly traffic
    assert defaults_for(tmp_path, "reach_overlap") == {}


# --- storage ------------------------------------------------------------------------


def test_file_is_written_atomically_and_survives_reload(tmp_path):
    tools = _tools(tmp_path)
    _set(tools, {"brand": "Acme", "price": 49}, source="user", run_id="run-1")
    path = brief_path(tmp_path)
    assert path == tmp_path.resolve() / ".rocky" / "brief.json"
    assert path.is_file()
    assert not path.with_suffix(".json.tmp").exists()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["fields"]["brand"] == {"value": "Acme", "source": "user", "set_at": "run-1"}
    again = brief(WorkspaceTools(tmp_path), {"action": "get"})
    assert again["fields"]["price"]["value"] == {"amount": 49.0, "currency": "EUR"}
    assert again["fields"]["brand"]["assumed"] is False


def test_corrupt_or_foreign_file_falls_back_to_skeleton(tmp_path):
    path = brief_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert load_brief(tmp_path) == {"version": 1, "fields": {}, "decisions": []}
    path.write_text(json.dumps({"fields": {"brand": {"value": "A", "source": "user", "set_at": "x"}, "colour": {"value": "red"}, "price": "49"}}), encoding="utf-8")
    assert set(load_brief(tmp_path)["fields"]) == {"brand"}


def test_file_path_is_fixed_inside_workspace(tmp_path):
    tools = _tools(tmp_path)
    outside = tmp_path.parent / "elsewhere.json"
    out = brief(tools, {"action": "set", "fields": {"brand": "Acme"}, "path": str(outside), "file": "../x.json", "target": "/etc/brief.json"}, )
    assert out["path"] == ".rocky/brief.json"
    assert brief_path(tmp_path).is_file()
    assert not outside.exists()
    written = {p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_file()}
    assert {str(p) for p in written} == {".rocky/brief.json"}


def test_tool_wrapper_returns_json_with_summary(tmp_path):
    text = brief_tool(_tools(tmp_path), {"action": "set", "fields": {"brand": "Acme"}, "source": "user"})
    data = json.loads(text)
    assert data["action"] == "set"
    assert data["summary"].startswith("Set brand. Brief has 1 field (0 assumed) and 0 decisions.")
    assert "missing_for" in data and "assumed" in data and "findings" in data


def test_variable_cost_above_price_is_a_finding(tmp_path):
    tools = _tools(tmp_path)
    _set(tools, {"price": 10}, source="user")
    out = _set(tools, {"variable_cost": 12}, source="user")
    assert [f["code"] for f in out["findings"]] == ["cost_not_below_price"]
    assert out["missing_for"]["pricing"] == ["units_per_period"]



def test_tools_default_their_missing_arguments_from_the_brief_and_roles_see_the_block(tmp_path, monkeypatch):
    import json as _json

    from rocky.engine import orchestrate
    from rocky.tools import WorkspaceTools, dispatch
    from tests.test_loop import MODELS, ScriptedClient

    tools = WorkspaceTools(tmp_path)
    dispatch(tools, "brief", {"action": "set", "source": "user", "fields": {"price": 100, "variable_cost": 60, "units_per_period": 100, "period": "month", "brand": "X", "url": "https://x.lt"}})
    out = _json.loads(dispatch(tools, "unit_economics", {"new_price": 90}))
    assert out["change"]["required_units"] == 134, "price, cost and units came from the brief"
    explicit = _json.loads(dispatch(tools, "unit_economics", {"price": 200, "variable_cost": 60, "units": 100, "new_price": 180}))
    assert explicit["inputs"]["price"] == 200, "an argument the worker gives wins over the brief"

    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    seen = []

    class Recording(ScriptedClient):
        def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
            seen.append(messages[0]["content"])
            return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)

    orchestrate(goal="Write a short article", workspace=tmp_path, client=Recording(), models=MODELS, max_loops=1, budget_eur=1.0, eur_rate=0.92)
    worker = next(s for s in seen if "You execute this subtask" in s)
    assert "BRIEF" in worker and "https://x.lt" in worker
    assert "call brief (action=get)" in worker
