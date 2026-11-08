from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rocky.adboard import BOARD_PATH, fix_plan, is_board, ready as board_ready, refresh as refresh_board, verifier_brief
from rocky.catalog import Cast, Model, alternate_worker, parse_models, pick_cast
from rocky.budget import BudgetedClient
from rocky.config import forced_language, preferred_worker
from rocky.context import DEFAULT_CONTEXT, RepeatGuard, compact
from rocky.events import Emit, Event
from rocky.lang import budget_question, detect_language, language_rule, normalize_language
from rocky.jsonutil import JsonParseError, parse_json_object
from rocky.extras import BrowserSession
from rocky.llm import Completion, LLM, WebTools
from rocky.runs import run_history
from rocky.usage import (
    DEFAULT_BUDGET_EUR,
    RoleTally,
    calibration,
    estimate_run_eur,
    eur_from_usd,
    fetch_usd_eur_rate,
    format_receipt,
    needs_budget_lift,
    parse_budget_reply,
    suggest_cap_eur,
    usd_from_eur,
)
from rocky.mcp import MCPError, MCPRegistry
from rocky.memory import freeze_memory, recent_runs_block, snapshot_event_payload
from rocky.pipeline import ensure_pipeline
from rocky.recipes import catalog as recipe_catalog, find_recipe, load_recipes
from rocky.skills import catalog_for_prompt
from rocky.tools import (
    VERIFY_TOOL_NAMES,
    VERIFY_TOOL_SPECS,
    ToolError,
    WorkspaceTools,
    dispatch,
    worker_tools,
)
from rocky.waves import schedule_waves

# Turns one worker chat may take. Compaction keeps long steps affordable.
MAX_SUBTASK_TURNS = 20
# Extra turns a worker gets after the cap, writing tools only, to save what it found.
FINAL_WRITE_TURNS = 2
# With this many turns left and declared files still unwritten, the worker is told so.
TURNS_WARN_LEFT = 5
WRITE_ONLY_TOOLS = frozenset({"write_file", "append_file", "patch", "bash", "read_file", "list_dir", "todo"})
# Read-only tool turns the verifier may spend inspecting the workspace.
MAX_VERIFY_TURNS = 6
# A worker's final text, as passed to the verifier and the next loop. The verifier must
# read the files anyway; ten full worker monologues once made its first message 57k tokens.
WORKER_SUMMARY_CHARS = 1500
# One tool result in the verifier's thread (a deck is ~15k chars; a whole report need not be re-sent on every turn).
VERIFY_TOOL_CHARS = 12000
# Consecutive failed verifications before the worker model is swapped.
STALL_AFTER = 2
# Multiple of the cap at which verification is skipped; 1.0 means no grace above the cap.
VERIFY_GRACE = 1.0
# Times a worker is told to produce its missing output files before the step is marked incomplete.
MAX_NUDGES = 2
# Where files go. Working files (research, notes, drafts, scripts) under NOTES_DIR,
# what the user asked for under DELIVERABLES_DIR, nothing in the workspace root.
NOTES_DIR = "runs/notes"
DELIVERABLES_DIR = "deliverables"
LAYOUT_RULE = (
    f"Workspace layout: working files (research, notes, drafts, scripts, scratch data) go under {NOTES_DIR}/; "
    f"final deliverables — what the user asked for — go under {DELIVERABLES_DIR}/; "
    "nothing is written to the workspace root unless the user names a root path. "
)
# Warn once when this share of the cap is gone and the deliverable is still not on disk.
HALF_SPEND = 0.5
# After the last loop, a failed verdict at or above this score with the deliverable on disk
# ends as "passed_with_notes": the user has a good file, the report lists what is left.
# Stop replanning after this many failed loops in a row that did not beat the best score.
NO_PROGRESS_LOOPS = 2
NOTES_SCORE = 0.7
# A verdict with an empty blocking list passes with notes from this score up.
NOTES_MIN_SCORE = 0.5

ASSESS_SCHEMA = {
    "name": "assessment",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "goal": {"type": "string"},
            "complexity": {"type": "string", "enum": ["trivial", "moderate", "hard"]},
            "domain": {
                "type": "string",
                "enum": ["code", "research", "writing", "marketing", "ops", "mixed"],
            },
            "success_criteria": {"type": "array", "items": {"type": "string"}},
            "needs_web": {"type": "boolean"},
            "deliverable": {"type": "string"},
            "language": {
                "type": "string",
                "description": "ISO 639-1 code of the language the user wrote the goal in (lt, en, …).",
            },
        },
        "required": [
            "goal",
            "complexity",
            "domain",
            "success_criteria",
            "needs_web",
            "deliverable",
            "language",
        ],
    },
}

PLAN_SCHEMA = {
    "name": "plan",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {"type": "string"},
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "id": {"type": "integer"},
                        "title": {"type": "string"},
                        "detail": {"type": "string"},
                        "depends_on": {
                            "type": "array",
                            "items": {"type": "integer"},
                        },
                        "writes": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Files this step must leave on disk (relative to the workspace).",
                        },
                    },
                    "required": ["id", "title", "detail", "depends_on", "writes"],
                },
            },
            "deliverable_path": {"type": "string"},
            "notes": {"type": "string"},
        },
        "required": ["summary", "steps", "deliverable_path", "notes"],
    },
}

CRITIQUE_SCHEMA = {
    "name": "critique",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "clarity": {
                "type": "string",
                "enum": ["clear", "ambiguous", "underspecified"],
            },
            "missing": {"type": "array", "items": {"type": "string"}},
            "risks": {"type": "array", "items": {"type": "string"}},
            "quality_bar": {"type": "string"},
            "reframed_goal": {"type": "string"},
            "assumptions": {"type": "array", "items": {"type": "string"}},
            "split_hint": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "clarity",
            "missing",
            "risks",
            "quality_bar",
            "reframed_goal",
            "assumptions",
            "split_hint",
        ],
    },
}

VERIFY_SCHEMA = {
    "name": "verification",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "passed": {"type": "boolean"},
            "score": {"type": "number"},
            "summary": {"type": "string"},
            "usable": {
                "type": "boolean",
                "description": "True when the deliverable exists and a person could use it for the goal after fixing the blocking items themselves in under an hour; false when it must be redone.",
            },
            "blocking": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Defects that make the deliverable unusable for the goal as written. Empty when the user can use the file.",
            },
            "issues": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Non-blocking notes: polish, style, extra rigor. They do not fail the run.",
            },
            "remaining": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["passed", "score", "usable", "summary", "blocking", "issues", "remaining"],
    },
}


@dataclass
class RunResult:
    passed: bool
    goal: str
    workspace: Path
    cast: Cast | None
    cost: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    eur: float = 0.0
    loops: int = 0
    summary: str = ""
    events: list[Event] = field(default_factory=list)
    # passed | passed_with_notes | unmet. ``passed`` is True for the first two.
    status: str = "unmet"
    notes: list[str] = field(default_factory=list)
    assessment: dict = field(default_factory=dict)
    # Where the result is (workspace-relative) and whether it is really on disk.
    deliverable_path: str = ""
    deliverable_exists: bool = False
    # Every file the workers wrote that is still on disk, in write order.
    files: list[str] = field(default_factory=list)


def orchestrate(
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
    """Run one goal to a verdict.

    ``resume`` is the dict from ``rocky.runs.load_resume_state``: the previous
    run's assessment, critique, plan, cast, last verdict, and finished steps.
    With it, no model is asked to assess, critique, or plan again, and steps
    whose files are still on disk are skipped in the first loop.
    """
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

    def spender(role: str):
        return lambda completion: spend(completion, role)

    warned = {"half": False}

    def check_half_spend() -> None:
        """Once: half the cap is gone and the deliverable is still missing."""
        if warned["half"] or tally.usd < usd_cap * HALF_SPEND:
            return
        deliverable = str(plan.get("deliverable_path") or assessment.get("deliverable") or "")
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

    def worker_spend(completion: Completion) -> None:
        spend(completion, "worker")
        check_half_spend()

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
        fire("assessed", resumed=True, **{k: assessment[k] for k in assessment})
    else:
        # Cheap default cast for assess model id; refine after assessment.
        draft = pick_cast(models, "moderate", "mixed", preferred_worker=preferred_worker())
        assess_raw = _chat(
            client,
            model=draft.assess,
            system=(
                "You assess tasks for Rocky. "
                "Return JSON only. Judge complexity honestly. "
                "trivial = one short step or one short writing deliverable; moderate = several distinct phases or sources; "
                "hard = research, code, or multi-file work. Do not mark a short rewrite, summary, or article moderate just because it needs a file. "
                "domain: code | research | writing | marketing | ops | mixed. "
                "marketing = ads, copy, campaigns, landing pages, VOC, SEO, CRO. "
                f"deliverable = the final file's path under {DELIVERABLES_DIR}/ (e.g. {DELIVERABLES_DIR}/summary.md) "
                "unless the user named a path. "
                "language = ISO 639-1 code of the language the instruction is written in."
            ),
            user=_assess_user(goal, workspace_path),
            json_schema=ASSESS_SCHEMA,
        )
        spend(assess_raw, "assess")
        assessment = _safe_json(
            assess_raw.content,
            {
                "goal": goal,
                "complexity": "moderate",
                "domain": "mixed",
                "success_criteria": ["The user's request is fully completed."],
                "needs_web": True,
                "deliverable": f"{DELIVERABLES_DIR}/output.md",
            },
        )
        assessment["goal"] = assessment.get("goal") or goal
        assessment["deliverable"] = place_deliverable(str(assessment.get("deliverable") or ""))
        assessment["language"] = _language_for(goal, assessment)
        if _is_trivial(assessment):
            # An ad goal needs the recipe's facts and visuals; the trivial path has one step and no recipe.
            matched = find_recipe(goal, assessment, load_recipes(workspace_path))
            if matched is not None and matched.deliverable_path == BOARD_PATH:
                assessment["complexity"] = "moderate"
        base_eur = estimate_run_eur(assessment, goal)
        factor, samples = calibration(run_history(workspace_path), str(assessment.get("complexity") or ""))
        estimate_eur = round(base_eur * factor, 2)
        assessment["estimated_eur"] = estimate_eur
        assessment["estimate_base_eur"] = base_eur
        assessment["calibration"] = {"factor": factor, "samples": samples}
        fire("assessed", **{k: assessment[k] for k in assessment})

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

    cast = _resumed_cast(resume, models) if resume else None
    if cast is None:
        cast = pick_cast(
            models,
            str(assessment.get("complexity") or "moderate"),
            str(assessment.get("domain") or "mixed"),
            preferred_worker=preferred_worker(),
        )
    fire("cast", **{"models": {
        "assess": cast.assess,
        "critic": cast.critic,
        "planner": cast.planner,
        "worker": cast.worker,
        "verifier": cast.verifier,
    }, "reasons": cast.reasons})

    skip_steps: set[int] = set()
    if resume:
        critique = dict(resume.get("critique") or _trivial_critique(goal, assessment))
        fire("critique", resumed=True, **critique)
        plan = _normalize_steps(dict(resume.get("plan") or {}), goal)
        if not plan.get("deliverable_path"):
            plan["deliverable_path"] = assessment.get("deliverable") or f"{DELIVERABLES_DIR}/output.md"
        if resume.get("replan") and is_board(plan) and resume.get("follow_up"):
            plan = fix_plan(plan, str(resume["follow_up"]))
            fire("plan", resumed=True, **plan)
        elif resume.get("replan"):
            plan = _make_plan(
                client,
                cast.planner,
                goal,
                assessment,
                workspace_path,
                spender("planner"),
                fire,
                critique=critique,
                revision=resume.get("verify") or None,
                memory_snapshot=memory_snapshot,
                pinned_deliverable=str(plan.get("deliverable_path") or ""),
            )
        else:
            fire("plan", resumed=True, **plan)
            skip_steps = _steps_still_done(plan, resume.get("done_steps") or [], tools)
    elif _is_trivial(assessment):
        # One short step: no critic, no planner. Straight to a worker and the verifier.
        critique = _trivial_critique(goal, assessment)
        fire("critique", **critique)
        plan = _trivial_plan(goal, assessment)
        fire("plan", **plan)
    else:
        critique = _critique(
            client,
            cast.critic,
            goal,
            assessment,
            spender("critic"),
            fire,
            memory_snapshot=memory_snapshot,
            budget_eur=eur_from_usd(usd_cap, rate),
        )
        plan = _make_plan(
            client,
            cast.planner,
            goal,
            assessment,
            workspace_path,
            spender("planner"),
            fire,
            critique=critique,
            memory_snapshot=memory_snapshot,
        )
    worker_summary = ""
    passed = False
    verify: dict[str, Any] = dict((resume or {}).get("verify") or {})
    loops = 0
    worker_context = _context_length(models, cast.worker)

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

    fail_streak = 0
    tried_workers = {cast.worker}
    worker_model = cast.worker
    history: list[dict] = []  # one entry per verified loop: plan summary, steps, score, blocking
    completed_steps: set[int] = set()
    stopped_early = ""
    if resume and resume.get("worker") and _context_length(models, str(resume["worker"])) and any(
        m.id == resume["worker"] for m in models or []
    ):
        # The previous run had already swapped to a stronger worker; keep it.
        worker_model = str(resume["worker"])
        tried_workers.add(worker_model)
        worker_context = _context_length(models, worker_model)

    for loops in range(1, max_loops + 1):
        if tally.usd >= usd_cap:
            fire("budget", cap=round(usd_cap, 3), receipt=format_receipt(tally, rate), **tally.payload(rate))
            break
        fire("loop", n=loops, max=max_loops)
        worker_summary, completed_steps = _run_subagents(
            client=client,
            model=worker_model,
            goal=goal,
            assessment=assessment,
            critique=critique,
            plan=plan,
            previous=verify,
            tools=tools,
            searcher=searcher,
            include_server_tools=include_server_tools,
            spend=worker_spend,
            fire=fire,
            budget=usd_cap,
            cost_so_far=lambda: usd_cap if client.blocked else tally.usd,
            ask_user=ask_user,
            llm=client,
            browser=browser,
            memory_snapshot=memory_snapshot,
            worker_context=worker_context,
            summarize=summarize,
            mcp=mcp,
            skip=skip_steps,
        )
        skip_steps = set()
        refresh_board(plan, tools)
        if client.blocked or tally.usd >= usd_cap * VERIFY_GRACE:
            fire(
                "budget",
                cap=round(usd_cap, 3),
                stage="before verify",
                receipt=format_receipt(tally, rate),
                **tally.payload(rate),
            )
            break
        try:
            verify = _verify(
                client,
                cast.verifier,
                goal,
                assessment,
                critique,
                plan,
                worker_summary,
                tools,
                spender("verifier"),
                fire,
                memory_snapshot=memory_snapshot,
                max_turns=0 if _is_trivial(assessment) else (4 if str(assessment.get("complexity") or "") == "moderate" else MAX_VERIFY_TURNS),
            )
        except Exception as exc:  # noqa: BLE001
            fire("error", message=f"verifier failed: {exc}")
            verify = {
                "passed": False,
                "score": 0.0,
                "summary": f"Verifier error: {exc}",
                "issues": [str(exc)],
                "remaining": ["Re-run verification"],
            }
            fire("verify", **verify)
        passed = bool(verify.get("passed"))
        history.append(_history_entry(loops, plan, verify, worker_model))
        if passed:
            break
        if _no_progress(history):
            stopped_early = (
                f"no progress in {NO_PROGRESS_LOOPS} loops — scores "
                + " → ".join(f"{h['score']:.2f}" for h in history)
            )
            fire(
                "no_progress",
                loops=loops,
                scores=[h["score"] for h in history],
                reason=stopped_early,
                spent_eur=round(eur_from_usd(tally.usd, rate), 3),
            )
            break
        if tally.usd >= usd_cap or loops >= max_loops:
            if tally.usd >= usd_cap:
                fire(
                    "budget",
                    cap=round(usd_cap, 3),
                    stage="before replan",
                    receipt=format_receipt(tally, rate),
                    **tally.payload(rate),
                )
            break
        fail_streak += 1
        if fail_streak >= STALL_AFTER:
            # Same plan, same failure: the worker is the problem. Swap it, keep the plan.
            swap = alternate_worker(
                models,
                str(assessment.get("complexity") or "moderate"),
                str(assessment.get("domain") or "mixed"),
                exclude=tried_workers,
            )
            if swap is not None:
                tried_workers.add(swap.id)
                fire(
                    "stall",
                    loops=loops,
                    failed=fail_streak,
                    previous_worker=worker_model,
                    worker=swap.id,
                    reason=f"{fail_streak} failed verifications in a row — swapping worker, keeping the plan",
                )
                worker_model = swap.id
                worker_context = _context_length(models, worker_model)
                fail_streak = 0
                continue
        previous_plan = plan
        plan = _make_plan(
            client,
            cast.planner,
            goal,
            assessment,
            workspace_path,
            spender("planner"),
            fire,
            critique=critique,
            revision=verify,
            memory_snapshot=memory_snapshot,
            pinned_deliverable=str(plan.get("deliverable_path") or ""),
            history=history,
            completed_steps=[step for step in previous_plan.get("steps") or [] if step.get("id") in completed_steps],
        )
        skip_steps = _steps_reused_done(previous_plan, plan, completed_steps, tools)

    summary = (verify.get("summary") if verify else worker_summary) or ""
    status, notes = _final_status(passed, verify, plan, assessment, tools)
    if notes:
        summary = (summary + "\n" if summary else "") + "Notes: " + "; ".join(notes)
    if stopped_early and status == "unmet":
        summary = (summary + "\n" if summary else "") + f"Stopped early: {stopped_early}."
    passed = status != "unmet"
    bill = ledger.payload(rate)
    handover = _handover(plan, assessment, tools)
    fire(
        "done",
        passed=passed,
        status=status,
        notes=notes,
        loops=loops,
        summary=summary,
        receipt=format_receipt(tally, rate),
        **handover,
        **bill,
    )
    try:
        browser.close()
    except Exception:
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


def _handover(plan: dict, assessment: dict, tools: WorkspaceTools) -> dict[str, Any]:
    """What the user gets: the deliverable path, whether it exists, and every file written.

    This is the one thing a person needs at the end of a run, and it must be
    true to the disk — a path from the plan that was never written is reported
    as missing, not as the result.
    """
    deliverable = str(plan.get("deliverable_path") or assessment.get("deliverable") or "")
    exists = bool(deliverable) and not _missing_files(tools, [deliverable])
    files = tools.files_on_disk()
    if exists and deliverable not in files:
        files.insert(0, deliverable)
    return {"deliverable_path": deliverable, "deliverable_exists": exists, "files": files}


def _final_status(passed: bool, verify: dict, plan: dict, assessment: dict, tools: WorkspaceTools) -> tuple[str, list[str]]:
    """passed | passed_with_notes | unmet, plus the verifier's open points for the notes case.

    Inside the loop "almost right" is a fail, and that is correct: it drives a
    replan. Once the loops or the cap are gone, a deliverable the verifier
    scored >= NOTES_SCORE is a result the user can use — say so, list the notes.
    """
    if passed and not verify.get("notes_only"):
        return "passed", []
    if not verify:
        if board_ready(plan, tools):
            return "passed_with_notes", ["not reviewed by the verifier; the automatic checks passed"]
        return "unmet", []
    deliverable = str(plan.get("deliverable_path") or assessment.get("deliverable") or "")
    if not deliverable or _missing_files(tools, [deliverable]):
        return "unmet", []
    try:
        score = float(verify.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    blocking = [str(b) for b in verify.get("blocking") or [] if str(b).strip()]
    usable = verify.get("usable")
    if blocking:
        # Inside the loop a blocking defect drove a replan; here the loops are over. The
        # verifier says whether a person can still use the file after fixing those points.
        if usable is not True:
            return "unmet", []
    elif not verify.get("notes_only") and usable is not True and score < NOTES_SCORE:
        return "unmet", []
    notes = [f"must fix: {b}" for b in blocking]
    notes += [str(n) for n in (verify.get("issues") or []) + (verify.get("remaining") or []) if str(n).strip()]
    if not notes:
        notes = [str(verify.get("summary") or "verifier scored it below the bar without naming an issue")]
    return "passed_with_notes", notes


def _language_for(goal: str, assessment: dict) -> str:
    """ROCKY_LANGUAGE / config `language` wins; then the assessor's answer; then the heuristic."""
    forced = normalize_language(forced_language(), "")
    if forced:
        return forced
    return normalize_language(assessment.get("language"), detect_language(goal))


def _history_entry(loop: int, plan: dict, verify: dict, worker: str) -> dict:
    try:
        score = float(verify.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    return {
        "loop": loop,
        "worker": worker,
        "plan_summary": str(plan.get("summary") or "")[:300],
        "steps": [str(step.get("title") or "") for step in plan.get("steps") or []][:12],
        "score": round(score, 2),
        "passed": bool(verify.get("passed")),
        "blocking": [str(b) for b in verify.get("blocking") or []][:6],
        "issues": [str(i) for i in verify.get("issues") or []][:6],
    }


def _no_progress(history: list[dict]) -> bool:
    """True when the last NO_PROGRESS_LOOPS failed loops never beat the best score before them.

    Four near-identical plans scoring 0.05 → 0.02 → 0.01 → 0.00 is money burnt
    on a goal this cast cannot do; stopping leaves the cap for a resume with a
    changed brief.
    """
    if len(history) < NO_PROGRESS_LOOPS + 1:
        return False
    recent = history[-NO_PROGRESS_LOOPS:]
    best_before = max(h["score"] for h in history[: -NO_PROGRESS_LOOPS])
    return all(not h["passed"] and h["score"] <= best_before for h in recent)


def _chat(client: LLM, **kwargs) -> Completion:
    if "messages" not in kwargs:
        messages = []
        if kwargs.get("system"):
            messages.append({"role": "system", "content": kwargs.pop("system")})
        if kwargs.get("user"):
            messages.append({"role": "user", "content": kwargs.pop("user")})
        kwargs["messages"] = messages
    kwargs.pop("system", None)
    kwargs.pop("user", None)
    return client.complete(**kwargs)


def _is_trivial(assessment: dict) -> bool:
    return str(assessment.get("complexity") or "").lower() == "trivial"


def _trivial_critique(goal: str, assessment: dict) -> dict:
    criteria = [str(c) for c in assessment.get("success_criteria") or []] or [
        "The user's request is fully completed."
    ]
    return {
        "clarity": "clear",
        "missing": [],
        "risks": [],
        "quality_bar": " ".join(criteria),
        "reframed_goal": str(assessment.get("goal") or goal),
        "assumptions": [],
        "split_hint": [],
        "skipped": "trivial",
    }


def place_deliverable(path: str) -> str:
    """A bare filename lands in deliverables/; an explicit folder is respected."""
    clean = (path or "").strip().lstrip("./")
    if not clean:
        return f"{DELIVERABLES_DIR}/output.md"
    if "/" in clean:
        return clean
    return f"{DELIVERABLES_DIR}/{clean}"


def _trivial_plan(goal: str, assessment: dict) -> dict:
    deliverable = place_deliverable(str(assessment.get("deliverable") or ""))
    return {
        "summary": str(assessment.get("goal") or goal),
        "steps": [
            {
                "id": 1,
                "title": "Complete the goal",
                "detail": f"{goal}\nWrite the result to {deliverable}.",
                "depends_on": [],
                "writes": [deliverable],
            }
        ],
        "deliverable_path": deliverable,
        "notes": "trivial task: critic and planner skipped",
        "skipped": "trivial",
    }


def _context_length(models: list[Model] | None, model_id: str) -> int:
    for m in models or []:
        if m.id == model_id and m.context_length:
            return int(m.context_length)
    return DEFAULT_CONTEXT


def _resumed_cast(resume: dict, models: list[Model] | None) -> Cast | None:
    """The previous run's cast, if every model is still in the catalog."""
    saved = dict(resume.get("cast") or {})
    roles = ("assess", "critic", "planner", "worker", "verifier")
    if not all(saved.get(role) for role in roles):
        return None
    known = {m.id for m in models or []}
    if known and not all(saved[role] in known for role in roles):
        return None
    return Cast(
        **{role: str(saved[role]) for role in roles},
        reasons={role: f"kept from run {resume.get('from')}" for role in roles},
    )


def _steps_still_done(plan: dict, done_ids: list, tools: WorkspaceTools) -> set[int]:
    """Finished step ids whose declared files are still on disk."""
    keep: set[int] = set()
    by_id = {step.get("id"): step for step in plan.get("steps") or []}
    for ident in done_ids:
        step = by_id.get(ident)
        if step is None:
            continue
        if _missing_files(tools, [str(w) for w in step.get("writes") or []]):
            continue
        keep.add(ident)
    return keep


def _steps_reused_done(
    previous_plan: dict,
    new_plan: dict,
    completed_ids: set[int],
    tools: WorkspaceTools,
) -> set[int]:
    previous = [step for step in previous_plan.get("steps") or [] if step.get("id") in completed_ids]
    keep: set[int] = set()
    for step in new_plan.get("steps") or []:
        title = str(step.get("title") or "").strip().lower()
        writes = {str(path) for path in step.get("writes") or []}
        for old in previous:
            same_title = str(old.get("title") or "").strip().lower() == title
            old_writes = {str(path) for path in old.get("writes") or []}
            same_writes = bool(writes) and writes == old_writes
            if not ((same_title and writes == old_writes) or same_writes):
                continue
            if not _missing_files(tools, [str(path) for path in old.get("writes") or []]):
                keep.add(step.get("id"))
            break
    return keep


def _with_memory(base: str, snapshot: str) -> str:
    extra = (snapshot or "").strip()
    if not extra:
        return base
    return base + "\n\n" + extra


def _assess_user(goal: str, workspace: Path) -> str:
    text = f"User instruction:\n{goal}"
    recent = recent_runs_block(workspace)
    if recent:
        text += (
            "\n\nPrior work in this folder (continue it unless the goal replaces it):\n"
            + recent
        )
    return text


def _safe_json(text: str, fallback: dict) -> dict:
    try:
        parsed = parse_json_object(text)
        merged = dict(fallback)
        merged.update(parsed)
        return merged
    except JsonParseError:
        return fallback


def _maybe_lift_budget(
    *,
    assessment: dict,
    estimate_eur: float,
    budget_eur: float,
    usd_cap: float,
    rate: float,
    ask_user,
    fire,
) -> tuple[float, float]:
    if not needs_budget_lift(estimate_eur, budget_eur):
        return usd_cap, budget_eur
    suggested = suggest_cap_eur(estimate_eur, budget_eur)
    complexity = assessment.get("complexity") or "moderate"
    domain = assessment.get("domain") or "mixed"
    fire(
        "budget_offer",
        complexity=complexity,
        domain=domain,
        estimated_eur=round(estimate_eur, 2),
        current_eur=round(budget_eur, 3),
        suggested_eur=suggested,
    )
    if ask_user is None:
        fire(
            "budget_warn",
            estimated_eur=round(estimate_eur, 2),
            budget_eur=round(budget_eur, 3),
            note="no human on this run — keeping the current cap",
        )
        return usd_cap, budget_eur
    question, options = budget_question(
        str(assessment.get("language") or "en"), str(complexity), str(domain), estimate_eur, budget_eur, suggested
    )
    reply = ask_user(question, options)
    new_eur = parse_budget_reply(reply, suggested, budget_eur)
    usd_cap = usd_from_eur(new_eur, rate)
    fire(
        "budget_set",
        budget_eur=round(new_eur, 3),
        estimated_eur=round(estimate_eur, 2),
        suggested_eur=suggested,
        reply=(reply or "")[:80],
        lifted=new_eur > budget_eur + 0.001,
    )
    return usd_cap, new_eur


PROPORTIONATE_BAR = (
    "Size the quality bar to the goal and the budget. A one-line goal with a small cap gets the bar a "
    "competent freelancer meets in an afternoon — a usable, correct, well-organised deliverable — not a "
    "peer-reviewed report. Never demand external judges, zero-tolerance rules, a citation for every number, "
    "methodology sections, or formats the user did not ask for. Write the bar as two to five checkable "
    "statements about the deliverable itself. "
)


def _critique(client, model, goal, assessment, spend, fire, memory_snapshot: str = "", budget_eur: float = 0.0) -> dict:
    completion = _chat(
        client,
        model=model,
        messages=[
            {
                "role": "system",
                "content": _with_memory(
                    "You critique briefs for Rocky. Be precise, not harsh. "
                    + language_rule(str(assessment.get("language") or "en"), "critic") +
                    "Point out missing constraints, hidden risks, and the quality bar. "
                    "Reframe the goal so a worker cannot hide behind a vague prompt. "
                    "The quality bar describes the deliverable the user asked for — never add new "
                    "deliverables, checklists, logs, proof files, or reporting formats the user did not request; "
                    "a follow-up goal keeps the previous bar, it does not raise it. "
                    + PROPORTIONATE_BAR +
                    "Suggest how to split the work. Return JSON only. Do not refuse the task. "
                    "If MEMORY or RECENT RUNS name a deliverable, treat follow-up goals as continuations.",
                    memory_snapshot,
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"goal": goal, "assessment": assessment, "budget_eur": round(float(budget_eur or 0), 2)},
                    ensure_ascii=False,
                    indent=2,
                ),
            },
        ],
        json_schema=CRITIQUE_SCHEMA,
    )
    spend(completion)
    critique = _safe_json(
        completion.content,
        {
            "clarity": "clear",
            "missing": [],
            "risks": [],
            "quality_bar": "The original request is fully completed.",
            "reframed_goal": goal,
            "assumptions": [],
            "split_hint": [],
        },
    )
    fire("critique", **critique)
    return critique


def _normalize_steps(plan: dict, goal: str) -> dict:
    steps = []
    for i, raw in enumerate(plan.get("steps") or [], 1):
        if not isinstance(raw, dict):
            continue
        try:
            ident = int(raw.get("id") or i)
        except (TypeError, ValueError):
            ident = i
        deps = []
        for item in raw.get("depends_on") or []:
            try:
                deps.append(int(item))
            except (TypeError, ValueError):
                continue
        writes = [str(w).strip() for w in raw.get("writes") or [] if str(w).strip()]
        steps.append(
            {
                "id": ident,
                "title": str(raw.get("title") or f"Step {i}"),
                "detail": str(raw.get("detail") or ""),
                "depends_on": deps,
                "writes": writes,
            }
        )
    if not steps:
        steps = [{"id": 1, "title": "Complete the goal", "detail": goal, "depends_on": [], "writes": []}]
    plan["steps"] = steps
    return plan


def _make_plan(
    client,
    model,
    goal,
    assessment,
    workspace,
    spend,
    fire,
    critique=None,
    revision=None,
    memory_snapshot: str = "",
    pinned_deliverable: str = "",
    history: list[dict] | None = None,
    completed_steps: list[dict] | None = None,
) -> dict:
    """The plan. On a revision ``pinned_deliverable`` is the path from the previous
    plan and it is kept: a replan may change the steps, never the file the
    user will be handed (a rename between loops has lost a finished result)."""
    listing = ""
    try:
        listing = WorkspaceTools(workspace).list_dir(".")
    except ToolError:
        listing = ""
    recipes = load_recipes(workspace)
    recipe = find_recipe(goal, assessment, recipes)
    if recipe and recipe.deliverable_path == BOARD_PATH and not revision:
        # These steps are fixed. A planner can only add to them, and its added ZIP step is what failed a real run.
        plan = recipe.apply({"summary": goal, "steps": []}, goal)
        plan["recipe"] = recipe.name
        fire("plan", **plan)
        return plan
    user = {
        "goal": goal,
        "assessment": assessment,
        "brief_critique": critique,
        "workspace_files": listing,
        "revision_from_verifier": revision,
        "deliverable_path_fixed": pinned_deliverable or None,
        "completed_steps_to_preserve": completed_steps or [],
        "previous_loops": list(history or []) or None,
        "score_trend": [h["score"] for h in history or []] or None,
        "available_skills": catalog_for_prompt(),
        "available_recipes": recipe_catalog(recipes),
        "recipe": (
            {
                "name": recipe.name,
                "description": recipe.description,
                "steps": recipe.render_steps(goal),
                "deliverable_path": recipe.deliverable_path,
                "notes": recipe.notes,
                "rule": (
                    "This goal matches the recipe above. The plan MUST contain its steps in this order "
                    "with the same depends_on chain. You may add steps; you may not skip or merge these."
                ),
            }
            if recipe
            else None
        ),
    }
    completion = _chat(
        client,
        model=model,
        messages=[
            {
                "role": "system",
                "content": _with_memory(
                    "You write the plan for Rocky. Return JSON only. "
                    + language_rule(str(assessment.get("language") or "en"), "planner") +
                    "Split work into subtasks that can be given to separate workers. "
                    "Use depends_on so independent research can run in parallel, "
                    "and writing/assembly waits on research. Each step must be concrete. "
                    "Each step lists in writes the files it must leave on disk; later steps may only read those. "
                    + LAYOUT_RULE +
                    "deliverable_path and every writes entry must follow that layout. "
                    "If the deliverable is xlsx/docx/pptx/pdf, that worker step must skill_view the matching skill. "
                    "If a recipe is given, its steps are mandatory and ordered — a recipe is a proven "
                    "sequence for this kind of deliverable; Rocky will enforce it. "
                    "Name the final deliverable_path. If deliverable_path_fixed is set, use exactly that path — "
                    "the previous loop's work is there and the user expects that file. "
                    "When revision_from_verifier is given, change what the verifier faulted and keep what passed; "
                    "completed_steps_to_preserve already passed validation: keep those step ids, titles, and writes unchanged "
                    "unless the verdict explicitly invalidates their evidence, and do not run them again. "
                    "its blocking list is what must change, its issues are notes. "
                    "previous_loops lists every plan already tried with its score: do not resubmit those steps — "
                    "if the score did not rise, the approach was wrong, so change the approach (different split, "
                    "different sources, a worker step that reads the verifier's blocking list first), and say in "
                    "summary what is different this time. "
                    "Workers are fully autonomous in the workspace. "
                    "Continue MEMORY and RECENT RUNS: 'that' / 'the article' / 'now make X from it' "
                    "means the last deliverable unless the goal clearly replaces it.",
                    memory_snapshot,
                ),
            },
            {"role": "user", "content": json.dumps(user, ensure_ascii=False, indent=2)},
        ],
        json_schema=PLAN_SCHEMA,
        extra={"reasoning": {"effort": "medium"}} if revision or assessment.get("complexity") == "hard" else None,
    )
    spend(completion)
    plan = _safe_json(
        completion.content,
        {
            "summary": goal,
            "steps": [{"id": 1, "title": "Complete the goal", "detail": goal, "depends_on": []}],
            "deliverable_path": assessment.get("deliverable") or f"{DELIVERABLES_DIR}/output.md",
            "notes": "",
        },
    )
    plan = _normalize_steps(plan, goal)
    plan = ensure_pipeline(plan, goal, assessment, recipe=recipe)
    plan["deliverable_path"] = place_deliverable(str(plan.get("deliverable_path") or ""))
    if pinned_deliverable:
        plan["deliverable_path"] = pinned_deliverable
    if recipe:
        plan["recipe"] = recipe.name
    fire("plan", **plan)
    return plan


def _run_subagents(
    *,
    client,
    model,
    goal,
    assessment,
    critique,
    plan,
    previous,
    tools: WorkspaceTools,
    searcher: WebTools,
    include_server_tools: bool,
    spend,
    fire,
    budget: float,
    cost_so_far,
    ask_user=None,
    llm=None,
    browser=None,
    memory_snapshot: str = "",
    worker_context: int = DEFAULT_CONTEXT,
    summarize=None,
    mcp=None,
    skip: set[int] | None = None,
) -> tuple[str, set[int]]:
    summaries: list[str] = []
    completed: set[int] = set()
    skip = set(skip or ())
    for full_wave in schedule_waves(plan.get("steps") or []):
        if cost_so_far() >= budget:
            break
        wave = []
        for step in full_wave:
            if step.get("id") in skip:
                fire("subtask_skipped", id=step.get("id"), title=step.get("title"), writes=step.get("writes") or [])
                summaries.append(f"{step.get('title')}: done in the previous run (files on disk: {', '.join(step.get('writes') or []) or 'see workspace'})")
                completed.add(step.get("id"))
                continue
            wave.append(step)
        if not wave:
            continue

        def run_one(step: dict) -> tuple[str, bool]:
            return _run_subtask(
                client=client,
                model=model,
                goal=goal,
                assessment=assessment,
                critique=critique,
                plan=plan,
                step=step,
                previous=previous,
                tools=tools,
                searcher=searcher,
                include_server_tools=include_server_tools,
                spend=spend,
                fire=fire,
                budget=budget,
                cost_so_far=cost_so_far,
                ask_user=ask_user,
                llm=llm,
                browser=browser,
                memory_snapshot=memory_snapshot,
                worker_context=worker_context,
                summarize=summarize,
                mcp=mcp,
            )

        if len(wave) == 1:
            summary, done = run_one(wave[0])
            summaries.append(summary)
            if done:
                completed.add(wave[0].get("id"))
            continue
        with ThreadPoolExecutor(max_workers=min(4, len(wave))) as pool:
            futures = {pool.submit(run_one, step): step for step in wave}
            for future in as_completed(futures):
                summary, done = future.result()
                summaries.append(summary)
                if done:
                    completed.add(futures[future].get("id"))
    return "\n\n".join(s for s in summaries if s), completed


def _run_subtask(
    *,
    client,
    model,
    goal,
    assessment,
    critique,
    plan,
    step,
    previous,
    tools: WorkspaceTools,
    searcher: WebTools,
    include_server_tools: bool,
    spend,
    fire,
    budget: float,
    cost_so_far,
    ask_user=None,
    llm=None,
    browser=None,
    memory_snapshot: str = "",
    worker_context: int = DEFAULT_CONTEXT,
    summarize=None,
    mcp=None,
) -> tuple[str, bool]:
    fire("subtask_start", id=step.get("id"), title=step.get("title"), detail=step.get("detail"))
    specs = worker_tools(include_server_tools)
    if mcp is not None:
        specs = specs + mcp.tool_specs()
    messages: list[dict] = [
        {
            "role": "system",
            "content": _with_memory(
                "You execute this subtask only. You are a Rocky worker. Stay inside the workspace. "
                "Margins, break-even, discounts and promotions go through unit_economics; anything per customer over time — "
                "CLV, CAC, payback, churn, subscriptions, returns, channel ROI — goes through customer_economics; "
                "never do that arithmetic in prose, and when it reports a missing input ask with clarify or state the assumption. "
                "Any proposed A/B test gets its sample size, duration and decision rule from experiment_plan; a finished test is read out with its mode evaluate, never by comparing two percentages by eye. "
                "A CSV or XLSX of campaign, order or event data goes through funnel_analyze (stages) or repeat_buying "
                "(loyalty, cohorts, penetration, duplication, spikes) or table_analyze (any other count, share, mean, "
                "crosstab, before/after, period comparison or theme count); never read its percentages by eye. "
                "A channel comparison, media budget or combined reach goes through reach_overlap; never add reaches up yourself. "
                "Every ad or email that links to a page is checked against that page with page_inspect before you hand over; fix its blocks. "
                "SEO, titles, URLs, robots, sitemaps, structured data, rankings and search-term exports go through seo_inspect, never by eye. "
                "Survey or interview questions go through survey_check before they reach the deliverable; outcome statements and "
                "importance/satisfaction scores through outcome_scores; a concept test or criteria survey is designed with research_plan. "
                "Before you hand over a draft that states numbers, rankings or credentials, run claim_check on it; fix or remove what comes back unsupported. "
                + language_rule(str(assessment.get("language") or "en"), "worker") +
                "Do not do other workers' jobs. Use tools until YOUR step is done. "
                + LAYOUT_RULE +
                "Prefer patch for small file edits, write_file for new files; a long document goes in parts — "
                "write_file the first part (under ~8000 characters), append_file the rest. "
                "Use web_search/web_fetch for static pages; browser_navigate when the page is JS-heavy. "
                "Use todo to track steps. Use vision_analyze to look at images, image_generate to make them "
                "(ad visuals, hero images — PNG into deliverables/; write a one-line brief per image first). "
                "Use clarify only when you cannot proceed without the human. "
                "If the subtask names a skill, skill_view it first "
                "(deep-research, marketing-pipeline, voc-to-copy, copywriting, pptx, xlsx, …). "
                "For .xlsx .docx .pptx .pdf: skills_list then skill_view, then bash with openpyxl/python-docx/python-pptx/pypdf/reportlab — "
                "the `python3` on PATH already has them; never create or replace virtual environments (uv venv, uv run --python, python -m venv). "
                "Never write_file those binaries. Use office_read to inspect them. "
                "Cite sources. When the subtask is done, stop calling tools and summarize. "
                "Continue prior workspace work from MEMORY and RECENT RUNS. "
                "After learning a durable fact (path, convention, preference), call memory. "
                "Use runs_search if the user points at older work. "
                "If this kind of deliverable will recur and no recipe covers it, propose one with the recipe tool (action=save).",
                memory_snapshot,
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "goal": goal,
                    "reframed_goal": (critique or {}).get("reframed_goal"),
                    "quality_bar": (critique or {}).get("quality_bar"),
                    "success_criteria": assessment.get("success_criteria"),
                    "subtask": step,
                    "final_deliverable": plan.get("deliverable_path"),
                    "available_skills": [s["name"] for s in catalog_for_prompt()],
                    "verifier_feedback": previous or None,
                },
                ensure_ascii=False,
                indent=2,
            ),
        },
    ]
    last_text = ""
    guard = RepeatGuard()
    aborted = False
    nudges = 0
    expected = [str(w) for w in step.get("writes") or []]
    started_at = time.time()
    def run_calls(completion: Completion) -> None:
        """Append the assistant turn, run every tool call (guarded), append the results."""
        nonlocal aborted, last_text
        messages.append(
            {
                "role": "assistant",
                "content": completion.content or None,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                    for call in completion.tool_calls
                ],
            }
        )
        for call in completion.tool_calls:
            fire(
                "tool_call",
                name=call.name,
                arguments=_short_args(call.arguments),
                subtask=step.get("title"),
            )
            verdict = guard.check(call.name, call.arguments)
            if verdict == "abort":
                fire("loop_guard", action="abort", name=call.name, subtask=step.get("title"))
                result = "error: subtask aborted — the same call was repeated four times"
                aborted = True
                last_text = (
                    f"Aborted: {call.name} was called with identical arguments four times "
                    "without finishing. Work so far stays on disk."
                )
            elif verdict == "block":
                fire("loop_guard", action="block", name=call.name, subtask=step.get("title"))
                result = (
                    "error: this exact call already ran twice with the same arguments. "
                    "Do not repeat it — use the earlier result, change the arguments, "
                    "or stop and summarise."
                )
            else:
                try:
                    result = dispatch(
                        tools,
                        call.name,
                        call.arguments,
                        searcher=searcher,
                        ask_user=ask_user,
                        llm=llm,
                        browser=browser,
                        mcp=mcp,
                        spend=spend,
                    )
                except ToolError as exc:
                    result = f"error: {exc}"
            fire("tool_result", name=call.name, result=result[:2500], subtask=step.get("title"))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result[:20000],
                }
            )

    turns = 0
    warned_turns = False
    for turns in range(1, MAX_SUBTASK_TURNS + 1):
        if cost_so_far() >= budget or aborted:
            break
        left = MAX_SUBTASK_TURNS - turns + 1
        if left == TURNS_WARN_LEFT and not warned_turns and expected:
            missing_now = _missing_files(tools, expected, since=started_at)
            if missing_now:
                # A research step happily spends every turn on the web; say when the end is near.
                warned_turns = True
                fire("nudge", subtask=step.get("title"), missing=missing_now, n=nudges, reason="turns_left", left=left)
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Only {left} turns are left in this step and these files are still not written: "
                            + ", ".join(missing_now)
                            + ". Write them now from what you have (write_file, then append_file for long text); "
                            "search more only if turns remain after that."
                        ),
                    }
                )
        squeezed = compact(messages, context_length=worker_context, summarize=summarize)
        if squeezed:
            fire("compact", subtask=step.get("title"), **squeezed)
        completion = _chat(client, model=model, messages=messages, tools=specs)
        spend(completion)
        last_text = completion.content or last_text
        if not completion.tool_calls:
            missing = _missing_files(tools, expected, since=started_at)
            if missing and nudges < MAX_NUDGES:
                nudges += 1
                fire("nudge", subtask=step.get("title"), missing=missing, n=nudges)
                messages.append({"role": "assistant", "content": completion.content or "(no text)"})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "You stopped, but this step must write these files and they were not written "
                            "(missing, empty, or untouched since the step began): "
                            + ", ".join(missing)
                            + ". Write them now: write_file / append_file for text; for .pptx .xlsx .docx .pdf "
                            "run your script with bash (python3 runs/notes/make_x.py) — a script on disk is not "
                            "the deliverable until it has run and the file exists. Then summarise."
                        ),
                    }
                )
                continue
            break
        run_calls(completion)

    missing = _missing_files(tools, expected, since=started_at)
    if missing and turns >= MAX_SUBTASK_TURNS and not aborted and cost_so_far() < budget:
        # The turn cap ended the step mid-work — a research step spends its turns on fetches
        # and never reaches the write. What it found is still in the thread: one last pass,
        # writing tools only, before the step is declared incomplete.
        fire("nudge", subtask=step.get("title"), missing=missing, n=nudges + 1, reason="turns")
        messages.append(
            {
                "role": "user",
                "content": (
                    f"You are out of turns for this step ({MAX_SUBTASK_TURNS} used). Do not search, fetch, or "
                    "browse anything more. Write these files NOW from what you already found, even if "
                    "incomplete — note the gaps inside the file: " + ", ".join(missing)
                    + ". Use write_file (or bash for Office files) in this turn, then summarise in one paragraph."
                ),
            }
        )
        write_specs = [t for t in specs if t.get("function", {}).get("name") in WRITE_ONLY_TOOLS]
        for _ in range(FINAL_WRITE_TURNS):
            if aborted or cost_so_far() >= budget:
                break
            squeezed = compact(messages, context_length=worker_context, summarize=summarize)
            if squeezed:
                fire("compact", subtask=step.get("title"), **squeezed)
            completion = _chat(client, model=model, messages=messages, tools=write_specs)
            spend(completion)
            last_text = completion.content or last_text
            if not completion.tool_calls:
                break
            run_calls(completion)
    missing = _missing_files(tools, expected, since=started_at)
    if missing:
        last_text = (
            f"INCOMPLETE — did not write {', '.join(missing)}. "
            "Later steps must not pretend these exist. " + (last_text or "")
        )
        fire("subtask_incomplete", id=step.get("id"), title=step.get("title"), missing=missing)
    done = bool(expected) and not missing and not aborted and bool(last_text)
    fire("subtask_done", id=step.get("id"), title=step.get("title"), text=(last_text or "")[:2000])
    if last_text:
        fire("worker", text=last_text[:4000], subtask=step.get("title"))
    return f"{step.get('title')}: {last_text[:WORKER_SUMMARY_CHARS]}", done


def _missing_files(tools: WorkspaceTools, expected: list[str], since: float | None = None) -> list[str]:
    """Declared outputs that are absent, empty, or (when `since` is given) untouched since then."""
    missing = []
    for rel in expected:
        try:
            path = tools.resolve(rel)
        except ToolError:
            missing.append(rel)
            continue
        if not path.is_file() or path.stat().st_size == 0:
            missing.append(rel)
            continue
        if since is not None and path.stat().st_mtime < since - 1.0:
            missing.append(rel)
    return missing


def _verify(
    client,
    model,
    goal,
    assessment,
    critique,
    plan,
    worker_summary,
    tools,
    spend,
    fire,
    memory_snapshot: str = "",
    max_turns: int = MAX_VERIFY_TURNS,
) -> dict:
    listing = tools.list_dir(".")
    deliverable = plan.get("deliverable_path") or assessment.get("deliverable") or ""
    body = ""
    brief = verifier_brief(tools) if is_board(plan) else None
    if brief is not None:
        body = brief["pack"]
    elif deliverable:
        try:
            body = tools.read_file(deliverable)
        except ToolError as exc:
            body = f"(could not read {deliverable}: {exc})"
    messages: list[dict] = [
        {
            "role": "system",
            "content": _with_memory(
                "You verify completed work against the original goal and the critic's quality bar. You are Rocky's verifier. "
                "Judge the WHOLE result, not one subtask. Be exact and fair. "
                + language_rule(str(assessment.get("language") or "en"), "verifier") +
                "Sort what you find into two lists. blocking: defects that make the deliverable unusable for the goal "
                "as written — the file is missing or empty, wrong language, wrong format, a required part absent, "
                "core content wrong or invented. issues: everything else — polish, style, headings, missing citations "
                "the user never asked for, extra rigor. passed=true when the deliverable exists and blocking is empty; "
                "issues alone never fail a run, they are handed to the user as notes. "
                "Judge against the goal the user wrote, not against a stricter reading of it. "
                "usable=true when the deliverable exists and a person could use it for the goal after fixing the "
                "blocking items themselves in under an hour; false when it has to be redone. "
                "score is the share of the goal delivered: 1.0 every criterion met; 0.7–0.9 usable with notes; "
                "0.4–0.6 a major part missing or wrong; below 0.3 only when nothing usable exists. A complete "
                "package with one wrong claim is 0.6–0.8, never 0. "
                "You have read-only tools: read_file, list_dir, grep, office_read for .pptx/.xlsx/.docx/.pdf, "
                "and claim_check, which matches every number, ranking and credential in a draft to the sources on disk. "
                "Run claim_check on the deliverable when it states figures; an unsupported or differing claim is an issue. "
                "Open the deliverable yourself when the excerpt is not enough — never trust the worker summary alone. "
                "When you have seen enough, stop calling tools and return JSON only. "
                "Follow-ups may complete prior work named in MEMORY or RECENT RUNS.",
                memory_snapshot,
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "goal": goal,
                    "reframed_goal": (critique or {}).get("reframed_goal"),
                    "quality_bar": (critique or {}).get("quality_bar"),
                    "success_criteria": assessment.get("success_criteria"),
                    "plan": plan,
                    "workspace_listing": listing,
                    "deliverable_path": deliverable,
                    "deliverable_excerpt": body[:12000],
                    "worker_summary": worker_summary,
                    **({"automatic_checks": brief["automatic_checks"]} if brief else {}),
                },
                ensure_ascii=False,
                indent=2,
            ),
        },
    ]
    completion = None
    for _ in range(max_turns):
        completion = _chat(
            client, model=model, messages=messages, tools=VERIFY_TOOL_SPECS, json_schema=VERIFY_SCHEMA
        )
        spend(completion)
        if not completion.tool_calls:
            break
        messages.append(
            {
                "role": "assistant",
                "content": completion.content or None,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                    }
                    for call in completion.tool_calls
                ],
            }
        )
        for call in completion.tool_calls:
            fire("tool_call", name=call.name, arguments=_short_args(call.arguments), subtask="verifier")
            if call.name not in VERIFY_TOOL_NAMES:
                result = f"error: verifier may only use {', '.join(sorted(VERIFY_TOOL_NAMES))}"
            else:
                try:
                    result = dispatch(tools, call.name, call.arguments)
                except ToolError as exc:
                    result = f"error: {exc}"
            fire("tool_result", name=call.name, result=result[:2500], subtask="verifier")
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result[:VERIFY_TOOL_CHARS]})
    else:
        # Out of tool turns: one last call, no tools, JSON only.
        messages.append({"role": "user", "content": "No more tool calls. Return the verification JSON now."})
        completion = _chat(client, model=model, messages=messages, json_schema=VERIFY_SCHEMA)
        spend(completion)

    content = (completion.content if completion else "") or ""
    if not _looks_like_verdict(content):
        # Tools + response_format do not mix on every provider; ask once more, JSON only.
        messages.append({"role": "assistant", "content": content or None})
        messages.append(
            {
                "role": "user",
                "content": "Return ONLY the verification JSON with keys passed, score, summary, issues, remaining.",
            }
        )
        completion = _chat(client, model=model, messages=messages, json_schema=VERIFY_SCHEMA)
        spend(completion)
        content = completion.content or content
    try:
        parsed = parse_json_object(content)
    except JsonParseError:
        parsed = None
    if parsed is None:
        verify = {
            "passed": False,
            "score": 0.0,
            "summary": content[:1000] or "Verifier returned no JSON.",
            "issues": ["Unparseable verifier output"],
            "remaining": [],
        }
    else:
        verify = {"passed": False, "score": 0.0, "summary": "", "issues": [], "remaining": []}
        verify.update(parsed)
    verify = _normalize_verdict(verify)
    if _notes_only(verify) and deliverable and not _missing_files(tools, [deliverable]):
        # The verifier faulted nothing blocking but did not say passed: that is a pass with notes,
        # not another loop of polish the user did not ask for.
        verify["passed"] = True
        verify["notes_only"] = True
    fire("verify", **verify)
    return verify


def _notes_only(verify: dict) -> bool:
    """A verdict that names no blocking defect and still withholds passed."""
    if verify.get("passed") or "blocking" not in verify:
        return False
    if verify.get("blocking"):
        return False
    try:
        score = float(verify.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    return score >= NOTES_MIN_SCORE


def _looks_like_verdict(content: str) -> bool:
    try:
        parsed = parse_json_object(content)
    except JsonParseError:
        return False
    return "passed" in parsed and ("summary" in parsed or "reason" in parsed)


def _normalize_verdict(verify: dict) -> dict:
    """Tolerate near-schema answers: reason → summary, missing score, stringy booleans."""
    if verify.get("reason"):
        verify["summary"] = str(verify.pop("reason"))
    raw = verify.get("passed")
    if isinstance(raw, str):
        verify["passed"] = raw.strip().lower() in {"true", "yes", "pass", "passed"}
    verify["passed"] = bool(verify.get("passed"))
    try:
        score = float(verify.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    if score <= 0 and verify["passed"]:
        score = 1.0
    verify["score"] = round(min(max(score, 0.0), 1.0), 2)
    if "usable" in verify:
        raw_usable = verify.get("usable")
        if isinstance(raw_usable, str):
            raw_usable = raw_usable.strip().lower() in {"true", "yes", "1"}
        verify["usable"] = bool(raw_usable)
    for key in ("blocking", "issues", "remaining"):
        if key == "blocking" and key not in verify:
            continue
        value = verify.get(key)
        if isinstance(value, str):
            verify[key] = [value] if value.strip() else []
        elif not isinstance(value, list):
            verify[key] = []
        else:
            verify[key] = [_issue_text(v) for v in value if _issue_text(v)]
    return verify


def _issue_text(item: object) -> str:
    """A verifier issue as one line. Models sometimes return {path, description} objects."""
    if isinstance(item, dict):
        where = str(item.get("path") or item.get("file") or item.get("where") or "").strip()
        what = str(item.get("description") or item.get("issue") or item.get("detail") or item.get("text") or "").strip()
        if not what:
            what = "; ".join(f"{k}: {v}" for k, v in item.items() if k not in {"path", "file", "where"})
        return f"{where}: {what}" if where and what else (what or where)
    return str(item).strip()


def _short_args(args: dict) -> dict:
    out = {}
    for key, value in (args or {}).items():
        text = value if isinstance(value, str) else json.dumps(value)
        out[key] = text[:400] + ("…" if len(text) > 400 else "")
    return out
