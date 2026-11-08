from rocky.pipeline import canonical_marketing_steps, ensure_pipeline, is_marketing


def test_marketing_domain_is_marketing():
    assert is_marketing("anything", {"domain": "marketing"})
    assert is_marketing("write Meta Ads copy for a shop")
    assert is_marketing("paruošk Google Ads kampaniją")
    assert not is_marketing("fix the failing pytest", {"domain": "code"})


def test_thin_plan_becomes_five_stages():
    plan = ensure_pipeline(
        {
            "steps": [{"id": 1, "title": "Do it", "detail": "write ads", "depends_on": []}],
            "deliverable_path": "out.md",
        },
        "write landing page copy",
        {"domain": "marketing"},
    )
    titles = [s["title"].lower() for s in plan["steps"]]
    assert titles == ["research", "voc", "copy", "deck and spreadsheet", "visuals"]
    blob = " ".join(s["detail"].lower() for s in plan["steps"])
    assert "deep-research" in blob
    assert "voc-to-copy" in blob
    assert "copywriting" in blob
    assert "pptx" in blob and "xlsx" in blob
    assert "image_generate" in blob
    assert plan["deliverable_path"].endswith(".pptx")
    waves = [s["depends_on"] for s in plan["steps"]]
    assert waves == [[], [1], [2], [3], [3]]  # deck and visuals both wait on copy, run in parallel


def test_keeps_complete_marketing_plan():
    steps = [
        {"id": 1, "title": "Research market", "detail": "skill_view deep-research", "depends_on": []},
        {"id": 2, "title": "VOC mining", "detail": "skill_view voc-to-copy", "depends_on": [1]},
        {"id": 3, "title": "Write copy", "detail": "skill_view copywriting", "depends_on": [2]},
        {"id": 4, "title": "Package pptx and xlsx", "detail": "skill_view pptx and xlsx", "depends_on": [3]},
        {"id": 5, "title": "Ad visuals", "detail": "image_generate three creatives", "depends_on": [3]},
    ]
    out = ensure_pipeline(
        {"steps": steps, "deliverable_path": "deck.pptx"},
        "ads",
        {"domain": "marketing"},
    )
    assert [s["title"] for s in out["steps"]] == [s["title"] for s in steps]


def test_non_marketing_plan_untouched():
    original = {
        "steps": [{"id": 1, "title": "fix tests", "detail": "pytest", "depends_on": []}],
        "deliverable_path": "x",
    }
    out = ensure_pipeline(original, "fix failing tests", {"domain": "code"})
    assert out["steps"][0]["title"] == "fix tests"
    assert out["deliverable_path"] == "x"


def test_canonical_steps_mention_goal():
    steps = canonical_marketing_steps("EEAT Meta Ads article")
    assert "EEAT Meta Ads article" in steps[0]["detail"]
