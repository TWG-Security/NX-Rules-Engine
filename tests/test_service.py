from fastapi.testclient import TestClient

from nxre.config import Settings
from nxre.engine.bus import Event, EventBus
from nxre.engine.ingest.webhook import handle_payload
from nxre.service.app import create_app


def test_event_from_webhook_parsing():
    e = Event.from_webhook({"event": "motion", "source": "Cam1", "caption": "hi"})
    assert e.type == "motion"
    assert e.source == "Cam1"
    assert e.platform == "nx_event"


async def test_handle_payload_publishes():
    bus = EventBus()
    event = await handle_payload(bus, {"event": "generic", "caption": "test"})
    assert event.type == "generic"
    assert list(bus.recent)[-1] is event


def test_service_health_and_webhook_roundtrip():
    app = create_app(Settings(default_system="TWG"))
    client = TestClient(app)

    assert client.get("/health").json()["status"] == "ok"

    resp = client.post("/webhook/nx", json={"event": "deviceDisconnected", "source": "Cam9"})
    assert resp.json() == {"ok": True, "type": "deviceDisconnected"}

    recent = client.get("/events/recent").json()
    assert recent[-1]["type"] == "deviceDisconnected"
    assert recent[-1]["source"] == "Cam9"


def test_webhook_handles_non_json_body():
    app = create_app(Settings())
    client = TestClient(app)
    resp = client.post("/webhook/nx", content=b"plain text", headers={"content-type": "text/plain"})
    assert resp.status_code == 200
