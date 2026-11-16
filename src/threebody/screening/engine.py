from __future__ import annotations

import numpy as np

from threebody.config.models import ScreeningConfig
from threebody.screening.models import RuleResult, ScreeningReport
from threebody.screening.rules import RULES
from threebody.screening.s0 import validate_s0
from threebody.world.models import WorldInitialState


def _skip(rule_id: str, config, reason: str) -> RuleResult:
    return RuleResult(
        rule_id=rule_id,
        rule_version="0.1.0",
        status="SKIP",
        severity=config.severity,
        failure_action=config.failure_action,
        observed={"reason": reason},
        thresholds=config.params,
        message=reason,
    )


def _diagnostics(state: WorldInitialState) -> dict[str, object]:
    masses = np.asarray([body.mass_kg for body in state.bodies], dtype=np.float64)
    positions = np.asarray([body.position_m for body in state.bodies], dtype=np.float64)
    velocities = np.asarray([body.velocity_mps for body in state.bodies], dtype=np.float64)
    signed_total_mass = float(np.sum(masses))
    if signed_total_mass == 0.0:
        barycenter_position = None
        barycenter_velocity = None
    else:
        barycenter_position = (
            np.sum(masses[:, None] * positions, axis=0) / signed_total_mass
        ).tolist()
        barycenter_velocity = (
            np.sum(masses[:, None] * velocities, axis=0) / signed_total_mass
        ).tolist()
    surface_gaps: list[float] = []
    relative_speeds: list[float] = []
    for first_index, first in enumerate(state.bodies):
        for second_index, second in enumerate(state.bodies[first_index + 1 :], first_index + 1):
            surface_gaps.append(
                float(np.linalg.norm(positions[first_index] - positions[second_index]))
                - first.radius_m
                - second.radius_m
            )
            relative_speeds.append(
                float(np.linalg.norm(velocities[first_index] - velocities[second_index]))
            )
    return {
        "signed_total_mass_kg": signed_total_mass,
        "barycenter_position_m": barycenter_position,
        "barycenter_velocity_mps": barycenter_velocity,
        "minimum_surface_gap_m": min(surface_gaps),
        "maximum_relative_speed_mps": max(relative_speeds),
    }


def screen_candidate(state: WorldInitialState, config: ScreeningConfig) -> ScreeningReport:
    s0_errors = validate_s0(state)
    if s0_errors:
        results = tuple(
            _skip(rule.id, config.initial_physics.rules[rule.id], "S0 validation failed")
            for rule in RULES
        )
        return ScreeningReport(
            candidate_index=state.candidate_index,
            world_id=state.world_id,
            decision="INVALID",
            scientific_validity="failed",
            s0_errors=tuple(s0_errors),
            initial_state_diagnostics={},
            results=results,
        )

    results: list[RuleResult] = []
    stopped = False
    for rule in RULES:
        rule_config = config.initial_physics.rules[rule.id]
        enabled = config.initial_physics.enabled and rule_config.enabled
        if stopped:
            results.append(_skip(rule.id, rule_config, "stopped_after_failure"))
            continue
        if not enabled:
            results.append(_skip(rule.id, rule_config, "rule disabled"))
            continue
        try:
            params = rule.params_model.model_validate(rule_config.params, strict=True)
            passed, observed, message = rule.evaluate(state, params)
            status = "PASS" if passed else "FAIL"
            result = RuleResult(
                rule_id=rule.id,
                rule_version="0.1.0",
                status=status,
                severity=rule_config.severity,
                failure_action=rule_config.failure_action,
                observed=observed,
                thresholds=rule_config.params,
                message=message,
            )
        except Exception as exc:
            result = RuleResult(
                rule_id=rule.id,
                rule_version="0.1.0",
                status="ERROR",
                severity=rule_config.severity,
                failure_action=rule_config.failure_action,
                observed={"error_type": type(exc).__name__},
                thresholds=rule_config.params,
                message=f"rule evaluation failed: {exc}",
            )
        results.append(result)
        if config.fail_fast and result.status in {"FAIL", "ERROR"}:
            stopped = True

    if any(result.status == "ERROR" for result in results):
        decision = "INVALID"
    elif any(result.status == "FAIL" and result.failure_action == "reject" for result in results):
        decision = "REJECTED"
    else:
        decision = "ACCEPTED"
    all_skipped = all(result.status == "SKIP" for result in results)
    if decision in {"INVALID", "REJECTED"}:
        scientific_validity = "failed"
    elif all_skipped:
        scientific_validity = "unverified"
    else:
        scientific_validity = "screened"
    return ScreeningReport(
        candidate_index=state.candidate_index,
        world_id=state.world_id,
        decision=decision,
        scientific_validity=scientific_validity,
        s0_errors=(),
        initial_state_diagnostics=_diagnostics(state),
        results=tuple(results),
    )
