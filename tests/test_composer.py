import asyncio
import json

import pytest
from textual import events
from textual.widgets import Button, Input, Static

from rocky import __version__
from rocky import composer
from rocky.composer import BriefArea, TaskComposer
from rocky.drafts import TaskDraft, load_draft, save_draft


def content(app, selector):
    rendered = app.query_one(selector, Static).render()
    return getattr(rendered, "plain", str(rendered))


@pytest.mark.parametrize("submit", ["key", "button"])
def test_multiline_paste_and_enter_never_start_until_explicit_submission(tmp_path, submit):
    async def exercise():
        app = TaskComposer(tmp_path, budget_eur=1.25)
        async with app.run_test(size=(80, 24)) as pilot:
            pasted = "Create a campaign brief\nAudience: local bookshops\nOutput: checklist.md"
            app.post_message(events.Paste(pasted))
            await pilot.pause()
            await pilot.press("enter", "q")
            assert app.query_one(BriefArea).text == pasted + "\nq"
            assert app.return_value is None
            app.query_one("#budget", Input).value = "0,75"
            await pilot.pause()
            if submit == "key":
                await pilot.press("ctrl+r")
            else:
                await pilot.click("#start")
        result = app.return_value
        assert result.goal == pasted + "\nq"
        assert result.budget_text == "0,75"
        assert result.revision
        assert load_draft(tmp_path) == result, "Only the CLI may clear a launched draft"

    asyncio.run(exercise())


def test_budget_validation_preserves_brief_and_ctrl_r_does_not_submit(tmp_path):
    async def exercise():
        app = TaskComposer(tmp_path, budget_eur=1)
        async with app.run_test() as pilot:
            await pilot.press("ctrl+r")
            assert app.return_value is None
            assert app.query_one("#start", Button).disabled
            assert "Write a task" in content(app, "#error")
            app.post_message(events.Paste("Keep this complete\nmultiline brief."))
            await pilot.pause()
            for invalid in ("", "nan", "inf", "0", "-1", "not a number"):
                app.query_one("#budget", Input).value = invalid
                await pilot.pause()
                await pilot.press("ctrl+r")
                assert app.return_value is None
                assert app.query_one("#start", Button).disabled
                assert "finite amount" in content(app, "#error")
                assert app.query_one(BriefArea).text == "Keep this complete\nmultiline brief."
            await pilot.press("ctrl+q")
        assert load_draft(tmp_path).budget_text == "not a number"

    asyncio.run(exercise())


def test_save_exit_flushes_and_restores_invalid_raw_budget_and_text(tmp_path):
    async def exercise():
        first = TaskComposer(tmp_path, budget_eur=1)
        async with first.run_test() as pilot:
            first.query_one(BriefArea).load_text("  My draft\nwith blank lines\n\n")
            first.query_one("#budget", Input).value = "not finished"
            await pilot.pause()
            await pilot.click("#save-exit")
        saved = load_draft(tmp_path)
        assert first.return_value is None
        assert saved.goal == "  My draft\nwith blank lines\n\n"
        assert saved.budget_text == "not finished"
        restored = TaskComposer(tmp_path, budget_eur=9)
        async with restored.run_test() as pilot:
            assert restored.query_one(BriefArea).text == saved.goal
            assert restored.query_one("#budget", Input).value == saved.budget_text
            assert "Restored draft" in content(restored, "#save-status")
            assert restored.query_one("#start", Button).disabled
            await pilot.press("ctrl+q")
        assert load_draft(tmp_path).revision == saved.revision

    asyncio.run(exercise())


@pytest.mark.parametrize("override,expected", [(False, "0,75"), (True, "2.5")])
def test_explicit_budget_override_wins_only_when_requested(tmp_path, override, expected):
    save_draft(tmp_path, "A restored task", "0,75")

    async def exercise():
        app = TaskComposer(tmp_path, budget_eur=2.5, budget_override=override)
        async with app.run_test() as pilot:
            assert app.query_one("#budget", Input).value == expected
            await pilot.press("ctrl+r")
        assert app.return_value.goal == "A restored task"
        assert app.return_value.budget_text == expected

    asyncio.run(exercise())


def test_untouched_blank_initial_does_not_overwrite_another_draft(tmp_path):
    existing = save_draft(tmp_path, "Another terminal's work", "7")

    async def exercise():
        app = TaskComposer(tmp_path, budget_eur=1, initial=TaskDraft("", "1"))
        async with app.run_test() as pilot:
            await pilot.pause(0.45)
            assert app.query_one(BriefArea).text == ""
            await pilot.press("ctrl+q")
        assert load_draft(tmp_path) == existing

    asyncio.run(exercise())


def test_autosave_uses_latest_text_and_revision(tmp_path):
    async def exercise():
        app = TaskComposer(tmp_path, budget_eur=1)
        async with app.run_test() as pilot:
            app.post_message(events.Paste("Draft one\n"))
            await pilot.pause(0.45)
            first = load_draft(tmp_path)
            assert first.goal == "Draft one\n"
            app.post_message(events.Paste("Draft two"))
            await pilot.pause(0.45)
            second = load_draft(tmp_path)
            assert second.goal == "Draft one\nDraft two"
            assert second.revision != first.revision
            assert app.saved_revision == second.revision
            await pilot.press("ctrl+q")

    asyncio.run(exercise())


def test_save_failure_is_visible_and_explicit_unsaved_exit_is_available(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise PermissionError("workspace is read only")

    monkeypatch.setattr(composer, "save_draft", fail)

    async def exercise():
        app = TaskComposer(tmp_path, budget_eur=1)
        async with app.run_test(size=(60, 20)) as pilot:
            app.post_message(events.Paste("Keep this draft"))
            await pilot.pause()
            await pilot.press("ctrl+q")
            assert app.return_value is None and not app._finished
            assert "Draft not saved" in content(app, "#save-status")
            assert "read only" in content(app, "#error")
            assert app.query_one("#exit-unsaved", Button).display
            await pilot.click("#exit-unsaved")
        assert app.return_value is None
        assert load_draft(tmp_path) is None

    asyncio.run(exercise())


def test_save_conflict_preserves_other_terminal_and_can_submit_without_revision(tmp_path):
    original = save_draft(tmp_path, "Original", "1")
    app = TaskComposer(tmp_path, budget_eur=1)
    other = save_draft(tmp_path, "Other terminal", "3", expected_revision=original.revision)

    async def exercise():
        async with app.run_test() as pilot:
            app.query_one(BriefArea).load_text("My unsaved revision\nwith details")
            await pilot.pause()
            await pilot.press("ctrl+r")
        assert app.return_value == TaskDraft("My unsaved revision\nwith details", "1", "", original.revision)
        assert "another terminal" in app.save_error
        assert load_draft(tmp_path) == other

    asyncio.run(exercise())


def test_corrupt_saved_draft_stays_intact_and_does_not_prevent_a_new_submission(tmp_path):
    directory = tmp_path / ".rocky"
    directory.mkdir()
    path = directory / "composer.json"
    path.write_bytes(b"not a valid draft")

    async def exercise():
        app = TaskComposer(tmp_path, budget_eur=2)
        async with app.run_test() as pilot:
            assert "file kept" in content(app, "#error")
            assert app.query_one("#exit-unsaved", Button).display
            app.post_message(events.Paste("A replacement task\nwithout overwriting the file"))
            await pilot.pause(0.45)
            assert path.read_bytes() == b"not a valid draft"
            await pilot.press("ctrl+r")
        assert app.return_value.revision == ""
        assert app.return_value.budget_text == "2"
        assert path.read_bytes() == b"not a valid draft"

    asyncio.run(exercise())


def test_os_paste_replaces_selection_once_and_preserves_newlines(tmp_path, monkeypatch):
    monkeypatch.setattr(composer, "paste_text", lambda: "new\ntext")

    async def exercise():
        app = TaskComposer(tmp_path, budget_eur=1)
        async with app.run_test() as pilot:
            area = app.query_one(BriefArea)
            area.load_text("old text")
            area.action_select_all()
            await pilot.press("ctrl+v")
            assert area.text == "new\ntext"
            await pilot.press("ctrl+q")

    asyncio.run(exercise())


def test_ctrl_c_copies_editor_selection_to_os_clipboard_without_exiting(tmp_path, monkeypatch):
    copied = []
    monkeypatch.setattr(composer, "copy_text", lambda text: copied.append(text) or True)

    async def exercise():
        app = TaskComposer(tmp_path, budget_eur=1)
        async with app.run_test() as pilot:
            area = app.query_one(BriefArea)
            area.load_text("Copy this\nwhole brief")
            area.action_select_all()
            await pilot.press("ctrl+c")
            assert copied == ["Copy this\nwhole brief"]
            assert app.clipboard == copied[0]
            assert not app._finished
            await pilot.press("ctrl+q")

    asyncio.run(exercise())


def test_reverting_after_a_save_conflict_does_not_claim_a_successful_save(tmp_path):
    first = save_draft(tmp_path, "Original", "1")
    app = TaskComposer(tmp_path, budget_eur=1)
    other = save_draft(tmp_path, "Another terminal", "2", expected_revision=first.revision)

    async def exercise():
        async with app.run_test() as pilot:
            app.query_one(BriefArea).load_text("Edited here")
            await pilot.pause()
            await pilot.press("ctrl+q")
            assert app.save_error and not app._finished
            app.query_one(BriefArea).load_text("Original")
            await pilot.pause()
            await pilot.press("ctrl+r")
        assert app.return_value == TaskDraft("Original", "1", "", first.revision)
        assert load_draft(tmp_path) == other

    asyncio.run(exercise())


@pytest.mark.parametrize("size", [(80, 24), (60, 20)])
def test_editor_and_keyboard_actions_remain_reachable_at_small_sizes(tmp_path, size):
    async def exercise():
        app = TaskComposer(tmp_path, budget_eur=1, error="Previous launch failed; the brief is retained.")
        async with app.run_test(size=size) as pilot:
            brand = content(app, "#brand")
            assert "∩_____∩" in brand and "r o c k y" in brand and "the retriever" in brand
            assert content(app, "#workspace").startswith(f"    v{__version__}  ·  Workspace: ")
            assert app.query_one("#workspace").region.y == app.query_one("#brand").region.bottom
            assert str(tmp_path)[-15:] in content(app, "#workspace")
            assert len(content(app, "#workspace")) <= size[0] - 2
            assert app.query_one(BriefArea).region.height >= 5
            for selector in ("#brand", "#brief", "#budget", "#start", "#save-exit", "#keys"):
                region = app.query_one(selector).region
                assert 0 <= region.x < region.right <= size[0]
                assert 0 <= region.y < region.bottom <= size[1]
            await pilot.press("tab")
            assert app.focused.id == "budget"
            await pilot.press("shift+tab")
            assert app.focused.id == "brief"
            await pilot.press("ctrl+q")

    asyncio.run(exercise())


def test_live_resize_updates_brand_and_keeps_error_actions_inside_the_terminal(tmp_path):
    directory = tmp_path / ".rocky"
    directory.mkdir()
    path = directory / "composer.json"
    path.write_bytes(b"broken draft")

    async def exercise():
        app = TaskComposer(
            tmp_path, budget_eur=1,
            error="First launch detail.\nSecond launch detail.\nThird launch detail.",
        )
        async with app.run_test(size=(80, 24)) as pilot:
            brief = "Keep this multiline\nbrief while resizing."
            app.query_one(BriefArea).load_text(brief)
            await pilot.pause()
            for width, height, brand_height in ((80, 24, 6), (60, 20, 4), (120, 40, 6)):
                await pilot.resize_terminal(width, height)
                await pilot.pause()
                assert app.query_one("#brand").region.height == brand_height
                assert ("What should Rocky deliver?" in content(app, "#brand")) == (brand_height == 6)
                assert content(app, "#workspace").startswith(f"    v{__version__}  ·  Workspace: ")
                assert app.query_one("#error").region.height == 3
                assert app.query_one("#exit-unsaved", Button).display
                assert app.query_one(BriefArea).region.height >= 4
                assert app.query_one(BriefArea).text == brief
                assert app.query_one("#budget", Input).value == "1"
                assert len(content(app, "#workspace")) <= width - 2
                for selector in ("#brand", "#workspace", "#brief", "#budget", "#error", "#start", "#save-exit", "#exit-unsaved", "#keys"):
                    region = app.query_one(selector).region
                    assert 0 <= region.x < region.right <= width, selector
                    assert 0 <= region.y < region.bottom <= height, selector
            await pilot.click("#exit-unsaved")
        assert app.return_value is None
        assert path.read_bytes() == b"broken draft"

    asyncio.run(exercise())


def test_wrapper_returns_submission_and_discloses_failed_save(tmp_path, monkeypatch, capsys):
    def run(app):
        app.save_error = "Draft could not be saved: read only"
        return TaskDraft("Use this task", "1", "")

    monkeypatch.setattr(TaskComposer, "run", run)
    assert composer.run_composer(tmp_path, budget_eur=1) == TaskDraft("Use this task", "1", "")
    assert "no saved draft will be cleared" in capsys.readouterr().out


def test_retry_after_save_and_launch_failure_can_save_when_storage_recovers(tmp_path, monkeypatch):
    original = save_draft(tmp_path, "Original saved brief", "1")
    updated = "Updated brief\nwith all of its details"

    def unavailable(*args, **kwargs):
        raise PermissionError("temporary disk error")

    async def exercise():
        first = TaskComposer(tmp_path, budget_eur=1)
        async with first.run_test() as pilot:
            with monkeypatch.context() as failure:
                failure.setattr(composer, "save_draft", unavailable)
                first.query_one(BriefArea).load_text(updated)
                first.query_one("#budget", Input).value = "0,7"
                await pilot.pause()
                await pilot.press("ctrl+r")
        submitted = first.return_value
        assert submitted == TaskDraft(updated, "0,7", "", original.revision)
        assert load_draft(tmp_path) == original

        # The CLI passes this exact unsaved submission back after a failed launch.
        retry = TaskComposer(tmp_path, budget_eur=1, initial=submitted, error="Worker launch failed")
        assert retry._saved_values is None, "A retry baseline is not a saved copy of the new text"
        async with retry.run_test() as pilot:
            assert retry.query_one(BriefArea).text == updated
            await pilot.press("ctrl+q")
            assert retry._finished
        saved = load_draft(tmp_path)
        assert saved.goal == updated and saved.budget_text == "0,7"
        assert saved.revision != original.revision
        assert saved.base_revision == ""
        assert "base_revision" not in json.loads((tmp_path / ".rocky/composer.json").read_text())

    asyncio.run(exercise())


def test_retry_baseline_does_not_overwrite_a_real_newer_external_draft(tmp_path, monkeypatch):
    original = save_draft(tmp_path, "Original saved brief", "1")

    def unavailable(*args, **kwargs):
        raise PermissionError("temporary disk error")

    async def exercise():
        first = TaskComposer(tmp_path, budget_eur=1)
        async with first.run_test() as pilot:
            with monkeypatch.context() as failure:
                failure.setattr(composer, "save_draft", unavailable)
                first.query_one(BriefArea).load_text("My unsaved changed brief")
                await pilot.pause()
                await pilot.press("ctrl+r")
        submitted = first.return_value
        assert submitted.revision == "" and submitted.base_revision == original.revision
        newer = save_draft(tmp_path, "New work in another terminal", "2", expected_revision=original.revision)
        retry = TaskComposer(tmp_path, budget_eur=1, initial=submitted, error="Worker launch failed")
        async with retry.run_test() as pilot:
            await pilot.press("ctrl+q")
            assert not retry._finished
            assert "another terminal" in retry.save_error
            assert load_draft(tmp_path) == newer
            await pilot.click("#exit-unsaved")
        assert load_draft(tmp_path) == newer

    asyncio.run(exercise())
