from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from threebody.common.json import sha256_json


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, allow_inf_nan=False
    )


class ThermalReservoir(StrictModel):
    mass_kg: float = Field(gt=0.0, allow_inf_nan=False)
    specific_heat_j_kg_k: float = Field(gt=0.0, allow_inf_nan=False)
    initial_temperature_k: float = Field(gt=0.0, allow_inf_nan=False)
    material: str = Field(min_length=1, max_length=64)

    @property
    def heat_capacity_j_k(self) -> float:
        return self.mass_kg * self.specific_heat_j_kg_k


class OptionalOceanReservoir(StrictModel):
    mass_kg: float = Field(ge=0.0, allow_inf_nan=False)
    specific_heat_j_kg_k: float = Field(gt=0.0, allow_inf_nan=False)
    initial_temperature_k: float | None = Field(default=None, gt=0.0)
    material: Literal["water_mixed_layer"] = "water_mixed_layer"

    @model_validator(mode="after")
    def temperature_matches_inventory(self) -> OptionalOceanReservoir:
        if (self.mass_kg == 0.0) != (self.initial_temperature_k is None):
            raise ValueError(
                "ocean initial_temperature_k must be present exactly when mass_kg > 0"
            )
        return self

    @property
    def heat_capacity_j_k(self) -> float:
        return self.mass_kg * self.specific_heat_j_kg_k


class EnvironmentIntegratorConfig(StrictModel):
    method: Literal["RK45", "DOP853"] = "DOP853"
    rtol: float = Field(default=1.0e-9, ge=1.0e-13, le=1.0e-3)
    atol_k: float = Field(default=1.0e-9, gt=0.0, le=1.0)
    max_step_s: float = Field(default=3600.0, gt=0.0)


class DynamicEnvironmentConfig(StrictModel):
    schema_version: Literal["0.8.0"] = "0.8.0"
    model_id: Literal["m72_three_reservoir_v1"] = "m72_three_reservoir_v1"
    planet_radius_m: float = Field(gt=0.0)
    bond_albedo: float = Field(default=0.3, ge=0.0, le=1.0)
    emissivity: float = Field(default=1.0, gt=0.0, le=1.0)
    internal_heat_flux_w_m2: float = Field(default=0.0, ge=0.0)
    surface: ThermalReservoir
    shallow_rock: ThermalReservoir
    ocean_mixed_layer: OptionalOceanReservoir
    surface_rock_exchange_w_m2_k: float = Field(default=1.0, ge=0.0)
    surface_ocean_exchange_w_m2_k: float = Field(default=0.0, ge=0.0)
    max_orbit_sample_interval_s: float = Field(default=86400.0, gt=0.0)
    max_output_samples: int = Field(default=1_000_000, ge=2, le=100_000_000)
    integrator: EnvironmentIntegratorConfig = Field(
        default_factory=EnvironmentIntegratorConfig
    )

    @model_validator(mode="after")
    def physically_consistent_inventory(self) -> DynamicEnvironmentConfig:
        if (
            self.ocean_mixed_layer.mass_kg == 0.0
            and self.surface_ocean_exchange_w_m2_k != 0.0
        ):
            raise ValueError("ocean exchange must be zero when ocean inventory is zero")
        return self

    @property
    def config_sha256(self) -> str:
        return sha256_json(self)


class EnvironmentForcingValue(StrictModel):
    t_s: float
    top_of_atmosphere_flux_w_m2: float = Field(ge=0.0)
    absorbed_flux_w_m2: float = Field(ge=0.0)
    interpolation: Literal["CONSTANT", "STEP", "EXACT_SAMPLE", "LINEAR_FLUX"]
    source_bracket_s: tuple[float, float] | None = None


class EnvironmentFluxes(StrictModel):
    absorbed_stellar_w_m2: float = Field(ge=0.0)
    emitted_thermal_w_m2: float = Field(ge=0.0)
    internal_heat_w_m2: float = Field(ge=0.0)
    surface_to_rock_w_m2: float
    surface_to_ocean_w_m2: float
    net_external_w_m2: float


class DynamicEnvironmentState(StrictModel):
    t_s: float
    surface_temperature_k: float = Field(gt=0.0)
    shallow_rock_temperature_k: float = Field(gt=0.0)
    ocean_mixed_layer_temperature_k: float | None = Field(default=None, gt=0.0)
    surface_heat_capacity_j_k: float = Field(gt=0.0)
    shallow_rock_heat_capacity_j_k: float = Field(gt=0.0)
    ocean_mixed_layer_heat_capacity_j_k: float = Field(ge=0.0)
    surface_mass_kg: float = Field(gt=0.0)
    shallow_rock_mass_kg: float = Field(gt=0.0)
    ocean_mixed_layer_mass_kg: float = Field(ge=0.0)
    fluxes: EnvironmentFluxes
    total_sensible_energy_j: float


class EnvironmentDiagnostics(StrictModel):
    maximum_absolute_energy_residual_j: float = Field(ge=0.0)
    maximum_scaled_energy_residual: float = Field(ge=0.0)
    minimum_temperature_k: float = Field(gt=0.0)
    maximum_temperature_k: float = Field(gt=0.0)
    solver_nfev: int = Field(ge=0)


class DynamicEnvironmentResult(StrictModel):
    schema_version: Literal["0.8.0"] = "0.8.0"
    model_id: Literal["m72_three_reservoir_v1"] = "m72_three_reservoir_v1"
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    forcing_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    forcing_kind: str = Field(min_length=1)
    runtime_versions: dict[str, str]
    states: tuple[DynamicEnvironmentState, ...]
    diagnostics: EnvironmentDiagnostics
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    physics_semantics: Literal["PHYSICAL_PARAMETERIZED_THREE_RESERVOIR"] = (
        "PHYSICAL_PARAMETERIZED_THREE_RESERVOIR"
    )

