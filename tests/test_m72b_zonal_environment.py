from __future__ import annotations

import math

import numpy as np
import pytest
from pydantic import ValidationError

from threebody.errors import ConfigError
from threebody.physics.environment import (
    EnvironmentPointQuery,
    ZonalDynamicEnvironmentConfig,
    query_zonal_environment,
    sample_planet_material_profile,
    solve_zonal_environment,
)
from threebody.physics.environment.forcing import ConstantForcing
from threebody.physics.environment.models import (
    EnvironmentIntegratorConfig,
    OptionalOceanReservoir,
    ThermalReservoir,
)
from threebody.physics.environment.zonal_solver import surface_phase_diagnostic
from threebody.physics.thermal.diagnostics import STEFAN_BOLTZMANN_W_M2_K4


def zonal_config(
    *, material_seed: int = 42, conductance_w_k: float = 2.0e14,
    absorbed_flux_weights: tuple[float, ...] | None = None,
    initial_temperature_k: float = 285.0, max_step_s: float = 1800.0,
    ocean_mass_kg: float = 1.4e21,
) -> ZonalDynamicEnvironmentConfig:
    return ZonalDynamicEnvironmentConfig(
        latitude_band_count=12,
        planet_radius_m=6.371e6,
        emissivity=0.94,
        internal_heat_flux_w_m2=0.08,
        reference_surface_pressure_pa=101325.0,
        surface=ThermalReservoir(
            mass_kg=2.0e19, specific_heat_j_kg_k=900.0,
            initial_temperature_k=initial_temperature_k, material="regolith",
        ),
        shallow_rock=ThermalReservoir(
            mass_kg=1.0e21, specific_heat_j_kg_k=850.0,
            initial_temperature_k=initial_temperature_k, material="silicate_rock",
        ),
        ocean_mixed_layer=OptionalOceanReservoir(
            mass_kg=ocean_mass_kg, specific_heat_j_kg_k=4184.0,
            initial_temperature_k=(initial_temperature_k if ocean_mass_kg else None),
        ),
        surface_rock_exchange_w_m2_k=0.8,
        surface_ocean_exchange_w_m2_k=(8.0 if ocean_mass_kg else 0.0),
        meridional_interface_conductance_w_k=conductance_w_k,
        absorbed_flux_weights=absorbed_flux_weights,
        material_profile=sample_planet_material_profile(material_seed),
        integrator=EnvironmentIntegratorConfig(
            method="DOP853", rtol=1.0e-10, atol_k=1.0e-10,
            max_step_s=max_step_s,
        ),
    )


def test_material_prior_is_reproducible_independent_and_normalized():
    first = sample_planet_material_profile(20261119)
    assert first == sample_planet_material_profile(20261119)
    assert first != sample_planet_material_profile(20261120)
    assert sum(component.mass_fraction for component in first.components) == pytest.approx(1.0)
    assert len({component.material_id for component in first.components}) == 5
    assert first.semantics == "RANDOM_PRIOR_NOT_COMPOSITION_OBSERVATION"
    with pytest.raises(ValueError, match="uint32"):
        sample_planet_material_profile(-1)


def test_equal_area_uniform_equilibrium_and_energy_conservation():
    weights = (1.0,) * 12
    config = zonal_config(absorbed_flux_weights=weights)
    absorbed = (
        config.emissivity * STEFAN_BOLTZMANN_W_M2_K4 * 285.0**4
        - config.internal_heat_flux_w_m2
    )
    result = solve_zonal_environment(
        config, ConstantForcing(absorbed), np.linspace(0.0, 20 * 86400.0, 25)
    )
    assert result.schema_version == "0.9.0"
    assert len(result.snapshots[-1].bands) == 12
    assert sum(band.area_fraction for band in result.snapshots[0].bands) == pytest.approx(1.0)
    assert max(
        abs(band.surface_temperature_k - 285.0)
        for band in result.snapshots[-1].bands
    ) < 1.0e-9
    assert result.diagnostics.maximum_scaled_energy_residual < 1.0e-13
    assert result.diagnostics.maximum_transport_cancellation_w < 1.0e-8


def test_default_insolation_is_normalized_and_transport_reduces_contrast():
    times = np.linspace(0.0, 80 * 86400.0, 33)
    isolated = solve_zonal_environment(
        zonal_config(conductance_w_k=0.0), ConstantForcing(500.0), times
    )
    coupled = solve_zonal_environment(
        zonal_config(conductance_w_k=8.0e14), ConstantForcing(500.0), times
    )
    first = coupled.snapshots[0]
    assert sum(band.absorbed_stellar_w_m2 * band.area_fraction for band in first.bands) == (
        pytest.approx(500.0)
    )
    isolated_temperatures = [
        band.surface_temperature_k for band in isolated.snapshots[-1].bands
    ]
    coupled_temperatures = [
        band.surface_temperature_k for band in coupled.snapshots[-1].bands
    ]
    assert np.ptp(coupled_temperatures) < np.ptp(isolated_temperatures)
    assert coupled.diagnostics.maximum_scaled_energy_residual < 1.0e-9
    assert coupled.diagnostics.maximum_transport_cancellation_w < 1.0


def test_timestep_convergence_and_invalid_flux_weights():
    times = np.linspace(0.0, 10 * 86400.0, 21)
    coarse = solve_zonal_environment(
        zonal_config(max_step_s=3600.0), ConstantForcing(420.0), times
    )
    fine = solve_zonal_environment(
        zonal_config(max_step_s=450.0), ConstantForcing(420.0), times
    )
    difference = max(
        abs(a.surface_temperature_k - b.surface_temperature_k)
        for a, b in zip(
            coarse.snapshots[-1].bands, fine.snapshots[-1].bands, strict=True
        )
    )
    assert difference < 2.0e-5
    with pytest.raises(ValidationError, match="average to one"):
        zonal_config(absorbed_flux_weights=(0.5,) * 12)


def test_pressure_adjusted_probabilistic_partial_melting_is_explicit():
    config = zonal_config()
    cold = surface_phase_diagnostic(config, 200.0)
    warm = surface_phase_diagnostic(config, 1500.0)
    hot = surface_phase_diagnostic(config, 2300.0)
    assert cold.expected_liquid_fraction == 0.0
    assert cold.expected_magma_fraction == 0.0
    assert 0.0 < warm.expected_liquid_fraction < hot.expected_liquid_fraction
    assert 0.0 < warm.expected_magma_fraction < hot.expected_magma_fraction
    assert warm.classification == "PARTIALLY_LIQUID"
    assert hot.expected_liquid_fraction > 0.95
    assert hot.classification == "LIQUID_DOMINANT"
    basalt = next(
        component for component in warm.components
        if component.material_id == "basaltic_silicate"
    )
    high_pressure = surface_phase_diagnostic(
        config.model_copy(update={"reference_surface_pressure_pa": 1.0e9}), 1500.0
    )
    high_pressure_basalt = next(
        component for component in high_pressure.components
        if component.material_id == "basaltic_silicate"
    )
    assert high_pressure_basalt.pressure_adjusted_solidus_k > (
        basalt.pressure_adjusted_solidus_k
    )
    assert "PRESSURE_EXCEEDS_LINEAR_MELTING_PROXY_VALIDITY" in (
        high_pressure.uncertainty_flags
    )
    assert "NO_LATENT_HEAT_FEEDBACK" in warm.semantics


def test_stable_point_query_marks_uncomputed_weather_as_uncertain():
    config = zonal_config()
    result = solve_zonal_environment(
        config, ConstantForcing(450.0), [0.0, 86400.0, 2 * 86400.0]
    )
    exact = query_zonal_environment(
        config, result, EnvironmentPointQuery(
            t_s=86400.0, latitude_deg=20.0, longitude_deg=75.0
        )
    )
    interpolated = query_zonal_environment(
        config, result, EnvironmentPointQuery(
            t_s=43200.0, latitude_deg=-20.0, longitude_deg=-75.0
        )
    )
    assert exact.time_sampling == "EXACT"
    assert interpolated.time_sampling == "LINEAR_STATE_INTERPOLATION"
    assert interpolated.source_bracket_s == (0.0, 86400.0)
    assert exact.surface_pressure_pa == config.reference_surface_pressure_pa
    assert exact.expected_surface_liquid_fraction >= exact.expected_magma_fraction
    assert exact.air_temperature_k is None
    assert exact.relative_humidity is None
    assert exact.precipitation_rate_kg_m2_s is None
    assert exact.weather_events == ()
    assert any("NOT_COMPUTED" in flag for flag in exact.uncertainty_flags)
    with pytest.raises(ConfigError, match="outside result coverage"):
        query_zonal_environment(
            config, result, EnvironmentPointQuery(
                t_s=3 * 86400.0, latitude_deg=0.0
            )
        )


def test_hash_separates_material_seed_and_result_is_finite():
    first_config = zonal_config(material_seed=1)
    second_config = zonal_config(material_seed=2)
    assert first_config.config_sha256 != second_config.config_sha256
    result = solve_zonal_environment(
        first_config, ConstantForcing(350.0), [0.0, 3600.0]
    )
    assert result.material_profile_sha256 == first_config.material_profile.profile_sha256
    assert math.isfinite(result.diagnostics.maximum_temperature_k)


def test_zero_ocean_zonal_state_and_query_are_explicit():
    config = zonal_config(ocean_mass_kg=0.0)
    result = solve_zonal_environment(config, ConstantForcing(300.0), [0.0, 3600.0])
    assert all(
        band.ocean_mixed_layer_temperature_k is None
        and band.ocean_mixed_layer_heat_capacity_j_k == 0.0
        for band in result.snapshots[-1].bands
    )
    point = query_zonal_environment(
        config, result, EnvironmentPointQuery(t_s=1800.0, latitude_deg=0.0)
    )
    assert point.ocean_mixed_layer_temperature_k is None
