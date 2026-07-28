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

# Mobile push is a *native NX action* (``pushNotification``, "Send Mobile Notification")
# and NX only delivers it as the action side of a rule — there is no fire-and-forget REST
# endpoint. So an nxre automation reaches the phone in two hops: it raises a Generic Event
# tagged with a private source, and a single auto-managed NX "bridge" rule routes that
# source to the push action. We create the bridge once (idempotent, keyed by its comment)
# and thereafter every notification is just a Generic Event whose caption/description NX
# substitutes into the push (``pushNotification`` defaults caption to ``{event.caption}``).
#
# Analogy: the bridge rule is a mail slot wired to your phone; nxre just drops labelled
# envelopes ("nxre.push") through it — it doesn't rewire the phone each time.
MOBILE_NOTIFY_SOURCE = "nxre.push"
MOBILE_BRIDGE_COMMENT = "nxre:mobile-push — auto-managed bridge (Generic Event → Send Mobile Notification)"


def _bridge_rule_body(users: list[str] | None) -> dict[str, Any]:
    """Native NX rule: Generic Event (source == nxre.push) → Send Mobile Notification.

    ``users`` is a list of NX user ids; when empty, NX's default push audience (all power
    users) receives it, matching NX's own default for this action.
    """
    action: dict[str, Any] = {"type": "pushNotification"}
    if users:
        action["users"] = {"acceptAll": False, "ids": list(users)}
    return {
        "event": {"type": "generic", "source": MOBILE_NOTIFY_SOURCE},
        "action": action,
        "enabled": True,
        "schedule": [],
        "comment": MOBILE_BRIDGE_COMMENT,
    }


async def _send_mobile_notification(client: NxClient, config: dict[str, Any], event: Event) -> Any:
    """Ensure the push bridge rule exists, then fire the notification's Generic Event."""
    rules = await client.get_rules()
    if not any(r.get("comment") == MOBILE_BRIDGE_COMMENT for r in rules):
        await client.create_rule(_bridge_rule_body(config.get("users")))
    return await client.create_generic_event({
        "source": MOBILE_NOTIFY_SOURCE,
        "caption": config.get("title") or config.get("caption") or event.caption or "NX alert",
        "description": config.get("body") or config.get("description") or event.description or "",
        "state": "instant",
    })


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

    async def nx_mobile_notification(config: dict[str, Any], event: Event, ctx: dict[str, Any]) -> Any:
        return await _send_mobile_notification(client, config, event)

    registry.register("nx_generic_event", nx_generic_event)
    registry.register("nx_soft_trigger", nx_soft_trigger)
    registry.register("nx_mobile_notification", nx_mobile_notification)


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

    async def nx_mobile_notification(config: dict[str, Any], event: Event, ctx: dict[str, Any]) -> Any:
        async with _client() as client:
            return await _send_mobile_notification(client, config, event)

    registry.register("nx_generic_event", nx_generic_event)
    registry.register("nx_soft_trigger", nx_soft_trigger)
    registry.register("nx_bookmark", nx_bookmark)
    registry.register("nx_device_output", nx_device_output)
    registry.register("nx_mobile_notification", nx_mobile_notification)
