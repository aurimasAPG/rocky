from rocky.events import Event
from rocky.runs import (
    EventCursor,
    create_run,
    list_runs,
    pid_alive,
    read_ask,
    refresh_meta,
    resolve_run_id,
    run_dir,
    take_answer,
    write_answer,
    write_ask,
    write_meta,
    append_event,
)


def test_create_list_resolve(tmp_path):
    first = create_run(
        workspace=tmp_path,
        goal="one",
        budget_eur=1.0,
        max_loops=4,
        include_server_tools=True,
    )
    second = create_run(
        workspace=tmp_path,
        goal="two",
        budget_eur=1.0,
        max_loops=4,
        include_server_tools=True,
    )
    rows = list_runs(tmp_path)
    assert [r["id"] for r in rows] == [second["id"], first["id"]]
    assert resolve_run_id(tmp_path, None) == second["id"]
    assert resolve_run_id(tmp_path, second["id"][:10]) == second["id"]


def test_event_cursor(tmp_path):
    meta = create_run(
        workspace=tmp_path,
        goal="e",
        budget_eur=1.0,
        max_loops=2,
        include_server_tools=False,
    )
    folder = run_dir(tmp_path, meta["id"])
    cursor = EventCursor(folder / "events.jsonl")
    assert cursor.poll() == []
    append_event(folder, Event(type="plan", payload={"summary": "hi"}))
    got = cursor.poll()
    assert len(got) == 1
    assert got[0].type == "plan"
    assert cursor.poll() == []


def test_ask_answer(tmp_path):
    meta = create_run(
        workspace=tmp_path,
        goal="q",
        budget_eur=1.0,
        max_loops=2,
        include_server_tools=True,
    )
    folder = run_dir(tmp_path, meta["id"])
    write_ask(folder, "Raise cap?", ["yes", "keep"])
    assert read_ask(folder)["question"] == "Raise cap?"
    assert take_answer(folder) is None
    write_answer(folder, "yes")
    assert take_answer(folder) == "yes"
    assert read_ask(folder) is None


def test_refresh_marks_dead_pid_crashed(tmp_path):
    meta = create_run(
        workspace=tmp_path,
        goal="dead",
        budget_eur=1.0,
        max_loops=2,
        include_server_tools=True,
    )
    folder = run_dir(tmp_path, meta["id"])
    meta["status"] = "running"
    meta["pid"] = 99999999
    write_meta(folder, meta)
    assert pid_alive(99999999) is False
    out = refresh_meta(folder)
    assert out["status"] == "crashed"


def test_attach_replays_finished(tmp_path, monkeypatch):
    from rocky.cli import _attach

    meta = create_run(
        workspace=tmp_path,
        goal="done",
        budget_eur=1.0,
        max_loops=2,
        include_server_tools=True,
    )
    folder = run_dir(tmp_path, meta["id"])
    append_event(folder, Event(type="done", payload={"passed": True, "summary": "ok"}))
    meta["status"] = "passed"
    write_meta(folder, meta)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert _attach(tmp_path, meta["id"]) == 0


def _fake_job(tmp_path, goal="stop me"):
    import subprocess
    import sys

    meta = create_run(workspace=tmp_path, goal=goal, budget_eur=1.0, max_loops=2, include_server_tools=True)
    # The command line carries the run id, as a real `python -m rocky.job <id>` does.
    proc = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep(60)  # {meta['id']}"])
    folder = run_dir(tmp_path, meta["id"])
    meta["status"] = "running"
    meta["pid"] = proc.pid
    write_meta(folder, meta)
    return meta, proc, folder


def test_stop_run_kills_the_job_and_marks_it_stopped(tmp_path):
    from rocky.runs import RESUMABLE, stop_run

    meta, proc, folder = _fake_job(tmp_path)
    write_ask(folder, "Raise budget?", ["yes"])
    out = stop_run(tmp_path, meta["id"], grace=2.0)
    proc.wait(timeout=5)
    assert out["status"] == "stopped" and out["passed"] is False and out["finished"]
    assert "stopped" in RESUMABLE
    assert refresh_meta(folder)["status"] == "stopped", "a stopped run must not flip to crashed"
    assert read_ask(folder) is None
    events = EventCursor(folder / "events.jsonl").poll()
    assert events[-1].type == "done" and events[-1].payload["status"] == "stopped"


def test_stop_run_on_a_finished_run_changes_nothing(tmp_path):
    from rocky.runs import stop_run

    meta = create_run(workspace=tmp_path, goal="done", budget_eur=1.0, max_loops=2, include_server_tools=True)
    folder = run_dir(tmp_path, meta["id"])
    meta["status"] = "passed"
    write_meta(folder, meta)
    assert stop_run(tmp_path, meta["id"])["status"] == "passed"
    assert not (folder / "events.jsonl").read_text().strip()


def test_cli_stop_reports_and_hints_resume(tmp_path, capsys):
    from rocky.cli import main

    meta, proc, folder = _fake_job(tmp_path)
    assert main(["stop", meta["id"][:12], "-d", str(tmp_path)]) == 0
    proc.wait(timeout=5)
    out = capsys.readouterr().out
    assert "stopped" in out and f"rocky resume {meta['id']}" in out
    assert main(["stop", "-d", str(tmp_path)]) == 0
    assert "nothing to stop" in capsys.readouterr().out


def test_ctrl_c_while_attached_detaches_and_names_stop(tmp_path, monkeypatch, capsys):
    from rocky.cli import _attach

    meta = create_run(workspace=tmp_path, goal="long", budget_eur=1.0, max_loops=2, include_server_tools=True)
    folder = run_dir(tmp_path, meta["id"])
    meta["status"] = "running"
    meta["pid"] = __import__("os").getpid()
    write_meta(folder, meta)
    monkeypatch.setattr("rocky.runs._pid_command", lambda pid: None)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    def interrupt(timeout):
        raise KeyboardInterrupt

    monkeypatch.setattr("rocky.cli._stdin_line", interrupt)
    assert _attach(tmp_path, meta["id"]) == 0
    out = capsys.readouterr().out
    assert "detached" in out and "rocky stop" in out
    assert refresh_meta(folder)["status"] == "running", "Ctrl+C must not kill the job"


def test_pid_alive_rejects_a_recycled_pid(monkeypatch):
    import os

    me = os.getpid()
    assert pid_alive(me) is True
    monkeypatch.setattr("rocky.runs._pid_command", lambda pid: "/usr/bin/python -m rocky.job 20260905-aaaa")
    assert pid_alive(me, mark="20260905-aaaa") is True
    assert pid_alive(me, mark="20260905-bbbb") is False, "same pid, another job: not ours"
    monkeypatch.setattr("rocky.runs._pid_command", lambda pid: None)
    assert pid_alive(me, mark="20260905-bbbb") is True, "no ps: fall back to os.kill"


def test_refresh_marks_a_recycled_pid_crashed(tmp_path, monkeypatch):
    import os

    meta = create_run(workspace=tmp_path, goal="reboot", budget_eur=1.0, max_loops=2, include_server_tools=True)
    folder = run_dir(tmp_path, meta["id"])
    meta["status"] = "running"
    meta["pid"] = os.getpid()
    write_meta(folder, meta)
    monkeypatch.setattr("rocky.runs._pid_command", lambda pid: "Safari")
    assert refresh_meta(folder)["status"] == "crashed"


def _finished(tmp_path, goal, status, eur, finished, **extra):
    meta = create_run(workspace=tmp_path, goal=goal, budget_eur=1.0, max_loops=2, include_server_tools=True)
    folder = run_dir(tmp_path, meta["id"])
    meta.update(status=status, passed=status.startswith("passed"), eur=eur, finished=finished, pid=None, **extra)
    write_meta(folder, meta)
    return meta


def test_summarize_costs_totals_today_week_and_waste(tmp_path):
    from datetime import datetime, timedelta, timezone

    from rocky.runs import summarize_costs

    now = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    rows = [
        {"id": "a", "status": "passed", "eur": 0.5, "finished": (now - timedelta(hours=1)).isoformat(), "goal": "one"},
        {"id": "b", "status": "unmet", "eur": 8.36, "finished": (now - timedelta(days=3)).isoformat(), "goal": "two"},
        {"id": "c", "status": "passed_with_notes", "eur": 1.5, "finished": (now - timedelta(days=20)).isoformat(), "goal": "three"},
        {"id": "d", "status": "running", "eur": None, "created": now.isoformat(), "goal": "four"},
    ]
    c = summarize_costs(rows, now=now)
    assert c["runs"] == 4 and c["finished"] == 3 and c["usable"] == 2
    assert c["total_eur"] == 10.36 and c["today_eur"] == 0.5 and c["week_eur"] == 8.86
    assert c["per_usable_eur"] == 5.18 and c["wasted_eur"] == 8.36
    assert [t["id"] for t in c["top"]] == ["b", "c", "a"]


def test_ps_shows_price_time_and_deliverable(tmp_path, capsys):
    from datetime import datetime, timezone

    from rocky.cli import main

    now = datetime.now(timezone.utc).isoformat()
    _finished(tmp_path, "write ads", "passed", 0.42, now, deliverable_path="deliverables/ads.md", deliverable_exists=True)
    _finished(tmp_path, "analyse market", "unmet", 8.36, "2026-09-01T10:00:00+00:00", summary="Stopped early: no progress")
    assert main(["ps", "-d", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "€0.42" in out and "€8.36" in out
    assert "→ deliverables/ads.md" in out
    assert "Stopped early" in out
    assert "09-01" in out
    assert "rocky cost" in out


def test_cost_command_prints_the_summary(tmp_path, capsys):
    from datetime import datetime, timezone

    from rocky.cli import main

    now = datetime.now(timezone.utc).isoformat()
    _finished(tmp_path, "write ads", "passed", 0.42, now)
    _finished(tmp_path, "analyse market", "unmet", 8.36, now)
    assert main(["cost", "-d", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "€8.78" in out and "2 runs" in out
    assert "per usable result" in out and "€0.42" in out
    assert "gave nothing" in out and "€8.36" in out
    assert "costliest" in out
