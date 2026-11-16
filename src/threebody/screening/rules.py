from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from threebody.config.models import RuleConfig
from threebody.errors import ConfigError
from threebody.world.models import WorldInitialState

RULE_VERSION = "0.1.0"


class NoParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DensityParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    min_kg_m3: float = Field(gt=0)
    max_kg_m3: float = Field(gt=0)
    kinds: tuple[str, ...]

    @field_validator("kinds", mode="before")
    @classmethod
    def yaml_kinds_to_tuple(cls, value: Any) -> Any:
        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("kinds")
    @classmethod
    def valid_kinds(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or any(kind not in {"STAR", "PLANET"} for kind in value):
            raise ValueError("kinds must contain STAR and/or PLANET")
        return value

    @field_validator("max_kg_m3")
    @classmethod
    def finite_max(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("must be finite")
        return value


class OverlapParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    minimum_surface_gap_m: float


class BarycenterParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    max_relative_position_error: float = Field(ge=0)
    max_relative_velocity_error: float = Field(ge=0)


@dataclass(frozen=True)
class RuleDefinition:
    id: str
    params_model: type[BaseModel]
    evaluate: Callable[[WorldInitialState, BaseModel], tuple[bool, dict[str, Any], str]]


def _positive_mass(state: WorldInitialState, _: BaseModel) -> tuple[bool, dict[str, Any], str]:
    invalid = [body.id for body in state.bodies if body.mass_kg <= 0]
    message = "all masses are positive" if not invalid else "one or more masses are not positive"
    return not invalid, {"non_positive_body_ids": invalid}, message


def _positive_radius(state: WorldInitialState, _: BaseModel) -> tuple[bool, dict[str, Any], str]:
    invalid = [body.id for body in state.bodies if body.radius_m <= 0]
    message = "all radii are positive" if not invalid else "one or more radii are not positive"
    return not invalid, {"non_positive_body_ids": invalid}, message


def _density(state: WorldInitialState, params: BaseModel) -> tuple[bool, dict[str, Any], str]:
    assert isinstance(params, DensityParams)
    if params.min_kg_m3 > params.max_kg_m3:
        raise ValueError("min_kg_m3 must not exceed max_kg_m3")
    densities: dict[str, float] = {}
    invalid: list[str] = []
    for body in state.bodies:
        if body.kind.value not in params.kinds:
            continue
        if body.radius_m == 0:
            raise ValueError(f"density dependency missing: {body.id} has zero radius")
        value = 3.0 * body.mass_kg / (4.0 * math.pi * body.radius_m**3)
        if not math.isfinite(value):
            raise ValueError(f"density is non-finite for {body.id}")
        densities[body.id] = value
        if value < params.min_kg_m3 or value > params.max_kg_m3:
            invalid.append(body.id)
    if not densities:
        raise ValueError("density rule matched no bodies")
    observed = {"density_kg_m3": densities, "out_of_bounds_body_ids": invalid}
    message = (
        "densities are within configured bounds"
        if not invalid
        else "one or more densities are outside configured bounds"
    )
    return not invalid, observed, message


def _overlap(state: WorldInitialState, params: BaseModel) -> tuple[bool, dict[str, Any], str]:
    assert isinstance(params, OverlapParams)
    gaps: dict[str, float] = {}
    for first_index, first in enumerate(state.bodies):
        for second in state.bodies[first_index + 1:]:
            displacement = np.asarray(first.position_m) - np.asarray(second.position_m)
            distance = float(np.linalg.norm(displacement))
            gaps[f"{first.id}:{second.id}"] = distance - first.radius_m - second.radius_m
    minimum = min(gaps.values())
    passed = minimum >= params.minimum_surface_gap_m
    observed = {"minimum_surface_gap_m": minimum, "pair_surface_gaps_m": gaps}
    message = (
        "body surfaces meet the configured gap"
        if passed
        else "body surfaces overlap or violate the minimum gap"
    )
    return passed, observed, message


def _barycenter(state: WorldInitialState, params: BaseModel) -> tuple[bool, dict[str, Any], str]:
    assert isinstance(params, BarycenterParams)
    masses = np.asarray([body.mass_kg for body in state.bodies])
    positions = np.asarray([body.position_m for body in state.bodies])
    velocities = np.asarray([body.velocity_mps for body in state.bodies])
    total_mass = float(np.sum(masses))
    if total_mass == 0:
        raise ValueError("barycenter dependency invalid: total signed mass is zero")
    center_position = np.sum(masses[:, None] * positions, axis=0) / total_mass
    center_velocity = np.sum(masses[:, None] * velocities, axis=0) / total_mass
    position_scale = max(float(np.max(np.linalg.norm(positions, axis=1))), 1.0)
    velocity_scale = max(float(np.max(np.linalg.norm(velocities, axis=1))), 1.0)
    position_error = float(np.linalg.norm(center_position)) / position_scale
    velocity_error = float(np.linalg.norm(center_velocity)) / velocity_scale
    passed = (
        position_error <= params.max_relative_position_error
        and velocity_error <= params.max_relative_velocity_error
    )
    observed = {
        "relative_position_error": position_error,
        "relative_velocity_error": velocity_error,
    }
    message = (
        "barycenter normalization is within tolerance"
        if passed
        else "barycenter normalization exceeds tolerance"
    )
    return passed, observed, message


RULES = (
    RuleDefinition("body.mass.positive", NoParams, _positive_mass),
    RuleDefinition("body.radius.positive", NoParams, _positive_radius),
    RuleDefinition("body.density.bounds", DensityParams, _density),
    RuleDefinition("system.initial_overlap", OverlapParams, _overlap),
    RuleDefinition("system.barycenter", BarycenterParams, _barycenter),
)
RULE_REGISTRY = {rule.id: rule for rule in RULES}


def validate_rule_configs(configs: dict[str, RuleConfig]) -> None:
    missing = set(RULE_REGISTRY) - set(configs)
    unknown = set(configs) - set(RULE_REGISTRY)
    if missing or unknown:
        raise ConfigError(
            f"rule registry mismatch; missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    for rule in RULES:
        try:
            params = rule.params_model.model_validate(configs[rule.id].params, strict=True)
            if isinstance(params, DensityParams) and params.min_kg_m3 > params.max_kg_m3:
                raise ValueError("min_kg_m3 must not exceed max_kg_m3")
        except (ValidationError, ValueError) as exc:
            raise ConfigError(f"invalid parameters for rule {rule.id}: {exc}") from exc

