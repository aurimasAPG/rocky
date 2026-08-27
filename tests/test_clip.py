import subprocess

from rocky.clip import copy_text, paste_text


def test_copy_text_uses_pbcopy(monkeypatch):
    called = {}

    def fake_which(name):
        return "/usr/bin/pbcopy" if name == "pbcopy" else None

    def fake_run(cmd, **kwargs):
        called["cmd"] = cmd
        called["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("rocky.clip.shutil.which", fake_which)
    monkeypatch.setattr("rocky.clip.subprocess.run", fake_run)
    assert copy_text("hello rocky") is True
    assert called["cmd"] == ["pbcopy"]
    assert called["input"] == b"hello rocky"


def test_copy_empty_is_false():
    assert copy_text("") is False


def test_paste_text_uses_pbpaste(monkeypatch):
    monkeypatch.setattr(
        "rocky.clip.shutil.which",
        lambda name: "/usr/bin/pbpaste" if name == "pbpaste" else None,
    )
    monkeypatch.setattr(
        "rocky.clip.subprocess.check_output",
        lambda cmd, **kwargs: "pasted",
    )
    assert paste_text() == "pasted"
