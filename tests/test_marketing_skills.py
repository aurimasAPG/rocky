from rocky.recipes import load_recipes
from rocky.skills import catalog_for_prompt, view_skill


def _steps():
    return {step["title"]: step["detail"] for step in load_recipes()["ads"].render_steps("goal")}


def test_a_worker_can_find_the_strategy_skill():
    row = next(r for r in catalog_for_prompt() if r["name"] == "marketing-strategy")
    assert "positioning" in row["description"]


def test_ad_goals_start_from_the_job_the_customer_wants_done():
    steps = _steps()
    assert "skill_view marketing-strategy" in steps["Facts"]
    assert "Job:" in steps["Facts"]
    assert "the Job line" in steps["Ads"]


def test_the_job_rule_is_in_the_skill_with_its_source():
    text = view_skill("marketing-strategy")
    assert "Define the market around a durable job" in text
    assert "Jobs to Be Done, pp. 54–56" in text


def test_ads_are_aimed_at_segments_defined_by_unmet_needs():
    steps = _steps()
    assert "`Segment: ` lines" in steps["Facts"] and "struggles to reach" in steps["Facts"]
    assert "one Segment line" in steps["Ads"] and "in its audience" in steps["Ads"]


def test_the_segment_rule_is_in_the_skill_with_its_source():
    text = view_skill("marketing-strategy")
    assert "Segment by patterns of unmet needs" in text
    assert "Jobs to Be Done, pp. 97–98" in text


def test_the_repositioning_rule_is_in_the_skill_with_its_source():
    text = view_skill("marketing-strategy")
    assert "Test repositioning before changing the product" in text
    assert "Jobs to Be Done, pp. 99–101" in text


def test_the_table_stakes_rule_is_in_the_skill_with_its_source():
    text = view_skill("marketing-strategy")
    assert "Protect category table stakes" in text
    assert "Jobs to Be Done, pp. 105–106" in text


def test_the_portfolio_rule_is_in_the_skill_with_its_source():
    text = view_skill("marketing-strategy")
    assert "Match an existing portfolio to segments" in text
    assert "Jobs to Be Done, pp. 113–115" in text


def test_a_worker_choosing_where_to_invest_can_find_the_skill():
    row = next(r for r in catalog_for_prompt() if r["name"] == "marketing-strategy")
    assert "investment" in row["description"]


def test_marketing_research_applies_the_growth_share_rule_when_investment_is_the_question():
    research = load_recipes()["marketing"].render_steps("goal")[0]["detail"]
    assert "asks where to invest" in research and "skill_view marketing-strategy" in research


def test_the_growth_share_rule_labels_estimates_and_cites_its_source():
    text = view_skill("marketing-strategy")
    assert "Question investment choices with the growth-share matrix" in text
    assert "label each position as an estimate" in text
    assert "Principles of Marketing, pp. 71–73" in text


def test_the_growth_direction_rule_is_in_the_skill_with_its_source():
    text = view_skill("marketing-strategy")
    assert "Separate market development from market penetration" in text
    assert "Principles of Marketing, pp. 75–76" in text

