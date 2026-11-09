import json

import pytest
from fastapi.testclient import TestClient

from rocky.events import Event
from rocky.runs import append_event, create_run, run_dir, write_ask, write_meta
from rocky.webapp import app


@pytest.fixture
def stream_run(tmp_path, monkeypatch):
    monkeypatch.setattr("rocky.webapp.serve_token", lambda: "")
    monkeypatch.setattr("rocky.webapp.allowed_workspaces", lambda: [tmp_path])
    meta = create_run(workspace=tmp_path, goal="Write a note", budget_eur=0.3,
                      max_loops=1, include_server_tools=False)
    folder = run_dir(tmp_path, meta["id"])
    meta.update(status="passed", passed=True)
    write_meta(folder, meta)
    return TestClient(app), tmp_path, meta, folder


def frames(client, workspace, meta, headers=None):
    response = client.get(f"/api/runs/{meta['id']}/events",
                          params={"workspace": str(workspace)}, headers=headers)
    assert response.status_code == 200
    result = []
    for frame in response.text.split("\n\n"):
        lines = frame.splitlines()
        data = next((line[6:] for line in lines if line.startswith("data: ")), None)
        if data is None:
            continue
        event = json.loads(data)
        if "type" not in event:
            continue
        event_id = next((line[3:].strip() for line in lines if line.startswith("id:")), None)
        result.append((event_id, event))
    return result


def test_persisted_event_ids_are_stable_on_reconnect_without_skipping(stream_run):
    client, workspace, meta, folder = stream_run
    # Identical payloads and timestamps are still two distinct saved events.
    event = Event("phase", {"stage": "planning"}, ts="2026-11-09T10:00:00+00:00")
    append_event(folder, event)
    append_event(folder, event)
    first = frames(client, workspace, meta)
    replay = frames(client, workspace, meta, headers={"Last-Event-ID": "2"})
    assert first == replay
    assert [ident for ident, event in first if event["type"] == "phase"] == ["1", "2"]


def test_synthetic_question_and_metadata_clear_the_inherited_event_id(stream_run):
    client, workspace, meta, folder = stream_run
    append_event(folder, Event("run_started", {"goal": meta["goal"]}))
    write_ask(folder, "Choose a tone", ["Warm", "Formal"])
    result = frames(client, workspace, meta)
    assert [(ident, event["type"]) for ident, event in result] == [
        ("1", "run_started"), ("", "ask"), ("", "meta"),
    ]


def test_final_poll_events_continue_the_same_sequence(stream_run, monkeypatch):
    client, workspace, meta, folder = stream_run
    append_event(folder, Event("phase", {"stage": "verifying"}))
    appended = False

    def finish_between_polls(_folder):
        nonlocal appended
        if not appended:
            append_event(folder, Event("done", {"passed": True, "status": "passed"}))
            appended = True
        return meta

    monkeypatch.setattr("rocky.webapp.refresh_meta", finish_between_polls)
    first = frames(client, workspace, meta)
    replay = frames(client, workspace, meta)
    assert [(ident, event["type"]) for ident, event in first] == [
        ("1", "phase"), ("2", "done"), ("", "meta"),
    ]
    assert first == replay
