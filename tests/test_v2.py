import json

from rocky.budget import BudgetedClient
from rocky.engine import _steps_reused_done, orchestrate
from rocky.llm import Completion, ToolCall
from rocky.tools import WorkspaceTools
from tests.test_loop import MODELS, ScriptedClient, TrivialClient


class RecordingClient:
    def __init__(self):
        self.calls = []

    def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
        self.calls.append({"model": model, "extra": extra or {}})
        return Completion(content="ok", model=model)


def test_preflight_bounds_output_to_remaining_budget():
    events = []
    delegate = RecordingClient()
    client = BudgetedClient(delegate, MODELS, lambda: 0.001, lambda etype, **payload: events.append((etype, payload)))
    result = client.complete(model="google/flash", messages=[{"role": "user", "content": "short"}])
    assert result.content == "ok"
    assert 0 < delegate.calls[0]["extra"]["max_tokens"] <= 4096
    assert client.blocked is False


def test_preflight_blocks_before_an_unaffordable_call():
    events = []
    delegate = RecordingClient()
    client = BudgetedClient(delegate, MODELS, lambda: 0.0, lambda etype, **payload: events.append((etype, payload)))
    result = client.complete(model="google/flash", messages=[{"role": "user", "content": "short"}])
    assert result.raw["budget_blocked"] is True
    assert delegate.calls == []
    assert client.blocked is True
    assert events[0][0] == "budget" and events[0][1]["stage"] == "preflight"
    client.complete(model="google/flash", messages=[{"role": "user", "content": "again"}])
    assert delegate.calls == []


def test_run_stops_cleanly_when_assessment_is_unaffordable(tmp_path):
    events = []
    client = ScriptedClient()
    result = orchestrate(
        goal="Write a short article",
        workspace=tmp_path,
        client=client,
        models=MODELS,
        emit=events.append,
        budget=0.000001,
        eur_rate=1.0,
    )
    assert result.passed is False
    assert any(event.type == "budget" and event.payload.get("stage") == "preflight" for event in events)
    assert not any(event.type == "subtask_start" for event in events)


def test_completed_steps_are_reused_only_when_their_files_survive(tmp_path):
    tools = WorkspaceTools(tmp_path)
    (tmp_path / "notes.md").write_text("verified notes", encoding="utf-8")
    previous = {
        "steps": [
            {"id": 1, "title": "Research", "writes": ["notes.md"], "depends_on": []},
            {"id": 2, "title": "Draft", "writes": ["article.md"], "depends_on": [1]},
        ]
    }
    new = {
        "steps": [
            {"id": 1, "title": "Research", "writes": ["notes.md"], "depends_on": []},
            {"id": 2, "title": "Draft", "writes": ["article.md"], "depends_on": [1]},
            {"id": 3, "title": "Repair", "writes": ["repair.md"], "depends_on": [2]},
        ]
    }
    assert _steps_reused_done(previous, new, {1, 2}, tools) == {1}
    (tmp_path / "article.md").write_text("verified article", encoding="utf-8")
    assert _steps_reused_done(previous, new, {1, 2}, tools) == {1, 2}
    new["steps"][0]["writes"] = ["renamed-notes.md"]
    assert _steps_reused_done(previous, new, {1, 2}, tools) == {2}
    new["steps"][0]["writes"] = ["notes.md"]
    (tmp_path / "notes.md").unlink()
    assert _steps_reused_done(previous, new, {1, 2}, tools) == {2}


class ReplanClient(ScriptedClient):
    def __init__(self):
        super().__init__()
        self.plan_round = 0
        self.verify_round = 0
        self.worker_titles = []
        self.completed_payload = None
        self.written = set()

    def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
        system = messages[0]["content"] if messages else ""
        if "You write the plan" in system:
            self.plan_round += 1
            payload = json.loads(messages[-1]["content"])
            if self.plan_round == 2:
                self.completed_payload = payload["completed_steps_to_preserve"]
            steps = [
                {"id": 1, "title": "Research", "detail": "notes", "depends_on": [], "writes": ["deliverables/notes.md"]},
                {"id": 2, "title": "Draft", "detail": "draft", "depends_on": [1], "writes": ["deliverables/article.md"]},
            ]
            if self.plan_round > 1:
                steps.append({"id": 3, "title": "Repair", "detail": "repair", "depends_on": [2], "writes": ["deliverables/repair.md"]})
            return Completion(content=json.dumps({"summary": "work", "steps": steps, "deliverable_path": "deliverables/article.md", "notes": ""}))
        if "You execute this subtask" in system:
            task = json.loads(messages[1]["content"])["subtask"]
            title = task["title"]
            if title in self.written:
                return Completion(content="done")
            self.written.add(title)
            self.worker_titles.append(title)
            path = task["writes"][0]
            return Completion(content="", tool_calls=[ToolCall(id=title, name="write_file", arguments={"path": path, "content": title})])
        if "You verify completed work" in system:
            self.verify_round += 1
            passed = self.verify_round > 1
            return Completion(content=json.dumps({"passed": passed, "score": 1.0 if passed else 0.4, "summary": "checked", "issues": [], "remaining": []}))
        return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)


def test_replan_skips_completed_steps_and_runs_only_the_repair(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    client = ReplanClient()
    result = orchestrate(
        goal="Write a short article",
        workspace=tmp_path,
        client=client,
        models=MODELS,
        max_loops=2,
        budget_eur=2.0,
        eur_rate=0.92,
    )
    assert result.passed is True
    assert client.worker_titles == ["Research", "Draft", "Repair"]
    assert [step["id"] for step in client.completed_payload] == [1, 2]


class ReadingVerifier(TrivialClient):
    def __init__(self):
        super().__init__()
        self.verify_calls = 0

    def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
        system = messages[0]["content"] if messages else ""
        if "You verify completed work" in system:
            self.verify_calls += 1
            return Completion(content="", tool_calls=[ToolCall(id=str(self.verify_calls), name="list_dir", arguments={"path": "."})])
        return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)


def test_trivial_verification_is_limited_to_two_turns(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    client = ReadingVerifier()
    orchestrate(
        goal="kiek bus 5+5",
        workspace=tmp_path,
        client=client,
        models=MODELS,
        max_loops=1,
        budget_eur=1.0,
        eur_rate=0.92,
    )
    assert client.verify_calls == 2
