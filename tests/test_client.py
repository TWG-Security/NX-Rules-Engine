import httpx
import respx

from nxre.client.nx_client import NxClient
from nxre.config import NxSystem

BASE = "https://nx.test:7001"


def _system():
    return NxSystem(name="TWG", base_url=BASE, username="svc", password="pw", verify_tls=False)


@respx.mock
async def test_login_and_get_rules():
    respx.post(f"{BASE}/rest/v4/login/sessions").mock(
        return_value=httpx.Response(200, json={"token": "tok", "expiresInS": 600})
    )
    respx.get(f"{BASE}/rest/v4/events/rules").mock(
        return_value=httpx.Response(200, json={"result": [{"id": "r1", "event": {}, "action": {}}]})
    )
    async with NxClient(_system()) as client:
        rules = await client.get_rules()
    assert rules[0]["id"] == "r1"


@respx.mock
async def test_bearer_header_sent():
    respx.post(f"{BASE}/rest/v4/login/sessions").mock(
        return_value=httpx.Response(200, json={"token": "tok", "expiresInS": 600})
    )
    route = respx.get(f"{BASE}/rest/v4/events/rules").mock(
        return_value=httpx.Response(200, json={"result": []})
    )
    async with NxClient(_system()) as client:
        await client.get_rules()
    assert route.calls.last.request.headers["authorization"] == "Bearer tok"


@respx.mock
async def test_401_triggers_relogin_and_retry():
    login = respx.post(f"{BASE}/rest/v4/login/sessions").mock(
        return_value=httpx.Response(200, json={"token": "tok", "expiresInS": 600})
    )
    respx.get(f"{BASE}/rest/v4/events/rules").mock(
        side_effect=[
            httpx.Response(401, json={"error": "expired"}),
            httpx.Response(200, json={"result": [{"id": "ok", "event": {}, "action": {}}]}),
        ]
    )
    async with NxClient(_system()) as client:
        rules = await client.get_rules()
    assert rules[0]["id"] == "ok"
    assert login.call_count == 2  # initial + forced refresh


@respx.mock
async def test_create_rule_posts_body():
    respx.post(f"{BASE}/rest/v4/login/sessions").mock(
        return_value=httpx.Response(200, json={"token": "tok", "expiresInS": 600})
    )
    route = respx.post(f"{BASE}/rest/v4/events/rules").mock(
        return_value=httpx.Response(200, json={"result": {"id": "new"}})
    )
    async with NxClient(_system()) as client:
        created = await client.create_rule({"event": {"type": "motion"}, "action": {"type": "writeToLog"}})
    assert created["id"] == "new"
    assert route.calls.last.request.method == "POST"
