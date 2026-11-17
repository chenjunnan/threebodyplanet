from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from threebody.common.json import canonical_json_bytes, sha256_json
from threebody.errors import ConfigError
from threebody.physics.outcomes.models import C0Point, C1Point, FluxPoint
from threebody.screening.s0 import validate_s0
from threebody.visualization.reader import TrajectoryArchive, load_m2_archive
from threebody.world.models import WorldInitialState

Point = TypeVar("Point", bound=BaseModel)


@dataclass(frozen=True)
class M4Inputs:
    initial_path: Path
    initial_state: WorldInitialState
    initial_state_sha256: str
    m2_archive: TrajectoryArchive | None
    m2_manifest_sha256: str | None
    m3_dir: Path | None
    m3_manifest: dict[str, Any] | None
    m3_manifest_sha256: str | None
    flux: tuple[FluxPoint, ...]
    c0: tuple[C0Point, ...]
    c1: tuple[C1Point, ...]
    energy_budget: dict[str, Any] | None
    initial_outcomes: tuple[dict[str, Any], ...]


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value} is forbidden")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except OSError as exc:
        raise ConfigError(f"cannot read M4 input {path}: {exc}") from exc
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ConfigError(f"invalid JSON in M4 input {path}: {exc}") from exc


def _read_jsonl(path: Path, model: type[Point]) -> tuple[Point, ...]:
    values: list[Point] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    raise ConfigError(f"blank line {line_number} in {path}")
                try:
                    values.append(model.model_validate_json(line, strict=True))
                except (ValidationError, ValueError) as exc:
                    raise ConfigError(
                        f"invalid {model.__name__} at {path}:{line_number}: {exc}"
                    ) from exc
    except OSError as exc:
        raise ConfigError(f"cannot read M4 series {path}: {exc}") from exc
    previous = -math.inf
    for value in values:
        if value.t_s <= previous:
            raise ConfigError(f"times in {path} must be strictly increasing")
        previous = value.t_s
    return tuple(values)


def _load_initial(path: Path) -> tuple[WorldInitialState, str]:
    try:
        state = WorldInitialState.model_validate_json(
            path.read_text(encoding="utf-8"), strict=True
        )
    except OSError as exc:
        raise ConfigError(f"cannot read M4 initial state {path}: {exc}") from exc
    except ValidationError as exc:
        raise ConfigError(f"invalid M4 initial state {path}: {exc}") from exc
    s0_errors = validate_s0(state)
    if s0_errors:
        raise ConfigError("M4 initial state failed S0: " + "; ".join(s0_errors))
    return state, sha256_json(state)


def _load_initial_outcomes(
    path: Path | None, state: WorldInitialState
) -> tuple[dict[str, Any], ...]:
    if path is None:
        return ()
    raw = _read_json(path)
    if not isinstance(raw, list):
        raise ConfigError("initial outcome root must be an array")
    outcomes: list[dict[str, Any]] = []
    for index, value in enumerate(raw):
        if not isinstance(value, dict):
            raise ConfigError(f"initial outcome {index} must be an object")
        required = {
            "world_id",
            "status",
            "event_type",
            "t_s",
            "terminal",
            "integration_performed",
            "body_ids",
        }
        if not required <= set(value):
            raise ConfigError(f"initial outcome {index} lacks required fields")
        if value["world_id"] != state.world_id or value["t_s"] != state.reference_epoch_s:
            raise ConfigError("initial outcome world/time does not match initial state")
        if (
            value["status"] != "INITIAL_CONTACT"
            or value["event_type"] != "INITIAL_COLLISION"
            or value["terminal"] is not True
            or value["integration_performed"] is not False
        ):
            raise ConfigError("initial outcome has incompatible terminal semantics")
        body_ids = value["body_ids"]
        if not isinstance(body_ids, list) or not set(body_ids) <= {
            body.id for body in state.bodies
        }:
            raise ConfigError("initial outcome contains invalid body IDs")
        canonical_json_bytes(value)
        outcomes.append(value)
    return tuple(outcomes)


def load_m4_inputs(
    initial_path: str | Path,
    m2_dir: str | Path | None,
    m3_dir: str | Path | None,
    initial_outcome_path: str | Path | None,
) -> M4Inputs:
    initial_source = Path(initial_path)
    state, initial_hash = _load_initial(initial_source)
    initial_outcomes = _load_initial_outcomes(
        None if initial_outcome_path is None else Path(initial_outcome_path), state
    )
    if m2_dir is None:
        if not initial_outcomes:
            raise ConfigError("M4 requires an M2 archive unless initial contact is provided")
        m2 = None
        m2_hash = None
    else:
        m2 = load_m2_archive(m2_dir, max_samples=1_000_000)
        if m2.manifest.world_id != state.world_id:
            raise ConfigError("M2 world_id does not match M4 initial state")
        if m2.manifest.initial_state_sha256 != initial_hash:
            raise ConfigError("M2 initial-state hash does not match M4 input")
        m2_hash = sha256_json(m2.manifest)

    if m3_dir is None:
        return M4Inputs(
            initial_source,
            state,
            initial_hash,
            m2,
            m2_hash,
            None,
            None,
            None,
            (),
            (),
            (),
            None,
            initial_outcomes,
        )
    thermal_dir = Path(m3_dir)
    manifest = _read_json(thermal_dir / "thermal_manifest.json")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "0.3.0":
        raise ConfigError("M3 manifest must be a version 0.3.0 object")
    if manifest.get("world_id") != state.world_id:
        raise ConfigError("M3 world_id does not match M4 initial state")
    if manifest.get("initial_state_sha256") != initial_hash:
        raise ConfigError("M3 initial-state hash does not match M4 input")
    if m2 is None or manifest.get("m2_manifest_sha256") != m2_hash:
        raise ConfigError("M3 provenance does not match the supplied M2 archive")
    flux = _read_jsonl(thermal_dir / "stellar_flux.jsonl", FluxPoint)
    c0 = _read_jsonl(thermal_dir / "c0_temperature.jsonl", C0Point)
    c1 = _read_jsonl(thermal_dir / "c1_latitude_temperature.jsonl", C1Point)
    status = manifest.get("status")
    if status not in {"COMPLETED", "FAILED"}:
        raise ConfigError("M3 manifest status is unsupported")
    if status == "COMPLETED":
        if len(flux) != manifest.get("sample_count") or not flux:
            raise ConfigError("M3 flux count does not match completed manifest")
        if (
            flux[0].t_s != manifest.get("t_start_s")
            or flux[-1].t_s != manifest.get("t_final_s")
        ):
            raise ConfigError("M3 flux times do not match thermal manifest coverage")
        for series in (flux, c0, c1):
            if series and (
                series[0].t_s < m2.samples[0].t_s
                or series[-1].t_s > m2.samples[-1].t_s
            ):
                raise ConfigError("M3 series extends outside the M2 observation window")
        for name, series in (("C0", c0), ("C1", c1)):
            if series and tuple(point.t_s for point in series) != tuple(
                point.t_s for point in flux
            ):
                raise ConfigError(f"M3 {name} times do not align with radiation samples")
        for point in c1:
            lengths = {
                len(point.latitude_center_rad),
                len(point.temperature_k),
                len(point.zonal_incoming_w_m2),
                len(point.absorbed_w_m2),
                len(point.emitted_w_m2),
                len(point.meridional_convergence_w_m2),
            }
            if len(lengths) != 1 or next(iter(lengths)) < 4:
                raise ConfigError("M3 C1 vectors must have one consistent band count >= 4")
    elif flux or c0 or c1:
        raise ConfigError("failed M3 archive must not contain completed series")
    energy = _read_json(thermal_dir / "energy_budget.json")
    if not isinstance(energy, dict):
        raise ConfigError("M3 energy budget must be an object")
    canonical_json_bytes(energy)
    return M4Inputs(
        initial_source,
        state,
        initial_hash,
        m2,
        m2_hash,
        thermal_dir,
        manifest,
        sha256_json(manifest),
        flux,
        c0,
        c1,
        energy,
        initial_outcomes,
    )
