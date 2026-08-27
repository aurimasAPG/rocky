"""Guard rails for the `bash` tool: a deny list, and optional isolation.

Rocky is autonomous inside `--dir`, so there is no approval prompt. Instead:

* ``check_command`` refuses the small set of commands that are destructive
  no matter the workspace (wiping disks or home, fork bombs, pipe-to-shell,
  privilege escalation, force-pushes).
* ``wrap_command`` runs the command inside Docker (workspace mounted at
  ``/work``) or macOS ``sandbox-exec`` (writes allowed only inside the
  workspace and temp dirs) when ``ROCKY_SANDBOX`` says so.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

# (label, regex). Matched case-insensitively against the whole command.
DENY_RULES: list[tuple[str, str]] = [
    ("wipe root or home", r"\brm\s+(-[a-z]*r[a-z]*f[a-z]*|-[a-z]*f[a-z]*r[a-z]*)\s+(/|~|\$HOME|\.\.)(\s|$|/\*)"),
    ("wipe root or home", r"\brm\s+(-[a-z]*r[a-z]*f[a-z]*|-[a-z]*f[a-z]*r[a-z]*)\s+/(bin|etc|usr|var|home|Users|System|Library)\b"),
    ("format a disk", r"\bmkfs(\.\w+)?\b"),
    ("raw write to a device", r"\bdd\b.*\bof=/dev/"),
    ("raw write to a device", r">\s*/dev/(sd|disk|nvme|hd)"),
    ("fork bomb", r":\s*\(\s*\)\s*\{.*:\s*\|\s*:.*\}\s*;\s*:"),
    ("pipe the web into a shell", r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba|z|da|k)?sh\b"),
    ("privilege escalation", r"(^|[;&|]\s*)sudo\b"),
    ("privilege escalation", r"(^|[;&|]\s*)su\s+-?\s*(root)?\s*($|[;&|])"),
    ("power off the machine", r"\b(shutdown|reboot|halt|poweroff)\b"),
    ("world-writable system tree", r"\bchmod\s+(-R\s+)?777\s+/(\s|$)"),
    ("rewrite remote history", r"\bgit\s+push\b.*(--force\b|-f\b|--force-with-lease\b)"),
    ("delete git history", r"\bgit\s+(reset\s+--hard\s+\S+|clean\s+-[a-z]*f[a-z]*x|branch\s+-D\s+(main|master)\b)"),
    ("kill everything", r"\b(killall|pkill)\s+-9\s+-1\b|\bkill\s+-9\s+-1\b"),
]

WRITABLE_TEMP = ("/tmp", "/private/tmp", "/private/var/folders", "/var/folders", "/dev/null")


def check_command(command: str, extra_patterns: list[str] | None = None) -> str | None:
    """Return a reason to refuse the command, or None when it may run."""
    text = " ".join((command or "").split())
    if not text:
        return None
    rules = list(DENY_RULES) + [("blocked by ROCKY_BASH_DENY", p) for p in extra_patterns or []]
    for label, pattern in rules:
        try:
            if re.search(pattern, text, flags=re.I):
                return label
        except re.error:
            continue
    return None


def seatbelt_profile(root: Path, home: Path | None = None) -> str:
    """macOS sandbox-exec profile: read anything, write only inside the workspace and temp."""
    home = home or Path.home()
    allowed = [str(root.resolve()), *WRITABLE_TEMP, str(home / ".cache"), str(home / ".rocky")]
    subpaths = "\n".join(f'    (subpath "{p}")' for p in allowed)
    return (
        "(version 1)\n"
        "(allow default)\n"
        "(deny file-write*)\n"
        "(allow file-write*\n"
        f"{subpaths}\n"
        '    (literal "/dev/null")\n'
        '    (regex #"^/dev/tty")\n'
        '    (regex #"^/private/var/folders/")\n'
        ")\n"
    )


def wrap_command(
    command: str,
    root: Path,
    mode: str = "none",
    image: str = "python:3.12-slim",
) -> list[str] | str:
    """Argv for the sandboxed command, or the plain string when mode is none."""
    if mode == "none":
        return command
    if mode == "docker":
        return [
            "docker",
            "run",
            "--rm",
            "--network",
            "bridge",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "256",
            "-v",
            f"{root.resolve()}:/work",
            "-w",
            "/work",
            image,
            "sh",
            "-lc",
            command,
        ]
    if mode == "sandbox-exec":
        return ["sandbox-exec", "-p", seatbelt_profile(root), "sh", "-c", command]
    raise ValueError(f"unknown sandbox mode: {mode}")


def sandbox_available(mode: str) -> str | None:
    """Reason the mode cannot run here, or None when it can."""
    if mode == "none":
        return None
    binary = "docker" if mode == "docker" else "sandbox-exec"
    if shutil.which(binary) is None:
        return f"{binary} is not installed"
    return None
