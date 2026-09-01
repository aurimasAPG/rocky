import httpx
import pytest

from rocky.llm import OpenRouterClient


def _client(handler, retries=3):
    c = OpenRouterClient("sk-test")
    c._http = httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)
    return c


def test_retries_timeouts_then_succeeds(monkeypatch):
    monkeypatch.setattr("rocky.llm.time.sleep", lambda s: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ReadTimeout("boom", request=request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {}})

    out = _client(handler).complete(model="m", messages=[{"role": "user", "content": "hi"}])
    assert out.content == "ok"
    assert calls["n"] == 3


def test_retries_429_and_5xx(monkeypatch):
    monkeypatch.setattr("rocky.llm.time.sleep", lambda s: None)
    codes = iter([429, 503])
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        try:
            return httpx.Response(next(codes), text="busy", headers={"Retry-After": "0"})
        except StopIteration:
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {}})

    out = _client(handler).complete(model="m", messages=[{"role": "user", "content": "hi"}])
    assert out.content == "ok"
    assert calls["n"] == 3


def test_gives_up_after_max_attempts(monkeypatch):
    monkeypatch.setattr("rocky.llm.time.sleep", lambda s: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(RuntimeError) as exc:
        _client(handler).complete(model="m", messages=[{"role": "user", "content": "hi"}])
    assert "after 4 attempts" in str(exc.value)
    assert calls["n"] == 4


def test_client_errors_do_not_burn_retries(monkeypatch):
    monkeypatch.setattr("rocky.llm.time.sleep", lambda s: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(401, text="bad key")

    with pytest.raises(httpx.HTTPStatusError):
        _client(handler).complete(model="m", messages=[{"role": "user", "content": "hi"}])
    assert calls["n"] == 1


def test_empty_choices_is_retried(monkeypatch):
    monkeypatch.setattr("rocky.llm.time.sleep", lambda s: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={"choices": [], "usage": {}})
        if calls["n"] == 2:
            return httpx.Response(200, json={"error": {"code": 502, "message": "provider down"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {}})

    out = _client(handler).complete(model="m", messages=[{"role": "user", "content": "hi"}])
    assert out.content == "ok"
    assert calls["n"] == 3


def test_empty_choices_forever_gives_up(monkeypatch):
    monkeypatch.setattr("rocky.llm.time.sleep", lambda s: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={"choices": []})

    with pytest.raises(RuntimeError) as exc:
        _client(handler).complete(model="m", messages=[{"role": "user", "content": "hi"}])
    assert "empty choices" in str(exc.value)
    assert calls["n"] == 4
