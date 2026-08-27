from __future__ import annotations

import argparse
import select
import sys
import time
from pathlib import Path

from rocky.catalog import parse_models, pick_cast
from rocky.config import api_key, base_url
from rocky.job import spawn_worker
from rocky.jsonutil import parse_json_object
from rocky.llm import OpenRouterClient
from rocky.runs import (
    TERMINAL,
    EventCursor,
    create_run,
    list_runs,
    read_ask,
    refresh_meta,
    resolve_run_id,
    run_dir,
    write_answer,
)
from rocky.usage import DEFAULT_BUDGET_EUR
from rocky.view import console, render_event


def help_path() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in (here / "HELP.md", here.parent / "HELP.md"):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("HELP.md is missing from the rocky install")


def _help() -> int:
    text = help_path().read_text(encoding="utf-8")
    if sys.stdout.isatty():
        from rich.markdown import Markdown

        console.print(Markdown(text))
    else:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
    return 0


def prompt_goal(label: str = "Užduotis") -> str:
    try:
        line = input(f"{label}: ").strip()
    except EOFError:
        return ""
    if line.lower() in {"q", "quit", "exit"}:
        return ""
    return line


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    commands = {"run", "tui", "serve", "route", "ps", "attach", "help", "_job"}
    if argv and argv[0] in commands:
        command = argv[0]
        rest = argv[1:]
    else:
        command = "run"
        rest = argv

    parser = argparse.ArgumentParser(prog="rocky", add_help=True)
    parser.add_argument("goal", nargs="*", help="What to accomplish, or a run id for attach")
    parser.add_argument("-d", "--dir", default=".", help="Workspace folder")
    parser.add_argument("--attach", default="", help="TUI: attach to an existing run id")
    parser.add_argument(
        "--budget",
        type=float,
        default=DEFAULT_BUDGET_EUR,
        help="Max EUR for the run (default 1.00)",
    )
    parser.add_argument("--max-loops", type=int, default=4, dest="max_loops")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-server-tools", action="store_true")
    args = parser.parse_args(rest)
    goal = " ".join(args.goal).strip()
    workspace = Path(args.dir).expanduser().resolve()

    if command == "serve":
        from rocky.webapp import ServeRefused, serve

        try:
            serve(host=args.host, port=args.port)
        except ServeRefused as exc:
            console.print(f"[red]{exc}[/]")
            return 2
        return 0
    if command == "help":
        return _help()
    if command == "ps":
        return _ps(workspace)
    if command == "_job":
        from rocky.job import main as job_main

        return job_main([goal, "--dir", str(workspace)])

    if command == "attach":
        try:
            run_id = resolve_run_id(workspace, goal or None)
        except FileNotFoundError as exc:
            console.print(f"[red]{exc}[/]")
            return 1
        return _attach(workspace, run_id)

    key = api_key()
    if not key:
        console.print("[red]OPENROUTER_API_KEY is missing.[/] Put it in .env or ~/.env")
        return 2

    client = OpenRouterClient(key, base_url())
    try:
        if sys.stdout.isatty() and command not in {"ps", "attach", "help", "_job"}:
            from rocky.banner import print_banner

            print_banner(
                workspace=workspace,
                budget_eur=args.budget,
                interactive=not goal and command != "tui",
            )
        if command == "tui":
            from rocky.tui import run_tui

            run_tui(
                goal=goal,
                workspace=workspace,
                max_loops=args.max_loops,
                budget_eur=args.budget,
                include_server_tools=not args.no_server_tools,
                attach_id=args.attach.strip() or None,
            )
            return 0
        if goal:
            return _dispatch(command, client, goal, workspace, args)
        if not sys.stdout.isatty():
            console.print("[bold]rocky[/]  Įrašyk užduotį. Tuščia eilutė arba [bold]q[/] — išeiti.")
            console.print(
                f"[dim]aplankas {workspace}  ·  biudžetas €{args.budget:.3f}  ·  {args.max_loops} ciklai[/]"
            )
        console.print("[dim]Enter atsikabina nuo runo — darbas tęsiasi.  rocky ps  ·  rocky attach[/]")
        code = 0
        while True:
            nxt = prompt_goal()
            if not nxt:
                return code
            code = _dispatch(command, client, nxt, workspace, args)
    finally:
        client.close()


def _dispatch(command, client, goal, workspace, args) -> int:
    if command == "route":
        return _route(client, goal)
    return _run(
        goal=goal,
        workspace=workspace,
        max_loops=args.max_loops,
        budget_eur=args.budget,
        include_server_tools=not args.no_server_tools,
    )


def _ps(workspace: Path) -> int:
    rows = list_runs(workspace)
    if not rows:
        console.print("[dim]no runs in this folder[/]")
        return 0
    console.print(f"[bold]id[/]                       [bold]status[/]     [bold]goal[/]")
    for row in rows:
        goal = (row.get("goal") or "").replace("\n", " ")
        if len(goal) > 56:
            goal = goal[:53] + "..."
        ident = str(row.get("id") or "")
        status = str(row.get("status") or "?")
        color = {
            "running": "green",
            "waiting": "yellow",
            "passed": "cyan",
            "unmet": "red",
            "error": "red",
            "crashed": "red",
        }.get(status, "white")
        console.print(f"{ident:26} [{color}]{status:<10}[/] {goal}")
    console.print("[dim]rocky attach [id]   ·   Enter while attached detaches[/]")
    return 0


def _run(*, goal, workspace, max_loops, budget_eur, include_server_tools) -> int:
    meta = create_run(
        workspace=workspace,
        goal=goal,
        budget_eur=budget_eur,
        max_loops=max_loops,
        include_server_tools=include_server_tools,
    )
    spawn_worker(meta)
    console.print(
        f"[dim]run {meta['id']}  ·  Enter detaches, job keeps running  ·  rocky attach {meta['id'][:18]}[/]"
    )
    return _attach(workspace, meta["id"])


def _attach(workspace: Path, run_id: str) -> int:
    folder = run_dir(workspace, run_id)
    cursor = EventCursor(folder / "events.jsonl")
    console.print(f"[bold]attach[/] {run_id}")
    if sys.stdin.isatty():
        console.print("[dim]Enter = detach (do not kill)[/]")
    asked_ts = None
    idle_after_done = 0
    while True:
        for event in cursor.poll():
            render_event(event)
        try:
            meta = refresh_meta(folder)
        except FileNotFoundError:
            console.print("[red]run folder vanished[/]")
            return 1
        ask = read_ask(folder)
        if ask and ask.get("ts") != asked_ts:
            asked_ts = ask.get("ts")
            console.print(f"\n[bold #F07167]Agent asks[/] {ask.get('question')}")
            for i, opt in enumerate(ask.get("options") or [], 1):
                console.print(f"  {i}. {opt}")
            write_answer(folder, prompt_goal("Atsakymas"))
        status = meta.get("status") or ""
        if status in TERMINAL:
            leftover = cursor.poll()
            for event in leftover:
                render_event(event)
            idle_after_done += 1
            if idle_after_done >= 2:
                console.print(f"[dim]{status}  ·  {folder}[/]")
                return 0 if status == "passed" else 1
        line = _stdin_line(0.15)
        if line is not None:
            console.print(f"[dim]detached  {run_id}  still {status or 'running'}[/]")
            return 0


def _stdin_line(timeout: float) -> str | None:
    if not sys.stdin.isatty():
        time.sleep(timeout)
        return None
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    if not ready:
        return None
    try:
        return sys.stdin.readline()
    except EOFError:
        return ""


def _route(client: OpenRouterClient, goal: str) -> int:
    models = parse_models(client.list_models())
    assess = client.complete(
        model=pick_cast(models, "moderate", "mixed").assess,
        messages=[
            {
                "role": "system",
                "content": "You assess tasks. Return JSON with goal, complexity (trivial|moderate|hard), domain (code|research|writing|marketing|ops|mixed), success_criteria, needs_web, deliverable.",
            },
            {"role": "user", "content": goal},
        ],
    )
    try:
        data = parse_json_object(assess.content)
    except Exception:
        data = {"complexity": "moderate", "domain": "mixed", "goal": goal}
    cast = pick_cast(models, data.get("complexity") or "moderate", data.get("domain") or "mixed")
    console.print(f"[bold]goal[/] {data.get('goal') or goal}")
    console.print(f"[bold]complexity[/] {data.get('complexity')}  [bold]domain[/] {data.get('domain')}")
    for role in ("assess", "critic", "planner", "worker", "verifier"):
        console.print(f"[bold]{role}[/]  {getattr(cast, role)}")
        console.print(f"  [dim]{cast.reasons.get(role)}[/]")
    return 0
