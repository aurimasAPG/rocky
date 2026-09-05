
import pytest
from pathlib import Path

from rocky.config import api_key, preferred_worker, rocky_home


def test_api_key_reads_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-key")
    assert api_key() == "sk-test-key"


def test_rocky_home_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "alt"))
    assert rocky_home() == (tmp_path / "alt").resolve()


def test_preferred_worker_env(monkeypatch):
    monkeypatch.delenv("ROCKY_WORKER", raising=False)
    assert preferred_worker() is None
    monkeypatch.setenv("ROCKY_WORKER", "deepseek/v4-flash")
    assert preferred_worker() == "deepseek/v4-flash"


def test_rocky_home_defaults_to_dot_rocky(monkeypatch):
    monkeypatch.delenv("ROCKY_HOME", raising=False)
    assert rocky_home() == Path.home() / ".rocky"


def test_config_files_merge_and_env_wins(tmp_path, monkeypatch):
    import os

    from rocky.config import apply_config, config_paths, load_config

    home = tmp_path / "home"
    monkeypatch.setenv("ROCKY_HOME", str(home))
    project = tmp_path / "proj"
    (home).mkdir()
    (project / ".rocky").mkdir(parents=True)
    (home / "config.toml").write_text('budget = 3.0\nworker = "deepseek/v4"\nlanguage = "lt"\nworkspaces = ["/a", "/b"]\n')
    (project / ".rocky" / "config.toml").write_text('budget = 0.5\nmax_loops = 2\n')
    assert config_paths(project) == [home / "config.toml", project / ".rocky" / "config.toml"]
    cfg = load_config(project)
    assert cfg == {"budget": 0.5, "worker": "deepseek/v4", "language": "lt", "workspaces": "/a,/b", "max_loops": 2}
    for var in ("ROCKY_WORKER", "ROCKY_LANGUAGE", "ROCKY_WORKSPACES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ROCKY_WORKER", "openai/gpt")
    apply_config(project)
    assert os.environ["ROCKY_WORKER"] == "openai/gpt", "environment beats the file"
    assert os.environ["ROCKY_LANGUAGE"] == "lt" and os.environ["ROCKY_WORKSPACES"] == "/a,/b"
    (home / "config.toml").write_text("budget = [broken\n")
    with pytest.raises(ValueError, match="config.toml"):
        load_config(project)


def test_cli_uses_config_defaults_and_model_lang_flags(tmp_path, monkeypatch, capsys):
    import os

    from rocky.cli import main

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("ROCKY_HOME", str(home))
    (home / "config.toml").write_text("budget = 2.5\nmax_loops = 3\n")
    for var in ("ROCKY_WORKER", "ROCKY_LANGUAGE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("rocky.cli.api_key", lambda: "sk-test")
    seen = {}

    def fake_spawn(meta):
        seen.update(meta)
        seen["env_worker"] = os.environ.get("ROCKY_WORKER")
        seen["env_lang"] = os.environ.get("ROCKY_LANGUAGE")
        return 1

    monkeypatch.setattr("rocky.cli.spawn_worker", fake_spawn)
    monkeypatch.setattr("rocky.cli._attach", lambda workspace, rid: 0)

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def close(self):
            pass

    monkeypatch.setattr("rocky.cli.OpenRouterClient", FakeClient)
    assert main(["write x", "-d", str(tmp_path), "--model", "deepseek/v4", "--lang", "LT"]) == 0
    assert seen["budget_eur"] == 2.5 and seen["max_loops"] == 3
    assert seen["env_worker"] == "deepseek/v4" and seen["env_lang"] == "lt"
    assert main(["write x", "-d", str(tmp_path), "--budget", "9"]) == 0
    assert seen["budget_eur"] == 9.0
    assert main(["config", "-d", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "config.toml" in out and "budget €2.50" in out and "deepseek/v4" in out


def test_forced_language_beats_detection(tmp_path, monkeypatch):
    from rocky.engine import _language_for

    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ROCKY_LANGUAGE", raising=False)
    assert _language_for("Parašyk reklamą", {}) == "lt"
    assert _language_for("Write an ad", {"language": "Lithuanian"}) == "lt"
    monkeypatch.setenv("ROCKY_LANGUAGE", "en")
    assert _language_for("Parašyk reklamą", {}) == "en"
