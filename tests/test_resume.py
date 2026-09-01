import json

import pytest

from rocky.engine import orchestrate
from rocky.events import Event
from rocky.llm import Completion, ToolCall
from rocky.runs import append_event, create_run, load_resume_state, resolve_resumable, run_dir, write_meta
from tests.test_loop import MODELS, ScriptedClient


def _crashed_run(tmp_path, *, done_steps=(1,), judged=False, plan_steps=None, status="crashed"):
    meta = create_run(workspace=tmp_path, goal="Research then write", budget_eur=2.0, max_loops=3, include_server_tools=False)
    folder = run_dir(tmp_path, meta["id"])
    steps = plan_steps or [
        {"id": 1, "title": "Take notes", "detail": "notes", "depends_on": [], "writes": ["notes.md"]},
        {"id": 2, "title": "Write article", "detail": "article", "depends_on": [1], "writes": ["article.md"]},
    ]
    events = [
        Event("run_started", {"goal": "Research then write"}),
        Event("assessed", {"goal": "Research then write", "complexity": "moderate", "domain": "writing",
                           "success_criteria": ["article.md exists"], "needs_web": False, "deliverable": "article.md",
                           "estimated_eur": 0.9}),
        Event("cast", {"models": {"assess": "google/flash", "critic": "openai/gpt", "planner": "anthropic/opus",
                                  "worker": "google/flash", "verifier": "openai/gpt"}, "reasons": {}}),
        Event("critique", {"clarity": "clear", "missing": [], "risks": [], "quality_bar": "notes then article",
                           "reframed_goal": "Take notes, then write article.md", "assumptions": [], "split_hint": []}),
        Event("plan", {"summary": "two steps", "steps": steps, "deliverable_path": "article.md", "notes": ""}),
        Event("loop", {"n": 1, "max": 3}),
    ]
    for ident in done_steps:
        events.append(Event("subtask_done", {"id": ident, "title": "x", "text": "ok"}))
    events.append(Event("cost", {"eur": 0.42}))
    if judged:
        events.append(Event("verify", {"passed": False, "score": 0.3, "summary": "no article", "issues": ["article.md missing"], "remaining": []}))
    events.append(Event("error", {"message": "The read operation timed out"}))
    events.append(Event("done", {"passed": False, "summary": "timed out"}))
    for e in events:
        append_event(folder, e)
    meta["status"] = status
    meta["passed"] = False
    meta["pid"] = None
    write_meta(folder, meta)
    return meta["id"], folder


def test_load_resume_state_collects_plan_cast_and_done_steps(tmp_path):
    run_id, folder = _crashed_run(tmp_path, done_steps=(1,))
    state = load_resume_state(folder)
    assert state["from"] == run_id
    assert state["goal"] == "Research then write"
    assert state["budget_eur"] == 2.0 and state["max_loops"] == 3
    assert state["plan"]["deliverable_path"] == "article.md"
    assert state["cast"]["worker"] == "google/flash"
    assert state["critique"]["quality_bar"] == "notes then article"
    assert state["done_steps"] == [1]
    assert state["replan"] is False
    assert state["eur"] == 0.42


def test_judged_plan_resumes_with_a_replan_not_skips(tmp_path):
    _, folder = _crashed_run(tmp_path, done_steps=(1, 2), judged=True)
    state = load_resume_state(folder)
    assert state["replan"] is True
    assert state["done_steps"] == []
    assert state["verify"]["summary"] == "no article"


def test_incomplete_steps_are_not_skipped(tmp_path):
    run_id, folder = _crashed_run(tmp_path, done_steps=(1,))
    append_event(folder, Event("subtask_incomplete", {"id": 1, "title": "Take notes", "missing": ["notes.md"]}))
    assert load_resume_state(folder)["done_steps"] == []


def test_run_without_a_plan_cannot_be_resumed(tmp_path):
    meta = create_run(workspace=tmp_path, goal="x", budget_eur=1.0, max_loops=1, include_server_tools=True)
    folder = run_dir(tmp_path, meta["id"])
    append_event(folder, Event("run_started", {"goal": "x"}))
    with pytest.raises(FileNotFoundError):
        load_resume_state(folder)


def test_resolve_resumable_picks_latest_failed_run(tmp_path):
    first, _ = _crashed_run(tmp_path, status="unmet")
    ok = create_run(workspace=tmp_path, goal="fine", budget_eur=1.0, max_loops=1, include_server_tools=True)
    folder = run_dir(tmp_path, ok["id"])
    meta = dict(ok, status="passed", passed=True, pid=None)
    write_meta(folder, meta)
    assert resolve_resumable(tmp_path, None) == first
    with pytest.raises(FileNotFoundError):
        resolve_resumable(tmp_path, ok["id"])


class ResumingClient(ScriptedClient):
    """Only workers and the verifier may be called on a resumed run."""

    def __init__(self):
        super().__init__()
        self.prompts: list[str] = []
        self.worker_tasks: list[str] = []

    def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
        system = messages[0]["content"] if messages else ""
        self.prompts.append(system[:40])
        if "You execute this subtask" in system:
            task = json.loads(messages[1]["content"])
            self.worker_tasks.append(task["subtask"]["title"])
            if "article.md" not in self.worker_tasks:
                self.worker_tasks.append("article.md")
                return Completion(
                    content="",
                    tool_calls=[ToolCall(id="a", name="write_file", arguments={"path": "article.md", "content": "# Ads\n\nok\n"})],
                )
            return Completion(content="wrote it")
        return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)


def test_orchestrate_resume_skips_done_steps_and_planning(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    (tmp_path / "notes.md").write_text("- ads\n", encoding="utf-8")
    _, folder = _crashed_run(tmp_path, done_steps=(1,))
    state = load_resume_state(folder)
    events = []
    client = ResumingClient()
    result = orchestrate(
        goal=state["goal"],
        workspace=tmp_path,
        client=client,
        models=MODELS,
        emit=events.append,
        max_loops=2,
        budget_eur=1.0,
        eur_rate=0.92,
        resume=state,
    )
    assert result.passed is True
    assert not any("You assess tasks" in p for p in client.prompts)
    assert not any("You critique briefs" in p for p in client.prompts)
    assert not any("You write the plan" in p for p in client.prompts)
    assert list(dict.fromkeys(t for t in client.worker_tasks if t != "article.md")) == ["Write article"]
    types = [e.type for e in events]
    assert types[:2] == ["run_started", "memory"] or "resumed" in types
    resumed = next(e.payload for e in events if e.type == "resumed")
    assert resumed["done_steps"] == [1] and resumed["eur_before"] == 0.42
    skipped = [e.payload["id"] for e in events if e.type == "subtask_skipped"]
    assert skipped == [1]
    cast = next(e.payload for e in events if e.type == "cast")
    assert cast["models"]["worker"] == "google/flash"
    assert "kept from run" in cast["reasons"]["worker"]


def test_resume_redoes_a_done_step_whose_files_vanished(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    _, folder = _crashed_run(tmp_path, done_steps=(1,))  # notes.md never written to disk
    state = load_resume_state(folder)
    events = []
    client = ResumingClient()
    orchestrate(goal=state["goal"], workspace=tmp_path, client=client, models=MODELS, emit=events.append,
                max_loops=1, budget_eur=1.0, eur_rate=0.92, resume=state)
    assert not any(e.type == "subtask_skipped" for e in events)
    assert list(dict.fromkeys(t for t in client.worker_tasks if t != "article.md")) == ["Take notes", "Write article"]


def test_resume_after_a_failed_verdict_replans_first(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    _, folder = _crashed_run(tmp_path, done_steps=(1, 2), judged=True)
    state = load_resume_state(folder)
    client = ResumingClient()
    orchestrate(goal=state["goal"], workspace=tmp_path, client=client, models=MODELS,
                max_loops=1, budget_eur=1.0, eur_rate=0.92, resume=state)
    assert any("You write the plan" in p for p in client.prompts)
    assert not any("You assess tasks" in p for p in client.prompts)


def test_resume_repicks_cast_when_a_model_left_the_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    _, folder = _crashed_run(tmp_path, done_steps=())
    state = load_resume_state(folder)
    state["cast"]["worker"] = "gone/model"
    events = []
    orchestrate(goal=state["goal"], workspace=tmp_path, client=ResumingClient(), models=MODELS, emit=events.append,
                max_loops=1, budget_eur=1.0, eur_rate=0.92, resume=state)
    cast = next(e.payload for e in events if e.type == "cast")
    assert cast["models"]["worker"] != "gone/model"


def test_cli_resume_spawns_a_linked_run(tmp_path, monkeypatch):
    from rocky.cli import main

    run_id, _ = _crashed_run(tmp_path, done_steps=(1,))
    spawned = {}
    monkeypatch.setattr("rocky.cli.api_key", lambda: "sk-test")
    monkeypatch.setattr("rocky.cli.spawn_worker", lambda meta: spawned.update(meta) or 1)
    monkeypatch.setattr("rocky.cli._attach", lambda workspace, rid: 0)

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def close(self):
            pass

    monkeypatch.setattr("rocky.cli.OpenRouterClient", FakeClient)
    assert main(["resume", run_id[:12], "-d", str(tmp_path)]) == 0
    assert spawned["resume_from"] == run_id
    assert spawned["goal"] == "Research then write"
    assert spawned["budget_eur"] == 2.0 and spawned["max_loops"] == 3
    # --budget overrides the old cap
    assert main(["resume", "-d", str(tmp_path), "--budget", "5"]) == 0
    assert spawned["budget_eur"] == 5.0


def test_cli_resume_with_nothing_to_resume(tmp_path, monkeypatch, capsys):
    from rocky.cli import main

    monkeypatch.setattr("rocky.cli.api_key", lambda: "sk-test")

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def close(self):
            pass

    monkeypatch.setattr("rocky.cli.OpenRouterClient", FakeClient)
    assert main(["resume", "-d", str(tmp_path)]) == 1
    assert "resume" in capsys.readouterr().out


def test_run_history_reads_meta_or_falls_back_to_events(tmp_path):
    from rocky.runs import run_history

    run_id, folder = _crashed_run(tmp_path, status="unmet")  # events carry assessed + cost, no done.eur
    append_event(folder, Event("done", {"passed": False, "eur": 1.2}))
    newer = create_run(workspace=tmp_path, goal="n", budget_eur=1.0, max_loops=1, include_server_tools=True)
    nf = run_dir(tmp_path, newer["id"])
    write_meta(nf, dict(newer, status="passed", passed=True, pid=None, eur=0.5, estimated_eur=0.9, complexity="moderate"))
    rows = {r["id"]: r for r in run_history(tmp_path)}
    assert rows[newer["id"]]["eur"] == 0.5 and rows[newer["id"]]["estimated_eur"] == 0.9
    assert rows[run_id]["eur"] == 1.2 and rows[run_id]["estimated_eur"] == 0.9 and rows[run_id]["complexity"] == "moderate"
