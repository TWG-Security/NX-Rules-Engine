import httpx
import respx
from fastapi.testclient import TestClient

from nxre.config import NxSystem, Settings

BASE = "https://127.0.0.1:7001"
RULES = f"{BASE}/rest/v4/events/rules"


def _client(tmp_path, monkeypatch, *, writable=True):
    monkeypatch.setenv("NXRE_SESSION_FILE", str(tmp_path / "session.json"))
    from nxre.service.app import create_app

    settings = Settings(
        default_system="TWG",
        systems={"TWG": NxSystem(name="TWG", base_url=BASE, verify_tls=False, writable=writable)},
    )
    return TestClient(create_app(settings), follow_redirects=False)


def _mock_login():
    respx.post(f"{BASE}/rest/v4/login/sessions").mock(
        return_value=httpx.Response(200, json={"token": "tok", "expiresInS": 600})
    )


def _mock_manifest():
    respx.get(f"{BASE}/rest/v4/events/manifest/events").mock(
        return_value=httpx.Response(200, json={"result": {"motion": {}, "deviceDisconnected": {}}})
    )
    respx.get(f"{BASE}/rest/v4/events/manifest/actions").mock(
        return_value=httpx.Response(200, json={"result": {"writeToLog": {}, "http": {}}})
    )


def _login(client):
    client.post("/login", data={"username": "admin", "password": "pw"})


def test_rules_requires_login(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    resp = client.get("/rules")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


@respx.mock
def test_rules_list_shows_rules(tmp_path, monkeypatch):
    _mock_login()
    respx.get(RULES).mock(return_value=httpx.Response(200, json={"result": [
        {"id": "r1", "enabled": True, "event": {"type": "motion"},
         "action": {"type": "writeToLog"}, "comment": "log motion"},
        {"id": "r2", "enabled": False, "event": {"type": "deviceDisconnected"},
         "action": {"type": "http"}, "comment": "ping webhook"},
    ]}))
    client = _client(tmp_path, monkeypatch)
    _login(client)

    resp = client.get("/rules")
    assert resp.status_code == 200
    assert "log motion" in resp.text and "ping webhook" in resp.text
    assert "motion" in resp.text and "deviceDisconnected" in resp.text
    assert "+ New rule" in resp.text  # writable → create affordance shown


@respx.mock
def test_new_rule_form_lists_manifest_types(tmp_path, monkeypatch):
    _mock_login()
    _mock_manifest()
    client = _client(tmp_path, monkeypatch)
    _login(client)

    resp = client.get("/rules/new")
    assert resp.status_code == 200
    assert '<option value="motion"' in resp.text
    assert '<option value="writeToLog"' in resp.text


@respx.mock
def test_create_rule_posts_to_nx(tmp_path, monkeypatch):
    _mock_login()
    _mock_manifest()
    created = respx.post(RULES).mock(return_value=httpx.Response(200, json={"result": {"id": "new"}}))
    client = _client(tmp_path, monkeypatch)
    _login(client)

    resp = client.post("/rules/create", data={
        "comment": "log motion", "enabled": "on",
        "event_type": "motion", "event_json": "{}",
        "action_type": "writeToLog", "action_json": '{"intervalS": 60}',
    })
    assert resp.status_code == 303
    assert resp.headers["location"] == "/rules?notice=Rule+created"
    body = created.calls.last.request.content
    assert b"motion" in body and b"writeToLog" in body


@respx.mock
def test_create_rule_bad_json_shows_error(tmp_path, monkeypatch):
    _mock_login()
    _mock_manifest()
    posted = respx.post(RULES)
    client = _client(tmp_path, monkeypatch)
    _login(client)

    resp = client.post("/rules/create", data={
        "comment": "x", "event_type": "motion", "event_json": "{not json",
        "action_type": "writeToLog", "action_json": "{}",
    })
    assert resp.status_code == 400
    assert "Invalid JSON" in resp.text
    assert not posted.called  # nothing pushed to NX


@respx.mock
def test_edit_prefills_existing_rule(tmp_path, monkeypatch):
    _mock_login()
    _mock_manifest()
    respx.get(f"{RULES}/r1").mock(return_value=httpx.Response(200, json={"result": {
        "id": "r1", "enabled": True, "event": {"type": "motion"},
        "action": {"type": "writeToLog", "intervalS": 30}, "comment": "keep me",
    }}))
    client = _client(tmp_path, monkeypatch)
    _login(client)

    resp = client.get("/rules/r1/edit")
    assert resp.status_code == 200
    assert "keep me" in resp.text
    assert '<option value="motion" selected' in resp.text
    assert "intervalS" in resp.text


@respx.mock
def test_toggle_rule(tmp_path, monkeypatch):
    _mock_login()
    respx.get(f"{RULES}/r1").mock(return_value=httpx.Response(200, json={"result": {
        "id": "r1", "enabled": True, "event": {"type": "motion"}, "action": {"type": "writeToLog"},
    }}))
    patched = respx.patch(f"{RULES}/r1").mock(return_value=httpx.Response(200, json={"result": {}}))
    client = _client(tmp_path, monkeypatch)
    _login(client)

    resp = client.post("/rules/r1/toggle")
    assert resp.status_code == 303
    assert "disabled" in resp.headers["location"]
    assert b'"enabled":false' in patched.calls.last.request.content


@respx.mock
def test_delete_rule(tmp_path, monkeypatch):
    _mock_login()
    deleted = respx.delete(f"{RULES}/r1").mock(return_value=httpx.Response(200))
    client = _client(tmp_path, monkeypatch)
    _login(client)

    resp = client.post("/rules/r1/delete")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/rules?notice=Rule+deleted"
    assert deleted.called


@respx.mock
def test_readonly_system_blocks_writes(tmp_path, monkeypatch):
    _mock_login()
    posted = respx.post(RULES)
    client = _client(tmp_path, monkeypatch, writable=False)
    _login(client)

    resp = client.post("/rules/create", data={
        "comment": "x", "event_type": "motion", "event_json": "{}",
        "action_type": "writeToLog", "action_json": "{}",
    })
    assert resp.status_code == 303
    assert "read-only" in resp.headers["location"]
    assert not posted.called
