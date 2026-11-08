import json

import pytest

from rocky.adboard import (
    BOARD_PATH,
    FACTS_PATH,
    PACK_PATH,
    Check,
    ad_board_tool,
    check_pack,
    normalize,
    ready,
    refresh,
    render,
    verifier_brief,
)
from rocky.catalog import Model
from rocky.engine import _final_status, orchestrate
from rocky.llm import Completion, ToolCall
from rocky.recipes import find_recipe, load_recipes
from rocky.tools import WorkspaceTools, dispatch

PLAN = {"deliverable_path": BOARD_PATH}
SOURCE = "https://apgmedia.lt/apie"


def _pack(**ad):
    return {
        "brand": "APG Media",
        "url": "https://apgmedia.lt",
        "ads": [
            {
                "primary_text": "Rinkodara, kuri atsiperka.",
                "headline": "Augimas be spėlionių",
                "description": "Pokalbis nemokamai",
                "cta": "Sužinoti daugiau",
                "angle": "Rezultatas",
                "audience": "E. parduotuvių vadovai",
                "mix": {"product": "Reklamos valdymas", "price": "Nuo 990 € per mėnesį", "place": "Internetu, Lietuvoje"},
                **ad,
            }
        ],
        "claims": [],
        "table_stakes": [{"outcome": "Atsakymas į užklausą", "minimum": "Per vieną darbo dieną", "status": "met"}],
    }


def _tools(tmp_path, facts=f"200+ prekių ženklų — {SOURCE}\n"):
    tools = WorkspaceTools(tmp_path)
    if facts is not None:
        tools.write_file(FACTS_PATH, facts)
    return tools


def _codes(checks, level):
    return [c.code for c in checks if c.level == level]


def _check(pack, facts=None, images=None):
    return check_pack(normalize(pack), facts, images if images is not None else {1: "x"})


def test_a_clean_ad_has_no_problems():
    assert _check(_pack()) == []


def test_a_number_without_a_source_blocks_the_ad():
    checks = _check(_pack(primary_text="Dirbame su 200 prekių ženklų."))
    assert _codes(checks, "block") == ["number_unsourced"]
    assert checks[0].ad == 1 and checks[0].value == "200"


def test_a_number_with_a_source_passes():
    pack = _pack(primary_text="Dirbame su 200 prekių ženklų.")
    pack["claims"] = [{"text": "200+ prekių ženklų", "source": SOURCE}]
    assert _check(pack) == []


def test_digits_in_the_brand_name_are_not_a_claim():
    pack = _pack(primary_text="Studio 54 kviečia.")
    pack["brand"] = "Studio 54"
    assert _check(pack) == []


def test_a_claim_needs_a_real_link():
    pack = _pack(primary_text="Dirbame su 200 prekių ženklų.")
    pack["claims"] = [{"text": "200 prekių ženklų", "source": "mūsų svetainė"}]
    assert sorted(_codes(_check(pack), "block")) == ["number_unsourced", "source_bad"]


def test_a_source_the_research_never_saw_is_refused():
    pack = _pack(primary_text="Dirbame su 200 prekių ženklų.")
    pack["claims"] = [{"text": "200 prekių ženklų", "source": "https://made-up.example/x"}]
    assert "source_unknown" in _codes(_check(pack, facts=f"faktas {SOURCE}"), "block")


def test_a_source_matches_without_scheme_www_or_trailing_slash():
    pack = _pack(primary_text="Dirbame su 200 prekių ženklų.")
    pack["claims"] = [{"text": "200 prekių ženklų", "source": "http://www.apgmedia.lt/apie/"}]
    assert _check(pack, facts=f"faktas {SOURCE}") == []


def test_long_copy_warns_and_does_not_block():
    checks = _check(_pack(headline="x" * 52))
    assert _codes(checks, "block") == [] and _codes(checks, "warn") == ["too_long"]
    assert checks[0].n == 52 and checks[0].limit == 40


def test_an_absolute_promise_warns():
    assert _codes(_check(_pack(headline="Garantuojame rezultatą")), "warn") == ["promise"]


def test_required_fields_and_url_block():
    pack = _pack(cta="")
    pack["url"] = "javascript:alert(1)"
    assert sorted(_codes(_check(pack), "block")) == ["bad_url", "missing_field"]


def test_no_ads_blocks():
    pack = _pack()
    pack["ads"] = []
    assert _codes(_check(pack, images={}), "block") == ["no_ads"]


def _stakes(*statuses):
    pack = _pack()
    pack["table_stakes"] = [{"outcome": f"Rezultatas {s}", "minimum": "Minimumas", "status": s} for s in statuses]
    return pack


def test_an_unmet_table_stake_warns_and_names_the_outcome():
    checks = _check(_stakes("met", "unmet"))
    assert _codes(checks, "block") == [] and _codes(checks, "warn") == ["stake_unmet"]
    assert checks[0].value == "Rezultatas unmet"


def test_met_and_unknown_table_stakes_do_not_warn():
    assert _check(_stakes("met", "unknown")) == []


def test_listing_no_table_stakes_warns():
    pack = _pack()
    pack["table_stakes"] = []
    assert _codes(_check(pack), "warn") == ["no_stakes"]


def test_an_unknown_stake_status_counts_as_unknown():
    assert normalize(_stakes("maybe"))["table_stakes"][0]["status"] == "unknown"


def test_a_missed_minimum_is_flagged_at_the_top_and_listed_before_the_evidence():
    pack = normalize(_stakes("unmet"))
    page = render(pack, check_pack(pack, None, {1: "x"}), {1: "x"})
    assert '<ul class="alerts"><li>„Rezultatas unmet“' in page
    assert page.index("Prieš leidžiant biudžetą") < page.index("Įrodymai")
    assert '<td class="unmet">Nepasiekta</td>' in page


def test_the_recipe_collects_table_stakes_and_keeps_ads_off_them():
    steps = {step["title"]: step["detail"] for step in load_recipes()["ads"].render_steps("goal")}
    assert "`Table stake: `" in steps["Facts"] and "minimum acceptable level" in steps["Facts"]
    assert "table_stakes" in steps["Ads"] and "No ad leads on a table stake" in steps["Ads"]


def _two_ads(*roles):
    pack = _pack()
    pack["ads"] = [{**pack["ads"][0], "test_role": role} for role in roles]
    return _check(pack, images={1: "x", 2: "x"})


def test_a_set_of_ads_without_a_control_and_a_challenger_warns():
    assert _codes(_two_ads("", ""), "warn") == ["no_test"]
    assert _codes(_two_ads("control", "control"), "warn") == ["no_test"]
    assert _codes(_two_ads("challenger", "challenger"), "warn") == ["no_test"]


def test_a_control_against_a_challenger_is_a_ready_test():
    assert _two_ads("control", "challenger") == []


def test_one_ad_needs_no_test():
    assert _check(_pack()) == []


def test_an_unknown_test_role_is_dropped():
    assert normalize(_pack(test_role="winner"))["ads"][0]["test_role"] == ""
    assert normalize(_pack(test_role="Control"))["ads"][0]["test_role"] == "control"


def test_the_page_labels_the_control_and_the_challenger():
    page = render(normalize(_pack(test_role="control")), [], {})
    assert "Kontrolinė: dabartinė žinutė" in page
    assert "Nauja žinutė" in render(normalize(_pack(test_role="challenger")), [], {})


def test_the_recipe_runs_the_real_pitch_against_outcomes_already_delivered():
    steps = {step["title"]: step["detail"] for step in load_recipes()["ads"].render_steps("goal")}
    assert "`Pitch: `" in steps["Facts"]
    assert "test_role control" in steps["Ads"] and "already delivers" in steps["Ads"]


def test_an_ad_with_no_offer_behind_it_warns_for_each_gap():
    checks = _check(_pack(mix={"product": "Auditas"}))
    assert _codes(checks, "block") == [] and _codes(checks, "warn") == ["mix_missing", "mix_missing"]
    assert [c.field for c in checks] == ["price", "place"]


def test_the_offer_is_shown_beside_the_ad():
    page = render(normalize(_pack()), [], {})
    assert "Kiek kainuoja" in page and "Nuo 990 € per mėnesį" in page and "Internetu, Lietuvoje" in page


def test_a_gap_in_the_offer_is_named_on_the_page():
    pack = normalize(_pack(mix={"product": "Auditas", "price": "Nemokamai"}))
    page = render(pack, check_pack(pack, None, {1: "x"}), {1: "x"})
    assert "kur pirkėjas tai gauna" in page


def test_the_recipe_asks_for_the_offer_and_where_buyers_get_it():
    steps = {step["title"]: step["detail"] for step in load_recipes()["ads"].render_steps("goal")}
    assert "where and how buyers get it" in steps["Facts"]
    assert "product, price, place" in steps["Ads"]


def test_a_mix_sent_as_a_json_string_still_works():
    pack = _pack()
    pack["ads"][0]["mix"] = json.dumps({"product": "A", "price": "B", "place": "C"})
    assert normalize(pack)["ads"][0]["mix"] == {"product": "A", "price": "B", "place": "C"}


def test_nested_arrays_sent_as_json_strings_still_work():
    pack = _pack()
    pack["ads"] = json.dumps(pack["ads"])
    assert normalize(pack)["ads"][0]["headline"] == "Augimas be spėlionių"


def test_render_escapes_everything_a_model_or_a_page_wrote():
    evil = '<script>alert(1)</script>"><img src=x onerror=alert(2)>'
    pack = normalize(_pack(primary_text=evil, headline=evil, angle=evil, audience=evil, description=evil, cta=evil))
    pack["brand"] = evil
    pack["claims"] = [{"text": evil, "source": evil}]
    page = render(pack, [Check("warn", "promise", ad=1, field="headline", value=evil)], {})
    assert "<script>alert(1)" not in page and "<img src=x" not in page


def test_a_non_http_source_is_never_a_link():
    pack = normalize(_pack())
    pack["claims"] = [{"text": "t", "source": "javascript:alert(1)"}]
    assert 'href="javascript' not in render(pack, [], {})


def test_the_page_loads_nothing_from_the_network():
    page = render(normalize(_pack()), [], {})
    assert "default-src 'none'" in page and "<link" not in page and 'src="http' not in page


def test_the_page_speaks_the_language_of_the_ads():
    assert "Remiama" in render(normalize(_pack()), [], {})
    english = _pack(primary_text="Marketing that pays for itself.", headline="Grow", description="Free call", cta="Learn more")
    assert "Sponsored" in render(normalize(english), [], {})


def test_the_feed_cuts_long_text_at_a_word_behind_see_more():
    page = render(normalize(_pack(primary_text="žodis " * 40)), [], {})
    assert '<span class="rest" hidden>' in page and "Daugiau" in page


def test_tool_writes_the_page_even_when_it_reports_problems(tmp_path):
    tools = _tools(tmp_path)
    out = ad_board_tool(tools, _pack(primary_text="Dirbame su 200 prekių ženklų."))
    assert "Not ready" in out and "“200”" in out
    assert (tmp_path / BOARD_PATH).is_file() and (tmp_path / PACK_PATH).is_file()
    assert "Pataisyti" in (tmp_path / BOARD_PATH).read_text(encoding="utf-8")
    assert not ready(PLAN, tools)


def test_tool_says_nothing_is_blocking_once_fixed(tmp_path):
    tools = _tools(tmp_path)
    pack = _pack(primary_text="Dirbame su 200 prekių ženklų.")
    pack["claims"] = [{"text": "200+ prekių ženklų", "source": SOURCE}]
    assert "No blocking problems" in ad_board_tool(tools, pack)
    assert ready(PLAN, tools)


def test_dispatch_reaches_the_tool(tmp_path):
    tools = _tools(tmp_path)
    assert BOARD_PATH in dispatch(tools, "ad_board", _pack())


def _draw(tools, rel):
    target = tools.resolve(rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\x89PNG fake")
    tools._note_written(target)


def test_refresh_puts_this_runs_visuals_on_the_page(tmp_path):
    tools = _tools(tmp_path)
    ad_board_tool(tools, _pack())
    assert "data:image/png" not in (tmp_path / BOARD_PATH).read_text(encoding="utf-8")
    _draw(tools, "deliverables/visual-1.png")
    refresh(PLAN, tools)
    page = (tmp_path / BOARD_PATH).read_text(encoding="utf-8")
    assert "data:image/png;base64" in page and 'download="visual-1.png"' in page
    assert json.loads((tmp_path / PACK_PATH).read_text(encoding="utf-8"))["ads"][0]["image"] == "deliverables/visual-1.png"


def test_a_visual_left_by_an_earlier_campaign_is_not_used(tmp_path):
    old = tmp_path / "deliverables/visual-1.png"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"\x89PNG old")
    tools = _tools(tmp_path)
    ad_board_tool(tools, _pack())
    refresh(PLAN, tools)
    assert "data:image/png" not in (tmp_path / BOARD_PATH).read_text(encoding="utf-8")


def test_a_retried_visual_replaces_the_first(tmp_path):
    import os

    tools = _tools(tmp_path)
    ad_board_tool(tools, _pack())
    _draw(tools, "deliverables/visual-1.png")
    _draw(tools, "deliverables/visual-1-2.png")
    first = tools.resolve("deliverables/visual-1.png")
    os.utime(first, (first.stat().st_atime - 60, first.stat().st_mtime - 60))
    refresh(PLAN, tools)
    assert json.loads((tmp_path / PACK_PATH).read_text(encoding="utf-8"))["ads"][0]["image"] == "deliverables/visual-1-2.png"


def test_refresh_leaves_another_runs_pack_alone(tmp_path):
    tools = _tools(tmp_path)
    ad_board_tool(tools, _pack())
    before = (tmp_path / BOARD_PATH).read_text(encoding="utf-8")
    fresh = WorkspaceTools(tmp_path)
    (tmp_path / BOARD_PATH).write_text("stale", encoding="utf-8")
    refresh(PLAN, fresh)
    assert (tmp_path / BOARD_PATH).read_text(encoding="utf-8") == "stale" and before != "stale"


def test_refresh_ignores_other_deliverables(tmp_path):
    tools = _tools(tmp_path)
    ad_board_tool(tools, _pack())
    (tmp_path / BOARD_PATH).write_text("kept", encoding="utf-8")
    refresh({"deliverable_path": "deliverables/report.md"}, tools)
    assert (tmp_path / BOARD_PATH).read_text(encoding="utf-8") == "kept"


def test_verifier_reads_the_pack_and_the_code_findings(tmp_path):
    tools = _tools(tmp_path)
    ad_board_tool(tools, _pack(primary_text="Dirbame su 200 prekių ženklų."))
    brief = verifier_brief(tools)
    assert "Dirbame su 200" in brief["pack"] and "<style>" not in brief["pack"]
    assert any("“200”" in item for item in brief["automatic_checks"]["blocking"])


def test_a_clean_board_is_usable_when_the_verifier_never_ran(tmp_path):
    tools = _tools(tmp_path)
    ad_board_tool(tools, _pack())
    status, notes = _final_status(False, {}, PLAN, {}, tools)
    assert status == "passed_with_notes" and "automatic checks passed" in notes[0]


def test_a_blocked_board_is_not_passed_without_a_verifier(tmp_path):
    tools = _tools(tmp_path)
    ad_board_tool(tools, _pack(primary_text="Dirbame su 200 prekių ženklų."))
    assert _final_status(False, {}, PLAN, {}, tools)[0] == "unmet"


def test_no_verdict_and_no_board_is_still_unmet(tmp_path):
    tools = _tools(tmp_path)
    assert _final_status(False, {}, {"deliverable_path": "deliverables/report.md"}, {}, tools) == ("unmet", [])


@pytest.mark.parametrize(
    "goal",
    [
        "padaryk man apgmedia.lt Facebook reklamą. su tekstu ir vizualu. Skirta FAcebook.",
        "Isanalizuok invohub.lt ir pasiulyk facebook reklama. 3 variantus",
        "Paruošk Facebook reklamų kampaniją svetainei https://invohub.eu",
        "write facebook ad copy for acme.com",
    ],
)
def test_real_ad_goals_get_the_board_even_when_assessed_as_marketing(goal):
    assert find_recipe(goal, {"domain": "marketing"}, load_recipes()).name == "ads"


@pytest.mark.parametrize(
    "goal",
    [
        "Paruošk Facebook reklamų kampaniją ir skaidres pptx formatu",
        "Facebook ad copy in an xlsx sheet",
        "atlik seo audit svetainei",
    ],
)
def test_a_goal_that_names_a_deck_or_other_work_keeps_the_marketing_recipe(goal):
    assert find_recipe(goal, {"domain": "marketing"}, load_recipes()).name == "marketing"


def test_the_ads_recipe_pins_the_page_as_the_deliverable():
    recipe = load_recipes()["ads"]
    plan = {
        "deliverable_path": "deliverables/reklama.html",
        "steps": [
            {"id": 1, "title": "Faktai", "detail": "tyrimas"},
            {"id": 2, "title": "Reklamos", "detail": "ad_board"},
            {"id": 3, "title": "Vizualai", "detail": "image_generate"},
        ],
    }
    assert recipe.apply(plan, "goal")["deliverable_path"] == BOARD_PATH


class AdsClient:
    """Scripted run of the ads recipe: one tool call per step, then a passing verdict."""

    def __init__(self):
        self.prompts = []
        self.verifier_input = ""
        self.done = set()

    def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
        system = messages[0]["content"]
        self.prompts.append(system)
        if "You assess tasks" in system:
            return Completion(
                content=json.dumps(
                    {"goal": "ad", "complexity": "trivial", "domain": "marketing", "success_criteria": ["ad"], "needs_web": True}
                )
            )
        if "You critique briefs" in system:
            return Completion(content=json.dumps({"clarity": "clear", "quality_bar": "A usable ad.", "reframed_goal": "ad"}))
        if "You execute this subtask" in system:
            title = json.loads(messages[1]["content"])["subtask"]["title"]
            if title in self.done:
                return Completion(content=f"{title} done")
            self.done.add(title)
            call = {
                "Facts": ("write_file", {"path": FACTS_PATH, "content": f"200+ prekių ženklų — {SOURCE}"}),
                "Ads": ("ad_board", _pack()),
                "Visuals": ("write_file", {"path": "deliverables/visual-1.png", "content": "png"}),
            }[title]
            return Completion(content="", tool_calls=[ToolCall(id=title, name=call[0], arguments=call[1])])
        if "You verify" in system:
            self.verifier_input = messages[1]["content"]
            return Completion(
                content=json.dumps(
                    {"passed": True, "score": 0.9, "usable": True, "summary": "ok", "blocking": [], "issues": [], "remaining": []}
                )
            )
        raise AssertionError(f"unexpected call: {system[:60]}")


def test_an_ad_goal_runs_without_a_planner_and_ends_on_the_page(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    events = []
    client = AdsClient()
    models = [
        Model(id=f"{family}/m", name=family, prompt_price=1e-6, completion_price=2e-6, context_length=200000, params={"tools", "structured_outputs"})
        for family in ("anthropic", "openai", "google")
    ]
    result = orchestrate(
        goal="padaryk man apgmedia.lt Facebook reklamą su tekstu ir vizualu",
        workspace=tmp_path,
        client=client,
        models=models,
        emit=events.append,
        max_loops=1,
        budget_eur=1.0,
        eur_rate=0.92,
    )
    assert result.passed is True
    assert not any("You write the plan" in p for p in client.prompts), "the recipe's steps are fixed"
    assert any("You critique briefs" in p for p in client.prompts), "an ad goal is not sent down the trivial path"
    assert "data:image/png;base64" in (tmp_path / BOARD_PATH).read_text(encoding="utf-8")
    assert "automatic_checks" in client.verifier_input and "<style>" not in client.verifier_input
    done = next(e.payload for e in events if e.type == "done")
    assert done["deliverable_path"] == BOARD_PATH and done["deliverable_exists"] is True
    assert "deliverables/visual-1.png" in done["files"]

