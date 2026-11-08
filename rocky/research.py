"""Customer-research designs checked by code before anyone is asked a question.

A marketer fields a survey, a concept test or an outcome study once; a badly
worded question or an unbalanced design wastes the whole sample and the model
cannot see that from the inside. Three pure functions do the checking:

* ``survey_check`` audits an instrument question by question: double-barrelled
  wording, leading words, vague quantifiers, missing escape options, scale
  mismatches, jargon, motive questions with no behaviour question to back them,
  unbounded recall, response-field bias, routing that leads nowhere, awareness
  surveys that mix knowing the name with remembering the ad, and blind-comparison
  protocols with a fixed order or no blinded arm.
* ``outcome_scores`` validates Jobs-to-be-Done outcome statements (direction +
  metric + object + situation, no generic words, no solution in place of an
  outcome) and computes opportunity scores
  ``importance + max(importance − satisfaction, 0)``, classes, per-brand gaps
  and overserved outcomes that cost money; mode ``job_map`` checks a job map
  against the eight phases.
* ``research_plan`` turns criteria or concepts into a fieldable design: quota,
  sample sufficiency through ``experiment_plan``, a generated instrument that
  passes ``survey_check``, and a decision rule with its defaults labelled.

Everything is lexical and deterministic, works for Lithuanian and English, and
says what is missing instead of inventing a number or a question.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from rocky.calc import experiment_plan
from rocky.tools import ToolError

QUESTION_TYPES = ("single", "multi", "scale", "open", "number")
LEVEL_ORDER = {"block": 0, "warn": 1, "info": 2}
DIRECTIONS = ("minimize", "increase")
METRICS = ("time", "likelihood", "number", "cost", "other")
PHASES = ("define", "locate", "prepare", "confirm", "execute", "monitor", "modify", "conclude")
TODAY_STATES = ("ok", "repeated", "skipped", "out_of_order", "unknown")
DEFAULT_SCALE_MAX = 10
DEFAULT_THRESHOLD = 12.0          # Ulwick's "underserved" line on a 10-point scale
COMPARABLE_N = 30                 # per brand, before two satisfaction means are compared
MINUTES_CLOSED = 0.25
MINUTES_OPEN = 0.75
TOO_LONG_MINUTES = 12.0
PILOT_N = 5
SURVEY_MIN_N = 100
DEFAULT_TOP_TWO_BOX_PCT = 30.0
DEFAULT_MIN_COMPREHENSION = 0.7
DEFAULT_TOP_TWO_BOX_MIN = 0.4
SHORT_OPEN_FIELD = 60

# --- word lists (LT + EN) ------------------------------------------------------

_LT_MARK = re.compile(r"[ąčęėįšųūžĄČĘĖĮŠŲŪŽ]|\b(ar|kaip|kiek|jūs|jums|kad|kokį|kurį|ką|kada|kodėl|kuris|pirkote)\b", re.I)
_CONJ = re.compile(r"\s(?:ir|arba|bei|and|or)\s", re.I)
_CLAUSE_MARK = re.compile(
    r"\b(ar|kaip|kiek|kodėl|kada|kur|koks|kokia|kokį|kokią|kokie|kokios|kokius|kokias|kuris|kuri|kurį|kurią|kurie|kurios|kuriuos|kurias"
    r"|do|does|did|are|is|was|were|would|will|have|has|how|what|which|why|when|where|you)\b"
    r"|\w{3,}(?:ate|ote|ite|ėte|atės|otės|ėtės|tumėte)\b",
    re.I,
)
_QUOTED = re.compile(r"„[^“”\"]*[“”\"]|“[^”]*”|\"[^\"]*\"|'[^']*'")
_LEADING = re.compile(
    r"\bpuik\w*|\bnuostab\w*|\bgeriaus\w*|\bakivaizd\w*|\bvisi\s+žino|\bsutinkat\w*\s*,?\s*kad|\bsutiktumėt\w*\s*,?\s*kad"
    r"|\bar\s+ne\s*tiesa|\bkodėl\s+jums\s+patinka|\bkiek\s+jums\s+patinka"
    r"|\bgreat\b|\bexcellent\b|\bamazing\b|\bwonderful\b|\bobviously\b|\beveryone\s+knows|\bdon'?t\s+you\s+(?:agree|think)"
    r"|\bwouldn'?t\s+you\s+(?:agree|say)|\bdo\s+you\s+agree\s+that|\bhow\s+much\s+do\s+you\s+(?:love|like|enjoy)|\blove\b|\bhate\b",
    re.I,
)
_VAGUE = re.compile(r"\b(dažnai|kartais|retai|reguliariai|nuolat|dažnokai|often|sometimes|rarely|seldom|regularly|frequently|occasionally|usually)\b", re.I)
_VAGUE_ASKING = re.compile(r"\b(kaip|how)\s+(dažnai|often|frequently|regularly)\b", re.I)
_HAS_NUMBER = re.compile(r"\d|\b(kart\w*\s+per|per\s+(savait|mėnes|dien|met)\w*|times?\s+(a|per)\s+\w+|per\s+(week|month|day|year)|once|twice|nė\s+karto|never|niekada|kasdien|daily|weekly|monthly|visada|always|every)\b", re.I)
_ESCAPE = re.compile(r"\b(kit[aą]s?|kitk\w*|nežinau|neprisimenu|nė\s+vien\w*|nei\s+vien\w*|netaikoma|nenaudoju|neperku|nesu\s+\w+|sunku\s+pasakyti|neturiu\s+nuomonės|other|don'?t\s+know|do\s+not\s+know|none|not\s+sure|unsure|prefer\s+not|no\s+opinion|not\s+applicable|n/a|never\s+(used|bought))\b", re.I)
_AGREE_WORDS = re.compile(r"\b(sutinkate|sutinku|nesutinku|sutiktumėte|agree|disagree)\b", re.I)
_FREQ_WORDS = re.compile(r"\b(kaip\s+dažnai|how\s+often|niekada|kartais|dažnai|visada|never|sometimes|often|always|frequency|dažnumas)\b", re.I)
_FREQ_LABEL = re.compile(r"\b(niekada|retai|kartais|dažnai|visada|never|rarely|sometimes|often|always)\b", re.I)
_AGREE_LABEL = re.compile(r"\b(sutinku|nesutinku|agree|disagree)\b", re.I)
DEFAULT_JARGON = (
    "KPI", "ROI", "CTR", "CPC", "CPM", "ROAS", "SKU", "B2B", "B2C", "CRM", "SaaS", "omnichannel", "omnikanal", "funnel", "piltuvėlis",
    "konversija", "conversion rate", "retargeting", "atribucija", "attribution", "touchpoint", "lead", "USP", "engagement", "įsitraukimas",
)
_MOTIVE = re.compile(r"\b(kodėl|dėl\s+kokių\s+priežasčių|kas\s+(jus\s+)?paskatino|kas\s+lėmė|why|what\s+motivat\w*|what\s+made\s+you|for\s+what\s+reasons?|what\s+(?:are|were)\s+(?:the|your)\s+reasons?)\b", re.I)
_ATTITUDE = re.compile(r"\b(ar\s+(jums\s+)?patinka|kaip\s+vertinate|kiek\s+(jums\s+)?svarb\w*|kiek\s+esate\s+patenkint\w*|ar\s+rekomenduotumėte|ar\s+sutinkate|do\s+you\s+like|how\s+do\s+you\s+rate|how\s+important|how\s+satisfied|would\s+you\s+recommend|do\s+you\s+agree|your\s+opinion|jūsų\s+nuomone)\b", re.I)
_BEHAVIOUR = re.compile(
    r"\b(ar\s+(?:esate\s+)?pirk\w+|kiek\s+kartų|kada\s+paskutinį|kaip\s+dažnai|ar\s+naudoja\w+|kur\s+perkate|kiek\s+(?:eurų|€|sumokėjote|išleidote)|ką\s+pirkote"
    r"|did\s+you|have\s+you|how\s+many\s+times|when\s+did\s+you\s+last|how\s+often|do\s+you\s+use|which\s+\w+\s+do\s+you\s+(?:use|buy|shop)|where\s+do\s+you\s+(?:buy|shop)|how\s+much\s+did\s+you\s+(?:pay|spend))\b",
    re.I,
)
_PAST_EVENT = re.compile(
    r"\b(?:pirk|pirkdav|užsak|užsisak|lank|apsilank|mat|gird|skait|naudoj|band|išband|rink|pasirink|mokėj|sumokėj|gav|grąžin|skambin|raš|ieškoj|rad|atsisak|nutrauk|dalyvav)\w*(?:ote|ėte|otės|ėtės)\b"
    r"|\besate\s+\w+(?:ę|ęs|usi|usios)\b|\bpaskutinį\s+kartą\b|\bpastarąjį\s+kartą\b"
    r"|\bdid\s+you\b|\bhave\s+you\s+(?:ever\s+)?\w+(?:ed|en|ght|t)\b|\bwere\s+you\b|\bwhen\s+did\b|\bwhen\s+was\b|\blast\s+time\b",
    re.I,
)
_TIME_FRAME = re.compile(
    r"\bper\s+(?:pastar|paskutin)\w+|\b(?:pastar|paskutin)\w+\s+\d+|\b\d+\s*(?:dien|savait|mėnes|met)\w*|\b(?:šį|šią|šiais|šiemet|praėjus\w*|pernai|vakar|šiandien|nuo\s+20\d\d)\b"
    r"|\b(?:last|past)\s+(?:\d+\s+)?(?:day|week|month|year|time)s?\b|\bthis\s+(?:week|month|year)\b|\byesterday\b|\btoday\b|\bsince\s+20\d\d\b|\bin\s+20\d\d\b|\bin\s+the\s+(?:last|past)\b",
    re.I,
)
_USAGE_VERB = re.compile(r"\b(naudojat\w*|naudojatės|perkate|lankotės|žiūrite|skaitote|klausote|kur[iį]\w*|koki[ąųeo]s?\w*|which\s+\w+(?:\s+\w+)?\s+do\s+you|do\s+you\s+(?:use|buy|shop|watch|read|listen)|where\s+do\s+you)\b", re.I)
_LIST_EXPECTED = re.compile(r"\b(kokius|kokias|kuriuos|kurias|kokie|kokios|kurie|kurios|visus|visas|išvardykite|which\s+\w+s\b|what\s+\w+s\s+do\s+you|list|name\s+all|all\s+the)\b", re.I)
_AWARENESS = re.compile(r"žinomum\w*|awareness|prisimen\w*|\brecall\b|girdėj\w*|heard\s+of|familiar\w*", re.I)
_UNAIDED = re.compile(r"ateina\s+į\s+galvą|galite\s+išvardyti|išvardykite|prisimenate|come\s+to\s+mind|can\s+you\s+name|name\s+(?:any|the)|think\s+of|recall", re.I)
_AIDED = re.compile(r"\b(žinote|girdėjote|pažįstate|esate\s+girdėję|atpažįstate|heard\s+of|familiar|recogni[sz]e|know)\b", re.I)
_AD_WORD = re.compile(r"reklam\w*|advert\w*|\bads?\b|commercial", re.I)
_AD_MEMORY = re.compile(r"\b(matėte|girdėjote|prisimenate|pastebėjote|seen|saw|heard|recall|remember|noticed)\b", re.I)
_FIRST_CONTACT = re.compile(r"(pirmą\s+kartą|iš\s+kur|first).*(sužinoj\w*|išgird\w*|susidūr\w*|hear|heard|learn\w*|find\s+out|found\s+out|came\s+across|discover\w*)|(sužinoj\w*|hear|heard|learn\w*).*(pirmą\s+kartą|first)", re.I)
_GENERIC = re.compile(r"\b(geresn\w*|patikim\w*|greit\w*|kokybišk\w*|patog\w*|pig\w*|puik\w*|efektyv\w*|paprast\w*|better|reliable|fast|quick|good|quality|easy|convenient|cheap|efficient|simple|improved|great)\b", re.I)
_SOLUTION = re.compile(r"\b(app|apps|programėl\w*|aplikacij\w*|funkcij\w*|features?|mygtuk\w*|buttons?|įrank\w*|tools?|platform\w*|sistem\w*|systems?|modul\w*|plugin\w*|chatbot\w*|widget\w*)\b", re.I)
_WORD = re.compile(r"[^\W\d_]+")
_STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "with", "and", "or", "ir", "su", "į", "iš", "ant", "per", "kad", "kai", "be", "iki", "nuo", "prie", "po", "apie"}
_COND = re.compile(r"^\s*(?P<ref>[^\W\d][\w.-]*)\s*(?:(?P<op>==|=|!=|<>|≠|\bnot\s+in\b|\bin\b|\bis\s+not\b|\bis\b|\byra\b|\bne\b)\s*(?P<val>.+?))?\s*$", re.I)


# --- helpers --------------------------------------------------------------------


def _finding(code: str, level: str, where: str | None, value: Any, note: str) -> dict[str, Any]:
    return {"code": code, "level": level, "where": where, "value": value, "note": note}


def _detect_language(text: str) -> str:
    return "lt" if _LT_MARK.search(text or "") else "en"


def _language(args: dict, *texts: str) -> str:
    given = str(args.get("language") or args.get("lang") or "").strip().lower()
    if given in {"lt", "en"}:
        return given
    if given:
        raise ToolError("language must be lt or en")
    return _detect_language(" ".join(t for t in texts if t))


def _flag(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"false", "0", "no", "off", "ne"}


def _list(value: Any, label: str) -> list:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    raise ToolError(f"{label} must be a list")


def _num(value: Any, label: str, *, minimum: float | None = None, maximum: float | None = None) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(str(value).replace(",", ".").replace("€", "").replace("%", "").strip())
    except ValueError as exc:
        raise ToolError(f"{label} must be a number, got {value!r}") from exc
    if math.isnan(number) or math.isinf(number):
        raise ToolError(f"{label} must be a finite number")
    if minimum is not None and number < minimum:
        raise ToolError(f"{label} must be at least {minimum:g}, got {number:g}")
    if maximum is not None and number > maximum:
        raise ToolError(f"{label} is {number:g}, above the scale maximum {maximum:g}")
    return number


def _counts(findings: list[dict[str, Any]]) -> dict[str, Any]:
    by_code: dict[str, int] = {}
    for f in findings:
        by_code[f["code"]] = by_code.get(f["code"], 0) + 1
    by_level = {level: sum(1 for f in findings if f["level"] == level) for level in ("block", "warn", "info")}
    return {"by_code": dict(sorted(by_code.items())), "by_level": by_level}


def _sort(findings: list[dict[str, Any]]) -> None:
    findings.sort(key=lambda f: LEVEL_ORDER.get(f["level"], 9))


def _strip_quoted(text: str) -> str:
    return _QUOTED.sub(" X ", text)


def _words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text or "")]


# --- survey_check: question parsing ---------------------------------------------


def _parse_questions(raw: Any) -> list[dict[str, Any]]:
    items = _list(raw, "questions")
    questions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ToolError(f"question {index} must be an object with id, text and type")
        qid = str(item.get("id") or f"q{index}").strip()
        if qid in seen:
            raise ToolError(f"question id {qid!r} is used twice")
        seen.add(qid)
        text = str(item.get("text") or "").strip()
        if not text:
            raise ToolError(f"question {qid} has no text")
        answer = item.get("answer") if isinstance(item.get("answer"), dict) else {}
        qtype = str(item.get("type") or answer.get("type") or "").strip().lower()
        if qtype not in QUESTION_TYPES:
            raise ToolError(f"question {qid}: unknown type {qtype!r}; use one of {', '.join(QUESTION_TYPES)}")
        options = [str(o).strip() for o in _list(item.get("options", answer.get("options")), f"question {qid} options") if str(o).strip()]
        scale = item.get("scale") if isinstance(item.get("scale"), dict) else (answer.get("scale") if isinstance(answer.get("scale"), dict) else None)
        if scale is not None:
            lo = _num(scale.get("min"), f"question {qid} scale.min")
            hi = _num(scale.get("max"), f"question {qid} scale.max")
            if lo is not None and hi is not None and lo >= hi:
                raise ToolError(f"question {qid}: scale.min must be below scale.max")
            scale = {"min": lo, "max": hi, "labels": [str(l) for l in _list(scale.get("labels"), f"question {qid} scale.labels")]}
        max_len = _num(item.get("max_len", answer.get("max_len")), f"question {qid} max_len", minimum=0)
        questions.append({
            "id": qid,
            "text": text,
            "type": qtype,
            "options": options,
            "scale": scale,
            "ask_if": str(item.get("ask_if") or "").strip() or None,
            "requires_experience": _flag(item.get("requires_experience")),
            "exclusive": _flag(item.get("exclusive")),
            "main_choice_followup": str(item.get("main_choice_followup") or "").strip() or None,
            "corroborated_by": str(item.get("corroborated_by") or "").strip() or None,
            "max_len": max_len,
            "kind": str(item.get("kind") or "").strip().lower() or None,
        })
    return questions


def _parse_screener(raw: Any) -> dict[str, Any] | None:
    if raw is None or raw == "":
        return None
    if not isinstance(raw, dict):
        raise ToolError("screener must be an object with question and options")
    text = str(raw.get("question") or raw.get("text") or "").strip()
    options = [str(o).strip() for o in _list(raw.get("options"), "screener options") if str(o).strip()]
    if not text or not options:
        raise ToolError("screener needs a question and at least one option")
    return {"id": str(raw.get("id") or "screener").strip(), "text": text, "options": options, "qualify": str(raw.get("qualify") or "").strip() or None}


def _parse_condition(raw: str) -> tuple[str, bool, list[str]] | None:
    m = _COND.match(raw or "")
    if not m:
        return None
    op = (m.group("op") or "").strip().lower()
    negated = op in {"!=", "<>", "≠", "not in", "is not", "ne"}
    values_raw = m.group("val") or ""
    values_raw = values_raw.strip().strip("[](){}")
    values = [v.strip().strip("\"'„“”") for v in re.split(r"\s*(?:,|\||;|\bor\b|\barba\b)\s*", values_raw) if v.strip().strip("\"'„“”")]
    return m.group("ref"), negated, values


def _is_motive(text: str) -> bool:
    return bool(_MOTIVE.search(text))


def _is_attitude(text: str) -> bool:
    return bool(_ATTITUDE.search(text))


def _is_behaviour(q: dict[str, Any]) -> bool:
    if q["kind"] in {"behaviour", "behavior", "recall_event"}:
        return True
    return bool(_BEHAVIOUR.search(q["text"]) or _PAST_EVENT.search(q["text"]))


def _double_barrelled(text: str) -> str | None:
    """Return a note when the text asks two things; None when it reads as one question."""
    if text.count("?") >= 2:
        return "two question marks: two questions in one item"
    bare = _strip_quoted(text)
    for m in _CONJ.finditer(bare):
        left, right = bare[: m.start()], bare[m.end():]
        if len(_words(left)) >= 3 and len(_words(right)) >= 3 and _CLAUSE_MARK.search(left) and _CLAUSE_MARK.search(right):
            return f"'{m.group(0).strip()}' joins two clauses; a respondent who agrees with one and not the other has no answer"
    return None


# --- survey_check: the audit ------------------------------------------------------


def _check_question(q: dict[str, Any], ctx: dict[str, Any], findings: list[dict[str, Any]]) -> None:
    qid, text, qtype = q["id"], q["text"], q["type"]
    jargon: list[str] = ctx["jargon"]

    note = _double_barrelled(text)
    if note:
        findings.append(_finding("double_barrelled", "block", qid, text, note + ". Split it into two questions."))

    m = _LEADING.search(text)
    if m:
        findings.append(_finding("leading", "block", qid, m.group(0), f"'{m.group(0)}' tells the respondent which answer is expected. Use neutral wording."))

    for m in _VAGUE.finditer(text):
        before = text[: m.start()]
        if _VAGUE_ASKING.search(before[-8:] + m.group(0)):
            continue  # "kaip dažnai" / "how often" is the asking, not the answer; the options define it
        has_numbered_answers = any(_HAS_NUMBER.search(o) for o in q["options"]) or bool(q["scale"] and any(_HAS_NUMBER.search(l) for l in q["scale"]["labels"]))
        if not has_numbered_answers:
            findings.append(_finding("vague_quantifier", "warn", qid, m.group(0), f"'{m.group(0)}' means a different frequency to every respondent. Give a number or a period (times per week, last 30 days)."))
            break
    for i, option in enumerate(q["options"]):
        if _VAGUE.search(option) and not _HAS_NUMBER.search(option):
            findings.append(_finding("vague_quantifier", "warn", f"{qid}.options[{i}]", option, f"Option '{option}' is a frequency word without a number; anchor it (e.g. '1–2 kartus per savaitę')."))
    if q["scale"]:
        for i, label in enumerate(q["scale"]["labels"]):
            if _VAGUE.search(label) and not _HAS_NUMBER.search(label):
                findings.append(_finding("vague_quantifier", "warn", f"{qid}.scale.labels[{i}]", label, f"Scale label '{label}' is a frequency word without a number; anchor it."))

    if qtype in {"single", "multi"}:
        if not q["options"]:
            findings.append(_finding("no_escape_option", "block", qid, None, f"A {qtype} question needs options, and one of them must be an escape ('kita', 'nežinau', 'other', 'don't know')."))
        elif not any(_ESCAPE.search(o) for o in q["options"]):
            findings.append(_finding("no_escape_option", "warn", qid, q["options"], "No 'other' / 'don't know' option: respondents who fit none are forced into a wrong answer."))

    if qtype == "scale":
        scale = q["scale"]
        if not scale or scale["min"] is None or scale["max"] is None:
            findings.append(_finding("scale_mismatch", "block", qid, scale, "A scale question needs scale.min and scale.max (and labels for the ends at least)."))
        else:
            points = int(scale["max"] - scale["min"]) + 1
            labels = scale["labels"]
            if labels and len(labels) not in (2, points):
                findings.append(_finding("scale_mismatch", "block", f"{qid}.scale", {"points": points, "labels": len(labels)}, f"{points} points but {len(labels)} labels; give one label per point or just the two end labels."))
            label_text = " ".join(labels)
            if _AGREE_WORDS.search(text) and _FREQ_LABEL.search(label_text) and not _AGREE_LABEL.search(label_text):
                findings.append(_finding("scale_mismatch", "block", f"{qid}.scale", labels, "Agreement wording answered on a frequency scale: the labels do not answer the question."))
            elif _FREQ_WORDS.search(text) and _AGREE_LABEL.search(label_text):
                findings.append(_finding("scale_mismatch", "block", f"{qid}.scale", labels, "Frequency wording answered on an agreement scale: the labels do not answer the question."))
    elif q["scale"] and qtype != "number":
        findings.append(_finding("scale_mismatch", "warn", qid, qtype, f"Question carries a scale but its type is {qtype}; use type scale or drop the scale."))

    hit = [term for term in jargon if re.search(r"(?<![\w])" + re.escape(term) + r"(?![\w])", text, re.I)]
    if hit:
        findings.append(_finding("jargon", "warn", qid, hit, f"Trade words a respondent may not know: {', '.join(hit)}. Say it in the respondent's words."))

    motive, attitude = _is_motive(text), _is_attitude(text)
    if motive:
        findings.append(_finding("motive_question", "info", qid, text, "Asks for a stated reason. Prefer what the respondent did; if kept, label answers as 'stated reasons' in the report."))
    if (motive or attitude) and q["kind"] not in {"behaviour", "behavior", "recall_event", "demographic"}:
        ref = q["corroborated_by"]
        target = ctx["by_id"].get(ref) if ref else None
        if target is None:
            why = f"corroborated_by '{ref}' is not a question in this survey" if ref else "no corroborated_by"
            findings.append(_finding("uncorroborated", "warn", qid, ref, f"Motive or attitude question with {why}. Pair it with a behaviour question (what they did, how many times, when) and set corroborated_by."))
        elif not _is_behaviour(target):
            findings.append(_finding("uncorroborated", "warn", qid, ref, f"corroborated_by '{ref}' is not a behaviour question; point it at one that asks what the respondent did."))

    if _PAST_EVENT.search(text) and not _TIME_FRAME.search(text):
        findings.append(_finding("recall_event", "warn", qid, text, "Asks about a past event with no time frame. Add one ('per pastaruosius 3 mėnesius', 'in the last 30 days') so every respondent recalls the same window."))

    frequency_options = q["options"] and all(_VAGUE.search(o) or _HAS_NUMBER.search(o) for o in q["options"])
    if qtype == "single" and not q["exclusive"] and len(q["options"]) >= 2 and _USAGE_VERB.search(text) and not frequency_options:
        findings.append(_finding("single_where_multi", "warn", qid, q["options"], "Respondents plausibly use or buy several of these; a single choice forces one. Make it multi, add 'other', and ask separately which is the main one."))

    if qtype == "open" and q["max_len"] is not None and q["max_len"] < SHORT_OPEN_FIELD and (_LIST_EXPECTED.search(text) or _USAGE_VERB.search(text)):
        findings.append(_finding("open_field_short", "warn", qid, q["max_len"], f"Open field of {q['max_len']:g} characters for a question that expects a list; respondents will stop at one item. Allow at least {SHORT_OPEN_FIELD} or use multi-select."))

    if qtype == "multi":
        ref = q["main_choice_followup"]
        if not ref:
            findings.append(_finding("multi_no_primary", "warn", qid, None, "Multi-select with no follow-up asking which one is the main choice; set main_choice_followup."))
        elif ref not in ctx["by_id"]:
            findings.append(_finding("multi_no_primary", "warn", qid, ref, f"main_choice_followup '{ref}' is not a question in this survey."))

    if q["requires_experience"] and not q["ask_if"]:
        findings.append(_finding("unrouted_experience_question", "block", qid, None, "Needs experience (a purchase, a visit) but has no ask_if: respondents without it will answer anyway. Route it from the screener."))


def _check_routing(questions: list[dict[str, Any]], screener: dict[str, Any] | None, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {q["id"]: q for q in questions}
    answers: dict[str, dict[str, Any]] = {q["id"]: q for q in questions}
    if screener:
        answers[screener["id"]] = {"type": "single", "options": screener["options"], "scale": None}
    common = [q["id"] for q in questions if not q["ask_if"]]
    conditional: dict[tuple[str, bool, tuple[str, ...]], list[str]] = {}
    for q in questions:
        if not q["ask_if"]:
            continue
        parsed = _parse_condition(q["ask_if"])
        if not parsed:
            findings.append(_finding("dead_end", "block", q["id"], q["ask_if"], "ask_if could not be read; write it as 'q1 = Taip' or 'screener in [A, B]'."))
            continue
        ref, negated, values = parsed
        if ref == q["id"]:
            findings.append(_finding("dead_end", "block", q["id"], q["ask_if"], "A question cannot be routed on its own answer."))
            continue
        target = answers.get(ref)
        if target is None:
            findings.append(_finding("dead_end", "block", q["id"], q["ask_if"], f"ask_if refers to '{ref}', which is not a question or the screener; the question is never shown."))
            continue
        if ref in by_id and questions.index(by_id[ref]) > questions.index(q):
            findings.append(_finding("dead_end", "block", q["id"], q["ask_if"], f"ask_if refers to '{ref}', which comes later in the survey; the answer does not exist yet."))
            continue
        bad: list[str] = []
        if target["type"] in {"single", "multi"} and values:
            lower = {o.lower() for o in target["options"]}
            bad = [v for v in values if v.lower() not in lower]
        elif target["type"] == "scale" and values and target["scale"] and target["scale"]["min"] is not None:
            for v in values:
                try:
                    n = float(v.replace(",", "."))
                except ValueError:
                    bad.append(v)
                    continue
                if not target["scale"]["min"] <= n <= target["scale"]["max"]:
                    bad.append(v)
        if bad:
            findings.append(_finding("dead_end", "block", q["id"], q["ask_if"], f"ask_if names answer(s) {bad} that '{ref}' does not offer; the question is never shown."))
            continue
        conditional.setdefault((ref, negated, tuple(values)), []).append(q["id"])

    paths: list[dict[str, Any]] = []
    if screener:
        sid = screener["id"]
        for option in screener["options"]:
            ids = list(common)
            for (ref, negated, values), qids in conditional.items():
                if ref != sid:
                    continue
                match = True if not values else (option.lower() in {v.lower() for v in values})
                if negated:
                    match = not match
                if match:
                    ids.extend(qids)
            ids = [q["id"] for q in questions if q["id"] in set(ids)]
            paths.append({"condition": f"{sid} = {option}", "question_ids": ids, "count": len(ids)})
            if len(ids) == len(common) and any(ref == sid for ref, _, _ in conditional):
                findings.append(_finding("no_questions_for_group", "info", sid, option, f"Screener answer '{option}' leads only to the questions everyone gets; if this group matters, give it questions, otherwise screen it out."))
        for (ref, negated, values), qids in conditional.items():
            if ref != sid:
                paths.append({"condition": by_id[qids[0]]["ask_if"], "question_ids": qids, "count": len(qids)})
    else:
        paths.append({"condition": "all respondents", "question_ids": common, "count": len(common)})
        for (ref, negated, values), qids in conditional.items():
            paths.append({"condition": by_id[qids[0]]["ask_if"], "question_ids": qids, "count": len(qids)})
    return paths


def _check_awareness(questions: list[dict[str, Any]], audience: str, findings: list[dict[str, Any]]) -> dict[str, bool] | None:
    corpus = " ".join([q["text"] for q in questions] + [audience])
    if not _AWARENESS.search(corpus):
        return None
    blocks = {"unaided_recall": False, "aided_familiarity": False, "ad_recall": False, "source_of_first_contact": False}
    for q in questions:
        t = q["text"]
        if q["type"] == "open" and _UNAIDED.search(t) and not _AD_WORD.search(t):
            blocks["unaided_recall"] = True
        if q["type"] in {"single", "multi", "scale"} and _AIDED.search(t) and not _AD_WORD.search(t):
            blocks["aided_familiarity"] = True
        if _AD_WORD.search(t) and _AD_MEMORY.search(t):
            blocks["ad_recall"] = True
        if _FIRST_CONTACT.search(t):
            blocks["source_of_first_contact"] = True
    missing = [name for name, present in blocks.items() if not present]
    if missing:
        findings.append(_finding(
            "familiarity_blocks_missing", "block", "survey", missing,
            "An awareness survey must measure separately: unaided recall (open, no list), aided familiarity (list of names), advertising recall "
            "and the claimed source of first contact. Missing: " + ", ".join(missing) + ". Recognising a name does not prove the ad was seen.",
        ))
    return blocks


def _check_protocol(raw: Any, findings: list[dict[str, Any]], missing: list[str]) -> dict[str, Any] | None:
    if raw is None or raw == "":
        return None
    if not isinstance(raw, dict):
        raise ToolError("protocol must be an object with arms, n_per_arm and metric")
    arms_raw = _list(raw.get("arms"), "protocol.arms")
    if not arms_raw:
        raise ToolError("protocol.arms is empty: name at least a blinded and a branded arm")
    arms: list[dict[str, Any]] = []
    for i, arm in enumerate(arms_raw):
        if not isinstance(arm, dict):
            raise ToolError(f"protocol.arms[{i}] must be an object with label, blinded, samples and order")
        label = str(arm.get("label") or f"arm {i + 1}").strip()
        order = str(arm.get("order") or "").strip().lower()
        samples = [str(s).strip() for s in _list(arm.get("samples"), f"protocol.arms[{i}].samples") if str(s).strip()]
        arms.append({"label": label, "blinded": _flag(arm.get("blinded")), "samples": samples, "order": order or None})
        if order not in {"randomised", "randomized", "counterbalanced"}:
            findings.append(_finding("order_not_randomised", "block", f"protocol.arms[{i}]", order or None, f"Arm '{label}' presents samples in a fixed order; the first sample tasted or seen scores differently. Randomise or counterbalance."))
    if not any(a["blinded"] for a in arms):
        findings.append(_finding("arm_not_blinded", "block", "protocol", [a["label"] for a in arms], "No blinded arm: the branded score alone cannot separate the product from the brand. Add an arm where the same samples are unlabelled."))
    if len(arms) < 2:
        findings.append(_finding("arm_not_blinded", "block", "protocol", [a["label"] for a in arms], "One arm only: a comparison needs a blinded and a branded arm with the same samples."))
    sample_sets = [set(a["samples"]) for a in arms if a["samples"]]
    if len(sample_sets) >= 2 and any(s != sample_sets[0] for s in sample_sets[1:]):
        findings.append(_finding("unequal_samples", "warn", "protocol", [sorted(s) for s in sample_sets], "Arms do not contain the same samples; the difference between arms then mixes product and context."))
    n_per_arm = _num(raw.get("n_per_arm"), "protocol.n_per_arm", minimum=1)
    if n_per_arm is None:
        missing.append("protocol.n_per_arm")
    metric = str(raw.get("metric") or "").strip()
    if not metric:
        missing.append("protocol.metric")
    blind_present = any(a["blinded"] for a in arms) and any(not a["blinded"] for a in arms)
    return {
        "arms": arms,
        "n_per_arm": int(n_per_arm) if n_per_arm is not None else None,
        "n_total": int(n_per_arm * len(arms)) if n_per_arm is not None else None,
        "metric": metric or None,
        "blind_arm_present": blind_present,
        "context_effect_rule": "branded_score − blind_score = context effect; report both scores and the difference with n, never the branded score alone." if blind_present else None,
    }


def survey_check(args: dict) -> dict[str, Any]:
    """Audit a questionnaire (and optionally a blind-comparison protocol) before it is fielded.

    ``questions``: ``[{id, text, type single|multi|scale|open|number, options?, scale? {min, max, labels?},
    ask_if?, requires_experience?, exclusive?, main_choice_followup?, corroborated_by?, max_len?}]``;
    ``screener?`` ``{question, options, id?}``; ``language`` lt|en (detected when absent); ``audience?``;
    ``jargon?`` (list of words that replace the default list); ``protocol?`` ``{arms: [{label, blinded,
    samples, order}], n_per_arm, metric}``. Returns findings per question, routing ``paths``, ``counts``
    by code and level, ``estimated_minutes``, a five-person ``pilot_plan`` and ``clean``.
    """
    args = args or {}
    questions = _parse_questions(args.get("questions"))
    screener = _parse_screener(args.get("screener"))
    if not questions and not args.get("protocol"):
        raise ToolError("questions is empty: write the questionnaire as JSON first; this tool does not invent questions")
    audience = str(args.get("audience") or "").strip()
    language = _language(args, *(q["text"] for q in questions), screener["text"] if screener else "", audience)
    jargon_raw = args.get("jargon")
    jargon = [str(j).strip() for j in _list(jargon_raw, "jargon") if str(j).strip()] if jargon_raw is not None else list(DEFAULT_JARGON)

    findings: list[dict[str, Any]] = []
    missing: list[str] = []
    warnings: list[str] = []
    ctx = {"by_id": {q["id"]: q for q in questions}, "jargon": jargon}
    for q in questions:
        _check_question(q, ctx, findings)
    paths = _check_routing(questions, screener, findings) if questions else []
    awareness = _check_awareness(questions, audience, findings) if questions else None
    protocol = _check_protocol(args.get("protocol"), findings, missing)

    minutes = round(sum(MINUTES_OPEN if q["type"] == "open" else MINUTES_CLOSED for q in questions) + (MINUTES_CLOSED if screener else 0), 2)
    if minutes > TOO_LONG_MINUTES:
        findings.append(_finding("too_long", "warn", "survey", minutes, f"About {minutes:g} minutes to complete (over {TOO_LONG_MINUTES:g}); drop-off rises sharply after that."))
    if questions and screener is None and any(q["requires_experience"] for q in questions):
        warnings.append("Questions need experience but there is no screener; routing relies on earlier questions only.")

    _sort(findings)
    counts = _counts(findings)
    clean = counts["by_level"]["block"] == 0 and counts["by_level"]["warn"] == 0
    n_q = len(questions)
    head = f"{n_q} question{'s' if n_q != 1 else ''}" + (" + screener" if screener else "") + (f", protocol with {len(protocol['arms'])} arms" if protocol else "")
    if clean:
        summary = f"{head}: no blocking or warning findings" + (f" ({counts['by_level']['info']} info)" if counts["by_level"]["info"] else "") + ". Pilot with five respondents before sending."
    else:
        codes = ", ".join(f"{code} {n}" for code, n in counts["by_code"].items())
        summary = f"{head}: {len(findings)} finding{'s' if len(findings) != 1 else ''} ({counts['by_level']['block']} block, {counts['by_level']['warn']} warn, {counts['by_level']['info']} info): {codes}."
    script = (
        ["Ką šis klausimas jums reiškia savais žodžiais?", "Kaip priėjote prie savo atsakymo?", "Ar trūko atsakymo varianto, kuris jums tiktų?", "Kuris žodis buvo neaiškus?"]
        if language == "lt"
        else ["What does this question mean to you, in your own words?", "How did you arrive at your answer?", "Was an answer you needed missing?", "Which word was unclear?"]
    )
    return {
        "language": language,
        "questions": n_q,
        "findings": findings,
        "paths": paths,
        "awareness_blocks": awareness,
        "protocol": protocol,
        "counts": counts,
        "clean": clean,
        "estimated_minutes": minutes,
        "pilot_plan": {
            "n": PILOT_N,
            "script": script,
            "stop_rule": "Do not send the full sample until all five pilot notes are back and every question that confused two or more pilots is rewritten and re-checked.",
        },
        "missing": missing,
        "warnings": warnings,
        "summary": summary,
    }


# --- outcome_scores -----------------------------------------------------------------


def _outcome_statement(o: dict[str, Any], language: str) -> str:
    direction, metric, obj, situation = o["direction"], o["metric"], o["object"] or "…", o["situation"]
    if language == "lt":
        verb = "sumažinti" if direction == "minimize" else "padidinti"
        noun = {"time": "laiką, per kurį", "likelihood": "tikimybę, kad", "number": "skaičių kartų, kai", "cost": "kainą, už kurią", "other": ""}[metric or "other"]
        text = " ".join(p for p in (verb, noun, obj) if p)
    else:
        verb = "minimize" if direction == "minimize" else "increase"
        noun = {"time": "the time it takes to", "likelihood": "the likelihood that", "number": "the number of times", "cost": "the cost of", "other": ""}[metric or "other"]
        text = " ".join(p for p in (verb, noun, obj) if p)
    return text + (f", {situation}" if situation else "")


def _check_statement(o: dict[str, Any], where: str, findings: list[dict[str, Any]]) -> None:
    for part in ("direction", "metric", "object"):
        if not o.get(part):
            findings.append(_finding("part_missing", "block", f"{where}.{part}", None, f"Outcome statement without {part}: write direction + metric + object (+ situation), e.g. 'minimize | time | re-pair the printer | after a Wi-Fi drop'."))
    if not o.get("situation"):
        findings.append(_finding("part_missing", "warn", f"{where}.situation", None, "No situation: without 'when/where' the outcome cannot be observed or prioritised."))
    obj = o.get("object") or ""
    generic = [m.group(0) for m in _GENERIC.finditer(obj)]
    if generic:
        rest = [w for w in _words(_GENERIC.sub(" ", obj)) if w not in _STOP]
        level = "block" if len(rest) < 2 else "warn"
        findings.append(_finding("outcome_vague", level, f"{where}.object", generic, f"Generic word(s) {generic} in the object; say what is measured ('time to…', 'number of…') and about what, not 'better/fast/reliable'."))
    sol = _SOLUTION.search(obj + " " + (o.get("situation") or ""))
    if sol:
        findings.append(_finding("solution_not_outcome", "warn", f"{where}.object", sol.group(0), f"'{sol.group(0)}' names a solution; an outcome describes the customer's result, not the feature that might deliver it."))


def _parse_outcome(item: Any, index: int, scale_max: float) -> dict[str, Any]:
    where = f"outcomes[{index}]"
    if not isinstance(item, dict):
        raise ToolError(f"{where} must be an object")
    direction = str(item.get("direction") or "").strip().lower().replace("minimise", "minimize")
    if direction and direction not in DIRECTIONS:
        raise ToolError(f"{where}: direction must be minimize or increase, got {direction!r}")
    metric = str(item.get("metric") or "").strip().lower()
    if metric and metric not in METRICS:
        raise ToolError(f"{where}: metric must be one of {', '.join(METRICS)}, got {metric!r}")
    obj = str(item.get("object") or item.get("name") or item.get("outcome") or "").strip()
    importance = _num(item.get("importance"), f"{where}.importance", minimum=0, maximum=scale_max)
    sat_raw = item.get("satisfaction")
    brands: dict[str, float | None] | None = None
    if isinstance(sat_raw, dict):
        brands = {str(k).strip(): _num(v, f"{where}.satisfaction[{k}]", minimum=0, maximum=scale_max) for k, v in sat_raw.items()}
        if not brands:
            raise ToolError(f"{where}: satisfaction map is empty")
    satisfaction = None if brands is not None else _num(sat_raw, f"{where}.satisfaction", minimum=0, maximum=scale_max)
    n_raw = item.get("n")
    n: Any = None
    if isinstance(n_raw, dict):
        n = {str(k).strip(): _num(v, f"{where}.n[{k}]", minimum=0) for k, v in n_raw.items()}
    elif n_raw not in (None, ""):
        n = _num(n_raw, f"{where}.n", minimum=0)
    return {
        "index": index,
        "direction": direction or None,
        "metric": metric or None,
        "object": obj or None,
        "situation": str(item.get("situation") or "").strip() or None,
        "importance": importance,
        "satisfaction": satisfaction,
        "brands": brands,
        "n": n,
        "cost_per_unit": _num(item.get("cost_per_unit"), f"{where}.cost_per_unit", minimum=0),
        "source_url": str(item.get("source_url") or "").strip() or None,
    }


def _n_for(o: dict[str, Any], brand: str | None) -> float | None:
    n = o["n"]
    if isinstance(n, dict):
        return n.get(brand) if brand else None
    return n


def _score_outcomes(args: dict) -> dict[str, Any]:
    outcomes_raw = _list(args.get("outcomes", args.get("rows")), "outcomes")
    if not outcomes_raw:
        raise ToolError("outcomes is empty: give at least one outcome statement with direction, metric, object and situation")
    scale_max = _num(args.get("scale_max"), "scale_max")
    scale_max = DEFAULT_SCALE_MAX if scale_max is None else scale_max
    if scale_max <= 0:
        raise ToolError("scale_max must be greater than zero")
    threshold_given = _num(args.get("threshold_underserved"), "threshold_underserved")
    threshold_base = DEFAULT_THRESHOLD if threshold_given is None else threshold_given
    threshold = threshold_base * scale_max / 10
    focus_brand = str(args.get("focus_brand") or "").strip() or None

    outcomes = [_parse_outcome(item, i, scale_max) for i, item in enumerate(outcomes_raw)]
    language = _language(args, *(f"{o['object'] or ''} {o['situation'] or ''}" for o in outcomes))
    findings: list[dict[str, Any]] = []
    missing: list[str] = []
    warnings: list[str] = []
    if threshold_given is None and scale_max != 10:
        warnings.append(f"threshold_underserved {DEFAULT_THRESHOLD:g} is defined on a 10-point scale; scaled to {threshold:g} for a {scale_max:g}-point scale.")
    elif threshold_given is not None and scale_max != 10:
        warnings.append(f"threshold_underserved {threshold_given:g} is read on a 10-point scale and scaled to {threshold:g} for a {scale_max:g}-point scale; pass scale_max 10 to use it as is.")

    rows: list[dict[str, Any]] = []
    seen_statements: dict[str, int] = {}
    for o in outcomes:
        where = f"outcomes[{o['index']}]"
        _check_statement(o, where, findings)
        statement = _outcome_statement(o, language)
        key = " ".join(_words(statement))
        if key in seen_statements:
            findings.append(_finding("outcome_duplicate", "warn", where, statement, f"Same outcome as outcomes[{seen_statements[key]}]; collapse them."))
        else:
            seen_statements[key] = o["index"]

        brand_used = None
        sat = o["satisfaction"]
        competitors: dict[str, float | None] = {}
        if o["brands"] is not None:
            brand_used = focus_brand if focus_brand in o["brands"] else next(iter(o["brands"]))
            if focus_brand and focus_brand not in o["brands"]:
                warnings.append(f"{where}: focus_brand '{focus_brand}' has no satisfaction score; '{brand_used}' is used as the focus.")
            sat = o["brands"][brand_used]
            competitors = {b: v for b, v in o["brands"].items() if b != brand_used}
        imp = o["importance"]
        row: dict[str, Any] = {
            "index": o["index"],
            "statement": statement,
            "direction": o["direction"],
            "metric": o["metric"],
            "object": o["object"],
            "situation": o["situation"],
            "importance": imp,
            "satisfaction": sat,
            "brand": brand_used,
            "n": _n_for(o, brand_used),
            "opportunity": None,
            "opportunity_10": None,
            "class": None,
            "cost_per_unit": o["cost_per_unit"],
        }
        if imp is None:
            missing.append(f"{where}: importance")
        if sat is None:
            missing.append(f"{where}: satisfaction" + (f" for {brand_used}" if brand_used else ""))
        if imp is not None and sat is not None:
            opp = imp + max(imp - sat, 0.0)
            row["opportunity"] = round(opp, 2)
            row["opportunity_10"] = round(opp * 10 / scale_max, 2)
            if sat > imp:
                row["class"] = "overserved"
            elif opp >= threshold:
                row["class"] = "underserved"
            else:
                row["class"] = "appropriately served"
            small = {b: v for b, v in o["n"].items() if v is not None and v < COMPARABLE_N} if isinstance(o["n"], dict) else ({brand_used or "all": o["n"]} if o["n"] is not None and o["n"] < COMPARABLE_N else {})
            for brand, n_small in small.items():
                warnings.append(f"{where}: n = {n_small:g} for {brand} (under {COMPARABLE_N}); the score is indicative only.")
        if competitors:
            scored = {b: v for b, v in competitors.items() if v is not None}
            row["competitors"] = competitors
            if scored and sat is not None:
                best_brand = max(scored, key=lambda b: scored[b])
                best = scored[best_brand]
                n_focus, n_best = _n_for(o, brand_used), _n_for(o, best_brand)
                comparable = n_focus is not None and n_best is not None and n_focus >= COMPARABLE_N and n_best >= COMPARABLE_N
                row["best_competitor"] = best_brand
                row["gap_vs_best_competitor"] = round(sat - best, 2)
                row["comparable"] = comparable
                if not comparable:
                    reason = "n is not given for both brands" if n_focus is None or n_best is None else f"n under {COMPARABLE_N} for at least one brand"
                    row["comparable_note"] = f"Not comparable yet: {reason}. Report the gap as a hint, not a result."
            else:
                row["gap_vs_best_competitor"] = None
                row["comparable"] = False
                row["comparable_note"] = "No competitor score given; nothing is estimated for them."
        rows.append(row)

    scored_rows = [r for r in rows if r["opportunity"] is not None]
    top = [
        {"index": r["index"], "object": r["object"], "opportunity": r["opportunity"], "class": r["class"], "importance": r["importance"], "satisfaction": r["satisfaction"]}
        for r in sorted(scored_rows, key=lambda r: (-r["opportunity"], r["index"]))
    ]
    excess = [
        {
            "index": r["index"],
            "object": r["object"],
            "importance": r["importance"],
            "satisfaction": r["satisfaction"],
            "cost_per_unit": r["cost_per_unit"],
            "note": f"Satisfaction {r['satisfaction']:g} exceeds importance {r['importance']:g}: this performance costs {r['cost_per_unit']:g} per unit and buyers do not ask for it. "
                    "Potential saving per unit if removed; size it as a test with experiment_plan, do not decide from the score.",
        }
        for r in scored_rows if r["class"] == "overserved" and r["cost_per_unit"] is not None
    ]
    ties = {}
    for r in scored_rows:
        ties.setdefault(r["opportunity"], []).append(r["index"])
    for value, idx in ties.items():
        if len(idx) > 1:
            warnings.append(f"Tie at opportunity {value:g} between outcomes {idx}; rank by importance or sample before choosing.")

    _sort(findings)
    counts = _counts(findings)
    classes = {c: sum(1 for r in scored_rows if r["class"] == c) for c in ("underserved", "appropriately served", "overserved")}
    bits = [f"{len(outcomes)} outcome{'s' if len(outcomes) != 1 else ''}"]
    if scored_rows:
        bits.append(f"{len(scored_rows)} scored on a {scale_max:g}-point scale: {classes['underserved']} underserved (≥ {threshold:g}), {classes['appropriately served']} appropriately served, {classes['overserved']} overserved")
        bits.append(f"top: '{top[0]['object']}' at {top[0]['opportunity']:g}")
    if counts["by_level"]["block"]:
        bits.append(f"{counts['by_level']['block']} statement(s) need rewriting")
    if missing:
        bits.append(f"{len(missing)} value(s) missing")
    return {
        "mode": "outcomes",
        "language": language,
        "scale_max": scale_max,
        "threshold_underserved": threshold,
        "focus_brand": focus_brand,
        "formula": "opportunity = importance + max(importance − satisfaction, 0); overserved when satisfaction > importance",
        "outcomes": rows,
        "top": top,
        "excess": excess,
        "classes": classes,
        "findings": findings,
        "counts": counts,
        "missing": missing,
        "warnings": warnings,
        "summary": "; ".join(bits) + ".",
    }


def _job_map(args: dict) -> dict[str, Any]:
    raw = args.get("job_map") if isinstance(args.get("job_map"), dict) else args
    job = str(raw.get("job") or "").strip()
    executor = str(raw.get("executor") or "").strip()
    steps_raw = _list(raw.get("steps"), "steps")
    if not steps_raw:
        raise ToolError("steps is empty: list the job's steps with phase, title, outcomes and today")
    findings: list[dict[str, Any]] = []
    missing: list[str] = []
    if not job:
        missing.append("job")
    if not executor:
        missing.append("executor")
    language = _language(args, job, *(str(s.get("title") or "") for s in steps_raw if isinstance(s, dict)))

    steps: list[dict[str, Any]] = []
    for i, item in enumerate(steps_raw):
        where = f"steps[{i}]"
        if not isinstance(item, dict):
            raise ToolError(f"{where} must be an object")
        phase = str(item.get("phase") or "").strip().lower()
        if phase not in PHASES:
            raise ToolError(f"{where}: phase must be one of {', '.join(PHASES)}, got {phase!r}")
        today = str(item.get("today") or "unknown").strip().lower()
        if today not in TODAY_STATES:
            raise ToolError(f"{where}: today must be one of {', '.join(TODAY_STATES)}, got {today!r}")
        title = str(item.get("title") or "").strip() or f"step {i + 1}"
        outcomes_raw = _list(item.get("outcomes"), f"{where}.outcomes")
        outcomes: list[dict[str, Any]] = []
        for j, o in enumerate(outcomes_raw):
            if isinstance(o, str):
                o = {"object": o}
            parsed = _parse_outcome(o, j, DEFAULT_SCALE_MAX)
            _check_statement(parsed, f"{where}.outcomes[{j}]", findings)
            outcomes.append({"statement": _outcome_statement(parsed, language), **{k: parsed[k] for k in ("direction", "metric", "object", "situation")}})
        if not outcomes:
            findings.append(_finding("step_no_outcome", "warn", where, title, "Step carries no outcome statement; without one nothing says what 'done well' means here."))
        evidence = str(item.get("evidence_url") or "").strip() or None
        if today != "unknown" and not evidence:
            findings.append(_finding("step_unsourced", "warn", where, today, f"'{title}' is marked {today} today with no evidence_url; name the interview, ticket or observation that shows it."))
        if today in {"repeated", "skipped", "out_of_order"}:
            findings.append(_finding(f"step_{today}", "info", where, title, f"'{title}' is {today.replace('_', ' ')} today: a gap in the job, and a candidate for the offer."))
        steps.append({"index": i, "phase": phase, "title": title, "outcomes": outcomes, "today": today, "evidence_url": evidence})

    present = {s["phase"] for s in steps}
    missing_phases = [p for p in PHASES if p not in present]
    if missing_phases:
        findings.append(_finding("phase_missing", "warn", "job_map", missing_phases, f"Phases with no step: {', '.join(missing_phases)}. Every job has all eight; a missing phase is a step nobody has looked at yet."))
    order = [PHASES.index(s["phase"]) for s in steps]
    if order != sorted(order):
        out_of_order = [steps[i]["title"] for i in range(1, len(order)) if order[i] < order[i - 1]]
        findings.append(_finding("phase_order", "warn", "job_map", out_of_order, "Steps are not listed in phase order (define → … → conclude); reorder so the map reads as the job unfolds."))
    gaps = [s["title"] for s in steps if s["today"] in {"repeated", "skipped", "out_of_order"}]
    no_gap_found = not gaps
    _sort(findings)
    counts = _counts(findings)
    summary = (
        f"Job map '{job or '?'}' for {executor or '?'}: {len(steps)} steps, {len(present)}/8 phases covered"
        + (f", missing {', '.join(missing_phases)}" if missing_phases else "")
        + (f"; gaps today: {', '.join(gaps)}" if gaps else "; no gap found — the map is a hypothesis until a step is shown repeated, skipped or out of order")
        + "."
    )
    return {
        "mode": "job_map",
        "language": language,
        "job": job or None,
        "executor": executor or None,
        "steps": steps,
        "phases_covered": f"{len(present)}/8",
        "missing_phases": missing_phases,
        "gaps": gaps,
        "no_gap_found": no_gap_found,
        "findings": findings,
        "counts": counts,
        "missing": missing,
        "warnings": [],
        "summary": summary,
    }


def outcome_scores(args: dict) -> dict[str, Any]:
    """Validate outcome statements and score them, or (``mode: job_map`` / ``steps``) check a job map.

    Outcomes: ``outcomes: [{direction minimize|increase, metric time|likelihood|number|cost|other, object,
    situation?, importance?, satisfaction? (number or {brand: number}), n? (number or {brand: n}),
    cost_per_unit?}]``, ``scale_max`` (default 10), ``focus_brand?``, ``threshold_underserved`` (default 12,
    read on a 10-point scale and scaled with ``scale_max``). Job map: ``job``, ``executor``, ``steps: [{phase,
    title, outcomes, today ok|repeated|skipped|out_of_order|unknown, evidence_url?}]``.
    """
    args = args or {}
    mode = str(args.get("mode") or "").strip().lower()
    if mode == "job_map" or (not mode and (isinstance(args.get("job_map"), dict) or args.get("steps") is not None)):
        return _job_map(args)
    if mode and mode not in {"outcomes", "scores", "score"}:
        raise ToolError("mode must be outcomes or job_map")
    return _score_outcomes(args)


# --- research_plan --------------------------------------------------------------------


def _margin_pp(n: int, size: int | None) -> float:
    base = 1.96 * math.sqrt(0.25 / n)
    if size and size > n:
        base *= math.sqrt((size - n) / (size - 1))
    return round(base * 100, 1)


def _population(args: dict, findings: list[dict[str, Any]], missing: list[str]) -> tuple[dict[str, Any], int | None]:
    raw = args.get("population") if isinstance(args.get("population"), dict) else {}
    size = _num(raw.get("size"), "population.size", minimum=1)
    population = {"description": str(raw.get("description") or "").strip() or None, "size": int(size) if size else None, "reach": str(raw.get("reach") or "").strip() or None}
    if not population["description"]:
        missing.append("population.description")
    if population["size"] is None:
        findings.append(_finding("population_unknown", "info", "population.size", None, "Population size not given; the margin of error is computed without finite-population correction and the quota cannot be checked against reach."))
    if not population["reach"]:
        missing.append("population.reach (how these people will be contacted)")
    sample_raw = _num(args.get("sample_n"), "sample_n", minimum=1)
    sample_n = int(sample_raw) if sample_raw else None
    if sample_n is None:
        missing.append("sample_n")
    elif population["size"] and sample_n > population["size"]:
        findings.append(_finding("sample_exceeds_population", "block", "sample_n", sample_n, f"sample_n {sample_n} is larger than the population {population['size']}."))
    return population, sample_n


def _criteria_findings(args: dict, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    criteria: list[dict[str, Any]] = []
    for i, item in enumerate(_list(args.get("criteria"), "criteria")):
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict):
            raise ToolError(f"criteria[{i}] must be an object with text, source and n_mentions")
        text = str(item.get("text") or "").strip()
        if not text:
            raise ToolError(f"criteria[{i}] has no text")
        source_raw = item.get("source")
        sources = [str(s).strip() for s in source_raw] if isinstance(source_raw, (list, tuple)) else ([str(source_raw).strip()] if source_raw not in (None, "") else [])
        n_mentions = _num(item.get("n_mentions"), f"criteria[{i}].n_mentions", minimum=0)
        where = f"criteria[{i}]"
        single_source_kind = len(sources) == 1 and sources[0].lower() in {"focus_group", "focus group", "interview", "quote", "anecdote", "fokus grupė", "interviu"}
        if (n_mentions is not None and n_mentions <= 1) or (n_mentions is None and single_source_kind):
            findings.append(_finding("single_voice_criterion", "warn", where, {"text": text, "source": sources, "n_mentions": n_mentions}, "One voice said this. It is a survey question, not a headline or an angle, until the frequency question below shows how many buyers share it."))
        elif n_mentions is None:
            findings.append(_finding("single_voice_criterion", "info", where, {"text": text, "source": sources}, "n_mentions not given; count how many people raised it before using it in copy."))
        elif n_mentions < 5:
            findings.append(_finding("few_mentions", "info", where, n_mentions, f"{n_mentions:g} mentions; fewer than five is still a hypothesis to test in the survey."))
        criteria.append({"index": i, "text": text, "source": sources, "n_mentions": n_mentions})
    return criteria


def _survey_questions(criteria: list[dict[str, Any]], product: str, screener: dict[str, Any] | None, language: str) -> list[dict[str, Any]]:
    route = None
    if screener:
        qualify = screener["qualify"] if screener["qualify"] in screener["options"] else screener["options"][0]
        route = f"{screener['id']} = {qualify}"
    questions: list[dict[str, Any]] = []
    for c in criteria:
        if language == "lt":
            text = f"Pagalvokite apie paskutinius 5 kartus, kai pirkote {product}. Kiek kartų iš jų „{c['text']}“ lėmė, ką pasirinkote?"
            labels = ["Nė karto", "1 kartą", "2 kartus", "3 kartus", "4–5 kartus"]
        else:
            text = f"Think of the last 5 times you bought {product}. On how many of them did “{c['text']}” decide what you chose?"
            labels = ["None of them", "1 time", "2 times", "3 times", "4–5 times"]
        questions.append({"id": f"c{c['index'] + 1}", "text": text, "type": "scale", "scale": {"min": 1, "max": 5, "labels": labels}, "ask_if": route, "requires_experience": True, "criterion": c["text"]})
    questions.append({
        "id": "open_other",
        "text": "Kas dar lėmė jūsų pasirinkimą, ko čia nepaklausėme?" if language == "lt" else "What else shaped your choice that we did not ask about?",
        "type": "open",
        "ask_if": route,
        "requires_experience": True,
    })
    return questions


def _concept_instrument(concepts: list[dict[str, Any]], language: str, horizon: str) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    for i, c in enumerate(concepts, start=1):
        price = c["price"]
        if language == "lt":
            price_text = f"už {price:g} €" if price is not None else "už nurodytą kainą"
            benefit = f"Savais žodžiais: ką „{c['name']}“ jums duotų?"
            use = "Kurioje situacijoje tai naudotumėte? Aprašykite vieną konkretų atvejį."
            intent = f"Kiek tikėtina, kad per {horizon} pirktumėte „{c['name']}“ {price_text}?"
            labels = ["Tikrai nepirkčiau", "Greičiausiai nepirkčiau", "Nežinau", "Greičiausiai pirkčiau", "Tikrai pirkčiau"]
        else:
            price_text = f"at €{price:g}" if price is not None else "at the stated price"
            benefit = f"In your own words, what would “{c['name']}” do for you?"
            use = "In what situation would you use it? Describe one specific case."
            intent = f"How likely are you to buy “{c['name']}” {price_text} within {horizon}?"
            labels = ["Definitely would not buy", "Probably would not buy", "Not sure", "Probably would buy", "Definitely would buy"]
        questions.append({"id": f"c{i}_benefit", "text": benefit, "type": "open", "concept": c["name"], "measures": "comprehension"})
        questions.append({"id": f"c{i}_use", "text": use, "type": "open", "concept": c["name"], "measures": "use_situation"})
        questions.append({"id": f"c{i}_intent", "text": intent, "type": "scale", "scale": {"min": 1, "max": 5, "labels": labels}, "concept": c["name"], "measures": "purchase_intent"})
    return questions


def _concept_test(args: dict, language: str, population: dict[str, Any], sample_n: int | None, findings: list[dict[str, Any]], missing: list[str], warnings: list[str]) -> dict[str, Any]:
    concepts: list[dict[str, Any]] = []
    for i, item in enumerate(_list(args.get("concepts"), "concepts")):
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            raise ToolError(f"concepts[{i}] must be an object with name, price and is_lead_user")
        name = str(item.get("name") or "").strip()
        if not name:
            raise ToolError(f"concepts[{i}] has no name")
        price = _num(item.get("price", item.get("price_eur")), f"concepts[{i}].price", minimum=0)
        if price is None:
            warnings.append(f"concepts[{i}] '{name}': no price; intent is asked 'at the stated price', which respondents read differently.")
        concepts.append({"name": name, "price": price, "is_lead_user": _flag(item.get("is_lead_user"))})
    if not concepts:
        raise ToolError("concepts is empty: a concept test needs at least two concepts, the lead-user idea and an alternative the buyer already knows")
    if len(concepts) < 2:
        findings.append(_finding("single_concept", "block", "concepts", [c["name"] for c in concepts], "One concept only: preference needs an alternative the typical buyer already knows next to the new idea."))
    if all(c["is_lead_user"] for c in concepts):
        findings.append(_finding("lead_user_only", "block", "concepts", [c["name"] for c in concepts], "Every concept is a lead-user idea; add the ordinary alternative typical buyers use today, or the test cannot say whether the lead users are ahead or just different."))
    follow_up = args.get("follow_up") if isinstance(args.get("follow_up"), dict) else None
    if not follow_up or not (follow_up.get("launch_date") or follow_up.get("metric")):
        findings.append(_finding("no_follow_up", "warn", "follow_up", follow_up, "No follow_up {launch_date, metric}: stated intent is only worth what later sales show; fix now what will be compared against it."))

    measures = args.get("measures") if isinstance(args.get("measures"), dict) else {}
    design = str(measures.get("design") or args.get("design") or "monadic").strip().lower()
    if design not in {"monadic", "sequential"}:
        raise ToolError("design must be monadic (each respondent sees one concept) or sequential (each sees all)")
    quota: dict[str, Any] = {"design": design, "per_concept": None, "remainder": 0}
    if sample_n is not None:
        if design == "monadic":
            quota["per_concept"], quota["remainder"] = divmod(sample_n, len(concepts))
        else:
            quota["per_concept"] = sample_n
    quota["concepts"] = [{"name": c["name"], "n": quota["per_concept"]} for c in concepts]

    baseline = _num(args.get("baseline_top_two_box_pct"), "baseline_top_two_box_pct")
    baseline_default = baseline is None
    baseline = DEFAULT_TOP_TWO_BOX_PCT if baseline is None else baseline
    effect = _num(args.get("minimum_effect"), "minimum_effect")
    effect_kind = str(args.get("effect_kind") or "absolute").strip().lower()
    sample_check: dict[str, Any] = {
        "baseline_top_two_box_pct": baseline,
        "baseline_is_default": baseline_default,
        "minimum_effect": effect,
        "effect_kind": effect_kind,
        "needed_per_concept": None,
        "quota_per_concept": quota["per_concept"],
        "sufficient": None,
        "method": None,
    }
    if effect is None:
        missing.append("minimum_effect (the smallest top-two-box difference between concepts worth acting on, in percentage points)")
        sample_check["note"] = "Sample sufficiency cannot be judged without minimum_effect."
    else:
        plan = experiment_plan({"baseline_rate": baseline, "minimum_effect": effect, "effect_kind": effect_kind, "variants": len(concepts)})
        sample_check["needed_per_concept"] = plan["sample_per_variant"]
        sample_check["method"] = plan["method"] + f"; baseline {baseline:g} % top-two-box" + (" (default, state your own)" if baseline_default else "")
        if quota["per_concept"] is not None:
            sample_check["sufficient"] = quota["per_concept"] >= plan["sample_per_variant"]
            if not sample_check["sufficient"]:
                findings.append(_finding("sample_too_small", "block", "sample_n", {"per_concept": quota["per_concept"], "needed": plan["sample_per_variant"]}, f"{quota['per_concept']} per concept cannot detect a {effect:g} {'pp' if effect_kind == 'absolute' else '%'} difference from {baseline:g} % top-two-box; {plan['sample_per_variant']} per concept are needed."))

    horizon = str(args.get("horizon") or ("3 mėnesius" if language == "lt" else "3 months")).strip()
    instrument = _concept_instrument(concepts, language, horizon)
    if measures.get("preference") in {"rank", "choose_one"} and len(concepts) >= 2:
        if language == "lt":
            text = "Jei galėtumėte rinktis tik vieną iš parodytų variantų, kurį pasirinktumėte?" if measures.get("preference") == "choose_one" else "Išdėstykite parodytus variantus nuo labiausiai iki mažiausiai tinkamo jums."
        else:
            text = "If you could pick only one of the options shown, which would you pick?" if measures.get("preference") == "choose_one" else "Rank the options shown from the one that suits you most to the one that suits you least."
        instrument.append({"id": "preference", "text": text, "type": "single", "exclusive": True, "options": [c["name"] for c in concepts] + (["Nė vieno"] if language == "lt" else ["None of them"]), "measures": "preference"})
        if design == "monadic":
            warnings.append("A preference question needs every respondent to see all concepts; switch design to sequential or drop it.")
    min_comp = _num(args.get("min_comprehension"), "min_comprehension", minimum=0, maximum=1)
    t2b_min = _num(args.get("top_two_box_min"), "top_two_box_min", minimum=0, maximum=1)
    decision_rule = {
        "min_comprehension": DEFAULT_MIN_COMPREHENSION if min_comp is None else min_comp,
        "top_two_box_min": DEFAULT_TOP_TWO_BOX_MIN if t2b_min is None else t2b_min,
        "defaults_used": [k for k, v in (("min_comprehension", min_comp), ("top_two_box_min", t2b_min)) if v is None],
        "text": (
            f"Code every open benefit answer: a concept passes comprehension when at least {(DEFAULT_MIN_COMPREHENSION if min_comp is None else min_comp) * 100:.0f} % name the intended benefit. "
            f"Among those who understood, it passes intent when at least {(DEFAULT_TOP_TWO_BOX_MIN if t2b_min is None else t2b_min) * 100:.0f} % answer 4 or 5. "
            "Compare concepts on top-two-box with the sample fixed in advance; a concept that fails comprehension is rewritten, not relaunched with more respondents."
        ),
    }
    check = survey_check({"questions": instrument, "language": language})
    return {
        "concepts": concepts,
        "quota": quota,
        "sample_check": sample_check,
        "instrument": instrument,
        "instrument_check": {"findings": check["findings"], "clean": check["clean"], "estimated_minutes": check["estimated_minutes"]},
        "decision_rule": decision_rule,
        "follow_up": follow_up,
    }


def _survey_kind(args: dict, language: str, population: dict[str, Any], sample_n: int | None, findings: list[dict[str, Any]], missing: list[str], warnings: list[str]) -> dict[str, Any]:
    criteria = _criteria_findings(args, findings)
    if not criteria:
        raise ToolError("criteria is empty: list the buying criteria the focus group or reviews named, each with source and n_mentions")
    screener = _parse_screener(args.get("screener"))
    if screener is None:
        findings.append(_finding("no_screener", "warn", "screener", None, "No screener: buyers and non-buyers will answer the same experience questions. Add one ('Ar per pastaruosius 3 mėnesius pirkote …?') and route on it."))
    product = str(args.get("product") or population.get("description") or ("šį produktą" if language == "lt" else "this product")).strip()
    questions = _survey_questions(criteria, product, screener, language)
    extra_raw = _list(args.get("questions"), "questions")
    if extra_raw:
        questions.extend(_parse_questions(extra_raw))
        for q in questions[len(criteria) + 1:]:
            q.pop("kind", None)
    quota = {"sample_n": sample_n, "minimum": SURVEY_MIN_N, "sufficient": None if sample_n is None else sample_n >= SURVEY_MIN_N, "note": f"At least {SURVEY_MIN_N} qualified respondents (those routed past the screener), so a criterion named by 10 % of buyers is seen at least ten times."}
    if sample_n is not None and sample_n < SURVEY_MIN_N:
        findings.append(_finding("sample_too_small", "warn", "sample_n", sample_n, f"{sample_n} respondents is under the {SURVEY_MIN_N} minimum for frequency questions; shares will swing by more than ±10 pp."))
    check_args: dict[str, Any] = {"questions": questions, "language": language}
    if screener:
        check_args["screener"] = {"id": screener["id"], "question": screener["text"], "options": screener["options"]}
    check = survey_check(check_args)
    return {
        "criteria": criteria,
        "screener": screener,
        "questions": questions,
        "quota": quota,
        "instrument_check": {"findings": check["findings"], "clean": check["clean"], "estimated_minutes": check["estimated_minutes"]},
        "paths": check["paths"],
    }


def research_plan(args: dict) -> dict[str, Any]:
    """Design a concept test or a criteria survey that a marketer can field; never runs it.

    ``kind`` concept_test|survey; ``population {description, size, reach}``; ``sample_n``; ``language`` lt|en.
    concept_test: ``concepts [{name, price, is_lead_user}]``, ``minimum_effect`` (pp of top-two-box),
    ``baseline_top_two_box_pct`` (default 30), ``measures {preference, design}``, ``follow_up {launch_date, metric}``.
    survey: ``criteria [{text, source, n_mentions}]``, ``screener {question, options, qualify?}``, ``product``,
    extra ``questions``. The generated instrument is run through ``survey_check`` and its findings returned.
    """
    args = args or {}
    kind = str(args.get("kind") or "").strip().lower()
    if kind not in {"concept_test", "survey"}:
        raise ToolError("kind must be concept_test or survey")
    findings: list[dict[str, Any]] = []
    missing: list[str] = []
    warnings: list[str] = []
    population, sample_n = _population(args, findings, missing)
    texts = [population.get("description") or "", str(args.get("product") or "")]
    texts += [str(c.get("name") if isinstance(c, dict) else c) for c in _list(args.get("concepts"), "concepts")]
    texts += [str(c.get("text") if isinstance(c, dict) else c) for c in _list(args.get("criteria"), "criteria")]
    language = _language(args, *texts)

    out: dict[str, Any] = {"kind": kind, "language": language, "population": population, "sample_n": sample_n}
    if sample_n is not None:
        moe = _margin_pp(sample_n, population["size"])
        out["sample_note"] = {
            "margin_of_error_pp_95": moe,
            "finite_population_correction": population["size"] is not None,
            "text": f"n = {sample_n}" + (f" of {population['size']:,}" if population["size"] else "") + f": a 50 % share is measured to ±{moe:g} pp at 95 % confidence (standard formula for one proportion).",
        }
    else:
        out["sample_note"] = None
    if kind == "concept_test":
        out.update(_concept_test(args, language, population, sample_n, findings, missing, warnings))
    else:
        out.update(_survey_kind(args, language, population, sample_n, findings, missing, warnings))

    _sort(findings)
    counts = _counts(findings)
    instrument_clean = out["instrument_check"]["clean"]
    n_items = len(out.get("instrument") or out.get("questions") or [])
    if kind == "concept_test":
        quota = out["quota"]
        head = f"Concept test of {len(out['concepts'])} concepts, {quota['design']}" + (f", {quota['per_concept']} per concept" if quota["per_concept"] is not None else "")
        check = out["sample_check"]
        if check["sufficient"] is True:
            head += f" (enough for a {check['minimum_effect']:g} {'pp' if check['effect_kind'] == 'absolute' else '%'} difference)"
        elif check["sufficient"] is False:
            head += f" (needs {check['needed_per_concept']} per concept)"
        else:
            head += " (sufficiency unknown: minimum_effect missing)"
    else:
        head = f"Survey of {len(out['criteria'])} criteria, {n_items} questions" + (" routed by a screener" if out["screener"] else ", no screener") + (f", n = {sample_n}" if sample_n else "")
    bits = [head, f"instrument {'passes' if instrument_clean else 'fails'} survey_check"]
    if counts["by_level"]["block"] or counts["by_level"]["warn"]:
        bits.append(f"{counts['by_level']['block']} block, {counts['by_level']['warn']} warn: " + ", ".join(f"{c} {n}" for c, n in counts["by_code"].items()))
    if missing:
        bits.append(f"missing: {'; '.join(missing)}")
    out.update({"findings": findings, "counts": counts, "missing": missing, "warnings": warnings, "summary": "; ".join(bits) + "."})
    return out


# --- tool wrappers -------------------------------------------------------------------------


def survey_check_tool(args: dict) -> str:
    """Backs the worker-facing ``survey_check`` tool: JSON the worker can quote from."""
    return json.dumps(survey_check(args or {}), ensure_ascii=False, indent=1)


def outcome_scores_tool(args: dict) -> str:
    """Backs the worker-facing ``outcome_scores`` tool: JSON the worker can quote from."""
    return json.dumps(outcome_scores(args or {}), ensure_ascii=False, indent=1)


def research_plan_tool(args: dict) -> str:
    """Backs the worker-facing ``research_plan`` tool: JSON the worker can quote from."""
    return json.dumps(research_plan(args or {}), ensure_ascii=False, indent=1)
