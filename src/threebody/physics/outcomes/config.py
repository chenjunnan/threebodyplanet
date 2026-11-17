from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from threebody.common.json import sha256_json
from threebody.errors import ConfigError


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class ClassificationSettings(StrictModel):
    cold_threshold_k: float = Field(gt=0.0)
    hot_threshold_k: float = Field(gt=0.0)
    persistence_min_s: float = Field(ge=0.0)
    high_flux_variation_fraction: float = Field(gt=0.0)
    rapid_flux_change_fraction: float = Field(gt=0.0)
    require_complete_thermal_window: bool
    interpolation_policy: Literal["linear_threshold_crossing"]

    @model_validator(mode="after")
    def ordered_temperatures(self) -> ClassificationSettings:
        if self.hot_threshold_k <= self.cold_threshold_k:
            raise ValueError("hot_threshold_k must exceed cold_threshold_k")
        return self


class SelectionSettings(StrictModel):
    survival_required: bool
    include_collision_terminated: bool
    include_escape_candidates: bool
    include_unstable_worlds: bool
    preference: Literal["neutral", "gentle", "chaotic", "catastrophic"]


class M4Settings(StrictModel):
    schema_version: Literal["0.4.0"] = "0.4.0"
    classification: ClassificationSettings
    selection: SelectionSettings


def load_m4_settings(path: str | Path) -> tuple[M4Settings, str]:
    source = Path(path)
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read M4 config {source}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in M4 config {source}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("M4 config must contain a mapping")
    try:
        settings = M4Settings.model_validate(raw, strict=True)
    except ValidationError as exc:
        raise ConfigError(f"M4 configuration validation failed:\n{exc}") from exc
    return settings, sha256_json(settings)
