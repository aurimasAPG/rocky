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
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from rocky.lang import detect_language
from rocky.tools import ToolError, WorkspaceTools

BOARD_PATH = "deliverables/ads.html"
PACK_PATH = "runs/notes/ads.json"
FACTS_PATH = "runs/notes/facts.md"
MAX_ADS = 10
REQUIRED = ("primary_text", "headline", "cta")
COPY_FIELDS = ("primary_text", "headline", "description")
# The offer behind each ad's promise; the ad itself is the promotion (Principles of Marketing, pp. 32–35).
MIX_FIELDS = ("product", "price", "place")
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
    },
    "lt": {
        "primary_text": "pagrindinis tekstas", "headline": "antraštė", "description": "aprašymas", "cta": "mygtukas",
        "product": "ką pirkėjas gauna", "price": "kiek tai kainuoja", "place": "kur pirkėjas tai gauna",
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
        "mix_product": "What they get", "mix_price": "What it costs", "mix_place": "Where they get it",
    },
    "lt": {
        "slug": "Rocky · reklamų lenta", "ads": "Reklamų", "ready": "Paruošta", "fix": "Reikia pataisyti",
        "sponsored": "Remiama", "more": "Daugiau", "stamp_ok": "Paruošta", "stamp_fix": "Pataisyti",
        "stamp_image": "Be vizualo", "number": "Nr.", "audience": "Auditorija", "copy": "Kopijuoti",
        "copied": "Nukopijuota", "text": "Tekstas", "headline": "Antraštė", "description": "Aprašymas",
        "download": "Atsisiųsti vizualą", "no_visual": "Vizualas dar nesukurtas", "evidence": "Įrodymai",
        "evidence_note": "Iš kur paimtas kiekvienas skaičius šiose reklamose.", "claim": "Teiginys",
        "source": "Šaltinis", "no_claims": "Šiose reklamose nėra teiginių, kuriems reikėtų šaltinio.",
        "mix_product": "Ką gauna", "mix_price": "Kiek kainuoja", "mix_place": "Kur gauna",
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
            }
            for ad in _dicts(args.get("ads"))[:MAX_ADS]
        ],
        "claims": [
            {"text": _text(claim.get("text"), 500), "source": _text(claim.get("source"), 500)}
            for claim in _dicts(args.get("claims"))
            if _text(claim.get("text"))
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


def _publish(tools: WorkspaceTools, pack: dict) -> list[Check]:
    images = _attach(pack, tools)
    checks = check_pack(pack, _facts(tools), images)
    tools.write_file(PACK_PATH, json.dumps(pack, ensure_ascii=False, indent=2))
    tools.write_file(BOARD_PATH, render(pack, checks, images))
    return checks


def ad_board_tool(tools: WorkspaceTools, args: dict) -> str:
    """Backs the worker-facing ``ad_board`` tool. Always leaves a page, and says what is still wrong."""
    pack = normalize(args)
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
    pack = _load_pack(tools)
    if pack is None:
        return None
    images = {n: "" for n, ad in enumerate(pack["ads"], 1) if _data_uri(tools, ad.get("image") or "")}
    checks = check_pack(pack, _facts(tools), images)
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
    items = "".join(
        f'<li class="{e(c.level)}">{e(message(c, language))}</li>'
        for c in sorted(mine, key=lambda c: c.level)
        if c.code != "no_image"
    )
    audience = (
        f'<p class="audience"><small>{e(labels["audience"])}</small>{e(ad["audience"])}</p>' if ad["audience"] else ""
    )
    mix = ad.get("mix") or {}
    offer = "".join(
        f'<div><dt>{e(labels["mix_" + name])}</dt><dd>{e(mix[name])}</dd></div>' for name in MIX_FIELDS if mix.get(name)
    )
    offer = f'<dl class="offer">{offer}</dl>' if offer else ""
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
        f'<h2><small>{e(labels["number"])} {n}</small>{e(ad["angle"])}</h2>'
        f"{audience}{offer}"
        f'<ul class="checks">{items}</ul>'
        f'<div class="copies"><small>{e(labels["copy"])}</small><div>{buttons}</div>{download}</div>'
        "</aside>"
    )


def render(pack: dict, checks: list[Check], images: dict[int, str]) -> str:
    """One self-contained file: no network, and nothing from a page or a model runs as markup."""
    e = html.escape
    language = _language(pack)
    labels = _LABELS[language]
    blocked = {c.ad for c in checks if c.level == "block" and c.ad}
    fix = len(blocked)
    ready_count = sum(1 for n in range(1, len(pack["ads"]) + 1) if n not in blocked and n in images)
    alerts = "".join(f"<li>{e(message(c, language))}</li>" for c in checks if c.level == "block" and not c.ad)
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
        f'<body data-copied="{e(labels["copied"], quote=True)}">'
        f'<header class="mast"><p class="slug">{e(labels["slug"])}</p><h1>{e(pack["brand"])}</h1>{site}'
        f'<dl class="tally"><div><dt>{e(labels["ads"])}</dt><dd>{len(pack["ads"])}</dd></div>'
        f'<div><dt>{e(labels["ready"])}</dt><dd>{ready_count}</dd></div>'
        f'<div class="{"bad" if fix else ""}"><dt>{e(labels["fix"])}</dt><dd>{fix}</dd></div></dl>{alerts}</header>'
        f'<main>{proofs}</main>'
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
document.addEventListener('click',async function(event){
  const more=event.target.closest('.more');
  if(more){more.previousElementSibling.hidden=false;more.remove();return}
  const button=event.target.closest('[data-copy]');
  if(!button)return;
  await put(button.dataset.copy);
  const label=button.dataset.label||button.textContent;
  button.dataset.label=label;button.textContent=document.body.dataset.copied;button.classList.add('done');
  clearTimeout(button.timer);
  button.timer=setTimeout(function(){button.textContent=label;button.classList.remove('done')},1400);
});
"""
