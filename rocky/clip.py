"""OS clipboard for the TUI. OSC 52 does not work in Apple Terminal."""

from __future__ import annotations

import shutil
import subprocess


def copy_text(text: str) -> bool:
    if not text:
        return False
    data = text.encode("utf-8")
    if shutil.which("pbcopy"):
        return _run(["pbcopy"], data)
    if shutil.which("wl-copy"):
        return _run(["wl-copy"], data)
    if shutil.which("xclip"):
        return _run(["xclip", "-selection", "clipboard"], data)
    if shutil.which("xsel"):
        return _run(["xsel", "--clipboard", "--input"], data)
    return False


def paste_text() -> str:
    commands = (
        ["pbpaste"],
        ["wl-paste", "-n"],
        ["xclip", "-selection", "clipboard", "-o"],
        ["xsel", "--clipboard", "--output"],
    )
    for cmd in commands:
        if not shutil.which(cmd[0]):
            continue
        try:
            return subprocess.check_output(cmd, text=True, timeout=2, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            continue
    return ""


def _run(cmd: list[str], data: bytes) -> bool:
    try:
        subprocess.run(cmd, input=data, check=False, timeout=2, stderr=subprocess.DEVNULL)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
