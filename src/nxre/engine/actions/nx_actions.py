"""NX-native action handlers for the automation engine.

These let an automation *do something back to NX*: raise a Generic Event, fire a Soft
Trigger, etc. They wrap the same :class:`NxClient` used elsewhere, so auth/refresh is
shared. Registered into an :class:`ActionRegistry` by :func:`register_nx_actions`.
"""

from __future__ import annotations

from typing import Any

from ...client.nx_client import NxClient
from ..bus import Event
from .registry import ActionRegistry


def register_nx_actions(registry: ActionRegistry, client: NxClient) -> None:
    async def nx_generic_event(config: dict[str, Any], event: Event, ctx: dict[str, Any]) -> Any:
        body = {
            "source": config.get("source", "nxre"),
            "caption": config.get("caption", event.caption or "nxre automation"),
            "description": config.get("description", event.description),
            "state": config.get("state", "instant"),
        }
        return await client.create_generic_event(body)

    async def nx_soft_trigger(config: dict[str, Any], event: Event, ctx: dict[str, Any]) -> Any:
        return await client.fire_soft_trigger({"triggerId": config["trigger_id"]})

    registry.register("nx_generic_event", nx_generic_event)
    registry.register("nx_soft_trigger", nx_soft_trigger)
