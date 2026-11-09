"""Build a self-update command for the environment running Rocky."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import sys
import tomllib


class UpdateInstallError(ValueError):
    """The current installation cannot be updated without losing its settings."""


def _name(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
        raise UpdateInstallError("The installation has an invalid package name.")
    return re.sub(r"[-_.]+", "-", value).lower()


def _extras(values: object) -> str:
    if not isinstance(values, list):
        raise UpdateInstallError("The installation has invalid optional-package settings.")
    names = [_name(value) for value in values]
    return f"[{','.join(names)}]" if names else ""


def _manager(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        candidate = Path.home() / ".local" / "bin" / (name + (".exe" if os.name == "nt" else ""))
        if candidate.is_file() and os.access(candidate, os.X_OK):
            executable = str(candidate)
    if not executable:
        raise UpdateInstallError(f"{name} manages this Rocky installation but is not on PATH. Restore {name}, then run rocky update again.")
    return executable


def _uv(prefix: Path, receipt: dict, source: str) -> tuple[list[str], dict[str, str]]:
    tool = receipt.get("tool")
    if not isinstance(tool, dict) or prefix.name != "rocky":
        raise UpdateInstallError("Rocky's uv installation receipt is invalid.")
    if tool.get("options") or set(tool) - {"requirements", "python", "entrypoints", "options"}:
        raise UpdateInstallError("This uv installation has custom settings. Reinstall Rocky with uv and those settings to preserve them.")
    requirements = tool.get("requirements")
    if not isinstance(requirements, list) or not all(isinstance(item, dict) for item in requirements):
        raise UpdateInstallError("Rocky's uv installation requirements are invalid.")
    main = [item for item in requirements if _name(item.get("name")) == "rocky"]
    if len(main) != 1 or main[0].get("editable"):
        raise UpdateInstallError("Self-update requires one noneditable Rocky installation.")
    spec = "rocky" + _extras(main[0].get("extras", [])) + " @ " + source
    additional = []
    for item in requirements:
        if item is main[0]:
            continue
        if set(item) - {"name", "extras", "specifier", "marker"}:
            raise UpdateInstallError("This uv installation has additional packages from custom sources. Reinstall with uv to preserve those packages.")
        name = _name(item.get("name")) + _extras(item.get("extras", []))
        specifier = item.get("specifier", "")
        marker = item.get("marker", "")
        if not isinstance(specifier, str) or not isinstance(marker, str):
            raise UpdateInstallError("An additional uv package requirement is invalid.")
        additional.append(name + specifier + (f"; {marker}" if marker else ""))
    entries = tool.get("entrypoints")
    if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
        raise UpdateInstallError("This uv installation exposes additional commands. Reinstall with uv to preserve them.")
    entry = entries[0]
    if entry.get("name") not in {"rocky", "rocky.exe"} or entry.get("from") != "rocky":
        raise UpdateInstallError("Rocky's uv command location could not be verified.")
    location = entry.get("install-path")
    if not isinstance(location, str) or not Path(location).is_absolute():
        raise UpdateInstallError("Rocky's uv command location is invalid.")
    if Path(location).name not in {"rocky", "rocky.exe"}:
        raise UpdateInstallError("Rocky's uv command location is invalid.")
    if os.name != "nt":
        try:
            owns_command = Path(location).samefile(prefix / "bin" / "rocky")
        except OSError:
            owns_command = False
        if not owns_command:
            raise UpdateInstallError("Rocky's uv command no longer belongs to this installation. Repair it with uv before updating.")
    base_python = str(Path(getattr(sys, "_base_executable", sys.executable)).resolve())
    command = [_manager("uv"), "tool", "install", "--force", "--reinstall-package", "rocky", "--python", base_python]
    for requirement in additional:
        command.extend(["--with", requirement])
    command.append(spec)
    env = os.environ.copy()
    env["UV_TOOL_DIR"] = str(prefix.parent)
    env["UV_TOOL_BIN_DIR"] = str(Path(location).parent)
    return command, env


def _pipx_bin(prefix: Path) -> Path:
    # A PATH lookup alone could identify a different Rocky installation.
    candidates = [sys.argv[0], shutil.which("rocky")]
    for candidate in candidates:
        if candidate:
            path = Path(candidate).absolute()
            if path.name in {"rocky", "rocky.exe"} and path.is_file():
                try:
                    path.resolve().relative_to(prefix)
                except ValueError:
                    continue
                if path.parent not in {prefix / "bin", prefix / "Scripts"}:
                    return path.parent
    raise UpdateInstallError("Cannot verify this pipx installation's command directory. Run its installed rocky command from your terminal, then try rocky update again.")


def _pipx(prefix: Path, receipt: dict, source: str) -> tuple[list[str], dict[str, str]]:
    main = receipt.get("main_package")
    if prefix.parent.name != "venvs" or prefix.name != "rocky" or not isinstance(main, dict) or main.get("package") != "rocky":
        raise UpdateInstallError("Rocky's pipx installation receipt is invalid.")
    pip_args = main.get("pip_args", [])
    if not isinstance(pip_args, list) or not all(isinstance(arg, str) for arg in pip_args):
        raise UpdateInstallError("Rocky's pipx installer arguments are invalid.")
    if main.get("suffix") or main.get("pinned") or receipt.get("venv_args") or receipt.get("injected_packages") or any(arg != "--force-reinstall" for arg in pip_args):
        raise UpdateInstallError("This pipx installation has custom settings. Reinstall Rocky with pipx and those settings to preserve them.")
    original = main.get("package_or_url")
    if not isinstance(original, str):
        raise UpdateInstallError("Rocky's pipx package source is invalid.")
    matches = re.findall(r"\[([^\[\]]+)\]", original)
    if len(matches) > 1:
        raise UpdateInstallError("Rocky's pipx optional-package settings could not be read.")
    extras = _extras(matches[0].split(",") if matches else [])
    command = [_manager("pipx"), "install", "--force"]
    if main.get("include_dependencies"):
        command.append("--include-deps")
    command.append("rocky" + extras + " @ " + source)
    env = os.environ.copy()
    env["PIPX_HOME"] = str(prefix.parent.parent)
    env["PIPX_BIN_DIR"] = str(_pipx_bin(prefix))
    return command, env


def install_command(commit: str) -> tuple[list[str], dict[str, str]]:
    """Return installer arguments and environment targeting this exact installation."""
    if not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        raise UpdateInstallError("GitHub did not return a valid commit identifier.")
    prefix = Path(sys.prefix).resolve()
    source = f"git+https://github.com/aurimasAPG/rocky@{commit}"
    uv_receipt = prefix / "uv-receipt.toml"
    pipx_receipt = prefix / "pipx_metadata.json"
    try:
        if uv_receipt.is_file():
            return _uv(prefix, tomllib.loads(uv_receipt.read_text(encoding="utf-8")), source)
        if pipx_receipt.is_file():
            data = json.loads(pipx_receipt.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise UpdateInstallError("Rocky's pipx installation receipt is invalid.")
            return _pipx(prefix, data, source)
    except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        raise UpdateInstallError("Could not read Rocky's installation receipt. Repair the installation with its package manager, then retry.") from exc
    raise UpdateInstallError("Self-update supports uv and pipx installations. Install Rocky with the installer at https://github.com/aurimasAPG/rocky, then run rocky update.")
