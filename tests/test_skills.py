
from rocky.skills import catalog_for_prompt, list_skills, view_skill
from rocky.tools import WorkspaceTools, dispatch, worker_tools


def test_office_skills_are_listed():
    names = {s["name"] for s in list_skills()}
    assert {"xlsx", "docx", "pptx", "pdf"} <= names


def test_bundled_research_and_marketing_skills():
    names = {s["name"] for s in list_skills()}
    assert "deep-research" in names
    assert "marketing-pipeline" in names
    text = view_skill("marketing-pipeline")
    assert "VOC" in text
    assert "pptx" in text
    assert "xlsx" in text


def test_home_allowlist_skips_unrelated(tmp_path, monkeypatch):
    home = tmp_path / "claude" / "skills"
    for name, desc in (
        ("copywriting", "write copy"),
        ("voc-to-copy", "mine VOC"),
        ("apg-pitch-deck", "APG deck"),
        ("barbaras-rabarbaras", "kids story"),
        ("lietuviu-kalbos-rasyba", "LT spelling"),
    ):
        folder = home / name
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\n",
            encoding="utf-8",
        )
    monkeypatch.setattr("rocky.skills.home_skills_dir", lambda: home)
    names = {s["name"] for s in list_skills()}
    assert "copywriting" in names
    assert "voc-to-copy" in names
    assert "apg-pitch-deck" not in names
    assert "barbaras-rabarbaras" not in names
    assert "lietuviu-kalbos-rasyba" in names
    assert "xlsx" in names


def test_rocky_skills_env_loads_unlisted(tmp_path, monkeypatch):
    extra = tmp_path / "extra"
    folder = extra / "custom-foo"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: custom-foo\ndescription: extra\n---\n# Foo\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ROCKY_SKILLS", str(extra))
    names = {s["name"] for s in list_skills()}
    assert "custom-foo" in names


def test_legacy_orche_skills_env_still_works(tmp_path, monkeypatch):
    extra = tmp_path / "legacy"
    folder = extra / "legacy-bar"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        "---\nname: legacy-bar\ndescription: extra\n---\n# Bar\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("ROCKY_SKILLS", raising=False)
    monkeypatch.setenv("ORCHE_SKILLS", str(extra))
    names = {s["name"] for s in list_skills()}
    assert "legacy-bar" in names


def test_catalog_for_prompt_has_names():
    rows = catalog_for_prompt()
    names = {r["name"] for r in rows}
    assert "deep-research" in names
    assert all("description" in r for r in rows)


def test_skill_view_returns_instructions():
    text = view_skill("xlsx")
    assert "openpyxl" in text
    assert "write_file" in text.lower() or "UTF-8" in text


def test_skills_tools_registered():
    names = {spec["function"]["name"] for spec in worker_tools(include_server=False)}
    assert "skills_list" in names
    assert "skill_view" in names


def test_dispatch_skills_list():
    out = dispatch(WorkspaceTools("."), "skills_list", {})
    assert "xlsx" in out
    assert "docx" in out


def test_office_read_xlsx(tmp_path):
    from openpyxl import Workbook

    path = tmp_path / "t.xlsx"
    wb = Workbook()
    wb.active.append(["col", "n"])
    wb.active.append(["a", 1])
    wb.save(path)
    tools = WorkspaceTools(tmp_path)
    text = dispatch(tools, "office_read", {"path": "t.xlsx"})
    assert "col" in text
    assert "a" in text
