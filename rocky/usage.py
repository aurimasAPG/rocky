from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

_FALLBACK_USD_EUR = 0.92
DEFAULT_BUDGET_EUR = 1.0
_cached_rate: float | None = None


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    usd: float = 0.0

    def add(self, other: Usage) -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.reasoning_tokens += other.reasoning_tokens
        self.total_tokens += other.total_tokens or (
            other.prompt_tokens + other.completion_tokens
        )
        self.usd += other.usd
        if self.total_tokens == 0:
            self.total_tokens = self.prompt_tokens + self.completion_tokens

    def payload(self, eur_rate: float) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.total_tokens or (self.prompt_tokens + self.completion_tokens),
            "usd": round(self.usd, 3),
            "eur": round(eur_from_usd(self.usd, eur_rate), 3),
            "eur_rate": round(eur_rate, 4),
        }


def parse_usage(raw: dict | None) -> Usage:
    data = raw or {}
    details = data.get("completion_tokens_details") or {}
    prompt = int(data.get("prompt_tokens") or 0)
    completion = int(data.get("completion_tokens") or 0)
    reasoning = int(details.get("reasoning_tokens") or 0)
    total = int(data.get("total_tokens") or 0) or (prompt + completion)
    try:
        usd = float(data.get("cost") or 0)
    except (TypeError, ValueError):
        usd = 0.0
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        reasoning_tokens=reasoning,
        total_tokens=total,
        usd=usd,
    )


def eur_from_usd(usd: float, rate: float) -> float:
    return float(usd) * float(rate)


def usd_from_eur(eur: float, rate: float) -> float:
    if float(rate) <= 0:
        return float(eur)
    return float(eur) / float(rate)


def fetch_usd_eur_rate() -> float:
    global _cached_rate
    if _cached_rate is not None:
        return _cached_rate
    try:
        response = httpx.get(
            "https://api.frankfurter.app/latest",
            params={"from": "USD", "to": "EUR"},
            timeout=8.0,
        )
        response.raise_for_status()
        rate = float((response.json().get("rates") or {}).get("EUR") or 0)
        if rate > 0:
            _cached_rate = rate
            return rate
    except (httpx.HTTPError, TypeError, ValueError, KeyError):
        pass
    _cached_rate = _FALLBACK_USD_EUR
    return _FALLBACK_USD_EUR


def _commas(n: int) -> str:
    return f"{int(n):,}"


# Rough EUR a full loop usually burns. Not a quote — a tripwire for the default €1 cap.
_COMPLEXITY_EUR = {
    "trivial": 0.25,
    "moderate": 0.90,
    "hard": 3.50,
}
_CAP_STEPS = (2.0, 3.0, 5.0, 8.0, 10.0, 15.0, 20.0, 30.0, 50.0, 100.0)


def estimate_run_eur(assessment: dict | None, goal: str = "") -> float:
    assessment = assessment or {}
    complexity = str(assessment.get("complexity") or "moderate").lower()
    domain = str(assessment.get("domain") or "mixed").lower()
    base = _COMPLEXITY_EUR.get(complexity, 0.90)
    from rocky.recipes import find_recipe

    recipe = find_recipe(goal, assessment)
    if recipe is not None:
        base *= float(recipe.cost_multiplier or 1.0)
    elif domain == "research":
        base *= 1.4
    elif domain == "code" and complexity == "hard":
        base *= 1.3
    if assessment.get("needs_web"):
        base *= 1.15
    return round(base, 2)


def suggest_cap_eur(estimate: float, current: float) -> float:
    target = max(float(estimate) * 1.3, float(current))
    for step in _CAP_STEPS:
        if step >= target - 1e-9 and step > float(current) + 0.049:
            return float(step)
    return round(max(target, float(current) * 2), 2)


def needs_budget_lift(estimate: float, current: float) -> bool:
    return float(estimate) > float(current) * 1.15


def parse_budget_reply(text: str, suggested: float, current: float) -> float:
    raw = (text or "").strip().lower().replace(",", ".")
    raw = raw.replace("€", "").replace("eur", "").strip()
    if raw in {"1", "yes", "y", "ok", "taip", "kelti", "raise"}:
        return round(float(suggested), 2)
    if raw in {"", "2", "keep", "no", "n", "stay", "ne", "palikti", "default"}:
        return round(float(current), 2)
    try:
        value = float(raw)
    except ValueError:
        return round(float(current), 2)
    if value <= 0:
        return round(float(current), 2)
    return round(value, 2)


def format_receipt(usage: Usage, eur_rate: float) -> str:
    payload = usage.payload(eur_rate)
    extra = ""
    if payload["reasoning_tokens"]:
        extra = f" · {_commas(payload['reasoning_tokens'])} reason"
    return (
        f"{_commas(payload['prompt_tokens'])} in · "
        f"{_commas(payload['completion_tokens'])} out · "
        f"{_commas(payload['total_tokens'])} tokens{extra}  ·  "
        f"${payload['usd']:.3f}  ·  €{payload['eur']:.3f}"
    )
