"""Stable protocol facade and concrete P0-M2 REBOUND IAS15 exports."""

from __future__ import annotations

from typing import Any, Protocol

# Concrete M2 types live under physics.nbody; this module remains the stable facade.
from threebody.physics.nbody.guard import ReboundInputGuard
from threebody.physics.nbody.models import IntegrationResult, IntegrationSettings
from threebody.physics.nbody.rebound_ias15 import ReboundIAS15Integrator
from threebody.world.models import WorldInitialState


class NBodyIntegrator(Protocol):
    def integrate(self, initial_state: WorldInitialState, settings: dict[str, Any]) -> Any: ...


class IntegratorInputGuard(Protocol):
    """Future mandatory guard independent from configurable S1 screening."""

    def validate(self, initial_state: WorldInitialState) -> None: ...


__all__ = [
    "IntegrationResult",
    "IntegrationSettings",
    "IntegratorInputGuard",
    "NBodyIntegrator",
    "ReboundIAS15Integrator",
    "ReboundInputGuard",
]

