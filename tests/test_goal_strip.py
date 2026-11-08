import pytest
from fastapi.testclient import TestClient

from rocky.adboard import ad_board_tool, current_checks
from rocky.playbook import checks_for, set_switched_off, switched_off
from rocky.tools import WorkspaceTools
from rocky.webapp import app

AD_GOAL = "padaryk man Facebook reklamą"


@pytest.fixture
def served(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_WORKSPACES", str(tmp_path))
    monkeypatch.delenv("ROCKY_TOKEN", raising=False)
    return TestClient(app), tmp_path


def _preview(client, workspace, goal):
    return client.get("/api/playbook/preview", params={"goal": goal, "workspace": str(workspace)}).json()


def test_an_ad_goal_shows_what_rocky_will_check_in_its_language(served):
    client, workspace = served
    data = _preview(client, workspace, AD_GOAL)
    assert data["title"] == "Rocky patikrins:"
    assert "Ar auditorija pakankamai didelė, kad atsipirktų?" in [i["question"] for i in data["items"]]
    assert _preview(client, workspace, "write facebook ad copy for acme")["title"] == "Rocky will check:"


def test_a_goal_rocky_does_not_recognise_shows_nothing(served):
    client, workspace = served
    assert _preview(client, workspace, "kiek bus 5+5")["items"] == []


def test_only_elements_code_enforces_are_listed():
    assert all(e.codes and e.number for e in checks_for("ads"))
    assert {e.number for e in checks_for("ads")} == {1, 4, 5, 8, 9}


def test_switching_a_check_off_is_remembered_and_shown(served):
    client, workspace = served
    body = {"workspace": str(workspace), "number": 9, "off": True}
    assert client.post("/api/playbook/toggle", json=body).json() == {"off": [9]}
    assert next(i for i in _preview(client, workspace, AD_GOAL)["items"] if i["number"] == 9)["off"] is True
    assert client.post("/api/playbook/toggle", json={**body, "off": False}).json() == {"off": []}


def test_a_check_that_is_not_listed_cannot_be_switched_off(served):
    client, workspace = served
    assert client.post("/api/playbook/toggle", json={"workspace": str(workspace), "number": 7, "off": True}).status_code == 400


def test_a_folder_outside_the_allowed_ones_is_refused(served):
    client, _ = served
    assert client.get("/api/playbook/preview", params={"goal": AD_GOAL, "workspace": "/etc"}).status_code == 400


def _ad_with_gaps():
    return {
        "brand": "B",
        "url": "https://b.lt",
        "ads": [{"primary_text": "Tekstas su ženklais ąč", "headline": "A", "cta": "C", "mix": {"product": "p", "price": "q", "place": "r"}}],
        "table_stakes": [{"outcome": "o", "minimum": "m", "status": "met"}],
    }


def test_a_switched_off_check_stops_producing_findings(tmp_path):
    tools = WorkspaceTools(tmp_path)
    ad_board_tool(tools, _ad_with_gaps())
    assert "segment_gap" in {c.code for c in current_checks(tools)}
    set_switched_off(tmp_path, 9, True)
    assert "segment_gap" not in {c.code for c in current_checks(tools)}
    assert switched_off(tmp_path) == {9}


def test_a_corrupt_settings_file_switches_nothing_off(tmp_path):
    (tmp_path / ".rocky").mkdir()
    (tmp_path / ".rocky" / "playbook.json").write_text("not json", encoding="utf-8")
    assert switched_off(tmp_path) == set()
