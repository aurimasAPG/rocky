"""The finishing check: what code can prove before any model reads the result.

When the agent says it is done, this runs before anything else judges the work: the
deliverable and the recipe's files are on disk, a binary file opens, the ad board has
no blocking finding, every figure in the deliverable traces to something the run really
saw, and a plan that ``plan_check`` covers went through it. ``blocking`` findings send
the agent back. ``soft`` findings (a figure with no source) send it back once and then
reach the user as notes, because a match on words can miss a source a person would accept.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from rocky.adboard import is_board, verifier_brief
from rocky.claims import FACTS_PATH, _numbers, claim_check
from rocky.engine import _missing_files
from rocky.skills import read_office
from rocky.tools import ToolError, WorkspaceTools

EVIDENCE_PATH = "runs/notes/tool-evidence.md"
# Past this size the oldest half of the evidence file is dropped when a run starts.
EVIDENCE_MAX_BYTES = 2_000_000
EVIDENCE_RESULT_CHARS = 20_000
# Tools whose answers are arithmetic on the user's inputs: a figure they produced needs no web source.
CALC_TOOLS = frozenset({
    "unit_economics", "customer_economics", "experiment_plan", "funnel_analyze", "table_analyze",
    "repeat_buying", "reach_overlap", "media_plan", "campaign_calc",
})
# Tools that bring outside text into the run: what they returned is a source the deliverable may cite.
SOURCE_TOOLS = frozenset({"web_fetch", "web_search", "browser_snapshot", "office_read", "page_inspect", "seo_inspect", "web_check", "vision_analyze"})
TEXT_DRAFTS = (".md", ".markdown", ".txt", ".html", ".htm")
OFFICE_DRAFTS = (".pptx", ".docx", ".pdf")
BINARY_DELIVERABLES = (".pptx", ".docx", ".pdf", ".xlsx")
SOURCE_SUFFIXES = (".md", ".markdown", ".txt", ".html", ".htm", ".json", ".csv", ".tsv", ".xml")
MAX_SOURCE_FILES = 60
MAX_SOURCE_BYTES = 2_000_000
MAX_LISTED = 12
# A sentence that says the figure is the author's own proposal or estimate is not a factual claim.
_OWN_FIGURE = re.compile(
    r"siūl|rekomend|įvert|apytiksl|prielaid|planuoj|tiksl|biudžet|numatom|hipotez|preliminar|"
    r"estimat|assum|propos|recommend|target|budget|plan to|hypothes|approximately|roughly|~\s*\d",
    re.I,
)
# Goals whose deliverable plan_check knows how to check, with the kind to run.
_PLAN_KINDS = (
    ("content_calendar", r"content calendar|editorial calendar|turinio kalendor"),
    ("pr_board", r"press release|pranešim\w* spaudai|message house"),
    ("demo_board", r"demo plan|demonstracijos plan"),
    ("response_board", r"escalation matrix|eskalavimo"),
    ("partner_board", r"partner program|partnerių program"),
    ("qualification", r"qualification sheet|kvalifikavimo lap"),
    ("community_rules", r"community rules|bendruomenės taisykl"),
)


class Evidence:
    """What this run really saw, kept on disk so a figure can be traced to it afterwards."""

    def __init__(self, tools: WorkspaceTools, goal: str):
        self.tools = tools
        self.path = tools.root / EVIDENCE_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_file() and self.path.stat().st_size > EVIDENCE_MAX_BYTES:
            kept = self.path.read_text(encoding="utf-8", errors="replace")
            self.path.write_text(kept[len(kept) // 2 :], encoding="utf-8")
        self.computed_text = [goal]
        self._append("The user's brief", goal)

    def _append(self, title: str, body: str) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n\n## {title}\n{body}\n")

    def add(self, name: str, arguments: dict, result: str) -> None:
        if result.startswith("error:"):
            return
        if name in CALC_TOOLS:
            self.computed_text.append(result)
        elif name not in SOURCE_TOOLS:
            return
        hint = str((arguments or {}).get("url") or (arguments or {}).get("query") or (arguments or {}).get("path") or "")
        self._append(f"{name} {hint}".strip(), result[:EVIDENCE_RESULT_CHARS])

    def computed_numbers(self, language: str) -> set[str]:
        found: set[str] = set()
        for text in self.computed_text:
            for number in _numbers(text, language):
                found |= set(number.candidates)
        return found


def _source_files(tools: WorkspaceTools, deliverable: str) -> list[str]:
    """The run's evidence, notes that cite a URL, and files the user put in the folder."""
    root = tools.root
    written = {str(item) for item in tools.files_on_disk()}
    found: list[str] = [EVIDENCE_PATH] if (root / EVIDENCE_PATH).is_file() else []
    notes = root / "runs" / "notes"
    if notes.is_dir():
        for path in sorted(notes.rglob("*")):
            rel = path.relative_to(root).as_posix()
            if rel in found or rel == FACTS_PATH or not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES or path.stat().st_size > MAX_SOURCE_BYTES:
                continue
            # A draft the agent wrote without a single link is not evidence for itself.
            if "http://" in (text := path.read_text(encoding="utf-8", errors="replace")) or "https://" in text:
                found.append(rel)
    for path in sorted(root.iterdir()):
        rel = path.name
        if path.is_file() and path.suffix.lower() in SOURCE_SUFFIXES and rel not in written and rel != deliverable and path.stat().st_size <= MAX_SOURCE_BYTES:
            found.append(rel)
    return found[:MAX_SOURCE_FILES]


def deliverable_text(tools: WorkspaceTools, path: str, limit: int = 12_000) -> str:
    """The deliverable as text, for the figure check and the review."""
    try:
        target = tools.resolve(path)
        if target.suffix.lower() in BINARY_DELIVERABLES:
            return read_office(target)[:limit]
        return target.read_text(encoding="utf-8", errors="replace")[:limit]
    except Exception as exc:  # noqa: BLE001 — python-pptx, openpyxl and pypdf each raise their own
        return f"(could not read {path}: {exc})"


def _check_figures(tools: WorkspaceTools, deliverable: str, language: str, evidence: Evidence | None) -> tuple[list[str], list[str], dict]:
    """Every number, ranking and credential in the deliverable, against what the run saw."""
    suffix = Path(deliverable).suffix.lower()
    args: dict[str, Any] = {"language": language, "sources": _source_files(tools, deliverable)}
    if suffix in TEXT_DRAFTS:
        args["draft_path"] = deliverable
    elif suffix in OFFICE_DRAFTS:
        args["draft"] = deliverable_text(tools, deliverable, limit=60_000)
    else:
        return [], [], {"status": "not applicable"}
    try:
        result = claim_check(tools, args)
    except ToolError as exc:
        return [], [], {"status": "skipped", "detail": str(exc)}
    computed = evidence.computed_numbers(language) if evidence is not None else set()

    def from_a_tool(claim: dict) -> bool:
        numbers = _numbers(str(claim.get("text") or ""), language)
        return bool(numbers) and all(set(n.candidates) & computed for n in numbers)

    hard: list[str] = []
    soft: list[str] = []
    for claim in result.get("claims") or []:
        status = claim.get("status")
        if status not in {"number_differs", "unsupported"} or from_a_tool(claim):
            continue
        text = str(claim.get("text") or "")
        if status == "number_differs":
            hard.append(f"A figure differs from its source: \"{text[:160]}\". {claim.get('note') or ''}".strip())
        elif not _OWN_FIGURE.search(text):
            soft.append(f"No source for: \"{text[:160]}\"")
    return hard, soft, {"status": "checked", "counts": result.get("counts"), "differs": len(hard), "no_source": len(soft)}


def _check_plans(goal: str, deliverable: str, plan_checks: dict[str, dict]) -> tuple[list[str], dict]:
    hard: list[str] = []
    needed = [kind for kind, pattern in _PLAN_KINDS if re.search(pattern, f"{goal} {deliverable}", re.I)]
    for kind in needed:
        if kind not in plan_checks:
            hard.append(f"This goal asks for a plan plan_check can verify, and it was not run. Call plan_check with kind {kind} on the plan before handing it over.")
    for kind, result in plan_checks.items():
        for finding in result.get("findings") or []:
            if finding.get("level") == "block":
                hard.append(f"plan_check {kind} still blocks: {finding.get('code')}: {str(finding.get('note') or '')[:200]}")
    return hard, {"status": "checked" if needed or plan_checks else "not applicable", "kinds": sorted(plan_checks), "required": needed}


def run_gate(
    *,
    tools: WorkspaceTools,
    plan: dict,
    goal: str,
    language: str,
    required: list[str],
    evidence: Evidence | None = None,
    plan_checks: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """Run every check. ``required`` lists the files that must be on disk."""
    deliverable = str(plan.get("deliverable_path") or "")
    missing = _missing_files(tools, list(dict.fromkeys(p for p in [deliverable, *required] if p)))
    blocking: list[str] = [f"Missing or empty file: {path}" for path in missing]
    soft: list[str] = []
    checks: dict[str, Any] = {"files": {"status": "failed" if missing else "ok", "missing": missing}}
    ready = bool(deliverable) and deliverable not in missing

    board_warnings: list[str] = []
    if is_board(plan):
        brief = verifier_brief(tools) if ready else None
        if brief is None:
            if ready:
                blocking.append("ad_board has not produced this page. Call ad_board with the whole pack.")
            checks["ad_board"] = {"status": "failed"}
        else:
            found = brief["automatic_checks"]
            blocking.extend(f"ad_board: {line}" for line in found["blocking"])
            board_warnings = list(found["warnings"])
            checks["ad_board"] = {"status": "failed" if found["blocking"] else "ok", "blocking": len(found["blocking"]), "warnings": len(found["warnings"])}
    elif ready:
        if Path(deliverable).suffix.lower() in BINARY_DELIVERABLES:
            text = deliverable_text(tools, deliverable, limit=2_000)
            if text.startswith("(could not read") or not text.strip():
                blocking.append(f"{deliverable} does not open or holds no text. Rebuild it with its script and check it with office_read.")
                checks["opens"] = {"status": "failed"}
            else:
                checks["opens"] = {"status": "ok"}
        hard, loose, info = _check_figures(tools, deliverable, language, evidence)
        blocking.extend(hard)
        soft.extend(loose)
        checks["figures"] = info

    hard, info = _check_plans(goal, deliverable, plan_checks or {})
    blocking.extend(hard)
    checks["plans"] = info
    return {"blocking": blocking, "soft": soft, "checks": checks, "missing": missing, "board_warnings": board_warnings}


def gate_message(gate: dict) -> str:
    """What the agent is told when the check sends it back."""
    lines = ["Not finished. The automatic check found:"]
    lines.extend(f"- {item}" for item in gate["blocking"][:MAX_LISTED])
    lines.extend(f"- {item}" for item in gate["soft"][:MAX_LISTED])
    if len(gate["blocking"]) + len(gate["soft"]) > 2 * MAX_LISTED:
        lines.append("- (more of the same)")
    if gate["missing"]:
        lines.append("Write the missing files (write_file / append_file for text; for .pptx .xlsx .docx .pdf run your script with bash).")
    if gate["soft"]:
        lines.append(
            f"For a figure with no source: put the fact and its URL in {FACTS_PATH}, remove the figure, or say in the same sentence that it is your own estimate or proposal."
        )
    lines.append("Then call done again.")
    return "\n".join(lines)


def compact_checks(gate: dict) -> dict:
    """The check's result as one event or prompt payload."""
    return json.loads(json.dumps({k: gate[k] for k in ("checks", "blocking", "soft")}, ensure_ascii=False, default=str))
