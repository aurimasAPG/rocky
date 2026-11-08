from __future__ import annotations

from rocky.events import Event

# Events that still get a full log/panel. Everything else is status-line only on a TTY.
TRANSCRIPT_TYPES = frozenset(
    {
        "run_started",
        "resumed",
        "assessed",
        "budget_offer",
        "budget_set",
        "budget_warn",
        "cast",
        "critique",
        "plan",
        "subtask_start",
        "stall",
        "no_progress",
        "verify",
        "done",
        "error",
    }
)

_TOOL_VERB = {
    "web_search": "researching",
    "web_fetch": "reading the web",
    "browser_navigate": "browsing",
    "browser_snapshot": "browsing",
    "browser_close": "closing browser",
    "read_file": "reading",
    "write_file": "writing",
    "patch": "editing",
    "grep": "searching files",
    "list_dir": "listing",
    "bash": "running",
    "todo": "tracking",
    "memory": "remembering",
    "runs_search": "recalling",
    "recipe": "recipes",
    "skill_view": "loading skill",
    "skills_list": "listing skills",
    "office_read": "reading office",
    "vision_analyze": "looking",
    "image_generate": "drawing",
    "ad_board": "building ads",
    "clarify": "asking you",
}

_HINT_KEYS = ("path", "query", "url", "command", "name", "pattern", "text", "title")


def _hint(arguments: dict) -> str:
    for key in _HINT_KEYS:
        value = arguments.get(key)
        if value:
            text = " ".join(str(value).split())
            return text[:56]
    return ""


def status_line(event: Event) -> str | None:
    """One-line Claude Code / OpenCode style activity. None = keep the previous line."""
    kind = event.type
    payload = event.payload or {}
    if kind == "run_started":
        return "starting"
    if kind == "resumed":
        return f"resuming · {payload.get('from_run')}"
    if kind == "subtask_skipped":
        return f"skipping · {payload.get('title')} (done before)"
    if kind == "catalog":
        if payload.get("status") == "fetching":
            return "fetching models"
        return "catalog ready"
    if kind == "memory":
        return "loading memory"
    if kind == "mcp":
        return f"mcp · {payload.get('tools')} tools from {len(payload.get('servers') or [])} servers"
    if kind == "assessed":
        return f"thinking · assess · {payload.get('complexity')}/{payload.get('domain')}"
    if kind == "budget_offer":
        return "waiting for budget"
    if kind == "budget_warn" and payload.get("stage") == "half":
        return f"budget · half spent · no {payload.get('deliverable') or 'deliverable'} yet"
    if kind == "budget_set":
        return "thinking · plan"
    if kind == "cast":
        return "picking models"
    if kind == "critique":
        return "thinking · critic"
    if kind == "plan":
        return "thinking · plan"
    if kind == "loop":
        return f"thinking · loop {payload.get('n')}/{payload.get('max')}"
    if kind == "subtask_start":
        title = payload.get("title") or "subtask"
        return f"working · {title}"
    if kind == "subtask_done":
        return "thinking · worker"
    if kind == "tool_call":
        name = str(payload.get("name") or "tool")
        if name.startswith("mcp__"):
            name = "mcp · " + name[5:].replace("__", " · ")
        verb = _TOOL_VERB.get(name, name)
        hint = _hint(payload.get("arguments") or {})
        return f"{verb} · {hint}" if hint else verb
    if kind == "tool_result":
        return "thinking · worker"
    if kind == "worker":
        return "thinking · worker"
    if kind == "compact":
        mode = payload.get("mode") or "prune"
        return f"compacting · {mode} · {payload.get('before_tokens')}→{payload.get('after_tokens')} tok"
    if kind == "stall":
        return f"stalled · swapping worker → {payload.get('worker')}"
    if kind == "nudge":
        return f"nudging · missing {', '.join(payload.get('missing') or [])[:50]}"
    if kind == "subtask_incomplete":
        return f"incomplete · {payload.get('title')}"
    if kind == "no_progress":
        return "stopping · no progress"
    if kind == "loop_guard":
        return f"loop guard · {payload.get('action')} · {payload.get('name')}"
    if kind == "verify":
        return "checking result" if not payload.get("passed") else "verified"
    if kind == "error":
        msg = str(payload.get("message") or "error")
        return f"error · {msg[:72]}"
    if kind == "done":
        status = payload.get("status") or ("passed" if payload.get("passed") else "unmet")
        line = "done · " + str(status).replace("_", " ")
        if payload.get("deliverable_exists") and payload.get("deliverable_path"):
            line += f" · {payload['deliverable_path']}"
        return line
    if kind == "cost":
        return None
    return None
