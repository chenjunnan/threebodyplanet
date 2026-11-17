from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from threebody.common.json import sha256_json
from threebody.errors import VisualizationError
from threebody.visualization.reader import TrajectoryArchive, load_m2_archive


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class StarFluxDisplay(StrictModel):
    body_id: str
    luminosity_w: float = Field(gt=0.0)
    distance_m: float = Field(gt=0.0)
    direction_inertial_unit: tuple[float, float, float]
    flux_w_m2: float = Field(ge=0.0)


class FluxDisplay(StrictModel):
    t_s: float
    planet_id: str
    stars: tuple[StarFluxDisplay, StarFluxDisplay, StarFluxDisplay]
    planet_center_flux_w_m2: float = Field(ge=0.0)
    globally_averaged_incoming_w_m2: float = Field(ge=0.0)
    model: str
    occultation: str
    reference_equator_prime_meridian_normal: tuple[float, float, float]
    reference_equator_prime_meridian_toa_w_m2: float = Field(ge=0.0)


class C0Display(StrictModel):
    t_s: float
    temperature_k: float = Field(gt=0.0)
    planet_center_flux_w_m2: float = Field(ge=0.0)
    globally_averaged_incoming_w_m2: float = Field(ge=0.0)
    absorbed_w_m2: float
    internal_heat_flux_w_m2: float
    emitted_w_m2: float
    net_heating_w_m2: float


class C1Display(StrictModel):
    t_s: float
    latitude_center_rad: tuple[float, ...]
    temperature_k: tuple[float, ...]
    zonal_incoming_w_m2: tuple[float, ...]
    absorbed_w_m2: tuple[float, ...]
    emitted_w_m2: tuple[float, ...]
    meridional_convergence_w_m2: tuple[float, ...]
    global_mean_temperature_k: float
    globally_averaged_incoming_w_m2: float

    @model_validator(mode="after")
    def consistent_bands(self) -> C1Display:
        lengths = {
            len(self.latitude_center_rad),
            len(self.temperature_k),
            len(self.zonal_incoming_w_m2),
            len(self.absorbed_w_m2),
            len(self.emitted_w_m2),
            len(self.meridional_convergence_w_m2),
        }
        if len(lengths) != 1 or next(iter(lengths)) < 4:
            raise ValueError("C1 arrays require one consistent band count >= 4")
        return self


@dataclass(frozen=True)
class DashboardEvent:
    event_id: str
    event_type: str
    t_s: float
    body_ids: tuple[str, ...]
    source: str
    message: str
    detection_method: str
    terminal: bool


@dataclass(frozen=True)
class DashboardArchive:
    m2: TrajectoryArchive
    m3_dir: Path
    m3_manifest: dict[str, Any]
    m3_manifest_sha256: str
    flux: tuple[FluxDisplay, ...]
    c0: tuple[C0Display, ...]
    c1: tuple[C1Display, ...]
    energy_budget: dict[str, Any]
    m4_manifest: dict[str, Any] | None
    m4_classification: dict[str, Any] | None
    events: tuple[DashboardEvent, ...]
    t_start_s: float
    t_final_s: float


Point = TypeVar("Point", bound=BaseModel)


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value} is forbidden")


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except OSError as exc:
        raise VisualizationError(f"cannot read dashboard input {path}: {exc}") from exc
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise VisualizationError(f"invalid dashboard JSON {path}: {exc}") from exc


def _jsonl(path: Path, model: type[Point], maximum: int) -> tuple[Point, ...]:
    values: list[Point] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    raise VisualizationError(f"blank dashboard input line {path}:{line_number}")
                if len(values) >= maximum:
                    raise VisualizationError(f"{path} exceeds max_input_samples={maximum}")
                try:
                    values.append(model.model_validate_json(line, strict=True))
                except (ValidationError, ValueError) as exc:
                    raise VisualizationError(
                        f"invalid {model.__name__} at {path}:{line_number}: {exc}"
                    ) from exc
    except OSError as exc:
        raise VisualizationError(f"cannot read dashboard series {path}: {exc}") from exc
    previous = -math.inf
    for value in values:
        if value.t_s <= previous:
            raise VisualizationError(f"dashboard series times must increase in {path}")
        previous = value.t_s
    return tuple(values)


def _m2_events(archive: TrajectoryArchive) -> list[DashboardEvent]:
    return [
        DashboardEvent(
            event_id=f"m2-{index:06d}",
            event_type=event.event_type,
            t_s=event.t_s,
            body_ids=event.body_ids,
            source="M2",
            message=event.message,
            detection_method=event.detection_method,
            terminal=event.event_type == "COLLISION",
        )
        for index, event in enumerate(archive.events, 1)
    ]


def _load_m4(
    directory: Path | None,
    world_id: str,
    m2_hash: str,
    m3_hash: str,
    start: float,
    end: float,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[DashboardEvent]]:
    if directory is None:
        return None, None, []
    manifest = _json(directory / "world_outcome_manifest.json")
    classification = _json(directory / "world_classification.json")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "0.4.0":
        raise VisualizationError("M4 manifest must be a version 0.4.0 object")
    if manifest.get("world_id") != world_id:
        raise VisualizationError("M4 world_id does not match M2/M3 dashboard inputs")
    hashes = manifest.get("source_hashes")
    if not isinstance(hashes, dict) or hashes.get("m2_manifest_sha256") != m2_hash:
        raise VisualizationError("M4 M2 provenance hash does not match dashboard input")
    if hashes.get("m3_manifest_sha256") != m3_hash:
        raise VisualizationError("M4 M3 provenance hash does not match dashboard input")
    events: list[DashboardEvent] = []
    path = directory / "world_timeline.jsonl"
    try:
        with path.open(encoding="utf-8") as handle:
            for index, line in enumerate(handle, 1):
                raw = json.loads(line, parse_constant=_reject_constant)
                time = float(raw["t_start_s"])
                if time < start or time > end:
                    continue
                events.append(
                    DashboardEvent(
                        event_id=str(raw.get("event_id", f"m4-{index:06d}")),
                        event_type=str(raw["event_type"]),
                        t_s=time,
                        body_ids=tuple(raw.get("body_ids", ())),
                        source="M4",
                        message=str(raw.get("message", "")),
                        detection_method=str(raw.get("detection_method", "unknown")),
                        terminal=bool(raw.get("terminal", False)),
                    )
                )
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise VisualizationError(f"invalid M4 timeline {path}: {exc}") from exc
    return manifest, classification, events


def load_dashboard_archive(
    m2_dir: str | Path,
    m3_dir: str | Path,
    m4_dir: str | Path | None = None,
    *,
    max_input_samples: int = 100_000,
) -> DashboardArchive:
    if max_input_samples < 2 or max_input_samples > 1_000_000:
        raise VisualizationError("max_input_samples must be within [2, 1000000]")
    m2 = load_m2_archive(m2_dir, max_samples=max_input_samples)
    thermal_dir = Path(m3_dir)
    manifest = _json(thermal_dir / "thermal_manifest.json")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "0.3.0":
        raise VisualizationError("M3 manifest must be a version 0.3.0 object")
    if manifest.get("status") != "COMPLETED":
        raise VisualizationError("VIS2 requires a completed M3 archive")
    if manifest.get("world_id") != m2.manifest.world_id:
        raise VisualizationError("M2 and M3 world_id values do not match")
    m2_hash = sha256_json(m2.manifest)
    if manifest.get("m2_manifest_sha256") != m2_hash:
        raise VisualizationError("M3 m2_manifest_sha256 does not match supplied M2 archive")
    if manifest.get("initial_state_sha256") != m2.manifest.initial_state_sha256:
        raise VisualizationError("M2 and M3 initial-state hashes do not match")
    flux = _jsonl(thermal_dir / "stellar_flux.jsonl", FluxDisplay, max_input_samples)
    c0 = _jsonl(thermal_dir / "c0_temperature.jsonl", C0Display, max_input_samples)
    c1 = _jsonl(
        thermal_dir / "c1_latitude_temperature.jsonl", C1Display, max_input_samples
    )
    if len(flux) < 2:
        raise VisualizationError("VIS2 requires at least two M3 stellar flux samples")
    if len(flux) != manifest.get("sample_count"):
        raise VisualizationError("M3 flux count does not match manifest")
    if flux[0].t_s != manifest.get("t_start_s") or flux[-1].t_s != manifest.get("t_final_s"):
        raise VisualizationError("M3 flux times do not match manifest coverage")
    for name, series in (("C0", c0), ("C1", c1)):
        if series and tuple(point.t_s for point in series) != tuple(
            point.t_s for point in flux
        ):
            raise VisualizationError(f"M3 {name} times do not align with flux samples")
    start = max(m2.samples[0].t_s, flux[0].t_s)
    end = min(m2.samples[-1].t_s, flux[-1].t_s)
    if end < start:
        raise VisualizationError("M2 and M3 archives have no overlapping time interval")
    energy = _json(thermal_dir / "energy_budget.json")
    if not isinstance(energy, dict):
        raise VisualizationError("M3 energy budget must be an object")
    m3_hash = sha256_json(manifest)
    m4_manifest, classification, m4_events = _load_m4(
        None if m4_dir is None else Path(m4_dir),
        m2.manifest.world_id,
        m2_hash,
        m3_hash,
        start,
        end,
    )
    events = [event for event in _m2_events(m2) if start <= event.t_s <= end]
    events.extend(m4_events)
    events.sort(key=lambda event: (event.t_s, event.event_id))
    return DashboardArchive(
        m2=m2,
        m3_dir=thermal_dir,
        m3_manifest=manifest,
        m3_manifest_sha256=m3_hash,
        flux=flux,
        c0=c0,
        c1=c1,
        energy_budget=energy,
        m4_manifest=m4_manifest,
        m4_classification=classification,
        events=tuple(events),
        t_start_s=start,
        t_final_s=end,
    )
