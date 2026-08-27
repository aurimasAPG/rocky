from __future__ import annotations

from pathlib import Path

from rocky.config import rocky_home
from rocky.runs import TERMINAL, list_runs
from rocky.tools import ToolError

SEP = "\n§\n"
USER_CAP = 1375
MEMORY_CAP = 2200
RECENT_LIMIT = 5


def user_path() -> Path:
    return rocky_home() / "memories" / "USER.md"


def memory_path(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / ".rocky" / "MEMORY.md"


def _read(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def split_entries(text: str) -> list[str]:
    return [part.strip() for part in (text or "").split("§") if part.strip()]


def join_entries(entries: list[str]) -> str:
    return SEP.join(item.strip() for item in entries if item.strip())


def _cap_for(target: str) -> int:
    return USER_CAP if target == "user" else MEMORY_CAP


def _path_for(target: str, workspace: str | Path) -> Path:
    if target == "user":
        return user_path()
    if target == "memory":
        return memory_path(workspace)
    raise ToolError("target must be 'memory' or 'user'")


def _gauge(label: str, text: str, cap: int) -> str:
    n = len(text)
    pct = min(100, round(100 * n / cap)) if cap else 0
    return f"{label} [{pct}% — {n}/{cap} chars]"


def _block(title: str, text: str, cap: int) -> str:
    body = text.strip() or "(empty — add facts with the memory tool)"
    line = "═" * 46
    return f"{line}\n{_gauge(title, text, cap)}\n{line}\n{body}"


def recent_runs_block(workspace: str | Path, limit: int = RECENT_LIMIT) -> str:
    rows = []
    for row in list_runs(workspace, limit=40):
        status = str(row.get("status") or "")
        if status not in TERMINAL:
            continue
        goal = " ".join(str(row.get("goal") or "").split())
        summary = " ".join(str(row.get("summary") or "").split())
        if len(goal) > 120:
            goal = goal[:117] + "..."
        if len(summary) > 160:
            summary = summary[:157] + "..."
        mark = "passed" if row.get("passed") else status
        line = f"- {row.get('id')}: {mark} · {goal}"
        if summary:
            line += f" — {summary}"
        rows.append(line)
        if len(rows) >= limit:
            break
    if not rows:
        return ""
    line = "═" * 46
    return f"{line}\nRECENT RUNS (this workspace)\n{line}\n" + "\n".join(rows)


def freeze_memory(workspace: str | Path) -> str:
    """Frozen snapshot for this run. Disk writes after this are visible next run."""
    user = _read(user_path())
    mem = _read(memory_path(workspace))
    parts = [
        _block("USER (preferences)", user, USER_CAP),
        _block("MEMORY (this workspace)", mem, MEMORY_CAP),
    ]
    recent = recent_runs_block(workspace)
    if recent:
        parts.append(recent)
    return "\n\n".join(parts)


def search_runs(workspace: str | Path, query: str, limit: int = 8) -> str:
    needle = (query or "").strip().lower()
    hits: list[str] = []
    for row in list_runs(workspace, limit=40):
        blob = " ".join(
            [
                str(row.get("id") or ""),
                str(row.get("goal") or ""),
                str(row.get("summary") or ""),
                str(row.get("status") or ""),
            ]
        ).lower()
        if needle and needle not in blob:
            continue
        summary = " ".join(str(row.get("summary") or "").split())
        hits.append(
            f"{row.get('id')}  {row.get('status')}  {row.get('goal')}"
            + (f"\n  {summary}" if summary else "")
        )
        if len(hits) >= limit:
            break
    return "\n".join(hits) if hits else "no matching runs"


def apply_memory(workspace: str | Path, args: dict) -> str:
    action = str(args.get("action") or "").strip().lower()
    target = str(args.get("target") or "memory").strip().lower()
    text = str(args.get("text") or "").strip()
    old_text = str(args.get("old_text") or "").strip()
    path = _path_for(target, workspace)
    cap = _cap_for(target)
    entries = split_entries(_read(path))

    if action == "add":
        if not text:
            raise ToolError("memory add needs text")
        if any(item == text for item in entries):
            raise ToolError("duplicate memory entry")
        trial = join_entries([*entries, text])
        if len(trial) > cap:
            raise ToolError(
                f"{target} memory is full ({len(join_entries(entries))}/{cap} chars). "
                "replace or remove an entry before adding."
            )
        entries.append(text)
    elif action == "replace":
        if not old_text or not text:
            raise ToolError("memory replace needs old_text and text")
        matched = [i for i, item in enumerate(entries) if old_text in item]
        if len(matched) == 0:
            raise ToolError("old_text matched no entry")
        if len(matched) > 1:
            raise ToolError("old_text matched more than one entry; be more specific")
        entries[matched[0]] = text
        trial = join_entries(entries)
        if len(trial) > cap:
            raise ToolError(f"{target} memory would exceed {cap} chars")
    elif action == "remove":
        if not old_text:
            raise ToolError("memory remove needs old_text")
        matched = [i for i, item in enumerate(entries) if old_text in item]
        if len(matched) == 0:
            raise ToolError("old_text matched no entry")
        if len(matched) > 1:
            raise ToolError("old_text matched more than one entry; be more specific")
        entries.pop(matched[0])
    else:
        raise ToolError("action must be add, replace, or remove")

    body = join_entries(entries)
    if body:
        _atomic_write(path, body + "\n")
    elif path.exists():
        path.write_text("", encoding="utf-8")
    return _block(target.upper(), body, cap)


def snapshot_event_payload(workspace: str | Path) -> dict:
    user = _read(user_path())
    mem = _read(memory_path(workspace))
    recent = [r for r in list_runs(workspace, limit=40) if str(r.get("status") or "") in TERMINAL]
    return {
        "user_chars": len(user),
        "memory_chars": len(mem),
        "recent_runs": min(len(recent), RECENT_LIMIT),
    }
