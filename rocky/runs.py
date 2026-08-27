from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rocky.events import Event

TERMINAL = frozenset({"passed", "unmet", "error", "crashed"})


def runs_root(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / ".rocky" / "runs"


def legacy_runs_root(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / ".orche" / "runs"


def run_dir(workspace: str | Path, run_id: str) -> Path:
    workspace = Path(workspace).expanduser().resolve()
    modern = runs_root(workspace) / run_id
    if modern.exists() or not (legacy_runs_root(workspace) / run_id).exists():
        return modern
    return legacy_runs_root(workspace) / run_id


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_meta(folder: Path, meta: dict[str, Any]) -> None:
    _atomic_write(folder / "meta.json", meta)


def read_meta(folder: Path) -> dict[str, Any]:
    path = folder / "meta.json"
    if not path.exists():
        raise FileNotFoundError(f"no run at {folder}")
    return json.loads(path.read_text(encoding="utf-8"))


def create_run(
    *,
    workspace: str | Path,
    goal: str,
    budget_eur: float,
    max_loops: int,
    include_server_tools: bool,
) -> dict[str, Any]:
    workspace = Path(workspace).expanduser().resolve()
    run_id = new_run_id()
    folder = run_dir(workspace, run_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "events.jsonl").touch()
    meta = {
        "id": run_id,
        "goal": goal,
        "workspace": str(workspace),
        "budget_eur": float(budget_eur),
        "max_loops": int(max_loops),
        "include_server_tools": bool(include_server_tools),
        "status": "starting",
        "pid": None,
        "created": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "finished": None,
        "passed": None,
    }
    write_meta(folder, meta)
    return meta


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    return True


def refresh_meta(folder: Path) -> dict[str, Any]:
    meta = read_meta(folder)
    status = meta.get("status") or ""
    if status not in TERMINAL and not pid_alive(meta.get("pid")):
        if status in {"running", "waiting", "starting"}:
            meta["status"] = "crashed"
            meta["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            write_meta(folder, meta)
    return meta


def list_runs(workspace: str | Path, limit: int = 40) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in (runs_root(workspace), legacy_runs_root(workspace)):
        if not root.is_dir():
            continue
        for folder in root.iterdir():
            if not folder.is_dir() or not (folder / "meta.json").exists():
                continue
            if folder.name in seen:
                continue
            try:
                rows.append(refresh_meta(folder))
                seen.add(folder.name)
            except (OSError, json.JSONDecodeError):
                continue
    rows.sort(key=lambda r: str(r.get("created") or r.get("id") or ""), reverse=True)
    return rows[:limit]


def resolve_run_id(workspace: str | Path, ident: str | None) -> str:
    rows = list_runs(workspace)
    if not rows:
        raise FileNotFoundError("no rocky runs in this folder")
    if not ident:
        return str(rows[0]["id"])
    needle = ident.strip()
    for row in rows:
        if str(row["id"]) == needle or str(row["id"]).startswith(needle):
            return str(row["id"])
    raise FileNotFoundError(f"no run matching {ident!r}")


def append_event(folder: Path, event: Event) -> None:
    path = folder / "events.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event.as_dict(), ensure_ascii=False) + "\n")
        fh.flush()


class EventCursor:
    def __init__(self, path: Path):
        self.path = path
        self.pos = 0

    def poll(self) -> list[Event]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as fh:
            fh.seek(self.pos)
            chunk = fh.read()
            self.pos = fh.tell()
        events: list[Event] = []
        for line in chunk.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(Event.from_dict(json.loads(line)))
            except json.JSONDecodeError:
                continue
        return events


def write_ask(folder: Path, question: str, options: list[str] | None) -> None:
    _atomic_write(
        folder / "ask.json",
        {"question": question, "options": list(options or []), "ts": time.time()},
    )


def read_ask(folder: Path) -> dict[str, Any] | None:
    path = folder / "ask.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def clear_ask(folder: Path) -> None:
    for name in ("ask.json", "answer.json"):
        path = folder / name
        if path.exists():
            path.unlink()


def write_answer(folder: Path, text: str) -> None:
    _atomic_write(folder / "answer.json", {"text": text, "ts": time.time()})


def take_answer(folder: Path) -> str | None:
    path = folder / "answer.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    path.unlink(missing_ok=True)
    ask = folder / "ask.json"
    if ask.exists():
        ask.unlink()
    return str(data.get("text") or "")
