import base64
import json

import httpx
import pytest

from rocky.extras import generate_image
from rocky.llm import ImageResult, OpenRouterClient
from rocky.tools import ToolError, WorkspaceTools, dispatch
from rocky.usage import Usage

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


class FakeImageLLM:
    def __init__(self, fail_first=False, empty=False):
        self.calls = []
        self.fail_first = fail_first
        self.empty = empty

    def generate_image(self, *, prompt, model, reference=None, aspect_ratio=None):
        self.calls.append({"prompt": prompt, "model": model, "reference": reference, "aspect": aspect_ratio})
        if self.fail_first and len(self.calls) == 1:
            raise RuntimeError("OpenRouter 404: model not found")
        if self.empty:
            return ImageResult(images=[], text="I cannot draw that", model=model)
        return ImageResult(images=[("image/png", PNG)], text="here", model=model, usage=Usage(usd=0.03, calls=1))


def test_generate_image_writes_png_under_deliverables_and_bills(tmp_path):
    ws = WorkspaceTools(tmp_path)
    llm = FakeImageLLM()
    billed = []
    out = generate_image(llm, "a coral hero banner", "", ws, aspect_ratio="16:9", spend=billed.append)
    assert out.startswith("wrote deliverables/visual.png")
    assert (tmp_path / "deliverables" / "visual.png").read_bytes() == PNG
    assert llm.calls[0]["model"] == "google/gemini-nano-banana-2.1"
    assert llm.calls[0]["aspect"] == "16:9"
    assert billed and billed[0].usage.usd == 0.03
    # second call with the same default name does not overwrite
    out2 = generate_image(llm, "again", "", ws)
    assert out2.startswith("wrote deliverables/visual-2.png")


def test_generate_image_falls_back_to_the_next_model(tmp_path):
    ws = WorkspaceTools(tmp_path)
    llm = FakeImageLLM(fail_first=True)
    out = generate_image(llm, "x", "deliverables/ad.png", ws)
    assert "model google/gemini-3.1-flash-image" in out
    assert [c["model"] for c in llm.calls][:2] == ["google/gemini-nano-banana-2.1", "google/gemini-3.1-flash-image"]


def test_generate_image_reports_when_no_model_returns_an_image(tmp_path):
    ws = WorkspaceTools(tmp_path)
    with pytest.raises(ToolError) as exc:
        generate_image(FakeImageLLM(empty=True), "x", "", ws)
    assert "no image in the answer" in str(exc.value)
    assert not (tmp_path / "deliverables").exists()


def test_generate_image_env_pin_and_reference(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_IMAGE_MODEL", "openai/gpt-5-image")
    ws = WorkspaceTools(tmp_path)
    (tmp_path / "logo.png").write_bytes(PNG)
    llm = FakeImageLLM()
    generate_image(llm, "match this", "hero", ws, reference_path="logo.png")
    assert llm.calls[0]["model"] == "openai/gpt-5-image"
    assert llm.calls[0]["reference"] == ("image/png", PNG)
    assert (tmp_path / "deliverables" / "hero.png").exists()


def test_generate_image_rejects_bad_input(tmp_path):
    ws = WorkspaceTools(tmp_path)
    with pytest.raises(ToolError):
        generate_image(FakeImageLLM(), "", "", ws)
    with pytest.raises(ToolError):
        generate_image(FakeImageLLM(), "x", "", ws, aspect_ratio="7:3")
    with pytest.raises(ToolError):
        generate_image(FakeImageLLM(), "x", "", ws, reference_path="missing.png")
    with pytest.raises(ToolError):
        generate_image(FakeImageLLM(), "x", "../escape.png", ws)


def test_dispatch_routes_image_generate_and_bills(tmp_path):
    ws = WorkspaceTools(tmp_path)
    billed = []
    out = dispatch(ws, "image_generate", {"prompt": "p", "path": "deliverables/a.png"}, llm=FakeImageLLM(), spend=billed.append)
    assert out.startswith("wrote deliverables/a.png")
    assert len(billed) == 1
    with pytest.raises(ToolError):
        dispatch(ws, "image_generate", {"prompt": "p"})


def test_openrouter_client_parses_image_data_urls(monkeypatch):
    monkeypatch.setattr("rocky.llm.time.sleep", lambda s: None)
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        b64 = base64.b64encode(PNG).decode()
        return httpx.Response(
            200,
            json={
                "model": "google/gemini-3.1-flash-image",
                "choices": [
                    {
                        "message": {
                            "content": "done",
                            "images": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 1290, "cost": 0.04},
            },
        )

    c = OpenRouterClient("sk-test")
    c._http = httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)
    out = c.generate_image(prompt="a dog", model="google/gemini-3.1-flash-image", aspect_ratio="1:1")
    assert out.images == [("image/png", PNG)]
    assert out.text == "done" and out.usage.usd == 0.04
    assert seen["body"]["modalities"] == ["image", "text"]
    assert seen["body"]["image_config"] == {"aspect_ratio": "1:1"}
    assert seen["body"]["messages"][0]["content"] == "a dog"


def test_worker_tool_specs_include_image_generate():
    from rocky.tools import worker_tools

    names = [t["function"]["name"] for t in worker_tools(False)]
    assert "image_generate" in names
