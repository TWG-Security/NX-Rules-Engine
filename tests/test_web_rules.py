import json

import httpx
import respx
from fastapi.testclient import TestClient

from nxre.config import NxSystem, Settings

BASE = "https://127.0.0.1:7001"
RULES = f"{BASE}/rest/v4/events/rules"

# A minimal manifest in the real NX shape: type-id -> {displayName, flags, fields[]}.
EVENT_MANIFEST = {
    "analyticsSdkObjectDetected": {
        "displayName": "Analytics Object Detected", "flags": "instant",
        "fields": [
            {"type": "SourceCameraField", "fieldName": "deviceIds", "displayName": "Occurs At"},
            {"type": "TextField", "fieldName": "objectTypeId", "displayName": "Of Type"},
            {"type": "TextField", "fieldName": "attributes", "displayName": "Attributes"},
        ],
    },
    "motion": {"displayName": "Motion", "flags": "prolonged", "fields": [
        {"type": "SourceCameraField", "fieldName": "deviceIds", "displayName": "At Cameras"},
    ]},
}
ACTION_MANIFEST = {
    "http": {"displayName": "HTTP(S) Request", "flags": "instant", "fields": [
        {"type": "TextField", "fieldName": "url", "displayName": "URL"},
        {"type": "TextField", "fieldName": "method", "displayName": "Method"},
        {"type": "TextField", "fieldName": "content", "displayName": "Content"},
    ]},
    "writeToLog": {"displayName": "Write to Log", "flags": "instant", "fields": []},
}


def _client(tmp_path, monkeypatch, *, writable=True):
    monkeypatch.setenv("NXRE_SESSION_FILE", str(tmp_path / "session.json"))
    from nxre.service.app import create_app

    settings = Settings(
        default_system="TWG",
        systems={"TWG": NxSystem(name="TWG", base_url=BASE, verify_tls=False, writable=writable)},
    )
    return TestClient(create_app(settings), follow_redirects=False)


def _login(client):
    respx.post(f"{BASE}/rest/v4/login/sessions").mock(
        return_value=httpx.Response(200, json={"token": "tok", "expiresInS": 600})
    )
    client.post("/login", data={"username": "admin", "password": "pw"})


def _mock_manifest_and_devices():
    respx.get(f"{BASE}/rest/v4/events/manifest/events").mock(
        return_value=httpx.Response(200, json={"result": EVENT_MANIFEST})
    )
    respx.get(f"{BASE}/rest/v4/events/manifest/actions").mock(
        return_value=httpx.Response(200, json={"result": ACTION_MANIFEST})
    )
    respx.get(f"{BASE}/rest/v4/devices").mock(return_value=httpx.Response(200, json={"result": [
        {"id": "cam-1", "name": "Lobby Cam"}, {"id": "cam-2", "name": "Front Door"},
    ]}))


def test_rules_requires_login(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    resp = client.get("/rules")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


@respx.mock
def test_rules_list_shows_rules(tmp_path, monkeypatch):
    _login_route = respx.post(f"{BASE}/rest/v4/login/sessions").mock(
        return_value=httpx.Response(200, json={"token": "tok", "expiresInS": 600}))
    respx.get(RULES).mock(return_value=httpx.Response(200, json={"result": [
        {"id": "r1", "enabled": True, "event": {"type": "motion"},
         "action": {"type": "http"}, "comment": "bear webhook"},
    ]}))
    client = _client(tmp_path, monkeypatch)
    client.post("/login", data={"username": "admin", "password": "pw"})
    resp = client.get("/rules")
    assert resp.status_code == 200
    assert "bear webhook" in resp.text
    assert "+ New rule" in resp.text


@respx.mock
def test_builder_lists_manifest_types_and_cameras(tmp_path, monkeypatch):
    _login(client := _client(tmp_path, monkeypatch))
    _mock_manifest_and_devices()
    resp = client.get("/rules/new")
    assert resp.status_code == 200
    # event/action display names + camera names are injected for the dropdowns
    assert "Analytics Object Detected" in resp.text
    assert "HTTP(S) Request" in resp.text
    assert "Lobby Cam" in resp.text
    # device fields are known to render as camera pickers
    assert "deviceIds" in resp.text


@respx.mock
def test_create_native_rule_posts_to_nx(tmp_path, monkeypatch):
    _login(client := _client(tmp_path, monkeypatch))
    _mock_manifest_and_devices()
    created = respx.post(RULES).mock(return_value=httpx.Response(200, json={"result": {"id": "new"}}))

    payload = {
        "comment": "N8n bear system", "enabled": True,
        "event": {"type": "analyticsSdkObjectDetected", "deviceIds": ["cam-1", "cam-2"],
                  "objectTypeId": "Animal", "attributes": '"Species"="Bear"'},
        "action": {"type": "http", "url": "http://10.0.0.230:5678/webhook/nx-verify",
                   "method": "POST"},
    }
    resp = client.post("/rules/create", data={"payload": json.dumps(payload)})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/rules?notice=Rule+created"
    body = created.calls.last.request.content
    assert b"analyticsSdkObjectDetected" in body
    assert b"cam-1" in body and b"Bear" in body
    assert b"nx-verify" in body


@respx.mock
def test_create_requires_event_and_action(tmp_path, monkeypatch):
    _login(client := _client(tmp_path, monkeypatch))
    _mock_manifest_and_devices()
    posted = respx.post(RULES)
    payload = {"comment": "x", "enabled": True, "event": {}, "action": {"type": "http"}}
    resp = client.post("/rules/create", data={"payload": json.dumps(payload)})
    assert resp.status_code == 400
    assert "Pick both an event and an action" in resp.text
    assert not posted.called


@respx.mock
def test_edit_prefills_from_live_rule(tmp_path, monkeypatch):
    _login(client := _client(tmp_path, monkeypatch))
    _mock_manifest_and_devices()
    respx.get(f"{RULES}/r1").mock(return_value=httpx.Response(200, json={"result": {
        "id": "r1", "enabled": True, "event": {"type": "motion", "deviceIds": ["cam-1"]},
        "action": {"type": "http", "url": "http://x/y"}, "comment": "keep me",
    }}))
    resp = client.get("/rules/r1/edit")
    assert resp.status_code == 200
    assert "keep me" in resp.text and "http://x/y" in resp.text  # embedded INITIAL


@respx.mock
def test_toggle_and_delete(tmp_path, monkeypatch):
    _login(client := _client(tmp_path, monkeypatch))
    respx.get(f"{RULES}/r1").mock(return_value=httpx.Response(200, json={"result": {
        "id": "r1", "enabled": True, "event": {"type": "motion"}, "action": {"type": "http"},
    }}))
    patched = respx.patch(f"{RULES}/r1").mock(return_value=httpx.Response(200, json={"result": {}}))
    deleted = respx.delete(f"{RULES}/r1").mock(return_value=httpx.Response(200))

    assert client.post("/rules/r1/toggle").status_code == 303
    assert b'"enabled":false' in patched.calls.last.request.content
    assert client.post("/rules/r1/delete").status_code == 303
    assert deleted.called


@respx.mock
def test_readonly_system_blocks_create(tmp_path, monkeypatch):
    _login(client := _client(tmp_path, monkeypatch, writable=False))
    posted = respx.post(RULES)
    payload = {"event": {"type": "motion"}, "action": {"type": "http"}}
    resp = client.post("/rules/create", data={"payload": json.dumps(payload)})
    assert resp.status_code == 303
    assert "read-only" in resp.headers["location"]
    assert not posted.called
