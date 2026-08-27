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


def test_parse_usage_reads_cached_tokens_and_counts_calls():
    from rocky.usage import format_receipt, parse_usage

    u = parse_usage(
        {
            "prompt_tokens": 1000,
            "completion_tokens": 50,
            "prompt_tokens_details": {"cached_tokens": 800},
            "cost": 0.002,
        }
    )
    assert u.cached_tokens == 800
    assert u.calls == 1
    assert "800 cached" in format_receipt(u, 0.92)


def test_role_tally_splits_by_role():
    from rocky.usage import RoleTally, Usage, format_role_split

    t = RoleTally()
    t.add(Usage(prompt_tokens=10, completion_tokens=5, usd=0.01, calls=1), "critic")
    t.add(Usage(prompt_tokens=10, completion_tokens=5, usd=0.02, calls=1), "planner")
    t.add(Usage(prompt_tokens=100, completion_tokens=50, usd=0.005, cached_tokens=60, calls=1), "worker")
    t.add(Usage(prompt_tokens=100, completion_tokens=50, usd=0.005, calls=1), "worker")
    t.add(Usage(usd=0.001, calls=1), "mystery")
    p = t.payload(1.0)
    assert p["usd"] == 0.041
    assert p["by_role"]["worker"]["calls"] == 2
    assert p["by_role"]["worker"]["usd"] == 0.01
    assert p["by_role"]["worker"]["cached_tokens"] == 60
    assert p["by_role"]["other"]["usd"] == 0.001
    split = format_role_split(t.by_role, 1.0)
    assert split.startswith("critic €0.010 · planner €0.020 · worker €0.010 (60 cached)")
    assert "other €0.001" in split
    assert p["role_receipt"] == split
