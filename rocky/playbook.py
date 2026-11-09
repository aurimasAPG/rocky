"""The playbook registry: one record per marketing element.

Each record says what the element asks in plain words, when it applies, how
Rocky enforces it, and where it comes from. The ad page, the goal strip and the
playbook search all read from here instead of keeping their own copies.
"""

from __future__ import annotations

import json
import os
import re
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


@dataclass(frozen=True)
class Entry:
    number: int
    title: str
    body: str
    source: str
    topic: str


_HEADING = re.compile(r"^##\s+(.+?)\s*$")
_ENTRY = re.compile(r"^(\d+)\.\s+\*\*(.+?)\*\*\s*(.*)$")
_CITATION = re.compile(r"\s*\[([^\]]+)\]\([^)]*\)\s*$")
_WORD = re.compile(r"[^\W\d_]+")
_STOPWORDS = frozenset(
    "the and for are too not how why what with our you your they them this that has have from but very "
    "mano mūsų per yra kaip kodėl kas labai nes bet man mums jie tai jau dar nėra".split()
)
# A marketer describes a situation, often in Lithuanian, and the entries are in English.
_SYNONYMS = {
    "expensive": ["cost", "budget", "cpa", "spend"],
    "brang": ["cost", "budget", "cpa", "spend"],
    "kain": ["price", "pricing", "cost"],
    "ads": ["ad", "advertising", "campaign", "creative", "media"],
    "reklam": ["ad", "advertising", "campaign", "creative", "media"],
    "nebegrįž": ["repeat", "retention", "loyalty", "churn"],
    "grįž": ["repeat", "retention", "loyalty"],
    "pardav": ["sales", "sell", "selling"],
    "neperk": ["sales", "conversion", "buyer"],
    "perk": ["buy", "buyer", "purchase"],
    "pirk": ["buy", "buyer", "purchase"],
    "klient": ["customer", "client", "buyer"],
    "konkurent": ["competitor", "competition"],
    "konvers": ["conversion", "convert"],
    "rink": ["market"],
    "auditor": ["audience", "segment"],
    "segment": ["segment"],
    "tekst": ["copy", "headline", "message"],
    "antrašt": ["headline"],
    "lojal": ["loyalty", "repeat", "retention"],
    "paiešk": ["search", "seo"],
    "svetain": ["website", "landing", "site"],
    "biudžet": ["budget", "spend", "media"],
    "matav": ["measure", "measurement"],
    "test": ["test", "experiment"],
    "atsiliep": ["review", "testimonial"],
    "ženkl": ["brand"],
}


def _library_file(root: str | Path) -> Path | None:
    home = Path(os.environ.get("ROCKY_HOME") or Path.home() / ".rocky").expanduser()
    configured = os.environ.get("ROCKY_PLAYBOOK") or ""
    candidates = [Path(configured).expanduser()] if configured else []
    candidates += [Path(root) / "500list.md", home / "500list.md"]
    return next((path for path in candidates if path.is_file()), None)


def library(root: str | Path) -> list[Entry]:
    """The full list when this machine has it; otherwise the elements Rocky already applies."""
    path = _library_file(root)
    entries: list[Entry] = []
    topic = ""
    for line in path.read_text(encoding="utf-8").splitlines() if path else []:
        heading = _HEADING.match(line)
        if heading:
            topic = heading.group(1)
            continue
        entry = _ENTRY.match(line)
        if entry and topic:
            citation = _CITATION.search(entry.group(3))
            body = entry.group(3)[: citation.start()].strip() if citation else entry.group(3).strip()
            entries.append(Entry(int(entry.group(1)), entry.group(2).rstrip("."), body, citation.group(1) if citation else "", topic))
    if entries:
        return entries
    return [Entry(e.number, e.title, f"{e.question['en']} {e.why['en']}", e.source, e.topic) for e in elements() if e.number]


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _hit(term: str, words: list[str]) -> bool:
    if len(term) < 4:
        return term in words or f"{term}s" in words or term.rstrip("s") in words
    return any(word.startswith(term[:5]) for word in words)


def _terms(token: str) -> list[str]:
    extra = [term for stem, terms in _SYNONYMS.items() if token.startswith(stem) for term in terms]
    return [token, *extra]


def search(entries: list[Entry], query: str, topic: str = "", limit: int = 12) -> list[Entry]:
    """Entries for a situation described in plain words. An empty search lists a chosen topic, never all 500."""
    pool = [entry for entry in entries if not topic or entry.topic == topic]
    tokens = [word for word in dict.fromkeys(_words(query)) if len(word) > 2 and word not in _STOPWORDS]
    if not tokens:
        return pool[:limit] if topic else []
    scored: list[tuple[int, int, int, Entry]] = []
    for position, entry in enumerate(pool):
        title, body = _words(entry.title), _words(entry.body)
        points = [
            3 if any(_hit(t, title) for t in _terms(token)) else 1 if any(_hit(t, body) for t in _terms(token)) else 0
            for token in tokens
        ]
        if any(points):
            scored.append((-sum(1 for p in points if p), -sum(points), position, entry))
    return [row[3] for row in sorted(scored, key=lambda row: row[:3])[:limit]]

