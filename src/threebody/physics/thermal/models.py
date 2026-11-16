from __future__ import annotations

import math
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class ThermalInputSettings(StrictModel):
    initial_state: str = Field(min_length=1)
    m2_archive: str = Field(min_length=1)
    allow_collision_terminated: bool = False
    max_orbit_sample_interval_s: float = Field(gt=0.0, allow_inf_nan=False)


class RotationSettings(StrictModel):
    axis_inertial_unit: tuple[float, float, float]
    prime_meridian_inertial_unit: tuple[float, float, float]
    rotation_epoch_s: float = Field(allow_inf_nan=False)
    initial_phase_rad: float = Field(allow_inf_nan=False)
    rotation_rate_rad_s: float = Field(allow_inf_nan=False)

    @field_validator("axis_inertial_unit", "prime_meridian_inertial_unit", mode="before")
    @classmethod
    def yaml_array_to_tuple(cls, value):
        if not isinstance(value, (list, tuple)):
            raise ValueError("rotation vector must be a three-element array")
        return tuple(value)

    @model_validator(mode="after")
    def validate_orthonormal_inputs(self) -> RotationSettings:
        axis = np.asarray(self.axis_inertial_unit, dtype=np.float64)
        prime = np.asarray(self.prime_meridian_inertial_unit, dtype=np.float64)
        if not np.all(np.isfinite(axis)) or not np.all(np.isfinite(prime)):
            raise ValueError("rotation vectors must be finite")
        if not math.isclose(float(np.linalg.norm(axis)), 1.0, abs_tol=1.0e-10):
            raise ValueError("axis_inertial_unit must already be a unit vector")
        if not math.isclose(float(np.linalg.norm(prime)), 1.0, abs_tol=1.0e-10):
            raise ValueError("prime_meridian_inertial_unit must already be a unit vector")
        if not math.isclose(float(np.dot(axis, prime)), 0.0, abs_tol=1.0e-10):
            raise ValueError("axis and prime meridian must already be orthogonal")
        return self


class PlanetSettings(StrictModel):
    id: str = Field(min_length=1)
    rotation: RotationSettings


class RadiationSettings(StrictModel):
    model: Literal["isotropic_bolometric_v1"] = "isotropic_bolometric_v1"
    occultation: Literal["disabled"] = "disabled"


class C1Settings(StrictModel):
    latitude_bands: int = Field(ge=4, le=720)
    diffusivity_w_m2_k: float = Field(ge=0.0, allow_inf_nan=False)


class ThermalIntegratorSettings(StrictModel):
    rtol: float = Field(gt=0.0, le=1.0e-2, allow_inf_nan=False)
    atol_k: float = Field(gt=0.0, allow_inf_nan=False)
    max_step_s: float = Field(gt=0.0, allow_inf_nan=False)


class ThermalModelSettings(StrictModel):
    mode: Literal["c0", "c1", "both"] = "both"
    initial_temperature_k: float = Field(gt=0.0, allow_inf_nan=False)
    bond_albedo: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    effective_emissivity: float = Field(gt=0.0, le=1.0, allow_inf_nan=False)
    areal_heat_capacity_j_m2_k: float = Field(gt=0.0, allow_inf_nan=False)
    internal_heat_flux_w_m2: float = Field(ge=0.0, allow_inf_nan=False)
    c1: C1Settings
    integrator: ThermalIntegratorSettings


class ThermalOutputSettings(StrictModel):
    sample_interval_s: float = Field(gt=0.0, allow_inf_nan=False)
    max_samples: int = Field(ge=2, le=1_000_000)


class ThermalSettings(StrictModel):
    schema_version: Literal["0.3.0"] = "0.3.0"
    input: ThermalInputSettings
    planet: PlanetSettings
    radiation: RadiationSettings
    thermal: ThermalModelSettings
    output: ThermalOutputSettings

