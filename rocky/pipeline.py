from __future__ import annotations

MARKETING_HINTS = (
    "marketing",
    "copywriting",
    "landing page",
    "landing-page",
    "voice of customer",
    " voc",
    "voc ",
    "meta ads",
    "facebook ad",
    "instagram ad",
    "google ads",
    "google ad ",
    "paid ads",
    "ad copy",
    "ad campaign",
    "kampanij",
    "skelbim",
    "reklam",
    "seo audit",
    "seo ",
    " cro",
    "cro ",
    "headline",
    "cta ",
    "pitch deck",
)


def is_marketing(goal: str, assessment: dict | None = None) -> bool:
    domain = str((assessment or {}).get("domain") or "").lower()
    if domain == "marketing":
        return True
    blob = f" {goal} {(assessment or {}).get('goal') or ''} {(assessment or {}).get('deliverable') or ''} ".lower()
    return any(hint in blob for hint in MARKETING_HINTS)


def canonical_marketing_steps(goal: str) -> list[dict]:
    brief = (goal or "").strip() or "the user's marketing request"
    return [
        {
            "id": 1,
            "title": "Research",
            "detail": (
                "skill_view deep-research (and research-prompt if listed). "
                f"Investigate the market, competitors, and claims for: {brief}. "
                "web_search + web_fetch primary sources. Write research.md with URL, claim, why it matters. "
                "Separate fact / inference / unknown. Do not invent statistics."
            ),
            "depends_on": [],
        },
        {
            "id": 2,
            "title": "VOC",
            "detail": (
                "skill_view voc-to-copy. If product-marketing-context is listed, skill_view it too. "
                "Mine customer language from research.md plus public reviews, Reddit, G2, forums via web_search. "
                "Write voc.md: pains, desires, objections, outcomes, phrase bank with sources. "
                "Do not invent quotes."
            ),
            "depends_on": [1],
        },
        {
            "id": 3,
            "title": "Copy",
            "detail": (
                "skill_view copywriting. If the goal is ads, also skill_view meta-ads-copy or google-ads-copy. "
                "Write copy.md from VOC phrases (headlines, body, CTAs). No invented proof."
            ),
            "depends_on": [2],
        },
        {
            "id": 4,
            "title": "Deck and spreadsheet",
            "detail": (
                "skill_view pptx then skill_view xlsx. "
                "Build deliverables/campaign.pptx (one idea per slide from the copy) and "
                "deliverables/variants.xlsx (headlines, CTAs, VOC phrases, source URLs). "
                "Never write_file those binaries — use python-pptx and openpyxl via bash. office_read to QA."
            ),
            "depends_on": [3],
        },
    ]


def _blob(steps: list[dict]) -> str:
    return " ".join(f"{s.get('title', '')} {s.get('detail', '')}" for s in steps).lower()


def ensure_pipeline(plan: dict, goal: str, assessment: dict | None = None) -> dict:
    if not is_marketing(goal, assessment):
        return plan
    steps = list(plan.get("steps") or [])
    blob = _blob(steps)
    has_research = any(w in blob for w in ("research", "tyrim", "deep-research"))
    has_voc = "voc" in blob or "voice of customer" in blob
    has_copy = any(w in blob for w in ("copy", "copywrit", "headline", "ad text", "tekst"))
    has_office = any(w in blob for w in ("pptx", "xlsx", "spreadsheet", "deck", "slides"))
    if has_research and has_voc and has_copy and has_office and len(steps) >= 4:
        dest = str(plan.get("deliverable_path") or "")
        if not dest.endswith((".pptx", ".xlsx")):
            plan["deliverable_path"] = "deliverables/campaign.pptx"
        return plan
    plan["steps"] = canonical_marketing_steps(goal)
    plan["deliverable_path"] = "deliverables/campaign.pptx"
    extra = "Also write research.md, voc.md, copy.md, and deliverables/variants.xlsx."
    notes = str(plan.get("notes") or "").strip()
    plan["notes"] = f"{notes} {extra}".strip() if notes else extra
    return plan
