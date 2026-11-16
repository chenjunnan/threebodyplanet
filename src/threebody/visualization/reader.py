from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from threebody.errors import VisualizationError
from threebody.physics.nbody.models import (
    IntegrationResult,
    SimulationEvent,
    TrajectorySample,
)

CANONICAL_BODY_IDS = ("star-1", "star-2", "star-3", "planet-1")
TIME_ABSOLUTE_TOLERANCE_S = 1.0e-9
ABSOLUTE_MAX_INPUT_SAMPLES = 1_000_000


@dataclass(frozen=True)
class TrajectoryArchive:
    run_dir: Path
    manifest: IntegrationResult
    samples: tuple[TrajectorySample, ...]
    events: tuple[SimulationEvent, ...]

    @property
    def is_partial(self) -> bool:
        return self.manifest.status in {"STOPPED", "FAILED"}


def _read_manifest(path: Path) -> IntegrationResult:
    try:
        return IntegrationResult.model_validate_json(
            path.read_text(encoding="utf-8"), strict=True
        )
    except OSError as exc:
        raise VisualizationError(f"cannot read M2 manifest {path}: {exc}") from exc
    except (UnicodeError, ValidationError) as exc:
        raise VisualizationError(f"invalid M2 manifest {path}: {exc}") from exc


def _read_samples(path: Path, maximum: int) -> tuple[TrajectorySample, ...]:
    samples: list[TrajectorySample] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    raise VisualizationError(
                        f"blank trajectory line {line_number} is not allowed"
                    )
                if len(samples) >= maximum:
                    raise VisualizationError(
                        f"trajectory exceeds max_input_samples={maximum}"
                    )
                try:
                    sample = TrajectorySample.model_validate_json(line, strict=True)
                except ValidationError as exc:
                    raise VisualizationError(
                        f"invalid trajectory line {line_number}: {exc}"
                    ) from exc
                samples.append(sample)
    except VisualizationError:
        raise
    except (OSError, UnicodeError) as exc:
        raise VisualizationError(f"cannot read M2 trajectory {path}: {exc}") from exc
    if not samples:
        raise VisualizationError("trajectory contains no complete samples to display")
    return tuple(samples)


def _read_events(path: Path) -> tuple[SimulationEvent, ...]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise VisualizationError(f"cannot read M2 events {path}: {exc}") from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise VisualizationError(f"invalid M2 events JSON {path}: {exc}") from exc
    if not isinstance(raw, list):
        raise VisualizationError("events.json root must be an array")
    events: list[SimulationEvent] = []
    for index, value in enumerate(raw):
        try:
            # Validate through JSON so JSON arrays are accepted for tuple fields while
            # scalar types remain strict, matching the M2 on-disk contract.
            events.append(
                SimulationEvent.model_validate_json(
                    json.dumps(value, ensure_ascii=False, allow_nan=False), strict=True
                )
            )
        except (TypeError, ValueError) as exc:
            raise VisualizationError(f"invalid event at index {index}: {exc}") from exc
    return tuple(events)


def _same_time(actual: float, expected: float) -> bool:
    return math.isclose(
        actual,
        expected,
        rel_tol=0.0,
        abs_tol=TIME_ABSOLUTE_TOLERANCE_S,
    )


def _validate_archive(archive: TrajectoryArchive) -> None:
    manifest = archive.manifest
    samples = archive.samples
    events = archive.events
    if len(samples) != manifest.sample_count:
        raise VisualizationError(
            f"manifest sample_count={manifest.sample_count} but trajectory has {len(samples)}"
        )
    if len(events) != manifest.event_count:
        raise VisualizationError(
            f"manifest event_count={manifest.event_count} but events.json has {len(events)}"
        )
    previous = -math.inf
    for index, sample in enumerate(samples):
        ids = tuple(body.id for body in sample.bodies)
        if ids != CANONICAL_BODY_IDS:
            raise VisualizationError(
                f"sample {index} body IDs/order must be {CANONICAL_BODY_IDS}, got {ids}"
            )
        if sample.t_s <= previous:
            raise VisualizationError("trajectory times must be strictly increasing")
        previous = sample.t_s
    if not _same_time(samples[0].t_s, manifest.t_start_s):
        raise VisualizationError(
            "trajectory first time does not match manifest t_start_s within "
            f"{TIME_ABSOLUTE_TOLERANCE_S} s"
        )
    if not _same_time(samples[-1].t_s, manifest.t_final_s):
        raise VisualizationError(
            "trajectory final time does not match manifest t_final_s within "
            f"{TIME_ABSOLUTE_TOLERANCE_S} s"
        )
    valid_ids = set(CANONICAL_BODY_IDS)
    start = samples[0].t_s
    end = samples[-1].t_s
    for index, event in enumerate(events):
        if not event.body_ids or any(body_id not in valid_ids for body_id in event.body_ids):
            raise VisualizationError(f"event {index} contains unknown body IDs")
        if len(set(event.body_ids)) != len(event.body_ids):
            raise VisualizationError(f"event {index} contains duplicate body IDs")
        if event.t_s < start - TIME_ABSOLUTE_TOLERANCE_S or event.t_s > (
            end + TIME_ABSOLUTE_TOLERANCE_S
        ):
            raise VisualizationError(
                f"event {index} time {event.t_s} is outside trajectory interval [{start}, {end}]"
            )


def load_m2_archive(
    run_dir: str | Path, *, max_samples: int = 100_000
) -> TrajectoryArchive:
    directory = Path(run_dir)
    if max_samples < 1 or max_samples > ABSOLUTE_MAX_INPUT_SAMPLES:
        raise VisualizationError(
            f"max_samples must be within [1, {ABSOLUTE_MAX_INPUT_SAMPLES}]"
        )
    if not directory.is_dir():
        raise VisualizationError(f"M2 run directory does not exist: {directory}")
    archive = TrajectoryArchive(
        run_dir=directory,
        manifest=_read_manifest(directory / "simulation_manifest.json"),
        samples=_read_samples(directory / "trajectory.jsonl", max_samples),
        events=_read_events(directory / "events.json"),
    )
    _validate_archive(archive)
    return archive
