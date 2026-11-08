import json

import pytest

from rocky.calc import experiment_plan
from rocky.engine import orchestrate
from rocky.llm import Completion, ToolCall
from rocky.tools import ToolError, WorkspaceTools, dispatch, worker_tools
from tests.test_loop import MODELS, ScriptedClient


def test_a_ten_percent_lift_on_a_three_percent_rate_needs_about_53k_per_variant():
    out = experiment_plan({"baseline_rate": 3, "minimum_effect": 10, "weekly_traffic": 10000})
    assert 52_500 <= out["sample_per_variant"] <= 54_000
    assert out["total_sample"] == out["sample_per_variant"] * 2
    assert out["weeks"] == 11 and out["days"] == 77
    assert out["inputs"]["target_rate_pct"] == 3.3
    assert "do not stop early" in out["decision_rule"] and "0.3 percentage points" in out["decision_rule"]
    assert out["missing"] == []


def test_absolute_effect_and_one_sided_are_smaller_tests():
    relative = experiment_plan({"baseline_rate": 3, "minimum_effect": 10})
    absolute = experiment_plan({"baseline_rate": 3, "minimum_effect": 1, "effect_kind": "absolute"})
    one_sided = experiment_plan({"baseline_rate": 3, "minimum_effect": 10, "two_sided": False})
    assert absolute["inputs"]["target_rate_pct"] == 4.0
    assert absolute["sample_per_variant"] < relative["sample_per_variant"]
    assert one_sided["sample_per_variant"] < relative["sample_per_variant"]


def test_more_variants_split_alpha_and_raise_the_sample():
    two = experiment_plan({"baseline_rate": 5, "minimum_effect": 15})
    four = experiment_plan({"baseline_rate": 5, "minimum_effect": 15, "variants": 4})
    assert four["inputs"]["alpha_per_comparison"] == pytest.approx(0.05 / 3, abs=1e-4)
    assert four["sample_per_variant"] > two["sample_per_variant"]
    assert four["total_sample"] == four["sample_per_variant"] * 4
    assert any("alpha is split" in w for w in four["warnings"])


def test_missing_traffic_means_unknown_duration_not_a_guess():
    out = experiment_plan({"baseline_rate": 3, "minimum_effect": 10})
    assert out["missing"] == ["weekly_traffic"] and out["weeks"] is None
    assert "ask for it" in out["summary"]


def test_max_weeks_says_what_is_detectable_in_the_time_available():
    out = experiment_plan({"baseline_rate": 3, "minimum_effect": 10, "weekly_traffic": 10000, "max_weeks": 4})
    assert out["feasible_in_max_weeks"] is False
    det = out["detectable_in_max_weeks"]
    assert det["per_variant"] == 20000
    assert det["relative_effect_pct"] > 10
    # the detectable lift really is detectable with that sample
    check = experiment_plan({"baseline_rate": 3, "minimum_effect": det["relative_effect_pct"], "weekly_traffic": 10000})
    assert check["sample_per_variant"] <= 20000 * 1.01
    fine = experiment_plan({"baseline_rate": 3, "minimum_effect": 10, "weekly_traffic": 10000, "max_weeks": 12})
    assert fine["feasible_in_max_weeks"] is True and "detectable_in_max_weeks" not in fine


def test_bad_inputs_are_refused():
    with pytest.raises(ToolError, match="baseline_rate"):
        experiment_plan({"minimum_effect": 10})
    with pytest.raises(ToolError, match="percentage between"):
        experiment_plan({"baseline_rate": 300, "minimum_effect": 10})
    with pytest.raises(ToolError, match="outside"):
        experiment_plan({"baseline_rate": 90, "minimum_effect": 20})
    with pytest.raises(ToolError, match="effect_kind"):
        experiment_plan({"baseline_rate": 3, "minimum_effect": 10, "effect_kind": "huge"})


def test_dispatch_and_registry():
    out = json.loads(dispatch(WorkspaceTools("."), "experiment_plan", {"baseline_rate": 3, "minimum_effect": 10, "weekly_traffic": 10000}))
    assert out["weeks"] == 11
    assert "experiment_plan" in [t["function"]["name"] for t in worker_tools(False)]


class PlannerClient(ScriptedClient):
    """Worker that writes a test plan only from the planner's numbers."""

    def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
        system = messages[0]["content"] if messages else ""
        if "You assess tasks" in system:
            return Completion(
                content=json.dumps(
                    {
                        "goal": "Plan the checkout test",
                        "complexity": "trivial",
                        "domain": "marketing",
                        "success_criteria": ["plan.md gives sample and weeks"],
                        "needs_web": False,
                        "deliverable": "plan.md",
                    }
                )
            )
        if "You execute this subtask" in system:
            self.worker_round += 1
            last = messages[-1]
            if last.get("role") == "tool" and last["content"].startswith("{"):
                plan = json.loads(last["content"])
                text = f"{plan['sample_per_variant']} per variant, {plan['weeks']} weeks. {plan['decision_rule']}"
                return Completion(content="", tool_calls=[ToolCall(id="w", name="write_file", arguments={"path": "deliverables/plan.md", "content": text})])
            if last.get("role") == "tool":
                return Completion(content="done")
            return Completion(
                content="",
                tool_calls=[ToolCall(id="p", name="experiment_plan", arguments={"baseline_rate": 3, "minimum_effect": 10, "weekly_traffic": 10000, "metric": "checkout conversion"})],
            )
        return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)


def test_a_worker_plans_a_test_from_the_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    events = []
    result = orchestrate(
        goal="Checkout converts at 3 %. We get 10,000 checkouts a week. Plan a test to detect a 10 % lift.",
        workspace=tmp_path,
        client=PlannerClient(),
        models=MODELS,
        emit=events.append,
        max_loops=1,
        budget_eur=1.0,
        eur_rate=0.92,
    )
    assert result.passed is True
    call = next(e.payload for e in events if e.type == "tool_call" and e.payload["name"] == "experiment_plan")
    assert call["arguments"]["weekly_traffic"] == "10000"
    text = (tmp_path / "deliverables" / "plan.md").read_text()
    assert text.startswith("53") and "per variant, 11 weeks" in text and "checkout conversion" in text
