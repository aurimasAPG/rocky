
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
    monkeypatch.delenv("ORCHE_WORKER", raising=False)
    assert preferred_worker() is None
    monkeypatch.setenv("ROCKY_WORKER", "deepseek/v4-flash")
    assert preferred_worker() == "deepseek/v4-flash"


def test_rocky_home_defaults_to_dot_rocky(monkeypatch):
    monkeypatch.delenv("ROCKY_HOME", raising=False)
    assert rocky_home() == Path.home() / ".rocky"
