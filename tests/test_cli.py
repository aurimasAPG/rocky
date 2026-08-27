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
    assert "not a sandbox" in out
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
