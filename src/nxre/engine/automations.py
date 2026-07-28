"""The automation runner (Phase 1: match + log; actions dispatched if configured).

Subscribes to the :class:`EventBus`. For each incoming event it finds automations whose
triggers match and logs them. Conditions are not yet evaluated in Phase 1 (they parse
and are carried through); if an action registry is provided, matched automations'
actions are dispatched best-effort so the end-to-end loop is demonstrable.
"""

from __future__ import annotations

import logging

from ..models.automation import Automation, Trigger
from . import conditions
from .actions.registry import ActionRegistry
from .bus import Event, EventBus

log = logging.getLogger("nxre.engine")


def trigger_matches(trigger: Trigger, event: Event) -> bool:
    data = trigger.model_dump()
    platform = data.pop("platform", "nx_event")
    if platform not in ("any", "*", event.platform):
        return False
    # Remaining keys are field filters against the event.
    for key, want in data.items():
        want_s = str(want).lower()
        if key in ("event_type", "type"):
            if want_s != event.type.lower():
                return False
        elif key in ("source", "device"):
            if want_s not in event.source.lower():
                return False
        elif key == "caption":
            if want_s not in event.caption.lower():
                return False
        elif key == "description":
            if want_s not in event.description.lower():
                return False
        elif key in ("object_type", "objecttype") and want_s not in ("", "any"):
            # Analytics-object events carry the detected type in the caption/description and,
            # if the webhook forwarded it, in raw["objectTypeId"] (e.g. "nx.base.Person").
            # "any"/blank means don't filter. Substring match, like the other text fields.
            hay = " ".join([
                event.caption, event.description, str(event.raw.get("objectTypeId", "")),
            ]).lower()
            if want_s not in hay:
                return False
    return True


class AutomationEngine:
    def __init__(self, automations: list[Automation], registry: ActionRegistry | None = None):
        self.set_automations(automations)
        self.registry = registry

    def set_automations(self, automations: list[Automation]) -> None:
        """Replace the active automation set (only the enabled ones run)."""
        self.automations = [a for a in automations if a.enabled]

    def attach(self, bus: EventBus) -> None:
        bus.subscribe(self._on_event)

    async def _on_event(self, event: Event) -> None:
        for auto in self.automations:
            if not any(trigger_matches(t, event) for t in auto.trigger):
                continue
            if not conditions.evaluate(auto.condition, event, auto.condition_match):
                log.info("automation %r matched but a condition blocked it", auto.alias)
                continue
            log.info("automation %r triggered by %s/%s", auto.alias, event.type, event.source)
            if self.registry:
                await self._run_actions(auto, event)

    async def _run_actions(self, auto: Automation, event: Event) -> None:
        assert self.registry is not None
        for action in auto.action:
            cfg = action.model_dump()
            kind = cfg.pop("kind")
            try:
                await self.registry.dispatch(kind, cfg, event, {"automation": auto.alias})
                log.info("  action %r dispatched", kind)
            except Exception as exc:  # noqa: BLE001
                log.warning("  action %r failed: %s", kind, exc)
