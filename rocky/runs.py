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


def run_dir(workspace: str | Path, run_id: str) -> Path:
    return runs_root(workspace) / run_id


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
    follow_up: str = "",
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
    if follow_up:
        meta["follow_up"] = str(follow_up)
    write_meta(folder, meta)
    return meta


def pid_alive(pid: int | None, mark: str | None = None) -> bool:
    """True when ``pid`` is a live process and (when ``mark`` is set) its command line contains it.

    Pids are recycled; after a reboot the old number can belong to anything.
    Treating that as "still running" hides a crashed run behind a `running`
    status forever, and `rocky stop` would kill a stranger. A job is spawned
    as `python -m rocky.job <run id>`, so the run id is the mark.
    """
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    if not mark:
        return True
    command = _pid_command(int(pid))
    if command is None:
        return True  # cannot tell (no ps) — keep the old behaviour
    return mark in command


def _pid_command(pid: int) -> str | None:
    import subprocess

    try:
        out = subprocess.run(
            ["ps", "-ww", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return ""
    return out.stdout.strip()


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
    if pid_alive(pid, mark=run_id):
        try:
            os.kill(int(pid), signal.SIGTERM)
        except OSError:
            pass
        deadline = time.monotonic() + max(float(grace), 0.0)
        while pid_alive(pid, mark=run_id) and time.monotonic() < deadline:
            time.sleep(0.05)
        if pid_alive(pid, mark=run_id):
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
    if status not in TERMINAL and not pid_alive(meta.get("pid"), mark=str(meta.get("id") or "")):
        if status in {"running", "waiting", "starting"}:
            meta["status"] = "crashed"
            meta["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            write_meta(folder, meta)
    return meta


def list_runs(workspace: str | Path, limit: int = 40) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    root = runs_root(workspace)
    if root.is_dir():
        for folder in root.iterdir():
            if not folder.is_dir() or not (folder / "meta.json").exists():
                continue
            try:
                rows.append(refresh_meta(folder))
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


def list_runs_priced(workspace: str | Path, limit: int = 40) -> list[dict[str, Any]]:
    """list_runs, with `eur` / `complexity` filled from events for runs whose meta predates those fields."""
    rows = list_runs(workspace, limit=limit)
    for row in rows:
        if row.get("eur") is None and str(row.get("status") or "") in TERMINAL:
            found = _estimate_and_cost_from_events(run_dir(workspace, str(row["id"])) / "events.jsonl")
            if found.get("eur") is not None:
                row["eur"] = found["eur"]
            if row.get("complexity") is None and found.get("complexity"):
                row["complexity"] = found["complexity"]
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


def resolve_resumable(workspace: str | Path, ident: str | None, follow_up: bool = False) -> str:
    """A run id `rocky resume` may continue: the given one, or the latest that did not pass.

    With ``follow_up`` any finished run qualifies — a passed run is the usual
    target of "now make it shorter".
    """
    allowed = TERMINAL if follow_up else RESUMABLE
    if ident:
        run_id = resolve_run_id(workspace, ident)
        status = str(refresh_meta(run_dir(workspace, run_id)).get("status") or "")
        if status not in allowed:
            if status in TERMINAL:
                raise FileNotFoundError(
                    f"run {run_id} is {status} — it needs no resume; to build on it, say what to change: "
                    f'rocky resume {run_id[:15]} "make it shorter"'
                )
            raise FileNotFoundError(f"run {run_id} is {status or 'unknown'} — it is still going; rocky attach or rocky stop first")
        return run_id
    for row in list_runs(workspace):
        if str(row.get("status") or "") not in allowed:
            continue
        try:
            load_resume_state(run_dir(workspace, str(row["id"])))
        except (FileNotFoundError, OSError, ValueError):
            continue  # died before it had a plan
        return str(row["id"])
    raise FileNotFoundError("no unmet, error, or crashed run with a plan to resume in this folder")


def load_resume_state(folder: Path, follow_up: str = "") -> dict[str, Any]:
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
    for key in ("assessment", "critique", "plan"):
        if state[key]:
            state[key].pop("resumed", None)  # set by the run being resumed; this run sets its own
    state["replan"] = judged
    state["done_steps"] = [] if judged else [i for i in done if i not in incomplete]
    follow_up = (follow_up or "").strip()
    if follow_up:
        # A new instruction on top of the old result: keep assessment, critic, cast and
        # the deliverable path; replan from the instruction as if the verifier had asked for it.
        state["follow_up"] = follow_up
        state["goal"] = follow_up
        state["replan"] = True
        state["done_steps"] = []
        state["verify"] = {
            "passed": False,
            "score": 0.0,
            "summary": f"The user looked at the previous result and asked: {follow_up}",
            "blocking": [follow_up],
            "issues": [],
            "remaining": [],
        }
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


def _parse_ts(raw: object) -> datetime | None:
    try:
        ts = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def when_label(row: dict[str, Any], now: datetime | None = None) -> str:
    """`14:02` for today, `09-04` for an earlier day, `—` when unknown. Local time."""
    ts = _parse_ts(row.get("finished") or row.get("created"))
    if ts is None:
        return "—"
    now = now or datetime.now(timezone.utc)
    local = ts.astimezone()
    if local.date() == now.astimezone().date():
        return local.strftime("%H:%M")
    return local.strftime("%m-%d")


def summarize_costs(rows: list[dict[str, Any]], now: datetime | None = None) -> dict[str, Any]:
    """What this folder has spent: totals, today, last 7 days, per status, costliest runs."""
    now = now or datetime.now(timezone.utc)
    total = today = week = 0.0
    by_status: dict[str, int] = {}
    passed_eur: list[float] = []
    priced: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        status = str(row.get("status") or "?")
        by_status[status] = by_status.get(status, 0) + 1
        try:
            eur = float(row.get("eur") or 0.0)
        except (TypeError, ValueError):
            eur = 0.0
        total += eur
        ts = _parse_ts(row.get("finished") or row.get("created"))
        if ts is not None:
            age = now - ts
            if age.total_seconds() < 7 * 86400:
                week += eur
            if ts.astimezone().date() == now.astimezone().date():
                today += eur
        if status in {"passed", "passed_with_notes"}:
            passed_eur.append(eur)
        if eur > 0:
            priced.append((eur, row))
    priced.sort(key=lambda t: t[0], reverse=True)
    finished = sum(v for k, v in by_status.items() if k in TERMINAL)
    usable = len(passed_eur)
    return {
        "runs": len(rows),
        "finished": finished,
        "usable": usable,
        "by_status": by_status,
        "total_eur": round(total, 2),
        "today_eur": round(today, 2),
        "week_eur": round(week, 2),
        "per_usable_eur": round(total / usable, 2) if usable else None,
        "wasted_eur": round(sum(e for e, r in priced if str(r.get("status")) not in {"passed", "passed_with_notes"}), 2),
        "top": [
            {"id": r.get("id"), "eur": round(e, 2), "status": r.get("status"), "goal": str(r.get("goal") or "")[:60]}
            for e, r in priced[:3]
        ],
    }
