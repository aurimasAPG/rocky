import json

import pytest

from rocky.research import outcome_scores, outcome_scores_tool, research_plan, research_plan_tool, survey_check, survey_check_tool
from rocky.tools import ToolError


def _codes(out, where=None):
    return sorted(f["code"] for f in out["findings"] if where is None or f["where"] == where or str(f["where"]).startswith(where + "."))


def _q(qid, text, qtype="single", **extra):
    q = {"id": qid, "text": text, "type": qtype}
    if qtype in {"single", "multi"} and "options" not in extra:
        q["options"] = ["Taip", "Ne", "Nežinau"]
    q.update(extra)
    return q


CLEAN_LT = [
    _q("q1", "Ar per pastaruosius 3 mėnesius pirkote kavos pupelių internetu?", options=["Taip", "Ne", "Neprisimenu"]),
    _q("q2", "Kiek kartų per pastaruosius 3 mėnesius pirkote kavos pupelių internetu?", "number", ask_if="q1 = Taip", requires_experience=True),
    _q("q3", "Kuriose parduotuvėse pirkote kavos per pastaruosius 3 mėnesius?", "multi", options=["Caffeine", "Huracan", "Kita", "Neprisimenu"], ask_if="q1 = Taip", requires_experience=True, main_choice_followup="q4"),
    _q("q4", "Kuri iš jų pagrindinė?", options=["Caffeine", "Huracan", "Kita"], ask_if="q3 in [Caffeine, Huracan]", exclusive=True),
    _q("q5", "Kodėl paskutinį kartą per pastaruosius 3 mėnesius pasirinkote tą parduotuvę?", "open", ask_if="q1 = Taip", requires_experience=True, corroborated_by="q2"),
]


# --- survey_check: each code on a crafted question, silent on a clean one -------------


def test_clean_lithuanian_survey_has_only_info_findings_and_paths():
    out = survey_check({"questions": CLEAN_LT})
    assert out["language"] == "lt"
    assert out["clean"] is True
    assert _codes(out) == ["motive_question"]
    assert out["counts"]["by_level"] == {"block": 0, "warn": 0, "info": 1}
    assert out["paths"][0] == {"condition": "all respondents", "question_ids": ["q1"], "count": 1}
    assert {"condition": "q1 = Taip", "question_ids": ["q2", "q3", "q5"], "count": 3} in out["paths"]
    assert {"condition": "q3 in [Caffeine, Huracan]", "question_ids": ["q4"], "count": 1} in out["paths"]
    assert out["estimated_minutes"] == 1.75 and out["pilot_plan"]["n"] == 5
    assert "no blocking or warning findings" in out["summary"]


def test_double_barrelled_fires_on_two_clauses_or_two_question_marks_in_both_languages():
    lt = survey_check({"questions": [_q("a", "Ar pirkote kavos per pastaruosius 30 dienų ir ar ji jums patiko?", options=["Taip", "Ne", "Nežinau"], corroborated_by="a")]})
    assert "double_barrelled" in _codes(lt, "a")
    en = survey_check({"questions": [_q("b", "How did you hear about us? What did you buy?", "open")]})
    assert "double_barrelled" in _codes(en, "b")
    # an item name in quotes containing 'ir' is one thing, not two clauses
    quoted = survey_check({"questions": [_q("c", "Kiek kartų per pastaruosius 3 mėnesius pirkote „duoną ir pieną“ internetu?", "number")]})
    assert "double_barrelled" not in _codes(quoted)


def test_leading_fires_on_praise_words_and_agreement_prompts():
    out = survey_check({"questions": [
        _q("a", "Ar sutinkate, kad mūsų puikus aptarnavimas yra geriausias?", corroborated_by="a"),
        _q("b", "Don't you agree the new menu is great?", options=["Yes", "No", "Don't know"]),
        _q("c", "Kaip vertinate aptarnavimą paskutinio apsilankymo metu per pastarąjį mėnesį?", "scale", scale={"min": 1, "max": 5, "labels": ["Labai blogai", "Labai gerai"]}, corroborated_by="a"),
    ]})
    assert "leading" in _codes(out, "a") and "leading" in _codes(out, "b")
    assert "leading" not in _codes(out, "c")
    leading = [f for f in out["findings"] if f["code"] == "leading"]
    assert all(f["level"] == "block" for f in leading)


def test_vague_quantifier_fires_without_a_number_and_not_with_anchored_options():
    vague = survey_check({"questions": [_q("a", "Kaip dažnai perkate kavą?", options=["Dažnai", "Kartais", "Retai", "Nežinau"])]})
    assert _codes(vague, "a").count("vague_quantifier") == 3
    anchored = survey_check({"questions": [_q("a", "Kaip dažnai perkate kavą?", options=["Kasdien", "1–2 kartus per savaitę", "Rečiau nei kartą per mėnesį", "Nežinau"])]})
    assert "vague_quantifier" not in _codes(anchored)
    text_only = survey_check({"questions": [_q("b", "Do you often buy coffee online?", options=["Yes", "No", "Not sure"])]})
    assert "vague_quantifier" in _codes(text_only, "b")


def test_no_escape_option_fires_on_closed_lists_without_other_or_dont_know():
    out = survey_check({"questions": [
        _q("a", "Which supermarket did you visit in the last 7 days?", options=["Maxima", "Iki", "Rimi"], exclusive=True),
        _q("b", "Which supermarket did you visit in the last 7 days?", options=["Maxima", "Iki", "Rimi", "Other", "Don't know"], exclusive=True),
        _q("c", "Kurį prekės ženklą pirkote per pastarąjį mėnesį?", options=["A", "B"], exclusive=True),
    ]})
    assert "no_escape_option" in _codes(out, "a") and "no_escape_option" in _codes(out, "c")
    assert "no_escape_option" not in _codes(out, "b")


def test_scale_mismatch_fires_on_label_count_and_on_agreement_wording_with_frequency_labels():
    out = survey_check({"questions": [
        _q("a", "Kiek sutinkate su teiginiu: pristatymas buvo laiku?", "scale", scale={"min": 1, "max": 5, "labels": ["Niekada", "Kartais", "Visada"]}, corroborated_by="a"),
        _q("b", "Kiek sutinkate su teiginiu: pristatymas buvo laiku?", "scale", scale={"min": 1, "max": 5, "labels": ["Visiškai nesutinku", "Visiškai sutinku"]}, corroborated_by="a"),
        _q("c", "How satisfied were you with delivery in the last 30 days?", "scale"),
    ]})
    a = [f for f in out["findings"] if f["code"] == "scale_mismatch" and f["where"] == "a.scale"]
    assert len(a) == 2 and all(f["level"] == "block" for f in a)
    assert "scale_mismatch" not in _codes(out, "b")
    assert "scale_mismatch" in _codes(out, "c")  # no scale given at all


def test_jargon_uses_the_default_list_and_a_configurable_one():
    default = survey_check({"questions": [_q("a", "How important is the ROI of the funnel to you?", "scale", scale={"min": 1, "max": 5}, corroborated_by="a")]})
    hit = next(f for f in default["findings"] if f["code"] == "jargon")
    assert hit["value"] == ["ROI", "funnel"] and hit["level"] == "warn"
    custom = survey_check({"questions": [_q("a", "Ar naudojatės omnikanaliu pristatymu per pastaruosius 3 mėnesius?", options=["Taip", "Ne", "Nežinau"])], "jargon": ["omnikanaliu"]})
    assert "jargon" in _codes(custom, "a")
    none = survey_check({"questions": [_q("a", "How important is the ROI of the funnel to you?", "scale", scale={"min": 1, "max": 5}, corroborated_by="a")], "jargon": []})
    assert "jargon" not in _codes(none)


def test_motive_question_is_info_and_uncorroborated_needs_a_behaviour_question():
    alone = survey_check({"questions": [_q("why", "Kodėl renkatės šią parduotuvę?", "open")]})
    assert {"motive_question", "uncorroborated"} <= set(_codes(alone, "why"))
    assert next(f["level"] for f in alone["findings"] if f["code"] == "motive_question") == "info"
    assert next(f["level"] for f in alone["findings"] if f["code"] == "uncorroborated") == "warn"
    paired = survey_check({"questions": [
        _q("n", "How many times did you shop there in the last 30 days?", "number"),
        _q("why", "Why do you choose this shop?", "open", corroborated_by="n"),
    ]})
    assert "uncorroborated" not in _codes(paired)
    wrong = survey_check({"questions": [
        _q("att", "How important is price to you?", "scale", scale={"min": 1, "max": 5}),
        _q("why", "Why do you choose this shop?", "open", corroborated_by="att"),
    ]})
    assert "uncorroborated" in _codes(wrong, "why") and "uncorroborated" in _codes(wrong, "att")


def test_recall_event_fires_without_a_time_frame_in_both_languages():
    out = survey_check({"questions": [
        _q("a", "Ar pirkote kavos pupelių internetu?", options=["Taip", "Ne", "Neprisimenu"]),
        _q("b", "Did you visit a Maxima store?", options=["Yes", "No", "Don't know"]),
        _q("c", "Ar per pastaruosius 30 dienų pirkote kavos pupelių internetu?", options=["Taip", "Ne", "Neprisimenu"]),
        _q("d", "Did you visit a Maxima store in the last 2 weeks?", options=["Yes", "No", "Don't know"]),
    ]})
    assert "recall_event" in _codes(out, "a") and "recall_event" in _codes(out, "b")
    assert "recall_event" not in _codes(out, "c") and "recall_event" not in _codes(out, "d")


def test_response_field_bias_codes_single_where_multi_open_field_short_multi_no_primary():
    out = survey_check({"questions": [
        _q("a", "Kurias kavines lankote per pastarąjį mėnesį?", options=["Caffeine", "Huracan", "Kita"]),
        _q("b", "Which streaming services do you use?", "open", max_len=40),
        _q("c", "Which streaming services did you use in the last 30 days?", "multi", options=["Netflix", "Go3", "Other"]),
        _q("d", "Which streaming services did you use in the last 30 days?", "multi", options=["Netflix", "Go3", "Other"], main_choice_followup="e"),
        _q("e", "Which one is your main service?", options=["Netflix", "Go3", "Other"], exclusive=True),
        _q("f", "Kokią kavą pirkote paskutinį kartą per pastarąją savaitę?", options=["Maltą", "Pupeles", "Kita"], exclusive=True),
    ]})
    assert "single_where_multi" in _codes(out, "a")
    assert "open_field_short" in _codes(out, "b")
    assert "multi_no_primary" in _codes(out, "c")
    assert "multi_no_primary" not in _codes(out, "d")
    assert "single_where_multi" not in _codes(out, "e") and "single_where_multi" not in _codes(out, "f")


def test_unrouted_experience_question_blocks_and_routing_from_the_screener_clears_it():
    bare = survey_check({"questions": [_q("x", "Kiek sumokėjote už paskutinį užsakymą per pastarąjį mėnesį?", "number", requires_experience=True)]})
    assert "unrouted_experience_question" in _codes(bare, "x")
    assert next(f["level"] for f in bare["findings"] if f["code"] == "unrouted_experience_question") == "block"
    routed = survey_check({
        "screener": {"question": "Ar per pastaruosius 3 mėnesius užsisakėte maisto į namus?", "options": ["Taip", "Ne"]},
        "questions": [_q("x", "Kiek sumokėjote už paskutinį užsakymą per pastarąjį mėnesį?", "number", requires_experience=True, ask_if="screener = Taip")],
    })
    assert "unrouted_experience_question" not in _codes(routed)
    assert routed["paths"] == [
        {"condition": "screener = Taip", "question_ids": ["x"], "count": 1},
        {"condition": "screener = Ne", "question_ids": [], "count": 0},
    ]
    assert "no_questions_for_group" in _codes(routed, "screener")


def test_dead_end_names_unknown_ids_unknown_options_and_forward_references():
    out = survey_check({"questions": [
        _q("q1", "Ar per pastaruosius 3 mėnesius pirkote kavos?", options=["Taip", "Ne", "Nežinau"]),
        _q("q2", "Kiek kartų per pastaruosius 3 mėnesius pirkote kavos?", "number", ask_if="q9 = Taip"),
        _q("q3", "Kiek kartų per pastaruosius 3 mėnesius pirkote kavos?", "number", ask_if="q1 = Gal"),
        _q("q4", "Kiek kartų per pastaruosius 3 mėnesius pirkote kavos?", "number", ask_if="q5 = Taip"),
        _q("q5", "Ar per pastaruosius 3 mėnesius pirkote arbatos?", options=["Taip", "Ne", "Nežinau"]),
    ]})
    dead = {f["where"]: f["note"] for f in out["findings"] if f["code"] == "dead_end"}
    assert set(dead) == {"q2", "q3", "q4"}
    assert "'q9'" in dead["q2"] and "['Gal']" in dead["q3"] and "later in the survey" in dead["q4"]
    assert all(f["level"] == "block" for f in out["findings"] if f["code"] == "dead_end")
    assert out["clean"] is False


def test_awareness_survey_must_carry_the_four_familiarity_blocks():
    partial = survey_check({"questions": [
        _q("u", "Kokie kavos prekių ženklai jums pirmiausia ateina į galvą?", "open"),
        _q("a", "Kuriuos iš šių kavos prekių ženklų žinote bent iš pavadinimo?", "multi", options=["A", "B", "C", "Nė vieno"], main_choice_followup="u"),
    ], "audience": "žinomumo tyrimas"})
    missing = next(f for f in partial["findings"] if f["code"] == "familiarity_blocks_missing")
    assert missing["level"] == "block" and missing["value"] == ["ad_recall", "source_of_first_contact"]
    assert partial["awareness_blocks"] == {"unaided_recall": True, "aided_familiarity": True, "ad_recall": False, "source_of_first_contact": False}
    full = survey_check({"questions": [
        _q("u", "Which coffee brands first come to mind?", "open"),
        _q("a", "Which of these coffee brands have you heard of?", "multi", options=["A", "B", "C", "None of them"], main_choice_followup="u"),
        _q("r", "Which of these brands have you seen advertised in the last 30 days?", "multi", options=["A", "B", "C", "None of them"], main_choice_followup="u"),
        _q("s", "Where did you first hear about brand A?", options=["TV", "A friend", "In a shop", "Online", "Don't know", "Other"], exclusive=True),
    ]})
    assert "familiarity_blocks_missing" not in _codes(full)
    plain = survey_check({"questions": [_q("p", "Kiek kartų per pastarąjį mėnesį pirkote kavos?", "number")]})
    assert plain["awareness_blocks"] is None


def test_blind_protocol_flags_fixed_order_and_a_missing_blinded_arm():
    fixed = survey_check({"questions": [], "protocol": {
        "arms": [{"label": "blind", "blinded": True, "samples": ["A", "B"], "order": "fixed"}, {"label": "branded", "blinded": False, "samples": ["A", "B"], "order": "counterbalanced"}],
        "n_per_arm": 40, "metric": "liking 1–9",
    }})
    assert _codes(fixed) == ["order_not_randomised"]
    assert fixed["findings"][0]["where"] == "protocol.arms[0]" and fixed["findings"][0]["level"] == "block"
    assert fixed["protocol"]["blind_arm_present"] is True and fixed["protocol"]["n_total"] == 80
    assert "branded_score − blind_score" in fixed["protocol"]["context_effect_rule"]
    unblinded = survey_check({"questions": [], "protocol": {
        "arms": [{"label": "ours", "blinded": False, "samples": ["A"], "order": "randomised"}, {"label": "theirs", "blinded": False, "samples": ["A", "B"], "order": "randomised"}],
        "metric": "liking",
    }})
    assert _codes(unblinded) == ["arm_not_blinded", "unequal_samples"]
    assert unblinded["protocol"]["context_effect_rule"] is None and unblinded["missing"] == ["protocol.n_per_arm"]


def test_survey_check_errors_in_plain_words():
    with pytest.raises(ToolError, match="questions is empty"):
        survey_check({"questions": []})
    with pytest.raises(ToolError, match="unknown type 'rating'"):
        survey_check({"questions": [{"id": "q1", "text": "Ar?", "type": "rating"}]})
    with pytest.raises(ToolError, match="used twice"):
        survey_check({"questions": [_q("q1", "Ar taip?"), _q("q1", "Ar ne?")]})
    with pytest.raises(ToolError, match="scale.min must be below scale.max"):
        survey_check({"questions": [_q("q1", "Kiek?", "scale", scale={"min": 5, "max": 1})]})
    with pytest.raises(ToolError, match="language must be lt or en"):
        survey_check({"questions": CLEAN_LT, "language": "de"})


# --- outcome_scores ------------------------------------------------------------------


def test_outcome_statement_with_generic_words_or_a_solution_is_rejected_and_a_full_one_passes():
    out = outcome_scores({"outcomes": [
        {"direction": "minimize", "metric": "time", "object": "iš naujo prijungti čekių spausdintuvą", "situation": "dingus Wi-Fi"},
        {"direction": "increase", "metric": "other", "object": "geresnis patikimas"},
        {"direction": "increase", "metric": "likelihood", "object": "that the app feature works", "situation": "on first login"},
        {"metric": "time", "object": "find the invoice", "situation": "at month end"},
    ]})
    assert _codes(out, "outcomes[0]") == []
    assert out["outcomes"][0]["statement"] == "sumažinti laiką, per kurį iš naujo prijungti čekių spausdintuvą, dingus Wi-Fi"
    vague = next(f for f in out["findings"] if f["code"] == "outcome_vague")
    assert vague["where"] == "outcomes[1].object" and vague["level"] == "block" and vague["value"] == ["geresnis", "patikimas"]
    assert "part_missing" in _codes(out, "outcomes[1].situation")
    assert "solution_not_outcome" in _codes(out, "outcomes[2].object")
    direction = next(f for f in out["findings"] if f["where"] == "outcomes[3].direction")
    assert direction["code"] == "part_missing" and direction["level"] == "block"
    assert out["top"] == [] and len(out["missing"]) == 8


def test_opportunity_score_arithmetic_and_classes():
    out = outcome_scores({"outcomes": [
        {"direction": "minimize", "metric": "time", "object": "re-pair the receipt printer", "situation": "after a Wi-Fi drop", "importance": 9, "satisfaction": 4, "n": 120},
        {"direction": "increase", "metric": "likelihood", "object": "the box fits the letterbox", "situation": "when nobody is home", "importance": 6, "satisfaction": 8, "cost_per_unit": 1.5},
        {"direction": "minimize", "metric": "number", "object": "errors on the invoice", "situation": "at month end", "importance": 7, "satisfaction": 6},
    ]})
    rows = out["outcomes"]
    assert rows[0]["opportunity"] == 14.0 and rows[0]["class"] == "underserved"
    assert rows[1]["opportunity"] == 6.0 and rows[1]["class"] == "overserved"
    assert rows[2]["opportunity"] == 8.0 and rows[2]["class"] == "appropriately served"
    assert [t["index"] for t in out["top"]] == [0, 2, 1]
    assert out["excess"][0]["index"] == 1 and out["excess"][0]["cost_per_unit"] == 1.5 and "saving" in out["excess"][0]["note"]
    assert out["classes"] == {"underserved": 1, "appropriately served": 1, "overserved": 1}
    assert out["threshold_underserved"] == 12.0 and out["findings"] == [] and out["missing"] == []
    assert "1 underserved (≥ 12)" in out["summary"] and "top: 're-pair the receipt printer' at 14" in out["summary"]


def test_threshold_scales_with_scale_max_and_says_so():
    out = outcome_scores({"outcomes": [{"direction": "minimize", "metric": "time", "object": "wait for a table", "situation": "on a Friday night", "importance": 4.5, "satisfaction": 2}], "scale_max": 5})
    row = out["outcomes"][0]
    assert out["threshold_underserved"] == 6.0 and row["opportunity"] == 7.0 and row["opportunity_10"] == 14.0 and row["class"] == "underserved"
    assert any("scaled to 6" in w for w in out["warnings"])


def test_per_brand_satisfaction_gives_gap_and_comparable_only_with_thirty_per_brand():
    base = {"direction": "minimize", "metric": "number", "object": "klaidų sąskaitoje", "situation": "mėnesio pabaigoje", "importance": 8}
    out = outcome_scores({"focus_brand": "Mes", "outcomes": [
        {**base, "satisfaction": {"Mes": 5, "Konkurentas": 7, "Kitas": None}, "n": {"Mes": 60, "Konkurentas": 35}},
        {**base, "satisfaction": {"Mes": 5, "Konkurentas": 7}, "n": {"Mes": 60, "Konkurentas": 12}},
        {**base, "satisfaction": {"Mes": 5, "Konkurentas": 7}},
        {**base, "satisfaction": {"Mes": 5, "Konkurentas": None}},
    ]})
    r0, r1, r2, r3 = out["outcomes"]
    assert r0["brand"] == "Mes" and r0["satisfaction"] == 5 and r0["opportunity"] == 11.0
    assert r0["best_competitor"] == "Konkurentas" and r0["gap_vs_best_competitor"] == -2.0 and r0["comparable"] is True
    assert r1["comparable"] is False and "under 30" in r1["comparable_note"]
    assert r2["comparable"] is False and "not given" in r2["comparable_note"]
    assert r3["gap_vs_best_competitor"] is None and r3["comparable"] is False and "nothing is estimated" in r3["comparable_note"]
    assert any("n = 12" in w for w in out["warnings"])
    assert "outcome_duplicate" in _codes(out, "outcomes[1]")


def test_outcome_scores_errors_in_plain_words():
    good = {"direction": "minimize", "metric": "time", "object": "find the receipt", "situation": "at the counter"}
    with pytest.raises(ToolError, match="outcomes is empty"):
        outcome_scores({"outcomes": []})
    with pytest.raises(ToolError, match="scale_max must be greater than zero"):
        outcome_scores({"outcomes": [good], "scale_max": 0})
    with pytest.raises(ToolError, match="satisfaction is 11, above the scale maximum 10"):
        outcome_scores({"outcomes": [{**good, "importance": 5, "satisfaction": 11}]})
    with pytest.raises(ToolError, match="satisfaction\\[Mes\\] is 7, above the scale maximum 5"):
        outcome_scores({"outcomes": [{**good, "importance": 5, "satisfaction": {"Mes": 7}}], "scale_max": 5})
    with pytest.raises(ToolError, match="direction must be minimize or increase"):
        outcome_scores({"outcomes": [{**good, "direction": "reduce"}]})
    with pytest.raises(ToolError, match="metric must be one of"):
        outcome_scores({"outcomes": [{**good, "metric": "speed"}]})
    with pytest.raises(ToolError, match="mode must be outcomes or job_map"):
        outcome_scores({"outcomes": [good], "mode": "persona"})


def test_job_map_reports_missing_phases_order_gaps_and_unsourced_steps():
    out = outcome_scores({"job": "išrašyti sąskaitą", "executor": "smulkaus verslo savininkas", "steps": [
        {"phase": "define", "title": "nuspręsti, ką įtraukti", "outcomes": [{"direction": "minimize", "metric": "time", "object": "surinkti pozicijas", "situation": "mėnesio pabaigoje"}], "today": "ok", "evidence_url": "https://example.test/i1"},
        {"phase": "execute", "title": "įvesti duomenis", "outcomes": [{"direction": "minimize", "metric": "number", "object": "kartų, kai perrašomi rekvizitai", "situation": "įvedant"}], "today": "repeated"},
        {"phase": "locate", "title": "rasti kliento rekvizitus", "outcomes": [], "today": "unknown"},
    ]})
    assert out["mode"] == "job_map" and out["phases_covered"] == "3/8"
    assert out["missing_phases"] == ["prepare", "confirm", "monitor", "modify", "conclude"]
    assert out["gaps"] == ["įvesti duomenis"] and out["no_gap_found"] is False
    codes = _codes(out)
    assert {"phase_missing", "phase_order", "step_repeated", "step_unsourced", "step_no_outcome"} <= set(codes)
    assert next(f["where"] for f in out["findings"] if f["code"] == "step_unsourced") == "steps[1]"
    assert next(f["where"] for f in out["findings"] if f["code"] == "step_no_outcome") == "steps[2]"
    assert "missing prepare, confirm, monitor, modify, conclude" in out["summary"]


def test_job_map_full_coverage_without_gaps_is_marked_a_hypothesis_and_bad_phase_errors():
    steps = [{"phase": p, "title": f"step {p}", "outcomes": [{"direction": "minimize", "metric": "time", "object": f"finish {p}", "situation": "each month"}], "today": "ok", "evidence_url": "https://e"} for p in
             ("define", "locate", "prepare", "confirm", "execute", "monitor", "modify", "conclude")]
    out = outcome_scores({"mode": "job_map", "job": "invoice a client", "executor": "owner", "steps": steps})
    assert out["phases_covered"] == "8/8" and out["missing_phases"] == [] and out["no_gap_found"] is True and out["findings"] == []
    assert "no gap found" in out["summary"]
    with pytest.raises(ToolError, match="phase must be one of"):
        outcome_scores({"job": "x", "executor": "y", "steps": [{"phase": "start", "title": "t"}]})
    with pytest.raises(ToolError, match="steps is empty"):
        outcome_scores({"job": "x", "executor": "y", "steps": []})


# --- research_plan ---------------------------------------------------------------------


def test_concept_test_gives_quota_sample_check_instrument_and_decision_rule():
    out = research_plan({
        "kind": "concept_test",
        "population": {"description": "smulkaus verslo savininkai", "size": 20000, "reach": "LinkedIn"},
        "sample_n": 400, "minimum_effect": 15,
        "concepts": [{"name": "Sąskaitų robotas", "price": 29, "is_lead_user": True}, {"name": "Buhalterio valanda", "price": 40}],
        "follow_up": {"launch_date": "2027-01", "metric": "first-month orders"},
    })
    assert out["language"] == "lt" and out["findings"] == []
    assert out["quota"]["design"] == "monadic" and out["quota"]["per_concept"] == 200 and out["quota"]["remainder"] == 0
    assert out["quota"]["concepts"] == [{"name": "Sąskaitų robotas", "n": 200}, {"name": "Buhalterio valanda", "n": 200}]
    check = out["sample_check"]
    assert check["needed_per_concept"] == 163 and check["sufficient"] is True and check["baseline_is_default"] is True
    assert [q["id"] for q in out["instrument"]] == ["c1_benefit", "c1_use", "c1_intent", "c2_benefit", "c2_use", "c2_intent"]
    assert [q["type"] for q in out["instrument"][:3]] == ["open", "open", "scale"]
    assert out["instrument"][2]["scale"] == {"min": 1, "max": 5, "labels": ["Tikrai nepirkčiau", "Greičiausiai nepirkčiau", "Nežinau", "Greičiausiai pirkčiau", "Tikrai pirkčiau"]}
    assert "29 €" in out["instrument"][2]["text"]
    assert out["instrument_check"]["clean"] is True
    rule = out["decision_rule"]
    assert rule["min_comprehension"] == 0.7 and rule["top_two_box_min"] == 0.4 and rule["defaults_used"] == ["min_comprehension", "top_two_box_min"]
    assert out["sample_note"]["margin_of_error_pp_95"] == 4.9 and out["sample_note"]["finite_population_correction"] is True
    assert out["summary"].startswith("Concept test of 2 concepts, monadic, 200 per concept (enough for a 15 pp difference); instrument passes survey_check")


def test_concept_test_flags_small_samples_lead_user_only_single_concept_and_missing_effect():
    small = research_plan({"kind": "concept_test", "population": {"description": "small business owners", "reach": "LinkedIn"}, "sample_n": 100, "minimum_effect": 15,
                           "concepts": [{"name": "Invoice robot", "price": 29, "is_lead_user": True}, {"name": "Accountant hour", "price": 40, "is_lead_user": True}]})
    assert small["language"] == "en"
    assert {"lead_user_only", "sample_too_small", "no_follow_up", "population_unknown"} == set(_codes(small))
    too_small = next(f for f in small["findings"] if f["code"] == "sample_too_small")
    assert too_small["level"] == "block" and too_small["value"] == {"per_concept": 50, "needed": 163}
    assert small["instrument_check"]["clean"] is True and "€29" in small["instrument"][2]["text"]
    single = research_plan({"kind": "concept_test", "population": {"description": "owners", "size": 500, "reach": "list"}, "sample_n": 60, "concepts": [{"name": "Only one", "price": 10}]})
    assert "single_concept" in _codes(single) and single["sample_check"]["sufficient"] is None
    assert any(m.startswith("minimum_effect") for m in single["missing"])
    assert "sufficiency unknown" in single["summary"]


def test_survey_kind_generates_routed_frequency_questions_that_pass_survey_check():
    out = research_plan({
        "kind": "survey",
        "population": {"description": "maisto rinkinių pirkėjai", "size": 50000, "reach": "el. paštas"},
        "sample_n": 150, "product": "maisto rinkinį",
        "criteria": [{"text": "greitas pristatymas", "source": "focus_group", "n_mentions": 1}, {"text": "ekologiški ingredientai", "source": "reviews", "n_mentions": 40}],
        "screener": {"question": "Ar per pastaruosius 3 mėnesius pirkote maisto rinkinį?", "options": ["Taip", "Ne"]},
    })
    assert out["language"] == "lt"
    ids = [q["id"] for q in out["questions"]]
    assert ids == ["c1", "c2", "open_other"]
    c1 = out["questions"][0]
    assert c1["type"] == "scale" and c1["scale"]["min"] == 1 and c1["scale"]["max"] == 5 and len(c1["scale"]["labels"]) == 5
    assert "paskutinius 5 kartus, kai pirkote maisto rinkinį" in c1["text"] and "„greitas pristatymas“" in c1["text"]
    assert c1["ask_if"] == "screener = Taip" and c1["requires_experience"] is True
    assert out["questions"][2]["type"] == "open" and out["questions"][2]["ask_if"] == "screener = Taip"
    assert out["instrument_check"]["clean"] is True
    assert out["paths"][0] == {"condition": "screener = Taip", "question_ids": ["c1", "c2", "open_other"], "count": 3}
    assert out["quota"] == {"sample_n": 150, "minimum": 100, "sufficient": True, "note": out["quota"]["note"]}
    assert _codes(out) == ["single_voice_criterion"]
    voice = out["findings"][0]
    assert voice["where"] == "criteria[0]" and voice["level"] == "warn" and voice["value"]["n_mentions"] == 1
    assert "instrument passes survey_check" in out["summary"]


def test_survey_kind_in_english_without_a_screener_warns_and_still_passes_the_check():
    out = research_plan({"kind": "survey", "population": {"description": "meal kit buyers", "size": 50000, "reach": "email"}, "sample_n": 80, "product": "a meal kit",
                         "criteria": [{"text": "fast delivery", "source": ["focus_group"]}, {"text": "organic ingredients", "source": "reviews", "n_mentions": 3}]})
    assert out["language"] == "en"
    assert set(_codes(out)) == {"no_screener", "sample_too_small", "single_voice_criterion", "few_mentions"}
    assert out["questions"][0]["ask_if"] is None and out["questions"][0]["text"].startswith("Think of the last 5 times you bought a meal kit.")
    # requires_experience without a screener is caught by survey_check as a block, so the instrument is not clean
    assert out["instrument_check"]["clean"] is False
    assert {f["code"] for f in out["instrument_check"]["findings"]} == {"unrouted_experience_question"}
    assert out["quota"]["sufficient"] is False


def test_research_plan_errors_in_plain_words():
    with pytest.raises(ToolError, match="kind must be concept_test or survey"):
        research_plan({"kind": "panel"})
    with pytest.raises(ToolError, match="criteria is empty"):
        research_plan({"kind": "survey", "population": {"description": "x", "size": 100, "reach": "y"}, "sample_n": 120, "criteria": []})
    with pytest.raises(ToolError, match="concepts is empty"):
        research_plan({"kind": "concept_test", "population": {"description": "x", "size": 100, "reach": "y"}, "sample_n": 50})
    with pytest.raises(ToolError, match="design must be monadic"):
        research_plan({"kind": "concept_test", "population": {"description": "x", "size": 100, "reach": "y"}, "sample_n": 50, "concepts": ["A", "B"], "design": "carousel"})
    with pytest.raises(ToolError, match="sample_n must be at least 1"):
        research_plan({"kind": "concept_test", "population": {"description": "x", "size": 100, "reach": "y"}, "sample_n": 0, "concepts": ["A", "B"]})


# --- wrappers ----------------------------------------------------------------------------


def test_tool_wrappers_return_json_with_lithuanian_intact():
    survey = json.loads(survey_check_tool({"questions": CLEAN_LT}))
    assert survey["clean"] is True and survey["questions"] == 5
    raw = survey_check_tool({"questions": CLEAN_LT})
    assert "mėnesius" in raw and "\\u" not in raw
    scores = json.loads(outcome_scores_tool({"outcomes": [{"direction": "minimize", "metric": "time", "object": "prijungti spausdintuvą", "situation": "dingus Wi-Fi", "importance": 9, "satisfaction": 4}]}))
    assert scores["outcomes"][0]["opportunity"] == 14.0 and scores["outcomes"][0]["class"] == "underserved"
    plan = json.loads(research_plan_tool({"kind": "survey", "population": {"description": "pirkėjai", "size": 1000, "reach": "SMS"}, "sample_n": 120,
                                          "criteria": [{"text": "kaina", "source": "reviews", "n_mentions": 12}],
                                          "screener": {"question": "Ar per pastarąjį mėnesį pirkote?", "options": ["Taip", "Ne"]}}))
    assert plan["kind"] == "survey" and plan["instrument_check"]["clean"] is True and plan["findings"] == []
    with pytest.raises(ToolError):
        survey_check_tool({})



def test_dispatch_routes_the_three_research_tools():
    import json as _json

    from rocky.tools import WorkspaceTools, dispatch, worker_tools

    names = [t["function"]["name"] for t in worker_tools(False)]
    assert {"survey_check", "outcome_scores", "research_plan"} <= set(names)
    out = _json.loads(dispatch(WorkspaceTools("."), "outcome_scores", {"outcomes": [{"direction": "minimize", "metric": "time", "object": "re-pair the receipt printer", "situation": "after a Wi-Fi drop", "importance": 9, "satisfaction": 4}]}))
    assert out["outcomes"][0]["opportunity"] == 14 if "outcomes" in out else True
