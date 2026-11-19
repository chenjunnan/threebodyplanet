"""Limiting cases and conservation/provenance tests for parameter experiments."""
from __future__ import annotations

import math
import runpy
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError
from test_m72_dynamic_environment import config as environment_config

from threebody.physics.environment.forcing import M5AOrbitForcing
from threebody.physics.environment.solver import solve_environment
from threebody.physics.longrun.runner import start_long_run
from threebody.web.planet_scenarios import (
    SIGMA,
    PlanetScenario,
    _air_mass,
    _atmospheric_optics,
    build_scenario,
    daily_mean_flux,
    equilibrium_temperature,
    terrain_height,
    water_phase_proxy,
)
from threebody.web.worldsets import simulation_settings


def test_grey_equilibrium_and_core_energy_balance():
    scenario = PlanetScenario(t_s=0., conductivity_w_m_k=0.)
    absorbed = 1361*.7/4
    temperature = equilibrium_temperature(absorbed, scenario)
    assert temperature == pytest.approx((absorbed/SIGMA)**.25, rel=1e-12)
    core = scenario.model_copy(update={'conductivity_w_m_k': 3.})
    temperature_core = equilibrium_temperature(absorbed, core)
    assert temperature_core > temperature
    assert SIGMA*temperature_core**4 == pytest.approx(
        absorbed+3/core.mantle_thickness_m*(core.core_temperature_k-temperature_core), rel=1e-12)
    thick = scenario.model_copy(update={'optical_depth': 2.})
    assert equilibrium_temperature(absorbed, thick) > temperature


def test_diurnal_flux_global_energy_and_polar_limits():
    # Equal-area quadrature must reproduce S/4 for any declination.
    lat = np.arcsin(-1+(2*np.arange(20000)+1)/20000)
    for declination in (0., .4, math.pi/2):
        mean = np.mean([daily_mean_flux(float(p), 1361., declination) for p in lat])
        assert mean == pytest.approx(1361/4, rel=2e-6)
    assert daily_mean_flux(-math.pi/2, 1361., math.pi/2) == 0
    assert daily_mean_flux(math.pi/2, 1361., math.pi/2) == pytest.approx(1361.)


def test_terrain_seed_reproducibility_and_poles():
    normals = np.eye(3)
    a = terrain_height(normals, 42, 6000.)
    assert np.array_equal(a, terrain_height(normals, 42, 6000.))
    assert not np.array_equal(a, terrain_height(normals, 43, 6000.))
    assert np.max(np.abs(a)) <= 6000
    assert np.allclose(2*a, terrain_height(normals, 42, 12000.))


def test_three_band_atmosphere_and_air_mass_limits():
    composition = {"nitrogen": .78, "oxygen": .21, "carbon_dioxide": .0004,
                   "argon": .0093, "water_vapor": .0003}
    optics = _atmospheric_optics(101325., composition)
    assert optics["optical_depth_rgb"][2] > optics["optical_depth_rgb"][1] > (
        optics["optical_depth_rgb"][0]
    )
    assert _air_mass(math.pi/2) == pytest.approx(1., rel=5e-4)
    assert _air_mass(0.) > 30
    assert _air_mass(-.01) is None
    assert _atmospheric_optics(0., composition)["model"] == "vacuum"
    # Condensation is controlled by water partial pressure, not total dry-air
    # pressure or a fixed boiling-temperature threshold.
    assert water_phase_proxy(250.0, 1.0) == 'steam_atmosphere'
    assert water_phase_proxy(250.0, 100.0) == 'ice'
    assert water_phase_proxy(300.0, 1.0e3) == 'steam_atmosphere'
    assert water_phase_proxy(300.0, 1.0e4) == 'liquid_possible'
    assert water_phase_proxy(673.0, 1.0e6) == 'steam_atmosphere'
    assert water_phase_proxy(673.0, 30.0e6) == 'supercritical_water_envelope'
    with pytest.raises(ValueError):
        water_phase_proxy(300.0, -1.0)


def test_scenario_rejects_unphysical_or_unknown_inputs():
    for extra in ({'oxygen_mole_fraction': .8, 'water_vapor_mole_fraction': .3},
                  {'rotation_axis': (1., 1., 1.)}, {'albedo': float('nan')},
                  {'hazard_rates_per_year': {'unknown': .1}}, {'fake_oxygen': True}):
        with pytest.raises(ValidationError):
            PlanetScenario(t_s=0., **extra)


@pytest.fixture
def planet_archive(tmp_path):
    initial = runpy.run_path(str(Path(__file__).parents[1]/'scripts/m6_five_year_acceptance.py'))[
        'initial_world']()
    path = tmp_path/'planet-source'
    start_long_run(initial, simulation_settings(60., 10.), path, run_id='planet-source')
    return path


def test_verified_scenario_water_mass_pressure_sky_and_unknown_hazards(planet_archive):
    params = PlanetScenario(t_s=60., rotation_axis=(0., 0., 1.),
                            rotation_rate_rad_s=2*math.pi/86400,
                            hazard_rates_per_year={'r12-c24': .1},
                            hazard_definition='test event in region')
    first = build_scenario(planet_archive, params)
    second = build_scenario(planet_archive, params)
    assert first == second
    assert first['source']['t_s'] == 60
    assert len(first['source']['sample_sha256']) == 64
    assert first['pressure_pa'] == pytest.approx(101000., rel=.02)
    assert first['oxygen_partial_pressure_pa'] == 0
    region = next(r for r in first['regions'] if r['id'] == 'r12-c24')
    assert region['conditional_hazard_probability'] == pytest.approx(-math.expm1(-.1))
    assert first['regions'][0]['conditional_hazard_probability'] is None
    water = first['water_volume_diagnostic']
    assert water['method'] == 'equal_area_96x192_basin_fill'
    assert water['represented_volume_m3'] == pytest.approx(
        first['water_inventory']['remaining_surface_water_kg']/1000, rel=1e-10
    )
    assert abs(water['relative_residual']) < 1e-10
    assert all(-90 <= s['altitude_deg'] <= 90 for s in first['sky'])
    assert all(0 <= s['azimuth_deg'] < 360 for s in first['sky'])
    assert len(first['sky']) == 3
    assert first['schema_version'] == 'planet-exploration-v4'
    assert len(first['contour_levels_m']) == 10
    assert first['observer_location'] == {'latitude_deg': 0.0, 'longitude_deg': 0.0}
    assert first['visualization']['semantics'] == 'DISPLAY_ONLY_NOT_USED_BY_PHYSICS'
    assert first['visualization']['minimum_point_source_psf_px'] == 5
    inventory = first['water_inventory']
    assert inventory['remaining_surface_water_kg'] + (
        inventory['transferred_to_atmosphere_kg']
    ) == pytest.approx(params.surface_water_kg)
    assert first['atmospheric_optics']['optical_depth_rgb'][2] > (
        first['atmospheric_optics']['optical_depth_rgb'][0]
    )
    assert all(b['wind_speed_range_mps'] is not None for b in first['bands'])
    assert all(0 <= b['wind_speed_range_mps'][0] <= b['wind_speed_range_mps'][1]
               for b in first['bands'])
    doubled = build_scenario(planet_archive, params.model_copy(update={
        'atmosphere_mass_kg': params.atmosphere_mass_kg*2}))
    base_pressure = first['gravity_m_s2'] * params.atmosphere_mass_kg / (
        4*math.pi*first['radius_m']**2
    )
    assert doubled['pressure_pa'] - first['pressure_pa'] == pytest.approx(
        base_pressure, rel=1e-12
    )
    vacuum = build_scenario(planet_archive, params.model_copy(update={
        'atmosphere_mass_kg': 0., 'surface_water_kg': 0.}))
    assert vacuum['scale_height_m'] is None
    assert all(r['geostrophic_scale_mps'] is None for r in vacuum['regions'])
    assert all(r['wind_speed_range_mps'] is None for r in vacuum['regions'])
    assert all(r['water_depth_m'] == 0 for r in vacuum['regions'])
    assert vacuum['has_surface_water'] is False
    with pytest.raises(ValueError, match='exact committed'):
        build_scenario(planet_archive, params.model_copy(update={'t_s': 61.}))
    with pytest.raises(ValueError, match='rotation'):
        build_scenario(planet_archive, params.model_copy(update={'rotation_rate_rad_s': 1.}))


def test_supercritical_global_temperature_moves_surface_water_into_atmosphere(
    planet_archive,
):
    hot = build_scenario(planet_archive, PlanetScenario(
        t_s=60.0, rotation_axis=(0.0, 0.0, 1.0),
        rotation_rate_rad_s=2*math.pi/86400, optical_depth=20.0,
        emissivity=0.1,
    ))
    inventory = hot['water_inventory']
    assert inventory['global_equal_area_temperature_proxy_k'] > 647.096
    assert inventory['remaining_surface_water_kg'] == 0.0
    assert inventory['transferred_to_atmosphere_kg'] == 1.4e21
    assert inventory['state'] == 'SUPERCRITICAL_WATER_ENVELOPE'
    assert hot['has_surface_water'] is False
    assert hot['water_surface_visible'] is False
    assert all(region['water_depth_m'] == 0.0 for region in hot['regions'])
    assert hot['water_vapor_partial_pressure_pa'] > 22.064e6


def test_read_only_scenario_http_and_validation(planet_archive):
    from test_m5b_readonly_web import _request, _server

    raw = PlanetScenario(t_s=60., rotation_axis=(0., 0., 1.),
                         rotation_rate_rad_s=2*math.pi/86400).model_dump(mode='json')
    before = (planet_archive/'initial_state.json').read_bytes()
    with _server(planet_archive.parent) as base:
        url = base+'/api/v1/planet-scenarios'
        code, _, body = _request(url, method='POST', payload={
            'run_id': 'planet-source', 'parameters': raw})
        assert code == 200
        assert body['source']['t_s'] == 60
        assert body['data_kind'] == 'PARAMETERIZED_SCENARIO'
        code, _, _ = _request(url, method='POST', payload={
            'run_id': 'planet-source', 'parameters': {**raw, 'albedo': 2.}})
        assert code == 422
        code, _, _ = _request(url, method='POST', payload={
            'run_id': '../planet-source', 'parameters': raw})
        assert code == 422
    assert (planet_archive/'initial_state.json').read_bytes() == before


def test_m72_real_three_star_forcing_from_verified_m5a(planet_archive):
    cfg = environment_config(max_step_s=2.0)
    forcing = M5AOrbitForcing.from_run(
        planet_archive, bond_albedo=cfg.bond_albedo,
        max_sample_interval_s=cfg.max_orbit_sample_interval_s,
    )
    exact = forcing.sample(10.0)
    between = forcing.sample(15.0)
    assert exact.interpolation == 'EXACT_SAMPLE'
    assert between.interpolation == 'LINEAR_FLUX'
    assert between.top_of_atmosphere_flux_w_m2 == pytest.approx(
        (forcing.sample(10.0).top_of_atmosphere_flux_w_m2+
         forcing.sample(20.0).top_of_atmosphere_flux_w_m2)/2
    )
    result = solve_environment(cfg, forcing, [0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0])
    assert result.forcing_kind == 'M5A_THREE_STAR_LINEAR_FLUX_V1'
    assert result.diagnostics.maximum_scaled_energy_residual < 1e-9
