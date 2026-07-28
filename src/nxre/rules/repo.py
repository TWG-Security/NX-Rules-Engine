"""The on-disk rule repository: pull rules to YAML, load them back, scaffold new ones.

Layout (per site):
    <rules_dir>/<system>/<rule-id>.yaml     # one readable file per native rule
    <rules_dir>/<system>/_manifest.*.yaml   # cached event/action manifests

On **pull** secrets are redacted into the gitignored secret store; on **load** they
are resolved back so the in-memory rules carry real values for diffing/applying.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from ruamel.yaml import YAML

from ..client.manifest import Manifest
from ..client.nx_client import NxClient
from ..config import Settings
from ..models.rule import NativeRule
from ..secrets import SecretStore, redact_secrets, resolve_secrets

_yaml = YAML()
_yaml.indent(mapping=2, sequence=4, offset=2)
_yaml.default_flow_style = False
_yaml.preserve_quotes = True


def system_dir(settings: Settings, system: str) -> Path:
    return settings.rules_dir / system


def rule_path(settings: Settings, system: str, rule_id: str) -> Path:
    return system_dir(settings, system) / f"{rule_id}.yaml"


def _secret_prefix(system: str, rule_id: str | None) -> str:
    short = (rule_id or uuid.uuid4().hex)[:8]
    return f"{system}.{short}"


async def pull(client: NxClient, settings: Settings, system: str) -> int:
    """Fetch all rules + manifests, redact secrets, and write YAML. Returns rule count."""
    raw_rules = await client.get_rules()
    manifest = await Manifest.fetch(client)

    out_dir = system_dir(settings, system)
    out_dir.mkdir(parents=True, exist_ok=True)
    store = SecretStore.load(settings.secrets_file)

    for raw in raw_rules:
        rule_id = raw.get("id") or uuid.uuid4().hex
        redacted = redact_secrets(raw, store, name_prefix=_secret_prefix(system, rule_id))
        rule = NativeRule.from_api(redacted)
        with open(rule_path(settings, system, rule_id), "w", encoding="utf-8") as fh:
            _yaml.dump(rule.to_yaml_obj(), fh)

    store.save(settings.secrets_file)
    manifest.save(out_dir)
    return len(raw_rules)


def load_manifest(settings: Settings, system: str) -> Manifest:
    return Manifest.load(system_dir(settings, system))


def load_desired_rules(settings: Settings, system: str) -> list[NativeRule]:
    """Read every rule YAML for a system, resolving ``${secret:*}`` to real values."""
    out_dir = system_dir(settings, system)
    if not out_dir.exists():
        return []
    store = SecretStore.load(settings.secrets_file)
    rules: list[NativeRule] = []
    for path in sorted(out_dir.glob("*.yaml")):
        if path.name.startswith("manifest.") or path.name.startswith("_"):
            continue
        with open(path, encoding="utf-8") as fh:
            data = _yaml.load(fh)
        if not data:
            continue
        resolved = resolve_secrets(dict(data), store)
        rules.append(NativeRule.from_api(resolved))
    return rules


def set_rule_enabled(settings: Settings, system: str, rule_id: str, enabled: bool) -> bool:
    """Flip a rule's ``enabled`` flag directly in its YAML file (secrets stay redacted).

    Returns True if the file was found and updated. This edits the on-disk *desired*
    state; run ``rules apply`` (a SAFE change) to push it to the server.
    """
    path = rule_path(settings, system, rule_id)
    if not path.exists():
        return False
    with open(path, encoding="utf-8") as fh:
        data = _yaml.load(fh)
    data["enabled"] = enabled
    with open(path, "w", encoding="utf-8") as fh:
        _yaml.dump(data, fh)
    return True


def write_rule(settings: Settings, system: str, rule: NativeRule) -> Path:
    """Persist a single rule to YAML (used by ``rules new``). Does not redact — callers
    scaffolding new rules should reference secrets via ``${secret:*}`` themselves."""
    out_dir = system_dir(settings, system)
    out_dir.mkdir(parents=True, exist_ok=True)
    rid = rule.id or uuid.uuid4().hex
    rule.id = rid
    path = rule_path(settings, system, rid)
    with open(path, "w", encoding="utf-8") as fh:
        _yaml.dump(rule.to_yaml_obj(), fh)
    return path


# -- scaffolding new rules --------------------------------------------------
def scaffold_write_to_log(comment: str = "nxre: sample write-to-log rule") -> NativeRule:
    """A safe demo rule: log an entry whenever any camera is disconnected."""
    return NativeRule(
        id=uuid.uuid4().hex,
        comment=comment,
        enabled=True,
        event={"type": "deviceDisconnected", "devices": {"acceptAll": True}},
        action={"type": "writeToLog", "intervalS": 60},
    )


def scaffold_webhook_rule(webhook_url: str, comment: str = "nxre: event ingestion webhook") -> NativeRule:
    """A rule whose *Do HTTP Request* action pushes generic events to the nxre service.

    Triggers on any Generic Event so nxre's engine receives a live feed to react to.
    """
    return NativeRule(
        id=uuid.uuid4().hex,
        comment=comment,
        enabled=True,
        event={"type": "generic", "state": "instant"},
        action={
            "type": "http",
            "method": "POST",
            "url": f"{webhook_url.rstrip('/')}/webhook/nx",
            "contentType": "application/json",
            "content": '{"event":"{event.type}","caption":"{event.caption}",'
            '"source":"{event.source}","description":"{event.description}"}',
        },
    )
