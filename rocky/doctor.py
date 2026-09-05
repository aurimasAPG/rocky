"""`rocky doctor`: is this machine ready to run? Key, folder, sandbox, browser, skills.

Each check is a small function returning a Check. ``fetch`` is injectable so
tests never touch the network.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from rocky.config import api_key, base_url, home_env_path, sandbox_mode

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Check:
    name: str
    status: str  # ok | warn | fail
    detail: str
    hint: str = ""


def _python() -> Check:
    v = sys.version_info
    ver = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= (3, 11):
        return Check("python", OK, ver)
    return Check("python", FAIL, ver, "Rocky needs Python 3.11 or newer")


def _key() -> Check:
    key = api_key()
    if not key:
        return Check("api key", FAIL, "OPENROUTER_API_KEY not set", f"put OPENROUTER_API_KEY=sk-or-... in {home_env_path()}")
    where = "environment"
    for path in (Path.cwd() / ".env", home_env_path()):
        try:
            if path.is_file() and "OPENROUTER_API_KEY" in path.read_text(encoding="utf-8"):
                where = str(path)
                break
        except OSError:
            continue
    masked = key[:9] + "…" + key[-4:] if len(key) > 16 else "set"
    status = OK if key.startswith("sk-or-") else WARN
    hint = "" if status == OK else "OpenRouter keys start with sk-or-"
    return Check("api key", status, f"{masked} from {where}", hint)


def _env_permissions() -> Check:
    path = home_env_path()
    if not path.is_file():
        return Check("~/.env perms", OK, "no ~/.env")
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        return Check("~/.env perms", WARN, "cannot stat")
    if mode & 0o077:
        return Check("~/.env perms", WARN, oct(mode), f"chmod 600 {path}")
    return Check("~/.env perms", OK, oct(mode))


def _openrouter(fetch=None) -> Check:
    check, _ = _openrouter_with_catalog(fetch)
    return check


def _openrouter_with_catalog(fetch=None) -> tuple[Check, dict]:
    key = api_key()
    if not key:
        return Check("openrouter", FAIL, "skipped — no key"), {}
    if fetch is None:
        import httpx

        def fetch(url: str, headers: dict) -> tuple[int, dict]:
            r = httpx.get(url, headers=headers, timeout=12.0)
            try:
                return r.status_code, r.json()
            except ValueError:
                return r.status_code, {}

    try:
        code, data = fetch(f"{base_url()}/models", {"Authorization": f"Bearer {key}"})
    except Exception as exc:  # noqa: BLE001
        return Check("openrouter", FAIL, f"unreachable: {str(exc)[:80]}", "check your network / proxy"), {}
    if code == 401:
        return Check("openrouter", FAIL, "401 — key rejected", "create a key at openrouter.ai/keys"), {}
    if code == 402:
        return Check("openrouter", FAIL, "402 — no credits", "top up at openrouter.ai/credits"), {}
    if code >= 400:
        return Check("openrouter", FAIL, f"HTTP {code}"), {}
    data = data if isinstance(data, dict) else {}
    count = len(data.get("data") or [])
    return Check("openrouter", OK, f"reachable · {count} models"), data


def _workspace(workspace: Path) -> Check:
    try:
        probe = workspace / ".rocky" / ".doctor-probe"
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return Check("workspace", FAIL, f"{workspace} not writable ({exc})", "pick another folder with -d")
    if workspace == Path.home():
        return Check("workspace", WARN, str(workspace), "do not point Rocky at your home folder — use a project folder")
    return Check("workspace", OK, str(workspace))


def _sandbox() -> Check:
    try:
        mode = sandbox_mode()
    except ValueError as exc:
        return Check("sandbox", FAIL, str(exc), "ROCKY_SANDBOX=none | sandbox-exec | docker")
    if mode == "none":
        hint = "export ROCKY_SANDBOX=sandbox-exec" if sys.platform == "darwin" else "export ROCKY_SANDBOX=docker"
        return Check("sandbox", WARN, "off — bash can write outside the workspace", hint)
    from rocky.sandbox import sandbox_available

    blocker = sandbox_available(mode)
    if blocker:
        return Check("sandbox", FAIL, f"{mode}: {blocker}")
    return Check("sandbox", OK, mode)


def _playwright() -> Check:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return Check("browser", WARN, "playwright not installed", 'uv tool install --force --reinstall "rocky[browser]" && playwright install chromium')
    roots = [
        Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or ""),
        Path.home() / "Library" / "Caches" / "ms-playwright",
        Path.home() / ".cache" / "ms-playwright",
        Path(os.environ.get("LOCALAPPDATA") or "") / "ms-playwright",
    ]
    for root in roots:
        if str(root) and root.is_dir() and any(p.name.startswith("chromium") for p in root.iterdir()):
            return Check("browser", OK, f"playwright + chromium ({root})")
    return Check("browser", WARN, "playwright installed, chromium missing", "playwright install chromium")


def _skills() -> Check:
    from rocky.skills import list_skills

    rows = list_skills()
    names = ", ".join(s["name"] for s in rows[:6])
    return Check("skills", OK if rows else WARN, f"{len(rows)} · {names}{'…' if len(rows) > 6 else ''}")


def _models(catalog: dict | None = None) -> Check:
    """The worker Rocky will actually use, resolved against the live catalog when we have it."""
    from rocky.catalog import DEFAULT_WORKER_MODEL, _pinned_worker, parse_models
    from rocky.config import preferred_worker

    pin = preferred_worker() or DEFAULT_WORKER_MODEL
    pinned = "ROCKY_WORKER" if preferred_worker() else "default"
    image = os.environ.get("ROCKY_IMAGE_MODEL") or "google/gemini-3.1-flash-image (default)"
    rows = (catalog or {}).get("data") or []
    if not rows:
        return Check("models", OK, f"worker {pin} ({pinned}, not checked — no catalog) · image {image}")
    models = parse_models(catalog or {})
    hit = _pinned_worker([m for m in models if m.has_tools], pin)
    if hit is None:
        return Check(
            "models",
            WARN,
            f"worker {pin} ({pinned}) is not in the catalog — runs fall back to the cheapest eligible model",
            "unset ROCKY_WORKER or pick an id from openrouter.ai/models that supports tools",
        )
    resolved = hit.id if hit.id != pin else pin
    return Check("models", OK, f"worker {resolved} ({pinned}, {hit.blended_per_million:.2f} $/M) · image {image}")


def _config(workspace: Path) -> Check:
    from rocky.config import config_paths, load_config

    paths = config_paths(workspace)
    try:
        cfg = load_config(workspace)
    except ValueError as exc:
        return Check("config", FAIL, str(exc)[:120], "fix the TOML or remove the file")
    found = [str(p) for p in paths if p.is_file()]
    if not found:
        return Check("config", OK, "none (built-in defaults) — `rocky config` prints an example")
    keys = ", ".join(sorted(cfg)) or "empty"
    return Check("config", OK, f"{' + '.join(found)} · {keys}")


def run_checks(workspace: Path, fetch=None) -> list[Check]:
    openrouter, catalog = _openrouter_with_catalog(fetch)
    return [
        _python(),
        _config(Path(workspace).expanduser().resolve()),
        _key(),
        _env_permissions(),
        openrouter,
        _workspace(Path(workspace).expanduser().resolve()),
        _sandbox(),
        _playwright(),
        _skills(),
        _models(catalog),
    ]


def healthy(checks: list[Check]) -> bool:
    return not any(c.status == FAIL for c in checks)


MARK = {OK: "[green]✓[/]", WARN: "[yellow]![/]", FAIL: "[red]✗[/]"}


def print_report(checks: list[Check], console) -> None:
    for c in checks:
        line = f"{MARK[c.status]} [bold]{c.name:<13}[/] {c.detail}"
        if c.hint:
            line += f"\n  [dim]{c.hint}[/]"
        console.print(line)
    console.print("[green]ready[/]" if healthy(checks) else "[red]not ready — fix the ✗ lines above[/]")
