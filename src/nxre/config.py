"""Configuration loading for nxre.

Settings come from a YAML file (default ``nxre.config.yaml``, overridable via the
``NXRE_CONFIG`` env var). Per-system passwords may be supplied — and are preferably
supplied — via environment variables of the form ``NXRE__<SYSTEM>__PASSWORD`` so
that credentials never need to be written to disk. An env password always wins.

Think of this as the engine's wiring diagram: which NX sites exist, how to reach
them, and which ones we are even allowed to write to.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field, SecretStr
from ruamel.yaml import YAML

DEFAULT_CONFIG_FILENAME = "nxre.config.yaml"
ENV_CONFIG_VAR = "NXRE_CONFIG"


class NxSystem(BaseModel):
    """Connection details for a single NX Witness site."""

    name: str
    base_url: str = Field(..., description="e.g. https://127.0.0.1:7001")
    username: str = "nxre-service"
    password: SecretStr = SecretStr("")
    verify_tls: bool = False
    writable: bool = False

    def env_password(self) -> str | None:
        """Password from ``NXRE__<NAME>__PASSWORD``, if set."""
        key = f"NXRE__{self.name.upper()}__PASSWORD"
        return os.environ.get(key)

    def resolved_password(self) -> str:
        return self.env_password() or self.password.get_secret_value()


class WebhookConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8787
    public_url: str = "http://127.0.0.1:8787"


class Settings(BaseModel):
    """Top-level nxre settings."""

    default_system: str = "TWG"
    webhook: WebhookConfig = Field(default_factory=WebhookConfig)
    rules_dir: Path = Path("rules")
    automations_dir: Path = Path("automations")
    secrets_file: Path = Path("secrets.local.yaml")
    systems: dict[str, NxSystem] = Field(default_factory=dict)

    def system(self, name: str | None = None) -> NxSystem:
        """Return the requested system (or the default), raising if unknown."""
        target = name or self.default_system
        try:
            return self.systems[target]
        except KeyError:
            known = ", ".join(sorted(self.systems)) or "(none configured)"
            raise KeyError(f"Unknown NX system {target!r}. Configured: {known}") from None


def find_config_path(explicit: str | os.PathLike[str] | None = None) -> Path | None:
    """Resolve the config file path from an explicit arg, env var, or CWD default."""
    if explicit:
        return Path(explicit)
    env = os.environ.get(ENV_CONFIG_VAR)
    if env:
        return Path(env)
    candidate = Path.cwd() / DEFAULT_CONFIG_FILENAME
    return candidate if candidate.exists() else None


def load_settings(path: str | os.PathLike[str] | None = None) -> Settings:
    """Load settings from YAML, injecting the system ``name`` into each entry.

    Falls back to an empty ``Settings`` (no systems) when no config file exists,
    which keeps ``--help`` and unit tests usable without a live config.
    """
    config_path = find_config_path(path)
    if config_path is None or not Path(config_path).exists():
        return Settings()

    yaml = YAML(typ="safe")
    with open(config_path, encoding="utf-8") as fh:
        raw = yaml.load(fh) or {}

    # Stamp each system dict with its own key as `name` so NxSystem can build env keys.
    systems_raw = raw.get("systems", {}) or {}
    for sys_name, sys_cfg in systems_raw.items():
        if isinstance(sys_cfg, dict):
            sys_cfg.setdefault("name", sys_name)

    return Settings.model_validate(raw)
