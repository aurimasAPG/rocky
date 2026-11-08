"""Offer checks on the ad board: product experience, pricing and ad copy, settled by code."""

import datetime as dt

import pytest

from rocky import playbook
from rocky.adboard import check_pack, message, normalize
from rocky.adchecks import offer
from rocky.tools import worker_tools

SOURCE = "https://brand.lt/apie"
FACTS = f"Pristatymas 10–15 d. d. — {SOURCE}\nPro: neribotos sąskaitos, periodinės sąskaitos — https://brand.lt/kainos\n"


def _ad(**over):
    return {
        "primary_text": "Sąskaitos per kelias minutes.",
        "headline": "Apskaita smulkiam verslui",
        "description": "Pokalbis nemokamai",
        "cta": "Sužinoti daugiau",
        "angle": "Rezultatas",
        "audience": "E. parduotuvių vadovai",
        "mix": {"product": "Apskaitos programa", "price": "Nuo 19 € per mėnesį", "place": "Internetu"},
        "segment": {"size": "3 000", "buying_power": "Vidutinė", "reach_cost": "Žema", "distinct": "Kita problema"},
        **over,
    }


def _pack(*ads, **extra):
    return {"brand": "Brand", "url": "https://brand.lt", "ads": list(ads) or [_ad()], "claims": [], **extra}


def _codes(pack, facts=FACTS):
    return [c.code for c in offer.check(normalize(pack), facts, {})]


def _checks(pack, facts=FACTS):
    return offer.check(normalize(pack), facts, {})


# --- #124 complete product ------------------------------------------------------------------------------------------


def test_a_missing_complement_is_named_and_an_unknown_one_is_a_question():
    pack = _pack(complete_product=[
        {"need": "LT diegimo vadovas", "kind": "documentation", "provided_by": "missing"},
        {"need": "Montuotojas", "kind": "service", "provided_by": "unknown"},
        {"need": "Programėlė", "kind": "integration", "provided_by": "us", "source": SOURCE},
    ])
    checks = _checks(pack)
    assert [(c.code, c.value) for c in checks] == [("complement_missing", "LT diegimo vadovas"), ("complement_unknown", "Montuotojas")]
    assert "LT diegimo vadovas" in message(checks[0]) and "„Montuotojas“" in message(checks[1], "lt")


def test_a_provided_complement_needs_a_source_the_research_saw():
    pack = _pack(complete_product=[{"need": "Programėlė", "kind": "integration", "provided_by": "partner", "source": "https://other.example/x"}])
    assert _codes(pack) == ["complement_unsourced"]
    assert _codes(_pack()) == []


# --- #125 ownership map ---------------------------------------------------------------------------------------------


def _ownership(angle="Rezultatas", minutes_ours=2, source=SOURCE):
    rows = [{"stage": s, "ours": "x", "theirs": "y", "minutes_ours": None, "minutes_theirs": None, "source": ""} for s in offer.OWNERSHIP_STAGES]
    rows[2] = {"stage": "cleaning", "ours": "vientisas indas", "theirs": "4 dalys", "minutes_ours": minutes_ours, "minutes_theirs": 8, "source": source}
    return _pack(_ad(angle=angle), ownership_map=rows)


def test_a_stage_where_ours_is_faster_must_become_an_angle():
    assert _codes(_ownership()) == ["ownership_gap_unused"]
    assert _codes(_ownership(angle="cleaning: 2 minutės")) == []
    assert _codes(_ownership(minutes_ours=7)) == []


def test_missing_stages_and_unsourced_minutes_are_reported():
    pack = _ownership(angle="valymas", source="")
    pack["ownership_map"] = pack["ownership_map"][2:]
    codes = _codes(pack)
    assert codes.count("ownership_stage_missing") == 2 and "ownership_unsourced" in codes
    assert {c.value for c in _checks(pack) if c.code == "ownership_stage_missing"} == {"setup", "storage"}


# --- #139 upgrade prompt --------------------------------------------------------------------------------------------


def test_an_upgrade_prompt_that_locks_the_user_out_is_flagged():
    ad = _ad(primary_text="Jūsų paskyra užrakinta.", upgrade={"trigger_limit": "", "unlocks": [], "continue_path": ""})
    codes = _codes(_pack(ad))
    assert codes == ["upgrade_no_trigger", "upgrade_no_unlocks", "upgrade_blocks_work"]
    assert "užrakinta" in message(_checks(_pack(ad))[-1], "lt")


def test_a_complete_upgrade_prompt_with_unlocks_from_the_plan_table_passes():
    good = {"trigger_limit": "10 sąskaitų per mėnesį", "unlocks": ["neribotos sąskaitos"], "continue_path": "10 sąskaitų lieka redaguojamos"}
    assert _codes(_pack(_ad(upgrade=good))) == []
    unsourced = {**good, "unlocks": ["API prieiga"]}
    assert _codes(_pack(_ad(upgrade=unsourced))) == ["upgrade_unlocks_unsourced"]


# --- #142 depth -----------------------------------------------------------------------------------------------------


def test_a_mixed_audience_needs_evidence_for_both_depths():
    ad = _ad(audience="Pradedantys laisvai samdomi ir buhalterių įmonės")
    checks = _checks(_pack(ad, depth={"newcomer_entry": f"pirma sąskaita per 4 žingsnius {SOURCE}", "operator_depth": ""}))
    assert [(c.code, c.value) for c in checks] == [("depth_gap", "operator")]
    assert _codes(_pack(ad, depth={"newcomer_entry": f"x {SOURCE}", "operator_depth": f"CSV eksportas {SOURCE}"})) == []


def test_a_single_level_audience_asks_for_no_depth():
    assert _codes(_pack(_ad(audience="Pradedantys laisvai samdomi"))) == []


# --- #143 switch ----------------------------------------------------------------------------------------------------


def test_an_easy_switch_claim_needs_the_switch_map():
    ad = _ad(primary_text="Pereikite be vargo.")
    assert [(c.code, c.value) for c in _checks(_pack(ad))] == [("switch_claim", "be vargo")]
    assert _codes(_pack(ad, switch={"largest": "Excel istorija", "handled_by": "nemokamas importas"})) == []


def test_the_largest_change_needs_a_handler():
    checks = _checks(_pack(switch={"replaces": ["Excel"], "largest": "Excel istorija", "handled_by": ""}))
    assert [(c.code, c.value) for c in checks] == [("switch_unhandled", "Excel istorija")]


# --- #146 delivered -------------------------------------------------------------------------------------------------


def test_a_promise_better_than_the_delivery_fact_blocks():
    ad = _ad(headline="Pristatymas per 5 darbo dienas")
    checks = _checks(_pack(ad, delivered=[{"quantity": "10–15", "unit": "days", "source": SOURCE}]))
    assert [(c.level, c.code, c.field) for c in checks] == [("block", "delivered_promise_gap", "headline")]
    assert "5 darbo dienas / 10–15 days" in checks[0].value
    assert _codes(_pack(_ad(headline="Pristatymas per 12 darbo dienų"), delivered=[{"quantity": "10–15", "unit": "days", "source": SOURCE}])) == []


def test_a_timed_promise_without_a_delivery_fact_warns_once():
    pack = _pack(_ad(headline="Per 5 dienas"), _ad(headline="Per 3 dienas"))
    assert _codes(pack) == ["delivered_missing"]
    assert _codes(_pack(_ad(primary_text="Dirbame su 200 prekių ženklų."))) == []
    unsourced = _pack(delivered=[{"quantity": "10", "unit": "days", "source": "https://other.example"}])
    assert _codes(unsourced) == ["delivered_unsourced"]


# --- #173 evaluation cue --------------------------------------------------------------------------------------------


def test_a_quality_word_without_a_cue_warns_once_per_ad():
    ad = _ad(primary_text="Šalto spaudimo, aukščiausios kokybės aliejus.", headline="Natūralus skonis")
    codes = _codes(_pack(ad))
    assert codes.count("eval_cue_missing") == 1 and codes.count("proof_quality_unproven") == 1
    assert _codes(_pack(ad, evaluation_cue={"attribute": "tamsiai žalia spalva ir riešutų kvapas"}, quality_evidence=[{"claim": "šalto spaudimo", "process_step": "spaudžiama iki 40 °C", "source": SOURCE}])) == []


def test_a_cue_that_names_only_a_certificate_is_not_observable():
    checks = _checks(_pack(evaluation_cue={"attribute": "ekologiškas sertifikatas"}))
    assert [(c.code, c.value) for c in checks] == [("eval_cue_unobservable", "ekologiškas sertifikatas")]
    assert _codes(_pack(evaluation_cue={"attribute": "tamsiai žalia spalva"})) == []


# --- #174 purchase route --------------------------------------------------------------------------------------------


def test_an_offline_route_must_be_in_the_copy_itself():
    route = {"kind": "retail", "detail": "Kalvarijų turgus, šeštadieniais 8–14 val., tel. +370 600 00000"}
    assert _codes(_pack(purchase_route=route)) == ["route_not_in_copy"]
    assert _codes(_pack(_ad(primary_text="Kalvarijų turguje šeštadieniais."), purchase_route=route)) == []
    assert _codes(_pack(_ad(primary_text="Skambinkite +370 600 00000."), purchase_route=route)) == []


def test_an_offline_route_with_no_detail_anywhere_blocks_and_an_online_one_is_the_boards_job():
    checks = _checks(_pack(purchase_route={"kind": "phone", "detail": ""}))
    assert [(c.level, c.code) for c in checks] == [("block", "route_missing")]
    assert _codes(_pack(purchase_route={"kind": "online", "detail": ""})) == []


# --- #189 premium ---------------------------------------------------------------------------------------------------


def _premium(challenger_text, price=39):
    control = _ad(test_role="control", offer={"price": 29, "improved_outcome": ""}, primary_text="Apskaita už 29 €/mėn.")
    challenger = _ad(test_role="challenger", offer={"price": price, "improved_outcome": "PVM deklaracija automatiškai"}, primary_text=challenger_text)
    return _pack(control, challenger, price_position={"absolute_risk": "low"})


def test_a_dearer_challenger_must_show_the_improvement_and_the_price():
    checks = _checks(_premium("Apskaita už 39 €/mėn."))
    assert [(c.code, c.ad) for c in checks] == [("premium_without_improvement", 2)]
    hidden = _checks(_premium("PVM deklaracija pateikiama automatiškai iki termino."))
    assert [(c.code, c.ad) for c in hidden] == [("premium_hidden", 2)]
    assert "+34.5 %" in hidden[0].value and "39 €" in hidden[0].value
    assert _codes(_premium("PVM deklaracija automatiškai už 39 €/mėn.")) == []


def test_a_cheaper_challenger_is_no_premium_and_a_missing_offer_is_said():
    assert _codes(_premium("Apskaita už 25 €/mėn.", price=25)) == []
    pack = _premium("x")
    pack["ads"][1].pop("offer")
    assert [(c.code, c.n) for c in _checks(pack)] == [("offer_missing", 2)]


# --- #200 price position --------------------------------------------------------------------------------------------


def _position(copy, **over):
    position = {"price": 2400, "absolute_risk": "high", "category_price_low": 2800, "category_price_high": 3200, "category_source": SOURCE, "reassurance": ["guarantee", "financing"], **over}
    return _pack(_ad(primary_text=copy), price_position=position)


def test_a_high_risk_price_needs_reassurance_in_the_copy():
    checks = _checks(_position("Šilumos siurblys su montavimu už 2 400 €. Užsisakyk dabar."))
    assert [(c.code, c.ad) for c in checks] == [("price_position_risk_unaddressed", 1)]
    assert checks[0].value.startswith("2 400 €") and "-20 %" in checks[0].value
    assert _codes(_position("Už 2 400 € su 5 metų garantija ir mokėjimu dalimis.")) == []
    assert _codes(_position("Už 2 400 € su garantija.", reassurance=["none"])) == ["price_position_risk_unaddressed"]


def test_relative_position_is_derived_from_the_category_and_the_sources_checked():
    hidden = _checks(_position("Šilumos siurblys su garantija ir dalimis.", relative=""))
    assert [c.code for c in hidden] == ["price_position_value_hidden"]
    above = _checks(_position("Už 3 500 € su garantija ir dalimis.", price=3500, absolute_risk="low", category_source="https://other.example"))
    assert sorted(c.code for c in above) == ["price_position_premium_unjustified", "price_position_unsourced"]
    assert _codes(_pack(_ad(primary_text="Tik 19 € per mėnesį."))) == ["price_position_missing"]
    assert _codes(_pack(_ad(primary_text="Pigiau nei manote."))) == []


# --- #205 scarcity --------------------------------------------------------------------------------------------------


def _scarcity(copy, **row):
    entry = {"text": "liko tik 6 kambariai", "unit": "rooms", "remaining": 6, "source": SOURCE, "valid_until": "2999-01-01", **row}
    return _pack(_ad(headline=copy), scarcity=[entry])


def test_a_scarcity_phrase_without_a_sourced_limit_blocks():
    checks = _checks(_pack(_ad(headline="Liko tik 6 kambariai")))
    assert [(c.level, c.code, c.value) for c in checks] == [("block", "scarcity_unsourced", "Liko tik")]
    assert "stygiaus" in message(checks[0], "lt")
    assert _codes(_scarcity("Liko tik 6 kambariai", source="https://other.example")) == ["scarcity_unsourced"]
    assert _codes(_scarcity("Liko tik 6 kambariai")) == []


def test_a_stale_date_warns_and_a_different_count_blocks(monkeypatch):
    assert _codes(_scarcity("Liko tik 6 kambariai", valid_until="2000-01-01")) == ["scarcity_stale"]
    assert _codes(_scarcity("Liko tik 6 kambariai", valid_until="")) == ["scarcity_stale"]
    monkeypatch.setattr(offer, "_today", lambda: dt.date(2999, 6, 1))
    assert _codes(_scarcity("Liko tik 6 kambariai")) == ["scarcity_stale"]
    monkeypatch.setattr(offer, "_today", lambda: dt.date(2026, 1, 1))
    checks = _checks(_scarcity("Liko tik 4 kambariai"))
    assert [(c.level, c.code, c.value) for c in checks] == [("block", "scarcity_count_mismatch", "6")]


# --- #207 combined versus single ------------------------------------------------------------------------------------


def _arms(combined_attrs, single_two=False):
    single = [
        _ad(arm="single", attributes=[{"kind": "evidence", "text": "120 Google atsiliepimų"}] + ([{"kind": "delivery", "text": "montavimas per 7 d."}] if single_two else [])),
        _ad(arm="single", attributes=[{"kind": "benefit", "text": "5 metų garantija"}]),
    ]
    combined = [_ad(arm="combined", attributes=[{"kind": "evidence", "text": t} for t in combined_attrs])] if combined_attrs is not None else []
    return _pack(_ad(test_role="control"), *single, *combined, test_design={"kind": "combined_vs_single"})


def test_the_combined_arm_must_carry_every_single_attribute():
    checks = _checks(_arms(["120 Google atsiliepimų"]))
    assert [(c.level, c.code, c.ad, c.value) for c in checks] == [("block", "combo_incomplete", 4, "5 metu garantija")]
    assert _codes(_arms(["120 Google atsiliepimų", "5 metų garantija"])) == []


def test_a_single_arm_with_two_attributes_and_a_design_with_no_combined_arm_warn():
    assert _codes(_arms(None)) == ["combo_no_combined"]
    checks = _checks(_arms(["120 Google atsiliepimų", "5 metų garantija", "montavimas per 7 d."], single_two=True))
    assert [(c.code, c.ad, c.n) for c in checks] == [("combo_single_mixed", 2, 2)]


# --- #251 creative roles --------------------------------------------------------------------------------------------


def test_brand_and_direct_cells_need_a_combined_cell_and_a_lagged_measure():
    pack = _pack(_ad(test_role="control", creative_role="brand"), _ad(test_role="challenger", creative_role="direct"))
    assert _codes(pack) == ["combo_roles_untested_together", "combo_no_lagged_measure"]
    pack["ads"].append(_ad(test_role="challenger", creative_role="combined"))
    pack["test_plan"] = {"immediate_kpi": "orders", "immediate_window_days": 7, "lagged_kpi": "branded search", "lagged_window_days": 56}
    assert _codes(pack) == []


def test_ads_without_creative_roles_ask_for_no_test_plan():
    assert _codes(_pack(_ad(test_role="control"), _ad(test_role="challenger"))) == []


# --- #217 headline selector -----------------------------------------------------------------------------------------


def test_the_selector_must_be_in_the_headline_in_some_inflection():
    checks = _checks(_pack(_ad(headline="Buhalterija smulkiam verslui", selector="kepyklėlėms", qualifier="turi kasos aparatą")))
    assert [(c.code, c.field, c.value) for c in checks] == [("headline_generic", "headline", "kepyklėlėms")]
    assert "kepyklėlėms" in message(checks[0], "lt")
    assert _codes(_pack(_ad(headline="Buhalterija kepyklėlių savininkams", selector="kepyklėlėms", qualifier="turi kasos aparatą"))) == []


def test_a_selector_without_a_qualifier_is_half_the_concept():
    assert _codes(_pack(_ad(headline="Buhalterija kepyklėlėms", selector="kepyklėlėms"))) == ["headline_no_qualifier"]
    assert _codes(_pack(_ad(headline="Buhalterija nuo A iki Z"))) == []


# --- #219 headline test ---------------------------------------------------------------------------------------------


def _headline_test(third_text, benefits=("laikas", "pajūris", "be pastangų")):
    base = "Nuomokis el. dviratį Palangoje."
    ads = [
        _ad(test_role="control", primary_text=base, headline="Sutaupyk laiko", benefit=benefits[0]),
        _ad(test_role="challenger", primary_text=base, headline="Pamatyk daugiau pajūrio", benefit=benefits[1]),
        _ad(test_role="challenger", primary_text=third_text, headline="Be prakaito iki Šventosios", benefit=benefits[2]),
    ]
    return _pack(*ads, test_design={"kind": "headline"})


def test_in_a_headline_test_the_body_must_match_the_control():
    checks = _checks(_headline_test("Nuomokis el. dviratį Palangoje. Dar sakinys."))
    assert [(c.level, c.code, c.ad, c.field) for c in checks] == [("block", "headline_test_confound", 3, "primary_text")]
    assert _codes(_headline_test("Nuomokis el. dviratį Palangoje.")) == []


def test_benefits_must_be_named_and_distinct():
    assert _codes(_headline_test("Nuomokis el. dviratį Palangoje.", benefits=("laikas", "Laikas", ""))) == ["headline_benefit_duplicate", "headline_benefit_missing"]
    full = _pack(_ad(test_role="control", benefit="laikas"), _ad(test_role="challenger", benefit="laikas"), test_design={"kind": "full_ad"})
    assert _codes(full) == ["headline_benefit_duplicate"]


# --- #220 process evidence ------------------------------------------------------------------------------------------


def test_exclusivity_over_a_common_step_blocks_and_an_unchecked_one_asks():
    ad = _ad(headline="Vienintelis tikrai natūralus aliejus")
    cue = {"attribute": "riešutų kvapas"}
    evidence = [{"claim": "natūralus", "process_step": "spaudžiama šaltai iki 40 °C", "common_in_category": True, "source": SOURCE}]
    checks = _checks(_pack(ad, evaluation_cue=cue, quality_evidence=evidence))
    assert [(c.level, c.code, c.value) for c in checks] == [("block", "proof_false_exclusivity", "Vienintelis")]
    unchecked = _checks(_pack(ad, evaluation_cue=cue, quality_evidence=[{**evidence[0], "common_in_category": None}]))
    assert [c.code for c in unchecked] == ["proof_exclusivity_unchecked"]
    assert _codes(_pack(ad, evaluation_cue=cue, quality_evidence=[{**evidence[0], "common_in_category": False}])) == []


def test_a_quality_word_needs_a_sourced_process_step():
    ad = _ad(headline="Aukščiausios kokybės aliejus")
    assert sorted(_codes(_pack(ad))) == ["eval_cue_missing", "proof_quality_unproven"]
    cue = {"attribute": "riešutų kvapas"}
    assert _codes(_pack(ad, evaluation_cue=cue, quality_evidence=[{"claim": "kokybė", "process_step": "šaltas spaudimas", "source": "https://other.example"}])) == ["proof_unsourced"]
    assert _codes(_pack(ad, evaluation_cue=cue, quality_evidence=[{"claim": "kokybė", "process_step": "šaltas spaudimas", "source": SOURCE}])) == []


# --- #221 self-contained --------------------------------------------------------------------------------------------


def test_an_ad_that_refers_to_another_ad_blocks():
    checks = _checks(_pack(_ad(primary_text="Kaip minėjome ankstesnėje reklamoje, pirmas mėnuo nemokamas.")))
    assert [(c.level, c.code, c.field, c.value) for c in checks] == [("block", "copy_not_self_contained", "primary_text", "kaip minėjome")]
    assert "pati viena" in message(checks[0], "lt")


def test_an_ad_that_stands_alone_passes():
    assert _codes(_pack(_ad(primary_text="Brand: sąskaitos per kelias minutes. Pradėk."))) == []


# --- #223 magnified visual ------------------------------------------------------------------------------------------


def test_a_magnified_visual_needs_its_scale_in_the_copy():
    assert _codes(_pack(_ad(visual={"magnified": True, "scale": ""}))) == ["copy_scale_missing"]
    scale = "rodoma 10× didesnė nei tikra"
    assert _codes(_pack(_ad(visual={"magnified": True, "scale": scale}))) == ["copy_scale_not_stated"]
    assert _codes(_pack(_ad(description=f"Tinklelis: {scale}.", visual={"magnified": True, "scale": scale}))) == []


def test_an_unmagnified_visual_runs_no_scale_check():
    assert _codes(_pack(_ad(visual={"magnified": False, "scale": ""}))) == []


# --- #226 series ----------------------------------------------------------------------------------------------------


def test_a_verbal_series_cue_must_be_in_every_ad():
    ads = [_ad(test_role="control", headline="Rudens ritualas: latte"), _ad(test_role="challenger", headline="Karšta kava"), _ad(test_role="challenger", primary_text="Rudens ritualas tęsiasi.")]
    checks = _checks(_pack(*ads, series={"cue": "Rudens ritualas", "kind": "verbal"}))
    assert [(c.code, c.ad) for c in checks] == [("copy_series_break", 2)]
    assert _codes(_pack(*ads, series={"cue": "", "kind": "verbal"})) == ["copy_series_missing"]


def test_a_visual_series_is_judgment_not_code():
    ads = [_ad(test_role="control"), _ad(test_role="challenger", headline="Kita")]
    assert _codes(_pack(*ads, series={"cue": "oranžinis blokas", "kind": "visual"})) == []


# --- #228 test metrics ----------------------------------------------------------------------------------------------


def test_an_engagement_primary_metric_warns_and_a_humorous_ad_needs_recall():
    assert _codes(_pack(test_metrics={"primary_metric": "shares", "secondary_metric": "recall"})) == ["copy_engagement_primary"]
    assert _codes(_pack(_ad(angle="humoras apie i.SAF", insider={"reference": "i.SAF registrai iki 20 d.", "source": SOURCE}))) == ["copy_recall_metric_missing"]


def test_a_buying_metric_with_recall_beside_it_passes():
    pack = _pack(_ad(angle="humor", insider={"reference": "i.SAF registrai iki 20 d.", "source": SOURCE}), test_metrics={"primary_metric": "purchase", "secondary_metric": "unaided_recall"})
    assert _codes(pack) == []


# --- #235 realistic copy --------------------------------------------------------------------------------------------


def test_placeholder_text_is_caught_without_any_field():
    checks = _checks(_pack(_ad(primary_text="Lorem ipsum dolor", headline="[Product] for Client A")))
    assert [(c.code, c.field, c.value) for c in checks] == [("copy_placeholder", "primary_text", "Lorem"), ("copy_placeholder", "headline", "[Product]")]
    assert _codes(_pack(_ad(headline="x" * 52))) == []


def test_the_longest_real_product_name_stresses_the_headline():
    plan = "Invohub Verslui Plius – 24 mėn. sutartis"
    checks = _checks(_pack(_ad(headline="Sąskaitos su planu"), longest_strings={"product": plan}))
    assert [(c.code, c.n, c.limit) for c in checks] == [("copy_long_real_name", 59, 40)]
    assert "59" in message(checks[0]) and "40" in message(checks[0], "lt")
    assert _codes(_pack(_ad(headline="Planas"), longest_strings={"product": "Pro"})) == []


# --- #243 inbox preview ---------------------------------------------------------------------------------------------


def test_the_inbox_triple_is_cut_repeated_and_boilerplated():
    inbox = {
        "sender_name": "Pigu.lt naujienlaiškis",
        "subject": "Pigu.lt naujienlaiškis: Black Friday pasiūlymai Pigu.lt klientams – iki -40 %",
        "preview_text": "Peržiūrėkite šį laišką naršyklėje. Pigu.lt naujienlaiškis",
        "must_show": "-40 %",
    }
    checks = _checks(_pack(inbox=inbox))
    by_code = {}
    for c in checks:
        by_code.setdefault(c.code, []).append(c)
    assert by_code["copy_inbox_cut"][0].field == "subject" and by_code["copy_inbox_cut"][0].n == 77 and by_code["copy_inbox_cut"][0].limit == 40
    assert {c.value for c in by_code["copy_inbox_repeat"]} == {"pigu.lt", "naujienlaiskis"}
    assert by_code["copy_inbox_boilerplate"][0].value == "peržiūrėkite šį laišką naršyklėje"
    assert by_code["copy_inbox_detail_hidden"][0].value == "-40 %"


def test_a_rewritten_inbox_triple_passes():
    inbox = {
        "sender_name": "Pigu.lt",
        "subject": "Black Friday: iki -40 % partneriams",
        "preview_text": "Elektronika, buitis ir žaislai – kainos krenta nuo penktadienio 7 val.",
        "must_show": "-40 %",
    }
    assert _codes(_pack(inbox=inbox)) == []


# --- #247 continuity ------------------------------------------------------------------------------------------------


def test_a_character_must_appear_and_nothing_may_be_called_new_without_news():
    ads = [
        _ad(test_role="control", primary_text="Kepėja Rūta vėl kepa anksti ryte.", angle="continuity"),
        _ad(test_role="challenger", primary_text="Naujas ruginis kepalas."),
    ]
    checks = _checks(_pack(*ads, continuity={"character": "Kepėja Rūta", "story_so_far": "pavasarį kepė", "product_news": False}))
    assert [(c.level, c.code, c.ad) for c in checks] == [("warn", "copy_continuity_broken", 2), ("block", "copy_fake_news", 2)]
    assert checks[1].value == "Naujas"


def test_a_continued_story_passes_and_one_without_an_episode_is_asked_for():
    ads = [_ad(test_role="control", primary_text="Rūtos kepalas.", angle="continuity"), _ad(test_role="challenger", headline="Rūta kviečia")]
    assert _codes(_pack(*ads, continuity={"character": "Rūta", "product_news": False})) == []
    plain = [_ad(test_role="control", primary_text="Rūtos kepalas."), _ad(test_role="challenger", headline="Rūta kviečia")]
    assert _codes(_pack(*plain, continuity={"character": "Rūta", "product_news": True})) == ["copy_no_episode"]


# --- #253 occupational humour ---------------------------------------------------------------------------------------


def test_an_in_joke_needs_a_sourced_job_detail():
    assert _codes(_pack(_ad(angle="humor"), test_metrics={"secondary_metric": "recall"})) == ["copy_insider_unsourced"]
    short = _ad(insider={"reference": "i.SAF", "source": SOURCE})
    assert _codes(_pack(short)) == ["copy_insider_unsourced"]
    assert _codes(_pack(_ad(insider={"reference": "i.SAF registrai iki 20 d.", "source": "https://other.example"}))) == ["copy_insider_unsourced"]


def test_a_sourced_in_joke_passes():
    assert _codes(_pack(_ad(angle="humor", insider={"reference": "i.SAF registrai iki 20 d.", "source": SOURCE}), test_metrics={"secondary_metric": "recall"})) == []


# --- wiring ---------------------------------------------------------------------------------------------------------


def test_every_code_has_an_element_record_in_both_languages():
    for code, template in offer.MESSAGES["en"].items():
        element = playbook.for_code(code)
        assert element is not None, code
        assert element.question["en"] and element.question["lt"] and element.why["en"] and element.why["lt"], code
        assert element.enforced == "check" and "ads" in element.applies
        assert code in offer.MESSAGES["lt"], code
        assert "{" not in template.format(field="f", Field="F", value="v", n=1, limit=2, code=code)
    assert set(offer.MESSAGES["lt"]) == set(offer.MESSAGES["en"])


def test_the_elements_file_covers_exactly_its_concepts():
    numbers = {playbook.for_code(code).number for code in offer.MESSAGES["en"]}
    assert numbers == {124, 125, 139, 142, 143, 146, 173, 174, 189, 200, 205, 207, 217, 219, 220, 221, 223, 226, 228, 235, 243, 247, 251, 253}


def test_worker_tools_advertise_the_offer_fields():
    spec = next(t for t in worker_tools(False) if t["function"]["name"] == "ad_board")
    props = spec["function"]["parameters"]["properties"]
    for name in offer.PACK_FIELDS:
        assert name in props and props[name].get("description"), name
    ad_props = props["ads"]["items"]["properties"]
    for name in offer.AD_FIELDS:
        assert name in ad_props and ad_props[name].get("description"), name


def test_check_pack_runs_the_module_and_the_page_messages_render():
    checks = check_pack(normalize(_pack(_ad(headline="Liko tik 6 kambariai"))), FACTS, {1: "img"})
    scarcity = [c for c in checks if c.code == "scarcity_unsourced"]
    assert len(scarcity) == 1 and scarcity[0].level == "block"
    assert "scarcity cue" in message(scarcity[0]) and "stygiaus" in message(scarcity[0], "lt")


@pytest.mark.parametrize("field", offer.PACK_FIELDS)
def test_a_pack_field_sent_as_garbage_never_crashes_the_board(field):
    pack = _pack(**{field: "not json at all"})
    failed = [c.value for c in check_pack(normalize(pack), FACTS, {1: "img"}) if c.code == "check_failed"]
    assert not [v for v in failed if v.startswith("offer:")], failed
