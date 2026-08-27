from __future__ import annotations

import threading
import time
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, Log, Static

from rocky.banner import tui_splash
from rocky.clip import copy_text, paste_text
from rocky.events import Event
from rocky.status import status_line
from rocky.job import spawn_worker
from rocky.runs import (
    TERMINAL,
    EventCursor,
    create_run,
    read_ask,
    refresh_meta,
    resolve_run_id,
    run_dir,
    write_answer,
)


class GoalInput(Input):
    """Input that pastes from the real OS clipboard (Cmd+V / Ctrl+V)."""

    BINDINGS = [
        Binding("super+v", "paste", "Paste", show=False),
        Binding("ctrl+shift+v", "paste", "Paste", show=False),
    ]

    def action_paste(self) -> None:
        clip = paste_text() or self.app.clipboard
        if not clip:
            return
        start, end = self.selection
        self.replace(clip, start, end)


class OrcheTui(App):
    CSS = """
    Screen { background: #1C2B33; }
    Header { background: #152228; color: #E8F0EA; text-style: italic; }
    Footer { background: #152228; color: #7D8B90; }
    #cast { height: 9; padding: 1 2; color: #E8F0EA; background: #243540; }
    #bill { height: 3; padding: 0 2; color: #F07167; background: #152228; }
    #pulse { height: 1; padding: 0 2; color: #E8A87C; background: #152228; }
    #body { height: 1fr; }
    .col { border: solid #E8A87C; background: #243540; }
    .col Log { color: #E8F0EA; }
    #plan { border-title-color: #F07167; }
    #work { border-title-color: #3D9B8F; }
    #verify { border-title-color: #8B9CF7; }
    #goal_in { height: 3; background: #152228; color: #E8F0EA; border: tall #F07167; }
    """
    TITLE = "rocky"
    ALLOW_SELECT = True
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+c,super+c", "copy_selection", "Copy", show=False),
        Binding("pageup", "scroll_up", "PgUp", show=False),
        Binding("pagedown", "scroll_down", "PgDn", show=False),
    ]

    def __init__(self, **run_kwargs):
        super().__init__()
        self.run_kwargs = run_kwargs
        self._busy = False
        self._receipt = "0 in · 0 out · 0 tokens  ·  $0.00  ·  €0.00"
        self._run_id: str | None = None
        self._awaiting_ask = False
        self._ask_ts = None
        self._follow_stop = threading.Event()
        self._pulse = "ready"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(tui_splash(), id="cast")
        yield Static("0 in · 0 out · 0 tokens  ·  $0.00  ·  €0.00", id="bill")
        yield Static("ready", id="pulse")
        with Horizontal(id="body"):
            with Vertical(classes="col", id="plan"):
                yield Log(id="plan_log", max_lines=8000)
            with Vertical(classes="col", id="work"):
                yield Log(id="work_log", max_lines=8000)
            with Vertical(classes="col", id="verify"):
                yield Log(id="verify_log", max_lines=8000)
        yield GoalInput(placeholder="Užduotis + Enter   ·   q atsikabina   ·   PgUp  ·   ⌘C copy", id="goal_in")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#plan").border_title = "plan"
        self.query_one("#work").border_title = "work"
        self.query_one("#verify").border_title = "verify"
        self.query_one("#goal_in", GoalInput).focus()
        attach = (self.run_kwargs.get("attach_id") or "").strip()
        goal = self.run_kwargs.get("goal") or ""
        self.sub_title = "įrašyk užduotį  ·  q atsikabina, darbas tęsiasi"
        if attach:
            try:
                run_id = resolve_run_id(self.run_kwargs["workspace"], attach)
            except FileNotFoundError as exc:
                self.query_one("#verify_log", Log).write_line(str(exc))
                return
            self._watch(run_id)
        elif goal:
            self._start(goal)

    @on(Input.Submitted, "#goal_in")
    def _submit(self, event: Input.Submitted) -> None:
        text = (event.value or "").strip()
        event.input.clear()
        if text.lower() in {"q", "quit", "exit"}:
            self.exit()
            return
        if not text:
            return
        if self._awaiting_ask and self._run_id:
            folder = run_dir(self.run_kwargs["workspace"], self._run_id)
            write_answer(folder, text)
            self._awaiting_ask = False
            self.query_one("#work_log", Log).write_line(f"atsakymas · {text}")
            self.query_one("#goal_in", GoalInput).placeholder = (
                "Užduotis + Enter   ·   q atsikabina   ·   PgUp  ·   ⌘C copy"
            )
            return
        if self._busy:
            self.query_one("#work_log", Log).write_line("palauk — dar dirba (q atsikabina)")
            return
        self._start(text)

    def _need_file_ask(self, question: str) -> None:
        self._awaiting_ask = True
        label = "BUDGET" if "THIS RUN" in question else "CLARIFY"
        self.query_one("#verify_log", Log).write_line(f"{label}  {question}")
        self.sub_title = "agentas klausia  ·  atsakymas apačioje"
        box = self.query_one("#goal_in", GoalInput)
        box.disabled = False
        box.placeholder = "Atsakymas + Enter"
        box.focus()

    def _start(self, goal: str) -> None:
        meta = create_run(
            workspace=self.run_kwargs["workspace"],
            goal=goal,
            budget_eur=self.run_kwargs["budget_eur"],
            max_loops=self.run_kwargs["max_loops"],
            include_server_tools=self.run_kwargs["include_server_tools"],
        )
        spawn_worker(meta)
        for log_id in ("plan_log", "work_log", "verify_log"):
            self.query_one(f"#{log_id}", Log).clear()
        self.query_one("#cast", Static).update(f"run {meta['id']}\nq atsikabina — darbas tęsiasi")
        self.query_one("#work_log", Log).write_line(f"start · {goal}")
        self.query_one("#pulse", Static).update("starting")
        self._watch(meta["id"])

    def _watch(self, run_id: str) -> None:
        self._follow_stop.set()
        self._follow_stop = threading.Event()
        self._run_id = run_id
        self._busy = True
        self._awaiting_ask = False
        self._ask_ts = None
        self.sub_title = f"{run_id}  ·  q detaches"
        self.query_one("#goal_in", GoalInput).disabled = True
        stop = self._follow_stop
        self.run_worker(lambda: self._follow(run_id, stop), exclusive=True, thread=True, exit_on_error=False)

    def _follow(self, run_id: str, stop: threading.Event) -> None:
        folder = run_dir(self.run_kwargs["workspace"], run_id)
        cursor = EventCursor(folder / "events.jsonl")
        try:
            while not stop.is_set():
                for event in cursor.poll():
                    self.call_from_thread(self._on_event, event)
                try:
                    meta = refresh_meta(folder)
                except FileNotFoundError:
                    self.call_from_thread(self._crash, "run folder vanished")
                    return
                ask = read_ask(folder)
                if ask and ask.get("ts") != self._ask_ts:
                    self._ask_ts = ask.get("ts")
                    self.call_from_thread(self._need_file_ask, str(ask.get("question") or ""))
                if (meta.get("status") or "") in TERMINAL:
                    for event in cursor.poll():
                        self.call_from_thread(self._on_event, event)
                    return
                time.sleep(0.15)
        except Exception as exc:  # noqa: BLE001
            self.call_from_thread(self._crash, str(exc))
        finally:
            if not stop.is_set():
                self.call_from_thread(self._idle)

    def _crash(self, message: str) -> None:
        self.query_one("#verify_log", Log).write_line(f"ERROR  {message}")
        self.sub_title = "klaida — žiūrėk verify stulpelį"

    def _idle(self) -> None:
        self._busy = False
        if not self._pulse.startswith("done"):
            self._pulse = "ready"
            self.query_one("#pulse", Static).update("ready")
        self.query_one("#goal_in", GoalInput).disabled = False
        self.query_one("#goal_in", GoalInput).focus()

    def _emit(self, event: Event) -> None:
        self.call_from_thread(self._on_event, event)

    def _on_event(self, event: Event) -> None:
        line = status_line(event)
        if line:
            self._pulse = line
            self.query_one("#pulse", Static).update(line)
        payload = event.payload
        kind = event.type
        if kind == "run_started":
            self.query_one("#work_log", Log).write_line("ciklas paleistas")
            return
        if kind == "catalog":
            self.query_one("#work_log", Log).write_line(
                f"catalog · {payload.get('status')} {payload.get('count') or ''}"
            )
            return
        if kind == "assessed":
            est = payload.get("estimated_eur")
            extra = f"  ~€{est}" if est is not None else ""
            self.query_one("#plan_log", Log).write_line(
                f"assess  {payload.get('complexity')} · {payload.get('domain')}{extra}"
            )
            return
        if kind == "budget_offer":
            log = self.query_one("#plan_log", Log)
            log.write_line(
                f"budget  ~€{payload.get('estimated_eur')} vs cap €{payload.get('current_eur')}"
            )
            log.write_line(f"raise this run to €{payload.get('suggested_eur')}?  yes / keep / number")
            return
        if kind == "budget_set":
            verb = "raised" if payload.get("lifted") else "kept"
            self.query_one("#plan_log", Log).write_line(
                f"budget {verb}  €{payload.get('budget_eur')} this run"
            )
            return
        if kind == "budget_warn":
            self.query_one("#plan_log", Log).write_line(
                f"budget  estimate over cap — keeping €{payload.get('budget_eur')}"
            )
            return
        if kind == "cast":
            models = payload.get("models") or {}
            self.query_one("#cast", Static).update(
                f"critic   {models.get('critic')}\n"
                f"planner  {models.get('planner')}\n"
                f"worker   {models.get('worker')}\n"
                f"verify   {models.get('verifier')}"
            )
            return
        if kind == "critique":
            log = self.query_one("#plan_log", Log)
            log.write_line(f"critic  {payload.get('clarity')}")
            log.write_line(str(payload.get("quality_bar") or ""))
            return
        if kind == "plan":
            log = self.query_one("#plan_log", Log)
            log.write_line(str(payload.get("summary") or ""))
            for step in payload.get("steps") or []:
                deps = step.get("depends_on") or []
                extra = f"  (after {deps})" if deps else ""
                log.write_line(f"{step.get('id')}. {step.get('title')}{extra}")
            return
        if kind == "subtask_start":
            self.query_one("#work_log", Log).write_line(f"subagent · {payload.get('title')}")
            return
        if kind in {"tool_call", "tool_result", "worker"}:
            log = self.query_one("#work_log", Log)
            if kind == "tool_call":
                log.write_line(f"→ {payload.get('name')}")
            elif kind == "tool_result":
                line = str(payload.get("result") or "").splitlines()
                log.write_line(f"← {(line[0] if line else '')[:160]}")
            else:
                log.write_line(str(payload.get("text") or "")[:400])
            return
        if kind == "verify":
            log = self.query_one("#verify_log", Log)
            mark = "PASS" if payload.get("passed") else "FAIL"
            log.write_line(f"{mark}  score {payload.get('score')}")
            log.write_line(str(payload.get("summary") or ""))
            return
        if kind == "error":
            self.query_one("#verify_log", Log).write_line(str(payload.get("message") or payload))
            return
        if kind in {"cost", "done"}:
            receipt = payload.get("receipt") or self._receipt
            self._receipt = receipt
            self.query_one("#bill", Static).update(receipt)
            self.sub_title = receipt
        if kind == "done":
            mark = "passed" if payload.get("passed") else "unmet"
            self.sub_title = f"{mark}  ·  {self._receipt}  ·  kita užduotis arba q"


    def copy_to_clipboard(self, text: str) -> None:
        super().copy_to_clipboard(text)
        if copy_text(text):
            self.notify("Copied", timeout=1.4)

    def action_copy_selection(self) -> None:
        selected = self.screen.get_selected_text()
        if selected:
            self.copy_to_clipboard(selected)
            return
        box = self.query_one("#goal_in", GoalInput)
        if box.selected_text:
            self.copy_to_clipboard(box.selected_text)
            return
        # Nothing highlighted — leave Ctrl+C as interrupt-to-quit only if idle.
        if not self._busy:
            self.exit()

    def action_scroll_up(self) -> None:
        self.query_one("#work_log", Log).scroll_page_up()

    def action_scroll_down(self) -> None:
        self.query_one("#work_log", Log).scroll_page_down()


def run_tui(
    *,
    goal: str = "",
    workspace: Path,
    max_loops: int,
    budget_eur: float,
    include_server_tools: bool,
    attach_id: str | None = None,
) -> None:
    app = OrcheTui(
        goal=goal,
        workspace=workspace,
        max_loops=max_loops,
        budget_eur=budget_eur,
        include_server_tools=include_server_tools,
        attach_id=attach_id or "",
    )
    app.run()
