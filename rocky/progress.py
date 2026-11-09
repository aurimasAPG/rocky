"""A bounded, event-driven run board. Rendering never reads files or starts work."""

from __future__ import annotations

from collections import deque
from datetime import datetime
import math
import re
import time
import unicodedata

from rich.text import Text

from rocky import __version__
from rocky.banner import composer_brand
from rocky.events import Event
from rocky.runs import TERMINAL
from rocky.status import status_line
from rocky.theme import APRICOT, CORAL, DIM, INK, PERI, SEA

_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?|[@-Z\\-_])")
_STAGES = {"assessing": "Prepare", "planning": "Prepare", "working": "Work", "verifying": "Checks"}
_MARKS = {"pending": "○", "working": "›", "completed": "✓", "incomplete": "!", "skipped": "–"}


def _clean(value: object) -> str:
    text = _ANSI.sub("", str(value or ""))
    text = "".join(" " if unicodedata.category(c) in {"Cc", "Cs"} or c in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069" else c for c in text)
    return " ".join(text.split())


def _timestamp(value: object) -> float | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _number(value: object) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (ValueError, TypeError):
        return None


def _duration(value: float) -> str:
    seconds = max(0, int(value))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class RunProgress:
    def __init__(self, meta: dict, run_id: str) -> None:
        self.run_id = _clean(run_id)
        self.meta: dict = {}
        created = _timestamp(meta.get("created"))
        self.started_at = created if created is not None else time.time()
        self.last_activity = self.started_at
        self.finished_at: float | None = None
        self.goal = ""
        self.target = ""
        self.phase = "Prepare"
        self.activity = "Preparing the task"
        self.question = ""
        self.steps: dict[str, dict[str, str]] = {}
        self.actions: deque[dict[str, str]] = deque(maxlen=12)
        self.issues: list[str] = []
        self.spent_eur: float | None = None
        self.cap_eur: float | None = None
        self._budget_from_event = False
        self._event_status = ""
        self.update_meta(meta)

    @property
    def status(self) -> str:
        value = str(self.meta.get("status") or "")
        return value if value in TERMINAL else self._event_status or value

    def update_meta(self, meta: dict) -> None:
        self.meta.update(meta)
        self.goal = _clean(self.meta.get("goal") or self.goal)
        self.target = _clean(self.meta.get("deliverable_path") or self.target)
        if not self._budget_from_event:
            self.cap_eur = _number(self.meta.get("budget_eur"))
        reported = _number(meta.get("eur"))
        if reported is not None:
            self.spent_eur = max(self.spent_eur or 0, reported)
        if self.status in TERMINAL:
            self.finished_at = _timestamp(self.meta.get("finished")) or self.finished_at or time.time()

    def set_question(self, question: str | None) -> None:
        if self.question and not question:
            self.activity = {"Prepare": "Preparing the task", "Work": "Working on your task", "Checks": "Checking the result"}[self.phase]
        self.question = _clean(question)

    def _action(self, label: str, *, state: str = "completed", name: str = "") -> None:
        self.actions.append({"label": _clean(label), "state": state, "name": _clean(name)})

    def consume(self, event: Event) -> None:
        payload, kind = event.payload or {}, event.type
        stamp = _timestamp(event.ts)
        if stamp is not None:
            self.last_activity = max(self.last_activity, stamp)
        if kind == "run_started":
            self.goal = _clean(payload.get("goal") or self.goal)
        if kind in {"run_started", "budget_set", "budget_warn"}:
            cap = _number(payload.get("budget_eur"))
            if cap is not None:
                self.cap_eur, self._budget_from_event = cap, True
        if kind in {"cost", "done", "budget"}:
            reported = _number(payload.get("eur"))
            if reported is not None:
                self.spent_eur = max(self.spent_eur or 0, reported)
        if kind == "phase":
            self.phase = _STAGES.get(str(payload.get("stage")), self.phase)
            self.activity = {"Prepare": "Preparing the task", "Work": "Working on your task", "Checks": "Checking the result"}[self.phase]
        elif kind == "plan":
            self.target = _clean(payload.get("deliverable_path") or self.target)
            self.steps = {}
            for step in payload.get("steps") or []:
                key, title = str(step.get("id")), _clean(step.get("title") or "Step")
                self.steps[key] = {"title": title, "state": "pending"}
        elif kind.startswith("subtask_"):
            key = str(payload.get("id"))
            step = self.steps.setdefault(key, {"title": _clean(payload.get("title") or "Step"), "state": "pending"})
            if kind == "subtask_start":
                step["state"], self.phase = "working", "Work"
                self.activity = "Working: " + step["title"]
            elif kind == "subtask_skipped":
                step["state"] = "skipped"
            elif kind == "subtask_incomplete":
                step["state"] = "incomplete"
            elif kind == "subtask_done":
                step["state"] = "completed" if payload.get("completed", not payload.get("aborted")) and step["state"] != "incomplete" else "incomplete"
        elif kind == "tool_call":
            self.activity = _clean(status_line(event) or "Working")
            if self.phase == "Prepare":
                self.phase = "Work"
            self._action(self.activity, state="working", name=str(payload.get("name") or "tool"))
        elif kind == "tool_result":
            name, result = _clean(payload.get("name") or "tool"), _clean(payload.get("result"))
            exit_code = re.match(r"exit\s+(-?\d+)\b", result, re.I)
            failed = bool(re.match(r"error\s*:", result, re.I) or (exit_code and int(exit_code[1]) != 0))
            action = next((a for a in reversed(self.actions) if a["name"] == name and a["state"] == "working"), None)
            if action is None:
                self._action(name, name=name)
                action = self.actions[-1]
            action["state"] = "failed" if failed else "completed"
            if failed:
                action["label"] = f"{name} failed: {result}"
                self.activity = "Recovering from a tool error"
            else:
                self.activity = "Checking the result" if self.phase == "Checks" else "Working on your task"
        elif kind == "gate":
            self.issues = [_clean(item) for item in [*(payload.get("blocking") or []), *(payload.get("soft") or [])]]
            self.activity = "Checks found issues to address" if self.issues else "Automatic checks passed"
        elif kind == "verify":
            self.phase = "Checks"
            self.issues = [_clean(item) for item in [*(payload.get("blocking") or []), *(payload.get("issues") or []), *(payload.get("remaining") or [])]]
            self.activity = "Review passed" if payload.get("passed") else "Review found issues to address"
        elif kind == "budget_offer":
            self.activity = "Waiting for your budget choice"
        elif kind == "budget_warn":
            self._action("Budget: " + _clean(payload.get("note") or "the reported spend is approaching the cap"), state="failed")
        elif kind == "error":
            self.activity = _clean(payload.get("message") or "An error was reported")
            self._action(self.activity, state="failed")
        elif kind == "done":
            self._event_status = str(payload.get("status") or ("passed" if payload.get("passed") else "unmet"))
            self.finished_at = _timestamp(self.meta.get("finished")) or stamp or self.finished_at or time.time()
            self.target = _clean(payload.get("deliverable_path") or self.target)
        elif kind in {"nudge", "stall", "no_progress", "loop_guard"}:
            self.activity = _clean(status_line(event) or kind.replace("_", " "))
            self._action(self.activity, state="failed")
        elif kind in {"catalog", "memory", "assessed", "cast", "resumed", "compact"}:
            self.activity = _clean(status_line(event) or self.activity)

    def render(self, width: int = 80, height: int = 24, now: float | None = None) -> Text:
        width, height = max(1, width), max(1, height)
        now = time.time() if now is None else now
        end = self.finished_at if self.status in TERMINAL and self.finished_at is not None else now
        rows = list(composer_brand(compact=True).split("\n"))

        def row(value: object, style: str = f"#{INK}") -> Text:
            return Text(_clean(value), style=style, no_wrap=True, overflow="ellipsis")

        rows.extend([Text("    ") + row(f"v{__version__}  ·  Task {self.run_id}", f"#{DIM}"), row("Task: " + self.goal)])
        stages = Text()
        for index, stage in enumerate(("Prepare", "Work", "Checks")):
            if index:
                stages.append("  →  ", style=f"#{DIM}")
            stages.append(stage, style=f"bold #{CORAL}" if stage == self.phase and self.status not in TERMINAL else f"#{DIM}")
        rows.append(stages)
        terminal = {"passed": "Ready to review", "passed_with_notes": "Ready to review with notes", "unmet": "Task needs more work", "stopped": "Stopped · completed files are kept", "crashed": "Run interrupted · completed files are kept", "error": "Run ended with an error"}
        message = terminal.get(self.status) or ("Waiting for you: " + self.question if self.question else ("Addressing: " + self.issues[0] if self.issues else self.activity))
        last_error = bool(self.actions and self.actions[-1]["state"] == "failed" and self.actions[-1]["label"] == self.activity)
        caution = self.question or self.issues or last_error or self.activity == "Recovering from a tool error" or self.status in {"passed_with_notes", "unmet", "error", "crashed", "stopped"}
        rows.append(row(message, f"bold #{APRICOT}" if caution else f"bold #{SEA}"))
        spent = "—" if self.spent_eur is None else f"{self.spent_eur:.3f}"
        cap = "—" if self.cap_eur is None else f"{self.cap_eur:.2f}"
        rows.append(row(f"Elapsed {_duration(end - self.started_at)} · Last activity {_duration(end - self.last_activity)} ago", f"#{DIM}"))
        rows.append(row(f"Reported spend: EUR {spent} / cap {cap}", f"#{DIM}"))
        rows.append(row("Target: " + (self.target or "The agent has not named an output yet"), f"#{PERI}"))
        room = max(0, height - len(rows) - 4)  # section headings and two footer lines
        action_count = min(room, 5, max(3, room // 2)) if room else 0
        step_count = max(0, room - action_count)
        rows.append(row("STEPS", f"bold #{SEA}"))
        steps = list(self.steps.values())
        if not steps:
            step_rows = [row("The agent has not reported a step list", f"#{DIM}")]
        else:
            # Keep unfinished work visible when the plan is longer than the screen.
            steps = [s for s in steps if s["state"] in {"working", "incomplete"}] + [s for s in steps if s["state"] not in {"working", "incomplete"}]
            visible = steps[:max(0, step_count - (len(steps) > step_count))]
            step_rows = [row(f"{_MARKS[s['state']]} {s['title']} · {s['state']}", f"#{APRICOT}" if s["state"] == "incomplete" else f"#{INK}") for s in visible]
            if len(visible) < len(steps):
                step_rows.append(row(f"+ {len(steps) - len(visible)} more steps · d + Enter for details", f"#{DIM}"))
        rows.extend(step_rows[:step_count])
        rows.extend(row("") for _ in range(max(0, step_count - len(step_rows))))
        rows.append(row("RECENT ACTIVITY", f"bold #{SEA}"))
        actions = list(self.actions)[-action_count:] if action_count else []
        rows.extend(row(f"{'!' if a['state'] == 'failed' else '›' if a['state'] == 'working' else '✓'} {a['label']}", f"#{APRICOT}" if a["state"] == "failed" else f"#{INK}") for a in actions)
        if not actions and action_count:
            rows.append(row("Waiting for the first tool action", f"#{DIM}"))
        rows.extend(row("") for _ in range(max(0, action_count - max(1, len(actions)))))
        rows = rows[:max(0, height - 2)]
        rows.extend([row("Enter: keep working in background", f"#{DIM}"), row("d + Enter: details", f"#{DIM}")])
        clipped = []
        for line in rows[:height]:
            line = line.copy()
            line.truncate(width, overflow="ellipsis")
            clipped.append(line)
        return Text("\n").join(clipped)

    def __rich_console__(self, console, options):
        yield self.render(width=options.max_width, height=min(console.size.height, options.max_height))
