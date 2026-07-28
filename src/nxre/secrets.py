"""Secret redaction & resolution for rule bodies.

NX stores action credentials (camera ``root`` passwords, HTTP basic/digest auth)
*inline* in the rule JSON. Committing that to git would leak them. So on **pull** we
replace every secret-bearing value with a ``${secret:NAME}`` placeholder and stash the
real value in a gitignored store; on **apply** we resolve the placeholders back.

Analogy: the rule YAML is a recipe you can safely share; the passwords are the liquor
kept in a locked cabinet (``secrets.local.yaml``). The recipe says "add ${secret:...}",
and only at cooking time do we unlock the cabinet.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

_yaml = YAML(typ="safe")
_yaml.default_flow_style = False

SECRET_RE = re.compile(r"\$\{secret:([^}]+)\}")

# Dict keys whose string values are treated as secrets and redacted.
SECRET_KEY_NAMES = {"password", "passwd", "pwd", "secret", "token", "apikey", "api_key"}


class SecretStore:
    """A name -> value map persisted to a gitignored YAML file."""

    def __init__(self, values: dict[str, str] | None = None, path: Path | None = None):
        self.values: dict[str, str] = dict(values or {})
        self.path = path

    @classmethod
    def load(cls, path: Path) -> "SecretStore":
        if not path.exists():
            return cls(path=path)
        with open(path, encoding="utf-8") as fh:
            data = _yaml.load(fh) or {}
        return cls(values={str(k): str(v) for k, v in data.items()}, path=path)

    def save(self, path: Path | None = None) -> Path:
        target = path or self.path
        if target is None:
            raise ValueError("SecretStore has no path to save to")
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            _yaml.dump(dict(sorted(self.values.items())), fh)
        # Best-effort tighten perms; secrets file should not be world-readable.
        try:
            target.chmod(0o600)
        except OSError:
            pass
        return target

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def get(self, name: str) -> str:
        try:
            return self.values[name]
        except KeyError:
            raise KeyError(
                f"Secret {name!r} referenced by a rule is not in the secret store. "
                f"Add it to your secrets file or re-run `nxre rules pull`."
            ) from None


class MissingSecretError(KeyError):
    """Raised when resolving a placeholder with no stored value."""


def redact_secrets(data: Any, store: SecretStore, name_prefix: str = "") -> Any:
    """Return a deep copy of ``data`` with secret-bearing values replaced by
    ``${secret:NAME}`` placeholders, populating ``store`` with the real values."""

    def walk(node: Any, path: str) -> Any:
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for key, val in node.items():
                child_path = f"{path}.{key}" if path else key
                is_secret = (
                    key.lower() in SECRET_KEY_NAMES
                    and isinstance(val, str)
                    and val
                    and not SECRET_RE.fullmatch(val.strip())
                )
                if is_secret:
                    name = f"{name_prefix}.{child_path}" if name_prefix else child_path
                    store.set(name, val)
                    out[key] = f"${{secret:{name}}}"
                else:
                    out[key] = walk(val, child_path)
            return out
        if isinstance(node, list):
            return [walk(item, f"{path}[{i}]") for i, item in enumerate(node)]
        return node

    return walk(data, "")


def resolve_secrets(data: Any, store: SecretStore) -> Any:
    """Return a deep copy of ``data`` with every ``${secret:NAME}`` resolved."""

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(item) for item in node]
        if isinstance(node, str):
            return SECRET_RE.sub(lambda m: store.get(m.group(1)), node)
        return node

    return walk(data)


def find_secret_refs(data: Any) -> list[str]:
    """List all secret names referenced by ``${secret:...}`` placeholders in ``data``."""
    names: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            names.extend(SECRET_RE.findall(node))

    walk(data)
    return names
