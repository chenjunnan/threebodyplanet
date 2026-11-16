from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from threebody.common.constants import M_EARTH_KG, M_SUN_KG, R_SUN_M, G
from threebody.common.json import sha256_json
from threebody.config.loader import load_and_resolve
from threebody.errors import CandidateInvalid, ConfigError
from threebody.experiments.runner import run_experiment
from threebody.physics.nbody.guard import ReboundInputGuard
from threebody.world.generators import (
    generate_initial_state,
    generate_initial_state_with_metadata,
)


@pytest.fixture
def g2_resolved():
    return load_and_resolve("configs/g2_chaotic_worlds.yaml")


def test_g2_strict_config_and_replay_are_deterministic(g2_resolved):
    config, digest = g2_resolved
    generator = config.world_generation.generator
    first = generate_initial_state_with_metadata(generator, config.experiment.seed, 17, digest)
    second = generate_initial_state_with_metadata(generator, config.experiment.seed, 17, digest)
    assert first == second
    assert first[0].generator_id == "stochastic_conics"
    assert first[1].substream_scheme == "SeedSequence([seed,candidate_index,stream_id])"
    assert first[1].post_kick_elements_claimed is False


def test_g2_candidate_stream_is_order_independent(g2_resolved):
    config, digest = g2_resolved
    generator = config.world_generation.generator
    expected = generate_initial_state_with_metadata(generator, config.experiment.seed, 9, digest)
    for index in (4, 1, 20, 0, 3):
        generate_initial_state_with_metadata(generator, config.experiment.seed, index, digest)
    assert generate_initial_state_with_metadata(
        generator, config.experiment.seed, 9, digest
    ) == expected


def test_g2_unknown_fields_and_fixed_parabolic_mode_are_rejected(tmp_path):
    source = Path("configs/g2_chaotic_worlds.yaml").read_text(encoding="utf-8")
    unknown = tmp_path / "unknown.yaml"
    unknown.write_text(
        source.replace(
            "    version: \"0.4.0\"",
            "    version: \"0.4.0\"\n    bogus: 1",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="extra_forbidden"):
        load_and_resolve(unknown)

    parabolic = tmp_path / "parabolic.yaml"
    parabolic.write_text(
        source.replace(
            "eccentricity: {min: 0.0, max: 1.8, distribution: uniform}",
            "eccentricity: {min: 1.0, max: 1.0, distribution: uniform}",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="parabolic eccentricity"):
        load_and_resolve(parabolic)


def test_legacy_config_digest_and_initial_state_golden_outputs_are_unchanged():
    config, digest = load_and_resolve("configs/example_experiment.yaml")
    assert digest == "2047e119cba0fc03957807b39271bc418f787a486489b60335c5997ba6f44a0a"
    for index in range(3):
        state = generate_initial_state(
            config.world_generation.generator, config.experiment.seed, index, digest
        )
        reference = _legacy_hierarchical_reference(state, config, index)
        assert sha256_json(state) == sha256_json(reference)


def _legacy_orientation(rng):
    normal = rng.normal(size=3)
    normal /= np.linalg.norm(normal)
    helper = (
        np.array([1.0, 0.0, 0.0])
        if abs(normal[0]) < 0.8
        else np.array([0.0, 1.0, 0.0])
    )
    first = np.cross(normal, helper)
    first /= np.linalg.norm(first)
    second = np.cross(normal, first)
    phase = rng.uniform(0.0, 2.0 * math.pi)
    return (
        math.cos(phase) * first + math.sin(phase) * second,
        -math.sin(phase) * first + math.cos(phase) * second,
    )


def _legacy_add_relative(
    positions, velocities, first_indices, first_mass, second_index, second_mass,
    separation, velocity_factor, rng
):
    radial, tangent = _legacy_orientation(rng)
    total = first_mass + second_mass
    relative_speed = math.sqrt(G * total / separation) * velocity_factor
    positions[list(first_indices)] += -second_mass / total * separation * radial
    positions[second_index] += first_mass / total * separation * radial
    velocities[list(first_indices)] += -second_mass / total * relative_speed * tangent
    velocities[second_index] += first_mass / total * relative_speed * tangent


def _legacy_hierarchical_reference(state, config, candidate_index):
    generator = config.world_generation.generator
    sequence = np.random.SeedSequence([config.experiment.seed, candidate_index, 0])
    rng = np.random.Generator(np.random.PCG64(sequence))
    star_solar = rng.uniform(*generator.stellar_mass_solar, size=3)
    planet_earth = float(rng.uniform(*generator.planet_mass_earth))
    density = float(rng.uniform(*generator.planet_density_kg_m3))
    masses = np.concatenate((star_solar * M_SUN_KG, [planet_earth * M_EARTH_KG]))
    radii = np.concatenate(
        (
            R_SUN_M * np.power(star_solar, 0.8),
            [(3.0 * masses[3] / (4.0 * math.pi * density)) ** (1.0 / 3.0)],
        )
    )
    positions = np.zeros((4, 3), dtype=np.float64)
    velocities = np.zeros((4, 3), dtype=np.float64)
    _legacy_add_relative(
        positions, velocities, (0,), masses[0], 1, masses[1],
        generator.inner_orbit_radius_m, generator.velocity_scale_factor, rng,
    )
    _legacy_add_relative(
        positions, velocities, (0, 1), masses[0] + masses[1], 2, masses[2],
        generator.outer_orbit_radius_m, generator.velocity_scale_factor, rng,
    )
    host_index = int(generator.planet_orbit_host[-1]) - 1
    host_mass = float(masses[host_index])
    host_position = positions[host_index].copy()
    host_velocity = velocities[host_index].copy()
    radial, tangent = _legacy_orientation(rng)
    positions[3] = host_position + generator.planet_orbit_radius_m * radial
    planet_speed = math.sqrt(
        G * (host_mass + masses[3]) / generator.planet_orbit_radius_m
    )
    velocities[3] = host_velocity + planet_speed * tangent
    recoil_v = masses[3] / (host_mass + masses[3]) * (velocities[3] - host_velocity)
    velocities[host_index] -= recoil_v
    total_mass = float(np.sum(masses))
    positions -= np.sum(masses[:, None] * positions, axis=0) / total_mass
    velocities -= np.sum(masses[:, None] * velocities, axis=0) / total_mass

    star_luminosities = np.power(star_solar, 3.5) * 3.828e26
    properties = [
        {"luminosity_w": float(value), "mass_luminosity_model": "power_3.5_v1"}
        for value in star_luminosities
    ]
    properties.append(
        {"mean_density_kg_m3": density, "composition_profile_id": "rocky_mean_v1"}
    )
    bodies = tuple(
        body.model_copy(
            update={
                "mass_kg": float(masses[index]),
                "radius_m": float(radii[index]),
                "position_m": tuple(float(value) for value in positions[index]),
                "velocity_mps": tuple(float(value) for value in velocities[index]),
                "properties": properties[index],
            }
        )
        for index, body in enumerate(state.bodies)
    )
    return state.model_copy(update={"bodies": bodies})


def test_g2_distributions_bounds_hosts_and_barycenter(g2_resolved):
    config, digest = g2_resolved
    generator = config.world_generation.generator
    seen_hosts: set[str] = set()
    seen_hyperbolic = False
    for index in range(80):
        state, metadata, _ = generate_initial_state_with_metadata(
            generator, config.experiment.seed, index, digest
        )
        seen_hosts.add(metadata.selected_planet_host)
        star_masses = metadata.sampled_physical.stellar_mass_solar
        assert all(
            generator.stars.mass_solar.min <= value <= generator.stars.mass_solar.max
            for value in star_masses
        )
        for record, conic_config in zip(
            metadata.conics,
            (generator.geometry.inner, generator.geometry.outer, generator.geometry.planet),
            strict=True,
        ):
            assert (
                conic_config.periapsis_m.min
                <= record.periapsis_m
                <= conic_config.periapsis_m.max
            )
            assert (
                conic_config.eccentricity.min
                <= record.eccentricity
                <= conic_config.eccentricity.max
            )
            assert record.radius_m > 0.0
            seen_hyperbolic |= record.conic_type == "HYPERBOLIC"
        masses = np.asarray([body.mass_kg for body in state.bodies])
        positions = np.asarray([body.position_m for body in state.bodies])
        velocities = np.asarray([body.velocity_mps for body in state.bodies])
        inner_record, outer_record, planet_record = metadata.conics
        assert positions[1] - positions[0] == pytest.approx(
            inner_record.relative_position_m, rel=1.0e-11, abs=1.0e-2
        )
        assert velocities[1] - velocities[0] == pytest.approx(
            inner_record.relative_velocity_post_kick_mps, rel=1.0e-11, abs=1.0e-8
        )
        inner_mass = masses[0] + masses[1]
        inner_position = (masses[0] * positions[0] + masses[1] * positions[1]) / inner_mass
        inner_velocity = (
            masses[0] * velocities[0] + masses[1] * velocities[1]
        ) / inner_mass
        assert positions[2] - inner_position == pytest.approx(
            outer_record.relative_position_m, rel=1.0e-11, abs=1.0e-2
        )
        assert velocities[2] - inner_velocity == pytest.approx(
            outer_record.relative_velocity_post_kick_mps, rel=1.0e-11, abs=1.0e-8
        )
        if metadata.selected_planet_host == "barycenter":
            indices = np.asarray([0, 1, 2])
        else:
            indices = np.asarray([int(metadata.selected_planet_host[-1]) - 1])
        host_mass = float(np.sum(masses[indices]))
        host_position = np.sum(masses[indices, None] * positions[indices], axis=0) / host_mass
        host_velocity = np.sum(masses[indices, None] * velocities[indices], axis=0) / host_mass
        assert positions[3] - host_position == pytest.approx(
            planet_record.relative_position_m, rel=1.0e-11, abs=1.0e-2
        )
        assert velocities[3] - host_velocity == pytest.approx(
            planet_record.relative_velocity_post_kick_mps, rel=1.0e-11, abs=1.0e-8
        )
        assert np.linalg.norm(np.sum(masses[:, None] * positions, axis=0)) / (
            masses.sum() * max(np.max(np.linalg.norm(positions, axis=1)), 1.0)
        ) < 2.0e-15
        assert np.linalg.norm(np.sum(masses[:, None] * velocities, axis=0)) / (
            masses.sum() * max(np.max(np.linalg.norm(velocities, axis=1)), 1.0)
        ) < 2.0e-15
    assert seen_hosts == {"star-1", "star-2", "star-3", "barycenter"}
    assert seen_hyperbolic


def test_g2_batch_archives_metadata_statistics_and_no_stability_claim(tmp_path, g2_resolved):
    config, digest = g2_resolved
    summary = run_experiment(config, digest, tmp_path, count=12)
    g2 = summary["g2_generation"]
    assert g2["generated"] == 12
    assert g2["generation_invalid"] == 0
    assert sum(g2["selected_planet_hosts"].values()) == 12
    assert len(g2["candidate_outcomes"]) == 12
    assert "no generated candidate is claimed stable" in g2["scientific_claim"]
    for index in range(12):
        path = tmp_path / "candidates" / f"{index:06d}.generation_metadata.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["schema_version"] == "0.4.0"
        assert payload["scientific_claim"].startswith("stochastic initial state only")


def test_generation_failure_keeps_machine_readable_reason_code(tmp_path, g2_resolved):
    config, digest = g2_resolved
    generator = config.world_generation.generator
    exact_parabolic = generator.geometry.inner.eccentricity.model_copy(
        update={"min": 1.0, "max": 1.0, "distribution": "uniform"}
    )
    inner = generator.geometry.inner.model_copy(update={"eccentricity": exact_parabolic})
    geometry = generator.geometry.model_copy(update={"inner": inner})
    invalid_generator = generator.model_copy(update={"geometry": geometry})
    world_generation = config.world_generation.model_copy(update={"generator": invalid_generator})
    invalid_config = config.model_copy(update={"world_generation": world_generation})
    summary = run_experiment(invalid_config, digest, tmp_path, count=1)
    assert summary["invalid"] == 1
    assert summary["g2_generation"]["generation_invalid_reason_codes"] == {
        "PARABOLIC_UNSUPPORTED": 1
    }
    metadata = json.loads(
        (tmp_path / "candidates" / "000000.generation_metadata.json").read_text(
            encoding="utf-8"
        )
    )
    assert metadata["status"] == "INVALID"
    assert metadata["reason_code"] == "PARABOLIC_UNSUPPORTED"


def test_initial_contact_is_terminal_artifact_and_remains_rejected_by_m2_guard(
    tmp_path, g2_resolved
):
    config, digest = g2_resolved
    generator = config.world_generation.generator
    fixed_distance = generator.geometry.inner.periapsis_m.model_copy(
        update={"min": 1.0e6, "max": 1.0e6, "distribution": "uniform"}
    )
    fixed_eccentricity = generator.geometry.inner.eccentricity.model_copy(
        update={"min": 0.0, "max": 0.0, "distribution": "uniform"}
    )
    inner = generator.geometry.inner.model_copy(
        update={"periapsis_m": fixed_distance, "eccentricity": fixed_eccentricity}
    )
    geometry = generator.geometry.model_copy(update={"inner": inner})
    contact_generator = generator.model_copy(update={"geometry": geometry})
    world_generation = config.world_generation.model_copy(update={"generator": contact_generator})
    contact_config = config.model_copy(update={"world_generation": world_generation})
    state, _, contacts = generate_initial_state_with_metadata(
        contact_generator, config.experiment.seed, 0, digest
    )
    assert contacts
    assert contacts[0].status == "INITIAL_CONTACT"
    assert contacts[0].integration_performed is False
    with pytest.raises(CandidateInvalid, match="overlap or touch"):
        ReboundInputGuard().validate(state)

    summary = run_experiment(contact_config, digest, tmp_path, count=1)
    assert summary["g2_generation"]["initial_terminal_worlds"] == 1
    outcome = json.loads(
        (tmp_path / "candidates" / "000000.initial_outcome.json").read_text(encoding="utf-8")
    )
    assert outcome[0]["event_type"] == "INITIAL_COLLISION"
    assert outcome[0]["integration_performed"] is False
    # Research screening reports the contact but does not silently filter the game outcome.
    assert summary["accepted"] == 1
