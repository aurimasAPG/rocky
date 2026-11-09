import io

from rich.console import Console

from rocky.events import Event
from rocky import view
from rocky.view import handover_lines, render_event


def _render(event: Event) -> str:
    buf = io.StringIO()
    original = view.console
    view.console = Console(file=buf, width=120, force_terminal=False, color_system=None)
    try:
        render_event(event)
    finally:
        view.console = original
    return buf.getvalue()


def test_handover_names_the_result_when_it_exists():
    lines = handover_lines(
        {"status": "passed", "deliverable_path": "deliverables/ads.md", "deliverable_exists": True,
         "files": ["runs/notes/research.md", "deliverables/ads.md"]}
    )
    assert "result" in lines[0] and "deliverables/ads.md" in lines[0]
    assert "runs/notes/research.md" in lines[1]
    assert not any("resume" in line for line in lines)


def test_handover_on_unmet_lists_partial_work_and_resume_hint():
    lines = handover_lines(
        {"status": "unmet", "deliverable_path": "deliverables/deck.pptx", "deliverable_exists": False,
         "files": ["runs/notes/research.md"], "run_id": "20260905-abc"}
    )
    text = "\n".join(lines)
    assert "deliverables/deck.pptx was never written" in text
    assert "partial work" in text and "runs/notes/research.md" in text
    assert "rocky resume  20260905-abc" in text


def test_handover_with_nothing_written():
    lines = handover_lines({"passed": False, "summary": "boom"})
    assert "nothing was written" in lines[0]
    assert "rocky resume" in lines[-1]


def test_done_prints_the_path_not_only_the_price():
    out = _render(
        Event(type="done", payload={"status": "passed", "passed": True, "loops": 1, "receipt": "$0.01",
                                    "deliverable_path": "deliverables/ads.md", "deliverable_exists": True,
                                    "files": ["deliverables/ads.md"]})
    )
    assert "deliverables/ads.md" in out


def test_goal_and_review_transcript_preserve_literal_brackets():
    for event in (
        Event("run_started", {"goal": "Keep [time] and [/unknown] literally."}),
        Event("critique", {"quality_bar": "Keep [time] and [/unknown] literally."}),
        Event("plan", {"summary": "Keep [time] and [/unknown] literally.", "steps": []}),
        Event("verify", {"summary": "Kept [time] and [/unknown] literally.", "passed": True}),
        Event("done", {"summary": "Kept [time] and [/unknown] literally.", "notes": ["Use [date]."]}),
    ):
        text = _render(event)
        assert "[time]" in text
        assert "[/unknown]" in text


def test_error_and_worker_events_render_as_words_not_dicts():
    for event in (
        Event(type="error", payload={"message": "verifier failed: timeout"}),
        Event(type="nudge", payload={"subtask": "Write deck", "missing": ["deliverables/deck.pptx"], "n": 1}),
        Event(type="subtask_incomplete", payload={"id": 2, "title": "Write deck", "missing": ["deliverables/deck.pptx"]}),
        Event(type="compact", payload={"subtask": "Research", "mode": "prune", "before_tokens": 90000, "after_tokens": 30000}),
        Event(type="loop_guard", payload={"action": "abort", "name": "web_search", "subtask": "Research"}),
        Event(type="mcp", payload={"servers": ["fs"], "started": ["fs"], "tools": 3, "failures": {}}),
        Event(type="budget", payload={"cap": 1.09, "stage": "before replan"}),
    ):
        out = _render(event)
        assert "{" not in out, (event.type, out)
        assert out.strip(), event.type
    assert "timeout" in _render(Event(type="error", payload={"message": "verifier failed: timeout"}))
    assert "never wrote" in _render(
        Event(type="subtask_incomplete", payload={"id": 2, "title": "Write deck", "missing": ["deliverables/deck.pptx"]})
    )
