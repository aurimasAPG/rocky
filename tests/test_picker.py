from rocky.catalog import Model, pick_cast


def _m(id, prompt, completion, context=128000, params=None, desc=""):
    return Model(
        id=id,
        name=id,
        prompt_price=prompt,
        completion_price=completion,
        context_length=context,
        params=set(params or ["tools"]),
        description=desc,
    )


FIXTURE = [
    _m("budget/tiny", 0.00000002, 0.00000005, context=16000, params=["tools"]),
    _m(
        "deepseek/v4-flash",
        0.00000014,
        0.00000028,
        params=["tools", "structured_outputs"],
        desc="fast agent coding",
    ),
    _m(
        "anthropic/claude-opus",
        0.000015,
        0.000075,
        params=["tools", "reasoning", "structured_outputs"],
        desc="frontier reasoning",
    ),
    _m(
        "openai/gpt-flagship",
        0.000012,
        0.000060,
        params=["tools", "reasoning", "structured_outputs"],
        desc="frontier reasoning",
    ),
    _m(
        "google/gemini-flash",
        0.0000001,
        0.0000004,
        params=["tools", "structured_outputs"],
        desc="cheap flash",
    ),
]


def test_planner_is_a_frontier_model():
    cast = pick_cast(FIXTURE, complexity="hard", domain="writing")
    assert cast.planner.startswith(("anthropic/", "openai/"))


def test_cast_includes_a_brief_critic():
    cast = pick_cast(FIXTURE, complexity="hard", domain="writing")
    assert cast.critic
    critic = next(m for m in FIXTURE if m.id == cast.critic)
    worker = next(m for m in FIXTURE if m.id == cast.worker)
    assert critic.blended_per_million >= worker.blended_per_million


def test_worker_is_cheaper_than_planner():
    cast = pick_cast(FIXTURE, complexity="moderate", domain="research")
    worker = next(m for m in FIXTURE if m.id == cast.worker)
    planner = next(m for m in FIXTURE if m.id == cast.planner)
    assert worker.blended_per_million < planner.blended_per_million


def test_verifier_uses_a_different_family():
    cast = pick_cast(FIXTURE, complexity="hard", domain="research")
    assert cast.verifier.split("/")[0] != cast.planner.split("/")[0]


def test_trivial_jobs_can_use_the_cheapest_tool_model():
    cast = pick_cast(FIXTURE, complexity="trivial", domain="ops")
    assert cast.worker in {"budget/tiny", "google/gemini-flash", "deepseek/v4-flash"}


def test_assess_model_is_cheap():
    cast = pick_cast(FIXTURE, complexity="moderate", domain="mixed")
    assess = next(m for m in FIXTURE if m.id == cast.assess)
    planner = next(m for m in FIXTURE if m.id == cast.planner)
    assert assess.blended_per_million <= planner.blended_per_million


def test_batch_only_models_are_ignored():
    models = FIXTURE + [
        _m(
            "openai/gpt-5.5:batch",
            0.00001,
            0.00004,
            params=["tools", "reasoning", "structured_outputs"],
            desc="frontier reasoning",
        )
    ]
    cast = pick_cast(models, complexity="hard", domain="writing")
    assert ":batch" not in cast.planner
    assert ":batch" not in cast.worker
    assert ":batch" not in cast.verifier


def test_worker_defaults_to_gemini_37_flash_when_present():
    models = FIXTURE + [
        _m(
            "google/gemini-3.8-flash",
            0.000000375,
            0.000001875,
            context=1_000_000,
            params=["tools", "structured_outputs", "reasoning"],
            desc="agentic flash",
        ),
        _m(
            "google/gemini-3.8-flash-lite",
            0.0000001,
            0.0000004,
            context=1_000_000,
            params=["tools", "structured_outputs"],
            desc="lite",
        ),
    ]
    cast = pick_cast(models, complexity="moderate", domain="writing")
    assert cast.worker == "google/gemini-3.8-flash"
    assert "default worker" in cast.reasons["worker"]


def test_worker_skips_gemini_37_batch_and_lite():
    models = FIXTURE + [
        _m(
            "google/gemini-3.8-flash:batch",
            0.0000001,
            0.0000005,
            params=["tools", "structured_outputs"],
        ),
        _m(
            "google/gemini-3.8-flash-lite",
            0.0000001,
            0.0000004,
            params=["tools", "structured_outputs"],
        ),
    ]
    cast = pick_cast(models, complexity="moderate", domain="research")
    assert cast.worker != "google/gemini-3.8-flash:batch"
    assert cast.worker != "google/gemini-3.8-flash-lite"


def test_worker_pin_overrides_default():
    models = FIXTURE + [
        _m(
            "google/gemini-3.8-flash",
            0.000000375,
            0.000001875,
            params=["tools", "structured_outputs"],
        )
    ]
    cast = pick_cast(
        models,
        complexity="moderate",
        domain="research",
        preferred_worker="deepseek/v4-flash",
    )
    assert cast.worker == "deepseek/v4-flash"


def test_verifier_skips_ultra_expensive_when_a_strong_peer_exists():
    models = FIXTURE + [
        _m(
            "openai/ultra-pro",
            0.00008,
            0.0004,
            params=["tools", "reasoning", "structured_outputs"],
            desc="frontier reasoning",
        )
    ]
    cast = pick_cast(models, complexity="hard", domain="research")
    verifier = next(m for m in models if m.id == cast.verifier)
    assert verifier.blended_per_million <= 20
    assert verifier.family != cast.planner.split("/")[0]


def test_stall_swap_goes_up_in_capability_not_down():
    from rocky.catalog import Model, alternate_worker

    models = [
        Model(id="google/flash", name="", prompt_price=1e-7, completion_price=4e-7, context_length=200_000, params={"tools", "structured_outputs"}),
        Model(id="inclusionai/ling", name="", prompt_price=2e-8, completion_price=6e-8, context_length=128_000, params={"tools"}),
        Model(id="deepseek/v4", name="", prompt_price=3e-7, completion_price=1.2e-6, context_length=128_000, params={"tools", "reasoning", "structured_outputs"}),
        Model(id="openai/mini", name="", prompt_price=1e-6, completion_price=4e-6, context_length=200_000, params={"tools", "reasoning", "structured_outputs"}),
        Model(id="anthropic/opus", name="", prompt_price=1.5e-5, completion_price=7.5e-5, context_length=200_000, params={"tools", "reasoning", "structured_outputs"}),
    ]
    swap = alternate_worker(models, "moderate", "marketing", exclude={"google/flash"})
    assert swap is not None
    assert swap.id != "inclusionai/ling", "must not fall back to the weakest model"
    assert swap.family != "google"
    assert swap.blended_per_million <= 12
    # second stall: another family again, still not the cheapest
    swap2 = alternate_worker(models, "moderate", "marketing", exclude={"google/flash", swap.id})
    assert swap2 is not None and swap2.id not in {"google/flash", swap.id, "inclusionai/ling"}


def test_verifier_respects_the_price_cap_for_its_complexity():
    from rocky.catalog import VERIFY_MAX_PRICE

    models = FIXTURE + [
        _m("x-ai/grok-strong", 0.000003, 0.000006, params=["tools", "reasoning", "structured_outputs"], desc="frontier reasoning"),
        _m("openai/gpt-codex", 0.000002, 0.000004, params=["tools", "reasoning", "structured_outputs"], desc="agent coding"),
    ]
    for complexity in ("trivial", "moderate", "hard"):
        cast = pick_cast(models, complexity=complexity, domain="marketing")
        verifier = next(m for m in models if m.id == cast.verifier)
        assert verifier.blended_per_million <= VERIFY_MAX_PRICE[complexity], complexity
        assert verifier.family != cast.planner.split("/")[0]
    assert pick_cast(models, complexity="hard", domain="marketing").verifier == "x-ai/grok-strong", "codex is not a prose grader"
    assert pick_cast(models, complexity="hard", domain="code").verifier == "openai/gpt-codex"
    assert "under $12/M" in pick_cast(models, complexity="hard", domain="marketing").reasons["verifier"]


def test_verifier_falls_back_to_the_cheapest_peer_when_nothing_is_under_the_cap():
    cast = pick_cast(FIXTURE, complexity="hard", domain="marketing")
    verifier = next(m for m in FIXTURE if m.id == cast.verifier)
    assert verifier.family != cast.planner.split("/")[0]
