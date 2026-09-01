from __future__ import annotations

import ipaddress
import json
import threading
import uuid
from pathlib import Path
from queue import Empty, Queue
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from rocky.config import allowed_workspaces, api_key, base_url, serve_token
from rocky.engine import orchestrate
from rocky.events import Event
from rocky.llm import OpenRouterClient

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="rocky")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_runs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


class RunRequest(BaseModel):
    goal: str
    workspace: str
    budget: float = Field(default=1.0, ge=0.05, le=50)
    max_loops: int = Field(default=4, ge=1, le=12)


def _auth(request: Request) -> None:
    token = serve_token()
    if not token:
        return
    header = request.headers.get("authorization") or ""
    query = request.query_params.get("token") or ""
    cookie = request.cookies.get("rocky_token") or request.cookies.get("orche_token") or ""
    supplied = ""
    if header.lower().startswith("bearer "):
        supplied = header[7:].strip()
    supplied = supplied or query or cookie
    if supplied != token:
        raise HTTPException(status_code=401, detail="Set ROCKY_TOKEN in the page or Authorization header.")


def _workspace_ok(path: Path) -> bool:
    allowed = allowed_workspaces()
    if not allowed:
        return path.is_dir() or not path.exists()
    resolved = path.resolve()
    for root in allowed:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/meta")
def meta(request: Request):
    _auth(request)
    return {
        "needs_token": bool(serve_token()),
        "workspaces": [str(p) for p in allowed_workspaces()],
        "default_workspace": str(Path.cwd()),
    }


@app.get("/api/runs")
def list_runs(request: Request):
    _auth(request)
    with _lock:
        rows = []
        for run_id, row in _runs.items():
            rows.append(
                {
                    "id": run_id,
                    "goal": row["goal"],
                    "status": row["status"],
                    "passed": row.get("passed"),
                    "cost": row.get("cost", 0),
                    "eur": row.get("eur", 0),
                    "total_tokens": row.get("total_tokens", 0),
                }
            )
        return list(reversed(rows))


@app.post("/api/runs")
def start_run(body: RunRequest, request: Request):
    _auth(request)
    if not api_key():
        raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY is missing on the server.")
    workspace = Path(body.workspace).expanduser().resolve()
    if not _workspace_ok(workspace):
        raise HTTPException(status_code=400, detail="Workspace is not allowed.")
    workspace.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:10]
    queue: Queue = Queue()
    record = {
        "id": run_id,
        "goal": body.goal,
        "workspace": str(workspace),
        "status": "running",
        "passed": None,
        "cost": 0.0,
        "eur": 0.0,
        "total_tokens": 0,
        "queue": queue,
        "events": [],
    }
    with _lock:
        _runs[run_id] = record

    def emit(event: Event) -> None:
        data = event.as_dict()
        record["events"].append(data)
        if event.type in {"cost", "done"}:
            record["cost"] = event.payload.get("usd") or record["cost"]
            record["eur"] = event.payload.get("eur") or record["eur"]
            record["total_tokens"] = event.payload.get("total_tokens") or record["total_tokens"]
        queue.put(data)

    def work() -> None:
        client = OpenRouterClient(api_key(), base_url())
        try:
            result = orchestrate(
                goal=body.goal,
                workspace=workspace,
                client=client,
                emit=emit,
                max_loops=body.max_loops,
                budget_eur=body.budget,
            )
            record["status"] = result.status
            record["passed"] = result.passed
            record["cost"] = result.cost
            record["eur"] = result.eur
            record["total_tokens"] = result.total_tokens
        except Exception as exc:  # noqa: BLE001
            record["status"] = "error"
            queue.put({"type": "error", "payload": {"message": str(exc)}, "ts": ""})
        finally:
            client.close()
            queue.put(None)

    threading.Thread(target=work, daemon=True).start()
    return {"id": run_id}


@app.get("/api/runs/{run_id}/events")
def stream_events(run_id: str, request: Request):
    _auth(request)
    with _lock:
        record = _runs.get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Unknown run")

    def gen():
        for event in list(record["events"]):
            yield f"data: {json.dumps(event)}\n\n"
        queue: Queue = record["queue"]
        while True:
            try:
                item = queue.get(timeout=15)
            except Empty:
                yield ": ping\n\n"
                continue
            if item is None:
                yield "event: end\ndata: {}\n\n"
                break
            yield f"data: {json.dumps(item)}\n\n"

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
