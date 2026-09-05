from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from rocky.events import Event
from rocky.theme import APRICOT, CORAL, FAIL, INK, OK, PERI, SEA

console = Console()

ROLE_STYLE = {
    "assessed": f"italic #{APRICOT}",
    "cast": f"bold #{CORAL}",
    "critique": f"italic #{PERI}",
    "plan": f"#{CORAL}",
    "subtask_start": f"#{SEA}",
    "subtask_done": f"dim #{SEA}",
    "tool_call": f"#{PERI}",
    "tool_result": "dim",
    "worker": f"#{SEA}",
    "verify": f"#{PERI}",
    "done": f"bold #{INK}",
    "cost": "dim",
    "loop": f"dim #{CORAL}",
    "run_started": "bold",
    "catalog": "dim",
    "budget": f"bold #{CORAL}",
}


def render_event(event: Event) -> None:
    payload = event.payload
    kind = event.type
    style = ROLE_STYLE.get(kind, "")

    if kind == "run_started":
        console.rule(f"[italic bold #{CORAL}]rocky[/]")
        console.print(f"[bold]Goal[/]  {payload.get('goal')}")
        console.print(
            f"[dim]{payload.get('workspace')}  ·  budget €{payload.get('budget_eur', payload.get('budget'))}  ·  {payload.get('max_loops')} loops[/]"
        )
        return
    if kind == "resumed":
        done = payload.get("done_steps") or []
        if payload.get("follow_up"):
            what = f"follow-up on its result: {payload['follow_up']}"
        elif payload.get("replan"):
            what = "replanning from the last verdict"
        else:
            what = f"skipping finished steps {done}" if done else "redoing every step"
        spent = f"  ·  €{payload.get('eur_before')} spent before" if payload.get("eur_before") is not None else ""
        console.print(
            Text(f"resume  from {payload.get('from_run')} · {what}{spent}", style=f"bold #{APRICOT}")
        )
        return
    if kind == "subtask_skipped":
        console.print(Text(f"skipped  {payload.get('id')} · {payload.get('title')} (done in previous run)", style="dim"))
        return
    if kind == "assessed":
        extra = ""
        if payload.get("estimated_eur") is not None:
            extra = f"  ·  ~€{payload.get('estimated_eur')}"
            cal = payload.get("calibration") or {}
            if cal.get("samples") and cal.get("factor") not in (None, 1.0):
                extra += f" (×{cal['factor']} from {cal['samples']} past runs)"
        console.print(
            Text(
                f"assess  {payload.get('complexity')} · {payload.get('domain')}"
                f"{' · ' + str(payload['language']) if payload.get('language') else ''}{extra} · {payload.get('goal')}",
                style=style,
            )
        )
        return
    if kind == "budget_offer":
        console.print(
            Panel(
                f"looks {payload.get('complexity')} / {payload.get('domain')}\n"
                f"rough spend  €{payload.get('estimated_eur')}   current cap  €{payload.get('current_eur')}\n"
                f"suggest THIS RUN  €{payload.get('suggested_eur')}\n"
                "yes / keep / or a number — default cap stays €1 for the next task",
                title="budget",
                border_style=f"#{CORAL}",
            )
        )
        return
    if kind == "budget_set":
        verb = "raised" if payload.get("lifted") else "kept"
        console.print(
            Text(
                f"budget {verb}  €{payload.get('budget_eur')}  this run",
                style=f"bold #{CORAL}",
            )
        )
        return
    if kind == "budget_warn":
        if payload.get("stage") == "half":
            console.print(
                Text(
                    f"budget  €{payload.get('spent_eur')} of €{payload.get('budget_eur')} spent, "
                    f"{payload.get('deliverable') or 'deliverable'} not on disk yet",
                    style="bold red",
                )
            )
            return
        console.print(
            Text(
                f"budget  estimate €{payload.get('estimated_eur')} > cap €{payload.get('budget_eur')}  (no human — keeping cap)",
                style="bold red",
            )
        )
        return
    if kind == "cast":
        models = payload.get("models") or {}
        reasons = payload.get("reasons") or {}
        body = "\n".join(
            f"[bold]{role}[/]  {models.get(role)}\n[dim]{reasons.get(role, '')}[/]"
            for role in ("critic", "planner", "worker", "verifier")
        )
        console.print(Panel(body, title="cast", border_style=f"#{CORAL}"))
        return
    if kind == "critique":
        lines = [
            f"clarity  {payload.get('clarity')}",
            f"bar  {payload.get('quality_bar')}",
            f"reframe  {payload.get('reframed_goal')}",
        ]
        for miss in payload.get("missing") or []:
            lines.append(f"missing  {miss}")
        for risk in payload.get("risks") or []:
            lines.append(f"risk  {risk}")
        console.print(Panel("\n".join(lines), title="brief critic", border_style=f"#{PERI}"))
        return
    if kind == "subtask_start":
        console.print(Text(f"subagent  {payload.get('id')} · {payload.get('title')}", style=style))
        return
    if kind == "subtask_done":
        console.print(Text(f"subagent done  {payload.get('title')}", style=style))
        return
    if kind == "plan":
        steps = payload.get("steps") or []
        lines = [str(payload.get("summary") or "")]
        for step in steps:
            lines.append(f"{step.get('id')}. {step.get('title')} — {step.get('detail')}")
        lines.append(f"deliverable: {payload.get('deliverable_path')}")
        console.print(Panel("\n".join(lines), title="plan", border_style=f"#{CORAL}"))
        return
    if kind == "tool_call":
        args = payload.get("arguments") or {}
        preview = ", ".join(f"{k}={v}" for k, v in list(args.items())[:3])
        console.print(Text(f"→ {payload.get('name')}  {preview}", style=style))
        return
    if kind == "tool_result":
        snippet = str(payload.get("result") or "").splitlines()
        first = snippet[0] if snippet else ""
        console.print(Text(f"← {payload.get('name')}  {first[:180]}", style=style))
        return
    if kind == "verify":
        mark = "pass" if payload.get("passed") else "fail"
        color = f"#{OK}" if payload.get("passed") else f"#{FAIL}"
        lines = [f"{payload.get('summary')}", f"score {payload.get('score')}"]
        lines += [f"blocking  {b}" for b in payload.get("blocking") or []]
        prefix = "note  " if "blocking" in payload else ""
        lines += [f"{prefix}{i}" for i in payload.get("issues") or []]
        if payload.get("notes_only"):
            mark = "pass with notes"
        console.print(Panel("\n".join(lines), title=f"verify · {mark}", border_style=color))
        return
    if kind == "done":
        status = str(payload.get("status") or ("passed" if payload.get("passed") else "unmet"))
        mark = "done · " + status.replace("_", " ")
        console.rule(f"{mark}  ·  {payload.get('loops')} loops")
        for line in handover_lines(payload):
            console.print(line)
        if payload.get("notes"):
            console.print(
                Panel(
                    "\n".join(f"- {n}" for n in payload["notes"]),
                    title="passed with notes — what is still open",
                    border_style=f"#{APRICOT}",
                )
            )
        receipt = payload.get("receipt") or f"${payload.get('usd')}"
        if payload.get("role_receipt"):
            receipt = f"{receipt}\n{payload['role_receipt']}"
        console.print(
            Panel(
                receipt,
                title="price",
                border_style=f"#{OK}",
            )
        )
        if payload.get("summary"):
            console.print(payload["summary"])
        return
    if kind == "cost":
        console.print(Text(f"price  {payload.get('receipt')}", style=style))
        return
    if kind == "worker":
        console.print(Text(str(payload.get("text") or "")[:500], style=style))
        return
    if kind == "loop":
        console.print(Text(f"loop {payload.get('n')}/{payload.get('max')}", style=style))
        return
    if kind == "stall":
        console.print(
            Text(
                f"stall  {payload.get('failed')} failed checks in a row · worker "
                f"{payload.get('previous_worker')} → {payload.get('worker')} · plan kept",
                style=f"#{APRICOT}",
            )
        )
        return
    if kind == "error":
        console.print(Text(f"error  {payload.get('message') or payload}", style=f"bold #{FAIL}"))
        return
    if kind == "nudge":
        missing = ", ".join(str(m) for m in payload.get("missing") or [])
        if payload.get("reason") == "turns":
            text = f"nudge  {payload.get('subtask')} ran out of turns before writing {missing or 'its files'} · last pass, writing only"
        elif payload.get("reason") == "turns_left":
            text = f"nudge  {payload.get('subtask')} has {payload.get('left')} turns left and {missing or 'its files'} unwritten · told to write now"
        else:
            text = f"nudge  {payload.get('subtask')} stopped without writing {missing or 'its files'} · asked again ({payload.get('n')})"
        console.print(Text(text, style=f"#{APRICOT}"))
        return
    if kind == "subtask_incomplete":
        missing = ", ".join(str(m) for m in payload.get("missing") or [])
        console.print(
            Text(f"incomplete  {payload.get('title')} · never wrote {missing or 'its files'}", style=f"bold #{APRICOT}")
        )
        return
    if kind == "compact":
        console.print(
            Text(
                f"compact  {payload.get('subtask')} · {payload.get('mode') or 'prune'} · "
                f"{payload.get('before_tokens')} → {payload.get('after_tokens')} tokens",
                style="dim",
            )
        )
        return
    if kind == "loop_guard":
        verb = "aborted" if payload.get("action") == "abort" else "blocked"
        console.print(
            Text(f"loop guard  {verb} repeated {payload.get('name')} in {payload.get('subtask')}", style=f"#{APRICOT}")
        )
        return
    if kind == "no_progress":
        console.print(Text(f"stopping  {payload.get('reason')} · €{payload.get('spent_eur')} spent", style=f"bold #{APRICOT}"))
        return
    if kind == "budget":
        stage = f" ({payload.get('stage')})" if payload.get("stage") else ""
        console.print(Text(f"budget  cap ${payload.get('cap')} reached{stage} · stopping", style=style))
        return
    if kind == "mcp":
        started = ", ".join(str(n) for n in payload.get("started") or []) or "none"
        failures = payload.get("failures") or {}
        text = f"mcp  servers {started} · {payload.get('tools') or 0} tools"
        if failures:
            text += " · failed: " + ", ".join(f"{k} ({v})" for k, v in failures.items())
        console.print(Text(text, style="dim"))
        return
    if kind == "memory":
        console.print(
            Text(
                f"memory  {payload.get('recent_runs') or 0} recent runs · "
                f"USER.md {payload.get('user_chars') or 0} chars · MEMORY.md {payload.get('memory_chars') or 0} chars",
                style="dim",
            )
        )
        return
    if kind == "catalog":
        if payload.get("status") == "ok":
            console.print(Text(f"catalog  {payload.get('count')} models", style="dim"))
        return
    console.print(f"[dim]{kind}[/] {payload}")


def handover_lines(payload: dict) -> list[str]:
    """The lines a person needs at the end: where the result is, or what is there instead.

    Rich markup strings. Shared by the CLI renderer and the attach tail.
    """
    deliverable = str(payload.get("deliverable_path") or "")
    exists = bool(payload.get("deliverable_exists"))
    files = [str(f) for f in payload.get("files") or []]
    status = str(payload.get("status") or ("passed" if payload.get("passed") else "unmet"))
    out: list[str] = []
    if exists:
        out.append(f"[bold #{OK}]result[/]  {deliverable}")
        others = [f for f in files if f != deliverable]
        if others:
            out.append(f"[dim]also written  {', '.join(others[:8])}{' …' if len(others) > 8 else ''}[/]")
        return out
    if deliverable:
        out.append(f"[bold #{FAIL}]no result[/]  {deliverable} was never written")
    elif files:
        out.append(f"[bold #{FAIL}]no result[/]  the plan named no deliverable")
    else:
        out.append(f"[bold #{FAIL}]no result[/]  nothing was written")
    if files:
        out.append(f"[#{APRICOT}]partial work[/]  {', '.join(files[:8])}{' …' if len(files) > 8 else ''}")
    if status not in {"passed", "passed_with_notes"}:
        out.append(f"[dim]continue with  rocky resume{('  ' + str(payload['run_id'])) if payload.get('run_id') else ''}[/]")
    return out
