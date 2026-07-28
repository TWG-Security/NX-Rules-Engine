"""The native NX event-rule model.

Mirrors the NX rule object (``{event, action, enabled, schedule[], comment}`` plus
server-managed ``id``/``etag``). ``event`` and ``action`` are kept as free-form dicts
because their shape is polymorphic — it depends on the ``type`` inside them, which the
manifest describes. We keep them as dicts and validate against the manifest separately.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Keys that are identity/server-managed and must NOT be part of desired-state diffs.
_VOLATILE_KEYS = {"id", "etag"}
# Order fields are written to YAML — identity/metadata first, then the meat.
_YAML_ORDER = ["id", "comment", "enabled", "schedule", "event", "action"]


class NativeRule(BaseModel):
    """A single NX event rule."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    comment: str = ""
    enabled: bool = True
    event: dict[str, Any] = Field(default_factory=dict)
    action: dict[str, Any] = Field(default_factory=dict)
    schedule: list[dict[str, Any]] = Field(default_factory=list)
    etag: str | None = None

    @property
    def event_type(self) -> str | None:
        return self.event.get("type")

    @property
    def action_type(self) -> str | None:
        return self.action.get("type")

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "NativeRule":
        return cls.model_validate(data)

    def to_api_body(self) -> dict[str, Any]:
        """Body for POST/PUT/PATCH — desired-state fields only, no id/etag."""
        return {
            "event": self.event,
            "action": self.action,
            "enabled": self.enabled,
            "schedule": self.schedule,
            "comment": self.comment,
        }

    def to_yaml_obj(self) -> dict[str, Any]:
        """Human-friendly ordered dict for on-disk YAML (keeps id + etag as metadata)."""
        full = self.model_dump(exclude_none=True)
        ordered = {k: full[k] for k in _YAML_ORDER if k in full}
        # append any remaining (extra) keys, e.g. `prolonged`, but not etag yet
        for k, v in full.items():
            if k not in ordered and k != "etag":
                ordered[k] = v
        if self.etag:
            ordered["etag"] = self.etag
        return ordered

    def desired_state(self) -> dict[str, Any]:
        """Canonical, stable representation used for diffing (excludes id/etag)."""
        body = self.to_api_body()
        return json.loads(json.dumps(body, sort_keys=True))

    def fingerprint(self) -> str:
        """Stable hash-able string of the desired state."""
        return json.dumps(self.desired_state(), sort_keys=True, separators=(",", ":"))
