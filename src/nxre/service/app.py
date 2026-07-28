"""FastAPI companion service: the webhook receiver + a small inspection API.

``nxre serve`` runs this. It receives NX "Do HTTP Request" pushes at ``/webhook/nx``,
publishes them to the engine's event bus, and exposes ``/events/recent`` so you can
watch the live loop working. The control API (edit/create rules over HTTP) and the web
UI land in later phases; the foundation is intentionally here now.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from ruamel.yaml import YAML

from ..config import Settings
from ..engine.automations import AutomationEngine
from ..engine.bus import EventBus
from ..engine.ingest.webhook import handle_payload
from ..models.automation import Automation

log = logging.getLogger("nxre.service")
_yaml = YAML(typ="safe")


def load_automations(settings: Settings, system: str) -> list[Automation]:
    """Load HA-style automations for a system from ``<automations_dir>/<system>/*.yaml``."""
    directory = settings.automations_dir / system
    if not directory.exists():
        return []
    automations: list[Automation] = []
    for path in sorted(directory.glob("*.yaml")):
        with open(path, encoding="utf-8") as fh:
            data = _yaml.load(fh)
        if not data:
            continue
        # a file may hold a single automation or a list of them
        items = data if isinstance(data, list) else [data]
        automations.extend(Automation.from_yaml_obj(item) for item in items)
    return automations


def create_app(
    settings: Settings,
    system: str | None = None,
    *,
    authenticated_user: str | None = None,
) -> FastAPI:
    system = system or settings.default_system
    app = FastAPI(title="nxre", version="0.1.0")

    bus = EventBus()
    automations = load_automations(settings, system)
    engine = AutomationEngine(automations)
    engine.attach(bus)

    app.state.bus = bus
    app.state.engine = engine
    app.state.system = system
    app.state.authenticated_user = authenticated_user

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "system": system,
            "authenticated_user": authenticated_user,
            "automations": len(automations),
            "events_seen": len(bus.recent),
        }

    @app.post("/webhook/nx")
    async def webhook_nx(request: Request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001 — NX may send form/plain; fall back gracefully
            payload = {"description": (await request.body()).decode("utf-8", "replace")}
        event = await handle_payload(bus, payload if isinstance(payload, dict) else {"raw": payload})
        log.info("webhook event received: %s / %s", event.type, event.source)
        return {"ok": True, "type": event.type}

    @app.get("/events/recent")
    async def events_recent(limit: int = 50) -> list[dict[str, Any]]:
        events = list(bus.recent)[-limit:]
        return [
            {
                "type": e.type,
                "source": e.source,
                "caption": e.caption,
                "description": e.description,
                "received_at": e.received_at,
            }
            for e in events
        ]

    return app
