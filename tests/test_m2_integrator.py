from __future__ import annotations

import math
from itertools import pairwise

import numpy as np

from threebody.common.constants import M_EARTH_KG, M_SUN_KG, G
from threebody.physics.nbody.models import (
    EncounterSettings,
    EscapeSettings,
    IntegrationSettings,
)
from threebody.physics.nbody.rebound_ias15 import ReboundIAS15Integrator
from threebody.world.models import BodyKind, CelestialBody, WorldInitialState


def _settings(duration_s: float, output_interval_s: float, epsilon: float = 1.0e-11):
    return IntegrationSettings(
        start_time_s=0.0,
        duration_s=float(duration_s),
        output_interval_s=float(output_interval_s),
        epsilon=epsilon,
        encounter=EncounterSettings(enabled=False),
        escape=EscapeSettings(enabled=False, boundary_radius_m=1.0e20),
        max_samples=100,
    )


def _body(body_id, kind, mass, position, velocity, radius=0.0):
    return CelestialBody(
        id=body_id,
        kind=kind,
        mass_kg=float(mass),
        radius_m=float(radius),
        position_m=tuple(float(value) for value in position),
        velocity_mps=tuple(float(value) for value in velocity),
        physical_profile_id="m2_test",
    )


def _weakly_perturbed_two_body_world() -> tuple[WorldInitialState, float]:
    separation = 1.0e10
    first_mass = M_SUN_KG
    second_mass = M_EARTH_KG
    total_mass = first_mass + second_mass
    relative_speed = math.sqrt(G * total_mass / separation)
    period = 2.0 * math.pi * math.sqrt(separation**3 / (G * total_mass))
    first_position = (-second_mass / total_mass * separation, 0.0, 0.0)
    second_position = (first_mass / total_mass * separation, 0.0, 0.0)
    first_velocity = (0.0, -second_mass / total_mass * relative_speed, 0.0)
    second_velocity = (0.0, first_mass / total_mass * relative_speed, 0.0)
    bodies = (
        _body("star-1", BodyKind.STAR, first_mass, first_position, first_velocity),
        _body("star-2", BodyKind.STAR, second_mass, second_position, second_velocity),
        _body("star-3", BodyKind.STAR, 1.0, (1.0e16, 0.0, 0.0), (0.0, 1.0, 0.0)),
        _body("planet-1", BodyKind.PLANET, 1.0, (-1.0e16, 0.0, 0.0), (0.0, -1.0, 0.0)),
    )
    state = WorldInitialState(
        world_id="candidate-000000",
        seed=0,
        candidate_index=0,
        generator_id="m2_test",
        generator_version="0.1.0",
        config_sha256="0" * 64,
        bodies=bodies,
    )
    return state, period


def test_ias15_two_body_period_and_conservation_benchmark():
    state, period = _weakly_perturbed_two_body_world()
    run, samples = ReboundIAS15Integrator().integrate(
        state, _settings(period, period / 4.0, epsilon=1.0e-12)
    )
    assert run.status == "COMPLETED"
    assert run.t_final_s == period
    assert len(samples) == 5
    assert all(later.t_s > earlier.t_s for earlier, later in pairwise(samples))
    initial_relative = np.asarray(samples[0].bodies[1].position_m) - np.asarray(
        samples[0].bodies[0].position_m
    )
    final_relative = np.asarray(samples[-1].bodies[1].position_m) - np.asarray(
        samples[-1].bodies[0].position_m
    )
    relative_return_error = np.linalg.norm(final_relative - initial_relative) / np.linalg.norm(
        initial_relative
    )
    assert relative_return_error < 1e-8
    assert run.diagnostic_summary.maximum_scaled_energy_error < 1e-10
    assert run.diagnostic_summary.maximum_angular_momentum_relative_error < 1e-10


def test_ias15_uses_all_four_masses_and_preserves_exact_initial_sample():
    state, period = _weakly_perturbed_two_body_world()
    settings = _settings(period / 8.0, period / 8.0)
    baseline, baseline_samples = ReboundIAS15Integrator().integrate(state, settings)
    bodies = list(state.bodies)
    bodies[1] = bodies[1].model_copy(update={"mass_kg": bodies[1].mass_kg * 1000.0})
    changed_state = state.model_copy(update={"bodies": tuple(bodies)})
    changed, changed_samples = ReboundIAS15Integrator().integrate(changed_state, settings)
    assert baseline.status == changed.status == "COMPLETED"
    assert baseline_samples[0].bodies[0].position_m == state.bodies[0].position_m
    assert baseline_samples[0].bodies[0].velocity_mps == state.bodies[0].velocity_mps
    displacement = np.linalg.norm(
        np.asarray(changed_samples[-1].bodies[0].position_m)
        - np.asarray(baseline_samples[-1].bodies[0].position_m)
    )
    assert displacement > 1.0


def test_non_divisible_output_interval_includes_true_endpoint():
    state, _ = _weakly_perturbed_two_body_world()
    run, samples = ReboundIAS15Integrator().integrate(state, _settings(10.0, 3.0))
    assert run.status == "COMPLETED"
    assert [sample.t_s for sample in samples] == [0.0, 3.0, 6.0, 9.0, 10.0]


def test_same_state_environment_and_settings_are_reproducible():
    state, period = _weakly_perturbed_two_body_world()
    settings = _settings(period / 3.0, period / 6.0)
    first_run, first_samples = ReboundIAS15Integrator().integrate(state, settings)
    second_run, second_samples = ReboundIAS15Integrator().integrate(state, settings)
    assert first_samples == second_samples
    assert first_run.events == second_run.events
    assert first_run.diagnostic_summary == second_run.diagnostic_summary

