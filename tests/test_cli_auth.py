import httpx
import respx
from typer.testing import CliRunner

from nxre.cli import app
from nxre.session import SessionStore

BASE = "https://nx.test:7001"
runner = CliRunner()

CONFIG = f"""
default_system: TWG
systems:
  TWG:
    base_url: {BASE}
    username: ""
    verify_tls: false
    writable: true
"""


def _env(tmp_path, monkeypatch):
    cfg = tmp_path / "nxre.config.yaml"
    cfg.write_text(CONFIG, encoding="utf-8")
    session = tmp_path / "session.json"
    monkeypatch.setenv("NXRE_CONFIG", str(cfg))
    monkeypatch.setenv("NXRE_SESSION_FILE", str(session))
    return session


@respx.mock
def test_login_prompts_and_caches_token(tmp_path, monkeypatch):
    session = _env(tmp_path, monkeypatch)
    login = respx.post(f"{BASE}/rest/v4/login/sessions").mock(
        return_value=httpx.Response(200, json={"token": "user-tok", "expiresInS": 600})
    )

    result = runner.invoke(app, ["login"], input="msupczenski\nhunter2\n")
    assert result.exit_code == 0, result.output
    assert "Logged in" in result.output

    # The username+password were sent to NX...
    sent = login.calls.last.request
    assert b"msupczenski" in sent.content
    # ...but only the token landed on disk.
    store = SessionStore(session)
    assert store.load("TWG").value == "user-tok"
    assert store.username("TWG") == "msupczenski"
    assert "hunter2" not in session.read_text(encoding="utf-8")


@respx.mock
def test_login_username_flag_skips_prompt(tmp_path, monkeypatch):
    session = _env(tmp_path, monkeypatch)
    respx.post(f"{BASE}/rest/v4/login/sessions").mock(
        return_value=httpx.Response(200, json={"token": "t", "expiresInS": 600})
    )
    result = runner.invoke(app, ["login", "-u", "alice"], input="pw\n")
    assert result.exit_code == 0, result.output
    assert SessionStore(session).username("TWG") == "alice"


@respx.mock
def test_login_bad_credentials_exit_1(tmp_path, monkeypatch):
    session = _env(tmp_path, monkeypatch)
    respx.post(f"{BASE}/rest/v4/login/sessions").mock(
        return_value=httpx.Response(401, text="bad creds")
    )
    result = runner.invoke(app, ["login"], input="bob\nwrong\n")
    assert result.exit_code == 1
    assert SessionStore(session).load("TWG") is None


def test_logout_clears_cached_session(tmp_path, monkeypatch):
    session = _env(tmp_path, monkeypatch)
    from nxre.client.auth import Token

    SessionStore(session).save("TWG", Token(value="t", expires_at=9_999_999_999), "u")

    result = runner.invoke(app, ["logout"])
    assert result.exit_code == 0
    assert "Logged out" in result.output
    assert SessionStore(session).load("TWG") is None


def test_logout_when_nothing_cached(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    result = runner.invoke(app, ["logout"])
    assert result.exit_code == 0
    assert "No cached session" in result.output
