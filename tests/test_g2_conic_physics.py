from __future__ import annotations

import math

import numpy as np
import pytest

from threebody.common.constants import M_SUN_KG, G
from threebody.config.models import ScalarDistributionConfig, VelocityKickConfig
from threebody.world.stochastic_conics import build_conic_cartesian


def _no_kick():
    return VelocityKickConfig(
        distribution="none",
        speed_scale_fraction=ScalarDistributionConfig(
            min=0.0, max=0.0, distribution="uniform"
        ),
    )


@pytest.mark.parametrize(("eccentricity", "expected_sign"), [(0.4, -1), (1.6, 1)])
def test_conic_specific_energy_and_angular_momentum_benchmarks(
    eccentricity, expected_sign
):
    primary_mass = M_SUN_KG
    secondary_mass = 0.7 * M_SUN_KG
    result = build_conic_cartesian(
        relation="inner",
        primary_body_ids=("star-1",),
        secondary_body_id="star-2",
        host=None,
        primary_mass_kg=primary_mass,
        secondary_mass_kg=secondary_mass,
        periapsis_m=2.0e10,
        eccentricity=eccentricity,
        orientation_rng=np.random.default_rng(1234),
        kick_rng=np.random.default_rng(5678),
        kick_config=_no_kick(),
    )
    radius = float(np.linalg.norm(result.position_m))
    speed2 = float(np.dot(result.velocity_mps, result.velocity_mps))
    mu = G * (primary_mass + secondary_mass)
    energy = 0.5 * speed2 - mu / radius
    angular_momentum = float(np.linalg.norm(np.cross(result.position_m, result.velocity_mps)))
    expected_h = math.sqrt(mu * 2.0e10 * (1.0 + eccentricity))
    assert math.copysign(1.0, energy) == expected_sign
    assert energy == pytest.approx(result.record.pre_kick_specific_energy_j_kg, rel=2e-14)
    assert angular_momentum == pytest.approx(expected_h, rel=2e-14)


def test_isotropic_orientation_has_uniform_cosine_distribution():
    cosines = []
    for index in range(4000):
        result = build_conic_cartesian(
            relation="inner",
            primary_body_ids=("star-1",),
            secondary_body_id="star-2",
            host=None,
            primary_mass_kg=M_SUN_KG,
            secondary_mass_kg=M_SUN_KG,
            periapsis_m=1.0e11,
            eccentricity=0.2,
            orientation_rng=np.random.default_rng(index),
            kick_rng=np.random.default_rng(index + 10000),
            kick_config=_no_kick(),
        )
        cosines.append(math.cos(result.record.inclination_rad))
    values = np.asarray(cosines)
    assert abs(float(np.mean(values))) < 0.03
    assert float(np.var(values)) == pytest.approx(1.0 / 3.0, abs=0.02)
    assert np.count_nonzero(values > 0.0) / len(values) == pytest.approx(0.5, abs=0.03)


def test_hyperbolic_anomaly_is_inside_valid_domain():
    result = build_conic_cartesian(
        relation="planet",
        primary_body_ids=("star-1",),
        secondary_body_id="planet-1",
        host="star-1",
        primary_mass_kg=M_SUN_KG,
        secondary_mass_kg=5.9722e24,
        periapsis_m=5.0e10,
        eccentricity=2.0,
        orientation_rng=np.random.default_rng(22),
        kick_rng=np.random.default_rng(23),
        kick_config=_no_kick(),
    )
    limit = math.acos(-0.5)
    assert abs(result.record.true_anomaly_rad) < limit
    assert 1.0 + 2.0 * math.cos(result.record.true_anomaly_rad) > 0.0
    assert result.record.conic_type == "HYPERBOLIC"
