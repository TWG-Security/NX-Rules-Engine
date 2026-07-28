import httpx
import respx
from fastapi.testclient import TestClient

from nxre.config import NxSystem, Settings

BASE = "https://127.0.0.1:7001"


def _settings(tmp_path, monkeypatch):
    # Isolate the cached token per test.
    monkeypatch.setenv("NXRE_SESSION_FILE", str(tmp_path / "session.json"))
    return Settings(
        default_system="TWG",
        systems={"TWG": NxSystem(name="TWG", base_url=BASE, verify_tls=False, writable=True)},
    )


def _client(tmp_path, monkeypatch):
    from nxre.service.app import create_app

    return TestClient(create_app(_settings(tmp_path, monkeypatch)), follow_redirects=False)


def test_root_shows_login_when_unauthenticated(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'action="/login"' in resp.text
    assert "Sign in" in resp.text
    assert client.get("/health").json()["authenticated"] is False


@respx.mock
def test_login_success_caches_token_and_shows_status(tmp_path, monkeypatch):
    respx.post(f"{BASE}/rest/v4/login/sessions").mock(
        return_value=httpx.Response(200, json={"token": "web-tok", "expiresInS": 600})
    )
    client = _client(tmp_path, monkeypatch)

    resp = client.post("/login", data={"username": "msupczenski", "password": "pw"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"

    # Now the root page reflects the authenticated session.
    status = client.get("/")
    assert "Connected" in status.text
    assert "msupczenski" in status.text

    health = client.get("/health").json()
    assert health["authenticated"] is True
    assert health["authenticated_user"] == "msupczenski"


@respx.mock
def test_login_bad_credentials_shows_error(tmp_path, monkeypatch):
    respx.post(f"{BASE}/rest/v4/login/sessions").mock(
        return_value=httpx.Response(401, text="nope")
    )
    client = _client(tmp_path, monkeypatch)
    resp = client.post("/login", data={"username": "bob", "password": "wrong"})
    assert resp.status_code == 401
    assert "Login failed" in resp.text
    assert client.get("/health").json()["authenticated"] is False


@respx.mock
def test_login_unreachable_server_shows_error(tmp_path, monkeypatch):
    respx.post(f"{BASE}/rest/v4/login/sessions").mock(
        side_effect=httpx.ConnectError("refused")
    )
    client = _client(tmp_path, monkeypatch)
    resp = client.post("/login", data={"username": "u", "password": "p"})
    assert resp.status_code == 401
    assert "Could not reach NX" in resp.text


def test_login_missing_fields(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    resp = client.post("/login", data={"username": "", "password": ""})
    assert resp.status_code == 400
    assert "Enter both" in resp.text


@respx.mock
def test_logout_clears_session(tmp_path, monkeypatch):
    respx.post(f"{BASE}/rest/v4/login/sessions").mock(
        return_value=httpx.Response(200, json={"token": "t", "expiresInS": 600})
    )
    client = _client(tmp_path, monkeypatch)
    client.post("/login", data={"username": "u", "password": "p"})
    assert client.get("/health").json()["authenticated"] is True

    resp = client.post("/logout")
    assert resp.status_code == 303
    assert client.get("/health").json()["authenticated"] is False
