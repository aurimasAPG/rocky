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
