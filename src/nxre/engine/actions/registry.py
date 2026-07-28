"""Action registry — maps an automation action ``kind`` to a handler coroutine.

Phase 1 registers the NX-native actions. Phase 3 adds custom kinds (ConnectWise,
HubSpot, notifications) by calling ``register()`` — no engine changes required.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from ..bus import Event

# A handler receives (action_config, triggering_event, context) and does the thing.
ActionHandler = Callable[[dict[str, Any], Event, dict[str, Any]], Awaitable[Any]]


class ActionRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, ActionHandler] = {}

    def register(self, kind: str, handler: ActionHandler) -> None:
        self._handlers[kind] = handler

    def get(self, kind: str) -> ActionHandler | None:
        return self._handlers.get(kind)

    def kinds(self) -> list[str]:
        return sorted(self._handlers)

    async def dispatch(self, kind: str, config: dict[str, Any], event: Event,
                       context: dict[str, Any]) -> Any:
        handler = self.get(kind)
        if handler is None:
            raise KeyError(f"No action handler registered for kind {kind!r}")
        return await handler(config, event, context)
