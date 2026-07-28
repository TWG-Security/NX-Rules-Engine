"""Compute a plan: how the desired rules (from YAML) differ from the live server.

Every planned change carries a **write class** derived from the safe-write policy:

* ``SAFE``    — auto-applies (enable/disable toggles, comment edits, and creating a
                non-destructive rule such as ``writeToLog`` or our own webhook rule).
* ``GUARDED`` — requires an explicit ``--apply`` (editing/deleting an existing rule, or
                any rule whose action carries credentials or drives a device).
* ``BLOCKED`` — never applied (the target system is not marked ``writable``).

This is deliberately conservative: on a live site with 60+ real rules and physical
outputs, the default must never do something surprising.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..models.rule import NativeRule

# Action types that are safe to CREATE automatically (no physical / external effect).
SAFE_CREATE_ACTION_TYPES = {"writeToLog"}
# Top-level fields whose change is considered a SAFE edit of an existing rule.
SAFE_UPDATE_FIELDS = {"enabled", "comment"}


class ChangeType(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    NOOP = "noop"


class WriteClass(str, Enum):
    SAFE = "safe"
    GUARDED = "guarded"
    BLOCKED = "blocked"


@dataclass
class PlanEntry:
    change: ChangeType
    rule_id: str | None
    write_class: WriteClass
    reasons: list[str] = field(default_factory=list)
    desired: NativeRule | None = None
    live: NativeRule | None = None
    changed_fields: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        who = self.rule_id or (self.desired.comment if self.desired else "?")
        return f"{self.change.value.upper():6} {who}"


@dataclass
class Plan:
    entries: list[PlanEntry]

    @property
    def changes(self) -> list[PlanEntry]:
        return [e for e in self.entries if e.change is not ChangeType.NOOP]

    def by_class(self, wc: WriteClass) -> list[PlanEntry]:
        return [e for e in self.changes if e.write_class == wc]

    def is_empty(self) -> bool:
        return not self.changes


def _changed_fields(desired: NativeRule, live: NativeRule) -> list[str]:
    d, l = desired.to_api_body(), live.to_api_body()
    return sorted(k for k in set(d) | set(l) if d.get(k) != l.get(k))


def _is_webhook_rule(rule: NativeRule, webhook_url: str | None) -> bool:
    if rule.action_type != "http" or not webhook_url:
        return False
    return str(rule.action.get("url", "")).startswith(webhook_url.rstrip("/"))


def _classify_create(rule: NativeRule, webhook_url: str | None) -> tuple[WriteClass, list[str]]:
    if rule.action_type in SAFE_CREATE_ACTION_TYPES:
        return WriteClass.SAFE, [f"new rule with safe action {rule.action_type!r}"]
    if _is_webhook_rule(rule, webhook_url):
        return WriteClass.SAFE, ["new webhook ingestion rule pointing at our own service"]
    return WriteClass.GUARDED, [f"new rule with action {rule.action_type!r} needs --apply"]


def _classify_update(changed: list[str]) -> tuple[WriteClass, list[str]]:
    if changed and set(changed).issubset(SAFE_UPDATE_FIELDS):
        return WriteClass.SAFE, [f"only {', '.join(changed)} changed"]
    return WriteClass.GUARDED, [f"changes {', '.join(changed)} on an existing rule need --apply"]


def build_plan(
    desired: list[NativeRule],
    live: list[NativeRule],
    *,
    system_writable: bool = True,
    webhook_url: str | None = None,
    prune: bool = False,
) -> Plan:
    """Diff ``desired`` against ``live``. ``prune`` includes deletions of live rules
    absent from the desired set (off by default — safer)."""
    live_by_id = {r.id: r for r in live if r.id}
    entries: list[PlanEntry] = []
    seen_ids: set[str] = set()

    for d in desired:
        if d.id and d.id in live_by_id:
            seen_ids.add(d.id)
            l = live_by_id[d.id]
            if d.fingerprint() == l.fingerprint():
                entries.append(PlanEntry(ChangeType.NOOP, d.id, WriteClass.SAFE, desired=d, live=l))
                continue
            changed = _changed_fields(d, l)
            wc, reasons = _classify_update(changed)
            entries.append(
                PlanEntry(ChangeType.UPDATE, d.id, wc, reasons, desired=d, live=l, changed_fields=changed)
            )
        else:
            wc, reasons = _classify_create(d, webhook_url)
            entries.append(PlanEntry(ChangeType.CREATE, d.id, wc, reasons, desired=d))

    if prune:
        for rid, l in live_by_id.items():
            if rid not in seen_ids:
                entries.append(
                    PlanEntry(ChangeType.DELETE, rid, WriteClass.GUARDED,
                              ["delete rule absent from desired state (needs --apply)"], live=l)
                )

    if not system_writable:
        for e in entries:
            if e.change is not ChangeType.NOOP:
                e.write_class = WriteClass.BLOCKED
                e.reasons.append("target system is not marked writable")

    return Plan(entries)
