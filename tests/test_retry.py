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


def _capture_body(monkeypatch):
    monkeypatch.setattr("rocky.llm.time.sleep", lambda s: None)
    seen = {}

    def handler(request):
        import json as _json

        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {}})

    return _client(handler), seen


def test_anthropic_models_get_cache_breakpoints_on_system_and_task(monkeypatch):
    client, seen = _capture_body(monkeypatch)
    msgs = [
        {"role": "system", "content": "You are Rocky."},
        {"role": "user", "content": "task json"},
        {"role": "assistant", "content": "thinking"},
        {"role": "user", "content": "follow-up"},
    ]
    client.complete(model="anthropic/claude-sonnet-5", messages=msgs)
    sent = seen["body"]["messages"]
    assert sent[0]["content"] == [{"type": "text", "text": "You are Rocky.", "cache_control": {"type": "ephemeral"}}]
    assert sent[1]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent[2]["content"] == "thinking"
    assert sent[3]["content"] == "follow-up"
    # the caller's list is untouched
    assert msgs[0]["content"] == "You are Rocky."


def test_other_families_are_sent_untouched(monkeypatch):
    client, seen = _capture_body(monkeypatch)
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"}]
    client.complete(model="google/gemini-3.8-flash", messages=msgs)
    assert seen["body"]["messages"] == msgs


def test_cache_control_lands_on_the_last_text_part_of_a_list_content():
    from rocky.llm import with_cache_control

    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "look"}, {"type": "image_url", "image_url": {"url": "x"}}]},
    ]
    out = with_cache_control("anthropic/claude-opus-5", msgs)
    # last part is an image → no breakpoint there, message left alone
    assert out[0] is msgs[0]
    msgs = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}, {"type": "text", "text": "look"}]}]
    out = with_cache_control("anthropic/claude-opus-5", msgs)
    assert out[0]["content"][1]["cache_control"] == {"type": "ephemeral"}


def test_catalog_fetch_is_retried(monkeypatch):
    monkeypatch.setattr("rocky.llm.time.sleep", lambda s: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("dns", request=request)
        if calls["n"] == 2:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json={"data": [{"id": "a"}]})

    assert _client(handler).list_models() == {"data": [{"id": "a"}]}
    assert calls["n"] == 3


def test_rejected_key_says_what_to_do_and_does_not_retry(monkeypatch):
    from rocky.llm import OpenRouterRefused

    monkeypatch.setattr("rocky.llm.time.sleep", lambda s: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(401, json={"error": {"message": "User not found."}})

    with pytest.raises(OpenRouterRefused) as info:
        _client(handler).list_models()
    assert "rocky doctor" in str(info.value) and "401" in str(info.value)
    assert "User not found" not in str(info.value)
    assert calls["n"] == 1
    with pytest.raises(httpx.HTTPStatusError) as info2:
        _client(handler).complete(model="m", messages=[{"role": "user", "content": "hi"}])
    assert "rocky doctor" in str(info2.value)


def test_no_credits_is_explained(monkeypatch):
    from rocky.llm import OpenRouterRefused

    def handler(request):
        return httpx.Response(402, text="Insufficient credits")

    with pytest.raises(OpenRouterRefused) as info:
        _client(handler).list_models()
    assert "credits" in str(info.value) and "openrouter.ai/credits" in str(info.value)


def test_offline_catalog_names_the_network_and_doctor(monkeypatch):
    from rocky.llm import OpenRouterUnreachable

    monkeypatch.setattr("rocky.llm.time.sleep", lambda s: None)

    def handler(request):
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(OpenRouterUnreachable) as info:
        _client(handler).list_models()
    assert "network" in str(info.value) and "rocky doctor" in str(info.value)
