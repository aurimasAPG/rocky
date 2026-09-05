import pytest
from fastapi.testclient import TestClient

from rocky.cli import main
from rocky.webapp import ServeRefused, app, ensure_serve_allowed, serve


def test_home_page_serves():
    client = TestClient(app)
    res = client.get("/")
    assert res.status_code == 200
    assert "Cast a goal" in res.text
    assert "id=\"usd\"" in res.text
    assert "id=\"eur\"" in res.text
    assert "id=\"tok-total\"" in res.text


def test_css_serves():
    client = TestClient(app)
    res = client.get("/static/styles.css")
    assert res.status_code == 200
    assert "--copper" in res.text


def test_ensure_serve_allowed_refuses_non_loopback_without_token():
    with pytest.raises(ServeRefused, match="ROCKY_TOKEN"):
        ensure_serve_allowed("0.0.0.0", "")
    with pytest.raises(ServeRefused, match="ROCKY_TOKEN"):
        ensure_serve_allowed("192.168.1.10", "   ")
    with pytest.raises(ServeRefused, match="ROCKY_TOKEN"):
        ensure_serve_allowed("::", "")


def test_ensure_serve_allowed_allows_loopback_without_token():
    ensure_serve_allowed("127.0.0.1", "")
    ensure_serve_allowed("localhost", "")
    ensure_serve_allowed("::1", "")


def test_ensure_serve_allowed_allows_non_loopback_with_token():
    ensure_serve_allowed("0.0.0.0", "secret")


def test_serve_refuses_non_loopback_without_token_before_bind(monkeypatch):
    monkeypatch.setattr("rocky.webapp.serve_token", lambda: "")
    import uvicorn

    bound: list[object] = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: bound.append((a, k)))
    with pytest.raises(ServeRefused, match="ROCKY_TOKEN"):
        serve(host="0.0.0.0", port=8787)
    assert bound == []


def test_serve_allows_loopback_without_token(monkeypatch):
    monkeypatch.setattr("rocky.webapp.serve_token", lambda: "")
    import uvicorn

    bound: list[tuple[str, int]] = []

    def fake_run(app_obj, host="127.0.0.1", port=8787, **kwargs):
        bound.append((host, port))

    monkeypatch.setattr(uvicorn, "run", fake_run)
    serve(host="127.0.0.1", port=8787)
    assert bound == [("127.0.0.1", 8787)]


def test_cli_serve_refuses_public_host_without_token(monkeypatch, capsys):
    monkeypatch.setattr("rocky.webapp.serve_token", lambda: "")
    import uvicorn

    bound: list[object] = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: bound.append(True))
    assert main(["serve", "--host", "0.0.0.0"]) == 2
    captured = capsys.readouterr()
    assert "ROCKY_TOKEN" in captured.out + captured.err
    assert bound == []


def _fake_spawn_finishing(events_after_start):
    """A spawn_worker stand-in: writes events + a terminal meta as if the job had run."""
    from rocky.events import Event
    from rocky.runs import append_event, run_dir, write_meta

    def spawn(meta):
        folder = run_dir(meta["workspace"], meta["id"])
        for ev in events_after_start:
            append_event(folder, Event(type=ev["type"], payload=ev["payload"]))
        meta.update(status="passed", passed=True, pid=None, eur=0.12, deliverable_path="deliverables/out.md",
                    deliverable_exists=True, files=["deliverables/out.md"])
        write_meta(folder, meta)
        return 1

    return spawn


def _events_of(client, run_id, workspace):
    import json as _json

    seen = []
    with client.stream("GET", f"/api/runs/{run_id}/events?workspace={workspace}") as res:
        assert res.status_code == 200
        for line in res.iter_lines():
            if line.startswith("data: "):
                seen.append(_json.loads(line[6:]))
            if line.startswith("event: end"):
                break
    return seen


def test_web_run_is_a_background_job_with_its_own_event_stream(tmp_path, monkeypatch):
    monkeypatch.setattr("rocky.webapp.serve_token", lambda: "")
    monkeypatch.setattr("rocky.webapp.api_key", lambda: "sk-test")
    monkeypatch.setattr("rocky.webapp.allowed_workspaces", lambda: [tmp_path])
    monkeypatch.setattr(
        "rocky.webapp.spawn_worker",
        _fake_spawn_finishing([
            {"type": "run_started", "payload": {"goal": "write out.md"}},
            {"type": "done", "payload": {"passed": True, "status": "passed", "deliverable_path": "deliverables/out.md",
                                         "deliverable_exists": True, "files": ["deliverables/out.md"], "eur": 0.12}},
        ]),
    )
    client = TestClient(app)
    res = client.post("/api/runs", json={"goal": "write out.md", "workspace": str(tmp_path), "budget": 1, "max_loops": 2})
    assert res.status_code == 200, res.text
    run_id = res.json()["id"]
    assert res.json()["workspace"] == str(tmp_path.resolve())

    from rocky.runs import list_runs

    assert [r["id"] for r in list_runs(tmp_path)] == [run_id], "the run is a real .rocky/runs entry"
    for _ in range(2):  # two viewers, each gets the whole stream
        seen = _events_of(client, run_id, str(tmp_path))
        types = [e["type"] for e in seen]
        assert types[0] == "run_started" and "done" in types and types[-1] == "meta"
        assert seen[-1]["payload"]["deliverable_path"] == "deliverables/out.md"
    rows = client.get(f"/api/runs?workspace={tmp_path}").json()
    assert rows[0]["id"] == run_id and rows[0]["eur"] == 0.12 and rows[0]["deliverable_exists"] is True
    status = client.get(f"/api/runs/{run_id}?workspace={tmp_path}").json()
    assert status["status"] == "passed" and status["ask"] is None


def test_web_answer_and_stop_go_through_the_run_folder(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys

    from rocky.runs import create_run, read_meta, run_dir, take_answer, write_ask, write_meta

    monkeypatch.setattr("rocky.webapp.serve_token", lambda: "")
    monkeypatch.setattr("rocky.webapp.allowed_workspaces", lambda: [tmp_path])
    meta = create_run(workspace=tmp_path, goal="ask me", budget_eur=1.0, max_loops=2, include_server_tools=True)
    folder = run_dir(tmp_path, meta["id"])
    proc = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep(60)  # {meta['id']}"])
    meta.update(status="waiting", pid=proc.pid)
    write_meta(folder, meta)
    client = TestClient(app)
    ws = str(tmp_path)
    assert client.post(f"/api/runs/{meta['id']}/answer", json={"workspace": ws, "text": "yes"}).status_code == 409
    write_ask(folder, "Raise budget?", ["yes", "keep"])
    status = client.get(f"/api/runs/{meta['id']}?workspace={ws}").json()
    assert status["ask"]["question"] == "Raise budget?"
    assert client.post(f"/api/runs/{meta['id']}/answer", json={"workspace": ws, "text": "yes"}).json() == {"ok": True}
    assert take_answer(folder) == "yes"
    res = client.post(f"/api/runs/{meta['id']}/stop", json={"workspace": ws})
    proc.wait(timeout=5)
    assert res.status_code == 200 and res.json()["status"] == "stopped"
    assert read_meta(folder)["status"] == "stopped"


def test_web_workspace_defaults_to_the_server_folder(tmp_path, monkeypatch):
    monkeypatch.setattr("rocky.webapp.serve_token", lambda: "")
    monkeypatch.setattr("rocky.webapp.api_key", lambda: "sk-test")
    monkeypatch.setattr("rocky.webapp.allowed_workspaces", lambda: [])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("rocky.webapp.spawn_worker", _fake_spawn_finishing([]))
    client = TestClient(app)
    outside = tmp_path.parent / "elsewhere"
    res = client.post("/api/runs", json={"goal": "x", "workspace": str(outside)})
    assert res.status_code == 400
    res = client.post("/api/runs", json={"goal": "x", "workspace": str(tmp_path / "sub")})
    assert res.status_code == 200
    assert client.get("/api/meta").json()["workspaces"] == [str(tmp_path.resolve())]


def test_web_token_via_cookie_header_or_query(monkeypatch, tmp_path):
    monkeypatch.setattr("rocky.webapp.serve_token", lambda: "s3cret")
    monkeypatch.setattr("rocky.webapp.allowed_workspaces", lambda: [tmp_path])
    client = TestClient(app)
    assert client.get("/api/meta").status_code == 401
    assert client.get("/api/meta", headers={"Authorization": "Bearer s3cre"}).status_code == 401
    assert client.get("/api/meta", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.get("/api/meta", cookies={"rocky_token": "s3cret"}).status_code == 200
    assert client.get("/api/meta?token=s3cret").status_code == 200


def test_home_page_has_stop_ask_and_runs():
    res = TestClient(app).get("/")
    assert 'id="stop"' in res.text and 'id="ask-form"' in res.text and 'id="runs"' in res.text
