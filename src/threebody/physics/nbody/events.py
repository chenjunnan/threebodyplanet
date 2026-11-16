from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from threebody.common.constants import G
from threebody.physics.nbody.diagnostics import NumericalSafetyError
from threebody.physics.nbody.models import IntegrationSettings, SimulationEvent


def _simulation(value: Any) -> Any:
    return value.contents if hasattr(value, "contents") else value


@dataclass
class EventObserver:
    body_ids: tuple[str, ...]
    radii_m: tuple[float, ...]
    settings: IntegrationSettings
    started_monotonic: float = field(default_factory=time.monotonic)
    events: list[SimulationEvent] = field(default_factory=list)
    minimum_observed_pair_distances_m: dict[str, float] = field(default_factory=dict)
    timed_out: bool = False
    numerical_error: str | None = None
    _encounter_recorded: set[tuple[int, int]] = field(default_factory=set)
    _escape_recorded: set[int] = field(default_factory=set)
    _escape_started_s: dict[int, float] = field(default_factory=dict)

    def heartbeat(self, simulation_pointer: Any) -> None:
        simulation = _simulation(simulation_pointer)
        try:
            self.observe(simulation, "ias15_internal_step_endpoint")
        except (ArithmeticError, NumericalSafetyError, ValueError) as exc:
            self.numerical_error = f"event observation failed: {exc}"
            simulation.stop()
            return
        limit = self.settings.max_wall_seconds
        if limit is not None and time.monotonic() - self.started_monotonic >= limit:
            self.timed_out = True
            simulation.stop()

    def observe(self, simulation: Any, detection_method: str) -> None:
        particles = simulation.particles
        positions = np.asarray([[p.x, p.y, p.z] for p in particles], dtype=np.float64)
        velocities = np.asarray([[p.vx, p.vy, p.vz] for p in particles], dtype=np.float64)
        masses = np.asarray([p.m for p in particles], dtype=np.float64)
        if not all(
            np.all(np.isfinite(values)) for values in (positions, velocities, masses)
        ):
            raise NumericalSafetyError("particle state became non-finite")
        self._observe_encounters(float(simulation.t), positions, detection_method)
        self._observe_escape(float(simulation.t), masses, positions, velocities, detection_method)

    def _encounter_threshold(self, first: int, second: int) -> float:
        encounter = self.settings.encounter
        if encounter.threshold_mode == "absolute_m":
            return float(encounter.distance_m)
        return float(encounter.radius_multiplier) * (self.radii_m[first] + self.radii_m[second])

    def _observe_encounters(
        self, t_s: float, positions: np.ndarray, detection_method: str
    ) -> None:
        for first in range(len(self.body_ids)):
            for second in range(first + 1, len(self.body_ids)):
                with np.errstate(over="raise", invalid="raise"):
                    distance = float(
                        np.linalg.norm(positions[first] - positions[second])
                    )
                if not np.isfinite(distance):
                    raise NumericalSafetyError("observed pair distance is non-finite")
                pair_key = f"{self.body_ids[first]}:{self.body_ids[second]}"
                self.minimum_observed_pair_distances_m[pair_key] = min(
                    self.minimum_observed_pair_distances_m.get(pair_key, distance),
                    distance,
                )
                if not self.settings.encounter.enabled:
                    continue
                pair = (first, second)
                threshold = self._encounter_threshold(first, second)
                if distance <= threshold and pair not in self._encounter_recorded:
                    self._encounter_recorded.add(pair)
                    self.events.append(
                        SimulationEvent(
                            event_type="CLOSE_ENCOUNTER",
                            t_s=t_s,
                            body_ids=(self.body_ids[first], self.body_ids[second]),
                            observed={
                                "observed_distance_m": distance,
                                "threshold_m": threshold,
                                "is_first_observed_threshold_crossing": True,
                                "distance_semantics": (
                                    "internal_step_endpoint_observation"
                                ),
                            },
                            message=(
                                "close-encounter threshold observed at an internal-step "
                                "endpoint; neither crossing time nor minimum distance is exact"
                            ),
                            detection_method=detection_method,
                        )
                    )

    def _observe_escape(
        self,
        t_s: float,
        masses: np.ndarray,
        positions: np.ndarray,
        velocities: np.ndarray,
        detection_method: str,
    ) -> None:
        escape = self.settings.escape
        if not escape.enabled:
            return
        for index, body_id in enumerate(self.body_ids):
            if index in self._escape_recorded:
                continue
            other = np.arange(len(self.body_ids)) != index
            try:
                with np.errstate(over="raise", invalid="raise", divide="raise"):
                    remaining_mass = float(np.sum(masses[other]))
                    remaining_position = (
                        np.sum(masses[other, None] * positions[other], axis=0)
                        / remaining_mass
                    )
                    remaining_velocity = (
                        np.sum(masses[other, None] * velocities[other], axis=0)
                        / remaining_mass
                    )
                    relative_position = positions[index] - remaining_position
                    relative_velocity = velocities[index] - remaining_velocity
                    distance = float(np.linalg.norm(relative_position))
            except FloatingPointError as exc:
                raise NumericalSafetyError(
                    f"escape-candidate calculation overflow: {exc}"
                ) from exc
            if not np.isfinite(distance):
                raise NumericalSafetyError("escape-candidate distance is non-finite")
            if distance == 0.0:
                self._escape_started_s.pop(index, None)
                continue
            with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
                radial_velocity = float(
                    np.dot(relative_position, relative_velocity) / distance
                )
                specific_energy = 0.5 * float(
                    np.dot(relative_velocity, relative_velocity)
                )
                specific_energy -= G * remaining_mass / distance
            if not np.isfinite(radial_velocity) or not np.isfinite(specific_energy):
                raise NumericalSafetyError(
                    "escape-candidate velocity or specific energy is non-finite"
                )
            qualifies = distance > escape.boundary_radius_m
            if escape.require_outward_velocity:
                qualifies = qualifies and radial_velocity > 0.0
            if escape.require_positive_specific_energy:
                qualifies = qualifies and specific_energy > 0.0
            if not qualifies:
                self._escape_started_s.pop(index, None)
                continue
            started = self._escape_started_s.setdefault(index, t_s)
            if t_s - started < escape.minimum_duration_s:
                continue
            self._escape_recorded.add(index)
            self.events.append(
                SimulationEvent(
                    event_type="ESCAPE_CANDIDATE",
                    t_s=t_s,
                    body_ids=(body_id,),
                    observed={
                        "distance_from_remaining_com_m": distance,
                        "boundary_radius_m": escape.boundary_radius_m,
                        "radial_velocity_mps": radial_velocity,
                        "two_body_specific_energy_j_kg": specific_energy,
                        "condition_duration_s": t_s - started,
                    },
                    message=(
                        "heuristic escape candidate only; permanent escape is not proven"
                    ),
                    detection_method=detection_method,
                )
            )

    def collision_event(
        self, simulation: Any, rebound_particle_indices: tuple[int, int]
    ) -> SimulationEvent:
        particles = simulation.particles
        callback_first, callback_second = rebound_particle_indices
        if callback_first == callback_second or not all(
            0 <= index < len(self.body_ids)
            for index in (callback_first, callback_second)
        ):
            raise ValueError(
                "REBOUND collision callback returned invalid particle indices "
                f"{rebound_particle_indices}"
            )
        first, second = sorted(rebound_particle_indices)
        first_particle = particles[first]
        second_particle = particles[second]
        displacement = np.asarray(
            [
                first_particle.x - second_particle.x,
                first_particle.y - second_particle.y,
                first_particle.z - second_particle.z,
            ]
        )
        with np.errstate(over="ignore", invalid="ignore"):
            distance = float(np.linalg.norm(displacement))
        if not np.isfinite(distance):
            raise NumericalSafetyError("collision-pair distance is non-finite")
        gap = distance - self.radii_m[first] - self.radii_m[second]
        event = SimulationEvent(
            event_type="COLLISION",
            t_s=float(simulation.t),
            body_ids=(self.body_ids[first], self.body_ids[second]),
            observed={
                "surface_gap_at_halt_m": gap,
                "contact_distance_m": self.radii_m[first] + self.radii_m[second],
                "rebound_particle_indices": [callback_first, callback_second],
            },
            message=(
                "REBOUND swept-line collision detector halted integration; "
                "participants come directly from the collision callback and the "
                "reported time/state have internal-step precision"
            ),
            detection_method="rebound_line_collision_callback",
        )
        self.events.append(event)
        return event

