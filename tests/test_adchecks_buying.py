"""How people and organizations buy: the buying module settles each question by code, and says nothing when its fields are absent."""

from rocky import playbook
from rocky.adboard import Check, check_pack, message, normalize
from rocky.adchecks import buying
from rocky.tools import worker_tools

SITE = "https://veja.lt"
SOURCE = "https://veja.lt/apie"
FACTS = f"Job: nupjauti veją be darbo — {SOURCE}\n"


def _ad(**fields):
    return {
        "primary_text": "Veja nupjauta, kol gurkšnojate kavą.",
        "headline": "Robotas, kuris pjauna pats",
        "description": "Pristatymas per savaitę",
        "cta": "Sužinoti daugiau",
        "angle": "Laisvas laikas",
        "audience": "Sodybų savininkai, kuriems gaila savaitgalio",
        "mix": {"product": "Navimow i105", "price": "1 299 €", "place": "veja.lt, pristatymas į namus"},
        "segment": {"size": "Apie 20 000 (įvertis)", "buying_power": "Aukšta", "reach_cost": "Vidutinė", "distinct": "Turi sodybą"},
        **fields,
    }


def _pack(*ads, **pack):
    return {
        "brand": "Veja",
        "url": SITE,
        "ads": list(ads) or [_ad()],
        "claims": [],
        "table_stakes": [{"outcome": "Atsakymas", "minimum": "Per dieną", "status": "met"}],
        **pack,
    }


def _mine(pack, facts=FACTS):
    normalized = normalize(pack)
    return buying.check(normalized, facts, {n: "x" for n in range(1, len(normalized["ads"]) + 1)})


def _codes(checks):
    return sorted(c.code for c in checks)


# --- silence ---------------------------------------------------------------


def test_a_pack_without_any_buying_field_gets_no_finding_from_this_module():
    assert _mine(_pack()) == []
    assert _mine(_pack(_ad(test_role="control"), _ad(test_role="challenger"))) == []
    assert _mine(_pack(), facts=None) == []


def test_the_module_runs_inside_check_pack_and_its_messages_render_in_both_languages():
    pack = normalize(_pack(_ad(adoption_stage="enthusiast"), _ad(adoption_stage="enthusiast")))
    checks = check_pack(pack, FACTS, {1: "x", 2: "x"})
    found = next(c for c in checks if c.code == "adoption_single_stage")
    assert "enthusiasts" in message(found) and "entuziastams" in message(found, "lt")


# --- 70 attribute --------------------------------------------------------------


def test_an_internal_attribute_in_the_headline_warns_unless_the_buyer_is_trade():
    attr = {"name": "50 000 ciklų vyriai", "kind": "internal"}
    checks = _mine(_pack(_ad(headline="50 000 ciklų vyriai", attribute=attr)))
    assert _codes(checks) == ["attribute_internal_lead"] and checks[0].ad == 1 and checks[0].value == attr["name"]
    assert "proof line" in message(checks[0]) and "įrodymų eilutę" in message(checks[0], "lt")
    trade = _mine(_pack(_ad(headline="50 000 ciklų vyriai", attribute=attr, audience="Virtuvių montuotojai (B2B)")))
    assert "attribute_internal_lead" not in _codes(trade)


def test_a_visible_attribute_passes_and_a_half_filled_pack_warns_once():
    visible = {"name": "Matinis ąžuolas", "kind": "visible"}
    assert _mine(_pack(_ad(headline="Matinis ąžuolas be pirštų atspaudų", attribute=visible))) == []
    internal_as_proof = _mine(_pack(_ad(primary_text="Gražu. Vyriai išlaiko 50 000 ciklų.", attribute={"name": "50 000 ciklų", "kind": "internal"})))
    assert internal_as_proof == []
    partial = _mine(_pack(_ad(attribute=visible), _ad(), _ad()))
    assert _codes(partial) == ["attribute_missing"] and partial[0].ad == 2


# --- 76 objection --------------------------------------------------------------


def test_an_objection_needs_a_source_in_the_facts_and_an_answer_in_the_copy():
    facts = FACTS + "Objection: per brangu — runs/calls/call1.txt (4/6)\n"
    sourced = _mine(_pack(_ad(primary_text="Brangu? Atsiperka per du sezonus.", objection={"text": "per brangu", "source": "runs/calls/call1.txt"})), facts)
    assert sourced == []
    guessed = _mine(_pack(_ad(objection={"text": "per brangu", "source": "runs/calls/call9.txt"})), facts)
    assert _codes(guessed) == ["objection_unanswered", "objection_unsourced"]
    assert all(c.ad == 1 and c.value == "per brangu" for c in guessed)


def test_listed_objections_nobody_answers_warn_once_and_each_is_checked_against_the_facts():
    facts = FACTS + "Objection: per brangu — runs/calls/call1.txt\n"
    objections = [{"text": "per brangu", "source": "runs/calls/call1.txt", "count": 4}, {"text": "vogs", "source": "", "count": 1}]
    checks = _mine(_pack(objections=objections), facts)
    assert _codes(checks) == ["objection_missing", "objection_unsourced"]
    assert next(c for c in checks if c.code == "objection_unsourced").value == "vogs"


# --- 85 adoption ---------------------------------------------------------------


def test_a_launch_pack_for_enthusiasts_only_warns():
    checks = _mine(_pack(_ad(adoption_stage="enthusiast"), _ad(adoption_stage="enthusiast")))
    assert _codes(checks) == ["adoption_single_stage"]
    mixed = _mine(_pack(_ad(adoption_stage="enthusiast"), _ad(adoption_stage="mainstream", primary_text="312 vartotojų jau pjauna taip. Veja nupjauta.")))
    assert mixed == []


def test_a_mainstream_ad_needs_proof_and_a_half_marked_pack_warns_once():
    no_proof = _mine(_pack(_ad(adoption_stage="mainstream")))
    assert _codes(no_proof) == ["adoption_no_proof"] and no_proof[0].ad == 1
    with_proof = _mine(_pack(_ad(adoption_stage="mainstream", primary_text="Garantija dvejiems metams. Veja nupjauta.")))
    assert with_proof == []
    partial = _mine(_pack(_ad(adoption_stage="enthusiast"), _ad()))
    assert _codes(partial) == ["adoption_missing"] and partial[0].ad == 2


# --- 90 outcomes ---------------------------------------------------------------


def test_outcomes_without_feeling_or_standing_warn_once():
    checks = _mine(_pack(_ad(outcomes={"functional": "nupjauta veja"}), _ad(outcomes={"functional": "nupjauta veja"})))
    assert _codes(checks) == ["outcome_functional_only"]
    ok = _mine(_pack(_ad(outcomes={"functional": "nupjauta veja", "emotional": "ramus savaitgalis"}, headline="Ramus savaitgalis be pjovimo")))
    assert ok == []


def test_a_stated_outcome_must_reach_the_copy():
    checks = _mine(_pack(_ad(outcomes={"functional": "veja", "emotional": "ramybė", "social": "tvarkingas kaimynams"})))
    assert _codes(checks) == ["outcome_not_in_copy", "outcome_not_in_copy"]
    assert {c.field for c in checks} == {"emotional", "social"}
    assert "(laukas social)" in message(next(c for c in checks if c.field == "social"), "lt")
    partial = _mine(_pack(_ad(outcomes={"functional": "veja", "emotional": "kava", "social": ""}), _ad()))
    assert _codes(partial) == ["outcome_missing"]


# --- 91 buyer ------------------------------------------------------------------


def test_a_business_pack_without_a_payer_warns_and_a_consumer_pack_does_not():
    b2b = _pack(_ad(audience="Vežėjai, logistikos įmonės tiekėjams"))
    assert "buyer_missing" in _codes(_mine(b2b))
    assert "buyer_missing" not in _codes(_mine(_pack()))


def test_a_payer_who_is_not_the_user_needs_one_ad_in_his_number_with_a_source():
    buyer = {"payer": "savininkas", "user": "vairuotojas", "money_metric": "kuro sąnaudos", "money_value": "240 €/vilkikui", "source": SOURCE}
    gap = _mine(_pack(buyer=buyer))
    assert _codes(gap) == ["buyer_payer_gap"] and gap[0].value == "savininkas"
    spoken = _mine(_pack(_ad(primary_text="Kuro sąnaudos mažesnės 240 €/vilkikui per mėnesį."), buyer=buyer))
    assert spoken == []
    unsourced = _mine(_pack(_ad(primary_text="Kuro sąnaudos mažesnės."), buyer={**buyer, "source": "https://other.example/x"}))
    assert _codes(unsourced) == ["buyer_money_unsourced"] and unsourced[0].value == "240 €/vilkikui"
    same_person = _mine(_pack(buyer={"payer": "savininkas", "user": "Savininkas", "money_metric": "kuras", "money_value": ""}))
    assert same_person == []


# --- 94 end market -------------------------------------------------------------


def test_a_supplier_pack_must_name_the_end_market():
    ad = _ad(audience="Maisto gamintojai", segment={"size": "40", "buying_power": "Vidutinė", "reach_cost": "Žema", "distinct": "Tiekėjo klientai"})
    assert "end_market_missing" in _codes(_mine(_pack(ad)))
    traced = {"product": "fasuotas maistas", "indicator": "gamyba LT", "trend": "down", "value_pct": -7, "source": SOURCE}
    assert "end_market_missing" not in _codes(_mine(_pack(ad, end_market=traced)))


def test_an_end_market_indicator_needs_a_url_the_research_saw():
    market = {"product": "fasuotas maistas", "indicator": "gamyba LT -7 %", "trend": "down", "source": "https://osp.stat.gov.lt/x"}
    checks = _mine(_pack(end_market=market))
    assert _codes(checks) == ["end_market_unsourced"] and checks[0].value == "gamyba LT -7 %"
    assert _mine(_pack(end_market={**market, "source": SOURCE})) == []


# --- 95 occasion ---------------------------------------------------------------


def test_an_ad_that_sells_to_oneself_and_as_a_gift_at_once_warns():
    checks = _mine(_pack(_ad(primary_text="Plytelė sau po darbo arba dovana kolegai.", occasion={"when": "gruodis", "for_whom": "self", "kind": "routine"})))
    assert "occasion_mixed" in _codes(checks)
    mixed = next(c for c in checks if c.code == "occasion_mixed")
    assert mixed.ad == 1 and "sau" in mixed.value and "dovan" in mixed.value
    assert "occasion_mixed" not in _codes(_mine(_pack(_ad(primary_text="Saugus sausas sandėlis dovanoms."))))


def test_a_seasonal_brief_needs_more_than_one_occasion():
    facts = FACTS + "Season: Kalėdos\n"
    assert _codes(_mine(_pack(), facts)) == ["occasion_missing"]
    one = {"when": "gruodis", "for_whom": "self", "kind": "routine"}
    single = _mine(_pack(_ad(occasion=one), _ad(occasion=one)), facts)
    assert _codes(single) == ["occasion_single"] and single[0].value == "self"
    two = _mine(_pack(_ad(occasion=one), _ad(occasion={"when": "gruodis", "for_whom": "gift", "kind": "gift"})), facts)
    assert two == []


# --- 96 place effort -----------------------------------------------------------


def test_a_shop_in_the_offer_needs_the_shopping_effort_class_once():
    retail = _ad(mix={"product": "Aliejus", "price": "14,90 €", "place": "Maxima ir Iki"})
    checks = _mine(_pack(retail, retail))
    assert _codes(checks) == ["place_effort_missing"] and checks[0].ad == 1
    classed = _mine(_pack({**retail, "place_effort": {"class": "shopping", "evidence": "lyginama 3–5 aliejai", "source": SOURCE}}))
    assert classed == []


def test_the_in_store_plan_must_follow_the_class():
    retail = {"product": "Aliejus", "price": "14,90 €", "place": "Maxima"}
    grab = _mine(_pack(_ad(mix=retail, angle="Degustatorė prie lentynos", place_effort={"class": "convenience", "evidence": "dažnas pirkinys"})))
    assert _codes(grab) == ["place_effort_mismatch"] and grab[0].value == "convenience"
    sought = _mine(_pack(_ad(mix=retail, angle="Stendas prie kasos", place_effort={"class": "specialty", "evidence": "ieškoma pagal vardą"})))
    assert _codes(sought) == ["place_effort_mismatch"]
    assert _mine(_pack(_ad(mix=retail, angle="Degustacija", place_effort={"class": "shopping", "evidence": "lygina"}))) == []


# --- 97 zmot -------------------------------------------------------------------


def test_zmot_lines_in_the_facts_must_reach_the_pack_and_each_source_must_be_known():
    facts = FACTS + "ZMOT: research | YouTube apžvalga | https://youtube.com/watch?v=1\n"
    assert _codes(_mine(_pack(), facts)) == ["zmot_missing"]
    assert _codes(_mine(_pack())) == []
    rows = [{"stage": "research", "source": "YouTube apžvalga", "url": "https://youtube.com/watch?v=1", "trusted_because": "2 100 peržiūrų"}]
    assert _mine(_pack(zmot=rows), facts) == []
    unknown = _mine(_pack(zmot=[{**rows[0], "url": "https://tiktok.com/x"}]), facts)
    assert _codes(unknown) == ["zmot_unsourced"] and unknown[0].value == "YouTube apžvalga"


def test_zmot_needs_a_research_stage_and_ads_must_land_on_a_trusted_host():
    facts = FACTS + "ZMOT: buy | pigu.lt | https://pigu.lt/navimow\n"
    rows = [{"stage": "buy", "source": "pigu.lt", "url": "https://pigu.lt/navimow"}]
    assert _codes(_mine(_pack(zmot=rows), facts)) == ["zmot_stage_gap"]
    rows[0]["stage"] = "compare"
    assert _mine(_pack(_ad(landing_url="https://www.pigu.lt/navimow"), _ad(landing_url=f"{SITE}/pirkti"), zmot=rows), facts) == []
    stray = _mine(_pack(_ad(landing_url="https://amazon.de/x"), zmot=rows), facts)
    assert _codes(stray) == ["zmot_destination"] and stray[0].ad == 1 and stray[0].value == "amazon.de"


# --- 98 reviews ----------------------------------------------------------------


def test_copy_that_leans_on_reviews_needs_recorded_review_themes():
    assert _codes(_mine(_pack(_ad(primary_text="4,7/5 iš 312 atsiliepimų.")))) == ["review_signals_missing"]
    assert _codes(_mine(_pack())) == []


def test_a_promise_the_reviews_contradict_warns_unless_the_ad_addresses_it():
    signals = [
        {"theme": "pristatymas vėluoja", "polarity": "neg", "count": 7, "example_url": "https://maps.google.com/x", "latest_date": "2026-09-14"},
        {"theme": "pristatymas vėluoja", "polarity": "neg", "count": 2, "example_url": "https://maps.google.com/y", "latest_date": "2026-09-14"},
        {"theme": "graži apdaila", "polarity": "pos", "count": 9, "example_url": "https://maps.google.com/z", "latest_date": "2026-09-14"},
    ]
    checks = _mine(_pack(_ad(headline="Pristatymas per 4 savaites"), review_signals=signals))
    assert _codes(checks) == ["review_contradicted"] and checks[0].n == 7 and checks[0].ad == 1
    assert "7 reviews" in message(checks[0]) and "7 atsiliepimų" in message(checks[0], "lt")
    addressed = _mine(_pack(_ad(headline="Pristatymas per 4 savaites", angle="address_objection: terminas sutartyje"), review_signals=signals))
    assert addressed == []
    assert _mine(_pack(_ad(headline="Graži apdaila", description="Nuo 1 299 €"), review_signals=signals)) == []


# --- 103 prior exposure --------------------------------------------------------


def test_introducing_a_category_most_buyers_already_know_warns():
    prior = {"belief": "kombuča yra sveikas gėrimas", "share_pct": 70, "source": SOURCE}
    checks = _mine(_pack(_ad(angle="Atraskite kombučą", prior_exposure=prior)))
    assert _codes(checks) == ["exposure_mismatch"] and checks[0].n == 70
    assert "70 %" in message(checks[0]) and "70 %" in message(checks[0], "lt")
    assert _mine(_pack(_ad(angle="Atraskite kombučą", prior_exposure={**prior, "share_pct": 20}))) == []
    assert _mine(_pack(_ad(angle="Jūsų mėgstama kombuča", prior_exposure=prior))) == []


def test_prior_exposure_without_a_share_or_a_known_source_is_unverified():
    no_share = _mine(_pack(_ad(prior_exposure={"belief": "žino", "source": SOURCE})))
    assert _codes(no_share) == ["exposure_unsourced"]
    stray = _mine(_pack(_ad(prior_exposure={"belief": "žino", "share_pct": "70", "source": "https://other.example/x"})))
    assert _codes(stray) == ["exposure_unsourced"]
    assert _mine(_pack(_ad(prior_exposure={"belief": "žino", "share_pct": "70", "source": SOURCE}))) == []


# --- 105 triggers --------------------------------------------------------------


def test_a_channel_recommendation_on_fewer_than_five_triggers_is_provisional():
    rows = [{"buyer": f"b{i}", "immediate": "need", "earlier_cues": ["draugas"], "source": "data/survey.xlsx"} for i in range(3)]
    facts = FACTS + "Triggers: data/survey.xlsx\n"
    checks = _mine(_pack(growth={"kind": "development", "reach": "Facebook grupės"}, triggers=rows), facts)
    assert _codes(checks) == ["trigger_too_few"] and checks[0].n == 3 and checks[0].limit == 5
    assert "Only 3" in message(checks[0]) and "below 5" in message(checks[0])
    assert _mine(_pack(triggers=rows), facts) == []
    five = rows + [{**rows[0], "buyer": "b4"}, {**rows[0], "buyer": "b5"}]
    assert _mine(_pack(growth={"kind": "development", "reach": "Facebook grupės"}, triggers=five), facts) == []


def test_each_trigger_must_come_from_a_recorded_source():
    rows = [{"buyer": "Jonas", "immediate": "ad", "earlier_cues": [], "source": "data/other.xlsx"}]
    checks = _mine(_pack(triggers=rows), FACTS + "Triggers: data/survey.xlsx\n")
    assert _codes(checks) == ["trigger_unsourced"] and checks[0].value == "Jonas"
    assert _mine(_pack(triggers=rows), facts=None) == []


# --- 106 shortlist spec --------------------------------------------------------


def test_a_shortlist_spec_in_the_facts_must_reach_the_pack_with_its_source():
    facts = FACTS + f"Shortlist spec: duomenų kiekis (GB) — {SOURCE}\n"
    assert _codes(_mine(_pack(), facts)) == ["shortlist_missing"]
    spec = {"name": "duomenų kiekis", "value": "15 GB", "source_url": "https://palygink.lt/x"}
    checks = _mine(_pack(_ad(headline="15 GB už 7,99 €"), shortlist_spec=spec), facts)
    assert _codes(checks) == ["shortlist_unsourced"] and checks[0].value == "duomenų kiekis"


def test_a_challenger_must_lead_with_the_spec():
    spec = {"name": "duomenų kiekis", "value": "15 GB", "source_url": SOURCE}
    buried = _mine(_pack(
        _ad(test_role="control", headline="15 GB greitis"),
        _ad(test_role="challenger", headline="5G greitis", primary_text="Greičiausias tinklas Lietuvoje visą parą. Dabar 15 GB."),
        shortlist_spec=spec,
    ))
    assert _codes(buried) == ["shortlist_buried"] and buried[0].value == "15 GB" and buried[0].limit == 40
    led = _mine(_pack(_ad(test_role="control", headline="5G"), _ad(test_role="challenger", primary_text="Visi 15 GB už 7,99 €. Jokio 5G triukšmo."), shortlist_spec=spec))
    assert led == []
    assert _mine(_pack(_ad(headline="15 GB"), shortlist_spec=spec)) == []


# --- 117 appeal ----------------------------------------------------------------


def test_a_recorded_personal_risk_needs_an_ad_that_addresses_it():
    facts = FACTS + 'Personal risk: "Jei sandėlis sustos per Kalėdas, kaltas būsiu aš" (interview)\n'
    checks = _mine(_pack(_ad(appeal="operational")), facts)
    assert _codes(checks) == ["appeal_no_risk_variant", "occasion_missing"]
    assert _codes(_mine(_pack(_ad(appeal="operational"), _ad(appeal="personal_risk")), facts)) == ["occasion_missing"]
    assert _codes(_mine(_pack(_ad(appeal="both")), facts)) == ["occasion_missing"]


def test_a_business_pack_whose_facts_never_asked_about_personal_risk_is_told_to_ask():
    b2b = _ad(audience="Logistikos vadovai, B2B")
    assert "appeal_risk_unknown" in _codes(_mine(_pack(b2b)))
    assert "appeal_risk_unknown" in _codes(_mine(_pack(b2b), FACTS + "Personal risk: not researched\n"))
    assert "appeal_risk_unknown" not in _codes(_mine(_pack(b2b), facts=None))
    assert "appeal_risk_unknown" not in _codes(_mine(_pack()))


# --- 118 roles -----------------------------------------------------------------


def test_every_role_in_the_buying_unit_needs_an_ad():
    unit = [
        {"role": "IT vadovas", "cares_about": "integracija", "source": SOURCE},
        {"role": "finansų direktorius", "cares_about": "kaina", "source": SOURCE},
    ]
    checks = _mine(_pack(_ad(for_role="IT vadovas"), buying_unit=unit))
    assert _codes(checks) == ["role_uncovered"] and checks[0].value == "finansų direktorius"
    assert _mine(_pack(_ad(for_role="IT vadovas"), _ad(for_role="Finansų direktorius"), buying_unit=unit)) == []
    lone = _mine(_pack(_ad(for_role="IT vadovas"), buying_unit=[unit[0]]))
    assert _codes(lone) == ["role_unknown"]
    assert _codes(_mine(_pack(_ad(for_role="IT vadovas"), _ad(for_role="finansų direktorius"), buying_unit=[unit[0], {**unit[1], "source": "unknown"}]))) == ["role_unknown"]


def test_an_ad_for_a_non_technical_role_may_not_use_technical_terms():
    unit = [{"role": "IT vadovas", "cares_about": "x", "source": SOURCE}, {"role": "finansų direktorius", "cares_about": "y", "source": SOURCE}]
    terms = ["API", "SSO", "webhook"]
    checks = _mine(_pack(
        _ad(for_role="IT vadovas", primary_text="Atviras API ir SSO."),
        _ad(for_role="finansų direktorius", primary_text="Pilnas API prieinamumas be papildomų mokesčių."),
        buying_unit=unit, technical_terms=terms,
    ))
    assert _codes(checks) == ["role_jargon"] and checks[0].ad == 2 and checks[0].value == "API"
    assert "finansų direktorius" in message(checks[0]) and "„API“" in message(checks[0], "lt")
    assert _mine(_pack(_ad(for_role="IT vadovas", primary_text="API"), _ad(for_role="finansų direktorius", primary_text="Rapidas"), buying_unit=unit, technical_terms=terms)) == []


# --- 119 involvement -----------------------------------------------------------


def test_a_high_feeling_purchase_needs_an_emotional_ad_and_a_low_one_a_rational_ad():
    high = {"effort": "high", "feeling": "high", "source": SOURCE}
    checks = _mine(_pack(_ad(register="rational"), _ad(register="rational"), involvement=high))
    assert _codes(checks) == ["involvement_register_mismatch"] and checks[0].value == "high"
    assert _mine(_pack(_ad(register="rational"), _ad(register="emotional"), involvement=high)) == []
    low = {"effort": "low", "feeling": "low", "source": "clarify"}
    assert _codes(_mine(_pack(_ad(register="emotional"), involvement=low))) == ["involvement_register_mismatch"]
    assert _mine(_pack(_ad(register="emotional"), _ad(register="rational"), involvement=low)) == []


def test_unknown_or_unsourced_involvement_is_a_question_not_a_guess():
    checks = _mine(_pack(involvement={"effort": "high", "feeling": "unknown", "source": ""}))
    assert _codes(checks) == ["involvement_unknown", "involvement_unsourced"]
    assert _codes(_mine(_pack(involvement={"effort": "high", "feeling": "high", "source": "https://other.example/x"}))) == ["involvement_unsourced"]
    assert _mine(_pack(involvement={"effort": "high", "feeling": "high", "source": "clarify"})) == []


# --- 122 / 123 setting ---------------------------------------------------------


def test_food_without_a_setting_warns_once_and_a_pack_with_no_plain_control_is_untested():
    checks = _mine(_pack(_ad(), _ad(), category="food"))
    assert _codes(checks) == ["setting_missing"] and checks[0].value == "food"
    assert _mine(_pack(_ad(), _ad(), category="software")) == []
    scene = {"occasion": "sekmadienio pusryčiai", "scene": "duona ant stalo su šeima", "source": SOURCE, "achievable": True}
    assert _codes(_mine(_pack(_ad(setting=scene), _ad(setting=scene), category="food"))) == ["setting_untested"]
    assert _mine(_pack(_ad(test_role="control"), _ad(test_role="challenger", setting=scene), category="food")) == []


def test_a_setting_needs_a_source_or_the_word_hypothesis():
    scene = {"occasion": "pusryčiai", "scene": "duona ant stalo", "source": ""}
    checks = _mine(_pack(_ad(), _ad(setting=scene)))
    assert _codes(checks) == ["setting_unsourced"] and checks[0].ad == 2 and checks[0].value == "duona ant stalo"
    assert _mine(_pack(_ad(), _ad(setting={**scene, "source": "hypothesis"}))) == []
    assert _codes(_mine(_pack(_ad(), _ad(setting={**scene, "source": "https://other.example/x"})))) == ["setting_unsourced"]


def test_a_scene_beyond_the_segment_or_without_its_buying_power_warns():
    scene = {"occasion": "vakaras", "scene": "pirtis prie ežero vilos", "source": "hypothesis", "achievable": False}
    checks = _mine(_pack(_ad(), _ad(setting=scene)))
    assert _codes(checks) == ["setting_aspirational"] and checks[0].value == "pirtis prie ežero vilos"
    assert _codes(_mine(_pack(_ad(), _ad(setting={**scene, "achievable": "ne"})))) == ["setting_aspirational"]
    poor = _ad(setting={**scene, "achievable": True}, segment={"size": "a", "buying_power": "", "reach_cost": "c", "distinct": "d"})
    found = _mine(_pack(_ad(), poor))
    assert _codes(found) == ["setting_mismatch"] and found[0].ad == 2


# --- registry ------------------------------------------------------------------


def test_every_code_has_an_element_record_in_both_languages():
    codes = set(buying.MESSAGES["en"])
    assert codes == set(buying.MESSAGES["lt"])
    for code in codes:
        element = playbook.for_code(code)
        assert element is not None, code
        assert element.number in range(70, 124) and element.applies == ("ads",) and element.enforced == "check"
        assert element.question["en"] and element.question["lt"] and element.why["en"] and element.why["lt"]
        assert set(element.manual) <= set(element.codes)
    numbers = {e.number for e in playbook.elements() if e.codes and set(e.codes) & codes}
    assert numbers == {70, 76, 85, 90, 91, 94, 95, 96, 97, 98, 103, 105, 106, 117, 118, 119, 122, 123}


def test_messages_format_with_every_placeholder_and_without_leftover_braces():
    for language in ("en", "lt"):
        for code in buying.MESSAGES[language]:
            text = message(Check("warn", code, ad=1, field="setting", value="v", n=3, limit=5), language)
            assert "{" not in text and "}" not in text, (code, language)


def test_worker_tools_advertise_the_buying_fields():
    spec = next(t for t in worker_tools(False) if t["function"]["name"] == "ad_board")
    props = spec["function"]["parameters"]["properties"]
    for name in buying.PACK_FIELDS:
        assert name in props and props[name].get("description"), name
    for name in buying.AD_FIELDS:
        assert name in props["ads"]["items"]["properties"], name
    assert props["ads"]["items"]["properties"]["occasion"]["properties"]["for_whom"]["enum"] == ["self", "gift", "business", "other"]
    assert props["involvement"]["properties"]["feeling"]["enum"] == ["low", "high", "unknown"]
