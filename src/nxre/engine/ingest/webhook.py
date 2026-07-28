"""Webhook ingestion — the primary, officially-recommended real-time path.

We provision a native NX rule whose *Do HTTP Request* action POSTs to this endpoint the
instant an event matches. NX pushes to us (sub-second, no polling). This module turns an
incoming HTTP body into an :class:`Event` and publishes it to the bus.

Kept transport-agnostic: :func:`handle_payload` does the real work and is trivially unit-
testable; the FastAPI route in ``service/app.py`` is a thin wrapper over it.
"""

from __future__ import annotations

from typing import Any

from ..bus import Event, EventBus


async def handle_payload(bus: EventBus, payload: dict[str, Any]) -> Event:
    """Normalize an NX webhook payload into an Event and publish it. Returns the Event."""
    event = Event.from_webhook(payload)
    await bus.publish(event)
    return event
