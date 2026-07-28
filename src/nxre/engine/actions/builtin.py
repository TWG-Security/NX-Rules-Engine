"""Provider-agnostic action handlers — the "Then do" building blocks.

These don't need NX: ``log`` records a line, ``http`` calls any URL (the classic
IFTTT/webhook "then"). NX-specific actions live in :mod:`nx_actions`.

Placeholders ``{type}``, ``{source}``, ``{caption}``, ``{description}`` in a URL or body
are filled from the triggering event, so you can forward what happened to another system.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..bus import Event
from .registry import ActionRegistry

log = logging.getLogger("nxre.engine")


def _fill(template: str, event: Event) -> str:
    return (
        str(template)
        .replace("{type}", event.type)
        .replace("{source}", event.source)
        .replace("{caption}", event.caption)
        .replace("{description}", event.description)
    )


def register_builtin_actions(registry: ActionRegistry) -> None:
    async def log_action(config: dict[str, Any], event: Event, ctx: dict[str, Any]) -> Any:
        message = _fill(config.get("message", "{type} from {source}"), event)
        log.info("automation %s: %s", ctx.get("automation", "?"), message)
        return {"logged": message}

    async def http_action(config: dict[str, Any], event: Event, ctx: dict[str, Any]) -> Any:
        url = _fill(config.get("url", ""), event)
        if not url:
            raise ValueError("http action requires a 'url'")
        method = str(config.get("method", "POST")).upper()
        content_type = config.get("content_type", "application/json")
        verify = bool(config.get("verify_tls", False))
        body = config.get("body")
        kwargs: dict[str, Any] = {"headers": {"content-type": content_type}}
        if body is not None and method in ("POST", "PUT", "PATCH"):
            kwargs["content"] = _fill(body, event).encode("utf-8")
        async with httpx.AsyncClient(timeout=15.0, verify=verify) as client:
            resp = await client.request(method, url, **kwargs)
        log.info("http action %s %s -> %s", method, url, resp.status_code)
        return {"status_code": resp.status_code}

    registry.register("log", log_action)
    registry.register("http", http_action)
