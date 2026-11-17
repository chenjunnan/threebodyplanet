"""Local reproducible G2/G2.1 candidate gallery and bounded M5A run presets.

Worldsets are immutable. The user selects a candidate ID, never a filesystem path
or client-supplied celestial coordinates, to launch a real IAS15 run.
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

from threebody.common.json import sha256_json, write_json_atomic
from threebody.config.loader import resolve_mapping
from threebody.errors import ConfigError
from threebody.physics.longrun.models import LongRunSettings
from threebody.screening.s0 import validate_s0
from threebody.world.generators import generate_initial_state_with_metadata
from threebody.world.models import WorldInitialState

IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
YEAR_S = 31557600.0
MAX_WORLDS = 20


def _directory(root: Path, batch_id: str) -> Path:
    if not isinstance(batch_id, str) or not IDENTIFIER.fullmatch(batch_id):
        raise ValueError("invalid worldset ID")
    container = root / "worldsets"
    directory = container / batch_id
    if container.is_symlink() or directory.is_symlink() or not directory.is_dir():
        raise FileNotFoundError(batch_id)
    if directory.resolve().parent != container.resolve():
        raise ValueError("worldset escapes artifact root")
    return directory


def _state_path(root: Path, batch_id: str, index: int) -> Path:
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < MAX_WORLDS:
        raise ValueError("invalid candidate index")
    return _directory(root, batch_id) / f"{index:03d}.initial.json"


def load_candidate(root: Path, batch_id: str, index: int) -> WorldInitialState:
    path = _state_path(root, batch_id, index)
    if path.is_symlink():
        raise ConfigError("candidate symlink is forbidden")
    state = WorldInitialState.model_validate_json(path.read_text(encoding="utf-8"), strict=True)
    manifest = json.loads((_directory(root, batch_id) / "manifest.json").read_text())
    if sha256_json(state) != manifest["candidates"][index]["initial_sha256"]:
        raise ConfigError("worldset initial-state hash mismatch")
    if validate_s0(state):
        raise ConfigError("candidate fails strict S0 physical validation")
    if manifest["candidates"][index]["initial_contact"]:
        raise ConfigError("initial-contact world is terminal and cannot start IAS15")
    return state


def list_worldsets(root: Path) -> list[dict[str, Any]]:
    container = root / "worldsets"
    if not container.is_dir() or container.is_symlink():
        return []
    batches = []
    for child in sorted(container.iterdir()):
        if child.is_symlink() or not child.is_dir() or not IDENTIFIER.fullmatch(child.name):
            continue
        manifest_path = child / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            continue
        batches.append(json.loads(manifest_path.read_text(encoding="utf-8")))
    return batches


def create_worldset(root: Path, config_mapping: dict, count: int) -> dict:
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= MAX_WORLDS:
        raise ValueError(f"candidate count must be 1..{MAX_WORLDS}")
    if not isinstance(config_mapping, dict):
        raise ValueError("config must be a JSON object")
    resolved, config_hash = resolve_mapping(config_mapping)
    if count > resolved.output.max_candidates:
        raise ConfigError("candidate count exceeds experiment output.max_candidates")
    generator = resolved.world_generation.generator
    container = root / "worldsets"
    if container.is_symlink():
        raise ConfigError("worldset container cannot be a symlink")
    container.mkdir(exist_ok=True)
    batch_id = "worldset-" + uuid.uuid4().hex[:16]
    destination = container / batch_id
    temporary = Path(tempfile.mkdtemp(prefix=".worldset-temp-", dir=container))
    try:
        candidates = []
        for index in range(count):
            try:
                state, metadata, contacts = generate_initial_state_with_metadata(
                    generator, resolved.experiment.seed, index, config_hash,
                )
                issues = validate_s0(state)
                contact = bool(contacts)
                write_json_atomic(temporary / f"{index:03d}.initial.json", state)
                candidate = {
                    "index": index, "world_id": state.world_id,
                    "initial_sha256": sha256_json(state),
                    "initial_contact": contact,
                    "s0_issues": list(issues),
                    "can_integrate": not contact and not issues,
                    "bodies": [{
                        "id": body.id, "mass_kg": body.mass_kg,
                        "position_m": list(body.position_m),
                        "velocity_mps": list(body.velocity_mps),
                    } for body in state.bodies],
                }
                if metadata is not None:
                    candidate["generator_metadata"] = (\n                        metadata.model_dump(mode="json")\n                        if hasattr(metadata, "model_dump")\n                        else __import__("dataclasses").asdict(metadata)\n                    )
            except (ValueError, ArithmeticError, ConfigError) as exc:
                candidate = {
                    "index": index, "world_id": f"candidate-{index:06d}",
                    "can_integrate": False, "generation_error": str(exc),
                    "initial_contact": False, "s0_issues": [],
                    "initial_sha256": None,
                }
            candidates.append(candidate)
        result = {
            "schema_version": "web_worldset_v1",
            "batch_id": batch_id,
            "seed": resolved.experiment.seed,
            "generator_type": generator.type,
            "generator_version": generator.version,
            "config_sha256": config_hash,
            "count": count,
            "candidates": candidates,
            "scope": "initial conditions only; no survival or habitability claim",
        }
        write_json_atomic(temporary / "manifest.json", result)
        os.replace(temporary, destination)
        return result
    except Exception:
        import shutil
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def simulation_settings(duration_s: float, output_interval_s: float) -> LongRunSettings:
    if not isinstance(duration_s, (int, float)) or not math.isfinite(duration_s):
        raise ValueError("duration_s must be finite")
    if not 60 <= duration_s <= 10 * YEAR_S:
        raise ValueError("duration_s must be from 60 seconds to 10 Julian years")
    if not isinstance(output_interval_s, (int, float)) or not math.isfinite(output_interval_s):
        raise ValueError("output_interval_s must be finite")
    if not 1 <= output_interval_s <= duration_s:
        raise ValueError("output_interval_s must be within simulation duration")
    if math.ceil(duration_s / output_interval_s) + 1 > 20000:
        raise ValueError("too many trajectory samples; increase output interval")
    segment = min(duration_s, 30 * 86400.0)
    settings = {
        "schema_version": "0.6.0",
        "backend": "rebound_ias15",
        "time": {
            "start_time_s": 0.0, "end_time_s": float(duration_s),
            "output_interval_s": float(output_interval_s),
        },
        "segmentation": {"simulated_duration_s": segment},
        "checkpoint": {
            "simulation_interval_s": segment, "wall_interval_s": None,
            "retain_count": 3,
        },
        "resources": {
            "max_wall_seconds_per_invocation": 300.0,
            "max_segments": 200, "max_samples": 20000,
            "max_checkpoint_bytes": 1073741824,
        },
        "resume": {
            "require_same_rebound_version": True,
            "require_same_platform": True, "verify_all_committed_hashes": True,
        },
        "epsilon": 1.0e-9, "collision": {"mode": "terminate"},
        "encounter": {
            "enabled": True, "threshold_mode": "radius_multiple",
            "radius_multiplier": 5.0,
        },
        "escape": {
            "enabled": True, "boundary_radius_m": 1.0e14,
            "require_outward_velocity": True,
            "require_positive_specific_energy": True,
            "minimum_duration_s": 86400.0,
        },
        "require_positive_radius": False, "softening_m": 0.0,
    }
    return LongRunSettings.model_validate(settings)
