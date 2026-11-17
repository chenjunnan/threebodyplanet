"""Scientific limits for the expanded 0.3..8 M_sun main-sequence generator."""
import math

import numpy as np
import pytest

from threebody.common.constants import M_SUN_KG
from threebody.config.loader import load_and_resolve
from threebody.physics.stellar.main_sequence import (
    SOLAR_LUMINOSITY_W,
    main_sequence_properties,
)
from threebody.world.generators import generate_initial_state


def test_main_sequence_mass_luminosity_is_continuous_and_positive():
    for mass in (0.3, 0.43, 0.7, 1.0, 2.0, 4.0, 8.0):
        radius, luminosity = main_sequence_properties(mass)
        assert math.isfinite(radius) and radius > 0
        assert math.isfinite(luminosity) and luminosity > 0
    assert main_sequence_properties(1.0)[1] == pytest.approx(SOLAR_LUMINOSITY_W)
    for boundary in (0.43, 2.0):
        left = main_sequence_properties(boundary * (1-1e-9))[1]
        right = main_sequence_properties(boundary * (1+1e-9))[1]
        assert right == pytest.approx(left, rel=1e-7)
    assert main_sequence_properties(8.0)[1] > main_sequence_properties(2.0)[1]
    with pytest.raises(ValueError, match="0.3..8"):
        main_sequence_properties(8.01)


def test_generated_main_sequence_spins_and_luminosities_are_provenance_bound():
    config, digest = load_and_resolve("configs/g21_bounded_worlds.yaml")
    state = generate_initial_state(
        config.world_generation.generator, config.experiment.seed, 0, digest
    )
    for body in state.bodies:
        spin = body.properties
        axis = np.asarray(spin["rotation_axis_inertial_unit"])
        prime = np.asarray(spin["prime_meridian_inertial_unit"])
        assert np.linalg.norm(axis) == pytest.approx(1, abs=1e-12)
        assert np.linalg.norm(prime) == pytest.approx(1, abs=1e-12)
        assert float(np.dot(axis, prime)) == pytest.approx(0, abs=1e-12)
        assert 0 <= spin["obliquity_to_planet_reference_orbit_rad"] <= math.pi
        assert spin["rotation_rate_rad_s"] > 0
        if body.id.startswith("star"):
            mass = body.mass_kg / M_SUN_KG
            assert 0.3 <= mass <= 8.0
            radius, luminosity = main_sequence_properties(mass)
            assert body.radius_m == pytest.approx(radius)
            assert body.properties["luminosity_w"] == pytest.approx(luminosity)
            assert body.properties["mass_luminosity_model"] == (
                "piecewise_main_sequence_0p3_8_v1"
            )


def test_legacy_bounded_version_keeps_previous_luminosity_and_no_spin():
    config, digest = load_and_resolve("configs/g21_bounded_worlds.yaml")
    generator = config.world_generation.generator.model_copy(
        update={"version": "0.4.1"}
    )
    state = generate_initial_state(generator, config.experiment.seed, 0, digest)
    assert state.generator_version == "0.4.1"
    for body in state.bodies[:3]:
        solar_mass = body.mass_kg/M_SUN_KG
        assert body.properties["mass_luminosity_model"] == "power_3.5_v1"
        assert body.properties["luminosity_w"] == pytest.approx(
            3.828e26 * solar_mass**3.5
        )
        assert "rotation_model" not in body.properties
