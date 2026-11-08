"""Extension point for ad_board checks: one module per topic, no edits to adboard.py.

A module in this package declares:

- ``AD_FIELDS``: names of extra per-ad inputs it reads (kept raw by ``normalize``).
- ``PACK_FIELDS``: names of extra pack-level inputs.
- ``SCHEMA``: ``{"ad": {name: json-schema}, "pack": {name: json-schema}}`` merged
  into the ``ad_board`` tool spec so workers see the fields.
- ``MESSAGES``: ``{"en": {code: format}, "lt": {code: format}}`` for its codes,
  formatted with ``field``, ``Field``, ``value``, ``n``, ``limit`` like adboard's own.
- ``check(pack, facts, images) -> list[Check]``: the code that settles something
  without an opinion. It may return nothing when its inputs are absent.

Each module pairs with ``rocky/playbook/elements-<topic>.toml`` so every code maps
to a book element the page can explain and switch off.
"""

from __future__ import annotations

import importlib
import pkgutil
from functools import lru_cache
from types import ModuleType
from typing import Any

REQUIRED_ATTRS = ("check", "MESSAGES")


# Modules that failed to import, by name, so a broken topic file is visible without stopping the board.
IMPORT_ERRORS: dict[str, str] = {}


@lru_cache(maxsize=1)
def modules() -> tuple[ModuleType, ...]:
    found: list[ModuleType] = []
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name):
        if info.name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f"{__name__}.{info.name}")
        except Exception as exc:  # noqa: BLE001 — one bad topic module must not take the ad board down
            IMPORT_ERRORS[info.name] = f"{type(exc).__name__}: {exc}"[:200]
            continue
        if all(hasattr(module, attr) for attr in REQUIRED_ATTRS):
            found.append(module)
    return tuple(found)


def ad_fields() -> tuple[str, ...]:
    return tuple(dict.fromkeys(name for m in modules() for name in getattr(m, "AD_FIELDS", ())))


def pack_fields() -> tuple[str, ...]:
    return tuple(dict.fromkeys(name for m in modules() for name in getattr(m, "PACK_FIELDS", ())))


def schema() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {"ad": {}, "pack": {}}
    for m in modules():
        for scope in ("ad", "pack"):
            out[scope].update((getattr(m, "SCHEMA", None) or {}).get(scope) or {})
    return out


def messages(language: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in modules():
        table = getattr(m, "MESSAGES", {}) or {}
        out.update(table.get(language) or table.get("en") or {})
    return out


def run(pack: dict, facts: str | None, images: dict[int, str]) -> list:
    """Every module's findings. A module that raises becomes one warning, not a dead page."""
    from rocky.adboard import Check

    checks: list = []
    for name, error in IMPORT_ERRORS.items():
        checks.append(Check("warn", "check_failed", value=f"{name}: {error}"[:200]))
    for m in modules():
        try:
            checks.extend(m.check(pack, facts, images) or [])
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("warn", "check_failed", value=f"{m.__name__.rsplit('.', 1)[-1]}: {exc}"[:200]))
    return checks
