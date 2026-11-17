from __future__ import annotations

import json
import math

import numpy as np
import pytest

from threebody.common.constants import G
from threebody.config.loader import load_and_resolve
from threebody.errors import CandidateInvalid
from threebody.experiments.runner import run_experiment
from threebody.physics.nbody.guard import ReboundInputGuard
from threebody.world.generators import generate_initial_state_with_metadata
from threebody.world.stochastic_conics import ConstraintExhausted


@pytest.fixture
def g21_resolved():
    return load_and_resolve("configs/g21_bounded_worlds.yaml")


def _generate(resolved, index=0):
    config, digest = resolved
    return generate_initial_state_with_metadata(
        config.world_generation.generator, config.experiment.seed, index, digest
    )


def _replace_constraints(config, **updates):
    generator = config.world_generation.generator
    constraints = generator.initial_constraints.model_copy(update=updates)
    generator = generator.model_copy(update={"initial_constraints": constraints})
    world_generation = config.world_generation.model_copy(update={"generator": generator})
    return config.model_copy(update={"world_generation": world_generation})


def test_g21_same_seed_replays_selected_attempt_and_is_order_independent(g21_resolved):
    expected = _generate(g21_resolved, 12)
    assert expected == _generate(g21_resolved, 12)
    for index in (0, 3, 99, 1, 8):
        _generate(g21_resolved, index)
    assert expected == _generate(g21_resolved, 12)
    assert expected[1].substream_scheme == (
        "SeedSequence([seed,candidate_index,attempt,stream_id])"
    )


def test_bounded_metrics_use_final_cartesian_state_after_kick(g21_resolved):
    state, metadata, _ = _generate(g21_resolved, 0)
    metrics = metadata.attempts[-1].metrics
    masses = np.asarray([body.mass_kg for body in state.bodies])
    positions = np.asarray([body.position_m for body in state.bodies])
    velocities = np.asarray([body.velocity_mps for body in state.bodies])
    distances = np.linalg.norm(positions[:3] - positions[3], axis=1)
    stellar_com_velocity = np.sum(masses[:3, None] * velocities[:3], axis=0) / np.sum(
        masses[:3]
    )
    speed = float(np.linalg.norm(velocities[3] - stellar_com_velocity))
    combined_escape = math.sqrt(2.0 * G * float(np.sum(masses[:3] / distances)))
    assert tuple(metrics.planet_star_distances_m.values()) == pytest.approx(distances)
    assert metrics.nearest_planet_star_distance_m == pytest.approx(float(np.min(distances)))
    assert metrics.planet_speed_relative_stellar_com_mps == pytest.approx(speed)
    assert metrics.combined_potential_escape_speed_proxy_mps == pytest.approx(
        combined_escape
    )
    assert metrics.combined_potential_speed_fraction == pytest.approx(
        speed / combined_escape
    )
    assert any(np.linalg.norm(record.velocity_kick_mps) > 0.0 for record in metadata.conics)


def test_every_selected_bounded_sample_satisfies_enabled_thresholds(g21_resolved):
    config, _ = g21_resolved
    constraints = config.world_generation.generator.initial_constraints
    for index in range(40):
        _, metadata, _ = _generate(g21_resolved, index)
        metrics = metadata.attempts[-1].metrics
        assert metadata.attempts[-1].accepted
        assert not metadata.attempts[-1].reason_codes
        assert metrics.nearest_planet_star_distance_m <= (
            constraints.planet_star_distance_m.max_nearest
        )
        assert metrics.planet_speed_relative_stellar_com_mps <= (
            constraints.planet_speed.absolute_max_mps
        )
        assert metrics.combined_potential_speed_fraction <= (
            constraints.planet_speed.escape_speed_fraction_max
        )


def test_unrestricted_mode_keeps_first_draw_despite_impossible_thresholds(g21_resolved):
    config, digest = g21_resolved
    speed = config.world_generation.generator.initial_constraints.planet_speed.model_copy(
        update={"absolute_max_mps": 1.0e-12}
    )
    unrestricted = _replace_constraints(
        config, mode="unrestricted", max_attempts=99, planet_speed=speed
    )
    state, metadata, _ = generate_initial_state_with_metadata(
        unrestricted.world_generation.generator, config.experiment.seed, 0, digest
    )
    assert state.generator_version == "0.4.1"
    assert metadata.constraint_mode == "unrestricted"
    assert metadata.selected_attempt == 0
    assert metadata.attempt_count == 1
    assert metadata.attempts[0].accepted
    assert metadata.attempts[0].metrics.planet_speed_relative_stellar_com_mps > 1.0e-12


def test_selected_host_escape_proxy_uses_host_relative_state(g21_resolved):
    config, digest = g21_resolved
    generator = config.world_generation.generator
    weights = {"star-1": 1.0, "star-2": 0.0, "star-3": 0.0, "barycenter": 0.0}
    planet = generator.geometry.planet.model_copy(update={"host_weights": weights})
    geometry = generator.geometry.model_copy(update={"planet": planet})
    speed = generator.initial_constraints.planet_speed.model_copy(
        update={"escape_mode": "selected_host", "escape_speed_fraction_max": 100.0}
    )
    constraints = generator.initial_constraints.model_copy(update={"planet_speed": speed})
    generator = generator.model_copy(
        update={"geometry": geometry, "initial_constraints": constraints}
    )
    state, metadata, _ = generate_initial_state_with_metadata(
        generator, config.experiment.seed, 4, digest
    )
    assert metadata.selected_planet_host == "star-1"
    star, planet_body = state.bodies[0], state.bodies[3]
    distance = float(
        np.linalg.norm(np.asarray(planet_body.position_m) - np.asarray(star.position_m))
    )
    relative_speed = float(
        np.linalg.norm(np.asarray(planet_body.velocity_mps) - np.asarray(star.velocity_mps))
    )
    escape = math.sqrt(2.0 * G * (star.mass_kg + planet_body.mass_kg) / distance)
    metrics = metadata.attempts[-1].metrics
    assert metrics.selected_host_escape_speed_proxy_mps == pytest.approx(escape)
    assert metrics.selected_host_speed_fraction == pytest.approx(relative_speed / escape)


def test_constraint_exhaustion_is_finite_and_batch_continues(tmp_path, g21_resolved):
    config, digest = g21_resolved
    speed = config.world_generation.generator.initial_constraints.planet_speed.model_copy(
        update={
            "absolute_max_mps": 1.0e-12,
            "escape_mode": "none",
            "escape_speed_fraction_max": None,
        }
    )
    impossible = _replace_constraints(
        config, mode="bounded", max_attempts=3, planet_speed=speed,
        allow_initial_contact=False,
    )
    with pytest.raises(ConstraintExhausted) as caught:
        generate_initial_state_with_metadata(
            impossible.world_generation.generator, config.experiment.seed, 0, digest
        )
    assert caught.value.metadata["attempt_count"] == 3
    assert all(
        "PLANET_SPEED_ABOVE_ABSOLUTE_MAX" in attempt["reason_codes"]
        for attempt in caught.value.metadata["attempts"]
    )

    summary = run_experiment(impossible, digest, tmp_path, count=2)
    assert summary["invalid"] == 2
    assert summary["g2_generation"]["generation_invalid_reason_codes"] == {
        "GENERATION_CONSTRAINT_EXHAUSTED": 2
    }
    for index in range(2):
        payload = json.loads(
            (tmp_path / "candidates" / f"{index:06d}.generation_metadata.json").read_text()
        )
        assert payload["attempt_count"] == 3
        assert payload["reason_code"] == "GENERATION_CONSTRAINT_EXHAUSTED"


def test_initial_contact_policy_is_explicit_and_m2_guard_remains_mandatory(g21_resolved):
    config, digest = g21_resolved
    generator = config.world_generation.generator
    inner = generator.geometry.inner.model_copy(
        update={
            "periapsis_m": generator.geometry.inner.periapsis_m.model_copy(
                update={"min": 1.0e6, "max": 1.0e6, "distribution": "uniform"}
            ),
            "eccentricity": generator.geometry.inner.eccentricity.model_copy(
                update={"min": 0.0, "max": 0.0, "distribution": "uniform"}
            ),
        }
    )
    geometry = generator.geometry.model_copy(update={"inner": inner})
    allow = generator.model_copy(update={"geometry": geometry})
    state, metadata, contacts = generate_initial_state_with_metadata(
        allow, config.experiment.seed, 0, digest
    )
    assert contacts
    assert metadata.attempts[-1].accepted
    with pytest.raises(CandidateInvalid, match="overlap or touch"):
        ReboundInputGuard().validate(state)

    reject_constraints = allow.initial_constraints.model_copy(
        update={"allow_initial_contact": False, "max_attempts": 2}
    )
    reject = allow.model_copy(update={"initial_constraints": reject_constraints})
    with pytest.raises(ConstraintExhausted) as caught:
        generate_initial_state_with_metadata(reject, config.experiment.seed, 0, digest)
    assert all(
        "INITIAL_CONTACT_NOT_ALLOWED" in attempt["reason_codes"]
        for attempt in caught.value.metadata["attempts"]
    )


def test_allowed_contact_does_not_bypass_distance_speed_or_escape_bounds(g21_resolved):
    config, digest = g21_resolved
    generator = config.world_generation.generator
    inner = generator.geometry.inner.model_copy(
        update={
            "periapsis_m": generator.geometry.inner.periapsis_m.model_copy(
                update={"min": 1.0e6, "max": 1.0e6, "distribution": "uniform"}
            ),
            "eccentricity": generator.geometry.inner.eccentricity.model_copy(
                update={"min": 0.0, "max": 0.0, "distribution": "uniform"}
            ),
        }
    )
    geometry = generator.geometry.model_copy(update={"inner": inner})
    distances = generator.initial_constraints.planet_star_distance_m.model_copy(
        update={"max_nearest": 1.0}
    )
    speed = generator.initial_constraints.planet_speed.model_copy(
        update={
            "absolute_max_mps": 1.0e-12,
            "escape_mode": "combined_potential",
            "escape_speed_fraction_max": 1.0e-12,
        }
    )
    constraints = generator.initial_constraints.model_copy(
        update={
            "allow_initial_contact": True,
            "max_attempts": 2,
            "planet_star_distance_m": distances,
            "planet_speed": speed,
        }
    )
    generator = generator.model_copy(
        update={"geometry": geometry, "initial_constraints": constraints}
    )

    with pytest.raises(ConstraintExhausted) as caught:
        generate_initial_state_with_metadata(generator, config.experiment.seed, 0, digest)

    expected = {
        "NEAREST_STAR_DISTANCE_ABOVE_MAX",
        "PLANET_SPEED_ABOVE_ABSOLUTE_MAX",
        "COMBINED_POTENTIAL_ESCAPE_FRACTION_ABOVE_MAX",
    }
    for attempt in caught.value.metadata["attempts"]:
        assert attempt["metrics"]["initial_contact_count"] > 0
        assert "INITIAL_CONTACT_NOT_ALLOWED" not in attempt["reason_codes"]
        assert expected.issubset(attempt["reason_codes"])
