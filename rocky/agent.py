"""One agent, one conversation: a goal goes in, a finished file comes out.

``orchestrate`` splits a goal across a planner, a fresh worker context per step and a
separate verifier. Here a single model keeps the whole job in one message thread: it
reads the brief, follows a recipe's checklist or makes its own, calls the tools and
ends the work with the ``done`` tool. Tool dispatch, spending, compaction, the repeat
guard and the run's events are the ones the step workers use, so every surface (CLI,
TUI, web, ``rocky resume``) reads an agent run like any other.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from rocky.adboard import BOARD_PATH, fix_plan, is_board, refresh as refresh_board
from rocky.assess import assess_goal
from rocky.budget import BudgetedClient
from rocky.catalog import Model, parse_models, pick_cast
from rocky.config import preferred_worker
from rocky.context import RepeatGuard, compact
from rocky.engine import (
    CRAFT_RULES,
    DELIVERABLES_DIR,
    FINAL_WRITE_TURNS,
    LAYOUT_RULE,
    MAX_VERIFY_TURNS,
    MEMORY_RULES,
    NO_PROGRESS_LOOPS,
    TOOL_RULES,
    VERIFY_GRACE,
    WRITE_ONLY_TOOLS,
    HALF_SPEND,
    RunResult,
    _chat,
    _context_length,
    _final_status,
    _handover,
    _history_entry,
    _is_trivial,
    _language_for,
    _maybe_lift_budget,
    _missing_files,
    _no_progress,
    _normalize_steps,
    _verify,
    _with_memory,
    calibrated_estimate,
    place_deliverable,
    run_tool_calls,
)
from rocky.events import Emit, Event
from rocky.extras import BrowserSession
from rocky.lang import language_rule
from rocky.llm import Completion, LLM, WebTools
from rocky.mcp import MCPError, MCPRegistry
from rocky.memory import freeze_memory, snapshot_event_payload
from rocky.recipes import catalog as recipe_catalog, find_recipe, load_recipes
from rocky.skills import catalog_for_prompt
from rocky.tools import WorkspaceTools, worker_tools
from rocky.usage import (
    DEFAULT_BUDGET_EUR,
    RoleTally,
    estimate_run_eur,
    eur_from_usd,
    fetch_usd_eur_rate,
    format_receipt,
    usd_from_eur,
)

# Turns the agent may take before the first review. A goal with five recipe steps once had 20 per step.
MAX_AGENT_TURNS = 80
# Turns for each revision after a review sent the work back.
MAX_REVISION_TURNS = 30
# With this many turns left and required files still unwritten, the agent is told so.
AGENT_TURNS_WARN = 8
# Rounds of work and review in one run, however high --max-loops is set.
MAX_AGENT_LOOPS = 3
# Times the agent is sent back by the finishing check before the work is reviewed as it stands.
MAX_FINISH_BOUNCES = 3

DONE_SPEC = {
    "type": "function",
    "function": {
        "name": "done",
        "description": (
            "End the work. Call it once the result is on disk and you have checked it. Rocky then checks the file; "
            "if something is missing the answer says what, and you keep working."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "deliverable_path": {
                    "type": "string",
                    "description": "The file the user asked for, relative to the workspace, e.g. deliverables/plan.md",
                },
                "summary": {
                    "type": "string",
                    "description": "What you made and what the reader must know: assumptions, gaps, anything not confirmed. Three sentences at most.",
                },
            },
            "required": ["summary"],
        },
    },
}

AGENT_RULES = (
    "You are Rocky, an agent that takes one goal from the brief to a finished file in a single working session. "
    "You decide how to do the work: look at what is in the workspace, choose the tools, write the result, check it and hand it over. "
    "Stay inside the workspace. "
    "Size the work to the goal and the budget: a usable, correct, well-organised deliverable is the bar, not a peer-reviewed report. "
    "Never add deliverables, logs or formats the user did not ask for. "
)
FINISH_RULE = (
    "Cite sources. Do the work with tools and do not narrate it. When the result is on disk and checked, call done "
    "with deliverable_path and a short summary; a turn without a tool call ends the work the same way. "
)


def _checklist(plan: dict) -> str:
    """A recipe's steps as the work order the agent keeps in front of it."""
    lines = []
    for step in plan.get("steps") or []:
        writes = ", ".join(str(w) for w in step.get("writes") or [])
        lines.append(f"{step.get('id')}. {step.get('title')}" + (f" (must leave on disk: {writes})" if writes else ""))
        detail = str(step.get("detail") or "").strip()
        if detail:
            lines.append(detail)
        lines.append("")
    return "\n".join(lines).strip()


def _task_message(goal: str, plan: dict, assessment: dict, recipes: dict, resume: dict | None, tools: WorkspaceTools) -> str:
    parts = [f"GOAL\n{goal}", f"DELIVERABLE\n{plan.get('deliverable_path')}"]
    criteria = [str(c) for c in assessment.get("success_criteria") or [] if str(c).strip()]
    if criteria:
        parts.append("WHAT DONE LOOKS LIKE\n" + "\n".join(f"- {c}" for c in criteria))
    if plan.get("recipe") or resume:
        parts.append(
            "CHECKLIST\nA proven order for this kind of work, already on your todo list under these ids. Work in this order. "
            "Set an item in_progress when you start it and completed when its files are on disk. Skip an item only when this "
            "goal clearly does not need it: set it to skipped and say why in its content.\n\n" + _checklist(plan)
        )
    else:
        parts.append("CHECKLIST\nNone for this goal. If the job has more than two parts, write your own short todo list first.")
    if plan.get("notes"):
        parts.append(f"NOTES\n{plan['notes']}")
    skills = "\n".join(f"- {row['name']}: {row['description']}" for row in catalog_for_prompt())
    if skills:
        parts.append("SKILLS YOU CAN LOAD WITH skill_view\n" + skills)
    if not plan.get("recipe"):
        rows = "\n".join(f"- {row['name']}: {row['description']}" for row in recipe_catalog(recipes))
        if rows:
            parts.append("RECIPES (the recipe tool shows one in full)\n" + rows)
    if resume:
        verdict = resume.get("verify") or {}
        points = [str(v) for v in (verdict.get("blocking") or []) + (verdict.get("issues") or [])]
        block = ["This continues an earlier attempt. Its files are on disk: read them before changing anything and do not redo finished work."]
        if verdict.get("summary"):
            block.append(f"Last review: {verdict['summary']}")
        block.extend(f"- {p}" for p in points)
        parts.append("EARLIER ATTEMPT\n" + "\n".join(block))
    listing = tools.list_dir(".")
    parts.append(f"WORKSPACE\n{listing}")
    return "\n\n".join(parts)


def _agent_plan(goal: str, assessment: dict, recipes: dict) -> dict:
    """The recipe's steps when one matches by its words, else one open step the agent shapes itself.

    A marketing *domain* alone is not a match: a pricing question must not become a deck.
    """
    words_only = {k: v for k, v in assessment.items() if k != "domain"}
    recipe = find_recipe(goal, words_only, recipes)
    if recipe is not None:
        plan = recipe.apply({"summary": goal, "steps": []}, goal)
        plan["recipe"] = recipe.name
        return plan
    return {
        "summary": goal,
        "steps": [{"id": 1, "title": "Complete the goal", "detail": goal, "depends_on": [], "writes": []}],
        "deliverable_path": place_deliverable(str(assessment.get("deliverable") or "")),
        "notes": "",
    }


def _skipped_steps(plan: dict, tools: WorkspaceTools) -> set[str]:
    """Checklist items the agent marked skipped. The step that builds the ad page cannot be skipped."""
    wanted = {str(item.get("id")) for item in tools.todo_items() if item.get("status") == "skipped"}
    return {
        str(step.get("id"))
        for step in plan.get("steps") or []
        if str(step.get("id")) in wanted and BOARD_PATH not in [str(w) for w in step.get("writes") or []]
    }


def _live_steps(plan: dict, tools: WorkspaceTools) -> list[dict]:
    skipped = _skipped_steps(plan, tools)
    return [step for step in plan.get("steps") or [] if str(step.get("id")) not in skipped]


def _fixed_deliverable(plan: dict, tools: WorkspaceTools) -> bool:
    """A step being done writes the deliverable: that path is the contract, whatever the agent calls it."""
    target = str(plan.get("deliverable_path") or "")
    return any(target in [str(w) for w in step.get("writes") or []] for step in _live_steps(plan, tools))


def _choose_deliverable(plan: dict, tools: WorkspaceTools, declared: str) -> str:
    current = str(plan.get("deliverable_path") or "")
    if _fixed_deliverable(plan, tools):
        return current
    if declared.strip():
        return place_deliverable(declared)
    if current and not _missing_files(tools, [current]):
        return current
    written = [f for f in tools.files_on_disk() if f.startswith(f"{DELIVERABLES_DIR}/")]
    return written[-1] if written else current


def _revision_note(verify: dict) -> str:
    points = [str(b) for b in verify.get("blocking") or []] or [str(i) for i in verify.get("issues") or []]
    lines = ["The review sent the result back. " + str(verify.get("summary") or "")]
    lines.extend(f"- {p}" for p in points)
    lines.append("Fix these in the files, keep what is right, then call done again.")
    return "\n".join(line for line in lines if line.strip())


def run_agent(
    *,
    goal: str,
    workspace: str | Path,
    client: LLM,
    models: list[Model] | None = None,
    emit: Emit | None = None,
    max_loops: int = 4,
    budget_eur: float = DEFAULT_BUDGET_EUR,
    budget: float | None = None,
    include_server_tools: bool = True,
    eur_rate: float | None = None,
    ask_user=None,
    resume: dict | None = None,
) -> RunResult:
    """Run one goal to a verdict in a single conversation. Same inputs and result as ``orchestrate``."""
    events: list[Event] = []
    event_lock = threading.Lock()

    def fire(etype: str, **payload: Any) -> None:
        event = Event(type=etype, payload=payload)
        with event_lock:
            events.append(event)
            if emit:
                emit(event)

    workspace_path = Path(workspace).expanduser().resolve()
    workspace_path.mkdir(parents=True, exist_ok=True)
    memory_snapshot = freeze_memory(workspace_path)
    try:
        from rocky.brief import brief_block

        brief_text = brief_block(workspace_path)
    except Exception:  # noqa: BLE001 — a broken brief file must not stop the run
        brief_text = ""
    if brief_text:
        memory_snapshot = memory_snapshot + "\n\n" + brief_text
    tools = WorkspaceTools(workspace_path)
    searcher = WebTools()
    browser = BrowserSession()
    mcp: MCPRegistry | None = None
    try:
        mcp = MCPRegistry.load(workspace_path)
        if not mcp.names:
            mcp = None
    except MCPError as exc:
        fire("error", message=f"mcp config ignored: {exc}")
        mcp = None
    ledger = RoleTally()
    tally = ledger.total
    rate = fetch_usd_eur_rate() if eur_rate is None else eur_rate
    usd_cap = float(budget) if budget is not None else usd_from_eur(budget_eur, rate)

    fire(
        "run_started",
        goal=goal,
        workspace=str(workspace_path),
        budget=round(usd_cap, 3),
        budget_eur=round(float(budget_eur if budget is None else eur_from_usd(usd_cap, rate)), 3),
        max_loops=max_loops,
        mode="agent",
    )
    fire("memory", **snapshot_event_payload(workspace_path))

    if models is None:
        fire("catalog", status="fetching")
        models = parse_models(client.list_models())
        fire("catalog", status="ok", count=len(models))

    client = BudgetedClient(client, models, lambda: max(0.0, usd_cap - tally.usd), fire)

    def spend(completion: Completion, role: str = "other") -> None:
        with event_lock:
            ledger.add(completion.usage, role)
            bill = ledger.payload(rate)
            receipt = format_receipt(tally, rate)
        fire("cost", model=completion.model, role=role, receipt=receipt, **bill)

    recipes = load_recipes(workspace_path)
    if resume:
        assessment = dict(resume.get("assessment") or {})
        assessment["goal"] = assessment.get("goal") or goal
        assessment.setdefault("complexity", "moderate")
        assessment.setdefault("domain", "mixed")
        assessment["language"] = _language_for(goal, assessment)
        estimate_eur = float(assessment.get("estimated_eur") or estimate_run_eur(assessment, goal))
        assessment["estimated_eur"] = estimate_eur
        fire(
            "resumed",
            from_run=resume.get("from"),
            loops_before=resume.get("loops") or 0,
            eur_before=resume.get("eur"),
            replan=bool(resume.get("replan")),
            done_steps=list(resume.get("done_steps") or []),
            follow_up=resume.get("follow_up") or "",
        )
        fire("assessed", resumed=True, **assessment)
    else:
        fire("phase", stage="assessing")
        assessment = assess_goal(goal, workspace_path)
        # A marketing domain does not pull in the deck recipe here, so it must not price one either.
        basis = {**assessment, "domain": "mixed" if assessment["domain"] == "marketing" else assessment["domain"]}
        estimate_eur = calibrated_estimate(assessment, goal, workspace_path, basis=basis)
        fire("assessed", **assessment)

    budget_eur = float(budget_eur if budget is None else eur_from_usd(usd_cap, rate))
    language = str(assessment.get("language") or "en")
    usd_cap, budget_eur = _maybe_lift_budget(
        assessment=assessment,
        estimate_eur=estimate_eur,
        budget_eur=budget_eur,
        usd_cap=usd_cap,
        rate=rate,
        ask_user=ask_user,
        fire=fire,
    )

    complexity = str(assessment.get("complexity") or "moderate")
    cast = pick_cast(models, complexity, str(assessment.get("domain") or "mixed"), preferred_worker=preferred_worker())
    saved_worker = str(((resume or {}).get("cast") or {}).get("worker") or "")
    model = saved_worker if saved_worker and any(m.id == saved_worker for m in models) else cast.worker
    fire(
        "cast",
        mode="agent",
        models={"worker": model, "verifier": cast.verifier},
        reasons={"worker": "one agent does the whole job: " + str(cast.reasons.get("worker") or ""), "verifier": cast.reasons.get("verifier") or ""},
    )

    if resume:
        plan = _normalize_steps(dict(resume.get("plan") or {}), goal)
        if not plan.get("deliverable_path"):
            plan["deliverable_path"] = place_deliverable(str(assessment.get("deliverable") or ""))
        if resume.get("replan") and is_board(plan) and resume.get("follow_up"):
            plan = fix_plan(plan, str(resume["follow_up"]))
    else:
        plan = _agent_plan(goal, assessment, recipes)
    fire("plan", **({"resumed": True} if resume else {}), source="recipe" if plan.get("recipe") else "agent", **plan)

    tracked = bool(plan.get("recipe") or resume)
    done_before = {str(i) for i in (resume or {}).get("done_steps") or []}
    tools.todo_write(
        [
            {"id": str(step.get("id")), "content": str(step.get("title")), "status": "completed" if str(step.get("id")) in done_before else "pending"}
            for step in plan.get("steps") or []
        ]
        if tracked
        else []
    )

    specs = worker_tools(include_server_tools) + ([] if mcp is None else mcp.tool_specs()) + [DONE_SPEC]
    messages: list[dict] = [
        {
            "role": "system",
            "content": _with_memory(
                AGENT_RULES + TOOL_RULES + language_rule(language, "worker") + LAYOUT_RULE + CRAFT_RULES + FINISH_RULE + MEMORY_RULES,
                memory_snapshot,
            ),
        },
        {"role": "user", "content": _task_message(goal, plan, assessment, recipes, resume, tools)},
    ]
    context_length = _context_length(models, model)
    guard = RepeatGuard()
    state: dict[str, Any] = {"accepted": False, "aborted": False, "summary": "", "text": "", "bounces": 0}
    started: set[str] = set()
    closed: set[str] = set()
    warned = {"half": False}

    def over_budget() -> bool:
        return client.blocked or tally.usd >= usd_cap

    def start_step(step: dict) -> None:
        key = str(step.get("id"))
        if key not in started:
            started.add(key)
            fire("subtask_start", id=step.get("id"), title=step.get("title"), detail=step.get("detail"))

    def close_step(step: dict) -> None:
        """Report one step's end from what is on disk, so step views and ``rocky resume`` read this run like any other."""
        key = str(step.get("id"))
        if key in closed:
            return
        start_step(step)
        closed.add(key)
        missing = _missing_files(tools, [str(w) for w in step.get("writes") or []])
        if missing:
            fire("subtask_incomplete", id=step.get("id"), title=step.get("title"), missing=missing)
        fire("subtask_done", id=step.get("id"), title=step.get("title"), text=str(state["summary"])[:2000],
             completed=not missing and not state["aborted"], aborted=state["aborted"])

    def sync_steps() -> None:
        """The agent's todo list drives the step events the surfaces already show."""
        if not tracked:
            return
        status = {str(item.get("id")): item.get("status") for item in tools.todo_items()}
        for step in plan.get("steps") or []:
            key = str(step.get("id"))
            if status.get(key) == "in_progress":
                start_step(step)
            elif status.get(key) == "completed" and key not in closed and not _missing_files(tools, [str(w) for w in step.get("writes") or []]):
                close_step(step)
            elif key in _skipped_steps(plan, tools) and key not in closed:
                closed.add(key)
                fire("subtask_skipped", id=step.get("id"), title=step.get("title"), writes=step.get("writes") or [])

    def label() -> str:
        for step in plan.get("steps") or []:
            if str(step.get("id")) in started and str(step.get("id")) not in closed:
                return str(step.get("title") or "agent")
        return "agent"

    def worker_spend(completion: Completion) -> None:
        spend(completion, "worker")
        if warned["half"] or tally.usd < usd_cap * HALF_SPEND:
            return
        deliverable = str(plan.get("deliverable_path") or "")
        if deliverable and not _missing_files(tools, [deliverable]):
            return
        warned["half"] = True
        fire(
            "budget_warn",
            stage="half",
            spent_eur=round(eur_from_usd(tally.usd, rate), 3),
            budget_eur=round(eur_from_usd(usd_cap, rate), 3),
            deliverable=deliverable,
            note=f"{int(HALF_SPEND * 100)} % of the cap spent and no deliverable on disk yet",
        )

    def summarize(transcript: str) -> str:
        completion = _chat(
            client,
            model=cast.assess,
            system=(
                "Summarise this agent transcript for the same agent. Keep: files written "
                "(paths), facts and sources found, decisions made, what is still left. "
                "Plain text, under 300 words."
            ),
            user=transcript[:60000],
        )
        spend(completion, "compact")
        return completion.content or ""

    def required_missing() -> list[str]:
        wanted = [str(plan.get("deliverable_path") or "")]
        for step in _live_steps(plan, tools):
            wanted.extend(str(w) for w in step.get("writes") or [])
        return _missing_files(tools, list(dict.fromkeys(w for w in wanted if w)))

    def attempt_finish(declared: str, summary: str) -> str:
        """The agent says it is finished. Returns what it is told; sets ``accepted`` when the work may end."""
        chosen = _choose_deliverable(plan, tools, declared)
        if chosen != plan.get("deliverable_path"):
            plan["deliverable_path"] = chosen
            fire("deliverable", path=chosen)
        state["summary"] = summary or state["summary"]
        missing = required_missing()
        if missing and state["bounces"] < MAX_FINISH_BOUNCES:
            state["bounces"] += 1
            fire("nudge", subtask=label(), missing=missing, n=state["bounces"])
            return (
                "Not finished. These files are missing or empty: "
                + ", ".join(missing)
                + ". Write them now (write_file / append_file for text; for .pptx .xlsx .docx .pdf run your script with bash), then call done."
            )
        state["accepted"] = True
        return "Accepted."

    def intercept(call) -> str | None:
        if call.name != "done":
            return None
        return attempt_finish(str(call.arguments.get("deliverable_path") or ""), str(call.arguments.get("summary") or ""))

    def run_calls(completion: Completion) -> None:
        # done goes last, so a file written in the same turn is on disk when it is checked.
        completion.tool_calls = sorted(completion.tool_calls, key=lambda call: call.name == "done")
        reason = run_tool_calls(
            completion,
            messages=messages,
            guard=guard,
            tools=tools,
            searcher=searcher,
            ask_user=ask_user,
            llm=client,
            browser=browser,
            mcp=mcp,
            spend=worker_spend,
            fire=fire,
            label=label(),
            intercept=intercept,
            on_result=on_result,
        )
        if reason:
            state["aborted"] = True
            state["text"] = reason

    def on_result(call, result: str) -> None:
        # A long run legitimately re-reads a file it has just changed.
        if call.name not in {"read_file", "list_dir", "grep"} and not result.startswith("error:"):
            guard.counts.clear()
        if call.name == "todo":
            sync_steps()

    def work(max_turns: int) -> None:
        state.update(accepted=False, aborted=False, bounces=0)
        warned_turns = False
        turns = 0
        for turns in range(1, max_turns + 1):
            if over_budget() or state["aborted"] or state["accepted"]:
                break
            left = max_turns - turns + 1
            if left == AGENT_TURNS_WARN and not warned_turns:
                missing_now = required_missing()
                if missing_now:
                    warned_turns = True
                    fire("nudge", subtask=label(), missing=missing_now, n=state["bounces"], reason="turns_left", left=left)
                    messages.append(
                        {
                            "role": "user",
                            "content": f"Only {left} turns are left and these files are still not written: "
                            + ", ".join(missing_now)
                            + ". Write them now from what you have; search more only if turns remain after that.",
                        }
                    )
            squeezed = compact(messages, context_length=context_length, summarize=summarize)
            if squeezed:
                fire("compact", subtask=label(), **squeezed)
                if squeezed.get("mode") == "summary" and tools.todo_items():
                    messages.append({"role": "user", "content": "Your todo list now:\n" + tools.todo_list()})
            completion = _chat(client, model=model, messages=messages, tools=specs)
            worker_spend(completion)
            state["text"] = completion.content or state["text"]
            if client.blocked:
                break
            if completion.tool_calls:
                run_calls(completion)
                continue
            messages.append({"role": "assistant", "content": completion.content or "(no text)"})
            note = attempt_finish("", completion.content or "")
            if state["accepted"]:
                break
            messages.append({"role": "user", "content": note})
        missing = required_missing()
        if missing and turns >= max_turns and not state["accepted"] and not state["aborted"] and not over_budget():
            # The cap ended the work mid-way. What it found is still in the thread: one last pass, writing tools only.
            fire("nudge", subtask=label(), missing=missing, n=state["bounces"] + 1, reason="turns")
            messages.append(
                {
                    "role": "user",
                    "content": "You are out of turns. Do not search or fetch anything more. Write these files NOW from what you "
                    "already found, even if incomplete, and note the gaps inside the file: " + ", ".join(missing),
                }
            )
            write_specs = [t for t in specs if t.get("function", {}).get("name") in WRITE_ONLY_TOOLS]
            for _ in range(FINAL_WRITE_TURNS):
                if state["aborted"] or over_budget():
                    break
                completion = _chat(client, model=model, messages=messages, tools=write_specs)
                worker_spend(completion)
                state["text"] = completion.content or state["text"]
                if not completion.tool_calls:
                    break
                run_calls(completion)

    def settle_steps() -> None:
        sync_steps()
        for step in plan.get("steps") or []:
            close_step(step)

    passed = False
    verify: dict[str, Any] = dict((resume or {}).get("verify") or {})
    history: list[dict] = []
    stopped_early = ""
    loops = 0
    limit = max(1, min(int(max_loops or 1), MAX_AGENT_LOOPS))
    for loops in range(1, limit + 1):
        if tally.usd >= usd_cap:
            fire("budget", cap=round(usd_cap, 3), receipt=format_receipt(tally, rate), **tally.payload(rate))
            break
        fire("loop", n=loops, max=limit)
        fire("phase", stage="working", loop=loops, max=limit)
        started.clear()
        closed.clear()
        if not tracked:
            start_step((plan.get("steps") or [{}])[0])
        work(MAX_AGENT_TURNS if loops == 1 else MAX_REVISION_TURNS)
        refresh_board(plan, tools)
        settle_steps()
        if state["text"] or state["summary"]:
            fire("worker", text=str(state["summary"] or state["text"])[:4000], subtask=label())
        if client.blocked or tally.usd >= usd_cap * VERIFY_GRACE:
            fire("budget", cap=round(usd_cap, 3), stage="before verify", receipt=format_receipt(tally, rate), **tally.payload(rate))
            break
        fire("phase", stage="verifying", loop=loops, max=limit)
        try:
            verify = _verify(
                client,
                cast.verifier,
                goal,
                assessment,
                None,
                plan,
                str(state["summary"] or state["text"]),
                tools,
                lambda completion: spend(completion, "verifier"),
                fire,
                memory_snapshot=memory_snapshot,
                max_turns=0 if _is_trivial(assessment) else (4 if complexity == "moderate" else MAX_VERIFY_TURNS),
            )
        except Exception as exc:  # noqa: BLE001
            fire("error", message=f"verifier failed: {exc}")
            verify = {"passed": False, "score": 0.0, "summary": f"Verifier error: {exc}", "issues": [str(exc)], "remaining": ["Re-run verification"]}
            fire("verify", **verify)
        passed = bool(verify.get("passed"))
        history.append(_history_entry(loops, plan, verify, model))
        if passed:
            break
        if _no_progress(history):
            stopped_early = f"no progress in {NO_PROGRESS_LOOPS} loops — scores " + " → ".join(f"{h['score']:.2f}" for h in history)
            fire("no_progress", loops=loops, scores=[h["score"] for h in history], reason=stopped_early, spent_eur=round(eur_from_usd(tally.usd, rate), 3))
            break
        if tally.usd >= usd_cap or loops >= limit:
            if tally.usd >= usd_cap:
                fire("budget", cap=round(usd_cap, 3), stage="before replan", receipt=format_receipt(tally, rate), **tally.payload(rate))
            break
        messages.append({"role": "user", "content": _revision_note(verify)})

    summary = (verify.get("summary") if verify else "") or str(state["summary"] or state["text"])
    status, notes = _final_status(passed, verify, plan, assessment, tools)
    if notes:
        summary = (summary + "\n" if summary else "") + "Notes: " + "; ".join(notes)
    if stopped_early and status == "unmet":
        summary = (summary + "\n" if summary else "") + f"Stopped early: {stopped_early}."
    passed = status != "unmet"
    bill = ledger.payload(rate)
    handover = _handover(plan, assessment, tools)
    fire("done", passed=passed, status=status, notes=notes, loops=loops, summary=summary, receipt=format_receipt(tally, rate), **handover, **bill)
    try:
        browser.close()
    except Exception:  # noqa: BLE001
        pass
    if mcp is not None:
        fire("mcp", **mcp.summary())
        mcp.close()
    return RunResult(
        passed=passed,
        goal=goal,
        workspace=workspace_path,
        cast=cast,
        cost=tally.usd,
        prompt_tokens=tally.prompt_tokens,
        completion_tokens=tally.completion_tokens,
        total_tokens=tally.total_tokens,
        eur=bill["eur"],
        loops=loops,
        summary=summary,
        events=events,
        status=status,
        notes=notes,
        assessment=dict(assessment),
        deliverable_path=handover["deliverable_path"],
        deliverable_exists=handover["deliverable_exists"],
        files=list(handover["files"]),
    )
