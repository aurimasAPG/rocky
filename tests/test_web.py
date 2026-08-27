import pytest
from fastapi.testclient import TestClient

from rocky.cli import main
from rocky.webapp import ServeRefused, app, ensure_serve_allowed, serve


def test_home_page_serves():
    client = TestClient(app)
    res = client.get("/")
    assert res.status_code == 200
    assert "Cast a goal" in res.text
    assert "id=\"usd\"" in res.text
    assert "id=\"eur\"" in res.text
    assert "id=\"tok-total\"" in res.text


def test_css_serves():
    client = TestClient(app)
    res = client.get("/static/styles.css")
    assert res.status_code == 200
    assert "--copper" in res.text


def test_ensure_serve_allowed_refuses_non_loopback_without_token():
    with pytest.raises(ServeRefused, match="ROCKY_TOKEN"):
        ensure_serve_allowed("0.0.0.0", "")
    with pytest.raises(ServeRefused, match="ROCKY_TOKEN"):
        ensure_serve_allowed("192.168.1.10", "   ")
    with pytest.raises(ServeRefused, match="ROCKY_TOKEN"):
        ensure_serve_allowed("::", "")


def test_ensure_serve_allowed_allows_loopback_without_token():
    ensure_serve_allowed("127.0.0.1", "")
    ensure_serve_allowed("localhost", "")
    ensure_serve_allowed("::1", "")


def test_ensure_serve_allowed_allows_non_loopback_with_token():
    ensure_serve_allowed("0.0.0.0", "secret")


def test_serve_refuses_non_loopback_without_token_before_bind(monkeypatch):
    monkeypatch.setattr("rocky.webapp.serve_token", lambda: "")
    import uvicorn

    bound: list[object] = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: bound.append((a, k)))
    with pytest.raises(ServeRefused, match="ROCKY_TOKEN"):
        serve(host="0.0.0.0", port=8787)
    assert bound == []


def test_serve_allows_loopback_without_token(monkeypatch):
    monkeypatch.setattr("rocky.webapp.serve_token", lambda: "")
    import uvicorn

    bound: list[tuple[str, int]] = []

    def fake_run(app_obj, host="127.0.0.1", port=8787, **kwargs):
        bound.append((host, port))

    monkeypatch.setattr(uvicorn, "run", fake_run)
    serve(host="127.0.0.1", port=8787)
    assert bound == [("127.0.0.1", 8787)]


def test_cli_serve_refuses_public_host_without_token(monkeypatch, capsys):
    monkeypatch.setattr("rocky.webapp.serve_token", lambda: "")
    import uvicorn

    bound: list[object] = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: bound.append(True))
    assert main(["serve", "--host", "0.0.0.0"]) == 2
    captured = capsys.readouterr()
    assert "ROCKY_TOKEN" in captured.out + captured.err
    assert bound == []
