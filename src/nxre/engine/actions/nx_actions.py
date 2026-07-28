"""NX-native action handlers for the automation engine.

These let an automation *do something back to NX*: raise a Generic Event, fire a Soft
Trigger, etc. They wrap the same :class:`NxClient` used elsewhere, so auth/refresh is
shared. Registered into an :class:`ActionRegistry` by :func:`register_nx_actions`.
"""

from __future__ import annotations

from collections.abc import Callable
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


def register_nx_actions_factory(
    registry: ActionRegistry, client_factory: Callable[[], NxClient | None]
) -> None:
    """Like :func:`register_nx_actions`, but resolves a fresh client per dispatch.

    The long-running service's auth token changes over time (browser login, refresh),
    so instead of closing over one client we ask ``client_factory()`` at fire time. If it
    returns ``None`` (nobody logged in), the action raises a clear error the engine logs.
    """

    def _client() -> NxClient:
        client = client_factory()
        if client is None:
            raise RuntimeError("no NX session — log in through the web page to enable NX actions")
        return client

    async def nx_generic_event(config: dict[str, Any], event: Event, ctx: dict[str, Any]) -> Any:
        body = {
            "source": config.get("source", "nxre"),
            "caption": config.get("caption", event.caption or "nxre automation"),
            "description": config.get("description", event.description),
            "state": config.get("state", "instant"),
        }
        async with _client() as client:
            return await client.create_generic_event(body)

    async def nx_soft_trigger(config: dict[str, Any], event: Event, ctx: dict[str, Any]) -> Any:
        async with _client() as client:
            return await client.fire_soft_trigger({"triggerId": config["trigger_id"]})

    async def nx_bookmark(config: dict[str, Any], event: Event, ctx: dict[str, Any]) -> Any:
        device_id = config.get("device_id") or config.get("camera")
        if not device_id:
            raise ValueError("bookmark action needs a camera")
        body = {
            "name": config.get("name") or event.caption or "nxre bookmark",
            "description": config.get("description", event.description),
            "durationMs": int(config.get("duration_ms") or 5000),
        }
        async with _client() as client:
            return await client.create_bookmark(str(device_id), body)

    async def nx_device_output(config: dict[str, Any], event: Event, ctx: dict[str, Any]) -> Any:
        device_id = config.get("device_id") or config.get("camera")
        if not device_id:
            raise ValueError("output action needs a camera/device")
        port = str(config.get("port", ""))
        port_cmd: dict[str, Any] = {"isActive": True}
        if config.get("auto_reset_ms"):
            port_cmd["autoResetTimeoutMs"] = int(config["auto_reset_ms"])
        async with _client() as client:
            return await client.set_device_io(str(device_id), {port: port_cmd})

    registry.register("nx_generic_event", nx_generic_event)
    registry.register("nx_soft_trigger", nx_soft_trigger)
    registry.register("nx_bookmark", nx_bookmark)
    registry.register("nx_device_output", nx_device_output)
