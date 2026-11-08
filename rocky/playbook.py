"""The playbook registry: one record per marketing element.

Each record says what the element asks in plain words, when it applies, how
Rocky enforces it, and where it comes from. The ad page, the goal strip and the
playbook search all read from here instead of keeping their own copies.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ELEMENTS_PATH = Path(__file__).resolve().parent / "playbook" / "elements.toml"
# A fix from the page is one small follow-up run, capped so the price on the button is a true limit.
FIX_CAP_EUR = 0.5


@dataclass(frozen=True)
class Element:
    number: int
    title: str
    topic: str
    applies: tuple[str, ...]
    enforced: str
    codes: tuple[str, ...]
    manual: tuple[str, ...]
    source: str
    question: dict[str, str]
    why: dict[str, str]

    def fixable(self, code: str) -> bool:
        return code not in self.manual


@lru_cache(maxsize=1)
def elements() -> tuple[Element, ...]:
    raw = tomllib.loads(ELEMENTS_PATH.read_text(encoding="utf-8"))
    return tuple(
        Element(
            number=int(row["number"]),
            title=str(row["title"]),
            topic=str(row["topic"]),
            applies=tuple(row.get("applies") or ()),
            enforced=str(row["enforced"]),
            codes=tuple(row.get("codes") or ()),
            manual=tuple(row.get("manual") or ()),
            source=str(row.get("source") or ""),
            question=dict(row["question"]),
            why=dict(row["why"]),
        )
        for row in raw["element"]
    )


def for_code(code: str) -> Element | None:
    return next((element for element in elements() if code in element.codes), None)
