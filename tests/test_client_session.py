import time

import httpx
import pytest
import respx

from nxre.client.auth import AuthError, Token
from nxre.client.nx_client import NxClient
from nxre.config import NxSystem

BASE = "https://nx.test:7001"


def _system(**over):
    base = {"name": "TWG", "base_url": BASE, "username": "", "password": "", "verify_tls": False}
    base.update(over)
    return NxSystem(**base)


@respx.mock
async def test_injected_token_is_used_without_login():
    """A cached user session token means no /login call and no password needed."""
    login = respx.post(f"{BASE}/rest/v4/login/sessions")
    route = respx.get(f"{BASE}/rest/v4/events/rules").mock(
        return_value=httpx.Response(200, json={"result": []})
    )
    token = Token(value="session-tok", expires_at=time.time() + 600)
    async with NxClient(_system(), token=token) as client:
        await client.get_rules()

    assert not login.called
    assert route.calls.last.request.headers["authorization"] == "Bearer session-tok"


@respx.mock
async def test_expired_token_without_password_raises_login_hint():
    respx.get(f"{BASE}/rest/v4/events/rules").mock(
        return_value=httpx.Response(200, json={"result": []})
    )
    stale = Token(value="old", expires_at=time.time() - 10)
    async with NxClient(_system(), token=stale) as client:
        with pytest.raises(AuthError, match="nxre login"):
            await client.get_rules()


@respx.mock
async def test_expired_token_falls_back_to_service_password():
    """With a service-account password configured, an expired token self-renews."""
    login = respx.post(f"{BASE}/rest/v4/login/sessions").mock(
        return_value=httpx.Response(200, json={"token": "fresh", "expiresInS": 600})
    )
    route = respx.get(f"{BASE}/rest/v4/events/rules").mock(
        return_value=httpx.Response(200, json={"result": []})
    )
    stale = Token(value="old", expires_at=time.time() - 10)
    async with NxClient(_system(username="svc", password="pw"), token=stale) as client:
        await client.get_rules()

    assert login.called
    assert route.calls.last.request.headers["authorization"] == "Bearer fresh"
