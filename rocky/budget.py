from __future__ import annotations

import json
import math
from typing import Any

from rocky.catalog import Model
from rocky.llm import Completion

DEFAULT_MAX_OUTPUT_TOKENS = 4096


def _payload_tokens(messages: list[dict], tools: list[dict] | None) -> int:
    payload = json.dumps(
        {"messages": messages, "tools": tools or []},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return int(math.ceil(len(payload) / 3.0)) + 64


class BudgetedClient:
    def __init__(self, delegate, models: list[Model], remaining_usd, fire) -> None:
        self.delegate = delegate
        self.models = {model.id: model for model in models}
        self.remaining_usd = remaining_usd
        self.fire = fire
        self.blocked = False
        self.reason = ""

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)

    def _blocked_completion(self, model: str, estimated_usd: float) -> Completion:
        self.blocked = True
        self.reason = f"estimated next call ${estimated_usd:.4f} exceeds remaining budget"
        self.fire(
            "budget",
            stage="preflight",
            model=model,
            estimated_next_usd=round(estimated_usd, 6),
            remaining_usd=round(max(0.0, float(self.remaining_usd())), 6),
            note=self.reason,
        )
        return Completion(content="", model=model, raw={"budget_blocked": True})

    def complete(
        self,
        *,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        json_schema: dict | None = None,
        extra: dict | None = None,
    ) -> Completion:
        if self.blocked:
            return Completion(content="", model=model, raw={"budget_blocked": True})
        remaining = max(0.0, float(self.remaining_usd()))
        pricing = self.models.get(model)
        bounded = dict(extra or {})
        requested_output = int(bounded.get("max_tokens") or DEFAULT_MAX_OUTPUT_TOKENS)
        if pricing is None:
            bounded["max_tokens"] = requested_output
            return self.delegate.complete(
                model=model,
                messages=messages,
                tools=tools,
                json_schema=json_schema,
                extra=bounded,
            )

        prompt_tokens = _payload_tokens(messages, tools)
        prompt_usd = prompt_tokens * pricing.prompt_price
        available_output_usd = remaining - prompt_usd
        affordable_output = (
            int(available_output_usd / pricing.completion_price)
            if pricing.completion_price > 0
            else requested_output
        )
        estimated = prompt_usd + min(requested_output, max(0, affordable_output)) * pricing.completion_price
        if available_output_usd <= 0 or affordable_output <= 0 or estimated > remaining * 1.000001:
            return self._blocked_completion(model, max(remaining, prompt_usd))
        bounded["max_tokens"] = min(requested_output, affordable_output)
        return self.delegate.complete(
            model=model,
            messages=messages,
            tools=tools,
            json_schema=json_schema,
            extra=bounded,
        )
