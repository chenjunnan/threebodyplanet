from __future__ import annotations

import json
import platform
from pathlib import Path
from typing import Literal

import numpy as np
import scipy
from pydantic import Field, ValidationError

from threebody import __version__
from threebody.common.json import sha256_json, write_json_atomic
from threebody.errors import ConfigError
from threebody.physics.environment.forcing import EnvironmentForcing
from threebody.physics.environment.models import (
    DynamicEnvironmentConfig,
    DynamicEnvironmentState,
    StrictModel,
)


class EnvironmentCheckpoint(StrictModel):
    checkpoint_protocol: Literal["dynamic-environment-checkpoint-v1"]
    schema_version: Literal["0.8.0"]
    model_id: Literal["m72_three_reservoir_v1"]
    checkpoint_index: int = Field(ge=0)
    next_output_index: int = Field(ge=1)
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    forcing_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_checkpoint_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    runtime_versions: dict[str, str]
    integrator_config: dict[str, str | float]
    state: DynamicEnvironmentState
    checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def write_environment_checkpoint(
    path: str | Path, *, state: DynamicEnvironmentState,
    config: DynamicEnvironmentConfig, forcing: EnvironmentForcing,
    checkpoint_index: int, next_output_index: int,
    previous_checkpoint_sha256: str | None = None,
) -> EnvironmentCheckpoint:
    destination = Path(path)
    if destination.exists() or destination.is_symlink():
        raise ConfigError("environment checkpoints are immutable; target already exists")
    payload = {
        "checkpoint_protocol": "dynamic-environment-checkpoint-v1",
        "schema_version": "0.8.0", "model_id": "m72_three_reservoir_v1",
        "checkpoint_index": checkpoint_index, "next_output_index": next_output_index,
        "config_sha256": config.config_sha256,
        "forcing_source_sha256": forcing.source_sha256,
        "previous_checkpoint_sha256": previous_checkpoint_sha256,
        "runtime_versions": {
            "project": __version__, "python": platform.python_version(),
            "numpy": np.__version__, "scipy": scipy.__version__,
        },
        "integrator_config": config.integrator.model_dump(mode="json"),
        "state": state.model_dump(mode="json"),
    }
    payload["checkpoint_sha256"] = sha256_json(payload)
    try:
        checkpoint = EnvironmentCheckpoint.model_validate(payload, strict=True)
    except ValidationError as exc:
        raise ConfigError(f"invalid environment checkpoint: {exc}") from exc
    write_json_atomic(destination, checkpoint)
    return checkpoint


def load_environment_checkpoint(
    path: str | Path, *, config: DynamicEnvironmentConfig,
    forcing: EnvironmentForcing,
    expected_previous_checkpoint_sha256: str | None = None,
) -> EnvironmentCheckpoint:
    source = Path(path)
    if source.is_symlink():
        raise ConfigError("environment checkpoint symlinks are forbidden")
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
        checkpoint = EnvironmentCheckpoint.model_validate(raw, strict=True)
    except (OSError, ValueError, ValidationError) as exc:
        raise ConfigError(f"cannot load environment checkpoint: {exc}") from exc
    expected = checkpoint.model_dump(mode="json", exclude={"checkpoint_sha256"})
    if sha256_json(expected) != checkpoint.checkpoint_sha256:
        raise ConfigError("environment checkpoint digest mismatch")
    if checkpoint.config_sha256 != config.config_sha256:
        raise ConfigError("environment checkpoint config hash mismatch")
    if checkpoint.forcing_source_sha256 != forcing.source_sha256:
        raise ConfigError("environment checkpoint forcing hash mismatch")
    if checkpoint.integrator_config != config.integrator.model_dump(mode="json"):
        raise ConfigError("environment checkpoint integrator config mismatch")
    current_runtime = {
        "project": __version__, "python": platform.python_version(),
        "numpy": np.__version__, "scipy": scipy.__version__,
    }
    if checkpoint.runtime_versions != current_runtime:
        raise ConfigError("environment checkpoint runtime version mismatch")
    if (
        expected_previous_checkpoint_sha256 is not None
        and checkpoint.previous_checkpoint_sha256
        != expected_previous_checkpoint_sha256
    ):
        raise ConfigError("environment checkpoint chain mismatch")
    return checkpoint
