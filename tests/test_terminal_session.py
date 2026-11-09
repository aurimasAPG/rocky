"""Terminal session contracts using saved runs and no provider calls."""

from io import StringIO
from itertools import count
import sys

import pytest
from rich.console import Console

from rocky import cli
from rocky.events import Event
from rocky.runs import append_event, create_run, read_meta, resolve_run_id, run_dir, write_meta


def forbidden(*args, **kwargs):
    pytest.fail("Unexpected input, model call, or worker launch")


def set_tty(monkeypatch, stdin=True, stdout=True):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: stdin)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: stdout)


@pytest.fixture(autouse=True)
def isolated_cli(monkeypatch, tmp_path):
    output = StringIO()
    monkeypatch.setattr(cli, "console", Console(file=output, width=120, color_system=None))
    monkeypatch.setattr(cli, "apply_config", lambda _: {})
    monkeypatch.setattr(cli, "api_key", lambda: "test-key")
    monkeypatch.setattr(cli, "_onboard", forbidden)
    monkeypatch.setattr(cli, "OpenRouterClient", forbidden)
    monkeypatch.setattr(cli, "spawn_worker", forbidden)
    monkeypatch.setattr("builtins.input", forbidden)
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    identifiers = count(1)
    monkeypatch.setattr("rocky.runs.new_run_id", lambda: f"20261109-120000-{next(identifiers):06d}")
    set_tty(monkeypatch)
    return output


@pytest.fixture
def handoff():
    from rocky import handoff

    return handoff


def finish_run(workspace, meta, *, status="passed", planned=True):
    folder = run_dir(workspace, meta["id"])
    if planned:
        append_event(folder, Event("assessed", {"goal": meta["goal"], "complexity": "trivial"}))
        append_event(folder, Event("plan", {
            "summary": "Write a note", "deliverable_path": "deliverables/note.md",
            "steps": [{"id": 1, "title": "Write", "detail": "Save the note", "writes": ["deliverables/note.md"]}],
        }))
        target = workspace / "deliverables/note.md"
        target.parent.mkdir(exist_ok=True)
        target.write_text("A saved note.\n", encoding="utf-8")
    passed = status in {"passed", "passed_with_notes"}
    # Worker failure events may lack status; persisted metadata is authoritative.
    append_event(folder, Event("done", {"passed": passed, "summary": "Saved task outcome", "eur": 0.03}))
    meta = dict(meta, status=status, passed=passed, pid=None, summary="Saved task outcome")
    if planned:
        meta.update(deliverable_path="deliverables/note.md", deliverable_exists=True, files=["deliverables/note.md"])
    write_meta(folder, meta)
    return meta


def saved_run(workspace, *, status="passed", planned=True, goal="Write a note", budget=1.75, loops=7, tools=False):
    meta = create_run(workspace=workspace, goal=goal, budget_eur=budget, max_loops=loops, include_server_tools=tools)
    return finish_run(workspace, meta, status=status, planned=planned)


def snapshot(workspace):
    return {str(path.relative_to(workspace)): path.read_bytes() for path in workspace.rglob("*") if path.is_file()}


def test_two_followups_stay_linked_to_the_reviewed_run_despite_newer_unrelated_work(tmp_path, monkeypatch, handoff):
    first = saved_run(tmp_path)
    unrelated = saved_run(tmp_path, goal="Unrelated work")
    assert resolve_run_id(tmp_path, None) == unrelated["id"]
    spawned, watched, reviewed, depths = [], [], [], []
    actions = iter([
        handoff.HandoffAction("refine", follow_up="Make it shorter", budget_eur=0.4),
        handoff.HandoffAction("refine", follow_up="Add a warmer ending", budget_eur=0.25),
        None,
    ])

    def watch(workspace, run_id):
        watched.append(run_id)
        return 0, True

    def choose(console, workspace, result, **kwargs):
        reviewed.append(result["run_id"])
        assert kwargs["can_resume"] is True
        frame, depth = sys._getframe(), 0
        while frame is not None:
            depth += frame.f_code is cli._task_session.__code__
            frame = frame.f_back
        depths.append(depth)
        return next(actions)

    def spawn(meta):
        spawned.append(dict(meta))
        finish_run(tmp_path, meta)

    monkeypatch.setattr(cli, "_watch_run", watch)
    monkeypatch.setattr(cli, "spawn_worker", spawn)
    monkeypatch.setattr(handoff, "choose_action", choose)
    assert cli._task_session(tmp_path, first["id"]) == 0
    assert reviewed == watched == [first["id"], spawned[0]["id"], spawned[1]["id"]]
    assert [meta["resume_from"] for meta in spawned] == [first["id"], spawned[0]["id"]]
    assert [meta["follow_up"] for meta in spawned] == ["Make it shorter", "Add a warmer ending"]
    assert [meta["budget_eur"] for meta in spawned] == [0.4, 0.25]
    assert all(meta["max_loops"] == 7 and meta["include_server_tools"] is False for meta in spawned)
    assert depths == [1, 1, 1], "Successive reviews must not recursively grow the session stack"


@pytest.mark.parametrize("planned", [False, True])
def test_recovery_offers_restart_before_a_plan_and_resume_after_a_saved_plan(tmp_path, monkeypatch, handoff, planned):
    failed = saved_run(tmp_path, status="error", planned=planned)
    calls, spawned = [], []

    def choose(console, workspace, result, *, can_resume, **kwargs):
        calls.append((result["run_id"], can_resume))
        if len(calls) > 1:
            return None
        return handoff.HandoffAction("continue" if planned else "restart", budget_eur=0.6)

    def spawn(meta):
        spawned.append(dict(meta))
        finish_run(tmp_path, meta)

    monkeypatch.setattr(handoff, "choose_action", choose)
    monkeypatch.setattr(cli, "spawn_worker", spawn)
    monkeypatch.setattr(cli, "_watch_run", lambda workspace, run_id: (0, True))
    assert cli._task_session(tmp_path, failed["id"], review=True) == 0
    assert calls[0] == (failed["id"], planned)
    assert len(spawned) == 1
    new = spawned[0]
    assert new["goal"] == failed["goal"]
    assert new["budget_eur"] == 0.6
    assert new["max_loops"] == failed["max_loops"]
    assert new["include_server_tools"] is failed["include_server_tools"]
    assert new.get("resume_from") == (failed["id"] if planned else None)
    assert read_meta(run_dir(tmp_path, failed["id"]))["status"] == "error"


@pytest.mark.parametrize("tools", [False, True])
@pytest.mark.parametrize("override", [None, 0.2])
def test_linked_resume_keeps_the_original_limits_unless_the_budget_is_changed(tmp_path, tools, override):
    failed = saved_run(tmp_path, status="stopped", tools=tools, budget=2.75, loops=6)
    new, state = cli._create_resume(tmp_path, failed["id"], budget_eur=override)
    assert new["resume_from"] == state["from"] == failed["id"]
    assert new["budget_eur"] == (2.75 if override is None else override)
    assert new["max_loops"] == 6
    assert new["include_server_tools"] is tools
    assert read_meta(run_dir(tmp_path, new["id"])) == new


def test_detaching_returns_without_opening_the_review_desk(tmp_path, monkeypatch, handoff):
    meta = saved_run(tmp_path)
    monkeypatch.setattr(cli, "_watch_run", lambda workspace, run_id: (0, False))
    monkeypatch.setattr(handoff, "choose_action", forbidden)
    monkeypatch.setattr(handoff, "render_result", forbidden)
    assert cli._task_session(tmp_path, meta["id"]) == 0


def test_explicit_attach_stays_a_viewer_even_on_a_terminal(tmp_path, monkeypatch, handoff):
    failed = saved_run(tmp_path, status="error")
    watched = []
    monkeypatch.setattr(cli, "api_key", forbidden)
    monkeypatch.setattr(cli, "_watch_run", lambda workspace, run_id: watched.append(run_id) or (1, True))
    monkeypatch.setattr(handoff, "choose_action", forbidden)
    assert cli.main(["attach", failed["id"], "-d", str(tmp_path)]) == 1
    assert watched == [failed["id"]]
    assert cli._attach(tmp_path, failed["id"]) == 1


@pytest.mark.parametrize("status, expected", [("passed", 0), ("passed_with_notes", 0), ("error", 1), ("stopped", 1)])
def test_watch_reports_completion_separately_from_its_exit_status(tmp_path, monkeypatch, status, expected):
    meta = saved_run(tmp_path, status=status)
    set_tty(monkeypatch, False, False)
    monkeypatch.setattr(cli, "_stdin_line", lambda timeout: None)
    assert cli._watch_run(tmp_path, meta["id"]) == (expected, True)


@pytest.mark.parametrize("stdin, stdout", [(False, False), (False, True), (True, False)])
def test_review_without_both_terminals_is_read_only_and_needs_no_key(tmp_path, monkeypatch, handoff, stdin, stdout):
    meta = saved_run(tmp_path)
    before = snapshot(tmp_path)
    set_tty(monkeypatch, stdin, stdout)
    rendered = []
    monkeypatch.setattr(cli, "api_key", forbidden)
    monkeypatch.setattr(cli, "_watch_run", forbidden)
    monkeypatch.setattr(handoff, "choose_action", forbidden)
    monkeypatch.setattr(handoff, "render_result", lambda console, workspace, result: rendered.append(result))
    assert cli.main(["review", meta["id"], "-d", str(tmp_path)]) == 0
    assert [result["run_id"] for result in rendered] == [meta["id"]]
    assert snapshot(tmp_path) == before


def test_interactive_review_can_finish_without_checking_for_a_key(tmp_path, monkeypatch, handoff):
    meta = saved_run(tmp_path)
    monkeypatch.setattr(cli, "api_key", forbidden)
    monkeypatch.setattr(cli, "_watch_run", forbidden)
    monkeypatch.setattr(handoff, "choose_action", lambda *args, **kwargs: None)
    assert cli.main(["review", meta["id"], "-d", str(tmp_path)]) == 0


def test_review_preserves_saved_verification_after_a_sparse_done_event(tmp_path, monkeypatch, handoff):
    meta = saved_run(tmp_path, status="unmet")
    folder = run_dir(tmp_path, meta["id"])
    verdict = {
        "passed": False,
        "summary": "The result needs corrections.",
        "blocking": ["Required table missing"],
        "issues": ["Source citation missing"],
        "remaining": ["Write the conclusion"],
    }
    append_event(folder, Event("verify", verdict))
    append_event(folder, Event("done", {"passed": False, "status": "unmet"}))
    reviewed = []
    monkeypatch.setattr(cli, "api_key", forbidden)
    monkeypatch.setattr(cli, "_watch_run", forbidden)
    monkeypatch.setattr(handoff, "choose_action", lambda console, workspace, result, **kwargs: reviewed.append(result) or None)

    assert cli.main(["review", meta["id"], "-d", str(tmp_path)]) == 1
    assert len(reviewed) == 1
    for field in ("blocking", "issues", "remaining"):
        assert reviewed[0][field] == verdict[field]


def test_a_paid_choice_without_a_key_keeps_reviewing_without_creating_or_spawning(tmp_path, monkeypatch, handoff, isolated_cli):
    meta = saved_run(tmp_path)
    before = snapshot(tmp_path)
    actions = iter([handoff.HandoffAction("refine", follow_up="Shorter", budget_eur=0.2), None])
    reviewed = []

    def choose(console, workspace, result, **kwargs):
        reviewed.append(result["run_id"])
        return next(actions)

    monkeypatch.setattr(cli, "api_key", lambda: "")
    monkeypatch.setattr(cli, "_watch_run", forbidden)
    monkeypatch.setattr(handoff, "choose_action", choose)
    assert cli.main(["review", meta["id"], "-d", str(tmp_path)]) == 0
    assert reviewed == [meta["id"], meta["id"]]
    assert "API key" in isolated_cli.getvalue()
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("status", ["unmet", "error", "crashed", "stopped"])
def test_leaving_a_failed_result_preserves_its_nonzero_status(tmp_path, monkeypatch, handoff, status):
    meta = saved_run(tmp_path, status=status)
    seen = []
    monkeypatch.setattr(handoff, "choose_action", lambda console, workspace, result, **kwargs: seen.append(result) or None)
    assert cli._task_session(tmp_path, meta["id"], review=True) == 1
    assert seen[0]["status"] == status
    assert read_meta(run_dir(tmp_path, meta["id"]))["status"] == status


def fake_main_client(monkeypatch):
    closed = []

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def close(self):
            closed.append(True)

    monkeypatch.setattr(cli, "OpenRouterClient", Client)
    monkeypatch.setattr("rocky.banner.print_banner", lambda **kwargs: None)
    return closed


@pytest.mark.parametrize("stdin, stdout", [(True, True), (True, False), (False, True), (False, False)])
def test_automatic_handoff_requires_no_goal_and_both_terminals(tmp_path, monkeypatch, stdin, stdout):
    set_tty(monkeypatch, stdin, stdout)
    closed = fake_main_client(monkeypatch)
    goals = iter(["Write a note", ""])
    calls = []
    monkeypatch.setattr(cli, "prompt_goal", lambda: next(goals))
    monkeypatch.setattr(cli, "_run", lambda **kwargs: calls.append(kwargs) or 0)
    assert cli.main(["-d", str(tmp_path)]) == 0
    assert len(calls) == 1
    assert calls[0]["goal"] == "Write a note"
    assert calls[0]["handoff"] is (stdin and stdout)
    assert calls[0]["json_out"] is False
    assert closed == [True]


@pytest.mark.parametrize("json_out", [False, True])
def test_explicit_goal_is_one_shot_even_on_a_terminal(tmp_path, monkeypatch, json_out):
    closed = fake_main_client(monkeypatch)
    calls = []
    monkeypatch.setattr(cli, "prompt_goal", forbidden)
    monkeypatch.setattr(cli, "_run", lambda **kwargs: calls.append(kwargs) or 1)
    args = ["Write a note", "-d", str(tmp_path)] + (["--json"] if json_out else [])
    assert cli.main(args) == 1
    assert len(calls) == 1
    assert calls[0]["handoff"] is False
    assert calls[0]["json_out"] is json_out
    assert closed == [True]


@pytest.mark.parametrize("handoff, json_out, expected", [(False, False, "attach"), (True, False, "desk"), (True, True, "json")])
def test_created_run_routes_to_the_requested_viewer(tmp_path, monkeypatch, handoff, json_out, expected):
    spawned, viewed = [], []
    monkeypatch.setattr(cli, "spawn_worker", lambda meta: spawned.append(dict(meta)))
    monkeypatch.setattr(cli, "_attach", lambda workspace, run_id: viewed.append(("attach", run_id)) or 0)
    monkeypatch.setattr(cli, "_task_session", lambda workspace, run_id: viewed.append(("desk", run_id)) or 0)
    monkeypatch.setattr(cli, "_run_json", lambda workspace, run_id: viewed.append(("json", run_id)) or 0)
    assert cli._run(goal="Write a note", workspace=tmp_path, max_loops=2, budget_eur=0.3,
                    include_server_tools=False, json_out=json_out, handoff=handoff) == 0
    assert len(spawned) == 1
    assert viewed == [(expected, spawned[0]["id"])]
    assert read_meta(run_dir(tmp_path, spawned[0]["id"]))["goal"] == "Write a note"
