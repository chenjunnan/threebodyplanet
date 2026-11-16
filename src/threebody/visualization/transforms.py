from __future__ import annotations

import bisect
import html
import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

from threebody.errors import VisualizationError
from threebody.physics.nbody.models import SimulationEvent, TrajectorySample
from threebody.visualization.reader import TrajectoryArchive

AU_M = 149_597_870_700.0
SECONDS_PER_DAY = 86_400.0
DisplayUnits = Literal["au", "m"]


@dataclass(frozen=True)
class EventDisplayMarker:
    event_type: str
    t_s: float
    body_ids: tuple[str, ...]
    position_m: tuple[float, float, float]
    safe_hover_text: str
    interpolation_method: str = "piecewise_linear_sample_estimate"


def coordinate_scale(units: DisplayUnits) -> float:
    if units == "au":
        return AU_M
    if units == "m":
        return 1.0
    raise VisualizationError(f"unsupported display units: {units!r}")


def convert_position(
    position_m: tuple[float, float, float], units: DisplayUnits
) -> tuple[float, float, float]:
    scale = coordinate_scale(units)
    converted = tuple(float(value) / scale for value in position_m)
    if not all(math.isfinite(value) for value in converted):
        raise VisualizationError("display coordinate conversion produced a non-finite value")
    return converted


def _interpolate_body_position(
    samples: tuple[TrajectorySample, ...], body_id: str, t_s: float
) -> np.ndarray:
    times = [sample.t_s for sample in samples]
    if math.isclose(t_s, times[0], rel_tol=0.0, abs_tol=1.0e-9):
        body = next(body for body in samples[0].bodies if body.id == body_id)
        return np.asarray(body.position_m, dtype=np.float64)
    if math.isclose(t_s, times[-1], rel_tol=0.0, abs_tol=1.0e-9):
        body = next(body for body in samples[-1].bodies if body.id == body_id)
        return np.asarray(body.position_m, dtype=np.float64)
    right = bisect.bisect_left(times, t_s)
    if right < len(samples) and math.isclose(
        samples[right].t_s, t_s, rel_tol=0.0, abs_tol=1.0e-9
    ):
        body = next(body for body in samples[right].bodies if body.id == body_id)
        return np.asarray(body.position_m, dtype=np.float64)
    if right == 0 or right == len(samples):
        raise VisualizationError(f"event time {t_s} cannot be interpolated without extrapolation")
    first = samples[right - 1]
    second = samples[right]
    fraction = (t_s - first.t_s) / (second.t_s - first.t_s)
    first_body = next(body for body in first.bodies if body.id == body_id)
    second_body = next(body for body in second.bodies if body.id == body_id)
    position = (1.0 - fraction) * np.asarray(first_body.position_m) + fraction * np.asarray(
        second_body.position_m
    )
    if not np.all(np.isfinite(position)):
        raise VisualizationError("event display interpolation produced a non-finite position")
    return position


def event_display_marker(
    samples: tuple[TrajectorySample, ...], event: SimulationEvent
) -> EventDisplayMarker:
    positions = [
        _interpolate_body_position(samples, body_id, event.t_s)
        for body_id in event.body_ids
    ]
    if not positions:
        raise VisualizationError("event has no body IDs for display")
    position = np.mean(np.asarray(positions), axis=0)
    elapsed_s = event.t_s - samples[0].t_s
    body_text = ", ".join(html.escape(body_id) for body_id in event.body_ids)
    safe_hover = (
        f"<b>{html.escape(event.event_type)}</b><br>"
        f"delta t = {elapsed_s:.9g} s ({elapsed_s / SECONDS_PER_DAY:.9g} d)<br>"
        f"bodies: {body_text}<br>"
        f"method: {html.escape(event.detection_method)}<br>"
        f"message: {html.escape(event.message)}<br>"
        "position: sampling interpolation estimate, not exact physical contact"
    )
    return EventDisplayMarker(
        event_type=event.event_type,
        t_s=event.t_s,
        body_ids=event.body_ids,
        position_m=tuple(float(value) for value in position),
        safe_hover_text=safe_hover,
    )


def event_display_markers(archive: TrajectoryArchive) -> tuple[EventDisplayMarker, ...]:
    return tuple(event_display_marker(archive.samples, event) for event in archive.events)


def select_frame_indices(archive: TrajectoryArchive, frame_cap: int) -> tuple[int, ...]:
    sample_count = len(archive.samples)
    if frame_cap < 1:
        raise VisualizationError("frame_cap must be at least 1")
    if sample_count == 1:
        return (0,)
    times = [sample.t_s for sample in archive.samples]
    mandatory = {0, sample_count - 1}
    for event in archive.events:
        right = bisect.bisect_left(times, event.t_s)
        if right < sample_count:
            mandatory.add(right)
        if right > 0:
            mandatory.add(right - 1)
    if len(mandatory) > frame_cap:
        raise VisualizationError(
            f"frame_cap={frame_cap} cannot retain endpoints and all event-adjacent samples; "
            f"at least {len(mandatory)} frames are required"
        )
    if sample_count <= frame_cap:
        return tuple(range(sample_count))
    selected = set(mandatory)
    remaining = frame_cap - len(selected)
    for resolution in (remaining + 2, frame_cap * 4, sample_count):
        candidates = np.linspace(0, sample_count - 1, max(resolution, 2))[1:-1]
        for value in candidates:
            selected.add(int(round(float(value))))
            if len(selected) >= frame_cap:
                break
        if len(selected) >= frame_cap:
            break
    if len(selected) < frame_cap:
        for index in range(sample_count):
            selected.add(index)
            if len(selected) >= frame_cap:
                break
    return tuple(sorted(selected))
