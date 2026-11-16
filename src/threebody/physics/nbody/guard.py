from __future__ import annotations

import math

import numpy as np

from threebody.common.constants import G
from threebody.errors import CandidateInvalid
from threebody.physics.nbody.diagnostics import NumericalSafetyError, compute_diagnostics
from threebody.physics.nbody.models import IntegrationSettings
from threebody.screening.s0 import validate_s0
from threebody.world.models import WorldInitialState


class ReboundInputGuard:
    """Mandatory solver-domain validation, independent from configurable S1 rules."""

    def validate(
        self, initial_state: WorldInitialState, settings: IntegrationSettings | None = None
    ) -> None:
        errors = validate_s0(initial_state)
        if initial_state.units != "SI":
            errors.append("REBOUND IAS15 input units must be SI")
        if initial_state.coordinate_frame != "BARYCENTRIC_INERTIAL":
            errors.append("REBOUND IAS15 requires BARYCENTRIC_INERTIAL coordinates")
        if settings is not None and initial_state.reference_epoch_s != settings.start_time_s:
            errors.append("settings.start_time_s must equal initial_state.reference_epoch_s")

        for body in initial_state.bodies:
            if not math.isfinite(body.mass_kg) or body.mass_kg <= 0:
                errors.append(f"{body.id}.mass_kg must be finite and strictly positive")
            if not math.isfinite(body.radius_m) or body.radius_m < 0:
                errors.append(f"{body.id}.radius_m must be finite and non-negative")
            if settings is not None and settings.require_positive_radius and body.radius_m <= 0:
                errors.append(f"{body.id}.radius_m must be strictly positive for this run")

        for first_index, first in enumerate(initial_state.bodies):
            first_position = np.asarray(first.position_m, dtype=np.float64)
            for second in initial_state.bodies[first_index + 1 :]:
                with np.errstate(over="ignore", invalid="ignore"):
                    distance = float(
                        np.linalg.norm(
                            first_position
                            - np.asarray(second.position_m, dtype=np.float64)
                        )
                    )
                if not math.isfinite(distance):
                    errors.append(
                        f"{first.id}/{second.id} separation exceeds float64 mechanics range"
                    )
                elif distance == 0.0:
                    errors.append(f"{first.id} and {second.id} occupy the same point")
                elif distance <= first.radius_m + second.radius_m:
                    errors.append(f"{first.id} and {second.id} initially overlap or touch")
                else:
                    for target, source in ((first, second), (second, first)):
                        if not math.isfinite(source.mass_kg) or source.mass_kg <= 0.0:
                            continue
                        log_acceleration = (
                            math.log(G)
                            + math.log(source.mass_kg)
                            - 2.0 * math.log(distance)
                        )
                        if log_acceleration > math.log(np.finfo(np.float64).max):
                            errors.append(
                                f"{target.id} acceleration from {source.id} "
                                "exceeds float64 range"
                            )
        if not errors:
            masses = np.asarray(
                [body.mass_kg for body in initial_state.bodies], dtype=np.float64
            )
            positions = np.asarray(
                [body.position_m for body in initial_state.bodies], dtype=np.float64
            )
            velocities = np.asarray(
                [body.velocity_mps for body in initial_state.bodies], dtype=np.float64
            )
            try:
                compute_diagnostics(
                    initial_state.reference_epoch_s, masses, positions, velocities
                )
            except NumericalSafetyError as exc:
                errors.append(f"initial mechanical diagnostics are unsafe: {exc}")
        if errors:
            unique_errors = "; ".join(dict.fromkeys(errors))
            raise CandidateInvalid("integration input rejected: " + unique_errors)

