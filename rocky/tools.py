from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from rocky.config import extra_bash_deny, sandbox_image, sandbox_mode
from rocky.jsonutil import parse_json_object
from rocky.sandbox import check_command, sandbox_available, wrap_command

MAX_OUTPUT = 50_000


class ToolError(RuntimeError):
    pass


class WorkspaceTools:
    def __init__(self, workspace: str | Path):
        self.root = Path(workspace).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, path: str) -> Path:
        raw = Path(path).expanduser()
        target = raw.resolve() if raw.is_absolute() else (self.root / raw).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise ToolError(f"path escapes workspace: {path}") from exc
        return target

    def write_file(self, path: str, content: str) -> str:
        target = self.resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"wrote {target.relative_to(self.root)} ({len(content)} chars)"

    def read_file(self, path: str) -> str:
        target = self.resolve(path)
        if not target.exists():
            raise ToolError(f"not found: {path}")
        if target.is_dir():
            raise ToolError(f"is a directory: {path}")
        text = target.read_text(encoding="utf-8", errors="replace")
        return _clip(text)

    def list_dir(self, path: str = ".") -> str:
        target = self.resolve(path)
        if not target.exists():
            raise ToolError(f"not found: {path}")
        if not target.is_dir():
            raise ToolError(f"not a directory: {path}")
        names = []
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            names.append(child.name + ("/" if child.is_dir() else ""))
        return "\n".join(names) if names else "(empty)"

    def grep(self, pattern: str, path: str = ".") -> str:
        target = self.resolve(path)
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise ToolError(f"invalid pattern: {exc}") from exc
        hits: list[str] = []
        files = [target] if target.is_file() else target.rglob("*")
        for file in files:
            if not file.is_file():
                continue
            if any(part.startswith(".") for part in file.relative_to(self.root).parts):
                continue
            try:
                text = file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = file.relative_to(self.root)
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{rel}:{i}:{line[:240]}")
                    if len(hits) >= 80:
                        return _clip("\n".join(hits))
        return "\n".join(hits) if hits else "no matches"

    def patch(self, path: str, old_string: str, new_string: str) -> str:
        if not old_string:
            raise ToolError("old_string is empty")
        current = self.read_file(path)
        count = current.count(old_string)
        if count == 0:
            raise ToolError("old_string not found in file")
        if count > 1:
            raise ToolError(f"old_string found {count} times — make it unique")
        updated = current.replace(old_string, new_string, 1)
        self.write_file(path, updated)
        return f"patched {path} ({len(old_string)} → {len(new_string)} chars)"

    def todo_list(self) -> str:
        items = self._todo_load()
        if not items:
            return "(todo empty)"
        lines = []
        for item in items:
            lines.append(f"- [{item.get('status')}] {item.get('id')}: {item.get('content')}")
        return "\n".join(lines)

    def todo_write(self, items: list) -> str:
        cleaned = []
        for raw in items or []:
            if not isinstance(raw, dict):
                continue
            ident = str(raw.get("id") or len(cleaned) + 1)
            content = str(raw.get("content") or "").strip()
            status = str(raw.get("status") or "pending").strip() or "pending"
            if not content:
                continue
            cleaned.append({"id": ident, "content": content, "status": status})
        path = self.root / ".rocky" / "todo.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.todo_list()

    def _todo_load(self) -> list:
        path = self.root / ".rocky" / "todo.json"
        if not path.exists():
            path = self.root / ".orche" / "todo.json"
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

    def bash(self, command: str, timeout: int = 60, sandbox: str | None = None) -> str:
        if not (command or "").strip():
            raise ToolError("bash needs a command")
        reason = check_command(command, extra_bash_deny())
        if reason:
            raise ToolError(f"refused ({reason}): {command[:200]}")
        mode = sandbox_mode() if sandbox is None else sandbox
        blocker = sandbox_available(mode)
        if blocker:
            raise ToolError(f"ROCKY_SANDBOX={mode} but {blocker}")
        argv = wrap_command(command, self.root, mode, sandbox_image())
        try:
            proc = subprocess.run(
                argv,
                shell=isinstance(argv, str),
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolError(f"timed out after {timeout}s: {command}") from exc
        out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
        if proc.returncode != 0:
            out = f"exit {proc.returncode}\n{out}"
        return _clip(out.strip() or "(no output)")


def _clip(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... truncated ({len(text)} chars)"


CLIENT_TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command in the workspace directory. Use for tests, git, builds, listing, scripts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"}
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a UTF-8 text file. Do NOT use for .xlsx .docx .pptx .pdf — use bash + the matching skill instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 file relative to the workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files and folders in a workspace path.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search file contents in the workspace with a regex.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the public web. Returns titles, URLs, and snippets.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch a URL and return extracted text.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "patch",
            "description": "Replace one unique snippet in a workspace file. Prefer this over rewriting the whole file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo",
            "description": "Update or list the session todo list. Pass items to replace the list; omit items to only read it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "content": {"type": "string"},
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                },
                            },
                            "required": ["content", "status"],
                        },
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clarify",
            "description": "Ask the human a blocking question when the brief is missing a decision you cannot safely assume.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vision_analyze",
            "description": "Analyze an image from a workspace path or http(s) URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "url": {"type": "string"},
                    "prompt": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_navigate",
            "description": "Open a URL in a real Chromium browser (JS-rendered pages) and return visible text.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_snapshot",
            "description": "Return the current browser page title, URL, and visible text.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_close",
            "description": "Close the Chromium browser session.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skills_list",
            "description": "List available skills (deep-research, marketing-pipeline, voc-to-copy, copywriting, xlsx, docx, pptx, pdf, …). Call before research, copy, or Office files.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_view",
            "description": "Read a skill's full instructions by name (deep-research, voc-to-copy, copywriting, pptx, xlsx, …).",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "office_read",
            "description": "Extract text from .xlsx .docx .pptx .pdf .csv using markitdown.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory",
            "description": (
                "Add, replace, or remove a durable memory entry. "
                "target=user is global preferences (~/.rocky/memories/USER.md, 1375 chars). "
                "target=memory is facts about THIS workspace (.rocky/MEMORY.md, 2200 chars). "
                "Writes hit disk now but the prompt snapshot refreshes next run. "
                "Store compact facts, not transcripts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "replace", "remove"]},
                    "target": {"type": "string", "enum": ["memory", "user"]},
                    "text": {"type": "string"},
                    "old_text": {"type": "string"},
                },
                "required": ["action", "target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recipe",
            "description": (
                "List, view, or save plan recipes (ordered step skeletons Rocky enforces for a kind of goal, "
                "e.g. marketing: research → VOC → copy → pptx+xlsx). "
                "save writes <workspace>/.rocky/recipes/<name>.toml for the NEXT run; the user should review it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "view", "save"]},
                    "name": {"type": "string", "description": "for view"},
                    "recipe": {
                        "type": "object",
                        "description": "for save: {name, description, deliverable_path, notes, match:{domains,hints}, steps:[{id,title,detail,depends_on,requires}]}",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "runs_search",
            "description": (
                "Search prior Rocky runs in this workspace (goal, status, summary). "
                "Use when the user refers to earlier work ('that article', 'the last deck')."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        },
    },
]

SERVER_TOOLS = [
    {"type": "openrouter:web_search", "parameters": {"max_results": 5, "max_uses": 4}},
    {"type": "openrouter:web_fetch"},
    {"type": "openrouter:datetime"},
]

READ_TOOL_SPECS = [
    spec
    for spec in CLIENT_TOOL_SPECS
    if spec["function"]["name"] in {"read_file", "list_dir", "grep"}
]

# What the verifier may touch: read-only, plus Office/PDF extraction so it can open a deck.
VERIFY_TOOL_NAMES = frozenset({"read_file", "list_dir", "grep", "office_read"})
VERIFY_TOOL_SPECS = [
    spec for spec in CLIENT_TOOL_SPECS if spec["function"]["name"] in VERIFY_TOOL_NAMES
]


def worker_tools(include_server: bool = True) -> list[dict]:
    tools = list(CLIENT_TOOL_SPECS)
    if include_server:
        tools.extend(SERVER_TOOLS)
    return tools


def dispatch(
    tools: WorkspaceTools,
    name: str,
    arguments: str | dict,
    searcher=None,
    ask_user=None,
    llm=None,
    browser=None,
    mcp=None,
) -> str:
    if isinstance(arguments, str):
        args = parse_json_object(arguments) if arguments.strip() else {}
    else:
        args = arguments or {}
    try:
        if name.startswith("mcp__"):
            if mcp is None:
                raise ToolError("no MCP servers configured (.rocky/mcp.json)")
            from rocky.mcp import MCPError

            try:
                return _clip(mcp.call(name, args))
            except MCPError as exc:
                raise ToolError(str(exc)) from exc
        if name == "bash":
            return tools.bash(args.get("command") or "")
        if name == "write_file":
            return tools.write_file(args.get("path") or "", args.get("content") or "")
        if name == "read_file":
            return tools.read_file(args.get("path") or "")
        if name == "list_dir":
            return tools.list_dir(args.get("path") or ".")
        if name == "grep":
            return tools.grep(args.get("pattern") or "", args.get("path") or ".")
        if name == "web_search":
            if searcher is None:
                raise ToolError("web_search is not configured")
            return searcher.search(args.get("query") or "")
        if name == "web_fetch":
            if searcher is None:
                raise ToolError("web_fetch is not configured")
            return searcher.fetch(args.get("url") or "")
        if name == "patch":
            return tools.patch(
                args.get("path") or "",
                args.get("old_string") or "",
                args.get("new_string") or "",
            )
        if name == "todo":
            items = args.get("items")
            if items:
                return tools.todo_write(items)
            return tools.todo_list()
        if name == "clarify":
            question = str(args.get("question") or "").strip()
            if not question:
                raise ToolError("clarify needs a question")
            if ask_user is None:
                return "no user available; continue with documented assumptions"
            answer = ask_user(question, args.get("options"))
            return f"user: {answer}"
        if name == "vision_analyze":
            if llm is None:
                raise ToolError("vision_analyze needs an LLM client")
            from rocky.extras import analyze_image

            return analyze_image(
                llm,
                str(args.get("path") or args.get("url") or ""),
                str(args.get("prompt") or ""),
                tools,
            )
        if name == "browser_navigate":
            if browser is None:
                raise ToolError("browser is not configured")
            return browser.navigate(str(args.get("url") or ""))
        if name == "browser_snapshot":
            if browser is None:
                raise ToolError("browser is not configured")
            return browser.snapshot()
        if name == "browser_close":
            if browser is None:
                raise ToolError("browser is not configured")
            return browser.close()
        if name == "skills_list":
            from rocky.skills import list_skills

            rows = list_skills()
            if not rows:
                return "(no skills)"
            return "\n".join(f"- {s['name']}: {s['description']}" for s in rows)
        if name == "skill_view":
            from rocky.skills import view_skill

            try:
                return view_skill(str(args.get("name") or ""))
            except (FileNotFoundError, ValueError) as exc:
                raise ToolError(str(exc)) from exc
        if name == "office_read":
            from rocky.skills import read_office

            path = tools.resolve(str(args.get("path") or ""))
            if not path.exists():
                raise ToolError(f"not found: {args.get('path')}")
            return read_office(path)[:50000] or "(empty)"
        if name == "memory":
            from rocky.memory import apply_memory

            return apply_memory(tools.root, args)
        if name == "recipe":
            from rocky.recipes import recipe_tool

            return recipe_tool(tools.root, args)
        if name == "runs_search":
            from rocky.memory import search_runs

            return search_runs(tools.root, str(args.get("query") or ""))
        raise ToolError(f"unknown tool: {name}")
    except ToolError:
        raise
    except Exception as extra:  # noqa: BLE001
        raise ToolError(str(extra)) from extra
