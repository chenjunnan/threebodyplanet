from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ExperimentConfig(StrictModel):
    id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    seed: int = Field(ge=0, le=4_294_967_295)
    candidate_count: int = Field(ge=1)


class BodyCountConfig(StrictModel):
    stars: Literal[3]
    planets: Literal[1]


class GeneratorConfig(StrictModel):
    type: Literal["cartesian", "hierarchical_orbit"]
    version: Literal["0.1.0"]
    stellar_mass_solar: tuple[float, float]
    planet_mass_earth: tuple[float, float]
    planet_density_kg_m3: tuple[float, float]
    characteristic_length_m: float = Field(gt=0)
    inner_orbit_radius_m: float | None = Field(default=None, gt=0)
    outer_orbit_radius_m: float | None = Field(default=None, gt=0)
    planet_orbit_radius_m: float | None = Field(default=None, gt=0)
    planet_orbit_host: Literal["star-1", "star-2", "star-3", "barycenter"] = "star-1"
    velocity_scale_factor: float = Field(default=1.0, ge=0)

    @field_validator(
        "stellar_mass_solar", "planet_mass_earth", "planet_density_kg_m3", mode="before"
    )
    @classmethod
    def yaml_range_to_tuple(cls, value: Any) -> Any:
        if isinstance(value, list):
            if len(value) != 2 or any(type(item) not in (int, float) for item in value):
                raise ValueError("range must contain exactly two numeric values")
            return tuple(float(item) for item in value)
        return value

    @model_validator(mode="after")
    def valid_ranges(self) -> GeneratorConfig:
        for name in ("stellar_mass_solar", "planet_mass_earth", "planet_density_kg_m3"):
            low, high = getattr(self, name)
            if not math.isfinite(low) or not math.isfinite(high) or low <= 0 or high < low:
                raise ValueError(f"{name} must be a finite positive [minimum, maximum] range")
        if self.type == "hierarchical_orbit":
            required = (
                self.inner_orbit_radius_m,
                self.outer_orbit_radius_m,
                self.planet_orbit_radius_m,
            )
            if any(value is None for value in required):
                raise ValueError("hierarchical_orbit requires inner, outer and planet orbit radii")
            if self.outer_orbit_radius_m <= self.inner_orbit_radius_m:
                raise ValueError("outer_orbit_radius_m must exceed inner_orbit_radius_m")
        return self


class WorldGenerationConfig(StrictModel):
    body_count: BodyCountConfig
    generator: GeneratorConfig


class RuleConfig(StrictModel):
    enabled: bool
    severity: Literal["error", "warning", "info"]
    failure_action: Literal["reject", "report"]
    params: dict[str, Any]

    @model_validator(mode="after")
    def consistent_action(self) -> RuleConfig:
        if self.severity in ("warning", "info") and self.failure_action == "reject":
            raise ValueError("warning/info rules may only use failure_action=report")
        return self


class InitialPhysicsConfig(StrictModel):
    enabled: bool
    rules: dict[str, RuleConfig]


class ExplorationConfig(StrictModel):
    enabled: bool


class ScreeningConfig(StrictModel):
    preset: Literal["strict", "research", "raw"]
    default_rule_state: Literal["disabled"]
    fail_fast: bool
    initial_physics: InitialPhysicsConfig
    exploration: ExplorationConfig


class RuntimeConfig(StrictModel):
    simulation_enabled: bool


class OutputConfig(StrictModel):
    max_candidates: int = Field(ge=1)
    format: Literal["json"]


class ResolvedExperimentConfig(StrictModel):
    schema_version: Literal["0.1.0"]
    experiment: ExperimentConfig
    world_generation: WorldGenerationConfig
    screening: ScreeningConfig
    runtime: RuntimeConfig
    output: OutputConfig

    @model_validator(mode="after")
    def cross_constraints(self) -> ResolvedExperimentConfig:
        if self.experiment.candidate_count > self.output.max_candidates:
            raise ValueError("candidate_count exceeds output.max_candidates")
        if self.screening.exploration.enabled:
            raise ValueError(
                "exploration.enabled=true requires future M2/M3 and is not implemented"
            )
        if self.runtime.simulation_enabled:
            raise ValueError(
                "runtime.simulation_enabled=true requires future M2 and is not implemented"
            )
        return self

