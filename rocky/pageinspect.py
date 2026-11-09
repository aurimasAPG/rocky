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

The same parse feeds optional checks a marketer asks for by name: does the
page answer the search ``query`` and fit the search ``intent``; who runs it
(``credibility``); do its internal links work (``links``, through a
``fetch_meta`` callable); which pressure cues sit on it (``trust``); is the
price ladder logical (``price_ladder``); what a crawler sees versus a browser
(``required_facts`` + ``rendered_text``); which trackers load before consent
(``data_use``); does a transactional email carry its fields (``kind``);
structured data, UTM tags, navigation conventions, promise ledger, touchpoint
branding. Each check runs only when asked, reports ``missing`` when its input
is absent, and never needs a browser.

No network here: the page arrives as ``html``, as a workspace ``path`` or as a
``url`` the engine fetches through the ``fetch_html`` callable it passes in.
Link statuses come through ``fetch_meta(url) -> {status, final_url, headers}``.
Without a callable the check is reported under ``missing``, never guessed.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import parse_qsl, urljoin, urlparse

from rocky.tools import ToolError, WorkspaceTools

FIRST_SCREEN_CHARS = 600
FIRST_SCREEN_RAW_CHARS = 1500
THIN_PAGE_WORDS = 150
GUIDE_WORDS = 600
MAX_H2 = 8
MAX_BUTTONS = 12
MAX_PRICES = 12
MAX_LINKS = 30
MAX_ELEMENTS = 40
HEAVY_HTML_BYTES = 300_000
HEAVY_IMAGES = 15
MIN_CONTENT_LETTERS = 4
# Lithuanian endings are 1–3 letters; stems match on their first five letters,
# or on a shorter common prefix when the words themselves are short.
STEM_LETTERS = 5

SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "head"})
VOID_TAGS = frozenset({"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr"})
CAPTURE_TAGS = frozenset({"title", "h1", "h2", "h3", "h4", "a", "button", "li", "label", "option", "script", "form", "footer", "th", "td", "p"})
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
_PRICE = re.compile(rf"{_CURRENCY}\s?({_NUMBER})|(?<![^\W\d_])({_NUMBER})\s?{_CURRENCY}")
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

# --- vocabularies for the optional checks ----------------------------------------

INTENTS = ("informational", "commercial", "transactional", "navigational", "local")
CHECKS = (
    "credibility", "links", "trust", "price_ladder", "crawler", "data_use", "images_off", "schema", "navigation",
    "promises", "trust_signals", "touchpoint", "listing", "top_tasks", "mobile",
)
_CHECK_ALIASES = {"privacy": "data_use", "choice": "trust", "dark_patterns": "trust", "ladder": "price_ladder", "seo": "crawler", "structured_data": "schema", "nav": "navigation"}
_INTENT_WORDS = {
    "transactional": ("kaina", "kainos", "pirkti", "pirkite", "užsakyti", "užsisakyti", "nusipirkti", "įsigyti", "price", "pricing", "buy", "order", "purchase", "cheap", "pigiai", "akcija", "nuolaida", "discount", "coupon", "kuponas", "cost", "kainuoja", "prenumerata", "subscribe"),
    "commercial": ("geriausi", "geriausia", "geriausias", "best", "top", "palyginimas", "palyginti", "compare", "comparison", "vs", "review", "reviews", "atsiliepimai", "apžvalga", "alternatyva", "alternative", "alternatives", "which", "kuris", "kurią", "kokį", "kokią"),
    "informational": ("kaip", "how", "kas yra", "what is", "kodėl", "why", "kada", "when", "guide", "gidas", "patarimai", "tips", "tutorial", "pavyzdys", "example", "examples", "reikšmė", "meaning", "definition", "apibrėžimas", "instrukcija"),
    "navigational": ("prisijungti", "prisijungimas", "login", "log in", "sign in", "savitarna", "app", "programėlė", "kontaktai", "contact", "svetainė", "website"),
    "local": ("netoliese", "near me", "šalia", "nearby", "vilniuje", "kaune", "klaipėdoje", "šiauliuose", "panevėžyje", "alytuje"),
}
_PLACE_STEMS = (
    "vilni", "kaun", "klaipėd", "klaiped", "šiauli", "siauli", "panevėž", "panevez", "alyt", "marijampol", "uten", "mažeiki", "mazeiki",
    "jonav", "kėdaini", "kedaini", "telši", "telsi", "taurag", "ukmerg", "visagin", "palang", "plung", "kretin", "šilut", "silut",
    "radvilišk", "radvilisk", "druskinink", "rokišk", "rokisk", "birž", "elektrėn", "garliav", "jurbark", "vilkavišk", "raseini",
    "anykšči", "anyksci", "lentvar", "prien", "joniški", "kelm", "varėn", "kaišiadori", "pasvaly", "kupišk", "zaras", "skuod",
    "širvint", "molėt", "šaki", "ignalin", "švenčion", "pakruoj", "šalčinink", "trak", "lazdij", "kalvarij", "rietav", "nering",
    "pagėgi", "birštön", "birston", "neringa", "london", "riga", "ryg", "tallinn", "talin", "warsaw", "varšuv", "berlin",
    "dublin", "paris", "paryž", "oslo", "stockholm", "helsink", "copenhag", "kopenhag", "amsterdam",
)
_BRAND_FORMS = r"(?:UAB|MB|AB|VšĮ|VŠĮ|IĮ|VĮ|TŪB|KŪB|Ltd\.?|LLC|GmbH|Inc\.?|OÜ|SIA|Oy|S\.A\.|B\.V\.|AS|ApS|Plc|Sp\. z o\.o\.)"
_UPPER = "A-ZĄČĘĖĮŠŲŪŽ"
_LOWER = "a-ząčęėįšųūž"
_CAP_WORD = rf"[{_UPPER}][\w{_LOWER}'’.-]*"
_COMPANY = re.compile(
    rf"\b({_BRAND_FORMS})\s+[„\"“']?({_CAP_WORD}(?:\s+{_CAP_WORD}){{0,3}})[“\"”']?"
    rf"|[„\"“]({_CAP_WORD}(?:\s+{_CAP_WORD}){{0,3}})[“\"”],?\s+({_BRAND_FORMS})(?![\w{_LOWER}])"
    rf"|\b({_CAP_WORD}(?:\s+{_CAP_WORD}){{0,2}}),?\s+({_BRAND_FORMS})(?![\w{_LOWER}])",
)
_REG_CODE = re.compile(
    r"(?:įmonės kodas|įm\.\s*kodas|juridinio asmens kodas|registracijos (?:kodas|nr\.?|numeris)|company (?:code|number|reg\.? no\.?)|registration (?:no\.?|number|code)|reg\.? (?:no\.?|code|nr\.?)|kodas)\s*[:：]?\s*(\d{9})\b",
    re.IGNORECASE,
)
_LT_ADDRESS = re.compile(
    rf"({_CAP_WORD}(?:\s+{_CAP_WORD}){{0,3}}\s+(?:g|gatvė|pr|prospektas|al|alėja|a|aikštė|pl|plentas|kel|kelias|sk|skersgatvis|tak|takas)\.?\s+\d+[A-Za-z]?(?:\s*[-/]\s*\d+)?(?:,?\s*(?:LT-)?\d{{5}})?(?:,?\s*[{_UPPER}][{_LOWER}]+)?)"
)
_EN_ADDRESS = re.compile(
    r"(\d{1,5}\s+[A-Z][\w'-]*(?:\s+[A-Z][\w'-]*){0,3}\s+(?:Street|St|Road|Rd|Avenue|Ave|Lane|Ln|Drive|Dr|Boulevard|Blvd|Square|Sq|Place|Pl|Way|Court|Ct)\.?(?:,\s*[A-Z][\w' -]{1,30})?(?:,?\s*[A-Z0-9][A-Z0-9 -]{2,9})?)"
)
_PHONE = re.compile(r"(?<![\d€$])(\+\d{1,3}[\s(-]*\d{1,4}[\s)-]*\d{2,4}(?:[\s-]*\d{2,4}){1,3}|8[\s(-]*\d{3}[\s)-]*\d{2,3}[\s-]*\d{2,3})(?![\d€$])")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_ROLE = re.compile(
    r"(direktor\w*|vadov\w*|įkūrėj\w*|steigėj\w*|savinink\w*|buhalter\w*|gydytoj\w*|advokat\w*|partner\w*|vadybinink\w*|konsultant\w*|CEO|CTO|CFO|founder|co-founder|director|owner|manager|head of \w+|specialist|accountant|consultant|dentist|doctor)",
    re.IGNORECASE,
)
_PERSON = re.compile(rf"\b([{_UPPER}][{_LOWER}]{{2,}}\s+[{_UPPER}][{_LOWER}]{{2,}}(?:\s+[{_UPPER}][{_LOWER}]{{2,}})?)\b")
_NOT_PERSON = frozenset(
    {
        "uab", "mb", "ab", "všį", "ltd", "llc", "inc", "sausio", "vasario", "kovo", "balandžio", "gegužės", "birželio", "liepos", "rugpjūčio",
        "rugsėjo", "spalio", "lapkričio", "gruodžio", "january", "february", "march", "april", "june", "july", "august", "september",
        "october", "november", "december", "vilnius", "kaunas", "klaipėda", "lietuva", "lithuania", "google", "meta", "facebook",
    }
)
_HOURS = re.compile(
    r"((?:darbo laikas|dirbame|opening hours|working hours|open|hours|atidaryta)[^.;]{0,40}?\d{1,2}[:.]\d{2}\s?[–—-]\s?\d{1,2}[:.]\d{2}|\b(?:I|II|III|IV|V|VI|VII|Pr|An|Tr|Kt|Pn|Št|Sk|Mon|Tue|Wed|Thu|Fri|Sat|Sun|Pirmadien\w*|Antradien\w*|Trečiadien\w*|Ketvirtadien\w*|Penktadien\w*|Šeštadien\w*|Sekmadien\w*|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b[^.;]{0,25}?\d{1,2}[:.]\d{2}\s?[–—-]\s?\d{1,2}[:.]\d{2})",
    re.IGNORECASE,
)
_MAP_HREF = re.compile(r"maps\.google|google\.[a-z.]+/maps|goo\.gl/maps|maps\.app\.goo\.gl|waze\.com|maps\.apple\.com|openstreetmap\.org", re.IGNORECASE)
_CONTACT_LINK = re.compile(r"kontakt|contact|susisiek", re.IGNORECASE)
_PRIVACY_LINK = re.compile(r"privat|privacy|asmens[- ]duomen|duomenų[- ]apsaug|gdpr|slapuk|cookie", re.IGNORECASE)
_TERMS_LINK = re.compile(r"taisykl|terms|sąlyg|salyg|conditions|agreement|sutart", re.IGNORECASE)
_ABOUT_LINK = re.compile(r"apie|about|team|komanda|mes\b", re.IGNORECASE)
_CART_LINK = re.compile(r"cart|basket|krepš|kreps|checkout|bag\b", re.IGNORECASE)
_MENU_HINT = re.compile(r"menu|meniu|burger|hamburger|navbar-toggle|nav-toggle", re.IGNORECASE)
_LOGO_HINT = re.compile(r"logo|brand|site-title|navbar-brand", re.IGNORECASE)
_CONSENT_HINT = re.compile(r"cookie|consent|gdpr|slapuk|sutikim|(?<![a-z])cmp(?![a-z])|privacy-banner", re.IGNORECASE)
_CONSENT_TEXT = re.compile(r"naudoja\w* slapuk|slapukų politik|sutinku su slapuk|uses? cookies|accept (?:all )?cookies|cookie settings|slapukų nustatym|priimti (?:visus )?slapuk", re.IGNORECASE)
_TIER_HINT = re.compile(r"(?<![a-z])(?:plan|tier|package|paketas|planas|price-card|pricing-card|price-box|pricing-box|pricing-column|price-column|pricing-plan|price-plan|pricing-tier)(?![a-z])", re.IGNORECASE)
_HIGHLIGHT_HINT = re.compile(r"popular|recommended|featured|highlight|selected|is-active|best-value|rekomenduoj|populiar", re.IGNORECASE)
_PERIOD_MONTH = re.compile(r"/\s?(?:mėn|men|mo|month)\b|per (?:mėn|mėnesį|month)|monthly|mėnesiui|kas mėnesį|a month", re.IGNORECASE)
_PERIOD_YEAR = re.compile(r"/\s?(?:m|met|yr|year)\b|per (?:metus|year|annum)|yearly|annual|metams|kasmet|a year", re.IGNORECASE)
_LIMIT_WORDS = re.compile(r"\b(?:iki|up to|limit|vartotoj\w*|users?|seats?|projekt\w*|projects?|GB|TB|MB|contacts?|kontakt\w*|emails?|laišk\w*|per (?:mėn|month|day|dien)|vnt\.?|pcs|\d+\s?%)\b", re.IGNORECASE)
_SUPPORT_WORDS = re.compile(r"pagalb\w*|support|SLA|priorit\w*|priority|account manager|vadybinink\w*|onboarding|training|mokym\w*|phone support|chat", re.IGNORECASE)
_UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
_TRACK_PARAMS = ("utm_", "ref", "src", "source", "campaign", "via", "partner")

# Absolute or unsourced claims — the ad board's PROMISES idea, widened for pages.
ABSOLUTE_CLAIMS = (
    "garantuoj", "jokių klaidų", "be klaidų", "visiškai", "100 %", "100%", "geriausi", "geriausia", "geriausias", "pigiausi",
    "pigiausia", "nr. 1", "nr.1", "№1", "#1", "lyderis", "lyderiai", "vienintel", "niekada", "visada", "visiems",
    "guarantee", "guaranteed", "the best", "no errors", "cheapest", "number one", "number 1", "market leader", "leading",
    "the only", "never", "always", "everyone", "risk-free", "be rizikos",
)
_NUMBER_CLAIM = re.compile(
    r"(\d{1,3}\s?%\s?(?:patenkint\w*|klient\w*|vartotoj\w*|of (?:our )?(?:customers|users|clients)|satisfied|rekomenduo\w*|recommend\w*|sutaupo\w*|greičiau|faster|daugiau|more)[^.!;]{0,40}"
    r"|(?:\d{1,3}(?:[ .]\d{3})+|\d{4,})\s?\+?\s?(?:patenkint\w*\s)?(?:klient\w*|customers|users|vartotoj\w*|įmon\w*|companies|businesses|projekt\w*|projects|pacient\w*|patients|atsiliepim\w*|reviews)[^.!;]{0,30})",
    re.IGNORECASE,
)
_SOURCE_NEARBY = re.compile(r"šaltinis|source|pagal|according to|tyrim|survey|study|\(\d{4}\)|\bn\s?=\s?\d", re.IGNORECASE)

_SCARCITY = re.compile(
    r"(liko tik \d+(?:[^.!;]|\.(?=\d)){0,40}|liko \d+ vnt(?:[^.!;]|\.(?=\d)){0,30}|liko \d+ viet\w*(?:[^.!;]|\.(?=\d)){0,30}|paskutin(?:is|iai|ės|ę) \d+(?:[^.!;]|\.(?=\d)){0,30}|paskutin(?:is|iai|ės) (?:vnt|viet|egzemplior)\w*(?:[^.!;]|\.(?=\d)){0,30}"
    r"|only \d+ left(?:[^.!;]|\.(?=\d)){0,40}|\d+ left in stock(?:[^.!;]|\.(?=\d)){0,30}|last \d+ (?:left|items?|seats?|spots?)(?:[^.!;]|\.(?=\d)){0,30}|limited stock(?:[^.!;]|\.(?=\d)){0,30}|ribotas kiekis(?:[^.!;]|\.(?=\d)){0,30}"
    r"|beveik išparduota(?:[^.!;]|\.(?=\d)){0,30}|almost (?:sold out|gone)(?:[^.!;]|\.(?=\d)){0,30}|selling fast(?:[^.!;]|\.(?=\d)){0,30}|greitai baigsis(?:[^.!;]|\.(?=\d)){0,30}|hurry(?:[^.!;]|\.(?=\d)){0,30}|skubėkite(?:[^.!;]|\.(?=\d)){0,30})",
    re.IGNORECASE,
)
_SOCIAL_COUNTER = re.compile(
    r"(\d[\d\s.,]*\s?(?:žmon\w*|people|lankytoj\w*|asmen\w*|users|customers|pirkėj\w*|shoppers|klient\w*)\s(?:dabar |šiuo metu |currently |right now |now |are |is |ką tik |just )?(?:žiūri|peržiūri|viewing|looking|watching|bought|purchased|nusipirko|užsisakė|užsakė|booked|signed up|užsiregistravo)(?:[^.!;]|\.(?=\d)){0,40}"
    r"|(?:viewed|purchased|bought|booked|užsakyta|nupirkta|peržiūrėta)\s(?:by\s)?\d[\d\s.,]*\s?(?:times|people|kart\w*|žmon\w*)(?:[^.!;]|\.(?=\d)){0,30}"
    r"|\d+\s(?:bookings?|orders?|užsakym\w*)\s(?:today|šiandien|in the last|per paskutin\w*)(?:[^.!;]|\.(?=\d)){0,30})",
    re.IGNORECASE,
)
_TIMER = re.compile(
    r"((?:pasiūlymas|akcija|nuolaida|offer|sale|deal|kaina)\s(?:galioja|baigiasi|ends|expires)\s(?:dar\s|už\s|in\s|per\s)?(?:[^.!;]|\.(?=\d)){0,40}?\d+\s?(?:val|h|hours?|min|minutes?|sek|s|seconds?|d|dien\w*|days?)\b(?:[^.!;]|\.(?=\d)){0,30}"
    r"|\b\d{1,2}:\d{2}:\d{2}\b(?:[^.!;]|\.(?=\d)){0,30}|(?:liko|time left|ends in|baigiasi už|hurry, only)\s(?:[^.!;]|\.(?=\d)){0,10}?\d{1,2}:\d{2}\b(?:[^.!;]|\.(?=\d)){0,30}"
    r"|\b\d+\s?(?:val|hours?|hrs|min|minutes?)\s(?:left|liko|remaining)(?:[^.!;]|\.(?=\d)){0,30}|liko\s\d+\s?(?:val|h|min|dien\w*|days?|hours?)(?:[^.!;]|\.(?=\d)){0,30}|countdown(?:[^.!;]|\.(?=\d)){0,30})",
    re.IGNORECASE,
)
_LATE_FEE = re.compile(
    r"((?:\+|plius|plus)\s?(?:pristatym\w*|delivery|shipping|aptarnavim\w*|service|handling|processing|booking|administravim\w*|PVM|VAT)(?:[^.!;]|\.(?=\d)){0,40}"
    r"|(?:pristatymo|aptarnavimo|paslaugos|administravimo|rezervacijos|service|handling|processing|booking|convenience|delivery|shipping)\s(?:mokestis|fee|charge|surcharge)(?:[^.!;]|\.(?=\d)){0,40}"
    r"|(?:be PVM|PVM neįskaičiuot\w*|excl\.? VAT|plus VAT|VAT not included|\+ VAT)(?:[^.!;]|\.(?=\d)){0,30}"
    r"|(?:apskaičiuojam\w* (?:užsakymo|atsiskaitymo) metu|calculated at checkout|added at checkout|papildomai apmokestinam\w*)(?:[^.!;]|\.(?=\d)){0,30})",
    re.IGNORECASE,
)
_CONFIRM_SHAME = re.compile(
    r"((?:ne, |no, )?(?:ačiū, )?(?:thanks, )?(?:man )?(?:nenoriu|nereikia|nenoriu sutaupyti|nenoriu taupyti|nesidomiu|i (?:don'?t|do not) (?:want|need|care)|i'?d rather pay|i'?ll pay full price|mokėsiu pilną kainą|man nesvarbu|i hate|no thanks, i)(?:[^.!;\"”]|\.(?=\d)){0,40})",
    re.IGNORECASE,
)

# Known third-party script, pixel and iframe hosts → vendor and kind.
TRACKER_VENDORS: tuple[tuple[str, str, str], ...] = (
    ("connect.facebook.net", "Meta Pixel", "pixel"),
    ("facebook.com/tr", "Meta Pixel", "pixel"),
    ("googletagmanager.com/gtm", "Google Tag Manager", "tag_manager"),
    ("googletagmanager.com/gtag", "Google Analytics 4", "analytics"),
    ("googletagmanager.com", "Google Tag Manager", "tag_manager"),
    ("google-analytics.com", "Google Analytics", "analytics"),
    ("analytics.google.com", "Google Analytics", "analytics"),
    ("googleadservices.com", "Google Ads", "ads"),
    ("googlesyndication.com", "Google Ads", "ads"),
    ("doubleclick.net", "Google Ads", "ads"),
    ("hotjar.com", "Hotjar", "session_replay"),
    ("clarity.ms", "Microsoft Clarity", "session_replay"),
    ("fullstory.com", "FullStory", "session_replay"),
    ("mouseflow.com", "Mouseflow", "session_replay"),
    ("smartlook.com", "Smartlook", "session_replay"),
    ("snap.licdn.com", "LinkedIn Insight", "pixel"),
    ("analytics.tiktok.com", "TikTok Pixel", "pixel"),
    ("static.ads-twitter.com", "X Pixel", "pixel"),
    ("ct.pinterest.com", "Pinterest Tag", "pixel"),
    ("sc-static.net", "Snap Pixel", "pixel"),
    ("mc.yandex.ru", "Yandex Metrica", "analytics"),
    ("matomo", "Matomo", "analytics"),
    ("piwik", "Matomo", "analytics"),
    ("plausible.io", "Plausible", "analytics"),
    ("cdn.segment.com", "Segment", "analytics"),
    ("js.hs-scripts.com", "HubSpot", "analytics"),
    ("hs-analytics.net", "HubSpot", "analytics"),
    ("embed.tawk.to", "Tawk.to", "chat"),
    ("client.crisp.chat", "Crisp", "chat"),
    ("widget.intercom.io", "Intercom", "chat"),
    ("js.intercomcdn.com", "Intercom", "chat"),
    ("js.driftt.com", "Drift", "chat"),
    ("mailchimp.com", "Mailchimp", "email"),
    ("list-manage.com", "Mailchimp", "email"),
    ("consent.cookiebot.com", "Cookiebot", "consent"),
    ("cookiebot.com", "Cookiebot", "consent"),
    ("cdn.cookielaw.org", "OneTrust", "consent"),
    ("onetrust.com", "OneTrust", "consent"),
    ("cookieyes.com", "CookieYes", "consent"),
    ("cookie-script.com", "Cookie Script", "consent"),
    ("iubenda.com", "iubenda", "consent"),
    ("usercentrics.eu", "Usercentrics", "consent"),
    ("cookiefirst.com", "CookieFirst", "consent"),
    ("termly.io", "Termly", "consent"),
)
_INLINE_TRACKERS: tuple[tuple[str, str, str], ...] = (
    (r"\bfbq\s*\(", "Meta Pixel", "pixel"),
    (r"\bgtag\s*\(\s*['\"]config['\"]", "Google Analytics 4", "analytics"),
    (r"\bga\s*\(\s*['\"]create['\"]", "Google Analytics", "analytics"),
    (r"GTM-[A-Z0-9]{4,}", "Google Tag Manager", "tag_manager"),
    (r"\bhj\s*\(|_hjSettings", "Hotjar", "session_replay"),
    (r"\bclarity\s*\(", "Microsoft Clarity", "session_replay"),
    (r"\b_paq\b", "Matomo", "analytics"),
    (r"\bttq\.", "TikTok Pixel", "pixel"),
    (r"\b_linkedin_partner_id\b", "LinkedIn Insight", "pixel"),
    (r"\bym\s*\(\s*\d+", "Yandex Metrica", "analytics"),
)
_VENDOR_POLICY_WORDS = {
    "Meta Pixel": ("meta", "facebook"), "Google Tag Manager": ("google",), "Google Analytics 4": ("google analytics", "ga4", "google"),
    "Google Analytics": ("google analytics", "google"), "Google Ads": ("google ads", "google"), "Hotjar": ("hotjar",),
    "Microsoft Clarity": ("clarity", "microsoft"), "FullStory": ("fullstory",), "Mouseflow": ("mouseflow",), "Smartlook": ("smartlook",),
    "LinkedIn Insight": ("linkedin",), "TikTok Pixel": ("tiktok",), "X Pixel": ("twitter", " x "), "Pinterest Tag": ("pinterest",),
    "Snap Pixel": ("snap",), "Yandex Metrica": ("yandex",), "Matomo": ("matomo", "piwik"), "Plausible": ("plausible",), "Segment": ("segment",),
    "HubSpot": ("hubspot",), "Tawk.to": ("tawk",), "Crisp": ("crisp",), "Intercom": ("intercom",), "Drift": ("drift",), "Mailchimp": ("mailchimp",),
}
_PERSONAL_FIELDS = re.compile(r"email|e-?mail|el[_.-]?past|pašt|phone|tel|telefon|name|vard|pavard|surname|company|įmon|imon|kodas|code|address|adres", re.IGNORECASE)
_PAYMENT_WORDS = re.compile(r"paypal|apple pay|google pay|klarna|paysera|visa|mastercard|maestro|lizing\w*|išsimokėtinai|instal?lments?|bank transfer|pavedimu|kortele|credit card|debit card|revolut|stripe", re.IGNORECASE)
_REVIEW_TEXT = re.compile(r"(\d[.,]\d)\s?(?:iš|/|out of)\s?5|(\d[\d\s]*)\s?(?:atsiliepim\w*|reviews?|ratings?|įvertinim\w*)", re.IGNORECASE)
_RETURN_WINDOW = re.compile(r"(\d+)\s?(?:d\.|dienų|dienas|dien\w*|days?|-day)\s?[^.!;]{0,20}?(?:grąžin\w*|return\w*|refund\w*|money[- ]back)|(?:grąžin\w*|return\w*|refund\w*|money[- ]back)[^.!;]{0,30}?(\d+)\s?(?:d\.|dienų|dienas|dien\w*|days?)", re.IGNORECASE)
_PICKUP_TIME = re.compile(r"((?:atsiim\w*|atsiėm\w*|pickup|pick-up|pick up|collect\w*)[^.!;]{0,30}?(?:per|within|in)\s\d+\s?(?:val|h|hours?|min|minutes?|d\.|dien\w*|days?)[^.!;]{0,20})", re.IGNORECASE)
_DELIVERY_TIME = re.compile(r"((?:pristat\w*|deliver\w*|shipping|siunt\w*|gausite|arrives?)[^.!;]{0,30}?(?:per|within|in)?\s?\d+(?:\s?[–—-]\s?\d+)?\s?(?:val|h|hours?|d\.|d\.d\.|darbo dien\w*|dien\w*|days?|business days?|working days?)[^.!;]{0,20})", re.IGNORECASE)
_STOCK = re.compile(r"(yra sandėlyje|turime sandėlyje|sandėlyje|in stock|out of stock|išparduota|nėra sandėlyje|available now|prekė yra|back[- ]order|užsakoma|pre-?order|likutis[^.!;]{0,20})", re.IGNORECASE)
_WARRANTY = re.compile(r"((?:\d+)\s?(?:m\.|metų|metai|met\w*|years?|yr|-year|mėn\w*|months?)\s?garantij\w*|garantij\w*[^.!;]{0,20}?\d+\s?(?:m\.|metų|metai|met\w*|mėn\w*)|(?:\d+)[- ]?(?:year|month)s?\s?warranty|warranty[^.!;]{0,20}?\d+\s?(?:years?|months?))", re.IGNORECASE)
_DISCOVERY = re.compile(r"kaip išsirinkti|kaip pasirinkti|how to choose|buying guide|pirkimo gidas|gidas|guide|patarimai|tips|kas svarbu|what matters|explainer|sužinokite|learn more about|populiariausi|most popular|naujienos|new arrivals|rekomenduojam\w*", re.IGNORECASE)
_EVALUATION = re.compile(r"palygin\w*|compare|comparison|filtr\w*|filter|rūšiuo\w*|sort by|rikiuo\w*|atsiliepim\w*|reviews?|įvertinim\w*|rating|specifikacij\w*|specifications?|lentel\w*", re.IGNORECASE)
_TRACKING_HREF = re.compile(r"track|sek(?:ti|imas|ite)|siunt|shipment|parcel|dpd\.|omniva|venipak|lpexpress|lp-express|post\.lt|ups\.com|dhl\.|fedex|itella|paštomat|pastomat|gls-", re.IGNORECASE)
_DELIVERY_WORDS = re.compile(r"(pristatym\w*|delivery|deliver\w*|atsiėm\w*|atsiim\w*|pickup|pick-up|paštomat\w*|kurjer\w*|courier|shipping|išsiųs\w*|shipped|dispatched|siunt\w*|gausite|arriv\w*|atvyks\w*|atkeliau\w*)[^.!;]{0,80}", re.IGNORECASE)
_TOTAL_WORDS = re.compile(r"iš viso|viso|suma|mokėti|sumokėta|apmokėta|total|amount paid|grand total|paid|order total|bendra", re.IGNORECASE)
_ORDER_REF = re.compile(r"(?:užsakym\w*|order|sąskait\w*|invoice|rezervacij\w*|booking|nr\.?|no\.?|#)[\s:]*(?:nr\.?|no\.?|number|numeris|id|#)?[\s:#]*([A-Z]{1,5}[-_/]?\d{3,}(?:[-_/][A-Z0-9]+)?|\d{4,}(?:[-/]\d+)?)", re.IGNORECASE)
_LINE_ITEM = re.compile(rf"([{_UPPER}][^€$£\n|]{{3,80}}?)\s(?:[–—-]\s)?(?:(\d+)\s?(?:vnt\.?|x\b|×|pcs|qty|units?)|(?:x|×)\s?(\d+))", re.UNICODE)
_UNSUBSCRIBE = re.compile(r"atsisakyti|unsubscribe|nebenoriu|opt[- ]out|manage preferences|atsisakyk", re.IGNORECASE)
_TRY_WORDS = re.compile(r"try|išband\w*|nemokam\w*|free|start|pradėk\w*|registruo\w*|sign up|get started|demo", re.IGNORECASE)



def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ").replace(" ", " ")).strip()


class _PageParser(HTMLParser):
    """Collects the elements a visitor meets first. Tolerates the HTML real sites ship."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lang: str | None = None
        self.title_parts: list[str] = []
        self.meta_description: str | None = None
        self.meta: dict[str, str] = {}
        self.base_href: str | None = None
        self.viewport_meta = False
        self.h1: list[str] = []
        self.h2: list[str] = []
        self.headings: list[dict[str, Any]] = []
        self.buttons: list[str] = []
        self.button_offsets: dict[str, int] = {}
        self.body_parts: list[str] = []
        self.forms = 0
        self.input_types: list[str] = []
        self.images_total = 0
        self.images_with_alt = 0
        self.images: list[dict[str, Any]] = []
        self.image_only_links: list[dict[str, Any]] = []
        self.links: list[dict[str, Any]] = []
        self.scripts: list[dict[str, Any]] = []
        self.iframes: list[str] = []
        self.jsonld: list[str] = []
        self.form_details: list[dict[str, Any]] = []
        self.checked_controls: list[dict[str, Any]] = []
        self.selected_options: list[str] = []
        self.consent_orders: list[int] = []
        self.consent_texts: list[str] = []
        self.tier_blocks: list[dict[str, Any]] = []
        self.footer_parts: list[str] = []
        self.paragraphs: list[str] = []
        self.nav_links = 0
        self.home_link: str | None = None
        self.cart_link: str | None = None
        self.menu_control: str | None = None
        self.has_nav = False
        self.dead_anchors: list[str] = []
        self.tel_links: list[str] = []
        self.mailto_links: list[str] = []
        self.subject_line: str | None = None
        self._stack: list[tuple[str, list[str] | None, dict[str, str]]] = []
        self._skip = 0
        self._in_form = 0
        self._in_nav = 0
        self._in_footer = 0
        self._in_header = 0
        self._order = 0
        self._raw_chars = 0
        self._open_blocks: list[dict[str, Any]] = []
        self._pending_checked: dict[str, dict[str, Any]] = {}
        self._await_label: dict[str, Any] | None = None

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
            self.button_offsets[text] = self._raw_chars

    def _current_form(self) -> dict[str, Any] | None:
        return self.form_details[-1] if self._in_form and self.form_details else None

    def _enclosing(self, tag: str) -> dict[str, str] | None:
        for name, _, attrs in reversed(self._stack):
            if name == tag:
                return attrs
        return None

    @staticmethod
    def _hint(a: dict[str, str]) -> str:
        return f"{a.get('class', '')} {a.get('id', '')} {a.get('role', '')} {a.get('aria-label', '')} {a.get('data-testid', '')}"

    # -- parser callbacks -------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        a = self._attrs(attrs)
        self._order += 1
        if tag == "html" and a.get("lang"):
            self.lang = a["lang"].strip()
        if tag in VOID_TAGS:
            self.handle_startendtag(tag, attrs)
            return
        if tag in SKIP_TAGS:
            self._skip += 1
        hint = self._hint(a)
        if tag == "form":
            self.forms += 1
            self._in_form += 1
            self.form_details.append({"action": a.get("action") or None, "method": (a.get("method") or "get").lower(), "fields": [], "privacy_link_nearby": False, "checked": [], "order": self._order, "_text": []})
        if tag == "nav" or a.get("role") == "navigation":
            self._in_nav += 1
            self.has_nav = True
        if tag == "footer" or a.get("role") == "contentinfo" or re.search(r"(?<![a-z])footer(?![a-z])", a.get("class", "") + " " + a.get("id", ""), re.IGNORECASE):
            self._in_footer += 1
            a["__footer__"] = "1"
        if tag == "header" or a.get("role") == "banner":
            self._in_header += 1
            a["__header__"] = "1"
        if tag in ("textarea", "select"):
            self._note_input(tag)
            form = self._current_form()
            if form is not None:
                form["fields"].append({"name": a.get("name") or a.get("id") or tag, "type": tag})
        if tag == "iframe" and a.get("src"):
            self.iframes.append(a["src"])
        if tag == "script":
            a["__order__"] = str(self._order)
        if tag == "option" and "selected" in a:
            a["__selected__"] = "1"
        if tag == "button" and _MENU_HINT.search(hint) and not self.menu_control:
            self.menu_control = _clean(a.get("aria-label") or a.get("class") or "button")
        if _CONSENT_HINT.search(f"{a.get('class', '')} {a.get('id', '')}") and tag not in ("a", "link", "script", "input", "label"):
            self.consent_orders.append(self._order)
        if _TIER_HINT.search(a.get("class", "")) or a.get("data-plan") or a.get("data-tier"):
            block = {"order": self._order, "headings": [], "features": [], "_text": [], "highlighted": bool(_HIGHLIGHT_HINT.search(hint)), "depth": len(self._open_blocks)}
            self._open_blocks.append(block)
            self.tier_blocks.append(block)
            a["__tier__"] = "1"
        # Block boundaries separate words; a space is harmless inside inline runs.
        for _, buf, _ in self._stack:
            if buf is not None:
                buf.append(" ")
        for block in self._open_blocks:
            block["_text"].append(" ")
        if self._skip == 0:
            self.body_parts.append(" ")
            self._raw_chars += 1
        capture = [] if tag in CAPTURE_TAGS else None
        self._stack.append((tag, capture, a))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        a = self._attrs(attrs)
        if tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name == "description" and a.get("content"):
                self.meta_description = _clean(a["content"])
            if name == "viewport":
                self.viewport_meta = True
            if name and a.get("content"):
                self.meta.setdefault(name, _clean(a["content"]))
        elif tag == "base" and a.get("href"):
            self.base_href = a["href"]
        elif tag == "img":
            self.images_total += 1
            alt = _clean(a.get("alt", ""))
            if alt:
                self.images_with_alt += 1
            link = self._enclosing("a")
            src = a.get("src") or a.get("data-src") or ""
            self.images.append({"src": src, "alt": alt or None, "inside_link": link is not None, "order": self._order})
            if link is not None:
                link.setdefault("__imgs__", "")
                link["__imgs__"] += ("|" if link["__imgs__"] else "") + f"{src}\t{alt}"
            if _LOGO_HINT.search(f"{a.get('class', '')} {a.get('id', '')} {alt} {src}") and link is not None and self.home_link is None:
                self.home_link = link.get("href") or ""
        elif tag == "input":
            kind = (a.get("type") or "text").lower()
            self._note_input(kind)
            form = self._current_form()
            if form is not None and kind not in ("submit", "button", "image", "hidden"):
                form["fields"].append({"name": a.get("name") or a.get("id") or kind, "type": kind})
            if kind in ("submit", "button", "image"):
                label = a.get("value") or a.get("aria-label") or a.get("alt") or ""
                self._add_button(label or ("Submit" if kind == "submit" else ""))
            if kind in ("checkbox", "radio") and "checked" in a:
                record = {"type": kind, "name": a.get("name") or a.get("id") or "", "label": "", "in_form": self._in_form > 0, "order": self._order}
                self.checked_controls.append(record)
                if form is not None:
                    form["checked"].append(record)
                label = self._enclosing("label")
                if label is not None:
                    label["__checked__"] = str(len(self.checked_controls) - 1)
                elif a.get("id"):
                    self._pending_checked[a["id"]] = record
                    self._await_label = record
                else:
                    self._await_label = record
        elif tag == "br":
            self.body_parts.append(" ")
            self._raw_chars += 1
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
        if name == "nav" or attrs.get("role") == "navigation":
            self._in_nav = max(0, self._in_nav - 1)
        if attrs.get("__footer__"):
            self._in_footer = max(0, self._in_footer - 1)
        if attrs.get("__header__"):
            self._in_header = max(0, self._in_header - 1)
        if attrs.get("__tier__") and self._open_blocks:
            self._open_blocks.pop()
        if buf is None:
            return
        text = _clean("".join(buf))
        if name == "title":
            if text:
                self.title_parts.append(text)
        elif name == "h1":
            if text:
                self.h1.append(text)
                self.headings.append({"level": 1, "text": text})
        elif name == "h2":
            if text:
                self.h2.append(text)
                self.headings.append({"level": 2, "text": text})
                for block in self._open_blocks:
                    block["headings"].append(text)
        elif name in ("h3", "h4"):
            if text:
                self.headings.append({"level": int(name[1]), "text": text})
                for block in self._open_blocks:
                    block["headings"].append(text)
        elif name == "li":
            if text:
                for block in self._open_blocks:
                    block["features"].append(text)
        elif name in ("th", "td"):
            pass
        elif name == "p":
            if text:
                self.paragraphs.append(text)
        elif name == "label":
            idx = attrs.get("__checked__")
            if idx is not None and text:
                self.checked_controls[int(idx)]["label"] = text[:160]
            elif attrs.get("for") in self._pending_checked and text:
                record = self._pending_checked.pop(attrs["for"])
                record["label"] = record["label"] or text[:160]
                if self._await_label is record:
                    self._await_label = None
        elif name == "option":
            if attrs.get("__selected__") and text:
                self.selected_options.append(text[:160])
        elif name == "script":
            kind = (attrs.get("type") or "").lower()
            raw = "".join(buf)
            if "ld+json" in kind:
                self.jsonld.append(raw)
            else:
                self.scripts.append({"src": attrs.get("src") or None, "inline": raw[:4000] if not attrs.get("src") else "", "order": int(attrs.get("__order__") or 0)})
        elif name == "form":
            if self.form_details:
                form = self.form_details[-1]
                form["text"] = _clean(" ".join(form.pop("_text", [])))[:400]
        elif name == "footer" or attrs.get("__footer__"):
            if text:
                self.footer_parts.append(text)
        elif name == "button":
            self._add_button(text or attrs.get("aria-label", ""))
        elif name == "a":
            label = text or attrs.get("aria-label", "")
            href = attrs.get("href")
            imgs = [dict(zip(("src", "alt"), part.split("\t", 1))) for part in attrs.get("__imgs__", "").split("|") if part]
            if _looks_like_button(attrs, label):
                self._add_button(label)
            if not text and imgs:
                self.image_only_links.append({"href": href, "images": [{"src": i["src"], "alt": i.get("alt") or None} for i in imgs], "all_without_alt": all(not i.get("alt") for i in imgs)})
            if href is None or href.strip() in ("", "#") or href.strip().lower().startswith("javascript:"):
                if label and len(label) <= 80:
                    self.dead_anchors.append(label)
            else:
                href = href.strip()
                lower = href.lower()
                if lower.startswith("tel:"):
                    self.tel_links.append(href[4:].strip())
                elif lower.startswith("mailto:"):
                    self.mailto_links.append(href[7:].split("?")[0].strip())
                else:
                    self.links.append({
                        "href": href, "text": label[:120], "order": self._order, "in_nav": self._in_nav > 0,
                        "in_footer": self._in_footer > 0, "in_header": self._in_header > 0, "has_image": bool(imgs),
                        "rel": attrs.get("rel", ""), "hint": self._hint(attrs)[:200],
                    })
                    if self._in_nav:
                        self.nav_links += 1
                    hint = self._hint(attrs)
                    if self.home_link is None and (_LOGO_HINT.search(hint) or (self._in_header and imgs)):
                        self.home_link = href
                    if self.cart_link is None and (_CART_LINK.search(lower) or _CART_LINK.search(hint) or _CART_LINK.search(label)):
                        self.cart_link = href
                    if self.menu_control is None and _MENU_HINT.search(hint):
                        self.menu_control = _clean(attrs.get("aria-label") or attrs.get("class") or "link")
            form = self._current_form()
            if form is not None and href and (_PRIVACY_LINK.search(href) or _PRIVACY_LINK.search(label)):
                form["privacy_link_nearby"] = True

    def handle_data(self, data: str) -> None:
        if not data:
            return
        for _, buf, _ in self._stack:
            if buf is not None:
                buf.append(data)
        in_title = any(t == "title" for t, _, _ in self._stack)
        if self._skip == 0 and not in_title:
            self.body_parts.append(data)
            self._raw_chars += len(data)
            for block in self._open_blocks:
                block["_text"].append(data)
            form = self._current_form()
            if form is not None:
                form["_text"].append(data)
            if self._await_label is not None and data.strip():
                self._await_label["label"] = self._await_label["label"] or _clean(data)[:160]
                self._await_label = None
            if _CONSENT_TEXT.search(data):
                self.consent_orders.append(self._order)
                self.consent_texts.append(_clean(data)[:160])
            if self.subject_line is None:
                m = re.match(r"\s*(?:Subject|Tema|Antraštė)\s*:\s*(.+)", data)
                if m:
                    self.subject_line = _clean(m.group(1))[:200]

    def close(self) -> None:  # noqa: D401 - HTMLParser API
        super().close()
        while self._stack:
            name, buf, attrs = self._stack.pop()
            self._close(name, buf, attrs)

    # -- result -----------------------------------------------------------
    def page(self) -> dict[str, Any]:
        body = _clean(" ".join(self.body_parts))
        for form in self.form_details:
            if "_text" in form:
                form["text"] = _clean(" ".join(form.pop("_text")))[:400]
        tiers = []
        for block in self.tier_blocks:
            text = _clean(" ".join(block.pop("_text", [])))
            block["text"] = text
            block["prices"] = find_prices(text)
            tiers.append(block)
        return {
            "title": _clean(" ".join(self.title_parts)) or None,
            "meta_description": self.meta_description,
            "meta": self.meta,
            "base_href": self.base_href,
            "viewport_meta": self.viewport_meta,
            "lang": self.lang,
            "h1": self.h1,
            "h2": self.h2,
            "headings": self.headings,
            "buttons": self.buttons,
            "button_offsets": self.button_offsets,
            "body_text": body,
            "first_screen_text": body[:FIRST_SCREEN_CHARS],
            "word_count": len(_TOKEN.findall(body)),
            "forms": {"count": self.forms, "inputs": self.input_types},
            "form_details": self.form_details,
            "images_total": self.images_total,
            "images_with_alt": self.images_with_alt,
            "images": self.images,
            "image_only_links": self.image_only_links,
            "links": self.links,
            "tel_links": self.tel_links,
            "mailto_links": self.mailto_links,
            "scripts": self.scripts,
            "iframes": self.iframes,
            "jsonld_raw": self.jsonld,
            "checked_controls": self.checked_controls,
            "selected_options": self.selected_options,
            "consent_orders": self.consent_orders,
            "consent_texts": self.consent_texts,
            "tier_blocks": tiers,
            "footer_text": _clean(" ".join(self.footer_parts)),
            "paragraphs": self.paragraphs,
            "navigation": {"has_nav": self.has_nav, "nav_links": self.nav_links, "home_link": self.home_link, "cart_link": self.cart_link, "menu_control": self.menu_control, "dead_anchors": self.dead_anchors},
            "subject_line": self.subject_line,
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
    page["html_bytes"] = len((html or "").encode("utf-8", errors="replace"))
    page["jsonld"] = _parse_jsonld(page["jsonld_raw"])
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


def _fact_tokens(fact: str) -> list[str]:
    """Words of three letters or more plus every number, case-folded, for fact matching."""
    out: list[str] = []
    for tok in re.findall(r"\d+(?:[.,]\d+)?|[^\W\d_]+", str(fact or "").casefold()):
        if (tok[0].isdigit() or len(tok) >= 3) and tok not in _STOPWORDS and tok not in out:
            out.append(tok)
    return out


def _tokens_in_text(tokens: list[str], text: str) -> list[str]:
    """Which of the tokens occur in the text: numbers by value, words by stem."""
    words = set(_WORD.findall(str(text or "").casefold()))
    numbers = {parse_amount(n) for n in re.findall(r"\d+(?:[.,]\d+)?", str(text or ""))}
    found: list[str] = []
    for tok in tokens:
        if tok[0].isdigit():
            if parse_amount(tok) in numbers:
                found.append(tok)
        elif any(same_stem(tok, w) for w in words):
            found.append(tok)
    return found


def _fact_found(fact: str, text: str) -> bool:
    tokens = _fact_tokens(fact)
    return bool(tokens) and len(_tokens_in_text(tokens, text)) == len(tokens)


def _quote(text: str, needle: str, pad: int = 60) -> str | None:
    """The sentence-sized stretch of text around the first stem hit of the fact's tokens."""
    tokens = _fact_tokens(needle)
    if not tokens:
        return None
    low = text.casefold()
    for tok in tokens:
        pos = low.find(tok[:STEM_LETTERS] if not tok[0].isdigit() else tok)
        if pos >= 0:
            start = max(0, pos - pad)
            end = min(len(text), pos + len(tok) + pad)
            return ("…" if start > 0 else "") + text[start:end].strip() + ("…" if end < len(text) else "")
    return None


def _context(text: str, start: int, end: int, pad: int = 40) -> str:
    s = max(0, start - pad)
    e = min(len(text), end + pad)
    return ("…" if s > 0 else "") + text[s:e].strip() + ("…" if e < len(text) else "")


def _parse_jsonld(blocks: list[str]) -> dict[str, Any]:
    """Flattened JSON-LD entities with the fields the checks compare; parse errors kept."""
    entities: list[dict[str, Any]] = []
    errors: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        if "@graph" in node:
            walk(node["@graph"])
        types = node.get("@type")
        if types:
            if isinstance(types, list):
                types = "/".join(str(t) for t in types)
            entities.append({"type": str(types), "data": node})
        for key, value in node.items():
            if key != "@graph" and isinstance(value, (dict, list)):
                walk(value)

    for raw in blocks:
        raw = raw.strip()
        if not raw:
            continue
        try:
            walk(json.loads(raw))
        except ValueError as exc:
            errors.append(str(exc))
    return {"entities": entities, "errors": errors}


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


def _as_list(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        return [v for v in value if v not in (None, "")]
    return [value]


def _requested_checks(args: dict) -> set[str]:
    raw = args.get("checks")
    if isinstance(raw, str):
        raw = [part.strip() for part in re.split(r"[,\s]+", raw) if part.strip()]
    checks: set[str] = set()
    for item in raw or []:
        name = _CHECK_ALIASES.get(str(item).lower(), str(item).lower())
        if name == "all":
            checks.update(CHECKS)
        elif name in CHECKS:
            checks.add(name)
        else:
            raise ToolError(f"unknown check '{item}'; known: {', '.join(CHECKS)}")
    if args.get("tiers"):
        checks.add("price_ladder")
    if args.get("required_facts") or args.get("rendered_text"):
        checks.add("crawler")
    if args.get("required_content"):
        checks.update({"crawler", "mobile"})
    if args.get("policy"):
        checks.add("data_use")
    if args.get("schema"):
        checks.add("schema")
    if args.get("product_name"):
        checks.add("touchpoint")
    if args.get("tracked_links") or args.get("expected_tags"):
        checks.add("links")
    if args.get("kind"):
        checks.add("images_off")
    return checks


_NEW_INPUTS = ("query", "intent", "checks", "tiers", "required_facts", "required_content", "rendered_text", "kind", "policy", "schema", "product_name", "tracked_links", "expected_tags")


def page_inspect(
    tools: WorkspaceTools | None,
    args: dict,
    fetch_html: Callable[[str], str] | None = None,
    fetch_meta: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compare an ad with its destination page element by element, plus the optional checks.

    ``args.ad``: headline, primary_text, description, cta, audience, price,
    offer, language, deadline (headline or primary_text required unless another
    check is asked for). Page: ``html``, ``path`` (workspace file) or ``url``
    (fetched through ``fetch_html``). Optional: ``query``, ``intent``,
    ``checks`` (see CHECKS), ``tiers``, ``required_facts``, ``rendered_text``,
    ``kind`` + ``expected`` (emails), ``policy``, ``schema``, ``product_name``,
    ``tracked_links`` + ``expected_tags``, ``brand``, ``base_url``,
    ``max_links``. Returns the extracted page, findings ordered block → warn →
    info with the exact copy and page element behind each, counts, what is
    missing, and a one-sentence summary.
    """
    args = args or {}
    ad = args.get("ad")
    if ad is None and any(args.get(k) for k in ("headline", "primary_text")):
        ad = {k: args.get(k) for k in ("headline", "primary_text", "description", "cta", "audience", "price", "offer", "language", "deadline")}
    has_new_input = any(args.get(k) not in (None, "", [], {}) for k in _NEW_INPUTS)
    if ad is None and has_new_input:
        ad = {}
    if not isinstance(ad, dict):
        raise ToolError("the ad has no text: give ad.headline or ad.primary_text")
    ad = {k: (v if v is None else str(v)) for k, v in ad.items()}
    headline = _clean(ad.get("headline") or "")
    primary = _clean(ad.get("primary_text") or "")
    compare_ad = bool(headline or primary)
    if not compare_ad and not has_new_input:
        raise ToolError("the ad has no text: give ad.headline or ad.primary_text")
    checks = _requested_checks(args)
    intent = str(args.get("intent") or "").strip().lower() or None
    if intent and intent not in INTENTS:
        raise ToolError(f"unknown intent '{intent}'; one of {', '.join(INTENTS)}")
    kind = str(args.get("kind") or "").strip().lower() or None
    if kind and kind not in ("receipt", "shipping", "welcome"):
        raise ToolError(f"unknown message kind '{kind}'; one of receipt, shipping, welcome")

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
    base_url = str(args.get("base_url") or (source if source.startswith(("http://", "https://")) else "") or page["base_href"] or "").strip() or None
    page["base_url"] = base_url
    brand = _clean(str(args.get("brand") or args.get("product_name") or ""))
    findings: list[dict[str, Any]] = []
    clauses: list[str] = []
    extra: dict[str, Any] = {}

    if compare_ad:
        _compare_ad(ad, headline, primary, page, findings, clauses, missing)
    else:
        page["lang_detected"] = detect_language(page["body_text"])

    # The page itself (skipped for an email or a recipient-facing artefact: not a landing page).
    if kind is None and "touchpoint" not in checks:
        if page["word_count"] < THIN_PAGE_WORDS:
            findings.append(_finding(
                "thin_page", "warn", None, None, "body", page["word_count"],
                f"{page['word_count']} words of visible text (under {THIN_PAGE_WORDS}). Little here to confirm the promise or answer objections.",
            ))
            clauses.append(f"page has {page['word_count']} words (warn)")
        if not page["h1"]:
            findings.append(_finding("no_h1", "warn", None, None, "h1", None, "The page has no H1, so nothing restates the promise at the top."))
            clauses.append("no H1 (warn)")

    # Competing actions in the first screen (#246).
    first_actions = [b for b in page["buttons"] if page["button_offsets"].get(b, 0) <= FIRST_SCREEN_RAW_CHARS]
    page["first_screen_actions"] = first_actions
    cta = _clean(ad.get("cta") or "")
    if cta:
        cta_words = [w for w in _WORD.findall(cta.casefold()) if len(w) >= 3]
        competing = [b for b in first_actions if cta.casefold() not in b.casefold() and not (cta_words and shared_words(cta_words, b))]
        page["competing_actions"] = len(competing)
        if len(competing) >= 3:
            findings.append(_finding(
                "cta_competition", "warn", "cta", cta, "first_screen_actions", competing,
                f"{len(competing)} other actions share the first screen with “{cta}”: {', '.join(competing)}. Move the secondary ones below the fold.",
            ))
            clauses.append(f"{len(competing)} actions compete with the CTA (warn)")

    # --- optional checks -----------------------------------------------------
    query = _clean(str(args.get("query") or ""))
    if query or intent:
        extra.update(_query_and_intent(query, intent, brand, page, findings, clauses, missing))
    if "credibility" in checks or "trust_signals" in checks or "top_tasks" in checks or (intent == "local"):
        identity = _identity(page)
        extra["identity"] = identity
    if "credibility" in checks:
        extra["credibility"] = _credibility(page, extra["identity"], findings, clauses)
    if "trust" in checks:
        extra["choice_elements"] = _choice_elements(page, findings, clauses)
    if "price_ladder" in checks:
        extra["price_ladder"] = _price_ladder(args.get("tiers"), page, findings, clauses, missing)
    if "crawler" in checks:
        extra["crawler"] = _crawler(args, page, findings, clauses, missing)
    if "mobile" in checks:
        extra["mobile"] = _mobile(page, findings, clauses)
    if "data_use" in checks:
        extra["data_use"] = _data_use(tools, args, page, base_url, fetch_html, findings, clauses, missing)
    if "images_off" in checks:
        extra["images_off"] = _images_off(ad, kind, page, findings, clauses)
    if kind:
        extra["message"] = _message(kind, args.get("expected") or {}, brand, page, findings, clauses, missing)
    if "schema" in checks:
        extra["structured_data"] = _structured_data(args.get("schema"), page, findings, clauses, missing)
    if "navigation" in checks:
        extra["navigation"] = _navigation(page, base_url, findings, clauses)
    if "promises" in checks:
        extra["promises"] = _promises(page, source, missing)
    if "trust_signals" in checks:
        extra["trust_signals"] = _trust_signals(page, extra["identity"], findings, clauses)
    if "touchpoint" in checks:
        extra["touchpoint"] = _touchpoint(brand, page, findings, clauses, missing)
    if "listing" in checks:
        extra["listing"] = _listing(page, findings, clauses)
    if "top_tasks" in checks:
        extra["top_tasks"] = _top_tasks(page, extra["identity"], findings, clauses)
    if "links" in checks:
        extra["links"] = _links(args, page, base_url, fetch_meta, findings, clauses, missing)
        if args.get("tracked_links"):
            extra["tracked_links"] = _tracked_links(args, page, fetch_meta, findings, clauses, missing)

    # Dedupe identical findings that two checks both raised.
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for f in findings:
        key = (f["code"], json.dumps([f["ad_field"], f["ad_value"], f["page_element"], f["page_value"]], ensure_ascii=False, sort_keys=True, default=str))
        if key not in seen:
            seen.add(key)
            unique.append(f)
    findings = unique
    findings.sort(key=lambda f: LEVEL_ORDER.get(f["level"], 9))
    counts = {level: sum(1 for f in findings if f["level"] == level) for level in ("block", "warn", "info")}
    if clauses:
        summary = "; ".join(clauses)
    elif compare_ad:
        summary = "nothing to compare: the ad names no price, call to action, audience or offer and the page restates nothing"
    else:
        summary = "no findings from the requested checks"
    summary = summary[0].upper() + summary[1:] + "."

    out = {
        "page": {
            "source": source,
            "title": page["title"],
            "meta_description": page["meta_description"],
            "lang": page["lang"],
            "lang_detected": page.get("lang_detected"),
            "h1": page["h1"],
            "h2": page["h2"][:MAX_H2],
            "buttons": page["buttons"][:MAX_BUTTONS],
            "first_screen_actions": first_actions[:MAX_BUTTONS],
            "prices": page["prices"][:MAX_PRICES],
            "forms": page["forms"],
            "word_count": page["word_count"],
            "images_total": page["images_total"],
            "images_with_alt": page["images_with_alt"],
            "first_screen_text": page["first_screen_text"],
            "html_bytes": page["html_bytes"],
            "viewport_meta": page["viewport_meta"],
        },
        "findings": findings,
        "counts": counts,
        "missing": missing,
        "summary": summary,
    }
    if page.get("competing_actions") is not None:
        out["page"]["competing_actions"] = page["competing_actions"]
    if checks or query or intent or kind:
        out["checks_run"] = sorted(checks | ({"query"} if query else set()) | ({"intent"} if intent else set()) | ({"message"} if kind else set()))
    out.update(extra)
    return out


def _compare_ad(ad: dict[str, Any], headline: str, primary: str, page: dict[str, Any], findings: list, clauses: list, missing: list) -> None:
    """The original ad-versus-page comparison: promise, price, CTA, audience, language, offer."""
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

    # 6b. A deadline the email or ad names (#244).
    deadline = _clean(ad.get("deadline") or "")
    if deadline:
        tokens = _fact_tokens(deadline)
        found = _tokens_in_text(tokens, page["body_text"])
        absent = [t for t in tokens if t not in found]
        if absent:
            findings.append(_finding(
                "deadline_not_in_text", "warn", "deadline", deadline, "body", _quote(page["body_text"], deadline),
                f"Deadline words not in the visible text: {', '.join(absent)}. If the deadline sits in an image, it disappears with images blocked.",
            ))
            clauses.append(f"deadline '{deadline}' not in text (warn)")


# --- query and intent (#343, #322) -----------------------------------------------


def _infer_intent(query: str) -> str | None:
    low = f" {query.casefold()} "
    if any(f" {w} " in low or low.strip().endswith(w) for w in _INTENT_WORDS["local"]) or _place_in(query):
        return "local"
    for name in ("transactional", "commercial", "informational", "navigational"):
        if any(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", low) for w in _INTENT_WORDS[name]):
            return name
    return None


def _place_in(text: str) -> str | None:
    for w in _WORD.findall(str(text or "")):
        low = w.casefold()
        if any(low.startswith(stem) for stem in _PLACE_STEMS):
            return w
    return None


def _query_and_intent(query: str, intent: str | None, brand: str, page: dict[str, Any], findings: list, clauses: list, missing: list) -> dict[str, Any]:
    out: dict[str, Any] = {}
    above_fold = " ".join(page["h1"] + ([page["title"]] if page["title"] else []) + [page["first_screen_text"]])
    if query:
        terms = [t for t in _fact_tokens(query) if t[0].isdigit() or len(t) >= 3]
        found = _tokens_in_text(terms, above_fold)
        absent = [t for t in terms if t not in found]
        coverage = round(len(found) / len(terms), 2) if terms else 0.0
        quote = page["first_screen_text"][:200]
        out["query_match"] = {"terms": terms, "found": found, "missing": absent, "coverage": coverage, "first_screen_quote": quote}
        if not terms:
            missing.append("query: no content words to match")
        elif coverage < 0.5:
            findings.append(_finding(
                "query_not_answered", "warn", "query", query, "first_screen_text", page["h1"][0] if page["h1"] else quote,
                f"Only {len(found)} of {len(terms)} query words appear in the H1, title or first screen (coverage {coverage}). Missing: {', '.join(absent)}. "
                "The searcher does not see their own words; put them in the H1.",
            ))
            clauses.append(f"query '{query}' coverage {coverage} (warn)")
        else:
            clauses.append(f"query '{query}' coverage {coverage}")
        place = _place_in(query)
        if place and not any(same_stem(place, w) for w in _WORD.findall(page["body_text"])):
            findings.append(_finding(
                "query_location_missing", "block", "query", place, "body", page["h1"][0] if page["h1"] else quote,
                f"The query names {place}; the page never mentions it. A local searcher cannot tell the service is offered there.",
            ))
            clauses.append(f"place '{place}' not on page (block)")
        if intent is None:
            intent = _infer_intent(query)
            if intent:
                out["intent_inferred"] = True
            else:
                missing.append("intent: not given and not inferable from the query; give intent explicitly")
    if intent:
        out["intent_fit"] = _intent_fit(intent, brand, page, findings, clauses, missing)
    return out


def _intent_fit(intent: str, brand: str, page: dict[str, Any], findings: list, clauses: list, missing: list) -> dict[str, Any]:
    title_h1 = " ".join(([page["title"]] if page["title"] else []) + page["h1"])
    brand_in_title = (bool(brand) and any(same_stem(brand_word, w) for brand_word in _WORD.findall(brand.casefold()) for w in _WORD.findall(title_h1.casefold()))) if brand else None
    signals = {
        "has_price": bool(page["prices"]),
        "cta_buttons": len(page["buttons"]),
        "forms": page["forms"]["count"],
        "word_count": page["word_count"],
        "h2_count": len(page["h2"]),
        "brand_in_title": brand_in_title,
    }
    reasons: list[str] = []
    if intent == "transactional":
        if not (signals["cta_buttons"] or signals["forms"]):
            reasons.append("no button or form to buy, order or sign up")
        if not signals["has_price"]:
            reasons.append("no price on the page")
    elif intent == "commercial":
        if signals["word_count"] < 300:
            reasons.append(f"{signals['word_count']} words: too little to compare options")
        if signals["h2_count"] < 2 and not signals["has_price"]:
            reasons.append("no sections (H2) or prices to weigh options against")
    elif intent == "informational":
        if signals["word_count"] < GUIDE_WORDS:
            reasons.append(f"{signals['word_count']} words (a guide needs about {GUIDE_WORDS})")
        if signals["h2_count"] < 2:
            reasons.append(f"{signals['h2_count']} H2 sections (a guide needs at least 2)")
    elif intent == "navigational":
        if brand_in_title is None:
            missing.append("intent navigational: give brand to check that the title or H1 names it")
        elif not brand_in_title:
            reasons.append(f"brand '{brand}' not in the title or H1")
    elif intent == "local":
        identity = _identity(page)
        if not identity["address"]["value"] and not identity["phone"]["value"]:
            reasons.append("no address or phone on the page")
        if not _place_in(page["body_text"]) and not identity["address"]["value"]:
            reasons.append("no place name on the page")
    fits = not reasons
    fit = {"intent": intent, "fits": fits, "signals": signals, "reasons": reasons}
    if fits:
        clauses.append(f"page fits {intent} intent")
    else:
        findings.append(_finding(
            "intent_mismatch", "warn", "intent", intent, "page", signals,
            f"A {intent} searcher lands here and finds: {'; '.join(reasons)}.",
        ))
        clauses.append(f"page does not fit {intent} intent (warn)")
    return fit


# --- credibility (#150) --------------------------------------------------------------


def _identity(page: dict[str, Any]) -> dict[str, dict[str, Any]]:
    body = page["body_text"]
    footer = page["footer_text"]
    out: dict[str, dict[str, Any]] = {k: {"value": None, "where": None} for k in ("company_name", "reg_code", "address", "phone", "email", "named_person")}
    # JSON-LD Organization / LocalBusiness first: it is the most explicit statement.
    for ent in page.get("jsonld", {}).get("entities", []):
        data = ent["data"]
        t = ent["type"].lower()
        if any(k in t for k in ("organization", "localbusiness", "store", "corporation", "dentist", "physician", "restaurant", "hotel", "service")):
            name = data.get("legalName") or data.get("name")
            if name and out["company_name"]["value"] is None:
                out["company_name"] = {"value": _clean(str(name)), "where": f"json-ld {ent['type']}"}
            addr = data.get("address")
            if isinstance(addr, dict):
                parts = [str(addr.get(k)) for k in ("streetAddress", "postalCode", "addressLocality") if addr.get(k)]
                if parts and out["address"]["value"] is None:
                    out["address"] = {"value": ", ".join(parts), "where": f"json-ld {ent['type']}"}
            elif isinstance(addr, str) and out["address"]["value"] is None:
                out["address"] = {"value": _clean(addr), "where": f"json-ld {ent['type']}"}
            if data.get("telephone") and out["phone"]["value"] is None:
                out["phone"] = {"value": _clean(str(data["telephone"])), "where": f"json-ld {ent['type']}"}
            if data.get("email") and out["email"]["value"] is None:
                out["email"] = {"value": _clean(str(data["email"])), "where": f"json-ld {ent['type']}"}
            if data.get("taxID") or data.get("vatID"):
                code = re.search(r"\d{9}", str(data.get("taxID") or data.get("vatID")))
                if code and out["reg_code"]["value"] is None:
                    out["reg_code"] = {"value": code.group(0), "where": f"json-ld {ent['type']}"}
    where_of = lambda m, text: ("footer" if text is footer else "body") + f": “{_context(text, m.start(), m.end())}”"  # noqa: E731
    for text in (footer, body):
        if out["company_name"]["value"] is None:
            m = _COMPANY.search(text)
            if m:
                name = m.group(0)
                out["company_name"] = {"value": _clean(name), "where": where_of(m, text)}
        if out["reg_code"]["value"] is None:
            m = _REG_CODE.search(text)
            if m:
                out["reg_code"] = {"value": m.group(1), "where": where_of(m, text)}
        if out["address"]["value"] is None:
            m = _LT_ADDRESS.search(text) or _EN_ADDRESS.search(text)
            if m:
                out["address"] = {"value": _clean(m.group(1)), "where": where_of(m, text)}
        if out["email"]["value"] is None:
            m = _EMAIL.search(text)
            if m and not m.group(0).lower().endswith((".png", ".jpg", ".svg", ".webp")):
                out["email"] = {"value": m.group(0), "where": where_of(m, text)}
        if out["phone"]["value"] is None:
            m = _PHONE.search(text)
            if m and len(re.sub(r"\D", "", m.group(1))) >= 8:
                out["phone"] = {"value": _clean(m.group(1)), "where": where_of(m, text)}
        if out["named_person"]["value"] is None:
            for role in _ROLE.finditer(text):
                window = text[max(0, role.start() - 80): role.end() + 80]
                for pm in _PERSON.finditer(window):
                    words = [w for w in pm.group(1).split() if not _ROLE.fullmatch(w)]
                    if len(words) >= 2 and all(w.casefold() not in _NOT_PERSON for w in words) and not re.match(_BRAND_FORMS, words[0]):
                        out["named_person"] = {"value": " ".join(words), "where": ("footer" if text is footer else "body") + f": “{_clean(window)}”"}
                        break
                if out["named_person"]["value"]:
                    break
    if out["phone"]["value"] is None and page["tel_links"]:
        out["phone"] = {"value": page["tel_links"][0], "where": "a[href^=tel:]"}
    if out["email"]["value"] is None and page["mailto_links"]:
        out["email"] = {"value": page["mailto_links"][0], "where": "a[href^=mailto:]"}
    return out


def _absolute_claims(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    low = text.casefold()
    seen: set[str] = set()
    for phrase in ABSOLUTE_CLAIMS:
        for m in re.finditer(re.escape(phrase.casefold()), low):
            # whole-word guard for short alphabetic phrases such as 'never' or 'leading'
            if phrase[0].isalpha() and (m.start() > 0 and low[m.start() - 1].isalpha()):
                continue
            if phrase[-1].isalpha() and m.end() < len(low) and low[m.end()].isalpha() and phrase not in ("garantuoj", "vienintel", "leading"):
                continue
            seg_start = max(low.rfind(". ", 0, m.start()), low.rfind("! ", 0, m.start()), low.rfind("? ", 0, m.start()), m.start() - 90)
            seg_end_candidates = [i for i in (low.find(". ", m.end()), low.find("! ", m.end()), low.find("? ", m.end())) if i >= 0]
            seg_end = min(seg_end_candidates + [m.end() + 90, len(text)])
            sentence = _clean(text[seg_start + 1 if seg_start > 0 else 0: seg_end + 1])
            if sentence in seen:
                continue
            seen.add(sentence)
            out.append({"phrase": text[m.start(): m.end()], "text": sentence, "sourced": bool(_SOURCE_NEARBY.search(sentence))})
            if len(out) >= MAX_ELEMENTS:
                return out
    for m in _NUMBER_CLAIM.finditer(text):
        sentence = _clean(m.group(1))
        if sentence.casefold() in seen or any(sentence.casefold() in c["text"].casefold() for c in out):
            continue
        seen.add(sentence.casefold())
        around = _context(text, m.start(), m.end(), 80)
        out.append({"phrase": _clean(m.group(0))[:40], "text": sentence, "sourced": bool(_SOURCE_NEARBY.search(around))})
        if len(out) >= MAX_ELEMENTS:
            break
    return out


def _link_present(page: dict[str, Any], pattern: re.Pattern) -> str | None:
    for link in page["links"]:
        if pattern.search(link["href"]) or pattern.search(link["text"]):
            return link["href"]
    return None


def _credibility(page: dict[str, Any], identity: dict[str, dict[str, Any]], findings: list, clauses: list) -> dict[str, Any]:
    links = {
        "contact": _link_present(page, _CONTACT_LINK),
        "privacy": _link_present(page, _PRIVACY_LINK),
        "terms": _link_present(page, _TERMS_LINK),
        "about": _link_present(page, _ABOUT_LINK),
    }
    claims = _absolute_claims(page["body_text"])
    gaps: list[str] = []
    labels = {"company_name": "company name", "reg_code": "company registration code (įmonės kodas)", "address": "physical address", "phone": "phone number", "email": "e-mail address", "named_person": "a named person"}
    for key, label in labels.items():
        if identity[key]["value"] is None:
            gaps.append(key)
            findings.append(_finding(
                "credibility_gap", "warn", None, None, key, None,
                f"No {label} anywhere on the page. A visitor cannot tell who runs it or how to reach a person.",
            ))
    for key, label in (("contact", "contact page"), ("privacy", "privacy policy"), ("terms", "terms or rules")):
        if links[key] is None:
            gaps.append(f"{key}_link")
            findings.append(_finding("credibility_gap", "warn", None, None, f"{key}_link", None, f"No link to a {label} on the page."))
    for claim in claims:
        if not claim["sourced"]:
            findings.append(_finding(
                "unsupported_claim", "warn", None, None, "body", claim["text"],
                f"“{claim['phrase']}” is an absolute claim with no source beside it. Add the source or soften the wording.",
            ))
    if gaps:
        clauses.append(f"credibility gaps: {', '.join(gaps)} (warn)")
    else:
        clauses.append("identity, contact, privacy and terms all present")
    if claims:
        clauses.append(f"{sum(1 for c in claims if not c['sourced'])} unsourced absolute claims (warn)")
    return {"identity": identity, "links": links, "unsupported_claims": [c for c in claims if not c["sourced"]], "gaps": gaps}


# --- choice architecture and trust (#164) -------------------------------------------


def _numbers(text: str) -> list[float]:
    return [v for v in (parse_amount(n) for n in re.findall(r"\d+(?:[.,]\d+)?", text)) if v is not None]


def _choice_elements(page: dict[str, Any], findings: list, clauses: list) -> dict[str, Any]:
    body = page["body_text"]
    elements: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, text: str, context: str) -> None:
        text = _clean(text)
        if not text or (kind, text.casefold()) in seen or len(elements) >= MAX_ELEMENTS:
            return
        seen.add((kind, text.casefold()))
        elements.append({"kind": kind, "text": text, "context": context, "numeric_values": _numbers(text)})

    for label, where in [(b, "button") for b in page["buttons"]] + [(l["text"], "a") for l in page["links"]] + [(t, "a[href='#']") for t in page["navigation"]["dead_anchors"]]:
        if _CONFIRM_SHAME.search(label):
            add("confirm_shame", label, where)
    for kind, pattern in (("timer", _TIMER), ("scarcity", _SCARCITY), ("social_counter", _SOCIAL_COUNTER), ("late_fee", _LATE_FEE), ("confirm_shame", _CONFIRM_SHAME)):
        last_end = -1
        for m in pattern.finditer(body):
            if m.start() <= last_end:
                continue  # a second cue inside the first one's span, e.g. 'fee … added at checkout'
            last_end = m.end()
            text = _clean(m.group(1))
            if kind == "confirm_shame" and any(e["kind"] == "confirm_shame" and text.casefold()[:12] == e["text"].casefold()[:12] for e in elements):
                continue  # the same decline phrase already recorded from its button or link
            add(kind, text, _context(body, m.start(), m.end()))
    for ctrl in page["checked_controls"]:
        label = ctrl["label"] or ctrl["name"]
        if ctrl["type"] == "checkbox":
            add("preselected_addon", label, f"input[type=checkbox][checked] name={ctrl['name'] or '?'}")
        elif _PRICE.search(label) or re.search(r"plan|metin|mėnes|yearly|monthly|annual|year|month", label, re.IGNORECASE):
            add("default_plan", label, f"input[type=radio][checked] name={ctrl['name'] or '?'}")
    for option in page["selected_options"]:
        if _PRICE.search(option) or re.search(r"plan|metin|mėnes|yearly|monthly|annual|year|month", option, re.IGNORECASE):
            add("default_plan", option, "option[selected]")
    for block in page["tier_blocks"]:
        if block["highlighted"] and block["prices"]:
            name = block["headings"][0] if block["headings"] else block["text"][:60]
            add("default_plan", f"{name} {block['prices'][0]['text']}", "highlighted pricing tier")
    for claim in _absolute_claims(body):
        if not claim["sourced"]:
            add("absolute_claim", claim["text"], claim["phrase"])

    for el in elements:
        if el["kind"] == "absolute_claim":
            findings.append(_finding("unsupported_claim", "warn", None, None, "body", el["text"], f"“{el['context']}” is an absolute claim with no source beside it. Add the source or soften the wording."))
        else:
            notes = {
                "timer": "A countdown presses the buyer; keep it only if the deadline is real and shown as a date too.",
                "scarcity": "A stock or seat count presses the buyer; keep it only if it is fed by real stock, otherwise remove it.",
                "social_counter": "A live counter is unverifiable from the page; remove it unless a real-time data source feeds it.",
                "preselected_addon": "A pre-ticked box adds something the buyer did not choose; leave it unticked.",
                "default_plan": "A preselected plan steers the choice; show the full charge next to it.",
                "late_fee": "A fee named late or outside the headline price surprises the buyer at checkout; show it with the price.",
                "confirm_shame": "The decline option shames the reader; write it neutrally (e.g. 'Ne, ačiū').",
            }
            findings.append(_finding("dark_pattern", "warn", None, None, el["kind"], el["text"], notes[el["kind"]] + f" Context: “{el['context']}”."))
    kinds = sorted({e["kind"] for e in elements if e["kind"] != "absolute_claim"})
    if kinds:
        clauses.append(f"pressure cues: {', '.join(kinds)} (warn)")
    else:
        clauses.append("no pressure cues found")
    return {"elements": elements, "kinds": kinds, "note": "detected by text and form markup; JavaScript-rendered counters are not seen"}


# --- price ladder (#183) ----------------------------------------------------------------


def _period(text: str) -> str | None:
    if _PERIOD_MONTH.search(text):
        return "month"
    if _PERIOD_YEAR.search(text):
        return "year"
    return None


def _tiers_from_page(page: dict[str, Any]) -> list[dict[str, Any]]:
    blocks = [b for b in page["tier_blocks"] if b["prices"]]
    # Drop containers: a block whose text holds two or more other priced blocks' texts.
    keep: list[dict[str, Any]] = []
    for b in blocks:
        inner = sum(1 for o in blocks if o is not b and o["order"] > b["order"] and o["text"] and o["text"] in b["text"])
        if inner < 2:
            keep.append(b)
    tiers: list[dict[str, Any]] = []
    for b in keep:
        name = b["headings"][0] if b["headings"] else " ".join(b["text"].split()[:3])
        price = b["prices"][0]
        features = [f for f in b["features"] if not _PRICE.search(f) or len(f) > 40]
        tiers.append({"name": name, "price": price["value"], "price_text": price["text"], "period": _period(b["text"]), "features": features, "highlighted": b["highlighted"]})
    return tiers


def _normalise_tiers(raw: Any) -> list[dict[str, Any]]:
    tiers: list[dict[str, Any]] = []
    for i, t in enumerate(_as_list(raw)):
        if not isinstance(t, dict):
            raise ToolError("tiers: each tier needs name, price, period and features")
        price_raw = t.get("price")
        prices = find_prices(str(price_raw)) if price_raw not in (None, "") else []
        value = prices[0]["value"] if prices else (parse_amount(re.sub(r"[^\d ,.]", "", str(price_raw))) if price_raw not in (None, "") else None)
        if value is None:
            raise ToolError(f"tiers[{i}]: price '{price_raw}' is not a number")
        tiers.append({
            "name": _clean(str(t.get("name") or f"Tier {i + 1}")),
            "price": value,
            "price_text": _clean(str(price_raw)),
            "period": _period(str(t.get("period") or "")) or (str(t.get("period")).strip().lower() or None if t.get("period") else None),
            "features": [_clean(str(f)) for f in _as_list(t.get("features"))],
            "highlighted": bool(t.get("highlighted") or t.get("popular")),
        })
    return tiers


def _feature_kind(feature: str) -> str:
    if _SUPPORT_WORDS.search(feature):
        return "support"
    if _LIMIT_WORDS.search(feature) or re.search(r"\d", feature):
        return "limit"
    return "capability"


def _feature_key(feature: str) -> list[str]:
    return [w for w in _TOKEN.findall(feature.casefold()) if not w.isdigit() and len(w) >= 3 and w not in _STOPWORDS]


def _same_feature(a: str, b: str) -> bool:
    if a.casefold() == b.casefold():
        return True
    ka, kb = _feature_key(a), _feature_key(b)
    return bool(ka) and len(ka) == len(kb) and all(same_stem(x, y) for x, y in zip(ka, kb))


def _feature_in(feature: str, pool: list[str]) -> bool:
    return any(_same_feature(feature, p) for p in pool)


def _feature_diff(lower: list[str], higher: list[str]) -> tuple[list[str], list[str]]:
    """(added, removed): a feature whose words match but whose number grew is a raised limit, not a removal."""
    added: list[str] = []
    for f in higher:
        twin = next((l for l in lower if _same_feature(f, l)), None)
        if twin is None:
            added.append(f)
        elif twin.casefold() != f.casefold() and _numbers(twin) != _numbers(f):
            added.append(f"{f} (was {twin})")
    removed = [l for l in lower if not _feature_in(l, higher)]
    if any(_feature_kind(a) == "support" for a in added):
        removed = [r for r in removed if _feature_kind(r) != "support"]
    return added, removed


def _price_ladder(raw_tiers: Any, page: dict[str, Any], findings: list, clauses: list, missing: list) -> dict[str, Any]:
    source = "tiers"
    tiers = _normalise_tiers(raw_tiers) if raw_tiers else []
    if not tiers:
        tiers = _tiers_from_page(page)
        source = "page"
    if len(tiers) < 2:
        seen = [f"{t['name']} {t['price_text']}" for t in tiers]
        missing.append("price_ladder: fewer than two priced tiers found on the page (tiers_not_found); give tiers [{name, price, period, features}]" + (f"; seen: {', '.join(seen)}" if seen else ""))
        return {"status": "tiers_not_found", "tiers": tiers, "steps": [], "source": source, "assumptions": []}
    assumptions: list[str] = []
    periods = {t["period"] for t in tiers if t["period"]}
    unperioded = [t for t in tiers if not t["period"]]
    if len(periods) > 1 or ("year" in periods and unperioded):
        for t in tiers:
            t["monthly"] = round(t["price"] / 12, 2) if t["period"] == "year" else t["price"]
        assumptions.append("mixed periods: yearly prices divided by 12 to compare monthly")
        if unperioded:
            assumptions.append(f"tiers without a period assumed monthly: {', '.join(t['name'] for t in unperioded)}")
    else:
        for t in tiers:
            t["monthly"] = t["price"]
    ordered = sorted(tiers, key=lambda t: t["monthly"])
    if [t["name"] for t in ordered] != [t["name"] for t in tiers]:
        assumptions.append("tiers reordered by price ascending")
    steps: list[dict[str, Any]] = []
    for lower, higher in zip(ordered, ordered[1:]):
        added, removed = _feature_diff(lower["features"], higher["features"])
        kinds = {"capability": 0, "limit": 0, "support": 0}
        for f in added:
            kinds[_feature_kind(f)] += 1
        inc = round(higher["monthly"] - lower["monthly"], 2)
        pct = round(inc / lower["monthly"] * 100, 1) if lower["monthly"] else None
        codes: list[str] = []
        if added and kinds["capability"] == 0:
            codes.append("step_no_new_capability")
        if pct is not None and pct > 100:
            codes.append("step_increment_over_2x")
        if len(added) < 2:
            codes.append("step_increment_unexplained")
        if not added and not removed and lower["features"] and higher["features"]:
            codes.append("tier_feature_overlap")
        steps.append({"from": lower["name"], "to": higher["name"], "increment": inc, "increment_pct": pct, "added_features": added, "removed_features": removed, "added_kinds": kinds, "codes": codes})
    candidates: list[str] = []
    for i, step in enumerate(steps):
        if not step["added_features"]:
            findings.append(_finding(
                "ladder_illogical", "warn", "tiers", f"{step['from']} → {step['to']}", "price_ladder", f"+{step['increment']} (+{step['increment_pct']}%)",
                f"{step['to']} costs {step['increment']} more than {step['from']} and adds nothing the lower tier does not list" + (f"; it even drops: {', '.join(step['removed_features'])}" if step["removed_features"] else "") + ". A buyer cannot say what the extra money buys.",
            ))
            candidates.append(step["to"])
        elif i > 0 and step["increment"] < steps[i - 1]["increment"] and len(step["added_features"]) > len(steps[i - 1]["added_features"]):
            prev = steps[i - 1]
            findings.append(_finding(
                "ladder_illogical", "warn", "tiers", f"{prev['from']} → {prev['to']} → {step['to']}", "price_ladder", [prev["increment"], step["increment"]],
                f"{prev['to']} adds {len(prev['added_features'])} item(s) for +{prev['increment']}, then {step['to']} adds {len(step['added_features'])} for only +{step['increment']}. The middle step buys least per euro.",
            ))
            candidates.append(prev["to"])
        elif "step_no_new_capability" in step["codes"]:
            findings.append(_finding(
                "ladder_illogical", "warn", "tiers", f"{step['from']} → {step['to']}", "price_ladder", f"+{step['increment']} (+{step['increment_pct']}%)",
                f"{step['to']} adds only limits or support ({', '.join(step['added_features'])}) for {step['increment']} more; no new capability justifies the jump.",
            ))
            candidates.append(step["to"])
    if len(ordered) < 3:
        findings.append(_finding("ladder_anchor_missing", "warn", "tiers", [t["name"] for t in ordered], "price_ladder", len(ordered), "Only two tiers: there is no top tier to anchor the middle one against (good-better-best)."))
    elif source == "page" and not any(t["highlighted"] for t in ordered):
        findings.append(_finding("ladder_anchor_missing", "info", "tiers", [t["name"] for t in ordered], "price_ladder", None, "No tier is marked popular or recommended on the page; the buyer gets no anchor for which one to pick."))
    clauses.append(f"price ladder {len(ordered)} tiers, {len(candidates)} illogical step(s)" + (" (warn)" if candidates else ""))
    for t in ordered:
        t.pop("monthly", None)
    return {"status": "ok", "source": source, "tiers": ordered, "steps": steps, "redesign_candidates": list(dict.fromkeys(candidates)), "assumptions": assumptions}


# --- crawler-visible content (#323, #391) ----------------------------------------------


def _rendered_only_phrases(rendered: str, html_text: str, limit: int = 10) -> list[str]:
    r_tokens = _TOKEN.findall(rendered)
    h_tokens = set(_TOKEN.findall(html_text.casefold()))
    phrases: list[str] = []
    run: list[str] = []
    for tok in r_tokens:
        if tok.casefold() in h_tokens:
            if len(run) >= 4:
                phrases.append(" ".join(run))
            run = []
        else:
            run.append(tok)
    if len(run) >= 4:
        phrases.append(" ".join(run))
    return phrases[:limit]


def _crawler(args: dict, page: dict[str, Any], findings: list, clauses: list, missing: list) -> dict[str, Any]:
    rendered = _clean(str(args.get("rendered_text") or ""))
    facts = [_clean(str(f)) for f in _as_list(args.get("required_facts")) + _as_list(args.get("required_content"))]
    html_text = page["body_text"] + " " + " ".join(i["alt"] for i in page["images"] if i["alt"])
    out: dict[str, Any] = {
        "html_text_chars": len(page["body_text"]),
        "rendered_text_chars": len(rendered) if rendered else None,
        "headings": page["headings"],
        "images": [{"src": i["src"], "alt": i["alt"]} for i in page["images"]][:MAX_ELEMENTS],
        "images_without_alt": sum(1 for i in page["images"] if not i["alt"]),
        "iframes": page["iframes"],
        "rendered_only_phrases": _rendered_only_phrases(rendered, html_text) if rendered else None,
        "facts": [],
    }
    if not rendered:
        missing.append("crawler: no rendered_text given (browser_snapshot), so rendering was not compared with the raw HTML")
    if not facts:
        missing.append("crawler: no required_facts given; fact coverage not checked")
    for fact in facts:
        if _fact_found(fact, html_text):
            status, element = "html", _quote(page["body_text"], fact) or _quote(html_text, fact)
        elif rendered and _fact_found(fact, rendered):
            status, element = "rendered_only", _quote(rendered, fact)
        else:
            status, element = "missing", None
        out["facts"].append({"fact": fact, "status": status, "element": element})
        if status == "rendered_only":
            findings.append(_finding("fact_hidden_from_crawlers", "warn", "required_facts", fact, "rendered_text", element, "The fact appears only after rendering; a crawler reading the raw HTML does not see it. Put it in the HTML."))
        elif status == "missing":
            alt_less = [i["src"] for i in page["images"] if not i["alt"]]
            hint = f" Images without alt text on the page: {', '.join(alt_less[:5])}; if the fact sits in one of them, add it as text." if alt_less else " Add it as text or a table."
            findings.append(_finding("fact_missing", "block", "required_facts", fact, "body", None, "The fact is in neither the HTML nor the rendered text." + hint))
    if facts:
        n_missing = sum(1 for f in out["facts"] if f["status"] != "html")
        clauses.append(f"{len(facts) - n_missing} of {len(facts)} required facts in the HTML" + (f", {n_missing} hidden or missing (block)" if n_missing else ""))
    if out["rendered_only_phrases"]:
        clauses.append(f"{len(out['rendered_only_phrases'])} phrases visible only after rendering")
    return out


def _mobile(page: dict[str, Any], findings: list, clauses: list) -> dict[str, Any]:
    out = {"viewport_meta": page["viewport_meta"], "html_bytes": page["html_bytes"], "images": page["images_total"], "embeds": len(page["iframes"]), "note": "weight from the HTML alone; images and scripts not downloaded"}
    heavy = []
    if page["html_bytes"] > HEAVY_HTML_BYTES:
        heavy.append(f"{page['html_bytes']} bytes of HTML")
    if page["images_total"] > HEAVY_IMAGES:
        heavy.append(f"{page['images_total']} images")
    if heavy:
        findings.append(_finding("page_heavy", "warn", None, None, "page", out, f"{'; '.join(heavy)}: slow on a phone at the shelf or on 3G."))
        clauses.append("page heavy for mobile (warn)")
    if not page["viewport_meta"]:
        findings.append(_finding("no_viewport_meta", "warn", None, None, "meta[name=viewport]", None, "No viewport meta tag; phones render the page zoomed out."))
        clauses.append("no viewport meta (warn)")
    return out


# --- data use and consent (#315) -----------------------------------------------------------


def _host(url: str) -> str:
    try:
        return (urlparse(url if "//" in url else "//" + url).netloc or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def _trackers(page: dict[str, Any], base_url: str | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    page_host = _host(base_url) if base_url else ""
    seen: set[str] = set()

    def add(domain: str, vendor: str, kind: str, order: int, via: str) -> None:
        key = f"{vendor}|{domain}"
        if key in seen:
            return
        seen.add(key)
        out.append({"domain": domain, "vendor": vendor, "kind": kind, "order": order, "via": via})

    def match_url(url: str, order: int, via: str) -> None:
        low = url.lower()
        for needle, vendor, kind in TRACKER_VENDORS:
            if needle in low:
                add(_host(url) or needle, vendor, kind, order, via)
                return
        host = _host(url)
        if host and page_host and host != page_host and not host.endswith("." + page_host) and via in ("script", "iframe"):
            add(host, "unknown_third_party", "unknown", order, via)

    for s in page["scripts"]:
        if s["src"]:
            match_url(s["src"], s["order"], "script")
        elif s["inline"]:
            for pattern, vendor, kind in _INLINE_TRACKERS:
                if re.search(pattern, s["inline"]):
                    add("inline", vendor, kind, s["order"], "inline")
    for i, src in enumerate(page["iframes"]):
        match_url(src, 10**6 + i, "iframe")
    for img in page["images"]:
        if "facebook.com/tr" in img["src"].lower() or "google-analytics.com/collect" in img["src"].lower() or "ct.pinterest.com" in img["src"].lower():
            match_url(img["src"], img["order"], "img")
    return sorted(out, key=lambda t: t["order"])


def _policy_text(tools: WorkspaceTools | None, policy: Any, fetch_html: Callable[[str], str] | None, missing: list) -> str | None:
    if policy in (None, ""):
        return None
    policy = str(policy)
    if policy.startswith(("http://", "https://")):
        if fetch_html is None:
            missing.append(f"policy: {policy} not fetched (no fetcher here); give its text or a workspace path")
            return None
        try:
            fetched = fetch_html(policy)
        except Exception as exc:
            missing.append(f"policy: could not fetch {policy}: {exc}")
            return None
        if isinstance(fetched, bytes):
            fetched = fetched.decode("utf-8", errors="replace")
        return extract_page(str(fetched or ""))["body_text"]
    if tools is not None and len(policy) < 300 and "<" not in policy and "\n" not in policy:
        try:
            target = tools.resolve(policy)
        except ToolError:
            target = None
        if target is not None and target.is_file():
            text = target.read_text(encoding="utf-8", errors="replace")
            return extract_page(text)["body_text"] if "<" in text else text
        if re.search(r"\.(html?|md|txt)$", policy, re.IGNORECASE):
            missing.append(f"policy: path missing: {policy}")
            return None
    return extract_page(policy)["body_text"] if "<" in policy else policy


def _data_use(tools: WorkspaceTools | None, args: dict, page: dict[str, Any], base_url: str | None, fetch_html: Callable[[str], str] | None, findings: list, clauses: list, missing: list) -> dict[str, Any]:
    trackers = _trackers(page, base_url)
    consent_orders = list(page["consent_orders"])
    consent_vendor = next((t["vendor"] for t in trackers if t["kind"] == "consent"), None)
    consent_orders += [t["order"] for t in trackers if t["kind"] == "consent"]
    first_consent = min(consent_orders) if consent_orders else None
    live = [t for t in trackers if t["kind"] not in ("consent", "unknown")]
    forms = []
    for form in page["form_details"]:
        fields = [f["name"] for f in form["fields"]]
        forms.append({"action": form["action"], "fields": fields, "privacy_link_nearby": form["privacy_link_nearby"]})
        personal = [f for f in fields if _PERSONAL_FIELDS.search(f)]
        if personal and not form["privacy_link_nearby"]:
            findings.append(_finding(
                "no_privacy_link_at_form", "warn", None, None, f"form[action={form['action'] or '?'}]", fields,
                f"The form collects {', '.join(personal)} and has no privacy link inside it. Add one beside the submit button.",
            ))
    privacy_link = _link_present(page, _PRIVACY_LINK)
    if live:
        early = [t for t in live if first_consent is None or t["order"] < first_consent]
        if early:
            names = ", ".join(f"{t['vendor']} ({t['domain']})" for t in early)
            note = (
                f"{names} load with no consent banner anywhere in the HTML." if first_consent is None
                else f"{names} appear in the HTML before the consent banner markup, so they fire before a choice is made."
            ) + " Load trackers only after consent, or through a consent-aware tag manager."
            findings.append(_finding("tracker_before_consent", "warn", None, None, "scripts", [t["vendor"] for t in early], note))
            clauses.append(f"{len(early)} tracker(s) before consent (warn)")
    tag_manager = next((t for t in trackers if t["kind"] == "tag_manager"), None)
    if tag_manager:
        missing.append(f"data_use: {tag_manager['vendor']} present; tags it loads at runtime are not visible without a browser snapshot")
    policy_text = _policy_text(tools, args.get("policy"), fetch_html, missing)
    policy_out: dict[str, Any] = {"given": policy_text is not None, "recipients_named": [], "undisclosed": []}
    if policy_text is not None:
        low = policy_text.casefold()
        for t in live:
            words = _VENDOR_POLICY_WORDS.get(t["vendor"], (t["vendor"].casefold(),))
            if any(w.strip() in low for w in words):
                policy_out["recipients_named"].append(t["vendor"])
            else:
                policy_out["undisclosed"].append(t["vendor"])
                findings.append(_finding("undisclosed_recipient", "warn", "policy", None, t["domain"], t["vendor"], f"{t['vendor']} loads on the page but the privacy policy never names it."))
        collected = {f for form in forms for f in form["fields"] if _PERSONAL_FIELDS.search(f)}
        unexplained = []
        for field in sorted(collected):
            keys = {"phone": ("telefon", "phone"), "tel": ("telefon", "phone"), "kod": ("kodas", "code"), "email": ("el. pašt", "e-mail", "email", "elektronin"), "past": ("el. pašt", "e-mail", "email", "elektronin"), "vard": ("vard", "name"), "name": ("vard", "name"), "imon": ("įmon", "company"), "įmon": ("įmon", "company"), "adres": ("adres", "address")}
            words = next((v for k, v in keys.items() if k in field.casefold()), (field.casefold(),))
            if not any(w in low for w in words):
                unexplained.append(field)
        if unexplained:
            findings.append(_finding("unexplained_collection", "warn", "policy", None, "forms", unexplained, f"Forms collect {', '.join(unexplained)}; the policy does not mention these data."))
        policy_out["unexplained_collection"] = unexplained
        if policy_out["undisclosed"]:
            clauses.append(f"{len(policy_out['undisclosed'])} tracker(s) not in the policy (warn)")
    elif args.get("policy") in (None, ""):
        missing.append("data_use: no policy given, so disclosure was not compared")
    if not clauses or not any("tracker" in c for c in clauses):
        clauses.append(f"{len(live)} tracker(s), consent {'present' if first_consent is not None else 'absent'}")
    return {
        "trackers": [{k: v for k, v in t.items() if k != "order"} for t in trackers],
        "forms": forms,
        "consent": {"present": first_consent is not None, "vendor": consent_vendor, "texts": page["consent_texts"][:3]},
        "privacy_link": privacy_link,
        "policy": policy_out,
    }


# --- images off (#244) ----------------------------------------------------------------


def _images_off(ad: dict[str, Any], kind: str | None, page: dict[str, Any], findings: list, clauses: list) -> dict[str, Any]:
    text_only = page["body_text"] + " " + " ".join(i["alt"] for i in page["images"] if i["alt"])
    images = [{"src": i["src"], "alt": i["alt"], "inside_link": i["inside_link"]} for i in page["images"]]
    level = "block" if kind else "warn"
    for link in page["image_only_links"]:
        if link["all_without_alt"]:
            findings.append(_finding(
                "image_only_cta", level, None, None, f"a[href={link['href'] or '?'}] > img", [i["src"] for i in link["images"]],
                "The link's only content is an image without alt text; with images blocked there is no button to press. Add alt text or a text link.",
            ))
    alt_less = [i["src"] for i in page["images"] if not i["alt"]]
    if alt_less:
        heading = page["h1"][0] if page["h1"] else (page["title"] or "")
        findings.append(_finding("missing_alt", "warn", None, None, "img", alt_less[:10], f"{len(alt_less)} image(s) have no alt text; suggested alt from the heading: “{heading[:80]}”." if heading else f"{len(alt_less)} image(s) have no alt text."))
    essentials: dict[str, Any] = {}
    for field in ("offer", "deadline", "cta"):
        value = _clean(ad.get(field) or "")
        if not value:
            continue
        tokens = _fact_tokens(value)
        in_text = bool(tokens) and len(_tokens_in_text(tokens, page["body_text"] + " " + " ".join(page["buttons"]))) == len(tokens)
        in_alt = (not in_text) and bool(tokens) and len(_tokens_in_text(tokens, " ".join(i["alt"] for i in page["images"] if i["alt"]))) == len(tokens)
        essentials[field] = {"found": in_text or in_alt, "where": "text" if in_text else ("alt" if in_alt else "none")}
        if not in_text and not in_alt and page["images"]:
            findings.append(_finding("essential_in_image", level, field, value, "body", None, f"The {field} “{value}” appears in no text and no alt; if it sits in an image it vanishes when images are blocked."))
    if not page["images"]:
        findings.append(_finding("no_images", "info", None, None, "img", 0, "The message has no images, so nothing is lost with images blocked."))
    if alt_less or any(l["all_without_alt"] for l in page["image_only_links"]):
        clauses.append(f"{len(alt_less)} image(s) without alt" + (", image-only link" if any(l["all_without_alt"] for l in page["image_only_links"]) else "") + " (warn)")
    return {"text_only": text_only[:2000], "images": images[:MAX_ELEMENTS], "essentials": essentials}


# --- transactional email completeness (#157) --------------------------------------------


def _message(kind: str, expected: Any, brand: str, page: dict[str, Any], findings: list, clauses: list, missing: list) -> dict[str, Any]:
    if not isinstance(expected, dict):
        raise ToolError("expected must be an object: {amount, order_ref, items[]}")
    body = page["body_text"]
    fields: dict[str, Any] = {}
    # amount: a labelled total first, otherwise the largest amount
    total = None
    for p in page["prices"]:
        before = p["context"].casefold().split(p["text"].casefold())[0] if p["text"].casefold() in p["context"].casefold() else p["context"].casefold()
        if _TOTAL_WORDS.search(before[-45:]):
            total = p
            break
    if total is None and page["prices"]:
        total = max(page["prices"], key=lambda p: p["value"])
    fields["amount"] = {"found": total is not None, "value": total["value"] if total else None, "text": total["text"] if total else None, "labelled": bool(total and _TOTAL_WORDS.search(total["context"]))}
    # order reference
    ref = None
    for m in _ORDER_REF.finditer(body + " " + (page["title"] or "") + " " + (page["subject_line"] or "")):
        token = m.group(1)
        if sum(ch.isdigit() for ch in token) >= 3 and not _PRICE.search(m.group(0)):
            ref = token
            break
    fields["order_ref"] = {"found": ref is not None, "value": ref}
    # delivery
    delivery: dict[str, Any] = {"found": False, "kind": None, "value": None}
    for link in page["links"]:
        if _TRACKING_HREF.search(link["href"]) or _TRACKING_HREF.search(link["text"]):
            delivery = {"found": True, "kind": "tracking_url", "value": link["href"]}
            break
    if not delivery["found"]:
        for m in _DELIVERY_WORDS.finditer(body):
            phrase = _clean(m.group(0))
            has_date = bool(re.search(r"\d{1,2}[./-]\d{1,2}|\d+\s?(?:d\.|dien|day|val|hour|darbo)|sausio|vasario|kovo|balandžio|gegužės|birželio|liepos|rugpjūčio|rugsėjo|spalio|lapkričio|gruodžio|january|february|march|april|may|june|july|august|september|october|november|december|rytoj|tomorrow|šiandien|today", phrase, re.IGNORECASE))
            has_how = bool(re.search(r"paštomat|pastomat|kurjer|courier|atsiėm|atsiim|pickup|pick-up|parduotuv|store|adres|address|omniva|dpd|lp express|venipak|siunt|shipped|dispatched|išsiųs|gausite|arrive|track|sek", phrase, re.IGNORECASE))
            if has_date:
                delivery = {"found": True, "kind": "date", "value": phrase[:160]}
                break
            if has_how and not delivery["found"]:
                delivery = {"found": True, "kind": "instructions", "value": phrase[:160]}
    fields["delivery"] = delivery
    # items
    expected_items = [_clean(str(i)) for i in _as_list(expected.get("items"))]
    if expected_items:
        values = [i for i in expected_items if _fact_found(i, body)]
        fields["items"] = {"found": len(values) == len(expected_items), "values": values, "missing": [i for i in expected_items if i not in values]}
    else:
        detected = [_clean(m.group(1)) for m in _LINE_ITEM.finditer(body)]
        fields["items"] = {"found": bool(detected), "values": detected[:20], "missing": []}
    # support contact
    identity = _identity(page)
    support = identity["email"]["value"] or identity["phone"]["value"]
    fields["support_contact"] = {"found": support is not None, "value": support}
    # next step and unsubscribe (welcome)
    fields["next_step"] = {"found": bool(page["buttons"]), "value": page["buttons"][0] if page["buttons"] else None}
    unsub = next((l["href"] for l in page["links"] if _UNSUBSCRIBE.search(l["text"]) or _UNSUBSCRIBE.search(l["href"])), None)
    fields["unsubscribe"] = {"found": unsub is not None, "value": unsub}
    brand_found = bool(brand) and any(same_stem(w, p) for w in _WORD.findall(brand.casefold()) for p in _WORD.findall((body + " " + (page["title"] or "")).casefold()))
    fields["brand"] = {"found": brand_found if brand else None, "value": brand or None}
    fields["subject"] = page["subject_line"] or page["title"]

    required = {
        "receipt": (("items", "block"), ("amount", "block"), ("order_ref", "block"), ("delivery", "warn"), ("support_contact", "warn")),
        "shipping": (("order_ref", "block"), ("delivery", "block"), ("items", "warn"), ("support_contact", "warn")),
        "welcome": (("next_step", "block"), ("support_contact", "warn"), ("unsubscribe", "warn")),
    }[kind]
    labels = {"items": "the items bought", "amount": "the amount paid", "order_ref": "an order reference", "delivery": "delivery or tracking information", "support_contact": "a support e-mail or phone", "next_step": "a next-step button or link", "unsubscribe": "an unsubscribe link"}
    absent: list[str] = []
    for name, level in required:
        if not fields[name]["found"]:
            absent.append(name)
            findings.append(_finding("email_missing_field", level, "kind", kind, name, None, f"A {kind} email must carry {labels[name]}; none found in the message text." + (" Expected items not found: " + ", ".join(fields["items"]["missing"]) + "." if name == "items" and fields["items"].get("missing") else "")))
    if kind == "shipping" and fields["delivery"]["found"] and fields["delivery"]["kind"] != "tracking_url":
        findings.append(_finding("email_missing_field", "warn", "kind", kind, "delivery", fields["delivery"]["value"], "The shipping email names delivery but has no tracking link."))
    if kind == "welcome" and brand and not brand_found:
        absent.append("brand")
        findings.append(_finding("email_missing_field", "block", "kind", kind, "brand", brand, f"The welcome email never names {brand}."))
    # mismatches against what the test order expected
    exp_amount = expected.get("amount", expected.get("amount_eur"))
    if exp_amount not in (None, ""):
        value = parse_amount(re.sub(r"[^\d ,.]", "", str(exp_amount)))
        if value is not None and total is not None and abs(total["value"] - value) >= 0.005:
            findings.append(_finding("email_field_mismatch", "block", "expected.amount", value, "amount", total["text"], f"The test order paid {value}; the email says {total['text']} (“{total['context']}”)."))
        elif value is None:
            missing.append(f"expected.amount '{exp_amount}' is not a number")
    exp_ref = _clean(str(expected.get("order_ref") or ""))
    if exp_ref and ref and exp_ref.casefold() != ref.casefold() and exp_ref.casefold() not in body.casefold():
        findings.append(_finding("email_field_mismatch", "block", "expected.order_ref", exp_ref, "order_ref", ref, f"The test order was {exp_ref}; the email shows {ref}."))
    score = round(1 - len(absent) / len(required), 2) if required else 1.0
    clauses.append(f"{kind} email: {len(required) - len([a for a in absent if a != 'brand'])}/{len(required)} fields" + (f", missing {', '.join(absent)}" if absent else "") + (" (block)" if any(f["code"] == "email_missing_field" and f["level"] == "block" for f in findings) else ""))
    return {"kind": kind, "fields": fields, "missing_fields": absent, "score": score}


# --- structured data (#329) -------------------------------------------------------------------


def _structured_data(schema_arg: Any, page: dict[str, Any], findings: list, clauses: list, missing: list) -> dict[str, Any]:
    parsed = page["jsonld"]
    entities = list(parsed["entities"])
    errors = list(parsed["errors"])
    if schema_arg not in (None, ""):
        raw = schema_arg if isinstance(schema_arg, str) else json.dumps(schema_arg)
        more = _parse_jsonld([raw])
        entities += more["entities"]
        errors += more["errors"]
    for err in errors:
        findings.append(_finding("schema_invalid_json", "block", "schema", None, "script[type=application/ld+json]", err, f"The JSON-LD does not parse: {err}."))
    body = page["body_text"]
    body_numbers = {parse_amount(n) for n in re.findall(r"\d+(?:[.,]\d+)?", body)}
    blocks: list[dict[str, Any]] = []
    for ent in entities:
        data = ent["data"]
        fields: dict[str, Any] = {}
        if data.get("name"):
            fields["name"] = _clean(str(data["name"]))
        if data.get("price") not in (None, ""):
            fields["price"] = parse_amount(str(data["price"]))
            fields["currency"] = data.get("priceCurrency")
        if data.get("ratingValue") not in (None, ""):
            fields["ratingValue"] = parse_amount(str(data["ratingValue"]))
        for key in ("reviewCount", "ratingCount"):
            if data.get(key) not in (None, ""):
                fields[key] = parse_amount(str(data[key]))
        if data.get("availability"):
            fields["availability"] = str(data["availability"]).rsplit("/", 1)[-1]
        if not fields:
            continue
        blocks.append({"type": ent["type"], "fields": fields})
        for key, value in fields.items():
            if key in ("currency", "availability") or value is None:
                continue
            if key == "name":
                visible = _fact_found(value, body + " " + (page["title"] or ""))
            elif key == "price":
                visible = any(abs(p["value"] - value) < 0.005 for p in page["prices"]) or value in body_numbers
            else:
                visible = value in body_numbers
            if visible:
                findings.append(_finding("schema_matches_visible", "info", "schema", f"{ent['type']}.{key}", "body", value, "The markup value appears in the visible text."))
            else:
                shown = {"price": [p["text"] for p in page["prices"][:4]], "ratingValue": [m.group(1) for m in _REVIEW_TEXT.finditer(body) if m.group(1)][:3]}.get(key)
                findings.append(_finding("schema_field_not_visible", "block", "schema", f"{ent['type']}.{key}={value}", "body", shown, f"The markup claims {key} {value}; the visible page shows {shown or 'no such value'}. Search engines treat this as misleading markup."))
    has_offer = any("offer" in b["type"].lower() or "price" in b["fields"] for b in blocks)
    has_rating = any("rating" in b["type"].lower() or "ratingValue" in b["fields"] for b in blocks)
    candidates: list[str] = []
    if page["prices"] and not has_offer:
        candidates.append("Offer (visible price " + page["prices"][0]["text"] + ")")
    review = _REVIEW_TEXT.search(body)
    if review and not has_rating:
        candidates.append("AggregateRating (visible " + _clean(review.group(0)) + ")")
    for c in candidates:
        findings.append(_finding("schema_missing_for_entity", "warn", "schema", None, "body", c, "The page shows this visibly but has no structured data for it; stars or price cannot appear in search results."))
    if not entities and not errors:
        missing.append("schema: no JSON-LD on the page" + (f"; candidates: {', '.join(candidates)}" if candidates else ""))
    n_bad = sum(1 for f in findings if f["code"] in ("schema_field_not_visible", "schema_invalid_json"))
    clauses.append(f"structured data: {len(blocks)} block(s), {n_bad} mismatch(es)" + (" (block)" if n_bad else ""))
    return {"present": bool(entities), "blocks": blocks, "errors": errors, "candidates": candidates}


# --- navigation conventions (#149, #160) ----------------------------------------------------------


def _navigation(page: dict[str, Any], base_url: str | None, findings: list, clauses: list) -> dict[str, Any]:
    nav = page["navigation"]
    root_ok = False
    home = nav["home_link"]
    if home is not None:
        target = urljoin(base_url, home) if base_url else home
        path = urlparse(target).path if "//" in target else target
        root_ok = path in ("", "/", "/index.html", "/index.php") or home.strip() in ("/", "./", ".") or (base_url is not None and _host(target) == _host(base_url) and path.strip("/") == "")
    out = {
        "home_link": {"pass": root_ok, "href": home},
        "cart_link": {"pass": nav["cart_link"] is not None, "href": nav["cart_link"]},
        "menu": {"pass": nav["has_nav"] and nav["nav_links"] >= 3, "nav_links": nav["nav_links"], "control": nav["menu_control"]},
        "search": {"present": any(f["type"] == "search" or re.search(r"search|paiešk|ieškoti|^q$", f["name"], re.IGNORECASE) for form in page["form_details"] for f in form["fields"])},
        "dead_anchors": nav["dead_anchors"][:MAX_ELEMENTS],
        "note": "convention check by markup, not a user test; menus are not clicked and cursors are not seen",
    }
    if not root_ok:
        findings.append(_finding("nav_home_link_missing", "warn", None, None, "header logo", home, "No logo or brand link points to the site root; a lost visitor cannot get home by the usual click."))
    if not out["menu"]["pass"]:
        findings.append(_finding("nav_menu_weak", "warn", None, None, "nav", nav["nav_links"], f"The navigation landmark holds {nav['nav_links']} link(s); first-time visitors expect a menu with at least three."))
    for text in nav["dead_anchors"][:10]:
        findings.append(_finding("dead_click_target", "info", None, None, "a[href='#']", text, "This link has no destination; it looks clickable and does nothing without JavaScript."))
    fails = [k for k in ("home_link", "cart_link", "menu") if not out[k]["pass"]]
    clauses.append("navigation conventions: " + (f"{', '.join(fails)} fail (warn)" if fails else "home, cart and menu pass"))
    return out


# --- promise ledger (#392) ----------------------------------------------------------------


def _promises(page: dict[str, Any], source: str, missing: list) -> list[dict[str, Any]]:
    body = page["body_text"]
    out: list[dict[str, Any]] = []
    if page["prices"]:
        p = page["prices"][0]
        out.append({"kind": "price", "value": p["value"], "unit": "€" if "€" in p["text"] or "EUR" in p["text"] else p["text"][0], "quote": p["context"], "url": source})
    for kind, pattern in (("pickup_time", _PICKUP_TIME), ("delivery_time", _DELIVERY_TIME), ("stock", _STOCK), ("warranty", _WARRANTY)):
        m = pattern.search(body)
        if m:
            quote = _clean(m.group(1) if m.lastindex else m.group(0))
            nums = _numbers(quote)
            unit = None
            if kind != "stock":
                u = re.search(r"val|h\b|hour|min|d\.|dien|day|darbo|met|year|mėn|month", quote, re.IGNORECASE)
                unit = {"val": "hours", "h": "hours", "hour": "hours", "min": "minutes", "d.": "days", "dien": "days", "day": "days", "darbo": "working days", "met": "years", "year": "years", "mėn": "months", "month": "months"}.get(u.group(0).lower(), u.group(0)) if u else None
            out.append({"kind": kind, "value": nums[0] if nums and kind != "stock" else quote, "unit": unit, "quote": _context(body, m.start(), m.end()), "url": source})
    m = _RETURN_WINDOW.search(body)
    if m:
        out.append({"kind": "return_window", "value": parse_amount(m.group(1) or m.group(2)), "unit": "days", "quote": _context(body, m.start(), m.end()), "url": source})
    payments = sorted({_clean(m.group(0)) for m in _PAYMENT_WORDS.finditer(body + " " + " ".join(i["alt"] or "" for i in page["images"]))})
    if payments:
        out.append({"kind": "payment", "value": payments, "unit": None, "quote": ", ".join(payments), "url": source})
    found = {p["kind"] for p in out}
    absent = [k for k in ("price", "pickup_time", "delivery_time", "stock", "return_window", "payment", "warranty") if k not in found]
    if absent:
        missing.append(f"promises not found on the page: {', '.join(absent)}")
    return out


# --- trust signals (#398) --------------------------------------------------------------------


def _trust_signals(page: dict[str, Any], identity: dict[str, dict[str, Any]], findings: list, clauses: list) -> dict[str, Any]:
    body = page["body_text"]
    reviews: dict[str, Any] = {"present": False, "provider": None, "count": None, "score": None}
    for ent in page["jsonld"]["entities"]:
        d = ent["data"]
        if "aggregaterating" in ent["type"].lower() or isinstance(d.get("aggregateRating"), dict):
            r = d if "aggregaterating" in ent["type"].lower() else d["aggregateRating"]
            reviews = {"present": True, "provider": "schema.org", "count": parse_amount(str(r.get("reviewCount") or r.get("ratingCount") or "")) , "score": parse_amount(str(r.get("ratingValue") or ""))}
            break
    if not reviews["present"]:
        m = _REVIEW_TEXT.search(body)
        if m:
            reviews = {"present": True, "provider": None, "count": parse_amount(m.group(2) or "") if m.group(2) else None, "score": parse_amount(m.group(1) or "") if m.group(1) else None}
    for s in page["scripts"] + [{"src": i} for i in page["iframes"]]:
        src = (s.get("src") or "").lower()
        for prov in ("trustpilot", "google.com/maps", "yotpo", "judge.me", "reviews.io", "atsiliepimai"):
            if prov in src:
                reviews["present"] = True
                reviews["provider"] = reviews["provider"] or prov
    m = _RETURN_WINDOW.search(body)
    returns = {"present": m is not None, "excerpt": _context(body, m.start(), m.end()) if m else None, "days": parse_amount(m.group(1) or m.group(2)) if m else None}
    company = {"present": identity["company_name"]["value"] is not None or identity["reg_code"]["value"] is not None, "excerpt": identity["company_name"]["value"] or identity["reg_code"]["value"]}
    badges = sorted({_clean(m.group(0)) for m in _PAYMENT_WORDS.finditer(body + " " + " ".join(i["alt"] or "" for i in page["images"]))})
    comparison = [l["href"] for l in page["links"] if _EVALUATION.search(l["text"]) and re.search(r"palygin|compar", l["text"] + l["href"], re.IGNORECASE)][:10]
    out = {"reviews": reviews, "returns_policy": returns, "company_identity": company, "payment_badges": badges, "comparison_links": comparison}
    absent = []
    for key, label in (("reviews", "a review widget or review count"), ("returns_policy", "a returns or guarantee statement with a day count"), ("company_identity", "company name or code")):
        if not out[key]["present"]:
            absent.append(key)
            findings.append(_finding("trust_signal_missing", "warn", None, None, key, None, f"No {label} on the page; the trust a retailer's shelf used to give is replaced by nothing."))
    if not badges:
        absent.append("payment_badges")
        findings.append(_finding("trust_signal_missing", "info", None, None, "payment_badges", None, "No payment method names or badges on the page."))
    clauses.append("trust signals: " + (f"missing {', '.join(absent)} (warn)" if absent else "reviews, returns, identity and payment present"))
    return out


# --- product-use touchpoint (#268) ----------------------------------------------------------


def _touchpoint(brand: str, page: dict[str, Any], findings: list, clauses: list, missing: list) -> dict[str, Any]:
    if not brand:
        missing.append("touchpoint: give product_name to check that the artefact names the product")
        return {"brand_present": None}
    body = page["body_text"]
    brand_words = _WORD.findall(brand.casefold())
    low = body.casefold()
    positions = [m.start() for m in re.finditer(re.escape(brand_words[0]), low)] if brand_words else []
    alt_hits = sum(1 for i in page["images"] if i["alt"] and brand_words and brand_words[0] in i["alt"].casefold())
    mentions = len(positions) + alt_hits
    footer_only = bool(positions) and all(p > len(low) * 0.8 for p in positions) or (not positions and alt_hits > 0 and brand_words[0] in page["footer_text"].casefold())
    if not positions and alt_hits:
        footer_only = brand_words[0] in page["footer_text"].casefold() or len(page["footer_text"]) == 0
    value_line = None
    candidates: list[str] = []
    for para in page["paragraphs"] + [h["text"] for h in page["headings"]]:
        if brand_words and brand_words[0] in para.casefold():
            candidates.extend(re.split(r"(?<=[.!?])\s+", para))
    for pos in positions:
        start = max(low.rfind(". ", 0, pos), low.rfind("! ", 0, pos), 0)
        end_candidates = [i for i in (low.find(". ", pos), low.find("! ", pos)) if i >= 0]
        end = min(end_candidates + [len(body)])
        candidates.append(_clean(body[start + 1 if start else 0: end + 1]))
    for sentence in candidates:
        if brand_words and brand_words[0] not in sentence.casefold():
            continue
        if len(_WORD.findall(sentence)) >= 6 and (_TRY_WORDS.search(sentence) or re.search(r"išrašyt|sent|siųst|sukurta|created|powered|padėjo|helps|lets|leidžia|greičiau|faster|paprasč|easier|be\b|without|per\b|via\b", sentence, re.IGNORECASE)):
            value_line = _clean(sentence)
            break
    try_links = []
    for l in page["links"]:
        host = _host(l["href"])
        if (brand_words and any(w in host for w in brand_words)) or _TRY_WORDS.search(l["text"]):
            params = dict(parse_qsl(urlparse(l["href"]).query)) if "?" in l["href"] else {}
            tracked = any(k.lower().startswith(_TRACK_PARAMS) for k in params)
            try_links.append({"href": l["href"], "text": l["text"], "tracked": tracked, "param": next((k for k in params if k.lower().startswith(_TRACK_PARAMS)), None)})
    out = {"brand_present": mentions > 0, "brand_mentions": mentions, "footer_only": footer_only, "value_line": value_line, "try_links": try_links[:10]}
    if mentions == 0:
        findings.append(_finding("touchpoint_no_brand", "block", "product_name", brand, "body", None, f"The recipient-facing artefact never names {brand}; nobody who sees it learns what made it."))
    if value_line is None:
        findings.append(_finding("touchpoint_no_value", "warn", "product_name", brand, "body", None, f"No sentence tells the recipient what {brand} does for them; add one value line in their words."))
    if not try_links:
        findings.append(_finding("touchpoint_no_route", "warn", "product_name", brand, "links", None, "No link offers the recipient a way to try the product."))
    elif not any(l["tracked"] for l in try_links):
        findings.append(_finding("touchpoint_untracked", "warn", "product_name", brand, "links", [l["href"] for l in try_links[:3]], "The try link carries no utm or ref parameter, so sign-ups from this touchpoint cannot be counted."))
    clauses.append(f"touchpoint: brand {'present' if mentions else 'absent'}, {'value line' if value_line else 'no value line'}, {len(try_links)} try link(s)" + (" (block)" if mentions == 0 else ""))
    return out


# --- explore and evaluate on a listing (#88) --------------------------------------------------


def _listing(page: dict[str, Any], findings: list, clauses: list) -> dict[str, Any]:
    body = page["body_text"]
    discovery = []
    evaluation = []
    for l in page["links"]:
        if _DISCOVERY.search(l["text"]):
            discovery.append({"kind": "guide_link", "text": l["text"], "href": l["href"]})
        if re.search(r"palygin|compare", l["text"], re.IGNORECASE):
            evaluation.append({"kind": "compare_link", "text": l["text"], "href": l["href"]})
    for h in page["headings"]:
        if _DISCOVERY.search(h["text"]):
            discovery.append({"kind": "explainer_block", "text": h["text"]})
    for form in page["form_details"]:
        names = " ".join(f["name"] for f in form["fields"])
        if re.search(r"filter|filtr|sort|rikiuo|rūšiuo|price_min|price_max|kaina_nuo|brand|gamintoj", names, re.IGNORECASE):
            evaluation.append({"kind": "spec_filter", "text": names[:120]})
    for ctrl in page["checked_controls"]:
        if re.search(r"compare|palygin", ctrl["label"] + ctrl["name"], re.IGNORECASE):
            evaluation.append({"kind": "compare_checkbox", "text": ctrl["label"]})
    for m in _REVIEW_TEXT.finditer(body):
        evaluation.append({"kind": "review_block", "text": _clean(m.group(0))})
        break
    if re.search(r"specifikacij|specifications?|palyginimo lentel|comparison table", body, re.IGNORECASE):
        evaluation.append({"kind": "comparison_table", "text": _quote(body, "palyginimo lentelė") or _quote(body, "comparison table") or _quote(body, "specifikacijos") or "specifications"})
    for mode, items, label in (("discovery", discovery, "nothing helps a first-time buyer learn what matters (no guide link, explainer or tips)"), ("evaluation", evaluation, "nothing helps a buyer narrow down (no filters, compare control, reviews or spec table)")):
        if not items:
            findings.append(_finding("listing_mode_missing", "warn", None, None, mode, [], f"On this listing {label}."))
    clauses.append(f"listing: {len(discovery)} discovery, {len(evaluation)} evaluation element(s)" + (" (warn)" if not discovery or not evaluation else ""))
    return {"discovery": discovery[:MAX_ELEMENTS], "evaluation": evaluation[:MAX_ELEMENTS], "note": "counted from markup and text; selectors and positions need a browser"}


# --- top tasks in the first screen (#148) ---------------------------------------------------------


def _top_tasks(page: dict[str, Any], identity: dict[str, dict[str, Any]], findings: list, clauses: list) -> dict[str, Any]:
    first = page["first_screen_text"]
    body = page["body_text"]
    tasks: list[dict[str, Any]] = []

    def status(value: str | None, pattern: re.Pattern | None = None) -> tuple[str, str | None]:
        if value is None and pattern is None:
            return "absent", None
        if value is not None:
            if value.casefold() in first.casefold():
                return "first_screen", value
            if value.casefold() in body.casefold():
                return "below", value
            return "first_screen" if pattern is None else ("below", value)[0], value
        m = pattern.search(first) if pattern else None
        if m:
            return "first_screen", _clean(m.group(0))
        m = pattern.search(body) if pattern else None
        if m:
            return "below", _clean(m.group(0))
        return "absent", None

    for task, value, pattern in (("address", identity["address"]["value"], None), ("phone", identity["phone"]["value"], None), ("hours", None, _HOURS)):
        st, ev = status(value, pattern)
        tasks.append({"task": task, "status": st, "evidence": ev})
    map_link = next((l["href"] for l in page["links"] if _MAP_HREF.search(l["href"])), None)
    tasks.append({"task": "map", "status": "below" if map_link else "absent", "evidence": map_link})
    cta = page["first_screen_actions"][0] if page["first_screen_actions"] else (page["buttons"][0] if page["buttons"] else None)
    tasks.append({"task": "cta", "status": "first_screen" if page["first_screen_actions"] else ("below" if page["buttons"] else "absent"), "evidence": cta})
    for t in tasks:
        if t["status"] == "absent":
            findings.append(_finding("top_task_missing", "warn", None, None, t["task"], None, f"A visitor from a local search looks for the {t['task']}; the page has none."))
        elif t["status"] == "below" and t["task"] in ("address", "phone", "hours"):
            findings.append(_finding("top_task_below_fold", "info", None, None, t["task"], t["evidence"], f"The {t['task']} appears only after the first {FIRST_SCREEN_CHARS} characters of text; move it into the header."))
    absent = [t["task"] for t in tasks if t["status"] == "absent"]
    clauses.append("top tasks: " + (f"{', '.join(absent)} absent (warn)" if absent else "address, phone, hours, map and CTA present"))
    return {"tasks": tasks, "note": "position by text order (first 600 characters), not by pixel fold"}


# --- links (#150, #161) and UTM tags (#485) ----------------------------------------------------------


def _call_meta(fetch_meta: Callable[[str], dict[str, Any]], url: str) -> dict[str, Any]:
    try:
        meta = fetch_meta(url) or {}
    except Exception as exc:
        return {"status": None, "final_url": url, "headers": {}, "error": str(exc)}
    if not isinstance(meta, dict):
        return {"status": None, "final_url": url, "headers": {}, "error": "fetch_meta returned no mapping"}
    status = meta.get("status")
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    hops = meta.get("redirects")
    if isinstance(hops, list):
        hops = len(hops)
    return {"status": status, "final_url": str(meta.get("final_url") or url), "headers": meta.get("headers") or {}, "redirects": hops, "error": meta.get("error")}


def _links(args: dict, page: dict[str, Any], base_url: str | None, fetch_meta: Callable[[str], dict[str, Any]] | None, findings: list, clauses: list, missing: list) -> dict[str, Any] | None:
    try:
        max_links = int(args.get("max_links") or MAX_LINKS)
    except (TypeError, ValueError):
        raise ToolError("max_links must be a whole number") from None
    page_host = _host(base_url) if base_url else ""
    internal: list[dict[str, Any]] = []
    external = 0
    seen: set[str] = set()
    for l in page["links"]:
        href = l["href"]
        if href.startswith(("#", "javascript:", "data:")):
            continue
        absolute = urljoin(base_url, href) if base_url else href
        host = _host(absolute) if "//" in absolute else ""
        is_internal = (host == page_host or host.endswith("." + page_host)) if page_host else ("//" not in absolute)
        if not is_internal:
            external += 1
            continue
        key = absolute.split("#", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        internal.append({"href": key, "text": l["text"]})
    out: dict[str, Any] = {"internal_total": len(internal), "external_total": external, "checked": 0, "broken": [], "redirects": [], "unreachable": []}
    if fetch_meta is None:
        missing.append(f"links: {len(internal)} internal link(s) listed but not checked (no fetch_meta here)")
        out["status"] = "not_checked"
        out["internal"] = internal[:max_links]
        return out
    if not base_url and any("//" not in l["href"] for l in internal):
        missing.append("links: relative hrefs cannot be resolved without url or base_url")
        internal = [l for l in internal if "//" in l["href"]]
    for l in internal[:max_links]:
        meta = _call_meta(fetch_meta, l["href"])
        out["checked"] += 1
        if meta["status"] is None:
            out["unreachable"].append({**l, "error": meta.get("error")})
            continue
        if meta["status"] >= 400:
            out["broken"].append({**l, "status": meta["status"]})
            findings.append(_finding(
                "broken_link", "block" if meta["status"] >= 500 else "warn", None, None, f"a[href={l['href']}]", l["text"],
                f"“{l['text'] or l['href']}” → {l['href']} answers {meta['status']}. Fix the link or the page it points to.",
            ))
        elif meta["final_url"] and meta["final_url"].rstrip("/") != l["href"].rstrip("/"):
            hops = meta.get("redirects") if meta.get("redirects") is not None else 1
            out["redirects"].append({**l, "final_url": meta["final_url"], "hops": hops})
            if hops and hops >= 2:
                findings.append(_finding("redirect_chain", "info", None, None, f"a[href={l['href']}]", meta["final_url"], f"{hops} redirects before {meta['final_url']}; link straight to the final address."))
    if len(internal) > max_links:
        missing.append(f"links: {len(internal) - max_links} internal link(s) beyond max_links={max_links} not checked")
    out["status"] = "checked"
    clauses.append(f"links: {len(out['broken'])} broken of {out['checked']} checked" + (" (block)" if any(b["status"] >= 500 for b in out["broken"]) else " (warn)" if out["broken"] else ""))
    return out


def _tracked_links(args: dict, page: dict[str, Any], fetch_meta: Callable[[str], dict[str, Any]] | None, findings: list, clauses: list, missing: list) -> dict[str, Any]:
    urls = [str(u).strip() for u in _as_list(args.get("tracked_links"))]
    expected = args.get("expected_tags") or {}
    if not isinstance(expected, dict):
        raise ToolError("expected_tags must be an object: {source, medium, campaign}")
    rows: list[dict[str, Any]] = []
    campaign_values: list[str] = []
    for url in urls:
        parsed = urlparse(url)
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        utm = {k.removeprefix("utm_"): params.get(k) for k in _UTM_KEYS}
        absent = [k.removeprefix("utm_") for k in ("utm_source", "utm_medium", "utm_campaign") if not params.get(k)]
        problems: list[str] = []
        for key, value in utm.items():
            if value and (value != value.lower()):
                problems.append(f"{key} mixed case '{value}'")
            if value and re.search(r"\s", value):
                problems.append(f"{key} contains spaces")
            exp = expected.get(key)
            if value and exp and str(value) != str(exp):
                problems.append(f"{key} '{value}' differs from expected '{exp}'")
        if utm["campaign"]:
            campaign_values.append(utm["campaign"])
        row: dict[str, Any] = {"url": url, "utm": utm, "missing": absent, "inconsistent": problems, "final_url": None, "redirects": None, "tags_kept": None, "status": "ok"}
        if absent:
            row["status"] = "fix"
            findings.append(_finding("utm_missing", "warn", "tracked_links", url, "utm", absent, f"The link lacks utm_{', utm_'.join(absent)}; analytics will file it under direct or referral."))
        if problems:
            row["status"] = "fix"
            findings.append(_finding("utm_inconsistent", "warn", "tracked_links", url, "utm", problems, "; ".join(problems) + ". Use one lowercase value per campaign across every link."))
        if fetch_meta is not None:
            meta = _call_meta(fetch_meta, url)
            if meta["status"] is None:
                row["status"] = "unreachable"
            else:
                row["final_url"] = meta["final_url"]
                row["redirects"] = meta.get("redirects") if meta.get("redirects") is not None else (0 if meta["final_url"].rstrip("/") == url.rstrip("/") else 1)
                final_params = dict(parse_qsl(urlparse(meta["final_url"]).query))
                present = [k for k in _UTM_KEYS if params.get(k)]
                row["tags_kept"] = all(final_params.get(k) == params.get(k) for k in present) if present else None
                if present and not row["tags_kept"]:
                    row["status"] = "fix"
                    findings.append(_finding("utm_lost_on_redirect", "block", "tracked_links", url, "final_url", meta["final_url"], f"The link redirects to {meta['final_url']} and the utm tags do not survive; the visit is filed without its campaign."))
        rows.append(row)
    if fetch_meta is None and urls:
        missing.append("tracked_links: redirects not followed (no fetch_meta here); tags_kept unknown")
    distinct = sorted({v.lower() for v in campaign_values})
    if len(distinct) > 1:
        findings.append(_finding("utm_inconsistent", "warn", "tracked_links", campaign_values, "utm_campaign", distinct, f"Links of one pack carry different campaign values: {', '.join(distinct)}. Pick one."))
    trackers = _trackers(page, page.get("base_url"))
    if urls and not any(t["kind"] in ("analytics", "tag_manager", "pixel") for t in trackers):
        findings.append(_finding("no_tracker_on_page", "warn", None, None, "scripts", [t["vendor"] for t in trackers], "The landing page loads no analytics or tag manager script, so the tagged visit is never recorded."))
    fixes = sum(1 for r in rows if r["status"] != "ok")
    clauses.append(f"tracked links: {fixes} of {len(rows)} need a fix" + (" (block)" if any(f["code"] == "utm_lost_on_redirect" for f in findings) else " (warn)" if fixes else ""))
    return {"links": rows, "campaign_values": sorted(set(campaign_values)), "trackers": [t["vendor"] for t in trackers], "note": "tags reaching the page is all this proves; attribution must be confirmed in GA4 Realtime"}


def _element_name(element: str) -> str:
    return {"h1": "H1", "title": "title", "first_screen_text": "first screen"}.get(element, element)


def page_inspect_tool(
    tools: WorkspaceTools | None,
    args: dict,
    fetch_html: Callable[[str], str] | None = None,
    fetch_meta: Callable[[str], dict[str, Any]] | None = None,
) -> str:
    """Backs the worker-facing ``page_inspect`` tool: JSON the worker can quote from."""
    return json.dumps(page_inspect(tools, args or {}, fetch_html, fetch_meta), ensure_ascii=False, indent=1)
