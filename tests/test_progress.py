from datetime import datetime, timezone
from io import StringIO

import pytest
from rich.console import Console

from rocky import __version__
from rocky.events import Event
from rocky.progress import RunProgress


def stamp(seconds):
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def board():
    return RunProgress({"goal": "Write [brief].md", "created": stamp(1000), "budget_eur": 0.5, "status": "running"}, "test-run")


def event(kind, seconds=1010, **payload):
    return Event(kind, payload, stamp(seconds))


def render(value, width=80, height=24, now=1060):
    stream = StringIO()
    Console(file=stream, width=width, height=height, color_system=None).print(value.render(width, height, now))
    return stream.getvalue()


def test_real_phases_include_checks_and_can_return_to_work_for_repairs():
    value = board()
    value.consume(event("phase", stage="verifying"))
    assert value.phase == "Checks"
    assert "Checking the result" in render(value)
    value.consume(event("gate", blocking=["Missing output"], soft=[]))
    value.consume(event("phase", stage="working"))
    assert value.phase == "Work"
    assert "Addressing: Missing output" in render(value)
    value.consume(event("gate", blocking=[], soft=[]))
    assert "Missing output" not in render(value)
    assert "Automatic checks passed" in render(value)


def test_steps_follow_reported_events_not_a_guessed_percentage():
    value = board()
    value.consume(event("plan", deliverable_path="deliverables/brief.md", steps=[{"id": i, "title": f"Step {i}"} for i in range(1, 5)]))
    value.consume(event("subtask_start", id=1, title="Step 1"))
    value.consume(event("subtask_incomplete", id=1))
    value.consume(event("subtask_done", id=1, completed=False))
    value.consume(event("subtask_skipped", id=2))
    value.consume(event("subtask_done", id=3, completed=True))
    assert [s["state"] for s in value.steps.values()] == ["incomplete", "skipped", "completed", "pending"]
    assert "%" not in render(value)
    assert "Target: deliverables/brief.md" in render(value)
    value.consume(event("subtask_start", id=1))
    value.consume(event("subtask_done", id=1, completed=True))
    assert value.steps["1"]["state"] == "completed"


def test_a_new_plan_resets_completed_steps_even_when_ids_and_titles_are_unchanged():
    value = board()
    value.consume(event("plan", steps=[{"id": 1, "title": "First"}, {"id": 2, "title": "Second"}]))
    value.consume(event("subtask_done", id=1, completed=True))
    value.consume(event("subtask_done", id=2, completed=True))
    value.consume(event("plan", steps=[{"id": 1, "title": "First"}, {"id": 2, "title": "Replacement"}]))
    assert value.steps["1"]["state"] == "pending"
    assert value.steps["2"]["state"] == "pending"


@pytest.mark.parametrize("result", ["exit 127\npython: command not found", "error: file does not exist", "ERROR: permission denied"])
def test_tool_failures_stay_visible_after_immediate_results_and_new_activity(result):
    value = board()
    value.consume(event("tool_call", name="bash", arguments={"command": "python check.py"}))
    value.consume(event("tool_result", name="bash", result=result))
    value.consume(event("tool_call", name="write_file", arguments={"path": "deliverables/brief.md"}))
    value.consume(event("tool_result", name="write_file", result="wrote 100 bytes"))
    assert len(value.actions) == 2
    assert value.actions[0]["state"] == "failed"
    text = render(value, 60, 20)
    assert "bash failed:" in text
    assert "writing" in text


def test_successful_bash_and_unpaired_replayed_result_remain_readable():
    value = board()
    value.consume(event("tool_result", name="bash", result="exit 0\nVerified"))
    assert value.actions[-1]["state"] == "completed"
    assert "✓ bash" in render(value)


def test_new_budget_and_reported_spend_survive_stale_metadata_and_replay():
    value = board()
    value.consume(event("cost", eur=0.25))
    value.consume(event("budget_set", budget_eur=2))
    value.update_meta({"status": "running", "budget_eur": 0.5})
    value.consume(event("cost", seconds=1005, eur=0.1))
    value.update_meta({"eur": 0.05})
    assert value.spent_eur == 0.25
    assert value.cap_eur == 2
    assert "Reported spend: EUR 0.250 / cap 2.00" in render(value)


def test_spend_is_unknown_until_reported_and_usd_is_never_mislabeled_eur():
    value = board()
    value.consume(event("budget", cap=4, usd=0.7))
    assert value.spent_eur is None
    assert "Reported spend: EUR — / cap 0.50" in render(value)


@pytest.mark.parametrize("status", ["stopped", "crashed", "error"])
def test_terminal_metadata_overrides_sparse_done_and_freezes_elapsed(status):
    value = board()
    value.consume(event("done", seconds=1020, passed=True))
    value.update_meta({"status": status, "finished": stamp(1030)})
    assert value.status == status
    text = render(value, now=1200)
    assert "Elapsed 00:30" in text
    assert "Ready to review" not in text
    assert render(value, now=1400) == text


def test_last_activity_time_never_moves_backwards_and_clock_skew_is_bounded():
    value = board()
    value.consume(event("phase", seconds=1040, stage="working"))
    value.consume(event("cost", seconds=1010, eur=0.1))
    assert "Last activity 00:20 ago" in render(value, now=1060)
    assert "Last activity 00:00 ago" in render(value, now=1030)
    assert "Elapsed 00:00" in render(value, now=900)


def test_replayed_events_have_the_same_state_and_age_as_live_events():
    events = [event("phase", stage="working"), event("tool_call", seconds=1020, name="read_file", arguments={"path": "brief.md"}), event("tool_result", seconds=1030, name="read_file", result="brief"), event("cost", seconds=1030, eur=0.04)]
    live, replay = board(), board()
    for item in events:
        live.consume(item)
    for item in events:
        replay.consume(Event.from_dict(item.as_dict()))
    assert render(live) == render(replay)


def test_question_is_visible_and_clears_without_changing_work_state():
    value = board()
    value.consume(event("phase", stage="working"))
    value.set_question("Choose [date] for the workshop")
    assert "Waiting for you: Choose [date]" in render(value)
    value.set_question(None)
    assert "Waiting for you" not in render(value)
    assert value.phase == "Work"


def test_answering_budget_question_stops_claiming_it_is_still_waiting():
    value = board()
    value.consume(event("budget_offer"))
    value.set_question("Raise the budget?")
    value.set_question(None)
    value.consume(event("budget_set", budget_eur=1))
    assert "Waiting for your budget choice" not in render(value)
    assert "Waiting for you:" not in render(value)
    assert value.cap_eur == 1


@pytest.mark.parametrize("size", [(60, 20), (80, 24), (100, 32), (30, 12)])
def test_board_is_bounded_and_keeps_brand_version_and_footer_when_resized(size):
    width, height = size
    value = board()
    value.consume(event("plan", steps=[{"id": i, "title": "A long step " * 20} for i in range(10)]))
    for i in range(10):
        value.consume(event("tool_call", name="write_file", arguments={"path": "very-long-" * 30 + str(i)}))
        value.consume(event("tool_result", name="write_file", result="ok"))
    text = render(value, width, height)
    assert len(text.splitlines()) <= height
    assert all(len(line) <= width for line in text.splitlines())
    assert "∩_____∩" in text
    assert f"v{__version__}" in text
    if width >= 60:
        assert "r o c k y" in text
        assert "Enter: keep working in background" in text
        assert "d + Enter: details" in text


def test_user_text_is_literal_and_terminal_controls_are_removed():
    value = board()
    value.update_meta({"goal": "Keep [red]literal[/red]\x1b[2J\x1b]0;bad title\x07\u202e and text"})
    value.consume(event("tool_call", name="bash", arguments={"command": "printf '[bold]ok[/bold]'\x1b[31m"}))
    text = render(value)
    assert "[red]literal[/red]" in text
    assert "[bold]ok[/bold]" in text
    assert "\x1b" not in text and "\x07" not in text and "\u202e" not in text
    assert "bad title" not in text


def test_rich_protocol_uses_the_console_dimensions():
    value = board()
    output = StringIO()
    Console(file=output, width=60, height=20, color_system=None).print(value)
    assert len(output.getvalue().splitlines()) <= 20
    assert all(len(line) <= 60 for line in output.getvalue().splitlines())
