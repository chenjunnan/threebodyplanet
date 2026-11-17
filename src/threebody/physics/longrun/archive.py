from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import tempfile
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from threebody.common.json import canonical_json_bytes, sha256_json, write_json_atomic
from threebody.errors import ConfigError
from threebody.physics.longrun.clock import PhysicalClock, build_segment_index
from threebody.physics.longrun.hashes import compute_contract_hashes
from threebody.physics.longrun.models import (
    LongRunManifest,
    LongRunSettings,
    RunStatus,
    RunTaskState,
    SegmentIndexEntry,
    SegmentManifest,
    WriterLockRecord,
)
from threebody.physics.longrun.state import transition_task
from threebody.physics.nbody.models import SimulationEvent, TrajectorySample
from threebody.world.models import WorldInitialState

FailureHook = Callable[[str], None]


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON value is forbidden: {value}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ConfigError(f"cannot hash long-run artifact {path}: {exc}") from exc
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise ConfigError(f"cannot fsync long-run directory {path}: {exc}") from exc


def _read_model(path: Path, model):
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"), strict=True)
    except OSError as exc:
        raise ConfigError(f"cannot read long-run artifact {path}: {exc}") from exc
    except ValidationError as exc:
        raise ConfigError(f"invalid long-run artifact {path}: {exc}") from exc


def load_run_manifest(run_dir: str | Path) -> LongRunManifest:
    return _read_model(Path(run_dir) / "long_run_manifest.json", LongRunManifest)


def load_run_settings(run_dir: str | Path) -> LongRunSettings:
    return _read_model(Path(run_dir) / "resolved_long_run_config.json", LongRunSettings)


def _verify_contract_files(
    directory: Path, manifest: LongRunManifest
) -> tuple[WorldInitialState, LongRunSettings]:
    initial_state = _read_model(directory / "initial_state.json", WorldInitialState)
    settings = load_run_settings(directory)
    initial_hash = sha256_json(initial_state)
    if initial_hash != manifest.initial_state_sha256:
        raise ConfigError("long-run initial-state hash does not match manifest")
    observed_hashes = compute_contract_hashes(
        settings,
        initial_state_sha256=initial_hash,
        body_ids=tuple(body.id for body in initial_state.bodies),
    )
    if observed_hashes != manifest.hashes:
        raise ConfigError("resolved long-run contract hashes do not match manifest")
    return initial_state, settings


def initialize_run_archive(
    destination: str | Path,
    *,
    run_id: str,
    initial_state: WorldInitialState,
    settings: LongRunSettings,
) -> LongRunManifest:
    target = Path(destination)
    if target.exists():
        raise ConfigError(f"long-run output directory already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=str(target.parent))
    )
    initial_hash = sha256_json(initial_state)
    body_ids = tuple(body.id for body in initial_state.bodies)
    hashes = compute_contract_hashes(
        settings,
        initial_state_sha256=initial_hash,
        body_ids=body_ids,
    )
    task = RunTaskState(
        run_id=run_id,
        simulation_time_s=settings.time.start_time_s,
    )
    manifest = LongRunManifest(
        run_id=run_id,
        world_id=initial_state.world_id,
        initial_state_sha256=initial_hash,
        hashes=hashes,
        task=task,
        segments=build_segment_index(settings),
    )
    try:
        write_json_atomic(temporary / "initial_state.json", initial_state)
        write_json_atomic(temporary / "resolved_long_run_config.json", settings)
        write_json_atomic(temporary / "long_run_manifest.json", manifest)
        (temporary / "segments").mkdir()
        (temporary / "checkpoints").mkdir()
        (temporary / "locks").mkdir()
        _fsync_directory(temporary)
        os.replace(temporary, target)
        _fsync_directory(target.parent)
        return manifest
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


class SingleWriterLock:
    def __init__(self, run_dir: str | Path, run_id: str):
        self.run_dir = Path(run_dir)
        self.path = self.run_dir / "locks" / "writer.lock"
        self.record = WriterLockRecord(
            run_id=run_id,
            token=uuid.uuid4().hex,
            hostname=socket.gethostname(),
            pid=os.getpid(),
            acquired_at_utc=datetime.now(UTC).isoformat(),
        )
        self._held = False

    @property
    def token(self) -> str:
        return self.record.token

    def acquire(self) -> SingleWriterLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError as exc:
            raise ConfigError(
                "long-run writer lock already exists; inspect it and recover explicitly"
            ) from exc
        except OSError as exc:
            raise ConfigError(f"cannot acquire long-run writer lock: {exc}") from exc
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(canonical_json_bytes(self.record) + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            _fsync_directory(self.path.parent)
            self._held = True
            return self
        except Exception:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            raise

    def release(self) -> None:
        if not self._held:
            return
        observed = _read_model(self.path, WriterLockRecord)
        if observed != self.record:
            raise ConfigError("writer lock ownership changed; refusing to remove it")
        try:
            self.path.unlink()
            _fsync_directory(self.path.parent)
        except OSError as exc:
            raise ConfigError(f"cannot release long-run writer lock: {exc}") from exc
        self._held = False

    def __enter__(self) -> SingleWriterLock:
        return self.acquire()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()


def _require_lock(run_dir: Path, run_id: str, token: str) -> None:
    record = _read_model(run_dir / "locks" / "writer.lock", WriterLockRecord)
    if record.run_id != run_id or record.token != token:
        raise ConfigError("active writer lock token does not authorize this mutation")


def set_task_status(
    run_dir: str | Path,
    target: RunStatus,
    *,
    lock_token: str,
    reason_code: str | None = None,
) -> LongRunManifest:
    directory = Path(run_dir)
    manifest = load_run_manifest(directory)
    _require_lock(directory, manifest.run_id, lock_token)
    task = transition_task(manifest.task, target, reason_code=reason_code)
    updated = manifest.model_copy(update={"task": task})
    write_json_atomic(directory / "long_run_manifest.json", updated)
    _fsync_directory(directory)
    return updated


def set_active_checkpoint(
    run_dir: str | Path,
    *,
    checkpoint_sha256: str,
    simulation_time_s: float,
    segment_index: int,
    next_output_index: int,
    lock_token: str,
) -> LongRunManifest:
    directory = Path(run_dir)
    manifest = load_run_manifest(directory)
    _require_lock(directory, manifest.run_id, lock_token)
    if manifest.task.status != "RUNNING":
        raise ConfigError("active checkpoints may be registered only while RUNNING")
    if segment_index >= len(manifest.segments):
        raise ConfigError("active checkpoint segment is outside the run plan")
    entry = manifest.segments[segment_index]
    if entry.status != "PLANNED" or not (
        entry.t_start_s < simulation_time_s < entry.t_end_s
    ):
        raise ConfigError("active checkpoint is not inside its planned segment")
    task = manifest.task.model_copy(
        update={
            "simulation_time_s": simulation_time_s,
            "segment_index": segment_index,
            "next_output_index": next_output_index,
            "active_checkpoint_sha256": checkpoint_sha256,
            "revision": manifest.task.revision + 1,
        }
    )
    updated = manifest.model_copy(update={"task": task})
    write_json_atomic(directory / "long_run_manifest.json", updated)
    _fsync_directory(directory)
    return updated


def _write_trajectory(
    path: Path,
    samples: Iterable[TrajectorySample],
    expected_times: Iterable[float],
    *,
    terminal_time_s: float | None = None,
) -> int:
    sample_values = tuple(samples)
    owned_times = tuple(expected_times)
    if terminal_time_s is None:
        required_times = owned_times
    else:
        required_times = tuple(time for time in owned_times if time <= terminal_time_s)
        if not required_times or required_times[-1] != terminal_time_s:
            required_times += (terminal_time_s,)
    observed_times = tuple(sample.t_s for sample in sample_values)
    if observed_times != required_times:
        raise ConfigError(
            f"segment sample times {observed_times} do not match owned times {required_times}"
        )
    try:
        with path.open("w", encoding="utf-8") as handle:
            for sample in sample_values:
                handle.write(
                    json.dumps(
                        sample.model_dump(mode="json"),
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                        allow_nan=False,
                    )
                    + "\n"
                )
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise ConfigError(f"cannot write segment trajectory {path}: {exc}") from exc
    return len(sample_values)


def _expected_times(clock: PhysicalClock, entry: SegmentIndexEntry) -> Iterable[float]:
    if entry.first_output_index is None:
        return ()
    return (
        clock.output_time(index)
        for index in range(entry.first_output_index, entry.last_output_index + 1)
    )


def commit_segment(
    run_dir: str | Path,
    segment_index: int,
    samples: Iterable[TrajectorySample],
    events: Iterable[SimulationEvent],
    diagnostics: dict[str, Any],
    *,
    lock_token: str,
    input_checkpoint_sha256: str | None = None,
    output_checkpoint_sha256: str | None = None,
    terminal_reason: str | None = None,
    terminal_time_s: float | None = None,
    failure_hook: FailureHook | None = None,
) -> SegmentManifest:
    directory = Path(run_dir)
    manifest = load_run_manifest(directory)
    settings = load_run_settings(directory)
    _require_lock(directory, manifest.run_id, lock_token)
    if manifest.task.status != "RUNNING":
        raise ConfigError("segments may be committed only while the task is RUNNING")
    if segment_index >= len(manifest.segments):
        raise ConfigError("segment index is outside the run plan")
    entry = manifest.segments[segment_index]
    if entry.status != "PLANNED":
        raise ConfigError("segment is already committed or not writable")
    if segment_index > 0 and manifest.segments[segment_index - 1].status != "COMMITTED":
        raise ConfigError("segments must be committed in physical order")
    if output_checkpoint_sha256 is None:
        raise ConfigError("a committed segment requires an output checkpoint")
    if (terminal_reason is None) != (terminal_time_s is None):
        raise ConfigError("terminal reason and terminal time must be supplied together")
    if terminal_reason not in {None, "COLLISION", "FAILED"}:
        raise ConfigError("unsupported terminal segment reason")
    if terminal_time_s is not None and not (
        entry.t_start_s <= terminal_time_s <= entry.t_end_s
    ):
        raise ConfigError("terminal time is outside the planned segment")
    if segment_index == 0:
        if input_checkpoint_sha256 is not None:
            raise ConfigError("segment zero cannot reference an input checkpoint")
    else:
        previous_segment = _read_model(
            directory
            / "segments"
            / f"{segment_index - 1:08d}"
            / "segment_manifest.json",
            SegmentManifest,
        )
        if (
            previous_segment.output_checkpoint_sha256 is None
            or input_checkpoint_sha256 != previous_segment.output_checkpoint_sha256
        ):
            raise ConfigError("segment input checkpoint does not continue its predecessor")
    target = directory / "segments" / f"{segment_index:08d}"
    if target.exists():
        raise ConfigError("segment directory exists but is not registered; run recovery")
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{segment_index:08d}.tmp-", dir=str(target.parent))
    )
    try:
        clock = PhysicalClock(settings)
        sample_count = _write_trajectory(
            temporary / "trajectory.jsonl",
            samples,
            _expected_times(clock, entry),
            terminal_time_s=terminal_time_s,
        )
        event_values = tuple(events)
        write_json_atomic(
            temporary / "events.json",
            [event.model_dump(mode="json") for event in event_values],
        )
        write_json_atomic(temporary / "diagnostics.json", diagnostics)
        for artifact in ("events.json", "diagnostics.json"):
            with (temporary / artifact).open("rb") as handle:
                os.fsync(handle.fileno())
        if failure_hook:
            failure_hook("artifacts_written")
        previous_hash = (
            None
            if segment_index == 0
            else manifest.segments[segment_index - 1].segment_manifest_sha256
        )
        segment_manifest = SegmentManifest(
            run_id=manifest.run_id,
            segment_index=segment_index,
            t_start_s=entry.t_start_s,
            t_end_s=entry.t_end_s if terminal_time_s is None else terminal_time_s,
            owns_start_sample=entry.owns_start_sample,
            first_output_index=entry.first_output_index,
            last_output_index=entry.last_output_index,
            sample_count=sample_count,
            event_count=len(event_values),
            status="COMMITTED" if terminal_reason is None else "TERMINAL",
            terminal_reason=terminal_reason,
            previous_segment_sha256=previous_hash,
            input_checkpoint_sha256=input_checkpoint_sha256,
            output_checkpoint_sha256=output_checkpoint_sha256,
            artifact_sha256={
                "trajectory": _sha256_file(temporary / "trajectory.jsonl"),
                "events": _sha256_file(temporary / "events.json"),
                "diagnostics": _sha256_file(temporary / "diagnostics.json"),
            },
        )
        write_json_atomic(temporary / "segment_manifest.json", segment_manifest)
        _fsync_directory(temporary)
        if failure_hook:
            failure_hook("before_segment_rename")
        os.replace(temporary, target)
        _fsync_directory(target.parent)
        if failure_hook:
            failure_hook("after_segment_rename")
        segment_hash = sha256_json(segment_manifest)
        segments = list(manifest.segments)
        segments[segment_index] = entry.model_copy(
            update={
                "status": "COMMITTED" if terminal_reason is None else "TERMINAL",
                "previous_segment_sha256": previous_hash,
                "segment_manifest_sha256": segment_hash,
            }
        )
        next_output = (
            manifest.task.next_output_index
            if entry.last_output_index is None
            else entry.last_output_index + 1
        )
        task = manifest.task.model_copy(
            update={
                "simulation_time_s": (
                    entry.t_end_s if terminal_time_s is None else terminal_time_s
                ),
                "segment_index": segment_index + 1,
                "next_output_index": next_output,
                "active_checkpoint_sha256": None,
                "revision": manifest.task.revision + 1,
            }
        )
        if terminal_reason is not None:
            task = transition_task(task, terminal_reason)
        updated = manifest.model_copy(update={"segments": tuple(segments), "task": task})
        if failure_hook:
            failure_hook("before_global_manifest")
        write_json_atomic(directory / "long_run_manifest.json", updated)
        _fsync_directory(directory)
        if failure_hook:
            failure_hook("after_global_manifest")
        return segment_manifest
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _verify_segment_directory(
    directory: Path,
    *,
    expected_run_id: str,
    expected_index: int,
    previous_hash: str | None,
) -> tuple[SegmentManifest, str]:
    segment = _read_model(directory / "segment_manifest.json", SegmentManifest)
    if segment.run_id != expected_run_id or segment.segment_index != expected_index:
        raise ConfigError("segment identity does not match its run/index")
    if segment.previous_segment_sha256 != previous_hash:
        raise ConfigError("segment hash chain is broken")
    artifact_paths = {
        "trajectory": directory / "trajectory.jsonl",
        "events": directory / "events.json",
        "diagnostics": directory / "diagnostics.json",
    }
    for name, path in artifact_paths.items():
        if _sha256_file(path) != segment.artifact_sha256[name]:
            raise ConfigError(f"segment {expected_index} {name} SHA-256 mismatch")
    sample_count = 0
    previous_time = None
    try:
        with artifact_paths["trajectory"].open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                sample = TrajectorySample.model_validate_json(line, strict=True)
                if previous_time is not None and sample.t_s <= previous_time:
                    raise ConfigError("segment trajectory times are not strictly increasing")
                previous_time = sample.t_s
                sample_count = line_number
        events_raw = json.loads(
            artifact_paths["events"].read_text(encoding="utf-8"),
            parse_constant=_reject_nonfinite_json,
        )
        if not isinstance(events_raw, list):
            raise ConfigError("segment events root must be an array")
        for event in events_raw:
            SimulationEvent.model_validate_json(
                json.dumps(event, allow_nan=False), strict=True
            )
        diagnostics_raw = json.loads(
            artifact_paths["diagnostics"].read_text(encoding="utf-8"),
            parse_constant=_reject_nonfinite_json,
        )
        if not isinstance(diagnostics_raw, dict):
            raise ConfigError("segment diagnostics root must be an object")
    except (OSError, UnicodeError, ValueError, ValidationError) as exc:
        raise ConfigError(f"invalid committed segment {expected_index}: {exc}") from exc
    if sample_count != segment.sample_count or len(events_raw) != segment.event_count:
        raise ConfigError("segment artifact counts do not match manifest")
    return segment, sha256_json(segment)


def _validate_durable_segment_names(directory: Path, planned_count: int) -> None:
    segments_dir = directory / "segments"
    try:
        children = tuple(segments_dir.iterdir())
    except OSError as exc:
        raise ConfigError(f"cannot inspect segment archive {segments_dir}: {exc}") from exc
    for child in children:
        if child.name.startswith("."):
            continue
        if not child.is_dir() or len(child.name) != 8 or not child.name.isdecimal():
            raise ConfigError(f"unexpected durable segment artifact: {child.name}")
        if int(child.name) >= planned_count:
            raise ConfigError(f"durable segment is outside the immutable plan: {child.name}")


def verify_run_archive(run_dir: str | Path) -> LongRunManifest:
    directory = Path(run_dir)
    manifest = load_run_manifest(directory)
    _verify_contract_files(directory, manifest)
    _validate_durable_segment_names(directory, len(manifest.segments))
    previous_hash = None
    previous_checkpoint_hash = None
    seen_uncommitted = False
    for entry in manifest.segments:
        segment_dir = directory / "segments" / f"{entry.segment_index:08d}"
        if entry.status == "PLANNED":
            seen_uncommitted = True
            if segment_dir.exists():
                raise ConfigError("durable segment exists but global manifest needs recovery")
            continue
        if seen_uncommitted:
            raise ConfigError("global segment index contains a committed gap")
        if not segment_dir.is_dir():
            raise ConfigError("global manifest references a missing segment directory")
        segment, observed_hash = _verify_segment_directory(
            segment_dir,
            expected_run_id=manifest.run_id,
            expected_index=entry.segment_index,
            previous_hash=previous_hash,
        )
        if entry.segment_manifest_sha256 != observed_hash:
            raise ConfigError("global segment manifest SHA-256 mismatch")
        if entry.status != segment.status:
            raise ConfigError("global and local segment status differ")
        if segment.t_start_s != entry.t_start_s:
            raise ConfigError("segment coverage differs from global index")
        if (
            segment.owns_start_sample != entry.owns_start_sample
            or segment.first_output_index != entry.first_output_index
            or segment.last_output_index != entry.last_output_index
        ):
            raise ConfigError("segment output ownership differs from global index")
        if segment.status == "COMMITTED" and segment.t_end_s != entry.t_end_s:
            raise ConfigError("committed segment does not cover its planned interval")
        if segment.status == "TERMINAL" and segment.t_end_s > entry.t_end_s:
            raise ConfigError("terminal segment exceeds its planned interval")
        if segment.input_checkpoint_sha256 != previous_checkpoint_hash:
            raise ConfigError("segment checkpoint chain is broken")
        if segment.output_checkpoint_sha256 is None:
            raise ConfigError("committed segment has no output checkpoint")
        previous_hash = observed_hash
        previous_checkpoint_hash = segment.output_checkpoint_sha256
        if segment.status == "TERMINAL":
            seen_uncommitted = True
    return manifest


def recover_run_archive(
    run_dir: str | Path,
    *,
    lock_token: str,
) -> tuple[LongRunManifest, tuple[str, ...]]:
    directory = Path(run_dir)
    manifest = load_run_manifest(directory)
    _require_lock(directory, manifest.run_id, lock_token)
    _verify_contract_files(directory, manifest)
    _validate_durable_segment_names(directory, len(manifest.segments))
    segments = list(manifest.segments)
    previous_hash = None
    previous_checkpoint_hash = None
    changed = False
    for index, entry in enumerate(segments):
        segment_dir = directory / "segments" / f"{index:08d}"
        if not segment_dir.exists():
            if entry.status != "PLANNED":
                raise ConfigError("global manifest references a missing durable segment")
            break
        segment, observed_hash = _verify_segment_directory(
            segment_dir,
            expected_run_id=manifest.run_id,
            expected_index=index,
            previous_hash=previous_hash,
        )
        if segment.input_checkpoint_sha256 != previous_checkpoint_hash:
            raise ConfigError("segment checkpoint chain is broken")
        if segment.output_checkpoint_sha256 is None:
            raise ConfigError("committed segment has no output checkpoint")
        if entry.status == "PLANNED":
            segments[index] = entry.model_copy(
                update={
                    "status": segment.status,
                    "previous_segment_sha256": previous_hash,
                    "segment_manifest_sha256": observed_hash,
                }
            )
            changed = True
        elif entry.segment_manifest_sha256 != observed_hash:
            raise ConfigError("global segment hash differs from durable segment")
        previous_hash = observed_hash
        previous_checkpoint_hash = segment.output_checkpoint_sha256
        if segment.status == "TERMINAL":
            break
    if changed:
        committed = [entry for entry in segments if entry.status != "PLANNED"]
        latest = committed[-1]
        next_output = (
            manifest.task.next_output_index
            if latest.last_output_index is None
            else latest.last_output_index + 1
        )
        task = manifest.task.model_copy(
            update={
                "simulation_time_s": latest.t_end_s,
                "segment_index": latest.segment_index + 1,
                "next_output_index": next_output,
                "active_checkpoint_sha256": None,
                "revision": manifest.task.revision + 1,
            }
        )
        terminal = next(
            (entry for entry in committed if entry.status == "TERMINAL"), None
        )
        if terminal is not None:
            terminal_manifest = _read_model(
                directory
                / "segments"
                / f"{terminal.segment_index:08d}"
                / "segment_manifest.json",
                SegmentManifest,
            )
            task = transition_task(task, terminal_manifest.terminal_reason)
        manifest = manifest.model_copy(update={"segments": tuple(segments), "task": task})
        write_json_atomic(directory / "long_run_manifest.json", manifest)
        _fsync_directory(directory)
    orphan_temporaries = tuple(
        sorted(path.name for path in (directory / "segments").glob(".*.tmp-*"))
    )
    verify_run_archive(directory)
    return manifest, orphan_temporaries
