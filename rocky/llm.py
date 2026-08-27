from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from rocky.jsonutil import parse_json_object
from rocky.usage import Usage, parse_usage


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Completion:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    cost: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    model: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def usage(self) -> Usage:
        return Usage(
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            reasoning_tokens=self.reasoning_tokens,
            total_tokens=self.total_tokens or (self.prompt_tokens + self.completion_tokens),
            usd=self.cost,
        )


class LLM(Protocol):
    def complete(
        self,
        *,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        json_schema: dict | None = None,
        extra: dict | None = None,
    ) -> Completion: ...

    def list_models(self) -> dict: ...


class OpenRouterClient:
    def __init__(self, api_key: str, base_url: str = "https://openrouter.ai/api/v1"):
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is missing")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(timeout=httpx.Timeout(120.0, connect=20.0))

    def close(self) -> None:
        self._http.close()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://rocky.local",
            "X-OpenRouter-Title": "rocky",
        }

    def list_models(self) -> dict:
        response = self._http.get(
            f"{self.base_url}/models",
            headers=self._headers(),
            params={"supported_parameters": "tools", "output_modalities": "text"},
        )
        response.raise_for_status()
        return response.json()

    def complete(
        self,
        *,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        json_schema: dict | None = None,
        extra: dict | None = None,
    ) -> Completion:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "provider": {"require_parameters": True},
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
            body["parallel_tool_calls"] = True
            body["max_tool_calls"] = 12
        if json_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": json_schema.get("name") or "result",
                    "strict": True,
                    "schema": json_schema.get("schema") or json_schema,
                },
            }
            body["plugins"] = [{"id": "response-healing"}]
        if extra:
            body.update(extra)

        try:
            data = self._post(body)
        except httpx.HTTPStatusError as exc:
            # Structured output / server tools / require_parameters can 400 on some models.
            retry = dict(body)
            changed = False
            if json_schema:
                retry.pop("response_format", None)
                retry.pop("plugins", None)
                changed = True
            if tools and any(str(t.get("type", "")).startswith("openrouter:") for t in tools):
                retry["tools"] = [t for t in tools if t.get("type") == "function"]
                changed = True
            retry.get("provider", {}).pop("require_parameters", None)
            if retry.get("reasoning"):
                retry.pop("reasoning", None)
                changed = True
            if not changed:
                raise
            try:
                data = self._post(retry)
            except httpx.HTTPStatusError:
                raise exc from None

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage = parse_usage(data.get("usage") or {})
        calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            fn = raw.get("function") or {}
            args = fn.get("arguments") or "{}"
            try:
                parsed = parse_json_object(args) if isinstance(args, str) else (args or {})
            except Exception:
                parsed = {"_raw": args}
            calls.append(
                ToolCall(
                    id=str(raw.get("id") or ""),
                    name=str(fn.get("name") or ""),
                    arguments=parsed,
                )
            )
        content = message.get("content") or ""
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part) for part in content
            )
        return Completion(
            content=str(content),
            tool_calls=calls,
            cost=usage.usd,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            total_tokens=usage.total_tokens,
            model=data.get("model") or model,
            raw=data,
        )

    def _post(self, body: dict) -> dict:
        response = self._http.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=body,
        )
        if response.status_code >= 400:
            detail = response.text[:800]
            raise httpx.HTTPStatusError(
                f"OpenRouter {response.status_code}: {detail}",
                request=response.request,
                response=response,
            )
        return response.json()


class WebTools:
    """Client-side search/fetch so cheap workers still have the web."""

    def __init__(self, http: httpx.Client | None = None):
        self._http = http or httpx.Client(
            timeout=httpx.Timeout(25.0, connect=10.0),
            follow_redirects=True,
            headers={"User-Agent": "rocky/0.1 (+https://openrouter.ai)"},
        )

    def search(self, query: str) -> str:
        if not query.strip():
            return "empty query"
        try:
            response = self._http.get(
                "https://lite.duckduckgo.com/lite/",
                params={"q": query},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return f"search failed: {exc}"
        html = response.text
        # lite.duckduckgo.com uses result-link class
        import re

        items = re.findall(
            r'<a[^>]+class="result-link"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            html,
            flags=re.I | re.S,
        )
        if not items:
            items = re.findall(
                r'<a[^>]+rel="nofollow"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                html,
                flags=re.I | re.S,
            )
        lines = []
        for href, title in items[:8]:
            title = re.sub(r"<[^>]+>", "", title)
            title = re.sub(r"\s+", " ", title).strip()
            lines.append(f"- {title}\n  {href}")
        return "\n".join(lines) if lines else "no results"

    def fetch(self, url: str) -> str:
        if not url.startswith(("http://", "https://")):
            return "url must start with http:// or https://"
        try:
            response = self._http.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return f"fetch failed: {exc}"
        text = response.text
        if "<" in text[:200]:
            import re

            text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
            text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
            text = re.sub(r"(?is)<[^>]+>", " ", text)
        text = " ".join(text.split())
        return text[:12000] + ("…" if len(text) > 12000 else "")
