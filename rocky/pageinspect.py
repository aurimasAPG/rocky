"""Does the page the ad links to keep the ad's promise? Checked by code.

A worker writes an ad, an email or a post and links it to a page. The reader
clicks because of one promise, one price, one offer and one button; if the page
says something else, the click is wasted and the model's impression that
"it matches" is worth nothing. ``page_inspect`` parses the page with the
standard library, pulls out the elements a visitor meets first (title, H1,
first screen, buttons, prices, forms, language) and compares each of them with
the ad element by element. Every finding carries the exact copy from the ad
and the exact element from the page, so the fix is obvious and the verdict is
reproducible.

No network here: the page arrives as ``html``, as a workspace ``path`` or as a
``url`` the engine fetches through the ``fetch_html`` callable it passes in.
Without that callable a ``url`` is reported under ``missing``, never guessed.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any, Callable

from rocky.tools import ToolError, WorkspaceTools

FIRST_SCREEN_CHARS = 600
THIN_PAGE_WORDS = 150
MAX_H2 = 8
MAX_BUTTONS = 12
MAX_PRICES = 12
MIN_CONTENT_LETTERS = 4
# Lithuanian endings are 1–3 letters; stems match on their first five letters,
# or on a shorter common prefix when the words themselves are short.
STEM_LETTERS = 5

SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "head"})
VOID_TAGS = frozenset({"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr"})
BUTTON_CLASS_HINTS = ("btn", "button", "cta")
# A link whose first word is one of these reads as a call to action.
EN_CTA_VERBS = frozenset(
    {
        "get", "start", "book", "sign", "register", "buy", "order", "download", "try", "join", "learn", "request",
        "contact", "subscribe", "call", "claim", "apply", "schedule", "shop", "see", "discover", "reserve", "add",
        "create", "begin", "explore", "watch", "read", "talk", "ask", "find", "compare", "choose", "grab", "save",
        "send", "submit", "continue", "enroll", "unlock", "upgrade", "activate", "redeem", "view", "browse",
        "pirkti", "užsakyti",
    }
)
# Lithuanian verb forms that open a call to action: infinitive, imperative, reflexive.
LT_CTA_ENDINGS = ("ti", "tis", "k", "kis", "kite", "kitės", "kime", "kimės")

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_TOKEN = re.compile(r"\w+", re.UNICODE)
_NUMBER = r"\d+(?:[ .,]\d{3})*(?:[.,]\d{1,2})?"
_CURRENCY = r"(?:€|\$|£|(?:EUR|Eur|USD)(?![^\W\d_]))"
_PRICE = re.compile(rf"{_CURRENCY}\s?({_NUMBER})|({_NUMBER})\s?{_CURRENCY}")
_LT_LETTERS = set("ąčęėįšųūž")
_LT_MARKERS = frozenset(
    {
        "ir", "kad", "yra", "bus", "nuo", "iki", "arba", "kaip", "jūsų", "jums", "mūsų", "mes", "jūs", "tai", "kuris",
        "kuri", "kurie", "daugiau", "tik", "visi", "visos", "nes", "apie", "dėl", "kai", "taip", "dar", "jau", "prie",
        "pagal", "šiandien", "dabar", "nemokamai", "nemokama", "kaina", "registruotis", "verslui", "paslaugos",
    }
)
_EN_MARKERS = frozenset(
    {
        "the", "and", "you", "your", "with", "for", "are", "this", "that", "our", "from", "get", "free", "now", "more",
        "all", "will", "can", "how", "what", "start", "sign", "today", "price", "pricing", "learn", "about", "contact",
    }
)
# Function words that carry no promise; they never count as shared content.
_STOPWORDS = frozenset(
    {
        "your", "with", "that", "this", "from", "have", "more", "than", "they", "them", "their", "will", "what", "when",
        "where", "which", "into", "about", "only", "just", "also", "very", "over", "here", "there", "been", "being",
        "does", "make", "made", "like", "each", "every", "some", "most", "much", "many", "yours", "ours", "while",
        "jūsų", "jums", "mūsų", "kaip", "arba", "kuris", "kuri", "kurie", "kurios", "kurią", "kurį", "daugiau", "visi",
        "visos", "viską", "visą", "kiekvienas", "kiekvieną", "labai", "dabar", "šiandien", "tada", "todėl", "apie",
        "taip", "prie", "pagal", "savo", "savęs", "jūsų", "tavo", "tave", "jums", "mums", "tačiau", "arba",
    }
)
_LANGUAGE_NAMES = {
    "lt": ("lt", "lithuanian", "lietuvių", "lietuviškai", "lietuviu"),
    "en": ("en", "english", "anglų", "angliškai"),
}

LEVEL_ORDER = {"block": 0, "warn": 1, "info": 2}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ").replace(" ", " ")).strip()


class _PageParser(HTMLParser):
    """Collects the elements a visitor meets first. Tolerates the HTML real sites ship."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lang: str | None = None
        self.title_parts: list[str] = []
        self.meta_description: str | None = None
        self.h1: list[str] = []
        self.h2: list[str] = []
        self.buttons: list[str] = []
        self.body_parts: list[str] = []
        self.forms = 0
        self.input_types: list[str] = []
        self.images_total = 0
        self.images_with_alt = 0
        self._stack: list[tuple[str, list[str] | None, dict[str, str]]] = []
        self._skip = 0
        self._in_form = 0

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {k.lower(): (v or "") for k, v in attrs if k}

    def _note_input(self, kind: str) -> None:
        if self._in_form and kind not in self.input_types:
            self.input_types.append(kind)

    def _add_button(self, text: str) -> None:
        text = _clean(text)
        if text and text not in self.buttons:
            self.buttons.append(text)

    # -- parser callbacks -------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        a = self._attrs(attrs)
        if tag == "html" and a.get("lang"):
            self.lang = a["lang"].strip()
        if tag in VOID_TAGS:
            self.handle_startendtag(tag, attrs)
            return
        if tag in SKIP_TAGS:
            self._skip += 1
        if tag == "form":
            self.forms += 1
            self._in_form += 1
        if tag in ("textarea", "select"):
            self._note_input(tag)
        # Block boundaries separate words; a space is harmless inside inline runs.
        for _, buf, _ in self._stack:
            if buf is not None:
                buf.append(" ")
        if self._skip == 0:
            self.body_parts.append(" ")
        capture = [] if tag in ("title", "h1", "h2", "a", "button") else None
        self._stack.append((tag, capture, a))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        a = self._attrs(attrs)
        if tag == "meta":
            if a.get("name", "").lower() == "description" and a.get("content"):
                self.meta_description = _clean(a["content"])
        elif tag == "img":
            self.images_total += 1
            if _clean(a.get("alt", "")):
                self.images_with_alt += 1
        elif tag == "input":
            kind = (a.get("type") or "text").lower()
            self._note_input(kind)
            if kind in ("submit", "button", "image"):
                label = a.get("value") or a.get("aria-label") or a.get("alt") or ""
                self._add_button(label or ("Submit" if kind == "submit" else ""))
        elif tag == "br":
            self.body_parts.append(" ")
            for _, buf, _ in self._stack:
                if buf is not None:
                    buf.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in VOID_TAGS:
            return
        if not any(t == tag for t, _, _ in self._stack):
            return  # a stray close tag: ignore it rather than losing the page
        while self._stack:
            name, buf, attrs = self._stack.pop()
            self._close(name, buf, attrs)
            if name == tag:
                break

    def _close(self, name: str, buf: list[str] | None, attrs: dict[str, str]) -> None:
        if name in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        if name == "form":
            self._in_form = max(0, self._in_form - 1)
        if buf is None:
            return
        text = _clean("".join(buf))
        if name == "title":
            if text:
                self.title_parts.append(text)
        elif name == "h1":
            if text:
                self.h1.append(text)
        elif name == "h2":
            if text:
                self.h2.append(text)
        elif name == "button":
            self._add_button(text or attrs.get("aria-label", ""))
        elif name == "a":
            label = text or attrs.get("aria-label", "")
            if _looks_like_button(attrs, label):
                self._add_button(label)

    def handle_data(self, data: str) -> None:
        if not data:
            return
        for _, buf, _ in self._stack:
            if buf is not None:
                buf.append(data)
        in_title = any(t == "title" for t, _, _ in self._stack)
        if self._skip == 0 and not in_title:
            self.body_parts.append(data)

    def close(self) -> None:  # noqa: D401 - HTMLParser API
        super().close()
        while self._stack:
            name, buf, attrs = self._stack.pop()
            self._close(name, buf, attrs)

    # -- result -----------------------------------------------------------
    def page(self) -> dict[str, Any]:
        body = _clean(" ".join(self.body_parts))
        return {
            "title": _clean(" ".join(self.title_parts)) or None,
            "meta_description": self.meta_description,
            "lang": self.lang,
            "h1": self.h1,
            "h2": self.h2,
            "buttons": self.buttons,
            "body_text": body,
            "first_screen_text": body[:FIRST_SCREEN_CHARS],
            "word_count": len(_TOKEN.findall(body)),
            "forms": {"count": self.forms, "inputs": self.input_types},
            "images_total": self.images_total,
            "images_with_alt": self.images_with_alt,
        }


def _looks_like_button(attrs: dict[str, str], text: str) -> bool:
    hint = f"{attrs.get('class', '')} {attrs.get('role', '')} {attrs.get('id', '')}".lower()
    if any(h in hint for h in BUTTON_CLASS_HINTS):
        return bool(text)
    text = _clean(text)
    if not text or len(text) > 40:
        return False
    words = _WORD.findall(text.lower())
    if not words or len(words) > 6:
        return False
    first = words[0]
    if first in EN_CTA_VERBS:
        return True
    return len(first) >= 4 and first.endswith(LT_CTA_ENDINGS)


def extract_page(html: str) -> dict[str, Any]:
    """Title, H1s, buttons, prices and the rest of what a visitor meets first."""
    parser = _PageParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception as exc:  # pragma: no cover - HTMLParser rarely raises, but a page must never crash the tool
        raise ToolError(f"the page could not be parsed: {exc}") from exc
    page = parser.page()
    page["prices"] = find_prices(page["body_text"])
    return page


# --- prices ---------------------------------------------------------------


def parse_amount(token: str) -> float | None:
    """'1 200,50', '1,200.50', '1.200,50', '1200' and '49,99' all become one number."""
    s = re.sub(r"\s", "", str(token or ""))
    s = s.replace("\xa0", "")
    if not s:
        return None
    if "," in s and "." in s:
        dec = max(s.rfind(","), s.rfind("."))
        s = s[:dec].replace(",", "").replace(".", "") + "." + s[dec + 1:]
    elif "," in s or "." in s:
        sep = "," if "," in s else "."
        parts = s.split(sep)
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3):
            s = "".join(parts)
        else:
            s = parts[0] + "." + parts[1]
    try:
        return float(s)
    except ValueError:
        return None


def find_prices(text: str) -> list[dict[str, Any]]:
    """Every currency amount in the text with the words around it, in page order."""
    text = _clean(text)
    out: list[dict[str, Any]] = []
    seen: set[tuple[float, str]] = set()
    for m in _PRICE.finditer(text):
        token = m.group(0)
        value = parse_amount(m.group(1) or m.group(2) or "")
        if value is None:
            continue
        key = (value, token)
        if key in seen:
            continue
        seen.add(key)
        start = max(0, m.start() - 30)
        end = min(len(text), m.end() + 30)
        context = text[start:end].strip()
        if start > 0:
            context = "…" + context
        if end < len(text):
            context = context + "…"
        out.append({"text": token, "value": value, "context": context})
    return out


def _ad_price(ad: dict[str, Any]) -> tuple[str | None, str | None, float | None]:
    """(field, exact text, number) of the price the ad names, or Nones."""
    raw = ad.get("price")
    if raw not in (None, ""):
        text = _clean(str(raw))
        prices = find_prices(text)
        if prices:
            return "price", prices[0]["text"], prices[0]["value"]
        value = parse_amount(re.sub(r"[^\d ,.]", "", text)) if re.search(r"\d", text) else None
        if value is not None:
            return "price", text, value
    for field in ("headline", "primary_text", "description", "offer"):
        prices = find_prices(str(ad.get(field) or ""))
        if prices:
            return field, prices[0]["text"], prices[0]["value"]
    return None, None, None


# --- words ----------------------------------------------------------------


def content_words(text: str) -> list[str]:
    """Distinct words of four letters or more that carry meaning, case-folded, in order."""
    out: list[str] = []
    for w in _WORD.findall(str(text or "").casefold()):
        if len(w) >= MIN_CONTENT_LETTERS and w not in _STOPWORDS and w not in out:
            out.append(w)
    return out


def same_stem(a: str, b: str) -> bool:
    """'kaina' ~ 'kainos', 'registruotis' ~ 'registruokis', 'price' ~ 'prices'."""
    a, b = a.casefold(), b.casefold()
    if a == b:
        return True
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    shorter = min(len(a), len(b))
    return n >= STEM_LETTERS or (n >= MIN_CONTENT_LETTERS and n >= shorter - 2)


def shared_words(ad_words: list[str], text: str) -> list[str]:
    page_words = set(_WORD.findall(str(text or "").casefold()))
    return [w for w in ad_words if any(same_stem(w, p) for p in page_words)]


def detect_language(text: str) -> str | None:
    """'lt', 'en', or None when the text gives no signal. Diacritics weigh most."""
    words = _WORD.findall(str(text or "").lower())
    if not words:
        return None
    diacritics = sum(1 for w in words if any(ch in _LT_LETTERS for ch in w))
    lt = sum(1 for w in words if w in _LT_MARKERS) + 2 * diacritics
    en = sum(1 for w in words if w in _EN_MARKERS)
    if lt == en:
        return None
    return "lt" if lt > en else "en"


def _normalise_language(value: object) -> str | None:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    for code, names in _LANGUAGE_NAMES.items():
        if raw in names or raw.startswith(code + "-") or raw.startswith(code + "_"):
            return code
    return raw[:2] if len(raw) >= 2 and raw[:2].isalpha() else None


# --- the inspection ---------------------------------------------------------


def _finding(code: str, level: str, ad_field: str | None, ad_value: Any, page_element: str | None, page_value: Any, note: str) -> dict[str, Any]:
    return {
        "code": code,
        "level": level,
        "ad_field": ad_field,
        "ad_value": ad_value,
        "page_element": page_element,
        "page_value": page_value,
        "note": note,
    }


def _ad_text(ad: dict[str, Any], *fields: str) -> str:
    return " ".join(_clean(str(ad.get(f) or "")) for f in fields if ad.get(f)).strip()


def _load_page(tools: WorkspaceTools | None, args: dict, fetch_html: Callable[[str], str] | None) -> tuple[str | None, str, list[str]]:
    """(html or None, source label, missing notes). Exactly one source may be given."""
    html = args.get("html")
    path = args.get("path")
    url = args.get("url")
    given = [name for name, value in (("html", html), ("path", path), ("url", url)) if value not in (None, "")]
    if not given:
        raise ToolError("no page source: give the page as html, as a workspace path, or as a url")
    if len(given) > 1:
        raise ToolError(f"give one page source, not {', '.join(given)}")
    if html not in (None, ""):
        return str(html), "html", []
    if path not in (None, ""):
        if tools is None:
            raise ToolError("a path needs a workspace")
        target = tools.resolve(str(path))
        if not target.is_file():
            raise ToolError(f"path missing: {path}")
        return target.read_text(encoding="utf-8", errors="replace"), str(path), []
    url = str(url).strip()
    if fetch_html is None:
        return None, url, [f"page: {url} not fetched (no fetcher here); give the page as html or path, or open it with the browser tools"]
    try:
        fetched = fetch_html(url)
    except Exception as exc:
        raise ToolError(f"could not fetch {url}: {exc}") from exc
    if isinstance(fetched, bytes):
        fetched = fetched.decode("utf-8", errors="replace")
    return str(fetched or ""), url, []


def page_inspect(tools: WorkspaceTools | None, args: dict, fetch_html: Callable[[str], str] | None = None) -> dict[str, Any]:
    """Compare an ad with its destination page element by element.

    ``args.ad``: headline, primary_text, description, cta, audience, price,
    offer, language (headline or primary_text required). Page: ``html``,
    ``path`` (workspace file) or ``url`` (fetched through ``fetch_html``).
    Returns the extracted page, findings ordered block → warn → info with the
    exact ad copy and page element behind each, counts, what is missing, and
    a one-sentence summary.
    """
    args = args or {}
    ad = args.get("ad")
    if ad is None and any(args.get(k) for k in ("headline", "primary_text")):
        ad = {k: args.get(k) for k in ("headline", "primary_text", "description", "cta", "audience", "price", "offer", "language")}
    if not isinstance(ad, dict):
        raise ToolError("the ad has no text: give ad.headline or ad.primary_text")
    ad = {k: (v if v is None else str(v)) for k, v in ad.items()}
    headline = _clean(ad.get("headline") or "")
    primary = _clean(ad.get("primary_text") or "")
    if not headline and not primary:
        raise ToolError("the ad has no text: give ad.headline or ad.primary_text")

    html, source, missing = _load_page(tools, args, fetch_html)
    if html is None:
        return {
            "page": {"source": source, "fetched": False},
            "findings": [],
            "counts": {"block": 0, "warn": 0, "info": 0},
            "missing": missing,
            "summary": f"The page at {source} was not fetched, so nothing was compared; give its HTML or path.",
        }
    if not html.strip():
        raise ToolError("the page is empty")

    page = extract_page(html)
    findings: list[dict[str, Any]] = []
    clauses: list[str] = []

    # 1. The promise: does the page restate the ad's headline above the fold?
    promise_field = "headline" if headline else "primary_text"
    promise_text = headline or primary
    ad_words = content_words(f"{headline} {primary}")
    if not ad_words:
        missing.append("promise: the ad copy has no content words of four letters or more to compare")
    else:
        need = 2 if len(ad_words) >= 2 else 1
        candidates: list[tuple[str, str]] = [("h1", h) for h in page["h1"]]
        if page["title"]:
            candidates.append(("title", page["title"]))
        if page["first_screen_text"]:
            candidates.append(("first_screen_text", page["first_screen_text"]))
        best: tuple[str, str, list[str]] | None = None
        for element, text in candidates:
            shared = shared_words(ad_words, text)
            if len(shared) >= need and (best is None or len(shared) > len(best[2])):
                best = (element, text, shared)
        if best:
            element, text, shared = best
            findings.append(_finding(
                "promise_match", "info", promise_field, promise_text, element, text,
                f"Shared words: {', '.join(shared)}.",
            ))
            clauses.append(f"{promise_field.replace('_', ' ')} promise found in {_element_name(element)}")
        else:
            shown = page["h1"][0] if page["h1"] else (page["title"] or page["first_screen_text"][:120] or None)
            findings.append(_finding(
                "promise_missing", "warn", promise_field, promise_text,
                "h1" if page["h1"] else ("title" if page["title"] else "first_screen_text"), shown,
                "The H1, title and first screen share fewer than two content words with the ad; the reader does not "
                "see the promise they clicked on. Put the ad's promise in the H1.",
            ))
            clauses.append("ad promise not restated in the H1, title or first screen (warn)")

    # 2. The price.
    price_field, price_text, price_value = _ad_price(ad)
    if price_value is not None:
        page_prices = page["prices"]
        if not page_prices:
            findings.append(_finding(
                "price_absent", "warn", price_field, price_text, "prices", None,
                "The ad names a price; the page shows none. A visitor who clicked on the price cannot confirm it.",
            ))
            clauses.append(f"price {price_text} in ad, none on page (warn)")
        else:
            hit = next((p for p in page_prices if abs(p["value"] - price_value) < 0.005), None)
            if hit:
                findings.append(_finding(
                    "price_match", "info", price_field, price_text, "prices", hit["text"],
                    f"Page: “{hit['context']}”.",
                ))
                clauses.append(f"price {price_text} found on page")
            else:
                closest = min(page_prices, key=lambda p: abs(p["value"] - price_value))
                others = [p["text"] for p in page_prices if p is not closest]
                note = f"The ad says {price_text}; the page shows {closest['text']} (“{closest['context']}”)."
                if others:
                    note += f" Other prices on the page: {', '.join(others[:6])}."
                note += " Fix the ad or the page before this runs."
                findings.append(_finding("price_mismatch", "block", price_field, price_text, "prices", closest["text"], note))
                clauses.append(f"price {price_text} in ad, page shows {closest['text']} (block)")

    # 3. The call to action.
    cta = _clean(ad.get("cta") or "")
    has_action = bool(page["buttons"]) or page["forms"]["count"] > 0
    if not has_action:
        findings.append(_finding(
            "no_cta_on_page", "block", "cta", cta or None, "buttons", [],
            "The page has no button, link that acts as a button, or form. The visitor has nothing to do next.",
        ))
        clauses.append("page has no button or form (block)")
    elif cta:
        cta_words = [w for w in _WORD.findall(cta.casefold()) if len(w) >= 3]
        match = None
        for button in page["buttons"]:
            if cta.casefold() in button.casefold():
                match = button
                break
            if cta_words and shared_words(cta_words, button):
                match = button
                break
        if match:
            findings.append(_finding("cta_match", "info", "cta", cta, "button", match, "The page button repeats the ad's call to action."))
            clauses.append(f"CTA '{cta}' matches button '{match}'")
        else:
            shown = page["buttons"][:MAX_BUTTONS]
            note = (
                f"No page button shares a word with the ad's call to action. Page buttons: {', '.join(shown)}."
                if shown else "The page has a form but no button text to compare."
            )
            findings.append(_finding("cta_mismatch", "warn", "cta", cta, "buttons", shown, note))
            clauses.append(f"CTA '{cta}' matches no page button (warn)")

    # 4. The audience named in the ad.
    audience = _clean(ad.get("audience") or "")
    if audience:
        words = content_words(audience)
        if words:
            found = shared_words(words, page["body_text"] + " " + (page["title"] or ""))
            if found:
                findings.append(_finding("audience_match", "info", "audience", audience, "body", None, f"Words found on the page: {', '.join(found)}."))
            else:
                findings.append(_finding(
                    "audience_missing", "warn", "audience", audience, "body", None,
                    f"None of these words appear anywhere on the page: {', '.join(words)}. The reader cannot tell the page is for them.",
                ))
                clauses.append("audience words absent from page (warn)")

    # 5. Language.
    ad_lang = _normalise_language(ad.get("language")) or detect_language(_ad_text(ad, "headline", "primary_text", "description", "cta"))
    page_attr = _normalise_language(page["lang"])
    page_detected = detect_language(page["body_text"])
    page["lang_detected"] = page_detected
    if ad_lang is None:
        missing.append("language: not given in the ad and not detected from its copy")
    else:
        mismatch_with = None
        if page_attr and page_attr != ad_lang:
            mismatch_with = ("html[lang]", page["lang"], page_attr)
        elif page_detected and page_detected != ad_lang:
            mismatch_with = ("body", page["first_screen_text"][:120], page_detected)
        if mismatch_with:
            element, value, code = mismatch_with
            findings.append(_finding(
                "language_mismatch", "warn", "language", ad_lang, element, value,
                f"The ad reads as '{ad_lang}', the page as '{code}'. The reader lands in another language.",
            ))
            clauses.append(f"ad in {ad_lang}, page in {code} (warn)")

    # 6. The offer.
    offer = _clean(ad.get("offer") or "")
    if offer:
        offer_words = [w for w in _TOKEN.findall(offer.casefold()) if len(w) >= 3 or w.isdigit()]
        offer_words = [w for w in offer_words if w not in _STOPWORDS]
        if offer_words:
            found = shared_words(offer_words, page["body_text"])
            absent = [w for w in offer_words if w not in found]
            if not absent:
                findings.append(_finding("offer_match", "info", "offer", offer, "body", None, f"Offer words found on the page: {', '.join(found)}."))
            else:
                findings.append(_finding(
                    "offer_missing", "warn", "offer", offer, "body", None,
                    f"Not found on the page: {', '.join(absent)}." + (f" Found: {', '.join(found)}." if found else "")
                    + " The visitor cannot find the offer they clicked for.",
                ))
                clauses.append(f"offer '{offer}' not on page (warn)")

    # 7. The page itself.
    if page["word_count"] < THIN_PAGE_WORDS:
        findings.append(_finding(
            "thin_page", "warn", None, None, "body", page["word_count"],
            f"{page['word_count']} words of visible text (under {THIN_PAGE_WORDS}). Little here to confirm the promise or answer objections.",
        ))
        clauses.append(f"page has {page['word_count']} words (warn)")
    if not page["h1"]:
        findings.append(_finding("no_h1", "warn", None, None, "h1", None, "The page has no H1, so nothing restates the promise at the top."))
        clauses.append("no H1 (warn)")

    findings.sort(key=lambda f: LEVEL_ORDER.get(f["level"], 9))
    counts = {level: sum(1 for f in findings if f["level"] == level) for level in ("block", "warn", "info")}
    summary = "; ".join(clauses) if clauses else "Nothing to compare: the ad names no price, call to action, audience or offer and the page restates nothing."
    summary = summary[0].upper() + summary[1:] + "."

    return {
        "page": {
            "source": source,
            "title": page["title"],
            "meta_description": page["meta_description"],
            "lang": page["lang"],
            "lang_detected": page_detected,
            "h1": page["h1"],
            "h2": page["h2"][:MAX_H2],
            "buttons": page["buttons"][:MAX_BUTTONS],
            "prices": page["prices"][:MAX_PRICES],
            "forms": page["forms"],
            "word_count": page["word_count"],
            "images_total": page["images_total"],
            "images_with_alt": page["images_with_alt"],
            "first_screen_text": page["first_screen_text"],
        },
        "findings": findings,
        "counts": counts,
        "missing": missing,
        "summary": summary,
    }


def _element_name(element: str) -> str:
    return {"h1": "H1", "title": "title", "first_screen_text": "first screen"}.get(element, element)


def page_inspect_tool(tools: WorkspaceTools | None, args: dict, fetch_html: Callable[[str], str] | None = None) -> str:
    """Backs the worker-facing ``page_inspect`` tool: JSON the worker can quote from."""
    return json.dumps(page_inspect(tools, args or {}, fetch_html), ensure_ascii=False, indent=1)
