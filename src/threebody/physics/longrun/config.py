from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from threebody.common.json import sha256_json
from threebody.errors import ConfigError
from threebody.physics.longrun.models import LongRunSettings


def load_long_run_settings(path: str | Path) -> tuple[LongRunSettings, str]:
    source = Path(path)
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read long-run config {source}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in long-run config {source}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("long-run configuration must be a YAML mapping")
    try:
        settings = LongRunSettings.model_validate(raw, strict=True)
    except ValidationError as exc:
        raise ConfigError(f"long-run configuration validation failed:\n{exc}") from exc
    return settings, sha256_json(settings)
