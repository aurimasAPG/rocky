from __future__ import annotations

import math
from dataclasses import dataclass, field


PLAN_FAMILIES = {
    "anthropic": 1.0,
    "openai": 0.96,
    "google": 0.9,
    "x-ai": 0.9,
    "xai": 0.9,
    "moonshotai": 0.78,
    "z-ai": 0.76,
    "deepseek": 0.74,
    "qwen": 0.7,
    "mistralai": 0.68,
}

WORK_FAMILIES = {
    "deepseek": 0.92,
    "google": 0.9,
    "z-ai": 0.88,
    "qwen": 0.86,
    "moonshotai": 0.86,
    "anthropic": 0.84,
    "openai": 0.84,
    "x-ai": 0.82,
    "xai": 0.82,
    "mistralai": 0.75,
    "meta-llama": 0.62,
}

COMPLEXITY_FLOOR = {
    "trivial": 0.15,
    "moderate": 0.45,
    "hard": 0.7,
}

# Cheap multilingual workhorse (Lithuanian + tools). Overridable via ROCKY_WORKER.
DEFAULT_WORKER_MODEL = "google/gemini-3.8-flash"
# Planner whenever the catalog has it; otherwise the strongest current model.
DEFAULT_PLANNER_MODEL = "anthropic/claude-opus-5.5"
# Automatic picks ignore models released this long before the newest of their own family.
# Capability leans on price, and new generations are often cheaper than the ones they replace.
CURRENT_WINDOW_DAYS = 90


@dataclass
class Model:
    id: str
    name: str
    prompt_price: float
    completion_price: float
    context_length: int
    params: set[str]
    description: str = ""
    created: int = 0
    retiring: bool = False

    @property
    def family(self) -> str:
        return self.id.split("/", 1)[0]

    @property
    def blended_per_million(self) -> float:
        return (self.prompt_price * 0.35 + self.completion_price * 0.65) * 1_000_000

    @property
    def has_tools(self) -> bool:
        return "tools" in self.params

    @property
    def has_structured(self) -> bool:
        return "structured_outputs" in self.params

    @property
    def has_reasoning(self) -> bool:
        return "reasoning" in self.params or "include_reasoning" in self.params


@dataclass
class Cast:
    assess: str
    critic: str
    planner: str
    worker: str
    verifier: str
    reasons: dict[str, str] = field(default_factory=dict)


def parse_models(payload: dict) -> list[Model]:
    models: list[Model] = []
    for row in payload.get("data") or []:
        mid = row.get("id") or ""
        if not mid or "embed" in mid.lower() or "moderation" in mid.lower():
            continue
        if ":batch" in mid or mid.endswith("-batch"):
            continue
        arch = row.get("architecture") or {}
        outputs = arch.get("output_modalities") or ["text"]
        if "text" not in outputs:
            continue
        pricing = row.get("pricing") or {}
        try:
            prompt = float(pricing.get("prompt") or 0)
            completion = float(pricing.get("completion") or 0)
        except (TypeError, ValueError):
            continue
        if prompt < 0 or completion < 0:
            continue
        params = {str(p) for p in (row.get("supported_parameters") or [])}
        models.append(
            Model(
                id=mid,
                name=row.get("name") or mid,
                prompt_price=prompt,
                completion_price=completion,
                context_length=int(row.get("context_length") or 0),
                params=params,
                description=(row.get("description") or "")[:400],
                created=int(row.get("created") or 0),
                retiring=bool(row.get("expiration_date")),
            )
        )
    return models


def _quality_from_price(blended_per_m: float) -> float:
    return min(1.0, math.log10(max(blended_per_m, 0.001) * 80 + 1) / 3.2)


def capability(model: Model, families: dict[str, float]) -> float:
    score = families.get(model.family, 0.4)
    if model.has_reasoning:
        score += 0.18
    if model.has_structured:
        score += 0.06
    if model.context_length >= 100_000:
        score += 0.08
    elif model.context_length >= 32_000:
        score += 0.03
    score += 0.45 * _quality_from_price(model.blended_per_million)
    desc = model.description.lower()
    if any(w in desc for w in ("agent", "coding", "tool", "reasoning", "frontier")):
        score += 0.04
    return score


def _tool_models(models: list[Model], min_context: int = 8000) -> list[Model]:
    out = []
    for m in models:
        if not m.has_tools:
            continue
        if ":batch" in m.id:
            continue
        if m.context_length and m.context_length < min_context:
            continue
        # Skip free/zero-price as primary picks — they rate-limit hard.
        if m.prompt_price == 0 and m.completion_price == 0:
            continue
        out.append(m)
    return out or [m for m in models if m.has_tools and ":batch" not in m.id]


def _current(models: list[Model]) -> list[Model]:
    """Models fit for an automatic pick: not retiring, not a generation behind."""
    newest: dict[str, int] = {}
    for m in models:
        newest[m.family] = max(newest.get(m.family, 0), m.created)
    window = CURRENT_WINDOW_DAYS * 86400
    return [m for m in models if not m.retiring and (not m.created or m.created >= newest[m.family] - window)] or models


def _matches_preferred_worker(model_id: str, preferred: str) -> bool:
    if not preferred or ":batch" in model_id:
        return False
    if model_id == preferred:
        return True
    if not model_id.startswith(preferred):
        return False
    rest = model_id[len(preferred) :].lower()
    if rest.startswith("-lite") or "lite" in rest:
        return False
    return rest.startswith("-") or rest.startswith("/")


def _pinned_worker(pool: list[Model], preferred: str) -> Model | None:
    hits = [m for m in pool if _matches_preferred_worker(m.id, preferred)]
    if not hits:
        return None
    exact = next((m for m in hits if m.id == preferred), None)
    return exact or min(hits, key=lambda m: m.blended_per_million)


def _work_families(domain: str) -> dict[str, float]:
    if domain in {"code", "ops"}:
        return WORK_FAMILIES
    return {**WORK_FAMILIES, **PLAN_FAMILIES}


def eligible_workers(models: list[Model], complexity: str, domain: str) -> list[Model]:
    """Tool models strong enough for this complexity, cheapest first."""
    complexity = complexity if complexity in COMPLEXITY_FLOOR else "moderate"
    tool = _current(_tool_models(models))
    floor = COMPLEXITY_FLOOR[complexity]
    families = _work_families(domain)
    pool = [
        m
        for m in tool
        if capability(m, families) >= floor
        and m.context_length >= (16_000 if complexity == "trivial" else 32_000)
    ] or tool
    return sorted(pool, key=lambda m: (m.blended_per_million, -capability(m, families)))


# A stall swap may pick a worker up to this blended $/M — strong, not frontier-priced.
STALL_SWAP_MAX_PRICE = 12.0


def alternate_worker(
    models: list[Model],
    complexity: str,
    domain: str,
    exclude: set[str],
) -> Model | None:
    """Worker to try after a stall: the most capable affordable model from an unused family.

    A stalled worker was too weak for the plan, so the swap goes *up* in
    capability (never to the cheapest remaining model), preferring a family
    that has not been tried yet.
    """
    pool = [m for m in eligible_workers(models, complexity, domain) if m.id not in exclude]
    if not pool:
        return None
    families = _work_families(domain)
    used_families = {mid.split("/", 1)[0] for mid in exclude}
    affordable = [m for m in pool if m.blended_per_million <= STALL_SWAP_MAX_PRICE] or pool
    fresh = [m for m in affordable if m.family not in used_families] or affordable
    return max(fresh, key=lambda m: (capability(m, families), -m.blended_per_million))


# Blended $/M ceiling for the verifier by complexity. It reads the deliverable slide by
# slide over several tool turns, so its input tokens dominate: the invohub run paid
# €1.50 per verification at $18/M. A strong model under these caps grades just as well.
VERIFY_MAX_PRICE = {"trivial": 5.0, "moderate": 8.0, "hard": 12.0}


def pick_verifier(plan_pool: list[Model], planner: Model, worker: Model, complexity: str, domain: str) -> Model:
    """Strongest model from another family than the planner, under the complexity's price cap."""
    cap = VERIFY_MAX_PRICE.get(complexity, VERIFY_MAX_PRICE["moderate"])
    pool = [m for m in plan_pool if m.family != planner.family and m.id != worker.id] or [
        m for m in plan_pool if m.id != planner.id
    ]
    if domain not in {"code", "ops"}:
        # Code-tuned variants grade prose no better and read oddly in the cast panel.
        prose = [m for m in pool if "codex" not in m.id and "coder" not in m.id]
        pool = prose or pool
    affordable = [m for m in pool if m.blended_per_million <= cap]
    if affordable:
        return affordable[0]  # plan_pool is capability-sorted
    cheapest = sorted(pool, key=lambda m: m.blended_per_million)
    return (cheapest or [planner])[0]


def pick_cast(
    models: list[Model],
    complexity: str,
    domain: str,
    preferred_worker: str | None = None,
) -> Cast:
    complexity = complexity if complexity in COMPLEXITY_FLOOR else "moderate"
    tool = _tool_models(models)
    if not tool:
        raise ValueError("no tool-capable models in catalog")
    current = _current(tool)

    # The assessor reads goals in any language, so the cheapest model of a family Rocky knows.
    assess_pool = sorted(
        [m for m in current if m.context_length >= 8000 and m.family in PLAN_FAMILIES]
        or [m for m in current if m.context_length >= 8000],
        key=lambda m: (not m.has_structured, m.blended_per_million, -m.context_length),
    )
    assess = assess_pool[0]

    plan_pool = sorted(current, key=lambda m: capability(m, PLAN_FAMILIES), reverse=True)
    planner = next((m for m in tool if m.id == DEFAULT_PLANNER_MODEL), plan_pool[0])
    # Not dearer than the planner, or a cheaper planner would only move the cost to this role.
    others = [m for m in plan_pool if m.id != planner.id]
    critic = next((m for m in others if m.blended_per_million <= planner.blended_per_million), None) or next(iter(others), planner)

    work_families = _work_families(domain)
    eligible = eligible_workers(tool, complexity, domain)
    pin = DEFAULT_WORKER_MODEL if preferred_worker is None else preferred_worker.strip()
    worker = _pinned_worker(eligible, pin) or _pinned_worker(tool, pin)
    pinned = worker is not None
    if worker is None:
        worker = min(
            eligible,
            key=lambda m: (m.blended_per_million, -capability(m, work_families)),
        )

    verifier = pick_verifier(plan_pool, planner, worker, complexity, domain)

    worker_reason = (
        f"default worker {worker.id} ({worker.blended_per_million:.3f}/M)"
        if pinned
        else f"cheapest tool model above {complexity} floor ({worker.blended_per_million:.3f}/M)"
    )
    return Cast(
        assess=assess.id,
        critic=critic.id,
        planner=planner.id,
        worker=worker.id,
        verifier=verifier.id,
        reasons={
            "assess": f"cheapest structured/tool model ({assess.blended_per_million:.3f}/M blended)",
            "critic": f"critiques the brief before work is split ({critic.family})",
            "planner": f"default planner in {planner.family}" if planner.id == DEFAULT_PLANNER_MODEL else f"highest planning capability in {planner.family}",
            "worker": worker_reason,
            "verifier": f"strong model in {verifier.family}, not {planner.family}, under ${VERIFY_MAX_PRICE.get(complexity, 8.0):.0f}/M ({verifier.blended_per_million:.2f}/M)",
        },
    )
