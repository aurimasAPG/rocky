"""The playbook registry: one record per marketing element.

Each record says what the element asks in plain words, when it applies, how
Rocky enforces it, and where it comes from. The ad page, the goal strip and the
playbook search all read from here instead of keeping their own copies.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ELEMENTS_DIR = Path(__file__).resolve().parent / "playbook"
ELEMENTS_PATH = ELEMENTS_DIR / "elements.toml"  # the first file; elements-<topic>.toml files add to it
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


def _element_files() -> list[Path]:
    """elements.toml first, then every elements-<topic>.toml, so a topic module ships its own records."""
    extra = sorted(p for p in ELEMENTS_DIR.glob("elements-*.toml") if p.is_file())
    return [ELEMENTS_PATH, *extra] if ELEMENTS_PATH.is_file() else extra


@lru_cache(maxsize=1)
def elements() -> tuple[Element, ...]:
    raw: dict = {"element": []}
    seen: set[int] = set()
    for path in _element_files():
        for row in tomllib.loads(path.read_text(encoding="utf-8")).get("element") or []:
            number = int(row.get("number") or 0)
            if number and number in seen:
                continue  # the first file wins; a topic file cannot redefine a core element
            seen.add(number)
            raw["element"].append(row)
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


def checks_for(recipe: str) -> list[Element]:
    """The book elements code enforces on this kind of work: what a person can see and switch off."""
    return [e for e in elements() if e.number and e.codes and recipe in e.applies]


def _settings_path(root: str | Path) -> Path:
    return Path(root) / ".rocky" / "playbook.json"


def switched_off(root: str | Path) -> set[int]:
    try:
        raw = json.loads(_settings_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    off = raw.get("off") if isinstance(raw, dict) else None
    return {int(n) for n in off if isinstance(n, int)} if isinstance(off, list) else set()


def set_switched_off(root: str | Path, number: int, off: bool) -> set[int]:
    numbers = switched_off(root)
    numbers = numbers | {number} if off else numbers - {number}
    path = _settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"off": sorted(numbers)}), encoding="utf-8")
    return numbers
