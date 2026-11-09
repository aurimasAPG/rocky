"""Size a goal from its words, with no model call.

The assessor model answered four questions: how big, what kind of work, which file,
does it need the web. An agent decides the rest while it works, so these answers only
set the first estimate, the model tier and a starting deliverable path. Words are enough
for that, and they are free. Lithuanian stems sit beside the English ones because most
goals are written in Lithuanian.
"""

from __future__ import annotations

import re
from pathlib import Path

from rocky.engine import DELIVERABLES_DIR, _language_for, place_deliverable
from rocky.recipes import find_recipe, load_recipes

_DOMAIN_WORDS = {
    "marketing": (
        r"marketing|rinkodar|reklam|kampanij|\bads?\b|copywrit|landing|\bseo\b|\bcro\b|funnel|piltuv|konversij|conversion|"
        r"auditorij|segment|positioning|pozicion|prekės ženkl|prekių ženkl|\bbrand|kainodar|pricing|\bkain[aąoų]|\bprice|nuolaid|discount|"
        r"\bpromo|akcij|pardavim|\bsales\b|naujienlaišk|newsletter|press release|pranešim\w* spaudai|turinio kalendor|content calendar|"
        r"\broas\b|\bcac\b|\bclv\b|\bltv\b|\ba/b\b"
    ),
    "code": r"python|javascript|typescript|\bscript|skript|\bfunction\b|funkcij|\bbug\b|refactor|unit test|\bapi\b|\.py\b|\.js\b|\brepo\b|\bkod[oąas]\b",
    "research": r"research|tyrim|ištirk|išnagrinėk|compar|palygin|competitor|konkurent|market size|rinkos dyd|apžvalg|analy[sz]|analiz|šaltini|\bsources\b",
    "writing": r"\bwrite\b|parašyk|rašyk|article|straipsn|\bessay\b|esė|\bblog|tinklaraš|\bletter\b|laišk|\bemail\b|summary|santrauk|rewrite|perrašyk|translate|išversk|\bstory\b|istorij",
    "ops": r"deploy|backup|\bcron\b|\bserver\b|docker|migrat|\binstall\b|įdiek",
}
_DOMAIN_ORDER = ("marketing", "code", "research", "writing", "ops")
_FORMATS = {
    "pptx": r"\.pptx\b|\bpptx\b|\bdeck\b|\bslides?\b|skaidr|prezentacij",
    "xlsx": r"\.xlsx\b|\bxlsx\b|\bexcel\b|spreadsheet|skaičiuokl",
    "docx": r"\.docx\b|\bdocx\b|\bword document\b|word dokument",
    "pdf": r"\.pdf\b|\bpdf\b",
    "html": r"\.html\b|\bhtml\b|landing page|web page",
}
_OUTPUT_FILE = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w-]+\.(?:md|txt|html|pdf|docx|pptx|xlsx))\b", re.I)
_URL = re.compile(r"https?://|\b[\w-]+\.(?:lt|com|eu|org|net|io|lv|ee|pl|de)\b", re.I)
_WEB_WORDS = re.compile(r"research|tyrim|ištirk|konkurent|competitor|market|rinkos|trend|tendencij|naujien|\bnews\b|šaltini|\bsources\b", re.I)
_HARD_WORDS = re.compile(r"multi-file|kelis failus|architect|full report|išsami\w* ataskait|comprehensive|strategij\w* metams|annual strategy", re.I)


def _domain(text: str) -> str:
    counts = {name: len(re.findall(_DOMAIN_WORDS[name], text, re.I)) for name in _DOMAIN_ORDER}
    best = max(counts.values())
    if not best:
        return "mixed"
    return next(name for name in _DOMAIN_ORDER if counts[name] == best)


def _formats(text: str) -> list[str]:
    return [name for name, pattern in _FORMATS.items() if re.search(pattern, text, re.I)]


def _deliverable(goal: str, workspace: Path, recipe, formats: list[str]) -> str:
    """A file the goal names and that does not exist yet is the output; one already there is the input."""
    for match in _OUTPUT_FILE.finditer(goal):
        name = match.group(1)
        if not (workspace / name).exists():
            return place_deliverable(name)
    if recipe is not None and recipe.deliverable_path:
        return recipe.deliverable_path
    return f"{DELIVERABLES_DIR}/output.{formats[0] if formats else 'md'}"


def _complexity(goal: str, domain: str, recipe, formats: list[str], needs_web: bool) -> str:
    words = len(goal.split())
    if recipe is not None:
        return "moderate"
    if words > 120 or len(formats) >= 2 or _HARD_WORDS.search(goal):
        return "hard"
    if domain in {"code", "research"} and words > 25:
        return "hard"
    if words <= 30 and domain in {"writing", "mixed"} and not needs_web and not formats:
        return "trivial"
    return "moderate"


def assess_goal(goal: str, workspace: str | Path) -> dict:
    """The fields the model assessor returned, worked out from the goal's words."""
    workspace_path = Path(workspace)
    recipe = find_recipe(goal, {"goal": goal}, load_recipes(workspace_path))
    domain = _domain(goal)
    formats = _formats(goal)
    needs_web = bool(_URL.search(goal) or _WEB_WORDS.search(goal))
    assessment = {
        "goal": goal,
        "complexity": _complexity(goal, domain, recipe, formats, needs_web),
        "domain": domain,
        "success_criteria": ["The deliverable answers the goal as written."],
        "needs_web": needs_web,
        "deliverable": _deliverable(goal, workspace_path, recipe, formats),
        "heuristic": True,
    }
    assessment["language"] = _language_for(goal, {})
    return assessment
