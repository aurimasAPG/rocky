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



class _VisionFake:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def complete(self, *, model, messages, **kwargs):
        self.calls.append(messages)
        return type("Completion", (), {"content": self.text})()


def test_vision_analyze_checks_the_brief(tmp_path):
    tools = WorkspaceTools(tmp_path)
    (tmp_path / "deliverables").mkdir()
    (tmp_path / "deliverables" / "visual-1.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    llm = _VisionFake('{"verdict": "fail", "people_seen": "a couple in their twenties", "mismatches": ["people shown are a young couple; audience is women 50+ with a friend"]}')
    out = dispatch(
        tools,
        "vision_analyze",
        {"path": "deliverables/visual-1.png", "brief": {"style": "photo", "subject": "spa pool at dusk", "audience": "women 50+ with a friend", "must_not": ["young couple"]}},
        llm=llm,
    )
    assert "brief_check: fail" in out and "women 50+" in out and "Regenerate once" in out
    question = llm.calls[0][-1]["content"][0]["text"]
    assert "Must not show: young couple" in question and "women 50+ with a friend" in question


def test_vision_analyze_without_brief_is_unchanged(tmp_path):
    tools = WorkspaceTools(tmp_path)
    (tmp_path / "a.png").write_bytes(b"\x89PNG")
    out = dispatch(tools, "vision_analyze", {"path": "a.png", "prompt": "what is this"}, llm=_VisionFake("a square"))
    assert out.endswith("a square") and "brief_check" not in out
