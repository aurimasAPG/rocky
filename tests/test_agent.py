"""One model, one conversation: the whole run from goal to handover, driven by a scripted client."""

import json

import pytest

import rocky.agent as agent_module
from rocky.adboard import BOARD_PATH, FACTS_PATH
from rocky.agent import run_agent
from rocky.catalog import Model
from rocky.llm import Completion, ToolCall

MODELS = [
    Model(id=f"{family}/m", name=family, prompt_price=1e-6, completion_price=2e-6, context_length=200000, params={"tools", "structured_outputs"})
    for family in ("anthropic", "openai", "google")
]
PASS = {"passed": True, "score": 0.9, "usable": True, "summary": "It does what the goal asks.", "blocking": [], "issues": [], "remaining": []}
SOURCE = "https://apgmedia.lt/apie"


def turn(*calls):
    return [ToolCall(id=f"call_{i}_{name}", name=name, arguments=args) for i, (name, args) in enumerate(calls)]


class AgentClient:
    """Answers each role by its system prompt. The agent's turns come from ``script``."""

    def __init__(self, script, verdicts=None):
        self.script = list(script)
        self.verdicts = list(verdicts or [PASS])
        self.systems = []
        self.agent_threads = []
        self.verifier_calls = 0

    def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
        system = messages[0]["content"]
        self.systems.append(system)
        if "You verify completed work" in system:
            self.verifier_calls += 1
            return Completion(content=json.dumps(self.verdicts.pop(0) if len(self.verdicts) > 1 else self.verdicts[0]))
        if "You are Rocky, an agent" in system:
            self.agent_threads.append([dict(m) for m in messages])
            self.agent_tools = [t["function"]["name"] for t in tools or [] if t.get("function")]
            step = self.script.pop(0) if self.script else "All finished."
            if isinstance(step, str):
                return Completion(content=step)
            return Completion(content="", tool_calls=step)
        raise AssertionError(f"unexpected call: {system[:60]}")


def run(tmp_path, monkeypatch, goal, client, **kw):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    events = []
    result = run_agent(goal=goal, workspace=tmp_path, client=client, models=MODELS, emit=events.append, eur_rate=0.92,
                       budget_eur=kw.pop("budget_eur", 5.0), **kw)
    return result, events


def kinds(events, kind):
    return [e.payload for e in events if e.type == kind]


def ad_pack():
    return {
        "brand": "APG Media",
        "url": "https://apgmedia.lt",
        "ads": [
            {
                "primary_text": "Rinkodara, kuri atsiperka.",
                "headline": "Augimas be spėlionių",
                "description": "Pokalbis nemokamai",
                "cta": "Sužinoti daugiau",
                "angle": "Rezultatas",
                "audience": "E. parduotuvių vadovai",
                "mix": {"product": "Reklamos valdymas", "price": "Nuo 990 € per mėnesį", "place": "Internetu, Lietuvoje"},
                "segment": {"size": "Apie 3 000 įmonių (įvertis)", "buying_power": "Vidutinė", "reach_cost": "Žema", "distinct": "Kita problema"},
            }
        ],
        "claims": [],
        "table_stakes": [{"outcome": "Atsakymas į užklausą", "minimum": "Per vieną darbo dieną", "status": "met"}],
    }


# --- one conversation ---------------------------------------------------------------------------


def test_the_whole_run_is_one_conversation(tmp_path, monkeypatch):
    client = AgentClient(
        [
            turn(("write_file", {"path": "runs/notes/draft.md", "content": "draft"})),
            turn(("read_file", {"path": "runs/notes/draft.md"})),
            turn(("write_file", {"path": "deliverables/article.md", "content": "# Title\n\nBody.\n"})),
            turn(("done", {"deliverable_path": "deliverables/article.md", "summary": "Article written."})),
        ]
    )
    result, events = run(tmp_path, monkeypatch, "Write a short article about ads", client)
    assert result.status == "passed" and result.deliverable_path == "deliverables/article.md" and result.deliverable_exists
    assert "You write the plan" not in " ".join(client.systems) and "You critique briefs" not in " ".join(client.systems)
    assert "You execute this subtask" not in " ".join(client.systems)
    last = client.agent_threads[-1]
    assert sum(1 for m in last if m["role"] == "system") == 1
    assert any(m["role"] == "tool" and m["content"] == "draft" for m in last), "an earlier tool result is still in the thread"
    assert [len(t) for t in client.agent_threads] == sorted(len(t) for t in client.agent_threads)
    assert kinds(events, "run_started")[0]["mode"] == "agent"
    assert kinds(events, "done")[0]["status"] == "passed"
    assert "done" in client.agent_tools and "unit_economics" in client.agent_tools


def test_the_prompt_carries_the_tool_rules_and_the_language(tmp_path, monkeypatch):
    client = AgentClient([turn(("write_file", {"path": "deliverables/a.md", "content": "x"})), turn(("done", {"summary": "ok"}))])
    run(tmp_path, monkeypatch, "Parašyk trumpą tekstą apie reklamą", client)
    system = client.agent_threads[0][0]["content"]
    assert "unit_economics" in system and "claim_check" in system and "Lithuanian" in system and "call done" in system
    assert "GOAL\nParašyk trumpą tekstą" in client.agent_threads[0][1]["content"]


def test_done_can_name_another_deliverable_and_resume_keeps_it(tmp_path, monkeypatch):
    client = AgentClient([turn(("write_file", {"path": "deliverables/plan.md", "content": "plan"}), ("done", {"deliverable_path": "plan.md", "summary": "ok"}))])
    result, events = run(tmp_path, monkeypatch, "Write a plan", client)
    assert result.deliverable_path == "deliverables/plan.md" and result.status == "passed"
    assert kinds(events, "deliverable")[0]["path"] == "deliverables/plan.md"


def test_done_in_the_same_turn_as_the_write_sees_the_file(tmp_path, monkeypatch):
    calls = turn(("write_file", {"path": "deliverables/a.md", "content": "x"}), ("done", {"deliverable_path": "deliverables/a.md", "summary": "ok"}))
    client = AgentClient([list(reversed(calls))])
    result, _ = run(tmp_path, monkeypatch, "Write it", client)
    assert result.status == "passed" and len(client.agent_threads) == 1


def test_stopping_without_done_ends_the_work_and_finds_the_file(tmp_path, monkeypatch):
    client = AgentClient([turn(("write_file", {"path": "deliverables/result.md", "content": "x"})), "Here is your result."])
    result, _ = run(tmp_path, monkeypatch, "Write it", client)
    assert result.deliverable_path == "deliverables/result.md" and result.status == "passed"


def test_finishing_without_the_file_sends_the_agent_back(tmp_path, monkeypatch):
    client = AgentClient(
        [
            turn(("done", {"deliverable_path": "deliverables/a.md", "summary": "ok"})),
            turn(("write_file", {"path": "deliverables/a.md", "content": "x"})),
            turn(("done", {"deliverable_path": "deliverables/a.md", "summary": "now"})),
        ]
    )
    result, events = run(tmp_path, monkeypatch, "Write it", client)
    assert result.status == "passed"
    answer = next(m for m in client.agent_threads[1] if m["role"] == "tool")
    assert "Not finished" in answer["content"] and "deliverables/a.md" in answer["content"]
    assert kinds(events, "nudge")[0]["missing"] == ["deliverables/a.md"]


def test_the_review_sends_the_work_back_into_the_same_thread(tmp_path, monkeypatch):
    fail = {"passed": False, "score": 0.4, "usable": False, "summary": "The title is missing.", "blocking": ["No title"], "issues": [], "remaining": []}
    client = AgentClient(
        [
            turn(("write_file", {"path": "deliverables/a.md", "content": "body"}), ("done", {"deliverable_path": "deliverables/a.md", "summary": "ok"})),
            turn(("write_file", {"path": "deliverables/a.md", "content": "# T\nbody"}), ("done", {"deliverable_path": "deliverables/a.md", "summary": "fixed"})),
        ],
        verdicts=[fail, PASS],
    )
    result, events = run(tmp_path, monkeypatch, "Write it", client)
    assert result.status == "passed" and result.loops == 2
    revision = client.agent_threads[1][-1]
    assert revision["role"] == "user" and "No title" in revision["content"]
    assert [p["n"] for p in kinds(events, "loop")] == [1, 2]


def test_a_failed_review_with_a_usable_file_ends_with_notes(tmp_path, monkeypatch):
    fail = {"passed": False, "score": 0.75, "usable": True, "summary": "Mostly there.", "blocking": ["One figure is wrong"], "issues": ["Tone"], "remaining": []}
    client = AgentClient([turn(("write_file", {"path": "deliverables/a.md", "content": "x"}), ("done", {"deliverable_path": "deliverables/a.md", "summary": "ok"}))], verdicts=[fail])
    result, _ = run(tmp_path, monkeypatch, "Write it", client, max_loops=1)
    assert result.status == "passed_with_notes" and any("must fix: One figure is wrong" in n for n in result.notes)


def test_nothing_on_disk_is_unmet(tmp_path, monkeypatch):
    client = AgentClient([], verdicts=[{"passed": False, "score": 0.0, "usable": False, "summary": "No file.", "blocking": ["No file"], "issues": [], "remaining": []}])
    result, _ = run(tmp_path, monkeypatch, "Write it", client, max_loops=1)
    assert result.status == "unmet" and result.deliverable_exists is False


def test_the_turn_cap_gives_one_writing_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "MAX_AGENT_TURNS", 3)
    monkeypatch.setattr(agent_module, "AGENT_TURNS_WARN", 2)
    script = [turn(("list_dir", {"path": f"."})), turn(("grep", {"pattern": "a", "path": "."})), turn(("list_dir", {"path": "runs"})),
              turn(("write_file", {"path": "deliverables/late.md", "content": "partial"}))]
    client = AgentClient(script)
    result, events = run(tmp_path, monkeypatch, "Write late.md", client, max_loops=1)
    reasons = [p.get("reason") for p in kinds(events, "nudge")]
    assert "turns_left" in reasons and "turns" in reasons
    assert (tmp_path / "deliverables/late.md").read_text() == "partial" and result.deliverable_exists
    assert set(client.agent_tools) <= agent_module.WRITE_ONLY_TOOLS


def test_the_cap_stops_the_work_before_the_review(tmp_path, monkeypatch):
    class Costly(AgentClient):
        def complete(self, **kw):
            out = super().complete(**kw)
            if "You are Rocky, an agent" in kw["messages"][0]["content"]:
                out.cost = 2.0
            return out

    client = Costly([turn(("list_dir", {"path": "."})), turn(("list_dir", {"path": "runs"}))])
    result, events = run(tmp_path, monkeypatch, "Write it", client, budget_eur=1.0)
    assert client.verifier_calls == 0 and result.status == "unmet"
    assert kinds(events, "budget")


def test_the_same_read_is_allowed_again_after_a_write(tmp_path, monkeypatch):
    read = ("read_file", {"path": "deliverables/a.md"})
    write = ("write_file", {"path": "deliverables/a.md", "content": "x"})
    client = AgentClient([turn(write), turn(read), turn(read), turn(write), turn(read), turn(read), turn(("done", {"deliverable_path": "deliverables/a.md", "summary": "ok"}))])
    _, events = run(tmp_path, monkeypatch, "Write it", client)
    assert not kinds(events, "loop_guard")


def test_a_marketing_question_with_no_recipe_words_stays_open(tmp_path, monkeypatch):
    client = AgentClient([turn(("write_file", {"path": "deliverables/a.md", "content": "x"}), ("done", {"deliverable_path": "deliverables/a.md", "summary": "ok"}))])
    _, events = run(tmp_path, monkeypatch, "Should we cut the price from 100 to 90?", client)
    plan = kinds(events, "plan")[0]
    assert plan["source"] == "agent" and "recipe" not in plan and len(plan["steps"]) == 1


# --- the three briefs ---------------------------------------------------------------------------


def test_pricing_brief_answers_from_unit_economics(tmp_path, monkeypatch):
    goal = "Parduodame 100 vnt. per mėnesį po 100 €, savikaina 60 €. Ar verta mažinti kainą iki 90 €?"
    client = AgentClient(
        [
            turn(("unit_economics", {"price": 100, "variable_cost": 60, "units": 100, "new_price": 90})),
            turn(("write_file", {"path": "deliverables/kaina.md", "content": "Reikia 134 pardavimų, 34 daugiau nei dabar.\n"})),
            turn(("done", {"deliverable_path": "deliverables/kaina.md", "summary": "Reikia 134 pardavimų."})),
        ],
    )
    result, events = run(tmp_path, monkeypatch, goal, client)
    assert result.status == "passed" and result.deliverable_path == "deliverables/kaina.md"
    answer = next(m["content"] for m in client.agent_threads[1] if m["role"] == "tool")
    assert "134" in answer and json.loads(answer)["contribution_per_unit"] == 40.0
    assert kinds(events, "plan")[0]["source"] == "agent"


def test_ads_brief_follows_the_recipe_and_ends_on_the_page(tmp_path, monkeypatch):
    client = AgentClient(
        [
            turn(("write_file", {"path": FACTS_PATH, "content": f"200+ prekių ženklų — {SOURCE}"})),
            turn(("ad_board", ad_pack())),
            turn(("write_file", {"path": "deliverables/visual-1.png", "content": "png"})),
            turn(("done", {"summary": "Trys reklamos puslapyje."})),
        ],
    )
    result, events = run(tmp_path, monkeypatch, "Sukurk Facebook reklamas apgmedia.lt SEO paslaugai", client)
    assert result.status == "passed" and result.deliverable_path == BOARD_PATH and result.deliverable_exists
    assert kinds(events, "plan")[0]["recipe"] == "ads"
    task = client.agent_threads[0][1]["content"]
    assert "CHECKLIST" in task and "1. Facts" in task and "2. Ads" in task and "3. Visuals" in task
    assert "data:image/png;base64" in (tmp_path / BOARD_PATH).read_text(encoding="utf-8"), "the page is rebuilt with the visuals"
    assert "deliverables/visual-1.png" in result.files


def test_data_brief_reads_the_marketers_file(tmp_path, monkeypatch):
    (tmp_path / "orders.csv").write_text(
        "customer_id,order_date,sku,revenue\n" + "\n".join(f"c{i % 4},2026-0{1 + i % 9}-10,s{i % 3},{10 + i}" for i in range(24)) + "\n",
        encoding="utf-8",
    )
    client = AgentClient(
        [
            turn(("table_analyze", {"path": "orders.csv", "op": "describe"})),
            turn(("write_file", {"path": "deliverables/ataskaita.md", "content": "24 užsakymai.\n"})),
            turn(("done", {"deliverable_path": "deliverables/ataskaita.md", "summary": "24 užsakymai."})),
        ],
    )
    result, _ = run(tmp_path, monkeypatch, "Čia orders.csv. Kiek užsakymų ir kokia apyvarta?", client)
    assert result.status == "passed" and result.deliverable_exists
    answer = next(m["content"] for m in client.agent_threads[1] if m["role"] == "tool")
    assert not answer.startswith("error") and "24" in answer


# --- resume and the run mode --------------------------------------------------------------------


def test_resume_starts_one_new_conversation_from_the_last_verdict(tmp_path, monkeypatch):
    (tmp_path / "deliverables").mkdir()
    (tmp_path / "deliverables/a.md").write_text("old", encoding="utf-8")
    resume = {
        "from": "r1",
        "assessment": {"goal": "Write it", "complexity": "moderate", "domain": "writing", "deliverable": "deliverables/a.md"},
        "plan": {"summary": "Write it", "steps": [{"id": 1, "title": "Complete the goal", "detail": "x", "depends_on": [], "writes": []}], "deliverable_path": "deliverables/a.md"},
        "cast": {"worker": "openai/m"},
        "verify": {"passed": False, "score": 0.4, "summary": "Too short", "blocking": ["Add a second paragraph"], "issues": []},
        "replan": True,
        "done_steps": [],
    }
    seen_models = []

    class Recording(AgentClient):
        def complete(self, *, model, messages, **kw):
            if "You are Rocky, an agent" in messages[0]["content"]:
                seen_models.append(model)
            return super().complete(model=model, messages=messages, **kw)

    client = Recording([turn(("write_file", {"path": "deliverables/a.md", "content": "new"}), ("done", {"summary": "Done"}))])
    result, events = run(tmp_path, monkeypatch, "Write it", client, resume=resume)
    assert result.status == "passed" and result.deliverable_path == "deliverables/a.md"
    assert seen_models == ["openai/m"], "a resumed run keeps its model"
    task = client.agent_threads[0][1]["content"]
    assert "EARLIER ATTEMPT" in task and "Add a second paragraph" in task
    assert "You assess tasks" not in " ".join(client.systems)
    assert kinds(events, "plan")[0]["resumed"] is True


def _fake_result():
    class Done:
        status, passed, notes, summary, eur, cost, assessment, deliverable_path, deliverable_exists, files = "passed", True, [], "s", 0, 0, {}, "", False, []

    return Done()


@pytest.mark.parametrize(("env", "called"), [(None, "agent"), ("agent", "agent"), ("orchestrate", "orchestrate")])
def test_the_job_runs_the_chosen_mode(tmp_path, monkeypatch, env, called):
    from rocky.job import run_worker
    from rocky.runs import create_run

    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    if env is None:
        monkeypatch.delenv("ROCKY_MODE", raising=False)
    else:
        monkeypatch.setenv("ROCKY_MODE", env)
    meta = create_run(workspace=tmp_path, goal="x", budget_eur=0.5, max_loops=1, include_server_tools=True)
    ran = []
    monkeypatch.setattr("rocky.job.run_agent", lambda **kw: ran.append("agent") or _fake_result())
    monkeypatch.setattr("rocky.job.orchestrate", lambda **kw: ran.append("orchestrate") or _fake_result())
    monkeypatch.setattr("rocky.job.api_key", lambda: "k")
    run_worker(meta["id"], tmp_path)
    assert ran == [called]


def test_the_mode_flag_reaches_the_background_job(tmp_path, monkeypatch):
    import os

    from rocky.cli import main

    seen = {}
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ROCKY_MODE", "agent")  # main() writes the flag into the environment; this restores it afterwards
    monkeypatch.setattr("rocky.cli.spawn_worker", lambda meta: seen.update(env=os.environ.get("ROCKY_MODE")) or 1)
    monkeypatch.setattr("rocky.cli._attach", lambda workspace, rid: 0)

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def close(self):
            pass

    monkeypatch.setattr("rocky.cli.OpenRouterClient", FakeClient)
    assert main(["write x", "-d", str(tmp_path), "--mode", "orchestrate"]) == 0
    assert seen["env"] == "orchestrate"


# --- no assessor, critic or planner -------------------------------------------------------------


@pytest.mark.parametrize(
    ("goal", "complexity", "domain", "deliverable"),
    [
        ("Write a short article about dogs", "trivial", "writing", "deliverables/output.md"),
        ("Parašyk trumpą laišką klientui", "trivial", "writing", "deliverables/output.md"),
        ("Sukurk Facebook reklamas apgmedia.lt", "moderate", "marketing", "deliverables/ads.html"),
        ("Ar verta mažinti kainą iki 90 €?", "moderate", "marketing", "deliverables/output.md"),
        ("Make a deck and a spreadsheet of our results", "hard", "mixed", "deliverables/output.pptx"),
        ("Write summary.md from notes", "trivial", "writing", "deliverables/summary.md"),
        ("Fix the bug in the python script that parses the export and add a unit test for the empty file case and the header row case and the date format case", "hard", "code", "deliverables/output.md"),
    ],
)
def test_a_goal_is_sized_from_its_words(tmp_path, goal, complexity, domain, deliverable):
    from rocky.assess import assess_goal

    out = assess_goal(goal, tmp_path)
    assert (out["complexity"], out["domain"], out["deliverable"]) == (complexity, domain, deliverable)


def test_a_file_already_in_the_folder_is_the_input_not_the_output(tmp_path):
    from rocky.assess import assess_goal

    (tmp_path / "report.pdf").write_bytes(b"x")
    assert assess_goal("Summarise report.pdf into brief.md", tmp_path)["deliverable"] == "deliverables/brief.md"


def test_language_comes_from_the_goal_and_the_setting_wins(tmp_path, monkeypatch):
    from rocky.assess import assess_goal

    monkeypatch.delenv("ROCKY_LANGUAGE", raising=False)
    assert assess_goal("Parašyk straipsnį", tmp_path)["language"] == "lt"
    assert assess_goal("Write an article", tmp_path)["language"] == "en"
    monkeypatch.setenv("ROCKY_LANGUAGE", "en")
    assert assess_goal("Parašyk straipsnį", tmp_path)["language"] == "en"


@pytest.mark.parametrize(
    "goal",
    ["Write a short article", "Sukurk Facebook reklamas apgmedia.lt", "Ar verta mažinti kainą iki 90 €?", "Research our top competitors and write a comprehensive report with a deck"],
)
def test_no_assessor_critic_or_planner_is_called_for_any_goal(tmp_path, monkeypatch, goal):
    client = AgentClient([turn(("write_file", {"path": "deliverables/a.md", "content": "x"}), ("done", {"deliverable_path": "deliverables/a.md", "summary": "ok"}))])
    client.script.insert(0, turn(("list_dir", {"path": "."})))
    _, events = run(tmp_path, monkeypatch, goal, client, max_loops=1, budget_eur=9.0)
    joined = " ".join(client.systems)
    assert not any(word in joined for word in ("You assess tasks", "You critique briefs", "You write the plan", "You execute this subtask"))
    roles = {p["role"] for p in kinds(events, "cost")}
    assert roles <= {"worker", "verifier"}, roles
    assert not kinds(events, "critique")
    assert set(kinds(events, "cast")[0]["models"]) == {"worker", "verifier"}
    assert kinds(events, "assessed")[0]["heuristic"] is True


def test_the_budget_question_still_fires_from_the_estimate(tmp_path, monkeypatch):
    asked = []

    def ask(question, options=None):
        asked.append(question)
        return "ne"

    client = AgentClient([turn(("write_file", {"path": "deliverables/a.md", "content": "x"}), ("done", {"deliverable_path": "deliverables/a.md", "summary": "ok"}))])
    _, events = run(tmp_path, monkeypatch, "Research our top competitors and write a comprehensive report with a deck and a spreadsheet", client, budget_eur=1.0, ask_user=ask, max_loops=1)
    assert asked and kinds(events, "budget_offer")[0]["complexity"] == "hard"


# --- recipes as a checklist -----------------------------------------------------------------------


def ads_goal_script(*before):
    return [
        *before,
        turn(("write_file", {"path": FACTS_PATH, "content": f"200+ prekių ženklų — {SOURCE}"})),
        turn(("ad_board", ad_pack())),
        turn(("write_file", {"path": "deliverables/visual-1.png", "content": "png"})),
        turn(("done", {"summary": "Gatava."})),
    ]


def test_the_recipe_is_on_the_todo_list_from_the_start(tmp_path, monkeypatch):
    (tmp_path / ".rocky").mkdir()
    (tmp_path / ".rocky" / "todo.json").write_text('[{"id": "old", "content": "stale", "status": "pending"}]', encoding="utf-8")
    client = AgentClient([turn(("todo", {}))] + ads_goal_script())
    run(tmp_path, monkeypatch, "Sukurk Facebook reklamas apgmedia.lt", client)
    listing = next(m["content"] for m in client.agent_threads[1] if m["role"] == "tool")
    assert "[pending] 1: Facts" in listing and "[pending] 2: Ads" in listing and "[pending] 3: Visuals" in listing
    assert "stale" not in listing


def test_an_open_goal_starts_with_an_empty_list(tmp_path, monkeypatch):
    (tmp_path / ".rocky").mkdir()
    (tmp_path / ".rocky" / "todo.json").write_text('[{"id": "old", "content": "stale", "status": "pending"}]', encoding="utf-8")
    client = AgentClient([turn(("todo", {})), turn(("write_file", {"path": "deliverables/a.md", "content": "x"}), ("done", {"summary": "ok"}))])
    run(tmp_path, monkeypatch, "Write a short article", client)
    assert next(m["content"] for m in client.agent_threads[1] if m["role"] == "tool") == "(todo empty)"
    assert "write your own short todo list" in client.agent_threads[0][1]["content"]


def test_todo_updates_become_step_events(tmp_path, monkeypatch):
    def todo(**status):
        return turn(("todo", {"items": [{"id": k[1:], "content": t, "status": v} for (k, v), t in zip(status.items(), ("Facts", "Ads", "Visuals"))]}))

    client = AgentClient(
        [
            todo(s1="in_progress", s2="pending", s3="pending"),
            turn(("write_file", {"path": FACTS_PATH, "content": f"200+ prekių ženklų — {SOURCE}"})),
            todo(s1="completed", s2="in_progress", s3="pending"),
            turn(("ad_board", ad_pack())),
            todo(s1="completed", s2="completed", s3="in_progress"),
            turn(("write_file", {"path": "deliverables/visual-1.png", "content": "png"})),
            todo(s1="completed", s2="completed", s3="completed"),
            turn(("done", {"summary": "Gatava."})),
        ]
    )
    result, events = run(tmp_path, monkeypatch, "Sukurk Facebook reklamas apgmedia.lt", client)
    assert result.status == "passed"
    order = [(e.type, e.payload["id"]) for e in events if e.type in {"subtask_start", "subtask_done"}]
    assert order == [("subtask_start", 1), ("subtask_done", 1), ("subtask_start", 2), ("subtask_done", 2), ("subtask_start", 3), ("subtask_done", 3)]
    assert all(p["completed"] for p in kinds(events, "subtask_done"))
    tool_labels = [e.payload["subtask"] for e in events if e.type == "tool_call" and e.payload["name"] == "ad_board"]
    assert tool_labels == ["Ads"]


def test_marking_a_step_completed_with_its_file_missing_does_not_close_it(tmp_path, monkeypatch):
    client = AgentClient(
        [
            turn(("todo", {"items": [{"id": "1", "content": "Facts", "status": "completed"}, {"id": "2", "content": "Ads", "status": "pending"}, {"id": "3", "content": "Visuals", "status": "pending"}]})),
            *ads_goal_script(),
        ]
    )
    _, events = run(tmp_path, monkeypatch, "Sukurk Facebook reklamas apgmedia.lt", client)
    first_done = next(i for i, e in enumerate(events) if e.type == "subtask_done" and e.payload["id"] == 1)
    wrote = next(i for i, e in enumerate(events) if e.type == "tool_call" and e.payload["name"] == "write_file")
    assert first_done > wrote and next(e for e in events if e.type == "subtask_done" and e.payload["id"] == 1).payload["completed"]


def marketing_skip_script(*extra):
    items = [
        {"id": "1", "content": "Research", "status": "completed"},
        {"id": "2", "content": "VOC: no customer language needed for a keyword list", "status": "skipped"},
        {"id": "3", "content": "Copy", "status": "skipped"},
        {"id": "4", "content": "Deck and spreadsheet", "status": "skipped"},
        {"id": "5", "content": "Visuals", "status": "skipped"},
    ]
    return [
        turn(("write_file", {"path": "runs/notes/research.md", "content": "notes"}), ("todo", {"items": items})),
        turn(("write_file", {"path": "deliverables/raktazodziai.md", "content": "# Sąrašas\n"})),
        *extra,
        turn(("done", {"deliverable_path": "deliverables/raktazodziai.md", "summary": "Tik raktažodžių sąrašas."})),
    ]


def test_a_skipped_item_is_not_required_and_frees_the_deliverable(tmp_path, monkeypatch):
    client = AgentClient(marketing_skip_script())
    result, events = run(tmp_path, monkeypatch, "Paruošk SEO raktažodžių sąrašą seo paslaugai", client)
    assert kinds(events, "plan")[0]["recipe"] == "marketing"
    assert result.status == "passed" and result.deliverable_path == "deliverables/raktazodziai.md"
    assert not kinds(events, "nudge")
    assert sorted(p["id"] for p in kinds(events, "subtask_skipped")) == [2, 3, 4, 5]
    assert not kinds(events, "subtask_incomplete")


def test_the_step_that_builds_the_ad_page_cannot_be_skipped(tmp_path, monkeypatch):
    items = [{"id": "1", "content": "Facts", "status": "completed"}, {"id": "2", "content": "Ads", "status": "skipped"}, {"id": "3", "content": "Visuals", "status": "skipped"}]
    client = AgentClient(
        [
            turn(("write_file", {"path": FACTS_PATH, "content": "x"}), ("todo", {"items": items})),
            turn(("done", {"deliverable_path": "deliverables/other.md", "summary": "skipped it"})),
            turn(("ad_board", ad_pack())),
            turn(("done", {"summary": "Gatava."})),
        ]
    )
    result, events = run(tmp_path, monkeypatch, "Sukurk Facebook reklamas apgmedia.lt", client)
    assert result.deliverable_path == BOARD_PATH and result.status == "passed"
    assert BOARD_PATH in kinds(events, "nudge")[0]["missing"]


def test_the_todo_list_comes_back_after_compaction(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "compact", lambda messages, **kw: {"mode": "summary", "before_tokens": 9, "after_tokens": 1, "limit": 2})
    client = AgentClient(ads_goal_script())
    run(tmp_path, monkeypatch, "Sukurk Facebook reklamas apgmedia.lt", client)
    assert any(m["role"] == "user" and m["content"].startswith("Your todo list now:") and "1: Facts" in m["content"] for m in client.agent_threads[0])


def test_a_resumed_run_marks_finished_steps_on_the_list(tmp_path, monkeypatch):
    (tmp_path / "runs/notes").mkdir(parents=True)
    (tmp_path / FACTS_PATH).write_text("facts", encoding="utf-8")
    steps = [
        {"id": 1, "title": "Facts", "detail": "x", "depends_on": [], "writes": [FACTS_PATH]},
        {"id": 2, "title": "Ads", "detail": "y", "depends_on": [1], "writes": [BOARD_PATH]},
    ]
    resume = {"from": "r1", "assessment": {"goal": "g", "complexity": "moderate", "domain": "marketing"}, "plan": {"summary": "g", "steps": steps, "deliverable_path": BOARD_PATH, "recipe": "ads"},
              "verify": {}, "replan": False, "done_steps": [1]}
    client = AgentClient([turn(("todo", {})), turn(("ad_board", ad_pack())), turn(("done", {"summary": "ok"}))])
    run(tmp_path, monkeypatch, "Sukurk Facebook reklamas", client, resume=resume)
    listing = next(m["content"] for m in client.agent_threads[1] if m["role"] == "tool")
    assert "[completed] 1: Facts" in listing and "[pending] 2: Ads" in listing


def test_route_answers_from_the_words_without_a_model_call(tmp_path, monkeypatch, capsys):
    from rocky.cli import main

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ROCKY_MODE", "agent")
    monkeypatch.setenv("ROCKY_WORKER", "google/m")

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def close(self):
            pass

        def list_models(self):
            return {"data": [{"id": m.id, "name": m.name, "context_length": 200000, "pricing": {"prompt": "0.000001", "completion": "0.000002"}, "supported_parameters": ["tools", "structured_outputs"], "description": ""} for m in MODELS]}

        def complete(self, **kw):
            raise AssertionError("route must not call a model")

    monkeypatch.setattr("rocky.cli.OpenRouterClient", FakeClient)
    assert main(["route", "Sukurk Facebook reklamas apgmedia.lt", "-d", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "ads" in out and "deliverables/ads.html" in out and "planner" not in out
