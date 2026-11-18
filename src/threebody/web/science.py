"""Verified bounded-memory M5A science read APIs; never extrapolate the solver."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from threebody.errors import ConfigError
from threebody.physics.longrun.reader import SegmentedTrajectoryReader

BODY_IDS = ("star-1", "star-2", "star-3", "planet-1")
MAX_DISPLAY_POINTS = 2000
MAX_DISPLAY_EVENTS = 10000


def query_trajectory(
    directory: Path, *, start_s: float | None, end_s: float | None,
    max_points: int = 500,
) -> dict[str, Any]:
    if max_points < 2 or max_points > MAX_DISPLAY_POINTS:
        raise ValueError(f"max_points must be within [2, {MAX_DISPLAY_POINTS}]")
    for value in (start_s, end_s):
        if value is not None and not math.isfinite(value):
            raise ValueError("time boundaries must be finite SI seconds")
    if start_s is not None and end_s is not None and start_s > end_s:
        raise ValueError("start_s cannot be greater than end_s")
    reader = SegmentedTrajectoryReader(directory)
    committed = [
        segment for segment in reader.manifest.segments
        if segment.status in {"COMMITTED", "TERMINAL"}
    ]
    if not committed:
        return {
            "run_id": reader.manifest.run_id,
            "world_id": reader.manifest.world_id,
            "samples": [], "sample_count": 0, "display_count": 0,
            "range_start_s": None, "range_end_s": None,
            "complete": False, "data_status": "NO_COMMITTED_TRAJECTORY",
            "units": {"time": "s", "position": "m"},
        }
    # Two sequential passes yield exact bounded-memory, deterministic uniform sample
    # selection. No display interpolation, and both interval endpoints are included.
    count = sum(1 for _ in reader.iter_samples(start_s=start_s, end_s=end_s))
    selected_indices = (
        set(range(count)) if count <= max_points
        else {(i * (count - 1)) // (max_points - 1) for i in range(max_points)}
    )
    points = []
    for index, sample in enumerate(reader.iter_samples(start_s=start_s, end_s=end_s)):
        if index not in selected_indices:
            continue
        ids = tuple(body.id for body in sample.bodies)
        if ids != BODY_IDS:
            raise ConfigError("M5A display sample contains unexpected body IDs or order")
        points.append({
            "t_s": sample.t_s,
            "positions_m": {
                body.id: list(body.position_m) for body in sample.bodies
            },
        })
    if len(points) != len(selected_indices):
        raise ConfigError("logical trajectory changed while querying the archive")
    return {
        "run_id": reader.manifest.run_id,
        "world_id": reader.manifest.world_id,
        "samples": points,
        "sample_count": count,
        "display_count": len(points),
        "range_start_s": points[0]["t_s"] if points else None,
        "range_end_s": points[-1]["t_s"] if points else None,
        "complete": reader.manifest.task.status in {"COMPLETED", "COLLISION"},
        "data_status": reader.manifest.task.status,
        "units": {"time": "s", "position": "m"},
        "display_sampling": "uniform_record_index_no_interpolation",
    }


def query_events(
    directory: Path, *, start_s: float | None, end_s: float | None,
) -> dict[str, Any]:
    for value in (start_s, end_s):
        if value is not None and not math.isfinite(value):
            raise ValueError("time boundaries must be finite SI seconds")
    if start_s is not None and end_s is not None and start_s > end_s:
        raise ValueError("start_s cannot be greater than end_s")
    reader = SegmentedTrajectoryReader(directory)
    events = []
    total_in_range = 0
    for event in reader.iter_events():
        if start_s is not None and event.t_s < start_s:
            continue
        if end_s is not None and event.t_s > end_s:
            break
        total_in_range += 1
        if total_in_range > MAX_DISPLAY_EVENTS:
            raise ValueError(
                "event range exceeds safety cap; request a smaller time interval"
            )
        events.append(event.model_dump(mode="json"))
    return {
        "run_id": reader.manifest.run_id,
        "world_id": reader.manifest.world_id,
        "events": events,
        "event_count": len(events),
        "range_type": "committed_m5a_events_only",
        "escape_semantics": "candidate_not_proven_permanent_escape",
    }


def query_body_metadata(directory: Path) -> dict[str, Any]:
    """Read-only body properties from fully verified, immutable M5A initial state.

    All displayed masses, radii and rotation axes come from the actual archived
    world, never from a visual-size or guessed stellar color heuristic.
    """
    from threebody.common.json import sha256_json
    from threebody.world.models import WorldInitialState

    reader = SegmentedTrajectoryReader(directory)
    initial_path = directory / "initial_state.json"
    if initial_path.is_symlink():
        raise ConfigError("initial-state symlink forbidden")
    try:
        initial = WorldInitialState.model_validate_json(
            initial_path.read_text(encoding="utf-8"), strict=True
        )
    except (OSError, ValueError) as exc:
        raise ConfigError("cannot validate M5A initial state") from exc
    if sha256_json(initial) != reader.manifest.initial_state_sha256:
        raise ConfigError("M5A initial-state hash mismatch")
    if initial.world_id != reader.manifest.world_id:
        raise ConfigError("M5A initial world ID mismatch")
    if initial.units != "SI" or initial.coordinate_frame != "BARYCENTRIC_INERTIAL":
        raise ConfigError("M5A initial-state units/frame unsupported")
    if tuple(body.id for body in initial.bodies) != BODY_IDS:
        raise ConfigError("M5A bodies have unexpected order")
    return {
        "schema_version": "m6-3c-body-metadata-v1",
        "run_id": reader.manifest.run_id,
        "world_id": reader.manifest.world_id,
        "initial_state_sha256": reader.manifest.initial_state_sha256,
        "units": {"mass": "kg", "radius": "m", "rotation": "rad/s"},
        "coordinate_frame": initial.coordinate_frame,
        "bodies": [
            {
                "id": body.id,
                "kind": body.kind.value,
                "mass_kg": body.mass_kg,
                "radius_m": body.radius_m,
                "rotation_rate_rad_s": body.properties.get("rotation_rate_rad_s"),
                "rotation_axis_inertial_unit":
                    body.properties.get("rotation_axis_inertial_unit"),
                "luminosity_w": body.properties.get("luminosity_w"),
                "atmosphere_hypothesis": body.properties.get("atmosphere_hypothesis"),
                "physical_profile_id": body.physical_profile_id,
            }
            for body in initial.bodies
        ],
        "data_semantics": "VERIFIED_INITIAL_STATE_NOT_EVOLVED_PROPERTIES",
    }
