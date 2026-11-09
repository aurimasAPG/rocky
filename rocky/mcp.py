"""Minimal MCP client (stdio transport) so workers can use external tool servers.

Config lives in ``<workspace>/.rocky/mcp.json`` (wins) and ``~/.rocky/mcp.json``::

    {
      "servers": {
        "playwright": {"command": "npx", "args": ["@playwright/mcp@latest"]},
        "notion": {"command": "notion-mcp", "env": {"NOTION_TOKEN": "..."}}
      }
    }

``mcpServers`` (the Claude Code / Cursor key) is accepted as an alias. Each
server's tools are exposed to the worker as ``mcp__<server>__<tool>`` with
the server's own JSON schema. Servers start lazily on first use.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Any

from rocky.config import rocky_home

from rocky import __version__

PROTOCOL_VERSION = "2025-06-18"
START_TIMEOUT = 30.0
CALL_TIMEOUT = 90.0
PREFIX = "mcp__"
_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


class MCPError(RuntimeError):
    pass


def _safe(name: str) -> str:
    return _SAFE.sub("_", name).strip("_") or "server"


def config_paths(workspace: str | Path) -> list[Path]:
    return [rocky_home() / "mcp.json", Path(workspace).expanduser().resolve() / ".rocky" / "mcp.json"]


def load_config(workspace: str | Path) -> dict[str, dict]:
    """Merged server map; the workspace file overrides the home file per server name."""
    servers: dict[str, dict] = {}
    for path in config_paths(workspace):
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise MCPError(f"{path}: invalid JSON ({exc})") from exc
        block = raw.get("servers") or raw.get("mcpServers") or {}
        if not isinstance(block, dict):
            raise MCPError(f"{path}: 'servers' must be an object")
        for name, spec in block.items():
            if not isinstance(spec, dict) or not spec.get("command"):
                raise MCPError(f"{path}: server {name!r} needs a 'command'")
            if spec.get("disabled"):
                servers.pop(name, None)
                continue
            servers[_safe(str(name))] = dict(spec)
    return servers


class MCPServer:
    """One stdio server: JSON-RPC 2.0, newline-delimited."""

    def __init__(self, name: str, spec: dict, cwd: Path | None = None):
        self.name = name
        self.spec = spec
        self.cwd = cwd
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._next_id = 0
        self._pending: dict[int, dict] = {}
        self._events: dict[int, threading.Event] = {}
        self._reader: threading.Thread | None = None
        self.tools: list[dict] = []
        self.stderr_tail: list[str] = []

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._proc is not None:
            return
        env = os.environ.copy()
        for key, value in (self.spec.get("env") or {}).items():
            env[str(key)] = os.path.expandvars(str(value))
        cmd = [str(self.spec["command"]), *[str(a) for a in self.spec.get("args") or []]]
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self.spec.get("cwd") or self.cwd or Path.cwd()),
                env=env,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise MCPError(f"{self.name}: cannot start {cmd[0]!r}: {exc}") from exc
        self._reader = threading.Thread(target=self._read_loop, daemon=True, name=f"mcp-{self.name}")
        self._reader.start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        result = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "rocky", "version": __version__},
            },
            timeout=START_TIMEOUT,
        )
        if not isinstance(result, dict):
            raise MCPError(f"{self.name}: bad initialize response")
        self.notify("notifications/initialized", {})
        listed = self.request("tools/list", {}, timeout=START_TIMEOUT)
        self.tools = [t for t in (listed or {}).get("tools") or [] if isinstance(t, dict) and t.get("name")]

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    # -- transport ---------------------------------------------------------
    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            ident = msg.get("id")
            if ident is None:
                continue  # notification from the server; ignored
            with self._lock:
                self._pending[int(ident)] = msg
                event = self._events.get(int(ident))
            if event:
                event.set()
        # process ended: wake every waiter
        with self._lock:
            for event in self._events.values():
                event.set()

    def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            self.stderr_tail.append(line.rstrip()[:300])
            del self.stderr_tail[:-20]

    def _send(self, payload: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            raise MCPError(f"{self.name}: server is not running")
        try:
            proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise MCPError(f"{self.name}: server closed the pipe") from exc

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: dict, timeout: float = CALL_TIMEOUT) -> Any:
        with self._lock:
            self._next_id += 1
            ident = self._next_id
            event = threading.Event()
            self._events[ident] = event
        self._send({"jsonrpc": "2.0", "id": ident, "method": method, "params": params})
        if not event.wait(timeout):
            with self._lock:
                self._events.pop(ident, None)
            raise MCPError(f"{self.name}: {method} timed out after {timeout:.0f}s")
        with self._lock:
            msg = self._pending.pop(ident, None)
            self._events.pop(ident, None)
        if msg is None:
            tail = " | ".join(self.stderr_tail[-3:])
            raise MCPError(f"{self.name}: server exited during {method}" + (f" ({tail})" if tail else ""))
        if "error" in msg:
            err = msg["error"] or {}
            raise MCPError(f"{self.name}: {err.get('message') or err}")
        return msg.get("result")

    # -- tools -------------------------------------------------------------
    def call(self, tool: str, arguments: dict) -> str:
        result = self.request("tools/call", {"name": tool, "arguments": arguments or {}})
        return render_result(result)


def render_result(result: Any) -> str:
    if not isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False)
    parts: list[str] = []
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "text":
            parts.append(str(item.get("text") or ""))
        elif kind == "image":
            parts.append(f"[image {item.get('mimeType') or ''} {len(str(item.get('data') or ''))} b64 chars]")
        elif kind == "resource":
            res = item.get("resource") or {}
            parts.append(str(res.get("text") or f"[resource {res.get('uri')}]"))
        else:
            parts.append(json.dumps(item, ensure_ascii=False))
    if result.get("structuredContent") and not parts:
        parts.append(json.dumps(result["structuredContent"], ensure_ascii=False))
    text = "\n".join(p for p in parts if p).strip()
    if result.get("isError"):
        return "error: " + (text or "tool reported an error")
    return text or "(empty result)"


class MCPRegistry:
    """All configured servers, started lazily, exposed as OpenAI-style tool specs."""

    def __init__(self, servers: dict[str, dict], cwd: Path | None = None):
        self._servers = {name: MCPServer(name, spec, cwd) for name, spec in servers.items()}
        self._started: set[str] = set()
        self.failures: dict[str, str] = {}

    @classmethod
    def load(cls, workspace: str | Path) -> "MCPRegistry":
        ws = Path(workspace).expanduser().resolve()
        return cls(load_config(ws), cwd=ws)

    @property
    def names(self) -> list[str]:
        return sorted(self._servers)

    def _ensure(self, name: str) -> MCPServer:
        server = self._servers.get(name)
        if server is None:
            raise MCPError(f"unknown MCP server: {name}")
        if name not in self._started:
            try:
                server.start()
            except MCPError as exc:
                self.failures[name] = str(exc)
                raise
            self._started.add(name)
        return server

    def tool_specs(self) -> list[dict]:
        specs: list[dict] = []
        for name in self.names:
            try:
                server = self._ensure(name)
            except MCPError:
                continue
            for tool in server.tools:
                schema = tool.get("inputSchema") or {"type": "object", "properties": {}}
                if not isinstance(schema, dict):
                    schema = {"type": "object", "properties": {}}
                specs.append(
                    {
                        "type": "function",
                        "function": {
                            "name": f"{PREFIX}{name}__{_safe(str(tool['name']))}",
                            "description": (
                                f"[MCP {name}] " + str(tool.get("description") or tool["name"])
                            )[:1024],
                            "parameters": schema,
                        },
                    }
                )
        return specs

    def owns(self, tool_name: str) -> bool:
        return tool_name.startswith(PREFIX)

    def call(self, tool_name: str, arguments: dict) -> str:
        if not self.owns(tool_name):
            raise MCPError(f"not an MCP tool: {tool_name}")
        rest = tool_name[len(PREFIX):]
        server_name, _, tool = rest.partition("__")
        if not server_name or not tool:
            raise MCPError(f"malformed MCP tool name: {tool_name}")
        server = self._ensure(server_name)
        real = next((t["name"] for t in server.tools if _safe(str(t["name"])) == tool), tool)
        return server.call(real, arguments)

    def summary(self) -> dict:
        return {
            "servers": self.names,
            "started": sorted(self._started),
            "tools": sum(len(self._servers[n].tools) for n in self._started),
            "failures": dict(self.failures),
        }

    def close(self) -> None:
        for name in list(self._started):
            self._servers[name].close()
        self._started.clear()
