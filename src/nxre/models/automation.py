"""Home Assistant-style automation model (Phase 1: schema + parsing only).

This is the "better than NX" surface: automations expressed as
``trigger[] / condition[] / action[]`` with a run ``mode`` — the same mental model as
Home Assistant's ``automations.yaml``. The Phase 1 engine only *logs* matched events;
Phase 2 wires conditions and the action registry to make these live.

Keeping the model here now means the YAML format is stable from day one.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Trigger(BaseModel):
    """What starts the automation.

    ``platform`` mirrors an NX event source, e.g. ``nx_event`` (an event pushed from
    NX via webhook), ``generic`` (an NX Generic Event), or ``soft_trigger``.
    Remaining keys are matched against the incoming event payload.
    """

    model_config = ConfigDict(extra="allow")
    platform: str
    # e.g. event_type, device_id, caption keywords — matched against the event.


class Condition(BaseModel):
    """An additional test that must pass for actions to run (Phase 2 evaluates these)."""

    model_config = ConfigDict(extra="allow")
    condition: str  # "time", "state", "template", "and", "or", "not", ...


class Action(BaseModel):
    """Something to do. ``kind`` selects a handler in the action registry.

    Built-in kinds target NX (``nx_generic_event``, ``nx_soft_trigger``,
    ``nx_device_output``, ``nx_bookmark``); custom kinds (ConnectWise, HubSpot, …)
    register in Phase 3.
    """

    model_config = ConfigDict(extra="allow")
    kind: str


class Automation(BaseModel):
    """A full HA-style automation."""

    model_config = ConfigDict(extra="allow")
    id: str | None = None
    alias: str = "unnamed automation"
    description: str = ""
    enabled: bool = True
    mode: Literal["single", "restart", "queued", "parallel"] = "single"
    trigger: list[Trigger] = Field(default_factory=list)
    condition: list[Condition] = Field(default_factory=list)
    action: list[Action] = Field(default_factory=list)

    @classmethod
    def from_yaml_obj(cls, data: dict[str, Any]) -> "Automation":
        return cls.model_validate(data)
