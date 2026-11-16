from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, allow_inf_nan=False
    )


class CollisionSettings(StrictModel):
    mode: Literal["terminate"] = "terminate"


class EncounterSettings(StrictModel):
    enabled: bool = True
    threshold_mode: Literal["absolute_m", "radius_multiple"] = "radius_multiple"
    distance_m: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    radius_multiplier: float | None = Field(default=5.0, gt=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def selected_threshold_is_present(self) -> EncounterSettings:
        if not self.enabled:
            return self
        if self.threshold_mode == "absolute_m" and self.distance_m is None:
            raise ValueError("encounter.distance_m is required for threshold_mode=absolute_m")
        if self.threshold_mode == "radius_multiple" and self.radius_multiplier is None:
            raise ValueError(
                "encounter.radius_multiplier is required for threshold_mode=radius_multiple"
            )
        return self


class EscapeSettings(StrictModel):
    enabled: bool = True
    boundary_radius_m: float = Field(gt=0, allow_inf_nan=False)
    require_outward_velocity: bool = True
    require_positive_specific_energy: bool = True
    minimum_duration_s: float = Field(default=0.0, ge=0, allow_inf_nan=False)


class IntegrationSettings(StrictModel):
    schema_version: Literal["0.2.0"] = "0.2.0"
    backend: Literal["rebound_ias15"] = "rebound_ias15"
    start_time_s: float = Field(allow_inf_nan=False)
    duration_s: float = Field(gt=0, allow_inf_nan=False)
    output_interval_s: float = Field(gt=0, allow_inf_nan=False)
    epsilon: float = Field(default=1.0e-9, ge=1.0e-15, le=1.0e-4, allow_inf_nan=False)
    collision: CollisionSettings = Field(default_factory=CollisionSettings)
    encounter: EncounterSettings
    escape: EscapeSettings
    require_positive_radius: bool = False
    max_samples: int = Field(default=100_000, ge=2, le=1_000_000)
    max_wall_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    softening_m: Literal[0.0] = 0.0

    @property
    def end_time_s(self) -> float:
        return self.start_time_s + self.duration_s

    @model_validator(mode="after")
    def finite_end_and_sample_limit(self) -> IntegrationSettings:
        if not math.isfinite(self.end_time_s):
            raise ValueError("start_time_s + duration_s must be finite")
        required = math.ceil(self.duration_s / self.output_interval_s) + 1
        if required > self.max_samples:
            raise ValueError(
                f"settings require {required} samples, exceeding max_samples={self.max_samples}"
            )
        return self


class BodyTrajectoryState(StrictModel):
    id: str
    position_m: tuple[float, float, float]
    velocity_mps: tuple[float, float, float]


class DiagnosticSnapshot(StrictModel):
    t_s: float
    kinetic_energy_j: float
    potential_energy_j: float
    total_energy_j: float
    energy_scale_j: float
    linear_momentum_kg_mps: tuple[float, float, float]
    angular_momentum_kg_m2_s: tuple[float, float, float]
    barycenter_position_m: tuple[float, float, float]
    barycenter_velocity_mps: tuple[float, float, float]


class TrajectorySample(StrictModel):
    t_s: float
    bodies: tuple[BodyTrajectoryState, ...]
    diagnostics: DiagnosticSnapshot


class SimulationEvent(StrictModel):
    event_type: Literal["COLLISION", "CLOSE_ENCOUNTER", "ESCAPE_CANDIDATE"]
    t_s: float
    body_ids: tuple[str, ...]
    observed: dict[str, Any]
    message: str
    detection_method: str


class DiagnosticSummary(StrictModel):
    initial: DiagnosticSnapshot
    final: DiagnosticSnapshot
    maximum_scaled_energy_error: float
    final_scaled_energy_error: float
    maximum_traditional_relative_energy_error: float | None
    final_traditional_relative_energy_error: float | None
    maximum_angular_momentum_relative_error: float
    final_angular_momentum_relative_error: float
    maximum_barycenter_position_drift_m: float
    maximum_barycenter_velocity_drift_mps: float
    conservation_applicable: bool


class IntegrationResult(StrictModel):
    schema_version: Literal["0.2.0"] = "0.2.0"
    world_id: str
    initial_state_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    settings_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    integrator_name: Literal["REBOUND IAS15"] = "REBOUND IAS15"
    integrator_version: str
    project_version: str
    python_version: str
    numpy_version: str
    constant_set_version: str
    generated_at_utc: str
    settings: IntegrationSettings
    status: Literal["COMPLETED", "COLLISION", "STOPPED", "FAILED"]
    t_start_s: float
    t_final_s: float
    sample_count: int = Field(ge=0)
    event_count: int = Field(ge=0)
    artifact_refs: dict[str, str]
    diagnostic_summary: DiagnosticSummary | None
    error: str | None = None

