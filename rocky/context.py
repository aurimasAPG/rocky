"""Worker-thread context control: prune, summarise, and stop tool-call loops.

Each Rocky subtask is one chat. Without this module the whole thread is
re-sent on every turn, so a long research step can push millions of prompt
tokens through a cheap worker and still produce nothing. Three guards:

1. ``prune_tool_results`` — old tool outputs become one-line stubs.
2. ``compact`` — past a share of the model's window, the middle of the
   thread is summarised by a cheap model and replaced with one note.
3. ``RepeatGuard`` — the same tool call with the same arguments is blocked
   on the third try and aborts the subtask on the fourth.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

CHARS_PER_TOKEN = 4
COMPACT_THRESHOLD = 0.6
KEEP_LAST_TURNS = 4
STUB_AFTER_CHARS = 400
BLOCK_AT = 3
ABORT_AT = 4
DEFAULT_CONTEXT = 128_000

Summarize = Callable[[str], str]


def _text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(content)


def estimate_tokens(messages: list[dict]) -> int:
    total = 0
    for msg in messages:
        total += len(_text(msg.get("content")))
        for call in msg.get("tool_calls") or []:
            fn = call.get("function") or {}
            total += len(_text(fn.get("name"))) + len(_text(fn.get("arguments")))
    return total // CHARS_PER_TOKEN + len(messages)


def _turn_starts(messages: list[dict]) -> list[int]:
    """Indexes of assistant messages — each one begins a turn."""
    return [i for i, m in enumerate(messages) if m.get("role") == "assistant"]


def prune_tool_results(
    messages: list[dict],
    keep_last_turns: int = KEEP_LAST_TURNS,
    stub_after: int = STUB_AFTER_CHARS,
) -> int:
    """Replace old, long tool results with stubs in place. Returns chars removed."""
    starts = _turn_starts(messages)
    if len(starts) <= keep_last_turns:
        return 0
    cutoff = starts[-keep_last_turns]
    removed = 0
    for i in range(cutoff):
        msg = messages[i]
        if msg.get("role") != "tool":
            continue
        body = _text(msg.get("content"))
        if len(body) <= stub_after or body.startswith("[pruned"):
            continue
        head = body.splitlines()[0][:120] if body else ""
        stub = f"[pruned {len(body)} chars — earlier tool result] {head}"
        messages[i]["content"] = stub
        removed += len(body) - len(stub)
    return removed


def _boundary_for_tail(messages: list[dict], keep_last_turns: int) -> int | None:
    starts = _turn_starts(messages)
    if len(starts) <= keep_last_turns:
        return None
    return starts[-keep_last_turns]


def compact(
    messages: list[dict],
    *,
    context_length: int,
    summarize: Summarize | None,
    threshold: float = COMPACT_THRESHOLD,
    keep_last_turns: int = KEEP_LAST_TURNS,
    head: int = 2,
) -> dict | None:
    """Prune, then summarise the middle if the thread is still too long.

    Mutates ``messages``. Returns an event payload or None when nothing changed.
    ``head`` messages (system + task) are always kept verbatim.
    """
    limit = max(int(context_length or DEFAULT_CONTEXT) * threshold, 1)
    before = estimate_tokens(messages)
    if before < limit:
        return None
    pruned = prune_tool_results(messages, keep_last_turns=keep_last_turns)
    after = estimate_tokens(messages)
    mode = "prune"
    if after >= limit and summarize is not None:
        boundary = _boundary_for_tail(messages, keep_last_turns)
        if boundary is not None and boundary > head:
            middle = messages[head:boundary]
            transcript = "\n".join(
                f"{m.get('role')}: {_text(m.get('content'))[:1500]}"
                + (
                    " | calls: "
                    + ", ".join(
                        _text((c.get("function") or {}).get("name")) for c in m.get("tool_calls") or []
                    )
                    if m.get("tool_calls")
                    else ""
                )
                for m in middle
            )
            try:
                summary = summarize(transcript).strip()
            except Exception as exc:  # noqa: BLE001
                summary = f"(summary unavailable: {exc})"
            note = {
                "role": "user",
                "content": (
                    "[Context compacted. Summary of earlier turns in this subtask — "
                    "files already written stay on disk, do not redo them:]\n" + summary
                ),
            }
            messages[head:boundary] = [note]
            after = estimate_tokens(messages)
            mode = "summary"
    if mode == "prune" and pruned == 0:
        return None
    return {"mode": mode, "before_tokens": before, "after_tokens": after, "limit": int(limit)}


def signature(name: str, arguments) -> str:
    try:
        args = json.dumps(arguments or {}, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        args = str(arguments)
    return f"{name}:{args}"


@dataclass
class RepeatGuard:
    block_at: int = BLOCK_AT
    abort_at: int = ABORT_AT
    counts: dict[str, int] = field(default_factory=dict)

    def check(self, name: str, arguments) -> str:
        """'ok' → run it; 'block' → refuse with an error; 'abort' → end the subtask."""
        key = signature(name, arguments)
        n = self.counts.get(key, 0) + 1
        self.counts[key] = n
        if n >= self.abort_at:
            return "abort"
        if n >= self.block_at:
            return "block"
        return "ok"
