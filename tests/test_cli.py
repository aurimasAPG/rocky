import pytest

from io import StringIO

from rich.console import Console

from rocky.banner import print_banner, tui_splash
from rocky.cli import main, prompt_goal


def test_banner_prints_retriever_and_rocky():
    buf = StringIO()
    print_banner(
        workspace="/tmp/demo",
        budget_eur=1.0,
        interactive=True,
        console=Console(file=buf, force_terminal=True, color_system=None, width=80),
    )
    text = buf.getvalue()
    assert "r o c k y" in text.lower()
    assert "retriever" in text.lower()
    assert "██" not in text
    assert "golden retriever" in text
    assert "Type a task" in text
    assert tui_splash().count("∩") >= 1


def test_help_exits_zero():
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_help_mentions_live_status_line(capsys):
    assert main(["help"]) == 0
    out = capsys.readouterr().out
    assert "thinking · plan" in out
    assert "same row" in out


def test_rocky_help_prints_english_guide(capsys):
    assert main(["help"]) == 0
    out = capsys.readouterr().out
    assert "Rocky — how to use it" in out
    assert "Detach and reattach" in out
    assert "OPENROUTER_API_KEY" in out
    assert "ROCKY_SANDBOX" in out
    assert "ROCKY_TOKEN" in out
    assert "/Users/aurimas" not in out
    assert "uv tool install --force --reinstall ." in out
    assert "MEMORY.md" in out
    assert "runs_search" in out


def test_prompt_goal_returns_typed_text(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "  parašyk santrauką  ")
    assert prompt_goal() == "parašyk santrauką"


def test_prompt_goal_quit(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "q")
    assert prompt_goal() == ""


def test_interactive_session_runs_typed_goal(monkeypatch):
    answers = iter(["write notes.md", ""])
    monkeypatch.setattr("rocky.cli.prompt_goal", lambda: next(answers))
    monkeypatch.setattr("rocky.cli.api_key", lambda: "sk-test")
    called = {}

    def fake_run(**kwargs):
        called["goal"] = kwargs["goal"]
        return 0

    monkeypatch.setattr("rocky.cli._run", fake_run)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def close(self):
            pass

    monkeypatch.setattr("rocky.cli.OpenRouterClient", FakeClient)
    assert main([]) == 0
    assert called["goal"] == "write notes.md"


def test_json_mode_prints_one_object_and_answers_questions(tmp_path, monkeypatch, capsys):
    import json

    from rocky.events import Event
    from rocky.runs import append_event, read_meta, run_dir, take_answer, write_ask, write_meta

    monkeypatch.setattr("rocky.cli.api_key", lambda: "sk-test")

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def close(self):
            pass

    monkeypatch.setattr("rocky.cli.OpenRouterClient", FakeClient)

    def fake_spawn(meta):
        folder = run_dir(meta["workspace"], meta["id"])
        append_event(folder, Event(type="run_started", payload={"goal": meta["goal"]}))
        append_event(folder, Event(type="cast", payload={"models": {"worker": "google/flash"}}))
        append_event(folder, Event(type="plan", payload={"deliverable_path": "out.md", "steps": []}))
        write_ask(folder, "Raise budget?", ["yes", "keep"])
        meta["status"] = "waiting"
        meta["pid"] = __import__("os").getpid()  # alive, so refresh_meta does not mark it crashed
        monkeypatch.setattr("rocky.runs._pid_command", lambda pid: None)  # "cannot tell" → trust os.kill
        write_meta(folder, meta)
        return meta["pid"]

    monkeypatch.setattr("rocky.cli.spawn_worker", fake_spawn)

    # Finish the run as soon as the CLI has answered the question.
    original_read_ask = __import__("rocky.cli", fromlist=["read_ask"]).read_ask

    def finishing_read_ask(folder):
        ask = original_read_ask(folder)
        answer = take_answer(folder)
        if answer is not None:
            assert "documented assumptions" in answer
            append_event(folder, Event(type="verify", payload={"passed": True, "score": 0.9, "issues": []}))
            append_event(
                folder,
                Event(type="done", payload={"passed": True, "summary": "ok", "loops": 1, "usd": 0.01, "eur": 0.009, "receipt": "r",
                                            "deliverable_path": "out.md", "deliverable_exists": True, "files": ["out.md"]}),
            )
            meta = read_meta(folder)
            meta["status"] = "passed"
            meta["passed"] = True
            write_meta(folder, meta)
            return None
        return ask

    monkeypatch.setattr("rocky.cli.read_ask", finishing_read_ask)

    code = main(["write out.md", "--json", "-d", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0
    data = json.loads(out)
    assert data["passed"] is True
    assert data["status"] == "passed"
    assert data["deliverable_path"] == "out.md"
    assert data["deliverable_exists"] is True
    assert data["files"] == ["out.md"]
    assert data["models"] == {"worker": "google/flash"}
    assert data["eur"] == 0.009
    assert data["events"].endswith("events.jsonl")
    assert "Goal" not in out  # no rich transcript in JSON mode


def test_json_mode_needs_a_goal(monkeypatch, capsys):
    monkeypatch.setattr("rocky.cli.api_key", lambda: "sk-test")

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def close(self):
            pass

    monkeypatch.setattr("rocky.cli.OpenRouterClient", FakeClient)
    assert main(["--json"]) == 2


def test_version_flag(capsys):
    from rocky import __version__

    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"rocky {__version__}"
    assert main(["version"]) == 0


def test_lithuanian_guide_keeps_parity_with_help():
    from pathlib import Path

    from rocky.cli import help_path

    en = help_path().read_text(encoding="utf-8")
    lt = (Path(__file__).resolve().parent.parent / "docs" / "lt.md").read_text(encoding="utf-8")
    # same section count, same commands, same feature keywords
    import re

    assert len(re.findall(r"\n## \d+\. ", en)) == len(re.findall(r"\n## \d+\. ", lt)) == 18
    for token in (
        "rocky resume", "rocky doctor", "rocky ps", "rocky attach", "rocky serve", "rocky route", "--json",
        "passed_with_notes", "image_generate", "ROCKY_IMAGE_MODEL", "runs/notes/", "deliverables/",
        "ROCKY_SANDBOX", "ROCKY_TOKEN", "MEMORY.md", "runs_search", "recipe:", "subtask_skipped",
        "100k", "cache", "ROCKY_WORKER", "mcp.json", "AGENTS.md", "budget_warn", "ad_board", "deliverables/ads.html",
    ):
        assert token in en, f"{token} missing from HELP.md"
        assert token.replace("100k", "100 tūkst.") in lt, f"{token} missing from docs/lt.md"
    assert "„" in lt and '"citata"' not in lt  # Lithuanian quotes


def test_dash_dash_help_lists_the_subcommands(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for cmd in ("rocky ps", "rocky stop", "rocky resume", "rocky open", "rocky cost", "rocky config", "rocky doctor", "--model", "--lang"):
        assert cmd in out, cmd


def test_tui_input_stays_enabled_while_a_run_streams():
    import inspect

    from rocky import tui

    src = inspect.getsource(tui.RockyTui._watch)
    assert "disabled = True" not in src, "q + Enter must be able to detach during a run"
    assert any(b.key == "ctrl+v" for b in tui.GoalInput.BINDINGS)
