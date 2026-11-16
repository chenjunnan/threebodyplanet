from __future__ import annotations

from types import SimpleNamespace

import pytest

from threebody.physics.nbody.events import EventObserver
from threebody.physics.nbody.models import (
    EncounterSettings,
    EscapeSettings,
    IntegrationSettings,
)
from threebody.physics.nbody.rebound_ias15 import ReboundIAS15Integrator
from threebody.world.models import BodyKind, CelestialBody, WorldInitialState


def _body(body_id, kind, mass, radius, x, vx):
    return CelestialBody(
        id=body_id,
        kind=kind,
        mass_kg=float(mass),
        radius_m=float(radius),
        position_m=(float(x), 0.0, 0.0),
        velocity_mps=(float(vx), 0.0, 0.0),
        physical_profile_id="event_test",
    )


def _collision_world(speed: float) -> WorldInitialState:
    return WorldInitialState(
        world_id="candidate-000000",
        seed=0,
        candidate_index=0,
        generator_id="event_test",
        generator_version="0.1.0",
        config_sha256="1" * 64,
        bodies=(
            _body("star-1", BodyKind.STAR, 1.0e10, 1.0, -10.0, speed),
            _body("star-2", BodyKind.STAR, 1.0e10, 1.0, 10.0, -speed),
            _body("star-3", BodyKind.STAR, 1.0, 0.0, 1.0e7, 0.0),
            _body("planet-1", BodyKind.PLANET, 1.0, 0.0, -1.0e7, 0.0),
        ),
    )


def _settings(duration: float, interval: float, *, encounter_enabled: bool = True):
    return IntegrationSettings(
        start_time_s=0.0,
        duration_s=duration,
        output_interval_s=interval,
        epsilon=1.0e-10,
        encounter=EncounterSettings(
            enabled=encounter_enabled,
            threshold_mode="absolute_m",
            distance_m=5.0,
            radius_multiplier=None,
        ),
        escape=EscapeSettings(enabled=False, boundary_radius_m=1.0e20),
        max_samples=100,
    )


@pytest.mark.parametrize(
    ("speed", "duration", "interval"),
    [(1.0, 20.0, 10.0), (1000.0, 1.0, 1.0)],
)
def test_low_and_high_speed_collision_terminates_between_output_samples(
    speed, duration, interval
):
    run, samples = ReboundIAS15Integrator().integrate(
        _collision_world(speed), _settings(duration, interval)
    )
    assert run.status == "COLLISION"
    assert 0.0 < run.t_final_s < interval
    assert samples[-1].t_s == run.t_final_s
    collision = next(event for event in run.events if event.event_type == "COLLISION")
    assert collision.body_ids == ("star-1", "star-2")
    assert collision.detection_method == "rebound_line_collision_callback"
    assert set(collision.observed["rebound_particle_indices"]) == {0, 1}
    assert run.diagnostic_summary.conservation_applicable is False


def test_collision_uses_callback_pair_not_closest_pair_at_halt():
    state = _collision_world(1000.0)
    bodies = list(state.bodies)
    bodies[2] = bodies[2].model_copy(
        update={"mass_kg": 1.0e-30, "position_m": (1.0e6, 0.0, 0.0), "radius_m": 0.0}
    )
    bodies[3] = bodies[3].model_copy(
        update={
            "mass_kg": 1.0e-30,
            "position_m": (1.0e6 + 0.1, 0.0, 0.0),
            "radius_m": 0.0,
        }
    )
    adversarial_state = state.model_copy(update={"bodies": tuple(bodies)})
    run, samples = ReboundIAS15Integrator().integrate(
        adversarial_state, _settings(1.0, 1.0)
    )
    assert run.status == "COLLISION"
    collision = next(event for event in run.events if event.event_type == "COLLISION")
    assert collision.body_ids == ("star-1", "star-2")
    assert set(collision.observed["rebound_particle_indices"]) == {0, 1}
    assert tuple(body.id for body in samples[-1].bodies) == (
        "star-1",
        "star-2",
        "star-3",
        "planet-1",
    )
    assert samples[-1].bodies[2].position_m[0] < samples[-1].bodies[3].position_m[0]


def test_collision_event_never_reassigns_callback_pair_to_closest_gap():
    observer = EventObserver(
        body_ids=("a", "b", "c", "d"),
        radii_m=(1.0, 1.0, 1.0, 1.0),
        settings=_settings(10.0, 1.0),
    )
    simulation = _fake_simulation(
        1.0,
        [
            (1.0, -5.0, 0.0),
            (1.0, 5.0, 0.0),
            (1.0, 100.0, 0.0),
            (1.0, 100.1, 0.0),
        ],
    )
    event = observer.collision_event(simulation, (1, 0))
    assert event.body_ids == ("a", "b")
    assert event.observed["rebound_particle_indices"] == [1, 0]
    assert event.observed["surface_gap_at_halt_m"] == 8.0


def _fake_simulation(t_s, body_specs):
    particles = [
        SimpleNamespace(m=mass, x=x, y=0.0, z=0.0, vx=vx, vy=0.0, vz=0.0)
        for mass, x, vx in body_specs
    ]
    return SimpleNamespace(t=float(t_s), particles=particles)


def test_close_encounter_records_first_observed_crossing_and_minimum_distance():
    observer = EventObserver(
        body_ids=("a", "b", "c", "d"),
        radii_m=(1.0, 1.0, 1.0, 1.0),
        settings=_settings(10.0, 1.0),
    )
    far_bodies = [(1.0, 100.0, 0.0), (1.0, 200.0, 0.0)]
    observer.observe(
        _fake_simulation(0.0, [(1.0, -10.0, 0.0), (1.0, 10.0, 0.0), *far_bodies]),
        "test_endpoint",
    )
    observer.observe(
        _fake_simulation(1.0, [(1.0, -2.0, 0.0), (1.0, 2.0, 0.0), *far_bodies]),
        "test_endpoint",
    )
    observer.observe(
        _fake_simulation(2.0, [(1.0, -1.0, 0.0), (1.0, 1.0, 0.0), *far_bodies]),
        "test_endpoint",
    )
    encounters = [event for event in observer.events if event.event_type == "CLOSE_ENCOUNTER"]
    assert len(encounters) == 1
    assert encounters[0].t_s == 1.0
    assert observer.minimum_observed_pair_distances_m["a:b"] == 2.0
    assert encounters[0].observed["observed_distance_m"] == 4.0
    assert (
        encounters[0].observed["distance_semantics"]
        == "internal_step_endpoint_observation"
    )


def test_escape_requires_persistence_and_is_only_a_candidate():
    settings = IntegrationSettings(
        start_time_s=0.0,
        duration_s=100.0,
        output_interval_s=10.0,
        encounter=EncounterSettings(enabled=False),
        escape=EscapeSettings(
            enabled=True,
            boundary_radius_m=10.0,
            minimum_duration_s=10.0,
        ),
        max_samples=20,
    )
    observer = EventObserver(
        body_ids=("a", "b", "c", "d"), radii_m=(0.0, 0.0, 0.0, 0.0), settings=settings
    )
    base = [(1.0e10, 0.0, 0.0), (1.0e10, 1.0, 0.0), (1.0e10, -1.0, 0.0)]
    observer.observe(_fake_simulation(0.0, [*base, (1.0, 20.0, 10.0)]), "test")
    observer.observe(_fake_simulation(5.0, [*base, (1.0, 5.0, -10.0)]), "test")
    observer.observe(_fake_simulation(20.0, [*base, (1.0, 20.0, 10.0)]), "test")
    observer.observe(_fake_simulation(25.0, [*base, (1.0, 30.0, 10.0)]), "test")
    assert not [event for event in observer.events if event.event_type == "ESCAPE_CANDIDATE"]
    observer.observe(_fake_simulation(31.0, [*base, (1.0, 40.0, 10.0)]), "test")
    events = [event for event in observer.events if event.event_type == "ESCAPE_CANDIDATE"]
    assert len(events) == 1
    assert "not proven" in events[0].message

