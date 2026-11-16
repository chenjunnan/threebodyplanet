from __future__ import annotations

import math

import numpy as np

from threebody.errors import CandidateInvalid
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
                distance = float(
                    np.linalg.norm(first_position - np.asarray(second.position_m, dtype=np.float64))
                )
                if distance == 0.0:
                    errors.append(f"{first.id} and {second.id} occupy the same point")
                elif distance <= first.radius_m + second.radius_m:
                    errors.append(f"{first.id} and {second.id} initially overlap or touch")
        if errors:
            unique_errors = "; ".join(dict.fromkeys(errors))
            raise CandidateInvalid("integration input rejected: " + unique_errors)

