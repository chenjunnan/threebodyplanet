from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import resource
import shutil
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from threebody import __version__
from threebody.common.json import canonical_json_bytes, sha256_json, write_json_atomic
from threebody.errors import ConfigError
from threebody.physics.environment.integrated_models import (
    EnvironmentEvent,
    IntegratedEnvironmentConfig,
    IntegratedSnapshot,
)
from threebody.physics.environment.integrated_solver import (
    IntegratedEnvironmentEngine,
    IntegratedState,
)
from threebody.physics.environment.spatial_forcing import M5ASpatialForcing


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda value: (
            (_ for _ in ()).throw(ValueError(f"non-finite {value}"))
        ))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read integrated environment artifact {path}: {exc}") from exc


def _write_checkpoint(
    path: Path, *, state: IntegratedState, config: IntegratedEnvironmentConfig,
    forcing: M5ASpatialForcing, previous_sha256: str | None,
) -> str:
    payload = {
        "protocol": "integrated-environment-checkpoint-v1",
        "schema_version": "1.0.0", "model_id": "integrated_environment_v1",
        "run_id": forcing.run_id, "world_id": forcing.world_id,
        "config_sha256": config.config_sha256,
        "forcing_source_sha256": forcing.source_sha256,
        "previous_checkpoint_sha256": previous_sha256,
        "state": state.to_dict(),
        "runtime": {"project": __version__, "python": platform.python_version()},
    }
    digest = sha256_json(payload)
    write_json_atomic(path, {**payload, "checkpoint_sha256": digest})
    return digest


def _load_checkpoint(
    path: Path, *, config: IntegratedEnvironmentConfig,
    forcing: M5ASpatialForcing, previous_sha256: str | None,
    cell_count: int,
) -> tuple[IntegratedState, str]:
    if path.is_symlink():
        raise ConfigError("integrated environment checkpoint symlinks are forbidden")
    raw = _read_json(path)
    digest = raw.pop("checkpoint_sha256", None)
    if not isinstance(digest, str) or sha256_json(raw) != digest:
        raise ConfigError("integrated environment checkpoint digest mismatch")
    expected = {
        "protocol": "integrated-environment-checkpoint-v1",
        "schema_version": "1.0.0", "model_id": "integrated_environment_v1",
        "run_id": forcing.run_id, "world_id": forcing.world_id,
        "config_sha256": config.config_sha256,
        "forcing_source_sha256": forcing.source_sha256,
        "previous_checkpoint_sha256": previous_sha256,
    }
    for name, value in expected.items():
        if raw.get(name) != value:
            raise ConfigError(f"integrated checkpoint {name} mismatch")
    runtime = raw.get("runtime", {})
    if runtime.get("project") != __version__ or runtime.get("python") != platform.python_version():
        raise ConfigError("integrated checkpoint runtime version mismatch")
    return IntegratedState.from_dict(raw["state"], cell_count), digest


def _output_times(start: float, end: float, interval: float) -> list[float]:
    count = int(math.floor((end - start) / interval + 1.0e-12))
    times = [start + index * interval for index in range(count + 1)]
    if not math.isclose(times[-1], end, rel_tol=0.0, abs_tol=1.0e-9):
        times.append(end)
    return times


def run_integrated_environment_archive(
    m5a_run_dir: str | Path,
    destination: str | Path,
    config: IntegratedEnvironmentConfig,
    *,
    end_t_s: float | None = None,
    max_segments: int | None = None,
) -> dict[str, Any]:
    """Create or safely resume an immutable-segment integrated environment archive."""
    source = Path(m5a_run_dir)
    target = Path(destination)
    forcing = M5ASpatialForcing(source, config)
    end = forcing.coverage_s[1] if end_t_s is None else end_t_s
    if not forcing.coverage_s[0] < end <= forcing.coverage_s[1]:
        raise ConfigError("environment archive end time outside forcing coverage")
    times = _output_times(forcing.coverage_s[0], end, config.output_interval_s)
    if max_segments is not None and max_segments < 1:
        raise ConfigError("max_segments must be positive")
    engine = IntegratedEnvironmentEngine(config, forcing)
    created = not target.exists()
    if created:
        target.mkdir(parents=True)
        (target / "segments").mkdir()
        write_json_atomic(target / "resolved_config.json", config)
        write_json_atomic(target / "forcing_source.json", forcing.source_contract)
        manifest = {
            "schema_version": "1.0.0", "model_id": "integrated_environment_v1",
            "run_id": forcing.run_id, "world_id": forcing.world_id,
            "status": "RUNNING", "config_sha256": config.config_sha256,
            "forcing_source_sha256": forcing.source_sha256,
            "coverage_s": [times[0], times[-1]], "fidelity_mode": config.fidelity_mode,
            "grid": [config.latitude_count, config.longitude_count],
            "segments": [], "result_sha256": None, "performance": None,
            "performance_accumulator": {
                "wall_seconds": 0.0, "cpu_seconds": 0.0, "peak_rss_mib": 0.0,
            },
        }
        write_json_atomic(target / "integrated_environment_manifest.json", manifest)
        state = engine.initialize(times[0])
        next_output_index = 0
        previous_checkpoint = None
    else:
        if target.is_symlink() or not target.is_dir():
            raise ConfigError("unsafe integrated environment destination")
        reader = IntegratedEnvironmentArchiveReader(target, source)
        if reader.config != config:
            raise ConfigError("integrated environment resume config mismatch")
        manifest = reader.manifest
        if manifest["status"] == "COMPLETED":
            raise ConfigError("integrated environment archive is already complete")
        if tuple(manifest["coverage_s"]) != (times[0], times[-1]):
            raise ConfigError("integrated environment resume coverage mismatch")
        last = manifest["segments"][-1]
        checkpoint_path = target / "segments" / f"{last['segment_index']:08d}" / "checkpoint.json"
        previous_before_last = (
            None if len(manifest["segments"]) == 1
            else manifest["segments"][-2]["checkpoint_sha256"]
        )
        state, checkpoint_sha = _load_checkpoint(
            checkpoint_path, config=config, forcing=forcing,
            previous_sha256=previous_before_last, cell_count=engine.cell_count,
        )
        if checkpoint_sha != last["checkpoint_sha256"]:
            raise ConfigError("integrated checkpoint/manifest hash mismatch")
        next_output_index = last["next_output_index"]
        previous_checkpoint = checkpoint_sha
    start_wall = time.perf_counter()
    start_cpu = time.process_time()
    segments_this_call = 0
    while next_output_index < len(times):
        if max_segments is not None and segments_this_call >= max_segments:
            break
        segment_index = len(manifest["segments"])
        stop = min(next_output_index + config.segment_output_count, len(times))
        segment_times = times[next_output_index:stop]
        snapshots = []
        for output_time in segment_times:
            if output_time > state.t_s:
                engine.advance(state, output_time)
            elif not math.isclose(output_time, state.t_s, rel_tol=0.0, abs_tol=1.0e-9):
                raise ConfigError("environment segment output time precedes checkpoint")
            snapshots.append(engine.snapshot(state))
        stage = Path(tempfile.mkdtemp(prefix=f".segment-{segment_index:08d}-", dir=target))
        try:
            snapshots_path = stage / "snapshots.jsonl"
            with snapshots_path.open("w", encoding="utf-8") as handle:
                for snapshot in snapshots:
                    handle.write(canonical_json_bytes(snapshot).decode("utf-8") + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            write_json_atomic(stage / "events.json", state.events)
            checkpoint_sha = _write_checkpoint(
                stage / "checkpoint.json", state=state, config=config,
                forcing=forcing, previous_sha256=previous_checkpoint,
            )
            files = {
                name: _sha_file(stage / name)
                for name in ("snapshots.jsonl", "events.json", "checkpoint.json")
            }
            segment_payload = {
                "segment_index": segment_index,
                "t_start_s": segment_times[0], "t_final_s": segment_times[-1],
                "output_count": len(segment_times), "next_output_index": stop,
                "files": files, "checkpoint_sha256": checkpoint_sha,
                "previous_checkpoint_sha256": previous_checkpoint,
            }
            write_json_atomic(stage / "segment_manifest.json", segment_payload)
            final = target / "segments" / f"{segment_index:08d}"
            if final.exists():
                raise ConfigError("integrated environment segment target already exists")
            os.replace(stage, final)
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise
        manifest["segments"].append(segment_payload)
        previous_checkpoint = checkpoint_sha
        next_output_index = stop
        segments_this_call += 1
        forcing.verify_unchanged()
        manifest["status"] = "RUNNING"
        write_json_atomic(target / "integrated_environment_manifest.json", manifest)
    elapsed_wall = time.perf_counter() - start_wall
    elapsed_cpu = time.process_time() - start_cpu
    accumulator = manifest.setdefault("performance_accumulator", {
        "wall_seconds": 0.0, "cpu_seconds": 0.0, "peak_rss_mib": 0.0,
    })
    accumulator["wall_seconds"] += elapsed_wall
    accumulator["cpu_seconds"] += elapsed_cpu
    accumulator["peak_rss_mib"] = max(
        accumulator["peak_rss_mib"],
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0,
    )
    if next_output_index == len(times):
        diagnostics = engine.diagnostics(state).model_dump(mode="json")
        performance = {
            "wall_seconds": accumulator["wall_seconds"],
            "cpu_seconds": accumulator["cpu_seconds"],
            "peak_rss_mib": accumulator["peak_rss_mib"],
            "internal_step_count": state.step_index,
            "output_count": len(times),
        }
        manifest["status"] = "COMPLETED"
        manifest["diagnostics"] = diagnostics
        manifest["performance"] = performance
        manifest["result_sha256"] = sha256_json({
            "schema_version": manifest["schema_version"],
            "model_id": manifest["model_id"], "run_id": manifest["run_id"],
            "world_id": manifest["world_id"],
            "config_sha256": manifest["config_sha256"],
            "forcing_source_sha256": manifest["forcing_source_sha256"],
            "coverage_s": manifest["coverage_s"], "grid": manifest["grid"],
            "segments": manifest["segments"], "diagnostics": diagnostics,
        })
    else:
        manifest["status"] = "PAUSED"
    write_json_atomic(target / "integrated_environment_manifest.json", manifest)
    return manifest


class IntegratedEnvironmentArchiveReader:
    def __init__(self, environment_dir: str | Path, m5a_run_dir: str | Path):
        self.environment_dir = Path(environment_dir)
        self.m5a_run_dir = Path(m5a_run_dir)
        if self.environment_dir.is_symlink() or not self.environment_dir.is_dir():
            raise ConfigError("unsafe integrated environment archive")
        self.manifest = _read_json(
            self.environment_dir / "integrated_environment_manifest.json"
        )
        try:
            self.config = IntegratedEnvironmentConfig.model_validate_json(
                (self.environment_dir / "resolved_config.json").read_text(encoding="utf-8"),
                strict=True,
            )
        except (OSError, ValidationError) as exc:
            raise ConfigError(f"invalid integrated environment config: {exc}") from exc
        if self.config.config_sha256 != self.manifest.get("config_sha256"):
            raise ConfigError("integrated environment config hash mismatch")
        self.forcing = M5ASpatialForcing(self.m5a_run_dir, self.config)
        if _read_json(
            self.environment_dir / "forcing_source.json"
        ) != self.forcing.source_contract:
            raise ConfigError("integrated environment forcing contract mismatch")
        if self.forcing.source_sha256 != self.manifest.get("forcing_source_sha256"):
            raise ConfigError("integrated environment forcing hash mismatch")
        if self.forcing.run_id != self.manifest.get("run_id"):
            raise ConfigError("integrated environment run identity mismatch")
        if self.forcing.world_id != self.manifest.get("world_id"):
            raise ConfigError("integrated environment world identity mismatch")
        self._verify_segments()

    def _verify_segments(self) -> None:
        if self.manifest.get("status") not in {"RUNNING", "PAUSED", "COMPLETED"}:
            raise ConfigError("invalid integrated environment archive status")
        previous = None
        expected_index = 0
        for entry in self.manifest.get("segments", []):
            if entry.get("segment_index") != expected_index:
                raise ConfigError("integrated environment segment index gap")
            directory = self.environment_dir / "segments" / f"{expected_index:08d}"
            if directory.is_symlink() or not directory.is_dir():
                raise ConfigError("integrated environment segment is missing or unsafe")
            raw_segment = _read_json(directory / "segment_manifest.json")
            if raw_segment != entry:
                raise ConfigError("integrated segment/root manifest mismatch")
            if entry.get("previous_checkpoint_sha256") != previous:
                raise ConfigError("integrated checkpoint chain mismatch")
            for name, digest in entry.get("files", {}).items():
                path = directory / name
                if path.is_symlink() or not path.is_file() or _sha_file(path) != digest:
                    raise ConfigError(f"integrated environment file hash mismatch: {name}")
            previous = entry.get("checkpoint_sha256")
            expected_index += 1
        if self.manifest.get("status") == "COMPLETED":
            expected = sha256_json({
                "schema_version": self.manifest["schema_version"],
                "model_id": self.manifest["model_id"], "run_id": self.manifest["run_id"],
                "world_id": self.manifest["world_id"],
                "config_sha256": self.manifest["config_sha256"],
                "forcing_source_sha256": self.manifest["forcing_source_sha256"],
                "coverage_s": self.manifest["coverage_s"], "grid": self.manifest["grid"],
                "segments": self.manifest["segments"],
                "diagnostics": self.manifest["diagnostics"],
            })
            if expected != self.manifest.get("result_sha256"):
                raise ConfigError("integrated environment result hash mismatch")

    def iter_snapshots(self) -> Iterator[IntegratedSnapshot]:
        previous_time = None
        previous_index = None
        for entry in self.manifest["segments"]:
            path = (
                self.environment_dir
                / "segments"
                / f"{entry['segment_index']:08d}"
                / "snapshots.jsonl"
            )
            with path.open(encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, 1):
                    try:
                        snapshot = IntegratedSnapshot.model_validate_json(line, strict=True)
                    except ValidationError as exc:
                        raise ConfigError(
                            f"invalid integrated snapshot {path}:{line_number}: {exc}"
                        ) from exc
                    if previous_time is not None and snapshot.t_s <= previous_time:
                        raise ConfigError("integrated snapshots are not strictly increasing")
                    if previous_index is not None and snapshot.time_index != previous_index + 1:
                        raise ConfigError("integrated snapshot index gap")
                    previous_time = snapshot.t_s
                    previous_index = snapshot.time_index
                    yield snapshot

    def events(self) -> list[dict[str, Any]]:
        merged = {}
        for entry in self.manifest["segments"]:
            path = (
                self.environment_dir
                / "segments"
                / f"{entry['segment_index']:08d}"
                / "events.json"
            )
            raw_events = _read_json(path)
            if not isinstance(raw_events, list):
                raise ConfigError("integrated environment events must be a list")
            for raw_event in raw_events:
                try:
                    event = EnvironmentEvent.model_validate(raw_event, strict=True)
                except ValidationError as exc:
                    raise ConfigError(f"invalid integrated environment event: {exc}") from exc
                merged[event.event_id] = event.model_dump(mode="json")
        return [merged[key] for key in sorted(merged)]
