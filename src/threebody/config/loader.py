from __future__ import annotations

from copy import deepcopy
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from threebody.common.json import sha256_json
from threebody.config.models import ResolvedExperimentConfig
from threebody.errors import ConfigError, FeatureNotImplementedError

RULE_IDS = (
    "body.mass.positive",
    "body.radius.positive",
    "body.density.bounds",
    "system.initial_overlap",
    "system.barycenter",
)


def _safe_yaml(text: str, source: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"{source} must contain a YAML mapping")
    return value


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _load_preset(name: str) -> dict[str, Any]:
    if name not in {"strict", "research", "raw"}:
        raise ConfigError(f"unknown screening preset: {name!r}")
    resource = files("threebody.resources.presets").joinpath(f"{name}.yaml")
    return _safe_yaml(resource.read_text(encoding="utf-8"), f"preset {name}")


def load_and_resolve(path: str | Path) -> tuple[ResolvedExperimentConfig, str]:
    source_path = Path(path)
    try:
        raw = _safe_yaml(source_path.read_text(encoding="utf-8"), str(source_path))
    except OSError as exc:
        raise ConfigError(f"cannot read {source_path}: {exc}") from exc

    screening = raw.get("screening")
    if not isinstance(screening, dict):
        raise ConfigError("screening must be a mapping")
    preset_name = screening.get("preset")
    if not isinstance(preset_name, str):
        raise ConfigError("screening.preset must be a string")
    preset = _load_preset(preset_name)
    preset.pop("preset_version", None)

    initial_override = screening.get("initial_physics", {})
    if not isinstance(initial_override, dict):
        raise ConfigError("screening.initial_physics must be a mapping")
    rules_override = initial_override.get("rules", {})
    if not isinstance(rules_override, dict):
        raise ConfigError("screening.initial_physics.rules must be a mapping")
    unknown = set(rules_override) - set(RULE_IDS)
    if unknown:
        raise ConfigError(f"unknown rule id(s): {', '.join(sorted(unknown))}")

    resolved_raw = deepcopy(raw)
    resolved_raw["screening"] = _deep_merge(
        {
            "preset": preset_name,
            "default_rule_state": "disabled",
            "fail_fast": False,
            "exploration": {"enabled": False},
            **preset,
        },
        screening,
    )
    if resolved_raw["screening"].get("exploration", {}).get("enabled") is True:
        raise FeatureNotImplementedError("exploration.enabled=true is not implemented in P0-M1")
    if raw.get("runtime", {}).get("simulation_enabled") is True:
        raise FeatureNotImplementedError(
            "runtime.simulation_enabled=true is not implemented in P0-M1"
        )
    try:
        model = ResolvedExperimentConfig.model_validate(resolved_raw, strict=True)
    except ValidationError as exc:
        raise ConfigError(f"configuration validation failed:\n{exc}") from exc

    from threebody.screening.rules import validate_rule_configs

    validate_rule_configs(model.screening.initial_physics.rules)
    return model, sha256_json(model)

