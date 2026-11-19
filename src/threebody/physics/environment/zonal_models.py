from __future__ import annotations

import math
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from threebody.common.json import sha256_json
from threebody.physics.environment.models import (
    EnvironmentIntegratorConfig,
    OptionalOceanReservoir,
    StrictModel,
    ThermalReservoir,
)

MaterialId = Literal[
    "basaltic_silicate",
    "granitic_silicate",
    "iron_nickel",
    "sulfur_compounds",
    "water_ice",
]


class MaterialFraction(StrictModel):
    material_id: MaterialId
    mass_fraction: float = Field(ge=0.0, le=1.0)


class PlanetMaterialProfile(StrictModel):
    model_id: Literal["seeded_planet_material_prior_v1"] = (
        "seeded_planet_material_prior_v1"
    )
    seed: int = Field(ge=0, le=2**32 - 1)
    components: tuple[MaterialFraction, ...]
    semantics: Literal["RANDOM_PRIOR_NOT_COMPOSITION_OBSERVATION"] = (
        "RANDOM_PRIOR_NOT_COMPOSITION_OBSERVATION"
    )

    @model_validator(mode="after")
    def normalized_unique_components(self) -> PlanetMaterialProfile:
        identifiers = [component.material_id for component in self.components]
        if len(identifiers) != 5 or len(set(identifiers)) != 5:
            raise ValueError("material profile must contain each supported material exactly once")
        total = sum(component.mass_fraction for component in self.components)
        if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1.0e-12):
            raise ValueError("material mass fractions must sum to one")
        return self

    @property
    def profile_sha256(self) -> str:
        return sha256_json(self)


def sample_planet_material_profile(seed: int) -> PlanetMaterialProfile:
    """Create a reproducible broad material prior on an independent RNG stream."""
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**32 - 1:
        raise ValueError("material seed must be a uint32 integer")
    identifiers: tuple[MaterialId, ...] = (
        "basaltic_silicate",
        "granitic_silicate",
        "iron_nickel",
        "sulfur_compounds",
        "water_ice",
    )
    # Broad rocky-planet prior. It is intentionally not calibrated as a
    # population posterior and therefore remains tagged as a random prior.
    concentration = np.asarray([6.0, 1.8, 2.4, 0.45, 0.65], dtype=float)
    rng = np.random.Generator(np.random.PCG64(
        np.random.SeedSequence([seed, 0x4D373242])
    ))
    weights = rng.gamma(concentration, 1.0)
    fractions = weights / weights.sum()
    return PlanetMaterialProfile(
        seed=seed,
        components=tuple(
            MaterialFraction(material_id=identifier, mass_fraction=float(fraction))
            for identifier, fraction in zip(identifiers, fractions, strict=True)
        ),
    )


class ZonalDynamicEnvironmentConfig(StrictModel):
    schema_version: Literal["0.9.0"] = "0.9.0"
    model_id: Literal["m72_zonal_three_reservoir_v1"] = (
        "m72_zonal_three_reservoir_v1"
    )
    latitude_band_count: int = Field(default=18, ge=6, le=180)
    planet_radius_m: float = Field(gt=0.0)
    emissivity: float = Field(default=1.0, gt=0.0, le=1.0)
    internal_heat_flux_w_m2: float = Field(default=0.0, ge=0.0)
    reference_surface_pressure_pa: float = Field(default=101325.0, ge=0.0)
    surface: ThermalReservoir
    shallow_rock: ThermalReservoir
    ocean_mixed_layer: OptionalOceanReservoir
    surface_rock_exchange_w_m2_k: float = Field(default=1.0, ge=0.0)
    surface_ocean_exchange_w_m2_k: float = Field(default=0.0, ge=0.0)
    meridional_interface_conductance_w_k: float = Field(default=0.0, ge=0.0)
    absorbed_flux_weights: tuple[float, ...] | None = None
    material_profile: PlanetMaterialProfile
    max_output_samples: int = Field(default=1_000_000, ge=2, le=100_000_000)
    integrator: EnvironmentIntegratorConfig = Field(
        default_factory=EnvironmentIntegratorConfig
    )

    @model_validator(mode="after")
    def consistent_configuration(self) -> ZonalDynamicEnvironmentConfig:
        if (
            self.ocean_mixed_layer.mass_kg == 0.0
            and self.surface_ocean_exchange_w_m2_k != 0.0
        ):
            raise ValueError("ocean exchange must be zero when ocean inventory is zero")
        if self.absorbed_flux_weights is not None:
            if len(self.absorbed_flux_weights) != self.latitude_band_count:
                raise ValueError("absorbed flux weights must match latitude_band_count")
            if any(not math.isfinite(value) or value < 0.0
                   for value in self.absorbed_flux_weights):
                raise ValueError("absorbed flux weights must be finite and nonnegative")
            mean = sum(self.absorbed_flux_weights) / self.latitude_band_count
            if not math.isclose(mean, 1.0, rel_tol=0.0, abs_tol=1.0e-12):
                raise ValueError("equal-area absorbed flux weights must average to one")
        return self

    @property
    def config_sha256(self) -> str:
        return sha256_json(self)


class MaterialMeltComponent(StrictModel):
    material_id: MaterialId
    mass_fraction: float = Field(ge=0.0, le=1.0)
    pressure_adjusted_solidus_k: float = Field(gt=0.0)
    pressure_adjusted_liquidus_k: float = Field(gt=0.0)
    melt_probability: float = Field(ge=0.0, le=1.0)


class SurfacePhaseDiagnostic(StrictModel):
    expected_liquid_fraction: float = Field(ge=0.0, le=1.0)
    expected_magma_fraction: float = Field(ge=0.0, le=1.0)
    classification: Literal[
        "SOLID_DOMINANT", "PARTIALLY_LIQUID", "LIQUID_DOMINANT"
    ]
    components: tuple[MaterialMeltComponent, ...]
    semantics: Literal[
        "PARAMETERIZED_HETEROGENEITY_PROBABILITY_NO_LATENT_HEAT_FEEDBACK"
    ] = "PARAMETERIZED_HETEROGENEITY_PROBABILITY_NO_LATENT_HEAT_FEEDBACK"
    uncertainty_flags: tuple[str, ...]


class ZonalBandState(StrictModel):
    band_index: int = Field(ge=0)
    latitude_lower_deg: float = Field(ge=-90.0, le=90.0)
    latitude_center_deg: float = Field(ge=-90.0, le=90.0)
    latitude_upper_deg: float = Field(ge=-90.0, le=90.0)
    area_fraction: float = Field(gt=0.0, le=1.0)
    surface_temperature_k: float = Field(gt=0.0)
    shallow_rock_temperature_k: float = Field(gt=0.0)
    ocean_mixed_layer_temperature_k: float | None = Field(default=None, gt=0.0)
    surface_heat_capacity_j_k: float = Field(gt=0.0)
    shallow_rock_heat_capacity_j_k: float = Field(gt=0.0)
    ocean_mixed_layer_heat_capacity_j_k: float = Field(ge=0.0)
    absorbed_stellar_w_m2: float = Field(ge=0.0)
    emitted_thermal_w_m2: float = Field(ge=0.0)
    internal_heat_w_m2: float = Field(ge=0.0)
    meridional_heat_convergence_w_m2: float
    surface_phase: SurfacePhaseDiagnostic


class ZonalEnvironmentSnapshot(StrictModel):
    t_s: float
    bands: tuple[ZonalBandState, ...]
    total_sensible_energy_j: float


class ZonalEnvironmentDiagnostics(StrictModel):
    maximum_absolute_energy_residual_j: float = Field(ge=0.0)
    maximum_scaled_energy_residual: float = Field(ge=0.0)
    maximum_transport_cancellation_w: float = Field(ge=0.0)
    minimum_temperature_k: float = Field(gt=0.0)
    maximum_temperature_k: float = Field(gt=0.0)
    solver_nfev: int = Field(ge=0)


class ZonalDynamicEnvironmentResult(StrictModel):
    schema_version: Literal["0.9.0"] = "0.9.0"
    model_id: Literal["m72_zonal_three_reservoir_v1"] = (
        "m72_zonal_three_reservoir_v1"
    )
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    material_profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    forcing_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    forcing_kind: str = Field(min_length=1)
    runtime_versions: dict[str, str]
    snapshots: tuple[ZonalEnvironmentSnapshot, ...]
    diagnostics: ZonalEnvironmentDiagnostics
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    physics_semantics: Literal[
        "PHYSICAL_PARAMETERIZED_EQUAL_AREA_ZONAL_THREE_RESERVOIR"
    ] = "PHYSICAL_PARAMETERIZED_EQUAL_AREA_ZONAL_THREE_RESERVOIR"


class EnvironmentPointQuery(StrictModel):
    schema_version: Literal["environment-query-v1"] = "environment-query-v1"
    t_s: float
    latitude_deg: float = Field(ge=-90.0, le=90.0)
    longitude_deg: float = Field(default=0.0, ge=-180.0, le=180.0)


class EnvironmentPointState(StrictModel):
    schema_version: Literal["environment-point-state-v1"] = (
        "environment-point-state-v1"
    )
    model_id: Literal["m72_zonal_three_reservoir_v1"] = (
        "m72_zonal_three_reservoir_v1"
    )
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    t_s: float
    latitude_deg: float
    longitude_deg: float
    band_index: int = Field(ge=0)
    time_sampling: Literal["EXACT", "LINEAR_STATE_INTERPOLATION"]
    source_bracket_s: tuple[float, float]
    spatial_semantics: Literal["ZONAL_MEAN_LONGITUDE_UNRESOLVED"] = (
        "ZONAL_MEAN_LONGITUDE_UNRESOLVED"
    )
    surface_temperature_k: float = Field(gt=0.0)
    shallow_rock_temperature_k: float = Field(gt=0.0)
    ocean_mixed_layer_temperature_k: float | None = Field(default=None, gt=0.0)
    surface_pressure_pa: float = Field(ge=0.0)
    expected_surface_liquid_fraction: float = Field(ge=0.0, le=1.0)
    expected_magma_fraction: float = Field(ge=0.0, le=1.0)
    air_temperature_k: float | None = None
    relative_humidity: float | None = None
    specific_humidity_kg_kg: float | None = None
    eastward_wind_m_s: float | None = None
    northward_wind_m_s: float | None = None
    precipitation_rate_kg_m2_s: float | None = None
    weather_events: tuple[str, ...] = ()
    uncertainty_flags: tuple[str, ...]
