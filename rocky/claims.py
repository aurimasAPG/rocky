"""Match every measurable claim in a draft to the source passage that supports it.

``claim_check`` is the general form of the ad board's rule that every number in
ad copy needs a fact with a source. It reads a draft, pulls out the sentences
that state a number, a date, a ranking or a credential, and looks for each one
in the supplied sources (workspace files, ``runs/notes/facts.md`` by default,
and URLs when the engine passes a ``fetch`` callable). Each claim comes back
with the best passage, where it sits (file line or character range), and one of
three statuses: ``supported``, ``number_differs`` (same subject, other figure —
a human must look) or ``unsupported``. No model is involved: the same draft and
sources always give the same answer, and nothing is called supported without a
passage to show for it.

Additive capabilities, each off until its input is given:

* ``check_scope`` — a supported claim whose passage carries a qualifier the
  draft dropped (``tik``/``only``, a period, a place, a population, a test
  condition) becomes ``supported_scope_narrower`` with ``conditions`` and
  ``condition_dropped``; a draft that generalises (``visi``, ``every``,
  ``always``) beyond its passage gets the finding ``misleading_inference``.
* ``quote_sources`` — sentences inside „…“ "…" «…» of six words or more become
  claims of kind ``quote`` matched verbatim (or ``altered``) only inside those
  files; a quote with a name beside it needs that name near the passage
  (``attribution_unverified``).
* ``own_domains`` — every supported claim gets ``source_domain`` and ``party``
  (own | third_party | unknown) and ``attribution_missing`` when the sentence
  does not name the source.
* ``mode: reply`` — a thread reply checked point by point against the facts it
  cites, with ``point_unanswered``, ``no_spokesperson``, ``thread_mismatch``
  and ``no_sources``.
* ``mode: price_anchor`` — a struck-through reference price checked against
  the evidence and the words the draft uses for it.
* ``rumor`` — the rumour's figures paired with the draft's corrections.
* ``self_consistency`` — figures that disagree inside the draft itself.
* ``max_minutes`` — speaking length of the draft against a target.
* Credentials name their issuer, supported ones get a ``backer``; every result
  carries a ``fact_sheet`` (supported claims with sources, ready to paste) and
  ``corrections`` (one line per differing or unsupported claim).
* HTML sources index table rows (header + cells) and image ``alt`` text as
  passages, so a figure that only lives in a table or a chart's alt is found.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urlparse

from rocky.tools import ToolError, WorkspaceTools

FACTS_PATH = "runs/notes/facts.md"
PASSAGE_LIMIT = 300
TEXT_SUFFIXES = (".md", ".markdown", ".txt", ".html", ".htm", ".json", ".csv", ".tsv", ".xml")
HTML_SUFFIXES = (".html", ".htm")
# A passage supports a claim when it shares this many content words with it.
MIN_SHARED = 2
# Content words are letters only, at least this long, compared on their first STEM letters
# so Lithuanian endings (klientų / klientai / klientams) count as the same word.
WORD_MIN = 4
STEM = 5
QUOTE_MIN_WORDS = 6
ALTERED_RATIO = 0.6  # a quote sharing this share of its words with a passage is altered, not missing
WPM = 130
MODES = ("check", "reply", "price_anchor")
ANCHOR_KINDS = ("list_price", "competitor", "previous_price", "rrp")
GENERIC_SPOKESPERSON = {
    "komanda", "team", "support", "pagalba", "administracija", "admin", "info", "marketing", "rinkodara",
    "klientų aptarnavimas", "customer service", "customer support", "staff", "we", "mes", "įmonė", "company",
}

_SPACES = "\u00a0\u2009\u202f\u2007\u2008\u200a\u205f\u3000"
_NUM = re.compile(r"(?<![\d.,:])(?P<int>\d{1,3}(?: \d{3}(?!\d))+|\d+)(?P<frac>(?:[.,]\d+)+)?(?P<plus>\+)?(?!:\d)")
_DATE = re.compile(r"\b(?P<y1>\d{4})-(?P<m1>\d{2})-(?P<d1>\d{2})\b|\b(?P<d2>\d{1,2})\.(?P<m2>\d{1,2})\.(?P<y2>\d{4})\b|\b(?P<y3>\d{4})\.(?P<m3>\d{2})\.(?P<d3>\d{2})\b")
_URL = re.compile(r"https?://\S+")
_ISO_STANDARD = re.compile(r"\b(?:ISO|EN|IEC|DIN)\s?\d{4,5}\b")  # a standard's number is a credential, not a count
_PCT_AFTER = re.compile(r"^\s*(%|proc\.?|procent\w*|percent\w*|pct\b)", re.I)
_MONEY_AFTER = re.compile(r"^\s*(€|eur\b|eur[ųaiou]\w*|\$|usd\b|doleri\w*|dollars?\b)", re.I)
_MONEY_BEFORE = re.compile(r"[€$]\s*$")
_TIMES_AFTER = re.compile(r"^\s*(x\b|×|kart\w*|times\b)", re.I)
_YEARS_AFTER = re.compile(r"^\s*(m\.|met[ųaius]\w*|years?\b|yrs\b)", re.I)
_CONTEXT_AFTER = re.compile(r"^\s*(?:%|proc\.?|€|eur\w*|\$|usd|x\b|×)?\s*([^\W\d_]{2,})", re.I)
_WORD = re.compile(r"[^\W\d_]+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+(?=[\"'„“(«]?[A-ZĄČĘĖĮŠŲŪŽ0-9])")
_MD_PREFIX = re.compile(r"^\s{0,3}(?:#{1,6}\s+|[-*+•]\s+|\d{1,3}[.)]\s+|>\s*)+")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_MARK = re.compile(r"[*_`~]{1,3}")
_QUOTE = re.compile(r"[„“\"«]([^„“”\"«»]+?)[“”\"»]")
_NAME_RUN = re.compile(r"[A-ZĄČĘĖĮŠŲŪŽ](?:[a-ząčęėįšųūž]+|\.)(?:\s+[A-ZĄČĘĖĮŠŲŪŽ](?:[a-ząčęėįšųūž]+|\.))+")
_NOT_NAME = {"as", "the", "said", "says", "sako", "sakė", "kaip", "anot", "pasak", "teigia", "teigė", "according", "per", "in", "on", "at",
             "but", "and", "or", "when", "while", "after", "before", "then", "so", "yet", "with", "for", "kai", "nes", "bet", "tada", "nors", "dar", "jau"}
_SENT_END = re.compile(r"[.!?…][\"”“»]?(?=\s|$)")
_CAPITAL = re.compile(r"\b[A-ZĄČĘĖĮŠŲŪŽ][a-ząčęėįšųūž]{2,}\b|\bISO\s?\d{4,5}\b|\b[A-Z]{3,}\b")
_GENERALISER = re.compile(
    r"\bvis(?:i|ą|as|os|us|ų|iems|oms|ada|uomet|iškai)\b|\bkiekvien\w*|\bbet kur\b|\bbet kada\b|\bniekada\b|\bgarantuot\w*"
    r"|\ball\b|\bevery\b|\beveryone\b|\beverybody\b|\balways\b|\banywhere\b|\banytime\b|\bnever\b|\bguaranteed\b|\bany\b",
    re.I,
)
_OPERATIONAL = re.compile(
    r"\b(?:per|within|in)\s+\d+\s*(?:val|h\b|hours?|min|dien\w*|days?|darbo dien\w*|working days?|weeks?|sav\w*)"
    r"|\bkitą (?:darbo )?dieną\b|\bnext (?:working |business )?day\b|\bsame day\b|\btą pačią dieną\b|\b24/7\b"
    r"|\batsakom\w*|\bgrąžin\w*|\breturns?\b|\brefund\w*|\bdeliver\w*|\bpristat\w*|\bresponse\b|\bresponds?\b|\bshipping\b|\bopen(?:ing)? hours\b|\bdarbo laikas\b",
    re.I,
)
_TRENDS_COUNT = re.compile(r"\bsearch(?:es|ed)?\b|\bpaiešk\w*|\bužklaus\w*|\bper mėn\w*|\bper month\b|\ba month\b|/mo\b|\bmonthly\b|\bžmoni\w*|\bpeople\b", re.I)
_FUNC_WORDS = {
    "per", "in", "during", "from", "since", "nuo", "iki", "until", "by", "for", "last", "past", "previous", "of",
    "with", "to", "under", "up", "on", "among", "tarp", "those", "who", "the", "a", "an", "compared", "lyginant",
    "palyginti", "su", "sample", "metais", "metų", "controlled",
}

SUPERLATIVES = {
    "first": r"\bfirst\b|\bpirm(?:as|a|oji|asis|ieji|osios|ą|o|os|ų|am|ai|us|ais)\b|\bnumber one\b|\bno\.?\s*1\b|#\s*1\b|\bnr\.?\s*1\b",
    "largest": r"\b(?:largest|biggest)\b|\bdidžiaus\w*",
    "best": r"\bbest\b|\bgeriaus\w*",
    "fastest": r"\bfastest\b|\bgreičiaus\w*",
    "leading": r"\bleading\b|\bleaders?\b|\blyder\w*",
    "only": r"\bthe only\b|\bonly one\b|\bvienintel\w*",
    "cheapest": r"\b(?:cheapest|lowest)\b|\bpigiaus\w*|\bmažiaus\w*",
    "most": r"\bmost (?:popular|trusted|chosen|experienced|awarded|recommended)\b|\bpopuliariaus\w*|\bdaugiaus\w*|\bpatikimiaus\w*",
    "top": r"\btop[- ]\d+\b|\btop[- ]rated\b",
}
CREDENTIALS = {
    "certified": r"\bcertif\w*|\bsertifik\w*|\bakredit\w*|\baccredit\w*|\biso\s?\d{4,5}\b",
    "partner": r"\bpartner\w*",
    "award": r"\baward\w*|\bapdovanoj\w*|\bwinner\b|\blaureat\w*|\bnugalėtoj\w*",
    "licensed": r"\blicens\w*|\blicencij\w*",
}
_FAMILIES = {name: re.compile(pattern, re.I) for name, pattern in {**SUPERLATIVES, **CREDENTIALS}.items()}

# Scope qualifiers (#309): a family counts as kept when the draft keeps any one of its phrases.
_SCOPE = {
    "only": re.compile(r"\b(?:tik|tiktai|only|solely|exclusively|išskirtinai)\b", re.I),
    "period": re.compile(
        r"\b(?:per|in|during|from|since|nuo|iki|until|by|for)\s+(?:(?:19|20)\d{2}(?:\s*m\.)?|Q[1-4]\s*(?:19|20)\d{2})"
        r"|\b(?:19|20)\d{2}\s*m\.|\b(?:19|20)\d{2}\s*(?:metais|metų)\b"
        r"|\b(?:last|past|previous)\s+(?:year|month|quarter|week|\d+\s+(?:years|months|weeks|days))\b"
        r"|\bpernai\b|\bpraėjusiais metais\b|\bpraėjusį mėnesį\b|\bper paskutini\w+ \d+ \w+"
        r"|\bper\s+(?:metus|mėnesį|savaitę|dieną|valandą|ketvirtį)\b|\bper\s+\d+\s+(?:mėn|men|dien|sav|val|met)\w*"
        r"|\bper\s+(?:month|year|week|day|hour|quarter|user|customer|order|visit|session|employee)\b"
        r"|\ba\s+(?:month|year|week|day)\b|\bmonthly\b|\bannual(?:ly)?\b|\bweekly\b",
        re.I,
    ),
    "place": re.compile(r"\b[A-ZĄČĘĖĮŠŲŪŽ][a-ząčęėįšųūž]{2,}(?:uje|yje|oje|ėje|iuose|uose|ose|ėse|yse)\b|\bin [A-Z][a-z]{2,}(?: [A-Z][a-z]{2,})?\b"),
    "population": re.compile(r"\b(?:among|tarp|respondent\w*|apklaust\w*|surveyed|imtis|imtyje|sample of \d+|n\s*=\s*\d+)\b", re.I),
    "conditions": re.compile(
        r"\blaborator\w*|\bbandym\w*|\btest(?:s|ed|ing)?\b|\btyrim\w*|\bstudy\b|\biso\s?\d{4,5}\b|\d+\s?°\s?C\b|\d+\s?%\s?RH\b|\bin vitro\b"
        r"|\bunder controlled\b|\bkontroliuojam\w*|\bup to\b|\biki\s+\d|\bcompared (?:with|to)\b|\blyginant su\b|\bpalyginti su\b"
        r"|\bon average\b|\bvidutin\w*|\baverage\b|\bestimated\b|\bapytiksl\w*|\bapie \d|\babout \d|\bapprox\w*",
        re.I,
    ),
}
# Words a draft uses to describe a reference price (#182); each implies one reference_kind.
_ANCHOR_WORDS = {
    "previous_price": re.compile(r"\bbuvo\b|\banksčiau\b|\bankstesn\w*|\bsena kaina\b|\bwas\b|\bpreviously\b|\bold price\b|\bbefore\b|\biki šiol\b", re.I),
    "list_price": re.compile(r"\bįprasta kaina\b|\bregular price\b|\blist price\b|\bkainorašt\w*|\bstandartinė kaina\b|\bbazinė kaina\b|\bstandard price\b", re.I),
    "rrp": re.compile(r"\bRRP\b|\bMSRP\b|\brekomenduojam\w* (?:mažmenin\w* )?kain\w*|\brecommended retail\b", re.I),
    "competitor": re.compile(r"\bkonkurent\w*|\bcompetitors?\b|\belsewhere\b|\bkitur\b|\brinkos kaina\b|\bmarket price\b|\bpas kitus\b", re.I),
}
# Who stands behind a credential (#307): decided by the source domain first, then by the passage's words.
_REVIEW_DOMAINS = ("trustpilot", "capterra", "g2.com", "clutch.co", "yelp", "glassdoor", "google.com/maps", "atsiliepimai", "reviews")
_BACKER_WORDS = [
    ("industry_body", re.compile(
        r"\basociacij\w*|\bassociation\b|\brūm\w*|\bchamber\b|\binstitut\w*|\bministerij\w*|\bministry\b|\bfederacij\w*|\bfederation\b"
        r"|\bcouncil\b|\btaryb\w*|\baccredit\w*|\bakredit\w*|\biso\b|\bpartnersdirectory\b|\bwithgoogle\b|\bregistr\w*|\bdirectory\b"
        r"|\bcertification body\b|\bsertifikavimo\b|\bagentūr\w* prie\b|\bcommission\b|\bkomisij\w*", re.I)),
    ("independent_review", re.compile(
        r"\btrustpilot\b|\bcapterra\b|\bclutch\b|\bgoogle reviews\b|\breview\w*|\bapžvalg\w*|\bratings?\b|\breiting\w*"
        r"|\bjournal\w*|\bžurnal\w*|\bpress\b|\bspaud\w*|\b15min\b|\bdelfi\b|\blrt\b|\bmagazine\b|\bnewspaper\b|\blaikrašt\w*", re.I)),
    ("customer", re.compile(r"\bklient\w*|\bcustomer\w*|\bclient\w*|\bužsakov\w*|\btestimonial\w*|\batsiliepim\w*|\bpirkėj\w*", re.I)),
]
_FIRST_PERSON = re.compile(r"\b(?:mes|mūsų|mums|we|our|us|esame|turime)\b", re.I)

STOPWORDS = {
    "that", "this", "with", "from", "have", "more", "than", "their", "there", "which", "were", "been", "also",
    "about", "into", "over", "your", "they", "them", "what", "when", "will", "would", "each", "every", "some",
    "such", "very", "while", "where", "these", "those", "because", "after", "before", "within", "through",
    "kuri", "kurie", "kuris", "kurią", "kurių", "kurio", "kurios", "mūsų", "jūsų", "buvo", "taip", "labai",
    "arba", "kaip", "todėl", "tačiau", "visi", "visų", "visus", "visas", "visos", "daugiau", "mažiau", "šiuo",
    "šiais", "šiais", "savo", "bent", "tiek", "kiek", "apie", "prie", "tarp", "kartu", "jeigu", "nors", "taigi",
    "dabar", "tada", "čia", "turi", "turime", "turėti", "gali", "galima", "būti", "būtų", "esame", "esate",
}
_NOT_ISSUERS = STOPWORDS | {"mes", "the", "our", "since", "nuo", "gavome", "tapome", "esame", "turime", "with", "and", "for", "įmonė", "company", "agentūra"}


@dataclass
class Number:
    raw: str
    value: Any  # float, or an ISO date string
    cls: str  # percent | money | x | years | year | count | date
    candidates: frozenset
    context: str = ""
    approx: bool = False

    def unit(self) -> str | None:
        return {"percent": "%", "money": "€", "x": "x", "years": "years", "year": "year", "date": "date"}.get(self.cls)


@dataclass
class Passage:
    source: str
    index: int
    text: str
    line: int | None
    offset: int | None
    by_chars: bool
    stems: set[str] = field(default_factory=set)
    numbers: list[Number] = field(default_factory=list)
    families: set[str] = field(default_factory=set)

    def location(self) -> str:
        if self.by_chars:
            return f"chars {self.offset}–{self.offset + len(self.text)}"
        return f"line {self.line}"


@dataclass
class Claim:
    text: str
    line: int | None
    kind: str
    numbers: list[Number]
    families: set[str]
    stems: set[str]
    issuer: str | None = None
    issuer_stems: set[str] = field(default_factory=set)
    block: str = ""  # the draft block the sentence came from (for attribution and citation checks)
    attributed_to: str | None = None


# --- text in ----------------------------------------------------------------


def _is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _squash(text: str) -> str:
    text = text.translate({ord(ch): " " for ch in _SPACES})
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def _line_starts(raw: str) -> list[int]:
    starts, offset = [], 0
    for line in raw.splitlines(keepends=True):
        starts.append(offset)
        offset += len(line)
    return starts or [0]


_BLOCK_TAGS = {
    "p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "tr", "table", "br", "hr",
    "section", "article", "header", "footer", "blockquote", "pre", "title", "dd", "dt", "figcaption", "nav",
    "aside", "main", "body", "html", "head", "form", "fieldset", "legend", "address", "summary", "details", "caption",
}
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}
_CELL_TAGS = {"td", "th"}


class _Blocks(HTMLParser):
    """Collects visible text block by block with the line and column where each block starts.

    Table rows become one block each, header cells paired with the row's cells
    (``Klientai: 180 · Metai: 2025``), so a figure that only lives in a cell
    still sits next to the words that name it (#236). An image's ``alt`` text
    is a block of its own (#255).
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, int, int]] = []
        self._buf: list[str] = []
        self._pos: tuple[int, int] | None = None
        self._skip = 0
        self._table = 0
        self._cell: list[str] | None = None
        self._row: list[str] = []
        self._row_pos: tuple[int, int] | None = None
        self._header: list[str] | None = None
        self._row_is_header = False

    def _flush(self) -> None:
        text = _squash(" ".join(self._buf))
        if text and self._pos is not None:
            self.blocks.append((text, self._pos[0], self._pos[1]))
        self._buf, self._pos = [], None

    def _flush_row(self) -> None:
        cells = [c for c in self._row]
        if any(cells) and self._row_pos is not None:
            if self._header and not self._row_is_header and len(self._header) == len(cells):
                text = " · ".join(f"{h}: {c}" if h and c else (h or c) for h, c in zip(self._header, cells))
            else:
                text = " · ".join(c for c in cells if c)
            text = _squash(text)
            if text:
                self.blocks.append((text, self._row_pos[0], self._row_pos[1]))
            if self._header is None:
                self._header = cells
        self._row, self._row_pos, self._row_is_header = [], None, False

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        if tag == "img" and not self._skip:
            alt = _squash(dict(attrs).get("alt") or "")
            if alt:
                self._flush()
                self.blocks.append((alt, self.getpos()[0], self.getpos()[1]))
            return
        if tag == "table":
            self._flush()
            if self._table:
                self._flush_row()
            self._table += 1
            self._header, self._row, self._row_pos = None, [], None
            return
        if self._table and tag == "tr":
            self._flush()
            self._flush_row()
            self._row_pos = self.getpos()
            return
        if self._table and tag in _CELL_TAGS:
            self._flush()
            if self._row_pos is None:
                self._row_pos = self.getpos()
            self._cell = []
            if tag == "th":
                self._row_is_header = True
            return
        if tag in _BLOCK_TAGS:
            self._flush()

    def handle_startendtag(self, tag: str, attrs: Any) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        if self._table and tag in _CELL_TAGS:
            if self._cell is not None:
                self._row.append(_squash(" ".join(self._cell)))
            self._cell = None
            return
        if self._table and tag == "tr":
            self._flush_row()
            return
        if tag == "table":
            if self._cell is not None:
                self._row.append(_squash(" ".join(self._cell)))
                self._cell = None
            self._flush_row()
            self._table = max(0, self._table - 1)
            self._header = None
            return
        if tag in _BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip or not data.strip():
            return
        if self._cell is not None:
            self._cell.append(data)
            return
        if self._pos is None:
            self._pos = self.getpos()
        self._buf.append(data)

    def close(self) -> None:
        super().close()
        if self._cell is not None:
            self._row.append(_squash(" ".join(self._cell)))
            self._cell = None
        self._flush_row()
        self._flush()


def _looks_like_html(text: str) -> bool:
    head = text[:4000].lower()
    return bool(re.search(r"<(?:!doctype|html|body|head|p|div|h[1-6]|li|table)\b", head))


def _blocks(raw: str, html: bool) -> list[tuple[str, int, int]]:
    """(text, 1-based line, char offset) per block: HTML by element, plain text by line."""
    if html:
        parser = _Blocks()
        parser.feed(raw)
        parser.close()
        starts = _line_starts(raw)
        return [(text, line, starts[min(line, len(starts)) - 1] + col) for text, line, col in parser.blocks]
    out, offset = [], 0
    for n, line in enumerate(raw.splitlines(keepends=True), 1):
        text = _squash(line)
        if text:
            out.append((text, n, offset))
        offset += len(line)
    return out


def _plain(text: str) -> str:
    """Markdown and table noise out, so list markers are not numbers and link targets are not words."""
    text = _MD_PREFIX.sub("", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_MARK.sub("", text)
    return _squash(text.replace("|", " "))


# --- numbers and words ----------------------------------------------------------


def _fmt(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") if value != int(value) else str(int(value))


def _classify(text: str, m: re.Match, value: float, has_frac: bool) -> tuple[str, str]:
    after = text[m.end(): m.end() + 40]
    before = text[max(0, m.start() - 3): m.start()]
    if _PCT_AFTER.match(after):
        cls = "percent"
    elif _MONEY_AFTER.match(after) or _MONEY_BEFORE.search(before):
        cls = "money"
    elif _TIMES_AFTER.match(after):
        cls = "x"
    elif _YEARS_AFTER.match(after):
        cls = "years"
    elif not has_frac and 1900 <= value <= 2100 and value == int(value):
        cls = "year"
    else:
        cls = "count"
    ctx = _CONTEXT_AFTER.match(after)
    return cls, (ctx.group(1) if ctx else "")


def _numbers(text: str, language: str) -> list[Number]:
    """Numeric tokens with every reading of an ambiguous separator kept as a candidate."""
    text = _ISO_STANDARD.sub(lambda m: " " * len(m.group(0)), _URL.sub(" ", _squash(text)))
    out: list[Number] = []
    for m in _DATE.finditer(text):
        g = m.groupdict()
        y, mo, d = (g["y1"], g["m1"], g["d1"]) if g["y1"] else (g["y2"], g["m2"], g["d2"]) if g["y2"] else (g["y3"], g["m3"], g["d3"])
        iso = f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
        out.append(Number(m.group(0), iso, "date", frozenset({iso})))
    text = _DATE.sub(lambda m: " " * len(m.group(0)), text)
    for m in _NUM.finditer(text):
        int_part = m.group("int").replace(" ", "")
        frac = m.group("frac") or ""
        grouped = " " in m.group("int")
        candidates: set[str] = set()
        if not frac:
            primary = float(int_part)
            candidates.add(_fmt(primary))
        else:
            groups = re.findall(r"\d+", frac)
            seps = re.findall(r"[.,]", frac)
            if len(groups) >= 2:
                if all(len(g) == 3 for g in groups) and len(set(seps)) == 1:
                    primary = float(int_part + "".join(groups))
                else:
                    primary = float(int_part + "".join(groups[:-1]) + "." + groups[-1])
                candidates.add(_fmt(primary))
            else:
                g, sep = groups[0], seps[0]
                decimal = float(int_part + "." + g)
                if grouped or len(g) != 3:
                    primary = decimal
                    candidates.add(_fmt(decimal))
                else:
                    thousands = float(int_part + g)
                    candidates.update({_fmt(decimal), _fmt(thousands)})
                    lt_decimal = sep == ","
                    primary = decimal if (lt_decimal if language == "lt" else not lt_decimal) else thousands
        cls, context = _classify(text, m, primary, bool(frac))
        out.append(Number(m.group(0), primary, cls, frozenset(candidates), context, bool(m.group("plus"))))
    return out


def _measurable(n: Number) -> bool:
    """Bare counts below 2 are not claims; an ambiguous 1,250 counts because one reading is."""
    return n.cls != "count" or any(float(c) >= 2 for c in n.candidates)


def _families(text: str) -> dict[str, list[tuple[int, int]]]:
    found: dict[str, list[tuple[int, int]]] = {}
    for name, rx in _FAMILIES.items():
        spans = [m.span() for m in rx.finditer(text)]
        if spans:
            found[name] = spans
    return found


def _stems(text: str, exclude: list[tuple[int, int]] = ()) -> set[str]:
    out = set()
    for m in _WORD.finditer(text):
        if any(a <= m.start() < b for a, b in exclude):
            continue
        word = m.group(0).casefold()
        if len(word) >= WORD_MIN and word not in STOPWORDS:
            out.add(word[:STEM])
    return out


def _loose_stems(text: str) -> set[str]:
    """Stems of every word of three letters or more plus every digit run — for qualifier and name checks."""
    return {t.casefold()[:STEM] for t in re.findall(r"\d+|[^\W\d_]{3,}", text)}


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", text.casefold()))


def _domain(url: str) -> str | None:
    host = urlparse(url.rstrip(".,;:)»“”\"'")).netloc.lower()
    return host[4:] if host.startswith("www.") else (host or None)


def _issuer(sentence: str, spans: list[tuple[int, int]]) -> tuple[str | None, set[str]]:
    """Capitalised words (and ISO numbers) in a credential sentence that are not the credential word itself."""
    names: list[str] = []
    for m in _CAPITAL.finditer(sentence):
        word = m.group(0)
        if _ISO_STANDARD.fullmatch(word):
            names.append(word)  # the standard's number names the issuer even though it is also the credential word
            continue
        if any(a <= m.start() < b for a, b in spans):
            continue
        if m.start() == 0 and word.casefold() in _NOT_ISSUERS:
            continue
        if word.casefold() in _NOT_ISSUERS or any(rx.search(word) for rx in _FAMILIES.values()):
            continue
        names.append(word)
    if not names:
        return None, set()
    return " ".join(names), {w.casefold()[:STEM] for n in names for w in re.findall(r"\d+|[^\W\d_]+", n) if len(w) >= 3}


# --- claims out of the draft -------------------------------------------------------


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if _WORD.search(s)]


def _claims(blocks: list[tuple[str, int, int]], language: str) -> list[Claim]:
    claims: list[Claim] = []
    for text, line, _offset in blocks:
        plain = _plain(text)
        for sentence in _sentences(plain):
            numbers = [n for n in _numbers(sentence, language) if _measurable(n)]
            families = _families(sentence)
            superlative = [f for f in families if f in SUPERLATIVES]
            credential = [f for f in families if f in CREDENTIALS]
            issuer, issuer_stems = None, set()
            if numbers:
                kind = "number" if any(n.cls not in {"year", "date"} for n in numbers) else "date"
                stems = _stems(sentence)
            elif superlative:
                kind, stems = "superlative", _stems(sentence, [s for f in superlative for s in families[f]])
            elif credential:
                spans = [s for f in credential for s in families[f]]
                kind, stems = "credential", _stems(sentence, spans)
                issuer, issuer_stems = _issuer(sentence, spans)
            else:
                continue
            claims.append(Claim(sentence, line, kind, numbers, set(families), stems, issuer, issuer_stems, plain))
    return claims


def _names(text: str) -> list[str]:
    """Runs of two or more capitalised words, leading function words (As, Said, Sako) stripped."""
    out = []
    for m in _NAME_RUN.finditer(text):
        tokens = m.group(0).split()
        while tokens and tokens[0].casefold().rstrip(".") in _NOT_NAME:
            tokens.pop(0)
        if len(tokens) >= 2:
            out.append(" ".join(tokens))
    return out


def _name_near(text: str, start: int, end: int) -> str | None:
    """A capitalised name beside a quote, within its sentence: the first after it, else the last before it."""
    after = text[end: end + 120]
    cut = _SENT_END.search(after)
    after = after[: cut.start()] if cut else after
    found = _names(after)
    if found:
        return found[0]
    before = text[max(0, start - 120): start]
    ends = list(_SENT_END.finditer(before))
    before = before[ends[-1].end():] if ends else before
    found = _names(before)
    return found[-1] if found else None


def _quote_claims(blocks: list[tuple[str, int, int]]) -> list[Claim]:
    claims: list[Claim] = []
    for text, line, _offset in blocks:
        plain = _plain(text)
        for m in _QUOTE.finditer(plain):
            inner = m.group(1).strip(" ,;:—–-")
            if len(_WORD.findall(inner)) < QUOTE_MIN_WORDS:
                continue
            claim = Claim(inner, line, "quote", [], set(), _stems(inner), block=plain)
            claim.attributed_to = _name_near(plain, m.start(), m.end())
            claims.append(claim)
    return claims


# --- sources in ----------------------------------------------------------------


def _read_source(tools: WorkspaceTools, source: str, fetch: Callable[[str], str] | None) -> tuple[str | None, bool, bool, str | None]:
    """(text, is_html, by_chars, reason-if-missing)."""
    if _is_url(source):
        if fetch is None:
            return None, False, True, "not fetched (no fetch available; save the page into the workspace instead)"
        try:
            text = fetch(source)
        except Exception as exc:  # the engine's fetcher may raise anything
            return None, False, True, f"fetch failed: {exc}"
        if not isinstance(text, str) or not text.strip():
            return None, False, True, "fetched but empty"
        return text, _looks_like_html(text), True, None
    path = tools.resolve(source)
    if not path.exists():
        return None, False, False, "not found"
    if path.is_dir():
        return None, False, False, "is a directory"
    suffix = path.suffix.lower()
    if suffix and suffix not in TEXT_SUFFIXES:
        return None, False, False, f"unsupported file type {suffix} (text, markdown, html, json or csv only)"
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return None, False, False, "empty"
    return text, suffix in HTML_SUFFIXES or (not suffix and _looks_like_html(text)), False, None


def _passages(source: str, text: str, html: bool, by_chars: bool, language: str) -> list[Passage]:
    out = []
    for index, (block, line, offset) in enumerate(_blocks(text, html)):
        plain = _plain(block)
        if not plain:
            continue
        p = Passage(source, index, plain, line, offset, by_chars)
        p.stems = _stems(plain)
        p.numbers = [n for n in _numbers(plain, language) if _measurable(n)]
        p.families = set(_families(plain))
        out.append(p)
    return out


def _load_sources(tools: WorkspaceTools, sources: list[str], fetch, language: str, missing: list[str]) -> tuple[list[Passage], list[dict]]:
    passages: list[Passage] = []
    read: list[dict[str, Any]] = []
    for source in sources:
        text, html, by_chars, reason = _read_source(tools, source, fetch)
        if text is None:
            missing.append(f"{source}: {reason}")
            continue
        found = _passages(source, text, html, by_chars, language)
        read.append({"source": source, "passages": len(found), "chars": len(text)})
        passages.extend(found)
    return passages, read


# --- matching ------------------------------------------------------------------


def _same(a: Number, b: Number) -> bool:
    return bool(a.candidates & b.candidates) and (a.cls == "percent") == (b.cls == "percent")


def _rel_diff(a: Number, b: Number) -> float:
    try:
        x, y = float(a.value), float(b.value)
    except (TypeError, ValueError):
        return 1.0
    return abs(x - y) / max(abs(x), abs(y), 1e-9)


def _compare(claim: Claim, passage: Passage) -> tuple[str, tuple, dict]:
    """status, sort key (higher is better) and details for one claim against one passage."""
    shared = sorted(claim.stems & passage.stems)
    if claim.kind in {"number", "date"}:
        hits: list[tuple[Number, Number]] = []
        misses: list[Number] = []
        for cn in claim.numbers:
            hit = next((pn for pn in passage.numbers if _same(cn, pn)), None)
            (hits.append((cn, hit)) if hit else misses.append(cn))
        if len(shared) >= MIN_SHARED and not misses:
            return "supported", (3, len(shared), 0.0, -passage.index), {"shared": shared, "hits": hits}
        if len(shared) >= MIN_SHARED and misses:
            used = {id(pn) for _, pn in hits}
            diffs: list[tuple[Number, Number]] = []
            for cn in misses:
                pool = [pn for pn in passage.numbers if id(pn) not in used and pn.cls == cn.cls]
                if pool:
                    best = min(pool, key=lambda pn: (_rel_diff(cn, pn), pn.raw))
                    used.add(id(best))
                    diffs.append((cn, best))
            if diffs:
                worst = max(_rel_diff(cn, pn) for cn, pn in diffs)
                return "number_differs", (2, len(shared), -worst, -passage.index), {"shared": shared, "hits": hits, "diffs": diffs}
            return "none", (1, len(shared), 0.0, -passage.index), {"shared": shared}
        return "none", (0, len(shared), 0.0, -passage.index), {"shared": shared}
    wanted = claim.families & (set(SUPERLATIVES) if claim.kind == "superlative" else set(CREDENTIALS))
    common = sorted(wanted & passage.families)
    # A credential that names its issuer (Google, ISO 9001) needs a passage that names it too (#307).
    issuer_ok = not claim.issuer_stems or bool(claim.issuer_stems & _loose_stems(passage.text))
    if common and shared and issuer_ok:
        return "supported", (3, len(shared), 0.0, -passage.index), {"shared": shared, "families": common}
    if len(shared) >= MIN_SHARED:
        return "none", (1, len(shared), 0.0, -passage.index), {"shared": shared}
    return "none", (0, len(shared), 0.0, -passage.index), {"shared": shared}


def _clip(text: str, limit: int = PASSAGE_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _number_out(n: Number) -> dict[str, Any]:
    return {"raw": n.raw, "value": n.value, "unit": n.unit(), "context": n.context or None}


def _passage_url(p: Passage) -> str | None:
    if _is_url(p.source):
        return p.source
    m = _URL.search(p.text)
    return m.group(0) if m else None


def _backer(p: Passage, own_domains: list[str]) -> str:
    url = _passage_url(p)
    domain = _domain(url) if url else None
    if domain:
        if any(domain == own or domain.endswith("." + own) for own in own_domains):
            return "self"
        if any(mark in (url or "") for mark in _REVIEW_DOMAINS):
            return "independent_review"
    for name, rx in _BACKER_WORDS:
        if rx.search(p.text):
            return name
    return "self" if _FIRST_PERSON.search(p.text) else "none"


def _judge(claim: Claim, passages: list[Passage], own_domains: list[str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "text": claim.text,
        "line": claim.line,
        "kind": claim.kind,
        "numbers": [_number_out(n) for n in claim.numbers],
        "status": "unsupported",
        "source": None,
        "location": None,
        "passage": None,
        "note": "",
    }
    if claim.kind == "credential":
        out["issuer"] = claim.issuer
    best: tuple[tuple, Passage, str, dict] | None = None
    for p in passages:
        status, key, detail = _compare(claim, p)
        if best is None or key > best[0]:
            best = (key, p, status, detail)
    if best is None:
        out["note"] = "no source passages to check against"
        return out
    key, p, status, detail = best
    where = f"{p.source} {p.location()}"
    if status == "supported":
        out.update(status="supported", source=p.source, location=p.location(), passage=_clip(p.text), shared_words=detail["shared"])
        if claim.kind in {"number", "date"}:
            raws = ", ".join(pn.raw for _, pn in detail["hits"])
            out["note"] = f"{where} carries {raws} with the words {', '.join(detail['shared'])}."
        else:
            out["note"] = f"{where} makes the same '{', '.join(detail['families'])}' claim with the words {', '.join(detail['shared'])}."
            if claim.kind == "credential":
                out["backer"] = _backer(p, own_domains or [])
        return out
    if status == "number_differs":
        pairs = [{"claim": cn.raw, "source": pn.raw, "context": cn.context or None} for cn, pn in detail["diffs"]]
        said = "; ".join(f"draft says {d['claim']}, source says {d['source']}" for d in pairs)
        out.update(
            status="number_differs",
            source=p.source,
            location=p.location(),
            passage=_clip(p.text),
            shared_words=detail["shared"],
            differs=pairs,
            note=f"{said} ({where}) — a human must decide which is right.",
        )
        return out
    if key[0] == 1:
        out["nearest"] = {"source": p.source, "location": p.location(), "passage": _clip(p.text), "shared_words": detail["shared"]}
        what = "the number" if claim.kind in {"number", "date"} else f"the '{', '.join(sorted(claim.families))}' claim"
        tail = " but not the claim."
        if claim.kind == "credential" and claim.issuer and claim.issuer_stems and not (claim.issuer_stems & _loose_stems(p.text)):
            tail = f" but does not name the issuer {claim.issuer}."
        out["note"] = f"No passage carries {what}; the closest is {where}, which shares the words {', '.join(detail['shared'])}{tail}"
    else:
        out["note"] = (
            "No source passage shares two content words and the number with this sentence."
            if claim.kind in {"number", "date"}
            else f"No source passage makes a '{', '.join(sorted(claim.families))}' claim about the same thing."
        )
    return out


# --- quotes (#305, #283, #255) ---------------------------------------------------


def _name_present(name: str, text: str) -> bool:
    words = {w.casefold() for w in _WORD.findall(text)}
    for token in _WORD.findall(name):
        prefix = token.casefold()[: max(3, min(len(token) - 1, STEM))]
        if not any(w.startswith(prefix) for w in words):
            return False
    return True


def _judge_quote(claim: Claim, passages: list[Passage], reason: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "text": claim.text,
        "line": claim.line,
        "kind": "quote",
        "numbers": [],
        "status": "unsupported",
        "source": None,
        "location": None,
        "passage": None,
        "note": "",
        "attributed_to": claim.attributed_to,
    }
    if reason:
        out["note"] = reason
        return out
    if not passages:
        out["note"] = "quote_sources hold no text to check against"
        return out
    wanted = _norm(claim.text)
    hit = next((p for p in passages if wanted in _norm(p.text)), None)
    if hit is not None:
        out.update(status="supported", source=hit.source, location=hit.location(), passage=_clip(hit.text))
        out["note"] = f"{hit.source} {hit.location()} carries the quote verbatim."
        _check_attribution(out, claim, hit, passages)
        return out
    scored = sorted(passages, key=lambda p: (-len(claim.stems & p.stems), p.index))
    nearest = scored[0]
    shared = sorted(claim.stems & nearest.stems)
    ratio = len(shared) / max(1, len(claim.stems))
    if len(shared) >= MIN_SHARED and ratio >= ALTERED_RATIO:
        out.update(status="altered", source=nearest.source, location=nearest.location(), passage=_clip(nearest.text), shared_words=shared)
        out["note"] = f"The quote differs from {nearest.source} {nearest.location()} (shares {', '.join(shared)}) — quote it verbatim or drop the quotation marks."
        _check_attribution(out, claim, nearest, passages)
        return out
    if len(shared) >= MIN_SHARED:
        out["nearest"] = {"source": nearest.source, "location": nearest.location(), "passage": _clip(nearest.text), "shared_words": shared}
    out["note"] = "No interview source carries this quote; the words in quotation marks are not in quote_sources."
    return out


def _check_attribution(out: dict, claim: Claim, hit: Passage, passages: list[Passage]) -> None:
    if not claim.attributed_to:
        return
    near = [p for p in passages if p.source == hit.source and (abs(p.index - hit.index) <= 5 or p.index < 3)]
    found = any(_name_present(claim.attributed_to, p.text) for p in near)
    out["attribution"] = "ok" if found else "attribution_unverified"
    if not found:
        out["note"] += f" The draft attributes it to {claim.attributed_to}, but that name is not near the passage in {hit.source}."


# --- scope (#309) -------------------------------------------------------------------


def _qualifiers(text: str) -> dict[str, list[str]]:
    text = _URL.sub(" ", text)
    found: dict[str, list[tuple[int, int, str]]] = {}
    taken: list[tuple[int, int]] = []
    for family, rx in _SCOPE.items():
        for m in rx.finditer(text):
            if any(a <= m.start() < b or m.start() <= a < m.end() for a, b in taken):
                continue
            taken.append(m.span())
            found.setdefault(family, []).append((m.start(), m.end(), m.group(0).strip()))
    return {f: [t for _, _, t in sorted(v)] for f, v in found.items()}


def _keeps(phrase: str, claim_tokens: set[str]) -> bool:
    content = [t for t in re.findall(r"\d+|[^\W\d_]{3,}", phrase) if t.casefold() not in _FUNC_WORDS]
    if not content:
        content = re.findall(r"[^\W\d_]+", phrase)
    return all(t.casefold()[:STEM] in claim_tokens for t in content)


def _scope(claim: Claim, out: dict, passage_text: str, findings: list[dict]) -> None:
    claim_tokens = _loose_stems(claim.text)
    found = _qualifiers(passage_text)
    conditions = [phrase for phrases in found.values() for phrase in phrases]
    dropped: list[str] = []
    for family, phrases in found.items():
        if not any(_keeps(p, claim_tokens) for p in phrases):
            dropped.extend(phrases)
    out["conditions"] = conditions
    out["condition_dropped"] = dropped
    generalises = _GENERALISER.search(claim.text) and not _GENERALISER.search(passage_text)
    if dropped:
        out["status"] = "supported_scope_narrower"
        out["scope_hint"] = f"State the condition: {'; '.join(dropped)}."
        out["note"] += f" The passage holds only under: {'; '.join(dropped)} — the draft drops that."
    elif not conditions:
        out["note"] += " No qualifier words in the passage; scope could not be assessed by code."
    if generalises:
        word = _GENERALISER.search(claim.text).group(0)
        findings.append({"code": "misleading_inference", "line": claim.line, "claim": claim.text,
                         "detail": f"'{word}' generalises beyond the passage, which says nothing universal" + (f" and holds only under: {'; '.join(dropped)}" if dropped else "") + "."})


# --- party and attribution (#283) --------------------------------------------------------


def _party(out: dict, claim: Claim, passage: Passage | None, own_domains: list[str], findings: list[dict]) -> None:
    url = _passage_url(passage) if passage else None
    domain = _domain(url) if url else None
    out["source_domain"] = domain
    if not domain:
        out["party"] = "unknown"
        return
    own = any(domain == d or domain.endswith("." + d) for d in own_domains)
    out["party"] = "own" if own else "third_party"
    label = domain.split(".")[0]
    attributed = label in claim.block.casefold()
    out["attributed"] = attributed
    if not attributed:
        findings.append({"code": "attribution_missing", "line": claim.line, "claim": claim.text,
                         "detail": f"The sentence does not say this rests on {domain} ({out['party']}); name the source, e.g. 'pagal {label} …' / 'according to {label} …'."})


# --- modes ----------------------------------------------------------------------------


def _generic(value: Any) -> bool:
    text = str(value or "").strip().casefold().strip("[]")
    return not text or text in GENERIC_SPOKESPERSON or any(text == g or text.startswith(g + " ") for g in GENERIC_SPOKESPERSON)


def _reply(args: dict, reply_text: str, passages: list[Passage], sources_read: list[dict], findings: list[dict], missing: list[str]) -> dict[str, Any]:
    thread_url = str(args.get("thread_url") or "").strip()
    points = args.get("criticism_points") or []
    facts = args.get("facts") or []
    spokesperson = args.get("spokesperson") or {}
    if not thread_url:
        missing.append("thread_url: not given (the reply cannot be tied to the thread)")
    if not points:
        missing.append("criticism_points: none given (nothing to answer point by point)")
    if not facts:
        missing.append("facts: none given (no fact can answer a point)")
    reply_stems = _stems(reply_text) | set(re.findall(r"\d+", reply_text))
    present_facts: list[tuple[dict, set[str]]] = []
    for fact in facts:
        text = str(fact.get("text") if isinstance(fact, dict) else fact).strip()
        stems = _stems(text) | set(re.findall(r"\d+", text))
        need = min(MIN_SHARED, len(stems)) or 1
        if len(stems & reply_stems) >= need:
            present_facts.append((fact if isinstance(fact, dict) else {"text": text}, stems))
    point_out = []
    for point in points:
        ptext = str(point).strip()
        pstems = _stems(ptext) | set(re.findall(r"\d+", ptext))
        answer = next((f for f, s in present_facts if len(pstems & s) >= MIN_SHARED), None)
        point_out.append({"text": ptext, "answered": answer is not None, "fact": (answer or {}).get("text")})
        if answer is None:
            findings.append({"code": "point_unanswered", "detail": f"No fact in the reply answers: '{_short(ptext, 80)}'.", "point": ptext})
    name = spokesperson.get("name") if isinstance(spokesperson, dict) else spokesperson
    role = spokesperson.get("role") if isinstance(spokesperson, dict) else None
    spokesperson_ok = not _generic(name) and not _generic(role)
    if not spokesperson_ok:
        findings.append({"code": "no_spokesperson", "detail": "The reply needs a named person with a role, not a team or a placeholder."})
    elif _norm(str(name)) not in _norm(reply_text):
        spokesperson_ok = False
        findings.append({"code": "no_spokesperson", "detail": f"The reply text does not carry the spokesperson's name {name}."})
    fact_urls = {str(f.get("source")).rstrip("/") for f in facts if isinstance(f, dict) and f.get("source")}
    fact_urls |= {m.group(0).rstrip(".,;:)»“”\"'/") for p in passages for m in _URL.finditer(p.text)}
    fact_urls |= {p.source.rstrip("/") for p in passages if _is_url(p.source)}
    other = []
    for m in _URL.finditer(reply_text):
        url = m.group(0).rstrip(".,;:)»“”\"'")
        if thread_url and url.rstrip("/") == thread_url.rstrip("/"):
            continue
        if url.rstrip("/") in fact_urls:
            continue
        other.append(url)
    thread_ok = not other
    if other:
        findings.append({"code": "thread_mismatch", "detail": f"The reply points to {', '.join(other)} instead of continuing in {thread_url or 'the thread'}.", "urls": other})
    if not sources_read:
        findings.append({"code": "no_sources", "detail": "No source could be read; every figure in the reply stays unsupported."})
    return {"thread_url": thread_url or None, "points": point_out, "spokesperson": {"name": name, "role": role}, "spokesperson_ok": spokesperson_ok, "thread_ok": thread_ok}


def _money(value: Any, language: str) -> Number | None:
    if value is None or str(value).strip() == "":
        return None
    nums = _numbers(str(value), language)
    return nums[0] if nums else None


def _price_anchor(args: dict, draft_text: str, passages: list[Passage], evidence: str | None, language: str, findings: list[dict], missing: list[str]) -> dict[str, Any]:
    offer = _money(args.get("offer_price"), language)
    reference = _money(args.get("reference_price"), language)
    kind = str(args.get("reference_kind") or "").strip().lower() or None
    charged = args.get("charged_identical")
    for key, value in (("offer_price", offer), ("reference_price", reference), ("reference_kind", kind), ("evidence", evidence)):
        if value is None:
            missing.append(f"{key}: not given")
    if kind and kind not in ANCHOR_KINDS:
        raise ToolError(f"reference_kind must be one of {', '.join(ANCHOR_KINDS)}")
    if charged is None or charged == "":
        missing.append("charged_identical: not given (was the reference price ever charged for the identical offer?)")
        charged_flag = None
    else:
        charged_flag = _flag(charged, False)
    out: dict[str, Any] = {
        "offer_price": offer.value if offer else None,
        "reference_price": reference.value if reference else None,
        "reference_kind": kind,
        "evidence": evidence,
        "charged_identical": charged_flag,
        "reference_found": False,
        "source": None,
        "location": None,
        "passage": None,
        "kind_words": [],
        "implied_kind": None,
    }
    evidence_passages = [p for p in passages if evidence and p.source == evidence]
    if reference is not None and evidence_passages:
        hits = [p for p in evidence_passages if any(_same(reference, pn) for pn in p.numbers)]
        hits.sort(key=lambda p: (not any(_same(reference, pn) and pn.cls == "money" for pn in p.numbers), p.index))
        if hits:
            hit = hits[0]
            out.update(reference_found=True, source=hit.source, location=hit.location(), passage=_clip(hit.text))
    if reference is not None and evidence and not out["reference_found"]:
        reason = "the evidence could not be read" if not evidence_passages else f"{evidence} never shows {reference.raw}"
        findings.append({"code": "anchor_unsupported", "detail": f"Reference price {reference.raw} is not in the evidence: {reason}."})
    elif reference is not None and not evidence:
        findings.append({"code": "anchor_unsupported", "detail": "No evidence given for the reference price; a struck-through price needs a page or file that shows it."})
    if offer is not None and reference is not None and float(reference.value) <= float(offer.value):
        findings.append({"code": "anchor_below_offer", "detail": f"Reference {reference.raw} is not above the offer {offer.raw}; it anchors nothing."})
    implied = []
    for name, rx in _ANCHOR_WORDS.items():
        words = sorted({m.group(0) for m in rx.finditer(draft_text)})
        if words:
            implied.append((name, words))
    out["kind_words"] = [w for _, ws in implied for w in ws]
    if implied:
        out["implied_kind"] = implied[0][0] if len(implied) == 1 else [n for n, _ in implied]
        wrong = [(n, ws) for n, ws in implied if kind and n != kind]
        if wrong:
            said = "; ".join(f"'{', '.join(ws)}' says {n}" for n, ws in wrong)
            findings.append({"code": "anchor_misdescribed", "detail": f"The draft describes the reference as something else than {kind}: {said}."})
    if charged_flag is False:
        findings.append({"code": "anchor_never_charged", "detail": "The reference price was never charged for the identical offer; a struck-through or 'was' price must be a price actually charged, or the words must say what it is (RRP, competitor)."})
    return out


# --- corrections, rumours, consistency, length, trends --------------------------------------------


def _corrections(claims: list[dict], language: str) -> list[dict]:
    lt = language == "lt"
    out = []
    for c in claims:
        where = f"{c['source']} ({c['location']})" if c.get("source") else None
        if c["status"] == "number_differs":
            d = c["differs"][0]
            ctx = f" {d['context']}" if d.get("context") else ""
            line = (f"Ne {d['claim']}{ctx}, o {d['source']}{ctx} — šaltinis: {where}: „{_short(c['passage'], 120)}“"
                    if lt else f"Not {d['claim']}{ctx}, but {d['source']}{ctx} — source: {where}: \"{_short(c['passage'], 120)}\"")
        elif c["status"] == "unsupported":
            if c["kind"] == "quote":
                line = (f"Citata „{_short(c['text'], 80)}“ nerasta interviu šaltiniuose — cituoti pažodžiui iš failo arba nuimti kabutes."
                        if lt else f"Quote \"{_short(c['text'], 80)}\" is not in the interview sources — quote it verbatim from the file or drop the quotation marks.")
            else:
                line = (f"„{_short(c['text'], 80)}“ — šaltinio nėra: įrašyti faktą su URL į {FACTS_PATH} arba išbraukti."
                        if lt else f"\"{_short(c['text'], 80)}\" — no source: add the fact with its URL to {FACTS_PATH} or remove it.")
        elif c["status"] == "altered":
            line = (f"Citata „{_short(c['text'], 80)}“ skiriasi nuo šaltinio {where}: „{_short(c['passage'], 120)}“ — cituoti pažodžiui."
                    if lt else f"Quote \"{_short(c['text'], 80)}\" differs from {where}: \"{_short(c['passage'], 120)}\" — quote it verbatim.")
        elif c["status"] == "supported_scope_narrower":
            cond = "; ".join(c.get("condition_dropped") or [])
            line = (f"Pridėti sąlygą prie „{_short(c['text'], 60)}“: {cond} — šaltinis: {where}."
                    if lt else f"Add the condition to \"{_short(c['text'], 60)}\": {cond} — source: {where}.")
        else:
            continue
        out.append({"line": c["line"], "status": c["status"], "claim": c["text"], "correction": line, "source": c.get("source"), "location": c.get("location")})
    return out


def _fact_sheet(claims: list[dict]) -> tuple[list[dict], str]:
    rows = []
    for c in claims:
        if c["status"] not in {"supported", "supported_scope_narrower"}:
            continue
        value = ", ".join(f"{n['raw']}{(' ' + n['unit']) if n['unit'] in {'%', '€'} else ''}" for n in c["numbers"]) or "—"
        rows.append({"claim": c["text"], "kind": c["kind"], "value": value, "source": c["source"], "location": c["location"], "passage": c["passage"],
                     **({"backer": c["backer"]} if c.get("backer") else {}), **({"conditions": c["condition_dropped"]} if c.get("condition_dropped") else {})})
    lines = ["| # | Claim | Value | Source | Location | Passage |", "|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        cell = lambda s: str(s).replace("|", "/").replace("\n", " ")  # noqa: E731
        lines.append(f"| {i} | {cell(r['claim'])} | {cell(r['value'])} | {cell(r['source'])} | {cell(r['location'])} | {cell(_short(r['passage'], 120))} |")
    return rows, "\n".join(lines) if rows else ""


def _rumor(rumor: str, draft_claims: list[Claim], judged: list[dict], passages: list[Passage], language: str) -> list[dict]:
    out = []
    numbered = [(c, j) for c, j in zip(draft_claims, judged) if c.kind in {"number", "date"}]
    for rc in _claims(_blocks(rumor, False), language):
        if rc.kind not in {"number", "date"}:
            continue
        for rn in rc.numbers:
            paired = sorted(((len(rc.stems & c.stems), -i, c, j) for i, (c, j) in enumerate(numbered) if any(n.cls == rn.cls for n in c.numbers)), reverse=True)
            paired = [p for p in paired if p[0] >= 1]
            row: dict[str, Any] = {"subject": None, "rumor_value": rn.raw, "fact_value": None, "status": "missing_in_draft",
                                   "source": None, "location": None, "source_passage": None, "source_cited": False, "draft_line": None}
            if not paired:
                row["subject"] = " ".join(sorted(rc.stems)) or None
                out.append(row)
                continue
            shared, _, claim, judged_claim = paired[0]
            row["subject"] = " ".join(sorted(rc.stems & claim.stems))
            row["draft_line"] = claim.line
            candidates = [n for n in claim.numbers if n.cls == rn.cls and not _same(n, rn)]
            row["fact_value"] = candidates[0].raw if candidates else None
            row["status"] = "unsupported" if candidates else "repeats_rumor"
            for cand in candidates:
                hit = next((p for p in passages if any(_same(cand, pn) for pn in p.numbers) and (claim.stems & p.stems)), None)
                if hit is not None:
                    url = _passage_url(hit)
                    cited = bool(url and (url.rstrip("/") in claim.block or (_domain(url) or "x") in claim.block.casefold())) or hit.source.rsplit("/", 1)[-1] in claim.block
                    row.update(fact_value=cand.raw, status="supported", source=hit.source, location=hit.location(), source_passage=_clip(hit.text), source_url=url, source_cited=cited)
                    break
            out.append(row)
    return out


def _conflicts(claims: list[Claim]) -> list[dict]:
    seen: list[tuple[Number, Claim]] = [(n, c) for c in claims if c.kind in {"number", "date"} for n in c.numbers if n.cls not in {"year", "date"}]
    out, done = [], set()
    for i, (a, ca) in enumerate(seen):
        for j, (b, cb) in enumerate(seen):
            if j <= i or ca is cb or a.cls != b.cls or _same(a, b):
                continue
            same_context = a.context and b.context and a.context.casefold()[:STEM] == b.context.casefold()[:STEM]
            shared = ca.stems & cb.stems
            if not same_context and len(shared) < MIN_SHARED:
                continue
            key = (ca.text, cb.text, a.raw, b.raw)
            if key in done:
                continue
            done.add(key)
            subject = a.context if same_context else " ".join(sorted(shared))
            out.append({"subject": subject, "values": [{"value": a.raw, "text": ca.text, "line": ca.line}, {"value": b.raw, "text": cb.text, "line": cb.line}], "status": "conflict"})
    return out


def _length(text: str, max_minutes: Any, wpm: Any, findings: list[dict]) -> dict[str, Any]:
    words = len(_WORD.findall(text))
    rate = float(wpm or WPM)
    minutes = round(words / rate, 2) if rate > 0 else None
    out = {"words": words, "wpm": rate, "minutes": minutes, "max_minutes": float(max_minutes), "over_by_words": 0}
    if words == 0:
        out["status"] = "length_unknown"
        return out
    allowed = int(float(max_minutes) * rate)
    if words > allowed:
        out["over_by_words"] = words - allowed
        findings.append({"code": "too_long", "detail": f"{words} words ≈ {minutes} min at {int(rate)} wpm, over the {max_minutes}-minute target by {words - allowed} words."})
    return out


def _trends(out: dict, claim: Claim, passage: Passage | None, findings: list[dict]) -> None:
    url = _passage_url(passage) if passage else None
    if not url or "trends.google" not in url:
        return
    reasons = []
    if _TRENDS_COUNT.search(claim.text):
        reasons.append("absolute_count_from_index")
    tokens = _loose_stems(claim.text)
    if not any(re.fullmatch(r"(?:19|20)\d{2}", t) for t in tokens) and not _SCOPE["period"].search(claim.text):
        reasons.append("missing_window")
    if not _SCOPE["place"].search(claim.text) and not re.search(r"\b(?:LT|LV|EE|PL|UK|US|EU|DE)\b", claim.text):
        reasons.append("missing_geo")
    if reasons:
        out["trends"] = reasons
        findings.append({"code": "trends_relative", "line": claim.line, "claim": claim.text, "reasons": reasons,
                         "detail": "Google Trends gives a 0–100 index relative to the peak, not a count: state it as an index with geo and window, or cite an absolute-volume source."})


# --- summary ----------------------------------------------------------------------


def _short(text: str, limit: int = 48) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _summary(claims: list[dict], counts: dict[str, int], missing: list[str], findings: list[dict] = ()) -> str:
    if counts["total"] == 0:
        head = "No measurable claims found in the draft"
        text = head + (f" ({missing[0]})." if missing else ".")
    else:
        parts = [f"{counts['total']} claim{'s' if counts['total'] != 1 else ''}: {counts['supported']} supported"]
        if counts.get("supported_scope_narrower"):
            parts.append(f"{counts['supported_scope_narrower']} supported with a narrower scope")
        differs = [c for c in claims if c["status"] == "number_differs"]
        if differs:
            bits = []
            for c in differs[:3]:
                d = c["differs"][0]
                bits.append(f"{d['claim']} vs {d['source']}" + (f" {d['context']}" if d.get("context") else ""))
            more = f", +{len(differs) - 3} more" if len(differs) > 3 else ""
            parts.append(f"{len(differs)} number differs ({'; '.join(bits)}{more} — review)")
        if counts.get("altered"):
            parts.append(f"{counts['altered']} quote{'s' if counts['altered'] != 1 else ''} altered")
        unsupported = [c for c in claims if c["status"] == "unsupported"]
        if unsupported:
            quoted = ", ".join(f"'{_short(c['text'])}'" for c in unsupported[:3])
            more = f", +{len(unsupported) - 3} more" if len(unsupported) > 3 else ""
            parts.append(f"{len(unsupported)} unsupported ({quoted}{more})")
        text = ", ".join(parts) + "."
    if findings:
        codes: dict[str, int] = {}
        for f in findings:
            codes[f["code"]] = codes.get(f["code"], 0) + 1
        text += " Findings: " + ", ".join(f"{code}×{n}" if n > 1 else code for code, n in codes.items()) + "."
    return text


# --- the tool ------------------------------------------------------------------


def _flag(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"false", "0", "no", "off"}


def _detect_language(text: str) -> str:
    return "lt" if re.search(r"[ąčęėįšųūžĄČĘĖĮŠŲŪŽ]", text) else "en"


def _list(value: Any, name: str) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(s).strip() for s in value if str(s).strip()]
    raise ToolError(f"{name} must be a list of workspace paths or URLs")


def claim_check(tools: WorkspaceTools, args: dict, fetch: Callable[[str], str] | None = None) -> dict[str, Any]:
    """Every measurable claim in a draft, matched to the passage that supports it.

    ``draft`` (text) or ``draft_path`` (workspace .md/.txt/.html). ``sources``:
    workspace paths and/or http(s) URLs; ``facts_path`` (default
    ``runs/notes/facts.md``) is added when it exists unless ``include_facts`` is
    false. ``language`` ``lt``/``en`` only decides how an ambiguous separator
    such as 12,400 is read; both readings are kept for matching anyway. URLs are
    read only through ``fetch`` and otherwise reported under ``missing``.
    See the module docstring for the optional inputs (``check_scope``,
    ``quote_sources``, ``own_domains``, ``mode``, ``rumor``, ``self_consistency``,
    ``max_minutes``); each is off until given and reports absent inputs under
    ``missing`` instead of guessing.
    """
    args = args or {}
    missing: list[str] = []
    findings: list[dict] = []

    mode = str(args.get("mode") or "check").strip().lower()
    if mode not in MODES:
        raise ToolError(f"mode must be one of {', '.join(MODES)}")

    draft = args.get("draft")
    draft_path = args.get("draft_path")
    if mode == "reply" and (draft is None or str(draft).strip() == "") and not draft_path:
        draft = args.get("reply_text")
        if draft is None or str(draft).strip() == "":
            missing.append("reply_text: not given")
            draft = ""
    if (draft is None or str(draft).strip() == "") and not draft_path:
        if mode != "price_anchor":
            raise ToolError("give draft (text) or draft_path (a workspace file)")
        draft = ""
    if draft_path:
        path = tools.resolve(str(draft_path))
        if not path.is_file():
            raise ToolError(f"draft_path not found: {draft_path}")
        raw = path.read_text(encoding="utf-8", errors="replace")
        draft_blocks = _blocks(raw, path.suffix.lower() in HTML_SUFFIXES)
    else:
        raw = str(draft)
        draft_blocks = _blocks(raw, _looks_like_html(raw))
    language = str(args.get("language") or "").strip().lower() or _detect_language(raw + " ".join(str(p) for p in (args.get("criticism_points") or [])))
    if language not in {"lt", "en"}:
        language = "en"

    sources = _list(args.get("sources"), "sources")
    evidence: str | None = None
    if mode == "price_anchor":
        evidence = str(args.get("evidence") or "").strip() or None
        if evidence and evidence not in sources:
            sources.append(evidence)
    if mode == "reply":
        for fact in args.get("facts") or []:
            src = str(fact.get("source") or "").strip() if isinstance(fact, dict) else ""
            if src and src not in sources:
                sources.append(src)
    facts_path = str(args.get("facts_path") or FACTS_PATH).strip()
    facts_given = bool(args.get("facts_path"))
    include_facts = _flag(args.get("include_facts"), True)
    if include_facts and facts_path not in sources:
        facts_file = tools.resolve(facts_path)
        if facts_file.is_file():
            sources.insert(0, facts_path)
        elif facts_given:
            missing.append(f"{facts_path}: not found")
    if not sources:
        if mode == "check":
            raise ToolError(f"nothing to check against: give sources or write {FACTS_PATH}")
        missing.append(f"sources: none readable (give sources or write {FACTS_PATH})")

    passages, sources_read = _load_sources(tools, sources, fetch, language, missing)

    quote_sources = _list(args.get("quote_sources"), "quote_sources") if "quote_sources" in args else None
    quote_passages: list[Passage] = []
    quote_reason: str | None = None
    if quote_sources is not None:
        if not quote_sources:
            quote_reason = "no interview sources given (quote_sources is empty)"
            missing.append("quote_sources: none given; quotes cannot be verified")
        else:
            quote_passages, quote_read = _load_sources(tools, quote_sources, fetch, language, missing)
            sources_read.extend({**r, "role": "quotes"} for r in quote_read)
            if not quote_passages:
                quote_reason = "no interview source could be read"

    own_domains = [d for d in (_domain(d) if _is_url(d) else d.lower().strip().removeprefix("www.") for d in _list(args.get("own_domains"), "own_domains")) if d] if args.get("own_domains") is not None else None
    if "own_domains" in args and not own_domains:
        missing.append("own_domains: none given; every source party stays unknown")
    check_scope = _flag(args.get("check_scope"), False)

    if not any(_WORD.search(text) for text, _, _ in draft_blocks):
        missing.append("draft has no text")
    draft_claims = _claims(draft_blocks, language)
    judged: list[dict] = []
    for claim in draft_claims:
        pool = passages + quote_passages if claim.kind in {"number", "date"} else passages
        out = _judge(claim, pool, own_domains)
        hit = next((p for p in pool if out.get("source") == p.source and out.get("location") == p.location()), None) if out.get("source") else None
        if check_scope and out["status"] == "supported" and hit is not None:
            _scope(claim, out, hit.text, findings)
        if own_domains is not None and out["status"] in {"supported", "supported_scope_narrower", "number_differs"}:
            _party(out, claim, hit, own_domains, findings)
        if hit is not None:
            _trends(out, claim, hit, findings)
        if claim.kind == "number" and _OPERATIONAL.search(claim.text):
            out["operational"] = True
        judged.append(out)
    if quote_sources is not None:
        for claim in _quote_claims(draft_blocks):
            out = _judge_quote(claim, quote_passages, quote_reason)
            if out.get("attribution") == "attribution_unverified":
                findings.append({"code": "attribution_unverified", "line": claim.line, "claim": claim.text,
                                 "detail": f"The quote is attributed to {claim.attributed_to}, but the name is not near the passage in {out['source']}."})
            judged.append(out)

    claims = judged
    counts = {
        "supported": sum(c["status"] == "supported" for c in claims),
        "number_differs": sum(c["status"] == "number_differs" for c in claims),
        "unsupported": sum(c["status"] == "unsupported" for c in claims),
        "total": len(claims),
    }
    if check_scope:
        counts["supported_scope_narrower"] = sum(c["status"] == "supported_scope_narrower" for c in claims)
    if quote_sources is not None:
        counts["altered"] = sum(c["status"] == "altered" for c in claims)

    result: dict[str, Any] = {"language": language, "mode": mode, "claims": claims, "counts": counts, "sources_read": sources_read, "missing": missing}

    if mode == "reply":
        result["reply"] = _reply(args, raw, passages, sources_read, findings, missing)
    if mode == "price_anchor":
        result["anchor"] = _price_anchor(args, raw, passages, evidence, language, findings, missing)
    if args.get("rumor"):
        result["rumor_corrections"] = _rumor(str(args["rumor"]), draft_claims, judged[: len(draft_claims)], passages, language)
    if _flag(args.get("self_consistency"), False):
        result["conflicts"] = _conflicts(draft_claims)
        for conflict in result["conflicts"]:
            findings.append({"code": "conflict", "detail": f"{conflict['subject']}: {conflict['values'][0]['value']} (line {conflict['values'][0]['line']}) vs {conflict['values'][1]['value']} (line {conflict['values'][1]['line']})."})
    if args.get("max_minutes") not in (None, ""):
        result["length"] = _length(" ".join(_plain(t) for t, _, _ in draft_blocks), args["max_minutes"], args.get("wpm"), findings)

    result["fact_sheet"], result["fact_sheet_table"] = _fact_sheet(claims)
    result["corrections"] = _corrections(claims, language)
    result["findings"] = findings
    result["summary"] = _summary(claims, counts, missing, findings)
    return result


def claim_check_tool(tools: WorkspaceTools, args: dict, fetch: Callable[[str], str] | None = None) -> str:
    """Backs the worker- and verifier-facing ``claim_check`` tool: JSON they can quote from."""
    return json.dumps(claim_check(tools, args or {}, fetch), ensure_ascii=False, indent=1)
