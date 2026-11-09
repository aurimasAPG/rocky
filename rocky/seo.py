"""The search-channel checks a marketer wants before paying for traffic, done by code.

Eyeballing titles in a spreadsheet, pasting URLs into a chat window, reading
robots.txt by hand, skimming a search-terms export: all of it is slow and none
of it is reproducible. ``seo_inspect`` takes the pages, URLs, site or export
and answers one question per ``mode``:

- ``snippet``: are the title and description descriptive, unique, within the
  length Google shows, and do they promise what the page delivers?
- ``urls``: are the addresses readable, do canonical signals point one way?
- ``crawl_rules``: does robots.txt block an important page for Googlebot, and
  are the important pages in a sitemap at all?
- ``structured_data``: does the JSON-LD say the same price, stock and rating
  the visitor sees?
- ``crawler_content``: which facts exist only after JavaScript runs?
- ``templates``: how much of a templated page is unique and does it carry the
  fields a buyer decides on?
- ``serp``: where does the brand rank for the queries a shopper types at each
  stage, and what does each result page's intent look like?
- ``keywords``: which search terms cluster together, which ad groups mix
  needs, and which terms burn money without converting?

No network inside the module: pages come through an injected ``fetch_html``
callable, searches through ``search`` (lines of ``- title\\n  url``), and HTTP
status and redirect chains through an optional ``fetch_meta``. Without a
callable the url, query or chain lands under ``missing``, never guessed.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from html.parser import HTMLParser
from statistics import median
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

from rocky.pageinspect import LEVEL_ORDER, content_words, detect_language, extract_page, find_prices, same_stem
from rocky.tools import ToolError, WorkspaceTools

MODES = ("snippet", "urls", "crawl_rules", "structured_data", "crawler_content", "templates", "serp", "keywords")

TITLE_MAX = 60
META_MAX = 160
THIN_WORDS = 150
FIRST_SCREEN_CHARS = 600
SHINGLE = 5
MAX_SITEMAP_URLS = 50_000
MAX_SITEMAP_DEPTH = 3
MAX_URLS = 200
MAX_EXAMPLES = 10
TEMPLATE_MIN_PAGES = 3
TEMPLATE_MAX_PAGES = 10
DUPLICATE_RATIO = 0.2
LOW_UNIQUE_RATIO = 0.5
AMBIGUOUS_BELOW = 0.6
DEFAULT_TOP_N = 8
DEFAULT_MIN_COST = 10.0
DEFAULT_MAX_CONVERSIONS = 0.0
DEFAULT_MIN_SHARE = 0.2
STEM = 5

_TOKEN = re.compile(r"\w+", re.UNICODE)
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_DIGIT_RUN = re.compile(r"\d{5,}")
TRACKING_PARAMS = frozenset(
    {"sessionid", "session_id", "sid", "phpsessid", "jsessionid", "fbclid", "gclid", "dclid", "msclkid", "yclid",
     "mc_cid", "mc_eid", "ref", "referrer", "_ga", "_gl", "igshid", "sort", "order", "orderby", "sortby", "view", "page_view"}
)
SORT_PARAMS = frozenset({"sort", "order", "orderby", "sortby", "dir", "direction"})
DECISION_FIELDS = ("price", "availability", "delivery", "reviews", "specs", "images", "local_info")
_FIELD_PATTERNS = {
    "price": re.compile(r"\b(kaina|kainos|price|prices|pricing)\b"),
    "availability": re.compile(r"(in stock|out of stock|availability|available|sandėl|turime|likutis|išparduota|nėra prekyboje|prekyboje)"),
    "delivery": re.compile(r"(delivery|deliver|shipping|pristatym|siunt|atsiėm|pickup|pick-up)"),
    "reviews": re.compile(r"(review|atsiliepim|įvertinim|rating|\d[.,]\d\s*/\s*5|žvaigžd|stars?\b)"),
    "specs": re.compile(r"(specification|specs\b|specifikac|technini|parametr|dimension|matmen|weight|svoris|material|medžiag|capacity|talpa|galia|power\b)"),
    "local_info": re.compile(r"(address|adresas|phone|tel\.|telefon|opening hours|darbo laikas|\bmap\b|žemėlap|kontakt|contact|gatv|\bpr\. ?\d|\bg\. ?\d|vilni|kaun|klaipėd|šiauli|panevėž)"),
}
_INTENT_RULES = (
    ("how_to", re.compile(r"(how to|how do|kaip |kaip$|guide|gidas|tutorial|instrukcij|step[- ]by|žingsn|\btips\b|patarim|diy)")),
    ("local", re.compile(r"(\bnear\b|šalia|netoli|address|adresas|\bmap\b|žemėlap|opening hours|darbo laikas|contact|kontakt|vilni|kaun|klaipėd|šiauli|panevėž)")),
    ("price", re.compile(r"(price|prices|pricing|kaina|kainos|\bcost\b|cheap|pigi|pigū|discount|nuolaid|akcij|\bdeal|€|\beur\b)")),
    ("commercial", re.compile(r"(\bbuy\b|pirkti|pirk|\bshop\b|e-shop|eshop|\border\b|užsak|cart|krepšel|parduotuv|outlet|\bstore\b|prek[eė]s)")),
    ("info", re.compile(r".")),
)
_MODIFIERS = ("free", "nemokam", "cheap", "pig", "best", "geriaus", "top", "near", "šalia", "price", "kain", "review", "apžvalg",
              "atsiliep", "how", "kaip", "vs", "ar", "without", "be ")
_STAGE_QUERIES = {
    "en": {
        "category": ["{category}", "best {category}", "{category} guide"],
        "shortlist": ["{category} comparison", "{category} reviews", "which {category} to buy"],
        "model": ["{product} review", "{product} vs", "{product} test"],
        "price_specs": ["{product} price", "{product} specs", "buy {product}"],
    },
    "lt": {
        "category": ["{category}", "geriausi {category}", "{category} apžvalga"],
        "shortlist": ["{category} palyginimas", "{category} atsiliepimai", "kokį {category} rinktis"],
        "model": ["{product} apžvalga", "{product} atsiliepimai", "{product} testas"],
        "price_specs": ["{product} kaina", "{product} specifikacijos", "pirkti {product}"],
    },
}
DEFAULT_STAGES = ("category", "shortlist", "model", "price_specs")
_TERM_HEADERS = ("search term", "search terms", "paieškos terminas", "paieškos užklausa", "keyword", "raktažodis", "query", "term", "užklausa")
_CLICK_HEADERS = ("clicks", "paspaudimai", "paspaud.")
_COST_HEADERS = ("cost", "kaina", "išlaidos", "spend", "sąnaudos")
_CONV_HEADERS = ("conversions", "conv.", "konversijos", "konv.")
_VALUE_HEADERS = ("conv. value", "all conv. value", "conversion value", "konv. vertė", "konversijų vertė", "value", "vertė")
_GROUP_HEADERS = ("ad group", "skelbimų grupė", "group", "grupė", "campaign", "kampanija")
_IN_STOCK = re.compile(r"(in stock|yra sandėlyje|turime sandėlyje|turime|prekyboje\b(?! nėra))")
_OUT_OF_STOCK = re.compile(r"(out of stock|sold out|išparduota|nėra sandėlyje|nėra prekyboje|neturime)")


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").replace("\xa0", " ")).strip()


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


def _number_arg(args: dict, key: str, default: float) -> float:
    raw = args.get(key)
    if raw in (None, ""):
        return default
    try:
        return float(str(raw).replace(",", ".").replace("€", "").replace("%", "").strip())
    except ValueError as exc:
        raise ToolError(f"{key} must be a number, got {raw!r}") from exc


# --- page sources -----------------------------------------------------------


def _source_of(item: Any) -> tuple[str, str]:
    """(kind, value) for a page given as {url|path|html} or a bare string."""
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


def _label(kind: str, value: str) -> str:
    return value if kind != "html" else "html"


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
    if fetch_html is None:
        missing.append(f"page: {value} not fetched (no fetcher here); give the page as html or path, or open it with the browser tools")
        return None
    try:
        fetched = fetch_html(value)
    except Exception as exc:
        missing.append(f"page: {value} could not be fetched ({exc})")
        return None
    if isinstance(fetched, bytes):
        fetched = fetched.decode("utf-8", errors="replace")
    return str(fetched or "")


def _single_page(tools: WorkspaceTools | None, args: dict, fetch_html: Callable[[str], str] | None, missing: list[str], *, required: bool = True) -> tuple[str | None, str | None]:
    given = {k: args.get(k) for k in ("html", "path", "url") if args.get(k) not in (None, "")}
    if not given:
        if required:
            raise ToolError("no page source: give the page as html, as a workspace path, or as a url")
        return None, None
    kind, value = _source_of(given)
    return _read_source(tools, kind, value, fetch_html, missing), _label(kind, value)


def _pages(tools: WorkspaceTools | None, args: dict, fetch_html: Callable[[str], str] | None, missing: list[str], key: str = "pages") -> list[tuple[str, str]]:
    """[(label, html)] for every page that could be read; unreadable ones land in missing."""
    items = args.get(key)
    if not items and any(args.get(k) for k in ("html", "path", "url")):
        items = [{k: args[k] for k in ("html", "path", "url") if args.get(k)}]
    items = _list_arg(items, key)
    if not items:
        raise ToolError(f"{key} is required: a list of {{url|path|html}} entries")
    out: list[tuple[str, str]] = []
    for item in items:
        kind, value = _source_of(item)
        html = _read_source(tools, kind, value, fetch_html, missing)
        if html is not None:
            out.append((_label(kind, value), html))
    return out


# --- the head and the parts extract_page leaves out -------------------------


class _SeoParser(HTMLParser):
    """Canonical, robots meta, JSON-LD blocks, every heading and every image."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.canonical: str | None = None
        self.robots: list[str] = []
        self.jsonld: list[str] = []
        self.headings: list[dict[str, Any]] = []
        self.images: list[dict[str, Any]] = []
        self._ld: list[str] | None = None
        self._heading: tuple[int, list[str]] | None = None

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {k.lower(): (v or "") for k, v in attrs if k}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        a = self._attrs(attrs)
        if tag in ("link", "meta", "img"):
            self.handle_startendtag(tag, attrs)
            return
        if tag == "script" and "ld+json" in a.get("type", "").lower():
            self._ld = []
        elif len(tag) == 2 and tag[0] == "h" and tag[1].isdigit():
            self._heading = (int(tag[1]), [])

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        a = self._attrs(attrs)
        if tag == "link" and "canonical" in a.get("rel", "").lower().split() and a.get("href") and self.canonical is None:
            self.canonical = a["href"].strip()
        elif tag == "meta" and a.get("name", "").lower() in ("robots", "googlebot") and a.get("content"):
            self.robots.append(_clean(a["content"]).lower())
        elif tag == "img":
            alt = _clean(a.get("alt", ""))
            self.images.append({"src": a.get("src") or a.get("data-src") or "", "alt": alt or None})

    def handle_data(self, data: str) -> None:
        if self._ld is not None:
            self._ld.append(data)
        if self._heading is not None:
            self._heading[1].append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "script" and self._ld is not None:
            self.jsonld.append("".join(self._ld).strip())
            self._ld = None
        elif self._heading is not None and tag == f"h{self._heading[0]}":
            text = _clean("".join(self._heading[1]))
            if text:
                self.headings.append({"level": self._heading[0], "text": text})
            self._heading = None


def _scan(html: str) -> _SeoParser:
    parser = _SeoParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception as exc:  # pragma: no cover - HTMLParser rarely raises
        raise ToolError(f"the page could not be parsed: {exc}") from exc
    return parser


def _noindex(robots: list[str]) -> bool:
    return any("noindex" in r for r in robots)


# --- urls -------------------------------------------------------------------


def _host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _normalise_url(url: str) -> str:
    """Scheme-free, www-free, tracking- and sort-free, no trailing slash: two addresses of one page compare equal."""
    p = urlsplit(url.strip())
    path = p.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    query = sorted(
        (k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS and not k.lower().startswith("utm_")
    )
    out = f"{_host(url)}{path}"
    if query:
        out += "?" + urlencode(query)
    return out


def _parse_search(text: Any) -> tuple[list[dict[str, Any]], str | None]:
    """[{rank, title, url}] from WebTools.search lines, or (empty, reason)."""
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
        reason = text.strip().splitlines()[0] if text.strip() else "no results"
        return [], reason
    return results, None


def _url_readability(url: str) -> dict[str, Any]:
    p = urlsplit(url)
    path = p.path or "/"
    segments = [s for s in path.split("/") if s]
    params = parse_qsl(p.query, keep_blank_values=True)
    issues: list[str] = []
    words = [w for s in segments for w in re.split(r"[-_.+]", s) if _WORD.fullmatch(w) and len(w) >= 3]
    if _DIGIT_RUN.search(path) or any(_DIGIT_RUN.search(v) for _, v in params):
        issues.append("numeric_id")
    if any(k.lower() in TRACKING_PARAMS or k.lower().startswith("utm_") for k, _ in params):
        issues.append("tracking_params")
    if any(k.lower() in SORT_PARAMS for k, _ in params):
        issues.append("sort_params")
    if any(ch.isupper() for ch in path):
        issues.append("upper_case")
    if "_" in path:
        issues.append("underscores")
    if len(params) > 2:
        issues.append("many_params")
    if segments and not words:
        issues.append("no_words")
    if len(segments) > 4:
        issues.append("deep_path")
    if len(url) > 100:
        issues.append("long_url")
    if re.search(r"\.(php|aspx?|jsp|cgi)$", path, re.I):
        issues.append("script_extension")
    if re.search(r"%[0-9A-Fa-f]{2}", path):
        issues.append("encoded_characters")
    serious = {"numeric_id", "tracking_params", "no_words", "many_params", "upper_case", "encoded_characters"}
    return {"readable": not any(i in serious for i in issues), "issues": issues, "path_words": words, "depth": len(segments), "params": len(params)}


_URL_NOTES = {
    "numeric_id": "a run of five or more digits: a stranger cannot predict the page from the address",
    "tracking_params": "session or tracking parameters in the address: every share creates another copy of the page",
    "sort_params": "a sort or order parameter: the same list under another address",
    "upper_case": "upper-case letters: servers may treat /Shoes and /shoes as two pages",
    "underscores": "underscores instead of hyphens",
    "many_params": "more than two query parameters",
    "no_words": "no readable word in the path",
    "deep_path": "more than four folders deep",
    "long_url": "over 100 characters; it breaks when pasted into a plain-text message",
    "script_extension": "a script file extension in the address",
    "encoded_characters": "percent-encoded characters: the address is unreadable once pasted",
}


def _url_list(tools: WorkspaceTools | None, args: dict, fetch_html: Callable[[str], str] | None, missing: list[str], warnings: list[str]) -> list[str]:
    urls = _list_arg(args.get("urls"), "urls")
    path = args.get("path")
    sitemap_url = args.get("sitemap_url")
    if path:
        if tools is None:
            raise ToolError("a path needs a workspace")
        target = tools.resolve(str(path))
        if not target.is_file():
            raise ToolError(f"path missing: {path}")
        for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip().split()[0] if line.strip() else ""
            if line.startswith(("http://", "https://")):
                urls.append(line)
    if sitemap_url:
        found, _ = _collect_sitemap_urls([str(sitemap_url)], fetch_html, missing, warnings)
        urls.extend(found)
    urls = [str(u).strip() for u in urls if str(u).strip()]
    if not urls:
        raise ToolError("no urls: give urls, a sitemap_url, or a path to a text file with one url per line")
    seen: list[str] = []
    for u in urls:
        if u not in seen:
            seen.append(u)
    if len(seen) > MAX_URLS:
        warnings.append(f"{len(seen)} urls given; only the first {MAX_URLS} were checked")
        seen = seen[:MAX_URLS]
    return seen


def _chain(url: str, fetch_meta: Callable[[str], dict] | None) -> tuple[list[dict[str, Any]] | None, str | None, dict[str, str]]:
    if fetch_meta is None:
        return None, None, {}
    try:
        meta = fetch_meta(url) or {}
    except Exception as exc:
        return [{"url": url, "status": None, "error": str(exc)}], None, {}
    headers = {str(k).lower(): str(v) for k, v in (meta.get("headers") or {}).items()}
    chain = meta.get("chain")
    if isinstance(chain, list) and chain:
        chain = [{"url": str(h.get("url")), "status": h.get("status")} for h in chain if isinstance(h, dict)]
    else:
        status = meta.get("status")
        final = str(meta.get("final_url") or url)
        chain = [{"url": url, "status": status}]
        if _normalise_url(final) != _normalise_url(url):
            chain = [{"url": url, "status": status if status and status >= 300 else 301}, {"url": final, "status": 200}]
    final_url = str(meta.get("final_url") or chain[-1]["url"])
    return chain, final_url, headers


def _header_canonical(headers: dict[str, str]) -> str | None:
    link = headers.get("link", "")
    m = re.search(r"<([^>]+)>\s*;\s*rel=\"?canonical\"?", link, re.I)
    return m.group(1).strip() if m else None


def _mode_urls(tools, args, fetch_html, fetch_meta) -> dict[str, Any]:
    missing: list[str] = []
    warnings: list[str] = []
    findings: list[dict] = []
    urls = _url_list(tools, args, fetch_html, missing, warnings)
    html_by_url = args.get("html_by_url") if isinstance(args.get("html_by_url"), dict) else {}
    if fetch_meta is None:
        missing.append("redirect chains and HTTP status: no fetch_meta here; the chain is not reported")
    if fetch_html is None and not html_by_url:
        missing.append("canonical tags and robots meta: no fetcher here; give html_by_url or fetch the pages")

    rows: list[dict[str, Any]] = []
    for url in urls:
        read = _url_readability(url)
        row: dict[str, Any] = {"url": url, "normalised_url": _normalise_url(url), **read,
                               "canonical_href": None, "canonical_source": None, "canonical_points_elsewhere": None,
                               "robots_noindex": None, "redirect_chain": None, "final_url": None}
        for issue in read["issues"]:
            level = "warn" if issue in ("numeric_id", "tracking_params", "no_words", "many_params", "upper_case", "encoded_characters") else "info"
            findings.append(_finding(f"url_{issue}", level, url, read["path_words"], f"{url}: {_URL_NOTES[issue]}."))
        chain, final_url, headers = _chain(url, fetch_meta)
        if chain is not None:
            row["redirect_chain"] = chain
            row["final_url"] = final_url
            if len(chain) > 2:
                findings.append(_finding("redirect_chain_long", "warn", url, chain, f"{url} redirects {len(chain) - 1} times before landing on {final_url}; every hop costs crawl budget and link value."))
            elif len(chain) == 2:
                findings.append(_finding("redirects", "info", url, chain, f"{url} redirects to {final_url}."))
            status = chain[-1].get("status")
            if isinstance(status, int) and status >= 400:
                findings.append(_finding("url_error_status", "block", url, status, f"{url} answers {status}: the address is dead for visitors and crawlers."))
            header_canonical = _header_canonical(headers)
            if header_canonical:
                row["canonical_href"] = urljoin(url, header_canonical)
                row["canonical_source"] = "header"
        html = html_by_url.get(url)
        if html is None and fetch_html is not None:
            html = _read_source(tools, "url", url, fetch_html, missing)
        if html is not None:
            scan = _scan(html)
            if scan.canonical and row["canonical_source"] != "header":
                row["canonical_href"] = urljoin(url, scan.canonical)
                row["canonical_source"] = "tag"
            row["robots_noindex"] = _noindex(scan.robots)
            if row["canonical_href"]:
                row["canonical_points_elsewhere"] = _normalise_url(row["canonical_href"]) != _normalise_url(url)
            else:
                findings.append(_finding("canonical_missing", "warn", url, None, f"{url} declares no canonical; every variant of the address competes as its own page."))
            if row["robots_noindex"]:
                findings.append(_finding("noindex", "warn", url, scan.robots, f"{url} carries a robots noindex meta: it will not appear in search."))
            if row["canonical_points_elsewhere"]:
                findings.append(_finding("canonical_elsewhere", "info", url, row["canonical_href"], f"{url} points its canonical to {row['canonical_href']}."))
        rows.append(row)

    # Groups: addresses that normalise to one page, or that share a canonical target.
    key_of: dict[str, str] = {}
    for row in rows:
        key = _normalise_url(row["canonical_href"]) if row["canonical_href"] else row["normalised_url"]
        key_of[row["url"]] = key
    # Members that normalise equal join the canonical target's group.
    norm_to_key: dict[str, str] = {}
    for row in rows:
        if row["canonical_href"]:
            norm_to_key.setdefault(row["normalised_url"], key_of[row["url"]])
    for row in rows:
        if not row["canonical_href"] and row["normalised_url"] in norm_to_key:
            key_of[row["url"]] = norm_to_key[row["normalised_url"]]
    groups: list[dict[str, Any]] = []
    for key in dict.fromkeys(key_of.values()):
        members = [r for r in rows if key_of[r["url"]] == key]
        if len(members) < 2:
            continue
        canonicals = {r["canonical_href"] for r in members}
        known = {c for c in canonicals if c}
        consistent = len(known) == 1 and None not in canonicals
        groups.append({"canonical_target": key, "members": [r["url"] for r in members], "canonicals": sorted(known), "consistent": consistent})
        if not consistent:
            without = [r["url"] for r in members if not r["canonical_href"]]
            if any(r["canonical_href"] is None and r["robots_noindex"] is None for r in members):
                note = f"{len(members)} addresses show one page ({key}); their canonical tags were not read, so whether they point one way is unknown."
                findings.append(_finding("duplicate_addresses", "warn", key, [r["url"] for r in members], note))
            else:
                note = f"{len(members)} addresses show one page ({key}) but do not point one way: " + (f"{len(without)} without a canonical" if without else f"canonicals {', '.join(sorted(known))}") + ". Search engines pick a version themselves."
                findings.append(_finding("canonical_inconsistent", "block", key, [r["url"] for r in members], note))
        else:
            findings.append(_finding("canonical_consistent", "info", key, [r["url"] for r in members], f"{len(members)} addresses point their canonical to one target."))

    unreadable = [r["url"] for r in rows if not r["readable"]]
    clauses = [f"{len(rows)} urls checked", f"{len(unreadable)} not readable by a stranger"]
    bad_groups = [g for g in groups if not g["consistent"]]
    if groups:
        clauses.append(f"{len(groups)} groups of addresses show one page, {len(bad_groups)} without a consistent canonical")
    noindexed = [r["url"] for r in rows if r["robots_noindex"]]
    if noindexed:
        clauses.append(f"{len(noindexed)} carry noindex")
    body = {"urls": rows, "groups": groups, "unreadable": unreadable}
    return _finish("urls", body, findings, missing, warnings, "; ".join(clauses))


# --- robots.txt and sitemaps ------------------------------------------------


def _parse_robots(text: str) -> dict[str, Any]:
    groups: list[dict[str, Any]] = []
    sitemaps: list[str] = []
    current: dict[str, Any] | None = None
    last_was_agent = False
    for raw in str(text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field = field.strip().lower()
        value = value.strip()
        if field == "sitemap":
            if value:
                sitemaps.append(value)
            continue
        if field == "user-agent":
            if current is None or not last_was_agent:
                current = {"agents": [], "rules": []}
                groups.append(current)
            current["agents"].append(value.lower())
            last_was_agent = True
            continue
        last_was_agent = False
        if field in ("allow", "disallow") and current is not None:
            if field == "disallow" and value == "":
                continue
            current["rules"].append((field, value))
    return {"groups": groups, "sitemaps": sitemaps}


def _robots_group(robots: dict[str, Any], agent: str) -> dict[str, Any] | None:
    agent = agent.lower()
    best: tuple[int, dict] | None = None
    for group in robots["groups"]:
        for token in group["agents"]:
            if token != "*" and (agent.startswith(token) or token.startswith(agent)):
                if best is None or len(token) > best[0]:
                    best = (len(token), group)
    if best:
        return best[1]
    for group in robots["groups"]:
        if "*" in group["agents"]:
            return group
    return None


def _rule_regex(pattern: str) -> re.Pattern:
    anchored = pattern.endswith("$")
    core = pattern[:-1] if anchored else pattern
    parts = [re.escape(p) for p in core.split("*")]
    return re.compile("^" + ".*".join(parts) + ("$" if anchored else ""))


def _robots_verdict(group: dict[str, Any] | None, url: str) -> tuple[bool, str | None]:
    """(allowed, matching rule) by the longest-match rule; on a tie Allow wins."""
    if group is None:
        return True, None
    p = urlsplit(url)
    path = (p.path or "/") + (f"?{p.query}" if p.query else "")
    best: tuple[int, int, str, str] | None = None  # (length, allow_first, kind, pattern)
    for kind, pattern in group["rules"]:
        if _rule_regex(pattern).match(path):
            cand = (len(pattern), 1 if kind == "allow" else 0, kind, pattern)
            if best is None or cand[:2] > best[:2]:
                best = cand
    if best is None:
        return True, None
    return best[2] == "allow", f"{best[2].capitalize()}: {best[3]}"


def _collect_sitemap_urls(starts: list[str], fetch_html, missing: list[str], warnings: list[str]) -> tuple[list[str], list[dict[str, Any]]]:
    """Every <loc> reachable from the sitemaps, recursing through indexes up to three levels."""
    urls: list[str] = []
    fetched: list[dict[str, Any]] = []
    if fetch_html is None:
        for s in starts:
            missing.append(f"sitemap: {s} not fetched (no fetcher here)")
        return urls, fetched
    seen: set[str] = set()
    queue: list[tuple[str, int]] = [(s, 0) for s in starts]
    while queue and len(urls) < MAX_SITEMAP_URLS:
        sitemap_url, depth = queue.pop(0)
        if sitemap_url in seen:
            continue
        seen.add(sitemap_url)
        try:
            text = fetch_html(sitemap_url)
        except Exception as exc:
            fetched.append({"url": sitemap_url, "kind": "unreachable", "urls": 0, "error": str(exc)})
            continue
        text = text.decode("utf-8", errors="replace") if isinstance(text, bytes) else str(text or "")
        try:
            root = ET.fromstring(text.strip())
        except ET.ParseError:
            if text.strip() and not text.lstrip().startswith("<"):
                plain = [line.strip() for line in text.splitlines() if line.strip().startswith(("http://", "https://"))]
                if plain:
                    fetched.append({"url": sitemap_url, "kind": "text", "urls": len(plain)})
                    urls.extend(plain[: MAX_SITEMAP_URLS - len(urls)])
                    continue
            fetched.append({"url": sitemap_url, "kind": "invalid", "urls": 0})
            warnings.append(f"sitemap {sitemap_url} is not valid XML")
            continue
        local = root.tag.split("}")[-1].lower()
        locs = [_clean(el.text) for el in root.iter() if el.tag.split("}")[-1].lower() == "loc" and el.text]
        if local == "sitemapindex":
            fetched.append({"url": sitemap_url, "kind": "index", "urls": 0, "children": len(locs)})
            if depth + 1 >= MAX_SITEMAP_DEPTH:
                warnings.append(f"sitemap index {sitemap_url} nests deeper than {MAX_SITEMAP_DEPTH} levels; deeper sitemaps were not read")
                continue
            queue.extend((loc, depth + 1) for loc in locs)
        else:
            take = locs[: MAX_SITEMAP_URLS - len(urls)]
            fetched.append({"url": sitemap_url, "kind": "urlset", "urls": len(take)})
            urls.extend(take)
    if len(urls) >= MAX_SITEMAP_URLS:
        warnings.append(f"sitemaps hold {MAX_SITEMAP_URLS} urls or more; the count stopped at the cap")
    return urls, fetched


def _mode_crawl_rules(tools, args, fetch_html) -> dict[str, Any]:
    missing: list[str] = []
    warnings: list[str] = []
    findings: list[dict] = []
    site = _clean(args.get("site"))
    if not site:
        raise ToolError("site is required: the store's address, e.g. https://www.shop.lt")
    if not site.startswith(("http://", "https://")):
        site = "https://" + site
    base = f"{urlsplit(site).scheme}://{urlsplit(site).netloc}"
    important = [str(u).strip() for u in _list_arg(args.get("important_urls"), "important_urls")]
    if tools is not None and len(important) == 1 and not important[0].startswith("http"):
        target = tools.resolve(important[0])
        if target.is_file():
            important = [line.strip().split()[0] for line in target.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip().startswith("http")]
    important = [u if u.startswith("http") else urljoin(base + "/", u) for u in important]
    staging_hosts = [_host(h if h.startswith("http") else "https://" + h) for h in _list_arg(args.get("staging_hosts"), "staging_hosts")]
    agents = [str(a) for a in _list_arg(args.get("user_agents"), "user_agents")] or ["Googlebot", "*"]

    robots_url = base + "/robots.txt"
    robots_text: str | None = None
    robots_status = "not_fetched"
    if fetch_html is None:
        missing.append(f"robots.txt: {robots_url} not fetched (no fetcher here)")
    else:
        try:
            robots_text = fetch_html(robots_url)
            robots_text = robots_text.decode("utf-8", errors="replace") if isinstance(robots_text, bytes) else str(robots_text or "")
            robots_status = "ok"
            if "<html" in robots_text[:500].lower():
                robots_status = "html"
                warnings.append("robots.txt answered with an HTML page, not rules; crawlers treat that as no restrictions")
                robots_text = ""
        except Exception as exc:
            robots_status = "unreachable"
            findings.append(_finding("robots_missing", "info", robots_url, str(exc), f"{robots_url} could not be read ({exc}); crawlers treat a missing robots.txt as allow-all."))
            robots_text = ""
    robots = _parse_robots(robots_text or "")

    rules_out = [{"agents": g["agents"], "rules": [f"{k.capitalize()}: {v}" for k, v in g["rules"]]} for g in robots["groups"]]
    for group in robots["groups"]:
        if any(k == "disallow" and v == "/" for k, v in group["rules"]) and not any(k == "allow" for k, v in group["rules"]):
            findings.append(_finding("site_blocked", "block", robots_url, group["agents"], f"robots.txt disallows the whole site for {', '.join(group['agents'])}: a staging rule left on the live store."))
    if staging_hosts:
        for s in robots["sitemaps"]:
            if _host(s) in staging_hosts:
                findings.append(_finding("staging_sitemap", "warn", robots_url, s, f"robots.txt names a sitemap on the staging host {s}."))
        for u in important:
            if _host(u) in staging_hosts:
                findings.append(_finding("staging_url", "warn", u, _host(u), f"{u} lives on a staging host; it is not the live page."))

    checked: list[dict[str, Any]] = []
    blocked_for_google: list[str] = []
    for url in important:
        by_agent: dict[str, Any] = {}
        for agent in agents:
            allowed, rule = _robots_verdict(_robots_group(robots, agent), url)
            by_agent[agent] = {"allowed": allowed if robots_text is not None else None, "rule": rule}
        checked.append({"url": url, "by_agent": by_agent})
        if robots_text is None:
            continue
        google = by_agent.get("Googlebot") or by_agent.get(agents[0])
        star = by_agent.get("*")
        if google and google["allowed"] is False:
            blocked_for_google.append(url)
            findings.append(_finding("url_blocked", "block", url, google["rule"], f"{url} is blocked for Googlebot by '{google['rule']}'; it cannot be crawled or ranked."))
        elif star and star["allowed"] is False:
            findings.append(_finding("url_blocked_other_agents", "info", url, star["rule"], f"{url} is blocked for other crawlers by '{star['rule']}' but allowed for Googlebot."))
        else:
            findings.append(_finding("url_allowed", "info", url, google["rule"] if google else None, f"{url} is allowed for Googlebot."))

    sitemap_starts = list(dict.fromkeys(robots["sitemaps"] + [str(s) for s in _list_arg(args.get("sitemap_url"), "sitemap_url")]))
    sitemap_default_used = False
    if not sitemap_starts and fetch_html is not None:
        sitemap_starts = [base + "/sitemap.xml"]
        sitemap_default_used = True
    sitemap_urls, fetched = _collect_sitemap_urls(sitemap_starts, fetch_html, missing, warnings)
    reachable = [f for f in fetched if f["kind"] in ("urlset", "index", "text")]
    if fetch_html is not None and not reachable:
        findings.append(_finding("sitemap_missing", "warn", base, sitemap_starts, "No sitemap could be read" + (" (robots.txt names none and /sitemap.xml does not answer)" if sitemap_default_used else "") + "; search engines discover pages only by following links."))
    elif fetch_html is not None and not robots["sitemaps"] and reachable:
        findings.append(_finding("sitemap_not_in_robots", "info", robots_url, sitemap_starts, "robots.txt names no Sitemap line; the sitemap was found at the default address."))
    norm_sitemap = {_normalise_url(u) for u in sitemap_urls}
    not_in_sitemap = [u for u in important if reachable and _normalise_url(u) not in norm_sitemap]
    for u in not_in_sitemap:
        findings.append(_finding("not_in_sitemap", "warn", u, None, f"{u} is not in any sitemap read; check whether the whole section is missing and whether it gets search traffic."))
    google_group = _robots_group(robots, "Googlebot")
    sitemap_blocked = [u for u in sitemap_urls if robots_text and not _robots_verdict(google_group, u)[0]]
    if sitemap_blocked:
        findings.append(_finding("sitemap_urls_blocked", "warn", base, sitemap_blocked[:MAX_EXAMPLES], f"{len(sitemap_blocked)} sitemap urls are blocked by robots.txt for Googlebot: the sitemap invites crawlers to pages they may not open."))

    clauses = []
    if robots_text is not None:
        clauses.append(f"robots.txt {'read' if robots_status == 'ok' else robots_status} with {len(robots['groups'])} rule groups")
        if important:
            clauses.append(f"{len(blocked_for_google)} of {len(important)} important urls blocked for Googlebot")
    if reachable:
        clauses.append(f"{len(sitemap_urls)} urls in {len(reachable)} sitemap files")
        if important:
            clauses.append(f"{len(not_in_sitemap)} important urls not in a sitemap")
        if sitemap_blocked:
            clauses.append(f"{len(sitemap_blocked)} sitemap urls blocked")
    elif fetch_html is not None:
        clauses.append("no sitemap readable")
    if not clauses:
        clauses.append("nothing could be fetched; give the robots.txt text or open the site with the browser tools")
    body = {
        "site": base,
        "robots": {"url": robots_url, "status": robots_status, "groups": rules_out, "sitemaps": robots["sitemaps"], "text": (robots_text or "")[:4000] or None},
        "important_urls": checked,
        "sitemaps": {"files": fetched, "url_count": len(sitemap_urls), "not_in_sitemap": not_in_sitemap, "blocked_by_robots": sitemap_blocked[:MAX_EXAMPLES], "blocked_count": len(sitemap_blocked),
                     "sample": sitemap_urls[:MAX_EXAMPLES]},
    }
    return _finish("crawl_rules", body, findings, missing, warnings, "; ".join(clauses))


# --- snippets ---------------------------------------------------------------


def _page_record(label: str, html: str) -> dict[str, Any]:
    page = extract_page(html)
    scan = _scan(html)
    page["source"] = label
    page["scan"] = scan
    page["headings"] = scan.headings
    return page


def _words_on(words: list[str], text: str) -> tuple[list[str], list[str]]:
    page_words = set(_WORD.findall(str(text or "").casefold()))
    found = [w for w in words if any(same_stem(w, p) for p in page_words)]
    return found, [w for w in words if w not in found]


def _mode_snippet(tools, args, fetch_html, search) -> dict[str, Any]:
    missing: list[str] = []
    warnings: list[str] = []
    findings: list[dict] = []
    pages = _pages(tools, args, fetch_html, missing)
    brand = _clean(args.get("brand"))
    brand_words = content_words(brand) + [w for w in _WORD.findall(brand.casefold()) if len(w) >= 2]
    query = _clean(args.get("query"))

    competing: list[dict[str, Any]] = []
    if query:
        if search is None:
            missing.append(f"competing titles for '{query}': no search here")
        else:
            try:
                competing, reason = _parse_search(search(query))
            except Exception as exc:
                competing, reason = [], str(exc)
            if reason:
                warnings.append(f"search for '{query}' returned nothing usable: {reason}")
    competing_words = Counter(w for r in competing for w in set(content_words(r["title"])))

    rows: list[dict[str, Any]] = []
    for label, html in pages:
        page = _page_record(label, html)
        title = page["title"] or ""
        meta = page["meta_description"] or ""
        title_words = [w for w in content_words(title) if not any(same_stem(w, b) for b in brand_words)]
        meta_words = [w for w in content_words(meta) if not any(same_stem(w, b) for b in brand_words)]
        landing = " ".join(page["h1"]) + " " + page["first_screen_text"]
        t_found, t_missing = _words_on(title_words, landing)
        m_found, m_missing = _words_on(meta_words, landing)
        meta_prices = find_prices(meta)
        page_values = {p["value"] for p in page["prices"]}
        price_mismatch = [p for p in meta_prices if p["value"] not in page_values]
        row = {
            "source": label,
            "title": title or None,
            "title_chars": len(title),
            "title_over_60": len(title) > TITLE_MAX,
            "brand_in_title": bool(brand) and any(any(same_stem(w, b) for b in brand_words) for w in _WORD.findall(title.casefold())),
            "title_words": title_words,
            "meta_description": meta or None,
            "meta_chars": len(meta),
            "meta_missing": not meta,
            "meta_over_160": len(meta) > META_MAX,
            "h1": page["h1"],
            "snippet_vs_page": {
                "title_words_on_page": t_found, "title_words_missing": t_missing,
                "meta_words_on_page": m_found, "meta_words_missing": m_missing,
                "meta_prices": [p["text"] for p in meta_prices], "page_prices": [p["text"] for p in page["prices"][:6]],
            },
            "duplicate_title_of": [],
            "duplicate_meta_of": [],
            "competing_overlap": [w for w in title_words if w in competing_words],
        }
        if not title:
            findings.append(_finding("title_missing", "block", label, None, f"{label}: no <title>; the result shows the address or a guess."))
        else:
            if len(title) > TITLE_MAX:
                findings.append(_finding("title_over_60", "warn", label, title, f"{label}: title has {len(title)} characters; Google cuts it after about {TITLE_MAX}."))
            if len(title_words) < 2:
                findings.append(_finding("title_not_descriptive", "warn", label, title, f"{label}: title '{title}' names the subject in {len(title_words)} word(s) beyond the brand; a stranger cannot tell what the page is."))
            if brand and not row["brand_in_title"]:
                findings.append(_finding("brand_not_in_title", "info", label, title, f"{label}: the brand '{brand}' is not in the title."))
            if competing and title_words and all(w in competing_words for w in title_words):
                findings.append(_finding("title_undifferentiated", "info", label, title, f"{label}: every title word also appears in competing titles for '{query}'; nothing says why to open this one."))
        if not meta:
            findings.append(_finding("meta_missing", "warn", label, None, f"{label}: no meta description; Google writes its own snippet from the page."))
        elif len(meta) > META_MAX:
            findings.append(_finding("meta_over_160", "info", label, meta, f"{label}: description has {len(meta)} characters; the snippet cuts after about {META_MAX}."))
        promise_words = title_words + [w for w in meta_words if w not in title_words]
        promise_missing = [w for w in t_missing + m_missing if w in promise_words]
        promise_missing = list(dict.fromkeys(promise_missing))
        if promise_words and len(promise_missing) > len(promise_words) / 2:
            findings.append(_finding("snippet_promise_missing", "warn", label, promise_missing, f"{label}: the snippet promises {', '.join(promise_missing)} but the H1 and first screen do not say it; the visitor lands and looks for it."))
        elif promise_words:
            findings.append(_finding("snippet_promise_kept", "info", label, t_found + m_found, f"{label}: the snippet's words appear in the H1 or first screen."))
        if price_mismatch:
            findings.append(_finding("snippet_price_mismatch", "block", label, [p["text"] for p in price_mismatch], f"{label}: the description names {', '.join(p['text'] for p in price_mismatch)} but the page shows {', '.join(p['text'] for p in page['prices'][:4]) or 'no price'}."))
        rows.append(row)

    for field, code, what in (("title", "duplicate_title", "title"), ("meta_description", "duplicate_meta", "description")):
        groups: dict[str, list[str]] = {}
        for row in rows:
            if row[field]:
                groups.setdefault(_clean(row[field]).casefold(), []).append(row["source"])
        for text, sources in groups.items():
            if len(sources) > 1:
                for row in rows:
                    if row["source"] in sources:
                        row[f"{'duplicate_title' if field == 'title' else 'duplicate_meta'}_of"] = [s for s in sources if s != row["source"]]
                findings.append(_finding(code, "warn", ", ".join(sources), text, f"{len(sources)} pages share one {what} ('{text[:80]}'): {', '.join(sources)}. Search shows them as copies."))

    over = sum(1 for r in rows if r["title_over_60"])
    dup_t = sum(1 for r in rows if r["duplicate_title_of"])
    no_meta = sum(1 for r in rows if r["meta_missing"])
    promise = sum(1 for f in findings if f["code"] == "snippet_promise_missing")
    clauses = [f"{len(rows)} pages: {over} titles over {TITLE_MAX} characters", f"{dup_t} with a duplicate title", f"{no_meta} without a description", f"{promise} snippets promising what the page does not show"]
    if query and competing:
        clauses.append(f"{len(competing)} competing results read for '{query}'")
    return _finish("snippet", {"pages": rows, "competing": competing, "brand": brand or None, "query": query or None}, findings, missing, warnings, "; ".join(clauses))


# --- structured data --------------------------------------------------------


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def _type_name(node: dict) -> str:
    t = node.get("@type")
    if isinstance(t, list):
        t = t[0] if t else None
    return str(t) if t else "(untyped)"


def _to_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        prices = find_prices(value)
        if prices:
            return prices[0]["value"]
        m = re.search(r"\d+(?:[.,]\d+)?", value.replace(" ", ""))
        return float(m.group(0).replace(",", ".")) if m else None
    return None


def _blocks_from(data: Any) -> list[dict[str, Any]]:
    """Flatten JSON-LD (including @graph and nested offers) into blocks with the fields a shopper sees."""
    blocks: list[dict[str, Any]] = []

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for n in node:
                visit(n)
            return
        if not isinstance(node, dict):
            return
        if "@graph" in node:
            visit(node["@graph"])
        if "@type" not in node:
            for key, v in node.items():
                if key != "@graph" and isinstance(v, (dict, list)):
                    visit(v)
            return
        fields: dict[str, Any] = {}
        name = node.get("name") or node.get("headline")
        if name:
            fields["name"] = _clean(name)
        offers = _as_list(node.get("offers"))
        offer_prices = []
        for offer in offers:
            if isinstance(offer, dict):
                inner = _as_list(offer.get("offers")) if offer.get("offers") else [offer]
                for o in inner:
                    if not isinstance(o, dict):
                        continue
                    price = o.get("price", o.get("lowPrice"))
                    if price is not None:
                        offer_prices.append(_to_number(price))
                    if o.get("priceCurrency") and "currency" not in fields:
                        fields["currency"] = o["priceCurrency"]
                    if o.get("availability") and "availability" not in fields:
                        fields["availability"] = str(o["availability"]).split("/")[-1]
        if "price" in node and "offers" not in node:
            offer_prices.append(_to_number(node["price"]))
            if node.get("priceCurrency"):
                fields["currency"] = node["priceCurrency"]
        offer_prices = [p for p in offer_prices if p is not None]
        if offer_prices:
            fields["price"] = offer_prices[0]
            if len(offer_prices) > 1:
                fields["prices"] = offer_prices
        rating = node.get("aggregateRating") or node.get("reviewRating")
        if isinstance(rating, dict):
            if rating.get("ratingValue") is not None:
                fields["ratingValue"] = _to_number(rating["ratingValue"])
            count = rating.get("reviewCount", rating.get("ratingCount"))
            if count is not None:
                fields["reviewCount"] = int(_to_number(count) or 0)
        blocks.append({"type": _type_name(node), "fields": fields})
        for key in ("mainEntity", "itemListElement", "review", "hasPart"):
            if key in node:
                visit(node[key])

    visit(data)
    return blocks


def _mode_structured_data(tools, args, fetch_html) -> dict[str, Any]:
    missing: list[str] = []
    warnings: list[str] = []
    findings: list[dict] = []
    schema = args.get("schema")
    html, label = _single_page(tools, args, fetch_html, missing, required=schema in (None, ""))
    raw_blocks: list[str] = []
    page = None
    if html is not None:
        page = _page_record(label or "html", html)
        raw_blocks = list(page["scan"].jsonld)
    if schema not in (None, ""):
        raw_blocks.append(schema if isinstance(schema, str) else json.dumps(schema))
    blocks: list[dict[str, Any]] = []
    for idx, raw in enumerate(raw_blocks, 1):
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError as exc:
            findings.append(_finding("schema_invalid_json", "block", label, raw[:200], f"JSON-LD block {idx} is not valid JSON ({exc.msg} at char {exc.pos}); Google ignores it whole."))
            continue
        blocks.extend(_blocks_from(data))
    if not raw_blocks:
        if html is not None:
            findings.append(_finding("no_structured_data", "warn", label, None, f"{label}: no JSON-LD on the page; no rich result (price, stock, stars) can show."))
    body_text = page["body_text"] if page else ""
    folded = body_text.casefold()
    page_values = {p["value"] for p in page["prices"]} if page else set()
    for block in blocks:
        f = block["fields"]
        t = block["type"]
        where = f"{t} '{f.get('name', '')}'".strip()
        if t.lower() in ("product", "offer", "aggregateoffer"):
            if "price" not in f:
                findings.append(_finding("schema_field_missing", "warn", where, "price", f"{where}: no price in offers; the result shows no price."))
            if "availability" not in f:
                findings.append(_finding("schema_field_missing", "warn", where, "availability", f"{where}: no availability in offers."))
        if page is not None:
            for price in f.get("prices", [f["price"]] if "price" in f else []):
                if any(abs(price - v) < 0.005 for v in page_values):
                    findings.append(_finding("schema_price_match", "info", where, price, f"{where}: schema price {price:g} is visible on the page."))
                elif not page_values:
                    findings.append(_finding("schema_price_not_on_page", "warn", where, price, f"{where}: schema says {price:g} but the page shows no price at all."))
                else:
                    findings.append(_finding("schema_price_mismatch", "block", where, price, f"{where}: schema says {price:g}, the page shows {', '.join(p['text'] for p in page['prices'][:4])}. Google penalises markup that disagrees with the visible page."))
            if "ratingValue" in f and f["ratingValue"] is not None:
                r = f["ratingValue"]
                forms = {f"{r:g}", f"{r:.1f}", f"{r:.1f}".replace(".", ",")}
                if not any(re.search(rf"(?<![\d.,]){re.escape(x)}(?![\d])", body_text) for x in forms):
                    findings.append(_finding("schema_rating_not_visible", "warn", where, r, f"{where}: schema rating {r:g} is not shown anywhere on the page."))
                else:
                    findings.append(_finding("schema_rating_visible", "info", where, r, f"{where}: rating {r:g} is visible on the page."))
            if "reviewCount" in f and not re.search(rf"\b{f['reviewCount']}\b", body_text):
                findings.append(_finding("schema_review_count_not_visible", "warn", where, f["reviewCount"], f"{where}: schema counts {f['reviewCount']} reviews but the page shows no such number."))
            avail = (f.get("availability") or "").lower()
            if avail:
                if "outofstock" in avail and _IN_STOCK.search(folded) and not _OUT_OF_STOCK.search(folded):
                    findings.append(_finding("schema_availability_mismatch", "warn", where, f["availability"], f"{where}: schema says OutOfStock, the page reads as in stock."))
                elif "instock" in avail and _OUT_OF_STOCK.search(folded):
                    findings.append(_finding("schema_availability_mismatch", "warn", where, f["availability"], f"{where}: schema says InStock, the page says sold out."))
    types = Counter(b["type"] for b in blocks)
    mismatches = sum(1 for x in findings if x["code"] in ("schema_price_mismatch", "schema_availability_mismatch", "schema_rating_not_visible", "schema_review_count_not_visible"))
    if blocks:
        summary = f"{len(blocks)} structured-data block(s) ({', '.join(f'{k} ×{v}' if v > 1 else k for k, v in types.items())}); {mismatches} field(s) disagree with what the visitor sees"
    elif raw_blocks:
        summary = "the JSON-LD could not be read"
    elif html is None:
        summary = "no page and no schema could be read"
    else:
        summary = "no structured data on the page"
    body = {"source": label, "blocks": blocks, "page_prices": [p["text"] for p in page["prices"][:8]] if page else []}
    return _finish("structured_data", body, findings, missing, warnings, summary)


# --- crawler-visible content ---------------------------------------------


def _fact_present(fact: str, text: str) -> bool:
    words = content_words(fact)
    numbers = re.findall(r"\d+(?:[.,]\d+)?", fact)
    page_words = set(_WORD.findall(text.casefold()))
    page_numbers = set(re.findall(r"\d+(?:[.,]\d+)?", text))
    if not words and not numbers:
        return fact.casefold() in text.casefold()
    words_ok = all(any(same_stem(w, p) for p in page_words) for w in words)
    numbers_ok = all(n in page_numbers or n.replace(",", ".") in {x.replace(",", ".") for x in page_numbers} for n in numbers)
    return words_ok and numbers_ok


def _mode_crawler_content(tools, args, fetch_html) -> dict[str, Any]:
    missing: list[str] = []
    warnings: list[str] = []
    findings: list[dict] = []
    html, label = _single_page(tools, args, fetch_html, missing)
    rendered = _clean(args.get("rendered_text")) or None
    facts = [str(f).strip() for f in _list_arg(args.get("required_facts"), "required_facts")]
    if html is None:
        return _finish("crawler_content", {"source": label, "html_text_chars": None, "rendered_text_chars": len(rendered) if rendered else None, "facts": []}, findings, missing, warnings,
                       "The page could not be read; nothing to compare")
    page = _page_record(label or "html", html)
    text = page["body_text"]
    html_chars = len(text)
    rendered_chars = len(rendered) if rendered else None
    fact_rows = []
    for fact in facts:
        in_html = _fact_present(fact, text)
        in_rendered = _fact_present(fact, rendered) if rendered else None
        fact_rows.append({"fact": fact, "in_html": in_html, "in_rendered": in_rendered})
        if in_html:
            findings.append(_finding("fact_in_html", "info", label, fact, f"'{fact}' is in the HTML a crawler reads."))
        elif in_rendered:
            findings.append(_finding("fact_only_rendered", "block", label, fact, f"'{fact}' appears only after JavaScript renders; the crawler's first pass does not see it. Put it in the HTML as text."))
        else:
            findings.append(_finding("fact_missing", "warn", label, fact, f"'{fact}' is neither in the HTML nor in the rendered text" + ("" if rendered else " (no rendered text given)") + "."))
    if rendered_chars is not None and rendered_chars > 2 * html_chars + 500:
        findings.append(_finding("rendered_much_larger", "warn", label, {"html": html_chars, "rendered": rendered_chars}, f"The rendered page has {rendered_chars} characters of text, the HTML only {html_chars}: most content depends on JavaScript."))
    if rendered is None:
        missing.append("rendered_text: not given; take it from browser_snapshot to see what JavaScript adds")
    no_alt = [img for img in page["scan"].images if not img["alt"]]
    if no_alt:
        findings.append(_finding("images_without_alt", "warn", label, [i["src"] for i in no_alt[:MAX_EXAMPLES]], f"{len(no_alt)} of {len(page['scan'].images)} images have no alt text; whatever they show (specs, prices) is invisible to the crawler."))
    if not page["h1"]:
        findings.append(_finding("no_h1", "warn", label, None, "The page has no H1."))
    if page["word_count"] < THIN_WORDS:
        findings.append(_finding("thin_html_text", "warn", label, page["word_count"], f"Only {page['word_count']} words of text in the HTML (under {THIN_WORDS})."))
    only_rendered = sum(1 for r in fact_rows if r["in_rendered"] and not r["in_html"])
    clauses = [f"{html_chars} characters of crawler-visible text" + (f" vs {rendered_chars} rendered" if rendered_chars is not None else "")]
    if facts:
        clauses.append(f"{sum(1 for r in fact_rows if r['in_html'])} of {len(facts)} required facts in the HTML, {only_rendered} only after rendering")
    if no_alt:
        clauses.append(f"{len(no_alt)} images without alt")
    body = {
        "source": label, "html_text_chars": html_chars, "rendered_text_chars": rendered_chars,
        "word_count": page["word_count"], "headings": page["scan"].headings[:40],
        "images": page["scan"].images[:50], "images_without_alt": len(no_alt), "facts": fact_rows,
        "language": page["lang"] or detect_language(text),
    }
    return _finish("crawler_content", body, findings, missing, warnings, "; ".join(clauses))


# --- templates --------------------------------------------------------------


def _shingles(text: str) -> set[tuple[str, ...]]:
    tokens = _TOKEN.findall(text.casefold())
    return {tuple(tokens[i:i + SHINGLE]) for i in range(max(0, len(tokens) - SHINGLE + 1))}


def _decision_fields(page: dict[str, Any], fields: tuple[str, ...]) -> dict[str, bool]:
    folded = page["body_text"].casefold()
    out: dict[str, bool] = {}
    for field in fields:
        if field == "price":
            out[field] = bool(page["prices"]) or bool(_FIELD_PATTERNS["price"].search(folded))
        elif field == "images":
            out[field] = page["images_total"] > 0
        elif field in _FIELD_PATTERNS:
            out[field] = bool(_FIELD_PATTERNS[field].search(folded))
        else:
            out[field] = field.casefold() in folded
    return out


def _mode_templates(tools, args, fetch_html) -> dict[str, Any]:
    missing: list[str] = []
    warnings: list[str] = []
    findings: list[dict] = []
    pages = _pages(tools, args, fetch_html, missing)
    if len(pages) < TEMPLATE_MIN_PAGES or len(pages) > TEMPLATE_MAX_PAGES:
        raise ToolError(f"templates needs {TEMPLATE_MIN_PAGES}–{TEMPLATE_MAX_PAGES} readable pages from one template, got {len(pages)}")
    fields = tuple(str(f) for f in _list_arg(args.get("decision_fields"), "decision_fields")) or DECISION_FIELDS
    records = [(label, _page_record(label, html)) for label, html in pages]
    shingles = [_shingles(p["body_text"]) for _, p in records]
    rows: list[dict[str, Any]] = []
    for i, (label, page) in enumerate(records):
        others: set = set()
        for j, s in enumerate(shingles):
            if j != i:
                others |= s
        own = shingles[i]
        unique = len(own - others) / len(own) if own else 0.0
        present = _decision_fields(page, fields)
        absent = [f for f, ok in present.items() if not ok]
        row = {"source": label, "title": page["title"], "word_count": page["word_count"], "unique_ratio": round(unique, 2),
               "decision_fields": present, "decision_fields_missing": absent}
        rows.append(row)
        if unique < DUPLICATE_RATIO:
            findings.append(_finding("duplicate_template", "block", label, row["unique_ratio"], f"{label}: only {unique:.0%} of its text is its own; the rest repeats the other pages. Search treats it as a copy."))
        elif unique < LOW_UNIQUE_RATIO:
            findings.append(_finding("low_unique_text", "warn", label, row["unique_ratio"], f"{label}: {unique:.0%} unique text; most of the page is template."))
        if page["word_count"] < THIN_WORDS:
            findings.append(_finding("thin_template", "warn", label, page["word_count"], f"{label}: {page['word_count']} words of visible text (under {THIN_WORDS})."))
        if len(absent) >= 3:
            findings.append(_finding("decision_fields_missing", "warn", label, absent, f"{label} lacks {', '.join(absent)}: a buyer cannot decide here and leaves to a page that has them."))
    low = sum(1 for r in rows if r["unique_ratio"] < LOW_UNIQUE_RATIO)
    thin = sum(1 for r in rows if r["word_count"] < THIN_WORDS)
    med = median(r["unique_ratio"] for r in rows)
    lacking = sum(1 for r in rows if len(r["decision_fields_missing"]) >= 3)
    summary = f"{len(rows)} template pages: median {med:.0%} unique text, {low} under {LOW_UNIQUE_RATIO:.0%}, {thin} thin, {lacking} missing three or more decision fields"
    return _finish("templates", {"pages": rows, "decision_fields": list(fields)}, findings, missing, warnings, summary)


# --- serp -------------------------------------------------------------------


def _intent(text: str) -> str:
    folded = f" {_clean(text).casefold()} "
    for code, rx in _INTENT_RULES:
        if rx.search(folded):
            return code
    return "info"


def _modifier(query: str) -> str | None:
    folded = query.casefold()
    for m in _MODIFIERS:
        if re.search(rf"(^|\s){re.escape(m.strip())}", folded):
            return m.strip()
    return None


def _generate_queries(args: dict) -> list[dict[str, str]]:
    product = _clean(args.get("product"))
    category = _clean(args.get("category"))
    lang = (str(args.get("lang") or args.get("language") or "").strip().lower()[:2]) or (detect_language(f"{product} {category}") or "en")
    templates = _STAGE_QUERIES.get(lang, _STAGE_QUERIES["en"])
    stages = [str(s) for s in _list_arg(args.get("stages"), "stages")] or list(DEFAULT_STAGES)
    out: list[dict[str, str]] = []
    for stage in stages:
        if stage not in templates:
            raise ToolError(f"unknown stage {stage!r}; stages: {', '.join(DEFAULT_STAGES)}")
        for tpl in templates[stage]:
            needs = "{category}" in tpl and not category or "{product}" in tpl and not product
            if needs:
                continue
            out.append({"query": tpl.format(category=category, product=product).strip(), "stage": stage})
    if not out:
        raise ToolError("serp needs queries, or product/category to generate the ladder from")
    return out


def _mode_serp(args, search) -> dict[str, Any]:
    missing: list[str] = []
    warnings: list[str] = []
    findings: list[dict] = []
    queries = [{"query": str(q), "stage": None} for q in _list_arg(args.get("queries"), "queries")]
    if not queries:
        queries = _generate_queries(args)
    top_n = int(_number_arg(args, "top_n", DEFAULT_TOP_N))
    owned = [_host(d if d.startswith("http") else "https://" + d) for d in _list_arg(args.get("owned_domains"), "owned_domains")]
    if args.get("brand_domain"):
        bd = _host(str(args["brand_domain"]) if str(args["brand_domain"]).startswith("http") else "https://" + str(args["brand_domain"]))
        if bd not in owned:
            owned.insert(0, bd)
    competitors = [_host(d if d.startswith("http") else "https://" + d) for d in _list_arg(args.get("competitors"), "competitors")]
    if search is None:
        for q in queries:
            missing.append(f"query: '{q['query']}' not searched (no search here)")
    rows: list[dict[str, Any]] = []
    for q in queries:
        query = q["query"]
        row: dict[str, Any] = {"query": query, "stage": q["stage"], "results": [], "owned_rank": None, "competitor_ranks": {c: None for c in competitors},
                               "intent_split": {}, "dominant_intent": None, "dominant_share": None, "ambiguous_modifier": False, "modifier": _modifier(query)}
        if search is not None:
            try:
                results, reason = _parse_search(search(query))
            except Exception as exc:
                results, reason = [], str(exc)
            if reason:
                warnings.append(f"'{query}': {reason}")
                findings.append(_finding("no_results", "warn", query, reason, f"No results came back for '{query}' ({reason})."))
            results = results[:top_n]
            for r in results:
                r["intent"] = _intent(f"{r['title']} {r['url']}")
                r["owned"] = any(r["domain"] == d or r["domain"].endswith("." + d) for d in owned)
            row["results"] = results
            owned_ranks = [r["rank"] for r in results if r["owned"]]
            row["owned_rank"] = owned_ranks[0] if owned_ranks else None
            for c in competitors:
                ranks = [r["rank"] for r in results if r["domain"] == c or r["domain"].endswith("." + c)]
                row["competitor_ranks"][c] = ranks[0] if ranks else None
            if results:
                split = Counter(r["intent"] for r in results)
                row["intent_split"] = {k: round(v / len(results), 2) for k, v in split.most_common()}
                code, n = split.most_common(1)[0]
                row["dominant_intent"] = code
                row["dominant_share"] = round(n / len(results), 2)
                row["ambiguous_modifier"] = row["dominant_share"] < AMBIGUOUS_BELOW
                if row["ambiguous_modifier"]:
                    findings.append(_finding("ambiguous_modifier", "warn", query, row["intent_split"], f"'{query}' brings mixed results ({', '.join(f'{k} {v:.0%}' for k, v in row['intent_split'].items())}); " + (f"the word '{row['modifier']}' " if row["modifier"] else "the query ") + "means different things here, so one page cannot serve it."))
                if owned:
                    if row["owned_rank"] is None:
                        findings.append(_finding("owned_absent", "warn", query, owned, f"'{query}': none of {', '.join(owned)} in the top {len(results)}."))
                    else:
                        findings.append(_finding("owned_rank", "info", query, row["owned_rank"], f"'{query}': owned domain at position {row['owned_rank']}."))
                ahead = [c for c, r in row["competitor_ranks"].items() if r is not None and (row["owned_rank"] is None or r < row["owned_rank"])]
                if ahead:
                    findings.append(_finding("competitor_ahead", "info", query, {c: row["competitor_ranks"][c] for c in ahead}, f"'{query}': {', '.join(ahead)} rank ahead."))
        rows.append(row)
    if search is not None:
        missing.append("published dates are not in the search results; freshness and trend are not measured")
    searched = [r for r in rows if r["results"]]
    ranked = [r for r in searched if r["owned_rank"] is not None]
    clauses = [f"{len(rows)} queries" + (f" across {len({r['stage'] for r in rows if r['stage']})} stages" if any(r["stage"] for r in rows) else "")]
    if searched:
        if owned:
            clauses.append(f"owned domain in the top {top_n} for {len(ranked)} of {len(searched)} searched")
            if ranked:
                clauses.append(f"best position {min(r['owned_rank'] for r in ranked)}")
        amb = sum(1 for r in searched if r["ambiguous_modifier"])
        clauses.append(f"{amb} queries with mixed intent")
    else:
        clauses.append("nothing searched")
    body = {"queries": rows, "owned_domains": owned, "competitors": competitors, "top_n": top_n}
    return _finish("serp", body, findings, missing, warnings, "; ".join(clauses))


# --- keywords ---------------------------------------------------------------


def _detect_column(header: list[str], given: Any, candidates: tuple[str, ...], what: str, *, required: bool) -> str | None:
    from rocky.funnel import _column

    if given not in (None, ""):
        return _column(given, header, what)
    folded = {h.strip().lower(): h for h in header}
    for cand in candidates:
        if cand in folded:
            return folded[cand]
    for cand in candidates:
        for low, h in folded.items():
            if low.startswith(cand) or (len(cand) > 4 and cand in low):
                return h
    if required:
        raise ToolError(f"{what} column not given and not recognised; columns found: {', '.join(header) or '(none)'}")
    return None


def _cell_number(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"[^\d,.\-]", "", str(value).replace(" ", "").replace(" ", ""))
    if not text:
        return 0.0
    if "," in text and "." in text:
        dec = max(text.rfind(","), text.rfind("."))
        text = text[:dec].replace(",", "").replace(".", "") + "." + text[dec + 1:]
    elif "," in text:
        parts = text.split(",")
        text = "".join(parts) if len(parts) > 2 or len(parts[-1]) == 3 else parts[0] + "." + parts[1]
    try:
        return float(text)
    except ValueError:
        return 0.0


def _stems(term: str) -> list[str]:
    return list(dict.fromkeys(w[:STEM] for w in content_words(term)))


def _cluster_terms(terms: list[str]) -> list[list[str]]:
    """Terms sharing at least two five-letter stems (or the only stem of a one-word term) join one cluster."""
    stems = {t: _stems(t) for t in terms}
    parent = {t: t for t in terms}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_stem: dict[str, list[str]] = {}
    for t, ss in stems.items():
        for s in ss:
            by_stem.setdefault(s, []).append(t)
    for s, members in by_stem.items():
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                shared = len(set(stems[a]) & set(stems[b]))
                need = min(2, len(stems[a]), len(stems[b]))
                if shared >= max(1, need):
                    parent[find(a)] = find(b)
    groups: dict[str, list[str]] = {}
    for t in terms:
        groups.setdefault(find(t), []).append(t)
    return list(groups.values())


def _cluster_label(terms: list[str]) -> str:
    counts = Counter(w for t in terms for w in content_words(t))
    if not counts:
        return terms[0]
    top = [w for w, _ in counts.most_common(2)]
    return " ".join(top)


def _mode_keywords(tools: WorkspaceTools | None, args: dict) -> dict[str, Any]:
    from rocky.funnel import _load_rows

    if tools is None:
        raise ToolError("keywords needs a workspace to read the export from")
    path = args.get("path")
    if not path:
        raise ToolError("path is required: the search-terms export (CSV or XLSX)")
    header, rows = _load_rows(tools, str(path), args.get("sheet"))
    missing: list[str] = []
    warnings: list[str] = []
    findings: list[dict] = []
    term_col = _detect_column(header, args.get("term_column"), _TERM_HEADERS, "term_column", required=True)
    clicks_col = _detect_column(header, args.get("clicks_column"), _CLICK_HEADERS, "clicks_column", required=False)
    cost_col = _detect_column(header, args.get("cost_column"), _COST_HEADERS, "cost_column", required=False)
    conv_col = _detect_column(header, args.get("conversions_column"), _CONV_HEADERS, "conversions_column", required=False)
    value_col = _detect_column(header, args.get("value_column"), _VALUE_HEADERS, "value_column", required=False)
    if value_col == cost_col or value_col == conv_col:
        value_col = None
    group_col = _detect_column(header, args.get("group_column"), _GROUP_HEADERS, "group_column", required=False)
    for name, col in (("clicks", clicks_col), ("cost", cost_col), ("conversions", conv_col)):
        if col is None:
            missing.append(f"{name} column: not given and not recognised in the header; {name} figures are 0")
    min_cost = _number_arg(args, "min_cost", DEFAULT_MIN_COST)
    max_conv = _number_arg(args, "max_conversions", DEFAULT_MAX_CONVERSIONS)
    min_share = _number_arg(args, "min_share", DEFAULT_MIN_SHARE)
    if min_share > 1:
        min_share /= 100
    protect = [str(p).casefold() for p in _list_arg(args.get("protect_terms"), "protect_terms")]
    brand_terms = [str(b).casefold() for b in _list_arg(args.get("brand_terms"), "brand_terms")]

    agg: dict[str, dict[str, Any]] = {}
    skipped = 0
    for row in rows:
        term = _clean(row.get(term_col)).casefold()
        if not term or term.startswith("total"):
            skipped += 1
            continue
        rec = agg.setdefault(term, {"term": term, "clicks": 0.0, "cost": 0.0, "conversions": 0.0, "value": 0.0, "groups": set(), "rows": 0})
        rec["clicks"] += _cell_number(row.get(clicks_col)) if clicks_col else 0.0
        rec["cost"] += _cell_number(row.get(cost_col)) if cost_col else 0.0
        rec["conversions"] += _cell_number(row.get(conv_col)) if conv_col else 0.0
        rec["value"] += _cell_number(row.get(value_col)) if value_col else 0.0
        if group_col:
            rec["groups"].add(_clean(row.get(group_col)) or "(blank)")
        rec["rows"] += 1
    if not agg:
        raise ToolError(f"no search terms in column {term_col!r}")
    terms = list(agg)

    def touches(term: str, words: list[str]) -> bool:
        tw = _WORD.findall(term)
        return any(w in term or any(same_stem(w, x) for x in tw) for w in words)

    clusters_out: list[dict[str, Any]] = []
    cluster_of: dict[str, str] = {}
    for members in _cluster_terms(terms):
        label = _cluster_label(members)
        for m in members:
            cluster_of[m] = label
        recs = [agg[m] for m in members]
        cost = sum(r["cost"] for r in recs)
        conv = sum(r["conversions"] for r in recs)
        clusters_out.append({
            "label": label, "terms": sorted(members, key=lambda t: -agg[t]["cost"]), "term_count": len(members),
            "clicks": round(sum(r["clicks"] for r in recs), 2), "cost": round(cost, 2), "conversions": round(conv, 2),
            "value": round(sum(r["value"] for r in recs), 2), "cpa": round(cost / conv, 2) if conv else None,
            "brand": any(touches(m, brand_terms) for m in members) if brand_terms else False,
            "groups": sorted({g for r in recs for g in r["groups"]}),
        })
    clusters_out.sort(key=lambda c: -c["cost"])

    groups_out: list[dict[str, Any]] = []
    split_candidates: list[dict[str, Any]] = []
    if group_col:
        by_group: dict[str, dict[str, float]] = {}
        for term, rec in agg.items():
            for g in rec["groups"]:
                share = 1 / len(rec["groups"])
                by_group.setdefault(g, {}).setdefault(cluster_of[term], 0.0)
                by_group[g][cluster_of[term]] += rec["cost"] * share
        for g, cl in by_group.items():
            total = sum(cl.values())
            parts = sorted(({"cluster": k, "cost": round(v, 2), "share": round(v / total, 2) if total else 0.0} for k, v in cl.items()), key=lambda p: -p["cost"])
            major = [p for p in parts if p["share"] >= min_share]
            minor = [p for p in parts if 0 < p["share"] < min_share]
            groups_out.append({"name": g, "cost": round(total, 2), "clusters": parts, "mixed": len(major) >= 2})
            if len(parts) >= 2 and total > 0:
                if len(major) >= 2:
                    findings.append(_finding("group_mixed", "warn", g, [p["cluster"] for p in major], f"Ad group '{g}' spends on {len(major)} different needs ({', '.join(f'{p['cluster']} {p['share']:.0%}' for p in major)}); one ad cannot answer them all."))
                for p in minor:
                    split_candidates.append({"group": g, "cluster": p["cluster"], "cost": p["cost"], "share": p["share"]})
                    findings.append(_finding("split_candidate", "info", g, p["cluster"], f"In '{g}' the cluster '{p['cluster']}' holds {p['share']:.0%} of spend (under {min_share:.0%}): move it to its own group with its own ad, or exclude it."))

    negatives: list[dict[str, Any]] = []
    for term, rec in agg.items():
        if cost_col is None or rec["cost"] < min_cost or rec["conversions"] > max_conv:
            continue
        if protect and touches(term, protect):
            continue
        if brand_terms and touches(term, brand_terms):
            continue
        cluster = next(c for c in clusters_out if c["label"] == cluster_of[term])
        siblings = [t for t in cluster["terms"] if t != term]
        converting = [t for t in siblings if agg[t]["conversions"] > max_conv]
        if converting or not siblings:
            match, phrase, would_block = "exact", term, []
        else:
            shared = [w for w, _ in Counter(w for t in cluster["terms"] for w in content_words(t)).most_common() if all(any(same_stem(w, x) for x in content_words(t)) for t in cluster["terms"])]
            phrase = " ".join(shared[:2]) if shared else term
            would_block = [t for t in agg if phrase and all(any(same_stem(w, x) for x in content_words(t)) for w in phrase.split()) and agg[t]["conversions"] > max_conv]
            match = "phrase" if phrase != term and not would_block else "exact"
            if match == "exact":
                phrase = term
        negatives.append({"term": term, "clicks": round(rec["clicks"], 2), "cost": round(rec["cost"], 2), "conversions": round(rec["conversions"], 2),
                          "match": match, "negative": phrase, "cluster": cluster_of[term], "saving": round(rec["cost"], 2),
                          "would_block": would_block[:MAX_EXAMPLES], "groups": sorted(rec["groups"])})
    negatives.sort(key=lambda n: -n["cost"])
    saving = round(sum(n["saving"] for n in negatives), 2)
    total_cost = round(sum(r["cost"] for r in agg.values()), 2)
    if negatives:
        findings.append(_finding("negatives_found", "warn", str(path), [n["term"] for n in negatives[:MAX_EXAMPLES]], f"{len(negatives)} terms cost {saving:.2f} with at most {max_conv:g} conversions each ({saving / total_cost:.0%} of spend); exclude them and keep the protected ones."))
    elif cost_col is not None:
        findings.append(_finding("no_negatives", "info", str(path), None, f"No term with cost ≥ {min_cost:g} and ≤ {max_conv:g} conversions outside the protected list."))
    if skipped:
        warnings.append(f"{skipped} rows without a search term (totals or blanks) skipped")
    clauses = [f"{len(agg)} terms in {len(clusters_out)} clusters"]
    if group_col:
        clauses.append(f"{sum(1 for g in groups_out if g['mixed'])} of {len(groups_out)} ad groups mix needs, {len(split_candidates)} split candidates")
    if cost_col is not None:
        clauses.append(f"{len(negatives)} negative candidates would save {saving:.2f} of {total_cost:.2f} spent")
    body = {
        "columns": {"term": term_col, "clicks": clicks_col, "cost": cost_col, "conversions": conv_col, "value": value_col, "group": group_col},
        "term_count": len(agg), "total_cost": total_cost, "clusters": clusters_out, "groups": groups_out, "split_candidates": split_candidates,
        "negatives": negatives, "estimated_saving": saving, "thresholds": {"min_cost": min_cost, "max_conversions": max_conv, "min_share": min_share},
    }
    return _finish("keywords", body, findings, missing, warnings, "; ".join(clauses))


# --- entry points -----------------------------------------------------------


def seo_inspect(
    tools: WorkspaceTools | None,
    args: dict,
    fetch_html: Callable[[str], str] | None = None,
    search: Callable[[str], str] | None = None,
    fetch_meta: Callable[[str], dict] | None = None,
) -> dict[str, Any]:
    """One search-channel check per ``mode``; see the module docstring for the list.

    Every result carries ``findings`` ordered block → warn → info, ``counts``,
    ``missing`` (what could not be fetched or was not given), ``warnings`` and a
    ``summary`` sentence a marketer can quote.
    """
    args = args or {}
    mode = _clean(args.get("mode")).lower()
    if not mode:
        raise ToolError(f"mode is required; one of {', '.join(MODES)}")
    if mode not in MODES:
        raise ToolError(f"unknown mode {mode!r}; one of {', '.join(MODES)}")
    if mode == "snippet":
        return _mode_snippet(tools, args, fetch_html, search)
    if mode == "urls":
        return _mode_urls(tools, args, fetch_html, fetch_meta)
    if mode == "crawl_rules":
        return _mode_crawl_rules(tools, args, fetch_html)
    if mode == "structured_data":
        return _mode_structured_data(tools, args, fetch_html)
    if mode == "crawler_content":
        return _mode_crawler_content(tools, args, fetch_html)
    if mode == "templates":
        return _mode_templates(tools, args, fetch_html)
    if mode == "serp":
        return _mode_serp(args, search)
    return _mode_keywords(tools, args)


def seo_inspect_tool(
    tools: WorkspaceTools | None,
    args: dict,
    fetch_html: Callable[[str], str] | None = None,
    search: Callable[[str], str] | None = None,
    fetch_meta: Callable[[str], dict] | None = None,
) -> str:
    """Backs the worker-facing ``seo_inspect`` tool: JSON the worker can quote from."""
    return json.dumps(seo_inspect(tools, args or {}, fetch_html, search, fetch_meta), ensure_ascii=False, indent=1, default=str)
