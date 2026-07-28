"""A tiny async event bus — the heart of the Home Assistant-style engine.

Everything that happens (an NX event arriving via webhook, later a JSON-RPC state
change) becomes an ``Event`` and is published to the bus. Automations subscribe and
react. Keeping this abstraction thin now means Phase 2 can add richer trigger sources
without touching automation logic.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

Handler = Callable[["Event"], Awaitable[None]]


@dataclass
class Event:
    """A normalized event flowing through the engine."""

    type: str                       # e.g. "generic", "motion", "deviceDisconnected"
    source: str = ""                # camera/server name or id
    caption: str = ""
    description: str = ""
    platform: str = "nx_event"      # where it entered from (nx_event, generic, ...)
    raw: dict[str, Any] = field(default_factory=dict)
    received_at: float = field(default_factory=time.time)

    @classmethod
    def from_webhook(cls, payload: dict[str, Any]) -> "Event":
        return cls(
            type=str(payload.get("event") or payload.get("type") or "generic"),
            source=str(payload.get("source", "")),
            caption=str(payload.get("caption", "")),
            description=str(payload.get("description", "")),
            platform="nx_event",
            raw=payload,
        )


class EventBus:
    """Fan-out pub/sub with a bounded ring buffer of recent events (for inspection)."""

    def __init__(self, history: int = 200):
        self._handlers: list[Handler] = []
        self.recent: deque[Event] = deque(maxlen=history)

    def subscribe(self, handler: Handler) -> None:
        self._handlers.append(handler)

    async def publish(self, event: Event) -> None:
        self.recent.append(event)
        if self._handlers:
            await asyncio.gather(*(h(event) for h in self._handlers))
