import json
from pathlib import Path

import httpx
import respx
from fastapi.testclient import TestClient

from nxre import autos
from nxre.config import NxSystem, Settings
from nxre.models.automation import Automation

BASE = "https://127.0.0.1:7001"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        default_system="TWG",
        automations_dir=tmp_path / "automations",
        systems={"TWG": NxSystem(name="TWG", base_url=BASE, verify_tls=False, writable=True)},
    )


# -- persistence ------------------------------------------------------------
def test_save_load_delete_roundtrip(tmp_path):
    s = _settings(tmp_path)
    auto = Automation.from_yaml_obj({
        "alias": "Lobby motion → log",
        "trigger": [{"platform": "nx_event", "event_type": "motion"}],
        "action": [{"kind": "log", "message": "hi"}],
    })
    path = autos.save(s, "TWG", auto)
    assert path.exists() and auto.id == "lobby-motion-log"

    loaded = autos.load_all(s, "TWG")
    assert len(loaded) == 1 and loaded[0].alias == "Lobby motion → log"
    assert autos.get(s, "TWG", "lobby-motion-log") is not None

    assert autos.set_enabled(s, "TWG", "lobby-motion-log", False) is True
    assert autos.get(s, "TWG", "lobby-motion-log").enabled is False

    assert autos.delete(s, "TWG", "lobby-motion-log") is True
    assert autos.load_all(s, "TWG") == []


# -- web builder ------------------------------------------------------------
def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("NXRE_SESSION_FILE", str(tmp_path / "session.json"))
    from nxre.service.app import create_app

    return TestClient(create_app(_settings(tmp_path)), follow_redirects=False)


def _login(client):
    respx.post(f"{BASE}/rest/v4/login/sessions").mock(
        return_value=httpx.Response(200, json={"token": "tok", "expiresInS": 600})
    )
    client.post("/login", data={"username": "admin", "password": "pw"})


def _mock_builder_data():
    """The builder pages pull event types + cameras live from NX."""
    respx.get(f"{BASE}/rest/v4/events/manifest/events").mock(
        return_value=httpx.Response(200, json={"result": {"motion": {}, "deviceDisconnected": {}}})
    )
    respx.get(f"{BASE}/rest/v4/events/manifest/actions").mock(
        return_value=httpx.Response(200, json={"result": {}})
    )
    respx.get(f"{BASE}/rest/v4/devices").mock(return_value=httpx.Response(200, json={"result": [
        {"id": "cam-1", "name": "Lobby Cam"}, {"id": "cam-2", "name": "Front Door"},
    ]}))


def test_automations_requires_login(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    assert client.get("/automations").status_code == 303


@respx.mock
def test_builder_page_renders_sections(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _login(client)
    _mock_builder_data()
    resp = client.get("/automations/new")
    assert resp.status_code == 200
    for label in ("When", "And if", "Then do", "Add trigger", "Add action"):
        assert label in resp.text
    # Live cameras + friendly event labels are injected for the dropdowns.
    assert "Lobby Cam" in resp.text
    assert "Motion is detected" in resp.text


@respx.mock
def test_save_creates_automation_and_lists_it(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _login(client)
    payload = {
        "alias": "Alert on lobby offline",
        "enabled": True, "mode": "single",
        "trigger": [{"platform": "nx_event", "event_type": "deviceDisconnected",
                     "source": "Lobby"}],
        "condition": [{"condition": "caption_contains", "value": "offline"}],
        "action": [{"kind": "http", "method": "POST", "url": "https://hook.example/x"}],
    }
    resp = client.post("/automations/save", data={"payload": json.dumps(payload)})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/automations?notice=Automation+saved"

    # It's persisted...
    assert autos.get(_settings(tmp_path), "TWG", "alert-on-lobby-offline") is not None
    # ...and shows on the list.
    listing = client.get("/automations")
    assert "Alert on lobby offline" in listing.text
    assert "deviceDisconnected" in listing.text and "http" in listing.text


@respx.mock
def test_save_rejects_missing_trigger(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _login(client)
    _mock_builder_data()  # error path re-renders the builder, which needs live data
    payload = {"alias": "no trigger", "trigger": [],
               "action": [{"kind": "log"}]}
    resp = client.post("/automations/save", data={"payload": json.dumps(payload)})
    assert resp.status_code == 400
    assert "at least one trigger" in resp.text


@respx.mock
def test_edit_prefills_and_toggle_delete(tmp_path, monkeypatch):
    s = _settings(tmp_path)
    autos.save(s, "TWG", Automation.from_yaml_obj({
        "alias": "keep me",
        "trigger": [{"platform": "nx_event", "event_type": "motion"}],
        "action": [{"kind": "log", "message": "hello"}],
    }))
    client = _client(tmp_path, monkeypatch)
    _login(client)
    _mock_builder_data()

    edit = client.get("/automations/keep-me/edit")
    assert edit.status_code == 200
    assert "keep me" in edit.text and "hello" in edit.text  # embedded INITIAL

    assert client.post("/automations/keep-me/toggle").status_code == 303
    assert autos.get(s, "TWG", "keep-me").enabled is False

    assert client.post("/automations/keep-me/delete").status_code == 303
    assert autos.get(s, "TWG", "keep-me") is None
