from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rocky.catalog import Cast, Model, parse_models, pick_cast
from rocky.config import preferred_worker
from rocky.events import Emit, Event
from rocky.jsonutil import JsonParseError, parse_json_object
from rocky.extras import BrowserSession
from rocky.llm import Completion, LLM, WebTools
from rocky.usage import (
    DEFAULT_BUDGET_EUR,
    Usage,
    estimate_run_eur,
    eur_from_usd,
    fetch_usd_eur_rate,
    format_receipt,
    needs_budget_lift,
    parse_budget_reply,
    suggest_cap_eur,
    usd_from_eur,
)
from rocky.memory import freeze_memory, recent_runs_block, snapshot_event_payload
from rocky.pipeline import ensure_pipeline
from rocky.skills import catalog_for_prompt
from rocky.tools import (
    ToolError,
    WorkspaceTools,
    dispatch,
    worker_tools,
)
from rocky.waves import schedule_waves

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
        },
        "required": [
            "goal",
            "complexity",
            "domain",
            "success_criteria",
            "needs_web",
            "deliverable",
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
                    },
                    "required": ["id", "title", "detail", "depends_on"],
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
            "issues": {"type": "array", "items": {"type": "string"}},
            "remaining": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["passed", "score", "summary", "issues", "remaining"],
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
) -> RunResult:
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
    tally = Usage()
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

    def spend(completion: Completion) -> None:
        with event_lock:
            tally.add(completion.usage)
            bill = tally.payload(rate)
            receipt = format_receipt(tally, rate)
        fire("cost", model=completion.model, receipt=receipt, **bill)

    # Cheap default cast for assess model id; refine after assessment.
    draft = pick_cast(models, "moderate", "mixed", preferred_worker=preferred_worker())
    assess_raw = _chat(
        client,
        model=draft.assess,
        system=(
            "You assess tasks for Rocky. "
            "Return JSON only. Judge complexity honestly. "
            "trivial = one short step; moderate = a few steps; hard = research, code, or multi-file work. "
            "domain: code | research | writing | marketing | ops | mixed. "
            "marketing = ads, copy, campaigns, landing pages, VOC, SEO, CRO."
        ),
        user=_assess_user(goal, workspace_path),
        json_schema=ASSESS_SCHEMA,
    )
    spend(assess_raw)
    assessment = _safe_json(
        assess_raw.content,
        {
            "goal": goal,
            "complexity": "moderate",
            "domain": "mixed",
            "success_criteria": ["The user's request is fully completed."],
            "needs_web": True,
            "deliverable": "output.md",
        },
    )
    assessment["goal"] = assessment.get("goal") or goal
    estimate_eur = estimate_run_eur(assessment, goal)
    assessment["estimated_eur"] = estimate_eur
    fire("assessed", **{k: assessment[k] for k in assessment})

    budget_eur = float(budget_eur if budget is None else eur_from_usd(usd_cap, rate))
    usd_cap, budget_eur = _maybe_lift_budget(
        assessment=assessment,
        estimate_eur=estimate_eur,
        budget_eur=budget_eur,
        usd_cap=usd_cap,
        rate=rate,
        ask_user=ask_user,
        fire=fire,
    )

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

    critique = _critique(
        client, cast.critic, goal, assessment, spend, fire, memory_snapshot=memory_snapshot
    )
    plan = _make_plan(
        client,
        cast.planner,
        goal,
        assessment,
        workspace_path,
        spend,
        fire,
        critique=critique,
        memory_snapshot=memory_snapshot,
    )
    worker_summary = ""
    passed = False
    verify: dict[str, Any] = {}
    loops = 0

    for loops in range(1, max_loops + 1):
        if tally.usd >= usd_cap:
            fire("budget", cap=round(usd_cap, 3), receipt=format_receipt(tally, rate), **tally.payload(rate))
            break
        fire("loop", n=loops, max=max_loops)
        worker_summary = _run_subagents(
            client=client,
            model=cast.worker,
            goal=goal,
            assessment=assessment,
            critique=critique,
            plan=plan,
            previous=verify,
            tools=tools,
            searcher=searcher,
            include_server_tools=include_server_tools,
            spend=spend,
            fire=fire,
            budget=usd_cap,
            cost_so_far=lambda: tally.usd,
            ask_user=ask_user,
            llm=client,
            browser=browser,
            memory_snapshot=memory_snapshot,
        )
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
                spend,
                fire,
                memory_snapshot=memory_snapshot,
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
        if passed:
            break
        plan = _make_plan(
            client,
            cast.planner,
            goal,
            assessment,
            workspace_path,
            spend,
            fire,
            critique=critique,
            revision=verify,
            memory_snapshot=memory_snapshot,
        )

    summary = (verify.get("summary") if verify else worker_summary) or ""
    bill = tally.payload(rate)
    fire(
        "done",
        passed=passed,
        loops=loops,
        summary=summary,
        receipt=format_receipt(tally, rate),
        **bill,
    )
    try:
        browser.close()
    except Exception:
        pass
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
    )


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
    question = (
        f"This task looks {complexity} ({domain}). "
        f"Rough spend ~€{estimate_eur:.2f}; current cap is €{budget_eur:.2f}. "
        f"Raise THIS RUN only to €{suggested:.2f}? "
        f"Reply yes / keep / or a number."
    )
    options = [
        f"yes — €{suggested:.2f} this run only",
        f"keep €{budget_eur:.2f}",
        "or type a number, e.g. 3",
    ]
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


def _critique(client, model, goal, assessment, spend, fire, memory_snapshot: str = "") -> dict:
    completion = _chat(
        client,
        model=model,
        messages=[
            {
                "role": "system",
                "content": _with_memory(
                    "You critique briefs for Rocky. Be strict. "
                    "Point out missing constraints, hidden risks, and the quality bar. "
                    "Reframe the goal so a worker cannot hide behind a vague prompt. "
                    "Suggest how to split the work. Return JSON only. Do not refuse the task. "
                    "If MEMORY or RECENT RUNS name a deliverable, treat follow-up goals as continuations.",
                    memory_snapshot,
                ),
            },
            {
                "role": "user",
                "content": json.dumps({"goal": goal, "assessment": assessment}, ensure_ascii=False, indent=2),
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
        steps.append(
            {
                "id": ident,
                "title": str(raw.get("title") or f"Step {i}"),
                "detail": str(raw.get("detail") or ""),
                "depends_on": deps,
            }
        )
    if not steps:
        steps = [{"id": 1, "title": "Complete the goal", "detail": goal, "depends_on": []}]
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
) -> dict:
    listing = ""
    try:
        listing = WorkspaceTools(workspace).list_dir(".")
    except ToolError:
        listing = ""
    user = {
        "goal": goal,
        "assessment": assessment,
        "brief_critique": critique,
        "workspace_files": listing,
        "revision_from_verifier": revision,
        "available_skills": catalog_for_prompt(),
        "marketing_pipeline": (
            "If domain is marketing or the goal is ads/copy/campaign/landing/VOC/SEO: "
            "MUST split research → VOC → copy → pptx+xlsx. "
            "Workers skill_view deep-research, voc-to-copy, copywriting, pptx, xlsx in that order. "
            "Independent research may run in parallel; VOC waits on research; copy waits on VOC; "
            "deck and spreadsheet wait on copy."
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
                    "Split work into subtasks that can be given to separate workers. "
                    "Use depends_on so independent research can run in parallel, "
                    "and writing/assembly waits on research. Each step must be concrete. "
                    "If the deliverable is xlsx/docx/pptx/pdf, that worker step must skill_view the matching skill. "
                    "Marketing/ads/copy/campaign tasks MUST be four waves: "
                    "1 research (skill_view deep-research) → 2 VOC (skill_view voc-to-copy) → "
                    "3 copy (skill_view copywriting plus channel skill) → "
                    "4 pptx and xlsx (skill_view pptx then xlsx). Do not skip a wave. "
                    "Name the final deliverable_path. Workers are fully autonomous in the workspace. "
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
            "deliverable_path": assessment.get("deliverable") or "output.md",
            "notes": "",
        },
    )
    plan = _normalize_steps(plan, goal)
    plan = ensure_pipeline(plan, goal, assessment)
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
) -> str:
    summaries: list[str] = []
    for wave in schedule_waves(plan.get("steps") or []):
        if cost_so_far() >= budget:
            break

        def run_one(step: dict) -> str:
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
            )

        if len(wave) == 1:
            summaries.append(run_one(wave[0]))
            continue
        with ThreadPoolExecutor(max_workers=min(4, len(wave))) as pool:
            futures = [pool.submit(run_one, step) for step in wave]
            for future in as_completed(futures):
                summaries.append(future.result())
    return "\n\n".join(s for s in summaries if s)


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
) -> str:
    fire("subtask_start", id=step.get("id"), title=step.get("title"), detail=step.get("detail"))
    specs = worker_tools(include_server_tools)
    messages: list[dict] = [
        {
            "role": "system",
            "content": _with_memory(
                "You execute this subtask only. You are a Rocky worker. Stay inside the workspace. "
                "Do not do other workers' jobs. Use tools until YOUR step is done. "
                "Prefer patch for small file edits, write_file for new files. "
                "Use web_search/web_fetch for static pages; browser_navigate when the page is JS-heavy. "
                "Use todo to track steps. Use vision_analyze for images. "
                "Use clarify only when you cannot proceed without the human. "
                "If the subtask names a skill, skill_view it first "
                "(deep-research, marketing-pipeline, voc-to-copy, copywriting, pptx, xlsx, …). "
                "For .xlsx .docx .pptx .pdf: skills_list then skill_view, then bash with openpyxl/python-docx/python-pptx/pypdf/reportlab. Never write_file those binaries. Use office_read to inspect them. "
                "Cite sources. When the subtask is done, stop calling tools and summarize. "
                "Continue prior workspace work from MEMORY and RECENT RUNS. "
                "After learning a durable fact (path, convention, preference), call memory. "
                "Use runs_search if the user points at older work.",
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
    for _ in range(12):
        if cost_so_far() >= budget:
            break
        completion = _chat(client, model=model, messages=messages, tools=specs)
        spend(completion)
        last_text = completion.content or last_text
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
            try:
                result = dispatch(
                    tools,
                    call.name,
                    call.arguments,
                    searcher=searcher,
                    ask_user=ask_user,
                    llm=llm,
                    browser=browser,
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
    fire("subtask_done", id=step.get("id"), title=step.get("title"), text=(last_text or "")[:2000])
    if last_text:
        fire("worker", text=last_text[:4000], subtask=step.get("title"))
    return f"{step.get('title')}: {last_text}"


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
) -> dict:
    listing = tools.list_dir(".")
    deliverable = plan.get("deliverable_path") or assessment.get("deliverable") or ""
    body = ""
    if deliverable:
        try:
            body = tools.read_file(deliverable)
        except ToolError as exc:
            body = f"(could not read {deliverable}: {exc})"
    completion = _chat(
        client,
        model=model,
        messages=[
            {
                "role": "system",
                "content": _with_memory(
                    "You verify completed work against the original goal and the critic's quality bar. You are Rocky's verifier. "
                    "Judge the WHOLE result, not one subtask. Be strict. "
                    "passed=true only if the deliverable exists and meets every success criterion. "
                    "Return JSON only. Follow-ups may complete prior work named in MEMORY or RECENT RUNS.",
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
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            },
        ],
        json_schema=VERIFY_SCHEMA,
    )
    spend(completion)

    verify = _safe_json(
        completion.content,
        {
            "passed": False,
            "score": 0.0,
            "summary": completion.content[:1000] or "Verifier returned no JSON.",
            "issues": ["Unparseable verifier output"],
            "remaining": [],
        },
    )
    fire("verify", **verify)
    return verify


def _short_args(args: dict) -> dict:
    out = {}
    for key, value in (args or {}).items():
        text = value if isinstance(value, str) else json.dumps(value)
        out[key] = text[:400] + ("…" if len(text) > 400 else "")
    return out
