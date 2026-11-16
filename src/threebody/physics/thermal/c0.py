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
class C0Sample:
    t_s: float
    temperature_k: float
    planet_center_flux_w_m2: float
    globally_averaged_incoming_w_m2: float
    absorbed_w_m2: float
    internal_heat_flux_w_m2: float
    emitted_w_m2: float
    net_heating_w_m2: float


@dataclass(frozen=True)
class C0Result:
    samples: tuple[C0Sample, ...]
    energy_budget: EnergyBudget
    solver: str
    function_evaluations: int


def equilibrium_temperature_k(
    planet_center_flux_w_m2: float,
    bond_albedo: float,
    effective_emissivity: float,
    internal_heat_flux_w_m2: float = 0.0,
) -> float:
    numerator = (
        (1.0 - bond_albedo) * planet_center_flux_w_m2 / 4.0
        + internal_heat_flux_w_m2
    )
    denominator = effective_emissivity * STEFAN_BOLTZMANN_W_M2_K4
    if not all(
        math.isfinite(value)
        for value in (
            planet_center_flux_w_m2,
            bond_albedo,
            effective_emissivity,
            internal_heat_flux_w_m2,
        )
    ):
        raise ThermalExecutionError("equilibrium inputs must be finite")
    if not 0.0 <= bond_albedo <= 1.0:
        raise ThermalExecutionError("Bond albedo must be within [0, 1]")
    if not 0.0 < effective_emissivity <= 1.0:
        raise ThermalExecutionError("effective emissivity must be within (0, 1]")
    if planet_center_flux_w_m2 < 0.0 or internal_heat_flux_w_m2 < 0.0:
        raise ThermalExecutionError("radiative and internal fluxes must be non-negative")
    if numerator < 0.0 or denominator <= 0.0:
        raise ThermalExecutionError("equilibrium radiative power must be non-negative")
    return (numerator / denominator) ** 0.25


def solve_c0(
    *,
    times_s: Sequence[float],
    initial_temperature_k: float,
    planet_center_flux: Callable[[float], float],
    bond_albedo: float,
    effective_emissivity: float,
    areal_heat_capacity_j_m2_k: float,
    internal_heat_flux_w_m2: float,
    integrator: ThermalIntegratorSettings,
) -> C0Result:
    output_times = np.asarray(times_s, dtype=np.float64)
    if (
        output_times.ndim != 1
        or len(output_times) < 2
        or not np.all(np.isfinite(output_times))
        or not np.all(np.diff(output_times) > 0.0)
    ):
        raise ThermalExecutionError("C0 output times must be finite and strictly increasing")
    if initial_temperature_k <= 0.0 or not math.isfinite(initial_temperature_k):
        raise ThermalExecutionError("C0 initial temperature must be finite and positive")
    if not 0.0 <= bond_albedo <= 1.0:
        raise ThermalExecutionError("C0 Bond albedo must be within [0, 1]")
    if not 0.0 < effective_emissivity <= 1.0:
        raise ThermalExecutionError("C0 effective emissivity must be within (0, 1]")
    if (
        areal_heat_capacity_j_m2_k <= 0.0
        or not math.isfinite(areal_heat_capacity_j_m2_k)
    ):
        raise ThermalExecutionError("C0 areal heat capacity must be finite and positive")
    if internal_heat_flux_w_m2 < 0.0 or not math.isfinite(internal_heat_flux_w_m2):
        raise ThermalExecutionError("C0 internal heat flux must be finite and non-negative")

    def rates(t_s: float, state: np.ndarray) -> np.ndarray:
        temperature = float(state[0])
        center_flux = float(planet_center_flux(float(t_s)))
        if temperature <= 0.0 or not math.isfinite(temperature):
            raise ThermalExecutionError("C0 temperature became non-positive or non-finite")
        if center_flux < 0.0 or not math.isfinite(center_flux):
            raise ThermalExecutionError("C0 stellar forcing must be finite and non-negative")
        absorbed = (1.0 - bond_albedo) * center_flux / 4.0
        emitted = effective_emissivity * STEFAN_BOLTZMANN_W_M2_K4 * temperature**4
        net = absorbed + internal_heat_flux_w_m2 - emitted
        values = np.asarray(
            [
                net / areal_heat_capacity_j_m2_k,
                absorbed,
                internal_heat_flux_w_m2,
                emitted,
            ],
            dtype=np.float64,
        )
        if not np.all(np.isfinite(values)):
            raise ThermalExecutionError("C0 tendency or energy integral became non-finite")
        return values

    solution = solve_ivp(
        rates,
        (float(output_times[0]), float(output_times[-1])),
        np.asarray([initial_temperature_k, 0.0, 0.0, 0.0]),
        method="RK45",
        t_eval=output_times,
        rtol=integrator.rtol,
        atol=np.asarray([integrator.atol_k, 1.0e-3, 1.0e-3, 1.0e-3]),
        max_step=integrator.max_step_s,
    )
    if not solution.success:
        raise ThermalExecutionError(f"C0 solve_ivp failed: {solution.message}")
    if solution.y.shape != (4, len(output_times)) or not np.all(np.isfinite(solution.y)):
        raise ThermalExecutionError("C0 solver returned an invalid result array")
    if np.any(solution.y[0] <= 0.0):
        raise ThermalExecutionError("C0 solver returned a non-positive Kelvin temperature")

    samples: list[C0Sample] = []
    for index, t_s in enumerate(output_times):
        temperature = float(solution.y[0, index])
        center_flux = float(planet_center_flux(float(t_s)))
        global_incoming = center_flux / 4.0
        absorbed = (1.0 - bond_albedo) * global_incoming
        emitted = effective_emissivity * STEFAN_BOLTZMANN_W_M2_K4 * temperature**4
        samples.append(
            C0Sample(
                t_s=float(t_s),
                temperature_k=temperature,
                planet_center_flux_w_m2=center_flux,
                globally_averaged_incoming_w_m2=global_incoming,
                absorbed_w_m2=absorbed,
                internal_heat_flux_w_m2=internal_heat_flux_w_m2,
                emitted_w_m2=emitted,
                net_heating_w_m2=absorbed + internal_heat_flux_w_m2 - emitted,
            )
        )
    heat_change = areal_heat_capacity_j_m2_k * (
        samples[-1].temperature_k - samples[0].temperature_k
    )
    absorbed_energy = float(solution.y[1, -1])
    internal_energy = float(solution.y[2, -1])
    emitted_energy = float(solution.y[3, -1])
    residual = heat_change - (absorbed_energy + internal_energy - emitted_energy)
    scale = max(
        abs(heat_change),
        abs(absorbed_energy) + abs(internal_energy) + abs(emitted_energy),
        1.0,
    )
    budget = EnergyBudget(
        heat_content_change_j_m2=heat_change,
        absorbed_stellar_energy_j_m2=absorbed_energy,
        internal_heat_energy_j_m2=internal_energy,
        emitted_thermal_energy_j_m2=emitted_energy,
        meridional_transport_energy_j_m2=0.0,
        residual_j_m2=residual,
        normalized_residual=abs(residual) / scale,
    )
    return C0Result(
        samples=tuple(samples),
        energy_budget=budget,
        solver="scipy.solve_ivp.RK45",
        function_evaluations=int(solution.nfev),
    )
