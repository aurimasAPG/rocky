import pytest
from fastapi.testclient import TestClient

from rocky.events import Event
from rocky.playbook import FIX_CAP_EUR, library, search
from rocky.runs import append_event, create_run, run_dir, write_meta
from rocky.webapp import app

LIST = """# 500 concepts

## Contents

| Entries | Topic |
| --- | --- |
| 1–2 | [Media](#media) |

## Media planning and budgets

1. **Cut wasted reach.** When cost per lead climbs, drop placements that reach non-buyers. [Media Book, pp. 10–11](Marketing%20Books/x.pdf#page=10)

2. **Fix the frequency.** Limit how often one person sees an ad. [Media Book, p. 12](Marketing%20Books/x.pdf#page=12)

## Search, content and digital channels

3. **Answer the query.** Write the page around what the searcher asked. [SEO Book, p. 5](Marketing%20Books/y.pdf#page=5)

---

The source links open local editions.
"""


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ROCKY_PLAYBOOK", raising=False)
    (tmp_path / "500list.md").write_text(LIST, encoding="utf-8")
    return tmp_path


def _titles(entries):
    return [e.title for e in entries]


def test_the_list_is_read_with_topics_and_citations_without_links(workspace):
    entries = library(workspace)
    assert [e.number for e in entries] == [1, 2, 3]
    assert entries[0].title == "Cut wasted reach" and entries[0].topic == "Media planning and budgets"
    assert entries[0].source == "Media Book, pp. 10–11" and "Marketing%20Books" not in entries[0].body


def test_without_the_list_it_offers_what_rocky_already_applies(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ROCKY_PLAYBOOK", raising=False)
    assert [e.number for e in library(tmp_path)] == list(range(1, 11))


def test_a_situation_in_english_finds_the_entry(workspace):
    assert _titles(search(library(workspace), "my ads are too expensive"))[0] == "Cut wasted reach"


def test_a_situation_in_lithuanian_finds_the_entry(workspace):
    assert "Cut wasted reach" in _titles(search(library(workspace), "mano reklamos per brangios"))


def test_an_entry_matching_every_word_ranks_first(workspace):
    assert _titles(search(library(workspace), "searcher page ad"))[0] == "Answer the query"


def test_a_topic_filters_the_results(workspace):
    assert _titles(search(library(workspace), "", topic="Search, content and digital channels")) == ["Answer the query"]


def test_an_empty_search_never_lists_everything(workspace):
    assert search(library(workspace), "") == []


def test_results_are_limited(workspace):
    assert len(search(library(workspace), "ads page", limit=1)) == 1


@pytest.fixture
def served(workspace, monkeypatch):
    monkeypatch.setenv("ROCKY_WORKSPACES", str(workspace))
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.delenv("ROCKY_TOKEN", raising=False)
    spawned = []
    monkeypatch.setattr("rocky.webapp.spawn_worker", spawned.append)
    meta = create_run(workspace=workspace, goal="ad", budget_eur=1.0, max_loops=1, include_server_tools=True)
    folder = run_dir(workspace, meta["id"])
    append_event(folder, Event(type="assessed", payload={"goal": "ad"}))
    append_event(folder, Event(type="plan", payload={"deliverable_path": "deliverables/ads.html", "steps": []}))
    meta.update(status="passed")
    write_meta(folder, meta)
    return TestClient(app), meta["id"], workspace, spawned


def test_the_search_route_returns_topics_and_results(served):
    client, _, workspace, _ = served
    data = client.get("/api/playbook/search", params={"q": "expensive", "workspace": str(workspace)}).json()
    assert data["topics"] == ["Media planning and budgets", "Search, content and digital channels"]
    assert data["results"][0]["title"] == "Cut wasted reach" and data["results"][0]["source"] == "Media Book, pp. 10–11"


def test_apply_starts_one_capped_follow_up_built_from_the_entry(served):
    client, run_id, workspace, spawned = served
    res = client.post(f"/api/runs/{run_id}/apply", json={"workspace": str(workspace), "number": 2})
    assert res.status_code == 200
    assert spawned[0]["budget_eur"] == FIX_CAP_EUR and "Fix the frequency" in spawned[0]["follow_up"]


def test_apply_refuses_an_unknown_entry(served):
    client, run_id, workspace, spawned = served
    assert client.post(f"/api/runs/{run_id}/apply", json={"workspace": str(workspace), "number": 999}).status_code == 404
    assert not spawned
