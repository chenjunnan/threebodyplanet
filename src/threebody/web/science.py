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
