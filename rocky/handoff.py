"""A local, interactive review of a finished task; paid work stays with the caller."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from rich.text import Text

from rocky.artifacts import (
    PREVIEW_BYTES,
    TEXT_SUFFIXES,
    artifact_inventory,
    artifact_path,
    declared_artifacts,
    read_preview,
)


@dataclass(frozen=True)
class HandoffAction:
    kind: Literal["refine", "continue", "restart"]
    follow_up: str = ""
    budget_eur: float | None = None


def _clean(value) -> str:
    """Keep literal text, never terminal commands, including C1 control bytes."""
    return "".join(
        char for char in str(value if value is not None else "")
        if char in "\n\t" or (ord(char) >= 32 and not 127 <= ord(char) <= 159)
    )


def _line(value) -> str:
    return _clean(value).replace("\n", " ").replace("\t", " ")


def _print(console, value="", style="") -> None:
    console.print(Text(_clean(value), style=style))


def _lines(console, value) -> list[str]:
    width = max(12, int(console.width) - 2)
    return [line.plain for line in Text(_clean(value).expandtabs(4)).wrap(console, width)]


def _excerpt(console, value, limit: int) -> None:
    lines = _lines(console, value)
    for line in lines[:limit]:
        _print(console, line)
    if len(lines) > limit:
        _print(console, "… shortened for this view", "dim")


def _safe_file(workspace: Path, result: dict, name: str) -> Path | None:
    if name not in declared_artifacts(result):
        return None
    path = artifact_path(workspace, name)
    try:
        return path if path is not None and path.is_file() else None
    except OSError:
        return None


def _preview(console, workspace: Path, result: dict, name: str):
    path = _safe_file(workspace, result, name)
    if path is None:
        _print(console, f"File is missing or no longer available: {_line(name)}", "yellow")
        return None
    if path.suffix.lower() not in TEXT_SUFFIXES:
        _print(console, "No text preview for this format. Open it in its application.", "dim")
        return None
    try:
        return read_preview(path)
    except OSError:
        _print(console, "The file could not be read. It may have moved or changed.", "yellow")
        return None


def _default_file(files: list[dict]) -> str:
    primary = next((file for file in files if file["primary"]), None)
    return (primary or next(iter(files), {})).get("path", "")


def _passed(result: dict) -> bool:
    status = result.get("status")
    return status in {"passed", "passed_with_notes"} if status else bool(result.get("passed"))


def _size(size) -> str:
    if size is None:
        return ""
    return f"{size / 1024:.1f} KiB" if size >= 1024 else f"{size} bytes"


def render_result(console, workspace: Path, result: dict) -> None:
    """Print a bounded review with no input, model calls, or external launches."""
    status = str(result.get("status") or "")
    title = "Ready with notes" if status == "passed_with_notes" else (
        "Ready to review" if _passed(result) else "Needs attention"
    )
    _print(console)
    _print(console, title, "bold green" if _passed(result) else "bold yellow")
    if result.get("run_id"):
        _print(console, f"Task: {_line(result['run_id'])}", "dim")
    if not _passed(result) and status:
        _print(console, f"Task status: {status.replace('_', ' ')}", "dim")
    if result.get("summary"):
        _print(console, "Review verdict", "bold")
        _excerpt(console, result["summary"], 3)
    spend = result.get("eur")
    try:
        amount = float(spend)
        if math.isfinite(amount) and amount >= 0:
            _print(console, f"Spent: EUR {amount:.3f}", "dim")
    except (TypeError, ValueError):
        pass
    for label, key in (("Needs fixing", "blocking"), ("Review issues", "issues"), ("Remaining work", "remaining")):
        items = result.get(key) or []
        if items:
            _print(console, label, "bold")
            _excerpt(console, "\n".join(f"- {item}" for item in items), 2)
    notes = result.get("notes") or []
    if notes:
        _print(console, "Notes", "bold")
        for note in notes[:3]:
            _excerpt(console, f"- {note}", 2)
        if len(notes) > 3:
            _print(console, f"… {len(notes) - 3} more notes", "dim")
    files = artifact_inventory(workspace, result)
    _print(console, "Current workspace files — later tasks may change these files.", "dim")
    if not files:
        _print(console, "No saved file reported.")
        return
    for number, file in enumerate(files, 1):
        state = _size(file["size"]) if file["available"] else "missing"
        main = " · main" if file["primary"] else ""
        _print(console, f"{number}. {_line(file['path'])}{main} · {state}")
    selected = _default_file(files)
    _print(console, f"Preview: {_line(selected)}", "bold")
    preview = _preview(console, workspace, result, selected)
    if preview is not None:
        text, truncated = preview
        _excerpt(console, text or "(empty file)", min(10, max(3, int(console.height) - 18)))
        if truncated:
            _print(console, f"Preview limited to {PREVIEW_BYTES // 1024} KiB; open the file for the full content.", "dim")


def _pages(console, title: str, text: str, *, truncated: bool = False) -> None:
    lines = _lines(console, text or "(empty)")
    count = max(3, int(console.height) - 6)
    offset = 0
    while True:
        _print(console, title, "bold")
        for line in lines[offset:offset + count]:
            _print(console, line)
        more = offset + count < len(lines)
        if not more and truncated:
            _print(console, f"Preview limited to {PREVIEW_BYTES // 1024} KiB; open the file for the full content.", "dim")
        while True:
            prompt = "Enter: next page · q: back > " if more else "Enter: back · q: back > "
            choice = console.input(Text(prompt)).strip().lower()
            if choice == "q" or (not choice and not more):
                return
            if not choice:
                offset += count
                break
            _print(console, "Use Enter to continue or q to go back.", "yellow")


def _cap(console, result: dict) -> float | None:
    try:
        default = float(result.get("budget_eur") or 1.0)
    except (TypeError, ValueError):
        default = 1.0
    if not math.isfinite(default) or default <= 0:
        default = 1.0
    while True:
        raw = console.input(Text(f"New run cap (EUR) [{default:g}] · q: cancel > ")).strip()
        if raw.lower() == "q":
            return None
        try:
            value = float(raw.replace(",", ".")) if raw else default
            if math.isfinite(value) and value > 0:
                return value
        except ValueError:
            pass
        _print(console, "Enter a finite amount greater than zero, or q to cancel.", "yellow")


def choose_action(
    console,
    workspace: Path,
    result: dict,
    *,
    can_resume: bool,
    open_file: Callable[[Path], bool],
) -> HandoffAction | None:
    """Review files, or return an explicit request for the caller to start work."""
    try:
        render_result(console, workspace, result)
        selected = _default_file(artifact_inventory(workspace, result))
        while True:
            files = artifact_inventory(workspace, result)
            file_choices = ["number: preview", "o: open selected"] if files else []
            file_choices.append("v: full verdict and notes")
            choices = []
            if can_resume:
                choices.append("f: refine")
            if not _passed(result):
                choices.append("c: continue" if can_resume else "r: restart original task")
            choices.append("Enter: done")
            _print(console, " · ".join(file_choices), "dim")
            _print(console, " · ".join(choices), "dim")
            if selected:
                _print(console, f"Selected: {_line(selected)}", "dim")
            choice = console.input(Text("Review > ")).strip().lower()
            if not choice or choice == "q":
                return None
            if choice.isdecimal():
                index = int(choice) - 1
                if not 0 <= index < len(files):
                    _print(console, "Choose a file number from the list.", "yellow")
                    continue
                selected = files[index]["path"]
                preview = _preview(console, workspace, result, selected)
                if preview is not None:
                    _pages(console, f"Current workspace file: {_line(selected)}", preview[0], truncated=preview[1])
                continue
            if choice == "o" and selected:
                path = _safe_file(workspace, result, selected)
                if path is None:
                    _print(console, "The selected file is missing or no longer available.", "yellow")
                    continue
                try:
                    opened = open_file(path)
                except (OSError, ValueError):
                    opened = False
                _print(console, f"{'Opened' if opened else 'Could not open'}: {_line(path)}")
                continue
            if choice == "v":
                text = str(result.get("summary") or "No review verdict recorded.")
                for label, key in (("Needs fixing", "blocking"), ("Review issues", "issues"), ("Remaining work", "remaining"), ("Notes", "notes")):
                    items = result.get(key) or []
                    if items:
                        text += "\n\n" + label + "\n" + "\n".join(f"- {item}" for item in items)
                _pages(console, "Review verdict and notes", text)
                continue
            action = None
            follow_up = ""
            if choice == "f" and can_resume:
                follow_up = console.input(Text("What should change? · Enter or q: cancel > ")).strip()
                if not follow_up or follow_up.lower() == "q":
                    continue
                action = "refine"
            elif choice == "c" and can_resume and not _passed(result):
                action = "continue"
            elif choice == "r" and not can_resume and not _passed(result):
                action = "restart"
            if action is not None:
                budget = _cap(console, result)
                if budget is not None:
                    return HandoffAction(action, follow_up, budget)
                continue
            _print(console, "Use one of the choices above. Enter finishes this review.", "yellow")
    except (EOFError, KeyboardInterrupt):
        return None
