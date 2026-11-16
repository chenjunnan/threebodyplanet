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
    assert collision.detection_method == "rebound_line_internal_step"
    assert run.diagnostic_summary.conservation_applicable is False


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
    assert observer.minimum_distances_m["a:b"] == 2.0


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

