from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from rocky.config import api_key, base_url
from rocky.engine import orchestrate
from rocky.events import Event
from rocky.llm import OpenRouterClient
from rocky.runs import (
    append_event,
    clear_ask,
    load_resume_state,
    read_meta,
    run_dir,
    take_answer,
    write_ask,
    write_meta,
)


def file_ask_user(folder: Path, question: str, options=None) -> str:
    meta = read_meta(folder)
    meta["status"] = "waiting"
    write_meta(folder, meta)
    write_ask(folder, question, list(options or []))
    try:
        for _ in range(6000):
            answer = take_answer(folder)
            if answer is not None:
                return answer
            time.sleep(0.1)
    finally:
        clear_ask(folder)
        meta = read_meta(folder)
        if meta.get("status") == "waiting":
            meta["status"] = "running"
            write_meta(folder, meta)
    return "user did not answer; continue with documented assumptions"


def run_worker(run_id: str, workspace: str | Path) -> int:
    folder = run_dir(workspace, run_id)
    meta = read_meta(folder)
    meta["pid"] = os.getpid()
    meta["status"] = "running"
    write_meta(folder, meta)

    def emit(event: Event) -> None:
        append_event(folder, event)

    def ask_user(question: str, options=None) -> str:
        return file_ask_user(folder, question, options)

    key = api_key()
    if not key:
        meta["status"] = "error"
        meta["finished"] = _now()
        write_meta(folder, meta)
        emit(Event(type="error", payload={"message": "OPENROUTER_API_KEY is missing"}))
        emit(Event(type="done", payload={"passed": False, "summary": "missing API key"}))
        return 2

    resume = None
    if meta.get("resume_from"):
        try:
            resume = load_resume_state(run_dir(workspace, str(meta["resume_from"])))
        except (FileNotFoundError, OSError, ValueError) as exc:
            emit(Event(type="error", payload={"message": f"cannot resume {meta['resume_from']}: {exc}"}))
            resume = None

    client = OpenRouterClient(key, base_url())
    try:
        result = orchestrate(
            goal=str(meta["goal"]),
            workspace=str(meta["workspace"]),
            client=client,
            emit=emit,
            max_loops=int(meta.get("max_loops") or 4),
            budget_eur=float(meta.get("budget_eur") or 1),
            include_server_tools=bool(meta.get("include_server_tools", True)),
            ask_user=ask_user,
            resume=resume,
        )
        meta = read_meta(folder)
        meta["status"] = result.status
        meta["passed"] = result.passed
        meta["notes"] = list(result.notes)[:10]
        meta["summary"] = (result.summary or "")[:500]
        meta["eur"] = round(float(result.eur or 0), 3)
        meta["usd"] = round(float(result.cost or 0), 3)
        meta["estimated_eur"] = result.assessment.get("estimated_eur")
        meta["complexity"] = result.assessment.get("complexity")
        meta["domain"] = result.assessment.get("domain")
        meta["deliverable_path"] = result.deliverable_path
        meta["deliverable_exists"] = bool(result.deliverable_exists)
        meta["files"] = list(result.files)[:20]
        meta["finished"] = _now()
        write_meta(folder, meta)
        return 0 if result.passed else 1
    except Exception as exc:  # noqa: BLE001
        meta = read_meta(folder)
        meta["status"] = "error"
        meta["passed"] = False
        meta["finished"] = _now()
        write_meta(folder, meta)
        emit(Event(type="error", payload={"message": str(exc)}))
        emit(Event(type="done", payload={"passed": False, "summary": str(exc)}))
        return 1
    finally:
        client.close()


def spawn_worker(meta: dict) -> int:
    import subprocess

    workspace = meta["workspace"]
    folder = run_dir(workspace, meta["id"])
    log = folder / "worker.log"
    cmd = [
        sys.executable,
        "-m",
        "rocky.job",
        str(meta["id"]),
        "--dir",
        str(workspace),
    ]
    env = os.environ.copy()
    pkg_root = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = pkg_root + os.pathsep + env.get("PYTHONPATH", "")
    with log.open("ab") as fh:
        proc = subprocess.Popen(
            cmd,
            start_new_session=True,
            stdout=fh,
            stderr=subprocess.STDOUT,
            cwd=str(workspace),
            env=env,
        )
    meta["pid"] = proc.pid
    meta["status"] = "running"
    write_meta(folder, meta)
    return proc.pid


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="rocky-job")
    parser.add_argument("run_id")
    parser.add_argument("-d", "--dir", default=".")
    args = parser.parse_args(argv)
    return run_worker(args.run_id, args.dir)


if __name__ == "__main__":
    raise SystemExit(main())
