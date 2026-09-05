from __future__ import annotations

import os
import re
from pathlib import Path

_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)

# Research + marketing skills from ~/.claude/skills. Bundled names always win.
# APG / personal skills stay out unless the user points ROCKY_SKILLS at another folder.
CLAUDE_ALLOWLIST = frozenset(
    {
        "research-prompt",
        "perplexity-deep-research",
        "competitor-audit",
        "competitor-alternatives",
        "product-marketing-context",
        "voc-to-copy",
        "copywriting",
        "copy-editing",
        "copy-frameworks",
        "headline-factory",
        "benefit-ladder",
        "ad-creative",
        "meta-ads-copy",
        "google-ads-copy",
        "paid-ads",
        "video-ad-script",
        "page-cro",
        "landing-page-copy-audit",
        "content-strategy",
        "seo-audit",
        "ai-seo",
        "marketing-ideas",
        "marketing-psychology",
        "email-sequence",
        "cold-email",
        "social-content",
        "sales-enablement",
        "launch-strategy",
        "pricing-strategy",
        "pricing-page-copy",
        "form-cro",
        "popup-cro",
        "onboarding-cro",
        "signup-flow-cro",
        "paywall-upgrade-cro",
        "analytics-tracking",
        "ab-test-setup",
        "programmatic-seo",
        "schema-markup",
        "site-architecture",
        "free-tool-strategy",
        "referral-program",
        "churn-prevention",
        "revops",
        # Lithuanian language: spelling / grammar and richer wording for LT copy.
        "lietuviu-kalbos-rasyba",
        "turtinga-lietuviu-kalba",
    }
)


def bundled_dir() -> Path:
    return Path(__file__).resolve().parent / "skills"


def home_skills_dir() -> Path:
    return Path.home() / ".claude" / "skills"


def _env_skill_dirs() -> list[Path]:
    raw = os.environ.get("ROCKY_SKILLS") or os.environ.get("ORCHE_SKILLS") or ""
    out: list[Path] = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            out.append(Path(part).expanduser())
    return out


def extra_dirs() -> list[Path]:
    seen: list[Path] = []
    for raw in [bundled_dir(), home_skills_dir(), *_env_skill_dirs()]:
        try:
            path = raw.expanduser().resolve()
        except OSError:
            continue
        if path in seen:
            continue
        seen.append(path)
    return seen


def _is_home_catalog(root: Path) -> bool:
    try:
        home = home_skills_dir()
        if not home.is_dir():
            return False
        return root.resolve() == home.resolve()
    except OSError:
        return False


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    meta: dict[str, str] = {}
    match = _FRONT.match(text)
    body = text
    if match:
        body = text[match.end() :]
        for line in match.group(1).splitlines():
            if ":" not in line:
                continue
            key, val = line.split(":", 1)
            meta[key.strip()] = val.strip().strip("\"'")
    return meta, body


def list_skills() -> list[dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for root in extra_dirs():
        if not root.is_dir():
            continue
        restrict = _is_home_catalog(root)
        for skill_md in sorted(root.glob("*/SKILL.md")):
            text = skill_md.read_text(encoding="utf-8", errors="replace")
            meta, _ = _parse_frontmatter(text)
            folder = skill_md.parent.name
            name = meta.get("name") or folder
            if restrict and folder not in CLAUDE_ALLOWLIST and name not in CLAUDE_ALLOWLIST:
                continue
            if name in found:
                continue
            found[name] = {
                "name": name,
                "description": meta.get("description") or "",
                "path": str(skill_md),
            }
    return list(found.values())


def catalog_for_prompt() -> list[dict[str, str]]:
    rows = []
    for skill in list_skills():
        desc = (skill.get("description") or "").replace("\n", " ").strip()
        rows.append({"name": skill["name"], "description": desc[:180]})
    return rows


def view_skill(name: str) -> str:
    needle = (name or "").strip().lower().lstrip("/")
    if not needle:
        raise ValueError("skill name is empty")
    for skill in list_skills():
        if skill["name"].lower() == needle:
            return Path(skill["path"]).read_text(encoding="utf-8", errors="replace")
    available = ", ".join(s["name"] for s in list_skills()) or "(none)"
    raise FileNotFoundError(f"unknown skill {name!r}. available: {available}")


def read_office(path: Path) -> str:
    suf = path.suffix.lower()
    if suf in {".xlsx", ".xlsm"}:
        from openpyxl import load_workbook

        wb = load_workbook(path, data_only=True)
        chunks: list[str] = []
        for sheet in wb.worksheets:
            chunks.append(f"# {sheet.title}")
            for row in sheet.iter_rows(values_only=True):
                chunks.append("\t".join("" if c is None else str(c) for c in row))
        return "\n".join(chunks).strip()
    if suf == ".docx":
        from docx import Document

        doc = Document(str(path))
        parts = [p.text for p in doc.paragraphs if p.text]
        for table in doc.tables:
            for row in table.rows:
                parts.append(" | ".join(c.text for c in row.cells))
        return "\n".join(parts).strip()
    if suf == ".pptx":
        from pptx import Presentation

        prs = Presentation(str(path))
        parts = []
        for i, slide in enumerate(prs.slides, 1):
            parts.append(f"# Slide {i}")
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text:
                    parts.append(shape.text)
        return "\n".join(parts).strip()
    if suf == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n".join((p.extract_text() or "") for p in reader.pages).strip()
    from markitdown import MarkItDown

    return (MarkItDown().convert(str(path)).text_content or "").strip()
