"""The saved terminal brief crosses into a run exactly once, without paid calls."""

from io import StringIO
from itertools import count
from types import SimpleNamespace
import sys

import pytest
from rich.console import Console

from rocky import cli, composer, drafts
from rocky.drafts import TaskDraft, load_draft, save_draft
from rocky.runs import read_meta, run_dir, write_meta


def forbidden(*args, **kwargs):
    pytest.fail("Unexpected input, provider call, worker launch, or viewer")


@pytest.fixture(autouse=True)
def isolated_cli(monkeypatch, tmp_path):
    output = StringIO()
    monkeypatch.setattr(cli, "console", Console(file=output, width=120, color_system=None))
    monkeypatch.setattr(cli, "apply_config", lambda _: {})
    monkeypatch.setattr(cli, "api_key", lambda: "test-key")
    monkeypatch.setattr(cli, "_onboard", forbidden)
    monkeypatch.setattr(cli, "OpenRouterClient", forbidden)
    monkeypatch.setattr(cli, "spawn_worker", forbidden)
    monkeypatch.setattr(cli, "_task_session", forbidden)
    monkeypatch.setattr("builtins.input", forbidden)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    identifiers = count(1)
    monkeypatch.setattr("rocky.runs.new_run_id", lambda: f"20261109-140000-{next(identifiers):06d}")
    return output


@pytest.fixture
def args():
    return SimpleNamespace(budget=1.5, max_loops=3, no_server_tools=True)


def record_spawn(workspace, launches):
    def spawn(meta):
        meta.update(pid=12345, status="running")
        write_meta(run_dir(workspace, meta["id"]), meta)
        launches.append(dict(meta))
        return meta["pid"]

    return spawn


def test_multiline_submission_survives_exactly_and_clears_only_after_spawn(tmp_path, monkeypatch, args):
    goal = "  Paruošk santrauką.\n\n- Išlaikyk šaltinius.\n- Save deliverables/note.md.\n"
    submitted = save_draft(tmp_path, goal, "0,35")
    ui_calls, launches, order = [], [], []

    def editor(workspace, **kwargs):
        ui_calls.append(kwargs)
        if len(ui_calls) == 1:
            assert kwargs["initial"] is None
            return submitted
        assert kwargs["initial"] == TaskDraft("", "1.5")
        assert kwargs["error"] == ""
        assert load_draft(tmp_path) is None
        return None

    def spawn(meta):
        order.append("spawn")
        assert load_draft(tmp_path) == submitted
        return record_spawn(tmp_path, launches)(meta)

    def view(workspace, run_id):
        order.append("view")
        assert load_draft(tmp_path) is None
        assert read_meta(run_dir(workspace, run_id))["goal"] == goal
        return 0

    monkeypatch.setattr(composer, "run_composer", editor)
    monkeypatch.setattr(cli, "spawn_worker", spawn)
    monkeypatch.setattr(cli, "_task_session", view)
    assert cli._compose_tasks(tmp_path, args) == 0
    assert len(launches) == 1
    assert launches[0]["goal"] == goal
    assert launches[0]["budget_eur"] == 0.35
    assert launches[0]["max_loops"] == 3
    assert launches[0]["include_server_tools"] is False
    assert order == ["spawn", "view"]


def test_budget_edit_and_explicit_override_apply_to_one_task_only(tmp_path, monkeypatch, args):
    submitted = save_draft(tmp_path, "First task", "0.25")
    ui_calls, launches = [], []

    def editor(workspace, **kwargs):
        ui_calls.append(kwargs)
        if len(ui_calls) == 1:
            assert kwargs["budget_override"] is True
            return submitted
        assert kwargs["budget_override"] is False
        assert kwargs["initial"] == TaskDraft("", "1.5")
        if len(ui_calls) == 2:
            return save_draft(workspace, "Second task", kwargs["initial"].budget_text, expected_revision="")
        return None

    monkeypatch.setattr(composer, "run_composer", editor)
    monkeypatch.setattr(cli, "spawn_worker", record_spawn(tmp_path, launches))
    monkeypatch.setattr(cli, "_task_session", lambda *args: 0)
    assert cli._compose_tasks(tmp_path, args, budget_override=True) == 0
    assert [meta["budget_eur"] for meta in launches] == [0.25, 1.5]
    assert [meta["goal"] for meta in launches] == ["First task", "Second task"]
    assert args.budget == 1.5
    assert load_draft(tmp_path) is None


def test_cancel_keeps_the_saved_draft_without_creating_a_run(tmp_path, monkeypatch, args):
    submitted = save_draft(tmp_path, "Not ready\nKeep this draft", "0.4")
    monkeypatch.setattr(composer, "run_composer", lambda *args, **kwargs: None)
    assert cli._compose_tasks(tmp_path, args) == 0
    assert load_draft(tmp_path) == submitted
    assert not (tmp_path / ".rocky/runs").exists()


def test_failed_spawn_returns_the_same_draft_and_error_to_editor(tmp_path, monkeypatch, args):
    submitted = save_draft(tmp_path, "Keep this\nexact text\n", "0.4")
    calls, launches = [], []

    def editor(workspace, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return submitted
        assert kwargs["initial"] == submitted
        assert "launch denied" in kwargs["error"]
        assert kwargs["budget_override"] is False
        assert load_draft(tmp_path) == submitted
        return None

    def spawn(meta):
        launches.append(meta["id"])
        raise PermissionError("launch denied")

    monkeypatch.setattr(composer, "run_composer", editor)
    monkeypatch.setattr(cli, "spawn_worker", spawn)
    assert cli._compose_tasks(tmp_path, args, budget_override=True) == 1
    assert len(launches) == 1
    assert len(calls) == 2
    assert load_draft(tmp_path) == submitted


@pytest.mark.parametrize("budget", ["0", "-1", "NaN", "inf", "not a number"])
def test_invalid_budget_is_recoverable_without_a_launch(tmp_path, monkeypatch, args, budget):
    submitted = save_draft(tmp_path, "A task", budget)
    calls = []

    def editor(workspace, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return submitted
        assert kwargs["initial"] == submitted
        assert kwargs["error"]
        return None

    monkeypatch.setattr(composer, "run_composer", editor)
    assert cli._compose_tasks(tmp_path, args) == 1
    assert load_draft(tmp_path) == submitted
    assert not (tmp_path / ".rocky/runs").exists()


def test_starting_a_task_does_not_clear_a_newer_saved_revision(tmp_path, monkeypatch, args):
    submitted = save_draft(tmp_path, "Submitted task", "0.4")
    newer, launches, calls = [], [], []

    def editor(workspace, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return submitted
        assert kwargs["initial"] == TaskDraft("", "1.5")
        assert load_draft(workspace) == newer[0]
        return None

    def spawn(meta):
        newer.append(save_draft(tmp_path, "Work in another terminal", "0.6", expected_revision=submitted.revision))
        return record_spawn(tmp_path, launches)(meta)

    monkeypatch.setattr(composer, "run_composer", editor)
    monkeypatch.setattr(cli, "spawn_worker", spawn)
    monkeypatch.setattr(cli, "_task_session", lambda *args: 0)
    assert cli._compose_tasks(tmp_path, args) == 0
    assert len(launches) == 1
    assert load_draft(tmp_path) == newer[0]


@pytest.mark.parametrize("failure", [OSError("storage locked"), ValueError("unsafe draft storage")])
def test_clear_failure_after_start_warns_but_never_resubmits_the_saved_task(tmp_path, monkeypatch, args, isolated_cli, failure):
    submitted = save_draft(tmp_path, "Submitted task", "0.4")
    launches, calls = [], []

    def editor(workspace, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return submitted
        assert kwargs["initial"] == TaskDraft("", "1.5")
        assert kwargs["error"] == ""
        return None

    def fail_clear(workspace, revision):
        assert revision == submitted.revision
        assert len(launches) == 1
        raise failure

    monkeypatch.setattr(composer, "run_composer", editor)
    monkeypatch.setattr(drafts, "clear_draft", fail_clear)
    monkeypatch.setattr(cli, "spawn_worker", record_spawn(tmp_path, launches))
    monkeypatch.setattr(cli, "_task_session", lambda *args: 0)
    assert cli._compose_tasks(tmp_path, args) == 0
    assert len(launches) == 1
    assert len(calls) == 2
    assert load_draft(tmp_path) == submitted
    assert "could not be cleared" in isolated_cli.getvalue()


@pytest.mark.parametrize("failure", [OSError("viewer closed"), ValueError("invalid viewer state")])
def test_viewer_failure_after_spawn_does_not_offer_to_launch_the_draft_again(tmp_path, monkeypatch, args, isolated_cli, failure):
    submitted = save_draft(tmp_path, "Submitted task", "0.4")
    launches, calls = [], []

    def editor(*args, **kwargs):
        calls.append(kwargs)
        assert len(calls) == 1, "A running task must not reopen as an unstarted draft"
        return submitted

    def view(*args):
        raise failure

    monkeypatch.setattr(composer, "run_composer", editor)
    monkeypatch.setattr(cli, "spawn_worker", record_spawn(tmp_path, launches))
    monkeypatch.setattr(cli, "_task_session", view)
    assert cli._compose_tasks(tmp_path, args) == 1
    assert len(launches) == 1
    assert load_draft(tmp_path) is None
    assert f"rocky attach {launches[0]['id']}" in isolated_cli.getvalue()


def test_pid_saved_in_memory_after_process_launch_prevents_retry_if_metadata_write_fails(tmp_path, monkeypatch, args, isolated_cli):
    submitted = save_draft(tmp_path, "Already launched task", "0.4")
    launches, calls = [], []

    def editor(*args, **kwargs):
        calls.append(kwargs)
        assert len(calls) == 1
        return submitted

    def spawn(meta):
        meta["pid"] = 123
        launches.append(dict(meta))
        raise PermissionError("could not save worker metadata")

    monkeypatch.setattr(composer, "run_composer", editor)
    monkeypatch.setattr(cli, "spawn_worker", spawn)
    assert cli._compose_tasks(tmp_path, args) == 1
    assert len(launches) == 1
    assert load_draft(tmp_path) is None
    assert f"rocky attach {launches[0]['id']}" in isolated_cli.getvalue()


def test_unsaved_submission_never_clears_some_other_draft(tmp_path, monkeypatch, args):
    existing = save_draft(tmp_path, "Another editor's work", "0.7")
    submitted = TaskDraft("Submit without saving", "0.3")
    calls = iter([submitted, None])
    launches = []
    monkeypatch.setattr(composer, "run_composer", lambda *args, **kwargs: next(calls))
    monkeypatch.setattr(drafts, "clear_draft", forbidden)
    monkeypatch.setattr(cli, "spawn_worker", record_spawn(tmp_path, launches))
    monkeypatch.setattr(cli, "_task_session", lambda *args: 0)
    assert cli._compose_tasks(tmp_path, args) == 0
    assert len(launches) == 1
    assert load_draft(tmp_path) == existing


def fake_client(monkeypatch):
    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def close(self):
            pass

    monkeypatch.setattr(cli, "OpenRouterClient", Client)
    monkeypatch.setattr("rocky.banner.print_banner", lambda **kwargs: None)


@pytest.mark.parametrize("explicit", [False, True])
def test_main_forwards_explicit_budget_precedence_without_treating_config_as_override(tmp_path, monkeypatch, explicit):
    fake_client(monkeypatch)
    monkeypatch.setattr(cli, "apply_config", lambda _: {"budget": "1.9"})
    calls = []
    monkeypatch.setattr(cli, "_compose_tasks", lambda workspace, args, **kwargs: calls.append((args, kwargs)) or 0)
    argv = ["-d", str(tmp_path)] + (["--budget", "0.6"] if explicit else [])
    assert cli.main(argv) == 0
    assert len(calls) == 1
    assert calls[0][0].budget == (0.6 if explicit else 1.9)
    assert calls[0][1]["budget_override"] is explicit


@pytest.mark.parametrize("with_goal", [False, True])
def test_route_mode_keeps_its_existing_prompt_path(tmp_path, monkeypatch, with_goal):
    fake_client(monkeypatch)
    monkeypatch.setattr(cli, "_compose_tasks", forbidden)
    goals = iter(["Find a model", ""])
    monkeypatch.setattr(cli, "prompt_goal", forbidden if with_goal else lambda: next(goals))
    routed = []
    monkeypatch.setattr(cli, "_route", lambda client, goal: routed.append(goal) or 0)
    argv = ["route"] + (["Find a model"] if with_goal else []) + ["-d", str(tmp_path)]
    assert cli.main(argv) == 0
    assert routed == ["Find a model"]


def test_json_without_a_goal_does_not_open_the_composer(tmp_path, monkeypatch):
    fake_client(monkeypatch)
    monkeypatch.setattr(cli, "_compose_tasks", forbidden)
    monkeypatch.setattr(cli, "prompt_goal", forbidden)
    assert cli.main(["--json", "-d", str(tmp_path)]) == 2
