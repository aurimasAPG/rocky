from __future__ import annotations

import json
import re


class JsonParseError(ValueError):
    pass


_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def parse_json_object(text: str) -> dict:
    if not text or not str(text).strip():
        raise JsonParseError("empty response")
    raw = str(text).strip()
    fence = _FENCE.search(raw)
    if fence:
        raw = fence.group(1).strip()
    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        raise JsonParseError(f"no JSON object in: {raw[:200]}")
    try:
        value = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise JsonParseError(str(exc)) from exc
    if not isinstance(value, dict):
        raise JsonParseError("JSON was not an object")
    return value
