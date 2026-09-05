"""Which language the user writes in, and the rule every role gets about it.

Rocky's audience writes goals in Lithuanian; the models default to English.
Nothing in the prompts said which language the deliverable should be in, so
a Lithuanian brief could come back as an English report. The goal's own
language is the answer unless the goal names another.
"""

from __future__ import annotations

import re

LANGUAGES = {
    "lt": "Lithuanian",
    "en": "English",
}

_LT_CHARS = set("ąčęėįšųūž")
_LT_WORDS = frozenset(
    {
        "ir", "kad", "su", "be", "apie", "kaip", "kur", "kas", "nes", "bet", "arba", "tik",
        "man", "mums", "yra", "bus", "buvo", "nuo", "iki", "per", "pagal", "dėl", "prie",
        "parašyk", "sukurk", "padaryk", "paruošk", "surink", "išanalizuok", "atlik", "ištirk",
        "reklama", "reklamos", "reklamą", "skelbimas", "skelbimai", "skelbimą", "tekstas", "tekstą",
        "straipsnis", "straipsnį", "pasiūlymas", "pasiūlymą", "prezentacija", "prezentaciją",
        "lentelė", "lentelę", "kampanija", "kampaniją", "klientas", "klientai", "klientams",
        "įmonė", "įmonės", "įmonei", "rinka", "rinkos", "konkurentai", "konkurentų", "analizė", "analizę",
        "trumpai", "lietuviškai", "lietuvių", "kalba", "puslapis", "puslapį", "svetainė", "svetainei",
        "naujienlaiškis", "naujienlaiškį", "el", "laiškas", "laišką", "pardavimai", "pardavimų",
    }
)
_WORD = re.compile(r"[a-ząčęėįšųūž]+", re.I)


def detect_language(text: str) -> str:
    """'lt' when the goal reads as Lithuanian, otherwise 'en'."""
    text = text or ""
    lowered = text.lower()
    if any(ch in _LT_CHARS for ch in lowered):
        return "lt"
    words = _WORD.findall(lowered)
    if not words:
        return "en"
    hits = sum(1 for w in words if w in _LT_WORDS)
    if hits >= 2 or (hits == 1 and len(words) <= 4):
        return "lt"
    return "en"


def normalize_language(value: object, fallback: str = "en") -> str:
    """Model answers vary ('Lithuanian', 'lt-LT', 'LT'); reduce them to a code we know."""
    raw = str(value or "").strip().lower()
    if not raw:
        return fallback
    for code, name in LANGUAGES.items():
        if raw == code or raw.startswith(code + "-") or raw == name.lower():
            return code
    return raw[:2] if len(raw) >= 2 and raw[:2].isalpha() else fallback


def language_name(code: str) -> str:
    return LANGUAGES.get(code, code)


def language_rule(code: str, role: str = "") -> str:
    """One sentence for a role's system prompt. Keys of JSON answers stay English."""
    name = language_name(code)
    rule = (
        f"The user writes in {name}. Write the deliverable, summaries, issues, notes, and any question to the "
        f"user in {name} unless the goal asks for another language; JSON keys stay in English. "
    )
    if role == "verifier":
        rule += f"A deliverable in the wrong language is a blocking defect. "
    if role == "worker" and code == "lt":
        rule += (
            "Before writing final Lithuanian copy, skill_view lietuviu-kalbos-rasyba (spelling and grammar) "
            "if it is listed in available_skills. "
        )
    return rule


def budget_question(code: str, complexity: str, domain: str, estimate: float, current: float, suggested: float) -> tuple[str, list[str]]:
    if code == "lt":
        return (
            f"Užduotis atrodo {complexity} ({domain}). "
            f"Numatoma kaina ~{estimate:.2f} €; dabartinės lubos {current:.2f} €. "
            f"Pakelti TIK ŠIAM paleidimui iki {suggested:.2f} €? "
            f"Atsakyk taip / ne / arba skaičių.",
            [
                f"taip — {suggested:.2f} € tik šiam paleidimui",
                f"ne — palikti {current:.2f} €",
                "arba įrašyk skaičių, pvz. 3",
            ],
        )
    return (
        f"This task looks {complexity} ({domain}). "
        f"Rough spend ~€{estimate:.2f}; current cap is €{current:.2f}. "
        f"Raise THIS RUN only to €{suggested:.2f}? "
        f"Reply yes / keep / or a number.",
        [
            f"yes — €{suggested:.2f} this run only",
            f"keep €{current:.2f}",
            "or type a number, e.g. 3",
        ],
    )
