"""Market strategy and positioning checks on the ad board (rocky/adchecks/strategy.py)."""

from rocky.adboard import check_pack, message, normalize
from rocky.adchecks import strategy
from rocky.playbook import for_code
from rocky.tools import worker_tools

SOURCE = "https://apgmedia.lt/apie"
FACTS = f"Faktas — {SOURCE}\n"
PITCHED = FACTS + "Pitch: Reklamos valdymas be spėlionių\n"  # a pack with a control needs the site's pitch to compare against
IDS = {1, 2, 3, 4, 5, 6, 8, 11, 12, 13, 14, 16, 18, 20, 21, 22, 23, 25, 26, 31, 32, 33, 34, 35, 36, 37, 46, 47, 54}
AD = {
    "primary_text": "Rinkodara, kuri atsiperka.",
    "headline": "Reklamos valdymas be spėlionių",
    "description": "Pokalbis nemokamai",
    "cta": "Sužinoti daugiau",
    "angle": "Rezultatas",
    "audience": "E. parduotuvių vadovai, kurių reklamos kaina šį ketvirtį išaugo",
    "mix": {"product": "Reklamos valdymas", "price": "Nuo 990 € per mėnesį", "place": "Internetu, Lietuvoje"},
    "segment": {"size": "a", "buying_power": "b", "reach_cost": "c", "distinct": "d"},
}
JOB = {
    "statement": "Paruošti vakarienę šeimai darbo dienos vakarą grįžus po 18 val.",
    "action": "paruošti",
    "object": "vakarienė šeimai",
    "circumstance": "darbo dienos vakarą grįžus po 18 val. be plano",
    "before": "grįžta iš darbo ir pasiima vaikus",
    "after": "pavalgo ir sutvarko virtuvę",
}


def _pack(*ads, **pack):
    return {
        "brand": "APG Media",
        "url": "https://apgmedia.lt",
        "ads": [{**AD, **ad} for ad in (ads or ({},))],
        "claims": [],
        "table_stakes": [{"outcome": "Atsakymas į užklausą", "minimum": "Per dieną", "status": "met"}],
        **pack,
    }


def _checks(pack, facts=FACTS):
    return strategy.check(normalize(pack), facts, {})


def _codes(pack, facts=FACTS):
    return [c.code for c in _checks(pack, facts)]


def _two(control, challenger, **pack):
    return _pack({"test_role": "control", **control}, {"test_role": "challenger", **challenger}, **pack)


# --- the module as a whole -------------------------------------------------


def test_a_clean_pack_with_and_without_facts_is_silent():
    assert _codes(_pack()) == [] and _codes(_pack(), None) == []


def test_every_code_has_an_element_in_both_languages_and_formats():
    assert set(strategy.MESSAGES["en"]) == set(strategy.MESSAGES["lt"])
    for code in strategy.MESSAGES["en"]:
        element = for_code(code)
        assert element is not None and element.number in IDS, code
        assert element.question["en"] and element.question["lt"] and element.why["en"] and element.why["lt"]
        for language in ("en", "lt"):
            text = strategy.MESSAGES[language][code].format(code=code, field="f", Field="F", value="v", n=1, limit=2)
            assert "{" not in text, (code, language)


def test_the_tool_spec_advertises_every_field():
    spec = next(t for t in worker_tools(False) if t["function"]["name"] == "ad_board")
    props = spec["function"]["parameters"]["properties"]
    assert all(name in props and props[name].get("description") for name in strategy.PACK_FIELDS)
    assert all(name in props["ads"]["items"]["properties"] for name in strategy.AD_FIELDS)


def test_the_board_runs_the_module_and_renders_its_message_in_lithuanian():
    pack = normalize(_pack({"primary_text": "Pristatome kitą dieną visoje Lietuvoje už 2,99 €", "mix": {**AD["mix"], "price": "4,99 €", "place": "Vilniuje ir Kaune"}}))
    checks = [c for c in check_pack(pack, FACTS, {1: "x"}) if c.code == "mix_mismatch"]
    assert {c.field for c in checks} == {"price", "place"}
    assert message(checks[0], "lt").startswith("Reklama sako „")


# --- #1 the offer behind the promise ----------------------------------------


def test_a_price_in_the_copy_must_be_the_price_in_the_offer():
    checks = _checks(_pack({"primary_text": "Pristatymas už 2,99 €", "mix": {**AD["mix"], "price": "Nuo 4,99 €", "place": "Pristatymas visoje Lietuvoje"}}))
    assert [(c.code, c.field, c.value) for c in checks] == [("mix_mismatch", "price", "2,99 €")]
    assert _codes(_pack({"primary_text": "Pristatymas už 2,99 €", "mix": {**AD["mix"], "price": "2,99 €", "place": "Pristatymas visoje Lietuvoje"}})) == []


def test_a_delivery_promise_needs_delivery_in_the_place():
    assert _codes(_pack({"primary_text": "Pristatome per dvi dienas.", "mix": {**AD["mix"], "place": "Atsiėmimas Vilniuje"}})) == ["mix_mismatch"]
    assert _codes(_pack({"primary_text": "Pristatome per dvi dienas.", "mix": {**AD["mix"], "place": "Kurjeris į namus"}})) == []


def test_a_nationwide_promise_and_a_city_promise_must_match_the_place():
    nation = _checks(_pack({"primary_text": "Pristatome visoje Lietuvoje.", "mix": {**AD["mix"], "place": "Pristatymas Vilniuje ir Kaune"}}))
    assert [(c.code, c.value) for c in nation] == [("mix_mismatch", "visoje Lietuvoje")]
    city = _checks(_pack({"headline": "Atsiimkite Klaipėdoje", "mix": {**AD["mix"], "place": "Parduotuvė Vilniuje"}}))
    assert [(c.code, c.value) for c in city] == [("mix_mismatch", "Klaipėdoje")]
    assert _codes(_pack({"headline": "Atsiimkite Klaipėdoje", "mix": {**AD["mix"], "place": "Parduotuvės Vilniuje ir Klaipėdoje"}})) == []


def test_a_next_day_promise_needs_a_time_in_the_offer_and_a_promise_field_is_scanned_too():
    assert _codes(_pack({"promise": "Pristatome kitą dieną", "mix": {**AD["mix"], "place": "Kurjeriu Lietuvoje"}})) == ["mix_mismatch"]
    assert _codes(_pack({"promise": "Pristatome kitą dieną", "mix": {**AD["mix"], "place": "Kurjeriu Lietuvoje per 1 darbo dieną"}})) == []


def test_an_empty_offer_field_is_left_to_mix_missing():
    assert _codes(_pack({"primary_text": "Pristatome už 2,99 €", "mix": {"product": "x", "price": "", "place": ""}})) == []


# --- #2 #46 #47 the job ------------------------------------------------------


def test_a_job_line_in_the_facts_left_out_of_the_pack_is_reported_once():
    assert _codes(_pack(), FACTS + "Job: gauti pinigus už darbą laiku\n") == ["job_missing"]
    assert _codes(_pack(job={"statement": ""})) == ["job_missing"]
    assert "job_missing" not in _codes(_pack(job=JOB), FACTS + "Job: x\n")


def test_a_job_that_names_the_tool_or_the_brand_is_not_durable():
    pack = _pack(job={**JOB, "statement": "Greičiau išrašyti sąskaitas su APG Media programa"})
    found = next(c for c in _checks(pack) if c.code == "job_not_durable")
    assert "APG" in found.value and "programa" in found.value
    assert _codes(_pack(job=JOB)) == []


def test_a_job_without_its_before_and_after_steps_or_with_the_product_in_them():
    assert [(c.code, c.value) for c in _checks(_pack(job={**JOB, "before": "", "after": JOB["statement"]}))] == [("job_boundaries_missing", "before, after")]
    assert _codes(_pack(job={**JOB, "before": "Atsidaro APG Media ataskaitą"})) == ["job_boundary_names_product"]


def test_a_job_needs_a_circumstance_an_observable_action_and_a_statement_built_from_them():
    assert _codes(_pack(job={**JOB, "circumstance": "vakare"})) == ["job_no_circumstance"]
    assert _codes(_pack(job={**JOB, "circumstance": JOB["object"]})) == ["job_no_circumstance"]
    assert "job_vague_action" in _codes(_pack(job={**JOB, "action": "eat healthier"}))
    assert sorted(_codes(_pack(job="Valgyti sveikiau"))) == ["job_boundaries_missing", "job_no_circumstance", "job_vague_action"]
    assert _codes(_pack(job={**JOB, "statement": "Gauti pinigus laiku"})) == ["job_statement_mismatch"]


# --- #3 audience -------------------------------------------------------------


def test_an_audience_that_is_only_demographics_warns():
    checks = _checks(_pack({"audience": "Moterys 25–45, Vilnius"}))
    assert [(c.code, c.ad, c.value) for c in checks] == [("audience_demographic", 1, "Moterys 25–45, Vilnius")]
    assert _codes(_pack({"audience": "Moterys 25–45, kurios kas ketvirtį moka buhalteriui 150 €"})) == []
    assert _codes(_pack({"audience": "Women 25–45", "struggle": "cannot close the books in time"})) == []


# --- #4 repositioning ----------------------------------------------------------


def test_the_control_must_match_the_pitch_line():
    facts = FACTS + "Pitch: Premium Scandinavian design for your home office\n"
    drift = _checks(_two({"headline": "Affordable chairs", "primary_text": "Chairs for everyone"}, {}), facts)
    assert [(c.code, c.ad, c.value) for c in drift if c.code == "control_drift"] == [("control_drift", 1, "0 %")]
    assert "control_drift" not in _codes(_two({"headline": "Premium Scandinavian design", "primary_text": "For your home office"}, {}), facts)
    assert "control_drift" not in _codes(_two({"headline": "Affordable chairs"}, {}), FACTS + "Pitch: unknown\n")


def test_a_control_without_a_pitch_line_in_the_facts_warns_only_when_facts_exist():
    assert "no_pitch" in _codes(_two({}, {}), FACTS)
    assert "no_pitch" not in _codes(_two({}, {}), FACTS + "Pitch: Augimas be spėlionių\n")
    assert _codes(_two({}, {}), None) == []


def test_a_challenger_outcome_needs_a_sourced_claim():
    pack = _two({}, {"outcome": "Sėdėti 8 valandas be nugaros skausmo"})
    assert "outcome_unsourced" in _codes(pack, FACTS + "Pitch: Reklamos valdymas be spėlionių\n")
    pack["claims"] = [{"text": "Sėdėti 8 valandas be nugaros skausmo, patvirtinta", "source": SOURCE}]
    assert "outcome_unsourced" not in _codes(pack, FACTS + "Pitch: Reklamos valdymas be spėlionių\n")


# --- #5 table stakes -----------------------------------------------------------


def _stakes(**stake):
    pack = _pack()
    pack["table_stakes"] = [{"outcome": "Pristatymas kitą dieną", "minimum": "Užsakius iki 15 val.", "status": "met", **stake}]
    return pack


def test_an_unknown_stake_and_a_stake_without_a_minimum_warn_when_facts_exist():
    assert _codes(_stakes(status="unknown")) == ["stake_unknown"]
    assert _codes(_stakes(minimum="")) == ["stake_no_minimum"]
    assert _codes(_stakes(status="unknown", minimum=""), None) == []
    assert _codes(_stakes()) == []


def test_a_headline_that_leads_on_a_table_stake_warns():
    pack = _stakes()
    pack["ads"][0]["headline"] = "Pristatymas kitą dieną visoje šalyje"
    pack["ads"][0]["mix"] = {**AD["mix"], "place": "Pristatymas visoje Lietuvoje per 1 dieną"}
    found = _checks(pack)
    assert [(c.code, c.ad, c.field, c.value) for c in found] == [("stake_lead", 1, "headline", "Pristatymas kitą dieną")]


# --- #6 portfolio ----------------------------------------------------------------


def test_a_product_with_no_match_line_warns_and_a_matched_one_passes():
    facts = FACTS + "Match: Eco 6 kW → butai iki 70 m²\nMatch: Pro 9 kW -> namai 100–150 m²\n"
    unmatched = _pack({"headline": "Siurblys Max 12 kW namui", "mix": {**AD["mix"], "product": "Max 12 kW"}})
    assert [(c.code, c.value) for c in _checks(unmatched, facts)] == [("product_unmatched", "Max 12 kW")]
    assert _codes(_pack({"headline": "Siurblys Pro 9 kW namui", "mix": {**AD["mix"], "product": "Pro 9 kW"}}), facts) == []


def test_several_products_without_match_lines_warn_once():
    pack = _two({"mix": {**AD["mix"], "product": "Eco"}}, {"mix": {**AD["mix"], "product": "Pro"}})
    assert "no_match_lines" in _codes(pack, FACTS + "Pitch: Reklamos valdymas be spėlionių\n")
    assert "no_match_lines" not in _codes(_two({}, {}), FACTS + "Pitch: Reklamos valdymas be spėlionių\n")


# --- #8 #11 a new market ---------------------------------------------------------


def _growth(reach="", **pack):
    return _pack(growth={"kind": "development", "reach": reach}, **pack)


ADAPTATION = {"product": "adapt", "product_evidence_url": SOURCE, "message": "rewrite", "message_evidence_url": SOURCE}


def test_reach_evidence_needs_a_url_from_the_facts():
    assert "reach_unverified" in _codes(_growth("Latvian landing page", adaptation=ADAPTATION))
    assert _codes(_growth(f"Latvian landing page {SOURCE}", adaptation=ADAPTATION)) == []
    assert _codes(_growth("Latvian landing page"), None) == []


def test_ads_in_the_wrong_language_for_the_market_warn():
    found = _checks(_growth(SOURCE, adaptation=ADAPTATION, market={"country": "LV", "language": "lv"}))
    assert [(c.code, c.value) for c in found] == [("reach_mismatch", "lt → lv")]
    assert _codes(_growth(SOURCE, adaptation=ADAPTATION, market={"language": "lt"})) == []


def test_a_new_market_needs_an_adaptation_decision_with_evidence():
    assert _codes(_growth(SOURCE)) == ["adaptation_missing"]
    assert _codes(_growth(SOURCE, adaptation={**ADAPTATION, "product_evidence_url": ""})) == ["adaptation_unsourced"]
    assert [(c.code, c.value) for c in _checks(_growth(SOURCE, adaptation={**ADAPTATION, "message": "unknown"}))] == [("adaptation_unknown", "message")]
    assert _codes(_pack(growth={"kind": "penetration", "reach": ""})) == []


# --- #12 the line ------------------------------------------------------------------


def _line(new=49, intent="fill", prices=(24, 29, 34)):
    return _pack(line={"items": [{"name": f"Bag {p}", "price_eur": p} for p in prices], "new_name": "Premium", "new_price_eur": new, "intent": intent})


def test_a_stretch_declared_as_a_fill_is_corrected_and_needs_tier_proof():
    found = _checks(_line())
    assert [(c.code, c.field, c.value, c.n, c.limit) for c in found if c.code == "line_mismatch"] == [("line_mismatch", "fill", "stretch_up", 24, 34)]
    assert "stretch_unproven" in [c.code for c in found]
    pack = _line(intent="stretch_up")
    pack["claims"] = [{"text": "Konkurento premium maišas kainuoja 55 €", "source": SOURCE}]
    assert _codes(pack) == []
    assert _codes(_line(new=27)) == []


def test_an_item_without_a_price_stops_the_classification():
    assert [(c.code, c.value) for c in _checks(_line(prices=(24, None, 34)))] == [("line_incomplete", "Bag None")]


# --- #13 #14 comparison ----------------------------------------------------------------


def _cells(*names, **over):
    return [{"name": n, "brand": n, "audience": "x", "gains": "g", "risks": "r", **over.get(n, {})} for n in names]


def test_a_cobrand_test_needs_every_cell_with_an_ad_and_its_tradeoff():
    pack = _pack({"cell": "joint"}, {"cell": "partner_a_alone"}, comparison={"kind": "cobrand", "cells": _cells("joint", "partner_a_alone")})
    assert [(c.code, c.field, c.value) for c in _checks(pack)] == [("comparison_incomplete", "cobrand", "partner_b_alone")]
    pack = _pack({"cell": "joint"}, comparison={"kind": "cobrand", "cells": _cells("joint", "partner_a_alone", "partner_b_alone", joint={"risks": ""})})
    assert sorted(_codes(pack)) == ["cell_no_ads", "cell_no_ads", "cell_no_tradeoff"]
    pack = _pack({"cell": "joint"}, {"cell": "partner_a_alone"}, {"cell": "partner_b_alone"}, comparison={"kind": "cobrand", "cells": _cells("joint", "partner_a_alone", "partner_b_alone")})
    assert _codes(pack) == []


def test_an_ad_in_an_undeclared_cell_warns():
    pack = _pack({"cell": "solo"}, comparison={"kind": "repositioning", "cells": _cells("joint")})
    assert [(c.code, c.ad, c.value) for c in _checks(pack)] == [("cell_unknown", 1, "solo")]


def test_an_extension_needs_sourced_credibility_for_the_parent_brand():
    parent = {"credibility": "1,2 mln. klientų jau moka kas mėnesį", "source": SOURCE}
    good = _pack({"cell": "parent_brand"}, {"cell": "new_name"}, comparison={"kind": "extension", "cells": _cells("parent_brand", "new_name", parent_brand=parent)})
    assert _codes(good) == []
    bad = _pack({"cell": "parent_brand"}, {"cell": "new_name"}, comparison={"kind": "extension", "cells": _cells("parent_brand", "new_name", parent_brand={**parent, "source": "assumption"})})
    assert _codes(bad) == ["credibility_unsourced"]


# --- #16 the ladder --------------------------------------------------------------------

REASON = "Tik Standard planas leidžia penkis naudotojus be mokesčio už vietą"


def _ladder(reason=REASON, below=19, above=99, **ad):
    return _pack(ad, ladder={"tier": "mid", "below": {"name": "Basic", "price_eur": below}, "above": {"name": "Pro", "price_eur": above}, "reason": reason})


def test_a_middle_tier_needs_its_own_reason_and_an_ad_that_uses_it():
    assert _codes(_ladder(reason="")) == ["mid_reason_missing"]
    assert [(c.code, c.value) for c in _checks(_ladder())] == [("mid_reason_unused", REASON)]
    assert _codes(_ladder(primary_text=f"{REASON}. Išbandykite.")) == []


def test_an_upside_down_ladder_and_a_missing_ladder_warn():
    assert [(c.code, c.n, c.limit) for c in _checks(_ladder(below=99, above=19, primary_text=REASON))] == [("price_ladder_invalid", 99, 19)]
    assert _codes(_pack(), FACTS + "Price position: mid; below Basic 19 €; above Pro 99 €\n") == ["ladder_missing"]


# --- #18 launch ----------------------------------------------------------------------------


def _launch(kind, *associations, **ad):
    return _pack(ad, launch={"kind": kind, "associations": list(associations)})


def test_a_relaunch_needs_sourced_associations_that_the_copy_uses():
    assert _codes(_launch("relaunch")) == ["relaunch_associations_missing"]
    assert _codes(_launch("relaunch", {"text": "vaikystės skonis", "stance": "keep", "source": "https://x.example"})) == ["association_unsourced", "relaunch_association_unused"]
    assert _codes(_launch("relaunch", {"text": "vaikystės skonis", "stance": "keep", "source": SOURCE})) == ["relaunch_association_unused"]
    assert _codes(_launch("relaunch", {"text": "vaikystės skonis", "stance": "keep", "source": SOURCE}, primary_text="Vaikystės skonis sugrįžo.")) == []


def test_a_new_name_cannot_claim_heritage_and_a_launch_line_must_reach_the_pack():
    assert [(c.code, c.value) for c in _checks(_launch("new", headline="Legendinis skonis grįžta"))] == [("heritage_on_new_name", "Legendinis")]
    assert _codes(_launch("new")) == []
    assert _codes(_pack(), FACTS + "Launch: relaunch\n") == ["launch_missing"]


# --- #20 vision ----------------------------------------------------------------------------

VISION = "Padedame smulkiam verslui gauti pinigus už darbą be buhalterio."


def test_a_vision_is_one_short_sentence_and_the_hero_quotes_it():
    assert _codes(_pack(vision=VISION)) == []
    assert _codes(_pack(vision=VISION + " Ir dar kažkas.")) == ["vision_long"]
    assert [(c.code, c.n, c.limit) for c in _checks(_pack(vision=" ".join(["žodis"] * 26)))] == [("vision_long", 26, 25)]
    assert _codes(_pack(vision=VISION, hero={"headline": "Gauk pinigus greičiau", "reason": "nes vizija yra gauti pinigus už darbą"})) == []
    assert _codes(_pack(vision=VISION, hero={"headline": "Greičiausias", "reason": "skamba geriau"})) == ["hero_unjustified"]
    assert _codes(_pack(), FACTS + "Vision: x\n") == ["vision_missing"]


# --- #21 built for -----------------------------------------------------------------------------

WHO = "laisvai samdomi ir 1–5 žmonių įmonės be buhalterio"


def test_a_request_is_judged_against_whom_the_product_is_built_for():
    request = {"from": "120 žmonių buhalterinė įmonė", "value_eur": 40000, "change": "tvirtinimo lygiai", "affects_all": True, "verdict": "build", "reason": "didelis klientas"}
    assert _codes(_pack(requests=[request])) == ["built_for_missing", "request_unchecked"]
    assert [(c.code, c.value) for c in _checks(_pack(built_for={"who": WHO}, requests=[request]))] == [("request_unchecked", request["from"])]
    assert _codes(_pack(built_for={"who": WHO}, requests=[{**request, "reason": "keistų produktą visiems 1–5 žmonių įmonės pirkėjams"}])) == []
    assert _codes(_pack(built_for={"who": WHO}, requests=[{**request, "verdict": "custom_tier"}])) == []
    assert _codes(_pack(), FACTS + "For: laisvai samdomi\n") == ["built_for_missing"]


def test_an_ad_aimed_outside_the_built_for_customer_is_marked():
    assert [(c.code, c.ad) for c in _checks(_pack({"outside_for": True}, built_for={"who": WHO}))] == [("audience_outside", 1)]
    assert _codes(_pack({"outside_for": False}, built_for={"who": WHO})) == []


# --- #22 local optimum -----------------------------------------------------------------------------

LOSS = FACTS + "Loss: 40% of trials leave in week 1\nPrior tests: 3 onboarding email variants, +1.8%, +2.1%, +0.9%\n"
TRIAL = {"segment": "200 new signups", "days": 14, "metric": "week-1 retention", "threshold": "70 %"}


def test_losses_with_repeated_small_gains_demand_a_different_alternative():
    assert _codes(_pack(), LOSS) == ["local_optimum"]
    assert _codes(_pack(alternative_trial={"problem": "p", "alternative": "a fourth onboarding email variant", "trial": TRIAL}), LOSS) == ["local_optimum"]
    assert _codes(_pack(alternative_trial={"problem": "p", "alternative": "preloaded demo store with the merchant's own catalogue", "trial": TRIAL}), LOSS) == []
    assert _codes(_pack(), FACTS + "Loss: 40%\n") == []


def test_an_alternative_trial_must_be_sized():
    found = _checks(_pack(alternative_trial={"problem": "p", "alternative": "demo store", "trial": {"segment": "s", "metric": "m"}}))
    assert [(c.code, c.value) for c in found] == [("trial_incomplete", "days, threshold")]


# --- #23 capabilities -----------------------------------------------------------------------------


def _caps(n, **over):
    return [{"rival": "Paysera", "capability": f"cap {i}", "kind": "licence", "ours": "unmet", "source": SOURCE, **over} for i in range(n)]


def test_a_rivals_offer_needs_three_sourced_capabilities_with_our_status():
    assert [(c.code, c.n, c.limit) for c in _checks(_pack(rival_capabilities=_caps(2)))] == [("copy_headline_only", 2, 3)]
    assert _codes(_pack(rival_capabilities=_caps(3, ours=""))) == ["capability_ours_missing"] * 3
    assert _codes(_pack(rival_capabilities=_caps(3, source="https://other.example"))) == ["capability_unsourced"] * 3
    assert _codes(_pack(rival_capabilities=_caps(3))) == []
    assert _codes(_pack(), FACTS + "Capability: Paysera | EMI licence | ours: unmet | https://lb.lt\n") == ["rival_capabilities_missing"]


# --- #25 occasion ------------------------------------------------------------------------------------


def test_a_challenger_on_a_new_occasion_must_move_the_place_too():
    control = {"occasion": {"when": "pavasario valymas", "for_whom": "self", "kind": "routine"}}
    gift = {"occasion": {"when": "kalėdinė dovana", "for_whom": "gift", "kind": "gift"}}
    assert [(c.code, c.ad, c.value) for c in _checks(_two(control, gift), PITCHED)] == [("occasion_place", 2, "kalėdinė dovana")]
    assert _codes(_two(control, {**gift, "mix": {**AD["mix"], "place": "Dovanų skiltis svetainėje"}}), PITCHED) == []
    assert _codes(_two(control, {"occasion": "pavasario valymas"}), PITCHED) == []
    assert _codes(_two({}, gift), PITCHED) == []


# --- #26 #31 alternatives ------------------------------------------------------------------------------


def test_alternatives_in_the_facts_must_reach_the_pack_with_their_sources():
    assert _codes(_pack(), FACTS + "Alternative: naudotas automobilis ~1 500 € — https://forum.example\n") == ["alternatives_missing"]
    assert [(c.code, c.value) for c in _checks(_pack(alternatives=[{"text": "naudotas automobilis", "source": "https://forum.example"}]))] == [("alternative_unsourced", "naudotas automobilis")]
    assert _codes(_pack(alternatives=[{"text": "naudotas automobilis", "source": SOURCE}])) == []
    assert [(c.code, c.ad) for c in _checks(_pack({"alternative": {"what": "gaminimas namuose", "cost": "45 min/d.", "source": "estimate"}}))] == [("alternative_unsourced", 1)]
    assert _codes(_pack({"alternative": {"what": "gaminimas namuose", "cost": "45 min/d.", "source": SOURCE}})) == []


def test_copy_that_names_a_competitor_instead_of_the_real_alternative_warns():
    facts = FACTS + "Competitor: Wolt — https://wolt.com\n"
    alt = {"what": "gaminimas namuose", "cost": "45 min/d.", "source": SOURCE}
    assert [(c.code, c.ad, c.value) for c in _checks(_pack({"primary_text": "Greičiau nei Wolt.", "alternative": alt}), facts)] == [("wrong_comparison", 1, "Wolt")]
    assert _codes(_pack({"primary_text": "Greičiau nei Wolt.", "alternative": {**alt, "what": "Wolt užsakymai"}}), facts) == []
    assert _codes(_pack({"primary_text": "Greičiau nei gaminti pačiam.", "alternative": alt}), facts) == []


# --- #32 voice ------------------------------------------------------------------------------------------


def _voice(n=3, address="jus", limit=0, **ad):
    return _pack(ad, voice={"examples": [{"text": f"Sakinys {i}", "source": SOURCE} for i in range(n)], "address": address, "max_exclamations": limit})


def test_the_voice_needs_three_sourced_examples():
    assert _codes(_pack(), FACTS + "Voice: Jūs\n") == ["voice_missing"]
    assert [(c.code, c.n, c.limit) for c in _checks(_voice(2))] == [("voice_thin", 2, 3)]
    pack = _voice()
    pack["voice"]["examples"][0]["source"] = "https://other.example"
    assert [(c.code, c.value) for c in _checks(pack)] == [("voice_unsourced", "Sakinys 0")]
    assert _codes(_voice()) == []


def test_copy_that_switches_the_address_form_or_shouts_warns():
    assert [(c.code, c.ad, c.field, c.value) for c in _checks(_voice(primary_text="Tu gausi daugiau."))] == [("voice_address_mismatch", 1, "primary_text", "jūs")]
    assert [(c.code, c.value) for c in _checks(_voice(address="tu", headline="Jūs gausite daugiau"))] == [("voice_address_mismatch", "tu")]
    assert [(c.code, c.n, c.limit) for c in _checks(_voice(headline="Pirkite dabar!"))] == [("voice_exclamation", 1, 0)]
    assert _codes(_voice(address="jus", limit=1, headline="Jūs gausite daugiau!")) == []


# --- #33 the stranger test --------------------------------------------------------------------------------

MATCH = FACTS + "Match: Sąskaita+ programa → laisvai samdomi, deklaruojantys PVM\n"


def test_a_sparse_ad_a_stranger_cannot_read_warns_unless_the_brand_is_known():
    sparse = {"headline": "Paprasčiau.", "primary_text": "APG Media", "mix": {**AD["mix"], "product": "Sąskaita+ programa"}}
    assert [(c.code, c.ad) for c in _checks(_pack(sparse), MATCH)] == [("borrowed_equity", 1)]
    assert _codes(_pack({**sparse, "primary_text": "Sąskaitų programa be buhalterio"}), MATCH) == []
    assert _codes(_pack(sparse, known_brand={"evidence": "120 000 sekėjų", "source": SOURCE}), MATCH) == []
    assert _codes(_pack(sparse), FACTS) == []


# --- #34 purpose ----------------------------------------------------------------------------------------------


def test_each_buying_purpose_gets_its_own_standards_and_pitch():
    assert [(c.code, c.ad) for c in _checks(_pack({"purpose": "transform"}, {"purpose": "consume", "primary_text": "Kitas"}))] == [("purpose_no_standard", 1)]
    assert _codes(_pack({"purpose": "transform", "standards": ["E330 sertifikatas"]}, {"purpose": "consume", "primary_text": "Kitas"})) == []
    assert _codes(_pack({"purpose": "equipment", "primary_text": "COA kiekvienai partijai"}, {"purpose": "consume", "primary_text": "Kitas"})) == []
    assert [(c.code, c.ad) for c in _checks(_pack({"purpose": "consume"}, {"primary_text": "Kitas"}))] == [("purpose_missing", 2)]
    assert [(c.code, c.value) for c in _checks(_pack({"purpose": "consume"}, {"purpose": "transform", "standards": ["ISO 9001"]}))] == [("purpose_same_pitch", "1, 2")]
    assert [(c.code, c.value) for c in _checks(_pack({"purpose": "transform", "standards": [{"text": "E330", "source": "https://x.example"}]}))] == [("standard_unsourced", "E330")]
    assert _codes(_pack({"purpose": "transform", "standards": [{"text": "E330", "source": SOURCE}]})) == []


# --- #35 brand standard --------------------------------------------------------------------------------------------

SUPPLIERS = [{"name": "Kreta Oil", "origin": "Kreta", "url": "https://kreta-oil.example/spec"}, {"name": "Andalucia Oil", "origin": "Andalūzija", "url": "https://andalucia-oil.example/spec"}]
ATTRIBUTES = [{"name": "rūgštingumas", "unit": "%", "limit": "≤ 0,5", "method": "ISO 660", "source": SOURCE}]


def test_several_suppliers_need_one_standard_and_claims_that_hold_for_every_origin():
    assert [(c.code, c.n) for c in _checks(_pack(brand_standard={"suppliers": SUPPLIERS}))] == [("standard_missing", 2)]
    pack = _pack(brand_standard={"suppliers": SUPPLIERS, "attributes": ATTRIBUTES})
    pack["claims"] = [{"text": "Rūgštingumas 0,3 %", "source": "https://kreta-oil.example/spec"}]
    assert [(c.code, c.value, c.n) for c in _checks(pack, FACTS + "https://kreta-oil.example/spec\n")] == [("claim_single_origin", "Rūgštingumas 0,3 %", 2)]
    assert [(c.code, c.ad, c.value) for c in _checks(_pack({"headline": "Kretos alyvuogių aliejus"}, brand_standard={"suppliers": SUPPLIERS, "attributes": ATTRIBUTES}))] == [("origin_in_copy", 1, "Kreta")]
    assert _codes(_pack(brand_standard={"suppliers": SUPPLIERS, "attributes": ATTRIBUTES})) == []
    assert _codes(_pack({"headline": "Kretos aliejus"}, brand_standard={"suppliers": SUPPLIERS[:1]})) == []


# --- #36 stage -----------------------------------------------------------------------------------------------------------

VERSUS = {"attribute": "mėnesio uždarymas", "us": "2 val.", "them": "3,5 val.", "source": SOURCE}


def test_assessing_ads_need_a_sourced_comparison_and_out_of_market_ads_a_hook():
    assert [(c.code, c.ad) for c in _checks(_pack({"stage": "assessing"}))] == [("versus_missing", 1)]
    assert [(c.code, c.value) for c in _checks(_pack({"stage": "assessing", "versus": {**VERSUS, "source": ""}}))] == [("versus_unsourced", "mėnesio uždarymas")]
    assert _codes(_pack({"stage": "assessing", "versus": VERSUS})) == []
    assert [(c.code, c.ad) for c in _checks(_pack({"stage": "out_of_market"}))] == [("memory_hook_missing", 1)]
    assert _codes(_pack({"stage": "out_of_market", "memory_hook": "mėlynas dramblys"})) == []
    assert [(c.code, c.ad) for c in _checks(_pack({"stage": "out_of_market", "memory_hook": "h"}, {}))] == [("stage_missing", 2)]


def test_an_out_of_market_ad_that_compares_warns_and_a_pack_of_only_comparisons_too():
    hook = {"stage": "out_of_market", "memory_hook": "h"}
    assert [(c.code, c.value) for c in _checks(_pack({**hook, "headline": "Rivile vs mes"}))] == [("comparison_out_of_market", "vs")]
    assert [(c.code, c.value) for c in _checks(_pack({**hook, "headline": "Greičiau nei Rivile"}), FACTS + "Competitor: Rivile — https://rivile.lt\n")] == [("comparison_out_of_market", "Rivile")]
    assessing = {"stage": "assessing", "versus": VERSUS}
    assert _codes(_pack(assessing, assessing, in_market_share={"value": 0.08, "source": SOURCE})) == ["pack_all_assessing"]
    assert _codes(_pack(assessing, hook, in_market_share={"value": 0.08, "source": SOURCE})) == []
    assert _codes(_pack(assessing, assessing, in_market_share={"value": 0.6, "source": SOURCE})) == []


def test_an_assumed_in_market_share_is_said_so():
    assert [(c.code, c.value) for c in _checks(_pack(in_market_share=0.08))] == [("in_market_assumed", "8 %")]
    assert _codes(_pack(in_market_share={"value": 0.08, "source": SOURCE, "assumed": True})) == ["in_market_assumed"]
    assert _codes(_pack(in_market_share={"value": 0.08, "source": SOURCE})) == []


# --- #37 service -------------------------------------------------------------------------------------------------------------


def test_a_service_angle_needs_service_evidence_and_a_feature_only_pack_is_told_to_try_one():
    assert [(c.code, c.ad) for c in _checks(_pack({"angle_kind": "service"}))] == [("service_unsourced", 1)]
    assert _codes(_pack({"angle_kind": "service"}, service={"offered": True, "summary": "dalys per 24 val.", "source": "https://other.example"})) == ["service_unsourced"]
    assert _codes(_pack({"angle_kind": "service"}, service={"offered": True, "summary": "dalys per 24 val.", "source": SOURCE})) == []
    feature = {"angle_kind": "feature"}
    assert _codes(_pack(feature, feature, feature, service={"offered": True, "source": SOURCE})) == ["service_angle_missing"]
    assert _codes(_pack(feature, feature, {"angle_kind": "outcome"}, service={"offered": True, "source": SOURCE})) == []
    assert _codes(_pack(feature, feature, feature, service={"offered": False})) == []


# --- #54 lead users -----------------------------------------------------------------------------------------------------------------


def test_a_lead_user_needs_both_conditions_sourced():
    user = {"name": "@medunešis", "built": {"text": "HX711 svarstyklės", "url": SOURCE}, "stake": {"text": "prarado 6 iš 20 avilių, ~1 800 €", "url": SOURCE}}
    assert _codes(_pack(lead_users=[user])) == []
    found = _checks(_pack(lead_users=[{**user, "stake": {"text": "daug praranda", "url": ""}}]))
    assert [(c.code, c.field, c.value) for c in found] == [("lead_user_unqualified", "stake_url, stake_number", "@medunešis")]
