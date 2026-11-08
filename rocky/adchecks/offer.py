"""Offer checks for the ad board: product experience, pricing and ad copy.

Each check settles something a marketer would otherwise have to read for: does the
buyer get everything the promise needs, is the price positioned and reassured, does
a scarcity cue rest on a sourced constraint, does the headline pick its reader, does
the test design hold what it says it holds. Nothing here calls a model; every finding
is string matching, date arithmetic or price arithmetic over the pack, the facts file
and the images the board already has.

Why pack-level lists instead of extra claim fields: ``adboard.normalize`` keeps only
``text`` and ``source`` on a claim, so a scarcity constraint or a process step lives
in its own pack field and names the claim it backs.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import unicodedata
from typing import Any

from rocky.adboard import FEED_LIMITS, Check, _bare_url, _is_url

AD_FIELDS = (
    "upgrade", "offer", "attributes", "arm", "selector", "qualifier", "benefit", "visual", "creative_role", "insider",
)
PACK_FIELDS = (
    "complete_product", "ownership_map", "depth", "switch", "delivered", "evaluation_cue", "purchase_route",
    "price_position", "scarcity", "test_design", "quality_evidence", "series", "test_metrics", "longest_strings",
    "inbox", "continuity", "test_plan",
)

OWNERSHIP_STAGES = ("setup", "storage", "cleaning", "maintenance", "repair", "disposal")
COMPLEMENT_KINDS = ("training", "documentation", "accessory", "service", "integration")
PROVIDED_BY = ("us", "partner", "buyer", "missing", "unknown")
ROUTE_KINDS = ("online", "retail", "phone", "visit", "order_form")
OFFLINE_ROUTES = ("retail", "phone", "visit", "order_form")
TEST_DESIGNS = ("full_ad", "headline", "combined_vs_single")
ATTRIBUTE_KINDS = ("evidence", "benefit", "delivery")
CREATIVE_ROLES = ("brand", "direct", "combined")
BUYING_METRICS = ("purchase", "lead", "add_to_cart", "trial", "stated_intent")
ENGAGEMENT_METRICS = ("likes", "shares", "views", "comments", "recall", "amusement", "retell")
# iPhone Mail shows about this much of each line before it cuts (eMarketing, pp. 502–503); approximations.
INBOX_LIMITS = {"sender_name": 25, "subject": 40, "preview_text": 90}
OWNERSHIP_GAP_PCT = 30.0

_STEM = 5
_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_MONEY = re.compile(r"(?:€\s?\d[\d\s.,]*|\d[\d\s.,]*\s?(?:€|eur\b))", re.I)
_PHONE = re.compile(r"(?:\+370|8[\s-]?6)\d[\d\s-]{5,}")
_ROUTE_TOKENS = re.compile(r"\b(?:g\.|gatv|pr\.|prospekt|turg|market|parduotuv|stockist|store|shop|tel\.|phone|skambink|call)", re.I)
_BLOCK_WORDS = re.compile(r"(?:užrakint\w*|negalite tęsti|negalėsite tęsti|sustabdyt\w*|\block(?:ed)?\b|cannot continue|blocked|suspended)", re.I)
_BEGINNER = re.compile(r"(?:pradedan|naujok|\bnew\b|beginner|first[- ]time|\bsmall\b|smulk|freelanc|laisvai samdom)", re.I)
_PRO = re.compile(r"(?:buhalter|accountant|agenc|agentūr|\bteam|komand|\bpro\b|profesional|operator|expert|firm|įmon)", re.I)
_EASE_CLAIM = re.compile(
    r"(?:pereik\w*\s+per\s+\d+|per\s+\d+\s+minu|lengv\w*\s+pere|paprast\w*\s+pere|easy\s+(?:to\s+)?(?:switch|migrat|set\s?up)"
    r"|seamless|no\s+setup|painless|without\s+(?:a\s+)?setup|be\s+diegimo|be\s+vargo|switch\s+in\s+\d+)",
    re.I,
)
_UNIT_WORDS = (
    ("minutes", re.compile(r"minu|\bmin\b", re.I)),
    ("hours", re.compile(r"\bval\b|valand|hour|\bhrs?\b", re.I)),
    ("days", re.compile(r"\bd\.\s?d\b|dien|\bday|darbo d", re.I)),
    ("weeks", re.compile(r"savait|week", re.I)),
    ("percent", re.compile(r"%|proc\b|procent|percent", re.I)),
)
_TIMED = re.compile(
    r"(\d+(?:[.,]\d+)?)(?:\s?[–-]\s?\d+(?:[.,]\d+)?)?\s?(d\.\s?d\.|darbo\s+dien\w*|dien\w*|days?|val\.|valand\w*|hours?|hrs?"
    r"|min\.|minu\w*|minutes?|savai\w*|weeks?|%|proc\.|procent\w*|percent)",
    re.I,
)
_QUALITY = re.compile(
    r"(?:premium|kokyb\w*|natūral\w*|natural\w*|\bgryn\w*|šalto spaudimo|šaltai spaust\w*|cold[- ]pressed|aukščiausi\w*|quality|finest|\bpure\b)",
    re.I,
)
_PROCESS_ONLY = re.compile(r"(?:ekologi|sertifik|certif|cold[- ]pressed|šalto spaudimo|\biso\b|\bbio\b|organic|patent)", re.I)
_SENSORY = re.compile(
    r"(?:colou?r|smell|taste|texture|sound|second|minute|gram|°c|spalv|kvap|skon|tekstūr|garsas|sekund|minut|gram|laipsn|mm\b|cm\b|ml\b)",
    re.I,
)
_EXCLUSIVE = re.compile(r"(?:vienintel\w*|tik mes|tik pas mus|\bonly\b(?!\s+\d)|the only|exclusively|niekas kitas|no one else)", re.I)
_SCARCITY = re.compile(
    r"(?:liko tik|liko\s+\d|lik(?:ę|o)\s+(?:tik\s+)?\d|only\s+\d+\s+left|last chance|paskutin|ribotas kiekis|ribotas skaičius|\blimited"
    r"|tik šiandien|kol (?:yra|turime)|while stocks last|ends (?:today|soon|tonight)|baigiasi|hurry|skubėk)",
    re.I,
)
_CONTINUATION = (
    "kaip minėjome", "kaip minėta", "ankstesnėje reklamoje", "ankstesniame įraše", "žr. kitą", "2 dalis", "antra dalis", "tęsinys",
    "as we said", "as mentioned", "see our other ad", "part 2", "as above", "in our last ad", "previous ad",
)
_PLACEHOLDER = re.compile(r"(?:lorem|ipsum|\[[^\]]{1,40}\]|\bx{3,5}\b|client [a-c]\b|įmonė x\b|company x\b|\{\{[^}]*\}\})", re.I)
_HUMOUR = re.compile(r"(?:humou?r|\bjok|juok|insider|emoc|emotion|funny)", re.I)
_NEW = re.compile(r"(?:\bnauj\w*|\bnew\b|naujien)", re.I)
_EPISODE = re.compile(r"(?:continuity|tęsin|epizod|episode|series|serij)", re.I)
_REASSURANCE = re.compile(
    r"(?:garantij|guarantee|warranty|grąžin|return|refund|bandom|trial|išbandyk|dalimis|lizing|leasing|financ|išsimokėt|atsiliep|review|\d+\s?(?:metų|m\.)\s+garant)",
    re.I,
)
_BOILERPLATE = (
    "peržiūrėkite šį laišką naršyklėje", "peržiūrėti naršyklėje", "atsisakyti prenumeratos", "view in browser", "view this email",
    "unsubscribe", "having trouble viewing", "nematote laiško",
)
_STOP = {"ir", "the", "a", "an", "of", "to", "in", "for", "iki", "su", "nuo", "per", "ar", "or", "and", "jusu", "your"}

MESSAGES = {
    "en": {
        "complement_missing": "The buyer also needs “{value}” to get the promised result, and nobody provides it. Add it to the offer or soften the promise.",
        "complement_unknown": "Nobody knows who provides “{value}”. Ask the marketer, then mark it us, partner, buyer or missing.",
        "complement_unsourced": "“{value}” is said to be provided, but its source is not in runs/notes/facts.md.",
        "ownership_stage_missing": "The ownership map has no “{value}” stage. Say what the owner does there, or that there is no evidence.",
        "ownership_gap_unused": "Ours is at least {limit} % faster at “{value}”, yet no ad is built on it.",
        "ownership_unsourced": "The minutes for “{value}” have no source in runs/notes/facts.md.",
        "upgrade_no_trigger": "The upgrade prompt does not name the plan limit the user hit.",
        "upgrade_no_unlocks": "The upgrade prompt does not list what the paid plan unlocks.",
        "upgrade_unlocks_unsourced": "The unlock “{value}” is not in runs/notes/facts.md, so the plan table does not confirm it.",
        "upgrade_blocks_work": "The prompt says “{value}” but does not say what the user can still do on the current plan.",
        "depth_gap": "The audience mixes beginners and professionals, but the {value} side is not evidenced with a URL from the facts.",
        "switch_unhandled": "The largest change for the buyer is “{value}”, and nothing says how onboarding handles it.",
        "switch_claim": "“{value}” promises an easy switch, but the pack does not list what the buyer replaces, relearns or reorganizes.",
        "delivered_promise_gap": "{Field} promises {value}, which is better than what the brand delivers. Match the ad to the delivery fact or fix the delivery.",
        "delivered_missing": "The copy promises {value}, but there is no delivered fact for that unit. Add delivered from the terms or FAQ page.",
        "delivered_unsourced": "The delivery fact “{value}” has no source in runs/notes/facts.md.",
        "eval_cue_missing": "{Field} says “{value}”, but there is no cue a first-time buyer could check unaided. Add evaluation_cue: what they see, smell or measure, and when.",
        "eval_cue_unobservable": "The evaluation cue “{value}” names a process or certificate, not something the buyer can see, smell, taste or measure.",
        "route_missing": "A stranger could not tell how to get the product: no working link with a button, and no stockist, address or phone in the copy.",
        "route_not_in_copy": "The product is bought offline ({value}), but the copy does not say where or how. Put the stockist, hours or phone in the text.",
        "premium_without_improvement": "This challenger costs more than the control but its copy never names the improvement “{value}”. Show the better outcome and the premium together.",
        "premium_hidden": "This challenger tests a premium ({value}) but the price is not in its copy. Both arms must show the price.",
        "offer_missing": "Some ads carry an offer price and ad {n} does not, so the premium test cannot be compared.",
        "price_position_risk_unaddressed": "{value} is a high-risk outlay for the buyer, and this ad offers no guarantee, trial, financing or returns.",
        "price_position_premium_unjustified": "The price is above the category ({value}) and no claim justifies it.",
        "price_position_value_hidden": "The price is below the category ({value}) but this ad does not show it. A value message needs the number.",
        "price_position_unsourced": "The category prices have no source in runs/notes/facts.md.",
        "price_position_missing": "The copy carries a price ({value}) but price_position is not filled: say whether it is a big outlay for the buyer and where it sits in the category.",
        "scarcity_unsourced": "“{value}” is a scarcity cue with no sourced supply limit. Add it to scarcity with its source, or take it out.",
        "scarcity_stale": "The scarcity claim “{value}” has no valid re-verify date. Check the stock again and set valid_until.",
        "scarcity_count_mismatch": "The copy says a different number than the scarcity claim ({value} left).",
        "combo_incomplete": "The combined arm is missing attributes the single arms test: {value}.",
        "combo_single_mixed": "A single-attribute arm carries {n} attributes. Keep one, or mark it combined.",
        "combo_no_combined": "The design is combined versus single, but no ad is marked as the combined arm.",
        "combo_roles_untested_together": "Brand and direct executions are present but not tested together: add a combined cell.",
        "combo_no_lagged_measure": "The test plan has no lagged measure; it will only show short-term response. Name a longer-window KPI such as branded search.",
        "headline_generic": "The headline does not carry the word the buyer would recognize themselves by: “{value}”.",
        "headline_no_qualifier": "Nothing says how a respondent is confirmed to have the need. Fill qualifier with one question or observable fact.",
        "headline_test_confound": "In a headline test the {field} must match the control's. This ad's differs.",
        "headline_benefit_duplicate": "Two headlines promise the same benefit “{value}”; the test cannot tell them apart.",
        "headline_benefit_missing": "A headline test needs each ad's benefit named.",
        "proof_quality_unproven": "{Field} claims quality (“{value}”) with no manufacturing or ingredient step behind it. Add quality_evidence with a process_step and its URL.",
        "proof_false_exclusivity": "“{value}” implies exclusivity, but the evidence says the step is common in the category.",
        "proof_exclusivity_unchecked": "“{value}” implies exclusivity, and nobody has checked whether competitors do the same. Set common_in_category.",
        "proof_unsourced": "The process step “{value}” has no source in runs/notes/facts.md.",
        "copy_not_self_contained": "{Field} refers to another ad (“{value}”). Most people see only one ad; it must stand alone.",
        "copy_scale_missing": "The visual is magnified but the scale is not stated.",
        "copy_scale_not_stated": "The scale “{value}” is not in the copy. The buyer cannot tell the enlarged view from actual size.",
        "copy_series_break": "This ad drops the series cue “{value}”.",
        "copy_series_missing": "A series is declared but its cue is empty.",
        "copy_engagement_primary": "The primary metric is “{value}”. Judge a creative on a buying metric: purchase, lead, add_to_cart, trial or stated_intent.",
        "copy_recall_metric_missing": "A humorous or emotional ad needs a secondary recall or amusement metric beside the buying one.",
        "copy_placeholder": "{Field} contains placeholder text (“{value}”). Use real names and instructions.",
        "copy_long_real_name": "{Field} with the real product name “{value}” runs to {n} characters; the feed shows {limit}.",
        "copy_inbox_cut": "Inbox {field}: a phone shows about {limit} characters, this has {n}. Hidden tail: “{value}”.",
        "copy_inbox_repeat": "“{value}” repeats across the sender, subject and preview. It wastes the visible space.",
        "copy_inbox_boilerplate": "The preview text starts with boilerplate (“{value}”) instead of the message.",
        "copy_inbox_detail_hidden": "The key detail “{value}” falls outside what the phone shows.",
        "copy_continuity_broken": "The campaign character “{value}” does not appear in this ad.",
        "copy_fake_news": "There is no product news, yet the copy says “{value}”.",
        "copy_no_episode": "No ad advances the story. Mark one ad's angle as continuity and give the character a new episode.",
        "copy_insider_unsourced": "The in-joke rests on “{value}”, which is not sourced in runs/notes/facts.md. Name the job detail with its URL.",
    },
    "lt": {
        "complement_missing": "Kad gautų pažadėtą rezultatą, pirkėjui dar reikia „{value}“, o to niekas neteikia. Pridėkite tai prie pasiūlymo arba sušvelninkite pažadą.",
        "complement_unknown": "Nežinoma, kas teikia „{value}“. Paklauskite rinkodarininko ir pažymėkite: mes, partneris, pirkėjas ar trūksta.",
        "complement_unsourced": "Teigiama, kad „{value}“ teikiama, bet šaltinio nėra faktų faile.",
        "ownership_stage_missing": "Naudojimo žemėlapyje nėra etapo „{value}“. Nurodykite, ką savininkas ten daro, arba kad įrodymų nėra.",
        "ownership_gap_unused": "Mūsų produktas etape „{value}“ bent {limit} % greitesnis, bet nė viena reklama tuo nesiremia.",
        "ownership_unsourced": "Etapo „{value}“ minutės neturi šaltinio faktų faile.",
        "upgrade_no_trigger": "Pranešime apie planą nenurodyta, kokią ribą vartotojas pasiekė.",
        "upgrade_no_unlocks": "Pranešime apie planą nenurodyta, ką atrakina mokamas planas.",
        "upgrade_unlocks_unsourced": "„{value}“ nėra faktų faile, tad planų lentelė to nepatvirtina.",
        "upgrade_blocks_work": "Pranešime rašoma „{value}“, bet nepasakoma, ką vartotojas dar gali daryti su dabartiniu planu.",
        "depth_gap": "Auditorija apima ir pradedančiuosius, ir profesionalus, bet pusė „{value}“ nepagrįsta nuoroda iš faktų.",
        "switch_unhandled": "Didžiausias pokytis pirkėjui yra „{value}“, bet nenurodyta, kaip jį sprendžia įdiegimas.",
        "switch_claim": "„{value}“ žada lengvą perėjimą, bet nenurodyta, ką pirkėjas keičia, mokosi iš naujo ar pertvarko.",
        "delivered_promise_gap": "{Field} žada {value}, o tai geriau, nei prekės ženklas iš tikrųjų pristato. Suderinkite reklamą su pristatymo faktu arba pataisykite pristatymą.",
        "delivered_missing": "Tekste žadama {value}, bet nėra fakto, ką iš tikrųjų pristatote tuo matu. Pridėkite delivered iš taisyklių ar DUK puslapio.",
        "delivered_unsourced": "Pristatymo faktas „{value}“ neturi šaltinio faktų faile.",
        "eval_cue_missing": "{Field} teigia „{value}“, bet nėra požymio, kurį pirmą kartą perkantis galėtų patikrinti pats. Pridėkite evaluation_cue: ką jis pamatys, užuos ar išmatuos ir kada.",
        "eval_cue_unobservable": "Vertinimo požymis „{value}“ įvardija procesą ar sertifikatą, o ne tai, ką pirkėjas gali pamatyti, užuosti, paragauti ar išmatuoti.",
        "route_missing": "Nepažįstamas žmogus nesuprastų, kaip įsigyti: nėra veikiančios nuorodos su mygtuku, o tekste nėra nei parduotuvės, nei adreso, nei telefono.",
        "route_not_in_copy": "Produktas perkamas ne internetu ({value}), bet tekste nepasakyta, kur ir kaip. Įrašykite parduotuvę, laiką ar telefoną į tekstą.",
        "premium_without_improvement": "Ši nauja reklama kainuoja daugiau nei kontrolinė, bet jos tekste neįvardytas pagerinimas „{value}“. Parodykite geresnį rezultatą ir priemoką kartu.",
        "premium_hidden": "Ši reklama testuoja priemoką ({value}), bet kainos jos tekste nėra. Abi versijos turi rodyti kainą.",
        "offer_missing": "Kai kurios reklamos turi pasiūlymo kainą, o {n} reklama neturi, tad priemokos testo nepavyks palyginti.",
        "price_position_risk_unaddressed": "{value} pirkėjui yra didelė rizika, o ši reklama nesiūlo nei garantijos, nei bandymo, nei išsimokėjimo, nei grąžinimo.",
        "price_position_premium_unjustified": "Kaina aukštesnė nei kategorijoje ({value}), bet nė vienas teiginys to nepagrindžia.",
        "price_position_value_hidden": "Kaina žemesnė nei kategorijoje ({value}), bet ši reklama jos nerodo. Vertės žinutei reikia skaičiaus.",
        "price_position_unsourced": "Kategorijos kainos neturi šaltinio faktų faile.",
        "price_position_missing": "Tekste yra kaina ({value}), bet price_position neužpildyta: nurodykite, ar tai didelė išlaida pirkėjui ir kur kaina yra kategorijoje.",
        "scarcity_unsourced": "„{value}“ yra stygiaus užuomina be pagrįsto pasiūlos limito. Pridėkite ją į scarcity su šaltiniu arba išimkite.",
        "scarcity_stale": "Stygiaus teiginys „{value}“ neturi galiojančios pakartotinės patikros datos. Patikrinkite likutį ir nustatykite valid_until.",
        "scarcity_count_mismatch": "Tekste nurodytas kitoks skaičius nei stygiaus teiginyje (liko {value}).",
        "combo_incomplete": "Jungtinėje versijoje trūksta savybių, kurias testuoja pavienės versijos: {value}.",
        "combo_single_mixed": "Pavienės savybės versija turi {n} savybes. Palikite vieną arba pažymėkite ją kaip jungtinę.",
        "combo_no_combined": "Dizainas – jungtinė prieš pavienes, bet nė viena reklama nepažymėta kaip jungtinė versija.",
        "combo_roles_untested_together": "Yra ir prekės ženklo, ir tiesioginio atsako reklamų, bet jos netestuojamos kartu: pridėkite jungtinę celę.",
        "combo_no_lagged_measure": "Testo plane nėra uždelsto mato; jis parodys tik trumpalaikį atsaką. Įvardykite ilgesnio laikotarpio rodiklį, pvz., paieškas pagal prekės ženklą.",
        "headline_generic": "Antraštėje nėra žodžio, pagal kurį pirkėjas atpažintų save: „{value}“.",
        "headline_no_qualifier": "Nenurodyta, kaip patvirtinama, kad atsiliepęs žmogus tikrai turi šį poreikį. Užpildykite qualifier vienu klausimu ar matomu faktu.",
        "headline_test_confound": "Antraščių teste {field} turi sutapti su kontroline reklama. Šios reklamos skiriasi.",
        "headline_benefit_duplicate": "Dvi antraštės žada tą pačią naudą „{value}“; testas jų neatskirs.",
        "headline_benefit_missing": "Antraščių testui reikia įvardyti kiekvienos reklamos naudą.",
        "proof_quality_unproven": "{Field} teigia kokybę („{value}“), bet už to nėra gamybos ar sudėties žingsnio. Pridėkite quality_evidence su process_step ir nuoroda.",
        "proof_false_exclusivity": "„{value}“ reiškia išskirtinumą, bet įrodymai sako, kad šis žingsnis kategorijoje įprastas.",
        "proof_exclusivity_unchecked": "„{value}“ reiškia išskirtinumą, o niekas nepatikrino, ar konkurentai daro tą patį. Nustatykite common_in_category.",
        "proof_unsourced": "Proceso žingsnis „{value}“ neturi šaltinio faktų faile.",
        "copy_not_self_contained": "{Field} nurodo į kitą reklamą („{value}“). Dauguma pamatys tik vieną reklamą; ji turi būti suprantama pati viena.",
        "copy_scale_missing": "Vizualas padidintas, bet mastelis nenurodytas.",
        "copy_scale_not_stated": "Mastelio „{value}“ tekste nėra. Pirkėjas neatskirs padidinto vaizdo nuo tikro dydžio.",
        "copy_series_break": "Šioje reklamoje nėra serijos elemento „{value}“.",
        "copy_series_missing": "Serija nurodyta, bet jos elementas tuščias.",
        "copy_engagement_primary": "Pagrindinis matas yra „{value}“. Kūrybą vertinkite pagal pirkimo matą: purchase, lead, add_to_cart, trial arba stated_intent.",
        "copy_recall_metric_missing": "Juokingai ar emocingai reklamai šalia pirkimo mato reikia antrinio prisiminimo ar pramogos mato.",
        "copy_placeholder": "{Field} turi vietaženklį („{value}“). Naudokite tikrus pavadinimus ir instrukcijas.",
        "copy_long_real_name": "{Field} su tikru produkto pavadinimu „{value}“ siekia {n} simbolius; srautas rodo {limit}.",
        "copy_inbox_cut": "Laiško laukas {field}: telefonas rodo apie {limit} simbolių, čia yra {n}. Paslėpta pabaiga: „{value}“.",
        "copy_inbox_repeat": "„{value}“ kartojasi siuntėjo, temos ir peržiūros laukuose. Tai švaisto matomą vietą.",
        "copy_inbox_boilerplate": "Peržiūros tekstas prasideda šablonine fraze („{value}“), o ne žinute.",
        "copy_inbox_detail_hidden": "Svarbiausia detalė „{value}“ lieka už to, ką rodo telefonas.",
        "copy_continuity_broken": "Kampanijos personažo „{value}“ šioje reklamoje nėra.",
        "copy_fake_news": "Produkto naujienų nėra, bet tekste rašoma „{value}“.",
        "copy_no_episode": "Nė viena reklama netęsia istorijos. Pažymėkite vienos reklamos angle kaip continuity ir duokite personažui naują epizodą.",
        "copy_insider_unsourced": "Pokštas remiasi „{value}“, o tai nepagrįsta šaltiniu faktų faile. Įvardykite profesijos detalę su nuoroda.",
    },
}

SCHEMA = {
    "ad": {
        "upgrade": {
            "type": "object",
            "description": "for an in-app upgrade prompt: the plan limit the user hit, what the paid plan unlocks, and what they can still do on the current plan",
            "properties": {
                "trigger_limit": {"type": "string", "description": "the limit hit, e.g. 10 invoices a month"},
                "unlocks": {"type": "array", "items": {"type": "string"}, "description": "each item as the pricing page words it"},
                "continue_path": {"type": "string", "description": "what still works on the current plan"},
            },
        },
        "offer": {
            "type": "object",
            "description": "for a premium test: this arm's price and the improved outcome the premium buys",
            "properties": {
                "price": {"type": "number", "description": "this arm's price in the pack currency"},
                "improved_outcome": {"type": "string", "description": "the outcome the premium improves, as the copy words it"},
            },
        },
        "attributes": {
            "type": "array",
            "description": "for a combined-versus-single test: the evidence, benefit or delivery attributes this arm carries",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": list(ATTRIBUTE_KINDS)},
                    "text": {"type": "string", "description": "the attribute as the copy words it"},
                },
            },
        },
        "arm": {"type": "string", "enum": ["single", "combined"], "description": "in a combined-versus-single test: one attribute, or all of them"},
        "selector": {"type": "string", "description": "the word in the headline by which the intended buyer recognizes their situation, up to 40 characters"},
        "qualifier": {"type": "string", "description": "one question or observable fact that proves a respondent has the need"},
        "benefit": {"type": "string", "description": "in a headline test: the one motivation this headline promises"},
        "visual": {
            "type": "object",
            "description": "when the visual shows a feature larger than life",
            "properties": {
                "magnified": {"type": "boolean"},
                "scale": {"type": "string", "description": "the scale as the copy states it, e.g. shown at 10× actual size, real width 0.3 mm"},
            },
        },
        "creative_role": {"type": "string", "enum": list(CREATIVE_ROLES), "description": "brand-led, direct-response, or the cell that runs both"},
        "insider": {
            "type": "object",
            "description": "for an occupational joke: the job detail it rests on and where the facts show the buyer's role meets it",
            "properties": {
                "reference": {"type": "string", "description": "the form, regulation, deadline or ritual, at least three words"},
                "source": {"type": "string", "description": "URL present in runs/notes/facts.md"},
            },
        },
    },
    "pack": {
        "complete_product": {
            "type": "array",
            "description": "everything the buyer needs besides the core product to get the promised result, and who provides it",
            "items": {
                "type": "object",
                "properties": {
                    "need": {"type": "string"},
                    "kind": {"type": "string", "enum": list(COMPLEMENT_KINDS)},
                    "provided_by": {"type": "string", "enum": list(PROVIDED_BY)},
                    "source": {"type": "string", "description": "URL present in runs/notes/facts.md"},
                },
            },
        },
        "ownership_map": {
            "type": "array",
            "description": "what the owner does with ours and with the main rival at each stage of ownership, with minutes only where a source gives them",
            "items": {
                "type": "object",
                "properties": {
                    "stage": {"type": "string", "enum": list(OWNERSHIP_STAGES)},
                    "ours": {"type": "string"},
                    "theirs": {"type": "string"},
                    "minutes_ours": {"type": ["number", "null"]},
                    "minutes_theirs": {"type": ["number", "null"]},
                    "source": {"type": "string", "description": "URL present in runs/notes/facts.md"},
                },
            },
        },
        "depth": {
            "type": "object",
            "description": "what a first-timer reaches in the first session and what an experienced operator finds, each with the URL where you saw it",
            "properties": {
                "newcomer_entry": {"type": "string"},
                "operator_depth": {"type": "string"},
            },
        },
        "switch": {
            "type": "object",
            "description": "what a buyer must replace, relearn and reorganize to adopt the product, the largest of these, and how onboarding handles it",
            "properties": {
                "replaces": {"type": "array", "items": {"type": "string"}},
                "relearns": {"type": "array", "items": {"type": "string"}},
                "reorganizes": {"type": "array", "items": {"type": "string"}},
                "largest": {"type": "string"},
                "handled_by": {"type": "string", "description": "the import, training or migration offer that handles the largest change"},
            },
        },
        "delivered": {
            "type": "array",
            "description": "what the brand actually delivers for any time or percentage the copy promises, from the terms, FAQ or reviews",
            "items": {
                "type": "object",
                "properties": {
                    "quantity": {"type": "string", "description": "a number or range, e.g. 10–15"},
                    "unit": {"type": "string", "description": "days, hours, minutes, weeks or percent"},
                    "source": {"type": "string", "description": "URL present in runs/notes/facts.md"},
                },
            },
        },
        "evaluation_cue": {
            "type": "object",
            "description": "one attribute a first-time buyer can check without tools or prior knowledge: what they see, smell, taste or measure, and when",
            "properties": {
                "attribute": {"type": "string"},
                "how_to_notice": {"type": "string"},
                "when": {"type": "string"},
            },
        },
        "purchase_route": {
            "type": "object",
            "description": "how a stranger gets the product; for an offline route the stockist, hours or phone must also be in the copy",
            "properties": {
                "kind": {"type": "string", "enum": list(ROUTE_KINDS)},
                "detail": {"type": "string", "description": "address, stockist, hours or phone"},
            },
        },
        "price_position": {
            "type": "object",
            "description": "the buyer's financial risk judged by their wallet, and where the price sits against two or more sourced category prices",
            "properties": {
                "price": {"type": "number", "description": "the price the ads carry"},
                "absolute_risk": {"type": "string", "enum": ["low", "medium", "high"]},
                "category_price_low": {"type": "number"},
                "category_price_high": {"type": "number"},
                "category_source": {"type": "string", "description": "URL present in runs/notes/facts.md"},
                "relative": {"type": "string", "enum": ["below", "at", "above"], "description": "derived from the category prices when left empty"},
                "reassurance": {"type": "array", "items": {"type": "string"}, "description": "guarantee, trial, financing, returns, social_proof or none"},
            },
        },
        "scarcity": {
            "type": "array",
            "description": "the real supply limit behind every scarcity cue in the copy, with its source and the date it must be re-verified by",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "the cue as the copy words it"},
                    "unit": {"type": "string", "description": "rooms, seats, units or days"},
                    "remaining": {"type": "integer"},
                    "period": {"type": "string", "description": "the dates the limit applies to"},
                    "source": {"type": "string", "description": "URL present in runs/notes/facts.md"},
                    "checked_at": {"type": "string", "description": "ISO date"},
                    "valid_until": {"type": "string", "description": "ISO date by which the stock must be checked again"},
                },
            },
        },
        "test_design": {
            "type": "object",
            "description": "what this set of ads tests: whole ads, headlines over one shared body, or a combined offer against single attributes",
            "properties": {
                "kind": {"type": "string", "enum": list(TEST_DESIGNS)},
                "holds": {"type": "array", "items": {"type": "string"}, "description": "for a headline test, the fields every ad keeps identical to the control; default primary_text, description, cta"},
            },
        },
        "quality_evidence": {
            "type": "array",
            "description": "for each quality word in the copy, one verifiable manufacturing or ingredient step, its source, and whether competitors do it too",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string", "description": "the quality word or phrase in the copy"},
                    "process_step": {"type": "string"},
                    "common_in_category": {"type": "boolean"},
                    "source": {"type": "string", "description": "URL present in runs/notes/facts.md"},
                },
            },
        },
        "series": {
            "type": "object",
            "description": "the one recognizable feature every ad in the series carries; only a verbal cue is checked by code",
            "properties": {
                "cue": {"type": "string"},
                "kind": {"type": "string", "enum": ["verbal", "visual", "structural"]},
            },
        },
        "test_metrics": {
            "type": "object",
            "description": "the buying metric the test is judged on and the secondary recall or amusement metric reported beside it",
            "properties": {
                "primary_metric": {"type": "string", "description": "purchase, lead, add_to_cart, trial or stated_intent"},
                "secondary_metric": {"type": "string"},
            },
        },
        "longest_strings": {
            "type": "object",
            "description": "the longest real customer, product and instruction strings from the facts, so the layout is stressed with real copy",
            "properties": {
                "customer": {"type": "string"},
                "product": {"type": "string"},
                "instruction": {"type": "string"},
            },
        },
        "inbox": {
            "type": "object",
            "description": "for an email: the sender name, subject and preview text as they appear together on a phone, and the one detail that must stay visible",
            "properties": {
                "sender_name": {"type": "string"},
                "subject": {"type": "string"},
                "preview_text": {"type": "string"},
                "must_show": {"type": "string", "description": "e.g. -40 %"},
            },
        },
        "continuity": {
            "type": "object",
            "description": "an established campaign character or story carried into this launch, and whether there is any product news",
            "properties": {
                "character": {"type": "string"},
                "story_so_far": {"type": "string"},
                "prior_campaign_ref": {"type": "string"},
                "product_news": {"type": "boolean"},
            },
        },
        "test_plan": {
            "type": "object",
            "description": "for brand and direct cells: the immediate KPI with its window and the lagged KPI with its longer window",
            "properties": {
                "immediate_kpi": {"type": "string"},
                "immediate_window_days": {"type": "integer"},
                "lagged_kpi": {"type": "string"},
                "lagged_window_days": {"type": "integer"},
            },
        },
    },
}


# --- small helpers --------------------------------------------------------------------------------------------------


def _fold(text: Any) -> str:
    """Lowercase, no diacritics, single spaces: the form every containment test uses."""
    raw = unicodedata.normalize("NFKD", str(text or ""))
    return re.sub(r"\s+", " ", "".join(ch for ch in raw if not unicodedata.combining(ch)).lower()).strip()


def _stem(word: Any) -> str:
    """The first word without its ending: five characters, or two fewer than the word for short ones."""
    first = _fold(word).split(" ")[0] if _fold(word) else ""
    return first[: min(_STEM, max(3, len(first) - 2))]


def _mentions(phrase: Any, text: Any) -> bool:
    """The phrase, or its first word's stem for an inflected language, occurs in the text."""
    folded, haystack = _fold(phrase), _fold(text)
    return bool(folded) and (folded in haystack or (len(_stem(folded)) >= 3 and _stem(folded) in haystack))


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
            parsed = json.loads(value)
            value = parsed if isinstance(parsed, list) else [value]
        except ValueError:
            value = [value]
    return [str(v).strip() for v in value if str(v).strip()] if isinstance(value, list) else []


def _s(value: Any, limit: int = 500) -> str:
    return str(value).strip()[:limit] if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    match = _NUM.search(str(value).replace(" ", "").replace(" ", ""))
    try:
        return float(match.group().replace(",", ".")) if match else None
    except ValueError:
        return None


def _sourced(url: Any, facts: str | None) -> bool:
    """Same rule as the board's claims: a URL, and present in facts.md when that file exists."""
    url = _s(url)
    return _is_url(url) and (facts is None or _bare_url(url) in _bare_url(facts))


def _copy_fields(ad: dict) -> list[tuple[str, str]]:
    return [(name, str(ad.get(name) or "")) for name in ("primary_text", "headline", "description")]


def _copy(ad: dict) -> str:
    return " ".join(text for _, text in _copy_fields(ad))


def _control(pack: dict) -> dict | None:
    return next((ad for ad in pack["ads"] if ad.get("test_role") == "control"), None)


def _today() -> _dt.date:
    return _dt.date.today()


def _date(value: Any) -> _dt.date | None:
    try:
        return _dt.date.fromisoformat(_s(value)[:10])
    except ValueError:
        return None


def _money(value: float) -> str:
    return f"{value:,.0f} €".replace(",", " ") if float(value).is_integer() else f"{value:,.2f} €".replace(",", " ")


def _price_change_pct(base: float, new: float) -> float | None:
    """The price change in percent, through unit_economics so one place does price arithmetic."""
    if base <= 0:
        return None
    from rocky.calc import unit_economics

    try:
        result = unit_economics({"price": base, "new_price": new})
    except Exception:  # noqa: BLE001 - a refused input is simply "no percentage"
        return None
    change = result.get("change") or next((row.get("change") for row in result.get("conditional") or [] if row.get("change")), None)
    pct = (change or {}).get("price_change_pct")
    return float(pct) if isinstance(pct, (int, float)) else None


# --- product experience ---------------------------------------------------------------------------------------------


def _complete_product(pack: dict, facts: str | None) -> list[Check]:
    out: list[Check] = []
    for row in _rows(pack.get("complete_product")):
        need, by = _s(row.get("need"), 200), _fold(row.get("provided_by"))
        if not need:
            continue
        if by == "missing":
            out.append(Check("warn", "complement_missing", value=need))
        elif by in ("", "unknown"):
            out.append(Check("warn", "complement_unknown", value=need))
        elif by in ("us", "partner") and not _sourced(row.get("source"), facts):
            out.append(Check("warn", "complement_unsourced", value=need))
    return out


def _ownership(pack: dict, facts: str | None) -> list[Check]:
    rows = _rows(pack.get("ownership_map"))
    if not rows:
        return []
    out: list[Check] = []
    seen = {_fold(row.get("stage")) for row in rows}
    out.extend(Check("warn", "ownership_stage_missing", value=stage) for stage in OWNERSHIP_STAGES if stage not in seen)
    angles = " ".join(f"{ad.get('angle') or ''} {_copy(ad)}" for ad in pack["ads"])
    for row in rows:
        stage = _fold(row.get("stage"))
        ours, theirs = _float(row.get("minutes_ours")), _float(row.get("minutes_theirs"))
        if ours is None and theirs is None:
            continue  # no evidence: shown as such, never compared
        if not _sourced(row.get("source"), facts):
            out.append(Check("warn", "ownership_unsourced", value=stage))
        if ours is not None and theirs and theirs > 0 and (theirs - ours) / theirs * 100 >= OWNERSHIP_GAP_PCT:
            tokens = _STAGE_WORDS.get(stage, (stage,))
            if not any(_fold(token) in _fold(angles) for token in tokens):
                out.append(Check("warn", "ownership_gap_unused", value=stage, limit=int(OWNERSHIP_GAP_PCT)))
    return out


_STAGE_WORDS = {
    "setup": ("setup", "set up", "install", "diegim", "montav", "paruoš", "išpakav"),
    "storage": ("storage", "store", "laikym", "saugo", "sandėl"),
    "cleaning": ("clean", "wash", "valym", "plov", "plauti", "indaplov"),
    "maintenance": ("mainten", "service", "priežiūr", "aptarnav"),
    "repair": ("repair", "remont", "taisym", "garantij"),
    "disposal": ("dispos", "recycl", "utiliz", "perdirb", "išmet"),
}


def _upgrade(n: int, ad: dict, facts: str | None) -> list[Check]:
    upgrade = _obj(ad.get("upgrade"))
    if not upgrade:
        return []
    out: list[Check] = []
    if not _s(upgrade.get("trigger_limit")):
        out.append(Check("warn", "upgrade_no_trigger", ad=n))
    unlocks = _strings(upgrade.get("unlocks"))
    if not unlocks:
        out.append(Check("warn", "upgrade_no_unlocks", ad=n))
    known = _fold(facts) if facts else ""
    out.extend(Check("warn", "upgrade_unlocks_unsourced", ad=n, value=u) for u in unlocks if _fold(u) not in known)
    if not _s(upgrade.get("continue_path")):
        hit = _BLOCK_WORDS.search(f"{ad.get('primary_text') or ''} {ad.get('headline') or ''}")
        if hit:
            out.append(Check("warn", "upgrade_blocks_work", ad=n, field="primary_text", value=hit.group()))
    return out


def _depth(pack: dict, facts: str | None) -> list[Check]:
    depth = _obj(pack.get("depth"))
    sides = {"newcomer": _s(depth.get("newcomer_entry")), "operator": _s(depth.get("operator_depth"))}
    out: list[Check] = []
    for n, ad in enumerate(pack["ads"], 1):
        audience = str(ad.get("audience") or "")
        if not (_BEGINNER.search(audience) and _PRO.search(audience)):
            continue
        for side, text in sides.items():
            urls = re.findall(r"https?://\S+", text)
            if not text or not any(_sourced(u.rstrip(".,;)"), facts) for u in urls):
                out.append(Check("warn", "depth_gap", ad=n, value=side))
    return out


def _switch(pack: dict) -> list[Check]:
    switch = _obj(pack.get("switch"))
    out: list[Check] = []
    if switch and _s(switch.get("largest")) and not _s(switch.get("handled_by")):
        out.append(Check("warn", "switch_unhandled", value=_s(switch.get("largest"), 200)))
    if not switch:
        for n, ad in enumerate(pack["ads"], 1):
            for name, text in _copy_fields(ad):
                hit = _EASE_CLAIM.search(text)
                if hit:
                    out.append(Check("warn", "switch_claim", ad=n, field=name, value=hit.group()))
                    break
    return out


def _unit(text: str) -> str:
    return next((unit for unit, pattern in _UNIT_WORDS if pattern.search(text)), "")


def _delivered(pack: dict, facts: str | None) -> list[Check]:
    rows = [(row, _unit(_s(row.get("unit")))) for row in _rows(pack.get("delivered"))]
    out: list[Check] = []
    for row, unit in rows:
        if unit and not _sourced(row.get("source"), facts):
            out.append(Check("warn", "delivered_unsourced", value=f"{_s(row.get('quantity'))} {unit}".strip()))
    missing_units: dict[str, str] = {}
    for n, ad in enumerate(pack["ads"], 1):
        for name, text in _copy_fields(ad):
            for hit in _TIMED.finditer(text):
                unit = _unit(hit.group(2))
                if not unit:
                    continue
                promised = _float(hit.group(1))
                fact = next((row for row, u in rows if u == unit), None)
                if fact is None:
                    missing_units.setdefault(unit, hit.group().strip())
                    continue
                delivered = _float(fact.get("quantity"))
                if promised is None or delivered is None:
                    continue
                better = promised > delivered if unit == "percent" else promised < delivered
                if better:
                    value = f"{hit.group().strip()} / {_s(fact.get('quantity'))} {unit}"
                    out.append(Check("block", "delivered_promise_gap", ad=n, field=name, value=value))
    if missing_units:
        out.append(Check("warn", "delivered_missing", value=", ".join(missing_units.values())))
    return out


def _evaluation_cue(pack: dict) -> list[Check]:
    cue = _obj(pack.get("evaluation_cue"))
    attribute = _s(cue.get("attribute"), 200)
    out: list[Check] = []
    if attribute:
        if _PROCESS_ONLY.search(attribute) and not _SENSORY.search(attribute):
            out.append(Check("warn", "eval_cue_unobservable", value=attribute))
        return out
    for n, ad in enumerate(pack["ads"], 1):
        for name, text in _copy_fields(ad):
            hit = _QUALITY.search(text)
            if hit:
                out.append(Check("warn", "eval_cue_missing", ad=n, field=name, value=hit.group()))
                break
    return out


def _route(pack: dict) -> list[Check]:
    """An online route is the url plus the button, which the board already checks; an offline one must be in the copy."""
    route = _obj(pack.get("purchase_route"))
    kind, detail = _fold(route.get("kind")), _s(route.get("detail"), 300)
    if kind not in OFFLINE_ROUTES:
        return []
    out: list[Check] = []
    for n, ad in enumerate(pack["ads"], 1):
        copy = _copy(ad)
        in_copy = bool(_PHONE.search(copy) or _ROUTE_TOKENS.search(copy))
        if detail:
            words = [w for w in _fold(detail).split(" ") if len(w) >= 4 and w not in _STOP]
            digits = re.sub(r"\D", "", detail)
            in_copy = in_copy or any(w in _fold(copy) for w in words) or (len(digits) >= 6 and digits in re.sub(r"\D", "", copy))
        if not detail and not in_copy:
            out.append(Check("block", "route_missing", ad=n))
        elif detail and not in_copy:
            out.append(Check("warn", "route_not_in_copy", ad=n, value=kind))
    return out


# --- pricing and offers ---------------------------------------------------------------------------------------------


def _premium(pack: dict) -> list[Check]:
    offers = [(_obj(ad.get("offer")), ad) for ad in pack["ads"]]
    if not any(offer for offer, _ in offers):
        return []
    out: list[Check] = []
    control = _control(pack)
    base = _float(_obj((control or {}).get("offer")).get("price")) if control else None
    for n, (offer, ad) in enumerate(offers, 1):
        price = _float(offer.get("price"))
        if price is None:
            out.append(Check("warn", "offer_missing", ad=n, n=n))
            continue
        if ad.get("test_role") != "challenger" or base is None or price <= base:
            continue
        pct = _price_change_pct(base, price)
        label = _money(price) + (f" (+{pct:g} %)" if pct is not None else "")
        copy = f"{ad.get('primary_text') or ''} {ad.get('headline') or ''}"
        outcome = _s(offer.get("improved_outcome"), 200)
        if not outcome or not _mentions(outcome, copy):
            out.append(Check("warn", "premium_without_improvement", ad=n, value=outcome or "improved_outcome"))
        shown = f"{price:g}" in copy.replace(",", ".").replace(" ", "").replace(" ", "") or f"{price:.0f}" in re.sub(r"\s", "", copy)
        if not shown:
            out.append(Check("warn", "premium_hidden", ad=n, value=label))
    return out


def _first_price(pack: dict) -> float | None:
    for ad in pack["ads"]:
        for text in (_copy(ad), str((ad.get("mix") or {}).get("price") or "")):
            hit = _MONEY.search(text)
            if hit:
                return _float(hit.group())
    return None


def _price_position(pack: dict, facts: str | None) -> list[Check]:
    position = _obj(pack.get("price_position"))
    out: list[Check] = []
    if not position:
        for ad in pack["ads"]:
            hit = _MONEY.search(_copy(ad))
            if hit:
                return [Check("warn", "price_position_missing", value=hit.group().strip())]
        return out
    price = _float(position.get("price"))
    if price is None:
        price = _first_price(pack)
    low, high = _float(position.get("category_price_low")), _float(position.get("category_price_high"))
    if (low is not None or high is not None) and not _sourced(position.get("category_source"), facts):
        out.append(Check("warn", "price_position_unsourced"))
    relative = _fold(position.get("relative"))
    if relative not in ("below", "at", "above") and price is not None and low is not None and high is not None:
        relative = "below" if price < low else "above" if price > high else "at"
    label = _money(price) if price is not None else ""
    if price is not None and low is not None and high is not None:
        pct = _price_change_pct((low + high) / 2, price)
        if pct is not None:
            label += f", {pct:+g} % vs {_money(low)}–{_money(high)}"
    reassurance = [r for r in (_fold(r) for r in _strings(position.get("reassurance"))) if r and r != "none"]
    for n, ad in enumerate(pack["ads"], 1):
        copy = _copy(ad)
        if _fold(position.get("absolute_risk")) == "high" and not (reassurance and _REASSURANCE.search(copy)):
            out.append(Check("warn", "price_position_risk_unaddressed", ad=n, value=label or "the price"))
        if relative == "below" and not _MONEY.search(copy):
            out.append(Check("warn", "price_position_value_hidden", ad=n, value=label))
    if relative == "above" and not pack.get("claims"):
        out.append(Check("warn", "price_position_premium_unjustified", value=label))
    return out


def _scarcity(pack: dict, facts: str | None) -> list[Check]:
    rows = _rows(pack.get("scarcity"))
    sourced = [row for row in rows if _sourced(row.get("source"), facts)]
    out: list[Check] = []
    for n, ad in enumerate(pack["ads"], 1):
        for name, text in _copy_fields(ad):
            hit = _SCARCITY.search(text)
            if not hit:
                continue
            if not sourced:
                out.append(Check("block", "scarcity_unsourced", ad=n, field=name, value=hit.group()))
                break
            numbers = {num.replace(",", ".") for num in _NUM.findall(text)}
            for row in sourced:
                remaining = _float(row.get("remaining"))
                if remaining is not None and numbers and f"{remaining:g}" not in numbers:
                    out.append(Check("block", "scarcity_count_mismatch", ad=n, field=name, value=f"{remaining:g}"))
            break
    today = _today()
    for row in sourced:
        until = _date(row.get("valid_until"))
        if until is None or until < today:
            out.append(Check("warn", "scarcity_stale", value=_s(row.get("text"), 120) or _s(row.get("valid_until"))))
    return out


def _combo(pack: dict) -> list[Check]:
    design = _fold(_obj(pack.get("test_design")).get("kind"))
    arms = [(ad, _fold(ad.get("arm")), [_fold(a.get("text")) for a in _rows(ad.get("attributes")) if _fold(a.get("text"))]) for ad in pack["ads"]]
    if design != "combined_vs_single" and not any(arm for _, arm, _ in arms):
        return []
    out: list[Check] = []
    singles = {text for _, arm, texts in arms if arm == "single" for text in texts}
    combined = [(n, texts) for n, (_, arm, texts) in enumerate(arms, 1) if arm == "combined"]
    if not combined:
        out.append(Check("warn", "combo_no_combined"))
    for n, texts in combined:
        missing = sorted(t for t in singles if not any(t in have or have in t for have in texts))
        if missing:
            out.append(Check("block", "combo_incomplete", ad=n, value=", ".join(missing)))
    for n, (_, arm, texts) in enumerate(arms, 1):
        if arm == "single" and len(texts) > 1:
            out.append(Check("warn", "combo_single_mixed", ad=n, n=len(texts)))
    return out


def _roles(pack: dict) -> list[Check]:
    roles = {_fold(ad.get("creative_role")) for ad in pack["ads"]} & set(CREATIVE_ROLES)
    if not roles:
        return []
    out: list[Check] = []
    if {"brand", "direct"} <= roles and "combined" not in roles:
        out.append(Check("warn", "combo_roles_untested_together"))
    if not _s(_obj(pack.get("test_plan")).get("lagged_kpi")):
        out.append(Check("warn", "combo_no_lagged_measure"))
    return out


# --- copy -----------------------------------------------------------------------------------------------------------


def _headline_selector(pack: dict) -> list[Check]:
    out: list[Check] = []
    for n, ad in enumerate(pack["ads"], 1):
        selector = _s(ad.get("selector"), 40)
        if not selector:
            continue
        if not _mentions(selector, ad.get("headline")):
            out.append(Check("warn", "headline_generic", ad=n, field="headline", value=selector))
        if not _s(ad.get("qualifier")):
            out.append(Check("warn", "headline_no_qualifier", ad=n))
    return out


def _headline_test(pack: dict) -> list[Check]:
    design = _obj(pack.get("test_design"))
    benefits = [_fold(ad.get("benefit")) for ad in pack["ads"]]
    out: list[Check] = []
    seen: set[str] = set()
    for n, benefit in enumerate(benefits, 1):
        if benefit and benefit in seen:
            out.append(Check("warn", "headline_benefit_duplicate", ad=n, value=_s(pack["ads"][n - 1].get("benefit"), 80)))
        seen.add(benefit)
    if _fold(design.get("kind")) != "headline":
        return out
    holds = [h for h in _strings(design.get("holds")) if h in FEED_LIMITS or h == "cta"] or ["primary_text", "description", "cta"]
    control = _control(pack)
    for n, ad in enumerate(pack["ads"], 1):
        if not benefits[n - 1]:
            out.append(Check("warn", "headline_benefit_missing", ad=n))
        if control is None or ad is control:
            continue
        for field in holds:
            if str(ad.get(field) or "") != str(control.get(field) or ""):
                out.append(Check("block", "headline_test_confound", ad=n, field=field))
    return out


def _proof(pack: dict, facts: str | None) -> list[Check]:
    rows = _rows(pack.get("quality_evidence"))
    with_step = [row for row in rows if _s(row.get("process_step"))]
    out: list[Check] = []
    for row in with_step:
        if not _sourced(row.get("source"), facts):
            out.append(Check("warn", "proof_unsourced", value=_s(row.get("process_step"), 120)))
    common_known = [row for row in rows if isinstance(row.get("common_in_category"), bool)]
    common_true = any(row.get("common_in_category") is True for row in common_known)
    for n, ad in enumerate(pack["ads"], 1):
        for name, text in _copy_fields(ad):
            hit = _QUALITY.search(text)
            if hit and not with_step:
                out.append(Check("warn", "proof_quality_unproven", ad=n, field=name, value=hit.group()))
                break
        for name, text in _copy_fields(ad):
            hit = _EXCLUSIVE.search(text)
            if not hit:
                continue
            if common_true:
                out.append(Check("block", "proof_false_exclusivity", ad=n, field=name, value=hit.group()))
            elif not common_known:
                out.append(Check("warn", "proof_exclusivity_unchecked", ad=n, field=name, value=hit.group()))
            break
    return out


def _self_contained(pack: dict) -> list[Check]:
    out: list[Check] = []
    for n, ad in enumerate(pack["ads"], 1):
        for name, text in _copy_fields(ad):
            folded = _fold(text)
            phrase = next((p for p in _CONTINUATION if _fold(p) in folded), None)
            if phrase:
                out.append(Check("block", "copy_not_self_contained", ad=n, field=name, value=phrase))
                break
    return out


def _visual_scale(pack: dict) -> list[Check]:
    out: list[Check] = []
    for n, ad in enumerate(pack["ads"], 1):
        visual = _obj(ad.get("visual"))
        if not visual or visual.get("magnified") not in (True, "true", "True", 1):
            continue
        scale = _s(visual.get("scale"), 200)
        if not scale:
            out.append(Check("warn", "copy_scale_missing", ad=n))
        elif _fold(scale) not in _fold(f"{ad.get('primary_text') or ''} {ad.get('description') or ''}"):
            out.append(Check("warn", "copy_scale_not_stated", ad=n, value=scale))
    return out


def _series(pack: dict) -> list[Check]:
    series = _obj(pack.get("series"))
    if not series:
        return []
    cue = _s(series.get("cue"), 120)
    if not cue:
        return [Check("warn", "copy_series_missing")]
    if _fold(series.get("kind") or "verbal") != "verbal":
        return []  # visual and structural cues are judgment, not code
    return [
        Check("warn", "copy_series_break", ad=n, field="primary_text", value=cue)
        for n, ad in enumerate(pack["ads"], 1)
        if _fold(cue) not in _fold(f"{ad.get('primary_text') or ''} {ad.get('headline') or ''}")
    ]


def _test_metrics(pack: dict) -> list[Check]:
    metrics = _obj(pack.get("test_metrics"))
    primary, secondary = _fold(metrics.get("primary_metric")), _s(metrics.get("secondary_metric"))
    out: list[Check] = []
    if primary in ENGAGEMENT_METRICS:
        out.append(Check("warn", "copy_engagement_primary", value=primary))
    if not secondary and any(_HUMOUR.search(str(ad.get("angle") or "")) for ad in pack["ads"]):
        out.append(Check("warn", "copy_recall_metric_missing"))
    return out


def _realistic_copy(pack: dict) -> list[Check]:
    out: list[Check] = []
    product = _s(_obj(pack.get("longest_strings")).get("product"), 200)
    for n, ad in enumerate(pack["ads"], 1):
        for name, text in _copy_fields(ad):
            hit = _PLACEHOLDER.search(text)
            if hit:
                out.append(Check("warn", "copy_placeholder", ad=n, field=name, value=hit.group()))
        headline = str(ad.get("headline") or "")
        if product and _fold(product) not in _fold(headline) and len(f"{headline} {product}") > FEED_LIMITS["headline"]:
            out.append(Check("warn", "copy_long_real_name", ad=n, field="headline", value=product, n=len(f"{headline} {product}"), limit=FEED_LIMITS["headline"]))
    return out


def _inbox(pack: dict) -> list[Check]:
    inbox = _obj(pack.get("inbox"))
    if not inbox:
        return []
    fields = {name: _s(inbox.get(name), 1000) for name in INBOX_LIMITS}
    out: list[Check] = []
    for name, limit in INBOX_LIMITS.items():
        text = fields[name]
        if len(text) > limit:
            out.append(Check("warn", "copy_inbox_cut", field=name, value=text[limit:][:60], n=len(text), limit=limit))
    words: dict[str, set[str]] = {}
    for name, text in fields.items():
        for word in {w.rstrip(".-") for w in re.findall(r"[^\W\d_][\w.\-]{2,}", _fold(text))}:
            if word not in _STOP:
                words.setdefault(word, set()).add(name)
    for word, where in sorted(words.items()):
        if len(where) >= 2:
            out.append(Check("warn", "copy_inbox_repeat", value=word))
    preview = _fold(fields["preview_text"])
    phrase = next((p for p in _BOILERPLATE if _fold(p) in preview), None)
    if phrase is None and fields["sender_name"] and preview.startswith(_fold(fields["sender_name"])):
        phrase = fields["sender_name"]
    if phrase:
        out.append(Check("warn", "copy_inbox_boilerplate", field="preview_text", value=phrase))
    must = _s(inbox.get("must_show"), 80)
    if must:
        visible = fields["subject"][: INBOX_LIMITS["subject"]] + " " + fields["preview_text"][: INBOX_LIMITS["preview_text"]]
        if _fold(must) not in _fold(visible):
            out.append(Check("warn", "copy_inbox_detail_hidden", value=must))
    return out


def _continuity(pack: dict) -> list[Check]:
    continuity = _obj(pack.get("continuity"))
    character = _s(continuity.get("character"), 80)
    if not continuity or not character:
        return []
    out: list[Check] = []
    no_news = continuity.get("product_news") in (False, "false", "False", 0)
    for n, ad in enumerate(pack["ads"], 1):
        copy = f"{ad.get('primary_text') or ''} {ad.get('headline') or ''}"
        if not _mentions(character, copy):
            out.append(Check("warn", "copy_continuity_broken", ad=n, value=character))
        if no_news:
            hit = _NEW.search(copy)
            if hit:
                out.append(Check("block", "copy_fake_news", ad=n, field="primary_text", value=hit.group()))
    if not any(_EPISODE.search(str(ad.get("angle") or "")) for ad in pack["ads"]):
        out.append(Check("warn", "copy_no_episode"))
    return out


def _insider(pack: dict, facts: str | None) -> list[Check]:
    out: list[Check] = []
    for n, ad in enumerate(pack["ads"], 1):
        insider = _obj(ad.get("insider"))
        if not insider and not _HUMOUR.search(str(ad.get("angle") or "")):
            continue
        reference = _s(insider.get("reference"), 200)
        if len(reference.split()) < 3 or not _sourced(insider.get("source"), facts):
            out.append(Check("warn", "copy_insider_unsourced", ad=n, value=reference or "insider"))
    return out


def check(pack: dict, facts: str | None, images: dict[int, str]) -> list[Check]:
    if not pack.get("ads"):
        return []
    checks: list[Check] = []
    checks += _complete_product(pack, facts)
    checks += _ownership(pack, facts)
    for n, ad in enumerate(pack["ads"], 1):
        checks += _upgrade(n, ad, facts)
    checks += _depth(pack, facts)
    checks += _switch(pack)
    checks += _delivered(pack, facts)
    checks += _evaluation_cue(pack)
    checks += _route(pack)
    checks += _premium(pack)
    checks += _price_position(pack, facts)
    checks += _scarcity(pack, facts)
    checks += _combo(pack)
    checks += _roles(pack)
    checks += _headline_selector(pack)
    checks += _headline_test(pack)
    checks += _proof(pack, facts)
    checks += _self_contained(pack)
    checks += _visual_scale(pack)
    checks += _series(pack)
    checks += _test_metrics(pack)
    checks += _realistic_copy(pack)
    checks += _inbox(pack)
    checks += _continuity(pack)
    checks += _insider(pack, facts)
    return checks
