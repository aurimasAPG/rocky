from __future__ import annotations

import base64
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


def analyze_image(llm, source: str, prompt: str, workspace: WorkspaceTools) -> str:
    if not source:
        raise ToolError("vision_analyze needs path or url")
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
    text = completion.content or "(empty vision response)"
    return f"model {model}\n{text}"


def _vision_model() -> str:
    import os

    return (os.environ.get("ROCKY_VISION_MODEL") or os.environ.get("ORCHE_VISION_MODEL") or "google/gemini-2.5-flash").strip()
