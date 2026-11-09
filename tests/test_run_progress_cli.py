"""Run-board integration with saved runs; no provider calls or worker processes."""

import json
from io import StringIO
from types import SimpleNamespace

import pytest
from rich.console import Console

from rocky import cli, view
from rocky.events import Event
from rocky.runs import (
    EventCursor, append_event, create_run, read_meta, run_dir, take_answer,
    write_answer, write_ask, write_meta,
)


def forbidden(*args, **kwargs):
    pytest.fail("Unexpected prompt, model call, or worker mutation")


class FakeLive:
    instances = []

    def __init__(self, renderable, **kwargs):
        self.active = False
        self.calls = []
        self.frames = [str(renderable)]
        self.kwargs = kwargs
        self.instances.append(self)

    def start(self):
        self.active = True
        self.calls.append("start")

    def stop(self):
        self.active = False
        self.calls.append("stop")

    def update(self, renderable, **kwargs):
        assert self.active, "The board must be running before it refreshes"
        self.frames.append(str(renderable))
        self.calls.append("update")


@pytest.fixture
def board_cli(monkeypatch):
    output = StringIO()
    console = Console(file=output, force_terminal=True, color_system=None, width=100, height=32)
    monkeypatch.setattr(cli, "console", console)
    monkeypatch.setattr(view, "console", console)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(cli, "prompt_goal", forbidden)
    monkeypatch.setattr(cli, "api_key", forbidden)
    monkeypatch.setattr(cli, "OpenRouterClient", forbidden)
    monkeypatch.setattr(cli, "spawn_worker", forbidden)
    monkeypatch.setattr(cli, "stop_run", forbidden)
    monkeypatch.setattr(cli, "apply_config", lambda _: {})
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    monkeypatch.setattr("rocky.runs.pid_alive", lambda *args, **kwargs: True)
    monkeypatch.setattr("rich.live.Live", FakeLive)
    FakeLive.instances = []
    return output


def running(workspace, *, status="running"):
    meta = create_run(
        workspace=workspace, goal="Write a useful note", budget_eur=0.5,
        max_loops=3, include_server_tools=False,
    )
    meta.update(status=status, pid=1234)
    folder = run_dir(workspace, meta["id"])
    write_meta(folder, meta)
    append_event(folder, Event("run_started", {"goal": meta["goal"], "budget_eur": 0.5}))
    return meta, folder


def finish(workspace, meta, *, status="passed", append_done=True, summary="Saved the note"):
    folder = run_dir(workspace, meta["id"])
    target = workspace / "deliverables/note.md"
    target.parent.mkdir(exist_ok=True)
    target.write_text("A useful note.\n")
    latest = read_meta(folder)
    latest.update(
        status=status, passed=status in {"passed", "passed_with_notes"},
        deliverable_path="deliverables/note.md", deliverable_exists=True,
        files=["deliverables/note.md"], summary=summary, eur=0.04,
        finished="2026-11-09T12:01:00+00:00",
    )
    write_meta(folder, latest)
    if append_done:
        append_event(folder, Event("done", dict(latest)))


def test_question_pauses_board_then_resumes_once_and_finishes(tmp_path, monkeypatch, board_cli):
    meta, folder = running(tmp_path, status="waiting")
    write_ask(folder, "Keep [budget] unchanged?", ["keep", "raise"])
    prompts = []

    def answer(label):
        assert not FakeLive.instances[0].active
        prompts.append(label)
        return "keep"

    def progress(timeout):
        assert FakeLive.instances[0].active
        assert take_answer(folder) == "keep"
        finish(tmp_path, meta)
        return None

    monkeypatch.setattr(cli, "prompt_goal", answer)
    monkeypatch.setattr(cli, "_stdin_line", progress)
    assert cli._watch_progress(tmp_path, meta["id"]) == (0, True)
    assert prompts == ["Answer"]
    live = FakeLive.instances[0]
    assert live.calls.count("start") == 2
    assert not live.active
    assert "Keep [budget] unchanged?" in board_cli.getvalue()
    assert "deliverables/note.md" in board_cli.getvalue()


def test_reattach_keeps_an_answer_already_waiting_for_the_worker(tmp_path, monkeypatch, board_cli):
    meta, folder = running(tmp_path, status="waiting")
    write_ask(folder, "Raise cap?", ["yes", "keep"])
    write_answer(folder, "keep")
    before = (folder / "answer.json").read_bytes()

    def progress(timeout):
        assert (folder / "answer.json").read_bytes() == before
        finish(tmp_path, meta)
        return None

    monkeypatch.setattr(cli, "_stdin_line", progress)
    assert cli._watch_progress(tmp_path, meta["id"]) == (0, True)
    assert (folder / "answer.json").read_bytes() == before
    assert FakeLive.instances[0].calls.count("start") == 1
    assert "Rocky needs your answer" not in board_cli.getvalue()


@pytest.mark.parametrize("status,code", [("passed", 0), ("stopped", 1), ("crashed", 1), ("error", 1)])
def test_terminal_metadata_ignores_stale_ask_and_buffered_enter(tmp_path, monkeypatch, board_cli, status, code):
    meta, folder = running(tmp_path)
    finish(tmp_path, meta, status=status)
    write_ask(folder, "Stale question", ["yes"])
    monkeypatch.setattr(cli, "_stdin_line", forbidden)
    assert cli._watch_progress(tmp_path, meta["id"]) == (code, True)
    assert "Stale question" not in board_cli.getvalue()
    assert status in board_cli.getvalue()
    assert not FakeLive.instances[0].active


def test_terminal_drain_keeps_a_done_event_appended_after_metadata(tmp_path, monkeypatch, board_cli):
    meta, folder = running(tmp_path)
    finish(tmp_path, meta, append_done=False, summary="Metadata summary")
    created = []

    class DelayedCursor:
        def __init__(self, path):
            self.inner = EventCursor(path)
            self.polls = 0
            created.append(self)

        def poll(self):
            self.polls += 1
            if self is created[0] and self.polls == 2:
                append_event(folder, Event("done", {
                    "passed": True, "status": "passed", "summary": "Late final result",
                    "deliverable_path": "deliverables/note.md", "deliverable_exists": True,
                    "eur": 0.04,
                }))
            return self.inner.poll()

    monkeypatch.setattr(cli, "EventCursor", DelayedCursor)
    monkeypatch.setattr(cli, "_stdin_line", forbidden)
    assert cli._watch_progress(tmp_path, meta["id"]) == (0, True)
    assert "Late final result" in board_cli.getvalue()
    assert created[0].polls >= 4


def test_details_toggle_keeps_receiving_events_and_returns_to_board(tmp_path, monkeypatch, board_cli):
    meta, folder = running(tmp_path)
    calls = []

    def input_line(timeout):
        live = FakeLive.instances[0]
        calls.append(live.active)
        if len(calls) == 1:
            append_event(folder, Event("tool_call", {"name": "read_file", "arguments": {"path": "inside-details.md"}}))
            return "d\n"
        if len(calls) == 2:
            append_event(folder, Event("tool_call", {"name": "write_file", "arguments": {"path": "after-details.md"}}))
            append_event(folder, Event("tool_result", {"name": "write_file", "result": "wrote after-details.md (10 chars)"}))
            return "d\n"
        finish(tmp_path, meta)
        return None

    monkeypatch.setattr(cli, "_stdin_line", input_line)
    assert cli._watch_progress(tmp_path, meta["id"]) == (0, True)
    assert calls == [True, False, True]
    assert "inside-details.md" in board_cli.getvalue()
    assert any("after-details.md" in frame for frame in FakeLive.instances[0].frames)
    assert FakeLive.instances[0].calls.count("start") == 2


def test_question_in_details_mode_does_not_restart_the_hidden_board(tmp_path, monkeypatch, board_cli):
    meta, folder = running(tmp_path)
    calls = []

    def answer(label):
        assert not FakeLive.instances[0].active
        return "keep"

    def input_line(timeout):
        calls.append(FakeLive.instances[0].active)
        if len(calls) == 1:
            write_ask(folder, "Raise cap?", ["keep", "raise"])
            latest = read_meta(folder)
            write_meta(folder, dict(latest, status="waiting"))
            return "d\n"
        assert take_answer(folder) == "keep"
        finish(tmp_path, meta)
        return None

    monkeypatch.setattr(cli, "prompt_goal", answer)
    monkeypatch.setattr(cli, "_stdin_line", input_line)
    assert cli._watch_progress(tmp_path, meta["id"]) == (0, True)
    assert calls == [True, False]
    assert FakeLive.instances[0].calls.count("start") == 1


@pytest.mark.parametrize("status,code", [("passed", 0), ("stopped", 1)])
def test_details_prints_completion_once_using_authoritative_metadata(tmp_path, monkeypatch, board_cli, status, code):
    meta, folder = running(tmp_path)
    rendered_done = []
    reads = 0

    def render(event):
        if event.type == "done":
            rendered_done.append(event.payload)
        view.render_event(event)

    def input_line(timeout):
        nonlocal reads
        reads += 1
        if reads == 1:
            return "d\n"
        assert not FakeLive.instances[0].active
        finish(tmp_path, meta, status=status, append_done=False)
        # The final metadata may supersede a worker's earlier successful event.
        append_event(folder, Event("done", {"passed": True, "status": "passed", "eur": 0.04}))
        return None

    monkeypatch.setattr(cli, "render_event", render)
    monkeypatch.setattr(cli, "_stdin_line", input_line)
    assert cli._watch_progress(tmp_path, meta["id"]) == (code, True)
    assert len(rendered_done) == 1
    assert rendered_done[0]["run_id"] == meta["id"]
    assert rendered_done[0]["status"] == status
    assert rendered_done[0]["passed"] is (status == "passed")
    assert rendered_done[0]["deliverable_path"] == "deliverables/note.md"


@pytest.mark.parametrize("interrupt", [False, True])
def test_detach_stops_the_view_but_preserves_running_job(tmp_path, monkeypatch, board_cli, interrupt):
    meta, folder = running(tmp_path)
    before = (folder / "meta.json").read_bytes()

    def detach(timeout):
        if interrupt:
            raise KeyboardInterrupt
        return "\n"

    monkeypatch.setattr(cli, "_stdin_line", detach)
    assert cli._watch_progress(tmp_path, meta["id"]) == (0, False)
    assert (folder / "meta.json").read_bytes() == before
    assert not FakeLive.instances[0].active
    assert "background" in board_cli.getvalue()
    assert f"rocky attach {meta['id']}" in board_cli.getvalue()


@pytest.mark.parametrize("stdin,stdout,dumb,logs,expected", [
    (True, True, False, False, "board"),
    (False, True, False, False, "transcript"),
    (True, False, False, False, "transcript"),
    (False, False, False, False, "transcript"),
    (True, True, True, False, "transcript"),
    (True, True, False, True, "transcript"),
])
def test_board_routing_preserves_noninteractive_dumb_and_logs_modes(monkeypatch, tmp_path, stdin, stdout, dumb, logs, expected):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: stdin)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: stdout)
    monkeypatch.setattr(cli, "console", SimpleNamespace(is_dumb_terminal=dumb))
    called = []
    monkeypatch.setattr(cli, "_watch_progress", lambda *args: called.append(("board",)) or (0, False))
    monkeypatch.setattr(cli, "_watch_transcript", lambda *args, **kwargs: called.append(("transcript", kwargs["detailed"])) or (0, False))
    assert cli._watch_run(tmp_path, "run", show_logs=logs) == (0, False)
    assert called == ([("board",)] if expected == "board" else [("transcript", logs)])


def test_attach_logs_dispatches_without_a_key_or_new_worker(tmp_path, monkeypatch, board_cli):
    meta, _ = running(tmp_path)
    called = []
    monkeypatch.setattr(cli, "_attach", lambda *args, **kwargs: called.append((args, kwargs)) or 7)
    assert cli.main(["attach", meta["id"], "--logs", "-d", str(tmp_path)]) == 7
    assert called == [((tmp_path, meta["id"]), {"show_logs": True})]


def test_logs_transcript_keeps_tool_activity_without_creating_a_live_view(tmp_path, monkeypatch, board_cli):
    meta, folder = running(tmp_path)
    append_event(folder, Event("tool_call", {"name": "read_file", "arguments": {"path": "source.md"}}))
    append_event(folder, Event("tool_result", {"name": "read_file", "result": "Source content from the detailed transcript"}))
    finish(tmp_path, meta)
    monkeypatch.setattr(cli, "_stdin_line", lambda timeout: None)
    assert cli._watch_run(tmp_path, meta["id"], show_logs=True) == (0, True)
    assert not FakeLive.instances
    assert "source.md" in board_cli.getvalue()
    assert "Source content from the detailed transcript" in board_cli.getvalue()


@pytest.mark.parametrize("answer,expected", [
    ({"text": "", "ts": 11}, True),
    ({"text": "keep", "ts": 9}, False),
    ({"text": "keep"}, False),
    ({"text": "keep", "ts": "invalid"}, False),
    (None, False),
])
def test_only_answers_at_least_as_new_as_the_question_are_pending(tmp_path, answer, expected):
    (tmp_path / "answer.json").write_text(json.dumps(answer))
    assert cli._answer_pending(tmp_path, {"ts": 10}) is expected
