"""M7.1 versioned random rotation and atmosphere-prior regression tests."""
from __future__ import annotations

import math

import numpy as np
import pytest

from threebody.common.constants import G
from threebody.common.json import sha256_json
from threebody.config.loader import load_and_resolve
from threebody.world.generators import generate_initial_state


def test_generator_042_frozen_world_hashes_are_unchanged():
    config, digest = load_and_resolve("configs/g21_bounded_worlds.yaml")
    # Transcendental libm results may differ in the final bit across supported
    # Linux runners even with the same NumPy version.  Both hashes below are
    # frozen, reviewed 0.4.2 outputs; this is not permission for an arbitrary
    # new hash.  Within one recorded runtime the generator remains exact.
    expected = {
        0: {"d51b460d5c38c3f47b456e42b6a1af6cf9bee48d10e811263d61d687d6be57b3"},
        1: {
            "beb5c0fcc395f957aba8609844e94855b2239f659b023936ac05af3e0c90f82c",
            "01a00225fc22701dc95121236c819f3595a2cc444f4afcf32412c602dbe9e99b",
        },
        7: {"20d928032a12871cc56450efb3c22d67d3f34834ef9c96f4656b70175fc70635"},
    }
    for candidate_index, state_hashes in expected.items():
        state = generate_initial_state(
            config.world_generation.generator, config.experiment.seed,
            candidate_index, digest,
        )
        assert sha256_json(state) in state_hashes
        assert state.bodies[-1].properties["rotation_period_s"] == 86400.0
        assert "atmosphere_hypothesis" not in state.bodies[-1].properties


def test_generator_043_rotation_and_atmosphere_are_reproducible_and_bounded():
    config, digest = load_and_resolve("configs/m71_random_planets.yaml")
    generator = config.world_generation.generator
    statuses = set()
    periods = set()
    for candidate_index in range(20):
        first = generate_initial_state(generator, config.experiment.seed, candidate_index, digest)
        second = generate_initial_state(generator, config.experiment.seed, candidate_index, digest)
        assert first == second
        planet = first.bodies[-1]
        props = planet.properties
        period = props["rotation_period_s"]
        periods.add(round(period, 6))
        assert generator.planet_rotation.period_s.min <= period <= (
            generator.planet_rotation.period_s.max
        )
        assert np.linalg.norm(props["rotation_axis_inertial_unit"]) == pytest.approx(1.0)
        prior = props["atmosphere_hypothesis"]
        statuses.add(prior["status"])
        assert prior["is_observation"] is False
        assert 0 <= prior["weighted_occurrence_probability"] <= (
            prior["base_occurrence_probability"]
        )
        effective_escape_sq = max(
            0.0,
            2*G*planet.mass_kg/planet.radius_m
            - (props["rotation_rate_rad_s"]*planet.radius_m)**2,
        )
        assert prior["rotation_adjusted_escape_speed_proxy_mps"] == pytest.approx(
            math.sqrt(effective_escape_sq)
        )
        composition = prior["composition_mole_fraction"]
        if prior["status"] == "PRESENT_PRIOR_SAMPLE":
            assert sum(composition.values()) == pytest.approx(1.0)
            assert 0 < prior["atmosphere_mass_kg"] <= planet.mass_kg*.0002
            assert prior["mean_molar_mass_kg_mol"] > 0
        else:
            assert prior["atmosphere_mass_kg"] == 0
            assert sum(composition.values()) == 0
    assert len(periods) == 20
    assert statuses == {"PRESENT_PRIOR_SAMPLE", "ABSENT_PRIOR_SAMPLE"}


def test_043_requires_explicit_versioned_priors():
    config, _ = load_and_resolve("configs/m71_random_planets.yaml")
    raw = config.world_generation.generator.model_dump(mode="json")
    raw["planet_rotation"] = None
    with pytest.raises(ValueError, match="requires rotation"):
        type(config.world_generation.generator).model_validate(raw, strict=True)
