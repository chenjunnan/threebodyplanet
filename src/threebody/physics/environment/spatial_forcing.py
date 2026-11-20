from __future__ import annotations

import bisect
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from threebody.common.json import sha256_json
from threebody.errors import ConfigError
from threebody.physics.environment.integrated_models import IntegratedEnvironmentConfig
from threebody.physics.longrun.reader import SegmentedTrajectoryReader
from threebody.world.models import WorldInitialState


@dataclass(frozen=True)
class SpatialForcingSample:
    t_s: float
    star_direction_inertial: np.ndarray
    stellar_flux_w_m2: np.ndarray
    source_bracket_s: tuple[float, float]
    interpolation: str


class M5ASpatialForcing:
    kind = "M5A_THREE_STAR_CARTESIAN_LINEAR_V1"

    def __init__(self, run_dir: str | Path, config: IntegratedEnvironmentConfig):
        self.run_dir = Path(run_dir)
        reader = SegmentedTrajectoryReader(self.run_dir)
        if reader.manifest.task.status != "COMPLETED":
            raise ConfigError("integrated environment requires a COMPLETED M5A run")
        try:
            state = WorldInitialState.model_validate_json(
                (self.run_dir / "initial_state.json").read_text(encoding="utf-8"),
                strict=True,
            )
        except (OSError, ValueError) as exc:
            raise ConfigError(f"cannot read M5A initial state: {exc}") from exc
        if sha256_json(state) != reader.manifest.initial_state_sha256:
            raise ConfigError("M5A initial-state hash mismatch")
        if tuple(body.id for body in state.bodies) != (
            "star-1", "star-2", "star-3", "planet-1"
        ):
            raise ConfigError("integrated environment requires canonical four-body order")
        bodies = {body.id: body for body in state.bodies}
        planet = bodies["planet-1"]
        if not math.isclose(
            planet.radius_m, config.planet_radius_m, rel_tol=1.0e-9, abs_tol=1.0e-3
        ):
            raise ConfigError("environment planet radius differs from M5A initial state")
        archived_axis = planet.properties.get("rotation_axis_inertial_unit")
        if archived_axis is not None:
            try:
                axis_array = np.asarray(archived_axis, dtype=float)
            except (TypeError, ValueError) as exc:
                raise ConfigError("M5A rotation axis metadata is invalid") from exc
            if (
                axis_array.shape != (3,)
                or not np.all(np.isfinite(axis_array))
                or not np.allclose(
                    axis_array, config.rotation_axis, rtol=0.0, atol=1.0e-10
                )
            ):
                raise ConfigError(
                    "environment rotation axis differs from M5A initial state"
                )
        archived_rate = planet.properties.get("rotation_rate_rad_s")
        if archived_rate is not None:
            if isinstance(archived_rate, bool) or not isinstance(
                archived_rate, (int, float)
            ):
                raise ConfigError("M5A rotation rate metadata is invalid")
            if not math.isclose(
                float(archived_rate), config.rotation_rate_rad_s,
                rel_tol=1.0e-10, abs_tol=0.0,
            ):
                raise ConfigError(
                    "environment rotation rate differs from M5A initial state"
                )
        self.luminosities = np.asarray([
            self._luminosity(bodies[f"star-{index}"]) for index in range(1, 4)
        ])
        times = []
        relative = []
        for sample in reader.iter_samples():
            positions = {body.id: np.asarray(body.position_m, dtype=float)
                         for body in sample.bodies}
            times.append(sample.t_s)
            relative.append(np.stack([
                positions[f"star-{index}"] - positions["planet-1"]
                for index in range(1, 4)
            ]))
        if len(times) < 2:
            raise ConfigError("spatial forcing requires at least two M5A samples")
        gaps = np.diff(times)
        self.max_gap_s = float(np.max(gaps))
        if self.max_gap_s > config.max_orbit_sample_interval_s and not math.isclose(
            self.max_gap_s, config.max_orbit_sample_interval_s,
            rel_tol=1.0e-12, abs_tol=1.0e-6,
        ):
            raise ConfigError("M5A sample gap exceeds integrated environment limit")
        self.times_s = np.asarray(times, dtype=float)
        self.relative_positions_m = np.asarray(relative, dtype=float)
        self.coverage_s = (float(self.times_s[0]), float(self.times_s[-1]))
        source_contract = {
            "kind": self.kind,
            "run_id": reader.manifest.run_id,
            "world_id": reader.manifest.world_id,
            "initial_state_sha256": reader.manifest.initial_state_sha256,
            "physics_sha256": reader.manifest.hashes.physics_sha256,
            "schedule_sha256": reader.manifest.hashes.schedule_sha256,
            "segments": [
                entry.model_dump(mode="json") for entry in reader.manifest.segments
                if entry.status in {"COMMITTED", "TERMINAL"}
            ],
            "times_sha256": sha256_json(times),
            "relative_positions_sha256": sha256_json(
                self.relative_positions_m.tolist()
            ),
            "luminosities_w": self.luminosities.tolist(),
            "interpolation": "piecewise_linear_relative_cartesian_position",
        }
        self.source_contract = source_contract
        self.source_sha256 = sha256_json(source_contract)
        self.run_id = reader.manifest.run_id
        self.world_id = reader.manifest.world_id

    @staticmethod
    def _luminosity(body) -> float:
        value = body.properties.get("luminosity_w")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{body.id} luminosity_w is unavailable")
        result = float(value)
        if not math.isfinite(result) or result <= 0.0:
            raise ConfigError(f"{body.id} luminosity_w is invalid")
        return result

    def verify_unchanged(self) -> None:
        reader = SegmentedTrajectoryReader(self.run_dir)
        current = {
            "kind": self.kind,
            "run_id": reader.manifest.run_id,
            "world_id": reader.manifest.world_id,
            "initial_state_sha256": reader.manifest.initial_state_sha256,
            "physics_sha256": reader.manifest.hashes.physics_sha256,
            "schedule_sha256": reader.manifest.hashes.schedule_sha256,
            "segments": [
                entry.model_dump(mode="json") for entry in reader.manifest.segments
                if entry.status in {"COMMITTED", "TERMINAL"}
            ],
            "times_sha256": self.source_contract["times_sha256"],
            "relative_positions_sha256": self.source_contract[
                "relative_positions_sha256"
            ],
            "luminosities_w": self.luminosities.tolist(),
            "interpolation": "piecewise_linear_relative_cartesian_position",
        }
        if sha256_json(current) != self.source_sha256:
            raise ConfigError("M5A forcing source changed during environment run")

    def sample(self, t_s: float) -> SpatialForcingSample:
        if not math.isfinite(t_s) or not self.coverage_s[0] <= t_s <= self.coverage_s[1]:
            raise ConfigError("spatial forcing time outside verified M5A coverage")
        right = bisect.bisect_left(self.times_s, t_s)
        if right < len(self.times_s) and self.times_s[right] == t_s:
            relative = self.relative_positions_m[right]
            bracket = (t_s, t_s)
            interpolation = "EXACT_SAMPLE"
        else:
            left = right - 1
            fraction = (t_s - self.times_s[left]) / (
                self.times_s[right] - self.times_s[left]
            )
            relative = self.relative_positions_m[left] + fraction * (
                self.relative_positions_m[right] - self.relative_positions_m[left]
            )
            bracket = (float(self.times_s[left]), float(self.times_s[right]))
            interpolation = "LINEAR_CARTESIAN_POSITION"
        distance = np.linalg.norm(relative, axis=1)
        if np.any(~np.isfinite(distance)) or np.any(distance <= 0.0):
            raise ConfigError("spatial forcing produced invalid star distance")
        return SpatialForcingSample(
            t_s=t_s,
            star_direction_inertial=relative / distance[:, None],
            stellar_flux_w_m2=self.luminosities / (4.0 * math.pi * distance**2),
            source_bracket_s=bracket,
            interpolation=interpolation,
        )

    def provenance_json(self) -> str:
        return json.dumps(self.source_contract, sort_keys=True, separators=(",", ":"))
