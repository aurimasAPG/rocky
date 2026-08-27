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
