from __future__ import annotations


def _sid(step: dict) -> int:
    try:
        return int(step.get("id") or 0)
    except (TypeError, ValueError):
        return 0


def _deps(step: dict) -> list[int]:
    raw = step.get("depends_on") or []
    out = []
    for item in raw:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def schedule_waves(steps: list[dict]) -> list[list[dict]]:
    if not steps:
        return []
    remaining = {_sid(s): dict(s) for s in steps if _sid(s)}
    if not remaining:
        return [list(steps)]
    done: set[int] = set()
    waves: list[list[dict]] = []
    while remaining:
        ready = [s for s in remaining.values() if set(_deps(s)).issubset(done)]
        if not ready:
            ready = list(remaining.values())
        ready.sort(key=_sid)
        waves.append(ready)
        for step in ready:
            ident = _sid(step)
            done.add(ident)
            remaining.pop(ident, None)
    return waves
