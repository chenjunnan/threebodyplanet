"""Safe local post-integration M3/M4 pipeline, fully separate from REBOUND.

Only committed completed runs derive M3. Collision runs receive M4 dynamics-only
classification without invented collision aftermath. Derived artifacts are immutable.
"""
from __future__ import annotations

import json
import os
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory

from threebody.common.json import sha256_json
from threebody.errors import ConfigError
from threebody.experiments.longrun_assessment import assess_long_run
from threebody.experiments.longrun_environment import (
    _source_contract,
    derive_long_run_environment,
)
from threebody.physics.longrun.archive import load_run_manifest
from threebody.physics.longrun.reader import SegmentedTrajectoryReader
from threebody.web.outcome_science import read_long_outcome
from threebody.web.thermal_science import read_long_environment


def _quarantine(root: Path, run_id: str, directory: Path, kind: str, raw: dict) -> None:
    """Atomically preserve a recognized archive left by an older same-name run."""
    if directory.is_symlink() or not directory.is_dir():
        raise ConfigError(f"unsafe stale {kind} archive")
    stale_root = root / "stale_derived"
    run_root = stale_root / run_id
    if stale_root.is_symlink() or run_root.is_symlink():
        raise ConfigError("unsafe stale-derived quarantine path")
    run_root.mkdir(parents=True, exist_ok=True)
    destination = run_root / f"{kind}-{sha256_json(raw)[:16]}"
    if destination.exists() or destination.is_symlink():
        raise ConfigError(f"stale {kind} archive was already quarantined")
    os.replace(directory, destination)


def _quarantine_stale_environment(
    root: Path, run_id: str, directory: Path, source_contract: dict,
) -> bool:
    if not directory.exists():
        return False
    manifest_path = directory / "long_environment_manifest.json"
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(raw, dict):
        return False
    if raw.get("kind") != "M5A_DERIVED_ENVIRONMENT":
        return False
    if (
        raw.get("source") == source_contract
        and raw.get("run_id") == source_contract["run_id"]
        and raw.get("world_id") == source_contract["world_id"]
    ):
        return False
    _quarantine(root, run_id, directory, "environment", raw)
    return True


def _quarantine_stale_checkpoints(
    root: Path, run_id: str, directory: Path, source_contract: dict,
) -> bool:
    if not directory.exists():
        return False
    manifest_path = directory / "checkpoint_manifest.json"
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(raw, dict):
        return False
    payload = raw.get("payload")
    if not isinstance(payload, dict) or payload.get("schema_version") != (
        "m6_thermal_checkpoint_v1"
    ):
        return False
    if payload.get("source_contract") == source_contract:
        return False
    _quarantine(root, run_id, directory, "thermal-checkpoints", raw)
    return True


def _quarantine_stale_outcome(
    root: Path, run_id: str, directory: Path, source_contract: dict,
    environment_manifest_sha256: str | None,
) -> bool:
    if not directory.exists():
        return False
    manifest_path = directory / "long_outcome_manifest.json"
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(raw, dict):
        return False
    if raw.get("kind") != "M5A_DERIVED_WORLD_OUTCOME":
        return False
    if (
        raw.get("m5a_source") == source_contract
        and raw.get("run_id") == source_contract["run_id"]
        and raw.get("world_id") == source_contract["world_id"]
        and raw.get("initial_state_sha256")
        == source_contract["initial_state_sha256"]
        and raw.get("environment_manifest_sha256")
        == environment_manifest_sha256
    ):
        return False
    _quarantine(root, run_id, directory, "outcome", raw)
    return True


def derive_run_science(root: Path, run_id: str) -> dict[str, str]:
    run = root / run_id
    manifest = load_run_manifest(run)
    source_contract = _source_contract(SegmentedTrajectoryReader(run))
    status = manifest.task.status
    if status not in {"COMPLETED", "COLLISION"}:
        raise ConfigError("science derivation requires a physical terminal archive")
    env_dir = root / "environments" / run_id
    outcome_dir = root / "outcomes" / run_id
    checkpoint_dir = root / "thermal_checkpoints" / run_id
    result: dict[str, str] = {
        "thermal": "NOT_COMPUTED",
        "outcome": "NOT_COMPUTED",
    }
    with TemporaryDirectory(prefix="threebody-science-") as temp:
        thermal_config = Path(temp) / "thermal.yaml"
        outcome_config = Path(temp) / "outcome.yaml"
        thermal_config.write_text(
            files("threebody.web.static").joinpath("m3_default.yaml").read_text(),
            encoding="utf-8",
        )
        outcome_config.write_text(
            files("threebody.web.static").joinpath("m4_default.yaml").read_text(),
            encoding="utf-8",
        )
        if status == "COMPLETED":
            _quarantine_stale_environment(root, run_id, env_dir, source_contract)
            if env_dir.exists():
                read_long_environment(run, env_dir)
                result["thermal"] = "VERIFIED_EXISTING"
            else:
                try:
                    _quarantine_stale_checkpoints(
                        root, run_id, checkpoint_dir, source_contract
                    )
                    derive_long_run_environment(
                        run, thermal_config, env_dir,
                        checkpoint_dir=checkpoint_dir,
                    )
                    result["thermal"] = "COMPLETED"
                except (ConfigError, ValueError, ArithmeticError, RuntimeError) as exc:
                    # Solver and input-domain failures do not erase the valid M5A
                    # trajectory. M4 still runs in dynamics-only mode, and its
                    # independent reader must verify the original source chain.
                    result["thermal"] = (
                        f"UNAVAILABLE: {type(exc).__name__}: {exc}"
                    )
        else:
            result["thermal"] = "NOT_APPLICABLE_COLLISION"
        validated_env = env_dir if result["thermal"] in {
            "VERIFIED_EXISTING", "COMPLETED"
        } else None
        if validated_env is not None:
            # Coarse temperature estimates can be displayed with their uncertainty,
            # but must not silently drive M4 climate/habitability classifications.
            environment = read_long_environment(run, validated_env)
            if environment["sampling_quality"] == (
                "COARSE_UNVERIFIED_TEMPORAL_VARIABILITY"
            ):
                validated_env = None
        environment_hash = None
        if validated_env is not None:
            environment_hash = sha256_json(json.loads(
                (validated_env / "long_environment_manifest.json").read_text(
                    encoding="utf-8"
                )
            ))
        _quarantine_stale_outcome(
            root, run_id, outcome_dir, source_contract, environment_hash
        )
        if outcome_dir.exists():
            read_long_outcome(run, outcome_dir, validated_env)
            result["outcome"] = "VERIFIED_EXISTING"
        else:
            assess_long_run(
                run, outcome_config, outcome_dir, environment_dir=validated_env
            )
            result["outcome"] = "COMPLETED"
    return result
