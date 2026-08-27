from rocky.usage import Usage, eur_from_usd, format_receipt, parse_usage, usd_from_eur


def test_parse_usage_from_openrouter_payload():
    usage = parse_usage(
        {
            "prompt_tokens": 194,
            "completion_tokens": 50,
            "total_tokens": 244,
            "cost": 0.0012,
            "completion_tokens_details": {"reasoning_tokens": 12},
        }
    )
    assert usage.prompt_tokens == 194
    assert usage.completion_tokens == 50
    assert usage.reasoning_tokens == 12
    assert usage.total_tokens == 244
    assert usage.usd == 0.0012


def test_usage_adds():
    a = Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15, usd=0.01)
    a.add(Usage(prompt_tokens=2, completion_tokens=3, total_tokens=5, usd=0.02))
    assert a.prompt_tokens == 12
    assert a.completion_tokens == 8
    assert a.total_tokens == 20
    assert round(a.usd, 6) == 0.03


def test_eur_from_usd():
    assert round(eur_from_usd(1.0, 0.92), 2) == 0.92


def test_usd_from_eur_one_euro():
    assert round(usd_from_eur(1.0, 0.92), 3) == round(1.0 / 0.92, 3)


def test_format_receipt_includes_tokens_usd_and_eur():
    text = format_receipt(
        Usage(prompt_tokens=8000, completion_tokens=4345, total_tokens=12345, usd=0.043198),
        eur_rate=0.92,
    )
    assert "8,000" in text
    assert "4,345" in text
    assert "12,345" in text
    assert "$0.043" in text
    assert "€0.040" in text
