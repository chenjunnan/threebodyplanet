from __future__ import annotations

import math
from typing import Any

from threebody.world.models import BodyKind, WorldInitialState

EXPECTED_IDS = ("star-1", "star-2", "star-3", "planet-1")


def _non_finite_paths(value: Any, path: str) -> list[str]:
    if isinstance(value, float):
        return [] if math.isfinite(value) else [path]
    if isinstance(value, dict):
        return [
            nested
            for key, item in value.items()
            for nested in _non_finite_paths(item, f"{path}.{key}")
        ]
    if isinstance(value, (list, tuple)):
        return [
            nested
            for index, item in enumerate(value)
            for nested in _non_finite_paths(item, f"{path}[{index}]")
        ]
    return []


def validate_s0(state: WorldInitialState) -> list[str]:
    errors: list[str] = []
    if state.schema_version != "0.1.0":
        errors.append(f"unsupported state schema_version: {state.schema_version}")
    if state.units != "SI":
        errors.append("units must be SI")
    if state.coordinate_frame != "BARYCENTRIC_INERTIAL":
        errors.append("coordinate_frame must be BARYCENTRIC_INERTIAL")
    ids = tuple(body.id for body in state.bodies)
    if ids != EXPECTED_IDS:
        errors.append(f"body ids/order must be {EXPECTED_IDS!r}")
    if len(set(ids)) != 4:
        errors.append("body ids must be unique")
    kinds = tuple(body.kind for body in state.bodies)
    if kinds.count(BodyKind.STAR) != 3 or kinds.count(BodyKind.PLANET) != 1:
        errors.append("state must contain exactly three STAR and one PLANET")
    if state.world_id != f"candidate-{state.candidate_index:06d}":
        errors.append("world_id does not match candidate_index")
    non_finite = _non_finite_paths(state.reference_epoch_s, "reference_epoch_s")
    for index, body in enumerate(state.bodies):
        prefix = f"bodies[{index}]"
        non_finite.extend(_non_finite_paths(body.mass_kg, f"{prefix}.mass_kg"))
        non_finite.extend(_non_finite_paths(body.radius_m, f"{prefix}.radius_m"))
        non_finite.extend(_non_finite_paths(body.position_m, f"{prefix}.position_m"))
        non_finite.extend(_non_finite_paths(body.velocity_mps, f"{prefix}.velocity_mps"))
        non_finite.extend(_non_finite_paths(body.properties, f"{prefix}.properties"))
    if non_finite:
        errors.append(f"non-finite core numeric value(s): {', '.join(non_finite)}")
    return errors

