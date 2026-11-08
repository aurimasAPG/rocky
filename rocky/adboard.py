"""The ad board: Facebook / Instagram ads as one page a marketer can open.

Workers call the ``ad_board`` tool with structured ads. Rocky checks them with
code (every number needs a source, lengths against the feed) and renders a
self-contained ``deliverables/ads.html``: each ad as it looks in the feed, with
copy buttons and the evidence behind it. Every call leaves a page on disk, so a
run that dies later still hands over the copy.
"""

from __future__ import annotations

import base64
import html
import json
import mimetypes
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from rocky.lang import detect_language
from rocky.playbook import FIX_CAP_EUR, for_code, switched_off
from rocky.tools import ToolError, WorkspaceTools

BOARD_PATH = "deliverables/ads.html"
PACK_PATH = "runs/notes/ads.json"
FACTS_PATH = "runs/notes/facts.md"
MAX_ADS = 10
REQUIRED = ("primary_text", "headline", "cta")
COPY_FIELDS = ("primary_text", "headline", "description")
# The offer behind each ad's promise; the ad itself is the promotion (Principles of Marketing, pp. 32–35).
MIX_FIELDS = ("product", "price", "place")
# A set of ads is a test: the current pitch against messages about outcomes already delivered (Jobs to Be Done, pp. 99–101).
TEST_ROLES = ("control", "challenger")
# What buyers expect from anyone in the category, checked before money goes on differentiation (Jobs to Be Done, pp. 105–106).
STAKE_STATUSES = ("met", "unmet", "unknown")
# Selling more where the brand already sells, or taking it to new buyers, which needs proof they can be reached (Principles of Marketing, pp. 75–76).
GROWTH_KINDS = ("penetration", "development")
# Whether a segment is worth a distinct offer; "distinct" only matters with several ads (Principles of Marketing, pp. 188–189).
SEGMENT_FIELDS = ("size", "buying_power", "reach_cost", "distinct")
# What the feed shows before it cuts the text.
FEED_LIMITS = {"primary_text": 125, "headline": 40, "description": 30}
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
PROMISES = (
    "garantuoj", "jokių klaidų", "be klaidų", "visiškai", "100 %", "100%", "geriausi", "pigiausi", "nr. 1",
    "guarantee", "the best", "no errors", "cheapest",
)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")

_FIELD_NAMES = {
    "en": {
        "primary_text": "primary text", "headline": "headline", "description": "description", "cta": "button",
        "product": "what the buyer gets", "price": "what it costs", "place": "where the buyer gets it",
        "size": "how many such buyers there are", "buying_power": "whether they can afford it",
        "reach_cost": "what it costs to reach them", "distinct": "why they would respond differently",
    },
    "lt": {
        "primary_text": "pagrindinis tekstas", "headline": "antraštė", "description": "aprašymas", "cta": "mygtukas",
        "product": "ką pirkėjas gauna", "price": "kiek tai kainuoja", "place": "kur pirkėjas tai gauna",
        "size": "kiek yra tokių pirkėjų", "buying_power": "ar jie gali tai sau leisti",
        "reach_cost": "kiek kainuoja juos pasiekti", "distinct": "kodėl jie reaguotų kitaip",
    },
}
_MESSAGES = {
    "en": {
        "no_ads": "There are no ads.",
        "bad_url": "The website address must start with http:// or https://.",
        "missing_field": "{Field} is missing.",
        "number_unsourced": "The number “{value}” in the {field} has no source. Add it to claims with its URL, or write the copy without it.",
        "source_bad": "Claim “{value}”: the source is not a URL.",
        "source_unknown": "Claim “{value}”: the source is not in " + FACTS_PATH + ".",
        "too_long": "{Field}: the feed shows up to {limit} characters, this has {n}.",
        "promise": "Absolute promise “{value}”: make sure you can prove it.",
        "no_image": "No visual yet.",
        "mix_missing": "The promise needs an offer behind it: add {field}.",
        "segment_gap": "This segment is not checked yet: add {field}.",
        "no_reach": "New market: there is no evidence these buyers can be reached.",
        "stake_unmet": "“{value}” is below the minimum buyers expect. Fix it before spending on ads.",
        "no_stakes": "Table stakes are not listed: say what buyers expect from anyone in this category.",
        "no_test": "To compare messages, mark one ad as the control (the current pitch) and at least one as a challenger.",
    },
    "lt": {
        "no_ads": "Nėra nė vienos reklamos.",
        "bad_url": "Svetainės adresas turi prasidėti http:// arba https://.",
        "missing_field": "Trūksta: {field}.",
        "number_unsourced": "Skaičius „{value}“ neturi šaltinio ({field}).",
        "source_bad": "Teiginys „{value}“: šaltinis nėra nuoroda.",
        "source_unknown": "Teiginys „{value}“: šaltinio nėra faktų faile.",
        "too_long": "{Field}: rekomenduojama iki {limit} simbolių, dabar {n}.",
        "promise": "Absoliutus pažadas „{value}“: patikrinkite, ar galite jį įrodyti.",
        "no_image": "Vizualas dar nesukurtas.",
        "mix_missing": "Pažadą turi palaikyti pasiūlymas: nurodykite, {field}.",
        "segment_gap": "Segmentas dar nepatikrintas: nurodykite, {field}.",
        "no_reach": "Nauja rinka: nėra įrodymų, kad šiuos pirkėjus galima pasiekti.",
        "stake_unmet": "„{value}“: nepasiektas minimumas, kurio pirkėjai tikisi. Pirma sutvarkykite tai, tik tada leiskite biudžetą.",
        "no_stakes": "Nenurodyta, ko pirkėjai tikisi iš bet kurio šios srities pardavėjo.",
        "no_test": "Kad galėtumėte palyginti žinutes, pažymėkite vieną reklamą kaip kontrolinę (dabartinė žinutė) ir bent vieną kaip naują.",
    },
}
_LABELS = {
    "en": {
        "slug": "Rocky · ad board", "ads": "Ads", "ready": "Ready", "fix": "Need fixing", "sponsored": "Sponsored",
        "more": "See more", "stamp_ok": "Ready", "stamp_fix": "Fix", "stamp_image": "No visual", "number": "No.",
        "audience": "Audience", "copy": "Copy", "copied": "Copied", "text": "Text", "headline": "Headline",
        "description": "Description", "download": "Download visual", "no_visual": "No visual yet",
        "evidence": "Evidence", "evidence_note": "Where every number in these ads comes from.", "claim": "Claim",
        "source": "Source", "no_claims": "These ads make no claims that need a source.",
        "why": "Why?", "fix_action": "Fix it (up to €{cap})", "ignore": "Ignore", "handled": "Mark as handled", "undo": "Undo",
        "fixing": "Fixing…", "command_copied": "Command copied: run it in the campaign folder",
        "mix_product": "What they get", "mix_price": "What it costs", "mix_place": "Where they get it",
        "role_control": "Control: current pitch", "role_challenger": "Challenger",
        "seg_size": "Size", "seg_buying_power": "Buying power", "seg_reach_cost": "Cost to reach", "seg_distinct": "Why it differs",
        "growth_penetration": "Existing market", "growth_development": "New market", "growth_reach": "How we reach them",
        "guide": "Who gets what", "guide_note": "When a lead comes from an ad, this says which product to offer.",
        "guide_ad": "Ad", "guide_for": "For", "guide_offer": "Offer",
        "stakes": "Before you spend",
        "stakes_note": "What buyers expect from anyone in this category. If you miss a minimum, fix that before paying for ads.",
        "stake_outcome": "Expected", "stake_minimum": "Minimum", "stake_status": "Status",
        "stake_met": "Met", "stake_unmet": "Not met", "stake_unknown": "Unknown",
    },
    "lt": {
        "slug": "Rocky · reklamų lenta", "ads": "Reklamų", "ready": "Paruošta", "fix": "Reikia pataisyti",
        "sponsored": "Remiama", "more": "Daugiau", "stamp_ok": "Paruošta", "stamp_fix": "Pataisyti",
        "stamp_image": "Be vizualo", "number": "Nr.", "audience": "Auditorija", "copy": "Kopijuoti",
        "copied": "Nukopijuota", "text": "Tekstas", "headline": "Antraštė", "description": "Aprašymas",
        "download": "Atsisiųsti vizualą", "no_visual": "Vizualas dar nesukurtas", "evidence": "Įrodymai",
        "evidence_note": "Iš kur paimtas kiekvienas skaičius šiose reklamose.", "claim": "Teiginys",
        "source": "Šaltinis", "no_claims": "Šiose reklamose nėra teiginių, kuriems reikėtų šaltinio.",
        "why": "Kodėl?", "fix_action": "Taisyti (iki {cap} €)", "ignore": "Ignoruoti", "handled": "Pažymėti kaip sutvarkytą", "undo": "Grąžinti",
        "fixing": "Taisoma…", "command_copied": "Komanda nukopijuota: paleiskite ją kampanijos aplanke",
        "mix_product": "Ką gauna", "mix_price": "Kiek kainuoja", "mix_place": "Kur gauna",
        "role_control": "Kontrolinė: dabartinė žinutė", "role_challenger": "Nauja žinutė",
        "seg_size": "Dydis", "seg_buying_power": "Perkamoji galia", "seg_reach_cost": "Kaina pasiekti", "seg_distinct": "Kuo skiriasi",
        "growth_penetration": "Esama rinka", "growth_development": "Nauja rinka", "growth_reach": "Kaip pasieksime",
        "guide": "Kam ką siūlyti", "guide_note": "Kai užklausa ateina iš reklamos, šis sąrašas pasako, kurį produktą siūlyti.",
        "guide_ad": "Reklama", "guide_for": "Kam", "guide_offer": "Ką siūlyti",
        "stakes": "Prieš leidžiant biudžetą",
        "stakes_note": "Ko pirkėjai tikisi iš bet kurio šios srities pardavėjo. Jei minimumo nepasiekiate, pirma sutvarkykite tai, o ne reklamą.",
        "stake_outcome": "Ko tikimasi", "stake_minimum": "Minimumas", "stake_status": "Būsena",
        "stake_met": "Pasiekta", "stake_unmet": "Nepasiekta", "stake_unknown": "Nežinoma",
    },
}


@dataclass(frozen=True)
class Check:
    level: str  # "block" keeps an ad from launching, "warn" is the marketer's call
    code: str
    ad: int = 0  # 1-based; 0 is the whole pack
    field: str = ""
    value: str = ""
    n: int = 0
    limit: int = 0


def message(check: Check, language: str = "en") -> str:
    field = _FIELD_NAMES[language].get(check.field, check.field)
    return _MESSAGES[language][check.code].format(
        field=field, Field=field.capitalize(), value=check.value, n=check.n, limit=check.limit
    )


def _text(value: Any, limit: int = 3000) -> str:
    return str(value).strip()[:limit] if isinstance(value, (str, int, float)) else ""


def _dicts(value: Any) -> list[dict]:
    """A list of objects. Some models send a nested array as a JSON string."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _object(value: Any) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def normalize(args: dict) -> dict:
    fields = (*COPY_FIELDS, "cta", "angle", "audience", "image")
    return {
        "brand": _text(args.get("brand"), 120),
        "url": _text(args.get("url"), 500),
        "ads": [
            {
                **{name: _text(ad.get(name)) for name in fields},
                "mix": {name: _text(_object(ad.get("mix")).get(name), 300) for name in MIX_FIELDS},
                "segment": {name: _text(_object(ad.get("segment")).get(name), 300) for name in SEGMENT_FIELDS},
                "test_role": role if (role := _text(ad.get("test_role")).lower()) in TEST_ROLES else "",
            }
            for ad in _dicts(args.get("ads"))[:MAX_ADS]
        ],
        "claims": [
            {"text": _text(claim.get("text"), 500), "source": _text(claim.get("source"), 500)}
            for claim in _dicts(args.get("claims"))
            if _text(claim.get("text"))
        ],
        "growth": {
            "kind": kind if (kind := _text(_object(args.get("growth")).get("kind")).lower()) in GROWTH_KINDS else "",
            "reach": _text(_object(args.get("growth")).get("reach"), 400),
        },
        "table_stakes": [
            {
                "outcome": _text(stake.get("outcome"), 200),
                "minimum": _text(stake.get("minimum"), 200),
                "status": status if (status := _text(stake.get("status")).lower()) in STAKE_STATUSES else "unknown",
            }
            for stake in _dicts(args.get("table_stakes"))
            if _text(stake.get("outcome"))
        ],
    }


def _is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _bare_url(value: str) -> str:
    return re.sub(r"https?://(www\.)?", "", value.lower()).rstrip("/")


def check_pack(pack: dict, facts: str | None, images: dict[int, str]) -> list[Check]:
    """What code can settle without an opinion. ``facts`` is None when no facts file exists."""
    checks: list[Check] = []
    if not pack["ads"]:
        checks.append(Check("block", "no_ads"))
    if not _is_url(pack["url"]):
        checks.append(Check("block", "bad_url"))
    known_sources = _bare_url(facts) if facts is not None else ""
    sourced: set[str] = set()
    for claim in pack["claims"]:
        if not _is_url(claim["source"]):
            checks.append(Check("block", "source_bad", value=claim["text"]))
        elif facts is not None and _bare_url(claim["source"]) not in known_sources:
            checks.append(Check("block", "source_unknown", value=claim["text"]))
        else:
            sourced.update(_NUMBER.findall(claim["text"]))
    growth = pack.get("growth") or {}
    if growth.get("kind") == "development" and not growth.get("reach"):
        checks.append(Check("warn", "no_reach"))
    stakes = pack.get("table_stakes") or []
    if not stakes:
        checks.append(Check("warn", "no_stakes"))
    for stake in stakes:
        if stake["status"] == "unmet":
            checks.append(Check("warn", "stake_unmet", value=stake["outcome"]))
    roles = [ad.get("test_role") for ad in pack["ads"]]
    if len(roles) > 1 and (roles.count("control") != 1 or "challenger" not in roles):
        checks.append(Check("warn", "no_test", ad=1))
    for n, ad in enumerate(pack["ads"], 1):
        for name in REQUIRED:
            if not ad[name]:
                checks.append(Check("block", "missing_field", ad=n, field=name))
        for name in COPY_FIELDS:
            copy = re.sub(re.escape(pack["brand"]), " ", ad[name], flags=re.I) if pack["brand"] else ad[name]
            for number in dict.fromkeys(_NUMBER.findall(copy)):
                if number not in sourced:
                    checks.append(Check("block", "number_unsourced", ad=n, field=name, value=number))
            if len(ad[name]) > FEED_LIMITS[name]:
                checks.append(Check("warn", "too_long", ad=n, field=name, n=len(ad[name]), limit=FEED_LIMITS[name]))
            lowered = ad[name].lower()
            for phrase in PROMISES:
                if phrase in lowered:
                    checks.append(Check("warn", "promise", ad=n, field=name, value=phrase))
        mix = ad.get("mix") or {}
        for name in MIX_FIELDS:
            if not mix.get(name):
                checks.append(Check("warn", "mix_missing", ad=n, field=name))
        segment = ad.get("segment") or {}
        for name in SEGMENT_FIELDS if len(pack["ads"]) > 1 else SEGMENT_FIELDS[:-1]:
            if not segment.get(name):
                checks.append(Check("warn", "segment_gap", ad=n, field=name))
        if n not in images:
            checks.append(Check("warn", "no_image", ad=n))
    return checks


def _facts(tools: WorkspaceTools) -> str | None:
    path = tools.root / FACTS_PATH
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None


def _data_uri(tools: WorkspaceTools, rel: str) -> str | None:
    if not rel or not rel.lower().endswith(IMAGE_SUFFIXES):
        return None
    try:
        path = tools.resolve(rel)
    except ToolError:
        return None
    if not path.is_file():
        return None
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _newest_visual(tools: WorkspaceTools, n: int, written: list[str]) -> str:
    """This run's newest visual for ad ``n``. image_generate names a retry visual-1-2.png."""
    pattern = re.compile(rf"deliverables/visual-{n}(-\d+)?\.(png|jpe?g|webp)", re.I)
    hits = [rel for rel in written if pattern.fullmatch(rel)]
    return max(hits, key=lambda rel: (tools.root / rel).stat().st_mtime, default="")


def _attach(pack: dict, tools: WorkspaceTools) -> dict[int, str]:
    """Fill in each ad's visual and return {ad number: data URI}."""
    written = tools.files_on_disk()
    images: dict[int, str] = {}
    for n, ad in enumerate(pack["ads"], 1):
        ad["image"] = _newest_visual(tools, n, written) or ad["image"]
        uri = _data_uri(tools, ad["image"])
        if uri:
            images[n] = uri
    return images


def _keep_earlier_visuals(pack: dict, tools: WorkspaceTools) -> None:
    """A follow-up resends the copy without the image paths; ad numbers keep their visuals."""
    earlier = (_load_pack(tools) or {}).get("ads") or []
    for ad, old in zip(pack["ads"], earlier):
        ad["image"] = ad["image"] or str(old.get("image") or "")


def _switched_on(checks: list[Check], tools: WorkspaceTools) -> list[Check]:
    """Findings from the elements this workspace has not switched off."""
    off = switched_off(tools.root)
    return [c for c in checks if (element := for_code(c.code)) is None or element.number not in off]


def _publish(tools: WorkspaceTools, pack: dict) -> list[Check]:
    images = _attach(pack, tools)
    checks = _switched_on(check_pack(pack, _facts(tools), images), tools)
    tools.write_file(PACK_PATH, json.dumps(pack, ensure_ascii=False, indent=2))
    tools.write_file(BOARD_PATH, render(pack, checks, images))
    return checks


def ad_board_tool(tools: WorkspaceTools, args: dict) -> str:
    """Backs the worker-facing ``ad_board`` tool. Always leaves a page, and says what is still wrong."""
    pack = normalize(args)
    _keep_earlier_visuals(pack, tools)
    checks = _publish(tools, pack)
    blocking = [message(c) for c in checks if c.level == "block"]
    warnings = [message(c) for c in checks if c.level == "warn" and c.code != "no_image"]
    out = f"{BOARD_PATH} written with {len(pack['ads'])} ad(s)."
    if blocking:
        out += " Not ready. Fix these and call ad_board again with the whole pack:\n- " + "\n- ".join(blocking)
    else:
        out += " No blocking problems."
    if warnings:
        out += "\nShown on the page, fix them if you can:\n- " + "\n- ".join(warnings)
    return out


def fix_instruction(check: Check) -> str:
    where = f"Ad {check.ad}" if check.ad else "The ad page"
    return f"Fix one point on the ad page. {where}: {message(check)}"


def fix_plan(plan: dict, instruction: str) -> dict:
    """A follow-up on the page is one known edit, so it needs one worker and no planner."""
    step = {
        "id": 1,
        "title": "Fix",
        "detail": (
            f"{instruction}\nRead runs/notes/ads.json and runs/notes/facts.md. Change only what this asks and keep "
            "every other ad and field as it is. If you need a new fact, web_fetch it and add it to runs/notes/facts.md "
            "with its URL. Then call ad_board with the whole corrected pack."
        ),
        "depends_on": [],
        "writes": [BOARD_PATH],
    }
    return {**{k: v for k, v in plan.items() if k != "resumed"}, "summary": instruction, "steps": [step]}


def current_checks(tools: WorkspaceTools) -> list[Check] | None:
    """What code finds on the saved pack right now, or None when there is no pack."""
    pack = _load_pack(tools)
    if pack is None:
        return None
    images = {n: "" for n, ad in enumerate(pack["ads"], 1) if _data_uri(tools, ad.get("image") or "")}
    return _switched_on(check_pack(pack, _facts(tools), images), tools)


def _load_pack(tools: WorkspaceTools) -> dict | None:
    path = tools.root / PACK_PATH
    try:
        pack = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return pack if isinstance(pack, dict) else None


def is_board(plan: dict) -> bool:
    return str(plan.get("deliverable_path") or "") == BOARD_PATH


def refresh(plan: dict, tools: WorkspaceTools) -> None:
    """After the workers: rebuild this run's page so the visuals they drew are on it."""
    if not is_board(plan) or PACK_PATH not in tools.files_on_disk():
        return
    pack = _load_pack(tools)
    if pack is not None:
        _publish(tools, pack)


def verifier_brief(tools: WorkspaceTools) -> dict | None:
    """The pack and the code's findings, in place of a page that is mostly CSS and images."""
    checks = current_checks(tools)
    if checks is None:
        return None
    pack = _load_pack(tools)
    return {
        "pack": json.dumps(pack, ensure_ascii=False, indent=2),
        "automatic_checks": {
            "note": (
                "Measured by code. Every blocking item is a blocking defect. Do not re-measure length or sourcing. "
                "The page is the whole deliverable: do not require other files, formats or exact pixel sizes "
                "unless the goal names them."
            ),
            "blocking": [message(c) for c in checks if c.level == "block"],
            "warnings": [message(c) for c in checks if c.level == "warn"],
        },
    }


def ready(plan: dict, tools: WorkspaceTools) -> bool:
    """The page exists and code found nothing that keeps an ad from launching."""
    if not is_board(plan) or not (tools.root / BOARD_PATH).is_file():
        return False
    brief = verifier_brief(tools)
    return brief is not None and not brief["automatic_checks"]["blocking"]


def _language(pack: dict) -> str:
    sample = " ".join(ad[name] for ad in pack["ads"] for name in (*COPY_FIELDS, "cta"))
    return "lt" if detect_language(sample) == "lt" else "en"


def _split_for_feed(text: str) -> tuple[str, str]:
    """The part the feed shows and the part behind "See more", cut at a word."""
    limit = FEED_LIMITS["primary_text"]
    if len(text) <= limit:
        return text, ""
    cut = text.rfind(" ", 0, limit + 1)
    cut = cut if cut > 0 else limit
    return text[:cut], text[cut:]


def _feed_card(pack: dict, ad: dict, image: str | None, labels: dict) -> str:
    e = html.escape
    shown, rest = _split_for_feed(ad["primary_text"])
    more = (
        f'<span class="rest" hidden>{e(rest)}</span><button class="more" type="button">… {e(labels["more"])}</button>'
        if rest
        else ""
    )
    media = (
        f'<img src="{e(image)}" alt="">' if image else f'<div class="no-visual"><span>{e(labels["no_visual"])}</span></div>'
    )
    domain = re.sub(r"^www\.", "", urlparse(pack["url"]).netloc)
    description = f'<span class="desc">{e(ad["description"])}</span>' if ad["description"] else ""
    return (
        '<div class="feed">'
        f'<div class="feed-top"><span class="avatar">{e(pack["brand"][:1].upper() or "?")}</span>'
        f'<span class="who"><b>{e(pack["brand"])}</b><small>{e(labels["sponsored"])}</small></span></div>'
        f'<p class="feed-text">{e(shown)}{more}</p>'
        f'<div class="feed-media">{media}</div>'
        f'<div class="feed-link"><div class="link-copy"><small>{e(domain)}</small>'
        f'<b>{e(ad["headline"])}</b>{description}</div><span class="cta">{e(ad["cta"])}</span></div>'
        "</div>"
    )


def _finding(check: Check, language: str) -> str:
    """One thing code found, with why it matters and what can be done about it."""
    e = html.escape
    labels = _LABELS[language]
    element = for_code(check.code)
    fixable = element is None or element.fixable(check.code)
    reason = ""
    why_button = ""
    if element is not None:
        source = f" <cite>{e(element.source)}</cite>" if element.source else ""
        reason = f'<p class="why-text" hidden>{e(element.why[language])}{source}</p>'
        why_button = f'<button type="button" class="why">{e(labels["why"])}</button>'
    fix_button = ""
    if fixable:
        cap = f"{FIX_CAP_EUR:.2f}".replace(".", "," if language == "lt" else ".")
        target = json.dumps({"ad": check.ad, "code": check.code, "field": check.field, "value": check.value}, ensure_ascii=False)
        command = f"rocky resume --budget {FIX_CAP_EUR} {shlex.quote(fix_instruction(check))}"
        fix_button = (
            f'<button type="button" class="fix" data-fix="{e(target, quote=True)}" data-command="{e(command, quote=True)}">'
            f'{e(labels["fix_action"].format(cap=cap))}</button>'
        )
    dismiss_label = labels["ignore"] if fixable else labels["handled"]
    key = f"{check.ad}:{check.code}:{check.field}:{check.value}"
    return (
        f'<li class="{e(check.level)}" data-key="{e(key, quote=True)}"><span class="found">{e(message(check, language))}</span>'
        f'<span class="acts">{why_button}{fix_button}'
        f'<button type="button" class="dismiss" data-label="{e(dismiss_label, quote=True)}">{e(dismiss_label)}</button></span>{reason}</li>'
    )


def _note(n: int, ad: dict, image: str | None, checks: list[Check], language: str) -> str:
    e = html.escape
    labels = _LABELS[language]
    mine = [c for c in checks if c.ad == n]
    if any(c.level == "block" for c in mine):
        stamp, kind = labels["stamp_fix"], "fix"
    elif image is None:
        stamp, kind = labels["stamp_image"], "wait"
    else:
        stamp, kind = labels["stamp_ok"], "ok"
    items = "".join(_finding(c, language) for c in sorted(mine, key=lambda c: c.level))
    audience = (
        f'<p class="audience"><small>{e(labels["audience"])}</small>{e(ad["audience"])}</p>' if ad["audience"] else ""
    )
    role = f' · {e(labels["role_" + ad["test_role"]])}' if ad.get("test_role") else ""
    mix = ad.get("mix") or {}
    offer = "".join(
        f'<div><dt>{e(labels["mix_" + name])}</dt><dd>{e(mix[name])}</dd></div>' for name in MIX_FIELDS if mix.get(name)
    )
    offer = f'<dl class="offer">{offer}</dl>' if offer else ""
    segment = ad.get("segment") or {}
    fit = "".join(
        f'<div><dt>{e(labels["seg_" + name])}</dt><dd>{e(segment[name])}</dd></div>' for name in SEGMENT_FIELDS if segment.get(name)
    )
    fit = f'<dl class="offer">{fit}</dl>' if fit else ""
    buttons = "".join(
        f'<button type="button" data-copy="{e(ad[name], quote=True)}">{e(labels[label])}</button>'
        for name, label in (("primary_text", "text"), ("headline", "headline"), ("description", "description"))
        if ad[name]
    )
    download = ""
    if image:
        filename = f"visual-{n}{Path(ad['image']).suffix.lower()}"
        download = f'<a class="download" href="{e(image)}" download="{e(filename)}">{e(labels["download"])}</a>'
    return (
        '<aside class="note">'
        f'<div class="stamp {kind}">{e(stamp)}</div>'
        f'<h2><small>{e(labels["number"])} {n}{role}</small>{e(ad["angle"])}</h2>'
        f"{audience}{fit}{offer}"
        f'<ul class="checks">{items}</ul>'
        f'<div class="copies"><small>{e(labels["copy"])}</small><div>{buttons}</div>{download}</div>'
        "</aside>"
    )


def _growth_line(pack: dict, labels: dict) -> str:
    e = html.escape
    growth = pack.get("growth") or {}
    if not growth.get("kind"):
        return ""
    reach = f' · {e(labels["growth_reach"])}: {e(growth["reach"])}' if growth.get("reach") else ""
    return f'<p class="growth">{e(labels["growth_" + growth["kind"]])}{reach}</p>'


def _guide(pack: dict, labels: dict) -> str:
    """Which product to offer whoever arrives from each ad (Jobs to Be Done, pp. 113–115). One product needs no guide."""
    e = html.escape
    rows = [(n, ad["audience"], (ad.get("mix") or {}).get("product") or "") for n, ad in enumerate(pack["ads"], 1)]
    rows = [row for row in rows if row[2]]
    if len({product for _, _, product in rows}) < 2:
        return ""
    body = "".join(
        f'<tr><td>{e(labels["number"])} {n}</td><td>{e(audience)}</td><td>{e(product)}</td></tr>' for n, audience, product in rows
    )
    return (
        f'<section class="evidence guide"><h2>{e(labels["guide"])}</h2><p>{e(labels["guide_note"])}</p>'
        f'<table><thead><tr><th>{e(labels["guide_ad"])}</th><th>{e(labels["guide_for"])}</th>'
        f'<th>{e(labels["guide_offer"])}</th></tr></thead><tbody>{body}</tbody></table></section>'
    )


def render(pack: dict, checks: list[Check], images: dict[int, str]) -> str:
    """One self-contained file: no network, and nothing from a page or a model runs as markup."""
    e = html.escape
    language = _language(pack)
    labels = _LABELS[language]
    blocked = {c.ad for c in checks if c.level == "block" and c.ad}
    fix = len(blocked)
    ready_count = sum(1 for n in range(1, len(pack["ads"]) + 1) if n not in blocked and n in images)
    alerts = "".join(_finding(c, language) for c in checks if not c.ad)
    alerts = f'<ul class="alerts">{alerts}</ul>' if alerts else ""
    site = (
        f'<a href="{e(pack["url"], quote=True)}" target="_blank" rel="noopener">{e(_bare_url(pack["url"]))}</a>'
        if _is_url(pack["url"])
        else ""
    )
    proofs = "".join(
        f'<article class="proof" style="--i:{n}">{_feed_card(pack, ad, images.get(n), labels)}'
        f"{_note(n, ad, images.get(n), checks, language)}</article>"
        for n, ad in enumerate(pack["ads"], 1)
    )
    rows = "".join(
        f'<tr><td>{e(claim["text"])}</td><td>'
        + (
            f'<a href="{e(claim["source"], quote=True)}" target="_blank" rel="noopener">{e(_bare_url(claim["source"]))}</a>'
            if _is_url(claim["source"])
            else e(claim["source"])
        )
        + "</td></tr>"
        for claim in pack["claims"]
    )
    stake_rows = "".join(
        f'<tr><td>{e(stake["outcome"])}</td><td>{e(stake["minimum"])}</td>'
        f'<td class="{stake["status"]}">{e(labels["stake_" + stake["status"]])}</td></tr>'
        for stake in pack.get("table_stakes") or []
    )
    stakes = (
        f'<section class="evidence stakes"><h2>{e(labels["stakes"])}</h2><p>{e(labels["stakes_note"])}</p>'
        f'<table><thead><tr><th>{e(labels["stake_outcome"])}</th><th>{e(labels["stake_minimum"])}</th>'
        f'<th>{e(labels["stake_status"])}</th></tr></thead><tbody>{stake_rows}</tbody></table></section>'
        if stake_rows
        else ""
    )
    evidence = (
        f'<table><thead><tr><th>{e(labels["claim"])}</th><th>{e(labels["source"])}</th></tr></thead><tbody>{rows}</tbody></table>'
        if rows
        else f'<p class="none">{e(labels["no_claims"])}</p>'
    )
    return (
        f'<!doctype html><html lang="{language}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; '
        "style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">"
        f'<title>{e(pack["brand"])} · {e(labels["slug"])}</title><style>{_STYLE}</style></head>'
        f'<body data-copied="{e(labels["copied"], quote=True)}" data-undo="{e(labels["undo"], quote=True)}" '
        f'data-fixing="{e(labels["fixing"], quote=True)}" data-command-copied="{e(labels["command_copied"], quote=True)}">'
        f'<header class="mast"><p class="slug">{e(labels["slug"])}</p><h1>{e(pack["brand"])}</h1>{site}'
        f'<dl class="tally"><div><dt>{e(labels["ads"])}</dt><dd>{len(pack["ads"])}</dd></div>'
        f'<div><dt>{e(labels["ready"])}</dt><dd>{ready_count}</dd></div>'
        f'<div class="{"bad" if fix else ""}"><dt>{e(labels["fix"])}</dt><dd>{fix}</dd></div></dl>{_growth_line(pack, labels)}{alerts}</header>'
        f'<main>{proofs}</main>{_guide(pack, labels)}{stakes}'
        f'<section class="evidence"><h2>{e(labels["evidence"])}</h2><p>{e(labels["evidence_note"])}</p>{evidence}</section>'
        f"<script>{_SCRIPT}</script></body></html>"
    )


_STYLE = """
:root{color-scheme:light dark;--paper:#efe9dc;--sheet:#f8f4ea;--ink:#1a1610;--soft:#6d6456;--rule:#cfc6b4;--go:#0b6a4d;--stop:#c5301c;--hold:#a06a00;
--card:#fff;--card-ink:#050505;--card-soft:#65676b;--strip:#f0f2f5;--btn:#e4e6eb;
--serif:"Iowan Old Style","Palatino Linotype","Book Antiqua",Palatino,Georgia,serif;--mono:"SF Mono",ui-monospace,Menlo,Consolas,monospace;--sans:"Avenir Next","Gill Sans","Trebuchet MS",sans-serif}
@media (prefers-color-scheme:dark){:root{--paper:#14110c;--sheet:#1e1a13;--ink:#efe8d9;--soft:#a2977f;--rule:#3b3427;--go:#4fc79d;--stop:#ff7a63;--hold:#e0a93b;
--card:#242526;--card-ink:#e4e6eb;--card-soft:#b0b3b8;--strip:#3a3b3c;--btn:#4e4f50}}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.5 var(--sans);padding:clamp(20px,5vw,72px) clamp(16px,5vw,72px) 96px;
background-image:radial-gradient(var(--rule) .6px,transparent .7px);background-size:22px 22px}
.mast,main,.evidence{max-width:1040px;margin-inline:auto}
.mast{border-bottom:3px double var(--ink);padding-bottom:22px;margin-bottom:44px;display:grid;grid-template-columns:1fr auto;column-gap:32px;align-items:end}
.slug,small,dt,th{font:500 11.5px/1.3 var(--mono);letter-spacing:.09em;text-transform:uppercase;color:var(--soft)}
.slug{margin:0 0 10px;grid-column:1}
h1{font:400 clamp(44px,8vw,86px)/.98 var(--serif);letter-spacing:-.025em;margin:0;grid-column:1;overflow-wrap:anywhere}
.mast>a{grid-column:1;margin-top:10px;color:var(--soft);font:14px var(--mono)}
.tally{grid-column:2;grid-row:1/span 3;display:flex;gap:28px;margin:0}
.tally dd{margin:2px 0 0;font:400 44px/1 var(--serif)}
.tally .bad dd,.tally .bad dt{color:var(--stop)}
.growth{grid-column:1/-1;margin:16px 0 0;font-size:15px}
.alerts{grid-column:1/-1;margin:18px 0 0;padding:12px 16px 12px 32px;border:1px solid var(--stop);color:var(--stop)}
.proof{position:relative;display:grid;grid-template-columns:minmax(0,500px) minmax(0,1fr);gap:clamp(24px,5vw,60px);align-items:start;background:var(--sheet);padding:clamp(16px,3.4vw,36px);margin-bottom:36px;
outline:1px solid var(--rule);animation:rise .55s cubic-bezier(.2,.75,.25,1) both;animation-delay:calc(var(--i)*90ms)}
.proof::before,.proof::after{content:"";position:absolute;width:15px;height:15px;border:1px solid var(--soft)}
.proof::before{top:-7px;left:-7px;border-right:0;border-bottom:0}
.proof::after{right:-7px;bottom:-7px;border-left:0;border-top:0}
@keyframes rise{from{opacity:0;transform:translateY(16px)}}
@keyframes thud{from{opacity:0;transform:rotate(-14deg) scale(1.7)}}
.feed{background:var(--card);color:var(--card-ink);border-radius:9px;overflow:hidden;box-shadow:0 1px 2px rgb(0 0 0/.18),0 14px 34px -22px rgb(0 0 0/.55);font:15px/1.34 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
.feed-top{display:flex;gap:9px;align-items:center;padding:12px 16px 0}
.avatar{width:40px;height:40px;border-radius:50%;background:var(--card-ink);color:var(--card);display:grid;place-items:center;font-weight:700;font-size:18px;flex:none}
.who{display:grid;line-height:1.25}
.who small{font:400 13px/1.3 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;letter-spacing:0;text-transform:none;color:var(--card-soft)}
.feed-text{margin:10px 16px 12px;white-space:pre-line;overflow-wrap:anywhere}
.more{border:0;background:none;padding:0;font:inherit;font-weight:600;color:var(--card-soft);cursor:pointer}
.feed-media img{display:block;width:100%;aspect-ratio:1;object-fit:cover}
.no-visual{aspect-ratio:1;display:grid;place-items:center;color:var(--card-soft);background:repeating-linear-gradient(135deg,var(--strip) 0 14px,transparent 14px 28px)}
.no-visual span{background:var(--card);padding:6px 12px;border-radius:5px;font-size:14px}
.feed-link{display:flex;justify-content:space-between;gap:12px;align-items:center;background:var(--strip);padding:11px 16px}
.link-copy{display:grid;min-width:0;line-height:1.28}
.link-copy small{font:400 12px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;letter-spacing:.01em;color:var(--card-soft)}
.link-copy b{font-size:16px;overflow-wrap:anywhere}
.desc{font-size:14px;color:var(--card-soft)}
.cta{background:var(--btn);border-radius:6px;padding:8px 12px;font-weight:600;font-size:14px;white-space:nowrap}
.note{position:relative;padding-top:6px}
.stamp{display:inline-block;font:700 15px/1 var(--mono);letter-spacing:.14em;text-transform:uppercase;padding:9px 13px;border:3px solid currentColor;border-radius:4px;transform:rotate(-5deg);margin-bottom:24px;animation:thud .34s cubic-bezier(.3,1.5,.5,1) both;animation-delay:calc(var(--i)*90ms + .32s)}
.stamp.ok{color:var(--go)}.stamp.fix{color:var(--stop)}.stamp.wait{color:var(--hold)}
.note h2{font:400 28px/1.14 var(--serif);margin:0 0 18px;overflow-wrap:anywhere}
.note h2 small{display:block;margin-bottom:6px}
.audience{margin:0 0 18px;font-size:15px}.audience small{display:block;margin-bottom:3px}
.offer{margin:0 0 18px;font-size:15px}.offer div{margin-bottom:10px}.offer dt{margin-bottom:3px}.offer dd{margin:0}
.checks{list-style:none;margin:0 0 20px;padding:0;font-size:14.5px}
.checks:empty{display:none}
.checks li{padding:8px 0 8px 14px;border-top:1px solid var(--rule);position:relative}
.checks li::before{content:"";position:absolute;left:0;top:15px;width:7px;height:7px;background:var(--hold)}
.checks li.block{color:var(--stop)}.checks li.block::before{background:var(--stop)}
.acts{display:flex;flex-wrap:wrap;gap:4px 14px;margin-top:4px}
.acts button{border:0;background:none;padding:2px 0;font:600 13px/1.3 var(--sans);color:var(--soft);cursor:pointer;text-decoration:underline;text-underline-offset:3px}
.acts .fix{color:var(--ink)}.acts button:hover{color:var(--stop)}
.why-text{margin:8px 0 2px;font-size:14px;line-height:1.45;color:var(--soft)}.why-text cite{display:block;margin-top:4px;font:12px var(--mono)}
.dismissed .found{text-decoration:line-through;opacity:.55}.dismissed .why,.dismissed .fix,.dismissed .why-text{display:none}
.alerts li{margin-bottom:8px}.alerts .acts button,.alerts .why-text{color:inherit}
.copies>small{display:block;margin-bottom:7px}
.copies div{display:flex;flex-wrap:wrap;gap:8px}
.copies button,.download{font:600 14px/1 var(--sans);color:var(--ink);background:none;border:1px solid var(--ink);padding:10px 14px;cursor:pointer;text-decoration:none;transition:background .12s,color .12s}
.copies button:hover,.download:hover{background:var(--ink);color:var(--sheet)}
.copies button.done{background:var(--go);border-color:var(--go);color:var(--sheet)}
:focus-visible{outline:2px solid var(--stop);outline-offset:2px}
.download{display:inline-block;margin-top:10px}
.evidence{margin-top:64px;border-top:3px double var(--ink);padding-top:22px}
.evidence h2{font:400 34px/1.1 var(--serif);margin:0}
.evidence>p{margin:6px 0 22px;color:var(--soft)}
table{width:100%;border-collapse:collapse;font-size:15px}
th{text-align:left;padding:0 16px 8px 0}
td{border-top:1px solid var(--rule);padding:10px 16px 10px 0;vertical-align:top;overflow-wrap:anywhere}
td a,.mast>a{color:inherit;text-underline-offset:3px}
td:last-child{font:13.5px/1.45 var(--mono);width:42%}
.guide td:last-child{font:inherit;width:34%}.guide td:first-child{white-space:nowrap}
.stakes td:last-child{width:auto;white-space:nowrap}.stakes td.met{color:var(--go)}.stakes td.unmet{color:var(--stop)}.stakes td.unknown{color:var(--hold)}
@media (max-width:760px){.mast{grid-template-columns:1fr}.tally{grid-column:1;grid-row:auto;margin-top:20px}.proof{grid-template-columns:minmax(0,1fr)}td:last-child{width:auto}}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
@media print{body{background:#fff;padding:0}.copies{display:none}.proof{break-inside:avoid;animation:none}}
"""

_SCRIPT = """
async function put(text){
  try{await navigator.clipboard.writeText(text);return}catch(_){}
  const box=document.createElement('textarea');box.value=text;document.body.append(box);box.select();
  document.execCommand('copy');box.remove();
}
const framed=window.parent!==window;
const body=document.body.dataset;
const memory='rocky-board:'+document.title;
let gone=new Set();
function paint(){
  document.querySelectorAll('li[data-key]').forEach(function(row){
    const off=gone.has(row.dataset.key);
    row.classList.toggle('dismissed',off);
    const button=row.querySelector('.dismiss');
    button.textContent=off?body.undo:button.dataset.label;
  });
}
function remember(){
  const list=Array.from(gone);
  try{localStorage.setItem(memory,JSON.stringify(list))}catch(_){}
  if(framed)parent.postMessage({type:'rocky-dismissed',list:list},'*');
}
function height(){if(framed)parent.postMessage({type:'rocky-height',h:document.documentElement.scrollHeight},'*')}
try{gone=new Set(JSON.parse(localStorage.getItem(memory)||'[]'))}catch(_){}
paint();
window.addEventListener('message',function(event){
  if(event.source!==parent||!event.data||event.data.type!=='rocky-restore')return;
  gone=new Set(event.data.list||[]);paint();height();
});
window.addEventListener('resize',height);
if(framed)parent.postMessage({type:'rocky-ready'},'*');
height();
document.addEventListener('click',async function(event){
  const more=event.target.closest('.more');
  if(more){more.previousElementSibling.hidden=false;more.remove();height();return}
  const why=event.target.closest('.why');
  if(why){const text=why.closest('li').querySelector('.why-text');text.hidden=!text.hidden;height();return}
  const dismiss=event.target.closest('.dismiss');
  if(dismiss){const key=dismiss.closest('li').dataset.key;gone.has(key)?gone.delete(key):gone.add(key);paint();remember();return}
  const fix=event.target.closest('.fix');
  if(fix){
    if(framed){parent.postMessage(Object.assign({type:'rocky-fix'},JSON.parse(fix.dataset.fix)),'*');fix.textContent=body.fixing;fix.disabled=true}
    else{await put(fix.dataset.command);fix.textContent=body.commandCopied}
    return;
  }
  const button=event.target.closest('[data-copy]');
  if(!button)return;
  await put(button.dataset.copy);
  const label=button.dataset.label||button.textContent;
  button.dataset.label=label;button.textContent=document.body.dataset.copied;button.classList.add('done');
  clearTimeout(button.timer);
  button.timer=setTimeout(function(){button.textContent=label;button.classList.remove('done')},1400);
});
"""
