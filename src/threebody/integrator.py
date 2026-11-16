"""M2 boundary only: no numerical integrator is implemented in P0-M0/M1."""

from __future__ import annotations

from typing import Any, Protocol

from threebody.world.models import WorldInitialState


class NBodyIntegrator(Protocol):
    def integrate(self, initial_state: WorldInitialState, settings: dict[str, Any]) -> Any: ...


class IntegratorInputGuard(Protocol):
    """Future mandatory guard independent from configurable S1 screening."""

    def validate(self, initial_state: WorldInitialState) -> None: ...

