from pathlib import Path

from rocky.config import save_api_key
from rocky.doctor import FAIL, OK, WARN, healthy, run_checks


def test_save_api_key_creates_replaces_and_locks_down(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    env = tmp_path / ".env"
    out = save_api_key("sk-or-new", env)
    assert out == env
    assert env.read_text() == "OPENROUTER_API_KEY=sk-or-new\n"
    assert (env.stat().st_mode & 0o777) == 0o600
    import os

    assert os.environ["OPENROUTER_API_KEY"] == "sk-or-new"
    env.write_text("FOO=1\nOPENROUTER_API_KEY=sk-or-old\nBAR=2\n")
    save_api_key("sk-or-newer", env)
    assert env.read_text() == "FOO=1\nOPENROUTER_API_KEY=sk-or-newer\nBAR=2\n"


def _by_name(checks):
    return {c.name: c for c in checks}


def test_doctor_without_key_fails_key_and_openrouter(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ROCKY_SANDBOX", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "home").mkdir()
    checks = _by_name(run_checks(tmp_path, fetch=lambda url, headers: (200, {"data": []})))
    assert checks["api key"].status == FAIL
    assert checks["openrouter"].status == FAIL
    assert checks["workspace"].status == OK
    assert checks["sandbox"].status == WARN and "ROCKY_SANDBOX" in checks["sandbox"].hint
    assert checks["python"].status == OK
    assert not healthy(list(checks.values()))


def test_doctor_with_key_checks_openrouter_and_env_perms(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ROCKY_SANDBOX", raising=False)
    save_api_key("sk-or-abcdefghijklmnop", home / ".env")
    seen = {}

    def fetch(url, headers):
        seen["url"] = url
        seen["auth"] = headers["Authorization"]
        return 200, {"data": [{"id": "a"}, {"id": "b"}]}

    checks = _by_name(run_checks(tmp_path, fetch=fetch))
    assert checks["api key"].status == OK and "sk-or-abc" in checks["api key"].detail
    assert "abcdefghijklmnop" not in checks["api key"].detail, "key must be masked"
    assert checks["openrouter"].status == OK and "2 models" in checks["openrouter"].detail
    assert seen["url"].endswith("/models") and seen["auth"] == "Bearer sk-or-abcdefghijklmnop"
    assert checks["~/.env perms"].status == OK
    (home / ".env").chmod(0o644)
    checks = _by_name(run_checks(tmp_path, fetch=fetch))
    assert checks["~/.env perms"].status == WARN and "chmod 600" in checks["~/.env perms"].hint
    rejected = _by_name(run_checks(tmp_path, fetch=lambda u, h: (401, {})))
    assert rejected["openrouter"].status == FAIL and "401" in rejected["openrouter"].detail
    down = _by_name(run_checks(tmp_path, fetch=lambda u, h: (_ for _ in ()).throw(ConnectionError("no net"))))
    assert down["openrouter"].status == FAIL and "unreachable" in down["openrouter"].detail


def test_cli_doctor_and_onboarding(tmp_path, monkeypatch, capsys):
    from rocky.cli import main

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("rocky.doctor._openrouter", lambda fetch=None: __import__("rocky.doctor", fromlist=["Check"]).Check("openrouter", OK, "stub"))
    assert main(["doctor", "-d", str(tmp_path)]) == 1  # no key → ✗
    out = capsys.readouterr().out
    assert "api key" in out and "not ready" in out

    # onboarding: a terminal, the user pastes a key, it lands in ~/.env, the run proceeds
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "sk-or-pasted")
    monkeypatch.setattr("rocky.cli._run", lambda **kw: 0)
    monkeypatch.setattr("rocky.banner.print_banner", lambda **kw: None)

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def close(self):
            pass

    monkeypatch.setattr("rocky.cli.OpenRouterClient", FakeClient)
    assert main(["write x.md", "-d", str(tmp_path)]) == 0
    assert (home / ".env").read_text() == "OPENROUTER_API_KEY=sk-or-pasted\n"
    out = capsys.readouterr().out
    assert "saved" in out and "Įrašyta" in out
    assert "api key" in out  # doctor ran after saving


def test_cli_no_key_non_tty_still_errors(tmp_path, monkeypatch, capsys):
    from rocky.cli import main

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["write x.md", "-d", str(tmp_path)]) == 2
    assert "rocky doctor" in capsys.readouterr().out


def test_doctor_checks_the_pinned_worker_against_the_catalog(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ROCKY_SANDBOX", raising=False)
    save_api_key("sk-or-abcdefghijklmnop", home / ".env")
    catalog = {
        "data": [
            {"id": "google/gemini-3.8-flash", "pricing": {"prompt": "0.0000001", "completion": "0.0000004"},
             "context_length": 1000000, "supported_parameters": ["tools"], "architecture": {"output_modalities": ["text"]}},
            {"id": "openai/gpt", "pricing": {"prompt": "0.00001", "completion": "0.00003"},
             "context_length": 200000, "supported_parameters": ["tools"], "architecture": {"output_modalities": ["text"]}},
        ]
    }
    fetch = lambda u, h: (200, catalog)
    monkeypatch.delenv("ROCKY_WORKER", raising=False)
    ok = _by_name(run_checks(tmp_path, fetch=fetch))["models"]
    assert ok.status == OK and "google/gemini-3.8-flash" in ok.detail and "default" in ok.detail
    monkeypatch.setenv("ROCKY_WORKER", "acme/nonexistent")
    warn = _by_name(run_checks(tmp_path, fetch=fetch))["models"]
    assert warn.status == WARN and "acme/nonexistent" in warn.detail and "fall back" in warn.detail
    monkeypatch.setenv("ROCKY_WORKER", "openai/gpt")
    assert _by_name(run_checks(tmp_path, fetch=fetch))["models"].status == OK
    unchecked = _by_name(run_checks(tmp_path, fetch=lambda u, h: (401, {})))["models"]
    assert unchecked.status == OK and "not checked" in unchecked.detail
