from __future__ import annotations

from threebody.common.constants import CONSTANT_SET_VERSION
from threebody.common.json import sha256_json
from threebody.physics.longrun.models import ContractHashes, LongRunSettings


def compute_contract_hashes(
    settings: LongRunSettings,
    *,
    initial_state_sha256: str,
    body_ids: tuple[str, str, str, str],
) -> ContractHashes:
    physics = {
        "initial_state_sha256": initial_state_sha256,
        "body_ids": body_ids,
        "constant_set_version": CONSTANT_SET_VERSION,
        "backend": settings.backend,
        "epsilon": settings.epsilon,
        "collision": settings.collision,
        "encounter": settings.encounter,
        "escape": settings.escape,
        "require_positive_radius": settings.require_positive_radius,
        "softening_m": settings.softening_m,
    }
    schedule = {
        "time": settings.time,
        "segmentation": settings.segmentation,
        "checkpoint_simulation_interval_s": (
            settings.checkpoint.simulation_interval_s
        ),
    }
    execution = {
        "checkpoint_wall_interval_s": settings.checkpoint.wall_interval_s,
        "checkpoint_retain_count": settings.checkpoint.retain_count,
        "resources": settings.resources,
        "resume": settings.resume,
    }
    return ContractHashes(
        physics_sha256=sha256_json(physics),
        schedule_sha256=sha256_json(schedule),
        execution_policy_sha256=sha256_json(execution),
    )
