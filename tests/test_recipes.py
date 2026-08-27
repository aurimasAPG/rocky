import pytest

from rocky.recipes import (
    Recipe,
    find_recipe,
    from_dict,
    load_recipes,
    parse_recipe,
    recipe_tool,
    save_recipe,
    to_toml,
)
from rocky.tools import ToolError

SIMPLE = """
name = "seo-audit"
description = "Crawl, score, report."
deliverable_path = "deliverables/seo-audit.docx"
cost_multiplier = 1.4

[match]
domains = ["research"]
hints = ["seo audit", "auditas"]

[[steps]]
id = 1
title = "Crawl"
depends_on = []
requires = ["crawl"]
detail = "Crawl the site for: {goal}"

[[steps]]
id = 2
title = "Report"
depends_on = [1]
requires = ["docx", "report"]
detail = "skill_view docx. Write the report."
"""


def test_bundled_marketing_recipe_loads():
    recipes = load_recipes()
    m = recipes["marketing"]
    assert [s["title"] for s in m.steps] == ["Research", "VOC", "Copy", "Deck and spreadsheet"]
    assert m.deliverable_path.endswith(".pptx")
    assert m.cost_multiplier == 1.8
    assert "reklam" in m.hints


def test_parse_and_render_substitutes_goal():
    r = parse_recipe(SIMPLE, "test")
    steps = r.render_steps("audit example.lt")
    assert steps[0]["detail"] == "Crawl the site for: audit example.lt"
    assert steps[1]["depends_on"] == [1]
    assert "requires" not in steps[0]


def test_parse_rejects_bad_recipes():
    with pytest.raises(ValueError):
        parse_recipe('name = "Bad Name"\n[[steps]]\ndetail = "x"\n')
    with pytest.raises(ValueError):
        parse_recipe('name = "ok"\n')
    with pytest.raises(ValueError):
        parse_recipe("not toml at all = = =")


def test_workspace_recipes_override_bundled_and_env(tmp_path, monkeypatch):
    envdir = tmp_path / "shared"
    envdir.mkdir()
    (envdir / "seo-audit.toml").write_text(SIMPLE, encoding="utf-8")
    (envdir / "marketing.toml").write_text(SIMPLE.replace('"seo-audit"', '"marketing"'), encoding="utf-8")
    monkeypatch.setenv("ROCKY_RECIPES", str(envdir))
    ws = tmp_path / "ws"
    (ws / ".rocky" / "recipes").mkdir(parents=True)
    (ws / ".rocky" / "recipes" / "seo-audit.toml").write_text(
        SIMPLE.replace("Crawl, score, report.", "Local version"), encoding="utf-8"
    )
    recipes = load_recipes(ws)
    assert recipes["seo-audit"].description == "Local version"
    assert recipes["marketing"].description == "Crawl, score, report."  # env replaced bundled


def test_find_recipe_explicit_domain_and_hint():
    recipes = {"seo-audit": parse_recipe(SIMPLE), **{k: v for k, v in load_recipes().items()}}
    assert find_recipe("recipe:seo-audit do something", {}, recipes).name == "seo-audit"
    assert find_recipe("anything", {"domain": "marketing"}, recipes).name == "marketing"
    assert find_recipe("padaryk svetainės seo audit", {"domain": "mixed"}, recipes).name == "seo-audit"
    assert find_recipe("fix the tests", {"domain": "code"}, recipes) is None


def test_apply_replaces_thin_plan_and_keeps_full_plan():
    r = parse_recipe(SIMPLE)
    thin = {"steps": [{"id": 1, "title": "do", "detail": "x", "depends_on": []}], "deliverable_path": "a.md"}
    out = r.apply(thin, "audit x")
    assert [s["title"] for s in out["steps"]] == ["Crawl", "Report"]
    assert out["deliverable_path"] == "deliverables/seo-audit.docx"
    full = {
        "steps": [
            {"id": 1, "title": "Crawl pages", "detail": "crawl", "depends_on": []},
            {"id": 2, "title": "Write report", "detail": "skill_view docx", "depends_on": [1]},
        ],
        "deliverable_path": "out/report.md",
    }
    out = r.apply(full, "audit x")
    assert [s["title"] for s in out["steps"]] == ["Crawl pages", "Write report"]
    assert out["deliverable_path"] == "deliverables/seo-audit.docx"  # suffix corrected


def test_toml_roundtrip():
    r = parse_recipe(SIMPLE)
    again = parse_recipe(to_toml(r))
    assert again.as_dict() == r.as_dict()


def test_recipe_tool_list_view_save(tmp_path):
    listing = recipe_tool(tmp_path, {"action": "list"})
    assert "marketing" in listing
    view = recipe_tool(tmp_path, {"action": "view", "name": "marketing"})
    assert view.startswith('name = "marketing"')
    with pytest.raises(ToolError):
        recipe_tool(tmp_path, {"action": "view", "name": "nope"})
    saved = recipe_tool(
        tmp_path,
        {
            "action": "save",
            "recipe": {
                "name": "monthly-report",
                "description": "Numbers to a docx.",
                "deliverable_path": "deliverables/report.docx",
                "match": {"hints": ["monthly report", "mėnesio ataskait"]},
                "steps": [
                    {"title": "Collect", "detail": "gather numbers for {goal}", "requires": ["collect"]},
                    {"title": "Write", "detail": "skill_view docx", "depends_on": [1], "requires": ["docx"]},
                ],
            },
        },
    )
    assert "monthly-report.toml" in saved
    assert (tmp_path / ".rocky" / "recipes" / "monthly-report.toml").exists()
    assert "monthly-report" in recipe_tool(tmp_path, {"action": "list"})
    assert find_recipe("padaryk mėnesio ataskaitą", {}, load_recipes(tmp_path)).name == "monthly-report"
    with pytest.raises(ToolError):
        recipe_tool(tmp_path, {"action": "save", "recipe": {"name": "x"}})
    with pytest.raises(ToolError):
        recipe_tool(tmp_path, {"action": "explode"})


def test_from_dict_defaults_ids_and_depends():
    r = from_dict({"name": "two", "steps": [{"detail": "a"}, {"detail": "b", "depends_on": [1]}]})
    assert [s["id"] for s in r.steps] == [1, 2]
    assert isinstance(r, Recipe)
