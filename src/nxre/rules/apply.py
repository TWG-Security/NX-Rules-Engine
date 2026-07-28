"""Execute a plan against the live server, honoring the safe-write policy.

* SAFE changes apply automatically.
* GUARDED changes apply only when ``execute_guarded`` is set (the CLI ``--apply`` flag).
* BLOCKED changes never apply (non-writable system).
* ``dry_run`` simulates everything, mutating nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..client.nx_client import NxClient
from .diff import ChangeType, Plan, PlanEntry, WriteClass


class Outcome(str, Enum):
    APPLIED = "applied"
    SIMULATED = "simulated"
    SKIPPED = "skipped"      # guarded, but --apply not given
    BLOCKED = "blocked"      # system not writable
    FAILED = "failed"


@dataclass
class ApplyResult:
    entry: PlanEntry
    outcome: Outcome
    detail: str = ""


def _should_execute(entry: PlanEntry, execute_guarded: bool) -> tuple[bool, Outcome]:
    if entry.write_class is WriteClass.BLOCKED:
        return False, Outcome.BLOCKED
    if entry.write_class is WriteClass.GUARDED and not execute_guarded:
        return False, Outcome.SKIPPED
    return True, Outcome.APPLIED


async def _execute(client: NxClient, entry: PlanEntry) -> str:
    if entry.change is ChangeType.CREATE:
        assert entry.desired is not None
        created = await client.create_rule(entry.desired.to_api_body())
        return f"created rule id={created.get('id', '?')}"
    if entry.change is ChangeType.UPDATE:
        assert entry.desired is not None and entry.rule_id
        await client.update_rule(entry.rule_id, entry.desired.to_api_body())
        return f"updated fields: {', '.join(entry.changed_fields) or '(body)'}"
    if entry.change is ChangeType.DELETE:
        assert entry.rule_id
        await client.delete_rule(entry.rule_id)
        return "deleted"
    return "noop"


async def apply_plan(
    client: NxClient,
    plan: Plan,
    *,
    execute_guarded: bool = False,
    dry_run: bool = False,
) -> list[ApplyResult]:
    results: list[ApplyResult] = []
    for entry in plan.changes:
        will_run, blocked_outcome = _should_execute(entry, execute_guarded)
        if not will_run:
            results.append(ApplyResult(entry, blocked_outcome, "; ".join(entry.reasons)))
            continue
        if dry_run:
            results.append(ApplyResult(entry, Outcome.SIMULATED, "dry-run"))
            continue
        try:
            detail = await _execute(client, entry)
            results.append(ApplyResult(entry, Outcome.APPLIED, detail))
        except Exception as exc:  # noqa: BLE001 — surface any API failure per-entry
            results.append(ApplyResult(entry, Outcome.FAILED, str(exc)))
    return results
