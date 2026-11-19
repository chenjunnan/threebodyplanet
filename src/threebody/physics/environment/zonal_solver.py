from __future__ import annotations

import bisect
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
from threebody.physics.environment.zonal_models import (
    EnvironmentPointQuery,
    EnvironmentPointState,
    MaterialMeltComponent,
    SurfacePhaseDiagnostic,
    ZonalBandState,
    ZonalDynamicEnvironmentConfig,
    ZonalDynamicEnvironmentResult,
    ZonalEnvironmentDiagnostics,
    ZonalEnvironmentSnapshot,
)
from threebody.physics.thermal.diagnostics import STEFAN_BOLTZMANN_W_M2_K4

MELTING_RANGES = {
    # (solidus at 1 atm, liquidus at 1 atm, positive pressure slope K/Pa)
    "basaltic_silicate": (1350.0, 1950.0, 1.2e-8),
    "granitic_silicate": (1120.0, 1650.0, 1.0e-8),
    "iron_nickel": (1720.0, 2150.0, 8.0e-9),
    "sulfur_compounds": (360.0, 460.0, 3.0e-8),
    "water_ice": (251.0, 273.15, -7.4e-8),
}


def _latitude_geometry(count: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    sine_edges = np.linspace(-1.0, 1.0, count + 1)
    edges = np.arcsin(sine_edges)
    centers = np.arcsin((sine_edges[:-1] + sine_edges[1:]) / 2.0)
    fractions = np.diff(sine_edges) / 2.0
    return edges, centers, fractions


def _absorbed_flux_weights(config: ZonalDynamicEnvironmentConfig,
                           centers: np.ndarray) -> np.ndarray:
    if config.absorbed_flux_weights is not None:
        return np.asarray(config.absorbed_flux_weights, dtype=float)
    weights = np.maximum(0.0, np.cos(centers))
    return weights / weights.mean()


def surface_phase_diagnostic(
    config: ZonalDynamicEnvironmentConfig, temperature_k: float,
) -> SurfacePhaseDiagnostic:
    components = []
    expected_liquid = 0.0
    expected_magma = 0.0
    flags = [
        "MELT_IS_PARAMETERIZED_FROM_FIXED_PRESSURE_AND_RANDOM_MATERIAL_PRIOR",
        "LATENT_HEAT_AND_MATERIAL_TRANSPORT_NOT_COUPLED_IN_M72B_V1",
    ]
    if config.reference_surface_pressure_pa > 2.0e8:
        flags.append("PRESSURE_EXCEEDS_LINEAR_MELTING_PROXY_VALIDITY")
    pressure_offset = config.reference_surface_pressure_pa - 101325.0
    for material in config.material_profile.components:
        base_solidus, base_liquidus, slope = MELTING_RANGES[material.material_id]
        solidus = max(1.0, base_solidus + slope * pressure_offset)
        liquidus = max(solidus + 1.0, base_liquidus + slope * pressure_offset)
        probability = min(1.0, max(0.0, (temperature_k - solidus) / (
            liquidus - solidus
        )))
        contribution = material.mass_fraction * probability
        expected_liquid += contribution
        if material.material_id in {"basaltic_silicate", "granitic_silicate"}:
            expected_magma += contribution
        components.append(MaterialMeltComponent(
            material_id=material.material_id,
            mass_fraction=material.mass_fraction,
            pressure_adjusted_solidus_k=solidus,
            pressure_adjusted_liquidus_k=liquidus,
            melt_probability=probability,
        ))
    if temperature_k > 2500.0:
        flags.append("TEMPERATURE_EXCEEDS_SIMPLE_CONDENSED_PHASE_VALIDITY")
    classification = (
        "SOLID_DOMINANT" if expected_liquid <= 1.0e-12 else
        "LIQUID_DOMINANT" if expected_liquid >= 0.95 else "PARTIALLY_LIQUID"
    )
    return SurfacePhaseDiagnostic(
        expected_liquid_fraction=expected_liquid,
        expected_magma_fraction=expected_magma,
        classification=classification,
        components=tuple(components),
        uncertainty_flags=tuple(flags),
    )


def solve_zonal_environment(
    config: ZonalDynamicEnvironmentConfig,
    forcing: EnvironmentForcing,
    output_times_s: Sequence[float],
) -> ZonalDynamicEnvironmentResult:
    times = np.asarray(output_times_s, dtype=float)
    if times.ndim != 1 or len(times) < 2 or not np.all(np.isfinite(times)):
        raise ConfigError("zonal output times must contain at least two finite values")
    if np.any(np.diff(times) <= 0.0):
        raise ConfigError("zonal output times must be strictly increasing")
    if len(times) > config.max_output_samples:
        raise ConfigError("zonal output grid exceeds max_output_samples")
    if times[0] < forcing.coverage_s[0] or times[-1] > forcing.coverage_s[1]:
        raise ConfigError("zonal output times exceed forcing coverage")

    count = config.latitude_band_count
    edges, centers, fractions = _latitude_geometry(count)
    area_total = 4.0 * math.pi * config.planet_radius_m**2
    areas = area_total * fractions
    weights = _absorbed_flux_weights(config, centers)
    surface_capacities = config.surface.heat_capacity_j_k * fractions
    rock_capacities = config.shallow_rock.heat_capacity_j_k * fractions
    ocean_capacities = config.ocean_mixed_layer.heat_capacity_j_k * fractions
    capacities = np.concatenate((surface_capacities, rock_capacities, ocean_capacities))
    ocean_initial = config.ocean_mixed_layer.initial_temperature_k
    initial = np.concatenate((
        np.full(count, config.surface.initial_temperature_k),
        np.full(count, config.shallow_rock.initial_temperature_k),
        np.full(count, config.surface.initial_temperature_k
                if ocean_initial is None else ocean_initial),
    ))
    initial_energy = float(capacities @ initial)

    def flux_terms(t_s: float, temperatures: np.ndarray):
        surface = temperatures[:count]
        rock = temperatures[count:2 * count]
        ocean = temperatures[2 * count:3 * count]
        global_absorbed = forcing.sample(t_s).absorbed_flux_w_m2
        absorbed = global_absorbed * weights
        emitted = config.emissivity * STEFAN_BOLTZMANN_W_M2_K4 * surface**4
        external = absorbed + config.internal_heat_flux_w_m2 - emitted
        to_rock = config.surface_rock_exchange_w_m2_k * (surface - rock)
        to_ocean = config.surface_ocean_exchange_w_m2_k * (surface - ocean)
        transport_power = np.zeros(count)
        for interface in range(count - 1):
            conductance = (
                config.meridional_interface_conductance_w_k
                * max(0.0, math.cos(float(edges[interface + 1])))
            )
            poleward = conductance * (surface[interface] - surface[interface + 1])
            transport_power[interface] -= poleward
            transport_power[interface + 1] += poleward
        return absorbed, emitted, external, to_rock, to_ocean, transport_power

    def derivative(t_s: float, values: np.ndarray) -> np.ndarray:
        if np.any(values[:3 * count] <= 0.0) or not np.all(np.isfinite(values)):
            raise ConfigError("zonal integration reached invalid temperature/state")
        _, _, external, to_rock, to_ocean, transport = flux_terms(
            t_s, values[:3 * count]
        )
        result = np.zeros(3 * count + 1)
        result[:count] = (
            areas * (external - to_rock - to_ocean) + transport
        ) / surface_capacities
        result[count:2 * count] = areas * to_rock / rock_capacities
        if config.ocean_mixed_layer.mass_kg > 0.0:
            result[2 * count:3 * count] = areas * to_ocean / ocean_capacities
        result[-1] = float(areas @ external)
        return result

    solution = solve_ivp(
        derivative, (float(times[0]), float(times[-1])),
        np.concatenate((initial, [0.0])), t_eval=times,
        method=config.integrator.method, rtol=config.integrator.rtol,
        atol=np.concatenate((
            np.full(3 * count, config.integrator.atol_k),
            [max(1.0, abs(initial_energy) * config.integrator.rtol)],
        )),
        max_step=config.integrator.max_step_s,
    )
    if not solution.success or solution.y.shape != (3 * count + 1, len(times)):
        raise ConfigError(f"zonal environment solver failed: {solution.message}")

    snapshots = []
    residuals = []
    temperatures_seen = []
    transport_cancellation = []
    for time_index, t_s in enumerate(times):
        temperatures = solution.y[:3 * count, time_index]
        if np.any(temperatures <= 0.0) or not np.all(np.isfinite(temperatures)):
            raise ConfigError("zonal environment solver produced invalid temperatures")
        absorbed, emitted, _, _, _, transport = flux_terms(float(t_s), temperatures)
        energy = float(capacities @ temperatures)
        residuals.append(energy - initial_energy - float(solution.y[-1, time_index]))
        transport_cancellation.append(abs(float(transport.sum())))
        temperatures_seen.extend(temperatures[:2 * count])
        if config.ocean_mixed_layer.mass_kg > 0.0:
            temperatures_seen.extend(temperatures[2 * count:3 * count])
        bands = []
        for index in range(count):
            bands.append(ZonalBandState(
                band_index=index,
                latitude_lower_deg=math.degrees(float(edges[index])),
                latitude_center_deg=math.degrees(float(centers[index])),
                latitude_upper_deg=math.degrees(float(edges[index + 1])),
                area_fraction=float(fractions[index]),
                surface_temperature_k=float(temperatures[index]),
                shallow_rock_temperature_k=float(temperatures[count + index]),
                ocean_mixed_layer_temperature_k=(
                    None if config.ocean_mixed_layer.mass_kg == 0.0
                    else float(temperatures[2 * count + index])
                ),
                surface_heat_capacity_j_k=float(surface_capacities[index]),
                shallow_rock_heat_capacity_j_k=float(rock_capacities[index]),
                ocean_mixed_layer_heat_capacity_j_k=float(ocean_capacities[index]),
                absorbed_stellar_w_m2=float(absorbed[index]),
                emitted_thermal_w_m2=float(emitted[index]),
                internal_heat_w_m2=config.internal_heat_flux_w_m2,
                meridional_heat_convergence_w_m2=float(transport[index] / areas[index]),
                surface_phase=surface_phase_diagnostic(config, float(temperatures[index])),
            ))
        snapshots.append(ZonalEnvironmentSnapshot(
            t_s=float(t_s), bands=tuple(bands), total_sensible_energy_j=energy
        ))

    scale = max(abs(initial_energy), max(abs(float(v)) for v in solution.y[-1]), 1.0)
    maximum_residual = max(abs(value) for value in residuals)
    runtime = {
        "project": __version__, "python": platform.python_version(),
        "numpy": np.__version__, "scipy": scipy.__version__,
    }
    diagnostics = ZonalEnvironmentDiagnostics(
        maximum_absolute_energy_residual_j=maximum_residual,
        maximum_scaled_energy_residual=maximum_residual / scale,
        maximum_transport_cancellation_w=max(transport_cancellation),
        minimum_temperature_k=min(float(value) for value in temperatures_seen),
        maximum_temperature_k=max(float(value) for value in temperatures_seen),
        solver_nfev=solution.nfev,
    )
    payload = {
        "schema_version": "0.9.0",
        "model_id": "m72_zonal_three_reservoir_v1",
        "config_sha256": config.config_sha256,
        "material_profile_sha256": config.material_profile.profile_sha256,
        "forcing_source_sha256": forcing.source_sha256,
        "forcing_kind": forcing.kind,
        "runtime_versions": runtime,
        "snapshots": [snapshot.model_dump(mode="json") for snapshot in snapshots],
        "diagnostics": diagnostics.model_dump(mode="json"),
        "physics_semantics": (
            "PHYSICAL_PARAMETERIZED_EQUAL_AREA_ZONAL_THREE_RESERVOIR"
        ),
    }
    return ZonalDynamicEnvironmentResult(
        config_sha256=config.config_sha256,
        material_profile_sha256=config.material_profile.profile_sha256,
        forcing_source_sha256=forcing.source_sha256,
        forcing_kind=forcing.kind,
        runtime_versions=runtime,
        snapshots=tuple(snapshots), diagnostics=diagnostics,
        result_sha256=sha256_json(payload),
    )


def query_zonal_environment(
    config: ZonalDynamicEnvironmentConfig,
    result: ZonalDynamicEnvironmentResult,
    query: EnvironmentPointQuery,
) -> EnvironmentPointState:
    if result.config_sha256 != config.config_sha256:
        raise ConfigError("environment query config/result hash mismatch")
    times = [snapshot.t_s for snapshot in result.snapshots]
    if query.t_s < times[0] or query.t_s > times[-1]:
        raise ConfigError("environment query time outside result coverage")
    right = bisect.bisect_left(times, query.t_s)
    if right < len(times) and times[right] == query.t_s:
        left = right
        fraction = 0.0
        sampling = "EXACT"
    else:
        left = right - 1
        fraction = (query.t_s - times[left]) / (times[right] - times[left])
        sampling = "LINEAR_STATE_INTERPOLATION"
    reference = result.snapshots[left]
    band_index = next(
        band.band_index for band in reference.bands
        if band.latitude_lower_deg <= query.latitude_deg <= band.latitude_upper_deg
    )
    before = result.snapshots[left].bands[band_index]
    after = result.snapshots[right].bands[band_index]

    def interpolate(name: str) -> float | None:
        first = getattr(before, name)
        second = getattr(after, name)
        if first is None or second is None:
            return None
        return float(first + fraction * (second - first))

    surface_temperature = interpolate("surface_temperature_k")
    assert surface_temperature is not None
    phase = surface_phase_diagnostic(config, surface_temperature)
    return EnvironmentPointState(
        result_sha256=result.result_sha256,
        t_s=query.t_s, latitude_deg=query.latitude_deg,
        longitude_deg=query.longitude_deg, band_index=band_index,
        time_sampling=sampling,
        source_bracket_s=(times[left], times[right]),
        surface_temperature_k=surface_temperature,
        shallow_rock_temperature_k=interpolate("shallow_rock_temperature_k"),
        ocean_mixed_layer_temperature_k=interpolate(
            "ocean_mixed_layer_temperature_k"
        ),
        surface_pressure_pa=config.reference_surface_pressure_pa,
        expected_surface_liquid_fraction=phase.expected_liquid_fraction,
        expected_magma_fraction=phase.expected_magma_fraction,
        uncertainty_flags=(
            "PRESSURE_IS_FIXED_BOUNDARY_NOT_M72B_PREDICTION",
            "AIR_HUMIDITY_WIND_PRECIPITATION_AND_WEATHER_NOT_COMPUTED",
            *phase.uncertainty_flags,
        ),
    )
