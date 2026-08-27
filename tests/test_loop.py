import json

from rocky.catalog import Model
from rocky.engine import orchestrate
from rocky.llm import Completion, ToolCall


class ScriptedClient:
    def __init__(self):
        self.worker_round = 0

    def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
        system = messages[0]["content"] if messages else ""
        if "You assess tasks" in system:
            return Completion(
                content=json.dumps(
                    {
                        "goal": "Write a short article",
                        "complexity": "moderate",
                        "domain": "writing",
                        "success_criteria": ["Article file exists", "Has a title"],
                        "needs_web": False,
                        "deliverable": "article.md",
                    }
                )
            )
        if "You critique briefs" in system:
            return Completion(
                content=json.dumps(
                    {
                        "clarity": "clear",
                        "missing": [],
                        "risks": ["Shallow if sources are skipped"],
                        "quality_bar": "A titled markdown article with two paragraphs.",
                        "reframed_goal": "Write article.md with a title and two paragraphs.",
                        "assumptions": ["English is fine"],
                        "split_hint": ["Write the article"],
                    }
                )
            )
        if "You write the plan" in system:
            return Completion(
                content=json.dumps(
                    {
                        "summary": "Write article.md",
                        "steps": [
                            {
                                "id": 1,
                                "title": "Write the article",
                                "detail": "Create article.md with a title and two paragraphs.",
                                "depends_on": [],
                            }
                        ],
                        "deliverable_path": "article.md",
                        "notes": "",
                    }
                )
            )
        if "You execute this subtask" in system or "You execute the plan" in system:
            self.worker_round += 1
            if self.worker_round == 1:
                return Completion(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="call_1",
                            name="write_file",
                            arguments={
                                "path": "article.md",
                                "content": "# Ads\n\nHello world.\n",
                            },
                        )
                    ],
                )
            return Completion(content="Wrote article.md")
        if "You verify completed work" in system:
            return Completion(
                content=json.dumps(
                    {
                        "passed": True,
                        "score": 0.91,
                        "summary": "Article is present and titled.",
                        "issues": [],
                        "remaining": [],
                    }
                ),
                cost=0.01,
                prompt_tokens=100,
                completion_tokens=40,
                total_tokens=140,
            )
        raise AssertionError(f"unexpected prompt: {system[:80]}")


MODELS = [
    Model(
        id="google/flash",
        name="flash",
        prompt_price=1e-7,
        completion_price=4e-7,
        context_length=128000,
        params={"tools", "structured_outputs"},
        description="cheap",
    ),
    Model(
        id="anthropic/opus",
        name="opus",
        prompt_price=1.5e-5,
        completion_price=7.5e-5,
        context_length=200000,
        params={"tools", "reasoning", "structured_outputs"},
        description="frontier",
    ),
    Model(
        id="openai/gpt",
        name="gpt",
        prompt_price=1.2e-5,
        completion_price=6e-5,
        context_length=200000,
        params={"tools", "reasoning", "structured_outputs"},
        description="frontier",
    ),
]


def test_loop_writes_deliverable_and_passes(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    events = []
    result = orchestrate(
        goal="Write a short article",
        workspace=tmp_path,
        client=ScriptedClient(),
        models=MODELS,
        emit=events.append,
        max_loops=2,
        budget_eur=1.0,
        eur_rate=0.92,
    )
    assert result.passed is True
    assert (tmp_path / "article.md").read_text().startswith("# Ads")
    types = [e.type for e in events]
    assert types[0] == "run_started"
    assert events[0].payload["budget_eur"] == 1.0
    assert "cast" in types
    assert "critique" in types
    assert "plan" in types
    assert "subtask_start" in types
    assert "tool_call" in types
    assert "verify" in types
    assert types[-1] == "done"
    done = events[-1].payload
    assert done["usd"] == 0.01
    assert done["prompt_tokens"] == 100
    assert done["completion_tokens"] == 40
    assert done["total_tokens"] == 140
    assert done["eur"] == 0.009
    assert "$0.010" in done["receipt"]
    assert "€0.009" in done["receipt"]
    assert "memory" in types


def test_loop_injects_frozen_memory_into_cast(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    rocky = tmp_path / ".rocky"
    rocky.mkdir()
    (rocky / "MEMORY.md").write_text("Last deliverable is article.md\n", encoding="utf-8")
    seen: list[str] = []

    class Recording(ScriptedClient):
        def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
            seen.append(messages[0]["content"] if messages else "")
            if len(messages) > 1:
                seen.append(str(messages[1].get("content") or ""))
            return super().complete(
                model=model,
                messages=messages,
                tools=tools,
                json_schema=json_schema,
                extra=extra,
            )

    orchestrate(
        goal="Write a short article",
        workspace=tmp_path,
        client=Recording(),
        models=MODELS,
        max_loops=2,
        budget_eur=1.0,
        eur_rate=0.92,
    )
    blob = "\n".join(seen)
    assert "Last deliverable is article.md" in blob
    assert "You critique briefs" in blob
    assert "You write the plan" in blob
    assert "You execute this subtask" in blob


class TwoSubtaskClient:
    def __init__(self):
        self.written = set()

    def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
        system = messages[0]["content"] if messages else ""
        user = messages[-1].get("content") or ""
        if "You assess tasks" in system:
            return Completion(
                content=json.dumps(
                    {
                        "goal": "Research then write",
                        "complexity": "moderate",
                        "domain": "writing",
                        "success_criteria": ["notes.md exists", "article.md exists"],
                        "needs_web": False,
                        "deliverable": "article.md",
                    }
                )
            )
        if "You critique briefs" in system:
            return Completion(
                content=json.dumps(
                    {
                        "clarity": "underspecified",
                        "missing": ["Audience"],
                        "risks": ["Could skip notes"],
                        "quality_bar": "notes.md then article.md",
                        "reframed_goal": "Take notes, then write article.md",
                        "assumptions": ["Markdown is enough"],
                        "split_hint": ["notes", "article"],
                    }
                )
            )
        if "You write the plan" in system:
            return Completion(
                content=json.dumps(
                    {
                        "summary": "Split research and writing",
                        "steps": [
                            {
                                "id": 1,
                                "title": "Take notes",
                                "detail": "Write notes.md with one bullet.",
                                "depends_on": [],
                            },
                            {
                                "id": 2,
                                "title": "Write article",
                                "detail": "Write article.md using notes.md.",
                                "depends_on": [1],
                            },
                        ],
                        "deliverable_path": "article.md",
                        "notes": "",
                    }
                )
            )
        if "You execute this subtask" in system:
            if "Take notes" in user and "notes.md" not in self.written:
                self.written.add("notes.md")
                return Completion(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="n1",
                            name="write_file",
                            arguments={"path": "notes.md", "content": "- ads\n"},
                        )
                    ],
                )
            if "Write article" in user and "article.md" not in self.written:
                self.written.add("article.md")
                return Completion(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="a1",
                            name="write_file",
                            arguments={"path": "article.md", "content": "# Ads\n\nFrom notes.\n"},
                        )
                    ],
                )
            return Completion(content="subtask done")
        if "You verify completed work" in system:
            return Completion(
                content=json.dumps(
                    {
                        "passed": True,
                        "score": 1.0,
                        "summary": "Both files exist.",
                        "issues": [],
                        "remaining": [],
                    }
                )
            )
        raise AssertionError(system[:80])


def test_two_subagents_run_in_dependency_order(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    events = []
    result = orchestrate(
        goal="Research then write",
        workspace=tmp_path,
        client=TwoSubtaskClient(),
        models=MODELS,
        emit=events.append,
        max_loops=1,
        budget_eur=1.0,
        eur_rate=0.92,
    )
    assert result.passed is True
    assert (tmp_path / "notes.md").read_text().startswith("- ads")
    assert (tmp_path / "article.md").read_text().startswith("# Ads")
    starts = [e.payload.get("title") for e in events if e.type == "subtask_start"]
    assert starts == ["Take notes", "Write article"]
    critique = next(e.payload for e in events if e.type == "critique")
    assert critique["clarity"] == "underspecified"


class LoopingClient(ScriptedClient):
    """Worker that keeps fetching the same URL forever."""

    def __init__(self):
        super().__init__()
        self.tool_errors: list[str] = []

    def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
        system = messages[0]["content"] if messages else ""
        if "You execute this subtask" in system:
            self.worker_round += 1
            for msg in messages:
                if msg.get("role") == "tool" and str(msg.get("content", "")).startswith("error:"):
                    self.tool_errors.append(msg["content"])
            return Completion(
                content="",
                tool_calls=[
                    ToolCall(id=f"c{self.worker_round}", name="list_dir", arguments={"path": "."})
                ],
            )
        if "You verify completed work" in system:
            return Completion(
                content=json.dumps(
                    {"passed": False, "score": 0.0, "summary": "nothing", "issues": ["no file"], "remaining": []}
                )
            )
        return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)


def test_repeat_guard_aborts_a_looping_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    events = []
    client = LoopingClient()
    result = orchestrate(
        goal="Write a short article",
        workspace=tmp_path,
        client=client,
        models=MODELS,
        emit=events.append,
        max_loops=1,
        budget_eur=1.0,
        eur_rate=0.92,
    )
    assert result.passed is False
    guard = [e.payload for e in events if e.type == "loop_guard"]
    assert [g["action"] for g in guard] == ["block", "abort"]
    assert guard[0]["name"] == "list_dir"
    # four identical calls, not twenty
    assert client.worker_round == 4
    done = next(e.payload for e in events if e.type == "subtask_done")
    assert "Aborted" in done["text"]


class LongResultClient(ScriptedClient):
    """Worker whose tool results are huge, forcing compaction."""

    def __init__(self):
        super().__init__()
        self.summaries = 0

    def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
        system = messages[0]["content"] if messages else ""
        if "Summarise this agent transcript" in system:
            self.summaries += 1
            return Completion(content="Read five pages about ads.")
        if "You execute this subtask" in system:
            self.worker_round += 1
            if self.worker_round <= 6:
                return Completion(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id=f"c{self.worker_round}",
                            name="write_file",
                            arguments={"path": f"page{self.worker_round}.md", "content": "y" * 6000},
                        )
                    ],
                )
            return Completion(content="Wrote article.md")
        return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)


def test_worker_thread_is_compacted_against_small_context(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ROCKY_WORKER", "google/flash")
    tiny = [
        Model(
            id="google/flash",
            name="flash",
            prompt_price=1e-7,
            completion_price=4e-7,
            context_length=9_000,
            params={"tools", "structured_outputs"},
        ),
        *MODELS[1:],
    ]
    events = []
    client = LongResultClient()
    orchestrate(
        goal="Write a short article",
        workspace=tmp_path,
        client=client,
        models=tiny,
        emit=events.append,
        max_loops=1,
        budget_eur=1.0,
        eur_rate=0.92,
    )
    compacts = [e.payload for e in events if e.type == "compact"]
    assert compacts, "expected at least one compaction event"
    assert any(c["mode"] == "summary" for c in compacts)
    assert client.summaries >= 1


class TrivialClient(ScriptedClient):
    def __init__(self):
        super().__init__()
        self.prompts: list[str] = []

    def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
        system = messages[0]["content"] if messages else ""
        self.prompts.append(system[:40])
        if "You assess tasks" in system:
            return Completion(
                content=json.dumps(
                    {
                        "goal": "Answer 5+5",
                        "complexity": "trivial",
                        "domain": "writing",
                        "success_criteria": ["answer.md says 10"],
                        "needs_web": False,
                        "deliverable": "answer.md",
                    }
                ),
                cost=0.001,
            )
        if "You execute this subtask" in system:
            self.worker_round += 1
            if self.worker_round == 1:
                return Completion(
                    content="",
                    tool_calls=[
                        ToolCall(id="c1", name="write_file", arguments={"path": "answer.md", "content": "10\n"})
                    ],
                )
            return Completion(content="Wrote answer.md")
        return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)


def test_trivial_goal_skips_critic_and_planner(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    events = []
    client = TrivialClient()
    result = orchestrate(
        goal="kiek bus 5+5",
        workspace=tmp_path,
        client=client,
        models=MODELS,
        emit=events.append,
        max_loops=2,
        budget_eur=1.0,
        eur_rate=0.92,
    )
    assert result.passed is True
    assert (tmp_path / "answer.md").read_text() == "10\n"
    assert not any("You critique briefs" in p for p in client.prompts)
    assert not any("You write the plan" in p for p in client.prompts)
    critique = next(e.payload for e in events if e.type == "critique")
    plan = next(e.payload for e in events if e.type == "plan")
    assert critique["skipped"] == "trivial"
    assert plan["skipped"] == "trivial"
    assert plan["deliverable_path"] == "answer.md"
    assert plan["steps"][0]["depends_on"] == []
    # only assess + worker + verifier paid anything
    assert result.cost == 0.011


class TrivialThenFailClient(TrivialClient):
    """Verifier rejects the first pass so the real planner must step in."""

    def __init__(self):
        super().__init__()
        self.verify_round = 0

    def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
        system = messages[0]["content"] if messages else ""
        if "You verify completed work" in system:
            self.verify_round += 1
            if self.verify_round == 1:
                self.prompts.append(system[:40])
                return Completion(
                    content=json.dumps(
                        {"passed": False, "score": 0.2, "summary": "wrong file", "issues": ["x"], "remaining": []}
                    )
                )
        return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)


def test_trivial_goal_falls_back_to_planner_after_a_failed_verify(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    client = TrivialThenFailClient()
    result = orchestrate(
        goal="kiek bus 5+5",
        workspace=tmp_path,
        client=client,
        models=MODELS,
        max_loops=2,
        budget_eur=1.0,
        eur_rate=0.92,
    )
    assert result.passed is True
    assert result.loops == 2
    assert any("You write the plan" in p for p in client.prompts)


class InspectingVerifierClient(ScriptedClient):
    """Verifier opens the deliverable with a tool before judging."""

    def __init__(self):
        super().__init__()
        self.verify_calls = 0
        self.verify_tools = None

    def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
        system = messages[0]["content"] if messages else ""
        if "You verify completed work" in system:
            self.verify_calls += 1
            self.verify_tools = [t["function"]["name"] for t in tools or []]
            if self.verify_calls == 1:
                return Completion(
                    content="",
                    tool_calls=[ToolCall(id="v1", name="read_file", arguments={"path": "article.md"})],
                )
            seen = messages[-1]["content"]
            passed = seen.startswith("# Ads")
            return Completion(
                content=json.dumps(
                    {"passed": passed, "score": 0.9, "summary": "opened it", "issues": [], "remaining": []}
                )
            )
        return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)


def test_verifier_can_open_the_deliverable_with_read_only_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    events = []
    client = InspectingVerifierClient()
    result = orchestrate(
        goal="Write a short article",
        workspace=tmp_path,
        client=client,
        models=MODELS,
        emit=events.append,
        max_loops=1,
        budget_eur=1.0,
        eur_rate=0.92,
    )
    assert result.passed is True
    assert client.verify_calls == 2
    assert set(client.verify_tools) == {"read_file", "list_dir", "grep", "office_read"}
    calls = [e.payload for e in events if e.type == "tool_call" and e.payload.get("subtask") == "verifier"]
    assert calls and calls[0]["name"] == "read_file"


class StubbornClient(ScriptedClient):
    """Worker never satisfies the verifier; records which model did the work."""

    def __init__(self):
        super().__init__()
        self.worker_models: list[str] = []
        self.plan_calls = 0

    def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
        system = messages[0]["content"] if messages else ""
        if "You write the plan" in system:
            self.plan_calls += 1
        if "You execute this subtask" in system:
            self.worker_models.append(model)
            return Completion(content="I wrote nothing useful.")
        if "You verify completed work" in system:
            return Completion(
                content=json.dumps(
                    {"passed": False, "score": 0.1, "summary": "no deliverable", "issues": ["missing"], "remaining": []}
                )
            )
        return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)


def test_two_failed_verifications_swap_the_worker_instead_of_replanning(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    events = []
    client = StubbornClient()
    result = orchestrate(
        goal="Write a short article",
        workspace=tmp_path,
        client=client,
        models=MODELS,
        emit=events.append,
        max_loops=3,
        budget_eur=1.0,
        eur_rate=0.92,
    )
    assert result.passed is False
    stalls = [e.payload for e in events if e.type == "stall"]
    assert len(stalls) == 1
    first, second, third = client.worker_models
    assert first == second
    assert third == stalls[0]["worker"]
    assert third != first
    assert third.split("/")[0] != first.split("/")[0], "swap should prefer another family"
    # plans: initial, after loop 1, after loop 3 — none after the stall in loop 2
    assert client.plan_calls == 3


class McpClient(ScriptedClient):
    """Worker that uses an MCP tool before writing the article."""

    def __init__(self):
        super().__init__()
        self.tool_names: list[str] = []

    def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
        system = messages[0]["content"] if messages else ""
        if "You execute this subtask" in system:
            self.worker_round += 1
            self.tool_names = [t["function"]["name"] for t in tools or [] if t.get("type") == "function"]
            if self.worker_round == 1:
                return Completion(
                    content="",
                    tool_calls=[ToolCall(id="m1", name="mcp__fake__echo", arguments={"text": "ads"})],
                )
            if self.worker_round == 2:
                echoed = messages[-1]["content"]
                return Completion(
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="w1",
                            name="write_file",
                            arguments={"path": "article.md", "content": f"# {echoed}\n\nHello.\n"},
                        )
                    ],
                )
            return Completion(content="done")
        return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)


def test_workers_can_call_configured_mcp_tools(tmp_path, monkeypatch):
    import sys
    from pathlib import Path

    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    fake = Path(__file__).parent / "fixtures" / "fake_mcp_server.py"
    (tmp_path / ".rocky").mkdir()
    (tmp_path / ".rocky" / "mcp.json").write_text(
        json.dumps({"servers": {"fake": {"command": sys.executable, "args": [str(fake)]}}}), encoding="utf-8"
    )
    events = []
    client = McpClient()
    result = orchestrate(
        goal="Write a short article",
        workspace=tmp_path,
        client=client,
        models=MODELS,
        emit=events.append,
        max_loops=1,
        budget_eur=1.0,
        eur_rate=0.92,
    )
    assert result.passed is True
    assert "mcp__fake__echo" in client.tool_names
    assert (tmp_path / "article.md").read_text().startswith("# ADS")
    mcp = next(e.payload for e in events if e.type == "mcp")
    assert mcp["servers"] == ["fake"] and mcp["tools"] == 2


def test_project_instructions_are_injected(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    (tmp_path / "AGENTS.md").write_text("Always write in Lithuanian.\n", encoding="utf-8")
    seen: list[str] = []

    class Recording(ScriptedClient):
        def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
            seen.append(messages[0]["content"])
            return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)

    events = []
    orchestrate(
        goal="Write a short article",
        workspace=tmp_path,
        client=Recording(),
        models=MODELS,
        emit=events.append,
        max_loops=1,
        budget_eur=1.0,
        eur_rate=0.92,
    )
    worker_prompt = next(s for s in seen if "You execute this subtask" in s)
    assert "PROJECT INSTRUCTIONS (AGENTS.md" in worker_prompt
    assert "Always write in Lithuanian." in worker_prompt
    mem = next(e.payload for e in events if e.type == "memory")
    assert mem["instructions"] == "AGENTS.md" and mem["instructions_chars"] > 0
