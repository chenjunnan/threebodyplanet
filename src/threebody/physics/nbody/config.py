from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from threebody.common.json import sha256_json
from threebody.errors import ConfigError
from threebody.physics.nbody.models import IntegrationSettings


def load_integration_settings(path: str | Path) -> tuple[IntegrationSettings, str]:
    source = Path(path)
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read simulation config {source}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in simulation config {source}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("simulation configuration must be a YAML mapping")
    try:
        settings = IntegrationSettings.model_validate(raw, strict=True)
    except ValidationError as exc:
        raise ConfigError(f"simulation configuration validation failed:\n{exc}") from exc
    return settings, sha256_json(settings)

