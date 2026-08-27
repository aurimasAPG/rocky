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
