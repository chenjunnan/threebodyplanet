from __future__ import annotations

import math
import platform
from collections.abc import Sequence

import numpy as np
import scipy
from scipy.integrate import solve_ivp

from threebody import __version__
from threebody.common.json import sha256_json
from threebody.errors import ConfigError
from threebody.physics.environment.forcing import EnvironmentForcing
from threebody.physics.environment.models import (
    DynamicEnvironmentConfig,
    DynamicEnvironmentResult,
    DynamicEnvironmentState,
    EnvironmentDiagnostics,
    EnvironmentFluxes,
)
from threebody.physics.thermal.diagnostics import STEFAN_BOLTZMANN_W_M2_K4


def _initial_temperatures(config: DynamicEnvironmentConfig) -> np.ndarray:
    ocean = config.ocean_mixed_layer.initial_temperature_k
    return np.asarray([
        config.surface.initial_temperature_k,
        config.shallow_rock.initial_temperature_k,
        config.surface.initial_temperature_k if ocean is None else ocean,
    ], dtype=float)


def _fluxes(config: DynamicEnvironmentConfig, forcing: EnvironmentForcing,
            t_s: float, temperatures: np.ndarray) -> EnvironmentFluxes:
    surface, rock, ocean = (float(value) for value in temperatures)
    absorbed = forcing.sample(t_s).absorbed_flux_w_m2
    emitted = config.emissivity * STEFAN_BOLTZMANN_W_M2_K4 * surface**4
    to_rock = config.surface_rock_exchange_w_m2_k * (surface - rock)
    to_ocean = config.surface_ocean_exchange_w_m2_k * (surface - ocean)
    return EnvironmentFluxes(
        absorbed_stellar_w_m2=absorbed,
        emitted_thermal_w_m2=emitted,
        internal_heat_w_m2=config.internal_heat_flux_w_m2,
        surface_to_rock_w_m2=to_rock,
        surface_to_ocean_w_m2=to_ocean,
        net_external_w_m2=absorbed + config.internal_heat_flux_w_m2 - emitted,
    )


def solve_environment(
    config: DynamicEnvironmentConfig,
    forcing: EnvironmentForcing,
    output_times_s: Sequence[float],
    *,
    initial_state: DynamicEnvironmentState | None = None,
) -> DynamicEnvironmentResult:
    times = np.asarray(output_times_s, dtype=float)
    if times.ndim != 1 or len(times) < 2 or not np.all(np.isfinite(times)):
        raise ConfigError("environment output times must contain at least two finite values")
    if np.any(np.diff(times) <= 0.0):
        raise ConfigError("environment output times must be strictly increasing")
    if len(times) > config.max_output_samples:
        raise ConfigError("environment output grid exceeds max_output_samples")
    if times[0] < forcing.coverage_s[0] or times[-1] > forcing.coverage_s[1]:
        raise ConfigError("environment output times exceed forcing coverage")
    area = 4.0 * math.pi * config.planet_radius_m**2
    capacities = np.asarray([
        config.surface.heat_capacity_j_k,
        config.shallow_rock.heat_capacity_j_k,
        config.ocean_mixed_layer.heat_capacity_j_k,
    ])
    if initial_state is None:
        temperatures = _initial_temperatures(config)
    else:
        if not math.isclose(initial_state.t_s, times[0], rel_tol=0.0, abs_tol=1e-9):
            raise ConfigError("resume state time does not equal first output time")
        if not math.isclose(initial_state.surface_heat_capacity_j_k, capacities[0]):
            raise ConfigError("resume state/config surface heat capacity mismatch")
        if not math.isclose(initial_state.shallow_rock_heat_capacity_j_k, capacities[1]):
            raise ConfigError("resume state/config rock heat capacity mismatch")
        if not math.isclose(initial_state.ocean_mixed_layer_heat_capacity_j_k, capacities[2]):
            raise ConfigError("resume state/config ocean heat capacity mismatch")
        temperatures = np.asarray([
            initial_state.surface_temperature_k,
            initial_state.shallow_rock_temperature_k,
            initial_state.surface_temperature_k
            if initial_state.ocean_mixed_layer_temperature_k is None
            else initial_state.ocean_mixed_layer_temperature_k,
        ])

    initial_energy = float(np.dot(capacities, temperatures))
    y0 = np.concatenate((temperatures, [0.0]))

    def derivative(t_s: float, y: np.ndarray) -> np.ndarray:
        if np.any(y[:3] <= 0.0) or not np.all(np.isfinite(y)):
            raise ConfigError("environment integration reached invalid temperature/state")
        flux = _fluxes(config, forcing, t_s, y[:3])
        derivatives = np.zeros(4)
        derivatives[0] = area * (
            flux.net_external_w_m2 - flux.surface_to_rock_w_m2
            - flux.surface_to_ocean_w_m2
        ) / capacities[0]
        derivatives[1] = area * flux.surface_to_rock_w_m2 / capacities[1]
        if capacities[2] > 0.0:
            derivatives[2] = area * flux.surface_to_ocean_w_m2 / capacities[2]
        derivatives[3] = area * flux.net_external_w_m2
        return derivatives

    solution = solve_ivp(
        derivative, (float(times[0]), float(times[-1])), y0,
        t_eval=times, method=config.integrator.method,
        rtol=config.integrator.rtol,
        # The fourth state is an energy integral in joules, not a temperature.
        # Giving it the kelvin absolute tolerance makes a forcing discontinuity
        # numerically impossible to cross near a zero-valued accumulator.
        atol=np.asarray([
            config.integrator.atol_k,
            config.integrator.atol_k,
            config.integrator.atol_k,
            max(1.0, abs(initial_energy) * config.integrator.rtol),
        ]),
        max_step=config.integrator.max_step_s,
    )
    if not solution.success or solution.y.shape != (4, len(times)):
        raise ConfigError(f"environment solver failed: {solution.message}")
    states = []
    residuals = []
    all_temperatures = []
    for index, t_s in enumerate(times):
        values = solution.y[:3, index]
        if np.any(values <= 0.0) or not np.all(np.isfinite(values)):
            raise ConfigError("environment solver produced invalid temperatures")
        flux = _fluxes(config, forcing, float(t_s), values)
        energy = float(np.dot(capacities, values))
        residuals.append(energy - initial_energy - float(solution.y[3, index]))
        all_temperatures.extend(values[:2] if capacities[2] == 0 else values)
        states.append(DynamicEnvironmentState(
            t_s=float(t_s), surface_temperature_k=float(values[0]),
            shallow_rock_temperature_k=float(values[1]),
            ocean_mixed_layer_temperature_k=(None if capacities[2] == 0 else float(values[2])),
            surface_heat_capacity_j_k=float(capacities[0]),
            shallow_rock_heat_capacity_j_k=float(capacities[1]),
            ocean_mixed_layer_heat_capacity_j_k=float(capacities[2]),
            surface_mass_kg=config.surface.mass_kg,
            shallow_rock_mass_kg=config.shallow_rock.mass_kg,
            ocean_mixed_layer_mass_kg=config.ocean_mixed_layer.mass_kg,
            fluxes=flux, total_sensible_energy_j=energy,
        ))
    scale = max(abs(initial_energy), max(abs(float(v)) for v in solution.y[3]), 1.0)
    maximum = max(abs(value) for value in residuals)
    runtime = {
        "project": __version__, "python": platform.python_version(),
        "numpy": np.__version__, "scipy": scipy.__version__,
    }
    payload = {
        "schema_version": "0.8.0", "model_id": "m72_three_reservoir_v1",
        "config_sha256": config.config_sha256,
        "forcing_source_sha256": forcing.source_sha256,
        "forcing_kind": forcing.kind, "runtime_versions": runtime,
        "states": [state.model_dump(mode="json") for state in states],
        "diagnostics": {
            "maximum_absolute_energy_residual_j": maximum,
            "maximum_scaled_energy_residual": maximum/scale,
            "minimum_temperature_k": min(all_temperatures),
            "maximum_temperature_k": max(all_temperatures),
            "solver_nfev": solution.nfev,
        },
        "physics_semantics": "PHYSICAL_PARAMETERIZED_THREE_RESERVOIR",
    }
    return DynamicEnvironmentResult(
        config_sha256=config.config_sha256,
        forcing_source_sha256=forcing.source_sha256,
        forcing_kind=forcing.kind,
        runtime_versions=runtime,
        states=tuple(states),
        diagnostics=EnvironmentDiagnostics.model_validate(payload["diagnostics"], strict=True),
        result_sha256=sha256_json(payload),
    )
