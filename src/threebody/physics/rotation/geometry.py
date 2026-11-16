from __future__ import annotations

import math
from decimal import Decimal, localcontext

import numpy as np

from threebody.errors import ConfigError
from threebody.physics.thermal.models import RotationSettings


class PlanetRotation:
    def __init__(self, settings: RotationSettings) -> None:
        self.settings = settings
        self.axis = np.asarray(settings.axis_inertial_unit, dtype=np.float64)
        self.e1 = np.asarray(settings.prime_meridian_inertial_unit, dtype=np.float64)
        self.e2 = np.cross(self.axis, self.e1)
        if not math.isclose(float(np.linalg.norm(self.e2)), 1.0, abs_tol=1.0e-10):
            raise ConfigError("rotation basis is not right-handed orthonormal")

    def phase_rad(self, t_s: float) -> float:
        if not math.isfinite(t_s):
            raise ConfigError("rotation time must be finite")
        elapsed = t_s - self.settings.rotation_epoch_s
        if not math.isfinite(elapsed):
            raise ConfigError("rotation elapsed time is outside float64 range")
        product = self.settings.rotation_rate_rad_s * elapsed
        if math.isfinite(product) and abs(product) < 1.0e12:
            return math.remainder(self.settings.initial_phase_rad + product, math.tau)
        with localcontext() as context:
            context.prec = 50
            phase = Decimal.from_float(self.settings.initial_phase_rad)
            phase += Decimal.from_float(self.settings.rotation_rate_rad_s) * Decimal.from_float(
                elapsed
            )
            reduced = phase % Decimal.from_float(math.tau)
        value = float(reduced)
        if value > math.pi:
            value -= math.tau
        return value

    def normal(
        self, latitude_rad: float, longitude_rad: float, t_s: float
    ) -> tuple[float, float, float]:
        if not all(math.isfinite(value) for value in (latitude_rad, longitude_rad)):
            raise ConfigError("latitude and longitude must be finite")
        if not -math.pi / 2 <= latitude_rad <= math.pi / 2:
            raise ConfigError("latitude must be within [-pi/2, pi/2]")
        angle = math.remainder(longitude_rad + self.phase_rad(t_s), math.tau)
        normal = math.cos(latitude_rad) * (
            math.cos(angle) * self.e1 + math.sin(angle) * self.e2
        ) + math.sin(latitude_rad) * self.axis
        if not np.all(np.isfinite(normal)) or not math.isclose(
            float(np.linalg.norm(normal)), 1.0, abs_tol=1.0e-10
        ):
            raise ConfigError("rotation produced an invalid surface normal")
        return tuple(float(value) for value in normal)

