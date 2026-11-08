import pytest

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


def test_the_usable_segment_rule_is_in_the_skill_with_its_source():
    text = view_skill("marketing-strategy")
    assert "Check whether a segment is commercially usable" in text
    assert "Principles of Marketing, pp. 188–189" in text


def test_a_worker_mapping_competitors_can_find_the_skill():
    row = next(r for r in catalog_for_prompt() if r["name"] == "marketing-strategy")
    assert "mapping competitors" in row["description"]


def test_marketing_research_applies_the_perceptual_map_rule_when_positioning_is_the_question():
    research = load_recipes()["marketing"].render_steps("goal")[0]["detail"]
    assert "positioning or a comparison with competitors" in research and "perceptual-map rule" in research


def test_the_perceptual_map_rule_refuses_to_pass_opinion_off_as_buyers():
    text = view_skill("marketing-strategy")
    assert "Build a perceptual map from buyers' judgments" in text
    assert "Label the map a hypothesis" in text and "An empty corner is a question" in text
    assert "Principles of Marketing, pp. 197–199" in text



def test_a_worker_can_find_the_numbers_skill():
    row = next(r for r in catalog_for_prompt() if r["name"] == "marketing-numbers")
    assert "unit economics" in row["description"] and "A/B-test" in row["description"] and "Pricing" in row["description"]


def test_pricing_and_test_goals_load_the_numbers_skill():
    research = load_recipes()["marketing"].render_steps("goal")[0]["detail"]
    assert "skill_view marketing-numbers" in research and "unit_economics or experiment_plan" in research
    facts = _steps()["Facts"]
    assert "skill_view marketing-numbers" in facts and "unit_economics" in facts


def test_the_numbers_skill_separates_margin_from_markup_and_handles_missing_inputs():
    text = view_skill("marketing-numbers")
    assert "Never do the arithmetic yourself" in text
    assert "A 40 % margin is a 67 % markup" in text
    assert "Never pick one row silently" in text
    assert "decision_rule" in text and "#488" in text and "#464" in text


# Judgment rules from the 500list ledger: (skill, section title, source line, one phrase the rule
# makes the worker write or refuse). Each row is one playbook entry; the id is in the test id.
RULE_SECTIONS = [
    # marketing-strategy
    ("405", "marketing-strategy", "Fund the introduction, not only the conversion", "Effectiveness in Context, pp. 30–31", "Spend split:"),
    ("127", "marketing-strategy", "Look inside the company before funding a feature", "Jobs to Be Done, p. 118", "Internal solution:"),
    ("116", "marketing-strategy", "Test affect and capability separately", "The 5 Principles of Growth in B2B Marketing, pp. 25–27", "## Mechanism test"),
    ("107", "marketing-strategy", "Show verified proof where the buyer has no experience", "Decoding Decisions, p. 50", "Do not invent ratings"),
    ("177", "marketing-strategy", "Judge a preview on resolved uncertainty, not clicks", "World Wide Rave, pp. 153–154", "resolution metric"),
    ("192", "marketing-strategy", "Invite a fair comparison instead of claiming superiority", "Scientific Advertising, p. 34", "`Compare:` line"),
    ("147", "marketing-strategy", "Map what the buyer feels at each touchpoint", "eMarketing, pp. 46–47", "runs/notes/journey.md"),
    ("73", "marketing-strategy", "Compare the settings where the product is accepted and rejected", "My Life in Advertising, p. 109", "## Setting contrast"),
    ("87", "marketing-strategy", "List the problems a new behaviour creates", "New Rules for the New Economy, pp. 153–155", "## Second-order opportunities"),
    ("130", "marketing-strategy", "Walk the configurator as a first-time user", "Democratizing Innovation, pp. 170–174", "runs/notes/feasibility.md"),
    ("172", "marketing-strategy", "Prove a physical item can be made before promising it", "My Life in Advertising, pp. 52–54", "runs/notes/production.md"),
    ("175", "marketing-strategy", "Service behaviour starts with managers", "Publicity, p. 47", "## Managers first"),
    ("163", "marketing-strategy", "Route public complaints to someone who can resolve them", "eMarketing, pp. 429–431", "OWNER NOT ASSIGNED"),
    ("78", "marketing-strategy", "Test a name bare, without the pack or the slogan", "Scientific Advertising, pp. 91–93", "hipotezė, ne pirkėjų atsakymai"),
    ("151", "marketing-strategy", "Test the flow on paper before code", "eMarketing, pp. 160–161", "Stebėtas kitas žingsnis"),
    ("209", "marketing-strategy", "Design the transition to a higher price", "Getting Real, p. 105", "date of first changed bill"),
    ("212", "marketing-strategy", "Price a custom offer from two numbers", "Democratizing Innovation, pp. 54–58", "fit_wtp"),
    ("252", "marketing-strategy", "Film the task people cannot follow in writing", "Winning the Zero Moment of Truth, pp. 50–51", "Completion check:"),
    ("360", "marketing-strategy", "Treat an objection as a pointer, not a question", "Principles of Marketing, pp. 528–529", "runs/notes/objection.md"),
    ("364", "marketing-strategy", "Locate the source of a channel conflict", "Principles of Marketing, pp. 607–608", "## Channel conflict"),
    ("371", "marketing-strategy", "Name the obstacle each piece of partner support removes", "The Macintosh Way, pp. 117–118", "to be agreed"),
    # customer-research
    ("48", "customer-research", "Interview the people who live with the purchase", "Jobs to Be Done, pp. 84–86", "Role: budget owner"),
    ("56", "customer-research", "Inspect the workaround itself", "Democratizing Innovation, pp. 49–54", "## Workarounds"),
    ("57", "customer-research", "Observe the use context the spec leaves out", "Democratizing Innovation, pp. 82–85", "## Use context"),
    ("59", "customer-research", "Search an analogous market for a harder version of the problem", "Democratizing Innovation, pp. 149–151", "## Analogous markets"),
    ("60", "customer-research", "Find rare experts through successive referrals", "Democratizing Innovation, pp. 151–152", "runs/notes/experts.md"),
    ("71", "customer-research", "Put a draft in front of a trade buyer", "My Life in Advertising, pp. 72–73", "runs/notes/expert_review.md"),
    ("86", "customer-research", "Ask what customers would remove", "Getting Real, p. 50", "Jei iš X galėtumėte pašalinti vieną žingsnį ar funkciją"),
    ("92", "customer-research", "Include nonconsumers in entry-price research", "Jobs to Be Done, pp. 74–75", "## Nonconsumers"),
    ("101", "customer-research", "Measure digital influence on store sales", "Winning the Zero Moment of Truth, pp. 37–38", "Offline influence:"),
    ("121", "customer-research", "Measure familiarity and ad memory apart", "The Psychology of Advertising, pp. 160–161", "Reklamos prisiminimas"),
    ("10", "customer-research", "Ask buyers for the map's axes before its placements", "Principles of Marketing, pp. 197–199", "## Perceptual map (buyers)"),
    # pr-response
    ("301", "pr-response", "Give staff three things to say and one way to say who they are", "World Wide Rave, pp. 113–115", "Dirbu X, rašau savo vardu"),
    ("302", "pr-response", "Answer criticism in the thread where it appears, with a name", "World Wide Rave, pp. 138–141", "[Vardas, Pareigos]"),
    ("296", "pr-response", "Let reporters see for themselves and say who pays", "How We Advertised America, pp. 269–270", "Costs covered"),
    ("273", "pr-response", "Design participation around why people make things", "Democratizing Innovation, pp. 76–77", "recognition alone fails"),
    ("274", "pr-response", "Give contributors a reason to share what they built", "Democratizing Innovation, pp. 102–104", "## Contributor incentives"),
]


@pytest.mark.parametrize(
    "skill, title, source, marker",
    [row[1:] for row in RULE_SECTIONS],
    ids=[f"{row[0]}-{row[1]}" for row in RULE_SECTIONS],
)
def test_each_ledger_rule_is_a_section_with_its_source_and_a_concrete_instruction(skill, title, source, marker):
    text = view_skill(skill)
    assert f"## {title}\n" in text
    assert f"Source: {source}." in text
    assert marker in text


def test_every_rule_section_stays_short_enough_to_be_read_by_a_worker():
    for skill in ("marketing-strategy", "customer-research", "pr-response"):
        for section in view_skill(skill).split("\n## ")[1:]:
            assert len(section.split()) <= 180, section.splitlines()[0]


def test_a_worker_planning_interviews_can_find_the_customer_research_skill():
    row = next(r for r in catalog_for_prompt() if r["name"] == "customer-research")
    assert "interviews" in row["description"] and "nonconsumers" in row["description"]
    assert "what to ask" in row["description"]


def test_a_worker_answering_critics_or_the_press_can_find_the_pr_skill():
    row = next(r for r in catalog_for_prompt() if r["name"] == "pr-response")
    assert "criticism" in row["description"] and "press" in row["description"]
    assert "staff" in row["description"] and "community" in row["description"]



def test_recipes_load_the_new_rule_skills_when_the_goal_matches():
    steps = {s["title"]: s["detail"] for s in load_recipes()["marketing"].render_steps("goal")}
    assert "skill_view customer-research" in steps["Research"] and "`## Analogous markets`" in steps["Research"]
    assert "`Role: operator`" in steps["VOC"] and "`## Nonconsumers`" in steps["VOC"]
    assert "skill_view pr-response" in steps["Copy"] and "named spokesperson" in steps["Copy"]
    assert "runs/notes/production.md" in steps["Deck and spreadsheet"]
    ads = _steps()
    assert "`Compare:` line" in ads["Facts"] and "never a superiority claim" in ads["Ads"]
