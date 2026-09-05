from __future__ import annotations

import argparse
import os
import re
import select
import sys
import time
from pathlib import Path

from rocky.catalog import parse_models, pick_cast
from rocky.config import (
    CONFIG_ENV,
    CONFIG_FLAGS,
    api_key,
    apply_config,
    base_url,
    config_paths,
    forced_language,
    home_env_path,
    preferred_worker,
    save_api_key,
)
from rocky.job import spawn_worker
from rocky.jsonutil import parse_json_object
from rocky.llm import OpenRouterClient
from rocky.runs import (
    TERMINAL,
    EventCursor,
    create_run,
    list_runs_priced,
    load_resume_state,
    read_ask,
    refresh_meta,
    resolve_resumable,
    resolve_run_id,
    run_dir,
    stop_run,
    summarize_costs,
    when_label,
    write_answer,
)
from rocky.status import TRANSCRIPT_TYPES, status_line
from rocky.usage import DEFAULT_BUDGET_EUR
from rocky.theme import CORAL
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
    except (EOFError, KeyboardInterrupt):
        sys.stdout.write("\n")
        return ""
    if line.lower() in {"q", "quit", "exit"}:
        return ""
    return line


COMMANDS_HELP = """commands:
  rocky "goal"                     start a background run in this folder and attach
  rocky                            interactive: type goals one after another
  rocky tui [--attach ID]          three-pane viewer (plan / work / verify)
  rocky ps                         runs in this folder: status, €, when, goal, deliverable
  rocky attach [ID]                follow a run (Enter or Ctrl+C detaches, the job keeps going)
  rocky stop [ID]                  end a run now; what it wrote stays, it can be resumed
  rocky resume [ID]                continue an unmet / error / crashed / stopped run
  rocky resume [ID] "what to change"   follow up on any finished run, passed ones included
  rocky open [ID]                  open the run's deliverable
  rocky cost                       what this folder has spent and where it went
  rocky config                     config files read, effective defaults, an example
  rocky doctor                     key, OpenRouter, folder, sandbox, browser, skills, models
  rocky serve [--host --port]      team web UI
  rocky route "goal"               only assess and pick models
  rocky help                       the full guide (docs/lt.md has the Lithuanian one)
"""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    commands = {"run", "tui", "serve", "route", "ps", "cost", "attach", "resume", "stop", "open", "config", "doctor", "help", "version", "_job"}
    if argv and argv[0] in commands:
        command = argv[0]
        rest = argv[1:]
    else:
        command = "run"
        rest = argv

    if argv and argv[0] in {"--version", "-V", "version"}:
        from rocky import __version__

        sys.stdout.write(f"rocky {__version__}\n")
        return 0

    parser = argparse.ArgumentParser(
        prog="rocky",
        add_help=True,
        usage="rocky [goal] | rocky <command> [args] [options]",
        description="OpenRouter agent orchestrator: plan → work → verify, €1 cap by default.",
        epilog=COMMANDS_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("goal", nargs="*", help="What to accomplish, or a run id for attach / resume")
    parser.add_argument("-d", "--dir", default=".", help="Workspace folder")
    parser.add_argument("--attach", default="", help="TUI: attach to an existing run id")
    parser.add_argument(
        "--budget",
        type=float,
        default=None,
        help="Max EUR for the run (default 1.00; resume keeps the old run's cap)",
    )
    parser.add_argument("--max-loops", type=int, default=None, dest="max_loops")
    parser.add_argument("--model", default="", help="Worker model for this run (OpenRouter id), e.g. deepseek/deepseek-v4")
    parser.add_argument("--lang", default="", help="Write the deliverable in this language (lt, en, …) instead of the goal's")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-server-tools", action="store_true")
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_out",
        help="Headless: run to completion, print one JSON object, no live view",
    )
    args = parser.parse_args(rest)
    workspace = Path(args.dir).expanduser().resolve()
    try:
        cfg = apply_config(workspace)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        return 2
    budget_given = args.budget is not None
    if args.budget is None:
        args.budget = _config_number(cfg, "budget", DEFAULT_BUDGET_EUR)
    if args.max_loops is None:
        args.max_loops = int(_config_number(cfg, "max_loops", 4))
    if args.model.strip():
        os.environ["ROCKY_WORKER"] = args.model.strip()
    if args.lang.strip():
        os.environ["ROCKY_LANGUAGE"] = args.lang.strip().lower()
    goal = " ".join(args.goal).strip()

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
    if command == "cost":
        return _cost(workspace)
    if command == "config":
        return _config(workspace, cfg, args)
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
    if command == "stop":
        return _stop(workspace, goal or None)
    if command == "open":
        return _open(workspace, goal or None)
    if command == "doctor":
        return _doctor(workspace)

    key = api_key() or _onboard(workspace)
    if not key:
        console.print("[red]OPENROUTER_API_KEY is missing.[/] Put it in .env or ~/.env, then run [bold]rocky doctor[/]")
        return 2

    if command == "resume":
        ident, follow_up = split_resume_args(args.goal)
        return _resume(
            workspace,
            ident,
            budget_eur=args.budget if budget_given else None,
            json_out=bool(args.json_out),
            follow_up=follow_up,
        )

    client = OpenRouterClient(key, base_url())
    try:
        if sys.stdout.isatty() and not args.json_out and command not in {"ps", "attach", "help", "_job"}:
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
        if args.json_out:
            console.print("[red]--json needs a goal on the command line[/]")
            return 2
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
            if command == "run":
                console.print(
                    "[dim]rocky open — atidaro rezultatą  ·  rocky resume \"ką pakeisti\" — tęsia nuo šio rezultato[/]"
                )
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
        json_out=bool(getattr(args, "json_out", False)),
    )


STATUS_COLOR = {
    "running": "green",
    "waiting": "yellow",
    "starting": "green",
    "passed": "cyan",
    "passed_with_notes": "yellow",
    "unmet": "red",
    "error": "red",
    "crashed": "red",
    "stopped": "yellow",
}


def _ps(workspace: Path) -> int:
    rows = list_runs_priced(workspace)
    if not rows:
        console.print("[dim]no runs in this folder[/]")
        return 0
    console.print(f"[bold]{'id':<24} {'status':<10} {'eur':>6}  {'when':<5}  goal[/]")
    room = max(24, console.width - 52)
    for row in rows:
        goal = (row.get("goal") or "").replace("\n", " ")
        if len(goal) > room:
            goal = goal[: room - 3] + "..."
        ident = str(row.get("id") or "")
        status = str(row.get("status") or "?")
        color = STATUS_COLOR.get(status, "white")
        eur = row.get("eur")
        price = f"€{float(eur):.2f}" if eur not in (None, "") else ""
        origin = f" [dim]↻ {str(row.get('resume_from'))[:15]}[/]" if row.get("resume_from") else ""
        console.print(f"{ident:<24} [{color}]{status:<10}[/] {price:>6}  {when_label(row):<5}  {goal}{origin}")
        second = ""
        if row.get("deliverable_exists") and row.get("deliverable_path"):
            second = f"→ {row['deliverable_path']}"
        elif status in TERMINAL and row.get("summary"):
            second = str(row["summary"]).replace("\n", " ")[:90]
        if second:
            console.print(f"[dim]{'':<24} {second}[/]")
    console.print("[dim]rocky attach [id]  ·  rocky resume [id]  ·  rocky open [id]  ·  rocky stop [id]  ·  rocky cost[/]")
    return 0


def _config_number(cfg: dict, key: str, default: float) -> float:
    try:
        return float(cfg.get(key, default))
    except (TypeError, ValueError):
        return float(default)


CONFIG_EXAMPLE = """# ~/.rocky/config.toml — defaults for every run; <workspace>/.rocky/config.toml overrides per project
budget = 3.0                     # EUR cap per run (--budget wins)
max_loops = 4
worker = "google/gemini-3.8-flash"   # OpenRouter id (--model wins)
language = "lt"                  # force the deliverable language (--lang wins); omit to follow the goal
sandbox = "sandbox-exec"         # macOS; "docker" or "none"
# token = "team-secret"          # rocky serve
# workspaces = ["/Users/me/projects"]
"""


def _config(workspace: Path, cfg: dict, args) -> int:
    """`rocky config`: which files are read, what they set, and what a run would use."""
    for path in config_paths(workspace):
        mark = "[green]✓[/]" if path.is_file() else "[dim]–[/]"
        console.print(f"{mark} {path}")
    console.print(
        f"[bold]effective[/]  budget €{float(args.budget):.2f}  ·  max_loops {int(args.max_loops)}  ·  "
        f"worker {preferred_worker() or 'catalog default'}  ·  language {forced_language() or 'from the goal'}"
    )
    extra = {k: v for k, v in cfg.items() if k not in CONFIG_FLAGS and k not in CONFIG_ENV}
    if extra:
        console.print(f"[yellow]unknown keys ignored:[/] {', '.join(sorted(extra))}")
    if not any(p.is_file() for p in config_paths(workspace)):
        console.print("[dim]no config file yet — example:[/]")
        console.print(CONFIG_EXAMPLE.rstrip())
    return 0


def _cost(workspace: Path) -> int:
    """`rocky cost`: what this folder has spent, and where it went."""
    rows = list_runs_priced(workspace, limit=1000)
    if not rows:
        console.print("[dim]no runs in this folder[/]")
        return 0
    c = summarize_costs(rows)
    console.print(f"[bold]spent[/]  €{c['total_eur']:.2f} over {c['runs']} runs  ·  today €{c['today_eur']:.2f}  ·  last 7 days €{c['week_eur']:.2f}")
    counts = "  ".join(f"[{STATUS_COLOR.get(k, 'white')}]{k}[/] {v}" for k, v in sorted(c["by_status"].items()))
    console.print(counts)
    if c["per_usable_eur"] is not None:
        console.print(f"[bold]per usable result[/]  €{c['per_usable_eur']:.2f}  ({c['usable']} passed or passed with notes)")
    if c["wasted_eur"]:
        console.print(f"[bold]spent on runs that gave nothing[/]  €{c['wasted_eur']:.2f}")
    if c["top"]:
        console.print("[bold]costliest[/]")
        for t in c["top"]:
            console.print(f"  €{t['eur']:.2f}  [{STATUS_COLOR.get(str(t['status']), 'white')}]{t['status']:<17}[/] {t['id']}  {t['goal']}")
    return 0


def _stop(workspace: Path, ident: str | None) -> int:
    """`rocky stop [id]`: end the job now; what it wrote stays, and the run can be resumed."""
    try:
        run_id = resolve_run_id(workspace, ident)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/]")
        return 1
    before = refresh_meta(run_dir(workspace, run_id)).get("status") or ""
    if before in TERMINAL:
        console.print(f"[dim]{run_id} already {before} — nothing to stop[/]")
        return 0
    meta = stop_run(workspace, run_id)
    console.print(f"[bold]stopped[/] {run_id}  ·  was {before}")
    console.print("[dim]files already written stay in the workspace[/]")
    console.print(f"[dim]continue with  rocky resume {run_id}[/]")
    return 0 if meta.get("status") == "stopped" else 1


def _run(*, goal, workspace, max_loops, budget_eur, include_server_tools, json_out=False) -> int:
    meta = create_run(
        workspace=workspace,
        goal=goal,
        budget_eur=budget_eur,
        max_loops=max_loops,
        include_server_tools=include_server_tools,
    )
    spawn_worker(meta)
    if json_out:
        return _run_json(workspace, meta["id"])
    console.print(
        f"[dim]run {meta['id']}  ·  Enter detaches, job keeps running  ·  rocky attach {meta['id'][:18]}[/]"
    )
    return _attach(workspace, meta["id"])


def _doctor(workspace: Path) -> int:
    from rocky.doctor import healthy, print_report, run_checks

    checks = run_checks(workspace)
    print_report(checks, console)
    return 0 if healthy(checks) else 1


def _onboard(workspace: Path) -> str:
    """No key yet: on a terminal, ask for it once, save to ~/.env, then run the doctor."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return ""
    console.print(
        "[bold]Nėra OPENROUTER_API_KEY.[/] Įklijuok raktą iš https://openrouter.ai/keys — įrašysiu jį į "
        f"[bold]{home_env_path()}[/] (tik tau skaitomą).\n"
        "[dim]No OPENROUTER_API_KEY yet. Paste your key from https://openrouter.ai/keys — "
        "it will be saved to ~/.env (mode 600). Empty line = quit.[/]"
    )
    try:
        raw = input("OPENROUTER_API_KEY: ").strip()
    except EOFError:
        return ""
    if not raw:
        return ""
    if not raw.startswith("sk-or-"):
        console.print("[yellow]Raktas paprastai prasideda „sk-or-“ / keys usually start with sk-or- — saving anyway.[/]")
    path = save_api_key(raw)
    console.print(f"[green]Įrašyta / saved →[/] {path}\n")
    _doctor(workspace)
    console.print()
    return raw


_RUN_ID = re.compile(r"^\d{8}-")


def split_resume_args(tokens: list[str]) -> tuple[str | None, str]:
    """`rocky resume [id] [instruction…]` → (id or None, instruction). Run ids start with a date."""
    tokens = [t for t in (tokens or []) if t.strip()]
    if tokens and _RUN_ID.match(tokens[0]):
        return tokens[0], " ".join(tokens[1:]).strip()
    return None, " ".join(tokens).strip()


def _open(workspace: Path, ident: str | None) -> int:
    """`rocky open [id]`: open the run's deliverable with the system opener and print its path."""
    try:
        run_id = resolve_run_id(workspace, ident)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/]")
        return 1
    meta = refresh_meta(run_dir(workspace, run_id))
    rel = str(meta.get("deliverable_path") or "")
    path = (workspace / rel) if rel else None
    if path is None or not path.is_file():
        files = [f for f in meta.get("files") or [] if (workspace / str(f)).is_file()]
        console.print(f"[red]no deliverable for {run_id}[/]" + (f"  ({rel} is not on disk)" if rel else ""))
        if files:
            console.print("[dim]files it did write:[/] " + ", ".join(files))
        return 1
    console.print(f"[bold]{path}[/]")
    opened = open_path(path)
    if not opened:
        console.print("[dim]could not launch an opener on this system — the path above is the file[/]")
    return 0


def open_path(path: Path) -> bool:
    import subprocess

    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except (OSError, AttributeError):
        return False
    return True


def _resume(
    workspace: Path,
    ident: str | None,
    *,
    budget_eur: float | None,
    json_out: bool = False,
    follow_up: str = "",
) -> int:
    """Continue an unmet / error / crashed / stopped run — or, with an instruction, build on any finished one."""
    try:
        run_id = resolve_resumable(workspace, ident, follow_up=bool(follow_up))
        state = load_resume_state(run_dir(workspace, run_id), follow_up=follow_up)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/]")
        return 1
    meta = create_run(
        workspace=workspace,
        goal=str(state.get("goal") or ""),
        budget_eur=float(budget_eur if budget_eur is not None else (state.get("budget_eur") or DEFAULT_BUDGET_EUR)),
        max_loops=int(state.get("max_loops") or 4),
        include_server_tools=bool(state.get("include_server_tools", True)),
        resume_from=run_id,
    )
    spawn_worker(meta)
    if json_out:
        return _run_json(workspace, meta["id"])
    done = state.get("done_steps") or []
    if follow_up:
        what = f"follow-up: {follow_up}"
    elif state.get("replan"):
        what = "replan from the last verdict"
    else:
        what = f"skip finished steps {done}" if done else "redo the plan's steps"
    console.print(
        f"[dim]resume {run_id} → run {meta['id']}  ·  {what}  ·  cap €{meta['budget_eur']:.2f}  ·  Enter detaches[/]"
    )
    return _attach(workspace, meta["id"])


HEADLESS_ANSWER = "no human available; continue with documented assumptions"


def collect_result(workspace: Path, run_id: str, events) -> dict:
    """One JSON-friendly summary of a finished run from its meta and events."""
    folder = run_dir(workspace, run_id)
    meta = refresh_meta(folder)
    done: dict = {}
    plan: dict = {}
    verify: dict = {}
    cast: dict = {}
    for event in events:
        if event.type == "done":
            done = event.payload
        elif event.type == "plan":
            plan = event.payload
        elif event.type == "verify":
            verify = event.payload
        elif event.type == "cast":
            cast = event.payload.get("models") or {}
    status = meta.get("status") or done.get("status") or ("passed" if done.get("passed") else "unmet")
    return {
        "run_id": run_id,
        "status": status,
        "passed": bool(meta.get("passed", done.get("passed", False))),
        "notes": list(done.get("notes") or meta.get("notes") or []),
        "goal": meta.get("goal"),
        "workspace": str(workspace),
        "deliverable_path": done.get("deliverable_path") or meta.get("deliverable_path") or plan.get("deliverable_path"),
        "deliverable_exists": bool(done.get("deliverable_exists", meta.get("deliverable_exists", False))),
        "files": list(done.get("files") or meta.get("files") or []),
        "summary": done.get("summary") or meta.get("summary") or "",
        "score": verify.get("score"),
        "issues": verify.get("issues") or [],
        "loops": done.get("loops"),
        "models": cast,
        "usd": done.get("usd"),
        "eur": done.get("eur"),
        "prompt_tokens": done.get("prompt_tokens"),
        "completion_tokens": done.get("completion_tokens"),
        "total_tokens": done.get("total_tokens"),
        "receipt": done.get("receipt"),
        "cached_tokens": done.get("cached_tokens"),
        "by_role": done.get("by_role") or {},
        "role_receipt": done.get("role_receipt"),
        "events": str(folder / "events.jsonl"),
    }


def _run_json(workspace: Path, run_id: str, poll: float = 0.2) -> int:
    """Headless follow: answer questions with a safe default, print JSON at the end."""
    import json as _json

    folder = run_dir(workspace, run_id)
    cursor = EventCursor(folder / "events.jsonl")
    seen = []
    asked_ts = None
    while True:
        seen.extend(cursor.poll())
        try:
            meta = refresh_meta(folder)
        except FileNotFoundError:
            sys.stdout.write(_json.dumps({"run_id": run_id, "status": "error", "passed": False, "summary": "run folder vanished"}) + "\n")
            return 1
        ask = read_ask(folder)
        if ask and ask.get("ts") != asked_ts:
            asked_ts = ask.get("ts")
            write_answer(folder, HEADLESS_ANSWER)
        if (meta.get("status") or "") in TERMINAL:
            seen.extend(cursor.poll())
            result = collect_result(workspace, run_id, seen)
            sys.stdout.write(_json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            sys.stdout.flush()
            return 0 if result["passed"] else 1
        time.sleep(poll)


def _attach(workspace: Path, run_id: str) -> int:
    folder = run_dir(workspace, run_id)
    cursor = EventCursor(folder / "events.jsonl")
    console.print(f"[bold]attach[/] {run_id}")
    if sys.stdin.isatty():
        console.print(f"[dim]Enter or Ctrl+C = detach, the job keeps going  ·  rocky stop {run_id[:15]} = end it[/]")
    live = sys.stdout.isatty()
    pulse = None
    if live:
        from rich.status import Status

        pulse = Status("starting", console=console, spinner="dots", spinner_style=f"#{CORAL}")
        pulse.start()
    asked_ts = None
    idle_after_done = 0
    try:
        while True:
            for event in cursor.poll():
                if event.type == "done":
                    event.payload.setdefault("run_id", run_id)
                _show_event(event, pulse)
            try:
                meta = refresh_meta(folder)
            except FileNotFoundError:
                if pulse is not None:
                    pulse.stop()
                console.print("[red]run folder vanished[/]")
                return 1
            ask = read_ask(folder)
            if ask and ask.get("ts") != asked_ts:
                asked_ts = ask.get("ts")
                if pulse is not None:
                    pulse.stop()
                console.print(f"\n[bold #F07167]Agent asks[/] {ask.get('question')}")
                for i, opt in enumerate(ask.get("options") or [], 1):
                    console.print(f"  {i}. {opt}")
                write_answer(folder, prompt_goal("Atsakymas"))
                if pulse is not None:
                    pulse.start()
                    pulse.update("thinking")
            status = meta.get("status") or ""
            if status in TERMINAL:
                leftover = cursor.poll()
                for event in leftover:
                    if event.type == "done":
                        event.payload.setdefault("run_id", run_id)
                    _show_event(event, pulse)
                idle_after_done += 1
                if idle_after_done >= 2:
                    if pulse is not None:
                        pulse.stop()
                    console.print(f"[dim]{status}  ·  {folder}[/]")
                    return 0 if status in {"passed", "passed_with_notes"} else 1
            line = _stdin_line(0.15)
            if line is not None:
                if pulse is not None:
                    pulse.stop()
                console.print(f"[dim]detached  {run_id}  still {status or 'running'}[/]")
                return 0
    except KeyboardInterrupt:
        if pulse is not None:
            pulse.stop()
        console.print(
            f"\n[dim]detached  {run_id}  still running in the background  ·  "
            f"rocky attach {run_id[:15]} to watch  ·  rocky stop {run_id[:15]} to end it[/]"
        )
        return 0
    finally:
        if pulse is not None:
            pulse.stop()


def _show_event(event, pulse) -> None:
    line = status_line(event)
    if pulse is not None and line:
        pulse.update(line)
    if pulse is None or event.type in TRANSCRIPT_TYPES:
        if pulse is not None and event.type in TRANSCRIPT_TYPES:
            pulse.stop()
            render_event(event)
            pulse.start()
            if line:
                pulse.update(line)
        else:
            render_event(event)


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
        model=pick_cast(models, "moderate", "mixed", preferred_worker=preferred_worker()).assess,
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
    cast = pick_cast(
        models,
        data.get("complexity") or "moderate",
        data.get("domain") or "mixed",
        preferred_worker=preferred_worker(),
    )
    console.print(f"[bold]goal[/] {data.get('goal') or goal}")
    console.print(f"[bold]complexity[/] {data.get('complexity')}  [bold]domain[/] {data.get('domain')}")
    for role in ("assess", "critic", "planner", "worker", "verifier"):
        console.print(f"[bold]{role}[/]  {getattr(cast, role)}")
        console.print(f"  [dim]{cast.reasons.get(role)}[/]")
    return 0
