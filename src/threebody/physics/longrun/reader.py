from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from pydantic import ValidationError

from threebody.errors import ConfigError
from threebody.physics.longrun.archive import verify_run_archive
from threebody.physics.nbody.models import SimulationEvent, TrajectorySample


class SegmentedTrajectoryReader:
    """Verified, bounded-memory logical view over immutable M5A segments."""

    def __init__(self, run_dir: str | Path):
        self.run_dir = Path(run_dir)
        self.manifest = verify_run_archive(self.run_dir)

    def iter_samples(
        self,
        *,
        start_s: float | None = None,
        end_s: float | None = None,
    ) -> Iterator[TrajectorySample]:
        if start_s is not None and end_s is not None and end_s < start_s:
            raise ConfigError("trajectory range end precedes its start")
        previous_time = None
        for entry in self.manifest.segments:
            if entry.status not in {"COMMITTED", "TERMINAL"}:
                break
            path = self.run_dir / "segments" / f"{entry.segment_index:08d}" / "trajectory.jsonl"
            try:
                with path.open(encoding="utf-8") as handle:
                    for _line_number, line in enumerate(handle, 1):
                        sample = TrajectorySample.model_validate_json(line, strict=True)
                        if previous_time is not None and sample.t_s <= previous_time:
                            raise ConfigError(
                                "logical segmented trajectory is not strictly increasing"
                            )
                        previous_time = sample.t_s
                        if start_s is not None and sample.t_s < start_s:
                            continue
                        if end_s is not None and sample.t_s > end_s:
                            return
                        yield sample
            except OSError as exc:
                raise ConfigError(f"cannot read segment trajectory {path}: {exc}") from exc
            except ValidationError as exc:
                raise ConfigError(
                    f"invalid trajectory sample {path}:{_line_number}: {exc}"
                ) from exc

    def iter_events(self) -> Iterator[SimulationEvent]:
        previous_time = None
        for entry in self.manifest.segments:
            if entry.status not in {"COMMITTED", "TERMINAL"}:
                break
            path = self.run_dir / "segments" / f"{entry.segment_index:08d}" / "events.json"
            try:
                import json

                raw = json.loads(path.read_text(encoding="utf-8"))
                events = tuple(
                    SimulationEvent.model_validate_json(
                        json.dumps(value, allow_nan=False), strict=True
                    )
                    for value in raw
                )
            except (OSError, ValueError, ValidationError) as exc:
                raise ConfigError(f"invalid segment events {path}: {exc}") from exc
            for event in events:
                if previous_time is not None and event.t_s < previous_time:
                    raise ConfigError("logical event stream is not time ordered")
                previous_time = event.t_s
                yield event
