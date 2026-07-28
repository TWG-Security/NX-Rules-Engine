"""On-disk store for HA-style automations.

One automation per file: ``<automations_dir>/<system>/<id>.yaml``, matching the format
in ``automations/TWG/example.yaml``. The web builder reads/writes through here and the
engine loads the same files, so what you build in the browser is what runs (and stays
version-controllable as plain YAML).
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from ruamel.yaml import YAML

from .config import Settings
from .models.automation import Automation

_yaml = YAML()
_yaml.indent(mapping=2, sequence=4, offset=2)
_yaml.default_flow_style = False


def system_dir(settings: Settings, system: str) -> Path:
    return settings.automations_dir / system


def _path(settings: Settings, system: str, auto_id: str) -> Path:
    return system_dir(settings, system) / f"{auto_id}.yaml"


def slug(text: str) -> str:
    """A filesystem-safe id from an alias, e.g. 'Intrusion → alert' -> 'intrusion-alert'."""
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return base or uuid.uuid4().hex[:8]


def load_all(settings: Settings, system: str) -> list[Automation]:
    """Every automation for a system, tagged with its id (from the filename if unset)."""
    directory = system_dir(settings, system)
    if not directory.exists():
        return []
    out: list[Automation] = []
    for path in sorted(directory.glob("*.yaml")):
        with open(path, encoding="utf-8") as fh:
            data = _yaml.load(fh)
        if not data:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            auto = Automation.from_yaml_obj(dict(item))
            if not auto.id:
                auto.id = path.stem
            out.append(auto)
    return out


def get(settings: Settings, system: str, auto_id: str) -> Automation | None:
    for auto in load_all(settings, system):
        if auto.id == auto_id:
            return auto
    return None


def save(settings: Settings, system: str, auto: Automation) -> Path:
    """Persist one automation. Assigns an id from the alias if it has none."""
    if not auto.id:
        auto.id = slug(auto.alias)
    directory = system_dir(settings, system)
    directory.mkdir(parents=True, exist_ok=True)
    path = _path(settings, system, auto.id)
    body = auto.model_dump(exclude_none=True)
    with open(path, "w", encoding="utf-8") as fh:
        _yaml.dump(body, fh)
    return path


def delete(settings: Settings, system: str, auto_id: str) -> bool:
    path = _path(settings, system, auto_id)
    if path.exists():
        path.unlink()
        return True
    return False


def set_enabled(settings: Settings, system: str, auto_id: str, enabled: bool) -> bool:
    auto = get(settings, system, auto_id)
    if auto is None:
        return False
    auto.enabled = enabled
    save(settings, system, auto)
    return True
