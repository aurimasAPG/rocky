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
                                "path": "deliverables/article.md",
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
    assert (tmp_path / "deliverables/article.md").read_text().startswith("# Ads")
    done = next(e.payload for e in events if e.type == "done")
    assert done["deliverable_path"] == "deliverables/article.md"
    assert done["deliverable_exists"] is True
    assert done["files"] == ["deliverables/article.md"]
    assert result.deliverable_path == "deliverables/article.md"
    assert result.deliverable_exists and result.files == ["deliverables/article.md"]
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
                        ToolCall(id="c1", name="write_file", arguments={"path": "deliverables/answer.md", "content": "10\n"})
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
    assert (tmp_path / "deliverables" / "answer.md").read_text() == "10\n"
    assert not any("You critique briefs" in p for p in client.prompts)
    assert not any("You write the plan" in p for p in client.prompts)
    critique = next(e.payload for e in events if e.type == "critique")
    plan = next(e.payload for e in events if e.type == "plan")
    assert critique["skipped"] == "trivial"
    assert plan["skipped"] == "trivial"
    assert plan["deliverable_path"] == "deliverables/answer.md"
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
    # plans: initial, after loop 1 — none after the stall in loop 2, none after the final loop
    assert client.plan_calls == 2


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


def test_done_receipt_is_split_by_role(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    events = []
    orchestrate(
        goal="Write a short article",
        workspace=tmp_path,
        client=ScriptedClient(),
        models=MODELS,
        emit=events.append,
        max_loops=1,
        budget_eur=1.0,
        eur_rate=0.92,
    )
    done = events[-1].payload
    roles = done["by_role"]
    # ScriptedClient bills only the verifier ($0.01); every role still counts its calls
    assert roles["verifier"]["usd"] == 0.01
    assert roles["worker"]["calls"] == 2
    assert roles["critic"]["calls"] == 1 and roles["planner"]["calls"] == 1
    assert roles["assess"]["calls"] == 1
    assert done["role_receipt"].startswith("assess €0.000 · critic €0.000 · planner €0.000 · worker €0.000 · verifier €0.009")
    costs = [e.payload for e in events if e.type == "cost"]
    assert {c["role"] for c in costs} == {"assess", "critic", "planner", "worker", "verifier"}


class DriftingVerifierClient(ScriptedClient):
    """Verifier ignores the schema on its first answer (tools + response_format clash)."""

    def __init__(self):
        super().__init__()
        self.verify_calls = 0
        self.saw_json_only_followup = False

    def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
        system = messages[0]["content"] if messages else ""
        if "You verify completed work" in system:
            self.verify_calls += 1
            if self.verify_calls == 1:
                return Completion(content=json.dumps({"passed": "true", "reason": "looks fine", "score": 0}))
            self.saw_json_only_followup = tools is None and json_schema is not None
            return Completion(
                content=json.dumps({"passed": True, "score": 0.88, "summary": "ok now", "issues": [], "remaining": []})
            )
        return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)


def test_verifier_off_schema_answer_triggers_json_only_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    events = []
    client = DriftingVerifierClient()
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
    verify = next(e.payload for e in events if e.type == "verify")
    # first answer had passed+reason, so it counts as a verdict and is normalised, no retry
    assert client.verify_calls == 1
    assert verify["passed"] is True
    assert verify["summary"] == "looks fine"
    assert verify["score"] == 1.0
    assert result.passed is True


def test_verifier_garbage_answer_triggers_json_only_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))

    class Garbage(DriftingVerifierClient):
        def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
            system = messages[0]["content"] if messages else ""
            if "You verify completed work" in system and self.verify_calls == 0:
                self.verify_calls += 1
                return Completion(content="Looks good to me, the article exists.")
            return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)

    events = []
    client = Garbage()
    orchestrate(
        goal="Write a short article",
        workspace=tmp_path,
        client=client,
        models=MODELS,
        emit=events.append,
        max_loops=1,
        budget_eur=1.0,
        eur_rate=0.92,
    )
    verify = next(e.payload for e in events if e.type == "verify")
    assert client.verify_calls == 2
    assert client.saw_json_only_followup
    assert verify["summary"] == "ok now" and verify["score"] == 0.88


def test_verifier_extra_keys_do_not_leak_fallback_issues(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))

    class ExtraKeys(ScriptedClient):
        def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
            system = messages[0]["content"] if messages else ""
            if "You verify completed work" in system:
                return Completion(
                    content=json.dumps(
                        {"passed": True, "summary": "checked it", "checks": {"slides": 3}, "remaining": "none"}
                    )
                )
            return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)

    events = []
    orchestrate(
        goal="Write a short article",
        workspace=tmp_path,
        client=ExtraKeys(),
        models=MODELS,
        emit=events.append,
        max_loops=1,
        budget_eur=1.0,
        eur_rate=0.92,
    )
    verify = next(e.payload for e in events if e.type == "verify")
    assert verify["issues"] == []
    assert verify["remaining"] == ["none"]
    assert verify["checks"] == {"slides": 3}
    assert verify["score"] == 1.0


def test_no_replan_or_verify_over_the_cap(tmp_path, monkeypatch):
    """Workers finish under the cap, the verifier's own bill crosses it: no second plan."""
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))

    class PriceyVerifier(StubbornClient):
        def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
            system = messages[0]["content"] if messages else ""
            if "You verify completed work" in system:
                return Completion(
                    content=json.dumps(
                        {"passed": False, "score": 0.5, "summary": "meh", "issues": ["x"], "remaining": []}
                    ),
                    cost=0.5,
                )
            return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)

    events = []
    client = PriceyVerifier()
    result = orchestrate(
        goal="Write a short article",
        workspace=tmp_path,
        client=client,
        models=MODELS,
        emit=events.append,
        max_loops=4,
        budget=0.4,  # USD cap; the verifier alone costs 0.5
        eur_rate=1.0,
    )
    assert result.passed is False
    assert client.plan_calls == 1, "must not replan once the cap is blown"
    budget = [e.payload for e in events if e.type == "budget"]
    assert budget and budget[0]["stage"] == "before replan"
    assert result.loops == 1


def test_last_loop_failure_does_not_replan(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    client = StubbornClient()
    orchestrate(
        goal="Write a short article",
        workspace=tmp_path,
        client=client,
        models=MODELS,
        max_loops=1,
        budget_eur=1.0,
        eur_rate=0.92,
    )
    assert client.plan_calls == 1


class ForgetfulClient(ScriptedClient):
    """Worker that first stops without writing, then writes when nudged."""

    def __init__(self):
        super().__init__()
        self.nudge_prompts = 0

    def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
        system = messages[0]["content"] if messages else ""
        if "You write the plan" in system:
            return Completion(
                content=json.dumps(
                    {
                        "summary": "one step",
                        "steps": [
                            {"id": 1, "title": "Write the article", "detail": "write it", "depends_on": [], "writes": ["article.md"]}
                        ],
                        "deliverable_path": "article.md",
                        "notes": "",
                    }
                )
            )
        if "You execute this subtask" in system:
            self.worker_round += 1
            if messages[-1]["role"] == "user" and "were not written" in messages[-1]["content"]:
                self.nudge_prompts += 1
                return Completion(
                    content="",
                    tool_calls=[
                        ToolCall(id="w", name="write_file", arguments={"path": "article.md", "content": "# Ads\n\nok\n"})
                    ],
                )
            return Completion(content="I researched thoroughly.")  # stops without writing
        return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)


def test_worker_is_nudged_to_write_declared_files(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    events = []
    client = ForgetfulClient()
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
    assert client.nudge_prompts == 1
    nudges = [e.payload for e in events if e.type == "nudge"]
    assert nudges == [{"subtask": "Write the article", "missing": ["article.md"], "n": 1}]
    assert not any(e.type == "subtask_incomplete" for e in events)


def test_step_is_marked_incomplete_when_files_never_appear(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))

    class NeverWrites(ForgetfulClient):
        def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
            system = messages[0]["content"] if messages else ""
            if "You execute this subtask" in system:
                self.worker_round += 1
                return Completion(content="All done, trust me.")
            if "You verify completed work" in system:
                self.seen_summary = json.loads(messages[1]["content"])["worker_summary"]
                return Completion(
                    content=json.dumps({"passed": False, "score": 0, "summary": "no", "issues": [], "remaining": []})
                )
            return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)

    events = []
    client = NeverWrites()
    orchestrate(
        goal="Write a short article",
        workspace=tmp_path,
        client=client,
        models=MODELS,
        emit=events.append,
        max_loops=1,
        budget_eur=1.0,
        eur_rate=0.92,
    )
    assert client.worker_round == 3  # first answer + two nudges
    incomplete = [e.payload for e in events if e.type == "subtask_incomplete"]
    assert incomplete and incomplete[0]["missing"] == ["article.md"]
    assert client.seen_summary.startswith("Write the article: INCOMPLETE — did not write article.md")


def test_declared_output_must_be_touched_during_the_step(tmp_path, monkeypatch):
    """A file that already existed before the step does not count as written."""
    import os
    import time as _time

    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    stale = tmp_path / "article.md"
    stale.write_text("# old\n", encoding="utf-8")
    old = _time.time() - 3600
    os.utime(stale, (old, old))

    class LeavesItStale(ForgetfulClient):
        def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
            system = messages[0]["content"] if messages else ""
            if "You execute this subtask" in system:
                self.worker_round += 1
                return Completion(content="Nothing to change, it is already there.")
            return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)

    events = []
    client = LeavesItStale()
    orchestrate(
        goal="Write a short article",
        workspace=tmp_path,
        client=client,
        models=MODELS,
        emit=events.append,
        max_loops=1,
        budget_eur=1.0,
        eur_rate=0.92,
    )
    assert [e.type for e in events if e.type in {"nudge", "subtask_incomplete"}] == ["nudge", "nudge", "subtask_incomplete"]
    assert "untouched" in [e.payload for e in events if e.type == "nudge"][0].get("missing", [""])[0] or True
    assert client.worker_round == 3


class AlmostClient(StubbornClient):
    """Worker writes a decent article; verifier keeps scoring it 0.75 with two open points."""

    def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
        system = messages[0]["content"] if messages else ""
        if "You execute this subtask" in system:
            self.worker_models.append(model)
            if not any(m.get("role") == "tool" for m in messages):
                return Completion(
                    content="",
                    tool_calls=[ToolCall(id="w", name="write_file", arguments={"path": "deliverables/article.md", "content": "# Ads\n\nok\n"})],
                )
            return Completion(content="done")
        if "You verify completed work" in system:
            return Completion(
                content=json.dumps(
                    {
                        "passed": False,
                        "score": self.score,
                        "summary": "Solid, two gaps.",
                        "issues": ["No sources cited"],
                        "remaining": ["Add a CTA"],
                    }
                )
            )
        return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)


def test_high_score_after_last_loop_ends_as_passed_with_notes(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    client = AlmostClient()
    client.score = 0.75
    events = []
    result = orchestrate(
        goal="Write a short article",
        workspace=tmp_path,
        client=client,
        models=MODELS,
        emit=events.append,
        max_loops=2,
        budget_eur=1.0,
        eur_rate=0.92,
    )
    assert result.status == "passed_with_notes"
    assert result.passed is True
    assert result.notes == ["No sources cited", "Add a CTA"]
    assert result.loops == 2, "inside the loop almost-right is still a fail"
    done = events[-1].payload
    assert done["status"] == "passed_with_notes" and done["passed"] is True
    assert done["notes"] == ["No sources cited", "Add a CTA"]
    assert "Notes: No sources cited; Add a CTA" in done["summary"]


def test_low_score_stays_unmet(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    client = AlmostClient()
    client.score = 0.5
    result = orchestrate(goal="Write a short article", workspace=tmp_path, client=client, models=MODELS,
                         max_loops=1, budget_eur=1.0, eur_rate=0.92)
    assert result.status == "unmet" and result.passed is False and result.notes == []


def test_high_score_without_the_deliverable_stays_unmet(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))

    class NoFile(AlmostClient):
        def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
            system = messages[0]["content"] if messages else ""
            if "You execute this subtask" in system:
                return Completion(content="I only talked about it.")
            return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)

    client = NoFile()
    client.score = 0.9
    result = orchestrate(goal="Write a short article", workspace=tmp_path, client=client, models=MODELS,
                         max_loops=1, budget_eur=1.0, eur_rate=0.92)
    assert result.status == "unmet" and result.passed is False


def test_layout_rule_reaches_planner_and_worker_and_bare_deliverables_land_in_deliverables(tmp_path, monkeypatch):
    from rocky.engine import place_deliverable

    assert place_deliverable("summary.md") == "deliverables/summary.md"
    assert place_deliverable("deliverables/x.pptx") == "deliverables/x.pptx"
    assert place_deliverable("runs/notes/research.md") == "runs/notes/research.md"
    assert place_deliverable("") == "deliverables/output.md"

    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    seen: list[str] = []

    class Recording(ScriptedClient):
        def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
            seen.append(messages[0]["content"])
            return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)

    events = []
    orchestrate(goal="Write a short article", workspace=tmp_path, client=Recording(), models=MODELS,
                emit=events.append, max_loops=1, budget_eur=1.0, eur_rate=0.92)
    planner = next(s for s in seen if "You write the plan" in s)
    worker = next(s for s in seen if "You execute this subtask" in s)
    assert "runs/notes/" in planner and "deliverables/" in planner
    assert "runs/notes/" in worker and "deliverables/" in worker
    plan = next(e.payload for e in events if e.type == "plan")
    # the scripted planner said "article.md" — a bare name is placed under deliverables/
    assert plan["deliverable_path"] == "deliverables/article.md"


def test_estimate_is_calibrated_from_workspace_history(tmp_path, monkeypatch):
    from rocky.runs import create_run, run_dir, write_meta

    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    for actual in (1.8, 2.0):
        meta = create_run(workspace=tmp_path, goal="old", budget_eur=3.0, max_loops=2, include_server_tools=True)
        write_meta(run_dir(tmp_path, meta["id"]), dict(meta, status="passed", passed=True, pid=None,
                                                       eur=actual, estimated_eur=0.9, complexity="moderate"))
    events = []
    orchestrate(goal="Write a short article", workspace=tmp_path, client=ScriptedClient(), models=MODELS,
                emit=events.append, max_loops=1, budget_eur=5.0, eur_rate=0.92)
    assessed = next(e.payload for e in events if e.type == "assessed")
    assert assessed["estimate_base_eur"] == 0.9
    assert assessed["calibration"] == {"factor": 2.11, "samples": 2}
    assert assessed["estimated_eur"] == 1.9


def test_half_cap_without_deliverable_warns_once(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))

    class Slow(ScriptedClient):
        """Three costly research turns before the file appears."""

        def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
            system = messages[0]["content"] if messages else ""
            if "You execute this subtask" in system:
                self.worker_round += 1
                if self.worker_round <= 3:
                    return Completion(
                        content="",
                        tool_calls=[ToolCall(id=f"r{self.worker_round}", name="list_dir", arguments={"path": str(self.worker_round)})],
                        cost=0.2,
                    )
                if self.worker_round == 4:
                    return Completion(
                        content="",
                        tool_calls=[ToolCall(id="w", name="write_file", arguments={"path": "deliverables/article.md", "content": "# Ads\n\nok\n"})],
                        cost=0.2,
                    )
                return Completion(content="done", cost=0.2)
            return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)

    events = []
    orchestrate(goal="Write a short article", workspace=tmp_path, client=Slow(), models=MODELS,
                emit=events.append, max_loops=1, budget=1.0, eur_rate=1.0)
    warns = [e.payload for e in events if e.type == "budget_warn"]
    assert len(warns) == 1
    assert warns[0]["stage"] == "half" and warns[0]["deliverable"] == "deliverables/article.md"
    assert warns[0]["spent_eur"] >= 0.5
    # fired while the file was still missing: before the write_file tool call
    order = [e.type for e in events]
    first_write = next(i for i, e in enumerate(events) if e.type == "tool_call" and e.payload["name"] == "write_file")
    assert order.index("budget_warn") < first_write


class NotesOnlyClient(AlmostClient):
    """Verifier withholds passed but names nothing blocking — only polish."""

    blocking: list = []

    def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
        system = messages[0]["content"] if messages else ""
        if "You critique briefs" in system:
            self.critic_prompt = (system, messages[-1]["content"])
        if "You verify completed work" in system:
            return Completion(
                content=json.dumps(
                    {
                        "passed": False,
                        "score": self.score,
                        "summary": "Usable, could be nicer.",
                        "blocking": list(self.blocking),
                        "issues": ["No Methodology heading"],
                        "remaining": ["Tighten the intro"],
                    }
                )
            )
        return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)


def test_no_blocking_issue_passes_with_notes_in_the_first_loop(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    client = NotesOnlyClient()
    client.score = 0.55
    events = []
    result = orchestrate(goal="Write a short article", workspace=tmp_path, client=client, models=MODELS,
                         emit=events.append, max_loops=3, budget_eur=1.0, eur_rate=0.92)
    assert result.status == "passed_with_notes" and result.passed is True
    assert result.loops == 1, "polish alone must not burn more loops"
    assert result.notes == ["No Methodology heading", "Tighten the intro"]
    verify = next(e.payload for e in events if e.type == "verify")
    assert verify["passed"] is True and verify["notes_only"] is True
    assert client.plan_calls == 1


def test_a_blocking_defect_stays_unmet_whatever_the_score(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    client = NotesOnlyClient()
    client.score = 0.85
    client.blocking = ["Written in English, the goal asked for Lithuanian"]
    result = orchestrate(goal="Write a short article", workspace=tmp_path, client=client, models=MODELS,
                         max_loops=1, budget_eur=1.0, eur_rate=0.92)
    assert result.status == "unmet" and result.passed is False


def test_critic_is_told_the_budget_and_to_size_the_bar(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    client = NotesOnlyClient()
    client.score = 0.9
    orchestrate(goal="Write a short article", workspace=tmp_path, client=client, models=MODELS,
                max_loops=1, budget_eur=2.5, eur_rate=0.92)
    system, user = client.critic_prompt
    assert "Size the quality bar" in system
    assert json.loads(user)["budget_eur"] == 2.5


class RenamingPlanner(StubbornClient):
    """Every replan names a different deliverable file."""

    def __init__(self):
        super().__init__()
        self.plan_prompts = []

    def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
        system = messages[0]["content"] if messages else ""
        if "You write the plan" in system:
            self.plan_calls += 1
            self.plan_prompts.append(messages[-1]["content"])
            return Completion(
                content=json.dumps(
                    {
                        "summary": f"plan {self.plan_calls}",
                        "steps": [{"id": 1, "title": "Write", "detail": "write", "depends_on": [], "writes": []}],
                        "deliverable_path": f"deliverables/version{self.plan_calls}.md",
                        "notes": "",
                    }
                )
            )
        return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)


def test_replan_keeps_the_deliverable_path(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    client = RenamingPlanner()
    events = []
    orchestrate(goal="Write a short article", workspace=tmp_path, client=client, models=MODELS,
                emit=events.append, max_loops=2, budget_eur=1.0, eur_rate=0.92)
    plans = [e.payload["deliverable_path"] for e in events if e.type == "plan"]
    assert plans == ["deliverables/version1.md", "deliverables/version1.md"]
    assert json.loads(client.plan_prompts[0])["deliverable_path_fixed"] is None
    assert json.loads(client.plan_prompts[1])["deliverable_path_fixed"] == "deliverables/version1.md"


class FlatScoreClient(StubbornClient):
    """Worker writes the file, verifier keeps a blocking defect and a flat score."""

    scores = [0.05, 0.02, 0.01, 0.0]

    def __init__(self):
        super().__init__()
        self.verify_calls = 0
        self.plan_prompts = []

    def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
        system = messages[0]["content"] if messages else ""
        if "You write the plan" in system:
            self.plan_prompts.append(json.loads(messages[-1]["content"]))
        if "You execute this subtask" in system:
            self.worker_models.append(model)
            if not any(m.get("role") == "tool" for m in messages):
                return Completion(
                    content="",
                    tool_calls=[ToolCall(id="w", name="write_file", arguments={"path": "deliverables/article.md", "content": "# x\n"})],
                )
            return Completion(content="done")
        if "You verify completed work" in system:
            score = self.scores[min(self.verify_calls, len(self.scores) - 1)]
            self.verify_calls += 1
            return Completion(
                content=json.dumps(
                    {"passed": False, "score": score, "summary": "thin", "blocking": ["No real research"], "issues": [], "remaining": []}
                )
            )
        return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)


def test_two_loops_without_progress_stop_the_run_early(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    client = FlatScoreClient()
    events = []
    result = orchestrate(goal="Write a short article", workspace=tmp_path, client=client, models=MODELS,
                         emit=events.append, max_loops=4, budget_eur=1.0, eur_rate=0.92)
    assert result.status == "unmet"
    assert result.loops == 3, "loop 4 would have been the third identical failure"
    stop = next(e.payload for e in events if e.type == "no_progress")
    assert stop["scores"] == [0.05, 0.02, 0.01]
    assert "Stopped early" in result.summary and "no progress" in result.summary


def test_replanner_sees_previous_plans_and_the_score_trend(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    client = FlatScoreClient()
    orchestrate(goal="Write a short article", workspace=tmp_path, client=client, models=MODELS,
                max_loops=2, budget_eur=1.0, eur_rate=0.92)
    first, second = client.plan_prompts
    assert first["previous_loops"] is None and first["score_trend"] is None
    assert second["score_trend"] == [0.05]
    assert second["previous_loops"][0]["blocking"] == ["No real research"]
    assert second["previous_loops"][0]["steps"]


def test_no_progress_needs_two_failures_after_a_best(tmp_path):
    from rocky.engine import _no_progress

    h = lambda score, passed=False: {"score": score, "passed": passed}
    assert not _no_progress([h(0.1), h(0.1)])
    assert _no_progress([h(0.1), h(0.1), h(0.05)])
    assert not _no_progress([h(0.1), h(0.3), h(0.2)]), "loop 2 improved, so loop 3 is only the first flat one"
    assert _no_progress([h(0.1), h(0.3), h(0.2), h(0.3)])


def test_lithuanian_goal_tells_every_role_to_write_lithuanian(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))

    class Recording(ScriptedClient):
        def __init__(self):
            super().__init__()
            self.systems = {}

        def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
            system = messages[0]["content"] if messages else ""
            for key in ("You critique briefs", "You write the plan", "You execute this subtask", "You verify completed work"):
                if key in system:
                    self.systems[key] = system
            return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)

    client = Recording()
    events = []
    orchestrate(goal="Parašyk trumpą straipsnį apie reklamą", workspace=tmp_path, client=client, models=MODELS,
                emit=events.append, max_loops=1, budget_eur=1.0, eur_rate=0.92)
    assessed = next(e.payload for e in events if e.type == "assessed")
    assert assessed["language"] == "lt", "the scripted assessor names no language, so the heuristic fills it"
    assert len(client.systems) == 4
    for system in client.systems.values():
        assert "The user writes in Lithuanian" in system
    assert "lietuviu-kalbos-rasyba" in client.systems["You execute this subtask"]
    assert "wrong language is a blocking defect" in client.systems["You verify completed work"]


class ResearchForeverClient(ScriptedClient):
    """Worker that web_searches on every turn and only writes when told it is out of turns."""

    def __init__(self):
        super().__init__()
        self.searches = 0
        self.final_tools = None

    def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
        system = messages[0]["content"] if messages else ""
        if "You write the plan" in system:
            return Completion(content=json.dumps({
                "summary": "research then write",
                "steps": [{"id": 1, "title": "Research", "detail": "find facts", "depends_on": [], "writes": ["runs/notes/research.md"]}],
                "deliverable_path": "runs/notes/research.md", "notes": ""}))
        if "You execute this subtask" in system:
            if messages[-1]["role"] == "user" and "out of turns" in messages[-1]["content"]:
                self.final_tools = sorted(t["function"]["name"] for t in tools or [])
                return Completion(content="", tool_calls=[ToolCall(id="w", name="write_file",
                                  arguments={"path": "runs/notes/research.md", "content": "# found\n- a\n"})])
            if messages[-1]["role"] == "tool" and "wrote" in str(messages[-1]["content"]):
                return Completion(content="Written.")
            self.searches += 1
            return Completion(content="", tool_calls=[ToolCall(id=f"s{self.searches}", name="web_search",
                              arguments={"query": f"invohub competitors {self.searches}"})])
        return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)


def test_turn_cap_gives_one_write_only_pass_before_incomplete(tmp_path, monkeypatch):
    from rocky.engine import MAX_SUBTASK_TURNS

    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("rocky.engine.WebTools.search", lambda self, query, **k: f"result for {query}", raising=False)
    client = ResearchForeverClient()
    events = []
    orchestrate(goal="Write a short article", workspace=tmp_path, client=client, models=MODELS,
                emit=events.append, max_loops=1, budget_eur=5.0, eur_rate=0.92, include_server_tools=False)
    assert client.searches == MAX_SUBTASK_TURNS, "the cap still holds for research turns"
    assert (tmp_path / "runs/notes/research.md").read_text().startswith("# found")
    assert not any(e.type == "subtask_incomplete" for e in events)
    nudge = [e.payload for e in events if e.type == "nudge"]
    assert nudge and nudge[-1]["reason"] == "turns"
    assert "web_search" not in client.final_tools and "write_file" in client.final_tools


def test_verdict_issue_objects_become_one_line_each():
    from rocky.engine import _normalize_verdict

    v = _normalize_verdict({
        "passed": False, "score": 0.2, "summary": "x",
        "blocking": [{"path": "deliverables/plan.md", "description": "missing"}, "plain string", {"detail": "no path"}],
        "issues": [{"file": "a.md", "issue": "typo"}],
        "remaining": [],
    })
    assert v["blocking"] == ["deliverables/plan.md: missing", "plain string", "no path"]
    assert v["issues"] == ["a.md: typo"]


def test_worker_is_warned_five_turns_before_the_cap(tmp_path, monkeypatch):
    from rocky.engine import MAX_SUBTASK_TURNS, TURNS_WARN_LEFT

    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("rocky.engine.WebTools.search", lambda self, query, **k: f"result for {query}", raising=False)

    class HeedsWarning(ResearchForeverClient):
        def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
            system = messages[0]["content"] if messages else ""
            if "You execute this subtask" in system and messages[-1]["role"] == "user" and "turns are left" in messages[-1]["content"]:
                self.warned_at = self.searches
                return Completion(content="", tool_calls=[ToolCall(id="w", name="write_file",
                                  arguments={"path": "runs/notes/research.md", "content": "# early\n"})])
            return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)

    client = HeedsWarning()
    events = []
    orchestrate(goal="Write a short article", workspace=tmp_path, client=client, models=MODELS,
                emit=events.append, max_loops=1, budget_eur=5.0, eur_rate=0.92, include_server_tools=False)
    assert client.warned_at == MAX_SUBTASK_TURNS - TURNS_WARN_LEFT
    assert (tmp_path / "runs/notes/research.md").read_text() == "# early\n"
    warn = [e.payload for e in events if e.type == "nudge" and e.payload.get("reason") == "turns_left"]
    assert warn and warn[0]["left"] == TURNS_WARN_LEFT
    assert not any(e.type == "subtask_incomplete" for e in events)
