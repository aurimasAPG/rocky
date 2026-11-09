"""Update the installed Rocky from a specific build of the official repository."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from rich.console import Console

from rocky import __file__ as PACKAGE_FILE
from rocky.update_install import UpdateInstallError, install_command

REPOSITORY = "https://github.com/aurimasAPG/rocky"
COMMIT_API = "https://api.github.com/repos/aurimasAPG/rocky/commits/main"


class UpdateError(ValueError):
    """An update could not be checked or verified."""


def _commit_from_source(source: dict) -> str:
    url = str(source.get("url", "")).rstrip("/").removesuffix(".git")
    commit = source.get("vcs_info", {}).get("commit_id", "")
    if url.lower() == REPOSITORY.lower() and isinstance(commit, str) and re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        return commit.lower()
    return ""


def installed_build() -> dict[str, str]:
    try:
        package = distribution("rocky")
    except PackageNotFoundError as exc:
        raise UpdateError("Rocky is running from source. Install it first with the installer in rocky help.") from exc
    if Path(PACKAGE_FILE).resolve() != Path(package.locate_file("rocky/__init__.py")).resolve():
        raise UpdateError("Rocky is running from a source checkout. Run the installed rocky command outside that checkout.")
    try:
        source = json.loads(package.read_text("direct_url.json") or "{}")
        if not isinstance(source, dict):
            raise ValueError("expected an object")
        if source.get("dir_info", {}).get("editable"):
            raise UpdateError("This is an editable development install. Use the installer in rocky help for automatic updates.")
        return {"version": package.version, "commit": _commit_from_source(source)}
    except (ValueError, TypeError, AttributeError) as exc:
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError("Rocky's installation metadata could not be read. Reinstall using rocky help.") from exc


def latest_commit() -> str:
    try:
        response = httpx.get(
            COMMIT_API,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "rocky-update"},
            timeout=15,
        )
        response.raise_for_status()
        commit = response.json().get("sha", "")
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        raise UpdateError("Could not check GitHub. Check your connection and retry rocky update.") from exc
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        raise UpdateError("GitHub did not return a valid build. Retry rocky update later.")
    return commit.lower()


def verify_installation(expected_commit: str) -> str:
    # A fresh interpreter reads the updated files, not this process's old imports.
    probe = (
        "import json, rocky; from importlib.metadata import distribution; "
        "p=distribution('rocky'); "
        "print(json.dumps({'version':p.version,'loaded_version':rocky.__version__,"
        "'source':json.loads(p.read_text('direct_url.json') or '{}')}))"
    )
    try:
        with TemporaryDirectory(prefix="rocky-update-verify-") as directory:
            result = subprocess.run(
                [sys.executable, "-I", "-c", probe], cwd=directory,
                capture_output=True, text=True, timeout=30, check=True,
            )
        build = json.loads(result.stdout)
        if (
            not build.get("version")
            or build.get("loaded_version") != build["version"]
            or _commit_from_source(build.get("source", {})) != expected_commit
        ):
            raise ValueError("installed build differs from the requested build")
        return str(build["version"])
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError) as exc:
        raise UpdateError("The installer finished, but the new build could not be verified. Retry rocky update or reinstall using rocky help.") from exc


def run_update(*, check_only: bool = False, console: Console | None = None) -> int:
    out = console or Console()
    try:
        current = installed_build()
        out.print(f"Rocky {current['version']} · checking GitHub…", markup=False)
        commit = latest_commit()
        if current["commit"] == commit:
            out.print(f"Rocky is up to date · v{current['version']} · build {commit[:7]}", markup=False)
            return 0
        if check_only:
            out.print(f"Latest GitHub build: {commit[:7]}. Run rocky update to install it.", markup=False)
            return 0
        command, env = install_command(commit)
        out.print(f"Installing the latest GitHub build ({commit[:7]})…", markup=False)
        with TemporaryDirectory(prefix="rocky-update-") as directory:
            result = subprocess.run(command, cwd=directory, env=env, check=False)
        if result.returncode:
            out.print("Update did not complete. Review the installer output above, then retry rocky update.", style="red")
            return 1
        version = verify_installation(commit)
        out.print(f"Updated to Rocky {version} · build {commit[:7]}. Reopen Rocky to use it.", style="green", markup=False)
        return 0
    except (UpdateError, UpdateInstallError, OSError) as exc:
        out.print(str(exc), style="red", markup=False)
        return 1
    except KeyboardInterrupt:
        out.print("Update interrupted. Run rocky update again when ready.")
        return 130
