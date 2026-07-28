import httpx
import respx

from nxre.client.nx_client import NxClient
from nxre.config import NxSystem
from nxre.engine.actions.nx_actions import register_nx_actions_factory
from nxre.engine.actions.registry import ActionRegistry
from nxre.engine.bus import Event

BASE = "https://nx.test:7001"


def _system():
    return NxSystem(name="TWG", base_url=BASE, username="svc", password="pw", verify_tls=False)


def _registry():
    reg = ActionRegistry()
    register_nx_actions_factory(reg, lambda: NxClient(_system()))
    return reg


def _login():
    respx.post(f"{BASE}/rest/v4/login/sessions").mock(
        return_value=httpx.Response(200, json={"token": "tok", "expiresInS": 600})
    )


# -- client endpoints -------------------------------------------------------
@respx.mock
async def test_get_devices():
    _login()
    respx.get(f"{BASE}/rest/v4/devices").mock(return_value=httpx.Response(200, json={"result": [
        {"id": "c1", "name": "Lobby"}, {"id": "c2", "name": "Door"},
    ]}))
    async with NxClient(_system()) as client:
        devices = await client.get_devices()
    assert [d["name"] for d in devices] == ["Lobby", "Door"]


@respx.mock
async def test_bookmark_action_posts_to_camera():
    _login()
    route = respx.post(f"{BASE}/rest/v4/devices/c1/bookmarks").mock(
        return_value=httpx.Response(200, json={"result": {"id": "bk1"}})
    )
    reg = _registry()
    await reg.dispatch("nx_bookmark", {"device_id": "c1", "name": "Intrusion", "duration_ms": 8000},
                       Event(type="motion", source="Lobby"), {})
    body = route.calls.last.request.content
    assert b'"name":"Intrusion"' in body
    assert b'"durationMs":8000' in body


@respx.mock
async def test_device_output_action_patches_io():
    _login()
    route = respx.patch(f"{BASE}/rest/v4/devices/c9/io").mock(
        return_value=httpx.Response(200, json={"result": {}})
    )
    reg = _registry()
    await reg.dispatch("nx_device_output", {"device_id": "c9", "auto_reset_ms": 1500},
                       Event(type="cameraInput", source="Gate"), {})
    body = route.calls.last.request.content
    assert b'"isActive":true' in body
    assert b'"autoResetTimeoutMs":1500' in body


@respx.mock
async def test_generic_event_action():
    _login()
    route = respx.post(f"{BASE}/rest/v4/events/generic").mock(
        return_value=httpx.Response(200, json={"result": {}})
    )
    reg = _registry()
    await reg.dispatch("nx_generic_event", {"caption": "Alert!"},
                       Event(type="motion", source="Lobby"), {})
    assert b'"caption":"Alert!"' in route.calls.last.request.content


# -- mobile push (bridge rule + generic event) ------------------------------
@respx.mock
async def test_mobile_notification_creates_bridge_then_fires():
    """First use with no bridge yet: create the pushNotification bridge rule, then fire."""
    _login()
    respx.get(f"{BASE}/rest/v4/events/rules").mock(
        return_value=httpx.Response(200, json={"result": []})
    )
    create_rule = respx.post(f"{BASE}/rest/v4/events/rules").mock(
        return_value=httpx.Response(200, json={"result": {"id": "r1"}})
    )
    generic = respx.post(f"{BASE}/rest/v4/events/generic").mock(
        return_value=httpx.Response(200, json={"result": {}})
    )
    reg = _registry()
    await reg.dispatch("nx_mobile_notification", {"title": "Person at door", "body": "Front Yard"},
                       Event(type="analyticsObject", source="Front Yard"), {})
    assert create_rule.called
    rbody = create_rule.calls.last.request.content
    assert b'"pushNotification"' in rbody
    assert b'"nxre.push"' in rbody          # bridge matches only our tagged events
    gbody = generic.calls.last.request.content
    assert b'"source":"nxre.push"' in gbody
    assert b'"caption":"Person at door"' in gbody


@respx.mock
async def test_mobile_notification_reuses_existing_bridge():
    """When the bridge already exists (matched by its comment) we must not create another."""
    from nxre.engine.actions.nx_actions import MOBILE_BRIDGE_COMMENT

    _login()
    respx.get(f"{BASE}/rest/v4/events/rules").mock(
        return_value=httpx.Response(200, json={"result": [
            {"id": "r1", "comment": MOBILE_BRIDGE_COMMENT},
        ]})
    )
    create_rule = respx.post(f"{BASE}/rest/v4/events/rules").mock(
        return_value=httpx.Response(200, json={"result": {}})
    )
    generic = respx.post(f"{BASE}/rest/v4/events/generic").mock(
        return_value=httpx.Response(200, json={"result": {}})
    )
    reg = _registry()
    await reg.dispatch("nx_mobile_notification", {"title": "Hi"},
                       Event(type="analyticsObject", source="Cam"), {})
    assert not create_rule.called
    assert generic.called
