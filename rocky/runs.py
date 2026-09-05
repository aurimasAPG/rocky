from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rocky.events import Event

TERMINAL = frozenset({"passed", "passed_with_notes", "unmet", "error", "crashed", "stopped"})
# Runs that `rocky resume` may pick up: they stopped without a clean pass.
RESUMABLE = frozenset({"unmet", "error", "crashed", "stopped"})
# How long `rocky stop` gives the job to exit after SIGTERM before SIGKILL.
STOP_GRACE = 3.0


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
    resume_from: str | None = None,
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
    if resume_from:
        meta["resume_from"] = str(resume_from)
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


def stop_run(workspace: str | Path, run_id: str, grace: float = STOP_GRACE) -> dict[str, Any]:
    """End a running job now. Files already written stay; the run becomes `stopped` and resumable.

    SIGTERM first, SIGKILL after ``grace`` seconds. meta.json and a final
    `done` event are written here, so a viewer that is attached sees the end
    even if the job died before its own handler ran.
    """
    import signal

    folder = run_dir(workspace, run_id)
    meta = read_meta(folder)
    status = str(meta.get("status") or "")
    if status in TERMINAL:
        return meta
    pid = meta.get("pid")
    if pid_alive(pid):
        try:
            os.kill(int(pid), signal.SIGTERM)
        except OSError:
            pass
        deadline = time.monotonic() + max(float(grace), 0.0)
        while pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        if pid_alive(pid):
            try:
                os.kill(int(pid), signal.SIGKILL)
            except OSError:
                pass
    meta = read_meta(folder)
    if str(meta.get("status") or "") not in TERMINAL:
        meta["status"] = "stopped"
        meta["passed"] = False
        meta["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        meta.setdefault("summary", "stopped by the user")
        write_meta(folder, meta)
        append_event(
            folder,
            Event(type="done", payload={"passed": False, "status": "stopped", "summary": "stopped by the user"}),
        )
    clear_ask(folder)
    return meta


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


def run_history(workspace: str | Path, limit: int = 40) -> list[dict[str, Any]]:
    """Finished runs with what was guessed and what was paid, for estimate calibration.

    Newer runs store eur / estimated_eur / complexity in meta.json; older ones are
    read back from their events (assessed.estimated_eur, done.eur).
    """
    rows: list[dict[str, Any]] = []
    for row in list_runs(workspace, limit=limit):
        if str(row.get("status") or "") not in TERMINAL:
            continue
        est, actual, complexity = row.get("estimated_eur"), row.get("eur"), row.get("complexity")
        if est is None or actual is None:
            found = _estimate_and_cost_from_events(run_dir(workspace, str(row["id"])) / "events.jsonl")
            est = est if est is not None else found.get("estimated_eur")
            actual = actual if actual is not None else found.get("eur")
            complexity = complexity or found.get("complexity")
        if est is None or actual is None:
            continue
        rows.append({"id": row.get("id"), "estimated_eur": est, "eur": actual, "complexity": complexity, "status": row.get("status")})
    return rows


def _estimate_and_cost_from_events(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not path.exists():
        return out
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if '"assessed"' not in line and '"done"' not in line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = raw.get("payload") or {}
                if raw.get("type") == "assessed":
                    out["estimated_eur"] = payload.get("estimated_eur")
                    out["complexity"] = payload.get("complexity")
                elif raw.get("type") == "done" and payload.get("eur") is not None:
                    out["eur"] = payload.get("eur")
    except OSError:
        return out
    return out


def resolve_resumable(workspace: str | Path, ident: str | None) -> str:
    """A run id `rocky resume` may continue: the given one, or the latest that did not pass."""
    if ident:
        run_id = resolve_run_id(workspace, ident)
        status = str(refresh_meta(run_dir(workspace, run_id)).get("status") or "")
        if status not in RESUMABLE:
            raise FileNotFoundError(f"run {run_id} is {status or 'unknown'} — only unmet, error, or crashed runs can be resumed")
        return run_id
    for row in list_runs(workspace):
        if str(row.get("status") or "") not in RESUMABLE:
            continue
        try:
            load_resume_state(run_dir(workspace, str(row["id"])))
        except (FileNotFoundError, OSError, ValueError):
            continue  # died before it had a plan
        return str(row["id"])
    raise FileNotFoundError("no unmet, error, or crashed run with a plan to resume in this folder")


def load_resume_state(folder: Path) -> dict[str, Any]:
    """Everything a new run needs to continue this one: plan, cast, verdict, finished steps.

    All of it already sits in events.jsonl. Steps count as done only when they
    finished after the *last* plan and were not reported incomplete. If a
    verification happened after that plan, the plan was judged and the resumed
    run replans from that verdict instead of skipping anything.
    """
    meta = read_meta(folder)
    events = EventCursor(folder / "events.jsonl").poll()
    state: dict[str, Any] = {
        "from": meta.get("id"),
        "goal": meta.get("goal"),
        "budget_eur": meta.get("budget_eur"),
        "max_loops": meta.get("max_loops"),
        "include_server_tools": meta.get("include_server_tools", True),
        "assessment": None,
        "critique": None,
        "plan": None,
        "cast": None,
        "worker": None,
        "verify": None,
        "replan": False,
        "done_steps": [],
        "loops": 0,
        "eur": None,
    }
    done: list[int] = []
    incomplete: set[int] = set()
    judged = False
    for event in events:
        p = event.payload
        if event.type == "assessed":
            state["assessment"] = dict(p)
        elif event.type == "critique":
            state["critique"] = dict(p)
        elif event.type == "cast":
            state["cast"] = dict(p.get("models") or {})
        elif event.type == "plan":
            state["plan"] = dict(p)
            done, incomplete, judged = [], set(), False
        elif event.type == "verify":
            state["verify"] = dict(p)
            judged = True
        elif event.type == "loop":
            state["loops"] = int(p.get("n") or 0)
        elif event.type == "stall":
            state["worker"] = p.get("worker")
        elif event.type == "subtask_done" and p.get("id") is not None:
            done.append(p.get("id"))
        elif event.type == "subtask_incomplete" and p.get("id") is not None:
            incomplete.add(p.get("id"))
        elif event.type in {"cost", "done"} and p.get("eur") is not None:
            state["eur"] = p.get("eur")
    if state["plan"] is None or state["assessment"] is None:
        raise FileNotFoundError(
            f"run {meta.get('id')} stopped before it had a plan — nothing to resume, start the goal again"
        )
    state["replan"] = judged
    state["done_steps"] = [] if judged else [i for i in done if i not in incomplete]
    return state


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
