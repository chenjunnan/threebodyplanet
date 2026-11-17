"""Bounded zero-age main-sequence proxies for the 0.3--8 solar-mass generator.

Piecewise mass--luminosity scaling is continuous at 0.43 and 2 solar masses.
It is an approximate bolometric main-sequence prescription, not a stellar
evolution, metallicity, spectrum, or post-merger luminosity model.
"""
from __future__ import annotations

import math

from threebody.common.constants import R_SUN_M

SOLAR_LUMINOSITY_W = 3.828e26
MAIN_SEQUENCE_MIN_SOLAR_MASS = 0.3
MAIN_SEQUENCE_MAX_SOLAR_MASS = 8.0


def main_sequence_properties(mass_solar: float) -> tuple[float, float]:
    """Return (radius_m, bolometric_luminosity_w) for supported MS masses."""
    if not math.isfinite(mass_solar) or not (
        MAIN_SEQUENCE_MIN_SOLAR_MASS <= mass_solar <= MAIN_SEQUENCE_MAX_SOLAR_MASS
    ):
        raise ValueError("main-sequence proxy supports stellar masses 0.3..8 M_sun")
    if mass_solar < 0.43:
        relative_luminosity = 0.43 ** (4.0 - 2.3) * mass_solar**2.3
    elif mass_solar <= 2.0:
        relative_luminosity = mass_solar**4.0
    else:
        relative_luminosity = math.sqrt(2.0) * mass_solar**3.5
    # Radius approximation for collision geometry, not detailed stellar structure.
    radius_m = R_SUN_M * mass_solar**0.8
    return radius_m, SOLAR_LUMINOSITY_W * relative_luminosity
