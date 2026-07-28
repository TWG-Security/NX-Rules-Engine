"""Condition evaluation for automations — the "And if" of if-this-then-that.

An automation runs its actions only when *every* condition passes (logical AND, like
Home Assistant's default). Each condition is a small dict with a ``condition`` kind and
a few fields; we evaluate the kinds the builder can emit and pass-through (with a
warning) anything unknown, so a hand-written automation never silently blocks.

Analogy: the trigger opens the door; the conditions are the bouncer's checklist. Every
box must be ticked or the actions don't get in.
"""

from __future__ import annotations

import logging
from datetime import datetime, time
from typing import Any

from ..models.automation import Condition
from .bus import Event

log = logging.getLogger("nxre.engine")


def _parse_hhmm(value: Any) -> time | None:
    try:
        hh, mm = str(value).strip().split(":")
        return time(int(hh), int(mm))
    except (ValueError, AttributeError):
        return None


def _time_between(now: time, after: time | None, before: time | None) -> bool:
    if after is None or before is None:
        return True  # incomplete window → don't block
    if after <= before:
        return after <= now <= before
    # Window wraps midnight, e.g. 22:00 → 06:00.
    return now >= after or now <= before


def evaluate_one(cond: Condition, event: Event, now: time | None = None) -> bool:
    data = cond.model_dump()
    kind = str(data.pop("condition", "") or "").lower()
    value = str(data.get("value", "")).lower()

    if kind in ("", "always"):
        return True
    if kind in ("source_contains", "source"):
        return value in event.source.lower()
    if kind in ("caption_contains", "caption"):
        return value in event.caption.lower()
    if kind in ("description_contains", "description"):
        return value in event.description.lower()
    if kind in ("event_type_is", "event_type", "type"):
        return value == event.type.lower()
    if kind in ("time_between", "time"):
        # Local wall-clock is intentional: operators think "between 22:00 and 06:00" in
        # the server's local time, not UTC.
        now = now or datetime.now().time()  # noqa: DTZ005
        return _time_between(now, _parse_hhmm(data.get("after")), _parse_hhmm(data.get("before")))
    if kind in ("day_of_week", "dow"):
        days = data.get("days", data.get("value", ""))
        if isinstance(days, list):
            days = ",".join(days)
        wanted = {d.strip().lower()[:3] for d in str(days).split(",") if d.strip()}
        today = datetime.now().strftime("%a").lower()  # noqa: DTZ005 — local day is intended
        return not wanted or today in wanted

    log.warning("unknown condition kind %r — treating as satisfied", kind)
    return True


def evaluate(
    conditions: list[Condition], event: Event, match: str = "all", now: time | None = None
) -> bool:
    """True if the conditions pass under ``match`` ('all' → AND, 'any' → OR).

    An empty condition list always passes.
    """
    if not conditions:
        return True
    results = [evaluate_one(c, event, now) for c in conditions]
    return any(results) if str(match).lower().startswith("any") else all(results)


def evaluate_all(conditions: list[Condition], event: Event, now: time | None = None) -> bool:
    """Back-compat: all conditions must pass (AND)."""
    return evaluate(conditions, event, "all", now)
