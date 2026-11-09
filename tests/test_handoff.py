from io import StringIO

import pytest
from rich.console import Console

from rocky.artifacts import PREVIEW_BYTES
from rocky.handoff import HandoffAction, choose_action, render_result


def desk(tmp_path, monkeypatch, answers=(), *, status="passed", text="Bring a book at [time]."):
    path = tmp_path / "reminder.txt"
    path.write_text(text)
    result = {
        "status": status, "summary": "The reminder includes [time].",
        "deliverable_path": "reminder.txt", "files": ["reminder.txt"],
        "budget_eur": 0.5, "eur": 0.123,
    }
    buffer = StringIO()
    console = Console(file=buffer, width=80, height=24, color_system=None)
    inputs = iter(answers)
    prompts = []

    def read(prompt):
        prompts.append(str(prompt))
        answer = next(inputs)
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer):
            return answer()
        return answer

    monkeypatch.setattr(console, "input", read)
    return console, buffer, result, prompts


def test_preview_is_literal_bounded_and_cannot_control_terminal(tmp_path, monkeypatch):
    console, buffer, result, _ = desk(tmp_path, monkeypatch, text="[red]literal[/red] [time]\x1b[2J\x9b\x07\r\n" + "long text\n" * 100)
    result["notes"] = ["Keep [time]."]
    render_result(console, tmp_path, result)
    text = buffer.getvalue()
    assert "[red]literal[/red] [time]" in text
    assert "includes [time]" in text
    assert not any(c in text for c in ("\x1b", "\x9b", "\x07", "\r"))
    assert "shortened" in text
    assert text.count("long text") < 10
    assert "Spent: EUR 0.123" in text
    assert "Current workspace files" in text
    assert all(len(line) <= 80 for line in text.splitlines())


def test_preview_open_and_done_never_request_paid_work(tmp_path, monkeypatch):
    console, buffer, result, _ = desk(tmp_path, monkeypatch, ["1", "", "o", ""])
    opened = []
    action = choose_action(console, tmp_path, result, can_resume=True, open_file=lambda p: opened.append(p) or True)
    assert action is None
    assert opened == [tmp_path / "reminder.txt"]
    assert "Opened:" in buffer.getvalue()


def test_long_file_can_be_read_in_pages(tmp_path, monkeypatch):
    console, buffer, result, prompts = desk(tmp_path, monkeypatch, ["1", "", "q", ""], text="\n".join(f"row-{n}" for n in range(50)))
    assert choose_action(console, tmp_path, result, can_resume=True, open_file=lambda p: False) is None
    assert "row-25" in buffer.getvalue()
    assert "row-45" not in buffer.getvalue()
    assert any("next page" in p for p in prompts)


def test_large_preview_discloses_byte_limit(tmp_path, monkeypatch):
    console, buffer, result, _ = desk(tmp_path, monkeypatch, text="x" * (PREVIEW_BYTES + 1000))
    render_result(console, tmp_path, result)
    assert "128 KiB" in buffer.getvalue()
    assert len(buffer.getvalue()) < 2500


def test_refine_requires_instruction_then_valid_new_budget(tmp_path, monkeypatch):
    console, buffer, result, prompts = desk(tmp_path, monkeypatch, ["f", "", "f", "Shorten it to [time].", "nan", "inf", "0", "-1", "0,25"])
    action = choose_action(console, tmp_path, result, can_resume=True, open_file=lambda p: False)
    assert action == HandoffAction("refine", "Shorten it to [time].", 0.25)
    assert any("New run cap (EUR) [0.5]" in p for p in prompts)
    assert buffer.getvalue().count("finite amount") == 4


@pytest.mark.parametrize("status,can_resume,choice,expected", [
    ("unmet", True, "c", "continue"),
    ("stopped", True, "c", "continue"),
    ("error", False, "r", "restart"),
    ("crashed", False, "r", "restart"),
])
def test_failure_offers_appropriate_recovery(tmp_path, monkeypatch, status, can_resume, choice, expected):
    console, buffer, result, _ = desk(tmp_path, monkeypatch, [choice, ""], status=status)
    action = choose_action(console, tmp_path, result, can_resume=can_resume, open_file=lambda p: False)
    assert action == HandoffAction(expected, "", 0.5)
    assert "Needs attention" in buffer.getvalue()
    assert ("c: continue" in buffer.getvalue()) == can_resume
    assert ("r: restart" in buffer.getvalue()) == (not can_resume)


@pytest.mark.parametrize("answers", [
    ["not a command", "c", "r", ""],
    ["f", "q", ""],
    ["f", "Make it shorter", "q", ""],
    [EOFError()], [KeyboardInterrupt()], ["f", KeyboardInterrupt()],
])
def test_invalid_or_cancelled_actions_do_not_start_work(tmp_path, monkeypatch, answers):
    console, _, result, _ = desk(tmp_path, monkeypatch, answers)
    assert choose_action(console, tmp_path, result, can_resume=True, open_file=lambda p: False) is None


def test_pass_without_file_is_still_reviewable(tmp_path, monkeypatch):
    console, buffer, result, _ = desk(tmp_path, monkeypatch, ["v", "", ""])
    result.update(deliverable_path=None, files=[])
    assert choose_action(console, tmp_path, result, can_resume=False, open_file=lambda p: False) is None
    assert "Ready to review" in buffer.getvalue()
    assert "No saved file reported" in buffer.getvalue()
    assert "f: refine" not in buffer.getvalue()


def test_binary_and_missing_files_remain_available_for_selection(tmp_path, monkeypatch):
    console, buffer, result, _ = desk(tmp_path, monkeypatch, ["2", "o", "3", "o", ""])
    (tmp_path / "slides.pptx").write_bytes(b"PK\x00\x01")
    result["files"] += ["slides.pptx", "missing.md"]
    opened = []
    assert choose_action(console, tmp_path, result, can_resume=True, open_file=lambda p: opened.append(p) or True) is None
    assert opened == [tmp_path / "slides.pptx"]
    assert "No text preview for this format" in buffer.getvalue()
    assert "missing or no longer available" in buffer.getvalue()


def test_open_rechecks_selected_file_after_symlink_escape(tmp_path, monkeypatch):
    console, buffer, result, _ = desk(tmp_path, monkeypatch)
    outside = tmp_path.parent / f"outside-{tmp_path.name}.txt"
    outside.write_text("outside private content")

    def replace_and_open():
        path = tmp_path / "reminder.txt"
        path.unlink()
        path.symlink_to(outside)
        return "o"

    answers = iter([replace_and_open, "1", ""])
    monkeypatch.setattr(console, "input", lambda prompt: (a() if callable(a := next(answers)) else a))
    opened = []
    assert choose_action(console, tmp_path, result, can_resume=True, open_file=lambda p: opened.append(p) or True) is None
    assert opened == []
    assert "outside private content" not in buffer.getvalue()
    assert "no longer available" in buffer.getvalue()


def test_review_reloads_file_content_and_handles_read_failure(tmp_path, monkeypatch):
    console, buffer, result, _ = desk(tmp_path, monkeypatch)
    def edit_and_review():
        (tmp_path / "reminder.txt").write_text("The current revision.")
        return "1"
    answers = iter([edit_and_review, "", "1", ""])
    reads = 0
    from rocky.artifacts import read_preview

    def read(path):
        nonlocal reads
        reads += 1
        if reads == 3:
            raise PermissionError("not readable")
        return read_preview(path)

    monkeypatch.setattr("rocky.handoff.read_preview", read)
    monkeypatch.setattr(console, "input", lambda prompt: (a() if callable(a := next(answers)) else a))
    assert choose_action(console, tmp_path, result, can_resume=True, open_file=lambda p: False) is None
    assert "The current revision." in buffer.getvalue()
    assert "could not be read" in buffer.getvalue()


def test_full_verdict_includes_notes_beyond_compact_preview(tmp_path, monkeypatch):
    console, buffer, result, _ = desk(tmp_path, monkeypatch, ["v", "", ""])
    result["status"] = "passed_with_notes"
    result["notes"] = [f"Note {n}" for n in range(5)]
    assert choose_action(console, tmp_path, result, can_resume=True, open_file=lambda p: False) is None
    assert "Ready with notes" in buffer.getvalue()
    assert "Note 4" in buffer.getvalue()


def test_full_verdict_exposes_all_blockers_issues_and_remaining_work(tmp_path, monkeypatch):
    console, buffer, result, _ = desk(tmp_path, monkeypatch, ["v", "", "", ""], status="unmet")
    result.update(
        summary="The result needs corrections.",
        blocking=[f"Blocker {n}" for n in range(4)],
        issues=["Source citation missing"],
        remaining=["Write the conclusion"],
        notes=["Optional wording polish"],
    )
    assert choose_action(console, tmp_path, result, can_resume=True, open_file=lambda p: False) is None
    output = buffer.getvalue()
    compact, full = output.split("Review verdict and notes", 1)
    assert "Blocker 0" in compact and "Blocker 1" in compact
    assert "Blocker 2" not in compact and "Blocker 3" not in compact
    for text in ("Blocker 0", "Blocker 3", "Source citation missing", "Write the conclusion", "Optional wording polish"):
        assert text in full
    assert full.index("Needs fixing") < full.index("Review issues") < full.index("Remaining work") < full.index("Notes")
