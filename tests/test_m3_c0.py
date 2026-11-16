from __future__ import annotations

import numpy as np
import pytest

from threebody.physics.thermal.c0 import equilibrium_temperature_k, solve_c0
from threebody.physics.thermal.diagnostics import STEFAN_BOLTZMANN_W_M2_K4
from threebody.physics.thermal.models import ThermalIntegratorSettings


def _integrator(max_step_s=10_000.0):
    return ThermalIntegratorSettings(rtol=1.0e-9, atol_k=1.0e-8, max_step_s=max_step_s)


def _solve(
    times,
    temperature,
    flux,
    *,
    capacity=4.0e7,
    albedo=0.3,
    emissivity=1.0,
    internal=0.0,
    max_step=10_000.0,
):
    return solve_c0(
        times_s=times,
        initial_temperature_k=temperature,
        planet_center_flux=lambda _time: flux,
        bond_albedo=albedo,
        effective_emissivity=emissivity,
        areal_heat_capacity_j_m2_k=capacity,
        internal_heat_flux_w_m2=internal,
        integrator=_integrator(max_step),
    )


def test_c0_earthlike_analytic_equilibrium_is_about_255_k_and_stationary():
    equilibrium = equilibrium_temperature_k(1361.0, 0.3, 1.0)
    assert equilibrium == pytest.approx(254.58, abs=0.1)
    result = _solve([0.0, 1.0e6], equilibrium, 1361.0)
    assert result.samples[-1].temperature_k == pytest.approx(equilibrium, abs=1.0e-8)
    assert result.energy_budget.normalized_residual < 1.0e-10


def test_c0_zero_flux_cooling_matches_analytic_solution_and_conserves_energy():
    initial = 300.0
    capacity = 1.0e7
    duration = 2.0e6
    result = _solve(
        [0.0, duration], initial, 0.0, capacity=capacity, albedo=0.0, max_step=1000.0
    )
    expected = (
        initial**-3 + 3.0 * STEFAN_BOLTZMANN_W_M2_K4 * duration / capacity
    ) ** (-1.0 / 3.0)
    assert result.samples[-1].temperature_k == pytest.approx(expected, rel=2.0e-8)
    assert result.energy_budget.normalized_residual < 1.0e-9


def test_c0_heat_capacity_controls_transient_and_step_size_converges():
    equilibrium = equilibrium_temperature_k(1361.0, 0.3, 1.0)
    times = [0.0, 200_000.0]
    low_capacity = _solve(times, 200.0, 1361.0, capacity=1.0e6, max_step=5000.0)
    high_capacity = _solve(times, 200.0, 1361.0, capacity=1.0e8, max_step=5000.0)
    assert abs(low_capacity.samples[-1].temperature_k - equilibrium) < abs(
        high_capacity.samples[-1].temperature_k - equilibrium
    )
    coarse = _solve(times, 220.0, 1361.0, max_step=5000.0)
    fine = _solve(times, 220.0, 1361.0, max_step=500.0)
    assert coarse.samples[-1].temperature_k == pytest.approx(
        fine.samples[-1].temperature_k, rel=2.0e-9
    )
    assert np.isfinite(fine.energy_budget.residual_j_m2)
