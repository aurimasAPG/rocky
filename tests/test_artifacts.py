from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from rocky.artifacts import PREVIEW_BYTES
from rocky.runs import create_run, run_dir, write_meta
from rocky.webapp import _workspace_for, app


@pytest.fixture
def served(tmp_path, monkeypatch):
    monkeypatch.setattr("rocky.webapp.serve_token", lambda: "")
    monkeypatch.setattr("rocky.webapp.allowed_workspaces", lambda: [tmp_path])
    meta = create_run(
        workspace=tmp_path, goal="Write a welcome note", budget_eur=0.3,
        max_loops=1, include_server_tools=False,
    )
    meta.update(status="passed", passed=True, deliverable_path="deliverables/welcome.md",
                deliverable_exists=True, files=["deliverables/welcome.md", "deliverables/slides.pptx"])
    write_meta(run_dir(tmp_path, meta["id"]), meta)
    (tmp_path / "deliverables").mkdir()
    (tmp_path / "deliverables/welcome.md").write_text("Welcome to Rocky!\n", encoding="utf-8")
    (tmp_path / "deliverables/slides.pptx").write_bytes(b"example office bytes")
    return TestClient(app), tmp_path, meta


def _url(meta, suffix="artifacts"):
    return f"/api/runs/{meta['id']}/{suffix}"


def test_artifact_inventory_uses_declared_files_and_current_availability(served):
    client, workspace, meta = served
    meta["files"] += ["deliverables/missing.txt", "deliverables/welcome.md"]
    write_meta(run_dir(workspace, meta["id"]), meta)
    (workspace / "unrelated.txt").write_text("not a run output")
    res = client.get(_url(meta), params={"workspace": str(workspace)})
    assert res.status_code == 200
    data = res.json()
    assert data["scope"] == "current_workspace"
    assert data["files"] == [
        {"path": "deliverables/welcome.md", "name": "welcome.md", "size": 18,
         "available": True, "previewable": True, "primary": True},
        {"path": "deliverables/slides.pptx", "name": "slides.pptx", "size": 20,
         "available": True, "previewable": False, "primary": False},
        {"path": "deliverables/missing.txt", "name": "missing.txt", "size": None,
         "available": False, "previewable": False, "primary": False},
    ]


def test_text_preview_is_json_and_download_is_attachment(served):
    client, workspace, meta = served
    content = "<script>alert('text only')</script>\nSveiki!"
    (workspace / "deliverables/welcome.md").write_text(content)
    params = {"workspace": str(workspace), "path": "deliverables/welcome.md"}
    res = client.get(_url(meta, "artifact"), params=params)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/json")
    assert res.json() == {"path": params["path"], "text": content, "truncated": False}
    res = client.get(_url(meta, "artifact"), params={**params, "download": "true"})
    assert res.content == content.encode()
    assert res.headers["content-disposition"] == 'attachment; filename="welcome.md"'
    assert res.headers["content-type"] == "application/octet-stream"
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("name", ["slides.pptx", "document.docx", "sheet.xlsx", "page.html", "image.svg"])
def test_nontext_artifacts_download_without_inline_preview(served, name):
    client, workspace, meta = served
    path = f"deliverables/{name}"
    (workspace / path).write_bytes(b"untrusted file bytes")
    meta["files"].append(path)
    write_meta(run_dir(workspace, meta["id"]), meta)
    params = {"workspace": str(workspace), "path": path}
    assert client.get(_url(meta, "artifact"), params=params).status_code == 415
    res = client.get(_url(meta, "artifact"), params={**params, "download": True})
    assert res.status_code == 200 and res.content == b"untrusted file bytes"
    assert res.headers["content-disposition"].startswith("attachment;")
    assert res.headers["x-content-type-options"] == "nosniff"


def test_preview_is_capped_and_invalid_utf8_is_replaced(served):
    client, workspace, meta = served
    path = "deliverables/welcome.md"
    (workspace / path).write_bytes(b"\xff" + b"a" * PREVIEW_BYTES)
    data = client.get(_url(meta, "artifact"), params={"workspace": str(workspace), "path": path}).json()
    assert data["truncated"] is True
    assert data["text"].startswith("\ufffd")
    assert len(data["text"]) == PREVIEW_BYTES


def test_missing_files_and_unlisted_files_cannot_be_retrieved(served):
    client, workspace, meta = served
    (workspace / "deliverables/welcome.md").unlink()
    (workspace / "unrelated.md").write_text("private input")
    data = client.get(_url(meta), params={"workspace": str(workspace)}).json()
    assert data["files"][0]["available"] is False
    for path in ["deliverables/welcome.md", "unrelated.md"]:
        assert client.get(_url(meta, "artifact"), params={"workspace": str(workspace), "path": path}).status_code == 404


@pytest.mark.parametrize("path", ["../secret.txt", "/tmp/secret.txt", "a/../../secret.txt", ".env",
                                 ".rocky/config.toml", ".git/config", "deliverables/.env",
                                 "C:\\secret.txt", "deliverables\\..\\secret.txt", "note.txt:private"])
def test_unsafe_declared_paths_are_not_exposed(served, path):
    client, workspace, meta = served
    meta["deliverable_path"] = path
    meta["files"] = [path]
    write_meta(run_dir(workspace, meta["id"]), meta)
    assert client.get(_url(meta), params={"workspace": str(workspace)}).json()["files"] == []
    assert client.get(_url(meta, "artifact"), params={"workspace": str(workspace), "path": path, "download": True}).status_code == 404


def test_symlink_escapes_hidden_targets_and_directories_are_not_exposed(served, tmp_path):
    client, workspace, meta = served
    outside = workspace.parent / f"{workspace.name}-secret.txt"
    outside.write_text("outside")
    (workspace / ".env").write_text("secret")
    (workspace / "deliverables/outside.md").symlink_to(outside)
    (workspace / "deliverables/alias.md").symlink_to(workspace / ".env")
    (workspace / "deliverables/folder").mkdir()
    meta["deliverable_path"] = None
    meta["files"] = ["deliverables/outside.md", "deliverables/alias.md", "deliverables/folder"]
    write_meta(run_dir(workspace, meta["id"]), meta)
    assert client.get(_url(meta), params={"workspace": str(workspace)}).json()["files"] == []
    for path in meta["files"]:
        assert client.get(_url(meta, "artifact"), params={"workspace": str(workspace), "path": path}).status_code == 404


def test_artifact_auth_accepts_headers_and_cookie_and_rejects_missing_token(served, monkeypatch):
    client, workspace, meta = served
    monkeypatch.setattr("rocky.webapp.serve_token", lambda: "team-secret")
    params = {"workspace": str(workspace), "path": "deliverables/welcome.md", "download": True}
    assert client.get(_url(meta), params=params).status_code == 401
    assert client.get(_url(meta, "artifact"), params=params).status_code == 401
    assert client.get(_url(meta), params=params, headers={"Authorization": "Bearer team-secret"}).status_code == 200
    client.cookies.set("rocky_token", "team-secret")
    assert client.get(_url(meta, "artifact"), params=params).status_code == 200


def test_artifacts_are_explicitly_current_workspace_files(served):
    client, workspace, meta = served
    path = "deliverables/welcome.md"
    (workspace / path).write_text("Updated by a later task")
    data = client.get(_url(meta), params={"workspace": str(workspace)}).json()
    assert data["scope"] == "current_workspace"
    preview = client.get(_url(meta, "artifact"), params={"workspace": str(workspace), "path": path}).json()
    assert preview["text"] == "Updated by a later task"


@pytest.mark.parametrize("run_id", ["..", "../other", "/tmp/elsewhere", "..\\other", ".secret"])
def test_run_identifier_cannot_escape_run_storage(served, run_id):
    _, workspace, _ = served
    with pytest.raises(HTTPException) as exc:
        _workspace_for(run_id, str(workspace))
    assert exc.value.status_code == 404


def test_run_metadata_symlink_cannot_escape_run_storage(served):
    client, workspace, meta = served
    folder = run_dir(workspace, meta["id"])
    outside = workspace / "not-a-run.json"
    (folder / "meta.json").rename(outside)
    (folder / "meta.json").symlink_to(outside)
    assert client.get(_url(meta), params={"workspace": str(workspace)}).status_code == 404


def test_unallowed_workspace_cannot_serve_artifacts(served, monkeypatch):
    client, workspace, meta = served
    monkeypatch.setattr("rocky.webapp.allowed_workspaces", lambda: [workspace / "other"])
    assert client.get(_url(meta), params={"workspace": str(workspace)}).status_code == 400
