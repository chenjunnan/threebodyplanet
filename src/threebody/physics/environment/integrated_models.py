from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, model_validator

from threebody.common.json import sha256_json
from threebody.physics.environment.models import StrictModel
from threebody.physics.environment.zonal_models import PlanetMaterialProfile
from threebody.world.models import WorldInitialState

GAS_MOLAR_MASS_KG_MOL = {
    "nitrogen": 0.0280134,
    "oxygen": 0.0319988,
    "carbon_dioxide": 0.0440095,
    "nitrogen_dioxide": 0.0460055,
    "methane": 0.0160425,
    "sulfur_dioxide": 0.064066,
    "water_vapor": 0.01801528,
    "hydrogen": 0.00201588,
    "helium": 0.004002602,
    "ammonia": 0.01703052,
    "argon": 0.039948,
}


class AtmosphereInventoryConfig(StrictModel):
    column_mass_kg_m2: float = Field(ge=0.0, le=1.0e9)
    mole_fractions: dict[str, float]

    @model_validator(mode="after")
    def known_normalized_gases(self) -> AtmosphereInventoryConfig:
        if set(self.mole_fractions) != set(GAS_MOLAR_MASS_KG_MOL):
            raise ValueError("atmosphere must contain exactly the supported gas set")
        if any(not math.isfinite(value) or value < 0.0 or value > 1.0
               for value in self.mole_fractions.values()):
            raise ValueError("gas mole fractions must be finite and in [0, 1]")
        if not math.isclose(
            sum(self.mole_fractions.values()), 1.0, rel_tol=0.0, abs_tol=1.0e-12
        ):
            raise ValueError("gas mole fractions must sum to one")
        if self.column_mass_kg_m2 == 0.0 and any(self.mole_fractions.values()):
            # A composition may be retained as a prior for vacuum, but it must
            # not be interpreted as a physical inventory by consumers.
            return self
        return self

    @property
    def mean_molar_mass_kg_mol(self) -> float:
        return sum(
            self.mole_fractions[name] * molar_mass
            for name, molar_mass in GAS_MOLAR_MASS_KG_MOL.items()
        )

    def initial_species_column_mass(self) -> dict[str, float]:
        mean = self.mean_molar_mass_kg_mol
        if self.column_mass_kg_m2 == 0.0:
            return {name: 0.0 for name in GAS_MOLAR_MASS_KG_MOL}
        return {
            name: self.column_mass_kg_m2 * fraction
            * GAS_MOLAR_MASS_KG_MOL[name] / mean
            for name, fraction in self.mole_fractions.items()
        }


def atmosphere_inventory_from_world_prior(
    state: WorldInitialState,
) -> AtmosphereInventoryConfig:
    """Resolve a frozen G2/M7.1 atmosphere hypothesis into a column inventory."""
    planet = next((body for body in state.bodies if body.id == "planet-1"), None)
    if planet is None:
        raise ValueError("world atmosphere prior requires planet-1")
    prior = planet.properties.get("atmosphere_hypothesis")
    if not isinstance(prior, dict):
        raise ValueError("world has no versioned atmosphere_hypothesis")
    status = prior.get("status")
    mass = prior.get("atmosphere_mass_kg")
    composition = prior.get("composition_mole_fraction")
    if status not in {"PRESENT_PRIOR_SAMPLE", "ABSENT_PRIOR_SAMPLE"}:
        raise ValueError("world atmosphere prior status is unsupported")
    if isinstance(mass, bool) or not isinstance(mass, (int, float)):
        raise ValueError("world atmosphere prior mass is invalid")
    mass = float(mass)
    if not math.isfinite(mass) or mass < 0.0:
        raise ValueError("world atmosphere prior mass is invalid")
    if not isinstance(composition, dict):
        raise ValueError("world atmosphere prior composition is invalid")
    unknown = set(composition) - set(GAS_MOLAR_MASS_KG_MOL)
    if unknown:
        raise ValueError(f"world atmosphere prior contains unknown gases: {sorted(unknown)}")
    fractions = {
        name: float(composition.get(name, 0.0))
        for name in GAS_MOLAR_MASS_KG_MOL
    }
    total = sum(fractions.values())
    if mass == 0.0:
        fractions = {name: 0.0 for name in GAS_MOLAR_MASS_KG_MOL}
        fractions["nitrogen"] = 1.0
    elif not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1.0e-10):
        raise ValueError("world atmosphere prior mole fractions do not sum to one")
    else:
        fractions = {name: value / total for name, value in fractions.items()}
    column = mass / (4.0 * math.pi * planet.radius_m**2)
    return AtmosphereInventoryConfig(
        column_mass_kg_m2=column, mole_fractions=fractions
    )


class IntegratedEnvironmentConfig(StrictModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    model_id: Literal["integrated_environment_v1"] = "integrated_environment_v1"
    fidelity_mode: Literal[
        "FIVE_YEAR_REDUCED", "GAME_DEFAULT", "CONVERGENCE_FINE", "CUSTOM"
    ] = "GAME_DEFAULT"
    world_seed: int = Field(ge=0, le=2**32 - 1)
    latitude_count: int = Field(ge=4, le=96)
    longitude_count: int = Field(ge=8, le=192)
    planet_radius_m: float = Field(gt=0.0)
    surface_gravity_m_s2: float = Field(gt=0.0)
    rotation_axis: tuple[float, float, float]
    rotation_rate_rad_s: float = Field(gt=0.0, le=1.0)
    longitude_phase_rad: float = 0.0
    bond_albedo: float = Field(default=0.3, ge=0.0, le=1.0)
    emissivity: float = Field(default=0.95, gt=0.0, le=1.0)
    internal_heat_flux_w_m2: float = Field(default=0.08, ge=0.0)
    surface_material_column_mass_kg_m2: float = Field(gt=0.0)
    shallow_rock_column_mass_kg_m2: float = Field(gt=0.0)
    shallow_rock_specific_heat_j_kg_k: float = Field(gt=0.0)
    ocean_mixed_layer_column_mass_kg_m2: float = Field(ge=0.0)
    initial_surface_temperature_k: float = Field(gt=0.0)
    initial_rock_temperature_k: float = Field(gt=0.0)
    initial_ocean_temperature_k: float | None = Field(default=None, gt=0.0)
    initial_air_temperature_k: float = Field(gt=0.0)
    initial_surface_liquid_water_kg_m2: float = Field(default=0.0, ge=0.0)
    initial_surface_ice_kg_m2: float = Field(default=0.0, ge=0.0)
    material_profile: PlanetMaterialProfile
    atmosphere: AtmosphereInventoryConfig
    surface_rock_exchange_w_m2_k: float = Field(default=0.8, ge=0.0)
    surface_ocean_exchange_w_m2_k: float = Field(default=8.0, ge=0.0)
    surface_air_exchange_w_m2_k: float = Field(default=12.0, ge=0.0)
    horizontal_surface_exchange_w_m2_k: float = Field(default=0.2, ge=0.0)
    horizontal_air_exchange_w_m2_k: float = Field(default=0.5, ge=0.0)
    moisture_exchange_s_inv: float = Field(default=2.0e-6, ge=0.0)
    evaporation_coefficient_kg_m2_s_pa: float = Field(default=2.0e-10, ge=0.0)
    precipitation_timescale_s: float = Field(default=10800.0, gt=0.0)
    cloud_threshold_kg_m2: float = Field(default=0.1, gt=0.0)
    wind_relaxation_s: float = Field(default=21600.0, gt=0.0)
    surface_drag_s: float = Field(default=43200.0, gt=0.0)
    max_internal_step_s: float = Field(gt=0.0, le=21600.0)
    output_interval_s: float = Field(gt=0.0)
    segment_output_count: int = Field(default=64, ge=2, le=4096)
    max_orbit_sample_interval_s: float = Field(gt=0.0)

    @model_validator(mode="after")
    def validate_integrated_config(self) -> IntegratedEnvironmentConfig:
        norm = math.sqrt(sum(value * value for value in self.rotation_axis))
        if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=1.0e-10):
            raise ValueError("rotation_axis must be a unit vector")
        has_ocean = self.ocean_mixed_layer_column_mass_kg_m2 > 0.0
        if has_ocean != (self.initial_ocean_temperature_k is not None):
            raise ValueError("ocean temperature must exist exactly when ocean mass is positive")
        if not has_ocean and self.surface_ocean_exchange_w_m2_k != 0.0:
            raise ValueError("ocean exchange must be zero without an ocean mixed layer")
        expected = {
            "FIVE_YEAR_REDUCED": (6, 12, 3600.0, 86400.0),
            "GAME_DEFAULT": (12, 24, 900.0, 21600.0),
            "CONVERGENCE_FINE": (24, 48, 450.0, 21600.0),
        }.get(self.fidelity_mode)
        if expected is not None and (
            self.latitude_count, self.longitude_count,
            self.max_internal_step_s, self.output_interval_s,
        ) != expected:
            raise ValueError("fidelity mode grid and time settings are frozen by specification")
        return self

    @property
    def config_sha256(self) -> str:
        return sha256_json(self)


Quality = Literal["COMPUTED", "PARAMETERIZED", "UNCERTAIN", "UNAVAILABLE"]


class IntegratedCellState(StrictModel):
    cell_id: int = Field(ge=0)
    latitude_deg: float = Field(ge=-90.0, le=90.0)
    longitude_deg: float = Field(ge=-180.0, le=180.0)
    surface_temperature_k: float = Field(gt=0.0)
    rock_temperature_k: float = Field(gt=0.0)
    ocean_temperature_k: float | None = Field(default=None, gt=0.0)
    air_temperature_k: float | None = Field(default=None, gt=0.0)
    surface_pressure_pa: float = Field(ge=0.0)
    air_density_kg_m3: float | None = Field(default=None, ge=0.0)
    mean_molar_mass_kg_mol: float | None = Field(default=None, gt=0.0)
    gas_mole_fractions: dict[str, float]
    gas_partial_pressures_pa: dict[str, float]
    specific_humidity_kg_kg: float | None = Field(default=None, ge=0.0, le=1.0)
    relative_humidity: float | None = Field(default=None, ge=0.0)
    eastward_wind_m_s: float | None = None
    northward_wind_m_s: float | None = None
    cloud_fraction: float = Field(ge=0.0, le=1.0)
    precipitation_rate_kg_m2_s: float = Field(ge=0.0)
    precipitation_phase: Literal["NONE", "RAIN", "SNOW"]
    liquid_water_kg_m2: float = Field(ge=0.0)
    ice_kg_m2: float = Field(ge=0.0)
    water_vapor_kg_m2: float = Field(ge=0.0)
    supercritical_water_kg_m2: float = Field(ge=0.0)
    cloud_water_kg_m2: float = Field(ge=0.0)
    runoff_kg_m2: float = Field(ge=0.0)
    surface_material_liquid_fraction: float = Field(ge=0.0, le=1.0)
    magma_fraction: float = Field(ge=0.0, le=1.0)
    water_phase_regime: Literal[
        "ABSENT", "ICE_DOMINANT", "LIQUID_DOMINANT", "VAPOR_DOMINANT",
        "MIXED", "SUPERCRITICAL_UNCERTAIN",
    ]
    weather_state: Literal["CLEAR", "PARTLY_CLOUDY", "OVERCAST", "RAIN", "SNOW"]
    active_events: tuple[str, ...]
    quality: dict[str, Quality]
    uncertainty_flags: tuple[str, ...]


class IntegratedSnapshot(StrictModel):
    t_s: float
    time_index: int = Field(ge=0)
    cells: tuple[IntegratedCellState, ...]
    total_energy_j: float
    total_water_kg: float = Field(ge=0.0)
    total_dry_atmosphere_kg: float = Field(ge=0.0)


class EnvironmentEvent(StrictModel):
    event_id: str
    cell_id: int = Field(ge=0)
    event_type: Literal[
        "FOG", "STRONG_WIND", "HEATWAVE", "COLDWAVE", "HEAVY_PRECIPITATION",
        "THUNDERSTORM_CANDIDATE",
    ]
    start_t_s: float
    end_t_s: float | None = None
    status: Literal["ACTIVE", "ENDED"]
    confidence: Literal["PARAMETERIZED", "CANDIDATE_UNCERTAIN"]
    message: str


class IntegratedDiagnostics(StrictModel):
    maximum_scaled_energy_residual: float = Field(ge=0.0)
    maximum_water_mass_relative_residual: float = Field(ge=0.0)
    maximum_dry_atmosphere_relative_residual: float = Field(ge=0.0)
    maximum_material_mass_relative_residual: float = Field(ge=0.0)
    wind_stability_limit_hits: int = Field(ge=0)
    orbital_forcing_max_gap_s: float = Field(ge=0.0)
    internal_step_count: int = Field(ge=0)


class IntegratedEnvironmentResult(StrictModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    model_id: Literal["integrated_environment_v1"] = "integrated_environment_v1"
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    forcing_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_versions: dict[str, str]
    snapshots: tuple[IntegratedSnapshot, ...]
    events: tuple[EnvironmentEvent, ...]
    diagnostics: IntegratedDiagnostics
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
