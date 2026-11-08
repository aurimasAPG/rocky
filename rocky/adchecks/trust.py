"""Trust checks for the ad board: word of mouth, PR and endorsements, retail and sales, media placement, measurement.

Every check here settles something by code, without an opinion: whether a paid voice says it was paid,
whether a green word has a baseline behind it, whether a clearance ad names the flaw, whether a launch
sequence gives each message its own job. Missing optional input is one ``<field>_missing`` warning;
a finding blocks only when the ad could not run truthfully as written (an undisclosed defect, a
sustainability promise with nothing measurable behind it).

Two pack kinds extend the board beyond feed ads: ``kind = "event_plan"`` (a live demonstration with host
economics) and ``kind = "sequence"`` (launch messages or inquiry follow-ups). They reuse the ads list and
the findings list; their own fields live at pack level.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from rocky.adboard import _NUMBER, Check, _bare_url, _is_url
from rocky.lang import detect_language
from rocky.pageinspect import content_words, same_stem, shared_words

AD_FIELDS = (
    "endorsements", "referral", "buying_situation", "entry_use_case", "defect", "placement", "tracking",
    "shows", "benefits", "record", "proof_test",
)
PACK_FIELDS = (
    "kind", "wom", "visibility", "retell", "backer", "green", "proof_actions", "demo", "event", "stockist",
    "channel", "chain", "display", "co_brand", "pack", "activation", "sequence", "inquiries", "outcomes", "range",
)

KINDS = ("ads", "event_plan", "sequence")
ENDORSER_KINDS = ("independent", "paid", "affiliate", "owned")
BACKERS = ("independent_review", "industry_body", "self", "customer", "none")
GRADES = ("new", "seconds", "returned", "damaged", "discontinued", "short_dated", "unknown")
SITUATIONS = ("straight_rebuy", "modified_rebuy", "new_task")
STAGES = ("manufacturer", "distributor", "retailer", "direct", "marketplace")
LEVERS = ("availability", "price", "promo", "service")
INTENSITIES = ("intensive", "selective", "exclusive")
INFLUENCES = ("sets", "recommends", "stocks", "buys")
JOBS = ("teaser", "preview", "launch")
OUTCOME_KINDS = ("awareness", "fame", "consideration", "sales", "other")
BENEFIT_KINDS = ("price", "constraint", "convenience", "status", "risk")
# Seconds a viewer realistically has per placement (The Psychology of Advertising, pp. 234–236); None = the feed limits apply.
DWELL_SECONDS: dict[str, float | None] = {
    "feed": None, "story": 5, "reel": 6, "bus_stop_roadside": 3, "bus_stop_pedestrian": 30, "in_vehicle": 10,
    "billboard_highway": 4, "billboard_street": 6, "print_page": 20, "pre_roll_6s": 6, "pre_roll_15s": 15,
}
GLANCE_RATE, SUSTAINED_RATE, LT_FACTOR = 2.5, 3.0, 0.8  # words per second; Lithuanian words run longer
MIN_EXECUTION = 0.8  # share of audited stores where a paid display must be up before the offer is judged

_WORDS = re.compile(r"[^\W_]+", re.UNICODE)
_REFERRAL = re.compile(r"(share|invite|refer|dalin|pakvies|kvies|rekomend)", re.I)
_DISCLOSURE = re.compile(
    r"(#reklama|#ad\b|#sponsored|#remiama|#apmokėta|remiam\w* partneryst\w*|paid partnership|sponsored|apmokėt\w*|reklaminė partnerystė)",
    re.I,
)
_VIRTUE = re.compile(r"\b(kokyb\w*|rūpi\w*|rūpest\w*|saugum\w*|patikim\w*|quality|care|caring|safety|reliab\w*)\b", re.I)
_GREEN = re.compile(
    r"\b(tvar(us|i|ios|ūs|iai|ių|ią|umo|umas|umą|ioji|usis)|ekologišk\w*|žal(ias|ia|ios|i|ią|ioji|iuosius)|draugišk\w* (gamtai|aplinkai)|"
    r"klimatui neutral\w*|perdirbam\w*|biologiškai skaid\w*|be plastiko|sustainab\w*|eco[- ]?friendly|eco|green|"
    r"carbon[- ]neutral|climate[- ]neutral|recyclab\w*|biodegrad\w*|plastic[- ]free|zero[- ]waste)\b",
    re.I,
)
_DISCOUNT = re.compile(r"(-\s?\d+\s?%|\d+\s?%\s?(off|nuolaid)|\bnuolaid\w*|išpardavim\w*|\boutlet\b|\bsale\b|\bclearance\b|€\s*vietoj|vietoj\s*\d)", re.I)
_MINIMISED = re.compile(r"(like new|as new|kaip nauj\w*|nepriekaišting\w*|be defektų|flawless|perfect condition|idealios būklės)", re.I)
_PASSIVE_ACT = re.compile(r"\b(rod\w*|pristat\w*|paaiškin\w*|dalij\w*|dalin\w*|papasakoj\w*|show\w*|present\w*|explain\w*|hand out|tell)\b", re.I)
_ACTIVE_ACT = re.compile(r"(ragau\w*|kep\w*|palygin\w*|išband\w*|band\w*|matuo\w*|kelia|kilnoj\w*|lieč\w*|uost\w*|taste|tast\w*|bake|compare|lift|try|tries|test\w*|measure|touch|smell|weigh)", re.I)
_GENERIC_CTA = frozenset({"learn more", "sužinoti daugiau", "sužinok daugiau", "sužinokite daugiau", "read more", "skaityti daugiau", "skaitykite daugiau", "see more", "plačiau"})
_BREADTH = re.compile(r"\b(visur|visose( \w+)? parduotuvėse|visose prekybos vietose|everywhere|(at )?every( \w+)? store|any retailer)\b", re.I)
_REORDER = re.compile(r"(re-?order|order again|same as last time|užsisakyk\w* vėl|pakartotin\w* užsakym\w*|užsakyk\w* dar kartą)", re.I)
_EDUCATION = re.compile(r"(\bwhat is\b|\bwhy you need\b|\bkas yra\b|\bkodėl reikia\b|\bkodėl verta\b)", re.I)
_COMPANY = re.compile(r"\b(UAB|AB|MB|VšĮ|SIA|OÜ|GmbH|Ltd|Inc)\b")
_CITY = re.compile(
    r"\b(vilni|kaun|klaipėd|šiauli|panevėž|alyt|marijampol|mažeik|jonav|kėdain|telši|taurag|ukmerg|visagin|plung|kretin|palang|druskinink|šilut|ryg|rīg|tallinn|talin)\w*",
    re.I,
)
_RETAIL = re.compile(
    r"\b(maxim\w*|rimi\w*|norf\w*|lidl\w*|senuk\w*|depo|ermitaž\w*|topo centr\w*|narvesen\w*|circle k|benu|eurovaistin\w*|cameli\w*|distribut\w*|platintoj\w*|"
    r"didmen\w*|wholesal\w*|retailer\w*|mažmen\w*|dealer\w*|prekybos (tinkl\w*|centr\w*)|lentyn\w*|shelf|shelves|kavin\w*|cafés?|parduotuvėse|in[- ]store|stores?)\b",
    re.I,
)
_TRADE = re.compile(
    r"(distribut|platintoj|didmen|wholesal|dealer|atstov|rangov|montuotoj|install|dizainer|designer|architekt|architect|specif|senukai|maxima|rimi|norfa|lidl|prekybos tinkl)",
    re.I,
)
_BRAND_LED = re.compile(r"(brand|žinomum|awareness|prekės ženkl|fame|įvaizd)", re.I)
_BUY = re.compile(r"(\bbuy\b|order|subscribe|purchase|pirk|užsisak|prenumeruo|įsigy|start for)", re.I)
_PRICE_WORDS = re.compile(r"(€|\beur\b|kain\w*|price|\bcost)", re.I)
_AVAILABILITY = re.compile(r"(available at|rasite|įsigysite|parduodam\w*)", re.I)
_PROMO = re.compile(r"(\d\s?\+\s?\d|bundle|dovan\w*|gift|\bfree\b|nemokam\w*|akcij\w*)", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_MARKETER = frozenset({"us", "we", "marketer", "mes", "own", "brand", "manufacturer"})

SCHEMA: dict[str, dict[str, Any]] = {
    "ad": {
        "endorsements": {
            "type": "array",
            "description": "every person or outlet whose word the ad borrows, with whether and how much they were paid",
            "items": {"type": "object", "properties": {
                "who": {"type": "string"},
                "kind": {"type": "string", "enum": list(ENDORSER_KINDS), "description": "independent only when nobody paid them"},
                "paid_amount": {"type": "number", "description": "EUR, when paid"},
                "disclosure_text": {"type": "string", "description": "the label in the ad (#reklama, Paid partnership) if it is not in the copy"},
                "source": {"type": "string", "description": "for independent voices: the URL of the review, present in runs/notes/facts.md"},
            }},
        },
        "referral": {
            "type": "object",
            "description": "for a share or invite ad: what recommending does for the recommender, not what they are paid",
            "properties": {
                "payoff": {"type": "string", "description": "one sentence on what the sharer gains socially"},
                "kind": {"type": "string", "enum": ["look_knowledgeable", "help_someone", "belong", "other"]},
                "source": {"type": "string", "description": "a URL where an advocate says this, or the word hypothesis"},
            },
        },
        "buying_situation": {"type": "string", "enum": list(SITUATIONS), "description": "whether this audience reorders, changes a specification or buys something unfamiliar"},
        "entry_use_case": {
            "type": "object",
            "description": "for an ad aimed at a named company: the department with an urgent application and who there can buy",
            "properties": {
                "department": {"type": "string"},
                "application": {"type": "string", "description": "what they would use it for this quarter"},
                "evidence_url": {"type": "string", "description": "dated evidence of the urgency, present in runs/notes/facts.md"},
                "budget_owner": {"type": "string"},
            },
        },
        "defect": {
            "type": "object",
            "description": "for clearance, returned or damaged stock: the grade, the flaw in plain words and why the price is lower",
            "properties": {
                "grade": {"type": "string", "enum": list(GRADES)},
                "flaw": {"type": "string", "description": "what is wrong with the goods, as the buyer would see it"},
                "price_reason": {"type": "string", "description": "why it costs less"},
            },
        },
        "placement": {
            "type": "object",
            "description": "where this ad will be seen when it is not a plain feed ad: the slot, how long a viewer has, and what sits around it",
            "properties": {
                "name": {"type": "string", "description": "one of " + ", ".join(DWELL_SECONDS) + ", or a custom name with dwell_seconds"},
                "dwell_seconds": {"type": "number", "description": "seconds a viewer realistically has; required for a custom name"},
                "surface": {"type": "string", "description": "the publisher section or screen, e.g. 15min.lt Maistas, 300x600"},
                "url": {"type": "string", "description": "the page the ad will sit on"},
                "adjacency_checked": {"type": "boolean", "description": "true once the surrounding imagery and headlines were previewed"},
                "adjacency_note": {"type": "string", "description": "what surrounds the slot and the verdict: keep, avoid or retime"},
            },
        },
        "tracking": {
            "type": "object",
            "description": "one trackable link per ad and placement so later inquiries trace back to the combination",
            "properties": {
                "landing_url": {"type": "string"},
                "campaign": {"type": "string", "description": "utm_campaign"},
                "placements": {"type": "array", "items": {"type": "object", "properties": {
                    "name": {"type": "string"}, "utm_content": {"type": "string", "description": "defaults to ad<n>_<placement>"},
                }}},
            },
        },
        "shows": {
            "type": "object",
            "description": "what the visual depicts and its price, so the pack can be compared with the typical range",
            "properties": {"item": {"type": "string"}, "price_eur": {"type": "number"}},
        },
        "benefits": {
            "type": "array",
            "description": "every benefit the offer expresses, one entry per kind, so a lift is not attributed to price alone",
            "items": {"type": "object", "properties": {"kind": {"type": "string", "enum": list(BENEFIT_KINDS)}, "text": {"type": "string"}}},
        },
        "record": {
            "type": "object",
            "description": "for an ad that already ran: where, with what offer, what it cost and what it made, with the file or URL the figures come from",
            "properties": {
                "placement": {"type": "string"}, "offer": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"},
                "spend_eur": {"type": "number"}, "impressions": {"type": "integer"}, "clicks": {"type": "integer"}, "leads": {"type": "integer"},
                "sales": {"type": "integer"}, "revenue_eur": {"type": "number"}, "source": {"type": "string"},
            },
        },
        "proof_test": {"type": "boolean", "description": "true when this control/challenger pair tests only who backs the proof, on otherwise identical copy"},
    },
    "pack": {
        "kind": {"type": "string", "description": "ads (default), event_plan for a live demonstration, or sequence for launch or follow-up messages"},
        "wom": {
            "type": "object",
            "description": "the existing group where this product's buyers already talk to one another and the standard they judge by",
            "properties": {
                "group": {"type": "string"}, "venue_url": {"type": "string", "description": "where they talk; the URL must be in runs/notes/facts.md"},
                "shared_standard": {"type": "string"}, "members": {"type": "integer", "description": "member count or posts per week, from the venue page"},
            },
        },
        "visibility": {
            "type": "array",
            "description": "optional ways a user can show something normally private, each with the metric that would show conversations start",
            "items": {"type": "object", "properties": {
                "what": {"type": "string"}, "opt_in": {"type": "boolean"}, "surface": {"type": "string", "enum": ["profile", "feed", "email", "physical", "other"]},
                "metric": {"type": "string"}, "baseline": {"type": "number"}, "unit": {"type": "string"},
            }},
        },
        "retell": {
            "type": "object",
            "description": "the one measurable extreme a recommender can repeat, with the competitor values that make it extreme",
            "properties": {
                "claim": {"type": "string"}, "metric": {"type": "string"}, "unit": {"type": "string"}, "our_value": {"type": "number"},
                "direction": {"type": "string", "enum": ["higher_better", "lower_better"]},
                "competitors": {"type": "array", "items": {"type": "object", "properties": {
                    "name": {"type": "string"}, "value": {"type": "number"}, "source": {"type": "string", "description": "URL in runs/notes/facts.md"},
                }}},
            },
        },
        "backer": {
            "type": "array",
            "description": "who stands behind each claim used as proof: an independent tester, a trade body, a customer, or the brand itself",
            "items": {"type": "object", "properties": {"claim": {"type": "string", "description": "the claim text as listed in claims"}, "backer": {"type": "string", "enum": list(BACKERS)}}},
        },
        "green": {
            "type": "array",
            "description": "for every sustainability word in the copy: what changed, from what baseline, by how much, and where that is published",
            "items": {"type": "object", "properties": {
                "subject": {"type": "string", "description": "the material, activity or community affected"},
                "baseline": {"type": "object", "properties": {"value": {"type": "number"}, "unit": {"type": "string"}, "date": {"type": "string", "description": "YYYY-MM"}}},
                "change": {"type": "object", "properties": {"value": {"type": "number"}, "unit": {"type": "string"}}},
                "source": {"type": "string", "description": "URL in runs/notes/facts.md"},
            }},
        },
        "proof_actions": {
            "type": "array",
            "description": "for abstract words like quality or care: one concrete action a customer could photograph, and where it is visible",
            "items": {"type": "object", "properties": {
                "claim": {"type": "string"}, "what": {"type": "string"}, "where_visible": {"type": "string"},
                "source": {"type": "string", "description": "URL in runs/notes/facts.md or the marketer's file"},
            }},
        },
        "demo": {
            "type": "object",
            "description": "for kind event_plan: the claim demonstrated, the act a visitor performs to judge it, and what they take home",
            "properties": {
                "claim": {"type": "string"}, "act": {"type": "string", "description": "what the visitor does or witnesses, not what staff present"},
                "judges_by": {"type": "string"}, "takeaway": {"type": "string"},
            },
        },
        "event": {
            "type": "object",
            "description": "for kind event_plan: the venue and what the host gains, with the host's own metrics and baselines",
            "properties": {
                "venue": {"type": "string"}, "date": {"type": "string"}, "budget_eur": {"type": "number"},
                "host": {"type": "object", "properties": {
                    "name": {"type": "string"},
                    "flow_check": {"type": "string", "description": "the venue's answer on how ordinary customers reach checkout during the event, verbatim"},
                    "host_kpis": {"type": "array", "items": {"type": "object", "properties": {
                        "metric": {"type": "string"}, "baseline": {"type": "number"}, "unit": {"type": "string"}, "source": {"type": "string"},
                    }}},
                    "our_kpis": {"type": "array", "items": {"type": "object", "properties": {"metric": {"type": "string"}, "target": {"type": "number"}, "unit": {"type": "string"}}}},
                }},
            },
        },
        "stockist": {
            "type": "object",
            "description": "for an offer tied to a place: where people outside it can redeem or buy, so the demand is not lost",
            "properties": {
                "catchment": {"type": "string"},
                "stockists": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "city": {"type": "string"}, "url": {"type": "string"}}}},
                "out_of_area_route": {"type": "string", "description": "the nearest stockist or online route for people outside the catchment"},
            },
        },
        "channel": {
            "type": "object",
            "description": "who owns each stage between you and the buyer and which levers (availability, price, promo, service) each one decides, plus how widely you distribute",
            "properties": {
                "stages": {"type": "array", "items": {"type": "object", "properties": {
                    "stage": {"type": "string", "enum": list(STAGES)}, "owner": {"type": "string", "description": "the company; us for your own stage"},
                    "contract_with": {"type": "string"}, "decides": {"type": "array", "items": {"type": "string", "enum": list(LEVERS)}},
                }}},
                "intensity": {"type": "string", "enum": list(INTENSITIES)},
                "service_need": {"type": "string", "enum": ["low", "high"]},
            },
        },
        "chain": {
            "type": "array",
            "description": "every role that sets, recommends, stocks or buys before the end buyer chooses, with a source for each",
            "items": {"type": "object", "properties": {
                "role": {"type": "string"}, "influence": {"type": "string", "enum": list(INFLUENCES)},
                "share_pct": {"type": "number"}, "source": {"type": "string", "description": "URL in runs/notes/facts.md or interview:<name>"},
            }},
        },
        "display": {
            "type": "object",
            "description": "a paid in-store display: where it goes, in how many stores, when, and how its execution is verified before the offer is judged",
            "properties": {
                "retailer": {"type": "string"}, "fee_eur": {"type": "number"}, "stores": {"type": "integer"}, "start": {"type": "string"}, "end": {"type": "string"},
                "location": {"type": "string"}, "signage": {"type": "array", "items": {"type": "string"}}, "materials": {"type": "array", "items": {"type": "string"}},
                "verification": {"type": "object", "properties": {"method": {"type": "string"}, "sample_stores": {"type": "integer"}, "due_date": {"type": "string"}}},
                "audit": {"type": "array", "items": {"type": "object", "properties": {"store": {"type": "string"}, "date": {"type": "string"}, "present": {"type": "boolean"}, "photo": {"type": "string"}}}},
            },
        },
        "co_brand": {
            "type": "object",
            "description": "for materials a partner runs under their own name: which brand hosts, which endorses, whose link the button opens, and who owns each claim",
            "properties": {
                "partner": {"type": "string"}, "host_brand": {"type": "string"}, "endorser_brand": {"type": "string"},
                "partner_cta": {"type": "boolean", "description": "true when the button must lead to the partner's site"},
                "partner_url": {"type": "string"},
                "claim_owners": {"type": "array", "items": {"type": "object", "properties": {"claim": {"type": "string"}, "brand": {"type": "string", "description": "host or endorser"}}}},
            },
        },
        "pack": {
            "type": "object",
            "description": "for a product sold on a shelf: the cues a shopper finds it by, which of them the creative shows, and which competitors share them",
            "properties": {
                "sold_in_retail": {"type": "boolean"},
                "distinctive_assets": {"type": "array", "items": {"type": "object", "properties": {"cue": {"type": "string"}, "kind": {"type": "string", "enum": ["color", "shape", "label", "logo"]}}}},
                "shown_in_creative": {"type": "array", "items": {"type": "string"}},
                "competitor_overlap": {"type": "array", "items": {"type": "object", "properties": {"cue": {"type": "string"}, "competitor": {"type": "string"}}}},
            },
        },
        "activation": {
            "type": "object",
            "description": "for buyers evaluating now: the concrete offer, the easy next step, and what counts as a qualified response",
            "properties": {
                "stage": {"type": "string", "enum": ["in_market", "out_of_market"]}, "offer": {"type": "string"}, "next_step": {"type": "string"},
                "qualified_response": {"type": "string", "description": "who must do what for a response to count, e.g. audit booked by a manufacturer with 20+ staff"},
                "baseline_qualified_rate_pct": {"type": "number"},
            },
        },
        "sequence": {
            "type": "array",
            "description": "for kind sequence: the messages in send order, each with its job (teaser, preview, launch) or the inquiry it answers",
            "items": {"type": "object", "properties": {
                "job": {"type": "string", "enum": list(JOBS)}, "inquiry": {"type": "string", "description": "the lead's question this message answers, verbatim"},
                "subject": {"type": "string"}, "body": {"type": "string"}, "cta": {"type": "string"}, "cta_url": {"type": "string"},
                "send_offset_days": {"type": "integer", "description": "relative to launch day"},
            }},
        },
        "inquiries": {
            "type": "array",
            "description": "the questions leads actually asked, with how many asked each, so every group gets a message that answers it first",
            "items": {"type": "object", "properties": {"text": {"type": "string"}, "count": {"type": "integer"}, "offer": {"type": "string"}}},
        },
        "outcomes": {
            "type": "array",
            "description": "how the campaign will be judged: recognition (awareness) and conversation (fame) are separate metrics with their own question or count",
            "items": {"type": "object", "properties": {"metric": {"type": "string"}, "kind": {"type": "string", "enum": list(OUTCOME_KINDS)}, "source": {"type": "string"}}},
        },
        "range": {
            "type": "object",
            "description": "the typical price of what the shop sells, so the visuals are not all of the hero item",
            "properties": {"typical_price_eur": {"type": "number"}, "hero_price_eur": {"type": "number"}, "typical_share": {"type": "number"}},
        },
    },
}

MESSAGES = {
    "en": {
        "wom_missing": "Referral ads need the group where these buyers already talk to each other: add wom (group, venue_url, shared_standard, members).",
        "wom_venue_unknown": "The community venue “{value}” is not in runs/notes/facts.md: fetch it so the group is real.",
        "wom_unsized": "The community “{value}” has no size: add members (count or posts per week) from the venue page.",
        "wom_share_not_optional": "Showing “{value}” is not opt-in. Visibility must be the user's choice or it starts no conversations.",
        "wom_share_no_metric": "“{value}” has no conversation metric and unit (shares per active user, replies, referral sign-ups).",
        "wom_share_no_baseline": "“{value}” has no baseline: measure the current rate for two weeks before promising a lift.",
        "referral_missing": "This ad asks people to share or invite but does not say what the sharer gains: add referral.payoff.",
        "referral_unverified": "The recommender payoff is a hypothesis. Ask advocates: what did recommending it do for you? Did it make you look knowledgeable or help someone you care about?",
        "retell_missing": "A referral push needs one verifiable extreme worth retelling: add retell with our value and at least two competitor values.",
        "retell_thin": "The distinction compares against {n} competitor(s); two or more are needed before calling it extreme.",
        "retell_not_extreme": "“{value}” is not the extreme: a competitor is at least as good. It is an advantage, not a story to retell.",
        "retell_unsourced": "Competitor “{value}” has no source URL in runs/notes/facts.md for its value.",
        "retell_not_in_copy": "No headline or text carries the distinction “{value}”: at least one ad should lead with it.",
        "pr_kind_missing": "Endorser “{value}”: say whether they are independent, paid, affiliate or owned. Unknown is never independent.",
        "pr_undisclosed": "“{value}” was paid but the ad carries no disclosure (#reklama, Remiama partnerystė, Paid partnership).",
        "pr_unverified": "“{value}” is listed as independent but the review's URL is not in runs/notes/facts.md.",
        "pr_proof_missing": "“{value}” is an abstract virtue: add a proof_action a customer could photograph, and where it is visible.",
        "pr_proof_unsourced": "Proof action “{value}”: the source is not a URL in runs/notes/facts.md or a file the marketer supplied.",
        "backer_missing": "Claim “{value}” is used in a proof test but names no backer (independent_review, industry_body, customer, self).",
        "backer_not_independent": "Every backer is “{value}”: a self-issued claim is not an endorsement, so there is no independent arm to test.",
        "backer_test_confounded": "The proof test also changes the {field}: the control and challenger must differ only in the proof sentence.",
        "green_unmeasured": "“{value}” is a sustainability promise with nothing measurable behind it. Add green (subject, baseline with date, change, source) or remove the word.",
        "green_unit_mismatch": "Sustainability entry “{value}”: baseline and change use different units.",
        "green_source_unknown": "Sustainability entry “{value}”: the source URL is not in runs/notes/facts.md.",
        "defect_missing": "The copy names a discount but not the condition of the goods: add defect (grade, flaw, price_reason), or grade new.",
        "defect_undisclosed": "Grade “{value}” but the ad does not say what is wrong or why it costs less. State the flaw and the price reason.",
        "defect_not_in_copy": "The flaw “{value}” is not in the copy. Say it plainly so the buyer can judge what is left.",
        "defect_minimised": "“{value}” plays down a known flaw on graded stock. Describe the flaw instead.",
        "demo_missing": "An event plan needs demo: the claim demonstrated, the act a visitor performs, what they judge by, and what they take home.",
        "demo_no_act": "The demonstration has no act. Name what the visitor does or witnesses that lets them judge the claim.",
        "demo_passive_act": "“{value}” only shows or explains. Visitors must taste, compare, lift or time it themselves.",
        "demo_no_takeaway": "No takeaway: give visitors something that lets them repeat the judgment at home.",
        "demo_claim_unsourced": "The demonstrated claim carries “{value}” with no source in claims.",
        "event_missing": "An event plan needs event.host: the venue's answer on customer flow and at least one host KPI with a baseline.",
        "event_no_flow_check": "Nobody asked the venue how ordinary customers reach checkout during the event. Record the answer verbatim in host.flow_check.",
        "event_no_host_kpi": "The plan measures only our side. Add at least one host KPI (host category sales, till receipts with the SKU).",
        "event_kpi_no_baseline": "Host KPI “{value}” has no baseline, so the host cannot see what the event changed.",
        "stockist_missing": "The offer is tied to “{value}” but there is no route for people outside it: add stockist (catchment, stockists, out_of_area_route).",
        "stockist_route_missing": "People outside “{value}” have no way to redeem: name the nearest stockist or the online route.",
        "stockist_url_unknown": "Stockist “{value}”: the URL is not in runs/notes/facts.md.",
        "trade_situation_missing": "Other ads name a buying situation; this one does not. Is this audience reordering, changing a specification or buying something new?",
        "trade_situation_mismatch": "A “{value}” audience gets copy for a different situation: new buyers need guidance, not a reorder button; reorderers need no category pitch.",
        "trade_channel_missing": "The ad names a retailer or distributor but channel is empty: say who owns each stage and which levers they decide.",
        "trade_lever_not_controlled": "The ad touches {field}, which no stage you own decides. Agreement is needed from: {value}.",
        "trade_intensity_missing": "The channel has stages but no intensity: intensive, selective or exclusive, matched to how buyers shop.",
        "trade_intensity_mismatch": "The copy promises availability everywhere, but distribution is {value}: name the outlets instead.",
        "trade_intensity_service": "Intensive distribution with a high service need: buyers who need a demonstration or service will not get it at every outlet.",
        "trade_entry_missing": "The audience names a company but no entry_use_case: which department needs this now, and who there can sign?",
        "trade_entry_unsourced": "Entry use case for “{value}”: the urgency evidence URL is not in runs/notes/facts.md.",
        "trade_display_incomplete": "The display agreement does not name {field}.",
        "trade_display_unverified": "No store has been audited: do not judge the offer until the display is confirmed in the sample stores.",
        "trade_display_audit_short": "Only {n} of {limit} sample stores audited: the offer cannot be judged yet.",
        "trade_display_execution_low": "The display was up in {n} of {limit} audited stores. Below 80 %, the result says nothing about the offer.",
        "trade_cobrand_cta_mismatch": "partner_cta is set but the ads link to “{value}”, not the partner's site.",
        "trade_cobrand_claims_unowned": "Claim “{value}” has no brand owner (host or endorser) in co_brand.claim_owners.",
        "trade_cobrand_brand_repeated": "“{value}” appears more than once in the headline: each brand once.",
        "trade_chain_missing": "The product reaches buyers through a trade or retail chain, but chain is empty: who sets, recommends, stocks and buys?",
        "trade_chain_upstream_missed": "“{value}” sets or recommends the choice, but no ad is aimed at them.",
        "trade_chain_no_buyer": "The decision chain names nobody who buys.",
        "trade_chain_source_unknown": "Chain role “{value}”: the source is not a URL in runs/notes/facts.md or an interview:<name> note.",
        "trade_chain_shares_exceed": "Decision shares add up to {n} %: they cannot exceed 110.",
        "pack_missing": "The product is sold on a shelf, but pack is empty: which colours, shapes or label details will shoppers look for?",
        "pack_not_carried": "The creative shows none of the pack's distinctive cues: a shopper who saw the ad cannot find the pack on the shelf.",
        "pack_not_distinct": "Every distinctive cue is shared with a competitor ({value}): foreground a cue only this pack has.",
        "pack_cue_unknown": "“{value}” is listed as shown in the creative but is not a distinctive asset of the pack.",
        "activation_no_offer": "These buyers are evaluating now, but the ads carry no specific offer: add activation.offer.",
        "activation_weak_step": "“{value}” is not an easy next step for an in-market buyer: offer a slot, an audit, a sample.",
        "activation_no_metric": "No qualified response is defined: say who must do what for a response to count. Clicks do not count.",
        "placement_adjacency_unchecked": "Placement “{value}” named but the surrounding content was not previewed: snapshot the page and note what sits around the slot.",
        "placement_adjacency_note_missing": "Adjacency marked as checked for “{value}” but there is no note on what surrounds the slot.",
        "placement_tracking_missing": "Tracking has no {field}: every ad and placement needs its own tagged link.",
        "placement_tracking_dup": "utm_content “{value}” is used for more than one ad or placement: later inquiries cannot be traced.",
        "dwell_unknown": "Placement “{value}” is not in the dwell table and has no dwell_seconds: say how long a viewer has.",
        "dwell_too_long": "{Field}: {n} words where a viewer has {limit} seconds ({value}). Cut the text.",
        "seq_missing": "A sequence pack has no messages.",
        "seq_dup_job": "Two messages share the job “{value}”: each launch message has one job.",
        "seq_missing_job": "No message has the job “{value}”: teaser earns permission, preview demonstrates, launch gives an immediate action.",
        "seq_teaser_sells": "The teaser's button “{value}” sells. A teaser earns permission to follow up; it does not ask for the order.",
        "seq_launch_no_action": "The launch message lacks {field}: it must give an immediate action with the price and the link.",
        "seq_order": "Send offsets are out of order: teaser before preview before launch.",
        "seq_same_cta": "Two messages open their button with “{value}”: each message asks for a different action.",
        "seq_number_unsourced": "Message {n}: the number “{value}” has no source in claims.",
        "seq_inquiry_uncovered": "{n} lead(s) asked “{value}” and no message answers it.",
        "seq_inquiry_unknown": "Message {n} answers “{value}”, which no lead asked.",
        "seq_inquiry_ignored": "Message {n} never answers the question it is for (“{value}”).",
        "seq_inquiry_late": "Message {n} answers “{value}” only after the second sentence. Answer first, then add reasons to buy.",
        "outcome_metric_missing": "A brand campaign with no outcomes: say how recognition and conversation will each be measured.",
        "outcome_metric_no_fame": "Outcomes measure awareness but not fame: add a metric for whether people talked about or shared the campaign.",
        "creative_shows_missing": "{n} ad(s) do not say what the visual shows (shows.item, price_eur), so the range cannot be checked.",
        "creative_unrepresentative": "Every visual shows an item at €{n} or more while the typical item is €{limit}: show the typical range in at least one ad.",
        "creative_confounded": "This offer expresses {value}. Add an ad that tests one of them alone before attributing the lift to price.",
        "archive_result_without_copy": "A result is recorded but the ad text is empty: a figure without the creative teaches the next team nothing.",
        "archive_spend_unsourced": "Spend of €{value} has no source file or URL.",
    },
    "lt": {
        "wom_missing": "Rekomendacijų reklamoms reikia grupės, kurioje šie pirkėjai jau kalbasi: pridėkite wom (group, venue_url, shared_standard, members).",
        "wom_venue_unknown": "Bendruomenės vieta „{value}“ nėra faktų faile: parsisiųskite ją, kad grupė būtų tikra.",
        "wom_unsized": "Bendruomenė „{value}“ neturi dydžio: nurodykite members (narių skaičių ar įrašų per savaitę) iš vietos puslapio.",
        "wom_share_not_optional": "„{value}“ rodymas nėra pasirenkamas. Matomumas turi būti vartotojo sprendimas, kitaip pokalbių nebus.",
        "wom_share_no_metric": "„{value}“ neturi pokalbių rodiklio ir vieneto (pasidalijimai vienam aktyviam vartotojui, atsakymai, rekomendacijų registracijos).",
        "wom_share_no_baseline": "„{value}“ neturi atskaitos taško: dvi savaites matuokite dabartinį rodiklį, tik tada žadėkite augimą.",
        "referral_missing": "Reklama prašo dalintis ar pakviesti, bet nepasako, ką gauna pats dalijantysis: pridėkite referral.payoff.",
        "referral_unverified": "Rekomenduotojo nauda yra hipotezė. Paklauskite rekomenduotojų: ką jums davė rekomendacija? Ar atrodėte išmanantis, ar padėjote artimam žmogui?",
        "retell_missing": "Rekomendacijų kampanijai reikia vieno patikrinamo kraštutinumo, kurį verta perpasakoti: pridėkite retell su mūsų ir bent dviejų konkurentų reikšmėmis.",
        "retell_thin": "Išskirtinumas lyginamas su {n} konkurentu (-ais); kad jį vadintume kraštutiniu, reikia bent dviejų.",
        "retell_not_extreme": "„{value}“ nėra kraštutinumas: konkurentas ne prastesnis. Tai pranašumas, o ne istorija, kurią kas nors perpasakos.",
        "retell_unsourced": "Konkurento „{value}“ reikšmė neturi šaltinio nuorodos faktų faile.",
        "retell_not_in_copy": "Nė viena antraštė ar tekstas neneša išskirtinumo „{value}“: bent viena reklama turėtų juo pradėti.",
        "pr_kind_missing": "Rekomenduotojas „{value}“: nurodykite, ar jis nepriklausomas, apmokėtas, partneris ar savas. Nežinoma niekada nereiškia nepriklausomo.",
        "pr_undisclosed": "„{value}“ buvo apmokėtas, bet reklamoje nėra žymos (#reklama, Remiama partnerystė, Paid partnership).",
        "pr_unverified": "„{value}“ pažymėtas kaip nepriklausomas, bet apžvalgos nuorodos nėra faktų faile.",
        "pr_proof_missing": "„{value}“ yra abstrakti savybė: pridėkite proof_action – veiksmą, kurį klientas galėtų nufotografuoti, ir kur jis matomas.",
        "pr_proof_unsourced": "Įrodymo veiksmas „{value}“: šaltinis nėra nuoroda faktų faile ar rinkodarininko pateiktas failas.",
        "backer_missing": "Teiginys „{value}“ naudojamas įrodymo teste, bet nenurodyta, kas už jo stovi (independent_review, industry_body, customer, self).",
        "backer_not_independent": "Visi įrodymai yra „{value}“: paties pardavėjo teiginys nėra rekomendacija, tad nėra nepriklausomos testo šakos.",
        "backer_test_confounded": "Įrodymo testas keičia ir {field}: kontrolinė ir nauja reklama turi skirtis tik įrodymo sakiniu.",
        "green_unmeasured": "„{value}“ yra tvarumo pažadas be nieko išmatuojamo. Pridėkite green (subject, baseline su data, change, source) arba pašalinkite žodį.",
        "green_unit_mismatch": "Tvarumo įrašas „{value}“: atskaitos taškas ir pokytis matuojami skirtingais vienetais.",
        "green_source_unknown": "Tvarumo įrašas „{value}“: šaltinio nuorodos nėra faktų faile.",
        "defect_missing": "Tekste yra nuolaida, bet nenurodyta prekių būklė: pridėkite defect (grade, flaw, price_reason) arba grade new.",
        "defect_undisclosed": "Būklė „{value}“, bet reklama nepasako, kas negerai ir kodėl kaina mažesnė. Įvardykite trūkumą ir kainos priežastį.",
        "defect_not_in_copy": "Trūkumo „{value}“ tekste nėra. Pasakykite jį tiesiai, kad pirkėjas pats įvertintų, ko liko.",
        "defect_minimised": "„{value}“ sumenkina žinomą trūkumą. Vietoj to aprašykite patį trūkumą.",
        "demo_missing": "Renginio planui reikia demo: demonstruojamas teiginys, lankytojo atliekamas veiksmas, pagal ką jis sprendžia ir ką parsineša.",
        "demo_no_act": "Demonstracija neturi veiksmo. Įvardykite, ką lankytojas daro ar mato, kad pats įvertintų teiginį.",
        "demo_passive_act": "„{value}“ tik rodo ar paaiškina. Lankytojai turi patys paragauti, palyginti, pakelti ar išmatuoti.",
        "demo_no_takeaway": "Nėra ką parsinešti: duokite lankytojams tai, kas leis pakartoti vertinimą namuose.",
        "demo_claim_unsourced": "Demonstruojamame teiginyje yra „{value}“ be šaltinio teiginių sąraše.",
        "event_missing": "Renginio planui reikia event.host: vietos atsakymo apie pirkėjų srautą ir bent vieno šeimininko rodiklio su atskaitos tašku.",
        "event_no_flow_check": "Niekas nepaklausė vietos, kaip renginio metu įprasti pirkėjai pasieks kasas. Įrašykite atsakymą pažodžiui į host.flow_check.",
        "event_no_host_kpi": "Planas matuoja tik mūsų pusę. Pridėkite bent vieną šeimininko rodiklį (kategorijos pardavimai, čekiai su mūsų preke).",
        "event_kpi_no_baseline": "Šeimininko rodiklis „{value}“ neturi atskaitos taško, tad šeimininkas nematys, ką renginys pakeitė.",
        "stockist_missing": "Pasiūlymas susietas su „{value}“, bet nėra kelio žmonėms už jo ribų: pridėkite stockist (catchment, stockists, out_of_area_route).",
        "stockist_route_missing": "Žmonės už „{value}“ ribų neturi kaip pasinaudoti: nurodykite artimiausią pardavėją ar kelią internetu.",
        "stockist_url_unknown": "Pardavėjas „{value}“: nuorodos nėra faktų faile.",
        "trade_situation_missing": "Kitos reklamos nurodo pirkimo situaciją, ši – ne. Ar ši auditorija užsako iš naujo, keičia specifikaciją ar perka ką nors nauja?",
        "trade_situation_mismatch": "Auditorija „{value}“ gauna kitai situacijai skirtą tekstą: naujiems pirkėjams reikia pagalbos, o ne pakartotinio užsakymo mygtuko; užsakantiems iš naujo – ne kategorijos pristatymo.",
        "trade_channel_missing": "Reklama mini prekybos tinklą ar platintoją, bet channel tuščias: nurodykite, kam priklauso kiekviena grandis ir ką ji sprendžia.",
        "trade_lever_not_controlled": "Reklama liečia {field}, kurio nesprendžia nė viena jūsų grandis. Reikia sutikimo: {value}.",
        "trade_intensity_missing": "Kanalas turi grandis, bet nenurodytas platinimo intensyvumas: intensive, selective ar exclusive, pagal tai, kaip pirkėjai perka.",
        "trade_intensity_mismatch": "Tekstas žada prekę visur, bet platinimas yra {value}: vietoj to įvardykite pardavimo vietas.",
        "trade_intensity_service": "Intensyvus platinimas, kai reikia daug aptarnavimo: pirkėjai, kuriems reikia demonstracijos ar serviso, jų negaus kiekvienoje vietoje.",
        "trade_entry_missing": "Auditorija įvardija įmonę, bet nėra entry_use_case: kuriam skyriui to reikia dabar ir kas ten gali pasirašyti?",
        "trade_entry_unsourced": "Įėjimo atvejis „{value}“: skubos įrodymo nuorodos nėra faktų faile.",
        "trade_display_incomplete": "Ekspozicijos sutartyje nenurodyta: {field}.",
        "trade_display_unverified": "Nė viena parduotuvė nepatikrinta: nevertinkite pasiūlymo, kol ekspozicija nepatvirtinta imties parduotuvėse.",
        "trade_display_audit_short": "Patikrinta tik {n} iš {limit} imties parduotuvių: pasiūlymo vertinti dar negalima.",
        "trade_display_execution_low": "Ekspozicija stovėjo {n} iš {limit} patikrintų parduotuvių. Žemiau 80 % rezultatas nieko nesako apie pasiūlymą.",
        "trade_cobrand_cta_mismatch": "Nustatyta partner_cta, bet reklamos veda į „{value}“, o ne į partnerio svetainę.",
        "trade_cobrand_claims_unowned": "Teiginys „{value}“ neturi prekės ženklo savininko (host ar endorser) co_brand.claim_owners sąraše.",
        "trade_cobrand_brand_repeated": "„{value}“ antraštėje kartojasi: kiekvienas prekės ženklas po vieną kartą.",
        "trade_chain_missing": "Prekė pirkėją pasiekia per prekybos ar specialistų grandinę, bet chain tuščias: kas parenka, rekomenduoja, laiko ir perka?",
        "trade_chain_upstream_missed": "„{value}“ parenka ar rekomenduoja, bet nė viena reklama nėra skirta jiems.",
        "trade_chain_no_buyer": "Sprendimų grandinėje nėra nė vieno, kuris perka.",
        "trade_chain_source_unknown": "Grandinės vaidmuo „{value}“: šaltinis nėra nuoroda faktų faile ar interview:<vardas> pastaba.",
        "trade_chain_shares_exceed": "Sprendimų dalys sudaro {n} %: jos negali viršyti 110.",
        "pack_missing": "Prekė parduodama lentynoje, bet pack tuščias: kokių spalvų, formų ar etiketės detalių pirkėjai ieškos?",
        "pack_not_carried": "Vizualas nerodo nė vieno pakuotės skiriamojo ženklo: reklamą matęs pirkėjas pakuotės lentynoje neras.",
        "pack_not_distinct": "Visi skiriamieji ženklai sutampa su konkurentų ({value}): išryškinkite ženklą, kurį turi tik ši pakuotė.",
        "pack_cue_unknown": "„{value}“ nurodytas kaip rodomas vizuale, bet nėra tarp pakuotės skiriamųjų ženklų.",
        "activation_no_offer": "Šie pirkėjai renkasi dabar, bet reklamose nėra konkretaus pasiūlymo: pridėkite activation.offer.",
        "activation_weak_step": "„{value}“ nėra lengvas kitas žingsnis besirenkančiam pirkėjui: siūlykite laiką, auditą, pavyzdį.",
        "activation_no_metric": "Neapibrėžtas kvalifikuotas atsakas: pasakykite, kas ir ką turi padaryti, kad atsakas būtų įskaitytas. Paspaudimai nesiskaito.",
        "placement_adjacency_unchecked": "Nurodyta vieta „{value}“, bet aplinkinis turinys neperžiūrėtas: nufotografuokite puslapį ir užrašykite, kas supa reklamą.",
        "placement_adjacency_note_missing": "Vietos „{value}“ aplinka pažymėta kaip patikrinta, bet nėra pastabos, kas supa reklamą.",
        "placement_tracking_missing": "Sekimui trūksta {field}: kiekvienai reklamai ir vietai reikia savo pažymėtos nuorodos.",
        "placement_tracking_dup": "utm_content „{value}“ naudojamas daugiau nei vienai reklamai ar vietai: vėliau užklausų nebus galima atsekti.",
        "dwell_unknown": "Vieta „{value}“ nėra žiūrėjimo laiko lentelėje ir neturi dwell_seconds: nurodykite, kiek laiko turi žiūrovas.",
        "dwell_too_long": "{Field}: {n} žodžiai ten, kur žiūrovas turi {limit} s ({value}). Trumpinkite tekstą.",
        "seq_missing": "Sekos pakete nėra žinučių.",
        "seq_dup_job": "Dvi žinutės turi tą pačią užduotį „{value}“: kiekviena paleidimo žinutė turi vieną užduotį.",
        "seq_missing_job": "Nė viena žinutė neturi užduoties „{value}“: anonsas gauna leidimą tęsti, peržiūra parodo, paleidimas duoda veiksmą iš karto.",
        "seq_teaser_sells": "Anonso mygtukas „{value}“ parduoda. Anonsas pelno leidimą tęsti, o ne prašo užsakymo.",
        "seq_launch_no_action": "Paleidimo žinutei trūksta {field}: ji turi duoti veiksmą iš karto su kaina ir nuoroda.",
        "seq_order": "Siuntimo laikai sumaišyti: anonsas prieš peržiūrą, peržiūra prieš paleidimą.",
        "seq_same_cta": "Dviejų žinučių mygtukas prasideda „{value}“: kiekviena žinutė prašo kitokio veiksmo.",
        "seq_number_unsourced": "Žinutė {n}: skaičius „{value}“ neturi šaltinio teiginių sąraše.",
        "seq_inquiry_uncovered": "{n} užklausa (-os) „{value}“ neturi jokios atsakančios žinutės.",
        "seq_inquiry_unknown": "Žinutė {n} atsako į „{value}“, ko nė vienas klientas neklausė.",
        "seq_inquiry_ignored": "Žinutė {n} taip ir neatsako į klausimą, kuriam skirta („{value}“).",
        "seq_inquiry_late": "Žinutė {n} į „{value}“ atsako tik po antro sakinio. Pirma atsakykite, tik tada pridėkite priežastis pirkti.",
        "outcome_metric_missing": "Prekės ženklo kampanija be outcomes: pasakykite, kaip atskirai bus matuojamas atpažinimas ir kalbėjimas.",
        "outcome_metric_no_fame": "Rodikliai matuoja žinomumą, bet ne kalbėjimą: pridėkite rodiklį, ar žmonės kalbėjo apie kampaniją ar ja dalijosi.",
        "creative_shows_missing": "{n} reklama (-os) nenurodo, ką rodo vizualas (shows.item, price_eur), tad asortimento patikrinti negalima.",
        "creative_unrepresentative": "Kiekvienas vizualas rodo prekę už {n} € ar daugiau, o įprasta prekė kainuoja {limit} €: bent vienoje reklamoje parodykite įprastą asortimentą.",
        "creative_confounded": "Šis pasiūlymas išreiškia {value}. Pridėkite reklamą, kuri tikrina vieną iš jų atskirai, prieš priskirdami poveikį kainai.",
        "archive_result_without_copy": "Rezultatas įrašytas, bet reklamos tekstas tuščias: skaičius be kūrybos kitai komandai nieko neišmoko.",
        "archive_spend_unsourced": "{value} € išlaidos neturi šaltinio failo ar nuorodos.",
    },
}


# --- small readers: the worker's input as sent, never guessed ---------------------------------------------


def _obj(value: Any) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return list(value) if isinstance(value, list) else []


def _dicts(value: Any) -> list[dict]:
    return [v for v in _list(value) if isinstance(v, dict)]


def _s(value: Any, limit: int = 500) -> str:
    return str(value).strip()[:limit] if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", ".").replace(" ", ""))
        except ValueError:
            return None
    return None


def _flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _s(value).lower() in {"true", "yes", "taip", "1", "y"}


def _enum(value: Any, allowed: tuple[str, ...]) -> str:
    text = _s(value).lower().replace("-", "_").replace(" ", "_")
    return text if text in allowed else ""


def _words(text: str) -> int:
    return len(_WORDS.findall(text or ""))


def _copy(ad: dict) -> str:
    return " ".join(_s(ad.get(name), 3000) for name in ("primary_text", "headline", "description"))


def _host(url: str) -> str:
    return re.sub(r"^www\.", "", urlparse(url).netloc.lower())


def _known(facts: str | None) -> str | None:
    return _bare_url(facts) if facts is not None else None


def _in_facts(url: str, known: str | None) -> bool:
    return _is_url(url) and (known is None or _bare_url(url) in known)


def _file_or_fact(source: str, facts: str | None, known: str | None) -> bool:
    """A URL the research saw, a string the facts file carries, or a file path the marketer supplied."""
    if not source:
        return False
    if _is_url(source):
        return _in_facts(source, known)
    return bool(facts and source in facts) or bool(re.fullmatch(r"[\w./ -]+\.\w{2,5}", source))


def _sourced_numbers(pack: dict, known: str | None) -> set[str]:
    out: set[str] = set()
    for claim in pack.get("claims") or []:
        if _in_facts(_s(claim.get("source")), known):
            out.update(_NUMBER.findall(_s(claim.get("text"))))
    return out


def _referral_cue(ad: dict) -> bool:
    return bool(_REFERRAL.search(_s(ad.get("cta")) + " " + _s(ad.get("angle"))))


def _place(ad: dict) -> str:
    return _s(_obj(ad.get("mix")).get("place"))


def _ads(pack: dict) -> list[dict]:
    return [ad for ad in pack.get("ads") or [] if isinstance(ad, dict)]


def _shares_a_word(words: list[str], text: str) -> bool:
    return bool(shared_words(words, text))


# --- word of mouth: #256 #262 #265 #266 --------------------------------------------------------------------


def _check_wom(pack: dict, known: str | None, out: list[Check]) -> None:
    cue = any(_referral_cue(ad) for ad in _ads(pack))
    wom = _obj(pack.get("wom"))
    if cue and not wom:
        out.append(Check("warn", "wom_missing"))
    if wom:
        group = _s(wom.get("group")) or _s(wom.get("venue_url"))
        if not _in_facts(_s(wom.get("venue_url")), known):
            out.append(Check("warn", "wom_venue_unknown", value=_s(wom.get("venue_url")) or group))
        if not (_num(wom.get("members")) or 0) > 0:
            out.append(Check("warn", "wom_unsized", value=group))
    for item in _dicts(pack.get("visibility")):
        what = _s(item.get("what"))
        if not _flag(item.get("opt_in")):
            out.append(Check("warn", "wom_share_not_optional", value=what))
        if not _s(item.get("metric")) or not _s(item.get("unit")):
            out.append(Check("warn", "wom_share_no_metric", value=what))
        if _num(item.get("baseline")) is None:
            out.append(Check("warn", "wom_share_no_baseline", value=what))


def _check_referral(pack: dict, out: list[Check]) -> None:
    for n, ad in enumerate(_ads(pack), 1):
        referral = _obj(ad.get("referral"))
        if _referral_cue(ad) and not _s(referral.get("payoff")):
            out.append(Check("warn", "referral_missing", ad=n))
        elif referral and _s(referral.get("payoff")):
            source = _s(referral.get("source"))
            if not _is_url(source):
                out.append(Check("warn", "referral_unverified", ad=n, value=source or "hypothesis"))


def _check_retell(pack: dict, known: str | None, out: list[Check]) -> None:
    retell = _obj(pack.get("retell"))
    cue = any(_referral_cue(ad) for ad in _ads(pack))
    if not retell:
        if cue:
            out.append(Check("warn", "retell_missing"))
        return
    claim = _s(retell.get("claim")) or _s(retell.get("metric"))
    competitors = _dicts(retell.get("competitors"))
    if len(competitors) < 2:
        out.append(Check("warn", "retell_thin", n=len(competitors)))
    ours = _num(retell.get("our_value"))
    values = [v for v in (_num(c.get("value")) for c in competitors) if v is not None]
    if ours is not None and values:
        lower = _s(retell.get("direction")).lower() == "lower_better"
        best = min(values) if lower else max(values)
        if (ours >= best) if lower else (ours <= best):
            shown = ", ".join(f"{v:g}" for v in values)
            out.append(Check("warn", "retell_not_extreme", value=f"{claim}: {ours:g} vs {shown}"))
    for competitor in competitors:
        if not _in_facts(_s(competitor.get("source")), known):
            out.append(Check("warn", "retell_unsourced", value=_s(competitor.get("name"))))
    words = content_words(f"{_s(retell.get('metric'))} {claim}")
    if words and not any(
        _shares_a_word(words, _s(ad.get("headline")) + " " + _s(ad.get("primary_text"), 3000)) for ad in _ads(pack)
    ):
        out.append(Check("warn", "retell_not_in_copy", value=claim))


# --- PR and trust: #257 #287 #307 #308 #310 ----------------------------------------------------------------


def _check_endorsements(pack: dict, known: str | None, out: list[Check]) -> None:
    for n, ad in enumerate(_ads(pack), 1):
        for voice in _dicts(ad.get("endorsements")):
            who = _s(voice.get("who"))
            kind = _enum(voice.get("kind"), ENDORSER_KINDS)
            if not kind:
                out.append(Check("warn", "pr_kind_missing", ad=n, value=who))
            elif kind in ("paid", "affiliate"):
                if not _DISCLOSURE.search(_copy(ad) + " " + _s(voice.get("disclosure_text"))):
                    out.append(Check("warn", "pr_undisclosed", ad=n, value=who))
            elif kind == "independent" and not _in_facts(_s(voice.get("source")), known):
                out.append(Check("warn", "pr_unverified", ad=n, value=who))


def _check_proof_actions(pack: dict, facts: str | None, known: str | None, out: list[Check]) -> None:
    actions = _dicts(pack.get("proof_actions"))
    for n, ad in enumerate(_ads(pack), 1):
        hit = _VIRTUE.search(_copy(ad))
        if hit and not actions:
            out.append(Check("warn", "pr_proof_missing", ad=n, value=hit.group(0)))
    for action in actions:
        if not _file_or_fact(_s(action.get("source")), facts, known):
            out.append(Check("warn", "pr_proof_unsourced", value=_s(action.get("what")) or _s(action.get("claim"))))


def _check_backer(pack: dict, out: list[Check]) -> None:
    ads = _ads(pack)
    testers = [n for n, ad in enumerate(ads, 1) if _flag(ad.get("proof_test"))]
    if not testers:
        return
    backers = {_s(row.get("claim")).casefold(): _enum(row.get("backer"), BACKERS) for row in _dicts(pack.get("backer"))}
    claims = [_s(claim.get("text")) for claim in pack.get("claims") or []]
    named: list[str] = []
    for text in claims:
        backer = backers.get(text.casefold())
        if not backer:
            out.append(Check("warn", "backer_missing", value=text))
        else:
            named.append(backer)
    if named and all(b in ("self", "none") for b in named):
        out.append(Check("warn", "backer_not_independent", value=", ".join(sorted(set(named)))))
    controls = [ad for ad in ads if ad.get("test_role") == "control"]
    if len(controls) != 1:
        return
    control = controls[0]
    for n, ad in enumerate(ads, 1):
        if ad.get("test_role") != "challenger":
            continue
        for name in ("primary_text", "headline", "description"):
            if _strip_claims(_s(control.get(name), 3000), claims) != _strip_claims(_s(ad.get(name), 3000), claims):
                out.append(Check("warn", "backer_test_confounded", ad=n, field=name))


def _strip_claims(text: str, claims: list[str]) -> str:
    for claim in claims:
        if claim:
            text = re.sub(re.escape(claim), " ", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip().casefold()


def _check_green(pack: dict, known: str | None, out: list[Check]) -> None:
    entries = _dicts(pack.get("green"))
    complete = False
    for entry in entries:
        subject = _s(entry.get("subject"))
        baseline, change = _obj(entry.get("baseline")), _obj(entry.get("change"))
        measured = (
            bool(subject)
            and _num(baseline.get("value")) is not None and bool(_s(baseline.get("unit"))) and bool(_s(baseline.get("date")))
            and _num(change.get("value")) is not None and bool(_s(change.get("unit")))
            and _is_url(_s(entry.get("source")))
        )
        if measured and not _in_facts(_s(entry.get("source")), known):
            out.append(Check("warn", "green_source_unknown", value=subject))
        if measured and _s(baseline.get("unit")).casefold() != _s(change.get("unit")).casefold():
            out.append(Check("warn", "green_unit_mismatch", value=subject))
        complete = complete or measured
    for n, ad in enumerate(_ads(pack), 1):
        for name in ("primary_text", "headline", "description"):
            hit = _GREEN.search(_s(ad.get(name), 3000))
            if hit and not complete:
                out.append(Check("block", "green_unmeasured", ad=n, field=name, value=hit.group(0)))
                break


def _check_defect(pack: dict, out: list[Check]) -> None:
    for n, ad in enumerate(_ads(pack), 1):
        defect = _obj(ad.get("defect"))
        copy = _copy(ad)
        if not defect:
            if _DISCOUNT.search(copy):
                out.append(Check("warn", "defect_missing", ad=n))
            continue
        grade = _enum(defect.get("grade"), GRADES) or "unknown"
        if grade == "new":
            continue
        flaw, reason = _s(defect.get("flaw")), _s(defect.get("price_reason"))
        if not flaw or not reason:
            out.append(Check("block", "defect_undisclosed", ad=n, value=grade))
        words = content_words(flaw)
        if words and not _shares_a_word(words, copy):
            out.append(Check("block", "defect_not_in_copy", ad=n, value=flaw))
        soft = _MINIMISED.search(copy)
        if soft and grade in ("seconds", "returned", "damaged"):
            out.append(Check("warn", "defect_minimised", ad=n, value=soft.group(0)))


# --- events: #311 #312 -------------------------------------------------------------------------------------


def _check_event(pack: dict, known: str | None, out: list[Check]) -> None:
    kind = _s(pack.get("kind")).lower()
    demo = _obj(pack.get("demo"))
    event = _obj(pack.get("event"))
    if kind == "event_plan" and not demo:
        out.append(Check("warn", "demo_missing"))
    if demo:
        act = _s(demo.get("act"))
        if not act:
            out.append(Check("warn", "demo_no_act"))
        elif _PASSIVE_ACT.search(act) and not _ACTIVE_ACT.search(act):
            out.append(Check("warn", "demo_passive_act", value=act[:80]))
        if not _s(demo.get("takeaway")):
            out.append(Check("warn", "demo_no_takeaway"))
        sourced = _sourced_numbers(pack, known)
        for number in dict.fromkeys(_NUMBER.findall(_s(demo.get("claim")))):
            if number not in sourced:
                out.append(Check("warn", "demo_claim_unsourced", value=number))
    host = _obj(event.get("host"))
    if kind == "event_plan" and not host:
        out.append(Check("warn", "event_missing"))
    if host:
        if not _s(host.get("flow_check")):
            out.append(Check("warn", "event_no_flow_check"))
        kpis = _dicts(host.get("host_kpis"))
        if not kpis:
            out.append(Check("warn", "event_no_host_kpi"))
        for kpi in kpis:
            if _num(kpi.get("baseline")) is None:
                out.append(Check("warn", "event_kpi_no_baseline", value=_s(kpi.get("metric"))))


# --- sales, retail and distribution: #356 #361 #362 #368 #378 #380 #381 #385 #386 ---------------------------


def _check_stockist(pack: dict, known: str | None, out: list[Check]) -> None:
    stockist = _obj(pack.get("stockist"))
    catchment = _s(stockist.get("catchment"))
    local = catchment
    for ad in _ads(pack):
        hit = _CITY.search(_place(ad))
        if hit and not local:
            local = hit.group(0)
    if not local:
        return
    if not stockist:
        out.append(Check("warn", "stockist_missing", value=local))
        return
    if not _s(stockist.get("out_of_area_route")):
        out.append(Check("warn", "stockist_route_missing", value=local))
    for row in _dicts(stockist.get("stockists")):
        url = _s(row.get("url"))
        if url and not _in_facts(url, known):
            out.append(Check("warn", "stockist_url_unknown", value=_s(row.get("name")) or url))


def _check_buying_situation(pack: dict, out: list[Check]) -> None:
    ads = _ads(pack)
    situations = [_enum(ad.get("buying_situation"), SITUATIONS) for ad in ads]
    for n, (ad, situation) in enumerate(zip(ads, situations), 1):
        text = _copy(ad) + " " + _s(ad.get("cta"))
        if not situation:
            if any(situations):
                out.append(Check("warn", "trade_situation_missing", ad=n))
            continue
        if situation == "new_task" and _REORDER.search(text):
            out.append(Check("warn", "trade_situation_mismatch", ad=n, value=situation))
        elif situation == "straight_rebuy" and _EDUCATION.search(text):
            out.append(Check("warn", "trade_situation_mismatch", ad=n, value=situation))


def _check_channel(pack: dict, out: list[Check]) -> None:
    channel = _obj(pack.get("channel"))
    ads = _ads(pack)
    if not channel:
        if any(_RETAIL.search(_place(ad)) for ad in ads):
            out.append(Check("warn", "trade_channel_missing"))
        return
    brand = _s(pack.get("brand")).casefold()
    stages = _dicts(channel.get("stages"))
    intensity = _enum(channel.get("intensity"), INTENSITIES)
    if stages and not intensity:
        out.append(Check("warn", "trade_intensity_missing"))
    if intensity == "intensive" and _s(channel.get("service_need")).lower() == "high":
        out.append(Check("warn", "trade_intensity_service"))
    ours: set[str] = set()
    others: dict[str, list[str]] = {lever: [] for lever in LEVERS}
    for stage in stages:
        owner = _s(stage.get("owner"))
        mine = owner.casefold() in _MARKETER or (brand and owner.casefold() == brand) or _enum(stage.get("stage"), STAGES) == "direct"
        for lever in _list(stage.get("decides")):
            lever = _enum(lever, LEVERS)
            if not lever:
                continue
            if mine:
                ours.add(lever)
            elif owner:
                others[lever].append(owner)
    for n, ad in enumerate(ads, 1):
        copy = _copy(ad)
        if intensity in ("selective", "exclusive") and _BREADTH.search(copy):
            out.append(Check("warn", "trade_intensity_mismatch", ad=n, value=intensity))
        if not stages:
            continue
        touched = []
        if _PRICE_WORDS.search(copy) or _NUMBER.search(copy):
            touched.append("price")
        if _AVAILABILITY.search(copy) or _RETAIL.search(copy):
            touched.append("availability")
        if _DISCOUNT.search(copy) or _PROMO.search(copy):
            touched.append("promo")
        for lever in touched:
            if lever not in ours:
                out.append(Check("warn", "trade_lever_not_controlled", ad=n, field=lever, value=", ".join(dict.fromkeys(others[lever])) or "?"))


def _check_entry(pack: dict, known: str | None, out: list[Check]) -> None:
    for n, ad in enumerate(_ads(pack), 1):
        entry = _obj(ad.get("entry_use_case"))
        if not entry:
            if _COMPANY.search(_s(ad.get("audience"))):
                out.append(Check("warn", "trade_entry_missing", ad=n))
            continue
        if not _in_facts(_s(entry.get("evidence_url")), known):
            out.append(Check("warn", "trade_entry_unsourced", ad=n, value=_s(entry.get("department"))))


def _check_display(pack: dict, out: list[Check]) -> None:
    display = _obj(pack.get("display"))
    if not display:
        return
    verification = _obj(display.get("verification"))
    required = {
        "retailer": _s(display.get("retailer")), "stores": (_num(display.get("stores")) or 0) > 0, "start": _s(display.get("start")),
        "end": _s(display.get("end")), "location": _s(display.get("location")), "signage": bool(_list(display.get("signage"))),
        "verification.method": _s(verification.get("method")), "verification.sample_stores": (_num(verification.get("sample_stores")) or 0) > 0,
    }
    for name, present in required.items():
        if not present:
            out.append(Check("warn", "trade_display_incomplete", field=name))
    audit = _dicts(display.get("audit"))
    if not audit:
        out.append(Check("warn", "trade_display_unverified"))
        return
    sample = int(_num(verification.get("sample_stores")) or 0)
    present_count = sum(1 for row in audit if _flag(row.get("present")))
    if sample and len(audit) < sample:
        out.append(Check("warn", "trade_display_audit_short", n=len(audit), limit=sample))
    elif present_count / len(audit) < MIN_EXECUTION:
        out.append(Check("warn", "trade_display_execution_low", n=present_count, limit=len(audit)))


def _check_co_brand(pack: dict, out: list[Check]) -> None:
    co = _obj(pack.get("co_brand"))
    if not co:
        return
    if _flag(co.get("partner_cta")):
        partner_host = _host(_s(co.get("partner_url")))
        ours = _host(_s(pack.get("url")))
        if not partner_host or partner_host != ours:
            out.append(Check("warn", "trade_cobrand_cta_mismatch", value=ours or "?"))
    owners = {_s(row.get("claim")).casefold(): _s(row.get("brand")) for row in _dicts(co.get("claim_owners"))}
    names = {_s(co.get("host_brand")).casefold(), _s(co.get("endorser_brand")).casefold()} - {""}
    for claim in pack.get("claims") or []:
        text = _s(claim.get("text"))
        owner = owners.get(text.casefold(), "").casefold()
        if owner not in {"host", "endorser"} and owner not in names:
            out.append(Check("warn", "trade_cobrand_claims_unowned", value=text))
    for n, ad in enumerate(_ads(pack), 1):
        headline = _s(ad.get("headline")).casefold()
        for name in names:
            if headline.count(name) > 1:
                out.append(Check("warn", "trade_cobrand_brand_repeated", ad=n, value=name))


def _check_pack_cues(pack: dict, out: list[Check]) -> None:
    cues = _obj(pack.get("pack"))
    ads = _ads(pack)
    retail_place = any(_RETAIL.search(_place(ad)) for ad in ads)
    if not cues:
        if retail_place:
            out.append(Check("warn", "pack_missing"))
        return
    assets = [_s(a.get("cue")).casefold() for a in _dicts(cues.get("distinctive_assets")) if _s(a.get("cue"))]
    shown = [_s(c).casefold() for c in _list(cues.get("shown_in_creative")) if _s(c)]
    overlap = {_s(o.get("cue")).casefold(): _s(o.get("competitor")) for o in _dicts(cues.get("competitor_overlap"))}
    for cue in shown:
        if cue not in assets:
            out.append(Check("warn", "pack_cue_unknown", value=cue))
    if (_flag(cues.get("sold_in_retail")) or retail_place) and not any(cue in assets for cue in shown):
        out.append(Check("warn", "pack_not_carried"))
    if assets and all(cue in overlap for cue in assets):
        out.append(Check("warn", "pack_not_distinct", value=", ".join(dict.fromkeys(overlap[c] for c in assets if overlap[c]))))


def _check_chain(pack: dict, known: str | None, out: list[Check]) -> None:
    chain = _dicts(pack.get("chain"))
    ads = _ads(pack)
    if not chain:
        if any(_TRADE.search(_place(ad)) for ad in ads):
            out.append(Check("warn", "trade_chain_missing"))
        return
    audiences = " ".join(_s(ad.get("audience")) + " " + _s(ad.get("angle")) for ad in ads)
    total = 0.0
    buys = False
    for row in chain:
        role, influence = _s(row.get("role")), _enum(row.get("influence"), INFLUENCES)
        buys = buys or influence == "buys"
        total += _num(row.get("share_pct")) or 0
        if influence in ("sets", "recommends"):
            words = content_words(role) or [w.casefold() for w in _WORDS.findall(role)]
            if words and not _shares_a_word(words, audiences):
                out.append(Check("warn", "trade_chain_upstream_missed", value=role))
        source = _s(row.get("source"))
        if not (source.lower().startswith("interview:") or _in_facts(source, known)):
            out.append(Check("warn", "trade_chain_source_unknown", value=role))
    if not buys:
        out.append(Check("warn", "trade_chain_no_buyer"))
    if total > 110:
        out.append(Check("warn", "trade_chain_shares_exceed", n=int(total)))


# --- media: #412 #416 #417 #458 ----------------------------------------------------------------------------


def _check_activation(pack: dict, out: list[Check]) -> None:
    activation = _obj(pack.get("activation"))
    if not activation or _s(activation.get("stage")).lower() != "in_market":
        return
    if not _s(activation.get("offer")):
        out.append(Check("warn", "activation_no_offer"))
    if not _s(activation.get("next_step")):
        out.append(Check("warn", "activation_weak_step", value="—"))
    for n, ad in enumerate(_ads(pack), 1):
        cta = _s(ad.get("cta"))
        if cta.casefold().strip(" .!") in _GENERIC_CTA:
            out.append(Check("warn", "activation_weak_step", ad=n, value=cta))
    if _words(_s(activation.get("qualified_response"))) < 3:
        out.append(Check("warn", "activation_no_metric"))


def _check_placement(pack: dict, out: list[Check]) -> None:
    contents: dict[str, list[int]] = {}
    for n, ad in enumerate(_ads(pack), 1):
        placement = _obj(ad.get("placement"))
        name = _s(placement.get("name")).lower().replace(" ", "_").replace("-", "_")
        if placement:
            label = name or _s(placement.get("surface")) or _s(placement.get("url"))
            if (_s(placement.get("url")) or _s(placement.get("surface"))) and not _flag(placement.get("adjacency_checked")):
                out.append(Check("warn", "placement_adjacency_unchecked", ad=n, value=label))
            elif _flag(placement.get("adjacency_checked")) and not _s(placement.get("adjacency_note")):
                out.append(Check("warn", "placement_adjacency_note_missing", ad=n, value=label))
            _check_dwell(ad, n, name, placement, out)
        tracking = _obj(ad.get("tracking"))
        if tracking:
            if not _is_url(_s(tracking.get("landing_url"))):
                out.append(Check("warn", "placement_tracking_missing", ad=n, field="landing_url"))
            if not _s(tracking.get("campaign")):
                out.append(Check("warn", "placement_tracking_missing", ad=n, field="campaign"))
            for slot in _list(tracking.get("placements")):
                slot = slot if isinstance(slot, dict) else {"name": slot}
                slot_name = re.sub(r"\W+", "_", _s(slot.get("name")).lower()).strip("_")
                content = _s(slot.get("utm_content")) or f"ad{n}_{slot_name}"
                contents.setdefault(content, []).append(n)
    for content, owners in contents.items():
        if len(owners) > 1:
            out.append(Check("warn", "placement_tracking_dup", ad=owners[-1], value=content))


def _check_dwell(ad: dict, n: int, name: str, placement: dict, out: list[Check]) -> None:
    custom = _num(placement.get("dwell_seconds"))
    if name and name not in DWELL_SECONDS and custom is None:
        out.append(Check("warn", "dwell_unknown", ad=n, value=name))
        return
    dwell = custom if custom is not None else DWELL_SECONDS.get(name)
    if not dwell or dwell <= 0:
        return
    text = _s(ad.get("headline")) + " " + _s(ad.get("primary_text"), 3000)
    words = _words(text)
    rate = GLANCE_RATE if dwell <= 5 else SUSTAINED_RATE
    if detect_language(text) == "lt":
        rate *= LT_FACTOR
    if words / rate > dwell:
        out.append(Check("warn", "dwell_too_long", ad=n, field="primary_text", value=name or "custom", n=words, limit=int(dwell)))


# --- sequences: #424 #379 ----------------------------------------------------------------------------------


def _check_sequence(pack: dict, known: str | None, out: list[Check]) -> None:
    kind = _s(pack.get("kind")).lower()
    messages = _dicts(pack.get("sequence"))
    if kind == "sequence" and not messages:
        out.append(Check("warn", "seq_missing"))
        return
    if not messages:
        return
    sourced = _sourced_numbers(pack, known)
    for i, msg in enumerate(messages, 1):
        text = " ".join(_s(msg.get(k), 5000) for k in ("subject", "body", "cta"))
        for number in dict.fromkeys(_NUMBER.findall(text)):
            if number not in sourced:
                out.append(Check("warn", "seq_number_unsourced", n=i, value=number))
    jobs = [_enum(m.get("job"), JOBS) for m in messages]
    inquiries = _dicts(pack.get("inquiries"))
    follow_ups = bool(inquiries) or any(_s(m.get("inquiry")) for m in messages)
    if any(jobs) or (kind == "sequence" and not follow_ups):
        _check_jobs(messages, jobs, out)
    if follow_ups:
        _check_inquiries(messages, inquiries, out)


def _check_jobs(messages: list[dict], jobs: list[str], out: list[Check]) -> None:
    for job in JOBS:
        if jobs.count(job) > 1:
            out.append(Check("warn", "seq_dup_job", value=job))
        elif job not in jobs:
            out.append(Check("warn", "seq_missing_job", value=job))
    verbs: dict[str, int] = {}
    offsets: dict[str, float] = {}
    for i, (msg, job) in enumerate(zip(messages, jobs), 1):
        cta = _s(msg.get("cta"))
        verb = (_WORDS.findall(cta.casefold()) or [""])[0]
        if verb:
            if verb in verbs:
                out.append(Check("warn", "seq_same_cta", n=i, value=verb))
            verbs.setdefault(verb, i)
        offset = _num(msg.get("send_offset_days"))
        if job and offset is not None and job not in offsets:
            offsets[job] = offset
        if job == "teaser" and _BUY.search(cta):
            out.append(Check("warn", "seq_teaser_sells", n=i, value=cta))
        if job == "launch":
            if not _is_url(_s(msg.get("cta_url"))):
                out.append(Check("warn", "seq_launch_no_action", n=i, field="cta_url"))
            if not re.search(r"\d\s?(€|eur)|€\s?\d|\$\s?\d", _s(msg.get("body"), 5000) + " " + cta, re.I):
                out.append(Check("warn", "seq_launch_no_action", n=i, field="price"))
    if len(offsets) == 3 and not (offsets["teaser"] < offsets["preview"] < offsets["launch"]):
        out.append(Check("warn", "seq_order"))


def _check_inquiries(messages: list[dict], inquiries: list[dict], out: list[Check]) -> None:
    asked = [(_s(q.get("text")), int(_num(q.get("count")) or 0)) for q in inquiries if _s(q.get("text"))]
    answered: set[str] = set()
    for i, msg in enumerate(messages, 1):
        inquiry = _s(msg.get("inquiry"))
        if not inquiry:
            continue
        match = next((text for text, _ in asked if _same_inquiry(text, inquiry)), None)
        if asked and match is None:
            out.append(Check("warn", "seq_inquiry_unknown", n=i, value=inquiry))
        if match is not None:
            answered.add(match)
        words = content_words(inquiry)
        body = _s(msg.get("body"), 5000)
        if not words:
            continue
        if not _shares_a_word(words, body):
            out.append(Check("warn", "seq_inquiry_ignored", n=i, value=inquiry))
        elif not _shares_a_word(words, " ".join(_SENTENCE.split(body)[:2])):
            out.append(Check("warn", "seq_inquiry_late", n=i, value=inquiry))
    for text, count in asked:
        if count > 0 and text not in answered:
            out.append(Check("warn", "seq_inquiry_uncovered", n=count, value=text))


def _same_inquiry(a: str, b: str) -> bool:
    if a.casefold().strip() == b.casefold().strip():
        return True
    words = content_words(a)
    return bool(words) and len(shared_words(words, b)) >= max(1, len(words) // 2)


# --- measurement and the creative: #439 #387 #491 #462 ------------------------------------------------------


def _check_outcomes(pack: dict, out: list[Check]) -> None:
    outcomes = _dicts(pack.get("outcomes"))
    ads = _ads(pack)
    if not outcomes:
        if any(_BRAND_LED.search(_s(ad.get("angle")) + " " + _s(ad.get("audience"))) for ad in ads):
            out.append(Check("warn", "outcome_metric_missing"))
        return
    kinds = {_enum(o.get("kind"), OUTCOME_KINDS) for o in outcomes}
    if "awareness" in kinds and "fame" not in kinds:
        out.append(Check("warn", "outcome_metric_no_fame"))


def _check_creative(pack: dict, out: list[Check]) -> None:
    ads = _ads(pack)
    price_range = _obj(pack.get("range"))
    typical = _num(price_range.get("typical_price_eur"))
    if typical:
        shows = [_obj(ad.get("shows")) for ad in ads]
        missing = sum(1 for s in shows if not _s(s.get("item")))
        if missing:
            out.append(Check("warn", "creative_shows_missing", n=missing))
        prices = [p for p in (_num(s.get("price_eur")) for s in shows) if p is not None]
        if len(ads) > 1 and len(prices) == len(ads) and all(p > 2 * typical for p in prices):
            out.append(Check("warn", "creative_unrepresentative", n=int(min(prices)), limit=int(typical)))
    kinds_by_ad = [tuple(dict.fromkeys(k for k in (_enum(b.get("kind"), BENEFIT_KINDS) for b in _dicts(ad.get("benefits"))) if k)) for ad in ads]
    for n, (ad, kinds) in enumerate(zip(ads, kinds_by_ad), 1):
        if len(kinds) < 2 or ad.get("test_role") == "control":
            continue
        isolated = any(len(other) == 1 and other[0] in kinds for m, other in enumerate(kinds_by_ad) if m != n - 1)
        if not isolated:
            out.append(Check("warn", "creative_confounded", ad=n, value=" + ".join(kinds)))


def _check_archive(pack: dict, out: list[Check]) -> None:
    for n, ad in enumerate(_ads(pack), 1):
        record = _obj(ad.get("record"))
        if not record:
            continue
        spend = _num(record.get("spend_eur")) or 0
        outcome = any((_num(record.get(k)) or 0) > 0 for k in ("impressions", "clicks", "leads", "sales", "revenue_eur"))
        if (spend > 0 or outcome) and not (_s(ad.get("primary_text")) or _s(ad.get("headline"))):
            out.append(Check("warn", "archive_result_without_copy", ad=n))
        if spend > 0 and not _s(record.get("source")):
            out.append(Check("warn", "archive_spend_unsourced", ad=n, value=f"{spend:g}"))


# --- entry point -------------------------------------------------------------------------------------------


def check(pack: dict, facts: str | None, images: dict[int, str]) -> list[Check]:
    known = _known(facts)
    out: list[Check] = []
    _check_wom(pack, known, out)
    _check_referral(pack, out)
    _check_retell(pack, known, out)
    _check_endorsements(pack, known, out)
    _check_proof_actions(pack, facts, known, out)
    _check_backer(pack, out)
    _check_green(pack, known, out)
    _check_defect(pack, out)
    _check_event(pack, known, out)
    _check_stockist(pack, known, out)
    _check_buying_situation(pack, out)
    _check_channel(pack, out)
    _check_entry(pack, known, out)
    _check_display(pack, out)
    _check_co_brand(pack, out)
    _check_pack_cues(pack, out)
    _check_chain(pack, known, out)
    _check_activation(pack, out)
    _check_placement(pack, out)
    _check_sequence(pack, known, out)
    _check_outcomes(pack, out)
    _check_creative(pack, out)
    _check_archive(pack, out)
    return out
