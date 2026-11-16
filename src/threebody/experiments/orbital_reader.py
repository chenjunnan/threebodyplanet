from __future__ import annotations

import bisect
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from pydantic import ValidationError

from threebody.common.json import sha256_json
from threebody.errors import ConfigError
from threebody.physics.nbody.models import (
    BodyTrajectoryState,
    IntegrationResult,
    TrajectorySample,
)
from threebody.world.models import WorldInitialState


@dataclass(frozen=True)
class OrbitInterpolationDiagnostics:
    algorithm: str
    version: str
    maximum_sample_interval_s: float
    minimum_observed_pair_distance_m: float
    maximum_midpoint_hermite_linear_relative_difference: float
    scientific_warning: str


@dataclass(frozen=True)
class EphemerisSample:
    t_s: float
    bodies: tuple[BodyTrajectoryState, ...]


class M2TrajectoryReader:
    """Validated, bounded in-memory M2 reader with cubic Hermite interpolation."""

    algorithm = "piecewise_cubic_hermite_position_velocity"
    version = "1.0.0"

    def __init__(
        self,
        initial_path: str | Path,
        archive_path: str | Path,
        *,
        allow_collision_terminated: bool = False,
        max_sample_interval_s: float,
    ) -> None:
        self.initial_path = Path(initial_path)
        self.archive_path = Path(archive_path)
        self.initial_state = self._load_initial()
        self.manifest = self._load_manifest()
        self.manifest_sha256 = sha256_json(self.manifest)
        self._validate_status(allow_collision_terminated)
        self.samples = self._load_samples()
        self.body_ids = tuple(body.id for body in self.initial_state.bodies)
        self._validate_samples(max_sample_interval_s)
        self._times = tuple(sample.t_s for sample in self.samples)
        self.diagnostics = self._interpolation_diagnostics()

    @property
    def t_start_s(self) -> float:
        return self._times[0]

    @property
    def t_end_s(self) -> float:
        return self._times[-1]

    def _load_initial(self) -> WorldInitialState:
        try:
            return WorldInitialState.model_validate_json(
                self.initial_path.read_text(encoding="utf-8"), strict=True
            )
        except OSError as exc:
            raise ConfigError(f"cannot read M2 initial state {self.initial_path}: {exc}") from exc
        except ValidationError as exc:
            raise ConfigError(f"invalid M2 initial state {self.initial_path}: {exc}") from exc

    def _load_manifest(self) -> IntegrationResult:
        path = self.archive_path / "simulation_manifest.json"
        try:
            return IntegrationResult.model_validate_json(
                path.read_text(encoding="utf-8"), strict=True
            )
        except OSError as exc:
            raise ConfigError(f"cannot read M2 manifest {path}: {exc}") from exc
        except ValidationError as exc:
            raise ConfigError(f"invalid M2 manifest {path}: {exc}") from exc

    def _validate_status(self, allow_collision_terminated: bool) -> None:
        if self.initial_state.units != "SI":
            raise ConfigError("M3 requires an SI M2 initial state")
        if self.initial_state.coordinate_frame != "BARYCENTRIC_INERTIAL":
            raise ConfigError("M3 requires the M2 barycentric inertial coordinate frame")
        if self.manifest.world_id != self.initial_state.world_id:
            raise ConfigError("M2 manifest world_id does not match initial state")
        if self.manifest.t_start_s != self.initial_state.reference_epoch_s:
            raise ConfigError("M2 manifest start time does not match initial reference epoch")
        if self.manifest.initial_state_sha256 != sha256_json(self.initial_state):
            raise ConfigError("M2 manifest initial_state_sha256 does not match initial state")
        if self.manifest.status == "COMPLETED":
            return
        if self.manifest.status == "COLLISION" and allow_collision_terminated:
            return
        if self.manifest.status == "COLLISION":
            raise ConfigError("collision-terminated M2 archive requires explicit opt-in")
        raise ConfigError(f"M2 archive status {self.manifest.status} is not usable for M3")

    def _load_samples(self) -> tuple[TrajectorySample, ...]:
        path = self.archive_path / "trajectory.jsonl"
        samples: list[TrajectorySample] = []
        try:
            with path.open(encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, 1):
                    if not line.strip():
                        raise ConfigError(f"blank trajectory line {line_number}")
                    try:
                        samples.append(TrajectorySample.model_validate_json(line, strict=True))
                    except ValidationError as exc:
                        raise ConfigError(
                            f"invalid trajectory line {line_number}: {exc}"
                        ) from exc
                    except json.JSONDecodeError as exc:
                        raise ConfigError(
                            f"invalid trajectory JSON line {line_number}: {exc}"
                        ) from exc
        except OSError as exc:
            raise ConfigError(f"cannot read M2 trajectory {path}: {exc}") from exc
        if len(samples) < 2:
            raise ConfigError("M2 trajectory requires at least two samples for interpolation")
        return tuple(samples)

    def _validate_samples(self, maximum_interval_s: float) -> None:
        if not math.isfinite(maximum_interval_s) or maximum_interval_s <= 0.0:
            raise ConfigError("max_sample_interval_s must be finite and positive")
        expected_ids = tuple(body.id for body in self.initial_state.bodies)
        if len(self.samples) != self.manifest.sample_count:
            raise ConfigError("trajectory sample count does not match M2 manifest")
        previous_time = -math.inf
        maximum_interval = 0.0
        for index, sample in enumerate(self.samples):
            ids = tuple(body.id for body in sample.bodies)
            if ids != expected_ids:
                raise ConfigError(f"trajectory body ID order mismatch at sample {index}")
            if sample.t_s <= previous_time:
                raise ConfigError("trajectory sample times must be strictly increasing")
            if index:
                maximum_interval = max(maximum_interval, sample.t_s - previous_time)
            previous_time = sample.t_s
        if self.samples[0].t_s != self.manifest.t_start_s:
            raise ConfigError("trajectory start time does not match M2 manifest")
        if self.samples[-1].t_s != self.manifest.t_final_s:
            raise ConfigError("trajectory final time does not match M2 manifest")
        if maximum_interval > maximum_interval_s:
            raise ConfigError(
                f"M2 sample interval {maximum_interval} s exceeds configured maximum "
                f"{maximum_interval_s} s; rerun M2 with more frequent output"
            )
        first = self.samples[0]
        for initial_body, trajectory_body in zip(
            self.initial_state.bodies, first.bodies, strict=True
        ):
            if (
                trajectory_body.position_m != initial_body.position_m
                or trajectory_body.velocity_mps != initial_body.velocity_mps
            ):
                raise ConfigError("first trajectory sample does not reproduce initial state")

    def sample(self, t_s: float) -> EphemerisSample:
        if not math.isfinite(t_s):
            raise ConfigError("ephemeris time must be finite")
        if t_s < self.t_start_s or t_s > self.t_end_s:
            raise ConfigError(
                f"ephemeris time {t_s} is outside [{self.t_start_s}, {self.t_end_s}]"
            )
        node = bisect.bisect_left(self._times, t_s)
        if node < len(self._times) and self._times[node] == t_s:
            sample = self.samples[node]
            return EphemerisSample(t_s=t_s, bodies=sample.bodies)
        right = bisect.bisect_right(self._times, t_s)
        left = right - 1
        return self._interpolate_interval(left, t_s)

    def _interpolate_interval(self, left: int, t_s: float) -> EphemerisSample:
        first = self.samples[left]
        second = self.samples[left + 1]
        interval = second.t_s - first.t_s
        fraction = (t_s - first.t_s) / interval
        f2 = fraction * fraction
        f3 = f2 * fraction
        h00 = 2.0 * f3 - 3.0 * f2 + 1.0
        h10 = f3 - 2.0 * f2 + fraction
        h01 = -2.0 * f3 + 3.0 * f2
        h11 = f3 - f2
        dh00 = (6.0 * f2 - 6.0 * fraction) / interval
        dh10 = 3.0 * f2 - 4.0 * fraction + 1.0
        dh01 = (-6.0 * f2 + 6.0 * fraction) / interval
        dh11 = 3.0 * f2 - 2.0 * fraction
        bodies: list[BodyTrajectoryState] = []
        for body_id, a, b in zip(self.body_ids, first.bodies, second.bodies, strict=True):
            p0 = np.asarray(a.position_m)
            p1 = np.asarray(b.position_m)
            v0 = np.asarray(a.velocity_mps)
            v1 = np.asarray(b.velocity_mps)
            position = h00 * p0 + h10 * interval * v0 + h01 * p1 + h11 * interval * v1
            velocity = dh00 * p0 + dh10 * v0 + dh01 * p1 + dh11 * v1
            if not np.all(np.isfinite(position)) or not np.all(np.isfinite(velocity)):
                raise ConfigError("Hermite interpolation produced a non-finite state")
            bodies.append(
                BodyTrajectoryState(
                    id=body_id,
                    position_m=tuple(float(value) for value in position),
                    velocity_mps=tuple(float(value) for value in velocity),
                )
            )
        return EphemerisSample(t_s=t_s, bodies=tuple(bodies))

    def _interpolation_diagnostics(self) -> OrbitInterpolationDiagnostics:
        maximum_interval = 0.0
        minimum_distance = math.inf
        maximum_relative_difference = 0.0
        for index, (first, second) in enumerate(
            zip(self.samples, self.samples[1:], strict=False)
        ):
            interval = second.t_s - first.t_s
            maximum_interval = max(maximum_interval, interval)
            midpoint = self._interpolate_interval(index, first.t_s + 0.5 * interval)
            for a in range(len(self.body_ids)):
                for b in range(a + 1, len(self.body_ids)):
                    h_relative = np.asarray(midpoint.bodies[a].position_m) - np.asarray(
                        midpoint.bodies[b].position_m
                    )
                    linear_a = 0.5 * (
                        np.asarray(first.bodies[a].position_m)
                        + np.asarray(second.bodies[a].position_m)
                    )
                    linear_b = 0.5 * (
                        np.asarray(first.bodies[b].position_m)
                        + np.asarray(second.bodies[b].position_m)
                    )
                    distance = float(np.linalg.norm(h_relative))
                    first_distance = float(
                        np.linalg.norm(
                            np.asarray(first.bodies[a].position_m)
                            - np.asarray(first.bodies[b].position_m)
                        )
                    )
                    second_distance = float(
                        np.linalg.norm(
                            np.asarray(second.bodies[a].position_m)
                            - np.asarray(second.bodies[b].position_m)
                        )
                    )
                    difference = float(np.linalg.norm(h_relative - (linear_a - linear_b)))
                    if not all(
                        math.isfinite(value)
                        for value in (distance, first_distance, second_distance, difference)
                    ):
                        raise ConfigError("orbit interpolation diagnostic is non-finite")
                    minimum_distance = min(
                        minimum_distance, distance, first_distance, second_distance
                    )
                    maximum_relative_difference = max(
                        maximum_relative_difference, difference / max(distance, 1.0)
                    )
        return OrbitInterpolationDiagnostics(
            algorithm=self.algorithm,
            version=self.version,
            maximum_sample_interval_s=maximum_interval,
            minimum_observed_pair_distance_m=minimum_distance,
            maximum_midpoint_hermite_linear_relative_difference=(
                maximum_relative_difference
            ),
            scientific_warning=(
                "midpoint Hermite-versus-linear difference is an interpolation diagnostic, "
                "not an error bound; close encounters require higher-cadence M2 output"
            ),
        )
