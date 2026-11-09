from __future__ import annotations

import base64
import json
import mimetypes
import threading
from rocky.tools import ToolError, WorkspaceTools

_BROWSER_LOCK = threading.Lock()


class ClarifyBridge:
    def __init__(self):
        self.pending: dict | None = None
        self._ready = threading.Event()
        self._answer = ""
        self.on_question = None

    def ask(self, question: str, options=None) -> str:
        self.pending = {"question": question, "options": list(options or [])}
        self._answer = ""
        self._ready.clear()
        hook = self.on_question
        if hook:
            hook(question, options)
        if not self._ready.wait(timeout=600):
            self.pending = None
            return "user did not answer; continue with documented assumptions"
        answer = self._answer
        self.pending = None
        return answer or "user sent an empty answer; continue with documented assumptions"

    def reply(self, text: str) -> None:
        self._answer = (text or "").strip()
        self._ready.set()


class BrowserSession:
    def __init__(self):
        self._play = None
        self._browser = None
        self._page = None

    def navigate(self, url: str) -> str:
        if not url.startswith(("http://", "https://")):
            raise ToolError("url must start with http:// or https://")
        with _BROWSER_LOCK:
            page = self._page_or_launch()
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            return self._snapshot_locked(page)

    def snapshot(self) -> str:
        with _BROWSER_LOCK:
            if self._page is None:
                raise ToolError("no open page — call browser_navigate first")
            return self._snapshot_locked(self._page)

    def close(self) -> str:
        with _BROWSER_LOCK:
            return self._close_locked()

    def _page_or_launch(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ToolError(
                "browser needs Playwright. Run: uv pip install playwright && playwright install chromium"
            ) from exc
        if self._page is not None:
            return self._page
        try:
            self._play = sync_playwright().start()
            self._browser = self._play.chromium.launch(headless=True)
            self._page = self._browser.new_page()
        except Exception as exc:  # noqa: BLE001
            self._close_locked()
            raise ToolError(
                f"could not start Chromium ({exc}). Run: playwright install chromium"
            ) from exc
        return self._page

    def _snapshot_locked(self, page) -> str:
        title = page.title()
        url = page.url
        try:
            text = page.inner_text("body")
        except Exception:
            text = page.content()
        text = " ".join((text or "").split())
        if len(text) > 12000:
            text = text[:12000] + "…"
        return f"title: {title}\nurl: {url}\n\n{text}"

    def _close_locked(self) -> str:
        for obj, method in (
            (self._page, "close"),
            (self._browser, "close"),
            (self._play, "stop"),
        ):
            if obj is None:
                continue
            try:
                getattr(obj, method)()
            except Exception:
                pass
        self._page = None
        self._browser = None
        self._play = None
        return "browser closed"


# Image models tried in order when ROCKY_IMAGE_MODEL is not set. The others are
# fallbacks for a 400 / 404 on the first.
IMAGE_MODEL_CANDIDATES = (
    "google/gemini-nano-banana-2.1",
    "google/gemini-3.1-flash-image",
    "google/gemini-3-pro-image",
)
IMAGE_DIR = "deliverables"
ASPECTS = {"1:1", "16:9", "9:16", "4:3", "3:4", "4:5", "5:4", "3:2", "2:3", "21:9"}


def image_models() -> list[str]:
    import os

    pinned = (os.environ.get("ROCKY_IMAGE_MODEL") or "").strip()
    if pinned:
        return [pinned, *[m for m in IMAGE_MODEL_CANDIDATES if m != pinned]]
    return list(IMAGE_MODEL_CANDIDATES)


def _unique_path(workspace: WorkspaceTools, path: str) -> str:
    """deliverables/visual.png → deliverables/visual-2.png when the first exists."""
    target = workspace.resolve(path)
    if not target.exists():
        return path
    stem, suffix = target.stem, target.suffix or ".png"
    for n in range(2, 200):
        candidate = target.with_name(f"{stem}-{n}{suffix}")
        if not candidate.exists():
            return str(candidate.relative_to(workspace.root))
    raise ToolError(f"too many files named like {path}")


def generate_image(
    llm,
    prompt: str,
    path: str,
    workspace: WorkspaceTools,
    reference_path: str = "",
    aspect_ratio: str = "",
    spend=None,
) -> str:
    """Generate a PNG with an OpenRouter image model and save it in the workspace."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ToolError("image_generate needs a prompt")
    generate = getattr(llm, "generate_image", None)
    if generate is None:
        raise ToolError("image_generate needs an OpenRouter client")
    aspect = (aspect_ratio or "").strip()
    if aspect and aspect not in ASPECTS:
        raise ToolError(f"aspect_ratio must be one of {', '.join(sorted(ASPECTS))}")
    reference = None
    if reference_path:
        ref = workspace.resolve(reference_path)
        if not ref.is_file():
            raise ToolError(f"reference image not found: {reference_path}")
        reference = (mimetypes.guess_type(ref.name)[0] or "image/png", ref.read_bytes())
    rel = (path or "").strip() or f"{IMAGE_DIR}/visual.png"
    if "/" not in rel:
        rel = f"{IMAGE_DIR}/{rel}"
    if not rel.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        rel += ".png"
    rel = _unique_path(workspace, rel)

    errors: list[str] = []
    result = None
    for model in image_models():
        try:
            result = generate(prompt=prompt, model=model, reference=reference, aspect_ratio=aspect or None)
        except Exception as exc:  # noqa: BLE001 — try the next model on any provider refusal
            errors.append(f"{model}: {str(exc)[:160]}")
            continue
        if spend is not None:
            try:
                spend(result)
            except Exception:  # noqa: BLE001
                pass
        if result.images:
            break
        errors.append(f"{model}: no image in the answer ({(result.text or '')[:120]})")
        result = None
    if result is None:
        raise ToolError("image generation failed — " + " | ".join(errors))

    mime, raw = result.images[0]
    if mime.startswith("image/") and not rel.lower().endswith(_ext_for(mime)):
        rel = str(workspace.resolve(rel).with_suffix(_ext_for(mime)).relative_to(workspace.root))
    target = workspace.resolve(rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    workspace._note_written(target)
    extra = ""
    if len(result.images) > 1:
        extra = f" (+{len(result.images) - 1} more returned, first saved)"
    note = f" · {result.text.strip()[:200]}" if result.text and result.text.strip() else ""
    return f"wrote {rel} ({len(raw) // 1024} KB) · model {result.model}{extra}{note}"


def _ext_for(mime: str) -> str:
    return {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/webp": ".webp"}.get(mime, ".png")


def _brief_question(brief: dict, prompt: str) -> str:
    """Turn a creative brief into a check the vision model answers as JSON."""
    lines = [
        "Check this image against the creative brief below. Answer with JSON only: "
        '{"verdict": "pass" or "fail", "style_seen": "...", "subject_seen": "...", '
        '"people_seen": "...", "mismatches": ["one line per brief item the image breaks"], "notes": "..."}. '
        "A mismatch is anything the brief requires and the image lacks, anything it forbids and the image shows, "
        "a style other than the one asked for, or people who do not look like the audience named."
    ]
    for key, label in (("style", "Style"), ("subject", "Subject"), ("audience", "People shown must look like this audience")):
        value = brief.get(key)
        if value:
            lines.append(f"{label}: {value}")
    for key, label in (("must_include", "Must include"), ("must_not", "Must not show")):
        items = brief.get(key)
        if isinstance(items, str):
            items = [items]
        if items:
            lines.append(f"{label}: " + "; ".join(str(x) for x in items if str(x).strip()))
    if prompt:
        lines.append(prompt)
    return "\n".join(lines)


def _brief_verdict(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {"verdict": "unparsed", "mismatches": []}
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return {"verdict": "unparsed", "mismatches": []}
    verdict = str(data.get("verdict") or "").strip().lower()
    mismatches = data.get("mismatches") or []
    if not isinstance(mismatches, list):
        mismatches = [str(mismatches)]
    if verdict not in ("pass", "fail"):
        verdict = "fail" if mismatches else "unparsed"
    return {"verdict": verdict, "mismatches": [str(m) for m in mismatches if str(m).strip()], "people_seen": data.get("people_seen"), "style_seen": data.get("style_seen")}


def analyze_image(llm, source: str, prompt: str, workspace: WorkspaceTools, spend=None, brief: dict | None = None) -> str:
    if not source:
        raise ToolError("vision_analyze needs path or url")
    if brief is not None and not isinstance(brief, dict):
        raise ToolError("brief must be an object with style, subject, audience, must_include and must_not")
    if brief:
        question = _brief_question(brief, prompt)
    else:
        question = prompt or "Describe this image. Note text, layout, and anything relevant to the task."
    image_part: dict
    if source.startswith(("http://", "https://")):
        image_part = {"type": "image_url", "image_url": {"url": source}}
    else:
        path = workspace.resolve(source)
        if not path.exists() or not path.is_file():
            raise ToolError(f"image not found: {source}")
        raw = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        b64 = base64.b64encode(raw).decode("ascii")
        image_part = {
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
        }
    model = _vision_model()
    completion = llm.complete(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    image_part,
                ],
            }
        ],
        extra={"provider": {"require_parameters": False}},
    )
    if spend is not None:
        try:
            spend(completion)
        except Exception:  # noqa: BLE001
            pass
    text = completion.content or "(empty vision response)"
    if brief:
        check = _brief_verdict(text)
        tail = f"\nbrief_check: {check['verdict']}"
        if check["mismatches"]:
            tail += "\nmismatches:\n" + "\n".join(f"- {m}" for m in check["mismatches"])
        if check["verdict"] == "fail":
            tail += "\nRegenerate once with the mismatches fixed, or record visual_mismatch in facts; a chart belongs to a script, not an image model."
        return f"model {model}\n{text}{tail}"
    return f"model {model}\n{text}"


def _vision_model() -> str:
    import os

    return (os.environ.get("ROCKY_VISION_MODEL") or "google/gemini-3.8-flash").strip()
