"""Market strategy and positioning checks for the ad board.

500list ids 1–6, 8, 11–14, 16, 18, 20–23, 25, 26, 31–37, 46, 47 and 54. Every check
settles something by code: a field is present, a field contains words of a class, a URL
appears in runs/notes/facts.md, two texts overlap, a count. No model calls.

A missing optional input yields at most one ``<field>_missing`` warning, and only when
the facts file shows the worker had the information (a ``Job:`` line, a ``Voice:`` block)
and left it out of the pack. Checks that ask for more research (an unknown table stake,
a new market without an adaptation plan) run only when a facts file exists, because a
pack without facts is a bare draft, not a researched campaign.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from rocky.adboard import _NUMBER, COPY_FIELDS, Check, _bare_url, _is_url
from rocky.lang import detect_language, normalize_language

AD_FIELDS = (
    "promise", "struggle", "circumstance", "outcome", "cell", "alternative",
    "purpose", "standards", "buying_process", "stage", "versus", "memory_hook", "angle_kind", "outside_for",
)
PACK_FIELDS = (
    "job", "vision", "hero", "built_for", "requests", "comparison", "market", "adaptation", "line",
    "ladder", "launch", "alternative_trial", "rival_capabilities", "alternatives", "voice",
    "known_brand", "brand_standard", "in_market_share", "buying_window", "service", "lead_users",
)

VISION_WORDS = 25
VOICE_EXAMPLES = 3
CAPABILITIES = 3
CONTROL_OVERLAP = 0.4
OUTCOME_OVERLAP = 0.4
STAKE_OVERLAP = 0.6
REASON_OVERLAP = 0.6
IN_MARKET_LOW = 0.5

_S = {"type": "string"}
_URL = {"type": "string", "description": "a URL that appears in runs/notes/facts.md"}


def _obj_schema(description: str, **props: dict) -> dict:
    return {"type": "object", "description": description, "properties": props}


def _arr_schema(description: str, **props: dict) -> dict:
    return {"type": "array", "description": description, "items": {"type": "object", "properties": props}}


SCHEMA = {
    "ad": {
        "promise": {**_S, "description": "one sentence saying what this ad promises the buyer, so the offer can be checked against it"},
        "struggle": {**_S, "description": "the outcome this ad's buyers struggle to reach, from the Segment line"},
        "circumstance": {**_S, "description": "the circumstance someone could spot from outside that identifies these buyers"},
        "outcome": {**_S, "description": "for a challenger: the outcome the product already delivers that this ad promises"},
        "cell": {**_S, "description": "the comparison cell this ad belongs to, one of the names declared in comparison.cells"},
        "alternative": _obj_schema(
            "what this ad's buyers do today instead of buying, with what it costs them and where that comes from",
            what=_S, cost={**_S, "description": "with a unit: €/month, min/day, or unknown"},
            source={**_S, "description": "a URL from runs/notes/facts.md, or estimate"},
        ),
        "purpose": {"type": "string", "enum": ["transform", "equipment", "consume"], "description": "whether the buyer transforms the item, uses it as equipment or consumes it"},
        "standards": {"type": "array", "description": "the specs or certificates this buyer checks, each a string or {text, source}", "items": {}},
        "buying_process": {**_S, "description": "who approves, whether there is a sample or trial step, how often they reorder"},
        "stage": {"type": "string", "enum": ["out_of_market", "assessing"], "description": "whether this ad speaks to buyers not yet choosing or to buyers assessing providers now"},
        "versus": _obj_schema(
            "for an assessing ad: the functional comparison it makes",
            attribute=_S, us=_S, them=_S, source=_URL,
        ),
        "memory_hook": {**_S, "description": "for an out-of-market ad: the story or asset buyers will recall when the contract comes up"},
        "angle_kind": {"type": "string", "enum": ["feature", "outcome", "service"], "description": "whether the ad sells a feature, an outcome or the after-sale relationship"},
        "outside_for": {"type": "boolean", "description": "true when this ad targets buyers outside those the product is built for"},
    },
    "pack": {
        "job": _obj_schema(
            "what the customer is trying to get done, in words that outlive the tool, with its parts and boundaries",
            statement={**_S, "description": "one line composed of the action, the object and the circumstance"},
            action={**_S, "description": "the verb, e.g. prepare"}, object={**_S, "description": "what it is done to, e.g. breakfast"},
            circumstance={**_S, "description": "when and where it happens, at least three words"},
            before={**_S, "description": "what the buyer does right before using the product"},
            after={**_S, "description": "what the buyer does right after"},
            scope_reason={**_S, "description": "why the job was widened, narrowed or kept"},
        ),
        "vision": {**_S, "description": "one sentence of at most 25 words saying what the product is for"},
        "hero": _obj_schema("the promise chosen for the top of the page and why", headline=_S, reason={**_S, "description": "must quote the vision"}),
        "built_for": _obj_schema("the customer type the product is built to serve", who=_S, not_for=_S),
        "requests": _arr_schema(
            "requests from prospects to change the product, judged against built_for",
            **{"from": _S}, value_eur={"type": "number"}, change=_S, affects_all={"type": "boolean"},
            verdict={"type": "string", "enum": ["build", "custom_tier", "decline"]}, reason=_S,
        ),
        "comparison": {
            "type": "object",
            "description": "a co-brand or brand-extension test: each version as a cell with the association it gains and the one it risks",
            "properties": {
                "kind": {"type": "string", "enum": ["repositioning", "cobrand", "extension"]},
                "cells": _arr_schema(
                    "cobrand needs joint, partner_a_alone, partner_b_alone; extension needs parent_brand and new_name",
                    name=_S, brand=_S, audience=_S, gains=_S, risks=_S,
                    credibility={**_S, "description": "parent_brand only: what buyers say makes the parent credible in the new category"},
                    source={**_S, "description": "a URL from runs/notes/facts.md, voc, or assumption"},
                ),
            },
        },
        "market": _obj_schema("for a new market: the country and the language its buyers read", country=_S, language={**_S, "description": "a language code such as lv or pl"}),
        "adaptation": _obj_schema(
            "for a new market: what to keep and what to adapt, separately for the product and for the message, each with evidence",
            product={"type": "string", "enum": ["keep", "adapt", "unknown"]}, product_evidence_url=_URL,
            message={"type": "string", "enum": ["keep", "translate", "rewrite", "unknown"]}, message_evidence_url=_URL, notes=_S,
        ),
        "line": {
            "type": "object",
            "description": "the current product line's prices and the new item's price, to tell filling a gap from stretching the range",
            "properties": {
                "items": _arr_schema("the items on sale today", name=_S, price_eur={"type": ["number", "null"]}, use=_S),
                "new_name": _S, "new_price_eur": {"type": "number"},
                "intent": {"type": "string", "enum": ["fill", "stretch_up", "stretch_down"]},
            },
        },
        "ladder": _obj_schema(
            "where this offer sits between its cheaper and dearer neighbours and, for a middle tier, the reason only it can claim",
            tier={"type": "string", "enum": ["low", "mid", "high"]},
            below=_obj_schema("the cheaper neighbour", name=_S, price_eur={"type": "number"}, url=_S),
            above=_obj_schema("the dearer neighbour", name=_S, price_eur={"type": "number"}, url=_S),
            reason={**_S, "description": "one sentence neither neighbour can claim; it must appear in an ad"},
        ),
        "launch": {
            "type": "object",
            "description": "whether this is a new launch, a relaunch of a name buyers remember, or an existing product",
            "properties": {
                "kind": {"type": "string", "enum": ["new", "relaunch", "existing"]},
                "associations": _arr_schema(
                    "for a relaunch: what buyers still link to the name, kept or countered",
                    text=_S, stance={"type": "string", "enum": ["keep", "counter"]}, source=_URL,
                ),
            },
        },
        "alternative_trial": _obj_schema(
            "when the facts show losses and repeated small gains: an alternative that solves the problem differently, and a small trial of it",
            problem=_S, alternative=_S, differs_from_current=_S,
            trial=_obj_schema("the trial's size", segment=_S, days={"type": "integer"}, metric=_S, threshold=_S),
        ),
        "rival_capabilities": _arr_schema(
            "the linked capabilities behind a rival's offer, at least three, each sourced",
            rival=_S, capability=_S,
            kind={"type": "string", "enum": ["delivery", "expertise", "service", "data", "licence", "partner", "scale"]},
            ours={"type": "string", "enum": ["met", "unmet", "unknown"]}, source=_URL,
        ),
        "alternatives": _arr_schema("what buyers say they would otherwise spend the same money on, quoted with a URL", text=_S, source=_URL),
        "voice": {
            "type": "object",
            "description": "the brand's recognised voice: quoted live sentences with URLs, the address form and the exclamation limit",
            "properties": {
                "examples": _arr_schema("three or more sentences from the brand's live copy", text=_S, source=_URL),
                "address": {"type": "string", "enum": ["jus", "tu", "none"]},
                "max_exclamations": {"type": "integer", "description": "the most exclamation marks one field may carry"},
            },
        },
        "known_brand": _obj_schema("evidence that the brand is already known, which lets a sparse ad skip the stranger test", evidence=_S, source=_URL),
        "brand_standard": {
            "type": "object",
            "description": "for a brand with several suppliers: the suppliers and the quality every lot must meet regardless of origin",
            "properties": {
                "suppliers": _arr_schema("each supplier with its origin and page", name=_S, origin=_S, url=_S),
                "attributes": _arr_schema("each measurable attribute with its limit", name=_S, unit=_S, limit=_S, method=_S, source=_S),
            },
        },
        "in_market_share": _obj_schema(
            "the share of buyers choosing a provider right now, 0–1, with a source or marked assumed",
            value={"type": "number"}, source=_S, assumed={"type": "boolean"},
        ),
        "buying_window": {**_S, "description": "when buyers in this category switch, e.g. January"},
        "service": _obj_schema(
            "what the brand does for the buyer after purchase, with the page that shows it",
            offered={"type": "boolean"}, summary=_S, source=_URL,
        ),
        "lead_users": _arr_schema(
            "candidates who built their own solution and have much to gain, each condition with a URL",
            name=_S, built=_obj_schema("what they built", text=_S, url=_URL), stake=_obj_schema("what they stand to gain, as a number with a unit", text=_S, url=_URL),
        ),
    },
}

MESSAGES = {
    "en": {
        "mix_mismatch": "The ad says “{value}”, but the offer does not back it ({field}). Change the ad or the offer.",
        "job_missing": "The job is missing: say in one line what the customer is trying to get done, without naming the brand or the tool.",
        "job_not_durable": "The job names the tool or the brand (“{value}”). Write what the customer wants done in words that outlive the tool.",
        "job_boundaries_missing": "The job does not say what the buyer does just before and after using the product (missing: {value}).",
        "job_boundary_names_product": "The step “{value}” names the product or the brand. Describe what the buyer does, not what they use.",
        "job_no_circumstance": "The job has no circumstance: say when and where it happens, so an interviewer knows what to watch.",
        "job_vague_action": "“{value}” is too vague to observe. Name the action and its object: “prepare breakfast during a commute”.",
        "job_statement_mismatch": "The job statement does not contain its action and object (“{value}”). Compose it from them.",
        "audience_demographic": "The audience is only demographics (“{value}”). Name the struggle and the circumstance that identify these buyers.",
        "control_drift": "The control does not match the site's pitch (overlap {value}). Quote the Pitch line so the test compares the real message.",
        "no_pitch": "The facts have no Pitch line, so the control is not the site's real message. Quote it from the site.",
        "outcome_unsourced": "The challenger's outcome “{value}” has no sourced claim behind it. Add one from the facts, or promise only what the product already delivers.",
        "stake_unknown": "“{value}”: nobody knows whether the brand meets this minimum. Find out before launch.",
        "stake_no_minimum": "“{value}”: set the minimum acceptable level before spending on differentiation.",
        "stake_lead": "The headline leads on a table stake (“{value}”). Competitors already deliver it; differentiate on something else.",
        "product_unmatched": "“{value}” has no Match line in the facts. Say which segment it serves, or drop the ad.",
        "no_match_lines": "Several products, but no Match lines in the facts. Say which segment each product serves best.",
        "reach_unverified": "New market: the reach evidence has no URL that is in the facts. Add the page that shows these buyers can be reached.",
        "reach_mismatch": "New market: the ads are in one language and the market reads another ({value}). Write the ads in the market's language.",
        "adaptation_missing": "New market: say what you keep and what you adapt, for the product and for the message, each with evidence.",
        "adaptation_unsourced": "Adaptation “{value}” has no evidence URL from the facts. Add a local source or mark it unknown.",
        "adaptation_unknown": "Adaptation of the {value} is still unknown. An open question for the marketer, not an error.",
        "line_incomplete": "The current line has an item without a price ({value}), so the new price cannot be placed. Add the price.",
        "line_mismatch": "The line block says {field}, but the new price against the current line ({n}–{limit} €) is {value}. Different evidence is needed.",
        "stretch_unproven": "Stretching the line to {value} € needs proof of buyers and competitors at that tier. No claim in the pack names a price there.",
        "comparison_incomplete": "The {field} test has no cell “{value}”. Each version needs its own cell and at least one ad.",
        "cell_no_ads": "Cell “{value}” has no ad. A version nobody sees is not tested.",
        "cell_no_tradeoff": "Cell “{value}” does not say what it gains and what it risks. Name both associations.",
        "cell_unknown": "The ad names cell “{value}”, which the comparison does not declare.",
        "credibility_unsourced": "The parent brand's credibility in the new category (“{value}”) rests on an assumption. Add a buyer source or label it untested.",
        "mid_reason_missing": "A middle-priced offer needs its own reason to be chosen. Say in one sentence what neither neighbour can claim.",
        "mid_reason_unused": "No ad uses the mid-tier reason (“{value}”). Put it in the copy, or it is not being tested.",
        "price_ladder_invalid": "The price ladder is upside down: the offer below ({n} €) costs as much as or more than the one above ({limit} €).",
        "ladder_missing": "The facts have a Price position line, but the pack has no ladder. Pass the tier, the neighbours and the reason.",
        "relaunch_associations_missing": "A relaunch inherits what buyers still link to the name. List those associations with sources, or plan it as a new launch.",
        "association_unsourced": "Association “{value}”: the source is not in the facts.",
        "heritage_on_new_name": "A new name cannot claim heritage (“{value}”). Remove it or declare a relaunch.",
        "relaunch_association_unused": "No ad uses a kept association or counters a negative one. The relaunch copy ignores what buyers remember.",
        "launch_missing": "The facts have a Launch line, but the pack has none. Pass the launch kind and its associations.",
        "vision_missing": "The facts have a Vision line, but the pack has none. Pass the one-sentence vision.",
        "vision_long": "The vision has {n} words or more than one sentence. Cut it to one sentence of at most {limit} words.",
        "hero_unjustified": "The hero choice does not quote the vision. The promise that restates the vision wins the top of the page.",
        "built_for_missing": "A request is being judged, but the pack does not say whom the product is built for. Add built_for.",
        "audience_outside": "This ad targets buyers outside those the product is built for. Keep their requests out of the core ads.",
        "request_unchecked": "The request from {value} changes the product for everyone and is accepted without checking it against whom the product is built for.",
        "local_optimum": "The facts show losses and repeated small gains, but there is no alternative that solves the problem differently. Propose a small trial of one.",
        "trial_incomplete": "The alternative trial is missing {value}. Size it: segment, days, metric and threshold.",
        "copy_headline_only": "Only {n} capabilities listed behind the rival's offer. List at least {limit}, each with a source, before deciding to copy the feature.",
        "capability_ours_missing": "Capability “{value}”: say whether we have it (met, unmet or unknown).",
        "capability_unsourced": "Capability “{value}”: the source is not in the facts.",
        "rival_capabilities_missing": "The facts list capabilities behind a rival's offer, but the pack has none. Pass rival_capabilities.",
        "occasion_place": "The occasion differs from the control (“{value}”), but the place is the same. Say where the buyer meets the product on that occasion.",
        "alternatives_missing": "The facts name what buyers would otherwise spend the money on, but the pack lists no alternatives.",
        "alternative_unsourced": "Alternative “{value}”: the source is not a URL from the facts, so it stays unverified.",
        "wrong_comparison": "The copy names “{value}”, but the buyer's real alternative is something else. Compare against what buyers do today.",
        "voice_missing": "The facts have a Voice block, but the pack has none. Pass the examples, the address form and the exclamation limit.",
        "voice_unsourced": "Voice example “{value}”: the source is not in the facts.",
        "voice_thin": "Only {n} voice examples; {limit} are needed before this counts as the recognised voice.",
        "voice_address_mismatch": "The brand addresses readers as “{value}”, but the {field} switches form.",
        "voice_exclamation": "{Field}: {n} exclamation marks, the brand's voice allows {limit}.",
        "borrowed_equity": "A stranger to the brand could not tell what is sold: the copy names neither the product nor the job.",
        "purpose_missing": "Other ads state the buying purpose; this one does not. Say whether the buyer transforms, equips or consumes.",
        "purpose_same_pitch": "Ads {value} serve different buying purposes with the same text. Each purpose has its own standards and buying process.",
        "purpose_no_standard": "A buyer who transforms or equips checks specifications. This ad names no standard, certificate or spec.",
        "standard_unsourced": "Standard “{value}”: the source is not in the facts.",
        "standard_missing": "{n} suppliers and no brand standard. Define the quality every lot must meet regardless of origin.",
        "claim_single_origin": "Claim “{value}” is sourced from one supplier only, but the brand has {n}. Claim only what every source meets.",
        "origin_in_copy": "The copy names one origin (“{value}”), but the brand comes from several. Drop the origin or guarantee it for every lot.",
        "stage_missing": "Other ads state the buying stage; this one does not. Mark it out_of_market or assessing.",
        "comparison_out_of_market": "An out-of-market ad compares against a competitor (“{value}”). Buyers not yet choosing will not remember a comparison; give them a story.",
        "pack_all_assessing": "Every ad speaks to buyers assessing providers, but only {value} of the market is choosing now. Add an ad the rest will remember later.",
        "in_market_assumed": "The share of buyers in market ({value}) is assumed, not sourced. Say so and name what would confirm it.",
        "versus_missing": "An assessing ad needs a functional comparison: attribute, us, them and a source.",
        "versus_unsourced": "Comparison “{value}”: the source is not in the facts.",
        "memory_hook_missing": "An out-of-market ad needs a memory hook: the story or asset buyers will recall when the contract comes up.",
        "service_unsourced": "This ad sells the after-sale relationship, but there is no sourced service evidence. Add the service page or build the ad on a product outcome.",
        "service_angle_missing": "The brand offers after-sale service, yet every ad sells features. One challenger should sell the relationship competitors' feature lists overlook.",
        "lead_user_unqualified": "Lead user “{value}” is missing {field}. Both conditions need a source: what they built and what they stand to gain.",
    },
    "lt": {
        "mix_mismatch": "Reklama sako „{value}“, bet pasiūlymas to nepatvirtina ({field}). Pakeiskite reklamą arba pasiūlymą.",
        "job_missing": "Trūksta darbo (job): vienu sakiniu pasakykite, ką klientas nori pasiekti, neminėdami prekės ženklo ar įrankio.",
        "job_not_durable": "Darbas įvardija įrankį arba prekės ženklą („{value}“). Parašykite, ką klientas nori pasiekti, žodžiais, kurie išliks ir pasikeitus įrankiui.",
        "job_boundaries_missing": "Prie darbo nenurodyta, ką pirkėjas daro prieš pat naudodamas produktą ir iškart po to (trūksta: {value}).",
        "job_boundary_names_product": "Žingsnis „{value}“ įvardija produktą arba prekės ženklą. Aprašykite, ką pirkėjas daro, o ne ką naudoja.",
        "job_no_circumstance": "Darbas neturi aplinkybės: pasakykite, kada ir kur tai vyksta, kad tyrėjas žinotų, ką stebėti.",
        "job_vague_action": "„{value}“ per miglota, kad būtų galima stebėti. Įvardykite veiksmą ir jo objektą: „paruošti pusryčius pakeliui į darbą“.",
        "job_statement_mismatch": "Darbo sakinyje nėra jo veiksmo ir objekto („{value}“). Sudarykite jį iš jų.",
        "audience_demographic": "Auditoriją apibrėžia tik demografija („{value}“). Įvardykite problemą ir aplinkybę, pagal kurią šiuos pirkėjus galima atpažinti.",
        "control_drift": "Kontrolinė reklama neatitinka svetainės žinutės (sutapimas {value}). Cituokite eilutę „Pitch“, kad testas lygintų tikrąją žinutę.",
        "no_pitch": "Faktuose nėra eilutės „Pitch“, todėl kontrolinė reklama nėra tikroji svetainės žinutė. Cituokite ją iš svetainės.",
        "outcome_unsourced": "Naujos žinutės rezultatas „{value}“ neturi šaltiniu pagrįsto teiginio. Pridėkite jį iš faktų arba žadėkite tik tai, ką produktas jau duoda.",
        "stake_unknown": "„{value}“: nežinoma, ar prekės ženklas pasiekia šį minimumą. Išsiaiškinkite prieš paleisdami reklamą.",
        "stake_no_minimum": "„{value}“: nustatykite mažiausią priimtiną lygį, prieš leisdami biudžetą išskirtinumui.",
        "stake_lead": "Antraštė remiasi tuo, ko pirkėjai tikisi iš visų („{value}“). Konkurentai tai jau duoda, išsiskirkite kuo nors kitu.",
        "product_unmatched": "„{value}“ neturi eilutės „Match“ faktuose. Pasakykite, kuriam segmentui jis skirtas, arba atsisakykite šios reklamos.",
        "no_match_lines": "Keli produktai, bet faktuose nėra eilučių „Match“. Pasakykite, kuriam segmentui kuris produktas tinka geriausiai.",
        "reach_unverified": "Nauja rinka: pasiekiamumo įrodymas neturi nuorodos, kuri būtų faktuose. Pridėkite puslapį, rodantį, kad šiuos pirkėjus galima pasiekti.",
        "reach_mismatch": "Nauja rinka: reklamos parašytos viena kalba, o rinka skaito kita ({value}). Rašykite reklamas rinkos kalba.",
        "adaptation_missing": "Nauja rinka: pasakykite, ką paliekate ir ką pritaikote, atskirai produktui ir žinutei, su įrodymais.",
        "adaptation_unsourced": "Pritaikymas „{value}“ neturi nuorodos iš faktų. Pridėkite vietinį šaltinį arba pažymėkite „unknown“.",
        "adaptation_unknown": "Pritaikymas ({value}) dar nežinomas. Tai atviras klausimas rinkodarininkui, ne klaida.",
        "line_incomplete": "Dabartinėje linijoje yra prekė be kainos ({value}), todėl naujos kainos neįmanoma įvertinti. Pridėkite kainą.",
        "line_mismatch": "Linijos bloke nurodyta {field}, bet nauja kaina, palyginti su dabartine linija ({n}–{limit} €), yra {value}. Reikia kitokių įrodymų.",
        "stretch_unproven": "Linijos plėtimas iki {value} € reikalauja įrodymų, kad tame lygyje yra pirkėjų ir konkurentų. Nė vienas teiginys pakete nenurodo kainos tame lygyje.",
        "comparison_incomplete": "Palyginime ({field}) nėra langelio „{value}“. Kiekviena versija turi turėti savo langelį ir bent vieną reklamą.",
        "cell_no_ads": "Langelis „{value}“ neturi reklamos. Versija, kurios niekas nemato, netestuojama.",
        "cell_no_tradeoff": "Langelyje „{value}“ nenurodyta, ką jis įgyja ir kuo rizikuoja. Įvardykite abi asociacijas.",
        "cell_unknown": "Reklama nurodo langelį „{value}“, kurio palyginime nėra.",
        "credibility_unsourced": "Pagrindinio prekės ženklo patikimumas naujoje kategorijoje („{value}“) remiasi prielaida. Pridėkite pirkėjų šaltinį arba pažymėkite kaip nepatikrintą.",
        "mid_reason_missing": "Vidutinės kainos pasiūlymui reikia savos priežasties jį rinktis. Vienu sakiniu pasakykite tai, ko negali pasakyti nė vienas kaimyninis pasiūlymas.",
        "mid_reason_unused": "Nė viena reklama nenaudoja vidurinio lygio priežasties („{value}“). Įrašykite ją į tekstą, kitaip ji netestuojama.",
        "price_ladder_invalid": "Kainų kopėčios apverstos: žemesnis pasiūlymas ({n} €) kainuoja tiek pat ar daugiau nei aukštesnis ({limit} €).",
        "ladder_missing": "Faktuose yra eilutė „Price position“, bet pakete nėra lauko ladder. Perduokite lygį, kaimyninius pasiūlymus ir priežastį.",
        "relaunch_associations_missing": "Atgaivinamas ženklas paveldi tai, ką pirkėjai vis dar sieja su vardu. Išvardykite šias asociacijas su šaltiniais arba planuokite kaip naują paleidimą.",
        "association_unsourced": "Asociacija „{value}“: šaltinio nėra faktų faile.",
        "heritage_on_new_name": "Naujas vardas negali remtis istorija („{value}“). Pašalinkite tai arba nurodykite, kad tai atgaivinimas.",
        "relaunch_association_unused": "Nė viena reklama nenaudoja išlaikomos asociacijos ir neatremia neigiamos. Atgaivinimo tekstas nepaiso to, ką pirkėjai prisimena.",
        "launch_missing": "Faktuose yra eilutė „Launch“, bet pakete jos nėra. Perduokite paleidimo tipą ir asociacijas.",
        "vision_missing": "Faktuose yra eilutė „Vision“, bet pakete jos nėra. Perduokite vieno sakinio viziją.",
        "vision_long": "Vizija yra {n} žodžių arba ilgesnė nei vienas sakinys. Sutrumpinkite iki vieno sakinio, ne ilgesnio kaip {limit} žodžių.",
        "hero_unjustified": "Pagrindinio pažado pasirinkimas nesiremia vizija. Puslapio viršų laimi pažadas, kuris pakartoja viziją.",
        "built_for_missing": "Vertinamas prašymas, bet pakete nenurodyta, kam produktas kuriamas. Pridėkite built_for.",
        "audience_outside": "Ši reklama skirta pirkėjams, kuriems produktas nekuriamas. Jų prašymai neturi patekti į pagrindines reklamas.",
        "request_unchecked": "Prašymas ({value}) keičia produktą visiems ir priimtas nepatikrinus, kam produktas kuriamas.",
        "local_optimum": "Faktai rodo nuostolius ir pasikartojančius menkus pagerėjimus, bet nėra alternatyvos, kuri problemą spręstų kitaip. Pasiūlykite nedidelį jos bandymą.",
        "trial_incomplete": "Alternatyvos bandyme trūksta: {value}. Nustatykite segmentą, trukmę, rodiklį ir slenkstį.",
        "copy_headline_only": "Už konkurento pasiūlymo išvardyta tik {n} gebėjimų. Prieš nusprendžiant kopijuoti funkciją, išvardykite bent {limit}, kiekvieną su šaltiniu.",
        "capability_ours_missing": "Gebėjimas „{value}“: pasakykite, ar jį turime (met, unmet arba unknown).",
        "capability_unsourced": "Gebėjimas „{value}“: šaltinio nėra faktų faile.",
        "rival_capabilities_missing": "Faktuose išvardyti konkurento pasiūlymo gebėjimai, bet pakete jų nėra. Perduokite rival_capabilities.",
        "occasion_place": "Proga skiriasi nuo kontrolinės („{value}“), bet vieta ta pati. Pasakykite, kur pirkėjas sutinka produktą ta proga.",
        "alternatives_missing": "Faktuose įvardyta, kam pirkėjai kitaip išleistų pinigus, bet pakete alternatyvų nėra.",
        "alternative_unsourced": "Alternatyva „{value}“: šaltinis nėra nuoroda iš faktų, todėl ji lieka nepatikrinta.",
        "wrong_comparison": "Tekste minima „{value}“, bet tikroji pirkėjo alternatyva yra kita. Lyginkite su tuo, ką pirkėjai daro šiandien.",
        "voice_missing": "Faktuose yra blokas „Voice“, bet pakete jo nėra. Perduokite pavyzdžius, kreipinį ir šauktukų ribą.",
        "voice_unsourced": "Balso pavyzdys „{value}“: šaltinio nėra faktų faile.",
        "voice_thin": "Balso pavyzdžių tik {n}; reikia {limit}, kad tai būtų laikoma atpažįstamu balsu.",
        "voice_address_mismatch": "Prekės ženklas į skaitytoją kreipiasi „{value}“, bet {field} kreipinį keičia.",
        "voice_exclamation": "{Field}: šauktukų skaičius {n}, prekės ženklo balsas leidžia {limit}.",
        "borrowed_equity": "Prekės ženklo nežinantis žmogus nesuprastų, kas parduodama: tekste neįvardytas nei produktas, nei darbas.",
        "purpose_missing": "Kitose reklamose nurodyta pirkimo paskirtis, šioje – ne. Pasakykite, ar pirkėjas perdirba, naudoja kaip įrangą ar suvartoja.",
        "purpose_same_pitch": "Reklamos {value} skirtos skirtingoms pirkimo paskirtims, bet tekstas tas pats. Kiekviena paskirtis turi savus standartus ir pirkimo eigą.",
        "purpose_no_standard": "Pirkėjas, kuris perdirba ar naudoja kaip įrangą, tikrina specifikacijas. Šioje reklamoje nenurodytas joks standartas, sertifikatas ar specifikacija.",
        "standard_unsourced": "Standartas „{value}“: šaltinio nėra faktų faile.",
        "standard_missing": "Tiekėjų: {n}, o prekės ženklo standarto nėra. Apibrėžkite kokybę, kurią turi atitikti kiekviena partija, nepriklausomai nuo kilmės.",
        "claim_single_origin": "Teiginys „{value}“ pagrįstas tik vienu tiekėju, o prekės ženklas jų turi {n}. Teigkite tik tai, ką atitinka visi šaltiniai.",
        "origin_in_copy": "Tekste įvardyta viena kilmė („{value}“), bet prekės ženklas tiekiamas iš kelių. Pašalinkite kilmę arba užtikrinkite ją kiekvienai partijai.",
        "stage_missing": "Kitose reklamose nurodyta pirkimo stadija, šioje – ne. Pažymėkite out_of_market arba assessing.",
        "comparison_out_of_market": "Reklama dar nesirenkantiems pirkėjams lygina su konkurentu („{value}“). Jie palyginimo neprisimins, duokite jiems istoriją.",
        "pack_all_assessing": "Visos reklamos kalba su tiekėjus vertinančiais pirkėjais, bet dabar renkasi tik {value} rinkos. Pridėkite reklamą, kurią likusieji prisimins vėliau.",
        "in_market_assumed": "Rinkoje esančių pirkėjų dalis ({value}) yra prielaida, ne pagrįstas skaičius. Pasakykite tai ir įvardykite, kas ją patvirtintų.",
        "versus_missing": "Vertinantiems pirkėjams skirtai reklamai reikia funkcinio palyginimo: savybė, mes, jie ir šaltinis.",
        "versus_unsourced": "Palyginimas „{value}“: šaltinio nėra faktų faile.",
        "memory_hook_missing": "Dar nesirenkantiems pirkėjams skirtai reklamai reikia įsimenamo elemento: istorijos ar ženklo, kurį pirkėjai prisimins, kai ateis laikas rinktis.",
        "service_unsourced": "Ši reklama parduoda santykį po pirkimo, bet pagrįstų aptarnavimo įrodymų nėra. Pridėkite aptarnavimo puslapį arba kurkite reklamą ant produkto rezultato.",
        "service_angle_missing": "Prekės ženklas teikia aptarnavimą po pirkimo, bet visos reklamos parduoda funkcijas. Viena nauja žinutė turėtų parduoti santykį, kurio konkurentų funkcijų sąrašai nepastebi.",
        "lead_user_unqualified": "Pirmaujantis naudotojas „{value}“: trūksta {field}. Abi sąlygos turi turėti šaltinį: ką jis pasidarė ir kiek jam tai svarbu.",
    },
}

# ---------------------------------------------------------------------------
# Helpers: coercion, words, facts
# ---------------------------------------------------------------------------

_WORD = re.compile(r"[a-z0-9ąčęėįšųūžà-ÿ]+", re.I)
_STOP = frozenset(
    "ir kad su be apie kaip kur kas nes bet arba tik yra bus buvo nuo iki per pagal dėl prie jūsų jūs tavo tau "
    "mes mūsų jie jos tai tas ta tie tos kuris kuri kurie kurios the and for with that this your you our are is "
    "was were from into onto than then them they their what when where which who will can not but all any one".split()
)
_URL_IN_TEXT = re.compile(r"https?://\S+")


def _s(value: Any, limit: int = 300) -> str:
    return str(value).strip()[:limit] if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


def _obj(value: Any) -> dict:
    if isinstance(value, str) and value[:1] == "{":
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _items(value: Any) -> list[dict]:
    if isinstance(value, str) and value[:1] == "[":
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _strings(value: Any) -> list[str]:
    """A list field whose items are strings or objects with a text."""
    if isinstance(value, str) and value[:1] == "[":
        try:
            value = json.loads(value)
        except ValueError:
            return [value]
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [_s(item) if not isinstance(item, dict) else _s(item.get("text") or item.get("name")) for item in value]


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = _s(value).replace("€", "").replace(" ", "").replace(",", ".")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group()) if match else None


def _flag(value: Any) -> bool:
    return value is True or _s(value).lower() in {"true", "yes", "taip", "1"}


def _money(text: str) -> str:
    number = _num(text)
    return f"{number:g}" if number is not None else ""


def _stems(text: str) -> set[str]:
    """Content words cut to five letters, so Lithuanian endings do not break a match."""
    return {w.lower()[:5] for w in _WORD.findall(text or "") if len(w) >= 3 and w.lower() not in _STOP}


def _overlap(text: str, reference: str) -> float:
    """What share of the reference's content words the text carries."""
    ref = _stems(reference)
    return len(_stems(text) & ref) / len(ref) if ref else 0.0


def _lines(facts: str | None, key: str) -> list[str]:
    """The text after ``Key:`` on every line that starts with it (list bullets and bold allowed)."""
    if not facts:
        return []
    pattern = re.compile(rf"^[\s\-\*#>•]*{re.escape(key)}\**\s*:\**\s*(.*\S)\s*$", re.I | re.M)
    return [m.group(1) for m in pattern.finditer(facts)]


def _in_facts(url: str, facts: str | None) -> bool:
    """A valid URL that the research saw. Without a facts file, any valid URL passes (adboard's rule)."""
    return _is_url(url) and (facts is None or _bare_url(url) in _bare_url(facts))


def _copy(ad: dict, pack: dict | None = None) -> str:
    text = " ".join(_s(ad.get(name), 3000) for name in COPY_FIELDS)
    brand = _s((pack or {}).get("brand"))
    return re.sub(re.escape(brand), " ", text, flags=re.I) if brand else text


def _domain(url: str) -> str:
    return re.sub(r"^www\.", "", urlparse(url).netloc.lower())


def _control(pack: dict) -> tuple[int, dict] | tuple[int, None]:
    for n, ad in enumerate(pack["ads"], 1):
        if ad.get("test_role") == "control":
            return n, ad
    return 0, None


# ---------------------------------------------------------------------------
# #1 The offer behind the promise
# ---------------------------------------------------------------------------

_PRICE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:€|eur\b|eurų|eurai|eurais)|€\s*(\d+(?:[.,]\d+)?)", re.I)
_DELIVERY_COPY = re.compile(r"\b(pristat\w*|deliver\w*|shipping|shipped|siunt\w*|atvež\w*|kurjer\w*)", re.I)
_DELIVERY_PLACE = re.compile(r"(pristat|deliver|ship|siunt|atvež|kurjer|omniva|dpd|express|paštomat|courier)", re.I)
_NATION = re.compile(r"(visoje lietuvoje|visoje šalyje|all of lithuania|across lithuania|throughout lithuania|nationwide)", re.I)
_NATION_PLACE = re.compile(r"(visoje|visa |lietuv|lithuania|nationwide|šalyje|country)", re.I)
_CITIES = ("vilni", "kaun", "klaipėd", "šiauli", "panevėž", "alytu", "marijampol", "latvij", "latvia", "ryg", "riga", "estij", "estonia", "talin", "lenkij", "poland", "varšuv", "warsaw")
_TIME_COPY = re.compile(r"(kitą dieną|next[- ]day|same[- ]day|tą pačią dieną|per 24|24 ?val|24 ?h\b|per valandą|within an hour|per \d+ min)", re.I)
_TIME_OFFER = re.compile(r"(\b24\b|dien|day|\bval\b|val\.|hour|\bh\b|\bmin)", re.I)


def _mix(pack: dict) -> list[Check]:
    out: list[Check] = []
    for n, ad in enumerate(pack["ads"], 1):
        mix = ad.get("mix") or {}
        text = _copy(ad, pack) + " " + _s(ad.get("promise"))
        lowered = text.lower()
        price, place, product = _s(mix.get("price")), _s(mix.get("place")), _s(mix.get("product"))
        if price:
            offered = {_money(x) for x in _NUMBER.findall(price)}
            for m in _PRICE.finditer(text):
                if _money(m.group(1) or m.group(2)) not in offered:
                    out.append(Check("warn", "mix_mismatch", ad=n, field="price", value=m.group(0).strip()))
        if place:
            delivery = _DELIVERY_COPY.search(text)
            if delivery and not _DELIVERY_PLACE.search(place):
                out.append(Check("warn", "mix_mismatch", ad=n, field="place", value=delivery.group(0)))
            nation = _NATION.search(text)
            if nation and not _NATION_PLACE.search(place):
                out.append(Check("warn", "mix_mismatch", ad=n, field="place", value=nation.group(0)))
            if not _NATION_PLACE.search(place):
                for city in _CITIES:
                    hit = re.search(rf"\b{city}\w*", lowered)
                    if hit and city not in place.lower():
                        out.append(Check("warn", "mix_mismatch", ad=n, field="place", value=text[hit.start() : hit.end()]))
            when = _TIME_COPY.search(text)
            if when and not _TIME_OFFER.search(place + " " + product):
                out.append(Check("warn", "mix_mismatch", ad=n, field="place", value=when.group(0)))
    return out


# ---------------------------------------------------------------------------
# #2 #46 #47 The job
# ---------------------------------------------------------------------------

_TOOL_WORDS = re.compile(
    r"\b(meta|facebook|google|instagram|tiktok|linkedin|youtube|ads?|reklam\w*|seo|apps?|program\w*|platform\w*|"
    r"e-?parduotuv\w*|e-?shop|eshop|svetain\w*|website|software|tool|įranki\w*|sistem\w*|system|crm|saas)\b",
    re.I,
)
_VAGUE = frozenset(
    (
        "eat healthier", "save time", "be happy", "grow", "succeed", "improve", "be healthier", "save money", "be better",
        "taupyti laiką", "sutaupyti laiko", "būti sveikesniam", "būti sveikesnei", "augti", "tobulėti", "sutaupyti",
        "valgyti sveikiau", "būti laimingam", "gerinti", "pagerinti",
    )
)


def _job_object(pack: dict) -> dict:
    raw = pack.get("job")
    if isinstance(raw, str) and raw[:1] != "{":
        return {"statement": raw}
    return _obj(raw)


def _names_product(text: str, pack: dict) -> list[str]:
    """The brand, the domain stem, a channel or tool word, or a product name inside ``text``."""
    hits: list[str] = []
    lowered = text.lower()
    brand = _s(pack.get("brand"))
    for word in _WORD.findall(brand):
        if len(word) >= 3 and word.lower() in lowered:
            hits.append(word)
            break
    stem = _domain(_s(pack.get("url"))).split(".")[0]
    if len(stem) >= 3 and stem in lowered and not any(h.lower() == stem for h in hits):
        hits.append(stem)
    tool = _TOOL_WORDS.search(text)
    if tool:
        hits.append(tool.group(0))
    for ad in pack["ads"]:
        product = _s((ad.get("mix") or {}).get("product")).lower()
        if product and product in lowered:
            hits.append(product)
            break
        if any(len(w) >= 5 and re.search(rf"\b{re.escape(w.lower())}\b", lowered) for w in _WORD.findall(product)):
            hits.append(product)
            break
    return list(dict.fromkeys(hits))


def _job(pack: dict, facts: str | None) -> list[Check]:
    job = _job_object(pack)
    statement = _s(job.get("statement"))
    if not statement:
        return [Check("warn", "job_missing")] if job or _lines(facts, "Job") else []
    out: list[Check] = []
    hits = _names_product(statement, pack)
    if hits:
        out.append(Check("warn", "job_not_durable", value=", ".join(hits)[:120]))
    missing = [name for name in ("before", "after") if not _s(job.get(name)) or _s(job.get(name)).lower() == statement.lower()]
    if missing:
        out.append(Check("warn", "job_boundaries_missing", value=", ".join(missing)))
    for name in ("before", "after"):
        step = _s(job.get(name))
        if step and name not in missing and _names_product(step, pack):
            out.append(Check("warn", "job_boundary_names_product", value=step[:80]))
    action, obj, circumstance = _s(job.get("action")), _s(job.get("object")), _s(job.get("circumstance"))
    if len(_WORD.findall(circumstance)) < 3 or (obj and _stems(circumstance) == _stems(obj)):
        out.append(Check("warn", "job_no_circumstance"))
    vague = (action or statement).lower().strip(" .!")
    if vague in _VAGUE:
        out.append(Check("warn", "job_vague_action", value=action or statement))
    if action and obj and not (_stems(action) & _stems(statement) and _stems(obj) & _stems(statement)):
        out.append(Check("warn", "job_statement_mismatch", value=statement[:80]))
    return out


# ---------------------------------------------------------------------------
# #3 Audience by struggle, not demographics
# ---------------------------------------------------------------------------

_DEMOGRAPHIC = re.compile(
    r"\b\d{2}\s*[–\-—]\s*\d{2}\b|\b\d{2}\s*\+|\b(vyrai|vyrams|vyrų|moterys|moterims|moterų|men|women|male|female|males|females)\b",
    re.I,
)
_STRUGGLE = re.compile(
    r"\b(kai|kurie|kurios|kurių|kuriems|kurioms|kuriuos|po to|nespėja|praranda|neranda|bijo|kainuoja|moka|negali|nesugeba|"
    r"trūksta|nežino|when|whose|who|after|before|struggl\w*|can'?t|cannot|los\w*|pay\w*|spend\w*|wast\w*|fail\w*|need\w*|without)\b",
    re.I,
)


def _audience(pack: dict) -> list[Check]:
    out: list[Check] = []
    for n, ad in enumerate(pack["ads"], 1):
        audience = _s(ad.get("audience"))
        context = " ".join((audience, _s(ad.get("struggle")), _s(ad.get("circumstance"))))
        if _DEMOGRAPHIC.search(audience) and not _STRUGGLE.search(context):
            out.append(Check("warn", "audience_demographic", ad=n, value=audience[:80]))
    return out


# ---------------------------------------------------------------------------
# #4 The control is the real pitch; the challenger's outcome is sourced
# ---------------------------------------------------------------------------


def _repositioning(pack: dict, facts: str | None) -> list[Check]:
    out: list[Check] = []
    n, control = _control(pack)
    pitches = _lines(facts, "Pitch")
    if control is not None and facts is not None:
        if not pitches:
            out.append(Check("warn", "no_pitch"))
        elif pitches[0].strip().lower() != "unknown":
            ratio = _overlap(_s(control.get("headline")) + " " + _s(control.get("primary_text"), 3000), pitches[0])
            if ratio < CONTROL_OVERLAP:
                out.append(Check("warn", "control_drift", ad=n, value=f"{round(ratio * 100)} %"))
    sourced = [c["text"] for c in pack.get("claims") or [] if _in_facts(c.get("source", ""), facts)]
    for n, ad in enumerate(pack["ads"], 1):
        outcome = _s(ad.get("outcome"))
        if outcome and ad.get("test_role") != "control" and not any(_overlap(text, outcome) >= OUTCOME_OVERLAP for text in sourced):
            out.append(Check("warn", "outcome_unsourced", ad=n, value=outcome[:80]))
    return out


# ---------------------------------------------------------------------------
# #5 Table stakes: known, with a minimum, and not what an ad leads on
# ---------------------------------------------------------------------------


def _stakes(pack: dict, facts: str | None) -> list[Check]:
    out: list[Check] = []
    stakes = pack.get("table_stakes") or []
    for stake in stakes:
        if facts is not None and stake.get("status") == "unknown":
            out.append(Check("warn", "stake_unknown", value=stake["outcome"]))
        if facts is not None and not _s(stake.get("minimum")):
            out.append(Check("warn", "stake_no_minimum", value=stake["outcome"]))
        for n, ad in enumerate(pack["ads"], 1):
            if _overlap(_s(ad.get("headline")), stake["outcome"]) >= STAKE_OVERLAP:
                out.append(Check("warn", "stake_lead", ad=n, field="headline", value=stake["outcome"]))
    return out


# ---------------------------------------------------------------------------
# #6 Each product matched to a segment
# ---------------------------------------------------------------------------


def _match_lines(facts: str | None) -> list[str]:
    return [re.split(r"\s*(?:→|->)\s*", line, maxsplit=1)[0].strip() for line in _lines(facts, "Match")]


def _portfolio(pack: dict, facts: str | None) -> list[Check]:
    out: list[Check] = []
    matched = [p.lower() for p in _match_lines(facts) if p]
    products = {_s((ad.get("mix") or {}).get("product")).lower() for ad in pack["ads"]} - {""}
    if facts is not None and not matched and len(products) > 1:
        return [Check("warn", "no_match_lines")]
    if not matched:
        return out
    for n, ad in enumerate(pack["ads"], 1):
        product = _s((ad.get("mix") or {}).get("product"))
        low = product.lower()
        if product and not any(low == m or low in m or m in low for m in matched):
            out.append(Check("warn", "product_unmatched", ad=n, value=product))
    return out


# ---------------------------------------------------------------------------
# #8 #11 A new market: reach with evidence, language that fits, what to adapt
# ---------------------------------------------------------------------------


def _new_market(pack: dict, facts: str | None) -> list[Check]:
    growth = pack.get("growth") or {}
    if growth.get("kind") != "development":
        return []
    out: list[Check] = []
    reach = _s(growth.get("reach"), 400)
    if reach and facts is not None and not any(_in_facts(url.rstrip(".,);"), facts) for url in _URL_IN_TEXT.findall(reach)):
        out.append(Check("warn", "reach_unverified"))
    market = _obj(pack.get("market"))
    wanted = normalize_language(market.get("language"), "")
    if wanted:
        found = detect_language(" ".join(_copy(ad) for ad in pack["ads"]))
        if found != wanted:
            out.append(Check("warn", "reach_mismatch", value=f"{found} → {wanted}"))
    adaptation = _obj(pack.get("adaptation"))
    if not adaptation:
        if facts is not None:
            out.append(Check("warn", "adaptation_missing"))
        return out
    for axis, allowed in (("product", {"keep", "adapt"}), ("message", {"keep", "translate", "rewrite"})):
        decision = _s(adaptation.get(axis)).lower()
        if decision in allowed:
            if not _in_facts(_s(adaptation.get(f"{axis}_evidence_url"), 500), facts):
                out.append(Check("warn", "adaptation_unsourced", value=f"{axis}: {decision}"))
        else:
            out.append(Check("warn", "adaptation_unknown", value=axis))
    return out


# ---------------------------------------------------------------------------
# #12 Filling the line or stretching it
# ---------------------------------------------------------------------------


def _line(pack: dict, facts: str | None) -> list[Check]:
    line = _obj(pack.get("line"))
    if not line:
        return []
    items = _items(line.get("items"))
    prices = [(_s(item.get("name")) or f"#{i}", _num(item.get("price_eur"))) for i, item in enumerate(items, 1)]
    unpriced = [name for name, price in prices if price is None]
    new = _num(line.get("new_price_eur"))
    if unpriced or not prices or new is None:
        return [Check("warn", "line_incomplete", value=", ".join(unpriced) or "new_price_eur")]
    lo, hi = min(p for _, p in prices), max(p for _, p in prices)
    position = "fill" if lo <= new <= hi else "stretch_up" if new > hi else "stretch_down"
    out: list[Check] = []
    intent = _s(line.get("intent")).lower()
    if intent in {"fill", "stretch_up", "stretch_down"} and intent != position:
        out.append(Check("warn", "line_mismatch", field=intent, value=position, n=int(lo), limit=int(hi)))
    if position != "fill":
        numbers = [
            _num(x) for c in pack.get("claims") or [] if _in_facts(c.get("source", ""), facts) for x in _NUMBER.findall(c["text"])
        ]
        proven = any(x is not None and (x > hi if position == "stretch_up" else x < lo) for x in numbers)
        if not proven:
            out.append(Check("warn", "stretch_unproven", value=f"{new:g}"))
    return out


# ---------------------------------------------------------------------------
# #13 #14 Co-brand and extension tests
# ---------------------------------------------------------------------------

_REQUIRED_CELLS = {"cobrand": ("joint", "partner_a_alone", "partner_b_alone"), "extension": ("parent_brand", "new_name")}


def _comparison(pack: dict, facts: str | None) -> list[Check]:
    comparison = _obj(pack.get("comparison"))
    if not comparison:
        return []
    kind = _s(comparison.get("kind")).lower()
    cells = {_s(c.get("name")).lower(): c for c in _items(comparison.get("cells")) if _s(c.get("name"))}
    used = [_s(ad.get("cell")).lower() for ad in pack["ads"]]
    out: list[Check] = []
    for name in _REQUIRED_CELLS.get(kind, ()):
        if name not in cells:
            out.append(Check("warn", "comparison_incomplete", field=kind, value=name))
    for n, cell in enumerate(used, 1):
        if cell and cell not in cells:
            out.append(Check("warn", "cell_unknown", ad=n, value=cell))
    for name, cell in cells.items():
        if kind in _REQUIRED_CELLS and name not in used:
            out.append(Check("warn", "cell_no_ads", value=name))
        if kind == "cobrand" and (not _s(cell.get("gains")) or not _s(cell.get("risks"))):
            out.append(Check("warn", "cell_no_tradeoff", value=name))
    parent = cells.get("parent_brand")
    if kind == "extension" and parent is not None:
        credibility, source = _s(parent.get("credibility")), _s(parent.get("source"), 500)
        if not credibility or source.lower() == "assumption" or not (source.lower() == "voc" or _in_facts(source, facts)):
            out.append(Check("warn", "credibility_unsourced", value=credibility[:80] or "parent_brand"))
    return out


# ---------------------------------------------------------------------------
# #16 A middle price needs its own reason
# ---------------------------------------------------------------------------


def _ladder(pack: dict, facts: str | None) -> list[Check]:
    position = _obj(pack.get("ladder"))
    if not position:
        return [Check("warn", "ladder_missing")] if _lines(facts, "Price position") else []
    out: list[Check] = []
    below, above = _num(_obj(position.get("below")).get("price_eur")), _num(_obj(position.get("above")).get("price_eur"))
    if below is not None and above is not None and below >= above:
        out.append(Check("warn", "price_ladder_invalid", n=int(below), limit=int(above)))
    if _s(position.get("tier")).lower() == "mid":
        reason = _s(position.get("reason"))
        if not reason:
            out.append(Check("warn", "mid_reason_missing"))
        elif not any(_overlap(_copy(ad), reason) >= REASON_OVERLAP for ad in pack["ads"]):
            out.append(Check("warn", "mid_reason_unused", value=reason[:80]))
    return out


# ---------------------------------------------------------------------------
# #18 Relaunch or new launch
# ---------------------------------------------------------------------------

_HERITAGE = re.compile(
    r"(since 1[89]\d\d|nuo 1[89]\d\d|\bgrįžt\w*|\bsugrįž\w*|\bgrįžo\b|\blegendin\w*|\blegendary\b|\bis back\b|\bare back\b|"
    r"\breturns\b|\btradicij\w*|\bheritage\b|\bklasikin\w*|\bclassic\b)",
    re.I,
)


def _launch(pack: dict, facts: str | None) -> list[Check]:
    launch = _obj(pack.get("launch"))
    if not launch:
        return [Check("warn", "launch_missing")] if _lines(facts, "Launch") else []
    kind = _s(launch.get("kind")).lower()
    associations = _items(launch.get("associations"))
    out: list[Check] = []
    for association in associations:
        if not _in_facts(_s(association.get("source"), 500), facts):
            out.append(Check("warn", "association_unsourced", value=_s(association.get("text"))[:80]))
    if kind == "relaunch":
        if not associations:
            out.append(Check("warn", "relaunch_associations_missing"))
        elif not any(_stems(_copy(ad)) & _stems(_s(a.get("text"))) for ad in pack["ads"] for a in associations):
            out.append(Check("warn", "relaunch_association_unused"))
    if kind == "new":
        for n, ad in enumerate(pack["ads"], 1):
            hit = _HERITAGE.search(_copy(ad, pack))
            if hit:
                out.append(Check("warn", "heritage_on_new_name", ad=n, value=hit.group(0)))
    return out


# ---------------------------------------------------------------------------
# #20 One-sentence vision and the hero it justifies
# ---------------------------------------------------------------------------


def _vision(pack: dict, facts: str | None) -> list[Check]:
    vision = _s(pack.get("vision"), 1000)
    if not vision:
        return [Check("warn", "vision_missing")] if _lines(facts, "Vision") else []
    out: list[Check] = []
    words = _WORD.findall(vision)
    sentences = len([s for s in re.split(r"[.!?]+", vision) if s.strip()])
    if len(words) > VISION_WORDS or sentences > 1:
        out.append(Check("warn", "vision_long", n=len(words), limit=VISION_WORDS))
    reason = _s(_obj(pack.get("hero")).get("reason"), 1000)
    if reason:
        low = [w.lower() for w in words]
        flat = " ".join(w.lower() for w in _WORD.findall(reason))
        windows = [" ".join(low[i : i + 4]) for i in range(max(1, len(low) - 3))]
        if not any(window in flat for window in windows):
            out.append(Check("warn", "hero_unjustified"))
    return out


# ---------------------------------------------------------------------------
# #21 Whose requests shape the product
# ---------------------------------------------------------------------------


def _built_for(pack: dict, facts: str | None) -> list[Check]:
    out: list[Check] = []
    who = _s(_obj(pack.get("built_for")).get("who"))
    requests = _items(pack.get("requests"))
    if not who and (requests or _lines(facts, "For")):
        out.append(Check("warn", "built_for_missing"))
    for n, ad in enumerate(pack["ads"], 1):
        if _flag(ad.get("outside_for")):
            out.append(Check("warn", "audience_outside", ad=n))
    for request in requests:
        if _flag(request.get("affects_all")) and _s(request.get("verdict")).lower() == "build":
            if not who or not (_stems(_s(request.get("reason"), 1000)) & _stems(who)):
                out.append(Check("warn", "request_unchecked", value=_s(request.get("from"))[:60] or "?"))
    return out


# ---------------------------------------------------------------------------
# #22 The local optimisation trap
# ---------------------------------------------------------------------------


def _alternative_trial(pack: dict, facts: str | None) -> list[Check]:
    out: list[Check] = []
    losses = _lines(facts, "Loss") + _lines(facts, "Churn")
    prior = _lines(facts, "Prior tests")
    triggered = any("%" in line or "percent" in line.lower() or "proc" in line.lower() for line in losses) and bool(prior)
    trial = _obj(pack.get("alternative_trial"))
    alternative = _s(trial.get("alternative"), 1000)
    if triggered and (not alternative or _overlap(alternative, " ".join(prior)) >= REASON_OVERLAP):
        out.append(Check("warn", "local_optimum"))
    if trial:
        inner = _obj(trial.get("trial"))
        missing = [k for k in ("segment", "days", "metric", "threshold") if not _s(inner.get(k))]
        if missing:
            out.append(Check("warn", "trial_incomplete", value=", ".join(missing)))
    return out


# ---------------------------------------------------------------------------
# #23 The capabilities behind a rival's offer
# ---------------------------------------------------------------------------


def _capabilities(pack: dict, facts: str | None) -> list[Check]:
    capabilities = _items(pack.get("rival_capabilities"))
    if not capabilities:
        return [Check("warn", "rival_capabilities_missing")] if _lines(facts, "Capability") else []
    out: list[Check] = []
    if len(capabilities) < CAPABILITIES:
        out.append(Check("warn", "copy_headline_only", n=len(capabilities), limit=CAPABILITIES))
    for capability in capabilities:
        name = _s(capability.get("capability"))[:80] or "?"
        if _s(capability.get("ours")).lower() not in {"met", "unmet", "unknown"}:
            out.append(Check("warn", "capability_ours_missing", value=name))
        if not _in_facts(_s(capability.get("source"), 500), facts):
            out.append(Check("warn", "capability_unsourced", value=name))
    return out


# ---------------------------------------------------------------------------
# #25 Buying occasions
# ---------------------------------------------------------------------------


def _occasion(ad: dict) -> str:
    """The ad's occasion as text. The field belongs to the buying module ({when, for_whom, kind}); a bare string also works."""
    raw = ad.get("occasion")
    obj = _obj(raw)
    return (_s(obj.get("when")) or _s(obj.get("kind"))) if obj else _s(raw)


def _occasions(pack: dict, facts: str | None) -> list[Check]:
    """A challenger that moves to another occasion must also move where the buyer meets the product."""
    _, control = _control(pack)
    if control is None:
        return []
    base_occasion = _occasion(control).lower()
    base_place = _s((control.get("mix") or {}).get("place")).lower()
    if not base_occasion or not base_place:
        return []
    out: list[Check] = []
    for n, ad in enumerate(pack["ads"], 1):
        occasion = _occasion(ad)
        place = _s((ad.get("mix") or {}).get("place")).lower()
        if ad.get("test_role") == "challenger" and occasion and occasion.lower() != base_occasion and place == base_place:
            out.append(Check("warn", "occasion_place", ad=n, value=occasion[:60]))
    return out


# ---------------------------------------------------------------------------
# #26 #31 What buyers do with the money or the time instead
# ---------------------------------------------------------------------------


def _competitors(facts: str | None) -> list[str]:
    names = []
    for line in _lines(facts, "Competitor"):
        name = re.split(r"\s+[—–-]\s+|[,(:]|https?://", line, maxsplit=1)[0].strip()
        if len(name) >= 3:
            names.append(name)
    return names


def _alternatives(pack: dict, facts: str | None) -> list[Check]:
    out: list[Check] = []
    shared = _items(pack.get("alternatives"))
    own = [(n, _obj(ad.get("alternative"))) for n, ad in enumerate(pack["ads"], 1)]
    own = [(n, alt) for n, alt in own if alt]
    if not shared and not own:
        return [Check("warn", "alternatives_missing")] if _lines(facts, "Alternative") else []
    for alt in shared:
        if not _in_facts(_s(alt.get("source"), 500), facts):
            out.append(Check("warn", "alternative_unsourced", value=_s(alt.get("text"))[:80]))
    for n, alt in own:
        if not _in_facts(_s(alt.get("source"), 500), facts):
            out.append(Check("warn", "alternative_unsourced", ad=n, value=_s(alt.get("what"))[:80]))
    competitors = _competitors(facts)
    targets_shared = " ".join(_s(a.get("text")) for a in shared).lower()
    for n, ad in enumerate(pack["ads"], 1):
        copy = _copy(ad, pack).lower()
        targets = (_s(_obj(ad.get("alternative")).get("what")).lower() or targets_shared)
        for name in competitors:
            if targets and name.lower() in copy and name.lower() not in targets:
                out.append(Check("warn", "wrong_comparison", ad=n, value=name))
    return out


# ---------------------------------------------------------------------------
# #32 Continuity of voice
# ---------------------------------------------------------------------------

_TU = re.compile(r"\b(tu|tavo|tave|tau|tavęs|tavimi|tavyje)\b", re.I)
_JUS = re.compile(r"\b(jūs|jūsų|jus|jums|jumis|jumyse)\b", re.I)


def _voice(pack: dict, facts: str | None) -> list[Check]:
    voice = _obj(pack.get("voice"))
    if not voice:
        return [Check("warn", "voice_missing")] if _lines(facts, "Voice") else []
    out: list[Check] = []
    examples = _items(voice.get("examples"))
    for example in examples:
        if not _in_facts(_s(example.get("source"), 500), facts):
            out.append(Check("warn", "voice_unsourced", value=_s(example.get("text"))[:60]))
    if len(examples) < VOICE_EXAMPLES:
        out.append(Check("warn", "voice_thin", n=len(examples), limit=VOICE_EXAMPLES))
    address = _s(voice.get("address")).lower().replace("ū", "u")
    limit = _num(voice.get("max_exclamations"))
    for n, ad in enumerate(pack["ads"], 1):
        for name in COPY_FIELDS:
            text = _s(ad.get(name), 3000)
            if address == "jus" and _TU.search(text):
                out.append(Check("warn", "voice_address_mismatch", ad=n, field=name, value="jūs"))
            elif address == "tu" and _JUS.search(text):
                out.append(Check("warn", "voice_address_mismatch", ad=n, field=name, value="tu"))
            if limit is not None and text.count("!") > limit:
                out.append(Check("warn", "voice_exclamation", ad=n, field=name, n=text.count("!"), limit=int(limit)))
    return out


# ---------------------------------------------------------------------------
# #33 The stranger test
# ---------------------------------------------------------------------------


def _stranger(pack: dict, facts: str | None) -> list[Check]:
    jobs = _lines(facts, "Job")
    products = _match_lines(facts)
    if not jobs and not products:
        return []
    if _in_facts(_s(_obj(pack.get("known_brand")).get("source"), 500), facts):
        return []
    nouns = _stems(" ".join(jobs + products))
    brand = _s(pack.get("brand"))
    out: list[Check] = []
    for n, ad in enumerate(pack["ads"], 1):
        known = nouns | _stems(_s((ad.get("mix") or {}).get("product")))
        copy = _s(ad.get("headline")) + " " + _s(ad.get("primary_text"), 3000)
        if brand:
            copy = re.sub(re.escape(brand), " ", copy, flags=re.I)
        if known and not (_stems(copy) & known):
            out.append(Check("warn", "borrowed_equity", ad=n))
    return out


# ---------------------------------------------------------------------------
# #34 Buying purpose
# ---------------------------------------------------------------------------

_PURPOSES = ("transform", "equipment", "consume")
_SPEC = re.compile(r"(sertifik\w*|certif\w*|\biso\b|\bcoa\b|\bspec\w*|standart\w*|standard\w*|\be\d{3}\b|partij\w*|\blot\b|\bgmp\b|\bhaccp\b|\bce\b)", re.I)


def _purpose(pack: dict, facts: str | None) -> list[Check]:
    purposes = [_s(ad.get("purpose")).lower() for ad in pack["ads"]]
    if not any(p in _PURPOSES for p in purposes):
        return []
    out: list[Check] = []
    for n, (ad, purpose) in enumerate(zip(pack["ads"], purposes), 1):
        if purpose not in _PURPOSES:
            out.append(Check("warn", "purpose_missing", ad=n))
            continue
        standards = _strings(ad.get("standards"))
        if purpose in ("transform", "equipment") and not any(standards) and not _SPEC.search(_copy(ad, pack)):
            out.append(Check("warn", "purpose_no_standard", ad=n))
        for item in _items(ad.get("standards")):
            source = _s(item.get("source"), 500)
            if source and not _in_facts(source, facts):
                out.append(Check("warn", "standard_unsourced", ad=n, value=_s(item.get("text"))[:80]))
    texts = [re.sub(r"\s+", " ", _s(ad.get("primary_text"), 3000).lower()) for ad in pack["ads"]]
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            if texts[i] and texts[i] == texts[j] and purposes[i] in _PURPOSES and purposes[j] in _PURPOSES and purposes[i] != purposes[j]:
                out.append(Check("warn", "purpose_same_pitch", ad=i + 1, value=f"{i + 1}, {j + 1}"))
    return out


# ---------------------------------------------------------------------------
# #35 One standard across suppliers
# ---------------------------------------------------------------------------


def _brand_standard(pack: dict, facts: str | None) -> list[Check]:
    standard = _obj(pack.get("brand_standard"))
    suppliers = _items(standard.get("suppliers"))
    if len(suppliers) < 2:
        return []
    out: list[Check] = []
    if not _items(standard.get("attributes")):
        out.append(Check("warn", "standard_missing", n=len(suppliers)))
    domains = [_domain(_s(s.get("url"), 500)) for s in suppliers]
    for claim in pack.get("claims") or []:
        domain = _domain(claim.get("source", ""))
        if domain and sum(1 for d in domains if d and d == domain) == 1:
            out.append(Check("warn", "claim_single_origin", value=claim["text"][:80], n=len(suppliers)))
    # Four letters, not five: "Kreta" inflects to "Kretos" and "Kretoje".
    origins = [(o, o.lower()[:4]) for s in suppliers if len(o := _s(s.get("origin"))) >= 4]
    for n, ad in enumerate(pack["ads"], 1):
        stems = {w.lower()[:4] for w in _WORD.findall(_copy(ad, pack)) if len(w) >= 4}
        for origin, stem in origins:
            if stem in stems:
                out.append(Check("warn", "origin_in_copy", ad=n, value=origin))
    return out


# ---------------------------------------------------------------------------
# #36 Buying stage
# ---------------------------------------------------------------------------

_STAGES = ("out_of_market", "assessing")
_VERSUS = re.compile(r"(\bvs\.?\b|\bversus\b|\bprieš\b|\bpalyginti su\b|\bcompared to\b)", re.I)


def _stage(pack: dict, facts: str | None) -> list[Check]:
    stages = [_s(ad.get("stage")).lower() for ad in pack["ads"]]
    out: list[Check] = []
    raw = pack.get("in_market_share")
    share_obj = _obj(raw)
    share = _num(share_obj.get("value")) if share_obj else _num(raw)
    if share is not None and (not share_obj or _flag(share_obj.get("assumed")) or not _s(share_obj.get("source"))):
        out.append(Check("warn", "in_market_assumed", value=f"{round(share * 100)} %"))
    if not any(s in _STAGES for s in stages):
        return out
    rivals = _competitors(facts) + [_s(_obj(ad.get("versus")).get("them")) for ad in pack["ads"]]
    rivals = [r for r in rivals if len(r) >= 3]
    for n, (ad, stage) in enumerate(zip(pack["ads"], stages), 1):
        if stage not in _STAGES:
            out.append(Check("warn", "stage_missing", ad=n))
            continue
        versus = _obj(ad.get("versus"))
        if stage == "assessing":
            if not _s(versus.get("attribute")):
                out.append(Check("warn", "versus_missing", ad=n))
            elif not _in_facts(_s(versus.get("source"), 500), facts):
                out.append(Check("warn", "versus_unsourced", ad=n, value=_s(versus.get("attribute"))[:80]))
        else:
            if not _s(ad.get("memory_hook")):
                out.append(Check("warn", "memory_hook_missing", ad=n))
            copy = _copy(ad, pack)
            hit = _VERSUS.search(copy)
            named = next((r for r in rivals if r.lower() in copy.lower()), "")
            if hit or named:
                out.append(Check("warn", "comparison_out_of_market", ad=n, value=named or hit.group(0)))
    if all(s == "assessing" for s in stages) and share is not None and share < IN_MARKET_LOW:
        out.append(Check("warn", "pack_all_assessing", value=f"{round(share * 100)} %"))
    return out


# ---------------------------------------------------------------------------
# #37 Selling the relationship after the sale
# ---------------------------------------------------------------------------


def _service(pack: dict, facts: str | None) -> list[Check]:
    service = _obj(pack.get("service"))
    offered = _flag(service.get("offered"))
    kinds = [_s(ad.get("angle_kind")).lower() for ad in pack["ads"]]
    out: list[Check] = []
    for n, kind in enumerate(kinds, 1):
        if kind == "service" and (not offered or not _in_facts(_s(service.get("source"), 500), facts)):
            out.append(Check("warn", "service_unsourced", ad=n))
    if offered and len(kinds) >= 3 and all(k == "feature" for k in kinds):
        out.append(Check("warn", "service_angle_missing"))
    return out


# ---------------------------------------------------------------------------
# #54 Lead users on two dimensions
# ---------------------------------------------------------------------------


def _lead_users(pack: dict, facts: str | None) -> list[Check]:
    out: list[Check] = []
    for user in _items(pack.get("lead_users")):
        built, stake = _obj(user.get("built")), _obj(user.get("stake"))
        missing = []
        if not _in_facts(_s(built.get("url"), 500), facts):
            missing.append("built_url")
        if not _in_facts(_s(stake.get("url"), 500), facts):
            missing.append("stake_url")
        if not _NUMBER.search(_s(stake.get("text"), 1000)):
            missing.append("stake_number")
        if missing:
            out.append(Check("warn", "lead_user_unqualified", field=", ".join(missing), value=_s(user.get("name"))[:60] or "?"))
    return out


# ---------------------------------------------------------------------------


def check(pack: dict, facts: str | None, images: dict[int, str]) -> list[Check]:
    if not pack.get("ads"):
        return []
    checks: list[Check] = []
    checks.extend(_mix(pack))
    checks.extend(_job(pack, facts))
    checks.extend(_audience(pack))
    checks.extend(_repositioning(pack, facts))
    checks.extend(_stakes(pack, facts))
    checks.extend(_portfolio(pack, facts))
    checks.extend(_new_market(pack, facts))
    checks.extend(_line(pack, facts))
    checks.extend(_comparison(pack, facts))
    checks.extend(_ladder(pack, facts))
    checks.extend(_launch(pack, facts))
    checks.extend(_vision(pack, facts))
    checks.extend(_built_for(pack, facts))
    checks.extend(_alternative_trial(pack, facts))
    checks.extend(_capabilities(pack, facts))
    checks.extend(_occasions(pack, facts))
    checks.extend(_alternatives(pack, facts))
    checks.extend(_voice(pack, facts))
    checks.extend(_stranger(pack, facts))
    checks.extend(_purpose(pack, facts))
    checks.extend(_brand_standard(pack, facts))
    checks.extend(_stage(pack, facts))
    checks.extend(_service(pack, facts))
    checks.extend(_lead_users(pack, facts))
    return checks
