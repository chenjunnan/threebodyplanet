from __future__ import annotations

import math
import runpy
import shutil
from pathlib import Path

import pytest
from test_m5b_readonly_web import _request, _server

from threebody.cli import build_parser, execute
from threebody.errors import ConfigError
from threebody.physics.environment.integrated_archive import (
    IntegratedEnvironmentArchiveReader,
    run_integrated_environment_archive,
)
from threebody.physics.environment.integrated_models import (
    AtmosphereInventoryConfig,
    IntegratedEnvironmentConfig,
    atmosphere_inventory_from_world_prior,
)
from threebody.physics.environment.integrated_solver import (
    IntegratedEnvironmentEngine,
    simulate_integrated_environment,
)
from threebody.physics.environment.spatial_forcing import M5ASpatialForcing
from threebody.physics.environment.zonal_models import (
    MaterialFraction,
    PlanetMaterialProfile,
    sample_planet_material_profile,
)
from threebody.physics.longrun.runner import start_long_run
from threebody.web.integrated_environment_science import (
    query_integrated_environment_point,
    query_integrated_environment_series,
    query_integrated_environment_tile,
)
from threebody.web.worldsets import simulation_settings


def atmosphere(*, column_mass: float = 10330.0, water: float = 0.0003):
    fractions = {
        "nitrogen": 0.7803 - water,
        "oxygen": 0.21,
        "carbon_dioxide": 0.0004,
        "nitrogen_dioxide": 0.0,
        "methane": 0.0,
        "sulfur_dioxide": 0.0,
        "water_vapor": water,
        "hydrogen": 0.0,
        "helium": 0.0,
        "ammonia": 0.0,
        "argon": 0.0093,
    }
    return AtmosphereInventoryConfig(
        column_mass_kg_m2=column_mass, mole_fractions=fractions
    )


def integrated_config(*, dt: float = 1800.0, atmosphere_config=None,
                      material_profile=None, initial_temperature: float = 288.0):
    return IntegratedEnvironmentConfig(
        fidelity_mode="CUSTOM", world_seed=20261119,
        latitude_count=4, longitude_count=8,
        planet_radius_m=6.371e6, surface_gravity_m_s2=9.81,
        rotation_axis=(0.0, 0.0, 1.0),
        rotation_rate_rad_s=2.0 * math.pi / 86400.0,
        bond_albedo=0.3, emissivity=0.95, internal_heat_flux_w_m2=0.08,
        surface_material_column_mass_kg_m2=2.0e5,
        shallow_rock_column_mass_kg_m2=1.0e7,
        shallow_rock_specific_heat_j_kg_k=850.0,
        ocean_mixed_layer_column_mass_kg_m2=100.0,
        initial_surface_temperature_k=initial_temperature,
        initial_rock_temperature_k=initial_temperature,
        initial_ocean_temperature_k=initial_temperature,
        initial_air_temperature_k=initial_temperature,
        initial_surface_liquid_water_kg_m2=20.0,
        initial_surface_ice_kg_m2=10.0,
        material_profile=(material_profile or sample_planet_material_profile(42)),
        atmosphere=(atmosphere_config or atmosphere()),
        max_internal_step_s=dt, output_interval_s=21600.0,
        max_orbit_sample_interval_s=21600.0,
    )


@pytest.fixture(scope="module")
def integrated_orbit(tmp_path_factory):
    root = tmp_path_factory.mktemp("integrated-orbit")
    initial = runpy.run_path(
        str(Path(__file__).parents[1] / "scripts/m6_five_year_acceptance.py")
    )["initial_world"]()
    run = root / "integrated-source"
    start_long_run(
        initial, simulation_settings(8 * 86400.0, 21600.0), run,
        run_id="integrated-source",
    )
    return run


def test_spatial_three_star_forcing_has_real_day_night(integrated_orbit):
    config = integrated_config()
    forcing = M5ASpatialForcing(integrated_orbit, config)
    engine = IntegratedEnvironmentEngine(config, forcing)
    first, flags = engine.incident_stellar_flux(0.0)
    later, _ = engine.incident_stellar_flux(21600.0)
    assert forcing.kind == "M5A_THREE_STAR_CARTESIAN_LINEAR_V1"
    assert first.min() >= 0.0 and first.max() > 100.0
    assert first.min() < first.max() * 0.01
    assert later != pytest.approx(first)
    assert flags == ()


def test_integrated_heat_atmosphere_water_weather_and_budgets(integrated_orbit):
    config = integrated_config()
    forcing = M5ASpatialForcing(integrated_orbit, config)
    result = simulate_integrated_environment(
        config, forcing, [0.0, 21600.0, 43200.0, 86400.0, 2 * 86400.0]
    )
    assert result.model_id == "integrated_environment_v1"
    assert result.diagnostics.maximum_water_mass_relative_residual < 1.0e-10
    assert result.diagnostics.maximum_dry_atmosphere_relative_residual < 1.0e-10
    assert result.diagnostics.maximum_material_mass_relative_residual == 0.0
    assert result.diagnostics.maximum_scaled_energy_residual < 5.0e-4
    cells = result.snapshots[-1].cells
    assert len(cells) == 32
    assert max(cell.surface_temperature_k for cell in cells) > min(
        cell.surface_temperature_k for cell in cells
    )
    assert all(cell.surface_pressure_pa > 0.0 for cell in cells)
    assert all(cell.air_temperature_k is not None for cell in cells)
    assert all(cell.eastward_wind_m_s is not None for cell in cells)
    assert all(sum(cell.gas_mole_fractions.values()) == pytest.approx(1.0) for cell in cells)
    assert all(cell.quality["wind"] in {"PARAMETERIZED", "UNCERTAIN"} for cell in cells)


def test_no_atmosphere_no_water_never_creates_weather(integrated_orbit):
    profile = PlanetMaterialProfile(
        seed=1,
        components=(
            MaterialFraction(material_id="basaltic_silicate", mass_fraction=0.7),
            MaterialFraction(material_id="granitic_silicate", mass_fraction=0.1),
            MaterialFraction(material_id="iron_nickel", mass_fraction=0.15),
            MaterialFraction(material_id="sulfur_compounds", mass_fraction=0.05),
            MaterialFraction(material_id="water_ice", mass_fraction=0.0),
        ),
    )
    config = integrated_config(
        atmosphere_config=atmosphere(column_mass=0.0, water=0.0),
        material_profile=profile,
    ).model_copy(update={
        "ocean_mixed_layer_column_mass_kg_m2": 0.0,
        "initial_ocean_temperature_k": None,
        "surface_ocean_exchange_w_m2_k": 0.0,
        "initial_surface_liquid_water_kg_m2": 0.0,
        "initial_surface_ice_kg_m2": 0.0,
    })
    forcing = M5ASpatialForcing(integrated_orbit, config)
    result = simulate_integrated_environment(config, forcing, [0.0, 21600.0])
    for cell in result.snapshots[-1].cells:
        assert cell.surface_pressure_pa == 0.0
        assert cell.air_temperature_k is None
        assert cell.precipitation_rate_kg_m2_s == 0.0
        assert cell.weather_state == "CLEAR"
        assert cell.quality["humidity"] == "UNAVAILABLE"
    assert result.snapshots[-1].total_water_kg == 0.0


@pytest.mark.parametrize("column_mass", [5.0e-4, 2.0e6])
def test_thin_and_high_pressure_atmospheres_are_finite_but_uncertain(
    integrated_orbit, column_mass,
):
    dry_profile = PlanetMaterialProfile(
        seed=2,
        components=(
            MaterialFraction(material_id="basaltic_silicate", mass_fraction=0.7),
            MaterialFraction(material_id="granitic_silicate", mass_fraction=0.1),
            MaterialFraction(material_id="iron_nickel", mass_fraction=0.15),
            MaterialFraction(material_id="sulfur_compounds", mass_fraction=0.05),
            MaterialFraction(material_id="water_ice", mass_fraction=0.0),
        ),
    )
    config = integrated_config(
        atmosphere_config=atmosphere(column_mass=column_mass, water=0.0),
        material_profile=dry_profile,
    ).model_copy(update={
        "ocean_mixed_layer_column_mass_kg_m2": 0.0,
        "initial_ocean_temperature_k": None,
        "surface_ocean_exchange_w_m2_k": 0.0,
        "initial_surface_liquid_water_kg_m2": 0.0,
        "initial_surface_ice_kg_m2": 0.0,
    })
    forcing = M5ASpatialForcing(integrated_orbit, config)
    engine = IntegratedEnvironmentEngine(config, forcing)
    cells = engine.snapshot(engine.initialize()).cells
    assert all(cell.surface_pressure_pa > 0.0 for cell in cells)
    assert all(cell.air_density_kg_m3 is not None for cell in cells)
    assert all(cell.quality["surface_pressure_pa"] == "UNCERTAIN" for cell in cells)
    assert all("UNCERTAIN_IDEAL_GAS_RANGE" in cell.uncertainty_flags for cell in cells)


def test_extreme_cold_retains_nonnegative_ice_inventory(integrated_orbit):
    config = integrated_config(initial_temperature=120.0)
    forcing = M5ASpatialForcing(integrated_orbit, config)
    result = simulate_integrated_environment(config, forcing, [0.0, 21600.0])
    assert all(cell.water_phase_regime == "ICE_DOMINANT"
               for cell in result.snapshots[0].cells)
    assert all(cell.ice_kg_m2 >= 0.0 and cell.surface_temperature_k > 0.0
               for snapshot in result.snapshots for cell in snapshot.cells)


def test_extreme_heat_distinguishes_magma_from_other_liquids(integrated_orbit):
    config = integrated_config(initial_temperature=1600.0)
    forcing = M5ASpatialForcing(integrated_orbit, config)
    result = simulate_integrated_environment(config, forcing, [0.0, 3600.0])
    cells = result.snapshots[0].cells
    assert all(cell.magma_fraction > 0.0 for cell in cells)
    assert all(cell.surface_material_liquid_fraction >= cell.magma_fraction for cell in cells)


@pytest.mark.parametrize(
    ("temperature", "expected_regime"),
    [(250.0, "ICE_DOMINANT"), (300.0, "LIQUID_DOMINANT"),
     (673.0, "VAPOR_DOMINANT")],
)
def test_water_initial_phase_uses_temperature_and_column_pressure(
    integrated_orbit, temperature, expected_regime,
):
    config = integrated_config(initial_temperature=temperature)
    forcing = M5ASpatialForcing(integrated_orbit, config)
    snapshot = IntegratedEnvironmentEngine(config, forcing).snapshot(
        IntegratedEnvironmentEngine(config, forcing).initialize()
    )
    assert {cell.water_phase_regime for cell in snapshot.cells} == {expected_regime}
    if temperature >= 673.0:
        assert all(cell.liquid_water_kg_m2 == 0.0 for cell in snapshot.cells)
        assert all(cell.ice_kg_m2 == 0.0 for cell in snapshot.cells)


def test_supercritical_water_is_explicitly_uncertain_not_magma(integrated_orbit):
    fractions = {name: 0.0 for name in atmosphere().mole_fractions}
    fractions["water_vapor"] = 1.0
    config = integrated_config(
        initial_temperature=700.0,
        atmosphere_config=AtmosphereInventoryConfig(
            column_mass_kg_m2=3.0e6, mole_fractions=fractions
        ),
    )
    forcing = M5ASpatialForcing(integrated_orbit, config)
    engine = IntegratedEnvironmentEngine(config, forcing)
    snapshot = engine.snapshot(engine.initialize())
    assert all(
        cell.water_phase_regime == "SUPERCRITICAL_UNCERTAIN"
        for cell in snapshot.cells
    )
    assert all(cell.supercritical_water_kg_m2 > 0.0 for cell in snapshot.cells)
    assert all("SUPERCRITICAL_WATER_UNCERTAIN" in cell.uncertainty_flags
               for cell in snapshot.cells)


def test_pressure_matches_gravity_times_atmospheric_column(integrated_orbit):
    config = integrated_config()
    forcing = M5ASpatialForcing(integrated_orbit, config)
    engine = IntegratedEnvironmentEngine(config, forcing)
    snapshot = engine.snapshot(engine.initialize())
    dry = sum(
        mass for name, mass in config.atmosphere.initial_species_column_mass().items()
        if name != "water_vapor"
    )
    for cell in snapshot.cells:
        expected = config.surface_gravity_m_s2 * (
            dry + cell.water_vapor_kg_m2 + cell.cloud_water_kg_m2
            + cell.supercritical_water_kg_m2
        )
        assert cell.surface_pressure_pa == pytest.approx(expected, rel=1.0e-12)
        assert sum(cell.gas_partial_pressures_pa.values()) <= (
            cell.surface_pressure_pa * (1.0 + 1.0e-12)
        )


def test_archived_atmosphere_prior_resolves_to_supported_column_inventory():
    initial = runpy.run_path(
        str(Path(__file__).parents[1] / "scripts/m6_five_year_acceptance.py")
    )["initial_world"]()
    planet = initial.bodies[-1]
    composition = {
        "nitrogen": 0.78, "oxygen": 0.21, "carbon_dioxide": 0.0004,
        "water_vapor": 0.0003, "argon": 0.0093,
    }
    hypothesis = {
        "status": "PRESENT_PRIOR_SAMPLE", "atmosphere_mass_kg": 5.15e18,
        "composition_mole_fraction": composition,
    }
    planet = planet.model_copy(update={
        "properties": {**planet.properties, "atmosphere_hypothesis": hypothesis}
    })
    state = initial.model_copy(update={"bodies": (*initial.bodies[:-1], planet)})
    inventory = atmosphere_inventory_from_world_prior(state)
    assert inventory.column_mass_kg_m2 == pytest.approx(
        5.15e18 / (4.0 * math.pi * planet.radius_m**2)
    )
    assert set(inventory.mole_fractions) == set(atmosphere().mole_fractions)
    assert inventory.mole_fractions["nitrogen_dioxide"] == 0.0


def test_timestep_and_grid_convergence_meet_frozen_thresholds(integrated_orbit):
    output = [float(day * 86400) for day in range(8)]
    coarse_config = integrated_config(dt=900.0).model_copy(update={
        "fidelity_mode": "GAME_DEFAULT",
        "latitude_count": 12, "longitude_count": 24,
    })
    fine_time_config = coarse_config.model_copy(update={
        "fidelity_mode": "CUSTOM", "max_internal_step_s": 450.0,
    })
    coarse = simulate_integrated_environment(
        coarse_config, M5ASpatialForcing(integrated_orbit, coarse_config), output
    )
    fine_time = simulate_integrated_environment(
        fine_time_config, M5ASpatialForcing(integrated_orbit, fine_time_config), output
    )
    coarse_cells = coarse.snapshots[-1].cells
    fine_time_cells = fine_time.snapshots[-1].cells
    assert max(abs(a.surface_temperature_k - b.surface_temperature_k)
               for a, b in zip(coarse_cells, fine_time_cells, strict=True)) < 2.0
    assert abs(
        coarse.snapshots[-1].total_water_kg - fine_time.snapshots[-1].total_water_kg
    ) / coarse.snapshots[-1].total_water_kg < 0.005

    fine_grid_config = coarse_config.model_copy(update={
        "fidelity_mode": "CONVERGENCE_FINE",
        "latitude_count": 24, "longitude_count": 48,
        "max_internal_step_s": 450.0,
    })
    fine_grid = simulate_integrated_environment(
        fine_grid_config, M5ASpatialForcing(integrated_orbit, fine_grid_config), output
    )
    mean_coarse = sum(cell.surface_temperature_k for cell in coarse_cells) / len(
        coarse_cells
    )
    mean_fine = sum(
        cell.surface_temperature_k for cell in fine_grid.snapshots[-1].cells
    ) / len(fine_grid.snapshots[-1].cells)
    assert abs(mean_coarse - mean_fine) < 3.0


def test_weather_state_is_continuous_and_event_entry_is_persistent(integrated_orbit):
    config = integrated_config(initial_temperature=340.0).model_copy(update={
        "output_interval_s": 3600.0,
    })
    forcing = M5ASpatialForcing(integrated_orbit, config)
    times = [float(value) for value in range(0, 86401, 3600)]
    result = simulate_integrated_environment(config, forcing, times)
    for previous, current in zip(result.snapshots, result.snapshots[1:], strict=False):
        assert max(abs(a.surface_temperature_k - b.surface_temperature_k)
                   for a, b in zip(previous.cells, current.cells, strict=True)) < 10.0
        assert max(abs((a.eastward_wind_m_s or 0.0) - (b.eastward_wind_m_s or 0.0))
                   for a, b in zip(previous.cells, current.cells, strict=True)) < 100.0
    heatwaves = [event for event in result.events if event.event_type == "HEATWAVE"]
    assert heatwaves
    assert len({event.cell_id for event in heatwaves}) == len(heatwaves)


def test_immutable_archive_resume_query_and_tamper_rejection(
    integrated_orbit, tmp_path,
):
    config = integrated_config().model_copy(update={"segment_output_count": 2})
    archive = tmp_path / "integrated"
    paused = run_integrated_environment_archive(
        integrated_orbit, archive, config, end_t_s=2 * 86400.0, max_segments=1
    )
    assert paused["status"] == "PAUSED"
    completed = run_integrated_environment_archive(
        integrated_orbit, archive, config, end_t_s=2 * 86400.0
    )
    assert completed["status"] == "COMPLETED"
    assert completed["result_sha256"]
    reader = IntegratedEnvironmentArchiveReader(archive, integrated_orbit)
    snapshots = list(reader.iter_snapshots())
    assert [snapshot.time_index for snapshot in snapshots] == list(range(len(snapshots)))
    continuous_archive = tmp_path / "continuous"
    continuous = run_integrated_environment_archive(
        integrated_orbit, continuous_archive, config, end_t_s=2 * 86400.0
    )
    continuous_snapshots = list(IntegratedEnvironmentArchiveReader(
        continuous_archive, integrated_orbit
    ).iter_snapshots())
    assert completed["result_sha256"] == continuous["result_sha256"]
    assert snapshots == continuous_snapshots
    assert completed["diagnostics"] == continuous["diagnostics"]
    point = query_integrated_environment_point(
        archive, integrated_orbit, t_s=10800.0,
        latitude_deg=10.0, longitude_deg=20.0,
    )
    assert point["interpolation"]["method"] == "LINEAR_STATE"
    assert point["result_sha256"] == completed["result_sha256"]
    assert point["fields"]["surface_pressure"]["unit"] == "Pa"
    series = query_integrated_environment_series(
        archive, integrated_orbit, latitude_deg=10.0, longitude_deg=20.0,
        max_points=3,
    )
    assert series["returned_count"] == 3
    assert series["rows"][0]["result_sha256"] == point["result_sha256"]
    tile = query_integrated_environment_tile(
        archive, integrated_orbit, time_index=1, level=1
    )
    assert tile["source_grid"] == [4, 8]
    assert tile["grid"] == [2, 4]
    assert tile["result_sha256"] == point["result_sha256"]
    snapshots_path = archive / "segments" / "00000000" / "snapshots.jsonl"
    snapshots_path.write_text(
        snapshots_path.read_text(encoding="utf-8") + "{}\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="file hash mismatch"):
        IntegratedEnvironmentArchiveReader(archive, integrated_orbit)


def test_public_environment_http_apis_share_one_committed_archive(
    integrated_orbit, tmp_path,
):
    run_id = "integrated-source"
    source = tmp_path / run_id
    shutil.copytree(integrated_orbit, source)
    environment = tmp_path / "integrated_environments" / run_id
    manifest = run_integrated_environment_archive(
        source, environment, integrated_config(), end_t_s=86400.0
    )
    with _server(tmp_path) as base:
        status, _, point = _request(
            f"{base}/api/v1/environment/{run_id}/point"
            "?t_s=10800&latitude_deg=10&longitude_deg=20"
        )
        assert status == 200
        status, _, series = _request(
            f"{base}/api/v1/environment/{run_id}/series"
            "?latitude_deg=10&longitude_deg=20&max_points=3"
        )
        assert status == 200
        status, _, tile = _request(
            f"{base}/api/v1/environment/{run_id}/tiles/1/1"
        )
        assert status == 200
        assert {
            point["result_sha256"], series["result_sha256"], tile["result_sha256"]
        } == {manifest["result_sha256"]}
        assert point["fields"]["surface_temperature"]["unit"] == "K"
        assert tile["aggregation"] == (
            "EQUAL_AREA_ARITHMETIC_MEAN_NO_PHYSICS_RECOMPUTE"
        )
        status, _, invalid = _request(
            f"{base}/api/v1/environment/{run_id}/point"
            "?t_s=nan&latitude_deg=0&longitude_deg=0"
        )
        assert status == 422
        assert "finite" in invalid["message"]
        status, _, invalid = _request(
            f"{base}/api/v1/environment/{run_id}/series"
            "?latitude_deg=0&latitude_deg=1&longitude_deg=0"
        )
        assert status == 422
        assert "duplicate" in invalid["message"]


def test_environment_long_cli_creates_real_source_verified_archive(
    integrated_orbit, tmp_path, capsys,
):
    output = tmp_path / "cli-environment"
    args = build_parser().parse_args([
        "world", "environment-long", "--run", str(integrated_orbit),
        "--config", "configs/m72_m73_five_year_reduced.yaml",
        "--out-dir", str(output), "--end-s", "86400",
    ])
    assert execute(args) == 0
    payload = capsys.readouterr().out
    assert '"status": "COMPLETED"' in payload
    reader = IntegratedEnvironmentArchiveReader(output, integrated_orbit)
    assert reader.manifest["fidelity_mode"] == "FIVE_YEAR_REDUCED"
    assert len(list(reader.iter_snapshots())) == 2


@pytest.mark.browser
def test_chromium_reads_archive_tile_point_and_physical_time(
    integrated_orbit, tmp_path,
):
    import os

    from test_m5b_browser import _live_server, sync_playwright

    run_id = "integrated-source"
    source = tmp_path / run_id
    shutil.copytree(integrated_orbit, source)
    environment = tmp_path / "integrated_environments" / run_id
    run_integrated_environment_archive(
        source, environment, integrated_config(), end_t_s=86400.0
    )
    with _live_server(tmp_path) as base, sync_playwright() as api:
        browser = api.chromium.launch(
            executable_path=os.environ.get("THREEBODY_TEST_CHROMIUM"), headless=True
        )
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base, wait_until="domcontentloaded")
        page.evaluate("() => { location.hash = '#/environment'; }")
        page.locator("#environment-run-id").fill(run_id)
        page.locator("#environment-load").click()
        page.wait_for_function(
            "() => document.querySelector('#integrated-environment-status')"
            ".textContent.includes('integrated_environment_v1')"
        )
        assert page.locator("#environment-time-label").inner_text().startswith("t=0")
        nonempty = page.locator("#integrated-environment-map").evaluate(
            "canvas => Array.from(canvas.getContext('2d').getImageData(0,0,"
            "canvas.width,canvas.height).data).some((value,index)=>index%4!==3&&value>0)"
        )
        assert nonempty
        page.locator("#integrated-environment-map").click(
            position={"x": 480, "y": 240}
        )
        page.wait_for_function(
            "() => document.querySelector('#integrated-environment-point')"
            ".textContent.includes('地表')"
        )
        assert "质量" in page.locator("#integrated-environment-point").inner_text()
        page.locator("#environment-next").click()
        page.wait_for_function(
            "() => document.querySelector('#environment-time-label')"
            ".textContent.includes('索引 1')"
        )
        provenance = page.locator("#integrated-environment-provenance").inner_text()
        assert "forcing_source_sha256" in provenance
        assert not errors
        browser.close()
