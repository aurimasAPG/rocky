"""A multiline task draft for the interactive CLI; never starts work itself."""

from __future__ import annotations

import math
from pathlib import Path

from rich.cells import cell_len
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Button, Input, Static, TextArea

from rocky.clip import copy_text, paste_text
from rocky.drafts import TaskDraft, load_draft, save_draft


class BriefArea(TextArea):
    BINDINGS = [
        Binding("ctrl+v,super+v,ctrl+shift+v", "paste", "Paste", show=False),
    ]

    def action_paste(self) -> None:
        """Native bracketed paste works normally; shortcuts also use the OS clipboard."""
        value = paste_text() or self.app.clipboard
        if value and not self.read_only:
            result = self.replace(value, *self.selection)
            self.move_cursor(result.end_location)


def _budget(value: str) -> float | None:
    try:
        number = float(value.strip().replace(",", "."))
    except ValueError:
        return None
    return number if math.isfinite(number) and number > 0 else None


class TaskComposer(App[TaskDraft | None]):
    """Edit and save a workspace draft, returning only an explicit submission."""

    TITLE = "rocky · new task"
    BINDINGS = [
        Binding("ctrl+r", "start_task", "Start task", priority=True),
        Binding("ctrl+q", "save_exit", "Save and exit", priority=True),
    ]
    CSS = """
    Screen { background: #1C2B33; color: #E8F0EA; padding: 0 1; }
    #workspace { height: 1; color: #A8B8BE; }
    #heading { height: 1; text-style: bold; color: #E8F0EA; }
    #guide { height: 1; color: #A8B8BE; }
    #brief { height: 1fr; min-height: 3; border: solid #3D9B8F; }
    #brief:focus { border: solid #F07167; }
    #budget-row { height: 3; }
    #budget-label { width: 15; padding: 1 0; }
    #budget { width: 14; height: 3; }
    #budget-hint { width: 1fr; padding: 1 1; color: #A8B8BE; }
    #save-status { height: 1; color: #A8B8BE; }
    #error { height: auto; max-height: 3; color: #FFB4A8; }
    #actions { height: 3; }
    #actions Button { min-width: 0; margin-right: 1; }
    #start { width: 14; }
    #save-exit { width: 16; }
    #exit-unsaved { width: 24; margin-right: 0; }
    #keys { height: 1; color: #A8B8BE; }
    """

    def __init__(
        self,
        workspace: Path,
        *,
        budget_eur: float,
        budget_override: bool = False,
        initial: TaskDraft | None = None,
        error: str = "",
    ) -> None:
        super().__init__()
        self.workspace = Path(workspace).expanduser().resolve()
        self.external_error = error
        self.save_error = ""
        self._load_error = False
        self._restored = False
        if initial is None:
            try:
                initial = load_draft(self.workspace)
                self._restored = initial is not None
            except (OSError, ValueError) as exc:
                self.save_error = f"{exc} (existing file kept)"
                self._load_error = True
        source = initial or TaskDraft("", f"{budget_eur:g}")
        self.saved_revision = source.revision or source.base_revision
        self._saved_values = (source.goal, source.budget_text) if source.revision else None
        budget_text = f"{budget_eur:g}" if budget_override else source.budget_text
        self._values = (source.goal, budget_text)
        self._observed_values = self._values
        self._touched = False
        self._composer_ready = False
        self._finished = False
        self._validation_requested = False
        self._save_timer = None

    def compose(self) -> ComposeResult:
        yield Static("", id="workspace")
        yield Static("What should Rocky deliver?", id="heading")
        yield Static("Paste a brief. Include the output and any limits.", id="guide")
        yield BriefArea(self._values[0], soft_wrap=True, tab_behavior="focus", id="brief")
        with Horizontal(id="budget-row"):
            yield Static("Budget · EUR", id="budget-label")
            yield Input(self._values[1], id="budget")
            yield Static("for this task", id="budget-hint")
        yield Static("", id="save-status")
        yield Static("", id="error")
        with Horizontal(id="actions"):
            yield Button("Start task", variant="primary", id="start")
            yield Button("Save & exit", id="save-exit")
            yield Button("Exit without saving", id="exit-unsaved")
        yield Static("Enter: new line · Ctrl+R: start · Ctrl+Q: save & exit", id="keys")

    def on_mount(self) -> None:
        self._composer_ready = True
        self.query_one("#brief", BriefArea).border_title = "Task brief"
        self.query_one("#brief", BriefArea).focus()
        self._update_workspace()
        self._refresh()
        # A fresh blank composer must not replace another terminal's draft.
        if self._values[0] and self._values != self._saved_values:
            self._schedule_save()

    def _update_workspace(self) -> None:
        prefix = "Workspace: "
        path = str(self.workspace)
        width = max(1, self.size.width - 2 - cell_len(prefix))
        if cell_len(path) > width:
            while path and cell_len(path) > width - 1:
                path = path[1:]
            path = "…" + path
        self.query_one("#workspace", Static).update(Text(prefix + path))

    def on_resize(self) -> None:
        if self._composer_ready:
            self._update_workspace()

    def _capture(self) -> tuple[str, str]:
        self._values = (
            self.query_one("#brief", BriefArea).text,
            self.query_one("#budget", Input).value,
        )
        return self._values

    def _refresh(self) -> None:
        goal, budget = self._values
        valid_budget = _budget(budget) is not None
        self.query_one("#start", Button).disabled = not goal.strip() or not valid_budget
        errors = [message for message in (self.external_error, self.save_error) if message]
        if not valid_budget:
            errors.append("Budget must be a finite amount greater than zero.")
        if self._validation_requested and not goal.strip():
            errors.append("Write a task before starting.")
        error_box = self.query_one("#error", Static)
        error_box.update(Text("\n".join(errors)))
        error_box.display = bool(errors)
        self.query_one("#exit-unsaved", Button).display = bool(self.save_error)
        if self.save_error:
            status = "Draft not saved · you can still start this task."
        elif self.saved_revision and self._values == self._saved_values:
            status = "Restored draft · saved in this workspace." if self._restored else "Draft saved in this workspace."
        elif self._touched or goal:
            status = "Saving draft…"
        else:
            status = "New task · changes are saved in this workspace."
        self.query_one("#save-status", Static).update(Text(status))

    def _cancel_timer(self) -> None:
        if self._save_timer is not None:
            self._save_timer.stop()
            self._save_timer = None

    def _schedule_save(self) -> None:
        self._cancel_timer()
        self._save_timer = self.set_timer(0.35, self._autosave)

    def _autosave(self) -> None:
        self._save_timer = None
        if not self._finished:
            self._capture()
            self._save_now()

    def _save_now(self, *, refresh: bool = True) -> bool:
        self._cancel_timer()
        if self._load_error:
            if refresh:
                self._refresh()
            return False
        if self.saved_revision and self._values == self._saved_values and not self.save_error:
            return True
        if not self._touched and not self._values[0] and self._saved_values is None:
            return True
        try:
            saved = save_draft(self.workspace, *self._values, expected_revision=self.saved_revision)
        except (OSError, ValueError) as exc:
            self.save_error = f"Draft could not be saved: {exc}"
            if refresh:
                self._refresh()
            return False
        self.saved_revision = saved.revision
        self._saved_values = (saved.goal, saved.budget_text)
        self.save_error = ""
        self._restored = False
        if refresh:
            self._refresh()
        return True

    def _edited(self) -> None:
        if not self._composer_ready or self._finished:
            return
        self._capture()
        if self._values == self._observed_values:
            return
        self._observed_values = self._values
        self._touched = True
        self._restored = False
        self._refresh()
        self._schedule_save()

    def copy_to_clipboard(self, text: str) -> None:
        super().copy_to_clipboard(text)
        copy_text(text)

    @on(TextArea.Changed, "#brief")
    def _brief_changed(self) -> None:
        self._edited()

    @on(Input.Changed, "#budget")
    def _budget_changed(self) -> None:
        self._edited()

    def action_start_task(self) -> None:
        if self._finished:
            return
        goal, budget = self._capture()
        self._validation_requested = True
        self._refresh()
        if not goal.strip() or _budget(budget) is None:
            self.query_one("#brief" if not goal.strip() else "#budget").focus()
            return
        saved = self._save_now()
        self._finished = True
        self.exit(TaskDraft(goal, budget, revision=self.saved_revision if saved else "",
                            base_revision="" if saved else self.saved_revision))

    def action_save_exit(self) -> None:
        if self._finished:
            return
        self._capture()
        if self._save_now():
            self._finished = True
            self.exit(None)

    @on(Button.Pressed, "#start")
    def _start_pressed(self) -> None:
        self.action_start_task()

    @on(Button.Pressed, "#save-exit")
    def _exit_pressed(self) -> None:
        self.action_save_exit()

    @on(Button.Pressed, "#exit-unsaved")
    def _exit_unsaved(self) -> None:
        self._cancel_timer()
        self._finished = True
        self.exit(None)

    def on_unmount(self) -> None:
        self._cancel_timer()
        if not self._finished:
            self._save_now(refresh=False)


def run_composer(
    workspace: Path,
    *,
    budget_eur: float,
    budget_override: bool = False,
    initial: TaskDraft | None = None,
    error: str = "",
) -> TaskDraft | None:
    app = TaskComposer(
        workspace, budget_eur=budget_eur, budget_override=budget_override,
        initial=initial, error=error,
    )
    result = app.run()
    if result is not None and app.save_error:
        print(f"{app.save_error}\nYour entered brief is ready to run; no saved draft will be cleared.")
    return result
