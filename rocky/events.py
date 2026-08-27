from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable


Emit = Callable[["Event"], None]


@dataclass
class Event:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    def as_dict(self) -> dict[str, Any]:
        return {"type": self.type, "ts": self.ts, "payload": self.payload}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Event":
        return cls(
            type=str(raw.get("type") or "unknown"),
            payload=dict(raw.get("payload") or {}),
            ts=str(raw.get("ts") or ""),
        )
