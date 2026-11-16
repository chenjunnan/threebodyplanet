from __future__ import annotations

import math
from pathlib import Path

import pytest

from threebody.errors import CandidateInvalid, ConfigError
from threebody.physics.nbody.config import load_integration_settings
from threebody.physics.nbody.diagnostics import DiagnosticTracker, NumericalSafetyError
from threebody.physics.nbody.guard import ReboundInputGuard
from threebody.physics.nbody.models import DiagnosticSnapshot
from threebody.world.generators import generate_initial_state


def _initial_state(resolved):
    config, digest = resolved
    return generate_initial_state(
        config.world_generation.generator, config.experiment.seed, 0, digest
    )


def _replace_body(state, index, **changes):
    bodies = list(state.bodies)
    bodies[index] = bodies[index].model_copy(update=changes)
    return state.model_copy(update={"bodies": tuple(bodies)})


def test_m2_settings_load_and_hash_is_stable():
    first, first_hash = load_integration_settings("configs/m2_ias15.yaml")
    second, second_hash = load_integration_settings("configs/m2_ias15.yaml")
    assert first == second
    assert first_hash == second_hash
    assert first.backend == "rebound_ias15"


def test_m2_settings_reject_unknown_and_excess_samples(tmp_path):
    source = Path("configs/m2_ias15.yaml").read_text(encoding="utf-8")
    unknown = tmp_path / "unknown.yaml"
    unknown.write_text(source + "unknown: true\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="extra_forbidden"):
        load_integration_settings(unknown)

    excessive = tmp_path / "excessive.yaml"
    excessive.write_text(
        source.replace("max_samples: 10000", "max_samples: 2"), encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="exceeding max_samples"):
        load_integration_settings(excessive)


@pytest.mark.parametrize("mass", [-1.0, 0.0, math.nan, math.inf])
def test_guard_rejects_invalid_mass_even_if_s1_is_irrelevant(resolved, mass):
    state = _replace_body(_initial_state(resolved), 3, mass_kg=mass)
    with pytest.raises(CandidateInvalid, match="mass_kg"):
        ReboundInputGuard().validate(state)


@pytest.mark.parametrize("radius", [-1.0, math.nan, math.inf])
def test_guard_rejects_invalid_radius(resolved, radius):
    state = _replace_body(_initial_state(resolved), 3, radius_m=radius)
    with pytest.raises(CandidateInvalid, match="radius_m"):
        ReboundInputGuard().validate(state)


def test_guard_rejects_overlap_and_same_position(resolved):
    state = _initial_state(resolved)
    state = _replace_body(state, 3, position_m=state.bodies[0].position_m)
    with pytest.raises(CandidateInvalid, match="same point"):
        ReboundInputGuard().validate(state)

    separated_but_overlapping = _replace_body(
        _initial_state(resolved),
        3,
        position_m=tuple(value + 1.0 for value in state.bodies[0].position_m),
    )
    with pytest.raises(CandidateInvalid, match="overlap or touch"):
        ReboundInputGuard().validate(separated_but_overlapping)


@pytest.mark.parametrize("field", ["position_m", "velocity_mps"])
def test_guard_rejects_non_finite_vectors(resolved, field):
    state = _initial_state(resolved)
    state = _replace_body(state, 3, **{field: (math.nan, 0.0, 0.0)})
    with pytest.raises(CandidateInvalid, match="non-finite"):
        ReboundInputGuard().validate(state)


def test_raw_s1_acceptance_cannot_bypass_guard(resolved):
    from threebody.screening.engine import screen_candidate

    config, _ = resolved
    state = _replace_body(_initial_state(resolved), 3, mass_kg=-1.0)
    rules = {
        key: value.model_copy(update={"enabled": False, "failure_action": "report"})
        for key, value in config.screening.initial_physics.rules.items()
    }
    initial_physics = config.screening.initial_physics.model_copy(
        update={"enabled": False, "rules": rules}
    )
    raw_screening = config.screening.model_copy(
        update={"preset": "raw", "initial_physics": initial_physics}
    )
    assert screen_candidate(state, raw_screening).decision == "ACCEPTED"
    with pytest.raises(CandidateInvalid, match="mass_kg"):
        ReboundInputGuard().validate(state)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("units", "AU", "units must be SI"),
        ("coordinate_frame", "HELIOCENTRIC", "BARYCENTRIC_INERTIAL"),
    ],
)
def test_guard_rejects_units_and_reference_frame(resolved, field, value, message):
    state = _initial_state(resolved).model_copy(update={field: value})
    with pytest.raises(CandidateInvalid, match=message):
        ReboundInputGuard().validate(state)


def test_guard_requires_start_time_to_match_reference_epoch(resolved):
    state = _initial_state(resolved)
    settings, _ = load_integration_settings("configs/m2_ias15.yaml")
    settings = settings.model_copy(update={"start_time_s": 1.0})
    with pytest.raises(CandidateInvalid, match="start_time_s"):
        ReboundInputGuard().validate(state, settings)


@pytest.mark.parametrize(
    ("body_updates", "message"),
    [
        ({"mass_kg": 1.0e308}, "mechanical diagnostics are unsafe"),
        ({"velocity_mps": (1.0e308, 0.0, 0.0)}, "mechanical diagnostics are unsafe"),
        ({"position_m": (1.0e308, 0.0, 0.0)}, "separation exceeds"),
    ],
)
def test_guard_rejects_finite_values_outside_float64_mechanics_domain(
    resolved, body_updates, message
):
    state = _replace_body(_initial_state(resolved), 0, **body_updates)
    with pytest.raises(CandidateInvalid, match=message) as caught:
        ReboundInputGuard().validate(state)
    error_text = str(caught.value).lower()
    assert "nan" not in error_text
    assert "infinity" not in error_text


def test_diagnostic_tracker_rejects_finite_snapshots_whose_difference_overflows():
    initial = DiagnosticSnapshot(
        t_s=0.0,
        kinetic_energy_j=1.0e308,
        potential_energy_j=0.0,
        total_energy_j=1.0e308,
        energy_scale_j=1.0e308,
        linear_momentum_kg_mps=(0.0, 0.0, 0.0),
        angular_momentum_kg_m2_s=(0.0, 0.0, 0.0),
        barycenter_position_m=(0.0, 0.0, 0.0),
        barycenter_velocity_mps=(0.0, 0.0, 0.0),
    )
    later = initial.model_copy(update={"t_s": 1.0, "total_energy_j": -1.0e308})
    with pytest.raises(NumericalSafetyError, match="scaled energy error"):
        DiagnosticTracker(initial).update(later)

