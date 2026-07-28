"""Validate a native rule against the live event/action manifest.

We refuse to apply a rule that references an event or action type the server doesn't
know about — that's a guaranteed failure. Missing *required* fields are reported as
warnings rather than hard errors, because the manifest's ``optional`` flag interacts
with ``acceptAll``/empty-selection semantics we don't fully model in Phase 1.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..client.manifest import Manifest
from ..models.rule import NativeRule

Level = Literal["error", "warning"]


@dataclass
class Issue:
    level: Level
    message: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.message}"


def validate_rule(rule: NativeRule, manifest: Manifest) -> list[Issue]:
    issues: list[Issue] = []

    # -- event --------------------------------------------------------------
    etype = rule.event_type
    if not etype:
        issues.append(Issue("error", "rule.event has no 'type'"))
    elif manifest.events and not manifest.has_event_type(etype):
        issues.append(
            Issue("error", f"unknown event type {etype!r}; known: {manifest.event_types()}")
        )
    elif manifest.events:
        for field in manifest.required_event_fields(etype):
            if field not in rule.event:
                issues.append(
                    Issue("warning", f"event {etype!r} may require field {field!r}")
                )

    # -- action -------------------------------------------------------------
    atype = rule.action_type
    if not atype:
        issues.append(Issue("error", "rule.action has no 'type'"))
    elif manifest.actions and not manifest.has_action_type(atype):
        issues.append(
            Issue("error", f"unknown action type {atype!r}; known: {manifest.action_types()}")
        )
    elif manifest.actions:
        for field in manifest.required_action_fields(atype):
            if field not in rule.action:
                issues.append(
                    Issue("warning", f"action {atype!r} may require field {field!r}")
                )

    return issues


def has_errors(issues: list[Issue]) -> bool:
    return any(i.level == "error" for i in issues)
