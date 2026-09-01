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
        what = "replanning from the last verdict" if payload.get("replan") else (
            f"skipping finished steps {done}" if done else "redoing every step"
        )
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
        console.print(
            Text(
                f"assess  {payload.get('complexity')} · {payload.get('domain')}{extra} · {payload.get('goal')}",
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
        console.print(
            Panel(
                f"{payload.get('summary')}\nscore {payload.get('score')}\n"
                + "\n".join(payload.get("issues") or []),
                title=f"verify · {mark}",
                border_style=color,
            )
        )
        return
    if kind == "done":
        status = str(payload.get("status") or ("passed" if payload.get("passed") else "unmet"))
        mark = "done · " + status.replace("_", " ")
        console.rule(f"{mark}  ·  {payload.get('loops')} loops")
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
    console.print(f"[dim]{kind}[/] {payload}")
