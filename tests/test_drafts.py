import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from rocky.drafts import DraftError, TaskDraft, clear_draft, load_draft, save_draft


def test_missing_draft_does_not_create_storage(tmp_path):
    assert load_draft(tmp_path) is None
    assert clear_draft(tmp_path, "unknown") is False
    assert not (tmp_path / ".rocky").exists()


def test_draft_preserves_multiline_unicode_whitespace_and_invalid_budget(tmp_path):
    goal = "  Sveiki, Žemė 🌍\n\n[time] 10:00\tįrašas\n"
    draft = save_draft(tmp_path, goal, "not yet a number, €", expected_revision="")
    assert draft.goal == goal and draft.budget_text == "not yet a number, €"
    assert draft.revision
    assert load_draft(tmp_path) == draft
    assert "Žemė" in (tmp_path / ".rocky/composer.json").read_text(encoding="utf-8")


def test_drafts_are_workspace_local_and_do_not_use_rocky_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    first = save_draft(tmp_path / "one", "one", "0.3")
    second = save_draft(tmp_path / "two", "two", "0.8")
    assert load_draft(tmp_path / "one") == first
    assert load_draft(tmp_path / "two") == second
    assert not (tmp_path / "home").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_new_draft_and_lock_have_private_permissions(tmp_path):
    save_draft(tmp_path, "private text", "1")
    assert (tmp_path / ".rocky").stat().st_mode & 0o777 == 0o700
    assert (tmp_path / ".rocky/composer.json").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / ".rocky/.composer.lock").stat().st_mode & 0o777 == 0o600


def test_empty_editor_contents_are_saved_without_validation_or_trimming(tmp_path):
    draft = save_draft(tmp_path, "", "")
    assert load_draft(tmp_path) == TaskDraft("", "", draft.revision)


def test_every_saved_snapshot_has_a_new_revision(tmp_path):
    first = save_draft(tmp_path, "same", "1")
    second = save_draft(tmp_path, "same", "1", expected_revision=first.revision)
    assert second.revision != first.revision


def test_clear_does_not_remove_a_newer_saved_draft(tmp_path):
    first = save_draft(tmp_path, "submitted", "1")
    second = save_draft(tmp_path, "new task", "2", expected_revision=first.revision)
    assert clear_draft(tmp_path, first.revision) is False
    assert load_draft(tmp_path) == second
    assert clear_draft(tmp_path, second.revision) is True
    assert load_draft(tmp_path) is None
    assert clear_draft(tmp_path, second.revision) is False


def test_stale_editor_cannot_replace_another_terminals_draft(tmp_path):
    first = save_draft(tmp_path, "original", "1", expected_revision="")
    second = save_draft(tmp_path, "newer", "2", expected_revision=first.revision)
    for revision in ("", first.revision):
        with pytest.raises(DraftError, match="changed in another terminal"):
            save_draft(tmp_path, "stale", "3", expected_revision=revision)
    assert load_draft(tmp_path) == second


def test_concurrent_editors_cannot_both_save_the_same_revision(tmp_path):
    original = save_draft(tmp_path, "original", "1")
    ready = Barrier(2)

    def save(goal):
        ready.wait()
        try:
            return save_draft(tmp_path, goal, "1", expected_revision=original.revision)
        except DraftError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ["first terminal", "second terminal"]))
    saved = [result for result in results if result is not None]
    assert len(saved) == 1
    assert load_draft(tmp_path) == saved[0]


@pytest.mark.parametrize("raw", [b"{broken", b"\xff", b"[]", b"{}",
    json.dumps({"version": 2, "goal": "old", "budget_text": "1", "revision": "rev"}).encode(),
    json.dumps({"version": 1, "goal": 12, "budget_text": "1", "revision": "rev"}).encode(),
    json.dumps({"version": 1, "goal": "old", "budget_text": "1", "revision": ""}).encode(),
])
def test_malformed_load_and_guarded_save_preserve_the_file(tmp_path, raw):
    (tmp_path / ".rocky").mkdir()
    path = tmp_path / ".rocky/composer.json"
    path.write_bytes(raw)
    with pytest.raises(DraftError, match="preserved"):
        load_draft(tmp_path)
    with pytest.raises(DraftError, match="preserved"):
        save_draft(tmp_path, "new", "1", expected_revision="")
    assert path.read_bytes() == raw


def test_explicit_unconditional_save_can_replace_an_invalid_draft(tmp_path):
    (tmp_path / ".rocky").mkdir()
    (tmp_path / ".rocky/composer.json").write_text("{broken")
    replacement = save_draft(tmp_path, "explicit replacement", "1")
    assert load_draft(tmp_path) == replacement


def test_failed_atomic_replace_keeps_previous_draft_and_cleans_unique_temp(tmp_path, monkeypatch):
    original = save_draft(tmp_path, "keep me", "1")
    seen = []

    def fail(source, target):
        seen.append(source)
        raise OSError("disk unavailable")

    monkeypatch.setattr("rocky.drafts.os.replace", fail)
    for _ in range(2):
        with pytest.raises(OSError, match="disk unavailable"):
            save_draft(tmp_path, "do not commit", "2", expected_revision=original.revision)
    assert load_draft(tmp_path) == original
    assert len(set(seen)) == 2
    assert not list((tmp_path / ".rocky").glob(".composer-*.tmp"))


@pytest.mark.parametrize("inside", [False, True])
def test_symlinked_control_directory_is_rejected(tmp_path, inside):
    target = tmp_path / "elsewhere" if inside else tmp_path.parent / f"{tmp_path.name}-outside"
    target.mkdir()
    (tmp_path / ".rocky").symlink_to(target, target_is_directory=True)
    for operation in (lambda: load_draft(tmp_path), lambda: save_draft(tmp_path, "text", "1"),
                      lambda: clear_draft(tmp_path, "rev")):
        with pytest.raises(DraftError, match="symlink"):
            operation()
    assert list(target.iterdir()) == []


def test_symlinked_draft_cannot_be_read_replaced_or_cleared(tmp_path):
    (tmp_path / ".rocky").mkdir()
    outside = tmp_path.parent / f"{tmp_path.name}-private.json"
    outside.write_text("private")
    (tmp_path / ".rocky/composer.json").symlink_to(outside)
    for operation in (lambda: load_draft(tmp_path), lambda: save_draft(tmp_path, "text", "1"),
                      lambda: clear_draft(tmp_path, "rev")):
        with pytest.raises(DraftError, match="symlink"):
            operation()
    assert outside.read_text() == "private"


def test_symlinked_lock_cannot_redirect_a_save(tmp_path):
    (tmp_path / ".rocky").mkdir()
    outside = tmp_path.parent / f"{tmp_path.name}-private.lock"
    outside.write_text("private")
    (tmp_path / ".rocky/.composer.lock").symlink_to(outside)
    with pytest.raises(DraftError, match="symlink"):
        save_draft(tmp_path, "text", "1")
    assert outside.read_text() == "private"


def test_directory_in_place_of_draft_is_not_overwritten(tmp_path):
    (tmp_path / ".rocky/composer.json").mkdir(parents=True)
    with pytest.raises(DraftError, match="regular file"):
        save_draft(tmp_path, "text", "1")
    assert (tmp_path / ".rocky/composer.json").is_dir()
