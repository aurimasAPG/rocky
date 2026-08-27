import json
import sys
from pathlib import Path

import pytest

from rocky.mcp import MCPError, MCPRegistry, load_config, render_result
from rocky.tools import ToolError, WorkspaceTools, dispatch

FAKE = Path(__file__).parent / "fixtures" / "fake_mcp_server.py"


def _config(workspace: Path, name: str = "fake", **extra) -> None:
    folder = workspace / ".rocky"
    folder.mkdir(exist_ok=True)
    spec = {"command": sys.executable, "args": [str(FAKE)], **extra}
    (folder / "mcp.json").write_text(json.dumps({"servers": {name: spec}}), encoding="utf-8")


def test_load_config_merges_home_and_workspace(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home).mkdir()
    monkeypatch.setenv("ROCKY_HOME", str(home))
    (home / "mcp.json").write_text(
        json.dumps({"mcpServers": {"notion": {"command": "notion-mcp"}, "old": {"command": "x"}}}),
        encoding="utf-8",
    )
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".rocky").mkdir()
    (ws / ".rocky" / "mcp.json").write_text(
        json.dumps({"servers": {"old": {"command": "y", "disabled": True}, "play wright": {"command": "npx"}}}),
        encoding="utf-8",
    )
    servers = load_config(ws)
    assert set(servers) == {"notion", "play_wright"}
    assert servers["play_wright"]["command"] == "npx"


def test_load_config_rejects_bad_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    (tmp_path / ".rocky").mkdir()
    (tmp_path / ".rocky" / "mcp.json").write_text('{"servers": {"x": {"args": []}}}', encoding="utf-8")
    with pytest.raises(MCPError):
        load_config(tmp_path)


def test_registry_lists_and_calls_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    _config(tmp_path)
    reg = MCPRegistry.load(tmp_path)
    try:
        specs = reg.tool_specs()
        names = [s["function"]["name"] for s in specs]
        assert names == ["mcp__fake__echo", "mcp__fake__fail"]
        assert specs[0]["function"]["parameters"]["required"] == ["text"]
        assert specs[0]["function"]["description"].startswith("[MCP fake]")
        assert reg.call("mcp__fake__echo", {"text": "labas"}) == "LABAS"
        assert reg.call("mcp__fake__fail", {}) == "error: boom"
        with pytest.raises(MCPError):
            reg.call("mcp__fake__missing", {})
        assert reg.summary()["tools"] == 2
    finally:
        reg.close()


def test_registry_reports_a_server_that_cannot_start(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    (tmp_path / ".rocky").mkdir()
    (tmp_path / ".rocky" / "mcp.json").write_text(
        json.dumps({"servers": {"ghost": {"command": "/definitely/not/here"}}}), encoding="utf-8"
    )
    reg = MCPRegistry.load(tmp_path)
    assert reg.tool_specs() == []
    assert "ghost" in reg.failures


def test_dispatch_routes_mcp_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    _config(tmp_path)
    reg = MCPRegistry.load(tmp_path)
    tools = WorkspaceTools(tmp_path)
    try:
        assert dispatch(tools, "mcp__fake__echo", {"text": "a"}, mcp=reg) == "A"
        with pytest.raises(ToolError):
            dispatch(tools, "mcp__fake__echo", {"text": "a"})
    finally:
        reg.close()


def test_render_result_handles_content_kinds():
    assert render_result({"content": [{"type": "text", "text": "hi"}]}) == "hi"
    assert "image" in render_result({"content": [{"type": "image", "data": "abc", "mimeType": "image/png"}]})
    assert render_result({"structuredContent": {"a": 1}, "content": []}) == '{"a": 1}'
    assert render_result({"content": []}) == "(empty result)"
