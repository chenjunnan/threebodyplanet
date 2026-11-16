from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from threebody.common.json import sha256_json
from threebody.errors import ConfigError
from threebody.physics.thermal.models import ThermalSettings


def load_thermal_settings(path: str | Path) -> tuple[ThermalSettings, str]:
    source = Path(path)
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read thermal config {source}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid thermal YAML {source}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("thermal configuration root must be a mapping")
    try:
        settings = ThermalSettings.model_validate(raw, strict=True)
    except ValidationError as exc:
        raise ConfigError(f"invalid thermal configuration: {exc}") from exc
    return settings, sha256_json(settings)

