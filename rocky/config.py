from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def load_env() -> None:
    home = Path.home() / ".env"
    if home.exists():
        load_dotenv(home, override=False)
    cwd = Path.cwd() / ".env"
    if cwd.exists():
        load_dotenv(cwd, override=True)


def api_key() -> str:
    load_env()
    return (os.environ.get("OPENROUTER_API_KEY") or "").strip()


def base_url() -> str:
    load_env()
    return (os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1").rstrip("/")


def serve_token() -> str:
    load_env()
    return (os.environ.get("ROCKY_TOKEN") or os.environ.get("ORCHE_TOKEN") or "").strip()


def preferred_worker() -> str | None:
    """None = catalog default (Gemini 3.7 Flash). Non-empty = pin that OpenRouter id."""
    load_env()
    raw = (os.environ.get("ROCKY_WORKER") or os.environ.get("ORCHE_WORKER") or "").strip()
    return raw or None


def rocky_home() -> Path:
    load_env()
    raw = (os.environ.get("ROCKY_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.home() / ".rocky"


def allowed_workspaces() -> list[Path]:
    load_env()
    raw = os.environ.get("ROCKY_WORKSPACES") or os.environ.get("ORCHE_WORKSPACES") or ""
    paths = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            paths.append(Path(part).expanduser().resolve())
    return paths


SANDBOX_MODES = ("none", "docker", "sandbox-exec")


def sandbox_mode() -> str:
    """How `bash` runs: none (host), docker (workspace mounted at /work), sandbox-exec (macOS)."""
    load_env()
    raw = (os.environ.get("ROCKY_SANDBOX") or "none").strip().lower()
    if raw in {"", "off", "0", "false"}:
        return "none"
    if raw in {"seatbelt", "macos"}:
        return "sandbox-exec"
    if raw not in SANDBOX_MODES:
        raise ValueError(f"ROCKY_SANDBOX must be one of {', '.join(SANDBOX_MODES)} (got {raw!r})")
    return raw


def sandbox_image() -> str:
    load_env()
    return (os.environ.get("ROCKY_SANDBOX_IMAGE") or "python:3.12-slim").strip()


def extra_bash_deny() -> list[str]:
    """Extra regexes (one per line or ;-separated) that block a bash command."""
    load_env()
    raw = os.environ.get("ROCKY_BASH_DENY") or ""
    return [part.strip() for part in raw.replace("\n", ";").split(";") if part.strip()]
