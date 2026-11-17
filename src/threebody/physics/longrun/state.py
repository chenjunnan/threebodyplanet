from __future__ import annotations

from threebody.errors import ConfigError
from threebody.physics.longrun.models import RunStatus, RunTaskState

TERMINAL_STATES: frozenset[RunStatus] = frozenset(
    {"CANCELLED", "COMPLETED", "COLLISION", "FAILED"}
)
ALLOWED_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    "PENDING": frozenset({"RUNNING", "CANCELLED", "FAILED"}),
    "RUNNING": frozenset(
        {"PAUSING", "CANCELLING", "COMPLETED", "COLLISION", "FAILED"}
    ),
    "PAUSING": frozenset({"PAUSED", "COLLISION", "FAILED"}),
    "PAUSED": frozenset({"RUNNING", "CANCELLED", "FAILED"}),
    "CANCELLING": frozenset({"CANCELLED", "COLLISION", "FAILED"}),
    "CANCELLED": frozenset(),
    "COMPLETED": frozenset(),
    "COLLISION": frozenset(),
    "FAILED": frozenset(),
}


def transition_task(
    current: RunTaskState,
    target: RunStatus,
    *,
    reason_code: str | None = None,
) -> RunTaskState:
    """Apply an audited state transition; repeated commands are idempotent."""

    if current.status == target:
        return current
    if target not in ALLOWED_TRANSITIONS[current.status]:
        raise ConfigError(f"invalid long-run transition {current.status} -> {target}")
    return current.model_copy(
        update={
            "status": target,
            "revision": current.revision + 1,
            "reason_code": reason_code,
        }
    )
