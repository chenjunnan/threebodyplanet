from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
from scipy.integrate import solve_ivp

from threebody.errors import ThermalExecutionError
from threebody.physics.thermal.diagnostics import (
    STEFAN_BOLTZMANN_W_M2_K4,
    EnergyBudget,
)
from threebody.physics.thermal.models import ThermalIntegratorSettings


@dataclass(frozen=True)
class C1Sample:
    t_s: float
    latitude_center_rad: tuple[float, ...]
    temperature_k: tuple[float, ...]
    zonal_incoming_w_m2: tuple[float, ...]
    absorbed_w_m2: tuple[float, ...]
    emitted_w_m2: tuple[float, ...]
    meridional_convergence_w_m2: tuple[float, ...]
    global_mean_temperature_k: float
    globally_averaged_incoming_w_m2: float


@dataclass(frozen=True)
class C1Result:
    samples: tuple[C1Sample, ...]
    energy_budget: EnergyBudget
    solver: str
    function_evaluations: int
    maximum_absolute_global_transport_imbalance_w_m2: float


def equal_area_grid(latitude_bands: int) -> tuple[np.ndarray, np.ndarray, float]:
    if latitude_bands < 4:
        raise ThermalExecutionError("C1 requires at least four latitude bands")
    dx = 2.0 / latitude_bands
    x = -1.0 + (np.arange(latitude_bands, dtype=np.float64) + 0.5) * dx
    return x, np.arcsin(x), dx


def meridional_diffusion_convergence(
    temperature_k: Sequence[float], diffusivity_w_m2_k: float
) -> np.ndarray:
    temperature = np.asarray(temperature_k, dtype=np.float64)
    if temperature.ndim != 1 or len(temperature) < 4 or not np.all(np.isfinite(temperature)):
        raise ThermalExecutionError("C1 temperature field must be a finite one-dimensional array")
    if diffusivity_w_m2_k < 0.0 or not math.isfinite(diffusivity_w_m2_k):
        raise ThermalExecutionError("C1 diffusivity must be finite and non-negative")
    count = len(temperature)
    dx = 2.0 / count
    face_x = -1.0 + np.arange(1, count, dtype=np.float64) * dx
    face_flux = (
        diffusivity_w_m2_k
        * (1.0 - face_x**2)
        * np.diff(temperature)
        / dx
    )
    convergence = np.empty(count, dtype=np.float64)
    convergence[0] = face_flux[0] / dx
    convergence[1:-1] = np.diff(face_flux) / dx
    convergence[-1] = -face_flux[-1] / dx
    if not np.all(np.isfinite(convergence)):
        raise ThermalExecutionError("C1 diffusion convergence became non-finite")
    return convergence


def solve_c1(
    *,
    times_s: Sequence[float],
    initial_temperature_k: float | Sequence[float],
    zonal_incoming: Callable[[float, np.ndarray], Sequence[float]],
    latitude_bands: int,
    bond_albedo: float,
    effective_emissivity: float,
    areal_heat_capacity_j_m2_k: float,
    internal_heat_flux_w_m2: float,
    diffusivity_w_m2_k: float,
    integrator: ThermalIntegratorSettings,
) -> C1Result:
    output_times = np.asarray(times_s, dtype=np.float64)
    if (
        output_times.ndim != 1
        or len(output_times) < 2
        or not np.all(np.isfinite(output_times))
        or not np.all(np.diff(output_times) > 0.0)
    ):
        raise ThermalExecutionError("C1 output times must be finite and strictly increasing")
    _, latitudes, _ = equal_area_grid(latitude_bands)
    if np.isscalar(initial_temperature_k):
        initial_temperature = np.full(latitude_bands, float(initial_temperature_k))
    else:
        initial_temperature = np.asarray(initial_temperature_k, dtype=np.float64)
    if (
        initial_temperature.shape != (latitude_bands,)
        or not np.all(np.isfinite(initial_temperature))
        or np.any(initial_temperature <= 0.0)
    ):
        raise ThermalExecutionError("C1 initial temperatures must be finite positive Kelvin")
    if not 0.0 <= bond_albedo <= 1.0:
        raise ThermalExecutionError("C1 Bond albedo must be within [0, 1]")
    if not 0.0 <= effective_emissivity <= 1.0:
        raise ThermalExecutionError("C1 effective emissivity must be within [0, 1]")
    if (
        areal_heat_capacity_j_m2_k <= 0.0
        or not math.isfinite(areal_heat_capacity_j_m2_k)
    ):
        raise ThermalExecutionError("C1 areal heat capacity must be finite and positive")
    if internal_heat_flux_w_m2 < 0.0 or not math.isfinite(internal_heat_flux_w_m2):
        raise ThermalExecutionError("C1 internal heat flux must be finite and non-negative")
    maximum_transport_imbalance = 0.0

    def forcing(t_s: float) -> np.ndarray:
        values = np.asarray(zonal_incoming(float(t_s), latitudes), dtype=np.float64)
        if values.shape != (latitude_bands,) or not np.all(np.isfinite(values)):
            raise ThermalExecutionError("C1 zonal forcing has invalid shape or values")
        if np.any(values < 0.0):
            raise ThermalExecutionError("C1 zonal forcing must be non-negative")
        return values

    def rates(t_s: float, state: np.ndarray) -> np.ndarray:
        nonlocal maximum_transport_imbalance
        temperature = state[:latitude_bands]
        if not np.all(np.isfinite(temperature)) or np.any(temperature <= 0.0):
            raise ThermalExecutionError("C1 temperature became non-positive or non-finite")
        incoming = forcing(t_s)
        absorbed = (1.0 - bond_albedo) * incoming
        emitted = effective_emissivity * STEFAN_BOLTZMANN_W_M2_K4 * temperature**4
        transport = meridional_diffusion_convergence(
            temperature, diffusivity_w_m2_k
        )
        transport_imbalance = abs(float(np.mean(transport)))
        maximum_transport_imbalance = max(
            maximum_transport_imbalance, transport_imbalance
        )
        tendency = (
            absorbed + internal_heat_flux_w_m2 - emitted + transport
        ) / areal_heat_capacity_j_m2_k
        result = np.concatenate(
            (
                tendency,
                np.asarray(
                    [
                        float(np.mean(absorbed)),
                        internal_heat_flux_w_m2,
                        float(np.mean(emitted)),
                        float(np.mean(transport)),
                    ]
                ),
            )
        )
        if not np.all(np.isfinite(result)):
            raise ThermalExecutionError("C1 tendency or energy integral became non-finite")
        return result

    initial_state = np.concatenate((initial_temperature, np.zeros(4)))
    absolute_tolerance = np.concatenate(
        (np.full(latitude_bands, integrator.atol_k), np.full(4, 1.0e-3))
    )
    solution = solve_ivp(
        rates,
        (float(output_times[0]), float(output_times[-1])),
        initial_state,
        method="BDF",
        t_eval=output_times,
        rtol=integrator.rtol,
        atol=absolute_tolerance,
        max_step=integrator.max_step_s,
    )
    if not solution.success:
        raise ThermalExecutionError(f"C1 solve_ivp failed: {solution.message}")
    if not np.all(np.isfinite(solution.y)) or np.any(solution.y[:latitude_bands] <= 0.0):
        raise ThermalExecutionError("C1 solver returned invalid temperatures")

    samples: list[C1Sample] = []
    for index, t_s in enumerate(output_times):
        temperature = solution.y[:latitude_bands, index]
        incoming = forcing(float(t_s))
        absorbed = (1.0 - bond_albedo) * incoming
        emitted = effective_emissivity * STEFAN_BOLTZMANN_W_M2_K4 * temperature**4
        transport = meridional_diffusion_convergence(
            temperature, diffusivity_w_m2_k
        )
        samples.append(
            C1Sample(
                t_s=float(t_s),
                latitude_center_rad=tuple(float(value) for value in latitudes),
                temperature_k=tuple(float(value) for value in temperature),
                zonal_incoming_w_m2=tuple(float(value) for value in incoming),
                absorbed_w_m2=tuple(float(value) for value in absorbed),
                emitted_w_m2=tuple(float(value) for value in emitted),
                meridional_convergence_w_m2=tuple(float(value) for value in transport),
                global_mean_temperature_k=float(np.mean(temperature)),
                globally_averaged_incoming_w_m2=float(np.mean(incoming)),
            )
        )
    heat_change = areal_heat_capacity_j_m2_k * (
        samples[-1].global_mean_temperature_k
        - samples[0].global_mean_temperature_k
    )
    absorbed_energy = float(solution.y[latitude_bands, -1])
    internal_energy = float(solution.y[latitude_bands + 1, -1])
    emitted_energy = float(solution.y[latitude_bands + 2, -1])
    transport_energy = float(solution.y[latitude_bands + 3, -1])
    residual = heat_change - (
        absorbed_energy + internal_energy - emitted_energy + transport_energy
    )
    scale = max(
        abs(heat_change),
        abs(absorbed_energy)
        + abs(internal_energy)
        + abs(emitted_energy)
        + abs(transport_energy),
        1.0,
    )
    return C1Result(
        samples=tuple(samples),
        energy_budget=EnergyBudget(
            heat_content_change_j_m2=heat_change,
            absorbed_stellar_energy_j_m2=absorbed_energy,
            internal_heat_energy_j_m2=internal_energy,
            emitted_thermal_energy_j_m2=emitted_energy,
            meridional_transport_energy_j_m2=transport_energy,
            residual_j_m2=residual,
            normalized_residual=abs(residual) / scale,
        ),
        solver="scipy.solve_ivp.BDF",
        function_evaluations=int(solution.nfev),
        maximum_absolute_global_transport_imbalance_w_m2=(
            maximum_transport_imbalance
        ),
    )
