from __future__ import annotations

import json
import math

import numpy as np
import pytest
from pydantic import ValidationError

from threebody.common.json import write_json_atomic
from threebody.world.generators import generate_initial_state
from threebody.world.models import CelestialBody, WorldInitialState


def test_t12_same_seed_index_and_config_is_identical(resolved):
    config, digest = resolved
    generator = config.world_generation.generator
    first = generate_initial_state(generator, config.experiment.seed, 4, digest)
    second = generate_initial_state(generator, config.experiment.seed, 4, digest)
    assert first == second


def test_t13_screening_changes_do_not_change_body_random_stream(resolved):
    config, digest = resolved
    generator = config.world_generation.generator
    first = generate_initial_state(generator, config.experiment.seed, 2, digest)
    other_hash = "f" * 64
    second = generate_initial_state(generator, config.experiment.seed, 2, other_hash)
    assert first.bodies == second.bodies


def test_t14_candidate_stream_is_order_independent(resolved):
    config, digest = resolved
    generator = config.world_generation.generator
    expected = generate_initial_state(generator, config.experiment.seed, 7, digest)
    for index in (0, 1, 2, 99, 3):
        generate_initial_state(generator, config.experiment.seed, index, digest)
    actual = generate_initial_state(generator, config.experiment.seed, 7, digest)
    assert actual == expected


def test_t14_rejected_candidate_does_not_change_next_stream(resolved):
    from threebody.screening.engine import screen_candidate

    config, digest = resolved
    generator = config.world_generation.generator
    expected_next = generate_initial_state(generator, config.experiment.seed, 1, digest)
    first = generate_initial_state(generator, config.experiment.seed, 0, digest)
    bodies = list(first.bodies)
    bodies[3] = bodies[3].model_copy(update={"mass_kg": -1.0})
    rejected = screen_candidate(
        first.model_copy(update={"bodies": tuple(bodies)}), config.screening
    )
    assert rejected.decision == "REJECTED"
    assert generate_initial_state(generator, config.experiment.seed, 1, digest) == expected_next


@pytest.mark.parametrize("kind", ["hierarchical_orbit", "cartesian"])
def test_t15_both_generators_produce_four_canonical_bodies(resolved, kind):
    config, digest = resolved
    generator = config.world_generation.generator.model_copy(update={"type": kind})
    state = generate_initial_state(generator, config.experiment.seed, 0, digest)
    assert [body.id for body in state.bodies] == ["star-1", "star-2", "star-3", "planet-1"]
    assert len({body.id for body in state.bodies}) == 4
    assert all(len(body.position_m) == len(body.velocity_mps) == 3 for body in state.bodies)


def test_t16_barycenter_is_normalized(resolved):
    config, digest = resolved
    state = generate_initial_state(
        config.world_generation.generator, config.experiment.seed, 0, digest
    )
    masses = np.asarray([body.mass_kg for body in state.bodies])
    positions = np.asarray([body.position_m for body in state.bodies])
    velocities = np.asarray([body.velocity_mps for body in state.bodies])
    center_r = np.sum(masses[:, None] * positions, axis=0) / masses.sum()
    center_v = np.sum(masses[:, None] * velocities, axis=0) / masses.sum()
    relative_r = np.linalg.norm(center_r) / np.max(np.linalg.norm(positions, axis=1))
    relative_v = np.linalg.norm(center_v) / np.max(np.linalg.norm(velocities, axis=1))
    assert relative_r < 1e-12
    assert relative_v < 1e-12


def test_t17_json_round_trip_is_lossless(tmp_path, resolved):
    config, digest = resolved
    state = generate_initial_state(
        config.world_generation.generator, config.experiment.seed, 0, digest
    )
    path = tmp_path / "state.json"
    write_json_atomic(path, state)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded == state.model_dump(mode="json")
    restored = WorldInitialState.model_validate_json(
        path.read_text(encoding="utf-8"), strict=True
    )
    assert restored == state
    assert "NaN" not in path.read_text(encoding="utf-8")
    assert "Infinity" not in path.read_text(encoding="utf-8")


def test_t06_non_finite_core_values_are_rejected():
    base = {
        "id": "star-1",
        "kind": "STAR",
        "mass_kg": 1.0,
        "radius_m": 1.0,
        "position_m": (0.0, 0.0, 0.0),
        "velocity_mps": (0.0, 0.0, 0.0),
        "physical_profile_id": "test",
        "properties": {},
    }
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValidationError):
            CelestialBody(**{**base, "mass_kg": bad})
        with pytest.raises(ValidationError):
            CelestialBody(**{**base, "properties": {"temperature_k": bad}})


def test_t06_s0_reports_non_finite_as_invalid_without_echoing_value(resolved):
    from threebody.screening.engine import screen_candidate

    config, digest = resolved
    state = generate_initial_state(
        config.world_generation.generator, config.experiment.seed, 0, digest
    )
    bodies = list(state.bodies)
    bodies[0] = bodies[0].model_copy(update={"mass_kg": math.nan})
    report = screen_candidate(
        state.model_copy(update={"bodies": tuple(bodies)}), config.screening
    )
    assert report.decision == "INVALID"
    assert report.initial_state_diagnostics == {}
    assert all(result.status == "SKIP" for result in report.results)
    assert "nan" not in report.model_dump_json().lower()

