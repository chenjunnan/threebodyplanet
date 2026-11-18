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
    # Transcendental libm results vary in their final bits across supported
    # Linux runners.  Freeze 12 significant decimal digits for portable
    # scientific regression, and separately require exact replay below.
    def stable(value):
        if isinstance(value, float):
            return float(format(value, ".12g"))
        if isinstance(value, dict):
            return {key: stable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [stable(item) for item in value]
        return value

    expected = {
        0: "963daf1396d3d90f021f12ec19109b8d042476f234098a2b3ab9748182759b31",
        1: "b95df3c7d9f3481320aba5779109ea087b94a2cea90730dbee13cfad7f34fe14",
        7: "60d148c0ea407619d20aca962e8041f26677bf74c19c2bfa000a636b25186516",
    }
    for candidate_index, stable_hash in expected.items():
        state = generate_initial_state(
            config.world_generation.generator, config.experiment.seed,
            candidate_index, digest,
        )
        replay = generate_initial_state(
            config.world_generation.generator, config.experiment.seed,
            candidate_index, digest,
        )
        assert state == replay
        assert sha256_json(stable(state.model_dump(mode="python"))) == stable_hash
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
