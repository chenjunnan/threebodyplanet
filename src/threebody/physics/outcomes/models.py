from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class StarFluxPoint(StrictModel):
    body_id: str
    luminosity_w: float = Field(gt=0.0)
    distance_m: float = Field(gt=0.0)
    direction_inertial_unit: tuple[float, float, float]
    flux_w_m2: float = Field(ge=0.0)


class FluxPoint(StrictModel):
    t_s: float
    planet_id: str
    stars: tuple[StarFluxPoint, StarFluxPoint, StarFluxPoint]
    planet_center_flux_w_m2: float = Field(ge=0.0)
    globally_averaged_incoming_w_m2: float = Field(ge=0.0)
    model: str
    occultation: str
    reference_equator_prime_meridian_normal: tuple[float, float, float]
    reference_equator_prime_meridian_toa_w_m2: float = Field(ge=0.0)


class C0Point(StrictModel):
    t_s: float
    temperature_k: float = Field(gt=0.0)
    planet_center_flux_w_m2: float = Field(ge=0.0)
    globally_averaged_incoming_w_m2: float = Field(ge=0.0)
    absorbed_w_m2: float
    internal_heat_flux_w_m2: float
    emitted_w_m2: float
    net_heating_w_m2: float


class C1Point(StrictModel):
    t_s: float
    latitude_center_rad: tuple[float, ...]
    temperature_k: tuple[float, ...]
    zonal_incoming_w_m2: tuple[float, ...]
    absorbed_w_m2: tuple[float, ...]
    emitted_w_m2: tuple[float, ...]
    meridional_convergence_w_m2: tuple[float, ...]
    global_mean_temperature_k: float
    globally_averaged_incoming_w_m2: float


class OutcomeEvent(StrictModel):
    event_id: str
    event_type: str
    source: Literal["G2", "M2", "M3", "M4"]
    t_start_s: float
    t_end_s: float
    body_ids: tuple[str, ...] = ()
    observed: dict[str, Any]
    detection_method: str
    sampling_uncertainty_s: float
    terminal: bool
    message: str


class ExposureEpisode(StrictModel):
    kind: Literal["COLD", "HOT"]
    t_start_s: float
    t_end_s: float
    duration_s: float
    threshold_k: float
    detection_method: Literal["piecewise_linear_threshold_crossing"]
    sampling_uncertainty_s: float


class EnvironmentStatistics(StrictModel):
    coverage_start_s: float | None
    coverage_end_s: float | None
    coverage_duration_s: float
    flux_sample_count: int
    c0_sample_count: int
    c1_sample_count: int
    sampled_flux_min_w_m2: float | None
    sampled_flux_max_w_m2: float | None
    sampled_c0_min_k: float | None
    sampled_c0_max_k: float | None
    cold_duration_s: float
    hot_duration_s: float
    longest_cold_episode_s: float
    longest_hot_episode_s: float
    maximum_c0_temperature_rate_k_s: float | None
    integrated_global_incoming_energy_j_m2: float
    maximum_c1_cold_band_fraction: float | None
    maximum_c1_hot_band_fraction: float | None
    flux_variation_fraction: float | None
    interval_semantics: str


class WorldClassification(StrictModel):
    scientific_input_status: Literal["S0_VALID", "NUMERICAL_INPUT_UNFIT", "INITIAL_CONTACT"]
    dynamical_outcome: Literal[
        "COMPLETED_OBSERVATION_WINDOW",
        "COLLISION_TERMINATED",
        "INITIAL_CONTACT",
        "ESCAPE_CANDIDATE",
        "INTEGRATION_STOPPED",
        "INTEGRATION_FAILED",
        "NOT_AVAILABLE",
    ]
    environmental_outcomes: tuple[str, ...]
    planet_status: Literal["observed", "collision_involved", "unresolved"]
    collision_with_planet: bool
    stellar_collision: bool
    ejection_candidate: bool
    thermal_exposure_extreme: bool
    narrative_limits: tuple[str, ...]
    selection_preference: str
    selection_score: float
    selected_by_preferences: bool
