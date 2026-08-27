import os

from rocky.config import api_key


def test_api_key_reads_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-key")
    assert api_key() == "sk-test-key"
