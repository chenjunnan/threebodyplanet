from __future__ import annotations

from dataclasses import dataclass

STEFAN_BOLTZMANN_W_M2_K4 = 5.670374419e-8
THERMAL_CONSTANT_SET_VERSION = "codata-2018-sigma-m3-v1"


@dataclass(frozen=True)
class EnergyBudget:
    heat_content_change_j_m2: float
    absorbed_stellar_energy_j_m2: float
    internal_heat_energy_j_m2: float
    emitted_thermal_energy_j_m2: float
    meridional_transport_energy_j_m2: float
    residual_j_m2: float
    normalized_residual: float

