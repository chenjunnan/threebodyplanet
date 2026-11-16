from __future__ import annotations

import math
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BodyKind(StrEnum):
    STAR = "STAR"
    PLANET = "PLANET"


def _finite_tree(value: Any, path: str = "properties") -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{path} contains a non-finite number")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} keys must be strings")
            _finite_tree(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _finite_tree(item, f"{path}[{index}]")
    elif not isinstance(value, (str, int, float, bool, type(None))):
        raise ValueError(f"{path} contains an unsupported object")
    return value


class CelestialBody(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str = Field(min_length=1)
    kind: BodyKind
    mass_kg: float
    radius_m: float
    position_m: tuple[float, float, float]
    velocity_mps: tuple[float, float, float]
    physical_profile_id: str = Field(min_length=1)
    properties: dict[str, Any] = Field(default_factory=dict)

    @field_validator("mass_kg", "radius_m")
    @classmethod
    def finite_scalar(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("must be finite")
        return value

    @field_validator("position_m", "velocity_mps")
    @classmethod
    def finite_vector(cls, value: tuple[float, float, float]) -> tuple[float, float, float]:
        if not all(math.isfinite(component) for component in value):
            raise ValueError("all vector components must be finite")
        return value

    @field_validator("properties")
    @classmethod
    def safe_properties(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _finite_tree(value)


class WorldInitialState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: str = "0.1.0"
    world_id: str = Field(min_length=1)
    seed: int = Field(ge=0, le=4_294_967_295)
    candidate_index: int = Field(ge=0)
    generator_id: str
    generator_version: str
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    units: str = "SI"
    coordinate_frame: str = "BARYCENTRIC_INERTIAL"
    reference_epoch_s: float = 0.0
    bodies: tuple[CelestialBody, CelestialBody, CelestialBody, CelestialBody]

    @field_validator("reference_epoch_s")
    @classmethod
    def finite_epoch(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("must be finite")
        return value

