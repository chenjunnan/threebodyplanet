from __future__ import annotations

import math

import numpy as np
import pytest

from threebody.physics.thermal.c1 import (
    equal_area_grid,
    meridional_diffusion_convergence,
    solve_c1,
)
from threebody.physics.thermal.models import ThermalIntegratorSettings


def _integrator(max_step=1000.0):
    return ThermalIntegratorSettings(rtol=1.0e-8, atol_k=1.0e-7, max_step_s=max_step)


def _equatorial_star_forcing(flux):
    def evaluate(_time, latitudes):
        x = np.sin(latitudes)
        dx = 2.0 / len(x)
        lower = x - 0.5 * dx
        upper = x + 0.5 * dx

        def antiderivative(value):
            return 0.5 * (
                value * np.sqrt(np.maximum(0.0, 1.0 - value**2))
                + np.arcsin(value)
            )

        return flux / math.pi * (antiderivative(upper) - antiderivative(lower)) / dx

    return evaluate


def _solve(
    *,
    bands=18,
    initial=250.0,
    duration=100_000.0,
    flux=1361.0,
    diffusivity=0.6,
    emissivity=1.0,
):
    return solve_c1(
        times_s=[0.0, duration],
        initial_temperature_k=initial,
        zonal_incoming=_equatorial_star_forcing(flux),
        latitude_bands=bands,
        bond_albedo=0.3,
        effective_emissivity=emissivity,
        areal_heat_capacity_j_m2_k=4.0e7,
        internal_heat_flux_w_m2=0.0,
        diffusivity_w_m2_k=diffusivity,
        integrator=_integrator(),
    )


def test_equal_area_grid_and_finite_volume_diffusion_conserve_global_energy():
    x, latitude, dx = equal_area_grid(36)
    assert dx == pytest.approx(2.0 / 36.0)
    assert np.sin(latitude) == pytest.approx(x)
    temperature = 250.0 + 30.0 * x + 10.0 * x**2
    convergence = meridional_diffusion_convergence(temperature, 0.6)
    assert float(np.mean(convergence)) == pytest.approx(0.0, abs=1.0e-13)


def test_pure_diffusion_reduces_variance_with_zero_polar_flux_and_conserves_mean():
    _, _, _ = equal_area_grid(18)
    initial = np.full(18, 250.0)
    initial[8:10] = 300.0
    result = solve_c1(
        times_s=[0.0, 1.0e6],
        initial_temperature_k=initial,
        zonal_incoming=lambda _time, latitude: np.zeros_like(latitude),
        latitude_bands=18,
        bond_albedo=0.0,
        effective_emissivity=0.0,
        areal_heat_capacity_j_m2_k=1.0e6,
        internal_heat_flux_w_m2=0.0,
        diffusivity_w_m2_k=1.0,
        integrator=_integrator(max_step=10_000.0),
    )
    final = np.asarray(result.samples[-1].temperature_k)
    assert float(np.mean(final)) == pytest.approx(float(np.mean(initial)), abs=1.0e-9)
    assert float(np.var(final)) < float(np.var(initial))
    assert abs(result.energy_budget.meridional_transport_energy_j_m2) < 1.0e-8
    assert abs(result.energy_budget.residual_j_m2) < 1.0e-6
    assert result.energy_budget.normalized_residual < 1.0e-9


def test_zero_diffusion_bands_are_independent():
    baseline = np.full(12, 250.0)
    perturbed = baseline.copy()
    perturbed[3] += 20.0
    first = _solve(bands=12, initial=baseline, diffusivity=0.0)
    second = _solve(bands=12, initial=perturbed, diffusivity=0.0)
    first_final = np.asarray(first.samples[-1].temperature_k)
    second_final = np.asarray(second.samples[-1].temperature_k)
    mask = np.arange(12) != 3
    assert second_final[mask] == pytest.approx(first_final[mask], abs=1.0e-10)
    assert second_final[3] != pytest.approx(first_final[3])


def test_c1_global_radiation_matches_quarter_law_energy_and_resolution_converges():
    coarse = _solve(bands=18)
    fine = _solve(bands=36)
    expected_incoming = 1361.0 / 4.0
    assert coarse.samples[0].globally_averaged_incoming_w_m2 == pytest.approx(
        expected_incoming, rel=2.0e-3
    )
    expected_absorbed_energy = 0.7 * coarse.samples[0].globally_averaged_incoming_w_m2 * 1.0e5
    assert coarse.energy_budget.absorbed_stellar_energy_j_m2 == pytest.approx(
        expected_absorbed_energy, rel=1.0e-8
    )
    assert coarse.energy_budget.normalized_residual < 1.0e-8
    assert coarse.samples[-1].global_mean_temperature_k == pytest.approx(
        fine.samples[-1].global_mean_temperature_k, rel=2.0e-5
    )
