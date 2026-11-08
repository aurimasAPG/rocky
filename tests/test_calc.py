import json

import pytest

from rocky.calc import unit_economics, unit_economics_tool
from rocky.engine import orchestrate
from rocky.llm import Completion, ToolCall
from rocky.tools import ToolError, WorkspaceTools, dispatch, worker_tools
from tests.test_loop import MODELS, ScriptedClient


def test_a_price_cut_says_how_many_extra_sales_it_needs():
    out = unit_economics({"price": 100, "variable_cost": 60, "units": 100, "new_price": 90})
    assert out["contribution_per_unit"] == 40 and out["margin_pct"] == 40.0
    assert out["total_contribution"] == 4000
    change = out["change"]
    assert change["new_contribution_per_unit"] == 30
    assert change["required_units"] == 134 and change["extra_units"] == 34
    assert change["required_uplift_pct"] == 33.3
    assert change["contribution_preserved"] == 4000
    assert "134 sales" in out["summary"] and "34 extra" in out["summary"]
    assert out["missing"] == []


def test_discount_pct_is_the_same_question():
    out = unit_economics({"price": "100", "variable_cost": "60", "units": "100", "discount_pct": "10"})
    assert out["change"]["required_units"] == 134


def test_an_expected_uplift_turns_the_numbers_into_a_decision():
    out = unit_economics({"price": 100, "variable_cost": 60, "units": 100, "new_price": 90, "expected_uplift_pct": 20})
    assert out["change"]["decision"] == "change loses contribution"
    assert out["change"]["contribution_delta"] == -400
    assert out["sensitivity"]["decision_flips_at_uplift_pct"] == 33.3
    # at a 20 % uplift the cut only pays if the unit cost is at most 40: (100-40) = 1.2 * (90-40)
    assert out["sensitivity"]["decision_flips_at_variable_cost"] == 40
    better = unit_economics({"price": 100, "variable_cost": 60, "units": 100, "new_price": 90, "expected_uplift_pct": 40})
    assert better["change"]["decision"] == "change pays"


def test_sensitivity_shows_the_unit_cost_assumption_moving():
    out = unit_economics({"price": 100, "variable_cost": 60, "units": 100, "new_price": 90})
    rows = out["sensitivity"]["variable_cost"]
    assert [r["variable_cost"] for r in rows] == [54, 60, 66]
    assert rows[0]["required_uplift_pct"] < rows[1]["required_uplift_pct"] < rows[2]["required_uplift_pct"]


def test_break_even_with_fixed_costs():
    out = unit_economics({"price": 25, "variable_cost": 10, "fixed_costs": 3000, "units": 250})
    assert out["break_even_units"] == 200 and out["break_even_revenue"] == 5000
    assert out["profit"] == 750 and out["safety_margin_pct"] == 20.0
    assert "Break-even at 200 units" in out["summary"]


def test_missing_variable_cost_is_named_and_answered_conditionally():
    out = unit_economics({"price": 100, "units": 100, "new_price": 90})
    assert out["missing"] == ["variable_cost"]
    assert "change" not in out
    rows = {r["assumed_margin_pct"]: r for r in out["conditional"]}
    assert set(rows) == {20.0, 40.0, 60.0, 80.0}
    assert rows[40.0]["change"]["required_units"] == 134
    assert rows[20.0]["change"]["required_units"] == 200
    assert "Ask for the unit cost" in out["summary"]


def test_without_volume_the_uplift_is_still_known():
    out = unit_economics({"price": 100, "variable_cost": 60, "new_price": 90, "expected_uplift_pct": 30})
    assert out["change"]["required_uplift_pct"] == 33.3
    assert "required_units" not in out["change"]
    assert out["change"]["decision"] == "change loses contribution"


def test_a_cut_below_cost_cannot_be_recovered():
    out = unit_economics({"price": 100, "variable_cost": 60, "units": 100, "new_price": 55})
    assert out["change"]["required_units"] is None
    assert "No volume recovers" in out["change"]["note"]


def test_bad_inputs_are_refused_in_plain_words():
    with pytest.raises(ToolError, match="price is required"):
        unit_economics({})
    with pytest.raises(ToolError, match="must be a number"):
        unit_economics({"price": "a hundred"})
    with pytest.raises(ToolError, match="discount_pct"):
        unit_economics({"price": 100, "variable_cost": 60, "discount_pct": 120})
    assert "loses money" in unit_economics({"price": 10, "variable_cost": 12})["warnings"][0]


def test_dispatch_routes_the_tool_and_returns_json():
    out = dispatch(WorkspaceTools("."), "unit_economics", {"price": 100, "variable_cost": 60, "units": 100, "new_price": 90})
    assert json.loads(out)["change"]["required_units"] == 134
    assert "unit_economics" in [t["function"]["name"] for t in worker_tools(False)]


class PricingClient(ScriptedClient):
    """Worker that answers a pricing question only from what the calculator returns."""

    def __init__(self, ask_missing=False):
        super().__init__()
        self.ask_missing = ask_missing
        self.tool_args = None
        self.clarified = None

    def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
        system = messages[0]["content"] if messages else ""
        if "You assess tasks" in system:
            return Completion(
                content=json.dumps(
                    {
                        "goal": "Extra sales needed after a price cut",
                        "complexity": "trivial",
                        "domain": "marketing",
                        "success_criteria": ["answer.md states the extra units"],
                        "needs_web": False,
                        "deliverable": "answer.md",
                    }
                )
            )
        if "You execute this subtask" in system:
            self.worker_round += 1
            last = messages[-1]
            if last.get("role") == "tool":
                if last["content"].startswith("wrote "):
                    return Completion(content="done")
                payload = json.loads(last["content"]) if last["content"].startswith("{") else {}
                if payload.get("missing") and self.ask_missing and self.clarified is None:
                    self.clarified = "pending"
                    return Completion(
                        content="",
                        tool_calls=[
                            ToolCall(id="q", name="clarify", arguments={"question": "What is the variable cost per unit?"})
                        ],
                    )
                if last["content"].startswith("user:"):
                    self.clarified = last["content"]
                    return Completion(content="", tool_calls=[ToolCall(id="c2", name="unit_economics", arguments={"price": 100, "variable_cost": 60, "units": 100, "new_price": 90})])
                change = payload.get("change") or {}
                text = f"{change.get('extra_units')} extra sales, {change.get('required_units')} total, keeping {change.get('contribution_preserved')} EUR."
                return Completion(
                    content="",
                    tool_calls=[ToolCall(id="w", name="write_file", arguments={"path": "deliverables/answer.md", "content": text + "\n"})],
                )
            if self.worker_round == 1:
                args = {"price": 100, "units": 100, "new_price": 90}
                if not self.ask_missing:
                    args["variable_cost"] = 60
                self.tool_args = args
                return Completion(content="", tool_calls=[ToolCall(id="c1", name="unit_economics", arguments=args)])
            return Completion(content="done")
        return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)


def test_a_worker_answers_the_pricing_brief_from_the_calculator(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    events = []
    client = PricingClient()
    result = orchestrate(
        goal="We sell 100 units at €100 each. Variable cost is €60 per unit. If we cut the price to €90, how many extra sales do we need?",
        workspace=tmp_path,
        client=client,
        models=MODELS,
        emit=events.append,
        max_loops=1,
        budget_eur=1.0,
        eur_rate=0.92,
    )
    assert result.passed is True
    calls = [e.payload for e in events if e.type == "tool_call" and e.payload["name"] == "unit_economics"]
    assert calls and calls[0]["arguments"] == {"price": "100", "units": "100", "new_price": "90", "variable_cost": "60"}
    # the figures in the deliverable are the tool's, not the model's
    assert (tmp_path / "deliverables" / "answer.md").read_text() == "34 extra sales, 134 total, keeping 4000.0 EUR.\n"


def test_a_worker_asks_for_the_missing_unit_cost_before_answering(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    events = []
    client = PricingClient(ask_missing=True)
    questions = []

    def ask_user(question, options=None):
        questions.append(question)
        return "60"

    result = orchestrate(
        goal="We sell 100 units at €100 each. If we cut the price to €90, how many extra sales do we need?",
        workspace=tmp_path,
        client=client,
        models=MODELS,
        emit=events.append,
        max_loops=1,
        budget_eur=1.0,
        eur_rate=0.92,
        ask_user=ask_user,
    )
    assert result.passed is True
    names = [e.payload["name"] for e in events if e.type == "tool_call"]
    assert names == ["unit_economics", "clarify", "unit_economics", "write_file"]
    assert questions == ["What is the variable cost per unit?"]
    first = next(e.payload["result"] for e in events if e.type == "tool_result" and e.payload["name"] == "unit_economics")
    assert '"missing": [\n  "variable_cost"' in first or '"variable_cost"' in json.loads(first)["missing"]
    assert (tmp_path / "deliverables" / "answer.md").read_text().startswith("34 extra sales, 134 total")


def test_the_worker_prompt_sends_arithmetic_to_the_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    seen = []

    class Recording(ScriptedClient):
        def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
            seen.append(messages[0]["content"])
            return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)

    orchestrate(goal="Write a short article", workspace=tmp_path, client=Recording(), models=MODELS, max_loops=1, budget_eur=1.0, eur_rate=0.92)
    worker = next(s for s in seen if "You execute this subtask" in s)
    assert "unit_economics" in worker and "never do that arithmetic in prose" in worker
