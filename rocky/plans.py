"""Structured marketing plans checked by code before they reach the marketer.

A plan is a list of items with named slots; a worker fills the slots and this
module says which slot is empty, contradicted or unsupported. One entry point,
``plan_check(tools, args, llm=None)``, dispatches on ``kind``:

* ``content_calendar``: items with date, topic, format, owner, goal and source;
  weeks without items, items without an owner or a goal, channel clashes, stage
  gaps, formats the audience evidence does not support, lifespan dates, the
  launch window, an inventory of existing content with freshness and overlap,
  and a check against stated weekly capacity.
* ``pr_board``: a press release, announcement, sponsorship pack or message house;
  news hook, attributed quote, boilerplate of 100 words or fewer, contact,
  date or embargo, byline with its disclosure, a fact desk with a source per
  figure (``claim_check`` when sources are given), partners with sourced goals,
  affected groups in their own words, reader reasons per publication, and a
  message house of one core sentence, at most three evidenced pillars and
  channel variants that keep the core.
* ``demo_board``: a demonstration built around one completed task: audience,
  the one claim with a number, the act the visitor performs (not watches), what
  they judge by, the takeaway, timing and materials; feature tours, late first
  product and unknown room reasons for live sessions.
* ``response_board``: an escalation matrix with owners, minutes, EUR
  thresholds, a sensitive tier that never answers in public, and drills that
  reach the escalation; coverage and an HTML-free summary table.
* ``partner_board``: partner tiers with obligations both ways, support items
  with the obstacle each removes and a ship signal, exclusivity terms, and a
  candidate shortlist judged by sourced investment signals, size mix and
  bundle conflicts.
* ``qualification``: a sales sheet (need, means, access, timing, decision
  process) with evidence or a discovery question per slot and disqualifiers,
  or a recruit screener whose questions discriminate between segments and
  whose routes have proof.
* ``evidence_log``: appends sourced observations to ``runs/notes/evidence.jsonl``
  inside the workspace and audits a secondary source's measure, population and
  period against what the decision needs.
* ``community_rules``: a rules document with what is allowed, what is removed,
  who decides, the appeal and the response time, examples per rule and a
  moderator table.
* ``retell`` and ``ad_pretest``: cold-reader tests through an injected ``llm``
  (``complete(model=, messages=)`` returning ``.content``): a persona who has
  never seen the brief repeats the statement or names the product and benefit;
  code scores which ``must_survive`` terms survived. Without ``llm`` they return
  the prompts the worker should run and ``missing: ["llm"]``.

Every kind returns ``findings`` ``{code, level, where, value, note}``, ``missing``,
``counts``, ``clean`` and a ``summary``; everything but the two reader tests is
deterministic and works for Lithuanian and English.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import date, datetime, timedelta
from typing import Any

from rocky.claims import claim_check
from rocky.pageinspect import content_words, detect_language, same_stem
from rocky.tools import ToolError, WorkspaceTools

KINDS = ("content_calendar", "pr_board", "demo_board", "response_board", "partner_board", "qualification", "evidence_log", "community_rules", "retell", "ad_pretest")
LEVEL_ORDER = {"block": 0, "warn": 1, "info": 2}
DEFAULT_MODEL = "google/gemini-3.8-flash"
DEFAULT_READERS = 3
MAX_READERS = 5
STAGES = ("awareness", "evaluation", "decision", "retention")
STAGE_FORMATS = {
    "awareness": {"short_video", "article", "post", "podcast", "infographic", "guide", "teaser", "reel", "story", "newsletter"},
    "evaluation": {"comparison", "case_study", "webinar", "demo", "faq", "article", "guide", "calculator", "checklist", "whitepaper", "newsletter"},
    "decision": {"case_study", "demo", "pricing", "trial", "faq", "testimonial", "offer", "comparison", "webinar"},
    "retention": {"newsletter", "webinar", "guide", "faq", "release_notes", "community", "case_study", "tutorial"},
}
LIFESPANS = ("evergreen", "timely")
LAUNCH_WINDOW_DAYS = 3
REVIEW_MONTHS = 12
STALE_MONTHS = 12
BOILERPLATE_MAX_WORDS = 100
MAX_PILLARS = 3
CORE_KEEP_SHARE = 0.6
DUPLICATE_SHARE = 0.8
LATE_PRODUCT_SHARE = 0.2
PRETALK_MIN = 3
RESPONSE_MAX_MIN = 1440
PARTNER_MIN_ROWS = 5
SMALL_MID_SHARE = 0.3
SALES_SLOTS = ("need", "means", "access", "timing", "decision_process")
SLOT_STATUSES = ("confirmed", "uncertain", "absent")
MAX_SCREENER_QUESTIONS = 5
QUESTION_MAX_CHARS = 160
EVIDENCE_KINDS = ("modification", "quote", "finding", "source_audit")
EVIDENCE_CATEGORIES = ("new_use", "refinement")
EVIDENCE_PATH = "runs/notes/evidence.jsonl"
PERIOD_SLACK_YEARS = 2
GLANCE_CHARS = 125
TBD = re.compile(r"^\s*(tbd|tba|\?+|-+|n/?a|role tbd|owner tbd|nežinoma|neaišku|unknown|none)\s*$", re.I)
_URL = re.compile(r"https?://[^\s)>\]\"']+", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?:\+370|8)\s?\d{2,3}\s?\d{2,3}\s?\d{2,3}|\+\d{7,14}")
_DATE_TEXT = re.compile(r"\b(?:19|20)\d\d[-./](?:0?[1-9]|1[0-2])[-./](?:0?[1-9]|[12]\d|3[01])\b|\b(?:0?[1-9]|[12]\d|3[01])[./](?:0?[1-9]|1[0-2])[./](?:19|20)\d\d\b|\b(?:19|20)\d\d\s*m\.\s*\w+\s*\d{1,2}\s*d\.|\b\d{1,2}\s+(?:january|february|march|april|may|june|july|august|september|october|november|december)(?:\s+(?:19|20)\d\d)?\b|\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}\b|\b(?:sausio|vasario|kovo|balandžio|gegužės|birželio|liepos|rugpjūčio|rugsėjo|spalio|lapkričio|gruodžio)\s+\d{1,2}\s*(?:d\.)?", re.I)
_EMBARGO = re.compile(r"embargo|embargas|skelbti\s+nuo|for\s+immediate\s+release|skelbiama\s+iš\s+karto|release\s+date", re.I)
_QUOTE = re.compile(r"[„“\"]([^“”\"]{12,}?)[“”\"]")
_ROLE_WORD = re.compile(r"\b(vadov\w*|direktor\w*|įkūrėj\w*|steigėj\w*|CEO|CTO|CFO|COO|CMO|vadybinink\w*|specialist\w*|analitik\w*|ekspert\w*|profesor\w*|gydytoj\w*|treneri\w*|head|chief|founder|director|manager|officer|lead|president|partner|owner|savinink\w*|atstov\w*|spokesperson|buhalter\w*|inžinieri\w*|engineer|researcher|tyrėj\w*)\b", re.I)
_NAME = re.compile(r"\b[A-ZĄČĘĖĮŠŲŪŽ][a-ząčęėįšųūž]+(?:\s+[A-ZĄČĘĖĮŠŲŪŽ][a-ząčęėįšųūž]+){1,2}\b")
_BOILER_HEAD = re.compile(r"^\s*(?:#+\s*)?(?:apie\s+\S+|about\s+\S+|boilerplate|apie\s+įmonę|apie\s+mus)\s*:?\s*$", re.I | re.M)
_CONTACT_HEAD = re.compile(r"kontakt\w*|contact\w*|daugiau\s+informacijos|press\s+office|spaudos\s+tarnyb\w*|media\s+(?:contact|inquiries)", re.I)
_READER_WORD = re.compile(r"skaitytoj\w*|readers?|auditorij\w*|audience", re.I)
_NUMBER = re.compile(r"(?<![\w/])(\d{1,3}(?:[  ]\d{3})+|\d+(?:[.,]\d+)?)(?:\s?(?:%|€|eur|proc\.?|mln\.?|tūkst\.?|k\b|m\b))?", re.I)
_PASSIVE_ACT = re.compile(r"\b(watch\w*|see\w*|listen\w*|observe\w*|look\w*|views?|is shown|are shown|we show|rep shows|presenter shows|demonstrat\w*|walk\w*\s+through|explain\w*|tell\w*|present\w*|žiūri|stebi|klauso|mato|matys|rodom\w*|parodom\w*|parodys\w*|pristatom\w*|paaiškin\w*|pasakoj\w*|demonstruoj\w*)\b", re.I)
_VISITOR_ACTOR = re.compile(r"visitor|lankytoj\w*|customer|klient\w*|prospect|attendee|dalyv\w*|buyer|pirkėj\w*|user|vartotoj\w*|guest|sveči\w*|accountant|buhalter\w*", re.I)
_SENSITIVE = re.compile(r"legal|lawsuit|sue|court|injur\w*|safety|health|medical|discriminat\w*|harass\w*|data\s+(?:breach|leak)|gdpr|privacy|press|media|journalist|teism\w*|teisin\w*|advokat\w*|sužalo\w*|sveikat\w*|saug\w*|diskriminac\w*|priekabiav\w*|duomen\w*|žiniasklaid\w*|žurnalist\w*|jautr\w*|sensitive", re.I)
_YES = re.compile(r"\b(yes|taip|would|galbūt|maybe|probably|tikrai)\b", re.I)
_NO = re.compile(r"\b(no|ne|nebūčiau|wouldn't|would not|not really|nelabai)\b", re.I)
_DONT_KNOW = re.compile(r"nežinau|neaišku|negirdėj\w*|nesuprant\w*|don'?t know|do not know|not sure|unsure|no idea|unfamiliar|not familiar|never heard|unclear", re.I)
_VISITS_METRIC = re.compile(r"sessions?|visits?|apsilank\w*|traffic|srautas|lankytoj\w*", re.I)
_SECTION_WORDS = {
    "allowed": re.compile(r"leidžiam\w*|galima|allowed|permitted|what\s+is\s+(?:fine|allowed|ok)|kas\s+leidžiama", re.I),
    "removed": re.compile(r"šalinam\w*|trinam\w*|pašalin\w*|removed?|deleted?|taken\s+down|not\s+allowed|draudžiam\w*|kas\s+trinama", re.I),
    "decides": re.compile(r"sprendži\w*|kas\s+sprendžia|moderator\w*|who\s+decides|decider|admin\w*", re.I),
    "appeal": re.compile(r"apeliac\w*|apskųs\w*|skund\w*|appeal\w*|dispute", re.I),
    "response_time": re.compile(r"atsakym\w*\s+laik\w*|per\s+\d+\s*(?:val|h|dien|d\.)|response\s+time|within\s+\d+\s*(?:h|hours?|days?)|\d+\s*(?:val\.|valand\w*|hours?)\b", re.I),
}
_RULE_HEAD = re.compile(r"^\s*#{2,4}\s*(?:taisykl\w*|rule)\b.*$|^\s*(?:\d+[.)]|[-*])\s*\*\*.+?\*\*", re.I | re.M)
_ALLOWED_LINE = re.compile(r"^\s*[-*]?\s*\**(?:leidžiama|allowed|ok|gerai)\**\s*:", re.I | re.M)
_REMOVED_LINE = re.compile(r"^\s*[-*]?\s*\**(?:šalinama|trinama|removed|deleted|not allowed)\**\s*:", re.I | re.M)
_TABLE_ROW = re.compile(r"^\s*\|.+\|\s*$", re.M)
_MOD_HEAD = re.compile(r"^\s*#{2,4}\s*(?:moderator\w*|moderavim\w*|sprendim\w*).*$", re.I | re.M)
_WORD = re.compile(r"[^\W\d_]+|\d+(?:[.,]\d+)?", re.UNICODE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-ZĄČĘĖĮŠŲŪŽ0-9„\"])")
_ARTEFACT = re.compile(r"invoice|sąskait\w*|report|ataskait\w*|export\w*|eksport\w*|sent|išsiųst\w*|dashboard|pdf|file|fail\w*|email|laišk\w*|order|užsakym\w*|paid|apmokėt\w*|published|paskelbt\w*|screen|ekran\w*|list|sąraš\w*|chart|grafik\w*|table|lentel\w*|status\w*|būsen\w*", re.I)


# --- helpers -----------------------------------------------------------------------------------


def _finding(code: str, level: str, where: str | None, value: Any, note: str) -> dict[str, Any]:
    return {"code": code, "level": level, "where": where, "value": value, "note": note}


def _language(args: dict, *texts: Any) -> str:
    given = str(args.get("language") or args.get("lang") or "").strip().lower()
    if given in {"lt", "en"}:
        return given
    if given:
        raise ToolError("language must be lt or en")
    return detect_language(" ".join(str(t) for t in texts if t)) or "en"


def _list(value: Any, label: str) -> list:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    raise ToolError(f"{label} must be a list")


def _dicts(value: Any, label: str) -> list[dict[str, Any]]:
    out = []
    for index, item in enumerate(_list(value, label), start=1):
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict):
            raise ToolError(f"{label}[{index}] must be an object")
        out.append(item)
    return out


def _num(value: Any, label: str, *, minimum: float | None = None) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(str(value).replace("€", "").replace("%", "").replace(" ", "").replace(" ", "").replace(",", ".").strip())
    except ValueError as exc:
        raise ToolError(f"{label} must be a number, got {value!r}") from exc
    if minimum is not None and number < minimum:
        raise ToolError(f"{label} must be at least {minimum:g}, got {number:g}")
    return number


def _flag(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"false", "0", "no", "off", "ne"}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _blank(value: Any) -> bool:
    text = _text(value)
    return not text or bool(TBD.match(text))


def _date(value: Any, label: str) -> date | None:
    text = _text(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%d.%m.%Y", "%Y/%m/%d", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m"):
        try:
            return datetime.strptime(text[:19] if "T" in fmt or " " in fmt else text, fmt).date()
        except ValueError:
            continue
    raise ToolError(f"{label} must be an ISO date (YYYY-MM-DD), got {value!r}")


def _today(args: dict) -> date:
    return _date(args.get("today"), "today") or date.today()


def _is_url(value: Any) -> bool:
    text = _text(value)
    return bool(_URL.fullmatch(text)) or (text.lower().startswith(("http://", "https://")) and " " not in text)


def _words(text: str) -> list[str]:
    return [w.casefold() for w in _WORD.findall(str(text or ""))]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(_text(text)) if s.strip()]


def _term_tokens(term: str) -> list[str]:
    tokens = _words(term)
    content = [t for t in tokens if len(t) >= 3 or t.isdigit()]
    return content or tokens


def _term_in(term: str, text: str) -> bool:
    """Every content token of ``term`` has a same-stem word in ``text`` (numbers must match exactly)."""
    words = _words(text)
    if not words:
        return False
    for token in _term_tokens(term):
        if token.replace(",", ".").replace(".", "", 1).isdigit():
            if token not in words:
                return False
        elif not any(same_stem(token, w) or (len(token) >= 3 and w.startswith(token)) for w in words):
            return False
    return True


def _share_kept(source: str, text: str) -> float:
    words = content_words(source)
    if not words:
        return 1.0 if _text(source) in _text(text) else 0.0
    kept = [w for w in words if any(same_stem(w, t) for t in _words(text))]
    return round(len(kept) / len(words), 2)


def _similar(a: str, b: str) -> float:
    wa, wb = set(content_words(a)), set(content_words(b))
    if not wa or not wb:
        return 1.0 if _text(a).casefold() == _text(b).casefold() and _text(a) else 0.0
    return round(len(wa & wb) / len(wa | wb), 2)


def _urls_in_workspace(tools: WorkspaceTools | None, paths: tuple[str, ...] = ("runs/notes/facts.md", "runs/notes/research.md")) -> tuple[set[str], list[str]]:
    urls: set[str] = set()
    read: list[str] = []
    if tools is None:
        return urls, read
    for rel in paths:
        try:
            path = tools.resolve(rel)
        except ToolError:
            continue
        if path.is_file():
            read.append(rel)
            urls.update(u.rstrip(".,;") for u in _URL.findall(path.read_text(encoding="utf-8", errors="replace")))
    return urls, read


def _url_known(url: str, known: set[str]) -> bool:
    url = _text(url).rstrip("/").casefold()
    return any(url == k.rstrip("/").casefold() for k in known)


def _read_draft(tools: WorkspaceTools | None, args: dict, text_key: str = "draft", path_key: str = "draft_path") -> str:
    text = _text(args.get(text_key) or args.get("text"))
    rel = _text(args.get(path_key) or args.get("path"))
    if text:
        return text
    if rel:
        if tools is None:
            raise ToolError(f"{path_key} needs a workspace")
        path = tools.resolve(rel)
        if not path.is_file():
            raise ToolError(f"{path_key} not found: {rel}")
        return path.read_text(encoding="utf-8", errors="replace")
    return ""


def _finish(kind: str, out: dict[str, Any], findings: list[dict[str, Any]], missing: list[str], head: str, clean_note: str = "") -> dict[str, Any]:
    findings.sort(key=lambda f: LEVEL_ORDER.get(f["level"], 9))
    by_code: dict[str, int] = {}
    for f in findings:
        by_code[f["code"]] = by_code.get(f["code"], 0) + 1
    by_level = {level: sum(1 for f in findings if f["level"] == level) for level in ("block", "warn", "info")}
    clean = by_level["block"] == 0 and by_level["warn"] == 0
    if clean:
        summary = f"{head}: no blocking or warning findings" + (f" ({by_level['info']} info)" if by_level["info"] else "") + "."
        if clean_note:
            summary += " " + clean_note
    else:
        codes = ", ".join(f"{code} {n}" for code, n in sorted(by_code.items()))
        summary = f"{head}: {len(findings)} finding{'s' if len(findings) != 1 else ''} ({by_level['block']} block, {by_level['warn']} warn, {by_level['info']} info): {codes}."
    if missing:
        summary += " Missing: " + "; ".join(missing) + "."
    out.update({"kind": kind, "findings": findings, "counts": {"by_code": dict(sorted(by_code.items())), "by_level": by_level}, "clean": clean, "missing": missing, "summary": summary})
    return out


# --- content_calendar ----------------------------------------------------------------------------


def _calendar_item(raw: dict, index: int) -> dict[str, Any]:
    where = f"items[{index}]"
    title = _text(raw.get("title") or raw.get("topic") or raw.get("piece"))
    publish = _date(raw.get("publish_date") or raw.get("date"), f"{where}.publish_date")
    stage = _text(raw.get("stage")).lower() or None
    if stage and stage not in STAGES:
        raise ToolError(f"{where}.stage must be one of {', '.join(STAGES)}, got {stage!r}")
    lifespan = _text(raw.get("lifespan")).lower() or None
    if lifespan and lifespan not in LIFESPANS:
        raise ToolError(f"{where}.lifespan must be evergreen or timely, got {lifespan!r}")
    return {
        "id": _text(raw.get("id")) or f"item-{index}",
        "where": where,
        "title": title,
        "publish_date": publish,
        "channel": _text(raw.get("channel")),
        "format": _text(raw.get("format")).lower().replace(" ", "_").replace("-", "_"),
        "owner": _text(raw.get("owner")),
        "goal": _text(raw.get("goal") or raw.get("question_answered") or raw.get("objective")),
        "stage": stage,
        "source": _text(raw.get("source") or raw.get("source_url")),
        "lifespan": lifespan,
        "review_date": _date(raw.get("review_date"), f"{where}.review_date"),
        "expires_on": _date(raw.get("expires_on"), f"{where}.expires_on"),
        "event_date": _date(raw.get("event_date"), f"{where}.event_date"),
        "hours": _num(raw.get("hours"), f"{where}.hours", minimum=0),
    }


def _week(d: date) -> str:
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def _content_calendar(tools: WorkspaceTools | None, args: dict) -> dict[str, Any]:
    items = [_calendar_item(r, i) for i, r in enumerate(_dicts(args.get("items"), "items"), start=1)]
    if not items:
        raise ToolError("items is empty: write the plan as JSON first; this tool does not invent content")
    findings: list[dict[str, Any]] = []
    missing: list[str] = []
    language = _language(args, *(i["title"] + " " + i["goal"] for i in items))
    launch = _date(args.get("launch_date"), "launch_date")
    today = _today(args)
    audience = args.get("audience_evidence")
    supported: dict[str, str] = {}
    for row in _dicts(audience, "audience_evidence"):
        fmt = _text(row.get("format") or row.get("text")).lower().replace(" ", "_").replace("-", "_")
        if fmt:
            supported[fmt] = _text(row.get("evidence") or row.get("source"))
    if audience is None:
        missing.append("audience_evidence: no formats the audience is known to use; format support not judged")

    by_week: dict[str, list[str]] = {}
    seen_slot: dict[tuple[date, str], list[str]] = {}
    for it in items:
        w = it["where"]
        if not it["title"]:
            findings.append(_finding("title_missing", "block", w, None, "The item has no topic or title."))
        if it["publish_date"] is None:
            findings.append(_finding("date_missing", "block", w, None, f"{it['title'] or it['id']}: no publish_date; a plan without dates is a wish list."))
        else:
            by_week.setdefault(_week(it["publish_date"]), []).append(it["id"])
            if it["channel"]:
                seen_slot.setdefault((it["publish_date"], it["channel"].casefold()), []).append(it["id"])
        if _blank(it["owner"]):
            findings.append(_finding("owner_missing", "block", w, it["owner"] or None, f"{it['title'] or it['id']}: no owner (TBD counts as none); nobody publishes an unowned piece."))
        if not it["goal"] and not it["stage"]:
            findings.append(_finding("goal_missing", "warn", w, None, f"{it['title'] or it['id']}: no goal, stage or question answered; say what the piece is for."))
        if not it["format"]:
            findings.append(_finding("format_missing", "warn", w, None, f"{it['title'] or it['id']}: no format."))
        elif supported and it["format"] not in supported and not any(same_stem(it["format"], s) for s in supported):
            findings.append(_finding("format_unsupported", "warn", w, it["format"], f"{it['title'] or it['id']}: format {it['format']} has no audience evidence (known: {', '.join(sorted(supported))})."))
        if it["stage"] and it["format"] and it["format"] not in STAGE_FORMATS[it["stage"]]:
            findings.append(_finding("format_fit", "info", w, it["format"], f"{it['format']} is unusual for the {it['stage']} stage (fits: {', '.join(sorted(STAGE_FORMATS[it['stage']]))})."))
        if not it["source"]:
            findings.append(_finding("source_missing", "info", w, None, f"{it['title'] or it['id']}: no source for the topic (research line, VOC quote or brief)."))
        if it["lifespan"] == "evergreen" and it["review_date"] is None:
            proposed = (it["publish_date"] + timedelta(days=30 * REVIEW_MONTHS)).isoformat() if it["publish_date"] else None
            it["review_date_proposed"] = proposed
            findings.append(_finding("review_missing", "warn", w, proposed, f"Evergreen piece without a review_date; proposed {proposed or 'publish_date + 12 months'} (proposed, not set)."))
        if it["lifespan"] == "timely" and it["expires_on"] is None and it["event_date"] is None:
            findings.append(_finding("expiry_missing", "block", w, None, f"{it['title'] or it['id']}: timely piece without expires_on or event_date."))
        if it["expires_on"] and it["publish_date"] and it["expires_on"] < it["publish_date"]:
            findings.append(_finding("expires_before_publish", "block", w, it["expires_on"].isoformat(), "expires_on is earlier than publish_date."))

    for (d, channel), ids in seen_slot.items():
        if len(ids) > 1:
            findings.append(_finding("channel_clash", "warn", ",".join(ids), f"{d.isoformat()} {channel}", f"{d.isoformat()} {channel}: {len(ids)} items on the same day and channel."))

    dated = sorted(i["publish_date"] for i in items if i["publish_date"])
    weeks_empty: list[str] = []
    if len(dated) >= 2:
        cursor = dated[0] - timedelta(days=dated[0].weekday())
        while cursor <= dated[-1]:
            wk = _week(cursor)
            if wk not in by_week:
                weeks_empty.append(wk)
            cursor += timedelta(days=7)
        if weeks_empty:
            findings.append(_finding("week_gap", "warn", "plan", weeks_empty, f"{len(weeks_empty)} week{'s' if len(weeks_empty) != 1 else ''} without an item between {dated[0].isoformat()} and {dated[-1].isoformat()}: {', '.join(weeks_empty)}."))

    stages_present = {i["stage"] for i in items if i["stage"]}
    counts_by_stage = {s: sum(1 for i in items if i["stage"] == s) for s in STAGES}
    if stages_present and len(stages_present) < 2:
        absent = [s for s in STAGES if s not in stages_present]
        note = (f"Nėra turinio etapams: {', '.join(absent)}; kuo atsakysite lyginantiems ir sprendžiantiems?" if language == "lt" else f"No content for stages {', '.join(absent)}; what do shortlisted buyers get?")
        findings.append(_finding("stage_gap", "warn", "plan", absent, note))
    if "evaluation" in stages_present and not _list(args.get("competitors"), "competitors") and "competitors" not in args:
        findings.append(_finding("unknown_rival", "info", "plan", None, "Evaluation items but no competitors named; name the rivals the comparison answers."))

    launch_window: list[str] = []
    if launch:
        window = [i for i in items if i["publish_date"] and abs((i["publish_date"] - launch).days) <= LAUNCH_WINDOW_DAYS]
        launch_window = [i["id"] for i in window]
        unassigned = [i["id"] for i in window if _blank(i["owner"])]
        if len(window) > 2 or unassigned:
            findings.append(_finding("launch_window_crowded", "warn", "launch_window", launch_window, f"{len(window)} items within ±{LAUNCH_WINDOW_DAYS} days of launch {launch.isoformat()}" + (f", unassigned: {', '.join(unassigned)}" if unassigned else "") + "."))
    elif "launch_date" not in args:
        missing.append("launch_date: not given; launch window not checked")

    capacity = args.get("capacity") if isinstance(args.get("capacity"), dict) else {}
    cap_items = _num(capacity.get("items_per_week"), "capacity.items_per_week", minimum=0) if capacity else None
    cap_hours = _num(capacity.get("hours_per_week"), "capacity.hours_per_week", minimum=0) if capacity else None
    capacity_out: dict[str, Any] = {"items_per_week": cap_items, "hours_per_week": cap_hours, "weeks_over": []}
    if cap_items is not None or cap_hours is not None:
        for wk, ids in sorted(by_week.items()):
            hours = sum(i["hours"] or 0 for i in items if i["id"] in ids)
            over_items = cap_items is not None and len(ids) > cap_items
            over_hours = cap_hours is not None and hours > cap_hours
            if over_items or over_hours:
                capacity_out["weeks_over"].append({"week": wk, "items": len(ids), "hours": hours})
                findings.append(_finding("over_capacity", "warn", wk, {"items": len(ids), "hours": hours}, f"{wk}: {len(ids)} items / {hours:g} h against capacity {cap_items if cap_items is not None else '-'} items / {cap_hours if cap_hours is not None else '-'} h per week."))
    else:
        missing.append("capacity: items_per_week or hours_per_week not given; plan not checked against capacity")

    inventory_rows = _dicts(args.get("inventory"), "inventory")
    inventory: list[dict[str, Any]] = []
    for index, row in enumerate(inventory_rows, start=1):
        title = _text(row.get("title") or row.get("topic") or row.get("text"))
        updated = _date(row.get("updated") or row.get("published") or row.get("publish_date"), f"inventory[{index}].updated")
        age_months = round((today - updated).days / 30.4, 1) if updated else None
        entry = {"title": title, "url": _text(row.get("url") or row.get("path")), "updated": updated.isoformat() if updated else None, "age_months": age_months, "overlaps": []}
        if age_months is None:
            findings.append(_finding("date_unknown", "info", f"inventory[{index}]", None, f"{title}: no publish or update date; freshness unknown."))
        elif age_months > STALE_MONTHS:
            findings.append(_finding("stale", "warn", f"inventory[{index}]", age_months, f"{title}: last updated {age_months:g} months ago; refresh or expire before planning a near-duplicate."))
        for it in items:
            if it["title"] and title and _similar(it["title"], title) >= 0.5:
                entry["overlaps"].append(it["id"])
                findings.append(_finding("overlap", "warn", it["where"], title, f"{it['title']} overlaps existing {title}" + (f" ({entry['url']})" if entry["url"] else "") + "; update that piece or make the new one answer a different question."))
        inventory.append(entry)
    if "inventory" not in args:
        missing.append("inventory: existing content not listed; overlap and freshness not checked")

    maintain = sorted((i for i in items if i["lifespan"] == "evergreen"), key=lambda i: i["review_date"] or i["publish_date"] or date.max)
    expire = [i for i in items if i["lifespan"] == "timely" and ((i["event_date"] and i["event_date"] < today) or (i["expires_on"] and i["expires_on"] < today))]
    out = {
        "language": language,
        "items": [{**i, "publish_date": i["publish_date"].isoformat() if i["publish_date"] else None, "review_date": i["review_date"].isoformat() if i["review_date"] else None, "expires_on": i["expires_on"].isoformat() if i["expires_on"] else None, "event_date": i["event_date"].isoformat() if i["event_date"] else None} for i in items],
        "by_week": dict(sorted(by_week.items())),
        "weeks_empty": weeks_empty,
        "counts_by_stage": counts_by_stage,
        "counts_by_lifespan": {s: sum(1 for i in items if i["lifespan"] == s) for s in LIFESPANS},
        "launch_window": launch_window,
        "capacity": capacity_out,
        "inventory": inventory,
        "maintain": [i["id"] for i in maintain],
        "expire": [i["id"] for i in expire],
    }
    head = f"{len(items)} items over {len(by_week)} week{'s' if len(by_week) != 1 else ''}"
    return _finish("content_calendar", out, findings, missing, head)


# --- pr_board ----------------------------------------------------------------------------------------


def _pr_parts(draft: str, parts: dict) -> dict[str, Any]:
    found: dict[str, Any] = {}
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", draft) if p.strip()]
    hook = _text(parts.get("news_hook") or parts.get("hook") or parts.get("lead"))
    found["news_hook"] = hook or (paragraphs[0] if paragraphs else "")
    quote = parts.get("quote")
    if isinstance(quote, dict):
        found["quote"] = {"text": _text(quote.get("text")), "name": _text(quote.get("name") or quote.get("who")), "role": _text(quote.get("role"))}
    else:
        m = _QUOTE.search(draft)
        if m:
            after = draft[m.end(): m.end() + 200]
            before = draft[max(0, m.start() - 200): m.start()]
            name = _NAME.search(after) or _NAME.search(before)
            role = _ROLE_WORD.search(after) or _ROLE_WORD.search(before)
            found["quote"] = {"text": m.group(1), "name": name.group(0) if name else "", "role": role.group(0) if role else ""}
        else:
            found["quote"] = None
    boiler = _text(parts.get("boilerplate"))
    if not boiler:
        m = _BOILER_HEAD.search(draft)
        if m:
            rest = draft[m.end():].strip()
            boiler = re.split(r"\n\s*\n", rest)[0] if rest else ""
    found["boilerplate"] = boiler
    contact = _text(parts.get("contact"))
    if not contact:
        m = _EMAIL.search(draft) or _PHONE.search(draft)
        contact = m.group(0) if (m and _CONTACT_HEAD.search(draft)) else (m.group(0) if m else "")
    found["contact"] = contact
    when = _text(parts.get("embargo") or parts.get("date") or parts.get("release_date"))
    if not when:
        m = _EMBARGO.search(draft) or _DATE_TEXT.search(draft)
        if m:
            line_start = draft.rfind("\n", 0, m.start()) + 1
            line_end = draft.find("\n", m.end())
            when = draft[line_start: line_end if line_end >= 0 else len(draft)].strip()
    found["date"] = when
    return found


def _fact_desk(draft: str, facts: list[dict], findings: list[dict], language: str) -> dict[str, Any]:
    figures: list[dict[str, Any]] = []
    seen: set[str] = set()
    text_wo_urls = _DATE_TEXT.sub(" ", _PHONE.sub(" ", _URL.sub(" ", _EMAIL.sub(" ", draft))))
    for m in _NUMBER.finditer(text_wo_urls):
        token = m.group(0).strip()
        bare = m.group(1).replace(" ", "").replace(" ", "")
        if bare in seen or (len(bare) <= 1 and not m.group(0).strip().endswith(("%", "€"))):
            continue
        if re.fullmatch(r"(19|20)\d\d", bare) and not m.group(0).strip().endswith(("%", "€")):
            continue
        seen.add(bare)
        entry = next((f for f in facts if bare in _text(f.get("figure") or f.get("text") or f.get("value")).replace(" ", "").replace(" ", "")), None)
        status = "to_verify" if entry and _text(entry.get("status")).lower().replace("-", "_") in {"to_verify", "unverified"} else ("sourced" if entry and (_text(entry.get("source")) or _text(entry.get("source_url"))) else ("unsourced" if entry else "no_entry"))
        figures.append({"figure": token, "status": status, "source": _text(entry.get("source") or entry.get("source_url")) if entry else None})
        if status == "no_entry":
            findings.append(_finding("figure_unsourced", "warn", f"figure:{token}", token, f"{token} has no fact-desk entry; add the figure with its source or mark it to-verify."))
        elif status == "unsourced":
            findings.append(_finding("figure_unsourced", "warn", f"figure:{token}", token, f"{token} is in the fact desk without a source."))
        elif status == "to_verify":
            findings.append(_finding("to_verify", "info", f"figure:{token}", token, f"{token} is marked to-verify; it needs an owner and a date before release."))
    return {"figures": figures, "n_sourced": sum(f["status"] == "sourced" for f in figures), "n_total": len(figures)}


def _message_house(house: dict, findings: list[dict]) -> dict[str, Any]:
    core = _text(house.get("core") or house.get("core_message"))
    pillars = _dicts(house.get("pillars") or house.get("proof_pillars"), "message_house.pillars")
    variants = _dicts(house.get("variants") or house.get("channel_variants"), "message_house.variants")
    if not core:
        findings.append(_finding("core_missing", "block", "message_house.core", None, "The message house has no core message."))
    elif len(_sentences(core)) > 1:
        findings.append(_finding("core_multiple", "warn", "message_house.core", len(_sentences(core)), "The core message is more than one sentence; a house has one roof."))
    if len(pillars) > MAX_PILLARS:
        findings.append(_finding("pillars_too_many", "warn", "message_house.pillars", len(pillars), f"{len(pillars)} proof pillars; keep at most {MAX_PILLARS}."))
    if not pillars:
        findings.append(_finding("pillars_missing", "warn", "message_house.pillars", None, "No proof pillars under the core message."))
    pillar_out = []
    for index, p in enumerate(pillars, start=1):
        evidence = p.get("evidence")
        ev_text = _text(evidence.get("text") if isinstance(evidence, dict) else evidence)
        ev_source = _text(evidence.get("source") if isinstance(evidence, dict) else p.get("source"))
        if not ev_text:
            findings.append(_finding("pillar_no_evidence", "block", f"message_house.pillars[{index}]", _text(p.get("text")), f"Pillar {index} has no evidence; a claim without proof is a slogan."))
        elif not ev_source:
            findings.append(_finding("pillar_evidence_unsourced", "warn", f"message_house.pillars[{index}]", ev_text[:60], f"Pillar {index} evidence has no source."))
        pillar_out.append({"text": _text(p.get("text")), "evidence": ev_text or None, "source": ev_source or None})
    variant_out = []
    for index, v in enumerate(variants, start=1):
        text = _text(v.get("text"))
        channel = _text(v.get("channel") or v.get("audience")) or f"variant-{index}"
        kept = _share_kept(core, text) if core else None
        if core and kept is not None and kept < CORE_KEEP_SHARE:
            findings.append(_finding("variant_core_lost", "warn", f"message_house.variants[{index}]", kept, f"{channel} variant keeps {kept:.0%} of the core sentence's words (below {CORE_KEEP_SHARE:.0%}); the roof must stay."))
        for other in variant_out:
            if _similar(other["text"], text) >= DUPLICATE_SHARE:
                findings.append(_finding("variant_duplicate", "warn", f"message_house.variants[{index}]", other["channel"], f"{channel} variant repeats the {other['channel']} variant; each channel needs its own form."))
        variant_out.append({"channel": channel, "text": text, "core_kept": kept})
    return {"core": core or None, "pillars": pillar_out, "variants": variant_out}


def _pr_board(tools: WorkspaceTools | None, args: dict) -> dict[str, Any]:
    draft = _read_draft(tools, args)
    doc_kind = _text(args.get("doc_kind") or args.get("document")).lower() or "press_release"
    house = args.get("message_house") if isinstance(args.get("message_house"), dict) else None
    if not draft and not house:
        raise ToolError("give draft (text), draft_path (a workspace file) or message_house")
    findings: list[dict[str, Any]] = []
    missing: list[str] = []
    language = _language(args, draft, (house or {}).get("core"))
    parts = args.get("parts") if isinstance(args.get("parts"), dict) else {}
    out: dict[str, Any] = {"language": language, "doc_kind": doc_kind}

    if draft and doc_kind in {"press_release", "story", "announcement", "sponsorship"}:
        found = _pr_parts(draft, parts)
        if not found["news_hook"]:
            findings.append(_finding("news_hook_missing", "block", "news_hook", None, "No news hook: the first paragraph must say what is new and why today."))
        quote = found["quote"]
        if doc_kind == "press_release":
            if not quote or not quote["text"]:
                findings.append(_finding("quote_missing", "block", "quote", None, "No quote; a release needs a named person saying something a journalist can print."))
            elif not quote["name"] or not quote["role"]:
                findings.append(_finding("quote_unattributed", "block", "quote", quote["text"][:60], "The quote has no name and role next to it."))
            if not found["boilerplate"]:
                findings.append(_finding("boilerplate_missing", "warn", "boilerplate", None, "No boilerplate ('Apie …' / 'About …' paragraph)."))
            elif len(_words(found["boilerplate"])) > BOILERPLATE_MAX_WORDS:
                findings.append(_finding("boilerplate_long", "warn", "boilerplate", len(_words(found["boilerplate"])), f"Boilerplate is {len(_words(found['boilerplate']))} words; keep it at {BOILERPLATE_MAX_WORDS} or fewer."))
            if not found["contact"]:
                findings.append(_finding("contact_missing", "block", "contact", None, "No press contact (e-mail or phone)."))
            if not found["date"]:
                findings.append(_finding("date_missing", "block", "date", None, "No release date or embargo line."))
        out["parts"] = found

    byline = args.get("byline") if isinstance(args.get("byline"), dict) else None
    if byline is not None and draft:
        author = _text(byline.get("author_name") or byline.get("author"))
        terms = [_text(t) for t in _list(byline.get("affiliation_terms"), "byline.affiliation_terms") if _text(t)]
        publication = _text(byline.get("publication"))
        author_named = bool(author) and author.casefold() in draft.casefold()
        if not author:
            findings.append(_finding("author_missing", "block", "byline", None, "No author named; a byline needs a practitioner who can answer the editor."))
        elif not author_named:
            findings.append(_finding("author_not_in_draft", "block", "byline", author, f"{author} does not appear in the draft."))
        disclosed = None
        if terms:
            low = _URL.sub(" ", _EMAIL.sub(" ", draft)).casefold()
            near_author =[low[max(0, m.start() - 300): m.end() + 300] for m in re.finditer(re.escape(author.casefold()), low)] if author else []
            bio = low[-max(600, len(low) // 4):]
            disclosed = any(t.casefold() in window for t in terms for window in near_author + [bio])
            if not disclosed:
                findings.append(_finding("disclosure_missing", "block", "byline", terms, "The commercial affiliation is not disclosed next to the author or in the closing bio."))
        else:
            missing.append("byline.affiliation_terms: not given; disclosure not checked")
        if publication and publication.casefold() not in draft.casefold():
            findings.append(_finding("publication_unnamed", "info", "byline", publication, f"{publication} is not named in the pitch."))
        if not _READER_WORD.search(draft):
            findings.append(_finding("reader_value_missing", "warn", "byline", None, "No sentence about what the publication's readers gain."))
        if not (_EMAIL.search(draft) or _PHONE.search(draft)):
            findings.append(_finding("contact_missing", "block", "byline", None, "No contact the editor can reach."))
        out["byline"] = {"author_named": author_named, "affiliation_disclosed": disclosed, "publication_named": bool(publication) and publication.casefold() in draft.casefold(), "reader_value_sentence_found": bool(_READER_WORD.search(draft)), "contact_found": bool(_EMAIL.search(draft) or _PHONE.search(draft))}

    facts = _dicts(args.get("facts") or args.get("fact_desk"), "facts")
    if draft and ("facts" in args or "fact_desk" in args):
        out["fact_desk"] = _fact_desk(draft, facts, findings, language)
    elif draft:
        missing.append("facts: no fact-desk entries; figures not matched to sources")
    sources = _list(args.get("sources"), "sources")
    if draft and sources and tools is not None:
        try:
            checked = claim_check(tools, {"draft": draft, "sources": sources, "language": language, "include_facts": args.get("include_facts", True)})
        except ToolError as exc:
            missing.append(f"claim_check: {exc}")
        else:
            for claim in checked.get("claims", []):
                if claim.get("status") == "unsupported":
                    findings.append(_finding("claim_unsupported", "warn", f"line {claim.get('line')}", claim.get("claim") or claim.get("text"), "claim_check found no source passage for this claim."))
                elif claim.get("status") == "number_differs":
                    findings.append(_finding("claim_number_differs", "warn", f"line {claim.get('line')}", claim.get("claim") or claim.get("text"), f"claim_check: the source says {claim.get('source_value') or 'a different number'}."))
            out["claim_check"] = {"counts": checked.get("counts"), "missing": checked.get("missing")}
            missing.extend(f"claim_check: {m}" for m in checked.get("missing", []))

    known_urls, read = _urls_in_workspace(tools)
    sponsor = _text(args.get("sponsor") or args.get("company"))
    partners = _dicts(args.get("partners"), "partners")
    for index, p in enumerate(partners, start=1):
        goal = _text(p.get("own_goal") or p.get("goal"))
        src = _text(p.get("source") or p.get("source_url"))
        if not goal or not src or (read and not _url_known(src, known_urls)):
            findings.append(_finding("partner_goal_unsourced", "block", f"partners[{index}]", _text(p.get("name")), f"{_text(p.get('name')) or 'partner'}: own goal {'missing' if not goal else 'without a source present in ' + (', '.join(read) or 'facts.md/research.md')}; build the project around what the partner already wants."))
    if doc_kind == "sponsorship" and not partners:
        findings.append(_finding("partners_missing", "block", "partners", None, "A sponsorship pack names the partner and its own goal."))
    if sponsor and draft and sponsor.casefold() not in draft.casefold():
        findings.append(_finding("sponsor_hidden", "block", "draft", sponsor, f"{sponsor} does not appear in the draft; disclose the sponsor."))
    beneficiaries = _dicts(args.get("beneficiaries"), "beneficiaries")
    if beneficiaries and sponsor and all(same_stem(_text(b.get("who")), sponsor) or sponsor.casefold() in _text(b.get("who")).casefold() for b in beneficiaries):
        findings.append(_finding("sponsor_only", "block", "beneficiaries", [b.get("who") for b in beneficiaries], f"Every beneficiary is {sponsor}; find who else gains or change the angle."))
    if doc_kind in {"press_release", "sponsorship"} and not beneficiaries and "beneficiaries" not in args:
        missing.append("beneficiaries: not given; who gains besides the company is not checked")
    affected = _dicts(args.get("affected"), "affected")
    if doc_kind == "announcement":
        if not affected:
            findings.append(_finding("affected_missing", "block", "affected", None, "An announcement of a change names who bears its cost."))
        for index, a in enumerate(affected, start=1):
            if not _text(a.get("their_words")):
                findings.append(_finding("their_words_missing", "warn", f"affected[{index}]", _text(a.get("group")), f"{_text(a.get('group')) or 'group'}: no sentence of how they would describe the change."))
    readers = _dicts(args.get("readers"), "readers")
    publications = [_text(p) for p in _list(args.get("publications"), "publications") if _text(p)]
    for pub in publications:
        if not any(same_stem(_text(r.get("publication")), pub) or _text(r.get("publication")).casefold() == pub.casefold() for r in readers):
            findings.append(_finding("reader_reason_missing", "block", f"readers:{pub}", pub, f"{pub}: no sentence on why its readers would look for this."))
    for index, r in enumerate(readers, start=1):
        reason = _text(r.get("reason"))
        if not reason:
            findings.append(_finding("reader_reason_missing", "block", f"readers[{index}]", _text(r.get("publication")), f"{_text(r.get('publication')) or 'publication'}: empty reader reason."))
        elif sponsor and sponsor.casefold() in reason.casefold() and not any(_term_in(_text(b.get("gain")), reason) for b in beneficiaries if _text(b.get("gain"))):
            findings.append(_finding("reason_is_company", "warn", f"readers[{index}]", reason[:80], f"The reason for {_text(r.get('publication'))} is about {sponsor}, not about what readers gain."))

    if house is not None:
        out["message_house"] = _message_house(house, findings)
    out["sources_read"] = read
    head = f"{doc_kind.replace('_', ' ')}" + (f", {len(_words(draft))} words" if draft else "") + (", message house" if house else "")
    return _finish("pr_board", out, findings, missing, head)


# --- demo_board ----------------------------------------------------------------------------------------


def _demo_board(args: dict) -> dict[str, Any]:
    steps = _dicts(args.get("steps") or args.get("acts"), "steps")
    audience = _text(args.get("audience"))
    claim = _text(args.get("claim") or args.get("one_claim"))
    situation = _text(args.get("situation"))
    result = _text(args.get("result"))
    if not steps and not claim and not situation and not result:
        raise ToolError("give a demo plan: audience, claim, situation, result and steps; this tool does not invent one")
    findings: list[dict[str, Any]] = []
    missing: list[str] = []
    language = _language(args, audience, claim, situation, result, *(s.get("action") for s in steps))
    session = _num(args.get("session_minutes") or args.get("timing"), "session_minutes", minimum=0)
    first_product = _num(args.get("first_product_minute"), "first_product_minute", minimum=0)
    judged_by = _text(args.get("judged_by") or args.get("judge_by"))
    takeaway = _text(args.get("takeaway"))
    materials = [_text(m) for m in _list(args.get("materials"), "materials") if _text(m)]

    if not audience:
        findings.append(_finding("audience_missing", "block", "audience", None, "No audience; a demo is built for one room, not for everyone."))
    if not claim:
        findings.append(_finding("claim_missing", "block", "claim", None, "No single claim the demo proves."))
    elif not re.search(r"\d", claim):
        findings.append(_finding("claim_no_number", "warn", "claim", claim, "The claim has no number; the visitor cannot judge 'faster' or 'easier' without one."))
    if not situation:
        findings.append(_finding("situation_missing", "block", "situation", None, "No starting situation (the customer's state before the task)."))
    if not result:
        findings.append(_finding("result_missing", "block", "result", None, "No visible end result."))
    elif not _ARTEFACT.search(result):
        findings.append(_finding("result_not_visible", "info", "result", result, "The result names no visible artefact (invoice sent, report exported, status changed)."))
    if not steps:
        findings.append(_finding("act_missing", "block", "steps", None, "No act for the visitor to perform; without a step list the demo is a talk."))
    if not judged_by:
        findings.append(_finding("judged_by_missing", "warn", "judged_by", None, "Say what the visitor judges the result by (time, count, a screen they recognise)."))
    if not takeaway:
        findings.append(_finding("takeaway_missing", "warn", "takeaway", None, "No takeaway the visitor leaves with (a file, a figure, a next step)."))
    if session is None:
        findings.append(_finding("timing_missing", "warn", "session_minutes", None, "No session length; timing not checked."))
    if not materials:
        findings.append(_finding("materials_missing", "info", "materials", None, "No materials listed (account, sample data, device, handout)."))

    timeline = []
    visitor_acts = 0
    for index, s in enumerate(steps, start=1):
        where = f"steps[{index}]"
        action = _text(s.get("action") or s.get("text"))
        actor = _text(s.get("actor"))
        minute = _num(s.get("minute"), f"{where}.minute", minimum=0)
        feature = _text(s.get("shows_feature") or s.get("feature"))
        moves = _text(s.get("moves_toward_result") or s.get("why"))
        passive = bool(_PASSIVE_ACT.search(action)) and not (actor and _VISITOR_ACTOR.search(actor) and not _PASSIVE_ACT.search(action))
        by_visitor = bool(actor and _VISITOR_ACTOR.search(actor)) or (not actor and not passive)
        if passive and (not actor or _VISITOR_ACTOR.search(actor)):
            findings.append(_finding("passive_act", "warn", where, action, f"Step {index}: the visitor watches or listens instead of doing; give them the click."))
        if by_visitor and not passive:
            visitor_acts += 1
        if feature and not moves:
            findings.append(_finding("feature_tour", "warn", where, feature, f"Step {index} shows {feature} without saying how it moves toward the result; cut or justify."))
        if index == 1 and situation and action and _share_kept(situation, action) < 0.2 and not _term_in(situation.split(",")[0], action):
            findings.append(_finding("situation_not_first", "warn", where, action, "Step 1 does not start from the customer's situation."))
        if index == len(steps) and result and action and _share_kept(result, action) < 0.2:
            findings.append(_finding("result_not_last", "warn", where, action, "The last step does not produce the stated result."))
        timeline.append({"step": index, "minute": minute, "actor": actor or None, "action": action, "shows_feature": feature or None, "moves_toward_result": moves or None, "passive": passive})
    if steps and visitor_acts == 0:
        findings.append(_finding("visitor_passive", "block", "steps", None, "No step is performed by the visitor; the act must be theirs."))

    first_product_pct = None
    if session and first_product is not None:
        first_product_pct = round(first_product / session, 2)
        if first_product_pct > LATE_PRODUCT_SHARE:
            findings.append(_finding("late_product", "warn", "first_product_minute", first_product, f"The working product appears at minute {first_product:g} of {session:g} ({first_product_pct:.0%}); get there within {LATE_PRODUCT_SHARE:.0%}."))
    elif first_product is None and session:
        missing.append("first_product_minute: not given; time to the live product not checked")
    live = _flag(args.get("live"), False) or "audience_reasons" in args or "pre_talk_questions" in args
    reasons = _dicts(args.get("audience_reasons"), "audience_reasons")
    pretalk = [_text(q) for q in _list(args.get("pre_talk_questions"), "pre_talk_questions") if _text(q)]
    if live:
        if len(reasons) < 2 and not _flag(args.get("audience_confirmed"), False):
            findings.append(_finding("audience_unknown", "warn", "audience_reasons", len(reasons), "Fewer than two reasons people come, and none confirmed; ask at the door or via clarify and branch the example."))
        for index, r in enumerate(reasons, start=1):
            if not _text(r.get("example_task") or r.get("example")):
                findings.append(_finding("reason_no_example", "warn", f"audience_reasons[{index}]", _text(r.get("reason")), "This reason has no example task to branch to."))
        if len(pretalk) < PRETALK_MIN:
            findings.append(_finding("pretalk_missing", "info", "pre_talk_questions", len(pretalk), f"{len(pretalk)} pre-talk questions; ask at least {PRETALK_MIN} before starting."))

    out = {"language": language, "audience": audience or None, "claim": claim or None, "situation": situation or None, "result": result or None, "judged_by": judged_by or None, "takeaway": takeaway or None,
           "session_minutes": session, "first_product_minute": first_product, "first_product_pct": first_product_pct, "materials": materials, "timeline": timeline, "visitor_acts": visitor_acts, "branches": len(reasons), "pre_talk_questions": pretalk}
    head = f"demo of {len(steps)} step{'s' if len(steps) != 1 else ''}" + (f", {session:g} min" if session else "")
    return _finish("demo_board", out, findings, missing, head)


# --- response_board ----------------------------------------------------------------------------------------


def _response_board(args: dict) -> dict[str, Any]:
    tiers = _dicts(args.get("tiers"), "tiers")
    if not tiers:
        raise ToolError("tiers is empty: write the escalation matrix as JSON first; this tool does not invent owners")
    scenarios = _dicts(args.get("scenarios") or args.get("drills"), "scenarios")
    findings: list[dict[str, Any]] = []
    missing: list[str] = []
    language = _language(args, *(f"{t.get('name')} {' '.join(map(str, t.get('examples') or []))}" for t in tiers))
    rows = []
    sensitive_present = False
    routine_owner: str | None = None
    sensitive_owners: list[str] = []
    for index, t in enumerate(tiers, start=1):
        where = f"tiers[{index}]"
        name = _text(t.get("name")) or f"tier-{index}"
        owner = _text(t.get("owner_role") or t.get("owner"))
        backup = _text(t.get("backup") or t.get("backup_owner"))
        minutes = _num(t.get("response_time_min") or t.get("minutes"), f"{where}.response_time_min", minimum=0)
        public = t.get("can_answer_publicly")
        escalate_to = _text(t.get("escalate_to"))
        escalate_when = _text(t.get("escalate_when"))
        threshold = _num(t.get("threshold_eur"), f"{where}.threshold_eur", minimum=0)
        text = " ".join([name, escalate_when, " ".join(map(str, t.get("examples") or []))])
        sensitive = _flag(t.get("sensitive"), False) or bool(_SENSITIVE.search(text))
        if _blank(owner):
            findings.append(_finding("tier_no_owner", "block", where, owner or None, f"{name}: no owner role (TBD counts as none)."))
        if minutes is None:
            findings.append(_finding("response_time_missing", "block", where, None, f"{name}: no response time in minutes."))
        elif minutes > RESPONSE_MAX_MIN:
            findings.append(_finding("response_time_long", "warn", where, minutes, f"{name}: {minutes:g} min is over a day."))
        if not escalate_to:
            findings.append(_finding("tier_no_escalation", "warn", where, None, f"{name}: no escalate_to; every tier needs a next hand."))
        elif not escalate_when:
            findings.append(_finding("escalate_when_missing", "warn", where, escalate_to, f"{name}: says who to escalate to but not when."))
        if sensitive:
            sensitive_present = True
            if _flag(public, False):
                findings.append(_finding("sensitive_public", "warn", where, name, f"{name} is sensitive but can answer publicly; acknowledge in public, move to private, hand over."))
            if not _blank(owner):
                sensitive_owners.append((owner.casefold(), backup))
        elif not _blank(owner) and routine_owner is None:
            routine_owner = owner.casefold()
        rows.append({"tier": name, "owner": owner or None, "response_time_min": minutes, "public": _flag(public, False) if public is not None else None, "escalate_to": escalate_to or None, "escalate_when": escalate_when or None, "threshold_eur": threshold, "sensitive": sensitive})
    if not sensitive_present:
        findings.append(_finding("no_sensitive_tier", "block", "tiers", None, "No tier for legal, safety, health, data or press cases; those never get a public answer from the community manager."))
    if routine_owner and any(o == routine_owner and not b for o, b in sensitive_owners):
        findings.append(_finding("same_owner_no_backup", "warn", "tiers", routine_owner, "Routine and sensitive tiers share one owner with no backup."))
    if not any(r["threshold_eur"] is not None for r in rows):
        findings.append(_finding("threshold_missing", "info", "tiers", None, "No EUR threshold on any tier; say above which refund or claim amount the case escalates."))
    if "contacts_confirmed" in args and not _flag(args.get("contacts_confirmed"), False):
        findings.append(_finding("contacts_unconfirmed", "warn", "contacts_confirmed", False, "Escalation contacts are not confirmed reachable."))
    elif "contacts_confirmed" not in args:
        missing.append("contacts_confirmed: not stated")

    tier_names = {r["tier"].casefold(): r for r in rows}
    reached = 0
    drills = []
    if not scenarios:
        findings.append(_finding("no_drill", "warn", "scenarios", None, "No practice scenario; a matrix nobody has walked is a guess."))
    for index, s in enumerate(scenarios, start=1):
        where = f"scenarios[{index}]"
        expected = _text(s.get("expected_tier")).casefold()
        walk = _dicts(s.get("walkthrough") or s.get("steps"), f"{where}.walkthrough")
        tier = tier_names.get(expected) or next((r for k, r in tier_names.items() if same_stem(k, expected)), None)
        if tier is None:
            findings.append(_finding("scenario_unknown_tier", "block", where, s.get("expected_tier"), f"Scenario {index} expects tier {s.get('expected_tier')!r}, which is not in the matrix."))
            drills.append({"scenario": _text(s.get("complaint_text"))[:80], "expected_tier": s.get("expected_tier"), "reached": False})
            continue
        actors = " ".join(_text(w.get("actor")) + " " + _text(w.get("action")) for w in walk)
        target = tier["escalate_to"]
        ok = bool(target) and _term_in(target, actors)
        if not walk:
            findings.append(_finding("drill_empty", "warn", where, None, f"Scenario {index} has no walkthrough."))
        elif target and not ok:
            findings.append(_finding("escalation_not_reached", "warn", where, target, f"Scenario {index}: the walkthrough never reaches {target}, the escalation of tier {tier['tier']}."))
        if ok:
            reached += 1
        last_minute = max((_num(w.get("minutes") or w.get("minute"), f"{where}.minutes") or 0 for w in walk), default=None)
        if tier["response_time_min"] is not None and walk and last_minute is not None and last_minute > tier["response_time_min"]:
            findings.append(_finding("drill_over_time", "warn", where, last_minute, f"Scenario {index} ends at minute {last_minute:g}, past the tier's {tier['response_time_min']:g} min target."))
        drills.append({"scenario": _text(s.get("complaint_text"))[:80], "expected_tier": tier["tier"], "reached": ok, "minutes": last_minute})

    with_owner = sum(1 for r in rows if r["owner"])
    coverage = {"tiers_with_owner": f"{with_owner}/{len(rows)}", "sensitive_tier_present": sensitive_present, "scenarios_reaching_escalation": f"{reached}/{len(scenarios)}"}
    header = ("Pakopa | Atsakingas | Min | Viešai | Eskaluoti kam | Kada | € riba" if language == "lt" else "Tier | Owner | Min | Public | Escalate to | When | EUR threshold")
    lines = [header, "-" * len(header)]
    for r in rows:
        lines.append(" | ".join(str(v if v is not None else "-") for v in (r["tier"], r["owner"], f"{r['response_time_min']:g}" if r["response_time_min"] is not None else None, r["public"], r["escalate_to"], r["escalate_when"], r["threshold_eur"])))
    out = {"language": language, "tiers": rows, "drills": drills, "coverage": coverage, "table": "\n".join(lines)}
    head = f"{len(rows)} tiers, {len(scenarios)} drill{'s' if len(scenarios) != 1 else ''}"
    return _finish("response_board", out, findings, missing, head)


# --- partner_board ----------------------------------------------------------------------------------------


def _partner_board(args: dict, known_urls: set[str], read: list[str]) -> dict[str, Any]:
    tiers = _dicts(args.get("tiers"), "tiers")
    support = _dicts(args.get("support_items") or args.get("support"), "support_items")
    partners = _dicts(args.get("partners") or args.get("candidates"), "partners")
    if not tiers and not support and not partners:
        raise ToolError("give tiers, support_items or partners; this tool does not invent a programme")
    findings: list[dict[str, Any]] = []
    missing: list[str] = []
    language = _language(args, *(t.get("name") for t in tiers), *(s.get("name") or s.get("text") for s in support))
    for index, t in enumerate(tiers, start=1):
        where = f"tiers[{index}]"
        name = _text(t.get("name")) or f"tier-{index}"
        theirs = [x for x in _list(t.get("partner_obligations") or t.get("partner_gives"), f"{where}.partner_obligations") if _text(x)]
        ours = [x for x in _list(t.get("our_obligations") or t.get("we_give"), f"{where}.our_obligations") if _text(x)]
        if not theirs or not ours:
            side = "partner" if not theirs else "our"
            findings.append(_finding("tier_one_sided", "block", where, name, f"{name}: no {side} obligations; a tier states what each side owes."))
    if "tiers" in args and not tiers:
        findings.append(_finding("tiers_missing", "warn", "tiers", None, "No tiers; say what partners get and give at each level."))
    for index, s in enumerate(support, start=1):
        where = f"support_items[{index}]"
        name = _text(s.get("name") or s.get("text")) or f"support-{index}"
        if not _text(s.get("removes_obstacle") or s.get("obstacle")):
            findings.append(_finding("support_no_obstacle", "warn", where, name, f"{name}: does not say which partner obstacle it removes."))
        if not _text(s.get("ship_signal") or s.get("signal")):
            findings.append(_finding("support_no_signal", "warn", where, name, f"{name}: no ship signal (what the partner does that tells us it worked)."))
    exclusivity = args.get("exclusivity")
    excl_text = _text(exclusivity.get("terms") if isinstance(exclusivity, dict) else exclusivity)
    if (tiers or support) and not excl_text:
        findings.append(_finding("exclusivity_unstated", "warn", "exclusivity", None, "Exclusivity terms not stated (none, by territory, by segment, by duration)."))

    rows = []
    small_mid = 0
    at_risk = 0.0
    unknown_risk = 0
    for index, p in enumerate(partners, start=1):
        where = f"partners[{index}]"
        name = _text(p.get("name")) or f"partner-{index}"
        band = _text(p.get("size_band")).lower()
        if band and band not in {"small", "mid", "large"}:
            raise ToolError(f"{where}.size_band must be small, mid or large, got {band!r}")
        if not band:
            findings.append(_finding("size_band_missing", "warn", where, name, f"{name}: no size_band."))
        elif band in {"small", "mid"}:
            small_mid += 1
        signal = p.get("investment_signal")
        sig_text = _text(signal.get("text") if isinstance(signal, dict) else signal)
        sig_src = _text(signal.get("source_url") if isinstance(signal, dict) else p.get("signal_source"))
        if not sig_text or not sig_src:
            findings.append(_finding("partner_no_signal", "warn", where, name, f"{name}: no sourced investment signal (shipped integration, public API, hiring); willingness unknown."))
        elif read and not _url_known(sig_src, known_urls):
            findings.append(_finding("partner_signal_unsourced", "warn", where, sig_src, f"{name}: signal source {sig_src} is not in {', '.join(read)}."))
        risk = _num(p.get("revenue_at_risk_eur"), f"{where}.revenue_at_risk_eur", minimum=0)
        bundled = _text(p.get("bundled_function"))
        boundary = _text(p.get("boundary_note"))
        if (risk and risk > 0) or bundled:
            if risk is None:
                unknown_risk += 1
            else:
                at_risk += risk
            if not boundary:
                findings.append(_finding("partner_bundle_conflict", "block", where, risk if risk is not None else "unknown", f"{name}: sells {bundled or 'the bundled function'} ({'€' + format(risk, 'g') + '/yr' if risk is not None else 'amount unknown'}) and no boundary is stated; write what we ship and what stays theirs."))
        rows.append({"name": name, "url": _text(p.get("url")) or None, "size_band": band or None, "signal": sig_text or None, "signal_source": sig_src or None, "revenue_at_risk_eur": risk, "bundled_function": bundled or None, "boundary_note": boundary or None})
    share = round(small_mid / len(partners), 2) if partners else None
    if partners and len(partners) < PARTNER_MIN_ROWS:
        missing.append(f"partners: {len(partners)} rows (fewer than {PARTNER_MIN_ROWS}); size mix not judged")
    elif partners and share is not None and share < SMALL_MID_SHARE:
        findings.append(_finding("partners_all_large", "warn", "partners", share, f"Only {share:.0%} of candidates are small or mid teams (below {SMALL_MID_SHARE:.0%}); size is not willingness."))
    if partners and not read:
        missing.append("runs/notes/facts.md, research.md: not found; signal sources not matched")
    out = {"language": language, "tiers": len(tiers), "support_items": len(support), "exclusivity": excl_text or None, "partners": rows,
           "summary_partners": {"n_total": len(partners), "share_small_mid": share, "n_without_signal": sum(1 for r in rows if not r["signal"] or not r["signal_source"]), "total_revenue_at_risk_eur": at_risk, "n_unknown_risk": unknown_risk}}
    head = f"{len(tiers)} tiers, {len(support)} support items, {len(partners)} partners"
    return _finish("partner_board", out, findings, missing, head)


# --- qualification ----------------------------------------------------------------------------------------


def _qualification(args: dict, known_urls: set[str], read: list[str]) -> dict[str, Any]:
    conditions = _dicts(args.get("conditions") or args.get("slots"), "conditions")
    questions = _dicts(args.get("questions"), "questions")
    segments = _dicts(args.get("segments"), "segments")
    sheet = _text(args.get("sheet")).lower() or ("screener" if (questions or segments) and not conditions else "sales")
    if sheet not in {"sales", "screener"}:
        raise ToolError("sheet must be sales or screener")
    findings: list[dict[str, Any]] = []
    missing: list[str] = []
    disqualifiers = [_text(d) for d in _list(args.get("disqualifiers"), "disqualifiers") if _text(d)]
    out: dict[str, Any] = {"sheet": sheet, "disqualifiers": disqualifiers}
    if not disqualifiers:
        findings.append(_finding("disqualifiers_missing", "warn", "disqualifiers", None, "No disqualifiers; say what ends the conversation (too small, wrong country, no decision this year)."))

    if sheet == "sales":
        if not conditions:
            raise ToolError("conditions is empty: qualification not done; this tool never infers need, means or access")
        language = _language(args, *(f"{c.get('evidence')} {c.get('question')}" for c in conditions))
        rank = {"absent": 0, "uncertain": 1, "confirmed": 2}
        by_slot: dict[str, dict[str, Any]] = {}
        for index, c in enumerate(conditions, start=1):
            where = f"conditions[{index}]"
            name = _text(c.get("name") or c.get("slot")).lower().replace(" ", "_").replace("-", "_")
            if name not in SALES_SLOTS:
                raise ToolError(f"{where}.name must be one of {', '.join(SALES_SLOTS)}, got {name!r}")
            status = _text(c.get("status")).lower()
            if status not in SLOT_STATUSES:
                raise ToolError(f"{where}.status must be one of {', '.join(SLOT_STATUSES)}, got {status!r}")
            evidence = _text(c.get("evidence"))
            src = _text(c.get("source_url") or c.get("source"))
            question = _text(c.get("question"))
            if status == "confirmed" and (not evidence or not src):
                findings.append(_finding("slot_unsourced", "block", where, name, f"{name} is confirmed without {'evidence' if not evidence else 'a source URL'}; 'they have budget' needs a source."))
            elif status == "confirmed" and read and _is_url(src) and not _url_known(src, known_urls):
                findings.append(_finding("slot_source_unknown", "warn", where, src, f"{name}: source {src} is not in {', '.join(read)}."))
            if status in {"uncertain", "absent"} and not question:
                findings.append(_finding("slot_no_question", "warn", where, name, f"{name} is {status} and has no discovery question."))
            by_slot[name] = {"status": status, "evidence": evidence or None, "source_url": src or None, "question": question or None}
        for slot in SALES_SLOTS:
            if slot not in by_slot:
                findings.append(_finding("slot_missing", "block", slot, None, f"{slot} not assessed; every slot gets a status, evidence or a question."))
        weakest = min(by_slot, key=lambda s: (rank[by_slot[s]["status"]], SALES_SLOTS.index(s))) if by_slot else None
        out.update({"language": language, "prospect": _text(args.get("prospect")) or None, "slots": by_slot, "weakest": weakest})
        head = f"sales qualification of {out['prospect'] or 'prospect'}: {len(by_slot)}/{len(SALES_SLOTS)} slots"
        return _finish("qualification", out, findings, missing, head, f"Tailor the follow-up to {weakest}." if weakest else "")

    if not segments:
        raise ToolError("segments is empty: no segments researched yet, run research first; this tool does not invent them")
    if not questions:
        raise ToolError("questions is empty: write the screener questions as JSON first")
    language = _language(args, *(s.get("name") for s in segments), *(q.get("text") for q in questions))
    names = [_text(s.get("name")) for s in segments]
    for index, s in enumerate(segments, start=1):
        if not _text(s.get("unmet_need") or s.get("need")):
            findings.append(_finding("segment_no_need", "warn", f"segments[{index}]", names[index - 1], f"{names[index - 1]}: no unmet need stated; a segment is a need, not a label."))
    matrix = {n: [0] * len(questions) for n in names}
    if len(questions) > MAX_SCREENER_QUESTIONS:
        findings.append(_finding("too_many_questions", "warn", "questions", len(questions), f"{len(questions)} questions; a screener sorts in {MAX_SCREENER_QUESTIONS} or fewer."))
    for qi, q in enumerate(questions, start=1):
        where = f"questions[{qi}]"
        text = _text(q.get("text"))
        if len(text) > QUESTION_MAX_CHARS:
            findings.append(_finding("question_long", "info", where, len(text), f"Question {qi} is {len(text)} characters; keep it under {QUESTION_MAX_CHARS}."))
        answers = _dicts(q.get("answers"), f"{where}.answers")
        if not answers:
            findings.append(_finding("question_no_answers", "block", where, text[:60], f"Question {qi} has no answers mapped to segments."))
            continue
        hit: set[str] = set()
        for ai, a in enumerate(answers, start=1):
            seg = _text(a.get("segment"))
            if not seg:
                findings.append(_finding("answer_no_segment", "warn", f"{where}.answers[{ai}]", _text(a.get("text")), f"Answer {ai} of question {qi} maps to no segment (fine only for a disqualifier)."))
                continue
            match = next((n for n in names if n.casefold() == seg.casefold()), None)
            if match is None:
                findings.append(_finding("unknown_segment", "block", f"{where}.answers[{ai}]", seg, f"Answer {ai} of question {qi} names segment {seg!r}, which is not in segments."))
                continue
            matrix[match][qi - 1] += 1
            hit.add(match)
        if names and all(_text(a.get("segment")).casefold() == _text(answers[0].get("segment")).casefold() for a in answers) and len(answers) > 1:
            findings.append(_finding("non_discriminating_question", "warn", where, _text(answers[0].get("segment")), f"Every answer of question {qi} points to {_text(answers[0].get('segment'))}; it sorts nobody."))
    for n in names:
        if sum(matrix[n]) == 0:
            findings.append(_finding("unreachable_segment", "block", f"segment:{n}", n, f"No answer leads to {n}; a screener that cannot reach a segment never sends its proof."))
    routes = _dicts(args.get("routes"), "routes")
    for n in names:
        route = next((r for r in routes if _text(r.get("segment")).casefold() == n.casefold()), None)
        if route is None:
            findings.append(_finding("route_missing", "warn", f"route:{n}", n, f"Segment {n} has no evidence item; add a case study or page before using this branch."))
        else:
            ref = _text(route.get("evidence") or route.get("evidence_url_or_file") or route.get("url"))
            if not ref:
                findings.append(_finding("route_missing", "warn", f"route:{n}", n, f"Route for {n} has no evidence reference."))
            elif read and _is_url(ref) and not _url_known(ref, known_urls):
                findings.append(_finding("evidence_unsourced", "warn", f"route:{n}", ref, f"Route for {n}: {ref} is not in {', '.join(read)}."))
    if routes and not read:
        missing.append("runs/notes/facts.md, research.md: not found; route evidence not matched")
    out.update({"language": language, "segments": names, "questions": len(questions), "coverage_matrix": matrix, "routes": [{"segment": _text(r.get("segment")), "evidence": _text(r.get("evidence") or r.get("evidence_url_or_file") or r.get("url")) or None} for r in routes]})
    head = f"screener of {len(questions)} questions for {len(names)} segments"
    return _finish("qualification", out, findings, missing, head)


# --- evidence_log ----------------------------------------------------------------------------------------


def _years(text: str) -> list[int]:
    return [int(y) for y in re.findall(r"\b(?:19|20)\d\d\b", text or "")]


def _source_audit(entry: dict, needed: dict, where: str, findings: list[dict]) -> dict[str, Any]:
    stated = {k: _text(entry.get(k) or entry.get(alias)) for k, alias in (("measure", "defines"), ("population", "covers"), ("period", "collected"))}
    out: dict[str, Any] = {"collected": stated["period"] or "not stated", "covers": stated["population"] or "not stated", "defines": stated["measure"] or "not stated", "gaps": []}
    fit = "fits"
    for key, label in (("period", "collected"), ("population", "covers"), ("measure", "defines")):
        if not stated[key]:
            out["gaps"].append(f"{label}: not stated on the page")
            fit = "partial"
            findings.append(_finding("source_not_stated", "warn", where, label, f"The source does not state its {key}; do not fill it from memory."))
    need_period = _years(_text(needed.get("period")))
    have_period = _years(stated["period"])
    if need_period and have_period and max(have_period) < min(need_period) - PERIOD_SLACK_YEARS:
        out["gaps"].append(f"collected {max(have_period)}, needed {min(need_period)}: older by more than {PERIOD_SLACK_YEARS} years")
        fit = "partial"
        findings.append(_finding("source_period_old", "warn", where, max(have_period), f"Collected {max(have_period)}, the decision needs {min(need_period)}."))
    need_pop = _text(needed.get("population"))
    if need_pop and stated["population"] and _share_kept(need_pop, stated["population"]) == 0:
        out["gaps"].append(f"covers {stated['population']}, needed {need_pop}")
        fit = "unfit"
        findings.append(_finding("source_population_mismatch", "warn", where, stated["population"], f"The source covers {stated['population']}; the decision needs {need_pop}."))
    need_measure = _text(needed.get("measure"))
    if need_measure and stated["measure"] and _share_kept(need_measure, stated["measure"]) < 0.5:
        out["gaps"].append(f"defines {stated['measure']}, needed {need_measure}")
        if fit != "unfit":
            fit = "partial"
        findings.append(_finding("source_measure_differs", "warn", where, stated["measure"], f"The source measures {stated['measure']}, not {need_measure}."))
    out["fit"] = fit
    return out


def _evidence_log(tools: WorkspaceTools | None, args: dict) -> dict[str, Any]:
    entries = _dicts(args.get("entries") or args.get("observations"), "entries")
    if not entries:
        raise ToolError("entries is empty: give at least one observation {kind, text, source_url, quote, tags}")
    if tools is None:
        raise ToolError("evidence_log needs a workspace")
    rel = _text(args.get("path")) or EVIDENCE_PATH
    target = tools.resolve(rel)
    findings: list[dict[str, Any]] = []
    missing: list[str] = []
    known_urls, read = _urls_in_workspace(tools)
    known_urls.update(_text(u) for u in _list(args.get("known_urls"), "known_urls") if _text(u))
    needed = args.get("needed") if isinstance(args.get("needed"), dict) else {}
    language = _language(args, *(e.get("text") for e in entries))
    stamp = datetime.now().isoformat(timespec="seconds")
    accepted: list[dict[str, Any]] = []
    no_url: list[str] = []
    counts = {k: 0 for k in EVIDENCE_KINDS}
    audits = []
    for index, e in enumerate(entries, start=1):
        where = f"entries[{index}]"
        kind = _text(e.get("kind")).lower()
        text = _text(e.get("text"))
        url = _text(e.get("source_url") or e.get("url") or e.get("source"))
        if kind not in EVIDENCE_KINDS:
            findings.append(_finding("invalid_kind", "block", where, kind or None, f"kind must be one of {', '.join(EVIDENCE_KINDS)}; entry not written."))
            continue
        if not text:
            findings.append(_finding("text_missing", "block", where, None, "Empty observation; entry not written."))
            continue
        tags = e.get("tags") if isinstance(e.get("tags"), dict) else {}
        category = _text(tags.get("category")).lower() or None
        if kind == "modification" and category not in EVIDENCE_CATEGORIES:
            findings.append(_finding("invalid_category", "block", where, category, f"A modification is new_use or refinement; got {category!r}. Entry not written."))
            continue
        status = "ok"
        if not url:
            status = "no_url"
            no_url.append(text[:80])
            findings.append(_finding("entry_no_url", "warn", where, text[:60], "No source URL or file reference; the row is marked unsourced."))
        elif _is_url(url) and (read or "known_urls" in args) and not _url_known(url, known_urls):
            status = "unsourced"
            findings.append(_finding("unsourced", "warn", where, url, f"{url} was not fetched this run and is not in {', '.join(read) or 'known_urls'}."))
        row = {"ts": stamp, "kind": kind, "text": text, "source_url": url or None, "quote": _text(e.get("quote")) or None, "tags": {"category": category, "task_enabled": _text(tags.get("task_enabled")) or None, **{k: v for k, v in tags.items() if k not in {"category", "task_enabled"}}}, "status": status}
        if kind == "source_audit":
            row["audit"] = _source_audit(e, needed or (e.get("needed") if isinstance(e.get("needed"), dict) else {}), where, findings)
            audits.append({"source_url": url or None, **row["audit"]})
        accepted.append(row)
        counts[kind] += 1
    if not read and not _list(args.get("known_urls"), "known_urls"):
        missing.append("runs/notes/facts.md, research.md, known_urls: none available; URL membership not checked")
    if "source_audit" in counts and counts["source_audit"] and not needed and not any(isinstance(e.get("needed"), dict) for e in entries):
        missing.append("needed {measure, population, period}: not given; fit judged only on what the source states")
    if accepted:
        tools.append_file(rel, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in accepted))
    by_category = {c: sum(1 for r in accepted if r["kind"] == "modification" and r["tags"]["category"] == c) for c in EVIDENCE_CATEGORIES}
    out = {"language": language, "path": str(target.relative_to(tools.root)), "written": len(accepted), "rejected": len(entries) - len(accepted), "counts_by_kind": counts, "counts_by_category": by_category, "no_url": no_url, "unsourced": sum(1 for r in accepted if r["status"] == "unsourced"), "audits": audits, "entries": accepted}
    head = f"{len(accepted)} of {len(entries)} entries appended to {out['path']}"
    return _finish("evidence_log", out, findings, missing, head)


# --- community_rules ----------------------------------------------------------------------------------------


def _community_rules(tools: WorkspaceTools | None, args: dict) -> dict[str, Any]:
    text = _read_draft(tools, args, "text", "path")
    if not text.strip():
        raise ToolError("give text (markdown) or path (a workspace file); this tool does not write the rules")
    findings: list[dict[str, Any]] = []
    missing: list[str] = []
    language = _language(args, text)
    sections = {}
    for key, pattern in _SECTION_WORDS.items():
        sections[key] = bool(pattern.search(text))
        if not sections[key]:
            findings.append(_finding("section_missing", "block", key, None, f"The rules do not say {key.replace('_', ' ')}."))
    heads = list(_RULE_HEAD.finditer(text))
    rules = []
    for i, m in enumerate(heads):
        body = text[m.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        has_allowed = bool(_ALLOWED_LINE.search(body))
        has_removed = bool(_REMOVED_LINE.search(body))
        title = m.group(0).strip().lstrip("#-*0123456789.) ").strip("* ")
        rules.append({"rule": title, "allowed_example": has_allowed, "removed_example": has_removed})
        if not (has_allowed and has_removed):
            findings.append(_finding("rule_no_examples", "warn", f"rule:{title[:40]}", title[:60], f"Rule '{title[:60]}' lacks {'an allowed' if not has_allowed else ''}{' and ' if not has_allowed and not has_removed else ''}{'a removed' if not has_removed else ''} example line."))
    if not rules:
        findings.append(_finding("no_rules", "block", "rules", None, "No rule headings (## Rule … / ## Taisyklė …) found."))
    mod = _MOD_HEAD.search(text)
    table_rows = 0
    if mod:
        after = text[mod.end():]
        rows = [r for r in _TABLE_ROW.findall(after.split("\n#")[0]) if not re.fullmatch(r"\s*\|[\s|:-]+\|\s*", r)]
        table_rows = max(0, len(rows) - 1) if rows else 0
    if table_rows < 5:
        findings.append(_finding("no_mod_table", "block", "moderator_table", table_rows, f"Moderator reference table has {table_rows} rows; it needs at least 5 cases (case, action, decider, appeal)."))
    out = {"language": language, "sections": sections, "rules": rules, "moderator_table_rows": table_rows}
    head = f"{len(rules)} rules, moderator table {table_rows} rows"
    return _finish("community_rules", out, findings, missing, head)


# --- retell / ad_pretest (cold readers) -----------------------------------------------------------------------


def _reader_prompt(persona: str, task: str, language: str) -> str:
    if language == "lt":
        return f"Tu esi {persona}. Tu nieko nežinai apie šį produktą ar įmonę, nematei jokio brief'o. {task} Atsakyk vienu sakiniu, savais žodžiais. Jei nežinai, parašyk „nežinau“."
    return f"You are {persona}. You know nothing about this product or company and have seen no brief. {task} Answer in one sentence, in your own words. If you do not know, write 'I don't know'."


def _ask(llm: Any, model: str, system: str, user: str) -> str:
    response = llm.complete(model=model, messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    content = getattr(response, "content", response)
    if isinstance(content, list):
        content = " ".join(_text(part.get("text") if isinstance(part, dict) else part) for part in content)
    return _text(content)


def _score(must_survive: list[str], readings: list[str]) -> dict[str, Any]:
    kept_per = []
    survivors = 0
    for r in readings:
        kept = [t for t in must_survive if _term_in(t, r)]
        kept_per.append(kept)
        if kept:
            survivors += 1
    lost = [t for t in must_survive if not any(t in k for k in kept_per)]
    rate = round(survivors / len(readings), 2) if readings and must_survive else None
    return {"kept": kept_per, "lost": lost, "survival_rate": rate}


def _retell(args: dict, llm: Any) -> dict[str, Any]:
    statements = _dicts(args.get("statements") or args.get("candidates"), "statements")
    if not statements:
        raise ToolError("statements is empty: give 1-5 candidate statements with must_survive terms")
    if len(statements) > 5:
        raise ToolError("at most 5 statements per call")
    findings: list[dict[str, Any]] = []
    missing: list[str] = []
    persona = _text(args.get("buyer_type") or args.get("persona") or args.get("audience"))
    language = _language(args, persona, *(s.get("text") for s in statements))
    n_readers = int(_num(args.get("n_readers"), "n_readers", minimum=1) or DEFAULT_READERS)
    n_readers = min(n_readers, MAX_READERS)
    model = _text(args.get("model")) or DEFAULT_MODEL
    if not persona:
        findings.append(_finding("no_persona", "block", "buyer_type", None, "No buyer type; the reader must be the buyer, not 'a person'."))
    task = "Kolega klausia, kas tai per produktas ir kam jis. Perpasakok šį teiginį:" if language == "lt" else "A colleague asks what this product is and what it is for. Repeat this statement:"
    system = _reader_prompt(persona or ("pirkėjas" if language == "lt" else "a buyer"), task, language)
    results = []
    for index, s in enumerate(statements, start=1):
        where = f"statements[{index}]"
        text = _text(s.get("text"))
        sid = _text(s.get("id")) or f"s{index}"
        terms = [_text(t) for t in _list(s.get("must_survive"), f"{where}.must_survive") if _text(t)]
        if not text:
            raise ToolError(f"{where}.text is empty")
        if not terms:
            findings.append(_finding("must_survive_missing", "warn", where, sid, f"{sid}: no must_survive terms; name the distinctive use case so the retelling can be scored."))
        row: dict[str, Any] = {"id": sid, "text": text, "must_survive": terms, "retellings": [], "kept": [], "lost": terms, "survival_rate": None, "named_use_case": None, "prompt": {"model": model, "system": system, "user": text}}
        if llm is not None and persona:
            readings = [_ask(llm, model, system, text) for _ in range(n_readers)]
            row["retellings"] = readings
            row["named_use_case"] = [bool(r) and not _DONT_KNOW.search(r) and len(_words(r)) >= 4 for r in readings]
            if terms:
                row.update(_score(terms, readings))
                if row["survival_rate"] == 0:
                    findings.append(_finding("retell_lost", "warn", where, sid, f"{sid}: none of {n_readers} readers kept {', '.join(terms)}; retold as: {readings[0][:80]!r}."))
                elif row["survival_rate"] is not None and row["survival_rate"] < 0.5:
                    findings.append(_finding("retell_weak", "info", where, row["survival_rate"], f"{sid}: {row['survival_rate']:.0%} of readers kept a distinctive term."))
            if not any(row["named_use_case"]):
                findings.append(_finding("no_use_case", "warn", where, sid, f"{sid}: no reader could say what the product is for."))
        results.append(row)
    if llm is None:
        missing.append("llm: no second-family reader available; run the listed prompts in fresh single-turn chats and score with must_survive, do not self-judge")
        findings.append(_finding("retell_not_run", "info", "llm", None, "Retell test not run: no reader model injected."))
    ranked = sorted((r for r in results if r["survival_rate"] is not None), key=lambda r: -r["survival_rate"])
    out = {"language": language, "buyer_type": persona or None, "model": model, "n_readers": n_readers, "proxy": True, "note": "model proxy, not real buyers", "statements": results, "best": ranked[0]["id"] if ranked else None}
    head = f"retell of {len(statements)} statement{'s' if len(statements) != 1 else ''} by {n_readers} cold readers" if llm is not None else f"retell of {len(statements)} statement{'s' if len(statements) != 1 else ''}, not run"
    return _finish("retell", out, findings, missing, head)


def _image_part(tools: WorkspaceTools | None, path: str, missing: list[str]) -> dict | None:
    if not path or tools is None:
        return None
    try:
        file = tools.resolve(path)
    except ToolError as exc:
        missing.append(str(exc))
        return None
    if not file.is_file():
        missing.append(f"image_path not found: {path}")
        return None
    mime = "image/png" if file.suffix.lower() == ".png" else "image/jpeg"
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(file.read_bytes()).decode('ascii')}"}}


def _ad_pretest(tools: WorkspaceTools | None, args: dict, llm: Any) -> dict[str, Any]:
    ads = _dicts(args.get("ads") or ([args["ad"]] if isinstance(args.get("ad"), dict) else None), "ads")
    if not ads:
        raise ToolError("ads is empty: give the ad copy (headline, primary_text) and the audience")
    mode = _text(args.get("mode")).lower() or "glance"
    if mode not in {"glance", "reference", "identification"}:
        raise ToolError("mode must be glance, reference or identification")
    findings: list[dict[str, Any]] = []
    missing: list[str] = []
    audience = _text(args.get("audience"))
    language = _language(args, audience, *(f"{a.get('headline')} {a.get('primary_text')}" for a in ads))
    n_readers = min(int(_num(args.get("n_readers"), "n_readers", minimum=1) or DEFAULT_READERS), MAX_READERS)
    model = _text(args.get("model")) or DEFAULT_MODEL
    exposure = int(_num(args.get("exposure_ms"), "exposure_ms", minimum=100) or 1500)
    job = _text(args.get("job"))
    if not audience:
        findings.append(_finding("no_audience", "block", "audience", None, "No audience; the test cannot be run as a persona and this tool never invents one."))
        missing.append("audience: not given")
    tasks = {
        "glance": (f"Šį skelbimą matei {exposure / 1000:g} s. Ką produktas daro ir kam jis skirtas?" if language == "lt" else f"You saw this ad for {exposure / 1000:g} seconds. What does the product do and for whom?"),
        "reference": ("Paaiškink savais žodžiais, ką reiškia šiame skelbime esantis palyginimas; jei nežinai dalyko, su kuriuo lyginama, taip ir parašyk." if language == "lt" else "Explain in your own words what the comparison in this ad means; say if you do not know the thing it refers to."),
        "identification": ("Kas yra šioje scenoje ir ar įsivaizduotum save prie jų prisijungiant? Atsakyk: KAS: … PRISIJUNGČIAU: taip/ne." if language == "lt" else "Who is in this scene, and would you picture yourself joining them? Answer: WHO: … JOIN: yes/no."),
    }
    system = _reader_prompt(audience or ("pirkėjas" if language == "lt" else "a buyer"), tasks[mode], language)
    results = []
    for index, ad in enumerate(ads, start=1):
        where = f"ads[{index}]"
        aid = _text(ad.get("id")) or f"ad{index}"
        headline = _text(ad.get("headline"))
        primary = _text(ad.get("primary_text") or ad.get("text"))
        if not headline and not primary:
            raise ToolError(f"{where}: empty copy")
        terms = [_text(t) for t in _list(ad.get("must_survive") or args.get("must_survive"), f"{where}.must_survive") if _text(t)]
        if job and not terms:
            terms = content_words(job)[:3]
        visible = (primary[:GLANCE_CHARS] + ("…" if len(primary) > GLANCE_CHARS else "") + ("\n" + headline if headline else "")).strip() if mode == "glance" and exposure <= 1500 else "\n".join(x for x in (headline, primary, _text(ad.get("description"))) if x)
        if not terms:
            findings.append(_finding("must_survive_missing", "warn", where, aid, f"{aid}: no must_survive terms (product and benefit) and no job line; the guess cannot be scored."))
        image_desc = _text(ad.get("image_description"))
        user_text = visible + (f"\n[Image: {image_desc}]" if image_desc else "")
        row: dict[str, Any] = {"id": aid, "visible_text": visible, "must_survive": terms, "readings": [], "kept": [], "lost": terms, "survival_rate": None, "verdict": None, "prompt": {"model": model, "system": system, "user": user_text}}
        if llm is not None and audience:
            image = _image_part(tools, _text(ad.get("image_path")), missing) if mode == "identification" else None
            if mode == "identification" and image is None and not image_desc:
                findings.append(_finding("no_image", "warn", where, aid, f"{aid}: identification needs image_path or image_description."))
                results.append(row)
                continue
            readings = []
            for _ in range(n_readers):
                if image is not None:
                    response = llm.complete(model=model, messages=[{"role": "system", "content": system}, {"role": "user", "content": [{"type": "text", "text": user_text}, image]}])
                    content = getattr(response, "content", response)
                    readings.append(_text(" ".join(_text(p.get("text") if isinstance(p, dict) else p) for p in content) if isinstance(content, list) else content))
                else:
                    readings.append(_ask(llm, model, system, user_text))
            row["readings"] = readings
            if terms:
                row.update(_score(terms, readings))
            dont_know = sum(1 for r in readings if _DONT_KNOW.search(r))
            if mode == "glance":
                row["verdict"] = "pass" if row["survival_rate"] not in (None, 0) and dont_know < len(readings) else "warn"
                if row["verdict"] == "warn":
                    findings.append(_finding("glance_unclear", "warn", where, aid, f"{aid}: after {exposure} ms readers could not say what the product does ({dont_know}/{len(readings)} did not know; kept {sum(1 for k in row['kept'] if k)}/{len(readings)}). Visible: {visible[:80]!r}."))
            elif mode == "reference":
                row["reference_understood"] = dont_know == 0 and (row["survival_rate"] is None or row["survival_rate"] > 0)
                row["verdict"] = "pass" if row["reference_understood"] else "warn"
                if not row["reference_understood"]:
                    findings.append(_finding("reference_unclear", "warn", where, aid, f"{aid}: {dont_know}/{len(readings)} readers did not know the reference; say it plainly or drop it."))
            else:
                joins = [bool(_YES.search(r.split("JOIN:")[-1].split("PRISIJUNGČIAU:")[-1])) and not _NO.search(r.split("JOIN:")[-1].split("PRISIJUNGČIAU:")[-1][:12]) for r in readings]
                row["would_join"] = sum(joins) > len(joins) / 2
                row["depicted_people"] = readings[0].split("JOIN:")[0].split("PRISIJUNGČIAU:")[0].replace("WHO:", "").replace("KAS:", "").strip()
                row["verdict"] = "pass" if row["would_join"] else "warn"
                if not row["would_join"]:
                    findings.append(_finding("depicted_mismatch", "warn", where, aid, f"{aid}: the audience proxy would not join the people shown ({row['depicted_people'][:60]!r}); show the segment's own people."))
        results.append(row)
    if llm is None:
        missing.append("llm: no reader model injected; run the listed prompts in fresh single-turn chats with only the ad and the persona, then score with must_survive")
        findings.append(_finding("pretest_not_run", "info", "llm", None, "Ad pretest not run: no reader model injected."))
    ordered = sorted(results, key=lambda r: (-(r["survival_rate"] or 0), len(r["visible_text"])))
    out = {"language": language, "mode": mode, "audience": audience or None, "model": model, "n_readers": n_readers, "exposure_ms": exposure, "proxy": True, "note": "model proxy, not real buyers; confirm with five buyers before spend", "ads": results, "ranking": [r["id"] for r in ordered]}
    head = f"{mode} pretest of {len(ads)} ad{'s' if len(ads) != 1 else ''}" + (f" by {n_readers} cold readers" if llm is not None and audience else ", not run")
    return _finish("ad_pretest", out, findings, missing, head)


# --- entry points -----------------------------------------------------------------------------------------------


def plan_check(tools: WorkspaceTools | None, args: dict, llm: Any = None) -> dict[str, Any]:
    """Check one structured marketing plan; ``kind`` picks the checker (see the module docstring).

    Deterministic for every kind except ``retell`` and ``ad_pretest``, which call ``llm.complete``
    once per reader and statement with a single-turn persona prompt and otherwise return the prompts
    under ``missing: ["llm"]``. Every result has ``findings``, ``missing``, ``counts``, ``clean``, ``summary``.
    """
    args = args or {}
    kind = _text(args.get("kind")).lower()
    if kind not in KINDS:
        raise ToolError(f"kind must be one of {', '.join(KINDS)}")
    if kind == "content_calendar":
        return _content_calendar(tools, args)
    if kind == "pr_board":
        return _pr_board(tools, args)
    if kind == "demo_board":
        return _demo_board(args)
    if kind == "response_board":
        return _response_board(args)
    if kind == "partner_board":
        known, read = _urls_in_workspace(tools)
        return _partner_board(args, known, read)
    if kind == "qualification":
        known, read = _urls_in_workspace(tools)
        return _qualification(args, known, read)
    if kind == "evidence_log":
        return _evidence_log(tools, args)
    if kind == "community_rules":
        return _community_rules(tools, args)
    if kind == "retell":
        return _retell(args, llm)
    return _ad_pretest(tools, args, llm)


def plan_check_tool(tools: WorkspaceTools | None, args: dict, llm: Any = None) -> str:
    """Backs the worker-facing ``plan_check`` tool: JSON the worker can quote from."""
    return json.dumps(plan_check(tools, args or {}, llm), ensure_ascii=False, indent=1, default=str)
