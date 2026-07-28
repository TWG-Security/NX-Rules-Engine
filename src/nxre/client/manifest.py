"""Event/action manifest handling.

The NX server publishes, per site, a manifest of every event type and every action
type it supports — including each field, whether it's optional, and its schema. We
use it as the source of truth for validation: a rule may only reference event/action
types (and required fields) the server actually knows about.

We cache the manifests to disk so offline validation and diffs work without a live
server, refreshing when a live client is available.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from .nx_client import NxClient

_yaml = YAML(typ="safe")
_yaml.default_flow_style = False


class Manifest:
    """Convenience view over the event + action manifests for one site."""

    def __init__(self, events: dict[str, Any], actions: dict[str, Any]):
        self.events = events or {}
        self.actions = actions or {}

    # -- events -------------------------------------------------------------
    def has_event_type(self, event_type: str) -> bool:
        return event_type in self.events

    def event_types(self) -> list[str]:
        return sorted(self.events)

    def required_event_fields(self, event_type: str) -> list[str]:
        return self._required_fields(self.events.get(event_type, {}))

    # -- actions ------------------------------------------------------------
    def has_action_type(self, action_type: str) -> bool:
        return action_type in self.actions

    def action_types(self) -> list[str]:
        return sorted(self.actions)

    def required_action_fields(self, action_type: str) -> list[str]:
        return self._required_fields(self.actions.get(action_type, {}))

    # -- builder-friendly views --------------------------------------------
    def event_items(self) -> list[dict]:
        """Each event type as ``{id, displayName, flags, fields[]}`` for the UI builder."""
        return self._items(self.events)

    def action_items(self) -> list[dict]:
        return self._items(self.actions)

    @staticmethod
    def _items(catalog: dict[str, Any]) -> list[dict]:
        out: list[dict] = []
        for type_id, spec in (catalog or {}).items():
            if not isinstance(spec, dict):
                continue
            fields = [
                {
                    "type": f.get("type", ""),
                    "fieldName": f.get("fieldName", ""),
                    "displayName": f.get("displayName") or f.get("fieldName", ""),
                }
                for f in (spec.get("fields") or [])
                if isinstance(f, dict) and f.get("fieldName")
            ]
            out.append({
                "id": type_id,
                "displayName": spec.get("displayName") or type_id,
                "flags": spec.get("flags", ""),
                "fields": fields,
            })
        return sorted(out, key=lambda x: x["displayName"].lower())

    @staticmethod
    def _required_fields(spec: dict[str, Any]) -> list[str]:
        """Field names the manifest marks non-optional (``optional: false``)."""
        required: list[str] = []
        for field in spec.get("fields", []) or []:
            name = field.get("fieldName")
            props = field.get("properties", {}) or {}
            # A field is required when it declares optional: false explicitly.
            if name and props.get("optional") is False:
                required.append(name)
        return required

    # -- persistence --------------------------------------------------------
    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        for name, data in (("events", self.events), ("actions", self.actions)):
            with open(directory / f"manifest.{name}.yaml", "w", encoding="utf-8") as fh:
                _yaml.dump(data, fh)

    @classmethod
    def load(cls, directory: Path) -> "Manifest":
        def _read(name: str) -> dict:
            path = directory / f"manifest.{name}.yaml"
            if not path.exists():
                return {}
            with open(path, encoding="utf-8") as fh:
                return _yaml.load(fh) or {}

        return cls(events=_read("events"), actions=_read("actions"))

    @classmethod
    async def fetch(cls, client: NxClient) -> "Manifest":
        return cls(
            events=await client.get_event_manifest(),
            actions=await client.get_action_manifest(),
        )
