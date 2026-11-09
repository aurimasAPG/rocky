from io import StringIO
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx
from rich.console import Console

from rocky import update


OLD_COMMIT = "1" * 40
NEW_COMMIT = "2" * 40


@pytest.fixture
def output():
    stream = StringIO()
    return stream, Console(file=stream, color_system=None, width=120)


def forbid_install(*args, **kwargs):
    pytest.fail("This update path must not launch an installer")


def prepare_build(monkeypatch, *, installed=OLD_COMMIT, latest=NEW_COMMIT):
    monkeypatch.setattr(update, "installed_build", lambda: {"version": "0.2.0", "commit": installed})
    monkeypatch.setattr(update, "latest_commit", lambda: latest)


def test_matching_github_commit_never_reinstalls(monkeypatch, output):
    prepare_build(monkeypatch, installed=NEW_COMMIT)
    monkeypatch.setattr(update, "install_command", forbid_install)
    monkeypatch.setattr(update.subprocess, "run", forbid_install)
    stream, console = output

    assert update.run_update(console=console) == 0
    assert "0.2.0" in stream.getvalue()
    assert any(word in stream.getvalue().lower() for word in ("up to date", "current", "latest"))


def test_check_only_reports_new_build_without_installing(monkeypatch, output):
    prepare_build(monkeypatch)
    monkeypatch.setattr(update, "install_command", forbid_install)
    monkeypatch.setattr(update.subprocess, "run", forbid_install)
    stream, console = output

    assert update.run_update(check_only=True, console=console) == 0
    assert NEW_COMMIT[:7] in stream.getvalue()
    assert "rocky update" in stream.getvalue()


@pytest.mark.parametrize("installed_commit", [OLD_COMMIT, ""])
def test_new_commit_updates_even_when_version_number_is_unchanged(monkeypatch, output, installed_commit):
    prepare_build(monkeypatch, installed=installed_commit)
    command = ["/manager with spaces/uv", "tool", "install", "rocky @ git+https://example.invalid/repo@" + NEW_COMMIT]
    environment = {"UV_TOOL_DIR": "/isolated/tools", "UV_TOOL_BIN_DIR": "/isolated/bin"}
    selected = []
    installed = []
    verified = []

    def plan(commit):
        selected.append(commit)
        return command, environment

    def install(argv, **kwargs):
        installed.append((argv, kwargs))
        assert Path(kwargs["cwd"]).is_dir()
        assert Path(kwargs["cwd"]).resolve() != Path.cwd().resolve()
        assert kwargs["env"] == environment
        assert not kwargs.get("shell", False)
        return SimpleNamespace(returncode=0)

    def verify(commit):
        assert installed, "Success must be checked after the installer has completed"
        verified.append(commit)
        return "0.2.0"

    monkeypatch.setattr(update, "install_command", plan)
    monkeypatch.setattr(update.subprocess, "run", install)
    monkeypatch.setattr(update, "verify_installation", verify)
    stream, console = output

    assert update.run_update(console=console) == 0
    assert selected == [NEW_COMMIT]
    assert len(installed) == 1 and installed[0][0] == command
    assert verified == [NEW_COMMIT]
    assert "0.2.0" in stream.getvalue()
    assert NEW_COMMIT[:7] in stream.getvalue()


def test_network_failure_does_not_install_or_claim_success(monkeypatch, output):
    prepare_build(monkeypatch)

    def unavailable():
        raise update.UpdateError("GitHub could not be reached")

    monkeypatch.setattr(update, "latest_commit", unavailable)
    monkeypatch.setattr(update, "install_command", forbid_install)
    monkeypatch.setattr(update.subprocess, "run", forbid_install)
    stream, console = output

    assert update.run_update(console=console) != 0
    assert "GitHub could not be reached" in stream.getvalue()
    assert "updated to" not in stream.getvalue().lower()


def test_invalid_installation_does_not_attempt_network_or_installer(monkeypatch, output):
    def unavailable():
        raise update.UpdateError("Rocky is running from a source checkout")

    monkeypatch.setattr(update, "installed_build", unavailable)
    monkeypatch.setattr(update, "latest_commit", forbid_install)
    monkeypatch.setattr(update, "install_command", forbid_install)
    stream, console = output

    assert update.run_update(console=console) != 0
    assert "source checkout" in stream.getvalue()


def test_installer_failure_is_not_reported_as_updated(monkeypatch, output):
    prepare_build(monkeypatch)
    monkeypatch.setattr(update, "install_command", lambda commit: (["installer"], {}))
    monkeypatch.setattr(update.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=2))
    monkeypatch.setattr(update, "verify_installation", forbid_install)
    stream, console = output

    assert update.run_update(console=console) != 0
    assert "updated to" not in stream.getvalue().lower()
    assert "did not complete" in stream.getvalue().lower()


def test_verification_failure_is_not_reported_as_updated(monkeypatch, output):
    prepare_build(monkeypatch)
    monkeypatch.setattr(update, "install_command", lambda commit: (["installer"], {}))
    monkeypatch.setattr(update.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))

    def failed_verification(commit):
        raise update.UpdateError("Installed build did not match the GitHub commit")

    monkeypatch.setattr(update, "verify_installation", failed_verification)
    stream, console = output

    assert update.run_update(console=console) != 0
    assert "did not match" in stream.getvalue()
    assert "updated to" not in stream.getvalue().lower()


@pytest.mark.parametrize("payload", [{}, [], {"sha": "not-a-commit"}, {"sha": NEW_COMMIT + ";command"}])
def test_github_response_must_contain_a_full_commit_sha(monkeypatch, payload):
    response = httpx.Response(200, json=payload, request=httpx.Request("GET", update.COMMIT_API))
    monkeypatch.setattr(update.httpx, "get", lambda *args, **kwargs: response)

    with pytest.raises(update.UpdateError, match="GitHub"):
        update.latest_commit()


def test_github_lookup_uses_official_main_and_a_bounded_timeout(monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, json={"sha": "A" * 40}, request=httpx.Request("GET", url))

    monkeypatch.setattr(update.httpx, "get", get)
    assert update.latest_commit() == "a" * 40
    assert calls[0][0] == "https://api.github.com/repos/aurimasAPG/rocky/commits/main"
    assert 0 < calls[0][1]["timeout"] <= 30


def test_github_http_failure_has_a_retry_message(monkeypatch):
    response = httpx.Response(429, request=httpx.Request("GET", update.COMMIT_API))
    monkeypatch.setattr(update.httpx, "get", lambda *args, **kwargs: response)

    with pytest.raises(update.UpdateError, match="retry rocky update"):
        update.latest_commit()


def fake_distribution(monkeypatch, tmp_path, source):
    package_file = tmp_path / "site-packages" / "rocky" / "__init__.py"
    monkeypatch.setattr(update, "PACKAGE_FILE", str(package_file))
    package = SimpleNamespace(
        version="0.2.0",
        locate_file=lambda relative: tmp_path / "site-packages" / relative,
        read_text=lambda name: source,
    )
    monkeypatch.setattr(update, "distribution", lambda name: package)


def test_local_install_has_no_claimed_github_commit(monkeypatch, tmp_path):
    fake_distribution(monkeypatch, tmp_path, json.dumps({"url": "file:///a/local/checkout", "dir_info": {}}))
    assert update.installed_build() == {"version": "0.2.0", "commit": ""}


@pytest.mark.parametrize("source", ["not json", "[]", '{"dir_info": []}'])
def test_malformed_install_metadata_is_reported(monkeypatch, tmp_path, source):
    fake_distribution(monkeypatch, tmp_path, source)
    with pytest.raises(update.UpdateError, match="metadata"):
        update.installed_build()


def test_editable_install_cannot_be_replaced_as_a_regular_package(monkeypatch, tmp_path):
    fake_distribution(monkeypatch, tmp_path, json.dumps({"url": "file:///checkout", "dir_info": {"editable": True}}))
    with pytest.raises(update.UpdateError, match="editable"):
        update.installed_build()


def test_import_from_source_checkout_does_not_update_another_installed_copy(monkeypatch, tmp_path):
    fake_distribution(monkeypatch, tmp_path, "{}")
    monkeypatch.setattr(update, "PACKAGE_FILE", str(tmp_path / "checkout" / "rocky" / "__init__.py"))
    with pytest.raises(update.UpdateError, match="source checkout"):
        update.installed_build()


def test_fresh_interpreter_verifies_version_and_commit_outside_project(monkeypatch):
    calls = []

    def probe(argv, **kwargs):
        calls.append((argv, kwargs))
        assert argv[:3] == [update.sys.executable, "-I", "-c"]
        assert Path(kwargs["cwd"]).is_dir()
        assert Path(kwargs["cwd"]).resolve() != Path.cwd().resolve()
        assert kwargs["check"] is True
        return SimpleNamespace(stdout=json.dumps({
            "version": "0.2.0", "loaded_version": "0.2.0",
            "source": {"url": update.REPOSITORY, "vcs_info": {"commit_id": NEW_COMMIT}},
        }))

    monkeypatch.setattr(update.subprocess, "run", probe)
    assert update.verify_installation(NEW_COMMIT) == "0.2.0"
    assert len(calls) == 1


@pytest.mark.parametrize("commit,loaded_version,repository", [
    (OLD_COMMIT, "0.2.0", update.REPOSITORY),
    (NEW_COMMIT, "0.1.0", update.REPOSITORY),
    (NEW_COMMIT, "0.2.0", "https://example.invalid/unrelated/rocky"),
])
def test_verification_rejects_stale_code_or_unrelated_install(monkeypatch, commit, loaded_version, repository):
    payload = {
        "version": "0.2.0", "loaded_version": loaded_version,
        "source": {"url": repository, "vcs_info": {"commit_id": commit}},
    }
    monkeypatch.setattr(update.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps(payload)))
    with pytest.raises(update.UpdateError, match="could not be verified"):
        update.verify_installation(NEW_COMMIT)
