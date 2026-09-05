from rocky.lang import budget_question, detect_language, language_rule, normalize_language


def test_detects_lithuanian_by_diacritics_and_common_words():
    assert detect_language("Parašyk Facebook reklamą invohub.lt") == "lt"
    assert detect_language("kas yra Lietuvos sostine") == "lt"
    assert detect_language("Write three Facebook ads for invohub.lt") == "en"
    assert detect_language("") == "en"
    assert detect_language("5+5") == "en"


def test_normalize_language_accepts_model_variants():
    assert normalize_language("Lithuanian") == "lt"
    assert normalize_language("lt-LT") == "lt"
    assert normalize_language("EN") == "en"
    assert normalize_language("", "lt") == "lt"
    assert normalize_language("de") == "de"


def test_language_rule_names_the_language_and_the_lt_skill():
    worker = language_rule("lt", "worker")
    assert "Lithuanian" in worker and "lietuviu-kalbos-rasyba" in worker
    assert "blocking" in language_rule("lt", "verifier")
    assert "lietuviu-kalbos-rasyba" not in language_rule("en", "worker")


def test_budget_question_in_lithuanian():
    q, options = budget_question("lt", "hard", "marketing", 3.5, 1.0, 5.0)
    assert "Pakelti" in q and "5.00 €" in q
    assert options[0].startswith("taip")
    q_en, _ = budget_question("en", "hard", "marketing", 3.5, 1.0, 5.0)
    assert "Raise THIS RUN" in q_en
