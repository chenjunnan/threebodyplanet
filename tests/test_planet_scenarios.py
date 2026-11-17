"""Limiting cases and conservation/provenance tests for parameter experiments."""
from __future__ import annotations

import math
import runpy
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from threebody.physics.longrun.runner import start_long_run
from threebody.web.planet_scenarios import (
    SIGMA,
    PlanetScenario,
    build_scenario,
    daily_mean_flux,
    equilibrium_temperature,
    terrain_height,
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
    area = 4*math.pi*first['radius_m']**2/len(first['regions'])
    volume = sum(r['water_depth_m'] for r in first['regions'])*area
    assert volume == pytest.approx(params.surface_water_kg/1000, rel=1e-10)
    assert all(-90 <= s['altitude_deg'] <= 90 for s in first['sky'])
    assert all(0 <= s['azimuth_deg'] < 360 for s in first['sky'])
    assert len(first['sky']) == 3
    doubled = build_scenario(planet_archive, params.model_copy(update={
        'atmosphere_mass_kg': params.atmosphere_mass_kg*2}))
    assert doubled['pressure_pa'] == pytest.approx(2*first['pressure_pa'])
    vacuum = build_scenario(planet_archive, params.model_copy(update={
        'atmosphere_mass_kg': 0., 'surface_water_kg': 0.}))
    assert vacuum['scale_height_m'] is None
    assert all(r['geostrophic_scale_mps'] is None for r in vacuum['regions'])
    assert all(r['water_depth_m'] == 0 for r in vacuum['regions'])
    with pytest.raises(ValueError, match='exact committed'):
        build_scenario(planet_archive, params.model_copy(update={'t_s': 61.}))
    with pytest.raises(ValueError, match='rotation'):
        build_scenario(planet_archive, params.model_copy(update={'rotation_rate_rad_s': 1.}))


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
