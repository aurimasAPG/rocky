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


def allowed_workspaces() -> list[Path]:
    load_env()
    raw = os.environ.get("ROCKY_WORKSPACES") or os.environ.get("ORCHE_WORKSPACES") or ""
    paths = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            paths.append(Path(part).expanduser().resolve())
    return paths
