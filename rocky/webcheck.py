"""The web checks a marketer asks for by eye and a worker should run by code.

``web_check`` takes names, queries, pages or a pasted email and answers one
question per ``mode``:

- ``name_check``: how a candidate name is spelled after being heard (LT/EN
  rule tables), what each spelling finds in search, and which spellings
  collide with an existing brand or a dictionary word.
- ``mention_scan``: which search results for a brand are about the brand
  (domain or qualifier present), which are ambiguous, and which qualifier
  separates them best.
- ``policy_finder``: the delivery, returns or exceptions page found by
  following footer / help / FAQ links from a start page within a page
  budget, how many clicks away it is, and the promise fields quoted.
- ``listing_check``: name, address, phone, hours and website on directory
  listings against the canonical record: match / mismatch / not_found.
- ``search_intent``: informational / commercial / transactional /
  navigational / mixed per query from modifier tokens, with the result
  shapes when a searcher is given.
- ``site_crawl``: BFS from the start page; depth and inbound count per page,
  important pages nothing links to, partial flag at the cap.
- ``keyword_inspect``: who ranks for a keyword and what their pages carry
  (host kind, words, prices, FAQ/schema) next to the marketer's own page.
- ``link_prospects``: which candidate pages really link to the target, with
  the anchor and the citing sentence, ranked by topical relevance then reach.
- ``locale_check``: language attribute, hreflang set, currency, date, number
  and phone formats on a page against the expected locale.
- ``kit_check``: a press or partner kit page or file list against the
  required items (logo files, short boilerplate, contact, sized images).
- ``email_auth``: SPF / DKIM / DMARC verdicts parsed from a pasted raw email;
  DNS record lookups are reported under ``missing`` (no resolver here).
- ``search_region``: region-aware query variants for a market; the searcher
  has no region parameter yet, so the region itself lands under ``missing``.

No network inside the module: pages come through an injected ``fetch_html``
callable, link metadata through ``fetch_meta`` (``{status, final_url,
headers}``), searches through ``search`` (lines of ``- title\\n  url``).
Without a callable the url or query lands under ``missing``, never guessed.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, deque
from statistics import median
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

from rocky.pageinspect import LEVEL_ORDER, content_words, detect_language, extract_page, same_stem
from rocky.tools import ToolError, WorkspaceTools

MODES = (
    "name_check", "mention_scan", "policy_finder", "listing_check", "search_intent", "site_crawl",
    "keyword_inspect", "link_prospects", "locale_check", "kit_check", "email_auth", "search_region",
)

MAX_NAMES = 10
MAX_VARIANTS = 8
COLLISION_REPEATS = 3
DEFAULT_POLICY_PAGES = 15
DEFAULT_CRAWL_PAGES = 300
DEFAULT_CRAWL_DEPTH = 4
MAX_ORPHAN_FETCH = 20
MAX_RESULT_URLS = 10
DEFAULT_MAX_FETCH = 50
BOILERPLATE_MAX_WORDS = 100
NOISE_SHARE = 0.5
STRONG_WORDS = 1000
CONTEXT_PAD = 200

_TOKEN = re.compile(r"\w+", re.UNICODE)
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_DIGITS = re.compile(r"\D+")
_PHONE = re.compile(r"(?<!\d)(\+?\d[\d\s\-()]{6,}\d)(?!\d)")
_TIME_RANGE = re.compile(r"(\d{1,2})[:.](\d{2})\s*[–\-—]\s*(\d{1,2})[:.](\d{2})")
_HREFLANG = re.compile(r"<link\b[^>]*\bhreflang\s*=\s*[\"']([^\"']+)[\"'][^>]*>", re.IGNORECASE)
_HREF_IN_TAG = re.compile(r"\bhref\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)
_GENERIC_TITLE_WORDS = frozenset({
    "the", "and", "for", "with", "from", "your", "our", "online", "official", "home", "page", "site", "lietuva", "lithuania",
    "lietuvoje", "vilnius", "kaunas", "www", "com", "app", "apps", "store", "google", "play", "free", "best", "new", "all",
    "ir", "bei", "apie", "kaip", "kas", "jūsų", "mūsų", "internetu", "parduotuvė", "e-parduotuvė",
})
_DICTIONARY_TITLE = re.compile(r"(dictionary|žodyn|meaning|reikšm|definition|apibrėž|translat|vertim|thesaurus|wiktionary)", re.IGNORECASE)

# how a listener of each language types what they heard
_HEARD_RULES: dict[str, list[tuple[str, str]]] = {
    "en": [
        ("ž", "zh"), ("š", "sh"), ("č", "ch"), ("ė", "e"), ("ę", "e"), ("ą", "a"), ("į", "i"), ("ų", "u"), ("ū", "u"),
        ("kv", "qu"), ("qv", "qu"), ("c", "ts"), ("j", "y"), ("ai", "i"), ("ei", "ay"), ("uo", "wo"), ("ie", "ye"),
    ],
    "lt": [
        ("qu", "kv"), ("q", "k"), ("w", "v"), ("x", "ks"), ("ck", "k"), ("ph", "f"), ("sh", "š"), ("ch", "č"), ("th", "t"),
        ("ee", "y"), ("oo", "ū"), ("y", "i"), ("c", "k"),
    ],
}

_TOPIC_LINKS = {
    "delivery": re.compile(r"(pristatym|deliver|shipping|siunt|atsi[eė]m|dostav|pieg[aā]d|tarne|versand|lieferung)", re.IGNORECASE),
    "returns": re.compile(r"(gr[aą][žz]in|return|refund|keitim|exchange|atgrie|tagast|r[üu]ckgabe|retour|zwrot)", re.IGNORECASE),
    "exceptions": re.compile(r"(v[eė]l(uoj|av|uoja)|delay|late\b|problem|skund|pretenz|complaint|exception|nesklandum|garantij|warranty)", re.IGNORECASE),
}
_HOP_LINKS = re.compile(r"(faq|d\.?u\.?k\b|dažniausiai|klausim|\bhelp\b|pagalba|support|\binfo\b|s[aą]lygos|terms|taisykl|klientams|customer|service|aptarnavim)", re.IGNORECASE)
_CARRIERS = ("omniva", "dpd", "lp express", "venipak", "itella", "smartpost", "ups", "dhl", "fedex", "gls", "unisend", "lietuvos paštas", "post")
_DAY_UNIT = r"(?:d\.\s*d\.|darbo\s*dien\w*|dien\w*|d\.|working\s*days?|business\s*days?|days?)"

_INTENT_TOKENS: dict[str, dict[str, tuple[str, ...]]] = {
    "informational": {
        "lt": ("kaip", "kas yra", "kodėl", "kada", "gidas", "patarimai", "vietos", "idėjos", "instrukcija", "receptas", "istorija", "reikšmė"),
        "en": ("how", "how to", "what is", "why", "when", "guide", "tips", "places", "ideas", "tutorial", "recipe", "history", "meaning"),
    },
    "commercial": {
        "lt": ("geriausi", "geriausias", "geriausios", "geriausia", "top", "vs", "palyginimas", "palyginti", "apžvalga", "atsiliepimai", "kokį rinktis", "kokį", "kurį", "alternatyva"),
        "en": ("best", "top", "vs", "comparison", "compare", "review", "reviews", "which", "alternative", "alternatives"),
    },
    "transactional": {
        "lt": ("pirkti", "pirk", "kaina", "kainos", "užsakyti", "užsakymas", "nuolaida", "akcija", "pigiai", "pigūs", "parduotuvė", "internetu", "nemokamai", "atsisiųsti"),
        "en": ("buy", "price", "prices", "order", "discount", "cheap", "shop", "coupon", "deal", "download", "free", "for sale", "near me"),
    },
}
_PLACE_CONTEXT = re.compile(r"(viet\w*|places?|kur\b|where|maršrut|trail|route|kelion|trip|miest|city|cities|restoran|restaurant|hotel|viešbu|paplūdim|beach|park)", re.IGNORECASE)
_SERVICE_CONTEXT = re.compile(r"(paslaug\w*|service|klinik|clinic|salon|remont|repair|kurs\w*|lessons?|pamok|dentist|odontolog|advokat|lawyer|agent[uū]r|agency)", re.IGNORECASE)
_SHAPE_RULES = (
    ("forum", re.compile(r"(forum|reddit|quora|diskusij|thread|atsakymai\.lt)", re.IGNORECASE)),
    ("news", re.compile(r"(delfi|15min|lrytas|lrt\.lt|vz\.lt|verslo žinios|bns|news|naujien)", re.IGNORECASE)),
    ("comparison", re.compile(r"(palygin|comparison|compare|\bvs\b|apžvalg|review|testas|test\b|geriausi\w*\s+\d|best\s+\d|top\s*\d)", re.IGNORECASE)),
    ("list_guide", re.compile(r"(\d+\s+(viet|places|idėj|ideas|patarim|tips)|gidas|guide|kaip\b|how\b|patarim|tips|ką\s+(žinoti|reikia)|viskas apie)", re.IGNORECASE)),
    ("product_category", re.compile(r"(pirkti|kaina|kainos|buy|price|€|parduotuv|shop|e-shop|eshop|internetu|katalog|catalog|prekės|products?)", re.IGNORECASE)),
)
_AGGREGATOR_HOSTS = re.compile(r"(rekvizitai|1588|118\.lt|visalietuva|kainos\.lt|kaina24|yelp|tripadvisor|trustpilot|booking\.com|google\.|facebook\.|katalog|catalog|yellow|infobel|cylex|firmos\.lt|imones\.lt)", re.IGNORECASE)
_PORTAL_HOSTS = re.compile(r"(delfi|15min|lrytas|lrt\.lt|vz\.lt|ve\.lt|bernardinai|wikipedia|wiki|alfa\.lt|tv3\.lt|diena\.lt|kauno\.diena)", re.IGNORECASE)
_CLINIC_HOSTS = re.compile(r"(klinik|clinic|dent|odont|med\b|medic|sveikat|health|gydy|doctor|dr\.)", re.IGNORECASE)
_SHOP_HOSTS = re.compile(r"(parduotuv|shop|store|e-shop|eshop|pigu\.lt|varle|senukai|topocentras|kilobaitas|skytech)", re.IGNORECASE)
_FAQ_HEADING = re.compile(r"(faq|d\.?u\.?k\b|dažniausiai užduodami|klausimai|questions)", re.IGNORECASE)

_LOCALES: dict[str, dict[str, Any]] = {
    "lt": {"currency": ("€", "eur"), "decimal": ",", "dates": ("ymd_dash", "ymd_dot"), "phone": "370", "name": "Lithuanian"},
    "lv": {"currency": ("€", "eur"), "decimal": ",", "dates": ("dmy_dot",), "phone": "371", "name": "Latvian"},
    "et": {"currency": ("€", "eur"), "decimal": ",", "dates": ("dmy_dot",), "phone": "372", "name": "Estonian"},
    "pl": {"currency": ("zł", "pln"), "decimal": ",", "dates": ("dmy_dot",), "phone": "48", "name": "Polish"},
    "de": {"currency": ("€", "eur"), "decimal": ",", "dates": ("dmy_dot",), "phone": "49", "name": "German"},
    "fi": {"currency": ("€", "eur"), "decimal": ",", "dates": ("dmy_dot",), "phone": "358", "name": "Finnish"},
    "sv": {"currency": ("kr", "sek"), "decimal": ",", "dates": ("ymd_dash",), "phone": "46", "name": "Swedish"},
    "en-gb": {"currency": ("£", "gbp"), "decimal": ".", "dates": ("dmy_slash",), "phone": "44", "name": "British English"},
    "en-us": {"currency": ("$", "usd"), "decimal": ".", "dates": ("mdy_slash",), "phone": "1", "name": "American English"},
    "en": {"currency": ("€", "eur", "£", "gbp", "$", "usd"), "decimal": ".", "dates": ("dmy_slash", "mdy_slash", "ymd_dash"), "phone": None, "name": "English"},
}
_DATE_SHAPES = {
    "ymd_dash": re.compile(r"\b(19|20)\d{2}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b"),
    "ymd_dot": re.compile(r"\b(19|20)\d{2}\.(0[1-9]|1[0-2])\.(0[1-9]|[12]\d|3[01])\b"),
    "dmy_dot": re.compile(r"\b(0?[1-9]|[12]\d|3[01])\.(0?[1-9]|1[0-2])\.((19|20)\d{2})\b"),
    "dmy_slash": re.compile(r"\b(0?[1-9]|[12]\d|3[01])/(0?[1-9]|1[0-2])/((19|20)\d{2})\b"),
    "mdy_slash": re.compile(r"\b(0?[1-9]|1[0-2])/(0?[1-9]|[12]\d|3[01])/((19|20)\d{2})\b"),
}
_CURRENCY_TOKENS = re.compile(r"(€|\$|£|zł|\bkr\b|\beur\b|\busd\b|\bgbp\b|\bpln\b|\bsek\b)", re.IGNORECASE)
_NUMBER_EN = re.compile(r"\b\d{1,3}(?:,\d{3})+\.\d{1,2}\b")
_NUMBER_EU = re.compile(r"\b\d{1,3}(?:[ . ]\d{3})+,\d{1,2}\b")
_INTL_PHONE = re.compile(r"\+(\d{1,3})[\s\-]?\d[\d\s\-]{5,}\d")
_REGIONS = {
    "lt-lt": ("lt", "Lithuania"), "lv-lv": ("lv", "Latvia"), "ee-et": ("ee", "Estonia"), "et-ee": ("ee", "Estonia"), "pl-pl": ("pl", "Poland"),
    "de-de": ("de", "Germany"), "uk-en": ("uk", "United Kingdom"), "en-gb": ("uk", "United Kingdom"), "us-en": ("com", "United States"),
    "fi-fi": ("fi", "Finland"), "se-sv": ("se", "Sweden"), "no-no": ("no", "Norway"), "dk-da": ("dk", "Denmark"),
}
_LOGO_FILE = re.compile(r"logo", re.IGNORECASE)
_VECTOR_EXT = re.compile(r"\.(svg|eps|ai|pdf)(\?|$)", re.IGNORECASE)
_RASTER_EXT = re.compile(r"\.(png|jpe?g|webp|gif|tiff?)(\?|$)", re.IGNORECASE)
_SIZE_IN_NAME = re.compile(r"(\d{3,4})\s*[x×]\s*(\d{3,4})", re.IGNORECASE)
_BOILERPLATE_HINT = re.compile(r"(boilerplate|apie\s+(mus|įmon|bendrov)|about\s+(us|the company)|įkurt|founded|is a\b|yra\b)", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_AUTH_RESULT = re.compile(r"\b(spf|dkim|dmarc|arc)\s*=\s*(pass|fail|softfail|neutral|none|temperror|permerror|policy|bestguesspass)", re.IGNORECASE)


# --- small shared helpers -----------------------------------------------------


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").replace("\xa0", " ")).strip()


def _strip_diacritics(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", str(text or "")) if not unicodedata.combining(ch))


def _finding(code: str, level: str, where: str | None, value: Any, note: str) -> dict[str, Any]:
    return {"code": code, "level": level, "where": where, "value": value, "note": note}


def _finish(mode: str, body: dict[str, Any], findings: list[dict], missing: list[str], warnings: list[str], summary: str) -> dict[str, Any]:
    findings.sort(key=lambda f: LEVEL_ORDER.get(f["level"], 9))
    counts = {level: sum(1 for f in findings if f["level"] == level) for level in ("block", "warn", "info")}
    summary = _clean(summary)
    if summary and not summary.endswith("."):
        summary += "."
    out = {"mode": mode}
    out.update(body)
    out.update({"findings": findings, "counts": counts, "missing": missing, "warnings": warnings, "summary": summary})
    return out


def _list_arg(value: Any, key: str) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [v.strip() for v in re.split(r"[\n,]", value) if v.strip()]
    if isinstance(value, (list, tuple)):
        return [v for v in value if v not in (None, "")]
    raise ToolError(f"{key} must be a list")


def _int_arg(args: dict, key: str, default: int, *, minimum: int = 1) -> int:
    raw = args.get(key)
    if raw in (None, ""):
        return default
    try:
        value = int(float(str(raw).strip()))
    except ValueError as exc:
        raise ToolError(f"{key} must be a whole number, got {raw!r}") from exc
    return max(minimum, value)


def _host(url: str) -> str:
    host = (urlsplit(str(url or "")).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _normalise_url(url: str) -> str:
    p = urlsplit(str(url or "").strip())
    path = p.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    out = f"{_host(url)}{path}"
    if p.query:
        out += "?" + p.query
    return out


def _parse_search(text: Any) -> tuple[list[dict[str, Any]], str | None]:
    """[{rank, title, url, domain}] from search lines, or (empty, reason)."""
    text = str(text or "")
    results: list[dict[str, Any]] = []
    title: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("- "):
            title = _clean(line[2:])
        elif line.strip().startswith(("http://", "https://")) and title is not None:
            url = line.strip()
            results.append({"rank": len(results) + 1, "title": title, "url": url, "domain": _host(url)})
            title = None
    if not results:
        return [], (text.strip().splitlines()[0] if text.strip() else "no results")
    return results, None


def _run_search(search: Callable[[str], str] | None, query: str, missing: list[str]) -> list[dict[str, Any]] | None:
    """Results for one query, or None (and a missing note) when the query could not be searched."""
    if search is None:
        missing.append(f"search: {query!r} not searched (no searcher here); run web_search and paste the results")
        return None
    try:
        text = search(query)
    except Exception as exc:
        missing.append(f"search: {query!r} failed ({exc})")
        return None
    results, _reason = _parse_search(text)
    return results


_ABBREV_END = re.compile(r"(?:\b(?:el|d|pr|g|al|tel|nr|val|t|y|pvz|m|mln|tūkst|dr|str|no|approx|etc|vs|e\.g|i\.e)|\b\d+|\b[A-Za-zĄ-Žą-ž])\.$", re.IGNORECASE)


def _sentences(text: str) -> list[str]:
    """Sentences of the text; 'el. paštu', '1–2 d. d.' and '1. Step' stay in one piece."""
    out: list[str] = []
    for part in _SENTENCE_SPLIT.split(_clean(text)):
        part = part.strip()
        if not part:
            continue
        if out and _ABBREV_END.search(out[-1]):
            out[-1] = f"{out[-1]} {part}"
        else:
            out.append(part)
    return out


def _word_in(word: str, text: str) -> bool:
    w = _strip_diacritics(word).casefold()
    return any(same_stem(w, _strip_diacritics(t).casefold()) for t in _WORD.findall(text or ""))


# --- page sources -----------------------------------------------------------


def _source_of(item: Any) -> tuple[str, str]:
    if isinstance(item, str):
        value = item.strip()
        if not value:
            raise ToolError("a page entry is empty")
        if value.startswith(("http://", "https://")):
            return "url", value
        if "<" in value and ">" in value:
            return "html", value
        return "path", value
    if not isinstance(item, dict):
        raise ToolError("each page must be {url|path|html} or a string")
    given = [k for k in ("html", "path", "url") if item.get(k) not in (None, "")]
    if not given:
        raise ToolError("a page has no source: give html, a workspace path, or a url")
    if len(given) > 1:
        raise ToolError(f"give one page source, not {', '.join(given)}")
    return given[0], str(item[given[0]]).strip()


def _read_source(tools: WorkspaceTools | None, kind: str, value: str, fetch_html: Callable[[str], str] | None, missing: list[str]) -> str | None:
    if kind == "html":
        return value
    if kind == "path":
        if tools is None:
            raise ToolError("a path needs a workspace")
        target = tools.resolve(value)
        if not target.is_file():
            raise ToolError(f"path missing: {value}")
        return target.read_text(encoding="utf-8", errors="replace")
    return _fetch(fetch_html, value, missing)


def _fetch(fetch_html: Callable[[str], str] | None, url: str, missing: list[str]) -> str | None:
    if fetch_html is None:
        missing.append(f"page: {url} not fetched (no fetcher here); give the page as html or path, or open it with the browser tools")
        return None
    try:
        html = fetch_html(url)
    except Exception as exc:
        missing.append(f"page: {url} could not be fetched ({exc})")
        return None
    if not str(html or "").strip():
        missing.append(f"page: {url} came back empty")
        return None
    return str(html)


def _single_page(tools: WorkspaceTools | None, args: dict, fetch_html, missing: list[str]) -> tuple[str | None, str | None]:
    """(label, html) for a page given as html / path / url / page / text in args; (None, None) when nothing was given."""
    if isinstance(args.get("page"), (dict, str)):
        kind, value = _source_of(args["page"])
    else:
        given = [k for k in ("html", "path", "url") if args.get(k) not in (None, "")]
        if not given:
            return None, None
        kind, value = given[0], str(args[given[0]]).strip()
    label = value if kind != "html" else "html"
    return label, _read_source(tools, kind, value, fetch_html, missing)


def _absolute_links(page: dict[str, Any], base_url: str | None) -> list[dict[str, Any]]:
    base = page.get("base_href") or base_url or ""
    out = []
    for link in page.get("links", []):
        href = str(link.get("href") or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#", "data:")):
            continue
        absolute = urljoin(base, href) if base else href
        absolute = absolute.split("#", 1)[0]
        if not absolute.startswith(("http://", "https://")):
            continue
        item = dict(link)
        item["url"] = absolute
        out.append(item)
    return out


# --- name_check ---------------------------------------------------------------


def _apply_rules(name: str, rules: list[tuple[str, str]]) -> str:
    out = name
    for src, dst in rules:
        out = out.replace(src, dst)
    return out


def _heard_spellings(name: str, langs: list[str]) -> list[dict[str, Any]]:
    """Spellings a listener in each language would type after hearing the name."""
    base = _clean(name).casefold()
    seen: dict[str, dict[str, Any]] = {base: {"spelling": base, "lang": "exact", "rule": "as written"}}
    plain = _strip_diacritics(base)
    if plain != base:
        seen.setdefault(plain, {"spelling": plain, "lang": "any", "rule": "diacritics dropped"})
    for lang in langs:
        rules = _HEARD_RULES.get(lang)
        if not rules:
            continue
        whole = _apply_rules(base, rules)
        if whole != base:
            seen.setdefault(whole, {"spelling": whole, "lang": lang, "rule": "all letter swaps"})
        for src, dst in rules:
            if src in base:
                single = base.replace(src, dst)
                if single != base:
                    seen.setdefault(single, {"spelling": single, "lang": lang, "rule": f"{src}→{dst}"})
    return list(seen.values())[:MAX_VARIANTS + 1]


def _title_brand_tokens(title: str, ignore: set[str]) -> set[str]:
    out = set()
    for tok in _WORD.findall(title):
        low = tok.casefold()
        if len(tok) < 3 or low in _GENERIC_TITLE_WORDS or low in ignore or not tok[0].isupper():
            continue
        if any(same_stem(low, ig) for ig in ignore):
            continue
        out.add(low)
    return out


def _collisions(results: list[dict[str, Any]], ignore: set[str]) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    for r in results:
        for tok in _title_brand_tokens(r["title"], ignore):
            counter[tok] += 1
    out = [{"kind": "brand", "token": tok, "titles": n} for tok, n in counter.most_common() if n >= COLLISION_REPEATS]
    dictionary_hits = sum(1 for r in results if _DICTIONARY_TITLE.search(r["title"]) or _DICTIONARY_TITLE.search(r["url"]))
    if dictionary_hits >= 2:
        out.append({"kind": "common_word", "token": None, "titles": dictionary_hits})
    return out


def _mode_name_check(args: dict, search) -> dict[str, Any]:
    names = [_clean(n) for n in _list_arg(args.get("names") or args.get("name"), "names") if _clean(n)]
    if not names:
        raise ToolError("names is required: 1-10 candidate names")
    if len(names) > MAX_NAMES:
        raise ToolError(f"at most {MAX_NAMES} names per call")
    langs = [str(v).strip().lower()[:2] for v in _list_arg(args.get("langs") or args.get("languages"), "langs")] or ["lt", "en"]
    category = _clean(args.get("category"))
    category_words = content_words(category) or [w.casefold() for w in _WORD.findall(category)]
    intended_url = _clean(args.get("intended_url"))
    intended_host = _host(intended_url) if intended_url else None
    findings: list[dict] = []
    missing: list[str] = []
    warnings: list[str] = []
    if not category and not intended_host:
        warnings.append("no category and no intended_url: findability not assessed, only spellings and collisions")
    unknown_langs = [lang for lang in langs if lang not in _HEARD_RULES]
    if unknown_langs:
        warnings.append(f"no hearing rules for {', '.join(unknown_langs)}; only lt/en spellings generated")
    missing.append("domain availability: no DNS or whois here; check .lt/.com with the domain tools and record the date")

    out_names = []
    for name in names:
        spellings = _heard_spellings(name, langs)
        ignore = {s["spelling"] for s in spellings} | set(category_words)
        per_variant = []
        name_collisions = []
        intended_found = False
        category_found = False
        for spelling in spellings:
            results = _run_search(search, spelling["spelling"], missing)
            row = dict(spelling)
            if results is None:
                row.update({"status": "search_unavailable", "top_domains": [], "collisions": []})
            elif not results:
                row.update({"status": "no_results", "top_domains": [], "collisions": []})
            else:
                top = results[:5]
                domains = [r["domain"] for r in top]
                hit_intended = bool(intended_host and any(d == intended_host or d.endswith("." + intended_host) for d in domains))
                hit_category = bool(category_words and any(any(_word_in(w, r["title"]) for w in category_words) for r in top))
                collisions = _collisions(top, ignore)
                status = "points_to_intended" if hit_intended else "points_to_category" if hit_category else "points_elsewhere"
                row.update({"status": status, "top_domains": domains, "collisions": collisions})
                intended_found = intended_found or hit_intended
                category_found = category_found or hit_category
                for c in collisions:
                    name_collisions.append({"spelling": spelling["spelling"], **c})
            per_variant.append(row)
        distinct = [s for s in spellings if s["lang"] != "exact"]
        searched = [v for v in per_variant if v["status"] not in ("search_unavailable",)]
        reasons = []
        score = 0
        if len(distinct) <= 2:
            score += 1
            reasons.append(f"{len(distinct)} heard spelling(s) besides the name itself")
        else:
            reasons.append(f"{len(distinct)} heard spellings: listeners will type it several ways")
        if searched and not name_collisions:
            score += 1
            reasons.append("no collision with another brand or a dictionary word in the top results")
        elif name_collisions:
            tokens = sorted({c["token"] or "dictionary pages" for c in name_collisions})
            reasons.append(f"collides with {', '.join(tokens)}")
            findings.append(_finding("name_collision", "warn", name, tokens, f"{name}: top results for a heard spelling belong to {', '.join(tokens)}"))
        else:
            reasons.append("collisions not assessed: search unavailable")
        if not category and not intended_host:
            findability = "not_assessed"
        elif not searched:
            findability = "search_unavailable"
        elif intended_found:
            findability = "intended_found"
            score += 1
        elif category_found:
            findability = "category_found"
            score += 1
        else:
            findability = "not_found"
            findings.append(_finding("name_not_findable", "warn", name, findability, f"{name}: neither the intended site nor the category appears in the top results of any spelling"))
        reasons.append(f"findability: {findability}")
        out_names.append({
            "name": name, "spellings": per_variant, "collisions": name_collisions, "findability": findability,
            "domain_status": {"lt": "unknown", "com": "unknown"}, "score": score, "score_max": 3, "reasons": reasons,
        })
    best = max(out_names, key=lambda n: (n["score"], -len(n["collisions"]))) if out_names else None
    summary = f"{len(out_names)} name(s) tested"
    if best:
        summary += f"; {best['name']} scores {best['score']}/3"
    summary += "; verdict untested_with_buyers: ask 5 customers to say it to a friend before deciding"
    return _finish("name_check", {"names": out_names, "langs": langs, "category": category or None, "intended_url": intended_url or None,
                                  "verdict": "untested_with_buyers"}, findings, missing, warnings, summary)


# --- mention_scan -------------------------------------------------------------


def _mode_mention_scan(args: dict, search) -> dict[str, Any]:
    brand = _clean(args.get("brand"))
    if not brand:
        raise ToolError("brand is required")
    domain = _host(args.get("domain") or "") or _clean(args.get("domain")).lower()
    qualifiers = [_clean(q) for q in _list_arg(args.get("qualifiers"), "qualifiers") if _clean(q)]
    variants = [_clean(v) for v in _list_arg(args.get("variants"), "variants") if _clean(v)]
    products = [_clean(p) for p in _list_arg(args.get("products"), "products") if _clean(p)]
    max_results = _int_arg(args, "max_results", 60)
    findings: list[dict] = []
    missing: list[str] = []
    warnings: list[str] = []
    if not variants:
        plain = _strip_diacritics(brand)
        if plain.casefold() != brand.casefold():
            variants.append(plain)
        for p in products[:3]:
            variants.append(f"{brand} {p}")
    if not qualifiers:
        warnings.append("no qualifiers; cannot disambiguate: results without the domain stay ambiguous")
    queries = [brand] + [v for v in variants if v.casefold() != brand.casefold()] + [f"{brand} {q}" for q in qualifiers[:5]]
    brand_low = _strip_diacritics(brand).casefold()
    qualifier_hits: Counter[str] = Counter()
    mentions: list[dict[str, Any]] = []
    per_query = []
    seen_urls: set[str] = set()
    for query in queries:
        results = _run_search(search, query, missing)
        if results is None:
            per_query.append({"query": query, "status": "search_unavailable", "results": 0, "ours": 0, "ambiguous": 0, "other": 0})
            continue
        counts = Counter()
        for r in results:
            if len(mentions) >= max_results:
                break
            key = _normalise_url(r["url"])
            if key in seen_urls:
                continue
            seen_urls.add(key)
            title_plain = _strip_diacritics(r["title"]).casefold()
            matched = [q for q in qualifiers if _word_in(q, r["title"]) or _strip_diacritics(q).casefold() in r["url"].casefold()]
            on_domain = bool(domain) and (r["domain"] == domain or r["domain"].endswith("." + domain))
            brand_present = brand_low in title_plain or brand_low.replace(" ", "") in r["url"].casefold()
            if on_domain or matched:
                entity = "ours"
                for q in matched:
                    qualifier_hits[q] += 1
            elif brand_present:
                entity = "ambiguous"
            else:
                entity = "other"
            counts[entity] += 1
            mentions.append({"query": query, "url": r["url"], "title": r["title"], "entity": entity, "matched_qualifiers": matched, "on_domain": on_domain})
        total = sum(counts.values())
        per_query.append({"query": query, "status": "ok" if total else "no_results", "results": total, "ours": counts["ours"], "ambiguous": counts["ambiguous"], "other": counts["other"]})
    totals = Counter(m["entity"] for m in mentions)
    n = len(mentions)
    ambiguity_share = round((totals["ambiguous"] + totals["other"]) / n, 2) if n else None
    noise_queries = [q["query"] for q in per_query if q["results"] and (q["ambiguous"] + q["other"]) / q["results"] > NOISE_SHARE]
    suggested = [q for q, _ in qualifier_hits.most_common()]
    if domain and any(m["on_domain"] for m in mentions):
        suggested.append(domain)
    best_qualifier = suggested[0] if suggested else None
    if ambiguity_share is not None and ambiguity_share > NOISE_SHARE:
        findings.append(_finding("brand_ambiguous", "block", brand, ambiguity_share, f"{int(ambiguity_share * 100)}% of results are not confirmed as {brand}: count only 'ours' rows as mentions"))
    for q in noise_queries:
        findings.append(_finding("noise_query", "warn", q, None, f"query {q!r}: more than half of its results are about something else"))
    if n and not qualifiers:
        findings.append(_finding("no_qualifiers", "warn", brand, None, "without qualifiers nothing can be labelled ours except pages on the brand domain"))
    summary = f"{n} result(s) for {len(queries)} queries: {totals['ours']} ours, {totals['ambiguous']} ambiguous, {totals['other']} other"
    if best_qualifier:
        summary += f"; {best_qualifier!r} disambiguates best"
    return _finish("mention_scan", {"brand": brand, "domain": domain or None, "queries": queries, "per_query": per_query, "mentions": mentions,
                                    "counts_by_entity": dict(totals), "ambiguity_share": ambiguity_share, "noise_queries": noise_queries,
                                    "best_qualifier": best_qualifier, "suggested_qualifiers": suggested}, findings, missing, warnings, summary)


# --- policy_finder -------------------------------------------------------------


def _days_in(low: str) -> tuple[int | None, int | None]:
    m = re.search(r"(\d+)\s*[–\-—]\s*(\d+)\s*" + _DAY_UNIT, low)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(?:per|within|in|iki|up to|until)\s*(\d+)\s*" + _DAY_UNIT, low)
    if m:
        return int(m.group(1)), int(m.group(1))
    m = re.search(r"(\d+)\s*" + _DAY_UNIT, low)
    if m:
        return int(m.group(1)), int(m.group(1))
    return None, None


def _channel_in(low: str) -> str | None:
    if re.search(r"el\.\s*pašt|e-?mail|elektronin", low):
        return "email"
    if re.search(r"\bsms\b|žinut|text message", low):
        return "sms"
    if re.search(r"telefon|phone|skambin|call", low):
        return "phone"
    if re.search(r"sekim|tracking|track", low):
        return "tracking_page"
    return None


def _policy_fields(topic: str, text: str) -> tuple[dict[str, Any], dict[str, str]]:
    fields: dict[str, Any] = {}
    quotes: dict[str, str] = {}

    def put(field: str, value: Any, sentence: str) -> None:
        if field not in fields and value not in (None, "", []):
            fields[field] = value
            quotes[field] = sentence[:300]

    for sentence in _sentences(text):
        low = sentence.casefold()
        if topic in ("delivery", "exceptions") and re.search(r"pristat|deliver|shipping|siun[tč]|atsi[eė]m|dostav", low):
            lo, hi = _days_in(low)
            if lo is not None:
                put("delivery_days_min", lo, sentence)
                put("delivery_days_max", hi, sentence)
            if re.search(r"užsak|order", low):
                m = re.search(r"(?:iki|before|by|until)\s*(\d{1,2}(?:[:.]\d{2})?\s*(?:val\.?|h\b|am|pm)?)", low)
                if m:
                    put("cutoff_time", _clean(m.group(1)), sentence)
            carriers = [c for c in _CARRIERS if c in low and c != "post"]
            if carriers:
                put("carriers", carriers, sentence)
            m = re.search(r"(\d+(?:[.,]\d+)?\s*(?:€|eur))|(nemokam\w*|free)", low)
            if m:
                put("delivery_cost", _clean(m.group(0)), sentence)
        if topic in ("delivery", "exceptions") and re.search(r"vėluo|vėlav|vėlavim|delay|\blate\b|running late", low):
            put("delay_notice_channel", _channel_in(low) or "stated_without_channel", sentence)
            if re.search(r"nauj\w*\s+(?:pristatymo\s+)?(?:dat|termin|laik)|new\s+(?:delivery\s+)?(?:date|time|estimate)|revised|updated\s+(?:delivery|eta)", low):
                put("revised_estimate_promised", True, sentence)
        if topic == "exceptions" and re.search(r"skund|pretenz|complaint|problem|nesklandum", low):
            put("complaint_channel", _channel_in(low) or "stated_without_channel", sentence)
            lo, hi = _days_in(low)
            if lo is not None:
                put("complaint_response_days", hi, sentence)
        if topic == "returns" and re.search(r"gr[aą]žin|return|refund|keis|exchange", low):
            lo, hi = _days_in(low)
            if lo is not None:
                if re.search(r"pinig|refund|sum|lėš|money", low):
                    put("refund_days", hi, sentence)
                if quotes.get("refund_days") != sentence:
                    put("return_window_days", hi, sentence)
            if re.search(r"nemokam|free of charge|free return|mes apmok|shop pays|we pay|we cover", low):
                put("return_shipping_paid_by", "shop", sentence)
            elif re.search(r"pirkėj\w* (?:apmoka|padengia|sumoka)|klient\w* (?:apmoka|padengia|sumoka)|customer pays|at your (?:own )?(?:cost|expense)|savo lėšomis|išskaičiuo|deducted|buyer pays", low):
                put("return_shipping_paid_by", "customer", sentence)
        if topic == "returns" and re.search(r"nenaudot|unused|unworn|nedėvėt|originali\w* pakuot|original packaging|etiket|tags?\b|nepažeist|undamaged|kvit|receipt|sąskait", low):
            fields.setdefault("conditions", [])
            if len(fields["conditions"]) < 5 and sentence[:300] not in fields["conditions"]:
                fields["conditions"].append(sentence[:300])
                quotes.setdefault("conditions", sentence[:300])
        if topic == "returns" and re.match(r"^\s*\d+[.)]\s", sentence):
            fields.setdefault("return_steps", [])
            if len(fields["return_steps"]) < 10:
                fields["return_steps"].append(sentence[:200])
                quotes.setdefault("return_steps", sentence[:200])
    if "revised_estimate_promised" not in fields and "delay_notice_channel" in fields:
        fields["revised_estimate_promised"] = False
        quotes["revised_estimate_promised"] = quotes["delay_notice_channel"]
    return fields, quotes


_EXPECTED_FIELDS = {
    "delivery": ("delivery_days_min", "delivery_days_max", "cutoff_time", "carriers", "delivery_cost", "delay_notice_channel", "revised_estimate_promised"),
    "returns": ("return_window_days", "return_shipping_paid_by", "conditions", "return_steps", "refund_days"),
    "exceptions": ("delay_notice_channel", "revised_estimate_promised", "complaint_channel", "complaint_response_days"),
}


def _mode_policy_finder(tools, args: dict, fetch_html) -> dict[str, Any]:
    site_url = _clean(args.get("site_url") or args.get("url"))
    topic = _clean(args.get("topic")).lower() or "delivery"
    if topic not in _TOPIC_LINKS:
        raise ToolError(f"topic must be one of {', '.join(_TOPIC_LINKS)}")
    start_url = _clean(args.get("start_url")) or site_url
    if not start_url:
        raise ToolError("site_url (or start_url) is required")
    max_pages = _int_arg(args, "max_pages", DEFAULT_POLICY_PAGES)
    topic_re = _TOPIC_LINKS[topic]
    findings: list[dict] = []
    missing: list[str] = []
    warnings: list[str] = []
    fetched = 0
    seen: set[str] = {_normalise_url(start_url)}
    queue: deque[tuple[str, int, str, str | None]] = deque([(start_url, 0, "start", None)])
    visited: list[dict[str, Any]] = []
    policy: dict[str, Any] | None = None
    start_is_product = bool(site_url) and _normalise_url(site_url) != _normalise_url(start_url)
    while queue and fetched < max_pages:
        url, depth, via, link_text = queue.popleft()
        html = _fetch(fetch_html, url, missing)
        fetched += 1
        if html is None:
            if depth == 0:
                break
            continue
        page = extract_page(html)
        title = page.get("title") or ""
        h1 = " ".join(page.get("h1") or [])
        visited.append({"url": url, "clicks": depth, "via": via, "link_text": link_text, "title": title or None})
        looks_like_policy = depth > 0 and (topic_re.search(urlsplit(url).path) or topic_re.search(title) or topic_re.search(h1))
        if looks_like_policy:
            fields, quotes = _policy_fields(topic, page.get("body_text") or "")
            if fields or topic_re.search(title) or topic_re.search(h1):
                policy = {"url": url, "clicks": depth, "via": via, "link_text": link_text, "title": title or None, "fields": fields, "quotes": quotes}
                break
        is_hop_page = depth > 0 and bool(_HOP_LINKS.search(title) or _HOP_LINKS.search(urlsplit(url).path))
        topical, hops = [], []
        for link in _absolute_links(page, url):
            if _host(link["url"]) != _host(url):
                continue
            key = _normalise_url(link["url"])
            if key in seen:
                continue
            text = link.get("text") or ""
            haystack = f"{text} {urlsplit(link['url']).path} {link.get('hint') or ''}"
            if link.get("in_footer"):
                found_via = "footer"
            elif link.get("in_header") or link.get("in_nav"):
                found_via = "header"
            elif is_hop_page:
                found_via = "faq"
            elif depth == 0 and start_is_product:
                found_via = "product_page"
            else:
                found_via = "body"
            if topic_re.search(haystack):
                seen.add(key)
                topical.append((link["url"], depth + 1, found_via, text[:80] or None))
            elif _HOP_LINKS.search(haystack) and (depth == 0 or is_hop_page):
                seen.add(key)
                hops.append((link["url"], depth + 1, found_via, text[:80] or None))
        for item in topical + hops:
            queue.append(item)
    partial = policy is None and bool(queue) and fetched >= max_pages
    expected = _EXPECTED_FIELDS[topic]
    body: dict[str, Any] = {"site_url": site_url or None, "start_url": start_url, "topic": topic, "pages_fetched": fetched, "max_pages": max_pages,
                            "visited": visited, "policy_url": None, "clicks_from_start": None, "found_via": None, "fields": {}, "quotes": {},
                            "fields_missing": list(expected), "partial": partial}
    if policy is None:
        note = f"no {topic} page found within {fetched} page(s)" + (" (budget exhausted, more links unvisited)" if partial else " following footer, header, help and FAQ links")
        missing.append(f"policy: {note}")
        findings.append(_finding("policy_not_found", "block", start_url, fetched, note + "; a buyer following links from here does not reach it either"))
        summary = note
        return _finish("policy_finder", body, findings, missing, warnings, summary)
    body.update({"policy_url": policy["url"], "clicks_from_start": policy["clicks"], "found_via": policy["via"], "link_text": policy["link_text"],
                 "fields": policy["fields"], "quotes": policy["quotes"]})
    body["fields_missing"] = [f for f in expected if f not in policy["fields"]]
    for f in body["fields_missing"]:
        missing.append(f"field: {f} has no sentence on {policy['url']}")
    if policy["clicks"] >= 3:
        findings.append(_finding("policy_buried", "warn", policy["url"], policy["clicks"], f"{topic} page is {policy['clicks']} clicks from the start, via {policy['via']} only"))
    if policy["via"] == "footer":
        findings.append(_finding("policy_footer_only", "info", policy["url"], None, f"the {topic} page is reached through the footer link {policy['link_text']!r}"))
    if topic in ("delivery", "exceptions") and "delay_notice_channel" not in policy["fields"]:
        findings.append(_finding("no_delay_notice", "warn", policy["url"], None, "no sentence says how a customer hears about a late delivery"))
    if topic in ("delivery", "exceptions") and policy["fields"].get("revised_estimate_promised") is False:
        findings.append(_finding("no_revised_estimate", "warn", policy["url"], None, "delays are mentioned but no new delivery date is promised"))
    if topic == "returns" and "return_shipping_paid_by" not in policy["fields"]:
        findings.append(_finding("return_cost_unclear", "warn", policy["url"], None, "no sentence says who pays return shipping"))
    summary = f"{topic} page {policy['url']} is {policy['clicks']} click(s) from the start via {policy['via']}; {len(policy['fields'])} field(s) quoted, {len(body['fields_missing'])} without a sentence"
    return _finish("policy_finder", body, findings, missing, warnings, summary)


# --- listing_check -------------------------------------------------------------


def normalise_phone(raw: Any, default_country: str = "370") -> str | None:
    """Digits only: '8 37 123456' and '+370 37 123456' and '(37) 123 456' become one string."""
    digits = _DIGITS.sub("", str(raw or ""))
    if len(digits) < 7:
        return None
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("8") and len(digits) == 9:
        digits = default_country + digits[1:]
    if not digits.startswith(default_country) and len(digits) <= 8:
        digits = default_country + digits
    return digits


def _phones_in(text: str) -> list[str]:
    out = []
    for m in _PHONE.finditer(text or ""):
        if re.search(r"\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4}|\d{4}\.\d{2}\.\d{2}", m.group(1)):
            continue
        p = normalise_phone(m.group(1))
        if p and 9 <= len(p) <= 15 and p not in out:
            out.append(p)
    return out


def _address_tokens(address: str) -> list[str]:
    return [t for t in _TOKEN.findall(_strip_diacritics(address).casefold().replace(".", " ")) if len(t) >= 2 and t not in ("g", "pr", "al", "str", "lt", "lithuania", "lietuva")]


def _walk_jsonld(blocks: list[str]) -> dict[str, Any]:
    found: dict[str, Any] = {}

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for n in node:
                visit(n)
            return
        if not isinstance(node, dict):
            return
        ntype = str(node.get("@type") or "")
        if "name" in node and isinstance(node["name"], str) and "name" not in found and ntype not in ("Review", "Person", "ImageObject"):
            found["name"] = node["name"]
        if "telephone" in node and "phone" not in found:
            found["phone"] = str(node["telephone"])
        addr = node.get("address")
        if addr and "address" not in found:
            if isinstance(addr, dict):
                found["address"] = _clean(" ".join(str(addr.get(k) or "") for k in ("streetAddress", "postalCode", "addressLocality")))
            elif isinstance(addr, str):
                found["address"] = addr
        hours = node.get("openingHours") or node.get("openingHoursSpecification")
        if hours and "hours" not in found:
            if isinstance(hours, list) and hours and isinstance(hours[0], dict):
                found["hours"] = " ".join(f"{h.get('dayOfWeek', '')} {h.get('opens', '')}-{h.get('closes', '')}" for h in hours)
            else:
                found["hours"] = " ".join(hours) if isinstance(hours, list) else str(hours)
        if "url" in node and isinstance(node["url"], str) and "website" not in found and ntype not in ("Review", "ImageObject"):
            found["website"] = node["url"]
        rating = node.get("aggregateRating")
        if isinstance(rating, dict) and "rating" not in found:
            try:
                found["rating"] = float(str(rating.get("ratingValue")).replace(",", "."))
            except (TypeError, ValueError):
                pass
            try:
                found["review_count"] = int(float(str(rating.get("reviewCount") or rating.get("ratingCount"))))
            except (TypeError, ValueError):
                pass
        for v in node.values():
            if isinstance(v, (dict, list)):
                visit(v)

    for block in blocks or []:
        try:
            visit(json.loads(block))
        except (ValueError, TypeError):
            continue
    return found


def _compare_field(field: str, canonical: str, page: dict[str, Any], jsonld: dict[str, Any], text: str) -> dict[str, Any]:
    source = "json_ld" if field in jsonld else "text"
    if field == "phone":
        want = normalise_phone(canonical)
        found = []
        if jsonld.get("phone"):
            p = normalise_phone(jsonld["phone"])
            if p:
                found.append(p)
        found += [p for p in _phones_in(" ".join(page.get("tel_links") or []) + " " + text) if p not in found]
        if not found:
            return {"found": None, "status": "not_found", "source": source}
        if want and any(f == want or f[-8:] == want[-8:] for f in found):
            return {"found": "+" + want, "status": "match", "source": source}
        return {"found": "+" + found[0], "status": "mismatch", "source": source}
    if field == "address":
        want_tokens = _address_tokens(canonical)
        if not want_tokens:
            return {"found": None, "status": "not_compared", "source": source}
        hay = _strip_diacritics(f"{jsonld.get('address', '')} {text}").casefold()
        hay_tokens = set(_TOKEN.findall(hay.replace(".", " ")))
        present = [t for t in want_tokens if t in hay_tokens]
        digits_ok = all(t in hay_tokens for t in want_tokens if t.isdigit())
        if len(present) >= max(1, round(0.7 * len(want_tokens))) and digits_ok:
            return {"found": jsonld.get("address") or " ".join(present), "status": "match", "source": source}
        street = next((t for t in want_tokens if t.isalpha() and len(t) >= 4), None)
        if street and street in hay_tokens:
            snippet = jsonld.get("address")
            if not snippet:
                m = re.search(r"[^.]{0,40}" + re.escape(street) + r"[^.]{0,40}", _strip_diacritics(text), re.IGNORECASE)
                snippet = _clean(m.group(0)) if m else street
            return {"found": snippet, "status": "mismatch", "source": source}
        return {"found": None, "status": "not_found", "source": source}
    if field == "hours":
        want = {f"{int(a):02d}:{b}-{int(c):02d}:{d}" for a, b, c, d in _TIME_RANGE.findall(canonical)}
        got = {f"{int(a):02d}:{b}-{int(c):02d}:{d}" for a, b, c, d in _TIME_RANGE.findall(f"{jsonld.get('hours', '')} {text}")}
        if not got:
            return {"found": None, "status": "not_found", "source": source}
        if not want:
            return {"found": sorted(got), "status": "not_compared", "source": source}
        return {"found": sorted(got), "status": "match" if want <= got else "mismatch", "source": source}
    if field == "website":
        want_host = _host(canonical if "://" in canonical else "https://" + canonical)
        hosts = {_host(l.get("href") or "") for l in page.get("links") or [] if str(l.get("href") or "").startswith("http")}
        if jsonld.get("website"):
            hosts.add(_host(jsonld["website"]))
        text_hit = want_host and want_host in _strip_diacritics(text).casefold()
        if want_host and (want_host in hosts or text_hit):
            return {"found": want_host, "status": "match", "source": source}
        others = sorted(h for h in hosts if h and not any(s in h for s in ("facebook", "instagram", "google", "youtube", "linkedin")))
        if jsonld.get("website") and _host(jsonld["website"]) != want_host:
            return {"found": _host(jsonld["website"]), "status": "mismatch", "source": source}
        return {"found": others[:3] or None, "status": "not_found", "source": source}
    # name
    want_plain = _strip_diacritics(canonical).casefold()
    hay = _strip_diacritics(f"{jsonld.get('name', '')} {page.get('title') or ''} {' '.join(page.get('h1') or [])} {text}").casefold()
    if want_plain and want_plain in hay:
        return {"found": canonical, "status": "match", "source": source}
    core = [w for w in _WORD.findall(want_plain) if len(w) >= 4]
    if core and all(w in hay for w in core):
        return {"found": jsonld.get("name") or page.get("title"), "status": "match", "source": source}
    if jsonld.get("name"):
        return {"found": jsonld["name"], "status": "mismatch", "source": source}
    return {"found": None, "status": "not_found", "source": source}


def _mode_listing_check(tools, args: dict, fetch_html) -> dict[str, Any]:
    canonical = args.get("canonical") or {}
    if not isinstance(canonical, dict) or not any(_clean(canonical.get(k)) for k in ("name", "address", "phone", "hours", "website")):
        raise ToolError("canonical is required: {name, address, phone, hours?, website}")
    items = _list_arg(args.get("listings") or args.get("urls") or args.get("pages"), "listings")
    if args.get("html"):
        items.append({"html": args["html"]})
    if not items:
        raise ToolError("listings is required: urls, workspace paths or html of the directory pages")
    findings: list[dict] = []
    missing: list[str] = []
    warnings: list[str] = []
    fields_wanted = [k for k in ("name", "address", "phone", "hours", "website") if _clean(canonical.get(k))]
    listings = []
    consistent = mismatched = unreachable = 0
    for item in items:
        kind, value = _source_of(item)
        label = value if kind != "html" else "html"
        html = _read_source(tools, kind, value, fetch_html, missing)
        if html is None:
            unreachable += 1
            listings.append({"url": label, "source_name": _host(value) if kind == "url" else None, "status": "unreachable", "fields": {}, "rating": None, "review_count": None})
            continue
        page = extract_page(html)
        text = page.get("body_text") or ""
        if len(_TOKEN.findall(text)) < 20 and not page.get("jsonld_raw"):
            unreachable += 1
            missing.append(f"listing: {label} has almost no text (client-side profile?); paste the profile text or use browser_snapshot")
            listings.append({"url": label, "source_name": _host(value) if kind == "url" else None, "status": "unreachable", "fields": {}, "rating": None, "review_count": None})
            continue
        jsonld = _walk_jsonld(page.get("jsonld_raw") or [])
        fields = {f: _compare_field(f, _clean(canonical[f]), page, jsonld, text) for f in fields_wanted}
        rating = jsonld.get("rating")
        review_count = jsonld.get("review_count")
        if rating is None:
            m = re.search(r"(\d[.,]\d)\s*(?:/\s*5|★|žvaigžd|stars?\b|iš 5|out of 5)", text)
            if m:
                rating = float(m.group(1).replace(",", "."))
        if review_count is None:
            m = re.search(r"(\d+)\s*(?:atsiliepim|reviews?|įvertinim|ratings?)", text, re.IGNORECASE)
            if m:
                review_count = int(m.group(1))
        statuses = [f["status"] for f in fields.values()]
        if "mismatch" in statuses:
            status = "mismatch"
            mismatched += 1
        elif all(s in ("match", "not_compared") for s in statuses):
            status = "consistent"
            consistent += 1
        else:
            status = "incomplete"
        for f, res in fields.items():
            if res["status"] == "mismatch":
                findings.append(_finding("listing_mismatch", "block", label, {"field": f, "found": res["found"], "canonical": _clean(canonical[f])}, f"{label}: {f} reads {res['found']!r}, canonical is {_clean(canonical[f])!r}"))
            elif res["status"] == "not_found" and f in ("phone", "address"):
                findings.append(_finding("listing_field_missing", "warn", label, f, f"{label}: no {f} on the listing"))
        listings.append({"url": label, "source_name": _host(value) if kind == "url" else None, "status": status, "fields": fields, "rating": rating,
                         "review_count": review_count, "extracted_from": "json_ld" if jsonld else "text"})
    summary_counts = {"consistent": consistent, "mismatched": mismatched, "unreachable": unreachable, "incomplete": len(listings) - consistent - mismatched - unreachable}
    summary = f"{len(listings)} listing(s): {consistent} consistent, {mismatched} with a mismatch, {unreachable} unreachable"
    return _finish("listing_check", {"canonical": {k: _clean(canonical.get(k)) or None for k in ("name", "address", "phone", "hours", "website")},
                                     "listings": listings, "summary_counts": summary_counts}, findings, missing, warnings, summary)


# --- search_intent -------------------------------------------------------------


def _intent_tokens(query: str, langs: list[str]) -> dict[str, list[str]]:
    low = f" {_strip_diacritics(query).casefold()} "
    evidence: dict[str, list[str]] = {}
    for intent, by_lang in _INTENT_TOKENS.items():
        hits = []
        for lang in langs:
            for tok in by_lang.get(lang, ()):
                plain = _strip_diacritics(tok).casefold()
                if " " in plain:
                    if f" {plain} " in low or low.strip().startswith(plain + " ") or low.strip() == plain:
                        hits.append(tok)
                elif any(same_stem(plain, w) and (len(w) <= len(plain) + 3) for w in low.split()):
                    hits.append(tok)
        if hits:
            evidence[intent] = sorted(set(hits))
    return evidence


def _best_context(query: str) -> dict[str, Any] | None:
    words = query.split()
    for i, w in enumerate(words):
        plain = _strip_diacritics(w).casefold()
        if plain.startswith("geriaus") or plain in ("best", "top"):
            after = words[i + 1:i + 3]
            rest = " ".join(words[i + 1:])
            kind = "place_or_experience" if _PLACE_CONTEXT.search(rest) else "service" if _SERVICE_CONTEXT.search(rest) else "product_class"
            return {"modifier": w, "nouns": after, "kind": kind}
    return None


def _shape_of(result: dict[str, Any], site: str | None) -> str:
    text = f"{result['title']} {result['url']}"
    if site and (result["domain"] == site or result["domain"].endswith("." + site)):
        return "brand"
    for shape, pattern in _SHAPE_RULES:
        if pattern.search(text):
            return shape
    return "other"


def _mode_search_intent(args: dict, search) -> dict[str, Any]:
    queries = [_clean(q) for q in _list_arg(args.get("queries") or args.get("query"), "queries") if _clean(q)]
    if not queries:
        raise ToolError("queries is required")
    language = _clean(args.get("language")).lower()[:2]
    site = _host(args.get("site") or "") or _clean(args.get("site")).lower() or None
    fetch_serp = args.get("fetch_serp", True) not in (False, "false", "no", 0)
    findings: list[dict] = []
    missing: list[str] = []
    warnings: list[str] = []
    langs = [language] if language in ("lt", "en") else ["lt", "en"]
    if language and language not in ("lt", "en"):
        warnings.append(f"no modifier list for {language!r}; classified with both lt and en lists")
    brand_tokens = [t for t in re.split(r"[.\-]", site or "") if len(t) >= 3 and t not in ("www", "com", "net", "org")]
    rows = []
    for query in queries:
        evidence = _intent_tokens(query, langs)
        nav_hits = [t for t in brand_tokens if t in _strip_diacritics(query).casefold().replace(" ", "")]
        if nav_hits:
            evidence["navigational"] = nav_hits
            intent = "navigational"
        elif not evidence:
            intent = "informational"
            evidence["informational"] = ["no modifier: bare topic"]
        else:
            ranked = sorted(evidence.items(), key=lambda kv: -len(kv[1]))
            intent = ranked[0][0] if len(ranked) == 1 or len(ranked[0][1]) > len(ranked[1][1]) else "mixed"
        context = _best_context(query)
        if context and context["kind"] == "place_or_experience" and intent in ("commercial", "mixed"):
            intent = "informational"
            evidence.setdefault("informational", []).append(f"best + {context['kind']}")
        shapes = []
        dominant = None
        serp_evidence = "unavailable"
        results = _run_search(search, query, missing) if fetch_serp else None
        if not fetch_serp:
            missing.append(f"search: {query!r} not searched (fetch_serp off)")
        if results:
            for r in results[:8]:
                shapes.append({"rank": r["rank"], "url": r["url"], "title": r["title"], "shape": _shape_of(r, site)})
            counter = Counter(s["shape"] for s in shapes)
            top_shape, top_n = counter.most_common(1)[0]
            dominant = {"shape": top_shape, "count": top_n, "of": len(shapes)}
            serp_evidence = "available" if len(shapes) >= 3 else "thin"
        recommended = {
            "informational": "guide article (list or how-to), linking to the category",
            "commercial": "comparison article with a criteria table",
            "transactional": "category or product page",
            "navigational": "brand home or the named page",
            "mixed": "hub page that routes to a guide and to the category",
        }[intent]
        agree = dominant is not None and {
            "informational": dominant["shape"] in ("list_guide", "forum", "news"),
            "commercial": dominant["shape"] in ("comparison", "list_guide"),
            "transactional": dominant["shape"] in ("product_category", "brand"),
            "navigational": dominant["shape"] == "brand",
            "mixed": True,
        }[intent]
        if serp_evidence == "available":
            confidence = "high" if agree else "medium"
            if not agree:
                findings.append(_finding("serp_disagrees", "warn", query, dominant, f"{query!r}: modifiers say {intent} but {dominant['count']} of {dominant['of']} results are {dominant['shape']} pages"))
        elif serp_evidence == "thin":
            confidence = "medium" if agree else "low"
        else:
            confidence = "low"
        if intent == "mixed":
            findings.append(_finding("mixed_intent", "info", query, evidence, f"{query!r} triggers more than one intent equally; decide the page from the SERP shape"))
        rows.append({"query": query, "intent": intent, "modifier_evidence": evidence, "best_context": context, "serp_shapes": shapes,
                     "dominant_shape": dominant, "serp_evidence": serp_evidence, "recommended_page_type": recommended, "confidence": confidence})
    by_intent = Counter(r["intent"] for r in rows)
    summary = f"{len(rows)} query(ies): " + ", ".join(f"{n} {k}" for k, n in by_intent.most_common())
    if all(r["serp_evidence"] == "unavailable" for r in rows):
        summary += "; rule-only, no SERP evidence"
    return _finish("search_intent", {"language": language or None, "site": site, "queries": rows}, findings, missing, warnings, summary)


# --- site_crawl ----------------------------------------------------------------


def _mode_site_crawl(tools, args: dict, fetch_html, fetch_meta) -> dict[str, Any]:
    start_url = _clean(args.get("start_url") or args.get("url"))
    if not start_url.startswith(("http://", "https://")):
        raise ToolError("start_url must be an absolute http(s) address")
    max_pages = _int_arg(args, "max_pages", DEFAULT_CRAWL_PAGES)
    max_depth = _int_arg(args, "max_depth", DEFAULT_CRAWL_DEPTH, minimum=0)
    same_host_only = args.get("same_host_only", True) not in (False, "false", "no", 0)
    important_raw = [_clean(u) for u in _list_arg(args.get("important_urls"), "important_urls")]
    important_path = _clean(args.get("important_path"))
    findings: list[dict] = []
    missing: list[str] = []
    warnings: list[str] = []
    if important_path:
        if tools is None:
            raise ToolError("important_path needs a workspace")
        target = tools.resolve(important_path)
        if not target.is_file():
            raise ToolError(f"path missing: {important_path}")
        for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip().split(",")[0].strip()
            if line and not line.startswith("#"):
                important_raw.append(line)
    important = []
    for u in important_raw:
        absolute = urljoin(start_url, u)
        if absolute not in important:
            important.append(absolute)
    start_host = _host(start_url)
    inbound: Counter[str] = Counter()
    reached: dict[str, dict[str, Any]] = {}
    words_by_url: dict[str, list[str]] = {}
    queue: deque[tuple[str, int, list[str]]] = deque([(start_url, 0, [start_url])])
    seen: set[str] = {_normalise_url(start_url)}
    fetched = 0
    partial = False
    start_anchor_count: int | None = None
    while queue:
        if fetched >= max_pages:
            partial = True
            break
        url, depth, path = queue.popleft()
        html = _fetch(fetch_html, url, missing)
        fetched += 1
        if html is None:
            if depth == 0:
                break
            reached[_normalise_url(url)] = {"url": url, "depth": depth, "inbound": inbound[_normalise_url(url)], "path": path, "title": None, "fetched": False}
            continue
        page = extract_page(html)
        key = _normalise_url(url)
        reached[key] = {"url": url, "depth": depth, "inbound": inbound[key], "path": path, "title": page.get("title"), "fetched": True}
        words_by_url[key] = content_words(" ".join([page.get("title") or ""] + (page.get("h1") or []) + (page.get("h2") or [])))
        links = _absolute_links(page, url)
        if depth == 0:
            start_anchor_count = len(links)
        for link in links:
            if same_host_only and _host(link["url"]) != start_host:
                continue
            lkey = _normalise_url(link["url"])
            if lkey == key:
                continue
            inbound[lkey] += 1
            if lkey in reached:
                reached[lkey]["inbound"] = inbound[lkey]
            if lkey in seen or depth + 1 > max_depth:
                continue
            seen.add(lkey)
            queue.append((link["url"], depth + 1, path + [link["url"]]))
    for key, rec in reached.items():
        rec["inbound"] = inbound[key]
    if not reached:
        missing.append(f"crawl: start page {start_url} could not be fetched; nothing crawled")
        return _finish("site_crawl", {"start_url": start_url, "pages_crawled": 0, "partial": False, "reached": [], "important_total": len(important) or None,
                                      "orphans": None, "unverified": important, "weak": []}, findings, missing, warnings, "start page not fetched; nothing crawled")
    reached_list = sorted(reached.values(), key=lambda r: (r["depth"], -r["inbound"], r["url"]))
    orphans: list[dict[str, Any]] | None = None
    unverified: list[str] = []
    if important:
        orphans = []
        for u in important:
            k = _normalise_url(u)
            if k in reached or inbound[k] > 0:
                continue
            if partial:
                unverified.append(u)
                continue
            title = None
            exists: bool | None = None
            if fetch_meta is not None:
                try:
                    meta = fetch_meta(u) or {}
                    status = meta.get("status")
                    exists = (int(status) < 400) if status is not None else None
                except Exception as exc:
                    missing.append(f"meta: {u} ({exc})")
            if len(orphans) < MAX_ORPHAN_FETCH:
                html = _fetch(fetch_html, u, missing)
                if html is not None:
                    page = extract_page(html)
                    title = page.get("title")
                    exists = True if exists is None else exists
                    orphan_words = content_words(" ".join([title or ""] + (page.get("h1") or [])))
                else:
                    orphan_words = content_words(urlsplit(u).path.replace("-", " ").replace("/", " ").replace(".html", ""))
            else:
                orphan_words = content_words(urlsplit(u).path.replace("-", " ").replace("/", " "))
            suggestions = []
            for rk, words in words_by_url.items():
                shared = [w for w in orphan_words if any(same_stem(w, pw) for pw in words)]
                if shared:
                    suggestions.append({"url": reached[rk]["url"], "shared_words": shared})
            suggestions.sort(key=lambda s: -len(s["shared_words"]))
            orphans.append({"url": u, "title": title, "exists": exists, "in_important_list": True, "suggested_sources": suggestions[:3]})
        if orphans:
            findings.append(_finding("orphan_pages", "block", start_url, len(orphans), f"{len(orphans)} of {len(important)} important pages have no inbound link from the crawled pages"))
    else:
        findings.append(_finding("no_important_list", "info", start_url, None, "no important-page list: orphans cannot be determined, reachability only"))
    weak = [{"url": r["url"], "inbound": r["inbound"], "depth": r["depth"]} for r in reached_list if r["inbound"] <= 1 and r["depth"] >= 4]
    if partial:
        findings.append(_finding("partial_crawl", "warn", start_url, fetched, f"crawl stopped at {max_pages} pages with {len(queue)} link(s) unvisited; {len(unverified)} important page(s) unverified, not orphan"))
    if start_anchor_count is not None and start_anchor_count < 5:
        findings.append(_finding("nav_not_in_html", "warn", start_url, start_anchor_count, "the start page has fewer than 5 anchors in HTML; the menu is probably rendered by JavaScript"))
    if weak:
        findings.append(_finding("weak_pages", "info", start_url, len(weak), f"{len(weak)} page(s) are 4+ clicks deep with a single inbound link"))
    summary = f"{len(reached)} page(s) reached from {start_url}" + (", partial" if partial else "")
    if orphans is not None:
        summary += f"; {len(orphans)} orphan(s) of {len(important)} important"
    return _finish("site_crawl", {"start_url": start_url, "pages_crawled": fetched, "partial": partial, "max_pages": max_pages, "max_depth": max_depth,
                                  "reached": reached_list, "important_total": len(important) or None, "orphans": orphans, "unverified": unverified, "weak": weak},
                   findings, missing, warnings, summary)


# --- keyword_inspect ----------------------------------------------------------


def _host_kind(host: str, own_host: str | None) -> str:
    if own_host and (host == own_host or host.endswith("." + own_host)):
        return "own"
    if _AGGREGATOR_HOSTS.search(host):
        return "aggregator"
    if _PORTAL_HOSTS.search(host):
        return "portal"
    if _CLINIC_HOSTS.search(host):
        return "clinic"
    if _SHOP_HOSTS.search(host):
        return "shop"
    return "unknown"


def _result_record(label: str, html: str, host: str, own_host: str | None, keyword: str) -> dict[str, Any]:
    page = extract_page(html)
    text = page.get("body_text") or ""
    prices = page.get("prices") or []
    has_table = "<table" in html.lower()
    has_price_table = (has_table and len(prices) >= 2) or len(prices) >= 4
    has_faq = any(_FAQ_HEADING.search(h["text"]) for h in page.get("headings") or []) or any("FAQPage" in b for b in page.get("jsonld_raw") or [])
    has_schema = bool(page.get("jsonld_raw"))
    kw_words = content_words(keyword)
    h1 = " ".join(page.get("h1") or [])
    return {
        "url": label, "host": host, "host_kind": _host_kind(host, own_host), "title": page.get("title"), "h1": h1 or None,
        "words": page.get("word_count") or 0, "has_price_table": has_price_table, "n_prices": len(prices), "has_faq": has_faq,
        "has_schema": has_schema, "has_faq_or_schema": has_faq or has_schema, "keyword_in_title": all(_word_in(w, page.get("title") or "") for w in kw_words) if kw_words else None,
        "keyword_in_h1": all(_word_in(w, h1) for w in kw_words) if kw_words else None, "headings": len(page.get("headings") or []),
        "images": page.get("images_total") or 0, "fetched": True,
    }


def _mode_keyword_inspect(tools, args: dict, fetch_html, search) -> dict[str, Any]:
    keyword = _clean(args.get("keyword"))
    if not keyword:
        raise ToolError("keyword is required")
    findings: list[dict] = []
    missing: list[str] = []
    warnings: list[str] = []
    urls = [_clean(u) for u in _list_arg(args.get("result_urls") or args.get("results"), "result_urls")]
    if not urls and args.get("search", True) not in (False, "false", "no", 0):
        results = _run_search(search, keyword, missing)
        urls = [r["url"] for r in results] if results else []
    urls = urls[:MAX_RESULT_URLS]
    own_url = _clean(args.get("own_url"))
    own_host = _host(own_url) if own_url else None
    own_html = args.get("own_html")
    results_out = []
    for rank, u in enumerate(urls, 1):
        html = _fetch(fetch_html, u, missing) if u.startswith("http") else _read_source(tools, "path", u, fetch_html, missing)
        host = _host(u) if u.startswith("http") else u
        if html is None:
            results_out.append({"rank": rank, "url": u, "host": host, "host_kind": _host_kind(host, own_host), "fetched": False})
            continue
        rec = _result_record(u, html, host, own_host, keyword)
        rec["rank"] = rank
        results_out.append(rec)
    own = None
    if own_html or own_url:
        html = str(own_html) if own_html else _fetch(fetch_html, own_url, missing)
        if html is not None:
            own = _result_record(own_url or "own_html", html, own_host or "own", own_host, keyword)
            own["host_kind"] = "own"
    fetched = [r for r in results_out if r.get("fetched")]
    strong = [r for r in fetched if r["words"] >= STRONG_WORDS and (r["has_price_table"] or r["has_faq_or_schema"])]
    median_words = int(median(r["words"] for r in fetched)) if fetched else None
    with_prices = sum(1 for r in fetched if r["has_price_table"])
    with_faq = sum(1 for r in fetched if r["has_faq_or_schema"])
    kinds = Counter(r["host_kind"] for r in results_out)
    gaps: list[str] = []
    if own and fetched:
        if median_words and own["words"] < 0.6 * median_words:
            gaps.append(f"words: own {own['words']} vs median {median_words}")
        if with_prices >= len(fetched) / 2 and not own["has_price_table"]:
            gaps.append(f"price table: {with_prices}/{len(fetched)} results have one, own page has none")
        if with_faq >= len(fetched) / 2 and not own["has_faq_or_schema"]:
            gaps.append(f"FAQ or schema: {with_faq}/{len(fetched)} results have it, own page has none")
        if own.get("keyword_in_title") is False:
            gaps.append("keyword missing from own title")
        if own.get("keyword_in_h1") is False:
            gaps.append("keyword missing from own H1")
        for g in gaps:
            findings.append(_finding("content_gap", "warn", own["url"], None, g))
    elif not own:
        missing.append("own page: give own_url or own_html to list the gaps")
    if len(fetched) < 5:
        warnings.append(f"only {len(fetched)} result page(s) fetched; difficulty picture is low-confidence")
    kd = args.get("kd")
    volume = args.get("volume")
    difficulty = {"strong_results": len(strong), "fetched": len(fetched), "median_words": median_words, "results_with_price_table": with_prices,
                  "results_with_faq_or_schema": with_faq, "host_kinds": dict(kinds), "label": "content gap only; authority not measured"}
    not_measured = ["authority", "backlinks"]
    confidence = "medium" if len(fetched) >= 5 else "low"
    summary = f"{len(fetched)} of {len(urls)} result pages fetched for {keyword!r}: {len(strong)} strong (≥{STRONG_WORDS} words with prices or FAQ/schema), median {median_words} words, {with_prices} with a price table"
    if own:
        summary += f"; own page {own['words']} words, {len(gaps)} gap(s)"
    summary += "; authority and backlinks not measured"
    return _finish("keyword_inspect", {"keyword": keyword, "results": results_out, "own": own, "gap": {"median_words": median_words, "own_words": own["words"] if own else None,
                                        "results_with_prices": with_prices, "own_has_price": own["has_price_table"] if own else None, "element_gaps": gaps},
                                        "difficulty": difficulty, "kd": kd, "volume": volume, "not_measured": not_measured, "confidence": confidence},
                   findings, missing, warnings, summary)


# --- link_prospects -----------------------------------------------------------


def _target_match(href: str, targets: list[tuple[str, str]]) -> str | None:
    host = _host(href)
    path = urlsplit(href).path.rstrip("/")
    for t_host, t_path in targets:
        if host == t_host or host.endswith("." + t_host):
            exact = not t_path or path.startswith(t_path)
            return t_host + (t_path or "") + ("" if exact else " (homepage/other path)")
    return None


def _context_for(anchor: str, page: dict[str, Any]) -> str | None:
    anchor = _clean(anchor)
    pools = list(page.get("paragraphs") or []) + [page.get("body_text") or ""]
    if anchor:
        for para in page.get("paragraphs") or []:
            if anchor.casefold() in para.casefold():
                for s in _sentences(para):
                    if anchor.casefold() in s.casefold():
                        return s[:CONTEXT_PAD * 2]
                return para[:CONTEXT_PAD * 2]
        body = page.get("body_text") or ""
        idx = body.casefold().find(anchor.casefold())
        if idx >= 0:
            return _clean(body[max(0, idx - CONTEXT_PAD):idx + len(anchor) + CONTEXT_PAD])
    return None


def _relevance(terms: list[str], page: dict[str, Any]) -> tuple[float, dict[str, int]]:
    if not terms:
        return 0.0, {}
    title = f"{page.get('title') or ''} {' '.join(page.get('h1') or [])}"
    headings = " ".join(h["text"] for h in page.get("headings") or [])
    body = page.get("body_text") or ""
    weights: dict[str, int] = {}
    for term in terms:
        if _word_in(term, title):
            weights[term] = 3
        elif _word_in(term, headings):
            weights[term] = 2
        elif _word_in(term, body):
            weights[term] = 1
        else:
            weights[term] = 0
    return round(sum(weights.values()) / (3 * len(terms)), 2), weights


def _mode_link_prospects(tools, args: dict, fetch_html, fetch_meta) -> dict[str, Any]:
    targets_raw = [_clean(t) for t in _list_arg(args.get("target_urls") or args.get("target_url"), "target_urls") if _clean(t)]
    if not targets_raw:
        raise ToolError("target_urls is required")
    targets = [(_host(t if "://" in t else "https://" + t), urlsplit(t if "://" in t else "https://" + t).path.rstrip("/")) for t in targets_raw]
    topic_terms = [_clean(t) for t in _list_arg(args.get("topic_terms"), "topic_terms") if _clean(t)]
    raw_candidates = _list_arg(args.get("candidates"), "candidates")
    max_fetch = _int_arg(args, "max_fetch", DEFAULT_MAX_FETCH)
    findings: list[dict] = []
    missing: list[str] = []
    warnings: list[str] = []
    candidates = []
    for c in raw_candidates:
        if isinstance(c, str):
            candidates.append({"url": c.strip(), "reach": None})
        elif isinstance(c, dict) and c.get("url"):
            reach = c.get("reach", c.get("dr", c.get("traffic")))
            try:
                reach = float(str(reach).replace(",", ".")) if reach not in (None, "") else None
            except ValueError:
                reach = None
            candidates.append({"url": str(c["url"]).strip(), "reach": reach})
    if not candidates:
        return _finish("link_prospects", {"target_urls": targets_raw, "topic_terms": topic_terms, "candidates": [], "ranked": [], "no_link": [], "unverified": [],
                                          "reason": "no candidates given; supply a backlink export or search results, nothing is invented",
                                          "summary_counts": {"fetched": 0, "linking": 0, "failed": 0}}, findings, missing, warnings, "no candidates given; nothing ranked")
    if not topic_terms:
        warnings.append("no topic_terms: relevance is 0 for every candidate, ranking falls back to reach")
    if all(c["reach"] is None for c in candidates):
        missing.append("reach: no DR or traffic column given; ranking by relevance only")
    if len(candidates) > max_fetch:
        warnings.append(f"{len(candidates)} candidates, only the first {max_fetch} fetched")
    ranked, no_link, unverified = [], [], []
    fetched = 0
    for cand in candidates[:max_fetch]:
        url = cand["url"]
        html = _fetch(fetch_html, url, missing)
        if html is None:
            unverified.append({**cand, "status": "fetch_failed"})
            continue
        fetched += 1
        page = extract_page(html)
        links = _absolute_links(page, url)
        hit = None
        for link in links:
            matched = _target_match(link["url"], targets)
            if matched:
                hit = (link, matched)
                if not (link.get("in_footer") or link.get("in_nav") or link.get("in_header")):
                    break
        relevance, weights = _relevance(topic_terms, page)
        row = {**cand, "page_title": page.get("title"), "headings": [h["text"] for h in (page.get("headings") or [])][:12], "relevance": relevance, "term_weights": weights}
        if hit is None:
            row["status"] = "no_link"
            no_link.append(row)
            continue
        link, matched = hit
        boilerplate = bool(link.get("in_footer") or link.get("in_nav") or link.get("in_header"))
        anchor = link.get("text") or ""
        row.update({"status": "ok", "links_to_target": True, "target_matched": matched, "anchor_text": anchor or None, "link_href": link["url"],
                    "boilerplate": boilerplate, "cited_context": None if boilerplate else _context_for(anchor, page), "rel": link.get("rel") or ""})
        if boilerplate:
            row["cited_context_note"] = "nav/footer link: no citing sentence"
        ranked.append(row)
    ranked.sort(key=lambda r: (-r["relevance"], -(r["reach"] or 0)))
    for r in ranked:
        if r["relevance"] <= 0.25 and (r["reach"] or 0) >= 50:
            findings.append(_finding("high_reach_low_relevance", "info", r["url"], r["relevance"], f"{r['url']}: reach {r['reach']} but relevance {r['relevance']}; readers of this page do not care about the topic"))
    if unverified:
        findings.append(_finding("unverified_candidates", "warn", None, len(unverified), f"{len(unverified)} candidate(s) could not be fetched; list them as unverified, never as prospects"))
    if fetched and not ranked:
        findings.append(_finding("no_linking_pages", "warn", None, 0, "none of the fetched candidates links to a target; ask for a backlink export"))
    counts = {"fetched": fetched, "linking": len(ranked), "no_link": len(no_link), "failed": len(unverified)}
    summary = f"{fetched} candidate(s) fetched, {len(ranked)} link to a target, {len(no_link)} do not, {len(unverified)} unverified"
    if ranked:
        summary += f"; top prospect {ranked[0]['url']} (relevance {ranked[0]['relevance']})"
    return _finish("link_prospects", {"target_urls": targets_raw, "topic_terms": topic_terms, "ranked": ranked, "no_link": no_link, "unverified": unverified,
                                      "summary_counts": counts}, findings, missing, warnings, summary)


# --- locale_check -------------------------------------------------------------


def _locale_profile(expected: str) -> tuple[str, str, dict[str, Any] | None]:
    raw = expected.replace("_", "-").strip().lower()
    lang = raw.split("-")[0]
    profile = _LOCALES.get(raw) or _LOCALES.get(lang)
    return raw, lang, profile


def _mode_locale_check(tools, args: dict, fetch_html) -> dict[str, Any]:
    expected = _clean(args.get("expected_locale") or args.get("locale"))
    if not expected:
        raise ToolError("expected_locale is required, e.g. lv-LV")
    findings: list[dict] = []
    missing: list[str] = []
    warnings: list[str] = []
    label, html = _single_page(tools, args, fetch_html, missing)
    text_arg = args.get("text")
    if html is None and not text_arg:
        if label is None:
            raise ToolError("give the page as html, path or url, or the copy as text")
        return _finish("locale_check", {"page": label, "expected_locale": expected, "mismatches": []}, findings, missing, warnings, "page not fetched; nothing checked")
    page = extract_page(html) if html else None
    text = (page.get("body_text") if page else "") or ""
    if text_arg:
        text = f"{text} {text_arg}".strip()
    locale_key, lang, profile = _locale_profile(expected)
    if profile is None:
        warnings.append(f"no format profile for {expected}; only the lang attribute and hreflang set were checked")
    mismatches: list[dict[str, Any]] = []
    lang_attr = (page.get("lang") if page else None) or None
    lang_attr_norm = (lang_attr or "").replace("_", "-").lower()
    if page and not lang_attr:
        mismatches.append({"check": "lang_attr", "found": None, "expected": lang, "note": "html has no lang attribute"})
    elif lang_attr and lang_attr_norm.split("-")[0] != lang:
        mismatches.append({"check": "lang_attr", "found": lang_attr, "expected": lang, "note": "html lang attribute names another language"})
    detected = detect_language(text)
    if detected and detected != lang and lang in ("lt", "en"):
        mismatches.append({"check": "text_language", "found": detected, "expected": lang, "note": "the body text reads as another language"})
    elif detected and lang not in ("lt", "en") and detected == "lt":
        mismatches.append({"check": "text_language", "found": "lt", "expected": lang, "note": "the body text still reads as Lithuanian"})
    hreflangs = [h.strip().lower() for h in _HREFLANG.findall(html or "")]
    hreflang_hrefs = {}
    for tag in re.findall(r"<link\b[^>]*\bhreflang\s*=\s*[\"'][^\"']+[\"'][^>]*>", html or "", re.IGNORECASE):
        code = _HREFLANG.search(tag)
        href = _HREF_IN_TAG.search(tag)
        if code and href:
            hreflang_hrefs[code.group(1).strip().lower()] = href.group(1)
    if hreflangs:
        if locale_key not in hreflangs and lang not in hreflangs:
            mismatches.append({"check": "hreflang", "found": hreflangs, "expected": locale_key, "note": "hreflang set does not include the expected locale"})
        if "x-default" not in hreflangs:
            findings.append(_finding("no_x_default", "info", label, hreflangs, "hreflang set has no x-default"))
    elif html:
        findings.append(_finding("no_hreflang", "info", label, None, "no hreflang links on the page"))
    currencies = sorted({c.lower() for c in _CURRENCY_TOKENS.findall(text)})
    if profile:
        wrong = [c for c in currencies if c not in [x.lower() for x in profile["currency"]]]
        if wrong:
            mismatches.append({"check": "currency", "found": wrong, "expected": list(profile["currency"]), "note": "currency symbols that do not belong to the locale"})
    date_hits = {shape: [m.group(0) for m in pattern.finditer(text)] for shape, pattern in _DATE_SHAPES.items()}
    date_shapes_found = [s for s, hits in date_hits.items() if hits]
    if profile:
        wrong_dates = [s for s in date_shapes_found if s not in profile["dates"]]
        # dd.mm.yyyy and mm/dd/yyyy overlap with each other only in ambiguous cases; report shapes not in the profile
        if wrong_dates:
            examples = [date_hits[s][0] for s in wrong_dates]
            mismatches.append({"check": "date_format", "found": wrong_dates, "expected": list(profile["dates"]), "note": f"date shapes not used in the locale (e.g. {', '.join(examples)})"})
    numbers_en = _NUMBER_EN.findall(text)
    numbers_eu = _NUMBER_EU.findall(text)
    if profile:
        if profile["decimal"] == "," and numbers_en:
            mismatches.append({"check": "number_format", "found": numbers_en[:3], "expected": "1 200,50", "note": "numbers with a dot decimal and comma thousands"})
        if profile["decimal"] == "." and numbers_eu:
            mismatches.append({"check": "number_format", "found": numbers_eu[:3], "expected": "1,200.50", "note": "numbers with a comma decimal"})
    phone_codes = sorted({m.group(1) for m in _INTL_PHONE.finditer(text)})
    if profile and profile.get("phone") and phone_codes:
        foreign = [c for c in phone_codes if c != profile["phone"]]
        if foreign:
            mismatches.append({"check": "phone_format", "found": [f"+{c}" for c in foreign], "expected": f"+{profile['phone']}", "note": "phone numbers with another country code"})
    for m in mismatches:
        level = "block" if m["check"] in ("lang_attr", "text_language", "currency") else "warn"
        findings.append(_finding(f"locale_{m['check']}", level, label, m["found"], m["note"]))
    summary = f"{len(mismatches)} mismatch(es) against {expected}" + (f" on {label}" if label else "")
    if not mismatches:
        summary = f"no locale mismatches against {expected}" + (f" on {label}" if label else "")
    return _finish("locale_check", {"page": label, "expected_locale": expected, "profile": profile["name"] if profile else None, "lang_attr": lang_attr,
                                    "text_language": detected, "hreflang": hreflangs, "hreflang_hrefs": hreflang_hrefs, "currencies": currencies,
                                    "date_shapes": date_shapes_found, "phone_codes": [f"+{c}" for c in phone_codes], "mismatches": mismatches},
                   findings, missing, warnings, summary)


# --- kit_check ----------------------------------------------------------------


def _mode_kit_check(tools, args: dict, fetch_html) -> dict[str, Any]:
    findings: list[dict] = []
    missing: list[str] = []
    warnings: list[str] = []
    label, html = _single_page(tools, args, fetch_html, missing)
    files = [str(f.get("path") if isinstance(f, dict) else f).strip() for f in _list_arg(args.get("files"), "files")]
    file_meta = {str(f.get("path")).strip(): f for f in _list_arg(args.get("files"), "files") if isinstance(f, dict) and f.get("path")}
    boilerplate_arg = _clean(args.get("boilerplate"))
    contact_arg = _clean(args.get("contact"))
    required = [str(r).strip().lower() for r in _list_arg(args.get("required"), "required")] or ["logo_vector", "logo_raster", "boilerplate", "contact", "images_sized", "images_alt"]
    if html is None and not files and not boilerplate_arg:
        if label is None:
            raise ToolError("give the kit page (html, path or url) or a files list")
        return _finish("kit_check", {"page": label, "items": {}, "gaps": required}, findings, missing, warnings, "kit page not fetched; nothing checked")
    page = extract_page(html) if html else None
    names: list[str] = list(files)
    images: list[dict[str, Any]] = []
    if page:
        for link in page.get("links") or []:
            names.append(str(link.get("href") or ""))
        for img in page.get("images") or []:
            names.append(str(img.get("src") or ""))
            images.append(img)
    logo_vector = sorted({n for n in names if _LOGO_FILE.search(n) and _VECTOR_EXT.search(n)})
    logo_raster = sorted({n for n in names if _LOGO_FILE.search(n) and _RASTER_EXT.search(n)})
    boilerplate_text = boilerplate_arg or None
    boilerplate_source = "given" if boilerplate_arg else None
    if boilerplate_text is None and page:
        for para in page.get("paragraphs") or []:
            if _BOILERPLATE_HINT.search(para) and len(_TOKEN.findall(para)) >= 15:
                boilerplate_text = para
                boilerplate_source = "page paragraph"
                break
    if boilerplate_text is None:
        hinted = [n for n in names if re.search(r"(boilerplate|apie|about|aprasym|description)", n, re.IGNORECASE) and re.search(r"\.(md|txt|docx?|pdf)$", n, re.IGNORECASE)]
        if hinted:
            boilerplate_source = f"file {hinted[0]} (length not checked)"
    boilerplate_words = len(_TOKEN.findall(boilerplate_text)) if boilerplate_text else None
    contact_text = f"{contact_arg} {(page.get('body_text') if page else '') or ''} {' '.join(page.get('mailto_links') or []) if page else ''}"
    emails = sorted(set(_EMAIL.findall(contact_text)))
    phones = _phones_in(contact_text + " " + " ".join(page.get("tel_links") or []) if page else contact_text)
    image_rows = []
    for img in images:
        src = str(img.get("src") or "")
        if _LOGO_FILE.search(src):
            continue
        size = _SIZE_IN_NAME.search(src)
        image_rows.append({"src": src, "alt": img.get("alt"), "size_in_name": f"{size.group(1)}x{size.group(2)}" if size else None})
    for f in files:
        meta = file_meta.get(f, {})
        if _RASTER_EXT.search(f) and not _LOGO_FILE.search(f):
            size = _SIZE_IN_NAME.search(f)
            w, h = meta.get("width"), meta.get("height")
            image_rows.append({"src": f, "alt": meta.get("alt"), "size_in_name": f"{size.group(1)}x{size.group(2)}" if size else (f"{w}x{h}" if w and h else None)})
    items = {
        "logo_vector": {"present": bool(logo_vector), "files": logo_vector},
        "logo_raster": {"present": bool(logo_raster), "files": logo_raster},
        "boilerplate": {"present": boilerplate_text is not None or boilerplate_source is not None, "words": boilerplate_words, "within_limit": (boilerplate_words <= BOILERPLATE_MAX_WORDS) if boilerplate_words is not None else None, "source": boilerplate_source, "text": (boilerplate_text or "")[:400] or None},
        "contact": {"present": bool(emails or phones), "emails": emails, "phones": [f"+{p}" for p in phones]},
        "images_sized": {"present": bool(image_rows) and all(r["size_in_name"] for r in image_rows), "images": len(image_rows), "without_size": [r["src"] for r in image_rows if not r["size_in_name"]]},
        "images_alt": {"present": bool(image_rows) and all(r["alt"] for r in image_rows), "without_alt": [r["src"] for r in image_rows if not r["alt"]]},
    }
    gaps = []
    for key in required:
        item = items.get(key)
        if item is None:
            warnings.append(f"unknown required item {key!r}")
            continue
        if not item["present"]:
            gaps.append(key)
            findings.append(_finding(f"kit_missing_{key}", "block" if key in ("logo_vector", "boilerplate", "contact") else "warn", label, item, f"kit lacks {key.replace('_', ' ')}"))
    if items["boilerplate"]["within_limit"] is False:
        gaps.append("boilerplate_too_long")
        findings.append(_finding("kit_boilerplate_long", "warn", label, boilerplate_words, f"boilerplate is {boilerplate_words} words; partners paste at most {BOILERPLATE_MAX_WORDS}"))
    if not image_rows and ("images_sized" in required or "images_alt" in required):
        missing.append("images: none found on the page or in files; sizes and alt not checked")
    summary = f"{len(required) - len([g for g in gaps if g in required])} of {len(required)} required kit items present; gaps: {', '.join(gaps) or 'none'}"
    return _finish("kit_check", {"page": label, "items": items, "gaps": gaps, "required": required, "image_rows": image_rows}, findings, missing, warnings, summary)


# --- email_auth ---------------------------------------------------------------


def _unfold_headers(raw: str) -> list[str]:
    head = raw.replace("\r\n", "\n").split("\n\n", 1)[0]
    lines: list[str] = []
    for line in head.split("\n"):
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += " " + line.strip()
        else:
            lines.append(line)
    return lines


def _org_domain(domain: str) -> str:
    parts = domain.lower().strip(".").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain.lower()


def _mode_email_auth(args: dict) -> dict[str, Any]:
    headers_text = str(args.get("headers_text") or args.get("headers") or "")
    domain = _clean(args.get("domain")).lower()
    selectors = [_clean(s) for s in _list_arg(args.get("dkim_selectors"), "dkim_selectors") if _clean(s)] or ["default", "selector1", "selector2", "ml", "k1", "s1", "google"]
    findings: list[dict] = []
    missing: list[str] = []
    warnings: list[str] = []
    if not headers_text.strip() and not domain:
        raise ToolError("headers_text (a raw email with headers) or domain is required")
    lines = _unfold_headers(headers_text) if headers_text.strip() else []
    from_domain = None
    auth_results: list[str] = []
    received_spf: list[str] = []
    dkim_sigs: list[dict[str, str]] = []
    return_path = None
    for line in lines:
        name, _, value = line.partition(":")
        key = name.strip().lower()
        value = value.strip()
        if key == "from":
            m = re.search(r"@([\w.-]+)", value)
            if m and from_domain is None:
                from_domain = m.group(1).lower().rstrip(".>")
        elif key in ("return-path", "envelope-from"):
            m = re.search(r"@([\w.-]+)", value)
            if m:
                return_path = m.group(1).lower().rstrip(".>")
        elif key in ("authentication-results", "arc-authentication-results"):
            auth_results.append(value)
        elif key == "received-spf":
            received_spf.append(value)
        elif key == "dkim-signature":
            tags = {}
            for part in value.split(";"):
                k, _, v = part.strip().partition("=")
                if k.strip():
                    tags[k.strip().lower()] = v.strip()
            dkim_sigs.append({"d": tags.get("d"), "s": tags.get("s"), "a": tags.get("a"), "has_bh": "bh" in tags, "has_b": "b" in tags})
    spf = {"result": None, "source": None, "domain": None}
    dkim = {"result": None, "domains": [], "selectors": [], "signatures": dkim_sigs}
    dmarc = {"result": None, "policy": None}
    for value in auth_results:
        for m in _AUTH_RESULT.finditer(value):
            method, result = m.group(1).lower(), m.group(2).lower()
            tail = value[m.end():m.end() + 200]
            if method == "spf" and spf["result"] is None:
                spf["result"] = result
                spf["source"] = "authentication-results"
                dm = re.search(r"(?:smtp\.mailfrom|envelope-from|mailfrom)=\"?(?:[^@\s\"]+@)?([\w.-]+)", tail, re.IGNORECASE)
                spf["domain"] = dm.group(1).lower() if dm else None
            elif method == "dkim":
                if dkim["result"] is None or (result == "pass" and dkim["result"] != "pass"):
                    dkim["result"] = result
                dm = re.search(r"header\.d=([\w.-]+)", tail, re.IGNORECASE) or re.search(r"header\.i=@?(?:[^@\s]+@)?([\w.-]+)", tail, re.IGNORECASE)
                sm = re.search(r"header\.s=([\w.-]+)", tail, re.IGNORECASE)
                if dm and dm.group(1).lower() not in dkim["domains"]:
                    dkim["domains"].append(dm.group(1).lower())
                if sm and sm.group(1) not in dkim["selectors"]:
                    dkim["selectors"].append(sm.group(1))
            elif method == "dmarc" and dmarc["result"] is None:
                dmarc["result"] = result
                pm = re.search(r"\bp=(none|quarantine|reject)", tail, re.IGNORECASE)
                dmarc["policy"] = pm.group(1).lower() if pm else None
    if spf["result"] is None and received_spf:
        m = re.match(r"\s*(pass|fail|softfail|neutral|none|temperror|permerror)", received_spf[0], re.IGNORECASE)
        if m:
            spf["result"] = m.group(1).lower()
            spf["source"] = "received-spf"
            dm = re.search(r"(?:envelope-from|mailfrom)=\"?(?:[^@\s\"]+@)?([\w.-]+)", received_spf[0], re.IGNORECASE)
            spf["domain"] = dm.group(1).lower() if dm else return_path
    if dkim["result"] is None and dkim_sigs:
        dkim["result"] = "signed_not_verified"
        for sig in dkim_sigs:
            if sig.get("d") and sig["d"].lower() not in dkim["domains"]:
                dkim["domains"].append(sig["d"].lower())
            if sig.get("s") and sig["s"] not in dkim["selectors"]:
                dkim["selectors"].append(sig["s"])
    domain = domain or from_domain or ""
    alignment = {"spf": None, "dkim": None}
    if from_domain:
        if spf.get("domain"):
            alignment["spf"] = _org_domain(spf["domain"]) == _org_domain(from_domain)
        if dkim["domains"]:
            alignment["dkim"] = any(_org_domain(d) == _org_domain(from_domain) for d in dkim["domains"])
    reasons = []
    if lines:
        if spf["result"] is None:
            reasons.append("no SPF result in the headers")
        elif spf["result"] != "pass":
            reasons.append(f"SPF {spf['result']}")
        elif alignment["spf"] is False:
            reasons.append(f"SPF passes for {spf['domain']} but From is {from_domain} (not aligned)")
        if dkim["result"] is None:
            reasons.append("no DKIM signature or result in the headers")
        elif dkim["result"] not in ("pass", "signed_not_verified"):
            reasons.append(f"DKIM {dkim['result']}")
        elif dkim["result"] == "signed_not_verified":
            reasons.append("DKIM signature present, no receiver verdict in the headers")
        elif alignment["dkim"] is False:
            reasons.append(f"DKIM signs {', '.join(dkim['domains'])} but From is {from_domain} (not aligned)")
        if dmarc["result"] is None:
            reasons.append("no DMARC result in the headers")
        elif dmarc["result"] != "pass":
            reasons.append(f"DMARC {dmarc['result']}")
        hard_fail = any(r in ("fail", "permerror") for r in (spf["result"], dkim["result"], dmarc["result"]))
        if hard_fail or dmarc["result"] == "fail":
            overall = "fail"
        elif spf["result"] == "pass" and dkim["result"] == "pass" and dmarc["result"] == "pass":
            overall = "pass"
        elif spf["result"] is None and dkim["result"] is None and dmarc["result"] is None:
            overall = "unknown"
            reasons.append("the pasted text carries no authentication headers; paste the full raw source (Show original)")
        else:
            overall = "weak"
    else:
        overall = "unknown"
        reasons.append("no headers given; only DNS lookups could tell, and there is no resolver here")
    if domain:
        missing.append(f"dns: SPF record for {domain} not looked up (no resolver here); run: dig +short TXT {domain}")
        missing.append(f"dns: DMARC record for {domain} not looked up; run: dig +short TXT _dmarc.{domain}")
        tried = dkim["selectors"] or selectors
        missing.append(f"dns: DKIM keys for {domain} not looked up; run: dig +short TXT <selector>._domainkey.{domain} for selectors {', '.join(tried)}")
    else:
        missing.append("dns: no domain known (no From header and no domain arg); nothing to look up")
    if overall == "fail":
        findings.append(_finding("email_auth_fail", "block", domain or None, reasons, "a receiver rejected or failed authentication; fix this before judging copy"))
    elif overall == "weak":
        findings.append(_finding("email_auth_weak", "warn", domain or None, reasons, "authentication incomplete in the headers: " + "; ".join(reasons)))
    elif overall == "unknown":
        findings.append(_finding("email_auth_unknown", "warn", domain or None, reasons, "; ".join(reasons)))
    summary = f"email authentication for {domain or 'unknown domain'} from headers: SPF {spf['result'] or 'none'}, DKIM {dkim['result'] or 'none'}, DMARC {dmarc['result'] or 'none'}; overall {overall}; DNS records not looked up"
    return _finish("email_auth", {"domain": domain or None, "from_domain": from_domain, "return_path_domain": return_path, "spf": spf, "dkim": dkim, "dmarc": dmarc,
                                  "alignment": alignment, "headers": {"spf": spf["result"], "dkim": dkim["result"], "dmarc": dmarc["result"]}, "overall": overall,
                                  "reasons": reasons, "dns": {"spf": None, "dmarc": None, "dkim": None, "status": "not_looked_up"}},
                   findings, missing, warnings, summary)


# --- search_region -------------------------------------------------------------


def _mode_search_region(args: dict, search) -> dict[str, Any]:
    query = _clean(args.get("query"))
    region = _clean(args.get("region")).lower().replace("_", "-")
    if not query:
        raise ToolError("query is required")
    if not region:
        raise ToolError("region is required, e.g. lv-lv")
    local_terms = [_clean(t) for t in _list_arg(args.get("local_terms") or args.get("terms"), "local_terms") if _clean(t)]
    findings: list[dict] = []
    missing: list[str] = []
    warnings: list[str] = []
    tld, market = _REGIONS.get(region, (None, None))
    if tld is None:
        parts = region.split("-")
        if len(parts) == 2 and len(parts[0]) == 2:
            tld, market = parts[0], parts[0].upper()
            warnings.append(f"region {region!r} is not in the known list; tld .{tld} taken from the code")
        else:
            raise ToolError(f"unsupported region {region!r}; use a code like lt-lt, lv-lv, pl-pl, de-de")
    missing.append(f"region: the searcher has no region parameter yet; {region} ({market}) was not applied, results are global unless a site:.{tld} variant is used")
    variants = [{"query": query, "kind": "as_given", "region_applied": False}, {"query": f"{query} site:.{tld}", "kind": "site_tld", "region_applied": "proxy"}]
    for term in local_terms:
        variants.append({"query": term, "kind": "local_term", "region_applied": False})
        variants.append({"query": f"{term} site:.{tld}", "kind": "local_term_site_tld", "region_applied": "proxy"})
    if not local_terms:
        warnings.append("no local_terms given: only the query as given and its site:.tld variant; native-language terms must come from the caller")
    results_by_variant = []
    for v in variants:
        if v["kind"] in ("site_tld", "local_term_site_tld") and search is not None:
            results = _run_search(search, v["query"], missing)
            results_by_variant.append({**v, "results": results or [], "status": "ok" if results else "no_results" if results is not None else "search_unavailable"})
        else:
            results_by_variant.append({**v, "results": None, "status": "not_run" if search is not None else "search_unavailable"})
            if search is None:
                missing.append(f"search: {v['query']!r} not searched (no searcher here)")
    local_hosts = Counter()
    for row in results_by_variant:
        for r in row.get("results") or []:
            if r["domain"].endswith("." + tld):
                local_hosts[r["domain"]] += 1
    summary = f"{len(variants)} query variant(s) for {region} ({market}); region not applied by the searcher, site:.{tld} used as a proxy"
    return _finish("search_region", {"query": query, "region": region, "market": market, "tld": tld, "variants": results_by_variant, "local_hosts": dict(local_hosts),
                                     "region_supported": False}, findings, missing, warnings, summary)


# --- entry points -----------------------------------------------------------


def web_check(
    tools: WorkspaceTools | None,
    args: dict,
    fetch_html: Callable[[str], str] | None = None,
    fetch_meta: Callable[[str], dict] | None = None,
    search: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """One web check per ``mode``; see the module docstring for the list.

    Every result carries ``findings`` ordered block → warn → info, ``counts``,
    ``missing`` (what could not be fetched, searched or looked up), ``warnings``
    and a ``summary`` sentence a marketer can quote.
    """
    args = args or {}
    mode = _clean(args.get("mode")).lower()
    if not mode:
        raise ToolError(f"mode is required; one of {', '.join(MODES)}")
    if mode not in MODES:
        raise ToolError(f"unknown mode {mode!r}; one of {', '.join(MODES)}")
    if mode == "name_check":
        return _mode_name_check(args, search)
    if mode == "mention_scan":
        return _mode_mention_scan(args, search)
    if mode == "policy_finder":
        return _mode_policy_finder(tools, args, fetch_html)
    if mode == "listing_check":
        return _mode_listing_check(tools, args, fetch_html)
    if mode == "search_intent":
        return _mode_search_intent(args, search)
    if mode == "site_crawl":
        return _mode_site_crawl(tools, args, fetch_html, fetch_meta)
    if mode == "keyword_inspect":
        return _mode_keyword_inspect(tools, args, fetch_html, search)
    if mode == "link_prospects":
        return _mode_link_prospects(tools, args, fetch_html, fetch_meta)
    if mode == "locale_check":
        return _mode_locale_check(tools, args, fetch_html)
    if mode == "kit_check":
        return _mode_kit_check(tools, args, fetch_html)
    if mode == "email_auth":
        return _mode_email_auth(args)
    return _mode_search_region(args, search)


def web_check_tool(
    tools: WorkspaceTools | None,
    args: dict,
    fetch_html: Callable[[str], str] | None = None,
    fetch_meta: Callable[[str], dict] | None = None,
    search: Callable[[str], str] | None = None,
) -> str:
    """Backs the worker-facing ``web_check`` tool: JSON the worker can quote from."""
    return json.dumps(web_check(tools, args or {}, fetch_html, fetch_meta, search), ensure_ascii=False, indent=1, default=str)
