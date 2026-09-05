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
    apply_config()


# config.toml keys → the environment variable the rest of Rocky already reads.
# The environment (and .env) wins over the file; the file wins over built-in defaults.
CONFIG_ENV = {
    "worker": "ROCKY_WORKER",
    "image_model": "ROCKY_IMAGE_MODEL",
    "language": "ROCKY_LANGUAGE",
    "sandbox": "ROCKY_SANDBOX",
    "sandbox_image": "ROCKY_SANDBOX_IMAGE",
    "token": "ROCKY_TOKEN",
    "workspaces": "ROCKY_WORKSPACES",
    "skills": "ROCKY_SKILLS",
    "recipes": "ROCKY_RECIPES",
    "bash_deny": "ROCKY_BASH_DENY",
    "base_url": "OPENROUTER_BASE_URL",
}
# Keys the CLI reads as defaults for its flags rather than through the environment.
CONFIG_FLAGS = ("budget", "max_loops")


def config_paths(workspace: str | Path | None = None) -> list[Path]:
    """Home config first, project config second (the project one wins per key)."""
    raw = (os.environ.get("ROCKY_HOME") or "").strip()
    home = Path(raw).expanduser().resolve() if raw else Path.home() / ".rocky"
    project = Path(workspace).expanduser().resolve() if workspace else Path.cwd()
    return [home / "config.toml", project / ".rocky" / "config.toml"]


def load_config(workspace: str | Path | None = None) -> dict:
    """Merged config.toml values. Unknown keys are kept so `rocky config` can show them."""
    import tomllib

    merged: dict = {}
    for path in config_paths(workspace):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"{path}: {exc}") from exc
        for key, value in data.items():
            if isinstance(value, list):
                value = ",".join(str(v) for v in value)
            merged[key] = value
    return merged


def apply_config(workspace: str | Path | None = None) -> dict:
    """Put config.toml values into the environment where nothing else set them. Returns the merged config."""
    try:
        cfg = load_config(workspace)
    except ValueError:
        return {}
    for key, env in CONFIG_ENV.items():
        if key in cfg and not (os.environ.get(env) or "").strip():
            os.environ[env] = str(cfg[key])
    return cfg


def forced_language() -> str:
    """ROCKY_LANGUAGE (or `language` in config.toml): write every deliverable in this language."""
    load_env()
    return (os.environ.get("ROCKY_LANGUAGE") or "").strip().lower()


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


def home_env_path() -> Path:
    return Path.home() / ".env"


def save_api_key(key: str, path: Path | None = None) -> Path:
    """Write OPENROUTER_API_KEY into ~/.env (replace an existing line), chmod 600, export it."""
    key = (key or "").strip()
    if not key:
        raise ValueError("empty key")
    path = path or home_env_path()
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    replaced = False
    for i, line in enumerate(lines):
        if line.strip().removeprefix("export ").split("=", 1)[0].strip() == "OPENROUTER_API_KEY":
            lines[i] = f"OPENROUTER_API_KEY={key}"
            replaced = True
    if not replaced:
        lines.append(f"OPENROUTER_API_KEY={key}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    os.environ["OPENROUTER_API_KEY"] = key
    return path
