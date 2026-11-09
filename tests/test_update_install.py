import json
from pathlib import Path

import pytest

from rocky import update_install
from rocky.update_install import UpdateInstallError, install_command


COMMIT = "a" * 40
SOURCE = f"git+https://github.com/aurimasAPG/rocky@{COMMIT}"


@pytest.fixture
def uv_install(tmp_path, monkeypatch):
    prefix = tmp_path / "custom tools" / "rocky"
    prefix.mkdir(parents=True)
    command = tmp_path / "custom bin" / "rocky"
    command.parent.mkdir()
    (prefix / "bin").mkdir()
    inner_command = prefix / "bin" / "rocky"
    inner_command.write_text("#!/python\n")
    command.symlink_to(inner_command)
    receipt = prefix / "uv-receipt.toml"
    receipt.write_text(
        '[tool]\n'
        'requirements = [{name="rocky", extras=["browser"], directory="/source/checkout"}, {name="rich", specifier="==15.0.0"}]\n'
        'python="3.13"\n'
        f'entrypoints=[{{name="rocky", install-path={json.dumps(str(command))}, from="rocky"}}]\n'
    )
    monkeypatch.setattr(update_install.sys, "prefix", str(prefix))
    monkeypatch.setattr(update_install.sys, "_base_executable", str(tmp_path / "base python"))
    monkeypatch.setattr(update_install.shutil, "which", lambda name: f"/tools/{name}")
    return prefix, command, receipt


def test_uv_preserves_extras_dependencies_python_and_custom_directories(uv_install, monkeypatch):
    prefix, executable, _ = uv_install
    monkeypatch.setenv("UV_TOOL_DIR", "/wrong/tools")
    monkeypatch.setenv("UV_TOOL_BIN_DIR", "/wrong/bin")
    command, env = install_command(COMMIT)
    assert command == [
        "/tools/uv", "tool", "install", "--force", "--reinstall-package", "rocky", "--python",
        str(prefix.parent.parent / "base python"), "--with", "rich==15.0.0",
        f"rocky[browser] @ {SOURCE}",
    ]
    assert env["UV_TOOL_DIR"] == str(prefix.parent)
    assert env["UV_TOOL_BIN_DIR"] == str(executable.parent)
    assert "/source/checkout" not in " ".join(command)


def test_uv_preserves_additional_requirement_extras_and_marker(uv_install):
    _, _, receipt = uv_install
    text = receipt.read_text().replace(
        '{name="rich", specifier="==15.0.0"}',
        '{name="httpx", extras=["http2"], specifier=">=0.27", marker="python_version >= \'3.11\'"}',
    )
    receipt.write_text(text)
    command, _ = install_command(COMMIT)
    assert command[command.index("--with") + 1] == "httpx[http2]>=0.27; python_version >= '3.11'"


@pytest.mark.parametrize("replacement", [
    ('python="3.13"', 'python="3.13"\nconstraints=["rich<15"]'),
    ('directory="/source/checkout"', 'directory="/source/checkout", editable=true'),
    ('specifier="==15.0.0"', 'git="https://example.invalid/rich"'),
    ('name="rocky", install-path=', 'name="unrelated", install-path='),
])
def test_uv_refuses_settings_it_cannot_preserve(uv_install, replacement):
    _, _, receipt = uv_install
    receipt.write_text(receipt.read_text().replace(*replacement))
    with pytest.raises(UpdateInstallError):
        install_command(COMMIT)


def test_uv_refuses_custom_resolver_options(uv_install):
    _, _, receipt = uv_install
    receipt.write_text(receipt.read_text() + '\n[tool.options]\nprerelease="allow"\n')
    with pytest.raises(UpdateInstallError, match="custom settings"):
        install_command(COMMIT)


def test_uv_does_not_overwrite_command_owned_by_another_install(uv_install, tmp_path):
    _, command, _ = uv_install
    command.unlink()
    command.write_text("Another installed tool")
    with pytest.raises(UpdateInstallError, match="no longer belongs"):
        install_command(COMMIT)


def test_invalid_receipt_has_actionable_error(uv_install):
    _, _, receipt = uv_install
    receipt.write_text("[broken")
    with pytest.raises(UpdateInstallError, match="installation receipt"):
        install_command(COMMIT)


def test_missing_manager_never_switches_to_another_installer(uv_install, monkeypatch, tmp_path):
    monkeypatch.setattr(update_install.shutil, "which", lambda _: None)
    monkeypatch.setattr(update_install.Path, "home", lambda: tmp_path)
    with pytest.raises(UpdateInstallError, match="uv manages this Rocky installation"):
        install_command(COMMIT)


def test_nonmanaged_environment_does_not_change_global_installation(tmp_path, monkeypatch):
    monkeypatch.setattr(update_install.sys, "prefix", str(tmp_path))
    with pytest.raises(UpdateInstallError, match="supports uv and pipx"):
        install_command(COMMIT)


@pytest.mark.parametrize("commit", ["main", "a" * 39, "z" * 40, "--force", "a" * 40 + "\n"])
def test_invalid_commit_never_reaches_manager(commit):
    with pytest.raises(UpdateInstallError, match="commit identifier"):
        install_command(commit)


@pytest.fixture
def pipx_install(tmp_path, monkeypatch):
    prefix = tmp_path / "custom pipx" / "venvs" / "rocky"
    (prefix / "bin").mkdir(parents=True)
    inner_command = prefix / "bin" / "rocky"
    inner_command.write_text("#!/python\n")
    command = tmp_path / "custom bin" / "rocky"
    command.parent.mkdir()
    command.symlink_to(inner_command)
    metadata = {
        "main_package": {
            "package": "rocky", "package_or_url": "rocky[browser] @ git+https://github.com/aurimasAPG/rocky",
            "pip_args": [], "include_dependencies": True,
        },
        "venv_args": [], "injected_packages": {},
    }
    receipt = prefix / "pipx_metadata.json"
    receipt.write_text(json.dumps(metadata))
    monkeypatch.setattr(update_install.sys, "prefix", str(prefix))
    monkeypatch.setattr(update_install.sys, "argv", [str(command), "update"])
    monkeypatch.setattr(update_install.shutil, "which", lambda name: "/tools/pipx" if name == "pipx" else None)
    return prefix, command, receipt, metadata


def test_pipx_updates_same_environment_preserving_extras_and_python(pipx_install, monkeypatch):
    prefix, executable, _, _ = pipx_install
    monkeypatch.setenv("PIPX_HOME", "/wrong/pipx")
    monkeypatch.setenv("PIPX_BIN_DIR", "/wrong/bin")
    command, env = install_command(COMMIT)
    assert command == ["/tools/pipx", "install", "--force", "--include-deps", f"rocky[browser] @ {SOURCE}"]
    # pipx --force installs into the existing venv and retains its Python.
    assert "--python" not in command
    assert env["PIPX_HOME"] == str(prefix.parent.parent)
    assert env["PIPX_BIN_DIR"] == str(executable.parent)


@pytest.mark.parametrize("original", [
    "git+https://github.com/aurimasAPG/rocky#egg=rocky[browser]",
    "/source/rocky[browser]",
])
def test_pipx_preserves_extras_from_legacy_and_local_sources(pipx_install, original):
    _, _, receipt, metadata = pipx_install
    metadata["main_package"]["package_or_url"] = original
    receipt.write_text(json.dumps(metadata))
    command, _ = install_command(COMMIT)
    assert command[-1] == f"rocky[browser] @ {SOURCE}"


def test_pipx_does_not_overwrite_a_different_rocky_on_path(pipx_install, monkeypatch, tmp_path):
    other = tmp_path / "other" / "rocky"
    other.parent.mkdir()
    other.write_text("#!/python\n")
    monkeypatch.setattr(update_install.sys, "argv", [str(other), "update"])
    monkeypatch.setattr(update_install.shutil, "which", lambda name: "/tools/pipx" if name == "pipx" else str(other))
    with pytest.raises(UpdateInstallError, match="command directory"):
        install_command(COMMIT)


def test_pipx_refuses_custom_pip_arguments(pipx_install):
    _, _, receipt, metadata = pipx_install
    metadata["main_package"]["pip_args"] = ["--index-url", "https://example.invalid/simple"]
    receipt.write_text(json.dumps(metadata))
    with pytest.raises(UpdateInstallError, match="custom settings"):
        install_command(COMMIT)


def test_pipx_refuses_to_lose_injected_package_constraints(pipx_install):
    _, _, receipt, metadata = pipx_install
    metadata["injected_packages"] = {"rich": {"package_or_url": "rich==14.0.0"}}
    receipt.write_text(json.dumps(metadata))
    with pytest.raises(UpdateInstallError, match="custom settings"):
        install_command(COMMIT)


@pytest.mark.parametrize("arguments", [None, "--force-reinstall", {"flag": "bad"}, [7]])
def test_pipx_malformed_arguments_have_clear_error(pipx_install, arguments):
    _, _, receipt, metadata = pipx_install
    metadata["main_package"]["pip_args"] = arguments
    receipt.write_text(json.dumps(metadata))
    with pytest.raises(UpdateInstallError, match="installer arguments are invalid"):
        install_command(COMMIT)
