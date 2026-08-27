from rocky.tools import ToolError, WorkspaceTools, dispatch, worker_tools


def test_new_tools_are_registered():
    names = {spec["function"]["name"] for spec in worker_tools(include_server=False)}
    assert names >= {
        "patch",
        "todo",
        "clarify",
        "vision_analyze",
        "browser_navigate",
        "browser_snapshot",
        "browser_close",
    }


def test_patch_replaces_unique_snippet(tmp_path):
    tools = WorkspaceTools(tmp_path)
    tools.write_file("page.md", "hello world\nsecond line\n")
    out = dispatch(
        tools,
        "patch",
        {"path": "page.md", "old_string": "hello world", "new_string": "hello rocky"},
    )
    assert "patched" in out
    assert tools.read_file("page.md").startswith("hello rocky")


def test_patch_fails_when_snippet_missing(tmp_path):
    tools = WorkspaceTools(tmp_path)
    tools.write_file("page.md", "only this\n")
    try:
        dispatch(tools, "patch", {"path": "page.md", "old_string": "nope", "new_string": "x"})
        raise AssertionError("should fail")
    except ToolError as exc:
        assert "not found" in str(exc)


def test_todo_roundtrip(tmp_path):
    tools = WorkspaceTools(tmp_path)
    dispatch(
        tools,
        "todo",
        {
            "items": [
                {"id": "1", "content": "Research", "status": "in_progress"},
                {"id": "2", "content": "Write", "status": "pending"},
            ]
        },
    )
    listing = dispatch(tools, "todo", {})
    assert "Research" in listing
    assert "in_progress" in listing


def test_clarify_uses_callback(tmp_path):
    tools = WorkspaceTools(tmp_path)
    asked = {}

    def ask(question, options=None):
        asked["q"] = question
        return "audience is marketers"

    out = dispatch(
        tools,
        "clarify",
        {"question": "Who is this for?"},
        ask_user=ask,
    )
    assert asked["q"] == "Who is this for?"
    assert "marketers" in out
