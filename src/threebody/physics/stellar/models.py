from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StarFlux:
    body_id: str
    luminosity_w: float
    distance_m: float
    direction_inertial_unit: tuple[float, float, float]
    flux_w_m2: float


@dataclass(frozen=True)
class StellarFluxSnapshot:
    t_s: float
    planet_id: str
    stars: tuple[StarFlux, ...]
    planet_center_flux_w_m2: float
    globally_averaged_incoming_w_m2: float
    model: str = "isotropic_bolometric_v1"
    occultation: str = "disabled"

