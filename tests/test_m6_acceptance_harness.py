"""Fast validation of the true five-year acceptance harness.

The full five-year run is in the separate opt-in workflow, never misrepresented
by this 60-second smoke.
"""
from __future__ import annotations

import json

from scripts.m6_five_year_acceptance import accept, initial_world
from threebody.screening.s0 import validate_s0


def test_m6_acceptance_fixture_has_valid_four_body_initial_state():
    world = initial_world()
    assert not validate_s0(world)
    assert [b.id for b in world.bodies] == [
        "star-1", "star-2", "star-3", "planet-1",
    ]
    assert all(b.mass_kg > 0 and b.radius_m > 0 for b in world.bodies)


def test_m6_actual_ias15_sixty_second_smoke_never_claims_five_years(tmp_path):
    result = accept(tmp_path, duration_s=60.0, interval_s=10.0)
    assert result["duration_s"] == 60.0
    assert result["verified_physical_samples"] == 7
    assert result["source"] == "REAL_REBOUND_IAS15_NOT_INTERPOLATED"
    assert result["thermal_recovery_verified"]
    assert result["normalized_residual_c0"] < 1e-4
    assert result["normalized_residual_c1"] < 1e-4
    report = json.loads(
        (tmp_path / "m6_five_year_acceptance.json").read_text()
    )
    assert report["duration_s"] == 60.0
