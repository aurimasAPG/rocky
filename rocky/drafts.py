"""Workspace-local composer drafts, independent of run and configuration state."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


class DraftError(ValueError):
    """A draft cannot safely be restored or replaced."""


@dataclass(frozen=True)
class TaskDraft:
    goal: str
    budget_text: str
    revision: str = ""
    base_revision: str = ""


def _directory(workspace: str | Path, *, create: bool = False) -> Path | None:
    root = Path(workspace).expanduser().resolve()
    directory = root / ".rocky"
    if directory.is_symlink():
        raise DraftError("Cannot store a draft in a symlinked .rocky directory.")
    if not directory.exists():
        if not create:
            return None
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir() or directory.resolve().parent != root:
        raise DraftError("Draft storage must be a real .rocky directory inside the workspace.")
    return directory


def _file(directory: Path, name: str = "composer.json") -> Path:
    path = directory / name
    if path.is_symlink():
        raise DraftError(f"Cannot use a symlink for draft storage: {name}.")
    if path.exists() and not path.is_file():
        raise DraftError(f"Draft storage is not a regular file: {name}.")
    return path


@contextmanager
def _locked(directory: Path):
    """OS locks release on exit or process death; no stale lock directory remains."""
    path = _file(directory, ".composer.lock")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise DraftError("Draft lock is not a regular file.")
        if os.name == "nt":
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _read(directory: Path) -> TaskDraft | None:
    path = _file(directory)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise DraftError("Draft storage is not a regular file.")
        with os.fdopen(descriptor, "r", encoding="utf-8") as source:
            descriptor = None
            data = json.load(source)
    except (ValueError, UnicodeError) as exc:
        raise DraftError("Cannot restore composer.json: invalid draft data; the file was preserved.") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if (
        not isinstance(data, dict)
        or type(data.get("version")) is not int
        or data["version"] != 1
        or not all(isinstance(data.get(key), str) for key in ("goal", "budget_text", "revision"))
        or not data["revision"]
    ):
        raise DraftError("Cannot restore composer.json: unsupported draft format; the file was preserved.")
    return TaskDraft(data["goal"], data["budget_text"], data["revision"])


def load_draft(workspace: str | Path) -> TaskDraft | None:
    """Read an atomic snapshot without creating storage or changing invalid data."""
    directory = _directory(workspace)
    return _read(directory) if directory is not None else None


def save_draft(
    workspace: str | Path,
    goal: str,
    budget_text: str,
    *,
    expected_revision: str | None = None,
) -> TaskDraft:
    """Save exact editor contents; '' expects no draft, None explicitly overwrites."""
    if not isinstance(goal, str) or not isinstance(budget_text, str):
        raise DraftError("Draft goal and budget must be text.")
    if expected_revision is not None and not isinstance(expected_revision, str):
        raise DraftError("The expected draft revision must be text.")
    directory = _directory(workspace, create=True)
    with _locked(directory):
        path = _file(directory)
        if expected_revision is not None:
            current = _read(directory)
            if (current.revision if current is not None else "") != expected_revision:
                raise DraftError("Draft changed in another terminal; reopen the composer to restore the latest draft.")
        draft = TaskDraft(goal, budget_text, uuid.uuid4().hex)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=".composer-", suffix=".tmp",
                                             dir=directory, delete=False) as target:
                temporary = Path(target.name)
                json.dump({"version": 1, "goal": draft.goal, "budget_text": draft.budget_text,
                           "revision": draft.revision}, target, ensure_ascii=False, indent=2)
                target.write("\n")
                target.flush()
                os.fsync(target.fileno())
            _file(directory)
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return draft


def clear_draft(workspace: str | Path, expected_revision: str) -> bool:
    """Remove only the submitted snapshot, preserving a newer terminal's edits."""
    directory = _directory(workspace)
    if directory is None:
        return False
    with _locked(directory):
        current = _read(directory)
        if current is None or current.revision != expected_revision:
            return False
        _file(directory).unlink()
        return True
