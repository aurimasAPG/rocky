"""The declared files of a run, as they currently exist in its workspace."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any


PREVIEW_BYTES = 128 * 1024
TEXT_SUFFIXES = frozenset({
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml",
    ".toml", ".log", ".py", ".js", ".ts", ".css", ".sql", ".sh",
})


def artifact_name(raw: Any) -> str | None:
    """Only ordinary relative paths; never configuration, control files or ADS."""
    if not isinstance(raw, str) or not raw or any(c in raw for c in ("\\", "\x00", ":")):
        return None
    parts = raw.split("/")
    if any(part.startswith(".") for part in parts):
        return None
    path = PurePosixPath(raw)
    if path.is_absolute() or not path.name:
        return None
    return path.as_posix()


def declared_artifacts(meta: dict[str, Any]) -> list[str]:
    primary = meta.get("deliverable_path")
    files = meta.get("files")
    entries = [primary, *(files if isinstance(files, list) else [])]
    return list(dict.fromkeys(name for raw in entries if (name := artifact_name(raw))))


def artifact_path(workspace: Path, name: str) -> Path | None:
    """Resolve again on each request: a saved path is not proof a file is safe."""
    if artifact_name(name) != name:
        return None
    root = workspace.resolve()
    try:
        path = (root / name).resolve()
        relative = path.relative_to(root)
        if any(part.startswith(".") for part in relative.parts):
            return None
        if path.exists() and not path.is_file():
            return None
    except (OSError, ValueError, RuntimeError):
        return None
    return path


def artifact_inventory(workspace: Path, meta: dict[str, Any]) -> list[dict[str, Any]]:
    primary = artifact_name(meta.get("deliverable_path"))
    files = []
    for name in declared_artifacts(meta):
        path = artifact_path(workspace, name)
        if path is None:
            continue
        try:
            available = path.is_file()
            size = path.stat().st_size if available else None
        except OSError:
            available, size = False, None
        files.append({
            "path": name,
            "name": PurePosixPath(name).name,
            "size": size,
            "available": available,
            "previewable": available and path.suffix.lower() in TEXT_SUFFIXES,
            "primary": name == primary,
        })
    return files


def read_preview(path: Path) -> tuple[str, bool]:
    with path.open("rb") as source:
        data = source.read(PREVIEW_BYTES + 1)
    return data[:PREVIEW_BYTES].decode("utf-8", errors="replace"), len(data) > PREVIEW_BYTES
