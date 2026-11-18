from __future__ import annotations

import math

import numpy as np
import pytest
from pydantic import ValidationError

from threebody.errors import ConfigError
from threebody.physics.environment.checkpoint import (
    load_environment_checkpoint,
    write_environment_checkpoint,
)
from threebody.physics.environment.forcing import ConstantForcing, StepForcing
from threebody.physics.environment.models import (
    DynamicEnvironmentConfig,
    EnvironmentIntegratorConfig,
    OptionalOceanReservoir,
    ThermalReservoir,
)
from threebody.physics.environment.solver import solve_environment
from threebody.physics.thermal.diagnostics import STEFAN_BOLTZMANN_W_M2_K4


def config(*, temperature_k: float = 285.0, max_step_s: float = 1800.0,
           ocean_mass_kg: float = 1.4e21) -> DynamicEnvironmentConfig:
    return DynamicEnvironmentConfig(
        planet_radius_m=6.371e6,
        emissivity=0.94,
        internal_heat_flux_w_m2=0.08,
        surface=ThermalReservoir(
            mass_kg=2.0e19, specific_heat_j_kg_k=900.0,
            initial_temperature_k=temperature_k, material="regolith",
        ),
        shallow_rock=ThermalReservoir(
            mass_kg=1.0e21, specific_heat_j_kg_k=850.0,
            initial_temperature_k=temperature_k, material="silicate_rock",
        ),
        ocean_mixed_layer=OptionalOceanReservoir(
            mass_kg=ocean_mass_kg, specific_heat_j_kg_k=4184.0,
            initial_temperature_k=(temperature_k if ocean_mass_kg else None),
        ),
        surface_rock_exchange_w_m2_k=0.8,
        surface_ocean_exchange_w_m2_k=(8.0 if ocean_mass_kg else 0.0),
        integrator=EnvironmentIntegratorConfig(
            method="DOP853", rtol=1e-10, atol_k=1e-10, max_step_s=max_step_s,
        ),
    )


def test_constant_equilibrium_and_energy_conservation():
    cfg = config()
    equilibrium_absorbed = (
        cfg.emissivity * STEFAN_BOLTZMANN_W_M2_K4 * 285.0**4
        - cfg.internal_heat_flux_w_m2
    )
    result = solve_environment(
        cfg, ConstantForcing(equilibrium_absorbed), np.linspace(0.0, 20 * 86400.0, 41)
    )
    assert max(abs(state.surface_temperature_k - 285.0) for state in result.states) < 1e-10
    assert max(abs(state.shallow_rock_temperature_k - 285.0) for state in result.states) < 1e-10
    assert result.diagnostics.maximum_scaled_energy_residual < 1e-14
    assert result.physics_semantics == "PHYSICAL_PARAMETERIZED_THREE_RESERVOIR"


def test_radiative_step_has_thermal_inertia_and_lagging_reservoirs():
    cfg = config()
    before = cfg.emissivity * STEFAN_BOLTZMANN_W_M2_K4 * 285.0**4 - 0.08
    result = solve_environment(
        cfg, StepForcing(before, before + 400.0, 86400.0),
        np.linspace(0.0, 4 * 86400.0, 49),
    )
    at_step = result.states[12]
    final = result.states[-1]
    assert at_step.surface_temperature_k == pytest.approx(285.0, abs=2e-7)
    assert final.surface_temperature_k > final.shallow_rock_temperature_k > 285.0
    assert final.surface_temperature_k > final.ocean_mixed_layer_temperature_k > 285.0
    assert result.diagnostics.maximum_scaled_energy_residual < 1e-10


def test_timestep_convergence_and_positive_stability():
    forcing = StepForcing(180.0, 580.0, 3600.0)
    times = np.linspace(0.0, 8 * 86400.0, 65)
    coarse = solve_environment(config(max_step_s=3600.0), forcing, times)
    fine = solve_environment(config(max_step_s=450.0), forcing, times)
    difference = max(
        abs(a.surface_temperature_k - b.surface_temperature_k)
        for a, b in zip(coarse.states, fine.states, strict=True)
    )
    assert difference < 2e-5
    assert coarse.diagnostics.minimum_temperature_k > 0.0


def test_larger_ocean_inventory_reduces_transient_surface_warming():
    times = np.linspace(0.0, 12 * 86400.0, 49)
    low = solve_environment(
        config(ocean_mass_kg=1.0e19), ConstantForcing(620.0), times
    )
    high = solve_environment(
        config(ocean_mass_kg=2.0e21), ConstantForcing(620.0), times
    )
    assert high.states[-1].surface_temperature_k < low.states[-1].surface_temperature_k
    assert high.states[-1].ocean_mixed_layer_heat_capacity_j_k > (
        low.states[-1].ocean_mixed_layer_heat_capacity_j_k
    )


def test_long_constant_forcing_remains_finite_and_tends_toward_balance():
    cfg = config(ocean_mass_kg=0.0, max_step_s=21600.0)
    cfg = cfg.model_copy(update={
        "surface": ThermalReservoir(
            mass_kg=1.0e18, specific_heat_j_kg_k=900.0,
            initial_temperature_k=285.0, material="thin_benchmark_surface",
        ),
        "shallow_rock": ThermalReservoir(
            mass_kg=1.0e18, specific_heat_j_kg_k=850.0,
            initial_temperature_k=285.0, material="thin_benchmark_rock",
        ),
    })
    times = np.linspace(0.0, 600 * 86400.0, 61)
    result = solve_environment(cfg, ConstantForcing(280.0), times)
    assert result.diagnostics.minimum_temperature_k > 0.0
    assert math.isfinite(result.diagnostics.maximum_temperature_k)
    last = result.states[-1]
    assert abs(last.fluxes.net_external_w_m2) < 0.2
    assert abs(last.fluxes.surface_to_rock_w_m2) < 0.2


def test_checkpoint_resume_matches_uninterrupted_solution(tmp_path):
    cfg = config()
    forcing = StepForcing(220.0, 420.0, 2 * 86400.0)
    times = np.linspace(0.0, 6 * 86400.0, 49)
    whole = solve_environment(cfg, forcing, times)
    first = solve_environment(cfg, forcing, times[:25])
    checkpoint_path = tmp_path / "environment-checkpoint.json"
    saved = write_environment_checkpoint(
        checkpoint_path, state=first.states[-1], config=cfg, forcing=forcing,
        checkpoint_index=0, next_output_index=25,
    )
    loaded = load_environment_checkpoint(checkpoint_path, config=cfg, forcing=forcing)
    assert loaded.checkpoint_sha256 == saved.checkpoint_sha256
    resumed = solve_environment(cfg, forcing, times[24:], initial_state=loaded.state)
    assert resumed.states[-1].surface_temperature_k == pytest.approx(
        whole.states[-1].surface_temperature_k, abs=1e-9
    )
    assert resumed.states[-1].shallow_rock_temperature_k == pytest.approx(
        whole.states[-1].shallow_rock_temperature_k, abs=1e-9
    )
    assert resumed.states[-1].ocean_mixed_layer_temperature_k == pytest.approx(
        whole.states[-1].ocean_mixed_layer_temperature_k, abs=1e-9
    )
    with pytest.raises(ConfigError, match="immutable"):
        write_environment_checkpoint(
            checkpoint_path, state=first.states[-1], config=cfg, forcing=forcing,
            checkpoint_index=0, next_output_index=25,
        )


def test_checkpoint_rejects_tamper_and_config_mismatch(tmp_path):
    cfg = config()
    forcing = ConstantForcing(240.0)
    result = solve_environment(cfg, forcing, [0.0, 3600.0])
    path = tmp_path / "checkpoint.json"
    write_environment_checkpoint(
        path, state=result.states[-1], config=cfg, forcing=forcing,
        checkpoint_index=0, next_output_index=2,
    )
    text = path.read_text().replace('"checkpoint_index": 0', '"checkpoint_index": 1')
    path.write_text(text)
    with pytest.raises(ConfigError, match="digest mismatch"):
        load_environment_checkpoint(path, config=cfg, forcing=forcing)


def test_zero_ocean_inventory_is_explicit_and_strict():
    cfg = config(ocean_mass_kg=0.0)
    result = solve_environment(cfg, ConstantForcing(240.0), [0.0, 3600.0])
    assert result.states[-1].ocean_mixed_layer_temperature_k is None
    assert result.states[-1].ocean_mixed_layer_heat_capacity_j_k == 0.0
    raw = cfg.model_dump(mode="python")
    raw["surface_ocean_exchange_w_m2_k"] = 1.0
    with pytest.raises(ValidationError, match="ocean exchange"):
        DynamicEnvironmentConfig.model_validate(raw, strict=True)


def test_forcing_rejects_nonfinite_and_output_grid_is_strict():
    with pytest.raises(ConfigError):
        ConstantForcing(math.inf)
    with pytest.raises(ConfigError, match="strictly increasing"):
        solve_environment(config(), ConstantForcing(200.0), [0.0, 0.0])
