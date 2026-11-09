"""Trust checks on the ad board: word of mouth, PR and endorsements, retail and sales, media placement, measurement."""

import pytest

from rocky import playbook
from rocky.adboard import check_pack, message, normalize
from rocky.adchecks import trust
from rocky.tools import worker_tools

SOURCE = "https://brand.lt/faktai"
FACTS = f"faktas — {SOURCE}\n"


def _ad(**extra):
    return {
        "primary_text": "Rinkodara, kuri atsiperka.",
        "headline": "Augimas be spėlionių",
        "description": "Pokalbis nemokamai",
        "cta": "Sužinoti daugiau",
        "angle": "Rezultatas",
        "audience": "E. parduotuvių vadovai",
        "mix": {"product": "Reklamos valdymas", "price": "Nuo 990 € per mėnesį", "place": "Internetu, Lietuvoje"},
        "segment": {"size": "3 000 (įvertis)", "buying_power": "Vidutinė", "reach_cost": "Žema", "distinct": "Kita problema"},
        **extra,
    }


def _pack(*ads, **extra):
    return {
        "brand": "APG Media",
        "url": "https://apgmedia.lt",
        "ads": list(ads) or [_ad()],
        "claims": [],
        "table_stakes": [{"outcome": "Atsakymas", "minimum": "Per dieną", "status": "met"}],
        **extra,
    }


def _trust(pack, facts=FACTS):
    """Only this module's findings, on a normalized pack."""
    return trust.check(normalize(pack), facts, {})


def _codes(checks):
    return sorted(c.code for c in checks)


def _one(checks, code):
    found = [c for c in checks if c.code == code]
    assert len(found) == 1, _codes(checks)
    return found[0]


# --- the contract ------------------------------------------------------------------------------------------


def test_a_plain_pack_gets_no_trust_findings():
    assert _trust(_pack()) == []


def test_every_code_has_an_element_record_in_both_languages():
    for code in trust.MESSAGES["en"]:
        element = playbook.for_code(code)
        assert element is not None, code
        assert element.question["en"] and element.question["lt"] and element.why["en"] and element.why["lt"]
        assert element.applies == ("ads",) and element.enforced == "check"
        assert code in trust.MESSAGES["lt"], code


def test_every_element_code_belongs_to_this_module_and_messages_format():
    from rocky.adboard import Check

    mine = set(trust.MESSAGES["en"])
    for element in playbook.elements():
        if element.number in (256, 308, 424, 491):
            assert set(element.codes) <= mine
    for code in mine:
        for language in ("en", "lt"):
            text = message(Check("warn", code, ad=1, field="headline", value="v", n=3, limit=5), language)
            assert "{" not in text and code not in text


def test_worker_tools_advertise_the_fields():
    spec = next(t for t in worker_tools(False) if t["function"]["name"] == "ad_board")
    props = spec["function"]["parameters"]["properties"]
    for name in trust.PACK_FIELDS:
        assert name in props and props[name].get("description"), name
    for name in trust.AD_FIELDS:
        assert name in props["ads"]["items"]["properties"], name


def test_the_module_runs_inside_check_pack_and_normalize_keeps_its_fields():
    pack = normalize(_pack(_ad(defect={"grade": "returned", "flaw": "", "price_reason": ""})))
    assert pack["ads"][0]["defect"]["grade"] == "returned"
    assert "defect_undisclosed" in [c.code for c in check_pack(pack, FACTS, {1: "x"})]


# --- #256 wom ------------------------------------------------------------------------------------------------


def test_a_referral_ad_without_a_community_warns_once():
    checks = _trust(_pack(_ad(cta="Pakviesti draugą", referral={"payoff": "Atrodysi išmanantis", "source": SOURCE})))
    assert _codes(checks) == ["retell_missing", "wom_missing"]


def test_a_community_needs_a_fetched_venue_and_a_size():
    wom = {"group": "Tech Park Slack", "venue_url": "https://unknown.example/x", "members": 0}
    checks = _trust(_pack(wom=wom))
    assert _codes(checks) == ["wom_unsized", "wom_venue_unknown"]
    assert _trust(_pack(wom={**wom, "venue_url": SOURCE, "members": 2300})) == []


# --- #262 referral -------------------------------------------------------------------------------------------


def test_a_share_cta_without_a_recommender_payoff_warns():
    checks = _trust(_pack(_ad(cta="Share", wom=None), retell={"claim": "x", "competitors": [{}, {}]}, wom={"venue_url": SOURCE, "members": 5}))
    assert "referral_missing" in _codes(checks) and _one(checks, "referral_missing").ad == 1


def test_a_hypothesis_payoff_is_flagged_with_the_interview_question():
    referral = {"payoff": "Draugas matys, kad išmanai", "kind": "look_knowledgeable", "source": "hypothesis"}
    checks = _trust(_pack(_ad(cta="Pakviesti draugą", referral=referral), wom={"venue_url": SOURCE, "members": 5}, retell={"competitors": [{"source": SOURCE}, {"source": SOURCE}], "claim": "pigiausia", "our_value": 0, "direction": "lower_better"}))
    assert "referral_unverified" in _codes(checks) and "referral_missing" not in _codes(checks)
    assert "išmanantis" in message(_one(checks, "referral_unverified"), "lt")


# --- #265 visibility -----------------------------------------------------------------------------------------


def test_a_forced_share_feature_without_metric_or_baseline_warns_three_ways():
    checks = _trust(_pack(visibility=[{"what": "streak badge", "opt_in": False, "surface": "profile"}]))
    assert _codes(checks) == ["wom_share_no_baseline", "wom_share_no_metric", "wom_share_not_optional"]
    assert all(c.value == "streak badge" for c in checks)


def test_an_opt_in_feature_with_metric_and_baseline_passes():
    feature = {"what": "streak badge", "opt_in": True, "surface": "profile", "metric": "shares per active user", "baseline": 0.44, "unit": "%"}
    assert _trust(_pack(visibility=[feature])) == []


# --- #266 retell ---------------------------------------------------------------------------------------------


def _retell(our=0, competitors=((1, SOURCE), (1, SOURCE)), direction="lower_better", claim="be atrakinimo mokesčio"):
    return {
        "claim": claim, "metric": "unlock fee", "unit": "EUR", "our_value": our, "direction": direction,
        "competitors": [{"name": f"C{i}", "value": v, "source": s} for i, (v, s) in enumerate(competitors, 1)],
    }


def test_an_extreme_that_a_competitor_matches_is_not_extreme():
    ads = [_ad(headline="Be atrakinimo mokesčio")]
    assert _trust(_pack(*ads, retell=_retell())) == []
    checks = _trust(_pack(*ads, retell=_retell(our=1)))
    assert _codes(checks) == ["retell_not_extreme"] and "1 vs 1, 1" in checks[0].value
    assert _trust(_pack(*ads, retell=_retell(our=5, competitors=((3, SOURCE), (4, SOURCE)), direction="higher_better"))) == []


def test_a_thin_or_unsourced_comparison_and_copy_without_the_distinction_warn():
    checks = _trust(_pack(retell=_retell(competitors=((1, "https://nowhere.example"),))))
    assert _codes(checks) == ["retell_not_in_copy", "retell_thin", "retell_unsourced"]
    assert _one(checks, "retell_thin").n == 1 and _one(checks, "retell_unsourced").value == "C1"


# --- #257 endorsements ---------------------------------------------------------------------------------------


def test_a_paid_voice_without_a_disclosure_warns_and_a_label_clears_it():
    voice = {"who": "Jonas K.", "kind": "paid", "paid_amount": 300}
    ad = _ad(primary_text="Treneris Jonas K.: „Geriausias batonėlis po treniruotės“", endorsements=[voice])
    checks = _trust(_pack(ad))
    assert _codes(checks) == ["pr_undisclosed"] and checks[0].value == "Jonas K."
    assert _trust(_pack({**ad, "primary_text": ad["primary_text"] + " Remiama partnerystė."})) == []
    assert _trust(_pack(_ad(endorsements=[{**voice, "disclosure_text": "#reklama"}]))) == []


def test_an_independent_voice_needs_a_fetched_review_and_an_unknown_kind_is_never_independent():
    checks = _trust(_pack(_ad(endorsements=[{"who": "Delfi", "kind": "independent", "source": "https://delfi.lt/x"}, {"who": "Ona"}])))
    assert _codes(checks) == ["pr_kind_missing", "pr_unverified"]
    assert _trust(_pack(_ad(endorsements=[{"who": "Delfi", "kind": "independent", "source": SOURCE}]))) == []


# --- #287 proof actions --------------------------------------------------------------------------------------


def test_an_abstract_virtue_without_a_proof_action_warns():
    checks = _trust(_pack(_ad(primary_text="Mums rūpi kokybė."), _ad()))
    assert _codes(checks) == ["pr_proof_missing"] and checks[0].ad == 1 and checks[0].value == "rūpi"


def test_a_proof_action_needs_a_source_the_marketer_supplied():
    action = {"claim": "kokybė", "what": "42 punktų sąrašas", "where_visible": "registratūra", "source": "deliverables/in/checklist_2026.pdf"}
    ad = _ad(primary_text="Mums rūpi kokybė.")
    assert _trust(_pack(ad, proof_actions=[action])) == []
    assert _codes(_trust(_pack(ad, proof_actions=[{**action, "source": "https://nowhere.example/a"}]))) == ["pr_proof_unsourced"]


# --- #307 backer ---------------------------------------------------------------------------------------------


def _proof_pack(challenger_headline="Šilumos siurbliai", backers=("independent_review", "industry_body")):
    proof_a, proof_b = "Testas 4,6/5", "Rekomenduoja LŠĮA"
    control = _ad(test_role="control", proof_test=True, primary_text=f"Šiluma namams. {proof_a}", headline="Šilumos siurbliai")
    challenger = _ad(test_role="challenger", proof_test=True, primary_text=f"Šiluma namams. {proof_b}", headline=challenger_headline)
    return _pack(
        control, challenger,
        claims=[{"text": proof_a, "source": SOURCE}, {"text": proof_b, "source": SOURCE}],
        backer=[{"claim": proof_a, "backer": backers[0]}, {"claim": proof_b, "backer": backers[1]}],
    )


def test_a_proof_test_on_identical_copy_passes_and_a_changed_headline_confounds_it():
    assert _trust(_proof_pack()) == []
    checks = _trust(_proof_pack(challenger_headline="Nauja antraštė"))
    assert _codes(checks) == ["backer_test_confounded"] and checks[0].field == "headline" and checks[0].ad == 2


def test_a_proof_claim_without_a_backer_or_only_self_issued_ones_warns():
    pack = _proof_pack()
    pack["backer"] = pack["backer"][:1]
    assert _codes(_trust(pack)) == ["backer_missing"]
    assert _codes(_trust(_proof_pack(backers=("self", "self")))) == ["backer_not_independent"]


# --- #308 green ----------------------------------------------------------------------------------------------


def _green(unit_change="g", source=SOURCE):
    return {"subject": "pakuotė", "baseline": {"value": 38, "unit": "g", "date": "2025-01"}, "change": {"value": -38, "unit": unit_change}, "source": source}


def test_a_green_word_without_a_measured_baseline_blocks():
    checks = _trust(_pack(_ad(headline="Draugiška gamtai pakuotė")))
    assert _codes(checks) == ["green_unmeasured"]
    assert checks[0].level == "block" and checks[0].value == "Draugiška gamtai" and checks[0].field == "headline"
    assert _trust(_pack(_ad(headline="Draugiška gamtai pakuotė"), green=[_green()])) == []


def test_an_incomplete_green_entry_still_blocks_and_mismatched_units_or_unknown_sources_warn():
    incomplete = {**_green(), "baseline": {"value": 38, "unit": "g"}}
    assert _codes(_trust(_pack(_ad(headline="Sustainable packaging"), green=[incomplete]))) == ["green_unmeasured"]
    checks = _trust(_pack(_ad(headline="Sustainable packaging"), green=[_green(unit_change="kg", source="https://nowhere.example/t")]))
    assert _codes(checks) == ["green_source_unknown", "green_unit_mismatch"]
    assert all(c.level == "warn" for c in checks)


# --- #310 defect ---------------------------------------------------------------------------------------------


def test_graded_stock_with_no_flaw_or_reason_blocks_and_the_flaw_must_be_in_the_copy():
    ad = _ad(headline="Sportmax -45 %", primary_text="Sportmax bėgimo bateliai tik 49 € vietoj 89 €.", defect={"grade": "returned", "flaw": "", "price_reason": ""})
    checks = _trust(_pack(ad))
    assert _codes(checks) == ["defect_undisclosed"] and checks[0].level == "block" and checks[0].value == "returned"
    told = {"grade": "returned", "flaw": "subraižytos dėžutės, lengvi padų pėdsakai", "price_reason": "grąžintos prekės"}
    assert _codes(_trust(_pack({**ad, "defect": told}))) == ["defect_not_in_copy"]
    honest = {**ad, "defect": told, "primary_text": "Grąžinti Sportmax bateliai: subraižytos dėžutės, lengvi padų pėdsakai – todėl 49 € vietoj 89 €."}
    assert _trust(_pack(honest)) == []


def test_a_discount_without_a_condition_warns_once_and_playing_down_a_flaw_warns():
    checks = _trust(_pack(_ad(headline="Išpardavimas -30 %")))
    assert _codes(checks) == ["defect_missing"] and checks[0].level == "warn"
    assert _trust(_pack(_ad(headline="Išpardavimas -30 %", defect={"grade": "new"}))) == []
    soft = _ad(primary_text="Grąžinti bateliai, kaip nauji: subraižytos dėžutės.", defect={"grade": "returned", "flaw": "subraižytos dėžutės", "price_reason": "grąžinta"})
    assert _codes(_trust(_pack(soft))) == ["defect_minimised"]


# --- #311 #312 event plan ------------------------------------------------------------------------------------


def _event_pack(act="lankytojas pats pakepa duonos riekelę mūsų aliejuje ir palygina skonį", kpis=None, flow="Stendas prie įėjimo, ne prie kasų"):
    kpis = [{"metric": "aliejų kategorijos pardavimai", "baseline": 2350, "unit": "EUR", "source": "Maxima"}] if kpis is None else kpis
    return _pack(
        _ad(),
        kind="event_plan",
        demo={"claim": "išlaiko skonį kaitinant iki 200 °C", "act": act, "judges_by": "skonis", "takeaway": "50 ml mėginys"},
        event={"venue": "Maxima Akropolis", "date": "2026-11-22", "budget_eur": 900, "host": {"name": "Maxima", "flow_check": flow, "host_kpis": kpis}},
        claims=[{"text": "Rūkimo taškas 200 °C", "source": SOURCE}],
    )


def test_an_event_plan_kind_requires_the_demo_and_host_blocks():
    checks = _trust(_pack(_ad(), kind="event_plan"))
    assert _codes(checks) == ["demo_missing", "event_missing"]
    assert _trust(_event_pack()) == []


def test_a_passive_act_an_unsourced_claim_and_host_gaps_are_named():
    checks = _trust(_event_pack(act="pristatome produktą ir dalijame lankstinukus", kpis=[], flow=""))
    assert _codes(checks) == ["demo_passive_act", "event_no_flow_check", "event_no_host_kpi"]
    pack = _event_pack(kpis=[{"metric": "SKU vienetai", "unit": "vnt"}])
    pack["claims"] = []
    pack["demo"]["takeaway"] = ""
    checks = _trust(pack)
    assert _codes(checks) == ["demo_claim_unsourced", "demo_no_takeaway", "event_kpi_no_baseline"]
    assert _one(checks, "demo_claim_unsourced").value == "200" and _one(checks, "event_kpi_no_baseline").value == "SKU vienetai"


# --- #378 stockist -------------------------------------------------------------------------------------------


def test_a_local_offer_without_a_route_for_outsiders_warns():
    local = _ad(mix={"product": "Kuponas", "price": "2 € nuolaida", "place": "Maxima Kaunas parduotuvėse"})
    checks = _trust(_pack(local, pack={"distinctive_assets": [{"cue": "a"}], "shown_in_creative": ["a"]}, channel={"stages": [], "intensity": "intensive"}))
    assert "stockist_missing" in _codes(checks) and _one(checks, "stockist_missing").value == "Kaunas"
    route = {"catchment": "Kaunas", "stockists": [{"name": "Maxima Jonava", "city": "Jonava", "url": SOURCE}], "out_of_area_route": "Jonavos Maxima arba e-shop"}
    assert "stockist" not in " ".join(_codes(_trust(_pack(local, stockist=route, pack={"distinctive_assets": [{"cue": "a"}], "shown_in_creative": ["a"]}, channel={"stages": [], "intensity": "intensive"}))))


def test_a_stockist_route_can_be_empty_or_point_to_an_unknown_url():
    checks = _trust(_pack(stockist={"catchment": "Kaunas", "stockists": [{"name": "Maxima Jonava", "url": "https://nowhere.example"}], "out_of_area_route": ""}))
    assert _codes(checks) == ["stockist_route_missing", "stockist_url_unknown"]
    assert _one(checks, "stockist_url_unknown").value == "Maxima Jonava"


# --- #356 buying situation -----------------------------------------------------------------------------------


def test_copy_for_the_wrong_buying_situation_warns():
    new = _ad(buying_situation="new_task", cta="Order again")
    rebuy = _ad(buying_situation="straight_rebuy", primary_text="Kas yra HEPA filtras ir kodėl reikia jo?")
    checks = _trust(_pack(new, rebuy))
    assert _codes(checks) == ["trade_situation_mismatch", "trade_situation_mismatch"]
    assert [c.ad for c in checks] == [1, 2]
    assert _trust(_pack(_ad(buying_situation="new_task", cta="Download the selection guide"))) == []


def test_an_ad_without_a_situation_warns_only_when_the_others_have_one():
    checks = _trust(_pack(_ad(buying_situation="straight_rebuy"), _ad()))
    assert _codes(checks) == ["trade_situation_missing"] and checks[0].ad == 2
    assert _trust(_pack(_ad(), _ad())) == []


# --- #361 #362 channel ---------------------------------------------------------------------------------------


def _channel(intensity="selective", service="high", stages=None):
    stages = [
        {"stage": "retailer", "owner": "Maxima LT", "contract_with": "Sanitex", "decides": ["availability", "price", "promo"]},
        {"stage": "direct", "owner": "us", "decides": ["price", "promo"]},
    ] if stages is None else stages
    return {"stages": stages, "intensity": intensity, "service_need": service}


def test_a_promo_decided_by_the_retailer_names_who_must_agree():
    maxima = _ad(primary_text="Spalį Maximoje – trečia Bubbly nemokamai", mix={"product": "Kombuča", "price": "2,49 €", "place": "Maxima per Sanitex"})
    cafe = _ad(primary_text="Kavinėse – trečia Bubbly nemokamai", mix={"product": "Kombuča", "price": "2,49 €", "place": "40 kavinių tiesiogiai"})
    stages = [{"stage": "retailer", "owner": "Maxima LT", "decides": ["availability", "price", "promo"]}, {"stage": "direct", "owner": "us", "decides": ["price"]}]
    checks = [c for c in _trust(_pack(maxima, cafe, channel=_channel(stages=stages), pack={"sold_in_retail": False}, chain=[{"role": "shopper", "influence": "buys", "source": SOURCE}])) if c.code.startswith("trade_lever")]
    assert [(c.ad, c.field, c.value) for c in checks] == [
        (1, "availability", "Maxima LT"), (1, "promo", "Maxima LT"), (2, "availability", "Maxima LT"), (2, "promo", "Maxima LT"),
    ]


def test_a_retailer_in_place_without_a_channel_warns_once_and_intensity_is_checked():
    retail = _ad(mix={"product": "E-dviratis", "price": "1 890 €", "place": "Sportland ir Decathlon parduotuvėse"})
    codes = _codes(_trust(_pack(retail)))
    assert codes.count("trade_channel_missing") == 1 and "pack_missing" in codes
    everywhere = _ad(primary_text="Velora dabar visose sporto parduotuvėse Lietuvoje")
    checks = _trust(_pack(everywhere, channel=_channel(stages=[])))
    assert _codes(checks) == ["trade_intensity_mismatch"] and checks[0].value == "selective"
    checks = _trust(_pack(_ad(), channel=_channel(intensity="intensive", service="high")))
    assert _codes(checks) == ["trade_intensity_service"]
    own = [{"stage": "direct", "owner": "us", "decides": ["price", "promo"]}]
    assert _codes(_trust(_pack(_ad(), channel=_channel(intensity="", stages=own)))) == ["trade_intensity_missing"]


# --- #368 entry use case -------------------------------------------------------------------------------------


def test_an_ad_aimed_at_a_named_company_needs_an_entry_use_case_with_sourced_urgency():
    checks = _trust(_pack(_ad(audience="UAB Maxima LT personalo skyrius")))
    assert _codes(checks) == ["trade_entry_missing"]
    entry = {"department": "HR", "application": "300 sezoninių sutarčių", "evidence_url": "https://nowhere.example/job", "budget_owner": "HR direktorė"}
    checks = _trust(_pack(_ad(audience="UAB Maxima LT", entry_use_case=entry)))
    assert _codes(checks) == ["trade_entry_unsourced"] and checks[0].value == "HR"
    assert _trust(_pack(_ad(audience="UAB Maxima LT", entry_use_case={**entry, "evidence_url": SOURCE}))) == []


def test_an_audience_without_a_company_name_needs_no_entry_use_case():
    assert _trust(_pack(_ad(audience="Gamyklų vadovai Baltijos šalyse"))) == []


# --- #380 display --------------------------------------------------------------------------------------------


def _display(audit=None, sample=12):
    return {
        "retailer": "Maxima", "fee_eur": 4000, "stores": 60, "start": "2026-06-01", "end": "2026-06-14", "location": "end-cap",
        "signage": ["wobbler", "shelf strip"], "materials": ["freezer sticker"],
        "verification": {"method": "merchandiser photo audit", "sample_stores": sample, "due_date": "2026-06-04"},
        "audit": audit or [],
    }


def test_an_unaudited_display_cannot_be_judged_and_a_low_execution_rate_is_counted():
    checks = _trust(_pack(display=_display()))
    assert _codes(checks) == ["trade_display_unverified"]
    audit = [{"store": f"S{i}", "present": i < 7} for i in range(12)]
    checks = _trust(_pack(display=_display(audit=audit)))
    assert _codes(checks) == ["trade_display_execution_low"] and (checks[0].n, checks[0].limit) == (7, 12)
    assert _trust(_pack(display=_display(audit=[{"store": "S", "present": True}] * 12))) == []


def test_a_display_agreement_names_every_term_and_a_short_audit_is_flagged():
    display = _display(audit=[{"store": "S1", "present": True}])
    del display["location"]
    display["verification"] = {"method": "", "sample_stores": 12}
    checks = _trust(_pack(display=display))
    assert _codes(checks) == ["trade_display_audit_short", "trade_display_incomplete", "trade_display_incomplete"]
    assert sorted(c.field for c in checks if c.code == "trade_display_incomplete") == ["location", "verification.method"]


# --- #381 co-brand -------------------------------------------------------------------------------------------


def test_a_partner_button_must_lead_to_the_partner_and_claims_need_an_owner():
    co = {"partner": "Grindų meistrai", "host_brand": "Grindų meistrai", "endorser_brand": "Ąžuolas by Medis", "partner_cta": True, "partner_url": "https://grindumeistrai.lt"}
    pack = _pack(co_brand=co, claims=[{"text": "15 metų garantija", "source": SOURCE}])
    checks = _trust(pack)
    assert _codes(checks) == ["trade_cobrand_claims_unowned", "trade_cobrand_cta_mismatch"]
    assert _one(checks, "trade_cobrand_cta_mismatch").value == "apgmedia.lt"
    pack["url"] = "https://www.grindumeistrai.lt/azuolas"
    pack["co_brand"]["claim_owners"] = [{"claim": "15 metų garantija", "brand": "endorser"}]
    assert _trust(pack) == []


def test_a_brand_name_repeated_in_a_headline_warns():
    co = {"host_brand": "Medis", "endorser_brand": "Ąžuolas", "partner_cta": False}
    checks = _trust(_pack(_ad(headline="Ąžuolas by Medis – Ąžuolas jūsų namams"), co_brand=co))
    assert _codes(checks) == ["trade_cobrand_brand_repeated"] and checks[0].value == "ąžuolas"


# --- #385 pack -----------------------------------------------------------------------------------------------


def _shelf_ad():
    return _ad(mix={"product": "Šokoladas", "price": "2,49 €", "place": "Maxima ir Rimi lentynose"})


def test_a_shelf_product_without_pack_cues_warns_and_a_creative_must_carry_a_cue():
    checks = [c for c in _trust(_pack(_shelf_ad(), channel=_channel(), chain=[{"role": "x", "influence": "buys", "source": SOURCE}])) if c.code.startswith("pack")]
    assert _codes(checks) == ["pack_missing"]
    cues = {"sold_in_retail": True, "distinctive_assets": [{"cue": "gold roundel", "kind": "label"}, {"cue": "cocoa pod", "kind": "shape"}], "shown_in_creative": []}
    checks = [c for c in _trust(_pack(_ad(), pack=cues)) if c.code.startswith("pack")]
    assert _codes(checks) == ["pack_not_carried"]
    assert [c for c in _trust(_pack(_ad(), pack={**cues, "shown_in_creative": ["cocoa pod"]})) if c.code.startswith("pack")] == []


def test_cues_every_competitor_shares_are_not_distinct_and_an_unknown_shown_cue_is_named():
    cues = {
        "sold_in_retail": True,
        "distinctive_assets": [{"cue": "black pack", "kind": "color"}, {"cue": "gold roundel", "kind": "label"}],
        "shown_in_creative": ["black pack", "ribbon"],
        "competitor_overlap": [{"cue": "black pack", "competitor": "Lindt"}, {"cue": "gold roundel", "competitor": "Lindt"}],
    }
    checks = _trust(_pack(_ad(), pack=cues))
    assert _codes(checks) == ["pack_cue_unknown", "pack_not_distinct"]
    assert _one(checks, "pack_not_distinct").value == "Lindt" and _one(checks, "pack_cue_unknown").value == "ribbon"


# --- #386 chain ----------------------------------------------------------------------------------------------


def _chain(audience="Namų savininkai Vilniuje"):
    chain = [
        {"role": "interjero dizaineriai", "influence": "recommends", "source": SOURCE},
        {"role": "Senukai category buyer", "influence": "stocks", "source": SOURCE},
        {"role": "namų savininkas", "influence": "buys", "source": "interview:Jonas"},
    ]
    return _pack(_ad(audience=audience), chain=chain)


def test_a_role_that_recommends_with_no_ad_aimed_at_it_is_missed():
    checks = _trust(_chain())
    assert _codes(checks) == ["trade_chain_upstream_missed"] and checks[0].value == "interjero dizaineriai"
    assert _trust(_chain(audience="Interjero dizaineriai Vilniuje")) == []


def test_a_chain_needs_a_buyer_sources_and_plausible_shares_and_a_trade_place_asks_for_one():
    chain = [{"role": "architektai", "influence": "sets", "share_pct": 70, "source": "https://nowhere.example"}, {"role": "rangovai", "influence": "recommends", "share_pct": 50, "source": "interview:Ona"}]
    checks = _trust(_pack(_ad(audience="Architektai ir rangovai"), chain=chain))
    assert _codes(checks) == ["trade_chain_no_buyer", "trade_chain_shares_exceed", "trade_chain_source_unknown"]
    assert _one(checks, "trade_chain_shares_exceed").n == 120 and _one(checks, "trade_chain_source_unknown").value == "architektai"
    trade = _ad(mix={"product": "Grindys", "price": "45 €/m²", "place": "per platintoją ir montuotojus"})
    assert "trade_chain_missing" in _codes(_trust(_pack(trade)))


# --- #387 #491 creative --------------------------------------------------------------------------------------


def test_visuals_that_all_show_the_hero_item_are_unrepresentative():
    ads = [_ad(shows={"item": "Ąžuolinis stalas", "price_eur": 1890}) for _ in range(3)]
    checks = _trust(_pack(*ads, range={"typical_price_eur": 229, "hero_price_eur": 1890}))
    assert _codes(checks) == ["creative_unrepresentative"] and (checks[0].n, checks[0].limit) == (1890, 229)
    ads[1]["shows"] = {"item": "Lentyna KALA", "price_eur": 189}
    assert _trust(_pack(*ads, range={"typical_price_eur": 229})) == []


def test_ads_that_do_not_say_what_they_show_are_counted_once():
    checks = _trust(_pack(_ad(shows={"item": "Stalas", "price_eur": 1890}), _ad(), _ad(), range={"typical_price_eur": 229}))
    assert _codes(checks) == ["creative_shows_missing"] and checks[0].n == 2


def test_an_offer_with_two_benefits_needs_an_ad_that_isolates_one():
    control = _ad(test_role="control")
    bundle = _ad(test_role="challenger", benefits=[{"kind": "price", "text": "€25 saved"}, {"kind": "constraint", "text": "no bag worry"}])
    checks = _trust(_pack(control, bundle))
    assert _codes(checks) == ["creative_confounded"] and checks[0].ad == 2 and checks[0].value == "price + constraint"
    isolating = _ad(test_role="challenger", benefits=[{"kind": "price", "text": "€25 off"}])
    assert _trust(_pack(control, bundle, isolating)) == []


def test_a_single_benefit_or_no_benefits_field_is_silent():
    assert _trust(_pack(_ad(test_role="control"), _ad(test_role="challenger", benefits=[{"kind": "price", "text": "x"}]))) == []


# --- #412 activation -----------------------------------------------------------------------------------------


def test_in_market_buyers_need_an_offer_a_step_and_a_qualified_response():
    checks = _trust(_pack(_ad(cta="Learn more"), activation={"stage": "in_market"}))
    assert _codes(checks) == ["activation_no_metric", "activation_no_offer", "activation_weak_step", "activation_weak_step"]
    assert [c.ad for c in checks if c.code == "activation_weak_step"] == [0, 1]
    assert _one(checks, "activation_no_metric").level == "warn"


def test_a_complete_activation_passes_and_out_of_market_runs_no_checks():
    activation = {"stage": "in_market", "offer": "free 2-hour data-migration audit", "next_step": "book a 15-min slot", "qualified_response": "audit booked by a manufacturer with 20+ employees"}
    assert _trust(_pack(_ad(cta="Book audit"), activation=activation)) == []
    assert _trust(_pack(_ad(cta="Learn more"), activation={"stage": "out_of_market"})) == []


# --- #416 #417 #458 placement --------------------------------------------------------------------------------


def test_a_named_placement_must_have_its_surroundings_previewed():
    unchecked = _ad(placement={"url": "https://www.lrytas.lt/"})
    noted = _ad(placement={"surface": "15min.lt Maistas", "url": "https://www.15min.lt/maistas", "adjacency_checked": True, "adjacency_note": "recipe photography; keep"})
    checks = _trust(_pack(noted, unchecked))
    assert _codes(checks) == ["placement_adjacency_unchecked"] and checks[0].ad == 2 and checks[0].value == "https://www.lrytas.lt/"
    checks = _trust(_pack(_ad(placement={"surface": "lrytas.lt", "adjacency_checked": True})))
    assert _codes(checks) == ["placement_adjacency_note_missing"]


def test_copy_too_long_for_the_dwell_time_warns_with_the_word_count():
    roadside = _ad(headline="Ryte skubi?", primary_text="Kava ir šviežia bandelė už 2,49 € visuose Narvesen kioskuose jau nuo 6 val.", placement={"name": "bus_stop_roadside"})
    checks = [c for c in _trust(_pack(roadside)) if c.code.startswith("dwell")]
    assert _codes(checks) == ["dwell_too_long"] and checks[0].n == 16 and checks[0].limit == 3 and checks[0].value == "bus_stop_roadside"
    assert "16 žodžiai" in message(checks[0], "lt") and "3 s" in message(checks[0], "lt")
    on_bus = _ad(headline="Kava ir bandelė", primary_text="Dvidešimt žodžių telpa į dešimt sekundžių autobuse, kai keleivis sėdi ir skaito ekraną ramiai iki savo stotelės.", placement={"name": "in_vehicle"})
    assert [c for c in _trust(_pack(on_bus)) if c.code.startswith("dwell")] == []


def test_a_custom_placement_needs_its_own_seconds_and_a_feed_ad_is_left_to_the_feed_limits():
    checks = _trust(_pack(_ad(placement={"name": "elevator screen"})))
    assert _codes(checks) == ["dwell_unknown"] and checks[0].value == "elevator_screen"
    assert _trust(_pack(_ad(placement={"name": "elevator screen", "dwell_seconds": 15}))) == []
    assert _trust(_pack(_ad(primary_text="x " * 200, placement={"name": "feed"}))) == []


def test_tracking_needs_a_landing_url_and_a_campaign_and_distinct_utm_content():
    ad1 = _ad(tracking={"landing_url": "https://invohub.lt/registracija", "campaign": "nov_signup", "placements": ["feed", "stories"]})
    ad2 = _ad(tracking={"campaign": "", "placements": ["feed"]})
    ad3 = _ad(tracking={"landing_url": "https://invohub.lt/registracija", "campaign": "nov_signup", "placements": [{"name": "stories", "utm_content": "reels"}, {"name": "reels", "utm_content": "reels"}]})
    checks = _trust(_pack(ad1, ad2, ad3))
    assert _codes(checks) == ["placement_tracking_dup", "placement_tracking_missing", "placement_tracking_missing"]
    assert sorted(c.field for c in checks if c.code == "placement_tracking_missing") == ["campaign", "landing_url"]
    assert _one(checks, "placement_tracking_dup").value == "reels" and _one(checks, "placement_tracking_dup").ad == 3


def test_default_utm_content_is_distinct_per_ad_and_placement():
    tracking = {"landing_url": "https://invohub.lt/r", "campaign": "c", "placements": ["feed", "stories", "reels"]}
    assert _trust(_pack(_ad(tracking=tracking), _ad(tracking=tracking), _ad(tracking=tracking))) == []


# --- #424 #379 sequence --------------------------------------------------------------------------------------


def _launch_sequence(teaser_cta="Reply YES to get early access first", jobs=("teaser", "preview", "launch")):
    return _pack(
        _ad(),
        kind="sequence",
        claims=[{"text": "€19/month", "source": SOURCE}, {"text": "90-second demo", "source": SOURCE}],
        sequence=[
            {"job": jobs[0], "subject": "Something is coming", "body": "We built InvoHub Pro.", "cta": teaser_cta, "send_offset_days": -10},
            {"job": jobs[1], "subject": "See it", "body": "Here is the 90-second demo.", "cta": "Watch the demo", "cta_url": "https://invohub.lt/demo", "send_offset_days": -3},
            {"job": jobs[2], "subject": "Live", "body": "Start today for €19/month.", "cta": "Start for €19/month", "cta_url": "https://invohub.lt/pro", "send_offset_days": 0},
        ],
    )


def test_a_launch_sequence_with_three_distinct_jobs_passes_and_a_sequence_kind_needs_messages():
    assert _trust(_launch_sequence()) == []
    assert _codes(_trust(_pack(_ad(), kind="sequence"))) == ["seq_missing"]


def test_a_selling_teaser_duplicate_jobs_and_a_missing_job_are_each_named():
    checks = _trust(_launch_sequence(teaser_cta="Buy now", jobs=("teaser", "launch", "launch")))
    assert _codes(checks) == ["seq_dup_job", "seq_launch_no_action", "seq_missing_job", "seq_teaser_sells"]
    assert _one(checks, "seq_dup_job").value == "launch" and _one(checks, "seq_missing_job").value == "preview"
    assert _one(checks, "seq_teaser_sells").n == 1
    assert (_one(checks, "seq_launch_no_action").n, _one(checks, "seq_launch_no_action").field) == (2, "price")


def test_a_launch_without_a_link_or_price_an_order_mix_up_a_repeated_verb_and_an_unsourced_number_warn():
    pack = _launch_sequence()
    pack["sequence"][2].update(cta_url="", body="Start today.", cta="Start now", send_offset_days=-20)
    pack["sequence"][1]["cta"] = "Start the demo"
    pack["sequence"][0]["body"] = "Join 3200 others on the list."
    checks = _trust(pack)
    assert _codes(checks) == ["seq_launch_no_action", "seq_launch_no_action", "seq_number_unsourced", "seq_order", "seq_same_cta"]
    assert sorted(c.field for c in checks if c.code == "seq_launch_no_action") == ["cta_url", "price"]
    assert _one(checks, "seq_number_unsourced").value == "3200" and _one(checks, "seq_same_cta").value == "start"


def _followups(parama_body="Valstybės parama siekia iki 50 %. Įrengiame per 3 savaites."):
    return _pack(
        _ad(),
        kind="sequence",
        claims=[{"text": "parama iki 50 %", "source": SOURCE}, {"text": "per 3 savaites", "source": SOURCE}, {"text": "10 kW", "source": SOURCE}],
        inquiries=[{"text": "Kiek kainuoja 10 kW?", "count": 18}, {"text": "Ar yra valstybės parama?", "count": 15}, {"text": "Per kiek laiko sumontuojate?", "count": 9}],
        sequence=[
            {"inquiry": "Kiek kainuoja 10 kW?", "subject": "Kaina", "body": "10 kW elektrinė kainuoja pagal stogą. Paskaičiuosime.", "cta": "Gauti kainą"},
            {"inquiry": "Ar yra valstybės parama?", "subject": "Parama", "body": parama_body, "cta": "Pasitikrinti paramą"},
        ],
    )


def test_follow_ups_must_cover_every_inquiry_and_answer_it_first():
    checks = _trust(_followups())
    assert _codes(checks) == ["seq_inquiry_uncovered"] and checks[0].n == 9 and "sumontuojate" in checks[0].value
    late = _followups(parama_body="Mūsų elektrinės tarnauja ilgai. Įrengiame per 3 savaites. Valstybės parama siekia iki 50 %.")
    assert "seq_inquiry_late" in _codes(_trust(late)) and _one(_trust(late), "seq_inquiry_late").n == 2


def test_a_message_that_never_answers_or_answers_a_question_nobody_asked_warns():
    ignored = _followups(parama_body="Įrengiame per 3 savaites. Mūsų inžinieriai sertifikuoti.")
    assert "seq_inquiry_ignored" in _codes(_trust(ignored))
    pack = _followups()
    pack["sequence"][1]["inquiry"] = "Kokios spalvos moduliai?"
    checks = _trust(pack)
    assert "seq_inquiry_unknown" in _codes(checks) and _one(checks, "seq_inquiry_unknown").n == 2


# --- #439 outcomes -------------------------------------------------------------------------------------------


def test_a_brand_pack_without_outcomes_warns_and_awareness_without_fame_is_named():
    checks = _trust(_pack(_ad(angle="Prekės ženklo žinomumas")))
    assert _codes(checks) == ["outcome_metric_missing"]
    outcomes = [{"metric": "unaided recall", "kind": "awareness", "source": "survey Q1"}]
    assert _codes(_trust(_pack(_ad(angle="Brand"), outcomes=outcomes))) == ["outcome_metric_no_fame"]


def test_awareness_and_fame_together_pass_and_a_sales_pack_needs_no_outcomes():
    outcomes = [{"metric": "unaided recall", "kind": "awareness", "source": "Q1"}, {"metric": "talked about it", "kind": "fame", "source": "Q4"}]
    assert _trust(_pack(_ad(angle="Brand"), outcomes=outcomes)) == []
    assert _trust(_pack(_ad(angle="Kaina"))) == []


# --- #462 archive --------------------------------------------------------------------------------------------


def test_a_result_without_the_copy_and_spend_without_a_source_warn():
    record = {"placement": "Meta feed LT", "spend_eur": 312, "leads": 41, "sales": 9}
    checks = _trust(_pack(_ad(primary_text="", headline="", record=record)))
    assert _codes(checks) == ["archive_result_without_copy", "archive_spend_unsourced"]
    assert _one(checks, "archive_spend_unsourced").value == "312"


def test_a_complete_record_beside_its_copy_passes():
    record = {"placement": "Meta feed LT", "spend_eur": 312, "leads": 41, "sales": 9, "source": "results_2026_spring.xlsx"}
    assert _trust(_pack(_ad(record=record))) == []
    assert _trust(_pack(_ad(record={"placement": "Meta feed", "spend_eur": 0}))) == []


# --- messages in both languages ------------------------------------------------------------------------------


@pytest.mark.parametrize("code", sorted(trust.MESSAGES["en"]))
def test_each_message_formats_in_both_languages(code):
    from rocky.adboard import Check

    check = Check("warn", code, ad=1, field="headline", value="x", n=2, limit=3)
    assert message(check, "en") != message(check, "lt")
    assert "”" not in message(check, "lt") and "„" not in message(check, "en")  # „…“ in Lithuanian, “…” in English
