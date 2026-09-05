import pytest

from rocky.tools import ToolError, WorkspaceTools


def test_write_and_read_inside_workspace(tmp_path):
    tools = WorkspaceTools(tmp_path)
    tools.write_file("notes/hello.md", "# hi")
    assert tools.read_file("notes/hello.md") == "# hi"
    listing = tools.list_dir(".")
    assert "notes/" in listing


def test_rejects_path_escape(tmp_path):
    tools = WorkspaceTools(tmp_path)
    with pytest.raises(ToolError, match="workspace"):
        tools.write_file("../outside.txt", "nope")


def test_bash_runs_in_workspace(tmp_path):
    tools = WorkspaceTools(tmp_path)
    tools.write_file("a.txt", "alpha")
    out = tools.bash("cat a.txt && pwd")
    assert "alpha" in out
    assert str(tmp_path.resolve()) in out


def test_grep_finds_matches(tmp_path):
    tools = WorkspaceTools(tmp_path)
    tools.write_file("src/app.py", "def optimize_ads():\n    return 1\n")
    hits = tools.grep("optimize_ads")
    assert "src/app.py" in hits


def test_tracks_files_written_and_drops_deleted_ones(tmp_path):
    tools = WorkspaceTools(tmp_path)
    tools.write_file("runs/notes/a.md", "alpha")
    tools.write_file("deliverables/out.md", "out")
    tools.write_file("runs/notes/a.md", "alpha again")
    tools.patch("deliverables/out.md", "out", "final")
    assert tools.written == ["runs/notes/a.md", "deliverables/out.md"]
    (tmp_path / "runs/notes/a.md").unlink()
    (tmp_path / "deliverables/empty.md").write_text("")
    assert tools.files_on_disk() == ["deliverables/out.md"]


def test_patch_keeps_the_whole_file_past_the_read_clip(tmp_path):
    tools = WorkspaceTools(tmp_path)
    body = "line\n" * 20_000  # 100 KB, twice the read_file clip
    tools.write_file("deliverables/long.md", "# Title\n" + body + "END\n")
    tools.patch("deliverables/long.md", "# Title", "# Better title")
    text = (tmp_path / "deliverables/long.md").read_text()
    assert text.startswith("# Better title\n") and text.endswith("END\n")
    assert len(text) == len("# Better title\n") + len(body) + len("END\n")
    assert "truncated" not in text


def test_bash_finds_rockys_python_and_drops_pythonpath(tmp_path, monkeypatch):
    import os
    import sys

    from rocky.tools import bash_env

    monkeypatch.setenv("PYTHONPATH", "/somewhere/else")
    env = bash_env()
    assert "PYTHONPATH" not in env
    assert env["PATH"].split(os.pathsep)[0] == str(__import__("pathlib").Path(sys.executable).parent)
    tools = WorkspaceTools(tmp_path)
    out = tools.bash("python3 -c 'import sys, pptx, openpyxl; print(sys.executable)'")
    assert "exit" not in out.splitlines()[0]
    assert "Traceback" not in out


def test_append_file_builds_a_long_document_in_parts(tmp_path):
    tools = WorkspaceTools(tmp_path)
    tools.write_file("deliverables/plan.md", "# Plan\n")
    out = tools.append_file("deliverables/plan.md", "## Part 2\n")
    tools.append_file("deliverables/new.md", "created by append\n")
    assert "appended 10 chars" in out
    assert (tmp_path / "deliverables/plan.md").read_text() == "# Plan\n## Part 2\n"
    assert (tmp_path / "deliverables/new.md").read_text() == "created by append\n"
    assert tools.files_on_disk() == ["deliverables/plan.md", "deliverables/new.md"]


def test_cut_off_tool_arguments_tell_the_worker_to_write_in_parts(tmp_path):
    from rocky.tools import dispatch

    tools = WorkspaceTools(tmp_path)
    raw = '{"path":"deliverables/facebook_reklama_planas.md","content":"# Planas\\n\\nlabai ilgas tekstas'
    with pytest.raises(ToolError) as info:
        dispatch(tools, "write_file", {"_raw": raw})
    msg = str(info.value)
    assert "cut off for deliverables/facebook_reklama_planas.md" in msg
    assert "append_file" in msg and "Nothing was written" in msg
    assert not (tmp_path / "deliverables").exists()
