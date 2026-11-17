from __future__ import annotations

import math

import numpy as np
import pytest

from threebody.errors import ConfigError
from threebody.experiments.orbital_reader import EphemerisSample
from threebody.physics.nbody.models import BodyTrajectoryState
from threebody.physics.rotation.geometry import PlanetRotation
from threebody.physics.stellar.radiation import (
    StellarRadiation,
    latitude_band_mean_top_of_atmosphere_flux,
    latitude_band_means_vectorized,
    local_top_of_atmosphere_flux,
    zonal_mean_top_of_atmosphere_flux,
)
from threebody.physics.thermal.models import RotationSettings
from threebody.world.generators import generate_initial_state


def _radiation_fixture(resolved):
    config, digest = resolved
    state = generate_initial_state(
        config.world_generation.generator, config.experiment.seed, 0, digest
    )
    bodies = []
    positions = {
        "star-1": (1.0e11, 0.0, 0.0),
        "star-2": (0.0, 2.0e11, 0.0),
        "star-3": (0.0, 0.0, 3.0e11),
        "planet-1": (0.0, 0.0, 0.0),
    }
    for index, body in enumerate(state.bodies):
        properties = dict(body.properties)
        if body.id.startswith("star"):
            properties["luminosity_w"] = float(index + 1) * 1.0e26
        bodies.append(
            body.model_copy(
                update={
                    "position_m": positions[body.id],
                    "velocity_mps": (0.0, 0.0, 0.0),
                    "properties": properties,
                }
            )
        )
    state = state.model_copy(update={"bodies": tuple(bodies)})
    ephemeris = EphemerisSample(
        t_s=0.0,
        bodies=tuple(
            BodyTrajectoryState(
                id=body.id,
                position_m=body.position_m,
                velocity_mps=body.velocity_mps,
            )
            for body in state.bodies
        ),
    )
    return state, ephemeris


def _rotation(rate=1.0):
    return PlanetRotation(
        RotationSettings(
            axis_inertial_unit=(0.0, 0.0, 1.0),
            prime_meridian_inertial_unit=(1.0, 0.0, 0.0),
            rotation_epoch_s=0.0,
            initial_phase_rad=0.0,
            rotation_rate_rad_s=rate,
        )
    )


def test_three_star_flux_sums_and_follows_inverse_square_law(resolved):
    state, ephemeris = _radiation_fixture(resolved)
    radiation = StellarRadiation(state, "planet-1")
    snapshot = radiation.evaluate(ephemeris)
    assert len(snapshot.stars) == 3
    assert snapshot.planet_center_flux_w_m2 == pytest.approx(
        sum(star.flux_w_m2 for star in snapshot.stars)
    )
    assert snapshot.globally_averaged_incoming_w_m2 == pytest.approx(
        snapshot.planet_center_flux_w_m2 / 4.0
    )
    bodies = list(ephemeris.bodies)
    star = bodies[0]
    bodies[0] = star.model_copy(update={"position_m": (2.0e11, 0.0, 0.0)})
    farther = radiation.evaluate(EphemerisSample(t_s=1.0, bodies=tuple(bodies)))
    assert farther.stars[0].flux_w_m2 == pytest.approx(snapshot.stars[0].flux_w_m2 / 4.0)


def test_radiation_rejects_missing_luminosity_and_physical_contact(resolved):
    state, ephemeris = _radiation_fixture(resolved)
    bodies = list(state.bodies)
    bodies[0] = bodies[0].model_copy(update={"properties": {}})
    with pytest.raises(ConfigError, match="missing numeric luminosity"):
        StellarRadiation(state.model_copy(update={"bodies": tuple(bodies)}), "planet-1")
    bodies[0] = state.bodies[0].model_copy(
        update={"properties": {"luminosity_w": -1.0}}
    )
    with pytest.raises(ConfigError, match="finite and positive"):
        StellarRadiation(state.model_copy(update={"bodies": tuple(bodies)}), "planet-1")
    radiation = StellarRadiation(state, "planet-1")
    ephemeris_bodies = list(ephemeris.bodies)
    ephemeris_bodies[0] = ephemeris_bodies[0].model_copy(
        update={"position_m": (1.0, 0.0, 0.0)}
    )
    with pytest.raises(ConfigError, match="physical contact"):
        radiation.evaluate(EphemerisSample(t_s=0.0, bodies=tuple(ephemeris_bodies)))


def test_rotation_local_noon_midnight_and_sign_of_rate(resolved):
    state, ephemeris = _radiation_fixture(resolved)
    snapshot = StellarRadiation(state, "planet-1").evaluate(ephemeris)
    only_first = snapshot.__class__(
        t_s=0.0,
        planet_id=snapshot.planet_id,
        stars=(snapshot.stars[0],),
        planet_center_flux_w_m2=snapshot.stars[0].flux_w_m2,
        globally_averaged_incoming_w_m2=snapshot.stars[0].flux_w_m2 / 4.0,
    )
    rotation = _rotation(rate=1.0)
    noon = rotation.normal(0.0, 0.0, 0.0)
    midnight = rotation.normal(0.0, math.pi, 0.0)
    assert local_top_of_atmosphere_flux(only_first, noon) == pytest.approx(
        only_first.planet_center_flux_w_m2
    )
    assert local_top_of_atmosphere_flux(only_first, midnight) == 0.0
    assert rotation.normal(0.0, 0.0, math.pi / 2.0) == pytest.approx((0.0, 1.0, 0.0))
    assert _rotation(rate=-1.0).normal(0.0, 0.0, math.pi / 2.0) == pytest.approx(
        (0.0, -1.0, 0.0)
    )


def test_zonal_insolation_handles_polar_day_night_and_global_quarter_law(resolved):
    state, ephemeris = _radiation_fixture(resolved)
    snapshot = StellarRadiation(state, "planet-1").evaluate(ephemeris)
    first = snapshot.stars[0]
    one_star = snapshot.__class__(
        t_s=0.0,
        planet_id=snapshot.planet_id,
        stars=(first,),
        planet_center_flux_w_m2=first.flux_w_m2,
        globally_averaged_incoming_w_m2=first.flux_w_m2 / 4.0,
    )
    axis_toward_star = first.direction_inertial_unit
    assert zonal_mean_top_of_atmosphere_flux(
        one_star, math.pi / 2.0, axis_toward_star
    ) == pytest.approx(first.flux_w_m2)
    assert zonal_mean_top_of_atmosphere_flux(
        one_star, -math.pi / 2.0, axis_toward_star
    ) == 0.0

    count = 20_000
    x = -1.0 + (np.arange(count) + 0.5) * (2.0 / count)
    means = np.asarray(
        [
            zonal_mean_top_of_atmosphere_flux(
                snapshot, math.asin(float(value)), (0.0, 0.0, 1.0)
            )
            for value in x
        ]
    )
    assert float(np.mean(means)) == pytest.approx(
        snapshot.planet_center_flux_w_m2 / 4.0, rel=2.0e-6
    )


@pytest.mark.parametrize("bands", [4, 36, 72])
def test_vectorized_c1_insolation_matches_original_quadrature(resolved, bands):
    state, ephemeris = _radiation_fixture(resolved)
    snapshot = StellarRadiation(state, "planet-1").evaluate(ephemeris)
    axes = ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0))
    for axis in axes:
        vectorized = latitude_band_means_vectorized(snapshot, bands, axis)
        dx = 2.0 / bands
        original = np.asarray([
            latitude_band_mean_top_of_atmosphere_flux(
                snapshot, -1.0 + i*dx, -1.0 + (i+1)*dx, axis
            )
            for i in range(bands)
        ])
        assert vectorized == pytest.approx(original, rel=5e-13, abs=1e-10)
        assert float(np.mean(vectorized)) == pytest.approx(
            snapshot.planet_center_flux_w_m2/4, rel=2e-4
        )
