from __future__ import annotations

import math

import numpy as np

from threebody.screening.engine import screen_candidate
from threebody.world.generators import generate_initial_state


def _state(resolved):
    config, digest = resolved
    return generate_initial_state(
        config.world_generation.generator, config.experiment.seed, 0, digest
    )


def _replace_body(state, index, **changes):
    bodies = list(state.bodies)
    bodies[index] = bodies[index].model_copy(update=changes)
    return state.model_copy(update={"bodies": tuple(bodies)})


def test_t03_disabled_rule_is_skip(resolved):
    config, _ = resolved
    state = _state(resolved)
    rules = dict(config.screening.initial_physics.rules)
    rules["system.initial_overlap"] = rules["system.initial_overlap"].model_copy(
        update={"enabled": False}
    )
    initial = config.screening.initial_physics.model_copy(update={"rules": rules})
    screening = config.screening.model_copy(update={"initial_physics": initial})
    report = screen_candidate(state, screening)
    overlap = next(
        result for result in report.results if result.rule_id == "system.initial_overlap"
    )
    assert overlap.status == "SKIP"
    assert report.decision == "ACCEPTED"


def test_t04_negative_mass_strict_is_rejected_not_s0_invalid(resolved):
    config, _ = resolved
    state = _replace_body(_state(resolved), 3, mass_kg=-1.0)
    report = screen_candidate(state, config.screening)
    mass = report.results[0]
    assert mass.status == "FAIL"
    assert report.decision == "REJECTED"
    assert not report.s0_errors


def test_t05_negative_mass_raw_is_accepted_unverified(resolved):
    config, _ = resolved
    state = _replace_body(_state(resolved), 3, mass_kg=-1.0)
    rules = {
        key: value.model_copy(update={"enabled": False, "failure_action": "report"})
        for key, value in config.screening.initial_physics.rules.items()
    }
    initial = config.screening.initial_physics.model_copy(update={"enabled": False, "rules": rules})
    screening = config.screening.model_copy(update={"initial_physics": initial, "preset": "raw"})
    report = screen_candidate(state, screening)
    assert report.decision == "ACCEPTED"
    assert report.scientific_validity == "unverified"
    assert all(result.status == "SKIP" for result in report.results)


def test_t07_negative_radius_strict_is_rejected(resolved):
    config, _ = resolved
    state = _replace_body(_state(resolved), 3, radius_m=-1.0)
    report = screen_candidate(state, config.screening)
    radius = next(result for result in report.results if result.rule_id == "body.radius.positive")
    assert radius.status == "FAIL"
    assert report.decision == "REJECTED"


def test_t08_t09_overlap_rejects_unless_disabled(resolved):
    config, _ = resolved
    state = _state(resolved)
    state = _replace_body(state, 3, position_m=state.bodies[0].position_m)
    masses = np.asarray([body.mass_kg for body in state.bodies])
    positions = np.asarray([body.position_m for body in state.bodies])
    center = np.sum(masses[:, None] * positions, axis=0) / masses.sum()
    bodies = tuple(
        body.model_copy(update={"position_m": tuple(position - center)})
        for body, position in zip(state.bodies, positions, strict=True)
    )
    state = state.model_copy(update={"bodies": bodies})
    strict_report = screen_candidate(state, config.screening)
    overlap = next(r for r in strict_report.results if r.rule_id == "system.initial_overlap")
    assert overlap.status == "FAIL"
    assert strict_report.decision == "REJECTED"

    rules = dict(config.screening.initial_physics.rules)
    rules["system.initial_overlap"] = rules["system.initial_overlap"].model_copy(
        update={"enabled": False}
    )
    initial = config.screening.initial_physics.model_copy(update={"rules": rules})
    screening = config.screening.model_copy(
        update={"initial_physics": initial}
    )
    disabled_report = screen_candidate(state, screening)
    disabled_overlap = next(
        result
        for result in disabled_report.results
        if result.rule_id == "system.initial_overlap"
    )
    assert disabled_overlap.status == "SKIP"
    assert not any(
        result.status == "FAIL" and result.failure_action == "reject"
        for result in disabled_report.results
    )


def test_t10_temperature_is_not_an_s1_rejection(resolved):
    config, _ = resolved
    state = _state(resolved)
    properties = dict(state.bodies[3].properties)
    properties["effective_temperature_k"] = 1.0e12
    state = _replace_body(state, 3, properties=properties)
    report = screen_candidate(state, config.screening)
    assert report.decision == "ACCEPTED"


def test_t11_rule_dependency_error_invalidates_candidate(resolved):
    config, _ = resolved
    state = _replace_body(_state(resolved), 3, radius_m=0.0)
    report = screen_candidate(state, config.screening)
    density = next(result for result in report.results if result.rule_id == "body.density.bounds")
    assert density.status == "ERROR"
    assert report.decision == "INVALID"


def test_density_formula_standard_case(resolved):
    config, _ = resolved
    density = 5000.0
    radius = 2.0
    mass = density * (4.0 / 3.0) * math.pi * radius**3
    state = _replace_body(_state(resolved), 3, mass_kg=mass, radius_m=radius)
    rules = dict(config.screening.initial_physics.rules)
    density_config = rules["body.density.bounds"]
    rules["body.density.bounds"] = density_config.model_copy(
        update={"params": {"min_kg_m3": density, "max_kg_m3": density, "kinds": ["PLANET"]}}
    )
    initial = config.screening.initial_physics.model_copy(update={"rules": rules})
    screening = config.screening.model_copy(
        update={"initial_physics": initial}
    )
    report = screen_candidate(state, screening)
    result = next(r for r in report.results if r.rule_id == "body.density.bounds")
    assert result.status == "PASS"


def test_overlap_exactly_at_threshold_passes(resolved):
    config, _ = resolved
    state = _state(resolved)
    positions = ((0.0, 0.0, 0.0), (3.0, 0.0, 0.0), (10.0, 0.0, 0.0), (20.0, 0.0, 0.0))
    radii = (1.0, 2.0, 1.0, 1.0)
    bodies = tuple(
        body.model_copy(update={"position_m": position, "radius_m": radius})
        for body, position, radius in zip(state.bodies, positions, radii, strict=True)
    )
    state = state.model_copy(update={"bodies": bodies})
    rules = dict(config.screening.initial_physics.rules)
    for rule_id in ("body.density.bounds", "system.barycenter"):
        rules[rule_id] = rules[rule_id].model_copy(update={"enabled": False})
    initial = config.screening.initial_physics.model_copy(update={"rules": rules})
    report = screen_candidate(
        state, config.screening.model_copy(update={"initial_physics": initial})
    )
    overlap = next(
        result for result in report.results if result.rule_id == "system.initial_overlap"
    )
    assert overlap.status == "PASS"
    assert overlap.observed["minimum_surface_gap_m"] == 0.0


def test_screening_report_contains_initial_state_diagnostics(resolved):
    config, _ = resolved
    report = screen_candidate(_state(resolved), config.screening)
    diagnostics = report.initial_state_diagnostics
    assert diagnostics["signed_total_mass_kg"] > 0.0
    assert len(diagnostics["barycenter_position_m"]) == 3
    assert len(diagnostics["barycenter_velocity_mps"]) == 3
    assert diagnostics["minimum_surface_gap_m"] > 0.0
    assert diagnostics["maximum_relative_speed_mps"] > 0.0

