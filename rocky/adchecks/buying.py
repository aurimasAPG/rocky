"""How people and organizations buy: ad_board checks on who buys, when, for whom, and what they look up first.

Every check here settles something by code: a field is present, a word of a known
class is in the copy, a URL or path is in runs/notes/facts.md, two word sets overlap,
or a count is under a limit. Nothing is inferred; a missing optional input is either
silent or one ``<field>_missing`` warning when another input shows it matters.

Concepts: #70 visible attributes, #76 objections from sales calls, #85 early adopters,
#90 emotional and social outcomes, #91 who pays and what he measures, #94 derived
demand, #95 purchase occasion, #96 shopping effort, #97 zero moment of truth,
#98 review feedback, #103 prior exposure, #105 purchase triggers, #106 shortlist
spec, #117 personal risk, #118 the buying unit, #119 involvement, #122/#123 the
use setting.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any
from urllib.parse import urlparse

from rocky.adboard import FACTS_PATH, Check

AD_FIELDS = (
    "attribute", "objection", "adoption_stage", "outcomes", "occasion", "place_effort", "landing_url",
    "prior_exposure", "appeal", "for_role", "register", "setting",
)
PACK_FIELDS = (
    "objections", "buyer", "end_market", "zmot", "review_signals", "triggers", "shortlist_spec",
    "buying_unit", "technical_terms", "involvement", "category",
)

ATTRIBUTE_KINDS = ("visible", "internal", "outcome")
ADOPTION_STAGES = ("enthusiast", "mainstream")
FOR_WHOM = ("self", "gift", "business", "other")
OCCASION_KINDS = ("routine", "gift", "emergency", "event")
EFFORT_CLASSES = ("convenience", "shopping", "specialty")
ZMOT_STAGES = ("first_hear", "research", "compare", "buy")
TRIGGER_KINDS = ("need", "reminder", "ad", "recommendation", "price", "other")
APPEALS = ("operational", "personal_risk", "both")
REGISTERS = ("rational", "emotional")
LEVELS = ("low", "high", "unknown")
TRENDS = ("up", "flat", "down", "unknown")
MIN_TRIGGERS = 5
MIN_REVIEW_COUNT = 3
MIN_PRIOR_SHARE = 50.0
SPEC_WINDOW = 40

# Word classes, written without diacritics: text is folded before matching.
TRADE_MARKERS = ("b2b", "trade", "installer", "montuotoj", "architekt", "architect", "didmen", "wholesale", "verslui", "imonems")
B2B_MARKERS = ("b2b", "gamintoj", "tiekej", "supplier", "component", "komponent", "manufactur", "didmen", "wholesale", "verslui", "imonems")
SELF_MARKERS = ("sau", "pats sau", "pati sau", "yourself", "for you", "treat yourself", "po darbo")
GIFT_MARKERS = ("dovan", "gift", "kolegai", "draugui", "draugei", "for him", "for her", "present for")
SEASON_MARKERS = (
    "kaled", "christmas", "valentin", "velyk", "easter", "dovan", "gift", "motinos dien", "tevo dien", "mother's day",
    "father's day", "gruod", "december", "helovin", "halloween", "black friday", "juodasis penktadien", "sventin", "holiday",
)
RETAILER_MARKERS = ("maxim", "rimi", "lidl", "norfa", "lentyn", "shelf", "parduotuv", "store", "retail", "prekybos centr")
STAFFED_MARKERS = ("degustat", "promoter", "konsultant", "consultant", "tasting", "demonstr", "pardavej")
IMPULSE_MARKERS = ("end-cap", "endcap", "kasos", "prie kasos", "impuls", "checkout", "stend")
DISCOVERY_MARKERS = ("discover", "atrask", "susipazink", "first time", "pirma karta", "what is", "kas yra", "introducing", "pristatome", "new to", "never heard")
REVIEW_MARKERS = ("atsiliepim", "review", "ivertin", "/5", "zvaigzd", "rating", "trustpilot")
PROOF_MARKERS = ("garantij", "warranty", "guarantee", "atsiliepim", "review", "ivertin", "klient", "customer", "users", "vartotoj", "sertifik", "certif", "testuot", "tested", "isbandyt")
TECHNICAL_ROLE = re.compile(r"\bit\b|\bcto\b|technic|technin|develop|programuot|inzinier|engineer|admin|devops")
UNRESEARCHED = ("not researched", "netirta", "unknown", "nezinoma")
HYPOTHESIS = ("hypothesis", "hipoteze", "assumed", "prielaida")
FALSE_WORDS = ("false", "no", "ne", "0")
_WORD = re.compile(r"[a-z0-9]+")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")

SCHEMA = {
    "ad": {
        "attribute": {
            "type": "object",
            "description": "the attribute this ad's angle rests on and whether the buyer can see it at home (visible), it is engineering or process (internal), or it is a result (outcome)",
            "properties": {
                "name": {"type": "string"},
                "kind": {"type": "string", "enum": list(ATTRIBUTE_KINDS)},
            },
        },
        "objection": {
            "type": "object",
            "description": "the buyer objection this ad answers, with the transcript path or URL in runs/notes/facts.md it was heard in",
            "properties": {"text": {"type": "string"}, "source": {"type": "string"}},
        },
        "adoption_stage": {
            "type": "string",
            "enum": list(ADOPTION_STAGES),
            "description": "enthusiast when the ad sells novelty to early buyers, mainstream when it names a familiar problem with reliability proof",
        },
        "outcomes": {
            "type": "object",
            "description": "what the buyer gets done (functional), how they want to feel (emotional) and how they want to be seen (social), in words that also appear in the copy",
            "properties": {
                "functional": {"type": "string"},
                "emotional": {"type": "string"},
                "social": {"type": "string"},
            },
        },
        "occasion": {
            "type": "object",
            "description": "when this purchase happens and for whom, so routine and gift buying get separate ads",
            "properties": {
                "when": {"type": "string"},
                "for_whom": {"type": "string", "enum": list(FOR_WHOM)},
                "kind": {"type": "string", "enum": list(OCCASION_KINDS)},
            },
        },
        "place_effort": {
            "type": "object",
            "description": "for a product sold in shops: whether buyers grab the category by habit (convenience), compare options (shopping) or seek one product (specialty), with the evidence",
            "properties": {
                "class": {"type": "string", "enum": list(EFFORT_CLASSES)},
                "evidence": {"type": "string"},
                "source": {"type": "string"},
            },
        },
        "landing_url": {"type": "string", "description": "where this ad sends the buyer when it is not the pack url"},
        "prior_exposure": {
            "type": "object",
            "description": "what this ad's buyers already know or feel about the category, the share who do (percent) and the URL in runs/notes/facts.md",
            "properties": {
                "belief": {"type": "string"},
                "share_pct": {"type": "number"},
                "source": {"type": "string"},
            },
        },
        "appeal": {
            "type": "string",
            "enum": list(APPEALS),
            "description": "whether this ad speaks to the operational benefit, to what a wrong choice costs the buyer personally, or both",
        },
        "for_role": {"type": "string", "description": "the buying-unit role this ad is written for, as named in buying_unit"},
        "register": {
            "type": "string",
            "enum": list(REGISTERS),
            "description": "rational when the ad leads with specs or price, emotional when it leads with the felt outcome",
        },
        "setting": {
            "type": "object",
            "description": "the occasion and scene the visual shows the product in; leave it out on the plain-product control; achievable says the scene fits the segment's home",
            "properties": {
                "occasion": {"type": "string"},
                "scene": {"type": "string"},
                "source": {"type": "string", "description": "URL in runs/notes/facts.md, or 'hypothesis'"},
                "achievable": {"type": "boolean"},
            },
        },
    },
    "pack": {
        "objections": {
            "type": "array",
            "description": "objections heard in sales calls or notes: text, the transcript path or URL in runs/notes/facts.md, and how many calls raised it",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "source": {"type": "string"}, "count": {"type": "integer"}},
            },
        },
        "buyer": {
            "type": "object",
            "description": "who pays and who uses, the cost or risk the payer measures, its sourced value and the URL in runs/notes/facts.md",
            "properties": {
                "payer": {"type": "string"},
                "user": {"type": "string"},
                "money_metric": {"type": "string"},
                "money_value": {"type": "string"},
                "source": {"type": "string"},
            },
        },
        "end_market": {
            "type": "object",
            "description": "for a supplier: the finished product the customers sell, an indicator of that market's direction and its URL in runs/notes/facts.md",
            "properties": {
                "product": {"type": "string"},
                "indicator": {"type": "string"},
                "trend": {"type": "string", "enum": list(TRENDS)},
                "value_pct": {"type": "number"},
                "source": {"type": "string"},
            },
        },
        "zmot": {
            "type": "array",
            "description": "the sources buyers check after first hearing of the product, from the ZMOT lines in runs/notes/facts.md",
            "items": {
                "type": "object",
                "properties": {
                    "stage": {"type": "string", "enum": list(ZMOT_STAGES)},
                    "source": {"type": "string"},
                    "url": {"type": "string"},
                    "trusted_because": {"type": "string"},
                },
            },
        },
        "review_signals": {
            "type": "array",
            "description": "themes in the brand's public reviews with polarity, how many reviews raised each, one example URL and the latest date",
            "items": {
                "type": "object",
                "properties": {
                    "theme": {"type": "string"},
                    "polarity": {"type": "string", "enum": ["neg", "pos"]},
                    "count": {"type": "integer"},
                    "example_url": {"type": "string"},
                    "latest_date": {"type": "string"},
                },
            },
        },
        "triggers": {
            "type": "array",
            "description": "one row per recorded buyer: what made them act, the earlier cues, and the survey file or URL in runs/notes/facts.md",
            "items": {
                "type": "object",
                "properties": {
                    "buyer": {"type": "string"},
                    "immediate": {"type": "string", "enum": list(TRIGGER_KINDS)},
                    "earlier_cues": {"type": "array", "items": {"type": "string"}},
                    "source": {"type": "string"},
                },
            },
        },
        "shortlist_spec": {
            "type": "object",
            "description": "the one specification buyers shortlist on in this category, this product's value for it and the comparison URL in runs/notes/facts.md",
            "properties": {"name": {"type": "string"}, "value": {"type": "string"}, "source_url": {"type": "string"}},
        },
        "buying_unit": {
            "type": "array",
            "description": "every role that must say yes, what each cares about and the URL in runs/notes/facts.md that names it",
            "items": {
                "type": "object",
                "properties": {"role": {"type": "string"}, "cares_about": {"type": "string"}, "source": {"type": "string"}},
            },
        },
        "technical_terms": {
            "type": "array",
            "description": "words a non-technical role would not understand, so ads for that role can be checked for them",
            "items": {"type": "string"},
        },
        "involvement": {
            "type": "object",
            "description": "how much effort and how much feeling this purchase carries (low, high or unknown), with the review URL or 'clarify'",
            "properties": {
                "effort": {"type": "string", "enum": list(LEVELS)},
                "feeling": {"type": "string", "enum": list(LEVELS)},
                "source": {"type": "string"},
            },
        },
        "category": {"type": "string", "description": "the product category in one or two words, such as food, drink, home, software"},
    },
}

MESSAGES = {
    "en": {
        "attribute_missing": "Some ads name the attribute they rest on and this one does not: add attribute with its kind.",
        "attribute_internal_lead": "The headline or first sentence leads with an internal attribute (“{value}”). Buyers choose by what they will see; move it to the proof line.",
        "objection_missing": "The facts list buyer objections, but no ad says which one it answers: add objection with its source.",
        "objection_unsourced": "Objection “{value}”: the source is not in " + FACTS_PATH + ". It is a guess until a call transcript or note backs it.",
        "objection_unanswered": "Objection “{value}” is named, but the copy shares no word with it. Answer it in the text or drop it.",
        "adoption_missing": "Some ads carry an adoption stage and this one does not: mark it enthusiast or mainstream.",
        "adoption_single_stage": "Every ad speaks to enthusiasts. Add a mainstream ad that names the familiar problem and a reliability proof.",
        "adoption_no_proof": "A mainstream ad needs evidence of reliability (a warranty, a test, a review score, a user count) and this one has none.",
        "outcome_missing": "Some ads state their outcomes and this one does not: add functional, emotional and social.",
        "outcome_functional_only": "No ad carries an emotional or social outcome. Say how the buyer wants to feel or be seen in at least one.",
        "outcome_not_in_copy": "The {field} outcome “{value}” is stated, but no word of it reaches the copy.",
        "buyer_missing": "A business buyer and nobody says who pays: add buyer with payer, user and the measure the payer watches.",
        "buyer_payer_gap": "The payer ({value}) is not the user, and no ad or claim speaks in the measure he watches. Give him one ad in his number.",
        "buyer_money_unsourced": "The payer's figure “{value}” has no URL in " + FACTS_PATH + ". Source it or leave the number out.",
        "end_market_missing": "These buyers resell what they make: say who buys the finished product and how that market is moving (end_market).",
        "end_market_unsourced": "The end-market indicator has no URL in " + FACTS_PATH + ". A trend without a source is a guess.",
        "occasion_missing": "A seasonal brief and no ad names its occasion: add occasion with when, for_whom and kind.",
        "occasion_mixed": "This ad speaks to buying for oneself and buying a gift at once (“{value}”). Give each occasion its own ad.",
        "occasion_single": "A seasonal brief and every ad buys for the same person ({value}). Add an ad for another occasion with its own offer.",
        "place_effort_missing": "The offer names a shop and no ad says how buyers shop the category: add place_effort with convenience, shopping or specialty and the evidence.",
        "place_effort_mismatch": "Shopping effort is {value}, but the ad recommends the opposite in-store treatment.",
        "zmot_missing": "The facts have ZMOT lines, but the pack carries no zmot entries: list the sources buyers check.",
        "zmot_unsourced": "ZMOT source “{value}”: the URL is not in " + FACTS_PATH + ".",
        "zmot_stage_gap": "No ZMOT entry covers research or comparison. Which source do buyers check before they decide?",
        "zmot_destination": "This ad sends buyers to {value}, which is neither the brand site nor a source they trust.",
        "review_signals_missing": "The copy leans on reviews, but no review themes are recorded: add review_signals with counts and URLs.",
        "review_contradicted": "{n} reviews complain about “{value}” and this ad promises the opposite. Address it or mark the angle address_objection with the fix.",
        "exposure_mismatch": "The ad introduces the category, but {n} % of these buyers already know it (“{value}”). Speak to what they know.",
        "exposure_unsourced": "Prior exposure “{value}” has no URL in " + FACTS_PATH + " or no share. It is unverified.",
        "trigger_too_few": "Only {n} buyer triggers are recorded, below {limit}. Label the channel recommendation provisional or record more.",
        "trigger_unsourced": "Trigger “{value}”: the source is not in " + FACTS_PATH + ".",
        "shortlist_missing": "The facts name a shortlist spec, but the pack does not carry it: add shortlist_spec.",
        "shortlist_unsourced": "Shortlist spec “{value}”: the comparison URL is not in " + FACTS_PATH + ".",
        "shortlist_buried": "No challenger leads with the spec buyers shortlist on (“{value}”). Put it in a headline or the first {limit} characters.",
        "appeal_no_risk_variant": "Buyers named a personal stake and no ad addresses it. Add a challenger with appeal personal_risk.",
        "appeal_risk_unknown": "A business buyer and no Personal risk line in the facts: ask buyers what a wrong choice would mean for them personally.",
        "role_uncovered": "The role “{value}” must say yes and no ad is written for it.",
        "role_jargon": "This ad is for {field}, a non-technical role, and uses “{value}”.",
        "role_unknown": "The buying unit lists fewer than two roles or a role without a source. Who else must agree?",
        "involvement_register_mismatch": "Buyers' feeling is {value}, but the ads' register says otherwise. Match at least one ad to it.",
        "involvement_unknown": "How much feeling this purchase carries is unknown. Ask five recent buyers how they decided.",
        "involvement_unsourced": "Involvement has no source: a review URL in " + FACTS_PATH + " or 'clarify'.",
        "setting_missing": "A {value} product and no ad shows it in a setting: add setting with occasion and scene to a challenger.",
        "setting_untested": "Every ad shows a setting. Keep one plain-product control so the scene can be tested.",
        "setting_unsourced": "The setting “{value}” has no source: a URL in " + FACTS_PATH + " or the word hypothesis.",
        "setting_aspirational": "The scene “{value}” is beyond what this segment has. Show a place they actually own.",
        "setting_mismatch": "A setting is shown but the segment's buying power is empty, so nobody can judge whether the scene fits.",
    },
    "lt": {
        "attribute_missing": "Kitos reklamos nurodo savybę, kuria remiasi, o ši ne: pridėkite attribute su jos rūšimi.",
        "attribute_internal_lead": "Antraštė arba pirmas sakinys prasideda vidine savybe („{value}“). Pirkėjai renkasi pagal tai, ką matys; perkelkite ją į įrodymų eilutę.",
        "objection_missing": "Faktuose surašyti pirkėjų prieštaravimai, bet nė viena reklama nenurodo, į kurį atsako: pridėkite objection su šaltiniu.",
        "objection_unsourced": "Prieštaravimas „{value}“: šaltinio nėra faktų faile. Kol jo nepatvirtina skambučio įrašas ar užrašai, tai spėjimas.",
        "objection_unanswered": "Prieštaravimas „{value}“ nurodytas, bet tekste nėra nė vieno jo žodžio. Atsakykite į jį tekste arba pašalinkite.",
        "adoption_missing": "Kitose reklamose nurodyta pirkėjų banga, o šioje ne: pažymėkite enthusiast arba mainstream.",
        "adoption_single_stage": "Visos reklamos kalba entuziastams. Pridėkite reklamą įprastiems pirkėjams, kuri įvardija pažįstamą problemą ir patikimumo įrodymą.",
        "adoption_no_proof": "Reklamai įprastiems pirkėjams reikia patikimumo įrodymo (garantijos, testo, atsiliepimų įverčio, vartotojų skaičiaus), o šioje jo nėra.",
        "outcome_missing": "Kitos reklamos nurodo rezultatus, o ši ne: pridėkite functional, emotional ir social.",
        "outcome_functional_only": "Nė viena reklama neturi emocinio ar socialinio rezultato. Bent vienoje pasakykite, kaip pirkėjas nori jaustis ar atrodyti.",
        "outcome_not_in_copy": "Nurodytas rezultatas „{value}“ (laukas {field}), bet nė vienas jo žodis nepasiekia teksto.",
        "buyer_missing": "Pirkėjas – verslas, o niekas nesako, kas moka: pridėkite buyer su mokėtoju, naudotoju ir rodikliu, kurį mokėtojas stebi.",
        "buyer_payer_gap": "Mokėtojas ({value}) nėra naudotojas, o nė viena reklama ar teiginys nekalba jo rodikliu. Skirkite jam vieną reklamą jo skaičiumi.",
        "buyer_money_unsourced": "Mokėtojo skaičius „{value}“ neturi nuorodos faktų faile. Nurodykite šaltinį arba praleiskite skaičių.",
        "end_market_missing": "Šie pirkėjai perparduoda tai, ką gamina: pasakykite, kas perka galutinį produktą ir kaip tai rinkai sekasi (end_market).",
        "end_market_unsourced": "Galutinės rinkos rodiklis neturi nuorodos faktų faile. Tendencija be šaltinio yra spėjimas.",
        "occasion_missing": "Šventinė užduotis, o nė viena reklama nenurodo progos: pridėkite occasion su when, for_whom ir kind.",
        "occasion_mixed": "Ši reklama vienu metu kalba apie pirkimą sau ir dovaną („{value}“). Kiekvienai progai skirkite atskirą reklamą.",
        "occasion_single": "Šventinė užduotis, o visos reklamos perka tam pačiam asmeniui ({value}). Pridėkite reklamą kitai progai su savo pasiūlymu.",
        "place_effort_missing": "Pasiūlymas nurodo parduotuvę, bet nė viena reklama nesako, kaip pirkėjai renkasi šią kategoriją: pridėkite place_effort su convenience, shopping arba specialty ir įrodymu.",
        "place_effort_mismatch": "Apsipirkimo pastanga – {value}, o reklama siūlo priešingą elgesį parduotuvėje.",
        "zmot_missing": "Faktuose yra ZMOT eilučių, bet pakete nėra zmot įrašų: išvardykite šaltinius, kuriuos pirkėjai tikrina.",
        "zmot_unsourced": "ZMOT šaltinis „{value}“: nuorodos nėra faktų faile.",
        "zmot_stage_gap": "Nė vienas ZMOT įrašas neapima tyrimo ar palyginimo. Ką pirkėjas patikrina prieš apsispręsdamas?",
        "zmot_destination": "Ši reklama siunčia pirkėjus į {value} – tai nei prekės ženklo svetainė, nei šaltinis, kuriuo jie pasitiki.",
        "review_signals_missing": "Tekstas remiasi atsiliepimais, bet atsiliepimų temos neužfiksuotos: pridėkite review_signals su skaičiais ir nuorodomis.",
        "review_contradicted": "{n} atsiliepimų skundžiasi dėl „{value}“, o ši reklama žada priešingai. Atsakykite į tai arba pažymėkite kampą address_objection su pataisymu.",
        "exposure_mismatch": "Reklama pristato kategoriją, bet {n} % šių pirkėjų ją jau žino („{value}“). Kalbėkite apie tai, ką jie žino.",
        "exposure_unsourced": "Ankstesnė patirtis „{value}“ neturi nuorodos faktų faile arba dalies. Ji nepatikrinta.",
        "trigger_too_few": "Užfiksuota tik {n} pirkėjų paskatų, mažiau nei {limit}. Pažymėkite kanalo rekomendaciją kaip preliminarią arba surinkite daugiau.",
        "trigger_unsourced": "Paskata „{value}“: šaltinio nėra faktų faile.",
        "shortlist_missing": "Faktuose įvardyta atrankos savybė, bet pakete jos nėra: pridėkite shortlist_spec.",
        "shortlist_unsourced": "Atrankos savybė „{value}“: palyginimo nuorodos nėra faktų faile.",
        "shortlist_buried": "Nė viena nauja žinutė neprasideda savybe, pagal kurią pirkėjai atsirenka („{value}“). Įrašykite ją į antraštę arba pirmus {limit} simbolių.",
        "appeal_no_risk_variant": "Pirkėjai įvardijo asmeninę riziką, o nė viena reklama į ją neatsako. Pridėkite naują žinutę su appeal personal_risk.",
        "appeal_risk_unknown": "Pirkėjas – verslas, o faktuose nėra eilutės „Personal risk:“: paklauskite pirkėjų, ką klaidingas pasirinkimas reikštų jiems asmeniškai.",
        "role_uncovered": "Vaidmuo „{value}“ turi pritarti, o nė viena reklama jam neskirta.",
        "role_jargon": "Ši reklama skirta {field} – netechniniam vaidmeniui – ir vartoja „{value}“.",
        "role_unknown": "Pirkimo grupėje mažiau nei du vaidmenys arba vaidmuo be šaltinio. Kas dar turi sutikti?",
        "involvement_register_mismatch": "Pirkėjų jausmų lygis – {value}, o reklamų registras sako kitaip. Bent vieną reklamą suderinkite su juo.",
        "involvement_unknown": "Nežinoma, kiek jausmų lydi šį pirkimą. Paklauskite penkių neseniai pirkusiųjų, kaip jie apsisprendė.",
        "involvement_unsourced": "Įsitraukimas neturi šaltinio: atsiliepimų nuorodos faktų faile arba „clarify“.",
        "setting_missing": "Produktas iš kategorijos „{value}“, o nė viena reklama nerodo jo aplinkoje: pridėkite setting su occasion ir scene naujai žinutei.",
        "setting_untested": "Visos reklamos rodo aplinką. Palikite vieną kontrolinę su pliku produktu, kad aplinką būtų galima patikrinti.",
        "setting_unsourced": "Aplinka „{value}“ neturi šaltinio: nuorodos faktų faile arba žodžio hypothesis.",
        "setting_aspirational": "Scena „{value}“ pranoksta tai, ką šis segmentas turi. Parodykite vietą, kurią jie tikrai turi.",
        "setting_mismatch": "Rodoma aplinka, bet segmento perkamoji galia tuščia, tad niekas negali spręsti, ar scena tinka.",
    },
}


# --- helpers -----------------------------------------------------------------


def _fold(text: Any) -> str:
    """Lowercase without diacritics, so ą, č, ė, į, š, ų, ū, ž match their bare letters."""
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def _obj(value: Any) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> list[dict]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value) if value[:1] == "[" else [value]
        except ValueError:
            return [value]
    return [str(v).strip() for v in value if isinstance(v, (str, int, float)) and str(v).strip()] if isinstance(value, list) else []


def _s(value: Any) -> str:
    return str(value).strip() if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


def _has(text: str, markers: tuple[str, ...]) -> str:
    folded = _fold(text)
    return next((m for m in markers if m in folded), "")


def _words(text: str) -> set[str]:
    """Content words of four letters or more, folded."""
    return {w for w in _WORD.findall(_fold(text)) if len(w) > 3}


def _stems(text: str) -> set[str]:
    """Five-letter stems of content words, so an inflected Lithuanian word still meets its root."""
    return {w[:5] for w in _words(text)}


def _is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _bare_url(value: str) -> str:
    return re.sub(r"https?://(www\.)?", "", value.lower()).rstrip("/")


def _host(value: str) -> str:
    return re.sub(r"^www\.", "", urlparse(value).netloc.lower())


def _in_facts(source: str, facts: str | None) -> bool:
    """A URL or file path the research wrote down. Without a facts file nothing can be verified, so it passes."""
    if not source:
        return False
    if facts is None:
        return True
    return _bare_url(source) in _bare_url(facts) if _is_url(source) else source.lower() in facts.lower()


def _copy(ad: dict) -> str:
    return " ".join(_s(ad.get(name)) for name in ("primary_text", "headline", "description"))


def _lead(ad: dict) -> str:
    """The headline and the first sentence of the primary text: what the feed shows first."""
    first = re.split(r"(?<=[.!?])\s", _s(ad.get("primary_text")), maxsplit=1)[0]
    return f"{_s(ad.get('headline'))} {first}"


def _line(facts: str | None, label: str) -> str:
    """The text after a ``Label:`` line in facts.md, or empty."""
    if not facts:
        return ""
    match = re.search(rf"^\s*{re.escape(label)}\s*:\s*(.+)$", facts, flags=re.I | re.M)
    return match.group(1).strip() if match else ""


def _b2b(pack: dict) -> bool:
    text = " ".join(f"{_s(ad.get('audience'))} {_s((ad.get('segment') or {}).get('distinct'))}" for ad in pack["ads"])
    return bool(_has(text, B2B_MARKERS))


def _partial(pack: dict, name: str, code: str) -> list[Check]:
    """One warning when some ads carry an optional field and another does not."""
    present = [bool(ad.get(name)) for ad in pack["ads"]]
    if any(present) and not all(present):
        return [Check("warn", code, ad=present.index(False) + 1, field=name)]
    return []


# --- checks ------------------------------------------------------------------


def _attribute(pack: dict) -> list[Check]:  # 70
    out = _partial(pack, "attribute", "attribute_missing")
    for n, ad in enumerate(pack["ads"], 1):
        attr = _obj(ad.get("attribute"))
        name = _s(attr.get("name"))
        if _s(attr.get("kind")).lower() != "internal" or not name or _has(_s(ad.get("audience")), TRADE_MARKERS):
            continue
        lead = _fold(_lead(ad))
        needles = [_fold(name), *_NUMBER.findall(name)]
        if any(needle and needle in lead for needle in needles):
            out.append(Check("warn", "attribute_internal_lead", ad=n, field="attribute", value=name))
    return out


def _objection(pack: dict, facts: str | None) -> list[Check]:  # 76
    out: list[Check] = []
    listed = _rows(pack.get("objections"))
    carried = [ad for ad in pack["ads"] if _s(_obj(ad.get("objection")).get("text"))]
    if listed and not carried:
        out.append(Check("warn", "objection_missing", field="objection"))
    for row in listed:
        text = _s(row.get("text"))
        if text and not _in_facts(_s(row.get("source")), facts):
            out.append(Check("warn", "objection_unsourced", field="objections", value=text[:120]))
    for n, ad in enumerate(pack["ads"], 1):
        objection = _obj(ad.get("objection"))
        text = _s(objection.get("text"))
        if not text:
            continue
        if not _in_facts(_s(objection.get("source")), facts):
            out.append(Check("warn", "objection_unsourced", ad=n, field="objection", value=text[:120]))
        if not _stems(text) & _stems(_copy(ad)):
            out.append(Check("warn", "objection_unanswered", ad=n, field="objection", value=text[:120]))
    return out


def _adoption(pack: dict) -> list[Check]:  # 85
    out = _partial(pack, "adoption_stage", "adoption_missing")
    stages = [_s(ad.get("adoption_stage")).lower() for ad in pack["ads"]]
    known = [s for s in stages if s in ADOPTION_STAGES]
    if len(pack["ads"]) > 1 and len(known) == len(stages) and all(s == "enthusiast" for s in known):
        out.append(Check("warn", "adoption_single_stage", field="adoption_stage"))
    for n, (ad, stage) in enumerate(zip(pack["ads"], stages), 1):
        copy = _copy(ad)
        if stage == "mainstream" and not _has(copy, PROOF_MARKERS) and not _NUMBER.search(copy):
            out.append(Check("warn", "adoption_no_proof", ad=n, field="adoption_stage"))
    return out


def _outcomes(pack: dict) -> list[Check]:  # 90
    out = _partial(pack, "outcomes", "outcome_missing")
    blocks = [_obj(ad.get("outcomes")) for ad in pack["ads"]]
    if any(blocks) and not any(_s(b.get("emotional")) or _s(b.get("social")) for b in blocks):
        out.append(Check("warn", "outcome_functional_only", field="outcomes"))
    for n, (ad, block) in enumerate(zip(pack["ads"], blocks), 1):
        copy_words = _words(f"{_s(ad.get('primary_text'))} {_s(ad.get('headline'))}")
        for key in ("emotional", "social"):
            stated = _s(block.get(key))
            if stated and _words(stated) and not _words(stated) & copy_words:
                out.append(Check("warn", "outcome_not_in_copy", ad=n, field=key, value=stated[:120]))
    return out


def _buyer(pack: dict, facts: str | None) -> list[Check]:  # 91
    buyer = _obj(pack.get("buyer"))
    if not buyer:
        return [Check("warn", "buyer_missing", field="buyer")] if _b2b(pack) else []
    out: list[Check] = []
    payer, user = _s(buyer.get("payer")), _s(buyer.get("user"))
    metric, value = _s(buyer.get("money_metric")), _s(buyer.get("money_value"))
    if payer and user and _fold(payer) != _fold(user):
        spoken = " ".join([*(_copy(ad) for ad in pack["ads"]), *(_s(c.get("text")) for c in pack.get("claims") or [])])
        hit = (metric and _stems(metric) & _stems(spoken)) or (value and _fold(value) in _fold(spoken))
        if not hit:
            out.append(Check("warn", "buyer_payer_gap", field="buyer", value=payer))
    if _NUMBER.search(value) and not (_is_url(_s(buyer.get("source"))) and _in_facts(_s(buyer.get("source")), facts)):
        out.append(Check("warn", "buyer_money_unsourced", field="buyer", value=value))
    return out


def _end_market(pack: dict, facts: str | None) -> list[Check]:  # 94
    market = _obj(pack.get("end_market"))
    if not market:
        return [Check("warn", "end_market_missing", field="end_market")] if _b2b(pack) else []
    source = _s(market.get("source"))
    if not (_is_url(source) and _in_facts(source, facts)):
        return [Check("warn", "end_market_unsourced", field="end_market", value=_s(market.get("indicator"))[:120])]
    return []


def _occasion(pack: dict, facts: str | None) -> list[Check]:  # 95
    out: list[Check] = []
    for n, ad in enumerate(pack["ads"], 1):
        copy = _copy(ad)
        self_hit = next((m for m in SELF_MARKERS if re.search(rf"\b{re.escape(m)}\b", _fold(copy))), "")
        gift_hit = _has(copy, GIFT_MARKERS)
        if self_hit and gift_hit:
            out.append(Check("warn", "occasion_mixed", ad=n, field="occasion", value=f"{self_hit} + {gift_hit}"))
    seasonal_text = " ".join([facts or "", *(f"{_copy(ad)} {_s(ad.get('angle'))}" for ad in pack["ads"])])
    if not _has(seasonal_text, SEASON_MARKERS):
        return out
    whom = [_s(_obj(ad.get("occasion")).get("for_whom")).lower() for ad in pack["ads"]]
    if not any(_obj(ad.get("occasion")) for ad in pack["ads"]):
        out.append(Check("warn", "occasion_missing", field="occasion"))
    elif len(pack["ads"]) > 1 and all(whom) and len(set(whom)) == 1:
        out.append(Check("warn", "occasion_single", field="occasion", value=whom[0]))
    return out


def _place_effort(pack: dict) -> list[Check]:  # 96
    out: list[Check] = []
    missing_at = 0
    for n, ad in enumerate(pack["ads"], 1):
        effort = _obj(ad.get("place_effort"))
        klass = _s(effort.get("class")).lower()
        place = _s((ad.get("mix") or {}).get("place"))
        retail = _has(place, RETAILER_MARKERS) or re.search(r"\bIKI\b|\bIki\b", place)
        if retail and klass not in EFFORT_CLASSES and not missing_at:
            missing_at = n
        if klass not in EFFORT_CLASSES:
            continue
        text = f"{_copy(ad)} {_s(ad.get('angle'))} {place} {_s(effort.get('evidence'))}"
        if (klass == "convenience" and _has(text, STAFFED_MARKERS)) or (klass == "specialty" and _has(text, IMPULSE_MARKERS)):
            out.append(Check("warn", "place_effort_mismatch", ad=n, field="place_effort", value=klass))
    if missing_at:
        out.append(Check("warn", "place_effort_missing", ad=missing_at, field="place_effort"))
    return out


def _zmot(pack: dict, facts: str | None) -> list[Check]:  # 97
    rows = _rows(pack.get("zmot"))
    out: list[Check] = []
    if not rows:
        if _line(facts, "ZMOT"):
            out.append(Check("warn", "zmot_missing", field="zmot"))
        return out
    stages = {_s(r.get("stage")).lower() for r in rows}
    if not stages & {"research", "compare"}:
        out.append(Check("warn", "zmot_stage_gap", field="zmot"))
    trusted = {_host(pack.get("url") or "")}
    for row in rows:
        url = _s(row.get("url"))
        if not (_is_url(url) and _in_facts(url, facts)):
            out.append(Check("warn", "zmot_unsourced", field="zmot", value=_s(row.get("source"))[:120] or url[:120]))
        if _is_url(url):
            trusted.add(_host(url))
    for n, ad in enumerate(pack["ads"], 1):
        landing = _s(ad.get("landing_url"))
        if _is_url(landing) and _host(landing) not in trusted:
            out.append(Check("warn", "zmot_destination", ad=n, field="landing_url", value=_host(landing)))
    return out


def _reviews(pack: dict) -> list[Check]:  # 98
    rows = _rows(pack.get("review_signals"))
    out: list[Check] = []
    if not rows:
        spoken = " ".join([*(_copy(ad) for ad in pack["ads"]), *(_s(c.get("text")) for c in pack.get("claims") or [])])
        if _has(spoken, REVIEW_MARKERS):
            out.append(Check("warn", "review_signals_missing", field="review_signals"))
        return out
    negatives = []
    for row in rows:
        try:
            count = int(row.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        if _s(row.get("polarity")).lower() == "neg" and count >= MIN_REVIEW_COUNT and _s(row.get("theme")):
            negatives.append((_s(row.get("theme")), count))
    for n, ad in enumerate(pack["ads"], 1):
        if "address_objection" in _fold(ad.get("angle")):
            continue
        stems = _stems(_copy(ad))
        for theme, count in negatives:
            if _stems(theme) & stems:
                out.append(Check("warn", "review_contradicted", ad=n, field="review_signals", value=theme[:120], n=count))
    return out


def _exposure(pack: dict, facts: str | None) -> list[Check]:  # 103
    out: list[Check] = []
    for n, ad in enumerate(pack["ads"], 1):
        prior = _obj(ad.get("prior_exposure"))
        belief = _s(prior.get("belief"))
        if not prior or not belief:
            continue
        try:
            share = float(str(prior.get("share_pct")).replace(",", ".")) if prior.get("share_pct") not in (None, "") else None
        except ValueError:
            share = None
        source = _s(prior.get("source"))
        if share is None or not (_is_url(source) and _in_facts(source, facts)):
            out.append(Check("warn", "exposure_unsourced", ad=n, field="prior_exposure", value=belief[:120]))
        if share is not None and share >= MIN_PRIOR_SHARE and _has(f"{_s(ad.get('angle'))} {_s(ad.get('headline'))}", DISCOVERY_MARKERS):
            out.append(Check("warn", "exposure_mismatch", ad=n, field="prior_exposure", value=belief[:120], n=int(share)))
    return out


def _triggers(pack: dict, facts: str | None) -> list[Check]:  # 105
    rows = _rows(pack.get("triggers"))
    if not rows:
        return []
    out: list[Check] = []
    if _s((pack.get("growth") or {}).get("reach")) and len(rows) < MIN_TRIGGERS:
        out.append(Check("warn", "trigger_too_few", field="triggers", n=len(rows), limit=MIN_TRIGGERS))
    for row in rows:
        if not _in_facts(_s(row.get("source")), facts):
            label = _s(row.get("buyer")) or _s(row.get("immediate"))
            out.append(Check("warn", "trigger_unsourced", field="triggers", value=label[:120]))
    return out


def _shortlist(pack: dict, facts: str | None) -> list[Check]:  # 106
    spec = _obj(pack.get("shortlist_spec"))
    if not spec:
        return [Check("warn", "shortlist_missing", field="shortlist_spec")] if _line(facts, "Shortlist spec") else []
    out: list[Check] = []
    name, value, source = _s(spec.get("name")), _s(spec.get("value")), _s(spec.get("source_url"))
    if not (_is_url(source) and _in_facts(source, facts)):
        out.append(Check("warn", "shortlist_unsourced", field="shortlist_spec", value=name or value))
    if value:
        roles = [_s(ad.get("test_role")) for ad in pack["ads"]]
        candidates = [ad for ad in pack["ads"] if _s(ad.get("test_role")) == "challenger"] if "challenger" in roles else pack["ads"]
        needle = _fold(value)
        shown = any(needle in _fold(_s(ad.get("headline"))) or needle in _fold(_s(ad.get("primary_text"))[:SPEC_WINDOW]) for ad in candidates)
        if not shown:
            out.append(Check("warn", "shortlist_buried", field="shortlist_spec", value=value, limit=SPEC_WINDOW))
    return out


def _appeal(pack: dict, facts: str | None) -> list[Check]:  # 117
    risk = _line(facts, "Personal risk")
    appeals = {_s(ad.get("appeal")).lower() for ad in pack["ads"]}
    if risk and not _has(risk, UNRESEARCHED):
        if not appeals & {"personal_risk", "both"}:
            return [Check("warn", "appeal_no_risk_variant", field="appeal", value=risk[:120])]
        return []
    if facts is not None and _b2b(pack):
        return [Check("warn", "appeal_risk_unknown", field="appeal")]
    return []


def _roles(pack: dict) -> list[Check]:  # 118
    unit = _rows(pack.get("buying_unit"))
    out: list[Check] = []
    if not unit:
        return out
    roles = [_s(r.get("role")) for r in unit if _s(r.get("role"))]
    if len(roles) < 2 or any(not _s(r.get("source")) or _fold(r.get("source")) in UNRESEARCHED for r in unit):
        out.append(Check("warn", "role_unknown", field="buying_unit"))
    for_roles = [_fold(ad.get("for_role")) for ad in pack["ads"]]
    for role in roles:
        folded = _fold(role)
        if not any(f and (f in folded or folded in f) for f in for_roles):
            out.append(Check("warn", "role_uncovered", field="buying_unit", value=role))
    terms = _strings(pack.get("technical_terms"))
    for n, ad in enumerate(pack["ads"], 1):
        role = _s(ad.get("for_role"))
        if not role or TECHNICAL_ROLE.search(_fold(role)):
            continue
        copy = _fold(_copy(ad))
        for term in terms:
            if re.search(rf"(?<![a-z0-9]){re.escape(_fold(term))}(?![a-z0-9])", copy):
                out.append(Check("warn", "role_jargon", ad=n, field=role, value=term))
    return out


def _involvement(pack: dict, facts: str | None) -> list[Check]:  # 119
    inv = _obj(pack.get("involvement"))
    if not inv:
        return []
    out: list[Check] = []
    feeling, effort, source = _s(inv.get("feeling")).lower(), _s(inv.get("effort")).lower(), _s(inv.get("source"))
    if feeling not in ("low", "high") or effort not in ("low", "high"):
        out.append(Check("warn", "involvement_unknown", field="involvement"))
    if not source or (source.lower() != "clarify" and not _in_facts(source, facts)):
        out.append(Check("warn", "involvement_unsourced", field="involvement"))
    registers = [_s(ad.get("register")).lower() for ad in pack["ads"] if _s(ad.get("register")).lower() in REGISTERS]
    if registers and (
        (feeling == "high" and "emotional" not in registers) or (feeling == "low" and all(r == "emotional" for r in registers))
    ):
        out.append(Check("warn", "involvement_register_mismatch", field="involvement", value=feeling))
    return out


def _setting(pack: dict, facts: str | None) -> list[Check]:  # 122, 123
    out: list[Check] = []
    settings = [_obj(ad.get("setting")) for ad in pack["ads"]]
    category = _s(pack.get("category"))
    if not any(settings):
        if _has(category, ("food", "maist", "drink", "gerim", "home", "nam", "bald", "furnitur", "interj")):
            out.append(Check("warn", "setting_missing", field="setting", value=category))
        return out
    if len(pack["ads"]) > 1 and all(settings):
        out.append(Check("warn", "setting_untested", field="setting"))
    for n, (ad, setting) in enumerate(zip(pack["ads"], settings), 1):
        if not setting:
            continue
        label = (_s(setting.get("scene")) or _s(setting.get("occasion")))[:120]
        source = _s(setting.get("source"))
        if not (_has(source, HYPOTHESIS) or _in_facts(source, facts)):
            out.append(Check("warn", "setting_unsourced", ad=n, field="setting", value=label))
        achievable = setting.get("achievable")
        if achievable is False or (isinstance(achievable, str) and achievable.strip().lower() in FALSE_WORDS):
            out.append(Check("warn", "setting_aspirational", ad=n, field="setting", value=label))
        if not _s((ad.get("segment") or {}).get("buying_power")):
            out.append(Check("warn", "setting_mismatch", ad=n, field="setting"))
    return out


def check(pack: dict, facts: str | None, images: dict[int, str]) -> list[Check]:
    pack = {**pack, "ads": [ad for ad in pack.get("ads") or [] if isinstance(ad, dict)]}
    return [
        *_attribute(pack),
        *_objection(pack, facts),
        *_adoption(pack),
        *_outcomes(pack),
        *_buyer(pack, facts),
        *_end_market(pack, facts),
        *_occasion(pack, facts),
        *_place_effort(pack),
        *_zmot(pack, facts),
        *_reviews(pack),
        *_exposure(pack, facts),
        *_triggers(pack, facts),
        *_shortlist(pack, facts),
        *_appeal(pack, facts),
        *_roles(pack),
        *_involvement(pack, facts),
        *_setting(pack, facts),
    ]
