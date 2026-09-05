from rocky.events import Event
from rocky.status import TRANSCRIPT_TYPES, status_line


def test_status_line_thinking_and_tools():
    assert status_line(Event(type="catalog", payload={"status": "fetching"})) == "fetching models"
    assert status_line(Event(type="critique", payload={})) == "thinking · critic"
    assert status_line(Event(type="plan", payload={})) == "thinking · plan"
    assert (
        status_line(Event(type="tool_call", payload={"name": "web_search", "arguments": {"query": "EEAT Meta Ads"}}))
        == "researching · EEAT Meta Ads"
    )
    assert (
        status_line(Event(type="tool_call", payload={"name": "write_file", "arguments": {"path": "article.md"}}))
        == "writing · article.md"
    )
    assert (
        status_line(Event(type="tool_call", payload={"name": "bash", "arguments": {"command": "pytest -q"}}))
        == "running · pytest -q"
    )
    assert status_line(Event(type="tool_result", payload={"name": "bash"})) == "thinking · worker"
    assert status_line(Event(type="done", payload={"passed": True})) == "done · passed"


def test_status_line_skips_cost():
    assert status_line(Event(type="cost", payload={"receipt": "$0.01"})) is None


def test_transcript_keeps_the_big_beats():
    assert "plan" in TRANSCRIPT_TYPES
    assert "verify" in TRANSCRIPT_TYPES
    assert "done" in TRANSCRIPT_TYPES
    assert "tool_call" not in TRANSCRIPT_TYPES
    assert "cost" not in TRANSCRIPT_TYPES


def test_done_status_line_names_the_deliverable():
    assert (
        status_line(Event(type="done", payload={"status": "passed_with_notes", "deliverable_path": "deliverables/ads.md", "deliverable_exists": True}))
        == "done · passed with notes · deliverables/ads.md"
    )
    assert status_line(Event(type="done", payload={"status": "unmet", "deliverable_path": "x.md", "deliverable_exists": False})) == "done · unmet"
