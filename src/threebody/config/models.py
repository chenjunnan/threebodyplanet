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


class ScalarDistributionConfig(StrictModel):
    min: float
    max: float
    distribution: Literal["uniform", "log_uniform"]

    @model_validator(mode="after")
    def valid_bounds(self) -> ScalarDistributionConfig:
        if not math.isfinite(self.min) or not math.isfinite(self.max):
            raise ValueError("distribution bounds must be finite")
        if self.max < self.min:
            raise ValueError("distribution max must be greater than or equal to min")
        if self.distribution == "log_uniform" and self.min <= 0.0:
            raise ValueError("log_uniform distribution requires min > 0")
        return self


class StellarDistributionConfig(StrictModel):
    mass_solar: ScalarDistributionConfig

    @model_validator(mode="after")
    def positive_mass(self) -> StellarDistributionConfig:
        if self.mass_solar.min <= 0.0:
            raise ValueError("mass_solar requires min > 0")
        return self


class PlanetDistributionConfig(StrictModel):
    mass_earth: ScalarDistributionConfig
    density_kg_m3: ScalarDistributionConfig

    @model_validator(mode="after")
    def positive_physical_values(self) -> PlanetDistributionConfig:
        if self.mass_earth.min <= 0.0:
            raise ValueError("mass_earth requires min > 0")
        if self.density_kg_m3.min <= 0.0:
            raise ValueError("density_kg_m3 requires min > 0")
        return self


class ConicDistributionConfig(StrictModel):
    periapsis_m: ScalarDistributionConfig
    eccentricity: ScalarDistributionConfig

    @model_validator(mode="after")
    def physical_bounds(self) -> ConicDistributionConfig:
        if self.periapsis_m.min <= 0.0:
            raise ValueError("periapsis_m requires min > 0")
        if self.eccentricity.min < 0.0:
            raise ValueError("eccentricity requires min >= 0")
        if self.eccentricity.min == self.eccentricity.max == 1.0:
            raise ValueError("parabolic eccentricity e=1 is not supported by generator 0.4.0")
        return self


class PlanetConicDistributionConfig(ConicDistributionConfig):
    host_weights: dict[Literal["star-1", "star-2", "star-3", "barycenter"], float]

    @field_validator("host_weights")
    @classmethod
    def valid_host_weights(cls, value: dict[str, float]) -> dict[str, float]:
        expected = {"star-1", "star-2", "star-3", "barycenter"}
        if set(value) != expected:
            raise ValueError("host_weights must contain all four canonical hosts")
        if any(not math.isfinite(weight) or weight < 0.0 for weight in value.values()):
            raise ValueError("host weights must be finite and non-negative")
        if sum(value.values()) <= 0.0:
            raise ValueError("at least one host weight must be positive")
        return value


class OrientationDistributionConfig(StrictModel):
    normal: Literal["isotropic"]
    node: Literal["uniform"]
    periapsis_argument: Literal["uniform"]


class AnomalyDistributionConfig(StrictModel):
    distribution: Literal["true_anomaly_uniform_on_valid_domain"]


class VelocityKickConfig(StrictModel):
    distribution: Literal["none", "isotropic_speed_fraction"]
    speed_scale_fraction: ScalarDistributionConfig

    @model_validator(mode="after")
    def non_negative_fraction(self) -> VelocityKickConfig:
        if self.speed_scale_fraction.min < 0.0:
            raise ValueError("speed_scale_fraction requires min >= 0")
        return self


class StochasticGeometryConfig(StrictModel):
    inner: ConicDistributionConfig
    outer: ConicDistributionConfig
    planet: PlanetConicDistributionConfig
    orientation: OrientationDistributionConfig
    anomaly: AnomalyDistributionConfig
    velocity_kick: VelocityKickConfig


class StochasticConicsGeneratorConfig(StrictModel):
    type: Literal["stochastic_conics"]
    version: Literal["0.4.0"]
    stars: StellarDistributionConfig
    planet: PlanetDistributionConfig
    geometry: StochasticGeometryConfig


class PlanetStarDistanceConstraintConfig(StrictModel):
    min: float | None = Field(default=None, gt=0.0)
    max_nearest: float | None = Field(default=None, gt=0.0)
    max_each: float | None = Field(default=None, gt=0.0)


class PlanetSpeedConstraintConfig(StrictModel):
    absolute_max_mps: float | None = Field(default=None, gt=0.0)
    escape_mode: Literal["none", "selected_host", "combined_potential"]
    escape_speed_fraction_max: float | None = Field(default=None, gt=0.0)

    @model_validator(mode="after")
    def escape_fraction_matches_mode(self) -> PlanetSpeedConstraintConfig:
        if self.escape_mode == "none" and self.escape_speed_fraction_max is not None:
            raise ValueError("escape_speed_fraction_max requires an active escape_mode")
        if self.escape_mode != "none" and self.escape_speed_fraction_max is None:
            raise ValueError("active escape_mode requires escape_speed_fraction_max")
        return self


class InitialConstraintsConfig(StrictModel):
    mode: Literal["unrestricted", "bounded"]
    max_attempts: int = Field(ge=1, le=100_000)
    planet_star_distance_m: PlanetStarDistanceConstraintConfig
    stellar_pair_max_distance_m: float | None = Field(default=None, gt=0.0)
    planet_speed: PlanetSpeedConstraintConfig
    allow_initial_contact: bool


class PlanetRotationDistributionConfig(StrictModel):
    axis: Literal["isotropic"]
    period_s: ScalarDistributionConfig

    @model_validator(mode="after")
    def positive_period(self) -> PlanetRotationDistributionConfig:
        if self.period_s.min <= 0.0:
            raise ValueError("planet rotation period requires min > 0")
        return self


class AtmospherePriorConfig(StrictModel):
    model: Literal["retention_weighted_prior_v1"]
    occurrence_probability: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    mass_fraction: ScalarDistributionConfig
    albedo: ScalarDistributionConfig
    jeans_transition: tuple[float, float]
    composition_dirichlet_alpha: dict[
        Literal["nitrogen", "carbon_dioxide", "argon", "water_vapor", "oxygen"],
        float,
    ]

    @field_validator("composition_dirichlet_alpha")
    @classmethod
    def valid_composition_alpha(cls, value: dict[str, float]) -> dict[str, float]:
        expected = {"nitrogen", "carbon_dioxide", "argon", "water_vapor", "oxygen"}
        if set(value) != expected:
            raise ValueError("composition Dirichlet alpha requires the five canonical gases")
        if any(not math.isfinite(item) or item <= 0.0 for item in value.values()):
            raise ValueError("composition Dirichlet alpha values must be finite and positive")
        return value

    @field_validator("jeans_transition", mode="before")
    @classmethod
    def list_to_transition(cls, value: Any) -> Any:
        if isinstance(value, list):
            return tuple(float(item) for item in value)
        return value

    @model_validator(mode="after")
    def valid_prior(self) -> AtmospherePriorConfig:
        if self.mass_fraction.min <= 0.0 or self.mass_fraction.max > 0.05:
            raise ValueError("atmosphere mass fraction must be within (0, 0.05]")
        if not 0.0 <= self.albedo.min <= self.albedo.max <= 1.0:
            raise ValueError("atmosphere albedo distribution must be within [0, 1]")
        low, high = self.jeans_transition
        if not (math.isfinite(low) and math.isfinite(high) and 0.0 < low < high):
            raise ValueError("jeans_transition must be an increasing positive pair")
        return self


class BoundedStochasticConicsGeneratorConfig(StrictModel):
    type: Literal["stochastic_conics"]
    version: Literal["0.4.1", "0.4.2", "0.4.3"]
    stars: StellarDistributionConfig
    planet: PlanetDistributionConfig
    geometry: StochasticGeometryConfig
    initial_constraints: InitialConstraintsConfig
    planet_rotation: PlanetRotationDistributionConfig | None = None
    atmosphere_prior: AtmospherePriorConfig | None = None

    @model_validator(mode="after")
    def supported_main_sequence_range(self) -> BoundedStochasticConicsGeneratorConfig:
        if self.version in {"0.4.2", "0.4.3"}:
            masses = self.stars.mass_solar
            if masses.min < 0.3 or masses.max > 8.0:
                raise ValueError(
                    f"stochastic_conics/{self.version} main-sequence mass must be within "
                    "0.3..8 M_sun"
                )
        enhanced = self.planet_rotation is not None or self.atmosphere_prior is not None
        if self.version == "0.4.3" and (
            self.planet_rotation is None or self.atmosphere_prior is None
        ):
            raise ValueError("stochastic_conics/0.4.3 requires rotation and atmosphere priors")
        if self.version != "0.4.3" and enhanced:
            raise ValueError("rotation/atmosphere priors require stochastic_conics/0.4.3")
        return self


AnyGeneratorConfig = (
    GeneratorConfig | StochasticConicsGeneratorConfig | BoundedStochasticConicsGeneratorConfig
)


class WorldGenerationConfig(StrictModel):
    body_count: BodyCountConfig
    generator: AnyGeneratorConfig


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
    schema_version: Literal["0.1.0", "0.4.0", "0.4.1", "0.4.2", "0.4.3"]
    experiment: ExperimentConfig
    world_generation: WorldGenerationConfig
    screening: ScreeningConfig
    runtime: RuntimeConfig
    output: OutputConfig

    @model_validator(mode="after")
    def cross_constraints(self) -> ResolvedExperimentConfig:
        generator = self.world_generation.generator
        if isinstance(generator, BoundedStochasticConicsGeneratorConfig):
            expected_schema = generator.version
        elif isinstance(generator, StochasticConicsGeneratorConfig):
            expected_schema = "0.4.0"
        else:
            expected_schema = "0.1.0"
        if self.schema_version != expected_schema:
            raise ValueError(
                f"schema_version must be {expected_schema!r} for the selected generator"
            )
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

