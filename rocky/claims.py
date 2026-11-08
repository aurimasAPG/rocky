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

_SPACES = "       　"
_NUM = re.compile(r"(?<![\d.,:])(?P<int>\d{1,3}(?: \d{3}(?!\d))+|\d+)(?P<frac>(?:[.,]\d+)+)?(?P<plus>\+)?(?!:\d)")
_DATE = re.compile(r"\b(?P<y1>\d{4})-(?P<m1>\d{2})-(?P<d1>\d{2})\b|\b(?P<d2>\d{1,2})\.(?P<m2>\d{1,2})\.(?P<y2>\d{4})\b|\b(?P<y3>\d{4})\.(?P<m3>\d{2})\.(?P<d3>\d{2})\b")
_URL = re.compile(r"https?://\S+")
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

STOPWORDS = {
    "that", "this", "with", "from", "have", "more", "than", "their", "there", "which", "were", "been", "also",
    "about", "into", "over", "your", "they", "them", "what", "when", "will", "would", "each", "every", "some",
    "such", "very", "while", "where", "these", "those", "because", "after", "before", "within", "through",
    "kuri", "kurie", "kuris", "kurią", "kurių", "kurio", "kurios", "mūsų", "jūsų", "buvo", "taip", "labai",
    "arba", "kaip", "todėl", "tačiau", "visi", "visų", "visus", "visas", "visos", "daugiau", "mažiau", "šiuo",
    "šiais", "šiais", "savo", "bent", "tiek", "kiek", "apie", "prie", "tarp", "kartu", "jeigu", "nors", "taigi",
    "dabar", "tada", "čia", "turi", "turime", "turėti", "gali", "galima", "būti", "būtų", "esame", "esate",
}


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
    "aside", "main", "body", "html", "head", "form", "fieldset", "legend", "address", "summary", "details",
}
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}


class _Blocks(HTMLParser):
    """Collects visible text block by block with the line and column where each block starts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, int, int]] = []
        self._buf: list[str] = []
        self._pos: tuple[int, int] | None = None
        self._skip = 0

    def _flush(self) -> None:
        text = _squash(" ".join(self._buf))
        if text and self._pos is not None:
            self.blocks.append((text, self._pos[0], self._pos[1]))
        self._buf, self._pos = [], None

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        if tag in _BLOCK_TAGS:
            self._flush()

    def handle_startendtag(self, tag: str, attrs: Any) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        if tag in _BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip or not data.strip():
            return
        if self._pos is None:
            self._pos = self.getpos()
        self._buf.append(data)

    def close(self) -> None:
        super().close()
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
    text = _URL.sub(" ", _squash(text))
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


# --- claims out of the draft -------------------------------------------------------


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if _WORD.search(s)]


def _claims(blocks: list[tuple[str, int, int]], language: str) -> list[Claim]:
    claims: list[Claim] = []
    for text, line, _offset in blocks:
        for sentence in _sentences(_plain(text)):
            numbers = [n for n in _numbers(sentence, language) if _measurable(n)]
            families = _families(sentence)
            superlative = [f for f in families if f in SUPERLATIVES]
            credential = [f for f in families if f in CREDENTIALS]
            if numbers:
                kind = "number" if any(n.cls not in {"year", "date"} for n in numbers) else "date"
                stems = _stems(sentence)
            elif superlative:
                kind, stems = "superlative", _stems(sentence, [s for f in superlative for s in families[f]])
            elif credential:
                kind, stems = "credential", _stems(sentence, [s for f in credential for s in families[f]])
            else:
                continue
            claims.append(Claim(sentence, line, kind, numbers, set(families), stems))
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
    if common and shared:
        return "supported", (3, len(shared), 0.0, -passage.index), {"shared": shared, "families": common}
    if len(shared) >= MIN_SHARED:
        return "none", (1, len(shared), 0.0, -passage.index), {"shared": shared}
    return "none", (0, len(shared), 0.0, -passage.index), {"shared": shared}


def _clip(text: str, limit: int = PASSAGE_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _number_out(n: Number) -> dict[str, Any]:
    return {"raw": n.raw, "value": n.value, "unit": n.unit(), "context": n.context or None}


def _judge(claim: Claim, passages: list[Passage]) -> dict[str, Any]:
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
        out["note"] = f"No passage carries {what}; the closest is {where}, which shares the words {', '.join(detail['shared'])} but not the claim."
    else:
        out["note"] = (
            "No source passage shares two content words and the number with this sentence."
            if claim.kind in {"number", "date"}
            else f"No source passage makes a '{', '.join(sorted(claim.families))}' claim about the same thing."
        )
    return out


def _short(text: str, limit: int = 48) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _summary(claims: list[dict], counts: dict[str, int], missing: list[str]) -> str:
    if counts["total"] == 0:
        head = "No measurable claims found in the draft"
        return head + (f" ({missing[0]})." if missing else ".")
    parts = [f"{counts['total']} claim{'s' if counts['total'] != 1 else ''}: {counts['supported']} supported"]
    differs = [c for c in claims if c["status"] == "number_differs"]
    if differs:
        bits = []
        for c in differs[:3]:
            d = c["differs"][0]
            bits.append(f"{d['claim']} vs {d['source']}" + (f" {d['context']}" if d.get("context") else ""))
        more = f", +{len(differs) - 3} more" if len(differs) > 3 else ""
        parts.append(f"{len(differs)} number differs ({'; '.join(bits)}{more} — review)")
    unsupported = [c for c in claims if c["status"] == "unsupported"]
    if unsupported:
        quoted = ", ".join(f"'{_short(c['text'])}'" for c in unsupported[:3])
        more = f", +{len(unsupported) - 3} more" if len(unsupported) > 3 else ""
        parts.append(f"{len(unsupported)} unsupported ({quoted}{more})")
    return ", ".join(parts) + "."


# --- the tool ------------------------------------------------------------------


def _flag(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"false", "0", "no", "off"}


def _detect_language(text: str) -> str:
    return "lt" if re.search(r"[ąčęėįšųūžĄČĘĖĮŠŲŪŽ]", text) else "en"


def claim_check(tools: WorkspaceTools, args: dict, fetch: Callable[[str], str] | None = None) -> dict[str, Any]:
    """Every measurable claim in a draft, matched to the passage that supports it.

    ``draft`` (text) or ``draft_path`` (workspace .md/.txt/.html). ``sources``:
    workspace paths and/or http(s) URLs; ``facts_path`` (default
    ``runs/notes/facts.md``) is added when it exists unless ``include_facts`` is
    false. ``language`` ``lt``/``en`` only decides how an ambiguous separator
    such as 12,400 is read; both readings are kept for matching anyway. URLs are
    read only through ``fetch`` and otherwise reported under ``missing``.
    """
    args = args or {}
    missing: list[str] = []

    draft = args.get("draft")
    draft_path = args.get("draft_path")
    if (draft is None or str(draft).strip() == "") and not draft_path:
        raise ToolError("give draft (text) or draft_path (a workspace file)")
    if draft_path:
        path = tools.resolve(str(draft_path))
        if not path.is_file():
            raise ToolError(f"draft_path not found: {draft_path}")
        raw = path.read_text(encoding="utf-8", errors="replace")
        draft_blocks = _blocks(raw, path.suffix.lower() in HTML_SUFFIXES)
    else:
        raw = str(draft)
        draft_blocks = _blocks(raw, _looks_like_html(raw))
    language = str(args.get("language") or "").strip().lower() or _detect_language(raw)
    if language not in {"lt", "en"}:
        language = "en"

    sources_arg = args.get("sources")
    if sources_arg is None or sources_arg == "":
        sources: list[str] = []
    elif isinstance(sources_arg, str):
        sources = [sources_arg]
    elif isinstance(sources_arg, (list, tuple)):
        sources = [str(s).strip() for s in sources_arg if str(s).strip()]
    else:
        raise ToolError("sources must be a list of workspace paths or URLs")
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
        raise ToolError(f"nothing to check against: give sources or write {FACTS_PATH}")

    passages: list[Passage] = []
    sources_read: list[dict[str, Any]] = []
    for source in sources:
        text, html, by_chars, reason = _read_source(tools, source, fetch)
        if text is None:
            missing.append(f"{source}: {reason}")
            continue
        found = _passages(source, text, html, by_chars, language)
        sources_read.append({"source": source, "passages": len(found), "chars": len(text)})
        passages.extend(found)

    if not any(_WORD.search(text) for text, _, _ in draft_blocks):
        missing.append("draft has no text")
    claims = [_judge(c, passages) for c in _claims(draft_blocks, language)]
    counts = {
        "supported": sum(c["status"] == "supported" for c in claims),
        "number_differs": sum(c["status"] == "number_differs" for c in claims),
        "unsupported": sum(c["status"] == "unsupported" for c in claims),
        "total": len(claims),
    }
    return {
        "language": language,
        "claims": claims,
        "counts": counts,
        "sources_read": sources_read,
        "missing": missing,
        "summary": _summary(claims, counts, missing),
    }


def claim_check_tool(tools: WorkspaceTools, args: dict, fetch: Callable[[str], str] | None = None) -> str:
    """Backs the worker- and verifier-facing ``claim_check`` tool: JSON they can quote from."""
    return json.dumps(claim_check(tools, args or {}, fetch), ensure_ascii=False, indent=1)
