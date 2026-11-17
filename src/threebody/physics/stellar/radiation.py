from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

from threebody.errors import ConfigError
from threebody.experiments.orbital_reader import EphemerisSample
from threebody.physics.stellar.models import StarFlux, StellarFluxSnapshot
from threebody.world.models import BodyKind, WorldInitialState


@lru_cache(maxsize=16)
def _legendre_quadrature(order: int) -> tuple[np.ndarray, np.ndarray]:
    return np.polynomial.legendre.leggauss(order)


class StellarRadiation:
    """Three-source isotropic bolometric radiation evaluated from an ephemeris."""

    def __init__(
        self,
        initial_state: WorldInitialState,
        planet_id: str,
        *,
        allow_zero_luminosity_for_tests: bool = False,
    ) -> None:
        bodies = {body.id: body for body in initial_state.bodies}
        if planet_id not in bodies or bodies[planet_id].kind != BodyKind.PLANET:
            raise ConfigError(f"configured planet {planet_id!r} is not the M2 planet")
        self.planet_id = planet_id
        self.planet_radius_m = bodies[planet_id].radius_m
        stars = [body for body in initial_state.bodies if body.kind == BodyKind.STAR]
        if len(stars) != 3:
            raise ConfigError("radiation model requires exactly three stars")
        source_data: list[tuple[str, float, float]] = []
        for star in stars:
            luminosity = star.properties.get("luminosity_w")
            if isinstance(luminosity, bool) or not isinstance(luminosity, (int, float)):
                raise ConfigError(f"star {star.id} is missing numeric luminosity_w")
            luminosity = float(luminosity)
            valid = luminosity >= 0.0 if allow_zero_luminosity_for_tests else luminosity > 0.0
            if not math.isfinite(luminosity) or not valid:
                qualifier = "non-negative test" if allow_zero_luminosity_for_tests else "positive"
                raise ConfigError(f"star {star.id} luminosity_w must be finite and {qualifier}")
            source_data.append((star.id, star.radius_m, luminosity))
        self.sources = tuple(source_data)

    def evaluate(self, ephemeris: EphemerisSample) -> StellarFluxSnapshot:
        states = {body.id: body for body in ephemeris.bodies}
        if len(states) != len(ephemeris.bodies):
            raise ConfigError("ephemeris body IDs must be unique")
        try:
            planet_position = np.asarray(states[self.planet_id].position_m, dtype=np.float64)
        except KeyError as exc:
            raise ConfigError(f"ephemeris is missing planet {self.planet_id}") from exc
        fluxes: list[StarFlux] = []
        total = 0.0
        for star_id, star_radius, luminosity in self.sources:
            try:
                displacement = np.asarray(states[star_id].position_m) - planet_position
            except KeyError as exc:
                raise ConfigError(f"ephemeris is missing star {star_id}") from exc
            with np.errstate(over="ignore", invalid="ignore"):
                distance = float(np.linalg.norm(displacement))
            if not math.isfinite(distance) or distance <= 0.0:
                raise ConfigError(f"star/planet distance for {star_id} is not finite and positive")
            if distance <= star_radius + self.planet_radius_m:
                raise ConfigError(
                    "radiation invalid after physical contact between "
                    f"{star_id} and {self.planet_id}"
                )
            flux = luminosity / (4.0 * math.pi) / distance / distance
            direction = displacement / distance
            if flux < 0.0 or (luminosity > 0.0 and flux == 0.0):
                raise ConfigError(f"radiation calculation for {star_id} underflowed")
            if not math.isfinite(flux) or not np.all(np.isfinite(direction)):
                raise ConfigError(f"radiation calculation for {star_id} is non-finite")
            fluxes.append(
                StarFlux(
                    body_id=star_id,
                    luminosity_w=luminosity,
                    distance_m=distance,
                    direction_inertial_unit=tuple(float(value) for value in direction),
                    flux_w_m2=flux,
                )
            )
            total += flux
        if not math.isfinite(total):
            raise ConfigError("total stellar flux is non-finite")
        return StellarFluxSnapshot(
            t_s=ephemeris.t_s,
            planet_id=self.planet_id,
            stars=tuple(fluxes),
            planet_center_flux_w_m2=total,
            globally_averaged_incoming_w_m2=total / 4.0,
        )


def local_top_of_atmosphere_flux(
    snapshot: StellarFluxSnapshot, normal_inertial_unit: tuple[float, float, float]
) -> float:
    normal = np.asarray(normal_inertial_unit, dtype=np.float64)
    if not np.all(np.isfinite(normal)) or not math.isclose(
        float(np.linalg.norm(normal)), 1.0, abs_tol=1.0e-10
    ):
        raise ConfigError("surface normal must be a finite unit vector")
    incoming = sum(
        star.flux_w_m2
        * max(0.0, float(np.dot(normal, star.direction_inertial_unit)))
        for star in snapshot.stars
    )
    if not math.isfinite(incoming):
        raise ConfigError("local top-of-atmosphere flux is non-finite")
    return incoming


def zonal_mean_top_of_atmosphere_flux(
    snapshot: StellarFluxSnapshot,
    latitude_rad: float,
    axis_inertial_unit: tuple[float, float, float],
) -> float:
    if not math.isfinite(latitude_rad) or not -math.pi / 2 <= latitude_rad <= math.pi / 2:
        raise ConfigError("latitude_rad must be finite and within [-pi/2, pi/2]")
    axis = np.asarray(axis_inertial_unit, dtype=np.float64)
    if not np.all(np.isfinite(axis)) or not math.isclose(
        float(np.linalg.norm(axis)), 1.0, abs_tol=1.0e-10
    ):
        raise ConfigError("rotation axis must be a finite unit vector")
    sin_latitude = math.sin(latitude_rad)
    cos_latitude = math.cos(latitude_rad)
    result = 0.0
    for star in snapshot.stars:
        direction = np.asarray(star.direction_inertial_unit)
        sin_declination = float(np.clip(np.dot(direction, axis), -1.0, 1.0))
        cos_declination = math.sqrt(max(0.0, 1.0 - sin_declination**2))
        a = cos_latitude * cos_declination
        b = sin_latitude * sin_declination
        if a <= 1.0e-15:
            mean_projection = max(0.0, b)
        elif b >= a:
            mean_projection = b
        elif b <= -a:
            mean_projection = 0.0
        else:
            half_day_angle = math.acos(float(np.clip(-b / a, -1.0, 1.0)))
            mean_projection = (
                a * math.sin(half_day_angle) + b * half_day_angle
            ) / math.pi
        result += star.flux_w_m2 * mean_projection
    if not math.isfinite(result):
        raise ConfigError("zonal-mean top-of-atmosphere flux is non-finite")
    return result


def latitude_band_mean_top_of_atmosphere_flux(
    snapshot: StellarFluxSnapshot,
    x_lower: float,
    x_upper: float,
    axis_inertial_unit: tuple[float, float, float],
    *,
    quadrature_order: int = 8,
) -> float:
    """Area-mean forcing over one x=sin(latitude) finite-volume cell."""
    if not -1.0 <= x_lower < x_upper <= 1.0:
        raise ConfigError("latitude-band x bounds must satisfy -1 <= lower < upper <= 1")
    if quadrature_order < 2 or quadrature_order > 64:
        raise ConfigError("latitude-band quadrature order must be between 2 and 64")
    nodes, weights = _legendre_quadrature(quadrature_order)
    midpoint = 0.5 * (x_lower + x_upper)
    half_width = 0.5 * (x_upper - x_lower)
    values = [
        zonal_mean_top_of_atmosphere_flux(
            snapshot,
            math.asin(float(midpoint + half_width * node)),
            axis_inertial_unit,
        )
        for node in nodes
    ]
    return float(np.dot(weights, values) / 2.0)


def latitude_band_means_vectorized(
    snapshot: StellarFluxSnapshot,
    latitude_bands: int,
    axis_inertial_unit: tuple[float, float, float],
    *,
    quadrature_order: int = 8,
) -> np.ndarray:
    """Vectorized 8-point equal-area insolation over all finite-volume bands.

    Exact same zonal day-average and Gauss-Legendre rule as the scalar routine.
    Returns a fresh array, so no shared mutable quadrature cache escapes.
    """
    if latitude_bands < 4 or latitude_bands > 720:
        raise ConfigError("latitude_bands must be within [4, 720]")
    if quadrature_order < 2 or quadrature_order > 64:
        raise ConfigError("quadrature order must be between 2 and 64")
    axis = np.asarray(axis_inertial_unit, dtype=np.float64)
    if axis.shape != (3,) or not np.all(np.isfinite(axis)) or not math.isclose(
        float(np.linalg.norm(axis)), 1.0, abs_tol=1.0e-10
    ):
        raise ConfigError("rotation axis must be a finite unit vector")
    nodes, weights = _legendre_quadrature(quadrature_order)
    dx = 2.0 / latitude_bands
    x_center = -1.0 + (np.arange(latitude_bands) + 0.5) * dx
    x = x_center[:, None] + 0.5 * dx * nodes[None, :]
    sin_lat = x
    cos_lat = np.sqrt(np.maximum(0.0, 1.0 - x*x))
    total = np.zeros_like(x)
    for star in snapshot.stars:
        direction = np.asarray(star.direction_inertial_unit, dtype=np.float64)
        sin_dec = float(np.clip(np.dot(direction, axis), -1.0, 1.0))
        cos_dec = math.sqrt(max(0.0, 1.0 - sin_dec*sin_dec))
        a = cos_lat * cos_dec
        b = sin_lat * sin_dec
        projection = np.maximum(0.0, b)
        daytime = (a > 1.0e-15) & (b < a) & (b > -a)
        day_angle = np.arccos(np.clip(-b / np.maximum(a, 1.0e-15), -1.0, 1.0))
        intermediate = (a * np.sin(day_angle) + b * day_angle) / math.pi
        projection = np.where(daytime, intermediate, projection)
        projection = np.where(b <= -a, 0.0, projection)
        total += star.flux_w_m2 * projection
    result = np.asarray(total @ weights / 2.0, dtype=np.float64)
    if not np.all(np.isfinite(result)) or np.any(result < -1.0e-12):
        raise ConfigError("vectorized latitude radiation is invalid")
    return result
