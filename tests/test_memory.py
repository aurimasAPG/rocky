import pytest

from rocky.memory import (
    MEMORY_CAP,
    freeze_memory,
    memory_path,
    recent_runs_block,
    user_path,
)
from rocky.runs import create_run, run_dir, write_meta
from rocky.tools import ToolError, WorkspaceTools, dispatch


def test_freeze_includes_user_memory_and_recent_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    mem = memory_path(tmp_path)
    mem.parent.mkdir(parents=True)
    mem.write_text("Deliverable lives in article.md\n", encoding="utf-8")
    user_path().parent.mkdir(parents=True)
    user_path().write_text("User prefers Lithuanian how-tos\n", encoding="utf-8")
    meta = create_run(
        workspace=tmp_path,
        goal="write article.md",
        budget_eur=1.0,
        max_loops=2,
        include_server_tools=False,
    )
    folder = run_dir(tmp_path, meta["id"])
    meta["status"] = "passed"
    meta["passed"] = True
    meta["summary"] = "Wrote article.md with a title."
    write_meta(folder, meta)

    snap = freeze_memory(tmp_path)
    assert "USER (preferences)" in snap
    assert "User prefers Lithuanian how-tos" in snap
    assert "MEMORY (this workspace)" in snap
    assert "Deliverable lives in article.md" in snap
    assert "RECENT RUNS" in snap
    assert "write article.md" in snap
    assert "Wrote article.md with a title." in snap
    assert str(MEMORY_CAP) in snap


def test_recent_runs_skips_in_progress(tmp_path, monkeypatch):
    import os

    live = create_run(
        workspace=tmp_path,
        goal="still going",
        budget_eur=1.0,
        max_loops=2,
        include_server_tools=False,
    )
    folder = run_dir(tmp_path, live["id"])
    live["status"] = "running"
    live["pid"] = os.getpid()
    monkeypatch.setattr("rocky.runs._pid_command", lambda pid: None)
    write_meta(folder, live)
    assert recent_runs_block(tmp_path) == ""


def test_memory_add_replace_remove_and_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    tools = WorkspaceTools(tmp_path)
    added = dispatch(tools, "memory", {"action": "add", "target": "memory", "text": "Use openpyxl for xlsx"})
    assert "Use openpyxl for xlsx" in added
    assert memory_path(tmp_path).read_text(encoding="utf-8").strip() == "Use openpyxl for xlsx"

    dispatch(
        tools,
        "memory",
        {
            "action": "replace",
            "target": "memory",
            "old_text": "openpyxl",
            "text": "Use openpyxl; never write_file xlsx",
        },
    )
    assert "never write_file xlsx" in memory_path(tmp_path).read_text(encoding="utf-8")

    dispatch(tools, "memory", {"action": "remove", "target": "memory", "old_text": "openpyxl"})
    assert memory_path(tmp_path).read_text(encoding="utf-8").strip() == ""

    with pytest.raises(ToolError, match="full"):
        dispatch(
            tools,
            "memory",
            {"action": "add", "target": "memory", "text": "x" * (MEMORY_CAP + 1)},
        )


def test_memory_user_target_writes_home_not_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    tools = WorkspaceTools(tmp_path)
    dispatch(tools, "memory", {"action": "add", "target": "user", "text": "Keep answers short"})
    assert "Keep answers short" in user_path().read_text(encoding="utf-8")
    assert not memory_path(tmp_path).exists()


def test_runs_search_finds_finished_goal(tmp_path):
    meta = create_run(
        workspace=tmp_path,
        goal="EEAT Meta Ads article",
        budget_eur=1.0,
        max_loops=2,
        include_server_tools=False,
    )
    folder = run_dir(tmp_path, meta["id"])
    meta["status"] = "passed"
    meta["passed"] = True
    meta["summary"] = "Saved to article.md"
    write_meta(folder, meta)
    tools = WorkspaceTools(tmp_path)
    hit = dispatch(tools, "runs_search", {"query": "Meta Ads"})
    assert meta["id"] in hit
    assert "article.md" in hit
    assert "no matching" in dispatch(tools, "runs_search", {"query": "pptx deck that never happened"})


def test_snapshot_frozen_until_next_load(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    tools = WorkspaceTools(tmp_path)
    dispatch(tools, "memory", {"action": "add", "target": "memory", "text": "first fact"})
    frozen = freeze_memory(tmp_path)
    dispatch(tools, "memory", {"action": "add", "target": "memory", "text": "second fact"})
    assert "first fact" in frozen
    assert "second fact" not in frozen
    assert "second fact" in freeze_memory(tmp_path)


def test_instruction_file_precedence_and_clip(tmp_path, monkeypatch):
    from rocky.memory import INSTRUCTIONS_CAP, freeze_memory, read_instructions

    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    assert read_instructions(tmp_path) == ("", "")
    (tmp_path / "CLAUDE.md").write_text("claude rules", encoding="utf-8")
    assert read_instructions(tmp_path)[0] == "CLAUDE.md"
    (tmp_path / "AGENTS.md").write_text("x" * (INSTRUCTIONS_CAP + 500), encoding="utf-8")
    name, text = read_instructions(tmp_path)
    assert name == "AGENTS.md"
    assert "clipped" in text and len(text) < INSTRUCTIONS_CAP + 100
    (tmp_path / "ROCKY.md").write_text("rocky first", encoding="utf-8")
    assert read_instructions(tmp_path) == ("ROCKY.md", "rocky first")
    assert "PROJECT INSTRUCTIONS (ROCKY.md" in freeze_memory(tmp_path)
