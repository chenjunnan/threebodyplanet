from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from threebody.common.constants import G
from threebody.physics.nbody.models import DiagnosticSnapshot, DiagnosticSummary

FloatArray = NDArray[np.float64]


class NumericalSafetyError(ValueError):
    """A finite input exceeds the representable numerical mechanics domain."""


def _require_finite(name: str, value) -> None:
    if not np.all(np.isfinite(value)):
        raise NumericalSafetyError(f"{name} is not finite in float64 mechanics")


def compute_diagnostics(
    t_s: float, masses_kg: FloatArray, positions_m: FloatArray, velocities_mps: FloatArray
) -> DiagnosticSnapshot:
    _require_finite("masses", masses_kg)
    _require_finite("positions", positions_m)
    _require_finite("velocities", velocities_mps)
    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            kinetic = float(0.5 * np.sum(masses_kg[:, None] * velocities_mps**2))
            potential = 0.0
            for first in range(len(masses_kg)):
                for second in range(first + 1, len(masses_kg)):
                    distance = float(
                        np.linalg.norm(positions_m[first] - positions_m[second])
                    )
                    _require_finite("pair distance", distance)
                    if distance <= 0.0:
                        raise NumericalSafetyError("pair distance must be strictly positive")
                    pair_potential = (
                        G * masses_kg[first] * masses_kg[second] / distance
                    )
                    _require_finite("pair potential energy", pair_potential)
                    potential -= float(pair_potential)
                    _require_finite("potential energy sum", potential)
            total_mass = float(np.sum(masses_kg))
            linear_momentum = np.sum(masses_kg[:, None] * velocities_mps, axis=0)
            angular_momentum = np.sum(
                masses_kg[:, None] * np.cross(positions_m, velocities_mps), axis=0
            )
            barycenter_position = (
                np.sum(masses_kg[:, None] * positions_m, axis=0) / total_mass
            )
            barycenter_velocity = linear_momentum / total_mass
    except (FloatingPointError, OverflowError) as exc:
        raise NumericalSafetyError(f"float64 mechanics overflow: {exc}") from exc

    total_energy = kinetic + potential
    energy_scale = abs(kinetic) + abs(potential)
    for name, value in (
        ("kinetic energy", kinetic),
        ("potential energy", potential),
        ("total energy", total_energy),
        ("energy scale", energy_scale),
        ("total mass", total_mass),
        ("linear momentum", linear_momentum),
        ("angular momentum", angular_momentum),
        ("barycenter position", barycenter_position),
        ("barycenter velocity", barycenter_velocity),
    ):
        _require_finite(name, value)
    if energy_scale <= 0.0:
        raise NumericalSafetyError("energy scale must be strictly positive")
    return DiagnosticSnapshot(
        t_s=float(t_s),
        kinetic_energy_j=kinetic,
        potential_energy_j=potential,
        total_energy_j=total_energy,
        energy_scale_j=energy_scale,
        linear_momentum_kg_mps=tuple(float(value) for value in linear_momentum),
        angular_momentum_kg_m2_s=tuple(float(value) for value in angular_momentum),
        barycenter_position_m=tuple(float(value) for value in barycenter_position),
        barycenter_velocity_mps=tuple(float(value) for value in barycenter_velocity),
    )


@dataclass
class DiagnosticTracker:
    initial: DiagnosticSnapshot
    maximum_scaled_energy_error: float = 0.0
    maximum_traditional_relative_energy_error: float | None = None
    maximum_angular_momentum_relative_error: float = 0.0
    maximum_barycenter_position_drift_m: float = 0.0
    maximum_barycenter_velocity_drift_mps: float = 0.0
    _latest: DiagnosticSnapshot = field(init=False)

    def __post_init__(self) -> None:
        self._latest = self.initial

    def update(self, snapshot: DiagnosticSnapshot) -> None:
        energy_denominator = max(abs(self.initial.total_energy_j), self.initial.energy_scale_j)
        try:
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                energy_error = (
                    abs(snapshot.total_energy_j - self.initial.total_energy_j)
                    / energy_denominator
                )
                traditional_error = None
                if abs(self.initial.total_energy_j) > 1.0e-12 * self.initial.energy_scale_j:
                    traditional_error = abs(
                        snapshot.total_energy_j - self.initial.total_energy_j
                    ) / abs(self.initial.total_energy_j)
                initial_angular = np.asarray(self.initial.angular_momentum_kg_m2_s)
                angular_denominator = max(float(np.linalg.norm(initial_angular)), 1.0)
                angular_error = float(
                    np.linalg.norm(
                        np.asarray(snapshot.angular_momentum_kg_m2_s) - initial_angular
                    )
                ) / angular_denominator
                position_drift = float(
                    np.linalg.norm(
                        np.asarray(snapshot.barycenter_position_m)
                        - np.asarray(self.initial.barycenter_position_m)
                    )
                )
                velocity_drift = float(
                    np.linalg.norm(
                        np.asarray(snapshot.barycenter_velocity_mps)
                        - np.asarray(self.initial.barycenter_velocity_mps)
                    )
                )
        except (FloatingPointError, OverflowError, ZeroDivisionError) as exc:
            raise NumericalSafetyError(f"diagnostic drift overflow: {exc}") from exc
        for name, value in (
            ("scaled energy error", energy_error),
            ("traditional relative energy error", traditional_error or 0.0),
            ("angular momentum relative error", angular_error),
            ("barycenter position drift", position_drift),
            ("barycenter velocity drift", velocity_drift),
        ):
            _require_finite(name, value)
        if traditional_error is not None:
            self.maximum_traditional_relative_energy_error = max(
                self.maximum_traditional_relative_energy_error or 0.0,
                traditional_error,
            )
        self.maximum_scaled_energy_error = max(self.maximum_scaled_energy_error, energy_error)
        self.maximum_angular_momentum_relative_error = max(
            self.maximum_angular_momentum_relative_error, angular_error
        )
        self.maximum_barycenter_position_drift_m = max(
            self.maximum_barycenter_position_drift_m, position_drift
        )
        self.maximum_barycenter_velocity_drift_mps = max(
            self.maximum_barycenter_velocity_drift_mps, velocity_drift
        )
        self._latest = snapshot

    def summary(self, *, conservation_applicable: bool) -> DiagnosticSummary:
        energy_denominator = max(abs(self.initial.total_energy_j), self.initial.energy_scale_j)
        final_energy_error = (
            abs(self._latest.total_energy_j - self.initial.total_energy_j) / energy_denominator
        )
        if abs(self.initial.total_energy_j) > 1.0e-12 * self.initial.energy_scale_j:
            final_traditional_error = abs(
                self._latest.total_energy_j - self.initial.total_energy_j
            ) / abs(self.initial.total_energy_j)
        else:
            final_traditional_error = None
        initial_angular = np.asarray(self.initial.angular_momentum_kg_m2_s)
        final_angular = np.asarray(self._latest.angular_momentum_kg_m2_s)
        final_angular_error = float(np.linalg.norm(final_angular - initial_angular)) / max(
            float(np.linalg.norm(initial_angular)), 1.0
        )
        for name, value in (
            ("final scaled energy error", final_energy_error),
            ("final traditional relative energy error", final_traditional_error or 0.0),
            ("final angular momentum relative error", final_angular_error),
        ):
            _require_finite(name, value)
        return DiagnosticSummary(
            initial=self.initial,
            final=self._latest,
            maximum_scaled_energy_error=self.maximum_scaled_energy_error,
            final_scaled_energy_error=final_energy_error,
            maximum_traditional_relative_energy_error=(
                self.maximum_traditional_relative_energy_error
            ),
            final_traditional_relative_energy_error=final_traditional_error,
            maximum_angular_momentum_relative_error=(
                self.maximum_angular_momentum_relative_error
            ),
            final_angular_momentum_relative_error=final_angular_error,
            maximum_barycenter_position_drift_m=self.maximum_barycenter_position_drift_m,
            maximum_barycenter_velocity_drift_mps=self.maximum_barycenter_velocity_drift_mps,
            conservation_applicable=conservation_applicable,
        )

