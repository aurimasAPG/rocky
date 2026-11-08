"""`rocky serve`: the team web UI.

Runs are ordinary background jobs (rocky.job), so a run started here shows in
`rocky ps`, can be attached from a terminal, answered, stopped, and resumed.
Every viewer follows events.jsonl with its own cursor — nothing is held in
process memory that a second tab or a restart would lose.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from rocky.adboard import BOARD_PATH, current_checks, fix_instruction
from rocky.config import allowed_workspaces, api_key, serve_token
from rocky.job import spawn_worker
from rocky.lang import detect_language
from rocky.playbook import FIX_CAP_EUR, checks_for, elements, for_code, library, search, set_switched_off, switched_off
from rocky.recipes import find_recipe, load_recipes
from rocky.runs import (
    TERMINAL,
    EventCursor,
    create_run,
    list_runs,
    load_resume_state,
    read_ask,
    refresh_meta,
    run_dir,
    stop_run,
    write_answer,
)
from rocky.tools import WorkspaceTools

STATIC = Path(__file__).parent / "static"
POLL = 0.3

app = FastAPI(title="rocky")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

# run id → workspace for runs started from this server (a client may also pass ?workspace=).
_started: dict[str, Path] = {}
_lock = threading.Lock()


class RunRequest(BaseModel):
    goal: str
    workspace: str
    budget: float = Field(default=1.0, ge=0.05, le=50)
    max_loops: int = Field(default=4, ge=1, le=12)


class AnswerRequest(BaseModel):
    text: str
    workspace: str = ""


class StopRequest(BaseModel):
    workspace: str = ""


class ToggleRequest(BaseModel):
    workspace: str
    number: int
    off: bool


class ApplyRequest(BaseModel):
    workspace: str = ""
    number: int


class FixRequest(BaseModel):
    workspace: str = ""
    ad: int = Field(default=0, ge=0)
    code: str
    field: str = ""
    value: str = ""


def _auth(request: Request) -> None:
    token = serve_token()
    if not token:
        return
    header = request.headers.get("authorization") or ""
    query = request.query_params.get("token") or ""
    cookie = request.cookies.get("rocky_token") or ""
    supplied = ""
    if header.lower().startswith("bearer "):
        supplied = header[7:].strip()
    supplied = supplied or cookie or query
    if not hmac.compare_digest(supplied.encode("utf-8"), token.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Set ROCKY_TOKEN in the page or Authorization header.")


def _workspace_ok(path: Path) -> bool:
    """Allowed folders from ROCKY_WORKSPACES; without it, only the folder the server was started in."""
    allowed = allowed_workspaces() or [Path.cwd().resolve()]
    resolved = path.resolve()
    for root in allowed:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _workspace_for(run_id: str, raw: str) -> Path:
    with _lock:
        known = _started.get(run_id)
    if known is not None:
        return known
    if not raw:
        raise HTTPException(status_code=404, detail="Unknown run — pass ?workspace=")
    workspace = Path(raw).expanduser().resolve()
    if not _workspace_ok(workspace):
        raise HTTPException(status_code=400, detail="Workspace is not allowed.")
    if not (run_dir(workspace, run_id) / "meta.json").exists():
        raise HTTPException(status_code=404, detail="Unknown run")
    return workspace


def _row(meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": meta.get("id"),
        "goal": meta.get("goal"),
        "status": meta.get("status"),
        "passed": meta.get("passed"),
        "eur": meta.get("eur"),
        "created": meta.get("created"),
        "finished": meta.get("finished"),
        "deliverable_path": meta.get("deliverable_path"),
        "deliverable_exists": bool(meta.get("deliverable_exists")),
        "files": list(meta.get("files") or []),
        "summary": meta.get("summary") or "",
        "notes": list(meta.get("notes") or []),
        "resume_from": meta.get("resume_from"),
    }


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/meta")
def meta(request: Request):
    _auth(request)
    return {
        "needs_token": bool(serve_token()),
        "workspaces": [str(p) for p in (allowed_workspaces() or [Path.cwd().resolve()])],
        "default_workspace": str(Path.cwd().resolve()),
    }


@app.get("/api/runs")
def runs(request: Request, workspace: str = ""):
    _auth(request)
    folder = Path(workspace or Path.cwd()).expanduser().resolve()
    if not _workspace_ok(folder):
        raise HTTPException(status_code=400, detail="Workspace is not allowed.")
    return [_row(m) for m in list_runs(folder)]


@app.post("/api/runs")
def start_run(body: RunRequest, request: Request):
    _auth(request)
    if not api_key():
        raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY is missing on the server.")
    goal = (body.goal or "").strip()
    if not goal:
        raise HTTPException(status_code=400, detail="Goal is empty.")
    workspace = Path(body.workspace).expanduser().resolve()
    if not _workspace_ok(workspace):
        raise HTTPException(status_code=400, detail="Workspace is not allowed.")
    workspace.mkdir(parents=True, exist_ok=True)
    meta = create_run(
        workspace=workspace,
        goal=goal,
        budget_eur=body.budget,
        max_loops=body.max_loops,
        include_server_tools=True,
    )
    spawn_worker(meta)
    with _lock:
        _started[meta["id"]] = workspace
    return {"id": meta["id"], "workspace": str(workspace)}


@app.get("/api/runs/{run_id}")
def run_status(run_id: str, request: Request, workspace: str = ""):
    _auth(request)
    folder = run_dir(_workspace_for(run_id, workspace), run_id)
    row = _row(refresh_meta(folder))
    ask = read_ask(folder)
    row["ask"] = ask
    return row


@app.post("/api/runs/{run_id}/answer")
def answer(run_id: str, body: AnswerRequest, request: Request):
    _auth(request)
    folder = run_dir(_workspace_for(run_id, body.workspace), run_id)
    if read_ask(folder) is None:
        raise HTTPException(status_code=409, detail="The run is not asking anything right now.")
    write_answer(folder, (body.text or "").strip())
    return {"ok": True}


@app.post("/api/runs/{run_id}/stop")
def stop(run_id: str, body: StopRequest, request: Request):
    _auth(request)
    workspace = _workspace_for(run_id, body.workspace)
    meta = stop_run(workspace, run_id)
    return _row(meta)


def _follow_up(workspace: Path, run_id: str, instruction: str) -> dict[str, str]:
    """One capped follow-up on a finished run. Two at once would overwrite each other's page."""
    if not api_key():
        raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY is missing on the server.")
    if any(str(row.get("status") or "") not in TERMINAL for row in list_runs(workspace)):
        raise HTTPException(status_code=409, detail="A run is still going in this folder.")
    try:
        state = load_resume_state(run_dir(workspace, run_id), follow_up=instruction)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    meta = create_run(
        workspace=workspace,
        goal=instruction,
        budget_eur=FIX_CAP_EUR,
        max_loops=1,
        include_server_tools=bool(state.get("include_server_tools", True)),
        resume_from=run_id,
        follow_up=instruction,
    )
    spawn_worker(meta)
    with _lock:
        _started[meta["id"]] = workspace
    return {"id": meta["id"], "workspace": str(workspace)}


def _folder(raw: str) -> Path:
    folder = Path(raw or Path.cwd()).expanduser().resolve()
    if not _workspace_ok(folder):
        raise HTTPException(status_code=400, detail="Workspace is not allowed.")
    return folder


@app.get("/api/playbook/preview")
def playbook_preview(request: Request, goal: str = "", workspace: str = ""):
    """What Rocky will check for this goal, as soon as it recognises the kind of work."""
    _auth(request)
    folder = _folder(workspace)
    recipe = find_recipe(goal, None, load_recipes(folder))
    language = "lt" if detect_language(goal) == "lt" else "en"
    off = switched_off(folder)
    return {
        "title": "Rocky patikrins:" if language == "lt" else "Rocky will check:",
        "edit": "keisti" if language == "lt" else "edit",
        "items": [
            {"number": e.number, "question": e.question[language], "off": e.number in off}
            for e in (checks_for(recipe.name) if recipe else [])
        ],
    }


@app.post("/api/playbook/toggle")
def playbook_toggle(body: ToggleRequest, request: Request):
    _auth(request)
    folder = _folder(body.workspace)
    if not any(body.number == e.number and e.codes for e in checks_for("ads")):
        raise HTTPException(status_code=400, detail="This one cannot be switched off.")
    return {"off": sorted(set_switched_off(folder, body.number, body.off))}


@app.get("/api/playbook/search")
def playbook_search(request: Request, q: str = "", topic: str = "", workspace: str = ""):
    _auth(request)
    entries = library(_folder(workspace))
    applied = {e.number for e in elements()}
    return {
        "topics": list(dict.fromkeys(entry.topic for entry in entries)),
        "apply_label": f"Apply to this campaign (up to €{FIX_CAP_EUR:.2f})",
        "results": [
            {"number": e.number, "title": e.title, "body": e.body, "source": e.source, "topic": e.topic, "applied": e.number in applied}
            for e in search(entries, q, topic)
        ],
    }


@app.post("/api/runs/{run_id}/apply")
def playbook_apply(run_id: str, body: ApplyRequest, request: Request):
    """Apply one playbook entry to a finished run. The instruction comes from the entry, not the request."""
    _auth(request)
    workspace = _workspace_for(run_id, body.workspace)
    entry = next((e for e in library(workspace) if e.number == body.number), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="No such playbook entry.")
    instruction = f"Apply this marketing principle to the campaign and change only what it calls for. {entry.title}. {entry.body}"
    return _follow_up(workspace, run_id, instruction)


@app.post("/api/runs/{run_id}/fix")
def fix(run_id: str, body: FixRequest, request: Request):
    """Fix one finding. It must still be on the page, so the request cannot carry its own instruction."""
    _auth(request)
    workspace = _workspace_for(run_id, body.workspace)
    element = for_code(body.code)
    if element is None or not element.fixable(body.code):
        raise HTTPException(status_code=400, detail="Rocky cannot fix this one.")
    target = (body.ad, body.code, body.field, body.value)
    found = next(
        (c for c in current_checks(WorkspaceTools(workspace)) or [] if (c.ad, c.code, c.field, c.value) == target),
        None,
    )
    if found is None:
        raise HTTPException(status_code=409, detail="That point is no longer on the page.")
    return _follow_up(workspace, run_id, fix_instruction(found))


@app.get("/api/runs/{run_id}/board")
def board(run_id: str, request: Request, workspace: str = ""):
    """The ad page for the viewer in the web UI. Its text comes from models and websites, so it is sandboxed."""
    _auth(request)
    ws = _workspace_for(run_id, workspace)
    path = ws / BOARD_PATH
    if refresh_meta(run_dir(ws, run_id)).get("deliverable_path") != BOARD_PATH or not path.is_file():
        raise HTTPException(status_code=404, detail="This run has no ad page.")
    return FileResponse(
        path,
        media_type="text/html",
        headers={
            "Content-Security-Policy": "sandbox allow-scripts allow-downloads allow-popups allow-popups-to-escape-sandbox",
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/runs/{run_id}/events")
def stream_events(run_id: str, request: Request, workspace: str = ""):
    _auth(request)
    ws = _workspace_for(run_id, workspace)
    folder = run_dir(ws, run_id)

    def gen():
        cursor = EventCursor(folder / "events.jsonl")
        asked_ts = None
        idle = 0.0
        while True:
            events = cursor.poll()
            for event in events:
                yield f"data: {json.dumps(event.as_dict(), ensure_ascii=False)}\n\n"
            try:
                meta = refresh_meta(folder)
            except FileNotFoundError:
                yield "event: end\ndata: {}\n\n"
                return
            ask = read_ask(folder)
            if ask and ask.get("ts") != asked_ts:
                asked_ts = ask.get("ts")
                yield f"data: {json.dumps({'type': 'ask', 'payload': ask, 'ts': ask.get('ts')}, ensure_ascii=False)}\n\n"
            if str(meta.get("status") or "") in TERMINAL:
                for event in cursor.poll():
                    yield f"data: {json.dumps(event.as_dict(), ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'meta', 'payload': _row(meta), 'ts': ''}, ensure_ascii=False)}\n\n"
                yield "event: end\ndata: {}\n\n"
                return
            if events:
                idle = 0.0
            else:
                idle += POLL
                if idle >= 15.0:
                    idle = 0.0
                    yield ": ping\n\n"
            time.sleep(POLL)

    return StreamingResponse(gen(), media_type="text/event-stream")


class ServeRefused(RuntimeError):
    """Raised when `rocky serve` would bind a public interface without a token."""


def is_loopback_host(host: str) -> bool:
    raw = (host or "").strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    if raw.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(raw).is_loopback
    except ValueError:
        return False


def ensure_serve_allowed(host: str, token: str) -> None:
    """Refuse a non-loopback bind when ROCKY_TOKEN is empty. Called before uvicorn binds."""
    if is_loopback_host(host):
        return
    if (token or "").strip():
        return
    raise ServeRefused(
        "ROCKY_TOKEN is required when binding a non-loopback host. "
        "Set ROCKY_TOKEN or use --host 127.0.0.1."
    )


def serve(host: str = "127.0.0.1", port: int = 8787) -> None:
    import uvicorn

    ensure_serve_allowed(host, serve_token())
    uvicorn.run(app, host=host, port=port, log_level="info")
