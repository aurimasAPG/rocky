import json

from rocky.context import RepeatGuard, compact, estimate_tokens, prune_tool_results


def _thread(turns: int, result_chars: int = 2000) -> list[dict]:
    messages = [
        {"role": "system", "content": "You execute this subtask only."},
        {"role": "user", "content": json.dumps({"subtask": "research"})},
    ]
    for i in range(turns):
        messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"c{i}",
                        "type": "function",
                        "function": {"name": "web_fetch", "arguments": json.dumps({"url": f"u{i}"})},
                    }
                ],
            }
        )
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"line one {i}\n" + "x" * result_chars})
    return messages


def test_estimate_tokens_scales_with_text():
    small = estimate_tokens(_thread(1, 400))
    big = estimate_tokens(_thread(1, 40_000))
    assert big > small * 10


def test_prune_keeps_recent_turns_and_stubs_old_results():
    messages = _thread(8)
    removed = prune_tool_results(messages, keep_last_turns=3)
    assert removed > 0
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    old, recent = tool_msgs[:5], tool_msgs[5:]
    assert all(m["content"].startswith("[pruned") for m in old)
    assert all(m["content"].startswith("line one") for m in recent)
    # stubs keep the first line so the model still knows what it was
    assert "line one 0" in old[0]["content"]
    # idempotent
    assert prune_tool_results(messages, keep_last_turns=3) == 0


def test_compact_is_noop_below_threshold():
    messages = _thread(2, 100)
    assert compact(messages, context_length=128_000, summarize=lambda t: "sum") is None
    assert len(messages) == 6


def test_compact_prunes_then_summarises_when_still_too_long():
    messages = _thread(10, 3000)
    calls = []

    def summarize(transcript: str) -> str:
        calls.append(transcript)
        return "wrote notes.md; sources a, b"

    payload = compact(messages, context_length=1_000, summarize=summarize, keep_last_turns=2)
    assert payload is not None
    assert payload["mode"] == "summary"
    assert payload["after_tokens"] < payload["before_tokens"]
    assert calls and "web_fetch" in calls[0]
    # system + task kept verbatim, one summary note, then the last two turns (4 msgs)
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[2]["role"] == "user" and "Context compacted" in messages[2]["content"]
    assert "notes.md" in messages[2]["content"]
    assert [m["role"] for m in messages[3:]] == ["assistant", "tool", "assistant", "tool"]


def test_compact_prune_only_when_that_is_enough():
    messages = _thread(6, 3000)
    payload = compact(messages, context_length=6_000, summarize=lambda t: "never", keep_last_turns=1)
    assert payload is not None and payload["mode"] == "prune"


def test_compact_survives_failing_summariser():
    messages = _thread(10, 3000)

    def boom(_):
        raise RuntimeError("model down")

    payload = compact(messages, context_length=1_000, summarize=boom, keep_last_turns=2)
    assert payload["mode"] == "summary"
    assert "summary unavailable" in messages[2]["content"]


def test_repeat_guard_blocks_third_and_aborts_fourth():
    guard = RepeatGuard()
    args = {"url": "https://x"}
    assert guard.check("web_fetch", args) == "ok"
    assert guard.check("web_fetch", {"url": "https://x"}) == "ok"
    assert guard.check("web_fetch", args) == "block"
    assert guard.check("web_fetch", args) == "abort"
    # different arguments are a different call
    assert guard.check("web_fetch", {"url": "https://y"}) == "ok"


def test_compact_kicks_in_at_the_absolute_ceiling_on_huge_windows():
    from rocky.context import ABSOLUTE_COMPACT_TOKENS, compact, estimate_tokens

    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "task"}]
    for i in range(12):
        msgs.append({"role": "assistant", "content": None, "tool_calls": [{"id": str(i), "type": "function", "function": {"name": "web_fetch", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": str(i), "content": "x" * 40_000})
    before = estimate_tokens(msgs)
    assert ABSOLUTE_COMPACT_TOKENS < before < 1_000_000 * 0.6
    out = compact(msgs, context_length=1_000_000, summarize=lambda t: "summary")
    assert out is not None
    assert out["limit"] == ABSOLUTE_COMPACT_TOKENS
    assert out["after_tokens"] < before
