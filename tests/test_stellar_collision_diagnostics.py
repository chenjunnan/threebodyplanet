"""Causal IAS15 collision capture plus explicitly hypothetical remnant observables."""
from types import SimpleNamespace

import numpy as np
import pytest

from threebody.common.constants import M_SUN_KG
from threebody.physics.nbody.events import EventObserver


def _particle(mass, pos, vel):
    return SimpleNamespace(
        m=mass, x=pos[0], y=pos[1], z=pos[2],
        vx=vel[0], vy=vel[1], vz=vel[2],
    )


def test_stellar_collision_has_classified_event_and_noncontinuation_proxy():
    observer = EventObserver(
        body_ids=("star-1", "star-2", "star-3", "planet-1"),
        radii_m=(1e9, 1e9, 1e9, 1e7),
        settings=None,
    )
    simulation = SimpleNamespace(
        t=12.0,
        particles=[
            _particle(M_SUN_KG, (-1e9, 0, 0), (1000, 0, 0)),
            _particle(2*M_SUN_KG, (1e9, 0, 0), (-2000, 0, 0)),
            _particle(M_SUN_KG, (1e12, 0, 0), (0, 0, 0)),
            _particle(6e24, (0, 2e11, 0), (0, 0, 0)),
        ],
    )
    event = observer.collision_event(simulation, (1, 0))
    assert event.event_type == "COLLISION"
    assert event.body_ids == ("star-1", "star-2")
    assert event.observed["collision_class"] == "STAR_STAR"
    proxy = event.observed["stellar_merger_scenario"]
    assert proxy["hypothetical_merged_mass_kg"] == pytest.approx(3*M_SUN_KG)
    assert proxy["relative_kinetic_energy_available_j"] > 0
    velocity = np.asarray(proxy["momentum_conserving_com_velocity_mps"])
    assert velocity[0] == pytest.approx(-1000)
    assert proxy["hypothetical_relaxed_main_sequence_luminosity_w"] > 0
    assert len(proxy["planet_acceleration_change_at_contact_m_s2"]) == 3
    assert proxy["status"] == "DIAGNOSTIC_ONLY_NO_POST_COLLISION_INTEGRATION"
    assert proxy["radiative_efficiency"] == "UNKNOWN"
    assert observer.events == [event]


def test_star_planet_contact_does_not_claim_stellar_merger():
    observer = EventObserver(
        body_ids=("star-1", "star-2", "star-3", "planet-1"),
        radii_m=(1e9, 1e9, 1e9, 1e7),
        settings=None,
    )
    particles = [
        _particle(M_SUN_KG, (0, 0, 0), (0, 0, 0)),
        _particle(M_SUN_KG, (1e11, 0, 0), (0, 0, 0)),
        _particle(M_SUN_KG, (0, 1e11, 0), (0, 0, 0)),
        _particle(6e24, (1e9, 0, 0), (0, 0, 0)),
    ]
    event = observer.collision_event(
        SimpleNamespace(t=10.0, particles=particles), (0, 3)
    )
    assert event.observed["collision_class"] == "STAR_PLANET"
    assert "stellar_merger_scenario" not in event.observed
